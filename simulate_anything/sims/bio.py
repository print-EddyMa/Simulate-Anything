"""Bio & medicine: heat from implants in perfused tissue, drug diffusion."""
from __future__ import annotations

import math

import numpy as np
from scipy.optimize import brentq
from scipy.special import erfc, erfcx

from .. import checks as ck
from ..core import Field, Param, ParamError, Simulation, Solution, register
from ..solvers.transport import (Grid, TransportProblem, dirichlet, neumann, robin,
                                 transient_benchmark)
from ..units import Q
from ._heat import bounds_check, entropy_check, to_c

MASS = "mass transport"


def _crossing(x, v, level) -> float:
    """First x at which a decreasing profile v(x) drops to ``level``."""
    if v[0] <= level:
        return float(x[0])
    below = np.nonzero(v < level)[0]
    if not len(below):
        return float(x[-1])
    i = below[0]
    return float(x[i - 1] + (v[i - 1] - level) * (x[i] - x[i - 1]) / (v[i - 1] - v[i]))


@register
class ImplantHeating(Simulation):
    name = "implant_heating"
    title = "Tissue heating around an implant (Pennes bioheat equation)"
    domain = "bio & medicine"
    description = """Steady temperature rise in perfused tissue around a
    spherical implant that dissipates power (pulse generator, charging coil,
    neural stimulator).  Solves k lap(T) - rho_b c_b w (T - T_art) = 0 on a
    spherical grid with the implant heat flux at its surface; blood perfusion
    carries the heat away over the length delta = sqrt(k / (rho_b c_b w))."""
    params = [
        Param("power", "50 mW", "W", "heat dissipated by the implant", lo=0.001, hi=1.0),
        Param("implant_radius", "5 mm", "m", "radius of the (equivalent) sphere",
              lo=0.001, hi=0.03),
        Param("tissue_conductivity", "0.5 W/(m*K)", "W/(m*K)",
              "tissue thermal conductivity (muscle 0.5, fat 0.2, brain 0.5)",
              lo=0.15, hi=0.7),
        Param("perfusion_rate", "0.5e-3 1/s", "1/s",
              "blood perfusion, m^3 blood per m^3 tissue per s "
              "(resting muscle 0.5e-3, brain 8e-3, fat 0.3e-3)", lo=1e-4, hi=2e-2),
        Param("blood_density", "1060 kg/m^3", "kg/m^3", "blood density"),
        Param("blood_specific_heat", "3600 J/(kg*K)", "J/(kg*K)", "blood specific heat"),
        Param("arterial_temperature", "37 degC", "K", "arterial blood / core temperature"),
        Param("outer_radius", "60 mm", "m",
              "where the tissue domain is truncated (radiating boundary)", lo=0.01, hi=0.3),
        Param("limit", "2 K", "K", "allowed temperature rise at the implant surface "
              "(2 K is the usual active-implant limit)", lo=0.5, hi=6, delta=True),
    ]
    default_resolution = 128
    primary = ("surface_temperature_rise", "distance_to_0.1K_rise")

    def validate(self):
        p = self.p
        if p.outer_radius <= 1.5 * p.implant_radius:
            raise ParamError("outer_radius must be at least 1.5 x implant_radius")
        if p.power <= 0:
            raise ParamError("power must be positive")

    # -- model ---------------------------------------------------------------
    def _hv(self) -> float:
        p = self.p
        return p.blood_density * p.blood_specific_heat * p.perfusion_rate

    def _delta(self) -> float:
        return math.sqrt(self.p.tissue_conductivity / self._hv())

    def _exact(self, r):
        """Rise above arterial temperature in an unbounded perfused medium."""
        p, d = self.p, self._delta()
        a = p.implant_radius
        return (p.power * np.exp(-(np.asarray(r, float) - a) / d)
                / (4 * math.pi * p.tissue_conductivity * np.asarray(r, float) * (1 + a / d)))

    def _exact_distance(self, level) -> float:
        a, big = self.p.implant_radius, self.p.outer_radius
        if self._exact(a) <= level:
            return 0.0
        if self._exact(big) >= level:
            return big - a
        return brentq(lambda r: self._exact(r) - level, a, big) - a

    def _problem(self, n):
        p = self.p
        a, big, k = p.implant_radius, p.outer_radius, p.tissue_conductivity
        # geometric grading: the 1/r part of the field is steepest at the implant
        g = Grid(n, big - a, coord="spherical", xf=a * (big / a) ** (np.arange(n + 1) / n))
        flux = p.power / (4 * math.pi * a ** 2)
        h_out = k * (1 / self._delta() + 1 / big)      # exact for exp(-r/delta)/r
        prob = TransportProblem(
            g, k, sink=self._hv(), source=self._hv() * p.arterial_temperature,
            bcs={"west": neumann(flux), "east": robin(h_out, p.arterial_temperature)})
        return prob, flux

    def equations(self):
        dT = Q(1, "K")
        a = self.q("implant_radius")
        return {"bioheat equation": {
            "conduction k*dT/a^2": self.q("tissue_conductivity") * dT / a ** 2,
            "perfusion rho*c*w*dT": self.q("blood_density") * self.q("blood_specific_heat")
            * self.q("perfusion_rate") * dT,
            "implant heating P/a^3": self.q("power") / a ** 3}}

    def dimensionless(self):
        delta = (self.q("tissue_conductivity") / (
            self.q("blood_density") * self.q("blood_specific_heat")
            * self.q("perfusion_rate"))).sqrt()
        return {"implant radius / perfusion length a/delta": self.q("implant_radius") / delta}

    def well_posed(self):
        return self._problem(8)[0].well_posed(what="temperature")

    def solve(self, n):
        p = self.p
        prob, flux = self._problem(n)
        ts = prob.solve_steady()
        g, T = ts.grid, ts.phi[:, 0]
        k, a, t_art = p.tissue_conductivity, p.implant_radius, p.arterial_temperature
        # surface value: first cell centre plus the spherical-shell resistance
        # of the half cell that separates it from the implant
        t_surf = T[0] + flux * a ** 2 / k * (1 / a - 1 / g.x[0])
        r = np.concatenate([[a], g.x])
        rise = np.concatenate([[t_surf], T]) - t_art
        qoi = {
            "surface_temperature_rise": (rise[0], "K"),
            "surface_temperature": (to_c(t_surf), "degC"),
            "distance_to_1K_rise": ((_crossing(r, rise, 1.0) - a) * 1e3, "mm"),
            "distance_to_0.1K_rise": ((_crossing(r, rise, 0.1) - a) * 1e3, "mm"),
            "margin_to_limit": (p.limit - rise[0], "K"),
            "perfusion_length": (self._delta() * 1e3, "mm"),
        }
        fields = [Field("temperature rise", r * 1e3,
                        {"simulation": rise, "exact (unbounded tissue)": self._exact(r)},
                        unit="K", xlabel="distance from implant centre [mm]",
                        ylabel="rise above arterial [K]")]
        return Solution(qoi, fields, ts.residual, data={"ts": ts, "r": r, "rise": rise})

    def known_solution(self, sol):
        p = self.p
        exact = self._exact(sol.data["r"])
        err = float(np.max(np.abs(sol.data["rise"] - exact))) / float(exact[0])
        out = [ck.close("known solution: surface temperature rise", ck.UNIVERSAL,
                        sol.qoi["surface_temperature_rise"][0], exact[0], 1e-3,
                        detail="exact P exp(-(r-a)/delta) / (4 pi k r (1 + a/delta))",
                        unit="K"),
               ck.small("known solution: whole radial profile", ck.UNIVERSAL, err, 1e-3,
                        "largest deviation from the exact profile, relative to the "
                        "surface rise")]
        for key, level in (("distance_to_1K_rise", 1.0), ("distance_to_0.1K_rise", 0.1)):
            want = self._exact_distance(level) * 1e3
            out.append(ck.close(f"known solution: {key}", ck.UNIVERSAL, sol.qoi[key][0],
                                want, 5e-3, 0.01,
                                detail="root of the exact profile", unit="mm"))
        return out

    def domain_checks(self, sol):
        p = self.p
        ts = sol.data["ts"]
        g, T = ts.grid, ts.phi
        four_pi = g.measure
        inflow = ts.boundary_inflow()
        perf = four_pi * float(np.sum(self._hv() * (T - p.arterial_temperature) * g.vol))
        outer = -four_pi * inflow["east"]
        out = [ck.close("energy balance (implant power = perfusion + far field)", ck.HEAT,
                        perf + outer, p.power, 1e-8,
                        detail=f"blood carries off {perf:.4g} W, {outer:.4g} W conducts "
                               f"past the outer radius", unit="W"),
               ck.close("energy balance: surface flux integrates to the power", ck.HEAT,
                        four_pi * inflow["west"], p.power, 1e-10, unit="W"),
               bounds_check(ts, lo=p.arterial_temperature,
                            why="heating only, so nothing can be cooler than arterial blood")]
        ent = entropy_check(ts, generation=0.0, loss=(self._hv(), p.arterial_temperature))
        if ent.value is not None:
            ent.value *= four_pi
            ent.detail = (f"total entropy production {ent.value:.4g} W/K "
                          f"({'heat only flows from hot to cold' if ent.status == ck.PASS else 'second law violated'})")
        out.append(ent)
        rise = sol.qoi["surface_temperature_rise"][0]
        upper = p.power / (4 * math.pi * p.tissue_conductivity * p.implant_radius)
        vol_delta = 4 / 3 * math.pi * ((p.implant_radius + self._delta()) ** 3
                                       - p.implant_radius ** 3)
        out.append(ck.truth(
            "back-of-envelope R-network bound", ck.HEAT,
            0.0 <= rise <= upper * (1 + 1e-9),
            f"surface rise {rise:.4g} K is below the conduction-only spreading "
            f"resistance P/(4 pi k a) = {upper:.4g} K (perfusion can only lower it); "
            f"order of magnitude P/(rho c w V_delta) = "
            f"{p.power / (self._hv() * vol_delta):.3g} K"))
        return out


