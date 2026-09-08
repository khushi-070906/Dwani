"""
launcher.py

The thing a presenter actually double-clicks (after this gets compiled to
DwaniLive.exe via PyArmor + PyInstaller -- see build steps in the project's
packaging notes). Build with --name so the output .exe is what the presenter
sees in Downloads/Desktop, not the source filename. The UI is now a real
window (see gui.py), not the console, so build --windowed rather than
--console, and make sure pywebview's Windows backend is actually bundled
(see gui.py's module docstring for why --collect-submodules is required
here, not optional):

    pyinstaller --name DwaniLive --onefile --windowed \
        --collect-submodules webview --collect-submodules clr_loader \
        launcher.py

If gui.py or pywebview is ever missing/broken at runtime, main() below
falls back to the original plain-console flow automatically -- that
fallback is why setup_models_if_needed()/prompt_for_activation_if_needed()/
setup_firewall_if_needed() below are still console-flavored (input(),
print(), ANSI colour) rather than removed outright.
No Python source, no terminal commands, no manual model setup.

What it does, in order:
    1. First run only: checks whether nllb-200-ct2/ and
       sentencepiece.bpe.model already exist next to the exe. If not,
       downloads a single zip bundle (pre-converted by YOU, the developer,
       on a machine with transformers+torch -- see backends.py's docstring
       for why that conversion can't happen on the presenter's machine)
       and extracts it, with a visible progress bar so a multi-hundred-MB
       download doesn't look frozen.
    2. faster-whisper handles its OWN model download/caching automatically
       the first time it's used -- nothing extra needed here for that part.
    3. Starts server.py's actual FastAPI app (imported directly, not
       subprocessed, so this is one single compiled binary rather than a
       launcher that shells out to a second script sitting next to it in
       plain text).
    4. Opens the presenter's default browser to the local host page once
       the server's actually listening, so there's no "now go type
       localhost:8000 yourself" step either.

Configure MODEL_BUNDLE_URL below before building -- point it at wherever
you've uploaded the zip (GitHub Releases is the easiest free option, and
supports files up to 2GB, which comfortably covers an int8-quantized
NLLB-200-distilled-600M bundle).
"""

from __future__ import annotations

import ctypes
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Console UI helpers
# ---------------------------------------------------------------------------
# Windows 10+ consoles support ANSI colour codes, but only after explicitly
# opting in via SetConsoleMode -- older/plain cmd.exe windows otherwise print
# raw escape-code garbage instead of colour. _ansi_enabled() does that
# opt-in and reports whether it's safe to use colour at all; every colour
# helper below degrades to plain text if it isn't.
_ANSI_ENABLED = False


def _enable_ansi() -> bool:
    if sys.platform != "win32":
        return sys.stdout.isatty()
    try:
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_ulong()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING))
    except Exception:
        return False


def _set_console_title(title: str) -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.kernel32.SetConsoleTitleW(title)
    except Exception:
        pass  # cosmetic only -- never worth interrupting startup for


def _c(code: str, text: str) -> str:
    """Wraps text in an ANSI colour code if colour is available, else
    returns it unchanged. Never lets a display nicety raise or crash."""
    if not _ANSI_ENABLED:
        return text
    return f"\033[{code}m{text}\033[0m"


def _cyan(text: str) -> str:
    return _c("36", text)


def _green(text: str) -> str:
    return _c("32", text)


def _yellow(text: str) -> str:
    return _c("33", text)


def _red(text: str) -> str:
    return _c("31", text)


def _bold(text: str) -> str:
    return _c("1", text)


def _banner(title: str) -> None:
    width = 70
    line = "\u2500" * (width - 2)
    print(_cyan(f"\u250c{line}\u2510"))
    print(_cyan("\u2502") + _bold(title.center(width - 2)) + _cyan("\u2502"))
    print(_cyan(f"\u2514{line}\u2518"))


def _render_progress_bar(pct: int, mb_done: float, mb_total: float, bar_width: int = 32) -> str:
    filled = int(bar_width * pct / 100)
    bar = "\u2588" * filled + "\u2591" * (bar_width - filled)
    return f"\r  [{_green(bar)}] {pct:3d}%  ({mb_done:6.1f} / {mb_total:6.1f} MB)"

# ---------------------------------------------------------------------------
# Configure before building
# ---------------------------------------------------------------------------

# Point this at your hosted zip containing nllb-200-ct2/ (the whole folder)
# and sentencepiece.bpe.model at its top level. GitHub Releases direct-asset
# URLs look like:
#   https://github.com/<you>/<repo>/releases/download/<tag>/models.zip
MODEL_BUNDLE_URL = "https://github.com/khushi-070906/Dwani-models/releases/download/v1.0/dwani-models.zip"

