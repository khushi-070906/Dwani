"""
LDST host server -- runs on the presenter's laptop.
Serves the attendee web client, generates the join QR code, and
broadcasts translated captions to attendees over the local network.

Run:
    pip install fastapi uvicorn "qrcode[pil]" --break-system-packages
    python server.py --port 8000
"""

import argparse
import asyncio
import dataclasses
import json
import socket
from pathlib import Path

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from accessibility import AccessibilityPreferences
from pipeline import AudioSegmenter, FakeASRBackend, FakeTranslationBackend, Pipeline
from qa_pipeline import BidirectionalTranslationBackend, FakeBidirectionalTranslationBackend, QAPipeline
from session import Session

app = FastAPI()

# Default session, used as-is when the app is imported directly (e.g. `uvicorn
# server:app`). The __main__ block below replaces this with one built from
# --port/--session-id when the script is run directly.
session = Session(port=8000)

# language_code -> set of connected websockets subscribed to that language
subscribers: dict[str, set[WebSocket]] = {}

# websocket -> that attendee's most recently sent accessibility settings.
# Populated from the same /ws connection subscribers already uses -- see
# attendee_socket() below -- rather than a separate endpoint, since it's
# just another field on the same "attendee connected" control channel the
# language selection already goes over.
accessibility_prefs: dict[WebSocket, AccessibilityPreferences] = {}

# True while a presenter's /host-ws stream is connected -- only one presenter
# stream is meaningful per session, so a second one is rejected rather than
# silently interleaving audio with the first.
host_connected = False

# Set in __main__ if --dynamic-glossary is passed; picked up by the startup
# event below to launch its background polling loop. None means disabled.
dynamic_glossary_updater = None

# Set in __main__ if --qa is passed (see qa_pipeline.py). None means the
# reverse (attendee -> presenter) translation path is disabled entirely --
# /qa-ws, /qa-notify, /qa/pending, /qa/answer all report enabled=False or
# reject connections rather than partially working.
qa_pipeline_instance: QAPipeline | None = None

# Presenter-side listeners for incoming Q&A (host.html and/or
# qa_presenter_listener.py -- see that script's docstring) -- pushed to as
# soon as a question finishes translating, same fan-out shape
# broadcast_caption already uses for attendees.
qa_listeners: set[WebSocket] = set()


@app.websocket("/ws")
async def attendee_socket(websocket: WebSocket, lang: str, session_param: str):
    if session_param != session.session_id:
        await websocket.close(code=4000, reason="unknown session")
        return

    await websocket.accept()
    subscribers.setdefault(lang, set()).add(websocket)
    try:
        while True:
            # The default attendee client doesn't send anything meaningful,
            # so this mainly just keeps the connection open and detects
            # disconnects -- but accessible_caption_client.html (and any
            # client wanting accessibility formatting) sends a JSON control
            # message of the form {"type": "settings", "accessibility": {...}}
            # over this same connection whenever the attendee changes a
            # setting. Anything else received (including the default
            # client's plain keepalive text) is silently ignored here,
            # same as before this feature existed.
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if isinstance(message, dict) and message.get("type") == "settings":
                accessibility_prefs[websocket] = AccessibilityPreferences.from_dict(
                    message.get("accessibility", {})
                )
    except WebSocketDisconnect:
        pass
    finally:
        subscribers[lang].discard(websocket)
        accessibility_prefs.pop(websocket, None)


async def broadcast_caption(lang: str, text: str, is_final: bool = True):
    """Called by the ASR/MT pipeline (module 2/3) once per language, per segment."""
    dead = []
    # snapshot the set before awaiting inside the loop -- a concurrent disconnect
    # mutating subscribers[lang] mid-iteration would otherwise raise RuntimeError
    for ws in list(subscribers.get(lang, set())):
        try:
            await ws.send_json({"text": text, "final": is_final})
        except Exception:
            dead.append(ws)
    for ws in dead:
        subscribers[lang].discard(ws)


