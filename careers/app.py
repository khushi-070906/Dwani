"""
careers/app.py -- DwaniLive Careers: hiring & internship portal.

Runs inside the DwaniLive website: license_server.py mounts it at /careers
(same Render service, same Supabase database -- every table is prefixed
careers_ so license data is never touched). It can also run on its own:
    uvicorn careers.app:app --reload
All links are relative to wherever it is mounted (request root_path).

Public
  /                       open roles
  /roles/<key>            role details + application form (resume upload or link)
  /status/<token>         applicant's own tracking page (stage, interview, documents, withdraw)
  /verify?id=...          verify an offer letter / certificate / recommendation (QR on every PDF)
Team (/admin, login with email + password)
  /admin                  applicant board: reminders, filters, bulk actions, scores
  /admin/c/<id>           applicant: stages, interview invite (+ calendar file), scorecards, notes,
                          offer letter, completion certificate, letter of recommendation
  /admin/analytics        funnel, roles, colleges, weekly applications, sources
  /admin/broadcast        email a group (e.g. all current interns)          (owner only)
  /admin/roles            create / edit / open / close roles
  /admin/team             owners and reviewers                              (owner only)
  /admin/settings         organisation, signatory, signature, defaults      (owner only)
"""

from __future__ import annotations

import asyncio
import copy
import csv
import datetime as dt
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import smtplib
import threading
import time
import tomllib
import urllib.parse
import uuid
from collections import Counter, defaultdict, deque
from contextlib import asynccontextmanager
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from . import credentials as vc
from . import db
from .db import Asset, Candidate, Document, Event, PublicKey, Role, Scorecard, Setting, User
from .emails import TEMPLATES
from .scoring import score

ROOT = Path(__file__).resolve().parent
TOML = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
# Everything is namespaced CAREERS_* so it can never collide with the license
# server's own settings. Email falls back to the website's SMTP account.
PUBLIC_URL = os.environ.get("CAREERS_PUBLIC_URL", "http://localhost:8000/careers").rstrip("/")
OWNER_EMAIL = os.environ.get("CAREERS_ADMIN_EMAIL", "").strip().lower()
OWNER_PASSWORD = os.environ.get("CAREERS_ADMIN_PASSWORD", "")
SESSION_SECRET = os.environ.get("CAREERS_SESSION_SECRET") or secrets.token_hex(32)
SMTP_USER = os.environ.get("CAREERS_SMTP_USER") or os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("CAREERS_SMTP_PASSWORD") or os.environ.get("SMTP_PASSWORD", "")
IS_PROD = PUBLIC_URL.startswith("https://")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
MAX_RESUME = 2 * 1024 * 1024

STATUSES = ["applied", "shortlisted", "interview", "selected", "offered", "joined", "completed",
            "declined", "rejected", "withdrawn"]
FLOW = {
    "applied": ["shortlisted", "rejected", "withdrawn"],
    "shortlisted": ["interview", "selected", "rejected", "withdrawn"],
    "interview": ["selected", "rejected", "withdrawn"],
    "selected": ["offered", "rejected", "withdrawn"],
    "offered": ["joined", "declined", "withdrawn"],
    "joined": ["completed", "withdrawn"],
}
STATUS_LABEL = {"applied": "Applied", "shortlisted": "Shortlisted", "interview": "Interview", "selected": "Selected",
                "offered": "Offer sent", "joined": "Intern", "completed": "Completed", "declined": "Declined offer",
                "rejected": "Rejected", "withdrawn": "Withdrawn"}
ACTION_LABEL = {"shortlisted": "Shortlist", "interview": "Mark interview", "selected": "Select", "offered": "Offer sent",
                "joined": "Mark as joined", "completed": "Mark completed", "declined": "Declined offer",
                "rejected": "Reject", "withdrawn": "Withdrawn"}
PUBLIC_STAGES = [("received", "Application received"), ("review", "Under review"), ("interview", "Interview"),
                 ("offer", "Offer"), ("intern", "Internship")]
PUBLIC_STAGE_OF = {"applied": "received", "shortlisted": "review", "interview": "interview", "selected": "review",
                   "offered": "offer", "joined": "intern", "completed": "intern"}
RECO = {"strong_yes": "Strong yes", "yes": "Yes", "no": "No", "strong_no": "Strong no"}
SETTING_FIELDS = [
    ("org.name", "Organisation name (short)", "org"), ("org.legal_name", "Legal name on letters", "org"),
    ("org.tagline", "Tagline", "org"), ("org.address", "Address", "org"), ("org.website", "Website", "org"),
    ("org.email", "Contact email (reply-to)", "org"), ("org.doc_prefix", "Document number prefix", "org"),
    ("signatory.name", "Signatory name", "signatory"), ("signatory.title", "Signatory designation", "signatory"),
    ("offer_defaults.mode", "Default mode (Remote / Hybrid / On-site)", "offer"),
    ("offer_defaults.hours_per_week", "Default hours per week", "offer"),
    ("offer_defaults.stipend", "Default stipend (e.g. ₹5,000 per month, or Unpaid)", "offer"),
    ("offer_defaults.reporting_to", "Interns report to", "offer"),
    ("offer_defaults.notice_days", "Notice period (days)", "offer"),
]


_ready = False
_ready_lock = threading.Lock()


def ensure_ready() -> None:
    """Create/upgrade tables once. Called from lifespan when run standalone and
    from the first request when mounted (Starlette doesn't run a mounted app's lifespan)."""
    global _ready
    if _ready:
        return
    with _ready_lock:
        if not _ready:
            db.init_db(TOML.get("roles", []))
            if not (OWNER_EMAIL and OWNER_PASSWORD):
                print("careers: CAREERS_ADMIN_EMAIL / CAREERS_ADMIN_PASSWORD not set -- only team members already "
                      "in the database can log in.")
            _ready = True


@asynccontextmanager
async def lifespan(_app):
    ensure_ready()
    yield


app = FastAPI(title="Careers", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, session_cookie="careers_session",
                   max_age=60 * 60 * 12, same_site="lax", https_only=IS_PROD)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
templates = Jinja2Templates(directory=ROOT / "templates")
templates.env.globals.update(STATUS_LABEL=STATUS_LABEL, STATUSES=STATUSES, ACTION_LABEL=ACTION_LABEL, RECO=RECO,
                             PUBLIC_URL=PUBLIC_URL)
templates.env.filters["date"] = lambda d: d.strftime("%d %b %Y").lstrip("0") if d else ""
templates.env.filters["datetime"] = lambda d: (d.replace(tzinfo=dt.timezone.utc).astimezone(IST)
                                               .strftime("%d %b %Y, %I:%M %p").lstrip("0")) if d else ""
templates.env.filters["ist"] = lambda d: d.strftime("%a %d %b %Y, %I:%M %p IST") if d else ""


def root(request: Request) -> str:
    """Where this app is mounted ('' standalone, '/careers' inside the website)."""
    return request.scope.get("root_path", "").rstrip("/")


def local_path(request: Request) -> str:
    p, r = request.scope.get("path", ""), root(request)
    return p[len(r):] if r and p.startswith(r) else p


def go(request: Request, path: str) -> RedirectResponse:
    return RedirectResponse(root(request) + path, 303)


def back(request: Request, fallback: str) -> RedirectResponse:
    ref = request.headers.get("referer", "")
    return RedirectResponse(ref if ref else root(request) + fallback, 303)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    if not _ready:
        await asyncio.to_thread(ensure_ready)
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    if local_path(request).startswith(("/admin", "/status")):
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Robots-Tag"] = "noindex"
    return resp


# ---------------------------------------------------------------------------
# settings: config.toml defaults, overridden from Admin -> Settings
# ---------------------------------------------------------------------------

