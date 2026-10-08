"""Spoken-input normalisation: the research gap named in the project notes
(spoken numbers, names, addresses in Indic languages).

Whisper often already returns digits, but for Hindi/Hinglish it frequently
returns number WORDS ("नौ आठ सात ...", "double five"), so every numeric field
passes through here. Coverage: English and Hindi (Devanagari + romanised)
number words, Devanagari/Eastern-Arabic digits, double/triple. Other
languages' digit words are NOT covered yet (see LANG_DIGIT_WORDS to extend).
"""
from __future__ import annotations

import datetime as dt
import re
import unicodedata

_DIGIT_TRANSLATION = {}
for _base in (0x0966, 0x09E6, 0x0A66, 0x0AE6, 0x0B66, 0x0BE6, 0x0C66, 0x0CE6, 0x0D66, 0x0660, 0x06F0):
    for _i in range(10):
        _DIGIT_TRANSLATION[_base + _i] = str(_i)   # Devanagari, Bengali, Gurmukhi, Gujarati, Odia, Tamil, Telugu, Kannada, Malayalam, Arabic-Indic, Persian


def ascii_digits(text: str) -> str:
    """Map every Indic/Arabic digit character to 0-9."""
    return text.translate(_DIGIT_TRANSLATION)


def _norm(token: str) -> str:
    token = unicodedata.normalize("NFC", token.strip().lower())
    return token.replace("ँ", "ं").replace("़", "").replace("ॉ", "ा")   # chandrabindu->anusvara, drop nukta: ASR spelling variance


# --- English -----------------------------------------------------------------
_EN_UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen".split())}
_EN_UNITS.update({"oh": 0, "o": 0, "nil": 0, "to": 2, "too": 2, "for": 4, "ate": 8})   # common ASR mishearings of digits
_EN_TENS = {w: 10 * (i + 2) for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split())}

# --- Hindi 0-99 (irregular, so a full table) ---------------------------------
_HI_0_99 = (
    "शून्य एक दो तीन चार पांच छह सात आठ नौ दस ग्यारह बारह तेरह चौदह पंद्रह सोलह सत्रह अठारह उन्नीस बीस "
    "इक्कीस बाईस तेईस चौबीस पच्चीस छब्बीस सत्ताईस अट्ठाईस उनतीस तीस इकतीस बत्तीस तैंतीस चौंतीस पैंतीस छत्तीस सैंतीस "
    "अड़तीस उनतालीस चालीस इकतालीस बयालीस तैंतालीस चौवालीस पैंतालीस छियालीस सैंतालीस अड़तालीस उनचास पचास "
    "इक्यावन बावन तिरपन चौवन पचपन छप्पन सत्तावन अट्ठावन उनसठ साठ इकसठ बासठ तिरसठ चौंसठ पैंसठ छियासठ सड़सठ अड़सठ उनहत्तर "
    "सत्तर इकहत्तर बहत्तर तिहत्तर चौहत्तर पचहत्तर छिहत्तर सतहत्तर अठहत्तर उनासी अस्सी "
    "इक्यासी बयासी तिरासी चौरासी पचासी छियासी सत्तासी अट्ठासी नवासी नब्बे इक्यानवे बानवे तिरानवे चौरानवे पचानवे "
    "छियानवे सत्तानवे अट्ठानवे निन्यानवे"
).split()
assert len(_HI_0_99) == 100, len(_HI_0_99)
_HI_NUM = {_norm(w): i for i, w in enumerate(_HI_0_99)}
_HI_NUM.update({_norm(k): v for k, v in {
    "पाँच": 5, "छः": 6, "छे": 6, "छ": 6, "अठ्ठाईस": 28, "पचीस": 25, "सत्रा": 17, "अठारा": 18, "बाइस": 22, "तेइस": 23,
}.items()})
# romanised Hindi (Whisper sometimes emits Latin for Hinglish). "no" is deliberately NOT here: it is the English word
# ("Aadhaar no. ...", "no income") far more often than a spelling of nau.
_HI_ROMAN = {
    "shunya": 0, "ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5, "chhe": 6, "chhah": 6,
    "che": 6, "chah": 6, "saat": 7, "sat": 7, "aath": 8, "ath": 8, "nau": 9, "das": 10, "bees": 20, "tees": 30,
    "chalis": 40, "pachas": 50, "saath": 60, "sattar": 70, "assi": 80, "nabbe": 90,
}
_HI_ROMAN_0_99 = (
    "shunya ek do teen char paanch chhah saat aath nau das gyarah barah terah chaudah pandrah solah satrah atharah unnis "
    "bees ikkis baais teis chaubis pachchis chhabbis sattais atthais untis tees ikatis battis taintis chauntis paintis "
    "chhattis saintis adtis untalis chalis iktalis bayalis taintalis chauvalis paintalis chhiyalis saintalis adtalis unchas "
    "pachas ikyavan bavan tirpan chauvan pachpan chhappan sattavan atthavan unsath saath iksath basath tirsath chausath "
    "painsath chhiyasath sarsath adsath unhattar sattar ikhattar bahattar tihattar chauhattar pachhattar chhihattar "
    "satahattar athhattar unasi assi ikyasi bayasi tirasi chaurasi pachasi chhiyasi sattasi atthasi navasi nabbe ikyanve "
    "banve tiranve chauranve pachanve chhiyanve sattanve atthanve ninyanve"
).split()
assert len(_HI_ROMAN_0_99) == 100, len(_HI_ROMAN_0_99)
for _i, _w in enumerate(_HI_ROMAN_0_99):
    _HI_ROMAN.setdefault(_w, _i)