def _semi_infinite(x, t, diff, rate):
    """C/C0 in a half-space with surface held at C0, diffusion and first-order
    loss (Danckwerts 1950); reduces to erfc(x / 2 sqrt(D t)) when rate = 0."""
    a = np.asarray(x, float) / (2 * math.sqrt(diff * t))
    b = math.sqrt(rate * t)
    m = math.sqrt(rate / diff)
    return 0.5 * (np.exp(-m * np.asarray(x, float)) * erfc(a - b)
                  + np.exp(-a * a - b * b) * erfcx(a + b))


@register
class DrugDiffusion(Simulation):
    name = "drug_diffusion"
    title = "Drug diffusion into tissue with first-order elimination"
    domain = "bio & medicine"
    description = """A drug held at constant concentration on one face of a
    tissue layer (patch, eluting implant, wound dressing) diffuses in while the
    tissue clears it at a first-order rate: dC/dt = D C'' - k C, no flux at the
    far side.  Reports how deep the drug gets by the end time and what the
    profile settles to (penetration length sqrt(D/k))."""
    params = [
        Param("source_concentration", "1 mg/mL", "kg/m^3", "concentration held at the surface",
              lo=0.01, hi=100),
        Param("diffusivity", "1e-10 m^2/s", "m^2/s",
              "diffusion coefficient in tissue (small molecule ~1e-10, antibody ~1e-11)",
              lo=1e-12, hi=1e-9),
        Param("elimination_rate", "1e-4 1/s", "1/s",
              "first-order clearance rate, ln 2 / half-life", lo=0, hi=1e-2),
        Param("tissue_depth", "5 mm", "m", "thickness of the tissue layer", lo=5e-4, hi=0.05),
        Param("duration", "6 h", "s", "how long the source is applied", lo=60, hi=7 * 86400),
        Param("target_depth", "1 mm", "m", "depth at which the concentration is reported",
              lo=0, hi=0.05),
        Param("threshold", 0.1, "", "penetration level as a fraction of the source",
              lo=0.01, hi=0.9),
    ]
    default_resolution = 128
    primary = ("concentration_at_target", "penetration_depth")

    def validate(self):
        p = self.p
        if not 0 <= p.target_depth <= p.tissue_depth:
            raise ParamError("target_depth must lie inside the tissue")
        if not 0 < p.threshold < 1:
            raise ParamError("threshold must be between 0 and 1")
        if p.duration <= 0 or p.source_concentration <= 0:
            raise ParamError("duration and source_concentration must be positive")
        if p.elimination_rate < 0:
            raise ParamError("elimination_rate cannot be negative")

    def _problem(self, n):
        p = self.p
        g = Grid(n, p.tissue_depth)
        return TransportProblem(g, p.diffusivity, C=1.0, sink=p.elimination_rate,
                                bcs={"west": dirichlet(p.source_concentration)})

    def equations(self):
        c0 = self.q("source_concentration")
        return {"diffusion-reaction equation": {
            "storage C/t": c0 / self.q("duration"),
            "diffusion D*C/L^2": self.q("diffusivity") * c0 / self.q("tissue_depth") ** 2,
            "elimination k*C": self.q("elimination_rate") * c0}}

    def dimensionless(self):
        length, d = self.q("tissue_depth"), self.q("diffusivity")
        return {"Fourier number D*t/L^2": d * self.q("duration") / length ** 2,
                "Thiele modulus L*sqrt(k/D)": length * (self.q("elimination_rate") / d).sqrt(),
                "k*t": self.q("elimination_rate") * self.q("duration")}

    def well_posed(self):
        return self._problem(8).well_posed(steady=False, what="concentration")

    def _steady_exact(self, x):
        p = self.p
        m = math.sqrt(p.elimination_rate / p.diffusivity)
        ml = m * p.tissue_depth
        x = np.asarray(x, float)
        if ml < 1e-9:
            return np.full(x.shape, p.source_concentration)
        # cosh(m (L - x)) / cosh(m L), written to avoid overflow
        return p.source_concentration * (np.exp(-m * x) + np.exp(-m * (2 * p.tissue_depth - x))) \
            / (1 + math.exp(-2 * ml))

    def solve(self, n):
        p = self.p
        prob = self._problem(n)
        g = prob.grid
        nsteps = max(8, n * n // 16)             # dt ~ dx^2: second order overall
        dt = p.duration / nsteps
        coef, rhs = prob.bc_terms("west")
        tally = {"in": 0.0, "out": 0.0}
        shots, want = [], [0.02, 0.1, 0.3, 1.0]

        def record(t, phi):
            if t > 0:                             # backward Euler: rates at the new level
                tally["in"] += dt * float(np.sum(rhs - coef * phi[0, :]))
                tally["out"] += dt * float(np.sum(p.elimination_rate * phi * g.vol))
            if want and t >= want[0] * p.duration * (1 - 1e-9):
                want.pop(0)
                shots.append((t, phi[:, 0].copy()))

        ts = prob.solve_transient(0.0, p.duration, dt, theta=1.0, callback=record)
        steady = prob.solve_steady()
        c0 = p.source_concentration
        x = np.concatenate([[0.0], g.x])
        conc = np.concatenate([[c0], ts.phi[:, 0]])
        conc_ss = np.concatenate([[c0], steady.phi[:, 0]])
        stored = float(np.sum(ts.phi * g.vol))
        stored_ss = float(np.sum(steady.phi * g.vol))
        slowest = p.elimination_rate + p.diffusivity * (math.pi / (2 * p.tissue_depth)) ** 2
        qoi = {
            "penetration_depth": (_crossing(x, conc, p.threshold * c0) * 1e3, "mm"),
            "concentration_at_target": (float(np.interp(p.target_depth, x, conc)), "kg/m^3"),
            "steady_penetration_depth": (_crossing(x, conc_ss, p.threshold * c0) * 1e3, "mm"),
            "steady_concentration_at_target": (
                float(np.interp(p.target_depth, x, conc_ss)), "kg/m^3"),
            "fraction_of_steady_state": (stored / stored_ss, ""),
            "time_to_steady_estimate": (4 / slowest / 3600, "h"),
            "drug_in_tissue": (stored, "kg/m^2"),
        }
        series = {f"t = {t / 3600:.3g} h": np.concatenate([[c0], c]) for t, c in shots}
        series["steady state"] = conc_ss
        fields = [Field("concentration profile", x * 1e3, series, unit="kg/m^3",
                        xlabel="depth [mm]", ylabel="C [kg/m^3]")]
        cfl = (0.0, 1.0, f"backward Euler, unconditionally stable: explicit-part number "
                         f"(1-theta)*dt*A_ii/M_i with theta = 1; the mesh Fourier number "
                         f"is {ts.mesh_number:.3g} over {nsteps} steps, so accuracy - not "
                         f"stability - sets the step")
        return Solution(qoi, fields, ts.residual, cfl=cfl,
                        residual_note="relative residual of the last implicit step",
                        data={"ts": ts, "steady": steady, "x": x, "conc_ss": conc_ss,
                              "tally": tally, "stored": stored})

    def known_solution(self, sol):
        p = self.p
        c0 = p.source_concentration
        exact_ss = self._steady_exact(sol.data["x"])
        err = float(np.max(np.abs(sol.data["conc_ss"] - exact_ss))) / c0
        out = [ck.small("known solution: steady-state profile", ck.UNIVERSAL, err, 2e-3,
                        "largest deviation from C0 cosh(m (L - x)) / cosh(m L), as a "
                        "fraction of C0")]
        name = "known solution: transient half-space profile"
        if p.target_depth <= 0:
            out.append(ck.skip(name, ck.UNIVERSAL, "target depth is the surface itself"))
        else:
            # influence of the far wall ~ the image source at distance 2L - x
            image = float(_semi_infinite(2 * p.tissue_depth - p.target_depth, p.duration,
                                         p.diffusivity, p.elimination_rate))
            exact = c0 * float(_semi_infinite(p.target_depth, p.duration, p.diffusivity,
                                              p.elimination_rate))
            if image > 1e-3 * exact / c0:
                out.append(ck.skip(name, ck.UNIVERSAL,
                                   "the drug has reached the far wall, so the half-space "
                                   "solution no longer applies"))
            else:
                out.append(ck.close(
                    name, ck.UNIVERSAL, sol.qoi["concentration_at_target"][0], exact, 1e-2,
                    detail="exact diffusion-with-reaction solution for a half-space "
                           "(erfc form) at the target depth", unit="kg/m^3"))
        out.append(transient_benchmark())
        return out

    def domain_checks(self, sol):
        p = self.p
        ts, tally = sol.data["ts"], sol.data["tally"]
        stored = sol.data["stored"]
        scale = max(abs(tally["in"]), 1e-300)
        hist = ts.history
        return [
            ck.small("mass balance (influx = stored + eliminated)", MASS,
                     (tally["in"] - stored - tally["out"]) / scale, 1e-8,
                     f"entered {tally['in']:.5g}, still in tissue {stored:.5g}, cleared "
                     f"{tally['out']:.5g} kg/m^2; relative mismatch"),
            ck.bounds("concentration bounds (maximum principle)", MASS, 0.0,
                      p.source_concentration, float(hist["min"].min()),
                      float(hist["max"].max()), unit="kg/m^3",
                      detail="never negative, never above the source"),
            ck.truth("monotone approach to steady state", MASS,
                     bool(np.all(ts.phi <= sol.data["steady"].phi * (1 + 1e-9) + 1e-300)),
                     "the transient profile lies below the steady profile everywhere"),
        ]
