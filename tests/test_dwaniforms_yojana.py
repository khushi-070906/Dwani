"""DwaniYojana: 'which schemes might I get?' as a stateless page. Answers in, schemes out, nothing stored, nothing about the
person on the share card."""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dwaniforms import yojana  # noqa: E402
from dwaniforms.schema import load_all  # noqa: E402

PAGE = (ROOT / "dwaniforms" / "static" / "yojana.html").read_text(encoding="utf-8")
T = load_all()["scheme_advisor"]
FARMER = {"age": 62, "gender": "Female", "area": "Rural", "occupation": "Farmer", "land_acres": 2, "annual_income": 120000,
          "income_tax_payer": "No", "govt_employee": "No", "bank_account": "Yes", "ration_card": "PHH / BPL", "house": "Kutcha house"}


def names(r, key):
    return [i["id"] for i in r[key]]


def test_a_landowning_farmer_in_her_sixties_gets_the_schemes_that_fit():
    r = yojana.advise(T, FARMER, "en")
    assert {"pm_kisan", "ignoaps"} <= set(names(r, "likely")), names(r, "likely")
    assert r["reviewed"] and r["not_matched"] >= 1


def test_a_man_is_never_asked_or_judged_on_the_widow_and_gas_questions():
    facts = yojana.clean_facts(T, {**FARMER, "gender": "Male", "widow": "Yes", "lpg": "No"})
    assert facts["widow"] == facts["lpg"] == "N/A"           # same as the spoken interview: not applicable


@pytest.mark.parametrize("bad", [{"age": -1}, {"age": 500}, {"age": "abc"}, {"age": None}, {"zzz": 1}, {"gender": "robot"},
                                 {"bank_account": "maybe"}, {"annual_income": float("nan")}])
def test_answers_the_advisor_would_not_accept_are_refused(bad):
    with pytest.raises(yojana.BadFacts):
        yojana.clean_facts(T, bad)


def test_only_the_questions_a_scheme_actually_checks_are_asked():
    ids = [q["id"] for q in yojana.questions(T, "en")]
    assert "category" not in ids and ids[:4] == ["age", "gender", "area", "occupation"]
    assert [q for q in yojana.questions(T, "hi") if q["id"] == "age"][0]["prompt"].startswith("आपकी उम्र")


def test_the_page_has_a_short_label_in_both_languages_for_every_question_and_option():
    head = re.search(r"var HEAD = \{(.*?)\n  \};", PAGE, re.S).group(1)
    opt = re.search(r"var OPT = \{(.*?)\n  \};", PAGE, re.S).group(1)
    for q in yojana.questions(T, "en"):
        assert re.search(rf"\b{q['id']}: \[\"[^\"]+\", \"[^\"]+\"\]", head), f"no heading for {q['id']}"
        for v in q["options"]:
            assert re.search(rf'"{re.escape(v)}": \["[^"]+", "[^"]+"\]', opt), f"no label for option {v!r}"


def test_the_share_card_is_drawn_from_scheme_names_only():
    card = PAGE[PAGE.index("function drawCard"):PAGE.index("function fit(")]
    assert "facts" not in card and "last.likely" in card, "the card must not draw anything the person answered"
    assert "localStorage" not in PAGE and "sessionStorage" not in PAGE and "document.cookie" not in PAGE


def test_the_page_draws_its_icons_and_asks_for_no_outside_files():
    assert not re.search(r"[✓✗✎⚠⤓←→]", PAGE)
    assert not re.search(r"(src|href)=\"https?://", PAGE) and "googleapis" not in PAGE


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from dwaniforms.standalone import build_app
    return TestClient(build_app("small", None, "sentencepiece.bpe.model", True, True, False))


def test_the_page_and_its_two_endpoints_work(client):
    assert client.get("/form/yojana").status_code == 200
    q = client.get("/form/yojana/questions?lang=hi").json()
    assert q[0]["id"] == "age" and client.get("/form/yojana/questions?lang=ta").status_code == 404
    r = client.post("/form/yojana/advise", json={"lang": "hi", "facts": FARMER})
    assert r.status_code == 200 and "pm_kisan" in [i["id"] for i in r.json()["likely"]]
    assert client.post("/form/yojana/advise", json={"facts": {"age": -3}}).status_code == 422
    assert client.post("/form/yojana/advise", json={"facts": {"nope": 1}}).status_code == 422


