# DwaniYojana: which schemes might I get?

A page for people who are not filling a form yet: about ten taps, then the schemes they may be able to get, each with its benefit,
how to apply and the date its rules were last checked. The DwaniForms app serves it at `/form/yojana`; it needs no sign-in.

It uses the scheme advisor that already exists for the spoken interview (`templates/scheme_advisor.json` and `schemes/*.json`), so
there is one set of rules, not two. `yojana.py` holds the only new logic: it validates answers, decides which questions to ask and
turns spoken words into an answer for one question.

## What is and is not stored

- No session, no cookie, no database row and no log line holds an answer. The endpoints are stateless: answers come in, schemes go out.
- Income is asked in bands (`INCOME_BANDS` in `yojana.py`) and the top of the band is used, so a borderline family sees "needs
  checking" instead of a promise.
- The share card is drawn in the browser from scheme names and a count. Nothing about the person is on it, and neither the
  WhatsApp text nor the copied list carries an answer.
- Where the browser can listen, "Answer by speaking" uses the browser's own speech recogniser, which may send the sound to its
  vendor (the page says so). Tapping always works, and the button is hidden where the browser cannot listen.

## Endpoints

All under `/form` (so `/form/yojana/questions`). None needs a key.

| Method and path | Body | Returns |
|---|---|---|
| `GET /yojana` | | the page (`Cache-Control: no-cache`) |
| `GET /yojana/questions?lang=hi\|en` | | the questions some scheme actually checks: `id`, `kind`, `prompt`, `required`, `when`, `options`, `min`, `max` (cached for five minutes) |
| `POST /yojana/understand` | `{question, text, lang}` | `{value}` for that one question; `null` when the words were not understood or were "don't know" |
| `POST /yojana/advise` | `{facts, lang}` | `{likely, maybe, not_matched, reviewed}` |

Anything the advisor would not accept (an age of -1, an unknown question, an option that does not exist) is refused with 422.
A language other than `hi` or `en` is a 404.

## How spoken answers are read

`POST /yojana/understand` reads the words for one question only, with the same readers the spoken forms use.

- Numbers: Hindi and English words and the common alternative spellings (अठाईस, पैसठ, पन्द्रह...), decimals ("ढाई एकड़").
- Income is turned into a year before it is put in a band: "दस हज़ार महीना" is 1,20,000 a year. Pay per day or per week is **not** guessed; the page then shows the bands to tap.
- "ज़मीन नहीं है" is 0 acres; "No" is not 0 for any other number question.
- "पता नहीं", "नहीं पता", "pata nahi", "idk" are never a No: they leave the question unanswered.
- A choice that fits two options (a bare "नौकरी": private or government?) is not guessed. Add the words people really use to the option's `synonyms` in `templates/scheme_advisor.json`.

## Keeping the rules current

Each scheme is one JSON file in `schemes/`. When you check a scheme against its official page, edit its `rules` or `benefit`
and set `last_reviewed` to today. The result shows the oldest `last_reviewed` as "Rules checked on ...", and a scheme older than
180 days is marked "needs re-checking" with a pointer to the official portal.

A question appears on the page only if some scheme has a rule on it, so a rule on a new fact also needs that fact in
`templates/scheme_advisor.json`, and a label for it in both languages in `HEAD` and `OPT` in `static/yojana.html`; a test fails
if one is missing.

## Tests

- `pytest tests/test_dwaniforms_yojana.py`: the rules, the refusals, spoken answers, the income bands matching the page, the
  preview picture, the endpoints and that they write nothing.
- With a server running (`python -m dwaniforms.standalone --text-only --demo-records --port 8130`):
  `DWANIFORMS_URL=http://127.0.0.1:8130 node tests/js/dwaniforms_yojana.test.js` walks the page by tapping, and
  `dwaniforms_yojana_voice.test.js` does the same by speaking, with a fake recogniser. CI runs both.
