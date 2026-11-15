"""Real-world tests: turn "this part, in this situation" into a verified simulation.

Each scenario in a design names a part and a situation (a load, a drop, a
flame, a current...).  The functions here idealise the part (a bracket arm
becomes a beam, a lamp shade becomes a heated wall), run the matching
Simulate Anything template with the part's real dimensions and material, and
compare the result with a requirement.

A test only counts as passed when the requirement is met *and* the simulation
passed its own self-checks.  Otherwise it is reported as failed or unverified.
"""
from __future__ import annotations

import math

import numpy as np

import simulate_anything as sa
from simulate_anything.units import DimensionError, convert

from . import materials
from simulate_anything.units import split_quantity

from .design import (AXES, DesignError, aabb, contains, extents, mass_volume_mm3,
                     part_by_id, total_mass_kg, volume_mm3)

G0 = 9.80665
FLUIDS = {"water": (998.0, 1.0e-3), "air": (1.2, 1.8e-5), "oil": (870.0, 0.1),
          "glycol": (1070.0, 4.0e-3)}


class ScenarioError(ValueError):
    """The scenario cannot be applied to this part."""


def _q(sc, key, unit, default=None, delta=False):
    value = sc.get(key, default)
    if value is None:
        raise ScenarioError(f"scenario '{sc['id']}' needs '{key}' (for example \"5 {unit}\")")
    try:
        return convert(value, unit, delta=delta)
    except DimensionError as exc:
        raise ScenarioError(f"scenario '{sc['id']}', '{key}': {exc}") from None


MAX_LOAD = 99.0          # cap so a load is always a finite JSON number


def load_of(value, limit, kind, base=0.0):
    """How much of its allowance a result uses: 1 is exactly at the limit, 0.5 half
    of it, 2 twice over (None when it has no meaning).  A maximum is measured
    from ``base`` (a temperature from the ambient it rises from); a minimum such
    as a safety factor is required / actual."""
    if not all(math.isfinite(v) for v in (value, limit, base)):
        return None
    if kind == "min":
        if value <= 0 or limit <= 0:
            return None
        out = limit / value
    else:
        span = limit - base
        if span <= 0:
            return None
        out = max(0.0, (value - base) / span)
    return float(min(out, MAX_LOAD))


def margin_of(load):
    """Margin of safety from a load: allowable / actual - 1.  +0.25 means 25% to
    spare, 0 is exactly at the limit, negative fails (None when untrusted)."""
    if load is None:
        return None
    return MAX_LOAD if load <= 1.0 / (MAX_LOAD + 1) else float(1.0 / load - 1.0)


def _metric(label, value, unit, limit, kind, base=0.0):
    ok = value >= limit if kind == "min" else value <= limit
    load = load_of(float(value), float(limit), kind, float(base))
    return {"label": label, "value": float(value), "unit": unit, "limit": float(limit),
            "kind": kind, "ok": bool(ok), "base": float(base), "load": load,
            "margin": margin_of(load)}


def load_of_test(t: dict):
    """A test's load: its most loaded metric (None without a trusted number)."""
    if t.get("status") not in ("pass", "fail"):
        return None
    loads = [m["load"] for m in t.get("metrics", []) if m.get("load") is not None]
    return max(loads) if loads else None


def _violation(metrics) -> float:
    worst = 0.0
    for m in metrics:
        scale = max(abs(m["limit"]), 1e-12)
        gap = (m["limit"] - m["value"]) if m["kind"] == "min" else (m["value"] - m["limit"])
        worst = max(worst, gap / scale)
    return max(worst, 0.0)


def beam_axes(part: dict, sc: dict):
    """(span, load, side) axis indices of a box treated as a beam."""
    e = extents(part)
    span = AXES[sc["span_axis"]] if sc.get("span_axis") in AXES else int(np.argmax(e))
    rest = [a for a in range(3) if a != span]
    if sc.get("load_axis") in AXES and AXES[sc["load_axis"]] != span:
        load = AXES[sc["load_axis"]]
    else:
        load = 2 if span != 2 else min(rest, key=lambda a: e[a])
    side = next(a for a in rest if a != load)
    return span, load, side


def spec(part: dict, key: str, unit: str, default=None):
    """A datasheet value from part['specs'], converted to ``unit``."""
    raw = (part.get("specs") or {}).get(key, default)
    if raw is None:
        return None
    try:
        return convert(raw, unit)
    except DimensionError as exc:
        raise ScenarioError(f"part '{part['id']}', spec '{key}': {exc}") from None


def spec_db(part: dict, key: str, default: float) -> float:
    raw = (part.get("specs") or {}).get(key)
    if raw is None:
        return default
    try:
        return float(split_quantity(str(raw))[0])
    except Exception:
        raise ScenarioError(f"part '{part['id']}', spec '{key}' must look like \"10 dB\"") from None


def section(part: dict, sc: dict):
    """Beam idealisation of a part: (length, width, height) in metres + note.

    Round and hollow sections are replaced by the solid rectangle with the
    same second moment of area and the same outer-fibre distance, so both
    stiffness and bending stress are exact."""
    e = extents(part)
    shape = part["shape"]
    if shape in ("cylinder", "tube"):
        dia = 2 * part["radius"]
        inner = 2 * part.get("inner_radius", 0.0) if shape == "tube" else 0.0
        inertia = math.pi / 64 * (dia ** 4 - inner ** 4)
        return (part["length"] / 1e3, 12 * inertia / dia ** 3 / 1e3, dia / 1e3,
                "round section modelled as the rectangle of equal stiffness and strength")
    if shape not in ("box", "mesh"):
        raise ScenarioError(f"a {shape} cannot be tested as a beam")
    span, load, side = beam_axes(part, sc)
    length, height, width, note = e[span], e[load], e[side], ""
    if shape == "mesh":
        solidity = volume_mm3(part) / max(e[0] * e[1] * e[2], 1e-12)
        width *= min(1.0, solidity)
        note = (f"imported shape approximated by its bounding box at {100 * solidity:.0f}% "
                "solidity - treat as an estimate")
    elif part.get("wall"):
        w = part["wall"]
        inertia = (width * height ** 3 - (width - 2 * w) * (height - 2 * w) ** 3) / 12
        width = 12 * inertia / height ** 3
        note = "hollow section modelled as the rectangle of equal stiffness and strength"
    return length / 1e3, width / 1e3, height / 1e3, note


