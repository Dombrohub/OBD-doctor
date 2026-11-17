"""Reading trouble codes from ABS, airbag (SRS) and transmission (TCM) modules.

Generic OBD-II (mode 03) only reaches emissions-related computers. ABS and airbag
modules have to be asked directly. Three schemes are supported, all on the CAN bus:

  * Standard UDS (ISO 14229) - Ford/Mazda, Toyota/Lexus/Subaru, Hyundai/Kia, VW group,
    Nissan, 2020+ GM, and most others: service 0x19 02 ReadDTCInformation over ISO-TP,
    with a KWP2000 (service 0x18) fallback for older modules.
  * GM GMLAN (most 2005-2019 GM vehicles, including Holden VE/VF Commodore and the Korean- and
    Opel-built Holdens): service 0xA9 81 ReadDTCInformationByStatus.
    The request goes to 0x24x and each code comes back as its own unsegmented (UUDT)
    frame on 0x54x.
  * BMW / MINI (E-series D-CAN from about 2007, F-series): ISO-TP with CAN extended
    addressing. Requests go out on 0x6F1 with the module address as the first data byte,
    replies come back on 0x600 + module address starting with 0xF1. Needs the
    adapter's ATCEA command. G-series (2017+) needs Ethernet (ENET) and is not reachable.

The VIN tells us the brand, so the quick check only asks that brand's addresses.
"""

from __future__ import annotations

import re

from .decoder import decode_dtc
from .elm327 import _isotp_reassemble

# Standard UDS modules, 11-bit CAN: (system, request id, response id, family key, family name)
KNOWN_MODULES = [
    ("ABS", 0x760, 0x768, "Ford", "Ford / Mazda / Lincoln"),
    ("Airbag", 0x737, 0x73F, "Ford", "Ford / Mazda / Lincoln"),
    ("ABS", 0x7B0, 0x7B8, "Toyota", "Toyota / Lexus / Subaru"),
    ("Airbag", 0x780, 0x788, "Toyota", "Toyota / Lexus / Subaru"),
    ("ABS", 0x7D1, 0x7D9, "Hyundai", "Hyundai / Kia"),
    ("Airbag", 0x7D2, 0x7DA, "Hyundai", "Hyundai / Kia"),
    ("ABS", 0x713, 0x77D, "VW", "VW / Audi / Skoda / Seat"),
    ("Airbag", 0x715, 0x77F, "VW", "VW / Audi / Skoda / Seat"),
    ("ABS", 0x740, 0x760, "Nissan", "Nissan / Infiniti"),
    # Transmission control module at the standard ISO 15765 address - used by most brands
    ("Transmission", 0x7E1, 0x7E9, "ALL", "standard transmission address"),
    ("Airbag", 0x752, 0x772, "Nissan", "Nissan / Infiniti"),
]
# GM GMLAN: (system, request id, module name) - USDT reply on id + 0x400, UUDT codes on id + 0x300
# GM GMLAN: (system, request id, response id, module name). Codes come back as UUDT frames on 0x500 + low byte
# of the response id (EBCM 0x643 -> 0x543, TCM 0x7EA -> 0x5EA).
GM_MODULES = [("ABS", 0x243, 0x643, "EBCM"), ("Airbag", 0x247, 0x647, "SDM"), ("Transmission", 0x7E2, 0x7EA, "TCM")]
# BMW / MINI: (system, module address, module name)
BMW_MODULES = [("ABS", 0x29, "DSC"), ("Airbag", 0x01, "ACSM"), ("Transmission", 0x18, "EGS")]

ABS_WORDS = ("ABS", "ESP", "ESC", "EBCM", "BRAKE", "VSA", "DSC", "VSC", "STABIL")
AIRBAG_WORDS = ("SRS", "AIRBAG", "AIR BAG", "RCM", "ACU", "ORC", "RESTRAINT", "OCS", "SDM", "ACSM", "MRS")
TRANS_WORDS = ("TCM", "TCU", "EGS", "TRANS", "GEARBOX", "DSG", "CVT", "AUTOMATIC")

STATUS_BITS = {0x01: "failing right now", 0x04: "pending", 0x08: "confirmed", 0x80: "warning light requested"}

UNSUPPORTED_NOTE = ("ABS and airbag codes can only be read on CAN-bus cars (most cars from about 2008 on). "
                    "This car uses an older protocol.")
