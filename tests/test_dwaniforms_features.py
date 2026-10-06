"""DwaniForms: regressions for the review fixes, and the three citizen-service flows
(complaint / RTI drafting, land & ration look-up, scheme eligibility advisor)."""
import asyncio
import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

import pytest

from dwaniforms import eligibility, extract, records, validators as V
from dwaniforms.messages import parse_yes_no
from dwaniforms.schema import load_all, parse_template
from dwaniforms.service import FormService
from dwaniforms.session import FormSession
from dwaniforms.spoken import spoken_to_khasra, spoken_to_number

TODAY = dt.date(2026, 10, 5)


class MT:
    async def translate(self, text, lang):
        return f"[{lang}] {text}"

    async def translate_between(self, text, src, tgt):
        return f"[{src}->{tgt}] {text}"


def run(coro):
    return asyncio.run(coro)


async def converse(svc, form, lang, answers, prefill_from=None):
    s, r = await svc.create(form, lang, prefill_from)
    for a in answers:
        r = await svc.answer_text(s.id, a)
        if r.done:
            break
    return s, r


@pytest.fixture()
def svc(tmp_path):
    return FormService(None, MT(), record_store=records.RecordStore(records.write_demo(tmp_path, as_of="2026-09-01")))


# ---------------------------------------------------------------- review fixes
@pytest.mark.parametrize("said,value", [
    ("2.5 acre", "2.5"), ("1,50,000", "150000"), ("ढाई लाख", "250000"), ("dhai lakh", "250000"), ("डेढ़ लाख", "150000"),
    ("sava do lakh", "225000"), ("paune do lakh", "175000"), ("साढ़े तीन सौ", "350"), ("one and a half lakh", "150000"),
    ("half acre", "0.5"), ("two point five", "2.5"), ("₹ 1,20,000 per year", "120000"), ("2 lakh for the year", "200000"),
])
def test_indian_quantities(said, value):
    assert format(spoken_to_number(said), "f") == value


def test_number_field_rules():
    assert V.number("2.5 acre")[2] == "number_whole"                       # whole-number field: no silent rounding
    assert V.number("2.5 acre", decimals=2, unit="acre") == (True, "2.5", "")
    assert V.number("2 hectare", decimals=2, unit="acre") == (True, "4.94", "")
    assert V.number("3 bigha", decimals=2, unit="acre")[2] == "unit_acres"  # state-specific: ask in acres, don't guess
    assert V.number("no income")[2] == "number_invalid"


@pytest.mark.parametrize("said,want", [("yes no problem", True), ("हाँ, कोई दिक्कत नहीं", True), ("haan ji koi baat nahi", True),
                                       ("नहीं, गलत है", False), ("koi baat nahi", None), ("जी हाँ", True)])
def test_yes_no_with_benign_negations(said, want):
    assert parse_yes_no(said) is want


def _tpl(fields):
    return parse_template({"id": "t", "title": "T", "fields": fields})


def test_optional_yes_no_answer_no_is_recorded_not_skipped():
    t = _tpl([{"id": "lpg", "kind": "yesno", "label": "LPG", "prompt": {"en": "LPG?"}, "required": False},
              {"id": "n", "kind": "name", "label": "N", "prompt": {"en": "Name?"}}])
    s = FormSession(t, "hi")
    run(s.start())
    run(s.submit("नहीं"))
    assert s.answers["lpg"].value == "No"


def test_longest_option_wins():
    t = load_all()["scheme_advisor"]
    s = FormSession(t, "en")
    assert s._match_option(t.field_by_id("occupation"), "I am a farm labourer") == "Farm labourer"
    assert s._match_option(t.field_by_id("occupation"), "I do labour work") == "Daily-wage / unorganised worker"


# ---------------------------------------------------------------- spoken identifiers
@pytest.mark.parametrize("said,khasra", [("145 बटा 2", "145/2"), ("एक सौ पैंतालीस बटा दो", "145/2"), ("145 by 2", "145/2"),
                                         ("145 क", "145क"), ("खसरा नंबर तीन सौ बारह", "312"), ("nahi pata", None)])
def test_khasra(said, khasra):
    assert spoken_to_khasra(said) == khasra


def test_ration_card_numbers():
    assert V.ration_card("ration card number nine eight seven six five four three two one zero") == (True, "9876543210", "")
    assert V.ration_card("मेरा राशन कार्ड नंबर 1234 5678 9012")[1] == "123456789012"
    assert V.ration_card("12345")[0] is False


