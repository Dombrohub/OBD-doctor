"""Diagnostic Trouble Code knowledge base.

Descriptions follow SAE J2012 generic codes. Manufacturer-specific codes
(P1xxx, P3000-P33xx, B1/B2, C1/C2, U1/U2) differ per brand; for those we give the
system area and recommend a brand-specific lookup.

Severity levels:
  critical - stop driving / risk of engine damage or safety issue
  high     - repair soon; can damage other parts (e.g. catalytic converter)
  medium   - drivability / emissions problem, repair when possible
  low      - minor, mostly emissions or convenience
"""

from __future__ import annotations

import re

DESCRIPTIONS = {
    "P0008": "Engine Position System Performance (Bank 1)",
    "P0010": "'A' Camshaft Position Actuator Circuit (Bank 1)",
    "P0011": "'A' Camshaft Position - Timing Over-Advanced or System Performance (Bank 1)",
    "P0012": "'A' Camshaft Position - Timing Over-Retarded (Bank 1)",
    "P0014": "'B' Camshaft Position - Timing Over-Advanced or System Performance (Bank 1)",
    "P0016": "Crankshaft Position - Camshaft Position Correlation (Bank 1 Sensor A)",
    "P0017": "Crankshaft Position - Camshaft Position Correlation (Bank 1 Sensor B)",
    "P0018": "Crankshaft Position - Camshaft Position Correlation (Bank 2 Sensor A)",
    "P0021": "'A' Camshaft Position - Timing Over-Advanced or System Performance (Bank 2)",
    "P0030": "HO2S Heater Control Circuit (Bank 1 Sensor 1)",
    "P0036": "HO2S Heater Control Circuit (Bank 1 Sensor 2)",
    "P0050": "HO2S Heater Control Circuit (Bank 2 Sensor 1)",
    "P0056": "HO2S Heater Control Circuit (Bank 2 Sensor 2)",
    "P0087": "Fuel Rail/System Pressure - Too Low",
    "P0088": "Fuel Rail/System Pressure - Too High",
    "P0089": "Fuel Pressure Regulator 1 Performance",
    "P0100": "Mass or Volume Air Flow Circuit Malfunction",
    "P0101": "Mass or Volume Air Flow Circuit Range/Performance",
    "P0102": "Mass or Volume Air Flow Circuit Low Input",
    "P0103": "Mass or Volume Air Flow Circuit High Input",
    "P0106": "Manifold Absolute Pressure/Barometric Pressure Circuit Range/Performance",
    "P0107": "Manifold Absolute Pressure/Barometric Pressure Circuit Low Input",
    "P0108": "Manifold Absolute Pressure/Barometric Pressure Circuit High Input",
    "P0110": "Intake Air Temperature Circuit Malfunction",
    "P0112": "Intake Air Temperature Circuit Low Input",
    "P0113": "Intake Air Temperature Circuit High Input",
    "P0115": "Engine Coolant Temperature Circuit Malfunction",
    "P0116": "Engine Coolant Temperature Circuit Range/Performance",
    "P0117": "Engine Coolant Temperature Circuit Low Input",
    "P0118": "Engine Coolant Temperature Circuit High Input",
    "P0120": "Throttle/Pedal Position Sensor/Switch 'A' Circuit Malfunction",
    "P0121": "Throttle/Pedal Position Sensor/Switch 'A' Circuit Range/Performance",
    "P0122": "Throttle/Pedal Position Sensor/Switch 'A' Circuit Low Input",
    "P0123": "Throttle/Pedal Position Sensor/Switch 'A' Circuit High Input",
    "P0125": "Insufficient Coolant Temperature for Closed Loop Fuel Control",
    "P0128": "Coolant Thermostat (Coolant Temperature Below Thermostat Regulating Temperature)",
    "P0130": "O2 Sensor Circuit Malfunction (Bank 1 Sensor 1)",
    "P0131": "O2 Sensor Circuit Low Voltage (Bank 1 Sensor 1)",
    "P0132": "O2 Sensor Circuit High Voltage (Bank 1 Sensor 1)",
    "P0133": "O2 Sensor Circuit Slow Response (Bank 1 Sensor 1)",
    "P0134": "O2 Sensor Circuit No Activity Detected (Bank 1 Sensor 1)",
    "P0135": "O2 Sensor Heater Circuit Malfunction (Bank 1 Sensor 1)",
    "P0136": "O2 Sensor Circuit Malfunction (Bank 1 Sensor 2)",
    "P0137": "O2 Sensor Circuit Low Voltage (Bank 1 Sensor 2)",
    "P0138": "O2 Sensor Circuit High Voltage (Bank 1 Sensor 2)",
    "P0139": "O2 Sensor Circuit Slow Response (Bank 1 Sensor 2)",
    "P0140": "O2 Sensor Circuit No Activity Detected (Bank 1 Sensor 2)",
    "P0141": "O2 Sensor Heater Circuit Malfunction (Bank 1 Sensor 2)",
    "P0150": "O2 Sensor Circuit Malfunction (Bank 2 Sensor 1)",
    "P0151": "O2 Sensor Circuit Low Voltage (Bank 2 Sensor 1)",
    "P0152": "O2 Sensor Circuit High Voltage (Bank 2 Sensor 1)",
    "P0153": "O2 Sensor Circuit Slow Response (Bank 2 Sensor 1)",
    "P0154": "O2 Sensor Circuit No Activity Detected (Bank 2 Sensor 1)",
    "P0155": "O2 Sensor Heater Circuit Malfunction (Bank 2 Sensor 1)",
    "P0156": "O2 Sensor Circuit Malfunction (Bank 2 Sensor 2)",
    "P0157": "O2 Sensor Circuit Low Voltage (Bank 2 Sensor 2)",
    "P0158": "O2 Sensor Circuit High Voltage (Bank 2 Sensor 2)",
    "P0161": "O2 Sensor Heater Circuit Malfunction (Bank 2 Sensor 2)",
    "P0171": "System Too Lean (Bank 1)",
    "P0172": "System Too Rich (Bank 1)",
    "P0174": "System Too Lean (Bank 2)",
    "P0175": "System Too Rich (Bank 2)",
    "P0191": "Fuel Rail Pressure Sensor 'A' Circuit Range/Performance",
    "P0217": "Engine Coolant Over Temperature Condition",
    "P0219": "Engine Overspeed Condition",
    "P0230": "Fuel Pump Primary Circuit Malfunction",
    "P0234": "Turbocharger/Supercharger 'A' Overboost Condition",
    "P0299": "Turbocharger/Supercharger 'A' Underboost Condition",
    "P0300": "Random/Multiple Cylinder Misfire Detected",
    "P0315": "Crankshaft Position System Variation Not Learned",
    "P0325": "Knock Sensor 1 Circuit Malfunction (Bank 1 or Single Sensor)",
    "P0327": "Knock Sensor 1 Circuit Low Input (Bank 1 or Single Sensor)",
    "P0328": "Knock Sensor 1 Circuit High Input (Bank 1 or Single Sensor)",
    "P0335": "Crankshaft Position Sensor 'A' Circuit Malfunction",
    "P0336": "Crankshaft Position Sensor 'A' Circuit Range/Performance",
    "P0340": "Camshaft Position Sensor 'A' Circuit Malfunction (Bank 1 or Single Sensor)",
    "P0341": "Camshaft Position Sensor 'A' Circuit Range/Performance (Bank 1 or Single Sensor)",
    "P0400": "Exhaust Gas Recirculation Flow Malfunction",
    "P0401": "Exhaust Gas Recirculation Flow Insufficient Detected",
    "P0402": "Exhaust Gas Recirculation Flow Excessive Detected",
    "P0404": "Exhaust Gas Recirculation Circuit Range/Performance",
    "P0410": "Secondary Air Injection System Malfunction",
    "P0420": "Catalyst System Efficiency Below Threshold (Bank 1)",
    "P0430": "Catalyst System Efficiency Below Threshold (Bank 2)",
    "P0440": "Evaporative Emission Control System Malfunction",
    "P0441": "Evaporative Emission Control System Incorrect Purge Flow",
    "P0442": "Evaporative Emission Control System Leak Detected (Small Leak)",
    "P0443": "Evaporative Emission Control System Purge Control Valve Circuit",
    "P0446": "Evaporative Emission Control System Vent Control Circuit",
    "P0449": "Evaporative Emission Control System Vent Valve/Solenoid Circuit",
    "P0455": "Evaporative Emission Control System Leak Detected (Large Leak)",
    "P0456": "Evaporative Emission Control System Leak Detected (Very Small Leak)",
    "P0457": "Evaporative Emission Control System Leak Detected (Fuel Cap Loose/Off)",
    "P0480": "Cooling Fan 1 Control Circuit",
    "P0500": "Vehicle Speed Sensor 'A' Malfunction",
    "P0505": "Idle Air Control System Malfunction",
    "P0506": "Idle Control System RPM Lower Than Expected",
    "P0507": "Idle Control System RPM Higher Than Expected",
    "P0520": "Engine Oil Pressure Sensor/Switch Circuit",
    "P0521": "Engine Oil Pressure Sensor/Switch Range/Performance",
    "P0524": "Engine Oil Pressure Too Low",
    "P0562": "System Voltage Low",
    "P0563": "System Voltage High",
    "P0571": "Brake Switch 'A' Circuit",
    "P0600": "Serial Communication Link",
    "P0601": "Internal Control Module Memory Check Sum Error",
    "P0603": "Internal Control Module Keep Alive Memory (KAM) Error",
    "P0606": "Control Module Processor",
    "P0218": "Transmission Fluid Over Temperature Condition",
    "P0700": "Transmission Control System (MIL Request)",
    "P0711": "Transmission Fluid Temperature Sensor 'A' Circuit Range/Performance",
    "P0712": "Transmission Fluid Temperature Sensor 'A' Circuit Low",
    "P0713": "Transmission Fluid Temperature Sensor 'A' Circuit High",
    "P0717": "Input/Turbine Speed Sensor 'A' Circuit No Signal",
    "P0722": "Output Speed Sensor Circuit No Signal",
    "P0731": "Gear 1 Incorrect Ratio",
    "P0732": "Gear 2 Incorrect Ratio",
    "P0733": "Gear 3 Incorrect Ratio",
    "P0734": "Gear 4 Incorrect Ratio",
    "P0735": "Gear 5 Incorrect Ratio",
    "P0736": "Reverse Incorrect Ratio",
    "P0742": "Torque Converter Clutch Circuit Stuck On",
    "P0743": "Torque Converter Clutch Circuit Electrical",
    "P0748": "Pressure Control Solenoid 'A' Electrical",
    "P0751": "Shift Solenoid 'A' Performance or Stuck Off",
    "P0752": "Shift Solenoid 'A' Stuck On",
    "P0753": "Shift Solenoid 'A' Electrical",
    "P0756": "Shift Solenoid 'B' Performance or Stuck Off",
    "P0757": "Shift Solenoid 'B' Stuck On",
    "P0758": "Shift Solenoid 'B' Electrical",
    "P0761": "Shift Solenoid 'C' Performance or Stuck Off",
    "P0868": "Transmission Fluid Pressure Low",
    "P0894": "Transmission Component Slipping",
    "P0960": "Pressure Control Solenoid 'A' Control Circuit/Open",
    "P0962": "Pressure Control Solenoid 'A' Control Circuit Low",
    "P0963": "Pressure Control Solenoid 'A' Control Circuit High",
    "P0705": "Transmission Range Sensor Circuit (PRNDL Input)",
    "P0715": "Input/Turbine Speed Sensor 'A' Circuit",
    "P0720": "Output Speed Sensor Circuit",
    "P0730": "Incorrect Gear Ratio",
    "P0740": "Torque Converter Clutch Circuit Malfunction",
    "P0741": "Torque Converter Clutch Circuit Performance or Stuck Off",
    "P0750": "Shift Solenoid 'A' Malfunction",
    "P0755": "Shift Solenoid 'B' Malfunction",
    "P0841": "Transmission Fluid Pressure Sensor/Switch 'A' Circuit Range/Performance",
    "P0A80": "Replace Hybrid Battery Pack",
    "P2096": "Post Catalyst Fuel Trim System Too Lean (Bank 1)",
    "P2097": "Post Catalyst Fuel Trim System Too Rich (Bank 1)",
    "P20EE": "SCR NOx Catalyst Efficiency Below Threshold (Bank 1)",
    "P2135": "Throttle/Pedal Position Sensor/Switch 'A'/'B' Voltage Correlation",
    "P2138": "Throttle/Pedal Position Sensor/Switch 'D'/'E' Voltage Correlation",
    "P2195": "O2 Sensor Signal Stuck Lean (Bank 1 Sensor 1)",
    "P2196": "O2 Sensor Signal Stuck Rich (Bank 1 Sensor 1)",
    "P2270": "O2 Sensor Signal Stuck Lean (Bank 1 Sensor 2)",
    "P2271": "O2 Sensor Signal Stuck Rich (Bank 1 Sensor 2)",
    "P242F": "Diesel Particulate Filter Restriction - Ash Accumulation",
    "P2463": "Diesel Particulate Filter Restriction - Soot Accumulation",
    "U0001": "High Speed CAN Communication Bus",
    "U0073": "Control Module Communication Bus 'A' Off",
    "U0100": "Lost Communication With ECM/PCM 'A'",
    "U0101": "Lost Communication With TCM",
    "U0121": "Lost Communication With Anti-Lock Brake System (ABS) Control Module",
    "U0140": "Lost Communication With Body Control Module",
    "U0155": "Lost Communication With Instrument Panel Cluster (IPC) Control Module",
    "U0401": "Invalid Data Received From ECM/PCM 'A'",
    "B0001": "Driver Frontal Stage 1 Deployment Control",
    "B0002": "Driver Frontal Stage 2 Deployment Control",
    "C0035": "Left Front Wheel Speed Sensor Circuit",
    "C0040": "Right Front Wheel Speed Sensor Circuit",
    "C0045": "Left Rear Wheel Speed Sensor Circuit",
    "C0050": "Right Rear Wheel Speed Sensor Circuit",
}

