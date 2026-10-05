"""
crash_report.py -- error reports the user chooses to send.

Nothing is sent automatically. When DwaniLive shows its error screen the user
can press "Send error report", see exactly what will be sent, and confirm.
Before anything is shown or sent, personal details are scrubbed:

  * the Windows user name and home folder  -> ~ / <user>
  * the computer's name                     -> <computer>
  * e-mail addresses                        -> <email>
  * IP addresses (except 127.0.0.1)         -> <ip>
  * licence keys / long tokens              -> <secret>

The report goes to the DwaniLive website (/api/crash-report), which answers
with a reference like DL-ERR-1042 the user can quote when they e-mail.
"""

from __future__ import annotations

import getpass
import json
import platform
import re
import urllib.request
from pathlib import Path

MAX_BYTES = 60_000
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_IP = re.compile(r"\b(?!127\.0\.0\.1\b)(?:\d{1,3}\.){3}\d{1,3}\b")
_TOKEN = re.compile(r"\b(?=[A-Za-z0-9+/_=-]*\d)(?=[A-Za-z0-9+/_=-]*[A-Za-z])[A-Za-z0-9+/_=-]{32,}\b")
_KEYLIKE = re.compile(r"\b(?:DL|DWANI|LDST)[-_][A-Z0-9]{4,}(?:[-_][A-Z0-9]{4,})+\b", re.I)


def _private_bits() -> list[tuple[str, str]]:
    bits = []
    home = str(Path.home())
    if home and len(home) > 3:
        bits.append((home, "~"))
    try:
        user = getpass.getuser()
        if user and len(user) >= 2:
            bits.append((user, "<user>"))
    except Exception:
        pass
    node = platform.node()
    if node and len(node) >= 3:
        bits.append((node, "<computer>"))
    return bits


def scrub(text: str, extra: list[tuple[str, str]] | None = None) -> str:
    # Patterns first (so "khushi.m@gmail.com" goes as a whole e-mail), then the
    # personal words, longest first (so "KHUSHI-LAPTOP" isn't half-replaced
    # by the shorter user name "Khushi").
    out = _EMAIL.sub("<email>", text or "")
    out = _KEYLIKE.sub("<secret>", out)
    out = _TOKEN.sub("<secret>", out)
    out = _IP.sub("<ip>", out)
    bits = sorted((extra or []) + _private_bits(), key=lambda b: len(b[0]), reverse=True)
    for needle, repl in bits:
        for form in {needle, needle.replace("\\", "/")}:
            out = re.sub(re.escape(form), repl, out, flags=re.I)
    return out


def build(error: str, log_lines: list[str] | None = None, system: dict | None = None, version: str = "") -> dict:
    """The report, already scrubbed. `system` is appenv.system_summary()-style data."""
    # only what helps diagnose; folders (exe_dir, data_dir...) are left out entirely
    sysinfo = {k: v for k, v in (system or {}).items() if k in (
        "app_version", "platform", "python", "frozen", "machine", "cpu_count", "ram_gb", "free_disk_gb")}
    report = {
        "version": version or str(sysinfo.get("app_version", "")),
        "os": scrub(str(sysinfo.get("platform") or platform.platform())),
        "system": json.loads(scrub(json.dumps(sysinfo, default=str))),
        "error": scrub(error or "")[:4000],
        "log": scrub("\n".join((log_lines or [])[-150:])),
    }
    raw = json.dumps(report, ensure_ascii=False)
    while len(raw.encode("utf-8")) > MAX_BYTES and report["log"]:
        report["log"] = report["log"][len(report["log"]) // 4:]       # keep the most recent log lines
        raw = json.dumps(report, ensure_ascii=False)
    return report


def preview(report: dict) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2)


def send(report: dict, base_url: str, timeout: float = 15) -> str:
    """POSTs the report; returns the reference (e.g. DL-ERR-1042). Raises on failure."""
    req = urllib.request.Request(base_url.rstrip("/") + "/api/crash-report",
                                 data=json.dumps(report).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "DwaniLive"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))["ref"]