def _default_pipeline() -> Pipeline:
    """Fake-backed Pipeline, used unless --whisper-model/--nllb-model-dir are
    passed on the command line. Segmentation timing is still driven by real
    mic audio in this mode, but FakeASRBackend always returns the same
    placeholder transcript regardless of what was actually said -- useful for
    confirming the audio pipeline and broadcast wiring work end to end before
    installing real models (see backends.py)."""
    return Pipeline(
        asr=FakeASRBackend(
            default_transcript="[no ASR model loaded -- start with --whisper-model to enable real transcription]"
        ),
        translator=FakeTranslationBackend(),
        broadcast=broadcast_caption,
        subscribed_languages=lambda: subscribers.keys(),
    )


# Built once here so it exists when the app is imported directly (e.g. by
# tests, or `uvicorn server:app`); the __main__ block below replaces it with
# a real-model-backed Pipeline if --whisper-model/--nllb-model-dir are given.
pipeline = _default_pipeline()


@app.websocket("/host-ws")
async def host_socket(websocket: WebSocket, session_param: str):
    """Presenter mic stream (see static/host.html): receives raw float32,
    16kHz, mono PCM chunks as binary WebSocket frames and runs each one
    through `pipeline`, which handles segmentation, ASR, per-language
    translation, and broadcast to attendees on its own (Section 4.3)."""
    global host_connected

    if session_param != session.session_id:
        await websocket.close(code=4000, reason="unknown session")
        return
    if host_connected:
        await websocket.close(code=4001, reason="a presenter is already connected")
        return

    await websocket.accept()
    host_connected = True
    # Fresh segmenter per connection so a previous presenter session's
    # half-open segment (if any) never bleeds into this one.
    pipeline.segmenter = AudioSegmenter()
    try:
        while True:
            data = await websocket.receive_bytes()
            chunk = np.frombuffer(data, dtype=np.float32).copy()
            await pipeline.handle_audio_chunk(chunk)
    except WebSocketDisconnect:
        pass
    finally:
        await pipeline.flush()
        host_connected = False


async def _push_question_to_presenter(question) -> None:
    """QAPipeline's deliver_to_presenter callback: fans a translated
    question out to every connected presenter-side listener (host.html
    and/or qa_presenter_listener.py), same snapshot-before-await pattern
    broadcast_caption uses so a listener disconnecting mid-fan-out can't
    raise RuntimeError."""
    dead = []
    for ws in list(qa_listeners):
        try:
            await ws.send_json(dataclasses.asdict(question))
        except Exception:
            dead.append(ws)
    for ws in dead:
        qa_listeners.discard(ws)


@app.post("/qa/ask")
async def qa_ask(payload: dict):
    """Typed-question counterpart to /qa-ws: no mic, no browser audio
    permissions, no secure-context (HTTPS) requirement at all -- an
    attendee types their question in whatever language they're comfortable
    in, it's translated straight to --presenter-language via
    qa_pipeline.py's handle_question_text(), and delivered to presenter-side
    listeners exactly the same way a spoken question is (see
    _push_question_to_presenter above). Body: {"text": "...", "lang": "hi"}.
    """
    if qa_pipeline_instance is None:
        return JSONResponse({"error": "Q&A is not enabled on this server (start with --qa)"}, status_code=404)

    text = (payload.get("text") or "").strip()
    lang = (payload.get("lang") or "").strip()
    if not text:
        return JSONResponse({"error": "text is required"}, status_code=400)
    if not lang:
        return JSONResponse({"error": "lang is required"}, status_code=400)

    question = await qa_pipeline_instance.handle_question_text(text, asker_language=lang)
    return {"ok": True, "question": dataclasses.asdict(question)}


@app.websocket("/qa-ws")
async def qa_socket(websocket: WebSocket, lang: str, session_param: str):
    """Attendee mic stream for asking a question (the reverse of
    /host-ws): receives raw float32, 16kHz, mono PCM chunks -- same wire
    format /host-ws expects -- while an attendee has their hand raised,
    for exactly as long as that one WebSocket connection is open. `lang`
    is the language the attendee is asking their question in.

    Deliberately its own endpoint rather than reusing /host-ws: many
    attendees may open this concurrently (one per raised hand), whereas
    /host-ws is explicitly single-presenter (host_connected guard above),
    and a question's audio should never be run through the *forward*
    pipeline's segmenter/broadcast by mistake.
    """
    if qa_pipeline_instance is None:
        await websocket.close(code=4004, reason="Q&A is not enabled on this server (start with --qa)")
        return
    if session_param != session.session_id:
        await websocket.close(code=4000, reason="unknown session")
        return

    await websocket.accept()
    segmenter = AudioSegmenter()  # fresh per raised-hand connection, same reasoning as /host-ws
    try:
        while True:
            data = await websocket.receive_bytes()
            chunk = np.frombuffer(data, dtype=np.float32).copy()
            segment = segmenter.push(chunk)
            if segment is not None:
                await qa_pipeline_instance.handle_question_audio(segment, asker_language=lang)
    except WebSocketDisconnect:
        pass
    finally:
        trailing = segmenter.flush()
        if trailing is not None:
            await qa_pipeline_instance.handle_question_audio(trailing, asker_language=lang)