_SCALES = {
    "hundred": 100, "thousand": 1000, "lakh": 100_000, "lac": 100_000, "lakhs": 100_000, "crore": 10_000_000,
    "सौ": 100, "हज़ार": 1000, "हजार": 1000, "लाख": 100_000, "करोड़": 10_000_000, "करोड": 10_000_000,
    "sau": 100, "hazar": 1000, "hazaar": 1000, "karod": 10_000_000,
}
_SCALES = {_norm(k): v for k, v in _SCALES.items()}
_REPEAT = {"double": 2, "triple": 3, "डबल": 2, "दबल": 2, "ट्रिपल": 3, "तिबल": 3, "dabal": 2, "tipal": 3, "triple": 3}
_REPEAT = {_norm(k): v for k, v in _REPEAT.items()}
_FILLER = {"no", "num", "is", "my", "number", "the", "and", "a", "uh", "um", "hmm", "मेरा", "नंबर", "नम्बर", "है", "हैं", "और", "मेरी",
           "आधार", "aadhaar", "aadhar", "mobile", "phone", "pin", "code", "पिन", "कोड", "मोबाइल", "फोन", "फ़ोन", "नंबर", "number"}

# In QUANTITY fields ("income", "land area") these are ordinary words, not digits: "2 lakh for the year" must not become
# 200004, "I sat on 2 acres" must not contain a 7. (In digit-by-digit dictation they are still accepted.)
# "do" (Hindi 2) is kept only when a scale word follows ("do lakh").
_QTY_IGNORE = {"to", "too", "for", "ate", "o", "oh", "no", "sat", "ath", "che"}

# Extension point: other languages' single-digit words.
LANG_DIGIT_WORDS: dict[str, dict[str, int]] = {}


def _lang_number(tok: str) -> int | None:
    """A number word from a language pack (lang/*.json: words.digits, 0-10)."""
    for table in LANG_DIGIT_WORDS.values():
        if tok in table:
            return table[tok]
    return None


def _tokens(text: str) -> list[str]:
    text = ascii_digits(unicodedata.normalize("NFC", text))
    text = re.sub(r"[,;:।|/\\()\[\]\-–—_.]+", " ", text)   # separators people say/ASR emits inside numbers
    return [_norm(t) for t in text.split() if t.strip()]


def _digit_word(tok: str) -> int | None:
    for table in (_EN_UNITS, _HI_NUM, _HI_ROMAN, *LANG_DIGIT_WORDS.values()):
        v = table.get(tok)
        if v is not None and v <= 9:
            return v
    return None