# ---------------------------------------------------------------- extraction
@pytest.mark.parametrize("text,dep", [
    ("हमारे गाँव में बिजली नहीं है, ट्रांसफार्मर जल गया", "Electricity"),
    ("kotedar ne ration nahi diya", "Food & civil supplies (ration)"),
    ("street light kharab hai aur nali band hai", "Municipal (sanitation)"),        # "street light" isn't Electricity
    ("school mein teacher nahi aate aur hospital mein doctor bhi nahi", None),      # tie: ask, don't guess
])
def test_department_guess(text, dep):
    assert extract.guess_department(text) == dep


def test_place_and_date_guess():
    assert extract.guess_place("ग्राम सोनपुर तहसील बिसवां जिला सीतापुर")[0] == "Village सोनपुर, Tehsil बिसवां, District सीतापुर"
    assert extract.guess_date("2 baar 15 August ko application di", TODAY) == "2026-08-15"   # not the 17th
    assert extract.guess_date("20 दिसंबर को राशन नहीं मिला", TODAY) == "2025-12-20"          # not in the future
    assert extract.guess_date("कल से पानी नहीं", TODAY) == "2026-10-04"
    assert extract.guess_date("31 December 2026 ko aayenge", TODAY) is None


def test_department_lexicon_matches_form_options():
    deps = set(extract.departments())
    for tid in ("grievance", "rti_application"):
        opts = {o.value for o in load_all()[tid].field_by_id("department").options}
        assert deps <= opts and opts - deps == {"Other"}


# ---------------------------------------------------------------- 1. complaint / RTI
def test_grievance_by_voice_in_hindi(svc):
    s, r = run(converse(svc, "grievance", "hi", [
        "सुनीता देवी", "हाँ", "98765 43210", "हाँ",
        "हमारे गाँव सोनपुर में 20 सितंबर से बिजली नहीं है, ट्रांसफार्मर जल गया है, तहसील बिसवां जिला सीतापुर", "हाँ",
        "हाँ", "हाँ", "हाँ",                       # department, place, date: suggested from the complaint, confirmed
        "छोड़ो",                                     # relief (optional)
        "नहीं", "विभाग", "पानी"]))                 # final read-back: "no" -> which part? -> the department
    assert s.answers["department"].value == "Water supply" and s.answers["location"].value.startswith("Village सोनपुर")
    assert s.answers["incident_date"].value.endswith("-09-20")
    assert r.phase == "final"                       # read back again after the fix
    r = run(svc.answer_text(s.id, "हाँ"))
    assert r.done and r.outcome["type"] == "draft"
    assert "हमारे गाँव सोनपुर" in r.outcome["drafts"]["hi"] and "जल आपूर्ति" in r.outcome["drafts"]["hi"]
    assert "[hi->en]" in r.outcome["drafts"]["en"] and "Water supply" in r.outcome["drafts"]["en"]


def test_wrong_suggestion_just_asks():
    t = load_all()["grievance"]
    s = FormSession(t, "en", MT())
    s.extractor = extract.extract_for
    run(s.start())
    for a in ("Ravi Kumar", "yes", "9876543210", "yes", "No electricity in village Rampur since yesterday", "yes"):
        r = run(s.submit(a))
    assert r.phase == "confirm" and "Electricity" in r.text
    r = run(s.submit("no"))
    assert r.phase == "ask" and r.text == t.field_by_id("department").prompt["en"] and s.attempts == 0


def test_rti_application(svc):
    s, r = run(converse(svc, "rti_application", "en", [
        "Ravi Kumar", "yes", "House 12, Rampur, Sitapur", "yes", "261001", "yes", "skip",
        "Please give the list of MGNREGA job card holders and wages paid in my panchayat", "yes",
        "yes",                                  # suggested department (panchayat / MGNREGA)
        "2023 to 2025", "yes", "yes", "by post", "yes"]))
    assert r.done
    en = r.outcome["drafts"]["en"]
    assert "Section 6(1) of the Right to Information Act, 2005" in en and "BPL" in en and "exempt" in en
    assert s.answers["department"].value == "Rural development & panchayat"


# ---------------------------------------------------------------- 2. records
def test_land_lookup_hindi(svc):
    s, r = run(converse(svc, "land_record", "hi", ["सीतापुर", "हाँ", "सोनपुर", "हाँ", "एक सौ पैंतालीस बटा दो", "हाँ"]))
    assert r.done and r.outcome["found"] and "डेमो राम लाल" in r.text and "1 सितंबर 2026" in r.text
    assert "डेमो रिकॉर्ड" in r.text                                  # demo data is never passed off as real


