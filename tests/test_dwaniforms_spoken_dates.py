"""Hindi spoken numbers and dates: "do hazaar teen sau paanch" is 2305 and "pandrah march do hazaar do" is 15/03/2002,
in Latin or Devanagari letters. These are what people say for Aadhaar, phone, pincode and date of birth."""
import datetime as dt

import pytest

from dwaniforms.spoken import spoken_to_date, spoken_to_digits, spoken_to_int, spoken_to_number

TODAY = dt.date(2026, 10, 14)


# ---- spoken numbers and dates ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("said,want", [
    ("do hazaar teen sau paanch", 2305), ("दो हज़ार तीन सौ पाँच", 2305), ("two thousand three hundred five", 2305),
    ("do hazaar do", 2002), ("ek lakh pachas hazaar", 150000), ("do lakh", 200000), ("saade teen sau", 350),
    ("do hazaar teen sau do", 2302)])
def test_spoken_numbers(said, want):
    assert spoken_to_number(said) == want


def test_a_lone_do_is_still_the_word_do():
    assert spoken_to_number("mujhe do") is None and spoken_to_int("mujhe do") is None


@pytest.mark.parametrize("said,want", [
    ("pandrah march do hazaar do", (15, 3, 2002)), ("पंद्रह मार्च दो हज़ार दो", (15, 3, 2002)), ("15 march 2002", (15, 3, 2002)),
    ("paanch janvari do hazaar chaar", (5, 1, 2004)), ("pehli agast unnis sau nabbe", (1, 8, 1990)),
    ("doosri may do hazaar paanch", (2, 5, 2005)), ("teesri sitambar unnis sau pachanve", (3, 9, 1995)),
    ("bees farvari do hazaar", (20, 2, 2000)), ("tees janvari do hazaar do ko", (30, 1, 2002)),
    ("fifteen august nineteen ninety", (15, 8, 1990)), ("15th march 2002", (15, 3, 2002)), ("1st april 1999", (1, 4, 1999)),
    ("pandrah march do hazaar do mein", (15, 3, 2002)), ("पहली जनवरी दो हज़ार", (1, 1, 2000)), ("15-8-90", (15, 8, 1990))])
def test_spoken_dates(said, want):
    assert spoken_to_date(said, TODAY) == want


@pytest.mark.parametrize("said", ["pachas march do hazaar do", "hello world", "march", ""])
def test_things_that_are_not_dates(said):
    assert spoken_to_date(said, TODAY) is None


def test_digit_strings_still_read_as_before():
    assert spoken_to_digits("nau aath saat chhe paanch chaar teen do ek shunya") == "9876543210"
    assert spoken_to_digits("double nine teen triple aath") == "993888"
    assert spoken_to_digits("दो हज़ार तीन सौ पैंतालीस") == "2345"