def get_cfg(s) -> dict:
    cfg = copy.deepcopy(TOML)
    for row in s.scalars(select(Setting)):
        sec, _, key = row.key.partition(".")
        if sec in cfg and key:
            cfg[sec][key] = row.value
    sig = s.get(Asset, "signature")
    cfg["signatory"]["signature_png"] = sig.data if sig else None
    for k in ("hours_per_week", "notice_days"):
        try:
            cfg["offer_defaults"][k] = int(cfg["offer_defaults"][k])
        except (TypeError, ValueError):
            pass
    return cfg


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def render(request: Request, name: str, status_code: int = 200, **ctx) -> HTMLResponse:
    if "org" not in ctx:
        with db.session() as s:
            ctx["org"] = get_cfg(s)["org"]
    ctx.setdefault("csrf", csrf_token(request))
    ctx.setdefault("flash", request.session.pop("flash", None))
    ctx.setdefault("me", request.session.get("user"))
    ctx.setdefault("P", root(request))
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def flash(request: Request, msg: str, kind: str = "ok") -> None:
    request.session["flash"] = {"msg": msg, "kind": kind}


def csrf_token(request: Request) -> str:
    tok = request.session.get("csrf")
    if not tok:
        tok = request.session["csrf"] = secrets.token_urlsafe(24)
    return tok


def check_csrf(request: Request, token) -> None:
    if not token or not hmac.compare_digest(str(token), request.session.get("csrf", "")):
        raise HTTPException(400, "This form expired. Go back, refresh the page and try again.")


def require(request: Request, owner: bool = False) -> dict:
    user = request.session.get("user")
    if not user:
        raise HTTPException(303, headers={"Location": "/admin/login"})
    if owner and user["role"] != "owner":
        raise HTTPException(403, "Only owners can do this.")
    return user


async def form_of(request: Request):
    form = await request.form()
    check_csrf(request, form.get("csrf"))
    return form


def hash_pw(pw: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1)
    return f"scrypt${salt.hex()}${h.hex()}"


def check_pw(pw: str, stored: str) -> bool:
    try:
        _, salt, h = stored.split("$")
        return hmac.compare_digest(hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1).hex(), h)
    except Exception:
        return False


def verify_url(doc_id: str) -> str:
    return f"{PUBLIC_URL}/verify?id={doc_id}"


def status_url(c: Candidate) -> str:
    return f"{PUBLIC_URL}/status/{c.token}"


def fmt_date(d: dt.date) -> str:
    return d.strftime("%d %B %Y").lstrip("0")


def log(s, cand: Candidate, kind: str, detail: str = "", by: str = "") -> None:
    s.add(Event(candidate_id=cand.id, kind=kind, detail=detail, by=by))


def next_doc_id(s, cfg: dict, kind: str) -> str:
    year = dt.date.today().year
    tag = {"offer": "OFF", "certificate": "CERT", "lor": "LOR"}[kind]
    prefix = f"{cfg['org']['doc_prefix']}-{tag}-{year}-"
    n = s.scalar(select(func.count()).select_from(Document).where(Document.id.like(prefix + "%")))
    return f"{prefix}{n + 1:04d}"


def linkedin_add_url(cfg: dict, d: Document) -> str:
    q = {"startTask": "CERTIFICATION_NAME", "name": f"{d.role_title} Internship",
         "organizationName": cfg["org"]["name"], "issueYear": d.issued.year, "issueMonth": d.issued.month,
         "certUrl": verify_url(d.id), "certId": d.id}
    return "https://www.linkedin.com/profile/add?" + urllib.parse.urlencode(q)


def mail_fields(cfg: dict, c: Candidate, **extra) -> dict:
    sig, org = cfg["signatory"], cfg["org"]
    return {"first_name": c.name.split()[0], "name": c.name, "role_title": c.role.title,
            "signatory_name": sig["name"], "signatory_title": sig["title"], "org_name": org["name"],
            "status_url": status_url(c), **extra}


def send_email(cfg: dict, cand_id: int, to: str, kind: str, fields: dict, attachments=(), subject: str = "",
               body: str = "") -> None:
    """Sends in a background thread (Gmail SMTP takes 1-3 s) and records the
    outcome in the applicant's history, so a failed email is never silent."""
    if kind in TEMPLATES:
        subject, _, body = TEMPLATES[kind].format(**fields).partition("\n")
    org = cfg["org"]

    def work():
        ok, detail = True, f"{kind} email sent to {to}"
        try:
            if not (SMTP_USER and SMTP_PASSWORD):
                raise RuntimeError("SMTP_USER / SMTP_PASSWORD not configured")
            msg = EmailMessage()
            msg["From"] = formataddr((f"{org['name']} Careers", SMTP_USER))
            msg["To"] = to
            msg["Reply-To"] = org["email"] or SMTP_USER
            msg["Subject"] = subject.strip()
            msg["Message-ID"] = make_msgid(domain=SMTP_USER.split("@")[-1])
            msg.set_content(body.strip() + "\n")
            for name, data, mtype in attachments:
                main, sub = mtype.split("/", 1)
                params = {"method": "REQUEST"} if sub == "calendar" else {}
                msg.add_attachment(data, maintype=main, subtype=sub, filename=name, params=params)
            with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as srv:
                srv.starttls()
                srv.login(SMTP_USER, SMTP_PASSWORD)
                srv.send_message(msg)
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, f"{kind} email to {to} FAILED: {exc}"
            print(detail)
        with db.session() as s:
            s.add(Event(candidate_id=cand_id, kind="email" if ok else "email_failed", detail=detail, by="system"))

    threading.Thread(target=work, daemon=True).start()


def _ics_escape(t: str) -> str:
    return t.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def ics_invite(cfg: dict, c: Candidate, start_ist: dt.datetime, minutes: int, where: str) -> bytes:
    start = start_ist.replace(tzinfo=IST).astimezone(dt.timezone.utc)
    end = start + dt.timedelta(minutes=minutes)
    f = "%Y%m%dT%H%M%SZ"
    org = cfg["org"]
    summary = f"{org['name']} interview: {c.name} ({c.role.title})"
    desc = f"Interview for the {c.role.title} internship. Join: {where}"
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:-//{org['name']} Careers//EN", "METHOD:REQUEST", "BEGIN:VEVENT",
        f"UID:{uuid.uuid4()}@careers", f"DTSTAMP:{dt.datetime.now(dt.timezone.utc).strftime(f)}",
        f"DTSTART:{start.strftime(f)}", f"DTEND:{end.strftime(f)}",
        f"SUMMARY:{_ics_escape(summary)}", f"DESCRIPTION:{_ics_escape(desc)}", f"LOCATION:{_ics_escape(where)}",
        f"ORGANIZER;CN={_ics_escape(cfg['signatory']['name'])}:mailto:{SMTP_USER or org['email']}",
        f"ATTENDEE;CN={_ics_escape(c.name)};RSVP=TRUE:mailto:{c.email}",
        "END:VEVENT", "END:VCALENDAR",
    ]
    return ("\r\n".join(lines) + "\r\n").encode()


def set_status(s, c: Candidate, new: str, note: str = "", force: bool = False, by: str = "") -> None:
    if new not in STATUSES:
        raise HTTPException(400, "Unknown status")
    if not force and new != c.status and new not in FLOW.get(c.status, []):
        raise HTTPException(400, f"Can't move from {STATUS_LABEL[c.status]} to {STATUS_LABEL[new]}.")
    old, c.status = c.status, new
    log(s, c, new, note or f"{STATUS_LABEL[old]} → {STATUS_LABEL[new]}", by)


def get_cand(s, cid: int) -> Candidate:
    c = s.get(Candidate, cid)
    if not c:
        raise HTTPException(404, "Applicant not found")
    return c


def pdf_name(d: Document) -> str:
    return f"{d.id}_{re.sub(r'[^A-Za-z0-9]+', '_', d.holder_name)}.pdf"


def _active_offer(c: Candidate) -> Document | None:
    return next((d for d in reversed(c.documents) if d.kind == "offer" and not d.revoked), None)


