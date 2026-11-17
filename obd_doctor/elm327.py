"""Low-level communication with ELM327-compatible OBD-II adapters.

Supports:
  * USB / Bluetooth adapters that appear as a serial port (COM3, /dev/ttyUSB0, /dev/rfcomm0)
  * Wi-Fi adapters that expose a TCP socket (usually 192.168.0.10:35000)

The ELM327 is configured with headers ON so we can tell which control module
(engine, transmission, ...) sent each reply, and multi-frame CAN (ISO-TP)
messages are re-assembled here.
"""

from __future__ import annotations

import re
import socket
import time
from collections import defaultdict, deque

# Always-on record of recent adapter traffic, for support files (kept in memory only).
# Module-level so it survives failed connection attempts.
TRAFFIC: deque = deque(maxlen=4000)


def _record(direction: str, text: str) -> None:
    TRAFFIC.append(f"{time.strftime('%H:%M:%S')} {direction} {text!r}")

ERROR_STRINGS = (
    "NO DATA", "UNABLE TO CONNECT", "CAN ERROR", "BUS ERROR", "BUS INIT",
    "DATA ERROR", "BUFFER FULL", "STOPPED", "FB ERROR", "RX ERROR",
    "LV RESET", "ACT ALERT", "ERR",
)

PROTOCOLS = {
    "0": "Automatic",
    "1": "SAE J1850 PWM (41.6 kbaud)",
    "2": "SAE J1850 VPW (10.4 kbaud)",
    "3": "ISO 9141-2 (5 baud init)",
    "4": "ISO 14230-4 KWP (5 baud init)",
    "5": "ISO 14230-4 KWP (fast init)",
    "6": "ISO 15765-4 CAN (11 bit ID, 500 kbaud)",
    "7": "ISO 15765-4 CAN (29 bit ID, 500 kbaud)",
    "8": "ISO 15765-4 CAN (11 bit ID, 250 kbaud)",
    "9": "ISO 15765-4 CAN (29 bit ID, 250 kbaud)",
}

# Source addresses -> human names
ECU_NAMES = {
    "7E8": "Engine (ECM/PCM)", "7E9": "Transmission (TCM)", "7EA": "Module 7EA",
    "7EB": "Module 7EB", "7EC": "Hybrid / Module 7EC", "7ED": "Module 7ED",
    "7EE": "Module 7EE", "7EF": "Module 7EF",
    "10": "Engine (ECM/PCM)", "18": "Transmission (TCM)", "28": "ABS / Brakes",
}


class OBDError(Exception):
    pass


# --------------------------------------------------------------------------- transports
class _Transport:
    def write(self, data: bytes) -> None:
        raise NotImplementedError

    def read_chunk(self) -> bytes:
        raise NotImplementedError

    def close(self) -> None:
        pass

    def drain(self) -> None:
        for _ in range(20):
            if not self.read_chunk():
                return

    def read_until_prompt(self, timeout: float) -> str:
        buf = b""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            chunk = self.read_chunk()
            if chunk:
                buf += chunk
                if b">" in chunk:
                    break
        return buf.decode("ascii", errors="ignore")


class SerialTransport(_Transport):
    def __init__(self, port: str, baudrate: int = 38400):
        try:
            import serial  # pyserial
        except ImportError as exc:  # pragma: no cover
            raise OBDError("pyserial is required for USB/Bluetooth adapters: pip install pyserial") from exc
        self.ser = serial.Serial(port, baudrate, timeout=0.05)

    def write(self, data: bytes) -> None:
        self.ser.write(data)
        self.ser.flush()

    def read_chunk(self) -> bytes:
        return self.ser.read(self.ser.in_waiting or 1)

    def close(self) -> None:
        self.ser.close()


class TcpTransport(_Transport):
    def __init__(self, host: str, port: int):
        self.sock = socket.create_connection((host, port), timeout=5)
        self.sock.settimeout(0.05)

    def write(self, data: bytes) -> None:
        self.sock.sendall(data)

    def read_chunk(self) -> bytes:
        try:
            data = self.sock.recv(4096)
            if data == b"":
                raise OBDError("Adapter closed the connection")
            return data
        except socket.timeout:
            return b""

    def close(self) -> None:
        self.sock.close()


