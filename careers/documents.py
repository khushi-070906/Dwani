"""
documents.py -- DwaniLive-branded PDFs (`out` may be a path or a BytesIO): internship offer letter and
internship completion certificate. Both carry the DwaniLive wordmark, a
document number and a QR code that opens the public verification page.

Fonts are the app's own (Rozha One for display, Mukta for text), shipped
in fonts/ under the SIL Open Font License.
"""

from __future__ import annotations

import datetime as dt
import io
from pathlib import Path
from typing import Optional

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (Image, KeepTogether, ListFlowable, ListItem, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

ROOT = Path(__file__).resolve().parent
FONTS = ROOT / "fonts"
ASSETS = ROOT / "static"

# Brand palette (same tokens as the website / app)
BG = colors.HexColor("#fdf6e7")
BG_RAISED_2 = colors.HexColor("#f4e9cf")
INK = colors.HexColor("#2c1810")
INK_DIM = colors.HexColor("#5c4433")
INK_FAINT = colors.HexColor("#83694f")
ACCENT_DEEP = colors.HexColor("#a8460c")
ACCENT_2 = colors.HexColor("#c81e5e")
INDIGO = colors.HexColor("#223367")

_fonts_done = False


def register_fonts() -> None:
    global _fonts_done
    if _fonts_done:
        return
    for name, file in (("Mukta", "Mukta-Regular.ttf"), ("Mukta-SemiBold", "Mukta-SemiBold.ttf"),
                       ("Mukta-ExtraBold", "Mukta-ExtraBold.ttf"), ("Rozha", "RozhaOne-Regular.ttf")):
        pdfmetrics.registerFont(TTFont(name, str(FONTS / file)))
    pdfmetrics.registerFontFamily("Mukta", normal="Mukta", bold="Mukta-SemiBold",
                                  italic="Mukta", boldItalic="Mukta-SemiBold")
    _fonts_done = True


def fmt_date(d: dt.date | str) -> str:
    if isinstance(d, str):
        d = dt.date.fromisoformat(d)
    return d.strftime("%d %B %Y").lstrip("0")


def months_between(a: str, b: str) -> str:
    d1, d2 = dt.date.fromisoformat(a), dt.date.fromisoformat(b)
    days = (d2 - d1).days + 1
    weeks = round(days / 7)
    if days >= 56:
        m = round(days / 30.4)
        return f"{m} month{'s' if m != 1 else ''}"
    return f"{weeks} week{'s' if weeks != 1 else ''}"


def _gradient_bar(c, x, y, w, h, steps: int = 120) -> None:
    stops = [(0.0, ACCENT_DEEP), (0.5, ACCENT_2), (1.0, INDIGO)]
    seg = w / steps
    for i in range(steps):
        t = i / (steps - 1)
        a, ca = stops[0] if t <= 0.5 else stops[1]
        b, cb = stops[1] if t <= 0.5 else stops[2]
        u = (t - a) / (b - a)
        col = colors.Color(ca.red + (cb.red - ca.red) * u, ca.green + (cb.green - ca.green) * u,
                           ca.blue + (cb.blue - ca.blue) * u)
        c.setFillColor(col)
        c.rect(x + i * seg, y, seg + 0.4, h, stroke=0, fill=1)


def _qr(c, url: str, x: float, y: float, size: float) -> None:
    w = QrCodeWidget(url, barLevel="M")
    x0, y0, x1, y1 = w.getBounds()
    d = Drawing(size, size, transform=[size / (x1 - x0), 0, 0, size / (y1 - y0), 0, 0])
    w.barFillColor = INK
    d.add(w)
    renderPDF.draw(d, c, x, y)


def _wordmark(height: float):
    img = ImageReader(str(ASSETS / "dwanilive-wordmark.png"))
    iw, ih = img.getSize()
    return img, height * iw / ih, height


# ---------------------------------------------------------------------------
# Letters (offer letter, letter of recommendation): plain business letter
# on a simple letterhead, the way companies actually write them.
# ---------------------------------------------------------------------------

def _signature_flowables(sig: dict, style) -> list:
    out = []
    png = sig.get("signature_png")
    if png:
        out.append(Image(io.BytesIO(png), width=40 * mm, height=14 * mm, kind="proportional", hAlign="LEFT"))
    else:
        out.append(Spacer(1, 13 * mm))
    return out


def _letter(out, *, cfg: dict, doc_id: str, verify_url: str, title: str, recipient: list[str], subject: str,
            paragraphs: list[str], closing: str = "Sincerely,") -> None:
    register_fonts()
    org, sig = cfg["org"], cfg["signatory"]
    W, H = A4
    M = 22 * mm
    body = ParagraphStyle("body", fontName="Mukta", fontSize=10.8, leading=16.2, textColor=INK,
                          alignment=TA_JUSTIFY, spaceAfter=9)
    small = ParagraphStyle("small", fontName="Mukta", fontSize=10.2, leading=14, textColor=INK)
    subj = ParagraphStyle("subj", parent=body, fontName="Mukta-SemiBold", alignment=0, spaceBefore=4, spaceAfter=12)

    def letterhead(c, doc):
        c.saveState()
        img, iw, ih = _wordmark(13 * mm)
        c.drawImage(img, M, H - 17 * mm - ih, iw, ih, mask="auto")
        c.setFillColor(INK)
        c.setFont("Mukta-SemiBold", 10)
        c.drawRightString(W - M, H - 17 * mm - 3.5 * mm, org["legal_name"])
        c.setFont("Mukta", 8.6)
        c.setFillColor(INK_DIM)
        for i, line in enumerate([org["address"], org["email"], org["website"].replace("https://", "")]):
            c.drawRightString(W - M, H - 17 * mm - 8 * mm - i * 4 * mm, line)
        c.setStrokeColor(ACCENT_DEEP)
        c.setLineWidth(0.9)
        c.line(M, H - 39 * mm, W - M, H - 39 * mm)
        # footer: reference + small verification QR, like a document control strip
        c.setStrokeColor(BG_RAISED_2)
        c.setLineWidth(0.6)
        c.line(M, 21 * mm, W - M, 21 * mm)
        _qr(c, verify_url, W - M - 13 * mm, 6.5 * mm, 13 * mm)
        c.setFont("Mukta", 7.8)
        c.setFillColor(INK_FAINT)
        c.drawString(M, 15 * mm, f"{title} · Ref. {doc_id}")
        c.drawString(M, 11 * mm, f"Verify at {verify_url}")
        c.restoreState()

    doc = SimpleDocTemplate(out, pagesize=A4, leftMargin=M, rightMargin=M, topMargin=46 * mm, bottomMargin=27 * mm,
                            title=title, author=org["legal_name"], subject=doc_id,
                            creator=f"{org['name']} Careers")
    story = []
    right = ParagraphStyle("right", parent=small, alignment=2)
    head = Table([[Paragraph(f"Ref: {doc_id}", small), Paragraph(f"Date: {fmt_date(dt.date.today())}", right)]],
                 colWidths=[(W - 2 * M) / 2] * 2)
    head.setStyle(TableStyle([("ALIGN", (1, 0), (1, 0), "RIGHT"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                              ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    story += [head, Spacer(1, 10)]
    if recipient:
        story.append(Paragraph("To,<br/>" + "<br/>".join(recipient), small))
        story.append(Spacer(1, 10))
    story.append(Paragraph(f"Subject: {subject}", subj))
    for p in paragraphs:
        story.append(Paragraph(p, body))
    story.append(Spacer(1, 4))
    sig_block = [Paragraph(closing, body)] + _signature_flowables(sig, small) + [
        Paragraph(f"<b>{sig['name']}</b><br/>{sig['title']}<br/>{org['legal_name']}", small)]
    story.append(KeepTogether(sig_block))
    doc.build(story, onFirstPage=letterhead, onLaterPages=letterhead)


def _stipend_sentence(stipend: str) -> str:
    s = (stipend or "").strip()
    if not s or s.lower().startswith(("unpaid", "nil", "none", "no stipend")):
        return "This is an unpaid internship."
    return f"You will receive a stipend of {s}."


def _gerund(word: str) -> str:
    w = word.lower()
    if w.endswith("ing"):
        return w
    if w.endswith("ie"):
        return w[:-2] + "ying"
    if w.endswith("e") and not w.endswith(("ee", "ye", "oe")):
        return w[:-1] + "ing"
    vowels = "aeiou"
    if len(w) <= 4 and len(w) >= 3 and w[-1] not in vowels + "wxy" and w[-2] in vowels and w[-3] not in vowels:
        return w + w[-1] + "ing"  # run -> running, plan -> planning
    return w + "ing"


def _duty_phrase(d: str) -> str:
    """'Build benchmarking scripts' -> 'building benchmarking scripts', so duties
    written as short imperatives read naturally inside a sentence."""
    d = d.strip().rstrip(".")
    if not d:
        return ""
    words = d.split()
    if not (words[0][:1].isupper() and words[0].isalpha()):
        return d
    words[0] = _gerund(words[0])
    if len(words) > 2 and words[1] == "and" and words[2].isalpha():  # "Evaluate and improve X"
        words[2] = _gerund(words[2])
    return " ".join(words)


def _join(items: list[str]) -> str:
    items = [_duty_phrase(i) for i in items if i.strip()]
    if not items:
        return ""
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def offer_letter(out, *, cfg: dict, cand: dict, offer: dict, doc_id: str, verify_url: str):
    org = cfg["org"]
    first = cand["name"].split()[0]
    recipient = [f"<b>{cand['name']}</b>"] + ([cand["college"]] if cand.get("college") else [])
    duration = months_between(offer["start"], offer["end"])
    paras = [
        f"Dear {first},",
        f"We are pleased to offer you an internship with <b>{org['legal_name']}</b> as a "
        f"<b>{offer['role_title']}</b>. The internship will be for a period of {duration}, starting on "
        f"<b>{fmt_date(offer['start'])}</b> and ending on <b>{fmt_date(offer['end'])}</b>.",
    ]
    work = _join(offer.get("duties", []))
    p = f"You will report to {offer['reporting_to']}."
    if work:
        p += f" Your work will include {work}."
    paras.append(p)
    m = offer["mode"].strip()
    m = m[0].lower() + m[1:] if m else "remote"
    article = "an" if m[:1] in "aeiou" else "a"
    paras.append(f"This will be {article} {m} internship with an expected time commitment of about "
                 f"{offer['hours_per_week']} hours per week, which can be planned around your academic schedule. "
                 f"{_stipend_sentence(offer['stipend'])}")
    paras.append(f"During and after the internship, you are expected to keep confidential any non-public "
                 f"information about {org['name']}, its users and its technology. Work you produce during the "
                 f"internship will be the property of {org['name']}; you are welcome to describe your "
                 f"contribution in your resume and portfolio.")
    paras.append(f"Either party may end the internship by giving {offer['notice_days']} days' notice in writing. "
                 f"On successful completion, you will receive an internship completion certificate.")
    paras.append(f"We look forward to working with you. Welcome to {org['name']}!")
    _letter(out, cfg=cfg, doc_id=doc_id, verify_url=verify_url, title="Internship Offer Letter",
            recipient=recipient, subject=f"Offer of Internship: {offer['role_title']}", paragraphs=paras)
    return out


def recommendation_letter(out, *, cfg: dict, cand: dict, rec: dict, doc_id: str, verify_url: str):
    """rec: role_title, start, end, body (admin-written paragraphs separated by blank lines)."""
    paras = ["To whom it may concern,",
             f"This is to certify that <b>{cand['name']}</b>"
             + (f" of {cand['college']}" if cand.get("college") else "")
             + f" worked with {cfg['org']['legal_name']} as a <b>{rec['role_title']}</b> from "
               f"{fmt_date(rec['start'])} to {fmt_date(rec['end'])}."]
    for chunk in [c.strip() for c in rec["body"].replace("\r", "").split("\n\n") if c.strip()]:
        paras.append(_escape(chunk).replace("\n", "<br/>"))
    paras.append("Please feel free to contact me for any further information.")
    _letter(out, cfg=cfg, doc_id=doc_id, verify_url=verify_url, title="Letter of Recommendation",
            recipient=[], subject=f"Letter of Recommendation for {cand['name']}",
            paragraphs=paras, closing="Sincerely,")
    return out


def _escape(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------------------------------------------------------------
# Completion certificate
# ---------------------------------------------------------------------------

def certificate(out, *, cfg: dict, cand: dict, cert: dict, doc_id: str, verify_url: str) -> Path:
    register_fonts()
    from reportlab.pdfgen import canvas as rl_canvas

    org, sig = cfg["org"], cfg["signatory"]
    W, H = landscape(A4)
    c = rl_canvas.Canvas(out, pagesize=(W, H))
    c.setTitle(f"Internship certificate - {cand['name']}")
    c.setAuthor(org["legal_name"])
    c.setSubject(doc_id)

    # background + frame
    c.setFillColor(BG)
    c.rect(0, 0, W, H, stroke=0, fill=1)
    pad = 10 * mm
    for (x, y, w, h) in ((pad, pad, W - 2 * pad, 2.2), (pad, H - pad - 2.2, W - 2 * pad, 2.2)):
        _gradient_bar(c, x, y, w, h)
    c.setStrokeColor(ACCENT_DEEP)
    c.setLineWidth(2.2)
    c.line(pad, pad, pad, H - pad)
    c.setStrokeColor(INDIGO)
    c.line(W - pad, pad, W - pad, H - pad)
    c.setStrokeColor(BG_RAISED_2)
    c.setLineWidth(0.8)
    c.rect(pad + 5 * mm, pad + 5 * mm, W - 2 * pad - 10 * mm, H - 2 * pad - 10 * mm)

    # wordmark
    img, iw, ih = _wordmark(22 * mm)
    c.drawImage(img, (W - iw) / 2, H - 46 * mm, iw, ih, mask="auto")

    # title
    title = "CERTIFICATE OF INTERNSHIP"
    c.setFillColor(INDIGO)
    c.setFont("Mukta-ExtraBold", 17)
    c.drawCentredString(W / 2, H - 60 * mm, " ".join(title).replace("   ", "     "))  # letter-spaced
    c.setFillColor(INK_DIM)
    c.setFont("Mukta", 12.5)
    c.drawCentredString(W / 2, H - 73 * mm, "This is to certify that")

    # name (auto-shrink long names)
    name = cand["name"]
    size = 40
    while pdfmetrics.stringWidth(name, "Rozha", size) > W - 90 * mm and size > 22:
        size -= 1
    c.setFillColor(ACCENT_DEEP)
    c.setFont("Rozha", size)
    c.drawCentredString(W / 2, H - 92 * mm, name)
    nw = pdfmetrics.stringWidth(name, "Rozha", size)
    _gradient_bar(c, (W - nw) / 2, H - 96 * mm, nw, 1.1)

    college = f" of {cand['college']}" if cand.get("college") else ""
    body = (f"{college.strip()} has successfully completed an internship as <b>{cert['role_title']}</b> at "
            f"{org['name']} from <b>{fmt_date(cert['start'])}</b> to <b>{fmt_date(cert['end'])}</b>."
            if college else
            f"has successfully completed an internship as <b>{cert['role_title']}</b> at {org['name']} "
            f"from <b>{fmt_date(cert['start'])}</b> to <b>{fmt_date(cert['end'])}</b>.")
    if cert.get("highlight"):
        body += f" {cert['highlight'].rstrip('.')}."
    body += " We thank them for their contribution and wish them the very best."
    para = Paragraph(body, ParagraphStyle("cb", fontName="Mukta", fontSize=12.5, leading=19, textColor=INK,
                                           alignment=TA_CENTER))
    pw = W - 80 * mm
    _, ph = para.wrap(pw, 60 * mm)
    para.drawOn(c, (W - pw) / 2, H - 103 * mm - ph)

    # signature (left)
    base = 36 * mm
    sx = 40 * mm
    if sig.get("signature_png"):
        simg = ImageReader(io.BytesIO(sig["signature_png"]))
        siw, sih = simg.getSize()
        h = 14 * mm
        c.drawImage(simg, sx, base + 2 * mm, h * siw / sih, h, mask="auto")
    c.setStrokeColor(INK_FAINT)
    c.setLineWidth(0.6)
    c.line(sx, base, sx + 60 * mm, base)
    c.setFillColor(INK)
    c.setFont("Mukta-SemiBold", 11)
    c.drawString(sx, base - 5.5 * mm, sig["name"])
    c.setFont("Mukta", 9.5)
    c.setFillColor(INK_DIM)
    c.drawString(sx, base - 10 * mm, sig["title"])

    # date (centre)
    c.setFont("Mukta", 9.5)
    c.drawCentredString(W / 2, base - 5.5 * mm, f"Issued on {fmt_date(cert['issued'])}")
    c.setFont("Mukta-SemiBold", 9.5)
    c.drawCentredString(W / 2, base - 10 * mm, f"Certificate No. {doc_id}")

    # seal + QR (right)
    seal = ImageReader(str(ASSETS / "dwanilive-icon.png"))
    c.drawImage(seal, W - 88 * mm, base - 13 * mm, 26 * mm, 26 * mm, mask="auto")
    _qr(c, verify_url, W - 58 * mm, base - 13 * mm, 26 * mm)
    c.setFont("Mukta", 7.5)
    c.setFillColor(INK_FAINT)
    c.drawCentredString(W - 45 * mm, base - 17 * mm, "Scan to verify")

    c.showPage()
    c.save()
    return out
