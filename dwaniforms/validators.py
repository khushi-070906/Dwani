"""Field validators. Each returns (ok, value_or_None, error_key). Error keys are looked up in messages.py so the
citizen hears the reason in their own language. Pure functions, no I/O."""
from __future__ import annotations

import datetime as dt
import re

from .spoken import land_unit, spoken_to_code, spoken_to_khasra, spoken_to_date, spoken_to_digits, spoken_to_int, spoken_to_number

Result = tuple[bool, "str | None", str]

# --- Verhoeff (Aadhaar checksum) --------------------------------------------
_D = [[0,1,2,3,4,5,6,7,8,9],[1,2,3,4,0,6,7,8,9,5],[2,3,4,0,1,7,8,9,5,6],[3,4,0,1,2,8,9,5,6,7],[4,0,1,2,3,9,5,6,7,8],
      [5,9,8,7,6,0,4,3,2,1],[6,5,9,8,7,1,0,4,3,2],[7,6,5,9,8,2,1,0,4,3],[8,7,6,5,9,3,2,1,0,4],[9,8,7,6,5,4,3,2,1,0]]
_P = [[0,1,2,3,4,5,6,7,8,9],[1,5,7,6,2,8,3,0,9,4],[5,8,0,3,7,9,6,1,4,2],[8,9,1,6,0,4,3,5,2,7],[9,4,5,3,1,2,6,8,7,0],
      [4,2,8,6,5,7,3,9,0,1],[2,7,9,3,8,0,6,4,1,5],[7,0,4,6,9,1,3,2,5,8]]


def verhoeff_valid(number: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(number)):
        c = _D[c][_P[i % 8][int(ch)]]
    return c == 0


def verhoeff_append(number: str) -> str:
    """Append the check digit (used by tests/demo data generators)."""
    inv = [0, 4, 3, 2, 1, 5, 6, 7, 8, 9]
    c = 0
    for i, ch in enumerate(reversed(number)):
        c = _D[c][_P[(i + 1) % 8][int(ch)]]
    return number + str(inv[c])


def aadhaar(text: str, **_) -> Result:
    d = spoken_to_digits(text)
    if len(d) != 12:
        return False, None, "aadhaar_length"
    if d[0] in "01" or not verhoeff_valid(d):
        return False, None, "aadhaar_invalid"
    return True, d, ""


def mobile(text: str, **_) -> Result:
    d = spoken_to_digits(text)
    if len(d) == 12 and d.startswith("91"):
        d = d[2:]
    if len(d) == 11 and d.startswith("0"):
        d = d[1:]
    if len(d) != 10:
        return False, None, "mobile_length"
    if d[0] not in "6789":
        return False, None, "mobile_invalid"
    return True, d, ""


def pincode(text: str, **_) -> Result:
    d = spoken_to_digits(text)
    return (True, d, "") if len(d) == 6 and d[0] != "0" else (False, None, "pin_invalid")


def account_number(text: str, **_) -> Result:
    d = spoken_to_digits(text)
    return (True, d, "") if 9 <= len(d) <= 18 and set(d) != {"0"} else (False, None, "account_invalid")


def pan(text: str, **_) -> Result:
    c = spoken_to_code(text)
    return (True, c, "") if re.fullmatch(r"[A-Z]{5}[0-9]{4}[A-Z]", c) else (False, None, "pan_invalid")


def ifsc(text: str, **_) -> Result:
    c = spoken_to_code(text)
    return (True, c, "") if re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6}", c) else (False, None, "ifsc_invalid")


def date(text: str, min_age: int | None = None, max_age: int | None = None, today: dt.date | None = None, **_) -> Result:
    today = today or dt.date.today()
    parsed = spoken_to_date(text, today)
    if not parsed:
        return False, None, "date_invalid"
    d, m, y = parsed
    try:
        value = dt.date(y, m, d)
    except ValueError:
        return False, None, "date_invalid"
    if value > today:
        return False, None, "date_future"
    age = today.year - value.year - ((today.month, today.day) < (value.month, value.day))
    if min_age is not None and age < min_age:
        return False, None, "age_low"
    if max_age is not None and age > max_age:
        return False, None, "age_high"
    return True, value.isoformat(), ""


def number(text: str, minimum: int | None = None, maximum: int | None = None, decimals: int = 0,
           unit: str | None = None, **_) -> Result:
    """Quantities: "ढाई लाख", "1,50,000", "2.5 acre", "सवा दो". `decimals` > 0 allows fractions (land area);
    unit="acre" converts hectares and refuses state-specific units (bigha, kanal...) instead of guessing."""
    from decimal import ROUND_HALF_UP, Decimal

    n = spoken_to_number(text)
    if n is None:
        return False, None, "number_invalid"
    if unit == "acre":
        u = land_unit(text)
        if u == "local":
            return False, None, "unit_acres"
        if u == "hectare":
            n = n * Decimal("2.4711")
    if decimals:
        n = n.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP).normalize()
    elif n != n.to_integral_value():
        return False, None, "number_whole"
    if (minimum is not None and n < minimum) or (maximum is not None and n > maximum):
        return False, None, "number_range"
    return True, format(n, "f"), ""


def text(value: str, min_length: int = 2, **_) -> Result:
    v = " ".join(value.split()).strip(" .,;:।")
    return (True, v, "") if len(v) >= min_length and re.search(r"\w", v) else (False, None, "text_short")


def khasra(text: str, **_) -> Result:
    k = spoken_to_khasra(text)
    return (True, k, "") if k and int(k.split("/")[0].rstrip("कखगघABCD")) <= 99999 else (False, None, "khasra_invalid")


def ration_card(text: str, **_) -> Result:
    """Formats differ by state (all digits in many, letters + digits in some): 8-20 characters, mostly digits."""
    filler = {"ration", "card", "number", "no", "num", "is", "my", "the", "राशन", "कार्ड", "नंबर", "नम्बर", "मेरा", "है",
              "rashan", "kard", "nambar"}
    words = [w for w in re.split(r"[\s,.:;।]+", text) if w and w.lower() not in filler]
    c = spoken_to_code(" ".join(words))
    digits = sum(ch.isdigit() for ch in c)
    return (True, c, "") if 8 <= len(c) <= 20 and digits >= 6 else (False, None, "ration_invalid")


VALIDATORS = {
    "aadhaar": aadhaar, "mobile": mobile, "pincode": pincode, "account": account_number, "pan": pan, "ifsc": ifsc,
    "date": date, "number": number, "khasra": khasra, "ration_card": ration_card,
}

# Kinds whose readback must be spoken digit-by-digit/char-by-char and grouped, never as a number.
SPELLED_KINDS = {"aadhaar", "mobile", "pincode", "account", "pan", "ifsc", "ration_card"}
