"""DwaniForms tests: core needs only pytest + reportlab + numpy (no models, no DwaniLive); the HTTP tests are skipped unless
fastapi + httpx are installed. Run: python -m pytest tests/test_dwaniforms.py"""
import asyncio
import datetime as dt
import json

from dwaniforms import export, messages, validators
from dwaniforms.schema import load_all, parse_template
from dwaniforms.service import FormService
from dwaniforms.session import FormSession
from dwaniforms.spoken import spoken_to_code, spoken_to_date, spoken_to_digits, spoken_to_int
from dwaniforms.validators import verhoeff_append

run = asyncio.run
TEMPLATES = load_all()
AADHAAR = verhoeff_append("23456789012")


class FakeMT:
    """Tags text with the target language, like pipeline.FakeTranslationBackend, but maps a few answers back to English."""
    def __init__(self): self.calls = []
    async def translate(self, text, lang): self.calls.append((text, lang)); return f"[{lang}] {text}"
    async def translate_between(self, text, src, tgt): self.calls.append((text, tgt)); return {"किसान": "Farmer"}.get(text, f"[{tgt}] {text}")


# ---- spoken ----------------------------------------------------------------
def test_digit_words_en_hi_and_devanagari():
    assert spoken_to_digits("nine eight seven six double five four three two one") == "9876554321"
    assert spoken_to_digits("नौ आठ सात छह पाँच चार तीन दो एक शून्य") == "9876543210"
    assert spoken_to_digits("९८७६ ५४३२ १०") == "9876543210"
    assert spoken_to_digits("डबल दो तीन") == "223"

def test_quantities():
    assert spoken_to_int("पच्चीस") == 25 and spoken_to_int("thirty five") == 35
    assert spoken_to_int("two lakh fifty thousand") == 250000 and spoken_to_int("२ लाख ५० हजार") == 250000
    assert spoken_to_int("hello") is None

def test_codes_and_dates():
    assert spoken_to_code("ए बी सी डी ई एक दो तीन चार एफ") == "ABCDE1234F"
    assert spoken_to_date("fifteen august nineteen ninety") == (15, 8, 1990)
    assert spoken_to_date("१५/०८/१९९०") == (15, 8, 1990) and spoken_to_date("पंद्रह अगस्त 1990") == (15, 8, 1990)


# ---- validators --------------------------------------------------------------
def test_aadhaar_checksum():
    assert validators.aadhaar(AADHAAR)[0]
    assert validators.aadhaar(AADHAAR[:-1] + str((int(AADHAAR[-1]) + 1) % 10))[2] == "aadhaar_invalid"
    assert validators.aadhaar("1234")[2] == "aadhaar_length"
    assert validators.aadhaar("0" + AADHAAR[1:])[2] == "aadhaar_invalid"

def test_mobile_pin_pan_ifsc_date():
    assert validators.mobile("+91 98765 43210")[1] == "9876543210"
    assert validators.mobile("1234567890")[2] == "mobile_invalid"
    assert validators.pincode("११००३१")[1] == "110031" and not validators.pincode("012345")[0]
    assert validators.pan("abcde 1234 f")[0] and not validators.pan("ABCDE12345")[0]
    assert validators.ifsc("SBIN0001234")[0] and not validators.ifsc("SBIN1001234")[0]
    today = dt.date(2026, 10, 5)
    assert validators.date("15 August 2010", min_age=18, today=today)[2] == "age_low"
    assert validators.date("15 August 1990", min_age=18, today=today)[1] == "1990-08-15"
    assert validators.date("15 August 2030", today=today)[2] == "date_future"
    assert validators.date("31 February 1990")[2] == "date_invalid"


# ---- templates ----------------------------------------------------------------
def test_all_templates_load_and_have_hindi_prompts():
    assert set(TEMPLATES) >= {"e_shram", "pm_kisan", "bank_kyc", "health_registration", "income_certificate"}
    for t in TEMPLATES.values():
        for f in t.fields:
            assert "hi" in f.prompt, (t.id, f.id)

def test_bad_template_rejected():
    import pytest
    with pytest.raises(ValueError):
        parse_template({"id": "x", "title": "x", "fields": [{"id": "a", "kind": "bogus", "label": "a", "prompt": {"en": "?"}}]})


# ---- yes/no ---------------------------------------------------------------------
def test_yes_no_multilingual_and_no_wins():
    P = messages.parse_yes_no
    assert P("हाँ") is True and P("जी सही है") is True and P("yes") is True and P("అవును") is True
    assert P("नहीं") is False and P("no, wrong") is False and P("haan nahi") is False
    assert P("umm") is None


