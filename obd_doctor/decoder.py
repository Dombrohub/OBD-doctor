"""Decoding of OBD-II (SAE J1979) data: trouble codes and live sensor PIDs."""

from __future__ import annotations


def decode_dtc(b1: int, b2: int) -> str:
    letter = "PCBU"[b1 >> 6]
    return f"{letter}{(b1 >> 4) & 0x3}{b1 & 0xF:X}{b2:02X}"


def encode_dtc(code: str) -> bytes:
    b1 = ("PCBU".index(code[0].upper()) << 6) | (int(code[1]) << 4) | int(code[2], 16)
    return bytes([b1, int(code[3:5], 16)])


def parse_dtc_message(msg: bytes, is_can: bool) -> list[str]:
    """msg starts with the response service byte (0x43 / 0x47 / 0x4A)."""
    data = msg[1:]
    if is_can and data:
        data = data[1:]  # CAN replies carry a 'number of DTCs' byte
    codes = []
    for i in range(0, len(data) - 1, 2):
        if data[i] == 0 and data[i + 1] == 0:
            continue
        codes.append(decode_dtc(data[i], data[i + 1]))
    return codes


# --------------------------------------------------------------------------- PID decoders
def _u16(d):
    return (d[0] << 8) | d[1]


def _trim(d):
    return round((d[0] - 128) * 100 / 128, 1)


SPARK_MONITORS = ["Catalyst", "Heated catalyst", "EVAP system", "Secondary air system",
                  "A/C refrigerant", "O2 sensor", "O2 sensor heater", "EGR/VVT system"]
DIESEL_MONITORS = ["NMHC catalyst", "NOx/SCR aftertreatment", None, "Boost pressure",
                   None, "Exhaust gas sensor", "PM filter", "EGR/VVT system"]


def decode_status(d):
    a, b, c, e = d[:4]
    diesel = bool(b & 0x08)
    monitors = {}
    for name, bit in (("Misfire", 0), ("Fuel system", 1), ("Components", 2)):
        if b & (1 << bit):
            monitors[name] = "incomplete" if b & (1 << (bit + 4)) else "complete"
    for bit, name in enumerate(DIESEL_MONITORS if diesel else SPARK_MONITORS):
        if name and c & (1 << bit):
            monitors[name] = "incomplete" if e & (1 << bit) else "complete"
    return {"mil_on": bool(a & 0x80), "dtc_count": a & 0x7F,
            "engine_type": "diesel" if diesel else "gasoline", "monitors": monitors}


OBD_STANDARDS = {1: "OBD-II (California ARB)", 2: "OBD (US EPA)", 3: "OBD and OBD-II", 4: "OBD-I", 5: "Not OBD compliant",
                 6: "EOBD (Europe)", 7: "EOBD and OBD-II", 8: "EOBD and OBD", 9: "EOBD, OBD and OBD-II", 10: "JOBD (Japan)",
                 11: "JOBD and OBD-II", 12: "JOBD and EOBD", 13: "JOBD, EOBD and OBD-II"}
FUEL_TYPES = {1: "Petrol", 2: "Methanol", 3: "Ethanol", 4: "Diesel", 5: "LPG", 6: "CNG", 7: "Propane", 8: "Electric",
              9: "Bi-fuel petrol", 10: "Bi-fuel methanol", 11: "Bi-fuel ethanol", 12: "Bi-fuel LPG", 13: "Bi-fuel CNG",
              16: "Hybrid petrol", 17: "Hybrid ethanol", 18: "Hybrid diesel", 19: "Hybrid electric"}

FUEL_SYSTEM = {
    1: "Open loop (engine still warming up)",
    2: "Closed loop (normal)",
    4: "Open loop (load / deceleration)",
    8: "Open loop (SYSTEM FAULT)",
    16: "Closed loop (fault in O2 feedback)",
}


def decode_fuel_system(d):
    return FUEL_SYSTEM.get(d[0], f"Unknown ({d[0]})")


