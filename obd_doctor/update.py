"""Automatic update check using GitHub Releases.

The app asks GitHub for the latest release of UPDATE_REPO. A release must carry the
installer (OBDDoctor-Setup.exe) and, ideally, its SHA-256 checksum file
(OBDDoctor-Setup.exe.sha256) - the GitHub Actions workflow publishes both.
Downloads are verified against that checksum before the installer is started.

UPDATE_REPO is empty until the project has a GitHub repository; the update check
then reports that it isn't set up yet. For testing, OBD_DOCTOR_UPDATE_API can point
at any URL that returns a GitHub-style release JSON.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
import time
import urllib.request

from . import __version__, config

UPDATE_REPO = "Dombrohub/obd-doctor"  # GitHub repository that hosts the releases
ASSET = "OBDDoctor-Setup.exe"
PORTABLE_ASSET = "OBDDoctor-portable.zip"
CHECK_INTERVAL = 24 * 3600  # automatic checks at most once a day


class UpdateError(Exception):
    pass


def parse_version(text: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", text or "")
    return tuple(int(n) for n in nums[:4]) or (0,)


def api_url() -> str | None:
    override = os.environ.get("OBD_DOCTOR_UPDATE_API") or config.get("update_api")
    if override:
        return override
    return f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest" if UPDATE_REPO else None


def is_configured() -> bool:
    return api_url() is not None


def _get(url: str, timeout: float = 10) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": f"OBD-Doctor/{__version__}",
                                               "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except OSError as exc:  # URLError, timeouts, SSL errors
        raise UpdateError(f"Couldn't reach the update server: {getattr(exc, 'reason', exc)}") from exc


def check(timeout: float = 10) -> dict | None:
    """Return details of a newer release, or None if this is the latest version."""
    url = api_url()
    if not url:
        raise UpdateError("Automatic updates aren't set up for this copy of OBD Doctor yet.")
    try:
        rel = json.loads(_get(url, timeout))
    except ValueError as exc:
        raise UpdateError("The update server sent an unexpected reply.") from exc
    latest = rel.get("tag_name") or rel.get("name") or ""
    config.set("last_update_check", int(time.time()))
    if parse_version(latest) <= parse_version(__version__):
        return None
    assets = {a.get("name"): a for a in rel.get("assets", [])}
    inst, sha, port = assets.get(ASSET), assets.get(ASSET + ".sha256"), assets.get(PORTABLE_ASSET)
    return {"version": latest.lstrip("vV"), "notes": (rel.get("body") or "").strip(),
            "page": rel.get("html_url"), "installer_url": inst and inst.get("browser_download_url"),
            "installer_size": inst and inst.get("size"), "sha256_url": sha and sha.get("browser_download_url"),
            "portable_url": port and port.get("browser_download_url")}


def due_for_auto_check() -> bool:
    if not config.get("auto_update_check", True) or not is_configured():
        return False
    return time.time() - (config.get("last_update_check") or 0) >= CHECK_INTERVAL


def is_installed_copy() -> bool:
    """True for the installed app (Program Files); False for the portable folder or source."""
    if not getattr(sys, "frozen", False):
        return False
    exe = os.path.abspath(sys.executable).lower()
    return any(os.path.abspath(p).lower() in exe for p in
               (os.environ.get("ProgramFiles", r"C:\Program Files"),
                os.environ.get("ProgramW6432", r"C:\Program Files")) if p)


def download_installer(info: dict, progress=lambda done, total: None) -> str:
    """Download and verify the installer; returns its path."""
    if not info.get("installer_url"):
        raise UpdateError("This release has no installer attached.")
    expected = None
    if info.get("sha256_url"):
        m = re.search(r"\b[0-9a-fA-F]{64}\b", _get(info["sha256_url"]).decode("ascii", "ignore"))
        expected = m.group(0).lower() if m else None
    req = urllib.request.Request(info["installer_url"], headers={"User-Agent": f"OBD-Doctor/{__version__}"})
    path = os.path.join(tempfile.gettempdir(), f"OBDDoctor-Setup-{info['version']}.exe")
    h = hashlib.sha256()
    try:
        with urllib.request.urlopen(req, timeout=30) as r, open(path, "wb") as fh:
            total = int(r.headers.get("Content-Length") or info.get("installer_size") or 0)
            done = 0
            while True:
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                fh.write(chunk)
                h.update(chunk)
                done += len(chunk)
                progress(done, total)
    except OSError as exc:
        raise UpdateError(f"The download failed: {getattr(exc, 'reason', exc)}") from exc
    if info.get("installer_size") and done != info["installer_size"]:
        os.remove(path)
        raise UpdateError("The download was incomplete. Please try again.")
    if expected and h.hexdigest() != expected:
        os.remove(path)
        raise UpdateError("The downloaded installer didn't match its published checksum, so it was deleted. "
                          "Please try again later.")
    return path


def launch_installer(path: str) -> None:
    if sys.platform != "win32":
        raise UpdateError(f"Downloaded to {path} - run it on Windows to install.")
    os.startfile(path)  # noqa: S606 - Windows asks the user for permission (UAC) before installing