# Zero as speech recognisers write it in Hindi ("जीरो") and as people say it.
_ZERO_WORDS = {_norm(w) for w in ("जीरो", "ज़ीरो", "जेरो", "ज़ेरो", "ज़िरो", "जिरो", "सिफ़र", "सिफर", "zero", "jiro", "zeero")}


def _number_word(tok: str) -> int | None:
    """0-99 as one spoken word in any table ("पैंतालीस" -> 45, "twenty" -> 20, "जीरो" -> 0)."""
    if tok in _ZERO_WORDS:
        return 0
    for table in (_EN_UNITS, _EN_TENS, _HI_NUM, _HI_ROMAN, *LANG_DIGIT_WORDS.values()):
        v = table.get(tok)
        if v is not None:
            return v
    return None


def spoken_to_digits(text: str) -> str:
    """Digits of an Aadhaar / mobile / PIN / account number, however it was said or transcribed:
    one digit at a time ("दो तीन चार"), in pairs as people read phone numbers ("तेईस पैंतालीस" -> 2345),
    in groups ("दो हज़ार तीन सौ पैंतालीस" -> 2345), as numerals ("2345 6789"), with "double / डबल",
    and with zero written as "जीरो". Returns only the digits found."""
    out: list[str] = []
    repeat = 1
    group = None            # value of a "दो हज़ार तीन सौ पैंतालीस"-style group being built
    after_scale = False     # the previous token was सौ / हज़ार: a following number word adds to the group
    en_tens = False         # the previous token was an English tens word: "twenty three" -> 23

    def flush():
        nonlocal group
        if group is not None:
            out.append(str(group))
            group = None

    for tok in _tokens(text):
        if tok in _REPEAT:
            flush(); repeat = _REPEAT[tok]; after_scale = en_tens = False
            continue
        if tok.isdecimal():
            flush()
            out.append(tok * repeat if repeat > 1 and len(tok) == 1 else tok)
            repeat, after_scale, en_tens = 1, False, False
            continue
        if tok in _SCALES and _SCALES[tok] in (100, 1000):
            base = int(out.pop()) if out and not after_scale and group is None and out[-1].isdecimal() and len(out[-1]) <= 2 else None
            if group is None:
                group = (base if base is not None else 1) * _SCALES[tok]
            elif _SCALES[tok] == 100:
                last = group % 1000
                group = group - last + (last or 1) * 100
            else:
                group = (group or 1) * 1000
            after_scale, en_tens = True, False
            continue
        n = _number_word(tok)
        if n is None:
            if tok not in _FILLER:
                repeat = 1
            continue
        if group is not None and after_scale:
            group += n
            after_scale = False
            continue
        if en_tens and 1 <= n <= 9 and out:
            out[-1] = str(int(out[-1]) + n)
            en_tens = False
            continue
        flush()
        out.append(str(n) * repeat if repeat > 1 and n <= 9 else str(n))
        repeat, after_scale = 1, False
        en_tens = tok in _EN_TENS
    flush()
    return "".join(out)


def _spoken_to_digits_strict(text: str) -> str:
    """Digit-by-digit reading (Aadhaar, mobile, PIN, account no.).

    "nine eight seven ... double five" / "९८७६ ५४३२ १०" / "9876 54 3210" -> "98765432..."
    A token that is a multi-digit number word (e.g. "सत्तासी") is NOT split: people dictating digit strings say
    one digit at a time, so unknown words are dropped rather than guessed. Returns only the digits found.
    """
    out: list[str] = []
    repeat = 1
    for tok in _tokens(text):
        if tok in _REPEAT:
            repeat = _REPEAT[tok]
            continue
        if tok.isdecimal():
            out.append(tok * repeat if repeat > 1 and len(tok) == 1 else tok)
        else:
            d = _digit_word(tok)
            if d is None:
                if tok not in _FILLER:
                    repeat = 1
                continue
            out.append(str(d) * repeat)
        repeat = 1
    return "".join(out)


