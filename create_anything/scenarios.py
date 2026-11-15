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
from simulate_anything.units import _NUM, DimensionError, convert

from . import materials
from .design import (AXES, PROFILE_AXES, DesignError, extents, part_by_id, profile_section,
                     total_mass_kg, volume_mm3)

G0 = 9.80665
FLUIDS = {"water": (998.0, 1.0e-3), "air": (1.2, 1.8e-5), "oil": (870.0, 0.1),
          "glycol": (1070.0, 4.0e-3)}


class ScenarioError(ValueError):
    """The scenario cannot be applied to this part."""


def _q(sc, key, unit, default=None, delta=False):
    value = sc.get(key, default)
    if value is None:
        raise ScenarioError(f"scenario '{sc['id']}' needs '{key}' (for example \"5 {unit}\")")
    if not _has_unit(value):
        if value not in (0, "0"):            # a bare number: which unit? never guess
            raise ScenarioError(f"scenario '{sc['id']}', '{key}': give a unit, for example "
                                f"\"{value} {unit}\"")
    try:
        return convert(value, unit, delta=delta)
    except DimensionError as exc:
        raise ScenarioError(f"scenario '{sc['id']}', '{key}': {exc}") from None


def _has_unit(value) -> bool:
    if not isinstance(value, str):
        return False
    m = _NUM.match(value.strip())
    return bool(m and m.group(2).strip())


MAX_LOAD = 99.0          # cap so a load is always a finite JSON number


def load_of(value, limit, kind, base=0.0):
    """How much of its allowance a result uses: 1 means exactly at the limit (the
    load line), 0.5 half of it, 2 twice over.  None when it has no meaning.

    A maximum is measured from ``base`` (for a temperature, the ambient it rises
    from: 70 degC against an 80 degC limit in 25 degC air is 82 %, not 88 %).
    A minimum such as a safety factor or a natural frequency is required/actual:
    a safety factor of 4 where 2 is required uses half the allowance."""
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


def _metric(label, value, unit, limit, kind, base=0.0):
    ok = value >= limit if kind == "min" else value <= limit
    return {"label": label, "value": float(value), "unit": unit, "limit": float(limit),
            "kind": kind, "ok": bool(ok), "base": float(base),
            "load": load_of(float(value), float(limit), kind, float(base))}


def load_of_test(t: dict):
    """The load of a test: its most loaded metric (None if it has no trusted number)."""
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
    """(span, load, side) axis indices of a box or mesh treated as a beam.

    The span is ``span_axis`` if given, else the longest side.  The load acts
    along ``load_axis`` if given, else straight down (z), unless the span is
    vertical, in which case it bends about the thinner of the other two sides."""
    e = extents(part)
    if sc.get("span_axis") in AXES:
        span = AXES[sc["span_axis"]]
    elif part["shape"] == "extrusion":
        span = AXES[part["axis"]]
    else:
        span = int(np.argmax(e))
    rest = [a for a in range(3) if a != span]
    if sc.get("load_axis") in AXES and AXES[sc["load_axis"]] != span:
        load = AXES[sc["load_axis"]]
    else:
        load = 2 if span != 2 else min(rest, key=lambda a: e[a])
    side = next(a for a in rest if a != load)
    return span, load, side


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
    if shape == "extrusion":
        u, v, along = PROFILE_AXES[part["axis"]]
        span, load, _ = beam_axes(part, sc)
        if span != along:
            raise ScenarioError("an extrusion is tested as a beam along its own axis; "
                                "drop span_axis or set it to the extrusion axis")
        ps = profile_section(part)
        inertia, c = ((ps["I_about_u"], ps["c_v"]) if load == v else
                      (ps["I_about_v"], ps["c_u"]))
        height = 2 * c                       # same outer-fibre distance -> same stress
        return (part["length"] / 1e3, 12 * inertia / height ** 3 / 1e3, height / 1e3,
                "profile modelled as the rectangle with the same second moment of area "
                "and the same outer-fibre distance from the centroid")
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
    if part["shape"] == "extrusion":
        raise ScenarioError("give the extrusion a 'wall' (its thinnest wall in mm) so heat "
                            "through it can be modelled")
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
    weight = mat["density"] * G0 * volume_mm3(part) * 1e-9 / length      # self-weight, N/m
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
    weight = f"{mass * 1e3:.3g} g" if mass < 1 else f"{mass:.3g} kg"
    head = (f"{weight} dropped {drop:.2g} m onto the {part['name'].lower()}: peak "
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
    elif part["shape"] == "extrusion":
        pts = np.array(part["profile"])
        area = profile_section(part)["area"]
        perim = float(np.sum(np.linalg.norm(np.roll(pts, -1, axis=0) - pts, axis=1)))
        side = math.sqrt(area)
        params.update(fin_length=f"{part['length']} mm", fin_thickness=f"{side} mm",
                      fin_width=f"{side} mm", h=f"{h * perim / (4 * side)} W/(m^2*K)",
                      tip="adiabatic")
        notes.append("profile modelled as the square bar of equal area, with the film "
                     "coefficient scaled so heat loss per length is exact")
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
    elif part["shape"] in ("box", "tube", "extrusion"):
        span = (AXES[part["axis"]] if part["shape"] in ("tube", "extrusion")
                else int(np.argmax(e)))
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
                  "fin_thickness", "n_fins", "profile_u", "profile_v")
