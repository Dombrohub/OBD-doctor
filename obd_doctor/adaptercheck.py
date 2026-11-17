"""Adapter self-test: what the OBD adapter can do, without needing a car.

Checks the adapter's identity and every command OBD EFT relies on, and says
which features are affected if one is missing. Cheap ELM327 clones often claim
support they don't have; the most reliable check is simply sending each command.
"""

from __future__ import annotations

from .elm327 import ELM327, open_transport

# (commands to send, feature name, what needs it). Each command must not reply "?".
FEATURE_TESTS = [
    (["ATH1", "ATS1"], "Headers and spacing", "everything (identifies which computer answered)"),
    (["ATST19", "ATAT1"], "Timeout control", "faster scans and module searches"),
    (["ATSH7E0", "ATCRA7E8"], "Custom header and receive address", "ABS & airbag modules"),
    (["ATFCSH7E0", "ATFCSD300000", "ATFCSM1"], "Custom flow control",
     "ABS & airbag modules with long replies (e.g. VW group, many others)"),
    (["ATCF700", "ATCM700"], "Receive filters", "Full module search"),
    (["ATCAF0", "ATCAF1"], "Raw CAN frames", "GM ABS & airbag modules"),
    (["ATCEA29", "ATCEA"], "CAN extended addressing", "BMW / MINI ABS & airbag modules"),
]


def _ok(reply: list[str]) -> bool:
    return not any(r.strip() == "?" for r in reply)


def check(elm: ELM327, progress=lambda m: None, test_car: bool = True) -> dict:
    """Run every test on an open adapter. Leaves the adapter in normal OBD mode."""
    progress("Identifying the adapter…")
    elm.send("ATZ", timeout=5)
    elm.send("ATE0", timeout=2)
    version = " ".join(elm.send("ATI", timeout=2)) or "unknown"
    description = " ".join(elm.send("AT@1", timeout=2))
    if description.strip().upper() in ("OK", "?"):
        description = ""
    sti = elm.send("STI", timeout=2)  # STN chips (OBDLink and similar) answer this
    stn = " ".join(sti) if sti and _ok(sti) and "?" not in " ".join(sti) else None
    try:
        voltage = float(" ".join(elm.send("ATRV", timeout=2)).strip().rstrip("Vv"))
    except ValueError:
        voltage = None

    results = []
    for cmds, name, needed_by in FEATURE_TESTS:
        progress(f"Testing: {name}…")
        ok = all(_ok(elm.send(c, timeout=2)) for c in cmds)
        results.append({"feature": name, "supported": ok, "needed_for": needed_by})

    car = None
    if test_car:
        progress("Checking for a car…")
        elm.send("ATD", timeout=3)
        for c in ("ATE0", "ATL0", "ATS1", "ATH1", "ATSP0"):
            elm.send(c, timeout=2)
        reply = elm.send("0100", timeout=15)
        joined = " ".join(reply).upper()
        if "UNABLE TO CONNECT" in joined or "NO DATA" in joined or not reply or "?" in joined:
            car = {"connected": False, "detail": "No car answered (normal if the adapter isn't plugged into a car "
                                                 "with the ignition on)"}
        else:
            dp = elm.send("ATDP", timeout=2)
            car = {"connected": True, "detail": f"A car answered using {' '.join(dp) or 'an OBD-II protocol'}"}

    elm.send("ATD", timeout=3)
    for c in ("ATE0", "ATL0", "ATS1", "ATH1", f"ATSP{elm.protocol or '0'}"):
        elm.send(c, timeout=2)

    verdict, notes = _verdict(version, stn, results)
    return {"version": version, "description": description, "stn": stn, "voltage": voltage,
            "features": results, "car": car, "verdict": verdict, "notes": notes}


def _verdict(version: str, stn: str | None, results: list[dict]) -> tuple[str, list[str]]:
    notes = []
    missing = [r for r in results if not r["supported"]]
    if stn:
        notes.append(f"STN-based adapter ({stn}) - these are high quality and support everything OBD EFT uses.")
    elif "1.5" in version:
        notes.append("Reports version 1.5. Elm Electronics never made a v1.5 chip, so this is a clone. "
                     "Clones vary a lot - the tests below show what this one really supports.")
    if not missing:
        return "Excellent - supports every feature OBD EFT uses", notes
    if all(r["feature"] in ("CAN extended addressing", "Raw CAN frames") for r in missing):
        return "Good - engine scanning and most ABS/airbag modules work", notes + [
            "Missing features only affect the brands listed below. For full support, consider an OBDLink, "
            "vLinker or genuine ELM327 adapter."]
    return "Limited - basic engine scanning works, some ABS/airbag reading won't", notes + [
        "For ABS/airbag reading, consider an OBDLink, vLinker or genuine ELM327 adapter."]


def check_target(target: str, baud: int = 38400, progress=lambda m: None) -> dict:
    """Open an adapter by port name and test it (no car needed)."""
    elm = ELM327(open_transport(target, baud))
    try:
        return check(elm, progress)
    finally:
        elm.t.close()


def format_result(r: dict) -> str:
    lines = [f"Adapter: {r['version']}" + (f" - {r['description']}" if r["description"] else ""),
             f"Verdict: {r['verdict']}"]
    lines += [f"  Note: {n}" for n in r["notes"]]
    if r["voltage"]:
        lines.append(f"Voltage at the adapter: {r['voltage']:.1f} V")
    lines.append("")
    for f in r["features"]:
        lines.append(f"  [{'OK' if f['supported'] else 'NO'}] {f['feature']:<36} needed for: {f['needed_for']}")
    if r["car"]:
        lines += ["", f"Car: {r['car']['detail']}"]
    return "\n".join(lines)
