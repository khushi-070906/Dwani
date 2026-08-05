"""
Tests for server.py's /host-ws endpoint -- the presenter mic stream added
alongside the attendee-facing /ws. Covers the same session-id auth gate as
/ws, the single-presenter guard, and that bytes sent over the socket really
do flow through `pipeline` and reach `broadcast_caption`.

Requires fastapi/pytest/anyio/httpx, same as test_session.py.
"""

from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient

import server
from pipeline import AudioSegmenter, FakeASRBackend, FakeTranslationBackend, Pipeline, make_silence, make_tone
from session import Session


@pytest.fixture(autouse=True)
def fresh_state():
    """Give every test a known session id, empty subscribers, a cleared
    host_connected flag, and a fresh Pipeline with fast-closing timing so
    tests don't need many seconds of synthetic audio to trigger a segment."""
    server.session = Session(port=8000, session_id="testsession")
    server.subscribers.clear()
    server.host_connected = False
    server.pipeline = Pipeline(
        FakeASRBackend(default_transcript="hello from the host"),
        FakeTranslationBackend(),
        server.broadcast_caption,
        lambda: server.subscribers.keys(),
        segmenter=AudioSegmenter(min_voiced_seconds=0.1, min_silence_seconds=0.2),
    )
    yield
    server.subscribers.clear()
    server.host_connected = False


@pytest.fixture
def client():
    return TestClient(server.app)


class TestHostSocketAuth:
    def test_wrong_session_id_is_rejected(self, client):
        with pytest.raises(Exception):
            with client.websocket_connect("/host-ws?session_param=wrongid"):
                pass
        assert server.host_connected is False

    def test_correct_session_id_is_accepted(self, client):
        with client.websocket_connect("/host-ws?session_param=testsession"):
            assert server.host_connected is True
        assert server.host_connected is False  # cleared once the presenter disconnects

    def test_second_presenter_while_one_connected_is_rejected(self, client):
        with client.websocket_connect("/host-ws?session_param=testsession"):
            with pytest.raises(Exception):
                with client.websocket_connect("/host-ws?session_param=testsession"):
                    pass

    def test_presenter_slot_reopens_after_disconnect(self, client):
        with client.websocket_connect("/host-ws?session_param=testsession"):
            pass
        # should succeed now that the first presenter has left
        with client.websocket_connect("/host-ws?session_param=testsession"):
            assert server.host_connected is True


class TestHostAudioStreaming:
    def test_streamed_audio_reaches_pipeline_and_gets_broadcast(self, client):
        received = []

        async def capturing_broadcast(lang, text, is_final):
            received.append((lang, text, is_final))

        server.pipeline = Pipeline(
            FakeASRBackend(default_transcript="hello there"),
            FakeTranslationBackend(),
            capturing_broadcast,
            lambda: ["hi"],
            segmenter=AudioSegmenter(min_voiced_seconds=0.1, min_silence_seconds=0.2),
        )

        with client.websocket_connect("/host-ws?session_param=testsession") as ws:
            # enough voiced audio, then enough silence, to close a segment --
            # mirrors the timings test_pipeline.py already exercises directly
            for _ in range(3):
                ws.send_bytes(make_tone(0.1).tobytes())
            ws.send_bytes(make_silence(0.3).tobytes())

        assert ("hi", "[hi] hello there", True) in received

    def test_no_subscribers_streams_audio_without_error(self, client):
        # subscribed_languages() returning nothing shouldn't raise -- same
        # no-op contract Pipeline already guarantees in test_pipeline.py
        with client.websocket_connect("/host-ws?session_param=testsession") as ws:
            for _ in range(3):
                ws.send_bytes(make_tone(0.1).tobytes())
            ws.send_bytes(make_silence(0.3).tobytes())
        # reaching here without an exception is the assertion

    def test_disconnect_flushes_trailing_audio(self, client):
        received = []

        async def capturing_broadcast(lang, text, is_final):
            received.append((lang, text, is_final))

        server.pipeline = Pipeline(
            FakeASRBackend(default_transcript="closing remarks"),
            FakeTranslationBackend(),
            capturing_broadcast,
            lambda: ["hi"],
            segmenter=AudioSegmenter(min_voiced_seconds=0.1),
        )

        with client.websocket_connect("/host-ws?session_param=testsession") as ws:
            # voiced audio with no trailing silence -- only flush() on
            # disconnect should close this out, not the streaming loop itself
            ws.send_bytes(make_tone(0.3).tobytes())

        assert ("hi", "[hi] closing remarks", True) in received