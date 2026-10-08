# DwaniForms: speak, and the form fills itself

An offline voice kiosk for government forms and citizen services, built on DwaniLive's speech engine
(Whisper ASR, NLLB translation, glossary protection). It has its own UI (`static/app.html`) and its own website
(`../dwaniforms_site/`). DwaniLive's home page links to it.

| Service | What happens |
|---|---|
| **Fill a form** | PM-Kisan, e-Shram, bank KYC, hospital registration, income certificate. One question at a time; every answer is read back; Aadhaar (Verhoeff), mobile, PIN, PAN, IFSC are validated as spoken |
| **Complaint / RTI** | The citizen describes the problem aloud; department, place and date are extracted and confirmed with one yes/no each; the whole draft is read back; a letter / RTI application (Section 6(1), RTI Act 2005) is drafted in English and, for Hindi speakers, in their own words |
| **Land / ration record** | District → village (spelling-tolerant, Hindi or roman) → khasra ("एक सौ पैंतालीस बटा दो" = 145/2), or a ration-card number; looked up in a dataset cached on the kiosk, with the date of that copy |
| **Scheme advisor** | A short interview (questions only where they apply) → schemes likely / maybe, with reasons, what to verify, source and review date → "Fill this form" opens the form with answers carried over |

## Run

```bash
python -m dwaniforms.standalone --text-only --demo-records          # typed answers + fictional records: demos / UI work
python -m dwaniforms.standalone --whisper-model small --nllb-model-dir nllb-200-ct2   # full voice, offline
# open http://127.0.0.1:8100/  (kiosks reached over the network need https: --ssl-keyfile / --ssl-certfile)
```

## Data the operator manages

- **Records** (never shipped, never guessed): export from the state portal once, then
  `python -m dwaniforms.records import land land.csv --as-of 2026-09-01 --source "UP Bhulekh export"` (same for `ration`).
  Columns are listed in `records.py`. Answers older than 90 days carry a "check the official portal" warning.
  `--demo-records` uses clearly-marked fictional data and is never the default.
- **Scheme rules** live in `schemes/*.json`, one file per scheme, with `source` and `last_reviewed`.
  `python -m dwaniforms.eligibility` lists any not reviewed in 180 days (the kiosk also flags them).
  **The rules were written from knowledge up to mid-2026 (`last_reviewed: 2026-06-30`): verify each against its
  official site before a pilot.**
- **Department keywords** for complaints: `data/departments.json`. Add words freely; the test suite checks they
  match the forms' department options.
- **New forms**: add a JSON file to `templates/` (field kinds in `schema.py`; `when` asks a question only when it
  applies; `extract` mines a free-text answer; `final_readback` reads the draft back).

## Privacy

Nothing leaves the machine. Aadhaar / account / PAN are masked in responses and the default PDF; sessions are dropped
after 30 minutes idle (15 once complete); ration-card holder names are masked; advisor answers (income, category,
disability) stay in the session and only gender, category, income and land carry over to a form, each confirmed again.

## Tests

`pytest tests/test_dwaniforms.py tests/test_dwaniforms_features.py` (100+ tests: spoken numbers incl. ढाई/डेढ़/सवा and
"1,50,000", yes/no, extraction, drafts, look-ups, eligibility rules and data consistency, HTTP), and the kiosk UI against a
running server: `DWANIFORMS_URL=http://127.0.0.1:8100 node tests/js/dwaniforms_kiosk.test.js`. CI runs both.

## Known limits

Hand-written questions: Hindi and English (in the code) plus Bengali, Marathi, Gujarati, Punjabi, Tamil and Telugu
(language packs in `lang/`, written by an AI assistant, **marked "draft" until a native speaker reviews them**; see
`lang/README.md`). The online version offers exactly these 8. On a kiosk with NLLB, other languages are machine-translated
(flagged on screen). Spoken number words: English and Hindi 0-99; the six pack languages 0-10 plus hundred / thousand /
lakh / crore and their "two and a half" words (speech recognisers mostly return bigger numbers as numerals). Names are not transliterated (flagged for the operator). No Odia ASR.
PDFs print form-language text; Hindi letters are printed from the screen (the browser shapes Devanagari correctly).
Real Whisper/NLLB, the microphone path and text-to-speech must still be tried on the target hardware.
