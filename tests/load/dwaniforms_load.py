"""Load test for the online DwaniForms app: N people filling a form at the same time.

    python tests/load/dwaniforms_load.py http://127.0.0.1:8100 --users 50
    (run the server with DWANIFORMS_TRUST_PROXY=1 so each simulated person gets their own IP and the
     per-IP rate limit applies per person, as it would in real use)

Reports latency per answer (p50 / p95 / max), errors, and requests per second. Never point this at someone
else's server.
"""
import argparse
import asyncio
import random
import statistics
import time

import httpx

ANSWERS = ["Ramesh Kumar", "हाँ", "Shyam Lal", "हाँ", "15 August 1980", "हाँ", "पुरुष", "ओबीसी", "98765 43210", "हाँ",
           "Sonpur", "हाँ", "Sitapur", "हाँ"]


async def person(client: httpx.AsyncClient, base: str, n: int, lat: list, errs: list):
    ip = f"10.{n // 250}.{n % 250}.{random.randint(1, 250)}"
    h = {"x-forwarded-for": ip}
    t = time.perf_counter()
    r = await client.post(f"{base}/form/sessions", json={"form_id": "pm_kisan", "lang": "hi"}, headers=h)
    lat.append(time.perf_counter() - t)
    if r.status_code != 200:
        errs.append(r.status_code); return
    sid = r.json()["session_id"]
    for a in ANSWERS:
        await asyncio.sleep(random.uniform(0.05, 0.3))          # people take a moment between answers
        t = time.perf_counter()
        r = await client.post(f"{base}/form/sessions/{sid}/text", json={"text": a}, headers=h)
        lat.append(time.perf_counter() - t)
        if r.status_code != 200:
            errs.append(r.status_code)
    await client.delete(f"{base}/form/sessions/{sid}", headers=h)


async def main(base: str, users: int):
    lat, errs = [], []
    t0 = time.perf_counter()
    async with httpx.AsyncClient(timeout=30) as client:
        await asyncio.gather(*(person(client, base, i, lat, errs) for i in range(users)))
    wall = time.perf_counter() - t0
    lat.sort()
    p = lambda q: lat[min(len(lat) - 1, int(q * len(lat)))] * 1000
    print(f"{users} people, {len(lat)} requests in {wall:.1f}s ({len(lat) / wall:.0f} req/s)")
    print(f"latency ms: p50 {p(.5):.0f}  p95 {p(.95):.0f}  max {lat[-1] * 1000:.0f}  mean {statistics.mean(lat) * 1000:.0f}")
    print(f"errors: {len(errs)} {sorted(set(errs)) if errs else ''}")
    return 1 if errs else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("--users", type=int, default=50)
    a = ap.parse_args()
    raise SystemExit(asyncio.run(main(a.base.rstrip("/"), a.users)))
