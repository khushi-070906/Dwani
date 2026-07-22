"""
Fast, fully offline tests for persistent_memory.py -- no model weights,
no network, matching the rest of the LDST test suite (Section 5.3).
Run with: pytest test_persistent_memory.py -v
"""

import asyncio
import os
import tempfile

import pytest

from persistent_memory import FakeEmbedder, PersistentGlossary, PersistentSemanticCache


def run(coro):
    return asyncio.run(coro)


# -- PersistentSemanticCache --------------------------------------------

def test_cache_miss_on_empty_store():
    with tempfile.TemporaryDirectory() as d:
        cache = PersistentSemanticCache(os.path.join(d, "cache.json"), FakeEmbedder())
        result = run(cache.lookup_or_none("Gradient descent minimizes the loss function.", "hi"))
        assert result is None


def test_cache_hit_on_near_duplicate_within_process():
    with tempfile.TemporaryDirectory() as d:
        cache = PersistentSemanticCache(
            os.path.join(d, "cache.json"), FakeEmbedder(), similarity_threshold=0.6
        )
        run(cache.store("Gradient descent minimizes the loss function.", "hi", "TRANSLATED_1"))
        result = run(cache.lookup_or_none("Gradient descent optimizes the loss function.", "hi"))
        assert result == "TRANSLATED_1"


def test_cache_is_isolated_per_language():
    with tempfile.TemporaryDirectory() as d:
        cache = PersistentSemanticCache(os.path.join(d, "cache.json"), FakeEmbedder())
        run(cache.store("Any questions?", "hi", "HINDI_TRANSLATION"))
        result = run(cache.lookup_or_none("Any questions?", "fr"))
        assert result is None


def test_cache_survives_reload_from_disk():
    """The core 6.3 behaviour: memory persists across a process restart."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "cache.json")

        cache_session_1 = PersistentSemanticCache(path, FakeEmbedder())
        run(cache_session_1.store("Backpropagation computes gradients.", "hi", "TRANSLATED_BP"))
        cache_session_1.save()

        # Simulate a fresh process / new session by constructing a new
        # instance pointed at the same file.
        cache_session_2 = PersistentSemanticCache(path, FakeEmbedder(), similarity_threshold=0.6)
        result = run(
            cache_session_2.lookup_or_none("Backpropagation calculates gradients.", "hi")
        )
        assert result == "TRANSLATED_BP"


def test_cross_session_stats_track_hits_since_load():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "cache.json")
        cache1 = PersistentSemanticCache(path, FakeEmbedder())
        run(cache1.store("LoRA reduces trainable parameters.", "hi", "T1"))
        cache1.save()

        cache2 = PersistentSemanticCache(path, FakeEmbedder(), similarity_threshold=0.6)
        run(cache2.lookup_or_none("LoRA reduces the number of trainable parameters.", "hi"))
        stats = cache2.cross_session_stats()
        assert stats["hi"]["cross_session_hits"] == 1


def test_capacity_eviction_bounds_growth():
    with tempfile.TemporaryDirectory() as d:
        cache = PersistentSemanticCache(
            os.path.join(d, "cache.json"),
            FakeEmbedder(),
            max_entries_per_language=3,
            autosave_every=1000,  # avoid disk churn mid-test
        )
        for i in range(10):
            run(cache.store(f"unrelated sentence number {i} about topic {i}", "hi", f"T{i}"))
        stats = cache.cross_session_stats()
        assert stats["hi"]["entries"] <= 3


# -- PersistentGlossary ---------------------------------------------------

def test_unapproved_terms_are_not_protected():
    with tempfile.TemporaryDirectory() as d:
        glossary = PersistentGlossary(os.path.join(d, "glossary.json"))
        glossary.add_candidate("Transformer")
        protected, mapping = glossary.protect("The Transformer architecture is powerful.")
        assert mapping == {}
        assert protected == "The Transformer architecture is powerful."


def test_approved_term_round_trips_through_protect_restore():
    with tempfile.TemporaryDirectory() as d:
        glossary = PersistentGlossary(os.path.join(d, "glossary.json"))
        glossary.approve("Transformer", translations={"hi": "ट्रांसफार्मर"})

        protected, mapping = glossary.protect("The Transformer architecture is powerful.")
        assert "Transformer" not in protected

        restored = glossary.restore(protected, mapping, target_lang="hi")
        assert restored == "The ट्रांसफार्मर architecture is powerful."


def test_approval_persists_across_sessions():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "glossary.json")
        g1 = PersistentGlossary(path)
        g1.approve("LoRA", translations={"hi": "LoRA-हिंदी"})

        g2 = PersistentGlossary(path)  # fresh instance, new "session"
        protected, mapping = g2.protect("LoRA reduces parameters.")
        assert mapping != {}
        restored = g2.restore(protected, mapping, target_lang="hi")
        assert "LoRA-हिंदी" in restored


def test_longest_match_first_protects_multiword_terms():
    with tempfile.TemporaryDirectory() as d:
        glossary = PersistentGlossary(os.path.join(d, "glossary.json"))
        glossary.approve("Neural Network", translations={"hi": "न्यूरल नेटवर्क"})
        glossary.approve("Neural", translations={"hi": "न्यूरल-अकेला"})

        protected, mapping = glossary.protect("A Neural Network was trained.")
        restored = glossary.restore(protected, mapping, target_lang="hi")
        assert "न्यूरल नेटवर्क" in restored
        assert "न्यूरल-अकेला" not in restored


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
