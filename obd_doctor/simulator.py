"""A fake car + ELM327 adapter over TCP, for testing without a vehicle.

Emulates a 2015-ish gasoline car on CAN 11-bit / 500k with an engine ECU (7E8)
and transmission ECU (7E9). The engine has a vacuum-leak story: both banks lean
(P0171/P0174), a pending random misfire (P0300), high positive fuel trims.
"""

from __future__ import annotations

import random
import socket
import threading
import time

from .decoder import encode_dtc

# Demo vehicles: "ford" (standard UDS modules), "gm" (GMLAN modules), "bmw" (extended addressing)
PROFILES = {"ford": "1FAHP3F20CL148530", "gm": "1G1ZD5ST5JF100001", "bmw": "WBA8E9G50GNT00001",
            "holden": "6G1FA8E53EL000001"}  # holden = VF Commodore (GMLAN, like GM)


class FakeCar:
    def __init__(self, profile: str = "ford"):
        self.profile = profile if profile in PROFILES else "ford"
        self.vin = PROFILES[self.profile]
        self.start = time.monotonic()
        self.reset_codes(initial=True)
        # ABS and airbag modules (Ford-family addresses), answering UDS requests.
        # dtc = (code, failure type byte, UDS status byte); 0x89 = failing now + confirmed + warning light
        self.modules = {
            0x760: {"resp": 0x768, "name": "ABS", "dtcs": [("C0035", 0x1C, 0x89)]},
            0x737: {"resp": 0x73F, "name": "RCM", "dtcs": [("B0001", 0x13, 0x89)]},
            0x7E1: {"resp": 0x7E9, "name": "TCM", "dtcs": [("P0733", 0x00, 0x09)]},  # gear 3 incorrect ratio
        } if self.profile == "ford" else {}
        # GM GMLAN modules: request id -> codes (code, failure type, status)
        self.gm_modules = {
            0x243: {"resp": 0x643, "dtcs": [("C0040", 0x1C, 0x08)]},   # EBCM: right front wheel speed sensor
            0x247: {"resp": 0x647, "dtcs": [("B0001", 0x13, 0x08)]},   # SDM: driver airbag circuit open
            0x7E2: {"resp": 0x7EA, "dtcs": [("P0751", 0x00, 0x08)]},   # TCM: shift solenoid A stuck off
        } if self.profile == "gm" else {
            0x243: {"resp": 0x643, "dtcs": [("C0035", 0x1C, 0x08)]},   # EBCM: left front wheel speed sensor
            0x247: {"resp": 0x647, "dtcs": [("B0001", 0x13, 0x08)]},   # SDM: driver airbag circuit open
            0x7E2: {"resp": 0x7EA, "dtcs": [("P0751", 0x00, 0x08)]},   # TCM: shift solenoid A stuck off
        } if self.profile == "holden" else {}
        # BMW modules by extended address: codes as (hi, mid, low/FTB, status)
        self.bmw_modules = {
            0x29: {"dtcs": [(0x48, 0x0A, 0x20, 0x09)]},                            # DSC
            0x01: {"dtcs": [(0x93, 0x0B, 0x20, 0x09), (0x93, 0x0C, 0x20, 0x08)]},  # ACSM (2 codes: multi-frame)
            0x18: {"dtcs": [(0x50, 0x0C, 0x20, 0x09)]},                            # EGS (transmission)
        } if self.profile == "bmw" else {}

    def handle_gm(self, req_id: int, req: bytes):
        """GMLAN. Returns ('raw', [(id, frame), ...]) or ('isotp', id, payload) or None."""
        mod = self.gm_modules.get(req_id)
        if not mod or not req:
            return None
        resp, uudt = mod["resp"], 0x500 | (mod["resp"] & 0xFF)
        if req[0] == 0xA9 and req[1:2] == b"\x81":
            frames = [(uudt, bytes([0x81]) + encode_dtc(c) + bytes([ftb, st, 0, 0])) for c, ftb, st in mod["dtcs"]]
            frames.append((uudt, bytes([0x81, 0, 0, 0, 0, 0, 0, 0])))  # end of list
            return "raw", frames
        if req[0] == 0x04:
            mod["dtcs"] = []
            return "isotp", resp, bytes([0x44])
        return "isotp", resp, bytes([0x7F, req[0], 0x11])

    def handle_bmw(self, addr: int, req: bytes):
        mod = self.bmw_modules.get(addr)
        if not mod or not req:
            return None
        if req[0] == 0x19 and req[1:2] == b"\x02":
            return bytes([0x59, 0x02, 0xFF]) + b"".join(bytes(d) for d in mod["dtcs"])
        if req[0] == 0x14:
            mod["dtcs"] = []
            return bytes([0x54])
        return bytes([0x7F, req[0], 0x11])

    def handle_uds(self, req_id: int, req: bytes):
        """Return (response id, payload) for a request sent to a module, or None if nothing is there."""
        mod = self.modules.get(req_id)
        if not mod or not req:
            return None
        sid = req[0]
        if sid == 0x3E:
            p = bytes([0x7E, 0x00])
        elif sid == 0x22 and req[1:3] == b"\xf1\x97":
            p = b"\x62\xf1\x97" + mod["name"].encode()
        elif sid == 0x19 and req[1:2] == b"\x02":
            p = bytes([0x59, 0x02, 0xFF]) + b"".join(encode_dtc(c) + bytes([ftb, st]) for c, ftb, st in mod["dtcs"])
        elif sid == 0x14:
            mod["dtcs"] = []
            p = bytes([0x54])
        else:
            p = bytes([0x7F, sid, 0x11])  # service not supported
        return mod["resp"], p

    def reset_codes(self, initial=False):
        if initial:
            self.stored = {"7E8": ["P0171", "P0174"], "7E9": []}
            self.pending = {"7E8": ["P0300"], "7E9": []}
            self.permanent = {"7E8": ["P0171"], "7E9": []}
            self.freeze = {"dtc": "P0171", 0x03: [2, 0], 0x04: [70], 0x05: [128], 0x06: [140],
                           0x07: [152], 0x08: [138], 0x09: [150], 0x0B: [33], 0x0C: [0x0C, 0x30],
                           0x0D: [0], 0x0E: [140], 0x0F: [70], 0x10: [0x01, 0x90], 0x11: [33]}
        else:
            self.stored = {"7E8": [], "7E9": []}
            self.pending = {"7E8": [], "7E9": []}
            self.freeze = {}
            self.start = time.monotonic()  # monitors reset

    @property
    def mil(self):
        return bool(self.stored["7E8"])

    def live(self, pid):
        j = lambda a, b: random.randint(a, b)
        rpm = 760 + j(-25, 25)
        runtime = int(time.monotonic() - self.start) + 1260
        table = {
            0x01: [(0x80 if self.mil else 0) | len(self.stored["7E8"]), 0x07, 0x65, 0x00 if self.stored["7E8"] else 0x21],
            0x03: [2, 0], 0x04: [j(55, 62)], 0x05: [128 + j(0, 2)],
            0x06: [128 + j(4, 10)], 0x07: [151 + j(0, 2)], 0x08: [128 + j(3, 9)], 0x09: [149 + j(0, 2)],
            0x0B: [j(32, 35)], 0x0C: [(rpm * 4) >> 8, (rpm * 4) & 0xFF], 0x0D: [0], 0x0E: [128 + j(16, 24)],
            0x0F: [72], 0x10: [0x01, j(0x70, 0x9A)], 0x11: [j(32, 34)],
            0x14: [j(10, 180), 0xFF], 0x15: [j(120, 140), 0xFF], 0x1F: [runtime >> 8, runtime & 0xFF],
            0x21: [0, 87], 0x2F: [j(150, 152)], 0x30: [12], 0x31: [0x02, 0x1C], 0x33: [101],
            0x42: [0x37, 0x50 + j(0, 40)], 0x46: [62], 0x5C: [131],
            0x1C: [6], 0x4D: [0, 95], 0x4E: [0x03, 0x20], 0x51: [1], 0x5E: [0, 22 + j(0, 4)],
            0xA6: list((1482513).to_bytes(4, "big")),  # odometer 148,251.3 km
        }
        return table.get(pid)

    def supported(self, base, ecu):
        if ecu == "7E9":
            pids = {0x01, 0x05, 0x0C, 0x0D} if base == 0 else set()
        else:
            pids = set(self.live_pids()) | {0x20, 0x40, 0x60, 0x80, 0xA0}
        bits = 0
        for p in pids:
            if base < p <= base + 32:
                bits |= 1 << (32 - (p - base))
        return list(bits.to_bytes(4, "big")) if (bits or base == 0) else None

    @staticmethod
    def live_pids():
        return [0x01, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F,
                0x10, 0x11, 0x14, 0x15, 0x1F, 0x21, 0x2F, 0x30, 0x31, 0x33, 0x42, 0x46, 0x5C,
                0x1C, 0x4D, 0x4E, 0x51, 0x5E, 0xA6]

    # Monitor test results (service $06): MID -> [(TID, UAS, value, min, max)]
    MODE06 = {
        0x01: [(0x01, 0x0A, 3690, 3690, 3690), (0x05, 0x10, 62, 0, 150), (0x06, 0x10, 71, 0, 150)],  # O2 B1S1
        0x21: [(0x80, 0x01, 141, 0, 420)],          # catalyst bank 1: passing
        0x22: [(0x80, 0x01, 133, 0, 420)],          # catalyst bank 2: passing
        0x3C: [(0x84, 0x01, 812, 0, 640)],          # EVAP 0.020" leak test: FAILING (no code yet)
        0xA1: [(0x0B, 0x24, 8, 0, 65535), (0x0C, 0x24, 27, 0, 65535)],
        0xA2: [(0x0B, 0x24, 0, 0, 65535), (0x0C, 0x24, 0, 0, 65535)],
        0xA3: [(0x0B, 0x24, 6, 0, 65535), (0x0C, 0x24, 19, 0, 65535)],    # cylinder 2 misfiring most
        0xA4: [(0x0B, 0x24, 2, 0, 65535), (0x0C, 0x24, 8, 0, 65535)],
        0xA5: [(0x0B, 0x24, 0, 0, 65535), (0x0C, 0x24, 0, 0, 65535)],
    }

    def monitor_test(self, mid: int):
        if mid % 0x20 == 0:  # supported-MID bitmap
            bits = 0
            for m in self.MODE06:
                if mid < m <= mid + 32:
                    bits |= 1 << (32 - (m - mid))
            if mid + 0x20 <= 0xA0:
                bits |= 1  # more MIDs follow
            return bytes([0x46, mid]) + bits.to_bytes(4, "big")
        if mid not in self.MODE06:
            return None
        out = bytes([0x46])
        for tid, uas, v, lo, hi in self.MODE06[mid]:
            out += bytes([mid, tid, uas]) + v.to_bytes(2, "big") + lo.to_bytes(2, "big") + hi.to_bytes(2, "big")
        return out

    def ecu_info(self, ecu: str, pid: int):
        names = {"7E8": "ECM\0-EngineControl", "7E9": "TCM\0-TransmissionCtrl"}
        if pid == 0x0A:
            return bytes([0x49, 0x0A, 0x01]) + names[ecu].encode()[:20].ljust(20, b"\0")
        if pid == 0x04:
            cal = {"7E8": "CM5A-12A650-AXC", "7E9": "CM5P-7J104-AB"}[ecu]
            return bytes([0x49, 0x04, 0x01]) + cal.encode().ljust(16, b"\0")
        return bytes([0x49, 0x06, 0x01]) + ({"7E8": b"\x3a\x7f\x10\xc2", "7E9": b"\x91\x04\xee\x5b"}[ecu])

    def handle(self, req: bytes) -> dict[str, bytes]:
        """Return {ecu: payload} for an OBD request."""
        mode = req[0]
        out = {}
        for ecu in ("7E8", "7E9"):
            p = None
            if mode == 0x01 and len(req) >= 2:
                pid = req[1]
                data = self.supported(pid, ecu) if pid % 0x20 == 0 else (
                    self.live(pid) if ecu == "7E8" else ([0, 7, 0x65, 0] if pid == 1 else None))
                if data is not None:
                    p = bytes([0x41, pid] + data)
            elif mode == 0x02 and len(req) >= 2 and ecu == "7E8":
                pid = req[1]
                if pid == 0x02:
                    dtc = encode_dtc(self.freeze["dtc"]) if self.freeze else b"\x00\x00"
                    p = bytes([0x42, 0x02, 0x00]) + dtc
                elif pid == 0x00:
                    p = bytes([0x42, 0x00, 0x00, 0x7F, 0xB8, 0x00, 0x00])
                elif pid in self.freeze:
                    p = bytes([0x42, pid, 0x00] + self.freeze[pid])
            elif mode in (0x03, 0x07, 0x0A):
                src = {0x03: self.stored, 0x07: self.pending, 0x0A: self.permanent}[mode][ecu]
                p = bytes([mode + 0x40, len(src)]) + b"".join(encode_dtc(c) for c in src)
            elif mode == 0x04:
                self.reset_codes()
                p = bytes([0x44])
            elif mode == 0x09 and len(req) >= 2 and req[1] == 0x02 and ecu == "7E8":
                p = bytes([0x49, 0x02, 0x01]) + self.vin.encode()
            elif mode == 0x09 and len(req) >= 2 and req[1] in (0x04, 0x06, 0x0A):
                p = self.ecu_info(ecu, req[1])
            elif mode == 0x06 and len(req) >= 2 and ecu == "7E8":
                p = self.monitor_test(req[1])
            if p is not None:
                out[ecu] = p
        return out


