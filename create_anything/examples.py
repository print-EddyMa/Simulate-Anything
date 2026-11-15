"""Built-in examples.  Each one is a design plus the story the live demo tells
while it builds and tests it.  Every number in the story comes from the
simulations at run time; nothing here is a stored result."""
from __future__ import annotations

import copy

# -- Ghostlight: a deployable passive-radar node -----------------------------------
# Public textbook physics and catalogue-level parts only.
GHOSTLIGHT_DIAGRAM = {
    "nodes": [
        {"id": "tv", "label": "TV transmitter", "kind": "environment", "group": "Sky"},
        {"id": "drone", "label": "Drone", "kind": "environment", "group": "Sky"},
        {"id": "ref", "label": "Reference dipole", "kind": "component",
         "group": "RF front end", "part": "ref_dipole"},
        {"id": "surv", "label": "Surveillance antenna", "kind": "component",
         "group": "RF front end", "part": "surv_antenna"},
        {"id": "screen", "label": "Direct-path screen", "kind": "component",
         "group": "RF front end", "part": "screen"},
        {"id": "sdr", "label": "Coherent 2-channel SDR", "kind": "component",
         "group": "Electronics", "part": "sdr"},
        {"id": "cpu", "label": "Edge computer", "kind": "component", "group": "Electronics",
         "part": "computer"},
        {"id": "dsp", "label": "Cancel direct signal → cross-ambiguity → CFAR → tracker",
         "kind": "software", "group": "Electronics"},
        {"id": "report", "label": "Morning report", "kind": "software", "group": "Output"},
        {"id": "solar", "label": "Solar panel", "kind": "power", "group": "Power",
         "part": "solar"},
        {"id": "battery", "label": "LiFePO4 battery", "kind": "power", "group": "Power",
         "part": "battery"},
        {"id": "mast", "label": "Mast and tripod", "kind": "component", "group": "Structure",
         "part": "mast"},
    ],
    "edges": [
        {"from": "tv", "to": "ref", "label": "direct signal", "kind": "rf"},
        {"from": "tv", "to": "drone", "label": "illuminates", "kind": "rf"},
        {"from": "drone", "to": "surv", "label": "echo", "kind": "rf"},
        {"from": "screen", "to": "surv", "label": "blocks leakage", "kind": "rf"},
        {"from": "ref", "to": "sdr", "label": "channel 1", "kind": "rf"},
        {"from": "surv", "to": "sdr", "label": "channel 2", "kind": "rf"},
        {"from": "sdr", "to": "cpu", "label": "I/Q samples, USB", "kind": "data"},
        {"from": "cpu", "to": "dsp", "label": "runs", "kind": "data"},
        {"from": "dsp", "to": "report", "label": "tracks", "kind": "data"},
        {"from": "solar", "to": "battery", "label": "charge", "kind": "power"},
        {"from": "battery", "to": "sdr", "label": "5 V", "kind": "power"},
        {"from": "battery", "to": "cpu", "label": "12 V", "kind": "power"},
        {"from": "mast", "to": "surv", "label": "holds", "kind": "mechanical"},
        {"from": "mast", "to": "solar", "label": "holds", "kind": "mechanical"},
    ],
}

