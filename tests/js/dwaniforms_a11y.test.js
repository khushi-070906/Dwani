// What a screen reader is told while the form is being filled, checked in jsdom against a REAL running server.
// Run: DWANIFORMS_URL=http://127.0.0.1:8100 node dwaniforms_a11y.test.js
const fs = require("fs"), path = require("path"), { JSDOM } = require("jsdom");
const BASE = process.env.DWANIFORMS_URL || "http://127.0.0.1:8100";
const html = fs.readFileSync(path.join(__dirname, "..", "..", "dwaniforms", "static", "app.html"), "utf8");
const ok = (c, m) => { console.log((c ? "PASS " : "FAIL ") + m); if (!c) process.exitCode = 1; };
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, ms = 5000) { const end = Date.now() + ms; while (Date.now() < end) { if (fn()) return true; await wait(40); } return false; }
function boot(lang) {
  return new JSDOM(html, { url: BASE + "/form/", runScripts: "dangerously", pretendToBeVisual: true,
    beforeParse(w) {
      w.sessionStorage.setItem("dwaniforms:consent", "v1");
      w.fetch = (u, opt) => fetch(new URL(u, BASE + "/form/").href, opt);
      w.speechSynthesis = { cancel() {}, speak() {} };
      w.SpeechSynthesisUtterance = function (t) { this.text = t; };
      if (lang) w.localStorage.setItem("dwaniforms:lang", lang);
    } }).window;
}
const $ = (w, id) => w.document.getElementById(id);
const q = (w, s) => w.document.querySelector(s);
const label = (w, id) => $(w, id).getAttribute("aria-label");

(async () => {
  // --- the controls name themselves in the citizen's language, not in English
  let w = boot("hi");
  await until(() => q(w, '[data-form="grievance"]'));
  ok(/होम/.test(label(w, "home-link")) || /ध्वनि/.test(label(w, "home-link")), "Hindi: the logo button has a Hindi name: " + label(w, "home-link"));
  ok(/बड़े/.test(label(w, "big-btn")), "Hindi: A+ says what it does: " + label(w, "big-btn"));
  ok(/भाषा/.test(label(w, "lang")), "Hindi: the language chooser is named in Hindi");
  w.close();

  w = boot("bn");
  await until(() => q(w, '[data-form="grievance"]') && /ধ্বনি|হোম/.test(label(w, "home-link") || ""), 6000);
  ok(/ধ্বনি|হোম/.test(label(w, "home-link")), "Bengali: the same buttons are named in Bengali: " + label(w, "home-link"));
  ok(/বড়/.test(label(w, "big-btn")), "Bengali: A+ too: " + label(w, "big-btn"));

  // --- the mic is a toggle and says which way it is turned
  await until(() => q(w, '[data-form="grievance"]'));
  q(w, '[data-form="grievance"]').click();
  await until(() => !$(w, "talk").hidden && $(w, "question").textContent);
  const mic = $(w, "mic");
  ok(mic.getAttribute("aria-pressed") === "false", "the mic starts as an untoggled button, not a mystery icon");
  ok(/মাইক|লিখে|বলুন/.test(label(w, "mic") || ""), "the mic's name is in Bengali: " + label(w, "mic"));

  // --- the question itself is announced when it changes
  ok($(w, "question").getAttribute("aria-live") === "polite", "the question is a live region, so a new one is read out");
  ok($(w, "net-banner").getAttribute("role") === "alert", "a lost connection interrupts, rather than waiting to be noticed");
  ok($(w, "status").getAttribute("role") === "status", "'understanding…' is announced politely");

  // --- which of the three steps we are on
  await until(() => q(w, "#steps .s.on"));
  ok(q(w, '#steps .s[aria-current="step"]') === q(w, "#steps .s.on"),
     "the step we are on is marked aria-current, not only painted dark");

  // --- heading order on the page a reader navigates by
  w.close();
  w = boot("hi");
  await until(() => q(w, "#all-groups .group h3"), 6000);
  const levels = [...w.document.querySelectorAll("#home h1, #home h2, #home h3, #home h4")]
    .filter((h) => h.offsetParent !== null || true).map((h) => +h.tagName[1]);
  ok(levels.length > 3 && levels.every((n, i) => i === 0 || n <= levels[i - 1] + 1),
     "home: headings step down one level at a time (" + levels.join(",") + ")");
  ok(!q(w, "#all-groups h4"), "the service groups are h3 under the h2, not h4");
  w.close();
})();
