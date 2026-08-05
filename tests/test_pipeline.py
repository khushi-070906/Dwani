"""
Tests for pipeline.py -- the AudioSegmenter's pause-boundary detection, and
the Pipeline's translate-once-per-language-per-segment broadcast fan-out.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import numpy as np
import pytest

from pipeline import (
    AudioSegmenter,
    FakeASRBackend,
    FakeTranslationBackend,
    Pipeline,
    make_silence,
    make_tone,
)


# ---------------------------------------------------------------------------
# AudioSegmenter
# ---------------------------------------------------------------------------

class TestAudioSegmenter:
    def test_no_segment_while_voice_is_ongoing(self):
        seg = AudioSegmenter(min_voiced_seconds=0.3, min_silence_seconds=0.6)
        # 0.3s of tone, in small chunks -- never followed by silence yet
        chunk = make_tone(0.1)
        for _ in range(3):
            result = seg.push(chunk)
        assert result is None

    def test_segment_closes_after_pause(self):
        seg = AudioSegmenter(min_voiced_seconds=0.3, min_silence_seconds=0.6)
        for _ in range(4):
            assert seg.push(make_tone(0.1)) is None  # 0.4s voiced
        assert seg.push(make_silence(0.3)) is None    # 0.3s silence, not enough yet
        result = seg.push(make_silence(0.4))           # now 0.7s silence total
        assert result is not None
        assert result.duration_seconds == pytest.approx(0.4 + 0.3 + 0.4, abs=1e-6)

    def test_short_voiced_blip_does_not_force_close_without_enough_duration(self):
        seg = AudioSegmenter(min_voiced_seconds=0.5, min_silence_seconds=0.3)
        assert seg.push(make_tone(0.1)) is None   # only 0.1s voiced so far
        result = seg.push(make_silence(0.5))        # plenty of silence, but not enough voice
        assert result is None

    def test_max_segment_seconds_forces_a_cut(self):
        seg = AudioSegmenter(max_segment_seconds=0.5, min_voiced_seconds=0.1, min_silence_seconds=10)
        result = None
        for _ in range(6):  # 6 * 0.1s = 0.6s of continuous tone, no silence at all
            result = seg.push(make_tone(0.1))
            if result is not None:
                break
        assert result is not None
        assert result.duration_seconds >= 0.5

    def test_segments_are_independent_after_close(self):
        seg = AudioSegmenter(min_voiced_seconds=0.2, min_silence_seconds=0.3)
        for _ in range(2):
            seg.push(make_tone(0.1))
        first = seg.push(make_silence(0.3))
        assert first is not None

        # start a fresh utterance -- segmenter must not still think it's mid-segment
        assert seg.push(make_tone(0.1)) is None
        second = seg.push(make_silence(0.3))
        # second utterance alone is only 0.1s voiced, below min_voiced_seconds
        assert second is None

    def test_flush_returns_none_when_nothing_buffered(self):
        seg = AudioSegmenter()
        assert seg.flush() is None

    def test_flush_returns_none_for_voiced_audio_below_minimum(self):
        seg = AudioSegmenter(min_voiced_seconds=0.5)
        seg.push(make_tone(0.1))
        assert seg.flush() is None

    def test_flush_returns_trailing_segment_with_enough_voice(self):
        seg = AudioSegmenter(min_voiced_seconds=0.2)
        seg.push(make_tone(0.3))
        result = seg.flush()
        assert result is not None
        assert result.duration_seconds == pytest.approx(0.3, abs=1e-6)

    def test_pure_silence_never_produces_a_segment(self):
        seg = AudioSegmenter(min_silence_seconds=0.2)
        result = None
        for _ in range(10):
            result = seg.push(make_silence(0.1))
        assert result is None
        assert seg.flush() is None


# ---------------------------------------------------------------------------
# Pipeline wiring
# ---------------------------------------------------------------------------

class TestPipeline:
    @pytest.mark.anyio
    async def test_translates_once_per_subscribed_language_not_per_attendee(self):
        asr = FakeASRBackend(default_transcript="hello everyone")
        translator = FakeTranslationBackend()
        broadcast = AsyncMock()
        # simulate 3 attendees on "hi" and 2 on "fr" -- still just 2 distinct languages
        subscribed = lambda: ["hi", "hi", "hi", "fr", "fr"]

        pipeline = Pipeline(asr, translator, broadcast, subscribed)
        # force out a segment directly rather than feeding audio chunk-by-chunk
        pipeline.segmenter.push(make_tone(0.5))
        transcript = await pipeline.handle_audio_chunk(make_silence(1.0))

        assert transcript == "hello everyone"
        # translate called once per *distinct* language, despite duplicate entries
        called_langs = {lang for _, lang in translator.calls}
        assert called_langs == {"hi", "fr"}
        assert len(translator.calls) == 2

    @pytest.mark.anyio
    async def test_broadcasts_once_per_language_with_translated_text(self):
        asr = FakeASRBackend(default_transcript="welcome to the seminar")
        translator = FakeTranslationBackend()
        broadcast = AsyncMock()
        subscribed = lambda: ["hi", "fr"]

        pipeline = Pipeline(asr, translator, broadcast, subscribed)
        pipeline.segmenter.push(make_tone(0.5))
        await pipeline.handle_audio_chunk(make_silence(1.0))

        broadcast.assert_any_await("hi", "[hi] welcome to the seminar", True)
        broadcast.assert_any_await("fr", "[fr] welcome to the seminar", True)
        assert broadcast.await_count == 2

    @pytest.mark.anyio
    async def test_no_subscribers_transcribes_but_broadcasts_nothing(self):
        asr = FakeASRBackend(default_transcript="hello")
        translator = FakeTranslationBackend()
        broadcast = AsyncMock()
        subscribed = lambda: []

        pipeline = Pipeline(asr, translator, broadcast, subscribed)
        pipeline.segmenter.push(make_tone(0.5))
        transcript = await pipeline.handle_audio_chunk(make_silence(1.0))

        assert transcript == "hello"
        assert len(asr.calls) == 1
        assert translator.calls == []
        broadcast.assert_not_awaited()

    @pytest.mark.anyio
    async def test_incomplete_segment_returns_none_and_runs_nothing(self):
        asr = FakeASRBackend(default_transcript="should not be called")
        translator = FakeTranslationBackend()
        broadcast = AsyncMock()
        subscribed = lambda: ["hi"]

        pipeline = Pipeline(asr, translator, broadcast, subscribed)
        result = await pipeline.handle_audio_chunk(make_tone(0.1))  # too short to close

        assert result is None
        assert asr.calls == []
        broadcast.assert_not_awaited()

    @pytest.mark.anyio
    async def test_flush_processes_trailing_audio(self):
        asr = FakeASRBackend(default_transcript="closing remarks")
        translator = FakeTranslationBackend()
        broadcast = AsyncMock()
        subscribed = lambda: ["hi"]

        pipeline = Pipeline(
            asr, translator, broadcast, subscribed,
            segmenter=AudioSegmenter(min_voiced_seconds=0.1),
        )
        pipeline.segmenter.push(make_tone(0.3))  # voiced, no trailing silence yet
        transcript = await pipeline.flush()

        assert transcript == "closing remarks"
        broadcast.assert_awaited_once_with("hi", "[hi] closing remarks", True)

    @pytest.mark.anyio
    async def test_flush_with_nothing_buffered_is_a_noop(self):
        asr = FakeASRBackend()
        translator = FakeTranslationBackend()
        broadcast = AsyncMock()

        pipeline = Pipeline(asr, translator, broadcast, lambda: ["hi"])
        result = await pipeline.flush()

        assert result is None
        broadcast.assert_not_awaited()

    @pytest.mark.anyio
    async def test_asr_called_once_per_segment_regardless_of_language_count(self):
        asr = FakeASRBackend(default_transcript="one segment")
        translator = FakeTranslationBackend()
        broadcast = AsyncMock()
        subscribed = lambda: ["hi", "fr", "ta", "te", "bn"]

        pipeline = Pipeline(asr, translator, broadcast, subscribed)
        pipeline.segmenter.push(make_tone(0.5))
        await pipeline.handle_audio_chunk(make_silence(1.0))

        assert len(asr.calls) == 1