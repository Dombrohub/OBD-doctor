"""Scan history: every scan is saved locally so changes can be tracked over time.

Files live next to the settings file (see config.py), in a "history" folder:
  Windows: %APPDATA%\\OBDEFT\\history\\
  macOS/Linux: ~/.config/obd_doctor/history/
Nothing is uploaded anywhere.
"""

from __future__ import annotations

import json
import os
import time

from . import config

KEEP = 300  # newest scans kept
TRACKED = {0x07: "LTFT B1", 0x09: "LTFT B2", 0x05: "Coolant", 0x42: "Battery"}


def history_dir() -> str:
    return os.path.join(os.path.dirname(config.config_path()), "history")


def _all_codes(scan: dict) -> set[str]:
    return {c for kind in ("stored", "pending") for codes in scan["dtcs"][kind].values() for c in codes}


def _to_file(scan: dict) -> dict:
    """JSON-safe copy (PID keys become '0x0C' strings)."""
    out = dict(scan)
    out["live"] = {f"0x{p:02X}": v for p, v in scan.get("live", {}).items()}
    ff = scan.get("freeze_frame")
    if ff:
        out["freeze_frame"] = {**ff, "data": {f"0x{p:02X}": v for p, v in ff["data"].items()}}
    out.pop("history", None)
    return out


def _from_file(data: dict) -> dict:
    data["live"] = {int(k, 16): v for k, v in data.get("live", {}).items()}
    ff = data.get("freeze_frame")
    if ff:
        ff["data"] = {int(k, 16): v for k, v in ff["data"].items()}
    return data


def load_all(vin: str | None = None) -> list[dict]:
    """Saved scans, oldest first. With a VIN, only that vehicle's scans."""
    d = history_dir()
    if not os.path.isdir(d):
        return []
    scans = []
    for name in sorted(os.listdir(d)):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(d, name), encoding="utf-8") as fh:
                data = _from_file(json.load(fh))
        except (OSError, ValueError, KeyError, TypeError):
            continue
        data["_file"] = name
        if data.get("demo") or "(simulator)" in str(data.get("adapter", "")):
            continue  # demo scans (simulated car) never count as the user's history
        if vin is None or data.get("vin") == vin:
            scans.append(data)
    return scans


def summarize(scan: dict) -> dict:
    st = scan.get("status") or {}
    live = scan.get("live", {})
    return {"timestamp": scan.get("timestamp"), "vin": scan.get("vin"), "mil_on": st.get("mil_on"),
            "codes": sorted(_all_codes(scan)), "tracked": {label: live.get(pid) for pid, label in TRACKED.items()}}


def compare(previous: list[dict], scan: dict) -> dict | None:
    """What changed compared with this vehicle's earlier scans."""
    if not previous:
        return None
    last = previous[-1]
    before, now = _all_codes(last), _all_codes(scan)
    ever = set().union(*(_all_codes(s) for s in previous[:-1])) if len(previous) > 1 else set()
    returned = sorted(c for c in now - before if c in ever)  # gone last time, back now
    return {"previous_time": last.get("timestamp"), "scans_before": len(previous),
            "new": sorted(now - before - set(returned)), "returned": returned,
            "resolved": sorted(before - now), "persisting": sorted(now & before)}


def save(scan: dict) -> dict | None:
    """Save a scan and attach scan['history'] (changes since the last scan of this vehicle)."""
    vin = scan.get("vin")
    previous = load_all(vin) if vin else []
    scan["history"] = compare(previous, scan)
    d = history_dir()
    os.makedirs(d, exist_ok=True)
    name = time.strftime("%Y%m%d-%H%M%S") + f"_{(vin or 'unknown')[-8:]}.json"
    with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
        json.dump(_to_file(scan), fh, indent=1, ensure_ascii=False)
    files = sorted(f for f in os.listdir(d) if f.endswith(".json"))
    for old in files[:-KEEP]:
        try:
            os.remove(os.path.join(d, old))
        except OSError:
            pass
    return scan["history"]


def clear() -> int:
    d = history_dir()
    n = 0
    if os.path.isdir(d):
        for f in os.listdir(d):
            if f.endswith(".json"):
                os.remove(os.path.join(d, f))
                n += 1
    return n
