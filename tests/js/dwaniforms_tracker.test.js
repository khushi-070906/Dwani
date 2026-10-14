// "My applications": the date arithmetic and the saved list, without a browser.
// Run: node dwaniforms_tracker.test.js
const path = require("path");
const T = require(path.join(__dirname, "..", "..", "dwaniforms", "static", "js", "tracker.js"));
const ok = (c, m) => { console.log((c ? "PASS " : "FAIL ") + m); if (!c) process.exitCode = 1; };
const fake = () => { const m = {}; return { getItem: (k) => (k in m ? m[k] : null), setItem: (k, v) => { m[k] = String(v); }, raw: m }; };

// ---- dates
ok(T.addDays("2026-01-31", 30) === "2026-03-02", "30 days after 31 January is 2 March");
ok(T.addDays("2028-02-28", 1) === "2028-02-29" && T.addDays("2027-02-28", 1) === "2027-03-01", "leap years");
ok(T.diffDays("2026-10-14", "2026-11-13") === 30 && T.diffDays("2026-11-13", "2026-10-14") === -30, "days between two dates, both ways");
ok(T.diffDays("2026-10-25", "2026-11-01") === 7, "a daylight-saving change elsewhere cannot move a day");
ok(T.parse("2026-02-30") === null && T.parse("14/03/1990") === null && T.parse("") === null && T.parse(null) === null, "impossible or odd dates are refused");
ok(T.today(new Date(2026, 9, 14, 23, 59)) === "2026-10-14", "today is the phone's own calendar day");

// ---- an RTI, stage by stage (sent 1 Oct 2026)
const rti = (o) => Object.assign({ id: "a1", kind: "rti", title: "RTI", dept: "Delhi Jal Board" }, o);
let p = T.phase(rti({}), "2026-10-14");
ok(p.phase === "unsent" && !p.date, "no date sent yet: nothing to count");
p = T.phase(rti({ sent: "2026-10-01" }), "2026-10-14");
ok(p.phase === "wait" && p.date === "2026-10-31" && p.left === 17 && !p.late, "waiting: the office has until 31 October, 17 days left");
p = T.phase(rti({ sent: "2026-10-01" }), "2026-10-31");
ok(p.phase === "wait" && p.left === 0, "on the 30th day the office still has time");
p = T.phase(rti({ sent: "2026-10-01" }), "2026-11-01");
ok(p.phase === "first" && p.date === "2026-11-30" && p.left === 29 && !p.late, "day 31: no reply, first appeal due by 30 November");
p = T.phase(rti({ sent: "2026-10-01" }), "2026-11-30");
ok(p.phase === "first" && p.left === 0 && !p.late, "the last day to appeal is still in time");
p = T.phase(rti({ sent: "2026-10-01" }), "2026-12-05");
ok(p.phase === "first" && p.late && p.left === -5, "after that: late, five days ago");
p = T.phase(rti({ sent: "2026-10-01", reply: "2026-10-20" }), "2026-11-01");
ok(p.phase === "reply" && p.date === "2026-11-19" && p.left === 18, "a reply starts a new 30 days");
p = T.phase(rti({ sent: "2026-10-01", reply: "2026-10-20" }), "2026-11-25");
ok(p.phase === "reply" && p.late, "late after the reply's 30 days");
p = T.phase(rti({ sent: "2026-10-01", appeal: "2026-11-05" }), "2026-11-20");
ok(p.phase === "wait2" && p.date === "2026-12-05" && p.left === 15, "first appeal sent: decision due in 30 days");
p = T.phase(rti({ sent: "2026-10-01", appeal: "2026-11-05" }), "2026-12-10");
ok(p.phase === "second" && p.date === "2027-03-05" && !p.late, "no decision: second appeal within 90 days of the day it was due");
p = T.phase(rti({ sent: "2026-10-01", appeal: "2026-11-05", decision: "2026-11-25" }), "2026-12-10");
ok(p.phase === "second" && p.date === "2027-02-23", "a decision starts 90 days from the decision");
p = T.phase(rti({ sent: "2026-10-01", appeal: "2026-11-05", decision: "2026-11-25" }), "2027-03-01");
ok(p.phase === "second" && p.late, "second appeal late");
ok(T.phase(rti({ sent: "2026-10-01", closed: true }), "2026-12-05").phase === "closed", "marked done: no more countdown");
ok(T.phase(rti({ sent: "2026-13-45" }), "2026-12-05").phase === "unsent", "a broken date counts as no date");

// ---- anything that is not an RTI has no legal clock, only a date the person chose
const other = { id: "o", kind: "other", title: "Pension form" };
ok(T.phase(other, "2026-10-14").phase === "other" && !T.phase(other, "2026-10-14").date, "a form has no deadline unless one is set");
p = T.phase(Object.assign({}, other, { check: "2026-10-20" }), "2026-10-14");
ok(p.date === "2026-10-20" && p.left === 6, "a date the person chose is counted down");

