"""System messages in the citizen's language.

English and Hindi are hand-written (review with a native speaker before a pilot). Every other language is produced at
run time by the translation backend from the English text and cached -- flagged as machine-translated in Reply.machine_translated.
"""
from __future__ import annotations

import unicodedata

MESSAGES: dict[str, dict[str, str]] = {
    "en": {
        "welcome": "Hello. I will help you fill the {title} form. Please answer each question by speaking.",
        "you_said": "You said:", "is_correct": "Is this correct? Say yes or no.",
        "retry": "Okay, please say it again.",
        "done": "The form is complete. Please check it on the screen.",
        "ask_operator": "I am having trouble. Please ask the operator to help.",
        "choice_invalid": "I did not understand. Please choose one of:", "yesno_invalid": "Please say yes or no.",
        "aadhaar_length": "An Aadhaar number has 12 digits. Please say it again, slowly.",
        "aadhaar_invalid": "That Aadhaar number does not look right. Please say it again.",
        "mobile_length": "A mobile number has 10 digits. Please say it again.",
        "mobile_invalid": "A mobile number starts with 6, 7, 8 or 9. Please say it again.",
        "pin_invalid": "A PIN code has 6 digits. Please say it again.",
        "account_invalid": "I did not get a valid account number. Please say it again.",
        "pan_invalid": "A PAN looks like five letters, four digits, one letter. Please say it again.",
        "ifsc_invalid": "I did not get a valid IFSC code. Please say it again.",
        "date_invalid": "I did not understand the date. Say day, month and year.",
        "date_future": "That date is in the future. Please say it again.",
        "age_low": "You are too young for this form.", "age_high": "That age is above the allowed limit.",
        "number_invalid": "I did not hear a number. Please say it again.", "number_range": "That number is outside the allowed range.",
        "text_short": "I did not catch that. Please say it again.",
        "number_whole": "Please say a whole number.",
        "lookup_no_district": "I could not find that district in the saved records. Please say it again.",
        "lookup_no_village": "I could not find that village in this district's saved records. Please say it again.",
        "lookup_ambiguous": "More than one place matches. Please say the name again, more clearly.",
        "lookup_not_found": "I could not find {what} in the saved records. Please check the number, or ask the operator.",
        "lookup_land": "Khasra {khasra}. Owner: {owners}. Area: {area}. Land type: {kind}.",
        "lookup_ration": "This ration card is in the {category} category, with {members} members. Ration shop: {shop}.",
        "records_as_of": "These details are from the copy of the records saved on {date}.",
        "records_stale": "This copy is old. Please also check the official portal.",
        "records_demo": "These are demonstration records, not real ones.",
        "outcome_grievance": "Your complaint letter is ready on the screen. Print it, sign it and submit it.",
        "outcome_rti": "Your RTI application is ready on the screen. Print it, sign it and send it to the department.",
        "advisor_result": "You may be able to get {count} schemes: {names}. The details and the forms to fill are on the screen.",
        "advisor_none": "I could not find a scheme that matches your answers. Please ask the operator about other schemes.",
        "advisor_disclaimer": "This is guidance only. The office decides after checking your documents.",
        "partial_digits": "I got {got} digits. Please say the remaining {left} digits.",
        "suggested": "I noted {label}: {value}.",
        "final_intro": "Here is what I have.",
        "final_ok": "Is everything correct? Say yes or no.",
        "fix_which": "Which part should I change? Say one of:",
        "khasra_invalid": "I did not get the khasra number. Say it like one hundred forty five by two.",
        "ration_invalid": "I did not get the ration card number. Please say it again, slowly.",
        "unit_acres": "Please tell me the land in acres. Bigha, kanal and guntha differ from state to state.",
    },
    "hi": {
        "welcome": "नमस्ते। मैं आपका {title} फ़ॉर्म भरने में मदद करूँगा। कृपया हर सवाल का जवाब बोलकर दीजिए।",
        "you_said": "आपने कहा:", "is_correct": "क्या यह सही है? हाँ या नहीं बोलिए।",
        "retry": "ठीक है, कृपया दोबारा बोलिए।",
        "done": "फ़ॉर्म पूरा हो गया है। कृपया स्क्रीन पर जाँच लीजिए।",
        "ask_operator": "मुझे दिक्कत हो रही है। कृपया ऑपरेटर से मदद लीजिए।",
        "choice_invalid": "मैं समझ नहीं पाया। कृपया इनमें से चुनिए:", "yesno_invalid": "कृपया हाँ या नहीं बोलिए।",
        "aadhaar_length": "आधार नंबर में 12 अंक होते हैं। कृपया धीरे-धीरे दोबारा बोलिए।",
        "aadhaar_invalid": "यह आधार नंबर सही नहीं लग रहा। कृपया दोबारा बोलिए।",
        "mobile_length": "मोबाइल नंबर में 10 अंक होते हैं। कृपया दोबारा बोलिए।",
        "mobile_invalid": "मोबाइल नंबर 6, 7, 8 या 9 से शुरू होता है। कृपया दोबारा बोलिए।",
        "pin_invalid": "पिन कोड में 6 अंक होते हैं। कृपया दोबारा बोलिए।",
        "account_invalid": "मुझे सही खाता संख्या नहीं मिली। कृपया दोबारा बोलिए।",
        "pan_invalid": "पैन में पाँच अक्षर, चार अंक और एक अक्षर होता है। कृपया दोबारा बोलिए।",
        "ifsc_invalid": "मुझे सही आईएफ़एससी कोड नहीं मिला। कृपया दोबारा बोलिए।",
        "date_invalid": "मैं तारीख़ समझ नहीं पाया। दिन, महीना और साल बोलिए।",
        "date_future": "यह तारीख़ आने वाले समय की है। कृपया दोबारा बोलिए।",
        "age_low": "इस फ़ॉर्म के लिए आपकी उम्र कम है।", "age_high": "उम्र अनुमत सीमा से अधिक है।",
        "number_invalid": "मुझे कोई संख्या सुनाई नहीं दी। कृपया दोबारा बोलिए।", "number_range": "यह संख्या अनुमत सीमा से बाहर है।",
        "text_short": "मैं समझ नहीं पाया। कृपया दोबारा बोलिए।",
        "number_whole": "कृपया पूरी संख्या बोलिए।",
        "lookup_no_district": "सहेजे गए रिकॉर्ड में यह जिला नहीं मिला। कृपया दोबारा बोलिए।",
        "lookup_no_village": "इस जिले के सहेजे गए रिकॉर्ड में यह गाँव नहीं मिला। कृपया दोबारा बोलिए।",
        "lookup_ambiguous": "एक से ज़्यादा जगहें मिल रही हैं। कृपया नाम दोबारा साफ़ बोलिए।",
        "lookup_not_found": "सहेजे गए रिकॉर्ड में {what} नहीं मिला। नंबर जाँच लीजिए, या ऑपरेटर से मदद लीजिए।",
        "lookup_land": "खसरा {khasra}। मालिक: {owners}। रकबा: {area}। भूमि का प्रकार: {kind}।",
        "lookup_ration": "यह राशन कार्ड {category} श्रेणी का है, {members} सदस्य हैं। राशन दुकान: {shop}।",
        "records_as_of": "यह जानकारी {date} को सहेजे गए रिकॉर्ड से है।",
        "records_stale": "यह रिकॉर्ड पुराना है। सरकारी पोर्टल पर भी जाँच लीजिए।",
        "records_demo": "यह डेमो रिकॉर्ड है, असली नहीं।",
        "outcome_grievance": "आपका शिकायत पत्र स्क्रीन पर तैयार है। इसे प्रिंट करके हस्ताक्षर कीजिए और जमा कीजिए।",
        "outcome_rti": "आपका आरटीआई आवेदन स्क्रीन पर तैयार है। इसे प्रिंट करके हस्ताक्षर कीजिए और विभाग को भेजिए।",
        "advisor_result": "आपको {count} योजनाएँ मिल सकती हैं: {names}। पूरी जानकारी और भरने वाले फ़ॉर्म स्क्रीन पर हैं।",
        "advisor_none": "आपके जवाबों से मेल खाती कोई योजना नहीं मिली। दूसरी योजनाओं के बारे में ऑपरेटर से पूछिए।",
        "advisor_disclaimer": "यह सिर्फ़ जानकारी है। अंतिम फ़ैसला विभाग आपके कागज़ जाँच कर करेगा।",
        "partial_digits": "{got} अंक मिल गए। बाकी {left} अंक बोलिए।",
        "suggested": "मैंने {label} समझा: {value}।",
        "final_intro": "आपने जो बताया वह यह है।",
        "final_ok": "क्या सब सही है? हाँ या नहीं बोलिए।",
        "fix_which": "क्या बदलना है? इनमें से बोलिए:",
        "khasra_invalid": "मुझे खसरा नंबर समझ नहीं आया। ऐसे बोलिए: एक सौ पैंतालीस बटा दो।",
        "ration_invalid": "मुझे राशन कार्ड नंबर समझ नहीं आया। कृपया धीरे-धीरे दोबारा बोलिए।",
        "unit_acres": "कृपया ज़मीन एकड़ में बताइए। बीघा, कनाल और गुंठा हर राज्य में अलग होते हैं।",
    },
}

