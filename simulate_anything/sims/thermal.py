"""Heat transfer: walls, generic 2-D conduction, battery packs, fuel pins and
re-entry heat shields.  Temperatures are kelvin internally, degC in reports."""
from __future__ import annotations

import math

import numpy as np

from .. import checks as ck
from ..core import Field, Param, ParamError, Simulation, Solution, register
from ..solvers.transport import (Grid, TransportProblem, dirichlet, insulated,
                                 neumann, operator_benchmark, robin,
                                 transient_benchmark)
from ..units import DimensionError, Q, convert
from ._heat import SIGMA, heat_checks, to_c

NETWORK = "back-of-envelope R-network"


def _qty(obj, key, unit, where, default=None):
    """Read ``obj[key]`` as a quantity in ``unit`` with a helpful error."""
    if not isinstance(obj, dict):
        raise ParamError(f"{where}: expected an object like {{...}}, got {obj!r}")
    if key not in obj:
        if default is not None:
            return default
        raise ParamError(f"{where}: missing '{key}' (give it with units, in {unit})")
    try:
        return convert(obj[key], unit)
    except DimensionError as exc:
        raise DimensionError(f"{where} '{key}': {exc}") from exc
    except (TypeError, ValueError) as exc:
        raise ParamError(f"{where} '{key}': cannot read {obj[key]!r}") from exc


def _film_surface(t_cell, k_cell, half, h, t_env):
    """Surface temperature from the half-cell / film voltage divider."""
    u = k_cell / half
    return (u * t_cell + h * t_env) / (u + h)


# ---------------------------------------------------------------------------
@register
class WallHeatLoss(Simulation):
    name = "wall_heat_loss"
    title = "Heat loss through a multilayer wall"
    domain = "climate & energy"
    description = """Steady 1-D conduction through a layered wall (or roof,
    window, pipe-free insulation build-up) with air films on both sides.
    Reports heat flux, total loss, U-value and every surface and interface
    temperature.  Layers are listed from the inside surface outwards."""
    params = [
        Param("layers", [
            {"name": "plasterboard", "thickness": "12.5 mm", "k": "0.2 W/(m*K)"},
            {"name": "mineral wool", "thickness": "100 mm", "k": "0.04 W/(m*K)"},
            {"name": "brick", "thickness": "100 mm", "k": "0.7 W/(m*K)"}], None,
            "layers from inside to outside: list of {name, thickness, k} with units"),
        Param("inside_temperature", "20 degC", "K", "indoor air temperature"),
        Param("outside_temperature", "-5 degC", "K", "outdoor air temperature"),
        Param("h_inside", "8 W/(m^2*K)", "W/(m^2*K)",
              "inside surface film coefficient", lo=1, hi=50),
        Param("h_outside", "25 W/(m^2*K)", "W/(m^2*K)",
              "outside surface film coefficient (wind raises it)", lo=1, hi=100),
        Param("area", "10 m^2", "m^2", "wall area", lo=0.1, hi=1000),
    ]
    default_resolution = 64
    primary = ("heat_flux", "inner_surface_temperature")
    resolution_doc = "grid cells through the wall thickness"

    def validate(self):
        p = self.p
        if not isinstance(p.layers, (list, tuple)) or not p.layers:
            raise ParamError("layers must be a non-empty list of {name, thickness, k}")
        self.layers = []
        for i, lay in enumerate(p.layers):
            where = f"layer {i + 1}"
            t, k = _qty(lay, "thickness", "m", where), _qty(lay, "k", "W/(m*K)", where)
            if t <= 0 or k <= 0:
                raise ParamError(f"{where}: thickness and conductivity must be positive")
            self.layers.append({"name": str(lay.get("name", f"layer{i + 1}")),
                                "t": t, "k": k})
        if p.h_inside <= 0 or p.h_outside <= 0 or p.area <= 0:
            raise ParamError("film coefficients and area must be positive")
        if p.inside_temperature == p.outside_temperature:
            raise ParamError("inside and outside temperatures are equal: no heat flows")

    def _resistances(self):
        """Series network per unit area: film, layers..., film  [m^2 K/W]."""
        p = self.p
        return ([1 / p.h_inside] + [l["t"] / l["k"] for l in self.layers]
                + [1 / p.h_outside])

    def _problem(self, n):
        p = self.p
        g, lay = Grid.layered([l["t"] for l in self.layers], n)
        k = np.array([l["k"] for l in self.layers])[lay][:, None]
        prob = TransportProblem(g, k, bcs={
            "west": robin(p.h_inside, p.inside_temperature),
            "east": robin(p.h_outside, p.outside_temperature)})
        return prob, lay

    def equations(self):
        dT = Q(1, "K")
        first = self.layers[0]
        return {"series heat flux": {
            "layer conduction k*dT/t": Q(first["k"], "W/(m*K)") * dT / Q(first["t"], "m"),
            "surface film h*dT": self.q("h_inside") * dT,
            "heat loss per area Q/A": Q(1, "W") / self.q("area")}}

    def dimensionless(self):
        r_wall = sum(l["t"] / l["k"] for l in self.layers)
        return {"Biot number h_inside*R_wall": self.q("h_inside") * Q(r_wall, "m^2*K/W")}

    def well_posed(self):
        return self._problem(8)[0].well_posed(what="temperature")

    def solve(self, n):
        p = self.p
        prob, lay = self._problem(n)
        ts = prob.solve_steady()
        g, T, K = ts.grid, ts.phi[:, 0], prob.K[:, 0]
        flux = ts.boundary_inflow()["west"]
        t_si = _film_surface(T[0], K[0], g.dxs[0] / 2, p.h_inside, p.inside_temperature)
        t_so = _film_surface(T[-1], K[-1], g.dxs[-1] / 2, p.h_outside,
                             p.outside_temperature)
        nodes = [t_si]                      # surface, interfaces..., surface
        xs = [0.0]
        for i in np.nonzero(np.diff(lay))[0]:
            ua, ub = K[i] / (g.dxs[i] / 2), K[i + 1] / (g.dxs[i + 1] / 2)
            nodes.append((ua * T[i] + ub * T[i + 1]) / (ua + ub))
            xs.append(g.xf[i + 1])
        nodes.append(t_so)
        xs.append(g.xf[-1])
        d_t = p.inside_temperature - p.outside_temperature
        qoi = {"heat_flux": (flux, "W/m^2"),
               "heat_loss": (flux * p.area, "W"),
               "u_value": (flux / d_t, "W/(m^2*K)"),
               "inner_surface_temperature": (to_c(t_si), "degC"),
               "outer_surface_temperature": (to_c(t_so), "degC")}
        for j in range(len(self.layers) - 1):
            a, b = self.layers[j]["name"], self.layers[j + 1]["name"]
            qoi[f"T_between_{a}_and_{b}"] = (to_c(nodes[j + 1]), "degC")
        order = np.argsort(np.concatenate([xs, g.x]))
        px = np.concatenate([xs, g.x])[order]
        pt = np.concatenate([nodes, T])[order]
        fields = [Field("temperature through wall", px * 1e3, pt - 273.15, unit="degC",
                        xlabel="distance from inside surface [mm]", ylabel="T [degC]")]
        return Solution(qoi, fields, ts.residual, data={"ts": ts, "nodes": nodes})

    def known_solution(self, sol):
        p = self.p
        r = self._resistances()
        q = (p.inside_temperature - p.outside_temperature) / sum(r)
        return [ck.close("known solution: heat flux", ck.UNIVERSAL,
                         sol.qoi["heat_flux"][0], q, 1e-8,
                         detail="exact series resistance dT / sum(R)", unit="W/m^2"),
                ck.close("known solution: inner surface temperature", ck.UNIVERSAL,
                         sol.qoi["inner_surface_temperature"][0],
                         to_c(p.inside_temperature - q / p.h_inside), 0, 1e-6,
                         detail="T_in - q / h_inside", unit="degC")]

    def domain_checks(self, sol):
        p = self.p
        lo, hi = sorted((p.inside_temperature, p.outside_temperature))
        out = heat_checks(sol.data["ts"], lo, hi, generation=0.0)
        # every element of the chain must carry the same flux: dT_i / R_i = q
        temps = [p.inside_temperature] + list(sol.data["nodes"]) + [p.outside_temperature]
        q = sol.qoi["heat_flux"][0]
        through = [(temps[i] - temps[i + 1]) / r for i, r in enumerate(self._resistances())]
        worst = max(abs(v - q) for v in through) / abs(q)
        out.append(ck.small(
            NETWORK, ck.HEAT, worst, 1e-8,
            f"temperature drop over each of the {len(through)} series resistances "
            f"divided by its R gives the same flux ({q:.5g} W/m^2); worst mismatch"))
        return out


