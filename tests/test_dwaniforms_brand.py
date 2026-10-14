"""The DwaniForms logo, fonts and favicon must be the same on every page a person can open: the app, its numbers page,
and the site (home, privacy, terms). The app and the site are served from different addresses, so each carries a copy
of the rules; this test fails when a copy drifts, a font file goes missing, or the Google Fonts address is wrong."""
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "dwaniforms" / "static"
SITE = ROOT / "dwaniforms_site"
APP, METRICS = STATIC / "app.html", STATIC / "metrics.html"
PAGES = [APP, METRICS, SITE / "index.html", SITE / "privacy.html", SITE / "terms.html"]
SITE_PAGES = PAGES[2:]
BRAND_CSS = SITE / "brand.css"


def text(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def rules(css: str) -> dict:
    """selector -> {property: value}; later rules for one selector add to (and override) earlier ones. Comments and
    at-rules with blocks other than @media are ignored; rules inside @media are skipped."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    css = re.sub(r"@media[^{]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}", "", css)
    css = re.sub(r"@font-face\s*\{[^}]*\}", "", css)
    out = {}
    for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        decl = {}
        for d in body.split(";"):
            if ":" in d:
                k, v = d.split(":", 1)
                decl[k.strip()] = re.sub(r"\s+", " ", v.strip())
        for s in sel.split(","):
            out.setdefault(re.sub(r"\s+", " ", s.strip()), {}).update(decl)
    return out


def style_of(p: Path) -> str:
    return "\n".join(re.findall(r"<style>(.*?)</style>", text(p), flags=re.S))


# what makes the logo look the same: size, shape, colour and font -- not how it is laid out around it
LOOK = ("width", "height", "border-radius", "background", "color", "font", "font-size", "font-family", "font-weight",
        "inset", "border", "margin-left", "line-height")
PAIRS = [(".seal", ".df-seal"), (".seal::after", ".df-seal::after"), (".word b", ".df-word b"),
         (".word b span", ".df-word b span"), (".word small", ".df-word small"),
         (".brand.sm .seal", ".df-brand.sm .df-seal"), (".brand.sm .seal::after", ".df-brand.sm .df-seal::after"),
         (".brand.sm .word b", ".df-brand.sm .df-word b"), (".brand.sm .word b span", ".df-brand.sm .df-word b span")]


def look(decl: dict) -> dict:
    return {k: v for k, v in decl.items() if k in LOOK}


def test_logo_rules_are_the_same_in_app_and_site():
    site = rules(text(BRAND_CSS))
    for page in (APP, METRICS):
        mine = rules(style_of(page))
        for app_sel, site_sel in PAIRS:
            if app_sel.startswith(".brand.sm") and page == METRICS:
                continue  # the numbers page has only the large logo
            assert app_sel in mine, f"{page.name}: no rule for {app_sel}"
            assert site_sel in site, f"brand.css: no rule for {site_sel}"
            assert look(mine[app_sel]) == look(site[site_sel]), \
                f"{page.name} {app_sel} differs from brand.css {site_sel}: {look(mine[app_sel])} vs {look(site[site_sel])}"


def test_font_stacks_are_the_same_everywhere():
    want = {}
    for p in (APP, METRICS, BRAND_CSS):
        css = style_of(p) if p.suffix == ".html" else text(p)
        for name in ("--serif", "--sans"):
            m = re.search(re.escape(name) + r":\s*([^;]+);", css)
            assert m, f"{p.name}: {name} is not defined"
            want.setdefault(name, set()).add(re.sub(r"\s+", " ", m.group(1).strip()))
    assert all(len(v) == 1 for v in want.values()), want


def test_every_page_loads_the_same_google_fonts():
    seen = {}
    for p in PAGES:
        m = re.search(r'href="(https://fonts\.googleapis\.com/css2\?[^"]+)"', text(p))
        assert m, f"{p.name}: no Google Fonts link"
        qs = parse_qs(urlparse(m.group(1)).query.replace(";", "%3B"))
        fams = {f.split(":")[0] for f in qs["family"]}
        seen[p.name] = fams
        for f in qs["family"]:
            if f.startswith("Lato"):
                weights = set(re.findall(r"\d+", f.split("@", 1)[1])) if "@" in f else set()
                # Lato has no 800; one unknown weight makes Google answer 400 and NO font loads at all
                assert weights <= {"100", "300", "400", "700", "900"}, f"{p.name}: {f}"
    base = {"Rozha One", "Source Serif 4", "Lato", "Mukta", "Noto Serif Devanagari"}
    for name, fams in seen.items():
        assert fams - {"Kalam"} == base, f"{name}: {fams}"      # Kalam is only the handwriting on the home page demo


def test_favicon_is_the_logo_everywhere():
    icons = set()
    for p in PAGES:
        m = re.search(r'<link rel="icon" href="([^"]+)"', text(p))
        assert m, f"{p.name}: no favicon"
        icons.add(m.group(1))
    assert len(icons) == 1, "the favicon differs between pages"
    icon = icons.pop()
    assert "<path" in icon and "font-family" not in icon, "the favicon should be drawn from the logo glyph, not a system font"


def test_every_page_has_the_same_wordmark_and_no_old_logo():
    for p in PAGES:
        t = text(p)
        assert re.search(r'ध्वनि<span lang="en">Forms</span>', t), f"{p.name}: no wordmark"
        assert 'class="logo"' not in t and ".logo .dwani" not in t and ".logo .emb" not in t, f"{p.name}: old logo left"
    for p in SITE_PAGES:
        assert '<link rel="stylesheet" href="brand.css">' in text(p), f"{p.name}: brand.css is not linked"


def test_bundled_fonts_exist_with_their_licences():
    css = text(BRAND_CSS)
    files = re.findall(r'url\("(fonts/[^"]+)"\)', css)
    assert {"fonts/RozhaOne-subset.woff2", "fonts/SourceSerif4-latin.woff2"} <= set(files)
    for f in files:
        assert (SITE / f).stat().st_size > 1000, f
    for lic in ("OFL-RozhaOne.txt", "OFL-SourceSerif4.txt", "OFL-Mukta.txt"):
        assert (SITE / "fonts" / lic).exists(), lic
    # the app serves its own copies (fonts are an allowed asset kind) and the service worker keeps the logo font
    for f in ("RozhaOne-Regular.ttf", "SourceSerif4-latin.woff2", "OFL-SourceSerif4.txt"):
        assert (STATIC / "fonts" / f).exists(), f
    sw = text(STATIC / "sw.js")
    assert "SourceSerif4-latin.woff2" in sw and "RozhaOne-Regular.ttf" in sw
    for p in (APP, METRICS):
        assert "SourceSerif4-latin.woff2" in text(p) and "RozhaOne-Regular.ttf" in text(p), p.name


def test_metrics_page_finds_its_fonts_from_its_own_address():
    # served at /form/metrics/view, so assets are one level up
    t = text(METRICS)
    assert 'url("../assets/fonts/' in t and 'url("assets/fonts/' not in t


def test_no_stray_css_swallowing_a_rule():
    # A leftover half-rule ("font: ...; }") in front of a selector makes the browser drop that selector's whole rule.
    # (This once turned the home page's phone header dark with dark letters.) A selector never contains a ';'.
    for p in PAGES + [BRAND_CSS]:
        css = style_of(p) if p.suffix == ".html" else text(p)
        css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        depth, buf = 0, ""
        for ch in css:
            if ch == "{":
                assert not (depth == 0 and ";" in buf.strip()), f"{p.name}: stray CSS before a selector: {buf.strip()[:80]!r}"
                depth += 1
                buf = ""
            elif ch == "}":
                assert depth > 0, f"{p.name}: a closing brace with nothing open (a leftover half-rule): {buf.strip()[-80:]!r}"
                depth -= 1
                buf = ""
            elif depth == 0:
                buf += ch
        assert depth == 0, f"{p.name}: unbalanced braces"


def test_home_page_has_no_emoji_icons_or_rainbow_cards():
    # the home page is calm: drawn icons and initials, one card style, flat buttons
    t = text(SITE / "index.html")
    body = t.split("<body", 1)[1]
    for ch in "🎤🗣📝📢🔍💡👨👩👵▶":
        assert ch not in body, f"emoji {ch!r} is back on the home page"
    assert "box-shadow: 0 8px 0" not in t and 'class="feat f' not in t and "rotate(1.5deg)" not in t


def test_app_has_drawn_icons_not_emoji():
    # the app matches the site: line icons in the brand colour, not emoji whose look changes with every phone
    t = text(APP)
    for ch in "🎤🎙🔒📷🗣📝📢📄⚖🧾🗺🍚💡":
        assert ch not in t, f"emoji {ch!r} is back in the app"
    assert "function icon(" in t and t.count('class="ic"') >= 4


def test_the_dwaniforms_site_writes_to_the_team_mailbox():
    from pathlib import Path
    site = Path(__file__).resolve().parent.parent / "dwaniforms_site"
    for n in ("index", "privacy", "terms"):
        t = (site / f"{n}.html").read_text(encoding="utf-8")
        assert "dwanilive@gmail.com" in t and "khushimittal070906" not in t, n


def test_the_app_draws_its_ticks_pencil_warning_and_download_instead_of_text_symbols():
    """✓ ✎ ⚠ ⤓ come from whatever font a phone has (often a coloured emoji), so the app draws them. They remain only inside
    the per-language wording tables ('✓ हाँ, सही है'), which are text."""
    app = (Path(__file__).resolve().parent.parent / "dwaniforms" / "static" / "app.html").read_text(encoding="utf-8")
    code = app[:app.index("  var UI = {")] + app[app.index("  var LANG_NAMES"):]
    for sym in "✓✎⚠⤓":
        assert sym not in code, f"{sym!r} is still a text symbol in the app"
    for name in ("check", "warn", "pencil"):
        assert f'"{name}": "<path' in app, name
