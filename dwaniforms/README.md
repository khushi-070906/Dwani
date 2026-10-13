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

## On a phone

Online, DwaniForms is installable: the browser offers "Add to phone" (`static/manifest.webmanifest`) and after that it
opens from the home screen like an app, full screen, with no address bar. A small service worker (`static/sw.js`, scope
`/form/`) keeps the page, the icons, the bundled fonts and the three public lists, so it opens at once and a weak signal
no longer leaves a blank screen. It never touches `/sessions`: answers are not stored in the browser.

If the page reloads -- the phone slept, the signal dropped, the app was reopened -- the half-filled form comes back
(`POST /form/sessions/{id}/resume`). The browser keeps only the session id, for that one tab (`sessionStorage`); every
answer stays on the server and is still dropped there after 30 idle minutes. An answer that had been read back but not
yet confirmed is asked once more rather than quietly kept.

## Reachable by people who can't see it well

The app is checked against WCAG AA, because the people it is for are often old, often cannot read much, and often
cannot see small grey text:

- every text colour clears 4.5:1 on every background the page uses (`tests/test_dwaniforms_a11y.py` computes the
  ratios from the CSS, so one careless tweak fails the build);
- every control is at least 44px, the size a fingertip lands on without aiming;
- the questions sit in a live region, so a screen reader reads each new one; a dropped connection is an `alert`;
- the mic is a toggle that says which way it is turned, and every control with no visible words of its own is named
  **in the citizen's language** (`data-ta`, translated in all eight packs) -- a blind Tamil speaker should not hear
  the questions in Tamil and the buttons in English;
- headings step down one level at a time, the current step carries `aria-current`, and the mic's pulse stops for
  anyone who asked their phone for less motion.

Not yet checked: a real screen reader (TalkBack, VoiceOver) on a real phone.

## Understand a paper (Samjhao)

Someone got an electricity bill, a property tax notice, a bank loan letter, a "your KYC will be blocked" message, a
court summons, an income tax notice or a traffic challan. They say which, and (if it has them) the date and the amount
printed on it. The app says what the paper is, what to do, what to do if something on it is wrong, how to spot the fraud
that is commonly dressed up as that paper, and where free help is (1930 for cyber fraud, 15100 for free legal aid, the
RBI Ombudsman at cms.rbi.org.in / 14448). The date is counted: "12 days left", "today", or "already passed -- go today".

Nothing reads the paper and nothing guesses: each kind of paper is a plain file in `papers/*.json`, English and Hindi
written by hand and every other language in its pack, with the public source of each rule and a `last_reviewed` date. A
reviewer can fix a sentence without touching code; `python -m dwaniforms.langpacks check` then lists what each language
still needs. The fraud warning is read aloud, not only shown, because it is the one thing worth hearing.

Dates on a paper may lie ahead and are often said without a year ("15 November"): fields with `"future": true` accept
both (the year is this year, or next if that day is more than six months behind us). Birth dates still refuse the future.

## RTI first appeal

No reply to an RTI in 30 days, or an incomplete one: the first appeal under Section 19(1) is drafted (English, and
Hindi alongside for Hindi speakers), addressed to the officer senior to the PIO. The dates given decide what it says:
if the office still has time, it says until when; if 30 days have run out since the reply or since the PIO's 30 days,
the draft asks for the delay to be condoned and leaves a blank for the reason; with no reply it cites deemed refusal
(s.7(2)) and asks for the information free of charge (s.7(6)).

## Pilot numbers

`/form/metrics/view` shows, on any phone, how many forms were started and finished, where people stopped, and which
questions had to be repeated -- the one list that tells you which question to rewrite first. It is behind
`DWANIFORMS_METRICS_TOKEN`: without that variable set, neither the page nor the JSON exists. The token is typed into the
page and sent as a header, so it never reaches a URL, a browser history or a server log. The counters hold form ids,
question ids and numbers; never anything anyone said.

## Privacy

Nothing leaves the machine. Aadhaar / account / PAN are masked in responses and the default PDF; sessions are dropped
after 30 minutes idle (15 once complete); ration-card holder names are masked; advisor answers (income, category,
disability) stay in the session and only gender, category, income and land carry over to a form, each confirmed again.

## Tests

`pytest tests/test_dwaniforms.py tests/test_dwaniforms_features.py` (100+ tests: spoken numbers incl. ढाई/डेढ़/सवा and
"1,50,000", yes/no, extraction, drafts, look-ups, eligibility rules and data consistency, HTTP), and the kiosk UI against a
running server: `DWANIFORMS_URL=http://127.0.0.1:8100 node tests/js/dwaniforms_kiosk.test.js`. CI runs both.

## Known limits

Hand-written questions: Hindi and English (in the code) plus Bengali, Marathi, Gujarati, Punjabi, Tamil, Telugu, Kannada and Malayalam
(language packs in `lang/`, written by an AI assistant, **marked "draft" until a native speaker reviews them**; see
`lang/README.md`). The online version offers exactly these 10. On a kiosk with NLLB, other languages are machine-translated
(flagged on screen). Spoken number words: English and Hindi 0-99; the eight pack languages 0-10 plus hundred / thousand /
lakh / crore and their "two and a half" words (speech recognisers mostly return bigger numbers as numerals). Names are not transliterated (flagged for the operator). No Odia ASR.
PDFs print form-language text; Hindi letters are printed from the screen (the browser shapes Devanagari correctly).
Real Whisper/NLLB, the microphone path and text-to-speech must still be tried on the target hardware.