BRAND_NOTE = ("Mercedes, most Chrysler/Stellantis vehicles and BMW G-series (2017+) use protocols or "
              "connections an ELM327 adapter can't reach, and usually need a dealer-level scan tool.")


# --------------------------------------------------------------------------- brand from VIN
def brand_family(vin: str | None) -> str | None:
    """Manufacturer family from the VIN's first characters (World Manufacturer Identifier)."""
    if not vin or len(vin) < 3:
        return None
    w = vin[:3].upper()
    if w[:2] in ("1G", "2G", "3G", "KL") or w in ("W0L", "W0V", "6G1", "6G2", "6G3", "6H8"):  # incl. Holden
        return "GM"
    if w in ("WBA", "WBS", "WBY", "WMW", "4US", "5UX", "5UM", "5YM"):
        return "BMW"
    if w[:2] in ("1F", "2F", "3F", "JM") or w in ("WF0", "1ZV", "4F2", "4F4", "1LN", "5LM", "3LN"):
        return "Ford"
    if w[:2] == "JT" or w in ("4T1", "4T3", "5TD", "5TF", "2T1", "2T3", "JF1", "JF2", "4S3", "4S4"):
        return "Toyota"
    if w in ("KMH", "5NP", "KNA", "KND", "5XY", "KM8", "3KP"):
        return "Hyundai"
    if w in ("WVW", "WVG", "3VW", "1VW", "WAU", "WA1", "TMB", "VSS", "TRU", "WV1", "WV2"):
        return "VW"
    if w[:2] == "JN" or w in ("1N4", "1N6", "3N1", "5N1", "5N3", "3N6"):
        return "Nissan"
    return None


def classify(name: str | None, codes: list[str], guess: str | None) -> str:
    up = (name or "").upper()
    if any(w in up for w in AIRBAG_WORDS):
        return "Airbag"
    if any(w in up for w in ABS_WORDS):
        return "ABS"
    if any(w in up for w in TRANS_WORDS):
        return "Transmission"
    if guess:
        return guess
    if codes and all(c[:3] in ("P07", "P08", "P09") for c in codes):
        return "Transmission"
    if codes and all(c.startswith("C") for c in codes):
        return "ABS"
    if codes and all(c.startswith("B00") for c in codes):
        return "Airbag"
    return "Other"


# --------------------------------------------------------------------------- code parsing
def _dtc_record(code: str, status: int, flags_known: bool = True) -> dict:
    if flags_known:
        confirmed, pending, active = bool(status & 0x08), bool(status & 0x04), bool(status & 0x01)
        if not (confirmed or pending or active):
            confirmed = True  # stored with no flags set
        text = [t for bit, t in STATUS_BITS.items() if status & bit]
    else:  # GM: the status byte isn't decoded here - report the code as stored
        confirmed, pending, active = True, False, False
        text = [f"status byte 0x{status:02X}"]
    return {"code": code, "status_byte": status, "confirmed": confirmed, "pending": pending, "active": active,
            "status": text}


def parse_uds_dtcs(msg: bytes, bmw: bool = False) -> list[dict]:
    """msg = 59 02 <availability mask> [DTC_hi DTC_mid FTB status]..."""
    out = []
    data = msg[3:]
    for i in range(0, len(data) - 3, 4):
        b1, b2, ftb, st = data[i:i + 4]
        if b1 == b2 == ftb == 0:
            continue
        if bmw:
            code = f"BMW-{b1:02X}{b2:02X}{ftb:02X}"
        else:
            code = decode_dtc(b1, b2) + (f"-{ftb:02X}" if ftb else "")
        out.append(_dtc_record(code, st))
    return out


def parse_kwp_dtcs(msg: bytes, bmw: bool = False) -> list[dict]:
    """msg = 58 <count> [DTC_hi DTC_lo status]..."""
    out = []
    data = msg[2:]
    for i in range(0, len(data) - 2, 3):
        b1, b2, st = data[i:i + 3]
        if b1 == b2 == 0:
            continue
        # KWP status: bit 6 = stored, bit 5 = pending, bit 7 = warning lamp (common convention)
        uds_like = (0x08 if st & 0x40 else 0) | (0x04 if st & 0x20 else 0) | (0x01 if st & 0x80 else 0)
        code = f"BMW-{b1:02X}{b2:02X}" if bmw else decode_dtc(b1, b2)
        out.append(_dtc_record(code, uds_like or 0x08))
    return out


