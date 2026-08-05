"""
Dependency-free tests for qa_pipeline.py -- exercises the question flow
(transcribe -> translate -> deliver) and the in-flight cap, using
FakeBidirectionalTranslationBackend and a minimal fake ASR backend, same
pattern test_pipeline.py uses for the main pipeline.
"""

from __future__ import annotations

import pytest

from qa_pipeline import FakeBidirectionalTranslationBackend, QAPipeline


class _FakeASR:
    def __init__(self, transcript: str = "what is the difference from prior work?") -> None:
        self.transcript = transcript
        self.calls = 0

    async def transcribe(self, segment) -> str:
        self.calls += 1
        return self.transcript


@pytest.mark.anyio
async def test_handle_question_audio_transcribes_translates_and_delivers():
    delivered = []

    async def deliver(question):
        delivered.append(question)

    pipeline = QAPipeline(
        asr=_FakeASR("what is the difference from prior work?"),
        translator=FakeBidirectionalTranslationBackend(),
        presenter_language="en",
        deliver_to_presenter=deliver,
    )

    question = await pipeline.handle_question_audio(segment=None, asker_language="hi")

    assert question.original_text == "what is the difference from prior work?"
    assert question.translated_text == "[hi->en] what is the difference from prior work?"
    assert question.asker_language == "hi"
    assert question.presenter_language == "en"
    assert question.answered is False
    assert delivered == [question]


@pytest.mark.anyio
async def test_deliver_callback_may_be_sync():
    delivered = []

    def deliver(question):  # not a coroutine -- QAPipeline must handle both
        delivered.append(question)

    pipeline = QAPipeline(
        asr=_FakeASR(),
        translator=FakeBidirectionalTranslationBackend(),
        presenter_language="en",
        deliver_to_presenter=deliver,
    )

    await pipeline.handle_question_audio(segment=None, asker_language="fr")
    assert len(delivered) == 1


@pytest.mark.anyio
async def test_pending_questions_excludes_answered_and_is_oldest_first():
    pipeline = QAPipeline(
        asr=_FakeASR(),
        translator=FakeBidirectionalTranslationBackend(),
        presenter_language="en",
        deliver_to_presenter=lambda q: None,
    )

    q1 = await pipeline.handle_question_audio(segment=None, asker_language="hi")
    q2 = await pipeline.handle_question_audio(segment=None, asker_language="ta")
    q1.asked_at, q2.asked_at = 100.0, 200.0  # force a deterministic order

    pending = pipeline.pending_questions()
    assert [q.id for q in pending] == [q1.id, q2.id]

    pipeline.mark_answered(q1.id)
    pending = pipeline.pending_questions()
    assert [q.id for q in pending] == [q2.id]


@pytest.mark.anyio
async def test_max_questions_in_flight_evicts_oldest():
    pipeline = QAPipeline(
        asr=_FakeASR(),
        translator=FakeBidirectionalTranslationBackend(),
        presenter_language="en",
        deliver_to_presenter=lambda q: None,
        max_questions_in_flight=2,
    )

    q1 = await pipeline.handle_question_audio(segment=None, asker_language="hi")
    q1.asked_at = 1.0
    q2 = await pipeline.handle_question_audio(segment=None, asker_language="ta")
    q2.asked_at = 2.0
    q3 = await pipeline.handle_question_audio(segment=None, asker_language="bn")
    q3.asked_at = 3.0

    assert q1.id not in pipeline._questions
    assert q2.id in pipeline._questions
    assert q3.id in pipeline._questions


@pytest.mark.anyio
async def test_handle_question_text_skips_asr_entirely():
    delivered = []

    async def deliver(question):
        delivered.append(question)

    # asr=None -- the typed-question path never touches it, unlike
    # handle_question_audio which would raise if it tried.
    pipeline = QAPipeline(
        asr=None,
        translator=FakeBidirectionalTranslationBackend(),
        presenter_language="en",
        deliver_to_presenter=deliver,
    )

    question = await pipeline.handle_question_text("kya yeh offline chalta hai?", asker_language="hi")

    assert question.original_text == "kya yeh offline chalta hai?"
    assert question.translated_text == "[hi->en] kya yeh offline chalta hai?"
    assert delivered == [question]
