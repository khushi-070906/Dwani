"""Update-check tests against a local fake of GitHub's releases API."""

import importlib
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

RELEASE = {}


class _H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/latest":
            body = json.dumps(RELEASE).encode()
            self.send_response(200)
        elif self.path == "/DwaniLive-Setup.exe":
            body = b"MZ" + b"x" * 5000
            self.send_response(200)
        else:
            body = b""
            self.send_response(404)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture()
def up(tmp_path, monkeypatch):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    monkeypatch.setenv("DWANI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DWANI_UPDATE_API", base + "/latest")
    monkeypatch.delenv("DWANI_NO_UPDATE_CHECK", raising=False)
    import appenv, updater
    importlib.reload(appenv)
    appenv.ensure_dirs()
    u = importlib.reload(updater)
    monkeypatch.setattr(u.appenv, "APP_VERSION", "1.2.0")
    RELEASE.clear()
    yield u, base
    srv.shutdown()


def _release(base, tag, **kw):
    RELEASE.update({"tag_name": tag, "html_url": base + "/rel", "draft": False, "prerelease": False,
                    "assets": [{"name": "DwaniLive-Setup.exe", "browser_download_url": base + "/DwaniLive-Setup.exe",
                                "size": 5002}], **kw})


def test_version_compare():
    import updater
    assert updater.is_newer("v1.10.0", "1.9.9")
    assert updater.is_newer("v1.2.1", "1.2.0")
    assert not updater.is_newer("v1.2.0", "1.2.0")
    assert not updater.is_newer("v1.1.9", "1.2.0")
    assert updater.parse_version("v2") == (2, 0, 0)


def test_newer_release_is_reported(up):
    u, base = up
    _release(base, "v1.3.0")
    info = u.check_for_update()
    assert info and info.version == "1.3.0" and info.size_bytes == 5002


def test_same_or_older_release_is_ignored(up):
    u, base = up
    _release(base, "v1.2.0")
    assert u.check_for_update() is None
    _release(base, "v1.0.1")
    assert u.check_for_update() is None


def test_prerelease_and_missing_installer_ignored(up):
    u, base = up
    _release(base, "v9.0.0", prerelease=True)
    assert u.check_for_update() is None
    _release(base, "v9.0.0", assets=[])
    assert u.check_for_update() is None


def test_offline_is_silent(up, monkeypatch):
    u, _ = up
    monkeypatch.setattr(u, "API_URL", "http://127.0.0.1:9/nothing")
    assert u.check_for_update(timeout=1) is None


def test_opt_out(up, monkeypatch):
    u, base = up
    _release(base, "v9.0.0")
    monkeypatch.setenv("DWANI_NO_UPDATE_CHECK", "1")
    assert u.check_for_update() is None


def test_installer_download(up):
    u, base = up
    _release(base, "v1.3.0")
    path = u.download_installer(u.check_for_update())
    assert path.stat().st_size == 5002 and path.name == "DwaniLive-Setup-1.3.0.exe"
