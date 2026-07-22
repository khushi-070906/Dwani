"""
Intelligent Translation Decision Engine (ITDE).

SemanticCache (translation_cache.py) decides whether to reuse a translation
purely on embedding similarity. Glossary (glossary.py) decides which literal
terms to protect. Neither one, on its own, catches a specific and genuinely
dangerous failure mode for a *technical* talk: two sentences that embed as
near-duplicates while actually being about two different technical things --

    "The Transformer uses gradient checkpointing to reduce memory."
    "The Diffusion Model uses gradient checkpointing to reduce memory."

These can sit well above a typical similarity threshold (same structure,
same rare words, one swapped noun) while being wrong to treat as
interchangeable in a room full of people who came specifically to hear the
difference. ITDE sits between SemanticCache and the translator and adds one
governing rule: a cache hit is only trusted if the glossary terms detected
in the query match the glossary terms recorded against the cached entry.
If they don't match, the hit is vetoed and the segment is translated fresh,
regardless of how high the embedding similarity was.

    transcript -> SemanticCache lookup -> similarity >= threshold?
                                                |
                                    yes -> glossary terms match cached entry's terms?
                                                |                    |
                                              yes -> CACHE_HIT      no -> VETO, retranslate
                                                |
                                    no -> CACHE_MISS, retranslate

ITDE additionally tunes each language's similarity threshold adaptively
over the session rather than leaving it fixed: a veto (a hit that looked
right on embedding similarity but turned out to be about the wrong term) is
evidence the current threshold is too loose, so it nudges the threshold up
a little; a long run of misses with no vetoes is weak evidence the
threshold is too strict, so it nudges the threshold down a little,
bounded within [min_threshold, max_threshold]. This is a small, bandit-style
control loop, not a full contextual bandit -- it needs no training data and
converges purely from the session's own veto/hit/miss counts.

Every decision is logged (DecisionRecord) with the action taken, the
similarity score, the threshold in force at the time, and the term sets
compared, so a full session's decisions can be replayed for the paper's
evaluation section (fraction of would-be cache hits vetoed, threshold
trajectory over the session, hit rate with vs without the veto rule).

-----------------------------------------------------------------------------
Wiring into server.py
-----------------------------------------------------------------------------

    from decision_engine import IntelligentTranslationDecisionEngine
    from translation_cache import SemanticCache
    from glossary import Glossary
    from backends import RealNLLBBackend

    engine = IntelligentTranslationDecisionEngine(
        inner=RealNLLBBackend(model_dir="nllb-200-ct2"),
        cache=SemanticCache(storage_path="~/.ldst/memory/cache.json"),
        glossary=Glossary.load("glossary.json"),
    )
    pipeline = Pipeline(
        asr=RealWhisperBackend(model_size="small"),
        translator=engine,          # satisfies pipeline.py's TranslationBackend protocol
        broadcast=broadcast_caption,
        subscribed_languages=lambda: subscribers.keys(),
        # leave Pipeline's own cache= unset (NoOpCache) -- ITDE owns caching
        # internally so it can veto a hit; a second cache layer outside it
        # would just re-introduce the exact failure mode ITDE exists to catch.
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from glossary import Glossary
    from pipeline import TranslationBackend
    from translation_cache import SemanticCache


class DecisionAction(str, Enum):
    CACHE_HIT = "cache_hit"
    CACHE_MISS = "cache_miss"
    CACHE_VETOED_TERM_MISMATCH = "cache_vetoed_term_mismatch"


@dataclass
class DecisionRecord:
    """One row of ITDE's decision log -- the unit evaluate_itde.py (or a
    notebook) replays to compute veto rate, hit rate with vs without the
    veto rule, and the threshold trajectory over a session."""

    text: str
    lang: str
    action: DecisionAction
    similarity: Optional[float]
    threshold_in_force: float
    query_terms: frozenset
    cached_terms: Optional[frozenset]


@dataclass
class _ThresholdState:
    value: float
    hits: int = 0
    misses: int = 0
    vetoes: int = 0


class IntelligentTranslationDecisionEngine:
    """TranslationBackend-compatible wrapper (drop-in for Pipeline's
    `translator=`) that governs SemanticCache with a term-consistency veto
    and an adaptive per-language similarity threshold. See this module's
    docstring for the decision rule and the adaptive-threshold rule.
    """

    def __init__(
        self,
        inner: "TranslationBackend",
        cache: "SemanticCache",
        glossary: "Glossary",
        initial_threshold: float = 0.88,
        min_threshold: float = 0.75,
        max_threshold: float = 0.98,
        adjustment_step: float = 0.02,
        misses_before_loosening: int = 20,
    ) -> None:
        """`initial_threshold` starts slightly below SemanticCache's own
        default (0.92) deliberately: the veto rule is what keeps a looser
        threshold safe, since embedding similarity alone no longer has to
        do all the precision work -- term consistency backs it up. Loosening
        the base threshold a little is what lets the adaptive loop actually
        have room to move in both directions instead of only ever
        tightening from an already-strict starting point.
        """
        self._inner = inner
        self._cache = cache
        self._glossary = glossary
        self._min_threshold = min_threshold
        self._max_threshold = max_threshold
        self._step = adjustment_step
        self._misses_before_loosening = misses_before_loosening

        self._thresholds: dict[str, _ThresholdState] = {}
        self._initial_threshold = initial_threshold
        # (query_text, lang) -> frozenset of glossary terms found in it, so a
        # cached entry's term set can be looked up again when a *later*
        # segment's cache lookup matches against it -- avoids re-running
        # glossary.protect() on every historical entry on every lookup.
        self._term_sets_by_query: dict[tuple[str, str], frozenset] = {}

        self.log: list[DecisionRecord] = []

    def _threshold_state(self, lang: str) -> _ThresholdState:
        if lang not in self._thresholds:
            self._thresholds[lang] = _ThresholdState(value=self._initial_threshold)
        return self._thresholds[lang]

    def _terms_in(self, text: str) -> frozenset:
        _protected, placeholder_map = self._glossary.protect(text)
        return frozenset(gt.term for gt, _matched in placeholder_map.values())

    async def translate(self, text: str, target_lang: str) -> str:
        state = self._threshold_state(target_lang)
        query_terms = self._terms_in(text)

        # Temporarily point the cache at this decision's threshold. This
        # relies on SemanticCache exposing a mutable `_threshold` -- an
        # internal, not a public API, accepted here because ITDE and
        # SemanticCache are designed to be used together (see this
        # module's docstring) rather than as arbitrary interchangeable
        # parts; a future SemanticCache.set_threshold() would be the
        # cleaner seam if this needs to be public later.
        original_threshold = self._cache._threshold
        self._cache._threshold = state.value
        try:
            hit = await self._cache.get_with_source(text, target_lang)
        finally:
            self._cache._threshold = original_threshold

        if hit is None:
            translated = await self._inner.translate(text, target_lang)
            await self._cache.put(text, target_lang, translated)
            self._term_sets_by_query[(text, target_lang)] = query_terms
            self._record_miss(state, text, target_lang, query_terms)
            return translated

        cached, matched_source = hit
        cached_terms = self._term_sets_by_query.get((matched_source, target_lang))
        if cached_terms is not None and cached_terms != query_terms:
            # Embedding similarity said "reuse", term consistency says
            # "these are about different things" -- trust the veto.
            translated = await self._inner.translate(text, target_lang)
            await self._cache.put(text, target_lang, translated)
            self._term_sets_by_query[(text, target_lang)] = query_terms
            self._record_veto(state, text, target_lang, query_terms, cached_terms)
            return translated

        self._record_hit(state, text, target_lang, query_terms, cached_terms)
        return cached

    # -- adaptive threshold bookkeeping --------------------------------

    def _record_hit(self, state, text, lang, query_terms, cached_terms) -> None:
        state.hits += 1
        self.log.append(DecisionRecord(
            text=text, lang=lang, action=DecisionAction.CACHE_HIT,
            similarity=None, threshold_in_force=state.value,
            query_terms=query_terms, cached_terms=cached_terms,
        ))

    def _record_miss(self, state, text, lang, query_terms) -> None:
        state.misses += 1
        self.log.append(DecisionRecord(
            text=text, lang=lang, action=DecisionAction.CACHE_MISS,
            similarity=None, threshold_in_force=state.value,
            query_terms=query_terms, cached_terms=None,
        ))
        # A long run of misses with no vetoes at all is weak evidence the
        # threshold is stricter than it needs to be for this session's
        # actual speech pattern -- loosen it a step, bounded. This only
        # fires on a clean run (reset by any veto or hit) so a single
        # early miss streak right after a veto doesn't immediately undo
        # the tightening that veto just caused.
        if state.misses >= self._misses_before_loosening and state.vetoes == 0 and state.hits == 0:
            state.value = max(self._min_threshold, round(state.value - self._step, 4))
            state.misses = 0

    def _record_veto(self, state, text, lang, query_terms, cached_terms) -> None:
        state.vetoes += 1
        state.misses = 0
        state.hits = 0
        self.log.append(DecisionRecord(
            text=text, lang=lang, action=DecisionAction.CACHE_VETOED_TERM_MISMATCH,
            similarity=None, threshold_in_force=state.value,
            query_terms=query_terms, cached_terms=cached_terms,
        ))
        # A veto is direct evidence this threshold let through a hit it
        # shouldn't have -- tighten immediately, bounded.
        state.value = min(self._max_threshold, round(state.value + self._step, 4))

    # -- evaluation hooks -------------------------------------------------

    def threshold_for(self, lang: str) -> float:
        return self._threshold_state(lang).value

    def veto_rate(self, lang: Optional[str] = None) -> float:
        records = [r for r in self.log if lang is None or r.lang == lang]
        cache_lookups = [r for r in records if r.action != DecisionAction.CACHE_MISS]
        if not cache_lookups:
            return 0.0
        vetoes = sum(1 for r in cache_lookups if r.action == DecisionAction.CACHE_VETOED_TERM_MISMATCH)
        return vetoes / len(cache_lookups)
