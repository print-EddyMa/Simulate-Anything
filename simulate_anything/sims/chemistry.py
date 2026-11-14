"""Chemistry: tubular reactors with axial dispersion, reaction-diffusion fronts."""
from __future__ import annotations

import math

import numpy as np

from .. import checks as ck
from ..core import Field, Param, ParamError, Simulation, Solution, register
from ..solvers.transport import Grid, TransportProblem, neumann

MASS = "mass transport"


def _danckwerts(z, pe, da):
    """C/C_in along a tubular reactor with axial dispersion, first-order
    reaction and Danckwerts (closed-closed) boundaries; z = x/L."""
    z = np.asarray(z, float)
    a = math.sqrt(1 + 4 * da / pe)
    den = (1 + a) ** 2 - (1 - a) ** 2 * math.exp(-a * pe)
    return 2 * np.exp(pe * z * (1 - a) / 2) * (
        (1 + a) - (1 - a) * np.exp(-a * pe * (1 - z))) / den


@register
class PlugFlowReactor(Simulation):
    name = "plug_flow_reactor"
    title = "Tubular reactor with axial dispersion and first-order reaction"
    domain = "chemistry"
    description = """Steady conversion in a tubular (plug-flow) reactor where
    the reactant is carried at velocity u, mixes axially with dispersion
    coefficient D and reacts at first order: u C' = D C'' - k C, with
    Danckwerts boundary conditions (the total flux entering is u C_in; nothing
    diffuses out of the exit).  The result sits between the two ideal limits:
    plug flow (no mixing) and a perfectly stirred tank."""
    params = [
        Param("length", "2 m", "m", "reactor length", lo=0.1, hi=50),
        Param("diameter", "50 mm", "m", "tube inner diameter", lo=0.005, hi=2),
        Param("velocity", "0.01 m/s", "m/s", "superficial velocity", lo=1e-4, hi=5),
        Param("dispersion", "1e-3 m^2/s", "m^2/s",
              "axial dispersion coefficient (molecular diffusion plus mixing)",
              lo=1e-6, hi=1),
        Param("rate_constant", "0.01 1/s", "1/s", "first-order rate constant", lo=0, hi=10),
        Param("inlet_concentration", "100 mol/m^3", "mol/m^3", "feed concentration",
              lo=1, hi=1e4),
    ]
    default_resolution = 128
    primary = ("outlet_concentration",)

    def validate(self):
        p = self.p
        if p.velocity <= 0:
            raise ParamError("velocity must be positive (flow from inlet to outlet)")
        if p.rate_constant < 0:
            raise ParamError("rate_constant cannot be negative")
        if p.inlet_concentration <= 0 or p.length <= 0 or p.diameter <= 0:
            raise ParamError("inlet_concentration, length and diameter must be positive")

    def _numbers(self):
        p = self.p
        return (p.velocity * p.length / p.dispersion,       # Peclet
                p.rate_constant * p.length / p.velocity)    # Damkoehler

    def _scheme(self, n):
        cell_pe = self.p.velocity * (self.p.length / n) / self.p.dispersion
        return ("central" if cell_pe < 2 else "upwind"), cell_pe

    def _problem(self, n):
        p = self.p
        area = math.pi * p.diameter ** 2 / 4
        g = Grid(n, p.length, ly=area)
        scheme, _ = self._scheme(n)
        return TransportProblem(
            g, p.dispersion, sink=p.rate_constant, ux=p.velocity, scheme=scheme,
            bcs={"west": neumann(p.velocity * p.inlet_concentration)}), area

    def equations(self):
        c = self.q("inlet_concentration")
        return {"species balance": {
            "advection u*C/L": self.q("velocity") * c / self.q("length"),
            "dispersion D*C/L^2": self.q("dispersion") * c / self.q("length") ** 2,
            "reaction k*C": self.q("rate_constant") * c}}

    def dimensionless(self):
        return {"Peclet number u*L/D": self.q("velocity") * self.q("length")
                / self.q("dispersion"),
                "Damkoehler number k*L/u": self.q("rate_constant") * self.q("length")
                / self.q("velocity")}

    def well_posed(self):
        prob, _ = self._problem(8)
        out = [c for c in prob.well_posed(what="concentration")
               if c.name != "boundary conditions complete"]
        out.append(ck.truth(
            "boundary conditions complete", ck.MATH, self.p.velocity > 0,
            "Danckwerts pair: total flux u*C_in prescribed at the inlet, convective "
            "outflow with zero gradient at the exit - the level is set by the feed"))
        return out

    def solve(self, n):
        p = self.p
        prob, area = self._problem(n)
        ts = prob.solve_steady()
        g, conc = ts.grid, ts.phi[:, 0]
        pe, da = self._numbers()
        c_out = float(conc[-1])            # value carried out by the exit face
        qoi = {
            "conversion": (1 - c_out / p.inlet_concentration, ""),
            "outlet_concentration": (c_out, "mol/m^3"),
            "inlet_face_concentration": (float(conc[0]), "mol/m^3"),
            "peclet_number": (pe, ""),
            "damkohler_number": (da, ""),
            "residence_time": (p.length / p.velocity, "s"),
            "reaction_rate": (float(np.sum(p.rate_constant * conc * g.vol[:, 0])), "mol/s"),
        }
        fields = [Field("concentration along reactor", g.x, {
            "simulation": conc,
            "exact (Danckwerts)": p.inlet_concentration * _danckwerts(g.x / p.length, pe, da),
            "ideal plug flow": p.inlet_concentration * np.exp(-da * g.x / p.length)},
            unit="mol/m^3", xlabel="x [m]", ylabel="C [mol/m^3]")]
        return Solution(qoi, fields, ts.residual,
                        data={"ts": ts, "area": area, "n": n})

    def known_solution(self, sol):
        p = self.p
        pe, da = self._numbers()
        ts = sol.data["ts"]
        scheme, _ = self._scheme(sol.data["n"])
        tol = 5e-3 if scheme == "central" else 5e-2
        exact = p.inlet_concentration * _danckwerts(ts.grid.x / p.length, pe, da)
        err = float(np.max(np.abs(ts.phi[:, 0] - exact))) / p.inlet_concentration
        c_exact = p.inlet_concentration * float(_danckwerts(1.0, pe, da))
        return [ck.close("known solution: outlet concentration", ck.UNIVERSAL,
                         sol.qoi["outlet_concentration"][0], c_exact, tol,
                         detail="Danckwerts / Wehner-Wilhelm closed form", unit="mol/m^3"),
                ck.small("known solution: whole axial profile", ck.UNIVERSAL, err, tol,
                         "largest deviation from the exact profile as a fraction of C_in")]

    def domain_checks(self, sol):
        p = self.p
        ts, area = sol.data["ts"], sol.data["area"]
        pe, da = self._numbers()
        scheme, cell_pe = self._scheme(sol.data["n"])
        feed = p.velocity * p.inlet_concentration * area
        out_flow = p.velocity * sol.qoi["outlet_concentration"][0] * area
        reacted = sol.qoi["reaction_rate"][0]
        ratio = sol.qoi["outlet_concentration"][0] / p.inlet_concentration
        pfr, cstr = math.exp(-da), 1 / (1 + da)
        checks = [
            ck.small("mass balance (in = out + reacted)", MASS,
                     (feed - out_flow - reacted) / feed, 1e-8,
                     f"fed {feed:.5g}, left {out_flow:.5g}, reacted {reacted:.5g} mol/s; "
                     f"relative mismatch"),
            ck.truth("ideal-reactor limits bracket the result", MASS,
                     pfr * (1 - 1e-9) <= ratio <= cstr * (1 + 1e-9),
                     f"C_out/C_in = {ratio:.5g} lies between ideal plug flow "
                     f"exp(-Da) = {pfr:.5g} and a stirred tank 1/(1+Da) = {cstr:.5g}"),
            ck.bounds("concentration bounds (maximum principle)", MASS, 0.0,
                      p.inlet_concentration, float(ts.phi.min()), float(ts.phi.max()),
                      unit="mol/m^3", detail="reactant is only consumed"),
        ]
        if scheme == "central":
            checks.append(ck.truth(
                "cell Peclet number (convection scheme)", ck.MATH, True,
                f"cell Peclet u*dx/D = {cell_pe:.3g} < 2: second-order central "
                f"differencing is non-oscillatory"))
        else:
            smear = cell_pe / 2            # numerical / physical dispersion
            checks.append(ck.Check(
                "cell Peclet number (convection scheme)", ck.MATH,
                ck.PASS if smear <= 0.1 else ck.WARN,
                f"cell Peclet u*dx/D = {cell_pe:.3g} >= 2, so first-order upwinding is "
                f"used; it adds numerical dispersion u*dx/2 = {100 * smear:.3g}% of the "
                f"physical D - refine until cell Peclet < 2", cell_pe, 2.0))
        return checks


