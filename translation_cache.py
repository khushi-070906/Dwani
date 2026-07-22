"""
Semantic translation cache -- extends the LDST pipeline (pipeline.py,
Section 4.3) with the "Option 1: Semantic Translation Cache" idea: before
calling the real TranslationBackend, check whether a semantically similar
transcript has already been translated into the same target language this
session, and reuse that translation instead of re-running NLLB.

    Speech -> Whisper -> Sentence Embedding -> Similarity Search
                                                  |-- hit  -> reuse
                                                  `-- miss -> translate + store

Research question this is designed to let you answer (Section 8):
    "Can semantic translation caching reduce latency and computation while
    maintaining translation quality?"

Metrics `CacheStats` (below) makes directly measurable:
    - cache hit rate
    - translation time saved (estimated from the wall-clock cost of the
      real translator calls this cache *did* make, averaged over misses,
      and assumed roughly representative of what each hit avoided)
CPU/memory overhead is environment-dependent and better measured externally
(e.g. wrapping a live session with `psutil`) than inferred from this module.

This class lives in its own file rather than in pipeline.py for the same
reason RealWhisperBackend/RealNLLBBackend live in backends.py rather than
pipeline.py: importing pipeline.py must never require sentence-transformers
to be installed, so Pipeline's wiring tests (test_pipeline.py) and the
default NoOpCache/ExactMatchCache stay fast and dependency-free. The heavy
import (sentence_transformers) is deferred to SemanticCache.__init__ rather
than done at module import time, same as backends.py.

-----------------------------------------------------------------------------
Setup
-----------------------------------------------------------------------------

    pip install sentence-transformers --break-system-packages

First use downloads and caches the embedding model (default
"all-MiniLM-L6-v2", ~80MB) from Hugging Face -- do this once while you still
have internet, same caveat as backends.py's Whisper/NLLB models.

-----------------------------------------------------------------------------
Wiring into server.py
-----------------------------------------------------------------------------

    from translation_cache import SemanticCache
    from pipeline import Pipeline

    pipeline = Pipeline(
        asr=RealWhisperBackend(model_size="small"),
        translator=RealNLLBBackend(model_dir="nllb-200-ct2"),
        broadcast=broadcast_caption,
        subscribed_languages=lambda: subscribers.keys(),
        cache=SemanticCache(),
    )

-----------------------------------------------------------------------------
Cross-session persistence (Section 6.3)
-----------------------------------------------------------------------------

By default the cache is purely in-memory and session-scoped, exactly as
above. Passing `storage_path` makes it load entries from a prior session's
save() at construction time, and adds a `save()` method to call when the
session ends -- closing the "resets to zero every session" gap without any
network path, server, or cross-device sync: it's still one JSON file on the
presenter's own disk.

    cache = SemanticCache(storage_path="~/.ldst/memory/cache.json")
    ...
    # in server.py's host_socket, in the `finally` block, after flush():
    cache.save()

`CacheStats.cross_session_hits` (a subset of `.hits`) tracks how many hits
matched an entry carried over from a previous session specifically, which
is the number Section 7.6's cross-session evaluation reports.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import numpy as np


@dataclass
class CacheStats:
    """Running counters for Section 8's cache-hit-rate / latency-reduction
    evaluation. Read `hit_rate` and `estimated_seconds_saved` directly off a
    live SemanticCache's `.stats` attribute at the end of a session."""

    hits: int = 0
    misses: int = 0
    # Of `hits` above, how many matched an entry that was loaded from disk
    # (i.e. carried over from a *previous* session) rather than one added
    # during the current process's lifetime. Only ever nonzero when
    # SemanticCache is constructed with storage_path -- see Section 6.3.
    cross_session_hits: int = 0
    total_lookup_seconds: float = 0.0
    # Wall-clock time of real translator.translate() calls made *after* a
    # cache miss, recorded via record_miss_translate_seconds(). Used to
    # estimate what each hit avoided -- an actual A/B run (cache on vs off)
    # is the more rigorous way to report this in the paper, but this gives
    # a live-session estimate for free.
    miss_translate_seconds: list[float] = field(default_factory=list)

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0

    @property
    def mean_translate_seconds(self) -> float:
        if not self.miss_translate_seconds:
            return 0.0
        return sum(self.miss_translate_seconds) / len(self.miss_translate_seconds)

    @property
    def estimated_seconds_saved(self) -> float:
        """hits * (average real-translation cost observed on misses) --
        an estimate, not a measurement, since a hit never actually pays that
        cost to compare against."""
        return self.hits * self.mean_translate_seconds


