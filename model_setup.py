"""
model_setup.py

One-time model installation for the desktop app, built for the networks
presenters actually have (college WiFi that drops every few minutes, a
phone hotspot, proxies) rather than a dev machine on fibre.

What the old launcher did vs. what this does:

  old: urlretrieve() 580MB in one shot, no timeout, no retry, no resume.
       Any blip -> start from 0. Interrupted extraction left a half-written
       nllb-200-ct2/ that `is_dir()` reported as "installed" -> ctranslate2
       crashed on every launch afterwards until the user deleted it by hand.
       Whisper was left to faster-whisper's own first-use Hugging Face
       download: no progress shown, silently stuck on slow networks.
  new: * HTTP Range resume from a .part file + retries with backoff
       * size check against Content-Length (+ optional SHA-256)
       * extract into a temp dir, validate, then move into place, then
         write a `.dwani-complete` marker -- a model counts as installed
         ONLY if the marker exists and the key files have the right size
       * Whisper downloaded by us too (same resume/progress), into
         DATA_DIR, and handed to WhisperModel as a local path -- so the
         running app never touches the network again
       * disk-space check up front
       * reuses models from older installs (next to the exe) instead of
         re-downloading 1GB
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import shutil
import socket
import ssl
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import appenv

# ---------------------------------------------------------------------------
# Configure per release
# ---------------------------------------------------------------------------

NLLB_BUNDLE_URL = os.environ.get(
    "DWANI_MODEL_BUNDLE_URL",
    "https://github.com/khushi-070906/Dwani-models/releases/download/v1.0/dwani-models.zip",
)
# Leave empty to skip hash verification (size is always checked). Fill in
# with the value build_exe.py prints for the bundle -- `python build_exe.py
# --hash-bundle path/to/dwani-models.zip`.
NLLB_BUNDLE_SHA256 = os.environ.get("DWANI_MODEL_BUNDLE_SHA256", "")

WHISPER_MODEL_SIZE = "small"
WHISPER_REPO = "Systran/faster-whisper-small"
WHISPER_FILES = ["config.json", "model.bin", "tokenizer.json", "vocabulary.txt"]
# Mirrors are tried in order; huggingface.co is blocked on some campus
# networks, hf-mirror.com usually isn't.
WHISPER_BASE_URLS = [
    u for u in (
        os.environ.get("DWANI_WHISPER_BASE_URL"),  # e.g. your own GitHub release mirror: .../download/v1.0/{file}
        "https://huggingface.co/{repo}/resolve/main/{file}",
        "https://hf-mirror.com/{repo}/resolve/main/{file}",
    ) if u
]

NLLB_DIR = appenv.MODELS_DIR / "nllb-200-ct2"
SPM_FILE = appenv.MODELS_DIR / "sentencepiece.bpe.model"
WHISPER_DIR = appenv.MODELS_DIR / f"faster-whisper-{WHISPER_MODEL_SIZE}"
MARKER = ".dwani-complete"

USER_AGENT = f"DwaniLive/{appenv.APP_VERSION}"
CHUNK = 1024 * 1024
READ_TIMEOUT = 30
MAX_ATTEMPTS = 8


@dataclass
class Progress:
    stage: str             # "nllb" | "whisper" | "extract" | "verify"
    label: str             # human text for the UI
    done_bytes: int = 0
    total_bytes: int = 0
    speed_bps: float = 0.0
    eta_s: Optional[float] = None
    note: str = ""         # e.g. "Connection dropped -- retrying in 4s"

    @property
    def pct(self) -> int:
        return int(self.done_bytes * 100 / self.total_bytes) if self.total_bytes > 0 else 0


ProgressCb = Callable[[Progress], None]


class DownloadError(RuntimeError):
    pass


class CancelledError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Low-level resumable download
# ---------------------------------------------------------------------------

def _ssl_contexts():
    yield ssl.create_default_context()
    try:
        import certifi  # bundled in the build; covers machines with a stale Windows root store

        yield ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return


def _open(url: str, start: int, ctx: ssl.SSLContext):
    headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
    if start > 0:
        headers["Range"] = f"bytes={start}-"
    req = urllib.request.Request(url, headers=headers)
    return urllib.request.urlopen(req, timeout=READ_TIMEOUT, context=ctx)


def download_file(
    url: str,
    dest: Path,
    *,
    label: str,
    stage: str,
    progress_cb: Optional[ProgressCb] = None,
    expected_size: Optional[int] = None,
    cancel: Optional[Callable[[], bool]] = None,
) -> Path:
    """Downloads url -> dest with resume. Returns dest. Raises DownloadError."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    report = progress_cb or (lambda p: None)
    last_exc: Optional[BaseException] = None
    ctx_list = list(_ssl_contexts())

    for attempt in range(1, MAX_ATTEMPTS + 1):
        start = part.stat().st_size if part.exists() else 0
        ctx = ctx_list[min(attempt - 1, len(ctx_list) - 1)] if isinstance(last_exc, ssl.SSLError) else ctx_list[0]
        try:
            with _open(url, start, ctx) as resp:
                status = getattr(resp, "status", 200)
                if start > 0 and status != 206:
                    start = 0  # server ignored Range -> restart cleanly
                length = resp.headers.get("Content-Length")
                total = (int(length) + start) if length and length.isdigit() else (expected_size or 0)
                if expected_size and total and total != expected_size:
                    total = expected_size

                mode = "ab" if start > 0 else "wb"
                done = start
                t0, bytes_since = time.monotonic(), 0
                speed = 0.0
                with open(part, mode) as fh:
                    while True:
                        if cancel and cancel():
                            raise CancelledError("Download cancelled")
                        chunk = resp.read(CHUNK)
                        if not chunk:
                            break
                        fh.write(chunk)
                        done += len(chunk)
                        bytes_since += len(chunk)
                        now = time.monotonic()
                        if now - t0 >= 0.5:
                            inst = bytes_since / (now - t0)
                            speed = inst if speed == 0 else 0.7 * speed + 0.3 * inst
                            t0, bytes_since = now, 0
                            eta = (total - done) / speed if speed > 0 and total else None
                            report(Progress(stage, label, done, total, speed, eta))
            if total and done < total:
                raise DownloadError(f"connection closed early ({done}/{total} bytes)")
            if expected_size and part.stat().st_size != expected_size:
                raise DownloadError(f"size mismatch ({part.stat().st_size} != {expected_size})")
            os.replace(part, dest)
            report(Progress(stage, label, done, total or done, speed, 0))
            return dest
        except CancelledError:
            raise
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if exc.code == 416:  # Range not satisfiable: .part is already complete or corrupt
                part.unlink(missing_ok=True)
            elif exc.code in (401, 403, 404):
                raise DownloadError(f"{url} returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, http.client.HTTPException, socket.timeout, ConnectionError, ssl.SSLError, TimeoutError, DownloadError, OSError) as exc:
            last_exc = exc
            if isinstance(exc, OSError) and getattr(exc, "errno", None) == 28:
                raise  # disk full: retrying won't help
        if attempt < MAX_ATTEMPTS:
            wait = min(30, 2 ** attempt)
            for remaining in range(wait, 0, -1):
                if cancel and cancel():
                    raise CancelledError("Download cancelled")
                done_now = part.stat().st_size if part.exists() else 0
                report(Progress(stage, label, done_now, 0, 0, None,
                                note=f"Connection problem ({_short(last_exc)}) -- retrying in {remaining}s"))
                time.sleep(1)
    raise DownloadError(f"Download failed after {MAX_ATTEMPTS} attempts: {_short(last_exc)}")


def _short(exc: Optional[BaseException]) -> str:
    if exc is None:
        return "unknown error"
    reason = getattr(exc, "reason", None)
    return str(reason or exc)[:140]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def remote_size(url: str) -> int:
    for ctx in _ssl_contexts():
        try:
            req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=READ_TIMEOUT, context=ctx) as resp:
                return int(resp.headers.get("Content-Length") or 0)
        except Exception:
            continue
    return 0


