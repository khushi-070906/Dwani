"""Document photos for forms: an Aadhaar card, a ration card or a khasra copy photographed on the phone and suggested to the form.

What must not break:
  * only a few short values reach the server (as a suggestion); the picture never does;
  * an Aadhaar number with a wrong check digit, or a date that is not a date, is dropped by the SAME checks as a typed answer;
  * every value is read back for a yes; a "no" asks the question normally; what the person already said is never overwritten;
  * an Aadhaar number is spoken in full for the yes, but shown masked, in the reply and on the form sheet;
  * the reader script is served from this server, with a type a browser runs.
"""
import pytest

from dwaniforms import hardening
from dwaniforms.api import create_router
from dwaniforms.service import FormService
from dwaniforms.validators import verhoeff_append

TestClient = pytest.importorskip("fastapi.testclient").TestClient
from fastapi import FastAPI  # noqa: E402

AADHAAR = verhoeff_append("23456789012")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("DWANIFORMS_RATE_LIMIT", "0")
    svc = FormService(None, None)
    app = FastAPI(); app.include_router(create_router(svc)); hardening.install(app, svc, version="test")
    return TestClient(app)


def start(c, form, lang="en"):
    r = c.post("/form/sessions", json={"form_id": form, "lang": lang}).json()
    return r["session_id"], r


def suggest(c, sid, values):
    return c.post(f"/form/sessions/{sid}/suggest", json={"values": values})


def say(c, sid, text):
    return c.post(f"/form/sessions/{sid}/text", json={"text": text}).json()


def test_a_card_fills_several_fields_and_each_is_asked_to_be_confirmed(client):
    sid, _ = start(client, "ayushman_card")
    r = suggest(client, sid, {"full_name": "Asha Verma", "dob": "1990-03-14", "gender": "Female", "aadhaar": AADHAAR}).json()
    assert sorted(r["suggested"]) == ["aadhaar", "dob", "full_name", "gender"]
    assert "Asha Verma" in r["text"] and "correct" in r["text"].lower()
    r = say(client, sid, "yes")                                   # name accepted
    assert "father" in r["text"].lower() and "correct" not in r["text"].lower()   # not on the card: asked as usual
    say(client, sid, "Ramesh Verma")
    r = say(client, sid, "yes")
    assert "14" in r["text"] and "1990" in r["text"] and "correct" in r["text"].lower()   # the date is read back


def test_a_no_asks_the_question_normally_and_the_wrong_guess_is_gone(client):
    sid, _ = start(client, "ayushman_card")
    suggest(client, sid, {"full_name": "Asha Verma"})
    r = say(client, sid, "no")
    assert "name" in r["text"].lower() and "Asha Verma" not in r["text"]
    r = say(client, sid, "Meena Devi")
    assert not any(a["value"] == "Asha Verma" for a in r["fields"])


def test_the_aadhaar_number_is_spoken_in_full_but_shown_masked(client):
    sid, _ = start(client, "ayushman_card")
    suggest(client, sid, {"aadhaar": AADHAAR})
    r = None
    for a in ["Asha Verma", "Ramesh Verma", "14/03/1990", "Female", "9876543210"]:
        r = say(client, sid, a)
        while "correct" in r["text"].lower() and "aadhaar" not in r["text"].lower():
            r = say(client, sid, "yes")
        if "aadhaar" in r["text"].lower():
            break
    assert "aadhaar" in r["text"].lower() and "correct" in r["text"].lower(), r["text"]
    assert AADHAAR[-4:] in r["text"] and AADHAAR not in r["text"] and AADHAAR[:8] not in r["text"]
    assert AADHAAR in r["speak"].replace(" ", "")                 # the citizen confirms by ear
    assert r["pending"].endswith(AADHAAR[-4:]) and AADHAAR not in r["pending"]
    assert AADHAAR not in str(r["fields"])


def test_values_that_do_not_fit_are_dropped(client):
    sid, _ = start(client, "ayushman_card")
    bad_check_digit = AADHAAR[:-1] + str((int(AADHAAR[-1]) + 1) % 10)
    r = suggest(client, sid, {"aadhaar": bad_check_digit, "dob": "1990-02-31", "mobile": "12345", "pincode": "000000",
                              "gender": "Robot", "full_name": "Asha Verma"}).json()
    assert r["suggested"] == ["full_name"]


def test_what_the_person_already_said_is_not_overwritten(client):
    sid, _ = start(client, "ayushman_card")
    say(client, sid, "Meena Devi"); say(client, sid, "yes")
    r = suggest(client, sid, {"full_name": "Asha Verma"}).json()
    assert r["suggested"] == []


def test_ration_card_number_goes_to_the_form_and_to_the_look_up(client):
    sid, _ = start(client, "ayushman_card")
    assert suggest(client, sid, {"ration_card": "0912345678901", "card_no": "0912345678901"}).json()["suggested"] == ["ration_card"]
    sid, _ = start(client, "ration_lookup")
    r = suggest(client, sid, {"ration_card": "0912345678901", "card_no": "0912345678901"}).json()
    assert r["suggested"] == ["card_no"]


def test_khasra_number_goes_to_the_forms_that_ask_for_it(client):
    sid, _ = start(client, "pm_kisan")
    assert suggest(client, sid, {"khasra": "145/2", "ration_card": "0912345678901"}).json()["suggested"] == ["khasra"]
    sid, _ = start(client, "pm_kisan")
    assert suggest(client, sid, {"khasra": "not a khasra"}).json()["suggested"] == []


def test_only_eight_values_at_a_time_and_none_too_long(client):
    sid, _ = start(client, "ayushman_card")
    assert suggest(client, sid, {f"f{i}": "x" for i in range(9)}).status_code == 422
    assert suggest(client, sid, {"full_name": "x" * 101}).status_code == 422


def test_the_reader_script_is_served_with_a_type_a_browser_runs(client):
    r = client.get("/form/assets/ocr/dwani-docs.js")
    assert r.status_code == 200 and "javascript" in r.headers["content-type"]
    assert "DwaniDocs" in r.text and "verhoeff" in r.text


@pytest.mark.parametrize("lang", ["en", "hi", "bn", "gu", "kn", "ml", "mr", "pa", "ta", "te"])
def test_the_page_note_exists_in_every_language(lang):
    from dwaniforms import langpacks
    if lang in ("en", "hi"):
        html = open("dwaniforms/static/app.html", encoding="utf-8").read()
        assert "doc_note" in html
    else:
        assert langpacks.packs()[lang]["ui"]["doc_note"]
