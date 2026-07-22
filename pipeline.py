"""
LDST transcription-translation pipeline -- Section 4.3 of the paper.

Audio captured on the host device is segmented on pause boundaries (voice
activity detection), each segment is transcribed once, and the transcript is
translated once per *distinct target language currently selected by connected
attendees* -- not once per attendee -- before being broadcast to every
attendee subscribed to that language. This module implements that pipeline
end to end, deliberately decoupled from any specific ASR/MT model:

    AudioSegmenter        -- turns a stream of raw audio chunks into
                              utterance-length AudioSegments on pause
                              boundaries (energy-based VAD, no external
                              dependency).
    ASRBackend             -- protocol for "audio in, transcript out".
                              Swap in a compact Whisper variant (e.g. via
                              faster-whisper) for real transcription.
    TranslationBackend     -- protocol for "text + target language in,
                              translated text out". Swap in a locally hosted
                              NLLB-200 (or similar) for real translation.
    Pipeline               -- wires the above together and calls the
                              broadcast function from server.py once per
                              language, per segment.

Real ASR/MT models are not loaded here on purpose: this environment has no
network access to a model hub, and the interesting, testable part of this
module is the *wiring* (segmentation boundaries, translate-once-per-language,
broadcast fan-out) rather than the models themselves. `FakeASRBackend` /
`FakeTranslationBackend` exist for tests and local development without GPU
or model downloads; production deployments should implement `ASRBackend`
and `TranslationBackend` against real local models.

Usage sketch from server.py:

    from pipeline import Pipeline, AudioSegmenter

    pipeline = Pipeline(
        asr=RealWhisperBackend(...),
        translator=RealNLLBBackend(...),
        broadcast=broadcast_caption,               # from server.py
        subscribed_languages=lambda: subscribers.keys(),
    )

    # somewhere in the audio capture loop:
    await pipeline.handle_audio_chunk(chunk)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Iterable, Protocol

import numpy as np


# ---------------------------------------------------------------------------
# Audio segmentation
# ---------------------------------------------------------------------------

@dataclass
class AudioSegment:
    """One utterance's worth of audio, bounded by silence on either side."""

    samples: np.ndarray
    sample_rate: int
    start_ts: float
    end_ts: float

    @property
    def duration_seconds(self) -> float:
        return self.end_ts - self.start_ts


@dataclass
class AudioSegmenter:
    """
    Buffers streaming audio and yields an AudioSegment each time it detects a
    natural pause boundary -- i.e. enough voiced audio followed by enough
    silence. This is a simple energy-threshold VAD rather than a dedicated
    VAD library (e.g. webrtcvad): it has no external dependency, and its
    behavior is fully deterministic and easy to unit test with synthetic
    tone/silence buffers. Swap in a proper VAD model here without touching
    any other part of the pipeline if accuracy on real-world noisy venues
    turns out to need it.

    Frames are fed in fixed-size chunks via `push`; a completed segment is
    returned from `push` when a pause boundary is crossed, or `None`
    otherwise. Call `flush` at end-of-stream to force out any buffered
    voiced audio that hasn't yet been followed by silence.
    """

    sample_rate: int = 16_000
    energy_threshold: float = 0.02          # RMS amplitude, audio normalized to [-1, 1]
    min_voiced_seconds: float = 0.3         # ignore blips shorter than this
    min_silence_seconds: float = 0.6        # pause length that closes a segment
    max_segment_seconds: float = 15.0       # force a cut so one run-on sentence
    #                                          doesn't block captions indefinitely

    _voiced_chunks: list[np.ndarray] = field(default_factory=list, init=False)
    _voiced_duration: float = field(default=0.0, init=False)
    _silence_duration: float = field(default=0.0, init=False)
    _segment_start_ts: float | None = field(default=None, init=False)
    _clock: float = field(default=0.0, init=False)

    def _chunk_duration(self, chunk: np.ndarray) -> float:
        return len(chunk) / self.sample_rate

    def _is_voiced(self, chunk: np.ndarray) -> bool:
        if len(chunk) == 0:
            return False
        rms = float(np.sqrt(np.mean(np.square(chunk))))
        return rms >= self.energy_threshold

    def push(self, chunk: np.ndarray) -> AudioSegment | None:
        """Feed one chunk of mono float32 audio in [-1, 1]. Returns a completed
        segment if this chunk closed one out, else None."""
        chunk_duration = self._chunk_duration(chunk)
        voiced = self._is_voiced(chunk)

        if voiced:
            if self._segment_start_ts is None:
                self._segment_start_ts = self._clock
            self._voiced_chunks.append(chunk)
            self._voiced_duration += chunk_duration
            self._silence_duration = 0.0
        else:
            self._silence_duration += chunk_duration
            if self._voiced_chunks:
                # keep the trailing silence in the segment so words aren't clipped
                self._voiced_chunks.append(chunk)

        self._clock += chunk_duration

        segment = None
        should_close = self._voiced_chunks and (
            (self._silence_duration >= self.min_silence_seconds
             and self._voiced_duration >= self.min_voiced_seconds)
            or self._voiced_duration >= self.max_segment_seconds
        )
        if should_close:
            segment = self._close_segment()

        return segment

    def flush(self) -> AudioSegment | None:
        """Force out any buffered voiced audio at end-of-stream, even without
        a trailing pause. Returns None if there's nothing to flush."""
        if not self._voiced_chunks or self._voiced_duration < self.min_voiced_seconds:
            self._voiced_chunks.clear()
            self._voiced_duration = 0.0
            self._silence_duration = 0.0
            self._segment_start_ts = None
            return None
        return self._close_segment()

    def _close_segment(self) -> AudioSegment:
        samples = np.concatenate(self._voiced_chunks)
        segment = AudioSegment(
            samples=samples,
            sample_rate=self.sample_rate,
            start_ts=self._segment_start_ts,
            end_ts=self._clock,
        )
        self._voiced_chunks = []
        self._voiced_duration = 0.0
        self._silence_duration = 0.0
        self._segment_start_ts = None
        return segment