_YES = {
    "yes", "yeah", "yep", "yup", "correct", "right", "ok", "okay", "sure", "haan", "han", "ji", "sahi", "theek", "thik",
    "हाँ", "हां", "जी", "सही", "ठीक", "बिल्कुल", "ஆம்", "சரி", "হ্যাঁ", "ঠিক", "అవును", "సరే", "होय", "बरोबर", "હા", "સાચું",
    "ಹೌದು", "ಸರಿ", "അതെ", "ശരി", "ਹਾਂ", "ਠੀਕ", "جی", "ہاں", "صحیح", "ହଁ", "ହଁ",
}
_NO = {
    "no", "nope", "wrong", "incorrect", "nahi", "nahin", "na", "galat", "नहीं", "नही", "ना", "गलत", "ग़लत", "இல்லை", "தவறு",
    "না", "নয়", "ভুল", "కాదు", "లేదు", "తప్పు", "नाही", "चूक", "ના", "નહીં", "ખોટું", "ಇಲ್ಲ", "ಅಲ್ಲ", "തെറ്റ്", "അല്ല", "ਨਹੀਂ",
    "ਗਲਤ", "نہیں", "غلط", "ନା", "ନାହିଁ",
}
_SKIP = {
    "skip", "next", "none", "nothing", "no", "nahi", "nahin", "chhodo", "na", "nil", "not applicable",
    "छोड़ो", "छोड़ें", "छोड़िए", "आगे", "कोई नहीं", "नहीं", "नही", "कुछ नहीं", "लागू नहीं",
}
_SKIP_PHRASES = {"i don t have", "i don t have it", "don t have", "i do not have", "i dont have", "i do not have it", "i dont have it", "do not have", "dont have",
                 "i have none", "i have nothing", "मेरे पास नहीं है", "मेरे पास नहीं"}
