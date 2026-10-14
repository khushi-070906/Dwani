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

## Reading the pilot numbers, and keeping every commit green

- **`/form/metrics/view`**: the anonymous counters as a page you can open on a phone -- forms started and finished, the
  completion rate, **where people stopped**, and **which questions had to be repeated**. Until now the numbers existed
  but needed `curl` with a header to read, which is no use at a centre. The token is typed into the page and sent as a
  header, so it never reaches a URL, a browser history or a server log; without `DWANIFORMS_METRICS_TOKEN` set, neither
  the page nor the JSON exists. The JSON now also carries a `labels` map, so a report reads "PM-Kisan · Father /
  husband name" instead of `pm_kisan:father_name`.
- **Fixed: "where people stopped" was only counting people who timed out.** Pressing "stop and go back", or closing the
  page, deletes the session, and that path counted nothing -- so the one number that tells you which question is too
  hard was missing most of its data. `service.delete()` now counts it, exactly like an expiry.
- **Fixed: tests could fail because of the tests before them.** The per-address cap on new conversations lives in a
  module-level counter, so the twenty-first session in a run was refused and an unrelated test went red. `tests/conftest.py`
  clears it around every test.
- **`check_commits.py`**: runs pytest and the language-pack check over *every* commit you are about to push, each in a
  throwaway worktree. Splitting work into many small commits is good, but when the code lands in one commit and its test
  in the next, GitHub marks the one in between with a red X and e-mails about it even though the branch tip is fine.
  `python check_commits.py` says which commits would be red, before they are pushed.
- Tests: `tests/test_dwaniforms_metrics_page.py` (6: the page and the numbers both need the token, the page carries no
  numbers of its own, the counters a pilot report needs, readable names for every key, and that nothing anyone said --
  no name, no Aadhaar, no long digit string -- can appear in the counters).

## Usable by the people it is for (WCAG AA)

The app was audited in a real browser at phone width, on the home screen and mid-form. What it found, and what changed:

- **17 pieces of small grey text failed contrast** (4.14:1 against 4.5:1 needed): the question counts, the step names
  "check" and "ready", the breadcrumb, the footer. `--ink-3` is now `#60687A`, which clears 4.5:1 on every background
  the page uses -- page, card, tinted card and the amber note.
- **Controls too small to hit.** "Home" in the breadcrumb -- the way back out of a form -- was **17x21px**; miss it and
  people press the browser's back button and lose the form. A+ was 39x38, the language chooser 102x40, the privacy
  links 53x21. Everything a finger has to find is now at least 44px tall.
- **Buttons spoke English in every language.** The logo, A+, the language chooser, the type box and the mic had
  hard-coded English `aria-label`s, so a blind Tamil speaker heard the questions in Tamil and the buttons in English.
  They are now translated through the packs (`data-ta`), with three new strings added to all eight languages.
- **The mic did not say whether it was listening.** It is now a proper toggle (`aria-pressed`) whose name changes
  between "tap and speak" and "listening" as it starts and stops.
- **Headings skipped a level** (h2 -> h4), which a screen reader reads as a missing section; the service groups are h3.
  The step you are on carries `aria-current`.
- Already right, and now held in place by tests: the question is a live region, a lost connection is an `alert`,
  and every animation stops for `prefers-reduced-motion`.
- Tests: `tests/test_dwaniforms_a11y.py` (40: contrast computed from the CSS for every text-on-background pair, the
  44px rules, the translated names in all eight packs, heading levels) and `tests/js/dwaniforms_a11y.test.js` (13: the
  real page in two languages -- names, mic toggle, live regions, aria-current, heading order). Both run in CI.
- Not done: nobody has listened to a whole form with TalkBack or VoiceOver on a real phone. The markup is right; the
  experience is unverified.

## Understand a paper (Samjhao) and the RTI first appeal

