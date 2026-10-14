// DwaniYojana on the real page against a REAL server: tap through the questions, see the schemes, switch language, nothing is saved.
// Run: DWANIFORMS_URL=http://127.0.0.1:8100 node dwaniforms_yojana.test.js
const fs = require("fs"), path = require("path"), { JSDOM } = require("jsdom");
const BASE = process.env.DWANIFORMS_URL || "http://127.0.0.1:8100";
const html = fs.readFileSync(path.join(__dirname, "..", "..", "dwaniforms", "static", "yojana.html"), "utf8");
const ok = (c, m) => { console.log((c ? "PASS " : "FAIL ") + m); if (!c) process.exitCode = 1; };
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, ms = 8000) { const end = Date.now() + ms; while (Date.now() < end) { if (fn()) return true; await wait(30); } return false; }

(async () => {
  const sent = [];
  const w = new JSDOM(html, { url: BASE + "/form/yojana", runScripts: "dangerously", pretendToBeVisual: true,
    beforeParse(w) {
      w.fetch = (u, opt) => { sent.push({ u: String(u), body: opt && opt.body }); return fetch(new URL(u, BASE + "/form/").href, opt); };
      w.scrollTo = () => {}; w.Element.prototype.scrollIntoView = () => {};
    } }).window;
  const d = w.document, $ = (id) => d.getElementById(id);
  const heading = () => $("qh").textContent;
  const tap = (label) => { const b = [].slice.call(d.querySelectorAll(".opt")).filter((x) => x.textContent === label)[0]; if (b) b.click(); return !!b; };
  const num = (v) => { $("num-in").value = String(v); $("num-in").dispatchEvent(new w.KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true })); };

  ok(!$("start").hidden && $("ask").hidden && /सरकारी योजना/.test($("start-h").textContent), "opens on the Hindi start screen");
  $("go").click();
  ok(await until(() => /उम्र/.test(heading())), "first question is the age");
  num(-4); ok(/–/.test(d.querySelector(".err").textContent) && /उम्र/.test(heading()), "an impossible age is refused with the allowed range");
  num(62); ok(await until(() => /कौन/.test(heading())), "a valid age moves on");
  ok(tap("महिला") && tap("गाँव") && tap("किसान"), "gender, area and work are single taps");
  ok(await until(() => /ज़मीन/.test(heading())), "a farmer is asked about land");
  num(2); await until(() => /कमाई/.test(heading()));
  ok(tap("₹1 – 2 लाख"), "income is a band, not a typed number");
  ok(await until(() => /आयकर/.test(heading())), "then the yes/no questions");
  tap("नहीं"); tap("नहीं"); await until(() => /पीएफ/.test(heading())); tap("नहीं");        // tax, government family, PF
  await until(() => /बैंक/.test(heading())); tap("हाँ");
  await until(() => /राशन/.test(heading())); tap("पात्र गृहस्थी / बीपीएल");
  await until(() => /घर/.test(heading())); tap("कच्चा घर");
  await until(() => /गैस/.test(heading())); tap("हाँ");
  await until(() => /विधवा/.test(heading())); ok(!!d.querySelector(".opt.skip"), "the widow question can be skipped"); tap("नहीं");
  await until(() => /दिव्यांग/.test(heading())); d.querySelector(".opt.skip").click();
  ok(await until(() => !$("out").hidden && d.querySelectorAll(".scheme").length > 0), "the answer screen appears with schemes");
  ok(/PM-KISAN|पीएम-किसान/.test($("result").textContent) && /वृद्धावस्था/.test($("result").textContent), "PM-KISAN and the old-age pension are among them");
  ok(/नियम जाँचे गए: \d{1,2} (जनवरी|फ़रवरी|मार्च|अप्रैल|मई|जून|जुलाई|अगस्त|सितंबर|अक्टूबर|नवंबर|दिसंबर) \d{4}/.test($("result").textContent) && /फ़ैसला नहीं/.test($("result").textContent), "it says when the rules were checked, and that it is guidance");

  const call = sent.filter((s) => /yojana\/advise/.test(s.u))[0], body = JSON.parse(call.body);
  ok(body.facts.age === 62 && body.facts.annual_income === 200000 && !("category" in body.facts), "one request carries the answers (income as the top of its band)");
  const wa = [].slice.call(d.querySelectorAll("a")).filter((a) => /wa\.me/.test(a.href))[0];
  ok(wa && !/Farmer|62|BPL|Kutcha/i.test(decodeURIComponent(wa.href)), "the WhatsApp message carries no personal answer");
  let copied = null; Object.defineProperty(w.navigator, "clipboard", { configurable: true, value: { writeText: (x) => { copied = x; return Promise.resolve(); } } });
  $("copy-list").click();
  ok(await until(() => copied !== null) && /PM-KISAN|पीएम-किसान/.test(copied) && /^मुझे \d+ सरकारी योजनाएँ/.test(copied) && /\/form\/yojana$/.test(copied), "'Copy the list' copies the scheme names and the link");
  ok(!/Farmer|62|BPL|Kutcha|किसान|1,00|200000/.test(copied.split("\n").filter((l) => !/•/.test(l)).join("\n")) && copied.split("\n").length >= 3, "the copied list has no personal answer");
  ok(/कॉपी हो गई/.test($("result").textContent), "it says the list was copied");
  ok(!w.localStorage.length && !w.sessionStorage.length && !/;/.test(d.cookie), "nothing is stored in the browser");

  d.querySelector('.lang button[data-l="en"]').click();
  ok(await until(() => /You may get/.test($("result").textContent) && /Rules checked on: \d{1,2} (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{4}/.test($("result").textContent)), "switching to English redraws the same answer in English, with the date in words");
  console.log(process.exitCode ? "FAILED" : "all passed");
})();
