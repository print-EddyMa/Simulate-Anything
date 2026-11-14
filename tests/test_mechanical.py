"""Structures: beams, vibration modes and plane-stress brackets."""
import math

import numpy as np
import pytest

from simulate_anything import core
from simulate_anything.sims import mechanical  # noqa: F401  (registers templates)
from simulate_anything.solvers.fem import BeamFE, PlateQ4


def failed(result):
    return [c.name for c in result.report.failed()]


def check(result, name):
    return next(c for c in result.report.checks if c.name == name)


@pytest.mark.parametrize("name", ["beam", "beam_vibration", "plate_stress"])
def test_defaults_are_verified(name):
    r = core.run(name)
    assert r.report.verdict == "VERIFIED", r.to_text()
    assert r.elapsed < 5


# -- beam -------------------------------------------------------------------
EI = 200e9 * 0.04 * 0.06 ** 3 / 12


def test_simply_supported_centre_load_matches_formula():
    r = core.run("beam")
    assert r.value("max_deflection") == pytest.approx(1000 / (48 * EI) * 1e3, rel=1e-8)
    assert r.value("max_bending_moment") == pytest.approx(250.0, rel=1e-10)
    assert r.value("max_bending_stress") == pytest.approx(250 * 0.03 / (0.04 * 0.06 ** 3 / 12) / 1e6)
    assert r.value("total_reaction") == pytest.approx(1000.0)


@pytest.mark.parametrize("support,coef", [("cantilever", 1 / 8), ("simply_supported", 5 / 384),
                                          ("fixed_fixed", 1 / 384), ("fixed_pinned", 1 / 184.6)])
def test_uniform_load_cases(support, coef):
    w = 2000.0
    r = core.run("beam", {"support": support, "point_load": "0 N",
                          "distributed_load": f"{w} N/m"})
    assert r.report.verdict == "VERIFIED", r.to_text()
    assert r.value("max_deflection") == pytest.approx(coef * w / EI * 1e3, rel=2e-3)


def test_cantilever_tip_load_and_units():
    r = core.run("beam", {"support": "cantilever", "load_position": 1.0,
                          "length": "1000 mm", "point_load": "1 kN"})
    assert r.ok
    assert r.value("max_deflection") == pytest.approx(1000 / (3 * EI) * 1e3, rel=1e-8)
    assert r.value("max_bending_moment") == pytest.approx(1000.0)


def test_general_supports_and_loads():
    r = core.run("beam", {
        "supports": [{"x": "0 m", "type": "pin"}, {"x": "0.6 m", "type": "roller"},
                     {"x": "1 m", "type": "roller"}],
        "point_loads": [{"x": "0.3 m", "force": "500 N"}, {"x": "0.8 m", "force": "300 N"}]})
    assert r.report.verdict == "VERIFIED", r.to_text()
    assert r.value("total_reaction") == pytest.approx(800.0)
    assert check(r, "known solution: beam benchmark").status == "pass"


def test_symmetry_is_checked_when_setup_is_symmetric():
    r = core.run("beam")
    assert check(r, "symmetry about midspan").status == "pass"
    r = core.run("beam", {"load_position": 0.3})
    assert check(r, "symmetry").status == "skip"


def test_under_supported_beam_is_caught_before_solving():
    r = core.run("beam", {"supports": [{"x": "0.5 m", "type": "roller"}]})
    assert "supports restrain rigid-body motion" in failed(r)
    assert r.qoi == {}                      # never solved


def test_wrong_units_fail_dimensional_analysis():
    r = core.run("beam", {"youngs_modulus": "200 N"})
    assert failed(r) == ["dimensional analysis"]
    r = core.run("plate_stress", {"shear_force": "5 Pa"})
    assert failed(r) == ["dimensional analysis"]
    r = core.run("beam_vibration", {"density": "2700 kg/m^2"})
    assert failed(r) == ["dimensional analysis"]


def test_stocky_beam_warns_about_model_validity():
    r = core.run("beam", {"length": "0.2 m"})
    assert check(r, "model validity: slender beam").status == "warn"
    assert r.report.verdict == "PASSED WITH WARNINGS"


def test_corrupted_reactions_break_the_balance():
    sim = mechanical.Beam()
    sol = sim.solve(16)
    sol.data["sol"].r[0] *= 1.01            # tamper with one support reaction
    names = {c.name: c.status for c in sim.domain_checks(sol)}
    assert names["force balance (sum F = 0)"] == "fail"
    assert names["reaction forces equal applied loads"] == "fail"


# -- vibration ----------------------------------------------------------------
@pytest.mark.parametrize("support", ["cantilever", "simply_supported", "fixed_fixed",
                                     "free_free"])