def _frames(payload: bytes) -> list[bytes]:
    if len(payload) <= 7:
        return [bytes([len(payload)]) + payload]
    frames = [bytes([0x10 | (len(payload) >> 8), len(payload) & 0xFF]) + payload[:6]]
    rest, seq = payload[6:], 1
    while rest:
        frames.append((bytes([0x20 | (seq & 0xF)]) + rest[:7]).ljust(8, b"\x00"))
        rest, seq = rest[7:], seq + 1
    return frames


def _frames_ext(payload: bytes, ext: int = 0xF1) -> list[bytes]:
    """ISO-TP frames with CAN extended addressing (first data byte = address)."""
    if len(payload) <= 6:
        return [bytes([ext, len(payload)]) + payload]
    frames = [bytes([ext, 0x10 | (len(payload) >> 8), len(payload) & 0xFF]) + payload[:5]]
    rest, seq = payload[5:], 1
    while rest:
        frames.append((bytes([ext, 0x20 | (seq & 0xF)]) + rest[:6]).ljust(8, b"\x00"))
        rest, seq = rest[6:], seq + 1
    return frames


class _Session:
    def __init__(self, conn, car):
        self.conn, self.car = conn, car
        self.echo, self.headers, self.spaces = True, False, True
        self.tx = "7DF"  # header set with ATSH
        self.caf, self.cea = True, None

    def reply(self, cmd: str, text: str):
        echo = cmd + "\r" if self.echo else ""
        self.conn.sendall((echo + text + "\r\r>").encode())

    def run(self):
        buf = b""
        while True:
            data = self.conn.recv(1024)
            if not data:
                return
            buf += data
            while b"\r" in buf:
                line, buf = buf.split(b"\r", 1)
                cmd = line.decode(errors="ignore").strip().upper().replace(" ", "")
                if cmd:
                    self.dispatch(cmd)

    def dispatch(self, cmd):
        time.sleep(0.01)
        if cmd.startswith("AT"):
            c = cmd[2:]
            if c in ("Z", "WS", "D"):
                self.echo, self.headers, self.spaces = True, False, True
                self.tx, self.caf, self.cea = "7DF", True, None
                if c == "D":
                    return self.reply(cmd, "OK")
                return self.reply(cmd, "\rELM327 v1.5 (simulator)")
            if c == "I":
                return self.reply(cmd, "ELM327 v1.5 (simulator)")
            if c in ("E0", "E1"):
                self.echo = c == "E1"
            elif c in ("H0", "H1"):
                self.headers = c == "H1"
            elif c in ("S0", "S1"):
                self.spaces = c == "S1"
            elif c.startswith("SH"):
                self.tx = c[2:]
            elif c in ("CAF0", "CAF1"):
                self.caf = c == "CAF1"
            elif c.startswith("CEA"):
                self.cea = int(c[3:], 16) if c[3:] else None
            elif c == "DPN":
                return self.reply(cmd, "A6")
            elif c == "DP":
                return self.reply(cmd, "AUTO, ISO 15765-4 (CAN 11/500)")
            elif c == "RV":
                return self.reply(cmd, f"{14.1 + random.uniform(-0.1, 0.1):.1f}V")
            return self.reply(cmd, "OK")
        try:
            req = bytes.fromhex(cmd)
        except ValueError:
            return self.reply(cmd, "?")
        if not self.caf:  # raw frame: first byte is the ISO-TP length
            req = req[1:1 + req[0]] if req else req
        frames = []  # (header, frame bytes)
        if self.tx == "6F1":
            r = self.car.handle_bmw(self.cea, req) if self.cea is not None else None
            if r:
                frames = [(f"{0x600 + self.cea:03X}", fr) for fr in _frames_ext(r)]
        elif self.tx == "7DF" or (self.tx.startswith("7E") and int(self.tx, 16) not in self.car.modules
                                       and int(self.tx, 16) not in self.car.gm_modules):
            frames = [(ecu, fr) for ecu, p in self.car.handle(req).items() for fr in _frames(p)]
        elif int(self.tx, 16) in self.car.gm_modules:
            r = self.car.handle_gm(int(self.tx, 16), req)
            if r and r[0] == "raw":
                frames = [(f"{i:03X}", fr) for i, fr in r[1]]
            elif r:
                frames = [(f"{r[1]:03X}", fr) for fr in _frames(r[2])]
        else:
            r = self.car.handle_uds(int(self.tx, 16), req)
            if r:
                frames = [(f"{r[0]:03X}", fr) for fr in _frames(r[1])]
        if not frames:
            return self.reply(cmd, "NO DATA")
        sep = " " if self.spaces else ""
        lines = []
        for ecu, fr in frames:
            hexb = sep.join(f"{b:02X}" for b in fr)
            lines.append(f"{ecu}{sep}{hexb}" if self.headers else hexb)
        self.reply(cmd, "\r".join(lines))


def serve(host="127.0.0.1", port=35000, ready: threading.Event | None = None, profile: str = "ford"):
    car = FakeCar(profile)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(4)
    if ready:
        ready.port = srv.getsockname()[1]
        ready.set()
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=lambda c=conn: _safe(_Session(c, car).run), daemon=True).start()


def _safe(fn):
    try:
        fn()
    except OSError:
        pass


def start_background(profile: str = "ford") -> int:
    """Start the simulator in a thread on a free port; returns the port."""
    ready = threading.Event()
    threading.Thread(target=serve, kwargs={"port": 0, "ready": ready, "profile": profile}, daemon=True).start()
    ready.wait(5)
    return ready.port
