"""
Slide-Synchronized Dynamic Glossary -- Option 3.

Extends glossary.py's static, pre-built glossary (Option 2) with a
mid-session update loop: periodically capture the presenter's screen (or a
region of it -- the slide-display area), OCR whatever's currently on
screen, and merge newly-seen recurring technical terms into the live
Glossary a running Pipeline is already reading from -- so a term that only
appears on slide 40 gets protected by the time the presenter actually
reaches slide 40, without needing the whole deck pre-processed and reviewed
ahead of time the way build_glossary.py's static workflow (Option 2)
requires.

    Screen -> Screenshot -> OCR -> candidate terms -> merge into Glossary
       ^                                                       |
       |_______________________ poll every N seconds  _________|

This reuses glossary.py's Glossary and extract_candidate_terms() as-is --
the new piece here is purely the capture/OCR/dedup/merge loop.

-----------------------------------------------------------------------------
Setup
-----------------------------------------------------------------------------

    pip install mss pytesseract pillow --break-system-packages

pytesseract is a Python wrapper, not an OCR engine -- the actual Tesseract
binary must be installed separately and be on PATH:
    Windows: https://github.com/UB-Mannheim/tesseract/wiki
    macOS:   brew install tesseract
    Linux:   apt install tesseract-ocr

-----------------------------------------------------------------------------
Privacy / scope caveat
-----------------------------------------------------------------------------

A full-screen capture picks up whatever is actually on screen, including
notifications, other open windows, or presenter notes if not in a clean
slideshow view. Pass `region=` to restrict capture to just the
slide-display area (e.g. the projector output), or run the presenter's
device in a dedicated full-screen slideshow mode with nothing else visible.
This module never sends the captured image anywhere -- OCR runs locally,
same as the rest of the pipeline's "no traffic leaves the local network"
design -- but it's still worth being deliberate about what's on screen.

-----------------------------------------------------------------------------
Design notes
-----------------------------------------------------------------------------

A term only gets counted from a given slide ONCE per distinct slide
(deduplicated by a hash of the OCR'd text), even if that slide stays on
screen across many poll intervals -- otherwise a slide sitting on screen
for two minutes at a 5-second poll interval would look like the term
appeared 24 times, and every term would hit min_occurrences on its very
first slide. A term still needs to occur `min_occurrences` times across
accumulated text: either repeated within one slide's own text, or by
reappearing on a later, genuinely different slide.

-----------------------------------------------------------------------------
Wiring into server.py
-----------------------------------------------------------------------------

    from dynamic_glossary import DynamicGlossaryUpdater
    from glossary import Glossary, GlossaryAwareTranslationBackend

    glossary = Glossary.load("glossary.json")  # or Glossary() to start empty
    translator = GlossaryAwareTranslationBackend(RealNLLBBackend(...), glossary)

    updater = DynamicGlossaryUpdater(glossary, region=SLIDE_REGION)
    asyncio.create_task(updater.run())   # background loop, session lifetime
    ...
    updater.stop()                       # on session end
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass
from typing import Callable, Optional

from glossary import Glossary, GlossaryTerm, extract_candidate_terms


@dataclass
class TermAddedEvent:
    """One entry in DynamicGlossaryUpdater's log -- when a term was first
    added to the live glossary, and at what poll count / session time. This
    is what makes Option 3's research question ("does term accuracy improve
    once a term first appears on-screen, versus before it was seen")
    measurable after the fact: cross-reference this log's timestamps
    against when each term's audio occurrences happened in the session
    transcript."""

    term: str
    added_at_seconds: float
    poll_index: int


class DynamicGlossaryUpdater:
    """Background polling loop: capture screen -> OCR -> extract recurring
    terms -> merge new ones into `glossary` (mutated in place, the same
    Glossary instance a live GlossaryAwareTranslationBackend is reading
    from -- no restart or hot-swap needed for a newly added term to take
    effect on the very next segment)."""

    def __init__(
        self,
        glossary: Glossary,
        poll_interval_seconds: float = 5.0,
        min_occurrences: int = 2,
        region: Optional[dict] = None,
        ocr_fn: Optional[Callable[[], str]] = None,
        on_term_added: Optional[Callable[[TermAddedEvent], None]] = None,
    ) -> None:
        """`ocr_fn`, if given, replaces the real screen-capture-plus-OCR
        step with any zero-arg `() -> str` callable returning "whatever
        text is on screen right now". This is what lets
        test_dynamic_glossary.py exercise the polling/dedup/merge logic
        below with scripted slide text instead of a real screen and
        Tesseract -- same dependency-injection pattern as
        translation_cache.py's SemanticCache taking an embed_fn override.
        """
        self.glossary = glossary
        self.poll_interval_seconds = poll_interval_seconds
        self.min_occurrences = min_occurrences
        self.region = region
        self._ocr_fn = ocr_fn or (lambda: _capture_and_ocr(region))
        self._on_term_added = on_term_added

        self._accumulated_text: list[str] = []
        self._last_slide_hash: Optional[str] = None
        self._known_terms: set[str] = {t.term for t in glossary}
        self.log: list[TermAddedEvent] = []
        self._poll_count = 0
        self._start_time: Optional[float] = None
        self._stopped = asyncio.Event()

    async def run(self) -> None:
        """Runs until stop() is called -- intended to be launched as a
        background asyncio task for the lifetime of a presenter session.
        The (possibly blocking) capture/OCR step runs off the event loop
        via poll_once_async so it never stalls the WebSocket handling the
        rest of the pipeline shares the loop with."""
        self._start_time = time.monotonic()
        self._stopped.clear()
        while not self._stopped.is_set():
            await self.poll_once_async()
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=self.poll_interval_seconds)
            except asyncio.TimeoutError:
                pass

    def stop(self) -> None:
        self._stopped.set()

    def poll_once(self) -> list[str]:
        """Runs one capture-OCR-extract-merge cycle synchronously. Returns
        any newly added terms this poll. Exposed directly (in addition to
        poll_once_async) so tests and manual/CLI use don't need an event
        loop just to drive one cycle."""
        self._poll_count += 1
        if self._start_time is None:
            self._start_time = time.monotonic()

        text = self._ocr_fn()

        slide_hash = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()
        if text.strip() and slide_hash != self._last_slide_hash:
            self._accumulated_text.append(text)
            self._last_slide_hash = slide_hash

        if not self._accumulated_text:
            return []

        combined = "\n".join(self._accumulated_text)
        candidates = extract_candidate_terms(combined, min_occurrences=self.min_occurrences)

        newly_added = []
        for term in candidates:
            if term in self._known_terms:
                continue
            self._known_terms.add(term)
            self.glossary.add(GlossaryTerm(term=term))
            newly_added.append(term)
            event = TermAddedEvent(
                term=term,
                added_at_seconds=time.monotonic() - self._start_time,
                poll_index=self._poll_count,
            )
            self.log.append(event)
            if self._on_term_added is not None:
                self._on_term_added(event)

        return newly_added

    async def poll_once_async(self) -> list[str]:
        """Same as poll_once(), but runs the capture/OCR step off the
        event loop via asyncio.to_thread -- use this (or run()) rather
        than poll_once() directly from async server code."""
        return await asyncio.to_thread(self.poll_once)


def _capture_and_ocr(region: Optional[dict]) -> str:
    """Real capture+OCR step: screenshot via mss, text via pytesseract.
    Deferred imports -- same reasoning as backends.py/translation_cache.py:
    importing this module must never require mss/pytesseract/an actual
    Tesseract install just to construct or unit-test a
    DynamicGlossaryUpdater."""
    import mss
    from PIL import Image
    import pytesseract

    with mss.mss() as sct:
        monitor = region or sct.monitors[1]  # monitors[0] is "all monitors combined"
        raw = sct.grab(monitor)
        image = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

    return pytesseract.image_to_string(image)