# ---------------------------------------------------------------------------
# Install state
# ---------------------------------------------------------------------------

def _marker_ok(folder: Path, required: dict[str, int]) -> bool:
    """required: {relative_file: min_size_bytes}"""
    if not (folder / MARKER).is_file():
        return False
    for rel, min_size in required.items():
        p = folder / rel
        if not p.is_file() or p.stat().st_size < min_size:
            return False
    return True


def _write_marker(folder: Path, info: dict) -> None:
    (folder / MARKER).write_text(json.dumps(info, indent=2), encoding="utf-8")


NLLB_REQUIRED = {"model.bin": 100 * 1024 * 1024, "config.json": 2, "shared_vocabulary.json": 1000}
WHISPER_REQUIRED = {"model.bin": 100 * 1024 * 1024, "config.json": 2, "tokenizer.json": 1000}


def nllb_installed() -> bool:
    return _marker_ok(NLLB_DIR, NLLB_REQUIRED) and SPM_FILE.is_file() and SPM_FILE.stat().st_size > 100_000


def whisper_installed() -> bool:
    return _marker_ok(WHISPER_DIR, WHISPER_REQUIRED)


def all_installed() -> bool:
    return nllb_installed() and whisper_installed()


def _looks_like_nllb(folder: Path) -> bool:
    return all((folder / f).is_file() for f in NLLB_REQUIRED) and (folder / "model.bin").stat().st_size > NLLB_REQUIRED["model.bin"]


