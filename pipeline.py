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

import asyncio
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
    silence.

    Two VAD backends are available (`vad_backend`):
      "energy"    -- default. A plain RMS-amplitude threshold. No external
                     dependency, fully deterministic, easy to unit test with
                     synthetic tone/silence buffers -- but crude on a real
                     venue: background noise can keep segments open (feeding
                     Whisper long noisy stretches it may hallucinate on),
                     and a quiet/distant mic can fail to register as voiced
                     at all (dropped words).
      "webrtcvad" -- Google's WebRTC voice activity detector (`pip install
                     webrtcvad`), a real speech-vs-non-speech classifier
                     rather than a bare energy threshold -- meaningfully
                     more robust to background noise without needing a full
                     ML VAD model. Requires sample_rate in
                     {8000, 16000, 32000, 48000}.

    Frames are fed in fixed-size chunks via `push`; a completed segment is
    returned from `push` when a pause boundary is crossed, or `None`
    otherwise. Call `flush` at end-of-stream to force out any buffered
    voiced audio that hasn't yet been followed by silence.

    `interim_interval_seconds`, if set, additionally makes `should_emit_interim()`
    return True periodically during a long, still-open voiced run, so
    Pipeline can broadcast an unfinalized "here's what's been said so far"
    caption well before the segment actually closes (on a pause, or the
    max_segment_seconds force-cut) -- see Pipeline._process_segment's
    is_final param. This is NOT true incremental/streaming ASR: each
    interim tick re-transcribes the entire buffered-so-far audio from
    scratch (the only kind of "streaming" a batch ASR backend like Whisper
    supports without a custom incremental decoder), so it trades CPU for
    lower perceived latency -- left off (None) by default for exactly that
    reason; enable and watch CPU load on your actual presenter hardware
    before shipping it on.
    """

    sample_rate: int = 16_000
    energy_threshold: float = 0.02          # RMS amplitude, audio normalized to [-1, 1] -- only used by vad_backend="energy"
    min_voiced_seconds: float = 0.3         # ignore blips shorter than this
    min_silence_seconds: float = 0.6        # pause length that closes a segment
    max_segment_seconds: float = 15.0       # force a cut so one run-on sentence
    #                                          doesn't block captions indefinitely
    max_trailing_silence_seconds: float = 0.3
    # The pause-detection logic needs up to min_silence_seconds of real
    # silence to reliably *decide* a segment has ended -- but feeding Whisper
    # all of that silence as audio is a known hallucination trigger (Whisper
    # sometimes invents repeated phrases when a chunk trails off into pure
    # silence with nothing more to transcribe). _close_segment() below keeps
    # only up to this much trailing silence in the actual audio handed to
    # ASR -- still enough that the last word isn't clipped, just not the
    # full pause-detection window. Independent of min_silence_seconds: raising
    # the pause threshold for better sentence-boundary detection no longer
    # also means feeding ASR more silence.
    vad_backend: str = "energy"             # "energy" or "webrtcvad"
    webrtcvad_aggressiveness: int = 2       # 0 (most permissive) - 3 (most aggressive filtering); only used by vad_backend="webrtcvad"
    interim_interval_seconds: float | None = None  # None = disabled; see class docstring

    _voiced_chunks: list[np.ndarray] = field(default_factory=list, init=False)
    _voiced_duration: float = field(default=0.0, init=False)
    _silence_duration: float = field(default=0.0, init=False)
    _segment_start_ts: float | None = field(default=None, init=False)
    _clock: float = field(default=0.0, init=False)
    _last_interim_voiced_duration: float = field(default=0.0, init=False)
    _vad: object = field(default=None, init=False, repr=False)
    _vad_leftover: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32), init=False, repr=False)

    def __post_init__(self) -> None:
        if self.vad_backend not in ("energy", "webrtcvad"):
            raise ValueError(f"vad_backend must be 'energy' or 'webrtcvad', got {self.vad_backend!r}")
        if self.vad_backend == "webrtcvad":
            if self.sample_rate not in (8000, 16000, 32000, 48000):
                raise ValueError(
                    f"webrtcvad only supports sample rates of 8000/16000/32000/48000 Hz, "
                    f"got {self.sample_rate}. This module's own default (16000) already "
                    f"matches what the rest of the pipeline expects."
                )
            import webrtcvad  # deferred: optional dep, only needed for this backend

            self._vad = webrtcvad.Vad(self.webrtcvad_aggressiveness)

    def _chunk_duration(self, chunk: np.ndarray) -> float:
        return len(chunk) / self.sample_rate

    def _is_voiced(self, chunk: np.ndarray) -> bool:
        if len(chunk) == 0:
            return False
        if self.vad_backend == "webrtcvad":
            return self._is_voiced_webrtcvad(chunk)
        rms = float(np.sqrt(np.mean(np.square(chunk))))
        return rms >= self.energy_threshold

    def _is_voiced_webrtcvad(self, chunk: np.ndarray) -> bool:
        """webrtcvad requires exact 10/20/30ms frames of 16-bit PCM.
        Incoming chunks (whatever size the client happens to send over the
        WebSocket) rarely divide evenly into that, so leftover samples are
        carried over and prepended to the next call rather than dropped or
        zero-padded -- no audio silently skipped from VAD's view. A chunk
        counts as voiced if ANY of its 30ms sub-frames does (30ms = the
        largest, and so fewest-frames-per-chunk, valid webrtcvad frame
        size) -- matching this class's existing bias toward not missing
        short voiced blips (min_voiced_seconds already filters those out
        downstream), rather than requiring the whole chunk to be voiced,
        which would make this backend less sensitive than the energy
        threshold it's replacing.
        """
        frame_samples = int(self.sample_rate * 0.03)
        combined = np.concatenate([self._vad_leftover, chunk])
        n_frames = len(combined) // frame_samples
        usable = combined[: n_frames * frame_samples]
        self._vad_leftover = combined[n_frames * frame_samples:].copy()

        if n_frames == 0:
            return False  # not enough buffered yet to form even one 30ms frame

        pcm16 = np.clip(usable * 32767.0, -32768, 32767).astype(np.int16)
        for i in range(n_frames):
            frame_bytes = pcm16[i * frame_samples:(i + 1) * frame_samples].tobytes()
            if self._vad.is_speech(frame_bytes, self.sample_rate):
                return True
        return False

    def should_emit_interim(self) -> bool:
        """True if interim captions are enabled and enough new voiced audio
        has accumulated since the last interim/final emission to justify
        re-transcribing the buffer so far. Doesn't close or mutate the
        segment -- call current_partial_segment() to get the audio itself,
        then mark_interim_emitted() after actually processing it."""
        if self.interim_interval_seconds is None or not self._voiced_chunks:
            return False
        return (self._voiced_duration - self._last_interim_voiced_duration) >= self.interim_interval_seconds

    def mark_interim_emitted(self) -> None:
        self._last_interim_voiced_duration = self._voiced_duration

    def current_partial_segment(self) -> AudioSegment | None:
        """A snapshot of the audio buffered so far, without closing the
        segment -- used for interim captions. Unlike _close_segment(), this
        does NOT trim trailing silence (there typically isn't much yet --
        an interim tick fires because enough new *voiced* audio arrived,
        not because a pause was detected) and does not reset any state."""
        if not self._voiced_chunks:
            return None
        samples = np.concatenate(self._voiced_chunks)
        return AudioSegment(
            samples=samples,
            sample_rate=self.sample_rate,
            start_ts=self._segment_start_ts,
            end_ts=self._clock,
        )

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
            self._last_interim_voiced_duration = 0.0
            return None
        return self._close_segment()

    def _close_segment(self) -> AudioSegment:
        samples = np.concatenate(self._voiced_chunks)
        if self._silence_duration > self.max_trailing_silence_seconds:
            trim_seconds = self._silence_duration - self.max_trailing_silence_seconds
            trim_samples = int(trim_seconds * self.sample_rate)
            if 0 < trim_samples < len(samples):
                samples = samples[:-trim_samples]
            # end_ts intentionally still reflects the FULL wall-clock span
            # (including the untrimmed silence) -- duration_seconds is used
            # elsewhere (realtime-factor reporting in evaluate_accuracy.py)
            # to mean "how long did this utterance, including its natural
            # trailing pause, actually take", which is a different question
            # from "how many audio samples got sent to ASR".
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
        self._last_interim_voiced_duration = 0.0
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
        useful for logging/tests); otherwise returns None. If interim
        captions are enabled on the segmenter (interim_interval_seconds) and
        this chunk crosses an interim tick without closing a segment, also
        runs ASR + MT + broadcast for the buffered-so-far audio as a
        non-final caption -- see AudioSegmenter's docstring for what that
        does and doesn't mean."""
        segment = self.segmenter.push(chunk)
        if segment is not None:
            return await self._process_segment(segment, is_final=True)

        if self.segmenter.should_emit_interim():
            partial = self.segmenter.current_partial_segment()
            if partial is not None:
                await self._process_segment(partial, is_final=False)
            self.segmenter.mark_interim_emitted()

        return None

    async def flush(self) -> str | None:
        """Force out and process any trailing buffered audio at session end."""
        segment = self.segmenter.flush()
        if segment is None:
            return None
        return await self._process_segment(segment, is_final=True)

    async def _process_segment(self, segment: AudioSegment, is_final: bool = True) -> str:
        transcript = await self.asr.transcribe(segment)

        # Snapshot languages once per segment: translating once per language
        # here is what keeps MT cost independent of attendee count.
        languages = list(dict.fromkeys(self.subscribed_languages()))

        async def _translate_and_broadcast(lang: str) -> None:
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
            await self.broadcast(lang, translated, is_final)

        # Fan out across subscribed languages CONCURRENTLY instead of one at
        # a time: with N distinct languages in the room, per-attendee
        # caption latency used to scale as N * (avg MT call time) -- last
        # attendee's language waited behind every other language's full
        # translate() call first. Each translate() call already hops onto
        # its own worker thread (see RealWhisperBackend/RealNLLBBackend's
        # asyncio.to_thread), and ctranslate2's Translator is explicitly
        # designed to accept concurrent calls from multiple Python threads
        # (see backends.py's inter_threads) -- so gathering these is safe
        # and turns that N * time into roughly max(time) instead. Safe
        # w.r.t. shared state too: each task only touches its own (text,
        # lang) cache key and broadcasts to its own language's subscribers,
        # and Python's GIL means the only real parallelism happening is
        # inside the C++ translate_batch call itself while it's off the
        # event loop -- nothing here needs a lock.
        #
        # return_exceptions=True is deliberate: previously, one language's
        # translate() call raising (a real possibility -- a transient MT
        # error, an unmapped language code) silently killed every other
        # language's caption for that segment too, since the old for-loop
        # would raise straight out of _process_segment before reaching the
        # remaining languages. Now a single language's failure only drops
        # that language's caption for this one segment; everyone else still
        # gets theirs.
        if languages:
            results = await asyncio.gather(
                *(_translate_and_broadcast(lang) for lang in languages),
                return_exceptions=True,
            )
            for lang, result in zip(languages, results):
                if isinstance(result, Exception):
                    print(f"[pipeline] translation/broadcast failed for {lang!r}: {result}")

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