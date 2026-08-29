"""
nllb_tokenizer.py

Minimal, dependency-light NLLB-200 tokenizer -- reimplements exactly the
piece of transformers.AutoTokenizer / NllbTokenizer that backends.py and
qa_pipeline.py actually used, using only the `sentencepiece` package
directly.

Why this exists: importing transformers.AutoTokenizer pulls in
transformers' full "Auto" model registry, which touches every model
architecture the library ships (100+), and transformers itself depends on
torch. None of that is needed at runtime here -- NLLB-200 tokenization,
for what this project actually does with it, is just:

    source side:  [source_lang_code_token, *sentencepiece_pieces(text), "</s>"]
    target side:  ctranslate2's `target_prefix` already forces the first
                  generated token to the target language code -- unchanged,
                  see backends.py / qa_pipeline.py.

Both language-code tokens (e.g. "eng_Latn") and "</s>" are literal string
tokens in NLLB's shared vocabulary, and ctranslate2's Translator already
works on these token strings directly rather than numeric ids (see the
existing target_prefix=[[...]] calls) -- so no id mapping is needed at
all, just plain sentencepiece subword encoding plus a couple of literal
strings glued on either end.

-----------------------------------------------------------------------------
Setup (presenter's device, at RUNTIME)
-----------------------------------------------------------------------------
    pip install sentencepiece
    (no transformers, no torch needed on the presenter's device anymore --
    those are still needed on whichever machine runs the one-time
    ct2-transformers-converter model-conversion step, per backends.py's
    docstring, but that machine doesn't have to be the presenter's laptop)

You still need the raw `sentencepiece.bpe.model` file NLLB-200 ships in its
Hugging Face repo -- get it ONCE, on a machine with internet. Easiest way,
with no transformers/torch dependency at all:

    pip install huggingface_hub
    python -c "
from huggingface_hub import hf_hub_download
path = hf_hub_download('facebook/nllb-200-distilled-600M', 'sentencepiece.bpe.model')
print(path)
"

Then copy that file alongside your `nllb-200-ct2` model directory on the
presenter's device, e.g.:

    nllb-200-ct2/
        model.bin
        shared_vocabulary.txt
        ...
    sentencepiece.bpe.model      <- this file, same folder level

(If you already ran ct2-transformers-converter on some machine, this file
is also sitting in that machine's Hugging Face cache as a side effect --
under ~/.cache/huggingface/hub/models--facebook--nllb-200-distilled-600M/
-- so you can copy it from there instead of re-downloading.)
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Union

# Every literal special-token string NLLB's tokenizer can produce besides
# ordinary sentencepiece subword pieces. Stripped from model output before
# detokenizing -- the same job transformers' skip_special_tokens=True did.
_SPECIAL_TOKENS = {"</s>", "<s>", "<pad>", "<unk>"}


class NllbLiteTokenizer:
    """Encodes text into the exact token-string sequence NLLB-200 expects,
    and decodes ctranslate2's output token strings back into text -- using
    only `sentencepiece`, matching what `transformers.AutoTokenizer` did
    for this project's actual usage in backends.py / qa_pipeline.py, and
    nothing more than that.
    """

    def __init__(self, sentencepiece_model_path: Union[str, Path]) -> None:
        import sentencepiece as spm  # deferred: keeps this module importable without it too

        path = Path(sentencepiece_model_path)
        if not path.exists():
            raise FileNotFoundError(
                f"NLLB sentencepiece model not found at {path}. See this module's "
                f"docstring for how to fetch sentencepiece.bpe.model once, offline-safe "
                f"from then on."
            )

        self._sp = spm.SentencePieceProcessor()
        self._sp.load(str(path))
        self._special_tokens = set(_SPECIAL_TOKENS)

    def encode_source(self, text: str, source_flores_code: str) -> List[str]:
        """[source_lang_token, *subword_pieces(text), eos_token] -- exactly
        the sequence transformers' NllbTokenizer.__call__() built when
        src_lang was set: prefix=[src_lang_id], suffix=[eos_id], per HF's
        build_inputs_with_special_tokens for this tokenizer family.
        """
        pieces = self._sp.encode(text, out_type=str)
        return [source_flores_code, *pieces, "</s>"]

    def decode_target(self, tokens: List[str]) -> str:
        """Inverse of encode_source's subword step -- strips any special
        tokens ctranslate2's output might still include (defensively;
        exact behavior can vary by ctranslate2/model version), then
        detokenizes the remaining sentencepiece pieces back into text.
        """
        pieces = [t for t in tokens if t not in self._special_tokens]
        return self._sp.decode_pieces(pieces).strip()
