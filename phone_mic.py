"""
phone_mic.py -- lets the presenter use their PHONE as the microphone.

Why a second port: phone browsers only allow microphone access on a
"secure context" (https:// or localhost). The attendee link stays plain
http:// on the main port (no certificate warning for 50 attendees); only
the presenter's phone uses a separate https:// port with a self-signed
certificate generated offline on this laptop.

Security: anyone on the Wi-Fi can open /host, so streaming presenter audio
from another device now requires a per-session random presenter key that
only appears in the QR shown on the presenter's laptop (served by a
localhost-only endpoint). Connections from the laptop itself (localhost)
work without it, as before.
"""

from __future__ import annotations

import datetime
import io
import ipaddress
import json
import secrets
import socket
from pathlib import Path

PRESENTER_KEY = secrets.token_urlsafe(9)


def cert_dir() -> Path:
    try:
        import appenv

        appenv.RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        return appenv.RUNTIME_DIR
    except Exception:
        return Path.cwd()


def _cert_ips(cert_path: Path) -> set[str]:
    from cryptography import x509

    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    expires = getattr(cert, "not_valid_after_utc", None) or cert.not_valid_after.replace(tzinfo=datetime.timezone.utc)
    if expires - datetime.datetime.now(datetime.timezone.utc) < datetime.timedelta(days=30):
        return set()
    return {str(ip) for ip in san.get_values_for_type(x509.IPAddress)}


def ensure_cert(ips: list[str]) -> tuple[Path, Path]:
    """Reuse the existing cert if it already covers every current LAN IP, so
    a phone that tapped "Proceed" once doesn't see the warning again; make a
    new one when the laptop's IP changed (different Wi-Fi) or it's expiring."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    d = cert_dir()
    cert_path, key_path = d / "phone_mic_cert.pem", d / "phone_mic_key.pem"
    wanted = set(ips) | {"127.0.0.1"}
    if cert_path.exists() and key_path.exists():
        try:
            if wanted <= _cert_ips(cert_path):
                return cert_path, key_path
        except Exception:
            pass

    known = set()
    meta = d / "phone_mic_cert_ips.json"
    if meta.exists():
        try:
            known = set(json.loads(meta.read_text()))
        except Exception:
            known = set()
    all_ips = sorted(wanted | known)  # keep earlier networks valid too (home + college Wi-Fi)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "DwaniLive presenter (local)")])
    now = datetime.datetime.now(datetime.timezone.utc)
    san = [x509.DNSName("localhost")] + [x509.IPAddress(ipaddress.ip_address(ip)) for ip in all_ips]
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=825))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .sign(key, hashes.SHA256())
    )
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                           serialization.NoEncryption()))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    meta.write_text(json.dumps(all_ips))
    return cert_path, key_path


def pick_port(exclude: int, start: int = 8001, end: int = 8010) -> int:
    for port in range(start, end + 1):
        if port == exclude:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("0.0.0.0", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"No free port in {start}-{end} for the phone-mic page.")


def phone_url(ip: str, port: int, session_id: str) -> str:
    return f"https://{ip}:{port}/host?session={session_id}&key={PRESENTER_KEY}"


def qr_png(url: str) -> bytes:
    import qrcode

    buf = io.BytesIO()
    qrcode.make(url, border=2).save(buf, format="PNG")
    return buf.getvalue()


def is_loopback(host: str | None) -> bool:
    try:
        return ipaddress.ip_address((host or "").split("%")[0]).is_loopback
    except ValueError:
        return host in ("localhost", "testclient")
