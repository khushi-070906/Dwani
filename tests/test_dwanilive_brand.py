"""DwaniLive's logo and fonts live in ONE file, static/brand.css. This fails when a page keeps its own (different) copy of the
logo rules, forgets to link brand.css, asks Google Fonts for a weight it does not have, or when a bundled font or its
licence goes missing. (static/logo.html is the logo sheet itself and is the one page that draws the logo its own way.)"""
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
PAGES = sorted(p for p in STATIC.glob("*.html") if p.name != "logo.html")
BRAND = STATIC / "brand.css"
EMOJI = "🎤📶📱📝📊💾🔄♿🌐🔒⚡🎯"


def text(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_every_page_links_brand_css_and_keeps_no_logo_rules_of_its_own():
    assert len(PAGES) >= 10
    for p in PAGES:
        t = text(p)
        assert 'href="/static/brand.css"' in t, f"{p.name}: brand.css is not linked"
        css = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", t, flags=re.S))
        assert not re.search(r"\.brand-name\s*(\.|\{|,)", css), f"{p.name}: has its own .brand-name rules"


def test_pages_that_show_the_logo_use_the_shared_markup():
    for p in PAGES:
        t = text(p)
        if 'class="brand-name' in t:
            assert re.search(r'<span class="dwani" lang="hi">ध्वनि</span><span class="live">Live</span>', t), f"{p.name}: logo markup differs"


def test_google_fonts_requests_only_ask_for_real_weights():
    for p in PAGES:
        for url in re.findall(r'href="(https://fonts\.googleapis\.com/css2\?[^"]+)"', text(p)):
            qs = parse_qs(urlparse(url).query.replace(";", "%3B"))
            for fam in qs["family"]:
                name, _, spec = fam.partition(":")
                weights = set(re.findall(r"\d+", spec.split("@", 1)[1])) if "@" in spec else set()
                if name == "Mukta":      # 200-800
                    assert weights <= {"200", "300", "400", "500", "600", "700", "800"}, f"{p.name}: {fam}"
                if name == "Lato":       # no 800: one bad weight and Google sends NO fonts at all
                    assert weights <= {"100", "300", "400", "700", "900"}, f"{p.name}: {fam}"


def test_bundled_fonts_exist_with_their_licences():
    css = text(BRAND)
    files = re.findall(r'url\("/static/(fonts/[^"]+)"\)', css)
    assert {"fonts/RozhaOne-subset.woff2", "fonts/SourceSerif4-latin.woff2", "fonts/Mukta-Regular-subset.woff2"} <= set(files)
    for f in files:
        assert (STATIC / f).stat().st_size > 1000, f
    for lic in ("OFL-RozhaOne.txt", "OFL-SourceSerif4.txt", "OFL-Mukta.txt"):
        assert (STATIC / "fonts" / lic).exists(), lic


def test_brand_css_is_balanced():
    css = re.sub(r"/\*.*?\*/", "", text(BRAND), flags=re.S)
    assert css.count("{") == css.count("}")


def test_pricing_page_has_drawn_icons_not_emoji():
    body = text(STATIC / "pricing.html").split("<body", 1)[1]
    body = re.sub(r"<script.*?</script>", "", body, flags=re.S)
    for ch in EMOJI:
        assert ch not in body, f"emoji {ch!r} is back on the pricing page"


def test_pricing_divider_is_a_quiet_line_not_a_flower_garland():
    t = text(STATIC / "pricing.html")
    assert 'class="garland" aria-hidden="true"></div>' in t, "the divider should have no flower or leaf shapes inside"
    assert ".garland span" not in t and ".bloom" not in t
