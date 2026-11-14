"""Real-world abuse tests: flame, electric current, being dropped.

These are the simulations Create Anything runs on a design to see how it
survives outside the drawing: a part held in a flame, a conductor carrying
current, a product falling onto one of its parts.
"""
from __future__ import annotations

import math

import numpy as np
from scipy.linalg import solve_banded

from .. import checks as ck
from ..core import Field, Param, ParamError, Simulation, Solution, register
from ..solvers.transport import Grid, TransportProblem, dirichlet
from ..units import Q
from ._heat import SIGMA, heat_checks, to_c

G0 = 9.80665
DOMAIN = "real-world tests"


def _march_slab(n, thickness, k, rho, cp, q_abs, eps, h_front, h_back, t_amb,
                duration, steps, t_crit=None):
    """Backward-Euler march of a slab heated on its front face.

    Front face: absorbed flux minus convection minus re-radiation (the
    nonlinear loss is iterated to convergence every step, so the energy tally
    closes).  Returns a dict of histories and totals.
    """
    dx, dt = thickness / n, duration / steps
    cap, cond, u_half = rho * cp * dx, k / dx, 2.0 * k / dx
    T = np.full(n, float(t_amb))
    absorbed = lost_front = lost_back = 0.0
    hist_t, hist_s, hist_b = [0.0], [t_amb], [t_amb]
    t_hit, worst_picard = None, 0.0
    ts = float(t_amb)

    def loss(surface):
        return h_front * (surface - t_amb) + eps * SIGMA * (surface ** 4 - t_amb ** 4)

    g_back = u_half * h_back / (u_half + h_back) if h_back > 0 else 0.0
    base = np.zeros((3, n))                       # everything but the hot-face term
    base[1, :] = cap / dt
    base[1, :-1] += cond
    base[1, 1:] += cond
    base[0, 1:] = -cond
    base[2, :-1] = -cond
    base[1, -1] += g_back
    for step in range(steps):
        prev_s = ts
        rhs0 = cap / dt * T
        rhs0[-1] += g_back * t_amb
        for _ in range(60):
            h_eff = h_front + eps * SIGMA * (ts ** 2 + t_amb ** 2) * (ts + t_amb)
            g_front = u_half * h_eff / (u_half + h_eff) if h_eff > 0 else 0.0
            drive = (u_half / (u_half + h_eff)) * q_abs      # flux reaching cell 0
            ab, rhs = base.copy(), rhs0.copy()
            ab[1, 0] += g_front
            rhs[0] += g_front * t_amb + drive
            new = solve_banded((1, 1), ab, rhs, check_finite=False)
            ts_new = (u_half * new[0] + h_eff * t_amb + q_abs) / (u_half + h_eff)
            change = abs(ts_new - ts)
            ts = ts_new
            if change <= 1e-11 * max(ts, 1.0):
                break
        worst_picard = max(worst_picard, change / max(ts, 1.0))
        tb = (u_half * new[-1] + h_back * t_amb) / (u_half + h_back)
        absorbed += q_abs * dt
        lost_front += loss(ts) * dt
        lost_back += h_back * (tb - t_amb) * dt
        T = new
        now = (step + 1) * dt
        if t_crit is not None and t_hit is None and ts >= t_crit:
            frac = (t_crit - prev_s) / (ts - prev_s) if ts > prev_s else 1.0
            t_hit = now - dt + frac * dt
        hist_t.append(now)
        hist_s.append(ts)
        hist_b.append(tb)
    stored = float(np.sum(cap * (T - t_amb)))
    return {"T": T, "x": (np.arange(n) + 0.5) * dx, "surface": ts, "back": tb,
            "t": np.array(hist_t), "hist_surface": np.array(hist_s),
            "hist_back": np.array(hist_b), "absorbed": absorbed,
            "lost_front": lost_front, "lost_back": lost_back, "stored": stored,
            "t_hit": t_hit, "picard": worst_picard, "dt": dt, "dx": dx}


