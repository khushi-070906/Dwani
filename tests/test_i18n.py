"""Every interface string in the Hindi dictionary must still exist in the app's English
source -- otherwise an edited English sentence silently stops being translated."""
import html
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
I18N = (ROOT / "static" / "i18n.js").read_text(encoding="utf-8")


def _source_text() -> str:
    parts = []
    for f in ("static/index.html", "static/host.html", "preflight.py", "server.py"):
        t = html.unescape((ROOT / f).read_text(encoding="utf-8"))
        parts.append(t.replace('\\"', '"'))
    return " ".join(" ".join(parts).split())


def _keys() -> list[str]:
    block = I18N[I18N.index("var HI = {"):I18N.index("var HI_PATTERNS")]
    return [k.replace('\\"', '"') for k in re.findall(r'^\s*"((?:[^"\\]|\\.)+)":', block, re.M)]


def test_no_dead_translations():
    src = _source_text()
    keys = _keys()
    assert len(keys) > 120
    dead = [k for k in keys if " ".join(k.split()) not in src]
    assert not dead, f"translated strings no longer in the app (update or remove them): {dead}"


def test_patterns_are_valid_and_hindi():
    block = I18N[I18N.index("var HI_PATTERNS"):I18N.index("var DICTS")]
    templates = re.findall(r'\],?\s*\n?\s*"([^"]+)"\]', block) or re.findall(r'/, "([^"]+)"\]', block)
    assert templates and all(re.search("[\u0900-\u097F]", t) for t in templates)
