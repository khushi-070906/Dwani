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
    pip install fastapi uvicorn razorpay python-dotenv --break-system-packages
    export RAZORPAY_KEY_ID=rzp_test_...
    export RAZORPAY_KEY_SECRET=...
    export RAZORPAY_WEBHOOK_SECRET=...          # set when you add the webhook in the Dashboard
    export LDST_LICENSE_SIGNING_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")
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

import os
import sqlite3
import time
import uuid
from pathlib import Path
from contextlib import contextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from licensing import License, sign_license, TIER_FEATURES

load_dotenv()  # reads .env in the current directory if present, no-op if it doesn't exist

DB_PATH = Path(__file__).parent / "licenses.db"
SIGNING_KEY = os.environ.get("LDST_LICENSE_SIGNING_KEY", "").encode("utf-8")
RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "")
RAZORPAY_WEBHOOK_SECRET = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")

# Where the pricing page is served from. pricing.html lives in static/ and
# is served by THIS same app (see the routes below), so this is just this
# server's own public URL, e.g. https://dwanilive-api.onrender.com
# Locally it's wherever you run uvicorn, e.g. http://localhost:8443
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:8443") + "/pricing.html"

if not SIGNING_KEY:
    raise RuntimeError("Set LDST_LICENSE_SIGNING_KEY before starting the server.")

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


init_db()


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

    license = License(
        tier=row["tier"],
        presenter_email=req.email,
        issued_at=now,
        expires_at=expires_at,
        max_attendees=row["max_attendees"],
    )
    token = sign_license(license, SIGNING_KEY)

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
    email: str


@app.post("/create-subscription")
def create_subscription(req: CreateSubscriptionRequest):
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
        """, (license_key, req.email, req.tier))

    subscription = client.subscription.create({
        "plan_id": plan_id,
        "customer_notify": 1,
        "total_count": TOTAL_COUNT_BY_PERIOD.get(req.period, 120),
        "notes": {"license_key": license_key, "email": req.email},
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