# PyInstaller's own recommended pattern: when frozen (compiled), sys.argv[0]
# can be unreliable depending on how the exe was launched (a shortcut, a
# different working directory, etc.) -- sys.executable is the safe,
# documented way to find where the actual .exe lives. This matters a lot
# here specifically because APP_DIR is where downloaded models get saved
# PERSISTENTLY -- getting this wrong would mean re-downloading the ~580MB
# model bundle on every single launch instead of just the first one.
if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parent
NLLB_MODEL_DIR = APP_DIR / "nllb-200-ct2"
SENTENCEPIECE_MODEL = APP_DIR / "sentencepiece.bpe.model"
WHISPER_MODEL_SIZE = "small"
SERVER_PORT = 8000


def models_already_present() -> bool:
    return NLLB_MODEL_DIR.is_dir() and SENTENCEPIECE_MODEL.is_file()


def download_with_progress(url: str, dest_path: Path, progress_cb=None) -> None:
    """progress_cb, if given, is called as progress_cb(pct, mb_done, mb_total)
    instead of printing a console progress bar -- used by gui.py to drive
    the window's progress bar. Console behavior (default) is unchanged."""
    def _report(block_num, block_size, total_size):
        if total_size <= 0:
            return
        downloaded = block_num * block_size
        pct = min(100, downloaded * 100 // total_size)
        mb_done = downloaded / (1024 * 1024)
        mb_total = total_size / (1024 * 1024)
        if progress_cb:
            progress_cb(pct, mb_done, mb_total)
        else:
            print(_render_progress_bar(pct, mb_done, mb_total), end="", flush=True)

    if not progress_cb:
        print(_yellow("First run: downloading translation models (one-time, ~few hundred MB)..."))
    urllib.request.urlretrieve(url, dest_path, reporthook=_report)
    if not progress_cb:
        print()  # newline after the progress line


def setup_models_if_needed(progress_cb=None, interactive_on_error: bool = True) -> None:
    """progress_cb, if given, is forwarded to download_with_progress() and
    console banners/prints are skipped (gui.py drives its own progress bar
    and status text instead). interactive_on_error controls what happens
    on failure: True (console default) prints and blocks on input() before
    exiting, same as before; False (GUI) re-raises instead, since a
    --windowed build has no console to type "Enter" into -- see gui.py's
    _run_pipeline(), which catches this and shows the error view.
    """
    if models_already_present():
        return

    if not progress_cb:
        _banner("DwaniLive -- First-Run Setup")

    zip_path = APP_DIR / "_dwanilive_models_tmp.zip"
    try:
        download_with_progress(MODEL_BUNDLE_URL, zip_path, progress_cb=progress_cb)

        if not progress_cb:
            print("Extracting models...")
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(APP_DIR)

        if not models_already_present():
            # Common real-world case: whoever zipped the bundle selected a
            # single parent folder (e.g. "dwani-models/") rather than its
            # contents directly, so everything landed one level deeper than
            # expected -- APP_DIR/dwani-models/nllb-200-ct2/ instead of
            # APP_DIR/nllb-200-ct2/. Auto-detect and fix rather than making
            # every zip re-upload get the exact structure right by hand.
            candidate_dirs = [p for p in APP_DIR.iterdir() if p.is_dir() and p.name not in {"__pycache__"}]
            for candidate in candidate_dirs:
                nested_nllb = candidate / "nllb-200-ct2"
                nested_spm = candidate / "sentencepiece.bpe.model"
                if nested_nllb.is_dir() and nested_spm.is_file():
                    print(f"(Found models nested inside '{candidate.name}/' -- moving up one level.)")
                    shutil.move(str(nested_nllb), str(NLLB_MODEL_DIR))
                    shutil.move(str(nested_spm), str(SENTENCEPIECE_MODEL))
                    shutil.rmtree(candidate, ignore_errors=True)
                    break

        if not models_already_present():
            raise RuntimeError(
                "Download completed but expected files weren't found after extraction, "
                "even after checking one level of nesting. The bundle's contents may not "
                "match what this launcher expects -- check MODEL_BUNDLE_URL points at a "
                "zip containing nllb-200-ct2/ and sentencepiece.bpe.model, at its top "
                "level or nested inside a single wrapper folder."
            )

        if not progress_cb:
            print(_green("Setup complete. This only happens once.\n"))
    except Exception as exc:
        print(_red(f"\nSetup failed: {exc}"), file=sys.stderr)
        print("Check your internet connection and try running DwaniLive again.", file=sys.stderr)
        if interactive_on_error:
            input("Press Enter to exit...")
            sys.exit(1)
        raise
    finally:
        if zip_path.exists():
            zip_path.unlink()


LICENSE_SERVER_URL = "https://dhwani-elit.onrender.com"


def prompt_for_activation_if_needed() -> None:
    """A double-clicked .exe has no terminal args to pass --license-key
    into, and no bundled activate.py a presenter could run themselves --
    so if there's no cached token yet, ask for the two things activation
    needs right here, in plain console prompts, and activate on the spot.
    """
    from licensing import DEFAULT_CACHE_PATH

    if DEFAULT_CACHE_PATH.exists():
        return  # already activated on a previous run

    from activate import activate

    _banner("Welcome to DwaniLive")
    print("This looks like the first time you're running DwaniLive on this")
    print("computer. Enter your license details once (from your purchase")
    print("confirmation) -- after this, it works fully offline.")
    print()

    while True:
        license_key = input(_bold("License key: ")).strip()
        email = input(_bold("Email (used at checkout): ")).strip()
        print()
        if activate(license_key, LICENSE_SERVER_URL, email):
            print(_green("Activated successfully.\n"))
            break
        print(_red("Activation failed.\n"))
        retry = input("Try again? (y/n): ").strip().lower()
        if retry != "y":
            print("Cannot continue without activation. Exiting.")
            input("Press Enter to exit...")
            sys.exit(1)


FIREWALL_RULE_NAME = "DwaniLive"


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
            capture_output=True, text=True, timeout=10,
        )
        # netsh prints "No rules match the specified criteria." (localized
        # in non-English Windows, but the return code is reliable) when
        # nothing matches -- return code 0 with actual rule fields present
        # is the "it's there" case.
        return result.returncode == 0 and "Rule Name" in result.stdout
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
                f"localport={SERVER_PORT}",
            ],
            capture_output=True, text=True, timeout=10,
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
        f'dir=in action=allow protocol=TCP localport={SERVER_PORT}'
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

    added = _add_firewall_rule() if _is_admin() else _elevate_and_add_firewall_rule()

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
              f'dir=in action=allow protocol=TCP localport={SERVER_PORT}'))
        print()
        print("Note: even with this rule in place, some venue/hotel WiFi and")
        print("phone hotspots block device-to-device traffic entirely ('client")
        print("isolation') at the router level -- that can't be fixed from")
        print("this laptop, only from the router/hotspot settings.\n")