# ---- session flow -----------------------------------------------------------------
def make(form="pm_kisan", lang="hi", mt=None):
    return FormSession(TEMPLATES[form], lang, mt)

def say(s, *texts):
    out = None
    for t in texts:
        out = run(s.submit(t))
    return out

def test_full_happy_path_hindi_pm_kisan():
    s = make()
    r = run(s.start())
    assert "नमस्ते" in r.text and "पूरा नाम" in r.text
    # name -> confirm -> yes
    r = say(s, "रमेश कुमार"); assert r.phase == "confirm" and "रमेश कुमार" in r.text
    say(s, "हाँ"); say(s, "सुरेश कुमार", "हाँ")
    say(s, "पंद्रह अगस्त 1985", "हाँ")
    r = say(s, "पुरुष"); assert s.answers["gender"].value == "Male"          # choice: no confirm round trip
    r = say(s, "ओबीसी"); assert s.answers["category"].value == "OBC"
    say(s, "नौ आठ सात छह पाँच चार तीन दो एक शून्य", "हाँ")
    assert s.answers["mobile"].value == "9876543210"
    r = say(s, " ".join(AADHAAR))
    assert r.phase == "confirm" and "2 3 4 5  6 7 8 9  0 1 2 4" == r.text.split(":")[1].split(".")[0].strip().replace("  ", "  ")[:len("2 3 4 5  6 7 8 9  0 1 2 4")]
    say(s, "हाँ")
    assert s.answers["aadhaar"].value == AADHAAR and s.current.id == "village"

def test_invalid_then_retry_then_escalate():
    s = make(); run(s.start()); say(s, "रमेश", "हाँ", "सुरेश", "हाँ", "15 August 1985", "हाँ", "male", "general")
    assert s.current.id == "mobile"
    # a number that is too LONG is wrong (a too-short one is treated as "said in pieces": see the next test)
    r = say(s, "123456789012"); assert r.phase == "ask" and "10 अंक" in r.text
    r = say(s, "1234567890123"); assert r.phase == "ask"
    r = say(s, "99999999999"); assert r.phase == "escalate" and "ऑपरेटर" in r.text
    ok, err = s.operator_set("mobile", "9876543210"); assert ok and s.current.id == "aadhaar"
    assert s.operator_set("aadhaar", "123456789012")[1] == "aadhaar_invalid"

def test_denial_reasks_and_confirm_ignores_noise():
    s = make(); run(s.start())
    say(s, "रमेश")
    r = say(s, "hmm"); assert r.phase == "confirm"                      # unclear -> stays on confirm
    r = say(s, "नहीं"); assert r.phase == "ask" and "दोबारा" in r.text and "रमेश" not in s.answers.get("full_name", type("x", (), {"value": ""})).value
    say(s, "राम", "हाँ"); assert s.answers["full_name"].value == "राम" or s.answers["full_name"].needs_review

def test_age_gate_blocks_minor():
    s = make(); run(s.start()); say(s, "रमेश", "हाँ", "सुरेश", "हाँ")
    r = say(s, "15 August 2015"); assert r.phase == "ask" and "उम्र कम" in r.text

def test_optional_skip_and_yesno_field():
    s = make("bank_kyc", "en"); run(s.start())
    say(s, "Ramesh Kumar", "yes", "Suresh Kumar", "yes", "15 August 1985", "yes", "male")
    say(s, "9876543210", "yes", " ".join(AADHAAR), "yes")
    assert s.current.id == "pan"
    say(s, "skip"); assert s.answers["pan"].value == "" and s.current.id == "address_line"

def test_name_in_other_language_flagged_for_review_not_translated():
    mt = FakeMT(); s = make("pm_kisan", "ta", mt); run(s.start()); say(s, "ரமேஷ்", "ஆம்")
    a = s.answers["full_name"]; assert a.value == "ரமேஷ்" and a.needs_review
    assert not any(c[0] == "ரமேஷ்" for c in mt.calls)                    # names are never machine-translated

def test_non_hindi_messages_translated_and_cached():
    # Kannada: no hand-written language pack (yet), so the kiosk's translator is used
    mt = FakeMT(); s = make("pm_kisan", "kn", mt); r = run(s.start())
    assert r.text.startswith("[kn]") and r.machine_translated
    n = len(mt.calls); run(s._prompt(s.current)); assert len(mt.calls) == n   # cached

