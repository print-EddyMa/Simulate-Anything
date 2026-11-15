# Simulate Anything

Describe a physical problem. Get back a numerical simulation of it, together
with a report of how that simulation checked itself.

```console
$ simulate-anything run heat_sink_fin -p fin_length="40 mm" -p h="40 W/(m^2*K)"

Results
  heat_per_fin                     8.06751 W
  tip_temperature                  69.6967 degC
  fin_efficiency                   0.874148

Self-checks
  [every simulation]
    PASS  dimensional analysis: fin equation: all terms share one dimension: ...
    PASS  mesh convergence: heat_per_fin: n=16=8.06555, n=32=8.06712, n=64=8.06751;
          observed order 2.00, est. discretisation error 0.00161% (limit 2%)
    PASS  known solution: fin heat rate: got 8.06751 W, expected 8.06764 W
    PASS  solver residual: 1.03522e-16 (limit 1e-08)
  [heat & thermodynamics]
    PASS  energy balance (in = out + stored): relative imbalance 4.57888e-14
    PASS  temperature bounds (maximum principle): [342.849, 352.986] K within [298.15, 353.15] K
    PASS  entropy generation >= 0: 0.004214 W/K
    PASS  back-of-envelope R-network bounds: 8.068 W lies between 6.424 W and 9.229 W
  [math sanity]
    PASS  boundary conditions complete
    skip  time-step (CFL) condition: not applicable: steady-state problem
  VERIFIED: 15 passed, 0 warnings, 0 failed, 2 not applicable
```

(Output shortened; every line carries its numbers.)

![The web app: inputs with sliders on the left, results, plots and the check report on the right](docs/screenshot.png)

A wrong input is caught instead of computed:

```console
$ simulate-anything run heat_sink_fin -p h="40 W/(m*K)"
    FAIL  dimensional analysis: parameter 'h': '40 W/(m*K)' has dimension
          [m·kg/(s^3·K)] but [kg/(s^3·K)] (W/(m^2*K)) is required
```

## Install

```bash
pip install -e .
```

Python 3.10+, NumPy and SciPy. For plain-language mode add the agent extra:

```bash
pip install -e ".[agent]"
```

## Three ways to use it

**1. Interactive web app.** Pick a simulation, drag a slider, and the result,
the plots and the whole checklist update.

```bash
simulate-anything serve
```

Then open http://127.0.0.1:8000.

**2. Command line.**

```bash
simulate-anything list
```

```bash
simulate-anything describe pcb_thermal
```

```bash
simulate-anything run pcb_thermal -p h="25 W/(m^2*K)" --html report.html
```

`--json` gives machine-readable output; the exit code is non-zero when a check
fails, so it can gate a script or CI job.

**3. Plain language.** With an `ANTHROPIC_API_KEY` set, describe the problem
and the agent picks the model, fills in the parameters, runs it and reads the
check report. A failed check sends it back to fix the setup.

```bash
simulate-anything ask "A 20 mm steel cantilever, 300 mm long, 5 mm thick, carries 40 N at the tip. How far does it bend and is it safe?"
```

From Python:

```python
import simulate_anything as sa

result = sa.run("beam", {"support": "cantilever", "length": "300 mm", "width": "20 mm",
                         "height": "5 mm", "point_load": "40 N", "load_position": 1.0})
print(result.value("max_deflection"), result.report.verdict)
```

## Where it applies

| Area | Problems | Simulations |
|---|---|---|
| Electronics | chip and PCB heat, heat sinks, battery packs | `pcb_thermal`, `heat_sink_fin`, `battery_pack` |
| Mechanical | stress and deflection in brackets, beams and 3D prints; vibration modes | `beam`, `plate_stress`, `beam_vibration` |
| Fluids | airflow, pipe flow, drag, cooling channels | `channel_flow`, `lid_driven_cavity`, `pipe_flow`, `projectile_drag`, `cooling_channel` |
| Electromagnetics | antennas, coils, capacitors, EMI shielding | `antenna_array`, `magnetostatics`, `capacitor`, `em_shield` |
| Aerospace | CubeSat temperatures, re-entry heating, structural loads | `cubesat_thermal`, `reentry_heating`, `beam` |
| Bio and medicine | drug diffusion in tissue, blood flow, heat from implants | `drug_diffusion`, `pipe_flow`, `implant_heating` |
| Climate and energy | groundwater, building heat loss, solar panels, reactor cooling | `groundwater_well`, `wall_heat_loss`, `solar_panel`, `fuel_pin` |
| Chemistry | reaction-diffusion, mass transfer in reactors | `reaction_diffusion`, `plug_flow_reactor` |
| Product safety | a part on fire, a wire carrying current, a product dropped on the floor | `flame_exposure`, `wire_heating`, `drop_impact` |
| Education | any textbook problem as an interactive simulation | `conduction_2d`, `projectile_drag`, and every template above in the web app |

