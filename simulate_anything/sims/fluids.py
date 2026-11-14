"""Fluid mechanics: pipe flow, cavity and channel Navier-Stokes, cooling
channels and drag on a thrown ball."""
from __future__ import annotations

import math

import numpy as np
from scipy.optimize import brentq

from .. import checks as ck
from ..core import Field, Param, ParamError, Simulation, Solution, register
from ..solvers.navier_stokes import IncompressibleFlow2D
from ..solvers.transport import (Grid, TransportProblem, dirichlet, neumann,
                                 operator_benchmark)
from ..units import Q
from ._heat import bounds_check, to_c

G0 = 9.80665
RE_LAMINAR, RE_TURBULENT = 2300.0, 4000.0


def _positive(sim, *names):
    for n in names:
        if not getattr(sim.p, n) > 0:
            raise ParamError(f"{n} must be positive")


def _even(n: int) -> int:
    return max(4, 2 * int(round(n / 2)))


# ---------------------------------------------------------------------------
# friction-factor correlations
# ---------------------------------------------------------------------------
def colebrook(re: float, rel_rough: float):
    """Darcy friction factor from the Colebrook-White equation (Newton).
    Returns (f, residual of the equation)."""
    a, b = rel_rough / 3.7, 2.51 / re
    x = 1.0 / math.sqrt(haaland(re, rel_rough))
    for _ in range(50):
        F = x + 2.0 * math.log10(a + b * x)
        dF = 1.0 + 2.0 * b / ((a + b * x) * math.log(10))
        step = F / dF
        x -= step
        if abs(step) < 1e-14 * abs(x):
            break
    res = abs(x + 2.0 * math.log10(a + b * x)) / abs(x)
    return 1.0 / x ** 2, res


def haaland(re: float, rel_rough: float) -> float:
    return (-1.8 * math.log10((rel_rough / 3.7) ** 1.11 + 6.9 / re)) ** -2


def swamee_jain(re: float, rel_rough: float) -> float:
    return 0.25 / math.log10(rel_rough / 3.7 + 5.74 / re ** 0.9) ** 2


@register
class PipeFlow(Simulation):
    name = "pipe_flow"
    title = "Pipe flow: pressure drop, friction factor, velocity profile"
    domain = "fluids"
    description = """Fully developed flow in a round pipe.  The Reynolds number
    decides the physics: laminar flow is solved numerically from the momentum
    equation (and must reproduce Hagen-Poiseuille), turbulent flow uses the
    Colebrook friction factor (and must sit on the Moody chart).  Works for
    water lines, air ducts, blood vessels, microfluidics."""
    params = [
        Param("diameter", "25 mm", "m", "inner diameter", lo=0.0005, hi=1.0),
        Param("length", "10 m", "m", "pipe length", lo=0.01, hi=1000),
        Param("flow_rate", "0.03 L/s", "m^3/s", "volumetric flow rate", lo=1e-7, hi=0.05),
        Param("density", "998 kg/m^3", "kg/m^3", "fluid density (water 998, air 1.2, blood 1060)",
              lo=0.5, hi=14000),
        Param("viscosity", "1.0e-3 Pa*s", "Pa*s",
              "dynamic viscosity (water 1.0e-3, air 1.8e-5, blood ~3.5e-3)", lo=1e-6, hi=10),
        Param("roughness", "0.0015 mm", "m",
              "wall roughness (drawn tube 0.0015 mm, steel 0.045 mm, cast iron 0.26 mm)",
              lo=0.0, hi=0.005),
    ]
    default_resolution = 64
    primary = ("pressure_drop", "max_velocity")
    resolution_doc = "radial cells across the pipe radius (laminar solve)"

    def validate(self):
        p = self.p
        _positive(self, "diameter", "length", "flow_rate", "density", "viscosity")
        if p.roughness < 0 or p.roughness >= 0.05 * p.diameter:
            raise ParamError("roughness must be between 0 and 5% of the diameter")
        self.area = math.pi * p.diameter ** 2 / 4
        self.V = p.flow_rate / self.area
        self.Re = p.density * self.V * p.diameter / p.viscosity
        self.regime = ("laminar" if self.Re < RE_LAMINAR else
                       "transitional" if self.Re < RE_TURBULENT else "turbulent")
        if self.regime == "laminar":
            self.entry = 0.06 * self.Re * p.diameter
        else:
            self.entry = 4.4 * self.Re ** (1 / 6) * p.diameter
            self.primary = ()
            self.no_mesh_reason = ("turbulent friction comes from the algebraic "
                                   "Colebrook correlation, which has no grid")

    def _problem(self, n):
        g = Grid(n, self.p.diameter / 2, coord="cylindrical")
        return TransportProblem(g, self.p.viscosity, source=1.0,
                                bcs={"east": dirichlet(0.0)})

    def equations(self):
        v = Q(self.V, "m/s")
        return {"pipe momentum balance": {
            "pressure gradient dp/L": Q(1, "Pa") / self.q("length"),
            "viscous mu*V/D^2": self.q("viscosity") * v / self.q("diameter") ** 2,
            "inertial rho*V^2/D": self.q("density") * v ** 2 / self.q("diameter")}}

    def dimensionless(self):
        return {"Reynolds number rho*V*D/mu": self.q("density") * Q(self.V, "m/s")
                * self.q("diameter") / self.q("viscosity"),
                "relative roughness eps/D": self.q("roughness") / self.q("diameter")}

    def well_posed(self):
        p = self.p
        if self.regime == "laminar":
            out = self._problem(8).well_posed(what="velocity (no-slip wall)")
        else:
            out = [ck.truth("boundary conditions complete", ck.MATH, True,
                            "flow rate, fluid and pipe geometry fix the developed flow")]
        out.append(ck.truth(
            "model validity: fully developed flow", ck.MATH, p.length >= self.entry,
            f"entrance length ~{self.entry:.3g} m vs pipe length {p.length:.3g} m"
            + ("" if p.length >= self.entry else
               " - flow is still developing, real pressure drop will be higher"),
            warn=True))
        return out

    def solve(self, n):
        p = self.p
        R, dyn = p.diameter / 2, 0.5 * p.density * self.V ** 2
        data = {"regime": self.regime}
        if self.regime == "laminar":
            ts = self._problem(n).solve_steady()
            g = ts.grid
            unit = ts.phi[:, 0]                              # u for dp/dx = -1
            grad = p.flow_rate / (g.measure * float(np.sum(unit * g.vol[:, 0])))
            u = unit * grad
            dp = grad * p.length
            u_max = u[0] + grad * (g.dxs[0] / 2) ** 2 / (4 * p.viscosity)
            tau_w = -ts.boundary_inflow()["east"] * grad / R   # from the wall flux
            f = dp / (p.length / p.diameter * dyn)
            r_nodes = np.concatenate([[0.0], g.x, [R]])
            u_nodes = np.concatenate([[u_max], u, [0.0]])
            data.update(ts=ts, q_check=float(2 * math.pi * np.trapezoid(
                u_nodes * r_nodes, r_nodes)))
            fields = [Field("velocity profile", np.concatenate([-r_nodes[::-1], r_nodes[1:]]) * 1e3,
                            np.concatenate([u_nodes[::-1], u_nodes[1:]]), unit="m/s",
                            xlabel="radial position [mm]", ylabel="u [m/s]")]
            residual, note = ts.residual, "linear solve of the radial momentum equation"
        else:
            rel = p.roughness / p.diameter
            f, residual = colebrook(self.Re, rel)
            dp = f * p.length / p.diameter * dyn
            tau_w = f * dyn / 4
            u_max = self.V * (1 + 1.33 * math.sqrt(f))       # log-law estimate
            note = "residual of the Colebrook equation after Newton iteration"
            logre = np.linspace(math.log10(RE_TURBULENT), 8, 80)
            fields = [Field("Moody curve for this roughness", logre,
                            [colebrook(10 ** x, rel)[0] for x in logre], unit="",
                            xlabel="log10(Reynolds number)", ylabel="friction factor")]
        qoi = {"reynolds_number": (self.Re, ""),
               "pressure_drop": (dp, "Pa"),
               "friction_factor": (f, ""),
               "mean_velocity": (self.V, "m/s"),
               "max_velocity": (u_max, "m/s"),
               "wall_shear_stress": (tau_w, "Pa"),
               "pumping_power": (dp * p.flow_rate, "W"),
               "head_loss": (dp / (p.density * G0), "m"),
               "entrance_length": (self.entry, "m")}
        return Solution(qoi, fields, residual, note, 1e-8, data=data)

    def known_solution(self, sol):
        p = self.p
        dp = sol.qoi["pressure_drop"][0]
        if self.regime == "laminar":
            hp = 128 * p.viscosity * p.length * p.flow_rate / (math.pi * p.diameter ** 4)
            return [ck.close("known solution: Hagen-Poiseuille pressure drop", ck.UNIVERSAL,
                             dp, hp, 5e-3, detail="128 mu L Q / (pi D^4)", unit="Pa"),
                    ck.close("known solution: centreline velocity = 2 x mean", ck.UNIVERSAL,
                             sol.qoi["max_velocity"][0], 2 * self.V, 5e-3, unit="m/s")]
        f_h = haaland(self.Re, p.roughness / p.diameter)
        return [ck.close("known solution: Moody chart (Haaland fit)", ck.UNIVERSAL,
                         sol.qoi["friction_factor"][0], f_h, 0.03,
                         detail="Colebrook friction factor vs the explicit Haaland formula")]

    def domain_checks(self, sol):
        p = self.p
        dp, tau = sol.qoi["pressure_drop"][0], sol.qoi["wall_shear_stress"][0]
        out = []
        if self.regime == "laminar":
            out.append(ck.truth("Reynolds regime", ck.FLUID, True,
                                f"Re = {self.Re:.4g} < {RE_LAMINAR:.0f}: laminar, the "
                                "parabolic-profile solve applies"))
            out.append(ck.close("mass conservation (profile integrates to Q)", ck.FLUID,
                                sol.data["q_check"], p.flow_rate, 5e-3,
                                detail="flow rate re-integrated from the velocity profile "
                                "with an independent quadrature", unit="m^3/s"))
            hp = 128 * p.viscosity * p.length * p.flow_rate / (math.pi * p.diameter ** 4)
            out.append(ck.close("pressure drop vs Hagen-Poiseuille", ck.FLUID, dp, hp,
                                5e-3, unit="Pa"))
            out.append(ck.close("wall force balance (dp*A = tau_w*pi*D*L)", ck.FLUID,
                                dp * self.area, tau * math.pi * p.diameter * p.length,
                                1e-6, detail="pressure force vs wall shear from the "
                                "computed velocity gradient", unit="N"))
        else:
            if self.regime == "transitional":
                out.append(ck.Check(
                    "Reynolds regime", ck.FLUID, ck.WARN,
                    f"Re = {self.Re:.4g} is transitional ({RE_LAMINAR:.0f}-"
                    f"{RE_TURBULENT:.0f}): flow may switch between laminar and "
                    "turbulent, so the answer is uncertain. Turbulent (higher) value "
                    f"reported; laminar would give "
                    f"{128 * p.viscosity * p.length * p.flow_rate / (math.pi * p.diameter ** 4):.4g} Pa"))
            else:
                out.append(ck.truth("Reynolds regime", ck.FLUID, True,
                                    f"Re = {self.Re:.4g} > {RE_TURBULENT:.0f}: turbulent, "
                                    "Colebrook / Moody friction applies"))
            out.append(ck.skip("mass conservation (profile integrates to Q)", ck.FLUID,
                               "the turbulent friction model has no velocity field; "
                               "continuity reduces to V = Q/A"))
            f_sj = swamee_jain(self.Re, p.roughness / p.diameter)
            dp_sj = f_sj * p.length / p.diameter * 0.5 * p.density * self.V ** 2
            out.append(ck.close("pressure drop vs Moody chart", ck.FLUID, dp, dp_sj, 0.05,
                                detail="Darcy-Weisbach with the Swamee-Jain fit of the "
                                "Moody chart", unit="Pa"))
            out.append(ck.skip("wall force balance (dp*A = tau_w*pi*D*L)", ck.FLUID,
                               "wall shear is derived from this balance in the "
                               "algebraic model"))
        return out


