"""What "today" means. The online server runs on UTC, but the people using it live in India: between midnight and
5:30 in the morning there, UTC is still yesterday, and "days left" on a bill came out one too many. India keeps one
time zone all year with no daylight saving, so a fixed +5:30 is exact (and needs no timezone database on Windows)."""
from __future__ import annotations

import datetime as dt

IST = dt.timezone(dt.timedelta(hours=5, minutes=30), "IST")


def today() -> dt.date:
    return dt.datetime.now(IST).date()
