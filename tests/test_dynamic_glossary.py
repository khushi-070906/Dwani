"""
Tests for dynamic_glossary.py -- DynamicGlossaryUpdater's poll/dedup/merge
logic, exercised with a scripted ocr_fn instead of a real screen and
Tesseract (see the module's own docstring for why -- same rationale as
translation_cache.py's SemanticCache tests injecting a fake embed_fn).
"""

from __future__ import annotations

import asyncio

import pytest

from dynamic_glossary import DynamicGlossaryUpdater, TermAddedEvent
from glossary import Glossary, GlossaryTerm


def _scripted_ocr(slides: list[str]):
    """Returns a zero-arg callable that yields each string in `slides` in
    order, then repeats the last one forever (simulating "presenter is
    still on the final slide")."""
    index = 0

    def _ocr() -> str:
        nonlocal index
        text = slides[min(index, len(slides) - 1)]
        index += 1
        return text

    return _ocr


class TestPollOnce:
    def test_term_repeated_within_one_slide_is_added_immediately(self):
        glossary = Glossary()
        updater = DynamicGlossaryUpdater(
            glossary, ocr_fn=_scripted_ocr(["We use CUDA. Later we use CUDA again."])
        )
        added = updater.poll_once()
        assert "CUDA" in added
        assert "CUDA" in {t.term for t in glossary}

    def test_term_appearing_once_is_not_yet_added(self):
        glossary = Glossary()
        updater = DynamicGlossaryUpdater(glossary, ocr_fn=_scripted_ocr(["We use CUDA once."]))
        added = updater.poll_once()
        assert added == []
        assert glossary_term_names(glossary) == set()

    def test_identical_slide_polled_repeatedly_is_not_double_counted(self):
        glossary = Glossary()
        updater = DynamicGlossaryUpdater(
            glossary, min_occurrences=2, ocr_fn=_scripted_ocr(["We use CUDA once."])
        )
        # same (unchanged) slide text on every poll -- dedup should mean
        # this never accumulates to 2 occurrences no matter how many polls
        for _ in range(5):
            added = updater.poll_once()
            assert added == []
        assert glossary_term_names(glossary) == set()

    def test_term_reappearing_on_a_later_distinct_slide_is_added(self):
        glossary = Glossary()
        updater = DynamicGlossaryUpdater(
            glossary,
            ocr_fn=_scripted_ocr([
                "We introduce the Transformer in this section.",
                "This part covers unrelated logistics about seating.",
                "The Transformer architecture is covered here in detail.",
            ]),
        )
        first = updater.poll_once()   # "Transformer" seen once -- not added yet
        second = updater.poll_once()  # different slide, no "Transformer" -- still not added
        third = updater.poll_once()   # "Transformer" seen again on a new slide -- now added
        assert first == []
        assert second == []
        assert third == ["Transformer"]

    def test_term_already_in_glossary_is_not_re_added(self):
        glossary = Glossary([GlossaryTerm(term="CUDA")])
        updater = DynamicGlossaryUpdater(
            glossary, ocr_fn=_scripted_ocr(["CUDA is used here. CUDA again too."])
        )
        added = updater.poll_once()
        assert added == []
        assert len(glossary) == 1  # no duplicate entry

    def test_empty_ocr_text_is_ignored_without_error(self):
        glossary = Glossary()
        updater = DynamicGlossaryUpdater(glossary, ocr_fn=_scripted_ocr(["", "", ""]))
        for _ in range(3):
            assert updater.poll_once() == []

    def test_on_term_added_callback_is_invoked(self):
        glossary = Glossary()
        received: list[TermAddedEvent] = []
        updater = DynamicGlossaryUpdater(
            glossary,
            ocr_fn=_scripted_ocr(["CUDA training. More CUDA details."]),
            on_term_added=received.append,
        )
        updater.poll_once()
        assert len(received) == 1
        assert received[0].term == "CUDA"
        assert received[0].poll_index == 1

    def test_log_records_poll_index(self):
        glossary = Glossary()
        updater = DynamicGlossaryUpdater(
            glossary,
            ocr_fn=_scripted_ocr([
                "This section covers nothing notable yet.",
                "This section introduces CUDA now.",
                "CUDA appears again in this section here.",
            ]),
        )
        updater.poll_once()  # poll 1
        updater.poll_once()  # poll 2
        updater.poll_once()  # poll 3 -- CUDA hits threshold here
        assert len(updater.log) == 1
        assert updater.log[0].term == "CUDA"
        assert updater.log[0].poll_index == 3

    def test_multiple_terms_can_be_added_in_a_single_poll(self):
        glossary = Glossary()
        updater = DynamicGlossaryUpdater(
            glossary,
            ocr_fn=_scripted_ocr(["CUDA and CUDA power the Transformer and the Transformer both."]),
        )
        added = updater.poll_once()
        assert set(added) == {"CUDA", "Transformer"}


class TestAsyncRunStop:
    @pytest.mark.anyio
    async def test_run_polls_periodically_until_stopped(self):
        glossary = Glossary()
        updater = DynamicGlossaryUpdater(
            glossary,
            poll_interval_seconds=0.01,
            ocr_fn=_scripted_ocr(["CUDA repeated. CUDA repeated again."]),
        )

        task = asyncio.create_task(updater.run())
        await asyncio.sleep(0.05)
        updater.stop()
        await asyncio.wait_for(task, timeout=1.0)

        assert updater._poll_count >= 1
        assert "CUDA" in glossary_term_names(glossary)

    @pytest.mark.anyio
    async def test_poll_once_async_runs_off_the_event_loop(self):
        glossary = Glossary()
        updater = DynamicGlossaryUpdater(
            glossary, ocr_fn=_scripted_ocr(["CUDA here. CUDA there."])
        )
        added = await updater.poll_once_async()
        assert added == ["CUDA"]


def glossary_term_names(glossary: Glossary) -> set[str]:
    return {t.term for t in glossary}