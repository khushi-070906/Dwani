// Reading an Aadhaar card photo into a form, on the real page against a REAL server (only the engine and the picture are stood in).
// Run: DWANIFORMS_URL=http://127.0.0.1:8100 node dwaniforms_docs_page.test.js
const fs = require("fs"), path = require("path"), { JSDOM } = require("jsdom");
const BASE = process.env.DWANIFORMS_URL || "http://127.0.0.1:8100";
const root = path.join(__dirname, "..", "..", "dwaniforms", "static");
const html = fs.readFileSync(path.join(root, "app.html"), "utf8");
const realDocs = require(path.join(root, "ocr", "dwani-docs.js")), realOcr = require(path.join(root, "ocr", "dwani-ocr.js"));
const ok = (c, m) => { console.log((c ? "PASS " : "FAIL ") + m); if (!c) process.exitCode = 1; };
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, ms = 8000) { const end = Date.now() + ms; while (Date.now() < end) { if (fn()) return true; await wait(40); } return false; }
const $ = (w, id) => w.document.getElementById(id);
const q = (w, s) => w.document.querySelector(s);
const AAD = "234567890124", AADG = "2345 6789 0124";
const OCRTEXT = "Government of India\nAsha Verma\nDOB: 14/03/1990\nFemale\n" + AADG + "\nVID : 9123 4567 8901 2345";

(async () => {
  const posted = [];
  const w = new JSDOM(html, { url: BASE + "/form/", runScripts: "dangerously", pretendToBeVisual: true,
    beforeParse(w) {
      w.sessionStorage.setItem("dwaniforms:consent", "v1"); w.localStorage.setItem("dwaniforms:lang", "en");
      w.fetch = (u, opt) => { if (opt && opt.method === "POST") posted.push({ u: String(u), body: opt.body }); return fetch(new URL(u, BASE + "/form/").href, opt); };
      w.speechSynthesis = { cancel() {}, speak() {} }; w.SpeechSynthesisUtterance = function (t) { this.text = t; };
      w.createImageBitmap = async () => ({ width: 3000, height: 4000, close() {} });
      w.HTMLCanvasElement.prototype.getContext = () => ({ drawImage() {} });
      const add = w.HTMLHeadElement.prototype.appendChild;
      w.HTMLHeadElement.prototype.appendChild = function (el) {
        if (el.tagName === "SCRIPT" && /\/assets\/ocr\//.test(el.src)) {
          if (/tesseract/.test(el.src)) w.Tesseract = { createWorker: async () => ({ reinitialize: async () => {}, recognize: async () => ({ data: { text: OCRTEXT } }) }) };
          else if (/dwani-docs/.test(el.src)) w.DwaniDocs = realDocs; else w.DwaniOCR = realOcr;
          setTimeout(() => el.onload && el.onload(), 0); return el;
        }
        return add.call(this, el);
      };
    } }).window;
  await until(() => q(w, '[data-form="ayushman_card"]') || q(w, '[data-form]'));
  const start = (id) => { const b = q(w, '[data-form="' + id + '"]'); if (b) b.click(); return !!b; };
  const clicked = start("ayushman_card");
  ok(clicked, "the Ayushman card form is on the home page");
  await until(() => !$(w, "talk").hidden && $(w, "question").textContent);
  ok(!$(w, "photo").hidden && /Aadhaar card, ration card or khasra/.test($(w, "doc-note").textContent), "the photo button and its note are there");

  const file = new w.File([new Uint8Array(10)], "aadhaar.jpg", { type: "image/jpeg" });
  Object.defineProperty($(w, "photo-file"), "files", { value: [file], configurable: true });
  $(w, "photo-file").dispatchEvent(new w.Event("change"));
  await until(() => /Is this correct/.test($(w, "question").textContent));
  ok(/Asha Verma/.test($(w, "question").textContent), "the first question is the name read off the card, to be confirmed: " + $(w, "question").textContent);

  const sent = posted.filter((p) => /\/suggest$/.test(p.u));
  const vals = sent.length === 1 ? JSON.parse(sent[0].body).values : {};
  ok(Object.keys(vals).sort().join() === "aadhaar,dob,full_name,gender", "only the values read were sent: " + Object.keys(vals).sort().join());
  ok(vals.aadhaar === AAD && vals.dob === "1990-03-14" && vals.gender === "Female", "with the right values");
  ok(!posted.some((p) => p.body && typeof p.body !== "string"), "no picture was posted");

  const list = $(w, "photo-list").textContent;
  ok(/Asha Verma/.test(list) && /1990/.test(list), "the found values are listed: " + list);
  ok(list.includes(AAD.slice(-4)) && !list.includes(AAD.slice(0, 8)) && !list.includes(AADG.slice(0, 9)), "the Aadhaar number is listed masked, last four only");
  ok(!JSON.stringify(vals).includes("9123"), "the virtual ID was not taken");

  // yes to the name, then the questions that were not on the card are asked normally; the date comes back as a read-back
  const yes = async () => { const was = $(w, "question").textContent; $(w, "btn-yes").click(); await until(() => $(w, "question").textContent !== was); };
  await yes();
  ok(/father/i.test($(w, "question").textContent) && !/Is this correct/.test($(w, "question").textContent), "a question not on the card is asked as usual: " + $(w, "question").textContent);
  w.close();
})();
