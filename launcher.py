r"""
launcher.py -- what DwaniLive.exe runs.

    DwaniLive.exe                 normal start: control window + setup + server
    DwaniLive.exe --console       same flow in a terminal (no window)
    DwaniLive.exe --self-test     verify the build (imports, DLLs, static files);
                                  exit code 0 = OK. build_exe.py runs this on
                                  every build so a broken exe never ships.
    DwaniLive.exe --diagnose      print an error report (system, models, log tail)
    DwaniLive.exe --reset-models  delete downloaded models, then start normally

Order of operations (see gui.py / model_setup.py for details):
    1. stdio made safe for a windowed build, logging to
       %LOCALAPPDATA%\DwaniLive\logs\dwanilive.log
    2. single-instance check -- a second launch just re-opens the window
    3. preflight (writable data dir, app files present, RAM warning)
    4. models: resumable, verified download into %LOCALAPPDATA%\DwaniLive\models
    5. license: cached token, or activate in the window, or Free plan
    6. Windows Firewall rule (one UAC prompt, once)
    7. server.main(...) with absolute model paths
"""

from __future__ import annotations

import ctypes
import json
import subprocess
import sys
import time
from pathlib import Path

import appenv

SERVER_PORT = 8000
LICENSE_SERVER_URL = "https://dhwani-elit.onrender.com"


def _yellow(t): return t
def _green(t): return t
def _red(t): return t
def _bold(t): return t


# Renamed from "DwaniLive" (which only opened port 8000): server.py falls back
# to 8001-8010 when 8000 is busy, and phones then got silently blocked. New
# name forces the wider rule to be added once on machines that had the old one.
FIREWALL_RULE_NAME = "DwaniLive LAN"
FIREWALL_PORT_RANGE = f"{SERVER_PORT}-{SERVER_PORT + 10}"


def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        # Not on Windows, or the check itself failed -- treat as "can't
        # tell", which downstream just means we'll try the direct netsh
        # call and let it fail on its own if elevation was actually needed.
        return False


def _firewall_rule_exists() -> bool:
    try:
        result = subprocess.run(
            ["netsh", "advfirewall", "firewall", "show", "rule", f"name={FIREWALL_RULE_NAME}"],
            capture_output=True, text=True, errors="replace", timeout=10,
        )
        # netsh prints "No rules match the specified criteria." (localized
        # in non-English Windows, but the return code is reliable) when
        # nothing matches -- return code 0 with actual rule fields present
        # is the "it's there" case.
        return result.returncode == 0 and FIREWALL_RULE_NAME in (result.stdout or "")
    except Exception:
        return False


def _add_firewall_rule() -> bool:
    """Best-effort: adds a single inbound-allow rule scoped to
    SERVER_PORT/TCP, not a blanket app exception. Returns True on success.
    """
    try:
        result = subprocess.run(
            [
                "netsh", "advfirewall", "firewall", "add", "rule",
                f"name={FIREWALL_RULE_NAME}",
                "dir=in", "action=allow", "protocol=TCP",
                f"localport={FIREWALL_PORT_RANGE}", "profile=any",
            ],
            capture_output=True, text=True, errors="replace", timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


class _SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("fMask", ctypes.c_ulong),
        ("hwnd", ctypes.c_void_p),
        ("lpVerb", ctypes.c_wchar_p),
        ("lpFile", ctypes.c_wchar_p),
        ("lpParameters", ctypes.c_wchar_p),
        ("lpDirectory", ctypes.c_wchar_p),
        ("nShow", ctypes.c_int),
        ("hInstApp", ctypes.c_void_p),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", ctypes.c_wchar_p),
        ("hKeyClass", ctypes.c_void_p),
        ("dwHotKey", ctypes.c_ulong),
        ("hIcon", ctypes.c_void_p),
        ("hProcess", ctypes.c_void_p),
    ]


