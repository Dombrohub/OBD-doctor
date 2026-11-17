"""OBD EFT - read your car's computer and figure out what's wrong.

Usage examples:
  python -m obd_doctor scan --port /dev/ttyUSB0        # USB adapter (Linux)
  python -m obd_doctor scan --port COM3                # USB/Bluetooth adapter (Windows)
  python -m obd_doctor scan --port 192.168.0.10:35000  # Wi-Fi adapter
  python -m obd_doctor scan --port demo                # built-in simulated car
  python -m obd_doctor live --port demo                # live sensor dashboard
  python -m obd_doctor clear --port demo               # erase codes / turn off check engine light
  python -m obd_doctor scan --port COM3 --discover     # also search every address for ABS/airbag modules
  python -m obd_doctor clear-modules --port COM3       # clear ABS & airbag codes
  python -m obd_doctor history                         # past scans: codes, fuel trims, battery
  python -m obd_doctor test-adapter --port COM3        # what your adapter supports (no car needed)
  python -m obd_doctor live --port COM3 --record drive.csv   # record sensors during a drive
  python -m obd_doctor lookup P0420 C0035-1C           # explain codes (no car needed)
  python -m obd_doctor scan --port demo --ai           # add an AI explanation (Claude)
  python -m obd_doctor set-api-key                     # save your Anthropic API key
  python -m obd_doctor                                 # graphical interface
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict

from . import decoder
from .diagnose import code_details, diagnose
from .dtc_db import is_valid, lookup
from .elm327 import ELM327, OBDError, find_serial_adapter, open_transport
from .scanner import Scanner

# --------------------------------------------------------------------------- pretty output
USE_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
COLORS = {"critical": "1;97;41", "high": "1;31", "medium": "1;33", "low": "36", "info": "34",
          "bold": "1", "dim": "2", "green": "32"}
ICONS = {"critical": "!!! CRITICAL", "high": "!! HIGH", "medium": "! MEDIUM", "low": "LOW", "info": "INFO"}


def c(text, style):
    return f"\033[{COLORS[style]}m{text}\033[0m" if USE_COLOR else str(text)


def header(title):
    print("\n" + c(f"== {title} " + "=" * max(0, 64 - len(title)), "bold"))


def fmt_value(pid, v):
    unit = decoder.PIDS[pid][1]
    return f"{v} {unit}".strip()


def print_code(info, indent="  "):
    sev = info["severity"]
    print(f"{indent}{c(info['code'], 'bold')}  {info['description']}  [{c(sev.upper(), sev)}]")
    if info.get("found_in"):
        print(f"{indent}   {c('Found in: ' + ', '.join(info['found_in']), 'dim')}")
    if info["manufacturer_specific"]:
        print(f"{indent}   {c('Manufacturer-specific code - meaning varies by brand', 'dim')}")
    if info["causes"]:
        print(f"{indent}   Likely causes (most common first):")
        for cause in info["causes"]:
            print(f"{indent}     - {cause}")
    for tip in info["tips"]:
        print(f"{indent}   Tip: {tip}")


def print_report(scan, findings):
    if scan.get("demo"):
        print(c("\n  DEMO MODE - this is a simulated car with made-up faults, not a real vehicle.", "medium"))
    header("VEHICLE")
    from . import vin as vinlib
    from .diagnose import inspection_readiness
    decoded = vinlib.describe(scan.get("vin"))
    print(f"  VIN:       {scan['vin'] or 'not reported'}" + (f"  ({decoded})" if decoded else ""))
    print(f"  Protocol:  {scan['protocol']}")
    print(f"  Adapter:   {scan['adapter']}")
    if scan["adapter_voltage"]:
        print(f"  Battery:   {scan['adapter_voltage']:.1f} V (at OBD port)")
    st = scan.get("status")
    if st:
        mil = c("ON", "high") if st["mil_on"] else c("OFF", "green")
        print(f"  Check engine light: {mil}   Codes reported by ECU: {st['dtc_count']}   Engine: {st['engine_type']}")
        insp = inspection_readiness(scan)
        if insp:
            verdict = c("likely PASS", "green") if insp["pass"] else c("likely FAIL", "high")
            print(f"  Emissions inspection: {verdict}" + ("" if insp["pass"] else " - " + "; ".join(insp["reasons"])))
        if st["monitors"]:
            print("  Readiness monitors: " + ", ".join(
                f"{n} {c('OK', 'green') if s == 'complete' else c('NOT READY', 'medium')}"
                for n, s in st["monitors"].items()))

    header("TROUBLE CODES")
    details = code_details(scan)
    if not details:
        print("  " + c("No trouble codes stored. ", "green"))
    for info in details:
        print_code(info)
        print()

    hist = scan.get("history")
    if hist:
        header(f"CHANGES SINCE LAST SCAN ({hist['previous_time']})")
        for key, label, style in (("new", "New", "high"), ("returned", "Came back", "high"),
                                  ("resolved", "Gone", "green"), ("persisting", "Still there", "medium")):
            if hist[key]:
                print(f"  {c(label + ':', style):<24} {', '.join(hist[key])}")
        if not any(hist[k] for k in ("new", "returned", "resolved", "persisting")):
            print("  No trouble codes now or at the last scan.")

    mods = scan.get("modules")
    if mods:
        header("ABS, AIRBAG & TRANSMISSION MODULES")
        if not mods["supported"]:
            print(f"  {mods['note']}")
        elif not mods["list"]:
            print("  No ABS, airbag or transmission module answered" + (" the full search." if mods.get("searched") else
                  " at the common addresses. Try: scan --discover (full module search, about a minute)."))
            print(c(f"  {mods['note']}", "dim"))
        for m in mods["list"]:
            n = len(m["dtcs"])
            state = c(f"{n} code(s)", "high") if n else c("no codes", "green")
            extra = f"  {m['error']}" if m["error"] else ""
            print(f"  {m['label']:<44} {state}{c(extra, 'dim')}")

    ff = scan.get("freeze_frame")
    if ff:
        header(f"FREEZE FRAME (conditions when {ff['trigger_dtc']} was set)")
        for pid, v in ff["data"].items():
            print(f"  {decoder.PIDS[pid][0]:<32} {fmt_value(pid, v)}")

    tests = scan.get("tests") or []
    if tests:
        from .monitor_tests import misfire_counts
        header("SELF-TEST RESULTS (OBD Mode 6)")
        for t in tests:
            if 0xA1 <= t["mid"] <= 0xAD:
                continue  # misfire counters are summarised below
            mark = c("PASS", "green") if t["passed"] else c("FAIL", "high")
            val = f"{t['value']} {t['unit']}".strip()
            print(f"  {mark}  {t['monitor']:<34} {t['test']:<34} {val}  (limits {t['min']} to {t['max']})")
        counts = misfire_counts(tests)
        if counts:
            print("  Misfire counts: " + ", ".join(f"cylinder {cyl}: {n}" for cyl, n in sorted(counts.items())))
    info = scan.get("ecu_info") or {}
    if info:
        header("COMPUTERS")
        for ecu, d in info.items():
            print(f"  {ecu}: {d.get('name', '')}  calibration {', '.join(d.get('calibration_ids', [])) or '-'}"
                  f"  checksum {', '.join(d.get('checksums', [])) or '-'}")

    if scan["live"]:
        header("LIVE DATA")
        for pid, v in scan["live"].items():
            if pid == 0x01:
                continue
            print(f"  {decoder.PIDS[pid][0]:<32} {fmt_value(pid, v)}")

    header("DIAGNOSIS")
    if not findings:
        print("  " + c("No problems detected. The engine computer reports everything is OK.", "green"))
    for i, fnd in enumerate(findings, 1):
        print(f"  {i}. {c(ICONS[fnd.severity], fnd.severity)}  {c(fnd.title, 'bold')}")
        if fnd.codes:
            print(f"     {c('Related codes: ' + ', '.join(fnd.codes), 'dim')}")
        if fnd.detail:
            print(f"     {fnd.detail}")
        for cause in fnd.causes:
            print(f"       - {cause}")
        for step in fnd.steps:
            print(f"     -> {step}")
        print()
    print(c("  Note: this is guidance based on OBD-II data, not a substitute for a professional inspection.\n"
            "  For anything safety-related (brakes, steering, overheating, oil pressure) see a mechanic.", "dim"))


# --------------------------------------------------------------------------- connection
def connect(args) -> tuple[ELM327, Scanner]:
    target = args.port
    if target in ("demo", "demo-gm", "demo-holden", "demo-bmw"):
        from .simulator import start_background
        target = f"127.0.0.1:{start_background(target[5:] or 'ford')}"
        print(c("Using the built-in simulated car (demo mode)", "dim"))
    elif target == "auto":
        print("Searching for an ELM327 adapter on serial ports...")
        target, args.baud = find_serial_adapter()
        print(f"Found adapter on {target} @ {args.baud} baud")
    print(f"Connecting to {target} ...")
    elm = ELM327(open_transport(target, args.baud), protocol=args.protocol, debug=args.debug)
    elm.initialize()
    print(c(f"Connected: {elm.protocol_name}", "green"))
    return elm, Scanner(elm)


def cmd_scan(args):
    try:
        elm, sc = connect(args)
    except Exception:
        if args.support_file:
            from .support import write_support_file
            write_support_file(args.support_file, port=args.port)
            print(f"Support file saved to {args.support_file}")
        raise
    try:
        progress = lambda m: print(c(f"  ... {m}", "dim"))
        scan = sc.full_scan(progress=progress, modules=not args.no_modules)
        if args.discover:
            from .modules import merge_into_dtcs
            scan["modules"] = sc.scan_modules(discover=True, progress=progress, vin=scan.get("vin"))
            merge_into_dtcs(scan)
    finally:
        elm.close()
    if args.port.startswith("demo"):
        scan["demo"] = True
    elif not args.no_save:
        from . import history
        try:
            history.save(scan)
        except OSError as e:
            print(c(f"  (Couldn't save to scan history: {e})", "dim"))
    findings = diagnose(scan)
    print_report(scan, findings)
    ai_text = None
    if args.ai:
        ai_text = run_ai(scan, findings, args.vehicle, args.symptoms)
    if args.support_file:
        from .support import write_support_file
        write_support_file(args.support_file, scan, findings, elm=elm, port=args.port,
                           ai_transcript=[("assistant", ai_text)] if ai_text else None)
        print(f"Support file saved to {args.support_file}")
    if args.json:
        data = build_json(scan, findings)
        if ai_text:
            data["ai_explanation"] = [{"role": "assistant", "text": ai_text}]
        with open(args.json, "w") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
        print(f"\nReport saved to {args.json}")


def run_ai(scan, findings, vehicle="", symptoms="") -> str | None:
    """Stream an AI explanation to the console, then offer follow-up questions."""
    from . import ai
    header("AI EXPLANATION (Claude)")
    print(c("  Sending the codes and sensor readings (not the VIN) to Anthropic's Claude...\n", "dim"))
    explainer = ai.AIExplainer()
    out = lambda t: print(t, end="", flush=True)
    try:
        text = explainer.explain(scan, findings, vehicle or "", symptoms or "", on_text=out)
        print()
        if sys.stdin.isatty():
            while True:
                q = input(c("\nFollow-up question (Enter to finish): ", "bold")).strip()
                if not q:
                    break
                print()
                explainer.ask(q, on_text=out)
                print()
        return explainer.transcript_text() or text
    except ai.AIError as e:
        print(c(f"  AI explanation unavailable: {e}", "high"))
        return None
    except (KeyboardInterrupt, EOFError):
        print()
        return explainer.transcript_text() or None


def cmd_set_api_key(args):
    import getpass
    from . import config
    key = getpass.getpass("Anthropic API key (input hidden, Enter to remove): ").strip()
    config.set("anthropic_api_key", key)
    print(f"{'Saved' if key else 'Removed'} - {config.config_path()}")


def build_json(scan, findings) -> dict:
    ff = scan["freeze_frame"]
    return {**scan, "live": {decoder.PIDS[p][0]: v for p, v in scan["live"].items()},
            "freeze_frame": ff and {"trigger_dtc": ff["trigger_dtc"],
                                    "data": {decoder.PIDS[p][0]: v for p, v in ff["data"].items()}},
            "code_details": code_details(scan), "diagnosis": [asdict(f) for f in findings]}


def text_report(scan, findings) -> str:
    """The console report as plain text (no colors)."""
    import contextlib
    import io
    global USE_COLOR
    old, USE_COLOR = USE_COLOR, False
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            print_report(scan, findings)
    finally:
        USE_COLOR = old
    return buf.getvalue()


def cmd_codes(args):
    elm, sc = connect(args)
    try:
        for mode, label in (("03", "Stored"), ("07", "Pending"), ("0A", "Permanent")):
            res = sc.read_dtcs(mode)
            codes = sorted({x for v in res.values() for x in v})
            print(f"{label:>10}: {', '.join(codes) if codes else 'none'}")
            for code in codes:
                print(f"            {code}  {lookup(code)['description']}")
    finally:
        elm.close()


def cmd_clear(args):
    print(c("WARNING: Clearing codes also erases freeze-frame data and resets readiness monitors.\n"
            "The car will NOT pass an emissions test until it has been driven for a few days.\n"
            "Clearing codes does not fix the problem - the light will return if the fault remains.", "medium"))
    if not args.yes and input("Clear codes now? Engine OFF, ignition ON. [y/N] ").strip().lower() != "y":
        print("Cancelled.")
        return
    elm, sc = connect(args)
    try:
        print(c("Codes cleared.", "green") if sc.clear_dtcs() else c("The car did not confirm the clear.", "high"))
    finally:
        elm.close()


def cmd_check_update(args):
    from . import __version__, update
    try:
        info = update.check()
    except update.UpdateError as e:
        print(f"OBD EFT {__version__}. {e}")
        return
    if not info:
        print(f"You have the latest version ({__version__}).")
        return
    print(f"OBD EFT {info['version']} is available (you have {__version__}).")
    if info["notes"]:
        print("\nWhat's new:\n" + info["notes"][:2000])
    print(f"\nDownload: {info.get('installer_url') or info.get('page')}")


def cmd_history(args):
    from . import history
    if args.clear:
        print(f"Deleted {history.clear()} saved scan(s).")
        return
    scans = history.load_all()
    if not scans:
        print(f"No saved scans yet. Scans are saved in {history.history_dir()}")
        return
    print(f"{'Date':<20} {'VIN':<18} {'CEL':<4} {'LTFT B1':>8} {'LTFT B2':>8} {'Battery':>8}  Codes")
    for s in scans[-args.limit:]:
        h = history.summarize(s)
        t = h["tracked"]
        fmt = lambda v, u: f"{v:g}{u}" if isinstance(v, (int, float)) else "-"
        print(f"{h['timestamp'] or '?':<20} {(h['vin'] or 'unknown'):<18} {'ON' if h['mil_on'] else 'off':<4} "
              f"{fmt(t['LTFT B1'], '%'):>8} {fmt(t['LTFT B2'], '%'):>8} {fmt(t['Battery'], 'V'):>8}  "
              f"{', '.join(h['codes']) or '-'}")
    print(c(f"\nSaved in {history.history_dir()}", "dim"))


def cmd_clear_modules(args):
    print(c("WARNING: This clears codes in the ABS, airbag and transmission modules and turns off their warning lights.\n"
            "It does NOT fix anything. If the fault is still there, the light comes back - for airbags that means\n"
            "the airbags may not work. Airbag modules that stored crash data cannot be cleared this way.", "medium"))
    if not args.yes and input("Clear ABS/airbag/transmission codes now? Engine OFF, ignition ON. [y/N] ").strip().lower() != "y":
        print("Cancelled.")
        return
    from .modules import ModuleScanner
    elm, sc = connect(args)
    try:
        progress = lambda m: print(c(f"  ... {m}", "dim"))
        found = sc.scan_modules(discover=args.discover, progress=progress, vin=sc.read_vin())
        targets = [m for m in found["list"] if m["dtcs"]]
        if not targets:
            print("No ABS/airbag/transmission codes to clear." if found["supported"] else found["note"])
            return
        for label, ok in ModuleScanner(elm, progress).clear_modules(targets).items():
            print(f"  {label}: " + (c("cleared", "green") if ok else c("not confirmed", "high")))
    finally:
        elm.close()


def cmd_test_adapter(args):
    from .adaptercheck import check_target, format_result
    target = args.port
    if target in ("demo", "demo-gm", "demo-holden", "demo-bmw"):
        from .simulator import start_background
        target = f"127.0.0.1:{start_background(target[5:] or 'ford')}"
    elif target == "auto":
        target, args.baud = find_serial_adapter()
    print(f"Testing the adapter on {target} (no car needed)...")
    print(format_result(check_target(target, args.baud, progress=lambda m: print(c(f"  ... {m}", "dim")))))


def cmd_live(args):
    elm, sc = connect(args)
    pids = [p for p in decoder.LIVE_PIDS if p in sc.supported_pids()]
    writer, fh, t_start = None, None, time.monotonic()
    if args.record:
        import csv
        fh = open(args.record, "w", newline="", encoding="utf-8-sig")
        writer = csv.writer(fh)
        writer.writerow(["Time (s)"] + [f"{decoder.PIDS[p][0]} ({decoder.PIDS[p][1]})" if decoder.PIDS[p][1]
                                        else decoder.PIDS[p][0] for p in pids])
    try:
        while True:
            t0 = time.monotonic()
            vals = {p: sc.read_pid(p) for p in pids}
            if writer:
                writer.writerow([round(time.monotonic() - t_start, 2)] + [vals.get(p, "") for p in pids])
                fh.flush()
            out = ["\033[H\033[J" if USE_COLOR else "", c("LIVE DATA  (Ctrl+C to stop)", "bold"),
                   time.strftime("%H:%M:%S"), ""]
            for p, v in vals.items():
                if v is not None:
                    out.append(f"  {decoder.PIDS[p][0]:<32} {fmt_value(p, v)}")
            print("\n".join(out), flush=True)
            if args.once:
                break
            time.sleep(max(0.0, args.interval - (time.monotonic() - t0)))
    except KeyboardInterrupt:
        pass
    finally:
        elm.close()
        if fh:
            fh.close()
            print(f"Recording saved to {args.record}")


def cmd_lookup(args):
    for code in args.codes:
        code = code.upper()
        if not is_valid(code):
            print(f"{code}: not a valid OBD-II code (format: P0420, C0035, B1234, U0100)")
            continue
        print_code(lookup(code), indent="")
        print()


def cmd_simulate(args):
    from .simulator import serve
    print(f"Simulated ELM327 + car listening on {args.host}:{args.sim_port}  (Ctrl+C to stop)")
    print(f"Connect with: python -m obd_doctor scan --port {args.host}:{args.sim_port}")
    try:
        serve(args.host, args.sim_port)
    except KeyboardInterrupt:
        pass


def main(argv=None):
    p = argparse.ArgumentParser(prog="obd_doctor", description="Read your car's computer and diagnose problems "
                                "using an ELM327 OBD-II adapter.")
    from . import __version__
    p.add_argument("--version", action="version", version=f"OBD EFT {__version__}")
    sub = p.add_subparsers(dest="cmd")
    conn = argparse.ArgumentParser(add_help=False)
    conn.add_argument("--port", "-p", default="auto",
                      help="Serial port (COM3, /dev/ttyUSB0, /dev/rfcomm0), host:port for Wi-Fi "
                           "adapters (192.168.0.10:35000), 'auto' to search, or 'demo' / 'demo-gm' / "
                           "'demo-holden' / 'demo-bmw' for a simulated car")
    conn.add_argument("--baud", "-b", type=int, default=38400, help="Serial baud rate (default 38400)")
    conn.add_argument("--protocol", default="0", help="OBD protocol 0-9 (0 = auto-detect)")
    conn.add_argument("--debug", action="store_true", help="Show raw adapter traffic")

    s = sub.add_parser("scan", parents=[conn], help="Full scan + diagnosis report")
    s.add_argument("--json", metavar="FILE", help="Also save the report as JSON")
    s.add_argument("--discover", action="store_true",
                   help="Full search for ABS/airbag/other modules on every address (slower, about a minute)")
    s.add_argument("--no-modules", action="store_true", help="Skip the ABS & airbag check")
    s.add_argument("--no-save", action="store_true", help="Don't save this scan to the scan history")
    s.add_argument("--support-file", metavar="ZIP", help="Also save a support file (report + logs) for troubleshooting")
    s.add_argument("--ai", action="store_true", help="Add a plain-English AI explanation (needs an Anthropic API key)")
    s.add_argument("--vehicle", help="Vehicle for the AI, e.g. '2014 Ford Focus 2.0L, 120000 km'")
    s.add_argument("--symptoms", help="What you notice, e.g. 'rough idle when cold'")
    s.set_defaults(fn=cmd_scan)
    sub.add_parser("set-api-key", help="Save your Anthropic API key for AI explanations").set_defaults(
        fn=cmd_set_api_key)
    sub.add_parser("codes", parents=[conn], help="Read trouble codes only").set_defaults(fn=cmd_codes)
    s = sub.add_parser("clear", parents=[conn], help="Clear codes and turn off the check engine light")
    s.add_argument("--yes", "-y", action="store_true", help="Don't ask for confirmation")
    s.set_defaults(fn=cmd_clear)
    sub.add_parser("test-adapter", parents=[conn], help="Check what your adapter supports (no car needed)"
                   ).set_defaults(fn=cmd_test_adapter)
    sub.add_parser("check-update", help="Check whether a newer version is available").set_defaults(fn=cmd_check_update)
    s = sub.add_parser("history", help="List saved scans (no car needed)")
    s.add_argument("--limit", type=int, default=30, help="How many recent scans to show")
    s.add_argument("--clear", action="store_true", help="Delete all saved scans")
    s.set_defaults(fn=cmd_history)
    s = sub.add_parser("clear-modules", parents=[conn], help="Clear ABS, airbag and transmission codes")
    s.add_argument("--yes", "-y", action="store_true", help="Don't ask for confirmation")
    s.add_argument("--discover", action="store_true", help="Search every address for modules first")
    s.set_defaults(fn=cmd_clear_modules)
    s = sub.add_parser("live", parents=[conn], help="Live sensor dashboard")
    s.add_argument("--interval", type=float, default=1.0, help="Refresh interval in seconds")
    s.add_argument("--once", action="store_true", help="Print one snapshot and exit")
    s.add_argument("--record", metavar="CSV", help="Also record every reading to a CSV file (opens in Excel)")
    s.set_defaults(fn=cmd_live)
    s = sub.add_parser("lookup", help="Explain trouble codes (no car needed)")
    s.add_argument("codes", nargs="+")
    s.set_defaults(fn=cmd_lookup)
    s = sub.add_parser("simulate", help="Run a fake car/adapter on TCP for testing")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--sim-port", type=int, default=35000)
    s.set_defaults(fn=cmd_simulate)

    s = sub.add_parser("gui", help="Open the graphical interface (default when no command is given)")
    s.set_defaults(fn=lambda a: __import__("obd_doctor.gui", fromlist=["run"]).run())

    args = p.parse_args(argv)
    if not args.cmd:  # double-clicked / no arguments -> open the window
        from .gui import run
        run()
        return 0
    try:
        args.fn(args)
    except OBDError as e:
        print(c(f"Error: {e}", "high"), file=sys.stderr)
        return 1
    except (OSError, ConnectionError) as e:
        print(c(f"Connection error: {e}", "high"), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