def _equilibrium(q_abs, eps, h, t_amb):
    """Surface temperature at which front-face losses equal the absorbed flux."""
    lo, hi = t_amb, t_amb + 1.0
    f = lambda t: h * (t - t_amb) + eps * SIGMA * (t ** 4 - t_amb ** 4) - q_abs
    while f(hi) < 0 and hi < 1e5:
        hi = t_amb + 2 * (hi - t_amb)
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if f(mid) < 0 else (lo, mid)
    return 0.5 * (lo + hi)


@register
class FlameExposure(Simulation):
    name = "flame_exposure"
    title = "Flame / radiant heat exposure of a wall (time to ignite or soften)"
    domain = DOMAIN
    description = """A wall of material with a heat flux on one face - a flame,
    a heater, a fire across the room.  Solves transient conduction through the
    thickness with convection and re-radiation from the hot face, and reports
    how long the surface takes to reach its critical temperature (ignition for
    plastics and wood, loss of strength for metals) and how hot the far side
    gets.  No charring, melting or flame spread: it answers "does it get hot
    enough, and when", not what happens after."""
    params = [
        Param("thickness", "3 mm", "m", "wall thickness", lo=0.0003, hi=0.1),
        Param("conductivity", "0.13 W/(m*K)", "W/(m*K)", "thermal conductivity", lo=0.02, hi=400),
        Param("density", "1240 kg/m^3", "kg/m^3", "density", lo=20, hi=9000),
        Param("specific_heat", "1800 J/(kg*K)", "J/(kg*K)", "specific heat", lo=100, hi=4000),
        Param("heat_flux", "30 kW/m^2", "W/m^2",
              "incident heat flux (candle at 5 cm ~10, small flame contact 30-60, "
              "room fire 20-50, torch 100+ kW/m^2)", lo=1e3, hi=2e5),
        Param("absorptivity", 0.9, None, "fraction absorbed (also used as emissivity)"),
        Param("h", "15 W/(m^2*K)", "W/(m^2*K)", "convective loss from the hot face",
              lo=0, hi=100),
        Param("back_face", "insulated", None, "far side of the wall",
              choices=["insulated", "convective"]),
        Param("ambient_temperature", "25 degC", "K", "surroundings and starting temperature"),
        Param("critical_temperature", "388 degC", "K",
              "ignition temperature (or softening / strength-loss temperature)"),
        Param("duration", "120 s", "s", "exposure time", lo=1, hi=3600),
    ]
    default_resolution = 64
    min_resolution = 8
    primary = ("surface_temperature_end", "back_face_temperature_end")
    resolution_doc = "cells through the wall (time steps scale with it)"

    def validate(self):
        p = self.p
        if not 0 < p.absorptivity <= 1:
            raise ParamError("absorptivity must be in (0, 1]")
        for key in ("thickness", "conductivity", "density", "specific_heat", "heat_flux",
                    "duration"):
            if getattr(p, key) <= 0:
                raise ParamError(f"{key} must be positive")
        if p.h < 0:
            raise ParamError("h cannot be negative")

    def equations(self):
        dT = Q(1, "K")
        return {
            "heat equation": {
                "storage rho*c*dT/t": self.q("density") * self.q("specific_heat") * dT
                / self.q("duration"),
                "conduction k*dT/L^2": self.q("conductivity") * dT / self.q("thickness") ** 2},
            "hot-face energy balance": {
                "incident flux": self.q("heat_flux"),
                "convection h*dT": self.q("h") * dT,
                "re-radiation sigma*T^4": Q(SIGMA, "W/(m^2*K^4)") * Q(1, "K") ** 4,
                "conduction k*dT/L": self.q("conductivity") * dT / self.q("thickness")}}

    def dimensionless(self):
        alpha = self.q("conductivity") / (self.q("density") * self.q("specific_heat"))
        return {"Fourier number alpha*t/L^2": alpha * self.q("duration") / self.q("thickness") ** 2,
                "Biot number h*L/k": self.q("h") * self.q("thickness") / self.q("conductivity")}

    def well_posed(self):
        p = self.p
        ok = p.critical_temperature > p.ambient_temperature
        return [ck.truth("boundary conditions complete", ck.MATH, True,
                         "initial temperature everywhere, a flux balance on the hot face "
                         f"and a {p.back_face} far face"),
                ck.truth("critical temperature above ambient", ck.MATH, ok,
                         "the part starts below its critical temperature" if ok else
                         "the part is already past its critical temperature before heating")]

    def _run(self, n):
        p = self.p
        return _march_slab(n, p.thickness, p.conductivity, p.density, p.specific_heat,
                           p.absorptivity * p.heat_flux, p.absorptivity, p.h,
                           p.h if p.back_face == "convective" else 0.0,
                           p.ambient_temperature, p.duration, 8 * n,
                           p.critical_temperature)

    def solve(self, n):
        p = self.p
        r = self._run(n)
        t_eq = _equilibrium(p.absorptivity * p.heat_flux, p.absorptivity, p.h,
                            p.ambient_temperature)
        peak = float(r["hist_surface"].max())
        hit = r["t_hit"]
        qoi = {
            "reaches_critical": (1.0 if hit is not None else 0.0, ""),
            "time_to_critical": (hit if hit is not None else p.duration, "s"),
            "surface_temperature_end": (to_c(r["surface"]), "degC"),
            "back_face_temperature_end": (to_c(r["back"]), "degC"),
            "margin_to_critical": (p.critical_temperature - peak, "K"),
            "equilibrium_surface_temperature": (to_c(t_eq), "degC"),
        }
        gross = max(r["absorbed"], 1e-300)
        resid = abs(r["absorbed"] - r["lost_front"] - r["lost_back"] - r["stored"]) / gross
        fields = [
            Field("temperature history", r["t"],
                  {"hot face [degC]": r["hist_surface"] - 273.15,
                   "far face [degC]": r["hist_back"] - 273.15,
                   "critical [degC]": np.full_like(r["t"], to_c(p.critical_temperature))},
                  unit="degC", xlabel="time [s]", ylabel="T [degC]"),
            Field("temperature through the wall at the end",
                  np.concatenate([[0.0], r["x"], [p.thickness]]) * 1e3,
                  np.concatenate([[r["surface"]], r["T"], [r["back"]]]) - 273.15,
                  unit="degC", xlabel="depth [mm]", ylabel="T [degC]")]
        alpha = p.conductivity / (p.density * p.specific_heat)
        cfl = (0.0, 1.0, "backward Euler is unconditionally stable (explicit part zero); "
               f"mesh Fourier number alpha*dt/dx^2 = {alpha * r['dt'] / r['dx'] ** 2:.3g} "
               "sets accuracy only")
        return Solution(qoi, fields, max(resid, r["picard"]), cfl=cfl,
                        residual_note="energy closure and radiation-iteration error of the march",
                        residual_limit=1e-7, data={"r": r, "t_eq": t_eq})

    def known_solution(self, sol):
        # same marcher, no losses, thick wall: semi-infinite solid under constant flux
        k, rho, cp, q, t_end, thick = 0.2, 1200.0, 1500.0, 2e4, 20.0, 0.05
        exact = 2 * q * math.sqrt(k / (rho * cp) * t_end / math.pi) / k
        errs = []
        for n in (60, 120):
            r = _march_slab(n, thick, k, rho, cp, q, 0.0, 0.0, 0.0, 300.0, t_end, 8 * n)
            errs.append(abs((r["surface"] - 300.0) - exact) / exact)
        ok = errs[1] < 0.01 and errs[1] < errs[0]
        return [ck.Check("known solution: constant-flux half-space", ck.UNIVERSAL,
                         ck.PASS if ok else ck.FAIL,
                         "same time-marcher with losses off against dT = 2 q sqrt(alpha t/pi)/k: "
                         f"error {100 * errs[0]:.3g}% -> {100 * errs[1]:.3g}% on refinement "
                         "(limit 1%)", errs[1], 0.0, 0.01)]

    def domain_checks(self, sol):
        p, r = self.p, sol.data["r"]
        gross = max(r["absorbed"], 1e-300)
        out = [ck.small("energy balance (absorbed = lost + stored)", ck.HEAT,
                        (r["absorbed"] - r["lost_front"] - r["lost_back"] - r["stored"]) / gross,
                        1e-7,
                        f"absorbed {r['absorbed']:.5g}, re-radiated and convected "
                        f"{r['lost_front']:.5g}, lost from the far face {r['lost_back']:.5g}, "
                        f"stored {r['stored']:.5g} J/m^2; relative mismatch")]
        lo, hi = p.ambient_temperature, sol.data["t_eq"]
        peak = float(max(r["hist_surface"].max(), r["T"].max()))
        out.append(ck.bounds(
            "temperature bounds", ck.HEAT, lo, hi, float(min(r["T"].min(), r["back"])), peak,
            detail="never colder than ambient, never hotter than the face temperature at "
                   "which losses balance the flame", unit="K"))
        prof = np.concatenate([[r["surface"]], r["T"], [r["back"]]])
        mono = bool(np.all(np.diff(prof) <= 1e-9 * prof[0]))
        out.append(ck.truth("heat flows from hot to cold (second law)", ck.HEAT, mono,
                            "temperature falls monotonically from the heated face to the far "
                            "face" if mono else "temperature profile is not monotone"))
        # back of envelope: a loss-free insulated slab is the hottest this wall can get
        q_abs = p.absorptivity * p.heat_flux
        bound = (q_abs * p.duration / (p.density * p.specific_heat * p.thickness)
                 + q_abs * p.thickness / (3 * p.conductivity))
        rise = float(r["hist_surface"].max()) - p.ambient_temperature
        out.append(ck.truth(
            "back-of-envelope bound: loss-free slab", ck.HEAT, rise <= bound * (1 + 1e-6),
            f"hot-face rise {rise:.4g} K stays below q t/(rho c L) + q L/(3 k) = {bound:.4g} K, "
            "the rise of the same wall with every loss switched off"))
        return out


