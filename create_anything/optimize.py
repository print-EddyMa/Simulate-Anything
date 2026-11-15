"""Optimise a design against its real-world tests.

The loop is deliberately simple and fully checked:

1. Run every test.  If any fail, generate candidate changes to the parts
   involved (thicker, wider, longer, more fins, a bigger battery, a different
   material) and to any scenario inputs the design marks as tunable.
2. Re-run all the tests on every candidate.  Keep the candidate that fixes the
   most, by the least added mass.  A change is never accepted on a promise:
   only its measured effect counts.
3. Once everything passes, shave material back off while the tests still pass.
4. Re-run the final design with full verification (grid study, known
   solutions, conservation checks).  It is only reported as optimised if every
   test passes on verified simulations.

``optimise_steps`` yields progress events as it goes, for a live interface.
"""
from __future__ import annotations

import copy

from simulate_anything.units import split_quantity

from . import materials
from .design import contains, extents, part_by_id, total_mass_kg
from .scenarios import beam_axes, evaluate

GROW = (1.15, 1.3, 1.6, 2.0)
BOTH = (0.4, 0.6, 0.8, 1.25, 1.6)          # tunable inputs may move either way
SHRINK = (0.8, 0.9, 0.95)
TRIM_MIN_MARGIN = 0.05     # trimming stops before any test's margin of safety drops below 5%
TRIM_MAX_LOAD = 1.0 / (1.0 + TRIM_MIN_MARGIN)
MAX_SCALE, MIN_SCALE = 4.0, 0.35
STRUCTURAL = ("load", "drop", "vibration", "wind")
METALS_FOR_HEAT = ("aluminium", "steel", "stainless", "copper")
AXIS_NAME = "xyz"
ALL3 = [("size", 0), ("size", 1), ("size", 2)]


def _part_variables(part: dict, sc: dict) -> list:
    shape, kind = part["shape"], sc["type"]
    out = []
    if kind in STRUCTURAL:
        if shape == "box":
            span, load, side = beam_axes(part, {**sc, "span_axis": "z"} if kind == "wind" else sc)
            out += [("thickness", [("size", load)]), ("width", [("size", side)])]
            if part.get("wall"):
                out.append(("wall", [("wall", None)]))
        elif shape == "cylinder":
            out.append(("radius", [("radius", None)]))
        elif shape == "tube":
            out += [("outer radius", [("radius", None), ("inner_radius", None)]),
                    ("wall", [("radius", "wall")])]
    elif kind == "heat":
        if shape == "heatsink":
            out += [("fin height", [("fin_height", None)]), ("fin count", [("n_fins", None)])]
        elif shape == "box":
            out.append(("length", [("size", beam_axes(part, sc)[0])]))
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
    elif kind in ("enclosure", "power") and shape == "box":
        out.append(("size", ALL3))
    return out


def _variables(design: dict, sc: dict) -> list:
    """(owner id, label, targets) that this test responds to.  Owner is a part
    id, or '@<scenario id>' for a tunable scenario input."""
    out = []
    part = part_by_id(design, sc["part"])
    if not part.get("locked"):
        out += [(part["id"], lab, t) for lab, t in _part_variables(part, sc)]
    if sc["type"] == "power" and sc.get("panel"):
        panel = part_by_id(design, sc["panel"])
        if not panel.get("locked") and panel["shape"] == "box":
            e = extents(panel)
            thin = e.index(min(e))
            out.append((panel["id"], "panel area", [("size", a) for a in range(3) if a != thin]))
    for key in (sc.get("tune") or {}):
        out.append((f"@{sc['id']}", key.replace("_", " "), [(key, "scenario")]))
    return out


def _owner(design, owner):
    if owner.startswith("@"):
        sid = owner[1:]
        return next(s for s in design["scenarios"] if s["id"] == sid)
    return part_by_id(design, owner)