@app.websocket("/qa-notify")
async def qa_notify_socket(websocket: WebSocket, session_param: str):
    """Presenter-side push channel: as soon as a question finishes
    translating, its JSON representation is sent here (see
    _push_question_to_presenter above). host.html and
    qa_presenter_listener.py both connect here rather than polling
    /qa/pending, for lower latency between a question being asked and the
    presenter seeing/hearing it."""
    if session_param != session.session_id:
        await websocket.close(code=4000, reason="unknown session")
        return

    await websocket.accept()
    qa_listeners.add(websocket)
    try:
        while True:
            await websocket.receive_text()  # keepalive / detect disconnect only
    except WebSocketDisconnect:
        pass
    finally:
        qa_listeners.discard(websocket)


@app.get("/qa/pending")
async def qa_pending():
    """Polling alternative to /qa-notify -- e.g. for a presenter-side page
    that only refreshes periodically rather than holding a WebSocket open.
    Returns enabled=False when --qa wasn't passed, same convention as
    /cache-stats and /glossary-stats above."""
    if qa_pipeline_instance is None:
        return {"enabled": False}
    return {"enabled": True, "questions": [dataclasses.asdict(q) for q in qa_pipeline_instance.pending_questions()]}


@app.post("/qa/answer/{question_id}")
async def qa_answer(question_id: str):
    if qa_pipeline_instance is None:
        return JSONResponse({"error": "Q&A is not enabled on this server"}, status_code=404)
    qa_pipeline_instance.mark_answered(question_id)
    return {"ok": True}


@app.get("/session-info")
async def session_info():
    return {"session_id": session.session_id, "languages_available": list(subscribers.keys())}


@app.get("/presenter-info")
async def presenter_info():
    """Everything host.html needs to display itself, standalone: the
    attendee join URL (so it can render/link it) and where to fetch the QR
    image from. Kept separate from /session-info so that endpoint's existing
    response shape (asserted exactly in test_session.py) doesn't change.

    hotspot_ssid/hotspot_qr_url are only added when a self-broadcast hotspot
    (Section 4.1) is actually active, so the response shape for a plain
    venue-WiFi session (announce(), not announce_with_hotspot()) is
    unchanged. The hotspot password is deliberately NOT included here --
    it's only ever shown via the terminal ASCII / QR PNG the presenter
    controls, not over an HTTP endpoint."""
    info = {"session_id": session.session_id, "join_url": session.primary_url(), "qr_url": "/qr.png"}
    if session.hotspot_active:
        info["hotspot_ssid"] = session.hotspot_ssid
        info["hotspot_qr_url"] = "/qr-wifi.png"
    return info


@app.get("/qr.png")
async def qr_image():
    return FileResponse(session.qr_image_path)


@app.get("/qr-wifi.png")
async def qr_wifi_image():
    """WiFi-onboarding QR code (Section 4.1): scanning this joins the
    presenter's self-broadcast hotspot directly, before the /qr.png join-URL
    QR is reachable at all. 404s with a clear reason if no hotspot is
    active for this session (e.g. it fell back to venue-WiFi discovery)."""
    if not session.hotspot_active:
        return JSONResponse({"error": "no hotspot active for this session"}, status_code=404)
    return FileResponse(session.wifi_qr_image_path)


@app.get("/health")
async def health():
    """Used by load/latency test scripts (module 5) to confirm the server is up
    before hammering it with simulated attendees."""
    total_subscribers = sum(len(v) for v in subscribers.values())
    return {"status": "ok", "session_id": session.session_id, "connected_attendees": total_subscribers}