# ---------------------------------------------------------------------------
# signing (see credentials.py)
# ---------------------------------------------------------------------------

def get_signer(s) -> tuple[vc.SigningKey, str]:
    """Returns (key, source). Prefers the SIGNING_KEY env var; otherwise a key
    generated once and kept in the database. The public half is always recorded
    so it stays published after rotation."""
    seed, source = vc.seed_from_env(), "environment (CAREERS_SIGNING_KEY)"
    if seed is None:
        a = s.get(Asset, "signing_key")
        if a is None:
            a = Asset(name="signing_key", data=vc.new_seed(), mime="application/octet-stream")
            s.add(a)
            s.flush()
        seed, source = a.data, "database (auto-generated: set CAREERS_SIGNING_KEY for production)"
    key = vc.SigningKey.from_seed(seed)
    if s.get(PublicKey, key.kid) is None:
        for old in s.scalars(select(PublicKey).where(PublicKey.active.is_(True))):
            old.active = False
        s.add(PublicKey(kid=key.kid, public_hex=key.pub_raw.hex(), active=True))
        s.flush()
    return key, source


def published_keys(s) -> dict[str, bytes]:
    return {k.kid: bytes.fromhex(k.public_hex) for k in s.scalars(select(PublicKey))}


def issue_document(s, cfg: dict, c: Candidate, *, kind: str, doc_id: str, role_title: str, start: dt.date,
                   end: dt.date, pdf: bytes, meta: dict) -> Document:
    """Signs a W3C Verifiable Credential for the document, embeds it in the PDF
    and stores both. The stored sha256 is of the final (embedded) PDF."""
    key, _ = get_signer(s)
    doc = Document(id=doc_id, candidate_id=c.id, kind=kind, issued=dt.date.today(), role_title=role_title,
                   start=start, end=end, holder_name=c.name, holder_college=c.college, meta=json.dumps(meta),
                   sha256="", pdf=b"")
    cred = vc.build_credential(public_url=PUBLIC_URL, issuer_name=cfg["org"]["legal_name"], doc_id=doc_id, kind=kind,
                               kind_label=doc.kind_label, holder=c.name, college=c.college, role_title=role_title,
                               start=start, end=end, issued=doc.issued)
    doc.credential = vc.sign(cred, key, PUBLIC_URL)
    doc.pdf = vc.embed_in_pdf(pdf, doc.credential)
    doc.sha256 = hashlib.sha256(doc.pdf).hexdigest()
    s.add(doc)
    return doc


_hits: dict[str, deque] = defaultdict(deque)


def rate_limited(key: str, limit: int, per_s: int) -> bool:
    q, t = _hits[key], time.time()
    while q and t - q[0] > per_s:
        q.popleft()
    if len(q) >= limit:
        return True
    q.append(t)
    return False


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "?")


# ---------------------------------------------------------------------------
# public
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    with db.session() as s:
        cfg = get_cfg(s)
        roles = [r for r in s.scalars(select(Role).order_by(Role.sort, Role.id)) if r.accepting]
    return render(request, "index.html", org=cfg["org"], roles=roles, d=cfg["offer_defaults"])


def job_posting_ld(cfg: dict, role: Role) -> str:
    """schema.org JobPosting so the internship can show up in Google's job search."""
    d = cfg["offer_defaults"]
    data = {
        "@context": "https://schema.org/", "@type": "JobPosting", "title": role.title,
        "description": "<p>" + role.summary + "</p><ul>" + "".join(f"<li>{x}</li>" for x in role.duty_list()) + "</ul>",
        "datePosted": role.created_at.date().isoformat(), "employmentType": "INTERN",
        "hiringOrganization": {"@type": "Organization", "name": cfg["org"]["legal_name"], "sameAs": cfg["org"]["website"],
                               "logo": f"{PUBLIC_URL}/static/apple-touch-icon.png"},
        "directApply": True,
    }
    if role.closes_on:
        data["validThrough"] = role.closes_on.isoformat() + "T23:59:59+05:30"
    if str(d["mode"]).lower().startswith("remote"):
        data["jobLocationType"] = "TELECOMMUTE"
        data["applicantLocationRequirements"] = {"@type": "Country", "name": "India"}
    else:
        data["jobLocation"] = {"@type": "Place", "address": {"@type": "PostalAddress",
                                                             "addressLocality": cfg["org"]["address"],
                                                             "addressCountry": "IN"}}
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


@app.get("/roles/{key}", response_class=HTMLResponse)
def role_page(request: Request, key: str):
    with db.session() as s:
        cfg = get_cfg(s)
        role = s.scalar(select(Role).where(Role.key == key))
        if not role:
            raise HTTPException(404, "Role not found")
    return render(request, "role.html", org=cfg["org"], role=role, d=cfg["offer_defaults"], form={}, errors={},
                  ld=job_posting_ld(cfg, role))


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
URL_RE = re.compile(r"^https?://\S+$", re.I)
FIELD_MAX = {"name": 120, "email": 200, "phone": 40, "city": 80, "college": 200, "degree": 120, "grad_year": 10,
             "cgpa": 20, "hours": 20, "skills": 2000, "why": 3000, "answer": 3000, "links": 1000, "resume": 1000,
             "source": 80}


@app.post("/roles/{key}/apply", response_class=HTMLResponse)
async def apply(request: Request, key: str):
    raw = await request.form()
    upload = raw.get("resume_file")
    form = {k: v.strip() for k, v in raw.items() if isinstance(v, str)}
    with db.session() as s:
        cfg = get_cfg(s)
        role = s.scalar(select(Role).where(Role.key == key))
        if not role or not role.accepting:
            raise HTTPException(404, "This role is not accepting applications.")
        if form.get("website"):  # honeypot
            return go(request, "/thanks")
        errors = {}
        # Keep the form short: only what's needed to judge an application is
        # required; everything else lives under "More about you (optional)".
        required = {"name": "your name", "email": "your email", "phone": "your phone number", "college": "your college",
                    "degree": "your degree and year"}
        if role.question:
            required["answer"] = "an answer"
        else:
            required["why"] = "why you'd like to join"
        for f, label in required.items():
            if not form.get(f):
                errors[f] = f"Please enter {label}."
        if form.get("email") and not EMAIL_RE.match(form["email"]):
            errors["email"] = "That doesn't look like an email address."
        for f in ("resume", "links"):
            if form.get(f) and not URL_RE.match(form[f]):
                errors[f] = "Paste a full link starting with https://"
        file_bytes, file_name = None, ""
        if isinstance(upload, UploadFile) and upload.filename:
            file_bytes = await upload.read(MAX_RESUME + 1)
            file_name = re.sub(r"[^\w.\- ]+", "_", upload.filename)[:150]
            if len(file_bytes) > MAX_RESUME:
                errors["resume_file"] = "Resume must be under 2 MB."
            elif not file_bytes.startswith(b"%PDF"):
                errors["resume_file"] = "Please upload your resume as a PDF."
            if errors.get("resume_file"):
                file_bytes = None
        if not form.get("resume") and not file_bytes and not errors.get("resume_file"):
            errors["resume_file"] = "Upload your resume (PDF) or paste a link to it."
        available_from = None
        if form.get("available_from"):
            try:
                available_from = dt.date.fromisoformat(form["available_from"])
            except ValueError:
                errors["available_from"] = "Pick a valid date."
        if not form.get("consent"):
            errors["consent"] = "Please agree so we can process your application."
        for f, n in FIELD_MAX.items():
            form[f] = form.get(f, "")[:n]
        if not errors and s.scalar(select(Candidate).where(func.lower(Candidate.email) == form["email"].lower(),
                                                           Candidate.role_id == role.id)):
            errors["email"] = "You've already applied for this role with this email. Check your inbox for your status link."
        if errors:
            return render(request, "role.html", status_code=422, org=cfg["org"], role=role, d=cfg["offer_defaults"],
                          form=form, errors=errors, ld=job_posting_ld(cfg, role))
        if rate_limited("apply:" + client_ip(request), 6, 3600):
            raise HTTPException(429, "Too many applications from this network. Please try again later.")
        c = Candidate(role_id=role.id, available_from=available_from, resume_file=file_bytes, resume_name=file_name,
                      **{k: form[k] for k in FIELD_MAX})
        c.role = role
        c.score, c.score_notes = score(c, role, int(cfg["offer_defaults"]["hours_per_week"]))
        s.add(c)
        s.flush()
        log(s, c, "applied", "via careers site", "applicant")
        cid, email, fields, token = c.id, c.email, mail_fields(cfg, c), c.token
    send_email(cfg, cid, email, "received", fields)
    return go(request, f"/thanks?t={token}")


