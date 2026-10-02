// Presenter page Notes tab (static/host.html): status, session list, download links, pause.
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");
const html = fs.readFileSync(path.join(__dirname, "..", "..", "static", "host.html"), "utf8");
const ok = (c, m) => { console.log((c ? "PASS " : "FAIL ") + m); if (!c) process.exitCode = 1; };
const flush = () => new Promise((r) => setTimeout(r, 40));

let status = { available: true, recording: true, captions: 42, id: "2026-10-02_1830_abc123" };
const sessions = [
  { id: "2026-10-02_1830_abc123", title: "Session ABC123", started: 1790000000, captions: 42, minutes: 31.5, src_lang: "en", languages: ["en", "hi"] },
  { id: "2026-09-30_1000_old111", title: "Session OLD111", started: 1789800000, captions: 300, minutes: 58, src_lang: "hi", languages: ["hi"] },
];
const posts = [];
function boot(query) {
  return new JSDOM(html, { url: "http://localhost:8000/host?session=abc123" + (query || ""), runScripts: "dangerously", pretendToBeVisual: true,
    beforeParse(w) {
      w.fetch = (u, opt) => {
        if (opt && opt.method === "POST") { posts.push(u); status = Object.assign({}, status, { recording: /on=true/.test(u) }); }
        const body = u.startsWith("/notes/status") ? status : u.startsWith("/notes/sessions") ? sessions
          : u.startsWith("/presenter-info") ? { session_id: "abc123", join_url: "http://x/?session=abc123", qr_url: "/qr.png" }
          : u.startsWith("/preflight") ? { overall: "ok", checks: [] } : u.startsWith("/qa/pending") ? [] : {};
        return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
      };
      class WS extends w.EventTarget { constructor() { super(); this.readyState = 0; } send() {} close() {} }
      WS.OPEN = 1; w.WebSocket = WS;
    } }).window;
}
const $ = (w, id) => w.document.getElementById(id);

(async () => {
  const opened = [];
  let w = boot(); opened.push(w);
  await flush();
  w.document.querySelector('.tab-btn[data-tab="notes"]').click();
  await flush();
  ok(!$(w, "tab-notes").hidden && $(w, "tab-stats").hidden, "Notes tab opens");
  ok(/Saving notes · 42 captions/.test($(w, "notes-status-text").textContent) && $(w, "notes-dot").classList.contains("on"), "shows live recording status");
  const items = w.document.querySelectorAll("#notes-list li");
  ok(items.length === 2 && /This session/.test(items[0].textContent), "lists this session first, then past sessions");
  ok($(w, "notes-lang").value === "en", "language defaults to the talk's language");
  const links = [...items[0].querySelectorAll("a")].map((a) => a.getAttribute("href"));
  ok(links.length === 4 && links[0] === "/notes/export?id=2026-10-02_1830_abc123&lang=en&format=notes", "download links: " + links[0]);
  ok(items[0].querySelector("a.primary").target === "_blank", "Notes (PDF) opens in a new tab");
  $(w, "notes-lang").value = "ta"; $(w, "notes-lang").dispatchEvent(new w.Event("change"));
  ok(/தமிழ் will be translated on download/.test(w.document.querySelector("#notes-list li .notes-meta").textContent), "warns that a non-live language is translated on download");
  ok(w.document.querySelector("#notes-list a").getAttribute("href").includes("lang=ta"), "links switch to the chosen language");
  $(w, "notes-toggle").click(); await flush();
  ok(posts.some((u) => u.startsWith("/notes/recording?on=false")) && /Saving paused/.test($(w, "notes-status-text").textContent), "Pause saving works");

  w = boot("&key=SECRET"); opened.push(w);   // presenter's phone (phone-mic link) passes its key along
  await flush();
  w.document.querySelector('.tab-btn[data-tab="notes"]').click(); await flush();
  ok(w.document.querySelector("#notes-list a").getAttribute("href").endsWith("&key=SECRET"), "phone presenter: links carry the presenter key");
  opened.forEach((win) => win.close());   // stop the page's polling timers so the process exits
})();