- **Samjhao** (`templates/samjhao.json`, `explain.py`, `papers/*.json`): nine kinds of paper -- electricity bill, water
  bill, property tax notice, bank loan / EMI notice, bank KYC message, court summons or legal notice, income tax notice,
  traffic challan, something else. For each: what it is, what to do, if something on it is wrong, the fraud to watch for,
  and free help. The date on the paper is counted (days left / today / passed) and read out, with the fraud warning and
  a one-line "general guidance, not legal advice". No model, no photo: the same paper always gets the same answer.
  Each rule was checked against a public source before it was written (RBI directive of 12 Aug 2022 on recovery agents
  calling before 8 am / after 7 pm; the RBI Integrated Ombudsman Scheme, cms.rbi.org.in, 14448, free; NALSA 15100;
  I4C on "digital arrest" and 1930 / cybercrime.gov.in; the DIN on every income tax notice; PIB on fake e-challans;
  consumer grievance forums of electricity companies under s.42(5) of the Electricity Act). Sources and a
  `last_reviewed` date sit in each file.
- **RTI first appeal** (`templates/rti_first_appeal.json`, `outcomes.rti_appeal_draft`): the Section 19(1) appeal, with
  the 30-day window worked out from the dates given -- too early (the PIO still has time: says until when), on time, or
  late (asks for condonation, blank for the reason). Department choices are the RTI application's own.
- **Dates ahead** (`validators.date(future=True)`): a bill's last date or a hearing date may be in the future and said
  without a year. Only fields that ask for it accept this.
