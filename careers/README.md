# DwaniLive Careers (`/careers`)

DwaniLive's hiring and internship portal, part of the DwaniLive website. `license_server.py` mounts it at
**https://dhwani-elit.onrender.com/careers/**, so it runs on the same Render service and the same Supabase
database. All its tables are named `careers_*`, and it never reads or writes license or user data. It is
server-only code: the desktop app (`DwaniLive.exe`) doesn't include it.

| Who | URL | What |
|---|---|---|
| Students | `/careers/` | Open roles, each with the projects an intern could own |
| Students | `/careers/roles/<key>` | Role details + application form (resume PDF or link) |
| Applicants | `/careers/status/<token>` | Track the application, see the interview, download documents, withdraw |
| Anyone | `/careers/verify` | Check an offer letter / certificate / recommendation by number or by uploading the PDF |
| Team | `/careers/admin` | Board, applicant pages, scorecards, documents, analytics, broadcast, roles, team, settings |

Every document is a PDF plus an **Ed25519-signed W3C Verifiable Credential** embedded inside it. The issuer
is `did:web:dhwani-elit.onrender.com:careers`, and its public keys are at `/careers/did.json`. Documents can be
verified on the website (the browser re-checks the signature with WebCrypto), by uploading the PDF, or offline:
`python careers/tools/verify_credential.py file.pdf --did https://dhwani-elit.onrender.com/careers/did.json --status`.

## Setting it up on Render (one time)

The DwaniLive service already has `DATABASE_URL` and `SMTP_USER`/`SMTP_PASSWORD`. Careers uses those.
Add these in Render → `dwanilive-license-server` → **Environment**:

| Variable | Value |
|---|---|
| `CAREERS_PUBLIC_URL` | `https://dhwani-elit.onrender.com/careers` (it's printed in every QR, so set it **before** issuing documents) |
| `CAREERS_ADMIN_EMAIL` | your email: the owner login for `/careers/admin` |
| `CAREERS_ADMIN_PASSWORD` | a long password |
| `CAREERS_SESSION_SECRET` | any long random string |
| `CAREERS_SIGNING_KEY` | output of `python -m careers.credentials new-key`. Keep a copy in your password manager. Never commit it |
| `CAREERS_SMTP_USER` / `CAREERS_SMTP_PASSWORD` | *optional*: a separate Gmail for hiring mail (otherwise `SMTP_USER` is used) |

Save, then the service redeploys. Tables are created automatically on the first visit to `/careers/`.
Next, open `/careers/admin/settings`: the system check should show the database, email and signing key in green.
Upload your signature there and preview the sample offer letter.
Finally, in `/careers/admin/roles`: click **Add them** if it says starter roles are missing, then **Apply opening plan**.
Round one opens Speech & Language Research, Backend & Platform, and Frontend & Accessibility.

If careers ever fails to start, the license server keeps working. Only `/careers` is unavailable, and the
error appears in the Render logs.

## Code

| File | What |
|---|---|
| `app.py` | the portal (FastAPI sub-app; all links follow the mount path) |
| `db.py` | models, automatic add-column migration, starter-role sync and opening plan |
| `credentials.py` | Ed25519 keys, did:web, VC 2.0 as `vc+jwt`, PDF embedding |
| `documents.py` | offer letter, completion certificate, recommendation letter (Rozha One + Mukta) |
| `emails.py`, `scoring.py` | email texts; fit-score sorting aid |
| `config.toml` | first-run defaults and the starter roles with their projects |
| `templates/`, `static/`, `fonts/` | pages, styles, logo, fonts (SIL OFL) |
| `tools/verify_credential.py` | offline verifier |

## Tests

`pip install -r requirements.txt pytest httpx pillow`, then `pytest careers/tests`. That runs 41 tests:
the end-to-end suite runs twice (standalone and mounted at `/careers`), plus crypto and migration tests.
Set `CAREERS_TEST_PG=postgresql://…` to run the end-to-end suite against PostgreSQL. CI does both on every push
(`.github/workflows/careers-tests.yml`).
