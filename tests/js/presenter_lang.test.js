// Presenter page: "I'm speaking" language selector.
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");
const S = (f) => fs.readFileSync(path.join(__dirname, "..", "..", "static", f), "utf8");
const ok = (c, m) => { console.log((c ? "PASS " : "FAIL ") + m); if (!c) process.exitCode = 1; };
const flush = () => new Promise((r) => setTimeout(r, 40));
let state = { choice: "en" }, posts = [];
function boot(i18n) {
  let html = S("host.html");
  if (i18n) html = html.replace('<script src="/static/i18n.js"></script>', "<script>" + S("i18n.js") + "</script>");
  return new JSDOM(html, { url: "http://localhost:8000/host?session=abc123", runScripts: "dangerously", pretendToBeVisual: true,
    beforeParse(w) {
      if (i18n) w.localStorage.setItem("dwani:hostLang", "hi");
      w.fetch = (u, opt) => {
        let body = {}, status = 200;
        if (u.startsWith("/presenter-language")) {
          if (opt && opt.method === "POST") {
            const lang = JSON.parse(opt.body).language; posts.push(lang);
            if (lang === "xx") { status = 400; body = { error: "That language isn't supported." }; }
            else { state.choice = lang; body = { choice: lang, speaking: lang === "auto" ? "en" : lang }; }
          } else body = { choice: state.choice, speaking: state.choice, choices: ["auto", "en", "hi"] };
        } else if (u.startsWith("/presenter-info")) body = { session_id: "abc123", join_url: "http://x/?session=abc123", qr_url: "/qr.png" };
        else if (u.startsWith("/preflight")) body = { overall: "ok", checks: [] };
        else if (u.startsWith("/qa/pending")) body = [];
        return Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) });
      };
      class WS extends w.EventTarget { constructor() { super(); this.readyState = 0; } send() {} close() {} }
      WS.OPEN = 1; w.WebSocket = WS;
    } }).window;
}
const $ = (w, id) => w.document.getElementById(id);
(async () => {
  state.choice = "hi";
  let w = boot(); await flush();
  const sel = $(w, "speak-lang");
  ok(sel.options.length === 15 && sel.options[0].value === "auto", "15 choices: auto-detect + 14 languages");
  ok(sel.value === "hi", "shows the saved choice from the laptop");
  sel.value = "ta"; sel.dispatchEvent(new w.Event("change")); await flush();
  ok(posts.pop() === "ta" && /Listening for தமிழ் from the next sentence/.test($(w, "speak-note").textContent), "switching posts and confirms");
  sel.value = "auto"; sel.dispatchEvent(new w.Event("change")); await flush();
  ok(/Detecting the language of each sentence/.test($(w, "speak-note").textContent), "auto-detect explained");
  const bad = w.document.createElement("option"); bad.value = "xx"; sel.appendChild(bad);
  sel.value = "xx"; sel.dispatchEvent(new w.Event("change")); await flush();
  ok(/isn't supported/.test($(w, "speak-note").textContent), "server refusal is shown, not swallowed");
  w.close();
  w = boot(true); await flush();
  ok(w.document.querySelector(".speak-row label").textContent === "मेरी भाषा" &&
     $(w, "speak-lang").options[0].textContent.startsWith("अपने आप पहचानें"), "Hindi interface translates the selector");
  w.close();
})();