class SemanticCache:
    """TranslationCache (see pipeline.py's protocol) backed by sentence
    embeddings + cosine similarity, one similarity index per target
    language (queries never need to compare across languages).

    A transcript is a "hit" against the most similar transcript previously
    cached for that language if their cosine similarity is at least
    `similarity_threshold`; the *original* translation for that near-match
    is reused verbatim rather than re-translating.

    `embed_fn`, if given, replaces the sentence-transformers model with any
    `str -> np.ndarray` callable (the array should be L2-normalized so a
    dot product is a cosine similarity) -- this is what lets
    test_translation_cache.py exercise the caching/threshold/eviction logic
    below with a deterministic, dependency-free embedding instead of
    downloading a real model, exactly like backends.py's RealWhisperBackend
    tests stay out of test_backend.py's fast suite.
    """

    def __init__(
        self,
        model_name: str = "all-MiniLM-L6-v2",
        similarity_threshold: float = 0.92,
        max_entries_per_language: int = 2000,
        embed_fn: Optional[Callable[[str], np.ndarray]] = None,
        storage_path: str | Path | None = None,
    ) -> None:
        if embed_fn is not None:
            self._embed_fn = embed_fn
        else:
            from sentence_transformers import SentenceTransformer  # deferred: heavy, optional dep

            model = SentenceTransformer(model_name)

            def _embed_fn(text: str) -> np.ndarray:
                return np.asarray(
                    model.encode(text, normalize_embeddings=True), dtype=np.float32
                )

            self._embed_fn = _embed_fn

        if not 0.0 < similarity_threshold <= 1.0:
            raise ValueError("similarity_threshold must be in (0, 1]")

        self._threshold = similarity_threshold
        self._max_entries = max_entries_per_language
        # lang -> (embeddings [N, D], translations, source transcripts)
        self._index: dict[str, tuple[np.ndarray, list[str], list[str]]] = {}
        self.stats = CacheStats()

        # Section 6.3: cross-session persistence. None (the default)
        # preserves the exact Section 6.1 behavior -- a fresh, empty,
        # session-scoped cache with no disk I/O at all. When set, entries
        # from a prior session's save() are loaded immediately below, and
        # _loaded_counts records, per language, how many of the current
        # entries came from disk -- this is what lets stats.hits be split
        # into cross_session_hits vs this-session hits without needing a
        # second index.
        self._storage_path = Path(storage_path) if storage_path else None
        self._loaded_counts: dict[str, int] = {}
        if self._storage_path is not None:
            self._load()

    async def get(self, text: str, lang: str) -> Optional[str]:
        return await asyncio.to_thread(self._get_sync, text, lang)

    async def get_with_source(self, text: str, lang: str) -> Optional[tuple[str, str]]:
        """Same lookup as get(), but also returns the *stored* source text
        that actually matched -- which, for a near-duplicate hit, is not
        `text` itself. Exists for callers like decision_engine.py's ITDE
        that need to know which cached entry was matched (e.g. to compare
        glossary terms between the query and the matched entry), not just
        the translation it produced. get() is unaffected and remains the
        simpler, more common-case interface."""
        return await asyncio.to_thread(self._get_with_source_sync, text, lang)

    async def put(self, text: str, lang: str, translation: str) -> None:
        await asyncio.to_thread(self._put_sync, text, lang, translation)

    def record_miss_translate_seconds(self, seconds: float) -> None:
        """Call this with the wall-clock time a real translator.translate()
        call took, right after a miss -- feeds CacheStats.estimated_seconds_saved.
        Optional: Pipeline itself doesn't call this (it doesn't measure
        translator timing), so wire it in yourself if you want the live
        estimate; see evaluate_accuracy.py for an example of measuring
        translator calls directly instead, which is the more rigorous path
        for the numbers that actually go in the paper.
        """
        self.stats.miss_translate_seconds.append(seconds)

    # -- cross-session persistence (Section 6.3) -----------------------------

    def _load(self) -> None:
        """Populate `self._index` from `self._storage_path`, if it exists.
        Called once, from __init__, before this cache has served any
        lookups this session -- so every entry loaded here is, by
        definition, from a previous session."""
        if not self._storage_path.exists():
            return
        raw = json.loads(self._storage_path.read_text(encoding="utf-8"))
        for lang, entries in raw.items():
            if not entries:
                continue
            embeddings = np.asarray([e["embedding"] for e in entries], dtype=np.float32)
            translations = [e["translation"] for e in entries]
            sources = [e["source"] for e in entries]
            self._index[lang] = (embeddings, translations, sources)
            self._loaded_counts[lang] = len(entries)

    def save(self, path: str | Path | None = None) -> None:
        """Write the current cache contents to disk so a future SemanticCache
        constructed with the same storage_path picks up where this one left
        off. Call this when a presenter's session ends (see server.py's
        host_socket, which calls it in its `finally` block alongside
        pipeline.flush()).

        `path` overrides `storage_path` for this call only; if neither is
        set, this is a silent no-op rather than an error, so server.py can
        call `.save()` unconditionally on any cache without needing to know
        in advance whether persistence was configured for it -- the same
        "no-op is a normal state" convention as NoOpCache/ExactMatchCache's
        default behavior.
        """
        target = Path(path) if path is not None else self._storage_path
        if target is None:
            return

        payload = {
            lang: [
                {"embedding": embedding.tolist(), "translation": translation, "source": source}
                for embedding, translation, source in zip(embeddings, translations, sources)
            ]
            for lang, (embeddings, translations, sources) in self._index.items()
        }

        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            os.replace(tmp_path, target)
        except BaseException:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise

    # -- internal, synchronous (run off the event loop via asyncio.to_thread) --

    def _get_sync(self, text: str, lang: str) -> Optional[str]:
        result = self._lookup_sync(text, lang)
        return result[0] if result is not None else None

    def _get_with_source_sync(self, text: str, lang: str) -> Optional[tuple[str, str]]:
        return self._lookup_sync(text, lang)

    def _lookup_sync(self, text: str, lang: str) -> Optional[tuple[str, str]]:
        """Shared implementation for _get_sync/_get_with_source_sync --
        returns (translation, matched_source_text) on a hit, else None.
        Splitting this out means get() and get_with_source() can never
        disagree about what counts as a hit, only about how much of the
        match they report back to the caller."""
        start = time.monotonic()
        entry = self._index.get(lang)
        if entry is None or len(entry[1]) == 0:
            self.stats.misses += 1
            self.stats.total_lookup_seconds += time.monotonic() - start
            return None

        embeddings, translations, sources = entry
        query = self._embed_fn(text)
        similarities = embeddings @ query  # both sides normalized -> cosine similarity
        best_idx = int(np.argmax(similarities))
        best_similarity = float(similarities[best_idx])
        self.stats.total_lookup_seconds += time.monotonic() - start

        if best_similarity >= self._threshold:
            self.stats.hits += 1
            if best_idx < self._loaded_counts.get(lang, 0):
                self.stats.cross_session_hits += 1
            return translations[best_idx], sources[best_idx]

        self.stats.misses += 1
        return None

    def _put_sync(self, text: str, lang: str, translation: str) -> None:
        embedding = self._embed_fn(text)
        embeddings, translations, sources = self._index.get(
            lang, (np.zeros((0, embedding.shape[0]), dtype=np.float32), [], [])
        )
        embeddings = np.vstack([embeddings, embedding[None, :]])
        translations = translations + [translation]
        sources = sources + [text]

        # Bound memory and per-lookup cost -- drop the oldest entries first,
        # same "recent context matters most" assumption AudioSegmenter's
        # max_segment_seconds makes for a live, unbounded session.
        if len(translations) > self._max_entries:
            overflow = len(translations) - self._max_entries
            embeddings = embeddings[overflow:]
            translations = translations[overflow:]
            sources = sources[overflow:]
            # Loaded (disk-carried-over) entries are always at the front of
            # the list -- they're appended first, in _load(), before any
            # entry from this session -- so eviction removes them first.
            # Shrink the count accordingly so cross-session-hit tracking
            # above stays accurate after eviction.
            if lang in self._loaded_counts:
                self._loaded_counts[lang] = max(0, self._loaded_counts[lang] - overflow)

        self._index[lang] = (embeddings, translations, sources)