@app.get("/thanks", response_class=HTMLResponse)
def thanks(request: Request, t: str = ""):
    return render(request, "thanks.html", token=t)


@app.get("/status/{token}", response_class=HTMLResponse)
def status_page(request: Request, token: str):
    with db.session() as s:
        cfg = get_cfg(s)
        c = s.scalar(select(Candidate).where(Candidate.token == token))
        if not c:
            raise HTTPException(404, "This status link isn't valid.")
        docs = [d for d in c.documents if not d.revoked and d.sent_at]
    stage = PUBLIC_STAGE_OF.get(c.status)
    order = [k for k, _ in PUBLIC_STAGES]
    return render(request, "status.html", org=cfg["org"], c=c, stages=PUBLIC_STAGES,
                  current=order.index(stage) if stage else -1, docs=docs,
                  can_withdraw=c.status in ("applied", "shortlisted", "interview", "selected"))


@app.get("/status/{token}/doc/{doc_id}.pdf")
def status_doc(token: str, doc_id: str):
    with db.session() as s:
        c = s.scalar(select(Candidate).where(Candidate.token == token))
        d = s.get(Document, doc_id)
        if not c or not d or d.candidate_id != c.id or d.revoked or not d.sent_at:
            raise HTTPException(404, "Document not found")
        return Response(d.pdf, media_type="application/pdf",
                        headers={"Content-Disposition": f'inline; filename="{pdf_name(d)}"'})


@app.post("/status/{token}/withdraw")
async def withdraw(request: Request, token: str):
    await form_of(request)
    with db.session() as s:
        c = s.scalar(select(Candidate).where(Candidate.token == token))
        if not c:
            raise HTTPException(404)
        if c.status in ("applied", "shortlisted", "interview", "selected"):
            set_status(s, c, "withdrawn", "withdrawn by applicant", force=True, by="applicant")
    flash(request, "Your application has been withdrawn. Thank you for your interest.")
    return go(request, f"/status/{token}")


@app.get("/verify", response_class=HTMLResponse)
def verify(request: Request, id: str = ""):
    doc_id = id.strip().upper()[:40]
    with db.session() as s:
        cfg = get_cfg(s)
        doc = s.get(Document, doc_id) if doc_id else None
        sig = vc.verify(doc.credential, published_keys(s)) if doc and doc.credential else None
    li = linkedin_add_url(cfg, doc) if doc and doc.kind == "certificate" and not doc.revoked else ""
    return render(request, "verify.html", org=cfg["org"], doc_id=doc_id, doc=doc, linkedin=li, sig=sig,
                  upload=None)


@app.post("/verify/upload", response_class=HTMLResponse)
async def verify_upload(request: Request):
    """Check a PDF someone was given: (1) is this exact file one we issued
    (hash match), and (2) does it carry a valid signed credential?"""
    form = await request.form()
    up = form.get("pdf")
    if not isinstance(up, UploadFile) or not up.filename:
        raise HTTPException(400, "Choose a PDF to check.")
    if rate_limited("verify:" + client_ip(request), 30, 600):
        raise HTTPException(429, "Too many checks. Try again in a few minutes.")
    data = await up.read(10 * 1024 * 1024 + 1)
    if len(data) > 10 * 1024 * 1024 or not data.startswith(b"%PDF"):
        raise HTTPException(400, "That isn't a PDF (or it's larger than 10 MB).")
    digest = hashlib.sha256(data).hexdigest()
    token = vc.extract_from_pdf(data)
    with db.session() as s:
        cfg = get_cfg(s)
        exact = s.scalar(select(Document).where(Document.sha256 == digest))
        sig = vc.verify(token, published_keys(s)) if token else None
        claimed = None
        if sig and sig.credential:
            claimed = s.get(Document, str(sig.credential.get("credentialSubject", {}).get("documentNumber", "")))
    doc = exact or (claimed if sig and sig.valid else None)
    upload = {"filename": up.filename, "exact": bool(exact), "has_credential": bool(token), "digest": digest}
    li = linkedin_add_url(cfg, doc) if doc and doc.kind == "certificate" and not doc.revoked else ""
    return render(request, "verify.html", org=cfg["org"], doc_id=doc.id if doc else "", doc=doc, linkedin=li,
                  sig=sig, upload=upload)


@app.get("/did.json")
@app.get("/.well-known/did.json")
def did_json():
    with db.session() as s:
        get_signer(s)  # make sure the current key is published
        keys = sorted(published_keys(s).items())
    return Response(json.dumps(vc.did_document(PUBLIC_URL, keys), indent=2), media_type="application/did+json",
                    headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "public, max-age=300"})


@app.get("/api/credentials/{doc_id}")
def credential_jwt(doc_id: str):
    with db.session() as s:
        d = s.get(Document, doc_id.upper())
        if not d or not d.credential:
            raise HTTPException(404, "No such credential")
        return Response(d.credential, media_type="application/vc+jwt",
                        headers={"Access-Control-Allow-Origin": "*",
                                 "Content-Disposition": f'inline; filename="{d.id}.jwt"'})


@app.get("/api/credentials/{doc_id}/status")
def credential_status(doc_id: str):
    with db.session() as s:
        d = s.get(Document, doc_id.upper())
        if not d:
            raise HTTPException(404, "No such credential")
        body = {"id": d.id, "revoked": d.revoked, "checked": dt.datetime.now(dt.timezone.utc).isoformat()}
    return Response(json.dumps(body), media_type="application/json", headers={"Access-Control-Allow-Origin": "*"})


@app.get("/healthz", response_class=PlainTextResponse)
def healthz():
    return "ok"


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots():
    return f"User-agent: *\nDisallow: /admin\nDisallow: /status\nSitemap: {PUBLIC_URL}/sitemap.xml\n"


@app.get("/sitemap.xml")
def sitemap():
    with db.session() as s:
        roles = [r for r in s.scalars(select(Role)) if r.accepting]
    urls = [PUBLIC_URL + "/"] + [f"{PUBLIC_URL}/roles/{r.key}" for r in roles]
    xml = ('<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
           + "".join(f"<url><loc>{u}</loc></url>" for u in urls) + "</urlset>")
    return Response(xml, media_type="application/xml")


# ---------------------------------------------------------------------------
# team auth
# ---------------------------------------------------------------------------

@app.get("/admin/login", response_class=HTMLResponse)
def login_page(request: Request):
    return render(request, "admin/login.html")


@app.post("/admin/login")
async def login(request: Request):
    f = await form_of(request)
    email, pw = (f.get("email") or "").strip().lower(), f.get("password") or ""
    if rate_limited("login:" + client_ip(request), 8, 900):
        raise HTTPException(429, "Too many attempts. Wait 15 minutes.")
    user = None
    if OWNER_EMAIL and OWNER_PASSWORD and email == OWNER_EMAIL and hmac.compare_digest(
            hashlib.sha256(pw.encode()).digest(), hashlib.sha256(OWNER_PASSWORD.encode()).digest()):
        user = {"email": email, "name": TOML["signatory"]["name"], "role": "owner"}
    else:
        with db.session() as s:
            u = s.scalar(select(User).where(func.lower(User.email) == email, User.active.is_(True)))
            if u and check_pw(pw, u.pw_hash):
                u.last_login = db.now()
                user = {"email": u.email, "name": u.name, "role": u.role}
    if not user:
        flash(request, "Wrong email or password.", "error")
        return go(request, "/admin/login")
    request.session.clear()
    request.session["user"] = user
    return go(request, "/admin")


