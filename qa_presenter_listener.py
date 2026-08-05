"""
Presenter-side Q&A listener, no browser involved.

Connects to the running server's /qa-notify endpoint and prints each
translated question to the terminal as it arrives -- this is the
presenter-facing half of qa_pipeline.py's bidirectional Q&A, and it
deliberately mirrors presenter_mic.py's standalone-terminal approach for
the same reason: a presenter who's already running server.py + mic
streaming via run.py (no host.html open) still needs some way to see
incoming questions, and a second terminal window is more reliable than
requiring a browser tab to be visible and unminimized during a talk.

Setup:
    pip install websockets
    pip install pyttsx3   # optional, only needed for --speak (offline TTS)

Usage (server must already be running, --qa must have been passed to it):
    python qa_presenter_listener.py <session_id>

    python qa_presenter_listener.py a1b2c3d4 --host 192.168.1.42 --port 8000 --speak

Press Ctrl+C to stop listening.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

import websockets


def _print_question(question: dict) -> None:
    asker_lang = question.get("asker_language", "?")
    original = question.get("original_text", "")
    translated = question.get("translated_text", "")
    print(f"\n{'='*60}")
    print(f"  QUESTION (asked in {asker_lang})")
    print(f"  Original:   {original}")
    print(f"  Translated: {translated}")
    print(f"{'='*60}\n")


def _speak(text: str) -> None:
    """Offline text-to-speech via pyttsx3 -- deferred import, same pattern
    as this project's other optional-dependency modules, so importing this
    script (or running without --speak) never requires pyttsx3 to be
    installed. Runs entirely on-device, same "no traffic leaves the local
    network" property as the rest of the pipeline -- pyttsx3 wraps each
    OS's own built-in TTS engine (SAPI5 on Windows, NSSpeechSynthesizer on
    macOS, espeak on Linux) rather than calling out to a cloud API."""
    try:
        import pyttsx3
    except ImportError:
        print("(--speak requires pyttsx3: pip install pyttsx3)", file=sys.stderr)
        return
    engine = pyttsx3.init()
    engine.say(text)
    engine.runAndWait()


async def listen(session_id: str, host: str, port: int, speak: bool) -> None:
    uri = f"ws://{host}:{port}/qa-notify?session_param={session_id}"
    print(f"Connecting to {uri} ...")

    try:
        async with websockets.connect(uri) as ws:
            print("Connected. Listening for questions -- Ctrl+C to stop.\n")
            async for raw in ws:
                try:
                    question = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                _print_question(question)
                if speak:
                    _speak(question.get("translated_text", ""))
    except websockets.exceptions.ConnectionClosedError as e:
        print(f"\nServer closed the connection: code={e.code} reason={e.reason!r}")
        if e.code == 4000:
            print("-> The session ID doesn't match what the server currently has.")
        elif e.code == 4004:
            print("-> This server wasn't started with --qa.")
    except (ConnectionRefusedError, OSError) as e:
        print(f"\nCouldn't reach the server at {uri}: {e}")
        print("-> Is server.py running with --qa, and is --host/--port correct?")
    except KeyboardInterrupt:
        print("\nStopped listening.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Listen for incoming Q&A on the LDST server, no browser required.")
    parser.add_argument("session_id", help="Session ID printed by server.py on startup")
    parser.add_argument("--host", default="localhost", help="Server host (default: localhost)")
    parser.add_argument("--port", type=int, default=8000, help="Server port (default: 8000)")
    parser.add_argument("--speak", action="store_true", help="Also read each translated question aloud (offline TTS via pyttsx3)")
    args = parser.parse_args()

    try:
        asyncio.run(listen(args.session_id, args.host, args.port, args.speak))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