@app.get("/cache-stats")
async def cache_stats():
    """Section 8.2 (semantic translation cache) evaluation hook: exposes the
    live hit rate / estimated time saved off whatever cache `pipeline` is
    currently using, so benchmark_semantic_cache.py (or a manual `curl`) can
    read it without needing in-process access to `pipeline`. Returns
    enabled=False rather than 404/error when the default NoOpCache or
    ExactMatchCache (neither of which tracks CacheStats) is in use, since
    "no semantic cache running" is a normal state, not a failure."""
    stats = getattr(pipeline.cache, "stats", None)
    if stats is None:
        return {"enabled": False}
    return {
        "enabled": True,
        "hits": stats.hits,
        "misses": stats.misses,
        "hit_rate": stats.hit_rate,
        "mean_translate_seconds": stats.mean_translate_seconds,
        "estimated_seconds_saved": stats.estimated_seconds_saved,
    }


@app.get("/accessibility-stats")
async def accessibility_stats():
    """How many currently-connected attendees have accessibility mode
    settings active, and which ones -- a live number worth having on
    screen during a demo, and a real evaluation metric (Section 7-style)
    for the accessibility extension on its own: adoption, not just
    presence of the feature."""
    active = list(accessibility_prefs.values())
    return {
        "connected_attendees": sum(len(v) for v in subscribers.values()),
        "attendees_with_accessibility_settings": len(active),
        "high_contrast_count": sum(1 for p in active if p.high_contrast),
        "large_text_count": sum(1 for p in active if p.large_text),
        "flash_on_new_caption_count": sum(1 for p in active if p.flash_on_new_caption),
    }


@app.get("/glossary-stats")
async def glossary_stats():
    """Option 3 (slide-synchronized dynamic glossary) evaluation hook:
    exposes the live term-added log so you can cross-reference, after a
    session, when each term entered the glossary against when it was
    actually spoken -- the basis for "does term accuracy improve once a
    term first appears on-screen" (dynamic_glossary.py's research
    question). Returns enabled=False when --dynamic-glossary wasn't
    passed, same "not running is a normal state" convention as
    /cache-stats above."""
    if dynamic_glossary_updater is None:
        return {"enabled": False}
    return {
        "enabled": True,
        "glossary_term_count": len(dynamic_glossary_updater.glossary),
        "terms_added": [
            {"term": e.term, "added_at_seconds": e.added_at_seconds, "poll_index": e.poll_index}
            for e in dynamic_glossary_updater.log
        ],
    }


@app.get("/decision-log")
async def decision_log():
    """ITDE evaluation hook: exposes the live veto rate and current
    per-language thresholds off pipeline.translator, when it's an
    IntelligentTranslationDecisionEngine. Returns enabled=False otherwise --
    same "not running is a normal state" convention as /cache-stats and
    /glossary-stats above. The full per-decision log (text, action,
    threshold-in-force) is available in-process via
    pipeline.translator.log for a post-session evaluation script; this
    endpoint returns the summary, not the full log, to avoid growing
    unbounded over a long session."""
    engine = getattr(pipeline, "translator", None)
    if not hasattr(engine, "veto_rate"):
        return {"enabled": False}
    languages = sorted({r.lang for r in engine.log})
    return {
        "enabled": True,
        "decisions_logged": len(engine.log),
        "overall_veto_rate": engine.veto_rate(),
        "per_language": {
            lang: {"threshold": engine.threshold_for(lang), "veto_rate": engine.veto_rate(lang)}
            for lang in languages
        },
    }


@app.on_event("startup")
async def _start_dynamic_glossary_updater():
    """Launches DynamicGlossaryUpdater.run() as a background task for the
    server's lifetime, if --dynamic-glossary was passed. A no-op otherwise
    -- dynamic_glossary_updater stays None unless __main__ sets it."""
    if dynamic_glossary_updater is not None:
        asyncio.create_task(dynamic_glossary_updater.run())


static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
async def index():
    return FileResponse(static_dir / "index.html")