_VACUUM = "Vacuum leak (cracked hose, intake gasket, PCV valve/hose, brake booster hose)"

KNOWLEDGE = {
    "P0011": ("medium", ["Low or dirty engine oil (check level and condition first)",
                         "Faulty camshaft timing (VVT) oil control solenoid",
                         "Clogged oil passage / solenoid screen", "Stretched timing chain"],
              ["Check oil level and change oil if overdue - this fixes many VVT codes"]),
    "P0016": ("high", ["Stretched timing chain or jumped timing belt",
                       "Faulty crank or cam position sensor", "VVT solenoid / phaser fault",
                       "Damaged reluctor (tone) ring"],
              ["Do not ignore - a jumped chain/belt can destroy the engine",
               "Compare cam/crank correlation values with a scan tool; listen for chain rattle on cold start"]),
    "P0087": ("high", ["Weak fuel pump", "Clogged fuel filter", "Faulty fuel pressure regulator",
                       "Faulty high-pressure pump (direct injection)", "Fuel pressure sensor fault"],
              ["Measure fuel pressure with a gauge and compare with specification"]),
    "P0101": ("medium", ["Dirty MAF sensor", "Air leak between MAF and throttle body",
                         "Clogged air filter", "Faulty MAF sensor"],
              ["Clean the MAF with MAF-specific cleaner (never touch the wire)",
               "Inspect the intake duct for cracks after the MAF"]),
    "P0113": ("low", ["Unplugged/damaged IAT sensor connector", "Open circuit in IAT wiring",
                      "Faulty IAT sensor (often built into the MAF)"],
              ["Live IAT reading of -40 °C means an open circuit"]),
    "P0117": ("medium", ["Shorted coolant temperature sensor", "Wiring shorted to ground"], []),
    "P0118": ("medium", ["Open coolant temperature sensor / unplugged connector",
                         "Corroded connector", "Wiring open circuit"],
              ["Live coolant reading of -40 °C means an open circuit"]),
    "P0121": ("high", ["Faulty throttle position sensor", "Dirty/sticking throttle body",
                       "Wiring/connector issue"],
              ["May cause limp mode or sudden loss of power"]),
    "P0125": ("low", ["Thermostat stuck open", "Faulty coolant temperature sensor",
                      "Low coolant level"], []),
    "P0128": ("low", ["Thermostat stuck open (most common)", "Faulty coolant temperature sensor",
                      "Low coolant level", "Cooling fan running constantly"],
              ["If the temperature gauge stays low and the heater is weak, replace the thermostat"]),
    "P0133": ("low", ["Aging/contaminated upstream O2 sensor", "Exhaust leak near the sensor",
                      "Wiring issue"], ["Upstream O2 sensors are wear items (~100,000 mi / 160,000 km)"]),
    "P0171": ("medium", [_VACUUM, "Dirty or faulty MAF sensor",
                         "Low fuel pressure (weak pump, clogged filter, bad regulator)",
                         "Clogged or leaking fuel injectors", "Exhaust leak before the upstream O2 sensor"],
              ["Lean at idle but OK at 2500 rpm -> vacuum leak; lean everywhere -> fuel supply or MAF",
               "Smoke test the intake, or listen for hissing with the engine running"]),
    "P0172": ("medium", ["Leaking fuel injector(s)", "Fuel pressure too high (bad regulator)",
                         "Dirty MAF reading incorrectly", "EVAP purge valve stuck open",
                         "Clogged air filter", "Faulty coolant temperature sensor (reads cold)"], []),
    "P0217": ("critical", ["Low coolant / coolant leak", "Failed water pump", "Stuck-closed thermostat",
                           "Cooling fan not working", "Clogged radiator", "Head gasket failure"],
              ["STOP DRIVING. Let the engine cool before opening the radiator cap"]),
    "P0219": ("high", ["Engine was over-revved (missed shift / downshift)", "Transmission slipping"], []),
    "P0234": ("high", ["Stuck wastegate / VGT vanes", "Faulty boost control solenoid",
                       "Boost pressure sensor fault"], ["Avoid hard acceleration until fixed"]),
    "P0299": ("medium", ["Boost leak (split intercooler hose, loose clamp)", "Wastegate stuck open",
                         "Faulty turbocharger", "Boost control solenoid / vacuum line",
                         "Clogged air filter or DPF (diesel)"],
              ["Inspect all intercooler pipes and clamps for oil residue/splits"]),
    "P0300": ("high", ["Worn spark plugs", "Failing ignition coil(s)", _VACUUM,
                       "Low fuel pressure / dirty injectors", "Bad fuel", "Low compression",
                       "Timing chain/belt out of position"],
              ["A FLASHING check engine light means severe misfire - avoid driving, it can melt the catalytic converter",
               "Check fuel trims: high positive trims point to a lean (vacuum/fuel) cause"]),
    "P0325": ("medium", ["Faulty knock sensor", "Damaged wiring (often under the intake manifold)",
                         "Loose sensor (wrong torque)"], []),
    "P0335": ("high", ["Faulty crankshaft position sensor", "Damaged wiring/connector",
                       "Damaged reluctor ring"],
              ["Can cause stalling or no-start. Engine may not start at all if the signal is lost"]),
    "P0340": ("high", ["Faulty camshaft position sensor", "Wiring/connector damage",
                       "Timing chain/belt slipped"], ["Can cause hard starting or stalling"]),
    "P0401": ("medium", ["Clogged EGR passages / carbon build-up", "Faulty EGR valve",
                         "Faulty EGR position sensor or DPFE sensor", "Leaking vacuum line to EGR"], []),
    "P0420": ("medium", ["Worn-out catalytic converter", "Faulty downstream (or upstream) O2 sensor",
                         "Exhaust leak near the O2 sensors",
                         "Engine problem damaging the cat (misfire, burning oil, running rich)"],
              ["Fix any misfire / fuel trim problems FIRST, then clear and re-test",
               "Downstream O2 voltage switching as fast as upstream = catalyst no longer storing oxygen"]),
    "P0442": ("low", ["Loose or worn gas cap seal", "Cracked EVAP hose", "Leaking purge or vent valve",
                      "Leaking charcoal canister"], ["Replace the gas cap first - cheapest and most common fix"]),
    "P0455": ("low", ["Gas cap missing/not tightened", "Disconnected or split EVAP hose",
                      "Purge valve stuck", "Vent valve stuck open", "Cracked charcoal canister"],
              ["Check gas cap, then smoke-test the EVAP system"]),
    "P0456": ("low", ["Gas cap seal", "Tiny crack in EVAP hose", "Purge/vent valve seal"], []),
    "P0457": ("low", ["Gas cap loose, missing or not sealing"], ["Tighten the cap until it clicks; code clears after several drive cycles"]),
    "P0446": ("low", ["Faulty canister vent valve", "Blocked vent filter (dirt / spiders)", "Wiring fault"], []),
    "P0505": ("medium", ["Dirty throttle body / idle air control valve", _VACUUM, "Faulty IAC valve"],
              ["Clean the throttle body and perform an idle relearn"]),
    "P0506": ("low", ["Dirty throttle body", "Restricted intake / air filter", "Faulty IAC"], []),
    "P0507": ("medium", [_VACUUM, "Dirty or faulty idle air control valve / throttle body",
                         "Throttle not closing fully"], []),
    "P0520": ("high", ["Faulty oil pressure sensor/switch", "Wiring fault", "Actual low oil pressure"],
              ["Check the oil level immediately"]),
    "P0524": ("critical", ["Low oil level", "Worn oil pump", "Clogged oil pickup", "Worn bearings",
                           "Wrong oil viscosity"],
              ["STOP THE ENGINE. Check oil level - driving with low oil pressure destroys engines"]),
    "P0562": ("high", ["Weak battery", "Failing alternator", "Loose/corroded battery terminals",
                       "Bad ground strap", "Slipping drive belt"],
              ["Battery should be ~12.6 V engine off and 13.5-14.7 V running"]),
    "P0563": ("high", ["Faulty alternator voltage regulator", "Bad battery sense wire"], []),
    "P0700": ("medium", ["The transmission computer (TCM) has its own fault code"],
              ["Read the TCM codes (listed in this report if the TCM answers)"]),
    "P0730": ("high", ["Low or burnt transmission fluid", "Slipping clutch packs",
                       "Shift solenoid fault", "Internal transmission wear"],
              ["Check transmission fluid level and smell (burnt = internal damage)"]),
    "P0741": ("medium", ["Low/dirty transmission fluid", "Faulty torque converter clutch solenoid",
                         "Worn torque converter"], []),
    "P2096": ("medium", ["Exhaust leak", "Upstream lean condition (vacuum leak, MAF)",
                         "Faulty downstream O2 sensor"], []),
    "P2097": ("medium", ["Leaking injectors", "Faulty upstream O2 sensor", "Failed catalyst"], []),
    "P2135": ("high", ["Faulty electronic throttle body", "Faulty accelerator pedal sensor",
                       "Corroded throttle connector"], ["Usually triggers limp mode (reduced power)"]),
    "P2463": ("high", ["DPF soot loaded from short trips", "Failed regeneration",
                       "Faulty DPF pressure sensor"],
              ["A 20-30 min highway drive may trigger regeneration; otherwise a forced regen is needed"]),
    "U0100": ("high", ["Loose/corroded ECM connector", "CAN bus wiring damage",
                       "Low battery voltage", "ECM power or ground fault", "Failed ECM"],
              ["Check battery and ECM fuses first"]),
    "U0001": ("high", ["CAN bus wiring short/open", "Faulty module pulling the bus down",
                       "Water in a connector"], []),
}

