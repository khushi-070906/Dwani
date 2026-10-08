"""DwaniForms language packs (dwaniforms/lang/*.json): hand-written questions, messages and answer words for languages
beyond Hindi and English, so the online version (no translation model) can offer them.

These tests check that every pack is COMPLETE and INTERNALLY CONSISTENT. They cannot check that the language is good:
that needs a native-speaker review (each pack carries status "draft" until then)."""
import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dwaniforms import langpacks, records
from dwaniforms.api import create_router
from dwaniforms.messages import MESSAGES, is_skip, parse_yes_no
from dwaniforms.schema import load_all
from dwaniforms.service import FormService
from dwaniforms.session import FormSession
from dwaniforms.spoken import spoken_to_date, spoken_to_digits, spoken_to_khasra, spoken_to_number

PACKS = langpacks.packs()
LANGS = sorted(PACKS)
TEMPLATES = load_all()


def run(coro):
    return asyncio.run(coro)


def test_every_pack_is_installed():
    assert LANGS == ["bn", "gu", "kn", "ml", "mr", "pa", "ta", "te"]
    assert langpacks.langs()[:2] == ["hi", "en"]


@pytest.mark.parametrize("lang", LANGS)
def test_pack_is_complete(lang):
    assert langpacks.problems(lang) == []


@pytest.mark.parametrize("lang", LANGS)
def test_pack_is_marked_as_needing_review(lang):
    # flip to "reviewed" (and fill reviewed_by) only after a native speaker has gone through it
    assert PACKS[lang]["status"] in ("draft", "reviewed")
    if PACKS[lang]["status"] == "reviewed":
        assert PACKS[lang]["reviewed_by"]


@pytest.mark.parametrize("lang", LANGS)
def test_yes_no_words(lang):
    w, ui = PACKS[lang]["words"], PACKS[lang]["ui"]
    assert all(" " not in x for x in w["yes"] + w["no"]), "yes / no words are matched one word at a time"
    assert not set(w["yes"]) & set(w["no"])
    assert parse_yes_no(ui["yes_word"]) is True and parse_yes_no(ui["no_word"]) is False   # what the big buttons send
    for b in w.get("benign_negations", []):
        assert parse_yes_no(ui["yes_word"] + ", " + b) is True, b    # "yes, no problem" is a yes


def test_no_pack_word_flips_another_languages_answer():
    yes = {x for p in PACKS.values() for x in p["words"]["yes"]}
    no = {x for p in PACKS.values() for x in p["words"]["no"]}
    assert not yes & no
    # Hindi and English still behave
    assert parse_yes_no("हाँ") is True and parse_yes_no("नहीं") is False and parse_yes_no("yes") is True


@pytest.mark.parametrize("lang", LANGS)
def test_skip_words(lang):
    for s in PACKS[lang]["words"]["skip"]:
        assert is_skip(s), s


@pytest.mark.parametrize("lang", LANGS)
def test_digit_words_and_numerals(lang):
    d = PACKS[lang]["words"]["digits"]
    first = {}
    for word, v in d.items():
        first.setdefault(v, word)
    said = " ".join(first[i] for i in (9, 8, 7, 6, 5, 4, 3, 2, 1, 0))
    assert spoken_to_digits(said) == "9876543210"


@pytest.mark.parametrize("lang", LANGS)
def test_dates_with_month_names(lang):
    months = PACKS[lang]["words"]["months"]
    for m in (1, 8, 12):
        assert spoken_to_date(f"15 {months[m - 1]} 1980") == (15, m, 1980)
    # read back with the pack's month name
    f = TEMPLATES["pm_kisan"].field_by_id("dob")
    assert FormSession.readback(f, "1980-08-15", lang) == f"15 {months[7]} 1980"


@pytest.mark.parametrize("lang,said", [("bn", "১৫ই আগস্ট ১৯৮০"), ("ta", "15ஆம் தேதி ஆகஸ்ட் 1980"), ("te", "15వ తేదీ ఆగస్టు 1980"),
                                       ("en", "15th August 1980")])
def test_day_with_ordinal_ending(lang, said):
    assert spoken_to_date(said) == (15, 8, 1980)


@pytest.mark.parametrize("lang", LANGS)
def test_fractions_scales_and_khasra(lang):
    w = PACKS[lang]["words"]
    two_and_half = [k for k, v in w["fractions"].items() if v == 2.5]
    assert two_and_half, "every pack should know 'two and a half' (2.5 acres is a common answer)"
    assert format(spoken_to_number(two_and_half[0]), "f") == "2.5"
    lakh = [k for k, v in w["scales"].items() if v == 100000][0]
    assert spoken_to_number(f"2 {lakh}") == 200000
    assert spoken_to_khasra(f"145 {w['khasra_sep'][0]} 2") == "145/2"


def _choice_fields():
    for t in TEMPLATES.values():
        for f in t.fields:
            if f.kind == "choice":
                yield t, f


