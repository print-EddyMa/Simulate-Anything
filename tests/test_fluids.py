"""Fluid-mechanics templates: defaults verify, key numbers match theory, and
deliberately broken setups trip the right check."""
import math

import pytest

from simulate_anything import core
from simulate_anything.sims import fluids  # noqa: F401  (registers the templates)
from simulate_anything.solvers.navier_stokes import IncompressibleFlow2D, Poisson2D

import numpy as np


def status(result, fragment):
    """Statuses of all checks whose name contains ``fragment``."""
    found = [c.status for c in result.report.checks if fragment in c.name]
    assert found, f"no check named like '{fragment}'"
    return found


def assert_verified(result):
    assert result.report.verdict == "VERIFIED", result.to_text()


# -- pipe flow ----------------------------------------------------------------
def test_pipe_laminar_matches_hagen_poiseuille():
    r = core.run("pipe_flow")
    assert_verified(r)
    d, length, q, mu = 0.025, 10.0, 0.03e-3, 1.0e-3
    exact = 128 * mu * length * q / (math.pi * d ** 4)
    assert r.value("pressure_drop") == pytest.approx(exact, rel=2e-3)
    assert r.value("friction_factor") == pytest.approx(64 / r.value("reynolds_number"),
                                                       rel=2e-3)
    assert r.value("max_velocity") == pytest.approx(2 * r.value("mean_velocity"), rel=2e-3)
    for name in ("mass conservation", "Hagen-Poiseuille", "wall force balance",
                 "Reynolds regime", "mesh convergence"):
        assert set(status(r, name)) == {"pass"}


def test_pipe_turbulent_sits_on_the_moody_chart():
    r = core.run("pipe_flow", {"flow_rate": "0.5 L/s", "roughness": "0.045 mm"})
    assert r.ok, r.to_text()
    re, rel = r.value("reynolds_number"), 0.045e-3 / 0.025
    assert re > 4000
    f = r.value("friction_factor")
    # Colebrook must be satisfied by the reported friction factor
    assert 1 / math.sqrt(f) == pytest.approx(
        -2 * math.log10(rel / 3.7 + 2.51 / (re * math.sqrt(f))), rel=1e-9)
    assert status(r, "Moody chart (Haaland") == ["pass"]
    assert status(r, "pressure drop vs Moody chart") == ["pass"]
    assert status(r, "mesh convergence") == ["skip"]
    # smooth-pipe sanity: Blasius within 5% at this Reynolds number
    smooth = core.run("pipe_flow", {"flow_rate": "0.2 L/s", "roughness": "0 mm"})
    blasius = 0.316 / smooth.value("reynolds_number") ** 0.25
    assert smooth.value("friction_factor") == pytest.approx(blasius, rel=0.05)


def test_pipe_transitional_is_flagged_uncertain():
    r = core.run("pipe_flow", {"flow_rate": "0.06 L/s"})
    assert 2300 < r.value("reynolds_number") < 4000
    assert status(r, "Reynolds regime") == ["warn"]
    assert r.report.verdict == "PASSED WITH WARNINGS"


def test_pipe_short_pipe_warns_about_entrance_length():
    r = core.run("pipe_flow", {"length": "0.5 m"})
    assert status(r, "fully developed") == ["warn"]


def test_pipe_wrong_units_fail_dimensional_analysis():
    r = core.run("pipe_flow", {"viscosity": "1e-3 Pa"})
    assert not r.ok
    assert status(r, "dimensional analysis") == ["fail"]


# -- Navier-Stokes solver -------------------------------------------------------
def test_poisson_solver_is_exact_for_both_boundary_types():
    rng = np.random.default_rng(0)
    for dirichlet in (False, True):
        ps = Poisson2D(12, 9, 0.1, 0.2, east_dirichlet=dirichlet)
        rhs = rng.standard_normal((12, 9))
        if not dirichlet:
            rhs -= rhs.mean()           # compatibility for the pure-Neumann problem
        assert np.allclose(ps.apply(ps.solve(rhs)), rhs, atol=1e-9)


def test_projection_keeps_velocity_divergence_free():
    flow = IncompressibleFlow2D(16, 16, 1.0, 1.0, 0.01, lid=1.0)
    dt = 0.5 * flow.dt_limit(1.0)
    for _ in range(50):
        flow.step(dt)
    assert np.max(np.abs(flow.divergence())) < 1e-10


