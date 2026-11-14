"""Aerospace: CubeSat temperatures around a low-Earth orbit."""
from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np

from .. import checks as ck
from ..core import Field, Param, ParamError, Simulation, Solution, register
from ..solvers.lumped import ThermalNetwork
from ..units import Q
from ._heat import SIGMA, to_c

MU_EARTH = 3.986004418e14       # gravitational parameter, m^3/s^2
R_EARTH = 6371.0e3              # mean radius, m
T_SPACE = 2.7                   # deep-space background, K

# CubeSat Design Specification envelopes [m] and number of units
SIZES = {"1U": (0.100, 0.100, 0.1135, 1), "2U": (0.100, 0.100, 0.2270, 2),
         "3U": (0.100, 0.100, 0.3405, 3), "6U": (0.100, 0.2263, 0.3660, 6)}


def _trap_mean(t, y) -> float:
    """Time average by the trapezoidal rule (the rule the solver conserves)."""
    return float(np.sum(np.diff(t) * 0.5 * (y[1:] + y[:-1])) / (t[-1] - t[0]))


def _extreme(y, marks, kind) -> float:
    """Extremum of a sampled curve, refined with a 3-point parabola unless it
    sits on a kink (eclipse entry/exit), where the sample itself is exact."""
    k = int(np.argmax(y) if kind == "max" else np.argmin(y))
    if k in marks or k == 0 or k == len(y) - 1:
        return float(y[k])
    a, b, c = y[k - 1], y[k], y[k + 1]
    den = a - 2 * b + c
    return float(b) if den == 0 else float(b - 0.125 * (a - c) ** 2 / den)