- Spoken result: outcomes can add lines (`say_text`, already in the citizen's language) and messages with a date read
  out in words (`say_more`).
- Home page: "Understand a paper" joins the five most-used tasks and gets its own group; the first appeal sits with
  complaints and RTI.
- Eight language packs: 21 strings for the appeal and 79 for Samjhao (messages, questions, every paper sentence, the
  nine paper names, screen text), all AI-drafted, packs still `draft`. A check confirmed every number and web address
  (1930, 15100, 14448, cms.rbi.org.in, incometax.gov.in, echallan.parivahan.gov.in) survives in every translation.
- Tests: `test_dwaniforms_samjhao.py` (36: every paper file complete, the questions follow the paper, **saying each
  paper's name picks that paper in all ten languages**, days counted, the warning spoken, explanations in the pack
  language), `test_dwaniforms_rti_appeal.py` (19: the window in every case, English and Hindi drafts, condonation,
  the reply words in all eight packs), `test_dwaniforms_dates_ahead.py` (12), `tests/js/dwaniforms_samjhao.test.js`
  (16: the real page, in CI).
- Not done: a lawyer or consumer-rights worker reading the nine explanations; reading the paper from a photo; any
  state-specific rule (fees, penalties, objection periods differ by state and city, so the text says "usually" and
  "check your state's rules" rather than a number).

## Samjhao: read the paper from a photo

- **"Read from a photo"** in Samjhao (`static/app.html`, `static/ocr/`): the picture is read on the phone by tesseract.js
  (English, then Hindi + English when needed) in a web worker served from this server; it is never uploaded. The
  engine is fetched on first use only (about 7 MB) and kept by the service worker (`CACHE` is now `dwaniforms-v2`).
- **`POST /sessions/{id}/suggest`** + `Session.suggest()`: up to three values read off the photo are checked like typed
  answers and read back with "is this correct?"; a "no" asks normally; answers already given are never replaced.
  `operator_set` and `suggest` share one `_clean()`.
- `/assets/ocr/...` is served with the right types (`.wasm` as `application/wasm`; `.gz` as raw bytes, not "gzip");
  the CSP adds `'wasm-unsafe-eval'` and still blocks `eval`.
- Privacy page (English and Hindi) says plainly that the photo stays on the phone and no camera permission is asked.
- **Fixed: "days left" was one too many after midnight in India.** The server runs on UTC, which is still yesterday
  until 5:30 am IST. `clock.today()` (fixed +5:30; India has no daylight saving) is now what every "today" means.
- Eight packs gain the eight photo strings (drafts, as the rest).

## Samjhao: ask one question (optional AI, off by default)

- `ai.py`, `POST /sessions/{id}/ask`, an "Ask the AI" box under the explanation. Needs `DWANIFORMS_AI_KEY`; without it the
  box, the endpoint and every outbound call are absent (`capabilities.ai` is false).
- Consent every time (server refuses without `consent: true`; the page asks again for each question and says what leaves).
- Only the question (identifying numbers, PAN codes and e-mails blanked) and the kind of paper / days left are sent; the
  prompt is built from our own written explanation and rules the question cannot override; nothing stored or logged;
  `hardening.LIMITS["ai"]` = 10 per hour per address. Uses only the standard library (`urllib`), so no new dependency.
- Privacy page (English and Hindi) has a section for it; `render.yaml` explains how to switch it on; nine strings added to
  all eight packs.

## Company API: services sold one by one, 30-day trial keys

- `apikeys.py`: signed keys (`dfk_...`), scoped to a group (`rti`, `rti_appeal`, `grievance`, `samjhao`, `advisor`, `forms`), one
  template or `*`; end date, finished-draft quota and requests a minute inside the key; `issue` / `inspect` / `services` on the
  command line; revoke by id with `DWANIFORMS_REVOKED_KEYS`. No secret, no API.
- `b2b.py`, mounted at `/api/v1` by `standalone.py` when `DWANIFORMS_KEY_SECRET` is set: `GET /services`, `GET /usage`,
  `POST /{service}/draft` (fields in, outcome out, validated by the same code as the voice app). Stateless: nothing stored,
  bodies never logged, key never echoed, no CORS, one error shape, guessing keys is slowed down. Optional
  `DWANIFORMS_USAGE_LOG` for invoicing (key id, service, status only).
- ISO dates (`2026-10-20`) are now accepted by `Session._clean`, so the operator box and photo suggestions take them too.
- `API.md` for customers, with an RTI / appeal walk-through; `render.yaml` generates `DWANIFORMS_KEY_SECRET`.

## Scheme forms: pension, scholarship, Ayushman card

- Three new forms: `pension_application` (old age, widow or disability; the disability percentage is asked only for a
  disability pension), `scholarship_application` (level of study, school or college, last marks, parent's name) and
  `ayushman_card` (family size, "is the family on the list"). Hindi and English written by hand; all eight packs gained the
  21 questions and 8 options (drafts, as the rest).
- The schemes link to them: old age, widow and disability pensions -> `pension_application` (they pointed at the income
  certificate before), Ayushman Bharat -> `ayushman_card`. Scholarship rules are NOT in the scheme advisor yet (they differ
  by state and year); the form is there for a centre to use.
- The forms are on sale through the company API under the `forms` group.

## Spoken numbers and dates, names, addresses

- **Dates**: "pandrah march do hazaar do" is 15/03/2002 (a Romanised "do" inside a date or after a thousand is the number 2, not
  the word "do"); Hindi months written in Latin letters (janvari, agast, sitambar ...), day ordinals (pehli, doosri, teesri,
  chauthi, paanchvi), and a day that is not 1-31 is refused. "do hazaar do" is 2002 as a quantity too.
- **Names** (`names.py`): "Mohd Aslam", "Mohammad Aslam" and "मोहम्मद अस्लम" are one person. Words are turned into sound keys (all
  Indian scripts by sound, schwa dropped the way Hindi is spoken), well-known variants are folded, word order and titles
  do not matter, an initial matches a full word. Answers `same` / `probably` / `different` with the reasons; a close
  but different name (Rahul / Rahil, Sunil / Sushil) is `probably`, never `same`.
- **Addresses** (`address.py`): house, floor, block, street, landmark (with near / opposite / behind), locality, village,
  post office, tehsil, district, city, state, pincode, care-of; Hindi and English, commas or none; what cannot be placed goes
  to `unparsed` rather than being guessed.
- All three are on the company API (`/names/match`, `/address/parse`, `/speech/parse`) with their own key scopes
  (`names`, `address`, `speech`).

## Document reader: photo of an Aadhaar card, ration card or khasra copy fills the form

- `static/ocr/dwani-docs.js` (parsing only, no look-behind so older iPhones run it): from the text of a photo it finds the
  Aadhaar number (accepted only if its Verhoeff check digit is right; a 16-digit virtual ID is ignored), name (the line above
  the date of birth), date of birth, gender, mobile and PIN code (by their labels), father's name (from S/O), the ration
  card number and the khasra number. Nothing is guessed: a value with no clear label is not suggested.
- The page shows "Read from a photo" on every form and on the ration and land look-ups (not only Samjhao), reads with the same
  on-device engine (English pass first, Hindi + English only when the card was not read fully) and sends only the values
  found to `/suggest` -- the same endpoint, same checks, same "is this correct?" for each; a "no" asks the question normally.
- An Aadhaar number is spoken in full for the yes but listed on the screen with its last four digits only.
- `doc_note` string in all eight packs (draft); privacy page (English and Hindi) says what is read and what is sent.
- Tests: `tests/js/dwaniforms_docs.test.js` (parsing), `tests/js/dwaniforms_docs_page.test.js` (real page, real server),
  `tests/test_dwaniforms_docs.py` (server). Checked once in a real Chromium with the real engine on a generated card.

## My applications (what happened to my RTI?)

A list kept **on the phone only** (`localStorage`, key `dwaniforms:apps`), reached from the home page. After an RTI, appeal or any
form, "Save to my applications" stores the office, the title and the dates the person types (sent, reply received, first appeal
sent, decision received) and a reference number -- never a name, address, phone number or what was asked. From those dates it
works out what the RTI Act allows next: the office's 30 days (s.7(1)), the first appeal window, 30 days from the end of that or
from the reply (s.19(1)), the decision date and the 90-day second-appeal window (s.19(3)). It warns when a limit has passed (an
appeal is still possible, with a request to excuse the delay), downloads a calendar reminder (`.ics`, two days before), and "Write
the first appeal" opens that form with the office and the RTI date offered as suggestions (each read back for a yes, like a photo's).

There is **no connection to any government system**: the page cannot know whether an office replied, so the person enters the
dates. The date rules live in `static/js/tracker.js` and a test runs them against the server's own `outcomes.appeal_timing` on
the same dates. Tests: `tests/js/dwaniforms_tracker.test.js`, `tests/js/dwaniforms_tracker_page.test.js`, `tests/test_dwaniforms_tracker.py`.

## Company API: any office name

`department` on `rti_application`, `rti_first_appeal` and `grievance` took only the voice app's twelve offices, so a platform
could only send "Other" and the letter read "PIO, Other". The API now accepts the office's own name as free text (3-100 printable
characters) and prints it as sent; `/services` marks the field `accepts_free_text`. The voice app and every other choice are
unchanged. Tests: `tests/test_dwaniforms_b2b.py`.

## One logo and one set of fonts on every page
The logo and the fonts differed between the app, its numbers page and the website (privacy and terms had their own colours, body font and heading font; the favicon was drawn with whatever serif the device had; the logo font was fetched only from Google).
- One logo (seal with ध + ध्वनिForms) with the same size, colours and fonts on the app, the numbers page, the website home, privacy and terms. The website keeps its copy in `dwaniforms_site/brand.css`; the app and the numbers page keep theirs inline. The app footer now shows the logo too.
- One font set: Rozha One (logo), Source Serif 4 (headings), Lato then Mukta (text), Noto Serif Devanagari (Hindi headings). Rozha One, Source Serif 4 and Mukta now ship with the website and Rozha One and Source Serif 4 ship with the app, so the logo and the headings look the same when Google Fonts is slow or blocked (all SIL OFL; licence texts sit next to the files). Lato is not shipped: its licence reserves the name, so only the original files may be copied.
- Fixed: the Google Fonts address asked for Lato weight 800, which Lato does not have; Google refuses the whole request in that case, so no font loaded. Every page now asks for the same list, with valid weights.
- One favicon on all five pages, drawn from the logo glyph itself (no system font involved). Privacy and terms had none or an older one.
- Privacy and terms use the same colours, header, heading font and footer as the rest; the top bar is 64 px high and starts at the same left edge on every page; old unused logo rules removed.
- Numbers page (`/form/metrics/view`) gets the logo and the same fonts (its font files are one level up from its address).
- The service worker keeps the new font file; `CACHE` is now `dwaniforms-v4`.
- `tests/test_dwaniforms_brand.py` fails when the logo rules, the font stacks, the Google Fonts list, the favicon or the bundled font files drift apart between pages.

## Website: a calmer home page
The home page looked childish: chunky buttons with a thick offset shadow, three different button shapes in a row, an orange "NEW" chip, a hand-drawn orange underline, emoji faces and icons, rainbow cards (dark headings on green, orange and blue), a tilted phone, tilted numbered tiles, and big orange boxes.
- Buttons: one flat style (8 px corners, 48 px high): a white primary, an outlined secondary and a plain link; on a phone they stack at full width.
- Heading: smaller, one colour with a soft green second line; the eyebrow is a small ruled label instead of a chip.
- Phone picture straight, not tilted, with calm bubbles; the microphone is a drawn icon, not an emoji; the speakers are initials, not faces.
- The four service cards are one white style with a green top edge; the numbered steps are green circles; the ready-today list is plain tags; the pilot box is brand green instead of orange; the footer links sit together instead of being spread across the page.
- Fixed: a leftover half-rule in the home page's style sheet made the phone picture's header dark with dark letters; the test now catches a stray rule like that.

## Website: colour back in, kept calm
The first calm pass was nearly all one green. Colour is back, in a small muted set so it reads as professional: forest green (main), navy, terracotta, slate blue and ochre, plus a soft gold as the one accent.
- Hero: forest green sliding into a deep navy-teal; the label, the second heading line and the three numbers in soft gold.
- The four service cards each get a soft tint, a coloured top edge and a drawn line icon (forms: green, complaints: terracotta, records: slate, schemes: ochre); the three story speakers and the tags carry the same colours as small accents.
- Numbered steps in green, slate and ochre; the "use it from home" band has a gold hairline; the offline/private/honest band has gold headings; the pilot band runs green to navy.
- Section labels are dark gold, which stays readable (about 5:1) on the sage page.
- Still no emoji, no thick shadows and no tilted shapes.

## Home page and phone: fewer repeats, nothing hidden behind the assistant
- The five most-used services are listed once. "Everything else" no longer repeats them (PM-Kisan, Samjhao, complaint, scheme advisor and land record used to appear twice).
- "My applications" sits above the lists, where a person coming back for their RTI will see it, instead of in the side column that falls to the bottom on a phone. Its heading is an h2 so headings still step down one level at a time.
- On a phone the assistant panel no longer covers most of the screen: the two notes about the photo (read on this phone only; what the photo is used for) sit behind one line, "How the photo is used", so the question, the form rows, the mic and the typing box fit together.
- The offline cache is `dwaniforms-v5`, so phones that already installed the app pick the new page up.

## The app uses drawn icons, like the website
- The service list, the three promises on the welcome card, the microphone, the photo button and the home-page chips use line icons in the brand green instead of emoji. Emoji look different on every phone (and some old phones show empty boxes); the icons look the same everywhere and follow the text colour.
- What the person said is shown in quotation marks instead of after a speech-bubble emoji.
- The offline cache is `dwaniforms-v6`.

## Ticks, crosses, the pencil and the warning are drawn too
- The ticks (answer checked, step done, confirmed note, the options you chose), the red cross on "No", the pencil on a not-yet-confirmed answer, the warning on a scheme whose rules need re-checking, and the download arrow on "Add to phone" are line icons, not text symbols. Text symbols come from whatever font a phone has and are sometimes coloured emoji.
- "Yes" and "No" get their tick and cross in every language, including English. A tick left at the front of a language table's words is not drawn twice.
- The offline cache is `dwaniforms-v7`.
