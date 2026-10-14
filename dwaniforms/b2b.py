"""The company API: DwaniForms services as plain HTTP calls, for a platform that has its own screens.

    GET  /api/v1/services             what this key may use, with every field of each service
    POST /api/v1/{service_id}/draft   fields in -> the finished draft / explanation / advice out (or what is missing)
    GET  /api/v1/usage                the key's own limits and use
    GET  /api/v1/openapi.json         machine-readable description

Mounted (standalone.py) only when DWANIFORMS_KEY_SECRET is set. Every call needs a key (apikeys.py): header
`Authorization: Bearer dfk_...` or `X-API-Key: dfk_...`. Server-to-server: there is no CORS, so a key placed in a web
page would not work (and must not be put there).

How it treats the data it is given -- the platform's customers' names and addresses:
  * one call = one request and one response. Nothing is kept: no session is opened, nothing is written to disk, and
    the request body is never logged (the usage log, if switched on, holds the key id, the service id and a status);
  * answers are validated by the same code the voice app uses (Aadhaar checksum, dates, PIN, choices), so a platform
    gets the same drafts, the same next steps and the same deadlines;
  * a key can be limited to some services, has an end date, a size (finished drafts) and a per-minute rate.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import defaultdict, deque

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as HTTPError
from pydantic import BaseModel, Field

from . import address as addr_mod
from . import apikeys, clock, names, spoken
from .hardening import RateLimiter, client_ip
from .messages import MESSAGES
from .session import FormSession

log = logging.getLogger("dwaniforms.api")

MAX_BODY = 64_000
MAX_FIELDS = 60
MAX_VALUE = 2000
INTERNAL = {"say_key", "say_kw", "say_text", "say_more", "disclaimer_key"}      # voice-only parts of an outcome

# what to tell a developer (the voice app's own messages say "Say day, month and year")
PROBLEMS = {
    "date_invalid": "Not a valid date. Send YYYY-MM-DD.", "date_future": "This date cannot be in the future.",
    "age_low": "The age on this date is below the minimum for this form.", "age_high": "The age on this date is above the maximum for this form.",
    "aadhaar_length": "An Aadhaar number has 12 digits.", "aadhaar_invalid": "Not a valid Aadhaar number (checksum fails).",
    "mobile_length": "A mobile number has 10 digits.", "mobile_invalid": "Not a valid Indian mobile number (starts with 6-9).",
    "pin_invalid": "A PIN code has 6 digits.", "account_invalid": "Not a valid bank account number.", "pan_invalid": "Not a valid PAN (ABCDE1234F).",
    "ifsc_invalid": "Not a valid IFSC code.", "number_invalid": "Not a number.", "number_whole": "Send a whole number.",
    "number_range": "The number is outside the allowed range.", "choice_invalid": "Not one of the listed options (see `options` in /services).",
    "yesno_invalid": "Send Yes or No.", "text_short": "Too short: write a few words.", "khasra_invalid": "Not a valid khasra number.",
    "ration_invalid": "Not a valid ration card number.", "unit_acres": "Send the area in acres.",
}
PARAMS = ("min_age", "max_age", "minimum", "maximum", "min_length", "decimals", "future", "unit")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, headers: dict | None = None):
        self.status, self.code, self.message, self.headers = status, code, message, headers or {}


class Meter:
    """In-memory use per key: requests a minute, finished drafts. Resets when the server restarts -- the signed key still
    carries its end date, but the size of a trial is best-effort until a durable store is added (see API.md).
    DWANIFORMS_USAGE_LOG=<file> appends one JSON line per draft call (key id, service id, status: no personal data) for
    invoicing; on a host whose disk is wiped on deploy, keep it on a persistent disk."""

    def __init__(self, log_path: str | None = None):
        self.log_path = log_path if log_path is not None else os.environ.get("DWANIFORMS_USAGE_LOG", "")
        self.done: dict[str, int] = defaultdict(int)
        self.recent: dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def rate(self, k: apikeys.Key, now: float | None = None) -> tuple[bool, int]:
        now = time.monotonic() if now is None else now
        with self.lock:
            q = self.recent[k.id]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= k.rpm:
                return False, int(60 - (now - q[0])) + 1
            q.append(now)
            return True, 0

    def left(self, k: apikeys.Key) -> int:
        return max(0, k.quota - self.done[k.id])

    def finished(self, k: apikeys.Key) -> None:
        with self.lock:
            self.done[k.id] += 1

    def record(self, k: apikeys.Key, service: str, status: int, complete: bool) -> None:
        if not self.log_path:
            return
        line = json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "key": k.id, "org": k.org,
                           "service": service, "status": status, "complete": complete})
        try:
            with self.lock, open(self.log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError as e:
            log.warning("usage log not written: %s", type(e).__name__)


class NamesIn(BaseModel):
    a: str = Field(max_length=200)
    b: str = Field(max_length=200)


class AddressIn(BaseModel):
    address: str = Field(max_length=1000)


class SpeechIn(BaseModel):
    text: str = Field(max_length=500)
    kind: str = Field(default="auto", pattern="^(auto|number|date|digits)$")


class DraftIn(BaseModel):
    lang: str = Field("en", pattern=r"^[a-z]{2,3}$")
    fields: dict = Field(default_factory=dict)


def _text(v) -> str | None:
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, (int, float)):
        return str(v)
    return v if isinstance(v, str) else None


def field_spec(f) -> dict:
    out = {"id": f.id, "label": f.label, "kind": f.kind, "required": f.required, "sensitive": f.sensitive}
    if f.options:
        out["options"] = [o.value for o in f.options]
    if f.when:
        out["when"] = f.when
    params = {k: v for k, v in f.params.items() if k in PARAMS}
    if params:
        out["params"] = params
    return out


async def run_draft(service, template, lang: str, raw: dict) -> dict:
    """Validate the given fields in the template's own order (so `when` rules see earlier answers), then build the outcome."""
    s = FormSession(template, lang, None, None)
    errors, ignored, unknown = {}, [], [k for k in raw if k not in {f.id for f in template.fields}]
    for k in unknown:
        errors[k] = {"code": "unknown_field", "message": "This service has no such field (see /services)."}
    for f in template.fields:
        if f.id not in raw:
            continue
        value = _text(raw[f.id])
        if value is None:
            errors[f.id] = {"code": "bad_type", "message": "Send a string, a number or true/false."}
        elif not s.applicable(f):
            ignored.append(f.id)
        elif value.strip() == "" and not f.required:
            continue
        else:
            ok, err = s.operator_set(f.id, value)
            if not ok:
                errors[f.id] = {"code": err, "message": PROBLEMS.get(err) or MESSAGES["en"].get(err, "Not accepted.")}
    missing = [f.id for f in template.fields if f.required and s.applicable(f) and f.id not in s.answers and f.id not in errors]
    values = {f.id: s.answers[f.id].value for f in template.fields if f.id in s.answers}
    res = {"complete": not errors and not missing, "values": values, "missing": missing, "errors": errors, "ignored": ignored}
    if res["complete"]:
        from .service import FLOW_OUTCOMES
        build = FLOW_OUTCOMES.get(template.flow)
        if build is not None:
            out = build(s, service=service)
            res["outcome"] = {k: v for k, v in out.items() if k not in INTERNAL}
    return res


