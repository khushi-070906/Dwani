"""Indian address parser: "H-24, gali no. 3, near Shiv mandir, Uttam Nagar" -> house, street, landmark, locality ...

Indian addresses have no fixed order and are often typed or spoken without commas. This reads one the way a postman does:
it looks for the words that mark each part ("near", "gali", "vill", "dist", "nagar", a pincode, a state) and takes what is
left as the locality or city. Hindi and English are both understood (Devanagari and Latin).

    parse("H-24, gali no. 3, near Shiv mandir, Uttam Nagar, New Delhi 110059")
    -> {"house": "H-24", "street": "Gali No. 3", "landmark": "Shiv Mandir", "landmark_relation": "near",
        "locality": "Uttam Nagar", "city": "New Delhi", "state": "Delhi", "pincode": "110059", "unparsed": []}

It never invents a part: a part it cannot place goes to "unparsed", so a form can ask the person about it. It is a helper
for filling a form, not a postal-address verifier (it does not know whether the street exists).
"""
from __future__ import annotations

import re
import unicodedata

from . import spoken

FIELDS = ("care_of", "house", "floor", "block", "street", "landmark", "locality", "village", "post_office", "tehsil",
          "district", "city", "state", "pincode")

STATES = {
    "andhra pradesh": "Andhra Pradesh", "arunachal pradesh": "Arunachal Pradesh", "assam": "Assam", "bihar": "Bihar",
    "chhattisgarh": "Chhattisgarh", "goa": "Goa", "gujarat": "Gujarat", "haryana": "Haryana",
    "himachal pradesh": "Himachal Pradesh", "jharkhand": "Jharkhand", "karnataka": "Karnataka", "kerala": "Kerala",
    "madhya pradesh": "Madhya Pradesh", "maharashtra": "Maharashtra", "manipur": "Manipur", "meghalaya": "Meghalaya",
    "mizoram": "Mizoram", "nagaland": "Nagaland", "odisha": "Odisha", "orissa": "Odisha", "punjab": "Punjab",
    "rajasthan": "Rajasthan", "sikkim": "Sikkim", "tamil nadu": "Tamil Nadu", "telangana": "Telangana", "tripura": "Tripura",
    "uttar pradesh": "Uttar Pradesh", "uttarakhand": "Uttarakhand", "west bengal": "West Bengal", "delhi": "Delhi",
    "new delhi": "Delhi", "nct of delhi": "Delhi", "jammu and kashmir": "Jammu and Kashmir", "jammu & kashmir": "Jammu and Kashmir",
    "ladakh": "Ladakh", "chandigarh": "Chandigarh", "puducherry": "Puducherry", "pondicherry": "Puducherry",
    "andaman and nicobar": "Andaman and Nicobar Islands", "lakshadweep": "Lakshadweep",
    "dadra and nagar haveli": "Dadra and Nagar Haveli and Daman and Diu", "daman and diu": "Dadra and Nagar Haveli and Daman and Diu",
    "u.p.": "Uttar Pradesh", "up": "Uttar Pradesh", "m.p.": "Madhya Pradesh", "mp": "Madhya Pradesh", "hp": "Himachal Pradesh",
    "uk": "Uttarakhand", "wb": "West Bengal", "tn": "Tamil Nadu", "ap": "Andhra Pradesh", "mh": "Maharashtra", "rj": "Rajasthan",
    "hr": "Haryana", "pb": "Punjab", "br": "Bihar", "gj": "Gujarat", "kl": "Kerala", "ka": "Karnataka", "ts": "Telangana",
    "जम्मू और कश्मीर": "Jammu and Kashmir", "उत्तर प्रदेश": "Uttar Pradesh", "मध्य प्रदेश": "Madhya Pradesh", "दिल्ली": "Delhi",
    "नई दिल्ली": "Delhi", "बिहार": "Bihar", "राजस्थान": "Rajasthan", "हरियाणा": "Haryana", "पंजाब": "Punjab",
    "महाराष्ट्र": "Maharashtra", "गुजरात": "Gujarat", "उत्तराखंड": "Uttarakhand", "झारखंड": "Jharkhand", "छत्तीसगढ़": "Chhattisgarh",
    "हिमाचल प्रदेश": "Himachal Pradesh", "पश्चिम बंगाल": "West Bengal", "ओडिशा": "Odisha", "कर्नाटक": "Karnataka", "केरल": "Kerala",
    "तमिलनाडु": "Tamil Nadu", "तेलंगाना": "Telangana", "आंध्र प्रदेश": "Andhra Pradesh", "असम": "Assam",
}
_AMBIGUOUS_STATE = {"up", "mp", "hp", "uk", "wb", "tn", "ap", "mh", "rj", "hr", "pb", "br", "gj", "kl", "ka", "ts"}   # abbreviations: only at the end

