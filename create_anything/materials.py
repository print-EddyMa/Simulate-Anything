"""Material library: typical handbook values, enough to size and test a design.

Units are SI.  Temperatures are in degrees Celsius here because that is how
people read them; the tests convert.  ``service_temp`` is where the material
stops doing its job (softens, loses temper or strength); ``ignition_temp`` is
None for materials that do not burn.  Grades vary - treat these as starting
points and replace them with the datasheet of the stock you actually buy.
"""
from __future__ import annotations

# key: (name, family, density, E [Pa], strength [Pa], k, cp, service C, ignition C,
#       resistivity [ohm m] or None, cost [$/kg], colour, processes)
_ROWS = {
    "pla": ("PLA", "plastic", 1240, 3.5e9, 50e6, 0.13, 1800, 55, 388, None, 22,
            "#e8e4d8", ["3d_print"]),
    "petg": ("PETG", "plastic", 1270, 2.1e9, 45e6, 0.20, 1200, 70, 420, None, 25,
             "#cfe3ee", ["3d_print"]),
    "abs": ("ABS", "plastic", 1050, 2.2e9, 40e6, 0.17, 1400, 90, 416, None, 22,
            "#d9d9d6", ["3d_print", "injection_mould"]),
    "nylon": ("Nylon PA12", "plastic", 1010, 1.7e9, 45e6, 0.25, 1700, 110, 420, None, 60,
              "#f1efe6", ["3d_print", "injection_mould"]),
    "polycarbonate": ("Polycarbonate", "plastic", 1200, 2.4e9, 60e6, 0.20, 1200, 120, 580,
                      None, 40, "#dfeaf0", ["3d_print", "injection_mould", "machine"]),
    "acrylic": ("Acrylic (PMMA)", "plastic", 1180, 3.0e9, 65e6, 0.19, 1470, 80, 300, None, 8,
                "#e6f2f5", ["laser_cut", "machine"]),
    "aluminium": ("Aluminium 6061-T6", "metal", 2700, 69e9, 275e6, 167, 896, 150, None,
                  4.0e-8, 6, "#c9ced3", ["machine", "sheet_metal", "extrude"]),
    "steel": ("Mild steel", "metal", 7850, 200e9, 250e6, 50, 490, 400, None, 1.6e-7, 1.5,
              "#8d949b", ["machine", "sheet_metal", "weld"]),
    "stainless": ("Stainless steel 304", "metal", 8000, 193e9, 215e6, 16, 500, 800, None,
                  7.2e-7, 5, "#b4b9be", ["machine", "sheet_metal", "weld"]),
    "copper": ("Copper", "metal", 8960, 117e9, 70e6, 400, 385, 200, None, 1.72e-8, 10,
               "#c98452", ["machine", "sheet_metal", "wire"]),
    "brass": ("Brass", "metal", 8500, 100e9, 200e6, 120, 380, 300, None, 6.4e-8, 8,
              "#c9a858", ["machine"]),
    "titanium": ("Titanium Ti-6Al-4V", "metal", 4430, 114e9, 880e6, 6.7, 526, 400, None,
                 1.7e-6, 35, "#9aa1a8", ["machine", "3d_print"]),
    "plywood": ("Birch plywood", "wood", 680, 9e9, 40e6, 0.15, 1400, 100, 350, None, 3,
                "#d8b98a", ["laser_cut", "saw"]),
    "pine": ("Pine", "wood", 500, 9e9, 40e6, 0.12, 1600, 100, 350, None, 1.5,
             "#e2c79b", ["saw"]),
    "carbon_fibre": ("Carbon-fibre composite", "composite", 1600, 70e9, 600e6, 5, 1000, 120,
                     400, None, 80, "#3b3f44", ["layup"]),
}

_FIELDS = ("name", "family", "density", "youngs_modulus", "strength", "conductivity",
           "specific_heat", "service_temp", "ignition_temp", "resistivity", "cost_per_kg",
           "color", "processes")

MATERIALS = {key: dict(zip(_FIELDS, row), id=key) for key, row in _ROWS.items()}

ALIASES = {"aluminum": "aluminium", "al": "aluminium", "6061": "aluminium",
           "mild_steel": "steel", "stainless_steel": "stainless", "ss304": "stainless",
           "pc": "polycarbonate", "pmma": "acrylic", "pa12": "nylon", "wood": "pine",
           "birch": "plywood", "cfrp": "carbon_fibre", "carbon_fiber": "carbon_fibre",
           "cu": "copper", "ti": "titanium"}


def get(key: str) -> dict:
    """Material record by id (a few common aliases are accepted)."""
    k = str(key).strip().lower().replace(" ", "_").replace("-", "_")
    k = ALIASES.get(k, k)
    if k not in MATERIALS:
        raise KeyError(f"unknown material '{key}'; available: {sorted(MATERIALS)}")
    return MATERIALS[k]


def critical_temperature(material: dict, criterion: str = "auto"):
    """(temperature in C, what happens there) for a heat or flame test."""
    if criterion == "softening" or material["ignition_temp"] is None:
        what = ("softens and loses its shape" if material["family"] == "plastic"
                else "loses its strength")
        return material["service_temp"], what
    return material["ignition_temp"], "ignites"


def catalog() -> list:
    return [MATERIALS[k] for k in sorted(MATERIALS)]
