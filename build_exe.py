"""
build_exe.py

Compiles launcher.py (which imports and starts server.py internally) into a
standalone .exe using Nuitka -- real machine code, not just zipped bytecode
(PyInstaller-style), so it's genuinely hard to recover readable source
from, not just mildly inconvenient.

Entry point is launcher.py, not server.py directly: launcher.py handles
first-run model downloading (see its own docstring) before starting
server.py's actual app, so the presenter never sees or needs any of the
.py source files -- just DwaniLive.exe.

Run this ONCE per release, on Windows, inside your activated .venv:

    pip install nuitka --break-system-packages
    python build_exe.py

Output lands in dist/launcher.dist/ (standalone folder mode -- NOT
--onefile). Standalone folder mode is used instead of --onefile because
faster-whisper/ctranslate2 ship shared libraries (.dll files) and data
alongside the package; --onefile has to unpack everything to a temp folder
on every launch, which is slow and leaves a brief window where files sit
unpacked on disk. Folder mode avoids both problems.

WHAT THIS DOES NOT SOLVE:
A sufficiently motivated person can still disassemble the compiled binary
and patch out the check_license() call entirely -- no client-side license
check is unbreakable, compiled or not. What this DOES solve: casual/curious
users can no longer just open server.py in Notepad and read your pipeline
logic, prompt templates, or ITDE decision code. That's a real, meaningful
bar-raise, just not an absolute one.
"""

import subprocess
import sys

NUITKA_ARGS = [
    sys.executable, "-m", "nuitka",
    "--standalone",                    # bundle a full folder, not a single exe (see docstring)
    "--assume-yes-for-downloads",      # let Nuitka fetch its C compiler (MinGW) on first run without prompting
    "--show-progress",                 # print continuous output -- without this, long silent phases can look like a hang
    "--show-memory",
    # Note: --jobs=1 (single-threaded) is deliberately NOT used here anymore.
    # It was only needed earlier to rule out a Windows file-write race
    # condition, which turned out to actually be OneDrive evicting files to
    # cloud-only placeholders mid-build -- a real bug, now fixed by keeping
    # the project folder fully downloaded locally. Single-threaded compiles
    # are dramatically slower (hours instead of tens of minutes) with no
    # benefit now that the real cause is resolved.
    "--output-dir=dist",
    "--output-filename=DwaniLive.exe",

    # Packages Nuitka's static analysis sometimes misses because they load
    # things dynamically (compiled extensions, plugin-style imports).
    "--include-package=fastapi",
    "--include-package=uvicorn",
    "--include-package=starlette",
    "--include-package=faster_whisper",
    "--include-package=ctranslate2",
    "--include-package=sentencepiece",
    "--include-package=licensing",
    "--include-package=cryptography",

    # server.py, backends.py, qa_pipeline.py, nllb_tokenizer.py, pipeline.py,
    # session.py, glossary.py etc. don't need explicit --include-package
    # entries -- they're local modules launcher.py imports (via server.py),
    # and Nuitka's standalone mode traces + compiles every local import
    # automatically. Only third-party packages with dynamic-import patterns
    # need the explicit flags above.

    # Ship the static/ folder (host.html, index.html, css/js assets) next to
    # the exe, exactly as server.py's FileResponse/StaticFiles expects to
    # find it relative to the script's own location.
    "--include-data-dir=static=static",

    # Entry point -- launcher.py, not server.py directly (see docstring).
    "launcher.py",
]

print("Running:", " ".join(NUITKA_ARGS))
result = subprocess.run(NUITKA_ARGS)

if result.returncode != 0:
    print("\nBuild failed. Common causes:")
    print("  - Nuitka couldn't find a C compiler: let it auto-download MinGW "
          "(--assume-yes-for-downloads above should handle this), or install "
          "Visual Studio Build Tools yourself.")
    print("  - A package failed to trace: add it to --include-package above "
          "and re-run.")
    sys.exit(result.returncode)

print("\nBuild succeeded.")
print("Your distributable folder is at: dist/launcher.dist/")
print("Hand presenters that WHOLE FOLDER (not just the .exe) -- it contains")
print("the DLLs the exe needs. NO source .py files are in there, and NO")
print("model files need to be added manually -- DwaniLive.exe downloads")
print("nllb-200-ct2/ and sentencepiece.bpe.model itself on first run (see")
print("launcher.py's MODEL_BUNDLE_URL). faster-whisper downloads its own")
print("model on first use too, same as before.")