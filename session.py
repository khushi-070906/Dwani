"""
LDST session module -- session identity, self-broadcast WiFi hotspot, local
network discovery, and QR generation.

This is the "session hosting" piece described in the paper (Section 4.1): it
owns the session ID, has the presenter's laptop broadcast its own WiFi hotspot
(so attendees never depend on the venue's network -- see Section 2.1 on client
isolation), builds the join URL attendees scan, and renders both the hotspot
credentials and the join URL as QR codes (terminal ASCII for the presenter,
PNG for projecting on a slide).

Pulled out of server.py so it can be unit-tested and reused independently of the
WebSocket/broadcast logic -- e.g. a future multi-room deployment (Section 7) would
need N of these, one per presenter device, without touching server.py at all.

Usage from server.py:

    from session import Session

    session = Session(port=args.port, session_id=args.session_id)
    join_url = session.announce_with_hotspot()   # broadcasts our own WiFi
    # or, if you deliberately want the old venue-WiFi-discovery behavior:
    join_url = session.announce()
    ...
    @app.websocket("/ws")
    async def attendee_socket(websocket, lang, session_param):
        if session_param != session.session_id:
            ...

Platform support for automatic hotspot creation:
    Windows -- netsh wlan hostednetwork (requires a WiFi adapter driver that
               still supports the legacy Hosted Network feature; not all do).
    Linux   -- nmcli device wifi hotspot (requires NetworkManager >= 1.16 and
               a WiFi adapter that supports AP mode).
    macOS   -- not automatable: modern macOS has no public CLI for Internet
               Sharing / Personal Hotspot. start_hotspot() raises HotspotError
               with instructions to enable it manually and pass matching
               --hotspot-ssid/--hotspot-password so the QR code is still correct.
"""

from __future__ import annotations

import platform
import secrets
import socket
import string
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path


class HotspotError(RuntimeError):
    """Raised when the presenter's device could not broadcast its own WiFi
    hotspot -- unsupported OS, missing tool (netsh/nmcli), no WiFi adapter
    with AP-mode support, or a subprocess call failing for any other reason.
    Callers (see announce_with_hotspot) are expected to catch this and fall
    back to venue-WiFi discovery rather than crash the whole session."""


