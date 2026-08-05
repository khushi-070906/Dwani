"""
Tests for backends.py -- specifically the FLORES-200 language-code mapping
and the fallback resampler, which have no dependency on faster-whisper or
ctranslate2 being installed. RealWhisperBackend and RealNLLBBackend
themselves need real model weights to exercise meaningfully and are out of
scope for this fast unit-test suite; they're covered by manual/integration
testing on a machine with the models installed (see backends.py's module
docstring for setup).
"""

from __future__ import annotations

import numpy as np
import pytest

from backends import LANG_TO_FLORES, UnsupportedLanguageError, flores_code, resample_linear

# Every preset code offered in index.html's language grid, plus "en" for the
# presenter's own spoken language -- all of these must resolve, or the app's
# UI would let an attendee pick a language RealNLLBBackend can't serve.
INDEX_HTML_PRESET_CODES = ["en", "hi", "pa", "bn", "ta", "te", "mr", "ur"]


class TestFloresMapping:
    @pytest.mark.parametrize("code", INDEX_HTML_PRESET_CODES)
    def test_every_index_html_preset_language_is_mapped(self, code):
        # doesn't raise
        flores_code(code)

    def test_known_code_maps_to_expected_flores_code(self):
        assert flores_code("hi") == "hin_Deva"
        assert flores_code("fr") == "fra_Latn"

    def test_mapping_is_case_and_whitespace_insensitive(self):
        assert flores_code("HI") == flores_code("hi")
        assert flores_code(" hi ") == flores_code("hi")

    def test_unmapped_code_raises_clear_error_not_keyerror(self):
        with pytest.raises(UnsupportedLanguageError):
            flores_code("xx-not-a-real-code")

    def test_every_flores_value_is_unique(self):
        # a mapping collision would mean two ISO codes silently translate to
        # the same language
        assert len(set(LANG_TO_FLORES.values())) == len(LANG_TO_FLORES)


class TestResampleLinear:
    def test_same_rate_is_a_true_noop(self):
        samples = np.array([0.1, 0.2, 0.3], dtype=np.float32)
        result = resample_linear(samples, 16_000, 16_000)
        assert result is samples  # identity, not just equal -- no copy needed

    def test_empty_input_returns_empty(self):
        result = resample_linear(np.array([], dtype=np.float32), 8_000, 16_000)
        assert len(result) == 0

    def test_upsampling_preserves_duration_in_samples(self):
        one_second_at_8k = np.zeros(8_000, dtype=np.float32)
        result = resample_linear(one_second_at_8k, 8_000, 16_000)
        assert result.shape[0] == pytest.approx(16_000, abs=2)

    def test_downsampling_preserves_duration_in_samples(self):
        one_second_at_48k = np.zeros(48_000, dtype=np.float32)
        result = resample_linear(one_second_at_48k, 48_000, 16_000)
        assert result.shape[0] == pytest.approx(16_000, abs=2)

    def test_resampling_preserves_a_constant_signal(self):
        # a DC-offset (constant) signal should resample to the same constant,
        # regardless of rate change -- a good sanity check that interpolation
        # isn't introducing scaling errors
        constant = np.full(8_000, 0.42, dtype=np.float32)
        result = resample_linear(constant, 8_000, 16_000)
        assert np.allclose(result, 0.42, atol=1e-5)

    def test_output_dtype_is_float32(self):
        samples = np.zeros(8_000, dtype=np.float32)
        result = resample_linear(samples, 8_000, 16_000)
        assert result.dtype == np.float32