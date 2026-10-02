"""Session notes: recording, highlights, key terms, exports, server endpoints."""
import asyncio
import json
import re
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import notes as N  # noqa: E402

LECTURE = [
    "Good morning everyone, welcome to today's class.",
    "Today we will learn how gradient descent trains a neural network.",
    "Okay.",
    "A neural network makes a prediction and we measure the error with a loss function.",
    "Gradient descent changes each weight a little in the direction that reduces the loss function.",
    "The learning rate decides how big each step of gradient descent is.",
    "If the learning rate is too large, gradient descent overshoots and the loss function goes up.",
    "Yes, that is right.",
    "If the learning rate is too small, training the neural network becomes very slow.",
    "In practice we use mini batches, so gradient descent sees a few examples at a time.",
    "This version is called stochastic gradient descent.",
    "Let us take a short break.",
    "After the break we will write gradient descent for a small neural network in Python.",
]


def make_session(tmp_path, lang="en", lines=LECTURE):
    rec = N.NotesRecorder(tmp_path, "abc123", lang)
    for i, t in enumerate(lines):
        rec.record(t, {"hi": f"[hi] {t}"}, rec.started + 5 * (i + 1), 3.5)
    return rec, N.load_session(rec.path)


def test_recorder_and_crash_safety(tmp_path):
    rec, s = make_session(tmp_path)
    assert rec.count == len(LECTURE) and len(s.captions) == len(LECTURE)
    assert s.captions[1].tr == {"hi": "[hi] " + LECTURE[1]} and s.languages() == ["en", "hi"]
    assert abs(s.captions[0].start - 1.5) < 0.01 and abs(s.captions[0].end - 5) < 0.01
    with open(rec.path, "a", encoding="utf-8") as f:
        f.write('{"type": "cap", "t": 1, "sta')            # power cut mid-write
    assert len(N.load_session(rec.path).captions) == len(LECTURE)
    rec.enabled = False
    rec.record("not saved", {}, rec.started + 99, 1)
    rec.record("   ", {}, rec.started + 99, 1)
    assert len(N.load_session(rec.path).captions) == len(LECTURE)


def test_highlights_pick_the_core_of_the_talk(tmp_path):
    _, s = make_session(tmp_path)
    idx = N.highlights(s.captions, k=4)
    picked = [s.captions[i].src for i in idx]
    assert idx == sorted(idx)                                   # talk order
    assert all("gradient descent" in p or "neural network" in p or "learning rate" in p for p in picked)
    assert not any(p in ("Okay.", "Yes, that is right.", "Let us take a short break.") for p in picked)


def test_key_terms(tmp_path):
    _, s = make_session(tmp_path)
    terms = N.key_terms(s.captions)
    assert terms[0] == "gradient descent"
    for t in ("neural network", "learning rate", "loss function"):
        assert t in terms
    assert "descent" not in terms and "the" not in terms
    assert not {"break", "each", "small"} & set(terms)             # one-off words aren't key terms


def test_exports(tmp_path):
    rec, s = make_session(tmp_path)
    txt = N.export_txt(s, "en")
    assert "[00:01] Good morning" in txt
    srt = N.export_srt(s, "hi")
    cues = re.findall(r"(\d\d:\d\d:\d\d,\d{3}) --> (\d\d:\d\d:\d\d,\d{3})\n(.+)", srt)
    assert len(cues) == len(LECTURE) and cues[0][2].startswith("[hi] ")
    sec = lambda t: int(t[:2]) * 3600 + int(t[3:5]) * 60 + int(t[6:8]) + int(t[9:]) / 1000
    for (a, b, _), (c, _, _) in zip(cues, cues[1:]):
        assert sec(b) - sec(a) >= 1.0 and sec(b) <= sec(c)        # readable and never overlapping
    md = N.export_md(s, "en")
    assert "## Highlights" in md and "## Key terms" in md and "gradient descent" in md
    html = N.export_html(s, "en")
    assert "Save as PDF" in html and "Highlights" in html and 'dir="ltr"' in html