@app.post("/admin/logout")
async def logout(request: Request):
    await form_of(request)
    request.session.clear()
    return go(request, "/admin/login")


# ---------------------------------------------------------------------------
# board
# ---------------------------------------------------------------------------

@app.get("/admin", response_class=HTMLResponse)
def board(request: Request, status: str = "", role: str = "", q: str = ""):
    require(request)
    today = dt.date.today()
    with db.session() as s:
        cfg = get_cfg(s)
        roles = s.scalars(select(Role).order_by(Role.sort, Role.id)).all()
        counts = dict(s.execute(select(Candidate.status, func.count()).group_by(Candidate.status)).all())
        stmt = select(Candidate)
        if status:
            stmt = stmt.where(Candidate.status == status)
        elif not q:
            stmt = stmt.where(Candidate.status.not_in(["rejected", "withdrawn", "declined", "completed"]))
        if role:
            stmt = stmt.join(Role).where(Role.key == role)
        if q:
            like = f"%{q.lower()}%"
            stmt = stmt.where(or_(func.lower(Candidate.name).like(like), func.lower(Candidate.email).like(like),
                                  func.lower(Candidate.college).like(like)))
        cands = s.scalars(stmt.order_by(Candidate.score.desc(), Candidate.id)).unique().all()
        avg = {cid: round(float(a), 1) for cid, a in s.execute(
            select(Scorecard.candidate_id,
                   func.avg((Scorecard.skills + Scorecard.communication + Scorecard.ownership) / 3.0))
            .group_by(Scorecard.candidate_id)).all()}
        joined = s.scalars(select(Candidate).where(Candidate.status == "joined")).unique().all()
        cert_due = [c for c in joined if (o := _active_offer(c)) and o.end <= today]
        unsent = s.scalars(select(Document).where(Document.sent_at.is_(None), Document.revoked.is_(False))).all()
        now_ist = dt.datetime.now(IST).replace(tzinfo=None)
        upcoming = [c for c in s.scalars(select(Candidate).where(Candidate.interview_at.is_not(None),
                                                                  Candidate.status == "interview")
                                         .order_by(Candidate.interview_at)).unique()
                    if c.interview_at >= now_ist - dt.timedelta(hours=1)][:5]
        new_count = s.scalar(select(func.count()).select_from(Candidate).where(
            Candidate.status == "applied", Candidate.created_at >= db.now() - dt.timedelta(days=7)))
    return render(request, "admin/board.html", org=cfg["org"], cands=cands, roles=roles, counts=counts,
                  total=sum(counts.values()), f_status=status, f_role=role, q=q, avg=avg, cert_due=cert_due,
                  unsent=unsent, upcoming=upcoming, new_count=new_count, active="board")


@app.post("/admin/bulk")
async def bulk(request: Request):
    user = require(request)
    f = await form_of(request)
    ids = [int(x) for x in f.getlist("ids") if str(x).isdigit()]
    new = f.get("status", "")
    send_rejection = f.get("send_rejection") == "1"
    if user["role"] != "owner" and new != "shortlisted":
        raise HTTPException(403, "Reviewers can only shortlist.")
    if not ids or new not in STATUSES:
        flash(request, "Select applicants and an action first.", "error")
        return back(request, "/admin")
    moved, skipped, emails = 0, 0, []
    with db.session() as s:
        cfg = get_cfg(s)
        for c in s.scalars(select(Candidate).where(Candidate.id.in_(ids))).unique():
            ok = new in FLOW.get(c.status, []) or (
                new == "rejected" and c.status in ("applied", "shortlisted", "interview", "selected"))
            if ok:
                set_status(s, c, new, "bulk action", force=True, by=user["name"])
                moved += 1
                if new == "rejected" and send_rejection:
                    emails.append((c.id, c.email, mail_fields(cfg, c)))
            else:
                skipped += 1
    for cid, to, fields in emails:
        send_email(cfg, cid, to, "rejection", fields)
    flash(request, f"{moved} applicant(s) → {STATUS_LABEL[new]}"
                   + (f"; {skipped} skipped (not allowed from their stage)" if skipped else "")
                   + (f"; {len(emails)} rejection email(s) sending." if emails else "."))
    return back(request, "/admin")


@app.get("/admin/export.csv")
def export_csv(request: Request):
    require(request, owner=True)
    buf = io.StringIO()
    w = csv.writer(buf)
    cols = ["id", "status", "score", "name", "email", "phone", "city", "college", "degree", "grad_year", "cgpa",
            "hours", "available_from", "skills", "links", "resume", "why", "answer", "source", "created_at"]
    w.writerow(["role"] + cols)
    with db.session() as s:
        for c in s.scalars(select(Candidate).order_by(Candidate.id)).unique():
            w.writerow([c.role.title] + [getattr(c, k) for k in cols])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="applicants-{dt.date.today()}.csv"'})


# ---------------------------------------------------------------------------
# one applicant
# ---------------------------------------------------------------------------

@app.get("/admin/c/{cid}", response_class=HTMLResponse)
def candidate_page(request: Request, cid: int):
    require(request)
    with db.session() as s:
        cfg = get_cfg(s)
        c = get_cand(s, cid)
        roles = s.scalars(select(Role).order_by(Role.sort, Role.id)).all()
        _ = c.events, c.documents, c.scorecards
        offer = _active_offer(c)
        others = s.scalars(select(Candidate).where(func.lower(Candidate.email) == c.email.lower(),
                                                   Candidate.id != c.id)).unique().all()
    today = dt.date.today()
    sc_avg = round(sum(x.average for x in c.scorecards) / len(c.scorecards), 1) if c.scorecards else None
    return render(request, "admin/candidate.html", org=cfg["org"], c=c, roles=roles, allowed=FLOW.get(c.status, []),
                  d=cfg["offer_defaults"], offer=offer, today=today, others=others, sc_avg=sc_avg,
                  default_start=(today + dt.timedelta(days=14)).isoformat(),
                  default_end=(today + dt.timedelta(days=14 + 90)).isoformat(), verify_url=verify_url,
                  status_link=status_url(c), active="board")


@app.get("/admin/c/{cid}/resume")
def resume_file(request: Request, cid: int):
    require(request)
    with db.session() as s:
        c = get_cand(s, cid)
        if not c.resume_file:
            raise HTTPException(404, "No uploaded resume")
        return Response(c.resume_file, media_type="application/pdf",
                        headers={"Content-Disposition": f'inline; filename="{c.resume_name or "resume.pdf"}"'})


@app.post("/admin/c/{cid}/move")
async def move(request: Request, cid: int):
    user = require(request)
    f = await form_of(request)
    new = f.get("status", "")
    if user["role"] != "owner" and new != "shortlisted":
        raise HTTPException(403, "Reviewers can only shortlist.")
    with db.session() as s:
        cfg = get_cfg(s)
        c = get_cand(s, cid)
        set_status(s, c, new, by=user["name"])
        mail = (c.id, c.email, mail_fields(cfg, c)) if new == "rejected" and f.get("send_rejection") == "1" else None
    if mail:
        send_email(cfg, mail[0], mail[1], "rejection", mail[2])
    flash(request, f"Moved to {STATUS_LABEL[new]}." + (" Rejection email sending." if mail else ""))
    return go(request, f"/admin/c/{cid}")


@app.post("/admin/c/{cid}/note")
async def note(request: Request, cid: int):
    user = require(request)
    f = await form_of(request)
    text = (f.get("text") or "").strip()
    if text:
        with db.session() as s:
            log(s, get_cand(s, cid), "note", text[:2000], user["name"])
    return go(request, f"/admin/c/{cid}#history")


