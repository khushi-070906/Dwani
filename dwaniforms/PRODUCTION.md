# DwaniForms in production

## Done in code (Phase 1)

| Area | What | Where |
|---|---|---|
| Abuse | Per-IP limits: 30 new conversations / 10 min, 300 API calls / min; 300 open conversations at most; size limits on every input and on audio | `hardening.py`, `api.py` |
| Web security | CSP, no framing, nosniff, no referrer, mic only for this site, HSTS behind HTTPS, `no-store` on answers | `hardening.py` |
| Privacy | Consent screen before anything is collected (phone: once per visit; kiosk: every person); answers in memory only, deleted after 30 min (15 once finished); Aadhaar/account/PAN masked on screen and in the PDF; no personal data in logs (tested) | `static/app.html`, `service.py`, `tests/test_dwaniforms_hardening.py` |
| Monitoring | `/form/health` (no personal data) for Render and uptime monitors; `/form/metrics` anonymous counts (started / finished / where people stopped / which questions needed repeating) behind `DWANIFORMS_METRICS_TOKEN` | `hardening.py` |
| Reliability | Friendly offline / busy messages with retry; numbers said in pieces are joined; 15 s mic cap | `static/app.html`, `session.py` |
| On the phone | Installable from the browser ("Add to phone"), then opens full screen; a service worker keeps the page, icons and fonts (never `/sessions`); a reload or a sleeping phone no longer loses a half-filled form | `static/manifest.webmanifest`, `static/sw.js`, `api.py` |
| Supply chain | `pip-audit` on the online app's dependencies in CI | `.github/workflows/tests.yml` |
| Capacity | Load test: 50 people at once p95 ≈ 120 ms, 150 at once p95 ≈ 380 ms, 0 errors (1 CPU) | `tests/load/dwaniforms_load.py` |

## To do: needs a person, money or a signature (Phase 2-4)

- [ ] **Render paid plan** for `dwaniforms-app` (Starter, so it never sleeps). Whisper on the server needs a 2 GB plan.
- [ ] **Own domain** (e.g. dwaniforms.in) for both the site and the app; Render gives free HTTPS.
- [ ] **Uptime monitor** (e.g. UptimeRobot, free) on `https://<app>/form/health`, alerting your e-mail.
- [ ] **Legal review by a lawyer**: DPDP Act 2023 and the DPDP Rules; whether a private service may take Aadhaar numbers to prepare forms (Aadhaar Act). Written and live, but **not yet read by a lawyer**: `dwaniforms_site/terms.html` (what the service does and does not do, fair use, no e-KYC, limits of responsibility, Indian law / Delhi courts) and the privacy policy, which now names a **grievance officer** (Khushi Mittal) with a 30-day reply promise — change the name if someone else should answer complaints.
- [ ] **Pilot** with a CSC / NGO / panchayat: 20-50 real people, older and less literate users included. Read `/form/metrics` weekly.
- [ ] **Native-speaker review** of Hindi and of the eight language packs in `lang/` (Bengali, Marathi, Gujarati, Punjabi, Tamil, Telugu, Kannada, Malayalam). Each pack says `"status": "draft"` and the app shows a small "not yet checked by a native speaker" note until a reviewer sets it to `"reviewed"`. See `lang/README.md`. Odia, Urdu, Assamese: add a pack (`python -m dwaniforms.langpacks skeleton`) or use NLLB / Bhashini on a bigger server.
- [ ] **Try the install on a real phone** (Android Chrome and an iPhone): "Add to phone" needs HTTPS, which Render gives; on an iPhone there is no prompt, the person uses Share › Add to Home Screen. Check the icon, the full-screen opening, and that a half-filled form survives locking the phone.
- [ ] **Real records** only with the data owner's permission; until then the online app shows clearly-marked demo records.
- [ ] Re-check scheme rules every 6 months (`python -m dwaniforms.eligibility` lists any that are due).

## Runbook

- **Is it up?** `curl https://<app>/form/health` → `{"status":"ok",...}`. Render → `dwaniforms-app` → Logs / Events.
- **Pilot numbers:** `curl -H "x-metrics-token: <DWANIFORMS_METRICS_TOKEN>" https://<app>/form/metrics`. Rotate the token in Render → Environment if it leaks.
- **People see "too many requests":** a whole centre shares one IP; raise `LIMITS["new_session"]` in `hardening.py`.
- **Memory high / slow:** fewer open conversations (`MAX_SESSIONS` in `api.py`) or a bigger plan; run the load test against a staging copy, never against someone else's server.
- **A security fix in a dependency (`security` check red):** bump the version in `requirements-web.txt`, run the tests, push.
- **Data incident** (e.g. a leaked log): rotate tokens, check what was exposed; under the DPDP Rules a personal-data breach must be reported, so involve your legal adviser the same day.
