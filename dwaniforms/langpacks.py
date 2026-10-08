"""Hand-written language packs: one JSON file per language in dwaniforms/lang/.

Why: the online version has no translation model (it runs on a small server), so until now it offered only the two
languages whose questions were written by hand, Hindi and English. A pack gives a language everything the app says or
shows -- questions, labels, system messages, screen text, scheme details, answer words (yes / no / skip, digits,
months) -- written once, by a person, and reviewed by a native speaker. The kiosk (with its translation model) uses
the pack first and only machine-translates what a pack doesn't cover.

A pack is plain data so that a reviewer who doesn't code can correct it. Shape (see lang/bn.json):

    lang, name, speech (BCP-47 tag for speech recognition / voices), status (draft | reviewed), reviewed_by
    messages   {key: text}            same keys and {placeholders} as messages.MESSAGES["en"]
    ui         {key: text}            screen text, same keys as the English UI table in static/app.html
    text       {English: text}        every question, label, title, description, scheme line and next step,
                                      keyed by the English original (so a changed English sentence shows up as missing)
    options    {value: [spoken forms]} the first is the name shown and read back for that option
    words      yes, no, skip, skip_phrases, benign_negations, digits {word: 0-10}, scales {word: 100|1000|...},
               months [12 names], month_aliases {word: 1-12}, filler [words ignored inside numbers]
    formats    area ("{ha} ... {acres} ...")
    categories {AAY|PHH|NPHH: name}   ration card categories

`python -m dwaniforms.langpacks check` lists what each pack is missing; tests/test_dwaniforms_langpacks.py fails the
build if a pack is incomplete or a {placeholder} was lost.
"""
from __future__ import annotations

import json
import re
import sys
from functools import lru_cache
from pathlib import Path

PACK_DIR = Path(__file__).parent / "lang"
BUILT_IN = ("hi", "en")          # written inside the code itself (messages.py, templates, app.html)


@lru_cache(maxsize=1)
def packs() -> dict[str, dict]:
    out = {}
    for p in sorted(PACK_DIR.glob("*.json")):
        if p.name.startswith("_"):
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        out[d["lang"]] = d
    return out


def langs() -> list[str]:
    """Languages with hand-written text: usable without a translation model."""
    return [*BUILT_IN, *packs()]


def text(lang: str, en: str) -> str | None:
    p = packs().get(lang)
    return p["text"].get(en) if p else None


def localize(d: dict | None) -> dict:
    """{"en": "...", "hi": "..."} -> the same dict with every pack language filled in from its text table."""
    d = dict(d or {})
    en = d.get("en")
    if isinstance(en, str):
        for lang, p in packs().items():
            if lang not in d and en in p["text"]:
                d[lang] = p["text"][en]
    return d


def localize_label(label: str, labels: dict | None) -> dict:
    """A field's English label -> its labels dict with pack languages added."""
    out = dict(labels or {})
    for lang, p in packs().items():
        if lang not in out and label in p["text"]:
            out[lang] = p["text"][label]
    return out


def option_synonyms(value: str, synonyms: dict | None) -> dict:
    out = {k: list(v) for k, v in (synonyms or {}).items()}
    for lang, p in packs().items():
        if lang not in out and value in p.get("options", {}):
            out[lang] = list(p["options"][value])
    return out


def localize_tree(x):
    """Walk a JSON structure (a scheme file) and localize every {"en": ...} dict in it."""
    if isinstance(x, dict):
        y = {k: localize_tree(v) for k, v in x.items()}
        return localize(y) if isinstance(y.get("en"), str) else y
    if isinstance(x, list):
        return [localize_tree(v) for v in x]
    return x


def words(lang: str, key: str, default=None):
    p = packs().get(lang)
    return (p.get("words", {}).get(key, default) if p else default)


def all_words(key: str) -> list:
    """A word list (yes / no / skip ...) from every pack, for parsers that don't know the language."""
    out = []
    for p in packs().values():
        out.extend(p.get("words", {}).get(key, []))
    return out


def ui(lang: str) -> dict:
    p = packs().get(lang)
    return dict(p.get("ui", {})) if p else {}


def meta() -> list[dict]:
    """What the app needs to list a language: code, name, speech tag, review status."""
    return [{"lang": l, "name": p.get("name", l), "speech": p.get("speech", l + "-IN"), "status": p.get("status", "draft"),
             "reviewed_by": p.get("reviewed_by", "")} for l, p in packs().items()]


# ---------------------------------------------------------------------------------------------
# completeness check (used by the tests and by `python -m dwaniforms.langpacks check`)
# ---------------------------------------------------------------------------------------------
_PH = re.compile(r"\{[a-z_]+\}")