@register
class WireHeating(Simulation):
    name = "wire_heating"
    title = "Current-carrying conductor (Joule heating and voltage drop)"
    domain = DOMAIN
    description = """A wire or bar carrying current, cooled by the air around
    it and clamped to terminals at both ends.  Solves k A T'' + I^2 rho_e / A -
    h P (T - T_inf) = 0 along its length and reports the hottest point, the
    voltage drop and the power wasted as heat.  Resistivity is taken at room
    temperature."""
    params = [
        Param("length", "1 m", "m", "conductor length between terminals", lo=0.01, hi=50),
        Param("diameter", "1 mm", "m", "conductor diameter (round equivalent)", lo=1e-4, hi=0.03),
        Param("current", "10 A", "A", "current", lo=0.01, hi=500),
        Param("resistivity", "1.72e-8 ohm*m", "ohm*m", "electrical resistivity (copper 1.72e-8)"),
        Param("conductivity", "400 W/(m*K)", "W/(m*K)", "thermal conductivity", lo=10, hi=430),
        Param("h", "12 W/(m^2*K)", "W/(m^2*K)", "convection from the surface "
              "(bare wire in still air 10-25; lower inside insulation or a bundle)", lo=1, hi=200),
        Param("ambient_temperature", "25 degC", "K", "air temperature"),
        Param("terminal_temperature", "25 degC", "K", "temperature of the end terminals"),
    ]
    default_resolution = 64
    primary = ("max_temperature",)

    def validate(self):
        for key in ("length", "diameter", "resistivity", "conductivity", "h"):
            if getattr(self.p, key) <= 0:
                raise ParamError(f"{key} must be positive")

    def _numbers(self):
        p = self.p
        area = math.pi * p.diameter ** 2 / 4
        heat = p.current ** 2 * p.resistivity / area ** 2
        return area, heat, 4 * p.h / p.diameter

    def equations(self):
        area = Q(self._numbers()[0], "m^2")
        return {"conductor energy balance": {
            "conduction k*dT/L^2": self.q("conductivity") * Q(1, "K") / self.q("length") ** 2,
            "Joule heating I^2*rho_e/A^2": self.q("current") ** 2 * self.q("resistivity")
            / area ** 2,
            "convection 4*h*dT/d": self.q("h") * Q(1, "K") / self.q("diameter")},
            "Ohm's law": {"voltage drop": Q(1, "V"),
                          "I*rho_e*L/A": self.q("current") * self.q("resistivity")
                          * self.q("length") / area}}

    def dimensionless(self):
        return {"Biot number h*d/k": self.q("h") * self.q("diameter") / self.q("conductivity")}

    def _problem(self, n):
        p = self.p
        area, heat, hv = self._numbers()
        return TransportProblem(
            Grid(n, p.length, ly=area), p.conductivity, sink=hv,
            source=heat + hv * p.ambient_temperature,
            bcs={"west": dirichlet(p.terminal_temperature),
                 "east": dirichlet(p.terminal_temperature)})

    def well_posed(self):
        return self._problem(8).well_posed(what="temperature")

    def solve(self, n):
        p = self.p
        area, heat, hv = self._numbers()
        ts = self._problem(n).solve_steady()
        g = ts.grid
        resistance = p.resistivity * p.length / area
        qoi = {"max_temperature": (to_c(ts.sample(p.length / 2)), "degC"),
               "voltage_drop": (p.current * resistance, "V"),
               "power_dissipated": (p.current ** 2 * resistance, "W"),
               "resistance": (resistance * 1e3, "mohm"),
               "current_density": (p.current / area / 1e6, "A/mm^2")}
        fields = [Field("temperature along the conductor", g.x * 1e3, ts.phi[:, 0] - 273.15,
                        unit="degC", xlabel="position [mm]", ylabel="T [degC]")]
        return Solution(qoi, fields, ts.residual, data={"ts": ts})

    def _exact_mid(self):
        p = self.p
        _, heat, hv = self._numbers()
        m = math.sqrt(hv / p.conductivity)
        far = p.ambient_temperature + heat / hv
        arg = m * p.length / 2
        return far + (p.terminal_temperature - far) / (math.cosh(arg) if arg < 700 else math.inf)

    def known_solution(self, sol):
        exact = to_c(self._exact_mid())
        return [ck.close("known solution: mid-span temperature", ck.UNIVERSAL,
                         sol.qoi["max_temperature"][0], exact, 2e-3, 0.02,
                         detail="exact cosh solution of the fin equation with heating",
                         unit="degC")]

    def domain_checks(self, sol):
        p = self.p
        area, heat, hv = self._numbers()
        ts = sol.data["ts"]
        out = heat_checks(ts, lo=min(p.ambient_temperature, p.terminal_temperature),
                          generation=heat, loss=(hv, p.ambient_temperature),
                          why="heating only: nothing is colder than the surroundings")
        generated = heat * area * p.length
        out.append(ck.close("charge / energy bookkeeping: I^2 R = heat generated", ck.EM,
                            sol.qoi["power_dissipated"][0], generated, 1e-9,
                            detail="electrical power from Ohm's law vs the volumetric "
                                   "heating integrated over the conductor", unit="W"))
        far = to_c(p.ambient_temperature + heat / hv)
        peak = sol.qoi["max_temperature"][0]
        lo_b = to_c(min(p.ambient_temperature, p.terminal_temperature))
        out.append(ck.truth(
            "back-of-envelope R-network bound", ck.HEAT,
            peak <= max(far, to_c(p.terminal_temperature)) + 1e-6 and peak >= lo_b - 1e-6,
            f"hottest point {peak:.4g} degC is at most the long-wire value "
            f"T_inf + I^2 R' / (h P) = {far:.4g} degC (end terminals can only cool it)"))
        return out

    def symmetry_checks(self, sol):
        return sol.data["ts"].symmetry_checks()


