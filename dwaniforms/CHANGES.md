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

## DwaniForms on the phone (installable, survives a reload)

- **Add to phone**: `static/manifest.webmanifest` + icons drawn from the header seal (192, 512, maskable 512, Apple 180,
  `static/img/`). Chrome and Edge offer the install; the home screen shows a small "फ़ोन में लगाएँ" chip only when the
  browser actually offers it. Once installed it opens full screen, with no address bar.
- **Service worker** `static/sw.js`, served from `/form/sw.js` so its scope cannot reach the rest of the site. It keeps
  the page, icons, bundled fonts and the three public lists (`/forms`, `/capabilities`, `/ui/{lang}`): the app opens at
  once and a weak signal no longer gives a blank screen. It never touches `/sessions` -- a citizen's answers are not
  stored in the browser, and the server still marks them `no-store`.
- **A reload no longer loses the form.** `POST /sessions/{id}/resume` re-asks the current question; the browser keeps
  only the session id, for that one tab. An answer read back but not yet confirmed is asked again instead of being kept.
  Leaving the page still ends the session, except while a form is half filled -- a phone that sleeps or switches app
  fires the same event, and the server drops the session after 30 idle minutes anyway.
- Fixed on the way: the website's favicon was a different mark (orange ध in a rounded square) from the app's seal.
- Tests: `tests/test_dwaniforms_pwa.py` (13: manifest, icon sizes, worker scope and what it may cache, what the page
  stores, resume incl. an unconfirmed answer and an expired session) and `tests/js/dwaniforms_pwa.test.js` (10: the real
  app in jsdom, filled then reloaded). CI runs the browser one against a live server.

## Terms of use, and a named grievance officer

- **New page `dwaniforms_site/terms.html`** (English + Hindi, same shell as the privacy page): who runs this and that it
  is not a government service; that DwaniForms prepares a form but **submits nothing anywhere**; that scheme advice is
  guidance and the department decides; that look-ups read a saved copy (demo records online); that speech misheard is
  why every answer is read back, so check before signing; fair use; that Aadhaar / account / PAN are checked only for
  shape, with **no UIDAI verification or e-KYC**; availability; limits of responsibility, without claiming to remove
  rights Indian law does not allow to be given up; Indian law, Delhi courts. Linked from the site and privacy footers.
- **Privacy policy**: names a grievance officer (DPDP Act 2023 expects a person, not just an address) with a 30-day
  reply, and gains a "What is kept on your phone" section now that the app can be installed -- the page, icons and
  fonts are kept, the answers are not, and the session id lives in one tab only.
- Tests: `tests/js/dwaniforms_legal.test.js` (31: both pages bilingual, the named officer, what the browser keeps, the
  promises the terms must make, and every link between the three pages resolving to a file that exists). In `npm test`.

## Kannada and Malayalam (10 languages online)

- **Two more hand-written language packs**, `lang/kn.json` and `lang/ml.json`, built the same way as the first six: all
  226 questions, labels and scheme texts, the 46 system messages, the 116 screen strings, every answer option with the
  words people actually say, and the spoken words for yes / no / skip, 0-10, hundred-thousand-lakh-crore, "two and a
  half" (ಎರಡೂವರೆ, രണ്ടര), the months, and the local land units a speaker is likely to use (ಗುಂಟೆ, സെന്റ്) -- which the
  app answers by asking for acres instead of guessing. Online that makes **ten** languages with no translator at all.
- Both are `"status": "draft"`: written by an AI assistant, **not yet checked by a native speaker**, and the app says so
  under every question in that language.
- **The kiosk test no longer hard-codes the language list**; it asks `/form/capabilities` and checks the page offers
  exactly what the server can do. That was the real reason the `dwaniforms-ui` CI job went red on the commits that added
  the first packs. A stray `console.log("DEBUG", ...)` left in that test is gone too.
- The machine-translation tests moved from Kannada to Odia, which still has no pack.
- Still not offered: Odia, Urdu, Assamese. Odia and Assamese have no browser speech recognition to fall back on, so a
  pack there would be typing-only; worth doing, but it buys less.