def test_land_lookup_rejects_unknown_places_and_numbers(svc):
    s, r = run(svc.create("land_record", "en"))
    r = run(svc.answer_text(s.id, "Lucknow"))
    assert "could not find that district" in r.text
    s, r = run(converse(svc, "land_record", "en", ["Seetapur", "yes", "Sonepur", "yes", "999", "yes"]))
    assert r.done and r.outcome["found"] is False and "could not find khasra 999" in r.text


def test_same_village_name_in_two_districts(svc):
    _, r = run(converse(svc, "land_record", "en", ["Barabanki", "yes", "Rampur", "yes", "145/2", "yes"]))
    assert "DEMO Abdul Karim" in r.text                              # the Barabanki Rampur, not the Sitapur one


def test_ration_lookup_masks_the_name(svc):
    _, r = run(converse(svc, "ration_lookup", "en", ["1234 5678 9012", "yes"]))
    res = r.outcome["results"][0]
    assert res["Head of family"] == "D*** R*** L***" and res["Card no."].endswith("9012") and "PHH" in r.text
    assert "Ram Lal" not in r.text


def test_stale_records_are_flagged(tmp_path):
    store = records.RecordStore(records.write_demo(tmp_path, as_of="2025-01-01"))
    svc = FormService(None, MT(), record_store=store)
    _, r = run(converse(svc, "ration_lookup", "en", ["1234 5678 9012", "yes"]))
    assert r.outcome["stale"] and "copy is old" in r.text


def test_lookups_hidden_without_data(tmp_path):
    svc = FormService(None, None, record_store=records.RecordStore(tmp_path / "empty"))
    avail = {f["id"]: f["available"] for f in svc.forms()}
    assert avail["land_record"] is False and avail["ration_lookup"] is False and "scheme_advisor" not in avail


# ---------------------------------------------------------------- 3. eligibility
FARMER = dict(age="35", gender="Male", area="Rural", occupation="Farmer", land_acres="2.5", annual_income="120000",
              income_tax_payer="No", govt_employee="No", epfo="No", bank_account="Yes", ration_card="PHH / BPL",
              house="Kutcha house", category="OBC", lpg="N/A", widow="N/A")


def test_farmer_profile():
    r = eligibility.advise(FARMER)
    likely = {i["id"] for i in r["likely"]}
    assert {"pm_kisan", "pmfby", "e_shram", "pmay_g", "apy", "pm_sym"} <= likely
    assert "ignwps" not in {i["id"] for i in r["maybe"]}                # widow pension isn't "maybe" for a man
    assert r["forms"][:2] == ["e_shram", "pm_kisan"] or set(r["forms"]) >= {"pm_kisan", "e_shram"}


def test_rules_exclude_and_soften():
    taxpayer = dict(FARMER, income_tax_payer="Yes")
    assert "pm_kisan" not in {i["id"] for i in eligibility.advise(taxpayer)["likely"] + eligibility.advise(taxpayer)["maybe"]}
    clerk = dict(FARMER, govt_employee="Yes")
    maybe = {i["id"]: i for i in eligibility.advise(clerk)["maybe"]}
    assert "pm_kisan" in maybe and "Group D" in " ".join(maybe["pm_kisan"]["reasons"])   # soft rule: maybe, with why
    senior = dict(FARMER, age="72", occupation="Not working", land_acres="N/A", ration_card="Other (APL)")
    assert "pmjay" in {i["id"] for i in eligibility.advise(senior)["likely"]}            # 70+: covered regardless


def test_scheme_files_are_consistent():
    advisor = load_all()["scheme_advisor"]
    fields = {f.id: f for f in advisor.fields}
    templates = load_all()
    for s in eligibility.load_schemes():
        assert {"id", "name", "benefit", "source", "last_reviewed", "rules", "apply"} <= set(s), s["id"]
        assert s["source"].startswith("https://") and dt.date.fromisoformat(s["last_reviewed"])
        assert s["form"] is None or s["form"] in templates, s["id"]

        def check(rule):
            if "any_of" in rule:
                for r in rule["any_of"]:
                    check(r)
                return
            if "check" in rule:
                return
            f = fields[rule["fact"]]                                    # every fact is an interview question
            if f.kind == "choice":
                for v in rule.get("in", []) + ([rule["eq"]] if "eq" in rule else []):
                    assert v in {o.value for o in f.options}, (s["id"], rule)
            if f.kind == "yesno" and "eq" in rule:
                assert rule["eq"] in ("Yes", "No")
        for rule in s["rules"]:
            check(rule)


