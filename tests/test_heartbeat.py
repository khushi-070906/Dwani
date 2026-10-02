"""Attendee heartbeat: the server answers pings, so phones can detect dead connections."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_ping_gets_pong_and_captions_still_flow():
    import server
    from fastapi.testclient import TestClient

    c = TestClient(server.app)
    sid = server.session.session_id
    with c.websocket_connect(f"/ws?lang=hi&session_param={sid}") as ws:
        ws.send_json({"type": "ping", "t": 123})
        assert ws.receive_json() == {"type": "pong", "t": 123}
        ws.send_text("not json")                       # ignored, connection stays up
        ws.send_json({"type": "settings", "accessibility": {}})
        ws.send_json({"type": "ping", "t": 456})
        assert ws.receive_json() == {"type": "pong", "t": 456}


def test_attendee_page_has_reconnect_features():
    html = (Path(__file__).resolve().parent.parent / "static" / "index.html").read_text(encoding="utf-8")
    for needle in ("startHeartbeat", "forceReconnect", "navigator.connection", "dwani:last", "save-btn", "Reconnecting in "):
        assert needle in html
