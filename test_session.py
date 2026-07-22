"""
Tests for server.py: session-gated WebSocket auth, per-language broadcast
fan-out, and the /session-info and /health endpoints.

Each test gets a fresh `server.session` (fixed ID, so we don't need to know
a random one) and a cleared `server.subscribers` dict, since both are
module-level state shared across the app.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import server
from session import Session


@pytest.fixture(autouse=True)
def fresh_session_and_subscribers():
    """Give every test a known session id and empty subscriber sets."""
    server.session = Session(port=8000, session_id="testsession")
    server.subscribers.clear()
    yield
    server.subscribers.clear()


@pytest.fixture
def client():
    return TestClient(server.app)


# ---------------------------------------------------------------------------
# WebSocket auth (session id gate)
# ---------------------------------------------------------------------------

class TestAttendeeSocketAuth:
    def test_wrong_session_id_is_rejected(self, client):
        with pytest.raises(Exception):
            # starlette's TestClient raises when the server closes during handshake
            with client.websocket_connect("/ws?lang=en&session_param=wrongid"):
                pass

    def test_correct_session_id_is_accepted_and_registers_subscriber(self, client):
        with client.websocket_connect("/ws?lang=en&session_param=testsession"):
            assert "en" in server.subscribers
            assert len(server.subscribers["en"]) == 1

    def test_disconnect_removes_subscriber(self, client):
        with client.websocket_connect("/ws?lang=en&session_param=testsession"):
            assert len(server.subscribers["en"]) == 1
        # context manager exit disconnects; server should have pruned it
        assert len(server.subscribers["en"]) == 0

    def test_two_attendees_same_language_both_registered(self, client):
        with client.websocket_connect("/ws?lang=fr&session_param=testsession") as ws1:
            with client.websocket_connect("/ws?lang=fr&session_param=testsession") as ws2:
                assert len(server.subscribers["fr"]) == 2

    def test_different_languages_tracked_separately(self, client):
        with client.websocket_connect("/ws?lang=en&session_param=testsession"):
            with client.websocket_connect("/ws?lang=fr&session_param=testsession"):
                assert set(server.subscribers.keys()) == {"en", "fr"}
                assert len(server.subscribers["en"]) == 1
                assert len(server.subscribers["fr"]) == 1


# ---------------------------------------------------------------------------
# broadcast_caption
# ---------------------------------------------------------------------------

class TestBroadcastCaption:
    @pytest.mark.anyio
    async def test_sends_to_all_subscribers_of_the_language(self):
        ws_a, ws_b = AsyncMock(), AsyncMock()
        server.subscribers["en"] = {ws_a, ws_b}

        await server.broadcast_caption("en", "hello", is_final=True)

        ws_a.send_json.assert_awaited_once_with({"text": "hello", "final": True})
        ws_b.send_json.assert_awaited_once_with({"text": "hello", "final": True})

    @pytest.mark.anyio
    async def test_does_not_send_to_other_languages(self):
        ws_en, ws_fr = AsyncMock(), AsyncMock()
        server.subscribers["en"] = {ws_en}
        server.subscribers["fr"] = {ws_fr}

        await server.broadcast_caption("en", "hello", is_final=True)

        ws_en.send_json.assert_awaited_once()
        ws_fr.send_json.assert_not_awaited()

    @pytest.mark.anyio
    async def test_no_subscribers_is_a_noop(self):
        # should not raise even though "es" was never joined
        await server.broadcast_caption("es", "hola", is_final=True)

    @pytest.mark.anyio
    async def test_dead_socket_is_pruned_without_blocking_other_sends(self):
        dead = AsyncMock()
        dead.send_json.side_effect = Exception("connection reset")
        alive = AsyncMock()
        server.subscribers["en"] = {dead, alive}

        await server.broadcast_caption("en", "hello", is_final=True)

        alive.send_json.assert_awaited_once()
        assert dead not in server.subscribers["en"]
        assert alive in server.subscribers["en"]

    @pytest.mark.anyio
    async def test_default_is_final_true(self):
        ws = AsyncMock()
        server.subscribers["en"] = {ws}

        await server.broadcast_caption("en", "partial text")

        ws.send_json.assert_awaited_once_with({"text": "partial text", "final": True})


# ---------------------------------------------------------------------------
# HTTP endpoints
# ---------------------------------------------------------------------------

class TestHttpEndpoints:
    def test_session_info_reports_session_id_and_languages(self, client):
        server.subscribers["en"] = {MagicMock()}
        server.subscribers["fr"] = {MagicMock()}

        resp = client.get("/session-info")

        assert resp.status_code == 200
        body = resp.json()
        assert body["session_id"] == "testsession"
        assert set(body["languages_available"]) == {"en", "fr"}

    def test_session_info_empty_when_no_subscribers(self, client):
        resp = client.get("/session-info")
        assert resp.json() == {"session_id": "testsession", "languages_available": []}

    def test_health_reports_status_and_counts(self, client):
        server.subscribers["en"] = {MagicMock(), MagicMock()}
        server.subscribers["fr"] = {MagicMock()}

        resp = client.get("/health")

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["session_id"] == "testsession"
        assert body["connected_attendees"] == 3

    def test_health_zero_attendees(self, client):
        resp = client.get("/health")
        assert resp.json()["connected_attendees"] == 0