def test_free_text_translated_to_form_language_and_flagged():
    t = TEMPLATES["e_shram"]; mt = FakeMT(); s = FormSession(t, "hi", mt); s.idx = [f.id for f in t.fields].index("occupation"); s.phase = "ask"
    say(s, "किसान", "हाँ"); a = s.answers["occupation"]; assert a.value == "Farmer" and a.needs_review

def test_redo_field():
    s = make(); run(s.start()); say(s, "रमेश", "हाँ"); r = run(s.redo("full_name"))
    assert "full_name" not in s.answers and s.current.id == "full_name"


# ---- export + service ----------------------------------------------------------------
def test_json_and_pdf_export():
    s = make(); run(s.start()); say(s, "Ramesh", "yes")
    data = json.loads(export.to_json(s, include_native=False))
    assert data["form"] == "pm_kisan" and data["completed"] is False and "native" not in data["fields"][0]
    pdf = export.to_pdf(s, mask_sensitive=True); assert pdf.startswith(b"%PDF")

def test_service_audio_path_with_fake_asr():
    import numpy as np
    class ASR:
        def __init__(self): self.lang = None
        def set_language(self, l): self.lang = l
        async def transcribe(self, seg): assert seg.sample_rate == 16000 and len(seg.samples) == 1600; return "रमेश"
    svc = FormService(ASR(), None); asr = svc.asr
    s, _ = run(svc.create("pm_kisan", "hi"))
    transcript, reply = run(svc.answer_audio(s.id, (np.zeros(1600, dtype="<i2")).tobytes()))
    assert transcript == "रमेश" and reply.phase == "confirm" and asr.lang == "hi"

def test_service_rejects_asr_for_unsupported_language():
    import pytest
    svc = FormService(object(), None); s, _ = run(svc.create("pm_kisan", "or"))
    with pytest.raises(RuntimeError): run(svc.answer_audio(s.id, b"\0\0" * 100))


# =====================================================================================
# Regression tests for the review fixes
# =====================================================================================
import pytest


def at(s, field_id):
    """Put a fresh session on a given field (as if the earlier ones had been answered)."""
    s.idx = [f.id for f in s.template.fields].index(field_id); s.phase = "ask"; return s


def test_choice_matches_despite_punctuation_from_whisper():
    s = make("pm_kisan", "en"); g = s.template.field_by_id("gender")
    assert s._match_option(g, "Female.") == "Female" and s._match_option(g, "Male,") == "Male"
    assert s._match_option(g, "male or female") is None                      # still ambiguous -> no match
    sh = make("pm_kisan", "hi")
    assert sh._match_option(g, "महिला।") == "Female" and sh._match_option(g, "पुरुष।") == "Male"


def test_english_words_are_not_digits_in_numbers():
    assert spoken_to_digits("aadhaar no 1234 5678 9012") == "123456789012"
    assert spoken_to_int("no income") is None
    assert spoken_to_int("2 lakh for the year") == 200000
    assert spoken_to_int("I sat on 2 acres") == 2
    assert spoken_to_int("do lakh") == 200000                                # Hindi "do" still works before a scale word
    assert spoken_to_digits("nine to for") == "924"                          # ...and mishearings still work in digit dictation


def test_romanised_hindi_numbers_21_to_99():
    assert spoken_to_int("pachchis") == 25 and spoken_to_int("navasi") == 89
    assert spoken_to_int("ek lakh pachas hazaar") == 150000


def test_skip_word_inside_an_answer_does_not_skip():
    P = messages.is_skip
    assert P("skip") and P("Skip.") and P("please skip") and P("कोई नहीं") and P("I don't have it")
    assert not P("next to the temple") and not P("मंदिर के आगे") and not P("none of those")
    s = make("bank_kyc", "en"); at(s, "pan")
    say(s, "next to the temple")
    assert "pan" not in s.answers                                            # it was treated as an (invalid) PAN, not a skip


def test_redo_then_continue_does_not_reask_answered_fields():
    s = make(); run(s.start()); say(s, "रमेश", "हाँ", "सुरेश", "हाँ", "15 August 1985", "हाँ")
    assert s.current.id == "gender"
    run(s.redo("full_name")); say(s, "रमेश कुमार", "हाँ")
    assert s.current.id == "gender"                                          # jumped past answered father_name and dob


def test_operator_edit_of_a_later_field_does_not_derail_flow():
    s = make("pm_kisan", "en"); run(s.start()); say(s, "Ramesh")
    assert s.operator_set("dob", "15 8 1990")[0] and s.current.id == "full_name" and s.phase == "confirm"
    say(s, "yes", "Suresh", "yes")
    assert s.current.id == "gender"                                          # dob already filled by the operator: skipped


