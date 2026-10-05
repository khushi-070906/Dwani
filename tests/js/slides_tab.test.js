// Presenter page Slides tab (vocabulary from uploaded slides) and Free-plan locks on notes.
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");
const html = fs.readFileSync(path.join(__dirname, "..", "..", "static", "host.html"), "utf8");
const ok = (c, m) => { console.log((c ? "PASS " : "FAIL ") + m); if (!c) process.exitCode = 1; };
const flush = () => new Promise((r) => setTimeout(r, 40));

let terms = [], locked = false, posts = [], notesStatus = { available: true, recording: true, captions: 3, id: "s1", plan: "pro", full_notes: true };
const sessions = [{ id: "s1", title: "S", started: 1790000000, captions: 3, minutes: 2, src_lang: "en", languages: ["en", "hi"] }];
function boot() {
  return new JSDOM(html, { url: "http://localhost:8000/host?session=abc123", runScripts: "dangerously", pretendToBeVisual: true,
    beforeParse(w) {
      w.fetch = (u, opt) => {
        let body = {}, status = 200;
        if (u.startsWith("/glossary/extract")) {
          posts.push(["extract", opt.body instanceof w.FormData]);
          body = locked ? { error: "Slides vocabulary is part of the Pro and Institution plans.", locked: true }
            : { file: "talk.pptx", candidates: [{ term: "gradient descent", count: 3, suggested: true },
                                                 { term: "LSTM", count: 3, suggested: true }, { term: "Deep Learning", count: 1, suggested: false }] };
          status = locked ? 402 : 200;
        } else if (u.startsWith("/glossary/terms")) {
          if (opt && opt.method === "POST") {
            const b = JSON.parse(opt.body); posts.push(["terms", b]);
            if (b.add) terms = terms.concat(b.add.filter((t) => terms.indexOf(t) < 0));
            if (b.remove) terms = terms.filter((t) => b.remove.indexOf(t) < 0);
          }
          body = { available: true, locked, terms, whisper_hint: terms.length > 0 };
        } else if (u.startsWith("/notes/status")) body = notesStatus;
        else if (u.startsWith("/notes/sessions")) body = sessions;
        else if (u.startsWith("/presenter-info")) body = { session_id: "abc123", join_url: "http://x/?session=abc123", qr_url: "/qr.png" };
        else if (u.startsWith("/preflight")) body = { overall: "ok", checks: [] };
        else if (u.startsWith("/qa/pending")) body = [];
        return Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) });
      };
      class WS extends w.EventTarget { constructor() { super(); this.readyState = 0; } send() {} close() {} }
      WS.OPEN = 1; w.WebSocket = WS;
    } }).window;
}
const $ = (w, id) => w.document.getElementById(id);
function upload(w) {
  const input = $(w, "slides-file");
  const file = new w.File(["x"], "talk.pptx");
  Object.defineProperty(input, "files", { value: [file], configurable: true });
  input.dispatchEvent(new w.Event("change"));
}

(async () => {
  const opened = [];
  let w = boot(); opened.push(w); await flush();
  w.document.querySelector('.tab-btn[data-tab="slides"]').click(); await flush();
  ok(!$(w, "tab-slides").hidden && $(w, "terms-count").textContent === "0", "Slides tab opens with no terms");
  upload(w); await flush();
  const chips = [...w.document.querySelectorAll("#slides-chips input")];
  ok(posts[0][0] === "extract" && posts[0][1] === true, "upload is sent as a file");
  ok(chips.length === 3 && chips[0].checked && chips[1].checked && !chips[2].checked, "suggested terms ticked, others not");
  ok(/Found 3 possible terms/.test($(w, "slides-status").textContent), "status says what was found");
  chips[1].checked = false;   // presenter unticks LSTM
  $(w, "slides-add").click(); await flush();
  ok(JSON.stringify(terms) === '["gradient descent"]' && $(w, "terms-count").textContent === "1", "only ticked terms are added");
  ok($(w, "slides-candidates").hidden && /used from the next sentence/.test($(w, "slides-status").textContent), "confirms they apply immediately");
  ok(/listening for these terms/.test($(w, "slides-foot").textContent), "explains what the terms do");
  $(w, "term-input").value = "backpropagation";
  $(w, "term-input").dispatchEvent(new w.KeyboardEvent("keydown", { key: "Enter" })); await flush();
  ok(terms.indexOf("backpropagation") > -1 && $(w, "term-input").value === "", "typing a term + Enter adds it");
  w.document.querySelector('#terms-chips button[aria-label="Remove gradient descent"]').click(); await flush();
  ok(JSON.stringify(terms) === '["backpropagation"]', "✕ removes a term");

  locked = true;
  w = boot(); opened.push(w); await flush();
  w.document.querySelector('.tab-btn[data-tab="slides"]').click(); await flush();
  ok($(w, "slides-file").disabled && /Pro and Institution/.test($(w, "slides-foot").textContent), "Free plan: upload disabled with a clear reason");

  // notes locks on the Free plan
  notesStatus = Object.assign({}, notesStatus, { plan: "free", full_notes: false });
  w = boot(); opened.push(w); await flush();
  w.document.querySelector('.tab-btn[data-tab="notes"]').click(); await flush();
  const links = [...w.document.querySelectorAll("#notes-list a")];
  const lockedLinks = links.filter((a) => a.classList.contains("locked")).map((a) => a.textContent);
  ok(JSON.stringify(lockedLinks) === JSON.stringify(["Notes (PDF) · Pro", ".srt · Pro", ".md · Pro"]), "Free: transcript open, others marked Pro: " + lockedLinks);
  ok(links.every((a) => a.classList.contains("locked") ? a.target === "_blank" : true), "Pro links open beside the presenter page, not over it");
  ok(!$(w, "notes-plan").hidden, "Free plan explains what Pro adds");
  $(w, "notes-lang").value = "hi"; $(w, "notes-lang").dispatchEvent(new w.Event("change"));
  ok(w.document.querySelector("#notes-list a:nth-child(2)").classList.contains("locked"), "Free: other languages are Pro too");
  opened.forEach((x) => x.close());
})();
