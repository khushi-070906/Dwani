"""'Today' is India's today. The online server runs on UTC; after 18:30 UTC it is already tomorrow for the people using it."""
import asyncio
import datetime as dt

from dwaniforms import clock, validators
from dwaniforms.service import FormService


def test_today_is_the_date_in_india_not_in_utc():
    assert clock.IST.utcoffset(None) == dt.timedelta(hours=5, minutes=30)
    assert clock.today() == dt.datetime.now(clock.IST).date()


def test_days_left_use_the_indian_date(monkeypatch):
    # 21:00 UTC on 13 Oct is 02:30 on 14 Oct in India; a last date of 20 Oct is 6 days away, not 7
    monkeypatch.setattr(clock, "today", lambda: dt.date(2026, 10, 14))

    async def go():
        svc = FormService()
        s, r = await svc.create("samjhao", "en")
        for text in ["electricity bill", "20/10/2026", "yes", "2890", "yes"]:
            if r.done:
                break
            r = await svc.answer_text(s.id, text)
        return r
    r = asyncio.run(go())
    assert r.done and r.outcome["days"] == 6


def test_a_date_on_a_paper_is_judged_against_the_indian_date(monkeypatch):
    monkeypatch.setattr(clock, "today", lambda: dt.date(2026, 10, 14))
    ok, value, _ = validators.date("14/10/2026", future=True)
    assert ok and value == "2026-10-14"
