/* DwaniForms: "My applications". A list kept ON THIS PHONE (localStorage), with the dates an RTI application runs on.

   What it is not: no government system is asked, nothing is sent anywhere, and nothing here knows whether an office has
   replied. The person types the dates ("sent on", "reply received on") and this works out what the RTI Act allows next:

     reply due       30 days after the application was sent                      (Section 7(1))
     first appeal    within 30 days after that, or after the reply                (Section 19(1))
     decision due    30 days after the first appeal was sent                      (Section 19(6); 45 at most)
     second appeal   within 90 days of the decision, or of the day it was due     (Section 19(3))

   The same arithmetic as outcomes.appeal_timing on the server (a test runs both on the same dates). Only the department
   and the dates are saved, never a name, an address or what was asked. Plain functions with the storage handed in, so
   tests/js/dwaniforms_tracker.test.js can run them without a browser. No look-behind in any pattern (older iPhones). */
(function (root) {
  "use strict";

  var KEY = "dwaniforms:apps";
  var MAX = 40;                                      // a phone is not a case-management system

  // ---- dates: ISO strings (YYYY-MM-DD), worked out in UTC so a daylight-saving change can never move a day ----
  function parse(iso) {
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ""));
    if (!m) return null;
    var t = Date.UTC(+m[1], +m[2] - 1, +m[3]), d = new Date(t);
    return d.getUTCFullYear() === +m[1] && d.getUTCMonth() === +m[2] - 1 && d.getUTCDate() === +m[3] ? t : null;
  }
  function fmt(t) {
    var d = new Date(t);
    return d.getUTCFullYear() + "-" + String(d.getUTCMonth() + 1).padStart(2, "0") + "-" + String(d.getUTCDate()).padStart(2, "0");
  }
  function addDays(iso, n) { var t = parse(iso); return t === null ? null : fmt(t + n * 86400000); }
  function diffDays(from, to) {                      // whole days from -> to (negative when `to` is earlier)
    var a = parse(from), b = parse(to);
    return a === null || b === null ? null : Math.round((b - a) / 86400000);
  }
  function today(now) {                              // the phone's own calendar day
    var d = now || new Date();
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
  }

  // ---- where a case stands ----
  // phase: closed | other | unsent | wait | first | reply | wait2 | second
  //   `date` is the day that matters in that phase, `left` the days from today to it (negative once it has passed),
  //   `late` is true when the time allowed has run out (an appeal is still possible, with a request to excuse the delay).
  function phase(a, now) {
    if (!a) return { phase: "other" };
    if (a.closed) return { phase: "closed" };
    if (a.kind !== "rti") {
      return a.check && parse(a.check) !== null ? { phase: "other", date: a.check, left: diffDays(now, a.check), late: false } : { phase: "other" };
    }
    if (!a.sent || parse(a.sent) === null) return { phase: "unsent" };
    var date, ph, late = false;
    if (a.appeal && parse(a.appeal) !== null) {
      if (a.decision && parse(a.decision) !== null) { ph = "second"; date = addDays(a.decision, 90); }
      else {
        var due = addDays(a.appeal, 30);
        if (diffDays(now, due) >= 0) return { phase: "wait2", date: due, left: diffDays(now, due), late: false };
        ph = "second"; date = addDays(due, 90);
      }
    } else if (a.reply && parse(a.reply) !== null) { ph = "reply"; date = addDays(a.reply, 30); }
    else {
      var reply = addDays(a.sent, 30);
      if (diffDays(now, reply) >= 0) return { phase: "wait", date: reply, left: diffDays(now, reply), late: false };
      ph = "first"; date = addDays(reply, 30);
    }
    var left = diffDays(now, date);
    return { phase: ph, date: date, left: left, late: left < 0 };
  }

  // ---- the list ----
  function newId() { return "a" + Date.now().toString(36) + Math.floor(Math.random() * 1e6).toString(36); }
  function clean(a) {
    var out = { id: String(a.id || newId()).slice(0, 24), kind: a.kind === "rti" ? "rti" : "other",
                title: String(a.title || "").slice(0, 80), dept: String(a.dept || "").slice(0, 100),
                ref: String(a.ref || "").slice(0, 40), created: parse(a.created) !== null ? a.created : null };
    ["sent", "reply", "appeal", "decision", "check"].forEach(function (k) { if (a[k] && parse(a[k]) !== null) out[k] = a[k]; });
    if (a.closed) out.closed = true;
    return out;
  }
  function store(storage) {
    function read() {
      try {
        var v = JSON.parse(storage.getItem(KEY) || "[]");
        return Array.isArray(v) ? v.filter(function (x) { return x && typeof x === "object"; }).map(clean) : [];
      } catch (e) { return []; }
    }
    function write(list) { try { storage.setItem(KEY, JSON.stringify(list.slice(0, MAX))); return true; } catch (e) { return false; } }
    return {
      list: read,
      add: function (a) { var list = read(); var x = clean(a); list.unshift(x); return write(list) ? x : null; },
      update: function (id, patch) {
        var list = read(), hit = null;
        list = list.map(function (x) {
          if (x.id !== id) return x;
          var merged = {}; Object.keys(x).forEach(function (k) { merged[k] = x[k]; });
          Object.keys(patch).forEach(function (k) { if (patch[k] === "" || patch[k] === null || patch[k] === false) delete merged[k]; else merged[k] = patch[k]; });
          hit = clean(merged); return hit;
        });
        return hit && write(list) ? hit : null;
      },
      remove: function (id) { return write(read().filter(function (x) { return x.id !== id; })); }
    };
  }

  // What to save from a finished flow. `fields` is the session's field list ({id, value}); nothing but the
  // department and the dates is taken from it.
  function fromResult(flow, templateTitle, fields, now) {
    var v = {}; (fields || []).forEach(function (f) { if (f && f.id) v[f.id] = f.value || ""; });
    var base = { created: now, title: String(templateTitle || "").slice(0, 80), dept: v.department || "" };
    if (flow === "rti") return clean(Object.assign(base, { kind: "rti" }));
    if (flow === "rti_appeal") {
      // an appeal about an earlier application: that application's date, and the reply's if there was one
      var a = { kind: "rti", sent: v.rti_date };
      if (/^reply/i.test(v.reply_status || "")) a.reply = v.reply_date;
      return clean(Object.assign(base, a));
    }
    return clean(Object.assign(base, { kind: "other" }));
  }

  // The most urgent open item, for the home page: the one whose date is nearest (an overdue one counts first).
  function urgent(list, now) {
    var best = null;
    list.forEach(function (a) {
      var p = phase(a, now);
      if (!p.date) return;
      if (!best || p.left < best.p.left) best = { a: a, p: p };
    });
    return best;
  }

  // A calendar file (iCalendar) with one all-day event on the day that matters and a reminder two days before.
  function icsEscape(s) { return String(s || "").replace(/\\/g, "\\\\").replace(/;/g, "\\;").replace(/,/g, "\\,").replace(/\r?\n/g, "\\n"); }
  function ics(a, p, description) {
    if (!p || !p.date) return null;
    var d = p.date.replace(/-/g, ""), end = addDays(p.date, 1).replace(/-/g, "");
    var stamp = (new Date().toISOString().replace(/[-:]/g, "").replace(/\.\d+Z$/, "Z"));
    return ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//DwaniForms//My applications//EN", "CALSCALE:GREGORIAN", "BEGIN:VEVENT",
            "UID:" + a.id + "-" + d + "@dwaniforms", "DTSTAMP:" + stamp, "DTSTART;VALUE=DATE:" + d, "DTEND;VALUE=DATE:" + end,
            "SUMMARY:" + icsEscape("DwaniForms: " + (a.title || "application") + (a.dept ? " - " + a.dept : "")),
            "DESCRIPTION:" + icsEscape(description || ""), "BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:" + icsEscape(a.title || "DwaniForms"),
            "TRIGGER:-P2D", "END:VALARM", "END:VEVENT", "END:VCALENDAR"].join("\r\n") + "\r\n";
  }

  var api = { KEY: KEY, MAX: MAX, parse: parse, addDays: addDays, diffDays: diffDays, today: today, phase: phase, store: store,
              fromResult: fromResult, urgent: urgent, ics: ics, clean: clean };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.DwaniTracker = api;
})(typeof self !== "undefined" ? self : this);
