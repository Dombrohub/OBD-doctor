"""High-level OBD-II operations on top of the ELM327 driver."""

from __future__ import annotations

import time

from . import decoder
from .elm327 import ELM327, ecu_name, ecu_sort_key
from .modules import ModuleScanner, merge_into_dtcs


class Scanner:
    def __init__(self, elm: ELM327):
        self.elm = elm
        self._supported: set[int] | None = None

    # ---- helpers
    def _responses(self, cmd: str, service: int, pid: int | None = None):
        """Yield (ecu_addr, message) for positive replies, engine ECU first."""
        replies = self.elm.query(cmd)
        for addr in sorted(replies, key=ecu_sort_key):
            for msg in replies[addr]:
                if msg and msg[0] == service and (pid is None or (len(msg) > 1 and msg[1] == pid)):
                    yield addr, msg

    # ---- supported PIDs
    def supported_pids(self) -> set[int]:
        if self._supported is None:
            pids: set[int] = set()
            base = 0x00
            while base <= 0xE0:
                got = False
                for _, msg in self._responses(f"01{base:02X}", 0x41, base):
                    if len(msg) >= 6:
                        pids |= decoder.parse_supported(msg[2:6], base)
                        got = True
                if not got or (base + 0x20) not in pids:
                    break
                base += 0x20
            self._supported = pids
        return self._supported

    # ---- live data
    def read_pid(self, pid: int):
        for _, msg in self._responses(f"01{pid:02X}", 0x41, pid):
            val = decoder.decode_pid(pid, msg[2:])
            if val is not None:
                return val
        return None

    def read_live(self, pids=None) -> dict[int, object]:
        supported = self.supported_pids()
        wanted = pids if pids is not None else decoder.REPORT_PIDS
        out = {}
        for pid in wanted:
            if pid in supported and pid in decoder.PIDS:
                v = self.read_pid(pid)
                if v is not None:
                    out[pid] = v
        return out

    # ---- trouble codes
    def read_dtcs(self, mode: str = "03") -> dict[str, list[str]]:
        service = 0x40 + int(mode, 16)
        result: dict[str, list[str]] = {}
        for addr, msg in self._responses(mode, service):
            codes = decoder.parse_dtc_message(msg, self.elm.is_can)
            lst = result.setdefault(ecu_name(addr), [])
            lst += [c for c in codes if c not in lst]
        return result

    def clear_dtcs(self) -> bool:
        replies = self.elm.query("04", timeout=10)
        return any(m and m[0] == 0x44 for msgs in replies.values() for m in msgs)

    # ---- freeze frame
    def read_freeze_frame(self) -> dict:
        trigger = None
        for _, msg in self._responses("020200", 0x42, 0x02):
            if len(msg) >= 5 and (msg[3] or msg[4]):
                trigger = decoder.decode_dtc(msg[3], msg[4])
                break
        if not trigger:
            return {}
        data = {}
        for pid in decoder.FREEZE_PIDS:
            if pid not in self.supported_pids():
                continue
            for _, msg in self._responses(f"02{pid:02X}00", 0x42, pid):
                val = decoder.decode_pid(pid, msg[3:])
                if val is not None:
                    data[pid] = val
                    break
        return {"trigger_dtc": trigger, "data": data}

    # ---- vehicle info
    def read_vin(self) -> str | None:
        raw = b""
        for _, msg in self._responses("0902", 0x49, 0x02):
            raw += msg[3:]
        vin = "".join(chr(b) for b in raw if 0x30 <= b <= 0x5A)
        return vin[-17:] if len(vin) >= 17 else (vin or None)

    # ---- everything
    def scan_modules(self, discover=False, progress=lambda msg: None, vin=None) -> dict:
        """ABS / airbag modules (manufacturer-level diagnostics). The VIN picks the brand's addresses."""
        return ModuleScanner(self.elm, progress).scan(discover=discover, vin=vin)

    def full_scan(self, progress=lambda msg: None, modules=True) -> dict:
        progress("Reading vehicle info")
        vin = self.read_vin()
        progress("Reading monitor status")
        status = None
        for _, msg in self._responses("0101", 0x41, 0x01):
            status = decoder.decode_status(msg[2:])
            break
        progress("Reading stored trouble codes")
        stored = self.read_dtcs("03")
        progress("Reading pending trouble codes")
        pending = self.read_dtcs("07")
        progress("Reading permanent trouble codes")
        permanent = self.read_dtcs("0A")
        progress("Reading freeze frame")
        freeze = self.read_freeze_frame()
        progress("Reading live sensor data")
        live = self.read_live()
        result = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "adapter": self.elm.version,
            "protocol": self.elm.protocol_name,
            "adapter_voltage": self.elm.voltage,
            "vin": vin,
            "status": status,
            "dtcs": {"stored": stored, "pending": pending, "permanent": permanent},
            "freeze_frame": freeze,
            "live": live,
            "modules": None,
        }
        if modules:
            progress("Checking ABS & airbag modules")
            try:
                result["modules"] = self.scan_modules(progress=progress, vin=vin)
            except Exception as exc:  # never let the module check break the main scan
                result["modules"] = {"supported": True, "list": [], "searched": False,
                                     "note": f"The ABS & airbag check failed on this adapter: {exc}"}
            merge_into_dtcs(result)
        return result
