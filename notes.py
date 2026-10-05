"""
notes.py -- lecture notes from a DwaniLive session.

During a session every FINAL caption (the presenter's words + every
translation that was produced live) is appended to a JSON-lines file on the
presenter's laptop: %LOCALAPPDATA%\\DwaniLive\\sessions\\<date>_<session>.jsonl.
Appending line by line means a crash or power cut loses at most the last
caption. Nothing leaves the laptop.

Afterwards the presenter exports, in any language (missing translations are
produced on demand with the app's own NLLB model):

  * notes   -- printable HTML: highlights, key terms, full transcript. Printed
               to PDF by the browser, which shapes Hindi/Tamil/Urdu correctly.
  * txt     -- timestamped transcript
  * srt     -- subtitles, timed from the start of the session
  * md      -- Markdown version of the notes

"Highlights" are EXTRACTIVE: the most central sentences of the talk, picked
offline with TF-IDF centrality, in their original order. They are the
speaker's own words, not a rewritten summary.
"""

from __future__ import annotations

import html
import json
import math
import re
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

LANG_NAMES = {"en": "English", "hi": "हिन्दी", "pa": "ਪੰਜਾਬੀ", "bn": "বাংলা", "ta": "தமிழ்", "te": "తెలుగు",
              "mr": "मराठी", "ur": "اردو", "gu": "ગુજરાતી", "kn": "ಕನ್ನಡ", "ml": "മലയാളം", "or": "ଓଡ଼ିଆ",
              "as": "অসমীয়া", "ne": "नेपाली"}
RTL = {"ur", "ar", "fa", "he"}

_STOP_EN = set("""a an and are as at be been being but by can could did do does doing for from had has have having he her
here him his how i if in into is it its just me more most my no not of on or our out over she so some such than that the
their them then there these they this those through to too up us very was we were what when where which while who why will
with would you your yes okay ok also like going go get got one two let lets now know think really right well thing things
actually basically so-called today want see say said make made each every small large big little few many much good
bad new first last next time way example examples break class lecture talk question questions part kind lot sure""".split())
_STOP_HI = set("""का की के को में से पर और है हैं था थी थे यह वह ये वो इस उस एक भी तो ही कि जो कर करना करते किया
हम आप मैं तुम नहीं हो होता होती होते रहा रही रहे गया गई गए अब जब तक लिए साथ बहुत कुछ कोई सब अपने अपना अपनी""".split())
STOP = _STOP_EN | _STOP_HI
_WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)?", re.UNICODE)


# ---------------------------------------------------------------------------
# recording
# ---------------------------------------------------------------------------

@dataclass
class Caption:
    t: float                 # wall-clock time the caption was produced
    start: float             # seconds from session start when the sentence began
    end: float               # ... and ended
    src: str                 # presenter's words
    tr: dict = field(default_factory=dict)  # {lang: translation}
    src_lang: str = ""       # language spoken for this caption (presenter can switch mid-talk)


class NotesRecorder:
    """Appends final captions to <dir>/<YYYY-MM-DD_HHMM>_<session>.jsonl."""

    def __init__(self, directory: Path, session_id: str, src_lang: str, title: str = ""):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.session_id = session_id
        self.src_lang = src_lang
        self.started = time.time()
        self.enabled = True
        self.count = 0
        self.path = self.dir / f"{time.strftime('%Y-%m-%d_%H%M', time.localtime(self.started))}_{session_id}.jsonl"
        self._lock = threading.Lock()
        self._write({"type": "meta", "session": session_id, "src_lang": src_lang, "started": self.started,
                     "title": title or f"Session {session_id.upper()}"})

    def _write(self, obj: dict) -> None:
        with self._lock, open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    def record(self, transcript: str, translations: dict, end_wall: float, speech_seconds: float,
               src_lang: str | None = None) -> None:
        text = (transcript or "").strip()
        if not self.enabled or not text:
            return
        spoken = src_lang or self.src_lang
        end = max(0.0, end_wall - self.started)
        start = max(0.0, end - max(speech_seconds, 0.5))
        line = {"type": "cap", "t": round(end_wall, 3), "start": round(start, 2), "end": round(end, 2),
                "src": text, "tr": {k: v for k, v in (translations or {}).items() if v and k != spoken}}
        if spoken != self.src_lang:
            line["sl"] = spoken
        self._write(line)
        self.count += 1

    def mark_confused(self, wall: float, lang: str = "") -> None:
        """An attendee tapped 'Lost me' (anonymous)."""
        if self.enabled:
            self._write({"type": "confused", "t": round(wall, 3), "lang": lang})


