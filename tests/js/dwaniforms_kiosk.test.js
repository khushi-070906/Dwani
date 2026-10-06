// DwaniForms kiosk UI (dwaniforms/static/app.html) driven in jsdom against a REAL running server
// (python -m dwaniforms.standalone --text-only --demo-records). Run: DWANIFORMS_URL=http://127.0.0.1:8132 node kiosk.test.js
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");
const BASE = process.env.DWANIFORMS_URL || "http://127.0.0.1:8132";
const html = fs.readFileSync(path.join(__dirname, "..", "..", "dwaniforms", "static", "app.html"), "utf8");
const ok = (c, m) => { console.log((c ? "PASS " : "FAIL ") + m); if (!c) process.exitCode = 1; };
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, ms = 4000) { const end = Date.now() + ms; while (Date.now() < end) { if (fn()) return true; await wait(40); } return false; }

function boot(lang) {
  const spoken = [];
  const dom = new JSDOM(html, { url: BASE + "/form/", runScripts: "dangerously", pretendToBeVisual: true,
    beforeParse(w) {
      w.fetch = (u, opt) => fetch(new URL(u, BASE + "/form/").href, opt);       // the real API
      w.speechSynthesis = { cancel() {}, speak(u) { spoken.push(u.text); } };
      w.SpeechSynthesisUtterance = function (t) { this.text = t; };
      if (lang) w.localStorage.setItem("dwaniforms:lang", lang);
      w.print = () => { w.__printed = w.document.body.classList.contains("print-letter"); };
      w.open = (u) => { w.__opened = u; };
    } });
  dom.window.__spoken = spoken;
  return dom.window;
}
const $ = (w, id) => w.document.getElementById(id);
async function say(w, text) {
  const before = $(w, "question").textContent;
  $(w, "type-input").value = text;
  $(w, "type-form").dispatchEvent(new w.Event("submit", { cancelable: true }));
  await until(() => $(w, "question").textContent !== before || !$(w, "result").hidden);
}

