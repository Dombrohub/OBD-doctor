"""Support file: one .zip with everything needed to troubleshoot a problem.

Contents: report, scan data, app log, recent raw adapter traffic and system details.
Privacy: the Anthropic API key is never included, and the VIN is partly masked
(the first 11 characters - make, model, year, plant - are kept; the serial number is hidden).
"""

from __future__ import annotations

import json
import os
import platform
import sys
import time
import zipfile

from . import __version__, config, elm327

README = """OBD EFT support file
=======================

This file helps troubleshoot OBD EFT. It contains:

  report.txt           The scan report (and any AI explanation), as shown in the app
  scan.json            The same scan as structured data
  app-log.txt          The app's log
  adapter-traffic.txt  The most recent raw messages between OBD EFT and the OBD adapter
  system.txt           App version, Windows/OS version, adapter details

Privacy:
  - Your Anthropic API key is NOT included.
  - Your VIN is partly masked: make/model/year are kept, the serial number is hidden.
  - Nothing is sent anywhere automatically. You choose who to share this file with.
"""


def mask_vin(vin: str | None) -> str | None:
    if not vin or len(vin) < 17:
        return vin
    return vin[:11] + "******"


def _mask_text(text: str, vin: str | None) -> str:
    return text.replace(vin, mask_vin(vin)) if vin and len(vin) >= 17 else text


def _traffic_text() -> str:
    """Raw adapter traffic, with the VIN reply (service 09 02) hidden."""
    out, hide = [], False
    for line in list(elm327.TRAFFIC):
        if " >> " in line:
            hide = line.rstrip("'").replace(" ", "").upper().endswith("0902")
            out.append(line)
        elif hide:
            out.append(line.split(" << ")[0] + " << '[VIN reply hidden for privacy]'")
        else:
            out.append(line)
    return "\n".join(out) or "(no adapter traffic recorded yet)"


def _system_text(elm=None, port: str | None = None) -> str:
    try:
        from serial.tools import list_ports
        ports = ", ".join(f"{p.device} ({p.description})" for p in list_ports.comports()) or "none found"
    except Exception as exc:  # pyserial missing or no permission
        ports = f"unavailable ({exc})"
    try:
        from . import history
        n_hist = len([f for f in os.listdir(history.history_dir()) if f.endswith(".json")])
    except OSError:
        n_hist = 0
    lines = [
        f"OBD EFT version: {__version__}",
        f"Created: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Operating system: {platform.platform()}",
        f"Python: {sys.version.split()[0]} ({'packaged app' if getattr(sys, 'frozen', False) else 'source'})",
        f"Adapter port chosen: {port or 'not set'}",
        f"Serial ports seen: {ports}",
    ]
    if elm is not None:
        lines += [f"Adapter: {elm.version or 'unknown'}",
                  f"Protocol: {elm.protocol_name if elm.protocol else 'not detected'}",
                  f"Adapter voltage: {elm.voltage if elm.voltage is not None else 'unknown'}"]
    else:
        lines.append("Adapter: not connected")
    lines += [f"AI key configured: {'yes' if (os.environ.get('ANTHROPIC_API_KEY') or config.get('anthropic_api_key')) else 'no'}",
              f"Saved scans in history: {n_hist}"]
    return "\n".join(lines)


def write_support_file(path: str, scan: dict | None = None, findings=None, log_text: str = "",
                       elm=None, port: str | None = None, ai_transcript: list | None = None) -> str:
    """Write the support .zip and return its path."""
    from .__main__ import build_json, text_report

    vin = (scan or {}).get("vin")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.txt", README)
        if scan:
            report = text_report(scan, findings or [])
            if ai_transcript:
                report += "\n== AI EXPLANATION (Claude) " + "=" * 37 + "\n\n" + "\n\n".join(
                    (f"Q: {t}" if r == "user" else t) for r, t in ai_transcript) + "\n"
            z.writestr("report.txt", _mask_text(report, vin))
            data = build_json(scan, findings or [])
            data["vin"] = mask_vin(vin)
            if ai_transcript:
                data["ai_explanation"] = [{"role": r, "text": t} for r, t in ai_transcript]
            z.writestr("scan.json", _mask_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), vin))
        else:
            z.writestr("report.txt", "No scan was completed in this session.\n")
        z.writestr("app-log.txt", _mask_text(log_text or "(empty)", vin))
        z.writestr("adapter-traffic.txt", _traffic_text())
        z.writestr("system.txt", _system_text(elm, port))
    return path


def default_filename() -> str:
    return f"OBDEFT-support-{time.strftime('%Y%m%d-%H%M')}.zip"
