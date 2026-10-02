"""Rejections must reach the browser with their code + reason (accept, then close)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_unknown_session_reaches_browser_as_4000():
    import server
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
    with TestClient(server.app).websocket_connect("/ws?lang=hi&session_param=wrong") as ws:
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
    assert exc.value.code == 4000


def test_attendee_page_handles_these_codes():
    html = (Path(__file__).resolve().parent.parent / "static" / "index.html").read_text(encoding="utf-8")
    assert "event.code === 4000" in html and "event.code === 4003" in html
