"""HTTP layer (FastAPI). Mount into any app:

    from dwaniforms.api import create_router
    app.include_router(create_router(FormService(asr, translator)))
    # and, so idle sessions are purged even when nobody starts a new one:
    asyncio.create_task(service.sweep_forever())     # see standalone.py (lifespan)

Everything stays on the local machine; there are no outbound calls.

Privacy defaults: sensitive fields (Aadhaar, account, PAN) are MASKED in every JSON response unless the request sends
`X-Reveal-Sensitive: 1`; the PDF is masked unless `?mask=false`; the JSON export (meant for portal import) is full unless
`?mask=true`. Request bodies must be application/json and the audio endpoint requires an `X-Sample-Rate` header, so a
web page on another origin cannot drive this API with a simple cross-site form/fetch.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from .export import to_json, to_pdf
from .service import FormService

STATIC = Path(__file__).parent / "static"
MAX_AUDIO_BYTES = 6_000_000            # ~3 minutes of 16 kHz mono PCM16; an answer is a few seconds


class NewSession(BaseModel):
    form_id: str = Field(max_length=64)
    lang: str = Field("hi", pattern=r"^[a-z]{2,3}$")
    prefill_from: str | None = Field(None, max_length=32, pattern=r"^[0-9a-f]+$")   # a finished scheme-advisor session


class TextIn(BaseModel):
    text: str = Field(max_length=2000)


class RedoIn(BaseModel):
    field_id: str = Field(max_length=64)


class OperatorIn(BaseModel):
    field_id: str = Field(max_length=64)
    value: str = Field(max_length=500)


def _reply(r) -> dict:
    return {"text": r.text, "field_id": r.field_id, "phase": r.phase, "done": r.done, "pending": r.pending,
            "machine_translated": r.machine_translated, "outcome": r.outcome}


def _reveal(request: Request) -> bool:
    return request.headers.get("x-reveal-sensitive") == "1"


def _resp(s, request: Request, reply=None, **extra) -> dict:
    out = _reply(reply) if reply is not None else {}
    if reply is not None and reply.pending and not _reveal(request) and reply.field_id:
        # The form sheet pencils the pending answer in. Aadhaar / account / PAN show only their last 4 characters.
        from .session import mask_value
        if s.template.field_by_id(reply.field_id).sensitive:
            out["pending"] = mask_value(reply.pending)
            # The read-back is SPOKEN in full (the citizen confirms by ear) but SHOWN masked, like the form sheet.
            import re as _re
            out["speak"] = reply.text
            out["text"] = _re.sub(r"(?:[0-9A-Za-z]\s+){7,}[0-9A-Za-z]|[0-9A-Za-z]{8,}",
                                  lambda m: mask_value(m.group(0).replace(" ", "")), reply.text)
    return {**out, "english_fallback": s.english_fallback, "fields": _sheet(s, s.form_values(mask=not _reveal(request))),
            "flow": s.template.flow, "title": s.template.title_in(s.lang), **({"outcome": s.outcome} if s.outcome else {}), **extra}


import time as _time

MAX_SESSIONS = 300          # open conversations at once
NEW_PER_MINUTE = 20         # new forms per address per minute
_new_by_ip: dict = {}


def _sheet(s, rows: list[dict]) -> list[dict]:
    """Extra per-field detail the form-sheet UI draws (the citizen's-language label, checkbox options).
    Exports keep using form_values() unchanged."""
    for r in rows:
        f = s.template.field_by_id(r["id"])
        r["label_local"] = f.label_in(s.lang) or ""
        r["required"] = f.required
        if f.options:
            r["options"] = [{"value": o.value,
                             "label": o.value if s.lang == "en" else (o.synonyms.get(s.lang) or [o.value])[0]}
                            for o in f.options]
    return rows


def create_router(service: FormService, prefix: str = "/form") -> APIRouter:
    router = APIRouter(prefix=prefix)

    def session(sid: str):
        try:
            return service.get(sid)
        except KeyError:
            raise HTTPException(404, "unknown or expired session")

    @router.get("/")
    async def ui():
        return FileResponse(STATIC / "app.html", headers={"Cache-Control": "no-cache"})

    @router.get("/assets/{kind}/{name}")
    async def asset(kind: str, name: str):
        """Logo and fonts, bundled so a kiosk with no internet still looks right."""
        import re as _re
        if kind not in ("fonts", "img") or not _re.fullmatch(r"[\w.-]+", name):
            raise HTTPException(404)
        p = (STATIC / kind / name).resolve()
        if STATIC.resolve() not in p.parents or not p.is_file():
            raise HTTPException(404)
        return FileResponse(p, headers={"Cache-Control": "public, max-age=86400"})

    @router.get("/forms")
    async def forms():
        return service.forms()

    @router.get("/capabilities")
    async def capabilities():
        return service.capabilities()

    @router.get("/schemes")
    async def schemes():
        from .eligibility import load_schemes, stale
        return [{"id": x["id"], "name": x["name"], "source": x["source"], "last_reviewed": x["last_reviewed"],
                 "stale": stale(x)} for x in load_schemes()]

    @router.post("/sessions")
    async def create(body: NewSession, request: Request):
        # Online (people using it from home) the server is shared: cap open sessions and new sessions per address.
        service.purge_expired()
        if len(service.sessions) >= MAX_SESSIONS:
            raise HTTPException(503, "busy")
        ip = (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
              or (request.client.host if request.client else "?"))
        now = _time.time()
        recent = [t for t in _new_by_ip.get(ip, []) if now - t < 60]
        if len(recent) >= NEW_PER_MINUTE:
            raise HTTPException(429, "too many")
        _new_by_ip[ip] = recent + [now]
        if len(_new_by_ip) > 5000:
            _new_by_ip.clear()
        try:
            s, reply = await service.create(body.form_id, body.lang, body.prefill_from)
        except KeyError:
            raise HTTPException(404, "unknown form")
        return _resp(s, request, reply, session_id=s.id)

    @router.post("/sessions/{sid}/text")
    async def text(sid: str, body: TextIn, request: Request):
        s = session(sid)
        return _resp(s, request, await service.answer_text(sid, body.text))

    @router.post("/sessions/{sid}/audio")
    async def audio(sid: str, request: Request):
        s = session(sid)
        raw_rate = request.headers.get("x-sample-rate")
        if raw_rate is None:
            raise HTTPException(400, "X-Sample-Rate header required")
        try:
            rate = int(raw_rate)
        except ValueError:
            raise HTTPException(400, "bad X-Sample-Rate")
        if not 8_000 <= rate <= 96_000:
            raise HTTPException(400, "X-Sample-Rate out of range")
        if int(request.headers.get("content-length") or 0) > MAX_AUDIO_BYTES:
            raise HTTPException(413, "audio too large")
        data = await request.body()
        if len(data) > MAX_AUDIO_BYTES:
            raise HTTPException(413, "audio too large")
        try:
            transcript, reply = await service.answer_audio(sid, data, rate)
        except RuntimeError as e:
            raise HTTPException(422, str(e))
        return _resp(s, request, reply, transcript=transcript)

    @router.post("/sessions/{sid}/redo")
    async def redo(sid: str, body: RedoIn, request: Request):
        s = session(sid)
        try:
            reply = await service.finish(s, await s.redo(body.field_id))
        except KeyError:
            raise HTTPException(422, "unknown field")
        return _resp(s, request, reply)

    @router.post("/sessions/{sid}/operator")
    async def operator(sid: str, body: OperatorIn, request: Request):
        s = session(sid)
        try:
            ok, err = s.operator_set(body.field_id, body.value)
        except KeyError:
            raise HTTPException(422, "unknown field")
        if not ok:
            raise HTTPException(422, err)
        return _resp(s, request, await service.finish(s, await s.ask_current()))

    @router.get("/sessions/{sid}")
    async def state(sid: str, request: Request):
        s = session(sid)
        return _resp(s, request, complete=s.complete(), phase=s.phase)

    @router.get("/sessions/{sid}/export.json")
    async def export_json(sid: str, mask: bool = False):
        return Response(to_json(session(sid), mask_sensitive=mask), media_type="application/json")

    @router.get("/sessions/{sid}/export.pdf")
    async def export_pdf(sid: str, mask: bool = True):
        return Response(to_pdf(session(sid), mask_sensitive=mask), media_type="application/pdf",
                        headers={"Content-Disposition": f'inline; filename="{sid}.pdf"'})

    @router.delete("/sessions/{sid}")
    async def delete(sid: str):
        service.delete(sid)
        return JSONResponse({"deleted": True})

    return router
