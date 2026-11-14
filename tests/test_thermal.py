"""Heat-transfer templates: defaults verify, answers match theory, and broken
setups are caught by the right check."""
import math

import pytest

from simulate_anything import core
from simulate_anything.sims import thermal  # noqa: F401  (registers templates)

NAMES = ["wall_heat_loss", "conduction_2d", "battery_pack", "fuel_pin",
         "reentry_heating"]
CHECKLIST = ["energy balance", "temperature bounds", "entropy", "R-network"]
CONV = {"type": "convection", "h": "100 W/(m^2*K)", "ambient": "20 degC"}


def temp(value):
    return {"type": "temperature", "value": value}


def find(result, text):
    hits = [c for c in result.report.checks if text in c.name]
    assert hits, f"no check named like '{text}'"
    return hits


def status(result, text):
    return {c.status for c in find(result, text)}


@pytest.mark.parametrize("name", NAMES)
def test_defaults_are_verified(name):
    r = core.run(name)
    assert r.report.verdict == "VERIFIED", r.to_text()
    assert r.elapsed < 5
    for item in CHECKLIST:                      # every checklist item is present
        assert find(r, item)
    assert status(r, "mesh convergence") == {"pass"}
    assert status(r, "known solution") <= {"pass", "skip"}
    assert "pass" in status(r, "known solution")
    assert r.fields and r.to_dict()["fields"]


# -- wall ---------------------------------------------------------------------
def test_wall_matches_series_resistance():
    r = core.run("wall_heat_loss")
    r_total = 1 / 8 + 0.0125 / 0.2 + 0.1 / 0.04 + 0.1 / 0.7 + 1 / 25
    q = 25 / r_total
    assert r.value("heat_flux") == pytest.approx(q, rel=1e-9)
    assert r.value("heat_loss") == pytest.approx(10 * q, rel=1e-9)
    assert r.value("u_value") == pytest.approx(1 / r_total, rel=1e-9)
    assert r.value("inner_surface_temperature") == pytest.approx(20 - q / 8, abs=1e-8)
    assert r.value("T_between_mineral wool_and_brick") == pytest.approx(
        -5 + q * (1 / 25 + 0.1 / 0.7), abs=1e-8)


def test_wall_rejects_bad_inputs():
    bad_unit = core.run("wall_heat_loss", {"layers": [
        {"name": "glass", "thickness": "4 mm", "k": "1 W/m"}]})
    assert status(bad_unit, "dimensional analysis") == {"fail"}
    assert not bad_unit.qoi
    assert not core.run("wall_heat_loss", {"layers": []}).ok
    assert not core.run("wall_heat_loss", {"layers": [{"name": "x", "k": "1 W/(m*K)"}]}).ok
    assert not core.run("wall_heat_loss", {"outside_temperature": "20 degC"}).ok


# -- generic 2-D conduction ---------------------------------------------------
def test_conduction_fourier_series_square():
    r = core.run("conduction_2d", {"left": temp("0 degC"), "right": temp("0 degC"),
                                   "bottom": temp("0 degC"), "top": temp("100 degC")})
    assert r.ok, r.to_text()
    assert status(r, "Fourier series") == {"pass"}
    assert r.value("centre_temperature") == pytest.approx(25.0, abs=1e-6)
    assert status(r, "symmetry about the x") == {"pass"}


def test_conduction_fourier_series_rectangle():
    r = core.run("conduction_2d", {"width": "0.2 m", "left": temp("10 degC"),
                                   "right": temp("50 degC"), "bottom": temp("0 degC"),
                                   "top": temp("100 degC")})
    assert r.ok, r.to_text()
    exact = thermal._series_rect(0.1, 0.05, 0.2, 0.1, 10, 50, 0, 100)
    assert r.value("centre_temperature") == pytest.approx(exact, abs=0.05)


