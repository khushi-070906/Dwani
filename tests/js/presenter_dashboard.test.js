// Presenter dashboard (static/host.html) in jsdom: audience per language, caption
// delay, microphone meter and silent/too-loud warnings. Fake mic, fake server.
// Run:  cd tests/js && npm install && npm run test:dashboard
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");
const FakeTimers = require("@sinonjs/fake-timers");
const html = fs.readFileSync(path.join(__dirname, "..", "..", "static", "host.html"), "utf8");
let clock, proc, sockets = [], dash = {}, urls = [];

const ok = (c, m) => { console.log((c ? "PASS " : "FAIL ") + m); if (!c) process.exitCode = 1; };
const flush = async () => { for (let i = 0; i < 6; i++) { await Promise.resolve(); } };

function boot() {
  const dom = new JSDOM(html, {
    url: "http://localhost:8000/host?session=abc123", runScripts: "dangerously", pretendToBeVisual: true,
    beforeParse(w) {
      clock = FakeTimers.withGlobal(w).install({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "Date"] });
      w.fetch = (url) => {
        urls.push(url);
        const body = url.startsWith("/presenter-info") ? { session_id: "abc123", join_url: "http://192.168.1.5:8000/?session=abc123", qr_url: "/qr.png" }
          : url.startsWith("/dashboard-stats") ? dash
          : url.startsWith("/preflight") ? { overall: "ok", checks: [] }
          : url.startsWith("/qa/pending") ? [] : { enabled: false, connected_attendees: 0, by_feature: {} };
        return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
      };
      class WS extends w.EventTarget {
        constructor(u) { super(); this.url = u; this.readyState = 0; sockets.push(this); }
        send() {} close() { this.readyState = 3; }
        _open() { this.readyState = 1; this.dispatchEvent(new w.Event("open")); }
      }
      WS.OPEN = 1; w.WebSocket = WS;
      Object.defineProperty(w.navigator, "mediaDevices", { value: { getUserMedia: () => Promise.resolve({ getTracks: () => [{ stop() {} }], getAudioTracks: () => [] }) } });
      w.AudioContext = function () {
        this.sampleRate = 16000; this.destination = {};
        this.createMediaStreamSource = () => ({ connect() {}, disconnect() {} });
        this.createScriptProcessor = () => { proc = new w.EventTarget(); proc.connect = () => {}; proc.disconnect = () => {}; return proc; };
        this.createGain = () => ({ gain: { value: 1 }, connect() {}, disconnect() {} });
        this.close = () => Promise.resolve();
      };
    },
  });
  return dom.window;
}
function audio(w, amp) {   // one 4096-sample chunk of a sine wave at the given amplitude
  const buf = new Float32Array(4096);
  for (let i = 0; i < buf.length; i++) buf[i] = amp * Math.sin(i / 5);
  const e = new w.Event("audioprocess"); e.inputBuffer = { getChannelData: () => buf };
  proc.dispatchEvent(e);
}
const $ = (w, id) => w.document.getElementById(id);

(async () => {
  dash = { attendees: 0, by_language: {}, max_attendees: 20, presenting: false, latency: { count: 0, captions_sent: 0 } };
  const w = boot();
  await flush();
  ok(urls.some((u) => u.startsWith("/presenter-info")), "page loads session info");

  // start presenting with a fake microphone
  $(w, "mic-btn").click(); await flush();
  const host = sockets.find((x) => x.url.includes("/host-ws"));
  ok(!!host, "Start opens the presenter audio socket");
  host._open();
  ok(!$(w, "live-meters").hidden, "mic meter + delay chip appear while presenting");
  audio(w, 0.1);
  ok(parseFloat($(w, "mic-meter-fill").style.width) > 20 && $(w, "mic-meter").getAttribute("aria-valuenow") !== "0", "meter moves with speech: " + $(w, "mic-meter-fill").style.width);
  for (let i = 0; i < 60; i++) { audio(w, 0.001); clock.tick(250); }   // 15 s of near silence
  ok(/Can't hear anything/.test($(w, "mic-hint").textContent), "silent-mic warning after ~12 s: " + $(w, "mic-hint").textContent);
  audio(w, 0.1); clock.tick(2000); audio(w, 0.1);
  ok($(w, "mic-hint").textContent === "", "warning clears when you speak");
  for (let i = 0; i < 10; i++) { audio(w, 1.2); clock.tick(200); }
  ok(/Too loud/.test($(w, "mic-hint").textContent), "distortion warning when clipping");
  for (let i = 0; i < 40; i++) { audio(w, 0.1); clock.tick(200); }
  ok($(w, "mic-hint").textContent === "", "too-loud warning clears");

  // audience + latency from the server
  dash = { attendees: 7, by_language: { hi: 4, ta: 2, en: 1 }, max_attendees: 20, presenting: true,
           latency: { count: 12, captions_sent: 12, p50_ms: 1200, p95_ms: 2600, last_ms: 1100, asr_avg_ms: 800, mt_avg_ms: 350, seconds_since_last: 3 } };
  clock.tick(3100); await flush();
  const pills = [...w.document.querySelectorAll("#lang-pill-row .lang-pill")].map((p) => p.textContent);
  ok(JSON.stringify(pills) === JSON.stringify(["हिन्दी4", "தமிழ்2", "English1"]), "language pills show names + counts, biggest first: " + pills.join(" | "));
  ok(w.document.querySelector('#lang-pill-row .lang-name[lang="hi"]'), "pill names carry their language for screen readers");
  ok($(w, "stat-attendees").textContent === "7" && $(w, "stat-languages").textContent === "3", "attendee + language totals");
  ok($(w, "stat-delay").textContent === "1.2s" && $(w, "stat-delay").classList.contains("good"), "typical delay 1.2s shown as good");
  ok(/slowest 2.6s/.test($(w, "stat-delay-sub").textContent) && /speech→text 0.8s/.test($(w, "stat-delay-sub").textContent), "delay breakdown shown");
  ok($(w, "delay-chip").textContent === "Caption delay ~1.2s", "live delay chip under the mic");
  ok($(w, "stat-captions").textContent === "12", "captions sent counter");

  dash = Object.assign({}, dash, { attendees: 19, latency: Object.assign({}, dash.latency, { p50_ms: 4200 }) });
  clock.tick(3100); await flush();
  ok($(w, "delay-chip").classList.contains("slow") && /close other apps/.test($(w, "delay-chip").title), "slow delay turns red with a hint");
  ok(!$(w, "no-audience").hidden && /19 of 20 seats/.test($(w, "no-audience").textContent), "near the plan's seat limit: warning");

  $(w, "mic-btn").click(); await flush();
  ok($(w, "live-meters").hidden && $(w, "mic-hint").textContent === "", "meters hidden after Stop");
})();
