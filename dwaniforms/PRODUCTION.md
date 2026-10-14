# DwaniForms in production

## Done in code (Phase 1)

| Area | What | Where |
|---|---|---|
| Abuse | Per-IP limits: 30 new conversations / 10 min, 300 API calls / min; 300 open conversations at most; size limits on every input and on audio | `hardening.py`, `api.py` |
| Web security | CSP, no framing, nosniff, no referrer, mic only for this site, HSTS behind HTTPS, `no-store` on answers | `hardening.py` |
| Privacy | Consent screen before anything is collected (phone: once per visit; kiosk: every person); answers in memory only, deleted after 30 min (15 once finished); Aadhaar/account/PAN masked on screen and in the PDF; no personal data in logs (tested) | `static/app.html`, `service.py`, `tests/test_dwaniforms_hardening.py` |
| Monitoring | `/form/health` (no personal data) for Render and uptime monitors; `/form/metrics` anonymous counts (started / finished / where people stopped / which questions needed repeating) behind `DWANIFORMS_METRICS_TOKEN`, and `/form/metrics/view` to read them on a phone | `hardening.py`, `static/metrics.html` |
| Reliability | Friendly offline / busy messages with retry; numbers said in pieces are joined; 15 s mic cap | `static/app.html`, `session.py` |
| On the phone | Installable from the browser ("Add to phone"), then opens full screen; a service worker keeps the page, icons and fonts (never `/sessions`); a reload or a sleeping phone no longer loses a half-filled form | `static/manifest.webmanifest`, `static/sw.js`, `api.py` |
| Reachable | WCAG AA checked: 4.5:1 contrast everywhere, 44px targets, live regions, screen-reader names translated into all eight languages, heading order, reduced motion | `static/app.html`, `tests/test_dwaniforms_a11y.py`, `tests/js/dwaniforms_a11y.test.js` |
| Understand a paper | Nine kinds of paper explained from hand-written data with sources and a review date; fraud warning read aloud; dates counted; RTI first appeal drafted with its 30-day window | `explain.py`, `papers/*.json`, `outcomes.py` |
| Photo of a paper | Samjhao reads a bill or notice from a photo on the phone (tesseract.js, English + Hindi, no upload); only up to three values reach the server and each is read back for a yes/no; "days left" counts by India's date | `static/ocr/`, `clock.py`, `tests/test_dwaniforms_photo.py`, `tests/js/dwaniforms_ocr.test.js` |
| Optional AI question | Off unless `DWANIFORMS_AI_KEY` is set; consent for every question; identifying numbers blanked; nothing stored or logged; 10 per hour per address; mocked tests | `ai.py`, `tests/test_dwaniforms_ai.py` |
| Company API | Signed per-service keys with a 30-day end, quota and rate; stateless, nothing kept or logged; same validators as the voice app | `apikeys.py`, `b2b.py`, `API.md`, `tests/test_dwaniforms_b2b.py` |
| Scheme forms | Pension (old age / widow / disability), student scholarship and Ayushman card forms in Hindi, English and the eight packs; the pension and Ayushman schemes link to them | `templates/pension_application.json`, `scholarship_application.json`, `ayushman_card.json`, `tests/test_dwaniforms_scheme_forms.py` |
| Spoken numbers / names / addresses | Hindi number and date speech ("do hazaar do"), same-person matching of Indian names across spellings and scripts, free-text address splitting; also sold through the company API | `spoken.py`, `names.py`, `address.py`, `tests/test_dwaniforms_spoken_dates.py`, `test_dwaniforms_names.py`, `test_dwaniforms_address.py`, `test_dwaniforms_tools_api.py` |
| Document photo | Aadhaar / ration card / khasra copy read on the phone; Aadhaar needs a valid check digit, is read back in full and shown masked; only the values found are sent, each confirmed | `static/ocr/dwani-docs.js`, `tests/test_dwaniforms_docs.py`, `tests/js/dwaniforms_docs*.test.js` |
| Supply chain | `pip-audit` on the online app's dependencies in CI | `.github/workflows/tests.yml` |
| Capacity | Load test: 50 people at once p95 ≈ 120 ms, 150 at once p95 ≈ 380 ms, 0 errors (1 CPU) | `tests/load/dwaniforms_load.py` |

## To do: needs a person, money or a signature (Phase 2-4)

