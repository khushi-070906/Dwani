/*
 * i18n.js -- translates DwaniLive's own interface (buttons, labels, status
 * messages) on the attendee and presenter pages.
 *
 * How it works: the pages stay written in English. This file swaps any text
 * node / placeholder / aria-label / title whose English text appears in the
 * dictionary below, and keeps doing so as the page's JavaScript changes text
 * later (a MutationObserver). Captions, questions and other user content are
 * never touched (CAPTION_AREAS), and anything not in the dictionary simply
 * stays English, so a missing translation can never break the page.
 *
 * Adding a language = adding one block to DICTS (+ PATTERNS for text with
 * numbers in it). Have a native speaker review it before shipping.
 */
(function () {
  "use strict";

  var HI = {
    // ---- attendee page
    "Live": "लाइव",
    "Pick your language": "अपनी भाषा चुनें",
    "Captions stream directly from the presenter's device over this room's network. Nothing leaves the building.":
      "कैप्शन सीधे वक्ता के डिवाइस से इसी कमरे के नेटवर्क पर आते हैं। कुछ भी बाहर नहीं जाता।",
    "Join": "जुड़ें",
    "Connecting…": "जुड़ रहे हैं…",
    "💬 Ask": "💬 सवाल पूछें",
    "⚙ Accessibility": "⚙ सेटिंग्स",
    "💾 Save": "💾 सेव करें",
    "Change language": "भाषा बदलें",
    "Type your question in any language -- the presenter sees it in their own language.":
      "किसी भी भाषा में सवाल लिखें — वक्ता उसे अपनी भाषा में देखेंगे।",
    "Send": "भेजें",
    "Dark mode": "डार्क मोड",
    "High contrast (yellow on black)": "हाई कॉन्ट्रास्ट (काले पर पीला)",
    "Text size": "अक्षरों का आकार",
    "Line spacing": "पंक्तियों के बीच जगह",
    "Normal": "सामान्य",
    "Relaxed": "थोड़ी ज़्यादा",
    "Extra": "ज़्यादा",
    "Easy-read font & spacing": "आसानी से पढ़ा जाने वाला फ़ॉन्ट",
    "(helps many readers with dyslexia)": "(डिस्लेक्सिया में मददगार)",
    "Hold captions longer": "कैप्शन ज़्यादा देर दिखाएँ",
    "Flash on new caption": "नए कैप्शन पर चमक",
    "⛶ Full screen / projector": "⛶ पूरी स्क्रीन / प्रोजेक्टर",
    "Reset": "रीसेट",
    "Help": "मदद",
    "Waiting for the presenter to begin…": "वक्ता के शुरू करने का इंतज़ार है…",
    "✕ Exit full screen": "✕ पूरी स्क्रीन से बाहर",
    "Connection to the presenter's device dropped.": "वक्ता के डिवाइस से कनेक्शन टूट गया।",
    "Retry now": "फिर से कोशिश करें",
    "Trouble connecting?": "जुड़ने में दिक्कत?",
    "Other language code, e.g. ja, ar": "दूसरी भाषा का कोड, जैसे ja, ar",
    "Other language code, for example ja or ar": "दूसरी भाषा का कोड, जैसे ja या ar",
    "Download everything shown so far as a text file": "अब तक के सारे कैप्शन टेक्स्ट फ़ाइल में डाउनलोड करें",
    "Type your question…": "अपना सवाल लिखें…",
    "Captions": "कैप्शन",
    "NO SESSION FOUND": "सेशन नहीं मिला",
    "Session not found": "सेशन नहीं मिला",
    "Session full": "सेशन भर गया",
    "This session is full. Tap Retry now to try again later.": "यह सेशन भर गया है। थोड़ी देर बाद \"फिर से कोशिश करें\" दबाएँ।",
    "This session code isn't recognized by the presenter's device anymore. Re-scan the presenter's QR code.":
      "वक्ता का डिवाइस अब इस सेशन कोड को नहीं पहचानता। वक्ता का QR कोड फिर से स्कैन करें।",
    "Disconnected": "कनेक्शन टूटा",
    "Reconnecting…": "फिर से जुड़ रहे हैं…",
    "Reconnecting to the presenter": "वक्ता से फिर से जुड़ रहे हैं",
    "Nothing to save yet": "अभी सेव करने को कुछ नहीं",
    "Full screen captions. Press Escape or the Exit button to leave.": "पूरी स्क्रीन पर कैप्शन। बाहर आने के लिए Escape या Exit दबाएँ।",
    "Sending…": "भेज रहे हैं…",
    "Couldn't send -- try again.": "नहीं भेज पाए — फिर कोशिश करें।",
    "Sent -- the presenter will see it shortly.": "भेज दिया — वक्ता इसे जल्द देखेंगे।",
    "Couldn't reach the server -- check your connection and try again.": "सर्वर तक नहीं पहुँच पाए — कनेक्शन जाँचें और फिर कोशिश करें।",
    "🤔 Lost me": "🤔 समझ नहीं आया",
    "🔊 Read captions aloud": "🔊 कैप्शन पढ़कर सुनाएँ",
    "Reading speed": "पढ़ने की रफ़्तार",
    "Slower": "धीमी",
    "Faster": "तेज़",
    "This browser can't read aloud. Try Chrome or Safari.": "यह ब्राउज़र पढ़कर नहीं सुना सकता। Chrome या Safari इस्तेमाल करें।",
    "✓ Sent to the presenter": "✓ वक्ता को बता दिया",
    "Tell the presenter you didn't follow that (anonymous)": "वक्ता को बताएँ कि यह समझ नहीं आया (नाम नहीं जाएगा)",

    // ---- presenter page
    "Using your phone as the mic: keep this screen open and unlocked, and silence calls & notifications during the talk.":
      "फ़ोन को माइक बनाया है: स्क्रीन खुली और अनलॉक रखें, और बोलते समय कॉल व नोटिफ़िकेशन बंद रखें।",
    "Presenter mic": "वक्ता का माइक",
    "Tap to start streaming your microphone to attendees over this room's local network. Nothing leaves the building.":
      "अपनी आवाज़ श्रोताओं तक भेजना शुरू करने के लिए टैप करें। सब कुछ इसी कमरे के नेटवर्क पर रहता है।",
    "Start": "शुरू करें",
    "Stop": "रोकें",
    "Not presenting": "अभी शुरू नहीं किया",
    "Requesting microphone…": "माइक की अनुमति माँग रहे हैं…",
    "Stopping…": "रोक रहे हैं…",
    "Live — streaming to attendees": "लाइव — श्रोताओं तक पहुँच रहा है",
    "Disconnected from the server.": "सर्वर से कनेक्शन टूट गया।",
    "Still loading session info -- wait a moment and try again.": "सेशन की जानकारी लोड हो रही है — थोड़ा रुककर फिर कोशिश करें।",
    "This session code isn't recognized by the server anymore.": "सर्वर अब इस सेशन कोड को नहीं पहचानता।",
    "Another presenter is already streaming for this session (stop the mic on the other device first).":
      "इस सेशन में कोई और पहले से माइक चला रहा है (पहले दूसरे डिवाइस पर माइक रोकें)।",
    "This link can't stream audio. Scan the 'Use your phone as the mic' QR in the DwaniLive window on the laptop.":
      "इस लिंक से आवाज़ नहीं भेजी जा सकती। लैपटॉप की DwaniLive विंडो में 'फ़ोन को माइक बनाएँ' वाला QR स्कैन करें।",
    "Caption delay —": "कैप्शन में देरी —",
    "Check setup": "सेटअप जाँचें",
    "Microphone": "माइक्रोफ़ोन",
    "Test microphone": "माइक जाँचें",
    "Click \"Test microphone\" and say something. The bar should move.": "\"माइक जाँचें\" दबाकर कुछ बोलें। पट्टी हिलनी चाहिए।",
    "Troubleshooting guide": "समस्या-समाधान गाइड",
    "· Updates every few seconds. Scan the join QR with your own phone: the phone test turns green when it connects.":
      "· हर कुछ सेकंड में अपडेट होता है। अपने फ़ोन से जॉइन QR स्कैन करें: जुड़ते ही फ़ोन टेस्ट हरा हो जाएगा।",
    "Join QR & link": "जॉइन QR और लिंक",
    "Copy link": "लिंक कॉपी करें",
    "Copied!": "कॉपी हो गया!",
    "Attendees": "श्रोता",
    "Questions": "सवाल",
    "Stats": "आँकड़े",
    "Notes": "नोट्स",
    "Connected attendees": "जुड़े हुए श्रोता",
    "Nobody has joined yet.": "अभी कोई नहीं जुड़ा है।",
    "Questions from the audience": "श्रोताओं के सवाल",
    "No questions yet.": "अभी कोई सवाल नहीं।",
    "Listening for questions": "सवालों का इंतज़ार है",
    "Mark answered": "जवाब दे दिया",
    "Answered": "जवाब दिया गया",
    "Live session stats": "लाइव सेशन के आँकड़े",
    "Attendees connected": "जुड़े हुए श्रोता",
    "Languages in the room": "कमरे में भाषाएँ",
    "Caption delay (typical)": "कैप्शन में देरी (आम तौर पर)",
    "Appears after the first captions": "पहले कैप्शन के बाद दिखेगा",
    "Start speaking to measure": "मापने के लिए बोलना शुरू करें",
    "Appears once someone has joined and you speak": "किसी के जुड़ने और आपके बोलने के बाद दिखेगा",
    "Captions sent": "भेजे गए कैप्शन",
    "\"Lost me\" taps": "\"समझ नहीं आया\" टैप",
    "Anonymous, this session": "बिना नाम के, इस सेशन में",
    "Slow down, or say it another way.": "थोड़ा धीरे बोलें, या दूसरे तरीके से समझाएँ।",
    "Using accessibility mode": "सुलभता मोड इस्तेमाल कर रहे",
    "Questions answered / asked": "जवाब दिए / पूछे गए सवाल",
    "Semantic cache hit rate": "कैश से मिले अनुवाद",
    "Glossary terms tracked": "शब्दावली के शब्द",
    "Microphone level": "माइक का स्तर",
    "Time from the end of a sentence to the caption being sent": "वाक्य खत्म होने से कैप्शन भेजे जाने तक का समय",
    "Too loud (distorting): move the microphone a little further away or lower its volume.":
      "आवाज़ बहुत तेज़ है (फट रही है): माइक थोड़ा दूर करें या उसका वॉल्यूम कम करें।",
    "SERVER UNREACHABLE": "सर्वर तक नहीं पहुँच पाए",
    "All set": "सब तैयार है",
    "How to fix →": "कैसे ठीक करें →",
    "You're live, so the microphone is already in use. Stop presenting to run the test.":
      "आप लाइव हैं, इसलिए माइक पहले से इस्तेमाल में है। जाँच के लिए पहले प्रस्तुति रोकें।",
    "Listening for 5 seconds. Say something…": "5 सेकंड तक सुन रहे हैं। कुछ बोलिए…",
    "Couldn't hear you. See the fix above.": "आपकी आवाज़ नहीं आई। ऊपर समाधान देखें।",
    "The browser blocked the microphone.": "ब्राउज़र ने माइक रोक दिया है।",
    "Session notes": "सेशन नोट्स",
    "Session notes are off": "सेशन नोट्स बंद हैं",
    "Saving paused": "सेव करना रुका है",
    "Pause saving": "सेव करना रोकें",
    "Resume saving": "सेव करना फिर शुरू करें",
    "Every caption is saved on this laptop (never uploaded). After the talk, download notes for students in any language:":
      "हर कैप्शन इसी लैपटॉप पर सेव होता है (कहीं अपलोड नहीं होता)। बात खत्म होने पर छात्रों के लिए किसी भी भाषा में नोट्स डाउनलोड करें:",
    "opens a printable page with highlights, key terms and the full transcript (use":
      "मुख्य बातें, ज़रूरी शब्द और पूरा ट्रांसक्रिप्ट वाला प्रिंट करने लायक पेज खोलता है (",
    "Save as PDF": "PDF के रूप में सेव करें",
    "is subtitles for your recording.": "आपकी रिकॉर्डिंग के सबटाइटल हैं।",
    "Language": "भाषा",
    "No sessions yet. Notes appear here once you start speaking.": "अभी कोई सेशन नहीं। बोलना शुरू करते ही नोट्स यहाँ दिखेंगे।",
    "Notes (PDF)": "नोट्स (PDF)",
    "Slides": "स्लाइड्स",
    "Upload your slides or notes. DwaniLive picks out the technical terms so they're heard correctly and never mistranslated. Works during a session too.":
      "अपनी स्लाइड्स या नोट्स अपलोड करें। DwaniLive इनमें से तकनीकी शब्द चुन लेता है, ताकि वे सही सुने जाएँ और उनका अनुवाद न बिगड़े। सेशन के बीच में भी काम करता है।",
    "Choose slides (.pptx, .pdf, .docx, .txt)": "स्लाइड्स चुनें (.pptx, .pdf, .docx, .txt)",
    "Untick anything that isn't a technical term:": "जो तकनीकी शब्द नहीं है, उसका निशान हटा दें:",
    "Add selected terms": "चुने हुए शब्द जोड़ें",
    "Terms in use": "इस्तेमाल हो रहे शब्द",
    "Add": "जोड़ें",
    "Add a term, e.g. backpropagation": "कोई शब्द जोड़ें, जैसे backpropagation",
    "Add a term": "शब्द जोड़ें",
    "Available when DwaniLive runs with its speech models (not in demo mode).": "यह तब उपलब्ध है जब DwaniLive अपने मॉडल के साथ चल रहा हो (डेमो मोड में नहीं)।",
    "Speech recognition is listening for these terms; translation keeps them intact.": "आवाज़ पहचान इन शब्दों को ध्यान से सुनती है, और अनुवाद इन्हें ज्यों का त्यों रखता है।",
    "No new technical terms found in that file.": "इस फ़ाइल में कोई नया तकनीकी शब्द नहीं मिला।",
    "Couldn't reach DwaniLive on this laptop.": "इस लैपटॉप पर DwaniLive से संपर्क नहीं हो पाया।",
    "Couldn't read that file.": "यह फ़ाइल पढ़ी नहीं जा सकी।",
    "Free plan: the full transcript (.txt) in the talk's language. Pro adds printable notes with highlights, subtitles and every language.":
      "फ़्री प्लान: बात की भाषा में पूरा ट्रांसक्रिप्ट (.txt)। प्रो में मुख्य बातों वाले नोट्स, सबटाइटल और हर भाषा मिलती है।",
    "· Pro": "· प्रो",
    "I'm speaking": "मेरी भाषा",
    "Auto-detect (Hindi + English mix)": "अपने आप पहचानें (हिंदी-अंग्रेज़ी मिली-जुली)",
    "Detecting the language of each sentence.": "हर वाक्य की भाषा अपने आप पहचानी जाएगी।",
    "That language isn't supported.": "यह भाषा अभी उपलब्ध नहीं है।",
    "Slides vocabulary is part of the Pro and Institution plans.": "स्लाइड्स शब्दावली प्रो और इंस्टीट्यूशन प्लान में मिलती है।",
    "Pro feature": "प्रो फ़ीचर",
    // setup-check titles (their details come from the laptop in English)
    "Speech recognition": "आवाज़ पहचान",
    "Translation": "अनुवाद",
    "Network": "नेटवर्क",
    "Network adapters": "नेटवर्क अडैप्टर",
    "Join address": "जॉइन पता",
    "Firewall": "फ़ायरवॉल",
    "Windows Firewall": "विंडोज़ फ़ायरवॉल",
    "Phone test": "फ़ोन टेस्ट",
    "Phone as microphone": "फ़ोन को माइक बनाएँ",
    "Plan": "प्लान",
    "Browser security": "ब्राउज़र सुरक्षा",
    "Microphone access": "माइक की अनुमति"
  };

  // Text with numbers or names in it: [regex on the English, Hindi template]
  var HI_PATTERNS = [
    [/^Live · reconnected \(~(\d+)s missed\)$/, "लाइव · फिर से जुड़े (~$1 सेकंड छूटे)"],
    [/^Reconnecting in (\d+)s…$/, "$1 सेकंड में फिर से जुड़ेंगे…"],
    [/^SESSION (.+)$/, "सेशन $1"],
    [/^Saving notes · (\d+) captions$/, "नोट्स सेव हो रहे हैं · $1 कैप्शन"],
    [/^(\d+) of (\d+) seats used on your plan: upgrade from your dashboard for more\.$/,
      "आपके प्लान की $2 में से $1 सीटें भर गई हैं: ज़्यादा के लिए डैशबोर्ड से अपग्रेड करें।"],
    [/^Caption delay ~([\d.]+)s$/, "कैप्शन में देरी ~$1 सेकंड"],
    [/^slowest ([\d.]+)s · speech→text ([\d.]+)s · translation ([\d.]+)s$/,
      "सबसे धीमा $1 से. · आवाज़→टेक्स्ट $2 से. · अनुवाद $3 से."],
    [/^last one (\d+)s ago$/, "आख़िरी $1 सेकंड पहले"],
    [/^Can't hear anything for (\d+)s: is the microphone muted or the wrong one selected\?$/,
      "$1 सेकंड से कुछ सुनाई नहीं दिया: क्या माइक म्यूट है या गलत माइक चुना है?"],
    [/^Microphone works \((.+)\)\.$/, "माइक काम कर रहा है ($1)।"],
    [/^(\d+) problems? to fix$/, "$1 समस्या ठीक करनी है"],
    [/^Ready, (\d+) things? to check$/, "तैयार है, $1 बात जाँच लें"],
    [/^This session · (.+)$/, "यह सेशन · $1"],
    [/^([\d.]+) min · (\d+) captions · (.+) will be translated on download \(about a minute per hour of talk\)$/,
      "$1 मिनट · $2 कैप्शन · $3 का अनुवाद डाउनलोड के समय होगा (हर घंटे की बात पर लगभग एक मिनट)"],
    [/^([\d.]+) min · (\d+) captions$/, "$1 मिनट · $2 कैप्शन"],
    [/^Asked in (.+)$/, "$1 में पूछा गया"],
    [/^Your phone has no (.+) voice\. Add one in your phone's text-to-speech settings\.$/,
      "आपके फ़ोन में $1 की आवाज़ नहीं है। फ़ोन की टेक्स्ट-टू-स्पीच सेटिंग में जोड़ें।"],
    [/^Reading (.+)…$/, "$1 पढ़ रहे हैं…"],
    [/^Listening for (.+) from the next sentence\.$/, "अगले वाक्य से $1 सुनेंगे।"],
    [/^Found (\d+) possible terms\.$/, "$1 संभावित शब्द मिले।"],
    [/^Added (\d+) terms\. They're used from the next sentence\.$/, "$1 शब्द जोड़े गए। अगले वाक्य से इस्तेमाल होंगे।"],
    [/^Remove (.+)$/, "$1 हटाएँ"],
    [/^🤔 (\d+) of (\d+) lost you in the last minute$/, "🤔 पिछले एक मिनट में $2 में से $1 लोगों को समझ नहीं आया"],
    [/^on: “…(.+)”$/, "इस पर: “…$1”"],
    [/^Original: (.+)$/, "मूल: $1"],
    [/^Microphone access denied or unavailable: (.+)$/, "माइक की अनुमति नहीं मिली या माइक उपलब्ध नहीं: $1"],
    [/^Couldn't load session info from the server: (.+)$/, "सर्वर से सेशन की जानकारी नहीं मिली: $1"]
  ];

  var DICTS = { hi: HI };
  var PATTERNS = { hi: HI_PATTERNS };
  var NAMES = { en: "English", hi: "हिन्दी" };
  // user content: never translated
  var CAPTION_AREAS = "#caption-stage, #sr-captions, .lang-native, .lang-english, .q-text, .qa-original, #join-url, [data-no-i18n]";
  var ATTRS = ["placeholder", "aria-label", "title"];

  var lang = "en";
  var originals = new WeakMap();     // text node -> English text it had
  var attrOriginals = new WeakMap(); // element -> {attr: English}
  var busy = false;

  function norm(t) { return t.replace(/\s+/g, " ").trim(); }

  function translateText(english) {
    var key = norm(english);
    if (!key || lang === "en") return null;
    var d = DICTS[lang] || {};
    if (Object.prototype.hasOwnProperty.call(d, key)) return d[key];
    var ps = PATTERNS[lang] || [];
    for (var i = 0; i < ps.length; i++) if (ps[i][0].test(key)) return key.replace(ps[i][0], ps[i][1]);
    return null;
  }

  function excluded(node) {
    var el = node.nodeType === 1 ? node : node.parentElement;
    return !el || !!(el.closest && el.closest(CAPTION_AREAS)) || /^(SCRIPT|STYLE|TEXTAREA)$/.test(el.tagName);
  }

  function doText(node) {
    if (excluded(node)) return;
    var cur = node.data;
    var orig = originals.get(node);
    // page JS replaced the text since we last translated it -> new English original
    if (orig === undefined || (cur !== orig && cur !== node.__dwaniShown)) { orig = cur; originals.set(node, orig); }
    var t = translateText(orig);
    var want = t === null ? orig : orig.replace(norm(orig), t);
    if (cur !== want) { node.data = want; }
    node.__dwaniShown = want;
  }

  function doAttrs(el) {
    if (excluded(el)) return;
    var o = attrOriginals.get(el) || {};
    ATTRS.forEach(function (a) {
      if (!el.hasAttribute(a)) return;
      var cur = el.getAttribute(a);
      if (!(a in o) || (cur !== o[a] && cur !== (el.__dwaniAttr || {})[a])) o[a] = cur;
      var t = translateText(o[a]);
      var want = t === null ? o[a] : t;
      if (cur !== want) el.setAttribute(a, want);
      (el.__dwaniAttr = el.__dwaniAttr || {})[a] = want;
    });
    attrOriginals.set(el, o);
  }

  function walk(root) {
    if (root.nodeType === 3) { doText(root); return; }
    if (root.nodeType !== 1 || excluded(root)) return;
    doAttrs(root);
    var tw = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT, null);
    var n;
    while ((n = tw.nextNode())) { if (n.nodeType === 3) doText(n); else doAttrs(n); }
  }

  function apply() {
    busy = true;
    walk(document.body);
    document.documentElement.lang = lang;
    observer.takeRecords();
    busy = false;
  }

  var observer = new MutationObserver(function (records) {
    if (busy) return;
    busy = true;
    records.forEach(function (r) {
      if (r.type === "characterData") doText(r.target);
      else if (r.type === "attributes") doAttrs(r.target);
      else r.addedNodes.forEach(walk);
    });
    observer.takeRecords();
    busy = false;
  });

  function setLang(code) {
    var next = DICTS[code] ? code : "en";
    if (next === lang) return lang;
    lang = next;
    apply();
    try { document.dispatchEvent(new CustomEvent("dwani:lang", { detail: lang })); } catch (e) {}
    return lang;
  }

  function start() {
    observer.observe(document.body, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ATTRS });
    apply();
  }
  if (document.body) start(); else document.addEventListener("DOMContentLoaded", start);

  window.DwaniI18n = {
    setLang: setLang,
    lang: function () { return lang; },
    has: function (code) { return !!DICTS[code]; },
    names: NAMES,
    t: function (english) { var t = translateText(english); return t === null ? english : t; }
  };
})();