@dataclass
class Session:
    id: str                  # file stem, used in URLs
    path: Path
    session: str
    src_lang: str
    title: str
    started: float
    captions: list[Caption]
    confused: list = field(default_factory=list)   # wall times of anonymous "Lost me" taps

    @property
    def duration_s(self) -> float:
        return self.captions[-1].end if self.captions else 0.0

    def languages(self) -> list[str]:
        langs = {self.src_lang}
        for c in self.captions:
            langs.update(c.tr)
            if c.src_lang:
                langs.add(c.src_lang)
        return sorted(langs)


def load_session(path: Path) -> Session:
    meta, caps, confused = {}, [], []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                o = json.loads(line)
            except ValueError:
                continue  # a half-written last line after a crash
            if o.get("type") == "meta":
                meta = o
            elif o.get("type") == "cap":
                caps.append(Caption(o["t"], o["start"], o["end"], o["src"], o.get("tr") or {},
                                    o.get("sl") or meta.get("src_lang", "en")))
            elif o.get("type") == "tr" and 0 <= o.get("i", -1) < len(caps):   # translation added after the talk
                caps[o["i"]].tr[o["lang"]] = o["text"]
            elif o.get("type") == "confused":
                confused.append(float(o.get("t", 0)))
    return Session(path.stem, path, meta.get("session", ""), meta.get("src_lang", "en"),
                   meta.get("title", path.stem), meta.get("started", path.stat().st_mtime), caps, confused)


def list_sessions(directory: Path) -> list[dict]:
    out = []
    for p in sorted(Path(directory).glob("*.jsonl"), reverse=True):
        try:
            s = load_session(p)
        except OSError:
            continue
        if not s.captions:
            continue
        out.append({"id": s.id, "title": s.title, "started": s.started, "captions": len(s.captions),
                    "minutes": round(s.duration_s / 60, 1), "src_lang": s.src_lang, "languages": s.languages()})
    return out


def safe_session_path(directory: Path, session_id: str) -> Path | None:
    if not re.fullmatch(r"[\w-]{1,80}", session_id or ""):
        return None
    p = Path(directory) / f"{session_id}.jsonl"
    return p if p.is_file() else None


async def ensure_language(session: Session, lang: str, translate) -> int:
    """Fill in translations missing for `lang` and persist them, so the next
    export is instant. `translate(text, lang, source_lang)` (preferred: each
    caption is translated from the language it was actually spoken in) or
    the older `translate(text, lang)`."""
    import inspect

    try:
        three = len(inspect.signature(translate).parameters) >= 3
    except (TypeError, ValueError):
        three = False
    todo = [(i, c) for i, c in enumerate(session.captions)
            if lang != (c.src_lang or session.src_lang) and lang not in c.tr]
    for i, c in todo:
        src = c.src_lang or session.src_lang
        c.tr[lang] = (await (translate(c.src, lang, src) if three else translate(c.src, lang))) or ""
        with open(session.path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "tr", "i": i, "lang": lang, "text": c.tr[lang]}, ensure_ascii=False) + "\n")
    return len(todo)


# ---------------------------------------------------------------------------
# highlights & key terms (offline, extractive)
# ---------------------------------------------------------------------------

def _tokens(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text)]


def _content(tokens: list[str]) -> list[str]:
    return [t for t in tokens if t not in STOP and len(t) > 2]


def highlights(captions: list[Caption], k: int | None = None) -> list[int]:
    """Indices of the most central captions (TF-IDF cosine to the whole talk),
    returned in talk order. Very short captions ("okay", "yes") never qualify."""
    docs = [_content(_tokens(c.src)) for c in captions]
    eligible = [i for i, d in enumerate(docs) if len(d) >= 4]
    if not eligible:
        return []
    if k is None:
        k = max(3, min(10, round(len(captions) / 12)))
    df = Counter(t for d in docs for t in set(d))
    n = len(docs)
    idf = {t: math.log((1 + n) / (1 + c)) + 1 for t, c in df.items()}

    def vec(d):
        tf = Counter(d)
        return {t: (v / len(d)) * idf[t] for t, v in tf.items()} if d else {}

    centroid = Counter()
    for d in docs:
        for t, v in vec(d).items():
            centroid[t] += v

    def cos(a, b):
        num = sum(v * b.get(t, 0.0) for t, v in a.items())
        den = math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values()))
        return num / den if den else 0.0

    ranked = sorted(eligible, key=lambda i: cos(vec(docs[i]), centroid), reverse=True)
    chosen: list[int] = []
    for i in ranked:   # skip near-duplicates of something already chosen
        if all(cos(vec(docs[i]), vec(docs[j])) < 0.7 for j in chosen):
            chosen.append(i)
        if len(chosen) >= k:
            break
    return sorted(chosen)


