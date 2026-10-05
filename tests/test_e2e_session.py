"""A whole session over real sockets: the actual server process (demo models),
a presenter streaming audio, two phones in different languages, Wi-Fi drops,
"Lost me", dashboard, notes, the seat limit. The closest automatic stand-in for
a real-room test; run it with `pytest tests/test_e2e_session.py -s`."""
import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import pytest

websockets = pytest.importorskip("websockets")
ROOT = Path(__file__).resolve().parent.parent
SID = "e2e123"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def srv(tmp_path_factory):
    port = free_port()
    notes = tmp_path_factory.mktemp("notes")
    env = dict(os.environ, DWANI_NOTES_DIR=str(notes), DWANI_GLOSSARY_PATH=str(notes / "g.json"), PYTHONUNBUFFERED="1")
    log = open(notes / "server.log", "w")
    p = subprocess.Popen([sys.executable, "server.py", "--port", str(port), "--session-id", SID],
                         cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    for _ in range(120):
        try:
            urllib.request.urlopen(base + "/presenter-info", timeout=1)
            break
        except Exception:
            time.sleep(0.25)
    else:
        p.kill()
        pytest.fail("server didn't start: " + (notes / "server.log").read_text()[-2000:])
    yield {"http": base, "ws": f"ws://127.0.0.1:{port}", "log": notes / "server.log"}
    p.terminate()
    p.wait(10)


def get(srv, path):
    try:
        r = urllib.request.urlopen(srv["http"] + path, timeout=10)
        return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def speech(sentences=3):
    """Voiced bursts separated by pauses, like a presenter talking."""
    t = np.arange(int(1.6 * 16000)) / 16000
    voiced = (0.25 * np.sin(2 * np.pi * 220 * t) * (1 + 0.3 * np.sin(2 * np.pi * 3 * t))).astype(np.float32)
    pause = np.zeros(int(1.2 * 16000), dtype=np.float32)
    return np.concatenate([np.concatenate([voiced, pause]) for _ in range(sentences)])


async def stream(host, audio):
    for i in range(0, len(audio), 4096):
        await host.send(audio[i:i + 4096].tobytes())
        await asyncio.sleep(0.005)


async def finals(ws, n, timeout=20):
    got = []
    end = time.monotonic() + timeout
    while len(got) < n and time.monotonic() < end:
        try:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=max(0.1, end - time.monotonic())))
        except asyncio.TimeoutError:
            break
        if msg.get("final") and msg.get("text"):
            got.append(msg["text"])
    return got


def test_whole_session(srv):
    status, body = get(srv, "/preflight")
    checks = {c["id"]: c for c in json.loads(body)["checks"]}
    assert status == 200 and {"asr", "translation", "network", "phones"} <= set(checks)
    assert checks["phones"]["status"] == "warn"                      # nobody joined yet

    async def run():
        w = srv["ws"]
        hi = await websockets.connect(f"{w}/ws?lang=hi&session_param={SID}")
        ta = await websockets.connect(f"{w}/ws?lang=ta&session_param={SID}")
        st = json.loads(get(srv, "/preflight")[1])
        assert next(c for c in st["checks"] if c["id"] == "phones")["status"] == "ok"   # phone test turns green

        host = await websockets.connect(f"{w}/host-ws?session_param={SID}")
        second = await websockets.connect(f"{w}/host-ws?session_param={SID}")   # another device tries to present
        with pytest.raises(websockets.ConnectionClosed) as exc:
            await asyncio.wait_for(second.recv(), 5)
        assert exc.value.rcvd.code >= 4000                           # rejected with a reason, not silently

        await stream(host, speech(3))
        got_hi, got_ta = await finals(hi, 3), await finals(ta, 3)
        assert len(got_hi) >= 2 and len(got_ta) >= 2, (got_hi, got_ta)
        assert got_hi[0] != got_ta[0]                                # each phone gets its own language
        print(f"\n  hindi phone got {len(got_hi)} captions, e.g. {got_hi[0]!r}\n  tamil phone got {len(got_ta)}, e.g. {got_ta[0]!r}")

        dash = json.loads(get(srv, "/dashboard-stats")[1])
        assert dash["attendees"] == 2 and dash["by_language"] == {"hi": 1, "ta": 1}
        assert dash["latency"]["count"] >= 2 and dash["latency"]["p50_ms"] >= 0
        print(f"  dashboard: {dash['attendees']} attendees {dash['by_language']}, caption delay p50 {dash['latency']['p50_ms']} ms")

        await hi.send(json.dumps({"type": "ping", "t": 7}))
        assert json.loads(await asyncio.wait_for(hi.recv(), 5)) == {"type": "pong", "t": 7}

        for phone in (hi, ta):                                       # both phones: "Lost me"
            await phone.send(json.dumps({"type": "confused"}))
            assert json.loads(await asyncio.wait_for(phone.recv(), 5))["counted"] is True
        conf = json.loads(get(srv, "/dashboard-stats")[1])["confusion"]
        assert conf["alert"] is True and conf["people"] == 2 and conf["about"]
        print(f"  lost-me banner: {conf['people']} of {conf['attendees']}, about {conf['about'][:40]!r}")

        tr = getattr(hi, "transport", None)                          # Wi-Fi drop: the socket just dies
        if tr is not None:
            tr.abort()
        else:
            await hi.close()
        await asyncio.sleep(0.5)
        hi = await websockets.connect(f"{w}/ws?lang=hi&session_param={SID}")      # phone reconnects
        await stream(host, speech(1))
        assert len(await finals(hi, 1)) == 1                         # captions resume after the drop

        # seat limit on the Free plan: 2 connected now, 18 more fit, the 21st is told it's full
        extra = [await websockets.connect(f"{w}/ws?lang=en&session_param={SID}") for _ in range(18)]
        over = await websockets.connect(f"{w}/ws?lang=en&session_param={SID}")
        with pytest.raises(websockets.ConnectionClosed) as exc:
            await asyncio.wait_for(over.recv(), 5)
        assert exc.value.rcvd.code == 4003 and "full" in exc.value.rcvd.reason.lower()
        print(f"  21st phone: closed {exc.value.rcvd.code} {exc.value.rcvd.reason!r}")
        bad = await websockets.connect(f"{w}/ws?lang=en&session_param=nope")
        with pytest.raises(websockets.ConnectionClosed) as exc:
            await asyncio.wait_for(bad.recv(), 5)
        assert exc.value.rcvd.code == 4000
        for s in extra + [hi, ta, host]:
            await s.close()

    asyncio.run(run())

    st = json.loads(get(srv, "/notes/status")[1])
    assert st["recording"] and st["captions"] >= 3 and st["plan"] == "free"
    sid = st["id"]
    status, txt = get(srv, f"/notes/export?id={sid}&format=txt")
    assert status == 200 and len(txt.strip().splitlines()) >= 4  # header + captions
    assert get(srv, f"/notes/export?id={sid}&format=notes")[0] == 402      # Pro on the Free plan
    sessions = json.loads(get(srv, "/notes/sessions")[1])
    assert sessions[0]["id"] == sid and set(sessions[0]["languages"]) >= {"hi", "ta"}
    print(f"  notes: {st['captions']} captions saved, languages {sessions[0]['languages']}; transcript:\n    " +
          "\n    ".join(txt.strip().splitlines()[:4]))
    for path in ("/help", "/static/i18n.js", "/host", f"/?session={SID}"):
        assert get(srv, path)[0] == 200, path
    log = srv["log"].read_text()
    assert "Traceback" not in log, log[-3000:]                    # no server-side crashes anywhere
