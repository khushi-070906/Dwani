"""Production hardening for the online DwaniForms app.

  * Rate limits per client IP: new conversations (a bot creating thousands would fill memory) and API calls.
  * Security headers on every response (CSP, no framing, no sniffing, HSTS behind HTTPS, mic only for this site).
  * /form/health: liveness for Render / uptime monitors. No personal data.
  * /form/metrics: anonymous counters for a pilot report (forms started / finished / where people stopped),
    behind DWANIFORMS_METRICS_TOKEN. Never answers, names or numbers: only form ids, field ids and counts.
  * /form/metrics/view: the same numbers as a page you can open on a phone. The token is typed into the page and
    sent as a header, so it never lands in a URL, a browser history or a server log.

Settings (environment):
  DWANIFORMS_TRUST_PROXY=1     read the client IP from X-Forwarded-For (set on Render, which sits behind a proxy)
  DWANIFORMS_RATE_LIMIT=0      switch rate limiting off (tests, load tests on a private machine)
  DWANIFORMS_METRICS_TOKEN=... enable /form/metrics for whoever sends this token
"""
from __future__ import annotations

import hmac
import os
import threading
import time
from collections import defaultdict, deque

from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse

# (requests, seconds) per client IP
LIMITS = {
    "new_session": (30, 600),     # 30 new conversations per 10 minutes per IP (a whole CSC queue fits)
    "api": (300, 60),             # 300 API calls per minute per IP
}

CSP = ("default-src 'self'; "
       "script-src 'self' 'unsafe-inline' blob:; "            # the page's own inline script + the mic AudioWorklet
       "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src 'self' data: https://fonts.gstatic.com; "
       "img-src 'self' data: blob:; media-src 'self' blob:; worker-src 'self' blob:; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'")


def client_ip(request: Request) -> str:
    if os.environ.get("DWANIFORMS_TRUST_PROXY") == "1":
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "?"


class RateLimiter:
    """Sliding-window counters, in memory (one instance; for several instances use a shared store)."""

    def __init__(self, limits: dict | None = None):
        self.limits = limits or LIMITS
        self.hits: dict[tuple[str, str], deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, bucket: str, key: str, now: float | None = None) -> tuple[bool, int]:
        n, window = self.limits[bucket]
        now = time.monotonic() if now is None else now
        with self.lock:
            q = self.hits[(bucket, key)]
            while q and now - q[0] > window:
                q.popleft()
            if len(q) >= n:
                return False, int(window - (now - q[0])) + 1
            q.append(now)
            if len(self.hits) > 50_000:                       # forget idle clients so the table can't grow forever
                for k in [k for k, v in self.hits.items() if not v][:10_000]:
                    del self.hits[k]
            return True, 0


class Metrics:
    """Anonymous counters. Field ids and form ids only -- never what anyone said."""

    def __init__(self):
        self.started = 0
        self.lock = threading.Lock()
        self.data: dict = {"started": defaultdict(int), "finished": defaultdict(int), "abandoned_at": defaultdict(int),
                           "retries": defaultdict(int)}
        self.since = time.time()

    def event(self, kind: str, form_id: str, field_id: str | None = None) -> None:
        key = f"{form_id}:{field_id}" if field_id else form_id
        with self.lock:
            self.data[kind][key] += 1

    def snapshot(self) -> dict:
        with self.lock:
            out = {k: dict(sorted(v.items(), key=lambda kv: -kv[1])) for k, v in self.data.items()}
        started = sum(out["started"].values())
        finished = sum(out["finished"].values())
        return {"since": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(self.since)),
                "totals": {"started": started, "finished": finished,
                           "completion_rate": round(finished / started, 3) if started else None},
                **out}


def _labels() -> dict:
    """Form and question names for the counter keys, so a pilot report reads "PM-Kisan · Aadhaar number" instead of
    "pm_kisan:aadhaar". Template metadata only -- nothing anyone typed or said."""
    from .schema import load_all
    out = {}
    for t in load_all().values():
        out[t.id] = t.title
        for f in t.fields:
            out[f"{t.id}:{f.id}"] = f.label
    return out


def install(app, service, prefix: str = "/form", version: str = "") -> None:
    limiter = RateLimiter()
    metrics = Metrics()
    service.metrics = metrics
    started_at = time.time()
    enabled = os.environ.get("DWANIFORMS_RATE_LIMIT", "1") != "0"

    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path
        if enabled and path.startswith(prefix) and not path.startswith(prefix + "/assets") and path not in (prefix + "/health",):
            ip = client_ip(request)
            buckets = ["api"] + (["new_session"] if request.method == "POST" and path == prefix + "/sessions" else [])
            for b in buckets:
                ok, retry = limiter.allow(b, ip)
                if not ok:
                    return JSONResponse({"detail": "Too many requests. Please wait a little and try again."},
                                        status_code=429, headers={"Retry-After": str(retry)})
        response = await call_next(request)
        h = response.headers
        h.setdefault("Content-Security-Policy", CSP)
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Referrer-Policy", "no-referrer")
        h.setdefault("Permissions-Policy", "microphone=(self), camera=(), geolocation=()")
        if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
            h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if path.startswith(prefix + "/sessions"):
            h.setdefault("Cache-Control", "no-store")            # answers must never sit in a shared cache
        return response

    @app.get(prefix + "/health", include_in_schema=False)
    async def health():
        return {"status": "ok", "version": version, "uptime_s": int(time.time() - started_at),
                "open_sessions": len(service.sessions)}

    def _authorised(request: Request) -> bool:
        token = os.environ.get("DWANIFORMS_METRICS_TOKEN", "")
        return bool(token) and hmac.compare_digest(request.headers.get("x-metrics-token", ""), token)

    @app.get(prefix + "/metrics", include_in_schema=False)
    async def metrics_view(request: Request):
        if not _authorised(request):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        return {**metrics.snapshot(), "open_sessions": len(service.sessions), "labels": _labels()}

    @app.get(prefix + "/metrics/view", include_in_schema=False)
    async def metrics_page():
        """The counters as a readable page. Opening it shows only a box asking for the token: the page holds no
        numbers of its own and fetches them with the token as a header."""
        if not os.environ.get("DWANIFORMS_METRICS_TOKEN", ""):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        from pathlib import Path as _Path
        return FileResponse(_Path(__file__).parent / "static" / "metrics.html",
                            headers={"Cache-Control": "no-store"})
