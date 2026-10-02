"""Website (license_server.py) pages: download links never pin an old release, and
every internal link points at a route that actually exists on the server."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
SERVER = (ROOT / "license_server.py").read_text(encoding="utf-8")
ROUTES = set(re.findall(r'@app\.(?:get|post)\("([^"{]+)"', SERVER))
SITE_PAGES = sorted(set(re.findall(r'FileResponse\(STATIC_DIR / "([\w-]+\.html)"\)', SERVER)))


def test_no_pinned_release_links():
    for page in STATIC.glob("*.html"):
        text = page.read_text(encoding="utf-8")
        pinned = re.findall(r"github\.com/[\w-]+/Dwani/releases/download/v[\d.]+/[\w.-]+", text)
        assert not pinned, f"{page.name} links to a fixed old release: {pinned}"


def test_home_page_offers_the_installer():
    text = (STATIC / "pricing.html").read_text(encoding="utf-8")
    assert "releases/latest/download/DwaniLive-Setup.exe" in text


def test_every_internal_link_on_the_website_exists():
    assert "pricing.html" in SITE_PAGES and "changelog.html" in SITE_PAGES
    broken = []
    for name in SITE_PAGES:
        html = (STATIC / name).read_text(encoding="utf-8")
        for href in re.findall(r'href="(/[^"#?]*)', html):
            if href in ("/",) or href.startswith("/static/"):
                continue
            if href not in ROUTES:
                broken.append(f"{name}: {href}")
        for href in re.findall(r'href="([\w-]+\.html)', html):   # relative links like dashboard.html
            if "/" + href not in ROUTES:
                broken.append(f"{name}: {href}")
    assert not broken, "links to pages the website doesn't serve: " + ", ".join(broken)


def test_how_it_works_describes_the_current_flow():
    text = (STATIC / "pricing.html").read_text(encoding="utf-8")
    assert "activation tool" not in text.lower()          # removed in 1.1: activation is inside the app
    assert "Check setup" in text and 'id="features"' in text