_WHEEL_TIPS = ["Check the sensor wiring and connector at that wheel first - they get damaged by road debris and suspension work",
               "A wheel bearing replacement or a cracked/rusted tone ring often causes this",
               "Normal brakes still work; ABS and traction/stability control are switched off until it's fixed"]
for _code, _wheel in (("C0035", "left front"), ("C0040", "right front"), ("C0045", "left rear"), ("C0050", "right rear")):
    KNOWLEDGE[_code] = ("high", [f"Faulty {_wheel} wheel speed sensor", f"Damaged wiring or connector at the {_wheel} wheel",
                                 f"Dirty, cracked or rusted tone ring (reluctor) on the {_wheel} hub",
                                 f"Worn {_wheel} wheel bearing (on cars where the sensor reads the bearing)"], _WHEEL_TIPS)
_AIRBAG_TIPS = ["Airbag systems can injure you if handled wrongly - have this repaired by a professional",
                "Never probe airbag connectors with a test light or multimeter"]
for _code in ("B0001", "B0002"):
    KNOWLEDGE[_code] = ("high", ["Failed clockspring (spiral cable) behind the steering wheel - the most common cause",
                                 "Loose or corroded driver airbag connector",
                                 "Faulty driver airbag module (replace only with the correct part)"],
                        _AIRBAG_TIPS + ["A clockspring often fails together with the horn or steering-wheel buttons"])

