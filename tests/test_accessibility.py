"""Dependency-free tests for accessibility.py."""

from __future__ import annotations

from accessibility import AccessibilityPreferences


def test_defaults_when_dict_empty():
    prefs = AccessibilityPreferences.from_dict({})
    assert prefs.high_contrast is False
    assert prefs.large_text is False
    assert prefs.font_scale == 1.0
    assert prefs.hold_seconds == 6.0
    assert prefs.flash_on_new_caption is False


def test_reads_supplied_values():
    prefs = AccessibilityPreferences.from_dict({
        "high_contrast": True,
        "large_text": True,
        "font_scale": 2.0,
        "hold_seconds": 10,
        "flash_on_new_caption": True,
    })
    assert prefs.high_contrast is True
    assert prefs.font_scale == 2.0
    assert prefs.hold_seconds == 10.0


def test_font_scale_clamped_to_range():
    assert AccessibilityPreferences.from_dict({"font_scale": 0.2}).font_scale == 1.0
    assert AccessibilityPreferences.from_dict({"font_scale": 99}).font_scale == 3.0


def test_hold_seconds_clamped_to_range():
    assert AccessibilityPreferences.from_dict({"hold_seconds": 0}).hold_seconds == 2.0
    assert AccessibilityPreferences.from_dict({"hold_seconds": 999999}).hold_seconds == 30.0


def test_unknown_keys_ignored():
    prefs = AccessibilityPreferences.from_dict({"nonsense_key": "abc", "high_contrast": True})
    assert prefs.high_contrast is True