def test_cavity_matches_ghia_benchmark():
    r = core.run("lid_driven_cavity")
    assert_verified(r)
    assert r.value("reynolds_number") == pytest.approx(100)
    # Ghia et al. (1982): u_min = -0.2109, psi_min = -0.1034, vortex at (0.617, 0.734)
    assert r.value("u_min_centerline") == pytest.approx(-0.2109, abs=0.004)
    assert r.value("stream_function_min") == pytest.approx(-0.1034, rel=0.01)
    assert r.value("vortex_center_x") == pytest.approx(0.6172, abs=0.01)
    assert r.value("vortex_center_y") == pytest.approx(0.7344, abs=0.01)
    assert len(status(r, "Ghia")) == 2 and set(status(r, "Ghia")) == {"pass"}
    assert set(status(r, "mass conservation")) == {"pass"}
    assert status(r, "time-step (CFL) condition") == ["pass", "pass"]
    assert status(r, "solver residual") == ["pass"]


def test_cavity_cfl_violation_fails_and_blocks_the_solve():
    r = core.run("lid_driven_cavity", {"cfl_number": 1.5})
    assert not r.ok and not r.qoi
    assert "fail" in status(r, "CFL")


def test_cavity_cfl_violation_really_is_unstable():
    flow = IncompressibleFlow2D(32, 32, 1.0, 1.0, 0.01, lid=1.0)
    flow.run(1.6 * flow.dt_limit(1.0), 20.0, 1e-6, 1.0)
    assert flow.blown_up


def test_cavity_unsteady_reynolds_number_is_rejected():
    r = core.run("lid_driven_cavity", {"viscosity": "0.0005 Pa*s"})
    assert not r.ok
    assert status(r, "Reynolds regime") == ["fail"]


def test_cavity_refuses_runs_that_would_take_forever():
    r = core.run("lid_driven_cavity", {"viscosity": "0.002 Pa*s"}, resolution=64)
    assert not r.ok
    assert status(r, "solver ran") == ["fail"]


# -- channel ----------------------------------------------------------------------
def test_channel_develops_into_plane_poiseuille():
    r = core.run("channel_flow")
    assert_verified(r)
    u, h, mu = 0.002, 0.01, 1.0e-3
    assert r.value("max_velocity") == pytest.approx(1.5 * u, rel=5e-3)
    assert r.value("pressure_gradient") == pytest.approx(12 * mu * u / h ** 2, rel=0.01)
    assert r.value("wall_shear_stress") == pytest.approx(6 * mu * u / h, rel=0.01)
    assert 0 < r.value("entrance_length") < 0.6 * 0.06
    for name in ("mass in = mass out", "u_max = 1.5 U", "plane Poiseuille",
                 "wall force balance", "Reynolds regime", "symmetry", "entrance length"):
        assert set(status(r, name)) == {"pass"}, name


def test_channel_turbulent_reynolds_number_fails_before_solving():
    r = core.run("channel_flow", {"mean_velocity": "2 m/s"})
    assert not r.ok and not r.qoi
    assert status(r, "Reynolds regime") == ["fail"]


def test_channel_too_short_skips_developed_flow_checks():
    r = core.run("channel_flow", {"length": "20 mm", "mean_velocity": "20 mm/s"})
    assert r.ok, r.to_text()
    assert status(r, "entrance length") == ["warn"]
    assert status(r, "u_max = 1.5 U") == ["skip"]
    assert status(r, "pressure drop vs plane Poiseuille") == ["skip"]
    assert status(r, "mass in = mass out") == ["pass"]


def test_channel_cfl_violation_fails():
    r = core.run("channel_flow", {"cfl_number": 1.2})
    assert "fail" in status(r, "CFL")


# -- cooling channel ------------------------------------------------------------------
def test_cooling_channel_isothermal_nusselt():
    r = core.run("cooling_channel")
    assert_verified(r)
    assert r.value("nusselt_developed") == pytest.approx(7.541, rel=0.01)
    assert 20 < r.value("outlet_bulk_temperature") < 60
    for name in ("energy balance", "entropy", "temperature bounds", "Nusselt",
                 "cell Peclet", "symmetry"):
        assert set(status(r, name)) == {"pass"}, name