# Failure type byte (the "-XX" suffix on ABS/airbag codes read with UDS), SAE J2012
FAILURE_TYPES = {
    "01": "general electrical failure", "02": "general signal failure", "04": "system internal failure",
    "11": "circuit short to ground", "12": "circuit short to battery", "13": "circuit open",
    "14": "circuit short to ground or open", "15": "circuit short to battery or open",
    "16": "circuit voltage below threshold", "17": "circuit voltage above threshold",
    "19": "circuit current above threshold", "1A": "circuit resistance below threshold",
    "1B": "circuit resistance above threshold", "1C": "circuit voltage out of range",
    "1D": "circuit current out of range", "1E": "circuit resistance out of range",
    "23": "signal stuck low", "24": "signal stuck high", "26": "signal rate of change below threshold",
    "28": "signal bias level out of range", "29": "signal invalid", "2F": "signal erratic",
    "31": "no signal", "36": "signal frequency too low", "37": "signal frequency too high",
    "4B": "over temperature", "54": "missing calibration", "55": "not configured",
    "62": "signal compare failure", "64": "signal plausibility failure", "71": "actuator stuck",
    "72": "actuator stuck open", "73": "actuator stuck closed", "87": "missing message",
    "92": "performance or incorrect operation", "96": "component internal failure",
}