(async () => {
  // home in Hindi
  let w = boot("hi");
  ok(w.document.querySelector('.tile[data-go="voice"]').disabled, "tiles disabled until the forms have loaded");
  await until(() => !w.document.querySelector('.tile[data-go="voice"]').disabled);
  ok(w.document.querySelector("h1").textContent === "आज आप क्या करना चाहते हैं?", "home in Hindi");
  ok(!$(w, "tile-records").disabled, "records tile enabled when records are loaded");
  ok($(w, "mic").disabled, "no speech model on this server: mic disabled, typing still works");

  // 1. complaint by typing (voice needs a model)
  w.document.querySelector('.tile[data-go="voice"]').click();
  await until(() => $(w, "pick-list").children.length > 0);
  const titles = [...$(w, "pick-list").querySelectorAll("b")].map((b) => b.textContent);
  ok(titles.includes("शिकायत") && titles.includes("आरटीआई आवेदन"), "complaint/RTI picker in Hindi: " + titles);
  $(w, "pick-list").children[0].click();
  await until(() => !$(w, "talk").hidden && $(w, "question").textContent);
  ok(/पूरा नाम/.test($(w, "question").textContent) && w.__spoken.length > 0, "first question shown and read aloud");
  ok(/^सवाल 1 \/ \d+$/.test($(w, "step-count").textContent), "question counter: " + $(w, "step-count").textContent);
  const n = w.__spoken.length; $(w, "repeat").click();
  ok(w.__spoken.length === n + 1 && w.__spoken[n] === $(w, "question").textContent, "🔁 repeats the question aloud");
  $(w, "big-btn").click();
  ok(w.document.body.classList.contains("big") && w.localStorage.getItem("dwaniforms:big") === "1", "A+ makes text bigger and is remembered");
  for (const a of ["सुनीता देवी"]) await say(w, a);
  ok(!$(w, "yn").hidden, "confirm step shows big Yes / No buttons");
  const row1 = () => $(w, "sheet-rows").querySelector('li[data-id="full_name"]');
  ok(row1().classList.contains("current") && row1().classList.contains("pencil") && /सुनीता देवी/.test(row1().textContent),
     "the answer is pencilled into the form until confirmed");
  ok(/आवेदन पत्र/.test($(w, "sheet").textContent) && /शिकायत/.test($(w, "talk-title").textContent), "form sheet header in Hindi");
  $(w, "btn-yes").click(); await until(() => /मोबाइल/.test($(w, "question").textContent));
  ok(row1().classList.contains("filled") && !row1().classList.contains("pencil") && row1().querySelector(".ok-tick, .review-flag"),
     "after 'yes' it is inked in, with a tick (or a check-it flag)");
  ok(w.document.querySelectorAll('#sheet-rows li[data-id="mobile"].current').length === 1, "the next field is highlighted");
  for (const a of ["98765 43210", "हाँ", "हमारे गाँव सोनपुर में 20 सितंबर से बिजली नहीं है, तहसील बिसवां जिला सीतापुर", "हाँ"]) await say(w, a);
  ok(/बिजली विभाग/.test($(w, "question").textContent), "department suggested from the complaint, in Hindi");
  for (const a of ["हाँ", "हाँ", "हाँ", "छोड़ो"]) await say(w, a);
  ok(/आपने जो बताया/.test($(w, "question").textContent), "whole draft read back before finishing");
  ok($(w, "sheet").classList.contains("final"), "the whole form is outlined during the read-back");
  ok($(w, "sheet-rows").querySelectorAll("li.filled").length >= 5 && $(w, "bar").style.width !== "0%", "form rows filled and progress bar");
  const mob = $(w, "sheet-rows").querySelector('li[data-id="mobile"] .boxes');
  ok(mob && mob.querySelectorAll(".b").length === 10 && mob.textContent === "9876543210", "mobile number written in 10 boxes");
  await say(w, "हाँ");
  await until(() => !$(w, "result").hidden);
  ok(!$(w, "draft").hidden && /सेवा में/.test($(w, "letter").textContent), "letter shown, Hindi tab first");
  const tabs = [...$(w, "draft-tabs").querySelectorAll("button")];
  tabs.find((b) => b.dataset.l === "en").click();
  ok(/Subject: Complaint/.test($(w, "letter").textContent), "English tab");
  $(w, "print-letter").click();
  ok(w.__printed === true, "Print prints only the letter");
  w.close();

  // 2. ration card lookup in English
  w = boot("en");
  await until(() => !w.document.querySelector('.tile[data-go="voice"]').disabled);
  w.document.querySelector('.tile[data-go="records"]').click();
  await until(() => $(w, "pick-list").children.length > 0);
  [...$(w, "pick-list").children].find((b) => /Ration/.test(b.textContent)).click();
  await until(() => !$(w, "talk").hidden && $(w, "question").textContent);
  await say(w, "1234 5678 9012");
  $(w, "btn-yes").click();
  await until(() => !$(w, "result").hidden);
  ok(!$(w, "lookup").hidden && /D\*\*\* R\*\*\* L\*\*\*/.test($(w, "lookup").textContent) && /Demo records/.test($(w, "lookup").textContent),
     "ration result: masked name and DEMO warning");
  w.close();

  // 3. advisor -> fill a recommended form with prefill
  w = boot("en");
  await until(() => !w.document.querySelector('.tile[data-go="voice"]').disabled);
  w.document.querySelector('.tile[data-go="advisor"]').click();
  await until(() => !$(w, "talk").hidden && $(w, "question").textContent);
  for (const a of ["35", "yes", "male", "village", "farmer", "two and a half", "yes", "1,20,000", "yes", "no", "no", "no", "yes",
                   "priority", "kutcha", "skip", "OBC"]) await say(w, a);
  await until(() => !$(w, "result").hidden);
  const names = [...$(w, "advice").querySelectorAll(".card.likely h3")].map((h) => h.textContent);
  ok(names.includes("PM-KISAN") && names.includes("e-Shram card"), "advisor: likely schemes listed: " + names.join(", "));
  ok(/Rules last reviewed: 2026-06-30/.test($(w, "advice").textContent), "every scheme shows its source and review date");
  const fill = [...$(w, "advice").querySelectorAll("button")].find((b) => /PM-Kisan application/.test(b.textContent));
  ok(!!fill, "'Fill this form' button for PM-Kisan");
  fill.click();
  await until(() => !$(w, "talk").hidden && /I noted/.test($(w, "question").textContent) || /What is your full name/.test($(w, "question").textContent));
  ok(!$(w, "talk").hidden, "PM-Kisan form starts");
  let gotPrefill = false;
  for (const a of ["Ram Lal", "yes", "Shyam Lal", "yes", "15 August 1985", "yes"]) {
    await say(w, a);
    if (/I noted Gender: Male/.test($(w, "question").textContent)) {
      gotPrefill = true;
      const dob = $(w, "sheet-rows").querySelector('li[data-id="dob"] .boxes');
      ok(dob && [...dob.querySelectorAll(".b")].map((b) => b.textContent).join("") === "15081985", "date of birth in DD / MM / YYYY boxes");
      const g = $(w, "sheet-rows").querySelector('li[data-id="gender"]');
      ok(g && g.classList.contains("pencil") && g.querySelector(".ck.on") && /Male/.test(g.querySelector(".ck.on").textContent), "carried-over gender pencilled as a ticked checkbox");
      break;
    }
  }
  ok(gotPrefill, "advisor answers carried over: 'I noted Gender: Male. Is this correct?'");
  w.close();
})();
