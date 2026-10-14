"""Samjhao: "what does this paper mean, what do I do, by when" -- for a bill, a notice or a summons.

What must not break:
  * saying the name of a paper picks THAT paper, in every language (two kinds of bill share the word "bill");
  * the date is counted correctly (days left / today / already passed) and said aloud;
  * every paper says how to spot the fraud dressed up as it, and that warning is spoken, not only shown;
  * a reviewer can fix a sentence in papers/*.json and every language pack is held to it.
"""
import asyncio
import datetime as dt
import json
from pathlib import Path

import pytest

from dwaniforms import clock, explain, langpacks
from dwaniforms.schema import load_all
from dwaniforms.service import FormService

PAPERS = sorted((Path(explain.__file__).parent / "papers").glob("*.json"))
TEMPLATE = load_all()["samjhao"]
OPTIONS = [o.value for o in TEMPLATE.field_by_id("paper").options]
LANGS = ["en", "hi", *sorted(langpacks.packs())]


def _yes(lang):
    return {"en": "yes", "hi": "हाँ"}.get(lang) or langpacks.words(lang, "yes")[0]


def _skip(lang):
    return {"en": "skip", "hi": "छोड़ो"}.get(lang) or langpacks.words(lang, "skip")[0]


def _run(lang, answers):
    async def go():
        svc = FormService()
        s, r = await svc.create("samjhao", lang)
        it = iter(answers)
        for _ in range(30):
            if r.done:
                break
            r = await svc.answer_text(s.id, next(it) if r.phase == "ask" else _yes(lang))
        return s, r
    return asyncio.run(go())


# ---- the data a reviewer edits ----------------------------------------------------------------------------------
@pytest.mark.parametrize("path", PAPERS, ids=lambda p: p.stem)
def test_each_paper_file_is_complete(path):
    p = json.loads(path.read_text(encoding="utf-8"))
    assert p["id"] == path.stem and p["option"] in OPTIONS
    assert p["date_role"] in (None, "pay", "appear", "reply") and isinstance(p["amount"], bool)
    assert p["what"]["en"] and p["what"]["hi"]
    assert p["do"] and p["beware"], "every paper says what to do and what fraud to watch for"
    for k in ("do", "if_wrong", "beware", "help"):
        for line in p[k]:
            assert line["en"] and line["hi"], f"{path.stem}.{k}: Hindi is hand-written alongside English"
    assert p["last_reviewed"]


def test_every_option_has_a_paper_and_the_questions_follow_it():
    by_option = {p["option"]: p for p in explain.papers().values()}
    assert set(by_option) == set(OPTIONS)
    date_when = TEMPLATE.field_by_id("due_date").when["in"]
    amount_when = TEMPLATE.field_by_id("amount").when["in"]
    assert sorted(date_when) == sorted(o for o, p in by_option.items() if p["date_role"])
    assert sorted(amount_when) == sorted(o for o, p in by_option.items() if p["amount"])


def test_a_date_on_a_paper_may_lie_ahead():
    assert TEMPLATE.field_by_id("due_date").params.get("future") is True


def test_the_language_packs_must_translate_the_papers():
    need = langpacks.required_text()
    for p in explain.papers().values():
        assert p["what"]["en"] in need
        assert all(x["en"] in need for x in p["beware"])


# ---- saying the name picks that paper ---------------------------------------------------------------------------
@pytest.mark.parametrize("lang", LANGS)
def test_each_paper_name_picks_that_paper(lang):
    f = TEMPLATE.field_by_id("paper")
    for o in f.options:
        said = o.synonyms.get(lang, o.synonyms.get("en"))[0]
        s, r = _run(lang, [said, _skip(lang), _skip(lang)])
        assert s.answers["paper"].value == o.value, f"{lang}: saying {said!r} chose {s.answers['paper'].value!r}"


# ---- the answer -------------------------------------------------------------------------------------------------
def test_hindi_bill_with_a_date_ahead_counts_the_days_and_warns_of_fraud():
    due = (clock.today() + dt.timedelta(days=12)).strftime("%d/%m/%Y")
    s, r = _run("hi", ["बिजली का बिल", due, "1850"])
    o = r.outcome
    assert o["type"] == "explain" and o["paper"] == "electricity_bill"
    assert o["days"] == 12 and o["amount"] == "1850"
    assert "12 दिन बाद" in o["say"]
    assert "धोखा" in o["say"], "the fraud warning is read out, not only printed"
    assert "क़ानूनी सलाह नहीं" in o["say"]


def test_a_date_that_has_passed_says_go_today():
    s, r = _run("en", ["traffic challan", (clock.today() - dt.timedelta(days=5)).strftime("%d/%m/%Y"), "skip"])
    assert r.outcome["days"] == -5 and "has already passed" in r.outcome["say"]


def test_the_kyc_message_asks_no_date_or_amount_and_says_never_share_the_otp():
    s, r = _run("en", ["kyc"])
    assert set(s.answers) == {"paper"}
    assert r.outcome["paper"] == "bank_kyc" and "date" not in r.outcome
    assert any("OTP" in x for x in r.outcome["beware"])
    assert "OTP" in r.outcome["say"]


def test_court_summons_names_free_legal_aid_and_digital_arrest():
    s, r = _run("en", ["court summons", _skip("en")])
    text = " ".join(r.outcome["do"] + r.outcome["beware"])
    assert "15100" in text and "digital arrest" in text


@pytest.mark.parametrize("lang", sorted(langpacks.packs()))
def test_the_explanation_comes_in_the_citizens_language(lang):
    opts = langpacks.packs()[lang]["options"]
    s, r = _run(lang, [opts["Bank loan or EMI notice"][0], _skip(lang), _skip(lang)])
    o = r.outcome
    assert o["paper"] == "bank_loan"
    assert o["what"] == langpacks.text(lang, explain.papers()["Bank loan or EMI notice"]["what"]["en"])
    assert "14448" in " ".join(o["if_wrong"]) and "cms.rbi.org.in" in " ".join(o["if_wrong"])


def test_something_else_still_gives_safe_advice():
    s, r = _run("en", ["something else"])
    assert r.outcome["paper"] == "other" and r.outcome["beware"]


def test_unknown_answers_fall_back_to_something_else():
    class S:
        lang = "en"
        answers = {}
    assert explain.explain_outcome(S())["paper"] == "other"
