"""
Full demo: translation-time measurement with a fake backend (works instantly,
no extra installs) and, if available, the real NLLB-200 backend (gives you
actual per-sentence translation time on your machine).

Run with just the fake backend (no setup required):
    python3 demo_full.py

Run with the real NLLB-200 backend (first install requirements):
    pip install transformers torch sentencepiece
    python3 demo_full.py --real
"""

import asyncio
import json
import random
import sys

from latency_tracker import LatencyTracker


class FakeTranslationBackend:
    """No real model — just simulates variable translation delay."""

    async def translate(self, text: str, target_language: str) -> str:
        await asyncio.sleep(random.uniform(0.02, 0.12))
        return f"[{target_language}] {text}"


SENTENCES = [
    "The presenter began the introduction.",
    "Next we discuss the segmentation algorithm.",
    "This concludes the methodology section.",
]

# NLLB FLORES-200 codes
TARGET_LANGUAGES = {
    "hi": "hin_Deva",
    "ta": "tam_Taml",
    "bn": "ben_Beng",
}


async def run_fake_backend():
    print("Running with FakeTranslationBackend (simulated timing)...\n")
    tracker = LatencyTracker()
    backend = FakeTranslationBackend()

    for i, sentence in enumerate(SENTENCES, start=1):
        segment_id = f"seg_{i}"
        for lang_code in TARGET_LANGUAGES:
            await tracker.timed_translate(
                backend, sentence, lang_code, segment_id, mode="segment"
            )

    print(json.dumps(tracker.full_report(), indent=2))


async def run_real_backend():
    print("Loading NLLB-200 model (this happens once, not timed)...\n")
    from real_backend import NLLBTranslationBackend

    tracker = LatencyTracker()
    backend = NLLBTranslationBackend()  # downloads/loads the model on first run

    for i, sentence in enumerate(SENTENCES, start=1):
        segment_id = f"seg_{i}"
        for lang_code, flores_code in TARGET_LANGUAGES.items():
            translated = await tracker.timed_translate(
                backend, sentence, flores_code, segment_id, mode="segment"
            )
            print(f"[{lang_code}] {sentence!r} -> {translated!r}")

    print()
    print(json.dumps(tracker.full_report(), indent=2))


if __name__ == "__main__":
    if "--real" in sys.argv:
        asyncio.run(run_real_backend())
    else:
        asyncio.run(run_fake_backend())
