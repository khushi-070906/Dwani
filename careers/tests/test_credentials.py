"""Unit tests for signing / verification (no web app involved)."""

import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from careers import credentials as vc  # noqa: E402

URL = "https://careers.example.com"


def make(kind="certificate"):
    key = vc.SigningKey.from_seed(vc.new_seed())
    cred = vc.build_credential(public_url=URL, issuer_name="DwaniLive", doc_id="DL-CERT-2027-0001", kind=kind,
                               kind_label="Internship Completion Certificate", holder="Aarav Sharma", college="GTBIT",
                               role_title="ML Intern", start=dt.date(2026, 11, 1), end=dt.date(2027, 1, 31),
                               issued=dt.date(2027, 2, 1))
    return key, cred, vc.sign(cred, key, URL)


def test_roundtrip_and_format():
    key, cred, tok = make()
    h = json.loads(vc.b64u_dec(tok.split(".")[0]))
    assert h == {"alg": "EdDSA", "typ": "vc+jwt", "cty": "vc", "kid": f"did:web:careers.example.com#{key.kid}"}
    r = vc.verify(tok, {key.kid: key.pub_raw})
    assert r.valid and r.credential["credentialSubject"]["name"] == "Aarav Sharma"
    assert cred["@context"] == ["https://www.w3.org/ns/credentials/v2"] and "VerifiableCredential" in cred["type"]


def test_tampered_payload_fails():
    key, cred, tok = make()
    h, p, s = tok.split(".")
    forged = dict(cred)
    forged["credentialSubject"] = dict(cred["credentialSubject"], role="Lead Engineer")
    p2 = vc.b64u(json.dumps(forged, separators=(",", ":")).encode())
    assert not vc.verify(f"{h}.{p2}.{s}", {key.kid: key.pub_raw}).valid


def test_other_key_and_unknown_key_fail():
    key, _, tok = make()
    other = vc.SigningKey.from_seed(vc.new_seed())
    assert not vc.verify(tok, {key.kid: other.pub_raw}).valid       # right kid, wrong key
    assert "not published" in vc.verify(tok, {other.kid: other.pub_raw}).reason
    assert not vc.verify("garbage", {}).valid


def test_did_document_roundtrip():
    k1, k2 = vc.SigningKey.from_seed(vc.new_seed()), vc.SigningKey.from_seed(vc.new_seed())
    doc = vc.did_document("http://localhost:8000", [(k1.kid, k1.pub_raw), (k2.kid, k2.pub_raw)])
    assert doc["id"] == "did:web:localhost%3A8000"
    assert all(m["publicKeyMultibase"].startswith("z6Mk") for m in doc["verificationMethod"])  # Ed25519 multikey prefix
    assert vc.keys_from_did_document(doc) == {k1.kid: k1.pub_raw, k2.kid: k2.pub_raw}


def test_pdf_embedding():
    from reportlab.pdfgen import canvas
    import io
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(100, 700, "hello")
    c.save()
    key, _, tok = make()
    pdf = vc.embed_in_pdf(buf.getvalue(), tok)
    assert vc.extract_from_pdf(pdf) == tok
    assert vc.extract_from_pdf(buf.getvalue()) is None


def test_path_based_did_web():
    assert vc.did_for("https://dhwani-elit.onrender.com/careers") == "did:web:dhwani-elit.onrender.com:careers"
    assert vc.did_for("https://dhwani-elit.onrender.com/careers/") == "did:web:dhwani-elit.onrender.com:careers"
    assert vc.did_for("http://localhost:8000") == "did:web:localhost%3A8000"
