# LDST — Local Device-to-Device Speech Translation (reference scaffold)

Implements the four modules from Section 4 of the proposal, mapped directly to code:

| Proposal section | Code |
|---|---|
| 4.1 Session Hosting | `server.py` — `presenter_page()`, `get_local_ip()`, generates a QR code with no external account or registration |
| 4.2 Local Discovery and Connection | `qrcode_png()` encodes `http://<local-ip>:8000/join`; all traffic stays on `0.0.0.0:8000`, nothing routes to the public internet |
| 4.3 Transcription-Translation Pipeline | `transcribe_segment()` (faster-whisper) + `translate_text()` (NLLB-200) + energy-based VAD in `ws_presenter_audio()`; translation runs once per active *language*, not once per attendee, matching the proposal's broadcast design |
| 4.4 Attendee Client | `static/attendee.html` — dropdown language picker + live caption stream over `/ws/captions`, no app install |
| Option 1 / Option 2 extensions | Semantic translation cache and conference glossary adaptation — both opt-in, see [Optional extensions](#optional-extensions) below |

## Setup

```bash
cd ldst
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

First run downloads the Whisper (`small`, ~250MB) and NLLB-200-distilled (~2.5GB) models from Hugging Face — do this once **while you still have internet**, before the offline conference.

## Run

```bash
python server.py
```

This prints two URLs:
- **Presenter control page** — open on the presenter's own laptop, click "Start Session," allow mic access.
- **Attendee join URL** — this is exactly what the QR code on the presenter page encodes. Any device on the same WiFi (or connected to the presenter's hotspot) can open it directly, or scan the QR code.

No attendee-side installation. No signup. No internet required once the models are downloaded.

## How a caption reaches an attendee

1. Presenter's browser captures mic audio, downsamples to 16kHz mono PCM16, streams it over `/ws/presenter/audio`.
2. Server buffers audio and uses RMS-based silence detection to find segment boundaries (a pause = the end of a sentence-like unit).
3. Each segment goes through `faster-whisper` → English transcript.
4. For every **distinct language currently selected by a connected attendee**, the transcript is translated once via NLLB-200 and broadcast to every attendee subscribed to that language over `/ws/captions`.

## What this scaffold deliberately simplifies (Section 7, Limitations)

These are the things to harden before using it in front of a real audience — flagged here rather than glossed over:

- **VAD is energy-threshold based**, not a trained voice-activity model. Fine for a quiet room; tune `SILENCE_RMS_THRESHOLD` for your venue, or swap in `webrtcvad` / Silero VAD for noisier rooms.
- **No reconnection logic** on the attendee client — if WiFi hiccups, the attendee has to refresh. Add exponential-backoff reconnect in `attendee.html`'s `connect()`.
- **Single presenter device only** — matches the proposal's stated single-room scope; multi-track support is explicitly future work (Section 7).
- **No hotspot fallback implemented** — the proposal mentions a device-hosted hotspot as an alternative to shared WiFi (4.2); this scaffold assumes a shared LAN. Standing up a hotspot from Python is OS-specific (`nmcli` on Linux, `netsh` on Windows) and isn't included here.
- **Translation quality/latency numbers aren't measured yet** — this is exactly what your Methodology section (5.1–5.3) is for. `transcribe_segment()` and `translate_text()` are natural places to hook in timing instrumentation for the latency evaluation.
- **CPU-only by default** (`device="cpu"` in `WhisperModel`). If the presenter's laptop has a GPU, switch to `device="cuda"` for materially lower latency — directly relevant to your 5.1 evaluation.

## Optional extensions

Two opt-in additions on top of the core pipeline, each addressing a specific weak point in Section 7's limitations above. Neither changes default behavior — the server runs exactly as before unless you pass the relevant flag.

### Semantic Translation Cache

**Problem it addresses:** a presenter re-reading a slide title, recapping a definition, or repeating "any questions?" triggers a full NLLB translate call every time, even though the sentence (or something close to it) was already translated minutes earlier in the same session.

**How it works:** `translation_cache.py`'s `SemanticCache` sits between the transcript and the translator. Each transcript is embedded (`sentence-transformers`, default `all-MiniLM-L6-v2`) and checked against everything already translated for that target language via cosine similarity; a hit (≥0.92 similarity by default) reuses the earlier translation instead of calling NLLB again.

```bash
pip install -r requirements-cache.txt --break-system-packages
python server.py --nllb-model-dir nllb-200-ct2 --whisper-model small --semantic-cache
```

Live hit-rate/latency numbers are available at `GET /cache-stats` while the server is running. To measure the effect offline against a set of sentences (hit rate, wall-clock time reduction, CPU%, peak memory), see `benchmark_semantic_cache.py` (needs `--nllb-model-dir` for meaningful timing numbers; `sample_sentences.json` is a ready-made input).

Files: `translation_cache.py` (implementation), `test_translation_cache.py`, `benchmark_semantic_cache.py`, `sample_sentences.json`, `requirements-cache.txt`.

### Conference Glossary Adaptation

**Problem it addresses:** NLLB-200 is trained on general text and is prone to mangling or mistranslating domain-specific technical vocabulary from a talk — acronyms, model/hardware names, terms coined in the paper itself.

**How it works:** `glossary.py`'s `Glossary` holds a list of terms, each either "preserve verbatim" or "translate to this specific word per target language." `GlossaryAwareTranslationBackend` wraps the real translator: before translating, glossary terms in the transcript are swapped for placeholder tokens NLLB passes through untouched; after translating, the placeholders are swapped back for the correct term. This is a from-scratch build (Section 4's terminology-constrained NMT isn't something NLLB supports natively), and its correctness depends on that placeholder-survival assumption holding for your actual model — verify with `smoke_test_glossary.py` before relying on it live.

```bash
# 1. Build a first-pass glossary from your paper/slides/abstract (always review the output before trusting it)
python build_glossary.py --input paper.pdf slides.txt --output glossary.json

# 2. Confirm placeholder tokens survive your actual NLLB model intact
python smoke_test_glossary.py nllb-200-ct2 glossary.json

# 3. Run with it
python server.py --nllb-model-dir nllb-200-ct2 --whisper-model small --glossary-file glossary.json
```

`--glossary-file` and `--semantic-cache` can be combined — the cache stores the final, glossary-restored text, so a cache hit still reflects correct terminology.

To measure whether glossary adaptation actually improves translation quality (the research question from Section 8: BLEU, chrF, optionally COMET, plus a glossary-specific term-accuracy metric and blank columns for human rating), see `evaluate_glossary.py` and `glossary_eval_sample_manifest.json` — note the sample manifest's reference translations are illustrative starting points, not vetted, so have a fluent speaker check them before trusting numbers computed against them.

Files: `glossary.py` (implementation), `build_glossary.py`, `smoke_test_glossary.py`, `test_glossary.py`, `evaluate_glossary.py`, `glossary_eval_sample_manifest.json`.



- **5.1 Latency**: timestamp at `receive_bytes()` (word spoken) and at `broadcast_caption()` (caption sent); log the delta per segment.
- **5.2 Accuracy**: feed pre-recorded lecture audio into `transcribe_segment()` + `translate_text()` offline and diff against reference translations (e.g. `jiwer` for WER, `sacrebleu` for translation quality).
- **5.3 Stability**: run `server.py` against a looped audio file for a full lecture-length duration and watch for memory growth or dropped WebSocket connections as simulated attendees join/leave.

how to run 
python server.py --nllb-model-dir nllb-200-ct2 --whisper-model small --semantic-cache



python server.py --whisper-model small --nllb-model-dir nllb-200-ct2 --itde --cache-storage-path memory\cache.json --persistent-glossary-path memory\glossary.json --dynamic-glossary --dynamic-glossary-interval 5


python run.py --whisper-model small --nllb-model-dir nllb-200-ct2 --presenter-language en --itde --semantic-cache-threshold 0.92 --cache-storage-path memory/cache.json --persistent-glossary-path memory/glossary.json --dynamic-glossary --dynamic-glossary-interval 5



python build_glossary.py --input input_paper.pdf --output glossary.json

