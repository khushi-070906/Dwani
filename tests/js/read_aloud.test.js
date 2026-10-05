// Attendee page: read captions aloud with the phone's own voice (Web Speech API, stubbed).
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");
const S = (f) => fs.readFileSync(path.join(__dirname, "..", "..", "static", f), "utf8");
const ok = (c, m) => { console.log((c ? "PASS " : "FAIL ") + m); if (!c) process.exitCode = 1; };
const flush = async () => { for (let i = 0; i < 6; i++) await Promise.resolve(); await new Promise((r) => setTimeout(r, 5)); };
let sockets = [], spoken = [], cancels = 0;
const VOICES = [{ name: "Lekha", lang: "hi-IN", localService: true }, { name: "Samantha", lang: "en-US", localService: true }];

function boot({ seed = {}, i18n = false, query = "?session=abc123" } = {}) {
  sockets = [];
  let html = S("index.html");
  if (i18n) html = html.replace('<script src="/static/i18n.js"></script>', "<script>" + S("i18n.js") + "</script>");
  return new JSDOM(html, { url: "http://192.168.1.5:8000/" + query, runScripts: "dangerously", pretendToBeVisual: true,
    beforeParse(w) {
      for (const [k, v] of Object.entries(seed)) w.localStorage.setItem(k, v);
      w.speechSynthesis = { getVoices: () => VOICES, speak: (u) => spoken.push(u), cancel: () => { cancels++; }, onvoiceschanged: null };
      w.SpeechSynthesisUtterance = function (text) { this.text = text; };
      class WS extends w.EventTarget {
        constructor(u) { super(); this.url = u; this.readyState = 0; sockets.push(this); }
        send() {} close() { this.readyState = 3; }
        _open() { this.readyState = 1; this.dispatchEvent(new w.Event("open")); }
        _msg(o) { const e = new w.Event("message"); e.data = JSON.stringify(o); this.dispatchEvent(e); }
      }
      WS.OPEN = 1; WS.CONNECTING = 0; w.WebSocket = WS;
    } }).window;
}
const $ = (w, id) => w.document.getElementById(id);
function toggle(w, id, value) { const el = $(w, id); if (el.type === "checkbox") el.checked = value; else el.value = value; el.dispatchEvent(new w.Event("change")); el.dispatchEvent(new w.Event("input")); }
const said = () => spoken.filter((u) => u.text.trim()).map((u) => u.text);

(async () => {
  let w = boot();
  w.document.querySelector('.lang-btn[data-lang="hi"]').click(); sockets[0]._open(); await flush();
  ok($(w, "a11y-speak").checked === false, "off by default");
  toggle(w, "a11y-speak", true); await flush();
  ok(spoken.length === 1 && spoken[0].volume === 0, "turning it on speaks a silent utterance (unlocks speech on iPhone)");
  ok(!$(w, "a11y-speak-rate-row").hidden && $(w, "a11y-speak-note").textContent === "", "speed control appears; Hindi voice found, no warning");
  sockets[0]._msg({ text: "नमस्ते", final: false });
  sockets[0]._msg({ text: "नमस्ते दोस्तों", final: true });
  const last = spoken[spoken.length - 1];
  ok(JSON.stringify(said()) === '["नमस्ते दोस्तों"]' && last.voice.name === "Lekha" && last.lang === "hi-IN", "final caption read in a Hindi voice; interim words not read");
  sockets[0]._msg({ text: "दूसरा", final: true });
  sockets[0]._msg({ text: "तीसरा", final: true });   // speech hasn't finished: 3 queued -> jump to the latest
  ok(cancels >= 1 && said().slice(-1)[0] === "तीसरा", "falls behind -> skips ahead to the latest caption");
  toggle(w, "a11y-speak-rate", "1.2");
  spoken[spoken.length - 1].onend();
  sockets[0]._msg({ text: "तेज़", final: true });
  ok(spoken[spoken.length - 1].rate === 1.2, "reading speed applies");

  // the setting survives changing another option and reloading
  toggle(w, "a11y-font-scale", "1.4");
  const seed = {}; for (let i = 0; i < w.localStorage.length; i++) { const k = w.localStorage.key(i); seed[k] = w.localStorage.getItem(k); }
  w.close();
  spoken = []; cancels = 0;
  w = boot({ seed }); await flush();
  ok($(w, "a11y-speak").checked && $(w, "a11y-speak-rate").value === "1.2", "still on after another setting changed + reload");
  toggle(w, "a11y-dark", true);           // change something else after the reload...
  const seed2 = {}; for (let i = 0; i < w.localStorage.length; i++) { const k = w.localStorage.key(i); seed2[k] = w.localStorage.getItem(k); }
  w.close(); w = boot({ seed: seed2 }); await flush();   // ...and reload again
  ok($(w, "a11y-speak").checked, "still on after a second reload (setting isn't dropped by other settings)");
  sockets[0] && sockets[0]._open(); await flush();
  sockets[0]._msg({ text: "फिर से", final: true });
  ok(said().includes("फिर से"), "reads again after reload");
  $(w, "a11y-reset").click(); await flush();
  ok(!$(w, "a11y-speak").checked && cancels >= 1, "Reset turns it off and stops speaking");
  w.close();

  // a language the phone has no voice for
  spoken = [];
  w = boot({ query: "?session=tam111" });
  w.document.querySelector('.lang-btn[data-lang="ta"]').click(); sockets[0]._open(); await flush();
  toggle(w, "a11y-speak", true); await flush();
  sockets[0]._msg({ text: "வணக்கம்", final: true });
  ok(/no தமிழ் voice/.test($(w, "a11y-speak-note").textContent) && said().length === 0, "no Tamil voice: explains how to add one, stays silent");
  w.close();

  w = boot({ i18n: true, query: "?session=hin222" });
  w.document.querySelector('.lang-btn[data-lang="hi"]').click(); sockets[0]._open(); await flush();
  ok(w.document.querySelector('label[for], #a11y-panel').textContent.includes("कैप्शन पढ़कर सुनाएँ"), "Hindi interface: 'कैप्शन पढ़कर सुनाएँ'");
  w.close();
})();
