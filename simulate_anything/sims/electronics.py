"""Electronics cooling: heat-sink fins, PCB hot spots, battery packs."""
from __future__ import annotations

import math

import numpy as np

from .. import checks as ck
from ..core import Field, Param, ParamError, Simulation, Solution, register
from ..solvers.transport import (Grid, TransportProblem, dirichlet, insulated,
                                 operator_benchmark, robin, transient_benchmark)
from ..units import Q, convert
from ._heat import heat_checks, to_c


@register
class HeatSinkFin(Simulation):
    name = "heat_sink_fin"
    title = "Heat-sink fin (1-D conduction with convection)"
    domain = "electronics"
    description = """Straight rectangular fins on a hot base.  Solves the fin
    equation k A T'' - h P (T - T_inf) = 0 numerically and reports heat removed,
    tip temperature, fin efficiency and sink thermal resistance.  Exact
    solution known, so this is also a good first check of any setup."""
    params = [
        Param("base_temperature", "80 degC", "K", "temperature of the fin base"),
        Param("ambient_temperature", "25 degC", "K", "air temperature"),
        Param("fin_length", "30 mm", "m", "fin height above the base", lo=0.002, hi=0.2),
        Param("fin_thickness", "1.5 mm", "m", "fin thickness", lo=0.0002, hi=0.01),
        Param("fin_width", "50 mm", "m", "fin depth along the flow", lo=0.005, hi=0.5),
        Param("n_fins", 10, None, "number of fins"),
        Param("conductivity", "205 W/(m*K)", "W/(m*K)", "fin material (aluminium 205, copper 390)",
              lo=1, hi=400),
        Param("h", "25 W/(m^2*K)", "W/(m^2*K)",
              "convection coefficient (natural 5-15, forced air 25-100)", lo=1, hi=500),
        Param("tip", "convective", None, "fin tip condition",
              choices=["convective", "adiabatic"]),
    ]
    default_resolution = 64
    primary = ("heat_per_fin", "tip_temperature")

    def validate(self):
        if self.p.n_fins < 1:
            raise ParamError("n_fins must be at least 1")

    def _geom(self):
        p = self.p
        area = p.fin_thickness * p.fin_width
        perim = 2 * (p.fin_thickness + p.fin_width)
        return area, perim

    def _problem(self, n):
        p = self.p
        area, perim = self._geom()
        g = Grid(n, p.fin_length, ly=area)
        hv = p.h * perim / area
        east = robin(p.h, p.ambient_temperature) if p.tip == "convective" else insulated()
        return TransportProblem(
            g, p.conductivity, sink=hv, source=hv * p.ambient_temperature,
            bcs={"west": dirichlet(p.base_temperature), "east": east}), hv

    def equations(self):
        area, perim = self._geom()
        dT = Q(1, "K")
        return {"fin equation": {
            "conduction k*A*dT/L^2": self.q("conductivity") * Q(area, "m^2") * dT
            / self.q("fin_length") ** 2,
            "convection h*P*dT": self.q("h") * Q(perim, "m") * dT}}

    def dimensionless(self):
        area, perim = self._geom()
        m = (self.q("h") * Q(perim, "m") / (self.q("conductivity") * Q(area, "m^2"))).sqrt()
        return {"Biot number h*t/(2k)": self.q("h") * self.q("fin_thickness")
                / (2 * self.q("conductivity")),
                "fin parameter m*L": m * self.q("fin_length")}

    def well_posed(self):
        out = self._problem(8)[0].well_posed(what="temperature")
        bi = self.p.h * self.p.fin_thickness / (2 * self.p.conductivity)
        out.append(ck.truth("model validity: thin-fin assumption", ck.MATH, bi < 0.1,
                            f"Biot number {bi:.3g} (1-D fin theory needs Bi < 0.1)",
                            warn=True))
        return out

    def _exact(self):
        p = self.p
        area, perim = self._geom()
        m = math.sqrt(p.h * perim / (p.conductivity * area))
        big = math.sqrt(p.h * perim * p.conductivity * area)
        theta_b = p.base_temperature - p.ambient_temperature
        ml = m * p.fin_length
        r = p.h / (m * p.conductivity) if p.tip == "convective" else 0.0
        q = big * theta_b * (math.sinh(ml) + r * math.cosh(ml)) / (
            math.cosh(ml) + r * math.sinh(ml))
        tip = p.ambient_temperature + theta_b / (math.cosh(ml) + r * math.sinh(ml))
        return q, tip

    def solve(self, n):
        p = self.p
        prob, hv = self._problem(n)
        ts = prob.solve_steady()
        g = ts.grid
        area, perim = self._geom()
        q_fin = ts.boundary_inflow()["west"]
        T = ts.phi[:, 0]
        if p.tip == "convective":       # surface value from the film/half-cell divider
            u_half = p.conductivity / (g.dxs[-1] / 2)
            tip = (u_half * T[-1] + p.h * p.ambient_temperature) / (u_half + p.h)
        else:
            tip = T[-1] + (T[-1] - T[-2]) * 0.5 * 0.5   # zero-gradient extrapolation
        theta_b = p.base_temperature - p.ambient_temperature
        surf = perim * p.fin_length + (area if p.tip == "convective" else 0.0)
        qoi = {
            "heat_per_fin": (q_fin, "W"),
            "total_heat": (q_fin * p.n_fins, "W"),
            "tip_temperature": (to_c(tip), "degC"),
            "fin_efficiency": (q_fin / (p.h * surf * theta_b) if theta_b else 1.0, ""),
            "thermal_resistance": (theta_b / (q_fin * p.n_fins) if q_fin else math.inf, "K/W"),
        }
        fields = [Field("temperature along fin", g.x * 1e3, T - 273.15, unit="degC",
                        xlabel="distance from base [mm]", ylabel="T [degC]")]
        return Solution(qoi, fields, ts.residual, data={"ts": ts, "hv": hv})

    def known_solution(self, sol):
        q, tip = self._exact()
        return [ck.close("known solution: fin heat rate", ck.UNIVERSAL,
                         sol.qoi["heat_per_fin"][0], q, 5e-3,
                         detail="exact fin-equation solution", unit="W"),
                ck.close("known solution: tip temperature", ck.UNIVERSAL,
                         sol.qoi["tip_temperature"][0], to_c(tip), 0, 0.05,
                         detail="exact fin-equation solution", unit="degC")]

    def domain_checks(self, sol):
        p = self.p
        ts = sol.data["ts"]
        lo, hi = sorted((p.ambient_temperature, p.base_temperature))
        out = heat_checks(ts, lo, hi, generation=0.0,
                          loss=(sol.data["hv"], p.ambient_temperature))
        area, perim = self._geom()
        theta_b = abs(p.base_temperature - p.ambient_temperature)
        surf = perim * p.fin_length + (area if p.tip == "convective" else 0.0)
        upper = p.h * surf * theta_b                      # isothermal fin
        lower = theta_b / (p.fin_length / (p.conductivity * area) + 1 / (p.h * surf))
        q = abs(sol.qoi["heat_per_fin"][0])
        out.append(ck.truth(
            "back-of-envelope R-network bounds", ck.HEAT,
            lower * (1 - 1e-6) <= q <= upper * (1 + 1e-6),
            f"heat per fin {q:.4g} W lies between the series conduction+convection "
            f"network ({lower:.4g} W) and an isothermal fin ({upper:.4g} W)"))
        return out


