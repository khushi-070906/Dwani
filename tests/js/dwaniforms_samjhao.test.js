// Samjhao on the real page (jsdom against a REAL running server): pick a paper, give its date, read what it means.
// Run: DWANIFORMS_URL=http://127.0.0.1:8100 node dwaniforms_samjhao.test.js
const fs = require("fs"), path = require("path"), { JSDOM } = require("jsdom");
const BASE = process.env.DWANIFORMS_URL || "http://127.0.0.1:8100";
const html = fs.readFileSync(path.join(__dirname, "..", "..", "dwaniforms", "static", "app.html"), "utf8");
const ok = (c, m) => { console.log((c ? "PASS " : "FAIL ") + m); if (!c) process.exitCode = 1; };
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, ms = 6000) { const end = Date.now() + ms; while (Date.now() < end) { if (fn()) return true; await wait(40); } return false; }
function boot(lang) {
  const spoken = [];
  const w = new JSDOM(html, { url: BASE + "/form/", runScripts: "dangerously", pretendToBeVisual: true,
    beforeParse(w) {
      w.sessionStorage.setItem("dwaniforms:consent", "v1");
      w.fetch = (u, opt) => fetch(new URL(u, BASE + "/form/").href, opt);
      w.speechSynthesis = { cancel() {}, speak(u) { spoken.push(u.text); } };
      w.SpeechSynthesisUtterance = function (t) { this.text = t; };
      if (lang) w.localStorage.setItem("dwaniforms:lang", lang);
    } }).window;
  w.spoken = spoken;
  return w;
}
const $ = (w, id) => w.document.getElementById(id);
const q = (w, s) => w.document.querySelector(s);
async function answer(w, text) {
  const before = $(w, "question").textContent;
  $(w, "type-input").value = text;
  $(w, "type-form").dispatchEvent(new w.Event("submit", { cancelable: true }));
  await until(() => $(w, "question").textContent !== before || !$(w, "result").hidden || !$(w, "yn").hidden);
  for (let i = 0; i < 3 && !$(w, "yn").hidden && $(w, "result").hidden; i++) {
    const b = q(w, "#yn button"); const was = $(w, "question").textContent; b.click();
    await until(() => $(w, "question").textContent !== was || !$(w, "result").hidden);
  }
}
function isoIn(days) { const d = new Date(); d.setDate(d.getDate() + days); return String(d.getDate()).padStart(2, "0") + "/" + String(d.getMonth() + 1).padStart(2, "0") + "/" + d.getFullYear(); }

(async () => {
  // --- the home page offers it, near the top, in the person's language
  let w = boot("hi");
  await until(() => q(w, '#top-list [data-form="samjhao"]'));
  ok(!!q(w, '#top-list [data-form="samjhao"]'), "Samjhao is one of the most-used tasks on the home page");
  ok(/कागज़ समझिए/.test(q(w, '#top-list [data-form="samjhao"]').textContent), "and is named in Hindi");
  ok([...w.document.querySelectorAll("#all-groups .group h3")].some((h) => h.textContent === "कागज़ समझिए"), "it has its own group");
  ok(!!q(w, '#all-groups [data-form="rti_first_appeal"]'), "the RTI first appeal is listed with complaints and RTI");

  // --- a bill with a last date 12 days away
  q(w, '#top-list [data-form="samjhao"]').click();
  await until(() => !$(w, "talk").hidden && $(w, "question").textContent);
  await answer(w, "बिजली का बिल");
  await answer(w, isoIn(12));
  await answer(w, "1850");
  await until(() => !$(w, "result").hidden && !$(w, "explain").hidden, 8000);
  ok(!$(w, "explain").hidden, "the explanation is shown");
  ok($(w, "cert-kicker").textContent === "कागज़ का मतलब", "the result says what it is: " + $(w, "cert-kicker").textContent);
  const box = $(w, "explain");
  const heads = [...box.querySelectorAll("h3")].map((h) => h.textContent);
  ok(heads.includes("यह क्या है") && heads.includes("क्या करें") && heads.includes("धोखे से बचिए"), "sections: " + heads.join(" / "));
  ok(/12 दिन बाकी/.test(box.textContent), "the days left are counted on screen");
  ok(/1,850/.test(box.textContent), "the amount is shown in Indian digit grouping");
  ok(!!box.querySelector(".alert.warn") && /धोखा/.test(box.querySelector(".alert.warn").textContent), "the fraud warning stands out");
  ok($(w, "pdf").hidden && $(w, "json").hidden && !$(w, "print-letter").hidden, "print, but no form PDF / data file for an explanation");
  // (Hindi speech spells numbers out -- "बारह दिन बाद" -- so look for the words around them)
  ok(/दिन बाद/.test(w.spoken.join(" ")) && /धोखा/.test(w.spoken.join(" ")), "the date and the fraud warning are read out");
  ok(!/<|&lt;/.test(box.innerHTML.replace(/<\/?(div|h3|p|ul|li|dl|dt|dd|span)[^>]*>/g, "")), "data is set as text, never as markup");
  w.close();

  // --- a KYC message: no date, no amount; the warning is the answer
  w = boot("en");
  await until(() => q(w, '[data-form="samjhao"]'));
  q(w, '#top-list [data-form="samjhao"]').click();
  await until(() => !$(w, "talk").hidden && $(w, "question").textContent);
  await answer(w, "kyc");
  await until(() => !$(w, "result").hidden, 8000);
  const t = $(w, "explain").textContent;
  ok(/OTP/.test(t) && /1930/.test(t), "KYC: never share the OTP, call 1930");
  ok(!$(w, "explain").querySelector("dl"), "KYC: no date or amount row when none was asked");
  w.close();

  // --- the same page in Tamil: the screen text comes from the pack
  w = boot("ta");
  await until(() => q(w, '[data-form="samjhao"]') && /காகித/.test(q(w, '#top-list [data-form="samjhao"]').textContent), 8000);
  ok(/காகித/.test(q(w, '#top-list [data-form="samjhao"]').textContent), "Tamil: the task is named in Tamil");
  w.close();
})();
