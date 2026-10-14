# DwaniForms company API

Government-form services as plain HTTP calls, for a platform that already has its own screens. You collect the fields
in your own app; one call returns the finished draft, the next steps and the deadlines, in English and Hindi (and the
explanation text in ten languages).

- **Server to server.** There is no CORS, so a key placed in a web page will not work, and must not be put there.
- **Nothing is kept.** One call is one request and one response: no session is opened, nothing is written to disk,
  and the body is never logged.
- **One key per customer per service.** A key opens only the services it was issued for, ends on a date, and has a
  size and a speed limit. A 30-day trial is just a key with `--days 30`.

Base address: `https://<your-deployment>/api/v1` · machine-readable description: `GET /api/v1/openapi.json`

## The services

| Service (group) | What you send | What comes back |
|---|---|---|
| `rti` · `rti_application` | applicant, address, the office, what information they want, BPL yes/no, how to receive it | the RTI application letter (Section 6(1)), fee note, what to do next |
| `rti_appeal` · `rti_first_appeal` | the same applicant, the date the RTI was filed, whether a reply came (and when, and why it is unsatisfactory) | the first-appeal letter (Section 19(1)), the **last date to appeal** and whether it is already late (a late appeal gets a condonation paragraph), next steps |
| `grievance` · `grievance` | the complaint, the department, place and date | a complaint letter and where to send it |
| `samjhao` · `samjhao` | the kind of paper (bill, notice, summons...), its last date, its amount | what it is, what to do, **days left** counted, how to spot the fraud dressed up as it, where to get free help |
| `advisor` · `scheme_advisor` | a few facts about the person | which schemes they are likely / may be eligible for, with sources and review dates |
| `forms` · `pm_kisan`, `e_shram`, `income_certificate`, `health_registration`, `bank_kyc`, `pension_application` (old age / widow / disability), `scholarship_application`, `ayushman_card` | the form's fields | the checked values, field by field (Aadhaar checksum, PIN, dates...) |
| `names` | two names, in any script | whether they are one person ("Mohd Aslam" = "Mohammad Aslam" = "मोहम्मद अस्लम"): score, `same` / `probably` / `different`, and why |
| `address` | a free-text Indian address, typed or spoken | house, street, landmark, locality, village, post office, tehsil, district, city, state, pincode; what is still missing |
| `speech` | what a person said (or an ASR wrote) | the date ("pandrah march do hazaar do" → 15/03/2002), the number ("do hazaar teen sau paanch" → 2305) or the digit string (Aadhaar, phone, pincode) |

A key can name a group (`rti`), one service (`pm_kisan`) or `*`. `names`, `address` and `speech` are small stand-alone calls (below); they use the same keys, speed limit and error shape, and do not use up the finished-draft count. Land and ration look-ups read a centre's own records and
are not sold through the API.

## Quickstart

```bash
curl https://<host>/api/v1/services -H "Authorization: Bearer dfk_..."

curl https://<host>/api/v1/rti_application/draft \
  -H "Authorization: Bearer dfk_..." -H "Content-Type: application/json" \
  -d '{"lang": "en", "fields": {
        "full_name": "Asha Verma", "address": "12 Gandhi Road, Lucknow", "pincode": "226001",
        "department": "Roads (PWD)", "information": "Status of my road repair request of June 2026",
        "bpl": false, "delivery": "By post"}}'
```

```python
import requests
r = requests.post("https://<host>/api/v1/rti_first_appeal/draft",
                  headers={"Authorization": "Bearer dfk_..."},
                  json={"lang": "hi", "fields": {...}}, timeout=20)
j = r.json()
if j["complete"]:
    letter = j["outcome"]["drafts"]["en"]; last_day = j["outcome"]["timing"]["last"]
else:
    show(j["missing"], j["errors"])        # ask your user for these, then call again
```

```js
const r = await fetch(`${BASE}/api/v1/samjhao/draft`, {
  method: "POST", headers: { "X-API-Key": KEY, "Content-Type": "application/json" },
  body: JSON.stringify({ lang: "hi", fields: { paper: "Electricity bill", due_date: "2026-10-25", amount: 1850 } }) });
```

## Calls

### `GET /services`
The services this key may use, each with every field: `id`, `label`, `kind`, `required`, `sensitive`, `options` (the
exact values to send for a choice), `when` (the field only applies if another field has one of these values) and
`params` (limits such as `min_age`). Also `languages`.

### `POST /{service_id}/draft`
Body: `{"lang": "en", "fields": {"<field id>": "<value>"}}`. Values may be strings, numbers or true/false (true/false for a
yes/no field). Dates are `YYYY-MM-DD` (`DD/MM/YYYY` also works). Choices must be one of the listed `options`.

Response (always HTTP 200 when the request itself was well formed, so you can call it again as your user fills in more):

| Key | Meaning |
|---|---|
| `complete` | true when nothing is missing and nothing is wrong |
| `values` | every accepted field as it will appear on the form (names and numbers cleaned up) |
| `missing` | required fields (that apply) not yet sent |
| `errors` | `{field: {code, message}}` for values that failed (`aadhaar_invalid`, `date_invalid`, `choice_invalid`, `unknown_field`...) |
| `ignored` | fields sent that do not apply given the other answers (a `when` rule) |
| `outcome` | only when complete: the draft / explanation / advice (shape depends on the service, below) |
| `usage` | `{remaining, expires}` |

`outcome` by service:

- **rti, grievance, rti_appeal:** `{type: "draft", title, drafts: {en, hi?}, next_steps: [...], next_steps_hi: [...]}`; with `lang: "hi"` the draft also comes in Hindi. The appeal adds `timing: {last: "2026-11-03", late?: true}`.
- **samjhao:** `{type: "explain", option, what, do, if_wrong, beware, help, date?, days?, amount?, last_reviewed}`, in the requested language.
- **advisor:** `{type: "advisor", likely, maybe, forms, ...}`.
- **forms:** no `outcome`; use `values`.

Only a **complete** call is counted against the key's quota.

**Any office.** The voice app lists twelve offices; a platform's users file with any public authority. For the `department`
field of `rti_application`, `rti_first_appeal` and `grievance` the API therefore also accepts the office's own name as plain
text (3 to 100 printable characters, with at least one letter). It is printed in the letter exactly as sent, in the English
and the Hindi version alike. `/services` marks these fields with `"accepts_free_text": true`; every other choice must still
be one of its `options`.

### `POST /names/match`, `POST /address/parse`, `POST /speech/parse`

Plain helpers, no form behind them. Nothing is kept or logged.

```bash
curl .../api/v1/names/match   -H "Authorization: Bearer dfk_..." -d '{"a": "Mohd Aslam", "b": "मोहम्मद अस्लम"}'
# {"a":"Muhammad Aslam","b":"Muhammad Aslam","score":1.0,"verdict":"same","reasons":["..."]}
curl .../api/v1/address/parse -H "Authorization: Bearer dfk_..." -d '{"address": "H-24, gali no. 3, near Shiv mandir, Uttam Nagar"}'
# {"parts":{"house":"H-24","street":"Gali No. 3","landmark":"Shiv Mandir","landmark_relation":"near","locality":"Uttam Nagar","unparsed":[]},
#  "formatted":"H-24, Gali No. 3, Near Shiv Mandir, Uttam Nagar","missing":["city_or_district","state","pincode"]}
curl .../api/v1/speech/parse  -H "Authorization: Bearer dfk_..." -d '{"text": "pandrah march do hazaar do"}'
# {"input_length":26,"date":{"day":15,"month":3,"year":2002,"dmy":"15/03/2002"}}
```

- **names**: `verdict` is `same` (0.92+), `probably` (0.80+: show both spellings and ask the person) or `different`. Titles and
  "S/O ..." are ignored, word order does not matter, an initial matches a full word, Devanagari / Gurmukhi / Bengali / Gujarati /
  Tamil / Telugu / Kannada / Malayalam letters are read by sound. It is a prompt to confirm, never a decision about identity.
- **address**: a part it cannot place is never guessed; it is listed in `unparsed`. `missing` is what a form usually still has to ask.
- **speech**: `kind` is `auto` (default: a date if one is found, otherwise the number and the digit string), `date`, `number`
  or `digits`.

### `GET /usage`
`{organisation, key_id, services, expires, days_left, quota, finished_drafts, remaining, requests_per_minute}`.
Every authenticated response also carries `X-Key-Expires` and `X-Quota-Remaining`.

## Errors

Always `{"error": {"code": "...", "message": "..."}}`; bodies you sent are never repeated back.

| Status | `code` | Meaning |
|---|---|---|
| 401 | `missing_key` · `invalid_key` · `key_expired` · `key_revoked` | send `Authorization: Bearer dfk_...` or `X-API-Key` |
| 403 | `service_not_in_plan` | this key does not include that service |
| 404 | `unknown_service` · `not_found` | see `/services` |
| 413 | `payload_too_large` | at most 64 KB, 60 fields, 2,000 characters a value |
| 422 | `invalid_request` · `unsupported_language` | the body is not `{"lang", "fields"}` JSON |
| 429 | `rate_limited` · `quota_used` · `too_many_attempts` | wait `Retry-After` seconds / ask for an extension / too many bad keys from one address |
| 500 | `server_error` | ours; nothing about your request is in our logs |

## Limits, and what a 30-day trial is

A trial key is `python -m dwaniforms.apikeys issue --org "<company>" --services rti,rti_appeal --days 30 --quota 500 --rpm 60`:
valid 30 days, 500 finished drafts, 60 requests a minute. The end date is inside the signed key, so it holds even if the
server restarts. **The count of finished drafts is kept in memory** and starts again after a restart (and the free Render
plan sleeps and restarts); for a paid customer, turn on `DWANIFORMS_USAGE_LOG=<file on a persistent disk>` (one line per
call: key id, service, status: no personal data) and invoice from that, or move the count to a database.

## Data, and who is responsible for what

- You are the one who collects your users' details and decides why; DwaniForms processes what you send to build the draft
  and returns it. We do not store, log, sell or look at the fields, and there is no AI model in these calls (the optional
  "ask the AI" feature of the public app is not part of this API).
- Send only what a service asks for. Aadhaar, bank-account and PAN fields exist for `forms` services because those forms
  need them; do not send them for services that do not.
- The drafts and explanations are general guidance prepared from public rules (each paper's sources and review date are in
  the response), **not legal advice**; the RTI / appeal drafts should be read by the applicant before signing.
- Not yet reviewed by a lawyer: a data-processing agreement and terms for companies. Have one signed before the trial
  turns into a paid contract.

## Switching it on (for us)

1. In Render add `DWANIFORMS_KEY_SECRET` (a long random value; `render.yaml` generates one). Without it nothing here exists.
2. Copy that value to your own computer: `set DWANIFORMS_KEY_SECRET=...`.
3. `python -m dwaniforms.apikeys issue --org "RTIwala" --services rti,rti_appeal --days 30` and send the key over a private channel.
4. To cut a key off: put its id in `DWANIFORMS_REVOKED_KEYS=id1,id2` in Render.

Treat the secret like a password: anyone who has it can issue keys. Changing it switches every key off at once.
