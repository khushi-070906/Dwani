"""RTI first appeal (Section 19(1)): the draft, and the 30-day window worked out from the dates the person gives.

The Act: the PIO has 30 days to reply (s.7(1)); no reply is a deemed refusal (s.7(2)); the appeal goes to the officer
senior to the PIO within 30 days of that period ending or of the reply (s.19(1)), and a late one may still be
admitted for sufficient cause (proviso). Get these dates wrong and a person files too early or gives up too soon.
"""
import asyncio
import datetime as dt

import pytest

from dwaniforms import langpacks
from dwaniforms.outcomes import appeal_timing, rti_appeal_draft
from dwaniforms.schema import load_all
from dwaniforms.service import FormService

TODAY = dt.date(2026, 10, 13)


# ---- the window -------------------------------------------------------------------------------------------------
def test_no_reply_yet_and_the_office_still_has_time():
    t = appeal_timing("2026-10-01", "", TODAY)
    assert t == {"early": "2026-10-31", "last": "2026-11-30"}


def test_no_reply_and_within_the_window():
    assert appeal_timing("2026-09-01", "", TODAY) == {"last": "2026-10-31"}


def test_no_reply_and_the_window_has_closed():
    assert appeal_timing("2026-08-01", "", TODAY) == {"last": "2026-09-30", "late": True}


def test_after_a_reply_the_30_days_run_from_the_reply():
    assert appeal_timing("2026-08-01", "2026-10-10", TODAY) == {"last": "2026-11-09"}
    assert appeal_timing("2026-06-01", "2026-07-01", TODAY) == {"last": "2026-07-31", "late": True}


def test_a_bad_date_gives_no_advice_rather_than_wrong_advice():
    assert appeal_timing("someday", "", TODAY) == {}


# ---- a whole conversation ---------------------------------------------------------------------------------------
def _fill(lang, answers):
    async def go():
        svc = FormService()
        s, r = await svc.create("rti_first_appeal", lang)
        it = iter(answers)
        yes = {"en": "yes", "hi": "हाँ"}.get(lang) or langpacks.words(lang, "yes")[0]
        for _ in range(60):
            if r.done:
                break
            r = await svc.answer_text(s.id, next(it) if r.phase == "ask" else yes)
        return s, r
    return asyncio.run(go())


def test_english_no_reply_draft_cites_deemed_refusal_and_free_information():
    s, r = _fill("en", ["Ramesh Kumar", "Village Rampur, Sitapur", "261001", "skip", "ration", "1 August 2026",
                        "list of ration given on my card in 2025", "no reply"])
    assert r.done and r.outcome["type"] == "draft"
    letter = r.outcome["drafts"]["en"]
    assert "First Appellate Authority" in letter and "Section 19(1)" in letter
    assert "Section 7(2)" in letter and "Section 7(6)" in letter          # deemed refusal; information free of charge
    assert "Food & civil supplies (ration)" in letter and "01 August 2026" in letter
    assert len(r.outcome["next_steps"]) == 3


def test_hindi_reply_received_draft_quotes_the_grounds_and_attaches_the_reply():
    s, r = _fill("hi", ["रमेश कुमार", "गाँव रामपुर", "261001", "छोड़ो", "राशन", "1 अक्टूबर 2026",
                        "मेरे कार्ड पर 2025 में मिले राशन की सूची", "जवाब आया", "10 अक्टूबर 2026",
                        "आधी जानकारी दी, बाकी मना कर दिया"])
    assert s.answers["reply_status"].value == "Reply received, not satisfied"
    hi = r.outcome["drafts"]["hi"]
    assert "प्रथम अपीलीय अधिकारी" in hi and "धारा 19(1)" in hi
    assert "आधी जानकारी दी" in hi and "जवाब की प्रति" in hi
    assert "Section 7(2)" not in r.outcome["drafts"]["en"]                # a reply came: no deemed refusal claimed


def test_a_late_appeal_asks_for_the_delay_to_be_condoned_and_says_so():
    s, r = _fill("en", ["Asha", "Lucknow", "226001", "skip", "pension", "1 May 2026", "my widow pension papers",
                        "no reply"])
    assert r.outcome["timing"].get("late")
    assert "delay be condoned" in r.outcome["drafts"]["en"]
    assert "appeal is late" in r.outcome["say"]


def test_reply_questions_are_only_asked_when_a_reply_came():
    t = load_all()["rti_first_appeal"]
    for fid in ("reply_date", "grounds"):
        assert t.field_by_id(fid).when == {"field": "reply_status", "in": ["Reply received, not satisfied"]}


def test_department_choices_are_the_same_as_the_rti_application():
    """So that "the department you sent it to" accepts exactly what the RTI form offered (and packs translate once)."""
    ts = load_all()
    a = [o.value for o in ts["rti_application"].field_by_id("department").options]
    b = [o.value for o in ts["rti_first_appeal"].field_by_id("department").options]
    assert a == b


@pytest.mark.parametrize("lang", sorted(langpacks.packs()))
def test_every_language_answers_the_reply_question_with_its_own_words(lang):
    """The question tells people what to say ("no reply, or reply received"): those words must pick the option."""
    opts = langpacks.packs()[lang]["options"]
    s, r = _fill(lang, ["Name", "Address", "110001", langpacks.words(lang, "skip")[0],
                        opts["Food & civil supplies (ration)"][0], "1/8/2026", "ration list for my card in 2025",
                        opts["No reply received"][0]])
    assert s.answers["reply_status"].value == "No reply received"
    assert r.done and r.outcome["timing"].get("late")


def test_the_draft_builder_needs_no_service():
    """outcomes are plain functions: easy to test, easy to reuse on a kiosk without the web layer."""
    class A:
        def __init__(self, v): self.value, self.native = v, None
    class S:
        lang = "en"
        answers = {k: A(v) for k, v in {"full_name": "X", "address": "Y", "pincode": "110001", "department": "Health",
                                         "rti_date": "2026-09-01", "information": "OPD register", "reply_status":
                                         "No reply received"}.items()}
    out = rti_appeal_draft(S(), today=TODAY)
    assert out["timing"] == {"last": "2026-10-31"} and out["say_more"] == []
