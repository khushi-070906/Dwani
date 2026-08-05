"""
Tests for decision_engine.py (ITDE) -- the term-consistency veto rule and
the adaptive per-language threshold, exercised with a deterministic embed_fn
(SemanticCache) and a real Glossary (dependency-free, no model needed).

The embed_fn below is engineered, not incidental: two specific sentences
are given a fixed, known cosine similarity of 0.9 (the "Transformer" /
"Diffusion Model" term-swap scenario from decision_engine.py's module
docstring), while every other text gets a high-dimensional deterministic
pseudo-random vector, which is near-orthogonal to everything else with
overwhelming probability -- giving reliable, reproducible cache misses for
unrelated text without needing real embeddings.
"""

from __future__ import annotations

import hashlib

import numpy as np
import pytest

from decision_engine import DecisionAction, IntelligentTranslationDecisionEngine
from glossary import Glossary, GlossaryTerm
from pipeline import FakeTranslationBackend
from translation_cache import SemanticCache

DIM = 50
TRANSFORMER_SENTENCE = "The Transformer uses gradient checkpointing to reduce memory."
DIFFUSION_SENTENCE = "The Diffusion Model uses gradient checkpointing to reduce memory."


def _hash_vec(text: str, dim: int = DIM) -> np.ndarray:
    seed = int(hashlib.sha256(text.encode()).hexdigest(), 16) % (2**32)
    rng = np.random.RandomState(seed)
    v = rng.normal(size=dim).astype(np.float32)
    return v / np.linalg.norm(v)


def embed_fn(text: str) -> np.ndarray:
    if text == TRANSFORMER_SENTENCE:
        v = np.zeros(DIM, dtype=np.float32)
        v[0] = 1.0
        return v
    if text == DIFFUSION_SENTENCE:
        v = np.zeros(DIM, dtype=np.float32)
        v[0] = 0.9
        v[1] = float(np.sqrt(1 - 0.9 ** 2))
        return v
    return _hash_vec(text)


def make_glossary() -> Glossary:
    return Glossary([GlossaryTerm(term="Transformer"), GlossaryTerm(term="Diffusion Model")])


def make_engine(**kwargs) -> tuple[IntelligentTranslationDecisionEngine, FakeTranslationBackend]:
    inner = FakeTranslationBackend()
    cache = SemanticCache(embed_fn=embed_fn, similarity_threshold=0.5)  # cache's own threshold
    # overridden per-call by ITDE's adaptive threshold -- see decision_engine.py
    engine = IntelligentTranslationDecisionEngine(inner, cache, make_glossary(), **kwargs)
    return engine, inner


class TestCacheMissAndHit:
    @pytest.mark.anyio
    async def test_cache_miss_calls_inner_translator_and_stores(self):
        engine, inner = make_engine()
        result = await engine.translate(TRANSFORMER_SENTENCE, "hi")
        assert result == "[hi] " + TRANSFORMER_SENTENCE
        assert len(inner.calls) == 1
        assert engine.log[-1].action == DecisionAction.CACHE_MISS

    @pytest.mark.anyio
    async def test_repeated_identical_text_is_a_cache_hit_second_time(self):
        engine, inner = make_engine()
        await engine.translate(TRANSFORMER_SENTENCE, "hi")
        result = await engine.translate(TRANSFORMER_SENTENCE, "hi")
        assert len(inner.calls) == 1  # inner translator not called again
        assert result == "[hi] " + TRANSFORMER_SENTENCE
        assert engine.log[-1].action == DecisionAction.CACHE_HIT

    @pytest.mark.anyio
    async def test_different_languages_are_independent(self):
        engine, inner = make_engine()
        await engine.translate(TRANSFORMER_SENTENCE, "hi")
        await engine.translate(TRANSFORMER_SENTENCE, "fr")
        # same text, different language -> both misses, inner called twice
        assert len(inner.calls) == 2


