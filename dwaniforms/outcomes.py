"""What a finished flow produces besides the filled fields.

  grievance -> a complaint letter        (English: the translated complaint; Hindi: the citizen's own words)
  rti       -> an RTI application under Section 6(1) of the RTI Act, 2005
  lookup    -> the record found in the locally cached dataset (records.py)
  advisor   -> schemes the person likely qualifies for (eligibility.py)

Each outcome is a dict: {"type", "title", "say" (spoken to the citizen, their language), ...type-specific keys}.
Drafts are only drafts: the citizen (or operator) reads, signs and submits them; nothing is submitted from here.
"""
from __future__ import annotations

import datetime as dt

DRAFT_FOOTER_EN = "This draft was prepared with DwaniForms from what the applicant said. Please read it before signing."
DRAFT_FOOTER_HI = "यह मसौदा आवेदक की कही बातों से DwaniForms ने तैयार किया है। हस्ताक्षर से पहले पढ़ लें।"


def _v(session, fid: str, native: bool = False) -> str:
    a = session.answers.get(fid)
    if not a:
        return ""
    return (a.native if native and a.native else a.value) or ""


def _date_en(iso: str) -> str:
    try:
        return dt.date.fromisoformat(iso).strftime("%d %B %Y")
    except ValueError:
        return iso


def _hi_date(iso: str) -> str:
    from .spoken import MONTH_NAMES
    try:
        d = dt.date.fromisoformat(iso)
    except ValueError:
        return iso
    return f"{d.day} {MONTH_NAMES['hi'][d.month - 1]} {d.year}"


def _dept_hi(value: str) -> str:
    from .extract import departments
    return departments().get(value, {}).get("hi", value)


def grievance_draft(session, today: dt.date | None = None, **_) -> dict:
    today = today or dt.date.today()
    dep, place = _v(session, "department"), _v(session, "location")
    when = _v(session, "incident_date")
    en = [
        f"Date: {today.strftime('%d %B %Y')}",
        "",
        "To,",
        f"The Officer-in-charge, {dep}" + (f" ({place})" if place else ""),
        "",
        f"Subject: Complaint regarding {dep.split(' (')[0].lower()}" + (f" at {place}" if place else ""),
        "",
        "Sir/Madam,",
        "",
        _v(session, "complaint"),
    ]
    if when:
        en.append(f"This happened on / since {_date_en(when)}.")
    if _v(session, "relief"):
        en += ["", f"I request the following action: {_v(session, 'relief')}"]
    en += ["", "Kindly look into this and take action at the earliest.", "",
           "Yours faithfully,", _v(session, "full_name"), f"Mobile: {_v(session, 'mobile')}", "", DRAFT_FOOTER_EN]
    drafts = {"en": "\n".join(en)}
    if session.lang == "hi":
        hi = [f"दिनांक: {_hi_date(today.isoformat())}", "", "सेवा में,", f"प्रभारी अधिकारी, {_dept_hi(dep)}" +
              (f" ({_v(session, 'location', True)})" if place else ""), "",
              f"विषय: {_dept_hi(dep)} से संबंधित शिकायत", "", "महोदय/महोदया,", "", _v(session, "complaint", True)]
        if when:
            hi.append(f"यह {_hi_date(when)} को / से हो रहा है।")
        if _v(session, "relief"):
            hi += ["", f"मेरा अनुरोध है: {_v(session, 'relief', True)}"]
        hi += ["", "कृपया इस पर शीघ्र कार्रवाई करें।", "", "भवदीय,", _v(session, "full_name", True),
               f"मोबाइल: {_v(session, 'mobile')}", "", DRAFT_FOOTER_HI]
        drafts["hi"] = "\n".join(hi)
    return {
        "type": "draft", "title": "Complaint letter", "drafts": drafts,
        "next_steps": [
            "Print, sign and submit it at the office, or file it on your state's public grievance portal.",
            "For central government departments you can file it on CPGRAMS (pgportal.gov.in).",
            "Keep the acknowledgement / registration number you are given.",
        ],
        "say_key": "outcome_grievance",
    }


