"""
Bidirectional Q&A Translation -- Hackathon Extension.

The core LDST pipeline (pipeline.py, Section 4.3 of the paper) is
one-directional: presenter speaks, attendees read translated captions.
This module adds the missing reverse path: an attendee raises their hand,
speaks a question in their own selected language, and the presenter sees
(and optionally hears) it translated into their own language -- still
entirely on the local network, no traffic ever leaving it, same as the
rest of the system.

    attendee mic --> AudioSegmenter (per-attendee, existing class) -->
    QAPipeline.handle_question_audio() -->
        ASR (attendee's language) --> BidirectionalTranslationBackend
        (attendee_lang -> presenter_lang) --> deliver_to_presenter()

-----------------------------------------------------------------------------
Why this needs its own translation backend
-----------------------------------------------------------------------------

backends.py's RealNLLBBackend pins `source_lang` once, at construction,
because the main pipeline only ever translates in one fixed direction
(presenter's language -> whichever languages attendees have subscribed
to). That's the right design for that direction: it doesn't need to know
or care about a per-call source language.

Q&A is the opposite: the *target* language is fixed (the presenter's own
language) but the *source* language changes with every question, depending
on which attendee is asking. Rather than complicating RealNLLBBackend's
constructor with a source language that main-pipeline callers would never
use, BidirectionalTranslationBackend wraps the same underlying CTranslate2
model and exposes a `translate_between(text, source_lang, target_lang)`
call instead -- a separate, narrow interface for a separate direction.

-----------------------------------------------------------------------------
Wiring into server.py
-----------------------------------------------------------------------------

    from qa_pipeline import QAPipeline, BidirectionalTranslationBackend
    from backends import RealWhisperBackend

    qa_translator = BidirectionalTranslationBackend(nllb_model_dir="nllb-200-ct2")
    qa_pipeline = QAPipeline(
        asr=RealWhisperBackend(model_size="small", language=None),  # None: per-question
        translator=qa_translator,
        presenter_language="en",
        deliver_to_presenter=send_question_to_presenter_ws,
    )

    # per-attendee, only while that attendee is actively asking a question:
    segmenter = AudioSegmenter()  # same class the main pipeline uses
    ...
    segment = segmenter.handle_chunk(chunk)
    if segment is not None:
        question = await qa_pipeline.handle_question_audio(segment, asker_language=attendee_lang)

`asr=RealWhisperBackend(..., language=None)` is deliberate here, unlike the
main pipeline's presenter-side instance which pins `language=` to the
presenter's known spoken language: an attendee's spoken language is only
known from what they selected in the caption UI, and letting Whisper
auto-detect per question (rather than trusting the UI selection blindly)
catches the case where an attendee asks in a different language than the
one they picked for reading captions.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Awaitable, Callable, Optional, Union

if TYPE_CHECKING:
    from pipeline import ASRBackend, AudioSegment


@dataclass
class QAQuestion:
    """One asked question, in both languages, plus lifecycle state a
    presenter-facing UI can use to show a queue (unanswered questions
    first) rather than a flat, ever-growing log."""

    id: str
    asker_language: str
    presenter_language: str
    original_text: str
    translated_text: str
    asked_at: float
    answered: bool = False


class BidirectionalTranslationBackend:
    """NLLB-200-backed translator that takes `source_lang` per call rather
    than fixing it at construction -- see this module's docstring for why
    that's a separate class from backends.py's RealNLLBBackend rather than
    a change to it.
    """

    def __init__(
        self,
        nllb_model_dir: str,
        tokenizer_name: str = "facebook/nllb-200-distilled-600M",
        device: str = "cpu",
        beam_size: int = 4,
    ) -> None:
        import ctranslate2  # deferred: heavy, optional dep -- same pattern as backends.py
        from transformers import AutoTokenizer  # deferred: heavy, optional dep

        self._translator = ctranslate2.Translator(nllb_model_dir, device=device)
        self._tokenizer = AutoTokenizer.from_pretrained(tokenizer_name)
        self._beam_size = beam_size

    async def translate_between(self, text: str, source_lang: str, target_lang: str) -> str:
        return await asyncio.to_thread(self._translate_sync, text, source_lang, target_lang)

    def _translate_sync(self, text: str, source_lang: str, target_lang: str) -> str:
        from backends import flores_code  # reuse the existing lang-code table -- one source of truth

        self._tokenizer.src_lang = flores_code(source_lang)
        source_tokens = self._tokenizer.convert_ids_to_tokens(self._tokenizer(text).input_ids)

        result = self._translator.translate_batch(
            [source_tokens],
            target_prefix=[[flores_code(target_lang)]],
            beam_size=self._beam_size,
        )
        output_tokens = result[0].hypotheses[0][1:]  # drop the target-lang prefix token
        output_ids = self._tokenizer.convert_tokens_to_ids(output_tokens)
        return self._tokenizer.decode(output_ids, skip_special_tokens=True).strip()


class FakeBidirectionalTranslationBackend:
    """Dependency-free stand-in for tests and demo-without-models runs --
    same role FakeASRBackend/FakeTranslationBackend play in pipeline.py."""

    async def translate_between(self, text: str, source_lang: str, target_lang: str) -> str:
        return f"[{source_lang}->{target_lang}] {text}"


DeliverCallback = Callable[["QAQuestion"], Optional[Awaitable[None]]]


class QAPipeline:
    """Handles the attendee-asks-a-question flow end to end. Mirrors
    Pipeline's own shape (an ASR backend, a translation call, a broadcast
    callback) deliberately, so this reads as a sibling of the main
    caption pipeline rather than a bolted-on side system.
    """

    def __init__(
        self,
        asr: Optional["ASRBackend"],
        translator: Union[BidirectionalTranslationBackend, FakeBidirectionalTranslationBackend],
        presenter_language: str,
        deliver_to_presenter: DeliverCallback,
        max_questions_in_flight: int = 50,
    ) -> None:
        """`asr` may be None if you only ever intend to call
        handle_question_text() (typed questions, no mic/audio permissions
        needed at all) -- it's only touched by handle_question_audio().
        `max_questions_in_flight` bounds the in-memory question log -- a
        long session with many questions shouldn't grow this unboundedly;
        the oldest entry is dropped once the cap is hit, same trade-off
        SemanticCache's eviction makes for its own store.
        """
        self._asr = asr
        self._translator = translator
        self._presenter_language = presenter_language
        self._deliver = deliver_to_presenter
        self._questions: dict[str, QAQuestion] = {}
        self._max_in_flight = max_questions_in_flight

    async def handle_question_audio(self, segment: "AudioSegment", asker_language: str) -> QAQuestion:
        original_text = await self._asr.transcribe(segment)
        return await self._finish_question(original_text, asker_language)

    async def handle_question_text(self, text: str, asker_language: str) -> QAQuestion:
        """Same as handle_question_audio, minus the ASR step -- for a typed
        question instead of a spoken one. No mic, no browser audio
        permissions, no AudioSegmenter -- just the translation half of the
        pipeline, which is also the half that actually needs the presenter
        to see anything. An attendee can type their question in whichever
        language they're comfortable in and it's translated the same way a
        spoken one would be.
        """
        return await self._finish_question(text, asker_language)

    async def _finish_question(self, original_text: str, asker_language: str) -> QAQuestion:
        translated_text = await self._translator.translate_between(
            original_text, source_lang=asker_language, target_lang=self._presenter_language
        )

        question = QAQuestion(
            id=str(uuid.uuid4()),
            asker_language=asker_language,
            presenter_language=self._presenter_language,
            original_text=original_text,
            translated_text=translated_text,
            asked_at=time.time(),
        )
        self._questions[question.id] = question
        if len(self._questions) > self._max_in_flight:
            oldest_id = min(self._questions, key=lambda qid: self._questions[qid].asked_at)
            del self._questions[oldest_id]

        result = self._deliver(question)
        if asyncio.iscoroutine(result):
            await result
        return question

    def mark_answered(self, question_id: str) -> None:
        if question_id in self._questions:
            self._questions[question_id].answered = True

    def pending_questions(self) -> list[QAQuestion]:
        """Oldest-first, unanswered only -- what a presenter-facing queue
        UI would actually want to render."""
        return sorted(
            (q for q in self._questions.values() if not q.answered),
            key=lambda q: q.asked_at,
        )
