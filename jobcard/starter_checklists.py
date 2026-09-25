"""Starter PPM checklists for common hospital equipment.

A starting point, copied onto an equipment description from Checklist
Settings and then edited. The steps follow common biomedical PPM practice;
limits vary by manufacturer, so "Expected" says "per manufacturer spec" where
the service manual decides. Review each against the device's service manual
before use.

Each step: (task, how to do it, expected result, answer type, required)
"""

CHECK, PASS_FAIL, VALUE = 'check', 'pass_fail', 'value'

_SAFETY = ("Electrical safety test (IEC 62353)",
           "Protective earth resistance, earth and patient leakage with a safety analyser.",
           "Within IEC 62353 limits for the device class", PASS_FAIL, True)
_CLEAN = ("Clean and disinfect",
          "Wipe housing, cables and accessories with a hospital-approved disinfectant.", "", CHECK, True)
_LABEL = ("Update PPM label",
          "Fill in date done, next due date and initials on the device label.", "", CHECK, True)

STARTERS = {
    'patient_monitor': {
        'label': 'Patient monitor — PPM',
        'task_type': 'PPM',
        'instructions': ("Disconnect from the patient and take the monitor out of clinical use first.\n"
                         "Tools: NIBP simulator/manometer, ECG and SpO2 simulators, electrical safety analyser.\n"
                         "Have a spare NIBP cuff and hose of the correct size to hand."),
        'items': [
            ("Visual inspection of housing, mounting and display",
             "Look for cracks, loose mounting, missing knobs, damaged screen.", "No damage", CHECK, True),
            ("Inspect power cord and plug", "Check for cuts, exposed wires and loose pins.", "Intact", CHECK, True),
            ("Inspect NIBP cuff and hose; replace the cuff if worn",
             "Check the bladder for leaks and the Velcro for wear. Replace a cracked, leaking or worn cuff and "
             "note in the Note field which cuff (size) was fitted.", "Cuff and hose intact or replaced", CHECK, True),
            ("NIBP leak test", "Run the monitor's leak test or pressurise to 250 mmHg with the simulator.",
             "Leak within manufacturer spec", PASS_FAIL, True),
            ("NIBP pressure accuracy", "Compare against the reference manometer at 0, 50, 150 and 250 mmHg.",
             "Within ±3 mmHg (or manufacturer spec)", VALUE, True),
            ("Inspect ECG leads and trunk cable", "Check insulation, snaps/clips and connector pins.",
             "Intact; replace damaged leads", CHECK, True),
            ("ECG heart-rate check with simulator", "Simulate 60 and 120 bpm; read the displayed heart rate.",
             "Within ±1 bpm or manufacturer spec", VALUE, True),
            ("Inspect SpO2 sensor and extension cable", "Check the sensor window, cable and connector.",
             "Intact; replace if damaged", CHECK, True),
            ("SpO2 accuracy with simulator", "Simulate 97% and 85% saturation.",
             "Within ±2% or manufacturer spec", VALUE, True),
            ("Temperature probe check", "If fitted, compare against a reference or simulator.",
             "Within manufacturer spec", PASS_FAIL, False),
            ("Alarm test", "Trigger high/low HR, SpO2 and NIBP alarms; check sound and visual indicators.",
             "All alarms audible and visible", PASS_FAIL, True),
            ("Battery test", "Run on battery for 15 minutes; check the charge indicator.",
             "Runs on battery; replace if it fails", PASS_FAIL, True),
            _SAFETY, _CLEAN, _LABEL,
        ],
    },
    'infusion_pump': {
        'label': 'Infusion pump — PPM',
        'task_type': 'PPM',
        'instructions': ("Remove from clinical use; make sure no infusion set is loaded from a patient.\n"
                         "Tools: infusion pump analyser, test set, electrical safety analyser."),
        'items': [
            ("Visual inspection of housing, door, pole clamp and keypad",
             "Check the door latch closes firmly, the clamp holds and all keys respond.", "No damage", CHECK, True),
            ("Inspect power cord and plug", "Check for cuts, exposed wires and loose pins.", "Intact", CHECK, True),
            ("Flow rate accuracy", "Measure with the analyser at 25 mL/h and 100 mL/h for the set time.",
             "Within ±5% or manufacturer spec", VALUE, True),
            ("Occlusion alarm (downstream)", "Clamp the line downstream; note the pressure at alarm.",
             "Alarms within manufacturer spec", VALUE, True),
            ("Air-in-line alarm", "Introduce an air bubble above the detector.", "Alarms and stops", PASS_FAIL, True),
            ("Door-open and end-of-infusion alarms", "Open the door during infusion; let a small volume complete.",
             "Both alarm", PASS_FAIL, True),
            ("Battery test", "Run on battery at 100 mL/h for 15 minutes.", "Runs on battery", PASS_FAIL, True),
            ("Record software / drug library version", "Note the version shown at start-up.", "", VALUE, False),
            _SAFETY, _CLEAN, _LABEL,
        ],
    },
    'defibrillator': {
        'label': 'Defibrillator — PPM',
        'task_type': 'PPM',
        'instructions': ("HIGH VOLTAGE. Discharge only into the analyser or the device's test load.\n"
                         "Tools: defibrillator analyser, ECG simulator, electrical safety analyser."),
        'items': [
            ("Inspect paddles / pads cable and connectors", "Check for pitting on paddles and cable damage.",
             "Intact", CHECK, True),
            ("Check pads and electrode expiry dates", "Replace expired or opened pads.", "In date", CHECK, True),
            ("Energy delivery at 200 J", "Discharge into the analyser; read the delivered energy.",
             "Within ±15% or manufacturer spec", VALUE, True),
            ("Charge time to maximum energy", "Time from charge button to ready at maximum energy.",
             "Within manufacturer spec (typically ≤ 10 s)", VALUE, True),
            ("Synchronised cardioversion test", "With the ECG simulator, check the shock is synchronised to the R wave.",
             "Delay within manufacturer spec", PASS_FAIL, True),
            ("AED rhythm analysis", "Simulate VF (shock advised) and normal sinus rhythm (no shock).",
             "Correct advice for both", PASS_FAIL, False),
            ("ECG monitoring and printer", "Simulate 60 bpm; check the display and a printed strip.",
             "Correct rate; printout legible", PASS_FAIL, True),
            ("Battery / self test", "Run the device self test; check battery capacity indicator.",
             "Self test passes", PASS_FAIL, True),
            _SAFETY, _CLEAN, _LABEL,
        ],
    },
    'suction_machine': {
        'label': 'Suction machine — PPM',
        'task_type': 'PPM',
        'instructions': "Empty and disinfect the collection jar before servicing. Tools: vacuum gauge.",
        'items': [
            ("Inspect housing, jar, lid and tubing", "Check for cracks and perished tubing.",
             "Intact; replace perished tubing", CHECK, True),
            ("Replace bacterial / overflow filter", "Fit a new filter; note the part in the Note field.",
             "New filter fitted", CHECK, True),
            ("Overflow shut-off valve", "Check the float closes when the jar is full.", "Shuts off", PASS_FAIL, True),
            ("Maximum vacuum", "Occlude the patient port; read the gauge at maximum setting.",
             "Within manufacturer spec", VALUE, True),
            ("Vacuum regulator", "Check the vacuum can be set low and high smoothly.", "Adjusts smoothly",
             PASS_FAIL, True),
            _SAFETY, _CLEAN, _LABEL,
        ],
    },
    'oxygen_concentrator': {
        'label': 'Oxygen concentrator — PPM',
        'task_type': 'PPM',
        'instructions': "Tools: oxygen analyser, flow meter. Run for 10 minutes before measuring.",
        'items': [
            ("Clean or replace the cabinet (gross) filter", "Wash or replace per the manual.", "Clean filter fitted",
             CHECK, True),
            ("Replace the inlet (fine) filter if due", "Check the hours meter against the manual's interval.",
             "Replaced if due", CHECK, False),
            ("Oxygen concentration", "Measure at the outlet with the analyser at the usual flow.",
             "Within manufacturer spec (typically ≥ 87%)", VALUE, True),
            ("Flow rate", "Compare the flow meter reading with a reference flow meter.",
             "Within manufacturer spec", VALUE, True),
            ("Low-purity and power-failure alarms", "Trigger each alarm per the manual.", "Both alarm", PASS_FAIL, True),
            ("Record hours meter", "Note the running hours.", "", VALUE, True),
            _SAFETY, _CLEAN, _LABEL,
        ],
    },
}


def choices():
    return [(key, s['label']) for key, s in STARTERS.items()]