@app.post("/admin/c/{cid}/scorecard")
async def scorecard(request: Request, cid: int):
    user = require(request)
    f = await form_of(request)
    try:
        vals = {k: int(f.get(k, 0)) for k in ("skills", "communication", "ownership")}
    except ValueError:
        raise HTTPException(400, "Ratings must be 1-5")
    if not all(1 <= v <= 5 for v in vals.values()) or f.get("recommendation") not in RECO:
        raise HTTPException(400, "Fill every rating (1-5) and a recommendation.")
    with db.session() as s:
        c = get_cand(s, cid)
        for old in list(c.scorecards):
            if old.reviewer == user["name"]:
                c.scorecards.remove(old)  # one scorecard per reviewer; latest wins
        c.scorecards.append(Scorecard(reviewer=user["name"], recommendation=f["recommendation"],
                                      notes=(f.get("notes") or "").strip()[:3000], **vals))
        log(s, c, "scorecard", f"{RECO[f['recommendation']]} ({sum(vals.values()) / 3:.1f}/5)", user["name"])
    flash(request, "Scorecard saved.")
    return go(request, f"/admin/c/{cid}#scorecards")


@app.post("/admin/c/{cid}/interview")
async def interview(request: Request, cid: int):
    user = require(request, owner=True)
    f = await form_of(request)
    try:
        when = dt.datetime.fromisoformat(f.get("when", ""))
        minutes = max(10, min(180, int(f.get("duration") or 30)))
    except ValueError:
        raise HTTPException(400, "Pick a date and time for the interview.")
    where = (f.get("where") or "").strip()[:500]
    if not where:
        raise HTTPException(400, "Add the meeting link or place.")
    with db.session() as s:
        cfg = get_cfg(s)
        c = get_cand(s, cid)
        c.interview_at, c.interview_where = when, where
        if c.status in ("applied", "shortlisted"):
            set_status(s, c, "interview", f"{when:%d %b %Y %I:%M %p} IST · {where}", force=True, by=user["name"])
        else:
            log(s, c, "interview", f"rescheduled: {when:%d %b %Y %I:%M %p} IST · {where}", user["name"])
        fields = mail_fields(cfg, c, when=when.strftime("%A, %d %B %Y at %I:%M %p IST"), where=where, duration=minutes)
        ics = ics_invite(cfg, c, when, minutes, where)
        target = (c.id, c.email)
    send_email(cfg, target[0], target[1], "interview", fields, attachments=[("invite.ics", ics, "text/calendar")])
    flash(request, "Interview invite (with calendar file) sending.")
    return go(request, f"/admin/c/{cid}")


@app.post("/admin/c/{cid}/offer")
async def make_offer(request: Request, cid: int):
    user = require(request, owner=True)
    f = await form_of(request)
    from .documents import offer_letter

    try:
        d_start, d_end = dt.date.fromisoformat(f.get("start", "")), dt.date.fromisoformat(f.get("end", ""))
    except ValueError:
        raise HTTPException(400, "Dates must be valid.")
    if d_end <= d_start:
        raise HTTPException(400, "End date must be after the start date.")
    with db.session() as s:
        cfg = get_cfg(s)
        d = cfg["offer_defaults"]
        c = get_cand(s, cid)
        if c.status not in ("selected", "offered"):
            raise HTTPException(400, "Move the applicant to Selected first.")
        role = s.get(Role, int(f.get("role_id") or c.role_id)) or c.role
        hours = int(f.get("hours") or 0) or d["hours_per_week"]
        offer = {"role_title": role.title, "start": d_start.isoformat(), "end": d_end.isoformat(),
                 "mode": (f.get("mode") or "").strip() or d["mode"], "hours_per_week": hours,
                 "stipend": (f.get("stipend") or "").strip() or d["stipend"],
                 "reporting_to": (f.get("reporting_to") or "").strip() or d["reporting_to"],
                 "duties": role.duty_list(), "notice_days": d["notice_days"]}
        doc_id = next_doc_id(s, cfg, "offer")
        buf = io.BytesIO()
        offer_letter(buf, cfg=cfg, cand={"name": c.name, "college": c.college}, offer=offer, doc_id=doc_id,
                     verify_url=verify_url(doc_id))
        pdf = buf.getvalue()
        for old in c.documents:
            if old.kind == "offer" and not old.revoked:
                old.revoked = True
                log(s, c, "revoked", f"{old.id} (replaced by {doc_id})", user["name"])
        issue_document(s, cfg, c, kind="offer", doc_id=doc_id, role_title=role.title, start=d_start, end=d_end,
                       pdf=pdf, meta=offer)
        if c.status == "selected":
            set_status(s, c, "offered", doc_id, by=user["name"])
        else:
            log(s, c, "offer", f"{doc_id} issued", user["name"])
    flash(request, f"Offer letter {doc_id} created. Preview it, then click Send.")
    return go(request, f"/admin/c/{cid}#documents")


@app.post("/admin/c/{cid}/certificate")
async def make_certificate(request: Request, cid: int):
    user = require(request, owner=True)
    f = await form_of(request)
    from .documents import certificate

    with db.session() as s:
        cfg = get_cfg(s)
        c = get_cand(s, cid)
        if c.status not in ("joined", "completed"):
            raise HTTPException(400, "Certificates are for interns who joined.")
        offer = _active_offer(c)
        if not offer:
            raise HTTPException(400, "No offer letter on record for this intern.")
        d_end = dt.date.fromisoformat(f["end"]) if f.get("end") else offer.end
        if d_end > dt.date.today():
            raise HTTPException(400, f"The internship ends on {fmt_date(d_end)}. Issue the certificate after it "
                                     f"ends, or enter the actual last day.")
        doc_id = next_doc_id(s, cfg, "certificate")
        data = {"role_title": offer.role_title, "start": offer.start.isoformat(), "end": d_end.isoformat(),
                "issued": dt.date.today().isoformat(), "highlight": (f.get("highlight") or "").strip()[:240]}
        buf = io.BytesIO()
        certificate(buf, cfg=cfg, cand={"name": c.name, "college": c.college}, cert=data, doc_id=doc_id,
                    verify_url=verify_url(doc_id))
        pdf = buf.getvalue()
        issue_document(s, cfg, c, kind="certificate", doc_id=doc_id, role_title=offer.role_title, start=offer.start,
                       end=d_end, pdf=pdf, meta=data)
        if c.status == "joined":
            set_status(s, c, "completed", doc_id, by=user["name"])
        else:
            log(s, c, "certificate", f"{doc_id} issued", user["name"])
    flash(request, f"Certificate {doc_id} created. Preview it, then click Send.")
    return go(request, f"/admin/c/{cid}#documents")


@app.post("/admin/c/{cid}/lor")
async def make_lor(request: Request, cid: int):
    user = require(request, owner=True)
    f = await form_of(request)
    from .documents import recommendation_letter

    body = (f.get("body") or "").strip()
    if len(body) < 80:
        raise HTTPException(400, "Write at least a few sentences about their work.")
    with db.session() as s:
        cfg = get_cfg(s)
        c = get_cand(s, cid)
        offer = _active_offer(c)
        if c.status not in ("joined", "completed") or not offer:
            raise HTTPException(400, "Recommendation letters are for interns.")
        end = min(offer.end, dt.date.today())
        doc_id = next_doc_id(s, cfg, "lor")
        rec = {"role_title": offer.role_title, "start": offer.start.isoformat(), "end": end.isoformat(),
               "body": body[:4000]}
        buf = io.BytesIO()
        recommendation_letter(buf, cfg=cfg, cand={"name": c.name, "college": c.college}, rec=rec, doc_id=doc_id,
                              verify_url=verify_url(doc_id))
        pdf = buf.getvalue()
        issue_document(s, cfg, c, kind="lor", doc_id=doc_id, role_title=offer.role_title, start=offer.start, end=end,
                       pdf=pdf, meta=rec)
        log(s, c, "lor", f"{doc_id} issued", user["name"])
    flash(request, f"Letter of recommendation {doc_id} created. Preview it, then click Send.")
    return go(request, f"/admin/c/{cid}#documents")