def test_operator_set_validates_every_kind_and_unknown_field():
    s = make("pm_kisan", "en")
    assert s.operator_set("gender", "female") == (True, "") and s.answers["gender"].value == "Female"
    assert s.operator_set("gender", "banana")[1] == "choice_invalid"
    assert s.operator_set("full_name", "x")[1] == "text_short"
    with pytest.raises(KeyError): s.operator_set("nope", "x")
    with pytest.raises(KeyError): run(s.redo("nope"))


def test_translated_welcome_survives_a_translator_that_mangles_braces():
    class Mangler(FakeMT):
        async def translate(self, text, lang): self.calls.append((text, lang)); return text.replace("{", "{ ").replace("}", " }")
    mt = Mangler(); s = make("pm_kisan", "kn", mt); r = run(s.start())           # used to raise KeyError
    assert "PM-Kisan" in r.text and "{" not in mt.calls[0][0]


def test_english_fallback_flag_without_translator():
    s = make("pm_kisan", "kn", None); r = run(s.start())
    assert s.english_fallback and "PM-Kisan" in r.text
    assert not make("pm_kisan", "en", None).english_fallback


def test_two_digit_year_pivot_is_relative_to_today():
    t = dt.date(2026, 10, 5)
    assert spoken_to_date("15-8-28", t) == (15, 8, 1928) and spoken_to_date("15-8-25", t) == (15, 8, 2025)
    assert spoken_to_date("15-8-90", t) == (15, 8, 1990)
    assert validators.date("15-8-28", today=t)[1] == "1928-08-15"


def test_account_number_all_zeros_rejected_and_superscript_does_not_crash():
    assert not validators.account_number("000000000")[0] and validators.account_number("123456789")[0]
    assert spoken_to_int("\u00b2") is None and spoken_to_digits("\u00b2") == ""


def test_date_readback_is_spoken_not_iso():
    f = TEMPLATES["pm_kisan"].field_by_id("dob")
    assert FormSession.readback(f, "1990-08-15") == "15 August 1990"
    assert FormSession.readback(f, "1990-08-15", "hi") == "15 अगस्त 1990"
    assert FormSession.readback(f, "1990-08-15", "kn") == "15 8 1990"            # no month names for this language


def test_masking_in_form_values_json_and_pdf():
    s = make("pm_kisan", "en"); s.operator_set("aadhaar", AADHAAR)
    row = next(r for r in s.form_values(mask=True) if r["id"] == "aadhaar")
    assert row["value"] == "\u2022" * 8 + AADHAAR[-4:] and row["native"] == ""
    assert next(r for r in s.form_values() if r["id"] == "aadhaar")["value"] == AADHAAR
    assert AADHAAR not in export.to_json(s, mask_sensitive=True) and AADHAAR in export.to_json(s)
    assert export.to_pdf(s, mask_sensitive=True).startswith(b"%PDF")


def test_pdf_formats_dates_dd_mm_yyyy_and_escapes_markup():
    assert export._human_date("1990-08-15") == "15/08/1990" and export._human_date("n/a") == "n/a"
    s = make("pm_kisan", "en"); s.operator_set("full_name", "A <b>&</b> B"); assert export.to_pdf(s).startswith(b"%PDF")


def test_sessions_expire_on_inactivity_not_age():
    svc = FormService(None, None); s, _ = run(svc.create("pm_kisan", "en"))
    s.created -= 10 * 3600                                                   # old, but just used: must survive
    assert svc.purge_expired() == 0 and svc.get(s.id) is s
    s.last_active -= 31 * 60
    assert svc.purge_expired() == 1
    with pytest.raises(KeyError): svc.get(s.id)


def test_completed_sessions_expire_sooner():
    svc = FormService(None, None); s, _ = run(svc.create("pm_kisan", "en")); s.phase = "done"
    s.last_active -= 16 * 60
    assert svc.purge_expired() == 1


def test_capabilities():
    caps = FormService(None, None).capabilities()
    assert {k: caps[k] for k in ("asr", "asr_langs", "translation")} == {"asr": False, "asr_langs": [], "translation": False}
    assert set(caps["records"]) == {"land", "ration", "meta"}
    assert FormService(object(), FakeMT()).capabilities()["asr_langs"] and FormService(object(), FakeMT()).capabilities()["translation"]