def _get(obj, key, idx):
    if idx == "wall":
        return obj["radius"] - obj["inner_radius"]
    if idx == "scenario":
        return float(split_quantity(str(obj[key]))[0])
    return obj[key] if idx is None else obj[key][idx]


def _scaled(design, original, owner, label, targets, factor):
    """Copy of the design with one variable scaled, or None if out of range."""
    new = copy.deepcopy(design)
    obj, ref = _owner(new, owner), _owner(original, owner)
    before = _get(obj, *targets[0])
    unit = " mm"
    for key, idx in targets:
        if idx == "scenario":
            num, u = split_quantity(str(obj[key]))
            lo, hi = (float(split_quantity(str(v))[0]) for v in obj["tune"][key])
            val = num * factor
            if not lo <= val <= hi:
                return None
            obj[key] = f"{val:.6g} {u}".strip()
            unit = f" {u}" if u else ""
            continue
        if key == "n_fins":
            step = max(1, round(abs(factor - 1) * obj["n_fins"]))
            obj["n_fins"] = obj["n_fins"] + (step if factor > 1 else -step)
            if obj["n_fins"] < 1 or obj["n_fins"] * obj["fin_thickness"] > 0.6 * obj["base"][0]:
                return None
            unit = ""
            continue
        if idx == "wall":
            obj["radius"] = obj["inner_radius"] + before * factor
        elif idx is None:
            obj[key] = obj[key] * factor
        else:
            obj[key][idx] = obj[key][idx] * factor
        ratio = _get(obj, key, idx) / _get(ref, key, idx)
        if not MIN_SCALE <= ratio <= MAX_SCALE:
            return None
    if obj.get("wall") and 2 * obj["wall"] >= min(obj["size"]):
        return None
    if not owner.startswith("@") and obj.get("wall"):
        # a housing must still hold everything it held in the original design
        for q in original["parts"]:
            if q["id"] != owner and contains(ref, q):
                if not contains(obj, part_by_id(new, q["id"])):
                    return None
    after = _get(obj, *targets[0])
    name = obj.get("name") or obj.get("title") or owner
    if owner.startswith("@"):
        name = obj.get("title") or obj["id"]
        nice = (obj.get("tune_labels") or {}).get(targets[0][0])
        if nice:
            return new, f"{nice}: {before:.3g} -> {after_value(obj, targets):.3g}"
    if len(targets) == 3 and all(k == "size" for k, _ in targets):
        return new, f"{name}: scaled x{factor:.2f} ({'x'.join(f'{v:.0f}' for v in obj['size'])} mm)"
    return new, f"{name}: {label} {before:.3g} -> {after:.3g}{unit}"


def after_value(obj, targets):
    return _get(obj, *targets[0])


def _material_options(part: dict, sc: dict) -> list:
    if part.get("material_options"):
        keys = [materials.get(k)["id"] for k in part["material_options"]]
    else:
        fam = materials.get(part["material"])["family"]
        if fam in ("battery", "solar", "electronics"):
            return []
        keys = [k for k, m in materials.MATERIALS.items() if m["family"] == fam]
        if sc["type"] in ("fire", "heat", "current"):
            keys += [k for k in METALS_FOR_HEAT if k not in keys]
    if sc["type"] == "current":
        keys = [k for k in keys if materials.get(k)["resistivity"] is not None]
    return [k for k in keys if k != part["material"]]


def _candidates(design, original, test) -> list:
    sc = next(s for s in design["scenarios"] if s["id"] == test["id"])
    out = []
    for owner, label, targets in _variables(design, sc):
        for f in (BOTH if owner.startswith("@") else GROW):
            made = _scaled(design, original, owner, label, targets, f)
            if made:
                out.append(made)
    part = part_by_id(design, sc["part"])
    if not part.get("locked") and sc["type"] not in ("coverage", "power"):
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


def _loads(ev):
    return {t["id"]: (t.get("load") or 0.0) for t in ev["tests"]}


