# Adding a simulation

A simulation is one class. You describe how to solve the problem at a given
resolution and which laws the answer must obey; `Simulation.run()` does the
rest. A complete worked example is in
[`examples/custom_simulation.py`](../examples/custom_simulation.py).

## The contract

```python
from simulate_anything import Param, Simulation, Solution, Field, register
from simulate_anything import checks as ck

@register
class MySim(Simulation):
    name = "my_sim"                 # id used by the CLI, the web app and the agent
    title = "Readable title"
    domain = "electronics"          # groups it in the catalogue
    description = "What is solved, and the governing equation."
    params = [
        Param("length", "100 mm", "m", "what it is", lo=0.01, hi=1.0),
        Param("mode", "a", None, "an option", choices=["a", "b"]),
    ]
    default_resolution = 64
    primary = ("some_result",)      # results used in the grid-convergence study

    def solve(self, n) -> Solution: ...
```

`Param(name, default, unit, doc)`: `unit` is what your solver works in. Users
can pass any compatible unit (`"4 in"`, `"85 degC"`); inside the class
`self.p.length` is a plain float already converted to `unit`, and
`self.q("length")` is the same value as a dimension-carrying `Quantity`.
`lo`/`hi` (in `unit`) give the web app a slider.

`solve(n)` returns a `Solution`:

| field | meaning |
|---|---|
| `qoi` | `{name: (value, unit)}`: the numbers people asked for |
| `fields` | `Field` objects to plot (a line, or a 2-D map) |
| `residual` | relative residual of the discrete equations (`None` for closed forms) |
| `cfl` | `(number, limit, description)` for time-stepping schemes |
| `data` | anything your own checks need later |

## The hooks behind each check

| Check from the README | What you implement | Helpers |
|---|---|---|
| Dimensional analysis | `equations()` returns `{equation: {term: Quantity}}`; every term must have the same dimension. `dimensionless()` returns groups that must be pure numbers. Inputs are checked automatically. | `self.q(name)`, `Q("1 K")` |
| Well-posed problem | `well_posed()` returns checks that run **before** solving. A failure stops the run. | `TransportProblem.well_posed()` |
| Mesh convergence | Nothing: `run()` calls `solve` at `n/4`, `n/2`, `n` and applies Richardson extrapolation to each `primary` result. | `ck.convergence` |
| Known solutions | `known_solution(sol)` compares with an exact answer, or runs the same solver on a benchmark that has one. | `ck.close`, `operator_benchmark()`, `transient_benchmark()` |
| Residuals | Set `Solution.residual`. | |
| Conservation, bounds, entropy | `domain_checks(sol)` | `ts.conservation_check`, `heat_checks`, `ck.bounds`, `ck.small` |
| CFL | Set `Solution.cfl`. | |
| Symmetry | `symmetry_checks(sol)` | `ts.symmetry_checks()`, `ck.symmetry` |

Rules that keep the checks honest:

- A check that does not apply is returned as `ck.skip(name, category, reason)`,
  never dropped, so the report always shows what was not verified.
- Compute the checked quantity by a different route from the one that produced
  the answer (boundary formulas against the matrix balance, a Gauss-law surface
  integral against the stored energy, an analytic formula against the solve).
- If a check fails on a correct setup, the code is wrong. Fix the code rather
  than widening the tolerance.
- Pick `primary` results that vary smoothly with the grid (a value at a point,
  an integral), not the maximum over cells.

## Building blocks

- `solvers/transport.py`: conservative finite-volume solver for
  `C dphi/dt + div(v phi) = div(K grad phi) + S - P phi` in 1-D (cartesian,
  cylindrical, spherical) and 2-D. Covers conduction, diffusion, groundwater,
  electrostatics, magnetostatics and developed duct flow.
- `solvers/fem.py`: beam and plane-stress finite elements.
- `solvers/navier_stokes.py`: 2-D incompressible flow (projection method).
- `solvers/fdtd.py`: 1-D and 2-D electromagnetic waves.
- `solvers/lumped.py`: thermal networks with conduction and radiation.

## Tests

Add a test that the default setup is verified, that a key result matches its
analytic value, and that a deliberately broken setup makes the right check fail:

```python
def test_my_sim():
    res = sa.run("my_sim")
    assert res.report.verdict == "VERIFIED", res.to_text()
    bad = sa.run("my_sim", {"length": "100 s"})        # wrong units
    assert not bad.ok
```

`tests/test_app.py` automatically runs every registered template with its
defaults and requires a clean report.