# --- fractional quantities (Indian usage) -------------------------------------
# Standalone values: आधा 0.5, डेढ़ 1.5, ढाई 2.5, पाव 0.25 ("ढाई लाख" = 2,50,000).
_FRACTION_WORDS = {_norm(k): v for k, v in {
    "आधा": "0.5", "आधी": "0.5", "आधे": "0.5", "aadha": "0.5", "aadhi": "0.5", "adha": "0.5", "half": "0.5",
    "डेढ़": "1.5", "डेढ": "1.5", "dedh": "1.5", "derh": "1.5", "dedhh": "1.5",
    "ढाई": "2.5", "dhai": "2.5", "dhaai": "2.5", "dhaee": "2.5",
    "पाव": "0.25", "pav": "0.25", "paav": "0.25", "quarter": "0.25",
}.items()}
# Modifiers that change the NEXT number: सवा +¼, साढ़े +½, पौने -¼ ("सवा दो लाख" = 2,25,000, "पौने दो" = 1.75).
_FRACTION_MODS = {_norm(k): v for k, v in {
    "सवा": "0.25", "sava": "0.25", "sawa": "0.25", "savaa": "0.25",
    "साढ़े": "0.5", "साढे": "0.5", "sadhe": "0.5", "saadhe": "0.5", "sarhe": "0.5", "saade": "0.5",
    "पौने": "-0.25", "paune": "-0.25", "pone": "-0.25", "paune": "-0.25",
}.items()}
_POINT = {_norm(k) for k in ("point", "दशमलव", "dashamlav", "पॉइंट", "प्वाइंट")}
# Land-area units other than acres vary by state (a bigha is 0.2-0.6 acre): never guess them.
_HECTARE = {_norm(k) for k in ("hectare", "hectares", "हेक्टेयर", "हैक्टेयर", "hektar")}
_LOCAL_LAND_UNITS = {_norm(k) for k in (
    "bigha", "bighas", "बीघा", "बिघा", "kanal", "कनाल", "guntha", "गुंठा", "biswa", "बिस्वा", "marla", "मरला",
    "cent", "cents", "डिसमिल", "dismil", "kattha", "कट्ठा", "katha", "ground", "gaj", "गज")}
_NUMBER_IN_TEXT = re.compile(r"\d+(?:,\d+)*(?:\.\d+)?")


def _qty_tokens(text: str) -> list[str]:
    """Like _tokens, but keeps "1,50,000" and "2.5" as single numbers (they're split on ',' / '.' elsewhere)."""
    text = ascii_digits(unicodedata.normalize("NFC", text))
    nums: list[str] = []

    def keep(m):
        nums.append(m.group(0).replace(",", ""))
        return f" \x00{len(nums) - 1} "
    text = _NUMBER_IN_TEXT.sub(keep, text)
    text = re.sub(r"[,;:।|/\\()\[\]\-–—_]+", " ", text).replace(".", " ")
    out = []
    for t in text.split():
        if t.startswith("\x00"):
            out.append(nums[int(t[1:])])
        elif t.strip():
            out.append(_norm(t))
    return out


def land_unit(text: str) -> str | None:
    """'hectare' / 'local' (bigha, kanal, guntha... -- state-specific) / None (acres or no unit said)."""
    toks = set(_qty_tokens(text))
    if toks & _HECTARE:
        return "hectare"
    if toks & _LOCAL_LAND_UNITS:
        return "local"
    return None


