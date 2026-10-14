"""My applications: a list on the phone, with the dates an RTI runs on.

What must not break:
  * the page's date arithmetic (static/js/tracker.js) gives the same answer as the server's own (outcomes.appeal_timing)
    for the same dates -- two copies of one rule are only safe if a test runs both;
  * nothing about a case is sent anywhere: the script makes no request, the page makes none for it;
  * every language has the screen text; the script is served with a type a browser runs, and the offline cache has it.
"""
import datetime as dt
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from dwaniforms import hardening, langpacks, outcomes
from dwaniforms.api import create_router
from dwaniforms.service import FormService

TestClient = pytest.importorskip("fastapi.testclient").TestClient
from fastapi import FastAPI  # noqa: E402

STATIC = Path(__file__).resolve().parents[1] / "dwaniforms" / "static"
SCRIPT = STATIC / "js" / "tracker.js"
NODE = shutil.which("node")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("DWANIFORMS_RATE_LIMIT", "0")
    svc = FormService(None, None)
    app = FastAPI(); app.include_router(create_router(svc)); hardening.install(app, svc, version="test")
    return TestClient(app)


def _cases():
    sent = dt.date(2026, 10, 1)
    out = []
    for gap in (0, 1, 29, 30, 31, 59, 60, 61, 90, 200):          # today, counted from the day it was sent
        today = sent + dt.timedelta(days=gap)
        out.append((sent, None, today))
        for rgap in (0, 5, 29):                                 # a reply that came `rgap` days after it was sent
            reply = sent + dt.timedelta(days=rgap)
            if reply <= today:
                out.append((sent, reply, today))
    return out


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_the_page_and_the_server_count_the_same_days():
    cases = _cases()
    script = (
        "const T=require(process.argv[1]);const cs=JSON.parse(require('fs').readFileSync(0,'utf8'));"
        "console.log(JSON.stringify(cs.map(c=>T.phase({kind:'rti',sent:c.sent,reply:c.reply||undefined},c.today))));"
    )
    payload = json.dumps([{"sent": s.isoformat(), "reply": r.isoformat() if r else None, "today": t.isoformat()} for s, r, t in cases])
    done = subprocess.run([NODE, "-e", script, str(SCRIPT)], input=payload, capture_output=True, text=True, check=True)
    js = json.loads(done.stdout)
    assert len(js) == len(cases) > 30
    for (sent, reply, today), got in zip(cases, js):
        py = outcomes.appeal_timing(sent.isoformat(), reply.isoformat() if reply else "", today)
        where = f"sent {sent}, reply {reply}, today {today}: python {py}, page {got}"
        if "early" in py:                                       # the office still has time
            assert got["phase"] == "wait" and got["date"] == py["early"], where
        else:                                                   # the appeal window
            assert got["phase"] == ("reply" if reply else "first") and got["date"] == py["last"], where
            assert bool(got["late"]) == bool(py.get("late")), where


def test_the_script_asks_nobody_and_hides_nothing_from_older_phones():
    js = SCRIPT.read_text(encoding="utf-8")
    for banned in ("fetch(", "XMLHttpRequest", "sendBeacon", "WebSocket", "eval(", "document.cookie"):
        assert banned not in js, banned
    assert "(?<" not in js                                      # no look-behind: an older iPhone refuses the whole script
    html = (STATIC / "app.html").read_text(encoding="utf-8")
    block = html[html.index("// ---------- My applications"):html.index("// ---------- installing on the phone")]
    assert "fetch(" not in block and "api(" not in block, "the tracker part of the page makes no request"


def test_the_first_appeal_is_started_with_only_the_office_and_the_date():
    html = (STATIC / "app.html").read_text(encoding="utf-8")
    # the values carried into the appeal are suggestions read back one by one, like a photo's
    assert "v.department = a.dept" in html and "v.rti_date = a.sent" in html
    assert 'form: "rti_first_appeal"' in html and '"/suggest"' in html


def test_the_script_is_served_with_a_type_a_browser_runs(client):
    r = client.get("/form/assets/js/tracker.js")
    assert r.status_code == 200 and "javascript" in r.headers["content-type"] and "DwaniTracker" in r.text
    assert client.get("/form/assets/js/nothing.js").status_code == 404
    assert client.get("/form/assets/js/..%2Fapp.html").status_code in (404, 422)


def test_the_offline_cache_holds_the_script_and_was_bumped():
    sw = (STATIC / "sw.js").read_text(encoding="utf-8")
    assert "./assets/js/tracker.js" in sw and 'CACHE = "dwaniforms-v2"' not in sw


def test_every_language_has_the_screen_text():
    html = (STATIC / "app.html").read_text(encoding="utf-8")
    import re
    keys = sorted(set(re.findall(r'\b(trk_[a-z0-9_]+)\b', html)))
    assert len(keys) >= 25
    for lang, pack in langpacks.packs().items():
        missing = [k for k in keys if not pack["ui"].get(k)]
        assert not missing, (lang, missing)


def test_the_note_says_what_is_and_is_not_kept(client):
    html = client.get("/form/").text
    assert 'id="trk-card"' in html and 'id="tracker"' in html and 'id="trk-save"' in html
    en = html[html.rindex("trk_note:"):][:400]        # the last table is the English one
    assert "only on this phone" in en and "not legal advice" in en and "enter the dates yourself" in en