# ---------------------------------------------------------------------------
# lid-driven cavity
# ---------------------------------------------------------------------------
GHIA_Y = [1.0, 0.9766, 0.9688, 0.9609, 0.9531, 0.8516, 0.7344, 0.6172, 0.5, 0.4531,
          0.2813, 0.1719, 0.1016, 0.0703, 0.0625, 0.0547, 0.0]
GHIA_U = {
    100: [1.0, 0.84123, 0.78871, 0.73722, 0.68717, 0.23151, 0.00332, -0.13641,
          -0.20581, -0.21090, -0.15662, -0.10150, -0.06434, -0.04775, -0.04192,
          -0.03717, 0.0],
    400: [1.0, 0.75837, 0.68439, 0.61756, 0.55892, 0.29093, 0.16256, 0.02135,
          -0.11477, -0.17119, -0.32726, -0.24299, -0.14612, -0.10338, -0.09266,
          -0.08186, 0.0],
    1000: [1.0, 0.65928, 0.57492, 0.51117, 0.46604, 0.33304, 0.18719, 0.05702,
           -0.06080, -0.10648, -0.27805, -0.38289, -0.29730, -0.22220, -0.20196,
           -0.18109, 0.0],
}
GHIA_X = [1.0, 0.9688, 0.9609, 0.9531, 0.9453, 0.9063, 0.8594, 0.8047, 0.5, 0.2344,
          0.2266, 0.1563, 0.0938, 0.0781, 0.0703, 0.0625, 0.0]
GHIA_V = {
    100: [0.0, -0.05906, -0.07391, -0.08864, -0.10313, -0.16914, -0.22445, -0.24533,
          0.05454, 0.17527, 0.17507, 0.16077, 0.12317, 0.10890, 0.10091, 0.09233, 0.0],
}


def _parabolic_min(x, f, j):
    """Refine a discrete minimum with a parabola through three points."""
    j = int(np.clip(j, 1, len(f) - 2))
    c = np.polyfit(x[j - 1:j + 2], f[j - 1:j + 2], 2)
    if c[0] <= 0:
        return x[j], f[j]
    xm = -c[1] / (2 * c[0])
    return float(xm), float(np.polyval(c, xm))


STEP_BUDGET = 6e8      # time steps x cells one Navier-Stokes solve may cost


def _affordable(steps: float, cells: int, re: float):
    """Explicit time stepping gets very expensive as Re grows; refuse runs
    that would take minutes instead of silently hanging."""
    if steps * max(cells, 2000) > STEP_BUDGET:
        raise RuntimeError(
            f"this explicit solver would need ~{steps:.2g} time steps on {cells} cells "
            f"to reach steady state at Re = {re:.4g}; lower the resolution or the "
            "Reynolds number")


def _cfl_pre_check(cfl_number) -> ck.Check:
    return ck.truth(
        "time-step (CFL) condition: requested step", ck.MATH, cfl_number <= 1.0,
        f"cfl_number = {cfl_number:g} of the stability limit of the explicit scheme"
        + ("" if cfl_number <= 1.0 else
           " - above 1 the time stepping is unstable and the solution blows up"))