def _elevate_and_add_firewall_rule() -> bool:
    """Relaunches just the netsh add-rule command through a UAC prompt.
    Only this one command runs elevated -- not the app itself.

    IMPORTANT: plain ShellExecuteW is fire-and-forget -- it returns as soon
    as the elevated netsh process is *launched*, not after it finishes. That
    made an earlier version of this function report success even when the
    UAC prompt was still on screen (or had just been dismissed with "No"),
    which is exactly the kind of thing that looks fixed in testing but
    isn't. SEE_MASK_NOCLOSEPROCESS gives us a real process handle to wait
    on, and we re-check the rule afterwards rather than trusting the launch
    result at all.
    """
    SEE_MASK_NOCLOSEPROCESS = 0x00000040
    SW_HIDE = 0
    INFINITE = 0xFFFFFFFF

    params = (
        f'advfirewall firewall add rule name="{FIREWALL_RULE_NAME}" '
        f'dir=in action=allow protocol=TCP localport={FIREWALL_PORT_RANGE} profile=any'
    )
    info = _SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(_SHELLEXECUTEINFOW)
    info.fMask = SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = "netsh"
    info.lpParameters = params
    info.nShow = SW_HIDE

    try:
        ok = ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info))
        if not ok or not info.hProcess:
            # Most common real cause: user clicked "No" on the UAC prompt.
            return False
        ctypes.windll.kernel32.WaitForSingleObject(info.hProcess, INFINITE)
        ctypes.windll.kernel32.CloseHandle(info.hProcess)
    except Exception:
        return False

    # Don't trust the launch outcome at all -- ask netsh itself, fresh.
    return _firewall_rule_exists()


def setup_firewall_if_needed() -> None:
    if sys.platform != "win32":
        return  # only relevant on Windows; other platforms just don't have this problem
    if _firewall_rule_exists():
        return

    print(_yellow("Setting up local network access so phones/other devices on"))
    print(_yellow("the same WiFi can join the session (one-time)..."))

    declined = appenv.RUNTIME_DIR / "firewall_declined"
    if declined.exists() and time.time() - declined.stat().st_mtime < 24 * 3600:
        print("Skipping firewall prompt (declined earlier today). Phones may not be able to join.")
        return

    added = _add_firewall_rule() if _is_admin() else _elevate_and_add_firewall_rule()
    if not added:
        try:
            declined.write_text("1", encoding="utf-8")
        except OSError:
            pass

    if added:
        print(_green("Done -- other devices on this network can now reach DwaniLive.\n"))
    else:
        print(_red("Couldn't add the firewall rule automatically") + " (you may have")
        print("clicked 'No' on the permission prompt, or aren't an admin on")
        print("this machine). DwaniLive will still work on THIS computer, but")
        print("other devices on the WiFi may not be able to join until you")
        print("either re-run DwaniLive and allow the prompt, or run this once")
        print("yourself in an admin Command Prompt:")
        print(_bold(f'  netsh advfirewall firewall add rule name="{FIREWALL_RULE_NAME}" '
              f'dir=in action=allow protocol=TCP localport={FIREWALL_PORT_RANGE} profile=any'))
        print()
        print("Note: even with this rule in place, some venue/hotel WiFi and")
        print("phone hotspots block device-to-device traffic entirely ('client")
        print("isolation') at the router level -- that can't be fixed from")
        print("this laptop, only from the router/hotspot settings.\n")




# ---------------------------------------------------------------------------
# Console (no-window) flow
# ---------------------------------------------------------------------------

def _console_progress(p) -> None:
    if p.note:
        print(f"  {p.note}")
        return
    if p.total_bytes:
        bar = int(p.pct / 3.2)
        speed = f"{p.speed_bps / 1048576:5.1f} MB/s" if p.speed_bps else ""
        print(f"\r  {p.label:<28} [{'#' * bar}{'.' * (32 - bar)}] {p.pct:3d}% {speed}   ", end="", flush=True)


def run_console() -> None:
    import model_setup

    for w in appenv.preflight():
        print(f"WARNING: {w}")
    if not model_setup.all_installed():
        print("First run: downloading models (one-time, ~1.1 GB, resumes if interrupted)...")
    model_setup.ensure_models(_console_progress)
    print()

    from licensing import DEFAULT_CACHE_PATH

    if not DEFAULT_CACHE_PATH.exists() and sys.stdin and sys.stdin.isatty():
        print("No license activated -- press Enter twice to continue on the Free plan.")
        key = input("License key: ").strip()
        email = input("Email: ").strip() if key else ""
        if key and email:
            from activate import activate

            if not activate(key, LICENSE_SERVER_URL, email):
                print("Activation failed -- continuing on the Free plan.")

    setup_firewall_if_needed()

    import os
    import server

    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    server.main([
        "--port", str(SERVER_PORT),
        "--whisper-model", str(model_setup.WHISPER_DIR),
        "--nllb-model-dir", str(model_setup.NLLB_DIR),
        "--no-hotspot",
        "--phone-mic",
        *server.licensed_launcher_flags(),
    ])


# ---------------------------------------------------------------------------
# --self-test: catches "works on my machine" before a release ships
# ---------------------------------------------------------------------------

