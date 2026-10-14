"""Indian name matcher: "Mohd Aslam", "Mohammad Aslam" and "मोहम्मद अस्लम" are one person.

Two documents that spell the same name differently (a ration card says "Mohd Aslam", the Aadhaar says "Mohammad Aslam") is
one of the commonest reasons a government application is sent back. This module says whether two spellings are
probably one person, and why, so a form can ask "is this the same person?" instead of rejecting.

How: each name is cut into words; titles ("Shri", "Smt", "Mr"), and everything after "S/O, D/O, W/O" are set aside;
every word is turned into Latin letters (Devanagari and the other Indian scripts are transliterated by sound) and then into a
sound-key that ignores the spelling habits of Indian English (aa/a, ee/i, w/v, ph/f, sh/s, double letters, h after a
consonant, trailing a). Well-known variants (Mohd / Muhammad, Kumar / Kr, Singh / Sinh, Shrivastava / Srivastava ...) are
folded together. Then the words of the two names are paired up: order does not matter ("Sharma Anil" = "Anil Sharma"), an
initial matches a full word ("A. K. Sharma" ~ "Anil Kumar Sharma"), and a missing middle name costs little.

The answer is a score from 0 to 1 and a verdict: "same" (0.92+), "probably" (0.80+: show both and ask), "different".
It is a helper for a human or a form to confirm, never a decision about who someone is.
"""
from __future__ import annotations

import difflib
import re
import unicodedata

# ---- other scripts -> Devanagari by code point (the Indian scripts share one layout) ---------------------------------
_BRAHMIC_BASES = {0x0980: "bn", 0x0A00: "pa", 0x0A80: "gu", 0x0B00: "or", 0x0B80: "ta", 0x0C00: "te", 0x0C80: "kn", 0x0D00: "ml"}


def _to_devanagari(text: str) -> str:
    out = []
    for ch in text:
        o = ord(ch)
        if o == 0x0A70:                       # Gurmukhi tippi (nasal), outside the shared layout
            out.append("ं"); continue
        if o == 0x0A71:                       # Gurmukhi addak (doubles the next sound): the double is collapsed anyway
            continue
        for base in _BRAHMIC_BASES:
            if base <= o < base + 0x80:
                out.append(chr(0x0900 + (o - base)))
                break
        else:
            out.append(ch)
    return "".join(out)


_CONS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n", "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "n",
    "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n", "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
    "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m", "य": "y", "र": "r", "ल": "l", "ळ": "l", "व": "v",
    "श": "sh", "ष": "sh", "स": "s", "ह": "h", "ऩ": "n", "ऱ": "r", "ऴ": "l",     # the last three are Tamil ன ற ழ after mapping
}
_NUKTA = {"क": "q", "ख": "kh", "ग": "g", "ज": "z", "ड": "r", "ढ": "rh", "फ": "f", "य": "y"}
_VOWELS = {"अ": "a", "आ": "aa", "इ": "i", "ई": "ii", "उ": "u", "ऊ": "uu", "ऋ": "ri", "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au",
           "ऑ": "o", "ऍ": "e", "ऎ": "e", "ऒ": "o"}
_MATRAS = {"ा": "aa", "ि": "i", "ी": "ii", "ु": "u", "ू": "uu", "ृ": "ri", "े": "e", "ै": "ai", "ो": "o", "ौ": "au", "ॉ": "o",
           "ॅ": "e", "ॆ": "e", "ॊ": "o"}
_VIRAMA, _NUKTA_MARK = "्", "़"


def transliterate(word: str) -> str:
    """Latin letters for a word in any Indian script; anything already Latin passes through unchanged."""
    w = unicodedata.normalize("NFC", _to_devanagari(word))
    if not any("\u0900" <= c <= "\u097f" for c in w):
        return w
    w = w.replace("ज्ञ", "gy")
    # units: (letters, kind) where kind is "C" (consonant with the inherent a still open), "V" (a vowel / explicit vowel
    # sign was written) or "X" (anything else). The inherent "a" is dropped at the end of a word and in the middle when a
    # syllable with a vowel comes before and after it (असलम -> aslam, गुरप्रीत -> gurpreet, कमला -> kamla).
    units: list[list] = []
    i, n = 0, len(w)
    while i < n:
        c = w[i]
        if c in _CONS:
            base = _CONS[c]
            if i + 1 < n and w[i + 1] == _NUKTA_MARK:
                base = _NUKTA.get(c, base); i += 1
            nxt = w[i + 1] if i + 1 < n else ""
            if nxt == _VIRAMA:
                units.append([base, "H"]); i += 1                  # half consonant: no vowel at all
            elif nxt in _MATRAS:
                units.append([base + _MATRAS[nxt], "V"]); i += 1
            else:
                units.append([base, "C"])
        elif c in _VOWELS:
            units.append([_VOWELS[c], "V"])
        elif c in ("ं", "ँ"):
            units.append(["n", "X"])
        elif c == "ः":
            units.append(["h", "X"])
        elif c in (_VIRAMA, _NUKTA_MARK) or c in _MATRAS:
            pass
        elif c.isalpha() or c.isdigit():
            units.append([c, "X"])
        i += 1
    out = []
    for k, u in enumerate(units):
        txt, kind = u
        if kind != "C":
            out.append(txt); continue
        prev = units[k - 1][1] if k else ""
        nxt = next((x[1] for x in units[k + 1:] if x[1] != "H"), "")
        drop = k == len(units) - 1 or (prev in ("V", "C") and nxt in ("V", "C"))
        out.append(txt if drop else txt + "a")
        if drop:
            u[1] = "D"                               # a dropped syllable no longer counts as one that has a vowel
    return "".join(out)


