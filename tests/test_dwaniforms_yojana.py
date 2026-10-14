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
