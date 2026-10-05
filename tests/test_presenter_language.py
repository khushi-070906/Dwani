"""The presenter picks the language they speak (and can switch mid-session)."""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backends import RealNLLBBackend, RealWhisperBackend, UnsupportedLanguageError, flores_code  # noqa: E402
from pipeline import AudioSegment, FakeASRBackend, FakeTranslationBackend, Pipeline  # noqa: E402


def fake_nllb(source="en"):
    b = RealNLLBBackend.__new__(RealNLLBBackend)
    seen = []
    b._tokenizer = SimpleNamespace(encode_source=lambda text, src: seen.append(src) or ["tok"],
                                   decode_target=lambda toks: "translated")
    b._translator = SimpleNamespace(translate_batch=lambda *a, **k: [SimpleNamespace(hypotheses=[["tgt", "x"]])])
    b._beam_size = 1
    b._source_flores = flores_code(source)
    return b, seen


def test_nllb_switches_source_and_translate_between_leaves_it_alone():
    b, seen = fake_nllb("en")
    asyncio.run(b.translate("hello", "hi"))
    b.set_source_language("hi")
    asyncio.run(b.translate("नमस्ते", "ta"))
    asyncio.run(b.translate_between("old english caption", "en", "ta"))   # notes re-translating an older caption
    asyncio.run(b.translate("फिर", "ta"))
    assert seen == [flores_code("en"), flores_code("hi"), flores_code("en"), flores_code("hi")]
    with pytest.raises(UnsupportedLanguageError):
        b.set_source_language("xx")
    assert b._source_flores == flores_code("hi")                       # a bad request changes nothing


def test_whisper_language_switch():
    w = RealWhisperBackend.__new__(RealWhisperBackend)
    w._language = "en"
    w.set_language("hi")
    assert w._language == "hi"
    w.set_language(None)
    assert w._language is None                                       # auto-detect


def seg():
    return AudioSegment(samples=np.zeros(16000, dtype=np.float32), sample_rate=16000, start_ts=0, end_ts=1)


def test_same_language_attendee_gets_the_spoken_words():
    sent = []

    async def bc(lang, text, final):
        sent.append((lang, text))
    mt = FakeTranslationBackend()
    p = Pipeline(asr=FakeASRBackend("नमस्ते दोस्तों"), translator=mt, broadcast=bc, subscribed_languages=lambda: ["hi", "ta"])
    p.source_language = "hi"
    asyncio.run(p._process_segment(seg(), is_final=True))
    assert ("hi", "नमस्ते दोस्तों") in sent and ("ta", "[ta] नमस्ते दोस्तों") in sent
    assert all(lang != "hi" for _, lang in mt.calls)                  # no hi->hi "translation"


def test_auto_mode_follows_detected_language():
    switched = []
    asr = FakeASRBackend("aaj hum gradient descent padhenge", language=None)
    asr.last_language = "hi"
    sent = []

    async def bc(lang, text, final):
        sent.append((lang, text))
    p = Pipeline(asr=asr, translator=FakeTranslationBackend(), broadcast=bc, subscribed_languages=lambda: ["hi", "en"])
    p.auto_source = True
    p.on_detected_language = lambda l: switched.append(l) or setattr(p, "source_language", l)
    asyncio.run(p._process_segment(seg(), is_final=True))
    assert switched == ["hi"] and ("hi", "aaj hum gradient descent padhenge") in sent


def test_endpoints_persistence_and_notes(tmp_path, monkeypatch):
    import notes as N
    import phone_mic
    import server
    from fastapi.testclient import TestClient

    prefs = tmp_path / "presenter.json"
    monkeypatch.setenv("DWANI_PRESENTER_PREFS", str(prefs))
    mt, asr = FakeTranslationBackend(), FakeASRBackend("x")
    monkeypatch.setattr(server, "pipeline", Pipeline(asr=asr, translator=mt, broadcast=server.pipeline.broadcast,
                                                     subscribed_languages=lambda: []))
    rec = N.NotesRecorder(tmp_path, "pl1", "en")
    monkeypatch.setattr(server, "notes_recorder", rec)
    c = TestClient(server.app)
    server.apply_presenter_language("en", persist=False)
    assert c.get("/presenter-language").json()["choice"] == "en"
    server.record_final("Good morning.", {"hi": "सुप्रभात।"}, rec.started + 2, 1)
    r = c.post("/presenter-language", json={"language": "hi"}).json()
    assert r == {"choice": "hi", "speaking": "hi"}
    assert asr._language == "hi" and mt.source_lang == "hi" and server.pipeline.source_language == "hi"
    assert json.loads(prefs.read_text())["language"] == "hi"           # remembered for next time
    server.record_final("अब हिंदी में बात करते हैं।", {"en": "Now let's talk in Hindi."}, rec.started + 6, 2)
    s = N.load_session(rec.path)
    assert [c_.src_lang for c_ in s.captions] == ["en", "hi"] and s.languages() == ["en", "hi"]
    assert N.text_of(s.captions[1], "hi", s.src_lang) == "अब हिंदी में बात करते हैं।"   # Hindi readers get the spoken words
    assert N.text_of(s.captions[1], "en", s.src_lang) == "Now let's talk in Hindi."
    assert c.post("/presenter-language", json={"language": "auto"}).json()["choice"] == "auto"
    assert asr._language is None and server.pipeline.auto_source is True
    assert c.post("/presenter-language", json={"language": "klingon"}).status_code == 400
    assert c.get("/presenter-language").json()["choice"] == "auto"    # unchanged by a bad request
    remote = TestClient(server.app, client=("10.0.0.5", 1))
    assert remote.post("/presenter-language", json={"language": "en"}).status_code == 404
    assert remote.post(f"/presenter-language?key={phone_mic.PRESENTER_KEY}", json={"language": "en"}).status_code == 200


def test_notes_translate_each_caption_from_its_own_language(tmp_path):
    import notes as N
    rec = N.NotesRecorder(tmp_path, "mix", "en")
    rec.record("Hello class.", {}, rec.started + 2, 1)
    rec.record("आज हम पढ़ेंगे।", {}, rec.started + 5, 1, src_lang="hi")
    s = N.load_session(rec.path)
    calls = []

    async def tr(text, target, source):
        calls.append((source, target))
        return f"[{source}->{target}] {text}"
    asyncio.run(N.ensure_language(s, "ta", tr))
    assert calls == [("en", "ta"), ("hi", "ta")]
    calls.clear()
    asyncio.run(N.ensure_language(s, "hi", tr))
    assert calls == [("en", "hi")]                                    # the Hindi caption isn't translated into Hindi
