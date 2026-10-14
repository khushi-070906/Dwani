"""API keys for companies that use DwaniForms as a service (a trial key for 30 days, then a paid one).

A key is SIGNED, not looked up: everything the server needs to know about it (who it is for, which services it opens,
when it ends, how much it may use) is inside the key, and an HMAC with the server's secret proves nobody edited it.
So there is no key database to lose: Render's free plan forgets its disk on every redeploy, and the keys still work.

    dfk_<url-safe base64 of a small JSON>.<signature>

Issue one on YOUR computer (the secret never leaves your machine and the Render dashboard):

    set DWANIFORMS_KEY_SECRET=<the same long random value that is in Render>
    python -m dwaniforms.apikeys issue --org "RTIwala" --services rti,rti_appeal --days 30 --quota 500
    python -m dwaniforms.apikeys inspect dfk_...
    python -m dwaniforms.apikeys services

Services (what a key can be limited to): rti, rti_appeal, grievance, samjhao, advisor, forms, names, address, speech --
or the id of one template (pm_kisan, ...), or * for all. One key per customer per service set is the intended way to sell them.

Revoking: put the key's id (printed on issue) in DWANIFORMS_REVOKED_KEYS=id1,id2 in Render. Without a secret in the
environment the whole company API stays switched off (nothing is mounted).
"""
from __future__ import annotations

import argparse
import base64
import binascii
import dataclasses
import datetime as dt
import hashlib
import hmac
import json
import os
import secrets
import sys
import time

PREFIX = "dfk_"
VERSION = 1
MAX_DAYS = 400
MIN_SECRET = 24

# which service a template belongs to. lookups (land / ration) read centre records and are not sold through the API.
GROUPS = {"rti": "rti", "rti_appeal": "rti_appeal", "grievance": "grievance", "explain": "samjhao", "advisor": "advisor",
          "form": "forms"}


# small stand-alone helpers sold the same way (no template behind them): matching two spellings of a name, splitting a
# free-text address, reading spoken numbers / dates / digit strings.
TOOLS = ("names", "address", "speech")