def build_api(service, meter: Meter | None = None) -> FastAPI:
    from .schema import load_all  # noqa: F401  (templates come from the service)

    api = FastAPI(title="DwaniForms API", version="1.0", docs_url=None, redoc_url=None,
                  description="Voice-first government-form services as a company API. See dwaniforms/API.md.")
    meter = meter or Meter()
    api.state.meter = meter
    bad_tries = RateLimiter({"bad": (20, 60)})

    # ---- one error shape everywhere: {"error": {"code", "message"}} -------------------------------------------------
    @api.exception_handler(ApiError)
    async def _api_error(_r: Request, e: ApiError):
        return JSONResponse({"error": {"code": e.code, "message": e.message}}, status_code=e.status,
                            headers={"Cache-Control": "no-store", **e.headers})

    @api.exception_handler(RequestValidationError)
    async def _bad_request(_r: Request, e: RequestValidationError):
        # the details of what was wrong, but never the values that were sent
        where = ".".join(str(x) for x in (e.errors()[0]["loc"][1:] if e.errors() else ())) or "body"
        return JSONResponse({"error": {"code": "invalid_request", "message": f"The request is not valid ({where})."}},
                            status_code=422, headers={"Cache-Control": "no-store"})

    @api.exception_handler(HTTPError)
    async def _http(_r: Request, e: HTTPError):
        code = {404: "not_found", 405: "method_not_allowed"}.get(e.status_code, "error")
        return JSONResponse({"error": {"code": code, "message": "No such address or method. See /openapi.json."}},
                            status_code=e.status_code, headers={"Cache-Control": "no-store"})

    @api.exception_handler(Exception)
    async def _crash(_r: Request, e: Exception):
        log.error("api error: %s", type(e).__name__)             # the kind of error only: the body holds personal data
        return JSONResponse({"error": {"code": "server_error", "message": "Something went wrong on our side."}},
                            status_code=500, headers={"Cache-Control": "no-store"})

    # ---- who is calling ---------------------------------------------------------------------------------------------
    def authenticate(request: Request) -> apikeys.Key:
        raw = request.headers.get("x-api-key", "")
        auth = request.headers.get("authorization", "")
        if not raw and auth[:7].lower() == "bearer ":
            raw = auth[7:].strip()
        ip = client_ip(request)
        try:
            if not raw:
                raise apikeys.KeyError_("missing")
            key = apikeys.verify(raw)
        except apikeys.KeyError_ as e:
            ok, retry = bad_tries.allow("bad", ip)             # someone guessing keys is slowed down, not told anything extra
            if not ok:
                raise ApiError(429, "too_many_attempts", "Too many bad keys. Wait a minute.", {"Retry-After": str(retry)}) from None
            msg = {"missing": "Send your key as `Authorization: Bearer dfk_...` or `X-API-Key`.",
                   "expired": "This key has expired. Ask us for a new one.", "revoked": "This key has been switched off.",
                   "disabled": "The company API is not switched on."}.get(e.code, "That key is not valid.")
            raise ApiError(401, {"missing": "missing_key", "expired": "key_expired", "revoked": "key_revoked"}.get(e.code, "invalid_key"), msg,
                           {"WWW-Authenticate": "Bearer"}) from None
        ok, retry = meter.rate(key)
        if not ok:
            raise ApiError(429, "rate_limited", f"At most {key.rpm} requests a minute for this key.", {"Retry-After": str(retry)})
        return key

    def stamp(response: Response, k: apikeys.Key) -> None:
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Key-Expires"] = k.expires_on()
        response.headers["X-Quota-Remaining"] = str(meter.left(k))

    def offered(k: apikeys.Key) -> dict:
        return {t.id: t for t in service.templates.values() if t.flow in apikeys.GROUPS and k.allows(t.id, t.flow)}

    # ---- the calls --------------------------------------------------------------------------------------------------
    @api.get("/services")
    async def services(request: Request, response: Response):
        k = authenticate(request)
        stamp(response, k)
        return {"services": [{"id": t.id, "service": apikeys.GROUPS[t.flow], "title": t.title,
                              "description": t.description.get("en", ""), "fields": [field_spec(f) for f in t.fields]}
                             for t in offered(k).values()],
                "languages": service.capabilities()["langs"]}

    @api.get("/usage")
    async def usage(request: Request, response: Response):
        k = authenticate(request)
        stamp(response, k)
        return {"organisation": k.org, "key_id": k.id, "services": list(k.services), "expires": k.expires_on(),
                "days_left": k.days_left(), "quota": k.quota, "finished_drafts": meter.done[k.id], "remaining": meter.left(k),
                "requests_per_minute": k.rpm,
                "note": "Counts restart if the server restarts; the end date does not."}

    async def read_json(request: Request, model):
        if int(request.headers.get("content-length") or 0) > MAX_BODY:
            raise ApiError(413, "payload_too_large", "At most 64 KB.")
        body = bytearray()
        async for chunk in request.stream():
            body += chunk
            if len(body) > MAX_BODY:
                raise ApiError(413, "payload_too_large", "At most 64 KB.")
        try:
            return model.model_validate_json(bytes(body) or b"{}")
        except ValueError:
            raise ApiError(422, "invalid_request", "The body is not the JSON this call expects (see /openapi.json).") from None

    def tool(request: Request, response: Response, name: str) -> apikeys.Key:
        k = authenticate(request)
        stamp(response, k)
        if not k.allows_tool(name):
            raise ApiError(403, "service_not_in_plan", "This key does not include that service.")
        return k

    @api.post("/names/match")
    async def names_match(request: Request, response: Response):
        k = tool(request, response, "names")
        d = await read_json(request, NamesIn)
        meter.record(k, "names", 200, False)
        return names.match(d.a, d.b)

    @api.post("/address/parse")
    async def address_parse(request: Request, response: Response):
        k = tool(request, response, "address")
        d = await read_json(request, AddressIn)
        meter.record(k, "address", 200, False)
        p = addr_mod.parse(d.address)
        return {"parts": p, "formatted": addr_mod.format_address(p), "missing": addr_mod.missing(p)}

    @api.post("/speech/parse")
    async def speech_parse(request: Request, response: Response):
        k = tool(request, response, "speech")
        d = await read_json(request, SpeechIn)
        meter.record(k, "speech", 200, False)
        out: dict = {}
        if d.kind in ("auto", "date"):
            dt_ = spoken.spoken_to_date(d.text, clock.today())
            if dt_:
                out["date"] = {"day": dt_[0], "month": dt_[1], "year": dt_[2],
                               "dmy": f"{dt_[0]:02d}/{dt_[1]:02d}/{dt_[2]}"}
        if d.kind == "number" or (d.kind == "auto" and "date" not in out):
            n = spoken.spoken_to_number(d.text)
            if n is not None:
                out["number"] = float(n) if n != int(n) else int(n)
        if d.kind == "digits" or (d.kind == "auto" and "date" not in out):
            digits = spoken.spoken_to_digits(d.text)
            if digits:
                out["digits"] = digits
        return {"input_length": len(d.text), **out}

    @api.post("/{service_id}/draft")
    async def draft(service_id: str, request: Request, response: Response):
        k = authenticate(request)
        stamp(response, k)
        t = service.templates.get(service_id)
        if t is None or t.flow not in apikeys.GROUPS:
            raise ApiError(404, "unknown_service", "No such service. GET /services lists yours.")
        if not k.allows(t.id, t.flow):
            raise ApiError(403, "service_not_in_plan", "This key does not include that service.")
        if int(request.headers.get("content-length") or 0) > MAX_BODY:
            raise ApiError(413, "payload_too_large", "At most 64 KB.")
        body = bytearray()
        async for chunk in request.stream():
            body += chunk
            if len(body) > MAX_BODY:
                raise ApiError(413, "payload_too_large", "At most 64 KB.")
        try:
            data = DraftIn.model_validate_json(bytes(body) or b"{}")
        except ValueError:
            raise ApiError(422, "invalid_request", "The body must be JSON: {\"lang\": \"en\", \"fields\": {...}}.") from None
        if len(data.fields) > MAX_FIELDS or any(len(str(v)) > MAX_VALUE for v in data.fields.values()):
            raise ApiError(422, "invalid_request", "Too many fields, or a value is too long.")
        if data.lang not in service.capabilities()["langs"]:
            raise ApiError(422, "unsupported_language", "Languages: " + ", ".join(service.capabilities()["langs"]))
        if meter.left(k) <= 0:
            raise ApiError(429, "quota_used", f"This key's {k.quota} finished drafts are used. Ask us to extend it.")
        res = await run_draft(service, t, data.lang, data.fields)
        if res["complete"]:
            meter.finished(k)
            service._count("finished", t.id)
        meter.record(k, t.id, 200, res["complete"])
        stamp(response, k)
        return {"service_id": t.id, "service": apikeys.GROUPS[t.flow], "language": data.lang, **res,
                "usage": {"remaining": meter.left(k), "expires": k.expires_on()}}

    return api