@app.get("/admin/doc/{doc_id}.pdf")
def doc_pdf(request: Request, doc_id: str):
    require(request)
    with db.session() as s:
        d = s.get(Document, doc_id)
        if not d:
            raise HTTPException(404)
        return Response(d.pdf, media_type="application/pdf",
                        headers={"Content-Disposition": f'inline; filename="{pdf_name(d)}"'})


@app.post("/admin/doc/{doc_id}/send")
async def doc_send(request: Request, doc_id: str):
    user = require(request, owner=True)
    await form_of(request)
    with db.session() as s:
        cfg = get_cfg(s)
        d = s.get(Document, doc_id)
        if not d or d.revoked:
            raise HTTPException(400, "Document not found or revoked")
        c = get_cand(s, d.candidate_id)
        fields = mail_fields(cfg, c, doc_id=d.id, verify_url=verify_url(d.id), start=fmt_date(d.start),
                             end=fmt_date(d.end), linkedin_url=linkedin_add_url(cfg, d))
        fields["role_title"] = d.role_title
        d.sent_at = db.now()
        log(s, c, "sent", f"{d.id} ({d.kind_label})", user["name"])
        job = (c.id, c.email, d.kind, fields, [(pdf_name(d), d.pdf, "application/pdf")])
    send_email(cfg, *job[:4], attachments=job[4])
    flash(request, f"Sending {doc_id} to {job[1]}. The result appears in History within a few seconds.")
    return go(request, f"/admin/c/{job[0]}#history")


@app.post("/admin/doc/{doc_id}/revoke")
async def doc_revoke(request: Request, doc_id: str):
    user = require(request, owner=True)
    await form_of(request)
    with db.session() as s:
        d = s.get(Document, doc_id)
        if not d:
            raise HTTPException(404)
        d.revoked = True
        c = get_cand(s, d.candidate_id)
        log(s, c, "revoked", doc_id, user["name"])
        cid = c.id
    flash(request, f"{doc_id} revoked. The verify page now shows it as invalid.")
    return go(request, f"/admin/c/{cid}#documents")


@app.post("/admin/c/{cid}/delete")
async def delete_candidate(request: Request, cid: int):
    require(request, owner=True)
    f = await form_of(request)
    with db.session() as s:
        c = get_cand(s, cid)
        if c.documents:
            raise HTTPException(400, "This person has issued documents; revoke them instead of deleting.")
        if (f.get("confirm") or "").strip().lower() != c.email.lower():
            flash(request, "Type the applicant's email exactly to confirm deletion.", "error")
            return go(request, f"/admin/c/{cid}")
        s.delete(c)
    flash(request, "Applicant data deleted.")
    return go(request, "/admin")


# ---------------------------------------------------------------------------
# analytics
# ---------------------------------------------------------------------------

@app.get("/admin/analytics", response_class=HTMLResponse)
def analytics(request: Request):
    require(request)
    with db.session() as s:
        cfg = get_cfg(s)
        cands = s.scalars(select(Candidate)).unique().all()
        for c in cands:
            _ = c.events
        roles = s.scalars(select(Role).order_by(Role.sort, Role.id)).all()
    order = ["applied", "shortlisted", "interview", "selected", "offered", "joined"]
    reached = dict.fromkeys(order, 0)
    for c in cands:
        hist = {e.kind for e in c.events} | {c.status}
        top = max((order.index(k) for k in hist if k in order), default=0)
        if c.status == "completed" or "completed" in hist:
            top = len(order) - 1
        for k in order[: top + 1]:
            reached[k] += 1
    by_role = []
    for r in roles:
        rc = [c for c in cands if c.role_id == r.id]
        by_role.append((r, len(rc), sum(c.status in ("offered", "joined", "completed") for c in rc)))
    colleges = Counter(c.college.strip() for c in cands if c.college.strip()).most_common(10)
    sources = Counter((c.source or "Not specified").strip() for c in cands).most_common(8)
    today = dt.date.today()
    weeks = []
    for i in range(11, -1, -1):
        start = today - dt.timedelta(days=today.weekday() + 7 * i)
        n = sum(start <= c.created_at.date() < start + dt.timedelta(days=7) for c in cands)
        weeks.append((start, n))
    return render(request, "admin/analytics.html", org=cfg["org"], total=len(cands), reached=reached, by_role=by_role,
                  colleges=colleges, sources=sources, weeks=weeks, max_week=max([n for _, n in weeks] + [1]),
                  active="analytics")


# ---------------------------------------------------------------------------
# broadcast
# ---------------------------------------------------------------------------

@app.get("/admin/broadcast", response_class=HTMLResponse)
def broadcast_page(request: Request):
    require(request, owner=True)
    with db.session() as s:
        cfg = get_cfg(s)
        roles = s.scalars(select(Role).order_by(Role.sort, Role.id)).all()
        counts = dict(s.execute(select(Candidate.status, func.count()).group_by(Candidate.status)).all())
    return render(request, "admin/broadcast.html", org=cfg["org"], roles=roles, counts=counts, active="broadcast")


@app.post("/admin/broadcast")
async def broadcast_send(request: Request):
    user = require(request, owner=True)
    f = await form_of(request)
    statuses = [x for x in f.getlist("statuses") if x in STATUSES]
    role_key = f.get("role", "")
    subject, body = (f.get("subject") or "").strip(), (f.get("body") or "").strip()
    if not statuses or not subject or not body:
        flash(request, "Choose at least one group and write a subject and message.", "error")
        return go(request, "/admin/broadcast")
    with db.session() as s:
        cfg = get_cfg(s)
        stmt = select(Candidate).where(Candidate.status.in_(statuses))
        if role_key:
            stmt = stmt.join(Role).where(Role.key == role_key)
        jobs = []
        for c in s.scalars(stmt).unique():
            fields = mail_fields(cfg, c)
            try:
                personal = body.format(**fields)
            except (KeyError, IndexError, ValueError):
                personal = body
            jobs.append((c.id, c.email, personal))
            log(s, c, "broadcast", subject[:200], user["name"])
    for cid, to, personal in jobs:
        send_email(cfg, cid, to, "broadcast", {}, subject=subject, body=personal)
    flash(request, f"Sending to {len(jobs)} people. Each person's History shows whether it was delivered.")
    return go(request, "/admin/broadcast")


# ---------------------------------------------------------------------------
# roles
# ---------------------------------------------------------------------------

@app.get("/admin/roles", response_class=HTMLResponse)
def roles_page(request: Request):
    require(request)
    with db.session() as s:
        cfg = get_cfg(s)
        roles = s.scalars(select(Role).order_by(Role.sort, Role.id)).all()
        counts = dict(s.execute(select(Candidate.role_id, func.count()).group_by(Candidate.role_id)).all())
    return render(request, "admin/roles.html", org=cfg["org"], roles=roles, counts=counts, active="roles",
                  missing=[r["title"] for r in db.missing_roles(TOML.get("roles", []))],
                  plan=db.plan_changes(TOML.get("roles", [])))


@app.post("/admin/roles/apply-plan")
async def roles_apply_plan(request: Request):
    """Open/close roles to match config.toml's plan (e.g. the first hiring round)."""
    require(request, owner=True)
    await form_of(request)
    n = db.apply_plan(TOML.get("roles", []))
    flash(request, f"Updated {n} role(s) to match the opening plan." if n else "Roles already match the opening plan.")
    return go(request, "/admin/roles")


