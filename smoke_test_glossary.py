"""
Standalone sanity check for glossary.py's core assumption: that
GlossaryAwareTranslationBackend's placeholder tokens survive a real NLLB-200
translation call intact (see glossary.py's module docstring CAVEAT). No
audio, no server -- just glossary-protected text in, translated text out, so
you can eyeball whether protected terms come back correctly *before*
trusting this in front of an audience.

Run on the machine that will host the server, after backends.py's NLLB
setup steps:

    python smoke_test_glossary.py /path/to/nllb-200-ct2 [glossary.json]

If glossary.json is omitted, a small built-in sample glossary is used
instead (CUDA, Transformer, Diffusion Model, Tensor Core).
"""

import asyncio
import sys

from backends import RealNLLBBackend
from glossary import Glossary, GlossaryAwareTranslationBackend, GlossaryTerm

SAMPLE_SENTENCES = [
    "This talk covers how the Transformer architecture uses CUDA for training.",
    "We use a Diffusion Model accelerated by Tensor Core hardware.",
    "Without any glossary terms this sentence should translate normally.",
]

SAMPLE_GLOSSARY = Glossary([
    GlossaryTerm(term="CUDA"),  # preserve verbatim
    GlossaryTerm(term="Tensor Core"),  # preserve verbatim, multi-word
    GlossaryTerm(term="Transformer", translations={"hi": "ट्रांसफार्मर", "fr": "Transformer"}),
    GlossaryTerm(term="Diffusion Model", translations={"hi": "डिफ्यूज़न मॉडल"}),
])

TEST_LANGS = ["hi", "fr"]


async def main(model_dir: str, glossary_path: str | None) -> None:
    glossary = Glossary.load(glossary_path) if glossary_path else SAMPLE_GLOSSARY
    print(f"Loading NLLB-200 from {model_dir} ...")
    translator = GlossaryAwareTranslationBackend(
        RealNLLBBackend(model_dir=model_dir, source_lang="en"), glossary
    )
    print(f"Loaded. Glossary has {len(glossary)} term(s).\n")

    any_failure = False
    for sentence in SAMPLE_SENTENCES:
        print(f"SOURCE: {sentence}")
        protected_text, placeholder_map = glossary.protect(sentence)
        for lang in TEST_LANGS:
            translated = await translator.translate(sentence, lang)
            print(f"  [{lang}] {translated}")

            for glossary_term, matched_text in placeholder_map.values():
                expected = glossary_term.translation_for(lang, matched_text)
                if expected not in translated:
                    any_failure = True
                    print(
                        f"    !! expected {expected!r} (for glossary term "
                        f"{glossary_term.term!r}) not found in the [{lang}] output above -- "
                        f"the placeholder may not have survived translation intact. "
                        f"See glossary.py's module docstring CAVEAT."
                    )
        print()

    if any_failure:
        print(
            "SOME GLOSSARY TERMS DID NOT COME THROUGH CORRECTLY (see !! lines above). "
            "Do not rely on this glossary live until this is resolved -- consider a "
            "different placeholder format in glossary.py's _make_placeholder(), or "
            "translating the sentence with the term removed entirely as a fallback."
        )
        sys.exit(1)
    else:
        print("All glossary terms came through correctly in every test sentence/language above.")


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        print("Usage: python smoke_test_glossary.py /path/to/nllb-200-ct2 [glossary.json]")
        sys.exit(1)
    model_dir = sys.argv[1]
    glossary_path = sys.argv[2] if len(sys.argv) == 3 else None
    asyncio.run(main(model_dir, glossary_path))