def key_terms(captions: list[Caption], k: int = 12) -> list[str]:
    """Recurring multi-word and single-word terms (e.g. 'gradient descent'),
    weighted by how often they appear and in how many different sentences."""
    phrase_sent: Counter = Counter()
    phrase_tot: Counter = Counter()
    for c in captions:
        toks = _tokens(c.src)
        seen = set()
        for n in (3, 2, 1):
            for i in range(len(toks) - n + 1):
                g = toks[i:i + n]
                if g[0] in STOP or g[-1] in STOP or any(len(w) <= 2 for w in g):
                    continue
                if n == 1 and len(g[0]) < 4:
                    continue
                p = " ".join(g)
                phrase_tot[p] += 1
                if p not in seen:
                    phrase_sent[p] += 1
                    seen.add(p)
    # a single word must recur in 3+ sentences to count; a 2-3 word phrase in 2+
    scored = {p: phrase_sent[p] * (1 + 0.6 * (len(p.split()) - 1)) for p in phrase_tot
              if phrase_sent[p] >= (3 if " " not in p else 2)}
    out: list[str] = []
    for p in sorted(scored, key=lambda x: (-scored[x], x)):
        if any(p in q or q in p for q in out):   # 'descent' adds nothing next to 'gradient descent'
            continue
        out.append(p)
        if len(out) >= k:
            break
    return out


# ---------------------------------------------------------------------------
# exporters
# ---------------------------------------------------------------------------

def _clock(s: float) -> str:
    s = int(s)
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"