@dataclass
class Session:
    """Owns one presenter session: its ID, join URL(s), and QR code."""

    port: int
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    qr_image_path: Path | None = None
    wifi_qr_image_path: Path | None = None

    # Hotspot state -- not constructor args, set by start_hotspot()/stop_hotspot().
    hotspot_active: bool = field(default=False, init=False)
    hotspot_ssid: str | None = field(default=None, init=False)
    hotspot_password: str | None = field(default=None, init=False)
    hotspot_ip: str | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        if self.session_id is None:
            self.session_id = uuid.uuid4().hex[:8]
        if self.qr_image_path is None:
            self.qr_image_path = Path(__file__).parent / "session_qr.png"
        if self.wifi_qr_image_path is None:
            self.wifi_qr_image_path = Path(__file__).parent / "session_qr_wifi.png"

    # -- network discovery -------------------------------------------------

    def candidate_local_ips(self) -> list[str]:
        """
        All non-loopback IPv4 addresses on this machine. Laptops with VPN/Ethernet/
        WiFi active simultaneously can have several -- the venue WiFi one isn't
        always first, so callers get the full list and can confirm rather than
        silently guessing.
        """
        ips: set[str] = set()
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                ip = info[4][0]
                if not ip.startswith("127."):
                    ips.add(ip)
        except socket.gaierror:
            pass

        # Fallback: the interface that would be used to reach the open internet.
        # No packet is actually sent for a UDP socket -- this only resolves routing.
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ips.add(s.getsockname()[0])
            s.close()
        except OSError:
            pass

        return sorted(ips) or ["127.0.0.1"]

    def primary_ip(self) -> str:
        # Once we're broadcasting our own hotspot, that's the address attendees
        # actually connect to -- prefer it over whatever candidate_local_ips()
        # would otherwise pick first (which no longer means anything once we're
        # not relying on the venue's network at all).
        if self.hotspot_active and self.hotspot_ip:
            return self.hotspot_ip
        return self.candidate_local_ips()[0]

    # -- hotspot (Section 4.1: self-broadcast WiFi, no venue-network dependency) ---

    def start_hotspot(self, ssid: str | None = None, password: str | None = None) -> tuple[str, str]:
        """Have this device broadcast its own WiFi access point instead of
        relying on whatever network the venue provides (Section 2.1). Returns
        (ssid, password) actually in use. Raises HotspotError if this OS/
        hardware combination can't do it automatically -- callers should catch
        this and fall back to venue-WiFi discovery (see announce_with_hotspot).

        ssid/password are generated automatically unless explicitly passed --
        pass both when the hotspot was already created manually (macOS, or a
        WiFi adapter without AP-mode/Hosted-Network support) so the QR code
        still encodes the credentials attendees actually need.
        """
        self.hotspot_ssid = ssid or self._generate_ssid()
        self.hotspot_password = password or self._generate_password()

        system = platform.system()
        before = set(self.candidate_local_ips())
        try:
            if system == "Windows":
                self._start_hotspot_windows(self.hotspot_ssid, self.hotspot_password)
            elif system == "Linux":
                self._start_hotspot_linux(self.hotspot_ssid, self.hotspot_password)
            elif system == "Darwin":
                self._start_hotspot_macos(self.hotspot_ssid, self.hotspot_password)
            else:
                raise HotspotError(f"Automatic hotspot creation isn't supported on {system!r}.")
        except HotspotError:
            raise
        except (subprocess.CalledProcessError, FileNotFoundError, OSError) as e:
            raise HotspotError(f"Failed to start a WiFi hotspot on {system}: {e}") from e

        # Give the OS a moment to bring the new interface up, then diff the
        # local-IP set to find the address the hotspot itself was assigned --
        # this works the same way regardless of platform-specific defaults
        # (Windows ICS typically uses 192.168.137.1, nmcli typically 10.42.0.1),
        # so we don't have to hardcode or parse either.
        time.sleep(2)
        new_ips = sorted(set(self.candidate_local_ips()) - before)
        self.hotspot_ip = new_ips[0] if new_ips else self._fallback_hotspot_ip(system)
        self.hotspot_active = True

        return self.hotspot_ssid, self.hotspot_password

    def stop_hotspot(self) -> None:
        """Tear down the hotspot started by start_hotspot(). Safe to call even
        if no hotspot is active (no-op), and safe to call more than once.
        Best-effort: failures are printed, not raised, since this runs during
        shutdown and shouldn't block the process from exiting."""
        if not self.hotspot_active:
            return
        system = platform.system()
        try:
            if system == "Windows":
                subprocess.run(
                    ["netsh", "wlan", "stop", "hostednetwork"],
                    check=False, capture_output=True, text=True,
                )
            elif system == "Linux":
                subprocess.run(
                    ["nmcli", "connection", "down", "Hotspot"],
                    check=False, capture_output=True, text=True,
                )
            # macOS: nothing was started programmatically, so nothing to stop.
        except OSError as e:
            print(f"Warning: failed to cleanly stop the hotspot: {e}")
        finally:
            self.hotspot_active = False

    def _generate_ssid(self) -> str:
        return f"LDST-{self.session_id[:4].upper()}"

    def _generate_password(self) -> str:
        # WPA2 requires >= 8 characters; 12 alnum characters is comfortably
        # above that while still being short enough to type by hand as a
        # fallback if a QR scan fails.
        alphabet = string.ascii_letters + string.digits
        return "".join(secrets.choice(alphabet) for _ in range(12))

    def _fallback_hotspot_ip(self, system: str) -> str | None:
        """Used only if the before/after IP diff in start_hotspot() finds
        nothing (e.g. the interface took longer than 2s to come up). Returns
        each platform's documented default gateway address for the hotspot
        mode we used, or None if there's no sensible default to guess."""
        return {"Windows": "192.168.137.1", "Linux": "10.42.0.1"}.get(system)

    def _start_hotspot_windows(self, ssid: str, password: str) -> None:
        subprocess.run(
            ["netsh", "wlan", "set", "hostednetwork", "mode=allow", f"ssid={ssid}", f"key={password}"],
            check=True, capture_output=True, text=True,
        )
        subprocess.run(
            ["netsh", "wlan", "start", "hostednetwork"],
            check=True, capture_output=True, text=True,
        )

    def _start_hotspot_linux(self, ssid: str, password: str) -> None:
        iface = self._first_wifi_interface_linux()
        cmd = ["nmcli", "device", "wifi", "hotspot"]
        if iface:
            cmd += ["ifname", iface]
        cmd += ["ssid", ssid, "password", password]
        subprocess.run(cmd, check=True, capture_output=True, text=True)

    def _first_wifi_interface_linux(self) -> str | None:
        try:
            result = subprocess.run(
                ["nmcli", "-t", "-f", "DEVICE,TYPE", "device"],
                check=True, capture_output=True, text=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            return None
        for line in result.stdout.splitlines():
            device, _, dtype = line.partition(":")
            if dtype == "wifi":
                return device
        return None

    def _start_hotspot_macos(self, ssid: str, password: str) -> None:
        raise HotspotError(
            "Automatic hotspot creation isn't supported on macOS: recent macOS versions "
            "removed the command-line 'sharing' tool, and Internet Sharing / Personal "
            "Hotspot have no public CLI. Turn on Internet Sharing manually in "
            "System Settings -> General -> Sharing (or Personal Hotspot), then rerun "
            "with --hotspot-ssid and --hotspot-password set to exactly what you configured "
            "there, so the generated QR code encodes the credentials attendees actually need."
        )

    # -- URL building --------------------------------------------------------

    def url_for(self, ip: str) -> str:
        return f"http://{ip}:{self.port}/?session={self.session_id}"

    def primary_url(self) -> str:
        return self.url_for(self.primary_ip())

    # -- QR generation --------------------------------------------------------

    def generate_qr(self, url: str) -> Path:
        """Render `url` as a QR code: ASCII (if supported) + PNG."""
        import qrcode
        import sys

        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make()

        # Try printing the QR in the terminal. Skip if the console doesn't support Unicode.
        try:
            if sys.stdout.encoding and "utf" in sys.stdout.encoding.lower():
                qr.print_ascii(invert=True)
            else:
                print("(Terminal does not support Unicode QR display.)")
        except Exception:
            print("(Skipping terminal QR display.)")

        qrcode.make(url).save(self.qr_image_path)
        return self.qr_image_path

    @staticmethod
    def wifi_qr_payload(ssid: str, password: str) -> str:
        """Standard WIFI: QR payload format that iOS/Android camera apps
        recognize natively for one-scan network onboarding (join-network,
        not just open-a-link). T:WPA covers WPA/WPA2, which is what every
        platform-specific hotspot method above configures."""

        def esc(s: str) -> str:
            # Per the format spec, backslash/semicolon/comma/colon inside a
            # field must be backslash-escaped or a scanning app may mis-parse
            # the field boundaries.
            return (
                s.replace("\\", "\\\\")
                .replace(";", "\\;")
                .replace(",", "\\,")
                .replace(":", "\\:")
            )

        return f"WIFI:T:WPA;S:{esc(ssid)};P:{esc(password)};;"

    def generate_wifi_qr(self, ssid: str, password: str) -> Path:
        """Render the hotspot's WiFi credentials as a QR code."""
        import qrcode
        import sys

        payload = self.wifi_qr_payload(ssid, password)

        qr = qrcode.QRCode(border=1)
        qr.add_data(payload)
        qr.make()

        # Try printing the QR in the terminal. Skip if the console doesn't support Unicode.
        try:
            if sys.stdout.encoding and "utf" in sys.stdout.encoding.lower():
                qr.print_ascii(invert=True)
            else:
                print("(Terminal does not support Unicode WiFi QR display.)")
        except Exception:
            print("(Skipping terminal WiFi QR display.)")

        qrcode.make(payload).save(self.wifi_qr_image_path)
        return self.wifi_qr_image_path

    # -- top-level entry point ------------------------------------------------

    def announce(self) -> str:
        """Print session info, generate the QR code, and return the primary join URL.

        Called once at server startup. Returns the URL so the caller (server.py)
        can log it or hand it to other modules without re-deriving it.
        """
        ips = self.candidate_local_ips()
        primary = ips[0]
        url = self.url_for(primary)

        print(f"\nSession ID: {self.session_id}")
        if len(ips) > 1:
            print("Multiple network interfaces detected -- confirm which one is the venue WiFi:")
            for ip in ips:
                print(f"  {self.url_for(ip)}")
        # Always printed, regardless of interface count -- run.py (and any other
        # tooling) parses this exact line to detect the server is ready and to
        # extract the port/session ID. Previously this was only printed in the
        # single-interface branch, so run.py silently hung forever (until its
        # own timeout) on any machine with more than one network adapter --
        # which is most Windows laptops, once VPN/virtual adapters are counted.
        print(f"Join URL: {url}\n")

        self.generate_qr(url)
        print(f"QR image saved to {self.qr_image_path} -- project this for attendees.\n")

        return url

    def announce_with_hotspot(self, ssid: str | None = None, password: str | None = None) -> str:
        """Preferred entry point (Section 4.1): has this device broadcast its
        own WiFi hotspot rather than relying on the venue's network, then
        prints/QR-encodes both the hotspot credentials and the join URL.

        Falls back to announce()'s old venue-WiFi-discovery behavior if
        hotspot creation isn't possible on this machine (unsupported OS,
        missing tool, no AP-capable WiFi adapter) -- printing why, so the
        presenter knows they're on the venue's network instead and should
        watch for client-isolation issues (Section 2.1).
        """
        try:
            actual_ssid, actual_password = self.start_hotspot(ssid, password)
        except HotspotError as e:
            print(f"Could not start a self-broadcast hotspot: {e}")
            print("Falling back to venue-WiFi discovery instead -- see Section 2.1 for the risk this reintroduces (e.g. client isolation).\n")
            return self.announce()

        url = self.url_for(self.hotspot_ip or self.primary_ip())

        print(f"\nSession ID: {self.session_id}")
        print(f"Hotspot broadcasting: SSID={actual_ssid}  password={actual_password}")
        print("Attendees: scan the WiFi QR code first to join the hotspot, then the join-URL QR (or open it once connected).\n")

        self.generate_wifi_qr(actual_ssid, actual_password)
        print(f"WiFi QR image saved to {self.wifi_qr_image_path}")

        self.generate_qr(url)
        print(f"Join URL: {url}")
        print(f"Join QR image saved to {self.qr_image_path} -- project both images for attendees.\n")

        return url


if __name__ == "__main__":
    # Quick manual check: `python session.py` prints IPs + QR without starting a server.
    s = Session(port=8000)
    s.announce()