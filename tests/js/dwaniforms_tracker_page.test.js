// "My applications" on the real page against a REAL server: save a finished RTI, count its days, start the first appeal.
// Run: DWANIFORMS_URL=http://127.0.0.1:8100 node dwaniforms_tracker_page.test.js
const fs = require("fs"), path = require("path"), { JSDOM } = require("jsdom");
const BASE = process.env.DWANIFORMS_URL || "http://127.0.0.1:8100";
const root = path.join(__dirname, "..", "..", "dwaniforms", "static");
const html = fs.readFileSync(path.join(root, "app.html"), "utf8");
const realTracker = require(path.join(root, "js", "tracker.js"));
const ok = (c, m) => { console.log((c ? "PASS " : "FAIL ") + m); if (!c) process.exitCode = 1; };
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, ms = 8000) { const end = Date.now() + ms; while (Date.now() < end) { if (fn()) return true; await wait(40); } return false; }
const $ = (w, id) => w.document.getElementById(id);
const qa = (w, s) => [].slice.call(w.document.querySelectorAll(s));
const post = async (u, d) => (await fetch(BASE + "/form" + u, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(d) })).json();

// a finished RTI, made through the same API the page uses
async function finishedRti() {
  const ans = { full_name: "Asha Verma", address: "H 24 Uttam Nagar Delhi", pincode: "110059", mobile: "9876543210", department: "Delhi Jal Board",
                information: "Status of my water connection file", period: "skip", delivery: "post", bpl: "no" };
  let r = await post("/sessions", { form_id: "rti_application", lang: "en" }); const sid = r.session_id;
  for (let n = 0; n < 40 && !r.done; n++) r = await post("/sessions/" + sid + "/text", { text: r.phase === "confirm" || r.phase === "final" ? "yes" : (ans[r.field_id] || "yes") });
  return { sid, r };
}
const iso = (d) => d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
const daysAgo = (n) => { const d = new Date(); d.setDate(d.getDate() - n); return d; };

