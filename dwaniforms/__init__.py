"""DwaniForm -- offline voice form filling built on the DwaniLive pipeline.

Reuses (unchanged): backends.RealWhisperBackend (ASR), backends.RealNLLBBackend
(translation), glossary.GlossaryAwareTranslationBackend (term protection),
pipeline.AudioSegment, backends.LANG_TO_FLORES (language codes).

Adds: form templates, spoken-number/letter normalisation, field validators
(Aadhaar/PAN/IFSC/mobile/PIN/date), a confirm-by-read-back slot-filling
session, and JSON/PDF export. The core (everything except api.py/standalone.py)
is pure standard library + reportlab, so it runs and tests without models.
"""