def parse_gm_uudt(frames: list[bytes]) -> list[dict]:
    """GMLAN $A9 replies: one UUDT frame per code, [81, DTC_hi, DTC_lo, FTB, status]; DTC 0000 ends the list."""
    out, seen = [], set()
    for f in frames:
        if len(f) < 5 or f[0] != 0x81:
            continue
        hi, lo, ftb, st = f[1:5]
        if hi == lo == 0:
            break
        code = decode_dtc(hi, lo) + (f"-{ftb:02X}" if ftb else "")
        if code not in seen:
            seen.add(code)
            out.append(_dtc_record(code, st, flags_known=False))
    return out


# --------------------------------------------------------------------------- scanner
class ModuleScanner:
    def __init__(self, elm, progress=lambda m: None):
        self.elm = elm
        self.progress = progress
        self.ext = elm.protocol in ("7", "9")  # 29-bit CAN
        self.fc_ok = True    # adapter supports custom flow control (many cheap clones don't)
        self.cea_ok = True   # adapter supports CAN extended addressing (needed for BMW)

    # ---------------------------------------------------------------- adapter helpers
    def _at(self, cmd: str) -> bool:
        reply = self.elm.send(cmd, timeout=2)
        return not any(r.strip() == "?" for r in reply)

    def _timeout(self, ms: int):
        self._at(f"ATST{min(255, ms // 4):02X}")

    def restore(self):
        """Put the adapter back into normal OBD-II mode."""
        self.elm.send("ATD", timeout=3)
        for c in ("ATE0", "ATL0", "ATS1", "ATH1", f"ATSP{self.elm.protocol}"):
            self.elm.send(c, timeout=2)

    def _filter_any(self, *ids: int):
        """Receive several 11-bit ids at once (ATCF/ATCM)."""
        mask = 0x7FF
        for i in ids[1:]:
            mask &= ~(ids[0] ^ i) & 0x7FF
        self._at(f"ATCF{ids[0] & mask:03X}")
        self._at(f"ATCM{mask:03X}")

    def _reset_mode(self):
        self._at("ATCAF1")
        self._at("ATCEA")  # extended addressing off (ignored by adapters without it)

    # ---------------------------------------------------------------- standard UDS
    def _target(self, req: int, resp: int | None):
        self._reset_mode()
        if self.ext:
            self._at("ATCP18")
            self._at(f"ATSHDA{req:02X}F1")
            if resp is not None:
                self._at(f"ATCRA18DAF1{req:02X}")
            self._at(f"ATFCSH18DA{req:02X}F1")
        else:
            self._at(f"ATSH{req:03X}")
            if resp is not None:
                self._at(f"ATCRA{resp:03X}")
            self._at(f"ATFCSH{req:03X}")  # flow control goes back to the module's request id
        if not (self._at("ATFCSD300000") and self._at("ATFCSM1")):
            self.fc_ok = False

    def _request(self, cmd: str, resp_header: str | None, timeout_ms: int = 600, strip_first=False) -> list[bytes]:
        self._timeout(timeout_ms)
        replies = self.elm.query(cmd, timeout=max(3, timeout_ms / 1000 + 2), strip_first=strip_first)

        def pick(r):
            return [m for h, lst in r.items() if resp_header is None or h.upper() == resp_header.upper() for m in lst]

        msgs = pick(replies)
        # 7F xx 78 = "response pending": the module needs more time, ask again with a long timeout
        if msgs and all(m[:1] == b"\x7f" and m[2:3] == b"\x78" for m in msgs):
            self._at("ATSTFF")
            msgs = pick(self.elm.query(cmd, timeout=6, strip_first=strip_first))
        return [m for m in msgs if not (m[:1] == b"\x7f" and m[2:3] == b"\x78")]

    def _resp_header(self, req: int, resp: int) -> str:
        return f"18DAF1{req:02X}" if self.ext else f"{resp:03X}"

    def read_module(self, req: int, resp: int, guess: str | None = None, family: str | None = None) -> dict:
        hdr = self._resp_header(req, resp)
        self._target(req, resp)
        mod = self._new(scheme="uds", req=f"18DA{req:02X}F1" if self.ext else f"{req:03X}", resp=hdr,
                        guess=guess, family=family)
        for m in self._request("22F197", hdr, 300):  # system name
            mod["responded"] = True
            if m[:3] == b"\x62\xf1\x97":
                mod["name"] = "".join(chr(b) for b in m[3:] if 32 <= b < 127).strip() or None
        msgs = self._request("1902FF", hdr, 600)
        if msgs:
            mod["responded"] = True
        pos = [m for m in msgs if m[:2] == b"\x59\x02"]
        if pos:
            mod["protocol"] = "UDS"
            for m in pos:
                mod["dtcs"] += parse_uds_dtcs(m)
        else:
            kmsgs = self._request("1800FF00", hdr, 600)  # KWP2000 fallback
            if kmsgs:
                mod["responded"] = True
            kpos = [m for m in kmsgs if m[:1] == b"\x58"]
            if kpos:
                mod["protocol"] = "KWP2000"
                for m in kpos:
                    mod["dtcs"] += parse_kwp_dtcs(m)
            elif mod["responded"]:
                mod["error"] = "Module answered but would not report codes (brand-specific protocol)"
        if not self.fc_ok and not self.ext and resp != req + 8 and not mod["dtcs"]:
            mod["error"] = ((mod["error"] + ". ") if mod["error"] else "") + (
                "Your adapter doesn't support the flow-control commands this module needs - "
                "a genuine ELM327 or a better clone (e.g. OBDLink, vLinker) may read it")
        return self._finish(mod)

    # ---------------------------------------------------------------- GM (GMLAN)
    def read_gm_module(self, req: int, resp: int, name: str, guess: str) -> dict:
        uudt = 0x500 | (resp & 0xFF)
        mod = self._new(scheme="gm", req=f"{req:03X}", resp=f"{resp:03X}", guess=guess, family="GM / Holden (GMLAN)",
                        name=name)
        self._reset_mode()
        self._at(f"ATSH{req:03X}")
        self._filter_any(resp, uudt)
        self._at("ATCAF0")  # GM's code frames aren't ISO-TP formatted, so send/receive raw frames
        self._timeout(500)
        frames = self.elm.query_frames("03A981FFAAAAAAAA", timeout=4)
        self._at("ATCAF1")
        codes = [d for h, d in frames if h.upper() == f"{uudt:03X}"]
        replies = [d for h, d in frames if h.upper() == f"{resp:03X}"]
        if codes or replies:
            mod["responded"] = True
            mod["protocol"] = "GMLAN"
        mod["dtcs"] = parse_gm_uudt(codes)
        if replies and not codes and any(len(d) > 2 and d[1] == 0x7F for d in replies):
            mod["error"] = "Module refused the GM trouble-code request"
        return self._finish(mod)

    # ---------------------------------------------------------------- BMW (extended addressing)
    def _target_bmw(self, addr: int, fc_with_addr: bool = False):
        self._at("ATCAF1")
        self._at("ATSH6F1")
        if not self._at(f"ATCEA{addr:02X}"):
            self.cea_ok = False
        self._at(f"ATCRA{0x600 + addr:03X}")
        self._at("ATFCSH6F1")
        self._at(f"ATFCSD{addr:02X}300000" if fc_with_addr else "ATFCSD300000")
        self._at("ATFCSM1")

    def _bmw_request(self, cmd: str, addr: int, timeout_ms: int = 600) -> list[bytes]:
        """Send to a BMW module; retries with the other flow-control layout if a long reply gets cut off."""
        hdr = f"{0x600 + addr:03X}"
        frames: list[bytes] = []
        for fc_with_addr in (False, True):
            self._target_bmw(addr, fc_with_addr)
            self._timeout(timeout_ms)
            frames = [d[1:] for h, d in self.elm.query_frames(cmd, timeout=max(3, timeout_ms / 1000 + 2))
                      if h.upper() == hdr and d[:1] == b"\xf1"]
            first = any(f and f[0] >> 4 == 1 for f in frames)
            cont = any(f and f[0] >> 4 == 2 for f in frames)
            if not (first and not cont):  # complete (or single-frame) reply
                break
        return [m for m in _isotp_reassemble(frames) if not (m[:1] == b"\x7f" and m[2:3] == b"\x78")]

    def read_bmw_module(self, addr: int, name: str, guess: str) -> dict:
        mod = self._new(scheme="bmw", req=f"BMW {addr:02X}", resp=f"{0x600 + addr:03X}", guess=guess,
                        family="BMW / MINI", name=name)
        msgs = self._bmw_request("1902FF", addr)
        if not self.cea_ok:
            mod["error"] = ("Your adapter doesn't support CAN extended addressing (ATCEA), which BMW modules need. "
                            "A genuine ELM327 v1.4+ or an OBDLink adapter can read them")
            return self._finish(mod)
        if msgs:
            mod["responded"] = True
        pos = [m for m in msgs if m[:2] == b"\x59\x02"]
        if pos:
            mod["protocol"] = "UDS"
            for m in pos:
                mod["dtcs"] += parse_uds_dtcs(m, bmw=True)
        else:
            kmsgs = self._bmw_request("1802FFFF", addr)  # E-series KWP2000
            if kmsgs:
                mod["responded"] = True
            kpos = [m for m in kmsgs if m[:1] == b"\x58"]
            if kpos:
                mod["protocol"] = "KWP2000"
                for m in kpos:
                    mod["dtcs"] += parse_kwp_dtcs(m, bmw=True)
            elif mod["responded"]:
                mod["error"] = "Module answered but would not report codes"
        return self._finish(mod)

    # ---------------------------------------------------------------- module records
    @staticmethod
    def _new(**kw) -> dict:
        mod = {"scheme": "uds", "req": "", "resp": "", "family": None, "guess": None, "name": None,
               "responded": False, "protocol": None, "dtcs": [], "error": None}
        mod.update(kw)
        return mod

    @staticmethod
    def _finish(mod: dict) -> dict:
        codes = [d["code"].split("-")[0] for d in mod["dtcs"] if not d["code"].startswith("BMW-")]
        mod["system"] = classify(mod["name"], codes, mod["guess"])
        name = f" “{mod['name']}”" if mod["name"] else ""
        system = {"ABS": "ABS / brakes", "Airbag": "Airbag (SRS)", "Transmission": "Transmission (TCM)",
                  "Other": "Module"}[mod["system"]]
        mod["label"] = f"{system}{name} @ {mod['req']}"
        return mod

    # ---------------------------------------------------------------- clearing
    def clear_module(self, mod: dict) -> bool:
        if mod.get("scheme") == "gm":
            req, resp = int(mod["req"], 16), int(mod["resp"], 16)
            self._target(req, resp)
            return any(m[:1] == b"\x44" for m in self._request("04", f"{resp:03X}", 1000))
        if mod.get("scheme") == "bmw":
            addr = int(mod["req"].split()[-1], 16)
            ok = any(m[:1] == b"\x54" for m in self._bmw_request("14FFFFFF", addr, 1000))
            return ok or any(m[:1] == b"\x54" for m in self._bmw_request("14FFFF", addr, 1000))
        req, resp = module_ids(mod)
        hdr = self._resp_header(req, resp)
        self._target(req, resp)
        ok = any(m[:1] == b"\x54" for m in self._request("14FFFFFF", hdr, 1000))
        return ok or any(m[:1] == b"\x54" for m in self._request("14FF00", hdr, 1000))

    def clear_modules(self, mods: list[dict]) -> dict[str, bool]:
        out = {}
        try:
            for m in mods:
                self.progress(f"Clearing {m['label']}…")
                out[m["label"]] = self.clear_module(m)
        finally:
            self.restore()
        return out

    # ---------------------------------------------------------------- discovery (standard UDS)
    def discover(self) -> list[tuple[int, int]]:
        """Probe every physical diagnostic address with TesterPresent; return (req, resp) pairs."""
        found = []
        self._reset_mode()
        if self.ext:
            self._at("ATCP18")
            self._at("ATCF18DAF100")
            self._at("ATCM1FFFFF00")
            candidates = [t for t in range(0x00, 0x100) if t not in (0xF1, 0x33)]
        else:
            self._at("ATCF700")
            self._at("ATCM700")
            candidates = [r for r in range(0x700, 0x7F8) if not 0x7DF <= r <= 0x7EF]
        self._at("ATST10")
        for i, req in enumerate(candidates):
            if i % 16 == 0:
                self.progress(f"Searching for modules… {int(100 * i / len(candidates))}%")
            self._at(f"ATSHDA{req:02X}F1" if self.ext else f"ATSH{req:03X}")
            for header, msgs in self.elm.query("3E00", timeout=1).items():
                if any(m[:1] in (b"\x7e", b"\x7f") for m in msgs):
                    if self.ext:
                        found.append((int(header[-2:], 16), int(header[-2:], 16)))
                    elif int(header, 16) != req:
                        found.append((req, int(header, 16)))
        return sorted(set(found))

    # ---------------------------------------------------------------- whole job
    @staticmethod
    def _plan(family: str | None) -> list[tuple]:
        """Modules to ask. The VIN's brand first; every brand's addresses if it's unknown."""
        uds = [("uds", r, s, g, name) for g, r, s, k, name in KNOWN_MODULES if family in (None, k) or k == "ALL"]
        gm = [("gm", r, resp, n, g) for g, r, resp, n in GM_MODULES]
        bmw = [("bmw", a, n, g) for g, a, n in BMW_MODULES]
        if family == "GM":
            return gm
        if family == "BMW":
            return bmw
        if family:
            return uds
        return uds + gm + bmw

    def _run(self, item) -> dict:
        kind = item[0]
        if kind == "uds":
            _, req, resp, guess, fam = item
            self.progress(f"Checking {guess} at {req:03X} ({fam})…")
            return self.read_module(req, resp, guess, fam)
        if kind == "gm":
            _, req, resp, name, guess = item
            self.progress(f"Checking GM {name} at {req:03X}…")
            return self.read_gm_module(req, resp, name, guess)
        _, addr, name, guess = item
        self.progress(f"Checking BMW {name} (address {addr:02X})…")
        return self.read_bmw_module(addr, name, guess)

    def scan(self, discover: bool = False, vin: str | None = None) -> dict:
        if not self.elm.is_can:
            return {"supported": False, "note": UNSUPPORTED_NOTE, "list": []}
        family = brand_family(vin)
        result = {"supported": True, "note": BRAND_NOTE, "list": [], "searched": discover,
                  "adapter_limited": False, "brand": family}
        seen = set()

        def add(mod):
            key = (mod["resp"], mod["name"])
            if (mod["responded"] or mod["error"]) and key not in seen:
                seen.add(key)
                result["list"].append(mod)

        try:
            if self.ext and not discover:
                # 29-bit cars: common ISO 15765 physical addresses for ABS (0x28) and airbag (0x58)
                for t, g in ((0x28, "ABS"), (0x58, "Airbag")):
                    add(self.read_module(t, t, g, "ISO 15765 29-bit"))
            else:
                plan = self._plan(family)
                for item in plan:
                    add(self._run(item))
                if family and not result["list"]:
                    # Nothing answered at the brand's addresses: try the other brands' addresses too
                    for item in self._plan(None):
                        if item not in plan:
                            add(self._run(item))
                if discover:
                    known = {(k[1], k[2]): (k[0], k[4]) for k in KNOWN_MODULES}
                    for r, s in self.discover():
                        guess, fam = known.get((r, s), (None, None))
                        add(self.read_module(r, s, guess, fam))
        finally:
            self.restore()
        if not self.fc_ok or not self.cea_ok:
            result["adapter_limited"] = True
            result["note"] += (" Note: your adapter rejected some commands needed for these modules (common with "
                               "cheap ELM327 clones), so some modules or codes may be missed.")
        return result


def merge_into_dtcs(scan: dict) -> None:
    """Add module codes to scan['dtcs'] so they show up in reports, diagnosis and the AI summary."""
    mods = (scan.get("modules") or {}).get("list", [])
    for kind in ("stored", "pending"):
        for key in [k for k in scan["dtcs"][kind] if k.startswith(("ABS / brakes", "Airbag (SRS)", "Module ", "Transmission (TCM) @"))]:
            del scan["dtcs"][kind][key]
    for m in mods:
        stored = [d["code"] for d in m["dtcs"] if d["confirmed"] or (d["active"] and not d["pending"])]
        pending = [d["code"] for d in m["dtcs"] if d["pending"] and d["code"] not in stored]
        if stored:
            scan["dtcs"]["stored"][m["label"]] = stored
        if pending:
            scan["dtcs"]["pending"][m["label"]] = pending


def module_ids(mod: dict) -> tuple[int, int]:
    """(request id, response id) of a standard-UDS module from a scan result."""
    m = re.fullmatch(r"18DA([0-9A-F]{2})F1", mod["req"].upper())
    if m:
        t = int(m.group(1), 16)
        return t, t
    return int(mod["req"], 16), int(mod["resp"], 16)
