"""The company API (apikeys.py + b2b.py): signed keys, per-service scope, 30-day trial, one-shot drafts.

What must not break:
  * a key proves itself by signature; edited, expired, revoked or foreign keys never work, whatever they look like;
  * a key opens only the services it was issued for, and the API does not exist at all without the server's secret;
  * the same checks as the voice app (Aadhaar checksum, dates, choices) decide what a field may hold;
  * nothing is kept: no session opened, no personal data in logs, no key in any response, nothing cached;
  * the trial is bounded: end date, finished-draft quota, requests a minute, and bad guesses are slowed down.
"""
import datetime as dt
import json
import logging
import time

import pytest

from dwaniforms import apikeys, b2b, clock
from dwaniforms.service import FormService

TestClient = pytest.importorskip("fastapi.testclient").TestClient
from fastapi import FastAPI  # noqa: E402

SECRET = "test-secret-0123456789-abcdefghij"


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("DWANIFORMS_KEY_SECRET", SECRET)
    monkeypatch.delenv("DWANIFORMS_REVOKED_KEYS", raising=False)
    monkeypatch.delenv("DWANIFORMS_USAGE_LOG", raising=False)


def make(meter=None):
    svc = FormService(None, None)
    app = FastAPI()
    app.mount("/api/v1", b2b.build_api(svc, meter))
    return TestClient(app, raise_server_exceptions=False), svc


def key(services="rti,rti_appeal", **kw):
    raw, k = apikeys.issue("RTIwala", services.split(","), **kw)
    return raw, k


def H(raw):
    return {"Authorization": f"Bearer {raw}"}


RTI = {"full_name": "Asha Verma", "address": "12 Gandhi Road, Lucknow", "pincode": "226001", "mobile": "9876543210",
       "department": "Roads (PWD)", "information": "The status of my road repair request of June 2026.",
       "bpl": False, "period": "January 2026 to June 2026", "delivery": "By post"}


def rti_fields():
    from dwaniforms.schema import load_all
    t = load_all()["rti_application"]
    return t


# ---- the keys ---------------------------------------------------------------------------------------------------
def test_a_key_round_trips_and_says_what_it_is_for():
    raw, k = key(days=30, quota=100, rpm=20)
    got = apikeys.verify(raw)
    assert got == k and got.org == "RTIwala" and got.services == ("rti", "rti_appeal")
    assert got.quota == 100 and got.rpm == 20 and 29 <= got.days_left() <= 30
    assert raw.startswith("dfk_") and SECRET not in raw


def test_an_edited_key_is_refused():
    raw, _ = key()
    body, sig = raw[4:].split(".")
    forged = apikeys._b64(json.dumps({"v": 1, "i": "aaaaaaaaaa", "o": "Me", "s": ["*"], "a": 1, "e": 99999999999, "q": 10**6, "r": 6000}).encode())
    for bad in (f"dfk_{forged}.{sig}", f"dfk_{body}.{sig[:-2]}AA", f"dfk_{body}x.{sig}", raw.replace("dfk_", "dfx_")):
        with pytest.raises(apikeys.KeyError_):
            apikeys.verify(bad)


def test_a_key_from_another_secret_is_refused(monkeypatch):
    raw, _ = key()
    monkeypatch.setenv("DWANIFORMS_KEY_SECRET", "a-different-secret-0123456789-xyz")
    with pytest.raises(apikeys.KeyError_) as e:
        apikeys.verify(raw)
    assert e.value.code == "bad_signature"


def test_an_expired_key_is_refused_and_a_key_lasts_exactly_its_days():
    raw, k = key(days=30)
    assert apikeys.verify(raw, now=k.expires - 5).org == "RTIwala"
    with pytest.raises(apikeys.KeyError_) as e:
        apikeys.verify(raw, now=k.expires + 1)
    assert e.value.code == "expired"


def test_a_revoked_key_is_refused(monkeypatch):
    raw, k = key()
    monkeypatch.setenv("DWANIFORMS_REVOKED_KEYS", f"zzz, {k.id}")
    with pytest.raises(apikeys.KeyError_) as e:
        apikeys.verify(raw)
    assert e.value.code == "revoked"


