"""Can an older person with weak eyes and unsteady hands actually use this?

Three things these tests hold in place, because all three are easy to undo by accident with one CSS tweak:
  * small grey text keeps enough contrast against every background the page uses (WCAG AA, 4.5:1);
  * every control is big enough to hit (44px, the size a fingertip lands on);
  * a control with no visible words of its own says what it is -- in the citizen's language, not in English.
"""
import re
from pathlib import Path

import pytest

from dwaniforms import langpacks

APP = (Path(__file__).resolve().parents[1] / "dwaniforms" / "static" / "app.html").read_text(encoding="utf-8")
ROOT = re.search(r":root \{(.*?)\n  \}", APP, re.S).group(1)


def var(name: str) -> str:
    return re.search(rf"--{name}:\s*(#[0-9A-Fa-f]{{6}})", ROOT).group(1)


def _lum(hexcolour: str) -> float:
    c = [int(hexcolour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    f = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
    return 0.2126 * f[0] + 0.7152 * f[1] + 0.0722 * f[2]


def contrast(a: str, b: str) -> float:
    la, lb = _lum(a), _lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


# every surface small text is ever drawn on
BACKGROUNDS = ["sage", "card", "sage-2", "forest-soft", "amber"]
# text colours used at small sizes, so the 4.5:1 rule applies to all of them
SMALL_TEXT = ["ink", "ink-2", "ink-3", "forest", "navy"]


@pytest.mark.parametrize("fg", SMALL_TEXT)
@pytest.mark.parametrize("bg", BACKGROUNDS)
def test_small_text_is_readable_on_every_background(fg, bg):
    r = contrast(var(fg), var(bg))
    assert r >= 4.5, f"--{fg} on --{bg} is {r:.2f}:1, below the 4.5:1 a reader with ageing eyes needs"


def test_warning_and_error_text_is_readable_too():
    assert contrast(var("bad-ink"), var("bad")) >= 4.5
    assert contrast(var("amber-ink"), var("amber")) >= 4.5
    assert contrast(var("ok-ink"), var("ok")) >= 4.5


def test_every_control_is_big_enough_to_hit():
    """44px: the header buttons, the language chooser and the breadcrumb 'home' were 38-21px tall before."""
    for rule in (r"\.tools select, \.tbtn \{[^}]*min-height: 44px",
                 r"\.tbtn \{[^}]*min-width: 44px",
                 r"\.brand \{[^}]*min-height: 44px",
                 r"\.crumbs button \{[^}]*min-height: 44px",
                 r"\.sagecard a, footer\.foot a \{[^}]*min-height: 44px"):
        assert re.search(rule, APP), rule


def test_the_big_text_button_and_motion_preference_still_exist():
    assert 'id="big-btn"' in APP and 'aria-pressed' in APP
    assert "@media (prefers-reduced-motion: reduce)" in APP        # the mic pulse must be stoppable


def _aria_keys() -> set[str]:
    return set(re.findall(r'data-ta="([a-z_0-9]+)"', APP))


def test_controls_without_visible_words_are_named():
    """A screen reader reads these out; they are the only name those buttons have."""
    assert {"a11y_home", "a11y_bigger", "a11y_lang", "tap", "type_ph"} <= _aria_keys()
    for key in _aria_keys():
        assert re.search(rf'\b{key}: "', APP), f"{key} has no English text in the EN table"


@pytest.mark.parametrize("lang", sorted(langpacks.packs()))
def test_those_names_are_translated_in_every_language(lang):
    ui = langpacks.ui(lang)
    for key in _aria_keys():
        assert ui.get(key), f"{lang}: no {key} -- a blind speaker of this language would hear English"


def test_the_screen_reader_labels_are_not_left_in_english():
    """applyUI must actually apply them, and the mic must say which way it is toggled."""
    assert 'el.setAttribute("aria-label"' in APP and "[data-ta]" in APP
    assert "function micState(on)" in APP and 'm.setAttribute("aria-pressed"' in APP
    assert APP.count("micState(") >= 5                              # set wherever the mic starts or stops


def test_headings_do_not_skip_a_level():
    """Screen-reader users jump by heading. h2 -> h4 reads as a missing section."""
    levels = [int(h) for h in re.findall(r"<h([1-4])[ >]", APP)]
    for a, b in zip(levels, levels[1:]):
        assert b <= a + 1, f"h{a} is followed by h{b}"
    assert 'createElement("h3")' in APP and 'createElement("h4")' not in APP
    assert ".group h3 {" in APP


def test_the_current_step_is_announced():
    assert 'setAttribute("aria-current", "step")' in APP


def test_the_a_plus_button_has_its_visible_words_first_in_its_name():
    """WCAG 2.5.3 'label in name': a person who says what they see ('A plus') must hit the button, so the accessible name
    starts with the visible words."""
    assert 'id="big-btn"' in APP and re.search(r'id="big-btn"[^>]*\bdata-vis\b', APP)
    assert 'el.hasAttribute("data-vis")' in APP and "(el.innerText ||" in APP


def test_small_draft_and_note_text_keeps_aa_contrast():
    # the pencilled (not yet confirmed) rows and the pencil note were pale grey / orange on near-white
    for sel_text in (".pencil .line, .pencil .boxes .b, .pencil .ck.on {", ".note-pen {"):
        i = APP.index(sel_text); rule = APP[i:APP.index("}", i)]
        col = re.search(r"color:\s*(#[0-9A-Fa-f]{6}|var\(--[a-z0-9-]+\))", rule).group(1)
        col = var(col[6:-1]) if col.startswith("var(") else col
        assert contrast(col, "#FCFCF9") >= 4.5, (sel_text, col)
