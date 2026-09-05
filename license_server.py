"""
license_server.py

Runs on YOUR infrastructure, not the presenter's laptop. Two jobs:

1. Razorpay webhook: when a subscription is authenticated/charged/cancelled,
   update the license record (tier, expiry) in a local DB.
2. /activate endpoint: presenter's activate.py calls this once while online;
   we look up their subscription status and hand back a signed token.

This is intentionally minimal (SQLite, no auth on top of the license key
itself) — enough to run a real pilot, not a production billing system.
Swap SQLite for Postgres and add rate limiting before scaling this up.

Run:
    pip install fastapi uvicorn razorpay python-dotenv cryptography --break-system-packages
    export RAZORPAY_KEY_ID=rzp_test_...
    export RAZORPAY_KEY_SECRET=...
    export RAZORPAY_WEBHOOK_SECRET=...          # set when you add the webhook in the Dashboard
    python generate_signing_keys.py             # run ONCE, save both keys somewhere safe
    export LDST_LICENSE_PRIVATE_KEY=<the PRIVATE key it printed>
    # The PUBLIC key it printed goes on the PRESENTER's machine instead,
    # as a constant baked into server.py -- never set it here.
    export LDST_DB_PATH=/var/data/licenses.db   # only needed if you've attached a persistent disk;
                                                 # otherwise licenses.db lives next to this script
    uvicorn license_server:app --host 0.0.0.0 --port 8443

One-time setup in the Razorpay Dashboard (Test mode first):
    1. Account & Settings -> API Keys -> generate a Key ID / Key Secret.
    2. Subscriptions -> Plans -> create one Plan per tier x billing period
       you want to sell, e.g.:
         - "DwaniLive Pro Monthly"          amount 19900 (paise), period=monthly, interval=1
         - "DwaniLive Pro Annual"           amount 199000 (paise), period=yearly, interval=1
         - "DwaniLive Institution Monthly"  amount 99900 (paise), period=monthly, interval=1
         - "DwaniLive Institution Annual"   amount 999000 (paise), period=yearly, interval=1
       Amounts are in paise (₹1 = 100), so ₹199/mo = 19900.
    3. Copy each Plan ID (plan_XXXXXXXXXXXXXX) into PLAN_TO_TIER /
       TIER_PERIOD_TO_PLAN below.
    4. Subscriptions -> Settings -> Webhooks (or Account Settings -> Webhooks)
       -> add a webhook pointing at https://<this-server>/razorpay-webhook,
       select the "subscription.*" events (activated, charged, cancelled,
       completed, halted, pending), and copy the webhook secret into
       RAZORPAY_WEBHOOK_SECRET above.
"""

import json
import os
import secrets
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from contextlib import contextmanager
from typing import Optional

import bcrypt
from dotenv import load_dotenv
from fastapi import Cookie, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from licensing import License, sign_license, TIER_FEATURES, TIER_DEFAULT_MAX_ATTENDEES

load_dotenv()  # reads .env in the current directory if present, no-op if it doesn't exist

DB_PATH = Path(os.environ.get("LDST_DB_PATH", str(Path(__file__).parent / "licenses.db")))
PRIVATE_KEY = os.environ.get("LDST_LICENSE_PRIVATE_KEY", "")
RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "")
RAZORPAY_WEBHOOK_SECRET = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")

# Google OAuth -- see this module's docstring for the Cloud Console setup
# steps. GOOGLE_CLIENT_SECRET must NEVER be committed or shared outside
# Render's environment variables -- same handling as the license signing key.
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")

SESSION_COOKIE_NAME = "dwanilive_session"
SESSION_LIFETIME_SECONDS = 30 * 24 * 60 * 60  # 30 days

# Where the pricing page is served from. pricing.html lives in static/ and
# is served by THIS same app (see the routes below), so this is just this
# server's own public URL, e.g. https://dwanilive-api.onrender.com
# Locally it's wherever you run uvicorn, e.g. http://localhost:8443
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:8443") + "/pricing.html"


def _external_base_url(request: Request) -> str:
    """The scheme+host this request actually arrived on, from the browser's
    point of view -- NOT request.url, which behind Render's reverse proxy
    reports the internal http:// connection rather than the public https://
    one. Render (like most PaaS reverse proxies) sets X-Forwarded-Proto /
    X-Forwarded-Host, so we prefer those when present.

    This means Google's redirect_uri is always correct for whatever domain
    is actually serving the request -- no need to keep a FRONTEND_URL env
    var in sync with the real domain (and no more redirect_uri_mismatch if
    it's ever missing, wrong, or the domain changes).
    """
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", request.url.netloc))
    return f"{proto}://{host}"

