// The two pages a real pilot needs: privacy and terms of use. Both must say the same things in Hindi and English,
// name a person to complain to, and be reachable from every other page.
const fs = require("fs"), path = require("path"), { JSDOM } = require("jsdom");
const SITE = path.join(__dirname, "..", "..", "dwaniforms_site");
const ok = (c, m) => { console.log((c ? "PASS " : "FAIL ") + m); if (!c) process.exitCode = 1; };
const read = (f) => fs.readFileSync(path.join(SITE, f), "utf8");
const dom = (f) => new JSDOM(read(f), { url: "https://dwaniforms.example/" + f }).window;
const text = (w, sel) => (w.document.querySelector(sel) || { textContent: "" }).textContent.replace(/\s+/g, " ");

const privacy = dom("privacy.html"), terms = dom("terms.html");

for (const [name, w] of [["privacy", privacy], ["terms", terms]]) {
  const en = text(w, "main > .en"), hi = text(w, "main > .hi");
  ok(en.length > 800 && hi.length > 800, name + ": written in both English and Hindi");
  ok(!!w.document.getElementById("lang"), name + ": has the language switch");
  ok(/Not a government website/.test(text(w, "footer")), name + ": footer says this is not a government site");
}

// --- who a citizen complains to (DPDP Act 2023 expects a named person, not just an address)
const pEn = text(privacy, "main > .en"), pHi = text(privacy, "main > .hi");
ok(/Grievance officer:\s*\S+/.test(pEn) && /शिकायत अधिकारी:\s*\S+/.test(pHi), "privacy: a grievance officer is named, in both languages");
ok(/30 days/.test(pEn) && /30 दिन/.test(pHi), "privacy: says how soon they will reply");

// --- what the browser keeps, now that the app can be installed on a phone
ok(/not among them/i.test(pEn) && /जवाब इनमें नहीं होते/.test(pHi), "privacy: says answers are NOT stored on the phone");
ok(/never uploaded/i.test(pEn) && /न अपलोड करते हैं/.test(pHi), "privacy: says a photographed paper is read on the phone and never uploaded");
ok(/Aadhaar card, ration card or khasra copy/i.test(pEn) && /on your phone only/i.test(pEn) && /only its last four digits/i.test(pEn) && /आधार कार्ड, राशन कार्ड या खसरे/.test(pHi) && /आख़िरी चार अंक/.test(pHi),
   "privacy: a photo of an Aadhaar / ration / khasra paper is read on the phone; only the values found are sent, confirmed one by one, Aadhaar shown masked");
ok(/do not ask for camera permission/i.test(pEn) && /कैमरे की अनुमति नहीं माँगते/.test(pHi), "privacy: no camera permission is asked");
ok(/off unless you see an "Ask the AI" box/i.test(pEn) && /तभी चालू है जब/.test(pHi), "privacy: the AI question is off unless the box is shown");
ok(/Every question asks again/i.test(pEn) && /हर सवाल पर दोबारा पूछा जाता है/.test(pHi), "privacy: consent is asked for every question");
ok(/Not<\/strong> the photo/i.test(privacy.document.querySelector("main > .en").innerHTML) && /नहीं<\/strong> जाता: फ़ोटो/.test(privacy.document.querySelector("main > .hi").innerHTML), "privacy: says the photo, amount and name never go to the AI");
ok(/Anthropic/.test(pEn) && /Anthropic/.test(pHi) && /do not store your question/i.test(pEn), "privacy: names who answers and says nothing is stored");
ok(/one tab/.test(pEn) && /एक टैब/.test(pHi), "privacy: explains the session id kept for one tab");

// --- the promises terms of use must make plainly
const tEn = text(terms, "main > .en"), tHi = text(terms, "main > .hi");
ok(/not a government service/i.test(tEn) && /सरकारी सेवा नहीं है/.test(tHi), "terms: not a government service");
ok(/does not submit anything/i.test(tEn) && /कहीं कुछ जमा नहीं करता/.test(tHi), "terms: it submits nothing anywhere");
ok(/not.{0,40}verify it with UIDAI/i.test(tEn) && /UIDAI/.test(tHi), "terms: no Aadhaar verification or e-KYC");
ok(/department can decide/i.test(tEn) && /फ़ैसला सिर्फ़ विभाग करता है/.test(tHi), "terms: the department decides eligibility, not us");
ok(/cannot be given up by agreement/i.test(tEn) && /छीना नहीं जा सकता/.test(tHi), "terms: does not claim to remove rights Indian law gives");

// --- every page can be reached from every other page
const links = (f) => [...dom(f).document.querySelectorAll("a")].map((a) => a.getAttribute("href"));
for (const f of ["index.html", "privacy.html", "terms.html"]) {
  const l = links(f);
  for (const target of ["index.html", "privacy.html", "terms.html"].filter((x) => x !== f)) {
    ok(l.includes(target), f + " links to " + target);
  }
}
for (const f of ["privacy.html", "terms.html"]) {
  for (const href of links(f).filter((h) => h && !/^(https?:|mailto:|#|data:)/.test(h))) {
    ok(fs.existsSync(path.join(SITE, href)), f + ": " + href + " exists");
  }
}
