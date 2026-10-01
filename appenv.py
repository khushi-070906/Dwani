"""
appenv.py

Everything about *where* DwaniLive runs, kept in one place so the desktop
build behaves the same no matter how the presenter got it onto their
machine (installer, unzipped folder, run from inside a zip preview, a
OneDrive-synced Desktop, Program Files, a dev checkout).

Rules this module enforces:

  * RESOURCE_DIR -- read-only things that ship with the app (static/).
    Resolved for: plain `python launcher.py`, Nuitka standalone, PyInstaller
    onedir AND onefile (sys._MEIPASS). Never written to.

  * DATA_DIR -- everything the app WRITES (models, logs, QR images, state).
    %LOCALAPPDATA%\\DwaniLive on Windows. Never next to the exe: the exe's
    folder can be read-only (Program Files, a zip opened in Explorer
    without extracting), cloud-synced (OneDrive evicts / locks 600MB
    model files mid-write), or wiped on update.
    Override with the DWANI_DATA_DIR environment variable.

  * A --windowed / --windows-console-mode=attach build has sys.stdout and
    sys.stderr set to None. The first print() anywhere then raises
    AttributeError -- this was the "double-click, nothing happens" bug.
    install_safe_streams() replaces them with a log-file tee before
    anything else runs.
"""

from __future__ import annotations

import ctypes
import io
import os
import platform
import shutil
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable, Optional

APP_NAME = "DwaniLive"
APP_VERSION = "1.3.0"

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

IS_FROZEN = bool(getattr(sys, "frozen", False)) or "__compiled__" in globals()


def _exe_dir() -> Path:
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _resource_dir() -> Path:
    # PyInstaller onefile/onedir unpack data next to the bootloader in _MEIPASS.
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass and (Path(meipass) / "static").is_dir():
        return Path(meipass)
    # Nuitka standalone puts --include-data-dir output next to the exe;
    # a dev checkout has static/ next to this file.
    for candidate in (Path(__file__).resolve().parent, _exe_dir()):
        if (candidate / "static").is_dir():
            return candidate
    return Path(__file__).resolve().parent


