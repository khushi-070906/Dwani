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
import socket
from pathlib import Path

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from pipeline import AudioSegmenter, FakeASRBackend, FakeTranslationBackend, Pipeline
from session import Session

app = FastAPI()

# Default session, used as-is when the app is imported directly (e.g. `uvicorn
# server:app`). The __main__ block below replaces this with one built from
# --port/--session-id when the script is run directly.
session = Session(port=8000)

# language_code -> set of connected websockets subscribed to that language
subscribers: dict[str, set[WebSocket]] = {}

# True while a presenter's /host-ws stream is connected -- only one presenter
# stream is meaningful per session, so a second one is rejected rather than
# silently interleaving audio with the first.
host_connected = False

# Set in __main__ if --dynamic-glossary is passed; picked up by the startup
# event below to launch its background polling loop. None means disabled.
dynamic_glossary_updater = None


@app.websocket("/ws")
async def attendee_socket(websocket: WebSocket, lang: str, session_param: str):
    if session_param != session.session_id:
        await websocket.close(code=4000, reason="unknown session")
        return

    await websocket.accept()
    subscribers.setdefault(lang, set()).add(websocket)
    try:
        while True:
            # attendee client doesn't send anything meaningful, this just
            # keeps the connection open and detects disconnects
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        subscribers[lang].discard(websocket)


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

    session = Session(port=resolved_port, session_id=args.session_id)

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

    if args.hotspot:
        join_url = session.announce_with_hotspot(args.hotspot_ssid, args.hotspot_password)
    else:
        if args.hotspot_ssid or args.hotspot_password:
            print("--hotspot-ssid/--hotspot-password given alongside --no-hotspot; ignoring them.\n")
        join_url = session.announce()

    host_url = join_url.replace(f"/?session=", "/host?session=")
    print(f"Presenter mic page: {host_url}\n")

    try:
        uvicorn.run(app, host="0.0.0.0", port=resolved_port)
    finally:
        # Best-effort: if we started a hotspot, don't leave it broadcasting
        # (and, on Windows, holding the WiFi adapter in AP mode) after the
        # server process exits.
        session.stop_hotspot()