@app.get("/host")
async def host_page():
    """The presenter opens this page (with ?session=<id>, same as attendees)
    to start streaming their mic -- see static/host.html."""
    return FileResponse(static_dir / "host.html")


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser(description="LDST host server")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--https",
        action="store_true",
        help="Serve over HTTPS using cert.pem/key.pem in the current directory (see generate_cert.py). "
        "Required for the mic-based features (host.html's presenter mic, index.html's Ask-a-question, "
        "qa_pipeline.py) to work on attendee devices connecting over the LAN IP rather than localhost -- "
        "browsers only allow microphone access on a secure context (https://, or http://localhost), "
        "and a plain http://<lan-ip> join URL doesn't qualify. Not needed if every device using the mic "
        "features is on http://localhost (e.g. testing on the presenter's own machine).",
    )
    parser.add_argument(
        "--session-id",
        type=str,
        default=None,
        help="Fix the session ID instead of generating a random one (useful for test scripts)",
    )
    parser.add_argument(
        "--whisper-model",
        type=str,
        default=None,
        help="faster-whisper model size (e.g. 'small', 'base') to enable real transcription. "
        "Omit to broadcast placeholder transcripts instead (see backends.py for setup).",
    )
    parser.add_argument(
        "--nllb-model-dir",
        type=str,
        default=None,
        help="Path to a CTranslate2-converted NLLB-200 directory to enable real translation. "
        "Omit to broadcast placeholder translations instead (see backends.py for setup).",
    )
    parser.add_argument(
        "--presenter-language",
        type=str,
        default="en",
        help="Language the presenter speaks, used as the Whisper transcription language "
        "and the NLLB source language.",
    )
    parser.add_argument(
        "--semantic-cache",
        action="store_true",
        help="Enable the semantic translation cache (translation_cache.SemanticCache): skips "
        "re-translating a segment whose transcript is close to one already translated this "
        "session, for the same target language. Requires sentence-transformers "
        "(pip install -r requirements-cache.txt). Only has an effect alongside "
        "--nllb-model-dir -- with FakeTranslationBackend there's nothing worth caching.",
    )
    parser.add_argument(
        "--semantic-cache-threshold",
        type=float,
        default=0.92,
        help="Cosine-similarity threshold for a semantic cache hit (default: 0.92). Only used "
        "with --semantic-cache.",
    )
    parser.add_argument(
        "--glossary-file",
        type=str,
        default=None,
        help="Path to a glossary.json (see glossary.py, build_glossary.py) protecting technical "
        "terms during translation. Only has an effect alongside --nllb-model-dir. Run "
        "smoke_test_glossary.py against your actual model before relying on this live.",
    )
    parser.add_argument(
        "--dynamic-glossary",
        action="store_true",
        help="Enable slide-synchronized live glossary updates (dynamic_glossary.py): "
        "periodically OCRs the presenter's screen and merges newly-seen recurring technical "
        "terms into the glossary mid-session. Requires mss + pytesseract + a Tesseract install "
        "(see dynamic_glossary.py's setup section) and --nllb-model-dir. Starts from "
        "--glossary-file if given, or an empty glossary otherwise.",
    )
    parser.add_argument(
        "--dynamic-glossary-interval",
        type=float,
        default=5.0,
        help="Seconds between screen polls for --dynamic-glossary (default: 5.0).",
    )
    parser.add_argument(
        "--persistent-glossary-path",
        type=str,
        default=None,
        help="Path to an ACKM PersistentGlossary store (persistent_memory.py): human-approved "
        "glossary terms that carry over across sessions without re-review. Only approved terms "
        "are used -- see PersistentGlossary.approve(). Takes priority over --glossary-file if "
        "both are given.",
    )
    parser.add_argument(
        "--cache-storage-path",
        type=str,
        default=None,
        help="Path for the semantic cache to persist entries to disk across sessions (ACKM). Only "
        "has an effect alongside --semantic-cache or --itde.",
    )
    parser.add_argument(
        "--itde",
        action="store_true",
        help="Enable the Intelligent Translation Decision Engine (decision_engine.py): governs the "
        "semantic cache with a glossary-term-consistency veto and an adaptive per-language "
        "similarity threshold, instead of the plain fixed-threshold cache. Implies a semantic "
        "cache is in use even if --semantic-cache wasn't separately passed.",
    )
    parser.add_argument(
        "--qa",
        action="store_true",
        help="Enable bidirectional Q&A (qa_pipeline.py): attendees can stream a spoken question "
        "in their own language over /qa-ws, translated back into --presenter-language and pushed "
        "to presenter-side listeners over /qa-notify. Works with placeholder ASR/MT if "
        "--whisper-model/--nllb-model-dir aren't given, same as the main pipeline.",
    )
    parser.add_argument(
        "--no-hotspot",
        dest="hotspot",
        action="store_false",
        default=True,
        help="Disable the self-broadcast WiFi hotspot (Section 4.1) and fall back to the old "
        "behavior of discovering IPs on whatever network this machine is already connected to "
        "(e.g. venue WiFi). On by default -- pass this if you specifically want the venue-WiFi "
        "path, or if this machine's WiFi adapter can't run as an access point.",
    )
    parser.add_argument(
        "--hotspot-ssid",
        type=str,
        default=None,
        help="Use this exact SSID instead of a generated one. Required on macOS (see session.py's "
        "HotspotError message) since hotspot creation there isn't automatable -- set up Internet "
        "Sharing manually first, then pass its SSID here so the QR code matches.",
    )
    parser.add_argument(
        "--hotspot-password",
        type=str,
        default=None,
        help="Use this exact password instead of a generated one. Required on macOS alongside "
        "--hotspot-ssid, for the same reason.",
    )
    args = parser.parse_args()

    def _port_is_free(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("0.0.0.0", port))
                return True
            except OSError:
                return False

    resolved_port = args.port
    if not _port_is_free(resolved_port):
        print(f"Port {resolved_port} is already in use (likely a server from a previous run still open in another terminal window).")
        for candidate in range(args.port + 1, args.port + 11):
            if _port_is_free(candidate):
                resolved_port = candidate
                break
        else:
            raise SystemExit(
                f"Ports {args.port}-{args.port + 10} are all in use. Close old "
                f"terminal windows running server.py, or pass a different --port."
            )
        print(f"Using port {resolved_port} instead. Use the URLs printed below (not the ones from any earlier run).\n")

    session = Session(port=resolved_port, session_id=args.session_id, scheme="https" if args.https else "http")

    if args.whisper_model or args.nllb_model_dir:
        from backends import RealNLLBBackend, RealWhisperBackend

        asr = (
            RealWhisperBackend(model_size=args.whisper_model, language=args.presenter_language)
            if args.whisper_model
            else FakeASRBackend(default_transcript="[no --whisper-model given]")
        )
        translator = (
            RealNLLBBackend(model_dir=args.nllb_model_dir, source_lang=args.presenter_language)
            if args.nllb_model_dir
            else FakeTranslationBackend()
        )

        if args.glossary_file or args.dynamic_glossary or args.persistent_glossary_path or args.itde:
            from glossary import Glossary

            if args.persistent_glossary_path:
                from persistent_memory import PersistentGlossary

                persistent_glossary = PersistentGlossary(args.persistent_glossary_path)
                glossary = persistent_glossary.to_glossary()
                pending = persistent_glossary.pending_review()
                print(
                    f"Persistent glossary loaded from {args.persistent_glossary_path}: "
                    f"{len(glossary)} approved term(s) in use"
                    + (f", {len(pending)} pending review: {pending}" if pending else "")
                    + "."
                )
            elif args.glossary_file:
                glossary = Glossary.load(args.glossary_file)
                print(f"Glossary loaded from {args.glossary_file} ({len(glossary)} term(s)).")
            else:
                glossary = Glossary()
                print("Starting from an empty glossary.")

            if args.dynamic_glossary:
                from dynamic_glossary import DynamicGlossaryUpdater

                # Module-level name, not a function-local -- /glossary-stats and the
                # startup event above both need to reach this same instance.
                dynamic_glossary_updater = DynamicGlossaryUpdater(
                    glossary, poll_interval_seconds=args.dynamic_glossary_interval
                )
                print(
                    f"Dynamic glossary updater enabled (polling every "
                    f"{args.dynamic_glossary_interval}s) -- terms newly seen on screen will be "
                    f"merged into the glossary above as the session runs."
                )
        else:
            glossary = None

        if args.itde:
            from decision_engine import IntelligentTranslationDecisionEngine
            from translation_cache import SemanticCache

            itde_cache = SemanticCache(
                similarity_threshold=args.semantic_cache_threshold, storage_path=args.cache_storage_path
            )
            translator = IntelligentTranslationDecisionEngine(
                inner=translator, cache=itde_cache, glossary=glossary or Glossary()
            )
            cache = None  # ITDE owns caching internally -- Pipeline's own cache stays a no-op,
            # since a second cache layer outside ITDE would re-introduce the exact
            # term-swap failure mode ITDE exists to catch (see decision_engine.py).
            print(
                f"ITDE enabled: adaptive per-language threshold (starting from "
                f"{args.semantic_cache_threshold}), glossary-term veto active"
                + (f", cache persisted to {args.cache_storage_path}" if args.cache_storage_path else "")
                + "."
            )
        else:
            if glossary is not None:
                from glossary import GlossaryAwareTranslationBackend

                translator = GlossaryAwareTranslationBackend(translator, glossary)

            cache = None
            if args.semantic_cache:
                from translation_cache import SemanticCache

                cache = SemanticCache(
                    similarity_threshold=args.semantic_cache_threshold, storage_path=args.cache_storage_path
                )
                print(
                    f"Semantic translation cache enabled (similarity threshold="
                    f"{args.semantic_cache_threshold})"
                    + (f", persisted to {args.cache_storage_path}" if args.cache_storage_path else "")
                    + "."
                )

        pipeline = Pipeline(asr, translator, broadcast_caption, lambda: subscribers.keys(), cache=cache)
        print(f"Using real backends: whisper={args.whisper_model or '(none)'} nllb_dir={args.nllb_model_dir or '(none)'}")
    else:
        print("No --whisper-model/--nllb-model-dir given -- broadcasting placeholder transcripts/translations.")

    if args.qa:
        # A separate RealWhisperBackend instance from the main pipeline's,
        # deliberately: the main one is pinned to language=args.presenter_language
        # (Section 4.3 -- the presenter's language is known ahead of time), but
        # a question could be asked in any language an attendee has selected,
        # so this one auto-detects per question instead (language=None). See
        # qa_pipeline.py's module docstring for the full reasoning.
        if args.whisper_model:
            from backends import RealWhisperBackend

            qa_asr = RealWhisperBackend(model_size=args.whisper_model, language=None)
        else:
            qa_asr = FakeASRBackend(default_transcript="[no --whisper-model given]")

        qa_translator = (
            BidirectionalTranslationBackend(nllb_model_dir=args.nllb_model_dir)
            if args.nllb_model_dir
            else FakeBidirectionalTranslationBackend()
        )

        qa_pipeline_instance = QAPipeline(
            asr=qa_asr,
            translator=qa_translator,
            presenter_language=args.presenter_language,
            deliver_to_presenter=_push_question_to_presenter,
        )
        print(
            f"Q&A enabled: attendees can ask questions at /qa-ws, delivered to /qa-notify "
            f"in {args.presenter_language} "
            f"(whisper={args.whisper_model or '(placeholder)'} nllb_dir={args.nllb_model_dir or '(placeholder)'})."
        )

    if args.hotspot:
        join_url = session.announce_with_hotspot(args.hotspot_ssid, args.hotspot_password)
    else:
        if args.hotspot_ssid or args.hotspot_password:
            print("--hotspot-ssid/--hotspot-password given alongside --no-hotspot; ignoring them.\n")
        join_url = session.announce()

    host_url = join_url.replace(f"/?session=", "/host?session=")
    print(f"Presenter mic page: {host_url}\n")

    ssl_kwargs = {}
    if args.https:
        cert_path, key_path = Path("cert.pem"), Path("key.pem")
        if not cert_path.exists() or not key_path.exists():
            raise SystemExit(
                "--https was passed but cert.pem/key.pem weren't found in the current directory.\n"
                "Generate them first (fully offline, no CA/internet needed): python generate_cert.py"
            )
        ssl_kwargs = {"ssl_certfile": str(cert_path), "ssl_keyfile": str(key_path)}
        print(
            "Serving over HTTPS with a self-signed certificate -- attendee browsers will show a "
            "'not secure' warning on first visit; that's expected (see generate_cert.py's docstring). "
            "This is what makes the mic-based features (presenter mic, Ask a question) work on "
            "phones connecting over the LAN IP rather than localhost.\n"
        )

    try:
        uvicorn.run(app, host="0.0.0.0", port=resolved_port, **ssl_kwargs)
    finally:
        # Best-effort: if we started a hotspot, don't leave it broadcasting
        # (and, on Windows, holding the WiFi adapter in AP mode) after the
        # server process exits.
        session.stop_hotspot()