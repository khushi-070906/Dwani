"""
Tests for translation caching: NoOpCache/ExactMatchCache (pipeline.py),
SemanticCache (translation_cache.py), and Pipeline's wiring to whichever
cache it's given.

SemanticCache is exercised with an injected deterministic `embed_fn` so this
suite stays fast and dependency-free, same rationale as test_backend.py
keeping RealWhisperBackend/RealNLLBBackend (which need real model weights)
out of its fast unit tests.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import numpy as np
import pytest

from pipeline import ExactMatchCache, FakeASRBackend, FakeTranslationBackend, NoOpCache, Pipeline, make_silence, make_tone
from translation_cache import SemanticCache


# ---------------------------------------------------------------------------
# A tiny, deterministic bag-of-words embedding for SemanticCache tests --
# no model download, but still gives "near-duplicate sentences score high,
# unrelated sentences score low" behavior so the threshold logic is
# meaningfully exercised rather than trivially always-hit/always-miss.
# ---------------------------------------------------------------------------

_VOCAB = [
    "welcome", "everyone", "to", "the", "seminar", "please", "find",
    "your", "seats", "today", "we", "will", "discuss", "attention",
    "mechanisms", "goodbye", "thank", "you", "for", "coming",
]


def bag_of_words_embed(text: str) -> np.ndarray:
    vec = np.zeros(len(_VOCAB), dtype=np.float32)
    words = text.lower().split()
    for w in words:
        if w in _VOCAB:
            vec[_VOCAB.index(w)] += 1.0
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm
    return vec


class TestExactMatchCache:
    @pytest.mark.anyio
    async def test_miss_on_empty_cache(self):
        cache = ExactMatchCache()
        assert await cache.get("hello", "hi") is None
        assert cache.misses == 1
        assert cache.hits == 0

    @pytest.mark.anyio
    async def test_hit_after_put_same_text_and_language(self):
        cache = ExactMatchCache()
        await cache.put("hello", "hi", "[hi] hello")
        assert await cache.get("hello", "hi") == "[hi] hello"
        assert cache.hits == 1

    @pytest.mark.anyio
    async def test_miss_on_different_language_same_text(self):
        cache = ExactMatchCache()
        await cache.put("hello", "hi", "[hi] hello")
        assert await cache.get("hello", "fr") is None

    @pytest.mark.anyio
    async def test_miss_on_slightly_different_text(self):
        cache = ExactMatchCache()
        await cache.put("hello there", "hi", "[hi] hello there")
        assert await cache.get("hello there!", "hi") is None


class TestNoOpCache:
    @pytest.mark.anyio
    async def test_always_misses_even_after_put(self):
        cache = NoOpCache()
        await cache.put("hello", "hi", "[hi] hello")
        assert await cache.get("hello", "hi") is None


class TestSemanticCache:
    def _cache(self, threshold: float = 0.92) -> SemanticCache:
        return SemanticCache(similarity_threshold=threshold, embed_fn=bag_of_words_embed)

    @pytest.mark.anyio
    async def test_miss_on_empty_cache(self):
        cache = self._cache()
        assert await cache.get("welcome everyone to the seminar", "hi") is None
        assert cache.stats.misses == 1

    @pytest.mark.anyio
    async def test_hit_on_identical_text(self):
        cache = self._cache()
        await cache.put("welcome everyone to the seminar", "hi", "[hi] welcome")
        result = await cache.get("welcome everyone to the seminar", "hi")
        assert result == "[hi] welcome"
        assert cache.stats.hits == 1

    @pytest.mark.anyio
    async def test_hit_on_near_duplicate_text_above_threshold(self):
        cache = self._cache(threshold=0.8)
        await cache.put("welcome everyone to the seminar", "hi", "[hi] welcome")
        # near-duplicate: same bag of words minus one word, still highly similar
        result = await cache.get("welcome everyone to seminar", "hi")
        assert result == "[hi] welcome"

    @pytest.mark.anyio
    async def test_miss_on_dissimilar_text_below_threshold(self):
        cache = self._cache(threshold=0.92)
        await cache.put("welcome everyone to the seminar", "hi", "[hi] welcome")
        result = await cache.get("thank you for coming today", "hi")
        assert result is None
        assert cache.stats.misses == 1

    @pytest.mark.anyio
    async def test_per_language_index_is_isolated(self):
        cache = self._cache()
        await cache.put("welcome everyone to the seminar", "hi", "[hi] welcome")
        # identical text, but nothing cached yet for "fr"
        assert await cache.get("welcome everyone to the seminar", "fr") is None

    @pytest.mark.anyio
    async def test_hit_rate_reported_correctly(self):
        cache = self._cache()
        await cache.get("welcome everyone to the seminar", "hi")  # miss
        await cache.put("welcome everyone to the seminar", "hi", "[hi] welcome")
        await cache.get("welcome everyone to the seminar", "hi")  # hit
        await cache.get("welcome everyone to the seminar", "hi")  # hit
        assert cache.stats.hits == 2
        assert cache.stats.misses == 1
        assert cache.stats.hit_rate == pytest.approx(2 / 3)

    @pytest.mark.anyio
    async def test_max_entries_per_language_evicts_oldest(self):
        cache = SemanticCache(
            similarity_threshold=0.99,
            max_entries_per_language=2,
            embed_fn=bag_of_words_embed,
        )
        await cache.put("welcome everyone to the seminar", "hi", "[hi] welcome")
        await cache.put("please find your seats today", "hi", "[hi] seats")
        await cache.put("we will discuss attention mechanisms", "hi", "[hi] attention")

        # the oldest entry ("welcome everyone...") should have been evicted
        assert await cache.get("welcome everyone to the seminar", "hi") is None
        # the two most recent should still hit
        assert await cache.get("please find your seats today", "hi") == "[hi] seats"
        assert await cache.get("we will discuss attention mechanisms", "hi") == "[hi] attention"

    def test_invalid_threshold_raises(self):
        with pytest.raises(ValueError):
            SemanticCache(similarity_threshold=0.0, embed_fn=bag_of_words_embed)
        with pytest.raises(ValueError):
            SemanticCache(similarity_threshold=1.5, embed_fn=bag_of_words_embed)

    def test_estimated_seconds_saved(self):
        cache = self._cache()
        cache.stats.hits = 4
        cache.record_miss_translate_seconds(0.2)
        cache.record_miss_translate_seconds(0.4)
        assert cache.stats.mean_translate_seconds == pytest.approx(0.3)
        assert cache.stats.estimated_seconds_saved == pytest.approx(1.2)


# ---------------------------------------------------------------------------
# Pipeline wiring: cache hits should skip the real translator entirely
# ---------------------------------------------------------------------------

class TestPipelineCacheWiring:
    @pytest.mark.anyio
    async def test_defaults_to_noop_cache_translator_always_called(self):
        asr = FakeASRBackend(default_transcript="hello everyone")
        translator = FakeTranslationBackend()
        broadcast = AsyncMock()
        subscribed = lambda: ["hi"]

        pipeline = Pipeline(asr, translator, broadcast, subscribed)
        pipeline.segmenter.push(make_tone(0.5))
        await pipeline.handle_audio_chunk(make_silence(1.0))
        pipeline.segmenter.push(make_tone(0.5))
        await pipeline.handle_audio_chunk(make_silence(1.0))

        # same transcript twice, but no cache given -> translator called both times
        assert len(translator.calls) == 2

    @pytest.mark.anyio
    async def test_exact_match_cache_skips_translator_on_repeat_transcript(self):
        asr = FakeASRBackend(default_transcript="hello everyone")
        translator = FakeTranslationBackend()
        broadcast = AsyncMock()
        subscribed = lambda: ["hi"]
        cache = ExactMatchCache()

        pipeline = Pipeline(asr, translator, broadcast, subscribed, cache=cache)
        pipeline.segmenter.push(make_tone(0.5))
        await pipeline.handle_audio_chunk(make_silence(1.0))
        pipeline.segmenter.push(make_tone(0.5))
        await pipeline.handle_audio_chunk(make_silence(1.0))

        # translator only called on the first (cache-miss) segment
        assert len(translator.calls) == 1
        broadcast.assert_any_await("hi", "[hi] hello everyone", True)
        assert broadcast.await_count == 2

    @pytest.mark.anyio
    async def test_semantic_cache_skips_translator_on_near_duplicate_transcript(self):
        asr = FakeASRBackend(default_transcript="welcome everyone to the seminar")
        translator = FakeTranslationBackend()
        broadcast = AsyncMock()
        subscribed = lambda: ["hi"]
        cache = SemanticCache(similarity_threshold=0.8, embed_fn=bag_of_words_embed)

        pipeline = Pipeline(asr, translator, broadcast, subscribed, cache=cache)
        pipeline.segmenter.push(make_tone(0.5))
        await pipeline.handle_audio_chunk(make_silence(1.0))

        # second segment: near-duplicate transcript (still resolves to the
        # same FakeASRBackend.default_transcript here, which is the common
        # case of a presenter re-reading a slide verbatim -- see the
        # ExactMatchCache test above for that path, and the SemanticCache
        # unit tests above for genuinely-different near-duplicate wording)
        pipeline.segmenter.push(make_tone(0.5))
        await pipeline.handle_audio_chunk(make_silence(1.0))

        assert len(translator.calls) == 1
        assert cache.stats.hits == 1

    @pytest.mark.anyio
    async def test_cache_is_isolated_per_language(self):
        asr = FakeASRBackend(default_transcript="hello everyone")
        translator = FakeTranslationBackend()
        broadcast = AsyncMock()
        subscribed = lambda: ["hi", "fr"]
        cache = ExactMatchCache()

        pipeline = Pipeline(asr, translator, broadcast, subscribed, cache=cache)
        pipeline.segmenter.push(make_tone(0.5))
        await pipeline.handle_audio_chunk(make_silence(1.0))

        # first segment: both "hi" and "fr" are misses (different languages)
        assert len(translator.calls) == 2
        called_langs = {lang for _, lang in translator.calls}
        assert called_langs == {"hi", "fr"}