# ---------------------------------------------------------------------------
# Pluggable ASR / MT backends
# ---------------------------------------------------------------------------

class ASRBackend(Protocol):
    """'Audio in, transcript out.' Implement against a real local model
    (e.g. faster-whisper) for production use."""

    async def transcribe(self, segment: AudioSegment) -> str: ...


class TranslationBackend(Protocol):
    """'Text + target language in, translated text out.' Implement against a
    real local model (e.g. NLLB-200 via ctranslate2) for production use."""

    async def translate(self, text: str, target_lang: str) -> str: ...


class TranslationCache(Protocol):
    """Sits in front of a TranslationBackend, per target language, so a
    segment whose transcript is a repeat (or near-repeat, for
    similarity-based implementations) of one already translated in this
    session can skip the real `translator.translate()` call entirely.

    `get` returns the cached translation on a hit, or None on a miss.
    `put` is only ever called after a real translate() call, so a cache
    starts empty and is populated purely from what the pipeline itself
    already translated -- it never needs pre-seeding.

    See translation_cache.py's SemanticCache for a similarity-based
    implementation backed by sentence embeddings; ExactMatchCache below is
    the dependency-free special case (exact string match only) used as a
    lightweight default and for tests.
    """

    async def get(self, text: str, lang: str) -> str | None: ...

    async def put(self, text: str, lang: str, translation: str) -> None: ...


class NoOpCache:
    """Default TranslationCache: always a miss. Pipeline behaves exactly as
    it did before caching existed unless a real cache is passed in."""

    async def get(self, text: str, lang: str) -> str | None:
        return None

    async def put(self, text: str, lang: str, translation: str) -> None:
        return None


class ExactMatchCache:
    """Dependency-free TranslationCache: hits only on byte-for-byte repeats
    of a transcript already seen for that language. Whisper rarely
    transcribes the same utterance identically twice, so this catches a
    narrower set of repeats than SemanticCache (translation_cache.py) --
    e.g. a presenter re-reading a fixed slide title, or a placeholder
    transcript -- but needs no embedding model and is fully synchronous
    under the hood, which makes it useful both as a cheap always-available
    default and as a fast fixture for testing Pipeline's cache wiring
    without pulling in sentence-transformers.
    """

    def __init__(self) -> None:
        # (text, lang) -> translation
        self._store: dict[tuple[str, str], str] = {}
        self.hits = 0
        self.misses = 0

    async def get(self, text: str, lang: str) -> str | None:
        result = self._store.get((text, lang))
        if result is None:
            self.misses += 1
        else:
            self.hits += 1
        return result

    async def put(self, text: str, lang: str, translation: str) -> None:
        self._store[(text, lang)] = translation