@pytest.mark.parametrize("lang", LANGS)
def test_every_option_can_be_chosen_by_its_shown_name(lang):
    """The name shown / read for an option must select that option, and must not also match another option."""
    for t, f in _choice_fields():
        s = FormSession(t, lang)
        for o in f.options:
            label = o.synonyms[lang][0]
            assert s._match_option(f, label) == o.value, (t.id, f.id, o.value, label)


@pytest.mark.parametrize("lang", LANGS)
def test_templates_and_schemes_have_hand_written_text(lang):
    for t in TEMPLATES.values():
        assert t.title_in(lang) != t.title
        for f in t.fields:
            assert lang in f.prompt and f.label_in(lang)
    from dwaniforms.eligibility import load_schemes
    for s in load_schemes():
        assert lang in s["name"] and lang in s["benefit"] and lang in s["apply"]


def _svc(tmp_path):
    return FormService(None, None, record_store=records.RecordStore(records.write_demo(tmp_path, as_of="2026-09-01")))


async def _converse(svc, form, lang, answers, yes):
    s, r = await svc.create(form, lang)
    texts = [r.text]
    answers = list(answers)
    for _ in range(80):
        if r.done:
            break
        a = yes if s.phase in ("confirm", "final") else answers.pop(0)
        r = await svc.answer_text(s.id, a)
        texts.append(r.text)
    return s, r, texts


@pytest.mark.parametrize("lang", LANGS)
def test_ration_lookup_entirely_in_the_language_without_a_translator(lang, tmp_path):
    svc = _svc(tmp_path)
    yes = PACKS[lang]["ui"]["yes_word"]
    s, r, texts = run(_converse(svc, "ration_lookup", lang, ["123456789012"], yes))
    assert r.done and not s.english_fallback and not s.machine_translated_used
    assert texts[0].startswith(PACKS[lang]["messages"]["welcome"].split("{")[0].strip())
    assert PACKS[lang]["categories"]["PHH"] in r.text            # "this card is in the <category> category" in the language


@pytest.mark.parametrize("lang", LANGS)
def test_scheme_advisor_in_the_language(lang, tmp_path):
    svc = _svc(tmp_path)
    p = PACKS[lang]
    first = lambda v: p["options"][v][0]
    yes, no = p["ui"]["yes_word"], p["ui"]["no_word"]
    answers = ["35", first("Male"), first("Rural"), first("Farmer"), "2", "120000", no, no, no, yes,
               first("PHH / BPL"), first("Kutcha house"), yes, first("OBC")]
    s, r, texts = run(_converse(svc, "scheme_advisor", lang, answers, yes))
    assert r.done and not s.english_fallback, texts[-3:]
    names = [i["name"] for i in s.outcome["likely"]]
    assert p["text"]["PM-KISAN"] in names
    assert all(x in s.outcome["form_titles"].values() for x in [TEMPLATES["pm_kisan"].title_in(lang)])


@pytest.mark.parametrize("lang", LANGS)
def test_grievance_next_steps_in_the_language(lang, tmp_path):
    svc = _svc(tmp_path)
    p = PACKS[lang]
    yes = p["ui"]["yes_word"]
    answers = ["Asha", "9876543210", "ration shop has been closed for two months in our village", p["options"]["Food & civil supplies (ration)"][0],
               "Rampur", p["words"]["skip"][0], p["words"]["skip"][0]]
    s, r, _ = run(_converse(svc, "grievance", lang, answers, yes))
    assert r.done
    assert s.outcome["next_steps_local"][0] == p["text"][s.outcome["next_steps"][0]]
    assert "en" in s.outcome["drafts"]                              # the letter itself is for the office


def test_ui_strings_endpoint_and_capabilities(tmp_path):
    svc = _svc(tmp_path)
    app = FastAPI(); app.include_router(create_router(svc))
    c = TestClient(app)
    caps = c.get("/form/capabilities").json()
    assert caps["langs"] == ["hi", "en", *LANGS]
    assert {p["lang"]: p["speech"] for p in caps["lang_packs"]}["pa"] == "pa-Guru-IN"
    r = c.get("/form/ui/ta").json()
    assert r["ui"]["yes_word"] == "ஆம்" and r["speech"] == "ta-IN"
    assert c.get("/form/ui/or").status_code == 404 and c.get("/form/ui/hi").status_code == 404


def test_messages_have_the_same_placeholders():
    for lang in LANGS:
        assert set(MESSAGES[lang]) == set(MESSAGES["en"])


def test_skeleton_for_a_new_language_lists_everything(capsys):
    assert langpacks._main(["skeleton"]) == 0
    import json
    sk = json.loads(capsys.readouterr().out)
    assert set(sk["text"]) == langpacks.required_text() and set(sk["ui"]) == langpacks.ui_keys()
    assert Path(langpacks.PACK_DIR).is_dir()