def spoken_to_number(text: str):
    """Quantity reading with fractions: "ढाई लाख" -> 250000, "2.5 acre" -> 2.5, "1,50,000" -> 150000,
    "सवा दो लाख" -> 225000, "one and a half lakh" -> 150000, "two point five" -> 2.5. Returns a Decimal or None."""
    from decimal import Decimal

    toks = _qty_tokens(text)
    total, current, seen = Decimal(0), Decimal(0), False
    mod = None             # pending सवा / साढ़े / पौने
    frac_digits = None     # after "point": the digits that follow
    for i, tok in enumerate(toks):
        nxt = toks[i + 1] if i + 1 < len(toks) else ""
        if frac_digits is not None:
            d = int(tok) if tok.isdecimal() and len(tok) == 1 else _digit_word(tok)
            if d is not None:
                frac_digits += str(d)
                continue
            current += Decimal("0." + frac_digits) if frac_digits else 0
            frac_digits = None
        if tok in _POINT and seen:
            frac_digits = ""
            continue
        if tok in _FRACTION_MODS:
            mod = Decimal(_FRACTION_MODS[tok])
            continue
        if tok in _QTY_IGNORE:
            continue
        # Hindi "do" (2) only counts before a scale word or after सवा/साढ़े/पौने ("paune do")
        if tok == "do" and not (nxt in _SCALES or mod is not None):
            continue
        value = None
        if re.fullmatch(r"\d+(?:\.\d+)?", tok):
            value = Decimal(tok)
        elif tok in _FRACTION_WORDS:
            if tok in ("half", _norm("आधा")) and i >= 2 and toks[i - 1] == "a" and toks[i - 2] == "and":
                current += Decimal("0.5")          # "one and a half"
                seen = True
                continue
            value = Decimal(_FRACTION_WORDS[tok])
        elif tok in _SCALES:
            scale = Decimal(_SCALES[tok])
            if scale == 100:
                current = max(current, Decimal(1)) * 100
            else:
                total += max(current, Decimal(1)) * scale
                current = Decimal(0)
            seen = True
            continue
        elif tok in _EN_TENS:
            value = Decimal(_EN_TENS[tok])
        elif tok in _EN_UNITS:
            value = Decimal(_EN_UNITS[tok])
        elif tok in _HI_NUM:
            value = Decimal(_HI_NUM[tok])
        elif tok in _HI_ROMAN:
            value = Decimal(_HI_ROMAN[tok])
        elif _lang_number(tok) is not None:
            value = Decimal(_lang_number(tok))
        if value is not None:
            if mod is not None:
                value += mod
                mod = None
            current += value
            seen = True
    if frac_digits:
        current += Decimal("0." + frac_digits)
    if not seen:
        return None
    result = total + current
    return result.to_integral_value() if result == result.to_integral_value() else result.normalize()


def spoken_to_int(text: str) -> int | None:
    """Quantity reading (age, income, land area): "twenty five" / "पच्चीस" / "2 lakh 50 thousand" / "३५" -> int."""
    toks = _tokens(text)
    total, current, seen = 0, 0, False
    for i, tok in enumerate(toks):
        if tok in _QTY_IGNORE:
            continue
        if tok == "do" and not (i + 1 < len(toks) and toks[i + 1] in _SCALES):
            continue
        if tok.isdecimal():
            current += int(tok); seen = True
        elif tok in _SCALES:
            scale = _SCALES[tok]
            if scale == 100:
                current = max(current, 1) * 100
            else:
                total += max(current, 1) * scale; current = 0
            seen = True
        elif tok in _EN_TENS:
            current += _EN_TENS[tok]; seen = True
        elif tok in _EN_UNITS:
            current += _EN_UNITS[tok]; seen = True
        elif tok in _HI_NUM:
            current += _HI_NUM[tok]; seen = True
        elif tok in _HI_ROMAN:
            current += _HI_ROMAN[tok]; seen = True
        elif _lang_number(tok) is not None:
            current += _lang_number(tok); seen = True
    return total + current if seen else None


