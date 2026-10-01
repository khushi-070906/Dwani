"""Download/install robustness tests for model_setup.py -- no internet,
a local HTTP server stands in for GitHub/Hugging Face (Range support,
dropped connections, 404s)."""

import importlib
import io
import os
import sys
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class _Files:
    blobs: dict = {}
    drop_first_n: dict = {}   # path -> remaining drops
    ignore_range = False


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _blob(self):
        return _Files.blobs.get(self.path)

    def do_HEAD(self):
        b = self._blob()
        if b is None:
            self.send_response(404); self.end_headers(); return
        self.send_response(200)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()

    def do_GET(self):
        b = self._blob()
        if b is None:
            self.send_response(404); self.end_headers(); return
        start = 0
        rng = self.headers.get("Range")
        if rng and not _Files.ignore_range:
            start = int(rng.split("=")[1].split("-")[0])
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(b)-1}/{len(b)}")
        else:
            self.send_response(200)
        body = b[start:]
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        drops = _Files.drop_first_n.get(self.path, 0)
        if drops:
            _Files.drop_first_n[self.path] = drops - 1
            self.wfile.write(body[: len(body) // 3])
            self.wfile.flush()
            self.connection.shutdown(2)  # simulate WiFi dropping mid-download
            return
        self.wfile.write(body)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    monkeypatch.setenv("DWANI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DWANI_MODEL_BUNDLE_URL", base + "/bundle.zip")
    monkeypatch.setenv("DWANI_WHISPER_BASE_URL", base + "/whisper/{file}")
    import appenv, model_setup
    importlib.reload(appenv)
    ms = importlib.reload(model_setup)
    ms.WHISPER_BASE_URLS = [base + "/whisper/{file}"]
    monkeypatch.setattr(ms.time, "sleep", lambda s: None)
    monkeypatch.setattr(ms, "NLLB_REQUIRED", {"model.bin": 10, "config.json": 1, "shared_vocabulary.json": 1})
    monkeypatch.setattr(ms, "WHISPER_REQUIRED", {"model.bin": 10, "config.json": 1, "tokenizer.json": 1})
    _Files.blobs, _Files.drop_first_n, _Files.ignore_range = {}, {}, False
    yield ms, base
    srv.shutdown()


def _bundle(backslashes=False, wrapper="dwani-models"):
    buf = io.BytesIO()
    sep = "\\" if backslashes else "/"
    with zipfile.ZipFile(buf, "w") as z:
        p = lambda *parts: sep.join([wrapper, *parts]) if wrapper else sep.join(parts)
        z.writestr(p("nllb-200-ct2", "model.bin"), os.urandom(5000))
        z.writestr(p("nllb-200-ct2", "config.json"), "{}")
        z.writestr(p("nllb-200-ct2", "shared_vocabulary.json"), "[]")
        z.writestr(p("nllb-200-ct2", ".cache", "huggingface", "junk"), "x")
        z.writestr(p("sentencepiece.bpe.model"), os.urandom(200_000))
    return buf.getvalue()


def _whisper_files():
    for f, data in {"config.json": b"{}", "model.bin": os.urandom(4000), "tokenizer.json": b"{}", "vocabulary.txt": b"a"}.items():
        _Files.blobs[f"/whisper/{f}"] = data


def test_full_install_with_backslash_zip(env):
    ms, _ = env
    _Files.blobs["/bundle.zip"] = _bundle(backslashes=True)
    _whisper_files()
    seen = []
    ms.ensure_models(seen.append)
    assert ms.all_installed()
    assert not (ms.NLLB_DIR / ".cache").exists()
    assert not (ms.appenv.MODELS_DIR / "dwani-models.zip").exists()
    assert any(p.stage == "extract" for p in seen)


def test_resume_after_connection_drop(env):
    ms, base = env
    data = os.urandom(3 * 1024 * 1024 + 17)
    _Files.blobs["/big.bin"] = data
    _Files.drop_first_n["/big.bin"] = 2
    notes = []
    dest = ms.appenv.MODELS_DIR / "big.bin"
    ms.download_file(base + "/big.bin", dest, label="t", stage="nllb", progress_cb=lambda p: notes.append(p.note))
    assert dest.read_bytes() == data
    assert any("retrying" in n for n in notes)


def test_server_ignoring_range_restarts_cleanly(env):
    ms, base = env
    data = os.urandom(2 * 1024 * 1024)
    _Files.blobs["/f.bin"] = data
    _Files.drop_first_n["/f.bin"] = 1
    _Files.ignore_range = True
    dest = ms.appenv.MODELS_DIR / "f.bin"
    ms.download_file(base + "/f.bin", dest, label="t", stage="nllb")
    assert dest.read_bytes() == data


def test_404_fails_fast_with_clear_error(env):
    ms, base = env
    with pytest.raises(ms.DownloadError, match="404"):
        ms.download_file(base + "/missing.zip", ms.appenv.MODELS_DIR / "x.zip", label="t", stage="nllb")


def test_interrupted_install_is_not_treated_as_installed(env):
    ms, _ = env
    ms.NLLB_DIR.mkdir(parents=True)
    (ms.NLLB_DIR / "model.bin").write_bytes(b"partial")
    ms.SPM_FILE.write_bytes(os.urandom(200_000))
    assert not ms.nllb_installed()  # old launcher said "installed" here and crashed forever


def test_corrupt_zip_is_deleted_so_retry_redownloads(env):
    ms, _ = env
    _Files.blobs["/bundle.zip"] = b"PK\x03\x04 this is not a zip"
    with pytest.raises(ms.DownloadError):
        ms.install_nllb()
    assert not (ms.appenv.MODELS_DIR / "dwani-models.zip").exists()


def test_flat_zip_without_wrapper_folder(env):
    ms, _ = env
    _Files.blobs["/bundle.zip"] = _bundle(wrapper="")
    ms.install_nllb()
    assert ms.nllb_installed()


def test_disk_space_check(env, monkeypatch):
    ms, _ = env
    monkeypatch.setattr(ms.appenv, "free_disk_bytes", lambda p: 100 * 1024 * 1024)
    with pytest.raises(ms.DownloadError, match="disk space"):
        ms.ensure_models()


def test_legacy_models_next_to_exe_are_reused(env, tmp_path, monkeypatch):
    ms, _ = env
    legacy = tmp_path / "old_install"
    (legacy / "nllb-200-ct2").mkdir(parents=True)
    (legacy / "nllb-200-ct2" / "model.bin").write_bytes(os.urandom(5000))
    (legacy / "nllb-200-ct2" / "config.json").write_text("{}")
    (legacy / "nllb-200-ct2" / "shared_vocabulary.json").write_text("[]")
    (legacy / "sentencepiece.bpe.model").write_bytes(os.urandom(200_000))
    monkeypatch.setattr(ms.appenv, "EXE_DIR", legacy)
    ms._adopt_legacy_models()
    assert ms.nllb_installed()
