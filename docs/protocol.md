# Margin: engine ↔ interface protocol

The Python server (`margin serve`, or `python -m create_anything serve`, default port 8000) serves
the built web app from `create_anything/web/dist/` and these endpoints. All
bodies are JSON. Lengths are millimetres, z is up, angles are degrees.

## Endpoints

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/status` | | `{"agent": bool, "version": str, "model": str}`: `agent` is false when no API key is set |
| GET | `/api/examples` | | `[{"id", "title", "tagline", "prompt", "icon"}]` |
| GET | `/api/materials` | | `[{"id", "name", "family", "density", "youngs_modulus", "strength", ...}]` |
| POST | `/api/demo/<id>` | `{}` | **event stream**: a scripted build of an example; every simulation runs live |
| POST | `/api/create` | `{"prompt": str, "image"?: dataURL, "mesh"?: part, "design"?: Design}` (with `design`: edit it) | **event stream**: Claude designs it (needs a key) |
| POST | `/api/evaluate` | `{"design"}` | `{"design", "summary", "issues", "diagram", "evaluation"}` |
| POST | `/api/optimise` | `{"design"}` | **event stream** |
| POST | `/api/import` | `{"name", "data": base64 STL}` | `{"part"}`: a `mesh` part to add to a design |

An **event stream** is `text/event-stream`: one JSON object per `data:` line,
in order, ending with a `done` or `error` event.

## Events

```jsonc
{"type": "status",  "phase": "understand|design|build|test|optimise|verify|done",
                    "label": "Designing the antenna array"}           // current stage
{"type": "thought", "text": "A passive radar needs two receive channels..."}   // narration, short
{"type": "thinking","text": "summary of the model's reasoning"}       // agent only; show collapsed
{"type": "tool",    "name": "run_tests", "label": "Running 5 real-world tests"} // agent tool call
{"type": "diagram", "diagram": Diagram}                                // whole diagram, replaces previous
{"type": "design",  "design": Design, "summary": Summary, "issues": [Issue],
                    "added": ["part ids new in this snapshot"], "changed": ["part ids modified"]}
{"type": "test_started", "id": "wind", "title": "Wind: 40 m/s", "part": "mast", "test_type": "wind"}
{"type": "test_result",  "test": TestResult}
{"type": "change",  "change": "Mast: wall 2 -> 3.2 mm", "reason": "...", "kind": "fix|trim",
                    "mass_g": [before, after], "tests": {"wind": "pass", ...}}
{"type": "done",    "design": Design, "summary": Summary, "evaluation": Evaluation,
                    "answer": "markdown", "verified": bool, "before"?: Evaluation}
{"type": "error",   "message": "..."}
```

## Design

```jsonc
{
  "name": "Passive radar node",
  "description": "...",
  "parts": [Part],
  "assembly": [{"parts": ["mast", "clamp"], "action": "Stand the mast in the base"}],
  "scenarios": [Scenario],          // real-world tests
  "diagram": Diagram                // optional system block diagram
}
```

### Part

Common fields: `id`, `name`, `shape`, `position` [x,y,z] (centre of the
unrotated bounding box), `rotation` [rx,ry,rz] (intrinsic XYZ Euler, degrees),
`material` (id from `/api/materials`), `quantity`, `process`, `purpose`
(one short line), `group` (subsystem name, e.g. "RF front end"; used for
colour-coding and the diagram), optional `look`, optional `color` (CSS hex
override), optional `locked` (optimiser must not change it).

| shape | fields | geometry |
|---|---|---|
| `box` | `size` [x,y,z]; optional `wall` | solid block, or a hollow shell when `wall` is set (render translucent so the inside is visible) |
| `cylinder` | `radius`, `length`, `axis` x/y/z | solid rod along the axis |
| `tube` | `radius`, `inner_radius`, `length`, `axis` | hollow tube |
| `sphere` | `radius` | |
| `heatsink` | `base` [w,d,t], `fin_height`, `fin_thickness`, `n_fins` | base plate centred low; `n_fins` fins spread evenly across x, running the full depth y, standing up +z from the base top. Bounding box z = t + fin_height |
| `yagi` | `boom_length`, `n_elements`, `element_length`, `boom_radius`, `element_radius`, `axis` (boom direction), `element_axis` | a boom along `axis`; `n_elements` thin rods along `element_axis`, evenly spaced along the boom from one end to the other; the first (reflector) is `element_length` long, the rest taper linearly to 0.85 × that at the far end (the end the antenna points to, +axis) |
| `mesh` | `mesh`: `{"vertices": [x,y,z,...], "faces": [i,j,k,...], "size", "volume"}` | imported triangles, already centred on the origin |

Optional `specs`: datasheet values as strings with units, e.g.
`{"gain": "10 dBi", "front_to_back": "15 dB", "beamwidth": "60 deg", "power": "12 W"}`.
Tests read them (an antenna's gain, a board's dissipation); the interface lists
them in the part's details.

`look` is a rendering hint only: `pcb` (green board with a few dark chips and a
connector), `solar` (dark blue cells in a grid, aluminium frame), `battery`
(dark casing, terminals on top), `screen`, `glass`, `rubber`, `cable`,
`bowtie` (UHF TV panel antenna: a column of four X-shaped bowtie elements in
front of a wire-grid reflector filling the box; the elements face +x in the
part's own frame), `wire_mesh` (a thin frame filled with a fine square wire
grid, like hardware cloth), `laptop` (closed or open laptop), `tripod`
(three splayed legs filling the box).
Materials map to finishes: `metal` family → metallic PBR (aluminium light and
slightly rough, steel darker, copper/brass warm), `plastic` → satin, `wood` →
matte warm, `composite` → dark woven. Each material has a `color`.

### Summary

```jsonc
{"parts": [{"id", "name", "material", "material_id", "process", "quantity",
            "mass_g", "cost", "purpose", "size_mm": [x,y,z]}],
 "mass_g": 0, "material_cost": 0, "size_mm": [x,y,z], "centre_mm": [x,y,z]}