# big cities that are not states (so "New Delhi" is a city, not a locality). Lower-case.
CITIES = {"new delhi", "delhi", "mumbai", "pune", "kolkata", "chennai", "bengaluru", "bangalore", "hyderabad", "ahmedabad",
          "jaipur", "lucknow", "kanpur", "nagpur", "indore", "bhopal", "patna", "ghaziabad", "noida", "gurgaon", "gurugram",
          "faridabad", "agra", "varanasi", "meerut", "ludhiana", "amritsar", "chandigarh", "ranchi", "dehradun", "surat",
          "vadodara", "nashik", "kochi", "coimbatore", "madurai", "visakhapatnam", "bhubaneswar", "raipur", "guwahati",
          "नई दिल्ली", "दिल्ली", "मुंबई", "लखनऊ", "जयपुर", "पटना", "भोपाल", "इंदौर", "कानपुर", "आगरा", "वाराणसी", "गाज़ियाबाद", "ग़ाज़ियाबाद",
          "नोएडा", "गुड़गांव", "गुरुग्राम", "फरीदाबाद", "मेरठ", "पुणे", "नागपुर"}

# words that end the name of a locality / colony
_LOCALITY_WORD = ("nagar", "vihar", "colony", "enclave", "puri", "pura", "puram", "pur", "bagh", "garden", "extension", "ext",
                  "market", "gaon", "gram", "mohalla", "basti", "camp", "khera", "kheda", "chowk", "ganj", "town", "township",
                  "estate", "park", "city", "residency", "apartment", "apartments", "society", "heights", "kunj", "dham", "nivas",
                  "नगर", "विहार", "कॉलोनी", "कालोनी", "एन्क्लेव", "पुरी", "पुरा", "पुर", "बाग", "गार्डन", "एक्सटेंशन", "बाजार", "बाज़ार", "गांव",
                  "मोहल्ला", "बस्ती", "कैंप", "खेड़ा", "चौक", "गंज", "टाउन", "कुंज", "धाम", "निवास", "सोसाइटी")
_LANDMARK_NOUN = ("mandir", "temple", "masjid", "mosque", "gurudwara", "gurdwara", "church", "school", "college", "hospital",
                  "dispensary", "petrol pump", "pump", "metro station", "metro", "bus stand", "bus stop", "railway station",
                  "station", "mall", "police station", "thana", "post office", "bank", "atm", "park", "ground", "dargah",
                  "मंदिर", "मस्जिद", "गुरुद्वारा", "चर्च", "स्कूल", "कॉलेज", "अस्पताल", "पेट्रोल पंप", "मेट्रो", "बस स्टैंड", "स्टेशन", "थाना",
                  "बैंक", "पार्क", "मैदान", "दरगाह")

_REL_EN = r"near(?:by)?|nr\.?|opposite|opp\.?|behind|beside|besides|next\s+to|in\s+front\s+of|front\s+of|close\s+to|adjacent\s+to|above|below|back\s+side\s+of"
_REL_HI = r"के\s+पास|के\s+सामने|के\s+पीछे|के\s+बगल\s+में|के\s+नज़दीक|के\s+नजदीक|के\s+सामने\s+वाली|ke\s+paas|ke\s+pass|ke\s+samne|ke\s+peeche|ke\s+piche|ke\s+bagal\s+mein|ke\s+nazdeek"
_REL_NORMAL = {"near": "near", "nearby": "near", "nr": "near", "opposite": "opposite", "opp": "opposite", "behind": "behind",
               "beside": "beside", "besides": "beside", "next to": "beside", "in front of": "opposite", "front of": "opposite",
               "close to": "near", "adjacent to": "beside", "above": "above", "below": "below", "back side of": "behind",
               "के पास": "near", "के सामने": "opposite", "के पीछे": "behind", "के बगल में": "beside", "के नज़दीक": "near", "के नजदीक": "near",
               "ke paas": "near", "ke pass": "near", "ke samne": "opposite", "ke peeche": "behind", "ke piche": "behind",
               "ke bagal mein": "beside", "ke nazdeek": "near"}
