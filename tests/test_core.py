"""Units, the check vocabulary, the transport solver and the protocol itself."""
import math

import numpy as np
import pytest

from simulate_anything import checks as ck
from simulate_anything import core
from simulate_anything.core import Param, Simulation, Solution
from simulate_anything.sims import electronics  # noqa: F401  (registers templates)
from simulate_anything.solvers.transport import (Grid, TransportProblem, dirichlet,
                                                 manufactured_error, neumann, robin)
from simulate_anything.units import DimensionError, Q, convert


# -- units ---------------------------------------------------------------------
def test_unit_conversion_and_arithmetic():
    flux = Q("205 W/(m*K)") * Q("10 K") / Q("2 mm")
    assert flux.to("W/m^2") == pytest.approx(1.025e6)
    assert Q("1 kPa").to("Pa") == pytest.approx(1000)
    assert Q("25 degC").to("K") == pytest.approx(298.15)
    assert Q("3 cP").to("Pa*s") == pytest.approx(3e-3)
    assert convert("12 in", "m") == pytest.approx(0.3048)
    assert convert("10 degC", "K", delta=True) == pytest.approx(10)
    assert (Q("9 m^2").sqrt()).to("m") == pytest.approx(3)


def test_units_must_work_out():
    with pytest.raises(DimensionError):
        Q("1 m") + Q("1 s")
    with pytest.raises(DimensionError):
        convert("5 W/(m*K)", "W/(m^2*K)")
    with pytest.raises(DimensionError):
        convert("25 C", "K")          # coulombs are not a temperature
    with pytest.raises(DimensionError):
        Q("3 furlongs")
    reynolds = Q("1000 kg/m^3") * Q("2 m/s") * Q("50 mm") / Q("1 mPa*s")
    assert reynolds.dimensionless and float(reynolds) == pytest.approx(1e5)


# -- checks --------------------------------------------------------------------
def test_convergence_check_recovers_order_and_flags_coarse_grids():
    exact = 3.0
    second = [exact + 0.4 / 4 ** k for k in range(3)]          # error ~ h^2
    c = ck.convergence("q", second, tol=0.02)
    assert c.status == ck.PASS and "order 2.00" in c.detail
    assert ck.convergence("q", [1.0, 1.6, 2.1]).status == ck.FAIL   # still moving
    assert ck.convergence("q", [2.0, 2.0, 2.0]).status == ck.PASS


def test_bounds_symmetry_and_report_verdict():
    assert ck.bounds("b", ck.HEAT, 0, 1, 0.1, 0.9).status == ck.PASS
    assert ck.bounds("b", ck.HEAT, 0, 1, -0.1, 0.9).status == ck.FAIL
    assert ck.bounds("b", ck.HEAT, 300, math.inf, -4e28, 4e28).status == ck.FAIL   # one-sided
    assert ck.bounds("b", ck.HEAT, 300, math.inf, 300, 4e28).status == ck.PASS
    f = np.array([1.0, 2.0, 2.0, 1.0])
    assert ck.symmetry("s", f, f[::-1]).status == ck.PASS
    assert ck.symmetry("s", f + [0, 0, 0, 0.5], (f + [0, 0, 0, 0.5])[::-1]).status == ck.FAIL
    rep = ck.Report([ck.truth("a", ck.MATH, True), ck.skip("b", ck.MATH, "n/a")])
    assert rep.verdict == "VERIFIED"
    rep.add(ck.truth("c", ck.MATH, False, warn=True))
    assert rep.ok and rep.verdict == "PASSED WITH WARNINGS"
    rep.add(ck.truth("d", ck.MATH, False))
    assert not rep.ok and rep.verdict == "FAILED"


# -- transport solver ----------------------------------------------------------
def test_operator_is_second_order():
    e1, e2 = manufactured_error(16), manufactured_error(32)
    assert math.log2(e1 / e2) == pytest.approx(2, abs=0.1)


def test_layered_cylinder_matches_exact_solution():
    g, layer = Grid.layered([4e-3, 1e-3], 80, coord="cylindrical")
    k = np.array([3.0, 15.0])[layer][:, None]
    q = np.array([3e8, 0.0])[layer][:, None]
    ts = TransportProblem(g, k, source=q, bcs={"east": robin(3e4, 560.0)}).solve_steady()
    lin = 3e8 * math.pi * 16e-6
    exact_surface = 560 + lin / (2 * math.pi * 5e-3 * 3e4)
    exact_centre = exact_surface + lin * math.log(5 / 4) / (2 * math.pi * 15) + 3e8 * 16e-6 / 12
    assert ts.phi[0, 0] == pytest.approx(exact_centre, rel=1e-3)
    net, gross = ts.balance()
    assert abs(net) / gross < 1e-10


