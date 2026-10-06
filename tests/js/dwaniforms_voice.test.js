// DwaniForms kiosk: questions are spoken with a voice of the chosen language, never with a wrong-language voice.
const fs = require("fs"), path = require("path"), { JSDOM } = require("jsdom");
const html = fs.readFileSync(path.join(__dirname, "..", "..", "dwaniforms", "static", "app.html"), "utf8");
const ok = (c, m) => { console.log((c ? "PASS " : "FAIL ") + m); if (!c) process.exitCode = 1; };
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const FORMS = [{ id: "grievance", flow: "grievance", title: "Grievance", titles: { hi: "शिकायत" }, available: true, description: {} }];
function boot(lang, voices) {
  const spoken = [];
  const w = new JSDOM(html, { url: "http://127.0.0.1:8100/form/", runScripts: "dangerously", pretendToBeVisual: true,
    beforeParse(w) {
      w.localStorage.setItem("dwaniforms:lang", lang);
      w.speechSynthesis = { cancel() {}, speak(u) { spoken.push(u); }, getVoices: () => voices, onvoiceschanged: null };
      w.SpeechSynthesisUtterance = function (t) { this.text = t; };
      w.fetch = (u, opt) => {
        let body = {};
        if (u.endsWith("/forms")) body = FORMS;
        else if (u.endsWith("/capabilities")) body = { asr: false, asr_langs: [], translation: true };
        else if (u.endsWith("/sessions") && opt && opt.method === "POST")
          body = { session_id: "abc", text: lang === "hi" ? "अपना पूरा नाम बताइए।" : "What is your name?", phase: "ask", field_id: "full_name",
                   title: "शिकायत", fields: [{ id: "full_name", label: "Name", kind: "name", value: "", native: "", required: true }] };
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
      };
    } }).window;
  w.__spoken = spoken;
  return w;
}
async function open(w) {
  for (let i = 0; i < 50 && !w.document.querySelector('[data-form="grievance"]'); i++) await wait(20);
  w.document.querySelector('[data-form="grievance"]').click(); await wait(80);
}
(async () => {
  const HI = { name: "Lekha", lang: "hi-IN", localService: true }, EN = { name: "Samantha", lang: "en-US", localService: true };
  let w = boot("hi", [EN, HI]);
  await open(w);
  const u = w.__spoken[w.__spoken.length - 1];
  ok(u && u.voice === HI && u.lang === "hi-IN" && /पूरा नाम/.test(u.text), "Hindi question spoken with the Hindi voice");
  ok(w.document.getElementById("voice-note").hidden, "no warning when the voice exists");
  w.close();
  w = boot("ta", [EN, HI]);
  await open(w);
  ok(w.__spoken.length === 0, "no Tamil voice: nothing read in a wrong-language voice");
  const note = w.document.getElementById("voice-note");
  ok(!note.hidden && /தமிழ்/.test(note.textContent) && /Settings/.test(note.textContent), "operator told which voice to install: " + note.textContent.slice(0, 60));
  w.close();
  w = boot("hi", []);                      // voices not loaded yet: let the browser choose rather than stay silent
  await open(w);
  ok(w.__spoken.length === 1 && w.__spoken[0].lang === "hi", "voices still loading: speaks with the language tag");
  w.close();
})();