_FLUID = "Check the transmission fluid level and condition (dark, burnt-smelling or gritty fluid points to internal wear)"
_TRANS_CAUSES = ["Low, old or burnt transmission fluid", "Faulty shift or pressure-control solenoid / valve body",
                 "Wiring or connector fault at the transmission", "Internal wear (clutches, bands, torque converter)",
                 "Transmission computer (TCM) software or fault"]
for _code in ("P0731", "P0732", "P0733", "P0734", "P0735", "P0736", "P0894", "P0730"):
    KNOWLEDGE[_code] = ("high", ["Low or burnt transmission fluid", "Worn clutch packs or bands (slipping)",
                                 "Faulty shift solenoid or valve body", "Low line pressure (pump, filter, regulator)",
                                 "Speed sensor giving a wrong reading"],
                        [_FLUID, "Avoid towing and hard acceleration - slipping quickly causes more damage"])
for _code in ("P0751", "P0752", "P0753", "P0756", "P0757", "P0758", "P0761", "P0750", "P0755",
              "P0748", "P0960", "P0962", "P0963"):
    KNOWLEDGE[_code] = ("medium", ["Faulty solenoid (often part of the valve body or solenoid pack)",
                                   "Damaged wiring or a corroded transmission connector", "Low or dirty fluid",
                                   "TCM output fault"], [_FLUID, "The transmission may stay in one gear (limp mode)"])
