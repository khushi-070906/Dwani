"""What a finished flow produces besides the filled fields.

  grievance -> a complaint letter        (English: the translated complaint; Hindi: the citizen's own words)
  rti       -> an RTI application under Section 6(1) of the RTI Act, 2005
  rti_appeal -> a first appeal under Section 19(1), with the 30-day window worked out from the dates given
  lookup    -> the record found in the locally cached dataset (records.py)
  advisor   -> schemes the person likely qualifies for (eligibility.py)

Each outcome is a dict: {"type", "title", "say" (spoken to the citizen, their language), ...type-specific keys}.
Drafts are only drafts: the citizen (or operator) reads, signs and submits them; nothing is submitted from here.
"""
from __future__ import annotations

import datetime as dt
from . import clock

DRAFT_FOOTER_EN = "This draft was prepared with DwaniForms from what the applicant said. Please read it before signing."
DRAFT_FOOTER_HI = "यह मसौदा आवेदक की कही बातों से DwaniForms ने तैयार किया है। हस्ताक्षर से पहले पढ़ लें।"


GRIEVANCE_NEXT_EN = (
    "Print, sign and submit it at the office, or file it on your state's public grievance portal.",
    "For central government departments you can file it on CPGRAMS (pgportal.gov.in).",
    "Keep the acknowledgement / registration number you are given.",
)
RTI_NEXT_EN = (
    "Send it to the Public Information Officer of the department by post or by hand, with the fee (unless BPL).",
    "For central ministries and departments you can also file online at rtionline.gov.in; many states have "
    "their own RTI portals.",
    "The PIO must reply within 30 days (48 hours if it concerns someone's life or liberty). If not, you can "
    "file a first appeal.",
)
RTI_APPEAL_NEXT_EN = (
    "Send it to the First Appellate Authority: the officer senior to the Public Information Officer in the same "
    "department. Attach copies of your RTI application and of the reply, if any.",
    "Central government departments charge no fee for a first appeal; some states do, so check your state's rules.",
    "The appeal must be decided within 30 days, 45 at most. If you are still not satisfied, you can make a second "
    "appeal to the Information Commission within 90 days.",
)
NEXT_STEPS_EN = (*GRIEVANCE_NEXT_EN, *RTI_NEXT_EN, *RTI_APPEAL_NEXT_EN)


def _local_steps(session, steps_en) -> dict:
    """Next steps in the citizen's language when a language pack has them (the letter itself stays English / Hindi:
    it goes to an office)."""
    from . import langpacks
    local = [langpacks.text(session.lang, s) for s in steps_en]
    return {"next_steps_local": local} if all(local) else {}


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
    today = today or clock.today()
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
        en.append(f"The problem began on {_date_en(when)}.")
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
            hi.append(f"यह समस्या {_hi_date(when)} से है।")
        if _v(session, "relief"):
            hi += ["", f"मेरा अनुरोध है: {_v(session, 'relief', True)}"]
        hi += ["", "कृपया इस पर शीघ्र कार्रवाई करें।", "", "भवदीय,", _v(session, "full_name", True),
               f"मोबाइल: {_v(session, 'mobile')}", "", DRAFT_FOOTER_HI]
        drafts["hi"] = "\n".join(hi)
    return {
        "type": "draft", "title": "Complaint letter", "drafts": drafts,
        "next_steps": list(GRIEVANCE_NEXT_EN), **_local_steps(session, GRIEVANCE_NEXT_EN),
        "next_steps_hi": [
            "प्रिंट करके हस्ताक्षर कीजिए और दफ़्तर में जमा कीजिए, या अपने राज्य के शिकायत पोर्टल पर दर्ज कीजिए।",
            "केंद्र सरकार के विभागों के लिए आप इसे CPGRAMS (pgportal.gov.in) पर भी दर्ज कर सकते हैं।",
            "जो पावती / पंजीकरण नंबर मिले, उसे संभालकर रखिए।",
        ],
        "say_key": "outcome_grievance",
    }


