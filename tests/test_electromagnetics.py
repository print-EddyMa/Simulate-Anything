"""Electromagnetics templates: defaults verify, numbers match theory, and the
right check fails when the setup is broken."""
import math

import numpy as np
import pytest

from simulate_anything import core
from simulate_anything.sims import electromagnetics as em
from simulate_anything.solvers import fdtd
from simulate_anything.solvers.fdtd import C0, EPS0, MU0


def failed(result):
    return [c.name for c in result.report.failed()]


def status(result, fragment):
    hits = [c for c in result.report.checks if fragment in c.name]
    assert hits, f"no check named like '{fragment}'"
    return {c.status for c in hits}


# -- capacitor ---------------------------------------------------------------
def test_capacitor_default_verified():
    r = core.run("capacitor")
    assert r.report.verdict == "VERIFIED", r.to_text()
    assert r.value("fringing_ratio") > 1.2            # w/d = 4: strong fringing
    assert r.value("capacitance_gauss") == pytest.approx(
        r.value("capacitance_energy"), rel=1e-9)
    assert r.value("gap_field") == pytest.approx(10 / 4e-3 / 1e3, rel=1e-3)   # V/d
    for name in ("Gauss's law over closed surfaces", "charge conservation"):
        assert status(r, name) == {"pass"}


def test_capacitor_ideal_is_exact():
    r = core.run("capacitor", {"fringing": False, "relative_permittivity": 4.4,
                               "plate_width": "20 mm", "gap": "0.5 mm"})
    assert r.ok, r.to_text()
    assert r.value("capacitance_gauss") == pytest.approx(
        EPS0 * 4.4 * 0.02 / 0.5e-3 * 1e12, rel=1e-9)
    assert r.value("fringing_ratio") == pytest.approx(1.0, rel=1e-9)


def test_capacitor_dielectric_and_wide_plates_approach_ideal():
    r = core.run("capacitor", {"relative_permittivity": 10, "gap": "2 mm",
                               "plate_width": "24 mm"})
    assert r.ok, r.to_text()
    assert 1.0 < r.value("fringing_ratio") < 1.08


def test_capacitor_wrong_units_fail_dimensional_analysis():
    r = core.run("capacitor", {"gap": "4 kg"})
    assert not r.ok
    assert "dimensional analysis" in failed(r)


def test_capacitor_contradictory_geometry_is_rejected():
    r = core.run("capacitor", {"plate_width": "40 mm"})
    assert not r.ok
    assert "well-posedness" in failed(r)


def test_capacitor_coarse_grid_is_caught():
    r = core.run("capacitor", {"gap": "0.25 mm"}, resolution=32)
    assert not r.ok                                     # gap is thinner than a cell


# -- magnetostatics ----------------------------------------------------------
def test_magnetostatics_default_verified():
    r = core.run("magnetostatics")
    assert r.report.verdict == "VERIFIED", r.to_text()
    assert r.value("force_x_per_length") < 0            # opposite currents repel
    assert abs(r.value("force_y_per_length")) < 1e-9
    for name in ("div B = 0", "Ampere's law", "charge conservation"):
        assert status(r, name) == {"pass"}


def test_coax_benchmark_matches_exact_field():
    c = em.coax_benchmark(128)
    assert c["b_gap"] == pytest.approx(c["b_gap_exact"], rel=5e-3)
    assert c["b_gap_exact"] == pytest.approx(MU0 * 10 / (2 * math.pi * 4e-3))
    assert c["b_in"] == pytest.approx(c["b_in_exact"], rel=2e-2)
    assert c["b_outside"] < 1e-2
    assert c["L"] == pytest.approx(c["L_exact"], rel=1e-2)


def test_two_wire_line_matches_textbook_estimates():
    wires = [{"x": "70 mm", "y": "80 mm", "w": "4 mm", "h": "4 mm", "current": "100 A"},
             {"x": "90 mm", "y": "80 mm", "w": "4 mm", "h": "4 mm", "current": "-100 A"}]
    r = core.run("magnetostatics", {"conductors": wires}, resolution=320)
    assert r.ok, r.to_text()
    # L' = (mu0/pi) ln(D / GMR), GMR of a square bar = 0.44705 * side
    estimate = MU0 / math.pi * math.log(0.02 / (0.44705 * 0.004)) * 1e6
    assert r.value("inductance_per_length") == pytest.approx(estimate, rel=0.03)
    assert abs(r.value("force_x_per_length")) == pytest.approx(
        MU0 * 100 ** 2 / (2 * math.pi * 0.02), rel=0.08)


def test_iron_core_raises_inductance():
    base = core.run("magnetostatics", verify=False).value("inductance_per_length")
    core_spec = {"x": "80 mm", "y": "80 mm", "w": "10 mm", "h": "60 mm", "mu_r": 500}
    r = core.run("magnetostatics", {"core": core_spec})
    assert r.ok, r.to_text()
    assert r.value("inductance_per_length") > 1.5 * base