def _keeps_margin(new_ev, old_ev):
    """A trim may not cut any test's margin below 5% (unless it already was)."""
    old = _loads(old_ev)
    return all(v <= max(TRIM_MAX_LOAD, old.get(k, 0.0)) + 1e-12
               for k, v in _loads(new_ev).items())


def _statuses(ev):
    return {t["id"]: t["status"] for t in ev["tests"]}


def _changed_parts(a, b):
    ids = []
    for p in b["parts"]:
        try:
            if part_by_id(a, p["id"]) != p:
                ids.append(p["id"])
        except Exception:
            ids.append(p["id"])
    return ids


def optimise_steps(design: dict, max_rounds: int = 12, slim: bool = True, before=None):
    """Generator: yields ("change", step_dict, design_after) for each accepted
    change, then ("result", result_dict, final_design)."""
    original = copy.deepcopy(design)
    current = copy.deepcopy(design)
    if before is None:
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
            if t["status"] == "error":
                continue
            options += _candidates(current, original, t)
        best = None
        for cand, label in options:
            s, e = _score(cand)
            if best is None or s < best[0]:
                best = (s, e, cand, label)
        if best is None or best[0][:2] >= score[:2]:
            stuck = worst
            break
        step = {"change": best[3], "reason": worst["headline"], "kind": "fix",
                "mass_g": [score[2] * 1e3, best[0][2] * 1e3], "tests": _statuses(best[1]),
                "parts": _changed_parts(current, best[2])}
        steps.append(step)
        score, ev, current = best[0], best[1], best[2]
        yield "change", step, current

    if slim and score[0] == 0:                      # everything passes: remove excess
        seen = {}
        for sc in current["scenarios"]:
            for owner, label, targets in _variables(current, sc):
                if targets[0][0] != "n_fins" and not owner.startswith("@"):
                    seen[(owner, label)] = targets
        for (owner, label), targets in seen.items():
            start_design = current
            mass0 = score[2]
            for f in SHRINK:
                for _ in range(8):
                    made = _scaled(current, original, owner, label, targets, f)
                    if not made:
                        break
                    s, e = _score(made[0])
                    if s[0] != 0 or s[2] >= score[2] or not _keeps_margin(e, ev):
                        break
                    score, ev, current = s, e, made[0]
            if current is not start_design:
                obj0, obj1 = _owner(start_design, owner), _owner(current, owner)
                a, b = _get(obj0, *targets[0]), _get(obj1, *targets[0])
                label_txt = (f"{obj1['name']}: {label} {a:.3g} -> {b:.3g} mm"
                             if len(targets) < 3 else
                             f"{obj1['name']}: scaled to "
                             f"{'x'.join(f'{v:.0f}' for v in obj1['size'])} mm")
                step = {"change": label_txt, "reason": "every test still passes with less "
                        "material", "kind": "trim", "mass_g": [mass0 * 1e3, score[2] * 1e3],
                        "tests": _statuses(ev), "parts": [owner]}
                steps.append(step)
                yield "change", step, current

    after = evaluate(current, verify=True, detail=True)
    m0, m1 = total_mass_kg(original) * 1e3, total_mass_kg(current) * 1e3
    note = ""
    if not after["all_pass"]:
        if stuck is not None:
            note = (f"No change to size or material within the allowed range fixes: "
                    f"{stuck['headline']}. It needs a different design idea.")
        else:
            note = "The final design does not pass every test on verified simulations."
    yield "result", {"design": current, "steps": steps, "before": before, "after": after,
                     "all_pass": after["all_pass"], "changed": bool(steps),
                     "mass_g": [m0, m1], "note": note}, current


def optimise(design: dict, max_rounds: int = 12, slim: bool = True) -> dict:
    """Run the whole loop and return the result dict."""
    out = None
    for kind, payload, _ in optimise_steps(design, max_rounds, slim):
        if kind == "result":
            out = payload
    return out
