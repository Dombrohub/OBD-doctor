"""On-board monitor test results (OBD-II service $06) and ECU information (service $09).

Service $06 reports the actual results of the car's own self-tests: catalyst efficiency,
O2 sensor response, EVAP leak tests, EGR flow, and - very usefully - misfire counts for
each cylinder. Each result has a measured value and the car's own minimum/maximum limits,
so it shows tests that are close to failing (or have failed) before a trouble code appears.

Pass/fail is decided by comparing the raw value with the raw limits, exactly as the car does,
so it doesn't depend on the unit scaling below (which is only used to display numbers).
This implements the CAN (ISO 15765-4) format used by cars from about 2008 on.
"""

from __future__ import annotations

# Monitor IDs (OBDMID), SAE J1979
def mid_name(mid: int) -> str:
    if 0x01 <= mid <= 0x10:
        bank, sensor = (mid - 1) // 4 + 1, (mid - 1) % 4 + 1
        return f"O2 sensor bank {bank} sensor {sensor}"
    names = {0x21: "Catalyst bank 1", 0x22: "Catalyst bank 2", 0x23: "Catalyst bank 3", 0x24: "Catalyst bank 4",
             0x31: "EGR / VVT bank 1", 0x32: "EGR / VVT bank 2", 0x33: "EGR / VVT bank 3", 0x34: "EGR / VVT bank 4",
             0x35: "VVT bank 1", 0x36: "VVT bank 2", 0x37: "VVT bank 3", 0x38: "VVT bank 4",
             0x39: "EVAP leak test (cap off / 0.150\")", 0x3A: "EVAP leak test (0.090\")",
             0x3B: "EVAP leak test (0.040\")", 0x3C: "EVAP leak test (0.020\")", 0x3D: "EVAP purge flow",
             0x61: "Heated catalyst bank 1", 0x62: "Heated catalyst bank 2",
             0x71: "Secondary air 1", 0x72: "Secondary air 2", 0x81: "Fuel system bank 1",
             0x82: "Fuel system bank 2", 0x83: "Fuel system bank 3", 0x84: "Fuel system bank 4",
             0x85: "Boost pressure bank 1", 0x86: "Boost pressure bank 2", 0x90: "NOx absorber bank 1",
             0x91: "NOx absorber bank 2", 0x98: "NOx catalyst bank 1", 0x99: "NOx catalyst bank 2",
             0xA1: "Misfire (all cylinders)", 0xB0: "Particulate filter bank 1", 0xB1: "Particulate filter bank 2"}
    if 0x41 <= mid <= 0x50:
        bank, sensor = (mid - 0x41) // 4 + 1, (mid - 0x41) % 4 + 1
        return f"O2 sensor heater bank {bank} sensor {sensor}"
    if 0xA2 <= mid <= 0xAD:
        return f"Misfire cylinder {mid - 0xA1}"
    return names.get(mid, f"Monitor {mid:02X}")


TID_NAMES = {0x01: "Rich-to-lean threshold voltage", 0x02: "Lean-to-rich threshold voltage",
             0x03: "Low voltage for switch time", 0x04: "High voltage for switch time",
             0x05: "Rich-to-lean switch time", 0x06: "Lean-to-rich switch time", 0x07: "Minimum sensor voltage",
             0x08: "Maximum sensor voltage", 0x09: "Time between transitions", 0x0A: "Sensor period",
             0x0B: "Misfires (average of last 10 drives)", 0x0C: "Misfires (this / last drive)"}