def test_html_escapes_and_rtl(tmp_path):
    rec = N.NotesRecorder(tmp_path, "x1", "ur")
    rec.record('<script>alert(1)</script> سلام', {}, rec.started + 3, 2)
    html = N.export_html(N.load_session(rec.path), "ur")
    assert "<script>alert" not in html and "&lt;script&gt;" in html and 'dir="rtl"' in html


def test_on_demand_translation_is_saved(tmp_path):
    rec, s = make_session(tmp_path)
    calls = []

    async def tr(text, lang):
        calls.append(text)
        return f"[{lang}] {text}"
    assert asyncio.run(N.ensure_language(s, "ta", tr)) == len(LECTURE)
    again = N.load_session(rec.path)
    assert again.captions[3].tr["ta"].startswith("[ta] ") and "ta" in again.languages()
    assert asyncio.run(N.ensure_language(again, "ta", tr)) == 0 and len(calls) == len(LECTURE)


def test_safe_session_path(tmp_path):
    rec, _ = make_session(tmp_path)
    assert N.safe_session_path(tmp_path, rec.path.stem) == rec.path
    for bad in ("../secret", "a/b", "", "x" * 200, "..\\x"):
        assert N.safe_session_path(tmp_path, bad) is None


def test_pipeline_hook_records_even_with_nobody_listening():
    from pipeline import AudioSegment, Pipeline

    class ASR:
        async def transcribe(self, seg):
            return "hello class"

    class MT:
        async def translate(self, text, lang):
            return f"{lang}:{text}"

    got = []
    async def bc(*a):
        pass
    langs = []
    p = Pipeline(asr=ASR(), translator=MT(), broadcast=bc, subscribed_languages=lambda: langs)
    p.on_final = lambda *a: got.append(a)
    seg = AudioSegment(samples=np.zeros(32000, dtype=np.float32), sample_rate=16000, start_ts=0, end_ts=2.5)
    asyncio.run(p._process_segment(seg, is_final=True))
    asyncio.run(p._process_segment(seg, is_final=False))          # interim: not recorded
    langs += ["hi", "ta"]
    asyncio.run(p._process_segment(seg, is_final=True))
    assert len(got) == 2 and got[0][1] == {} and got[1][1] == {"hi": "hi:hello class", "ta": "ta:hello class"}
    assert abs(got[1][3] - 2.0) < 1e-6                              # speech length from the samples


def test_server_endpoints(tmp_path, monkeypatch):
    import phone_mic
    import server
    from fastapi.testclient import TestClient

    monkeypatch.setenv("DWANI_NOTES_DIR", str(tmp_path))
    rec, s = make_session(tmp_path)
    monkeypatch.setattr(server, "notes_recorder", rec)
    c = TestClient(server.app)
    st = c.get("/notes/status").json()
    assert st == {"available": True, "recording": True, "captions": len(LECTURE), "id": rec.path.stem}
    assert c.post("/notes/recording?on=false").json() == {"recording": False}
    sessions = c.get("/notes/sessions").json()
    assert sessions[0]["id"] == rec.path.stem and sessions[0]["languages"] == ["en", "hi"]
    r = c.get(f"/notes/export?id={rec.path.stem}&format=notes")
    assert r.status_code == 200 and "Highlights" in r.text
    r = c.get(f"/notes/export?id={rec.path.stem}&lang=hi&format=srt")
    assert r.headers["content-disposition"].endswith('.srt"') and "[hi] Good morning" in r.text
    r = c.get(f"/notes/export?id={rec.path.stem}&lang=ta&format=txt")   # not live: translated now (demo translator)
    assert r.status_code == 200 and len(r.text.splitlines()) >= len(LECTURE)
    assert "ta" in c.get("/notes/sessions").json()[0]["languages"]       # saved for next time
    for bad in ("../x", "nope"):
        assert c.get(f"/notes/export?id={bad}&format=txt").status_code == 404
    assert c.get(f"/notes/export?id={rec.path.stem}&format=exe").status_code == 404
    assert c.get(f"/notes/export?id={rec.path.stem}&lang=../../x&format=txt").status_code == 400
    remote = TestClient(server.app, client=("192.168.1.9", 4000))
    assert remote.get("/notes/sessions").status_code == 404
    assert remote.get(f"/notes/sessions?key={phone_mic.PRESENTER_KEY}").status_code == 200