@pytest.mark.parametrize("junk", ["", "dfk_", "dfk_.", "dfk_a.b", "dfk_" + "A" * 3000, "Bearer x", None, 5, "dfk_e30.AAAA", "dfk_!!!.???"])
def test_junk_never_crashes_and_never_passes(junk):
    with pytest.raises(apikeys.KeyError_):
        apikeys.verify(junk)


def test_without_a_secret_nothing_can_be_issued_or_checked(monkeypatch):
    monkeypatch.delenv("DWANIFORMS_KEY_SECRET")
    assert not apikeys.enabled()
    with pytest.raises(apikeys.KeyError_) as e:
        apikeys.issue("X Corp", ["rti"])
    assert e.value.code == "disabled"
    monkeypatch.setenv("DWANIFORMS_KEY_SECRET", "short")
    assert not apikeys.enabled()


@pytest.mark.parametrize("kw", [dict(org="x"), dict(org="Acme", services=[]), dict(org="Acme", services=["lookups"]),
                                dict(org="Acme", days=0), dict(org="Acme", days=9999), dict(org="Acme", quota=0), dict(org="Acme", rpm=0)])
def test_nonsense_cannot_be_issued(kw):
    kw.setdefault("services", ["rti"])
    with pytest.raises(ValueError):
        apikeys.issue(**kw)


def test_services_can_be_a_group_a_template_or_everything():
    k = apikeys.verify(apikeys.issue("A Corp", ["pm_kisan"])[0])
    assert k.allows("pm_kisan", "form") and not k.allows("e_shram", "form")
    k = apikeys.verify(apikeys.issue("A Corp", ["forms"])[0])
    assert k.allows("e_shram", "form") and not k.allows("rti_application", "rti")
    k = apikeys.verify(apikeys.issue("A Corp", ["*"])[0])
    assert k.allows("samjhao", "explain")


def test_the_command_line(capsys, monkeypatch):
    assert apikeys.main(["issue", "--org", "RTIwala", "--services", "rti,rti_appeal", "--days", "30"]) == 0
    out = capsys.readouterr().out
    raw = next(w for w in out.split() if w.startswith("dfk_"))
    assert "until" in out and "rti, rti_appeal" in out
    assert apikeys.main(["inspect", raw]) == 0
    assert apikeys.main(["inspect", raw + "x"]) == 1
    assert apikeys.main(["issue", "--org", "RTIwala", "--services", "nope"]) == 2
    assert apikeys.main(["services"]) == 0 and "rti_application" in capsys.readouterr().out


# ---- who may call -----------------------------------------------------------------------------------------------
def test_no_key_a_wrong_key_and_an_expired_key_are_all_401():
    c, _ = make()
    assert c.get("/api/v1/services").status_code == 401
    assert c.get("/api/v1/services").json()["error"]["code"] == "missing_key"
    assert c.get("/api/v1/services", headers=H("dfk_x.y")).json()["error"]["code"] == "invalid_key"
    raw, _ = key(now=time.time() - 40 * 86400, days=30)
    r = c.get("/api/v1/services", headers=H(raw))
    assert r.status_code == 401 and r.json()["error"]["code"] == "key_expired" and r.headers["www-authenticate"] == "Bearer"
    assert raw not in r.text


def test_the_key_may_come_as_bearer_or_x_api_key():
    c, _ = make()
    raw, _ = key()
    assert c.get("/api/v1/usage", headers=H(raw)).status_code == 200
    assert c.get("/api/v1/usage", headers={"X-API-Key": raw}).status_code == 200


def test_guessing_keys_is_slowed_down():
    c, _ = make()
    codes = [c.get("/api/v1/services", headers=H("dfk_a.b")).status_code for _ in range(25)]
    assert codes[:20] == [401] * 20 and set(codes[20:]) == {429}
    raw, _ = key()                                                  # a real key from the same address is slowed too, but only for the minute
    assert c.get("/api/v1/usage", headers=H(raw)).status_code == 200


