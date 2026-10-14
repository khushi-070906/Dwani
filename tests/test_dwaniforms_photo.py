"""Samjhao photo reading: the phone reads the picture and suggests the kind of paper, its date and its amount.

What must not break:
  * the picture never reaches the server -- only up to three short values do, and each is checked like a typed answer;
  * every suggestion is read back with "is this correct?" and a "no" asks the question the normal way;
  * a value that does not fit (a made-up paper, a date years away, a negative amount) is dropped, not guessed;
  * an answer the person already gave is never overwritten by the photo;
  * the engine files are served so a browser can run them (wasm with the wasm type, CSP allows WebAssembly only).
"""
import asyncio
import datetime as dt
from pathlib import Path

import pytest

from dwaniforms import clock, langpacks
from dwaniforms.service import FormService

TestClient = pytest.importorskip("fastapi.testclient").TestClient
from fastapi import FastAPI  # noqa: E402

from dwaniforms import hardening  # noqa: E402
from dwaniforms.api import create_router  # noqa: E402

OCR = Path(__file__).resolve().parents[1] / "dwaniforms" / "static" / "ocr"
LANGS = ["en", "hi", *sorted(langpacks.packs())]
SOON = (clock.today() + dt.timedelta(days=9)).isoformat()


def _yes(lang):
    return {"en": "yes", "hi": "हाँ"}.get(lang) or langpacks.words(lang, "yes")[0]


def _no(lang):
    return {"en": "no", "hi": "नहीं"}.get(lang) or langpacks.words(lang, "no")[0]


def _start(lang="en"):
    async def go():
        svc = FormService()
        s, _ = await svc.create("samjhao", lang)
        return svc, s
    return asyncio.run(go())


def _ask(svc, s):
    return asyncio.run(svc.finish(s, asyncio.run(s.ask_current())))


def _say(svc, s, text):
    return asyncio.run(svc.answer_text(s.id, text))


# ---- the session ------------------------------------------------------------------------------------------------
def test_suggestions_are_read_back_one_by_one_and_lead_to_the_explanation():
    svc, s = _start("en")
    assert s.suggest({"paper": "Electricity bill", "due_date": SOON, "amount": "2890"}) == ["paper", "due_date", "amount"]
    r = _ask(svc, s)
    assert r.phase == "confirm" and "Electricity bill" in r.text and r.field_id == "paper"
    r = _say(svc, s, "yes")
    assert r.phase == "confirm" and r.field_id == "due_date"
    r = _say(svc, s, "yes")
    assert r.phase == "confirm" and r.field_id == "amount" and "2890" in r.text
    r = _say(svc, s, "yes")
    assert r.done and r.outcome["type"] == "explain"
    assert s.answers["paper"].value == "Electricity bill" and s.answers["due_date"].value == SOON


def test_a_no_asks_the_question_the_normal_way_and_the_wrong_guess_is_gone():
    svc, s = _start("en")
    s.suggest({"paper": "Water bill"})
    r = _ask(svc, s)
    assert r.phase == "confirm"
    r = _say(svc, s, "no")
    assert r.phase == "ask" and r.field_id == "paper" and "paper" not in s.answers
    r = _say(svc, s, "electricity bill")
    assert s.answers["paper"].value == "Electricity bill"


@pytest.mark.parametrize("bad", [
    {"paper": "Spaceship licence"},                        # not a paper Samjhao knows
    {"due_date": "2040-12-31"},                            # years away: a misread, not a last date
    {"due_date": "99/99/2026"},
    {"amount": "0"},
    {"amount": "lots"},
    {"nonsense_field": "x"},
    {"paper": ""},
])
def test_values_that_do_not_fit_are_dropped(bad):
    _, s = _start("en")
    assert s.suggest(bad) == []
    assert not s.suggestions


def test_what_the_person_already_said_is_never_overwritten():
    svc, s = _start("en")
    _say(svc, s, "water bill")
    assert s.answers["paper"].value == "Water bill"
    assert "paper" not in s.suggest({"paper": "Electricity bill", "amount": "640"})
    assert s.answers["paper"].value == "Water bill"


def test_an_iso_date_and_a_printed_date_both_work():
    _, s = _start("en")
    d = clock.today() + dt.timedelta(days=9)
    assert s.suggest({"due_date": d.isoformat()}) == ["due_date"]
    assert s.suggestions["due_date"][0] == d.isoformat()


@pytest.mark.parametrize("lang", LANGS)
def test_every_language_reads_a_suggestion_back_and_accepts_the_yes(lang):
    svc, s = _start(lang)
    s.suggest({"paper": "Electricity bill", "due_date": SOON})
    r = _ask(svc, s)
    assert r.phase == "confirm" and r.field_id == "paper"
    r = _say(svc, s, _yes(lang))
    assert r.field_id == "due_date" and r.phase == "confirm"
    r = _say(svc, s, _no(lang))                              # a wrong date is refused and asked again in full
    assert r.phase == "ask" and r.field_id == "due_date"


