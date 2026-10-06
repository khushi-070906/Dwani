// DwaniForms website: the real-life scenario player (timers sped up).
const fs = require("fs"), path = require("path"), { JSDOM } = require("jsdom");
const html = fs.readFileSync(path.join(__dirname, "..", "..", "dwaniforms_site", "index.html"), "utf8");
const ok = (c, m) => { console.log((c ? "PASS " : "FAIL ") + m); if (!c) process.exitCode = 1; };
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
function boot(lang) {
  return new JSDOM(html, { url: "https://dwaniforms.example/", runScripts: "dangerously", pretendToBeVisual: true,
    beforeParse(w) {
      const st = w.setTimeout.bind(w), si = w.setInterval.bind(w);
      w.setTimeout = (f, ms) => st(f, (ms || 0) >= 5000 ? ms : 3);   // keep the 6 s pause between stories real
      w.setInterval = (f, ms) => si(f, 1);
      if (lang) w.localStorage.setItem("dwaniforms-site:lang", lang);
    } }).window;
}
const $ = (w, s) => w.document.querySelector(s);
(async () => {
  let w = boot("en");
  await wait(1500);
  const rows = [...w.document.querySelectorAll("#paper .row .k")].map((k) => k.textContent);
  ok(rows.includes("Applicant name") && rows.includes("Aadhaar") && rows.includes("Land (acres)") && rows.includes("IFSC"), "farmer story fills the paper form: " + rows.join(", "));
  ok(/2\.5/.test($(w, "#paper").textContent) && /•••• •••• 4821/.test($(w, "#paper").textContent), "2.5 acres; Aadhaar masked on paper");
  ok(w.document.querySelectorAll("#chat .bubble.me").length >= 8, "citizen's spoken answers appear in the chat");
  ok(/no problem/.test($(w, "#chat").textContent), "English mode shows translations under Hindi speech");
  ok($(w, "#stamp").classList.contains("on"), "'ready to print' stamp lands at the end");
  w.document.querySelector('.person[data-story="complaint"]').click();
  await wait(1500);
  ok(w.document.querySelectorAll("#paper .chip").length === 3, "complaint: department, place and date picked out as chips");
  ok(/सेवा में/.test($(w, "#paper .letter-body").textContent) && $(w, '.person[data-story="complaint"]').getAttribute("aria-pressed") === "true", "complaint letter is written out");
  w.document.querySelector('.person[data-story="schemes"]').click();
  await wait(1500);
  ok(w.document.querySelectorAll("#paper .scheme").length === 5 && w.document.querySelectorAll("#paper .scheme.maybe").length === 1, "schemes story: 4 likely + 1 maybe");
  $(w, "#play").click();
  const n = w.document.querySelectorAll("#chat .bubble").length;
  $(w, "#restart").click(); await wait(200);
  ok(w.document.querySelectorAll("#chat .bubble").length === 0, "paused: restart clears and waits");
  $(w, "#lang").click();
  ok(w.document.body.dataset.lang === "hi" && $(w, "#lang").textContent === "English", "language switch");
  ok(!/Government of India/i.test(html) && /not a government website/i.test($(w, ".disclaimer").textContent + html), "no 'Government of India'; disclaimer present");
  ok($(w, 'footer a[href="privacy.html"]') !== null, "footer links to DwaniForms' own privacy page");
  w.close();
  const priv = fs.readFileSync(path.join(__dirname, "..", "..", "dwaniforms_site", "privacy.html"), "utf8");
  ok(/Digital Personal Data Protection Act, 2023/.test(priv) && /डिजिटल व्यक्तिगत डेटा संरक्षण अधिनियम, 2023/.test(priv), "privacy policy (English + Hindi) covers the DPDP Act");
  ok(/30 minutes/.test(priv) && /contains these numbers in full/.test(priv), "policy states session deletion and the full-number portal export honestly");
})();
