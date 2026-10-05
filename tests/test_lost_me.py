"""'Lost me' taps: anonymous, rate-limited, shown to the presenter, kept in session notes."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_taps_counted_rate_limited_and_summarised(monkeypatch):
    import server
    from fastapi.testclient import TestClient

    server.confused_events.clear()
    server.record_final("Gradient descent changes each weight a little.", {}, time.time(), 2.0)
    c = TestClient(server.app)
    sid = server.session.session_id
    with c.websocket_connect(f"/ws?lang=hi&session_param={sid}") as a, \
         c.websocket_connect(f"/ws?lang=hi&session_param={sid}") as b, \
         c.websocket_connect(f"/ws?lang=ta&session_param={sid}") as d:
        a.send_json({"type": "confused"})
        assert a.receive_json() == {"type": "confused_ack", "counted": True}
        a.send_json({"type": "confused"})                       # within 20 s: not counted again
        assert a.receive_json() == {"type": "confused_ack", "counted": False}
        conf = c.get("/dashboard-stats").json()["confusion"]
        assert conf["people"] == 1 and conf["attendees"] == 3 and conf["alert"] is False   # 1 person is not an alert
        b.send_json({"type": "confused"}); b.receive_json()
        conf = c.get("/dashboard-stats").json()["confusion"]
        assert conf["people"] == 2 and conf["alert"] is True and "Gradient descent" in conf["about"]
        assert conf["total"] == 2
        d.send_json({"type": "ping", "t": 1})
        assert d.receive_json()["type"] == "pong"                # other traffic unaffected
    assert server._last_confused == {}                           # forgotten when phones leave
    later = server.confusion_summary(attendees=3, now=time.time() + 120)
    assert later["people"] == 0 and later["alert"] is False      # only the last minute counts


def test_notes_show_where_students_got_lost(tmp_path):
    import notes as N
    rec = N.NotesRecorder(tmp_path, "s1", "en")
    lines = ["Welcome to the class.", "Backpropagation applies the chain rule layer by layer.", "Let us take a break."]
    for i, t in enumerate(lines):
        rec.record(t, {}, rec.started + 10 * (i + 1), 4)
    for dt in (19.0, 20.5, 21.0):                                # three taps while sentence 2 was ending
        rec.mark_confused(rec.started + dt, "hi")
    s = N.load_session(rec.path)
    moments = N.lost_moments(s)
    assert [(c.src, n) for c, n in moments] == [(lines[1], 3)]
    html = N.export_html(s, "en")
    assert "Where students got lost" in html and "3 taps" in html
    assert "## Where students got lost" in N.export_md(s, "en")
    rec2 = N.NotesRecorder(tmp_path, "s2", "en")
    rec2.record("Hello.", {}, rec2.started + 3, 1)
    assert "got lost" not in N.export_html(N.load_session(rec2.path), "en")   # section only when there were taps
