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
  ok(!$(w, "explain").querySelector(".ai"), "no 'ask the AI' box when the server has no AI key");
  w.close();


  // --- the photo of a paper: a real page, a real server; only the engine and the camera picture are stood in
  {
    const OCRTEXT = "ELECTRICITY BILL\nAmount Payable: Rs. 2,890.00\nDue Date: " + isoIn(9);
    const posted = [], realOcr = require(path.join(__dirname, "..", "..", "dwaniforms", "static", "ocr", "dwani-ocr.js")),
      realDocs = require(path.join(__dirname, "..", "..", "dwaniforms", "static", "ocr", "dwani-docs.js"));
    w = new JSDOM(html, { url: BASE + "/form/", runScripts: "dangerously", pretendToBeVisual: true,
      beforeParse(w) {
        w.sessionStorage.setItem("dwaniforms:consent", "v1"); w.localStorage.setItem("dwaniforms:lang", "en");
        w.fetch = (u, opt) => { if (opt && opt.method === "POST") posted.push({ u: String(u), body: opt.body }); return fetch(new URL(u, BASE + "/form/").href, opt); };
        w.speechSynthesis = { cancel() {}, speak() {} }; w.SpeechSynthesisUtterance = function (t) { this.text = t; };
        w.createImageBitmap = async () => ({ width: 3000, height: 4000, close() {} });
        w.HTMLCanvasElement.prototype.getContext = () => ({ drawImage() {} });
        w.scriptsAsked = [];
        // the engine is loaded on first use, not before: a script tag for it is answered with a stand-in
        const add = w.HTMLHeadElement.prototype.appendChild;
        w.HTMLHeadElement.prototype.appendChild = function (el) {
          if (el.tagName === "SCRIPT" && /\/assets\/ocr\//.test(el.src)) {
            w.scriptsAsked.push(el.src.split("/").pop());
            if (/tesseract/.test(el.src)) w.Tesseract = { createWorker: async () => ({ reinitialize: async () => {}, recognize: async () => ({ data: { text: OCRTEXT } }) }) };
            else if (/dwani-docs/.test(el.src)) w.DwaniDocs = realDocs;
            else w.DwaniOCR = realOcr;
            setTimeout(() => el.onload && el.onload(), 0); return el;
          }
          return add.call(this, el);
        };
      } }).window;
    await until(() => q(w, '[data-form="pm_kisan"]'));
    q(w, '[data-form="pm_kisan"]').click();
    await until(() => !$(w, "talk").hidden && $(w, "question").textContent);
    ok(!$(w, "photo").hidden && !$(w, "doc-note").hidden, "a form offers 'Read from a photo' of an Aadhaar / ration / khasra paper");
    ok(w.scriptsAsked.length === 0, "the 7 MB engine is not fetched until someone uses it");
    $(w, "talk-back").click();
    await until(() => !$(w, "home").hidden);
    q(w, '#top-list [data-form="samjhao"]').click();
    await until(() => !$(w, "talk").hidden && $(w, "question").textContent);
    ok(!$(w, "photo").hidden && $(w, "doc-note").hidden, "Samjhao offers 'Read from a photo' without the Aadhaar note");
    ok(/phone only/.test($(w, "photo-note").textContent) && /not sent or stored/.test($(w, "photo-note").textContent), "and says the photo stays on the phone");
    const file = new w.File([new Uint8Array(10)], "bill.jpg", { type: "image/jpeg" });
    Object.defineProperty($(w, "photo-file"), "files", { value: [file], configurable: true });
    $(w, "photo-file").dispatchEvent(new w.Event("change"));
    await until(() => /Electricity bill/.test($(w, "question").textContent), 8000);
    ok(/I noted Kind of paper: Electricity bill\. Is this correct/.test($(w, "question").textContent), "the kind of paper is read back for a yes: " + $(w, "question").textContent);
    ok(!$(w, "yn").hidden, "with yes / no buttons");
    const list = $(w, "photo-list").textContent;
    ok(/Electricity bill/.test(list) && /2,890/.test(list), "what was found is listed on screen: " + list);
    ok(w.scriptsAsked.includes("tesseract.min.js") && w.scriptsAsked.includes("dwani-ocr.js"), "the engine was fetched from this server only");
    const sent = posted.filter((p) => /\/sessions\/[^/]+\/suggest$/.test(p.u));
    ok(sent.length === 1 && typeof sent[0].body === "string" && Object.keys(JSON.parse(sent[0].body).values).sort().join() === "amount,due_date,paper",
       "only three short values were sent");
    ok(!posted.some((p) => p.body && typeof p.body !== "string"), "no file or binary body was ever posted");
    const yes = async () => { const was = $(w, "question").textContent; $(w, "btn-yes").click(); await until(() => $(w, "question").textContent !== was || !$(w, "result").hidden); };
    await yes();                                             // paper
    ok(/Date on the paper/.test($(w, "question").textContent) && /Is this correct/.test($(w, "question").textContent), "then the date is read back: " + $(w, "question").textContent);
    await yes();                                             // date
    ok(/Amount/.test($(w, "question").textContent) && /2890/.test($(w, "question").textContent), "then the amount: " + $(w, "question").textContent);
    await yes();                                             // amount
    await until(() => !$(w, "result").hidden && !$(w, "explain").hidden, 8000);
    ok(/electricity/i.test($(w, "explain").textContent) && /9 days left/.test($(w, "explain").textContent), "the explanation follows, with the days counted");
    w.close();
  }


  // --- "ask a question about this paper": only when the server says it has an AI key, and only after a yes every time
  {
    const asked = [];
    w = new JSDOM(html, { url: BASE + "/form/", runScripts: "dangerously", pretendToBeVisual: true,
      beforeParse(w) {
        w.sessionStorage.setItem("dwaniforms:consent", "v1"); w.localStorage.setItem("dwaniforms:lang", "en");
        w.fetch = async (u, opt) => {
          const url = new URL(u, BASE + "/form/");
          if (/\/sessions\/[^/]+\/ask$/.test(url.pathname)) {
            asked.push(JSON.parse(opt.body));
            return { ok: true, status: 200, json: async () => ({ answer: "Pay at the office. I may be wrong." }) };
          }
          const r = await fetch(url.href, opt);
          if (/\/capabilities$/.test(url.pathname)) { const j = await r.json(); j.ai = true; return { ok: true, status: 200, json: async () => j }; }
          return r;
        };
        w.speechSynthesis = { cancel() {}, speak() {} }; w.SpeechSynthesisUtterance = function (t) { this.text = t; };
      } }).window;
    await until(() => q(w, '#top-list [data-form="samjhao"]'));
    q(w, '#top-list [data-form="samjhao"]').click();
    await until(() => !$(w, "talk").hidden && $(w, "question").textContent);
    await answer(w, "kyc");
    await until(() => !$(w, "result").hidden && !$(w, "explain").hidden, 8000);
    const box = q(w, "#explain .ai");
    ok(!!box, "the question box appears when the server has an AI key");
    const input = box.querySelector("input"), form = box.querySelector("form"), consent = box.querySelector(".ai-consent");
    ok(consent.hidden, "the consent is not shown until a question is typed");
    form.dispatchEvent(new w.Event("submit", { cancelable: true }));
    ok(consent.hidden && asked.length === 0, "an empty question does nothing");
    input.value = "Will they really cut my account?";
    form.dispatchEvent(new w.Event("submit", { cancelable: true }));
    ok(!consent.hidden && /sent to an AI service/.test(consent.textContent) && /not the photo, your name or any number/.test(consent.textContent), "asking shows what will leave: " + consent.textContent.slice(0, 60));
    ok(asked.length === 0, "nothing is sent yet");
    const [yes, no] = consent.querySelectorAll("button");
    no.click();
    ok(consent.hidden && asked.length === 0, "'no' sends nothing");
    form.dispatchEvent(new w.Event("submit", { cancelable: true }));
    yes.click();
    await until(() => box.querySelector(".ai-answer"));
    ok(asked.length === 1 && asked[0].consent === true && asked[0].question === "Will they really cut my account?", "after 'yes' exactly the question and the consent are sent");
    ok(/Pay at the office/.test(box.querySelector(".ai-answer").textContent) && /can be wrong/.test(box.querySelector(".ai-answer").textContent) && /1930/.test(box.querySelector(".ai-answer").textContent), "the answer is shown with its warning and the helplines");
    ok(w.document.querySelector("#explain script, #explain img") === null && box.querySelector(".ai-answer p").children.length === 0, "the answer is set as text, never as markup");
    input.value = "And another?"; form.dispatchEvent(new w.Event("submit", { cancelable: true }));
    ok(!consent.hidden && asked.length === 1, "the next question asks again; consent is never remembered");
    w.close();
  }

  // --- the same page in Tamil: the screen text comes from the pack
  w = boot("ta");
  await until(() => q(w, '[data-form="samjhao"]') && /காகித/.test(q(w, '#top-list [data-form="samjhao"]').textContent), 8000);
  ok(/காகித/.test(q(w, '#top-list [data-form="samjhao"]').textContent), "Tamil: the task is named in Tamil");
  w.close();
})();