def rti_draft(session, today: dt.date | None = None, **_) -> dict:
    today = today or clock.today()
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
        "next_steps": list(RTI_NEXT_EN), **_local_steps(session, RTI_NEXT_EN),
        "next_steps_hi": [
            "इसे विभाग के जन सूचना अधिकारी को डाक से या हाथ से, फ़ीस के साथ भेजिए (बीपीएल हों तो फ़ीस नहीं)।",
            "केंद्रीय मंत्रालयों और विभागों के लिए rtionline.gov.in पर ऑनलाइन भी भर सकते हैं; कई राज्यों के अपने आरटीआई पोर्टल हैं।",
            "जन सूचना अधिकारी को 30 दिन में जवाब देना होता है (जीवन या स्वतंत्रता से जुड़ा हो तो 48 घंटे)। जवाब न मिले तो प्रथम अपील कर सकते हैं।",
        ],
        "say_key": "outcome_rti",
    }


def appeal_timing(rti_date: str, reply_date: str, today: dt.date) -> dict:
    """RTI Act s.7(1)/19(1): the PIO has 30 days; the appeal is due within 30 days of that period running out, or of
    the reply. Returns {"early": date the PIO's time ends} | {"late": True} | {} (on time), plus "last" (ISO)."""
    try:
        sent = dt.date.fromisoformat(rti_date)
    except ValueError:
        return {}
    pio_until = sent + dt.timedelta(days=30)
    if reply_date:
        try:
            last = dt.date.fromisoformat(reply_date) + dt.timedelta(days=30)
        except ValueError:
            return {}
        return {"last": last.isoformat(), **({"late": True} if today > last else {})}
    last = pio_until + dt.timedelta(days=30)
    if today <= pio_until:
        return {"early": pio_until.isoformat(), "last": last.isoformat()}
    return {"last": last.isoformat(), **({"late": True} if today > last else {})}


