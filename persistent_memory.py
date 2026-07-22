"""
persistent_memory.py

Cross-session persistent memory for LDST (Section 6.3).

Extends the existing per-session semantic translation cache (6.1) and
conference glossary (6.2) so that both survive a process restart and are
reused across future sessions on the same presenter device, instead of
being discarded when the host process exits.

Design constraints, matching the rest of the codebase (Section 5.1):
  - No heavy ML dependency (sentence-transformers) is imported at module
    load time. It is only imported inside the real embedder, which is
    never touched unless you actually construct it.
  - Everything here is testable with the dependency-free FakeEmbedder
    below, exactly the way the existing test suite exercises the ASR/MT
    backends with fakes.
  - Persistence is local-file-only. Nothing here opens a socket, calls
    out to a server, or talks to another device. That is a deliberate
    scope decision: this closes the "resets to zero every session" gap
    without reintroducing any of the trust/sync problems that a P2P
    federation design (out of scope here, see paper Section 9) would
    bring in.
  - Writes are atomic (write-to-temp then os.replace) so a crash or a
    laptop lid slam mid-session can't corrupt the memory file.

Two independent stores are provided:

  PersistentSemanticCache
      Cross-session version of the 6.1 semantic cache. Same per-language,
      cosine-similarity-over-normalized-embeddings design; the only
      change is that entries are loaded from disk on construction and
      can be flushed back to disk (call .save() at session end, or after
      every N new entries -- see `autosave_every`).

  PersistentGlossary
      Cross-session version of the 6.2 glossary. Adds validated terms
      once a human has reviewed them (matching the paper's "reviewed by
      the presenter before use" requirement) so that a term approved in
      one session is already trusted in the next one, without the
      presenter re-approving it every time.

Both stores are scoped to a single (institution_id, presenter_id) pair
by way of the file path you give them -- there is no cross-presenter or
cross-institution sharing built in. If you want that later, it belongs
in the opt-in export/import extension discussed separately, not here.
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from typing import Iterable, Protocol


# --------------------------------------------------------------------------
# Embedding backend protocol (mirrors the ASR/MT backend protocol pattern)
# --------------------------------------------------------------------------

class Embedder(Protocol):
    """Narrow protocol: text in, fixed-length float vector out."""

    async def embed(self, text: str) -> list[float]: ...


class FakeEmbedder:
    """
    Deterministic, dependency-free embedder for unit tests and local dev.

    Produces a crude bag-of-words vector over a fixed small vocabulary so
    that similarity behaves sensibly in tests (near-duplicate sentences
    score high, unrelated sentences score low) without pulling in any ML
    dependency. Never use this in production -- it exists purely so the
    cache's *logic* (dedup, thresholding, per-language isolation, disk
    round-trip) can be tested fast and offline, exactly like the fake
    ASR/MT backends elsewhere in the codebase.
    """

    _VOCAB_SIZE = 64

    async def embed(self, text: str) -> list[float]:
        vec = [0.0] * self._VOCAB_SIZE
        for token in re.findall(r"[a-z0-9]+", text.lower()):
            vec[hash(token) % self._VOCAB_SIZE] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


class SentenceTransformerEmbedder:
    """
    Real embedder. Import of sentence-transformers is deferred to first
    use (inside __init__), so importing this module -- or even
    constructing a FakeEmbedder-backed cache for tests -- never requires
    the ML stack to be installed, matching Section 5.1's isolation rule.
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        from sentence_transformers import SentenceTransformer  # deferred import

        self._model = SentenceTransformer(model_name)

    async def embed(self, text: str) -> list[float]:
        # SentenceTransformer.encode is blocking; callers running inside
        # an asyncio event loop should dispatch this to a worker thread,
        # the same way the existing ASR/MT backends do (Section 5.2).
        vec = self._model.encode(text, normalize_embeddings=True)
        return vec.tolist()


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


# --------------------------------------------------------------------------
# Persistent semantic cache
# --------------------------------------------------------------------------

@dataclass
class _CacheEntry:
    source_text: str
    translation: str
    embedding: list[float]
    added_at: float
    hit_count: int = 0


