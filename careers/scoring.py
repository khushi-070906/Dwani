"""Ranking aid for the admin board. It never rejects anyone automatically:
it only sorts, and shows the reasons so a human can disagree."""

from __future__ import annotations

import re


def score(cand, role, hours_needed: int) -> tuple[int, str]:
    text = f"{cand.skills} {cand.why} {cand.answer} {cand.links}".lower()
    hits = [k for k in role.keyword_list() if k in text]
    pts = min(50, len(hits) * 10)
    notes = [f"skills: {', '.join(hits) or 'no keyword matches'}"]
    m = re.search(r"\d+(?:\.\d+)?", cand.cgpa or "")
    if m and float(m.group()) <= 10:
        g = float(m.group())
        pts += round(max(0.0, g - 6) * 5)
        notes.append(f"CGPA {g:g}")
    h = re.search(r"\d+", cand.hours or "")
    if h:
        if int(h.group()) >= hours_needed:
            pts += 15
            notes.append(f"{h.group()} h/week")
        else:
            notes.append(f"only {h.group()} h/week (need {hours_needed})")
    if re.search(r"github\.com/[\w-]+", cand.links or "", re.I):
        pts += 10
        notes.append("GitHub")
    if len(f"{cand.why or ''} {cand.answer or ''}".split()) >= 40:
        pts += 5
        notes.append("detailed motivation")
    return min(pts, 100), "; ".join(notes)