_SKIP_FILLER = {"please", "pls", "ok", "okay", "i", "have", "do", "not", "dont", "don't", "bas", "बस", "कृपया", "जी", "हाँ", "हां",
                "yes", "haan", "ji"}


def _words(text: str) -> list[str]:
    cleaned = "".join(" " if (unicodedata.category(c).startswith(("P", "S"))) else c for c in text.lower())
    return [w for w in cleaned.split() if w]


# "No problem" / "कोई बात नहीं" contain a "no" word but are agreement, not refusal ("हाँ, कोई दिक्कत नहीं").
_BENIGN_NEGATIONS = [
    "no problem", "no problems", "not a problem", "no issue", "no issues", "no worries",
    "koi baat nahi", "koi baat nahin", "koi dikkat nahi", "koi dikkat nahin", "koi problem nahi", "koi pareshani nahi",
    "कोई बात नहीं", "कोई दिक्कत नहीं", "कोई परेशानी नहीं", "कोई समस्या नहीं", "कोई प्रॉब्लम नहीं", "कोई गलती नहीं",
]


def parse_yes_no(text: str) -> bool | None:
    """True / False / None (unclear). 'no' wins ties: a wrong 'yes' saves bad data, a wrong 'no' costs one retry."""
    joined = " " + " ".join(_words(text)) + " "
    for phrase in _BENIGN_NEGATIONS:
        joined = joined.replace(" " + " ".join(_words(phrase)) + " ", " ")
    words = joined.split()
    yes = any(w in _YES for w in words)
    no = any(w in _NO for w in words)
    if no:
        return False
    return True if yes else None


def is_skip(text: str) -> bool:
    """True only when the WHOLE utterance means "skip" (optionally with filler like "please"). A skip word inside a real
    answer ("next to the temple", "मंदिर के आगे") must not skip the field."""
    raw = " ".join(_words(text))
    if raw in _SKIP_PHRASES:
        return True
    words = [w for w in _words(text) if w not in _SKIP_FILLER or w in _SKIP]
    t = " ".join(words)
    return t in _SKIP or (bool(words) and all(w in _SKIP for w in words) and len(words) <= 2)