def _wall_thickness(part: dict) -> float:
    """Smallest through-thickness dimension in metres (what heat has to cross)."""
    if part.get("wall"):
        return part["wall"] / 1e3
    if part["shape"] == "tube":
        return (part["radius"] - part["inner_radius"]) / 1e3
    if part["shape"] == "heatsink":
        return part["base"][2] / 1e3
    return min(extents(part)) / 1e3


def _run(name, params, verify):
    return sa.run(name, params, verify=verify)


# -- individual tests ---------------------------------------------------------------
def test_load(design, part, sc, verify):
    mat = materials.get(part["material"])
    length, width, height, note = section(part, sc)
    force = _q(sc, "force", "N")
    support = sc.get("support", "cantilever")
    position = float(sc.get("position", 1.0 if support == "cantilever" else 0.5))
    weight = mat["density"] * G0 * mass_volume_mm3(part) * 1e-9 / length  # self-weight, N/m
    res = _run("beam", {
        "length": f"{length} m", "width": f"{width} m", "height": f"{height} m",
        "youngs_modulus": f"{mat['youngs_modulus']} Pa",
        "yield_strength": f"{mat['strength']} Pa", "support": support,
        "point_load": f"{force} N", "load_position": position,
        "distributed_load": f"{weight} N/m"}, verify)
    sf_min = float(sc.get("min_safety_factor", 2.0))
    defl_max = _q(sc, "max_deflection", "mm", f"{length * 1e3 / 60} mm")
    sf, defl = res.value("safety_factor"), res.value("max_deflection")
    metrics = [_metric("safety factor", sf, "", sf_min, "min"),
               _metric("deflection", defl, "mm", defl_max, "max")]
    head = (f"{force:.3g} N on the {part['name'].lower()}: safety factor {sf:.2f} "
            f"(needs {sf_min:g}), bends {defl:.3g} mm (limit {defl_max:.3g} mm)")
    return res, metrics, head, [note] if note else [], f"Load: {force:.3g} N"


def test_drop(design, part, sc, verify):
    mat = materials.get(part["material"])
    length, width, height, note = section(part, sc)
    mass = _q(sc, "mass", "kg", f"{total_mass_kg(design)} kg")
    drop = _q(sc, "height", "m")
    res = _run("drop_impact", {
        "mass": f"{mass} kg", "drop_height": f"{drop} m", "length": f"{length} m",
        "width": f"{width} m", "height": f"{height} m",
        "youngs_modulus": f"{mat['youngs_modulus']} Pa",
        "yield_strength": f"{mat['strength']} Pa",
        "support": sc.get("support", "cantilever")}, verify)
    sf_min = float(sc.get("min_safety_factor", 1.0))
    sf = res.value("safety_factor")
    metrics = [_metric("safety factor at impact", sf, "", sf_min, "min")]
    shown = f"{mass:.3g} kg" if mass >= 1 else f"{mass * 1e3:.3g} g"
    head = (f"{shown} dropped {drop:.2g} m onto the {part['name'].lower()}: peak "
            f"force {res.value('peak_force'):.3g} N ({res.value('impact_factor'):.0f}x the "
            f"weight), safety factor {sf:.2f} (needs {sf_min:g})")
    return res, metrics, head, [note] if note else [], f"Drop: {drop:.2g} m"


def test_vibration(design, part, sc, verify):
    mat = materials.get(part["material"])
    length, width, height, note = section(part, sc)
    excite = _q(sc, "excitation", "Hz")
    support = sc.get("support", "cantilever")
    res = _run("beam_vibration", {
        "length": f"{length} m", "width": f"{width} m", "height": f"{height} m",
        "youngs_modulus": f"{mat['youngs_modulus']} Pa",
        "density": f"{mat['density']} kg/m^3", "support": support}, verify)
    f1 = res.value("f1")
    notes = [note] if note else []
    tip = _q(sc, "tip_mass", "kg", "0 kg")
    if tip > 0:
        if support != "cantilever":
            raise ScenarioError("tip_mass is only supported for a cantilever")
        beam_mass = mat["density"] * width * height * length
        f1 = f1 / math.sqrt(1 + tip / (0.2427 * beam_mass))
        notes.append(f"frequency lowered for the {tip * 1e3:.3g} g carried at the tip "
                     "(Rayleigh's method, effective beam mass 0.2427 m)")
    margin = float(sc.get("margin", 1.2))
    metrics = [_metric("first natural frequency", f1, "Hz", margin * excite, "min")]
    head = (f"{part['name']} rings at {f1:.3g} Hz; it is shaken at {excite:.3g} Hz and must "
            f"stay above {margin * excite:.3g} Hz to avoid resonance")
    return res, metrics, head, notes, f"Vibration: {excite:.3g} Hz"


