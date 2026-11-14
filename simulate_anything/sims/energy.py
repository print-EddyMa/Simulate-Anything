"""Energy systems: operating temperature and output of a solar panel."""
from __future__ import annotations

import math

import numpy as np

from .. import checks as ck
from ..core import Field, Param, Simulation, Solution, register
from ..solvers.lumped import ThermalNetwork
from ..units import Q
from ._heat import SIGMA, to_c

T_REF = 298.15          # standard test conditions cell temperature, K
G_NOCT, T_AMB_NOCT, H_NOCT = 800.0, 293.15, 9.5     # NOCT test conditions


@register
class SolarPanel(Simulation):
    name = "solar_panel"
    title = "Solar panel operating temperature and power (energy balance)"
    domain = "climate & energy"
    description = """Steady energy balance of a photovoltaic module: absorbed
    sunlight minus electrical output (efficiency falls as the cell warms)
    equals convection from front and back (wind correlation h = 5.7 + 3.8 v)
    plus long-wave radiation to the sky and the ground.  Solved by Newton
    iteration on a one-node thermal network; reports cell temperature, power
    and where the waste heat goes."""
    params = [
        Param("irradiance", "800 W/m^2", "W/m^2", "sunlight in the plane of the module",
              lo=0, hi=1300),
        Param("ambient_temperature", "20 degC", "K", "air temperature"),
        Param("sky_temperature", "5 degC", "K",
              "effective sky temperature for long-wave radiation "
              "(clear sky is 10-25 K below air temperature)"),
        Param("wind_speed", "1 m/s", "m/s", "wind over the module", lo=0, hi=20),
        Param("area", "1.7 m^2", "m^2", "module area", lo=0.1, hi=3.0),
        Param("absorptivity", 0.9, "", "fraction of sunlight absorbed (tau-alpha product)",
              lo=0.5, hi=1.0),
        Param("efficiency_stc", 0.20, "", "electrical efficiency at 25 degC cell temperature",
              lo=0.05, hi=0.30),
        Param("temperature_coefficient", "-0.0035 1/K", "1/K",
              "relative change of efficiency per kelvin", lo=-0.006, hi=0.0),
        Param("emissivity_front", 0.90, "", "long-wave emissivity of the glass", lo=0, hi=1),
        Param("emissivity_back", 0.85, "", "long-wave emissivity of the backsheet",
              lo=0, hi=1),
        Param("noct", "45 degC", "K",
              "datasheet nominal operating cell temperature (for the sanity estimate)"),
        Param("mounting", "open_rack", None, "open rack cools both faces; an insulated "
              "back (roof-integrated) only the front",
              choices=["open_rack", "insulated_back"]),
        Param("radiation", True, None,
              "include long-wave radiation (switch off for the textbook linear case)"),
    ]
    default_resolution = 16
    primary = ()
    no_mesh_reason = "zero-dimensional energy balance: there is no grid to refine"
    resolution_doc = "unused (zero-dimensional balance)"

    # -- model ------------------------------------------------------------------
    def _h(self) -> float:
        return 5.7 + 3.8 * self.p.wind_speed

    def _network(self, radiation=None):
        """Module node linked to air (front, back), sky and ground."""
        p = self.p
        radiation = p.radiation if radiation is None else radiation
        back = 1.0 if p.mounting == "open_rack" else 0.0
        net = ThermalNetwork()
        net.node("module", 1.0)
        net.node("air", fixed=p.ambient_temperature)
        net.node("sky", fixed=p.sky_temperature)
        net.node("ground", fixed=p.ambient_temperature)
        net.conductance(0, 1, self._h() * p.area)                 # front convection
        net.conductance(0, 1, back * self._h() * p.area)          # back convection
        net.radiation(0, 2, (p.emissivity_front * p.area) if radiation else 0.0)
        net.radiation(0, 3, (back * p.emissivity_back * p.area) if radiation else 0.0)
        return net

    def _efficiency(self, T) -> float:
        p = self.p
        return max(p.efficiency_stc * (1 + p.temperature_coefficient * (T - T_REF)), 0.0)

    def _balance(self, irradiance=None, electrical=True, radiation=None):
        """Solve the balance; returns (network, steady result, electrical W)."""
        p = self.p
        G = p.irradiance if irradiance is None else irradiance
        net = self._network(radiation)

        def heating(on):
            def inputs(T):
                eta = p.efficiency_stc * (1 + p.temperature_coefficient * (T[0] - T_REF))
                Qv, dQ = np.zeros(4), np.zeros(4)
                Qv[0] = (p.absorptivity - (eta if on else 0.0)) * G * p.area
                dQ[0] = -(p.efficiency_stc * p.temperature_coefficient * G * p.area
                          if on else 0.0)
                return Qv, dQ
            return inputs

        st = net.steady(heating(electrical), guess=p.ambient_temperature + 20.0)
        if electrical and p.efficiency_stc * (
                1 + p.temperature_coefficient * (st.T[0] - T_REF)) < 0:
            st = net.steady(heating(False), guess=st.T[0])    # too hot to generate
        power = (self._efficiency(st.T[0]) * G * p.area) if electrical else 0.0
        return net, st, power

    # -- self-description ---------------------------------------------------------
    def equations(self):
        dT, area = Q(1, "K"), self.q("area")
        return {
            "module energy balance": {
                "absorbed alpha*G*A": self.q("absorptivity") * self.q("irradiance") * area,
                "electrical eta*G*A": self.q("efficiency_stc") * self.q("irradiance") * area,
                "convection h*A*dT": Q(self._h(), "W/(m^2*K)") * area * dT,
                "radiation eps*sigma*A*T^4": self.q("emissivity_front")
                * Q(SIGMA, "W/(m^2*K^4)") * area * dT ** 4},
            "wind convection correlation h = 5.7 + 3.8 v": {
                "h": Q(1, "W/(m^2*K)"),
                "5.7 W/(m^2 K)": Q(5.7, "W/(m^2*K)"),
                "3.8 W s/(m^3 K) * v": Q(3.8, "W*s/(m^3*K)") * self.q("wind_speed")}}

    def dimensionless(self):
        t_amb = self.q("ambient_temperature")
        return {
            "temperature coefficient x 1 K": self.q("temperature_coefficient") * Q(1, "K"),
            "radiative / convective conductance 4*eps*sigma*T^3/h":
                4 * self.q("emissivity_front") * Q(SIGMA, "W/(m^2*K^4)") * t_amb ** 3
                / Q(self._h(), "W/(m^2*K)")}

    def well_posed(self):
        p = self.p
        ok = p.ambient_temperature > 0 and p.sky_temperature > 0 and p.area > 0
        out = [ck.truth("inputs physical", ck.MATH,
                        ok and p.irradiance >= 0 and p.wind_speed >= 0
                        and 0 <= p.emissivity_front <= 1 and 0 <= p.emissivity_back <= 1,
                        "non-negative irradiance and wind, absolute temperatures, "
                        "emissivities in [0, 1]")]
        out.append(ck.truth(
            "first law: electrical output cannot exceed absorbed sunlight", ck.MATH,
            0 <= p.efficiency_stc <= p.absorptivity <= 1,
            f"efficiency {p.efficiency_stc:.3g} vs absorbed fraction {p.absorptivity:.3g}"))
        back = 1.0 if p.mounting == "open_rack" else 0.0
        slope = (1 + back) * self._h() + p.efficiency_stc * p.temperature_coefficient \
            * p.irradiance
        out.append(ck.truth(
            "unique equilibrium", ck.MATH, slope > 0,
            f"losses grow with temperature faster than the heating does "
            f"(net {slope:.3g} W/(m^2 K) before radiation)" if slope > 0 else
            "ill-posed: heating rises with temperature faster than the losses "
            "(thermal runaway) - check the temperature coefficient"))
        return out

    # -- solve ----------------------------------------------------------------------
    def _flows(self, net, T):
        qc, qr = net.link_flows(T)
        return {"front convection": qc[0], "back convection": qc[1],
                "sky radiation": qr[0], "ground radiation": qr[1]}

    def solve(self, n):
        p = self.p
        net, st, power = self._balance()
        T = st.T[0]
        flows = self._flows(net, st.T)
        _, stag, _ = self._balance(electrical=False)
        absorbed = p.absorptivity * p.irradiance * p.area
        qoi = {
            "cell_temperature": (to_c(T), "degC"),
            "electrical_power": (power, "W"),
            "operating_efficiency": (self._efficiency(T), ""),
            "heat_absorbed": (absorbed, "W"),
            "convective_loss": (flows["front convection"] + flows["back convection"], "W"),
            "radiative_loss": (flows["sky radiation"] + flows["ground radiation"], "W"),
            "stagnation_temperature": (to_c(stag.T[0]), "degC"),
            "convection_coefficient": (self._h(), "W/(m^2*K)"),
        }
        sweep = np.linspace(0.0, 1200.0, 25)
        temps, powers = [], []
        for g in sweep:
            _, s_g, p_g = self._balance(irradiance=float(g))
            temps.append(s_g.T[0] - 273.15)
            powers.append(p_g)
        fields = [
            Field("cell temperature vs irradiance", sweep, np.array(temps), unit="degC",
                  xlabel="irradiance [W/m^2]", ylabel="cell temperature [degC]"),
            Field("electrical power vs irradiance", sweep, np.array(powers), unit="W",
                  xlabel="irradiance [W/m^2]", ylabel="power [W]")]
        ok = st.converged and stag.converged
        return Solution(qoi, fields, st.residual if ok else math.inf,
                        "Newton iteration: energy imbalance / heat throughput", 1e-10,
                        data={"net": net, "T": st.T, "flows": flows, "power": power,
                              "absorbed": absorbed, "stagnation": stag.T[0]})

    # -- checks -----------------------------------------------------------------------
    def known_solution(self, sol):
        """Without radiation the balance is linear in T and has a closed form;
        the same solver must reproduce it."""
        p = self.p
        _, st, _ = self._balance(radiation=False)
        back = 1.0 if p.mounting == "open_rack" else 0.0
        H = (1 + back) * self._h()
        eta_amb = p.efficiency_stc * (1 + p.temperature_coefficient
                                      * (p.ambient_temperature - T_REF))
        slope = H + p.efficiency_stc * p.temperature_coefficient * p.irradiance
        exact = p.ambient_temperature + p.irradiance * (p.absorptivity - eta_amb) / slope
        if p.efficiency_stc * (1 + p.temperature_coefficient * (exact - T_REF)) < 0:
            exact = p.ambient_temperature + p.irradiance * p.absorptivity / H
        tag = "" if not p.radiation else " (radiation switched off)"
        return [ck.close(
            "known solution: linear convection-only balance", ck.UNIVERSAL,
            st.T[0] - p.ambient_temperature, exact - p.ambient_temperature, 1e-9, 1e-9,
            detail="same Newton solver vs closed form dT = G (alpha - eta) / "
                   f"(h_total + eta*gamma*G){tag}", unit="K")]

    def domain_checks(self, sol):
        p, d = self.p, sol.data
        T, flows = d["T"][0], d["flows"]
        lost = sum(flows.values())
        scale = max(d["absorbed"], sum(abs(v) for v in flows.values()), 1e-300)
        detail = ", ".join(f"{k} {v:.4g}" for k, v in flows.items() if v)
        out = [ck.small(
            "energy balance (absorbed = electrical + heat rejected)", ck.HEAT,
            (d["absorbed"] - d["power"] - lost) / scale, 1e-9,
            f"absorbed {d['absorbed']:.5g} W = electrical {d['power']:.5g} W + "
            f"[{detail}] W; relative imbalance")]

        lo = min(p.ambient_temperature, p.sky_temperature) if p.radiation \
            else p.ambient_temperature
        out.append(ck.bounds(
            "temperature bounds", ck.HEAT, lo, d["stagnation"], T, T, unit="K",
            detail="no colder than the coldest surroundings, no hotter than the "
                   "stagnation temperature with zero electrical output"))

        env = {"front convection": p.ambient_temperature,
               "back convection": p.ambient_temperature,
               "sky radiation": p.sky_temperature,
               "ground radiation": p.ambient_temperature}
        s_gen = sum(q * (1.0 / env[k] - 1.0 / T) for k, q in flows.items())
        downhill = all(q * (T - env[k]) >= 0 for k, q in flows.items())
        out.append(ck.Check(
            "entropy generation >= 0 (second law)", ck.HEAT,
            ck.PASS if (s_gen >= 0 and downhill) else ck.FAIL,
            f"entropy production of the heat rejection {s_gen:.4g} W/K; every flow "
            f"runs from hot to cold", s_gen, 0.0))

        name = "back-of-envelope: NOCT cell-temperature estimate"
        if p.mounting != "open_rack":
            out.append(ck.skip(name, ck.HEAT, "the NOCT rule assumes an open-rack module"))
        elif p.irradiance < 50:
            out.append(ck.skip(name, ck.HEAT, "irradiance too low for the NOCT rule"))
        else:
            eta = sol.qoi["operating_efficiency"][0]
            est = (p.irradiance / G_NOCT) * (p.noct - T_AMB_NOCT) \
                * (H_NOCT / self._h()) * (1 - eta / p.absorptivity)
            rise = T - p.ambient_temperature
            off = abs(rise - est) / est
            out.append(ck.Check(
                name, ck.HEAT, ck.PASS if off <= 0.30 else ck.WARN,
                f"cell is {rise:.3g} K above ambient; the empirical NOCT rule "
                f"(G/800)(NOCT-20)(9.5/h)(1-eta/alpha) gives {est:.3g} K "
                f"(off by {100 * off:.3g}%, rule of thumb good to ~30%)",
                rise, est, 0.30))
        return out