# pid: (name, unit, decoder)
PIDS = {
    0x01: ("Monitor status", "", decode_status),
    0x03: ("Fuel system status", "", decode_fuel_system),
    0x04: ("Engine load", "%", lambda d: round(d[0] * 100 / 255, 1)),
    0x05: ("Coolant temperature", "°C", lambda d: d[0] - 40),
    0x06: ("Short term fuel trim B1", "%", _trim),
    0x07: ("Long term fuel trim B1", "%", _trim),
    0x08: ("Short term fuel trim B2", "%", _trim),
    0x09: ("Long term fuel trim B2", "%", _trim),
    0x0A: ("Fuel pressure", "kPa", lambda d: d[0] * 3),
    0x0B: ("Intake manifold pressure", "kPa", lambda d: d[0]),
    0x0C: ("Engine RPM", "rpm", lambda d: round(_u16(d) / 4)),
    0x0D: ("Vehicle speed", "km/h", lambda d: d[0]),
    0x0E: ("Timing advance", "°", lambda d: d[0] / 2 - 64),
    0x0F: ("Intake air temperature", "°C", lambda d: d[0] - 40),
    0x10: ("Mass air flow", "g/s", lambda d: round(_u16(d) / 100, 2)),
    0x11: ("Throttle position", "%", lambda d: round(d[0] * 100 / 255, 1)),
    0x14: ("O2 sensor B1S1 voltage", "V", lambda d: round(d[0] / 200, 3)),
    0x15: ("O2 sensor B1S2 voltage", "V", lambda d: round(d[0] / 200, 3)),
    0x1F: ("Run time since start", "s", _u16),
    0x21: ("Distance with MIL on", "km", _u16),
    0x2F: ("Fuel level", "%", lambda d: round(d[0] * 100 / 255, 1)),
    0x30: ("Warm-ups since codes cleared", "", lambda d: d[0]),
    0x31: ("Distance since codes cleared", "km", _u16),
    0x33: ("Barometric pressure", "kPa", lambda d: d[0]),
    0x42: ("Control module voltage", "V", lambda d: round(_u16(d) / 1000, 2)),
    0x46: ("Ambient air temperature", "°C", lambda d: d[0] - 40),
    0x5C: ("Engine oil temperature", "°C", lambda d: d[0] - 40),
    0x1C: ("OBD standard", "", lambda d: OBD_STANDARDS.get(d[0], f"type {d[0]}")),
    0x4D: ("Time run with check engine light on", "min", _u16),
    0x4E: ("Time since codes cleared", "min", _u16),
    0x51: ("Fuel type", "", lambda d: FUEL_TYPES.get(d[0], f"type {d[0]}")),
    0x5E: ("Engine fuel rate", "L/h", lambda d: round(_u16(d) / 20, 2)),
    0xA6: ("Odometer", "km", lambda d: round(((d[0] << 24) | (d[1] << 16) | (d[2] << 8) | d[3]) / 10, 1)),
}

# PIDs shown in the scan report / live view (in display order)
REPORT_PIDS = [0x0C, 0x0D, 0x05, 0x5C, 0x04, 0x11, 0x0B, 0x10, 0x0F, 0x06, 0x07, 0x08,
               0x09, 0x0E, 0x14, 0x15, 0x03, 0x0A, 0x2F, 0x42, 0x33, 0x46, 0x1F, 0x21,
               0x30, 0x31, 0x4D, 0x4E, 0x5E, 0xA6, 0x51, 0x1C]
LIVE_PIDS = [0x0C, 0x0D, 0x05, 0x04, 0x11, 0x0B, 0x10, 0x06, 0x07, 0x08, 0x09, 0x0E,
             0x14, 0x15, 0x42]
FREEZE_PIDS = [0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F,
               0x10, 0x11]


def decode_pid(pid: int, data: bytes):
    name, unit, fn = PIDS[pid]
    try:
        return fn(data)
    except (IndexError, KeyError):
        return None


def parse_supported(data: bytes, base: int) -> set[int]:
    pids = set()
    for i in range(32):
        if data[i // 8] & (0x80 >> (i % 8)):
            pids.add(base + i + 1)
    return pids