GHOSTLIGHT = {
    "name": "Ghostlight field node",
    "description": "A receive-only radar that maps drones over a site by listening for "
                   "their echoes of a TV broadcast. This version stands on its own: "
                   "weatherproof, solar-powered and built to ride out a storm.",
    "parts": [
        {"id": "tripod", "name": "Tripod base", "shape": "box", "look": "tripod",
         "size": [1100, 1100, 700], "position": [0, 0, 350], "material": "aluminium",
         "group": "Structure", "purpose": "Stands the mast on uneven ground"},
        {"id": "mast", "name": "Mast", "shape": "tube", "radius": 20, "inner_radius": 18.5,
         "length": 2600, "axis": "z", "position": [0, 0, 1300], "material": "aluminium",
         "process": "extrude", "group": "Structure",
         "purpose": "Lifts the antennas above people and parked vehicles"},
        {"id": "antenna_arm", "name": "Antenna arm", "shape": "cylinder", "radius": 12,
         "length": 300, "axis": "x", "position": [170, 0, 2300], "material": "aluminium",
         "group": "Structure", "purpose": "Holds the surveillance antenna off the mast"},
        {"id": "surv_antenna", "name": "Surveillance antenna", "shape": "box", "look": "bowtie",
         "size": [90, 340, 760], "position": [365, 0, 2300], "material": "aluminium",
         "process": "buy", "group": "RF front end",
         "specs": {"gain": "10 dBi", "beamwidth": "60 deg", "front_to_back": "15 dB",
                   "band": "470-608 MHz"},
         "purpose": "Faces the protected area and hears echoes"},
        {"id": "ref_arm", "name": "Reference arm", "shape": "cylinder", "radius": 10,
         "length": 250, "axis": "x", "position": [-145, 0, 2500], "material": "aluminium",
         "group": "Structure", "purpose": "Holds the reference antenna toward the tower"},
        {"id": "ref_dipole", "name": "Reference dipole", "shape": "cylinder", "radius": 4,
         "length": 260, "axis": "y", "position": [-275, 0, 2500], "material": "aluminium",
         "process": "buy", "group": "RF front end", "specs": {"gain": "2 dBi"},
         "purpose": "Records the TV signal itself, the copy echoes are compared with"},
        {"id": "enclosure", "name": "Electronics enclosure", "shape": "box",
         "size": [240, 180, 110], "wall": 3, "position": [0, -110, 1500],
         "material": "polycarbonate", "process": "buy", "group": "Electronics",
         "specs": {"rating": "IP66"}, "purpose": "Keeps rain and dust off the electronics"},
        {"id": "sdr", "name": "Coherent 2-channel SDR", "shape": "box", "look": "pcb",
         "size": [98, 60, 20], "position": [-55, -110, 1475], "material": "electronics",
         "fill": 0.5, "process": "buy", "group": "Electronics",
         "specs": {"power": "3 W", "noise_figure": "4 dB", "channels": "2, shared clock",
                   "adc": "14-bit"},
         "purpose": "Digitises both antennas on one clock so they stay in step"},
        {"id": "computer", "name": "Edge computer", "shape": "box", "look": "pcb",
         "size": [90, 62, 25], "position": [60, -110, 1478], "material": "electronics",
         "fill": 0.5, "process": "buy", "group": "Electronics", "specs": {"power": "10 W"},
         "purpose": "Runs cancellation, detection and tracking in real time"},
        {"id": "battery", "name": "Battery", "shape": "box", "look": "battery",
         "size": [230, 140, 180], "position": [330, 330, 90], "material": "lifepo4",
         "process": "buy", "group": "Power", "purpose": "Keeps the node running at night"},
        {"id": "panel_bracket", "name": "Panel bracket", "shape": "box",
         "size": [50, 120, 50], "wall": 3, "position": [0, 80, 1900], "material": "steel",
         "group": "Structure", "purpose": "Clamps the solar panel to the mast"},
        {"id": "solar", "name": "Solar panel", "shape": "box", "look": "solar",
         "size": [670, 540, 30], "position": [0, 380, 1900], "rotation": [-35, 0, 0],
         "material": "solar_module", "process": "buy", "group": "Power",
         "purpose": "Recharges the battery by day"},
    ],
    "assembly": [
        {"parts": ["tripod", "mast"], "action": "Open the tripod and lock the mast into it"},
        {"parts": ["antenna_arm", "surv_antenna"],
         "action": "Bolt the surveillance antenna to its arm, facing the area to watch"},
        {"parts": ["ref_arm", "ref_dipole"],
         "action": "Fit the reference dipole on the opposite arm, broadside to the TV tower"},
        {"parts": ["enclosure", "sdr", "computer"],
         "action": "Mount the SDR and computer in the enclosure and strap it to the mast"},
        {"parts": ["panel_bracket", "solar"],
         "action": "Clamp the solar panel to the mast, tilted toward the sun"},
        {"parts": ["battery"], "action": "Set the battery between the tripod legs and connect"},
    ],
    "scenarios": [
        {"id": "coverage", "type": "coverage", "part": "surv_antenna",
         "title": "See a small drone 1.5 km out", "required_range": "1.5 km",
         "rcs": "0.01 m^2", "tx_erp": "1 MW", "frequency": "569 MHz", "baseline": "20 km",
         "tx_bearing": "180 deg", "cancellation": "30 dB", "integration_time": "0.5 s",
         "receiver": "sdr", "screen": "screen"},
        {"id": "wind", "type": "wind", "part": "mast", "title": "Storm gust: 35 m/s",
         "speed": "35 m/s", "carries": ["surv_antenna", "screen", "ref_dipole", "enclosure",
                                        "solar"], "min_safety_factor": 1.5},
        {"id": "sun", "type": "enclosure", "part": "enclosure", "title": "Summer afternoon",
         "ambient": "38 degC", "irradiance": "1000 W/m^2", "wind": "0.5 m/s",
         "absorptivity": "0.6", "max_temperature": "65 degC",
         "tune": {"absorptivity": ["0.2", "0.95"]},
         "tune_labels": {"absorptivity": "Enclosure paint, solar absorptivity (lower = lighter)"}},
        {"id": "power", "type": "power", "part": "battery", "title": "Three cloudy winter days",
         "panel": "solar", "loads": ["sdr", "computer"], "days": 3,
         "peak_irradiance": "500 W/m^2", "day_length": "10 h", "weather_factor": 0.35,
         "min_charge": 0.2},
        {"id": "ice", "type": "load", "part": "antenna_arm", "title": "Ice on the antenna",
         "force": "40 N", "support": "cantilever", "min_safety_factor": 2},
    ],
    "diagram": GHOSTLIGHT_DIAGRAM,
}