```

### Issue

`{"level": "warning", "where": "part id or 'scenarios'", "message": "..."}`
(geometry and assembly problems found before any physics is run).

### Diagram

```jsonc
{"nodes": [{"id", "label", "kind": "component|signal|software|environment|power",
            "group", "part"?: "part id"}],
 "edges": [{"from", "to", "label"?, "kind": "rf|data|power|mechanical|heat"}]}
```

## Scenario (a real-world test, inside the design)

`{"id", "type", "part", "title"?, ...type-specific inputs as strings with units}`.
Types: `load`, `drop`, `vibration`, `heat`, `fire`, `current`, `flow`, `wind`,
`coverage`, `enclosure`, `power`, and `simulation` (any of the 32 catalogue
simulations, bound to the part's dimensions and material). `GET /api/test-types` describes each.

## TestResult

```jsonc
{
  "id": "wind", "type": "wind", "part": "mast", "title": "Wind: 40 m/s",
  "status": "pass|fail|unverified|error",
  "headline": "one sentence with the key number",
  "metrics": [{"label": "safety factor", "value": 3.1, "unit": "", "limit": 2,
               "kind": "min|max", "ok": true, "base": 0,
               "load": 0.65,             // share of the limit used: 1 = at the limit
               "margin": 0.54}],         // margin of safety = 1/load - 1: 0.54 = 54% to spare
  "load": 0.65,                         // the test's most loaded metric; null if untrusted
  "margin": 0.54,                       // its margin of safety
  "notes": ["modelling assumptions, shown collapsed"],
  "simulation": "beam",                 // Simulate Anything template used
  "reproduce": "simulate-anything run beam -p 'length=0.3 m' ...",  // re-run it independently
  "checks": {"verdict": "VERIFIED", "summary": "...", "verified": true},
  "sim": SimResult,                     // full simulation output (see below)
  "overlay"?: {"kind": "ground", "field": 0, "threshold": 13, "unit": "dB",
               "center_mm": [x, y],    // coverage maps: drape field 0 on the ground
               "source_bearing_deg": 180,  // direction of the TV transmitter (0 = +x, CCW)
               "range_m": 1000}        // the required detection range, draw as a ring
}
```

`SimResult` is Simulate Anything's `Result.to_dict()`: `results` (name →
`{value, unit}`), `fields` (line: `{kind:"line", name, x, series:{label:[...]},
xlabel, ylabel, unit}`; image: `{kind:"image", name, x, y, values[i][j],
xlabel, ylabel, unit}`), `verification` (`{verdict, ok, summary, checks:[{name,
category, status: pass|warn|fail|skip, detail}]}`), `notes`, `params`.

## Evaluation

`{"tests": [TestResult], "counts": {"pass", "fail", "unverified", "error"},
"all_pass": bool, "violation": number, "load": number|null, "margin": number|null,
"limiting": "test id"|null}`

`load` is how much of its allowance a result uses: 0.5 is half, 1 is exactly at
the limit, 2 is twice over. A maximum is measured from `base` (a temperature
from the ambient it rises from); a minimum such as a safety factor is
required / actual. `margin` is the margin of safety, allowable / actual - 1:
0.25 means 25% to spare, 0 is exactly at the limit, negative fails. The
evaluation's `load` and `margin` come from its most loaded test.