def test_unreturned_current_is_flagged():
    one = [{"x": "80 mm", "y": "80 mm", "w": "10 mm", "h": "10 mm", "current": "50 A"}]
    r = core.run("magnetostatics", {"conductors": one})
    assert status(r, "current return path") == {"warn"}
    assert r.report.verdict == "PASSED WITH WARNINGS"


def test_magnetostatics_wrong_units_and_bad_geometry():
    bad = [{"x": "80 mm", "y": "80 mm", "w": "10 mm", "h": "10 mm", "current": "50 V"}]
    assert not core.run("magnetostatics", {"conductors": bad}).ok
    out = [{"x": "158 mm", "y": "80 mm", "w": "10 mm", "h": "10 mm", "current": "5 A"}]
    assert "well-posedness" in failed(core.run("magnetostatics", {"conductors": out}))


# -- EMI shield --------------------------------------------------------------
def test_slab_formula_limits():
    lam = C0 / 1e9
    r, t, _ = fdtd.slab_exact(1e9, 4.0, 0.0, lam / 4)        # half-wave window
    assert t == pytest.approx(1.0, abs=1e-12) and r == pytest.approx(0.0, abs=1e-12)
    r, t, _ = fdtd.slab_exact(1e9, 4.0, 0.0, lam / 8)        # quarter-wave: worst case
    assert r == pytest.approx(((1 - 4) / (1 + 4)) ** 2, rel=1e-12)
    assert r + t == pytest.approx(1.0, abs=1e-12)


def test_em_shield_default_verified():
    r = core.run("em_shield")
    assert r.report.verdict == "VERIFIED", r.to_text()
    _, t_exact, _ = fdtd.slab_exact(1e9, 4.0, 5.0, 0.01)
    assert r.value("shielding_effectiveness") == pytest.approx(
        -10 * math.log10(t_exact), abs=0.05)
    assert status(r, "Poynting energy balance") == {"pass"}


def test_em_shield_lossless_slab_conserves_energy():
    r = core.run("em_shield", {"conductivity": "0 S/m", "relative_permittivity": 9,
                               "thickness": "30 mm"})
    assert r.ok, r.to_text()
    assert r.value("reflection") + r.value("transmission") == pytest.approx(1, abs=2e-3)
    assert abs(r.value("absorption")) < 2e-3


def test_em_shield_courant_above_one_fails_cfl():
    r = core.run("em_shield", {"courant": 1.1})
    assert not r.ok
    assert "time-step (CFL) condition" in failed(r)


def test_em_shield_solid_metal_is_rejected_as_under_resolved():
    r = core.run("em_shield", {"conductivity": "5.8e7 S/m", "thickness": "1 mm"})
    assert not r.ok
    names = failed(r)
    assert any("resolvable" in n for n in names)
    detail = next(c.detail for c in r.report.failed())
    assert "skin depth" in detail and "dB" in detail      # advice with the exact answer
    assert r.qoi == {}                                     # never solved


def test_em_shield_wrong_units():
    assert "dimensional analysis" in failed(
        core.run("em_shield", {"conductivity": "5 ohm"}))


# -- antenna array -----------------------------------------------------------
def test_array_factor_formula():
    theta = np.radians([90.0, 0.0])
    af = em.array_factor(theta, 4, math.pi, 0.0)
    assert af[0] == pytest.approx(1.0) and af[1] == pytest.approx(0.0, abs=1e-12)


def test_antenna_array_default_verified():
    r = core.run("antenna_array")
    assert r.report.verdict == "VERIFIED", r.to_text()
    assert r.value("main_lobe_direction") == pytest.approx(90.0, abs=0.5)
    # 4 half-wave-spaced in-phase elements: far-field half-power width 26.3 deg
    assert r.value("half_power_beamwidth") == pytest.approx(26.3, rel=0.03)
    for name in ("Poynting flux through nested", "Poynting flux equals", "div B = 0"):
        assert status(r, name) == {"pass"}


def test_antenna_array_phase_shift_steers_the_beam():
    sim = em.AntennaArray(phase_shift="90 deg")
    sol = sim.solve(16)
    assert sol.qoi["main_lobe_direction"][0] == pytest.approx(120.0, abs=2.5)
    names = {c.name: c.status for c in sim.known_solution(sol)}
    assert names["known solution: beam direction"] == "pass"
    assert names["known solution: pattern of exact line sources"] == "pass"


def test_antenna_array_courant_above_limit_fails_cfl():
    r = core.run("antenna_array", {"courant": 0.8}, resolution=20)
    assert not r.ok
    assert "time-step (CFL) condition" in failed(r)


def test_antenna_array_rejects_bad_inputs():
    assert "dimensional analysis" in failed(
        core.run("antenna_array", {"spacing": "62.5 ms"}))
    assert "well-posedness" in failed(
        core.run("antenna_array", {"spacing": "200 mm", "n_elements": 8}))
