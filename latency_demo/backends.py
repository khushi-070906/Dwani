"""
Real ASR/MT backends for the LDST pipeline -- Section 4.3 of the paper.

These implement the `ASRBackend` and `TranslationBackend` protocols defined
in pipeline.py against actual locally-runnable models:

    RealWhisperBackend  -- a compact Whisper variant via faster-whisper
                            (a CTranslate2 reimplementation of Whisper),
                            running entirely on the host device with no
                            network calls at inference time.
    RealNLLBBackend     -- NLLB-200 (distilled 600M by default) via
                            CTranslate2, likewise fully local at inference
                            time.

Both classes live here rather than in pipeline.py on purpose: importing
pipeline.py must never require faster-whisper or ctranslate2 to be
installed, so the Fake* backends, the segmentation logic, and the wiring
tests in test_pipeline.py stay fast and dependency-free. Even within this
module, the heavy imports (faster_whisper, ctranslate2, transformers) are
deferred to each class's __init__ rather than done at module import time --
so the language-code mapping and resampling helpers below (which
test_backends.py exercises directly) can be imported and unit-tested with
nothing but numpy installed, exactly like the rest of this project.

-----------------------------------------------------------------------------
Setup
-----------------------------------------------------------------------------

1. Install the two inference libraries on the presenter's device:

       pip install faster-whisper ctranslate2 transformers sentencepiece \
           --break-system-packages

2. faster-whisper downloads and caches its own compact Whisper checkpoint on
   first use (e.g. "small" or "base") -- no manual conversion step needed,
   just network access the first time each model size is used.

3. NLLB-200 must be converted from its Hugging Face checkpoint into
   CTranslate2's format once, ahead of time, on any machine with internet
   access (this does not need to be the presenter's device):

       pip install ctranslate2 transformers sentencepiece torch
       ct2-transformers-converter \
           --model facebook/nllb-200-distilled-600M \
           --output_dir nllb-200-ct2 \
           --quantization int8

   Copy the resulting `nllb-200-ct2` directory onto the presenter's device
   alongside this code. `RealNLLBBackend` loads it from disk (`model_dir`)
   and never touches the network at inference time -- this is the piece
   that makes the "no traffic leaves the local network" claim in Section
   4.2 hold at translation time too, not just at the WebSocket layer.
   `AutoTokenizer.from_pretrained(...)` still needs the tokenizer files
   cached locally; run it once with network access ahead of the session so
   they land in the local HF cache, then it's offline from then on.

-----------------------------------------------------------------------------
Wiring into server.py
-----------------------------------------------------------------------------

    from backends import RealWhisperBackend, RealNLLBBackend
    from pipeline import Pipeline

    pipeline = Pipeline(
        asr=RealWhisperBackend(model_size="small"),
        translator=RealNLLBBackend(model_dir="nllb-200-ct2"),
        broadcast=broadcast_caption,
        subscribed_languages=lambda: subscribers.keys(),
    )

    # then, in the host's audio capture loop:
    await pipeline.handle_audio_chunk(chunk)
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Optional

import numpy as np

if TYPE_CHECKING:
    from pipeline import AudioSegment


class UnsupportedLanguageError(ValueError):
    """Raised when a language code has no known FLORES-200 mapping, so a
    typo'd or unsupported custom language code (attendees can type anything
    into index.html's "other language" box) fails clearly and immediately
    rather than silently mistranslating or raising deep inside ctranslate2.
    """


# ISO 639-1-ish codes -- matching both the preset codes in index.html's
# language grid (en, hi, pa, bn, ta, te, mr, ur) and other codes an attendee
# might type into the custom-language box -- mapped to the FLORES-200 codes
# NLLB-200 was trained on. NLLB does not understand bare ISO 639-1 codes, so
# every target language has to pass through this table before reaching the
# model.
LANG_TO_FLORES = {
    "en": "eng_Latn",
    "hi": "hin_Deva",
    "pa": "pan_Guru",
    "bn": "ben_Beng",
    "ta": "tam_Taml",
    "te": "tel_Telu",
    "mr": "mar_Deva",
    "ur": "urd_Arab",
    "gu": "guj_Gujr",
    "kn": "kan_Knda",
    "ml": "mal_Mlym",
    "or": "ory_Orya",
    "as": "asm_Beng",
    "ne": "npi_Deva",
    "si": "sin_Sinh",
    "fr": "fra_Latn",
    "de": "deu_Latn",
    "es": "spa_Latn",
    "pt": "por_Latn",
    "it": "ita_Latn",
    "ru": "rus_Cyrl",
    "zh": "zho_Hans",
    "ja": "jpn_Jpan",
    "ko": "kor_Hang",
    "ar": "arb_Arab",
}


def flores_code(lang: str) -> str:
    """Map an app-level language code to its FLORES-200 equivalent.

    Raises UnsupportedLanguageError (rather than a bare KeyError) for
    anything unmapped, since this is reachable directly from attendee input.
    """
    code = lang.strip().lower()
    try:
        return LANG_TO_FLORES[code]
    except KeyError:
        raise UnsupportedLanguageError(
            f"No FLORES-200 mapping for language code {lang!r}. "
            f"Known codes: {sorted(LANG_TO_FLORES)}"
        ) from None


def resample_linear(samples: np.ndarray, from_rate: int, to_rate: int) -> np.ndarray:
    """Dependency-free linear-interpolation resampler.

    AudioSegmenter's default sample_rate (16_000) already matches what
    faster-whisper expects, so in the common case both backends' calls to
    this are a no-op. It exists as a defensive fallback for a host device
    whose microphone capture happens to be wired up at some other native
    rate. This trades audio fidelity for zero extra dependencies -- a
    deployment that finds resampling artifacts affect transcription quality
    should switch to scipy.signal.resample_poly (or better, just capture
    audio at 16kHz natively) instead of this.
    """
    if from_rate == to_rate or len(samples) == 0:
        return samples
    duration = len(samples) / from_rate
    old_times = np.linspace(0, duration, num=len(samples), endpoint=False)
    new_len = max(1, int(round(duration * to_rate)))
    new_times = np.linspace(0, duration, num=new_len, endpoint=False)
    return np.interp(new_times, old_times, samples).astype(np.float32)


class RealWhisperBackend:
    """`ASRBackend` implementation backed by faster-whisper -- a compact,
    locally-run Whisper variant (Section 4.3) with no network calls at
    inference time.
    """

    def __init__(
        self,
        model_size: str = "small",
        device: str = "cpu",
        compute_type: str = "int8",
        language: Optional[str] = "en",
        target_sample_rate: int = 16_000,
    ) -> None:
        """`language` pins Whisper to the presenter's spoken language rather
        than letting it auto-detect per segment: auto-detection on short,
        few-second segments is markedly less reliable than on a full
        utterance, and in this single-presenter session the spoken language
        is known ahead of time. Pass language=None to fall back to
        per-segment auto-detection instead (e.g. for a presenter who
        code-switches between languages).
        """
        from faster_whisper import WhisperModel  # deferred: heavy, optional dep

        self._model = WhisperModel(model_size, device=device, compute_type=compute_type)
        self._language = language
        self._target_sample_rate = target_sample_rate

    async def transcribe(self, segment: "AudioSegment") -> str:
        # faster-whisper's transcribe() is a blocking, synchronous call --
        # push it to a worker thread so it doesn't stall the event loop that
        # server.py's WebSocket connections and other segments share.
        return await asyncio.to_thread(self._transcribe_sync, segment)

    def _transcribe_sync(self, segment: "AudioSegment") -> str:
        audio = resample_linear(segment.samples, segment.sample_rate, self._target_sample_rate)
        segments, _info = self._model.transcribe(
            audio,
            language=self._language,
            # AudioSegmenter already isolated this to one voiced utterance
            # with pause boundaries on either side -- re-running VAD inside
            # Whisper on top of that would just risk it disagreeing with the
            # boundaries the rest of the pipeline (and its tests) rely on.
            vad_filter=False,
        )
        return "".join(s.text for s in segments).strip()


class RealNLLBBackend:
    """`TranslationBackend` implementation backed by a locally converted
    NLLB-200 CTranslate2 model. Per Section 4.3, Pipeline already collapses
    subscribed languages down to the distinct set before calling this, so
    this class only ever does one text-in/text-out call per distinct
    language per segment -- it doesn't need to know or care how many
    attendees are subscribed to each one.
    """

    def __init__(
        self,
        model_dir: str,
        tokenizer_name: str = "facebook/nllb-200-distilled-600M",
        source_lang: str = "en",
        device: str = "cpu",
        beam_size: int = 4,
    ) -> None:
        import ctranslate2  # deferred: heavy, optional dep
        from transformers import AutoTokenizer  # deferred: heavy, optional dep

        self._translator = ctranslate2.Translator(model_dir, device=device)
        self._tokenizer = AutoTokenizer.from_pretrained(tokenizer_name)
        self._source_flores = flores_code(source_lang)
        self._beam_size = beam_size

    async def translate(self, text: str, target_lang: str) -> str:
        # ctranslate2's translate_batch() is a blocking C++ call -- likewise
        # pushed off the event loop.
        return await asyncio.to_thread(self._translate_sync, text, target_lang)

    def _translate_sync(self, text: str, target_lang: str) -> str:
        target_flores = flores_code(target_lang)
        self._tokenizer.src_lang = self._source_flores
        source_tokens = self._tokenizer.convert_ids_to_tokens(self._tokenizer(text).input_ids)

        result = self._translator.translate_batch(
            [source_tokens],
            target_prefix=[[target_flores]],
            beam_size=self._beam_size,
        )
        output_tokens = result[0].hypotheses[0][1:]  # drop the target-lang prefix token
        output_ids = self._tokenizer.convert_tokens_to_ids(output_tokens)
        return self._tokenizer.decode(output_ids, skip_special_tokens=True).strip()