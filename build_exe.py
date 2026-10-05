r"""
build_exe.py -- one command from source to release files.

    python build_exe.py                     # Nuitka (default: compiled, source-protected)
    python build_exe.py --backend pyinstaller   # faster build, use if Nuitka breaks
    python build_exe.py --skip-build        # just re-run self-test + packaging on dist/
    python build_exe.py --hash-bundle dwani-models.zip   # print SHA-256 for model_setup.py

Output (release/):
    DwaniLive-Setup.exe         Inno Setup installer -- what users should download
    DwaniLive-win64.zip         portable folder (fallback)
    SHA256SUMS.txt

Why each choice (all of these were real failures in v1.0.x):
  * Build on Python 3.11 or 3.12, in a clean venv, from a folder that is
    NOT inside OneDrive. 3.14 is too new for ctranslate2/onnxruntime/Nuitka
    (see nuitka-crash-report.xml); OneDrive evicts files mid-build.
  * Folder build (standalone/onedir), never onefile: onefile unpacks ~350MB
    to %TEMP% on every launch (slow, and antivirus scans it every time).
  * No UPX: UPX-packed exes/DLLs are the #1 antivirus false-positive trigger
    and can corrupt ctranslate2.dll.
  * faster_whisper's package data (assets/*.onnx) must be included explicitly.
  * Every build runs `DwaniLive.exe --self-test` from the OUTPUT folder with
    a throwaway data dir. If any import/DLL/static file is missing, the build
    fails here instead of on a presenter's laptop.
  * The zip is written with forward-slash paths (PowerShell 5's
    Compress-Archive writes backslashes, which some unzip tools mangle).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from appenv import APP_NAME, APP_VERSION  # noqa: E402

DIST = ROOT / "dist"
RELEASE = ROOT / "release"
APP_FOLDER = DIST / APP_NAME            # final folder that gets zipped / installed
ICON = ROOT / "assets" / "dwanilive.ico"

LOCAL_MODULES = [
    "appenv", "model_setup", "updater", "phone_mic", "preflight", "notes", "talk_glossary", "crash_report", "gui", "server", "session", "pipeline", "backends", "qa_pipeline",
    "nllb_tokenizer", "licensing", "activate", "accessibility", "glossary", "translation_cache",
    "decision_engine", "dynamic_glossary", "persistent_memory",
]
# Pulled in by optional code paths only; keep them out of the desktop build.
# av (PyAV/FFmpeg, ~25 unsigned DLLs) and onnxruntime are only used for decoding
# audio FILES and the optional Silero VAD -- never by DwaniLive -- and their
# unsigned DLLs are what Windows Smart App Control blocks. See backends.ensure_av_importable.
EXCLUDE = ["av", "onnxruntime", "torch", "transformers", "sentence_transformers", "tensorflow", "matplotlib",
           "webview", "clr_loader", "pythonnet", "IPython", "pytest", "tkinter"]


def check_environment() -> None:
    if sys.platform != "win32":
        print("WARNING: building on non-Windows produces a non-Windows binary.")
    if sys.version_info[:2] not in ((3, 11), (3, 12)):
        print(f"WARNING: Python {sys.version.split()[0]} -- release builds should use 3.11 or 3.12.")
    if "onedrive" in str(ROOT).lower():
        sys.exit("ERROR: project is inside OneDrive. Copy it to e.g. C:\\dev\\Dwani and build there.")
    missing = []
    for mod in ("ctranslate2", "faster_whisper", "sentencepiece", "fastapi", "uvicorn", "websockets", "qrcode", "certifi"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        sys.exit(f"ERROR: missing packages in this venv: {missing}. pip install -r requirements-desktop.txt")


def build_nuitka() -> Path:
    args = [
        sys.executable, "-m", "nuitka",
        "--standalone",
        "--assume-yes-for-downloads",
        "--output-dir=" + str(DIST),
        "--output-filename=DwaniLive.exe",
        "--windows-console-mode=attach",   # no console on double-click; output shows if run from cmd
        "--include-data-dir=static=static",
        "--include-package-data=faster_whisper",
        "--include-package=uvicorn",
        "--include-package=websockets",     # uvicorn loads it dynamically; without it every WebSocket (captions!) fails
        "--include-package=httptools",
        "--include-package=certifi",
        "--include-package-data=certifi",
        f"--company-name={APP_NAME}",
        f"--product-name={APP_NAME}",
        f"--file-version={APP_VERSION}",
        f"--product-version={APP_VERSION}",
        "--file-description=DwaniLive offline live translation",
    ]
    args += [f"--include-module={m}" for m in LOCAL_MODULES]
    args += [f"--nofollow-import-to={m}" for m in EXCLUDE]
    if ICON.is_file():
        args.append(f"--windows-icon-from-ico={ICON}")
    args.append("launcher.py")
    run(args)
    out = DIST / "launcher.dist"
    if APP_FOLDER.exists():
        shutil.rmtree(APP_FOLDER)
    out.rename(APP_FOLDER)
    return APP_FOLDER


def build_pyinstaller() -> Path:
    sep = ";" if sys.platform == "win32" else ":"
    args = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--onedir", "--windowed", "--noupx",
        "--name", APP_NAME,
        "--distpath", str(DIST), "--workpath", str(ROOT / "build"),
        "--add-data", f"static{sep}static",
        "--collect-data", "faster_whisper",
        "--collect-binaries", "ctranslate2",
        "--collect-submodules", "uvicorn",
        "--collect-submodules", "websockets",  # uvicorn loads it dynamically; without it every WebSocket (captions!) fails
        "--collect-data", "certifi",
    ]
    for m in LOCAL_MODULES:
        args += ["--hidden-import", m]
    for m in EXCLUDE:
        args += ["--exclude-module", m]
    if ICON.is_file():
        args += ["--icon", str(ICON)]
    args.append("launcher.py")
    run(args)
    return APP_FOLDER


def self_test(folder: Path) -> None:
    exe = folder / ("DwaniLive.exe" if sys.platform == "win32" else "DwaniLive")
    if not exe.exists():
        sys.exit(f"ERROR: {exe} not found after build.")
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ, DWANI_DATA_DIR=tmp)
        env.pop("PYTHONPATH", None)
        print(f"\nSelf-test: {exe} --self-test")
        res = subprocess.run([str(exe), "--self-test"], env=env, cwd=tmp, capture_output=True, text=True, timeout=300)
        report = Path(tmp, "logs", "self_test.json")
        text = report.read_text(encoding="utf-8") if report.exists() else (res.stdout + res.stderr)
        print(text)
        if res.returncode != 0:
            sys.exit("ERROR: self-test FAILED -- do not ship this build. Fix the FAIL lines above.")
    for junk in ("dwanilive_error.log", "license.token"):
        (folder / junk).unlink(missing_ok=True)
    print("Self-test passed.")


def make_zip(folder: Path) -> Path:
    RELEASE.mkdir(exist_ok=True)
    zpath = RELEASE / f"{APP_NAME}-win64.zip"  # stable name: dashboard links releases/latest/download/<name>
    zpath.unlink(missing_ok=True)
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in sorted(folder.rglob("*")):
            if p.is_file():
                z.write(p, f"{APP_NAME}/{p.relative_to(folder).as_posix()}")
    print(f"Portable zip: {zpath} ({zpath.stat().st_size / 1e6:.0f} MB)")
    return zpath


def make_installer() -> Path | None:
    iscc = shutil.which("iscc") or next(
        (str(p) for p in (Path(os.environ.get("ProgramFiles(x86)", "")) / "Inno Setup 6" / "ISCC.exe",
                          Path(os.environ.get("ProgramFiles", "")) / "Inno Setup 6" / "ISCC.exe") if p.is_file()),
        None,
    )
    if not iscc:
        print("Inno Setup not found -- skipping installer (install from https://jrsoftware.org/isdl.php).")
        return None
    defines = [f"/DAppVersion={APP_VERSION}", f"/DSourceDir={APP_FOLDER}", f"/DOutputDir={RELEASE}"]
    if ICON.is_file():
        defines.append(f"/DIconFile={ICON}")
    run([iscc, *defines, str(ROOT / "installer" / "DwaniLive.iss")])
    out = RELEASE / f"{APP_NAME}-Setup.exe"
    print(f"Installer: {out}")
    return out


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(4 << 20), b""):
            h.update(b)
    return h.hexdigest()


def run(args: list[str]) -> None:
    print("Running:", " ".join(map(str, args)))
    if subprocess.run(args, cwd=ROOT).returncode != 0:
        sys.exit("Build step failed (see output above).")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", choices=["nuitka", "pyinstaller"], default="nuitka")
    ap.add_argument("--skip-build", action="store_true")
    ap.add_argument("--hash-bundle", metavar="ZIP")
    a = ap.parse_args()

    if a.hash_bundle:
        print(f"NLLB_BUNDLE_SHA256 = \"{sha256(Path(a.hash_bundle))}\"")
        return

    check_environment()
    if not a.skip_build:
        shutil.rmtree(APP_FOLDER, ignore_errors=True)
        (build_nuitka if a.backend == "nuitka" else build_pyinstaller)()
    self_test(APP_FOLDER)
    files = [make_zip(APP_FOLDER)]
    inst = make_installer()
    if inst:
        files.insert(0, inst)
    sums = "\n".join(f"{sha256(f)}  {f.name}" for f in files)
    (RELEASE / "SHA256SUMS.txt").write_text(sums + "\n", encoding="utf-8")
    print("\n" + sums)
    print(f"\nUpload everything in {RELEASE} to a GitHub release tagged v{APP_VERSION} "
          f"and mark it 'Latest' -- the dashboard links to releases/latest automatically.")


if __name__ == "__main__":
    main()
