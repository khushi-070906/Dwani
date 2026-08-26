import sqlite3
import time

conn = sqlite3.connect("licenses.db")
conn.execute(
    """
    INSERT INTO subscriptions
        (license_key, email, stripe_customer_id, tier, status, current_period_end, max_attendees)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    """,
    ("test-key", "test@example.com", "cus_test", "pro", "active", int(time.time()) + 2592000, None),
)
conn.commit()
conn.close()
print("inserted test subscription")