def test_heat(design, part, sc, verify):
    mat = materials.get(part["material"])
    ambient = _q(sc, "ambient", "K", "25 degC")
    h = _q(sc, "h", "W/(m^2*K)", "10 W/(m^2*K)")
    mode = sc.get("mode") or ("heatsink" if part["shape"] == "heatsink" else "hot_end")
    notes = []
    if mode == "heatsink":
        if part["shape"] != "heatsink":
            raise ScenarioError("heat mode 'heatsink' needs a part with shape 'heatsink'")
        power = _q(sc, "power", "W")
        limit = _q(sc, "max_temperature", "K", "85 degC")
        base = {"fin_length": f"{part['fin_height']} mm",
                "fin_thickness": f"{part['fin_thickness']} mm",
                "fin_width": f"{part['base'][1]} mm", "n_fins": part["n_fins"],
                "conductivity": f"{mat['conductivity']} W/(m*K)", "h": f"{h} W/(m^2*K)",
                "ambient_temperature": f"{ambient} K", "tip": "convective"}
        # the fin problem is linear: one solve gives the resistance, which sets the
        # base temperature for this power; the reported run is at that temperature
        probe = _run("heat_sink_fin", dict(base, base_temperature=f"{ambient + 50} K"), False)
        t_base = ambient + power * probe.value("thermal_resistance")
        res = _run("heat_sink_fin", dict(base, base_temperature=f"{t_base} K"), verify)
        closure = abs(res.value("total_heat") - power) / power
        if closure > 1e-3:
            notes.append(f"heat removed differs from the source by {100 * closure:.2g}%")
        notes.append("only the fins are counted; the bare base between them adds a little")
        metrics = [_metric("temperature at the heat source", t_base - 273.15, "degC",
                           limit - 273.15, "max", ambient - 273.15)]
        head = (f"{power:.3g} W into the {part['name'].lower()}: the source sits at "
                f"{t_base - 273.15:.0f} degC (limit {limit - 273.15:.0f} degC), thermal "
                f"resistance {res.value('thermal_resistance'):.2f} K/W")
        return res, metrics, head, notes, f"Heat: {power:.3g} W"
    if mode != "hot_end":
        raise ScenarioError(f"unknown heat mode '{mode}' (use 'heatsink' or 'hot_end')")
    hot = _q(sc, "hot_temperature", "K")
    limit = _q(sc, "max_temperature", "K", "50 degC")
    e = extents(part)
    params = {"base_temperature": f"{hot} K", "ambient_temperature": f"{ambient} K",
              "n_fins": 1, "conductivity": f"{mat['conductivity']} W/(m*K)"}
    if part["shape"] in ("cylinder", "tube"):
        r_o, r_i = part["radius"], part.get("inner_radius", 0.0)
        area = math.pi * (r_o ** 2 - r_i ** 2)
        side = math.sqrt(area)                       # square of equal metal area
        scale = (2 * math.pi * r_o) / (4 * side)     # keep h x perimeter exact
        params.update(fin_length=f"{part['length']} mm", fin_thickness=f"{side} mm",
                      fin_width=f"{side} mm", h=f"{h * scale} W/(m^2*K)", tip="adiabatic")
        notes.append("round section modelled as the square bar of equal metal area, with "
                     "the film coefficient scaled so heat loss per length is exact")
    elif part["shape"] == "box":
        span = AXES[sc["span_axis"]] if sc.get("span_axis") in AXES else int(np.argmax(e))
        a, b = sorted(e[i] for i in range(3) if i != span)
        params.update(fin_length=f"{e[span]} mm", fin_thickness=f"{a} mm",
                      fin_width=f"{b} mm", h=f"{h} W/(m^2*K)", tip="convective")
    else:
        raise ScenarioError(f"a {part['shape']} cannot be tested as a rod heated at one end")
    res = _run("heat_sink_fin", params, verify)
    tip = res.value("tip_temperature")
    metrics = [_metric("temperature at the far end", tip, "degC", limit - 273.15, "max",
                       ambient - 273.15)]
    head = (f"One end of the {part['name'].lower()} at {hot - 273.15:.0f} degC: the far end "
            f"reaches {tip:.0f} degC (limit {limit - 273.15:.0f} degC)")
    return res, metrics, head, notes, f"Heat: {hot - 273.15:.0f} degC at one end"


def test_fire(design, part, sc, verify):
    mat = materials.get(part["material"])
    flux = _q(sc, "heat_flux", "W/m^2", "30 kW/m^2")
    duration = _q(sc, "duration", "s", "60 s")
    ambient = _q(sc, "ambient", "K", "25 degC")
    crit_c, what = materials.critical_temperature(mat, sc.get("criterion", "auto"))
    thickness = _wall_thickness(part)
    res = _run("flame_exposure", {
        "thickness": f"{thickness} m", "conductivity": f"{mat['conductivity']} W/(m*K)",
        "density": f"{mat['density']} kg/m^3",
        "specific_heat": f"{mat['specific_heat']} J/(kg*K)", "heat_flux": f"{flux} W/m^2",
        "h": sc.get("h", "15 W/(m^2*K)"), "back_face": sc.get("back_face", "insulated"),
        "ambient_temperature": f"{ambient} K", "critical_temperature": f"{crit_c} degC",
        "duration": f"{duration} s"}, verify)
    peak = crit_c - res.value("margin_to_critical")
    metrics = [_metric("hottest surface temperature", peak, "degC", crit_c, "max",
                       ambient - 273.15)]
    if res.value("reaches_critical"):
        head = (f"{flux / 1e3:.3g} kW/m^2 on the {part['name'].lower()}: {mat['name']} {what} "
                f"({crit_c:.0f} degC) after {res.value('time_to_critical'):.0f} s")
    else:
        head = (f"{flux / 1e3:.3g} kW/m^2 for {duration:.0f} s on the {part['name'].lower()}: "
                f"peaks at {peak:.0f} degC, below the {crit_c:.0f} degC at which "
                f"{mat['name']} {what}")
    notes = [f"criterion: {mat['name']} {what} at {crit_c:.0f} degC",
             "no charring, melting or flame spread is modelled"]
    return res, metrics, head, notes, f"Fire: {flux / 1e3:.3g} kW/m^2 for {duration:.0f} s"