# ---------------------------------------------------------------------------
_SIDES = {"left": "west", "right": "east", "bottom": "south", "top": "north"}
_BC_ALIASES = {"temperature": "dirichlet", "dirichlet": "dirichlet", "fixed": "dirichlet",
               "convection": "robin", "robin": "robin", "convective": "robin",
               "flux": "neumann", "neumann": "neumann", "heat_flux": "neumann",
               "insulated": "insulated", "adiabatic": "insulated",
               "symmetry": "insulated"}


def _parse_side(spec, side):
    where = f"boundary '{side}'"
    if isinstance(spec, str):
        spec = {"type": spec}
    if not isinstance(spec, dict) or "type" not in spec:
        raise ParamError(
            f"{where}: give an object with a 'type' of temperature, convection, "
            f"flux or insulated, e.g. {{\"type\": \"temperature\", \"value\": \"100 degC\"}}")
    kind = _BC_ALIASES.get(str(spec["type"]).strip().lower())
    if kind is None:
        raise ParamError(f"{where}: unknown type '{spec['type']}' (use temperature, "
                         "convection, flux or insulated)")
    if kind == "dirichlet":
        value = _qty(spec, "value", "K", where)
        if value <= 0:
            raise ParamError(f"{where}: temperature below absolute zero")
        return dirichlet(value)
    if kind == "robin":
        h = _qty(spec, "h", "W/(m^2*K)", where)
        if h < 0:
            raise ParamError(f"{where}: convection coefficient must not be negative")
        return robin(h, _qty(spec, "ambient", "K", where))
    if kind == "neumann":
        return neumann(_qty(spec, "value", "W/m^2", where))
    return insulated()


def _series_rect(x, y, a, b, t_left, t_right, t_bottom, t_top, terms=200):
    """Fourier-series solution of Laplace's equation on [0,a]x[0,b] with four
    constant edge temperatures (superposition of four one-hot-side problems)."""
    def ratio(u, v):                  # sinh(u) / sinh(v) without overflow
        return math.exp(u - v) * (1 - math.exp(-2 * u)) / (1 - math.exp(-2 * v))

    tot = 0.0
    for n in range(1, 2 * terms, 2):
        c = 4 / (n * math.pi)
        wx, wy = n * math.pi / a, n * math.pi / b
        tot += c * math.sin(wx * x) * (t_top * ratio(wx * y, wx * b)
                                       + t_bottom * ratio(wx * (b - y), wx * b))
        tot += c * math.sin(wy * y) * (t_right * ratio(wy * x, wy * a)
                                       + t_left * ratio(wy * (a - x), wy * a))
    return tot


def _exact_1d(length, k, gen, bc0, bc1):
    """Exact T(x) = -g x^2/(2k) + c1 x + c2 between two general boundary
    conditions.  Returns (c1, c2, heat into x=0, heat into x=L) per unit area."""
    A, r = np.zeros((2, 2)), np.zeros(2)
    if bc0.kind == "dirichlet":
        A[0], r[0] = (0, 1), bc0.value
    elif bc0.kind == "robin":         # -k c1 = h (T_inf - c2)
        A[0], r[0] = (-k, bc0.h), bc0.h * bc0.value
    else:                             # -k c1 = q_in
        A[0], r[0] = (-k, 0), bc0.value
    t_l, d_l = -gen * length ** 2 / (2 * k), -gen * length / k
    if bc1.kind == "dirichlet":
        A[1], r[1] = (length, 1), bc1.value - t_l
    elif bc1.kind == "robin":         # k T'(L) = h (T_inf - T(L))
        A[1], r[1] = (k + bc1.h * length, bc1.h), bc1.h * (bc1.value - t_l) - k * d_l
    else:                             # k T'(L) = q_in
        A[1], r[1] = (k, 0), bc1.value - k * d_l
    c1, c2 = np.linalg.solve(A, r)
    return c1, c2, -k * c1, k * (d_l + c1)