_PRE = re.compile(rf"(?<![\w])(?P<rel>{_REL_EN})(?![\w])\s*", re.I)
_POST = re.compile(rf"(?P<what>[^,;\n|]+?)\s+(?P<rel>{_REL_HI})(?![\w])", re.I)

# markers of the parts, longest first, with the field they start. The value runs to the next marker or separator.
_RURAL = [
    (r"village|vill\.?|vpo|v\.p\.o\.?|gram|ग्राम|गांव|गाँव", "village"),
    (r"post\s+office|post|p\.?o\.?|पोस्ट", "post_office"),
    (r"tehsil|tahsil|teh\.?|tah\.?|tal\.?|taluka|taluk|तहसील|तालुका", "tehsil"),
    (r"district|dist\.?|distt\.?|zila|jila|जिला|ज़िला", "district"),
    (r"police\s+station|p\.?s\.?|thana|थाना", "thana"),
]
_CAREOF = re.compile(r"\b(?:c\s*/\s*o|care\s+of|s\s*/\s*o|d\s*/\s*o|w\s*/\s*o)\s*[:.]?\s*(?P<v>[^,;\n|]+)", re.I)
_HOUSE_PREFIX = r"(?:h\.?\s*no\.?|h\.?\s*n\.?|house\s*(?:no\.?|number)?|flat\s*(?:no\.?|number)?|plot\s*(?:no\.?|number)?|room\s*(?:no\.?|number)?|shop\s*(?:no\.?|number)?|unit\s*(?:no\.?)?|door\s*(?:no\.?)?|d\.?\s*no\.?|quarter\s*(?:no\.?)?|qtr\.?\s*(?:no\.?)?|makan\s*(?:no\.?|number)?|मकान\s*(?:नंबर|नं\.?|न\.?)?|घर\s*(?:नंबर|नं\.?)?|फ्लैट\s*(?:नंबर|नं\.?)?|प्लॉट\s*(?:नंबर|नं\.?)?|एच\s*न\.?)"
_HOUSE_CORE = r"(?:[A-Za-zऀ-ॿ]{1,2}\s*[-/.]?\s*)?\d+[A-Za-z]?(?:\s*[-/]\s*\d+[A-Za-z]?)*(?:\s*(?:ka|ki|के)\s*\d+)?"
_HOUSE = re.compile(rf"^\s*(?:{_HOUSE_PREFIX}\s*[:.\-#]?\s*)?(?P<v>{_HOUSE_CORE})(?![\w])", re.I)
_HOUSE_WORDS_ONLY = re.compile(rf"^\s*(?:{_HOUSE_PREFIX})\s*[:.\-#]?\s*(?P<v>[A-Za-z0-9\-/]+)", re.I)
_FLOOR = re.compile(r"\b(?P<v>(?:ground|first|second|third|fourth|fifth|top|basement|1st|2nd|3rd|4th|5th|\d+(?:st|nd|rd|th))\s+floor|"
                    r"floor\s*(?:no\.?)?\s*\d+|gf|ff|sf|tf)\b|(?P<h>(?:भूतल|पहली\s+मंज़िल|पहली\s+मंजिल|दूसरी\s+मंज़िल|दूसरी\s+मंजिल|तीसरी\s+मंज़िल|तीसरी\s+मंजिल))", re.I)
_BLOCK = re.compile(r"\b(?P<v>(?:block|blk|sector|sec|phase|pocket|ward|wing|tower|lane|ph)\.?\s*(?:no\.?|number)?\s*[-:]?\s*[A-Za-z]?\d*[A-Za-z]?)(?![\w])|"
                    r"(?P<h>(?:ब्लॉक|सेक्टर|फेज़|फेज|पॉकेट|वार्ड|विंग|टावर)\s*(?:नंबर|नं\.?)?\s*[-:]?\s*[A-Za-zऀ-ॿ]?\d*)", re.I)