def test_current(design, part, sc, verify):
    mat = materials.get(part["material"])
    if mat["resistivity"] is None:
        raise ScenarioError(f"{mat['name']} is an insulator and cannot carry the current")
    current = _q(sc, "current", "A")
    ambient = _q(sc, "ambient", "K", "25 degC")
    limit = _q(sc, "max_temperature", "K", "70 degC")
    e = extents(part)
    notes = []
    if part["shape"] == "cylinder":
        length, dia = part["length"] / 1e3, 2 * part["radius"] / 1e3
    elif part["shape"] in ("box", "tube"):
        span = (AXES[part["axis"]] if part["shape"] == "tube" else int(np.argmax(e)))
        length = e[span] / 1e3
        area = volume_mm3(part) / e[span] * 1e-6
        dia = math.sqrt(4 * area / math.pi)
        notes.append("section modelled as the round conductor of equal area")
    else:
        raise ScenarioError(f"a {part['shape']} cannot be tested as a conductor")
    res = _run("wire_heating", {
        "length": f"{length} m", "diameter": f"{dia} m", "current": f"{current} A",
        "resistivity": f"{mat['resistivity']} ohm*m",
        "conductivity": f"{mat['conductivity']} W/(m*K)", "h": sc.get("h", "12 W/(m^2*K)"),
        "ambient_temperature": f"{ambient} K", "terminal_temperature": f"{ambient} K"}, verify)
    t_max, drop = res.value("max_temperature"), res.value("voltage_drop")
    metrics = [_metric("hottest point", t_max, "degC", limit - 273.15, "max",
                       ambient - 273.15)]
    if sc.get("max_voltage_drop") is not None:
        metrics.append(_metric("voltage drop", drop, "V",
                               _q(sc, "max_voltage_drop", "V"), "max"))
    elif sc.get("voltage") is not None:
        allowed = _q(sc, "voltage", "V") * float(sc.get("max_drop_percent", 3.0)) / 100
        metrics.append(_metric("voltage drop", drop, "V", allowed, "max"))
    head = (f"{current:.3g} A through the {part['name'].lower()}: {t_max:.0f} degC at the "
            f"hottest point (limit {limit - 273.15:.0f} degC), drops {drop:.3g} V and wastes "
            f"{res.value('power_dissipated'):.3g} W")
    return res, metrics, head, notes, f"Electricity: {current:.3g} A"


def test_flow(design, part, sc, verify):
    if part["shape"] != "tube":
        raise ScenarioError("a flow test needs a tube")
    fluid = str(sc.get("fluid", "water")).lower()
    if fluid not in FLUIDS:
        raise ScenarioError(f"unknown fluid '{fluid}' (known: {sorted(FLUIDS)})")
    rho, mu = FLUIDS[fluid]
    rate = _q(sc, "flow_rate", "m^3/s")
    limit = _q(sc, "max_pressure_drop", "Pa", "10 kPa")
    res = _run("pipe_flow", {
        "diameter": f"{2 * part['inner_radius']} mm", "length": f"{part['length']} mm",
        "flow_rate": f"{rate} m^3/s", "density": f"{rho} kg/m^3",
        "viscosity": f"{mu} Pa*s"}, verify)
    dp, vel = res.value("pressure_drop"), res.value("mean_velocity")
    metrics = [_metric("pressure drop", dp / 1e3, "kPa", limit / 1e3, "max")]
    if sc.get("max_velocity") is not None:
        metrics.append(_metric("flow speed", vel, "m/s", _q(sc, "max_velocity", "m/s"), "max"))
    head = (f"{rate * 6e4:.3g} L/min of {fluid} through the {part['name'].lower()}: loses "
            f"{dp / 1e3:.3g} kPa (limit {limit / 1e3:.3g} kPa) at {vel:.2g} m/s, Reynolds "
            f"number {res.value('reynolds_number'):.0f}")
    return res, metrics, head, [], f"Flow: {rate * 6e4:.3g} L/min"


# -- any simulation in the catalogue ---------------------------------------------------
MATERIAL_UNITS = {"density": "kg/m^3", "youngs_modulus": "Pa", "strength": "Pa",
                  "conductivity": "W/(m*K)", "specific_heat": "J/(kg*K)",
                  "service_temp": "degC", "ignition_temp": "degC", "resistivity": "ohm*m"}
DIMENSION_KEYS = ("size", "radius", "inner_radius", "length", "wall", "base", "fin_height",
                  "fin_thickness", "n_fins", "boom_length", "element_length", "n_elements")
SOURCES_HELP = ("size.0 | size.1 | size.2 (or size.x/y/z), radius, inner_radius, length, "
                "wall, base.0-2, fin_height, fin_thickness, n_fins, boom_length, "
                "element_length, n_elements, part.mass, part.volume, design.mass, "
                "spec.<name>, or material.<" + " | ".join(MATERIAL_UNITS) + ">")
AMBIENT_HINTS = ("ambient", "inlet", "coolant", "outside", "air_temperature",
                 "fluid_temperature", "initial_temperature")


def _has_unit(value) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return bool(split_quantity(value.strip())[1])
    except DimensionError:
        return False


def parse_source(source: str):
    """'size.2' -> ('size', 2); 'wall' -> ('wall', None); 'material.x' -> ('material', 'x')."""
    src = str(source).strip()
    key, _, idx = src.partition(".")
    if key in ("material", "part", "design", "spec"):
        return key, idx
    if key not in DIMENSION_KEYS:
        raise ScenarioError(f"unknown binding source '{src}' (use {SOURCES_HELP})")
    if not idx:
        return key, None
    if idx in AXES:
        return key, AXES[idx]
    if idx not in ("0", "1", "2"):
        raise ScenarioError(f"binding source '{src}': index must be 0-2 or x/y/z")
    return key, int(idx)