def _bramson_mean_speed(tau1, tau2) -> float:
    """Mean front speed (units of sqrt(r D)) between scaled times tau1 < tau2.

    A front that starts from a step lags the ideal 2 t by (3/2) ln t (Bramson
    1983), i.e. its speed is 2 - 3/(2 tau) + O(tau^-3/2); this is that law
    averaged over the interval.  Against a grid-converged solution it holds to
    0.05% for tau1 >= 25 (0.5% at tau1 = 12)."""
    return 2 - 1.5 * math.log(tau2 / tau1) / (tau2 - tau1)


@register
class ReactionDiffusion(Simulation):
    name = "reaction_diffusion"
    title = "Reaction-diffusion front (Fisher-KPP)"
    domain = "chemistry"
    description = """A travelling front of an autocatalytic reaction (also a
    flame, an advantageous gene or an invading population):
    du/dt = D u'' + r u (1 - u), where u is the converted fraction.  The reacted
    region invades the fresh one at a speed that tends to 2 sqrt(r D).  Solved
    with explicit time stepping, so the stability (CFL) limit is real."""
    params = [
        Param("diffusivity", "2e-9 m^2/s", "m^2/s", "diffusion coefficient of the autocatalyst",
              lo=1e-11, hi=1e-4),
        Param("rate", "0.5 1/s", "1/s", "autocatalytic growth rate r", lo=1e-4, hi=100),
        Param("domain_length", "10 mm", "m", "length of the tube / strip", lo=1e-4, hi=10),
        Param("duration", "100 s", "s", "simulated time", lo=1, hi=1e6),
        Param("initial_front", "0.5 mm", "m", "region already reacted at t = 0", lo=0, hi=1),
        Param("cfl_number", 0.4, "", "time step as a fraction of the explicit stability "
              "limit dx^2 / (2 D); above 1 the scheme is unstable", lo=0.05, hi=1.5),
    ]
    default_resolution = 1024
    min_resolution = 32
    primary = ("front_speed", "front_position")
    resolution_doc = "grid cells along the domain"

    def validate(self):
        p = self.p
        if min(p.diffusivity, p.rate, p.domain_length, p.duration, p.cfl_number) <= 0:
            raise ParamError("diffusivity, rate, domain_length, duration and cfl_number "
                             "must be positive")
        if not 0 < p.initial_front < p.domain_length:
            raise ParamError("initial_front must lie inside the domain")

    def _scales(self):
        p = self.p
        return math.sqrt(p.diffusivity / p.rate), math.sqrt(p.diffusivity * p.rate)

    def equations(self):
        return {"Fisher-KPP equation": {
            "storage u/t": 1 / self.q("duration"),
            "diffusion D*u/L^2": self.q("diffusivity") / self.q("domain_length") ** 2,
            "reaction r*u": self.q("rate")}}

    def dimensionless(self):
        return {"domain length / front width L*sqrt(r/D)": self.q("domain_length")
                * (self.q("rate") / self.q("diffusivity")).sqrt(),
                "reaction time r*t": self.q("rate") * self.q("duration")}

    def well_posed(self):
        p = self.p
        _, speed = self._scales()
        reach = p.initial_front + 2 * speed * p.duration
        return [ck.truth(
            "boundary conditions complete", ck.MATH, reach <= 0.9 * p.domain_length,
            f"no-flux ends; at 2 sqrt(r D) the front travels to {reach * 1e3:.4g} mm of "
            f"{p.domain_length * 1e3:.4g} mm"
            + ("" if reach <= 0.9 * p.domain_length else
               " - it would hit the far wall: lengthen the domain or shorten the run"))]

    def solve(self, n):
        p = self.p
        g = Grid(n, p.domain_length)
        prob = TransportProblem(g, p.diffusivity)        # no-flux ends
        lap, _ = prob.assemble()
        vol = g.vol[:, 0]
        dx = p.domain_length / n
        dt_limit = dx * dx / (2 * p.diffusivity)
        half = max(1, math.ceil(p.duration / 2 / (p.cfl_number * dt_limit)))
        dt = p.duration / 2 / half
        nsteps = 2 * half
        u = np.where(g.x < p.initial_front, 1.0, 0.0)
        start_mass = float(np.sum(u * vol))
        made, u_min, u_max = 0.0, 0.0, 1.0
        shots, marks = [(0.0, u.copy())], {round(nsteps * f) for f in (0.25, 0.5, 0.75, 1.0)}
        x_half = None
        with np.errstate(over="ignore", invalid="ignore"):
            for k in range(1, nsteps + 1):
                react = p.rate * u * (1 - u)
                made += dt * float(np.sum(react * vol))
                u = u + dt * (react - (lap @ u) / vol)
                u_min, u_max = min(u_min, float(u.min())), max(u_max, float(u.max()))
                if not np.isfinite(u_max) or abs(u_max) > 1e6:
                    u_min, u_max = float("-inf"), float("inf")
                    break                          # blown up: no point in continuing
                if k in marks:
                    shots.append((k * dt, u.copy()))
                if k == half:
                    x_half = self._front(g.x, u)
        blown = not np.isfinite(u_max)
        x_end = float("nan") if blown else self._front(g.x, u)
        speed = (float("nan") if blown or x_half is None
                 else (x_end - x_half) / (p.duration / 2))
        width, asym = self._scales()
        qoi = {
            "front_speed": (speed * 1e6, "um/s"),
            "asymptotic_speed": (2 * asym * 1e6, "um/s"),
            "front_position": (x_end * 1e3, "mm"),
            "front_width_scale": (width * 1e6, "um"),
            "converted_fraction": (float(np.sum(u * vol)) / p.domain_length if not blown
                                   else float("nan"), ""),
        }
        fields = [Field("front profiles", g.x * 1e3,
                        {f"t = {t:.4g} s": prof for t, prof in shots}, unit="",
                        xlabel="x [mm]", ylabel="converted fraction u")]
        cfl = (2 * p.diffusivity * dt / dx ** 2, 1.0,
               f"explicit Euler diffusion number 2 D dt/dx^2 with dt = {dt:.3g} s, "
               f"dx = {dx * 1e6:.3g} um ({nsteps} steps)")
        return Solution(qoi, fields, None, cfl=cfl,
                        residual_note="explicit time stepping - no algebraic system is solved",
                        data={"made": made, "stored": float(np.sum(u * vol)) - start_mass,
                              "u_min": u_min, "u_max": u_max, "dx": dx, "blown": blown})

    @staticmethod
    def _front(x, u) -> float:
        """Position where u crosses 1/2 (linear interpolation)."""
        above = np.nonzero(u >= 0.5)[0]
        if not len(above):
            return float("nan")
        i = above[-1]
        if i == len(u) - 1:
            return float(x[-1])
        return float(x[i] + (u[i] - 0.5) * (x[i + 1] - x[i]) / (u[i] - u[i + 1]))

    def known_solution(self, sol):
        p = self.p
        _, asym = self._scales()
        tau2 = p.rate * p.duration
        tau1 = tau2 / 2
        name = "known solution: front speed"
        if tau1 < 20:
            return [ck.skip(name, ck.UNIVERSAL,
                            f"r*t/2 = {tau1:.3g} < 20: the front has not yet settled into "
                            "its long-time form, run longer")]
        mean = _bramson_mean_speed(tau1, tau2)
        want = asym * mean * 1e6
        short = 100 * (1 - mean / 2)
        return [ck.close(
            name, ck.UNIVERSAL, sol.qoi["front_speed"][0], want, 1e-2,
            detail=f"2 sqrt(r D) = {2 * asym * 1e6:.5g} um/s is only reached as t -> "
                   f"infinity; Bramson's logarithmic delay predicts a {short:.3g}% "
                   f"shortfall when averaged over r*t = {tau1:.3g}..{tau2:.3g}",
            unit="um/s"),
            ck.truth("known solution: front speed below the asymptotic limit", ck.UNIVERSAL,
                     sol.qoi["front_speed"][0] <= 2 * asym * 1e6 * (1 + 1e-3),
                     "a front started from a step approaches 2 sqrt(r D) from below")]

    def domain_checks(self, sol):
        d = sol.data
        width, _ = self._scales()
        scale = max(abs(d["made"]), 1e-300)
        cells = width / d["dx"]
        return [
            ck.small("mass budget (growth of integral u = integral of the reaction)", MASS,
                     (d["stored"] - d["made"]) / scale, 1e-9,
                     f"integral u grew by {d['stored']:.6g} m, reaction produced "
                     f"{d['made']:.6g} m, nothing crosses the no-flux ends; mismatch"),
            ck.bounds("concentration bounds (maximum principle)", MASS, 0.0, 1.0,
                      d["u_min"], d["u_max"], slack=1e-9,
                      detail="converted fraction stays between 0 and 1"),
            ck.Check("front resolved by the grid", ck.MATH,
                     ck.PASS if cells >= 5 else (ck.WARN if cells >= 2.5 else ck.FAIL),
                     f"{cells:.3g} cells per front length sqrt(D/r) (want at least 5, "
                     f"i.e. resolution >= {math.ceil(5 * self.p.domain_length / width)})",
                     cells, 5.0),
        ]