if not PRIVATE_KEY:
    raise RuntimeError("Set LDST_LICENSE_PRIVATE_KEY before starting the server. "
                       "Generate one with generate_signing_keys.py.")

app = FastAPI(title="DwaniLive License Server")

# pricing.html calls this API on the SAME origin (see API_BASE = "" in
# pricing.html), so cross-origin requests shouldn't normally happen. CORS is
# kept here only in case you later split the page back out to a separate
# static host — lock allow_origins down to that domain if so.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Serve pricing.html directly, and static/ for any future assets (css, js,
# images) it references.
STATIC_DIR = Path(__file__).parent / "static"


@app.get("/pricing.html")
def serve_pricing_page():
    return FileResponse(STATIC_DIR / "pricing.html")


@app.get("/login.html")
def serve_login_page():
    return FileResponse(STATIC_DIR / "login.html")


@app.get("/signup.html")
def serve_signup_page():
    return FileResponse(STATIC_DIR / "signup.html")


@app.get("/dashboard.html")
def serve_dashboard_page():
    return FileResponse(STATIC_DIR / "dashboard.html")


@app.get("/settings.html")
def serve_settings_page():
    return FileResponse(STATIC_DIR / "settings.html")


@app.get("/")
def serve_root():
    return FileResponse(STATIC_DIR / "pricing.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                license_key TEXT PRIMARY KEY,
                email TEXT NOT NULL,
                razorpay_subscription_id TEXT,
                razorpay_customer_id TEXT,
                tier TEXT NOT NULL DEFAULT 'free',
                status TEXT NOT NULL DEFAULT 'inactive',
                current_period_end INTEGER,
                max_attendees INTEGER
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT,
                google_id TEXT UNIQUE,
                name TEXT,
                created_at INTEGER NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                expires_at INTEGER NOT NULL
            )
        """)


init_db()


# ---------------------------------------------------------------------------
# Auth helpers -- password hashing, sessions, current-user lookup
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:
        # Malformed hash (shouldn't happen from our own hash_password, but a
        # corrupt/empty stored value should fail closed, not raise a 500).
        return False


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires_at = int(time.time()) + SESSION_LIFETIME_SECONDS
    with db() as conn:
        conn.execute(
            "INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
            (token, user_id, expires_at),
        )
    return token


def get_current_user(session_token: Optional[str]):
    """Returns the users-table row for a valid, unexpired session cookie, or
    None. Never raises -- callers decide whether an anonymous request is an
    error (protected endpoints) or just means "logged out" (e.g. a page that
    shows different content either way).
    """
    if not session_token:
        return None
    with db() as conn:
        row = conn.execute(
            """
            SELECT users.* FROM sessions
            JOIN users ON users.id = sessions.user_id
            WHERE sessions.token = ? AND sessions.expires_at > ?
            """,
            (session_token, int(time.time())),
        ).fetchone()
    return row


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_LIFETIME_SECONDS,
        httponly=True,       # not readable from JS -- mitigates XSS token theft
        samesite="lax",      # sent on top-level navigation (needed for the Google redirect flow) but not cross-site POSTs
        secure=True,         # only sent over HTTPS -- fine since Render serves HTTPS; set False if you ever test over plain http://
    )


def get_razorpay_client():
    import razorpay

    if not (RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET):
        raise HTTPException(status_code=500, detail="Server misconfigured: RAZORPAY_KEY_ID/SECRET not set.")
    return razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))


# ---------------------------------------------------------------------------
# Activation — called by presenter's activate.py
# ---------------------------------------------------------------------------

class ActivateRequest(BaseModel):
    license_key: str
    email: str


@app.post("/activate")
def activate(req: ActivateRequest):
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM subscriptions WHERE license_key = ? AND email = ?",
            (req.license_key, req.email),
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="License key / email combination not found.")

    if row["status"] != "active":
        raise HTTPException(status_code=402, detail=f"Subscription status is '{row['status']}', not active.")

    now = int(time.time())
    expires_at = row["current_period_end"] or (now + 30 * 24 * 60 * 60)

    # A specific value in the DB always wins (lets you grant a custom cap to
    # one presenter); otherwise fall back to the tier's default cap.
    max_attendees = row["max_attendees"]
    if max_attendees is None:
        max_attendees = TIER_DEFAULT_MAX_ATTENDEES.get(row["tier"])

    license = License(
        tier=row["tier"],
        presenter_email=req.email,
        issued_at=now,
        expires_at=expires_at,
        max_attendees=max_attendees,
    )
    token = sign_license(license, PRIVATE_KEY)

    return {
        "token": token,
        "tier": license.tier,
        "expires_at_human": time.strftime("%Y-%m-%d", time.localtime(expires_at)),
    }


# ---------------------------------------------------------------------------
# Plan mapping — edit after creating Plans in the Razorpay Dashboard
# ---------------------------------------------------------------------------

# Map your actual Razorpay Plan IDs to tiers. Used by the webhook to figure
# out which tier a subscription belongs to.
PLAN_TO_TIER = {
    "plan_TUOKQmXcDrq1WE": "pro",           # Pro Monthly
    "plan_TUOMOeKQvjIG47": "pro",           # Pro Yearly
    "plan_TUOLasJRSu59bE": "institution",   # Institutional Monthly
    "plan_TUONHLp67zU75H": "institution",   # Institutional Yearly
}

# Which Plan ID to bill for a given (tier, period) pair selected on the
# pricing page. Edit these after creating the Plans above.
TIER_PERIOD_TO_PLAN = {
    ("pro", "monthly"): "plan_TUOKQmXcDrq1WE",
    ("pro", "annual"): "plan_TUOMOeKQvjIG47",
    ("institution", "monthly"): "plan_TUOLasJRSu59bE",
    ("institution", "annual"): "plan_TUONHLp67zU75H",
}

# Razorpay subscriptions need a max number of billing cycles up front.
# There's no "forever" option, so pick numbers that outlast any real
# subscription and let cancellation (or non-renewal) end it early.
TOTAL_COUNT_BY_PERIOD = {
    "monthly": 120,  # 10 years of monthly charges
    "annual": 15,    # 15 years of annual charges
}


# ---------------------------------------------------------------------------
# Create subscription — called by pricing.html when someone clicks Subscribe
# ---------------------------------------------------------------------------

class CreateSubscriptionRequest(BaseModel):
    tier: str      # "pro" or "institution"
    period: str    # "monthly" or "annual"
    email: Optional[str] = None  # ignored if logged in -- kept for older callers


@app.post("/create-subscription")
def create_subscription(req: CreateSubscriptionRequest, dwanilive_session: Optional[str] = Cookie(default=None)):
    # Subscriptions are now created only for a logged-in account, so the
    # email on the row always matches who actually paid -- never trust a
    # client-supplied email, since that'd let someone create a subscription
    # under a different person's address via devtools.
    user = get_current_user(dwanilive_session)
    if user is None:
        raise HTTPException(status_code=401, detail="Please log in before subscribing.")
    email = user["email"]

    client = get_razorpay_client()

    plan_id = TIER_PERIOD_TO_PLAN.get((req.tier, req.period))
    if plan_id is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown tier/period '{req.tier}/{req.period}'. "
                   f"Expected one of {list(TIER_PERIOD_TO_PLAN)}.",
        )

    # Generated now, before payment, and embedded in the subscription's
    # notes. The webhook later reads this same key back off the
    # subscription object to know which row to create/update.
    license_key = uuid.uuid4().hex[:20]

    # Reserve the row as 'pending' immediately so activate.py gives a clear
    # "payment still processing" message instead of a bare 404 if the
    # presenter races the webhook right after paying.
    with db() as conn:
        conn.execute("""
            INSERT INTO subscriptions (license_key, email, tier, status)
            VALUES (?, ?, ?, 'pending')
            ON CONFLICT(license_key) DO NOTHING
        """, (license_key, email, req.tier))

    subscription = client.subscription.create({
        "plan_id": plan_id,
        "customer_notify": 1,
        "total_count": TOTAL_COUNT_BY_PERIOD.get(req.period, 120),
        "notes": {"license_key": license_key, "email": email},
    })

    return {
        "subscription_id": subscription["id"],
        "key_id": RAZORPAY_KEY_ID,  # public key, safe to hand to the browser for Checkout.js
        "license_key": license_key,
    }


@app.get("/license-status")
def license_status(license_key: str, email: str):
    """Polled by the pricing page's success screen until the webhook lands."""
    with db() as conn:
        row = conn.execute(
            "SELECT status, tier FROM subscriptions WHERE license_key = ? AND email = ?",
            (license_key, email),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Not found.")
    return {"status": row["status"], "tier": row["tier"]}


# ---------------------------------------------------------------------------
# Auth -- email/password signup+login, Google OAuth, session-based "/me"
# ---------------------------------------------------------------------------

class SignupRequest(BaseModel):
    name: str
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


@app.post("/signup")
def signup(req: SignupRequest, response: Response):
    if len(req.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")

    with db() as conn:
        existing = conn.execute("SELECT id FROM users WHERE email = ?", (req.email,)).fetchone()
        if existing is not None:
            raise HTTPException(status_code=409, detail="An account with this email already exists.")

        cursor = conn.execute(
            "INSERT INTO users (email, password_hash, name, created_at) VALUES (?, ?, ?, ?)",
            (req.email, hash_password(req.password), req.name, int(time.time())),
        )
        user_id = cursor.lastrowid

    token = create_session(user_id)
    set_session_cookie(response, token)
    return {"ok": True, "email": req.email, "name": req.name}


@app.post("/login")
def login(req: LoginRequest, response: Response):
    with db() as conn:
        row = conn.execute(
            "SELECT id, password_hash FROM users WHERE email = ?", (req.email,)
        ).fetchone()

    # Same error for "no such user" and "wrong password" -- don't leak which
    # one it was, that's a user-enumeration side channel.
    if row is None or row["password_hash"] is None or not verify_password(req.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Incorrect email or password.")

    token = create_session(row["id"])
    set_session_cookie(response, token)
    return {"ok": True}


@app.post("/logout")
def logout(response: Response, dwanilive_session: Optional[str] = Cookie(default=None)):
    if dwanilive_session:
        with db() as conn:
            conn.execute("DELETE FROM sessions WHERE token = ?", (dwanilive_session,))
    response.delete_cookie(SESSION_COOKIE_NAME)
    return {"ok": True}


@app.get("/me")
def me(dwanilive_session: Optional[str] = Cookie(default=None)):
    """Polled by dashboard.html to render the logged-in user's info, plus
    whatever subscription (if any) is on file for their email -- the two
    systems (users, subscriptions) are joined here by email address since
    a subscription can predate an account existing at all (someone can
    subscribe via pricing.html's Razorpay flow before ever signing up).
    """
    user = get_current_user(dwanilive_session)
    if user is None:
        raise HTTPException(status_code=401, detail="Not logged in.")

    with db() as conn:
        subscription = conn.execute(
            "SELECT license_key, tier, status FROM subscriptions WHERE email = ? ORDER BY rowid DESC LIMIT 1",
            (user["email"],),
        ).fetchone()

    return {
        "email": user["email"],
        "name": user["name"],
        "has_password": user["password_hash"] is not None,
        "subscription": dict(subscription) if subscription else None,
    }


class UpdateAccountRequest(BaseModel):
    name: str


@app.put("/account")
def update_account(req: UpdateAccountRequest, dwanilive_session: Optional[str] = Cookie(default=None)):
    user = get_current_user(dwanilive_session)
    if user is None:
        raise HTTPException(status_code=401, detail="Not logged in.")

    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name can't be empty.")

    with db() as conn:
        conn.execute("UPDATE users SET name = ? WHERE id = ?", (name, user["id"]))

    return {"ok": True, "name": name}


class ChangePasswordRequest(BaseModel):
    current_password: Optional[str] = None
    new_password: str


@app.post("/account/password")
def change_password(req: ChangePasswordRequest, dwanilive_session: Optional[str] = Cookie(default=None)):
    user = get_current_user(dwanilive_session)
    if user is None:
        raise HTTPException(status_code=401, detail="Not logged in.")

    if len(req.new_password) < 8:
        raise HTTPException(status_code=400, detail="New password must be at least 8 characters.")

    # A Google-only account has no password_hash yet -- let them set one
    # without needing to prove a "current" password that never existed.
    # Anyone who already has a password must prove they know it first.
    if user["password_hash"] is not None:
        if not req.current_password or not verify_password(req.current_password, user["password_hash"]):
            raise HTTPException(status_code=401, detail="Current password is incorrect.")

    with db() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (hash_password(req.new_password), user["id"]),
        )

    return {"ok": True}


@app.post("/cancel-subscription")
def cancel_subscription(dwanilive_session: Optional[str] = Cookie(default=None)):
    user = get_current_user(dwanilive_session)
    if user is None:
        raise HTTPException(status_code=401, detail="Not logged in.")

    with db() as conn:
        row = conn.execute(
            "SELECT license_key, razorpay_subscription_id, status FROM subscriptions "
            "WHERE email = ? ORDER BY rowid DESC LIMIT 1",
            (user["email"],),
        ).fetchone()

    if row is None or row["status"] not in ("active", "pending"):
        raise HTTPException(status_code=400, detail="No active subscription to cancel.")

    if row["razorpay_subscription_id"]:
        client = get_razorpay_client()
        try:
            # cancel_at_cycle_end=1 keeps DwaniLive usable through the period
            # already paid for, matching pricing.html's FAQ promise that
            # cancelling doesn't cut you off mid-billing-cycle.
            client.subscription.cancel(row["razorpay_subscription_id"], {"cancel_at_cycle_end": 1})
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Razorpay cancellation failed: {exc}")

    with db() as conn:
        conn.execute(
            "UPDATE subscriptions SET status = 'cancelling' WHERE license_key = ?",
            (row["license_key"],),
        )

    return {"ok": True}


def _safe_next_path(next_path: str) -> str:
    """Only allow same-site relative paths (e.g. '/pricing.html') as a post-
    login redirect target -- rejects absolute URLs and protocol-relative
    '//evil.com' paths so this can't be turned into an open redirect."""
    if next_path and next_path.startswith("/") and not next_path.startswith("//"):
        return next_path
    return "/dashboard.html"


@app.get("/auth/google/login")
def google_login(request: Request, response: Response, next: str = "/dashboard.html"):
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=500, detail="Server misconfigured: GOOGLE_CLIENT_ID not set.")

    redirect_uri = _external_base_url(request) + "/auth/google/callback"

    # CSRF protection: a random value we can verify came back unchanged on
    # the callback, stored in a short-lived cookie rather than server-side
    # state, since there's no session yet at this point in the flow.
    state = secrets.token_urlsafe(24)
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
    }
    google_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)

    redirect = RedirectResponse(url=google_url)
    redirect.set_cookie(
        key="dwanilive_oauth_state", value=state, max_age=600, httponly=True, samesite="lax", secure=True
    )
    # Where to send the user after a successful callback -- e.g. back to
    # pricing.html so they can resume a subscription they started pre-login.
    redirect.set_cookie(
        key="dwanilive_oauth_next", value=_safe_next_path(next), max_age=600,
        httponly=True, samesite="lax", secure=True,
    )
    return redirect