def bound_value(design: dict, part: dict, source: str):
    """The value a binding source stands for, as something sa.run accepts."""
    key, idx = parse_source(source)
    mat = materials.get(part["material"])
    if key == "material":
        if idx not in MATERIAL_UNITS:
            raise ScenarioError(f"unknown material property '{idx}' "
                                f"(known: {sorted(MATERIAL_UNITS)})")
        if mat[idx] is None:
            raise ScenarioError(f"{mat['name']} has no {idx.replace('_', ' ')}")
        return f"{mat[idx]} {MATERIAL_UNITS[idx]}"
    if key == "spec":
        if idx not in (part.get("specs") or {}):
            raise ScenarioError(f"part '{part['id']}' has no spec '{idx}'")
        return part["specs"][idx]
    if key == "part":
        if idx == "mass":
            return f"{mass_volume_mm3(part) * 1e-9 * mat['density']} kg"
        if idx == "volume":
            return f"{volume_mm3(part) * 1e-9} m^3"
        raise ScenarioError(f"unknown part property '{idx}' (use part.mass or part.volume)")
    if key == "design":
        if idx == "mass":
            return f"{total_mass_kg(design)} kg"
        raise ScenarioError(f"unknown design property '{idx}' (use design.mass)")
    if key not in part:
        raise ScenarioError(f"part '{part['id']}' ({part['shape']}) has no '{key}'")
    value = part[key] if idx is None else part[key][idx]
    return int(value) if key in ("n_fins", "n_elements") else f"{value} mm"


def _is_temperature(unit: str) -> bool:
    try:
        convert("300 K", unit)
        return bool(unit)
    except (DimensionError, ValueError, TypeError):
        return False


def _base_for(req: dict, unit: str, params: dict):
    """Where a temperature's load is measured from: the requirement's 'from', else the
    run's ambient-like input, else 25 degC (said in a note).  Other units: zero."""
    if not unit or not _is_temperature(unit):
        return 0.0, None
    if req.get("from") is not None:
        try:
            return convert(req["from"], unit), None
        except (DimensionError, ValueError, TypeError) as exc:
            raise ScenarioError(f"requirement 'from': {exc}") from None
    for key, value in params.items():
        if any(h in key for h in AMBIENT_HINTS):
            try:
                return convert(value, unit), None
            except (DimensionError, ValueError, TypeError):
                continue
    return convert("25 degC", unit), ("temperature load measured as the rise above 25 degC "
                                      "(give the requirement a 'from' to change it)")


def test_simulation(design, part, sc, verify):
    name = sc.get("simulation")
    try:
        cls = sa.get(str(name))
    except sa.ParamError:
        known = sorted(x["name"] for x in sa.catalog())
        raise ScenarioError(f"unknown simulation '{name}' (known: {known})") from None
    schema = {p["name"]: p for p in cls.schema()["params"]}
    params = dict(sc.get("params") or {})
    bind = sc.get("bind") or {}
    if not isinstance(bind, dict) or not bind:
        raise ScenarioError(
            "a 'simulation' test must bind at least one parameter to the part ('bind': "
            "{\"param\": \"size.2\" or \"material.conductivity\"...}), otherwise its "
            "result does not depend on the design")
    for pname in list(params) + list(bind):
        if pname not in schema:
            raise ScenarioError(f"'{name}' has no parameter '{pname}' "
                                f"(parameters: {sorted(schema)})")
    for pname, source in bind.items():
        params[pname] = bound_value(design, part, source)
    reqs = sc.get("require")
    if not isinstance(reqs, list) or not reqs:
        raise ScenarioError("a 'simulation' test needs 'require': [{\"qoi\": name, "
                            "\"max\" or \"min\": value with unit}]")
    res = _run(cls.name, params, verify)
    if not res.qoi:
        failed = "; ".join(f"{c.name}: {c.detail}" for c in res.report.failed()[:3])
        raise ScenarioError(f"'{name}' rejected this setup before solving ({failed})")
    metrics, extra_notes = [], []
    for r in reqs:
        q = r.get("qoi") if isinstance(r, dict) else None
        if q not in res.qoi:
            raise ScenarioError(f"'{name}' reports no result '{q}' "
                                f"(results: {sorted(res.qoi)})")
        kinds = [k for k in ("min", "max") if r.get(k) is not None]
        if len(kinds) != 1:
            raise ScenarioError(f"requirement on '{q}' needs exactly one of min or max")
        value, unit = res.qoi[q]
        limit = r[kinds[0]]
        if unit and not _has_unit(limit):
            raise ScenarioError(f"requirement on '{q}': give a unit, for example "
                                f"\"{limit} {unit}\"")
        try:
            limit = float(limit) if not unit else convert(limit, unit)
        except (DimensionError, TypeError, ValueError) as exc:
            raise ScenarioError(f"requirement on '{q}' (reported in '{unit or 'no unit'}'): "
                                f"{exc}") from None
        base, note = _base_for(r, unit, params)
        if note and note not in extra_notes:
            extra_notes.append(note)
        metrics.append(_metric(r.get("label") or q.replace("_", " "), value, unit, limit,
                               kinds[0], base))
    title = sc.get("title") or cls.schema()["title"]
    shown = "; ".join(f"{m['label']} {m['value']:.4g} {m['unit']}".rstrip() +
                      f" ({'needs' if not m['ok'] else 'limit'} "
                      f"{'>=' if m['kind'] == 'min' else '<='} {m['limit']:.4g})"
                      for m in metrics)
    head = f"{cls.schema()['title']} on the {part['name'].lower()}: {shown}"
    notes = ["bound to the design: " + ", ".join(f"{k} = {v}" for k, v in bind.items())]
    return res, metrics, head, notes + extra_notes, title


