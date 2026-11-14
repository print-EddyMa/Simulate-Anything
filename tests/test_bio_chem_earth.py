"""Bio & medicine, chemistry and groundwater templates."""
import math

import numpy as np
import pytest

from simulate_anything import checks as ck
from simulate_anything import core
from simulate_anything.sims import bio, chemistry, earth  # noqa: F401  (registers)

NAMES = ["implant_heating", "drug_diffusion", "plug_flow_reactor",
         "reaction_diffusion", "groundwater_well"]


def status(result, fragment):
    found = [c for c in result.report.checks if fragment in c.name]
    assert found, f"no check named like '{fragment}'"
    return {c.status for c in found}


@pytest.fixture(scope="module")
def defaults():
    return {name: core.run(name) for name in NAMES}


@pytest.mark.parametrize("name", NAMES)
def test_defaults_are_verified(defaults, name):
    result = defaults[name]
    assert result.report.verdict == "VERIFIED", result.to_text()
    assert result.elapsed < 5


@pytest.mark.parametrize("name", NAMES)
def test_checklist_is_present(defaults, name):
    names = " | ".join(c.name for c in defaults[name].report.checks)
    for item in ("dimensional analysis", "mesh convergence", "known solution",
                 "boundary conditions complete"):
        assert item in names, f"{name} lacks '{item}'"
    assert any(word in names for word in ("balance", "budget", "mass in = mass out"))


# -- implant heating -----------------------------------------------------------
def test_implant_surface_rise_matches_closed_form(defaults):
    sim = bio.ImplantHeating()
    p = sim.p
    delta = math.sqrt(p.tissue_conductivity / (
        p.blood_density * p.blood_specific_heat * p.perfusion_rate))
    exact = p.power / (4 * math.pi * p.tissue_conductivity * p.implant_radius
                       * (1 + p.implant_radius / delta))
    got = defaults["implant_heating"].value("surface_temperature_rise")
    assert got == pytest.approx(exact, rel=1e-3)
    assert got < p.power / (4 * math.pi * p.tissue_conductivity * p.implant_radius)


def test_implant_rise_scales_linearly_with_power():
    one = core.run("implant_heating", {"power": "20 mW"}, verify=False)
    two = core.run("implant_heating", {"power": "40 mW"}, verify=False)
    assert two.value("surface_temperature_rise") == pytest.approx(
        2 * one.value("surface_temperature_rise"), rel=1e-10)


def test_implant_more_perfusion_means_cooler():
    slow = core.run("implant_heating", {"perfusion_rate": "0.3e-3 1/s"}, verify=False)
    fast = core.run("implant_heating", {"perfusion_rate": "8e-3 1/s"}, verify=False)
    assert fast.value("surface_temperature_rise") < slow.value("surface_temperature_rise")


def test_implant_wrong_units_fail_dimension_check():
    result = core.run("implant_heating", {"power": "50 mA"})
    assert not result.ok
    assert status(result, "dimensional analysis") == {ck.FAIL}


def test_implant_truncation_radius_does_not_matter():
    near = core.run("implant_heating", {"outer_radius": "30 mm"}, verify=False)
    far = core.run("implant_heating", {"outer_radius": "120 mm"}, verify=False)
    assert near.value("surface_temperature_rise") == pytest.approx(
        far.value("surface_temperature_rise"), rel=2e-3)


# -- drug diffusion --------------------------------------------------------------
def test_drug_steady_penetration_is_ln10_over_m(defaults):
    sim = bio.DrugDiffusion()
    m = math.sqrt(sim.p.elimination_rate / sim.p.diffusivity)
    # cosh(m (L - x)) / cosh(m L) = 0.1
    exact = sim.p.tissue_depth - math.acosh(0.1 * math.cosh(m * sim.p.tissue_depth)) / m
    got = defaults["drug_diffusion"].value("steady_penetration_depth")
    assert got == pytest.approx(exact * 1e3, rel=2e-3)


def test_drug_pure_diffusion_matches_erfc():
    result = core.run("drug_diffusion", {"elimination_rate": 0, "duration": "1 h"})
    assert result.report.verdict == "VERIFIED", result.to_text()
    exact = math.erfc(1e-3 / (2 * math.sqrt(1e-10 * 3600)))
    assert result.value("concentration_at_target") == pytest.approx(exact, rel=2e-3)
    assert status(result, "transient half-space profile") == {ck.PASS}


def test_drug_half_space_check_skipped_once_far_wall_matters():
    result = core.run("drug_diffusion", {"elimination_rate": 0, "duration": "48 h",
                                         "tissue_depth": "2 mm"})
    assert status(result, "transient half-space profile") == {ck.SKIP}
    assert result.ok, result.to_text()


def test_drug_wrong_units_fail_dimension_check():
    result = core.run("drug_diffusion", {"diffusivity": "1e-10 m/s"})
    assert status(result, "dimensional analysis") == {ck.FAIL}


def test_drug_target_outside_tissue_is_rejected():
    result = core.run("drug_diffusion", {"target_depth": "9 mm"})
    assert not result.ok


