"""Groundwater: drawdown around a pumping well in a confined aquifer."""
from __future__ import annotations

import math

import numpy as np
from scipy.special import gamma

from .. import checks as ck
from ..core import Field, Param, ParamError, Simulation, Solution, register
from ..solvers.transport import Grid, TransportProblem, dirichlet, operator_benchmark
from ..units import Q

# Conformal radius of a square about its centre, as a fraction of its side:
# 4 sqrt(pi) / Gamma(1/4)^2 = 0.53935...  Near a point sink in a square with a
# constant-head boundary the head behaves exactly like a Thiem well with this
# radius of influence.
CONFORMAL = 4 * math.sqrt(math.pi) / gamma(0.25) ** 2


@register
class GroundwaterWell(Simulation):
    name = "groundwater_well"
    title = "Pumping well in a confined aquifer (2-D Darcy flow)"
    domain = "climate & energy"
    description = """Steady drawdown around a well pumping from the centre of a
    square block of confined aquifer whose edges are held at constant head
    (river, lake or regional water table).  Solves div(T grad h) = Q delta(x)
    for the head h, with T the transmissivity, and reports the drawdown at an
    observation well.  Near the well the classic Thiem formula applies."""
    params = [
        Param("side", "1000 m", "m", "side length of the square aquifer block",
              lo=50, hi=20000),
        Param("transmissivity", "500 m^2/day", "m^2/s",
              "hydraulic conductivity x aquifer thickness (sand 100-1000 m^2/day)",
              lo=1e-5, hi=0.1),
        Param("pumping_rate", "1000 m^3/day", "m^3/s", "well discharge", lo=1e-4, hi=1),
        Param("boundary_head", "50 m", "m", "head held on the block edges", lo=1, hi=500),
        Param("observation_radius", "150 m", "m",
              "distance from the well at which drawdown is reported", lo=5, hi=5000),
        Param("well_radius", "0.15 m", "m", "radius of the well screen", lo=0.02, hi=2),
    ]
    default_resolution = 128
    min_resolution = 16
    primary = ("drawdown_at_observation",)

    def validate(self):
        p = self.p
        if min(p.side, p.transmissivity, p.well_radius) <= 0:
            raise ParamError("side, transmissivity and well_radius must be positive")
        if not p.well_radius < p.observation_radius < 0.45 * p.side:
            raise ParamError("observation_radius must lie between the well radius and "
                             "0.45 x side (inside the block)")

    def _problem(self, n):
        p = self.p
        g = Grid(n, p.side, n, p.side)
        c, r = p.side / 2, p.well_radius
        w = g.overlap(c - r, c + r, c - r, c + r)        # exact area weights
        sink = p.pumping_rate * w / float(np.sum(w * g.vol))
        b = dirichlet(p.boundary_head)
        return TransportProblem(g, p.transmissivity, source=-sink,
                                bcs={"west": b, "east": b, "south": b, "north": b})

    def equations(self):
        return {"Thiem drawdown s ~ Q/T": {
            "drawdown s": Q(1, "m"),
            "Q/T": self.q("pumping_rate") / self.q("transmissivity")},
            "Darcy mass balance per unit area": {
                "T*h/L^2": self.q("transmissivity") * self.q("boundary_head")
                / self.q("side") ** 2,
                "Q/L^2": self.q("pumping_rate") / self.q("side") ** 2}}

    def dimensionless(self):
        return {"observation radius / side": self.q("observation_radius") / self.q("side")}

    def well_posed(self):
        return self._problem(8).well_posed(what="head")

    def _ring(self, ts, r) -> float:
        """Mean head on a ring of 8 points.  Averaging the axes with the
        diagonals cancels the leading (4-fold) effect of the square boundary."""
        c = self.p.side / 2
        angles = np.arange(8) * math.pi / 4
        return float(np.mean([ts.sample(c + r * math.cos(a), c + r * math.sin(a))
                              for a in angles]))

    def _thiem(self, r) -> float:
        p = self.p
        return p.pumping_rate / (2 * math.pi * p.transmissivity) * math.log(
            CONFORMAL * p.side / r)

    def solve(self, n):
        p = self.p
        ts = self._problem(n).solve_steady()
        g = ts.grid
        head_obs = self._ring(ts, p.observation_radius)
        s_obs = p.boundary_head - head_obs
        # head at the screen: Thiem's law continued inwards from the observation ring
        s_well = s_obs + p.pumping_rate / (2 * math.pi * p.transmissivity) * math.log(
            p.observation_radius / p.well_radius)
        inflow = sum(ts.boundary_inflow().values())
        qoi = {
            "drawdown_at_observation": (s_obs, "m"),
            "head_at_observation": (head_obs, "m"),
            "drawdown_at_well_screen": (s_well, "m"),
            "boundary_inflow": (inflow * 86400, "m^3/day"),
            "radius_of_influence": (CONFORMAL * p.side, "m"),
        }
        j = g.ny // 2
        cut = 0.5 * (ts.phi[:, j - 1] + ts.phi[:, j])
        fields = [Field("hydraulic head", g.x, ts.phi, g.y, "m", "x [m]", "y [m]"),
                  Field("head along the centre line", g.x, cut, unit="m",
                        xlabel="x [m]", ylabel="head [m]")]
        return Solution(qoi, fields, ts.residual, data={"ts": ts, "inflow": inflow})

    def known_solution(self, sol):
        p = self.p
        dx = p.side / sol.data["ts"].grid.nx
        name = "known solution: Thiem drawdown"
        out = []
        if p.observation_radius < 3 * dx:
            out.append(ck.skip(name, ck.UNIVERSAL,
                               f"observation radius is only {p.observation_radius / dx:.2g} "
                               "cells from the point sink; refine the grid"))
        else:
            out.append(ck.close(
                name, ck.UNIVERSAL, sol.qoi["drawdown_at_observation"][0],
                self._thiem(p.observation_radius), 1e-2,
                detail="s = Q/(2 pi T) ln(R/r) with R = 0.5394 x side, the conformal "
                       "radius of the square (exact as r/side -> 0; next term ~ (r/side)^8)",
                unit="m"))
        out.append(operator_benchmark())
        return out

    def domain_checks(self, sol):
        p = self.p
        ts = sol.data["ts"]
        s_well = sol.qoi["drawdown_at_well_screen"][0]
        return [
            ts.conservation_check("mass in = mass out (discrete balance)", ck.FLUID,
                                  " m^3/s"),
            ck.close("mass in = mass out (boundary inflow = pumping)", ck.FLUID,
                     sol.data["inflow"], p.pumping_rate, 1e-8,
                     detail="water entering through the four edges", unit="m^3/s"),
            ck.bounds("head bounds (maximum principle)", ck.FLUID, -np.inf,
                      p.boundary_head, float(ts.phi.min()), float(ts.phi.max()), unit="m",
                      detail="pumping only lowers the head"),
            ck.truth("model validity: aquifer stays saturated", ck.FLUID,
                     s_well < p.boundary_head,
                     f"drawdown at the well screen {s_well:.4g} m vs available head "
                     f"{p.boundary_head:.4g} m", warn=True),
        ]

    def symmetry_checks(self, sol):
        ts = sol.data["ts"]
        return ts.symmetry_checks() + [ck.symmetry(
            "symmetry about the diagonal", ts.phi, ts.phi.T, 1e-8,
            "square block with a central well: x and y are interchangeable")]