DRAG = {"wire_mesh": (1.3, 0.25), "bowtie": (1.3, 0.3), "solar": (1.3, 1.0),
        "tripod": (1.2, 0.15)}


def _wind_area(part: dict, direction: int):
    """(drag coefficient x projected area [m^2]) of a part facing the wind."""
    e = extents(part)
    a0, a1 = aabb(part)
    size = a1 - a0                                   # world-axis box, rotation included
    across = [i for i in range(3) if i != direction]
    area = size[across[0]] * size[across[1]] / 1e6
    if part["shape"] in ("cylinder", "tube", "yagi"):
        if part["shape"] == "yagi":
            boom = part["boom_length"] * 2 * part["boom_radius"]
            els = part["n_elements"] * part["element_length"] * 0.925 * 2 * part["element_radius"]
            return 1.2 * (boom + els) / 1e6
        return 1.2 * area
    cd, solidity = DRAG.get(part.get("look"), (1.15, 1.0))
    return cd * solidity * area


def test_wind(design, part, sc, verify):
    mat = materials.get(part["material"])
    speed = _q(sc, "speed", "m/s")
    rho = _q(sc, "air_density", "kg/m^3", "1.225 kg/m^3")
    direction = AXES.get(sc.get("direction", "x"), 0)
    qdyn = 0.5 * rho * speed ** 2
    length, width, height, note = section(part, {**sc, "span_axis": "z"})
    base_z = float(aabb(part)[0][2])
    own = qdyn * 1.2 * height                     # N/m on the mast itself
    moment, area_total = 0.0, 0.0
    missing = []
    for pid in sc.get("carries", []):
        try:
            other = part_by_id(design, pid)
        except DesignError:
            missing.append(pid)
            continue
        cda = _wind_area(other, direction)
        arm = (float(np.mean(aabb(other), axis=0)[2]) - base_z) / 1e3
        moment += qdyn * cda * max(arm, 0.0)
        area_total += cda
    tip = moment / length                         # same base moment, applied at the top
    res = _run("beam", {
        "length": f"{length} m", "width": f"{width} m", "height": f"{height} m",
        "youngs_modulus": f"{mat['youngs_modulus']} Pa",
        "yield_strength": f"{mat['strength']} Pa", "support": "cantilever",
        "point_load": f"{tip} N", "load_position": 1.0,
        "distributed_load": f"{own} N/m"}, verify)
    sf_min = float(sc.get("min_safety_factor", 1.5))
    sway = _q(sc, "max_deflection", "mm", f"{length * 1e3 / 40} mm")
    sf, defl = res.value("safety_factor"), res.value("max_deflection")
    metrics = [_metric("safety factor", sf, "", sf_min, "min"),
               _metric("sway at the top", defl, "mm", sway, "max")]
    head = (f"A {speed:.0f} m/s gust ({speed * 2.237:.0f} mph) on the {part['name'].lower()} "
            f"and everything on it: safety factor {sf:.2f} (needs {sf_min:g}), the top sways "
            f"{defl:.0f} mm (limit {sway:.0f} mm)")
    notes = [n for n in (note,) if n] + [
        f"wind pressure {qdyn:.0f} Pa on {area_total:.2f} m^2 of drag area carried, plus the "
        "mast itself (drag coefficient 1.2)",
        "loads carried on the mast are moved to its top with the same base bending moment: "
        "the stress at the base is exact, the sway is slightly overstated"]
    if missing:
        notes.append(f"not in the design yet, so not loaded: {', '.join(missing)}")
    return res, metrics, head, notes, f"Wind: {speed:.0f} m/s"


def _inside(design, box):
    return [q for q in design["parts"] if q is not box and contains(box, q)]


def test_enclosure(design, part, sc, verify):
    if part["shape"] != "box" or not part.get("wall"):
        raise ScenarioError("an enclosure test needs a hollow box (a box with a wall)")
    mat = materials.get(part["material"])
    power = _q(sc, "power", "W", None) if sc.get("power") is not None else None
    if power is None:
        inside = _inside(design, part)
        if not inside:
            raise ScenarioError(f"nothing is inside the {part['name'].lower()}, so there is no "
                                "heat to test; give the test a power or put parts inside")
        power = sum(spec(q, "power", "W", "0 W") for q in inside)
    limit = _q(sc, "max_temperature", "K", "60 degC")
    x, y, z = (v / 1e3 for v in part["size"])
    absorb = float(sc.get("absorptivity", (part.get("specs") or {}).get("absorptivity", 0.5)))
    res = _run("enclosure_thermal", {
        "size_x": f"{x} m", "size_y": f"{y} m", "size_z": f"{z} m",
        "wall": f"{part['wall'] / 1e3} m",
        "wall_conductivity": f"{mat['conductivity']} W/(m*K)",
        "internal_power": f"{power} W", "irradiance": sc.get("irradiance", "1000 W/m^2"),
        "absorptivity": absorb, "wind": sc.get("wind", "1 m/s"),
        "ambient_temperature": sc.get("ambient", "35 degC"),
        "sky_temperature": sc.get("sky", "20 degC")}, verify)
    t_in = res.value("internal_temperature")
    amb_c = convert(sc.get("ambient", "35 degC"), "degC")
    metrics = [_metric("air temperature inside", t_in, "degC", limit - 273.15, "max", amb_c)]
    head = (f"{power:.3g} W of electronics in full sun: {t_in:.0f} degC inside the "
            f"{part['name'].lower()} (limit {limit - 273.15:.0f} degC), "
            f"{res.value('absorbed_sun'):.0f} W of it is sunlight")
    return res, metrics, head, [f"solar absorptivity {absorb:g} (paint colour)"], \
        f"Summer sun: {sc.get('ambient', '35 degC')}"


