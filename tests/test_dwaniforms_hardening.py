"""DwaniForms production hardening: rate limits, security headers, health, anonymous metrics, no personal data in logs."""
import json
import logging

import pytest

TestClient = pytest.importorskip("fastapi.testclient").TestClient
from fastapi import FastAPI  # noqa: E402

from dwaniforms import hardening  # noqa: E402
from dwaniforms.api import create_router  # noqa: E402
from dwaniforms.service import FormService  # noqa: E402
from dwaniforms.validators import verhoeff_append  # noqa: E402


def make_app(monkeypatch, limits=None, token=None, trust_proxy=False):
    if limits:
        monkeypatch.setattr(hardening, "LIMITS", limits)
    monkeypatch.setenv("DWANIFORMS_RATE_LIMIT", "1")
    if token:
        monkeypatch.setenv("DWANIFORMS_METRICS_TOKEN", token)
    else:
        monkeypatch.delenv("DWANIFORMS_METRICS_TOKEN", raising=False)
    if trust_proxy:
        monkeypatch.setenv("DWANIFORMS_TRUST_PROXY", "1")
    else:
        monkeypatch.delenv("DWANIFORMS_TRUST_PROXY", raising=False)
    svc = FormService(None, None)
    app = FastAPI()
    app.include_router(create_router(svc))
    hardening.install(app, svc, version="test")
    return TestClient(app), svc


def test_security_headers(monkeypatch):
    c, _ = make_app(monkeypatch)
    r = c.get("/form/")
    h = r.headers
    assert "frame-ancestors 'none'" in h["content-security-policy"] and h["x-frame-options"] == "DENY"
    assert h["x-content-type-options"] == "nosniff" and h["referrer-policy"] == "no-referrer"
    assert "microphone=(self)" in h["permissions-policy"]
    assert "strict-transport-security" not in h                         # plain http: no HSTS
    r = c.get("/form/forms", headers={"x-forwarded-proto": "https"})
    assert "max-age" in r.headers["strict-transport-security"]
    sid = c.post("/form/sessions", json={"form_id": "pm_kisan", "lang": "hi"}).json()["session_id"]
    assert c.get(f"/form/sessions/{sid}").headers["cache-control"] == "no-store"   # answers never cached


def test_rate_limits(monkeypatch):
    c, _ = make_app(monkeypatch, limits={"new_session": (3, 600), "api": (1000, 60)})
    codes = [c.post("/form/sessions", json={"form_id": "pm_kisan", "lang": "hi"}).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    r = c.post("/form/sessions", json={"form_id": "pm_kisan", "lang": "hi"})
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0 and "wait" in r.json()["detail"]
    assert c.get("/form/health").status_code == 200                      # health is never limited


def test_proxy_ip_only_when_trusted(monkeypatch):
    lim = {"new_session": (1, 600), "api": (1000, 60)}
    c, _ = make_app(monkeypatch, limits=lim, trust_proxy=True)
    a = c.post("/form/sessions", json={"form_id": "pm_kisan", "lang": "hi"}, headers={"x-forwarded-for": "1.1.1.1"})
    b = c.post("/form/sessions", json={"form_id": "pm_kisan", "lang": "hi"}, headers={"x-forwarded-for": "2.2.2.2"})
    assert a.status_code == b.status_code == 200                         # two real clients behind the proxy
    c2, _ = make_app(monkeypatch, limits=lim, trust_proxy=False)
    c2.post("/form/sessions", json={"form_id": "pm_kisan", "lang": "hi"}, headers={"x-forwarded-for": "1.1.1.1"})
    r = c2.post("/form/sessions", json={"form_id": "pm_kisan", "lang": "hi"}, headers={"x-forwarded-for": "9.9.9.9"})
    assert r.status_code == 429                                          # a forged header can't dodge the limit


def test_health_and_metrics_carry_no_personal_data(monkeypatch):
    c, svc = make_app(monkeypatch, token="s3cret")
    h = c.get("/form/health").json()
    assert h["status"] == "ok" and set(h) == {"status", "version", "uptime_s", "open_sessions"}
    assert c.get("/form/metrics").status_code == 404 and c.get("/form/metrics", headers={"x-metrics-token": "no"}).status_code == 404
    sid = c.post("/form/sessions", json={"form_id": "ration_lookup", "lang": "en"}).json()["session_id"]
    c.post(f"/form/sessions/{sid}/text", json={"text": "12"})                # wrong -> a retry is counted
    c.post(f"/form/sessions/{sid}/text", json={"text": "1234 5678 9012"})
    c.post(f"/form/sessions/{sid}/text", json={"text": "yes"})
    m = c.get("/form/metrics", headers={"x-metrics-token": "s3cret"}).json()
    assert m["totals"]["started"] == 1 and m["totals"]["finished"] == 1
    blob = json.dumps(m)
    assert "123456789012" not in blob and "1234" not in blob                 # ids and counts only


def test_abandoned_question_is_counted(monkeypatch):
    c, svc = make_app(monkeypatch, token="t")
    sid = c.post("/form/sessions", json={"form_id": "pm_kisan", "lang": "hi"}).json()["session_id"]
    svc.sessions[sid].last_active -= 10_000                               # gone quiet
    svc.purge_expired()
    m = c.get("/form/metrics", headers={"x-metrics-token": "t"}).json()
    assert m["abandoned_at"] == {"pm_kisan:full_name": 1}


def test_no_personal_data_in_logs(monkeypatch, caplog, capsys):
    c, _ = make_app(monkeypatch)
    aadhaar = verhoeff_append("23456789012")
    caplog.set_level(logging.DEBUG)
    sid = c.post("/form/sessions", json={"form_id": "pm_kisan", "lang": "hi"}).json()["session_id"]
    for t in ("Ravindra Kumar Singhania", "हाँ", "Shyam Lal", "हाँ", "15 August 1980", "हाँ", "पुरुष", "ओबीसी", "98765 43210", "हाँ",
              " ".join(aadhaar), "हाँ"):
        c.post(f"/form/sessions/{sid}/text", json={"text": t})
    out = capsys.readouterr()
    logs = caplog.text + out.out + out.err
    for secret in (aadhaar, " ".join(aadhaar), "Singhania", "9876543210", "98765 43210"):
        assert secret not in logs, secret