def rti_draft(session, today: dt.date | None = None, **_) -> dict:
    today = today or dt.date.today()
    bpl = _v(session, "bpl") == "Yes"
    period = _v(session, "period")
    en = [
        "To,",
        f"The Public Information Officer (PIO), {_v(session, 'department')}",
        "",
        "Subject: Application for information under Section 6(1) of the Right to Information Act, 2005",
        "",
        f"1. Name of the applicant: {_v(session, 'full_name')}",
        f"2. Address for correspondence: {_v(session, 'address')}, PIN {_v(session, 'pincode')}",
        f"3. Information sought: {_v(session, 'information')}",
    ]
    n = 4
    if period:
        en.append(f"{n}. Period to which the information relates: {period}")
        n += 1
    en.append(f"{n}. Preferred mode of receiving the information: {_v(session, 'delivery')}")
    n += 1
    if bpl:
        en.append(f"{n}. I belong to the Below Poverty Line (BPL) category; a copy of my BPL card is attached. "
                  "I am therefore exempt from the application fee.")
    else:
        en.append(f"{n}. I have paid the application fee of Rs. ____ by ____ (the fee and payment mode depend on the "
                  "government concerned; Rs. 10 for central public authorities).")
    en += ["", "I state that I am a citizen of India and that the information sought does not fall under the exemptions "
           "of Sections 8 and 9 of the Act, to the best of my knowledge.", "",
           f"Place: ____________     Date: {today.strftime('%d %B %Y')}", "",
           f"Signature: ____________     ({_v(session, 'full_name')})"]
    if _v(session, "mobile"):
        en.append(f"Mobile: {_v(session, 'mobile')}")
    en += ["", DRAFT_FOOTER_EN]
    drafts = {"en": "\n".join(en)}
    if session.lang == "hi":
        hi = ["सेवा में,", f"जन सूचना अधिकारी, {_dept_hi(_v(session, 'department'))}", "",
              "विषय: सूचना का अधिकार अधिनियम, 2005 की धारा 6(1) के अंतर्गत आवेदन", "",
              f"1. आवेदक का नाम: {_v(session, 'full_name', True)}",
              f"2. पत्राचार का पता: {_v(session, 'address', True)}, पिन {_v(session, 'pincode')}",
              f"3. माँगी गई सूचना: {_v(session, 'information', True)}"]
        k = 4
        if period:
            hi.append(f"{k}. सूचना की अवधि: {_v(session, 'period', True)}")
            k += 1
        hi.append(f"{k}. सूचना प्राप्त करने का माध्यम: " +
                  {"By post": "डाक द्वारा", "By email": "ईमेल द्वारा", "Inspection of records": "अभिलेखों का निरीक्षण"}
                  .get(_v(session, "delivery"), _v(session, "delivery")))
        k += 1
        hi.append(f"{k}. मैं गरीबी रेखा से नीचे (बीपीएल) श्रेणी से हूँ; बीपीएल कार्ड की प्रति संलग्न है, अतः शुल्क से मुक्त हूँ।"
                  if bpl else f"{k}. आवेदन शुल्क रु. ____ (माध्यम: ____) जमा किया गया है।")
        hi += ["", "मैं भारत का नागरिक हूँ।", "", f"स्थान: ________     दिनांक: {_hi_date(today.isoformat())}", "",
               f"हस्ताक्षर: ________     ({_v(session, 'full_name', True)})", "", DRAFT_FOOTER_HI]
        drafts["hi"] = "\n".join(hi)
    return {
        "type": "draft", "title": "RTI application", "drafts": drafts,
        "next_steps": [
            "Send it to the Public Information Officer of the department by post or by hand, with the fee (unless BPL).",
            "For central ministries and departments you can also file online at rtionline.gov.in; many states have "
            "their own RTI portals.",
            "The PIO must reply within 30 days (48 hours if it concerns someone's life or liberty). If not, you can "
            "file a first appeal.",
        ],
        "say_key": "outcome_rti",
    }


OUTCOMES = {"grievance": grievance_draft, "rti": rti_draft}   # lookup/advisor register themselves (records.py, eligibility.py)