def test_conduction_one_dimensional_network():
    r = core.run("conduction_2d", {
        "left": temp("100 degC"), "bottom": "insulated", "top": "insulated",
        "right": {"type": "convection", "h": "50 W/(m^2*K)", "ambient": "20 degC"}})
    assert r.report.verdict == "VERIFIED", r.to_text()
    q = 80 / (0.1 / (15 * 0.1) + 1 / (50 * 0.1))          # W per metre depth
    assert r.value("heat_in_left") == pytest.approx(q, rel=1e-8)
    assert status(r, "R-network") == {"pass"}
    assert status(r, "exact 1-D profile") == {"pass"}


def test_conduction_generation_slab():
    r = core.run("conduction_2d", {"generation": "1e6 W/m^3", "left": CONV,
                                   "right": CONV, "bottom": "insulated",
                                   "top": "insulated"})
    assert r.report.verdict == "VERIFIED", r.to_text()
    surface = 20 + 1e6 * 0.05 / 100
    assert r.value("centre_temperature") == pytest.approx(
        surface + 1e6 * 0.05 ** 2 / (2 * 15), abs=1e-6)
    assert r.value("heat_generated") == pytest.approx(1e4)
    assert r.value("heat_in_left") == pytest.approx(-5e3, rel=1e-8)


def test_conduction_heated_block_and_inclusion():
    block = core.run("conduction_2d", {"generation": "1e6 W/m^3", "left": CONV,
                                       "right": CONV, "bottom": CONV, "top": CONV})
    assert block.report.verdict == "VERIFIED", block.to_text()
    assert status(block, "symmetry") == {"pass"}
    assert status(block, "R-network") == {"pass"}
    spot = core.run("conduction_2d", {"regions": [
        {"x0": "0.04 m", "x1": "0.06 m", "y0": "0.04 m", "y1": "0.06 m",
         "k": "200 W/(m*K)", "generation": "5e6 W/m^3"}]})
    assert spot.ok, spot.to_text()
    assert spot.value("heat_generated") == pytest.approx(5e6 * 0.02 * 0.02, rel=1e-9)
    assert status(spot, "energy balance") == {"pass"}


def test_conduction_ill_posed_is_caught_before_solving():
    r = core.run("conduction_2d", {"generation": "1e6 W/m^3", "left": "insulated",
                                   "right": "insulated", "bottom": "insulated",
                                   "top": "insulated"})
    assert not r.ok
    assert status(r, "boundary conditions complete") == {"fail"}
    assert not r.qoi                       # never solved
    floating = core.run("conduction_2d", {"left": "insulated", "right": "insulated",
                                          "bottom": "insulated", "top": "insulated"})
    assert status(floating, "boundary conditions complete") == {"fail"}


def test_conduction_bad_boundary_specs():
    wrong_unit = core.run("conduction_2d", {"left": temp("100 W")})
    assert status(wrong_unit, "dimensional analysis") == {"fail"}
    assert not core.run("conduction_2d", {"left": {"type": "heated"}}).ok
    assert not core.run("conduction_2d", {"left": {"type": "convection"}}).ok
    assert not core.run("conduction_2d", {"regions": [
        {"x0": "0.05 m", "x1": "0.5 m", "y0": "0 m", "y1": "0.01 m",
         "k": "1 W/(m*K)"}]}).ok


def test_conduction_bounds_depend_on_source_sign():
    sim = core.get("conduction_2d")(generation="1e5 W/m^3")
    lo, hi, _ = sim._bounds()
    assert lo == pytest.approx(293.15) and hi is None
    sim = core.get("conduction_2d")(generation="-1e5 W/m^3")
    lo, hi, _ = sim._bounds()
    assert lo is None and hi == pytest.approx(373.15)
    sim = core.get("conduction_2d")()
    assert sim._bounds()[:2] == pytest.approx((293.15, 373.15))


# -- battery pack -------------------------------------------------------------
def test_battery_pack_physics():
    r = core.run("battery_pack")
    assert r.value("heat_generation") == pytest.approx(48 * 10 ** 2 * 0.025)
    tau = 2400 * 1000 * (0.3 * 0.15 * 0.2) / (15 * 2 * 0.45 * 0.2)
    assert r.value("time_constant") == pytest.approx(tau)
    lumped = 120 / (15 * 0.18) * (1 - math.exp(-1800 / tau))
    assert r.value("mean_temperature") - 25 == pytest.approx(lumped, rel=0.05)
    assert 25 < r.value("mean_temperature") < r.value("max_temperature") \
        < r.value("steady_max_temperature")
    assert status(r, "CFL") == {"pass"}
    assert status(r, "symmetry") == {"pass"}