@register
class Conduction2D(Simulation):
    name = "conduction_2d"
    title = "2-D steady heat conduction in a rectangle (textbook problem)"
    domain = "education"
    description = """The generic conduction problem: a rectangular cross-section
    with any mix of fixed-temperature, convection, heat-flux and insulated
    sides, uniform heat generation and optional rectangular inclusions with
    their own conductivity and generation.  Solves div(k grad T) + q''' = 0
    per unit depth.  Heat flows are positive into the body."""
    params = [
        Param("width", "0.1 m", "m", "size in x", lo=0.001, hi=10),
        Param("height", "0.1 m", "m", "size in y", lo=0.001, hi=10),
        Param("conductivity", "15 W/(m*K)", "W/(m*K)", "thermal conductivity",
              lo=0.02, hi=400),
        Param("generation", "0 W/m^3", "W/m^3", "uniform volumetric heat generation",
              lo=0, hi=1e7),
        Param("left", {"type": "temperature", "value": "100 degC"}, None,
              "boundary at x=0: {type: temperature, value} | {type: convection, h, "
              "ambient} | {type: flux, value (W/m^2 into the body)} | {type: insulated}"),
        Param("right", {"type": "convection", "h": "50 W/(m^2*K)", "ambient": "20 degC"},
              None, "boundary at x=width (same options)"),
        Param("bottom", {"type": "insulated"}, None, "boundary at y=0 (same options)"),
        Param("top", {"type": "convection", "h": "50 W/(m^2*K)", "ambient": "20 degC"},
              None, "boundary at y=height (same options)"),
        Param("regions", [], None,
              "optional inclusions: list of {x0, x1, y0, y1, k, generation} with units"),
    ]
    default_resolution = 64
    min_resolution = 4
    primary = ("centre_temperature", "mean_temperature")
    resolution_doc = "grid cells along the width"

    def validate(self):
        p = self.p
        if p.width <= 0 or p.height <= 0 or p.conductivity <= 0:
            raise ParamError("width, height and conductivity must be positive")
        self.bcs = {_SIDES[s]: _parse_side(getattr(p, s), s) for s in _SIDES}
        if not isinstance(p.regions, (list, tuple)):
            raise ParamError("regions must be a list of {x0, x1, y0, y1, k, generation}")
        self.regions = []
        for i, reg in enumerate(p.regions):
            where = f"region {i + 1}"
            box = [_qty(reg, key, "m", where) for key in ("x0", "x1", "y0", "y1")]
            if not (0 <= box[0] < box[1] <= p.width * (1 + 1e-12)
                    and 0 <= box[2] < box[3] <= p.height * (1 + 1e-12)):
                raise ParamError(f"{where}: needs 0 <= x0 < x1 <= width and "
                                 "0 <= y0 < y1 <= height")
            if "k" not in reg and "generation" not in reg:
                raise ParamError(f"{where}: give 'k' and/or 'generation'")
            k = _qty(reg, "k", "W/(m*K)", where, default=p.conductivity)
            if k <= 0:
                raise ParamError(f"{where}: conductivity must be positive")
            self.regions.append((box, k, "k" in reg,
                                 _qty(reg, "generation", "W/m^3", where, default=0.0)
                                 if "generation" in reg else None))

    # -- setup helpers -------------------------------------------------------
    def _problem(self, n):
        p = self.p
        ny = max(2, round(n * p.height / p.width))
        g = Grid(n, p.width, ny, p.height)
        k, gen = g.full(p.conductivity), g.full(p.generation)
        for box, rk, has_k, rgen in self.regions:
            if has_k:
                k[g.box(*box)] = rk
            if rgen is not None:      # exact area weights: total power is grid-independent
                w = g.overlap(*box)
                gen = gen * (1 - w) + rgen * w
        return TransportProblem(g, k, source=gen, bcs=dict(self.bcs)), gen

    def _side_length(self, side):
        return self.p.height if side in ("west", "east") else self.p.width

    def _env(self):
        """Temperatures imposed on the body (fixed sides and ambients)."""
        return [float(bc.value) for bc in self.bcs.values()
                if bc.kind == "dirichlet" or (bc.kind == "robin" and bc.h > 0)]

    def _gen_signs(self):
        gens = [self.p.generation] + [r[3] for r in self.regions if r[3] is not None]
        fluxes = [bc.value for bc in self.bcs.values() if bc.kind == "neumann"]
        return gens, fluxes

    def _one_d(self):
        """('x' or 'y', first bc, second bc, length, cross width) if the setup
        is really one-dimensional, else None."""
        if self.regions:
            return None

        def adiabatic(bc):
            return bc.kind == "neumann" and bc.value == 0

        b = self.bcs
        if adiabatic(b["south"]) and adiabatic(b["north"]):
            return "x", b["west"], b["east"], self.p.width, self.p.height
        if adiabatic(b["west"]) and adiabatic(b["east"]):
            return "y", b["south"], b["north"], self.p.height, self.p.width
        return None

    def equations(self):
        dT = Q(1, "K")
        eqs = {"heat equation": {
            "conduction k*dT/L^2": self.q("conductivity") * dT / self.q("width") ** 2,
            "generation q'''": Q(1, "W/m^3")}}
        hs = [bc.h for bc in self.bcs.values() if bc.kind == "robin"]
        if hs:
            eqs["surface energy balance"] = {
                "conduction k*dT/L": self.q("conductivity") * dT / self.q("width"),
                "convection h*dT": Q(max(hs), "W/(m^2*K)") * dT,
                "heat flux": Q(1, "W/m^2")}
        return eqs

    def dimensionless(self):
        out = {"aspect ratio height/width": self.q("height") / self.q("width")}
        for name, side in _SIDES.items():
            bc = self.bcs[side]
            if bc.kind == "robin":
                out[f"Biot number ({name}) h*L/k"] = (
                    Q(float(bc.h), "W/(m^2*K)") * self.q("width") / self.q("conductivity"))
        return out

    def well_posed(self):
        return self._problem(8)[0].well_posed(what="temperature")

    # -- solve ---------------------------------------------------------------
    def solve(self, n):
        p = self.p
        prob, gen = self._problem(n)
        ts = prob.solve_steady()
        g, T = ts.grid, ts.phi
        inflow = ts.boundary_inflow()
        qoi = {"max_temperature": (to_c(T.max()), "degC"),
               "min_temperature": (to_c(T.min()), "degC"),
               "mean_temperature": (to_c(np.sum(T * g.vol) / np.sum(g.vol)), "degC"),
               "centre_temperature": (to_c(ts.sample(p.width / 2, p.height / 2)), "degC")}
        for name, side in _SIDES.items():
            qoi[f"heat_in_{name}"] = (inflow[side], "W/m")
        qoi["heat_generated"] = (ts.source_total(), "W/m")
        fields = [Field("temperature", g.x, T - 273.15, g.y, "degC")]
        return Solution(qoi, fields, ts.residual, data={"ts": ts, "gen": gen})

    # -- verification --------------------------------------------------------
    def known_solution(self, sol):
        p, ts, b = self.p, sol.data["ts"], self.bcs
        uniform = not self.regions
        if uniform and p.generation == 0 and all(bc.kind == "dirichlet" for bc in b.values()):
            vals = [b[s].value for s in ("west", "east", "south", "north")]
            span = max(max(vals) - min(vals), 1e-9)
            worst = 0.0
            for fx, fy in ((0.5, 0.5), (0.25, 0.25), (0.75, 0.5), (0.5, 0.8)):
                x, y = fx * p.width, fy * p.height
                exact = _series_rect(x, y, p.width, p.height, *vals)
                worst = max(worst, abs(ts.sample(x, y) - exact))
            return [ck.small("known solution: Fourier series", ck.UNIVERSAL,
                             worst / span, 5e-3,
                             "exact separation-of-variables solution at four interior "
                             "points; worst error as a fraction of the temperature span")]
        one_d = self._one_d()
        if one_d:
            axis, bc0, bc1, length, _ = one_d
            c1, c2, _, _ = _exact_1d(length, p.conductivity, p.generation, bc0, bc1)

            def exact(s):
                return -p.generation * s ** 2 / (2 * p.conductivity) + c1 * s + c2

            mean = -p.generation * length ** 2 / (6 * p.conductivity) + c1 * length / 2 + c2
            ends = (exact(0.0), exact(length), exact(length / 2))
            tol = 2e-3 * max(max(ends) - min(ends), 1.0)
            return [ck.close("known solution: exact 1-D profile (centre)", ck.UNIVERSAL,
                             sol.qoi["centre_temperature"][0], to_c(exact(length / 2)),
                             0, tol, detail=f"setup is 1-D along {axis}", unit="degC"),
                    ck.close("known solution: exact 1-D profile (mean)", ck.UNIVERSAL,
                             sol.qoi["mean_temperature"][0], to_c(mean), 0, tol,
                             detail=f"setup is 1-D along {axis}", unit="degC")]
        return [operator_benchmark()]

    def _bounds(self):
        gens, fluxes = self._gen_signs()
        env = self._env()
        if not env:
            return None, None, "no temperature is imposed anywhere"
        heating = all(v >= 0 for v in gens + fluxes)
        cooling = all(v <= 0 for v in gens + fluxes)
        lo = min(env) if heating else None
        hi = max(env) if cooling else None
        if heating and cooling:
            why = "no sources: extremes sit on the boundary"
        elif heating:
            why = "heating only: nothing colder than the coldest surrounding, no a-priori maximum"
        elif cooling:
            why = "cooling only: nothing hotter than the hottest surrounding, no a-priori minimum"
        else:
            why = "heat sources and sinks of both signs: no a-priori bound"
        return lo, hi, why

    def _network_check(self, sol):
        p, ts = self.p, sol.data["ts"]
        inflow = ts.boundary_inflow()
        gens, fluxes = self._gen_signs()
        one_d = self._one_d()
        if one_d:
            axis, bc0, bc1, length, cross = one_d
            first = "west" if axis == "x" else "south"
            if p.generation == 0 and bc0.kind != "neumann" and bc1.kind != "neumann":
                r = length / (p.conductivity * cross)
                r += sum(1 / (bc.h * cross) for bc in (bc0, bc1) if bc.kind == "robin")
                expect = (bc0.value - bc1.value) / r
                what = "series network (films + slab), Q = dT / sum(R)"
            else:
                expect = _exact_1d(length, p.conductivity, p.generation, bc0, bc1)[2] * cross
                what = "1-D energy-balance network"
            scale = max(abs(expect), abs(ts.source_total()), 1e-12)
            return ck.close(NETWORK, ck.HEAT, inflow[first], expect, 0, 1e-6 * scale,
                            detail=what, unit="W/m")
        env = self._env()
        conv = [(s, bc) for s, bc in self.bcs.items() if bc.kind == "robin" and bc.h > 0]
        passive = all(v == 0 for v in gens + fluxes)
        if conv and passive and env:
            ok, parts = True, []
            for side, bc in conv:
                cap = bc.h * self._side_length(side) * max(abs(e - bc.value) for e in env)
                ok &= abs(inflow[side]) <= cap * (1 + 1e-9)
                parts.append(f"{side} {abs(inflow[side]):.4g} <= {cap:.4g}")
            return ck.truth(
                NETWORK, ck.HEAT, ok,
                "heat through each surface film stays below (largest imposed "
                "temperature difference) / (film resistance) [W/m]: " + "; ".join(parts))
        ambients = {float(bc.value) for _, bc in conv}
        only_films = all(bc.kind != "dirichlet" for bc in self.bcs.values())
        total = ts.source_total()
        if (conv and only_films and len(ambients) == 1 and total > 0
                and all(v >= 0 for v in gens) and all(v == 0 for v in fluxes)):
            lumped = total / sum(bc.h * self._side_length(s) for s, bc in conv)
            rise = float(ts.phi.max()) - ambients.pop()
            return ck.truth(
                NETWORK, ck.HEAT, rise >= lumped * (1 - 1e-9),
                f"hot-spot rise {rise:.4g} K is at least the lumped film-network rise "
                f"G / sum(h L) = {lumped:.4g} K (equal only for infinite conductivity)")
        return ck.skip(NETWORK, ck.HEAT,
                       "genuinely 2-D setup with mixed sources and boundary types has no "
                       "simple resistor network; energy balance and the benchmark cover it")

    def domain_checks(self, sol):
        lo, hi, why = self._bounds()
        out = heat_checks(sol.data["ts"], lo, hi, generation=sol.data["gen"], why=why)
        out.append(self._network_check(sol))
        return out

    def symmetry_checks(self, sol):
        return sol.data["ts"].symmetry_checks()


