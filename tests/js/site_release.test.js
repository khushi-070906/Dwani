// Website: home page shows the latest release from /api/release; /changelog lists releases.
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");
const read = (f) => fs.readFileSync(path.join(__dirname, "..", "..", "static", f), "utf8");
const ok = (c, m) => { console.log((c ? "PASS " : "FAIL ") + m); if (!c) process.exitCode = 1; };
const flush = () => new Promise((r) => setTimeout(r, 30));
const REL = [{ version: "1.8.1", published_at: "2026-10-02T09:00:00Z", setup_url: "https://x/v1.8.1/DwaniLive-Setup.exe",
               zip_url: "https://x/v1.8.1/DwaniLive-win64.zip", notes: "## What's new\n- Phones see Session full\n- Tests on every push\n- Bump version to 1.8.1" },
             { version: "1.6.0", published_at: "2026-10-01T09:00:00Z", setup_url: null, notes: "**Full Changelog**: https://..." },
             { version: "0.9.0", published_at: "2026-09-01T09:00:00Z", setup_url: null, notes: "" }];

function page(file, api) {
  return new JSDOM(read(file), { url: "http://localhost/", runScripts: "dangerously", pretendToBeVisual: true,
    beforeParse(w) {
      w.fetch = (u) => {
        if (u === "/api/release" || u === "/api/releases") {
          if (api === "down") return Promise.resolve({ ok: false, status: 503, json: () => Promise.resolve({}) });
          return Promise.resolve({ ok: true, json: () => Promise.resolve(u === "/api/release" ? REL[0] : REL) });
        }
        return Promise.resolve({ ok: false, status: 401, json: () => Promise.resolve({}) });
      };
    } }).window;
}

(async () => {
  let w = page("pricing.html"); await flush();
  const line = w.document.getElementById("release-line");
  ok(/^Latest: v1\.8\.1 · /.test(line.textContent) && line.querySelector('a[href="/changelog"]'), "home: 'Latest: v1.8.1 · date · What's new': " + line.textContent);
  ok(w.document.getElementById("dl-setup").href === REL[0].setup_url, "home: download button points at that exact release");
  ok(w.document.querySelectorAll("#features .feature").length === 8, "home: 8 'included in every plan' features");
  ok([...w.document.querySelectorAll('a[href="/careers/"]')].length >= 1, "home: Careers link points at /careers/");

  w = page("pricing.html", "down"); await flush();
  ok(w.document.getElementById("release-line").textContent === "" &&
     /releases\/latest\/download\/DwaniLive-Setup\.exe$/.test(w.document.getElementById("dl-setup").href), "home: API down -> still downloads latest, no broken text");

  w = page("changelog.html"); await flush();
  const rel = [...w.document.querySelectorAll(".release")];
  ok(rel.length === 3 && rel[0].classList.contains("latest") && /Latest/.test(rel[0].textContent), "changelog: newest first, marked Latest");
  const first = [...rel[0].querySelectorAll("li")].map((li) => li.textContent);
  ok(JSON.stringify(first) === JSON.stringify(["Phones see Session full", "Tests on every push"]),
     "changelog: notes from commit messages: " + first.join(" | "));
  ok(/dark mode/.test(rel[1].textContent), "changelog: older release without notes falls back to written highlights");
  ok(/Maintenance/.test(rel[2].textContent), "changelog: unknown old version gets a neutral line");
  ok(w.document.getElementById("dl").href === REL[0].setup_url, "changelog: download button = latest release");

  w = page("changelog.html", "down"); await flush();
  ok(/Couldn.t load/.test(w.document.getElementById("list").textContent), "changelog: friendly message if the API is down");
})();
