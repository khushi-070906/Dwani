"""
Single-terminal launcher: starts server.py as a background process (its logs
still stream straight into this same terminal window) and, once it's actually
up and accepting connections, starts streaming the presenter's mic into it --
no second terminal window needed.

Usage: pass it exactly the flags you'd give server.py directly, e.g.

    python run.py --whisper-model small --nllb-model-dir nllb-200-ct2 --semantic-cache

    python run.py --whisper-model small --nllb-model-dir nllb-200-ct2 --itde \
        --cache-storage-path memory/cache.json --persistent-glossary-path memory/glossary.json \
        --dynamic-glossary --dynamic-glossary-interval 5

Ctrl+C at any point -- during startup or once streaming -- reliably stops the
server subprocess too; see _stop_server()'s docstring for why this needed
fixing (an earlier version could leak an orphaned server.py process if you
Ctrl+C'd during the startup wait specifically).
"""

from __future__ import annotations

import asyncio
import os
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from urllib.parse import parse_qs, urlparse

from presenter_mic import stream_mic

JOIN_URL_RE = re.compile(r"Join URL:\s*(http://\S+)")


def _stream_server_output(proc: subprocess.Popen, found: dict) -> None:
    """Echoes the server subprocess's stdout into this terminal line by line
    (so you see exactly what you'd see running it in its own window) and
    watches for the 'Join URL: ...' line session.py prints, to pull out the
    port and session ID it actually ended up using -- both of which can
    differ from what was requested (port auto-increments on conflict; the
    session ID is randomly generated unless --session-id was passed).

    session.py's announce() always prints this exact line now, regardless of
    how many network interfaces the machine has -- it used to be conditional
    on having exactly one interface, which meant this regex silently never
    matched (and run.py hung until its own timeout) on any machine with a
    VPN adapter, virtual adapter, or more than one NIC. If you're still not
    seeing this line matched, confirm your session.py has that fix.
    """
    for line in proc.stdout:
        print(line, end="")
        if "session_id" not in found:
            m = JOIN_URL_RE.search(line)
            if m:
                parsed = urlparse(m.group(1))
                found["port"] = parsed.port
                found["session_id"] = parse_qs(parsed.query).get("session", [None])[0]


def _wait_for_health(port: int, timeout: float = 180.0) -> None:
    """Blocks until GET /health succeeds. The join URL is printed by
    session.announce()/announce_with_hotspot() *before* uvicorn.run() is
    actually called in server.py's __main__ block, so there's a real race
    between "URL printed" and "server actually accepting connections" --
    this closes it properly instead of guessing a fixed sleep(). 180s
    default, not 60s: loading real Whisper + NLLB-200 weights on CPU can
    legitimately take that long on its own, before any WebSocket is ready
    to accept connections."""
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/health"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2):
                return
        except OSError:
            time.sleep(0.5)
    raise TimeoutError(f"Server never came up at {url} within {timeout}s")


def _stop_server(proc: subprocess.Popen) -> None:
    """Single cleanup path, used from every exit branch in main() below.
    This is the fix for the orphaned-process bug: previously,
    proc.terminate()/kill() calls were scattered across individual error
    branches, so any exit path nobody had explicitly covered -- a Ctrl+C
    landing mid-time.sleep(0.2) in the startup wait loop, in particular --
    left the child server.py process running indefinitely, still holding
    its port and still consuming CPU loading models. That orphan then
    starved the *next* run.py invocation's server of both, which is exactly
    what a "server won't start / times out" report after an earlier Ctrl+C
    usually turns out to be.
    """
    if proc.poll() is not None:
        return  # already exited
    print("\n[run.py] Stopping server...")
    try:
        if sys.platform == "win32":
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            proc.send_signal(signal.SIGINT)
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        print("[run.py] Server didn't stop in time -- killing it.")
        proc.kill()
    except ProcessLookupError:
        pass  # already exited


def main() -> None:
    server_args = sys.argv[1:]  # forwarded straight through to server.py, unchanged

    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    proc = subprocess.Popen(
        # "-u": unbuffered stdout/stderr. Without this, Python detects its
        # stdout isn't a real terminal (it's a pipe, since we redirect it
        # below) and switches from line-buffered to block-buffered output --
        # server.py's own print() calls (Session ID, Join URL, QR code,
        # hotspot info) then sit in an internal buffer and never reach this
        # process's reader thread until that buffer fills or the process
        # exits, while uvicorn's separately-configured logging output still
        # gets through fine. That asymmetry -- uvicorn's INFO lines visible,
        # every plain print() from server.py silently missing -- is the
        # signature of this exact bug, and bufsize=1 below does NOT fix it:
        # that only controls how *this* process reads the pipe, not how the
        # *child* buffers what it writes into it.
        [sys.executable, "-u", "server.py", *server_args],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},  # belt-and-suspenders alongside -u
        creationflags=creationflags,
    )

    # Everything from here on -- waiting for the join URL, waiting for
    # /health, streaming the mic -- is wrapped in one try/finally so ANY
    # exit path (Ctrl+C at any point, a timeout, an unparseable session ID,
    # server.py exiting early) reliably reaches _stop_server() rather than
    # leaking the subprocess.
    try:
        found: dict = {}
        reader = threading.Thread(target=_stream_server_output, args=(proc, found), daemon=True)
        reader.start()

        print("\n[run.py] Starting server.py, waiting for it to print its join URL...\n")
        deadline = time.monotonic() + 180.0
        while "session_id" not in found:
            if proc.poll() is not None:
                raise SystemExit(f"\n[run.py] server.py exited early (code {proc.returncode}) -- see output above.")
            if time.monotonic() > deadline:
                raise SystemExit("\n[run.py] Timed out waiting for the server to start.")
            time.sleep(0.2)

        port, session_id = found["port"], found["session_id"]
        if not session_id:
            raise SystemExit("\n[run.py] Couldn't parse a session ID out of the server's output.")

        print(f"\n[run.py] Waiting for the server to finish starting up (port {port})...")
        _wait_for_health(port)

        # Always connect over localhost/loopback for the mic stream -- run.py and
        # server.py are on the same machine by construction, regardless of
        # whether the server ended up broadcasting its own hotspot or falling
        # back to venue WiFi, so there's no reason to route the mic connection
        # out through either of those.
        print(f"[run.py] Server is up. Starting presenter mic stream (session {session_id}) -- speak now, Ctrl+C to stop.\n")
        try:
            asyncio.run(stream_mic(session_id, "127.0.0.1", port))
        except KeyboardInterrupt:
            pass
    finally:
        _stop_server(proc)


if __name__ == "__main__":
    main()