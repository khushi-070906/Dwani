# Review fixes

**Correctness**
- Choice answers (gender, category, ...) now match when Whisper adds punctuation ("Female.", "महिला।").
- "no", "for", "to", "sat" ... are no longer read as digits in quantity fields ("no income" -> 9, "2 lakh for the year" -> 200004 are gone); `no` is no longer a digit anywhere ("Aadhaar no. ..." gave a 13th digit). Dictation of digit strings still accepts common mishearings.
- Skip words only skip when they are the whole answer ("next to the temple" no longer skips an optional field).
- After redo or an operator edit the flow continues at the first unanswered field instead of re-asking answered ones.
- Translated welcome message can no longer crash session start (placeholders are filled before translating).
- Two-digit years are expanded relative to today; all-zero account numbers rejected; superscript digits no longer crash `spoken_to_int`.
- Romanised Hindi number words 0-99; date read-back is spoken ("15 August 1990") and the PDF prints DD/MM/YYYY.
- `operator_set` validates choice/yesno/text fields; unknown field ids raise KeyError (HTTP 422) instead of a 500.

**Privacy / robustness**
- Sessions expire on inactivity (not age) and are purged by a background sweeper; completed forms expire sooner; UI deletes the session when done.
- Sensitive values masked in API responses by default; masked PDF by default; `?mask=` on JSON export.
- Pydantic request models, 422/413/400 instead of 500s, audio size limit, JSON-only bodies and required `X-Sample-Rate` header.
- Blocking ASR is run off the event loop; `numpy` and DwaniLive's `AudioSegment` are imported lazily (tests run standalone).
- Removed the stray `../careers/fonts` path (`$DWANIFORMS_FONT_DIR` / `dwaniforms/fonts`), HTML-escaped labels in the PDF.
- `--ssl-keyfile/--ssl-certfile` for https kiosks; `/capabilities` endpoint.

**UI**
- Per-field Redo buttons; New form button; language/form locked during a session; voice availability from the server per language.
- Box-filter downsampling to 16 kHz (no aliasing), AudioWorklet (ScriptProcessor fallback), TTS cancelled while recording, press-and-hold race fixed, keyboard hold-to-speak, touch long-press menu suppressed.
- "Show numbers" toggle, aria-live regions, labels, secure-context and English-fallback notices, escaped output.

**Tests**: 20 -> 42 (regressions for every item above, incl. HTTP layer).

# Round 2 (DwaniForms)

**Fixes** (each has a regression test)
- Quantities: "2.5 acre" was 7, "1,50,000" was 51, ढाई/डेढ़/सवा/पौने/साढ़े and "one and a half" were wrong. New
  `spoken_to_number` (Decimal); `number` fields take `decimals` and `unit: "acre"` (hectares converted; bigha / kanal /
  guntha refused rather than guessed, since they differ by state); fractional answers to whole-number fields are rejected.
- "हाँ, कोई दिक्कत नहीं" / "yes no problem" were read as **no**.
- Optional yes/no or choice questions answered "no" / "नहीं" were **skipped** instead of recorded.
- Choice matching prefers the longest synonym ("farm labour" no longer collides with "labour").
- Read-backs speak the citizen's own words (not the machine translation), option names in their language, and the
  form title in their language.

**New**: complaint / RTI drafting with extraction (`extract.py`, `outcomes.py`, `data/departments.json`), land & ration
look-ups (`records.py`, kinds `khasra` / `ration_card`), the scheme advisor (`eligibility.py`, `schemes/`,
`templates/scheme_advisor.json`), suggestions / `when` / final read-back in `session.py`, prefill between flows, the
kiosk UI (`static/app.html`, bundled Rozha One + Mukta fonts) and the website (`../dwaniforms_site`).

# Round 3 (DwaniForms): more languages online, no translation model needed

- **Language packs** (`lang/*.json`, loaded by `langpacks.py`): Bengali, Marathi, Gujarati, Punjabi, Tamil, Telugu. Each has
  every question, label, form title, system message, screen text, scheme name / benefit / rule, next step, option name
  (with the words people actually say), yes / no / skip words, digit words 0-10, hundred / thousand / lakh / crore,
  "two and a half" words, month names, the khasra "by" word, land units to refuse (bigha, guntha, kanal, cent...),
  ration-card category names and the area format. Keyed by the English sentence, so a changed English line shows up as
  missing. `python -m dwaniforms.langpacks check` (run in CI) lists anything missing or any lost `{placeholder}`.
  **Draft quality**: written by an AI assistant; each pack is `"status": "draft"` until a native speaker reviews it.
- The online app now offers the 8 hand-written languages (was Hindi / English only); the language list comes from
  `/form/capabilities` (`langs`, `lang_packs`), screen text from `/form/ui/<lang>`. On a kiosk, packs are used first and
  NLLB only for the rest.
- Speech: recogniser tag per pack (Punjabi `pa-Guru-IN`); if a browser refuses a language, the mic is turned off with a
  "please type" note instead of a raw error. Khasra "145/2" is spoken with the language's "by" word.
- Fixes found on the way: the "record not found" sentence put English ("khasra 145/2", "this ration card") inside the
  Hindi message; land / ration result labels were always English (now translated, Hindi too); advisor "Fill this form"
  titles were English; read-back ended with "." in Hindi / Bengali / Punjabi (now "।"); Bengali য়/ড় spelled two ways
  didn't match; day numbers with an ordinal ending ("15th", "১৫ই", "15ஆம்", "15వ") weren't understood; letter-spaced
  kickers broke Indic conjuncts; complaint text in a pack language now also suggests the department.
- Tests: `tests/test_dwaniforms_langpacks.py` (81: completeness, placeholders, yes/no and option collisions, digits,
  dates, fractions, khasra, full flows in each language with no translator, endpoints) + a Bengali run of the real
  app in `tests/js/dwaniforms_kiosk.test.js`.
