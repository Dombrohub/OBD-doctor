"""Rule-based diagnosis: combine trouble codes, freeze frame and live data
into a ranked list of findings with likely causes and next steps."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import vin as vinlib
from .dtc_db import lookup

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


@dataclass
class Finding:
    severity: str
    title: str
    detail: str = ""
    causes: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    codes: list[str] = field(default_factory=list)


def _all_codes(scan: dict) -> dict[str, set[str]]:
    out = {}
    for kind, by_ecu in scan["dtcs"].items():
        out[kind] = {c for codes in by_ecu.values() for c in codes}
    return out


def inspection_readiness(scan: dict) -> dict | None:
    """Would this car pass an OBD-II emissions inspection? Uses the US EPA guideline: check engine light
    off, and at most 2 readiness monitors not ready for 1996-2000 models, at most 1 for 2001 and newer."""
    st = scan.get("status")
    if not st:
        return None
    year = vinlib.model_year(scan.get("vin"))
    allowed = 2 if year and year <= 2000 else 1
    incomplete = [m for m, s in st.get("monitors", {}).items() if s == "incomplete"]
    reasons = []
    if st.get("mil_on"):
        reasons.append("the check engine light is on")
    if len(incomplete) > allowed:
        reasons.append(f"{len(incomplete)} readiness monitors are not ready ({', '.join(incomplete)}); "
                       f"the usual limit is {allowed} for a {'1996-2000' if allowed == 2 else '2001 or newer'} vehicle")
    return {"pass": not reasons, "reasons": reasons, "incomplete": incomplete, "allowed": allowed,
            "model_year": year}


def diagnose(scan: dict) -> list[Finding]:
    codes = _all_codes(scan)
    active = codes["stored"] | codes["pending"]          # what's going on now
    every = active | codes["permanent"]
    live = scan.get("live", {})
    status = scan.get("status") or {}
    f: list[Finding] = []

    # ------------------------------------------------------------------ combined code rules
    lean = {"P0171", "P0174"} & every
    rich = {"P0172", "P0175"} & every
    misfires = {c for c in every if re.fullmatch(r"P030\d|P031[0-2]", c)}
    cyl_misfires = {int(c[3:]) for c in misfires if c != "P0300"}
    cat = {"P0420", "P0430"} & every
    timing = {"P0008", "P0016", "P0017", "P0018", "P0011", "P0021"} & every

    if len(lean) == 2:
        f.append(Finding("high", "Both engine banks are running lean",
                         "When BOTH banks are lean the cause is shared by the whole engine, "
                         "not a single sensor or injector.",
                         ["Vacuum leak after the MAF sensor (intake gasket, PCV, brake booster hose)",
                          "Dirty / under-reading MAF sensor",
                          "Low fuel pressure (fuel pump, filter, regulator)"],
                         ["Clean the MAF sensor and re-check fuel trims",
                          "Smoke test the intake for vacuum leaks",
                          "Measure fuel pressure"], sorted(lean)))
    elif lean:
        bank = "1" if "P0171" in lean else "2"
        f.append(Finding("medium", f"Bank {bank} only is running lean",
                         "Only one bank is lean, so look for something specific to that side of the engine.",
                         [f"Vacuum leak on the bank {bank} intake runners / gasket",
                          f"Clogged injector(s) on bank {bank}",
                          f"Exhaust leak before the bank {bank} upstream O2 sensor",
                          f"Faulty bank {bank} upstream O2 sensor"], [], sorted(lean)))
    if len(rich) == 2:
        f.append(Finding("medium", "Both engine banks are running rich", "",
                         ["Fuel pressure too high / bad regulator", "MAF over-reading",
                          "EVAP purge valve stuck open", "Coolant temp sensor reading too cold"],
                         [], sorted(rich)))

    if misfires and lean:
        f.append(Finding("high", "Misfire is likely caused by the lean condition",
                         "A lean air/fuel mixture is hard to ignite. Fix the lean problem first - "
                         "the misfire will often disappear with it.", [], [],
                         sorted(misfires | lean)))
    if len(cyl_misfires) == 1:
        cyl = next(iter(cyl_misfires))
        coil, inj = f"P03{50 + cyl:02d}", f"P02{cyl:02d}"
        if coil in every:
            f.append(Finding("high", f"Cylinder {cyl} misfire caused by its ignition coil",
                             "A coil circuit fault on the same cylinder as the misfire.",
                             [f"Ignition coil cylinder {cyl}"], ["Replace the coil and its spark plug"],
                             [f"P030{cyl}" if cyl < 10 else f"P03{cyl}", coil]))
        elif inj in every:
            f.append(Finding("high", f"Cylinder {cyl} misfire caused by its fuel injector",
                             "", [f"Fuel injector / wiring cylinder {cyl}"], [], [inj]))
    elif len(cyl_misfires) >= 2 or ("P0300" in misfires and not lean):
        f.append(Finding("high", "Several cylinders are misfiring",
                         "A problem shared by several cylinders is more likely than several bad parts.",
                         ["Worn spark plugs (all due for replacement)", "Bad fuel / low fuel pressure",
                          "Large vacuum leak", "EGR valve stuck open",
                          "Timing chain/belt out of position" + (" (cam/crank correlation code present!)" if timing else "")],
                         [], sorted(misfires)))
    if timing and misfires:
        f.append(Finding("critical", "Possible timing chain / belt problem",
                         "Cam/crank correlation codes together with misfires suggest the valve timing is off. "
                         "A jumped belt/chain can cause severe engine damage.",
                         ["Stretched timing chain", "Jumped timing belt", "Failed VVT phaser"],
                         ["Have the timing checked before driving further"], sorted(timing | misfires)))
    if cat and (misfires or lean or rich):
        f.append(Finding("high", "Catalytic converter code is probably a SYMPTOM",
                         "Misfires and wrong fuel mixture overheat or poison the catalyst and confuse its monitor. "
                         "Do not replace the catalytic converter until the engine problem is fixed and the code returns.",
                         [], [], sorted(cat | misfires | lean | rich)))

    u_codes = {c for c in every if c.startswith("U")}
    if len(u_codes) >= 2:
        f.append(Finding("high", "Multiple network communication faults",
                         "Several modules lost communication - usually one shared cause.",
                         ["Weak battery / low voltage during cranking", "Corroded ground connections",
                          "Damaged CAN bus wiring or water in a connector", "One failing module disrupting the bus"],
                         ["Test/charge the battery first", "Inspect main grounds and module connectors"],
                         sorted(u_codes)))
    heaters = {"P0030", "P0036", "P0050", "P0056", "P0135", "P0141", "P0155", "P0161"} & every
    if len(heaters) >= 2:
        f.append(Finding("medium", "Several O2 sensor heater faults at once",
                         "Multiple heater codes together usually point to their shared power supply.",
                         ["Blown O2 heater fuse", "Failed relay / power feed"], ["Check the O2 heater fuse"],
                         sorted(heaters)))
    evap = {c for c in every if c in ("P0440", "P0442", "P0455", "P0456", "P0457")}
    if evap:
        f.append(Finding("low", "Evaporative (fuel vapor) system leak",
                         "Harmless to the engine, but it will fail an emissions test.",
                         ["Gas cap loose/worn (most common)", "Cracked EVAP hose", "Purge/vent valve"],
                         ["Tighten or replace the gas cap, then drive a few days"], sorted(evap)))

    # ------------------------------------------------------------------ changes since the last scan
    hist = scan.get("history")
    if hist:
        if hist["returned"]:
            f.append(Finding("high", "Codes came back after going away: " + ", ".join(hist["returned"]),
                             "These codes were gone at the last scan (fixed or cleared) and have returned. "
                             "The fault is still there, or it is intermittent - for example a loose connector "
                             "or a part that fails only when hot, cold or wet.",
                             [], ["If the code returned after a repair, the repair didn't address the cause",
                                  "Note when the light comes on (weather, temperature, speed) - it helps find "
                                  "intermittent faults"], hist["returned"]))
        if hist["new"]:
            f.append(Finding("info", "New since the last scan (" + str(hist["previous_time"]) + "): "
                             + ", ".join(hist["new"]), "", [], [], hist["new"]))
        if hist["resolved"]:
            f.append(Finding("info", "Gone since the last scan: " + ", ".join(hist["resolved"]),
                             "Either repaired, cleared, or the condition hasn't happened again yet.",
                             [], [], hist["resolved"]))

    # ------------------------------------------------------------------ ABS / airbag modules
    mods = (scan.get("modules") or {})
    for m in mods.get("list", []):
        mcodes = [d["code"] for d in m["dtcs"]]
        if not mcodes:
            continue
        bases = {c.split("-")[0] for c in mcodes}
        if m["system"] == "Airbag":
            causes = ["Seat connectors under the front seats (side airbags, belt pretensioners, occupant sensor) - "
                      "often disturbed when seats are moved or the carpet is cleaned",
                      "Seat belt buckle switch or pretensioner",
                      "Crash data stored after an accident (the module must be reset or replaced)",
                      "Low battery voltage during starting"]
            if bases & {"B0001", "B0002"}:
                causes.insert(0, "Clockspring (spiral cable) behind the steering wheel - the usual cause of driver "
                                 "airbag circuit codes, especially if the horn or wheel buttons also misbehave")
            f.append(Finding("high", "Airbag system fault - airbags may not deploy in a crash",
                             "When the airbag light is on, the system has detected a fault and may have switched "
                             "itself off. Get it repaired soon, and have a professional do the work: airbag "
                             "components can injure you if handled wrongly.",
                             causes, ["Never probe airbag connectors with a test light or multimeter",
                                      "Disconnect the battery and wait several minutes before any work near airbags",
                                      "An airbag light that stays on fails a NZ WoF / Australian roadworthy and many "
                                      "other inspections"],
                             mcodes))
        elif m["system"] == "ABS":
            wheels = [w for c, w in (("C0035", "left front"), ("C0040", "right front"),
                                     ("C0045", "left rear"), ("C0050", "right rear")) if c in bases]
            causes = ([f"{w.capitalize()} wheel speed sensor, its wiring or tone ring" for w in wheels] or
                      ["A wheel speed sensor, its wiring or tone ring (the most common ABS fault)"])
            causes += ["Low brake fluid", "ABS pump or module fault", "Low battery voltage"]
            f.append(Finding("high", "ABS / stability control fault",
                             "Your normal brakes still work, but ABS (and usually traction and stability control) "
                             "is switched off. Wheels can lock under hard braking, especially on wet or icy roads. "
                             "Leave extra stopping distance until it is repaired.",
                             causes, ["Check the brake fluid level",
                                      "Inspect the wheel speed sensor wiring at the wheel named in the code",
                                      "An ABS light that stays on fails a NZ WoF / Australian roadworthy and many "
                                      "other inspections"],
                             mcodes))
        else:
            f.append(Finding("medium", f"Codes in another module ({m['label']})",
                             "Codes from a body or chassis module. The meaning of these is often brand-specific.",
                             [], [], mcodes))
    if mods.get("supported") and not mods.get("list") and not mods.get("searched"):
        f.append(Finding("info", "No ABS or airbag module answered the quick check",
                         "OBD EFT asked the ABS and airbag addresses used by Ford/Mazda, Toyota/Lexus, Hyundai/Kia, "
                         "VW group and Nissan. Your car may use different addresses.",
                         [], ["Run the full module search on the ABS & airbag tab (takes about a minute)",
                              mods.get("note", "")]))

    # ------------------------------------------------------------------ live data rules
    rpm = live.get(0x0C)
    running = rpm is not None and rpm > 400
    coolant = live.get(0x05)
    warm = coolant is not None and coolant >= 75
    runtime = live.get(0x1F)

    if coolant is not None:
        if coolant >= 115:
            f.append(Finding("critical", f"Engine is OVERHEATING ({coolant} °C)",
                             "Stop the engine now to avoid head gasket / engine damage.",
                             ["Low coolant", "Cooling fan not working", "Water pump", "Stuck thermostat"]))
        elif coolant >= 106:
            f.append(Finding("high", f"Engine running hot ({coolant} °C)", "Normal is about 85-105 °C.",
                             ["Low coolant", "Cooling fan", "Clogged radiator", "Thermostat"]))
        elif running and runtime and runtime > 900 and coolant < 70:
            f.append(Finding("medium", f"Engine not reaching operating temperature ({coolant} °C after {runtime // 60} min)",
                             "", ["Thermostat stuck open", "Faulty coolant temperature sensor"],
                             ["Replace the thermostat"], sorted({"P0128", "P0125"} & every)))

    if running and warm:
        for bank, st, lt in ((1, 0x06, 0x07), (2, 0x08, 0x09)):
            if lt not in live:
                continue
            total = live.get(st, 0) + live[lt]
            if total >= 15:
                f.append(Finding("medium" if total < 25 else "high",
                                 f"Bank {bank} fuel trim is high: {total:+.1f}% (engine adding fuel)",
                                 "The computer is adding extra fuel to compensate for unmetered air or low fuel delivery.",
                                 [_v for _v in ("Vacuum leak", "Dirty MAF", "Low fuel pressure")]))
            elif total <= -15:
                f.append(Finding("medium", f"Bank {bank} fuel trim is low: {total:+.1f}% (engine removing fuel)",
                                 "", ["Leaking injector", "High fuel pressure", "Purge valve stuck open"]))

    volts = live.get(0x42) or scan.get("adapter_voltage")
    if volts:
        if running and volts < 13.0:
            f.append(Finding("high", f"Charging voltage low ({volts:.1f} V with engine running)",
                             "Should be 13.5-14.7 V. The battery is draining while you drive.",
                             ["Failing alternator", "Loose/slipping drive belt", "Corroded battery cables"],
                             ["Have the alternator and battery load-tested"], sorted({"P0562"} & every)))
        elif running and volts > 15.2:
            f.append(Finding("high", f"Charging voltage too high ({volts:.1f} V)",
                             "Overcharging can damage the battery and electronics.",
                             ["Faulty alternator voltage regulator"], [], sorted({"P0563"} & every)))
        elif not running and volts < 12.2:
            f.append(Finding("medium", f"Battery voltage low ({volts:.1f} V, engine off)",
                             "A healthy, charged battery reads about 12.6 V.",
                             ["Weak/old battery", "Parasitic drain", "Car not driven for a long time"]))

    speed = live.get(0x0D)
    if running and warm and speed == 0 and rpm > 1150 and live.get(0x11, 0) < 20:
        f.append(Finding("medium", f"High idle speed ({rpm} rpm when warm)", "Normal warm idle is ~600-900 rpm.",
                         ["Vacuum leak", "Dirty throttle body / idle air control valve"]))

    if status.get("mil_on") and not every:
        f.append(Finding("medium", "Check engine light is ON but no codes were returned",
                         "Codes may be stored in a module this adapter cannot reach, or the adapter missed them.",
                         [], ["Re-run the scan with the engine running", "Try a different adapter / protocol"]))
    incomplete = [m for m, s in status.get("monitors", {}).items() if s == "incomplete"]
    if incomplete:
        f.append(Finding("info", "Readiness monitors not complete: " + ", ".join(incomplete),
                         "These self-tests haven't finished since codes were last cleared or the battery was "
                         "disconnected. Most emissions inspections fail with incomplete monitors.",
                         [], ["Drive normally for several days (mix of city + highway) and re-scan"]))
    insp = inspection_readiness(scan)
    if insp and not insp["pass"]:
        f.append(Finding("medium", "Would likely FAIL an emissions inspection right now",
                         "Because " + " and ".join(insp["reasons"]) + ". Rules vary by state and country; "
                         "this uses the common US EPA guideline.",
                         [], ["Fix the cause of any check engine light first",
                              "After a repair or clearing codes, drive normally for several days (city and highway) "
                              "so the readiness monitors can finish, then re-scan before the inspection"]))
    perm_only = codes["permanent"] - active
    if perm_only:
        f.append(Finding("info", "Permanent codes from a previous fault",
                         "Permanent codes cannot be erased with a scan tool; the car clears them by itself after the "
                         "repair is verified over several drive cycles.", [], [], sorted(perm_only)))

    f.sort(key=lambda x: SEVERITY_ORDER[x.severity])
    return f


def code_details(scan: dict) -> list[dict]:
    """One lookup entry per unique code, with where it was found."""
    seen: dict[str, dict] = {}
    for kind, by_ecu in scan["dtcs"].items():
        for ecu, codes in by_ecu.items():
            for c in codes:
                entry = seen.setdefault(c, {**lookup(c), "found_in": []})
                entry["found_in"].append(f"{ecu} ({kind})")
    return sorted(seen.values(), key=lambda e: SEVERITY_ORDER.get(e["severity"], 9))
