"""
Real translation backend using NLLB-200, matching the same async interface
as FakeTranslationBackend (a single `translate(text, target_language)` method)
so it drops into LatencyTracker.timed_translate() with no other changes.

Requires: transformers, torch, sentencepiece
    pip install transformers torch sentencepiece

NLLB-200 uses FLORES-200 language codes, not plain ISO codes, e.g.:
    English -> "eng_Latn"
    Hindi   -> "hin_Deva"
    Tamil   -> "tam_Taml"
    Bengali -> "ben_Beng"
Full code list: https://github.com/facebookresearch/flores/tree/main/flores200
"""

import asyncio
from functools import partial
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer


class NLLBTranslationBackend:
    def __init__(
        self,
        model_name: str = "facebook/nllb-200-distilled-600M",
        source_language: str = "eng_Latn",
    ):
        # Loaded once, reused across all translate() calls — this load time
        # is NOT part of per-sentence translation time, so it happens here
        # in __init__, not inside translate().
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.model = AutoModelForSeq2SeqLM.from_pretrained(model_name)
        self.source_language = source_language

    def _translate_sync(self, text: str, target_language: str) -> str:
        self.tokenizer.src_lang = self.source_language
        inputs = self.tokenizer(text, return_tensors="pt")
        forced_bos_token_id = self.tokenizer.convert_tokens_to_ids(target_language)
        output_tokens = self.model.generate(
            **inputs,
            forced_bos_token_id=forced_bos_token_id,
            max_new_tokens=128,
        )
        return self.tokenizer.batch_decode(output_tokens, skip_special_tokens=True)[0]

    async def translate(self, text: str, target_language: str) -> str:
        # model.generate() is blocking CPU/GPU work, so run it in a thread
        # to avoid blocking the event loop — this mirrors how you'd call
        # your real backend inside the FastAPI host.
        loop = asyncio.get_running_loop()
        fn = partial(self._translate_sync, text, target_language)
        return await loop.run_in_executor(None, fn)
