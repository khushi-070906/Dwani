"""Scheme forms: pension (old age / widow / disability), student scholarship, Ayushman card (PM-JAY).

The scheme advisor sends people to these forms, so: they exist, they are linked from the schemes, every question can be asked
and answered in all ten languages without a translator, the disability question appears only for a disability pension, and
the company API checks them like any other form.
"""
import asyncio

import pytest

from dwaniforms import apikeys, b2b, langpacks, records
from dwaniforms.eligibility import load_schemes
from dwaniforms.schema import load_all
from dwaniforms.service import FormService
from dwaniforms.session import FormSession

TEMPLATES = load_all()
PACKS = langpacks.packs()
FORMS = ("pension_application", "scholarship_application", "ayushman_card")
AADHAAR = apikeys and None


def aadhaar():
    from dwaniforms.validators import verhoeff_append
    return verhoeff_append("23456789012")


def run(c):
    return asyncio.run(c)


def test_the_three_forms_exist():
    for f in FORMS:
        t = TEMPLATES[f]
        assert t.flow == "form" and t.title_in("hi") != t.title and t.fields


def test_schemes_point_to_them():
    forms = {s["id"]: s["form"] for s in load_schemes()}
    assert forms["ignoaps"] == forms["igndps"] == forms["ignwps"] == "pension_application"
    assert forms["pmjay"] == "ayushman_card"
    assert all(f in TEMPLATES for f in forms.values() if f)


@pytest.mark.parametrize("lang", sorted(PACKS))
def test_pack_is_complete_for_the_new_forms(lang):
    assert langpacks.problems(lang) == []


def test_disability_percent_only_for_a_disability_pension():
    t = TEMPLATES["pension_application"]
    s = FormSession(t, "en")
    f = t.field_by_id("disability_percent")
    ok, _ = s.operator_set("pension_type", "Old age pension")
    assert ok and not s.applicable(f)
    ok, _ = s.operator_set("pension_type", "Disability pension")
    assert ok and s.applicable(f)
    assert not s.operator_set("disability_percent", "0")[0] and not s.operator_set("disability_percent", "101")[0]
    assert s.operator_set("disability_percent", "40")[0]


def test_scholarship_checks_marks_and_age():
    s = FormSession(TEMPLATES["scholarship_application"], "en")
    assert s.operator_set("last_marks", "72.5")[0] and not s.operator_set("last_marks", "120")[0]
    assert not s.operator_set("dob", "01/01/2026")[0]            # a student is at least 5
    assert s.operator_set("dob", "01/01/2008")[0]


def test_ayushman_family_size():
    s = FormSession(TEMPLATES["ayushman_card"], "en")
    assert s.operator_set("family_members", "5")[0]
    assert not s.operator_set("family_members", "0")[0] and not s.operator_set("family_members", "51")[0]


@pytest.mark.parametrize("form,what", [("pension_application", {"Old age": "Old age pension", "widow": "Widow pension", "disability": "Disability pension"}),
                                       ("scholarship_application", {"class 11": "Class 11-12", "diploma": "Diploma / ITI", "graduation": "Degree (graduation)",
                                                                    "post graduation": "Post-graduation or higher", "school": "School (up to class 10)"})])
def test_choices_are_understood_in_english(form, what):
    t = TEMPLATES[form]
    s = FormSession(t, "en")
    f = next(x for x in t.fields if x.kind == "choice" and x.options and any(o.value in what.values() for o in x.options))
    for said, want in what.items():
        assert s._match_option(f, said) == want, said


@pytest.mark.parametrize("form,sel", [("pension_application", {"pension_type": ("Old age pension", "वृद्धावस्था")}),
                                      ("scholarship_application", {"course_level": ("Degree (graduation)", "ग्रेजुएशन")})])
def test_choices_are_understood_in_hindi(form, sel):
    t = TEMPLATES[form]
    s = FormSession(t, "hi")
    for fid, (value, said) in sel.items():
        assert s._match_option(t.field_by_id(fid), said) == value


def test_post_graduation_is_not_taken_for_graduation():
    t = TEMPLATES["scholarship_application"]
    for lang, said in (("en", "post graduation"), ("en", "masters"), ("hi", "पोस्ट ग्रेजुएशन")):
        s = FormSession(t, lang)
        assert s._match_option(t.field_by_id("course_level"), said) == "Post-graduation or higher", (lang, said)


@pytest.mark.parametrize("lang", sorted(PACKS))
def test_every_option_of_the_new_forms_is_choosable_in_every_language(lang):
    for form in FORMS:
        t = TEMPLATES[form]
        s = FormSession(t, lang)
        for f in t.fields:
            if f.kind == "choice":
                for o in f.options:
                    assert s._match_option(f, o.synonyms[lang][0]) == o.value, (form, f.id, o.value)


