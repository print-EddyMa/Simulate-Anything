"""Lumped thermal networks: solver, CubeSat orbit and solar panel."""
import math

import numpy as np
import pytest

from simulate_anything import checks as ck
from simulate_anything import core
from simulate_anything.sims import aerospace, energy  # noqa: F401  (registers templates)
from simulate_anything.sims._heat import SIGMA
from simulate_anything.solvers.lumped import ThermalNetwork


def status(result, fragment):
    found = [c for c in result.report.checks if fragment in c.name]
    assert found, f"no check named like '{fragment}'"
    return [c.status for c in found]


# -- network solver -------------------------------------------------------------
def test_steady_radiator_matches_closed_form():
    net = ThermalNetwork()
    net.node("plate", 100.0)
    net.node("space", fixed=3.0)
    net.radiation(0, 1, 0.5)
    st = net.steady(np.array([200.0, 0.0]), guess=300.0)
    assert st.converged
    assert st.T[0] == pytest.approx((200.0 / (SIGMA * 0.5) + 3.0 ** 4) ** 0.25, rel=1e-12)
    assert st.residual < 1e-12


def test_steady_resistor_chain():
    net = ThermalNetwork()
    a, b = net.node("a", 1.0), net.node("b", 1.0)
    sink = net.node("sink", fixed=300.0)
    net.conductance(a, b, 2.0)
    net.conductance(b, sink, 0.5)
    st = net.steady(np.array([10.0, 0.0, 0.0]))
    assert st.T[b] == pytest.approx(300.0 + 10.0 / 0.5)
    assert st.T[a] == pytest.approx(300.0 + 10.0 / 0.5 + 10.0 / 2.0)


def test_transient_cooling_is_second_order_and_conserves_energy():
    """Linear cooling T = T_inf + dT exp(-t/tau) and an exact energy tally."""
    def run(n):
        net = ThermalNetwork()
        net.node("mass", 50.0)
        net.node("air", fixed=300.0)
        net.conductance(0, 1, 2.0)
        m = net.march([400.0], np.linspace(0.0, 40.0, n + 1),
                      lambda t0, t1: np.zeros(2))
        return m, abs(m.T[-1, 0] - (300.0 + 100.0 * math.exp(-40.0 * 2.0 / 50.0)))

    (m1, e1), (m2, e2) = run(20), run(40)
    assert math.log(e1 / e2, 2) == pytest.approx(2.0, abs=0.1)
    assert m2.absorbed - m2.emitted == pytest.approx(m2.stored, rel=1e-12)
    assert m2.stored < 0


# -- CubeSat ----------------------------------------------------------------------
def test_cubesat_default_is_verified():
    r = core.run("cubesat_thermal")
    assert r.report.verdict == "VERIFIED", r.to_text()
    assert r.value("structure_min") < r.value("structure_max")
    assert r.value("internal_min") < r.value("internal_max")
    assert r.value("orbit_period") == pytest.approx(94.47, abs=0.05)   # 500 km LEO
    assert 0.3 < r.value("eclipse_fraction") < 0.4
    for name in ("energy balance", "temperature bounds", "entropy generation",
                 "R-network", "mesh convergence", "known solution", "dimensional analysis"):
        assert set(status(r, name)) == {ck.PASS}, name


def test_cubesat_fourth_power_mean_equilibrium():
    sim = aerospace.CubeSatThermal()
    sol = sim.solve(128)
    m = sol.data["march"]
    mean4 = (np.sum(np.diff(m.t) * 0.5 * (m.T[1:, 0] ** 4 + m.T[:-1, 0] ** 4))
             / (m.t[-1] - m.t[0])) ** 0.25
    s = sim.s
    q = (s.q_solar - s.harvest) * (1 - s.eclipse_fraction) + s.q_albedo / math.pi + s.q_ir
    exact = ((q + sim.p.internal_power) / (SIGMA * s.eps_area) + 2.7 ** 4) ** 0.25
    assert mean4 == pytest.approx(exact, rel=1e-9)


def test_cubesat_no_eclipse_at_high_beta():
    r = core.run("cubesat_thermal", {"beta_angle": "80 deg"})
    assert r.ok, r.to_text()
    assert r.value("eclipse_fraction") == 0.0
    swing = r.value("structure_max") - r.value("structure_min")
    assert swing < core.run("cubesat_thermal").value("structure_max") \
        - core.run("cubesat_thermal").value("structure_min")


def test_cubesat_zero_emissivity_is_ill_posed():
    r = core.run("cubesat_thermal", {"emissivity": 0.0})
    assert not r.ok
    assert status(r, "heat can leave") == [ck.FAIL]
    assert r.qoi == {}                       # never solved


def test_cubesat_wrong_units_fail_dimensional_analysis():
    r = core.run("cubesat_thermal", {"altitude": "500 s"})
    assert not r.ok
    assert status(r, "dimensional analysis") == [ck.FAIL]


def test_cubesat_power_beyond_harvest_violates_first_law():
    r = core.run("cubesat_thermal", {"internal_power": "30 W"})
    assert status(r, "first law") == [ck.FAIL]


# -- solar panel ---------------------------------------------------------------------
def test_solar_panel_default_is_verified():
    r = core.run("solar_panel")
    assert r.report.verdict == "VERIFIED", r.to_text()
    assert 25 < r.value("cell_temperature") < 60
    balance = r.value("heat_absorbed") - r.value("electrical_power") \
        - r.value("convective_loss") - r.value("radiative_loss")
    assert balance == pytest.approx(0.0, abs=1e-8)
    assert status(r, "mesh convergence") == [ck.SKIP]      # zero-dimensional
    for name in ("energy balance", "temperature bounds", "entropy generation", "NOCT"):
        assert set(status(r, name)) == {ck.PASS}, name


def test_solar_panel_linear_closed_form():
    kw = {"radiation": False, "irradiance": "1000 W/m^2", "wind_speed": "2 m/s"}
    r = core.run("solar_panel", kw)
    h_total = 2 * (5.7 + 3.8 * 2.0)
    eta_amb = 0.20 * (1 - 0.0035 * (20.0 - 25.0))
    rise = 1000.0 * (0.9 - eta_amb) / (h_total + 0.20 * -0.0035 * 1000.0)
    assert r.value("cell_temperature") == pytest.approx(20.0 + rise, abs=1e-8)
    assert r.ok


def test_solar_panel_night_sky_cools_below_ambient():
    r = core.run("solar_panel", {"irradiance": "0 W/m^2"})
    assert r.ok
    assert 5.0 < r.value("cell_temperature") < 20.0
    assert r.value("electrical_power") == 0.0


def test_solar_panel_first_law_violation_fails():
    r = core.run("solar_panel", {"efficiency_stc": 0.95})
    assert status(r, "first law") == [ck.FAIL]
    assert r.qoi == {}


def test_solar_panel_wrong_units_fail_dimensional_analysis():
    r = core.run("solar_panel", {"irradiance": "800 W/m"})
    assert status(r, "dimensional analysis") == [ck.FAIL]


def test_solar_panel_noct_rule_warns_when_radiation_is_ignored():
    r = core.run("solar_panel", {"radiation": False})
    assert r.ok
    assert status(r, "NOCT") == [ck.WARN]
