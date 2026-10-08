"""Pull the department, place and date out of a spoken complaint / RTI request, so the citizen is asked
"I noted the department as Electricity. Is this correct?" instead of three more open questions.

Everything here only SUGGESTS: each value is read back and confirmed (session.py), and a wrong guess just
falls back to asking normally. So the rules prefer saying nothing over guessing:
  * departments: keywords from data/departments.json; longest keywords match first and "use up" their words
    ("street light" -> Municipal, not "light" -> Electricity); a tie between departments suggests nothing.
  * places: the word after "village / गाँव / ग्राम", "tehsil / तहसील", "district / जिला" (and "X गाँव", "X district").
  * dates: a date written or spoken near a month name, or कल/परसों/yesterday -- never a future date.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from functools import lru_cache
from pathlib import Path

from .messages import _words
from .spoken import _norm, spoken_to_date

DATA = Path(__file__).parent / "data"


@lru_cache(maxsize=1)
def departments() -> dict:
    deps = json.loads((DATA / "departments.json").read_text(encoding="utf-8"))["departments"]
    from . import langpacks                       # a complaint said in Bengali / Tamil / ... names its department too
    for p in langpacks.packs().values():
        for dep, info in deps.items():
            info["words"] = [*info["words"], *p.get("options", {}).get(dep, [])[1:]]
    return deps


def _toks(text: str) -> list[str]:
    return [_norm(w) for w in _words(text)]


def guess_department(text: str) -> str | None:
    toks = _toks(text)
    used = [False] * len(toks)
    scores: dict[str, float] = {}
    keywords = sorted(((tuple(_toks(w)), dep) for dep, info in departments().items() for w in info["words"]),
                      key=lambda kw: -len(kw[0]))
    for kw, dep in keywords:
        n = len(kw)
        if not n:
            continue
        for i in range(len(toks) - n + 1):
            if tuple(toks[i:i + n]) == kw and not any(used[i:i + n]):
                for j in range(i, i + n):
                    used[j] = True
                scores[dep] = scores.get(dep, 0) + 1 + 0.5 * (n - 1)
    if not scores:
        return None
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return None                                   # ambiguous: ask instead of guessing
    return ranked[0][0]


_STOP = {_norm(w) for w in ("में", "के", "का", "की", "से", "है", "हैं", "पर", "को", "ने", "और", "mein", "me", "ke", "ka", "ki",
                             "se", "hai", "par", "ko", "the", "of", "in", "at", "and", "near", "पास", "paas")}
_PLACE_RULES = [  # (label in the form language, words BEFORE the name, words AFTER the name)
    ("Village", {"village", "gaon", "gaanv", "gram", "ग्राम", "गाँव", "गांव", "गावं", "मोहल्ला", "mohalla", "कस्बा"}, {"village", "गाँव", "गांव"}),
    ("Tehsil", {"tehsil", "तहसील", "block", "ब्लॉक", "ब्लाक"}, {"tehsil", "तहसील"}),
    ("District", {"district", "zila", "jila", "जिला", "ज़िला", "जनपद", "जिले", "ज़िले"}, {"district", "जिले", "ज़िले", "जिला", "जनपद"}),
]


def guess_place(text: str) -> tuple[str, str] | None:
    """('Village Rampur, District Sitapur', 'रामपुर गाँव ... सीतापुर जिला') or None. Names stay as spoken."""
    raw = [w for w in re.split(r"[\s,.;:।!?()]+", text) if w]
    norm = [_norm(w) for w in raw]
    found: list[tuple[str, str]] = []
    for label, before, after in _PLACE_RULES:
        b = {_norm(x) for x in before}
        a = {_norm(x) for x in after}
        name = None
        for i, w in enumerate(norm):
            if w in b and i + 1 < len(raw) and norm[i + 1] not in _STOP and norm[i + 1] not in b:
                name = raw[i + 1]
                break
            if w in a and i > 0 and norm[i - 1] not in _STOP and norm[i - 1] not in a and norm[i - 1] not in b:
                name = raw[i - 1]
                break
        if name and not any(name == n for _, n in found):
            found.append((label, name.strip("'\"")))
    if not found:
        return None
    value = ", ".join(f"{label} {name}" for label, name in found)
    return value, ", ".join(name for _, name in found)


_REL = {_norm(k): v for k, v in {"कल": 1, "kal": 1, "yesterday": 1, "परसों": 2, "parso": 2, "parson": 2,
                                  "आज": 0, "aaj": 0, "today": 0}.items()}
_MONTH_RE = re.compile(r"(january|february|march|april|may|june|july|august|september|october|november|december|"
                       r"jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec|जनवरी|फरवरी|फ़रवरी|मार्च|अप्रैल|मई|जून|जुलाई|"
                       r"अगस्त|सितंबर|सितम्बर|अक्टूबर|नवंबर|नवम्बर|दिसंबर|दिसम्बर)", re.I)


def guess_date(text: str, today: dt.date | None = None) -> str | None:
    """ISO date of the incident, or None. Only a small window around a month name is read, so "2 baar 15 August"
    gives the 15th, not the 17th."""
    today = today or dt.date.today()
    candidates = []
    for m in re.finditer(r"\b\d{1,2}\s*[/\-.]\s*\d{1,2}\s*[/\-.]\s*\d{2,4}\b", text):
        candidates.append(m.group(0))
    words = text.split()
    for i, w in enumerate(words):
        if _MONTH_RE.fullmatch(w.strip(".,।").lower()) or _MONTH_RE.fullmatch(w.strip(".,।")):
            window = words[max(0, i - 1): i + 3]
            candidates.append(" ".join(window))
    for c in candidates:
        p = spoken_to_date(c, today)
        if p is None:
            p = _day_month_only(c, today)              # "15 August ko ..." -- people rarely say the year
        if p:
            d, mo, y = p
            try:
                value = dt.date(y, mo, d)
            except ValueError:
                continue
            if value <= today:
                return value.isoformat()
    toks = _toks(text)
    for t in toks:
        if t in _REL:
            return (today - dt.timedelta(days=_REL[t])).isoformat()
    return None


def _day_month_only(window: str, today: dt.date):
    from .spoken import _MONTHS, spoken_to_int
    toks = _toks(window)
    for i, t in enumerate(toks):
        if t in _MONTHS and i > 0:
            day = spoken_to_int(toks[i - 1])
            if day and 1 <= day <= 31:
                mo = _MONTHS[t]
                y = today.year if (mo, day) <= (today.month, today.day) else today.year - 1
                return day, mo, y
    return None


def extract_for(text: str, lang: str, template, today: dt.date | None = None) -> dict:
    """{field_id: (value, native)} for the template's extract targets (by field kind / id)."""
    out = {}
    targets = (template.extract or {}).get("fields", {})
    for fid, what in targets.items():
        if what == "department":
            dep = guess_department(text)
            if dep:
                f = template.field_by_id(fid)
                if any(o.value == dep for o in f.options):
                    out[fid] = (dep, dep)
        elif what == "place":
            p = guess_place(text)
            if p:
                out[fid] = p
        elif what == "date":
            d = guess_date(text, today)
            if d:
                out[fid] = (d, d)
    return out