def required_text() -> set[str]:
    """Every English sentence a pack's `text` table must translate."""
    base = Path(__file__).parent
    need: set[str] = set()
    for p in base.glob("templates/*.json"):
        t = json.loads(p.read_text(encoding="utf-8"))
        need.add(t["title"])
        if (t.get("description") or {}).get("en"):
            need.add(t["description"]["en"])
        for f in t["fields"]:
            need.add(f["prompt"]["en"])
            need.add(f["label"])

    def walk(x):
        if isinstance(x, dict):
            if isinstance(x.get("en"), str):
                need.add(x["en"])
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    for p in base.glob("schemes/*.json"):
        walk(json.loads(p.read_text(encoding="utf-8")))
    from . import outcomes
    need.update(outcomes.NEXT_STEPS_EN)
    return need


def required_options() -> set[str]:
    base = Path(__file__).parent
    need = set()
    for p in base.glob("templates/*.json"):
        for f in json.loads(p.read_text(encoding="utf-8"))["fields"]:
            need.update(o["value"] for o in f.get("options", []))
    return need


def ui_keys() -> set[str]:
    """Keys of the English screen-text table in static/app.html (the `EN` object plus data-t defaults)."""
    html = (Path(__file__).parent / "static" / "app.html").read_text(encoding="utf-8")
    keys = set(re.findall(r'data-tp?="([a-z_0-9]+)"', html))
    m = re.search(r"var EN = \{(.*?)\};\n", html, re.S)
    if m:
        keys.update(re.findall(r'(?:^|[,{\s])([a-z_0-9]+):\s*"', m.group(1)))
    return keys


def problems(lang: str) -> list[str]:
    from .messages import MESSAGES
    p = packs()[lang]
    out = []
    for k, en in MESSAGES["en"].items():
        v = p.get("messages", {}).get(k)
        if not v:
            out.append(f"messages.{k}: missing")
        elif sorted(_PH.findall(v)) != sorted(_PH.findall(en)):
            out.append(f"messages.{k}: placeholders {_PH.findall(en)} expected, got {_PH.findall(v)}")
    for k in sorted(ui_keys()):
        if not p.get("ui", {}).get(k):
            out.append(f"ui.{k}: missing")
    if "{lang}" not in p.get("ui", {}).get("no_voice", "{lang}"):
        out.append("ui.no_voice: lost {lang}")
    for en in sorted(required_text()):
        if not p.get("text", {}).get(en):
            out.append(f"text: missing {en!r}")
    for v in sorted(required_options()):
        if not p.get("options", {}).get(v):
            out.append(f"options.{v}: missing")
    w = p.get("words", {})
    for k in ("yes", "no", "skip"):
        if not w.get(k):
            out.append(f"words.{k}: missing")
    if len(w.get("months", [])) != 12:
        out.append("words.months: need 12 names")
    if not set(range(11)) <= set(w.get("digits", {}).values()):
        out.append("words.digits: need one word for each of 0-10")
    if "{ha}" not in p.get("formats", {}).get("area", "") or "{acres}" not in p.get("formats", {}).get("area", ""):
        out.append("formats.area: needs {ha} and {acres}")
    if "{k}" not in p.get("formats", {}).get("what_khasra", "") or not p.get("formats", {}).get("what_card"):
        out.append("formats.what_khasra ({k}) / formats.what_card: missing")
    for c in ("AAY", "PHH", "NPHH"):
        if not p.get("categories", {}).get(c):
            out.append(f"categories.{c}: missing")
    return out


def _main(argv: list[str]) -> int:
    if argv[:1] == ["check"]:
        bad = 0
        for lang in packs():
            pr = problems(lang)
            bad += len(pr)
            print(f"{lang}: {'complete' if not pr else str(len(pr)) + ' problem(s)'}  [{packs()[lang].get('status', 'draft')}]")
            for line in pr:
                print("   ", line)
        return 1 if bad else 0
    if argv[:1] == ["skeleton"]:            # an empty pack for a new language, to hand to a translator
        from .messages import MESSAGES
        sk = {"lang": "xx", "name": "", "speech": "xx-IN", "status": "draft", "reviewed_by": "",
              "messages": dict(MESSAGES["en"]), "ui": {k: "" for k in sorted(ui_keys())},
              "text": {k: "" for k in sorted(required_text())}, "options": {k: [] for k in sorted(required_options())},
              "words": {"yes": [], "no": [], "skip": [], "skip_phrases": [], "benign_negations": [], "digits": {},
                        "scales": {}, "months": [], "month_aliases": {}, "filler": []},
              "formats": {"area": "{ha} hectare (about {acres} acres)", "what_khasra": "khasra {k}",
                          "what_card": "this ration card"}, "categories": {"AAY": "", "PHH": "", "NPHH": ""}}
        print(json.dumps(sk, ensure_ascii=False, indent=1))
        return 0
    print("usage: python -m dwaniforms.langpacks check | skeleton")
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
