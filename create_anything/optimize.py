"""Optimise a design against its real-world tests.

The loop is deliberately simple and fully checked:

1. Run every test.  If any fail, generate candidate changes to the parts
   involved (thicker, wider, longer, more fins, a different material).
2. Re-run all the tests on every candidate.  Keep the candidate that fixes
   the most, by the least added mass.  A change is never accepted on a
   promise: only its measured effect counts.
3. Once everything passes, shave material back off while the tests still pass.
4. Re-run the final design with full verification (grid study, known
   solutions, conservation checks).  It is only reported as optimised if
   every test passes on verified simulations.
"""
from __future__ import annotations

import copy

from . import materials
from .design import extents, part_by_id, total_mass_kg
from .scenarios import beam_axes, evaluate

GROW = (1.15, 1.3, 1.6, 2.0)
SHRINK = (0.8, 0.9, 0.95)
MAX_SCALE, MIN_SCALE = 4.0, 0.35
STRUCTURAL = ("load", "drop", "vibration")
METALS_FOR_HEAT = ("aluminium", "steel", "stainless", "copper")
AXIS_NAME = "xyz"


def _variables(design: dict, sc: dict) -> list:
    """Dimensions of the tested part that this kind of test responds to:
    (label, [(key, index-or-None), ...])."""
    part = part_by_id(design, sc["part"])
    shape, kind = part["shape"], sc["type"]
    out = []
    if kind in STRUCTURAL:
        if shape == "box":
            span, load, side = beam_axes(part, sc)
            out += [("thickness", [("size", load)]), ("width", [("size", side)])]
            if part.get("wall"):
                out.append(("wall", [("wall", None)]))
        elif shape == "cylinder":
            out.append(("radius", [("radius", None)]))
        elif shape == "tube":
            out.append(("diameter", [("radius", None), ("inner_radius", None)]))
    elif kind == "heat":
        if shape == "heatsink":
            out += [("fin height", [("fin_height", None)]), ("fin count", [("n_fins", None)])]
        elif shape == "box":
            span = beam_axes(part, sc)[0]
            out.append(("length", [("size", span)]))
        elif shape in ("cylinder", "tube"):
            out.append(("length", [("length", None)]))
    elif kind == "fire":
        if part.get("wall"):
            out.append(("wall", [("wall", None)]))
        elif shape == "box":
            e = extents(part)
            out.append(("thickness", [("size", e.index(min(e)))]))
        elif shape == "tube":
            out.append(("wall", [("radius", "wall")]))
        elif shape == "cylinder":
            out.append(("radius", [("radius", None)]))
    elif kind == "current":
        if shape == "cylinder":
            out.append(("radius", [("radius", None)]))
        elif shape == "box":
            e = extents(part)
            span = e.index(max(e))
            out += [(f"section {AXIS_NAME[a]}", [("size", a)]) for a in range(3) if a != span]
    elif kind == "flow" and shape == "tube":
        out.append(("bore", [("radius", None), ("inner_radius", None)]))
    return out


def _get(part, key, idx):
    if idx == "wall":
        return part["radius"] - part["inner_radius"]
    return part[key] if idx is None else part[key][idx]


def _scaled(design, original, pid, label, targets, factor):
    """Copy of the design with one dimension scaled, or None if out of range."""
    new = copy.deepcopy(design)
    part, ref = part_by_id(new, pid), part_by_id(original, pid)
    before = _get(part, *targets[0])
    for key, idx in targets:
        if key == "n_fins":
            step = max(1, round(abs(factor - 1) * part["n_fins"]))
            part["n_fins"] = part["n_fins"] + (step if factor > 1 else -step)
            if part["n_fins"] < 1 or part["n_fins"] * part["fin_thickness"] > 0.6 * part["base"][0]:
                return None
        elif idx == "wall":
            part["radius"] = part["inner_radius"] + before * factor
        elif idx is None:
            part[key] = part[key] * factor
        else:
            part[key][idx] = part[key][idx] * factor
        ratio = _get(part, key, idx) / _get(ref, key, idx)
        if not MIN_SCALE <= ratio <= MAX_SCALE:
            return None
    if part.get("wall") and 2 * part["wall"] >= min(part["size"]):
        return None
    after = _get(part, *targets[0])
    unit = "" if targets[0][0] == "n_fins" else " mm"
    return new, f"{part['name']}: {label} {before:.3g} -> {after:.3g}{unit}"