def _answers(form, lang):
    p = PACKS.get(lang)
    first = (lambda v: p["options"][v][0]) if p else (lambda v: v)
    yes = p["ui"]["yes_word"] if p else "yes"
    skip = p["words"]["skip"][0] if p else "skip"
    common = {"full_name": "Asha Verma", "father_name": "Ramesh Verma", "parent_name": "Ramesh Verma", "dob": "14/03/1990",
              "mobile": "9876543210", "aadhaar": aadhaar(), "ration_card": skip, "village": "Rampur", "tehsil": "Sadar",
              "district": "Lucknow", "state": "Uttar Pradesh", "pincode": "226001", "annual_income": "90000",
              "account_no": "123456789012", "ifsc": "SBIN0001234", "family_members": "5", "listed": yes, "institution": "Govt Inter College",
              "course": "class 9", "last_marks": "72.5", "disability": p["ui"]["no_word"] if p else "no", "disability_percent": "50"}
    choices = {"pension_type": "Old age pension", "gender": "Female", "category": "OBC", "course_level": "School (up to class 10)"}
    t = TEMPLATES[form]
    out = []
    for f in t.fields:
        if f.id == "disability_percent":
            continue
        out.append(first(choices[f.id]) if f.id in choices else common[f.id])
    if form == "scholarship_application":
        out[out.index("14/03/1990")] = "14/03/2008"
    return out, yes


async def _converse(form, lang, answers, yes):
    svc = FormService(None, None)
    s, r = await svc.create(form, lang)
    answers = list(answers)
    for _ in range(120):
        if r.done:
            break
        a = yes if s.phase in ("confirm", "final") else answers.pop(0)
        r = await svc.answer_text(s.id, a)
    return s, r


@pytest.mark.parametrize("form", FORMS)
@pytest.mark.parametrize("lang", ["en", "hi", *sorted(PACKS)])
def test_a_whole_conversation_in_every_language(form, lang):
    answers, yes = _answers(form, lang)
    s, r = run(_converse(form, lang, answers, yes))
    assert r.done, (form, lang, r.text)
    got = {a["id"]: a["value"] for a in s.form_values()} if s.form_values() and isinstance(s.form_values()[0], dict) and "id" in s.form_values()[0] else None
    vals = {k: v.value for k, v in s.answers.items()}
    assert vals["mobile"] == "9876543210" and vals["pincode"] == "226001"
    assert not getattr(s, "english_fallback", False) and not getattr(s, "machine_translated_used", False)


# ---- the company API -----------------------------------------------------------------------------------------------------
@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("DWANIFORMS_KEY_SECRET", "test-secret-0123456789-abcdefghij")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI(); app.mount("/api/v1", b2b.build_api(FormService(None, None)))
    return TestClient(app, raise_server_exceptions=False)


def test_api_checks_the_new_forms(client):
    raw, _ = apikeys.issue("Acme Ltd", ["forms"])
    h = {"Authorization": f"Bearer {raw}"}
    svc = {s["id"] for s in client.get("/api/v1/services", headers=h).json()["services"]}
    assert set(FORMS) <= svc
    f = {"pension_type": "Disability pension", "full_name": "Asha Verma", "father_name": "Ramesh Verma", "dob": "1950-03-14",
         "gender": "Female", "category": "OBC", "mobile": "9876543210", "aadhaar": aadhaar(), "village": "Rampur", "tehsil": "Sadar",
         "district": "Lucknow", "state": "Uttar Pradesh", "pincode": "226001", "annual_income": 90000, "account_no": "123456789012",
         "ifsc": "SBIN0001234"}
    j = client.post("/api/v1/pension_application/draft", headers=h, json={"fields": f}).json()
    assert j["missing"] == ["disability_percent"] and not j["complete"]
    j = client.post("/api/v1/pension_application/draft", headers=h, json={"fields": {**f, "disability_percent": 45}}).json()
    assert j["complete"] and j["values"]["dob"] == "1950-03-14"
    f["pension_type"] = "Old age pension"
    j = client.post("/api/v1/pension_application/draft", headers=h, json={"fields": {**f, "disability_percent": 45}}).json()
    assert j["complete"] and "disability_percent" in j["ignored"]
    bad = client.post("/api/v1/ayushman_card/draft", headers=h, json={"fields": {"aadhaar": "123456789012", "family_members": 0}}).json()
    assert bad["errors"]["aadhaar"]["code"] == "aadhaar_invalid" and "family_members" in bad["errors"]
