"""
Tests for glossary.py -- Glossary.protect()/restore(), the
GlossaryAwareTranslationBackend wrapper, extract_candidate_terms(), and
Glossary.load()/save() round-tripping.
"""

from __future__ import annotations

import json

import pytest

from glossary import (
    Glossary,
    GlossaryAwareTranslationBackend,
    GlossaryTerm,
    extract_candidate_terms,
)
from pipeline import FakeTranslationBackend


class TestProtectRestore:
    def test_verbatim_term_is_restored_unchanged(self):
        glossary = Glossary.preserve_only(["CUDA"])
        protected, placeholder_map = glossary.protect("We use CUDA for training.")

        assert "CUDA" not in protected
        assert len(placeholder_map) == 1

        restored = glossary.restore(protected, placeholder_map, "hi")
        assert restored == "We use CUDA for training."

    def test_translated_term_is_restored_with_language_specific_text(self):
        glossary = Glossary([GlossaryTerm(term="Transformer", translations={"hi": "ट्रांसफार्मर"})])
        protected, placeholder_map = glossary.protect("The Transformer architecture is powerful.")

        restored_hi = glossary.restore(protected, placeholder_map, "hi")
        assert "ट्रांसफार्मर" in restored_hi
        assert "Transformer" not in restored_hi

        # no "fr" entry -> falls back to verbatim
        restored_fr = glossary.restore(protected, placeholder_map, "fr")
        assert "Transformer" in restored_fr

    def test_multi_word_term_is_protected_as_one_unit(self):
        glossary = Glossary.preserve_only(["Tensor Core"])
        protected, placeholder_map = glossary.protect("Accelerated by Tensor Core hardware.")

        assert "Tensor Core" not in protected
        assert len(placeholder_map) == 1
        restored = glossary.restore(protected, placeholder_map, "hi")
        assert "Tensor Core" in restored

    def test_longest_term_takes_priority_over_shorter_overlapping_term(self):
        glossary = Glossary.preserve_only(["Tensor Core", "Tensor"])
        protected, placeholder_map = glossary.protect("The Tensor Core is fast.")

        # only one match -- "Tensor Core" -- not two separate ones
        assert len(placeholder_map) == 1
        matched_term = list(placeholder_map.values())[0][0].term
        assert matched_term == "Tensor Core"

    def test_case_insensitive_by_default(self):
        glossary = Glossary.preserve_only(["CUDA"])
        protected, placeholder_map = glossary.protect("we use cuda here")
        assert len(placeholder_map) == 1
        # original casing (lowercase, as it appeared) is preserved on restore
        restored = glossary.restore(protected, placeholder_map, "hi")
        assert "cuda" in restored

    def test_case_sensitive_term_does_not_match_different_case(self):
        glossary = Glossary([GlossaryTerm(term="CUDA", case_sensitive=True)])
        protected, placeholder_map = glossary.protect("we use cuda here")
        assert len(placeholder_map) == 0
        assert protected == "we use cuda here"

    def test_whole_word_matching_does_not_match_inside_longer_words(self):
        glossary = Glossary.preserve_only(["Core"])
        protected, placeholder_map = glossary.protect("Corerelated systems are common.")
        assert len(placeholder_map) == 0

    def test_no_matching_terms_leaves_text_unchanged(self):
        glossary = Glossary.preserve_only(["CUDA", "Transformer"])
        protected, placeholder_map = glossary.protect("This sentence has no glossary terms.")
        assert protected == "This sentence has no glossary terms."
        assert placeholder_map == {}

    def test_multiple_distinct_terms_all_protected_and_restored(self):
        glossary = Glossary([
            GlossaryTerm(term="CUDA"),
            GlossaryTerm(term="Transformer", translations={"hi": "ट्रांसफार्मर"}),
        ])
        text = "The Transformer uses CUDA under the hood."
        protected, placeholder_map = glossary.protect(text)
        assert len(placeholder_map) == 2
        restored = glossary.restore(protected, placeholder_map, "hi")
        assert "ट्रांसफार्मर" in restored
        assert "CUDA" in restored

    def test_repeated_term_each_occurrence_protected(self):
        glossary = Glossary.preserve_only(["CUDA"])
        protected, placeholder_map = glossary.protect("CUDA and more CUDA.")
        assert len(placeholder_map) == 2
        restored = glossary.restore(protected, placeholder_map, "hi")
        assert restored == "CUDA and more CUDA."