@app.get("/auth/google/callback")
def google_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    dwanilive_oauth_state: Optional[str] = Cookie(default=None),
    dwanilive_oauth_next: Optional[str] = Cookie(default=None),
):
    if error:
        raise HTTPException(status_code=400, detail=f"Google sign-in was cancelled or failed: {error}")

    if not state or state != dwanilive_oauth_state:
        raise HTTPException(status_code=400, detail="OAuth state mismatch -- please try signing in again.")

    if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET):
        raise HTTPException(status_code=500, detail="Server misconfigured: Google OAuth credentials not set.")

    redirect_uri = _external_base_url(request) + "/auth/google/callback"

    # Exchange the authorization code for tokens. Using urllib (stdlib) here
    # rather than adding `requests` as a new dependency -- same minimal-deps
    # approach as activate.py.
    token_body = urllib.parse.urlencode({
        "code": code,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }).encode("utf-8")
    token_req = urllib.request.Request(
        "https://oauth2.googleapis.com/token", data=token_body, method="POST"
    )
    try:
        with urllib.request.urlopen(token_req, timeout=15) as resp:
            token_data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise HTTPException(status_code=400, detail=f"Google token exchange failed: {exc.read().decode()}")

    access_token = token_data.get("access_token")
    if not access_token:
        raise HTTPException(status_code=400, detail="Google did not return an access token.")

    userinfo_req = urllib.request.Request(
        "https://www.googleapis.com/oauth2/v2/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    with urllib.request.urlopen(userinfo_req, timeout=15) as resp:
        userinfo = json.loads(resp.read())

    google_id = userinfo.get("id")
    email = userinfo.get("email")
    name = userinfo.get("name", email)

    if not (google_id and email):
        raise HTTPException(status_code=400, detail="Google did not return the expected profile info.")

    with db() as conn:
        row = conn.execute("SELECT id FROM users WHERE google_id = ? OR email = ?", (google_id, email)).fetchone()
        if row is None:
            cursor = conn.execute(
                "INSERT INTO users (email, google_id, name, created_at) VALUES (?, ?, ?, ?)",
                (email, google_id, name, int(time.time())),
            )
            user_id = cursor.lastrowid
        else:
            user_id = row["id"]
            # Link the Google account to an existing password-based user
            # signing in with Google for the first time, matched by email.
            conn.execute("UPDATE users SET google_id = ? WHERE id = ? AND google_id IS NULL", (google_id, user_id))

    token = create_session(user_id)
    redirect = RedirectResponse(url=_safe_next_path(dwanilive_oauth_next or "/dashboard.html"))
    redirect.delete_cookie("dwanilive_oauth_state")
    redirect.delete_cookie("dwanilive_oauth_next")
    set_session_cookie(redirect, token)
    return redirect


# ---------------------------------------------------------------------------
# Razorpay webhook — keeps the subscriptions table in sync with billing state
# ---------------------------------------------------------------------------

# Razorpay subscription.* statuses -> the status we store locally.
# ("created"/"authenticated" both mean "not paying yet"; we only flip to
# 'active' once a charge has actually gone through, i.e. on "activated" or
# the first "charged" event.)
STATUS_MAP = {
    "activated": "active",
    "charged": "active",
    "completed": "canceled",   # ran through all total_count cycles
    "cancelled": "canceled",
    "halted": "past_due",      # renewal payment failed
    "pending": "past_due",
}


@app.post("/razorpay-webhook")
async def razorpay_webhook(request: Request):
    import razorpay

    payload_bytes = await request.body()
    sig_header = request.headers.get("x-razorpay-signature", "")

    client = get_razorpay_client()
    try:
        client.utility.verify_webhook_signature(
            payload_bytes.decode("utf-8"), sig_header, RAZORPAY_WEBHOOK_SECRET
        )
    except razorpay.errors.SignatureVerificationError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid webhook: {exc}")

    event = await request.json()
    event_type = event.get("event", "")

    if not event_type.startswith("subscription."):
        return {"received": True}

    sub_payload = event.get("payload", {}).get("subscription", {}).get("entity")
    if not sub_payload:
        return {"received": True}

    razorpay_subscription_id = sub_payload["id"]
    razorpay_customer_id = sub_payload.get("customer_id")
    plan_id = sub_payload.get("plan_id")
    rp_status = sub_payload.get("status", "")
    current_end = sub_payload.get("current_end")  # unix timestamp, or None

    notes = sub_payload.get("notes") or {}
    license_key = notes.get("license_key")
    email = notes.get("email")

    tier = PLAN_TO_TIER.get(plan_id, "free")
    status = STATUS_MAP.get(rp_status, rp_status or "inactive")

    if license_key and email:
        with db() as conn:
            conn.execute("""
                INSERT INTO subscriptions
                    (license_key, email, razorpay_subscription_id, razorpay_customer_id,
                     tier, status, current_period_end)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(license_key) DO UPDATE SET
                    tier=excluded.tier, status=excluded.status,
                    current_period_end=COALESCE(excluded.current_period_end, subscriptions.current_period_end),
                    razorpay_subscription_id=excluded.razorpay_subscription_id,
                    razorpay_customer_id=excluded.razorpay_customer_id
            """, (license_key, email, razorpay_subscription_id, razorpay_customer_id,
                  tier, status, current_end))
    else:
        # Notes can go missing on some renewal events depending on API
        # version — fall back to matching on the subscription id we stored
        # at creation/first-activation time.
        with db() as conn:
            conn.execute("""
                UPDATE subscriptions
                SET status = ?, current_period_end = COALESCE(?, current_period_end)
                WHERE razorpay_subscription_id = ?
            """, (status, current_end, razorpay_subscription_id))

    return {"received": True}