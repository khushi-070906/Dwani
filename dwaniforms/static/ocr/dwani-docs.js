/* DwaniForms: read an IDENTITY OR LAND PAPER from a photo, ON THE PHONE, and suggest answers for the form being filled:
   Aadhaar card (number, name, date of birth, gender, mobile, PIN code), ration card (card number), khasra copy (khasra no.).

   Same rules as the Samjhao photo reader (dwani-ocr.js): nothing here decides anything. Every value is handed to the
   conversation as if the person had said it, and the conversation reads it back with "is this correct?"; a "no" asks the
   question normally. The picture never leaves the phone, only these few values do. An Aadhaar number is accepted only if
   its check digit is right, so a misread digit is dropped instead of suggested.

   Plain text parsing, kept apart from the engine so it can be tested without a browser (tests/js/dwaniforms_docs.test.js).
   No look-behind in any pattern: older iPhones refuse a whole script that has one. */
(function (root) {
  "use strict";

  var DEV = "०१२३४५६७८९";
  function norm(t) {
    return String(t || "").replace(/[०-९]/g, function (d) { return String(DEV.indexOf(d)); }).replace(/\r/g, "");
  }

  // ---------------------------------------------------------------------------------------------------------------
  // Aadhaar: 12 digits whose last digit is the Verhoeff check digit (the same check the server makes).
  var VD = [[0,1,2,3,4,5,6,7,8,9],[1,2,3,4,0,6,7,8,9,5],[2,3,4,0,1,7,8,9,5,6],[3,4,0,1,2,8,9,5,6,7],[4,0,1,2,3,9,5,6,7,8],
            [5,9,8,7,6,0,4,3,2,1],[6,5,9,8,7,1,0,4,3,2],[7,6,5,9,8,2,1,0,4,3],[8,7,6,5,9,3,2,1,0,4],[9,8,7,6,5,4,3,2,1,0]];
  var VP = [[0,1,2,3,4,5,6,7,8,9],[1,5,7,6,2,8,3,0,9,4],[5,8,0,3,7,9,6,1,4,2],[8,9,1,6,0,4,3,5,2,7],[9,4,5,3,1,2,6,8,7,0],
            [4,2,8,6,5,7,3,9,0,1],[2,7,9,3,8,0,6,4,1,5],[7,0,4,6,9,1,3,2,5,8]];
  function verhoeff(num) {
    var c = 0, d = String(num).split("").reverse();
    for (var i = 0; i < d.length; i++) c = VD[c][VP[i % 8][+d[i]]];
    return c === 0;
  }

  function digitRuns(text) {
    // every run of digits that may be written with single spaces or dashes between groups of four ("2345 6789 0123")
    var out = [], re = /\d[\d \-]*\d|\d/g, m;
    while ((m = re.exec(text))) out.push({ raw: m[0], digits: m[0].replace(/\D/g, ""), at: m.index, end: m.index + m[0].length });
    return out;
  }

  function findAadhaar(raw) {
    var text = norm(raw), found = null;
    // 4-4-4 groups, not part of a longer number (a 16-digit virtual ID is not an Aadhaar number)
    var re = /(\d{4})[ \-]?(\d{4})[ \-]?(\d{4})/g, m;
    while ((m = re.exec(text))) {
      var before = text.charAt(m.index - 1), after = text.charAt(m.index + m[0].length);
      if (/\d/.test(before) || /\d/.test(after)) { re.lastIndex = m.index + 1; continue; }
      if (/\d{4}[ \t]*$/.test(text.slice(Math.max(0, m.index - 6), m.index)) || /^[ \t]*\d{4}(?!\d)/.test(text.slice(m.index + m[0].length))) {
        // a fourth group next to it: a VID or a card number, not an Aadhaar number
        continue;
      }
      var d = m[1] + m[2] + m[3];
      if (/^[01]/.test(d) || !verhoeff(d)) { re.lastIndex = m.index + 1; continue; }
      found = d; break;
    }
    return found;
  }

  // ---------------------------------------------------------------------------------------------------------------
  function ymd(d, mo, y) {
    d = +d; mo = +mo; y = +y;
    var dt = new Date(Date.UTC(y, mo - 1, d));
    if (dt.getUTCFullYear() !== y || dt.getUTCMonth() !== mo - 1 || dt.getUTCDate() !== d) return null;
    return y + "-" + String(mo).padStart(2, "0") + "-" + String(d).padStart(2, "0");
  }

  function findDob(raw, today) {
    var text = norm(raw).toLowerCase();
    var label = /(dob|d\.o\.b|date of birth|birth date|जन्म\s*तिथि|जन्म\s*तारीख|जन्म\s*दिनांक|जन्मतिथि)/g, m;
    var cap = today ? Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate()) : Date.now();
    while ((m = label.exec(text))) {
      var tail = text.slice(m.index + m[0].length, m.index + m[0].length + 30);
      var d = /(\d{1,2})\s*[\/.\-]\s*(\d{1,2})\s*[\/.\-]\s*(\d{4})(?!\d)/.exec(tail);
      if (!d) continue;
      var iso = ymd(d[1], d[2], d[3]);
      if (iso && +d[3] >= 1900 && Date.parse(iso) <= cap) return iso;
    }
    return null;
  }

  function findGender(raw) {
    var t = norm(raw).toLowerCase();
    if (/(^|[^a-z])(female|woman)([^a-z]|$)|महिला|स्त्री/.test(t)) return "Female";
    if (/(^|[^a-z])male([^a-z]|$)|पुरुष|पुरूष/.test(t)) return "Male";
    return null;
  }

  function findMobile(raw) {
    var text = norm(raw);
    var lab = /(mobile|mob\.?|phone|मोबाइल|मोबाईल|फोन)\D{0,12}([6-9]\d{9})(?!\d)/i.exec(text.replace(/(\d)[ \-](?=\d)/g, "$1"));
    return lab ? lab[2] : null;
  }

  function findPin(raw) {
    var text = norm(raw);
    var lab = /(pin\s*code|pincode|pin|पिन\s*कोड|पिन)\D{0,6}([1-9]\d{5})(?!\d)/i.exec(text);
    if (lab) return lab[2];
    return null;
  }

  var NOT_NAME = /(government|govt|india|aadhaar|aadhar|unique|identification|authority|uidai|male|female|dob|birth|year|vid|address|enrolment|enrollment|download|issued|help|www|\.gov|\.in\b|card|number|signature|photo|mobile)/i;
  function findName(raw) {
    var lines = norm(raw).split("\n").map(function (l) { return l.trim(); });
    var at = -1;
    for (var i = 0; i < lines.length; i++) if (/(dob|date of birth|year of birth|जन्म)/i.test(lines[i])) { at = i; break; }
    if (at < 0) return null;
    for (var j = at - 1; j >= Math.max(0, at - 4); j--) {
      var l = lines[j].replace(/[^A-Za-z .]/g, " ").replace(/\s+/g, " ").trim();
      var words = l.split(" ").filter(function (w) { return w.replace(/\./g, "").length > 0; });
      if (!l || NOT_NAME.test(l) || words.length < 2 || words.length > 5) continue;
      if (!words.every(function (w) { return /^[A-Za-z][A-Za-z.]{1,}$/.test(w); })) continue;
      return words.map(function (w) { return w.charAt(0).toUpperCase() + w.slice(1).toLowerCase(); }).join(" ");
    }
    return null;
  }

  function findFather(raw) {
    var m = /(?:^|\n|\s)(?:s\/o|d\/o|son of|daughter of|पिता|पुत्र|पुत्री)[:\s,\-]+([A-Za-z][A-Za-z .]{2,40}?)(?:[,\n]|$)/i.exec(norm(raw));
    if (!m) return null;
    var words = m[1].trim().split(/\s+/).filter(Boolean);
    if (words.length < 1 || words.length > 4) return null;
    return words.map(function (w) { return w.charAt(0).toUpperCase() + w.slice(1).toLowerCase(); }).join(" ");
  }

  // ---------------------------------------------------------------------------------------------------------------
  function findRation(raw) {
    var text = norm(raw);
    var re = /(?:ration\s*card|card\s*(?:no|number|num)|राशन\s*कार्ड|कार्ड\s*(?:संख्या|नंबर|नं|क्रमांक))\s*[.:\-]?\s*(?:no\.?|number|संख्या|नंबर|नं\.?)?\s*[.:\-]?\s*([A-Za-z0-9][A-Za-z0-9\/\-]{6,22})/gi, m;
    while ((m = re.exec(text))) {
      var c = m[1].replace(/[\/\-]/g, "");
      var digits = (c.match(/\d/g) || []).length;
      if (c.length >= 8 && c.length <= 20 && digits >= 6) return c.toUpperCase();
    }
    return null;
  }

  function findKhasra(raw) {
    var m = /(?:khasra|khasara|खसरा)\s*(?:no\.?|number|नं\.?|नंबर|संख्या|क्रमांक)?\s*[.:\-]?\s*(\d{1,5}(?:\s*\/\s*\d{1,3}[कखगघA-D]?)?)/i.exec(norm(raw));
    return m ? m[1].replace(/\s+/g, "") : null;
  }

  // ---------------------------------------------------------------------------------------------------------------
  function read(text, today) {
    var t = norm(text), values = {}, kind = null;
    var aad = findAadhaar(t);
    var looksAadhaar = /aadhaar|aadhar|आधार|uidai|unique identification/i.test(t);
    var ration = /ration|राशन|nfsa|खाद्य/i.test(t) ? findRation(t) : null;
    var khasra = /khasra|खसरा/i.test(t) ? findKhasra(t) : null;
    if (aad && (looksAadhaar || !ration)) {
      kind = "aadhaar"; values.aadhaar = aad;
      var dob = findDob(t, today), g = findGender(t), mo = findMobile(t), pin = findPin(t), nm = findName(t), fa = findFather(t);
      if (dob) values.dob = dob;
      if (g) values.gender = g;
      if (mo) values.mobile = mo;
      if (pin) values.pincode = pin;
      if (nm) values.full_name = nm;
      if (fa) values.father_name = fa;
    } else if (ration) {
      kind = "ration"; values.ration_card = ration; values.card_no = ration;
    } else if (khasra) {
      kind = "khasra"; values.khasra = khasra;
    }
    return { kind: kind, values: values };
  }

  var api = { read: read, findAadhaar: findAadhaar, verhoeff: verhoeff, findDob: findDob, findGender: findGender,
              findMobile: findMobile, findPin: findPin, findName: findName, findFather: findFather,
              findRation: findRation, findKhasra: findKhasra };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.DwaniDocs = api;
})(typeof self !== "undefined" ? self : this);
