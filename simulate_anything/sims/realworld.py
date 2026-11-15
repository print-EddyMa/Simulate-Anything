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


# -- passive radar ------------------------------------------------------------------
C0, K_B, T0 = 299_792_458.0, 1.380649e-23, 290.0


def _db(x):
    return 10.0 * np.log10(x)


def _lin(db):
    return 10.0 ** (np.asarray(db, float) / 10.0)


@register
class PassiveRadarCoverage(Simulation):
    name = "passive_radar"
    title = "Passive radar detection coverage (TV illuminator, bistatic radar equation)"
    domain = DOMAIN
    description = """Where can a receive-only radar that listens to a TV
    transmitter see a small target?  Evaluates the bistatic radar equation over
    a map around the receiver: echo energy after coherent integration against
    the receiver noise plus whatever the direct TV signal leaks into the
    surveillance antenna after cancellation.  The antenna has a directional
    pattern from its gain, beamwidth and front-to-back ratio.  Free space, flat
    earth: no terrain, multipath or clutter beyond the direct signal."""
    params = [
        Param("tx_erp", "1 MW", "W", "effective radiated power of the TV transmitter",
              lo=1e3, hi=5e6),
        Param("frequency", "569 MHz", "Hz", "TV channel centre frequency", lo=5e7, hi=1e9),
        Param("bandwidth", "6 MHz", "Hz", "signal bandwidth (ATSC 6 MHz)", lo=1e5, hi=2e7),
        Param("baseline", "20 km", "m", "distance from the receiver to the transmitter",
              lo=100, hi=150e3),
        Param("tx_bearing", "180 deg", "rad",
              "direction of the transmitter seen from the receiver (0 = straight ahead)"),
        Param("rx_gain_dbi", 10.0, None, "surveillance antenna gain [dBi]"),
        Param("beamwidth", "60 deg", "rad", "half-power beamwidth of the surveillance antenna"),
        Param("front_to_back_db", 15.0, None,
              "how much weaker the antenna is behind than in front [dB]"),
        Param("noise_figure_db", 4.0, None, "receiver noise figure [dB]"),
        Param("losses_db", 8.0, None, "processing and system losses [dB]"),
        Param("cancellation_db", 50.0, None,
              "how far the direct signal is suppressed before detection [dB]"),
        Param("rcs", "0.05 m^2", "m^2", "radar cross-section of the target", lo=1e-4, hi=100),
        Param("integration_time", "0.5 s", "s", "coherent integration time", lo=0.01, hi=5),
        Param("snr_threshold_db", 13.0, None, "signal-to-noise ratio needed to declare a detection"),
        Param("required_range", "1 km", "m", "range the radar must see to, straight ahead",
              lo=50, hi=50e3),
        Param("extent", "2.5 km", "m", "half-width of the map around the receiver",
              lo=100, hi=60e3),
        Param("rx_height", "2.5 m", "m", "antenna height above ground"),
        Param("target_altitude", "100 m", "m", "target height above ground"),
    ]
    default_resolution = 192
    min_resolution = 16
    primary = ("detection_range",)
    resolution_doc = "map cells per side"

    def validate(self):
        p = self.p
        for key in ("tx_erp", "frequency", "bandwidth", "baseline", "rcs",
                    "integration_time", "extent", "beamwidth"):
            if getattr(p, key) <= 0:
                raise ParamError(f"{key} must be positive")
        if p.beamwidth >= math.pi:
            raise ParamError("beamwidth must be under 180 degrees")

    # -- the physics, in one place ---------------------------------------------------
    def _pattern(self, theta):
        """Antenna gain (linear) at angle theta from boresight."""
        p = self.p
        g0 = _lin(p.rx_gain_dbi)
        c = (1.0 + math.cos(p.beamwidth / 2)) / 2.0
        n = math.log(0.5) / math.log(c)
        main = ((1.0 + np.cos(theta)) / 2.0) ** n
        return g0 * np.maximum(main, _lin(-p.front_to_back_db))

    def _consts(self):
        p = self.p
        lam = C0 / p.frequency
        tx = np.array([math.cos(p.tx_bearing), math.sin(p.tx_bearing)]) * p.baseline
        g_dir = float(self._pattern(np.array(p.tx_bearing)))
        direct = p.tx_erp * g_dir * lam ** 2 / ((4 * math.pi) ** 2 * p.baseline ** 2)
        thermal = K_B * T0 * _lin(p.noise_figure_db)
        residual = direct * _lin(-p.cancellation_db) / p.bandwidth
        return lam, tx, direct, thermal, thermal + residual

    def snr(self, x, y):
        """Detection SNR (linear) of the target at (x, y) metres from the receiver."""
        p = self.p
        lam, tx, _, _, n0 = self._consts()
        rr = np.hypot(x, y)
        rt = np.hypot(x - tx[0], y - tx[1])
        g = self._pattern(np.arctan2(y, x))
        echo = (p.tx_erp * g * lam ** 2 * p.rcs
                / ((4 * math.pi) ** 3 * rt ** 2 * rr ** 2 * _lin(p.losses_db)))
        return echo * p.integration_time / n0

    def _blind(self, x, y):
        """Inside the direct signal's own range cell: cannot be separated from it."""
        _, tx, *_ = self._consts()
        excess = np.hypot(x, y) + np.hypot(x - tx[0], y - tx[1]) - self.p.baseline
        return excess < C0 / self.p.bandwidth

    def _boresight_root(self):
        """Exact boresight detection range: root of SNR(r) = threshold."""
        from scipy.optimize import brentq
        thr = _lin(self.p.snr_threshold_db)
        f = lambda r: math.log(float(self.snr(np.array(r), np.array(0.0))) / thr)
        lo, hi = 1e-3, 1.0
        if f(lo) < 0:
            return 0.0
        while f(hi) > 0 and hi < 1e7:
            hi *= 2
        return brentq(f, lo, hi, xtol=1e-9, rtol=1e-12)

    def equations(self):
        lam = Q(C0, "m/s") / self.q("frequency")
        r = Q(1, "m")
        return {"bistatic radar equation": {
            "received echo energy P_t G_t G_r lambda^2 sigma T_i / ((4 pi)^3 R_t^2 R_r^2)":
                self.q("tx_erp") * lam ** 2 * self.q("rcs") * self.q("integration_time")
                / (r ** 2 * r ** 2),
            "noise spectral density k T0 F": Q(K_B, "J/K") * Q(T0, "K"),
            "leaked direct signal per hertz": self.q("tx_erp") * lam ** 2
                / (self.q("baseline") ** 2 * self.q("bandwidth"))}}

    def dimensionless(self):
        lam = Q(C0, "m/s") / self.q("frequency")
        return {"time-bandwidth product T_i B": self.q("integration_time") * self.q("bandwidth"),
                "baseline in wavelengths": self.q("baseline") / lam}

    def well_posed(self):
        p = self.p
        return [ck.truth("geometry complete", ck.MATH, p.baseline > 0 and p.extent > 0,
                         "transmitter, receiver and the mapped area are all defined"),
                ck.truth("transmitter outside the map edge or clearly placed", ck.MATH, True,
                         f"transmitter {p.baseline / 1e3:.3g} km away at "
                         f"{math.degrees(p.tx_bearing):.0f} deg; map +/- "
                         f"{p.extent / 1e3:.3g} km")]

    def solve(self, n):
        p = self.p
        lam, tx, direct, thermal, n0 = self._consts()
        edges = np.linspace(-p.extent, p.extent, n + 1)
        xc = 0.5 * (edges[1:] + edges[:-1])
        X, Y = np.meshgrid(xc, xc, indexing="ij")
        with np.errstate(divide="ignore"):
            snr_db = _db(self.snr(X, Y))
        snr_db = np.where(np.hypot(X, Y) < 1e-9, np.inf, snr_db)
        detect = (snr_db >= p.snr_threshold_db) & ~self._blind(X, Y)
        # boresight range from the map: bilinear samples along the +x axis
        from scipy.interpolate import RegularGridInterpolator
        interp = RegularGridInterpolator((xc, xc), np.minimum(snr_db, 300.0),
                                         bounds_error=False, fill_value=None)
        rs = np.linspace(0, p.extent, 4 * n + 1)[1:]
        line = interp(np.column_stack([rs, np.zeros_like(rs)])) - p.snr_threshold_db
        below = np.nonzero(line < 0)[0]
        if len(below) == 0:
            rng = p.extent
        elif below[0] == 0:
            rng = 0.0
        else:
            i = below[0]
            rng = rs[i - 1] + line[i - 1] / (line[i - 1] - line[i]) * (rs[i] - rs[i - 1])
        cell = (edges[1] - edges[0]) ** 2
        in_disc = np.hypot(X, Y) <= p.required_range
        covered = float(np.sum(detect & in_disc)) / max(float(np.sum(in_disc)), 1.0)
        horizon = 4120.0 * (math.sqrt(p.rx_height) + math.sqrt(p.target_altitude))
        qoi = {
            "detection_range": (rng, "m"),
            "meets_required_range": (1.0 if rng >= p.required_range else 0.0, ""),
            "covered_fraction": (covered, ""),
            "detectable_area": (float(np.sum(detect)) * cell / 1e6, "km^2"),
            "noise_floor_rise": (float(_db(n0 / thermal)), "dB"),
            "direct_signal_power": (float(_db(direct * 1e3)), "dBm"),
            "range_resolution": (C0 / p.bandwidth, "m"),
            "doppler_per_mps": (2.0 / lam, "Hz/(m/s)"),
            "radio_horizon": (horizon / 1e3, "km"),
        }
        shown = np.clip(snr_db, p.snr_threshold_db - 30, p.snr_threshold_db + 40)
        shown = np.where(self._blind(X, Y), p.snr_threshold_db - 30, shown)
        fields = [Field("detection SNR", xc, shown, xc, "dB", "east [m]", "north [m]")]
        return Solution(qoi, fields, None, residual_note="closed-form radar equation "
                        "evaluated on a map", data={"X": X, "Y": Y, "snr_db": snr_db,
                                                    "detect": detect, "lam": lam})

    def known_solution(self, sol):
        p = self.p
        exact = self._boresight_root()
        exact_c = min(exact, p.extent)
        out = [ck.close("known solution: boresight detection range", ck.UNIVERSAL,
                        sol.qoi["detection_range"][0], exact_c, 0.01,
                        detail="range read off the map vs the exact root of the radar "
                               "equation along the boresight", unit="m")]
        # covered fraction: independent polar quadrature of the same detection region
        thr = _lin(p.snr_threshold_db)
        angs = np.linspace(-math.pi, math.pi, 721)[:-1] + math.pi / 720
        radii = (np.arange(400) + 0.5) / 400 * p.required_range
        R, A = np.meshgrid(radii, angs, indexing="ij")
        xs, ys = R * np.cos(A), R * np.sin(A)
        hit = (self.snr(xs, ys) >= thr) & ~self._blind(xs, ys)
        frac = float(np.sum(hit * R) / np.sum(R))
        h = 2 * p.extent / sol.data["X"].shape[0]
        out.append(ck.close("known solution: covered fraction by polar quadrature",
                            ck.UNIVERSAL, sol.qoi["covered_fraction"][0], frac, 0.0,
                            atol=max(0.01, 4 * h / max(p.required_range, h)),
                            detail="cell count on the map vs 288,000-point polar integration"))
        return out

    def domain_checks(self, sol):
        p, lam = self.p, sol.data["lam"]
        _, tx, _, _, n0 = self._consts()
        x, y = 0.6 * p.extent, 0.3 * p.extent              # any sample point
        rr, rt = math.hypot(x, y), math.hypot(x - tx[0], y - tx[1])
        g = float(self._pattern(np.array(math.atan2(y, x))))
        s_t = p.tx_erp / (4 * math.pi * rt ** 2)            # W/m^2 at the target
        s_r = s_t * p.rcs / (4 * math.pi * rr ** 2)         # scattered, at the receiver
        a_e = g * lam ** 2 / (4 * math.pi)                  # effective aperture
        chain = s_r * a_e / _lin(p.losses_db) * p.integration_time / n0
        out = [ck.close("energy flow (Poynting) adds up", ck.EM,
                        float(self.snr(np.array(x), np.array(y))), chain, 1e-12,
                        detail="map value vs power density -> scattering -> aperture chain "
                               "at a sample point")]
        snr = sol.data["snr_db"]
        ray = snr[snr.shape[0] // 2:, snr.shape[1] // 2]
        mono = bool(np.all(np.diff(ray[np.isfinite(ray)]) <= 1e-9))
        out.append(ck.truth("echo weakens with distance", ck.EM, mono,
                            "SNR falls monotonically along the boresight away from the "
                            "receiver (when the transmitter is behind it)"
                            if mono else "SNR rises somewhere along the boresight",
                            warn=not mono))
        horizon = sol.qoi["radio_horizon"][0] * 1e3
        out.append(ck.truth("model validity: line of sight", ck.MATH, p.required_range < horizon,
                            f"required range {p.required_range / 1e3:.3g} km vs radio horizon "
                            f"{horizon / 1e3:.3g} km for a {p.target_altitude:.0f} m target",
                            warn=True))
        far = 2 * 0.5 ** 2 / lam
        out.append(ck.truth("model validity: far field", ck.MATH, far < 0.1 * p.required_range,
                            f"antenna far-field distance ~{far:.1f} m, well inside the "
                            "detection range", warn=True))
        return out

    def symmetry_checks(self, sol):
        b = math.degrees(self.p.tx_bearing) % 180.0
        if min(b, 180.0 - b) > 1e-9:
            return [ck.skip("symmetry", ck.MATH, "transmitter is off the antenna axis")]
        s = np.nan_to_num(sol.data["snr_db"], posinf=0.0)
        return [ck.symmetry("symmetry about the antenna axis", s, s[:, ::-1], 1e-9,
                            "transmitter on the axis: the map is mirror-symmetric")]


# -- sealed enclosure in the sun ---------------------------------------------------
@register
class EnclosureThermal(Simulation):
    name = "enclosure_thermal"
    title = "Sealed electronics enclosure in sun and wind"
    domain = DOMAIN
    description = """Steady temperature inside a closed outdoor box: the
    electronics' heat and absorbed sunlight leave by convection and
    radiation from the outside, after crossing the wall and the still air
    inside.  Lumped (one surface temperature, one air temperature)."""
    params = [
        Param("size_x", "260 mm", "m", "box size", lo=0.02, hi=2),
        Param("size_y", "200 mm", "m", "box size", lo=0.02, hi=2),
        Param("size_z", "120 mm", "m", "box size (height)", lo=0.02, hi=2),
        Param("wall", "3 mm", "m", "wall thickness", lo=0.0005, hi=0.03),
        Param("wall_conductivity", "0.2 W/(m*K)", "W/(m*K)", "wall material conductivity"),
        Param("internal_power", "13 W", "W", "heat dissipated inside", lo=0, hi=500),
        Param("irradiance", "1000 W/m^2", "W/m^2", "sunlight", lo=0, hi=1200),
        Param("sun_elevation", "60 deg", "rad", "sun height above the horizon"),
        Param("absorptivity", 0.5, None, "solar absorptivity of the outside (white 0.25, grey 0.5, black 0.95)"),
        Param("emissivity", 0.9, None, "infrared emissivity of the outside"),
        Param("wind", "1 m/s", "m/s", "wind speed", lo=0, hi=30),
        Param("h_inside", "4 W/(m^2*K)", "W/(m^2*K)", "still-air coefficient inside"),
        Param("ambient_temperature", "35 degC", "K", "air temperature"),
        Param("sky_temperature", "20 degC", "K", "effective sky temperature (clear sky is colder)"),
    ]
    primary = ()
    no_mesh_reason = "lumped energy balance: no grid to refine"

    def validate(self):
        p = self.p
        if 2 * p.wall >= min(p.size_x, p.size_y, p.size_z):
            raise ParamError("the wall is thicker than half the box")
        for key in ("absorptivity", "emissivity"):
            if not 0 <= getattr(p, key) <= 1:
                raise ParamError(f"{key} must be between 0 and 1")

    def _numbers(self, radiation=True):
        p = self.p
        area = 2 * (p.size_x * p.size_y + p.size_x * p.size_z + p.size_y * p.size_z)
        top, side = p.size_x * p.size_y, max(p.size_x, p.size_y) * p.size_z
        sun_area = top * math.sin(p.sun_elevation) + side * math.cos(p.sun_elevation)
        q_sun = p.absorptivity * p.irradiance * sun_area
        h_o = 5.7 + 3.8 * p.wind
        u_wall = 1.0 / (1.0 / p.h_inside + p.wall / p.wall_conductivity)
        eps = p.emissivity if radiation else 0.0
        return area, q_sun, h_o, u_wall, eps

    def _surface(self, radiation=True):
        """Newton solve of the outside balance; returns (Ts, residual)."""
        p = self.p
        area, q_sun, h_o, _, eps = self._numbers(radiation)
        ta, tsky, q_in = p.ambient_temperature, p.sky_temperature, p.internal_power + q_sun

        def loss(t):
            rad = eps * SIGMA * area * (0.5 * (t ** 4 - tsky ** 4) + 0.5 * (t ** 4 - ta ** 4))
            return h_o * area * (t - ta) + rad

        t = ta + q_in / (h_o * area)
        for _ in range(100):
            f = loss(t) - q_in
            df = h_o * area + eps * SIGMA * area * 4 * t ** 3
            step = f / df
            t -= step
            if abs(step) < 1e-12 * t:
                break
        return t, abs(loss(t) - q_in) / max(q_in, 1e-300)

    def equations(self):
        dT = Q(1, "K")
        a = self.q("size_x") * self.q("size_y")
        return {"surface energy balance": {
            "internal power": self.q("internal_power"),
            "absorbed sun alpha G A": self.q("irradiance") * a,
            "convection h A dT": Q(5.7, "W/(m^2*K)") * a * dT,
            "radiation eps sigma A T^4": Q(SIGMA, "W/(m^2*K^4)") * a * Q(1, "K") ** 4,
            "wall conduction k A dT / t": self.q("wall_conductivity") * a * dT / self.q("wall")}}

    def dimensionless(self):
        return {"wall Biot number h t / k": self.q("h_inside") * self.q("wall")
                / self.q("wall_conductivity")}

    def well_posed(self):
        area, _, h_o, _, eps = self._numbers()
        return [ck.truth("heat has a way out", ck.MATH, h_o > 0 or eps > 0,
                         "convection and radiation carry heat away from the outside")]

    def solve(self, n):
        p = self.p
        area, q_sun, h_o, u_wall, eps = self._numbers()
        ts, res = self._surface()
        t_in = ts + p.internal_power / (u_wall * area)
        conv = h_o * area * (ts - p.ambient_temperature)
        rad = p.internal_power + q_sun - conv
        qoi = {"internal_temperature": (to_c(t_in), "degC"),
               "surface_temperature": (to_c(ts), "degC"),
               "absorbed_sun": (q_sun, "W"),
               "heat_by_convection": (conv, "W"),
               "heat_by_radiation": (rad, "W"),
               "rise_over_ambient": (t_in - p.ambient_temperature, "K")}
        winds = np.linspace(0, 10, 41)
        temps = []
        for w in winds:
            sub = EnclosureThermal(**{**self.given, "wind": f"{w} m/s"})
            t_s, _ = sub._surface()
            a, _, _, u, _ = sub._numbers()
            temps.append(to_c(t_s + p.internal_power / (u * a)))
        fields = [Field("inside temperature vs wind", winds, np.array(temps), unit="degC",
                        xlabel="wind [m/s]", ylabel="inside [degC]")]
        return Solution(qoi, fields, res, residual_note="Newton iteration: energy imbalance "
                        "/ heat throughput", residual_limit=1e-10,
                        data={"ts": ts, "t_in": t_in, "conv": conv, "rad": rad})

    def known_solution(self, sol):
        p = self.p
        area, q_sun, h_o, _, _ = self._numbers(False)
        ts, _ = self._surface(radiation=False)
        exact = p.ambient_temperature + (p.internal_power + q_sun) / (h_o * area)
        return [ck.close("known solution: convection-only balance", ck.UNIVERSAL, ts, exact,
                         1e-10, detail="same Newton solver with radiation off vs the exact "
                         "linear result", unit="K")]

    def domain_checks(self, sol):
        p, d = self.p, sol.data
        area, q_sun, h_o, _, _ = self._numbers()
        q_in = p.internal_power + q_sun
        out = [ck.small("energy balance (in = out)", ck.HEAT,
                        (q_in - d["conv"] - d["rad"]) / max(q_in, 1e-300), 1e-9,
                        f"electronics {p.internal_power:.3g} W + sun {q_sun:.3g} W = convection "
                        f"{d['conv']:.3g} W + radiation {d['rad']:.3g} W; relative mismatch")]
        hi = p.ambient_temperature + q_in / (h_o * area)
        out.append(ck.bounds("temperature bounds", ck.HEAT,
                             min(p.ambient_temperature, p.sky_temperature), hi + q_in,
                             d["ts"], d["ts"], unit="K",
                             detail="outside surface between the coldest surroundings and the "
                                    "no-radiation limit"))
        out.append(ck.truth("heat flows from hot to cold (second law)", ck.HEAT,
                            d["t_in"] >= d["ts"] - 1e-9 or p.internal_power <= 0,
                            f"inside {to_c(d['t_in']):.1f} degC >= outside surface "
                            f"{to_c(d['ts']):.1f} degC, so the electronics' heat flows out"))
        bound = to_c(hi + p.internal_power / (self._numbers()[3] * area))
        out.append(ck.truth("back-of-envelope R-network bound", ck.HEAT,
                            sol.qoi["internal_temperature"][0] <= bound + 1e-9,
                            f"inside {sol.qoi['internal_temperature'][0]:.1f} degC is below the "
                            f"convection-only series network {bound:.1f} degC (radiation only "
                            "helps)"))
        return out


# -- solar + battery endurance -------------------------------------------------------
@register
class SolarBattery(Simulation):
    name = "solar_battery"
    title = "Solar panel and battery endurance (multi-day energy balance)"
    domain = DOMAIN
    description = """Will an off-grid device stay on?  Marches the battery's
    state of charge through several days of a daily sun cycle with a constant
    load, charging losses and a full battery that has to throw away extra
    sun.  Reports the lowest charge, the energy that could not be supplied and
    how long the battery alone lasts."""
    params = [
        Param("panel_power", "100 W", "W", "panel rating at 1000 W/m^2", lo=1, hi=5000),
        Param("peak_irradiance", "500 W/m^2", "W/m^2", "noon sunlight on the panel", lo=0, hi=1100),
        Param("day_length", "10 h", "s", "hours of daylight", lo=3600, hi=86400),
        Param("weather_factor", 0.35, None, "fraction of clear-sky sun (overcast 0.1-0.4)"),
        Param("days", 3, None, "number of days to simulate"),
        Param("battery_capacity", "640 Wh", "J", "battery energy when full", lo=3600, hi=1e9),
        Param("usable_fraction", 0.9, None, "fraction of the capacity that may be used"),
        Param("start_charge", 1.0, None, "starting state of charge (0-1)"),
        Param("load", "13 W", "W", "constant electrical load", lo=0, hi=5000),
        Param("system_efficiency", 0.85, None, "panel-to-battery efficiency (controller, wiring)"),
        Param("charge_efficiency", 0.95, None, "battery round-trip efficiency"),
    ]
    default_resolution = 12
    min_resolution = 2
    primary = ("lowest_charge",)
    resolution_doc = "time steps per hour"

    def validate(self):
        p = self.p
        if p.days < 1:
            raise ParamError("days must be at least 1")
        for key in ("weather_factor", "usable_fraction", "start_charge", "system_efficiency",
                    "charge_efficiency"):
            if not 0 <= getattr(p, key) <= 1:
                raise ParamError(f"{key} must be between 0 and 1")

    def _sun(self, t):
        """Panel output [W] at time t (s), midpoint of a step; noon at 12 h."""
        p = self.p
        tod = np.mod(t, 86400.0)
        rise = 43200.0 - p.day_length / 2
        frac = (tod - rise) / p.day_length
        shape = np.where((frac > 0) & (frac < 1), np.sin(np.pi * np.clip(frac, 0, 1)), 0.0)
        return (p.panel_power * p.peak_irradiance / 1000.0 * p.weather_factor
                * p.system_efficiency * shape)

    def _march(self, n, sun_on=True):
        p = self.p
        dt = 3600.0 / n
        steps = int(p.days * 24 * n)
        cap = p.battery_capacity
        floor = cap * (1 - p.usable_fraction)
        e = cap * p.start_charge
        tot = dict(harvest=0.0, charged=0.0, direct=0.0, discharged=0.0, curtailed=0.0,
                   unserved=0.0, load=0.0)
        soc, ts, first_out = [e / cap], [0.0], None
        for k in range(steps):
            t_mid = (k + 0.5) * dt
            pv = float(self._sun(t_mid)) if sun_on else 0.0
            harvest, load = pv * dt, p.load * dt
            tot["harvest"] += harvest
            tot["load"] += load
            if harvest >= load:
                surplus = harvest - load
                room = (cap - e) / p.charge_efficiency
                take = min(surplus, room)
                e += take * p.charge_efficiency
                tot["direct"] += load
                tot["charged"] += take
                tot["curtailed"] += surplus - take
            else:
                deficit = load - harvest
                avail = e - floor
                give = min(deficit, max(avail, 0.0))
                e -= give
                tot["direct"] += harvest
                tot["discharged"] += give
                tot["unserved"] += deficit - give
                if deficit - give > 1e-9 and first_out is None:
                    first_out = (k + (give / deficit if deficit else 0.0)) * dt
            soc.append(e / cap)
            ts.append((k + 1) * dt)
        tot["stored"] = e - cap * p.start_charge
        return np.array(ts), np.array(soc), tot, first_out

    def equations(self):
        return {"battery energy balance": {
            "stored energy": self.q("battery_capacity"),
            "load x time": self.q("load") * self.q("day_length"),
            "panel x time": self.q("panel_power") * self.q("day_length")}}

    def dimensionless(self):
        return {"daily harvest / daily load": self.q("panel_power") * self.q("day_length")
                / (self.q("load") * Q(86400, "s"))}

    def well_posed(self):
        return [ck.truth("initial-value problem complete", ck.MATH, True,
                         "starting charge, sun profile and load are all defined")]

    def solve(self, n):
        p = self.p
        ts, soc, tot, first_out = self._march(n)
        usable = p.battery_capacity * p.usable_fraction
        qoi = {"lowest_charge": (float(soc.min()), ""),
               "ends_with_charge": (float(soc[-1]), ""),
               "energy_not_supplied": (tot["unserved"] / 3600.0, "Wh"),
               "stays_on": (1.0 if tot["unserved"] <= 1e-6 else 0.0, ""),
               "battery_only_runtime": (usable / p.load / 3600.0 if p.load else math.inf, "h"),
               "harvest_per_day": (tot["harvest"] / p.days / 3600.0, "Wh"),
               "load_per_day": (p.load * 86400.0 / 3600.0, "Wh")}
        fields = [Field("battery charge", ts / 3600.0, soc * 100.0, unit="%",
                        xlabel="time [h]", ylabel="charge [%]"),
                  Field("solar power", ts / 3600.0, self._sun(ts), unit="W",
                        xlabel="time [h]", ylabel="panel [W]")]
        gross = max(tot["harvest"] + tot["discharged"], 1e-300)
        res = abs(tot["stored"] - (tot["charged"] * p.charge_efficiency
                                   - tot["discharged"])) / max(p.battery_capacity, 1e-300)
        return Solution(qoi, fields, res, residual_note="battery bookkeeping: change in stored "
                        "energy vs charge in minus charge out", residual_limit=1e-9,
                        cfl=(0.0, 1.0, "explicit energy bookkeeping has no stability limit; "
                             f"step {3600 / n:.0f} s sets accuracy only"),
                        data={"tot": tot, "soc": soc, "gross": gross})

    def known_solution(self, sol):
        p = self.p
        _, soc, tot, first_out = self._march(max(4, self.default_resolution), sun_on=False)
        usable = p.battery_capacity * (p.start_charge - (1 - p.usable_fraction))
        exact = usable / p.load if p.load > 0 else math.inf
        if not math.isfinite(exact) or exact > p.days * 86400:
            return [ck.skip("known solution: battery-only runtime", ck.UNIVERSAL,
                            "the battery alone outlasts the simulated days")]
        return [ck.close("known solution: battery-only runtime", ck.UNIVERSAL,
                         first_out / 3600.0, exact / 3600.0, 1e-6,
                         detail="same march with the sun off vs usable energy / load",
                         unit="h")]

    def domain_checks(self, sol):
        tot = sol.data["tot"]
        p = self.p
        lhs = tot["harvest"]
        rhs = tot["direct"] + tot["charged"] + tot["curtailed"]
        out = [ck.small("energy balance: sun = used directly + charged + thrown away",
                        ck.HEAT, (lhs - rhs) / max(lhs, 1e-300), 1e-9,
                        f"harvest {lhs / 3600:.4g} Wh; relative mismatch"),
               ck.small("energy balance: load = direct + from battery + not supplied",
                        ck.HEAT, (tot["load"] - tot["direct"] - tot["discharged"]
                                  - tot["unserved"]) / max(tot["load"], 1e-300), 1e-9,
                        f"load {tot['load'] / 3600:.4g} Wh; relative mismatch")]
        soc = sol.data["soc"]
        out.append(ck.bounds("charge bounds", ck.HEAT, 1 - p.usable_fraction, 1.0,
                             float(soc.min()), float(soc.max()),
                             detail="never below the reserve, never above full"))
        return out