def test_natural_frequencies(support):
    r = core.run("beam_vibration", {"support": support})
    assert r.report.verdict == "VERIFIED", r.to_text()
    L, E, rho, b, h = 0.5, 69e9, 2700.0, 0.03, 0.005
    c = math.sqrt(E * (b * h ** 3 / 12) / (rho * b * h)) / (2 * math.pi * L ** 2)
    for k, beta in enumerate(mechanical._BETA[support]):
        assert r.value(f"f{k + 1}") == pytest.approx(beta ** 2 * c, rel=1e-5)


def test_frequency_scales_with_thickness_and_length():
    f = core.run("beam_vibration").value("f1")
    assert core.run("beam_vibration", {"height": "10 mm"}).value("f1") == pytest.approx(2 * f, rel=1e-6)
    assert core.run("beam_vibration", {"length": "1 m"}).value("f1") == pytest.approx(f / 4, rel=1e-6)


def test_consistent_mass_converges_at_fourth_order():
    def f3(n):
        return mechanical.BeamVibration(support="simply_supported").solve(n).qoi["f3"][0]
    exact = mechanical.BeamVibration(support="simply_supported")._exact()[2]
    e1, e2 = f3(8) - exact, f3(16) - exact
    assert e1 > e2 > 0                      # conforming FE: frequencies from above
    assert math.log(e1 / e2, 2) > 3.5


# -- plate / bracket ------------------------------------------------------------
def test_bracket_matches_beam_theory():
    r = core.run("plate_stress")
    P, L, H, t, E, nu = 200.0, 0.1, 0.02, 0.005, 69e9, 0.33
    I = t * H ** 3 / 12
    timo = P * L ** 3 / (3 * E * I) + P * L / (5 / 6 * E / (2 * (1 + nu)) * H * t)
    assert r.value("tip_deflection") == pytest.approx(timo * 1e3, rel=0.01)
    assert r.value("fibre_stress") == pytest.approx(P * (L / 2) * (H / 2) / I / 1e6, rel=1e-3)
    assert check(r, "symmetry about the mid-line").status == "pass"


def test_axial_load_is_exact_without_poisson_effect():
    r = core.run("plate_stress", {"shear_force": "0 N", "axial_force": "1 kN",
                                  "poisson_ratio": 0.0})
    assert r.report.verdict == "VERIFIED", r.to_text()
    assert r.value("axial_elongation") == pytest.approx(1000 * 0.1 / (69e9 * 1e-4) * 1e3, rel=1e-9)
    assert r.value("fibre_stress") == pytest.approx(10.0, rel=1e-9)


def test_plane_strain_and_combined_load():
    r = core.run("plate_stress", {"axial_force": "1 kN", "mode": "plane_strain"})
    assert r.ok, r.to_text()
    assert r.value("tip_deflection") < core.run("plate_stress").value("tip_deflection")


def test_no_shear_locking_on_a_coarse_mesh():
    """A plain Q4 would be far too stiff with one element through the depth."""
    P, L, H, t, E = 100.0, 1.0, 0.05, 0.01, 1e9
    fe = PlateQ4(L, H, 10, 1, E, 0.0, t)
    left = fe.node(0, np.arange(2))
    sol = fe.solve(list(2 * left) + list(2 * left + 1), fe.edge_load(0.0, -P))
    tip = -sol.u[2 * fe.node(10, 0) + 1]
    assert tip == pytest.approx(P * L ** 3 / (3 * E * t * H ** 3 / 12), rel=0.02)


def test_plate_needs_a_load_and_valid_poisson_ratio():
    assert not core.run("plate_stress", {"shear_force": "0 N"}).ok
    assert not core.run("plate_stress", {"poisson_ratio": 0.5}).ok


# -- solver-level -----------------------------------------------------------------
def test_beam_fe_fields_are_exact_inside_elements():
    L, ei, w = 2.0, 1.0e4, 300.0
    fe = BeamFE(np.linspace(0, L, 3), ei)          # just two elements
    sol = fe.solve([0, 4], fe.load_vector(w=-w), w=-w)
    x = np.linspace(0, L, 41)
    exact = w * x * (L ** 3 - 2 * L * x ** 2 + x ** 3) / (24 * ei)
    assert np.allclose(-sol.deflection(x), exact, rtol=1e-10, atol=1e-14)
    assert np.allclose(sol.moment(x), w * x * (L - x) / 2, atol=1e-8)   # sagging > 0
    assert np.allclose(sol.shear(x[:-1]), w * (L / 2 - x[:-1]), atol=1e-8)
    assert sol.max_moment() == pytest.approx(w * L ** 2 / 8)