def test_power(design, part, sc, verify):
    mat = materials.get(part["material"])
    if "energy_density" not in mat:
        raise ScenarioError("a power test needs a battery part (material 'lifepo4')")
    capacity = spec(part, "capacity", "J") or volume_mm3(part) * 1e-9 * mat["energy_density"]
    panel = part_by_id(design, sc["panel"]) if sc.get("panel") else None
    rating = 0.0
    if panel is not None:
        pm = materials.get(panel["material"])
        rating = spec(panel, "power", "W") or (
            sorted(extents(panel))[1] * sorted(extents(panel))[2] / 1e6
            * 1000.0 * pm.get("efficiency", 0.2))
    load = (_q(sc, "load", "W") if sc.get("load") is not None else
            sum(spec(part_by_id(design, pid), "power", "W", "0 W") for pid in sc.get("loads", [])))
    days = int(sc.get("days", 3))
    res = _run("solar_battery", {
        "panel_power": f"{max(rating, 1e-6)} W", "battery_capacity": f"{capacity} J",
        "load": f"{load} W", "days": days,
        "peak_irradiance": sc.get("peak_irradiance", "500 W/m^2"),
        "day_length": sc.get("day_length", "10 h"),
        "weather_factor": float(sc.get("weather_factor", 0.35))}, verify)
    reserve = float(sc.get("min_charge", 0.2))
    low = res.value("lowest_charge")
    metrics = [_metric("lowest battery charge", low * 100, "%", reserve * 100, "min"),
               _metric("energy not supplied", res.value("energy_not_supplied"), "Wh", 0.0, "max")]
    head = (f"{days} overcast winter days on a {rating:.0f} W panel and a "
            f"{capacity / 3600:.0f} Wh battery feeding {load:.1f} W: the charge bottoms out at "
            f"{low * 100:.0f}% (needs {reserve * 100:.0f}%)")
    notes = [f"battery capacity {capacity / 3600:.0f} Wh from its size (about 100 Wh per litre "
             "of LiFePO4 pack)" if not (part.get('specs') or {}).get('capacity') else
             "battery capacity from its datasheet",
             f"panel rating {rating:.0f} W" + ("" if (panel or {}).get("specs", {}).get("power")
                                              else " from its area at 20% efficiency")]
    return res, metrics, head, notes, f"Power: {days} cloudy days"


def test_coverage(design, part, sc, verify):
    gain = spec_db(part, "gain", 10.0)
    beam = spec(part, "beamwidth", "rad", "60 deg")
    ftb = spec_db(part, "front_to_back", 15.0)
    notes = []
    if sc.get("screen"):
        try:
            screen = part_by_id(design, sc["screen"])
            extra = spec_db(screen, "rejection", 10.0)
            ftb += extra
            notes.append(f"the {screen['name'].lower()} adds {extra:g} dB of rejection toward "
                         "the transmitter")
        except DesignError:
            notes.append("no screen in the design")
    nf = spec_db(part_by_id(design, sc["receiver"]), "noise_figure", 4.0) \
        if sc.get("receiver") else 4.0
    required = _q(sc, "required_range", "m")
    params = {"tx_erp": sc.get("tx_erp", "1 MW"), "frequency": sc.get("frequency", "569 MHz"),
              "bandwidth": sc.get("bandwidth", "6 MHz"), "baseline": sc.get("baseline", "20 km"),
              "tx_bearing": sc.get("tx_bearing", "180 deg"), "rx_gain_dbi": gain,
              "beamwidth": f"{beam} rad", "front_to_back_db": ftb, "noise_figure_db": nf,
              "losses_db": float(sc.get("losses_db", 8.0)),
              "cancellation_db": float(split_quantity(str(sc.get("cancellation", "30 dB")))[0]),
              "rcs": sc.get("rcs", "0.01 m^2"),
              "integration_time": sc.get("integration_time", "0.5 s"),
              "required_range": f"{required} m", "extent": f"{2.5 * required} m",
              "rx_height": f"{float(np.mean(aabb(part), axis=0)[2]) / 1e3} m",
              "target_altitude": sc.get("target_altitude", "100 m")}
    res = _run("passive_radar", params, verify)
    rng, rise = res.value("detection_range"), res.value("noise_floor_rise")
    metrics = [_metric("detection range straight ahead", rng / 1e3, "km", required / 1e3, "min")]
    limited = ("leaked TV signal, not receiver noise, sets the floor "
               f"(+{rise:.0f} dB)" if rise > 3 else "receiver noise sets the floor")
    head = (f"A {params['rcs'].replace('^2', '²')} target is detected out to {rng / 1e3:.2f} km "
            f"(needs {required / 1e3:.2g} km); {limited}")
    notes += [f"surveillance antenna: {gain:g} dBi, {math.degrees(beam):.0f} deg beam, "
              f"{ftb:g} dB front-to-back",
              f"direct signal suppressed by {params['cancellation_db']:g} dB before detection",
              "free space over flat ground: terrain, multipath and clutter are not modelled"]
    return res, metrics, head, notes, f"Detect at {required / 1e3:.2g} km"


TESTS = {"load": test_load, "drop": test_drop, "vibration": test_vibration,
         "heat": test_heat, "fire": test_fire, "current": test_current, "flow": test_flow,
         "wind": test_wind, "enclosure": test_enclosure, "power": test_power,
         "coverage": test_coverage, "simulation": test_simulation}