for _code in ("P0715", "P0717", "P0720", "P0722"):
    KNOWLEDGE[_code] = ("medium", ["Faulty speed sensor", "Damaged wiring or connector at the sensor",
                                   "Metal debris on a magnetic sensor tip"],
                        ["Can cause harsh or wrong shifts, limp mode, and sometimes a dead speedometer"])
for _code in ("P0711", "P0712", "P0713"):
    KNOWLEDGE[_code] = ("low", ["Faulty transmission fluid temperature sensor", "Wiring or connector fault",
                                "Fluid actually running too hot or cold"], [_FLUID])
KNOWLEDGE["P0218"] = ("high", ["Towing or heavy load in hot weather", "Low fluid", "Blocked transmission cooler",
                               "Slipping clutches generating heat"],
                      ["Stop and let the transmission cool; continued overheating destroys transmissions", _FLUID])
KNOWLEDGE["P0868"] = ("high", ["Low fluid level", "Clogged filter", "Worn pump", "Faulty pressure sensor or regulator"],
                      [_FLUID, "Low pressure causes slipping - avoid driving far until checked"])

_O2_HEATER = {"P0030", "P0036", "P0050", "P0056", "P0135", "P0141", "P0155", "P0161"}

_SYSTEMS_P0 = {
    "0": "Fuel & air metering / auxiliary emission controls", "1": "Fuel & air metering",
    "2": "Fuel & air metering (injector circuit)", "3": "Ignition system or misfire",
    "4": "Auxiliary emission controls", "5": "Vehicle speed, idle control & auxiliary inputs",
    "6": "Computer & output circuits", "7": "Transmission", "8": "Transmission",
    "9": "Transmission", "A": "Hybrid propulsion", "B": "Hybrid propulsion", "C": "Hybrid propulsion",
}
_LETTER = {"P": "Powertrain", "C": "Chassis", "B": "Body", "U": "Network / Communication"}


