"""The design document: what a thing is made of and how it goes together.

A design is plain JSON so a person, the web app and the language model can
all read and edit the same thing:

    {"name": ..., "description": ...,
     "parts": [{"id", "name", "shape", <dimensions in mm>, "position": [x, y, z],
                "rotation": [rx, ry, rz], "material", "process", "quantity", "purpose"}],
     "assembly": [{"parts": [ids], "action": "what you do in this step"}],
     "scenarios": [{"id", "type", "part", ...}]}        # real-world tests

Dimensions are millimetres, z is up, ``position`` is the centre of the part's
bounding box.  Shapes: box (size [x, y, z], optional wall for a hollow shell),
cylinder (radius, length, axis), tube (radius, inner_radius, length, axis),
sphere (radius), heatsink (base [w, d, t], fin_height, fin_thickness, n_fins)
and mesh (an imported triangle mesh).

Everything here is deterministic geometry and bookkeeping.  It is the first
gate a proposed design has to pass, before any physics is run.
"""
from __future__ import annotations

import copy
import math

import numpy as np

from . import materials

SHAPES = ("box", "cylinder", "tube", "sphere", "heatsink", "yagi", "mesh")
LOOKS = ("pcb", "solar", "battery", "screen", "glass", "rubber", "cable", "bowtie",
         "wire_mesh", "laptop", "tripod")
# open frames: their bounding box is mostly air, so mass and overlap use a fill factor
OPEN_LOOKS = {"bowtie": 0.012, "wire_mesh": 0.004, "tripod": 0.0012}
AXES = {"x": 0, "y": 1, "z": 2}
FASTENER_HINTS = ("screw", "bolt", "nut", "rivet", "pin", "fastener", "washer")


class DesignError(ValueError):
    """The design document is malformed."""


def _num(part, key, where):
    try:
        v = float(part[key])
    except KeyError:
        raise DesignError(f"{where}: missing '{key}'") from None
    except (TypeError, ValueError):
        raise DesignError(f"{where}: '{key}' must be a number in mm") from None
    if not math.isfinite(v) or v <= 0:
        raise DesignError(f"{where}: '{key}' must be positive")
    return v


def _vec(value, where, key, positive=False):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise DesignError(f"{where}: '{key}' must be three numbers")
    try:
        out = [float(v) for v in value]
    except (TypeError, ValueError):
        raise DesignError(f"{where}: '{key}' must be three numbers") from None
    if positive and min(out) <= 0:
        raise DesignError(f"{where}: every entry of '{key}' must be positive")
    return out


