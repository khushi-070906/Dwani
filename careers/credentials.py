"""
credentials.py -- cryptographically signed documents.

Every offer letter, certificate and recommendation letter is also issued as
a W3C Verifiable Credential (VC Data Model 2.0), secured with JOSE
(VC-JOSE-COSE: a compact JWS, alg EdDSA / Ed25519, typ "vc+jwt").

  * The signed credential is embedded inside the PDF as an attachment
    ("credential.jwt"), so the PDF carries its own proof.
  * The issuer's public keys are published as a did:web DID document at
    <careers url>/did.json (and /.well-known/did.json when run at a domain root). Anyone can check a signature with only the PDF and
    that public key -- the database is not involved, and a modified credential
    fails the check.
  * Revocation: credentialStatus points to /api/credentials/<id>/status.

Keys: CAREERS_SIGNING_KEY env var = base64 of a 32-byte Ed25519 seed (generate
with `python -m careers.credentials new-key`). It is deliberately separate from
the license server's LDST_LICENSE_PRIVATE_KEY. Without it, a key is generated once and
stored in the database (fine for testing; Settings flags it). Rotating keys
keeps old public keys published, so earlier documents keep verifying.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import io
import json
import os
import sys
from dataclasses import dataclass
from urllib.parse import urlparse

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

ATTACHMENT_NAME = "credential.jwt"
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


# ---------------------------------------------------------------------------
# encoding helpers
# ---------------------------------------------------------------------------

def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64u_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def base58btc(data: bytes) -> str:
    n = int.from_bytes(data, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    return "1" * (len(data) - len(data.lstrip(b"\0"))) + out


def base58btc_dec(s: str) -> bytes:
    n = 0
    for ch in s:
        n = n * 58 + _B58.index(ch)
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    return b"\0" * (len(s) - len(s.lstrip("1"))) + raw


def raw_public(pub: Ed25519PublicKey) -> bytes:
    return pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def multikey(pub_raw: bytes) -> str:
    """Multikey publicKeyMultibase for Ed25519: 'z' + base58btc(0xed01 || key)."""
    return "z" + base58btc(b"\xed\x01" + pub_raw)


def key_id(pub_raw: bytes) -> str:
    return "key-" + hashlib.sha256(pub_raw).hexdigest()[:12]


def did_for(public_url: str) -> str:
    """did:web identifier for the portal (port encoded as %3A; path segments become
    ':'-separated, so https://site.com/careers -> did:web:site.com:careers, whose
    DID document lives at https://site.com/careers/did.json per the did:web spec)."""
    u = urlparse(public_url)
    segs = [p for p in u.path.split("/") if p]
    return "did:web:" + u.netloc.replace(":", "%3A") + "".join(":" + p for p in segs)


# ---------------------------------------------------------------------------
# keys
# ---------------------------------------------------------------------------

@dataclass
class SigningKey:
    private: Ed25519PrivateKey
    pub_raw: bytes
    kid: str  # key-xxxxxxxxxxxx

    @classmethod
    def from_seed(cls, seed: bytes) -> "SigningKey":
        priv = Ed25519PrivateKey.from_private_bytes(seed)
        pub = raw_public(priv.public_key())
        return cls(priv, pub, key_id(pub))

    def seed(self) -> bytes:
        return self.private.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                          serialization.NoEncryption())


def new_seed() -> bytes:
    return Ed25519PrivateKey.generate().private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                                      serialization.NoEncryption())


def seed_from_env() -> bytes | None:
    v = os.environ.get("CAREERS_SIGNING_KEY", "").strip()
    if not v:
        return None
    seed = base64.b64decode(v + "=" * (-len(v) % 4))
    if len(seed) != 32:
        raise ValueError("CAREERS_SIGNING_KEY must be base64 of a 32-byte Ed25519 seed (python -m careers.credentials new-key)")
    return seed


# ---------------------------------------------------------------------------
# credentials
# ---------------------------------------------------------------------------

DOC_TYPES = {"offer": "InternshipOfferCredential", "certificate": "InternshipCompletionCredential",
             "lor": "RecommendationLetterCredential"}


