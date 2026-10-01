"""
updater.py -- "a new DwaniLive is available" + one-click update.

How it works:
  * On launch (in the background, never blocking startup, silently skipped
    when offline) asks GitHub for the latest release of UPDATE_REPO.
  * If its tag (v1.2.0) is newer than appenv.APP_VERSION, the launcher
    window shows a banner.
  * "Update now" downloads DwaniLive-Setup.exe with the same resumable
    downloader the models use, starts it with /SILENT (progress bar only, no
    wizard), and quits DwaniLive so the installer can replace the files.
    The installer relaunches DwaniLive when done (see DwaniLive.iss [Run]).

Disable with the env var DWANI_NO_UPDATE_CHECK=1 (e.g. kiosk/lab machines).
"""

from __future__ import annotations

import json
import os
import re
import ssl
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import appenv

UPDATE_REPO = "khushi-070906/Dwani"
API_URL = os.environ.get("DWANI_UPDATE_API", f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest")
INSTALLER_NAME = "DwaniLive-Setup.exe"


@dataclass
class UpdateInfo:
    version: str
    installer_url: str
    notes_url: str
    size_bytes: int = 0


def parse_version(v: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", v or "")
    return tuple(int(n) for n in nums[:3]) + (0,) * (3 - min(3, len(nums)))


def is_newer(remote: str, local: str) -> bool:
    return parse_version(remote) > parse_version(local)


def check_for_update(timeout: float = 6.0) -> Optional[UpdateInfo]:
    """Returns UpdateInfo if a newer release exists, else None. Never raises."""
    if os.environ.get("DWANI_NO_UPDATE_CHECK"):
        return None
    try:
        req = urllib.request.Request(API_URL, headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"DwaniLive/{appenv.APP_VERSION}",
        })
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
        except ssl.SSLError:
            import certifi

            resp = urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context(cafile=certifi.where()))
        with resp:
            data = json.loads(resp.read().decode("utf-8"))
        tag = data.get("tag_name", "")
        if data.get("draft") or data.get("prerelease") or not is_newer(tag, appenv.APP_VERSION):
            return None
        asset = next((a for a in data.get("assets", []) if a.get("name") == INSTALLER_NAME), None)
        if not asset:
            return None
        return UpdateInfo(
            version=tag.lstrip("v"),
            installer_url=asset["browser_download_url"],
            notes_url=data.get("html_url", f"https://github.com/{UPDATE_REPO}/releases/latest"),
            size_bytes=int(asset.get("size") or 0),
        )
    except Exception as exc:  # offline, rate-limited, GitHub down: just no banner
        print(f"(Update check skipped: {exc.__class__.__name__})")
        return None


def download_installer(info: UpdateInfo, progress_cb: Optional[Callable] = None) -> Path:
    import model_setup

    dest = appenv.RUNTIME_DIR / f"DwaniLive-Setup-{info.version}.exe"
    if dest.exists() and info.size_bytes and dest.stat().st_size == info.size_bytes:
        return dest
    return model_setup.download_file(
        info.installer_url, dest, label=f"DwaniLive {info.version}", stage="update",
        progress_cb=progress_cb, expected_size=info.size_bytes or None,
    )


def launch_installer(path: Path) -> None:
    """Starts the installer detached; caller must then exit DwaniLive so the
    installer (which waits on our single-instance mutex) can proceed."""
    if sys.platform != "win32":
        raise RuntimeError("Automatic update is only supported on Windows.")
    # Start the installer ~3s from now, from a detached helper, so DwaniLive
    # has fully exited (and released its single-instance mutex, which the
    # installer's AppMutex check waits on) before setup looks for it. With
    # /SUPPRESSMSGBOXES a "please close DwaniLive" prompt would default to
    # Cancel and silently abort the update.
    cmd = f'ping -n 4 127.0.0.1 >nul & start "" "{path}" /SILENT /SUPPRESSMSGBOXES /NORESTART'
    subprocess.Popen(
        ["cmd.exe", "/c", cmd],
        close_fds=True,
        creationflags=0x00000008 | 0x00000200 | 0x08000000,  # DETACHED | NEW_PROCESS_GROUP | NO_WINDOW
    )


def cleanup_old_installers() -> None:
    for p in appenv.RUNTIME_DIR.glob("DwaniLive-Setup-*.exe*"):
        try:
            if not p.name.endswith(f"{appenv.APP_VERSION}.exe"):
                p.unlink()
        except OSError:
            pass