class FakeASRBackend:
    """Deterministic stand-in for tests/local dev: returns a fixed transcript
    (or one supplied per-call via `next_transcript`) instead of running a
    real model."""

    def __init__(self, default_transcript: str = "") -> None:
        self.default_transcript = default_transcript
        self.calls: list[AudioSegment] = []

    async def transcribe(self, segment: AudioSegment) -> str:
        self.calls.append(segment)
        return self.default_transcript


class FakeTranslationBackend:
    """Deterministic stand-in for tests/local dev: 'translates' by tagging the
    source text with the target language code, so assertions can check both
    *what* was translated and *how many times*."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def translate(self, text: str, target_lang: str) -> str:
        self.calls.append((text, target_lang))
        return f"[{target_lang}] {text}"


# ---------------------------------------------------------------------------
# Pipeline: wires segmentation -> ASR -> per-language MT -> broadcast
# ---------------------------------------------------------------------------

BroadcastFn = Callable[[str, str, bool], Awaitable[None]]


class Pipeline:
    """
    Owns one host session's transcription-translation flow.

    `subscribed_languages` is a zero-arg callable returning the languages
    currently subscribed to (server.py passes `lambda: subscribers.keys()`)
    so this module never needs to import or know about server.py's
    WebSocket bookkeeping directly.

    Translation happens once per distinct subscribed language per segment,
    matching Section 4.3 of the paper -- not once per attendee -- and the
    result for each language is broadcast once via `broadcast`, which is
    itself responsible for fanning that single message out to every
    attendee subscribed to that language (see server.broadcast_caption).
    """

    def __init__(
        self,
        asr: ASRBackend,
        translator: TranslationBackend,
        broadcast: BroadcastFn,
        subscribed_languages: Callable[[], Iterable[str]],
        segmenter: AudioSegmenter | None = None,
        cache: TranslationCache | None = None,
    ) -> None:
        self.asr = asr
        self.translator = translator
        self.broadcast = broadcast
        self.subscribed_languages = subscribed_languages
        self.segmenter = segmenter or AudioSegmenter()
        # Defaults to NoOpCache (always a miss) so passing nothing preserves
        # exactly the old call-translate-every-time behavior -- caching is
        # opt-in via e.g. cache=ExactMatchCache() or
        # cache=translation_cache.SemanticCache().
        self.cache = cache or NoOpCache()

    async def handle_audio_chunk(self, chunk: np.ndarray) -> str | None:
        """Feed one chunk of live audio in. If it closes out a segment, runs
        ASR + per-language MT + broadcast and returns the transcript (mainly
        useful for logging/tests); otherwise returns None."""
        segment = self.segmenter.push(chunk)
        if segment is None:
            return None
        return await self._process_segment(segment)

    async def flush(self) -> str | None:
        """Force out and process any trailing buffered audio at session end."""
        segment = self.segmenter.flush()
        if segment is None:
            return None
        return await self._process_segment(segment)

    async def _process_segment(self, segment: AudioSegment) -> str:
        transcript = await self.asr.transcribe(segment)

        # Snapshot languages once per segment: translating once per language
        # here is what keeps MT cost independent of attendee count.
        languages = list(dict.fromkeys(self.subscribed_languages()))
        for lang in languages:
            cached = await self.cache.get(transcript, lang)
            if cached is not None:
                translated = cached
            else:
                start = time.monotonic()
                translated = await self.translator.translate(transcript, lang)
                elapsed = time.monotonic() - start
                await self.cache.put(transcript, lang, translated)
                # Only SemanticCache (translation_cache.py) exposes this --
                # feeds CacheStats.estimated_seconds_saved automatically for
                # any caller using it, without NoOpCache/ExactMatchCache
                # needing to know or care about timing at all.
                record = getattr(self.cache, "record_miss_translate_seconds", None)
                if record is not None:
                    record(elapsed)
            await self.broadcast(lang, translated, True)

        return transcript


def make_silence(duration_seconds: float, sample_rate: int = 16_000) -> np.ndarray:
    """Test/dev helper: a chunk of true silence."""
    return np.zeros(int(duration_seconds * sample_rate), dtype=np.float32)


def make_tone(duration_seconds: float, sample_rate: int = 16_000, amplitude: float = 0.5, freq: float = 220.0) -> np.ndarray:
    """Test/dev helper: a chunk of audible sine tone, standing in for speech
    for VAD purposes (energy-based VAD only cares about amplitude, not
    whether the signal is actually speech)."""
    t = np.arange(int(duration_seconds * sample_rate)) / sample_rate
    return (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float32)