_STREET = re.compile(r"\b(?P<v>(?:(?:gali|galli|lane|street|st\.|road|rd\.?|marg|path|rasta|sadak|main\s+road|cross)\s*(?:no\.?|number|nm\.?|n\.?)?\s*[-:]?\s*\d+[A-Za-z]?)|"
                     r"(?:\d+[A-Za-z]?\s*(?:th|nd|rd|st)?\s*(?:cross|main|street|road|lane|gali))|"
                     r"(?:[A-Z][A-Za-z.]*(?:\s+[A-Z][A-Za-z.]*){0,3}\s+(?:road|marg|street|lane|path|gali|chowk\s+road))|"
                     r"(?:gali|galli|lane|street|road|marg)\s+(?:no\.?\s*)?[A-Za-z]+)\b|"
                     r"(?P<h>(?:गली|गल्ली|लेन|सड़क|मार्ग|रोड)\s*(?:नंबर|नं\.?|संख्या)?\s*\d+|(?:[ऀ-ॿ]+\s+){1,3}(?:रोड|मार्ग|सड़क)|(?:गली|सड़क)\s+[ऀ-ॿ]+)", re.I)
_PIN = re.compile(r"(?<!\d)([1-9]\d{2}\s?\d{3})(?!\d)")
_SEP = re.compile(r"[,;\n|]+")


def _clean(v: str) -> str:
    v = re.sub(r"\s+", " ", v).strip(" ,.;:-–—|/\\()[]")
    return v


def _title(v: str) -> str:
    v = _clean(v)
    if not v or not re.search(r"[A-Za-z]", v):
        return v
    out = []
    for w in v.split():
        if re.fullmatch(r"[A-Za-z]-?\d+[A-Za-z]?(?:/\d+[A-Za-z]?)*", w) or re.fullmatch(r"[A-Z]{1,3}", w):
            out.append(w.upper())
        elif w.lower() in {"no", "no."}:
            out.append("No.")
        else:
            out.append(w[:1].upper() + w[1:].lower() if not w[:1].isdigit() else w)
    return " ".join(out)


def _house(v: str) -> str:
    v = _clean(v)
    v = re.sub(r"\s*([-/])\s*", r"\1", v)
    v = re.sub(r"^([A-Za-z]{1,2})\s*[.\s]\s*(\d)", r"\1-\2", v)
    v = re.sub(r"^([A-Za-z]{1,2})(\d)", r"\1-\2", v)
    return v.upper() if re.fullmatch(r"[A-Za-z0-9\-/ ]+", v) else v


def _is_locality(seg: str) -> bool:
    low = seg.lower()
    words = re.findall(r"[^\W\d_]+", low)
    return bool(words) and any(w in _LOCALITY_WORD or w.rstrip("s") in _LOCALITY_WORD for w in words[-3:])


def _is_landmark_noun(seg: str) -> bool:
    low = seg.lower()
    return any(re.search(rf"(?<![^\W\d_]){re.escape(n)}(?![^\W\d_])", low) for n in _LANDMARK_NOUN)


_CITY_STATES = {"delhi", "new delhi", "nct of delhi", "दिल्ली", "नई दिल्ली", "chandigarh", "puducherry", "pondicherry"}


def _state_at_end(text: str) -> tuple[str, str | None, str | None]:
    """Cut a state off the END of the text (longest name first); abbreviations only count when they are the last word."""
    t = text.rstrip(" ,.;-–—")
    low = t.lower()
    for name in sorted(STATES, key=len, reverse=True):
        if name in _AMBIGUOUS_STATE:
            m = re.search(rf"(?:^|[\s,.\-])({re.escape(name)})\.?$", low)
        else:
            m = re.search(rf"(?:^|[\s,.\-])({re.escape(name)})$", low)
        if m:
            cut = m.start(1)
            return t[:cut].rstrip(" ,.;-–—"), STATES[name], (t[cut:].strip(" .") if name in _CITY_STATES else None)
    return t, None, None


