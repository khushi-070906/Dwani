"""Framework-free controller: owns sessions and the ASR/MT backends. api.py is a thin HTTP layer over this, so the
logic is testable (and reusable from a desktop GUI) without FastAPI."""
from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import dataclass

from . import eligibility, extract, records
from .outcomes import OUTCOMES
from .schema import FormTemplate, load_all
from .session import FormSession, Reply

FLOW_OUTCOMES = {**OUTCOMES, "lookup_land": records.land_outcome, "lookup_ration": records.ration_outcome,
                 "advisor": eligibility.advisor_outcome}

# Whisper has no Odia model; others below are supported by Whisper per its language list. Unsupported => text/operator input.
ASR_LANGS = {"en", "hi", "bn", "ta", "te", "mr", "gu", "kn", "ml", "pa", "ur", "as", "ne", "sa", "sd"}
SESSION_IDLE_TTL = 30 * 60        # seconds without any activity before a session (and its Aadhaar etc.) is dropped
COMPLETED_IDLE_TTL = 15 * 60      # shorter once the form is finished: there is nothing left to do but export it
SWEEP_INTERVAL = 60


@dataclass
class _AudioSegmentFallback:
    """Same shape as DwaniLive's pipeline.AudioSegment, used only when DwaniLive is not importable (tests, tooling)."""
    samples: object
    sample_rate: int
    start: float
    end: float


def _audio_segment_cls():
    try:
        from pipeline import AudioSegment          # reuse DwaniLive's segment type
        return AudioSegment
    except ImportError:
        return _AudioSegmentFallback


class FormService:
    def __init__(self, asr=None, translator=None, templates: dict[str, FormTemplate] | None = None, transliterate=None,
                 record_store: "records.RecordStore | None" = None):
        self.asr, self.translator, self.transliterate = asr, translator, transliterate
        self.templates = templates if templates is not None else load_all()
        self.records = record_store if record_store is not None else records.RecordStore()
        self.sessions: dict[str, FormSession] = {}
        self._asr_lock = asyncio.Lock()      # RealWhisperBackend.set_language is global state: one utterance at a time

    def forms(self) -> list[dict]:
        return [{"id": t.id, "title": t.title, "titles": t.titles, "fields": len(t.fields), "flow": t.flow,
                 "description": t.description,
                 "available": self._available(t)}
                for t in self.templates.values() if not t.hidden]

    def _available(self, t: FormTemplate) -> bool:
        if t.flow == "lookup_land":
            return bool(self.records.land)
        if t.flow == "lookup_ration":
            return bool(self.records.ration)
        return True

    def capabilities(self) -> dict:
        return {"asr": self.asr is not None, "asr_langs": sorted(ASR_LANGS) if self.asr is not None else [],
                "translation": self.translator is not None, "records": self.records.available()}

    # ---- session lifetime ----------------------------------------------------------
    def purge_expired(self, now: float | None = None) -> int:
        """Drop sessions idle for too long (answers include Aadhaar etc.: don't keep them around). Returns how many."""
        now = time.time() if now is None else now
        dead = [k for k, s in self.sessions.items()
                if now - s.last_active > (COMPLETED_IDLE_TTL if s.phase == "done" else SESSION_IDLE_TTL)]
        for k in dead:
            del self.sessions[k]
        return len(dead)

    async def sweep_forever(self, interval: float = SWEEP_INTERVAL) -> None:
        """Run as a background task so idle sessions are purged even when nobody creates a new one."""
        while True:
            await asyncio.sleep(interval)
            self.purge_expired()

    async def create(self, form_id: str, lang: str, prefill_from: str | None = None) -> tuple[FormSession, Reply]:
        """prefill_from: a finished scheme-advisor session; its answers (gender, income, land...) become
        one-tap confirmations in the new form instead of questions."""
        self.purge_expired()
        if form_id not in self.templates:
            raise KeyError(form_id)
        t = self.templates[form_id]
        s = FormSession(t, lang, self.translator, self.transliterate)
        if t.extract:
            s.extractor = extract.extract_for
        if t.flow in ("lookup_land", "lookup_ration"):
            s.checker = records.checker_for(self.records)
        if prefill_from:
            src = self.sessions.get(prefill_from)
            if src is not None and src.template.flow == "advisor":
                s.prefill(eligibility.prefill_for(src, t))
        self.sessions[s.id] = s
        return s, await self.finish(s, await s.start())

    async def finish(self, s: FormSession, reply: Reply) -> Reply:
        """When a flow completes: build its outcome (draft / record / schemes) once and say it."""
        if not reply.done or s.outcome is not None:
            if reply.done and s.outcome is not None:
                reply.outcome = s.outcome
            return reply
        builder = FLOW_OUTCOMES.get(s.template.flow)
        if builder is None:
            return reply
        out = builder(s, service=self)
        said = [await s.msg(out["say_key"], **out.get("say_kw", {}))]
        if out.get("type") == "lookup":
            if out.get("demo"):
                said.append(await s.msg("records_demo"))
            if out.get("as_of"):
                from .schema import FormField
                spoken_date = s.readback(FormField("as_of", "date", "", {"en": ""}), out["as_of"], s.lang)
                said.append(await s.msg("records_as_of", date=spoken_date))
            if out.get("stale"):
                said.append(await s.msg("records_stale"))
        if out.get("disclaimer_key"):
            said.append(await s.msg(out["disclaimer_key"]))
        out["say"] = " ".join(said)
        s.outcome = out
        reply.text = out["say"] if out.get("type") in ("lookup", "advisor") else reply.text + " " + out["say"]
        reply.outcome = out
        return reply

    def get(self, sid: str) -> FormSession:
        self.purge_expired()
        s = self.sessions[sid]               # KeyError => unknown or expired
        s.touch()
        return s

    async def answer_text(self, sid: str, text: str) -> Reply:
        s = self.get(sid)
        return await self.finish(s, await s.submit(text))

    async def answer_audio(self, sid: str, pcm16: bytes, sample_rate: int = 16_000) -> tuple[str, Reply]:
        """Raw little-endian PCM16 mono in -> (transcript, Reply)."""
        if self.asr is None:
            raise RuntimeError("no ASR backend configured (running text-only)")
        s = self.get(sid)
        if s.lang not in ASR_LANGS:
            raise RuntimeError(f"speech recognition is not available for {s.lang!r}; use typed input")
        import numpy as np                           # lazy: the rest of the core needs no numpy
        samples = np.frombuffer(pcm16[: len(pcm16) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0
        seg = _audio_segment_cls()(samples, sample_rate, 0.0, len(samples) / sample_rate)
        async with self._asr_lock:
            if hasattr(self.asr, "set_language"):
                self.asr.set_language(s.lang)
            transcribe = self.asr.transcribe
            if inspect.iscoroutinefunction(transcribe):
                transcript = await transcribe(seg)
            else:                                    # blocking backend: keep it off the event loop
                transcript = await asyncio.to_thread(transcribe, seg)
                if inspect.isawaitable(transcript):
                    transcript = await transcript
        return transcript, await self.finish(s, await s.submit(transcript))

    def delete(self, sid: str) -> None:
        self.sessions.pop(sid, None)
