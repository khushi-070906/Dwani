"""
preflight.py -- "is everything ready?" checks for the presenter page.

Most failed sessions aren't bugs: the laptop is on a network phones can't
reach, Windows Firewall is blocking the port, the demo (fake) models are
loaded, or the microphone is muted. This runs the server-side checks; the
browser-side checks (microphone permission + live level, secure context)
run in host.html. Each check returns a status and, when it isn't OK, a
concrete fix in plain language.
"""

from __future__ import annotations

import ipaddress
import socket
import subprocess
import sys
from dataclasses import asdict, dataclass

OK, WARN, FAIL, INFO = "ok", "warn", "fail", "info"


@dataclass
class Check:
    id: str
    title: str
    status: str
    detail: str
    fix: str = ""
    help: str = ""   # anchor in static/help.html, e.g. "firewall" -> /help#firewall


# ---------------------------------------------------------------------------
# network
# ---------------------------------------------------------------------------

def classify_ip(ip: str) -> tuple[str, str]:
    """(kind, human label) for a laptop IPv4 address."""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return "unknown", "unknown address"
    if a.is_loopback:
        return "loopback", "this computer only"
    if a in ipaddress.ip_network("169.254.0.0/16"):
        return "linklocal", "no network (self-assigned address)"
    if a in ipaddress.ip_network("192.168.137.0/24"):
        return "hotspot-laptop", "this laptop's own Mobile Hotspot"
    if a in ipaddress.ip_network("172.20.10.0/28"):
        return "hotspot-phone", "an iPhone hotspot"
    if a in ipaddress.ip_network("192.168.43.0/24"):
        return "hotspot-phone", "an Android phone hotspot"
    if a in ipaddress.ip_network("100.64.0.0/10"):
        return "vpn", "a VPN / tunnel (e.g. Tailscale)"
    if a.is_private:
        return "lan", "Wi-Fi / LAN"
    return "public", "a public internet address"


def network_checks(ips: list[str], primary: str, port: int) -> list[Check]:
    checks: list[Check] = []
    if not ips:
        checks.append(Check("network", "Network", FAIL, "This laptop isn't connected to any network.",
                            "Connect to Wi-Fi, or turn on a phone hotspot and connect the laptop to it.", "hotspot"))
        return checks
    kind, label = classify_ip(primary)
    if kind == "linklocal":
        checks.append(Check("network", "Network", FAIL, f"{primary}: {label}.",
                            "The Wi-Fi didn't give the laptop an address. Reconnect, or use a phone hotspot.", "hotspot"))
    elif kind in ("hotspot-laptop", "hotspot-phone"):
        checks.append(Check("network", "Network", OK, f"{primary} on {label}: the most reliable setup.",
                            "Attendees' phones must join this same hotspot."))
    elif kind == "lan":
        checks.append(Check("network", "Network", WARN, f"{primary} on {label}.",
                            "Fine at home or office. College/hotel Wi-Fi often blocks phone-to-laptop traffic "
                            "(\"client isolation\"); if the phone test below fails, use a phone hotspot instead.", "phones"))
    elif kind == "vpn":
        checks.append(Check("network", "Network", WARN, f"{primary} is {label}.",
                            "Turn off the VPN during the session so phones get the Wi-Fi address.", "vpn"))
    else:
        checks.append(Check("network", "Network", WARN, f"{primary} ({label}).",
                            "Use a normal Wi-Fi or a phone hotspot for the session.", "hotspot"))
    others = [i for i in ips if i != primary]
    if others:
        checks.append(Check("adapters", "Network adapters", INFO,
                            f"Also on {', '.join(others)} (VPN / virtual adapters).",
                            f"Phones should use {primary}. The QR code already does."))
    checks.append(port_check(primary, port))
    return checks


