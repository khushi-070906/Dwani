/* DwaniForms: read a photographed paper ON THE PHONE and suggest three answers for Samjhao -- which kind of paper it
   is, the last date (or hearing date) printed on it, and the amount it asks for.

   Nothing here decides anything. Each suggestion is handed to the conversation as if the person had said it, and the
   conversation asks "is this correct?" before keeping it; a "no" asks the question again. The photo itself never
   leaves the phone: the engine (tesseract.js, Hindi + English) runs in a web worker loaded from this server.

   The functions below are plain text parsing, kept apart from the engine so they can be tested without a browser
   (tests/js/dwaniforms_ocr.test.js). */
(function (root) {
  "use strict";

  // ---------------------------------------------------------------------------------------------------------------
  // which kind of paper: keywords with weights. 3 = only this paper says it, 1 = a hint.
  var PAPERS = [
    ["Electricity bill", [["kwh", 3], ["units consumed", 3], ["electricity", 3], ["bijli", 3], ["बिजली", 3], ["विद्युत", 3],
                          ["discom", 3], ["sanctioned load", 3], ["power", 1], ["meter reading", 2], ["energy charges", 3]]],
    ["Water bill", [["water", 3], ["jal board", 3], ["जल बोर्ड", 3], ["जल कर", 3], ["पानी", 3], ["sewerage", 2], ["kilolitre", 3],
                    ["water charges", 3]]],
    ["Property tax notice", [["property tax", 4], ["house tax", 4], ["गृह कर", 4], ["गृहकर", 4], ["संपत्ति कर", 4], ["भवन कर", 4],
                             ["सम्पत्ति कर", 4], ["annual rental value", 3], ["assessment", 1], ["municipal", 1], ["नगर निगम", 1]]],
    ["Bank loan or EMI notice", [["emi", 3], ["loan account", 3], ["overdue", 2], ["sarfaesi", 4], ["npa", 2], ["demand notice", 2],
                                 ["installment", 2], ["instalment", 2], ["ऋण", 3], ["किस्त", 3], ["किश्त", 3], ["loan", 2]]],
    ["Bank KYC or account block message", [["kyc", 4], ["केवाईसी", 4], ["blocked", 2], ["suspended", 2], ["will be blocked", 3],
                                           ["update your pan", 3], ["click", 1], ["link", 1]]],
    ["Court summons or legal notice", [["summons", 4], ["court", 3], ["magistrate", 3], ["न्यायालय", 4], ["अदालत", 4], ["समन", 4],
                                       ["case no", 2], ["legal notice", 4], ["advocate", 2], ["वकील", 2], ["appear", 1]]],
    ["Income tax notice", [["income tax", 4], ["incometax.gov.in", 4], ["assessment year", 3], ["आयकर", 4], ["section 143", 3],
                           ["section 148", 3], ["document identification number", 3], ["din", 1]]],
    ["Traffic challan", [["challan", 4], ["चालान", 4], ["traffic", 3], ["parivahan", 3], ["motor vehicles act", 3],
                         ["mv act", 3], ["violation", 2], ["vehicle no", 2], ["helmet", 2]]]
  ];

  var DEV = "०१२३४५६७८९";
  function norm(t) {
    t = String(t || "").replace(/[०-९]/g, function (d) { return String(DEV.indexOf(d)); });
    return t.toLowerCase().replace(/[ \t]+/g, " ");
  }
  function hits(text, word) {
    // whole words for short latin keywords ("emi" must not hit "premises"); substrings for Devanagari and phrases
    if (/^[a-z]{2,4}$/.test(word)) return (text.match(new RegExp("(^|[^a-z])" + word + "([^a-z]|$)", "g")) || []).length;
    return text.split(word).length - 1;
  }

  function findPaper(raw) {
    var text = norm(raw), best = null, second = 0;
    PAPERS.forEach(function (p) {
      var score = 0;
      p[1].forEach(function (kw) { if (hits(text, kw[0])) score += kw[1]; });
      if (!best || score > best.score) { if (best) second = Math.max(second, best.score); best = { value: p[0], score: score }; }
      else second = Math.max(second, score);
    });
    // a guess needs real evidence and a clear lead; otherwise the person says it themselves
    return best && best.score >= 3 && best.score > second ? best.value : null;
  }

  // ---------------------------------------------------------------------------------------------------------------
  // dates
  var MONTHS = { jan: 1, january: 1, feb: 2, february: 2, mar: 3, march: 3, apr: 4, april: 4, may: 5, jun: 6, june: 6,
                 jul: 7, july: 7, aug: 8, august: 8, sep: 9, sept: 9, september: 9, oct: 10, october: 10, nov: 11,
                 november: 11, dec: 12, december: 12,
                 "जनवरी": 1, "फरवरी": 2, "फ़रवरी": 2, "मार्च": 3, "अप्रैल": 4, "अप्रेल": 4, "मई": 5, "जून": 6, "जुलाई": 7,
                 "अगस्त": 8, "सितंबर": 9, "सितम्बर": 9, "अक्टूबर": 10, "अक्तूबर": 10, "नवंबर": 11, "नवम्बर": 11,
                 "दिसंबर": 12, "दिसम्बर": 12 };
  var MONTH_RE = Object.keys(MONTHS).sort(function (a, b) { return b.length - a.length; }).join("|");
  var DATE_RES = [
    { re: /(\d{1,2})\s*[\/.\-]\s*(\d{1,2})\s*[\/.\-]\s*(\d{4}|\d{2})(?!\d)/g, f: function (m) { return [m[1], m[2], m[3]]; } },
    { re: new RegExp("(\\d{1,2})(?:st|nd|rd|th)?[\\s\\-.,]*(" + MONTH_RE + ")\\.?[\\s\\-.,]*(\\d{4}|\\d{2})(?!\\d)", "g"),
      f: function (m) { return [m[1], MONTHS[m[2]], m[3]]; } },
    { re: new RegExp("(" + MONTH_RE + ")\\.?\\s+(\\d{1,2})(?:st|nd|rd|th)?,?\\s+(\\d{4})", "g"),
      f: function (m) { return [m[2], MONTHS[m[1]], m[3]]; } },
    { re: /(\d{4})-(\d{2})-(\d{2})(?!\d)/g, f: function (m) { return [m[3], m[2], m[1]]; } }
  ];
  function iso(d, m, y) {
    d = +d; m = +m; y = +y;
    if (y < 100) y += 2000;
    var dt = new Date(Date.UTC(y, m - 1, d));
    if (dt.getUTCFullYear() !== y || dt.getUTCMonth() !== m - 1 || dt.getUTCDate() !== d) return null;
    return y + "-" + String(m).padStart(2, "0") + "-" + String(d).padStart(2, "0");
  }
  function allDates(text) {
    var out = [];
    DATE_RES.forEach(function (p) {
      var m; p.re.lastIndex = 0;
      while ((m = p.re.exec(text))) {
        var a = p.f(m), v = iso(a[0], a[1], a[2]);
        if (v) out.push({ iso: v, at: m.index });
      }
    });
    return out.sort(function (a, b) { return a.at - b.at; });
  }
  var DUE_WORDS = ["due date", "last date", "pay by", "payable by", "payable on or before", "on or before", "due on",
                   "date of hearing", "hearing date", "next date", "date of appearance", "appear on", "reply by",
                   "within", "अंतिम तिथि", "अन्तिम तिथि", "देय तिथि", "भुगतान की अंतिम", "अंतिम दिनांक", "पेशी", "तारीख पेशी",
                   "सुनवाई", "नियत तिथि", "हाजिर"];
  function findDueDate(raw, today) {
    var text = norm(raw), dates = allDates(text);
    if (!dates.length) return null;
    var best = null;
    DUE_WORDS.forEach(function (w) {
      var i = -1;
      while ((i = text.indexOf(w, i + 1)) > -1) {
        dates.forEach(function (d) {
          var gap = d.at - (i + w.length);               // the date comes after its label, close by
          if (gap >= 0 && gap <= 60 && (!best || gap < best.gap)) best = { iso: d.iso, gap: gap };
        });
      }
    });
    // "... by 20/10/2026", "... before 20/10/2026", "20/10/2026 तक": the date is the deadline even without a label
    if (!best) dates.forEach(function (d) {
      var before = text.slice(Math.max(0, d.at - 10), d.at), after = text.slice(d.at, d.at + 20);
      if (!best && (/(^|\s)(by|before|till|until)\s*[:\-]?\s*$/.test(before) || /^\S+\s*(तक|से पहले)/.test(after))) best = { iso: d.iso, gap: 0 };
    });
    if (best) return best.iso;
    // no label: only a single date that is not long past is a safe guess (a bill date is usually also printed)
    var t = today || new Date(), floor = new Date(t.getTime() - 120 * 864e5).toISOString().slice(0, 10);
    var recent = dates.map(function (d) { return d.iso; }).filter(function (v, i, a) { return v >= floor && a.indexOf(v) === i; });
    return recent.length === 1 ? recent[0] : null;
  }

  // ---------------------------------------------------------------------------------------------------------------
  // amount
  var AMOUNT_WORDS = [["amount payable", 5], ["net payable", 5], ["total payable", 5], ["payable amount", 5], ["amount due", 5],
                      ["total due", 4], ["net amount", 4], ["fine amount", 5], ["penalty amount", 4], ["total amount", 3],
                      ["bill amount", 4], ["outstanding", 3], ["overdue amount", 4], ["tax payable", 5], ["demand payable", 5], ["देय राशि", 5], ["कुल देय", 5], ["बकाया राशि", 5],
                      ["भुगतान योग्य", 5], ["कुल राशि", 3], ["जुर्माना", 4], ["राशि", 1], ["rs", 1], ["₹", 1], ["inr", 1]];
  var NUM = /(?:rs\.?|₹|inr)?\s*(\d{1,3}(?:,\d{2,3})+(?:\.\d{1,2})?|\d{1,8}(?:\.\d{1,2})?)(?![\d\/\-])/g;
  function findAmount(raw) {
    var text = norm(raw), best = null;
    AMOUNT_WORDS.forEach(function (aw) {
      var i = -1;
      while ((i = text.indexOf(aw[0], i + 1)) > -1) {
        var after = text.slice(i + aw[0].length, i + aw[0].length + 40).split("\n")[0];
        NUM.lastIndex = 0;
        var m = NUM.exec(after);
        if (!m) continue;
        var v = Math.round(parseFloat(m[1].replace(/,/g, "")));
        if (!(v >= 1 && v <= 100000000)) continue;
        // a year or a 6-digit PIN right after a label is not an amount
        if (/^(19|20)\d{2}$/.test(String(v)) && !/[,.]/.test(m[1]) && aw[1] < 5) continue;
        var score = aw[1] * 100 - m.index;
        if (!best || score > best.score) best = { value: String(v), score: score };
      }
    });
    return best ? best.value : null;
  }

  function read(text, today) {
    return { paper: findPaper(text), due_date: findDueDate(text, today), amount: findAmount(text) };
  }

  var api = { findPaper: findPaper, findDueDate: findDueDate, findAmount: findAmount, read: read, allDates: allDates };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.DwaniOCR = api;
})(typeof self !== "undefined" ? self : this);