def test_battery_pack_lumped_check_knows_its_limits():
    r = core.run("battery_pack", {"conductivity": "0.3 W/(m*K)",
                                  "h": "100 W/(m^2*K)"})
    assert r.ok, r.to_text()
    assert status(r, "R-network") == {"skip"}        # Biot number too large
    assert status(core.run("battery_pack", {"current": "10 V"}),
                  "dimensional analysis") == {"fail"}
    assert not core.run("battery_pack", {"current": "0 A"}).ok


# -- fuel pin -----------------------------------------------------------------
def test_fuel_pin_matches_layered_cylinder():
    r = core.run("fuel_pin")
    a, b, c, ql = 4.1e-3, 4.18e-3, 4.75e-3, 2e4
    t_co = 300 + ql / (2 * math.pi * c * 3e4)
    t_ci = t_co + ql * math.log(c / b) / (2 * math.pi * 15)
    t_fs = t_ci + ql * math.log(b / a) / (2 * math.pi * 0.3)
    t_cl = t_fs + ql / (4 * math.pi * 3)
    assert r.value("clad_outer_temperature") == pytest.approx(t_co, abs=1e-6)
    assert r.value("clad_inner_temperature") == pytest.approx(t_ci, abs=0.01)
    assert r.value("fuel_surface_temperature") == pytest.approx(t_fs, abs=0.02)
    assert r.value("centreline_temperature") == pytest.approx(t_cl, abs=0.1)
    assert r.value("margin_to_melting") == pytest.approx(2800 - t_cl, abs=0.1)
    assert r.value("linear_power_resolved") == pytest.approx(ql, rel=1e-10)
    assert status(core.run("fuel_pin", {"linear_power": "20 kW"}),
                  "dimensional analysis") == {"fail"}


# -- re-entry -----------------------------------------------------------------
def test_reentry_matches_semi_infinite_solid():
    r = core.run("reentry_heating")
    q = 1.7415e-4 * math.sqrt(1e-4 / 1.0) * 4000 ** 3
    assert r.value("heat_flux") == pytest.approx(q, rel=1e-12)
    alpha = 0.6 / (1450 * 1300)
    rise = 2 * q * math.sqrt(alpha * 60 / math.pi) / 0.6
    assert r.value("surface_temperature") - 26.85 == pytest.approx(rise, rel=5e-3)
    assert r.value("back_face_temperature") == pytest.approx(26.85, abs=1e-6)
    assert r.value("radiative_equilibrium_temperature") + 273.15 == pytest.approx(
        (q / (0.85 * 5.670374419e-8)) ** 0.25, rel=1e-9)
    assert status(r, "CFL") == {"pass"}
    assert status(r, "semi-infinite") == {"pass"}


def test_reentry_cfl_violation_is_caught():
    r = core.run("reentry_heating", {"cfl_number": 0.6})
    assert not r.ok
    assert status(r, "CFL") == {"fail"}
    assert status(r, "temperature bounds") == {"fail"}     # the blow-up is visible
    assert status(r, "energy balance") == {"fail"}


def test_reentry_thin_shield_uses_finite_slab_solution():
    r = core.run("reentry_heating", {"shield_thickness": "5 mm"}, resolution=64)
    assert r.ok, r.to_text()
    assert status(r, "semi-infinite") == {"skip"}
    assert status(r, "finite slab") == {"pass"}
    assert status(r, "R-network") == {"pass"}
    assert status(r, "model validity") == {"warn"}         # hotter than radiative eq.
    assert r.value("back_face_temperature") > 100


def test_reentry_dimension_error():
    r = core.run("reentry_heating", {"air_density": "1e-4 kg/m^2"})
    assert status(r, "dimensional analysis") == {"fail"}