# ---- the HTTP layer ---------------------------------------------------------------------------------------------
def _client(monkeypatch):
    monkeypatch.setenv("DWANIFORMS_RATE_LIMIT", "0")
    svc = FormService(None, None)
    app = FastAPI()
    app.include_router(create_router(svc))
    hardening.install(app, svc, version="test")
    return TestClient(app), svc


def test_suggest_endpoint(monkeypatch):
    c, svc = _client(monkeypatch)
    sid = c.post("/form/sessions", json={"form_id": "samjhao", "lang": "en"}).json()["session_id"]
    r = c.post(f"/form/sessions/{sid}/suggest", json={"values": {"paper": "Traffic challan", "amount": "1000", "x": "y"}})
    j = r.json()
    assert r.status_code == 200 and j["suggested"] == ["paper", "amount"] and j["phase"] == "confirm"
    assert "Traffic challan" in j["text"] and j["field_id"] == "paper"
    # the page is told to keep this out of every cache, like every other answer
    assert r.headers["cache-control"] == "no-store"


def test_suggest_endpoint_limits(monkeypatch):
    c, _ = _client(monkeypatch)
    sid = c.post("/form/sessions", json={"form_id": "samjhao", "lang": "en"}).json()["session_id"]
    assert c.post("/form/sessions/nope/suggest", json={"values": {}}).status_code == 404
    assert c.post(f"/form/sessions/{sid}/suggest", json={"values": {"paper": "x" * 101}}).status_code == 422
    assert c.post(f"/form/sessions/{sid}/suggest", json={"values": {str(i): "1" for i in range(9)}}).status_code == 422
    assert c.post(f"/form/sessions/{sid}/suggest", json={"nope": 1}).status_code == 422


# ---- the engine files --------------------------------------------------------------------------------------------
ENGINE = ["tesseract.min.js", "worker.min.js", "tesseract-core-simd-lstm.js", "tesseract-core-simd-lstm.wasm",
          "eng.traineddata.gz", "hin.traineddata.gz", "dwani-ocr.js", "NOTICE.txt", "LICENSE-Apache-2.0.txt"]


@pytest.mark.parametrize("name", ENGINE)
def test_engine_files_are_present_and_served(monkeypatch, name):
    assert (OCR / name).stat().st_size > 1000
    c, _ = _client(monkeypatch)
    r = c.get(f"/form/assets/ocr/{name}")
    assert r.status_code == 200 and len(r.content) == (OCR / name).stat().st_size


def test_media_types_let_a_browser_run_them(monkeypatch):
    c, _ = _client(monkeypatch)
    assert c.get("/form/assets/ocr/tesseract-core-simd-lstm.wasm").headers["content-type"] == "application/wasm"
    # raw bytes, NOT "gzip": the reader unzips the language data itself and a browser must not do it first
    r = c.get("/form/assets/ocr/hin.traineddata.gz")
    assert r.headers["content-type"].startswith("application/octet-stream") and "content-encoding" not in r.headers
    assert r.content[:2] == b"\x1f\x8b"
    assert c.get("/form/assets/ocr/worker.min.js").headers["content-type"].startswith("text/javascript")


@pytest.mark.parametrize("path", ["../app.html", "..%2Fapp.html", "%2e%2e/app.html", "nope.js", "a/b"])
def test_the_asset_route_cannot_leave_its_folders(monkeypatch, path):
    c, _ = _client(monkeypatch)
    assert c.get(f"/form/assets/ocr/{path}").status_code == 404
    assert c.get("/form/assets/secrets/x.js").status_code == 404


def test_csp_allows_webassembly_but_not_eval(monkeypatch):
    c, _ = _client(monkeypatch)
    csp = c.get("/form/").headers["content-security-policy"]
    assert "'wasm-unsafe-eval'" in csp and "'unsafe-eval'" not in csp
    assert "connect-src 'self'" in csp                       # the page still cannot talk to anyone else
    assert "camera=()" in c.get("/form/").headers["permissions-policy"]    # the picture comes from the phone's own camera app


def test_the_page_never_sends_the_picture_anywhere():
    html = (Path(__file__).resolve().parents[1] / "dwaniforms" / "static" / "app.html").read_text(encoding="utf-8")
    # the only thing posted after a photo is the short list of values
    assert '"/suggest"' in html and "FormData" not in html and "readAsDataURL" not in html
    assert 'type="file"' in html and "capture=" not in html      # the person may pick the camera or a saved picture


def test_the_service_worker_cache_was_bumped():
    sw = (Path(__file__).resolve().parents[1] / "dwaniforms" / "static" / "sw.js").read_text(encoding="utf-8")
    assert 'CACHE = "dwaniforms-v1"' not in sw