def parse(text: str) -> dict:
    """Break a free-text Indian address into its parts. Parts not found are absent; what could not be placed is in 'unparsed'."""
    raw = unicodedata.normalize("NFC", text or "")
    t = spoken.ascii_digits(raw)
    t = re.sub(r"\s+", " ", t.replace("\r", "\n")).strip()
    out: dict = {}
    unparsed: list[str] = []

    # 1. pincode (and anything like "PIN:" before it)
    m = list(_PIN.finditer(t))
    if m:
        out["pincode"] = re.sub(r"\s", "", m[-1].group(1))
        t = (t[:m[-1].start()] + " " + t[m[-1].end():])
        t = re.sub(r"(?i)\b(?:pin(?:\s*code)?|pincode|पिन(?:\s*कोड)?)\s*[:\-]?\s*(?=[,;\s]|$)", " ", t)
    # 2. state at the end ("... Delhi", "... U.P.")
    t, state, city_too = _state_at_end(t)
    if state:
        out["state"] = state
        if city_too:                                  # "New Delhi" is both the city and the state-level unit
            out["city"] = _title(city_too) if re.search(r"[A-Za-z]", city_too) else city_too
    # 3. care of
    m2 = _CAREOF.search(t)
    if m2:
        out["care_of"] = _title(m2.group("v"))
        t = t[:m2.start()] + " , " + t[m2.end():]
    # 4. "<name> ke paas" (Hindi landmark) and "near <name>" (English): the landmark runs to the next comma
    lm = None
    pm = _POST.search(t)
    if pm and not _PRE.search(t[:pm.start()]):
        what = re.sub(r"^.*[,;]\s*", "", pm.group("what")).strip()
        rel = re.sub(r"\s+", " ", pm.group("rel").lower())
        lm = (what, _REL_NORMAL.get(rel, "near"))
        t = t[:pm.end("what") - len(pm.group("what")) + pm.group("what").find(what) if what else pm.start()] + " , " + t[pm.end():]
    else:
        pr = _PRE.search(t)
        if pr:
            rest = t[pr.end():]
            end = _first_boundary(rest)
            val = rest[:end]
            lm = (val, _REL_NORMAL.get(re.sub(r"\s+", " ", pr.group("rel").lower().rstrip(".")), "near"))
            t = t[:pr.start()] + " , " + rest[end:]
    if lm and _clean(lm[0]):
        out["landmark"] = _title(lm[0]); out["landmark_relation"] = lm[1]

    # 5. rural markers: "Vill X, PO Y, Teh Z, Dist W"
    markers = "|".join(f"(?P<m{i}>{p})" for i, (p, _) in enumerate(_RURAL))
    rx = re.compile(rf"(?<![\w])(?:{markers})(?![A-Za-zऀ-ॿ])\s*[:.\-]?\s*", re.I)
    pos = [(mo.start(), mo.end(), next(_RURAL[i][1] for i in range(len(_RURAL)) if mo.group(f"m{i}"))) for mo in rx.finditer(t)]
    if pos:
        pieces = []
        for k, (s, e, fld) in enumerate(pos):
            nxt = pos[k + 1][0] if k + 1 < len(pos) else len(t)
            seg = t[e:nxt]
            cut = re.search(r"[,;\n|]", seg)
            val = seg[:cut.start()] if cut else seg
            tail = seg[cut.start():] if cut else ""
            if _clean(val):
                out.setdefault(fld, _title(val))
            pieces.append(tail)
        t = t[:pos[0][0]] + " , " + " , ".join(pieces)
    if "thana" in out:
        out.setdefault("police_station", out.pop("thana"))

    # 6. the rest, segment by segment
    leftovers: list[str] = []
    for seg in [s for s in _SEP.split(t) if _clean(s)]:
        rest = " " + seg + " "
        # house number at the start
        hm = _HOUSE.match(rest) if "house" not in out else None
        if hm and re.search(r"\d", hm.group("v")):
            out["house"] = _house(hm.group("v"))
            rest = rest[hm.end():]
        else:
            hw = _HOUSE_WORDS_ONLY.match(rest) if "house" not in out else None
            if hw:
                out["house"] = _house(hw.group("v")); rest = rest[hw.end():]
        for rx2, fld in ((_FLOOR, "floor"), (_BLOCK, "block"), (_STREET, "street")):
            mm = rx2.search(rest)
            if mm and fld == "block" and "block" in out:          # "Block C, Sector 12": keep both
                v = _title(mm.group("v") or mm.group("h"))
                if v and v not in out["block"]:
                    out["block"] += ", " + v
                rest = rest[:mm.start()] + " " + rest[mm.end():]
            elif mm and fld not in out:
                v = mm.group("v") or mm.group("h")
                if fld == "block" and not re.search(r"[A-Za-zऀ-ॿ]\w*\W*\w", v or "") and not re.search(r"\d|\b[A-Z]\b", v or ""):
                    continue
                out[fld] = _title(v)
                rest = rest[:mm.start()] + " " + rest[mm.end():]
        r = _clean(rest)
        r = re.sub(r"^(?:and|&|,|-)\s+", "", r, flags=re.I)
        if r:
            leftovers.append(r)

    # 7. leftovers: city / locality / landmark / unknown
    NOISE = {"india", "bharat", "भारत"}
    unknown: list[str] = []
    for seg in leftovers:
        low = seg.lower()
        if low in NOISE:
            continue
        if low in CITIES and "city" not in out:
            out["city"] = _title(seg) if re.search(r"[A-Za-z]", seg) else seg
        elif _is_landmark_noun(seg) and not _is_locality(seg) and "landmark" not in out:
            out["landmark"] = _title(seg); out["landmark_relation"] = "near"
        elif _is_locality(seg) and "locality" not in out:
            out["locality"] = _title(seg)
        else:
            unknown.append(seg)
    for i, seg in enumerate(unknown):
        has_place = any(k in out for k in ("house", "street", "block", "floor"))
        if "locality" not in out and "village" not in out and (len(unknown) > 1 and i == 0 or len(unknown) == 1 and has_place):
            out["locality"] = _title(seg)
        elif "city" not in out:
            out["city"] = _title(seg)
        elif "district" not in out:
            out["district"] = _title(seg)
        else:
            unparsed.append(seg)
    out["unparsed"] = unparsed
    return {k: out[k] for k in (*FIELDS, "landmark_relation", "police_station", "unparsed") if k in out}