@register
class LidDrivenCavity(Simulation):
    name = "lid_driven_cavity"
    title = "Lid-driven cavity (2-D incompressible Navier-Stokes)"
    domain = "fluids"
    description = """Recirculating flow in a square box whose top wall slides
    sideways: the classic test of a Navier-Stokes solver.  Projection method on
    a staggered grid marched to steady state, compared with the benchmark
    tables of Ghia, Ghia & Shin (1982) at Re = 100, 400 and 1000."""
    params = [
        Param("cavity_size", "0.1 m", "m", "side length of the square cavity", lo=0.001, hi=1),
        Param("lid_velocity", "0.1 m/s", "m/s", "speed of the sliding top wall", lo=0.001, hi=10),
        Param("density", "1000 kg/m^3", "kg/m^3", "fluid density", lo=0.5, hi=14000),
        Param("viscosity", "0.1 Pa*s", "Pa*s",
              "dynamic viscosity (default gives Re = 100)", lo=1e-5, hi=10),
        Param("cfl_number", 0.8, "", "time step as a fraction of the stability limit "
              "(must be <= 1)", lo=0.1, hi=1.5),
    ]
    default_resolution = 64
    min_resolution = 6
    primary = ("u_min_centerline", "stream_function_min")
    convergence_tol = 0.02

    def validate(self):
        _positive(self, "cavity_size", "lid_velocity", "density", "viscosity", "cfl_number")
        p = self.p
        self.nu = p.viscosity / p.density
        self.Re = p.lid_velocity * p.cavity_size / self.nu

    def equations(self):
        u, L = self.q("lid_velocity"), self.q("cavity_size")
        return {"Navier-Stokes momentum": {
            "inertia rho*U^2/L": self.q("density") * u ** 2 / L,
            "viscous mu*U/L^2": self.q("viscosity") * u / L ** 2,
            "pressure gradient p/L": Q(1, "Pa") / L}}

    def dimensionless(self):
        return {"Reynolds number rho*U*L/mu": self.q("density") * self.q("lid_velocity")
                * self.q("cavity_size") / self.q("viscosity")}

    def well_posed(self):
        return [ck.truth("boundary conditions complete", ck.MATH, True,
                         "velocity prescribed on all four walls with zero net inflow; "
                         "pressure defined up to a constant"),
                _cfl_pre_check(self.p.cfl_number)] + (
            [self._regime()] if self.Re > 8000 else [])

    def _regime(self) -> ck.Check:
        if self.Re <= 2000:
            return ck.truth("Reynolds regime", ck.FLUID, True,
                            f"Re = {self.Re:.4g}: steady laminar recirculation expected")
        if self.Re <= 8000:
            return ck.Check("Reynolds regime", ck.FLUID, ck.WARN,
                            f"Re = {self.Re:.4g}: still steady, but thin wall layers need a "
                            "much finer grid than the default - trust the grid study")
        return ck.Check("Reynolds regime", ck.FLUID, ck.FAIL,
                        f"Re = {self.Re:.4g} is beyond the onset of unsteadiness "
                        "(~8000): no steady laminar 2-D answer exists")

    def solve(self, n):
        p = self.p
        n = _even(n)
        L, U = p.cavity_size, p.lid_velocity
        flow = IncompressibleFlow2D(n, n, L, L, self.nu, lid=U)
        dt = p.cfl_number * flow.dt_limit(U)
        t_max = max(60.0, 0.4 * self.Re) * L / U
        _affordable(t_max / dt, n * n, self.Re)
        flow.run(dt, t_max, 1e-6, U ** 2 / L)
        cfl = (dt / flow.dt_limit(max(flow.max_speed(), U)) if not flow.blown_up
               else p.cfl_number, 1.0,
               "dt / stable dt (viscous, cell-Reynolds and Courant limits)")
        if flow.blown_up:
            nan = float("nan")
            return Solution({k: (nan, "") for k in self.primary}, [],
                            math.inf, "time stepping diverged", 1e-5, cfl,
                            {"flow": flow})
        uc = flow.u[n // 2, :] / U                      # u(y) on the vertical centreline
        vc = flow.v[:, n // 2] / U                      # v(x) on the horizontal centreline
        yc, xc = flow.y / L, flow.x / L
        y_min, u_min = _parabolic_min(yc, uc, np.argmin(uc))
        _, v_min = _parabolic_min(xc, vc, np.argmin(vc))
        _, v_max = _parabolic_min(xc, -vc, np.argmax(vc))
        psi = flow.stream_function() / (U * L)
        i, j = np.unravel_index(np.argmin(psi), psi.shape)
        nodes = np.arange(n + 1) / n
        x_c, psi_x = _parabolic_min(nodes, psi[:, j], i)
        y_c, psi_y = _parabolic_min(nodes, psi[i, :], j)
        psi_min = psi_x + psi_y - psi[i, j]
        ucell, vcell = flow.centre_velocity()
        qoi = {"reynolds_number": (self.Re, ""),
               "u_min_centerline": (u_min, "x lid speed"),
               "y_of_u_min": (y_min, "x L"),
               "v_max_centerline": (-v_max, "x lid speed"),
               "v_min_centerline": (v_min, "x lid speed"),
               "stream_function_min": (psi_min, "x U*L"),
               "vortex_center_x": (x_c, "x L"),
               "vortex_center_y": (y_c, "x L")}
        fields = [Field("flow speed", flow.x, np.hypot(ucell, vcell), flow.y, "m/s"),
                  Field("u on the vertical centreline", yc, uc, unit="x lid speed",
                        xlabel="y / L", ylabel="u / U")]
        return Solution(qoi, fields, flow.steady_residual,
                        "steady state: max |du/dt| in units of U^2/L", 1e-5, cfl,
                        {"flow": flow, "uc": uc, "vc": vc, "yc": yc, "xc": xc})

    def known_solution(self, sol):
        d = sol.data
        if "uc" not in d:
            return [ck.truth("known solution: Ghia et al. (1982)", ck.UNIVERSAL, False,
                             "no solution to compare (time stepping diverged)")]
        key = next((r for r in GHIA_U if abs(self.Re - r) <= 0.01 * r), None)
        if key is None:
            return [ck.skip("known solution: Ghia et al. (1982)", ck.UNIVERSAL,
                            f"benchmark tables exist for Re = 100, 400, 1000; this run "
                            f"has Re = {self.Re:.4g}")]
        yy = np.concatenate([[0.0], d["yc"], [1.0]])
        uu = np.concatenate([[0.0], d["uc"], [1.0]])
        err = float(np.max(np.abs(np.interp(GHIA_Y, yy, uu) - np.array(GHIA_U[key]))))
        out = [ck.small(f"known solution: Ghia et al. (1982), u on vertical centreline, Re={key}",
                        ck.UNIVERSAL, err, 0.01, "largest deviation from the 17 tabulated "
                        "points, as a fraction of lid speed", warn_factor=2.5)]
        if key in GHIA_V:
            xx = np.concatenate([[0.0], d["xc"], [1.0]])
            vv = np.concatenate([[0.0], d["vc"], [0.0]])
            err = float(np.max(np.abs(np.interp(GHIA_X, xx, vv) - np.array(GHIA_V[key]))))
            out.append(ck.small(
                f"known solution: Ghia et al. (1982), v on horizontal centreline, Re={key}",
                ck.UNIVERSAL, err, 0.01, "largest deviation from the 17 tabulated points, "
                "as a fraction of lid speed", warn_factor=2.5))
        return out

    def domain_checks(self, sol):
        p = self.p
        flow = sol.data["flow"]
        L, U = p.cavity_size, p.lid_velocity
        if flow.blown_up:
            return [ck.truth("mass conservation", ck.FLUID, False,
                             "velocity field is not finite")]
        div = float(np.max(np.abs(flow.divergence()))) * L / U
        through = max(float(np.max(np.abs(np.sum(flow.u, axis=1)))) * flow.dy,
                      float(np.max(np.abs(np.sum(flow.v, axis=0)))) * flow.dx) / (U * L)
        out = [ck.small("mass conservation: divergence of velocity", ck.FLUID, div, 1e-9,
                        "max |div u| in every cell, in units of U/L"),
               ck.small("mass conservation: net flow through any cross-section", ck.FLUID,
                        through, 1e-9, "closed box, so every cut must carry zero net "
                        "flow (units of U*L)")]
        out.append(self._regime())
        return out


# ---------------------------------------------------------------------------
# plane channel
# ---------------------------------------------------------------------------
@register
class ChannelFlow(Simulation):
    name = "channel_flow"
    title = "Developing flow between parallel plates (2-D Navier-Stokes)"
    domain = "fluids"
    description = """Fluid enters a slot between two plates with a flat
    velocity profile and develops into the parabolic Poiseuille profile.
    Solved with the full 2-D Navier-Stokes equations; reports entrance length,
    developed pressure gradient and pressure drop (cooling slots, microfluidic
    channels, air gaps)."""
    params = [
        Param("height", "10 mm", "m", "gap between the plates", lo=0.0001, hi=0.5),
        Param("length", "60 mm", "m", "channel length", lo=0.001, hi=5),
        Param("mean_velocity", "2 mm/s", "m/s", "inlet (mean) velocity", lo=1e-4, hi=10),
        Param("density", "998 kg/m^3", "kg/m^3", "fluid density", lo=0.5, hi=14000),
        Param("viscosity", "1.0e-3 Pa*s", "Pa*s", "dynamic viscosity", lo=1e-6, hi=10),
        Param("cfl_number", 0.8, "", "time step as a fraction of the stability limit "
              "(must be <= 1)", lo=0.1, hi=1.5),
    ]
    default_resolution = 32
    min_resolution = 4
    primary = ("max_velocity", "pressure_gradient")
    resolution_doc = "grid cells across the gap (cells along the channel follow)"

    def validate(self):
        _positive(self, "height", "length", "mean_velocity", "density", "viscosity",
                  "cfl_number")
        p = self.p
        if p.length < 2 * p.height:
            raise ParamError("length must be at least twice the height")
        if p.length > 40 * p.height:
            raise ParamError("length above 40 heights: use pipe_flow-style developed "
                             "formulas or shorten the channel")
        self.nu = p.viscosity / p.density
        self.Re = p.mean_velocity * 2 * p.height / self.nu       # hydraulic diameter 2H
        # Chen (1973) correlation for the hydrodynamic entrance length
        self.entry = 2 * p.height * (0.315 / (1 + 0.0175 * self.Re) + 0.011 * self.Re)

    def equations(self):
        u, h = self.q("mean_velocity"), self.q("height")
        return {"Navier-Stokes momentum": {
            "inertia rho*U^2/H": self.q("density") * u ** 2 / h,
            "viscous mu*U/H^2": self.q("viscosity") * u / h ** 2,
            "pressure gradient dp/L": Q(1, "Pa") / self.q("length")}}

    def dimensionless(self):
        return {"Reynolds number rho*U*2H/mu": self.q("density") * self.q("mean_velocity")
                * 2 * self.q("height") / self.q("viscosity"),
                "aspect ratio L/H": self.q("length") / self.q("height")}

    def well_posed(self):
        return [ck.truth("boundary conditions complete", ck.MATH, True,
                         "inflow velocity, no-slip walls and a pressure outlet: one "
                         "condition per boundary, pressure level fixed at the outlet"),
                _cfl_pre_check(self.p.cfl_number)] + (
            [] if self.Re < RE_LAMINAR else [self._regime()])

    def _regime(self) -> ck.Check:
        if self.Re < RE_LAMINAR:
            return ck.truth("Reynolds regime", ck.FLUID, True,
                            f"Re (hydraulic diameter 2H) = {self.Re:.4g} < "
                            f"{RE_LAMINAR:.0f}: laminar, as this solver assumes")
        return ck.truth("Reynolds regime", ck.FLUID, False,
                        f"Re (hydraulic diameter 2H) = {self.Re:.4g} >= {RE_LAMINAR:.0f}: "
                        "the real flow would be transitional or turbulent, so a laminar "
                        "Navier-Stokes solution is not physical")

    def solve(self, n):
        p = self.p
        ny = _even(n)
        nx = max(8, int(round(ny * p.length / p.height / 2)))
        H, L, U = p.height, p.length, p.mean_velocity
        flow = IncompressibleFlow2D(nx, ny, L, H, self.nu, inflow=U)
        dt = p.cfl_number * flow.dt_limit(1.5 * U)
        t_max = 12 * L / U + min(4 * H ** 2 / self.nu, 30 * L / U)
        _affordable(t_max / dt, nx * ny, self.Re)
        flow.run(dt, t_max, 1e-6, U ** 2 / H)
        cfl = (dt / flow.dt_limit(max(flow.max_speed(), 1.5 * U)) if not flow.blown_up
               else p.cfl_number, 1.0,
               "dt / stable dt (viscous, cell-Reynolds and Courant limits)")
        if flow.blown_up:
            nan = float("nan")
            return Solution({k: (nan, "") for k in self.primary}, [], math.inf,
                            "time stepping diverged", 1e-5, cfl, {"flow": flow})
        xf = np.arange(nx + 1) * flow.dx
        mid = slice(ny // 2 - 2, ny // 2 + 2)
        centre = np.array([np.polyval(np.polyfit(flow.y[mid], flow.u[i, mid], 2), H / 2)
                           for i in range(nx + 1)])
        i_dev = int(round(0.75 * nx))
        u_max = float(centre[i_dev])
        reached = np.nonzero(centre >= 0.99 * u_max)[0]
        i0 = int(reached[0]) if len(reached) else nx
        if 0 < i0 <= nx:                                  # linear interpolation in x
            c0, c1 = centre[i0 - 1], centre[i0]
            entry = xf[i0 - 1] + (0.99 * u_max - c0) / (c1 - c0) * flow.dx
        else:
            entry = xf[i0]
        pbar = p.density * flow.p.mean(axis=1)            # section-mean pressure
        sel = (flow.x >= max(0.55 * L, min(entry * 1.2, 0.7 * L))) & (flow.x <= 0.9 * L)
        if sel.sum() < 3:
            sel = flow.x >= 0.5 * L
        # developed = the centreline has stopped accelerating well before the outlet
        drift = abs(centre[int(round(0.9 * nx))] - centre[int(round(0.6 * nx))]) / u_max
        developed = bool(entry <= 0.6 * L and drift <= 0.01)
        slope = float(np.polyfit(flow.x[sel], pbar[sel], 1)[0])
        p_in = 1.5 * pbar[0] - 0.5 * pbar[1]
        prof = flow.u[i_dev, :]
        tau_w = p.viscosity * 0.5 * (prof[0] + prof[-1]) / (flow.dy / 2)
        qoi = {"reynolds_number": (self.Re, ""),
               "max_velocity": (u_max, "m/s"),
               "velocity_ratio": (u_max / U, ""),
               "pressure_gradient": (-slope, "Pa/m"),
               "pressure_drop": (p_in, "Pa"),
               "entrance_length": (float(entry) if developed else self.entry, "m"),
               "wall_shear_stress": (tau_w, "Pa")}
        ucell, vcell = flow.centre_velocity()
        fields = [Field("flow speed", flow.x * 1e3, np.hypot(ucell, vcell), flow.y * 1e3,
                        "m/s", "x [mm]", "y [mm]"),
                  Field("developed velocity profile", flow.y * 1e3, prof, unit="m/s",
                        xlabel="y [mm]", ylabel="u [m/s]"),
                  Field("pressure along the channel", flow.x * 1e3, pbar, unit="Pa",
                        xlabel="x [mm]", ylabel="p [Pa]")]
        return Solution(qoi, fields, flow.steady_residual,
                        "steady state: max |du/dt| in units of U^2/H", 1e-5, cfl,
                        {"flow": flow, "profile": prof, "entry": float(entry),
                         "developed": developed})

    def _developed(self, sol):
        return sol.data.get("developed", False)

    def known_solution(self, sol):
        p = self.p
        if "profile" not in sol.data:
            return [ck.truth("known solution: plane Poiseuille profile", ck.UNIVERSAL,
                             False, "no solution to compare (time stepping diverged)")]
        if not self._developed(sol):
            return [ck.skip("known solution: plane Poiseuille profile", ck.UNIVERSAL,
                            "channel is too short for the flow to become fully developed")]
        y = sol.data["flow"].y
        exact = 6 * p.mean_velocity * y * (p.height - y) / p.height ** 2
        err = float(np.max(np.abs(sol.data["profile"] - exact))) / p.mean_velocity
        return [ck.small("known solution: plane Poiseuille profile", ck.UNIVERSAL, err, 0.01,
                         "largest deviation of the developed profile from "
                         "6 U y (H - y) / H^2, as a fraction of U")]

    def domain_checks(self, sol):
        p = self.p
        flow = sol.data["flow"]
        H, U = p.height, p.mean_velocity
        if flow.blown_up:
            return [ck.truth("mass in = mass out", ck.FLUID, False,
                             "velocity field is not finite")]
        qsec = np.sum(flow.u, axis=1) * flow.dy
        out = [ck.small("mass in = mass out", ck.FLUID,
                        float(np.max(np.abs(qsec - qsec[0]))) / (U * H), 1e-9,
                        f"flow rate in {qsec[0]:.6g} m^2/s, out {qsec[-1]:.6g} m^2/s; "
                        "largest relative deviation over all cross-sections"),
               ck.small("mass conservation: divergence of velocity", ck.FLUID,
                        float(np.max(np.abs(flow.divergence()))) * H / U, 1e-9,
                        "max |div u| in every cell, in units of U/H")]
        out.append(self._regime())
        dev = self._developed(sol)
        out.append(ck.truth(
            "entrance length", ck.FLUID, dev,
            (f"flow is 99% developed after {sol.data['entry'] * 1e3:.3g} mm of "
             f"{p.length * 1e3:.3g} mm (correlation estimate {self.entry * 1e3:.3g} mm)")
            if dev else
            (f"the centreline is still accelerating near the outlet of the "
             f"{p.length * 1e3:.3g} mm channel (entrance length by correlation "
             f"~{self.entry * 1e3:.3g} mm), so there is no developed stretch long "
             "enough to check against theory"),
            warn=True))
        names = ("developed profile: u_max = 1.5 U", "pressure drop vs plane Poiseuille",
                 "wall force balance (dp/dx * H = 2 tau_w)")
        if not dev:
            return out + [ck.skip(nm, ck.FLUID, "flow never becomes fully developed "
                                  "in this channel") for nm in names]
        grad = sol.qoi["pressure_gradient"][0]
        out.append(ck.close(names[0], ck.FLUID, sol.qoi["max_velocity"][0], 1.5 * U, 0.01,
                            unit="m/s"))
        out.append(ck.close(names[1], ck.FLUID, grad, 12 * p.viscosity * U / H ** 2, 0.015,
                            detail="developed pressure gradient vs 12 mu U / H^2",
                            unit="Pa/m"))
        out.append(ck.close(names[2], ck.FLUID, grad * H,
                            2 * sol.qoi["wall_shear_stress"][0], 0.01,
                            detail="pressure force on a slice vs shear on both walls",
                            unit="Pa"))
        return out

    def symmetry_checks(self, sol):
        flow = sol.data["flow"]
        if flow.blown_up:
            return []
        return [ck.symmetry("symmetry about the channel centreline", flow.u,
                            flow.u[:, ::-1], 1e-8,
                            "setup is mirror-symmetric, so is the velocity field")]


# ---------------------------------------------------------------------------
# cooling channel (Graetz problem)
# ---------------------------------------------------------------------------
@register
class CoolingChannel(Simulation):
    name = "cooling_channel"
    title = "Cooling channel: laminar convection between heated plates (Graetz problem)"
    domain = "fluids"
    description = """Coolant flowing through a thin slot with hot walls (cold
    plates, micro-channel heat sinks, battery cooling gaps).  The fully
    developed Poiseuille velocity profile carries heat downstream while it
    conducts in from the walls: rho cp u dT/dx = div(k grad T).  Reports outlet
    temperature, heat removed and the Nusselt number, which must approach
    7.541 (isothermal walls) or 8.235 (constant wall heat flux)."""
    params = [
        Param("gap", "1 mm", "m", "distance between the plates", lo=0.0001, hi=0.02),
        Param("length", "30 mm", "m", "heated length", lo=0.001, hi=1),
        Param("channel_width", "10 mm", "m", "channel depth (for total heat)", lo=0.001, hi=1),
        Param("mean_velocity", "0.02 m/s", "m/s", "mean coolant velocity", lo=1e-4, hi=5),
        Param("inlet_temperature", "20 degC", "K", "coolant inlet temperature"),
        Param("wall_condition", "temperature", None, "what is fixed at the walls",
              choices=["temperature", "flux"]),
        Param("wall_temperature", "60 degC", "K", "wall temperature (if fixed)"),
        Param("wall_heat_flux", "5000 W/m^2", "W/m^2", "heat flux into the coolant at "
              "each wall (if fixed)", lo=-1e5, hi=1e6),
        Param("conductivity", "0.6 W/(m*K)", "W/(m*K)", "coolant conductivity", lo=0.01, hi=100),
        Param("density", "998 kg/m^3", "kg/m^3", "coolant density", lo=0.5, hi=14000),
        Param("specific_heat", "4182 J/(kg*K)", "J/(kg*K)", "coolant specific heat",
              lo=100, hi=15000),
        Param("viscosity", "1.0e-3 Pa*s", "Pa*s", "dynamic viscosity (for the Reynolds "
              "number only)", lo=1e-6, hi=10),
        Param("scheme", "upwind", None, "convection scheme: upwind is always bounded, "
              "central is 2nd order but needs cell Peclet < 2", choices=["upwind", "central"]),
    ]
    default_resolution = 48
    min_resolution = 4
    primary = ("outlet_bulk_temperature", "nusselt_developed")
    order_hint = 1
    resolution_doc = "grid cells across the gap (twice as many along the channel)"

    def validate(self):
        _positive(self, "gap", "length", "channel_width", "mean_velocity", "conductivity",
                  "density", "specific_heat", "viscosity")
        p = self.p
        self.dh = 2 * p.gap
        self.Re = p.density * p.mean_velocity * self.dh / p.viscosity
        self.alpha = p.conductivity / (p.density * p.specific_heat)
        self.Pe = p.mean_velocity * self.dh / self.alpha
        self.isothermal = p.wall_condition == "temperature"
        # dimensionless axial position of the Nusselt measurement zone (0.8 L)
        self.xstar = 0.8 * p.length / (self.dh * self.Pe)

    def _problem(self, n):
        p = self.p
        ny, nx = max(4, n), 2 * max(4, n)
        g = Grid(nx, p.length, ny, p.gap)

        def flow(y):        # exact integral of the Poiseuille profile
            return 6 * p.mean_velocity * (y ** 2 / (2 * p.gap) - y ** 3 / (3 * p.gap ** 2))

        u = (flow(g.yf[1:]) - flow(g.yf[:-1])) / g.dys      # cell means: mass-exact
        wall = (dirichlet(p.wall_temperature) if self.isothermal
                else neumann(p.wall_heat_flux))
        prob = TransportProblem(
            g, p.conductivity, ux=p.density * p.specific_heat * u[None, :],
            bcs={"west": dirichlet(p.inlet_temperature), "east": neumann(0.0),
                 "south": wall, "north": wall}, scheme=p.scheme)
        return prob, u

    def equations(self):
        dT = Q(1, "K")
        return {"energy equation": {
            "convection rho*cp*U*dT/L": self.q("density") * self.q("specific_heat")
            * self.q("mean_velocity") * dT / self.q("length"),
            "conduction k*dT/H^2": self.q("conductivity") * dT / self.q("gap") ** 2,
            "wall flux q/H": Q(1, "W/m^2") / self.q("gap")}}

    def dimensionless(self):
        dh = 2 * self.q("gap")
        alpha = self.q("conductivity") / (self.q("density") * self.q("specific_heat"))
        return {"Reynolds number rho*U*Dh/mu": self.q("density") * self.q("mean_velocity")
                * dh / self.q("viscosity"),
                "Peclet number U*Dh/alpha": self.q("mean_velocity") * dh / alpha,
                "Graetz position x/(Dh*Pe) at 0.8 L": 0.8 * self.q("length") * alpha
                / (dh ** 2 * self.q("mean_velocity"))}

    def well_posed(self):
        out = self._problem(8)[0].well_posed(what="temperature")
        out.append(ck.truth(
            "model validity: laminar developed flow", ck.MATH, self.Re < RE_LAMINAR,
            f"Re = {self.Re:.4g} (the parabolic velocity profile needs Re < "
            f"{RE_LAMINAR:.0f})"))
        return out

    def solve(self, n):
        p = self.p
        prob, u = self._problem(n)
        ts = prob.solve_steady()
        g, T = ts.grid, ts.phi
        k, t_in = p.conductivity, p.inlet_temperature
        bulk = (T * u[None, :]).sum(axis=1) / u.sum()
        half = g.dys[0] / 2
        if self.isothermal:
            q_wall = 0.5 * k * ((p.wall_temperature - T[:, 0])
                                + (p.wall_temperature - T[:, -1])) / half
            t_wall = np.full(g.nx, p.wall_temperature)
        else:
            q_wall = np.full(g.nx, p.wall_heat_flux)
            t_wall = 0.5 * (T[:, 0] + T[:, -1]) + p.wall_heat_flux * half / k
        with np.errstate(divide="ignore", invalid="ignore"):
            nu_x = q_wall / (t_wall - bulk) * self.dh / k
        zone = (g.x > 0.7 * p.length) & (g.x < 0.9 * p.length)
        nu_dev = float(np.mean(nu_x[zone]))
        cap = p.density * p.specific_heat * u * g.dys      # capacity rate per cell, W/(m K)
        inflow = ts.boundary_inflow()
        wall_heat = (inflow["south"] + inflow["north"]) * p.channel_width
        convected = float(np.sum(cap * (T[-1, :] - t_in))) * p.channel_width
        # heat conducted in through the inlet plane (against the flow)
        inlet_cond = k * g.dys / (g.dxs[0] / 2) * (t_in - T[0, :])
        qoi = {"outlet_bulk_temperature": (to_c(bulk[-1]), "degC"),
               "heat_removed": (wall_heat, "W"),
               "nusselt_developed": (nu_dev, ""),
               "heat_transfer_coefficient": (nu_dev * k / self.dh, "W/(m^2*K)"),
               "max_wall_temperature": (to_c(np.max(t_wall)), "degC"),
               "reynolds_number": (self.Re, ""),
               "peclet_number": (self.Pe, ""),
               "thermal_entry_length": (0.008 * self.Pe * self.dh if self.isothermal
                                        else 0.0115 * self.Pe * self.dh, "m")}
        fields = [Field("coolant temperature", g.x * 1e3, T - 273.15, g.y * 1e3, "degC",
                        "x [mm]", "y [mm]"),
                  Field("along the channel", g.x * 1e3,
                        {"bulk temperature [degC]": bulk - 273.15,
                         "wall temperature [degC]": t_wall - 273.15},
                        unit="degC", xlabel="x [mm]", ylabel="T [degC]"),
                  Field("local Nusselt number", g.x * 1e3, nu_x, unit="",
                        xlabel="x [mm]", ylabel="Nu")]
        cell_pe = float(np.max(u)) * g.dx / self.alpha
        return Solution(qoi, fields, ts.residual, data={
            "ts": ts, "wall_heat": wall_heat, "convected": convected,
            "cell_pe": cell_pe, "cap": cap, "inlet_cond": inlet_cond,
            "q_wall": q_wall, "t_wall": t_wall})

    def known_solution(self, sol):
        p = self.p
        exact = 7.541 if self.isothermal else 8.235
        nu = sol.qoi["nusselt_developed"][0]
        name = "known solution: fully developed Nusselt number"
        out = []
        if not math.isfinite(nu) or (self.isothermal and self.xstar > 0.2):
            out.append(ck.skip(name, ck.UNIVERSAL, "coolant reaches the wall temperature "
                               "before the measurement zone, so h = q/dT is 0/0"))
        elif self.xstar >= (0.03 if self.isothermal else 0.05):
            out.append(ck.close(name, ck.UNIVERSAL, nu, exact, 0.015,
                                detail=f"exact value for parallel plates with constant wall "
                                f"{p.wall_condition}"))
        else:
            out.append(ck.truth(
                name + " (lower bound)", ck.UNIVERSAL, nu >= exact * 0.995,
                f"channel is shorter than the thermal entry length (x* = {self.xstar:.3g}), "
                f"so Nu = {nu:.4g} must still be above the developed value {exact}"))
        if not self.isothermal:
            t_out = p.inlet_temperature + 2 * p.wall_heat_flux * p.length / (
                p.density * p.specific_heat * p.mean_velocity * p.gap)
            out.append(ck.close("known solution: outlet bulk temperature", ck.UNIVERSAL,
                                sol.qoi["outlet_bulk_temperature"][0], to_c(t_out), 0, 0.02
                                + 0.002 * abs(t_out - p.inlet_temperature),
                                detail="exact energy balance T_in + 2 q L / (rho cp U H)",
                                unit="degC"))
        out.append(operator_benchmark())
        return out

    def domain_checks(self, sol):
        p = self.p
        ts = sol.data["ts"]
        t_in = p.inlet_temperature
        if self.isothermal:
            lo, hi = sorted((t_in, p.wall_temperature))
            why = "no sources: bounded by inlet and wall temperatures"
        elif p.wall_heat_flux >= 0:
            lo, hi, why = t_in, None, "heating only: nothing colder than the inlet"
        else:
            lo, hi, why = None, t_in, "cooling only: nothing hotter than the inlet"
        d = sol.data
        out = [ts.conservation_check("energy balance (in = out + stored)", ck.HEAT, " W/m"),
               bounds_check(ts, lo, hi, why)]
        wall, conv = d["wall_heat"], d["convected"]
        axial = float(np.sum(d["inlet_cond"])) * p.channel_width
        scale = max(abs(wall), abs(conv), 1e-300)
        out.append(ck.small(
            "energy balance: m_dot*cp*dT_bulk = wall heat", ck.HEAT,
            (conv - wall - axial) / scale, 1e-8,
            f"enthalpy rise of the coolant {conv:.6g} W = heat through the walls "
            f"{wall:.6g} W + conduction through the inlet plane {axial:.3g} W "
            f"({100 * axial / scale:.2g}% - axial conduction is "
            f"{'negligible' if abs(axial) < 0.02 * scale else 'NOT negligible'}); "
            "relative mismatch"))
        out.append(self._entropy_check(sol))
        cell_pe = sol.data["cell_pe"]
        if p.scheme == "upwind":
            out.append(ck.truth("cell Peclet number vs scheme", ck.MATH, True,
                                f"cell Peclet {cell_pe:.3g}; upwind differencing is bounded "
                                "for any value (first-order accurate)"))
        else:
            out.append(ck.truth("cell Peclet number vs scheme", ck.MATH, cell_pe <= 2.0,
                                f"cell Peclet {cell_pe:.3g}; central differencing is only "
                                "bounded (wiggle-free) below 2 - refine the grid or use upwind"))
        return out

    def _entropy_check(self, sol) -> ck.Check:
        """Second law for an open system.  Flowing coolant carries entropy
        m_dot*cp*ln(T) (not enthalpy/T), heat crosses a boundary at that
        boundary's temperature, and what leaves minus what enters is the
        entropy generated inside - which must not be negative."""
        p, d = self.p, sol.data
        T = d["ts"].phi
        g = d["ts"].grid
        name = "entropy generation >= 0"
        if np.any(T <= 0):
            return ck.truth(name, ck.HEAT, False, "non-positive absolute temperature")
        t_in = p.inlet_temperature
        flow_terms = d["cap"] * np.log(T[-1, :] / t_in)        # carried out minus in
        inlet_terms = -d["inlet_cond"] / t_in                  # conducted in at T_in
        # wall heat enters at the wall temperature (both walls, per cell)
        half = g.dys[0] / 2
        if self.isothermal:
            q_s = p.conductivity * (p.wall_temperature - T[:, 0]) / half
            q_n = p.conductivity * (p.wall_temperature - T[:, -1]) / half
            t_s = t_n = p.wall_temperature
        else:
            q_s = q_n = p.wall_heat_flux
            t_s = T[:, 0] + p.wall_heat_flux * half / p.conductivity
            t_n = T[:, -1] + p.wall_heat_flux * half / p.conductivity
        wall_terms = -(q_s / t_s + q_n / t_n) * g.dxs
        parts = np.concatenate([np.ravel(flow_terms), np.ravel(inlet_terms),
                                np.ravel(wall_terms)]) * p.channel_width
        s_gen, scale = float(parts.sum()), float(np.abs(parts).sum()) + 1e-300
        ok = s_gen >= -1e-9 * scale
        carried = float(np.sum(flow_terms)) * p.channel_width
        return ck.Check(
            name, ck.HEAT, ck.PASS if ok else ck.FAIL,
            f"coolant carries away {carried:.4g} W/K of entropy, heat brings in "
            f"{carried - s_gen:.4g} W/K; production {s_gen:.4g} W/K "
            f"({'heat only flows from hot to cold' if ok else 'second law violated'})",
            s_gen, 0.0)

    def symmetry_checks(self, sol):
        return sol.data["ts"].symmetry_checks(1e-7)


# ---------------------------------------------------------------------------
# projectile with drag
# ---------------------------------------------------------------------------
def sphere_cd(re):
    """Standard drag curve of a smooth sphere (Clift & Gauvin 1970, Re < 3e5)."""
    re = max(float(re), 1e-12)
    return 24.0 / re * (1 + 0.15 * re ** 0.687) + 0.42 / (1 + 4.25e4 * re ** -1.16)


@register
class ProjectileDrag(Simulation):
    name = "projectile_drag"
    title = "Thrown or dropped ball with air drag"
    domain = "education"
    description = """Trajectory of a ball under gravity and quadratic air
    drag, integrated with 4th-order Runge-Kutta.  The drag coefficient is
    either constant or follows the standard sphere curve Cd(Re).  Reports
    range, flight time, apex, impact speed and terminal velocity, and compares
    with the vacuum trajectory.  Set initial_speed to 0 for a pure drop."""
    params = [
        Param("mass", "0.145 kg", "kg", "ball mass (baseball 0.145, football 0.43)",
              lo=0.001, hi=10),
        Param("diameter", "74 mm", "m", "ball diameter", lo=0.005, hi=0.5),
        Param("initial_speed", "30 m/s", "m/s", "launch speed (0 = dropped)", lo=0, hi=80),
        Param("launch_angle", "35 deg", "deg", "angle above horizontal", lo=-90, hi=90),
        Param("initial_height", "1 m", "m", "launch height above the ground", lo=0, hi=500),
        Param("drag_model", "constant", None, "drag law",
              choices=["constant", "sphere_curve", "none"]),
        Param("drag_coefficient", 0.47, "", "Cd for the constant model (smooth sphere 0.47)",
              lo=0.05, hi=1.5),
        Param("air_density", "1.225 kg/m^3", "kg/m^3", "fluid density", lo=0.01, hi=1500),
        Param("air_viscosity", "1.81e-5 Pa*s", "Pa*s", "fluid dynamic viscosity",
              lo=1e-6, hi=1),
        Param("gravity", "9.81 m/s^2", "m/s^2", "gravitational acceleration", lo=0.5, hi=30),
    ]
    default_resolution = 200
    min_resolution = 8
    primary = ("impact_speed", "flight_time")
    order_hint = 4
    convergence_tol = 0.005
    resolution_doc = "time steps over the (vacuum) flight time"

    def validate(self):
        _positive(self, "mass", "diameter", "air_density", "air_viscosity", "gravity")
        p = self.p
        if p.initial_speed < 0 or p.initial_height < 0:
            raise ParamError("initial_speed and initial_height cannot be negative")
        ang = math.radians(p.launch_angle)
        self.vx0, self.vy0 = p.initial_speed * math.cos(ang), p.initial_speed * math.sin(ang)
        if abs(self.vx0) < 1e-12 * max(p.initial_speed, 1.0):
            self.vx0 = 0.0
        if p.initial_height == 0 and self.vy0 <= 0:
            raise ParamError("the ball starts on the ground and is not thrown upward: "
                             "raise initial_height or use a positive launch_angle")
        self.area = math.pi * p.diameter ** 2 / 4
        g = p.gravity
        self.t_vac = (self.vy0 + math.sqrt(self.vy0 ** 2 + 2 * g * p.initial_height)) / g

    # drag force divided by speed [N s/m]
    def _k(self, speed, model=None, cd=None):
        p = self.p
        model = model or p.drag_model
        if model == "none":
            return 0.0
        if model == "constant":
            c = p.drag_coefficient if cd is None else cd
        else:
            c = sphere_cd(p.air_density * speed * p.diameter / p.air_viscosity)
        return 0.5 * p.air_density * self.area * c * speed

    def _rhs(self, s, model=None, cd=None):
        speed = math.hypot(s[2], s[3])
        k = self._k(speed, model, cd)
        m = self.p.mass
        return np.array([s[2], s[3], -k * s[2] / m, -self.p.gravity - k * s[3] / m,
                         k * speed * speed])

    def _rk4(self, s, dt, model=None, cd=None):
        k1 = self._rhs(s, model, cd)
        k2 = self._rhs(s + 0.5 * dt * k1, model, cd)
        k3 = self._rhs(s + 0.5 * dt * k2, model, cd)
        k4 = self._rhs(s + dt * k3, model, cd)
        return s + dt / 6 * (k1 + 2 * k2 + 2 * k3 + k4)

    def _fly(self, n, state=None, model=None, cd=None):
        """Integrate to touchdown.  The last (partial) step is solved so that
        y = 0 exactly, which keeps every output smooth in the step size."""
        p = self.p
        s = np.array([0.0, p.initial_height, self.vx0, self.vy0, 0.0]) if state is None \
            else np.array(state, float)
        g = p.gravity
        t_ref = (s[3] + math.sqrt(s[3] ** 2 + 2 * g * s[1])) / g
        dt = t_ref / n
        t, hist, apex = 0.0, [(0.0, *s)], s[1]
        for _ in range(60 * n):
            new = self._rk4(s, dt, model, cd)
            if s[3] > 0 >= new[3]:                         # passed the apex
                frac = brentq(lambda f: self._rk4(s, f * dt, model, cd)[3], 0.0, 1.0)
                apex = max(apex, self._rk4(s, frac * dt, model, cd)[1])
            if new[1] <= 0.0:
                frac = 0.0 if s[1] <= 0.0 else brentq(
                    lambda f: self._rk4(s, f * dt, model, cd)[1], 0.0, 1.0, xtol=1e-15)
                s = self._rk4(s, frac * dt, model, cd)
                t += frac * dt
                hist.append((t, *s))
                return np.array(hist), apex, dt
            s, t = new, t + dt
            hist.append((t, *s))
        raise RuntimeError("the ball never reached the ground")

    def terminal_velocity(self) -> float:
        p = self.p
        if p.drag_model == "none":
            return math.inf
        w = p.mass * p.gravity
        return brentq(lambda v: self._k(v) * v - w, 1e-9, 1e5)

    def equations(self):
        v = Q(max(self.p.initial_speed, 1.0), "m/s")
        return {"Newton's second law": {
            "weight m*g": self.q("mass") * self.q("gravity"),
            "drag rho*Cd*A*v^2/2": self.q("air_density") * self.q("drag_coefficient")
            * self.q("diameter") ** 2 * v ** 2,
            "inertia m*dv/dt": self.q("mass") * v / (v / self.q("gravity"))}}

    def dimensionless(self):
        v = Q(max(self.p.initial_speed, 1.0), "m/s")
        return {"Reynolds number rho*v*D/mu": self.q("air_density") * v
                * self.q("diameter") / self.q("air_viscosity"),
                "drag-to-weight ratio at launch": self.q("air_density")
                * self.q("drag_coefficient") * self.q("diameter") ** 2 * v ** 2
                / (self.q("mass") * self.q("gravity"))}

    def well_posed(self):
        return [ck.truth("initial conditions complete", ck.MATH, True,
                         "position and velocity given at t = 0; the ball is above the "
                         "ground or moving upward, so a landing time exists")]

    def solve(self, n):
        p = self.p
        hist, apex, dt = self._fly(n)
        t, x, y, vx, vy, work = hist.T
        speed = np.hypot(vx, vy)
        m, g = p.mass, p.gravity
        e0 = 0.5 * m * p.initial_speed ** 2 + m * g * p.initial_height
        e_end = 0.5 * m * speed[-1] ** 2 + m * g * y[-1]
        v_t = self.terminal_velocity()
        re_max = p.air_density * float(speed.max()) * p.diameter / p.air_viscosity
        rate = 2 * max(self._k(float(v)) for v in speed) / m
        qoi = {"range": (x[-1], "m"),
               "flight_time": (t[-1], "s"),
               "max_height": (apex, "m"),
               "impact_speed": (speed[-1], "m/s"),
               **({} if math.isinf(v_t) else {"terminal_velocity": (v_t, "m/s")}),
               "energy_lost_to_drag": (work[-1], "J"),
               "vacuum_range": (self.vx0 * self.t_vac, "m"),
               "max_reynolds_number": (re_max, "")}
        y_vac = p.initial_height + self.vy0 * t - 0.5 * g * t ** 2
        fields = [Field("trajectory", x, y, unit="m", xlabel="distance [m]",
                        ylabel="height [m]"),
                  Field("height vs time", t, {"with drag [m]": y,
                                              "vacuum [m]": np.maximum(y_vac, 0.0)},
                        unit="m", xlabel="time [s]", ylabel="height [m]"),
                  Field("speed vs time", t, speed, unit="m/s", xlabel="time [s]",
                        ylabel="speed [m/s]")]
        cfl = (dt * rate, 2.78, "RK4 step x drag relaxation rate")
        return Solution(qoi, fields, None, "explicit Runge-Kutta integration (no "
                        "algebraic system to converge)", cfl=cfl,
                        data={"e0": e0, "e_end": e_end, "work": float(work[-1]),
                              "re_max": re_max, "apex": apex, "v_t": v_t})

    def _drop_exact(self, h, cd):
        p = self.p
        v_t = math.sqrt(2 * p.mass * p.gravity / (p.air_density * cd * self.area))
        t_land = v_t / p.gravity * math.acosh(math.exp(p.gravity * h / v_t ** 2))
        v_imp = v_t * math.sqrt(1 - math.exp(-2 * p.gravity * h / v_t ** 2))
        return t_land, v_imp

    def known_solution(self, sol):
        p = self.p
        if p.drag_model == "none":
            g = p.gravity
            v_imp = math.sqrt(p.initial_speed ** 2 + 2 * g * p.initial_height)
            return [ck.close("known solution: vacuum range", ck.UNIVERSAL,
                             sol.qoi["range"][0], self.vx0 * self.t_vac, 1e-8, 1e-9,
                             detail="parabolic trajectory", unit="m"),
                    ck.close("known solution: vacuum impact speed", ck.UNIVERSAL,
                             sol.qoi["impact_speed"][0], v_imp, 1e-8, unit="m/s")]
        if p.drag_model == "constant" and p.initial_speed == 0:
            t_land, v_imp = self._drop_exact(p.initial_height, p.drag_coefficient)
            return [ck.close("known solution: drop with quadratic drag, impact speed",
                             ck.UNIVERSAL, sol.qoi["impact_speed"][0], v_imp, 1e-5,
                             detail="v_t * tanh(g t / v_t) solution", unit="m/s"),
                    ck.close("known solution: drop with quadratic drag, fall time",
                             ck.UNIVERSAL, sol.qoi["flight_time"][0], t_land, 1e-5,
                             unit="s")]
        # no closed form for this launch: run the same integrator on a vertical drop
        cd = p.drag_coefficient if p.drag_model == "constant" else sphere_cd(
            p.air_density * sol.data["v_t"] * p.diameter / p.air_viscosity)
        h = max(sol.data["apex"], 5.0)
        n = max(len(sol.fields[0].x) - 1, 8)
        hist, _, _ = self._fly(n, [0.0, h, 0.0, 0.0, 0.0], "constant", cd)
        _, v_imp = self._drop_exact(h, cd)
        return [ck.close("known solution: vertical-drop benchmark", ck.UNIVERSAL,
                         float(np.hypot(hist[-1, 3], hist[-1, 4])), v_imp, 1e-5,
                         detail=f"same integrator dropping the same ball from {h:.3g} m "
                         "vs the exact v_t*tanh(g t/v_t) solution", unit="m/s")]

    def domain_checks(self, sol):
        p, d = self.p, sol.data
        out = [ck.small("energy bookkeeping (kinetic + potential + drag work = initial)",
                        ck.FLUID, (d["e_end"] + d["work"] - d["e0"]) / d["e0"], 1e-6,
                        f"initial {d['e0']:.6g} J = final {d['e_end']:.6g} J + dissipated "
                        f"{d['work']:.6g} J; relative mismatch")]
        slower = sol.qoi["impact_speed"][0] <= math.sqrt(
            p.initial_speed ** 2 + 2 * p.gravity * p.initial_height) * (1 + 1e-9)
        shorter = abs(sol.qoi["range"][0]) <= abs(sol.qoi["vacuum_range"][0]) * (1 + 1e-9) + 1e-12
        out.append(ck.truth("drag only removes energy", ck.FLUID,
                            slower and shorter and d["work"] >= -1e-12,
                            "impact speed and range do not exceed the vacuum values"))
        re = d["re_max"]
        name = "Reynolds regime"
        if p.drag_model == "none":
            out.append(ck.skip(name, ck.FLUID, "drag is switched off"))
        elif p.drag_model == "constant":
            ok = 1e3 <= re <= 2.5e5
            out.append(ck.truth(
                name, ck.FLUID, ok,
                f"peak Re = {re:.3g}; a constant Cd near 0.47 holds in the Newton regime "
                "1e3 - 2.5e5" + ("" if ok else
                                 " - outside it (Stokes drag below, drag crisis above) "
                                 "use drag_model = sphere_curve or another Cd"), warn=True))
        else:
            ok = re <= 2.5e5
            out.append(ck.truth(
                name, ck.FLUID, ok,
                f"peak Re = {re:.3g}; the sphere drag curve is valid below the drag "
                "crisis (Re < 2.5e5)" + ("" if ok else
                                         " - beyond it Cd drops sharply and this model "
                                         "overestimates drag"), warn=True))
        return out