@app.post("/admin/roles/sync")
async def roles_sync(request: Request):
    """Add starter roles from config.toml that this database doesn't have yet. Existing roles are untouched."""
    require(request, owner=True)
    await form_of(request)
    added = db.add_missing_roles(TOML.get("roles", []))
    flash(request, f"Added {len(added)} role(s): " + ", ".join(added) + ". New ones that were created closed can be opened below."
          if added else "Nothing to add: every starter role already exists.")
    return go(request, "/admin/roles")


@app.post("/admin/roles")
async def roles_save(request: Request):
    require(request, owner=True)
    f = await form_of(request)
    key = re.sub(r"[^a-z0-9-]+", "-", (f.get("key") or "").lower()).strip("-")[:40]
    if not key or not (f.get("title") or "").strip():
        flash(request, "Key and title are required.", "error")
        return go(request, "/admin/roles")
    with db.session() as s:
        role = s.get(Role, int(f["id"])) if (f.get("id") or "").isdigit() else None
        if role is None:
            if s.scalar(select(Role).where(Role.key == key)):
                flash(request, f"A role with key '{key}' already exists.", "error")
                return go(request, "/admin/roles")
            role = Role(key=key, sort=99)
            s.add(role)
        role.title = f.get("title", "").strip()[:120]
        for k in ("summary", "duties", "looking_for", "keywords", "question", "projects"):
            setattr(role, k, (f.get(k) or "").strip())
        openings = str(f.get("openings") or "0").strip()
        role.openings = int(openings) if openings.isdigit() else 0
        try:
            role.closes_on = dt.date.fromisoformat(f["closes_on"]) if f.get("closes_on") else None
        except ValueError:
            role.closes_on = None
        role.is_open = f.get("is_open") == "1"
    flash(request, f"Saved role '{key}'.")
    return go(request, "/admin/roles")


# ---------------------------------------------------------------------------
# team & settings (owner only)
# ---------------------------------------------------------------------------

@app.get("/admin/team", response_class=HTMLResponse)
def team_page(request: Request):
    require(request, owner=True)
    with db.session() as s:
        cfg = get_cfg(s)
        users = s.scalars(select(User).order_by(User.id)).all()
    return render(request, "admin/team.html", org=cfg["org"], users=users, owner_email=OWNER_EMAIL, active="team")


@app.post("/admin/team")
async def team_save(request: Request):
    require(request, owner=True)
    f = await form_of(request)
    action = f.get("action", "add")
    with db.session() as s:
        if action == "add":
            email = (f.get("email") or "").strip().lower()
            pw = f.get("password") or ""
            if not EMAIL_RE.match(email) or len(pw) < 10 or not (f.get("name") or "").strip():
                flash(request, "Name, a valid email and a password of at least 10 characters are required.", "error")
                return go(request, "/admin/team")
            if s.scalar(select(User).where(func.lower(User.email) == email)) or email == OWNER_EMAIL:
                flash(request, "That email is already on the team.", "error")
                return go(request, "/admin/team")
            s.add(User(email=email, name=f["name"].strip()[:120], pw_hash=hash_pw(pw),
                       role="owner" if f.get("role") == "owner" else "reviewer"))
            msg = f"Added {email}. Share the password with them privately."
        else:
            u = s.get(User, int(f.get("id") or 0))
            if not u:
                raise HTTPException(404)
            if action == "toggle":
                u.active = not u.active
                msg = f"{u.email} {'re-activated' if u.active else 'deactivated'}."
            elif action == "reset":
                pw = f.get("password") or ""
                if len(pw) < 10:
                    flash(request, "New password must be at least 10 characters.", "error")
                    return go(request, "/admin/team")
                u.pw_hash = hash_pw(pw)
                msg = f"Password reset for {u.email}."
            else:
                raise HTTPException(400)
    flash(request, msg)
    return go(request, "/admin/team")


@app.get("/admin/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    require(request, owner=True)
    with db.session() as s:
        cfg = get_cfg(s)
    values = {k: cfg[k.split(".")[0]][k.split(".")[1]] for k, _, _ in SETTING_FIELDS}
    return render(request, "admin/settings.html", org=cfg["org"], fields=SETTING_FIELDS, values=values,
                  has_signature=bool(cfg["signatory"]["signature_png"]), smtp_ok=bool(SMTP_USER and SMTP_PASSWORD),
                  smtp_user=SMTP_USER, public_url=PUBLIC_URL,
                  signing=_signing_info(),
                  db_kind="Postgres" if not db.URL.startswith("sqlite") else
                  "SQLite (local only: data is lost on every Render redeploy!)", active="settings")


def _signing_info() -> dict:
    with db.session() as s:
        key, source = get_signer(s)
        n = s.scalar(select(func.count()).select_from(PublicKey))
    return {"kid": key.kid, "source": source, "env": source.startswith("environment"), "published": n,
            "did": vc.did_for(PUBLIC_URL)}


@app.post("/admin/settings")
async def settings_save(request: Request):
    require(request, owner=True)
    form = await form_of(request)
    with db.session() as s:
        for k, _, _ in SETTING_FIELDS:
            if k in form:
                row = s.get(Setting, k) or Setting(key=k)
                row.value = str(form[k]).strip()[:500]
                s.add(row)
        up = form.get("signature")
        if isinstance(up, UploadFile) and up.filename:
            data = await up.read(1024 * 1024 + 1)
            if len(data) > 1024 * 1024 or not (data.startswith(b"\x89PNG") or data.startswith(b"\xff\xd8")):
                flash(request, "Signature must be a PNG or JPG under 1 MB.", "error")
                return go(request, "/admin/settings")
            a = s.get(Asset, "signature") or Asset(name="signature")
            a.data, a.mime = data, "image/png" if data.startswith(b"\x89PNG") else "image/jpeg"
            s.add(a)
        if form.get("remove_signature") == "1":
            a = s.get(Asset, "signature")
            if a:
                s.delete(a)
    flash(request, "Settings saved. New documents will use them; already-issued PDFs don't change.")
    return go(request, "/admin/settings")


@app.get("/admin/settings/sample.pdf")
def settings_sample(request: Request, kind: str = "offer"):
    """Preview the current letterhead/signature without issuing a real document."""
    require(request, owner=True)
    from .documents import certificate, offer_letter

    with db.session() as s:
        cfg = get_cfg(s)
        role = s.scalars(select(Role).order_by(Role.sort, Role.id)).first()
    today = dt.date.today()
    title = role.title if role else "Intern"
    buf = io.BytesIO()
    if kind == "certificate":
        certificate(buf, cfg=cfg, cand={"name": "Sample Intern", "college": "Your College"},
                    cert={"role_title": title, "start": (today - dt.timedelta(days=90)).isoformat(),
                          "end": today.isoformat(), "issued": today.isoformat(), "highlight": ""},
                    doc_id="SAMPLE", verify_url=verify_url("SAMPLE"))
    else:
        d = cfg["offer_defaults"]
        offer_letter(buf, cfg=cfg, cand={"name": "Sample Intern", "college": "Your College"},
                     offer={"role_title": title, "start": today.isoformat(),
                            "end": (today + dt.timedelta(days=90)).isoformat(), "mode": d["mode"],
                            "hours_per_week": d["hours_per_week"], "stipend": d["stipend"],
                            "reporting_to": d["reporting_to"], "duties": role.duty_list() if role else [],
                            "notice_days": d["notice_days"]},
                     doc_id="SAMPLE", verify_url=verify_url("SAMPLE"))
    return Response(buf.getvalue(), media_type="application/pdf",
                    headers={"Content-Disposition": 'inline; filename="sample.pdf"'})


# ---------------------------------------------------------------------------

@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 303 and exc.headers:
        return go(request, exc.headers["Location"])
    msg = exc.detail if not (exc.status_code == 404 and exc.detail == "Not Found") else "That page doesn't exist."
    return render(request, "error.html", status_code=exc.status_code, code=exc.status_code, message=msg)
