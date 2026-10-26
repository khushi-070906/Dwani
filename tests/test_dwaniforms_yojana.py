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


def test_every_word_on_the_page_exists_in_both_languages():
    block = re.search(r"var T = \{(.*?)\n  \};", PAGE, re.S).group(1)
    hi, en = block.split("\n    en:", 1)
    keys = lambda part: re.findall(r"(?:^|[,{])\s*([a-z][a-z_0-9]*):\s*\"", part, re.M)
    assert keys(hi) and set(keys(hi)) == set(keys(en)), set(keys(hi)) ^ set(keys(en))
    for k in re.findall(r'data-t="([a-z_0-9]+)"', PAGE):
        assert k in keys(hi), k


def test_a_browser_without_javascript_is_told_what_to_do_in_both_languages():
    note = re.search(r"<noscript>(.*?)</noscript>", PAGE, re.S).group(1)
    assert "JavaScript" in note and re.search(r"[\u0900-\u097F]", note) and "turn on JavaScript" in note


def test_the_list_of_questions_may_be_cached_for_a_few_minutes_but_answers_never(client):
    q = client.get("/form/yojana/questions?lang=en")
    assert q.headers["cache-control"] == "public, max-age=300"          # no personal data in it; saves a round trip on a slow network
    for path, body in (("/form/yojana/advise", {"facts": FARMER}), ("/form/yojana/understand", {"question": "age", "text": "sixty two", "lang": "en"})):
        r = client.post(path, json=body)
        assert r.status_code == 200 and r.headers["cache-control"] == "no-store", path        # derived from what the person told us
    assert client.get("/form/yojana").headers["cache-control"] == "no-cache"


def test_life_insurance_pmjjby_is_offered_from_18_to_50_with_a_bank_account():
    base = {"gender": "Female", "area": "Urban", "occupation": "Daily-wage / unorganised worker", "annual_income": 100000,
            "income_tax_payer": "No", "govt_employee": "No", "bank_account": "Yes"}
    pmjjby = lambda age, **kw: [i for k in ("likely", "maybe") for i in yojana.advise(T, {**base, "age": age, **kw}, "en")[k] if i["id"] == "pmjjby"]
    assert pmjjby(30) and "436" in pmjjby(30)[0]["benefit"]
    assert pmjjby(18) and pmjjby(50)
    assert not pmjjby(17) and not pmjjby(51)                    # the age limits are the scheme's
    assert pmjjby(30, bank_account="No")[0]["id"] == "pmjjby"    # no account yet: still shown, as 'needs checking' ...
    assert pmjjby(30, bank_account="No") != pmjjby(30)


def test_every_scheme_has_text_in_all_ten_languages_and_a_recent_review_date():
    from dwaniforms.eligibility import load_schemes
    from dwaniforms import langpacks
    for s in load_schemes():
        for part in ("name", "benefit", "apply"):
            assert s[part]["en"] and s[part]["hi"], (s["id"], part)
            for lang in langpacks.packs():
                assert langpacks.text(lang, s[part]["en"]), (s["id"], part, lang)


def test_accident_insurance_pmsby_goes_to_70_so_a_man_past_the_life_cover_age_still_gets_it():
    facts = {"age": 62, "gender": "Male", "area": "Rural", "occupation": "Farmer", "land_acres": 1, "annual_income": 100000,
             "income_tax_payer": "No", "govt_employee": "No", "bank_account": "Yes"}
    got = {i["id"] for k in ("likely", "maybe") for i in yojana.advise(T, facts, "en")[k]}
    assert "pmsby" in got and "pmjjby" not in got              # 62: past 50 for life cover, within 70 for accident cover
    old = {i["id"] for k in ("likely", "maybe") for i in yojana.advise(T, {**facts, "age": 71}, "en")[k]}
    assert "pmsby" not in old


@pytest.mark.parametrize("said,lang", [("नहीं पता", "hi"), ("मुझे नहीं पता", "hi"), ("पता ही नहीं", "hi"), ("नही पता", "hi"),
                                       ("pata nahi", "hi"), ("nahi pata", "hi"), ("idk", "en")])
def test_every_way_of_saying_i_do_not_know_stays_not_understood_never_a_no(said, lang):
    # the one answer that must never become a "No": it would quietly remove a scheme from the list
    assert yojana.understand(T, "bank_account", said, lang) is None
    assert yojana.understand(T, "land_acres", said, lang) is None
    assert yojana.understand(T, "annual_income", said, lang) is None