@register
class DropImpact(Simulation):
    name = "drop_impact"
    title = "Drop impact on a beam-like part"
    domain = DOMAIN
    description = """Something of a given mass falls from a height and is
    caught by a part that bends like a beam (a cantilevered arm hit at its
    tip, or a span hit at its centre).  The fall is integrated in time as a
    mass landing on the part's bending stiffness; the peak force, deflection
    and stress follow.  Elastic, with no energy lost in the impact and the
    part's own mass ignored, so the answer is on the safe side."""
    params = [
        Param("mass", "0.5 kg", "kg", "mass that falls", lo=0.001, hi=200),
        Param("drop_height", "1 m", "m", "height of the fall", lo=0.0, hi=20),
        Param("length", "100 mm", "m", "beam length (cantilever) or span", lo=0.005, hi=5),
        Param("width", "20 mm", "m", "section width", lo=0.001, hi=1),
        Param("height", "4 mm", "m", "section height in the direction of the impact",
              lo=0.0005, hi=0.5),
        Param("youngs_modulus", "3.5 GPa", "Pa", "Young's modulus"),
        Param("yield_strength", "50 MPa", "Pa", "yield (or breaking) strength"),
        Param("support", "cantilever", None, "how the part is held",
              choices=["cantilever", "simply_supported"]),
    ]
    default_resolution = 64
    min_resolution = 8
    primary = ("peak_deflection",)
    resolution_doc = "time steps per quarter period of the bounce"

    def validate(self):
        for key in ("mass", "length", "width", "height", "youngs_modulus", "yield_strength"):
            if getattr(self.p, key) <= 0:
                raise ParamError(f"{key} must be positive")
        if self.p.drop_height < 0:
            raise ParamError("drop_height cannot be negative")

    def _beam(self):
        p = self.p
        inertia = p.width * p.height ** 3 / 12
        if p.support == "cantilever":
            stiff, arm = 3 * p.youngs_modulus * inertia / p.length ** 3, p.length
        else:
            stiff, arm = 48 * p.youngs_modulus * inertia / p.length ** 3, p.length / 4
        return stiff, arm, p.width * p.height ** 2 / 6

    def equations(self):
        stiff = Q(self._beam()[0], "N/m")
        x = Q(1, "m")
        return {"equation of motion": {
            "inertia m*x/t^2": self.q("mass") * x / Q(1, "s") ** 2,
            "weight m*g": self.q("mass") * Q(G0, "m/s^2"),
            "spring k*x": stiff * x},
            "energy": {"fall m*g*h": self.q("mass") * Q(G0, "m/s^2") * self.q("length"),
                       "strain k*x^2/2": stiff * x ** 2}}

    def dimensionless(self):
        static = Q(self.p.mass * G0 / self._beam()[0], "m")
        return {"drop height / static deflection": self.q("drop_height") / static}

    def well_posed(self):
        return [ck.truth("initial-value problem complete", ck.MATH, True,
                         "position and velocity at first contact are known "
                         "(x = 0, v = sqrt(2 g h))")]

    def solve(self, n):
        p = self.p
        stiff, arm, modulus = self._beam()
        omega = math.sqrt(stiff / p.mass)
        dt = (math.pi / 2 / omega) / n
        x, v = 0.0, math.sqrt(2 * G0 * p.drop_height)
        e0 = 0.5 * p.mass * v * v
        a = G0 - stiff / p.mass * x
        xs, ts, drift = [x], [0.0], 0.0
        steps = 0
        while steps < 40 * n:                     # velocity Verlet until it rebounds
            x_new = x + v * dt + 0.5 * a * dt * dt
            a_new = G0 - stiff / p.mass * x_new
            v = v + 0.5 * (a + a_new) * dt
            x, a = x_new, a_new
            steps += 1
            xs.append(x)
            ts.append(steps * dt)
            energy = 0.5 * p.mass * v * v + 0.5 * stiff * x * x - p.mass * G0 * x
            drift = max(drift, abs(energy - e0) / max(e0 + p.mass * G0 * abs(x), 1e-300))
            if len(xs) > 3 and xs[-1] < xs[-2] < xs[-3] and x < 0.5 * max(xs):
                break
        xs = np.array(xs)
        i = int(np.argmax(xs))
        peak = xs[i]
        if 0 < i < len(xs) - 1:                   # parabola through the top three points
            y0, y1, y2 = xs[i - 1], xs[i], xs[i + 1]
            den = y0 - 2 * y1 + y2
            if den < 0:
                peak = y1 - 0.125 * (y2 - y0) ** 2 / den
        force = stiff * peak
        stress = force * arm / modulus
        static = p.mass * G0 / stiff
        qoi = {"peak_deflection": (peak * 1e3, "mm"),
               "peak_force": (force, "N"),
               "impact_factor": (force / (p.mass * G0), ""),
               "peak_stress": (stress / 1e6, "MPa"),
               "safety_factor": (p.yield_strength / stress if stress > 0 else math.inf, ""),
               "static_deflection": (static * 1e3, "mm"),
               "impact_speed": (math.sqrt(2 * G0 * p.drop_height), "m/s")}
        fields = [Field("deflection during the impact", np.array(ts) * 1e3, xs * 1e3,
                        unit="mm", xlabel="time since contact [ms]", ylabel="deflection [mm]")]
        return Solution(qoi, fields, drift, cfl=(omega * dt, 2.0,
                        "velocity-Verlet step x natural frequency (omega*dt)"),
                        residual_note="largest relative drift of total energy during the march",
                        residual_limit=1e-3, data={"drift": drift, "static": static,
                                                   "stiff": stiff, "peak": peak})

    def known_solution(self, sol):
        p, static = self.p, sol.data["static"]
        exact = static + math.sqrt(static ** 2 + 2 * p.drop_height * static)
        return [ck.close("known solution: energy-method peak deflection", ck.UNIVERSAL,
                         sol.qoi["peak_deflection"][0], exact * 1e3, 2e-3,
                         detail="d_max = d_st + sqrt(d_st^2 + 2 h d_st)", unit="mm")]

    def domain_checks(self, sol):
        p, d = self.p, sol.data
        fall = p.mass * G0 * (p.drop_height + d["peak"])
        strain = 0.5 * d["stiff"] * d["peak"] ** 2
        out = [ck.close("energy balance: fall energy = strain energy at the bottom", ck.STRUCT,
                        strain, fall, 5e-3,
                        detail="m g (h + d_max) vs k d_max^2 / 2", unit="J"),
               ck.small("energy conserved during the march", ck.STRUCT, d["drift"], 1e-3,
                        "kinetic + strain - gravitational, largest relative drift"),
               ck.close("force balance at peak: spring force = k x deflection", ck.STRUCT,
                        sol.qoi["peak_force"][0], d["stiff"] * d["peak"], 1e-12, unit="N")]
        slender = p.length / p.height
        out.append(ck.truth("model validity: slender beam", ck.MATH, slender >= 8,
                            f"length / height = {slender:.3g} (beam theory wants >= 8)",
                            warn=True))
        return out