def test_the_endpoints_create_no_session_and_write_nothing():
    api = (ROOT / "dwaniforms" / "api.py").read_text(encoding="utf-8")
    part = api[api.index('@router.get("/yojana"'):api.index('@router.post("/sessions")')]
    for forbidden in ("service.create", "sessions", "open(", "write", "logging", "logger"):
        assert forbidden not in part, forbidden


@pytest.mark.parametrize("qid,said,lang,expected", [
    ("age", "बासठ साल", "hi", 62), ("age", "I am sixty two", "en", 62), ("age", "62.5", "en", None),
    ("gender", "मैं महिला हूँ", "hi", "Female"), ("occupation", "मैं खेती करता हूँ", "hi", "Farmer"),
    ("occupation", "खेत मज़दूर", "hi", "Farm labourer"), ("occupation", "farm labour", "en", "Farm labourer"),
    ("land_acres", "ढाई एकड़", "hi", 2.5), ("land_acres", "2.5 acres", "en", 2.5), ("land_acres", "दो एकड़", "hi", 2),
    ("annual_income", "डेढ़ लाख", "hi", 200000), ("annual_income", "8 lakh", "en", 1000000),
    ("bank_account", "हाँ जी", "hi", "Yes"), ("bank_account", "nope", "en", "No"),
    ("bank_account", "पता नहीं", "hi", None), ("bank_account", "I don't know", "en", None), ("ration_card", "अंत्योदय", "hi", "AAY"),
    ("house", "कच्चा", "hi", "Kutcha house"), ("age", "blah", "en", None), ("age", "", "en", None),
])
def test_spoken_words_mean_what_the_spoken_forms_would_take_them_to_mean(qid, said, lang, expected):
    assert yojana.understand(T, qid, said, lang) == expected


def test_a_question_that_is_not_asked_here_cannot_be_answered_by_voice():
    for bad in ("category", "nope"):
        with pytest.raises(yojana.BadFacts):
            yojana.understand(T, bad, "general", "en")


def test_the_income_bands_on_the_page_are_the_ones_the_server_rounds_to():
    tops = [int(x) for x in re.findall(r"\[(\d{6,7}), \"", re.search(r"var INCOME = \[(.*?)\];", PAGE, re.S).group(1))]
    assert tuple(tops) == yojana.INCOME_BANDS


def test_the_voice_endpoint_answers_and_refuses(client):
    ok = client.post("/form/yojana/understand", json={"question": "age", "text": "बासठ", "lang": "hi"})
    assert ok.status_code == 200 and ok.json() == {"value": 62}
    assert client.post("/form/yojana/understand", json={"question": "age", "text": "???", "lang": "hi"}).json() == {"value": None}
    assert client.post("/form/yojana/understand", json={"question": "category", "text": "x", "lang": "hi"}).status_code == 422
    assert client.post("/form/yojana/understand", json={"question": "age", "text": "x" * 400, "lang": "hi"}).status_code == 422


def test_the_page_tells_people_their_browser_may_send_speech_to_google():
    assert "Google" in PAGE[PAGE.index("voice_note: \"बोलने"):][:200] and "Google" in PAGE[PAGE.index("voice_note: \"When you speak"):][:200]
    assert "webkitSpeechRecognition" in PAGE and "$(\"voice\").hidden = false" in PAGE   # shown only where the browser can listen


def test_a_shared_link_has_a_title_a_description_and_a_real_preview_picture(client):
    meta = dict(re.findall(r'<meta (?:property|name)="((?:og|twitter):[a-z:]+)" content="([^"]*)"', PAGE))
    for key in ("og:title", "og:description", "og:image", "og:url", "og:image:alt"):
        assert meta.get(key), key
    assert meta["twitter:card"] == "summary_large_image"
    # chat apps fetch the picture from outside, so the address must be absolute https and one the app really serves
    assert meta["og:image"].startswith("https://") and "/form/assets/img/" in meta["og:image"]
    name = meta["og:image"].rsplit("/", 1)[1]
    r = client.get("/form/assets/img/" + name)
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    data = r.content
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    assert (width, height) == (int(meta["og:image:width"]), int(meta["og:image:height"])) == (1200, 630)
    assert len(data) < 300_000                       # a slow phone should still get the preview
    assert meta["og:url"].endswith("/form/yojana")

