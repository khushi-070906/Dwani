// Accessibility & display settings on the attendee page (static/index.html), simulated in jsdom.
// Run:  cd tests/js && npm install && npm run test:a11y
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");
const FakeTimers = require("@sinonjs/fake-timers");
const html = fs.readFileSync(path.join(__dirname, "..", "..", "static", "index.html"), "utf8");
let sockets = [], clock, fsCalls = { enter: 0, exit: 0 };

function boot({ seed = {}, dark = false, query = "?session=abc123" } = {}) {
  sockets = [];
  const dom = new JSDOM(html, {
    url: "http://192.168.1.5:8000/" + query, runScripts: "dangerously", pretendToBeVisual: true,
    beforeParse(w) {
      clock = FakeTimers.withGlobal(w).install({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "Date"] });
      for (const [k, v] of Object.entries(seed)) w.localStorage.setItem(k, v);
      w.matchMedia = (q) => ({ matches: dark && q.includes("dark"), addEventListener() {}, removeEventListener() {} });
      class WS extends w.EventTarget {
        constructor(url) { super(); this.url = url; this.readyState = 0; this.sent = []; sockets.push(this); }
        send(d) { this.sent.push(JSON.parse(d)); }
        close() { this.readyState = 3; }
        _open() { this.readyState = 1; this.dispatchEvent(new w.Event("open")); }
        _msg(o) { const e = new w.Event("message"); e.data = JSON.stringify(o); this.dispatchEvent(e); }
      }
      WS.OPEN = 1; WS.CONNECTING = 0;
      w.WebSocket = WS;
      let fsEl = null;
      Object.defineProperty(w.Document.prototype, "fullscreenElement", { configurable: true, get: () => fsEl, set: (v) => { fsEl = v; } });
      w.Element.prototype.requestFullscreen = function () { fsCalls.enter++; fsEl = this; return Promise.resolve(); };
      w.Document.prototype.exitFullscreen = function () { fsCalls.exit++; fsEl = null; return Promise.resolve(); };
    },
  });
  return dom.window;
}
const $ = (w, id) => w.document.getElementById(id);
const has = (w, c) => w.document.body.classList.contains(c);
const ok = (cond, msg) => { console.log((cond ? "PASS " : "FAIL ") + msg); if (!cond) process.exitCode = 1; };
function set(w, id, value) {
  const el = $(w, id);
  if (el.type === "checkbox") el.checked = value; else el.value = value;
  el.dispatchEvent(new w.Event("input", { bubbles: true }));
}

// 1. follows the phone's dark mode by default
let w = boot({ dark: true });
ok(has(w, "a11y-dark") && $(w, "a11y-dark").checked, "dark mode on by default when the phone is in dark mode");
w = boot({ dark: false });
ok(!has(w, "a11y-dark"), "light by default otherwise");

// 2. join, open panel, change settings
w.document.querySelector('.lang-btn[type="button"]').click();   // English
sockets[0]._open();
const btn = $(w, "a11y-btn");
btn.click();
ok(btn.getAttribute("aria-expanded") === "true" && !$(w, "a11y-panel").hidden, "panel opens, aria-expanded=true");
set(w, "a11y-dark", true);
set(w, "a11y-easyread", true);
set(w, "a11y-leading", "2");
set(w, "a11y-font-scale", "1.5");
ok(has(w, "a11y-dark") && has(w, "a11y-easyread"), "dark + easy-read classes applied");
ok(w.document.documentElement.style.getPropertyValue("--a11y-leading") === "2", "line spacing applied");
ok($(w, "a11y-font-scale").getAttribute("aria-valuetext") === "150%", "text size announces as 150%");
set(w, "a11y-high-contrast", true);
ok(has(w, "a11y-high-contrast") && !has(w, "a11y-dark"), "high contrast overrides dark");
const sent = sockets[0].sent.filter((m) => m.type === "settings").pop();
ok(sent && sent.accessibility.font_scale === 1.5 && !("dark" in sent.accessibility), "server gets only its own settings (no UI-only keys)");

// 3. saved on the phone and restored on reload
const seed = {};
for (let i = 0; i < w.localStorage.length; i++) { const k = w.localStorage.key(i); seed[k] = w.localStorage.getItem(k); }
w = boot({ seed, dark: false });
ok(has(w, "a11y-easyread") && has(w, "a11y-high-contrast") && $(w, "a11y-leading").value === "2" && $(w, "a11y-font-scale").value === "1.5",
   "settings restored after reload");
$(w, "a11y-reset").click();
ok(!has(w, "a11y-easyread") && !has(w, "a11y-high-contrast") && $(w, "a11y-font-scale").value === "1", "Reset restores defaults");

// 4. screen reader: finals only, correct language, RTL, status not spammed
w = boot({ query: "?session=xyz789" });
w.document.querySelectorAll(".lang-btn")[7].click();   // Urdu
const s = sockets[0]; s._open();
ok($(w, "caption-stage").getAttribute("aria-hidden") === "true", "visual caption stage hidden from screen readers (no double reading)");
s._msg({ text: "سلام", final: false });
ok($(w, "sr-captions").childNodes.length === 0, "interim (half-recognised) words are not announced");
s._msg({ text: "سلام دوستو", final: true });
const ann = $(w, "sr-captions").lastChild;
ok(ann && ann.textContent === "سلام دوستو" && ann.lang === "ur", "final caption announced with lang=ur");
ok($(w, "caption-stage").querySelector(".caption-line").dir === "auto", "caption lines use dir=auto (Urdu reads right-to-left)");
ok($(w, "caption-stage").lang === "ur", "caption stage tagged with the caption language");
ok($(w, "sr-captions").getAttribute("role") === "log" && $(w, "sr-status").getAttribute("role") === "status", "live regions have roles");
s.readyState = 3; const e = new w.Event("close"); e.code = 1006; s.dispatchEvent(e);
const first = $(w, "sr-status").textContent;
clock.tick(400); clock.tick(400);
ok(first === "Reconnecting to the presenter" && $(w, "sr-status").textContent === first, "reconnect announced once, countdown ticks not re-announced");
ok(w.document.querySelector('.lang-native[lang="hi"]'), "language names carry their own lang (Hindi read in a Hindi voice)");
ok($(w, "custom-lang-input").getAttribute("aria-label"), "custom language field has an accessible name");

// 5. full screen / projector
w = boot();
w.document.querySelector(".lang-btn").click(); sockets[0]._open();
$(w, "focus-btn").click();
ok(has(w, "a11y-focus") && fsCalls.enter === 1, "Full screen: focus class + Fullscreen API");
ok(w.document.activeElement === $(w, "focus-exit"), "focus moves to the Exit button");
w.document.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Escape" }));
ok(!has(w, "a11y-focus") && fsCalls.exit === 1, "Escape leaves full screen");
$(w, "focus-btn").click();
w.document.fullscreenElement = null; w.document.dispatchEvent(new w.Event("fullscreenchange"));
ok(!has(w, "a11y-focus"), "leaving browser full screen also leaves caption full screen");
$(w, "caption-stage").dispatchEvent(new w.MouseEvent("dblclick", { bubbles: true }));
ok(has(w, "a11y-focus"), "double-tap captions enters full screen");
