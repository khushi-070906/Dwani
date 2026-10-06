"""Step 5: completed form -> JSON (for portal import) and PDF (for print). Nothing is sent anywhere."""
from __future__ import annotations

import datetime as dt
import io
import json
import os
from pathlib import Path

from .session import FormSession

def _font_dirs() -> list[Path]:
    """Where to look for Mukta-Regular.ttf: $DWANIFORMS_FONT_DIR, then dwaniforms/fonts/. Falls back to Helvetica."""
    dirs = [Path(__file__).resolve().parent / "fonts"]
    if os.environ.get("DWANIFORMS_FONT_DIR"):
        dirs.insert(0, Path(os.environ["DWANIFORMS_FONT_DIR"]))
    return dirs


def to_json(session: FormSession, include_native: bool = True, mask_sensitive: bool = False) -> str:
    """JSON for portal import (ISO dates). mask_sensitive=True hides all but the last 4 characters of Aadhaar/account/PAN."""
    rows = session.form_values(mask=mask_sensitive)
    if not include_native:
        for r in rows:
            r.pop("native", None)
    return json.dumps({
        "form": session.template.id, "title": session.template.title, "language": session.lang,
        "completed": session.complete(), "created": dt.datetime.fromtimestamp(session.created).isoformat(timespec="seconds"),
        "fields": rows,
        **({"outcome": session.outcome} if session.outcome else {}),
    }, ensure_ascii=False, indent=2)


def _font() -> str:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    if "DwaniForms" in pdfmetrics.getRegisteredFontNames():
        return "DwaniForms"
    for d in _font_dirs():
        f = d / "Mukta-Regular.ttf"
        if f.exists():
            pdfmetrics.registerFont(TTFont("DwaniForms", str(f)))
            return "DwaniForms"
    return "Helvetica"


def _esc(x: str) -> str:
    return x.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _human_date(v: str) -> str:
    """ISO 2026-10-05 -> 05/10/2026 for the printout (the JSON keeps ISO)."""
    return f"{v[8:]}/{v[5:7]}/{v[:4]}" if len(v) == 10 and v[4] == "-" and v[7] == "-" else v


def to_pdf(session: FormSession, mask_sensitive: bool = False) -> bytes:
    """One-page-ish printout of the FORM-LANGUAGE values (what the portal/bank needs). Native-script text is not
    printed: reportlab has no complex-script shaping, so Devanagari conjuncts would render wrongly. Fields flagged
    needs_review are marked so the operator checks them before submission."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    font = _font()
    base = ParagraphStyle("b", fontName=font, fontSize=10.5, leading=14)
    head = ParagraphStyle("h", parent=base, fontSize=17, leading=22, spaceAfter=4)
    small = ParagraphStyle("s", parent=base, fontSize=8.5, textColor=colors.HexColor("#555555"))
    rows = [[Paragraph("Field", small), Paragraph("Value", small)]]
    review = False
    for r in session.form_values(mask=mask_sensitive):
        v = r["value"] or "—"
        if r["kind"] == "date" and r["value"]:
            v = _human_date(v)
        if r["needs_review"]:
            v += "  [CHECK]"; review = True
        rows.append([Paragraph(_esc(r["label"]), base), Paragraph(_esc(v), base)])
    t = Table(rows, colWidths=[62 * mm, 108 * mm], repeatRows=1)
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#bbbbbb")),
                           ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story = [Paragraph(session.template.title, head),
             Paragraph(f"Filled by voice on {dt.date.today().isoformat()} (spoken language: {session.lang}). Generated on this device; no data left it.", small),
             Spacer(1, 6 * mm), t, Spacer(1, 6 * mm)]
    if review:
        story.append(Paragraph("Items marked [CHECK] were not verified automatically (e.g. a spoken name converted to Latin script). "
                               "Please confirm them with the citizen.", small))
    if not session.complete():
        story.append(Paragraph("INCOMPLETE: some required fields are empty.", small))
    out = session.outcome or {}
    if out.get("type") == "draft" and out.get("drafts", {}).get("en"):
        from reportlab.platypus import PageBreak
        story += [PageBreak(), Paragraph(_esc(out.get("title", "Draft")), head), Spacer(1, 4 * mm)]
        for line in out["drafts"]["en"].split("\n"):
            story.append(Paragraph(_esc(line) if line.strip() else "&nbsp;", base))
        if out["drafts"].get("hi"):
            story += [Spacer(1, 6 * mm), Paragraph("A Hindi version of this letter is shown on screen; print it from there "
                                                   "(this PDF cannot shape Devanagari reliably).", small)]
    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
                      title=session.template.title).build(story)
    return buf.getvalue()
