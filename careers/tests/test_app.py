"""End-to-end tests for the careers portal (throwaway SQLite DB, no real email).

Every test runs twice: standalone, and mounted at /careers inside a parent
FastAPI app exactly the way license_server.py mounts it -- so every link,
redirect, QR URL and did:web identifier is checked with the prefix too.
"""

import datetime as dt
import os
import re
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OWNER = ("owner@example.com", "s3cret-owner-pass")


@pytest.fixture(scope="module", params=["", "/careers"], ids=["standalone", "mounted"])
def client(request, tmp_path_factory):
    prefix = request.param
    db_file = tmp_path_factory.mktemp("db") / "t.db"
    pg = os.environ.get("CAREERS_TEST_PG")  # e.g. postgresql://user:pw@localhost/db -> run against real Postgres
    if pg:
        import sqlalchemy as sa
        eng = sa.create_engine(pg.replace("postgresql://", "postgresql+psycopg2://"))
        with eng.begin() as conn:  # fresh schema per run
            conn.execute(sa.text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
        eng.dispose()
    os.environ.update(DATABASE_URL=pg or f"sqlite:///{db_file}", CAREERS_ADMIN_EMAIL=OWNER[0], CAREERS_ADMIN_PASSWORD=OWNER[1],
                      CAREERS_PUBLIC_URL="http://testserver" + prefix, CAREERS_SMTP_USER="", CAREERS_SMTP_PASSWORD="",
                      SMTP_USER="", SMTP_PASSWORD="")
    for m in [m for m in sys.modules if m == "careers" or m.startswith("careers.")]:
        sys.modules.pop(m)
    import careers.app as appmod
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    if prefix:
        parent = FastAPI()

        @parent.get("/")
        def site_home():
            return {"site": "dwanilive"}

        parent.mount(prefix, appmod.app)
        target = parent
    else:
        target = appmod.app

    class Prefixed(TestClient):
        def request(self, method, url, *a, **kw):
            if isinstance(url, str) and url.startswith("/") and not url.startswith(prefix + "/"):
                url = prefix + url
            return super().request(method, url, *a, **kw)

    with Prefixed(target) as c:
        c.appmod, c.prefix = appmod, prefix
        yield c


def csrf(c, url):
    return re.search(r'name="csrf" value="([^"]+)"', c.get(url).text).group(1)


PDF = b"%PDF-1.4\n% fake resume\n"
GOOD = dict(name="Aarav Sharma", email="aarav@example.com", phone="9810000001", city="Delhi",
            college="Guru Tegh Bahadur Institute of Technology", degree="B.Tech IT", grad_year="2028", cgpa="8.9",
            skills="Python, PyTorch, Whisper, NLP", links="https://github.com/aarav", hours="20",
            answer="Built a Hindi ASR fine-tune.", why="I fine-tuned Whisper on Hindi lectures " * 6,
            source="LinkedIn", consent="1")


def login(c, email=OWNER[0], pw=OWNER[1]):
    t = csrf(c, "/admin/login")
    return c.post("/admin/login", data={"email": email, "password": pw, "csrf": t}, follow_redirects=False)


def loc(r):
    return r.headers["location"]


def logout(c):
    c.post("/admin/logout", data={"csrf": csrf(c, "/admin")})


def test_public_pages(client):
    r = client.get("/")
    assert r.status_code == 200 and "Speech &amp; Language Research Intern" in r.text
    assert "Machine Learning Intern" not in r.text  # closed in round one
    r = client.get("/roles/speech-research")
    assert '"@type": "JobPosting"' in r.text and "Describe an experiment" in r.text
    assert client.get("/roles/nope").status_code == 404
    assert loc(client.get("/admin", follow_redirects=False)) == client.prefix + "/admin/login"
    sm = client.get("/sitemap.xml").text
    assert "/roles/speech-research" in sm and "/roles/ml<" not in sm


def test_apply(client):
    r = client.post("/roles/speech-research/apply", data={"name": "X"})
    assert r.status_code == 422 and "Please enter your email" in r.text and "Upload your resume" in r.text
    r = client.post("/roles/speech-research/apply", data=GOOD, files={"resume_file": ("cv.docx", b"PK\x03\x04", "application/octet-stream")})
    assert "as a PDF" in r.text
    r = client.post("/roles/speech-research/apply", data=GOOD, files={"resume_file": ("cv.pdf", PDF, "application/pdf")},
                    follow_redirects=False)
    assert r.status_code == 303
    token = r.headers["location"].split("t=")[1]
    assert "Track your application" in client.get(r.headers["location"]).text
    s = client.get(f"/status/{token}")
    assert "Application received" in s.text and "Withdraw application" in s.text
    assert "already applied" in client.post("/roles/speech-research/apply", data=GOOD,
                                            files={"resume_file": ("cv.pdf", PDF, "application/pdf")}).text
    client.post("/roles/speech-research/apply", data=dict(GOOD, email="bot@example.com", website="spam"))
    client.post("/roles/backend-platform/apply", data=dict(GOOD, name="Priya Nair", email="priya@example.com",
                                                     resume="https://drive.google.com/p", links=""))
    client.post("/roles/frontend-accessibility/apply", data=dict(GOOD, name="Rohan Verma", email="rohan@example.com",
                                                  resume="https://drive.google.com/r"))


def test_login(client):
    r = client.post("/admin/login", data={"email": OWNER[0], "password": "wrong", "csrf": csrf(client, "/admin/login")},
                    follow_redirects=True)
    assert "Wrong email or password" in r.text
    assert client.post("/admin/login", data={"email": OWNER[0], "password": OWNER[1], "csrf": "x"}).status_code == 400
    assert loc(login(client)) == client.prefix + "/admin"
    r = client.get("/admin")
    assert "Aarav Sharma" in r.text and "Priya Nair" in r.text and "bot@example.com" not in r.text
    assert "New this week" in r.text


def cid_of(client, name):
    return int(re.search(rf'href="[^"]*/admin/c/(\d+)">{name}<', client.get("/admin?q=" + name.split()[0]).text).group(1))


def test_full_pipeline(client):
    cid = cid_of(client, "Aarav Sharma")
    page = f"/admin/c/{cid}"
    t = csrf(client, page)
    assert client.get(f"{page}/resume").content == PDF
    assert client.post(f"{page}/move", data={"status": "selected", "csrf": t}).status_code == 400
    client.post(f"{page}/move", data={"status": "shortlisted", "csrf": t})
    when = (dt.datetime.now() + dt.timedelta(days=2)).strftime("%Y-%m-%dT16:00")
    client.post(f"{page}/interview", data={"when": when, "duration": "30", "where": "https://meet.google.com/x", "csrf": t})
    assert "Upcoming interviews" in client.get("/admin").text
    client.post(f"{page}/scorecard", data={"skills": 5, "communication": 4, "ownership": 5,
                                           "recommendation": "strong_yes", "notes": "great", "csrf": t})
    assert "Strong yes" in client.get(page).text
    client.post(f"{page}/move", data={"status": "selected", "csrf": t})
    start, end = dt.date.today() - dt.timedelta(days=90), dt.date.today() - dt.timedelta(days=1)
    r = client.post(f"{page}/offer", data={"start": start.isoformat(), "end": end.isoformat(), "role_id": 1,
                                           "stipend": "₹5,000 per month", "csrf": t})
    off = re.search(r"(DL-OFF-\d{4}-\d{4})", r.text).group(1)
    assert "not sent" in r.text
    assert client.get(f"/admin/doc/{off}.pdf").content.startswith(b"%PDF")
    client.post(f"/admin/doc/{off}/send", data={"csrf": t})
    time.sleep(0.6)
    assert "email failed" in client.get(page).text  # SMTP unset -> recorded, not silent
    client.post(f"{page}/move", data={"status": "joined", "csrf": t})
    r = client.post(f"{page}/certificate", data={"highlight": "Built the Hindi ASR benchmark", "csrf": t})
    cert = re.search(r"(DL-CERT-\d{4}-\d{4})", r.text).group(1)
    r = client.post(f"{page}/lor", data={"body": "Aarav built our benchmark and was excellent. " * 4, "csrf": t})
    lor = re.search(r"(DL-LOR-\d{4}-\d{4})", r.text).group(1)
    client.post(f"/admin/doc/{cert}/send", data={"csrf": t})
    v = client.get(f"/verify?id={cert.lower()}")
    assert "Genuine" in v.text and "Add to LinkedIn" in v.text and "aarav@example.com" not in v.text
    assert "Letter of Recommendation" in client.get(f"/verify?id={lor}").text
    # applicant can download only sent documents from their status page
    token = re.search(r"/status/([\w-]+)", client.get(page).text).group(1)
    st = client.get(f"/status/{token}")
    assert cert in st.text and lor not in st.text and "Withdraw" not in st.text
    assert client.get(f"/status/{token}/doc/{cert}.pdf").content.startswith(b"%PDF")
    assert client.get(f"/status/{token}/doc/{lor}.pdf").status_code == 404
    # --- signed credentials ---
    cert_pdf = client.get(f"/admin/doc/{cert}.pdf").content
    from careers import credentials as vc
    tok = vc.extract_from_pdf(cert_pdf)
    assert tok and tok == client.get(f"/api/credentials/{cert}").text
    did = client.get("/did.json").json()
    assert did["id"] == "did:web:testserver" + client.prefix.replace("/", ":")
    assert did == client.get("/.well-known/did.json").json()
    assert vc.verify(tok, vc.keys_from_did_document(did)).valid
    assert "server check: valid" in client.get(f"/verify?id={cert}").text
    import io
    up = client.post("/verify/upload", files={"pdf": ("c.pdf", cert_pdf, "application/pdf")})
    assert "This exact file was issued" in up.text
    from pypdf import PdfReader, PdfWriter
    rw = PdfWriter(clone_from=PdfReader(io.BytesIO(cert_pdf))); rw.add_metadata({"/Producer": "re-saved"})
    _b = io.BytesIO(); rw.write(_b); resaved = _b.getvalue()  # different bytes, same embedded credential
    assert "Contains a valid signed credential" in client.post("/verify/upload", files={"pdf": ("c.pdf", resaved, "application/pdf")}).text
    from reportlab.pdfgen import canvas
    import io
    fake = io.BytesIO(); cv = canvas.Canvas(fake); cv.drawString(100, 700, "Certificate"); cv.save()
    assert "Not a document issued" in client.post("/verify/upload", files={"pdf": ("f.pdf", fake.getvalue(), "application/pdf")}).text
    h, p, sgn = tok.split(".")
    import json as _j
    forged = vc.b64u(_j.dumps(dict(_j.loads(vc.b64u_dec(p)), credentialSubject={"name": "Someone Else", "documentNumber": cert}),
                              separators=(",", ":")).encode())
    forged_pdf = vc.embed_in_pdf(fake.getvalue(), f"{h}.{forged}.{sgn}")
    r = client.post("/verify/upload", files={"pdf": ("x.pdf", forged_pdf, "application/pdf")})
    assert "not valid" in r.text and "Someone Else" not in r.text
    open("/tmp/browser_check.json", "w").write(_j.dumps({"jwt": tok, "did": did}))
    assert client.get(f"/api/credentials/{cert}/status").json()["revoked"] is False
    client.post(f"/admin/doc/{cert}/revoke", data={"csrf": t})
    assert "Revoked" in client.get(f"/verify?id={cert}").text
    assert client.get(f"/api/credentials/{cert}/status").json()["revoked"] is True


def test_offer_letter_has_no_acceptance_block(client):
    cid = cid_of(client, "Aarav Sharma")
    off = re.search(r"(DL-OFF-\d{4}-\d{4})", client.get(f"/admin/c/{cid}").text).group(1)
    from pypdf import PdfReader
    import io
    text = PdfReader(io.BytesIO(client.get(f"/admin/doc/{off}.pdf").content)).pages[0].extract_text()
    assert "pleased to offer" in text and "Acceptance" not in text and "I accept" not in text


def test_certificate_refused_before_end(client):
    cid = cid_of(client, "Priya Nair")
    page = f"/admin/c/{cid}"
    t = csrf(client, page)
    for st in ("shortlisted", "selected"):
        client.post(f"{page}/move", data={"status": st, "csrf": t})
    future = dt.date.today() + dt.timedelta(days=60)
    client.post(f"{page}/offer", data={"start": dt.date.today().isoformat(), "end": future.isoformat(), "role_id": 2, "csrf": t})
    client.post(f"{page}/move", data={"status": "joined", "csrf": t})
    r = client.post(f"{page}/certificate", data={"csrf": t})
    assert r.status_code == 400 and "Issue the certificate after it" in r.text


def test_withdraw_by_applicant(client):
    cid = cid_of(client, "Rohan Verma")
    token = re.search(r"/status/([\w-]+)", client.get(f"/admin/c/{cid}").text).group(1)
    t = csrf(client, f"/status/{token}")
    client.post(f"/status/{token}/withdraw", data={"csrf": t})
    assert "was withdrawn" in client.get(f"/status/{token}").text


def test_team_reviewer_permissions(client):
    t = csrf(client, "/admin/team")
    client.post("/admin/team", data={"action": "add", "name": "Senior Intern", "email": "rev@example.com",
                                     "password": "reviewer-pass-123", "role": "reviewer", "csrf": t})
    assert "rev@example.com" in client.get("/admin/team").text
    logout(client)
    assert login(client, "rev@example.com", "reviewer-pass-123").status_code == 303
    assert client.get("/admin").status_code == 200
    assert client.get("/admin/settings").status_code == 403
    assert client.get("/admin/export.csv").status_code == 403
    cid = cid_of(client, "Aarav Sharma")
    t = csrf(client, f"/admin/c/{cid}")
    assert client.post(f"/admin/c/{cid}/move", data={"status": "rejected", "csrf": t}).status_code == 403
    client.post(f"/admin/c/{cid}/scorecard", data={"skills": 3, "communication": 3, "ownership": 4,
                                                   "recommendation": "yes", "csrf": t})
    assert "Senior Intern" in client.get(f"/admin/c/{cid}").text
    logout(client)
    login(client)


def test_settings_and_signature(client):
    t = csrf(client, "/admin/settings")
    from PIL import Image
    import io
    buf = io.BytesIO()
    Image.new("RGBA", (300, 100), (0, 0, 0, 0)).save(buf, "PNG")
    client.post("/admin/settings", data={"csrf": t, "signatory.title": "Founder & CEO", "org.legal_name": "DwaniLive"},
                files={"signature": ("sig.png", buf.getvalue(), "image/png")})
    r = client.get("/admin/settings")
    assert "Founder &amp; CEO" in r.text and "A signature is set" in r.text
    assert client.get("/admin/settings/sample.pdf").content.startswith(b"%PDF")
    assert client.get("/admin/settings/sample.pdf?kind=certificate").content.startswith(b"%PDF")
    assert "Document signing" in r.text and "set CAREERS_SIGNING_KEY" in r.text


def test_roles_analytics_broadcast_export(client):
    t = csrf(client, "/admin/roles")
    client.post("/admin/roles", data={"title": "Design Intern", "key": "Design", "summary": "UI", "is_open": "1",
                                      "openings": "2", "closes_on": (dt.date.today() - dt.timedelta(days=1)).isoformat(),
                                      "csrf": t})
    assert "Design Intern" not in client.get("/").text  # closing date passed
    assert client.post("/roles/design/apply", data=dict(GOOD, email="z@example.com")).status_code == 404
    a = client.get("/admin/analytics")
    assert a.status_code == 200 and "Funnel" in a.text and "LinkedIn" in a.text
    t = csrf(client, "/admin/broadcast")
    r = client.post("/admin/broadcast", data={"statuses": ["joined", "completed"], "subject": "Onboarding call",
                                              "body": "Hi {first_name}", "csrf": t}, follow_redirects=True)
    assert "Sending to 2 people" in r.text
    assert "aarav@example.com" in client.get("/admin/export.csv").text


def test_delete_applicant(client):
    client.post("/roles/frontend-accessibility/apply", data=dict(GOOD, name="Temp Person", email="temp@example.com",
                                                  resume="https://drive.google.com/t"))
    cid = cid_of(client, "Temp Person")
    t = csrf(client, f"/admin/c/{cid}")
    client.post(f"/admin/c/{cid}/delete", data={"confirm": "temp@example.com", "csrf": t})
    assert "Temp Person" not in client.get("/admin?q=Temp").text


def test_starter_roles_seeded_with_projects(client):
    home = client.get("/").text
    for title in ("Speech &amp; Language Research Intern", "Backend &amp; Platform Intern",
                  "Frontend &amp; Accessibility Intern"):
        assert title in home
    for closed in ("QA &amp; Release", "Technical Writer", "Pilot &amp; Market Research", "Machine Learning Intern",
                   "Full-Stack Developer Intern", "Growth &amp; Partnerships Intern"):
        assert closed not in home
    assert client.post("/roles/ml/apply", data=GOOD).status_code == 404
    page = client.get("/roles/speech-research").text
    assert "Example projects (6)" in page and "Smarter caption segmentation" in page
    assert "Example projects" not in client.get("/roles/ml").text


def test_sync_adds_only_missing_roles(client):
    page = client.get("/admin/roles").text
    assert "starter role(s) from config" not in page          # nothing missing on a fresh seed
    from sqlalchemy import select
    with client.appmod.db.session() as s:
        role = s.scalar(select(client.appmod.Role).where(client.appmod.Role.key == "design-brand"))
        s.delete(role)
        mine = s.scalar(select(client.appmod.Role).where(client.appmod.Role.key == "ml"))
        mine.title = "My Custom ML Title"                      # a customised role must survive the sync
    page = client.get("/admin/roles").text
    assert "1 starter role(s)" in page and "Product Design" in page
    t = csrf(client, "/admin/roles")
    r = client.post("/admin/roles/sync", data={"csrf": t}, follow_redirects=True)
    assert "Added 1 role(s): Product Design &amp; Brand Intern" in r.text
    assert "My Custom ML Title" in r.text and "starter role(s) from config" not in r.text
    assert "Nothing to add" in client.post("/admin/roles/sync", data={"csrf": t}, follow_redirects=True).text


def test_roles_form_saves_projects(client):
    t = csrf(client, "/admin/roles")
    client.post("/admin/roles", data={"id": "", "key": "tmp-role", "title": "Tmp", "is_open": "1",
                                      "projects": "One :: do a thing :: a result\nTwo", "csrf": t})
    p = client.get("/roles/tmp-role").text
    assert "<b>One</b>: do a thing" in p and "<b>Two</b>" in p


def test_apply_opening_plan(client):
    from sqlalchemy import select
    R = client.appmod.Role
    with client.appmod.db.session() as s:   # simulate an older deployment where ML was still open
        s.scalar(select(R).where(R.key == "ml")).is_open = True
    page = client.get("/admin/roles").text
    assert "Opening plan from config" in page and "My Custom ML Title → <b>closed</b>" in page
    t = csrf(client, "/admin/roles")
    r = client.post("/admin/roles/apply-plan", data={"csrf": t}, follow_redirects=True)
    assert "Updated 1 role(s)" in r.text and "Opening plan from config" not in r.text
    assert "My Custom ML Title" not in client.get("/").text


def test_links_and_qr_use_mount_prefix(client):
    p = client.prefix
    home = client.get("/").text
    assert f'href="{p}/roles/speech-research"' in home and f'href="{p}/static/site.css"' in home
    assert f'action="{p}/roles/speech-research/apply"' in client.get("/roles/speech-research").text
    r = client.post("/roles/backend-platform/apply", data=dict(GOOD, name="Link Check", email="link@example.com",
                    resume="https://drive.google.com/l"), follow_redirects=False)
    assert loc(r).startswith(p + "/thanks?t=")




def test_short_form_only_essentials_required(client):
    """The application needs only name, email, phone, college, degree+year, resume and
    the role's question; everything else is optional and tucked away."""
    minimal = dict(name="Short Form", email="short@example.com", phone="9000000000", college="GTBIT",
                   degree="B.Tech IT, 2nd year", resume="https://drive.google.com/s",
                   answer="I measured WER on my own recordings.", consent="1")
    r = client.post("/roles/speech-research/apply", data=minimal, follow_redirects=False)
    assert r.status_code == 303, r.text[:500]
    page = client.get("/roles/backend-platform").text
    assert '<details class="more">' in page and "More about you" in page
    bad = dict(minimal, email="short2@example.com", links="not a url")
    r = client.post("/roles/speech-research/apply", data=bad)
    assert r.status_code == 422 and '<details class="more" open>' in r.text   # optional section opens to show its error
