"""
Presenter mic streaming, no browser involved.

Captures your microphone directly via sounddevice/PortAudio and streams it
to the running server's /host-ws endpoint over a plain WebSocket -- this
replaces static/host.html entirely for testing, since it sidesteps browser
getUserMedia permissions (site settings, insecure-origin blocks, etc.)
which can be unpredictable on locked-down Windows setups.

Setup:
    pip install sounddevice websockets

Usage (server must already be running):
    python presenter_mic_stream.py <session_id>

    <session_id> is whatever server.py printed when you started it
    (e.g. "a1b2c3d4"). If you started the server on a different port than
    8000 or on another machine, pass --port / --host too:

    python presenter_mic_stream.py a1b2c3d4 --host 192.168.1.42 --port 8000

Press Ctrl+C to stop presenting.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import numpy as np
import sounddevice as sd
import websockets

from backends import resample_linear

TARGET_SAMPLE_RATE = 16_000
BLOCK_SIZE = 4096  # samples per callback, at the device's native rate


async def stream_mic(session_id: str, host: str, port: int) -> None:
    device_info = sd.query_devices(kind="input")
    native_rate = int(device_info["default_samplerate"])
    print(f"Using input device: {device_info['name']!r} (native rate {native_rate} Hz)")

    uri = f"ws://{host}:{port}/host-ws?session_param={session_id}"
    print(f"Connecting to {uri} ...")

    loop = asyncio.get_event_loop()
    queue: asyncio.Queue[np.ndarray] = asyncio.Queue()

    def callback(indata: np.ndarray, frames: int, time_info, status) -> None:
        if status:
            print(f"[sounddevice warning] {status}", file=sys.stderr)
        mono = indata[:, 0].copy()
        loop.call_soon_threadsafe(queue.put_nowait, mono)

    try:
        async with websockets.connect(uri) as ws:
            print("Connected. Streaming -- speak now. Press Ctrl+C to stop.\n")
            with sd.InputStream(
                channels=1,
                samplerate=native_rate,
                blocksize=BLOCK_SIZE,
                dtype="float32",
                callback=callback,
            ):
                while True:
                    chunk = await queue.get()
                    resampled = resample_linear(chunk, native_rate, TARGET_SAMPLE_RATE)
                    await ws.send(resampled.tobytes())
    except websockets.exceptions.ConnectionClosedError as e:
        print(f"\nServer closed the connection: code={e.code} reason={e.reason!r}")
        if e.code == 4000:
            print("-> The session ID doesn't match what the server currently has. "
                  "Copy the exact ID printed when you started server.py.")
        elif e.code == 4001:
            print("-> Another presenter stream is already connected for this session.")
    except (ConnectionRefusedError, OSError) as e:
        print(f"\nCouldn't reach the server at {uri}: {e}")
        print("-> Is server.py actually running, and is --host/--port correct?")
    except KeyboardInterrupt:
        print("\nStopped presenting.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Stream your microphone to the LDST server, no browser required.")
    parser.add_argument("session_id", help="Session ID printed by server.py on startup")
    parser.add_argument("--host", default="localhost", help="Server host (default: localhost)")
    parser.add_argument("--port", type=int, default=8000, help="Server port (default: 8000)")
    args = parser.parse_args()

    try:
        asyncio.run(stream_mic(args.session_id, args.host, args.port))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()