def normalise(design: dict) -> dict:
    """Validate the structure and fill in defaults.  Raises DesignError."""
    if not isinstance(design, dict):
        raise DesignError("a design must be a JSON object")
    d = copy.deepcopy(design)
    d["name"] = str(d.get("name") or "Untitled design")
    d["description"] = str(d.get("description") or "")
    parts = d.get("parts")
    if not isinstance(parts, list) or not parts:
        raise DesignError("a design needs at least one part")
    seen = set()
    for i, p in enumerate(parts):
        if not isinstance(p, dict):
            raise DesignError(f"part {i + 1} must be an object")
        p["id"] = str(p.get("id") or f"part{i + 1}")
        where = f"part '{p['id']}'"
        if p["id"] in seen:
            raise DesignError(f"{where}: duplicate id")
        seen.add(p["id"])
        p["name"] = str(p.get("name") or p["id"])
        shape = p.get("shape")
        if shape not in SHAPES:
            raise DesignError(f"{where}: shape must be one of {list(SHAPES)}")
        if shape == "box":
            p["size"] = _vec(p.get("size"), where, "size", positive=True)
            if p.get("wall") is not None:
                p["wall"] = _num(p, "wall", where)
                if 2 * p["wall"] >= min(p["size"]):
                    raise DesignError(f"{where}: wall is thicker than half the smallest side")
        elif shape in ("cylinder", "tube"):
            p["radius"] = _num(p, "radius", where)
            p["length"] = _num(p, "length", where)
            p["axis"] = str(p.get("axis") or "z").lower()
            if p["axis"] not in AXES:
                raise DesignError(f"{where}: axis must be x, y or z")
            if shape == "tube":
                p["inner_radius"] = _num(p, "inner_radius", where)
                if p["inner_radius"] >= p["radius"]:
                    raise DesignError(f"{where}: inner_radius must be smaller than radius")
        elif shape == "sphere":
            p["radius"] = _num(p, "radius", where)
        elif shape == "heatsink":
            p["base"] = _vec(p.get("base"), where, "base", positive=True)
            p["fin_height"] = _num(p, "fin_height", where)
            p["fin_thickness"] = _num(p, "fin_thickness", where)
            p["n_fins"] = int(_num(p, "n_fins", where))
            if p["n_fins"] * p["fin_thickness"] > p["base"][0]:
                raise DesignError(f"{where}: the fins do not fit across the base")
        elif shape == "yagi":
            for key in ("boom_length", "element_length"):
                p[key] = _num(p, key, where)
            p["n_elements"] = int(_num(p, "n_elements", where))
            p["boom_radius"] = float(p.get("boom_radius") or 6.0)
            p["element_radius"] = float(p.get("element_radius") or 3.0)
            p["axis"] = str(p.get("axis") or "x").lower()
            default_el = "z" if p["axis"] != "z" else "x"
            p["element_axis"] = str(p.get("element_axis") or default_el).lower()
            if p["axis"] not in AXES or p["element_axis"] not in AXES \
                    or p["axis"] == p["element_axis"]:
                raise DesignError(f"{where}: axis and element_axis must be two different "
                                  "of x, y, z")
        elif shape == "mesh":
            m = p.get("mesh")
            if not isinstance(m, dict) or "size" not in m or "volume" not in m:
                raise DesignError(f"{where}: mesh parts need mesh.size and mesh.volume")
        p["position"] = _vec(p.get("position", [0, 0, 0]), where, "position")
        p["rotation"] = _vec(p.get("rotation", [0, 0, 0]), where, "rotation")
        try:
            p["material"] = materials.get(p.get("material", "pla"))["id"]
        except KeyError as exc:
            raise DesignError(f"{where}: {exc.args[0]}") from None
        p["quantity"] = max(1, int(p.get("quantity") or 1))
        p["process"] = str(p.get("process") or materials.get(p["material"])["processes"][0])
        p["purpose"] = str(p.get("purpose") or "")
        p["group"] = str(p.get("group") or "")
        if p.get("look") is not None and p["look"] not in LOOKS:
            raise DesignError(f"{where}: look must be one of {list(LOOKS)}")
        specs = p.get("specs") or {}
        if not isinstance(specs, dict):
            raise DesignError(f"{where}: specs must be an object of name: value")
        p["specs"] = {str(k): str(v) for k, v in specs.items()}
        fill = p.get("fill", OPEN_LOOKS.get(p.get("look"), 1.0))
        try:
            p["fill"] = float(fill)
        except (TypeError, ValueError):
            raise DesignError(f"{where}: fill must be a number between 0 and 1") from None
        if not 0 < p["fill"] <= 1:
            raise DesignError(f"{where}: fill must be between 0 and 1")
    steps = d.get("assembly") or []
    if not isinstance(steps, list):
        raise DesignError("'assembly' must be a list of steps")
    for i, s in enumerate(steps):
        if not isinstance(s, dict) or not s.get("action"):
            raise DesignError(f"assembly step {i + 1} needs an 'action'")
        s["parts"] = [str(x) for x in (s.get("parts") or [])]
        for pid in s["parts"]:
            if pid not in seen:
                raise DesignError(f"assembly step {i + 1} refers to unknown part '{pid}'")
    d["assembly"] = steps
    scenarios = d.get("scenarios") or []
    if not isinstance(scenarios, list):
        raise DesignError("'scenarios' must be a list")
    ids = set()
    for i, s in enumerate(scenarios):
        if not isinstance(s, dict) or not s.get("type"):
            raise DesignError(f"scenario {i + 1} needs a 'type'")
        s["id"] = str(s.get("id") or f"{s['type']}{i + 1}")
        if s["id"] in ids:
            raise DesignError(f"scenario '{s['id']}': duplicate id")
        ids.add(s["id"])
        if str(s.get("part")) not in seen:
            raise DesignError(f"scenario '{s['id']}' refers to unknown part '{s.get('part')}'")
        s["part"] = str(s["part"])
    d["scenarios"] = scenarios
    diagram = d.get("diagram")
    if diagram is not None:
        if not isinstance(diagram, dict):
            raise DesignError("'diagram' must be an object with nodes and edges")
        nodes = [n for n in diagram.get("nodes") or [] if isinstance(n, dict) and n.get("id")]
        ids_n = {str(n["id"]) for n in nodes}
        edges = [e for e in diagram.get("edges") or [] if isinstance(e, dict)
                 and str(e.get("from")) in ids_n and str(e.get("to")) in ids_n]
        d["diagram"] = {"nodes": nodes, "edges": edges}
    return d


