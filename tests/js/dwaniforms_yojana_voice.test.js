// DwaniYojana by voice: the browser's speech recognizer is faked; the server (a REAL one) says what the words mean.
// Run: DWANIFORMS_URL=http://127.0.0.1:8100 node dwaniforms_yojana_voice.test.js
const fs = require("fs"), path = require("path"), { JSDOM } = require("jsdom");
const BASE = process.env.DWANIFORMS_URL || "http://127.0.0.1:8100";
const html = fs.readFileSync(path.join(__dirname, "..", "..", "dwaniforms", "static", "yojana.html"), "utf8");
const ok = (c, m) => { console.log((c ? "PASS " : "FAIL ") + m); if (!c) process.exitCode = 1; };
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, ms = 8000) { const end = Date.now() + ms; while (Date.now() < end) { if (fn()) return true; await wait(30); } return false; }

async function boot(withSpeech, sayings) {
  const w = new JSDOM(html, { url: BASE + "/form/yojana", runScripts: "dangerously", pretendToBeVisual: true,
    beforeParse(w) {
      w.fetch = (u, opt) => fetch(new URL(u, BASE + "/form/").href, opt);
      w.scrollTo = () => {}; w.Element.prototype.scrollIntoView = () => {};
      if (withSpeech) w.webkitSpeechRecognition = class {
        start() { w.__lang = this.lang; const said = sayings.shift(); setTimeout(() => { if (said !== undefined) this.onresult({ results: [[{ transcript: said }]] }); this.onend(); }, 10); }
        stop() {}
      };
    } }).window;
  return w;
}

(async () => {
  let w = await boot(false, []);
  w.document.getElementById("go").click(); await until(() => /उम्र/.test(w.document.getElementById("qh").textContent));
  ok(w.document.getElementById("voice").hidden, "no microphone button where the browser cannot listen");

  const said = ["बासठ साल", "मैं महिला हूँ", "पता नहीं क्या कहूँ", "गाँव में", "मैं खेती करता हूँ", "ढाई एकड़", "डेढ़ लाख"];
  w = await boot(true, said); const d = w.document, $ = (id) => d.getElementById(id);
  $("go").click(); await until(() => /उम्र/.test($("qh").textContent));
  ok(!$("voice").hidden, "the microphone button is shown");
  const speak = async (until_) => { $("mic").click(); return until(until_); };
  ok(await speak(() => /कौन/.test($("qh").textContent)), "'बासठ साल' answers the age (62) and moves on");
  ok(await speak(() => /कहाँ/.test($("qh").textContent)) && w.__lang === "hi-IN", "'मैं महिला हूँ' answers gender; the recognizer was set to Hindi");
  ok(await speak(() => /समझ नहीं आया/.test($("heard").textContent)) && /कहाँ/.test($("qh").textContent), "words that mean nothing here keep the same question and say so");
  ok(await speak(() => /काम/.test($("qh").textContent)), "'गाँव में' answers the area");
  ok(await speak(() => /ज़मीन/.test($("qh").textContent)), "'मैं खेती करता हूँ' answers the work: a farmer is now asked about land");
  ok(await speak(() => /कमाई/.test($("qh").textContent)), "'ढाई एकड़' answers the land as 2.5 acres");
  ok(await speak(() => /आयकर/.test($("qh").textContent)), "'डेढ़ लाख' picks the ₹1 to 2 lakh band for the income");
  console.log(process.exitCode ? "FAILED" : "all passed");
})();
