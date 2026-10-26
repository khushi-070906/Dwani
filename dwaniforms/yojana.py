"""DwaniYojana: "which government schemes might I get?" as a page anyone can open, with no sign-in and nothing stored.

The page asks a short list of questions (the same ones as the scheme advisor, templates/scheme_advisor.json), then sends the
answers here in ONE request. We check them against the advisor's own question list (a value the advisor would not accept is
refused), run the advisor's rules (eligibility.py, schemes/*.json), and send back the answer. No session is created, nothing is
written to disk or to a log, and the page's share card carries scheme names only, never the answers."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from . import eligibility
from .schema import FormTemplate

TEMPLATE_ID = "scheme_advisor"


class BadFacts(ValueError):
    pass


def _applies(field, facts: dict) -> bool:
    """The same `when` rule a spoken interview uses: a widow question only for women, a land question only for farmers."""
    w = field.when
    if not w:
        return True
    v = facts.get(w["field"])
    if v in (None, "", eligibility.NOT_APPLICABLE):
        return False
    if "in" in w:
        return v in w["in"]
    if "not_in" in w:
        return v not in w["not_in"]
    if "eq" in w:
        return v == w["eq"]
    return True


def clean_facts(template: FormTemplate, raw: dict) -> dict:
    """Keep only answers the advisor could have collected; refuse anything else."""
    if not isinstance(raw, dict) or len(raw) > 40:
        raise BadFacts("answers must be an object of at most 40 entries")
    facts: dict = {}
    for fid, value in raw.items():
        try:
            f = template.field_by_id(fid)
        except KeyError:
            raise BadFacts(f"unknown question: {str(fid)[:40]}")
        if f.kind == "number":
            try:
                n = Decimal(str(value))
            except InvalidOperation:
                raise BadFacts(f"{fid} must be a number")
            if not n.is_finite() or not (Decimal(str(f.params.get("minimum", 0))) <= n <= Decimal(str(f.params.get("maximum", 10**9)))):
                raise BadFacts(f"{fid} is out of range")
            facts[fid] = int(n) if n == n.to_integral_value() else float(n)
        elif f.kind == "choice":
            if value not in [o.value for o in f.options]:
                raise BadFacts(f"{fid} is not one of the choices")
            facts[fid] = value
        elif f.kind == "yesno":
            if value not in ("Yes", "No"):
                raise BadFacts(f"{fid} must be Yes or No")
            facts[fid] = value
        else:
            raise BadFacts(f"{fid} cannot be answered here")
    # a question that did not apply to this person (a widow question for a man) is "not applicable", exactly as in the interview
    for f in template.fields:
        if not _applies(f, facts):
            facts[f.id] = eligibility.NOT_APPLICABLE
    return facts


def advise(template: FormTemplate, raw: dict, lang: str, templates: dict | None = None) -> dict:
    res = eligibility.advise(clean_facts(template, raw), lang, templates)
    res["reviewed"] = min((i["last_reviewed"] for i in res["likely"] + res["maybe"]), default=None)
    return res


def _facts_used(rule: dict, out: set) -> None:
    if "fact" in rule:
        out.add(rule["fact"])
    for r in rule.get("any_of", []):
        _facts_used(r, out)


def questions(template: FormTemplate, lang: str) -> list[dict]:
    """The questions that matter: those whose answer some scheme's rules actually look at, in the interview's order.
    (The interview also asks for a social category; no scheme checks it, so asking it here would be a tap for nothing.)"""
    used: set = set()
    for s in eligibility.load_schemes():
        for r in s["rules"]:
            _facts_used(r, used)
    out = []
    for f in template.fields:
        if f.id not in used:
            continue
        prompt = f.prompt.get(lang) or f.prompt.get("en", "")
        out.append({"id": f.id, "kind": f.kind, "prompt": prompt, "required": f.required, "when": f.when,
                    "options": [o.value for o in f.options],
                    "min": f.params.get("minimum"), "max": f.params.get("maximum")})
    return out


# ---- speaking an answer ------------------------------------------------------------------------------------------------
# The page can listen (the browser turns speech into text) and asks the server what the words mean for ONE question. The
# understanding is the interview's own: the same number reader (Hindi and English number words), the same yes/no reader and
# the same option synonyms as the spoken forms. Nothing is stored.
INCOME_BANDS = (100000, 200000, 300000, 500000, 1000000)    # tops of the bands the page offers (the page shows their labels)
_DONT_KNOW = ("पता नहीं", "मालूम नहीं", "पता नही", "नहीं पता", "नही पता", "नहीं मालूम", "पता ही नहीं", "pata nahi", "pata nhi", "nahi pata",
              "malum nahi", "don't know", "dont know", "do not know", "not sure", "no idea", "idk")


def _match_option(field, text: str, lang: str):
    from .messages import _words
    from .spoken import ascii_digits

    def norm(x: str) -> str:
        return " " + " ".join(_words(ascii_digits(x))) + " "
    t = norm(text)
    found: dict[str, int] = {}           # option -> words in its longest matching synonym ("farm labour" beats "labour")
    for o in field.options:
        for s in [o.value, *o.synonyms.get(lang, []), *o.synonyms.get("en", [])]:
            n = norm(s)
            if n.strip() and n in t:
                found[o.value] = max(found.get(o.value, 0), len(n.split()))
    if not found:
        return None
    top = max(found.values())
    best = [v for v, n in found.items() if n == top]
    return best[0] if len(best) == 1 else None          # two options matched equally well: ask again


def understand(template: FormTemplate, qid: str, text: str, lang: str):
    """The value the spoken `text` gives for question `qid`, or None when it was not understood (the page then says so)."""
    from . import validators
    from .messages import parse_yes_no
    try:
        f = template.field_by_id(qid)
    except KeyError:
        raise BadFacts("unknown question")
    if qid not in {q["id"] for q in questions(template, lang)}:
        raise BadFacts("this question is not asked here")
    text = (text or "").strip()
    if not text or any(w in text.lower() for w in _DONT_KNOW):
        return None
    if f.kind == "number":
        ok, v, _ = validators.VALIDATORS["number"](text, **f.params)
        if not ok:
            return None
        try:
            n = Decimal(v)
        except InvalidOperation:
            return None
        if qid == "annual_income":
            return next((b for b in INCOME_BANDS if n <= b), INCOME_BANDS[-1])
        return int(n) if n == n.to_integral_value() else float(n)      # "2.5 acres" is 2.5, not an error
    if f.kind == "choice":
        return _match_option(f, text, lang)
    if f.kind == "yesno":
        yn = parse_yes_no(text)
        return None if yn is None else ("Yes" if yn else "No")
    return None