# Unit and scaling IDs: id -> (multiplier, offset, unit). 0x80+ are signed versions.
UAS = {
    0x01: (1, 0, ""), 0x02: (0.1, 0, ""), 0x03: (0.01, 0, ""), 0x04: (0.001, 0, ""), 0x05: (0.0000305, 0, ""),
    0x06: (0.000305, 0, ""), 0x07: (0.25, 0, "rpm"), 0x08: (0.01, 0, "km/h"), 0x09: (1, 0, "km/h"),
    0x0A: (0.122, 0, "mV"), 0x0B: (0.001, 0, "V"), 0x0C: (0.01, 0, "V"), 0x0D: (0.00390625, 0, "mA"),
    0x0E: (0.001, 0, "A"), 0x0F: (0.01, 0, "A"), 0x10: (1, 0, "ms"), 0x11: (100, 0, "ms"), 0x12: (1, 0, "s"),
    0x13: (1, 0, "mΩ"), 0x14: (1, 0, "Ω"), 0x15: (1, 0, "kΩ"), 0x16: (0.1, -40, "°C"), 0x17: (0.01, 0, "kPa"),
    0x18: (0.0117, 0, "kPa"), 0x19: (0.079, 0, "kPa"), 0x1A: (1, 0, "kPa"), 0x1B: (10, 0, "kPa"),
    0x1C: (0.01, 0, "°"), 0x1D: (0.5, 0, "°"), 0x1E: (0.0000305, 0, "ratio"), 0x1F: (0.05, 0, "ratio"),
    0x20: (0.00390625, 0, "ratio"), 0x21: (1, 0, "mHz"), 0x22: (1, 0, "Hz"), 0x23: (1, 0, "kHz"),
    0x24: (1, 0, "counts"), 0x25: (1, 0, "km"), 0x26: (0.1, 0, "mV/ms"), 0x27: (0.01, 0, "g/s"),
    0x28: (1, 0, "g/s"), 0x29: (0.25, 0, "Pa/s"), 0x2A: (0.001, 0, "kg/h"), 0x2B: (1, 0, "switches"),
    0x2C: (0.01, 0, "g/cyl"), 0x2D: (0.01, 0, "mg/stroke"), 0x2E: (1, 0, ""), 0x2F: (0.01, 0, "%"),
    0x30: (0.001526, 0, "%"), 0x31: (0.001, 0, "L"), 0x34: (1, 0, "min"), 0x36: (0.01, 0, "g"),
    0x37: (0.1, 0, "g"), 0x38: (1, 0, "g"), 0x3F: (0.01, 0, "L"), 0x40: (1, 0, "ppm"),
    0x81: (1, 0, ""), 0x82: (0.1, 0, ""), 0x83: (0.01, 0, ""), 0x84: (0.001, 0, ""), 0x85: (0.0000305, 0, ""),
    0x86: (0.000305, 0, ""), 0x8A: (0.122, 0, "mV"), 0x8B: (0.001, 0, "V"), 0x8C: (0.01, 0, "V"),
    0x8D: (0.00390625, 0, "mA"), 0x8E: (0.001, 0, "A"), 0x90: (1, 0, "ms"), 0x96: (0.1, 0, "°C"),
    0x9C: (0.01, 0, "°"), 0x9D: (0.5, 0, "°"), 0xA8: (1, 0, "g/s"), 0xA9: (0.25, 0, "Pa/s"),
    0xAF: (0.01, 0, "%"), 0xB0: (0.003052, 0, "%"), 0xFC: (0.01, 0, "kPa"), 0xFD: (0.001, 0, "kPa"),
    0xFE: (0.25, 0, "Pa"),
}


def _scale(raw: int, uas: int) -> tuple[float | int, str]:
    mult, offset, unit = UAS.get(uas, (1, 0, f"(unit {uas:02X})"))
    value = raw * mult + offset
    return (round(value, 4) if isinstance(value, float) else value), unit


def parse_mode06(msg: bytes) -> list[dict]:
    """msg = 46 MID [TID UAS VAL VAL MIN MIN MAX MAX]... (records may repeat the MID)."""
    if len(msg) < 2 or msg[0] != 0x46:
        return []
    mid = msg[1]
    body = msg[1:]
    # Records are usually 9 bytes each (MID repeated); some ECUs send 8-byte records after one MID.
    if len(body) % 9 == 0 and all(body[i] == mid for i in range(0, len(body), 9)):
        recs = [body[i + 1:i + 9] for i in range(0, len(body), 9)]
    elif (len(body) - 1) % 8 == 0:
        recs = [body[1 + i:1 + i + 8] for i in range(0, len(body) - 1, 8)]
    else:
        return []
    out = []
    for r in recs:
        tid, uas = r[0], r[1]
        raw, lo, hi = (r[2] << 8) | r[3], (r[4] << 8) | r[5], (r[6] << 8) | r[7]
        if uas >= 0x80:  # signed values
            raw, lo, hi = (x - 0x10000 if x >= 0x8000 else x for x in (raw, lo, hi))
        value, unit = _scale(raw, uas)
        vmin, _ = _scale(lo, uas)
        vmax, _ = _scale(hi, uas)
        out.append({"mid": mid, "monitor": mid_name(mid), "tid": tid,
                    "test": TID_NAMES.get(tid, f"Manufacturer test {tid:02X}"), "value": value, "min": vmin,
                    "max": vmax, "unit": unit, "passed": lo <= raw <= hi})
    return out


def misfire_counts(tests: list[dict]) -> dict[int, int]:
    """Misfire count per cylinder from the misfire monitor results (prefers this/last drive counts)."""
    counts: dict[int, int] = {}
    for t in tests:
        if 0xA2 <= t["mid"] <= 0xAD and t["tid"] in (0x0B, 0x0C):
            cyl = t["mid"] - 0xA1
            if t["tid"] == 0x0C or cyl not in counts:
                counts[cyl] = int(t["value"])
    return counts


def parse_ascii_records(msg: bytes, size: int) -> list[str]:
    """Service $09 text answers (calibration IDs: 16 chars, ECU name: 20 chars). msg = 49 PID count data..."""
    data = msg[3:]
    out = []
    for i in range(0, len(data) - size + 1, size):
        text = "".join(chr(b) for b in data[i:i + size] if 32 <= b < 127).strip()
        if text:
            out.append(text)
    return out


def parse_cvns(msg: bytes) -> list[str]:
    data = msg[3:]
    return [data[i:i + 4].hex().upper() for i in range(0, len(data) - 3, 4)]