def _adopt_legacy_models() -> None:
    """Older builds saved models next to DwaniLive.exe. Move (or copy, if
    on another drive) them into DATA_DIR instead of re-downloading."""
    if nllb_installed():
        return
    for base in {appenv.EXE_DIR, appenv.RESOURCE_DIR}:
        legacy_nllb, legacy_spm = base / "nllb-200-ct2", base / "sentencepiece.bpe.model"
        if legacy_nllb.is_dir() and legacy_spm.is_file() and _looks_like_nllb(legacy_nllb):
            try:
                print(f"Reusing models from previous install at {base}")
                shutil.rmtree(NLLB_DIR, ignore_errors=True)
                shutil.copytree(legacy_nllb, NLLB_DIR)
                shutil.copy2(legacy_spm, SPM_FILE)
                _write_marker(NLLB_DIR, {"source": str(legacy_nllb), "adopted": time.time()})
                return
            except OSError as exc:
                print(f"Couldn't reuse old models ({exc}); downloading fresh.")
                shutil.rmtree(NLLB_DIR, ignore_errors=True)


# ---------------------------------------------------------------------------
# Installers
# ---------------------------------------------------------------------------

def _find_nllb_in(root: Path) -> tuple[Optional[Path], Optional[Path]]:
    """Locate nllb-200-ct2/ and sentencepiece.bpe.model anywhere within two
    levels -- tolerant of whatever wrapper folder the zip was made with."""
    nllb = spm = None
    for depth_glob in ("*", "*/*", "*/*/*"):
        for p in root.glob(depth_glob):
            if p.is_dir() and p.name == "nllb-200-ct2" and nllb is None and _looks_like_nllb(p):
                nllb = p
            elif p.is_file() and p.name == "sentencepiece.bpe.model" and spm is None:
                spm = p
    if spm is None and nllb is not None and (nllb / "sentencepiece.bpe.model").is_file():
        spm = nllb / "sentencepiece.bpe.model"
    return nllb, spm


