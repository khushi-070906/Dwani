// Interface translation (static/i18n.js) on the attendee and presenter pages.
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");
const FakeTimers = require("@sinonjs/fake-timers");
const S = (f) => fs.readFileSync(path.join(__dirname, "..", "..", "static", f), "utf8");
const I18N = S("i18n.js");
const inline = (html) => html.replace('<script src="/static/i18n.js"></script>', "<script>" + I18N + "</script>");
const ok = (c, m) => { console.log((c ? "PASS " : "FAIL ") + m); if (!c) process.exitCode = 1; };
const flush = async () => { for (let i = 0; i < 6; i++) await Promise.resolve(); };
// Latin text allowed to stay in a Hindi interface: names, formats, keys, units
const ALLOWED = /^(DwaniLive|QR|PDF|\.srt|\.txt|\.md|Escape|Exit|ja, ar|[A-Z0-9]{4,8}|English|[a-z]{2}|[\d\s.:%—·→~()-]+|.*(ध्वनि|लाइव).*|सेशन [A-Z0-9]+|https?:\/\/\S+)$/;

let sockets = [], clock;
function boot(file, { lang = "en", query = "?session=abc123", seed = {} } = {}) {
  sockets = [];
  return new JSDOM(inline(S(file)), {
    url: "http://localhost:8000/" + (file === "host.html" ? "host" : "") + query, runScripts: "dangerously", pretendToBeVisual: true,
    beforeParse(w) {
      clock = FakeTimers.withGlobal(w).install({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "Date"] });
      Object.defineProperty(w.navigator, "language", { value: lang, configurable: true });
      for (const [k, v] of Object.entries(seed)) w.localStorage.setItem(k, v);
      w.fetch = (u) => Promise.resolve({ ok: true, json: () => Promise.resolve(
        u.startsWith("/presenter-info") ? { session_id: "abc123", join_url: "http://x/?session=abc123", qr_url: "/qr.png" }
        : u.startsWith("/dashboard-stats") ? { attendees: 3, by_language: { hi: 3 }, max_attendees: 20, presenting: true,
            latency: { count: 4, captions_sent: 4, p50_ms: 1200, p95_ms: 2100, asr_avg_ms: 800, mt_avg_ms: 300, seconds_since_last: 2 } }
        : u.startsWith("/notes/status") ? { available: true, recording: true, captions: 4, id: "s1" }
        : u.startsWith("/notes/sessions") ? [] : u.startsWith("/qa/pending") ? [] : u.startsWith("/preflight") ? { overall: "ok", checks: [] } : {}) });
      class WS extends w.EventTarget {
        constructor(u) { super(); this.url = u; this.readyState = 0; this.sent = []; sockets.push(this); }
        send(d) { this.sent.push(d); } close() { this.readyState = 3; }
        _open() { this.readyState = 1; this.dispatchEvent(new w.Event("open")); }
        _msg(o) { const e = new w.Event("message"); e.data = JSON.stringify(o); this.dispatchEvent(e); }
        _drop(code) { this.readyState = 3; const e = new w.Event("close"); e.code = code; e.reason = ""; this.dispatchEvent(e); }
      }
      WS.OPEN = 1; WS.CONNECTING = 0; w.WebSocket = WS;
    } }).window;
}
const $ = (w, id) => w.document.getElementById(id);
function leftoverEnglish(w, rootSel) {
  const out = [];
  const tw = w.document.createTreeWalker(w.document.querySelector(rootSel), w.NodeFilter.SHOW_TEXT);
  let n;
  while ((n = tw.nextNode())) {
    const el = n.parentElement;
    if (!el || el.closest("script,style,#caption-stage,#sr-captions,.lang-native,.lang-english,[data-no-i18n],[hidden]")) continue;
    const t = n.data.replace(/\s+/g, " ").trim();
    if (t && /[A-Za-z]{3}/.test(t) && !ALLOWED.test(t)) out.push(t);
  }
  return out;
}