@register
class CubeSatThermal(Simulation):
    name = "cubesat_thermal"
    title = "CubeSat orbital temperatures (two-node thermal network)"
    domain = "aerospace"
    description = """A CubeSat in a circular low-Earth orbit as two thermal
    nodes: the outer structure (absorbs sunlight, albedo and Earth infrared,
    radiates to deep space) and the internal electronics/battery (dissipates
    electrical power, coupled to the structure by a conductance).  The orbit is
    marched to a periodic state, giving the hot and cold extremes each part
    sees between sunlight and eclipse.  Internal dissipation is electrical
    power harvested from the absorbed sunlight (battery buffered), so energy is
    counted once."""
    params = [
        Param("size", "1U", None, "CubeSat form factor", choices=list(SIZES)),
        Param("altitude", "500 km", "km", "circular orbit altitude", lo=200, hi=2000),
        Param("beta_angle", "0 deg", "deg",
              "angle between the orbit plane and the Sun direction (0 = longest eclipse)",
              lo=0, hi=89),
        Param("absorptivity", 0.7, "", "solar absorptivity of the outer surface",
              lo=0.05, hi=1.0),
        Param("emissivity", 0.8, "", "infrared emissivity of the outer surface",
              lo=0.02, hi=1.0),
        Param("internal_power", "2 W", "W",
              "orbit-average electrical power dissipated inside", lo=0, hi=40),
        Param("mass_per_unit", "1.3 kg", "kg", "mass per U (total = this x number of units)",
              lo=0.3, hi=2.0),
        Param("internal_mass_fraction", 0.4, "",
              "share of the mass in the internal node", lo=0.05, hi=0.95),
        Param("specific_heat", "900 J/(kg*K)", "J/(kg*K)",
              "average specific heat (aluminium 900)", lo=300, hi=1500),
        Param("coupling_conductance", "0.5 W/K", "W/K",
              "thermal conductance between internals and structure", lo=0.02, hi=10),
        Param("attitude", "tumbling", None, "which area faces the Sun",
              choices=["tumbling", "largest_face_to_sun", "smallest_face_to_sun"]),
        Param("solar_constant", "1361 W/m^2", "W/m^2", "solar irradiance", lo=1300, hi=1420),
        Param("albedo", 0.3, "", "Earth albedo (reflected sunlight fraction)", lo=0, hi=0.6),
        Param("earth_ir", "237 W/m^2", "W/m^2", "Earth infrared emission", lo=150, hi=300),
    ]
    default_resolution = 256
    min_resolution = 4
    primary = ("structure_max", "structure_min", "internal_max", "internal_min")
    resolution_doc = "time steps per orbit"

    # -- derived quantities ---------------------------------------------------
    def validate(self):
        p = self.p
        if p.altitude < 100:
            raise ParamError(
                f"altitude {p.altitude:g} km is below 100 km: no stable orbit there "
                "(low Earth orbit is roughly 200-2000 km)")
        if not 0 <= p.beta_angle < 90:
            raise ParamError("beta_angle must be in [0, 90) degrees")
        a, b, c, units = SIZES[p.size]
        faces = sorted((a * b, b * c, c * a))
        area = 2 * sum(faces)
        proj = {"tumbling": area / 4, "largest_face_to_sun": faces[-1],
                "smallest_face_to_sun": faces[0]}[p.attitude]
        r = R_EARTH + p.altitude * 1e3
        beta = math.radians(p.beta_angle)
        horizon = math.sqrt(1 - (R_EARTH / r) ** 2)
        x = horizon / math.cos(beta)
        theta_e = math.acos(x) if x < 1 else 0.0        # eclipse half-angle
        view = 0.5 * (1 - horizon)                      # tumbling body -> Earth
        mass = p.mass_per_unit * units
        s = self.s = SimpleNamespace(
            area=area, proj=proj, radius=r, beta=beta, theta_e=theta_e,
            period=2 * math.pi * math.sqrt(r ** 3 / MU_EARTH),
            eclipse_fraction=theta_e / math.pi, view=view,
            q_solar=p.absorptivity * p.solar_constant * proj,
            q_albedo=p.absorptivity * p.albedo * p.solar_constant * area * view
            * math.cos(beta),
            q_ir=p.emissivity * p.earth_ir * area * view,
            c_struct=(1 - p.internal_mass_fraction) * mass * p.specific_heat,
            c_int=p.internal_mass_fraction * mass * p.specific_heat,
            eps_area=p.emissivity * area)
        s.omega = 2 * math.pi / s.period
        s.harvest = p.internal_power / (1 - s.eclipse_fraction)   # W while sunlit
        # orbit-average heat reaching the structure node, and its extremes
        s.q_avg = ((s.q_solar - s.harvest) * (1 - s.eclipse_fraction)
                   + s.q_albedo / math.pi + s.q_ir)
        s.q_hot = s.q_solar - s.harvest + s.q_albedo + s.q_ir
        s.q_cold = s.q_ir

    def _network(self):
        p, s = self.p, self.s
        net = ThermalNetwork()
        net.node("structure", s.c_struct)
        net.node("internals", s.c_int)
        net.node("deep space", fixed=T_SPACE)
        net.conductance(1, 0, p.coupling_conductance)
        net.radiation(0, 2, s.eps_area)
        return net

    # exact integrals of the on/off and cosine-shaped loads over a time step
    def _sunlit(self, th) -> float:
        te = self.s.theta_e
        k, phi = divmod(th, 2 * math.pi)
        part = phi if phi <= math.pi - te else (
            math.pi - te if phi <= math.pi + te else phi - 2 * te)
        return 2 * (math.pi - te) * k + part

    @staticmethod
    def _dayside(th) -> float:
        k, phi = divmod(th, 2 * math.pi)
        part = math.sin(phi) if phi <= math.pi / 2 else (
            1.0 if phi <= 1.5 * math.pi else 2.0 + math.sin(phi))
        return 2 * k + part

    def _q_avg(self, t0, t1):
        s = self.s
        a0, a1 = s.omega * t0, s.omega * t1
        sun = (self._sunlit(a1) - self._sunlit(a0)) / (a1 - a0)
        alb = (self._dayside(a1) - self._dayside(a0)) / (a1 - a0)
        return np.array([(s.q_solar - s.harvest) * sun + s.q_albedo * alb + s.q_ir,
                         self.p.internal_power, 0.0])

    def _times(self, n):
        """Step boundaries placed exactly on eclipse entry and exit."""
        s = self.s
        edges = ([0.0, math.pi - s.theta_e, math.pi + s.theta_e, 2 * math.pi]
                 if s.theta_e > 0 else [0.0, 2 * math.pi])
        frac = np.diff(edges) / (2 * math.pi)
        base = np.maximum(1, np.rint(16 * frac)).astype(int)
        cells = np.maximum(2, np.rint(base * n / 16)).astype(int)
        ang, marks = [0.0], {0}
        for a0, a1, c in zip(edges[:-1], edges[1:], cells):
            ang.extend(a0 + (a1 - a0) * np.arange(1, c + 1) / c)
            marks.add(len(ang) - 1)
        return np.array(ang) / s.omega, marks

    def _closed_form(self, q_struct):
        """Steady single-radiator temperatures for a constant heat load."""
        p, s = self.p, self.s
        ts = ((q_struct + p.internal_power) / (SIGMA * s.eps_area) + T_SPACE ** 4) ** 0.25
        return ts, ts + p.internal_power / p.coupling_conductance

    # -- self-description -----------------------------------------------------
    def equations(self):
        s = self.s
        sigma, area, dT = Q(SIGMA, "W/(m^2*K^4)"), Q(s.area, "m^2"), Q(1, "K")
        return {
            "node energy balance": {
                "storage m*c*dT/dt": self.q("mass_per_unit") * self.q("specific_heat")
                * dT / Q(1, "s"),
                "absorbed alpha*S*A": self.q("absorptivity") * self.q("solar_constant") * area,
                "Earth IR eps*q*A": self.q("emissivity") * self.q("earth_ir") * area,
                "radiated eps*sigma*A*T^4": self.q("emissivity") * sigma * area * dT ** 4,
                "coupling G*dT": self.q("coupling_conductance") * dT,
                "internal dissipation P": self.q("internal_power")},
            "Kepler period": {
                "T": Q(1, "s"),
                "sqrt(a^3/mu)": ((Q(R_EARTH, "m") + self.q("altitude")) ** 3
                                 / Q(MU_EARTH, "m^3/s^2")).sqrt()}}

    def dimensionless(self):
        s = self.s
        out = {"eclipse fraction": Q(s.eclipse_fraction, ""),
               "orbital speed^2 * a / mu": (Q(s.radius, "m") * Q(s.omega, "1/s")) ** 2
               * Q(s.radius, "m") / Q(MU_EARTH, "m^3/s^2")}
        if self.p.emissivity > 0 and s.q_avg + self.p.internal_power > 0:
            t_eq = self._closed_form(s.q_avg)[0]
            tau = (Q(s.c_struct + s.c_int, "J/K")
                   / (4 * Q(SIGMA, "W/(m^2*K^4)") * Q(s.eps_area, "m^2") * Q(t_eq, "K") ** 3))
            out["thermal time constant / orbit period"] = tau / Q(s.period, "s")
        return out

    def well_posed(self):
        p, s = self.p, self.s
        loads = s.q_solar + s.q_albedo + s.q_ir + p.internal_power
        out = [ck.truth(
            "heat can leave the spacecraft", ck.MATH, p.emissivity > 0 or loads == 0,
            "radiation to space balances the absorbed power" if p.emissivity > 0 else
            f"ill-posed: emissivity is zero, so the {loads:.3g} W absorbed can never be "
            "radiated away and no equilibrium exists")]
        ok = (p.mass_per_unit > 0 and p.specific_heat > 0
              and 0 < p.internal_mass_fraction < 1)
        out.append(ck.truth("heat capacities positive", ck.MATH, ok,
                            f"structure {s.c_struct:.4g} J/K, internals {s.c_int:.4g} J/K"
                            if ok else "both nodes need a positive heat capacity"))
        out.append(ck.truth(
            "nodes thermally connected", ck.MATH, p.coupling_conductance > 0,
            "internals reach the radiating structure through the coupling conductance"
            if p.coupling_conductance > 0 else
            "ill-posed: zero coupling leaves the internal node with no heat path"))
        ok = 0 <= p.absorptivity <= 1 and 0 <= p.emissivity <= 1 and 0 <= p.albedo <= 1
        out.append(ck.truth("optical properties physical", ck.MATH, ok,
                            "absorptivity, emissivity and albedo lie in [0, 1]"))
        ok = 0 <= s.harvest <= s.q_solar * (1 + 1e-12) and p.internal_power >= 0
        out.append(ck.truth(
            "internal power can be harvested (first law)", ck.MATH, ok,
            f"needs {s.harvest:.3g} W of the {s.q_solar:.3g} W absorbed while sunlit"))
        return out

    # -- solve ------------------------------------------------------------------
    def solve(self, n):
        p, s = self.p, self.s
        net = self._network()
        times, marks = self._times(n)
        start = np.array(self._closed_form(s.q_avg))
        march, res = None, math.inf
        for it in range(40):                # Newton shooting for the periodic orbit
            march = net.march(start, times, self._q_avg, theta=0.5, sensitivity=True)
            gap = march.T[-1, :2] - start
            res = float(np.max(np.abs(gap)) / np.max(start))
            if res < 1e-12:
                break
            step = np.linalg.solve(march.sensitivity - np.eye(2), -gap)
            start = np.maximum(start + step, 1.0)
        t, ts, ti = march.t, march.T[:, 0], march.T[:, 1]
        dt_max = float(np.max(np.diff(t)))
        rate = float(net.rates(march.T[int(np.argmax(ts))])[-1])
        qoi = {
            "structure_max": (to_c(_extreme(ts, marks, "max")), "degC"),
            "structure_min": (to_c(_extreme(ts, marks, "min")), "degC"),
            "internal_max": (to_c(_extreme(ti, marks, "max")), "degC"),
            "internal_min": (to_c(_extreme(ti, marks, "min")), "degC"),
            "structure_orbit_average": (to_c(_trap_mean(t, ts)), "degC"),
            "internal_orbit_average": (to_c(_trap_mean(t, ti)), "degC"),
            "eclipse_fraction": (s.eclipse_fraction, ""),
            "orbit_period": (s.period / 60, "min"),
            "average_absorbed_power": (s.q_avg + p.internal_power, "W"),
        }
        mid = 0.5 * (t[1:] + t[:-1])
        load = np.array([self._q_avg(a, b)[0] for a, b in zip(t[:-1], t[1:])])
        fields = [
            Field("temperature over one orbit", t / 60,
                  {"structure": ts - 273.15, "internals": ti - 273.15}, unit="degC",
                  xlabel="time since orbit noon [min]", ylabel="T [degC]"),
            Field("heat absorbed by the structure", mid / 60, load, unit="W",
                  xlabel="time since orbit noon [min]", ylabel="Q [W]")]
        return Solution(
            qoi, fields, res,
            "periodicity: relative temperature mismatch after one more orbit", 1e-9,
            cfl=(dt_max * rate, 2.0,
                 "trapezoidal rule (unconditionally stable): largest step x fastest "
                 "thermal rate, monotone below 2"),
            data={"march": march, "net": net, "load": load})

    # -- checks -------------------------------------------------------------------
    def _cases(self):
        """Hot (permanent sun at orbit noon) and cold (permanent eclipse) steady
        states from the network solver."""
        net, s, p = self._network(), self.s, self.p
        out = {}
        for label, q in (("hot", s.q_hot), ("cold", s.q_cold)):
            out[label] = net.steady(np.array([q, p.internal_power, 0.0]),
                                    guess=self._closed_form(s.q_avg)[0])
        return out

    def known_solution(self, sol):
        p, s = self.p, self.s
        m = sol.data["march"]
        ts = m.T[:, 0]
        mean4 = _trap_mean(m.t, ts ** 4) ** 0.25
        out = [ck.close(
            "known solution: fourth-power mean temperature", ck.UNIVERSAL, mean4,
            self._closed_form(s.q_avg)[0], 1e-7,
            detail="over a periodic orbit storage cancels, so eps*sigma*A*<T^4> must "
                   "equal the orbit-averaged absorbed power", unit="K")]
        for label, q in (("hot", s.q_hot), ("cold", s.q_cold)):
            st = self._cases()[label]
            exact = self._closed_form(q)
            err = max(abs(st.T[0] - exact[0]), abs(st.T[1] - exact[1])) / exact[0]
            out.append(ck.small(
                f"known solution: steady {label} case", ck.UNIVERSAL,
                err if st.converged else math.inf, 1e-9,
                f"network solver vs closed form T = ((Q + P)/(eps*sigma*A))^(1/4) = "
                f"{to_c(exact[0]):.2f} degC; relative error"))
        return out

    def domain_checks(self, sol):
        p, s = self.p, self.s
        m, net = sol.data["march"], sol.data["net"]
        t, ts, ti = m.t, m.T[:, 0], m.T[:, 1]
        dt = np.diff(t)
        out = [ck.small(
            "energy balance over the orbit (absorbed = emitted + stored)", ck.HEAT,
            (m.absorbed - m.emitted - m.stored) / max(abs(m.absorbed), 1e-300), 1e-8,
            f"absorbed {m.absorbed:.6g} J, radiated to space {m.emitted:.6g} J, "
            f"stored {m.stored:.3g} J; relative mismatch")]

        cases = self._cases()
        for k, label, y in ((0, "structure", ts), (1, "internals", ti)):
            c = ck.bounds(f"temperature bounds: {label}", ck.HEAT,
                          cases["cold"].T[k], cases["hot"].T[k],
                          float(y.min()), float(y.max()), unit="K",
                          detail="between permanent-eclipse and permanent-sun steady states")
            out.append(c)

        # second law, two independent routes
        qc = p.coupling_conductance * (ti - ts)             # internals -> structure
        ok = bool(np.all(qc * (ti - ts) >= 0))
        qrad = SIGMA * s.eps_area * (ts ** 4 - T_SPACE ** 4)
        ts_mid, ti_mid = 0.5 * (ts[1:] + ts[:-1]), 0.5 * (ti[1:] + ti[:-1])
        s_boundary = float(
            np.sum(dt * 0.5 * (qrad[1:] + qrad[:-1]) / ts_mid)      # leaves at T_s
            - np.sum(dt * sol.data["load"] / ts_mid)                # enters at T_s
            - np.sum(dt * p.internal_power / ti_mid)                # enters at T_i
            + s.c_struct * math.log(ts[-1] / ts[0]) + s.c_int * math.log(ti[-1] / ti[0]))
        irr = p.coupling_conductance * (ti - ts) ** 2 / (ti * ts)
        s_link = float(np.sum(dt * 0.5 * (irr[1:] + irr[:-1])))
        floor = 1e-9 * abs(m.absorbed) / ts.mean()
        agree = abs(s_boundary - s_link) <= 0.02 * s_link + floor
        status = (ck.FAIL if (s_boundary < -floor or not ok)
                  else ck.PASS if agree else ck.WARN)
        out.append(ck.Check(
            "entropy generation >= 0 (second law)", ck.HEAT, status,
            f"entropy produced per orbit {s_boundary:.4g} J/K from the boundary balance "
            f"(heat out/T - heat in/T) vs {s_link:.4g} J/K from the internal link "
            f"G*dT^2/(T1*T2)" + ("" if agree else " - the two routes differ by more "
                                 "than 2%: use more time steps per orbit")
            + "; heat always flows from the hotter to the colder node",
            s_boundary, s_link, 0.02))

        mean_gap = _trap_mean(t, ti) - _trap_mean(t, ts)
        out.append(ck.close(
            "back-of-envelope R-network: internals above structure by P/G", ck.HEAT,
            mean_gap, p.internal_power / p.coupling_conductance, 1e-6, 1e-7,
            detail="orbit-mean temperature drop across the coupling resistance",
            unit="K"))
        return out