Not in the catalogue? A new simulation is one class; see
[docs/extending.md](docs/extending.md) and
[examples/custom_simulation.py](examples/custom_simulation.py).

## Create Anything: describe a thing, get a tested design

`create_anything` sits on top of the simulations. Describe something physical
and the design agent writes a design document: parts (shape, dimensions in mm,
material, process), assembly steps, and the real-world tests it has to survive.
Each test runs as a verified simulation on the part's real dimensions and
material, and the agent revises the design until the tests pass.

```bash
create-anything serve
```

Open http://127.0.0.1:8001 and describe what you want to make. The design
appears in 3D as it is tested, coloured by result, and every version the agent
tried can be reopened from the activity log. Ask for changes ("use aluminium
instead", "make it lighter") and the agent edits the current design rather than
starting over. You can also change materials, edit the JSON, run the tests and
optimise by hand. Describing needs `ANTHROPIC_API_KEY` set where the server
runs; the key never reaches the page. The same agent runs from the command line:

```bash
create-anything design "A water-cooled copper cold plate for a 60 W laser diode; the diode base must stay under 45 degC" -o cold_plate.json
```

### Tests: built-in types, or any simulation

The built-in test types cover the common situations:

| Test | Situation | Simulation behind it |
|---|---|---|
| `load` | a steady force on a part held like a beam | `beam` |
| `drop` | the product falls and lands on the part | `drop_impact` |
| `vibration` | a part shaken by a motor or fan | `beam_vibration` |
| `heat` | a heat sink carrying a power, or a bar hot at one end | `heat_sink_fin` |
| `fire` | a flame or radiant heat on a wall | `flame_exposure` |
| `current` | current through a conductor | `wire_heating` |
| `flow` | liquid or gas through a tube | `pipe_flow` |

For anything else, a `simulation` test runs any template in the catalogue. Its
inputs are **bound** to the part, so the result depends on the design and the
optimiser can change it:

```json
{"type": "simulation", "part": "plate", "simulation": "pcb_thermal",
 "params": {"h": "10 W/(m^2*K)", "chips": [{"name": "LED", "x": "30 mm", "y": "20 mm",
            "w": "10 mm", "h": "10 mm", "power": "4 W"}]},
 "bind": {"board_length": "size.x", "board_width": "size.y", "thickness": "size.z",
          "conductivity": "material.conductivity"},
 "require": [{"qoi": "max_temperature", "max": "70 degC"}]}
```

A test with nothing bound is rejected. So are unknown templates, parameters or
results, a requirement in the wrong units, and any quantity given without a
unit. A test counts as passed only when the requirement is met **and** the
simulation passed its own checks. A failed self-check is reported as
*unverified*, never as a pass.

### Shapes

`box` (optionally a hollow shell), `cylinder`, `tube`, `sphere`, `heatsink`,
and `extrusion`: any 2-D outline pushed along an axis, such as an L-angle,
T-beam, channel or gear blank. An extrusion tested as a beam uses its true
second moment of area and outer-fibre distance. Before any physics, the
geometry is checked: parts that touch nothing, parts buried in each other,
parts too thin to make or in the wrong units, and parts left out of the
assembly.

### The optimiser

```console
$ create-anything optimise create_anything/examples/shelf_bracket.json -o fixed.json
Wall shelf bracket: 48.1 g -> 99.6 g
  fix   Arm: thickness 4 -> 8 mm
  fix   Arm: thickness 8 -> 16 mm
  fix   Arm: PLA -> Polycarbonate
  trim  Arm: thickness 16 -> 15.2 mm
  trim  Arm: width 30 -> 27 mm
  PASS       4 kg of books on the end
             40 N on the arm: safety factor 10.30 (needs 2), bends 2.39 mm (limit 2.5 mm)
             simulation beam: PASSED WITH WARNINGS: 12 passed, 1 warnings, 0 failed, 2 not applicable
```

(Output shortened.) The warning is the beam check noting that the thicker arm is
now slightly too stubby for simple beam theory (length/depth 9.9, wants 10 or
more).

It changes the dimensions each failing test depends on (for a `simulation`
test, the bound ones) and tries other materials. It re-runs every test on every
candidate and keeps the change that fixes the most for the least added mass.
Once everything passes, it trims material back off, but never below what the
manufacturing process can make (1.2 mm for a 3D print, for example). Parts
marked `"locked": true`, such as screws and circuit boards, are never changed,
and `material_options` limits which materials a part may switch to. The final
design is re-run with full verification, and any new overlaps or gaps caused by
resizing are reported.

Other commands: `create-anything check FILE` (exits non-zero unless every test
passes), `create-anything materials`, `create-anything tests`. The example
designs are in `create_anything/examples/`.

### Limits

Parts are idealised to fit the simulations: a bracket arm becomes a beam, a
round, hollow or extruded section becomes the rectangle with the same stiffness
and strength, and a wall in a fire becomes a 1-D slab. Each test's notes say
which idealisation was used. Physics that interacts across parts (a whole
assembly in one 3-D solve) is checked one piece at a time. Joints, fatigue,
buckling and stress concentrations are not modelled, and there is no CAD import.

## How it checks itself

Every run produces a report. Each line is `PASS`, `WARN`, `FAIL` or `skip`,
with the numbers behind it. A check that does not apply is listed as skipped
with the reason; it is never left out.

### Every simulation

| Check | What actually happens |
|---|---|
| **Mesh convergence** | The problem is solved on three grids (n/4, n/2, n). Richardson extrapolation gives the observed order of accuracy and an estimate of the error left in the answer. Above 2% is a warning, above 6% a failure. |
| **Dimensional analysis** | Every input is parsed with its unit and converted; a wrong dimension stops the run. Each simulation also lists the terms of its governing equation as dimensioned quantities, and they must all match. Named groups (Reynolds, Biot, Peclet...) must come out as pure numbers. |
| **Known solutions** | The result is compared with the exact answer where one exists (fin equation, beam formulas, Hagen-Poiseuille, Fresnel slab, layered cylinder...). Where the setup has no closed form, the same solver is run on a benchmark that does. |
| **Residuals** | The relative residual of the discrete equations must be below tolerance (1e-8 for linear solves). |

### By physics

| Physics | Checks |
|---|---|
| **Heat and thermodynamics** | Energy in = energy out + stored, summed from the boundary formulas. No temperature below the coldest boundary or above the hottest source (maximum principle), applied only where the signs of the sources make it a theorem. Entropy production is non-negative. The result agrees with, or is bracketed by, a back-of-envelope R-network. |
| **Structures** | Sum of forces and sum of moments are zero. Reactions equal the applied loads. Deflection and stress match the beam-theory formula in the standard cases. |
| **Fluids** | Mass in = mass out. The Reynolds number is compared with the regime the model assumes, and a laminar model at a turbulent Reynolds number is flagged. Pressure drop matches Hagen-Poiseuille (laminar) or the Moody chart via Colebrook (turbulent). |
| **Electromagnetism** | Div B = 0. Charge is conserved. Gauss's law holds over nested closed surfaces. Energy flow adds up: Poynting flux in = out + absorbed. |

### Math sanity

| Check | What actually happens |
|---|---|
| **Well-posed** | Before solving: boundary conditions are complete and consistent. A steady problem with only flux conditions and a net source has no solution and is rejected; a structure that can move as a rigid body is rejected. |
| **CFL** | For time stepping, the step is compared with the stability limit of the scheme. An unstable step fails here, as well as in every check its blow-up breaks. |
| **Symmetry** | If the setup is unchanged by a reflection, the solution must be too. |

The checks are tested the same way as the solvers: the test suite injects
wrong units, unstable time steps, missing supports and wrong physics, and
requires the matching check to fail.

## How the agent uses the checks

`simulate-anything ask` runs a tool-using language model that never does the
arithmetic itself. It can list the simulations, read a parameter schema, and
run a simulation. The full check report comes back as the tool result, so a
failure is something it has to deal with:

- dimensional failure: a unit is wrong, so fix the input;
- well-posedness failure: a boundary condition is missing or contradictory;
- convergence warning: raise the resolution and run again;
- CFL failure: reduce the time step;
- regime warning: the model does not apply as set up, so say so.

It is instructed not to present numbers from a run with failed checks as an
answer, and to say plainly when no template fits the problem.

## Limits

- Geometry is parametric: rectangles, layers, cylinders and spheres on
  structured 1-D and 2-D grids. There is no CAD import and no 3-D solver.
- Flow solvers are laminar. Turbulent pipe flow uses the Colebrook
  correlation, not a turbulence model.
- Templates state their own modelling assumptions (for example, the re-entry
  model has no ablation or re-radiation) and report them in the result.
- A clean report means the equations as posed were solved correctly. It
  cannot tell you that the equations are the right model of your hardware.

## Layout

```
simulate_anything/
  units.py        quantities that carry dimensions
  checks.py       the check vocabulary and report
  core.py         Simulation base class: runs the protocol
  solvers/        transport (finite volume), fem, navier_stokes, fdtd, lumped
  sims/           the simulation templates, by area
  agent.py        plain-language agent
  cli.py, server.py, report.py, web/
create_anything/
  materials.py    material library
  design.py       the design document, mass and cost, geometry checks
  scenarios.py    real-world tests mapped onto verified simulations
  optimize.py     propose, re-test, keep; then trim
  agent.py        plain-language designer
  cli.py, server.py (background jobs), web/ (three.js viewer), examples/
tests/            solver, template, protocol and app tests
docs/extending.md
examples/
```

## Development

```bash
pip install -e ".[dev,agent]"
```

```bash
python -m pytest
```