def open_transport(target: str, baudrate: int = 38400) -> _Transport:
    """target: 'tcp://host:port', 'host:port', or a serial port name."""
    m = re.match(r"^(?:tcp://)?([\w.\-]+):(\d+)$", target)
    if m and not target.upper().startswith("COM"):
        return TcpTransport(m.group(1), int(m.group(2)))
    return SerialTransport(target, baudrate)


def find_serial_adapter(bauds=(38400, 9600, 115200, 57600)) -> tuple[str, int]:
    """Probe serial ports for something that answers like an ELM327."""
    try:
        from serial.tools import list_ports
    except ImportError as exc:
        raise OBDError("pyserial is required for auto-detection: pip install pyserial") from exc
    ports = [p.device for p in list_ports.comports()]
    ports += [p for p in ("/dev/rfcomm0", "/dev/rfcomm1") if p not in ports]
    for port in ports:
        for baud in bauds:
            try:
                t = SerialTransport(port, baud)
            except Exception:
                continue
            try:
                t.write(b"\rATI\r")
                if "ELM" in t.read_until_prompt(2).upper():
                    return port, baud
            except Exception:
                pass
            finally:
                t.close()
    raise OBDError("No ELM327 adapter found. Plug it in / pair it, or pass --port explicitly.")


# --------------------------------------------------------------------------- ELM327 driver
class ELM327:
    def __init__(self, transport: _Transport, protocol: str = "0", debug: bool = False):
        self.t = transport
        self.requested_protocol = protocol
        self.debug = debug
        self.protocol = None
        self.version = ""
        self.voltage = None
        self.log = print  # where --debug traffic goes (the GUI redirects it)

    # ---- raw command
    def send(self, cmd: str, timeout: float = 5.0) -> list[str]:
        self.t.drain()
        self.t.write((cmd + "\r").encode("ascii"))
        _record(">>", cmd)
        raw = self.t.read_until_prompt(timeout)
        _record("<<", raw if raw else "(no reply - timed out)")
        if self.debug:
            self.log(f"  >> {cmd!r}\n  << {raw!r}")
        norm_cmd = cmd.replace(" ", "").upper()
        lines = []
        for ln in re.split(r"[\r\n]+", raw.replace(">", "")):
            ln = ln.strip()
            if not ln or ln.replace(" ", "").upper() == norm_cmd:
                continue  # blank or echo
            if ln.upper().startswith("SEARCHING"):
                continue
            lines.append(ln)
        return lines

    @staticmethod
    def _error_in(lines: list[str]) -> str | None:
        for ln in lines:
            up = ln.upper()
            for e in ERROR_STRINGS:
                if e in up:
                    return ln
            if up == "?":
                return "Command not understood (?)"
        return None

    # ---- setup
    def initialize(self) -> None:
        reply = self.send("ATZ", timeout=5)
        self.version = next((l for l in reply if "ELM" in l.upper()), " ".join(reply))
        for c in ("ATE0", "ATL0", "ATS1", "ATH1", f"ATSP{self.requested_protocol}"):
            self.send(c, timeout=3)
        probe = self.send("0100", timeout=20)  # triggers protocol search
        err = self._error_in(probe)
        if err and "NO DATA" not in err.upper():
            raise OBDError(
                f"Adapter could not talk to the car: {err}\n"
                "  - Is the ignition ON (or the engine running)?\n"
                "  - Is the adapter fully seated in the OBD port (under the dashboard)?\n"
                "  - Try forcing a protocol with --protocol 6 (most cars 2008+)."
            )
        dp = self.send("ATDPN", timeout=3)
        self.protocol = (dp[0].lstrip("A") if dp else "6")[:1].upper()
        try:
            self.voltage = float(self.send("ATRV", timeout=3)[0].rstrip("Vv"))
        except (IndexError, ValueError):
            self.voltage = None

    @property
    def is_can(self) -> bool:
        return self.protocol in ("6", "7", "8", "9")

    @property
    def protocol_name(self) -> str:
        return PROTOCOLS.get(self.protocol or "", f"Protocol {self.protocol}")

    # ---- OBD request / response
    def query(self, cmd: str, timeout: float = 5.0, strip_first: bool = False) -> dict[str, list[bytes]]:
        """Send an OBD request, return {ecu_address: [message_bytes, ...]}.
        strip_first drops the first data byte of every frame (CAN extended addressing, e.g. BMW)."""
        lines = self.send(cmd, timeout)
        if self._error_in(lines):
            return {}
        return self._parse_can(lines, strip_first) if self.is_can else self._parse_legacy(lines)

    def query_frames(self, cmd: str, timeout: float = 5.0) -> list[tuple[str, bytes]]:
        """Send a request and return the raw CAN frames [(header, data), ...] without ISO-TP reassembly."""
        lines = self.send(cmd, timeout)
        if self._error_in(lines):
            return []
        return self._can_frames(lines)

    def _can_frames(self, lines: list[str]) -> list[tuple[str, bytes]]:
        out = []
        ext = self.protocol in ("7", "9")
        for ln in lines:
            toks = ln.split()
            if len(toks) == 1 and len(toks[0]) > 3:  # adapter ignored ATS1: split manually
                s = toks[0]
                hlen = 8 if ext else 3
                toks = [s[:hlen]] + [s[i:i + 2] for i in range(hlen, len(s), 2)]
                if ext:
                    toks = [s[0:2], s[2:4], s[4:6], s[6:8]] + toks[1:]
            if not toks or not all(re.fullmatch(r"[0-9A-Fa-f]+", t) for t in toks):
                continue
            if ext:
                if len(toks) < 5:
                    continue
                header, data = "".join(toks[:4]).upper(), toks[4:]
            else:
                header, data = toks[0].upper(), toks[1:]
            out.append((header, bytes(int(t, 16) for t in data)))
        return out

    def _parse_can(self, lines: list[str], strip_first: bool = False) -> dict[str, list[bytes]]:
        frames: dict[str, list[bytes]] = defaultdict(list)
        for header, data in self._can_frames(lines):
            frames[header].append(data[1:] if strip_first else data)
        return {h: _isotp_reassemble(f) for h, f in frames.items() if f}

    def _parse_legacy(self, lines: list[str]) -> dict[str, list[bytes]]:
        out: dict[str, list[bytes]] = defaultdict(list)
        for ln in lines:
            toks = ln.split()
            if len(toks) < 5 or not all(re.fullmatch(r"[0-9A-Fa-f]{2}", t) for t in toks):
                continue
            # [priority, target, source, data..., checksum]
            out[toks[2].upper()].append(bytes(int(t, 16) for t in toks[3:-1]))
        return dict(out)

    def close(self) -> None:
        try:
            self.send("ATPC", timeout=1)
        except Exception:
            pass
        self.t.close()


def _isotp_reassemble(frames: list[bytes]) -> list[bytes]:
    msgs, buf, expected = [], None, 0
    for f in frames:
        if not f:
            continue
        kind = f[0] >> 4
        if kind == 0:  # single frame
            msgs.append(f[1:1 + (f[0] & 0x0F)])
        elif kind == 1:  # first frame
            expected = ((f[0] & 0x0F) << 8) | f[1]
            buf = bytearray(f[2:])
        elif kind == 2 and buf is not None:  # consecutive frame
            buf += f[1:]
            if len(buf) >= expected:
                msgs.append(bytes(buf[:expected]))
                buf = None
    if buf:
        msgs.append(bytes(buf))
    return msgs


def ecu_name(addr: str) -> str:
    if addr in ECU_NAMES:
        return ECU_NAMES[addr]
    if len(addr) == 8:  # 29-bit: source address is the last byte
        return ECU_NAMES.get(addr[-2:], f"Module {addr}")
    return f"Module {addr}"


def ecu_sort_key(addr: str) -> tuple:
    """Engine first, then transmission, then everything else."""
    src = addr[-2:] if len(addr) == 8 else addr
    order = {"7E8": 0, "10": 0, "7E9": 1, "18": 1}
    return (order.get(src, 5), addr)