def build_credential(*, public_url: str, issuer_name: str, doc_id: str, kind: str, kind_label: str, holder: str,
                     college: str, role_title: str, start: dt.date, end: dt.date, issued: dt.date) -> dict:
    issued_at = dt.datetime.combine(issued, dt.time(0, 0), tzinfo=dt.timezone.utc)
    subject = {"type": "Person", "name": holder, "role": role_title, "startDate": start.isoformat(),
               "endDate": end.isoformat(), "documentType": kind_label, "documentNumber": doc_id}
    if college:
        subject["college"] = college
    return {
        "@context": ["https://www.w3.org/ns/credentials/v2"],
        "id": f"{public_url}/verify?id={doc_id}",
        "type": ["VerifiableCredential", DOC_TYPES.get(kind, "DocumentCredential")],
        "issuer": {"id": did_for(public_url), "name": issuer_name},
        "validFrom": issued_at.isoformat().replace("+00:00", "Z"),
        "credentialSubject": subject,
        "credentialStatus": {"id": f"{public_url}/api/credentials/{doc_id}/status", "type": "DocumentRevocationStatus"},
    }


def sign(credential: dict, key: SigningKey, public_url: str) -> str:
    """Compact JWS per VC-JOSE-COSE: header {alg: EdDSA, typ: vc+jwt, cty: vc, kid}, payload = the credential."""
    header = {"alg": "EdDSA", "typ": "vc+jwt", "cty": "vc", "kid": f"{did_for(public_url)}#{key.kid}"}
    signing_input = b64u(json.dumps(header, separators=(",", ":")).encode()) + "." + \
        b64u(json.dumps(credential, separators=(",", ":"), ensure_ascii=False).encode())
    return signing_input + "." + b64u(key.private.sign(signing_input.encode()))


@dataclass
class VerifyResult:
    valid: bool
    reason: str
    credential: dict | None = None
    kid: str = ""


def verify(token: str, public_keys: dict[str, bytes]) -> VerifyResult:
    """public_keys: {"key-xxxx": raw 32-byte Ed25519 public key}. Pure function: no database, no network."""
    try:
        h64, p64, s64 = token.strip().split(".")
        header = json.loads(b64u_dec(h64))
        payload = json.loads(b64u_dec(p64))
    except Exception:
        return VerifyResult(False, "Not a valid signed credential (malformed).")
    if header.get("alg") != "EdDSA":
        return VerifyResult(False, f"Unsupported algorithm {header.get('alg')!r}.")
    kid = str(header.get("kid", "")).split("#")[-1]
    raw = public_keys.get(kid)
    if raw is None:
        return VerifyResult(False, "Signed with a key this issuer has not published.", payload, kid)
    try:
        Ed25519PublicKey.from_public_bytes(raw).verify(b64u_dec(s64), f"{h64}.{p64}".encode())
    except (InvalidSignature, ValueError):
        return VerifyResult(False, "Signature does not match: the credential was altered or not issued by this issuer.",
                            payload, kid)
    return VerifyResult(True, "Signature valid.", payload, kid)


def did_document(public_url: str, keys: list[tuple[str, bytes]]) -> dict:
    did = did_for(public_url)
    methods = [{"id": f"{did}#{kid}", "type": "Multikey", "controller": did, "publicKeyMultibase": multikey(raw)}
               for kid, raw in keys]
    return {"@context": ["https://www.w3.org/ns/did/v1", "https://w3id.org/security/multikey/v1"], "id": did,
            "verificationMethod": methods, "assertionMethod": [m["id"] for m in methods]}


def keys_from_did_document(doc: dict) -> dict[str, bytes]:
    out = {}
    for m in doc.get("verificationMethod", []):
        mb = m.get("publicKeyMultibase", "")
        if mb.startswith("z"):
            raw = base58btc_dec(mb[1:])
            if raw[:2] == b"\xed\x01" and len(raw) == 34:
                out[m["id"].split("#")[-1]] = raw[2:]
    return out


# ---------------------------------------------------------------------------
# PDF embedding
# ---------------------------------------------------------------------------

def embed_in_pdf(pdf: bytes, token: str) -> bytes:
    from pypdf import PdfReader, PdfWriter

    w = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf)))
    w.add_attachment(ATTACHMENT_NAME, token.encode())
    w.add_metadata({"/VerifiableCredential": "embedded as " + ATTACHMENT_NAME})
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


def extract_from_pdf(pdf: bytes) -> str | None:
    from pypdf import PdfReader

    try:
        att = PdfReader(io.BytesIO(pdf)).attachments
    except Exception:
        return None
    data = att.get(ATTACHMENT_NAME)
    if not data:
        return None
    return data[0].decode(errors="replace") if isinstance(data, list) else data.decode(errors="replace")


if __name__ == "__main__":
    if sys.argv[1:] == ["new-key"]:
        s = new_seed()
        k = SigningKey.from_seed(s)
        print("CAREERS_SIGNING_KEY=" + base64.b64encode(s).decode())
        print(f"(key id {k.kid}; keep this secret, store it only in Render's environment variables)")
    else:
        print("usage: python -m careers.credentials new-key")