def _data_dir() -> Path:
    override = os.environ.get("DWANI_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP_NAME


EXE_DIR = _exe_dir()
RESOURCE_DIR = _resource_dir()
STATIC_DIR = RESOURCE_DIR / "static"
DATA_DIR = _data_dir()
MODELS_DIR = DATA_DIR / "models"
LOGS_DIR = DATA_DIR / "logs"
RUNTIME_DIR = DATA_DIR / "runtime"
LOG_FILE = LOGS_DIR / "dwanilive.log"
INSTANCE_FILE = RUNTIME_DIR / "launcher_url.txt"


def ensure_dirs() -> None:
    for d in (DATA_DIR, MODELS_DIR, LOGS_DIR, RUNTIME_DIR):
        d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Logging / safe stdio
# ---------------------------------------------------------------------------

class LogHub:
    """One sink for every line the process prints: rotating log file on
    disk (what support asks for), an in-memory ring buffer (what the
    launcher window shows), and line listeners (ready-detection)."""

    def __init__(self, max_lines: int = 600):
        self.lines: deque[str] = deque(maxlen=max_lines)
        self.seq = 0
        self._listeners: list[Callable[[str], None]] = []
        self._lock = threading.Lock()
        self._fh: Optional[io.TextIOBase] = None

    def open_file(self) -> None:
        try:
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            if LOG_FILE.exists() and LOG_FILE.stat().st_size > 5 * 1024 * 1024:
                old = LOG_FILE.with_suffix(".log.1")
                try:
                    old.unlink(missing_ok=True)
                    LOG_FILE.rename(old)
                except OSError:
                    pass
            self._fh = open(LOG_FILE, "a", encoding="utf-8", errors="replace", buffering=1)
            self._fh.write(f"\n===== {APP_NAME} {APP_VERSION} started {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
        except OSError:
            self._fh = None

    def add_listener(self, fn: Callable[[str], None]) -> None:
        self._listeners.append(fn)

    def emit(self, line: str) -> None:
        with self._lock:
            self.seq += 1
            self.lines.append(line)
            if self._fh:
                try:
                    self._fh.write(line + "\n")
                except Exception:
                    pass
        for fn in list(self._listeners):
            try:
                fn(line)
            except Exception:
                pass

    def snapshot(self, since: int = 0) -> tuple[int, list[str]]:
        with self._lock:
            n_new = max(0, min(self.seq - since, len(self.lines)))
            return self.seq, list(self.lines)[len(self.lines) - n_new:] if n_new else []


LOG = LogHub()


class _HubStream(io.TextIOBase):
    """Replacement for sys.stdout/sys.stderr. Writes to the real stream if
    one exists (console builds, `python launcher.py`), always to LOG."""

    def __init__(self, real, name: str):
        self._real = real
        self._buf = ""
        self.name = name

    @property
    def encoding(self):  # session.py checks sys.stdout.encoding before printing an ASCII QR
        return "utf-8"

    def writable(self) -> bool:
        return True

    def isatty(self) -> bool:
        return False

    def fileno(self):  # some libs probe this; behave like a pipe-less stream
        raise io.UnsupportedOperation("fileno")

    def write(self, s) -> int:
        if not isinstance(s, str):
            s = str(s)
        if self._real is not None:
            try:
                self._real.write(s)
            except Exception:
                pass
        self._buf += s
        while True:
            idx_candidates = [i for i in (self._buf.find("\n"), self._buf.find("\r")) if i != -1]
            if not idx_candidates:
                break
            idx = min(idx_candidates)
            line, self._buf = self._buf[:idx], self._buf[idx + 1:]
            if line.strip():
                LOG.emit(line.rstrip())
        return len(s)

    def flush(self) -> None:
        if self._real is not None:
            try:
                self._real.flush()
            except Exception:
                pass


_streams_installed = False


def install_safe_streams() -> None:
    global _streams_installed
    if _streams_installed:
        return
    _streams_installed = True
    LOG.open_file()
    real_out = sys.stdout if _usable(sys.stdout) else None
    real_err = sys.stderr if _usable(sys.stderr) else None
    if real_out is not None:
        try:
            real_out.reconfigure(encoding="utf-8", errors="replace")  # Windows cp1252 consoles choke on ध्वनि
        except Exception:
            pass
    sys.stdout = _HubStream(real_out, "stdout")
    sys.stderr = _HubStream(real_err, "stderr")
    if sys.stdin is None:
        sys.stdin = io.StringIO("")  # input() raises EOFError instead of hanging forever


def _usable(stream) -> bool:
    if stream is None:
        return False
    try:
        stream.write("")
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Single instance
# ---------------------------------------------------------------------------

_mutex_handle = None


def acquire_single_instance() -> bool:
    """True if we are the only DwaniLive running for this user. Two copies
    would each load ~2GB of models and fight over port 8000 -- on an 8GB
    laptop that is an out-of-memory crash, not a second session."""
    global _mutex_handle
    if sys.platform == "win32":
        try:
            ERROR_ALREADY_EXISTS = 183
            kernel32 = ctypes.windll.kernel32
            kernel32.CreateMutexW.restype = ctypes.c_void_p
            _mutex_handle = kernel32.CreateMutexW(None, False, "Local\\DwaniLiveSingleInstance")
            return kernel32.GetLastError() != ERROR_ALREADY_EXISTS
        except Exception:
            return True
    # POSIX: advisory lock file
    try:
        import fcntl

        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        fh = open(RUNTIME_DIR / "instance.lock", "w")
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _mutex_handle = fh
        return True
    except BlockingIOError:
        return False
    except Exception:
        return True


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------

def free_disk_bytes(path: Path) -> int:
    p = path
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        return shutil.disk_usage(p).free
    except OSError:
        return -1


def total_ram_bytes() -> int:
    if sys.platform == "win32":
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]
        try:
            st = MEMORYSTATUSEX()
            st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
            return int(st.ullTotalPhys)
        except Exception:
            return -1
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except Exception:
        return -1


def is_inside_temp_or_zip() -> bool:
    """Explorer lets people double-click an exe *inside* a zip; Windows then
    runs it from a temp folder with none of its sibling DLLs."""
    exe = str(EXE_DIR).lower()
    tmp = os.environ.get("TEMP", "").lower()
    return bool(tmp) and exe.startswith(tmp) and ".zip" in exe


def preflight() -> list[str]:
    """Returns human-readable warnings. Raises RuntimeError for hard stops."""
    warnings: list[str] = []
    if IS_FROZEN and is_inside_temp_or_zip():
        raise RuntimeError(
            "DwaniLive is being run from inside the .zip file. Right-click the zip -> "
            "'Extract All...', then run DwaniLive.exe from the extracted folder "
            "(or use DwaniLive-Setup.exe instead)."
        )
    try:
        ensure_dirs()
        probe = RUNTIME_DIR / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise RuntimeError(
            f"DwaniLive can't write to its data folder ({DATA_DIR}): {exc}. "
            f"Set the DWANI_DATA_DIR environment variable to a writable folder."
        )
    if not STATIC_DIR.is_dir() or not (STATIC_DIR / "host.html").is_file():
        raise RuntimeError(
            f"App files are missing ({STATIC_DIR} not found). The download is incomplete "
            f"or an antivirus removed files -- reinstall DwaniLive."
        )
    ram = total_ram_bytes()
    if 0 < ram < 6 * 1024**3:
        warnings.append(
            f"This computer has {ram / 1024**3:.1f} GB RAM. DwaniLive needs about 3 GB free -- "
            f"close Chrome tabs / other apps before starting a session."
        )
    return warnings


def system_summary() -> dict:
    return {
        "app_version": APP_VERSION,
        "frozen": IS_FROZEN,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "ram_gb": round(total_ram_bytes() / 1024**3, 1) if total_ram_bytes() > 0 else None,
        "free_disk_gb": round(free_disk_bytes(DATA_DIR) / 1024**3, 1),
        "exe_dir": str(EXE_DIR),
        "resource_dir": str(RESOURCE_DIR),
        "data_dir": str(DATA_DIR),
    }


# ---------------------------------------------------------------------------
# Friendly error text
# ---------------------------------------------------------------------------

def friendly_error(exc: BaseException) -> str:
    """Turn the exceptions presenters actually hit into one actionable line.
    The raw traceback still goes to the log file."""
    text = str(exc) or exc.__class__.__name__
    low = text.lower()
    if isinstance(exc, MemoryError) or "bad allocation" in low or "out of memory" in low:
        return "Ran out of memory loading the translation models. Close other apps (especially browsers) and try again."
    if "application control policy" in low or "blocked by your organization" in low:
        return (
            "Windows Smart App Control (or a company policy) blocked one of DwaniLive's files. "
            "Update to the latest DwaniLive. If it still happens: Windows Security -> App & browser "
            "control -> Smart App Control settings. Note that turning it Off can't be undone "
            "without resetting Windows, so on a managed/college PC ask your IT admin instead."
        )
    if "dll load failed" in low or "specified module could not be found" in low:
        return (
            "A system library failed to load. Install the Microsoft Visual C++ Redistributable "
            "(x64) from https://aka.ms/vs/17/release/vc_redist.x64.exe, then reopen DwaniLive. "
            "If it persists, your antivirus may have quarantined a DwaniLive file -- reinstall."
        )
    if "unsupported model binary version" in low or "failed to load" in low and "model" in low or "unable to open file 'model.bin'" in low:
        return "The downloaded model files are damaged or incompatible. Click 'Re-download models'."
    if "certificate verify failed" in low:
        return "Secure connection failed (network is intercepting HTTPS). Try another network or a phone hotspot for the one-time download."
    if isinstance(exc, PermissionError):
        return f"Windows blocked access to a file: {text}. Close other DwaniLive windows and retry; if it persists, your antivirus may be locking the file."
    if "getaddrinfo failed" in low or "name or service not known" in low or "urlopen error" in low:
        return "No internet connection. The first launch needs internet once to download models (~1.1 GB); after that it runs offline."
    if "no space left" in low or "not enough space" in low or "errno 28" in low:
        return "Not enough disk space. Free up at least 3 GB and try again."
    return text


# ---------------------------------------------------------------------------
# No flashing console windows
# ---------------------------------------------------------------------------

def suppress_child_console_windows() -> None:
    """A windowed exe that runs netsh/ipconfig via subprocess makes a black
    console window flash up for each call. Default every child process to
    CREATE_NO_WINDOW (callers that pass their own creationflags keep them)."""
    if sys.platform != "win32":
        return
    import subprocess

    CREATE_NO_WINDOW = 0x08000000
    original_init = subprocess.Popen.__init__
    if getattr(original_init, "_dwani_patched", False):
        return

    def patched_init(self, *args, **kwargs):
        if not kwargs.get("creationflags"):
            kwargs["creationflags"] = CREATE_NO_WINDOW
        original_init(self, *args, **kwargs)

    patched_init._dwani_patched = True  # type: ignore[attr-defined]
    subprocess.Popen.__init__ = patched_init  # type: ignore[method-assign]