def test_each_service_is_its_own_key():
    c, _ = make()
    raw, _ = key("rti")
    ids = [s["id"] for s in c.get("/api/v1/services", headers=H(raw)).json()["services"]]
    assert ids == ["rti_application"]
    r = c.post("/api/v1/rti_first_appeal/draft", headers=H(raw), json={"fields": {}})
    assert r.status_code == 403 and r.json()["error"]["code"] == "service_not_in_plan"
    assert c.post("/api/v1/grievance/draft", headers=H(raw), json={}).status_code == 403
    assert c.post("/api/v1/nope/draft", headers=H(raw), json={}).json()["error"]["code"] == "unknown_service"
    raw2, _ = key("rti,rti_appeal,samjhao,advisor,grievance,forms")
    ids = {s["id"] for s in c.get("/api/v1/services", headers=H(raw2)).json()["services"]}
    assert {"rti_application", "rti_first_appeal", "samjhao", "scheme_advisor", "grievance", "pm_kisan"} <= ids
    assert "land_record" not in ids and "ration_lookup" not in ids          # centre records are not sold through the API
    assert c.post("/api/v1/land_record/draft", headers=H(raw2), json={}).json()["error"]["code"] == "unknown_service"


def test_services_lists_every_field_a_platform_needs():
    c, _ = make()
    raw, _ = key("rti_appeal")
    s = c.get("/api/v1/services", headers=H(raw)).json()["services"][0]
    f = {x["id"]: x for x in s["fields"]}
    assert s["service"] == "rti_appeal" and "hi" in c.get("/api/v1/services", headers=H(raw)).json()["languages"]
    assert f["reply_status"]["options"] == ["No reply received", "Reply received, not satisfied"]
    assert f["reply_date"]["when"]["field"] == "reply_status" and f["rti_date"]["kind"] == "date" and f["rti_date"]["required"]


# ---- drafts -----------------------------------------------------------------------------------------------------
def test_an_rti_application_comes_back_drafted_and_in_hindi_too_when_asked():
    c, svc = make()
    raw, _ = key("rti")
    r = c.post("/api/v1/rti_application/draft", headers=H(raw), json={"lang": "en", "fields": RTI})
    j = r.json()
    assert r.status_code == 200 and j["complete"] is True and j["errors"] == {} and j["missing"] == []
    o = j["outcome"]
    assert o["type"] == "draft" and "Asha Verma" in o["drafts"]["en"] and "Roads (PWD)" in o["drafts"]["en"] or "PWD" in o["drafts"]["en"]
    assert not set(o) & b2b.INTERNAL and "say" not in o and "hi" not in o["drafts"]
    hi = c.post("/api/v1/rti_application/draft", headers=H(raw), json={"lang": "hi", "fields": RTI}).json()["outcome"]["drafts"]
    assert hi["en"] and hi["hi"] and "Asha Verma" in hi["hi"]                      # Hindi alongside, for a Hindi-speaking applicant
    assert j["values"]["pincode"] == "226001" and j["usage"]["remaining"] == 499 and j["values"]["bpl"] == "No"
    assert r.headers["cache-control"] == "no-store" and r.headers["x-quota-remaining"] == "499" and r.headers["x-key-expires"]
    assert svc.sessions == {}                                           # nothing was kept


def test_what_is_missing_and_what_is_wrong_is_said_field_by_field():
    c, _ = make()
    raw, _ = key("rti")
    bad = dict(RTI, mobile="12345", pincode="22")
    del bad["information"]
    bad["colour"] = "red"
    j = c.post("/api/v1/rti_application/draft", headers=H(raw), json={"fields": bad}).json()
    assert j["complete"] is False and "outcome" not in j
    assert j["missing"] == ["information"]
    assert set(j["errors"]) == {"mobile", "pincode", "colour"}
    assert j["errors"]["mobile"]["code"].startswith("mobile") and "10 digits" in j["errors"]["mobile"]["message"] or j["errors"]["mobile"]["message"]
    assert j["errors"]["colour"]["code"] == "unknown_field"
    assert "Say " not in json.dumps(j)                                   # no voice-app wording leaks into an API
    assert c.get("/api/v1/usage", headers=H(raw)).json()["remaining"] == 500       # an incomplete call costs nothing


def test_a_bad_aadhaar_fails_its_checksum_like_the_voice_app():
    c, _ = make()
    raw, _ = key("forms")
    from dwaniforms.schema import load_all
    f = {x.id: x for x in load_all()["pm_kisan"].fields}
    assert "aadhaar" in f
    j = c.post("/api/v1/pm_kisan/draft", headers=H(raw), json={"fields": {"aadhaar": "123456789012"}}).json()
    assert j["errors"]["aadhaar"]["code"] == "aadhaar_invalid"


