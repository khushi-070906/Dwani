"""After logging in a person is sent to ?next=... . That must only ever be a page on this site: a link such as
login.html?next=//evil.com, next=/\\evil.com or next=javascript:... must not carry anyone off the site (or run script)
once they have logged in."""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOSTILE = ["//evil.com", "/\\evil.com", "/\t/evil.com", "/\n/evil.com", "/\r/evil.com", "javascript:alert(1)",
           "https://evil.com", "evil.com", "\\\\evil.com", "/\x7f/x"]
FINE = ["/pricing.html", "/careers/", "/dashboard.html?tab=billing", "/careers/jobs/3#apply"]


def _server_check():
    src = (ROOT / "license_server.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "_safe_next_path")
    ns = {}
    exec(compile(ast.Module([fn], []), "license_server.py", "exec"), ns)  # just this function: the module needs bcrypt etc.
    return ns["_safe_next_path"]


def test_the_server_only_follows_paths_on_this_site():
    safe = _server_check()
    for bad in HOSTILE:
        assert safe(bad) == "/dashboard.html", repr(bad)
    for ok in FINE:
        assert safe(ok) == ok, ok


def test_the_login_and_signup_pages_apply_the_same_rule_before_they_navigate():
    for name in ("login", "signup"):
        html = (ROOT / "static" / f"{name}.html").read_text(encoding="utf-8")
        m = re.search(r"const nextPath = /(.+)/\.test\(rawNext\) \? rawNext : '/dashboard\.html';", html)
        assert m, f"{name}.html does not check ?next before using it"
        rx = re.compile(m.group(1))
        for bad in HOSTILE:
            assert not rx.match(bad), (name, bad)
        for ok in FINE:
            assert rx.match(ok), (name, ok)
        assert "get('next') || '/dashboard.html'" not in html, name


def test_careers_login_brings_you_back_to_careers():
    base = (ROOT / "careers" / "templates" / "base.html").read_text(encoding="utf-8")
    assert 'href="/login.html?next=/careers/"' in base
