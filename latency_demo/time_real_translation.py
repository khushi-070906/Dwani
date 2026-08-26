"""
Real per-sentence translation timing using YOUR actual backend --
backends.RealNLLBBackend (CTranslate2-based), not a generic transformers
pipeline. This matches Section 4.3/5.1 of the paper and the setup
instructions already documented in backends.py's module docstring.

-----------------------------------------------------------------------------
One-time setup (per backends.py's own docstring)
-----------------------------------------------------------------------------

1. Install dependencies:

    pip install faster-whisper ctranslate2 transformers sentencepiece torch \
        --break-system-packages

2. Convert NLLB-200 to CTranslate2 format once (needs internet, can be done
   on any machine, not necessarily the presenter's device):

    ct2-transformers-converter \
        --model facebook/nllb-200-distilled-600M \
        --output_dir nllb-200-ct2 \
        --quantization int8

   Put the resulting `nllb-200-ct2` folder next to this script.

3. Run once with internet access so the tokenizer files land in the local
   Hugging Face cache (RealNLLBBackend needs AutoTokenizer.from_pretrained,
   even though the translation model itself is fully offline after this):

    python3 -c "from transformers import AutoTokenizer; \
        AutoTokenizer.from_pretrained('facebook/nllb-200-distilled-600M')"

-----------------------------------------------------------------------------
Run
-----------------------------------------------------------------------------

    python3 time_real_translation.py --model-dir nllb-200-ct2
"""

import argparse
import asyncio
import json

from latency_tracker import LatencyTracker
from backends import RealNLLBBackend

# Placeholder sentences -- swap these for real transcript segments from a
# recorded lecture (e.g. output of your ASR backend on real audio) for a
# paper-grade number rather than a synthetic one.
SENTENCES = [
    "The presenter began the introduction.",
    "Next we discuss the segmentation algorithm.",
    "This concludes the methodology section.",
]

# App-level codes, matching backends.py's LANG_TO_FLORES keys --
# RealNLLBBackend.translate() takes these directly, not raw FLORES codes.
TARGET_LANGUAGES = ["hi", "ta", "bn"]


async def main(model_dir: str, source_lang: str, warmup: bool):
    tracker = LatencyTracker()

    print(f"Loading RealNLLBBackend from {model_dir!r} (not timed)...")
    backend = RealNLLBBackend(model_dir=model_dir, source_lang=source_lang)

    if warmup:
        # First call after model load can be slower (memory allocation,
        # thread pool spin-up) -- run one throwaway call so timed results
        # reflect steady-state performance, not cold start.
        print("Warm-up call (not timed)...")
        await backend.translate("Warm up sentence.", "hi")

    for i, sentence in enumerate(SENTENCES, start=1):
        segment_id = f"seg_{i}"
        for lang in TARGET_LANGUAGES:
            translated = await tracker.timed_translate(
                backend, sentence, lang, segment_id, mode="segment"
            )
            print(f"[{lang}] {sentence!r} -> {translated!r}")

    print()
    print(json.dumps(tracker.full_report(), indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model-dir",
        required=True,
        help="Path to the CTranslate2-converted NLLB-200 directory (see setup steps above).",
    )
    parser.add_argument(
        "--source-lang",
        default="en",
        help="App-level source language code (default: en), matching backends.py's LANG_TO_FLORES keys.",
    )
    parser.add_argument(
        "--no-warmup",
        action="store_true",
        help="Skip the warm-up call and time the very first call too.",
    )
    args = parser.parse_args()

    asyncio.run(main(args.model_dir, args.source_lang, warmup=not args.no_warmup))