# -- plug-flow reactor ------------------------------------------------------------
def test_reactor_matches_danckwerts(defaults):
    pe, da = 20.0, 2.0
    a = math.sqrt(1 + 4 * da / pe)
    exact = 4 * a * math.exp(pe / 2) / ((1 + a) ** 2 * math.exp(a * pe / 2)
                                        - (1 - a) ** 2 * math.exp(-a * pe / 2))
    got = defaults["plug_flow_reactor"].value("outlet_concentration") / 100.0
    assert got == pytest.approx(exact, rel=1e-3)
    assert math.exp(-da) < got < 1 / (1 + da)


def test_reactor_limits():
    plug = core.run("plug_flow_reactor", {"dispersion": "1e-5 m^2/s"}, 4096, verify=False)
    tank = core.run("plug_flow_reactor", {"dispersion": "100 m^2/s"}, verify=False)
    assert plug.value("conversion") == pytest.approx(1 - math.exp(-2), rel=2e-3)
    assert tank.value("conversion") == pytest.approx(1 - 1 / 3, rel=2e-3)


def test_reactor_coarse_grid_at_high_peclet_is_flagged():
    result = core.run("plug_flow_reactor", {"dispersion": "1e-5 m^2/s"})
    assert status(result, "cell Peclet") == {ck.WARN}
    fine = core.run("plug_flow_reactor", {"dispersion": "1e-5 m^2/s"}, 2048)
    assert status(fine, "cell Peclet") == {ck.PASS}
    assert fine.report.verdict == "VERIFIED"


def test_reactor_wrong_units_fail_dimension_check():
    result = core.run("plug_flow_reactor", {"velocity": "0.01 m"})
    assert status(result, "dimensional analysis") == {ck.FAIL}


def test_reactor_backflow_is_rejected():
    assert not core.run("plug_flow_reactor", {"velocity": "-0.01 m/s"}).ok


# -- Fisher-KPP front ---------------------------------------------------------------
def test_front_speed_approaches_two_sqrt_rd(defaults):
    result = defaults["reaction_diffusion"]
    limit = result.value("asymptotic_speed")
    assert limit == pytest.approx(2 * math.sqrt(2e-9 * 0.5) * 1e6)
    speed = result.value("front_speed")
    assert 0.96 * limit < speed < limit          # slow approach from below
    assert speed == pytest.approx(limit * chemistry._bramson_mean_speed(25, 50) / 2, rel=1e-2)


def test_front_cfl_violation_is_caught():
    result = core.run("reaction_diffusion", {"cfl_number": 1.3})
    assert not result.ok
    assert status(result, "time-step (CFL)") == {ck.FAIL}
    assert status(result, "concentration bounds") == {ck.FAIL}


def test_front_at_the_cfl_limit_edge_still_passes():
    result = core.run("reaction_diffusion", {"cfl_number": 0.9}, 512, verify=False)
    assert math.isfinite(result.value("front_speed"))


def test_front_domain_too_short_is_ill_posed():
    result = core.run("reaction_diffusion", {"duration": "400 s"})
    assert status(result, "boundary conditions complete") == {ck.FAIL}
    assert not result.qoi                       # refused before solving


def test_front_under_resolved_grid_is_caught():
    result = core.run("reaction_diffusion", {"rate": "2 1/s", "duration": "40 s"}, 512)
    assert status(result, "front resolved") == {ck.FAIL}
    assert not result.ok


# -- groundwater ----------------------------------------------------------------------
def test_conformal_radius_constant():
    assert earth.CONFORMAL == pytest.approx(0.539352, abs=1e-6)


def test_groundwater_matches_thiem(defaults):
    q, t = 1000 / 86400, 500 / 86400
    exact = q / (2 * math.pi * t) * math.log(earth.CONFORMAL * 1000 / 150)
    assert defaults["groundwater_well"].value("drawdown_at_observation") == pytest.approx(
        exact, rel=2e-3)
    assert defaults["groundwater_well"].value("boundary_inflow") == pytest.approx(1000)


def test_groundwater_thiem_difference_between_two_radii():
    near = core.run("groundwater_well", {"observation_radius": "100 m"}, 192, verify=False)
    far = core.run("groundwater_well", {"observation_radius": "200 m"}, 192, verify=False)
    q, t = 1000 / 86400, 500 / 86400
    assert (near.value("drawdown_at_observation") - far.value("drawdown_at_observation")
            == pytest.approx(q / (2 * math.pi * t) * math.log(2), rel=3e-3))


def test_groundwater_head_never_exceeds_boundary(defaults):
    head = np.array(defaults["groundwater_well"].fields[0].values)
    assert head.max() <= 50 + 1e-9


def test_groundwater_overpumping_warns():
    result = core.run("groundwater_well", {"pumping_rate": "40000 m^3/day"})
    assert status(result, "aquifer stays saturated") == {ck.WARN}


def test_groundwater_wrong_units_fail_dimension_check():
    result = core.run("groundwater_well", {"transmissivity": "500 m/day"})
    assert status(result, "dimensional analysis") == {ck.FAIL}


def test_groundwater_observation_outside_block_is_rejected():
    assert not core.run("groundwater_well", {"observation_radius": "600 m"}).ok