def _srt_time(s: float) -> str:
    ms = int(round(s * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def text_of(c: Caption, lang: str, src_lang: str) -> str:
    return c.src if lang == (c.src_lang or src_lang) else c.tr.get(lang, "")


def export_txt(s: Session, lang: str) -> str:
    head = f"{s.title}\n{time.strftime('%d %B %Y, %H:%M', time.localtime(s.started))} · {LANG_NAMES.get(lang, lang)}\n\n"
    return head + "\n".join(f"[{_clock(c.start)}] {text_of(c, lang, s.src_lang)}" for c in s.captions
                            if text_of(c, lang, s.src_lang)) + "\n"


def export_srt(s: Session, lang: str) -> str:
    out, n = [], 0
    for i, c in enumerate(s.captions):
        t = text_of(c, lang, s.src_lang)
        if not t:
            continue
        nxt = s.captions[i + 1].start if i + 1 < len(s.captions) else c.end + 2
        end = max(c.start + 1.0, min(c.end + 1.5, nxt - 0.05))   # readable minimum, never overlaps the next cue
        n += 1
        out.append(f"{n}\n{_srt_time(c.start)} --> {_srt_time(end)}\n{t}\n")
    return "\n".join(out)


def lost_moments(s: Session, min_taps: int = 1) -> list[tuple[Caption, int]]:
    """Captions during which attendees tapped 'Lost me': each tap is attributed to
    the sentence being spoken (or just finished) at that moment. Busiest first
    would hide the flow of the talk, so they're returned in talk order."""
    if not s.captions or not s.confused:
        return []
    ends = [c.t for c in s.captions]
    counts: dict[int, int] = {}
    for t in s.confused:
        # the first caption that finished at/after the tap; a tap after the last caption -> the last one
        i = next((k for k, e in enumerate(ends) if e >= t - 2.0), len(ends) - 1)
        counts[i] = counts.get(i, 0) + 1
    return [(s.captions[i], n) for i, n in sorted(counts.items()) if n >= min_taps]


def _notes_parts(s: Session, lang: str) -> tuple[list[Caption], list[str]]:
    hl = [s.captions[i] for i in highlights(s.captions)]
    return hl, key_terms(s.captions)


def export_md(s: Session, lang: str, term_translations: dict | None = None) -> str:
    hl, terms = _notes_parts(s, lang)
    L = [f"# {s.title}", "", f"{time.strftime('%d %B %Y, %H:%M', time.localtime(s.started))} · "
         f"{_clock(s.duration_s)} · {LANG_NAMES.get(lang, lang)}", ""]
    if hl:
        L += ["## Highlights", ""] + [f"- **[{_clock(c.start)}]** {text_of(c, lang, s.src_lang)}" for c in hl] + [""]
    if terms:
        tt = term_translations or {}
        L += ["## Key terms", ""] + [f"- {t}" + (f" — {tt[t]}" if tt.get(t) and lang != s.src_lang else "") for t in terms] + [""]
    lost = lost_moments(s)
    if lost:
        L += ["## Where students got lost", ""] + [
            f"- **[{_clock(c.start)}]** {text_of(c, lang, s.src_lang)} _({n} tap{'s' if n != 1 else ''})_" for c, n in lost] + [""]
    L += ["## Transcript", ""] + [f"**[{_clock(c.start)}]** {text_of(c, lang, s.src_lang)}  " for c in s.captions
                                  if text_of(c, lang, s.src_lang)]
    return "\n".join(L) + "\n"


def export_html(s: Session, lang: str, term_translations: dict | None = None) -> str:
    hl, terms = _notes_parts(s, lang)
    e = html.escape
    d = "rtl" if lang in RTL else "ltr"
    tt = term_translations or {}
    hl_html = "".join(f'<li><span class="ts">{_clock(c.start)}</span> {e(text_of(c, lang, s.src_lang))}</li>' for c in hl)
    terms_html = "".join(
        f'<li><b lang="{s.src_lang}">{e(t)}</b>' + (f' <span lang="{lang}">— {e(tt[t])}</span>' if tt.get(t) and lang != s.src_lang else "")
        + "</li>" for t in terms)
    lost_html = "".join(f'<li><span class="ts">{_clock(c.start)}</span> {e(text_of(c, lang, s.src_lang))} '
                        f'<span class="taps">{n} tap{"s" if n != 1 else ""}</span></li>' for c, n in lost_moments(s))
    body = "".join(f'<p><span class="ts">{_clock(c.start)}</span> {e(text_of(c, lang, s.src_lang))}</p>'
                   for c in s.captions if text_of(c, lang, s.src_lang))
    when = time.strftime('%d %B %Y, %H:%M', time.localtime(s.started))
    return f"""<!DOCTYPE html>
<html lang="{lang}" dir="{d}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(s.title)} · notes</title>
<style>
  body {{ font-family: "Nirmala UI", "Noto Sans", "Mukta", system-ui, sans-serif; color: #2c1810; max-width: 780px;
         margin: 0 auto; padding: 1.5rem 1.2rem 3rem; line-height: 1.65; background: #fffdf8; }}
  header {{ border-bottom: 3px solid #a8460c; padding-bottom: .6rem; margin-bottom: 1.2rem; }}
  .brand {{ font-weight: 800; color: #a8460c; letter-spacing: .04em; font-size: .85rem; }}
  h1 {{ margin: .2rem 0; font-size: 1.6rem; }}
  .meta {{ color: #69543f; font-size: .92rem; }}
  h2 {{ color: #223367; font-size: 1.05rem; text-transform: uppercase; letter-spacing: .05em; margin: 1.6rem 0 .5rem; }}
  .hl li {{ margin: .45rem 0; }}
  .terms {{ columns: 2; column-gap: 2rem; }}
  .terms li {{ break-inside: avoid; margin: .2rem 0; }}
  .taps {{ color: #b3261e; font-size: .78rem; font-weight: 700; white-space: nowrap; }}
  .ts {{ color: #a8460c; font-size: .8rem; font-weight: 700; font-variant-numeric: tabular-nums; margin-inline-end: .35rem; }}
  .transcript p {{ margin: .35rem 0; }}
  .note {{ color: #69543f; font-size: .8rem; margin-top: 2rem; border-top: 1px solid #eadfc8; padding-top: .6rem; }}
  .print {{ position: fixed; top: 1rem; right: 1rem; background: #a8460c; color: #fff; border: 0; border-radius: 8px;
           padding: .5rem .9rem; font-weight: 700; cursor: pointer; }}
  @media print {{ .print {{ display: none; }} body {{ background: #fff; padding: 0; }} }}
</style></head><body>
<button class="print" onclick="window.print()">Save as PDF / Print</button>
<header><div class="brand">DWANILIVE · SESSION NOTES</div><h1>{e(s.title)}</h1>
<div class="meta">{when} · {_clock(s.duration_s)} · {e(LANG_NAMES.get(lang, lang))}</div></header>
{f'<h2>Highlights</h2><ul class="hl">{hl_html}</ul>' if hl_html else ''}
{f'<h2>Key terms</h2><ul class="terms">{terms_html}</ul>' if terms_html else ''}
{f'<h2>Where students got lost</h2><p class="meta">Anonymous "Lost me" taps from attendees during the talk.</p><ul class="hl">{lost_html}</ul>' if lost_html else ''}
<h2>Full transcript</h2><div class="transcript">{body}</div>
<p class="note">Generated offline by DwaniLive from live captions. Highlights are sentences selected automatically from the
talk; captions and translations are machine-generated and may contain errors.</p>
</body></html>"""