GHOSTLIGHT_SCREEN = {
    "id": "screen", "name": "Direct-path screen", "shape": "box", "look": "wire_mesh",
    "size": [12, 900, 1200], "position": [120, 0, 2300], "material": "galvanised_steel",
    "group": "RF front end", "specs": {"rejection": "10 dB", "mesh": "12 mm squares"},
    "purpose": "Shades the surveillance antenna from the TV tower behind it"}

# -- a 3D-printed shelf bracket ------------------------------------------------------
BRACKET = {
    "name": "Shelf bracket",
    "description": "A 3D-printed L bracket that holds a shelf of books off a wall.",
    "parts": [
        {"id": "wall_leg", "name": "Wall leg", "shape": "box", "size": [8, 30, 140],
         "position": [4, 0, 70], "material": "pla", "group": "Bracket",
         "purpose": "Screws to the wall"},
        {"id": "shelf_arm", "name": "Shelf arm", "shape": "box", "size": [180, 30, 8],
         "position": [98, 0, 136], "material": "pla", "group": "Bracket",
         "purpose": "Carries the shelf"},
        {"id": "screw_top", "name": "Wall screw (top)", "shape": "cylinder", "radius": 2.5,
         "length": 40, "axis": "x", "position": [-12, 0, 115], "material": "steel",
         "process": "buy", "group": "Fixings", "purpose": "Holds the bracket to the wall"},
        {"id": "screw_bottom", "name": "Wall screw (bottom)", "shape": "cylinder",
         "radius": 2.5, "length": 40, "axis": "x", "position": [-12, 0, 25],
         "material": "steel", "process": "buy", "group": "Fixings",
         "purpose": "Holds the bracket to the wall"},
    ],
    "assembly": [
        {"parts": ["wall_leg", "shelf_arm"], "action": "Print the bracket as one piece"},
        {"parts": ["screw_top", "screw_bottom"], "action": "Screw it to the wall"},
    ],
    "scenarios": [
        {"id": "books", "type": "load", "part": "shelf_arm", "title": "8 kg of books",
         "force": "40 N", "support": "cantilever", "position": 0.6, "min_safety_factor": 2,
         "max_deflection": "3 mm"},
        {"id": "knock", "type": "drop", "part": "shelf_arm", "title": "A 1 kg book dropped",
         "mass": "1 kg", "height": "0.3 m", "min_safety_factor": 1},
        {"id": "candle", "type": "fire", "part": "shelf_arm", "title": "Candle underneath",
         "heat_flux": "8 kW/m^2", "duration": "300 s", "criterion": "softening"},
    ],
}

# -- an LED heat sink ---------------------------------------------------------------
HEATSINK = {
    "name": "Heat sink for a 40 W LED",
    "description": "An extruded fin heat sink that keeps a high-power LED module cool in "
                   "still air.",
    "parts": [
        {"id": "sink", "name": "Heat sink", "shape": "heatsink", "base": [90, 90, 6],
         "fin_height": 25, "fin_thickness": 2, "n_fins": 9, "position": [0, 0, 18.5],
         "material": "aluminium", "process": "extrude", "group": "Thermal",
         "purpose": "Spreads the LED's heat into the air"},
        {"id": "led", "name": "LED module", "shape": "box", "look": "pcb",
         "size": [40, 40, 3], "position": [0, 0, 1.5], "material": "electronics",
         "process": "buy", "group": "Light", "specs": {"power": "40 W"},
         "purpose": "Makes the light (and the heat)"},
    ],
    "assembly": [{"parts": ["led", "sink"], "action": "Paste and screw the LED to the base"}],
    "scenarios": [
        {"id": "led_heat", "type": "heat", "part": "sink", "title": "40 W in still air",
         "mode": "heatsink", "power": "40 W", "max_temperature": "85 degC",
         "h": "8 W/(m^2*K)", "ambient": "30 degC"},
    ],
}

EXAMPLES = {
    "ghostlight": {
        "title": "Ghostlight field node",
        "tagline": "A passive radar that sees drones using TV signals",
        "prompt": "Turn our Ghostlight passive-radar prototype into a field node that can "
                  "stand alone at a substation: weatherproof, solar-powered, and able to "
                  "see a small drone 1.5 km out.",
        "icon": "radar", "design": GHOSTLIGHT},
    "bracket": {
        "title": "Shelf bracket", "tagline": "Will a 3D print hold your books?",
        "prompt": "A 3D-printed shelf bracket for 8 kg of books.", "icon": "bracket",
        "design": BRACKET},
    "heatsink": {
        "title": "LED heat sink", "tagline": "Keep 40 W of LED below 85 °C",
        "prompt": "A heat sink for a 40 W LED module in still air.", "icon": "thermometer",
        "design": HEATSINK},
}


def get(example_id: str) -> dict:
    if example_id not in EXAMPLES:
        raise KeyError(f"unknown example '{example_id}'; available: {sorted(EXAMPLES)}")
    return copy.deepcopy(EXAMPLES[example_id])


def listing() -> list:
    return [{"id": k, **{f: v[f] for f in ("title", "tagline", "prompt", "icon")}}
            for k, v in EXAMPLES.items()]
