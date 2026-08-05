"""
Accessibility Mode -- Hackathon Extension.

The per-language broadcast fan-out in Pipeline (Section 4.3 of the paper)
already serves an English caption stream to an English-speaking Deaf or
hard-of-hearing attendee today -- it was just never framed, or rendered,
as an accessibility feature. This module doesn't add a new pipeline stage;
it adds a small preferences object an attendee sends once (or updates
mid-session) over the *existing* caption WebSocket, alongside the language
selection already sent there, and a caption client built to be read rather
than skimmed (see accessible_caption_client.html alongside this file).

Deliberately NOT translation: an attendee can pick "en" purely for this
formatting, independent of whether they also want a different language.
The two concerns (which language, and how it's rendered) are orthogonal
and this keeps them that way.

-----------------------------------------------------------------------------
Wiring into server.py
-----------------------------------------------------------------------------

    from accessibility import AccessibilityPreferences

    # attendee's caption WebSocket, on connect or on a settings-changed
    # message (same JSON control-message channel the language selection
    # already goes over):
    prefs = AccessibilityPreferences.from_dict(message.get("accessibility", {}))
    subscribers[websocket].accessibility = prefs

    # when broadcasting a caption to this attendee, attach their prefs so
    # the client knows how to render it without a second round trip:
    await websocket.send_json({
        "caption": translated_text,
        "accessibility": dataclasses.asdict(subscribers[websocket].accessibility),
    })
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AccessibilityPreferences:
    """One attendee's accessibility settings.

    `hold_seconds` is the deliberate core of this feature: the default
    caption client (Section 4.4) is built for someone following along in
    real time and replacing a caption as soon as the next one arrives is
    fine for that. A Deaf or hard-of-hearing attendee reading captions as
    their *only* channel into the talk needs captions that don't vanish
    the instant they're replaced -- so this holds the previous caption
    visible (e.g. faded, smaller, above the current one) for at least
    `hold_seconds` before it's dropped, regardless of how fast the
    presenter is talking.
    """

    high_contrast: bool = False   # WCAG-style black background / yellow text
    large_text: bool = False
    font_scale: float = 1.0       # 1.0 = client default, clamped to [1.0, 3.0]
    hold_seconds: float = 6.0     # minimum time a caption stays visible before being dropped
    flash_on_new_caption: bool = False  # brief visual pulse when a new caption arrives, for attention

    @classmethod
    def from_dict(cls, data: dict) -> "AccessibilityPreferences":
        """Builds from an attendee-supplied JSON dict -- untrusted client
        input, so every field is defended: unknown/missing keys fall back
        to the default, and numeric fields are clamped to a sane range
        rather than trusting whatever a hand-edited or malicious client
        sends (an attendee could otherwise send hold_seconds=999999 and
        effectively freeze their own client, which is harmless to others
        but still worth bounding).
        """
        return cls(
            high_contrast=bool(data.get("high_contrast", False)),
            large_text=bool(data.get("large_text", False)),
            font_scale=_clamp(float(data.get("font_scale", 1.0)), 1.0, 3.0),
            hold_seconds=_clamp(float(data.get("hold_seconds", 6.0)), 2.0, 30.0),
            flash_on_new_caption=bool(data.get("flash_on_new_caption", False)),
        )


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))
