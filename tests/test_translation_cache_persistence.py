"""
Tests for SemanticCache's Section 6.3 cross-session persistence.

Uses a deterministic embed_fn, same pattern as the rest of the suite (see
translation_cache.py's docstring) -- no sentence-transformers download
needed, so this runs in the same fast, fully offline suite as everything
else.
"""

import hashlib
import re
import tempfile
from pathlib import Path

import numpy as np
import pytest

from translation_cache import SemanticCache


def deterministic_embed(text: str, dim: int = 32) -> np.ndarray:
    """Bag-of-hashed-tokens embedding, L2-normalized -- good enough to make
    near-duplicate sentences score high and unrelated ones score low,
    without pulling in any ML dependency. Not meant to resemble a real
    sentence embedding's geometry, only its *interface*."""
    vec = np.zeros(dim, dtype=np.float32)
    for token in re.findall(r"[a-z0-9]+", text.lower()):
        idx = int(hashlib.sha256(token.encode()).hexdigest(), 16) % dim
        vec[idx] += 1.0
    norm = np.linalg.norm(vec)
    return vec / norm if norm > 0 else vec


@pytest.fixture
def storage_path():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d) / "cache.json"


def test_default_cache_has_no_storage_path_and_save_is_a_no_op(storage_path):
    """Backward compatibility: constructing SemanticCache without
    storage_path must behave exactly as before -- including .save() being a
    harmless no-op rather than an error, since server.py calls it
    unconditionally on any cache."""
    cache = SemanticCache(embed_fn=deterministic_embed)
    cache.save()  # must not raise, must not touch disk
    assert not storage_path.exists()


@pytest.mark.anyio
async def test_fresh_storage_path_starts_empty(storage_path):
    cache = SemanticCache(embed_fn=deterministic_embed, storage_path=storage_path)
    result = await cache.get("Gradient descent minimizes the loss function.", "hi")
    assert result is None
    assert cache.stats.cross_session_hits == 0


@pytest.mark.anyio
async def test_entries_survive_a_simulated_restart(storage_path):
    """The core 6.3 behavior: save from one instance, load in a fresh one."""
    session_1 = SemanticCache(embed_fn=deterministic_embed, storage_path=storage_path)
    await session_1.put("Backpropagation computes gradients.", "hi", "TRANSLATED_BP")
    session_1.save()

    session_2 = SemanticCache(
        embed_fn=deterministic_embed, storage_path=storage_path, similarity_threshold=0.5
    )
    result = await session_2.get("Backpropagation computes the gradients.", "hi")
    assert result == "TRANSLATED_BP"


@pytest.mark.anyio
async def test_cross_session_hits_only_counts_entries_loaded_from_disk(storage_path):
    session_1 = SemanticCache(embed_fn=deterministic_embed, storage_path=storage_path)
    await session_1.put("LoRA reduces trainable parameters.", "hi", "T_LORA")
    session_1.save()

    session_2 = SemanticCache(
        embed_fn=deterministic_embed, storage_path=storage_path, similarity_threshold=0.5
    )
    # A hit against the loaded entry -> counts as cross-session.
    await session_2.get("LoRA reduces the trainable parameters.", "hi")
    assert session_2.stats.cross_session_hits == 1
    assert session_2.stats.hits == 1

    # A hit against something stored fresh *this* session -> not cross-session.
    await session_2.put("Attention is all you need.", "hi", "T_ATTN")
    await session_2.get("Attention is all you need.", "hi")
    assert session_2.stats.cross_session_hits == 1  # unchanged
    assert session_2.stats.hits == 2


@pytest.mark.anyio
async def test_save_without_configured_path_and_no_argument_is_a_no_op():
    cache = SemanticCache(embed_fn=deterministic_embed)  # no storage_path
    await cache.put("Any questions?", "hi", "T1")
    cache.save()  # no path anywhere -- must not raise


@pytest.mark.anyio
async def test_save_accepts_an_explicit_path_override(storage_path):
    cache = SemanticCache(embed_fn=deterministic_embed)  # constructed with no storage_path
    await cache.put("Any questions?", "hi", "T1")
    cache.save(storage_path)  # explicit override
    assert storage_path.exists()

    reloaded = SemanticCache(
        embed_fn=deterministic_embed, storage_path=storage_path, similarity_threshold=0.5
    )
    result = await reloaded.get("Any questions??", "hi")
    assert result == "T1"


@pytest.mark.anyio
async def test_eviction_shrinks_loaded_count_so_stats_stay_accurate(storage_path):
    session_1 = SemanticCache(embed_fn=deterministic_embed, storage_path=storage_path)
    await session_1.put("first old sentence about topic zero", "hi", "OLD0")
    session_1.save()

    # New session, tiny capacity, so a bunch of new entries evict the one
    # loaded entry out entirely.
    session_2 = SemanticCache(
        embed_fn=deterministic_embed,
        storage_path=storage_path,
        max_entries_per_language=2,
        similarity_threshold=0.99,  # avoid accidental hits between filler sentences
    )
    for i in range(5):
        await session_2.put(f"brand new unrelated sentence number {i}", "hi", f"NEW{i}")

    # The originally-loaded entry should have been evicted; a lookup against
    # it must be an ordinary miss, not a cross-session hit, and stats must
    # not error out.
    result = await session_2.get("first old sentence about topic zero", "hi")
    assert result is None
    assert session_2.stats.cross_session_hits == 0


@pytest.mark.anyio
async def test_language_isolation_preserved_across_reload(storage_path):
    session_1 = SemanticCache(embed_fn=deterministic_embed, storage_path=storage_path)
    await session_1.put("Any questions?", "hi", "HINDI_T")
    session_1.save()

    session_2 = SemanticCache(
        embed_fn=deterministic_embed, storage_path=storage_path, similarity_threshold=0.5
    )
    result = await session_2.get("Any questions?", "fr")
    assert result is None