DESCRIPTIONS = {
    "load": "a steady force on a part held like a beam: needs force; optional support "
            "(cantilever | simply_supported | fixed_fixed), position (0-1 along the span), "
            "span_axis, load_axis, min_safety_factor (2), max_deflection",
    "drop": "the product falls and lands on this part: needs height; optional mass "
            "(default: whole design), support, min_safety_factor (1)",
    "vibration": "a part shaken at a frequency (motor, fan, engine): needs excitation; "
                 "optional tip_mass, support, margin (1.2)",
    "heat": "steady heat. mode heatsink (part shape heatsink): needs power, optional "
            "max_temperature (85 degC), h. mode hot_end (rod or bar hot at one end): needs "
            "hot_temperature, optional max_temperature (50 degC), h",
    "fire": "a flame or radiant heat on a wall: optional heat_flux (30 kW/m^2), duration "
            "(60 s), criterion (auto | softening), back_face (insulated | convective)",
    "current": "electric current through a conductor: needs current; optional "
               "max_temperature (70 degC), max_voltage_drop, or voltage + max_drop_percent",
    "flow": "liquid or gas through a tube: needs flow_rate; optional fluid (water | air | "
            "oil | glycol), max_pressure_drop (10 kPa), max_velocity",
    "wind": "a gust on a vertical mast or pole and everything it carries: needs speed; "
            "optional carries (part ids on the mast), direction (x | y), min_safety_factor "
            "(1.5), max_deflection (length/40)",
    "enclosure": "a sealed box of electronics in the sun: part must be a box with a wall; "
                 "power defaults to the sum of specs.power of the parts inside; optional "
                 "ambient (35 degC), irradiance (1000 W/m^2), wind (1 m/s), absorptivity "
                 "(0.5), max_temperature (60 degC)",
    "power": "off-grid endurance: part is a battery (material lifepo4); panel (part id of a "
             "solar panel), loads (part ids with specs.power) or load; optional days (3), "
             "peak_irradiance (500 W/m^2), day_length (10 h), weather_factor (0.35), "
             "min_charge (0.2)",
    "coverage": "passive radar detection coverage around a surveillance antenna (part with "
                "specs gain, beamwidth, front_to_back): needs required_range; optional "
                "tx_erp (1 MW), frequency (569 MHz), baseline (20 km), tx_bearing (180 deg), "
                "rcs (0.01 m^2), integration_time (0.5 s), cancellation (30 dB), receiver "
                "(part id with specs noise_figure), screen (part id with specs rejection)",
    "simulation": "any simulation in the catalogue, for situations the types above do not "
                  "cover: needs simulation (template name), bind ({param: source}, at least "
                  "one, tying the run to this part; sources: " + SOURCES_HELP + "), require "
                  "([{qoi, min | max (with unit), optional label, optional from: the ambient "
                  "a temperature rises from}]); optional params (other inputs with units)",
}


def reproduce_command(res) -> str:
    """The exact command that re-runs this simulation on its own."""
    import json as _json
    import shlex
    parts = [f"simulate-anything run {res.name}"]
    for key, value in res.params.items():
        text = value if isinstance(value, str) else _json.dumps(value)
        parts.append(f"-p {shlex.quote(f'{key}={text}')}")
    return " ".join(parts)


def run_scenario(design: dict, sc: dict, verify: bool = True, detail: bool = True) -> dict:
    out = {"id": sc["id"], "type": sc["type"], "part": sc["part"],
           "title": sc.get("title") or sc["type"].capitalize(), "metrics": [], "notes": [],
           "headline": "", "simulation": None, "checks": None}
    fn = TESTS.get(sc["type"])
    try:
        if fn is None:
            raise ScenarioError(f"unknown test type '{sc['type']}' (known: {sorted(TESTS)})")
        part = part_by_id(design, sc["part"])
        res, metrics, head, notes, title = fn(design, part, sc, verify)
    except (ScenarioError, DesignError, KeyError) as exc:
        msg = exc.args[0] if exc.args else str(exc)
        out.update(status="error", headline=str(msg), violation=1e3)
        return out
    out.update(metrics=metrics, headline=head, notes=notes, simulation=res.name,
               title=sc.get("title") or title)
    if not res.ok or not res.qoi:
        failed = "; ".join(f"{c.name}: {c.detail}" for c in res.report.failed()[:3])
        out.update(status="unverified", violation=1e3)
        out["notes"] = notes + [f"the simulation failed its own checks, so this result is "
                                f"not trusted ({failed})"]
    elif all(m["ok"] for m in metrics):
        out.update(status="pass", violation=0.0)
    else:
        out.update(status="fail", violation=_violation(metrics))
    out["checks"] = {"verdict": res.report.verdict, "summary": res.report.summary(),
                     "verified": bool(verify)}
    if sc["type"] == "coverage" and res.qoi:
        c = np.mean(aabb(part_by_id(design, sc["part"])), axis=0)
        out["overlay"] = {"kind": "ground", "field": 0,
                          "threshold": 13.0,
                          "unit": "dB", "center_mm": [float(c[0]), float(c[1])],
                          "source_bearing_deg": float(split_quantity(
                              str(sc.get("tx_bearing", "180 deg")))[0]),
                          "range_m": float(_q(sc, "required_range", "m"))}
    out["load"] = load_of_test(out)
    out["margin"] = margin_of(out["load"])
    out["reproduce"] = reproduce_command(res)
    if detail:
        out["sim"] = res.to_dict(with_fields=True)
    return out


def evaluate(design: dict, verify: bool = True, detail: bool = True) -> dict:
    """Run every real-world test of a (normalised) design."""
    return summarise([run_scenario(design, sc, verify, detail) for sc in design["scenarios"]])


def summarise(tests: list) -> dict:
    """Evaluation record for a list of test results."""
    counts = {s: sum(t["status"] == s for t in tests)
              for s in ("pass", "fail", "unverified", "error")}
    loaded = [t for t in tests if t.get("load") is not None]
    worst = max(loaded, key=lambda t: t["load"]) if loaded else None
    return {"tests": tests, "counts": counts,
            "all_pass": bool(tests) and counts["pass"] == len(tests),
            "violation": sum(t["violation"] for t in tests),
            "load": worst["load"] if worst else None,
            "margin": margin_of(worst["load"]) if worst else None,
            "limiting": worst["id"] if worst else None}
