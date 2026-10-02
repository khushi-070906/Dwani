// Troubleshooting guide (static/help.html): search filtering and deep links.
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");
const html = fs.readFileSync(path.join(__dirname, "..", "..", "static", "help.html"), "utf8");
const ok = (c, m) => { console.log((c ? "PASS " : "FAIL ") + m); if (!c) process.exitCode = 1; };
const boot = (u) => new JSDOM(html, { url: "http://localhost:8000/help" + u, runScripts: "dangerously" }).window;
function search(w, text) { const q = w.document.getElementById("q"); q.value = text; q.dispatchEvent(new w.Event("input")); }
const visible = (w) => [...w.document.querySelectorAll("details")].filter((d) => !d.hidden).map((d) => d.id);

let w = boot("");
ok(visible(w).length >= 20, "all " + visible(w).length + " problems listed by default");
search(w, "firewall");
ok(visible(w).includes("firewall") && w.document.getElementById("firewall").open, "search 'firewall' finds and opens it");
ok(!visible(w).includes("activation"), "unrelated sections hidden");
ok(w.document.querySelector('a[href="#sec-licence"]') && w.document.getElementById("sec-licence").hidden, "empty section headings hidden");
search(w, "hotel wifi");
ok(visible(w).includes("phones"), "multi-word search ('hotel wifi') finds the phones section");
search(w, "wi fi"); ok(visible(w).includes("phones"), "'wi fi' also matches Wi-Fi");
search(w, "zzqx nothing");
ok(visible(w).length === 0 && !w.document.getElementById("empty").hidden, "no match: friendly 'nothing matched' message");
search(w, "");
ok(visible(w).length >= 20 && w.document.getElementById("empty").hidden, "clearing search shows everything again");

w = boot("#mic-silent");
ok(w.document.getElementById("mic-silent").open, "deep link /help#mic-silent opens that answer");
w = boot("?q=hotspot");
ok(w.document.getElementById("q").value === "hotspot" && visible(w).includes("hotspot"), "?q= pre-fills the search");
