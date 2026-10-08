// DwaniForms as a phone app, driven in jsdom against a REAL running server:
// the page can be installed, and a reload in the same tab picks the half-filled form back up.
// Run: DWANIFORMS_URL=http://127.0.0.1:8100 node dwaniforms_pwa.test.js
const fs = require("fs"), path = require("path"), { JSDOM } = require("jsdom");
const BASE = process.env.DWANIFORMS_URL || "http://127.0.0.1:8100";
const html = fs.readFileSync(path.join(__dirname, "..", "..", "dwaniforms", "static", "app.html"), "utf8");
const ok = (c, m) => { console.log((c ? "PASS " : "FAIL ") + m); if (!c) process.exitCode = 1; };
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, ms = 4000) { const end = Date.now() + ms; while (Date.now() < end) { if (fn()) return true; await wait(40); } return false; }
function boot(sid) {
  const w = new JSDOM(html, { url: BASE + "/form/", runScripts: "dangerously", pretendToBeVisual: true,
    beforeParse(w) {
      w.sessionStorage.setItem("dwaniforms:consent", "v1");
      if (sid) w.sessionStorage.setItem("dwaniforms:sid", sid);        // what survives a reload in the same tab
      w.fetch = (u, opt) => fetch(new URL(u, BASE + "/form/").href, opt);
      w.speechSynthesis = { cancel() {}, speak() {} };
      w.SpeechSynthesisUtterance = function (t) { this.text = t; };
      w.localStorage.setItem("dwaniforms:lang", "hi");
    } }).window;
  return w;
}
const $ = (w, id) => w.document.getElementById(id);
const q = (w, s) => w.document.querySelector(s);
async function say(w, text) {
  const before = $(w, "question").textContent;
  $(w, "type-input").value = text;
  $(w, "type-form").dispatchEvent(new w.Event("submit", { cancelable: true }));
  await until(() => $(w, "question").textContent !== before || !$(w, "result").hidden);
}

(async () => {
  // --- the page tells a phone it can be installed
  let w = boot();
  await until(() => q(w, '[data-form="grievance"]'));
  const man = q(w, 'link[rel="manifest"]');
  ok(man && man.getAttribute("href") === "manifest.webmanifest", "home screen: the page links its manifest");
  ok(!!q(w, 'link[rel="apple-touch-icon"]'), "an icon for iPhones too");
  ok($(w, "install-btn").hidden, "the 'Add to phone' button stays hidden until the browser offers an install");
  const m = await (await fetch(BASE + "/form/manifest.webmanifest")).json();
  ok(m.display === "standalone" && m.icons.length >= 3, "manifest served by the app server: " + m.short_name);

  // --- fill one answer, then "reload the tab"
  q(w, '[data-form="grievance"]').click();
  await until(() => !$(w, "talk").hidden && $(w, "question").textContent);
  await say(w, "सुनीता देवी");
  $(w, "btn-yes").click();
  await until(() => /मोबाइल/.test($(w, "question").textContent));
  const sid = w.sessionStorage.getItem("dwaniforms:sid");
  ok(!!sid, "the session id is kept for this tab");
  ok(!w.localStorage.getItem("dwaniforms:sid"), "and not in localStorage, which would outlive the tab");
  w.close();

  w = boot(sid);                                     // same tab, page loaded again
  ok(await until(() => !$(w, "talk").hidden && /मोबाइल/.test($(w, "question").textContent), 6000),
     "after a reload the same form comes back, on the same question");
  const row = q(w, '#sheet-rows li[data-id="full_name"]');
  ok(row && /सुनीता देवी/.test(row.textContent) && row.classList.contains("filled"),
     "the answer already given is still on the form sheet");
  ok(/शिकायत/.test($(w, "crumb-title").textContent), "with the form's name back in the breadcrumb");
  w.close();

  // --- a session the server has forgotten must not strand the citizen on a blank screen
  w = boot("0123456789abcdef");
  ok(await until(() => !$(w, "home").hidden && q(w, '[data-form="grievance"]'), 6000),
     "an expired session just shows the home screen again");
  w.close();
})();
