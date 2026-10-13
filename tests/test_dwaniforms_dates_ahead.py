"""Dates printed on a paper -- a bill's last date, a hearing date -- are often ahead of today and said without a year.

Only fields that ask for it ("future": true in their params) accept them; a birth date or the date a problem began
must keep refusing the future, or a misheard year would slip into a form.
"""
import datetime as dt

import pytest

from dwaniforms.validators import date

TODAY = dt.date(2026, 10, 13)


@pytest.mark.parametrize("said, iso", [
    ("15 November", "2026-11-15"),          # no year: this year
    ("15 नवंबर", "2026-11-15"),
    ("5 जनवरी", "2027-01-05"),              # more than six months behind us: next year
    ("1 March", "2027-03-01"),
    ("10 October", "2026-10-10"),           # a few days ago: still this year (an overdue bill)
    ("15/11/27", "2027-11-15"),             # two-digit year on a bill means 20yy, not 19yy
    ("15 november 2026", "2026-11-15"),
])
def test_a_date_on_a_paper_may_be_ahead_and_without_a_year(said, iso):
    assert date(said, today=TODAY, future=True) == (True, iso, "")


@pytest.mark.parametrize("said", ["31 february", "15 August 1990", "15 november 2040", "kal"])
def test_impossible_or_far_off_dates_are_asked_again(said):
    ok, _, err = date(said, today=TODAY, future=True)
    assert not ok and err == "date_invalid"


def test_ordinary_date_fields_still_refuse_the_future():
    assert date("15 november 2026", today=TODAY) == (False, None, "date_future")
    assert date("15 November", today=TODAY)[0] is False               # no year: still not guessed for a birth date
    assert date("15/11/27", today=TODAY) == (True, "1927-11-15", "")    # a birth year stays in the past
