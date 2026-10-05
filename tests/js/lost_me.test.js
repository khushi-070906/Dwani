// "Lost me": attendee button (static/index.html) and presenter banner (static/host.html).
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");
const FakeTimers = require("@sinonjs/fake-timers");
const S = (f) => fs.readFileSync(path.join(__dirname, "..", "..", "static", f), "utf8");
const I18N = S("i18n.js");
const ok = (c, m) => { console.log((c ? "PASS " : "FAIL ") + m); if (!c) process.exitCode = 1; };
const flush = async () => { for (let i = 0; i < 6; i++) await Promise.resolve(); };
let sockets = [], clock, dash = {};

function boot(file, lang = "en-US", withI18n = false) {
  sockets = [];
  let html = S(file);
  if (withI18n) html = html.replace('<script src="/static/i18n.js"></script>', "<script>" + I18N + "</script>");
  return new JSDOM(html, { url: "http://localhost:8000/" + (file === "host.html" ? "host" : "") + "?session=abc123",
    runScripts: "dangerously", pretendToBeVisual: true,
    beforeParse(w) {
      clock = FakeTimers.withGlobal(w).install({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "Date"] });
      Object.defineProperty(w.navigator, "language", { value: lang, configurable: true });
      w.fetch = (u) => Promise.resolve({ ok: true, json: () => Promise.resolve(
        u.startsWith("/dashboard-stats") ? dash
        : u.startsWith("/presenter-info") ? { session_id: "abc123", join_url: "http://x/?session=abc123", qr_url: "/qr.png" }
        : u.startsWith("/preflight") ? { overall: "ok", checks: [] } : u.startsWith("/qa/pending") ? [] : {}) });
      class WS extends w.EventTarget {
        constructor(u) { super(); this.url = u; this.readyState = 0; this.sent = []; sockets.push(this); }
        send(d) { this.sent.push(JSON.parse(d)); } close() { this.readyState = 3; }
        _open() { this.readyState = 1; this.dispatchEvent(new w.Event("open")); }
        _msg(o) { const e = new w.Event("message"); e.data = JSON.stringify(o); this.dispatchEvent(e); }
        _drop() { this.readyState = 3; const e = new w.Event("close"); e.code = 1006; this.dispatchEvent(e); }
      }
      WS.OPEN = 1; WS.CONNECTING = 0; w.WebSocket = WS;
    } }).window;
}
const $ = (w, id) => w.document.getElementById(id);

(async () => {
  // attendee
  let w = boot("index.html");
  w.document.querySelector(".lang-btn").click();
  ok($(w, "lost-btn").hidden, "button hidden until connected");
  sockets[0]._open();
  ok(!$(w, "lost-btn").hidden && $(w, "lost-btn").textContent === "🤔 Lost me", "button appears when live");
  $(w, "lost-btn").click();
  ok(sockets[0].sent.some((m) => m.type === "confused"), "tap sends an anonymous 'confused' message");
  ok($(w, "lost-btn").disabled && /Sent/.test($(w, "lost-btn").textContent), "confirms and disables");
  $(w, "lost-btn").click();
  ok(sockets[0].sent.filter((m) => m.type === "confused").length === 1, "no repeat taps during the cooldown");
  sockets[0]._msg({ type: "confused_ack", counted: true });
  ok(!$(w, "caption-stage").textContent.includes("confused"), "server acknowledgement isn't shown as a caption");
  clock.tick(20000);
  ok(!$(w, "lost-btn").disabled && $(w, "lost-btn").textContent === "🤔 Lost me", "available again after 20 s");
  sockets[0]._drop();
  ok($(w, "lost-btn").hidden, "hidden while disconnected");

  w = boot("index.html", "en-US", true);
  w.document.querySelector('.lang-btn[data-lang="hi"]').click(); sockets[0]._open(); await flush();
  ok($(w, "lost-btn").textContent === "🤔 समझ नहीं आया", "Hindi interface: 'समझ नहीं आया'");

  // presenter
  dash = { attendees: 30, by_language: { hi: 30 }, max_attendees: null, presenting: true, latency: { count: 0, captions_sent: 0 },
           confusion: { people: 1, attendees: 30, share: 0.03, alert: false, about: "x", total: 1 } };
  w = boot("host.html"); await flush();
  clock.tick(3100); await flush();
  ok($(w, "lost-banner").hidden && $(w, "stat-lost").textContent === "1", "one tap: counted, no banner: hidden=" + $(w, "lost-banner").hidden + " stat=" + $(w, "stat-lost").textContent);
  dash = Object.assign({}, dash, { confusion: { people: 6, attendees: 30, share: 0.2, alert: true,
           about: "the chain rule applied layer by layer", total: 7 } });
  clock.tick(3100); await flush();
  ok(!$(w, "lost-banner").hidden && $(w, "lost-head").textContent === "🤔 6 of 30 lost you in the last minute" &&
     /chain rule/.test($(w, "lost-about").textContent), "banner: how many, and on which sentence");
  dash = Object.assign({}, dash, { confusion: { people: 0, attendees: 30, share: 0, alert: false, about: "", total: 7 } });
  clock.tick(3100); await flush();
  ok($(w, "lost-banner").hidden && $(w, "stat-lost").textContent === "7", "banner goes away once the room is following again");
  process.exit(process.exitCode || 0);
})();
