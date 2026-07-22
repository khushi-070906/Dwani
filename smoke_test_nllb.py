"""
Standalone sanity check for RealNLLBBackend -- no audio, no server, just
text-in/text-out, so tokenizer/ctranslate2 issues surface here rather than
buried inside a live presenter session. Run on the machine that will host
the server, after step 1-2 in the setup instructions:

    python smoke_test_nllb.py /path/to/nllb-200-ct2
"""

import asyncio
import sys

from backends import RealNLLBBackend

SAMPLE_TEXT = "Welcome everyone to the seminar. Please find your seats."
TEST_LANGS = ["hi", "fr", "es", "ta"]


async def main(model_dir: str) -> None:
    print(f"Loading NLLB-200 from {model_dir} ...")
    translator = RealNLLBBackend(model_dir=model_dir, source_lang="en")
    print("Loaded. Translating a sample sentence into a few languages:\n")

    for lang in TEST_LANGS:
        result = await translator.translate(SAMPLE_TEXT, lang)
        print(f"  [{lang}] {result}")

    print(
        "\nIf these look like real, coherent translations (not gibberish, "
        "not English echoed back, not empty strings), NLLB is wired up "
        "correctly and you're clear to run the full server with "
        "--nllb-model-dir."
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python smoke_test_nllb.py /path/to/nllb-200-ct2")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))