class TestGlossaryAwareTranslationBackend:
    @pytest.mark.anyio
    async def test_wraps_translation_and_restores_glossary_terms(self):
        inner = FakeTranslationBackend()
        glossary = Glossary.preserve_only(["CUDA"])
        wrapped = GlossaryAwareTranslationBackend(inner, glossary)

        result = await wrapped.translate("We use CUDA for training.", "hi")

        # FakeTranslationBackend just tags with [lang]; CUDA should survive intact
        assert "CUDA" in result
        assert result.startswith("[hi]")

    @pytest.mark.anyio
    async def test_inner_backend_receives_protected_placeholder_text_not_the_term(self):
        inner = FakeTranslationBackend()
        glossary = Glossary.preserve_only(["CUDA"])
        wrapped = GlossaryAwareTranslationBackend(inner, glossary)

        await wrapped.translate("We use CUDA for training.", "hi")

        # inner.calls records exactly what text was passed to translate()
        called_text, called_lang = inner.calls[0]
        assert "CUDA" not in called_text
        assert called_lang == "hi"

    @pytest.mark.anyio
    async def test_translated_term_ends_up_in_output(self):
        inner = FakeTranslationBackend()
        glossary = Glossary([GlossaryTerm(term="Transformer", translations={"hi": "ट्रांसफार्मर"})])
        wrapped = GlossaryAwareTranslationBackend(inner, glossary)

        result = await wrapped.translate("The Transformer is powerful.", "hi")
        assert "ट्रांसफार्मर" in result

    @pytest.mark.anyio
    async def test_no_glossary_terms_present_translates_normally(self):
        inner = FakeTranslationBackend()
        glossary = Glossary.preserve_only(["CUDA"])
        wrapped = GlossaryAwareTranslationBackend(inner, glossary)

        result = await wrapped.translate("A sentence without any terms.", "fr")
        assert result == "[fr] A sentence without any terms."


class TestExtractCandidateTerms:
    def test_repeated_acronym_is_found(self):
        text = "We accelerate training with CUDA. Later, CUDA is used again for inference."
        candidates = extract_candidate_terms(text, min_occurrences=2)
        assert "CUDA" in candidates

    def test_single_occurrence_below_threshold_is_dropped(self):
        text = "We mention CUDA exactly once in this whole document."
        candidates = extract_candidate_terms(text, min_occurrences=2)
        assert "CUDA" not in candidates

    def test_repeated_title_case_phrase_is_found(self):
        text = (
            "We use a Diffusion Model for generation. "
            "Our Diffusion Model outperforms the baseline."
        )
        candidates = extract_candidate_terms(text, min_occurrences=2)
        assert "Diffusion Model" in candidates

    def test_common_sentence_starters_are_filtered_out(self):
        text = "The results are strong. The results hold across seeds. The method works."
        candidates = extract_candidate_terms(text, min_occurrences=2)
        assert "The" not in candidates
        assert not any(c.startswith("The ") for c in candidates)

    def test_lowercase_terms_are_not_extracted(self):
        # heuristic extractor only looks at capitalization patterns --
        # documented limitation, exercised here so it stays documented
        text = "backpropagation backpropagation backpropagation"
        candidates = extract_candidate_terms(text, min_occurrences=2)
        assert candidates == []


class TestGlossaryPersistence:
    def test_save_and_load_round_trip(self, tmp_path):
        glossary = Glossary([
            GlossaryTerm(term="CUDA", case_sensitive=True),
            GlossaryTerm(term="Transformer", translations={"hi": "ट्रांसफार्मर"}),
        ])
        path = tmp_path / "glossary.json"
        glossary.save(path)

        loaded = Glossary.load(path)
        terms_by_name = {t.term: t for t in loaded}

        assert terms_by_name["CUDA"].case_sensitive is True
        assert terms_by_name["Transformer"].translations == {"hi": "ट्रांसफार्मर"}

    def test_saved_json_is_human_editable_shape(self, tmp_path):
        glossary = Glossary.preserve_only(["CUDA"])
        path = tmp_path / "glossary.json"
        glossary.save(path)

        data = json.loads(path.read_text(encoding="utf-8"))
        assert data == [{"term": "CUDA", "translations": {}, "case_sensitive": False}]