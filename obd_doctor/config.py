"""Per-user settings (API key, vehicle description).

Stored as JSON in:
  Windows: %APPDATA%\\OBDEFT\\config.json
  macOS/Linux: ~/.config/obd_eft/config.json

The app was called "OBD Doctor" before; its old settings folder (OBDDoctor / obd_doctor)
is moved to the new name automatically, together with the scan history.
"""

from __future__ import annotations

import json
import os
import sys


def _dirs() -> tuple[str, str]:
    """(current settings folder, folder used before the rename)."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "OBDEFT"), os.path.join(base, "OBDDoctor")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "obd_eft"), os.path.join(base, "obd_doctor")


def config_path() -> str:
    new, old = _dirs()
    if not os.path.exists(new) and os.path.isdir(old):
        try:
            os.rename(old, new)  # keep settings, API key and history from "OBD Doctor"
        except OSError:
            pass
    return os.path.join(new if os.path.isdir(new) or not os.path.isdir(old) else old, "config.json")


def load() -> dict:
    try:
        with open(config_path(), encoding="utf-8") as fh:
            data = json.load(fh)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(data: dict) -> None:
    path = config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, path)
    if sys.platform != "win32":
        os.chmod(path, 0o600)  # the file may hold an API key


def get(key: str, default=None):
    return load().get(key, default)


def set(key: str, value) -> None:  # noqa: A001 - mirrors get()
    data = load()
    if value in (None, ""):
        data.pop(key, None)
    else:
        data[key] = value
    save(data)
