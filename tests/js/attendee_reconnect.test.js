// Simulates the attendee page (static/index.html) in jsdom with a fake WebSocket and a
// fast-forward clock: dead sockets, drops, network changes, reloads, transcript saving.
// Run:  cd tests/js && npm install && npm test
const fs = require("fs");
const { JSDOM } = require("jsdom");
const FakeTimers = require("@sinonjs/fake-timers");
const html = fs.readFileSync(require("path").join(__dirname, "..", "..", "static", "index.html"), "utf8");
let sockets = [], clock, savedBlob = null;

function boot(seed) {
  sockets = [];
  const dom = new JSDOM(html, {
    url: "http://192.168.1.5:8000/?session=abc123", runScripts: "dangerously", pretendToBeVisual: true,
    beforeParse(w) {
      clock = FakeTimers.withGlobal(w).install({ now: Date.parse("2026-10-02T10:00:00Z"), toFake: ["setTimeout","clearTimeout","setInterval","clearInterval","Date"] });
      if (seed) for (const [k, v] of Object.entries(seed)) w.localStorage.setItem(k, v);
      class WS extends w.EventTarget {
        constructor(url) { super(); this.url = url; this.readyState = 0; this.sent = []; sockets.push(this); }
        send(d) { this.sent.push(JSON.parse(d)); }
        close() { this.readyState = 3; }
        _open() { this.readyState = 1; this.dispatchEvent(new w.Event("open")); }
        _msg(o) { const e = new w.Event("message"); e.data = JSON.stringify(o); this.dispatchEvent(e); }
        _drop(code = 1006) { this.readyState = 3; const e = new w.Event("close"); e.code = code; e.reason = ""; this.dispatchEvent(e); }
      }
      WS.CONNECTING = 0; WS.OPEN = 1; WS.CLOSING = 2; WS.CLOSED = 3;
      w.WebSocket = WS;
      const conn = new w.EventTarget(); Object.defineProperty(w.navigator, "connection", { value: conn });
      const RealBlob = w.Blob; w.Blob = function (parts, opts) { const b = new RealBlob(parts, opts); b._parts = parts; return b; };
      w.URL.createObjectURL = (b) => { savedBlob = b; return "blob:x"; }; w.URL.revokeObjectURL = () => {};
      w.HTMLAnchorElement.prototype.click = function () {};
    },
  });
  return dom.window;
}
const $ = (w, id) => w.document.getElementById(id);
const status = (w) => $(w, "status-text").textContent;
const ok = (cond, msg) => { console.log((cond ? "PASS " : "FAIL ") + msg); if (!cond) process.exitCode = 1; };

(async () => {
  // 1. first visit: pick a language, go live, receive captions
  let w = boot();
  ok(!$(w, "join-screen") || $(w, "live-screen").hidden, "starts on the language picker (no saved session)");
  w.document.querySelector(".lang-btn").click();
  ok(sockets.length === 1 && /\/ws\?lang=/.test(sockets[0].url), "joining opens a WebSocket");
  sockets[0]._open();
  ok(status(w) === "Live", "status Live after open");
  sockets[0]._msg({ text: "Hello everyone", final: true });
  sockets[0]._msg({ text: "Today we discuss", final: true });
  ok($(w, "caption-stage").textContent.includes("Hello everyone"), "captions render");
  clock.tick(1000);
  const lang = JSON.parse(w.localStorage.getItem("dwani:last")).lang;
  ok(JSON.parse(w.localStorage.getItem("dwani:h:abc123:" + lang)).length === 2, "transcript persisted to localStorage");

  // 2. heartbeat + silently dead socket
  clock.tick(10000);
  ok(sockets[0].sent.some((m) => m.type === "ping"), "sends ping every 10s");
  sockets[0]._msg({ type: "pong", t: 1 });
  ok(!$(w, "caption-stage").textContent.includes("pong"), "pong is not shown as a caption");
  clock.tick(30000);  // nothing arrives for 30s: socket is dead but never fired close
  ok(sockets.length === 2, "stale socket detected -> new connection opened");
  sockets[0]._drop();  // late close of the old socket must be ignored
  ok(sockets.length === 2, "late close event of the replaced socket is ignored");
  sockets[1]._open();
  ok(/reconnected \(~\d+s missed\)/.test(status(w)), "gap notice after reconnect: " + status(w));
  ok($(w, "caption-stage").textContent.includes("Today we discuss"), "old captions stay on screen through the reconnect");
  clock.tick(9000);
  ok(status(w) === "Live", "gap notice clears");

  // 3. normal drop -> countdown -> reconnect, forever
  sockets[1]._drop(1006);
  ok(/Reconnecting in \ds…/.test(status(w)), "countdown shown: " + status(w));
  ok($(w, "reconnect-banner").classList.contains("visible"), "reconnect banner visible");
  clock.tick(1500);
  ok(sockets.length === 3, "reconnects after backoff");
  for (let i = 0; i < 8; i++) { sockets[sockets.length - 1]._drop(); clock.tick(10000); }
  ok(sockets.length === 11, "keeps retrying (never gives up): " + sockets.length + " sockets");
  sockets[sockets.length - 1]._open();
  ok(/^Live/.test(status(w)), "live again after many failures");

  // 4. network change (Wi-Fi -> other Wi-Fi) forces a fresh socket
  const before = sockets.length;
  w.navigator.connection.dispatchEvent(new w.Event("change"));
  ok(sockets.length === before + 1, "network change -> immediate reconnect");
  sockets[sockets.length - 1]._open();
  sockets[sockets.length - 1]._msg({ text: "After the switch", final: true });

  // 5. save transcript
  $(w, "save-btn").click();
  const txt = savedBlob ? savedBlob._parts.join("") : "";
  ok(txt.includes("Session ABC123") && txt.includes("Hello everyone") && txt.includes("After the switch") && /\[\d\d:\d\d\]/.test(txt), "Save downloads a timestamped transcript");

  // 6. reload: same session + language + captions come back automatically
  const seed = {};
  for (let i = 0; i < w.localStorage.length; i++) { const k = w.localStorage.key(i); seed[k] = w.localStorage.getItem(k); }
  clock.tick(1000);
  for (let i = 0; i < w.localStorage.length; i++) { const k = w.localStorage.key(i); seed[k] = w.localStorage.getItem(k); }
  w = boot(seed);
  ok(!$(w, "live-screen").hidden && sockets.length === 1, "reload rejoins automatically (no language picker)");
  ok($(w, "caption-stage").textContent.includes("After the switch"), "previous captions restored after reload");

  // 7. server says "session full" (4003) / "unknown session" (4000): explain, don't hammer the laptop
  {
    const n0 = sockets.length;
    const cur = sockets[sockets.length - 1];
    cur._drop(4003);
    clock.tick(30000);
    ok(sockets.length === n0 && /Session full/.test(status(w)), "4003 session full: message shown, no retry loop");
  }
  w = boot(seed);
  {
    const n0 = sockets.length;
    sockets[sockets.length - 1]._drop(4000);
    clock.tick(30000);
    ok(sockets.length === n0 && /Session not found/.test(status(w)), "4000 unknown session: asks to re-scan, no retry loop");
  }
  w = boot(seed);

  // 8. change language forgets the auto-rejoin
  $(w, "change-lang-btn").click();
  ok(w.localStorage.getItem("dwani:last") === null && !$(w, "join-screen").hidden, "Change language returns to picker and forgets auto-rejoin");
  sockets[0]._drop();
  ok(sockets.length === 1, "no reconnect after leaving deliberately");
})();