def port_check(ip: str, port: int) -> Check:
    try:
        with socket.create_connection((ip, port), timeout=1.5):
            pass
        return Check("port", "Join address", OK, f"DwaniLive is listening on {ip}:{port}.")
    except OSError as exc:
        return Check("port", "Join address", FAIL, f"Couldn't reach {ip}:{port} ({exc.__class__.__name__}).",
                     "Restart DwaniLive. If it still fails, another app may be using the port.", "wont-open")


def firewall_check(rule_name: str = "DwaniLive LAN") -> Check:
    if sys.platform != "win32":
        return Check("firewall", "Firewall", INFO, "Not Windows: no firewall rule needed in most cases.")
    try:
        r = subprocess.run(["netsh", "advfirewall", "firewall", "show", "rule", f"name={rule_name}"],
                           capture_output=True, text=True, errors="replace", timeout=6,
                           creationflags=0x08000000)
        if r.returncode == 0 and rule_name in (r.stdout or ""):
            return Check("firewall", "Windows Firewall", OK, "Phones are allowed to connect.")
    except Exception:  # noqa: BLE001
        return Check("firewall", "Windows Firewall", WARN, "Couldn't read the firewall settings.",
                     "If phones can't connect, close and reopen DwaniLive and click Allow on the network prompt.", "firewall")
    return Check("firewall", "Windows Firewall", WARN, "No DwaniLive firewall rule found, so Windows may block phones.",
                 "Close and reopen DwaniLive and click Yes / Allow when Windows asks about network access.", "firewall")


def phones_check(connected: int) -> Check:
    if connected > 0:
        return Check("phones", "Phone test", OK, f"{connected} device(s) connected: phones can reach this laptop.")
    return Check("phones", "Phone test", WARN, "No phone has joined yet.",
                 "Scan the join QR with your own phone now. If it doesn't open within 10 seconds, the network "
                 "is blocking phones: switch everyone to a phone hotspot.", "phones")


# ---------------------------------------------------------------------------
# models & licence
# ---------------------------------------------------------------------------

def _class_chain(obj, depth: int = 0, seen=None) -> list[str]:
    """Class names of a backend and whatever it wraps (cache/glossary/decision layers)."""
    seen = seen if seen is not None else set()
    if obj is None or id(obj) in seen or depth > 5:
        return []
    seen.add(id(obj))
    names = [type(obj).__name__]
    for attr in ("_inner", "inner", "_backend", "backend", "_translator", "translator", "_base", "base", "_asr", "asr"):
        child = getattr(obj, attr, None)
        if child is not None and not isinstance(child, (str, int, float, bool, dict, list)):
            names += _class_chain(child, depth + 1, seen)
    return names


def model_checks(asr, translator) -> list[Check]:
    out = []
    for cid, title, obj, real_hint in (("asr", "Speech recognition", asr, "Whisper"),
                                       ("translation", "Translation", translator, "NLLB")):
        chain = _class_chain(obj)
        if not chain:
            out.append(Check(cid, title, FAIL, "Not loaded.", "Restart DwaniLive.", "wont-open"))
        elif any(n.startswith("Fake") for n in chain):
            out.append(Check(cid, title, FAIL, "Demo mode: placeholder output, not real recognition/translation.",
                             "Start DwaniLive from the desktop app (not run.py) so the real models load.", "demo-mode"))
        else:
            out.append(Check(cid, title, OK, f"{real_hint} model loaded."))
    return out


def licence_check(lic) -> Check:
    if lic is None:
        return Check("licence", "Plan", INFO, "No licence information.")
    tier = str(getattr(lic, "tier", "free")).title()
    cap = getattr(lic, "max_attendees", None)
    return Check("licence", "Plan", INFO, f"{tier} plan" + (f": up to {cap} attendees." if cap else "."),
                 "Upgrade from your dashboard if you expect more people." if cap and cap < 50 else "")


def summarize(checks: list[Check]) -> dict:
    worst = FAIL if any(c.status == FAIL for c in checks) else WARN if any(c.status == WARN for c in checks) else OK
    return {"overall": worst, "checks": [asdict(c) for c in checks]}
