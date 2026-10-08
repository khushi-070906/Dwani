// DwaniForms app (dwaniforms/static/app.html) driven in jsdom against a REAL running server
// (python -m dwaniforms.standalone --text-only --demo-records). Run: DWANIFORMS_URL=http://127.0.0.1:8100 node dwaniforms_kiosk.test.js
const fs = require("fs"), path = require("path"), { JSDOM } = require("jsdom");
const BASE = process.env.DWANIFORMS_URL || "http://127.0.0.1:8100";
const html = fs.readFileSync(path.join(__dirname, "..", "..", "dwaniforms", "static", "app.html"), "utf8");
const ok = (c, m) => { console.log((c ? "PASS " : "FAIL ") + m); if (!c) process.exitCode = 1; };
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, ms = 4000) { const end = Date.now() + ms; while (Date.now() < end) { if (fn()) return true; await wait(40); } return false; }
function boot(lang) {
  const spoken = [];
  const w = new JSDOM(html, { url: BASE + "/form/", runScripts: "dangerously", pretendToBeVisual: true,
    beforeParse(w) {
      w.sessionStorage.setItem("dwaniforms:consent", "v1");     // consent itself is tested in dwaniforms_voice.test.js
      w.fetch = (u, opt) => fetch(new URL(u, BASE + "/form/").href, opt);
      w.speechSynthesis = { cancel() {}, speak(u) { spoken.push(u.text); } };
      w.SpeechSynthesisUtterance = function (t) { this.text = t; };
      if (lang) w.localStorage.setItem("dwaniforms:lang", lang);
      w.print = () => { w.__printed = w.document.body.classList.contains("print-letter"); };
      w.open = (u) => { w.__opened = u; };
      w.alert = (m) => { w.__alert = m; };
    } }).window;
  w.__spoken = spoken;
  return w;
}
const $ = (w, id) => w.document.getElementById(id);
const q = (w, s) => w.document.querySelector(s);
async function say(w, text) {
  const before = $(w, "question").textContent;
  $(w, "type-input").value = text;
  $(w, "type-form").dispatchEvent(new w.Event("submit", { cancelable: true }));
  await until(() => $(w, "question").textContent !== before || !$(w, "result").hidden);
}
async function open(w, id) {
  await until(() => q(w, `[data-form="${id}"]`));
  q(w, `[data-form="${id}"]`).click();
  await until(() => !$(w, "talk").hidden && $(w, "question").textContent);
}
(async () => {
  let w = boot("hi");
  await until(() => q(w, '[data-form="grievance"]'));
  ok(q(w, ".welcome h1").textContent === "नमस्ते" && /हर जवाब की जाँच/.test(q(w, ".chips").textContent), "home: welcome card in Hindi with the three promises");
  ok($(w, "top-list").querySelectorAll(".row").length === 4 && q(w, '#top-list [data-form="scheme_advisor"]'), "home: the four most-used services, incl. the scheme advisor");
  ok($(w, "all-groups").querySelectorAll(".group").length === 4, "home: everything else, grouped");
  ok(!$(w, "demo-note").hidden, "demo records -> the amber 'demo version' card is shown");
  // Which languages this server can really do is its own answer (Hindi, English + every lang/*.json pack): the page must
  // offer exactly those, no more. Asked of the server rather than hard-coded, so adding a pack does not break the test.
  const offered = (await (await fetch(BASE + "/form/capabilities")).json()).langs.slice().sort().join();
  ok([...$(w, "lang").options].filter((o) => !o.disabled).map((o) => o.value).sort().join() === offered,
     "no translator on this server: only languages with hand-written questions offered (" + offered + ")");
  ok($(w, "lang").querySelector('option[value="or"]').disabled, "Odia (no language pack yet) stays disabled online");

  // 1. complaint
  await open(w, "grievance");
  ok(/पूरा नाम/.test($(w, "question").textContent) && w.__spoken.length > 0, "first question shown and read aloud");
  ok(/^सवाल 1 \/ \d+$/.test($(w, "step-count").textContent) && q(w, '#steps .s.on [data-t="step1"]'), "question counter + step 1 'भरें'");
  ok(/शिकायत/.test($(w, "crumb-title").textContent), "breadcrumb home / form name");
  const n = w.__spoken.length; $(w, "repeat").click();
  ok(w.__spoken.length === n + 1, "repeat reads the question again");
  $(w, "big-btn").click();
  ok(w.document.body.classList.contains("big"), "A+ makes text bigger");
  await say(w, "सुनीता देवी");
  const row1 = () => q(w, '#sheet-rows li[data-id="full_name"]');
  ok(!$(w, "yn").hidden && row1().classList.contains("pencil") && /सुनीता देवी/.test(row1().textContent), "answer pencilled in, big Yes / No shown");
  $(w, "btn-yes").click(); await until(() => /मोबाइल/.test($(w, "question").textContent));
  ok(row1().classList.contains("filled") && !row1().classList.contains("pencil") && row1().querySelector(".note-ok, .note-review"), "after 'yes' it is inked in");
  ok(q(w, '#sheet-rows li[data-id="mobile"].current'), "the next field is highlighted");
  for (const a of ["98765 43210", "हाँ", "हमारे गाँव सोनपुर में 20 सितंबर से बिजली नहीं है, तहसील बिसवां जिला सीतापुर", "हाँ"]) await say(w, a);
  ok(/बिजली विभाग/.test($(w, "question").textContent), "department suggested from the complaint, in Hindi");
  const mob = q(w, '#sheet-rows li[data-id="mobile"] .boxes');
  ok(mob && mob.querySelectorAll(".b").length === 10 && mob.textContent === "9876543210", "mobile number in 10 boxes");
  for (const a of ["हाँ", "हाँ", "हाँ", "छोड़ो"]) await say(w, a);
  ok(/आपने जो बताया/.test($(w, "question").textContent) && $(w, "sheet").classList.contains("final") && q(w, '#steps .s.on [data-t="step2"]'),
     "whole draft read back: form outlined, step 2 'जाँचें'");
  await say(w, "हाँ");
  await until(() => !$(w, "result").hidden);
  ok($(w, "cert-kicker").textContent === "पत्र तैयार" && /DF-[0-9A-F]{8}/.test($(w, "cert-meta").textContent), "certificate: 'letter ready' + DF reference");
  ok(/सेवा में/.test($(w, "letter").textContent) && /प्रिंट करके हस्ताक्षर/.test($(w, "next-steps").textContent), "Hindi letter + Hindi next steps");
  [...$(w, "draft-tabs").querySelectorAll("button")].find((b) => b.dataset.l === "en").click();
  ok(/Subject: Complaint/.test($(w, "letter").textContent), "English tab");
  $(w, "print-letter").click();
  ok(w.__printed === true, "print prints only the letter");
  w.close();

  // 2. ration card (English)
  w = boot("en");
  await open(w, "ration_lookup");
  await say(w, "1234 5678 9012"); $(w, "btn-yes").click();
  await until(() => !$(w, "result").hidden);
  ok($(w, "cert-kicker").textContent === "Record found" && /D\*\*\* R\*\*\* L\*\*\*/.test($(w, "lookup").textContent) && /Demo records/.test($(w, "lookup").textContent),
     "ration: record found, name masked, demo warning");
  ok($(w, "pdf").hidden, "no PDF button for a look-up");
  w.close();

  // 3. advisor -> fill a recommended form with answers carried over
  w = boot("en");
  await open(w, "scheme_advisor");
  for (const a of ["35", "yes", "male", "village", "farmer", "two and a half", "yes", "1,20,000", "yes", "no", "no", "no", "yes",
                   "priority", "kutcha", "skip", "OBC"]) await say(w, a);
  await until(() => !$(w, "result").hidden);
  const likely = [...$(w, "advice").querySelectorAll(".scheme")].filter((s) => s.querySelector(".pill.ok")).map((s) => s.querySelector(".nm").textContent);
  ok(likely.includes("PM-KISAN") && likely.includes("e-Shram card"), "advisor: likely schemes: " + likely.join(", "));
  ok(/Rules reviewed 2026-06-30/.test($(w, "advice").textContent), "each scheme shows its source and review date");
  const fill = [...$(w, "advice").querySelectorAll(".scheme")].find((s) => s.querySelector(".nm").textContent === "PM-KISAN").querySelector(".btn.forest");
  fill.click();
  await until(() => !$(w, "talk").hidden && /full name/i.test($(w, "question").textContent));
  let gotPrefill = false;
  for (const a of ["Ram Lal", "yes", "Shyam Lal", "yes", "15 August 1985", "yes"]) {
    await say(w, a);
    if (/I noted Gender: Male/.test($(w, "question").textContent)) {
      gotPrefill = true;
      const dob = q(w, '#sheet-rows li[data-id="dob"] .boxes');
      ok(dob && [...dob.querySelectorAll(".b")].map((b) => b.textContent).join("") === "15081985", "date of birth in DD / MM / YYYY boxes");
      const g = q(w, '#sheet-rows li[data-id="gender"]');
      ok(g && g.classList.contains("pencil") && /Male/.test(g.querySelector(".ck.on").textContent), "carried-over gender pencilled in");
      break;
    }
  }
  ok(gotPrefill, "advisor answers carried over: 'I noted Gender: Male'");
  w.close();

  // 4. a language pack (Bengali) online: screen text, questions, answers and results all in Bengali, no translator
  if (!offered.split(",").includes("bn")) { console.log("SKIP Bengali: this server has no bn language pack"); return; }
  w = boot("bn");
  await until(() => q(w, '[data-form="ration_lookup"]') && q(w, ".welcome h1").textContent === "নমস্কার");
  ok(q(w, ".welcome h1").textContent === "নমস্কার" && /বলে বা লিখে/.test($(w, "hello-sub") ? $(w, "hello-sub").textContent : q(w, ".lead").textContent),
     "Bengali: home screen text comes from the language pack");
  ok(/রেশন কার্ড দেখা/.test(q(w, '[data-form="ration_lookup"]').textContent), "Bengali: service names from the pack");
  await open(w, "ration_lookup");
  ok(/রেশন কার্ড নম্বর কত/.test($(w, "question").textContent) && /নমস্কার/.test($(w, "question").textContent), "Bengali: hand-written question, not machine-translated");
  ok(!$(w, "draft-note").hidden && $(w, "mt-note").hidden, "Bengali: 'not yet checked by a native speaker' note, no machine-translation note");
  await say(w, "১২৩৪ ৫৬৭৮ ৯০১২");
  ok(/আপনি বললেন/.test($(w, "question").textContent), "Bengali: read-back in Bengali");
  $(w, "btn-yes").click();
  await until(() => !$(w, "result").hidden);
  const keys = [...$(w, "lookup").querySelectorAll("dt")].map((d) => d.textContent);
  ok(keys.includes("রেশন দোকান") && keys.includes("শ্রেণি"), "Bengali: record labels translated: " + keys.join(", "));
  ok(/অগ্রাধিকার|অন্ত্যোদয়/.test($(w, "res-say").textContent), "Bengali: result spoken with the category in Bengali: " + $(w, "res-say").textContent.slice(0, 80));
  w.close();
})();
