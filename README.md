# Create Anything

Describe something, show a photo of it, or import a part. Create Anything
designs it, shows exactly how it is built, puts it through the real world
(wind, sun, fire, current, drops, heat, radio), and improves it until every
test passes on simulations that check themselves.

![Create Anything building the Ghostlight field node](docs/screenshot.png)

## What happens when you ask

1. **Understand.** The request becomes a short engineering brief.
2. **Design.** A system diagram, then the parts: materials, dimensions, where
   each sits, how they go together. The 3D model builds in front of you.
3. **Test.** The design faces the situations it will meet. Each test runs a
   real numerical simulation with its own verification report.
4. **Fix.** A failure that needs a new idea (a missing part, the wrong layout)
   is fixed by the designer, with the number that showed it. A failure that is
   about size or material is fixed by the optimiser, which keeps only changes
   the simulations prove and then trims excess material.
5. **Verify.** The final design is re-run with full verification: grid
   convergence, known solutions, conservation laws. It is only reported as
   passing if every test passes on verified simulations.

## The flagship demo: Ghostlight

Ghostlight is a passive radar: a receive-only sensor that maps drones by
listening for their echoes of a TV broadcast. The demo turns the bench
prototype into a node that can stand alone at a substation.

- The first design fails 4 of 5 tests. Detection reaches only 0.75 km
  against a 1.5 km requirement, and the simulation shows why: TV signal leaking
  into the back of the surveillance antenna raises the noise floor by 44 dB.
  A bigger antenna would not help, because it hears the leak just as loudly.
- The designer adds a wire-mesh screen between the antenna and the tower.
  Detection goes to 2.2 km.
- The optimiser then paints the enclosure white (64 °C inside instead of 71 °C),
  enlarges the battery for three cloudy winter days, and stiffens the mast for
  a 35 m/s gust, then trims material wherever every test still passes.

The demo uses public textbook physics (the bistatic radar equation) and
catalogue-level parts only.

## Run it

```bash
pip install -e ".[agent]"
```

```bash
python -m create_anything serve
```

Open http://127.0.0.1:8000. The examples run without an API key: their design
steps are scripted, and every simulation runs live. To design anything from
your own words or photos, set your key before starting the server:

```bash
export ANTHROPIC_API_KEY="your-key"
```

In the terminal:

```bash
python -m create_anything demo ghostlight
```

```bash
python -m create_anything optimise my_design.json -o improved.json
```

## Real-world tests

| Test | What it answers | Simulation behind it |
|---|---|---|
| `load` | Does it hold a weight, and how far does it bend? | `beam` (finite elements) |
| `drop` | Does it survive a fall or an impact? | `drop_impact` |
| `vibration` | Will it resonate with a motor or fan? | `beam_vibration` |
| `wind` | Does a mast or pole survive a gust, with everything on it? | `beam` |
| `heat` | How hot does a heat sink or a rod get? | `heat_sink_fin` |
| `fire` | How long before a flame ignites or softens it? | `flame_exposure` |
| `enclosure` | How hot is it inside a sealed box in the sun? | `enclosure_thermal` |
| `current` | How hot does a conductor get, and what voltage is lost? | `wire_heating` |
| `power` | Does a solar panel and battery keep it on through bad weather? | `solar_battery` |
| `flow` | What pressure does a pipe lose? | `pipe_flow` |
| `coverage` | How far out can a passive radar see a target? | `passive_radar` |

A part is idealised for each test (a bracket arm becomes a beam, a hollow
section becomes the rectangle of equal stiffness), and every idealisation is
listed in that test's notes.

## How every number is checked

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

## Simulations in the engine

Create Anything runs on Simulate Anything, a library of 32 simulations that
verify themselves. Each one can also be run on its own:

| Area | Simulations |
|---|---|
| Real-world tests | `flame_exposure`, `wire_heating`, `drop_impact`, `passive_radar`, `enclosure_thermal`, `solar_battery` |
| Electronics | `pcb_thermal`, `heat_sink_fin`, `battery_pack` |
| Mechanical | `beam`, `plate_stress`, `beam_vibration` |
| Fluids | `channel_flow`, `lid_driven_cavity`, `pipe_flow`, `projectile_drag`, `cooling_channel` |
| Electromagnetics | `antenna_array`, `magnetostatics`, `capacitor`, `em_shield` |
| Aerospace | `cubesat_thermal`, `reentry_heating` |
| Bio and medicine | `drug_diffusion`, `implant_heating` |
| Climate and energy | `groundwater_well`, `wall_heat_loss`, `solar_panel`, `fuel_pin` |
| Chemistry | `reaction_diffusion`, `plug_flow_reactor` |
| Education | `conduction_2d` |

```bash
python -m simulate_anything run passive_radar -p rcs="0.01 m^2" --html report.html
```

```bash
python -m simulate_anything serve
```

New simulations are one class each; see [docs/extending.md](docs/extending.md).

## Limits

- Geometry is built from primitives (boxes, tubes, rods, fins, antennas) and
  imported STL meshes. Tests idealise parts as beams, walls, rods and lumped
  boxes; they do not run 3-D finite elements on the full shape.
- Fire means heating to ignition or softening; flame spread and burning are
  not modelled. Flow is laminar except turbulent pipe friction.
- The passive radar model is free space over flat ground: no terrain,
  multipath, or clutter other than the direct signal.
- A photo gives proportions, not measurements. Dimensions from a photo are
  estimates unless something of known size is in the picture.
- A clean report means the equations as posed were solved correctly. It does
  not prove the equations are the right model of your hardware.

## Layout

```
create_anything/      designs, real-world tests, optimiser, agent, live engine, server
  web/dist/           the built interface (served by the Python server)
web/                  interface source (React, three.js)
simulate_anything/    the self-checking simulation library
docs/                 protocol between engine and interface, extending guide
tests/
```

## Development

```bash
pip install -e ".[dev,agent]"
```

```bash
python -m pytest
```

```bash
cd web && npm install && npm run build
```
