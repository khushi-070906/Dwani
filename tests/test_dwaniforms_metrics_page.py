"""The pilot numbers as a page (/form/metrics/view): same counters as /form/metrics, readable on a phone.

Two things these tests guard. First, the page and the numbers stay behind DWANIFORMS_METRICS_TOKEN, and the token
travels in a header, never in a URL. Second, nothing a citizen said can reach it: the counters and the labels are
template metadata only."""
import asyncio
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dwaniforms import records
from dwaniforms.api import create_router
from dwaniforms.hardening import install
from dwaniforms.service import FormService

PAGE = (Path(__file__).resolve().parents[1] / "dwaniforms" / "static" / "metrics.html").read_text(encoding="utf-8")
TOKEN = "s3cret-pilot-token"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DWANIFORMS_METRICS_TOKEN", TOKEN)
    monkeypatch.setenv("DWANIFORMS_RATE_LIMIT", "0")
    svc = FormService(None, None, record_store=records.RecordStore(records.write_demo(tmp_path, as_of="2026-09-01")))
    app = FastAPI()
    app.include_router(create_router(svc))
    install(app, svc, version="test")
    return TestClient(app)


def test_the_page_needs_a_token_to_be_configured(tmp_path, monkeypatch):
    monkeypatch.delenv("DWANIFORMS_METRICS_TOKEN", raising=False)
    svc = FormService(None, None, record_store=records.RecordStore(records.write_demo(tmp_path, as_of="2026-09-01")))
    app = FastAPI()
    app.include_router(create_router(svc))
    install(app, svc)
    c = TestClient(app)
    assert c.get("/form/metrics/view").status_code == 404      # no token set up: the page does not exist at all
    assert c.get("/form/metrics").status_code == 404


def test_the_page_itself_holds_no_numbers(client):
    r = client.get("/form/metrics/view")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert r.headers["cache-control"] == "no-store"
    assert TOKEN not in r.text
    assert "x-metrics-token" in r.text                          # the token goes in a header...
    assert "?token" not in r.text and "token=" not in r.text    # ...never in the address bar
    assert "sessionStorage" in r.text and "localStorage" not in r.text   # this tab only
    assert 'name="robots" content="noindex, nofollow"' in r.text


def test_numbers_still_need_the_token(client):
    assert client.get("/form/metrics").status_code == 404
    assert client.get("/form/metrics", headers={"x-metrics-token": "wrong"}).status_code == 404
    assert client.get("/form/metrics", headers={"x-metrics-token": TOKEN}).status_code == 200


def _use_it(client):
    """One citizen finishes a ration lookup, another starts a complaint, answers badly once, and leaves."""
    s = client.post("/form/sessions", json={"form_id": "ration_lookup", "lang": "hi"}).json()
    client.post(f"/form/sessions/{s['session_id']}/text", json={"text": "123456789012"})
    client.post(f"/form/sessions/{s['session_id']}/text", json={"text": "हाँ"})
    g = client.post("/form/sessions", json={"form_id": "grievance", "lang": "hi"}).json()
    client.post(f"/form/sessions/{g['session_id']}/text", json={"text": "x"})        # too short: counted as a retry
    client.delete(f"/form/sessions/{g['session_id']}")                               # left before finishing


def test_the_counters_a_pilot_report_needs(client):
    _use_it(client)
    d = client.get("/form/metrics", headers={"x-metrics-token": TOKEN}).json()
    assert d["totals"]["started"] == 2 and d["totals"]["finished"] == 1
    assert d["totals"]["completion_rate"] == 0.5
    assert d["started"]["ration_lookup"] == 1 and d["finished"]["ration_lookup"] == 1
    assert any(k.startswith("grievance:") for k in d["abandoned_at"]), d["abandoned_at"]
    assert any(k.startswith("grievance:") for k in d["retries"]), d["retries"]


def test_keys_come_with_readable_names(client):
    _use_it(client)
    d = client.get("/form/metrics", headers={"x-metrics-token": TOKEN}).json()
    labels = d["labels"]
    assert labels["ration_lookup"] and labels["pm_kisan"] == "PM-Kisan application"
    assert labels["pm_kisan:aadhaar"] == "Aadhaar number"
    for key in list(d["abandoned_at"]) + list(d["retries"]) + list(d["started"]):
        assert key in labels, key                               # every counter the page shows can be named


def test_nothing_anyone_said_is_in_the_numbers(client):
    name, aadhaar = "सुनीता देवी", "234567890124"
    s = client.post("/form/sessions", json={"form_id": "grievance", "lang": "hi"}).json()["session_id"]
    client.post(f"/form/sessions/{s}/text", json={"text": name})
    client.post(f"/form/sessions/{s}/text", json={"text": "हाँ"})
    client.post(f"/form/sessions/{s}/text", json={"text": aadhaar})
    body = client.get("/form/metrics", headers={"x-metrics-token": TOKEN}).text
    assert name not in body and aadhaar not in body and "98765" not in body
    assert not re.search(r"\d{6,}", body), "no long digit strings anywhere in the counters"