def test_transient_conserves_energy():
    prob = TransportProblem(Grid(40, 0.01), 1.0, C=2e6,
                            bcs={"west": neumann(1e4), "east": robin(50.0, 300.0)})
    ts = prob.solve_transient(300.0, 100.0, 1.0)
    assert ts.stored_change == pytest.approx(ts.net_input, rel=1e-10)
    assert ts.phi.min() >= 300.0 - 1e-9


def test_fixed_cells_obey_gauss_law():
    g = Grid(40, 1.0, 40, 1.0)
    top, bottom = g.box(0, 1, 0.75, 1.0), g.box(0, 1, 0, 0.25)
    ts = TransportProblem(g, 2.0, fixed_mask=top | bottom,
                          fixed_value=np.where(top, 1.0, 0.0)).solve_steady()
    assert ts.fixed_outflow(top) == pytest.approx(2.0 * 1.0 / 0.5)        # eps w / d
    assert ts.fixed_outflow(top) == pytest.approx(-ts.fixed_outflow(bottom))
    assert ts.dissipation() == pytest.approx(4.0)                         # C V^2


def test_pure_neumann_problem_is_reported_ill_posed():
    prob = TransportProblem(Grid(8, 1.0), 1.0, source=1.0)
    bad = [c for c in prob.well_posed() if c.status == ck.FAIL]
    assert bad and "ill-posed" in bad[0].detail
    assert not [c for c in TransportProblem(
        Grid(8, 1.0), 1.0, source=1.0, bcs={"west": dirichlet(0.0)}).well_posed()
        if c.status == ck.FAIL]


# -- the protocol ----------------------------------------------------------------
def test_default_run_is_verified_and_matches_theory():
    res = core.run("heat_sink_fin")
    assert res.report.verdict == "VERIFIED", res.to_text()
    m = math.sqrt(25 * 2 * (0.0015 + 0.05) / (205 * 0.0015 * 0.05))
    assert 0 < res.value("fin_efficiency") < 1
    assert res.value("fin_efficiency") == pytest.approx(
        math.tanh(m * 0.03) / (m * 0.03), rel=0.02)
    names = " | ".join(c.name for c in res.report.checks)
    for item in ("dimensional analysis", "mesh convergence", "known solution",
                 "solver residual", "energy balance", "temperature bounds", "entropy",
                 "R-network", "boundary conditions complete", "CFL", "symmetry"):
        assert item in names, item


def test_wrong_units_fail_the_dimensional_check_without_solving():
    res = core.run("heat_sink_fin", {"h": "25 W/(m*K)"})
    assert not res.ok and not res.qoi
    assert res.report.failed()[0].name == "dimensional analysis"
    res = core.run("heat_sink_fin", {"lenght": "30 mm"})
    assert not res.ok and "unknown parameter" in res.report.failed()[0].detail


class _Toy(Simulation):
    """u'' = -2 on (0, 1), u(0) = u(1) = 0: exact midpoint value 0.25."""
    name, title, domain = "_toy", "toy", "test"
    params = [Param("bug", "none", None, "which defect to inject",
                    choices=["none", "wrong_physics", "no_convergence", "crash"])]
    primary = ("mid",)
    default_resolution = 32

    def solve(self, n):
        if self.p.bug == "crash":
            raise ZeroDivisionError("boom")
        src = 2.0 if self.p.bug != "wrong_physics" else 2.6
        ts = TransportProblem(Grid(n, 1.0), 1.0, source=src,
                              bcs={"west": dirichlet(0.0), "east": dirichlet(0.0)}).solve_steady()
        mid = ts.sample(0.5) + (1.0 / n if self.p.bug == "no_convergence" and n == 32 else 0)
        return Solution({"mid": (mid, "")}, residual=ts.residual)

    def known_solution(self, sol):
        return [ck.close("known solution: midpoint", ck.UNIVERSAL, sol.qoi["mid"][0], 0.25, 1e-2)]


def test_protocol_catches_injected_defects():
    assert _Toy().run().report.verdict == "VERIFIED"
    wrong = _Toy(bug="wrong_physics").run()
    assert [c.name for c in wrong.report.failed()] == ["known solution: midpoint"]
    rough = _Toy(bug="no_convergence").run()
    assert any("mesh convergence" in c.name for c in rough.report.failed())
    crash = _Toy(bug="crash").run()          # a crash is a failed check, not a traceback
    assert not crash.ok and "ZeroDivisionError" in crash.report.failed()[0].detail


def test_result_serialises():
    import json
    res = core.run("pcb_thermal", resolution=32)
    blob = json.loads(json.dumps(res.to_dict()))
    assert blob["verification"]["verdict"] == "VERIFIED"
    assert blob["fields"][0]["kind"] == "image"
    assert blob["results"]["total_power"]["value"] == pytest.approx(4.0)