// ---- what a finished flow saves: the office and the dates, nothing else
const rtiFields = [{ id: "full_name", value: "Asha Verma" }, { id: "address", value: "H-24 Uttam Nagar" }, { id: "mobile", value: "9876543210" },
                   { id: "department", value: "Delhi Jal Board" }, { id: "information", value: "my water connection file" }];
let x = T.fromResult("rti", "RTI application", rtiFields, "2026-10-14");
ok(x.kind === "rti" && x.dept === "Delhi Jal Board" && !x.sent && x.created === "2026-10-14", "an RTI is saved by office, not yet sent");
ok(!/Asha|Uttam|9876|water/.test(JSON.stringify(x)), "no name, address, number or question is kept");
x = T.fromResult("rti_appeal", "RTI first appeal", [{ id: "department", value: "Delhi Jal Board" }, { id: "rti_date", value: "2026-10-01" },
  { id: "reply_status", value: "Reply received but not satisfactory" }, { id: "reply_date", value: "2026-10-20" }, { id: "grounds", value: "incomplete" }], "2026-11-01");
ok(x.sent === "2026-10-01" && x.reply === "2026-10-20" && !/incomplete/.test(JSON.stringify(x)), "an appeal brings the RTI's dates along, not the grounds");
x = T.fromResult("rti_appeal", "RTI first appeal", [{ id: "department", value: "X" }, { id: "rti_date", value: "2026-10-01" }, { id: "reply_status", value: "No reply" }, { id: "reply_date", value: "2026-10-20" }], "2026-11-01");
ok(x.sent === "2026-10-01" && !x.reply, "no reply: a stray reply date is not taken");
x = T.fromResult("form", "Ayushman card", [{ id: "aadhaar", value: "234567890124" }], "2026-10-14");
ok(x.kind === "other" && !JSON.stringify(x).includes("2345"), "a form keeps only its title");

// ---- the list in storage
const st = T.store(fake());
ok(st.list().length === 0, "empty at first");
const a = st.add(rti({ id: "a1", sent: "2026-10-01" })), b = st.add({ kind: "other", title: "Pension" });
ok(st.list().length === 2 && st.list()[0].id === b.id, "newest first");
ok(st.update("a1", { reply: "2026-10-20", ref: "DJB/2026/55" }).reply === "2026-10-20" && st.list()[1].ref === "DJB/2026/55", "update sets a date and a reference");
ok(!("reply" in st.update("a1", { reply: "" })), "an empty value clears it");
ok(st.update("a1", { reply: "31/10/2026" }).reply === undefined, "a date in the wrong shape is dropped");
ok(st.update("a1", { closed: true }).closed === true && !("closed" in st.update("a1", { closed: false })), "done and open again");
ok(st.update("nope", { ref: "x" }) === null, "an unknown entry is not invented");
ok(st.remove("a1") && st.list().length === 1, "remove");
const broken = fake(); broken.setItem(T.KEY, "{not json");
ok(T.store(broken).list().length === 0, "a damaged list reads as empty instead of throwing");
const blocked = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } };
ok(T.store(blocked).list().length === 0 && T.store(blocked).add({ title: "x" }) === null, "blocked storage is survived");
const many = T.store(fake()); for (let i = 0; i < 60; i++) many.add({ kind: "other", title: "t" + i });
ok(many.list().length === T.MAX, "the list is capped at " + T.MAX);
ok(T.clean({ title: "x".repeat(500), dept: "y".repeat(500), ref: "z".repeat(500), sent: "<script>" }).title.length === 80, "long text is cut, odd dates dropped");

// ---- most urgent
const u = T.urgent([rti({ id: "late", sent: "2026-08-01" }), rti({ id: "soon", sent: "2026-10-01" }), { id: "o", kind: "other", title: "t" }], "2026-10-14");
ok(u && u.a.id === "late" && u.p.late, "an overdue case comes first");
ok(T.urgent([{ id: "o", kind: "other", title: "t" }], "2026-10-14") === null, "nothing dated, nothing urgent");

// ---- calendar file
const ev = T.ics(rti({ sent: "2026-10-01", title: "RTI; urgent, now" }), T.phase(rti({ sent: "2026-10-01" }), "2026-10-14"), "Waiting\nuntil 31 October");
ok(/BEGIN:VCALENDAR/.test(ev) && /DTSTART;VALUE=DATE:20261031/.test(ev) && /DTEND;VALUE=DATE:20261101/.test(ev) && /TRIGGER:-P2D/.test(ev), "an all-day event on 31 October with a reminder two days before");
const BS = String.fromCharCode(92), lines = ev.split("\r\n");
ok(lines.indexOf("SUMMARY:DwaniForms: RTI" + BS + "; urgent" + BS + ", now - Delhi Jal Board") > -1 && lines.indexOf("DESCRIPTION:Waiting" + BS + "nuntil 31 October") > -1 &&
   lines.every((l) => l.length < 200), "text is escaped the way iCalendar wants (; , and new lines)");
ok(T.ics(rti({}), T.phase(rti({}), "2026-10-14"), "") === null, "no date, no calendar file");