def _chips(spec) -> list:
    out = []
    for i, c in enumerate(spec or []):
        try:
            out.append({"name": str(c.get("name", f"chip{i + 1}")),
                        "x": convert(c["x"], "m"), "y": convert(c["y"], "m"),
                        "w": convert(c["w"], "m"), "h": convert(c["h"], "m"),
                        "power": convert(c["power"], "W")})
        except KeyError as exc:
            raise ParamError(f"chip {i + 1} is missing field {exc}") from exc
    return out


@register
class PcbThermal(Simulation):
    name = "pcb_thermal"
    title = "PCB / chip heat spreading (2-D conduction with surface convection)"
    domain = "electronics"
    description = """Temperature map of a circuit board with heat-dissipating
    components.  Heat spreads in-plane through the board (effective
    conductivity of FR4 plus copper) and leaves from both faces by convection:
    div(k t grad T) - 2h (T - T_inf) + q'' = 0, board edges insulated."""
    params = [
        Param("board_length", "100 mm", "m", "board size in x", lo=0.01, hi=0.5),
        Param("board_width", "80 mm", "m", "board size in y", lo=0.01, hi=0.5),
        Param("thickness", "1.6 mm", "m", "board thickness", lo=0.0002, hi=0.005),
        Param("conductivity", "30 W/(m*K)", "W/(m*K)",
              "effective in-plane conductivity (bare FR4 0.3, 2-layer ~10, 4-layer 20-60)",
              lo=0.2, hi=400),
        Param("h", "12 W/(m^2*K)", "W/(m^2*K)", "convection coefficient on each face",
              lo=1, hi=200),
        Param("ambient_temperature", "25 degC", "K", "air temperature"),
        Param("chips", [
            {"name": "CPU", "x": "30 mm", "y": "45 mm", "w": "15 mm", "h": "15 mm",
             "power": "3 W"},
            {"name": "regulator", "x": "75 mm", "y": "20 mm", "w": "8 mm", "h": "8 mm",
             "power": "1 W"}], None,
            "heat sources: list of {name, x, y (centre), w, h, power} with units"),
    ]
    default_resolution = 96
    min_resolution = 8
    primary = ("max_temperature",)
    resolution_doc = "grid cells along the board length"

    def validate(self):
        p = self.p
        self.chips = _chips(p.chips)
        if not self.chips:
            raise ParamError("at least one chip (heat source) is required")
        for c in self.chips:
            if (c["x"] - c["w"] / 2 < -1e-12 or c["x"] + c["w"] / 2 > p.board_length + 1e-12
                    or c["y"] - c["h"] / 2 < -1e-12
                    or c["y"] + c["h"] / 2 > p.board_width + 1e-12):
                raise ParamError(f"chip '{c['name']}' extends beyond the board edge")
            if c["w"] <= 0 or c["h"] <= 0:
                raise ParamError(f"chip '{c['name']}' needs a positive footprint")

    def _problem(self, n):
        p = self.p
        ny = max(2, round(n * p.board_width / p.board_length))
        g = Grid(n, p.board_length, ny, p.board_width)
        flux = np.zeros(g.shape)
        cover = []
        for c in self.chips:
            w = g.overlap(c["x"] - c["w"] / 2, c["x"] + c["w"] / 2,
                          c["y"] - c["h"] / 2, c["y"] + c["h"] / 2)
            flux += w * c["power"] / (c["w"] * c["h"])
            cover.append(w)
        prob = TransportProblem(g, p.conductivity * p.thickness, sink=2 * p.h,
                                source=flux + 2 * p.h * p.ambient_temperature)
        return prob, flux, cover

    def equations(self):
        dT, area = Q(1, "K"), self.q("board_length") * self.q("board_width")
        return {"board energy balance per unit area": {
            "spreading k*t*dT/L^2": self.q("conductivity") * self.q("thickness") * dT
            / self.q("board_length") ** 2,
            "convection h*dT": self.q("h") * dT,
            "chip heating P/A": Q(1, "W") / area}}

    def dimensionless(self):
        m = (2 * self.q("h") / (self.q("conductivity") * self.q("thickness"))).sqrt()
        return {"spreading parameter m*L": m * self.q("board_length")}

    def well_posed(self):
        return self._problem(8)[0].well_posed(what="temperature")

    def solve(self, n):
        p = self.p
        prob, flux, cover = self._problem(n)
        ts = prob.solve_steady()
        g, T = ts.grid, ts.phi
        qoi = {"max_temperature": (to_c(T.max()), "degC"),
               "mean_board_temperature": (to_c(np.sum(T * g.vol) / np.sum(g.vol)), "degC"),
               "total_power": (float(np.sum(flux * g.vol)), "W")}
        for c, w in zip(self.chips, cover):
            qoi[f"T_{c['name']}"] = (to_c(np.sum(T * w) / np.sum(w)), "degC")
        fields = [Field("board temperature", g.x * 1e3, T - 273.15, g.y * 1e3, "degC",
                        "x [mm]", "y [mm]")]
        return Solution(qoi, fields, ts.residual, data={"ts": ts, "flux": flux})

    def known_solution(self, sol):
        p = self.p
        power = sum(c["power"] for c in self.chips)
        mean = p.ambient_temperature + power / (2 * p.h * p.board_length * p.board_width)
        return [ck.close("known solution: mean board temperature", ck.UNIVERSAL,
                         sol.qoi["mean_board_temperature"][0], to_c(mean), 0, 1e-6,
                         detail="exact integral balance T_inf + P/(2 h A)", unit="degC"),
                operator_benchmark()]

    def domain_checks(self, sol):
        p = self.p
        ts = sol.data["ts"]
        out = heat_checks(ts, lo=p.ambient_temperature, generation=sol.data["flux"],
                          loss=(2 * p.h, p.ambient_temperature),
                          why="heating only, so nothing can be colder than ambient")
        power = sum(c["power"] for c in self.chips)
        lower = power / (2 * p.h * p.board_length * p.board_width)
        upper = max(c["power"] / (c["w"] * c["h"]) for c in self.chips) / (2 * p.h)
        rise = sol.qoi["max_temperature"][0] - to_c(p.ambient_temperature)
        out.append(ck.truth(
            "back-of-envelope R-network bounds", ck.HEAT,
            lower * (1 - 1e-9) <= rise <= upper * (1 + 1e-9),
            f"hot-spot rise {rise:.4g} K lies between perfect spreading P/(2hA) = "
            f"{lower:.4g} K and no spreading q''/(2h) = {upper:.4g} K"))
        return out

    def symmetry_checks(self, sol):
        return sol.data["ts"].symmetry_checks()
