"""
licensing.py

License enforcement for LDST/DwaniLive, following the same wrapper pattern as
glossary.py / accessibility.py: a small, clamped/validated module that
plugs into server.py at session start without touching the core pipeline.

SIGNING SCHEME: Ed25519 (asymmetric), not shared-secret HMAC.
-----------------------------------------------------------------
This matters: the presenter's app must be able to verify a license token
completely offline, with no server contact. That means whatever key material
ships inside the presenter's app (baked into the .exe, or set via env var)
is, eventually, extractable by a determined person -- decompilation, memory
dumps, etc. all get easier over time even with a compiled binary.

With a SHARED SECRET (the old HMAC approach), extracting that key means the
attacker can forge a brand new token claiming any tier, any expiry -- e.g.
"institution", valid until year 3000 -- and run forever, completely offline,
never contacting your license server again.

With Ed25519, the presenter's app only ever holds the PUBLIC key. A public
key can verify a signature but cannot produce a new valid one. Extracting it
gains an attacker nothing -- they still can't forge a token without the
PRIVATE key, which lives ONLY on license_server.py's infrastructure and is
never shipped anywhere near a presenter's machine.

This does not make the app un-crackable -- someone can still patch the
compiled binary to skip the check_license() call entirely, the same way
any client-side license check in any desktop software can theoretically be
patched out. What this fixes specifically is the "extract one key, mint
unlimited free licenses forever" failure mode, which is a much lower bar of
effort than binary patching and was the actual hole in the HMAC version.

Design goals (matching the project's offline-first constraint):
- Presenter activates ONCE while they still have internet (same moment
  they download the Whisper/NLLB models, per the README setup step).
- The activation call returns a signed license token, cached to disk.
- Every subsequent session start validates the CACHED token locally
  against the PUBLIC key -- no network call required at the venue.
- Token carries an expiry + a grace window so a presenter mid-conference
  isn't stranded if they're a day past their check-in date.

This file does NOT implement the license *server* -- see license_server.py
for that (a separate process you run, not shipped to presenters). Only
license_server.py should ever hold the PRIVATE key.
"""

from __future__ import annotations

import json
import time
import base64
import dataclasses
from pathlib import Path
from typing import Optional, Set

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.exceptions import InvalidSignature

# ---------------------------------------------------------------------------
# Tiers and feature gating
# ---------------------------------------------------------------------------

# Keep this in sync with license_server.py's TIER_FEATURES.
TIER_FEATURES = {
    "free": {
        "core",  # base transcription + translation pipeline, always on
    },
    "pro": {
        "core",
        "semantic-cache",
        "glossary",
    },
    "institution": {
        "core",
        "semantic-cache",
        "glossary",
        "qa",
        "accessibility",
        "isl",
        "dynamic-glossary",
        "itde",
    },
}

# Map CLI flags (as used in server.py / run.py) to the feature key that gates them.
FLAG_TO_FEATURE = {
    "semantic_cache": "semantic-cache",
    "glossary_file": "glossary",
    "qa": "qa",
    "isl": "isl",
    "dynamic_glossary": "dynamic-glossary",
    "itde": "itde",
    # accessibility mode has no flag today (always-on per README) --
    # included here so it CAN be gated later without changing callers.
    "accessibility": "accessibility",
}

GRACE_PERIOD_SECONDS = 7 * 24 * 60 * 60  # 7 days past expiry, offline-friendly


class LicenseError(Exception):
    """Raised when a session should not be allowed to start."""


@dataclasses.dataclass
class License:
    tier: str
    presenter_email: str
    issued_at: int
    expires_at: int
    max_attendees: Optional[int] = None  # None = unlimited

    def features(self) -> Set[str]:
        return TIER_FEATURES.get(self.tier, TIER_FEATURES["free"])

    def is_within_grace(self, now: Optional[int] = None) -> bool:
        now = now or int(time.time())
        return now <= self.expires_at + GRACE_PERIOD_SECONDS

    def is_expired(self, now: Optional[int] = None) -> bool:
        now = now or int(time.time())
        return now > self.expires_at


# ---------------------------------------------------------------------------
# Key helpers
# ---------------------------------------------------------------------------

def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def generate_keypair() -> tuple[str, str]:
    """
    Run this ONCE, on your own machine, to create the keypair. Print both,
    store the private key ONLY in license_server.py's environment
    (LDST_LICENSE_PRIVATE_KEY), and bake/ship the public key into the
    presenter app (LDST_LICENSE_PUBLIC_KEY). Never commit either to git.
    Returns (private_key_b64, public_key_b64).
    """
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    private_bytes = private_key.private_bytes_raw()
    public_bytes = public_key.public_bytes_raw()

    return _b64encode(private_bytes), _b64encode(public_bytes)


