"""
Conference glossary adaptation -- Option 2: preserve or correctly translate
domain-specific technical terms (paper/slide/abstract vocabulary -- "CUDA",
"Transformer", "Diffusion Model", "Tensor Core", ...) that NLLB-200, trained
on general text, is prone to mistranslating or garbling in a technical talk.

Research question this is designed to let you answer (Section 8):
    "Does domain-specific glossary adaptation improve translation quality
    for technical talks?"
Metrics: BLEU / COMET against reference translations (see evaluate_accuracy.py
-- point --nllb-model-dir there at a GlossaryAwareTranslationBackend-wrapped
translator to compare with/without), plus human evaluation.

-----------------------------------------------------------------------------
Approach: placeholder substitution ("term protection")
-----------------------------------------------------------------------------

NLLB-200 (like most seq2seq MT models) has no built-in mechanism for forcing
a specific translation of a specific term mid-sentence -- constrained
decoding and retraining are both out of scope for a presenter's laptop the
night before a talk. Placeholder substitution sidesteps that:

    1. Before translation, each glossary term found in the source text is
       replaced with a unique placeholder token built from Unicode Private
       Use Area characters (e.g. "\uE000 3 \uE000") that ordinary
       conference text will never contain.
    2. The placeholder text is translated normally by the wrapped
       TranslationBackend.
    3. After translation, each placeholder is swapped back for either the
       glossary's specified per-language translation (e.g. "Transformer" ->
       "ट्रांसफार्मर"), or -- if none is specified for that language -- the
       original source term verbatim (e.g. "CUDA" stays "CUDA").

This means a glossary term is never actually seen by the translation model
at all; correctness depends entirely on this module's substitution, not on
NLLB "learning" the term from surrounding context.

CAVEAT, stated plainly rather than glossed over: this assumes NLLB passes
an unfamiliar placeholder token through its sentencepiece tokenizer and
decoder without splitting, dropping, or duplicating it. That is a real
engineering assumption, not a guarantee -- verify it on your actual
--nllb-model-dir with smoke_test_glossary.py before trusting this in front
of an audience. If a given placeholder style turns out not to survive
translation intact, GlossaryTerm/Glossary's placeholder format is isolated
to _make_placeholder()/_PLACEHOLDER_RE below, so an alternative style is a
localized change.

-----------------------------------------------------------------------------
Building a glossary
-----------------------------------------------------------------------------

Two ways to get terms into a Glossary:

    1. Automatic candidate extraction from conference materials (papers,
       slides, abstracts) via extract_candidate_terms() / build_glossary.py
       -- a heuristic first pass (capitalized multi-word phrases, acronyms,
       frequency-filtered against common English words), NOT a guarantee of
       correctness. Always human-review the output before using it live --
       see build_glossary.py's docstring.
    2. Hand-written: construct GlossaryTerm(...) entries directly, or edit
       the JSON build_glossary.py writes.

-----------------------------------------------------------------------------
Wiring into server.py
-----------------------------------------------------------------------------

    from glossary import Glossary, GlossaryAwareTranslationBackend
    from backends import RealNLLBBackend
    from pipeline import Pipeline

    glossary = Glossary.load("glossary.json")
    translator = GlossaryAwareTranslationBackend(
        RealNLLBBackend(model_dir="nllb-200-ct2"), glossary
    )
    pipeline = Pipeline(
        asr=RealWhisperBackend(model_size="small"),
        translator=translator,           # satisfies pipeline.py's TranslationBackend protocol
        broadcast=broadcast_caption,
        subscribed_languages=lambda: subscribers.keys(),
        cache=SemanticCache(),           # optional -- caches the final, glossary-restored text
    )
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pipeline import TranslationBackend


# ---------------------------------------------------------------------------
# Glossary data model
# ---------------------------------------------------------------------------

@dataclass
class GlossaryTerm:
    """One protected term. `translations` maps target language code -> the
    translation to use for that language; a language with no entry (or an
    empty `translations` dict entirely) falls back to preserving the term
    verbatim, exactly as it appeared in the source text."""

    term: str
    translations: dict[str, str] = field(default_factory=dict)
    case_sensitive: bool = False

    def translation_for(self, lang: str, matched_text: str) -> str:
        return self.translations.get(lang, matched_text)


class Glossary:
    """A collection of GlossaryTerms, with protect()/restore() for wrapping
    a TranslationBackend (see GlossaryAwareTranslationBackend below)."""

    def __init__(self, terms: list[GlossaryTerm] | None = None) -> None:
        # Longest term first: protect() must match "Tensor Core" before it
        # considers matching "Tensor" alone, or the multi-word term would
        # never get a chance -- see protect()'s docstring.
        self._terms = sorted(terms or [], key=lambda t: len(t.term), reverse=True)

    def __len__(self) -> int:
        return len(self._terms)

    def __iter__(self):
        return iter(self._terms)

    def add(self, term: GlossaryTerm) -> None:
        self._terms.append(term)
        self._terms.sort(key=lambda t: len(t.term), reverse=True)

    @classmethod
    def preserve_only(cls, terms: list[str]) -> "Glossary":
        """Quick-start constructor: a glossary that preserves every given
        term verbatim (no per-language translations) -- the safe default
        for a term you haven't decided how to translate yet, or one that
        genuinely shouldn't be translated at all (product names, code
        identifiers like "CUDA")."""
        return cls([GlossaryTerm(term=t) for t in terms])

    # -- persistence -----------------------------------------------------

    @classmethod
    def load(cls, path: str | Path) -> "Glossary":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise SystemExit(f"{path} should contain a JSON list of glossary term objects")
        terms = [
            GlossaryTerm(
                term=entry["term"],
                translations=entry.get("translations", {}),
                case_sensitive=entry.get("case_sensitive", False),
            )
            for entry in data
        ]
        return cls(terms)

    def save(self, path: str | Path) -> None:
        data = [
            {"term": t.term, "translations": t.translations, "case_sensitive": t.case_sensitive}
            for t in self._terms
        ]
        Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    # -- protect / restore -------------------------------------------------

    def protect(self, text: str) -> tuple[str, dict[str, tuple[GlossaryTerm, str]]]:
        """Replace every glossary term found in `text` with a placeholder.

        Terms are matched longest-first and whole-word (via regex word
        boundaries) so "Tensor Core" is protected as one unit rather than
        "Tensor" and "Core" separately, and so "Transformer" doesn't match
        inside an unrelated longer word. Processing longest-to-shortest also
        means once a span is replaced with a placeholder, its original text
        is gone -- a shorter term that happened to be a substring of an
        already-protected phrase can't accidentally match again inside the
        placeholder itself.

        Returns (protected_text, placeholder_map), where placeholder_map
        maps each placeholder token used back to (the GlossaryTerm that
        matched, the exact original substring matched -- preserving its
        original casing for verbatim restoration).
        """
        placeholder_map: dict[str, tuple[GlossaryTerm, str]] = {}
        counter = 0

        for glossary_term in self._terms:
            flags = 0 if glossary_term.case_sensitive else re.IGNORECASE
            pattern = re.compile(r"\b" + re.escape(glossary_term.term) + r"\b", flags)

            def _replace(match: re.Match, glossary_term=glossary_term) -> str:
                nonlocal counter
                placeholder = _make_placeholder(counter)
                counter += 1
                placeholder_map[placeholder] = (glossary_term, match.group(0))
                return placeholder

            text = pattern.sub(_replace, text)

        return text, placeholder_map

    def restore(self, text: str, placeholder_map: dict[str, tuple[GlossaryTerm, str]], lang: str) -> str:
        """Inverse of protect(): swap each placeholder back for either the
        glossary's per-language translation, or the originally matched
        surface text if no translation was specified for `lang`.

        Placeholders are matched by literal substring, not by re-deriving
        the counter pattern, so this is robust to a translation model that
        reordered or re-cased the placeholder's surrounding punctuation --
        as long as the placeholder character sequence itself survived
        translation intact (see this module's docstring for that caveat).
        """
        for placeholder, (glossary_term, matched_text) in placeholder_map.items():
            if placeholder in text:
                text = text.replace(placeholder, glossary_term.translation_for(lang, matched_text))
        return text


# Private Use Area characters sandwiching an index -- see module docstring's
# CAVEAT for why this specific format is an assumption, not a guarantee.
_PLACEHOLDER_PREFIX = "\uE000"
_PLACEHOLDER_SUFFIX = "\uE001"


def _make_placeholder(index: int) -> str:
    return f"{_PLACEHOLDER_PREFIX}{index}{_PLACEHOLDER_SUFFIX}"


# ---------------------------------------------------------------------------
# TranslationBackend wrapper
# ---------------------------------------------------------------------------

class GlossaryAwareTranslationBackend:
    """Wraps any TranslationBackend (pipeline.py's protocol -- Fake or
    Real) with glossary protect()/restore(). Drop-in replacement anywhere a
    plain TranslationBackend is used; Pipeline never needs to know a
    glossary is involved.

    Order relative to a TranslationCache (translation_cache.py): wrap the
    real translator with this *before* handing it to Pipeline, and let
    Pipeline's cache (if any) sit on the outside as usual -- the cache then
    stores the final, glossary-restored text, which is what you actually
    want reused on a cache hit.
    """

    def __init__(self, inner: "TranslationBackend", glossary: Glossary) -> None:
        self._inner = inner
        self._glossary = glossary

    async def translate(self, text: str, target_lang: str) -> str:
        protected_text, placeholder_map = self._glossary.protect(text)
        translated = await self._inner.translate(protected_text, target_lang)
        return self._glossary.restore(translated, placeholder_map, target_lang)


# ---------------------------------------------------------------------------
# Candidate term extraction (heuristic -- always human-review the output)
# ---------------------------------------------------------------------------

# Common English words that are still frequently capitalized (sentence
# starts, headers) and would otherwise flood a naive "capitalized word"
# extractor with false positives. Not exhaustive -- extraction is a first
# pass, not a final answer; see the module and build_glossary.py docstrings.
_COMMON_WORDS = {
    "the", "a", "an", "this", "that", "these", "those", "we", "our", "you",
    "your", "it", "its", "in", "on", "at", "to", "for", "of", "and", "or",
    "but", "with", "as", "is", "are", "was", "were", "be", "been", "will",
    "can", "may", "if", "then", "than", "so", "not", "no", "yes", "do",
    "does", "did", "have", "has", "had", "figure", "table", "section",
    "chapter", "introduction", "conclusion", "abstract", "results",
    "discussion", "methods", "related", "work", "future", "acknowledgments",
    "references", "appendix", "please", "thank", "thanks", "welcome",
    "today", "here", "there", "now", "next", "first", "second", "third",
}

_ACRONYM_RE = re.compile(r"\b[A-Z]{2,6}(?:-[A-Z0-9]+)?\b")
_TITLECASE_PHRASE_RE = re.compile(
    r"\b[A-Z][a-zA-Z0-9]*(?:[- ][A-Z][a-zA-Z0-9]*){0,2}\b"
)


def extract_candidate_terms(text: str, min_occurrences: int = 2) -> list[str]:
    """Heuristic candidate-term extraction from raw conference-material
    text: acronyms (CUDA, RAG, LSTM) and Title Case / CamelCase phrases up
    to three words (Transformer, Diffusion Model, Tensor Core), each kept
    only if they occur at least `min_occurrences` times -- a term used once
    is more likely a stray capitalized word than real recurring vocabulary
    for this talk.

    This is a first-pass filter, not a classifier: it will both miss real
    terms (e.g. ones only ever written lowercase, like "backpropagation")
    and include false positives (a paper's own capitalized project name,
    an author's surname mentioned twice). Always review the output --
    build_glossary.py writes it as an editable glossary.json specifically
    so a human can prune/correct it before the conference, not use it as
    a finished glossary straight off the extractor.
    """
    candidate_terms: set[str] = set()

    for match in _ACRONYM_RE.finditer(text):
        word = match.group(0)
        if word.lower() not in _COMMON_WORDS:
            candidate_terms.add(word)

    for match in _TITLECASE_PHRASE_RE.finditer(text):
        # A capitalized sentence-starter ("Our", "The") can get swallowed
        # into the front of an otherwise-real phrase ("Our Diffusion Model"),
        # which would otherwise fragment "Diffusion Model" into two
        # differently-spelled, separately-undercounted candidates. Strip any
        # leading common words before treating what's left as a candidate.
        words = match.group(0).split()
        while words and words[0].lower() in _COMMON_WORDS:
            words.pop(0)
        if not words:
            continue
        phrase = " ".join(words)
        if len(phrase) < 3:
            continue
        candidate_terms.add(phrase)

    # Count each distinct candidate's real occurrences across the whole
    # text in one pass per term -- rather than accumulating hits per-regex,
    # which would double-count a string (e.g. "CUDA") that both the acronym
    # and title-case patterns happen to match at the same position.
    counts = {
        term: len(re.findall(r"\b" + re.escape(term) + r"\b", text))
        for term in candidate_terms
    }

    kept = [term for term, count in counts.items() if count >= min_occurrences]
    # Longest, then alphabetical -- stable, readable order for the JSON
    # a human is about to review, and matches Glossary's own longest-first
    # matching priority.
    return sorted(kept, key=lambda t: (-len(t), t))


def extract_text_from_file(path: str | Path) -> str:
    """Extracts plain text from a conference-material file for
    extract_candidate_terms() to run over. .txt/.md need no dependency;
    .pdf is supported via pypdf (deferred import, same pattern as
    backends.py's heavy deps). Slides/abstracts saved or exported as .txt
    or .pdf both work -- there's no .pptx/.docx reader here to keep this
    module's default dependency footprint at zero; export those to PDF or
    plain text first, or extend this function with python-pptx/python-docx
    if you'd rather read them directly.
    """
    path = Path(path)
    suffix = path.suffix.lower()

    if suffix in (".txt", ".md"):
        return path.read_text(encoding="utf-8", errors="replace")

    if suffix == ".pdf":
        from pypdf import PdfReader  # deferred: optional dep

        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)

    raise ValueError(
        f"Don't know how to extract text from {path.suffix!r} files ({path}). "
        f"Supported: .txt, .md, .pdf -- export slides/abstracts to one of these first."
    )