// Home page (static/pricing.html) nav highlighting: the section you're looking at
// is highlighted. Layout is simulated: Pricing/FAQ sit inside a position:relative
// <main>, so their offsetTop is small -- the bug that highlighted Pricing while
// reading How it works.
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");
const FakeTimers = require("@sinonjs/fake-timers");
const html = fs.readFileSync(path.join(__dirname, "..", "..", "static", "pricing.html"), "utf8");
const ok = (c, m) => { console.log((c ? "PASS " : "FAIL ") + m); if (!c) process.exitCode = 1; };

const ABS = { "how-it-works": 700, plans: 1400, compare: 2000, faq: 2600 };   // page positions (px)
const DOC_H = 3400, VIEW_H = 800, HEADER_H = 64;
let clock;
const dom = new JSDOM(html, {
  url: "http://localhost/", runScripts: "dangerously", pretendToBeVisual: true,
  beforeParse(w) {
    clock = FakeTimers.withGlobal(w).install({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    w.fetch = () => Promise.resolve({ ok: false, status: 401, json: () => Promise.resolve({}) });
    Object.defineProperty(w, "innerHeight", { value: VIEW_H, configurable: true });
    let y = 0;
    Object.defineProperty(w, "scrollY", { get: () => y, configurable: true });
    w.__scrollTo = (v) => { y = v; w.dispatchEvent(new w.Event("scroll")); };
    w.Element.prototype.getBoundingClientRect = function () {
      if (this.classList && this.classList.contains("site-header")) return { top: 0, bottom: HEADER_H, height: HEADER_H };
      const top = this.id in ABS ? ABS[this.id] - y : 99999;
      return { top, bottom: top + 500, height: 500 };
    };
    // what offsetTop reports for elements inside <main class="wrap" style="position:relative">
    Object.defineProperty(w.HTMLElement.prototype, "offsetTop", { get() { return { plans: 60, compare: 660, faq: 1260 }[this.id] ?? ABS[this.id] ?? 0; } });
    Object.defineProperty(w.document.documentElement || w.HTMLHtmlElement.prototype, "scrollHeight", { value: DOC_H, configurable: true });
  },
});
const w = dom.window;
Object.defineProperty(w.document.documentElement, "scrollHeight", { value: DOC_H, configurable: true });
const active = () => [...w.document.querySelectorAll("#nav-links a.active")].map((a) => a.textContent.trim());

w.__scrollTo(0);
ok(active().length === 0, "top of page (hero): nothing highlighted: " + active());
w.__scrollTo(700 - HEADER_H - 20);
ok(JSON.stringify(active()) === '["How it works"]', "reading How it works highlights How it works (not Pricing): " + active());
w.__scrollTo(1100);
ok(JSON.stringify(active()) === '["How it works"]', "still How it works while it fills the screen: " + active());
w.__scrollTo(1400 - HEADER_H);
ok(JSON.stringify(active()) === '["Pricing"]', "reaching the plans highlights Pricing: " + active());
w.__scrollTo(2600 - HEADER_H);
ok(JSON.stringify(active()) === '["FAQ"]', "FAQ section highlights FAQ: " + active());
w.__scrollTo(DOC_H - VIEW_H);
ok(JSON.stringify(active()) === '["FAQ"]', "bottom of page: FAQ: " + active());

// clicking a nav link highlights it immediately and doesn't flicker during smooth scroll
w.__scrollTo(0);
w.document.querySelector('#nav-links a[data-section="faq"]').click();
ok(JSON.stringify(active()) === '["FAQ"]', "click FAQ: highlighted at once");
w.__scrollTo(1500);   // smooth scroll passing through Pricing
ok(JSON.stringify(active()) === '["FAQ"]', "no flicker to Pricing while scrolling past it");
w.__scrollTo(2600 - HEADER_H); clock.tick(1000);
ok(JSON.stringify(active()) === '["FAQ"]', "settles on FAQ after the scroll");
