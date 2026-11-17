# OBD EFT

*Formerly called OBD Doctor. Installing OBD EFT replaces the old app and keeps your settings and history.*

**[Download the latest version for Windows](https://github.com/Dombrohub/obd-doctor/releases/latest)** - run `OBDEFT-Setup.exe`.

Reads your car's computer through an **ELM327 OBD-II adapter** and explains what's wrong.

## What it does
- Reads **stored, pending and permanent** trouble codes from every module that answers (engine, transmission, ...)
- Reads the **freeze frame** (sensor readings at the moment the fault happened)
- Reads **live sensor data**: RPM, coolant temp, fuel trims, MAF, O2 sensors, battery voltage, and more
- Checks **emissions readiness monitors** (will it pass inspection?)
- **Diagnosis engine**: puts the codes and sensor data together to find the root cause.
  For example, P0171 + P0174 + high fuel trims points to a vacuum leak, and P0420 with a misfire
  means fixing the misfire before replacing the catalytic converter
- Explains ~150 codes with likely causes and repair tips (works offline)
- Clears codes and turns off the check engine light
- Includes a built-in **simulated car** for testing without a vehicle

## Hardware
Any ELM327 adapter (about $10-30):
| Adapter type | `--port` value |
|---|---|
| USB | `COM3` (Windows) / `/dev/ttyUSB0` (Linux) / `/dev/tty.usbserial-xxx` (Mac) |
| Bluetooth | Pair it first (PIN usually `1234` or `0000`), then use its COM port / `/dev/rfcomm0` |
| Wi-Fi | Join the adapter's Wi-Fi network, then `192.168.0.10:35000` |

The OBD-II port is under the dashboard on the driver's side. It's on all US cars from 1996 and later,
EU petrol cars from 2001, and EU diesel cars from 2004.

## ABS, airbag and transmission codes
Standard OBD-II only covers the engine and emissions systems. OBD EFT also asks the **ABS**, **airbag
(SRS)** and **transmission (TCM)** computers directly, using the manufacturer-level diagnostic protocol (UDS, ISO 14229, with a
KWP2000 fallback). This works on most CAN-bus cars, roughly 2008 and newer.

- Every scan includes a **quick check** of the ABS and airbag modules, which takes a few seconds. The VIN
  identifies the brand, so only that brand's addresses are asked (all of them if the brand is unknown):
  - **Ford/Mazda/Lincoln, Toyota/Lexus/Subaru, Hyundai/Kia, VW/Audi/Skoda/Seat, Nissan/Infiniti:**
    standard UDS
  - **GM (most 2005-2019 vehicles, GMLAN):** EBCM (ABS) and SDM (airbag), read with GM's own trouble-code
    request. 2020+ GM uses standard UDS, so use the full module search for those
  - **Holden** uses the same GM method: VE/VF Commodore, Caprice and Ute (VIN `6G1`), the Korean-built
    Cruze, Captiva, Barina Spark, Trax and Malibu (`KL`), and the Opel-built Astra, Cascada and ZB Commodore
    (`W0L`/`W0V`). Holdens with VINs the app doesn't recognize, such as the Thai-built Colorado RG, are found
    through the try-every-brand fallback. Not supported for ABS/airbag: pre-2006 Commodores (VT-VZ, pre-CAN)
    and the Isuzu-built Rodeo / Colorado RA-RC. Engine codes still work on those
  - **BMW / MINI (E-series D-CAN from about 2007, F-series):** DSC (ABS) and ACSM (airbag), using CAN
    extended addressing. This needs an adapter that supports `ATCEA` (genuine ELM327 v1.4+, OBDLink and
    similar); the app says so if yours doesn't. BMW codes are shown in BMW's own format (e.g. `BMW-480A20`).
    G-series (2017+) needs an Ethernet connection, which ELM327 adapters can't do
- If nothing answers, run the **full module search** (the ABS & airbag tab, or `scan --discover`). It probes
  every diagnostic address and reads any module that answers, which takes about a minute.
- **Transmissions:** most brands' transmission computers are asked at the standard address with UDS; GM and
  Holden via GMLAN (TCM), BMW via its EGS module. This finds shifting, slipping, solenoid, speed-sensor,
  pressure and overheating faults that the standard engine scan often misses, and the diagnosis explains them.
- Codes look like `C0035-1C`. The suffix is the failure type, here "circuit voltage out of range".
- **Clear ABS/airbag codes** is on the ABS & airbag tab, or use the `clear-modules` command. Airbag modules
  that stored crash data can't be cleared this way.
- **Limits:** Mercedes and most Chrysler/Stellantis vehicles aren't supported, and neither are older
  non-CAN cars. The GM and BMW support is built from published protocol knowledge and tested on the simulator
  (`demo-gm`, `demo-holden`, `demo-bmw`), not yet on real vehicles. Module labels come from the module's own name when it
  reports one; otherwise they're a best guess from the address.

## Self-tests, computer information and drive cycles
- **Self-test results (OBD Mode 6)**, on the Vehicle & tests tab and in the report: the car's own test results
  for catalysts, O2 sensors, EVAP leaks, EGR and more, each with its pass/fail limits. A failed test is flagged
  as an early warning, often before a trouble code is set.
- **Misfire counts per cylinder** from the engine computer's misfire counter, which pinpoint the cylinder to
  check.
- **Computer information (OBD Mode 9):** each computer's name, calibration (software) ID and checksum. Useful
  for checking whether a software update has been applied.
- **More live data:** odometer (newer cars), fuel type, OBD standard, fuel rate, and time since codes were
  cleared / with the check engine light on.
- **Tools → Drive cycle helper:** watches the readiness monitors complete live while you follow a general
  drive cycle.
- **Tools → Relearn & reset guide:** what to do after a battery change, throttle body cleaning, clearing
  codes or a tyre rotation, and which jobs need a workshop.

## Trouble-code database
OBD EFT includes **[obdex](https://github.com/foerbsnavi/obdex)**, an open database of all 9,533 generic OBD-II
trouble codes (engine, transmission, chassis/ABS, body/airbag and network). For each code it gives a reviewed
title, what the car's computer checks, likely causes ranked by how common they are, symptoms, and a rough
repair estimate. obdex's data is public domain (CC0-1.0); its licence is in `obd_doctor/data/OBDEX-LICENSE.txt`.
Refresh the bundled copy with `python tools/update_obdex.py`. Manufacturer-specific codes (P1xxx, B1xxx and so
on) aren't in obdex; for those the app says the meaning depends on the make.

## What OBD EFT doesn't do
OBD EFT only reads from the car (apart from clearing codes). It does not do service resets, actuation tests,
module or injector coding, ECU programming, or key/immobiliser programming. Those write to the car's computers
using manufacturer-specific, often security-locked procedures that aren't publicly available. A mistake, or
a Bluetooth adapter dropping out mid-write, can permanently disable a module, including ABS or airbag
computers. Use a brand-specific or professional bi-directional scan tool, or a workshop, for those jobs.

## Tools
- **Adapter self-test** (**Tools → Test adapter…**, or the `test-adapter` command): checks what your adapter
  can do, with no car needed. It identifies clones (there was never a genuine "v1.5" chip) and STN-based
  adapters such as OBDLink, and tests every command OBD EFT uses. For each missing one, it says which
  feature is affected, e.g. BMW ABS/airbag.
- **Live data recording**: on the Live data tab, click **● Record**, drive, click **Stop recording**, then
  **Export CSV…** and open the file in Excel. On the command line: `live --record drive.csv`.
- **Emissions inspection check**: "Likely to PASS / FAIL" on the Vehicle & readiness tab and in the report,
  using the US EPA guideline: the check engine light must be off, and at most 2 readiness monitors may be
  not ready for 1996-2000 models, or 1 for 2001 and newer. Local rules vary.
- **VIN decoding**: the make, model year and country, shown in the report and given to the AI if you didn't
  type in your vehicle.
- **Shortcuts** (**Tools → Create desktop & Start Menu shortcuts**): useful for the portable version.

## Automatic updates
Releases are published at https://github.com/Dombrohub/obd-doctor/releases (set in `obd_doctor/update.py`).
- At startup, at most once a day, OBD EFT checks GitHub for a newer release. If there is one, a banner
  offers **Update now**, **What's new** and **Later**. You can turn this off under **Help → Check for updates
  automatically**, and check on demand with **Help → Check for updates…** (or the `check-update` command).
- **Update now** downloads the installer, verifies it against the SHA-256 checksum published with the
  release (a damaged or tampered download is deleted), runs it, and closes the app. Settings and history are
  kept. The portable version opens the download page instead.
- **Publishing a version:** bump the version numbers (`obd_doctor/__init__.py`, `installer/obd_doctor.nsi`,
  `version_info.txt`) and push to `main`. The GitHub Actions workflow builds and tests the app on Windows,
  then publishes a release with the installer, portable zip and checksum. Installed copies pick it up
  automatically. Pushes without a version change are built and tested, but not released.


## Getting help: support file
If something doesn't work, click **Save support file…** in the toolbar (or add `--support-file help.zip` to a
`scan` command) and send the .zip to whoever is helping you. It contains the scan report (with any AI
explanation), the scan data, the app log, the most recent raw adapter messages, and system details (app
version, Windows version, adapter, serial ports). It also works after a failed connection.

Privacy: the API key is never included, and the VIN is partly masked (make/model/year kept, serial number
hidden). Adapter messages are recorded in memory only, and nothing is sent anywhere automatically.

## Scan history
Every scan is saved on your computer (never uploaded) so you can see what changed:
- **New** codes, codes that are **gone**, and codes that **came back** after going away. A code that comes
  back means the fault is still there or is intermittent, and the diagnosis flags it as high priority.
- A **long-term fuel trim chart** across scans, plus check engine light, codes and battery voltage for each scan.
- The changes are included in the diagnosis and the AI explanation.

Use the **History** tab, or `python -m obd_doctor history` on the command line. To skip saving, untick the box
on the History tab or use `scan --no-save`. Scans are stored in `%APPDATA%\OBDEFT\history`
(`~/.config/obd_eft/history` elsewhere); the newest 300 are kept.

## AI explanations (optional)
After a scan, OBD EFT can ask Anthropic's Claude to explain the results in plain English:
- what's wrong
- how urgent it is ("Safe to drive" / "Drive gently and get it fixed soon" / "Stop driving")
- the most likely cause, with the codes and readings that point to it
- what to check first, marked DIY or Shop, with rough cost estimates
- a note to show your mechanic

You can then ask follow-up questions.

- **GUI:** the **AI explanation** tab. Optionally enter your vehicle and the symptoms you notice, then
  click **Explain with AI**.
- **Command line:** `python -m obd_doctor scan --port COM3 --ai --vehicle "2014 Ford Focus 2.0L" --symptoms "rough idle"`

You need your own Anthropic API key from console.anthropic.com. Each explanation costs a few cents.
Enter the key in **AI settings** (or run `python -m obd_doctor set-api-key`), or set the
`ANTHROPIC_API_KEY` environment variable. The key is stored in your user profile:
`%APPDATA%\OBDEFT\config.json` on Windows, `~/.config/obd_eft/config.json` elsewhere.

**What is sent:** trouble codes, freeze frame, live sensor readings, readiness monitors, the
rule-based findings, and the vehicle and symptoms you type. **The VIN is never sent.**
Uses the `claude-opus-5-5` model; the settings are at the top of `obd_doctor/ai.py`.
AI answers can be wrong, so double-check before buying parts.

## Windows installer
Run `OBDEFT-Setup.exe`. It installs to `C:\Program Files\OBD EFT`, adds Start Menu shortcuts
(app, command prompt, uninstall) and an optional desktop shortcut, and registers in
**Settings > Apps** so it can be uninstalled normally.
Silent install: `OBDEFT-Setup.exe /S` (optional `/D=C:\Path`, which must be last and unquoted).

To rebuild it, double-click `build_windows.bat` (needs Python, plus [NSIS](https://nsis.sourceforge.io)
for the installer), or run the included GitHub Actions workflow.
To change the version number, edit `APP_VERSION` in `installer/obd_doctor.nsi`.

## Graphical interface
```bash
python -m obd_doctor            # opens the window (same as: python -m obd_doctor gui)
```
Pick the adapter (or `demo`) and click **Connect**. A full scan runs automatically. Tabs:
- **Diagnosis**: color-coded findings with likely causes and what to do
- **Trouble codes**: every code with severity, which module reported it, and an explanation
- **Live data**: sensor tiles with mini graphs. Out-of-range values turn red
- **Freeze frame**: sensor readings at the moment the fault happened
- **Vehicle & readiness**: VIN, protocol, check engine light, and emissions readiness monitors
- **Log**: connection log (tick "Log raw traffic" to troubleshoot adapters)

There are also buttons to **Clear codes**, **Save report** (.txt or .json), and **Look up** any code.
The GUI uses Tkinter, which comes with Python on Windows and macOS. On Linux, install it with
`sudo apt install python3-tk`.

## Command line
```bash
pip install -r requirements.txt               # only needed for USB/Bluetooth

python -m obd_doctor scan --port demo         # try it on the simulated car
python -m obd_doctor scan --port COM3         # full scan + diagnosis
python -m obd_doctor scan --port auto --json report.json
python -m obd_doctor live --port 192.168.0.10:35000
python -m obd_doctor codes --port /dev/ttyUSB0
python -m obd_doctor clear --port COM3
python -m obd_doctor lookup P0420 P0301       # no car needed
```
Run commands from the repository folder (the one that contains `obd_doctor/`).
Turn the ignition ON (or start the engine) before scanning. If it won't connect, try `--protocol 6`
(used by most cars from 2008 on) and `--debug` to see the raw adapter traffic.

## Limits
Generic OBD-II only covers emissions-related data. ABS, airbag, body and other manufacturer-specific
modules usually need brand-specific tools. This software gives guidance, not a guarantee. Have
safety-critical problems checked by a mechanic.