def rti_appeal_draft(session, today: dt.date | None = None, **_) -> dict:
    today = today or clock.today()
    dep, sent, replied = _v(session, "department"), _v(session, "rti_date"), _v(session, "reply_date")
    got_reply = _v(session, "reply_status").startswith("Reply")
    timing = appeal_timing(sent, replied if got_reply else "", today)
    n = 6
    en = [
        "To,",
        "The First Appellate Authority",
        f"(Officer senior to the Public Information Officer), {dep}",
        "",
        "Subject: First appeal under Section 19(1) of the Right to Information Act, 2005",
        "",
        f"1. Name of the appellant: {_v(session, 'full_name')}",
        f"2. Address for correspondence: {_v(session, 'address')}, PIN {_v(session, 'pincode')}",
        f"3. RTI application dated {_date_en(sent)}, sent to the Public Information Officer, {dep}.",
        f"4. Information asked for: {_v(session, 'information')}",
    ]
    if got_reply:
        en += [f"5. Grounds of appeal: A reply dated {_date_en(replied)} was received. It is not satisfactory: "
               f"{_v(session, 'grounds')}",
               "6. Relief sought: Direct the Public Information Officer to provide the complete information asked for."]
    else:
        en += ["5. Grounds of appeal: No information and no reply was received within the 30 days allowed by Section "
               "7(1) of the Act. Under Section 7(2) this is deemed a refusal.",
               "6. Relief sought: Direct the Public Information Officer to provide the complete information asked for, "
               "free of charge as required by Section 7(6), within a fixed time."]
    if timing.get("late"):
        n += 1
        en.append(f"{n}. This appeal is filed after the 30 days allowed. I request that the delay be condoned, as "
                  "permitted by the proviso to Section 19(1), because: ______________________________")
    en += ["", "Enclosures: copy of the RTI application; proof of sending and of the fee paid"
           + ("; copy of the reply" if got_reply else "") + ".",
           "", "I state that the facts above are true to the best of my knowledge.", "",
           f"Place: ____________     Date: {today.strftime('%d %B %Y')}", "",
           f"Signature: ____________     ({_v(session, 'full_name')})"]
    if _v(session, "mobile"):
        en.append(f"Mobile: {_v(session, 'mobile')}")
    en += ["", DRAFT_FOOTER_EN]
    drafts = {"en": "\n".join(en)}
    if session.lang == "hi":
        dep_hi = _dept_hi(dep)
        hi = ["सेवा में,", "प्रथम अपीलीय अधिकारी", f"(जन सूचना अधिकारी से वरिष्ठ अधिकारी), {dep_hi}", "",
              "विषय: सूचना का अधिकार अधिनियम, 2005 की धारा 19(1) के अंतर्गत प्रथम अपील", "",
              f"1. अपीलकर्ता का नाम: {_v(session, 'full_name', True)}",
              f"2. पत्राचार का पता: {_v(session, 'address', True)}, पिन {_v(session, 'pincode')}",
              f"3. दिनांक {_hi_date(sent)} का आरटीआई आवेदन, जन सूचना अधिकारी, {dep_hi} को भेजा गया।",
              f"4. माँगी गई सूचना: {_v(session, 'information', True)}"]
        if got_reply:
            hi += [f"5. अपील का आधार: दिनांक {_hi_date(replied)} का जवाब मिला, जो संतोषजनक नहीं है: "
                   f"{_v(session, 'grounds', True)}",
                   "6. माँगी गई राहत: जन सूचना अधिकारी को माँगी गई पूरी सूचना देने का निर्देश दिया जाए।"]
        else:
            hi += ["5. अपील का आधार: अधिनियम की धारा 7(1) में दिए 30 दिनों में न सूचना मिली, न कोई जवाब। धारा 7(2) के अनुसार "
                   "इसे सूचना देने से इनकार माना जाता है।",
                   "6. माँगी गई राहत: जन सूचना अधिकारी को माँगी गई पूरी सूचना, धारा 7(6) के अनुसार निःशुल्क, तय समय में देने का "
                   "निर्देश दिया जाए।"]
        if timing.get("late"):
            hi.append("7. यह अपील 30 दिन बीतने के बाद की जा रही है। धारा 19(1) के परंतुक के अनुसार देरी माफ़ करने का अनुरोध है, "
                      "क्योंकि: ______________________________")
        hi += ["", "संलग्न: आरटीआई आवेदन की प्रति; भेजने और शुल्क की रसीद" + ("; जवाब की प्रति" if got_reply else "") + "।",
               "", "मैं घोषित करता/करती हूँ कि ऊपर दी गई बातें मेरी जानकारी में सही हैं।", "",
               f"स्थान: ________     दिनांक: {_hi_date(today.isoformat())}", "",
               f"हस्ताक्षर: ________     ({_v(session, 'full_name', True)})", "", DRAFT_FOOTER_HI]
        drafts["hi"] = "\n".join(hi)
    say_more = []
    if timing.get("early"):
        say_more.append(["rti_appeal_early", {"date": timing["early"]}])
    elif timing.get("late"):
        say_more.append(["rti_appeal_late", {}])
    return {
        "type": "draft", "title": "RTI first appeal", "drafts": drafts, "timing": timing,
        "next_steps": list(RTI_APPEAL_NEXT_EN), **_local_steps(session, RTI_APPEAL_NEXT_EN),
        "next_steps_hi": [
            "इसे प्रथम अपीलीय अधिकारी को भेजिए: उसी विभाग में जन सूचना अधिकारी से वरिष्ठ अधिकारी। आरटीआई आवेदन और जवाब (अगर आया हो) की प्रति साथ लगाइए।",
            "केंद्र सरकार के विभाग प्रथम अपील की कोई फ़ीस नहीं लेते; कुछ राज्य लेते हैं, इसलिए अपने राज्य के नियम देख लीजिए।",
            "अपील का फ़ैसला 30 दिन में, ज़्यादा से ज़्यादा 45 दिन में होना चाहिए। फिर भी संतुष्ट न हों तो 90 दिन में सूचना आयोग में दूसरी अपील कर सकते हैं।",
        ],
        "say_key": "outcome_rti_appeal", "say_more": say_more,
    }


OUTCOMES = {"grievance": grievance_draft, "rti": rti_draft, "rti_appeal": rti_appeal_draft}
# lookup/advisor register themselves (records.py, eligibility.py)
