"""Presenter dashboard: caption-delay measurement and per-language audience."""
import asyncio
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import AudioSegment, LatencyStats, Pipeline  # noqa: E402


def test_latency_stats_summary():
    s = LatencyStats(keep=5)
    assert s.summary() == {"count": 0, "captions_sent": 0}
    for ms in (900, 1100, 1000, 4000, 1200, 1300):   # 6 recorded, only last 5 kept
        s.record(asr_ms=ms * 0.6, mt_ms=ms * 0.3, total_ms=ms, audio_s=3.0, languages=2)
    out = s.summary()
    assert out["count"] == 5 and out["captions_sent"] == 6
    assert out["p50_ms"] == 1200 and out["p95_ms"] == 4000 and out["last_ms"] == 1300


class SlowASR:
    async def transcribe(self, segment):
        await asyncio.sleep(0.05)
        return "hello"


class SlowMT:
    async def translate(self, text, lang):
        await asyncio.sleep(0.08 if lang == "ta" else 0.02)
        return f"{text}-{lang}"


def test_pipeline_measures_final_captions_only():
    sent = []

    async def broadcast(lang, text, final):
        sent.append((lang, text, final))

    p = Pipeline(asr=SlowASR(), translator=SlowMT(), broadcast=broadcast, subscribed_languages=lambda: ["hi", "ta"])
    seg = AudioSegment(samples=np.zeros(16000, dtype=np.float32), sample_rate=16000, start_ts=0.0, end_ts=1.0)
    asyncio.run(p._process_segment(seg, is_final=False))
    assert getattr(p, "latency", None) is None or p.latency.summary()["count"] == 0   # interim not counted
    asyncio.run(p._process_segment(seg, is_final=True))
    out = p.latency.summary()
    assert out["count"] == 1 and out["asr_avg_ms"] >= 45
    assert out["mt_avg_ms"] >= 75          # slowest language (ta) is what the attendee waits for
    assert out["last_ms"] >= out["asr_avg_ms"] + 70
    assert len(sent) == 4


def test_dashboard_endpoint():
    import phone_mic
    import server
    from fastapi.testclient import TestClient

    c = TestClient(server.app)
    sid = server.session.session_id
    with c.websocket_connect(f"/ws?lang=hi&session_param={sid}"), \
         c.websocket_connect(f"/ws?lang=hi&session_param={sid}"), \
         c.websocket_connect(f"/ws?lang=ta&session_param={sid}"):
        d = c.get("/dashboard-stats").json()
        assert d["attendees"] == 3 and d["by_language"] == {"hi": 2, "ta": 1}
        assert list(d["by_language"]) == ["hi", "ta"]   # biggest first
        assert "captions_sent" in d["latency"]
    assert TestClient(server.app, client=("10.0.0.9", 5000)).get("/dashboard-stats").status_code == 404
    assert TestClient(server.app, client=("10.0.0.9", 5000)).get(f"/dashboard-stats?key={phone_mic.PRESENTER_KEY}").status_code == 200
    assert c.get("/session-info").json().keys() == {"session_id", "languages_available"}   # old shape unchanged
