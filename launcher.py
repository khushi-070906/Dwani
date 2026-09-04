"""
launcher.py

The thing a presenter actually double-clicks (after this gets compiled to
launcher.exe via PyArmor + PyInstaller -- see build steps in the project's
packaging notes). No Python source, no terminal commands, no manual model
setup.

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

import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

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


def download_with_progress(url: str, dest_path: Path) -> None:
    def _report(block_num, block_size, total_size):
        if total_size <= 0:
            return
        downloaded = block_num * block_size
        pct = min(100, downloaded * 100 // total_size)
        mb_done = downloaded / (1024 * 1024)
        mb_total = total_size / (1024 * 1024)
        print(f"\r  Downloading models: {pct:3d}%  ({mb_done:6.1f} / {mb_total:6.1f} MB)", end="", flush=True)

    print(f"First run: downloading translation models (one-time, ~few hundred MB)...")
    urllib.request.urlretrieve(url, dest_path, reporthook=_report)
    print()  # newline after the progress line


def setup_models_if_needed() -> None:
    if models_already_present():
        return

    print("=" * 70)
    print("DwaniLive first-run setup")
    print("=" * 70)

    zip_path = APP_DIR / "_dwanilive_models_tmp.zip"
    try:
        download_with_progress(MODEL_BUNDLE_URL, zip_path)

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

        print("Setup complete. This only happens once.")
        print("=" * 70)
    except Exception as exc:
        print(f"\nSetup failed: {exc}", file=sys.stderr)
        print("Check your internet connection and try running DwaniLive again.", file=sys.stderr)
        input("Press Enter to exit...")
        sys.exit(1)
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

    print("=" * 70)
    print("Welcome to DwaniLive -- first-time setup")
    print("=" * 70)
    print("This looks like the first time you're running DwaniLive on this")
    print("computer. Enter your license details once (from your purchase")
    print("confirmation) -- after this, it works fully offline.")
    print()

    while True:
        license_key = input("License key: ").strip()
        email = input("Email (used at checkout): ").strip()
        print()
        if activate(license_key, LICENSE_SERVER_URL, email):
            print()
            break
        print()
        retry = input("Try again? (y/n): ").strip().lower()
        if retry != "y":
            print("Cannot continue without activation. Exiting.")
            input("Press Enter to exit...")
            sys.exit(1)


def main() -> None:
    setup_models_if_needed()
    prompt_for_activation_if_needed()

    # Imported here, not at module top, so the (potentially slow) model
    # setup above always runs first and prints its own clear progress
    # before server.py's own heavier imports (faster_whisper, ctranslate2)
    # start loading.
    import server

    print()
    print("Starting DwaniLive... the link to open (and the QR code for attendees)")
    print("will be printed below by the server itself in a moment.")
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
