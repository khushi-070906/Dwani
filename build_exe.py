"""
build_exe.py

Compiles server.py into a standalone .exe using Nuitka -- real machine code,
not just zipped bytecode (PyInstaller-style), so it's genuinely hard to
recover readable source from, not just mildly inconvenient.

Run this ONCE per release, on Windows, inside your activated .venv:

    pip install nuitka --break-system-packages
    python build_exe.py

Output lands in dist/server.dist/ (standalone folder mode -- NOT --onefile).
Standalone folder mode is used instead of --onefile because faster-whisper /
ctranslate2 ship shared libraries (.dll files) and data alongside the
package; --onefile has to unpack everything to a temp folder on every
launch, which is slow and leaves a brief window where files sit unpacked
on disk. Folder mode avoids both problems.

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
    "--jobs=1",                        # single-threaded compile -- rules out Windows parallel-write file errors, slower but more reliable
    "--output-dir=dist",
    "--output-filename=DwaniLive.exe",

    # Packages Nuitka's static analysis sometimes misses because they load
    # things dynamically (compiled extensions, plugin-style imports).
    "--include-package=fastapi",
    "--include-package=uvicorn",
    "--include-package=starlette",
    "--include-package=faster_whisper",
    "--include-package=ctranslate2",
    "--include-package=licensing",
    "--include-package=cryptography",

    # Ship the static/ folder (host.html, index.html, css/js assets) next to
    # the exe, exactly as server.py's FileResponse/StaticFiles expects to
    # find it relative to the script's own location.
    "--include-data-dir=static=static",

    # Entry point.
    "server.py",
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
print("Your distributable folder is at: dist/server.dist/")
print("Hand presenters that WHOLE FOLDER (not just the .exe) -- it contains")
print("the DLLs the exe needs. Also copy your whisper-model and nllb-model-dir")
print("folders alongside it; those are NOT bundled by this script (they're")
print("large model weights, meant to be downloaded once per machine, not")
print("baked into every build).")