# -- geometry -------------------------------------------------------------------
def part_by_id(design: dict, pid: str) -> dict:
    for p in design["parts"]:
        if p["id"] == pid:
            return p
    raise DesignError(f"unknown part '{pid}'")


def extents(part: dict) -> list:
    """Unrotated bounding-box size [x, y, z] in mm."""
    s = part["shape"]
    if s == "box":
        return list(part["size"])
    if s in ("cylinder", "tube"):
        out = [2 * part["radius"]] * 3
        out[AXES[part["axis"]]] = part["length"]
        return out
    if s == "sphere":
        return [2 * part["radius"]] * 3
    if s == "heatsink":
        w, dpt, t = part["base"]
        return [w, dpt, t + part["fin_height"]]
    if s == "yagi":
        out = [2 * part["boom_radius"]] * 3
        out[AXES[part["axis"]]] = part["boom_length"]
        out[AXES[part["element_axis"]]] = max(part["element_length"], 2 * part["boom_radius"])
        return out
    return list(part["mesh"]["size"])


def volume_mm3(part: dict) -> float:
    s = part["shape"]
    if s == "box":
        x, y, z = part["size"]
        w = part.get("wall")
        return x * y * z - ((x - 2 * w) * (y - 2 * w) * (z - 2 * w) if w else 0.0)
    if s == "cylinder":
        return math.pi * part["radius"] ** 2 * part["length"]
    if s == "tube":
        return math.pi * (part["radius"] ** 2 - part["inner_radius"] ** 2) * part["length"]
    if s == "sphere":
        return 4 / 3 * math.pi * part["radius"] ** 3
    if s == "heatsink":
        w, dpt, t = part["base"]
        return w * dpt * t + part["n_fins"] * part["fin_thickness"] * part["fin_height"] * dpt
    if s == "yagi":
        mean_el = part["element_length"] * (1 + 0.85) / 2
        return (math.pi * part["boom_radius"] ** 2 * part["boom_length"]
                + part["n_elements"] * math.pi * part["element_radius"] ** 2 * mean_el)
    return float(part["mesh"]["volume"])


def rotation_matrix(rot_deg) -> np.ndarray:
    """Intrinsic XYZ Euler angles in degrees (matches three.js default order)."""
    rx, ry, rz = (math.radians(a) for a in rot_deg)
    cx, sx, cy, sy, cz, sz = (math.cos(rx), math.sin(rx), math.cos(ry), math.sin(ry),
                              math.cos(rz), math.sin(rz))
    mx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    my = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    mz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return mx @ my @ mz


def aabb(part: dict):
    """World-axis bounding box (min, max) in mm, rotation included."""
    half = np.array(extents(part)) / 2
    corners = np.array([[sx, sy, sz] for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)])
    world = (corners * half) @ rotation_matrix(part["rotation"]).T + np.array(part["position"])
    return world.min(axis=0), world.max(axis=0)


def mass_volume_mm3(part: dict) -> float:
    """Volume that carries mass (open frames count only their fill)."""
    return volume_mm3(part) * part.get("fill", 1.0)


def is_fastener(part: dict) -> bool:
    text = f"{part['id']} {part['name']} {part.get('process', '')}".lower()
    return any(h in text for h in FASTENER_HINTS)