# ---------------------------------------------------------------------------
@register
class BatteryPack(Simulation):
    name = "battery_pack"
    title = "Battery pack warm-up (transient 2-D conduction with I^2 R heating)"
    domain = "electronics"
    description = """Cross-section of a battery pack treated as a homogeneous
    block that heats uniformly by I^2 R losses and is cooled by convection on
    its four sides, starting at ambient.  Reports the temperatures at the end
    of the discharge, the steady state it is heading for and the thermal time
    constant.  Backward-Euler time stepping."""
    params = [
        Param("n_cells", 48, None, "number of cells in the pack"),
        Param("current", "10 A", "A", "current through every cell", lo=0, hi=100),
        Param("cell_resistance", "25 mohm", "ohm", "internal resistance per cell",
              lo=0.001, hi=0.2),
        Param("width", "0.3 m", "m", "pack cross-section width", lo=0.02, hi=2),
        Param("height", "0.15 m", "m", "pack cross-section height", lo=0.02, hi=2),
        Param("depth", "0.2 m", "m", "pack length normal to the section", lo=0.02, hi=2),
        Param("conductivity", "3 W/(m*K)", "W/(m*K)",
              "effective conductivity of the cell/holder block", lo=0.2, hi=50),
        Param("density", "2400 kg/m^3", "kg/m^3", "effective density", lo=500, hi=5000),
        Param("specific_heat", "1000 J/(kg*K)", "J/(kg*K)", "effective specific heat",
              lo=300, hi=2000),
        Param("h", "15 W/(m^2*K)", "W/(m^2*K)",
              "convection coefficient on the four sides", lo=1, hi=500),
        Param("ambient_temperature", "25 degC", "K", "air (and starting) temperature"),
        Param("duration", "30 min", "s", "length of the discharge", lo=60, hi=36000),
    ]
    default_resolution = 64
    min_resolution = 4
    primary = ("mean_temperature", "max_temperature")
    resolution_doc = "grid cells along the pack width (time steps scale with it)"

    def validate(self):
        p = self.p
        if p.n_cells < 1:
            raise ParamError("n_cells must be at least 1")
        for key in ("width", "height", "depth", "conductivity", "density",
                    "specific_heat", "duration", "cell_resistance"):
            if getattr(p, key) <= 0:
                raise ParamError(f"{key} must be positive")
        if p.h < 0:
            raise ParamError("h must not be negative")

    def _power(self):
        p = self.p
        return p.n_cells * p.current ** 2 * p.cell_resistance

    def _lumped(self):
        """(surface area, time constant, Biot number) of the lumped model."""
        p = self.p
        surf = 2 * (p.width + p.height) * p.depth
        vol = p.width * p.height * p.depth
        tau = p.density * p.specific_heat * vol / (p.h * surf) if p.h else math.inf
        return surf, tau, p.h * (vol / surf) / p.conductivity

    def _problem(self, n):
        p = self.p
        ny = max(2, round(n * p.height / p.width))
        g = Grid(n, p.width, ny, p.height)
        q = self._power() / (p.width * p.height * p.depth)
        film = robin(p.h, p.ambient_temperature)
        prob = TransportProblem(g, p.conductivity, C=p.density * p.specific_heat,
                                source=q, bcs={s: film for s in _SIDES.values()})
        return prob, q

    def equations(self):
        dT = Q(1, "K")
        vol = self.q("width") * self.q("height") * self.q("depth")
        return {"heat equation": {
            "storage rho*cp*dT/t": self.q("density") * self.q("specific_heat") * dT
            / self.q("duration"),
            "conduction k*dT/L^2": self.q("conductivity") * dT / self.q("width") ** 2,
            "heating n*I^2*R/V": Q(self.p.n_cells) * self.q("current") ** 2
            * self.q("cell_resistance") / vol}}

    def dimensionless(self):
        alpha = self.q("conductivity") / (self.q("density") * self.q("specific_heat"))
        half = self.q("height") / 2
        return {"Biot number h*(V/A)/k": Q(self._lumped()[2]),
                "Fourier number alpha*t/(H/2)^2": alpha * self.q("duration") / half ** 2,
                "Biot check h*L/k": self.q("h") * half / self.q("conductivity")}

    def well_posed(self):
        return self._problem(8)[0].well_posed(steady=False, what="temperature")

    def solve(self, n):
        p = self.p
        prob, q = self._problem(n)
        # dt ~ dx^2 so that time and space errors shrink together (2nd order)
        steps = max(8, n * n // 32)
        ts = prob.solve_transient(p.ambient_temperature, p.duration,
                                  p.duration / steps, theta=1.0)
        g, T = ts.grid, ts.phi
        steady = prob.solve_steady() if p.h > 0 else None
        _, tau, _ = self._lumped()
        qoi = {"max_temperature": (to_c(T.max()), "degC"),
               "mean_temperature": (to_c(np.sum(T * g.vol) / np.sum(g.vol)), "degC"),
               "steady_max_temperature": (to_c(steady.phi.max()) if steady else math.inf,
                                          "degC"),
               "heat_generation": (self._power(), "W"),
               "time_constant": (tau, "s")}
        h = ts.history
        fields = [Field("temperature at end of discharge", g.x * 1e3, T - 273.15,
                        g.y * 1e3, "degC", "x [mm]", "y [mm]"),
                  Field("temperature history", h["t"] / 60,
                        {"max": h["max"] - 273.15, "mean": h["mean"] - 273.15},
                        unit="degC", xlabel="time [min]", ylabel="T [degC]")]
        cfl = (0.0, 1.0,
               "backward Euler is unconditionally stable, so its explicit-part number "
               f"is zero; mesh Fourier number dt*A_ii/M_i = {ts.mesh_number:.3g} "
               "(accuracy, not stability)")
        return Solution(qoi, fields, ts.residual, cfl=cfl,
                        residual_note="relative residual of the last implicit step",
                        data={"ts": ts, "steady": steady, "q": q})

    def known_solution(self, sol):
        return [transient_benchmark(), operator_benchmark()]

    def domain_checks(self, sol):
        p = self.p
        ts, steady = sol.data["ts"], sol.data["steady"]
        hi = float(steady.phi.max()) if steady is not None else None
        out = heat_checks(ts, p.ambient_temperature, hi, generation=sol.data["q"],
                          why="starts at ambient and heats monotonically towards the "
                              "steady state, which caps it")
        surf, tau, biot = self._lumped()
        if p.h == 0 or self._power() == 0:
            out.append(ck.skip(NETWORK, ck.HEAT, "no cooling or no heating: nothing to "
                               "balance in a lumped network"))
        elif biot > 1:
            out.append(ck.skip(NETWORK, ck.HEAT,
                               f"Biot number {biot:.3g} > 1: internal gradients dominate, "
                               "a single lumped capacitance is not a fair estimate"))
        else:
            rise = self._power() / (p.h * surf) * (1 - math.exp(-p.duration / tau))
            got = sol.qoi["mean_temperature"][0] - to_c(p.ambient_temperature)
            out.append(ck.close(
                NETWORK, ck.HEAT, got, rise, 0.02 + 0.5 * biot,
                detail=f"mean rise vs lumped capacitance P/(hA)(1 - exp(-t/tau)), "
                       f"tolerance 2% + Bi/2 with Bi = {biot:.3g}", unit="K",
                warn_factor=2))
        return out

    def symmetry_checks(self, sol):
        return sol.data["ts"].symmetry_checks()


# ---------------------------------------------------------------------------
@register
class FuelPin(Simulation):
    name = "fuel_pin"
    title = "Reactor fuel pin (radial conduction: fuel, gap, cladding, coolant)"
    domain = "climate & energy"
    description = """Steady radial temperature profile of a cylindrical nuclear
    fuel pin: uniform volumetric heating in the pellet, conduction through the
    gas gap and cladding, convection to the coolant.  Reports centreline,
    pellet-surface and cladding temperatures and the margin to fuel melting."""
    params = [
        Param("linear_power", "20 kW/m", "W/m", "heat generated per unit pin length",
              lo=1000, hi=60000),
        Param("fuel_radius", "4.1 mm", "m", "pellet radius", lo=0.002, hi=0.01),
        Param("gap_thickness", "0.08 mm", "m", "pellet-clad gas gap", lo=1e-5, hi=5e-4),
        Param("clad_thickness", "0.57 mm", "m", "cladding wall", lo=1e-4, hi=2e-3),
        Param("k_fuel", "3 W/(m*K)", "W/(m*K)", "fuel conductivity (UO2 about 3)",
              lo=1, hi=30),
        Param("k_gap", "0.3 W/(m*K)", "W/(m*K)", "gap gas conductivity (helium ~0.3)",
              lo=0.02, hi=2),
        Param("k_clad", "15 W/(m*K)", "W/(m*K)", "cladding conductivity (Zircaloy ~15)",
              lo=5, hi=50),
        Param("h_coolant", "30000 W/(m^2*K)", "W/(m^2*K)",
              "coolant heat-transfer coefficient", lo=1000, hi=100000),
        Param("coolant_temperature", "300 degC", "K", "bulk coolant temperature"),
        Param("melting_temperature", "2800 degC", "K", "fuel melting point"),
    ]
    default_resolution = 96
    min_resolution = 6
    primary = ("centreline_temperature", "clad_outer_temperature")
    resolution_doc = "radial grid cells from centreline to clad surface"

    def validate(self):
        p = self.p
        for key in ("fuel_radius", "gap_thickness", "clad_thickness", "k_fuel", "k_gap",
                    "k_clad", "h_coolant"):
            if getattr(p, key) <= 0:
                raise ParamError(f"{key} must be positive")

    def _radii(self):
        p = self.p
        a = p.fuel_radius
        return a, a + p.gap_thickness, a + p.gap_thickness + p.clad_thickness

    def _problem(self, n):
        p = self.p
        g, lay = Grid.layered([p.fuel_radius, p.gap_thickness, p.clad_thickness], n,
                              coord="cylindrical")
        k = np.array([p.k_fuel, p.k_gap, p.k_clad])[lay][:, None]
        q = p.linear_power / (math.pi * p.fuel_radius ** 2)
        src = np.where(lay == 0, q, 0.0)[:, None]
        prob = TransportProblem(g, k, source=src,
                                bcs={"east": robin(p.h_coolant, p.coolant_temperature)})
        return prob, lay, q

    def _exact(self):
        """(centreline, fuel surface, clad inner, clad outer) temperatures."""
        p = self.p
        a, b, c = self._radii()
        ql = p.linear_power
        t_co = p.coolant_temperature + ql / (2 * math.pi * c * p.h_coolant)
        t_ci = t_co + ql * math.log(c / b) / (2 * math.pi * p.k_clad)
        t_fs = t_ci + ql * math.log(b / a) / (2 * math.pi * p.k_gap)
        return t_fs + ql / (4 * math.pi * p.k_fuel), t_fs, t_ci, t_co

    def equations(self):
        dT = Q(1, "K")
        a = self.q("fuel_radius")
        return {"radial conduction in the pellet": {
                    "conduction k*dT/r^2": self.q("k_fuel") * dT / a ** 2,
                    "heating q' / (pi a^2)": self.q("linear_power") / a ** 2},
                "clad surface balance": {
                    "convection h*dT": self.q("h_coolant") * dT,
                    "surface flux q' / (2 pi r)": self.q("linear_power") / a}}

    def dimensionless(self):
        return {"Biot number h*r_clad/k_clad": self.q("h_coolant")
                * Q(self._radii()[2], "m") / self.q("k_clad")}

    def well_posed(self):
        return self._problem(12)[0].well_posed(what="temperature")

    def solve(self, n):
        p = self.p
        prob, lay, q = self._problem(n)
        ts = prob.solve_steady()
        g, T, K = ts.grid, ts.phi[:, 0], prob.K[:, 0]
        # centreline: even (quadratic) extrapolation of the first two cells to r = 0
        r1, r2 = g.x[0], g.x[1]
        t_cl = T[0] - (T[1] - T[0]) * r1 ** 2 / (r2 ** 2 - r1 ** 2)
        faces = []
        for i in np.nonzero(np.diff(lay))[0]:
            ua, ub = K[i] / (g.dxs[i] / 2), K[i + 1] / (g.dxs[i + 1] / 2)
            faces.append((ua * T[i] + ub * T[i + 1]) / (ua + ub))
        t_fs, t_ci = faces
        t_co = _film_surface(T[-1], K[-1], g.dxs[-1] / 2, p.h_coolant,
                             p.coolant_temperature)
        power = ts.source_total() * g.measure
        qoi = {"centreline_temperature": (to_c(t_cl), "degC"),
               "fuel_surface_temperature": (to_c(t_fs), "degC"),
               "clad_inner_temperature": (to_c(t_ci), "degC"),
               "clad_outer_temperature": (to_c(t_co), "degC"),
               "margin_to_melting": (p.melting_temperature - t_cl, "K"),
               "surface_heat_flux": (power / (2 * math.pi * self._radii()[2]), "W/m^2"),
               "linear_power_resolved": (power, "W/m")}
        fields = [Field("radial temperature", g.x * 1e3, T - 273.15, unit="degC",
                        xlabel="radius [mm]", ylabel="T [degC]")]
        return Solution(qoi, fields, ts.residual, data={"ts": ts, "q": q, "lay": lay})

    def known_solution(self, sol):
        names = ("centreline_temperature", "fuel_surface_temperature",
                 "clad_inner_temperature", "clad_outer_temperature")
        rise = self._exact()[0] - self.p.coolant_temperature
        return [ck.close(f"known solution: {n.replace('_', ' ')}", ck.UNIVERSAL,
                         sol.qoi[n][0], to_c(t), 0, 1e-3 * rise,
                         detail="exact layered-cylinder solution", unit="degC")
                for n, t in zip(names, self._exact())]

    def domain_checks(self, sol):
        p = self.p
        ts = sol.data["ts"]
        src = np.where(sol.data["lay"] == 0, sol.data["q"], 0.0)[:, None]
        out = heat_checks(ts, lo=p.coolant_temperature, generation=src,
                          why="heating only: nothing is colder than the coolant")
        out.append(ck.close("energy balance: resolved linear power", ck.HEAT,
                            sol.qoi["linear_power_resolved"][0], p.linear_power, 1e-9,
                            detail="integral of q''' over the pellet", unit="W/m"))
        # thin-wall (planar) resistor chain, each layer at its mean radius
        a, b, c = self._radii()
        ql = p.linear_power
        estimate = (ql / (4 * math.pi * p.k_fuel)
                    + ql / (math.pi * (a + b)) * (b - a) / p.k_gap
                    + ql / (math.pi * (b + c)) * (c - b) / p.k_clad
                    + ql / (2 * math.pi * c * p.h_coolant))
        rise = sol.qoi["centreline_temperature"][0] - to_c(p.coolant_temperature)
        out.append(ck.close(NETWORK, ck.HEAT, rise, estimate, 0.02,
                            detail="centreline rise vs series network with gap and clad "
                                   "as flat walls at their mean radius", unit="K"))
        return out


# ---------------------------------------------------------------------------
K_SUTTON_GRAVES = 1.7415e-4        # kg^0.5 / m, Earth atmosphere, SI units


def _ierfc(x):
    return math.exp(-x * x) / math.sqrt(math.pi) - x * math.erfc(x)


@register
class ReentryHeating(Simulation):
    name = "reentry_heating"
    title = "Re-entry heat shield (stagnation heating, 1-D transient conduction)"
    domain = "aerospace"
    description = """Stagnation-point heat flux from the Sutton-Graves relation
    q = k sqrt(rho / R_nose) V^3, applied as a constant flux to a heat-shield
    slab with an insulated back face.  Explicit time stepping, so the time
    step is bound by the CFL-type limit alpha dt/dx^2 <= 1/2.  Ablation and
    surface re-radiation are NOT modelled: the surface temperature is an
    upper bound (compare it with the radiative-equilibrium temperature)."""
    params = [
        Param("velocity", "4 km/s", "m/s", "flight speed", lo=1000, hi=12000),
        Param("air_density", "1e-4 kg/m^3", "kg/m^3",
              "free-stream density (1e-4 is roughly 65 km altitude)", lo=1e-6, hi=1e-2),
        Param("nose_radius", "1 m", "m", "nose radius of curvature", lo=0.05, hi=5),
        Param("shield_thickness", "50 mm", "m", "heat-shield thickness", lo=0.005, hi=0.3),
        Param("conductivity", "0.6 W/(m*K)", "W/(m*K)", "shield conductivity",
              lo=0.05, hi=20),
        Param("density", "1450 kg/m^3", "kg/m^3", "shield density", lo=100, hi=4000),
        Param("specific_heat", "1300 J/(kg*K)", "J/(kg*K)", "shield specific heat",
              lo=300, hi=3000),
        Param("initial_temperature", "300 K", "K", "uniform starting temperature"),
        Param("duration", "60 s", "s", "length of the heat pulse", lo=1, hi=600),
        Param("emissivity", "0.85", "", "surface emissivity (for the radiative-"
              "equilibrium reference only)", lo=0.1, hi=1),
        Param("cfl_number", 0.4, None,
              "mesh Fourier number alpha*dt/dx^2 of the explicit scheme (stable <= 0.5)"),
    ]
    default_resolution = 128
    min_resolution = 8
    primary = ("surface_temperature", "penetration_depth")
    resolution_doc = "grid cells through the shield (time step follows from cfl_number)"

    def validate(self):
        p = self.p
        for key in ("velocity", "air_density", "nose_radius", "shield_thickness",
                    "conductivity", "density", "specific_heat", "initial_temperature",
                    "duration", "emissivity", "cfl_number"):
            if getattr(p, key) <= 0:
                raise ParamError(f"{key} must be positive")
        if p.emissivity > 1:
            raise ParamError("emissivity cannot exceed 1")

    def _flux(self):
        p = self.p
        return K_SUTTON_GRAVES * math.sqrt(p.air_density / p.nose_radius) * p.velocity ** 3

    def _alpha(self):
        p = self.p
        return p.conductivity / (p.density * p.specific_heat)

    def _problem(self, n):
        p = self.p
        g = Grid(n, p.shield_thickness)
        return TransportProblem(g, p.conductivity, C=p.density * p.specific_heat,
                                bcs={"west": neumann(self._flux())})

    def equations(self):
        dT = Q(1, "K")
        return {
            "Sutton-Graves stagnation heating": {
                "heat flux q": Q(1, "W/m^2"),
                "k_sg*sqrt(rho/R)*V^3": Q(K_SUTTON_GRAVES, "kg^0.5/m")
                * (self.q("air_density") / self.q("nose_radius")).sqrt()
                * self.q("velocity") ** 3},
            "heat equation": {
                "storage rho*c*dT/t": self.q("density") * self.q("specific_heat") * dT
                / self.q("duration"),
                "conduction k*dT/L^2": self.q("conductivity") * dT
                / self.q("shield_thickness") ** 2},
            "surface condition": {
                "conduction k*dT/L": self.q("conductivity") * dT
                / self.q("shield_thickness"),
                "heat flux q": Q(1, "W/m^2")}}

    def dimensionless(self):
        alpha = self.q("conductivity") / (self.q("density") * self.q("specific_heat"))
        return {"Fourier number alpha*t/L^2": alpha * self.q("duration")
                / self.q("shield_thickness") ** 2,
                "emissivity": self.q("emissivity")}

    def well_posed(self):
        return self._problem(8).well_posed(steady=False, what="temperature")

    def solve(self, n):
        p = self.p
        prob = self._problem(n)
        g = prob.grid
        q, alpha = self._flux(), self._alpha()
        dt = p.cfl_number * g.dx ** 2 / alpha
        ts = prob.solve_transient(p.initial_temperature, p.duration, dt, theta=0.0)
        T = ts.phi[:, 0]
        t_surf = T[0] + q * (g.dx / 2) / p.conductivity     # known surface gradient
        xs = np.concatenate([[0.0], g.x])
        rise = np.concatenate([[t_surf], T]) - p.initial_temperature
        level = 0.01 * rise[0]
        below = np.nonzero(rise < level)[0]
        if len(below) and below[0] > 0:
            i = below[0]
            depth = xs[i - 1] + (rise[i - 1] - level) / (rise[i - 1] - rise[i]) * (
                xs[i] - xs[i - 1])
        else:
            depth = p.shield_thickness
        t_rad = (q / (p.emissivity * SIGMA)) ** 0.25
        qoi = {"heat_flux": (q, "W/m^2"),
               "surface_temperature": (to_c(t_surf), "degC"),
               "back_face_temperature": (to_c(T[-1]), "degC"),
               "penetration_depth": (depth * 1e3, "mm"),
               "radiative_equilibrium_temperature": (to_c(t_rad), "degC")}
        fields = [Field("temperature through the shield", xs * 1e3,
                        np.concatenate([[t_surf], T]) - 273.15, unit="degC",
                        xlabel="depth [mm]", ylabel="T [degC]"),
                  Field("hottest-cell temperature history", ts.history["t"],
                        ts.history["max"] - 273.15, unit="degC", xlabel="time [s]",
                        ylabel="T [degC]")]
        cfl = (ts.mesh_number / 2, 0.5,
               "explicit Euler mesh Fourier number alpha*dt/dx^2")
        return Solution(qoi, fields, ts.residual, cfl=cfl,
                        residual_note="relative residual of the last explicit update",
                        data={"ts": ts, "t_surf": t_surf, "t_rad": t_rad})

    def _image_series(self, x):
        """Exact rise in a slab (flux at x=0, insulated at x=L) by images."""
        p = self.p
        s = math.sqrt(self._alpha() * p.duration)
        length, tot = p.shield_thickness, 0.0
        for m in range(400):
            a = _ierfc((2 * m * length + x) / (2 * s))
            b = _ierfc((2 * (m + 1) * length - x) / (2 * s))
            tot += a + b
            if a + b < 1e-16 * max(tot, 1e-300):
                break
        return 2 * self._flux() * s / p.conductivity * tot

    def known_solution(self, sol):
        p = self.p
        q, alpha = self._flux(), self._alpha()
        fo = alpha * p.duration / p.shield_thickness ** 2
        rise = sol.data["t_surf"] - p.initial_temperature
        name = "known solution: semi-infinite solid"
        if fo < 0.1:
            semi = 2 * q * math.sqrt(alpha * p.duration / math.pi) / p.conductivity
            first = ck.close(name, ck.UNIVERSAL, rise, semi, 0.01,
                             detail=f"surface rise 2 q sqrt(alpha t/pi)/k (Fo = {fo:.3g})",
                             unit="K")
        else:
            first = ck.skip(name, ck.UNIVERSAL,
                            f"Fourier number {fo:.3g} >= 0.1: the heat has reached the "
                            "back face, the finite-slab series below applies instead")
        exact_s, exact_b = self._image_series(0.0), self._image_series(p.shield_thickness)
        back = sol.qoi["back_face_temperature"][0] - to_c(p.initial_temperature)
        return [first,
                ck.close("known solution: finite slab (surface)", ck.UNIVERSAL, rise,
                         exact_s, 0.01, detail="method-of-images series", unit="K"),
                ck.close("known solution: finite slab (back face)", ck.UNIVERSAL, back,
                         exact_b, 0, 0.002 * exact_s,
                         detail="method-of-images series", unit="K")]

    def domain_checks(self, sol):
        p = self.p
        ts = sol.data["ts"]
        out = heat_checks(ts, lo=p.initial_temperature, generation=0.0,
                          why="heating only: nothing is colder than the initial state")
        q, alpha = self._flux(), self._alpha()
        fo = alpha * p.duration / p.shield_thickness ** 2
        rise = sol.data["t_surf"] - p.initial_temperature
        if fo < 1 / 6:
            # heat-balance integral: parabolic profile over a depth sqrt(6 alpha t)
            est = q * math.sqrt(6 * alpha * p.duration) / (2 * p.conductivity)
            out.append(ck.close(
                NETWORK, ck.HEAT, rise, est, 0.15,
                detail="surface rise vs one-lump penetration layer of depth "
                       "sqrt(6 alpha t) (integral method, known to read ~9% high)",
                unit="K"))
        else:
            est = q * p.duration / (p.density * p.specific_heat * p.shield_thickness)
            mean = float(np.mean(ts.phi)) - p.initial_temperature
            out.append(ck.close(
                NETWORK, ck.HEAT, mean, est, 1e-6,
                detail="heat has filled the slab: mean rise vs single capacitance "
                       "q t / (rho c L)", unit="K"))
        t_rad = sol.data["t_rad"]
        below = sol.data["t_surf"] <= t_rad
        out.append(ck.truth(
            "model validity: no ablation or re-radiation", ck.HEAT, below,
            f"predicted surface {to_c(sol.data['t_surf']):.0f} degC vs radiative "
            f"equilibrium {to_c(t_rad):.0f} degC - "
            + ("re-radiation is not yet dominant; the result is an upper bound" if below
               else "a real surface would radiate this heat away (and ablate); treat "
                    "the surface temperature as a loose upper bound"), warn=True))
        return out