def _material_options(part: dict, sc: dict) -> list:
    if part.get("material_options"):
        keys = [materials.get(k)["id"] for k in part["material_options"]]
    else:
        fam = materials.get(part["material"])["family"]
        keys = [k for k, m in materials.MATERIALS.items() if m["family"] == fam]
        if sc["type"] in ("fire", "heat", "current"):
            keys += [k for k in METALS_FOR_HEAT if k not in keys]
    if sc["type"] == "current":
        keys = [k for k in keys if materials.get(k)["resistivity"] is not None]
    return [k for k in keys if k != part["material"]]


def _candidates(design, original, test) -> list:
    sc = next(s for s in design["scenarios"] if s["id"] == test["id"])
    part = part_by_id(design, sc["part"])
    if part.get("locked"):
        return []
    out = []
    for label, targets in _variables(design, sc):
        for f in GROW:
            made = _scaled(design, original, part["id"], label, targets, f)
            if made:
                out.append(made)
    for key in _material_options(part, sc):
        new = copy.deepcopy(design)
        target = part_by_id(new, part["id"])
        old = materials.get(target["material"])["name"]
        target["material"] = key
        out.append((new, f"{part['name']}: {old} -> {materials.get(key)['name']}"))
    return out


def _score(design):
    ev = evaluate(design, verify=False, detail=False)
    bad = sum(t["status"] != "pass" for t in ev["tests"])
    return (bad, round(ev["violation"], 9), total_mass_kg(design)), ev


def _statuses(ev):
    return {t["id"]: t["status"] for t in ev["tests"]}


def optimise(design: dict, max_rounds: int = 12, slim: bool = True) -> dict:
    """Returns {"design", "steps", "before", "after", "improved", "all_pass", ...}.

    ``design`` must already be normalised.  ``before`` and ``after`` are full,
    verified evaluations."""
    original = copy.deepcopy(design)
    current = copy.deepcopy(design)
    before = evaluate(original, verify=True, detail=True)
    score, ev = _score(current)
    steps, stuck = [], None

    for _ in range(max_rounds):
        failing = [t for t in ev["tests"] if t["status"] != "pass"]
        if not failing:
            break
        worst = max(failing, key=lambda t: t["violation"])
        options = []
        for t in sorted(failing, key=lambda t: -t["violation"]):
            options += _candidates(current, original, t)
        best = None
        for cand, label in options:
            s, e = _score(cand)
            if best is None or s < best[0]:
                best = (s, e, cand, label)
        if best is None or best[0][:2] >= score[:2]:
            stuck = worst
            break
        steps.append({"change": best[3], "reason": worst["headline"], "kind": "fix",
                      "mass_g": [score[2] * 1e3, best[0][2] * 1e3],
                      "tests": _statuses(best[1])})
        score, ev, current = best[0], best[1], best[2]

    if slim and score[0] == 0:                      # everything passes: remove excess
        tested = {}
        for sc in current["scenarios"]:
            if part_by_id(current, sc["part"]).get("locked"):
                continue
            for label, targets in _variables(current, sc):
                if targets[0][0] != "n_fins":
                    tested[(sc["part"], label)] = targets
        for (pid, label), targets in tested.items():
            start = _get(part_by_id(current, pid), *targets[0])
            mass0 = score[2]
            for f in SHRINK:
                for _ in range(8):
                    made = _scaled(current, original, pid, label, targets, f)
                    if not made:
                        break
                    s, e = _score(made[0])
                    if s[0] != 0 or s[2] >= score[2]:
                        break
                    score, ev, current = s, e, made[0]
            end = _get(part_by_id(current, pid), *targets[0])
            if abs(end - start) > 1e-9 * start:
                name = part_by_id(current, pid)["name"]
                steps.append({"change": f"{name}: {label} {start:.3g} -> {end:.3g} mm",
                              "reason": "every test still passes with less material",
                              "kind": "trim", "mass_g": [mass0 * 1e3, score[2] * 1e3],
                              "tests": _statuses(ev)})

    after = evaluate(current, verify=True, detail=True)
    m0, m1 = total_mass_kg(original) * 1e3, total_mass_kg(current) * 1e3
    note = ""
    if not after["all_pass"]:
        if stuck is not None:
            note = (f"No change to size or material within the allowed range fixes: "
                    f"{stuck['headline']}. It needs a different design idea.")
        else:
            note = "The final design does not pass every test on verified simulations."
    return {"design": current, "steps": steps, "before": before, "after": after,
            "all_pass": after["all_pass"], "changed": bool(steps),
            "mass_g": [m0, m1], "note": note}