def test_stale_rules_flagged():
    s = {"last_reviewed": "2026-01-01"}
    assert eligibility.stale(s, dt.date(2026, 9, 1)) and not eligibility.stale(s, dt.date(2026, 3, 1))


def test_advisor_conversation_and_prefill(svc):
    adv, r = run(converse(svc, "scheme_advisor", "hi", [
        "पैंतीस", "हाँ", "पुरुष", "गाँव", "किसान", "ढाई", "हाँ", "एक लाख बीस हज़ार", "हाँ", "नहीं", "नहीं", "नहीं", "हाँ",
        "पात्र गृहस्थी", "कच्चा", "छोड़ो", "ओबीसी"]))
    assert r.done and r.outcome["type"] == "advisor" and "पीएम-किसान" in r.text
    assert "lpg" not in adv.answers and "widow" not in adv.answers              # not asked of a man
    s, r = run(svc.create("pm_kisan", "hi", adv.id))
    assert s.suggestions == {"gender": ("Male", "Male", False), "category": ("OBC", "OBC", False),
                             "land_acres": ("2.5", "2.5", False)}


# ---------------------------------------------------------------- HTTP
def test_http_flow_with_outcome(svc):
    TestClient = pytest.importorskip("fastapi.testclient").TestClient
    from fastapi import FastAPI
    from dwaniforms.api import create_router
    app = FastAPI()
    app.include_router(create_router(svc))
    c = TestClient(app)
    forms = {f["id"]: f for f in c.get("/form/forms").json()}
    assert forms["grievance"]["flow"] == "grievance" and "scheme_advisor" not in forms
    assert any(x["id"] == "pm_kisan" for x in c.get("/form/schemes").json())
    sid = c.post("/form/sessions", json={"form_id": "ration_lookup", "lang": "en"}).json()["session_id"]
    c.post(f"/form/sessions/{sid}/text", json={"text": "1234 5678 9012"})
    r = c.post(f"/form/sessions/{sid}/text", json={"text": "yes"}).json()
    assert r["done"] and r["outcome"]["type"] == "lookup" and r["flow"] == "lookup_ration"
    assert json.loads(c.get(f"/form/sessions/{sid}/export.json").text)["outcome"]["found"] is True
    assert c.post("/form/sessions", json={"form_id": "pm_kisan", "lang": "en", "prefill_from": "nothex!"}).status_code == 422


def test_pending_answer_pencilled_but_aadhaar_masked(svc):
    TestClient = pytest.importorskip("fastapi.testclient").TestClient
    from fastapi import FastAPI
    from dwaniforms.api import create_router
    from dwaniforms.validators import verhoeff_append
    app = FastAPI()
    app.include_router(create_router(svc))
    c = TestClient(app)
    r = c.post("/form/sessions", json={"form_id": "pm_kisan", "lang": "hi"}).json()
    assert r["title"] == "पीएम-किसान आवेदन"                                   # title in the citizen's language
    sid = r["session_id"]
    r = c.post(f"/form/sessions/{sid}/text", json={"text": "रमेश यादव"}).json()
    assert r["phase"] == "confirm" and r["pending"] == "रमेश यादव"              # pencilled into the form sheet
    name_row = next(f for f in r["fields"] if f["id"] == "full_name")
    assert name_row["label_local"] and name_row["required"] is True
    aadhaar = verhoeff_append("23456789012")
    for _ in range(30):                                              # walk to the Aadhaar question
        if r.get("field_id") == "aadhaar" and r["phase"] == "ask":
            break
        f = next(x for x in r["fields"] if x["id"] == r["field_id"])
        answer = {"name": "श्याम यादव", "date": "15 August 1980", "mobile": "98765 43210"}.get(f["kind"]) or \
            (f["options"][0]["label"] if f.get("options") else "हाँ")
        r = c.post(f"/form/sessions/{sid}/text", json={"text": "हाँ" if r["phase"] == "confirm" else answer}).json()
    assert r["field_id"] == "aadhaar"
    r = c.post(f"/form/sessions/{sid}/text", json={"text": " ".join(aadhaar)}).json()
    assert r["phase"] == "confirm" and aadhaar not in json.dumps(r) and r["pending"].endswith(aadhaar[-4:])
    gender = next(f for f in r["fields"] if f["id"] == "gender")
    assert [o["value"] for o in gender["options"]] == ["Male", "Female", "Other"] and gender["options"][0]["label"] == "पुरुष"


def test_every_field_has_a_hindi_label():
    for t in load_all().values():
        assert all(f.label_in("hi") for f in t.fields), [f.id for f in t.fields if not f.label_in("hi")]
