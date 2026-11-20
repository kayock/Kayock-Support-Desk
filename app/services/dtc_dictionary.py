"""
A local, offline dictionary of standardized generic OBD-II Diagnostic
Trouble Code (DTC) definitions -- the short, standardized description
each code has under the SAE J2012 / ISO 15031-6 naming convention (the
same convention every generic scan tool uses), e.g. "P0302" always means
"Cylinder 2 Misfire Detected" regardless of manufacturer.

Why local instead of an online API: as of this pass, no free, public,
no-API-key, stable HTTP service for generic DTC definitions could be
found (see README "Online Automotive Sources" for what was evaluated).
Rather than add a paid/unstable dependency, generic DTC lookup is served
from this small built-in dictionary through the same lookup_dtc()
interface an online source would use -- so it's a drop-in replacement
later if a suitable free API appears, and it has the nice side effect of
always working offline, with zero latency and no rate limit.

This is intentionally not exhaustive (~100 of the most common generic
powertrain codes plus a few representative chassis/body/network codes) --
it covers what a backyard mechanic runs into most often. An unrecognized
code still gets a category-level explanation from its first character and
digit (see lookup_dtc in automotive_sources.py) rather than nothing.
"""

GENERIC_DTC_DEFINITIONS: dict[str, str] = {
    # Fuel and air metering
    "P0100": "Mass or Volume Air Flow Circuit Malfunction",
    "P0101": "Mass or Volume Air Flow Circuit Range/Performance Problem",
    "P0102": "Mass or Volume Air Flow Circuit Low Input",
    "P0103": "Mass or Volume Air Flow Circuit High Input",
    "P0106": "Manifold Absolute Pressure/Barometric Pressure Circuit Range/Performance Problem",
    "P0107": "Manifold Absolute Pressure/Barometric Pressure Circuit Low Input",
    "P0108": "Manifold Absolute Pressure/Barometric Pressure Circuit High Input",
    "P0110": "Intake Air Temperature Circuit Malfunction",
    "P0113": "Intake Air Temperature Circuit High Input",
    "P0115": "Engine Coolant Temperature Circuit Malfunction",
    "P0116": "Engine Coolant Temperature Circuit Range/Performance Problem",
    "P0117": "Engine Coolant Temperature Circuit Low Input",
    "P0118": "Engine Coolant Temperature Circuit High Input",
    "P0120": "Throttle/Pedal Position Sensor/Switch A Circuit Malfunction",
    "P0121": "Throttle/Pedal Position Sensor/Switch A Circuit Range/Performance Problem",
    "P0125": "Insufficient Coolant Temperature for Closed Loop Fuel Control",
    "P0128": "Coolant Thermostat (Coolant Temperature Below Thermostat Regulating Temperature)",
    "P0130": "O2 Sensor Circuit Malfunction (Bank 1, Sensor 1)",
    "P0131": "O2 Sensor Circuit Low Voltage (Bank 1, Sensor 1)",
    "P0132": "O2 Sensor Circuit High Voltage (Bank 1, Sensor 1)",
    "P0133": "O2 Sensor Circuit Slow Response (Bank 1, Sensor 1)",
    "P0134": "O2 Sensor Circuit No Activity Detected (Bank 1, Sensor 1)",
    "P0135": "O2 Sensor Heater Circuit Malfunction (Bank 1, Sensor 1)",
    "P0140": "O2 Sensor Circuit No Activity Detected (Bank 1, Sensor 2)",
    "P0141": "O2 Sensor Heater Circuit Malfunction (Bank 1, Sensor 2)",
    "P0150": "O2 Sensor Circuit Malfunction (Bank 2, Sensor 1)",
    "P0155": "O2 Sensor Heater Circuit Malfunction (Bank 2, Sensor 1)",
    "P0160": "O2 Sensor Circuit No Activity Detected (Bank 2, Sensor 2)",
    "P0161": "O2 Sensor Heater Circuit Malfunction (Bank 2, Sensor 2)",
    "P0170": "Fuel Trim Malfunction (Bank 1)",
    "P0171": "System Too Lean (Bank 1)",
    "P0172": "System Too Rich (Bank 1)",
    "P0173": "Fuel Trim Malfunction (Bank 2)",
    "P0174": "System Too Lean (Bank 2)",
    "P0175": "System Too Rich (Bank 2)",
    # Injector / fuel delivery
    "P0200": "Injector Circuit Malfunction",
    "P0201": "Injector Circuit Malfunction - Cylinder 1",
    "P0202": "Injector Circuit Malfunction - Cylinder 2",
    "P0203": "Injector Circuit Malfunction - Cylinder 3",
    "P0204": "Injector Circuit Malfunction - Cylinder 4",
    "P0205": "Injector Circuit Malfunction - Cylinder 5",
    "P0206": "Injector Circuit Malfunction - Cylinder 6",
    "P0217": "Engine Overtemperature Condition",
    "P0230": "Fuel Pump Primary Circuit Malfunction",
    "P0231": "Fuel Pump Secondary Circuit Low",
    "P0232": "Fuel Pump Secondary Circuit High",
    # Ignition system / misfire
    "P0300": "Random/Multiple Cylinder Misfire Detected",
    "P0301": "Cylinder 1 Misfire Detected",
    "P0302": "Cylinder 2 Misfire Detected",
    "P0303": "Cylinder 3 Misfire Detected",
    "P0304": "Cylinder 4 Misfire Detected",
    "P0305": "Cylinder 5 Misfire Detected",
    "P0306": "Cylinder 6 Misfire Detected",
    "P0307": "Cylinder 7 Misfire Detected",
    "P0308": "Cylinder 8 Misfire Detected",
    "P0309": "Cylinder 9 Misfire Detected",
    "P0310": "Cylinder 10 Misfire Detected",
    "P0311": "Cylinder 11 Misfire Detected",
    "P0312": "Cylinder 12 Misfire Detected",
    "P0320": "Ignition/Distributor Engine Speed Input Circuit Malfunction",
    "P0325": "Knock Sensor 1 Circuit Malfunction (Bank 1 or Single Sensor)",
    "P0330": "Knock Sensor 2 Circuit Malfunction (Bank 2)",
    "P0335": "Crankshaft Position Sensor A Circuit Malfunction",
    "P0339": "Crankshaft Position Sensor A Circuit Intermittent",
    "P0340": "Camshaft Position Sensor Circuit Malfunction",
    "P0350": "Ignition Coil Primary/Secondary Circuit Malfunction",
    "P0351": "Ignition Coil A Primary/Secondary Circuit Malfunction",
    "P0352": "Ignition Coil B Primary/Secondary Circuit Malfunction",
    "P0353": "Ignition Coil C Primary/Secondary Circuit Malfunction",
    "P0354": "Ignition Coil D Primary/Secondary Circuit Malfunction",
    # Auxiliary emission controls
    "P0400": "Exhaust Gas Recirculation Flow Malfunction",
    "P0401": "Exhaust Gas Recirculation Flow Insufficient Detected",
    "P0402": "Exhaust Gas Recirculation Flow Excessive Detected",
    "P0410": "Secondary Air Injection System Malfunction",
    "P0420": "Catalyst System Efficiency Below Threshold (Bank 1)",
    "P0430": "Catalyst System Efficiency Below Threshold (Bank 2)",
    "P0440": "Evaporative Emission Control System Malfunction",
    "P0441": "Evaporative Emission Control System Incorrect Purge Flow",
    "P0442": "Evaporative Emission Control System Leak Detected (small leak)",
    "P0446": "Evaporative Emission Control System Vent Control Circuit Malfunction",
    "P0455": "Evaporative Emission Control System Leak Detected (large leak)",
    "P0456": "Evaporative Emission Control System Leak Detected (very small leak)",
    "P0460": "Fuel Level Sensor Circuit Malfunction",
    "P0461": "Fuel Level Sensor Circuit Range/Performance",
    "P0462": "Fuel Level Sensor Circuit Low Input",
    "P0463": "Fuel Level Sensor Circuit High Input",
    "P0470": "Exhaust Pressure Sensor Malfunction",
    # Vehicle speed / idle control
    "P0500": "Vehicle Speed Sensor Malfunction",
    "P0501": "Vehicle Speed Sensor Range/Performance",
    "P0505": "Idle Control System Malfunction",
    "P0506": "Idle Control System RPM Lower Than Expected",
    "P0507": "Idle Control System RPM Higher Than Expected",
    # Computer / auxiliary outputs
    "P0600": "Serial Communication Link Malfunction",
    "P0601": "Internal Control Module Memory Check Sum Error",
    "P0602": "Control Module Programming Error",
    "P0603": "Internal Control Module Keep Alive Memory (KAM) Error",
    "P0604": "Internal Control Module Random Access Memory (RAM) Error",
    "P0605": "Internal Control Module Read Only Memory (ROM) Error",
    "P0606": "PCM Processor Fault",
    # Transmission
    "P0700": "Transmission Control System Malfunction",
    "P0701": "Transmission Control System Range/Performance",
    "P0702": "Transmission Control System Electrical",
    "P0703": "Torque Converter/Brake Switch B Circuit Malfunction",
    "P0704": "Clutch Switch Input Circuit Malfunction",
    "P0705": "Transmission Range Sensor Circuit Malfunction (PRNDL Input)",
    "P0710": "Transmission Fluid Temperature Sensor Circuit Malfunction",
    "P0715": "Input/Turbine Speed Sensor Circuit Malfunction",
    "P0720": "Output Speed Sensor Circuit Malfunction",
    "P0730": "Incorrect Gear Ratio",
    "P0740": "Torque Converter Clutch Circuit Malfunction",
    "P0750": "Shift Solenoid A Malfunction",
    "P0755": "Shift Solenoid B Malfunction",
    # A representative sample of Body, Chassis, and Network codes
    "B0001": "Driver Frontal Stage 1 Deployment Control",
    "B1000": "ECU Malfunction",
    "B1200": "Climate Control Pushbutton Circuit Failure",
    "C0035": "Left Front Wheel Speed Sensor Circuit",
    "C0040": "Right Front Wheel Speed Sensor Circuit",
    "C0110": "Pump Motor Circuit Malfunction",
    "U0001": "High Speed CAN Communication Bus",
    "U0100": "Lost Communication With ECM/PCM",
    "U0101": "Lost Communication With TCM",
    "U0121": "Lost Communication With ABS Control Module",
}
