"""Scheme eligibility advisor: a short spoken interview (templates/scheme_advisor.json) is checked against rules
kept as data (schemes/*.json), one file per scheme, so keeping them current is editing JSON, not code.

A rule is one of
    {"fact": "age", "gte": 18, "lte": 40, "why": {...}}            facts are the interview's field ids
    {"fact": "occupation", "in": [...]} / {"eq": "No"} / {"gt": 0}
    {"any_of": [rule, rule], "otherwise": "maybe", "why": {...}}    at least one must hold
    {"check": {"en": "...", "hi": "..."}}                           something only the office can verify
and may be "soft": true when our question can only approximate the real criterion (e.g. we ask family income, the
scheme limits the worker's own income) -- failing a soft rule gives "maybe", never "no".

Result per scheme: likely (every rule holds) / maybe (a soft rule failed or a fact is unknown) / no.
This is guidance, not a decision: every scheme shows its source and the date its rules were last reviewed, and
anything older than STALE_DAYS is flagged on screen and by `python -m dwaniforms.eligibility --check`.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path

SCHEMES_DIR = Path(__file__).parent / "schemes"
STALE_DAYS = 180
# advisor answers that carry over into a form the citizen then fills (confirmed with one yes/no each)
PREFILL = ("gender", "category", "annual_income", "land_acres")
NOT_APPLICABLE = "N/A"     # fact of a question that wasn't asked because it didn't apply (widow? -> asked of women only)


@lru_cache(maxsize=1)
def load_schemes(directory: str | None = None) -> list[dict]:
    d = Path(directory) if directory else SCHEMES_DIR
    from . import langpacks
    return [langpacks.localize_tree(json.loads(p.read_text(encoding="utf-8"))) for p in sorted(d.glob("*.json"))]


def _num(v):
    try:
        return Decimal(str(v))
    except InvalidOperation:
        return None


def _rule(rule: dict, facts: dict) -> str:
    """'pass' | 'fail' | 'unknown'."""
    if "check" in rule:
        return "pass"
    if "any_of" in rule:
        results = [_rule(r, facts) for r in rule["any_of"]]
        if "pass" in results:
            return "pass"
        return "unknown" if "unknown" in results else "fail"
    v = facts.get(rule["fact"])
    if v in (None, ""):
        return "fail" if rule.get("unknown") == "no" else "unknown"
    if v == NOT_APPLICABLE:
        return "fail"                                   # the question didn't apply to this person
    if "in" in rule and v not in rule["in"]:
        return "fail"
    if "eq" in rule and v != rule["eq"]:
        return "fail"
    for op in ("gt", "gte", "lt", "lte"):
        if op in rule:
            n, lim = _num(v), Decimal(str(rule[op]))
            if n is None:
                return "unknown"
            ok = {"gt": n > lim, "gte": n >= lim, "lt": n < lim, "lte": n <= lim}[op]
            if not ok:
                return "fail"
    return "pass"


def evaluate(scheme: dict, facts: dict, lang: str = "en") -> dict:
    status, reasons, checks = "likely", [], []
    t = lambda d: (d or {}).get(lang) or (d or {}).get("en", "")
    for r in scheme["rules"]:
        if "check" in r:
            checks.append(t(r["check"]))
            continue
        res = _rule(r, facts)
        if res == "pass":
            continue
        soft = r.get("soft") or ("any_of" in r and r.get("otherwise") == "maybe")
        if res == "fail" and not soft:
            return {"status": "no", "reason": t(r.get("why"))}
        status = "maybe"
        reasons.append(t(r.get("why")))
    return {"status": status, "reasons": [x for x in reasons if x], "checks": checks}


def stale(scheme: dict, today: dt.date | None = None) -> bool:
    try:
        reviewed = dt.date.fromisoformat(scheme["last_reviewed"])
    except (KeyError, ValueError):
        return True
    return ((today or dt.date.today()) - reviewed).days > STALE_DAYS


def advise(facts: dict, lang: str = "en", templates: dict | None = None, today: dt.date | None = None) -> dict:
    t = lambda d: (d or {}).get(lang) or (d or {}).get("en", "")
    likely, maybe, no = [], [], 0
    for s in load_schemes():
        e = evaluate(s, facts, lang)
        if e["status"] == "no":
            no += 1
            continue
        form = s.get("form")
        item = {"id": s["id"], "name": t(s["name"]), "benefit": t(s["benefit"]), "apply": t(s["apply"]),
                "source": s["source"], "last_reviewed": s["last_reviewed"], "stale": stale(s, today),
                "reasons": e["reasons"], "checks": e["checks"],
                "form": form if (templates is None or form in (templates or {})) else None}
        (likely if e["status"] == "likely" else maybe).append(item)
    forms = []
    for item in likely + maybe:
        if item["form"] and item["form"] not in forms:
            forms.append(item["form"])
    return {"likely": likely, "maybe": maybe, "not_matched": no, "forms": forms}


def facts_of(session) -> dict:
    facts = {}
    for f in session.template.fields:
        if not session.applicable(f):
            facts[f.id] = NOT_APPLICABLE
        elif f.id in session.answers and session.answers[f.id].value != "":
            facts[f.id] = session.answers[f.id].value
    return facts


def prefill_for(advisor_session, template) -> dict:
    """Advisor answers that the next form has a field for (same id and, for choices, a matching option)."""
    out = {}
    facts = facts_of(advisor_session)
    for fid in PREFILL:
        if facts.get(fid) in (None, NOT_APPLICABLE):
            continue
        try:
            f = template.field_by_id(fid)
        except KeyError:
            continue
        if f.kind == "choice" and not any(o.value == facts[fid] for o in f.options):
            continue
        out[fid] = facts[fid]
    return out


def advisor_outcome(session, service=None, **_) -> dict:
    res = advise(facts_of(session), session.lang, getattr(service, "templates", None))
    titles = {}
    if service is not None:
        titles = {tid: service.templates[tid].title_in(session.lang) for tid in res["forms"] if tid in service.templates}
    names = ", ".join(i["name"] for i in res["likely"] + res["maybe"])
    n = len(res["likely"]) + len(res["maybe"])
    return {"type": "advisor", "title": "Schemes you may be able to get", **res, "form_titles": titles,
            "say_key": "advisor_result" if n else "advisor_none", "say_kw": {"count": n, "names": names},
            "disclaimer_key": "advisor_disclaimer"}


def main(argv=None) -> int:
    """--check: list schemes whose rules haven't been reviewed for STALE_DAYS (exit 1 if any)."""
    old = [s for s in load_schemes() if stale(s)]
    for s in old:
        print(f"STALE  {s['id']:10} last reviewed {s.get('last_reviewed')}  -> re-check {s['source']}")
    print(f"{len(load_schemes()) - len(old)} of {len(load_schemes())} schemes reviewed within {STALE_DAYS} days.")
    return 1 if old else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