SELF_TEST_IMPORTS = [
    "numpy", "fastapi", "starlette", "uvicorn", "websockets", "pydantic", "dotenv",
    "qrcode", "PIL", "cryptography", "sentencepiece", "ctranslate2", "faster_whisper",
    "tokenizers", "certifi", "pypdf",
    "licensing", "activate", "session", "pipeline", "backends", "qa_pipeline",
    "nllb_tokenizer", "accessibility", "glossary", "translation_cache", "server",
    "gui", "model_setup", "updater", "phone_mic", "preflight", "notes", "talk_glossary", "crash_report",
]


def self_test() -> int:
    import importlib

    results = {"system": appenv.system_summary(), "imports": {}, "checks": {}}
    try:
        from backends import ensure_av_importable

        ensure_av_importable()  # PyAV is deliberately not shipped -- see backends.py
    except Exception:
        pass
    ok = True
    for mod in SELF_TEST_IMPORTS:
        try:
            importlib.import_module(mod)
            results["imports"][mod] = "ok"
        except Exception as exc:  # noqa: BLE001
            results["imports"][mod] = f"FAIL: {exc.__class__.__name__}: {exc}"
            ok = False

    def check(name, fn):
        nonlocal ok
        try:
            fn()
            results["checks"][name] = "ok"
        except Exception as exc:  # noqa: BLE001
            results["checks"][name] = f"FAIL: {exc}"
            ok = False

    def _static():
        for f in ("index.html", "host.html"):
            if not (appenv.STATIC_DIR / f).is_file():
                raise FileNotFoundError(appenv.STATIC_DIR / f)

    def _ct2():
        import ctranslate2

        ctranslate2.get_cuda_device_count()  # forces the native library to load
        if not ctranslate2.get_supported_compute_types("cpu"):
            raise RuntimeError("ctranslate2 reports no CPU compute types")

    def _whisper_class():
        from faster_whisper import WhisperModel  # noqa: F401  -- must import WITHOUT PyAV

    def _fw_assets():
        import faster_whisper

        assets = Path(faster_whisper.__file__).parent / "assets"
        if not any(assets.glob("*.onnx")):
            raise FileNotFoundError(f"faster_whisper VAD assets missing in {assets}")

    check("static_files", _static)
    check("ctranslate2_native", _ct2)
    check("faster_whisper_without_pyav", _whisper_class)
    check("faster_whisper_assets", _fw_assets)
    check("data_dir_writable", appenv.preflight)
    results["ok"] = ok
    print(json.dumps(results, indent=2))
    try:
        (appenv.LOGS_DIR / "self_test.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    except OSError:
        pass
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

def _report_fatal(exc: BaseException) -> None:
    import traceback

    print("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)), file=sys.stderr)
    if sys.platform == "win32":
        try:
            ctypes.windll.user32.MessageBoxW(
                0,
                f"DwaniLive couldn't start:\n\n{appenv.friendly_error(exc)}\n\nLog file:\n{appenv.LOG_FILE}",
                "DwaniLive",
                0x10,
            )
        except Exception:
            pass


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    appenv.install_safe_streams()
    appenv.suppress_child_console_windows()
    try:
        appenv.ensure_dirs()
    except OSError:
        pass

    if "--self-test" in argv:
        return self_test()
    if "--diagnose" in argv:
        import gui

        print(gui.diagnostics_text())
        return 0
    if "--reset-models" in argv:
        import model_setup

        model_setup.reset_models()
        print("Models deleted; they'll be downloaded again on next start.")

    if not appenv.acquire_single_instance():
        # Already running: just bring its control window back.
        try:
            url = appenv.INSTANCE_FILE.read_text(encoding="utf-8").strip()
            import gui

            gui.open_app_window(url)
            return 0
        except OSError:
            if "--after-restart" not in argv:
                raise RuntimeError("DwaniLive is already running (check the taskbar).")
            time.sleep(3)  # previous instance is still exiting after a restart
            if not appenv.acquire_single_instance():
                raise RuntimeError("DwaniLive is already running (check the taskbar).")

    if "--console" in argv:
        run_console()
        return 0

    import gui

    gui.LauncherApp(
        setup_firewall_if_needed=setup_firewall_if_needed,
        server_port=SERVER_PORT,
        license_server_url=LICENSE_SERVER_URL,
    ).run(open_window="--no-window" not in argv)
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except SystemExit:
        raise
    except BaseException as e:  # noqa: BLE001 -- last line of defense; never vanish silently
        _report_fatal(e)
        code = 1
    sys.exit(code)