def _load_private_key(private_key_b64: str) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(_b64decode(private_key_b64))


def _load_public_key(public_key_b64: str) -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(_b64decode(public_key_b64))


# ---------------------------------------------------------------------------
# Token format: base64(payload_json) + "." + base64(ed25519_signature)
# ---------------------------------------------------------------------------

def sign_license(license: License, private_key_b64: str) -> str:
    """
    SERVER-SIDE ONLY. Requires the PRIVATE key -- never call this from
    anything that ships to a presenter's machine.
    """
    private_key = _load_private_key(private_key_b64)
    payload = json.dumps(dataclasses.asdict(license), separators=(",", ":")).encode("utf-8")
    signature = private_key.sign(payload)
    return f"{_b64encode(payload)}.{_b64encode(signature)}"


def verify_license(token: str, public_key_b64: str) -> License:
    """
    CLIENT-SIDE. Only needs the PUBLIC key. Raises LicenseError on any
    failure -- malformed token, bad signature, or corrupt payload.
    """
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        payload = _b64decode(payload_b64)
        signature = _b64decode(sig_b64)
    except (ValueError, Exception) as exc:
        raise LicenseError(f"Malformed license token: {exc}") from exc

    try:
        public_key = _load_public_key(public_key_b64)
        public_key.verify(signature, payload)
    except InvalidSignature:
        raise LicenseError(
            "License signature invalid -- token was not issued by this server "
            "or has been tampered with."
        )
    except Exception as exc:
        raise LicenseError(f"Could not verify license: {exc}") from exc

    try:
        data = json.loads(payload)
        license = License(**data)
    except (json.JSONDecodeError, TypeError) as exc:
        raise LicenseError(f"Corrupt license payload: {exc}") from exc

    return license


# ---------------------------------------------------------------------------
# Local cache (what actually lives on the presenter's laptop)
# ---------------------------------------------------------------------------

DEFAULT_CACHE_PATH = Path.home() / ".ldst" / "license.token"


def save_cached_license(token: str, path: Path = DEFAULT_CACHE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass  # best-effort on platforms without POSIX perms (e.g. some Windows setups)


def load_cached_license(path: Path = DEFAULT_CACHE_PATH) -> str:
    if not path.exists():
        raise LicenseError(
            f"No license found at {path}. Run `python activate.py` once while "
            f"online, same as the model download step in Setup."
        )
    return path.read_text(encoding="utf-8").strip()


# ---------------------------------------------------------------------------
# Entry point for server.py -- call this before presenter_page() serves the QR code
# ---------------------------------------------------------------------------

def check_license(
    public_key_b64: str,
    requested_flags: Optional[dict] = None,
    cache_path: Path = DEFAULT_CACHE_PATH,
) -> License:
    """
    Validate the cached license and confirm it covers every requested flag.

    public_key_b64: the PUBLIC key (safe to embed/ship in the presenter app).
    requested_flags: dict of {flag_name: bool_or_value} as parsed from argv,
    e.g. {"semantic_cache": True, "glossary_file": "glossary.json", "qa": True}.
    Only flags that are truthy are checked against the license tier.

    Raises LicenseError with a message safe to print directly to the presenter
    if the session should not start.
    """
    token = load_cached_license(cache_path)
    license = verify_license(token, public_key_b64)

    if license.is_expired():
        if license.is_within_grace():
            print(
                f"[license] Warning: subscription expired "
                f"{time.strftime('%Y-%m-%d', time.localtime(license.expires_at))}. "
                f"Running on a {GRACE_PERIOD_SECONDS // 86400}-day grace period -- "
                f"please reconnect to WiFi soon and run activate.py to renew."
            )
        else:
            raise LicenseError(
                "Your LDST license expired more than the grace period allows. "
                "Connect to the internet and run `python activate.py` to renew "
                "before starting a session."
            )

    if requested_flags:
        allowed = license.features()
        for flag_name, value in requested_flags.items():
            if not value:
                continue
            feature = FLAG_TO_FEATURE.get(flag_name)
            if feature and feature not in allowed:
                raise LicenseError(
                    f"The '{flag_name}' feature requires a plan that includes "
                    f"'{feature}'. Your current tier is '{license.tier}'. "
                    f"Upgrade at [your billing URL] or drop the flag to continue "
                    f"on your current plan."
                )

    if license.max_attendees is not None:
        print(f"[license] Session capped at {license.max_attendees} attendees on the '{license.tier}' plan.")

    return license
