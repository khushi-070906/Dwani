"""
Generates a self-signed TLS certificate for whatever IP(s) this machine is
reachable on -- exactly what --https (server.py) needs to serve over HTTPS
without any internet dependency, tunnel, or real domain name.

Why this exists: getUserMedia (microphone access, used by both host.html's
presenter mic and index.html's "Ask a question" -- see qa_pipeline.py) is
only allowed by browsers on a *secure context*: https://, or the special
case of http://localhost. Attendees joining over the presenter's own
broadcast hotspot or the venue's shared WiFi (Section 4.1/4.2) are on
http://<lan-ip>:8000, which is neither -- so their mic access is silently
blocked by the browser itself, no matter what the server or client code
does. This generates a certificate the server can use to close that gap
entirely offline, matching the rest of the system's no-internet-dependency
design (Section 2.1/2.3) rather than reaching for a tunnel like ngrok,
which would route traffic through a third party and undercut that
property.

-----------------------------------------------------------------------------
Setup
-----------------------------------------------------------------------------

    pip install cryptography --break-system-packages

-----------------------------------------------------------------------------
Run (once, ahead of a session -- while you still have internet is NOT
required, this needs no network access at all)
-----------------------------------------------------------------------------

    python generate_cert.py

Auto-detects every non-loopback IPv4 address this machine currently has
(same helper server.py already uses to pick which address to encode in the
join QR code) and includes all of them, plus "localhost"/127.0.0.1, in the
certificate's Subject Alternative Names -- so it stays valid whichever
interface ends up being the one attendees actually connect over.

Writes cert.pem and key.pem into the current directory. Valid for 365 days.

-----------------------------------------------------------------------------
Use it
-----------------------------------------------------------------------------

    python server.py --whisper-model small --nllb-model-dir nllb-200-ct2 --qa --https

server.py's --https flag (see its own --help) passes cert.pem/key.pem to
uvicorn directly.

-----------------------------------------------------------------------------
What attendees will see
-----------------------------------------------------------------------------

Since this certificate isn't signed by a browser-trusted authority (there's
no way to get one for a private LAN IP without internet access to a real
CA), every attendee's browser will show a "connection not private" /
"not secure" warning on first visit. This is expected and safe to click
through (Advanced -> Proceed) -- it's the same private network the plain
HTTP version was already using, this only adds the encryption + secure-
context flag getUserMedia checks for. It's one extra tap per attendee, once
per device, worth mentioning when you hand out the join QR code.
"""

from __future__ import annotations

import datetime
import ipaddress
import socket
import sys
from pathlib import Path


def get_lan_ips() -> list[str]:
    """Every non-loopback IPv4 address this machine is currently reachable
    on -- same reasoning as server.py's own address-discovery helper
    (Section 4.2: a laptop with WiFi, Ethernet, and VPN interfaces active
    simultaneously can have several, and the one attendees will actually
    use isn't always the first one a naive implementation would pick)."""
    ips = set()
    hostname = socket.gethostname()
    try:
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                ips.add(ip)
    except socket.gaierror:
        pass

    # Fallback that works even when the hostname doesn't resolve locally:
    # open a UDP "connection" (no packet actually sent) to a public address
    # just to ask the OS which local interface/IP it would route through.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ips.add(s.getsockname()[0])
    except OSError:
        pass

    return sorted(ips) or ["127.0.0.1"]


def generate(output_dir: Path) -> None:
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError:
        sys.exit("This script needs the 'cryptography' package: pip install cryptography --break-system-packages")

    lan_ips = get_lan_ips()
    print(f"Detected LAN IP(s): {', '.join(lan_ips)}")

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "LDST local session")])

    san_entries = [x509.DNSName("localhost")]
    for ip in lan_ips:
        san_entries.append(x509.IPAddress(ipaddress.ip_address(ip)))
    san_entries.append(x509.IPAddress(ipaddress.ip_address("127.0.0.1")))

    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
        .sign(key, hashes.SHA256())
    )

    key_path = output_dir / "key.pem"
    cert_path = output_dir / "cert.pem"

    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    print(f"\nWrote {cert_path} and {key_path} (valid 365 days).")
    print(f"Run: python server.py ... --https")
    print(
        "\nAttendee browsers will show a 'not secure' / 'connection not private' "
        "warning on first visit -- expected for a self-signed cert (see this "
        "script's module docstring). Tap through (Advanced -> Proceed) once per device."
    )


if __name__ == "__main__":
    generate(Path.cwd())