def is_valid(code: str) -> bool:
    return bool(re.fullmatch(r"[PCBU][0-3][0-9A-F]{3}(-[0-9A-F]{2})?", code.upper().strip()))


def lookup(code: str) -> dict:
    code = code.upper().strip()
    if re.fullmatch(r"BMW-[0-9A-F]{4,6}", code):
        return {"code": code, "description": f"BMW fault code {code[4:]}",
                "severity": "high", "causes": [], "system": "BMW module", "manufacturer_specific": True,
                "tips": ["BMW codes mean different things in different modules and models",
                         f"Look up '{code[4:]}' together with the module name (shown under Found in) and your model",
                         "BMW-capable scan tools or BMW's ISTA software show the full description and test plan"]}
    if re.fullmatch(r"[PCBU][0-3][0-9A-F]{3}-[0-9A-F]{2}", code):
        base, ftb = code.split("-")
        info = lookup(base)
        info["code"] = code
        info["failure_type"] = FAILURE_TYPES.get(ftb, f"failure type {ftb}")
        info["description"] = f"{info['description']} - {info['failure_type']}"
        return info
    info = {"code": code, "description": None, "severity": "medium",
            "causes": [], "tips": [], "system": _LETTER.get(code[:1], "Unknown"),
            "manufacturer_specific": False}
    if not is_valid(code):
        info["description"] = "Invalid code format"
        return info

    if code[0] == "P" and code[1] == "0":
        info["system"] = _SYSTEMS_P0.get(code[2], info["system"])

    # Generated families
    if code[:3] == "P03" and code[3:].isdigit() and 1 <= int(code[3:]) <= 12:
        cyl = int(code[3:])
        info.update(description=f"Cylinder {cyl} Misfire Detected", severity="high",
                    causes=[f"Worn/fouled spark plug in cylinder {cyl}",
                            f"Faulty ignition coil on cylinder {cyl}",
                            f"Clogged or failed fuel injector on cylinder {cyl}",
                            "Vacuum leak near that cylinder's intake runner",
                            "Low compression (valve, rings, head gasket)"],
                    tips=[f"Swap the cylinder {cyl} coil with a neighbor, clear codes, and see if the misfire moves",
                          "If it moves, replace the coil; if not, try the same with the spark plug, then the injector"])
    elif code[:3] == "P02" and code[3:].isdigit() and 1 <= int(code[3:]) <= 12:
        cyl = int(code[3:])
        info.update(description=f"Injector Circuit/Open - Cylinder {cyl}", severity="high",
                    causes=[f"Unplugged/damaged injector connector cylinder {cyl}",
                            f"Failed injector coil cylinder {cyl}", "Wiring open/short", "ECM driver fault"],
                    tips=["Measure injector resistance and compare with a neighbor"])
    elif code[:3] == "P03" and code[3:].isdigit() and 51 <= int(code[3:]) <= 62:
        cyl = int(code[3:]) - 50
        info.update(description=f"Ignition Coil {'ABCDEFGHIJKL'[cyl - 1]} Primary/Secondary Circuit (cylinder {cyl})",
                    severity="high",
                    causes=[f"Failed ignition coil on cylinder {cyl}", "Damaged coil connector/wiring",
                            "ECM coil driver fault"], tips=[])
    elif code in _O2_HEATER:
        info.update(severity="low",
                    causes=["Failed heater element inside the O2 sensor (most common - replace sensor)",
                            "Blown O2 heater fuse", "Damaged wiring/connector near the exhaust"],
                    tips=["Measure heater resistance across the sensor's heater pins (usually 2-20 ohm)"])
    elif code[:3] == "P01" and code[3:].isdigit() and 30 <= int(code[3:]) <= 67:
        info.update(severity="low",
                    causes=["Faulty oxygen (O2) sensor", "Exhaust leak near the sensor",
                            "Wiring/connector damage", "Engine running too rich/lean"], tips=[])

    if code in KNOWLEDGE:
        sev, causes, tips = KNOWLEDGE[code]
        info.update(severity=sev, causes=causes, tips=tips)
    if code == "P0174":
        info.update(severity="medium", causes=[c.replace("upstream", "bank 2 upstream") for c in KNOWLEDGE["P0171"][1]],
                    tips=KNOWLEDGE["P0171"][2])
    if code == "P0175":
        info.update(severity="medium", causes=KNOWLEDGE["P0172"][1], tips=KNOWLEDGE["P0172"][2])
    if code == "P0430":
        info.update(severity="medium", causes=KNOWLEDGE["P0420"][1], tips=KNOWLEDGE["P0420"][2])

    if not info["description"]:
        info["description"] = DESCRIPTIONS.get(code)

    if code[0] == "P":
        mfr = code[1] == "1" or (code[1] == "3" and code[2] in "0123")
    else:
        mfr = code[1] in "12"
    if mfr:
        info["manufacturer_specific"] = True
        info["description"] = info["description"] or "Manufacturer-specific code"
        info["tips"] = info["tips"] + [f"Look up '{code}' together with your car's make/model/year"]
    if not info["causes"] and code[:3] in ("P07", "P08", "P09"):
        info["causes"] = _TRANS_CAUSES
        info["tips"] = info["tips"] or [_FLUID]
    if not info["description"] and code.startswith("B00"):
        info.update(description="Restraint system (airbag / seat belt) fault", severity="high",
                    causes=info["causes"] or ["Airbag, seat belt pretensioner or seat sensor circuit fault",
                                              "Connector under a front seat disturbed or corroded",
                                              "Clockspring (for driver airbag circuits)"],
                    tips=info["tips"] or _AIRBAG_TIPS)
    if not info["description"] and code.startswith("C0"):
        info.update(description="Chassis fault (brakes / ABS / stability control / steering / suspension)",
                    severity="high")
    if not info["description"]:
        info["description"] = f"Generic {info['system'].lower()} fault (not in local database)"
        info["tips"] = info["tips"] + [f"Look up '{code}' in a service manual for full details"]
    if not info["causes"] and code[0] == "U":
        info["causes"] = ["Loose/corroded connector at the module", "CAN bus wiring fault",
                          "Low battery voltage", "Failed module"]
    return info
