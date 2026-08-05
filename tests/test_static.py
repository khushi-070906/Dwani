"""
Tests for server.py's static asset endpoints -- the attendee and presenter
HTML pages, the QR image, and /presenter-info. test_session.py already
covers /session-info and /health; this file covers what it doesn't.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import server
from session import Session


@pytest.fixture(autouse=True)
def fresh_session(tmp_path):
    """Known session id, and a real QR image on disk at a throwaway path so
    /qr.png has something to serve without touching the repo's own
    session_qr.png (Session.__post_init__ only sets the *path*; nothing
    generates the file until generate_qr()/announce() is called)."""
    server.session = Session(port=8000, session_id="testsession")
    server.session.qr_image_path = tmp_path / "session_qr.png"
    server.session.generate_qr(server.session.primary_url())
    server.subscribers.clear()
    yield
    server.subscribers.clear()


@pytest.fixture
def client():
    return TestClient(server.app)


class TestStaticPages:
    def test_index_serves_attendee_page(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        # sanity check it's actually index.html and not e.g. host.html
        assert "Live Captions" in resp.text

    def test_host_serves_presenter_page(self, client):
        resp = client.get("/host")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "Presenter mic" in resp.text

    def test_qr_image_is_served_as_png(self, client):
        resp = client.get("/qr.png")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"
        assert len(resp.content) > 0


class TestPresenterInfo:
    def test_presenter_info_reports_session_and_join_url(self, client):
        resp = client.get("/presenter-info")
        assert resp.status_code == 200
        body = resp.json()
        assert body["session_id"] == "testsession"
        assert body["qr_url"] == "/qr.png"
        assert "session=testsession" in body["join_url"]

    def test_presenter_info_does_not_change_session_info_shape(self, client):
        # /session-info's response shape is asserted exactly in
        # test_session.py -- confirm /presenter-info's addition didn't leak
        # extra keys into it or vice versa.
        resp = client.get("/session-info")
        assert set(resp.json().keys()) == {"session_id", "languages_available"}