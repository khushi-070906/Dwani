"""
seed_test_subscription.py

Manually inserts a fake 'active' subscription row into license_server.py's
SQLite DB, so you can test the full activate.py flow locally WITHOUT a real
Razorpay payment going through.

Run this in the SAME folder as license_server.py (so it finds licenses.db),
AFTER you've started license_server.py at least once (so the table exists).

Usage:
    python seed_test_subscription.py
"""

import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).parent / "licenses.db"

TEST_LICENSE_KEY = "test-license-0001"
TEST_EMAIL = "test@example.com"
TEST_TIER = "institution"   # try "pro" or "free" too if you want to test gating

conn = sqlite3.connect(DB_PATH)
conn.execute("""
    INSERT INTO subscriptions
        (license_key, email, tier, status, current_period_end, max_attendees)
    VALUES (?, ?, ?, 'active', ?, NULL)
    ON CONFLICT(license_key) DO UPDATE SET
        tier=excluded.tier, status=excluded.status,
        current_period_end=excluded.current_period_end
""", (TEST_LICENSE_KEY, TEST_EMAIL, TEST_TIER, int(time.time()) + 30 * 24 * 60 * 60))
conn.commit()
conn.close()

print(f"Seeded test subscription:")
print(f"  license_key = {TEST_LICENSE_KEY}")
print(f"  email       = {TEST_EMAIL}")
print(f"  tier        = {TEST_TIER}")
print()
print("Now run activate.py against your local server, e.g.:")
print(f'  python activate.py --license-key {TEST_LICENSE_KEY} --server http://localhost:8443 --email {TEST_EMAIL}')