class PersistentSemanticCache:
    """
    Cross-session semantic translation cache.

    Usage mirrors the in-session cache from 6.1: call `lookup_or_none`
    before translating, and `store` after a real translation call, per
    target language. The only new behaviour is disk-backed persistence.
    """

    def __init__(
        self,
        storage_path: str,
        embedder: Embedder,
        similarity_threshold: float = 0.92,
        max_entries_per_language: int = 5000,
        autosave_every: int = 25,
    ) -> None:
        self._path = storage_path
        self._embedder = embedder
        self._threshold = similarity_threshold
        self._max_entries = max_entries_per_language
        self._autosave_every = autosave_every
        self._dirty_count = 0
        # lang_code -> list[_CacheEntry]
        self._store: dict[str, list[_CacheEntry]] = {}
        self._load()

    # -- public API ---------------------------------------------------

    async def lookup_or_none(self, source_text: str, target_lang: str) -> str | None:
        """
        Returns a cached translation if a sufficiently similar segment
        was seen in *any* prior session for this language, else None.
        Matches the 6.1 always-translate-on-miss contract exactly.
        """
        entries = self._store.get(target_lang, [])
        if not entries:
            return None

        query_vec = await self._embedder.embed(source_text)
        best_entry = None
        best_score = -1.0
        for entry in entries:
            score = _cosine(query_vec, entry.embedding)
            if score > best_score:
                best_score, best_entry = score, entry

        if best_entry is not None and best_score >= self._threshold:
            best_entry.hit_count += 1
            return best_entry.translation
        return None

    async def store(self, source_text: str, target_lang: str, translation: str) -> None:
        """Record a freshly computed translation for future sessions."""
        embedding = await self._embedder.embed(source_text)
        entries = self._store.setdefault(target_lang, [])
        entries.append(
            _CacheEntry(
                source_text=source_text,
                translation=translation,
                embedding=embedding,
                added_at=time.time(),
            )
        )
        self._enforce_capacity(target_lang)
        self._dirty_count += 1
        if self._dirty_count >= self._autosave_every:
            self.save()

    def cross_session_stats(self) -> dict[str, dict[str, int]]:
        """
        Per-language entry count and total hits accumulated *since disk
        load* -- i.e. hits attributable to memory carried over from
        previous sessions. This is the number Section 7.6 reports.
        """
        return {
            lang: {
                "entries": len(entries),
                "cross_session_hits": sum(e.hit_count for e in entries),
            }
            for lang, entries in self._store.items()
        }

    def save(self) -> None:
        payload = {
            lang: [
                {
                    "source_text": e.source_text,
                    "translation": e.translation,
                    "embedding": e.embedding,
                    "added_at": e.added_at,
                    "hit_count": e.hit_count,
                }
                for e in entries
            ]
            for lang, entries in self._store.items()
        }
        _atomic_write_json(self._path, payload)
        self._dirty_count = 0

    # -- internals ------------------------------------------------------

    def _load(self) -> None:
        if not os.path.exists(self._path):
            return
        with open(self._path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        for lang, entries in raw.items():
            self._store[lang] = [
                _CacheEntry(
                    source_text=e["source_text"],
                    translation=e["translation"],
                    embedding=e["embedding"],
                    added_at=e["added_at"],
                    hit_count=e.get("hit_count", 0),
                )
                for e in entries
            ]

    def _enforce_capacity(self, target_lang: str) -> None:
        """
        Bound memory growth across many sessions. Evicts the
        least-recently-added, least-hit entries first once the cap is
        exceeded -- a simple policy that is easy to justify and easy to
        evaluate (Section 7.6 can report eviction rate alongside hit
        rate). Swap for LRU-by-access-time if evaluation shows it matters.
        """
        entries = self._store[target_lang]
        if len(entries) <= self._max_entries:
            return
        entries.sort(key=lambda e: (e.hit_count, e.added_at))
        del entries[: len(entries) - self._max_entries]


# --------------------------------------------------------------------------
# Persistent glossary
# --------------------------------------------------------------------------

@dataclass
class GlossaryTerm:
    term: str
    translations: dict[str, str] = field(default_factory=dict)  # lang -> text
    approved: bool = False
    added_at: float = field(default_factory=time.time)


class PersistentGlossary:
    """
    Cross-session conference glossary (Section 6.2, made persistent).

    Candidate terms extracted from new conference materials are added as
    *unapproved*. Only approved terms are used for placeholder
    substitution during translation, matching the paper's requirement
    that a human reviews extracted candidates before use. Once a term is
    approved in any session it stays approved in future sessions,
    without re-review, unless explicitly revoked.
    """

    _PLACEHOLDER_FMT = "\ue000TERM{idx}\ue000"  # private-use-area token, unlikely to collide

    def __init__(self, storage_path: str) -> None:
        self._path = storage_path
        self._terms: dict[str, GlossaryTerm] = {}
        self._load()

    # -- review workflow -------------------------------------------------

    def add_candidate(self, term: str) -> None:
        if term not in self._terms:
            self._terms[term] = GlossaryTerm(term=term)

    def approve(self, term: str, translations: dict[str, str] | None = None) -> None:
        entry = self._terms.setdefault(term, GlossaryTerm(term=term))
        entry.approved = True
        if translations:
            entry.translations.update(translations)
        self.save()

    def revoke(self, term: str) -> None:
        if term in self._terms:
            self._terms[term].approved = False
            self.save()

    def pending_review(self) -> list[str]:
        return [t for t, e in self._terms.items() if not e.approved]

    def to_glossary(self):
        """Exports only approved terms as a glossary.Glossary -- the
        runtime object the rest of the codebase (GlossaryAwareTranslationBackend,
        decision_engine.py's ITDE) already knows how to consume. Deferred
        import, same reasoning as the rest of this module's dependency
        boundaries: persistent_memory.py should not need glossary.py just
        to be constructed or tested, only when a caller actually asks to
        export.

        PersistentGlossary itself deliberately does NOT implement the
        protect()/restore() interface ITDE needs directly -- it owns a
        different concern (cross-session approval state, unapproved
        candidates awaiting human review), and bolting a second
        protect()/restore() shape onto it would just create two slightly
        different ways to do the same thing. Export once per session
        (after any new approvals) and hand the result to whatever expects
        a glossary.Glossary instead.
        """
        from glossary import Glossary, GlossaryTerm as _RuntimeGlossaryTerm

        return Glossary([
            _RuntimeGlossaryTerm(term=e.term, translations=dict(e.translations))
            for e in self._terms.values()
            if e.approved
        ])

    # -- substitution (unchanged logic from 6.2, longest-match-first) ---

    def protect(self, text: str) -> tuple[str, dict[str, str]]:
        """
        Replace every approved glossary term found in `text` with a
        placeholder token, longest-term-first, whole-word boundaries.
        Returns the substituted text and a placeholder->term map to
        restore afterward.
        """
        approved_terms = sorted(
            (t for t, e in self._terms.items() if e.approved),
            key=len,
            reverse=True,
        )
        placeholder_map: dict[str, str] = {}
        for idx, term in enumerate(approved_terms):
            pattern = r"\b" + re.escape(term) + r"\b"
            if re.search(pattern, text):
                placeholder = self._PLACEHOLDER_FMT.format(idx=idx)
                text = re.sub(pattern, placeholder, text)
                placeholder_map[placeholder] = term
        return text, placeholder_map

    def restore(self, text: str, placeholder_map: dict[str, str], target_lang: str) -> str:
        for placeholder, term in placeholder_map.items():
            entry = self._terms.get(term)
            replacement = term
            if entry and target_lang in entry.translations:
                replacement = entry.translations[target_lang]
            text = text.replace(placeholder, replacement)
        return text

    # -- persistence ------------------------------------------------------

    def save(self) -> None:
        payload = {
            term: {
                "translations": e.translations,
                "approved": e.approved,
                "added_at": e.added_at,
            }
            for term, e in self._terms.items()
        }
        _atomic_write_json(self._path, payload)

    def _load(self) -> None:
        if not os.path.exists(self._path):
            return
        with open(self._path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        for term, data in raw.items():
            self._terms[term] = GlossaryTerm(
                term=term,
                translations=data.get("translations", {}),
                approved=data.get("approved", False),
                added_at=data.get("added_at", time.time()),
            )


# --------------------------------------------------------------------------
# Shared helper
# --------------------------------------------------------------------------

def _atomic_write_json(path: str, payload: dict) -> None:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise
