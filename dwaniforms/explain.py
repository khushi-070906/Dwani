"""Samjhao: a paper someone received -- a bill, a notice, a summons -- explained in their language.

The person says what kind of paper it is (and, if it has one, the date and the amount printed on it). The
explanation itself is plain data in papers/*.json, written by hand in English and Hindi and in every language pack,
so a reviewer can correct a sentence without touching code. Nothing here guesses: no model reads the paper, and the
same paper always gets the same advice. Every paper also says how to spot the fraud that is commonly dressed up as it.
"""
from __future__ import annotations

import datetime as dt
import json
from functools import lru_cache
from pathlib import Path

from . import langpacks

PAPER_DIR = Path(__file__).parent / "papers"
FALLBACK = "Something else"
SECTIONS = ("do", "if_wrong", "beware", "help")


@lru_cache(maxsize=1)
def papers() -> dict[str, dict]:
    """Option value ("Electricity bill") -> that paper, with every pack language filled in."""
    out = {}
    for p in sorted(PAPER_DIR.glob("*.json")):
        d = langpacks.localize_tree(json.loads(p.read_text(encoding="utf-8")))
        out[d["option"]] = d
    return out


def _in(x: dict, lang: str) -> str:
    return x.get(lang) or x["en"]


def days_until(iso: str, today: dt.date) -> int | None:
    try:
        return (dt.date.fromisoformat(iso) - today).days
    except ValueError:
        return None


def explain_outcome(session, today: dt.date | None = None, **_) -> dict:
    today = today or dt.date.today()
    lang = session.lang
    a = session.answers.get("paper")
    p = papers().get(a.value if a else "") or papers()[FALLBACK]
    out = {"type": "explain", "title": "What this paper means", "paper": p["id"], "option": p["option"],
           "what": _in(p["what"], lang), **{k: [_in(x, lang) for x in p[k]] for k in SECTIONS},
           "date_role": p.get("date_role"), "last_reviewed": p.get("last_reviewed"),
           # spoken: what it is, and the fraud to watch for -- the one thing worth hearing even if nothing is read
           "say_key": "outcome_explain", "say_text": [_in(p["what"], lang), *[_in(x, lang) for x in p["beware"][:1]]],
           "say_more": [],
           "disclaimer_key": "explain_disclaimer"}
    d = session.answers.get("due_date")
    if d and d.value:
        days = days_until(d.value, today)
        if days is not None:
            out.update(date=d.value, days=days)
            out["say_more"].append(["explain_ahead", {"date": d.value, "days": days}] if days > 0 else
                                   ["explain_today", {}] if days == 0 else ["explain_passed", {"date": d.value}])
    m = session.answers.get("amount")
    if m and m.value:
        out["amount"] = m.value
    return out