SOURCES_HELP = ("size.0 | size.1 | size.2 (or size.x/y/z), radius, inner_radius, length, "
                "wall, base.0-2, fin_height, fin_thickness, n_fins, profile_u, profile_v "
                "(extrusion profile width/height), part.mass, part.volume, design.mass, "
                "or material.<" + " | ".join(MATERIAL_UNITS) + ">")


def parse_source(source: str):
    """'size.2' -> ('size', 2); 'wall' -> ('wall', None); 'material.x' -> ('material', 'x')."""
    src = str(source).strip()
    key, _, idx = src.partition(".")
    if key in ("material", "part", "design"):
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
    from .design import profile_extents
    key, idx = parse_source(source)
    mat = materials.get(part["material"])
    if key == "material":
        if idx not in MATERIAL_UNITS:
            raise ScenarioError(f"unknown material property '{idx}' "
                                f"(known: {sorted(MATERIAL_UNITS)})")
        if mat[idx] is None:
            raise ScenarioError(f"{mat['name']} has no {idx.replace('_', ' ')}")
        return f"{mat[idx]} {MATERIAL_UNITS[idx]}"
    if key == "part":
        if idx == "mass":
            return f"{volume_mm3(part) * 1e-9 * mat['density']} kg"
        if idx == "volume":
            return f"{volume_mm3(part) * 1e-9} m^3"
        raise ScenarioError(f"unknown part property '{idx}' (use part.mass or part.volume)")
    if key == "design":
        if idx == "mass":
            return f"{total_mass_kg(design)} kg"
        raise ScenarioError(f"unknown design property '{idx}' (use design.mass)")
    if key in ("profile_u", "profile_v"):
        if part["shape"] != "extrusion":
            raise ScenarioError(f"'{key}' only applies to an extrusion")
        return f"{profile_extents(part)[0 if key == 'profile_u' else 1]} mm"
    if key not in part:
        raise ScenarioError(f"part '{part['id']}' ({part['shape']}) has no '{key}'")
    value = part[key] if idx is None else part[key][idx]
    return int(value) if key == "n_fins" else f"{value} mm"


AMBIENT_HINTS = ("ambient", "inlet", "coolant", "outside", "air_temperature",
                 "fluid_temperature", "initial_temperature")


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
        known = sorted(s["name"] for s in sa.catalog())
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


TESTS = {"load": test_load, "drop": test_drop, "vibration": test_vibration,
         "heat": test_heat, "fire": test_fire, "current": test_current, "flow": test_flow,
         "simulation": test_simulation}

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
    "simulation": "any simulation in the Simulate Anything catalogue, for situations the "
                  "types above do not cover: needs simulation (template name), bind "
                  "({param: source} - at least one, tying the run to this part; sources: "
                  + SOURCES_HELP + "), require ([{qoi, min | max (with unit), optional "
                  "from: the ambient a temperature rises from}]); optional params (other "
                  "inputs with units), title",
}


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
    except (ScenarioError, DesignError, KeyError, sa.ParamError, DimensionError) as exc:
        msg = exc.args[0] if exc.args else str(exc)
        out.update(status="error", headline=str(msg), violation=1e3)
        return out
    except Exception as exc:          # a solver blew up: report it, never crash the design
        out.update(status="error", headline=f"the simulation crashed: {type(exc).__name__}: "
                                            f"{exc}", violation=1e3)
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
    if detail:
        out["sim"] = res.to_dict(with_fields=True)
    return out


def evaluate(design: dict, verify: bool = True, detail: bool = True) -> dict:
    """Run every real-world test of a (normalised) design."""
    tests = [run_scenario(design, sc, verify, detail) for sc in design["scenarios"]]
    counts = {s: sum(t["status"] == s for t in tests)
              for s in ("pass", "fail", "unverified", "error")}
    for t in tests:
        t["load"] = load_of_test(t)
    loaded = [t for t in tests if t["load"] is not None]
    worst = max(loaded, key=lambda t: t["load"]) if loaded else None
    return {"tests": tests, "counts": counts,
            "all_pass": bool(tests) and counts["pass"] == len(tests),
            "violation": sum(t["violation"] for t in tests),
            "load": worst["load"] if worst else None,
            "limiting": worst["id"] if worst else None}