(async () => {
  const { sid, r: done } = await finishedRti();
  ok(done.done && done.outcome && done.outcome.type === "draft", "an RTI was finished through the API");
  const dept = (done.fields.filter((f) => f.id === "department")[0] || {}).value;

  const posted = [], files = [];
  const w = new JSDOM(html, { url: BASE + "/form/", runScripts: "dangerously", pretendToBeVisual: true,
    beforeParse(w) {
      w.sessionStorage.setItem("dwaniforms:consent", "v1"); w.sessionStorage.setItem("dwaniforms:sid", sid); w.localStorage.setItem("dwaniforms:lang", "en");
      w.fetch = (u, opt) => { if (opt && opt.method === "POST") posted.push({ u: String(u), body: opt.body }); return fetch(new URL(u, BASE + "/form/").href, opt); };
      w.scrollTo = () => {}; w.speechSynthesis = { cancel() {}, speak() {} }; w.SpeechSynthesisUtterance = function (t) { this.text = t; };
      w.Blob = class { constructor(parts, o) { this.text = parts.join(""); this.type = o && o.type; } };
      w.URL.createObjectURL = (b) => { files.push(b); return "blob:x"; }; w.URL.revokeObjectURL = () => {};
      w.HTMLAnchorElement.prototype.click = function () { files.push({ download: this.download }); };
      const add = w.HTMLHeadElement.prototype.appendChild;
      w.HTMLHeadElement.prototype.appendChild = function (el) {
        if (el.tagName === "SCRIPT" && /\/assets\/js\/tracker\.js/.test(el.src)) { w.DwaniTracker = realTracker; setTimeout(() => el.onload && el.onload(), 0); return el; }
        return add.call(this, el);
      };
    } }).window;

  // ---- the finished RTI is on screen; save it
  ok(await until(() => !$(w, "result").hidden && !$(w, "trk-save").hidden), "the finished RTI shows a 'Save to my applications' button");
  $(w, "trk-save").click();
  const stored = () => JSON.parse(w.localStorage.getItem("dwaniforms:apps") || "[]");
  ok(await until(() => stored().length === 1), "saving puts one entry on this phone");
  ok(/Saved on this phone/.test($(w, "trk-msg").textContent) && $(w, "trk-save").disabled, "the page says so, and the button cannot be pressed twice");
  const raw = w.localStorage.getItem("dwaniforms:apps");
  ok(stored()[0].kind === "rti" && stored()[0].dept === dept && !stored()[0].sent, "it is an RTI for " + dept + ", not yet sent");
  ok(!/Asha|Uttam|9876|110059|water/.test(raw), "no name, address, phone, PIN or question was kept");
  ok(!posted.some((p) => /apps|trk/.test(String(p.body))), "nothing about the list was sent to the server");

  // ---- home: the card appears
  $(w, "home-link").click();
  ok(await until(() => !$(w, "trk-card").hidden), "the home page now shows the My applications card");
  ok(/Not sent yet/.test($(w, "trk-sum").textContent), "it says the RTI has not been sent: " + $(w, "trk-sum").textContent);

  // ---- the tracker: enter the date it was sent, 40 days ago
  $(w, "trk-open").click();
  ok(!$(w, "tracker").hidden && qa(w, "#trk-list .card").length === 1, "the list opens with one case");
  const dateInputs = () => qa(w, '#trk-list input[type="date"]');
  ok(dateInputs().length === 1, "only 'Sent on' is asked before it is sent");
  const setDate = (i, v) => { const el = dateInputs()[i]; el.value = v; el.dispatchEvent(new w.Event("change")); };
  const sent = iso(daysAgo(40)), due = iso(daysAgo(10));
  setDate(0, sent);
  ok(await until(() => /No reply within 30 days/.test($(w, "trk-list").textContent)), "40 days on: 'no reply within 30 days': " + $(w, "trk-list").textContent.slice(0, 160));
  ok(/20 days left/.test($(w, "trk-list").textContent), "the first appeal can wait 20 more days");
  ok(dateInputs().length === 3, "now 'Reply received on' and 'First appeal sent on' are asked too");
  const labels = qa(w, "#trk-list label").map((l) => l.textContent);
  ok(labels.some((l) => /Reference number/.test(l)), "and a reference number");
  ok(/days left/.test($(w, "trk-sum").textContent) || !$(w, "tracker").hidden, "(home summary follows)");

  // ---- calendar file
  const cal = qa(w, "#trk-list button").filter((b) => /calendar/.test(b.textContent))[0];
  ok(!!cal, "there is a 'Add a reminder to my calendar' button");
  cal.click();
  const ics = files.filter((f) => f.text)[0];
  const last = realTracker.addDays(sent, 60).replace(/-/g, "");
  ok(ics && ics.type === "text/calendar" && ics.text.indexOf("DTSTART;VALUE=DATE:" + last) > -1, "the calendar file is for the last day to appeal, " + last);
  ok(files.some((f) => f.download === "dwaniforms-reminder.ics"), "and it is offered as a download");

  // ---- the reference number sticks
  const refEl = qa(w, '#trk-list input[type="text"]')[0]; refEl.value = "DJB/2026/55"; refEl.dispatchEvent(new w.Event("change"));
  ok(await until(() => stored()[0].ref === "DJB/2026/55"), "a reference number is saved");

  // ---- other languages
  $(w, "lang").value = "hi"; $(w, "lang").dispatchEvent(new w.Event("change"));
  ok(await until(() => /मेरे आवेदन/.test($(w, "tracker").textContent) || /मेरे आवेदन/.test($(w, "trk-card").textContent)), "in Hindi the screen is in Hindi");
  ok(/30 दिन में जवाब नहीं आया/.test($(w, "trk-list").textContent), "and so is the sentence about the 30 days");
  $(w, "lang").value = "en"; $(w, "lang").dispatchEvent(new w.Event("change"));
  await until(() => /My applications/.test($(w, "tracker").textContent));

  // ---- write the first appeal: the office and the date go in as suggestions
  const appealBtn = () => qa(w, "#trk-list button").filter((b) => /Write the first appeal/.test(b.textContent))[0];
  ok(await until(() => !!appealBtn()), "the first appeal can be started from here");
  appealBtn().click();
  ok(await until(() => !$(w, "talk").hidden && $(w, "question").textContent), "the appeal form opens");
  ok(await until(() => posted.some((p) => /\/suggest$/.test(p.u))), "the office and the date are put to it as suggestions");
  const sug = posted.filter((p) => /\/suggest$/.test(p.u)).map((p) => JSON.parse(p.body).values)[0];
  ok(Object.keys(sug).sort().join() === "department,rti_date" && sug.rti_date === sent && sug.department === dept, "only those two: " + JSON.stringify(sug));
  ok(!posted.some((p) => /Asha|9876|Uttam/.test(String(p.body))), "no name, phone or address from the saved case went anywhere");

  // ---- done, open again, remove
  $(w, "home-link").click(); $(w, "trk-open").click();
  const byText = (re) => qa(w, "#trk-list button").filter((b) => re.test(b.textContent))[0];
  byText(/Mark as done/).click();
  ok(await until(() => /Marked as done/.test($(w, "trk-list").textContent)), "marked done: the countdown is gone");
  ok(!byText(/Write the first appeal/) && !byText(/calendar/), "and so are the appeal and calendar buttons");
  byText(/Open again/).click();
  ok(await until(() => /No reply within 30 days/.test($(w, "trk-list").textContent)), "opened again: the countdown is back");
  byText(/^Remove$/).click();
  ok(stored().length === 1 && !!byText(/Tap again to remove/), "one tap does not remove it");
  byText(/Tap again to remove/).click();
  ok(await until(() => stored().length === 0) && /Nothing saved yet/.test($(w, "trk-list").textContent), "a second tap does");
  $(w, "home-link").click();
  ok(await until(() => $(w, "trk-card").hidden), "and the home card goes away with it");
  w.close();
})().catch((e) => { console.log("FAIL " + (e && e.stack || e)); process.exitCode = 1; });