(async () => {
  // ---------- attendee
  let w = boot("index.html", { lang: "hi-IN" }); await flush();
  ok(w.document.body.textContent.includes("अपनी भाषा चुनें") && w.document.documentElement.lang === "hi", "Hindi phone: language picker in Hindi");

  w = boot("index.html", { lang: "en-US" }); await flush();
  ok(w.document.body.textContent.includes("Pick your language"), "English phone: picker in English");
  w.document.querySelector('.lang-btn[data-lang="hi"]').click();
  sockets[0]._open(); await flush();
  ok($(w, "status-text").textContent === "लाइव" && $(w, "save-btn").textContent.trim() === "💾 सेव करें", "choosing Hindi captions switches the interface to Hindi");
  ok($(w, "custom-lang-input").getAttribute("aria-label").startsWith("दूसरी भाषा"), "attributes translated too (aria-label)");
  sockets[0]._msg({ text: "Live", final: true }); await flush();   // a caption that happens to equal a UI string
  ok($(w, "caption-stage").textContent.includes("Live") && !$(w, "caption-stage").textContent.includes("लाइव"), "captions are never translated by the UI layer");
  sockets[0]._drop(1006); await flush();
  ok(/^\d सेकंड में फिर से जुड़ेंगे…$/.test($(w, "status-text").textContent), "countdown with numbers translated: " + $(w, "status-text").textContent);
  clock.tick(1500); sockets[1]._open(); await flush();
  sockets[1]._drop(4003); await flush();
  ok($(w, "status-text").textContent === "सेशन भर गया" && /सेशन भर गया है/.test($(w, "reconnect-banner").textContent), "session full message in Hindi");
  const left = leftoverEnglish(w, "#live-screen");
  ok(left.length === 0, "no untranslated English left on the live screen" + (left.length ? ": " + JSON.stringify(left) : ""));

  w = boot("index.html", { lang: "en-US", query: "?session=xyz111" }); await flush();
  w.document.querySelector('.lang-btn[data-lang="ta"]').click(); sockets[0]._open();
  ok($(w, "status-text").textContent === "Live" && w.document.documentElement.lang === "en", "a language without a UI translation keeps the interface in English");

  // ---------- presenter
  w = boot("host.html", { lang: "en-GB" }); await flush();
  const btn = $(w, "ui-lang");
  ok(btn.textContent === "हिन्दी" && w.document.body.textContent.includes("Check setup"), "presenter starts in English, offers हिन्दी");
  btn.click(); await flush();
  ok(btn.textContent === "English" && w.document.body.textContent.includes("सेटअप जाँचें") &&
     w.document.querySelector('.tab-btn[data-tab="notes"]').textContent.trim() === "नोट्स", "toggle switches the presenter page to Hindi");
  clock.tick(3100); await flush();
  ok($(w, "stat-delay-sub").textContent.startsWith("सबसे धीमा 2.1 से.") , "numbers inside dynamic text translated: " + $(w, "stat-delay-sub").textContent);
  const seed = { "dwani:hostLang": w.localStorage.getItem("dwani:hostLang") };
  const hostLeft = leftoverEnglish(w, "body");
  ok(hostLeft.length === 0, "no untranslated English on the presenter page" + (hostLeft.length ? ": " + JSON.stringify(hostLeft) : ""));
  btn.click(); await flush();
  const hindiLeft = [];
  const tw2 = w.document.createTreeWalker(w.document.body, w.NodeFilter.SHOW_TEXT);
  let n2;
  while ((n2 = tw2.nextNode())) {
    const el = n2.parentElement;
    const t2 = n2.data.trim();
    const nativeName = /^(ध्वनि|हिन्दी|मराठी|नेपाली)$/.test(t2);   // brand + languages' own names stay in their script
    if (el && !el.closest("script,style,[data-no-i18n],.lang-native") && /[\u0900-\u097F]/.test(t2) && !nativeName) hindiLeft.push(t2);
  }
  ok(hindiLeft.length === 0 && w.document.querySelector('.tab-btn[data-tab="notes"]').textContent.trim() === "Notes",
     "switching back restores the original English" + (hindiLeft.length ? ": " + JSON.stringify(hindiLeft) : ""));

  w = boot("host.html", { lang: "en-GB", seed }); await flush();
  ok($(w, "ui-lang").textContent === "English" && w.document.body.textContent.includes("सेटअप जाँचें"), "the choice is remembered after reload");
  process.exit(process.exitCode || 0);
})();