class KeyError_(KeyError):
    """A key that cannot be used. .code is machine-readable: malformed | bad_signature | expired | revoked | disabled."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def secret() -> bytes:
    s = os.environ.get("DWANIFORMS_KEY_SECRET", "").strip()
    if len(s) < MIN_SECRET:
        raise KeyError_("disabled")
    return s.encode("utf-8")


def enabled() -> bool:
    return len(os.environ.get("DWANIFORMS_KEY_SECRET", "").strip()) >= MIN_SECRET


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(body: str, key: bytes) -> str:
    return _b64(hmac.new(key, body.encode("ascii"), hashlib.sha256).digest())


@dataclasses.dataclass(frozen=True)
class Key:
    id: str
    org: str
    services: tuple[str, ...]
    issued: int
    expires: int
    quota: int            # finished drafts allowed in the key's life (the trial's size)
    rpm: int              # requests per minute

    def allows(self, template_id: str, flow: str) -> bool:
        group = GROUPS.get(flow)
        return "*" in self.services or template_id in self.services or (group is not None and group in self.services)

    def allows_tool(self, tool: str) -> bool:
        return "*" in self.services or tool in self.services

    def days_left(self, now: float | None = None) -> int:
        return max(0, int((self.expires - (now or time.time())) // 86400))

    def expires_on(self) -> str:
        return dt.datetime.fromtimestamp(self.expires, dt.timezone.utc).date().isoformat()


def valid_services() -> set[str]:
    from .schema import load_all
    ids = {t.id for t in load_all().values() if t.flow in GROUPS}
    return {*GROUPS.values(), *ids, *TOOLS, "*"}


def issue(org: str, services: list[str], days: int = 30, quota: int = 500, rpm: int = 60,
          now: float | None = None, key: bytes | None = None) -> tuple[str, Key]:
    org = " ".join(org.split())
    if not 2 <= len(org) <= 60:
        raise ValueError("org must be 2-60 characters")
    services = sorted({s.strip() for s in services if s.strip()})
    unknown = [s for s in services if s not in valid_services()]
    if not services or unknown:
        raise ValueError("services must be some of: " + ", ".join(sorted(valid_services())) +
                         (f" (unknown: {', '.join(unknown)})" if unknown else ""))
    if not 1 <= days <= MAX_DAYS or not 1 <= quota <= 10_000_000 or not 1 <= rpm <= 6000:
        raise ValueError("days 1-%d, quota 1-10000000, rpm 1-6000" % MAX_DAYS)
    now = int(now or time.time())
    k = Key(secrets.token_hex(5), org, tuple(services), now, now + days * 86400, quota, rpm)
    body = _b64(json.dumps({"v": VERSION, "i": k.id, "o": k.org, "s": list(k.services), "a": k.issued, "e": k.expires,
                            "q": k.quota, "r": k.rpm}, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    return f"{PREFIX}{body}.{_sign(body, key or secret())}", k


def revoked() -> set[str]:
    return {x.strip() for x in os.environ.get("DWANIFORMS_REVOKED_KEYS", "").split(",") if x.strip()}


def verify(raw: str, now: float | None = None, key: bytes | None = None) -> Key:
    """The key's contents, or KeyError_ with the reason. Constant-time signature check; never raises anything else."""
    try:
        if not isinstance(raw, str) or not raw.startswith(PREFIX) or len(raw) > 2000 or raw.count(".") != 1:
            raise KeyError_("malformed")
        body, sig = raw[len(PREFIX):].split(".")
        if not hmac.compare_digest(_sign(body, key or secret()), sig):
            raise KeyError_("bad_signature")
        d = json.loads(_unb64(body).decode("utf-8"))
        if d.get("v") != VERSION:
            raise KeyError_("malformed")
        k = Key(str(d["i"]), str(d["o"]), tuple(d["s"]), int(d["a"]), int(d["e"]), int(d["q"]), int(d["r"]))
    except KeyError_:
        raise
    except (ValueError, KeyError, TypeError, binascii.Error, UnicodeError):
        raise KeyError_("malformed") from None
    if k.id in revoked():
        raise KeyError_("revoked")
    if (now or time.time()) >= k.expires:
        raise KeyError_("expired")
    return k


# ---- command line ---------------------------------------------------------------------------------------------------
def _show(k: Key) -> str:
    return (f"  id        {k.id}   (put this in DWANIFORMS_REVOKED_KEYS to switch the key off)\n"
            f"  for       {k.org}\n  services  {', '.join(k.services)}\n"
            f"  valid     until {k.expires_on()} ({k.days_left()} days)\n"
            f"  limits    {k.quota} finished drafts, {k.rpm} requests a minute")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m dwaniforms.apikeys", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("issue", help="make a key")
    i.add_argument("--org", required=True)
    i.add_argument("--services", required=True, help="comma separated, e.g. rti,rti_appeal")
    i.add_argument("--days", type=int, default=30)
    i.add_argument("--quota", type=int, default=500)
    i.add_argument("--rpm", type=int, default=60)
    n = sub.add_parser("inspect", help="read a key (checks the signature)")
    n.add_argument("key")
    sub.add_parser("services", help="what a key can be limited to")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "services":
            from .schema import load_all
            print("Groups:  " + ", ".join(sorted(set(GROUPS.values()) | set(TOOLS))) + "   (and * for everything)")
            for t in sorted(load_all().values(), key=lambda t: t.id):
                if t.flow in GROUPS:
                    print(f"  {GROUPS[t.flow]:<11} {t.id}")
            return 0
        if a.cmd == "issue":
            raw, k = issue(a.org, a.services.split(","), a.days, a.quota, a.rpm)
            print(f"\n{raw}\n\n{_show(k)}\n\nSend the key over a private channel. It is shown only now; it cannot be recovered.")
            return 0
        k = verify(a.key)
        print(_show(k))
        return 0
    except KeyError_ as e:
        print({"disabled": "Set DWANIFORMS_KEY_SECRET (at least %d characters) first." % MIN_SECRET}.get(e.code, f"Not a usable key: {e.code}"),
              file=sys.stderr)
        return 1
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