def summary(design: dict) -> dict:
    """Mass, cost and size, per part and in total."""
    rows, lo, hi = [], np.full(3, np.inf), np.full(3, -np.inf)
    for p in design["parts"]:
        mat = materials.get(p["material"])
        mass = mass_volume_mm3(p) * 1e-9 * mat["density"] * p["quantity"]
        a, b = aabb(p)
        lo, hi = np.minimum(lo, a), np.maximum(hi, b)
        rows.append({"id": p["id"], "name": p["name"], "material": mat["name"],
                     "group": p.get("group", ""), "specs": p.get("specs", {}),
                     "material_id": mat["id"], "process": p["process"],
                     "quantity": p["quantity"], "mass_g": mass * 1e3,
                     "cost": mass * mat["cost_per_kg"], "purpose": p["purpose"],
                     "size_mm": [round(v, 2) for v in extents(p)]})
    return {"parts": rows, "mass_g": sum(r["mass_g"] for r in rows),
            "material_cost": sum(r["cost"] for r in rows),
            "size_mm": [float(v) for v in (hi - lo)],
            "centre_mm": [float(v) for v in (hi + lo) / 2]}


def total_mass_kg(design: dict) -> float:
    return summary(design)["mass_g"] / 1e3


# -- geometric checks -------------------------------------------------------------
def contains(outer: dict, inner: dict) -> bool:
    """Is ``inner`` inside the hollow ``outer`` (bounding boxes)?"""
    (a0, a1), (b0, b1) = aabb(outer), aabb(inner)
    return bool(np.all(b0 >= a0 - 1e-6) and np.all(b1 <= a1 + 1e-6))


def geometry_issues(design: dict) -> list:
    """Things that are wrong with the assembly before any physics: parts that
    touch nothing, parts buried in each other, steps that leave parts out."""
    issues = []
    parts = design["parts"]
    boxes = [aabb(p) for p in parts]
    n = len(parts)
    diag = max(float(np.linalg.norm(b[1] - b[0])) for b in boxes)
    tol = max(1.0, 0.01 * diag)
    touching = [[False] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            (a0, a1), (b0, b1) = boxes[i], boxes[j]
            gap = np.maximum(a0 - b1, b0 - a1).max()
            if gap <= tol:
                touching[i][j] = touching[j][i] = True
            overlap = np.minimum(a1, b1) - np.maximum(a0, b0)
            open_frame = parts[i].get("fill", 1) < 0.2 or parts[j].get("fill", 1) < 0.2
            if np.all(overlap > 0) and not (is_fastener(parts[i]) or is_fastener(parts[j])
                                            or open_frame):
                # a part inside a hollow shell or tube is how things are housed
                hollow = any(q.get("wall") or q["shape"] == "tube" for q in (parts[i], parts[j]))
                small = min(np.prod(a1 - a0), np.prod(b1 - b0))
                share = float(np.prod(overlap) / small) if small > 0 else 0.0
                if share > 0.3 and not hollow:
                    issues.append({
                        "level": "warning", "where": f"{parts[i]['id']} / {parts[j]['id']}",
                        "message": (f"'{parts[i]['name']}' and '{parts[j]['name']}' occupy the "
                                    f"same space ({100 * share:.0f}% of the smaller part)")})
    if n > 1:                                       # is the assembly one connected thing?
        seen, stack = {0}, [0]
        while stack:
            i = stack.pop()
            for j in range(n):
                if touching[i][j] and j not in seen:
                    seen.add(j)
                    stack.append(j)
        for i in range(n):
            if i not in seen:
                issues.append({"level": "warning", "where": parts[i]["id"],
                               "message": f"'{parts[i]['name']}' is not attached to the rest "
                                          "of the assembly (it touches nothing)"})
    for p in parts:
        e = extents(p)
        if min(e) < 0.2:
            issues.append({"level": "warning", "where": p["id"],
                           "message": f"'{p['name']}' has a dimension under 0.2 mm - "
                                      "too thin to make"})
        if max(e) > 20000:
            issues.append({"level": "warning", "where": p["id"],
                           "message": f"'{p['name']}' is over 20 m in one direction - "
                                      "check the units (dimensions are millimetres)"})
    used = {pid for s in design["assembly"] for pid in s["parts"]}
    if design["assembly"]:
        for p in parts:
            if p["id"] not in used:
                issues.append({"level": "warning", "where": p["id"],
                               "message": f"'{p['name']}' is never mentioned in the "
                                          "assembly steps"})
    if not design["scenarios"]:
        issues.append({"level": "warning", "where": "scenarios",
                       "message": "no real-world tests are defined for this design"})
    return issues
