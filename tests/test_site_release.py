"""Website release info (/api/release, /api/releases, /changelog): cached, survives
GitHub outages. Needs the website's own dependencies + a database; skipped otherwise
(the desktop-app CI job doesn't install them)."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
for mod in ("psycopg2", "bcrypt", "razorpay"):
    pytest.importorskip(mod)
if not (os.environ.get("DATABASE_URL", "").startswith("postgres") and os.environ.get("LDST_LICENSE_PRIVATE_KEY")):
    pytest.skip("website tests need DATABASE_URL (Postgres) and LDST_LICENSE_PRIVATE_KEY", allow_module_level=True)

import license_server as ls  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

FAKE = [{"tag": "v1.8.1", "version": "1.8.1", "published_at": "2026-10-02T09:00:00Z", "url": "u",
         "setup_url": "https://x/v1.8.1/DwaniLive-Setup.exe", "zip_url": "z", "notes": "## What's new\n- Fix A\n- Fix B"},
        {"tag": "v1.8.0", "version": "1.8.0", "published_at": "2026-10-01T09:00:00Z", "url": "u", "setup_url": None,
         "zip_url": None, "notes": "**Full Changelog**: ..."}]


@pytest.fixture()
def client(monkeypatch):
    calls = {"n": 0}

    def fake_fetch():
        calls["n"] += 1
        return FAKE
    monkeypatch.setattr(ls, "_fetch_releases", fake_fetch)
    ls._release_cache.update(at=0.0, data=None)
    c = TestClient(ls.app)
    c.calls = calls
    return c


def test_latest_and_all(client):
    r = client.get("/api/release")
    assert r.status_code == 200 and r.json()["version"] == "1.8.1" and "max-age" in r.headers["cache-control"]
    assert [x["version"] for x in client.get("/api/releases").json()] == ["1.8.1", "1.8.0"]
    assert client.calls["n"] == 1          # second call served from cache


def test_github_outage_keeps_last_good_copy(client, monkeypatch):
    client.get("/api/release")
    def boom():
        raise OSError("github down")
    monkeypatch.setattr(ls, "_fetch_releases", boom)
    ls._release_cache["at"] = 0.0           # cache expired
    assert client.get("/api/release").json()["version"] == "1.8.1"


def test_no_data_at_all_is_503(monkeypatch):
    def boom():
        raise OSError("github down")
    monkeypatch.setattr(ls, "_fetch_releases", boom)
    ls._release_cache.update(at=0.0, data=None)
    assert TestClient(ls.app).get("/api/release").status_code == 503


def test_changelog_page_served(client):
    for path in ("/changelog", "/changelog.html"):
        r = client.get(path)
        assert r.status_code == 200 and "What's new" in r.text