class TestTermConsistencyVeto:
    @pytest.mark.anyio
    async def test_similar_text_with_different_glossary_terms_is_vetoed(self):
        engine, inner = make_engine(initial_threshold=0.85)
        await engine.translate(TRANSFORMER_SENTENCE, "hi")  # miss, cached
        # DIFFUSION_SENTENCE has 0.9 similarity to TRANSFORMER_SENTENCE
        # (above the 0.85 threshold) but mentions "Diffusion Model" instead
        # of "Transformer" -- the veto rule should catch this even though
        # embedding similarity alone would have called it a hit.
        result = await engine.translate(DIFFUSION_SENTENCE, "hi")
        assert result == "[hi] " + DIFFUSION_SENTENCE  # freshly translated, not the cached "Transformer" text
        assert len(inner.calls) == 2  # inner translator called for both sentences
        assert engine.log[-1].action == DecisionAction.CACHE_VETOED_TERM_MISMATCH

    @pytest.mark.anyio
    async def test_veto_rate_reports_correct_fraction(self):
        engine, inner = make_engine(initial_threshold=0.85)
        await engine.translate(TRANSFORMER_SENTENCE, "hi")   # miss
        await engine.translate(DIFFUSION_SENTENCE, "hi")     # veto
        await engine.translate(TRANSFORMER_SENTENCE, "hi")   # hit (back to the original text)
        # cache_lookups = hit + veto = 2 candidates; 1 of them was vetoed
        assert engine.veto_rate("hi") == pytest.approx(0.5)


class TestAdaptiveThreshold:
    @pytest.mark.anyio
    async def test_veto_tightens_threshold(self):
        engine, inner = make_engine(initial_threshold=0.85, adjustment_step=0.02)
        before = engine.threshold_for("hi")
        await engine.translate(TRANSFORMER_SENTENCE, "hi")
        await engine.translate(DIFFUSION_SENTENCE, "hi")  # triggers a veto
        after = engine.threshold_for("hi")
        assert after == pytest.approx(before + 0.02)

    @pytest.mark.anyio
    async def test_threshold_does_not_exceed_max(self):
        engine, inner = make_engine(initial_threshold=0.97, max_threshold=0.98, adjustment_step=0.05)
        await engine.translate(TRANSFORMER_SENTENCE, "hi")
        await engine.translate(DIFFUSION_SENTENCE, "hi")  # would push threshold to 1.02 uncapped
        assert engine.threshold_for("hi") <= 0.98

    @pytest.mark.anyio
    async def test_run_of_clean_misses_loosens_threshold(self):
        engine, inner = make_engine(initial_threshold=0.85, adjustment_step=0.02, misses_before_loosening=3)
        before = engine.threshold_for("hi")
        # three genuinely unrelated sentences -- each a clean miss, no vetoes
        for i in range(3):
            await engine.translate(f"completely unrelated filler sentence number {i}", "hi")
        after = engine.threshold_for("hi")
        assert after == pytest.approx(before - 0.02)

    @pytest.mark.anyio
    async def test_threshold_does_not_go_below_min(self):
        engine, inner = make_engine(
            initial_threshold=0.76, min_threshold=0.75, adjustment_step=0.05, misses_before_loosening=2
        )
        for i in range(2):
            await engine.translate(f"unrelated sentence {i}", "hi")
        assert engine.threshold_for("hi") >= 0.75

    @pytest.mark.anyio
    async def test_thresholds_are_independent_per_language(self):
        engine, inner = make_engine(initial_threshold=0.85, adjustment_step=0.02)
        await engine.translate(TRANSFORMER_SENTENCE, "hi")
        await engine.translate(DIFFUSION_SENTENCE, "hi")  # veto tightens "hi" only
        assert engine.threshold_for("hi") > engine.threshold_for("fr")


class TestDecisionLog:
    @pytest.mark.anyio
    async def test_log_grows_by_one_entry_per_translate_call(self):
        engine, inner = make_engine()
        await engine.translate(TRANSFORMER_SENTENCE, "hi")
        await engine.translate(TRANSFORMER_SENTENCE, "hi")
        assert len(engine.log) == 2

    @pytest.mark.anyio
    async def test_log_entries_record_query_terms(self):
        engine, inner = make_engine()
        await engine.translate(TRANSFORMER_SENTENCE, "hi")
        assert engine.log[-1].query_terms == frozenset({"Transformer"})