# ---- sound keys --------------------------------------------------------------------------------------------------------
def _strip_marks(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


_RULES = [
    (r"chh|ch", "C"), (r"sh|ss|ś|ṣ", "S"), (r"ph", "f"), (r"kh", "k"), (r"gh", "g"), (r"th", "t"), (r"dh", "d"),
    (r"bh", "b"), (r"jh", "j"), (r"ck|q|c", "k"), (r"w", "v"), (r"z", "j"), (r"x", "ks"),
    (r"aa|ah\b", "a"), (r"ee|ii|ie|ey\b|y\b", "i"), (r"oo|uu|ou", "u"), (r"ai|ay|ei", "e"), (r"au|aw", "o"),
    (r"([a-zA-Z])\1+", r"\1"),
]


def sound(word: str) -> str:
    """The sound-key of ONE word (Latin or any Indian script)."""
    w = _strip_marks(transliterate(word)).lower()
    w = re.sub(r"[^a-z]", "", w.replace("ṭ", "t").replace("ḍ", "d"))
    for pat, rep in _RULES:
        w = re.sub(pat, rep, w)
    w = w.replace("S", "s").replace("C", "c")
    if len(w) > 3 and w.endswith("a"):          # Sharma / Sharm, Ashoka / Ashok
        w = w[:-1]
    return w


def _skeleton(key: str) -> str:
    if len(key) <= 3:
        return key
    return key[0] + re.sub(r"[aeiou]", "", key[1:])


_ALIASES: dict[str, str] = {}
_SPELLING: dict[str, str] = {}      # sound-key of a well-known name -> the spelling shown


def _alias(canonical: str, variants: str) -> None:
    c = sound(canonical)
    _SPELLING[c] = canonical.title()
    for v in variants.split():
        _ALIASES[sound(v)] = c


_alias("muhammad", "mohd mohammad mohammed mohamed mohamad mohmmad mohmad mahomed muhammed muhamad md mhd mohmed मोहम्मद मुहम्मद मोहम्मद्")
_alias("kumar", "kumar kr kumaar कुमार")
_alias("kumari", "kumari kumri कुमारी km")
_alias("singh", "singh sinh sing सिंह")
_alias("kaur", "kaur kour कौर")
_alias("devi", "devi dewi देवी")
_alias("shrivastava", "srivastava shrivastav srivastav shrivastava श्रीवास्तव")
_alias("agarwal", "agrawal aggarwal agarwal agarval अग्रवाल")
_alias("mishra", "mishra misra mishr मिश्रा मिश्र")
_alias("verma", "verma varma वर्मा")
_alias("sharma", "sharma sarma शर्मा")
_alias("yadav", "yadav yadava यादव")
_alias("hussain", "hussain hussein husain hossain हुसैन")
_alias("ahmad", "ahmad ahmed अहमद")
_alias("khatoon", "khatoon khatun khatoom खातून")
_alias("begum", "begum begam बेगम")
_alias("prasad", "prasad prasaad प्रसाद")
_alias("chandra", "chandra chander chandar chand चंद्र")
_alias("lakshmi", "lakshmi laxmi lakshmi लक्ष्मी")
_alias("krishna", "krishna krishan kishan कृष्ण")
_alias("ramesh", "ramesh ramesh रमेश")

TITLES = {"shri", "shree", "sri", "smt", "shrimati", "kumari", "km", "mr", "mrs", "ms", "miss", "dr", "late", "sh", "mast",
          "master", "haji", "hajji", "moulvi", "maulana", "pandit", "pt", "श्री", "श्रीमती", "सुश्री", "स्व", "स्वर्गीय", "डॉ", "पंडित",
          "हाजी", "मौलाना"}
_RELATION = re.compile(r"\b(?:s\s*/\s*o|d\s*/\s*o|w\s*/\s*o|c\s*/\s*o|son\s+of|daughter\s+of|wife\s+of|husband\s+of)\b|"
                       r"(?:^|\s)(?:पुत्र|पुत्री|पत्नी|सुपुत्र|सुपुत्री|द्वारा)(?=\s|$)", re.I)

# Hindi forms of these must not count as titles when they are the name itself (Kumari is also a name): keep them out
# only when other words remain.


def parts(name: str) -> list[str]:
    """The name's words: titles and 'son of ...' dropped, punctuation gone."""
    name = unicodedata.normalize("NFC", name or "")
    name = _RELATION.split(name, maxsplit=1)[0]
    name = re.sub(r"[.,;:()\[\]{}\"'`’‘_/\\|-]+", " ", name)
    words = [w for w in name.split() if w]
    kept = [w for w in words if w.lower() not in TITLES and w not in TITLES]
    return kept or words


def keys(name: str) -> list[str]:
    out = []
    for w in parts(name):
        k = sound(w)
        if k:
            out.append(_ALIASES.get(k, k))
    return out


def canonical(name: str) -> str:
    """One spelling for display / comparison: the Latin form of each word, title-cased."""
    out = []
    for w in parts(name):
        k = sound(w)
        if not k:
            continue
        if k in _ALIASES:
            out.append(_SPELLING.get(_ALIASES[k], _ALIASES[k].title()))
        else:
            latin = re.sub(r"[^A-Za-z]", "", _strip_marks(transliterate(w)))
            out.append(latin.title() if latin else w)
    return " ".join(out)


# ---- scoring -----------------------------------------------------------------------------------------------------------
def _word_sim(a: str, b: str, ia: bool, ib: bool) -> float:
    """Similarity of two sound-keys (ia / ib: that word was an initial, like the A of "A. Sharma")."""
    if a == b:
        return 1.0
    if ia or ib:
        return 0.82 if a[:1] == b[:1] else 0.0
    full = difflib.SequenceMatcher(None, a, b).ratio()
    sk = difflib.SequenceMatcher(None, _skeleton(a), _skeleton(b)).ratio()
    s = 0.6 * full + 0.4 * sk
    if _skeleton(a) == _skeleton(b) and len(a) != len(b) and min(len(a), len(b)) >= 4:
        s = max(s, 0.93)                   # a vowel added or dropped (Aslam / Asalam); a vowel CHANGED (Rahul / Rahil) is not
    return s


def match(a: str, b: str) -> dict:
    """Compare two names. -> {score, verdict: same|probably|different, reasons: [...], a: [...], b: [...]}"""
    pa, pb = parts(a), parts(b)
    ka, kb = keys(a), keys(b)
    # a word that is a single letter in the source is an initial
    ia = [len(re.sub(r"[^\w]", "", w)) == 1 for w in pa][:len(ka)]
    ib = [len(re.sub(r"[^\w]", "", w)) == 1 for w in pb][:len(kb)]
    ia += [False] * (len(ka) - len(ia)); ib += [False] * (len(kb) - len(ib))
    result = {"a": canonical(a), "b": canonical(b), "reasons": []}
    if not ka or not kb:
        return {**result, "score": 0.0, "verdict": "different", "reasons": ["a name is empty"]}

    pairs = sorted(((_word_sim(x, y, ia[i], ib[j]), i, j) for i, x in enumerate(ka) for j, y in enumerate(kb)), reverse=True)
    used_a: set[int] = set(); used_b: set[int] = set(); chosen = []
    for s, i, j in pairs:
        if s >= 0.6 and i not in used_a and j not in used_b:
            used_a.add(i); used_b.add(j); chosen.append((s, i, j))
    short, long_ = (len(ka), len(kb)) if len(ka) <= len(kb) else (len(kb), len(ka))
    matched = sum(s for s, _, _ in chosen)
    score = matched / short if short else 0.0
    # words in one name with no partner in the other: a missing middle name is fine, a different surname is not
    left_a = [ka[i] for i in range(len(ka)) if i not in used_a]
    left_b = [kb[j] for j in range(len(kb)) if j not in used_b]
    extra = len(left_a) + len(left_b)
    if short == long_ and extra:
        score *= 0.85                        # same number of words, but some did not pair
    elif extra > abs(len(ka) - len(kb)):
        score *= 0.9
    elif extra:
        score *= 1 - min(0.08, 0.04 * extra)   # one name just has more words
    joined = "".join(ka) == "".join(kb) or "".join(sorted(ka)) == "".join(sorted(kb))
    if joined:
        score = max(score, 0.95)             # Ravi Shankar / Ravishankar: the same letters, split differently
    elif any(s < 0.72 and not (ia[i] or ib[j]) for s, i, j in chosen):
        score = min(score, 0.7)              # one word is a different name (Aslam / Salim): a matching surname must not hide it
    elif any(s < 0.9 and not (ia[i] or ib[j]) for s, i, j in chosen):
        score = min(score, 0.89)             # one word is only close (Rahul / Rahil): a good surname must not hide it
    real = [t for t in chosen if not (ia[t[1]] or ib[t[2]]) and t[0] >= 0.8]
    if joined:
        pass
    elif not real:
        score = min(score, 0.6)              # initials alone never prove anything
    elif len(real) == 1 and long_ >= 2 and short >= 2 and any(ia[t[1]] or ib[t[2]] for t in chosen):
        score = min(score, 0.85)             # one real word plus initials: ask
    if short == 1 and long_ > 1 and not joined:
        score = min(score, 0.85)             # one word against a full name ("Aslam" vs "Mohd Aslam"): ask, don't assume
    score = round(max(0.0, min(1.0, score)), 3)

    for s, i, j in chosen:
        x, y = pa[i] if i < len(pa) else ka[i], pb[j] if j < len(pb) else kb[j]
        if s == 1.0 and x.lower() != y.lower():
            result["reasons"].append(f'"{x}" and "{y}" are the same name spelled differently')
        elif ia[i] or ib[j]:
            result["reasons"].append(f'"{x}" is an initial of "{y}"' if ia[i] else f'"{y}" is an initial of "{x}"')
        elif s < 1.0:
            result["reasons"].append(f'"{x}" is close to "{y}"')
    for w in left_a:
        result["reasons"].append(f'only the first name has "{w}"')
    for w in left_b:
        result["reasons"].append(f'only the second name has "{w}"')
    verdict = "same" if score >= 0.92 else "probably" if score >= 0.80 else "different"
    return {**result, "score": score, "verdict": verdict}


def same_person(a: str, b: str) -> bool:
    return match(a, b)["verdict"] == "same"