# --- spoken letters (PAN, IFSC) ----------------------------------------------
_LETTER_WORDS = {
    "ए": "A", "बी": "B", "सी": "C", "डी": "D", "ई": "E", "एफ": "F", "जी": "G", "एच": "H", "आई": "I", "जे": "J", "के": "K",
    "एल": "L", "एम": "M", "एन": "N", "ओ": "O", "पी": "P", "क्यू": "Q", "आर": "R", "एस": "S", "टी": "T", "यू": "U", "वी": "V",
    "डब्ल्यू": "W", "एक्स": "X", "वाई": "Y", "जेड": "Z", "ज़ेड": "Z",
    "alpha": "A", "bravo": "B", "charlie": "C", "delta": "D", "echo": "E", "foxtrot": "F", "golf": "G", "hotel": "H",
    "india": "I", "juliet": "J", "kilo": "K", "lima": "L", "mike": "M", "november": "N", "oscar": "O", "papa": "P",
    "quebec": "Q", "romeo": "R", "sierra": "S", "tango": "T", "uniform": "U", "victor": "V", "whiskey": "W",
    "xray": "X", "yankee": "Y", "zulu": "Z",
}
_LETTER_WORDS = {_norm(k): v for k, v in _LETTER_WORDS.items()}


def spoken_to_code(text: str) -> str:
    """Alphanumeric identifiers ("ABCDE 1234 F", "ए बी सी डी ई एक दो तीन चार एफ") -> "ABCDE1234F". Single Latin letters and
    digit groups pass through; letter names/NATO words and digit words are converted."""
    out: list[str] = []
    repeat = 1
    for tok in _tokens(text):
        if tok in _REPEAT:
            repeat = _REPEAT[tok]; continue
        if tok in _LETTER_WORDS:
            out.append(_LETTER_WORDS[tok] * repeat)
        elif (d := _digit_word(tok)) is not None and not re.fullmatch(r"[a-z]", tok):
            out.append(str(d) * repeat)
        elif re.fullmatch(r"[a-z0-9]+", tok):
            out.append(tok.upper() if repeat == 1 else tok.upper() * repeat)
        repeat = 1
    return "".join(out)


# --- khasra / plot numbers ---------------------------------------------------
# "145/2", "145 बटा 2", "एक सौ पैंतालीस बटा दो", "145 by 2", "145 slash 2", "145 क", "145/2 ख"
_KHASRA_SEP = {_norm(k) for k in ("बटा", "bata", "batta", "by", "slash", "stroke", "upon", "/")}
_KHASRA_FILLER = {_norm(k) for k in ("khasra", "खसरा", "number", "नंबर", "नम्बर", "no", "plot", "प्लॉट", "survey",
                                     "सर्वे", "gata", "गाटा", "ka", "का", "is", "है", "मेरा", "my")}
_KHASRA_LETTERS = {"क": "क", "ख": "ख", "ग": "ग", "घ": "घ", "a": "A", "b": "B", "c": "C", "d": "D"}


def spoken_to_khasra(text: str) -> str | None:
    t = ascii_digits(unicodedata.normalize("NFC", text))
    t = re.sub(r"(\d)\s*/\s*(\d)", r"\1 / \2", t)
    parts, cur, letter = [], [], ""
    for tok in t.replace(",", " ").replace(".", " ").split():
        n = _norm(tok)
        m = re.fullmatch(r"(\d+)([कखगघabcd]?)", n)
        if n in _KHASRA_SEP:
            parts.append(cur); cur = []
        elif n in _KHASRA_FILLER:
            continue
        elif m and m.group(2):
            cur.append(m.group(1)); letter = _KHASRA_LETTERS.get(m.group(2), "")
        elif n in _KHASRA_LETTERS and cur:
            letter = _KHASRA_LETTERS[n]
        else:
            cur.append(n)
    parts.append(cur)
    nums = []
    for p in parts:
        if not p:
            return None
        v = int(p[0]) if len(p) == 1 and p[0].isdecimal() else spoken_to_int(" ".join(p))
        if v is None or v <= 0:
            return None
        nums.append(str(v))
    return "/".join(nums) + letter if nums and len(nums) <= 3 else None


# --- dates --------------------------------------------------------------------
_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7, "august": 8, "september": 9,
    "october": 10, "november": 11, "december": 12, "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
    "जनवरी": 1, "फरवरी": 2, "फ़रवरी": 2, "मार्च": 3, "अप्रैल": 4, "मई": 5, "जून": 6, "जुलाई": 7, "अगस्त": 8, "सितंबर": 9,
    "सितम्बर": 9, "अक्टूबर": 10, "नवंबर": 11, "नवम्बर": 11, "दिसंबर": 12, "दिसम्बर": 12,
}
_MONTHS = {_norm(k): v for k, v in _MONTHS.items()}