def test_choices_dates_and_yes_no_in_plain_json_types():
    c, _ = make()
    raw, _ = key("rti_appeal")
    due = (clock.today() - dt.timedelta(days=40)).isoformat()
    fields = {"full_name": "Asha Verma", "address": "12 Gandhi Road, Lucknow", "pincode": "226001", "mobile": 9876543210,
              "department": "Roads (PWD)", "rti_date": due, "information": "Status of my road repair request.",
              "reply_status": "No reply received"}
    j = c.post("/api/v1/rti_first_appeal/draft", headers=H(raw), json={"lang": "hi", "fields": fields}).json()
    assert j["complete"], j
    assert j["outcome"]["type"] == "draft" and j["values"]["mobile"] == "9876543210"
    assert j["outcome"]["drafts"]["en"] and "Section 19(1)" in j["outcome"]["drafts"]["en"]


def test_a_field_that_does_not_apply_is_ignored_not_an_error():
    c, _ = make()
    raw, _ = key("rti_appeal")
    j = c.post("/api/v1/rti_first_appeal/draft", headers=H(raw),
               json={"fields": {"reply_status": "No reply received", "reply_date": "2026-01-01", "grounds": "x"}}).json()
    assert set(j["ignored"]) == {"reply_date", "grounds"} and "reply_date" not in j["errors"]


def test_samjhao_and_the_advisor_work_one_shot_too():
    c, _ = make()
    raw, _ = key("samjhao,advisor")
    j = c.post("/api/v1/samjhao/draft", headers=H(raw), json={"lang": "hi", "fields": {
        "paper": "Electricity bill", "due_date": (clock.today() + dt.timedelta(days=9)).isoformat(), "amount": "1850"}}).json()
    assert j["complete"] and j["outcome"]["type"] == "explain" and j["outcome"]["days"] == 9 and j["outcome"]["beware"]
    from dwaniforms.schema import load_all
    adv = load_all()["scheme_advisor"]
    assert c.post("/api/v1/scheme_advisor/draft", headers=H(raw), json={"fields": {}}).json()["missing"]


def test_a_plain_form_returns_its_values_for_the_platforms_own_use():
    c, _ = make()
    raw, _ = key("forms")
    from dwaniforms.schema import load_all
    t = load_all()["health_registration"]
    j = c.post("/api/v1/health_registration/draft", headers=H(raw), json={"fields": {}}).json()
    assert j["complete"] is False and set(j["missing"]) <= {f.id for f in t.fields} and j["missing"]


@pytest.mark.parametrize("body,status", [("not json", 422), ('{"lang": "english"}', 422), ('{"lang": "zz"}', 422),
                                         ('{"fields": [1]}', 422), ('{"fields": {"a": {"b": 1}}}', 200)])
def test_a_malformed_request_is_a_422_that_does_not_echo_the_body(body, status):
    c, _ = make()
    raw, _ = key("rti")
    r = c.post("/api/v1/rti_application/draft", headers={**H(raw), "Content-Type": "application/json"}, content=body)
    assert r.status_code == status and body.strip("{}\"") not in r.text.replace("fields", "") or status == 200


def test_a_huge_body_is_refused():
    c, _ = make()
    raw, _ = key("rti")
    r = c.post("/api/v1/rti_application/draft", headers=H(raw), json={"fields": {"information": "x" * 70_000}})
    assert r.status_code == 413 and r.json()["error"]["code"] == "payload_too_large"
    r = c.post("/api/v1/rti_application/draft", headers=H(raw), json={"fields": {f"f{i}": "1" for i in range(61)}})
    assert r.status_code == 422


# ---- the trial's limits ----------------------------------------------------------------------------------------
def test_the_quota_is_the_size_of_the_trial_and_only_finished_drafts_count():
    c, _ = make()
    raw, _ = key("rti", quota=2)
    for expect in (1, 0):
        r = c.post("/api/v1/rti_application/draft", headers=H(raw), json={"fields": RTI})
        assert r.json()["usage"]["remaining"] == expect
    r = c.post("/api/v1/rti_application/draft", headers=H(raw), json={"fields": RTI})
    assert r.status_code == 429 and r.json()["error"]["code"] == "quota_used"
    assert c.get("/api/v1/usage", headers=H(raw)).json()["remaining"] == 0