def test_blocking_asr_runs_off_the_event_loop():
    import numpy as np, threading
    seen = {}
    class SyncASR:
        def transcribe(self, seg): seen["thread"] = threading.current_thread(); return "रमेश"
    svc = FormService(SyncASR(), None); s, _ = run(svc.create("pm_kisan", "hi"))
    assert run(svc.answer_audio(s.id, np.zeros(1600, dtype="<i2").tobytes()))[0] == "रमेश"
    assert seen["thread"] is not threading.main_thread()


# ---- HTTP layer (skipped when fastapi/httpx are not installed) ------------------------------------------
@pytest.fixture
def client():
    pytest.importorskip("fastapi"); pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from dwaniforms.api import create_router
    app = FastAPI(); app.include_router(create_router(FormService(None, None)))
    return TestClient(app)


def _new(client, lang="en"):
    r = client.post("/form/sessions", json={"form_id": "pm_kisan", "lang": lang}); assert r.status_code == 200, r.text
    return r.json()["session_id"]


def test_api_flow_and_masking_defaults(client):
    sid = _new(client)
    assert client.get("/form/capabilities").json()["asr"] is False
    assert client.post(f"/form/sessions/{sid}/operator", json={"field_id": "aadhaar", "value": AADHAAR}).status_code == 200
    masked = next(f for f in client.get(f"/form/sessions/{sid}").json()["fields"] if f["id"] == "aadhaar")["value"]
    assert masked.endswith(AADHAAR[-4:]) and AADHAAR not in masked
    full = next(f for f in client.get(f"/form/sessions/{sid}", headers={"x-reveal-sensitive": "1"}).json()["fields"] if f["id"] == "aadhaar")["value"]
    assert full == AADHAAR
    assert AADHAAR in client.get(f"/form/sessions/{sid}/export.json").text                  # portal import: full by default
    assert AADHAAR not in client.get(f"/form/sessions/{sid}/export.json?mask=true").text
    assert client.get(f"/form/sessions/{sid}/export.pdf").content.startswith(b"%PDF")      # masked by default
    assert client.delete(f"/form/sessions/{sid}").json() == {"deleted": True}
    assert client.get(f"/form/sessions/{sid}").status_code == 404


def test_api_bad_input_gives_4xx_not_500(client):
    sid = _new(client)
    assert client.post("/form/sessions", json={}).status_code == 422
    assert client.post("/form/sessions", json={"form_id": "nope"}).status_code == 404
    assert client.post(f"/form/sessions/{sid}/text", json={}).status_code == 422
    assert client.post(f"/form/sessions/{sid}/redo", json={"field_id": "nope"}).status_code == 422
    assert client.post(f"/form/sessions/{sid}/operator", json={"field_id": "nope", "value": "x"}).status_code == 422
    assert client.post(f"/form/sessions/{sid}/operator", json={"field_id": "aadhaar", "value": "1234"}).status_code == 422
    assert client.post(f"/form/sessions/{sid}/text", content='{"text": "x"}', headers={"content-type": "text/plain"}).status_code == 422


def test_api_audio_guards(client):
    sid = _new(client)
    assert client.post(f"/form/sessions/{sid}/audio", content=b"\0\0").status_code == 400            # header required
    assert client.post(f"/form/sessions/{sid}/audio", content=b"\0\0", headers={"x-sample-rate": "abc"}).status_code == 400
    assert client.post(f"/form/sessions/{sid}/audio", content=b"\0" * 6_000_002, headers={"x-sample-rate": "16000"}).status_code == 413
    assert client.post(f"/form/sessions/{sid}/audio", content=b"\0\0", headers={"x-sample-rate": "16000"}).status_code == 422   # no ASR


def test_api_text_answer_roundtrip(client):
    sid = _new(client)
    r = client.post(f"/form/sessions/{sid}/text", json={"text": "Ramesh Kumar"}).json()
    assert r["phase"] == "confirm" and "Ramesh Kumar" in r["text"] and r["english_fallback"] is False


def test_number_said_in_pieces_is_joined():
    """Phone speech recognition stops at a pause: "98765" ... "43210" must still make one mobile number."""
    s = make(); run(s.start()); say(s, "रमेश", "हाँ", "सुरेश", "हाँ", "15 August 1985", "हाँ", "male", "general")
    r = say(s, "अट्ठानवे छिहत्तर"); assert r.phase == "ask" and "बाकी 6 अंक" in r.text
    r = say(s, "जीरो चौवन बत्तीस"); assert r.phase == "ask" and "बाकी 1 अंक" in r.text
    r = say(s, "एक"); assert r.phase == "confirm" and r.pending == "9876054321"
