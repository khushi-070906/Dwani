"""Website download links must always point at the latest release, never a pinned old version."""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "static"


def test_no_pinned_release_links():
    for page in STATIC.glob("*.html"):
        text = page.read_text(encoding="utf-8")
        pinned = re.findall(r"github\.com/[\w-]+/Dwani/releases/download/v[\d.]+/[\w.-]+", text)
        assert not pinned, f"{page.name} links to a fixed old release: {pinned}"


def test_home_page_offers_the_installer():
    text = (STATIC / "pricing.html").read_text(encoding="utf-8")
    assert "releases/latest/download/DwaniLive-Setup.exe" in text