- [ ] **Render paid plan** for `dwaniforms-app` (Starter, so it never sleeps). Whisper on the server needs a 2 GB plan.
- [ ] **Own domain** (e.g. dwaniforms.in) for both the site and the app; Render gives free HTTPS.
- [ ] **Uptime monitor** (e.g. UptimeRobot, free) on `https://<app>/form/health`, alerting your e-mail.
- [ ] **Legal review by a lawyer**: DPDP Act 2023 and the DPDP Rules; whether a private service may take Aadhaar numbers to prepare forms (Aadhaar Act). Written and live, but **not yet read by a lawyer**: `dwaniforms_site/terms.html` (what the service does and does not do, fair use, no e-KYC, limits of responsibility, Indian law / Delhi courts) and the privacy policy, which now names a **grievance officer** (Khushi Mittal) with a 30-day reply promise — change the name if someone else should answer complaints.
- [ ] **Pilot** with a CSC / NGO / panchayat: 20-50 real people, older and less literate users included. Open `/form/metrics/view` weekly: the top of "where people stopped" is the question to rewrite first.
- [ ] **Native-speaker review** of Hindi and of the eight language packs in `lang/` (Bengali, Marathi, Gujarati, Punjabi, Tamil, Telugu, Kannada, Malayalam). Each pack says `"status": "draft"` and the app shows a small "not yet checked by a native speaker" note until a reviewer sets it to `"reviewed"`. See `lang/README.md`. Odia, Urdu, Assamese: add a pack (`python -m dwaniforms.langpacks skeleton`) or use NLLB / Bhashini on a bigger server.
- [ ] **Have the nine paper explanations read** by a lawyer or a consumer-rights / legal-aid worker (`papers/*.json`), and the RTI first appeal draft by an RTI activist. Each file names its sources and a `last_reviewed` date; re-check them every 6 months like the scheme rules.
- [ ] **Try the photo reader on real phones and real papers** (a crumpled bill, a bad light, a photocopied notice, a Hindi bill): it was run only on generated pictures. Note how often it finds nothing, and any wrong suggestion; a wrong one is caught by the read-back, but it is worth knowing. Low-end phones: check the 7 MB first-time load on 2G/3G and memory use.
- [ ] **Have the three scheme forms checked** against the current state portals (pension, scholarship, Ayushman): each state asks for slightly different papers and fields, so a centre should compare the questions with the form they actually file and tell us what to add or remove. Scholarship eligibility is deliberately not in the scheme advisor (it differs by state, course and year).
- [ ] **Run the name matcher and the address parser on real data** (20-30 pairs of a person's names from different papers, 50 real addresses from your region): they were written from rules and tested on made-up examples. `probably` is meant to make a person confirm, so look for a wrong `same` first; then for addresses that end up in `unparsed`.
- [ ] **Try the document reader on real cards** (a worn Aadhaar PVC card, a Hindi ration card booklet, a khasra photocopy): it was run only on generated pictures. Hindi ration-card layouts differ by state, so expect the card number to be missed on some; a miss just means the person says it. Check that no wrong Aadhaar number is ever offered (the check digit makes this unlikely, but note any case).
- [ ] **Decide whether to switch the AI question on.** It is off. To turn it on: set `DWANIFORMS_AI_KEY` in Render, set a spending limit and an alert in the API console, read what comes back for twenty real questions in each language you will offer (the answer is not stored, so ask them yourself), and have the privacy page's "Asking an AI" section read by the lawyer with the rest.
- [ ] **Before the first paying company:** a data-processing agreement and API terms read by a lawyer (you are a processor of their users' details under the DPDP Act); decide the price per finished draft; put `DWANIFORMS_USAGE_LOG` on a persistent disk (or add a database) so the count survives a restart; move the app off Render's free plan so it does not sleep; keep `DWANIFORMS_KEY_SECRET` out of chat and e-mail (rotating it switches every key off).
- [ ] **Have RTIwala's real users' typical requests run through `rti_application` and `rti_first_appeal`** and the drafts read by an RTI practitioner; this is what they will judge the product on.
- [ ] **Try a screen reader on a real phone** (TalkBack on Android, VoiceOver on iPhone): the markup is right, but nobody has listened to a whole form being filled.
- [ ] **Try the install on a real phone** (Android Chrome and an iPhone): "Add to phone" needs HTTPS, which Render gives; on an iPhone there is no prompt, the person uses Share › Add to Home Screen. Check the icon, the full-screen opening, and that a half-filled form survives locking the phone.
- [ ] **Real records** only with the data owner's permission; until then the online app shows clearly-marked demo records.
- [ ] Re-check scheme rules every 6 months (`python -m dwaniforms.eligibility` lists any that are due).

## Runbook

- **Is it up?** `curl https://<app>/form/health` → `{"status":"ok",...}`. Render → `dwaniforms-app` → Logs / Events.
- **Pilot numbers:** open `https://<app>/form/metrics/view` on any phone and type the token (`DWANIFORMS_METRICS_TOKEN`); it is sent as a header, never in the address bar. The raw JSON is still at `/form/metrics` with `x-metrics-token`. The counts live in memory, so note them down before a redeploy. Rotate the token in Render → Environment if it leaks.
- **People see "too many requests":** a whole centre shares one IP; raise `LIMITS["new_session"]` in `hardening.py`.
- **Memory high / slow:** fewer open conversations (`MAX_SESSIONS` in `api.py`) or a bigger plan; run the load test against a staging copy, never against someone else's server.
- **A security fix in a dependency (`security` check red):** bump the version in `requirements-web.txt`, run the tests, push.
- **Data incident** (e.g. a leaked log): rotate tokens, check what was exposed; under the DPDP Rules a personal-data breach must be reported, so involve your legal adviser the same day.
