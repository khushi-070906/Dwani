"""DwaniForms as an app on the phone: the manifest and service worker it needs to be installed from the home screen,
and picking a half-filled form back up after the page reloads (the phone slept, the signal dropped, the app reopened).

The rule the service worker must never break: a citizen's ANSWERS are not kept in the browser. Only the session id is,
for that one tab; everything else lives on the server and is dropped there after 30 idle minutes."""
import json
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dwaniforms import records
from dwaniforms.api import create_router
from dwaniforms.service import FormService

STATIC = Path(__file__).resolve().parents[1] / "dwaniforms" / "static"
APP = (STATIC / "app.html").read_text(encoding="utf-8")
SW = (STATIC / "sw.js").read_text(encoding="utf-8")
MANIFEST = json.loads((STATIC / "manifest.webmanifest").read_text(encoding="utf-8"))


@pytest.fixture
def client(tmp_path):
    svc = FormService(None, None, record_store=records.RecordStore(records.write_demo(tmp_path, as_of="2026-09-01")))
    app = FastAPI()
    app.include_router(create_router(svc))
    return TestClient(app)


def test_manifest_is_served_and_installable(client):
    r = client.get("/form/manifest.webmanifest")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/manifest+json")
    m = r.json()
    # what a phone needs before it will offer "add to home screen"
    assert m["display"] == "standalone" and m["start_url"] == "./" and m["scope"] == "./"
    assert m["theme_color"] == "#1F5E4D"                      # same forest as the header
    sizes = {i["sizes"] for i in m["icons"]}
    assert {"192x192", "512x512"} <= sizes
    assert any(i.get("purpose") == "maskable" for i in m["icons"]), "Android crops a round icon out of this one"


@pytest.mark.parametrize("name", [i["src"].rsplit("/", 1)[-1] for i in MANIFEST["icons"]] + ["apple-touch-icon.png"])
def test_every_icon_exists_and_is_the_size_it_claims(client, name):
    r = client.get("/form/assets/img/" + name)
    assert r.status_code == 200 and r.content[:8] == b"\x89PNG\r\n\x1a\n"
    width = int.from_bytes(r.content[16:20], "big")
    expected = {"apple-touch-icon.png": 180}.get(name) or int(re.search(r"(\d+)\.png$", name).group(1))
    assert width == expected


def test_service_worker_is_served_with_a_scope_it_cannot_escape(client):
    r = client.get("/form/sw.js")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/javascript")
    assert r.headers["service-worker-allowed"] == "/form/"
    assert r.headers["cache-control"] == "no-cache"           # a stale worker is very hard to dislodge


def test_service_worker_never_stores_a_citizens_answers():
    assert "/sessions" in SW and re.search(r'indexOf\("/sessions"\) > -1\) return false', SW)
    cached = re.search(r"const SHELL = \[(.*?)\];", SW, re.S).group(1)
    assert "sessions" not in cached
    for path in re.findall(r'"(\./[^"]*)"', cached):          # only the page, icons and fonts are pre-stored
        assert path == "./" or path.startswith("./assets/") or path.endswith(".webmanifest"), path


def test_page_asks_the_phone_to_install_it():
    assert '<link rel="manifest" href="manifest.webmanifest">' in APP
    assert 'rel="apple-touch-icon"' in APP and 'name="apple-mobile-web-app-capable"' in APP
    assert 'navigator.serviceWorker.register("sw.js")' in APP
    assert 'location.protocol === "https:"' in APP            # never on a plain-http kiosk
    assert 'beforeinstallprompt' in APP and 'id="install-btn"' in APP


def test_only_the_session_id_is_kept_in_the_browser():
    assert 'sessionStorage.setItem(SID_KEY, id)' in APP       # this tab only, not localStorage
    for key in re.findall(r'localStorage\.setItem\("([^"]+)"', APP):
        assert key in ("dwaniforms:big", "dwaniforms:lang"), key   # text size and language: no personal answers


def _fill_one_answer(client):
    s = client.post("/form/sessions", json={"form_id": "grievance", "lang": "hi"}).json()
    sid = s["session_id"]
    client.post(f"/form/sessions/{sid}/text", json={"text": "सुनीता देवी"})
    client.post(f"/form/sessions/{sid}/text", json={"text": "हाँ"})
    return sid


def test_a_reloaded_page_picks_the_same_form_back_up(client):
    sid = _fill_one_answer(client)
    r = client.post(f"/form/sessions/{sid}/resume").json()
    assert r["session_id"] == sid and not r["done"]
    assert r["text"] and r["field_id"] == "mobile"            # asks the question it was on
    assert r["title"] and r["flow"] == "grievance"            # enough to redraw the whole screen
    name = [f for f in r["fields"] if f["id"] == "full_name"][0]
    assert name["value"] == "सुनीता देवी"                      # the answer already given is still there


def test_resume_asks_an_unconfirmed_answer_again_instead_of_keeping_it(client):
    sid = _fill_one_answer(client)
    said = client.post(f"/form/sessions/{sid}/text", json={"text": "98765 43210"}).json()
    assert said["phase"] == "confirm" and said["pending"]      # read back, waiting for a yes
    again = client.post(f"/form/sessions/{sid}/resume").json()
    assert again["phase"] == "ask" and not again["pending"] and again["field_id"] == "mobile"


def test_resume_of_a_forgotten_session_is_a_clean_404(client):
    assert client.post("/form/sessions/deadbeef/resume").status_code == 404


def test_answers_are_never_left_in_a_shared_cache(tmp_path, monkeypatch):
    """The real server (standalone) marks everything under /sessions no-store, resume included."""
    monkeypatch.setenv("DWANIFORMS_RATE_LIMIT", "0")
    from dwaniforms.hardening import install
    svc = FormService(None, None, record_store=records.RecordStore(records.write_demo(tmp_path, as_of="2026-09-01")))
    app = FastAPI()
    app.include_router(create_router(svc))
    install(app, svc)
    c = TestClient(app)
    sid = _fill_one_answer(c)
    assert c.get(f"/form/sessions/{sid}").headers["cache-control"] == "no-store"
    assert c.post(f"/form/sessions/{sid}/resume").headers["cache-control"] == "no-store"
    # the page, the manifest and the worker may be stored; they hold nobody's answers
    assert c.get("/form/manifest.webmanifest").headers["cache-control"] != "no-store"
