"""
talk_glossary.py -- "teach DwaniLive your slides".

The presenter uploads their slides or notes (.pptx, .pdf, .docx, .txt, .md);
we pull out the technical vocabulary (acronyms, Title-Case phrases, repeated
jargon), the presenter ticks the ones to keep, and from that moment -- no
restart -- every term is:

  * kept intact in translation   (glossary.GlossaryAwareTranslationBackend
                                   replaces it with a placeholder NLLB can't
                                   mangle, then puts it back), and
  * suggested to Whisper          (initial_prompt), so "LSTM", "Kubernetes" or
                                   "gradient descent" are heard as such.

The terms are saved on the laptop (glossary.json in the app's data folder),
so slides uploaded the night before are in place for the morning's class.

.pptx and .docx are ZIP files of XML; they're read with the standard library,
no extra dependency. PDF uses pypdf (bundled with the desktop app).
"""

from __future__ import annotations

import io
import json
import re
import threading
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from glossary import Glossary, GlossaryTerm, extract_candidate_terms

MAX_UPLOAD_BYTES = 40 * 1024 * 1024
MAX_TERMS = 200
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


# ---------------------------------------------------------------------------
# text extraction
# ---------------------------------------------------------------------------

def _slide_no(name: str) -> int:
    m = re.search(r"(\d+)\.xml$", name)
    return int(m.group(1)) if m else 0


def text_from_pptx(data: bytes) -> str:
    """All slide text + speaker notes, in slide order (one paragraph per line)."""
    out = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = [n for n in z.namelist() if re.fullmatch(r"ppt/(slides/slide|notesSlides/notesSlide)\d+\.xml", n)]
        for name in sorted(names, key=lambda n: (_slide_no(n), "notes" in n)):
            root = ET.fromstring(z.read(name))
            for para in root.iter(f"{_A}p"):
                line = "".join(t.text or "" for t in para.iter(f"{_A}t")).strip()
                if line:
                    out.append(line)
    return "\n".join(out)