def test_cooling_channel_constant_flux():
    kw = {"wall_condition": "flux", "mean_velocity": "0.01 m/s"}
    r = core.run("cooling_channel", kw)
    assert_verified(r)
    assert r.value("nusselt_developed") == pytest.approx(8.235, rel=0.01)
    # exact energy balance: T_out = T_in + 2 q L / (rho cp U H)
    t_out = 20 + 2 * 5000 * 0.03 / (998 * 4182 * 0.01 * 1e-3)
    assert r.value("outlet_bulk_temperature") == pytest.approx(t_out, abs=0.05)
    assert r.value("heat_removed") == pytest.approx(2 * 5000 * 0.03 * 0.01, rel=1e-9)


def test_cooling_channel_wall_colder_than_coolant_still_obeys_second_law():
    r = core.run("cooling_channel", {"wall_temperature": "5 degC"})
    assert_verified(r)
    assert r.value("heat_removed") < 0
    assert status(r, "entropy") == ["pass"]


def test_cooling_channel_central_scheme_is_caught_wiggling():
    r = core.run("cooling_channel", {"scheme": "central"})
    assert not r.ok
    assert status(r, "cell Peclet") == ["fail"]
    assert status(r, "temperature bounds") == ["fail"]     # undershoot below the inlet


def test_cooling_channel_turbulent_is_rejected():
    r = core.run("cooling_channel", {"mean_velocity": "3 m/s"})
    assert not r.ok
    assert status(r, "laminar developed flow") == ["fail"]


def test_cooling_channel_wrong_units():
    r = core.run("cooling_channel", {"wall_heat_flux": "5000 W/m"})
    assert status(r, "dimensional analysis") == ["fail"]


# -- projectile -------------------------------------------------------------------------
def test_projectile_default_verified_and_slower_than_vacuum():
    r = core.run("projectile_drag")
    assert_verified(r)
    assert r.value("range") < r.value("vacuum_range")
    assert r.value("terminal_velocity") == pytest.approx(
        math.sqrt(2 * 0.145 * 9.81 / (1.225 * 0.47 * math.pi * 0.074 ** 2 / 4)), rel=1e-9)
    assert status(r, "energy bookkeeping") == ["pass"]
    assert status(r, "time-step (CFL) condition") == ["pass"]


def test_projectile_drop_matches_tanh_solution():
    r = core.run("projectile_drag", {"initial_speed": "0 m/s", "initial_height": "50 m"})
    assert_verified(r)
    v_t = r.value("terminal_velocity")
    t_land = v_t / 9.81 * math.acosh(math.exp(9.81 * 50 / v_t ** 2))
    assert r.value("flight_time") == pytest.approx(t_land, rel=1e-7)
    assert r.value("impact_speed") == pytest.approx(v_t * math.tanh(9.81 * t_land / v_t),
                                                    rel=1e-7)


def test_projectile_without_drag_is_the_textbook_parabola():
    r = core.run("projectile_drag", {"drag_model": "none", "initial_height": "0 m",
                                     "launch_angle": "45 deg"})
    assert_verified(r)
    assert r.value("range") == pytest.approx(30 ** 2 / 9.81, rel=1e-9)
    assert r.value("max_height") == pytest.approx(30 ** 2 / (4 * 9.81), rel=1e-9)


def test_projectile_sphere_curve_recovers_stokes_drag():
    # 0.1 mm glass bead settling in glycerine-like oil: Stokes terminal velocity
    kw = {"mass": "1.309e-9 kg", "diameter": "0.1 mm", "initial_speed": "0 m/s",
          "initial_height": "1 mm", "drag_model": "sphere_curve",
          "air_density": "1 kg/m^3", "air_viscosity": "1 Pa*s"}
    r = core.run("projectile_drag", kw)
    stokes = 1.309e-9 * 9.81 / (3 * math.pi * 1.0 * 1e-4)
    assert r.value("terminal_velocity") == pytest.approx(stokes, rel=1e-3)


def test_projectile_drag_crisis_is_flagged():
    r = core.run("projectile_drag", {"initial_speed": "70 m/s"})
    assert status(r, "Reynolds regime") == ["warn"]


def test_projectile_bad_inputs():
    assert status(core.run("projectile_drag", {"mass": "5 m"}),
                  "dimensional analysis") == ["fail"]
    r = core.run("projectile_drag", {"initial_height": "0 m", "launch_angle": "-10 deg"})
    assert not r.ok and status(r, "well-posedness") == ["fail"]