def test_requests_a_minute_are_limited_per_key_not_per_address():
    c, _ = make()
    a, _ = key("rti", rpm=3)
    b, _ = key("rti", rpm=3)
    assert [c.get("/api/v1/usage", headers=H(a)).status_code for _ in range(5)] == [200, 200, 200, 429, 429]
    r = c.get("/api/v1/usage", headers=H(a))
    assert r.json()["error"]["code"] == "rate_limited" and int(r.headers["retry-after"]) > 0
    assert c.get("/api/v1/usage", headers=H(b)).status_code == 200


def test_usage_says_what_the_trial_has_left():
    c, _ = make()
    raw, k = key("rti,rti_appeal", days=30, quota=50)
    u = c.get("/api/v1/usage", headers=H(raw)).json()
    assert u["organisation"] == "RTIwala" and u["quota"] == 50 and u["remaining"] == 50 and u["days_left"] in (29, 30)
    assert u["services"] == ["rti", "rti_appeal"] and u["expires"] == k.expires_on() and u["key_id"] == k.id


# ---- privacy ----------------------------------------------------------------------------------------------------
def test_no_personal_data_in_logs_and_the_usage_log_has_none(monkeypatch, caplog, tmp_path):
    path = tmp_path / "usage.jsonl"
    monkeypatch.setenv("DWANIFORMS_USAGE_LOG", str(path))
    c, _ = make(b2b.Meter())
    raw, k = key("rti")
    with caplog.at_level(logging.DEBUG):
        c.post("/api/v1/rti_application/draft", headers=H(raw), json={"fields": RTI})
        c.post("/api/v1/rti_application/draft", headers=H(raw), json={"fields": dict(RTI, mobile="1")})
    logged = caplog.text + path.read_text(encoding="utf-8")
    for pii in ("Asha Verma", "Gandhi Road", "9876543210", "226001", "road repair", raw):
        assert pii not in logged, pii
    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    assert [x["complete"] for x in lines] == [True, False] and lines[0]["key"] == k.id and lines[0]["service"] == "rti_application"
    assert set(lines[0]) == {"t", "key", "org", "service", "status", "complete"}


def test_a_crash_says_nothing_about_the_request(monkeypatch, caplog):
    c, _ = make()
    raw, _ = key("rti")

    async def boom(*a, **k):
        raise RuntimeError("Asha Verma 9876543210")
    monkeypatch.setattr(b2b, "run_draft", boom)
    with caplog.at_level(logging.DEBUG):
        r = c.post("/api/v1/rti_application/draft", headers=H(raw), json={"fields": RTI})
    assert r.status_code == 500 and r.json()["error"]["code"] == "server_error"
    assert "Asha" not in r.text and "9876543210" not in r.text and "Asha" not in caplog.text


def test_the_key_is_never_echoed_back():
    c, _ = make()
    raw, _ = key("rti")
    for r in (c.get("/api/v1/usage", headers=H(raw)), c.get("/api/v1/services", headers=H(raw)),
              c.post("/api/v1/rti_application/draft", headers=H(raw), json={"fields": RTI}),
              c.post("/api/v1/rti_first_appeal/draft", headers=H(raw), json={}), c.get("/api/v1/openapi.json")):
        assert raw not in r.text and raw not in json.dumps(dict(r.headers))


def test_the_description_lists_only_the_company_api():
    c, _ = make()
    paths = set(c.get("/api/v1/openapi.json").json()["paths"])
    assert paths == {"/services", "/usage", "/{service_id}/draft", "/names/match", "/address/parse", "/speech/parse"}
    assert c.get("/api/v1/docs").status_code == 404 and c.get("/api/v1/nothing").json()["error"]["code"] == "not_found"


def test_no_cors_so_a_browser_page_cannot_use_it():
    c, _ = make()
    raw, _ = key("rti")
    r = c.options("/api/v1/usage", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in r.headers


# ---- switching it on ------------------------------------------------------------------------------------------
def test_the_standalone_app_mounts_it_only_with_a_secret(monkeypatch):
    from dwaniforms.standalone import build_app
    c = TestClient(build_app(None, None, "x", True, False, False))
    raw, _ = key("rti")
    assert c.get("/api/v1/usage", headers=H(raw)).status_code == 200
    monkeypatch.delenv("DWANIFORMS_KEY_SECRET")
    c = TestClient(build_app(None, None, "x", True, False, False))
    assert c.get("/api/v1/usage", headers=H(raw)).status_code == 404