def text_from_docx(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    return "\n".join("".join(t.text or "" for t in p.iter(f"{_W}t")) for p in root.iter(f"{_W}p"))


def text_from_pdf(data: bytes) -> str:
    from pypdf import PdfReader  # bundled with the desktop app

    reader = PdfReader(io.BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def extract_text(filename: str, data: bytes) -> str:
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("That file is larger than 40 MB. Export just the slides as PDF and try again.")
    ext = Path(filename or "").suffix.lower()
    try:
        if ext == ".pptx":
            return text_from_pptx(data)
        if ext == ".docx":
            return text_from_docx(data)
        if ext == ".pdf":
            return text_from_pdf(data)
        if ext in (".txt", ".md"):
            return data.decode("utf-8", errors="replace")
    except (zipfile.BadZipFile, KeyError, ET.ParseError):
        raise ValueError("Couldn't read that file. Is it a real .pptx / .docx? Try exporting it as PDF.") from None
    if ext in (".ppt", ".doc", ".key", ".odp"):
        raise ValueError(f"{ext} isn't supported. Save it as .pptx or PDF and upload that.")
    raise ValueError("Upload slides or notes as .pptx, .pdf, .docx, .txt or .md.")


# ---------------------------------------------------------------------------
# picking terms
# ---------------------------------------------------------------------------

_LOWER_STOP = set("""the and for with that this from into over under about which their there these those have has
been were will would could should what when where while your our you they them then than more most some such
also only very each into using used use based other first second third data model models value values result
results example examples figure table section slide slides page pages time number""".split())


def candidates(text: str, limit: int = 60) -> list[dict]:
    """Ranked candidate terms. `suggested` = ticked by default: acronyms and
    anything that appears at least twice (a capitalised word used once is
    often just a slide title)."""
    once = extract_candidate_terms(text, min_occurrences=1)
    out = []
    for term in once:
        n = len(re.findall(r"\b" + re.escape(term) + r"\b", text))
        acronym = term.isupper() and 2 <= len(term) <= 8
        out.append({"term": term, "count": n, "suggested": acronym or n >= 2})
    # lowercase two-word jargon repeated across slides ("gradient descent"), counted line by line
    seen = {c["term"].lower() for c in out}
    counts: dict[str, int] = {}
    for line in text.lower().splitlines():
        words = re.findall(r"[a-z][a-z-]+", line)
        for w1, w2 in zip(words, words[1:]):
            if len(w1) > 3 and len(w2) > 3 and w1 not in _LOWER_STOP and w2 not in _LOWER_STOP:
                counts[f"{w1} {w2}"] = counts.get(f"{w1} {w2}", 0) + 1
    for p, n in counts.items():
        if n >= 3 and p not in seen:
            out.append({"term": p, "count": n, "suggested": True})
    # Drop noise that would only make the presenter untick things: a single
    # capitalised word seen once is almost always a sentence start ("Use",
    # "Questions"), and "Why LSTM" / "Mention CUDA" are a real term with a
    # stray word in front of it.
    good = [c["term"].lower() for c in out if c["suggested"]]
    def noise(c):
        if c["suggested"]:
            return False
        t = c["term"].lower()
        if " " not in t:
            return True
        return any(re.search(r"\b" + re.escape(g) + r"\b", t) for g in good if g != t)
    out = [c for c in out if not noise(c)]
    out.sort(key=lambda c: (not c["suggested"], -c["count"], c["term"].lower()))
    return out[:limit]


# ---------------------------------------------------------------------------
# the live glossary
# ---------------------------------------------------------------------------

class TalkGlossary:
    """Owns the Glossary object the translator wraps, keeps Whisper's prompt
    in step with it, and saves the presenter's terms to disk."""

    def __init__(self, glossary: Glossary, path: Path | None, asr=None):
        self.glossary = glossary
        self.path = Path(path) if path else None
        self.asr = asr
        self._lock = threading.Lock()
        self.terms: list[str] = []
        if self.path and self.path.exists():
            try:
                saved = json.loads(self.path.read_text(encoding="utf-8")).get("terms", [])
                self.set_terms(saved, save=False)
            except (ValueError, OSError):
                pass

    def set_terms(self, terms: list[str], save: bool = True) -> list[str]:
        clean: list[str] = []
        for t in terms:
            t = " ".join(str(t).split())[:60]
            if t and t.lower() not in {c.lower() for c in clean}:
                clean.append(t)
        clean = clean[:MAX_TERMS]
        with self._lock:
            # replace only the terms we manage; terms from --glossary-file etc. stay
            mine = {t.lower() for t in self.terms}
            kept = [gt for gt in list(self.glossary) if gt.term.lower() not in mine]
            # Glossary matches longest-first (see glossary.Glossary.__init__)
            self.glossary._terms = sorted(kept + [GlossaryTerm(term=t) for t in clean],
                                          key=lambda gt: len(gt.term), reverse=True)
            self.terms = clean
            self._update_whisper()
            if save and self.path:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps({"terms": clean}, ensure_ascii=False, indent=1), encoding="utf-8")
        return clean

    def add(self, terms: list[str]) -> list[str]:
        return self.set_terms(self.terms + list(terms))

    def remove(self, terms: list[str]) -> list[str]:
        drop = {t.lower() for t in terms}
        return self.set_terms([t for t in self.terms if t.lower() not in drop])

    def _update_whisper(self) -> None:
        """Whisper reads initial_prompt on every transcription, so changing it
        takes effect from the next sentence."""
        if self.asr is not None and hasattr(self.asr, "_initial_prompt"):
            self.asr._initial_prompt = self.glossary.as_whisper_prompt() if self.terms else None

    def status(self) -> dict:
        return {"terms": list(self.terms), "whisper_hint": bool(getattr(self.asr, "_initial_prompt", None)),
                "max": MAX_TERMS}