def install_nllb(progress_cb: Optional[ProgressCb] = None, cancel=None) -> None:
    if nllb_installed():
        return
    report = progress_cb or (lambda p: None)
    zip_path = appenv.MODELS_DIR / "dwani-models.zip"
    tmp_dir = appenv.MODELS_DIR / "_extract_tmp"

    if not zip_path.exists():
        expected = remote_size(NLLB_BUNDLE_URL) or None
        download_file(NLLB_BUNDLE_URL, zip_path, label="Translation model", stage="nllb",
                      progress_cb=progress_cb, expected_size=expected, cancel=cancel)

    if NLLB_BUNDLE_SHA256:
        report(Progress("verify", "Verifying download…"))
        actual = sha256_file(zip_path)
        if actual.lower() != NLLB_BUNDLE_SHA256.lower():
            zip_path.unlink(missing_ok=True)
            raise DownloadError("Downloaded model file is corrupted (checksum mismatch). Please retry -- it will re-download.")

    report(Progress("extract", "Unpacking translation model…"))
    shutil.rmtree(tmp_dir, ignore_errors=True)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            bad = zf.testzip()
            if bad is not None:
                raise zipfile.BadZipFile(f"corrupt member {bad}")
            members = zf.infolist()
            total = sum(m.file_size for m in members) or 1
            done = 0
            for m in members:
                name = m.filename.replace("\\", "/")  # zips made with PowerShell Compress-Archive use backslashes
                if name.endswith("/") or "/.cache/" in f"/{name}":
                    continue
                target = (tmp_dir / name).resolve()
                if not str(target).startswith(str(tmp_dir.resolve())):
                    continue  # zip-slip guard
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(m) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst, CHUNK)
                done += m.file_size
                report(Progress("extract", "Unpacking translation model…", done, total))
    except zipfile.BadZipFile as exc:
        zip_path.unlink(missing_ok=True)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise DownloadError(f"Downloaded model file is damaged ({exc}). Retry to download it again.")

    nllb_src, spm_src = _find_nllb_in(tmp_dir)
    if nllb_src is None or spm_src is None:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise DownloadError(
            "Model bundle doesn't contain nllb-200-ct2/ + sentencepiece.bpe.model. "
            "(Developer: check NLLB_BUNDLE_URL points at the right zip.)"
        )

    shutil.rmtree(NLLB_DIR, ignore_errors=True)
    shutil.move(str(nllb_src), str(NLLB_DIR))
    if SPM_FILE.exists():
        SPM_FILE.unlink()
    shutil.move(str(spm_src), str(SPM_FILE))
    _write_marker(NLLB_DIR, {"url": NLLB_BUNDLE_URL, "installed": time.time()})
    shutil.rmtree(tmp_dir, ignore_errors=True)
    zip_path.unlink(missing_ok=True)
    if not nllb_installed():
        raise DownloadError("Translation model failed validation after install.")


def install_whisper(progress_cb: Optional[ProgressCb] = None, cancel=None) -> None:
    if whisper_installed():
        return
    WHISPER_DIR.mkdir(parents=True, exist_ok=True)
    (WHISPER_DIR / MARKER).unlink(missing_ok=True)
    errors = []
    for fname in WHISPER_FILES:
        dest = WHISPER_DIR / fname
        if dest.is_file() and dest.stat().st_size > 0:
            continue
        for template in WHISPER_BASE_URLS:
            url = template.format(repo=WHISPER_REPO, file=fname)
            try:
                download_file(url, dest, label="Speech recognition model", stage="whisper",
                              progress_cb=progress_cb, cancel=cancel)
                break
            except CancelledError:
                raise
            except Exception as exc:
                errors.append(f"{url}: {exc}")
        else:
            raise DownloadError("Couldn't download the speech model. " + " | ".join(errors[-2:]))
    _write_marker(WHISPER_DIR, {"repo": WHISPER_REPO, "installed": time.time()})
    if not whisper_installed():
        raise DownloadError("Speech model failed validation after download.")


def ensure_models(progress_cb: Optional[ProgressCb] = None, cancel=None) -> None:
    appenv.ensure_dirs()
    _adopt_legacy_models()
    if all_installed():
        return

    need = 0
    if not nllb_installed():
        need += 1_900 * 1024 * 1024  # zip (~580MB) + extracted (~630MB) + headroom
    if not whisper_installed():
        need += 600 * 1024 * 1024
    free = appenv.free_disk_bytes(appenv.MODELS_DIR)
    if 0 <= free < need:
        raise DownloadError(
            f"Not enough disk space on the drive holding {appenv.DATA_DIR}: "
            f"{free / 1024**3:.1f} GB free, need about {need / 1024**3:.1f} GB."
        )

    install_whisper(progress_cb, cancel)  # smaller first: fast visible progress, fails fast on network issues
    install_nllb(progress_cb, cancel)


def reset_models() -> None:
    shutil.rmtree(appenv.MODELS_DIR, ignore_errors=True)
    appenv.ensure_dirs()
