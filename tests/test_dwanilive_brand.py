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


def test_careers_pages_use_the_shared_logo_fonts_and_footer():
    # /careers must look like the rest of the website: the shared brand.css, no second copy of the logo rules, the site footer
    base = (ROOT / "careers" / "templates" / "base.html").read_text(encoding="utf-8")
    assert 'href="/static/brand.css"' in base
    assert "/changelog" in base and "dwaniforms.onrender.com" in base, "the footer should carry the site's links"
    nav = text(STATIC / "site-nav.css")
    assert not re.search(r"^\.brand-name", nav, flags=re.M), "site-nav.css has its own logo rules"
    assert ".site-header .nav-cta" in nav, "the Log in button must stay light on its gradient"


# ---- the account: a plain monogram avatar, one quiet account stylesheet, one contact address ----
ACCOUNT_PAGES = ["dashboard", "settings", "login", "signup", "pricing", "forgot-password"]


def test_avatar_is_drawn_locally_and_asks_for_no_gender():
    assert (STATIC / "avatar.js").exists()
    for n in ACCOUNT_PAGES:
        t = text(STATIC / f"{n}.html")
        assert 'src="/static/avatar.js"' in t, f"{n}: avatar.js is not loaded"
        assert "dicebear" not in t.lower() and "avataaars" not in t, f"{n}: still fetches a cartoon avatar"
        assert "-gender" not in t and "Gender" not in t, f"{n}: still asks for gender"
        assert "nav-dashboard-btn" not in t, f"{n}: a second menu icon is back"
    assert "dicebear" not in text(STATIC / "site-nav.js").lower()
    assert "dicebear" not in (ROOT / "careers" / "templates" / "base.html").read_text(encoding="utf-8").lower()


def test_account_pages_share_one_quiet_stylesheet():
    for n in ("dashboard", "settings"):
        assert 'href="/static/account.css"' in text(STATIC / f"{n}.html"), n
    css = text(STATIC / "account.css")
    assert ".mandala-bg { display: none; }" in css and "linear-gradient" not in css


def test_the_contact_address_is_the_team_mailbox():
    for rel in ("static/privacy.html", "static/terms.html", "static/help.html", "careers/config.toml"):
        t = (ROOT / rel).read_text(encoding="utf-8")
        assert "dwanilive@gmail.com" in t and "khushimittal070906" not in t, rel


def test_the_shared_header_asks_an_endpoint_that_exists_whether_you_are_logged_in():
    """The careers header once asked /api/me, which does not exist, so a logged-in person was always shown 'Log in'."""
    asked = re.search(r'fetch\("(/[^"]+)"', text(STATIC / "site-nav.js")).group(1)
    assert f'@app.get("{asked}")' in text(ROOT / "license_server.py"), f"site-nav.js asks {asked}, which license_server does not serve"


def test_attendees_see_who_made_the_captions_and_so_does_a_saved_transcript():
    """The attendee page is shown to a whole room at every event: it says whose captions they are (plain text, since the
    room network may have no internet), and a transcript saved and passed on carries the same line."""
    page = text(STATIC / "index.html")
    assert re.search(r'<p class="join-credit">Live translation by <b>DwaniLive</b>', page)
    assert "dhwani-elit.onrender.com" in page[page.index('class="join-credit"'):][:200]
    assert "<a" not in page[page.index('class="join-credit"'):][:200].split("</p>")[0], "no link: the room's network may be offline"
    save = page[page.index('getElementById("save-btn").addEventListener'):][:1500]
    assert "Captions by DwaniLive" in save and "https://dhwani-elit.onrender.com" in save