def _expand_year(y: int, this_year: int) -> int:
    """Two-digit year -> four-digit, relative to today: 20yy unless that is in the future (then 19yy)."""
    if y >= 100:
        return y
    return 2000 + y if 2000 + y <= this_year else 1900 + y


def spoken_to_date(text: str, today: "dt.date | None" = None) -> tuple[int, int, int] | None:
    """"15 August 1990" / "१५/०८/१९९०" / "fifteen august nineteen ninety" / "15-8-90" -> (day, month, year) or None."""
    this_year = (today or dt.date.today()).year
    t = ascii_digits(text.lower())
    m = re.search(r"\b(\d{1,2})\s*[/\-. ]\s*(\d{1,2})\s*[/\-. ]\s*(\d{4}|\d{2})\b", t)
    if m:
        d, mo, y = int(m[1]), int(m[2]), int(m[3])
        return d, mo, _expand_year(y, this_year)
    # "15th", "১৫ই", "১লা", "15ஆம்", "15వ": day numbers carry an ordinal ending in many languages
    toks = _tokens(re.sub(r"(\d+)[^\s\d/.\-]+", r"\1 ", ascii_digits(unicodedata.normalize("NFC", text))))
    month_idx = next((i for i, tk in enumerate(toks) if tk in _MONTHS), None)
    if month_idx is None:
        return None
    before = " ".join(toks[:month_idx]); after = " ".join(toks[month_idx + 1:])
    day, year = spoken_to_int(before), None
    ytoks = after.split()
    if len(ytoks) >= 2 and all(w in _EN_TENS or w in _EN_UNITS or w in _HI_NUM or w.isdecimal() for w in ytoks):
        # "nineteen ninety" style: two two-digit halves
        for cut in range(1, len(ytoks)):
            a, b = spoken_to_int(" ".join(ytoks[:cut])), spoken_to_int(" ".join(ytoks[cut:]))
            if a is not None and b is not None and 10 <= a <= 99 and 0 <= b <= 99:
                year = a * 100 + b; break
    if year is None:
        year = spoken_to_int(after)
    if day is None or year is None:
        return None
    year = _expand_year(year, this_year)
    return day, _MONTHS[toks[month_idx]], year


MONTH_NAMES = {
    "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November",
           "December"],
    "hi": ["जनवरी", "फ़रवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त", "सितंबर", "अक्टूबर", "नवंबर", "दिसंबर"],
}


# ---- language packs (lang/*.json): number words, scale words, months ------------------------------------------
def _install_packs() -> None:
    from . import langpacks
    for lang, p in langpacks.packs().items():
        w = p.get("words", {})
        LANG_DIGIT_WORDS[lang] = {_norm(k): int(v) for k, v in w.get("digits", {}).items()}
        _SCALES.update({_norm(k): int(v) for k, v in w.get("scales", {}).items()})
        _FILLER.update(_norm(k) for k in w.get("filler", []))
        months = w.get("months", [])
        if len(months) == 12:
            MONTH_NAMES[lang] = list(months)
            _MONTHS.update({_norm(m): i + 1 for i, m in enumerate(months)})
        _MONTHS.update({_norm(k): int(v) for k, v in w.get("month_aliases", {}).items()})
        _FRACTION_WORDS.update({_norm(k): str(v) for k, v in w.get("fractions", {}).items()})
        _FRACTION_MODS.update({_norm(k): str(v) for k, v in w.get("fraction_mods", {}).items()})
        _POINT.update(_norm(k) for k in w.get("point", []))
        _HECTARE.update(_norm(k) for k in w.get("hectare", []))
        _LOCAL_LAND_UNITS.update(_norm(k) for k in w.get("local_land_units", []))
        _KHASRA_SEP.update(_norm(k) for k in w.get("khasra_sep", []))
        _KHASRA_FILLER.update(_norm(k) for k in w.get("khasra_filler", []))


_install_packs()