def _first_boundary(rest: str) -> int:
    """Where a landmark phrase ends: the next comma, or the start of a locality/rural/house word run, or the end."""
    m = _SEP.search(rest)
    end = m.start() if m else len(rest)
    seg = rest[:end]
    # no commas ("near shiv mandir uttam nagar"): the landmark is the words up to and including a landmark noun
    if not m:
        for noun in sorted(_LANDMARK_NOUN, key=len, reverse=True):
            nm = re.search(rf"(?<![^\W\d_]){re.escape(noun)}(?![^\W\d_])", seg, re.I)
            if nm:
                return nm.end()
        words = seg.split()
        if len(words) > 2:
            return len(" ".join(words[:2])) + 0
    return end


def format_address(p: dict) -> str:
    """One clean line from parse()'s result, in the order a postman reads it."""
    state = p.get("state") if (p.get("state") or "").lower() != (p.get("city") or "").lower() else None
    rel = {"near": "Near", "opposite": "Opposite", "behind": "Behind", "beside": "Beside", "above": "Above", "below": "Below"}
    bits = [p.get("care_of") and f"C/o {p['care_of']}", p.get("house"), p.get("floor"), p.get("block"), p.get("street"),
            p.get("landmark") and (p["landmark"] if re.search(r"[\u0900-\u097f]", p["landmark"])
                                   else f"{rel.get(p.get('landmark_relation'), 'Near')} {p['landmark']}"), p.get("village") and f"Vill. {p['village']}",
            p.get("locality"), p.get("post_office") and f"P.O. {p['post_office']}", p.get("tehsil") and f"Teh. {p['tehsil']}",
            p.get("city"), p.get("district") and f"Dist. {p['district']}", state, p.get("pincode")]
    return ", ".join(b for b in bits if b)


def missing(p: dict) -> list[str]:
    """What a form usually still needs to ask: a place, a district or city, a state, a pincode."""
    need = []
    if not (p.get("house") or p.get("village") or p.get("locality") or p.get("street")):
        need.append("house_or_village")
    if not (p.get("city") or p.get("district")):
        need.append("city_or_district")
    for f in ("state", "pincode"):
        if not p.get(f):
            need.append(f)
    return need