def main() -> None:
    global _ANSI_ENABLED
    _ANSI_ENABLED = _enable_ansi()
    _set_console_title("DwaniLive")

    # Prefer the real GUI window (gui.py, styled to match the website) --
    # falls back to the plain console flow below if pywebview isn't
    # installed, or its native backend fails to initialize on this
    # machine (e.g. WebView2 missing/broken). That fallback matters: it's
    # what keeps this runnable on a dev machine without pywebview set up,
    # and it's what keeps a presenter's session from being blocked
    # entirely by a GUI-layer problem rather than a real one.
    try:
        import gui as _gui
    except Exception:
        _gui = None

    if _gui is not None:
        try:
            _gui.run(
                setup_models_if_needed=setup_models_if_needed,
                setup_firewall_if_needed=setup_firewall_if_needed,
                app_dir=APP_DIR,
                nllb_model_dir=NLLB_MODEL_DIR,
                whisper_model_size=WHISPER_MODEL_SIZE,
                server_port=SERVER_PORT,
                license_server_url=LICENSE_SERVER_URL,
            )
            return
        except Exception as exc:
            print(_yellow(f"GUI window failed to start ({exc}); falling back to the console."), file=sys.stderr)

    setup_models_if_needed()
    prompt_for_activation_if_needed()
    setup_firewall_if_needed()

    # Imported here, not at module top, so the (potentially slow) model
    # setup above always runs first and prints its own clear progress
    # before server.py's own heavier imports (faster_whisper, ctranslate2)
    # start loading.
    import server

    _banner("DwaniLive is starting")
    print("The link to open (and the QR code for attendees) will be")
    print("printed below by the server itself in a moment.")
    print()

    # server.main() takes the exact same flags you'd type on the command
    # line, as a list -- this is the real, tested argparse path server.py
    # already uses, not a separate/guessed entry point. NOTE: auto-opening
    # the browser directly to the right page isn't done here, because the
    # actual host URL includes a session ID generated at runtime inside
    # Session (session.py) -- guessing that URL format without seeing that
    # file would risk opening a broken link instead of just telling the
    # presenter to click the one server.py already prints below.
    server.main([
        "--port", str(SERVER_PORT),
        "--whisper-model", WHISPER_MODEL_SIZE,
        "--nllb-model-dir", str(NLLB_MODEL_DIR),
        "--qa",
    ])


if __name__ == "__main__":
    main()
