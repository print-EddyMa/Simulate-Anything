"""Electromagnetics: capacitors, busbars and coils, EMI shielding, antenna arrays.

Statics reuse the conservative transport solver (potential or vector
potential); waves use the Yee FDTD solvers.  The checks are the integral laws
themselves: Gauss, Ampere, charge/current conservation, div B = 0 and the
Poynting energy balance.
"""
from __future__ import annotations

import math

import numpy as np
from scipy.integrate import quad
from scipy.special import hankel2

from .. import checks as ck
from ..core import Field, Param, ParamError, Simulation, Solution, register
from ..solvers import fdtd
from ..solvers.fdtd import C0, EPS0, ETA0, MU0
from ..solvers.transport import Grid, TransportProblem, dirichlet
from ..units import Q, convert

eps0_q = Q(EPS0, "F/m")
mu0_q = Q(MU0, "H/m")
c0_q = Q(C0, "m/s")


def closed_flux(ts, i0, i1, j0, j1) -> float:
    """Net outward flux of -K grad(phi) through the closed surface that bounds
    cells [i0, i1) x [j0, j1).  For electrostatics this is the enclosed charge
    (Gauss); for A_z it is the circulation of H (Ampere)."""
    qx, qy = ts.face_flux()
    g = ts.grid
    return float(np.sum((qx[i1, j0:j1] - qx[i0, j0:j1]) * g.dys[j0:j1])
                 + np.sum((qy[i0:i1, j1] - qy[i0:i1, j0]) * g.dxs[i0:i1]))


def _span(mask):
    """Index bounds (i0, i1, j0, j1) of the cells in a mask."""
    ii, jj = np.nonzero(mask)
    return int(ii.min()), int(ii.max()) + 1, int(jj.min()), int(jj.max()) + 1


def _bilinear(g: Grid, arr, x, y):
    """Bilinear interpolation of a cell-centred array at points (vectorised)."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    i = np.clip(np.searchsorted(g.x, x) - 1, 0, g.nx - 2)
    j = np.clip(np.searchsorted(g.y, y) - 1, 0, g.ny - 2)
    tx = np.clip((x - g.x[i]) / (g.x[i + 1] - g.x[i]), 0, 1)
    ty = np.clip((y - g.y[j]) / (g.y[j + 1] - g.y[j]), 0, 1)
    return ((1 - tx) * (1 - ty) * arr[i, j] + tx * (1 - ty) * arr[i + 1, j]
            + (1 - tx) * ty * arr[i, j + 1] + tx * ty * arr[i + 1, j + 1])


# ---------------------------------------------------------------------------
# Capacitor
# ---------------------------------------------------------------------------
def _electrostatics(g, eps, plus, minus, voltage, bcs=None):
    """Solve div(eps grad V) = 0 with the plates held at +-V/2."""
    value = np.where(plus, voltage / 2, np.where(minus, -voltage / 2, 0.0))
    prob = TransportProblem(g, eps, fixed_mask=plus | minus, fixed_value=value, bcs=bcs)
    return prob, prob.solve_steady()


def _ideal_capacitor(width, layers, voltage, n):
    """Plates spanning the full width with insulated sides: no fringing, so the
    capacitance is exactly eps*w/d (layers in series).  Returns (C, ts)."""
    total = sum(t for t, _ in layers)
    yf, er_rows, pos = [0.0], [], 0.0
    plate = total / 8
    spans = [(plate, None)] + list(layers) + [(plate, None)]
    for t, er in spans:
        cells = 2 if er is None else max(2, round(n * t / total))
        yf.extend(pos + t * np.arange(1, cells + 1) / cells)
        er_rows.extend([1.0 if er is None else er] * cells)
        pos += t
    g = Grid(4, width, yf=yf)
    rows = np.arange(g.ny)
    minus = np.zeros(g.shape, bool)
    plus = np.zeros(g.shape, bool)
    minus[:, rows < 2] = True
    plus[:, rows >= g.ny - 2] = True
    eps = EPS0 * np.asarray(er_rows)[None, :] * np.ones((g.nx, 1))
    _, ts = _electrostatics(g, eps, plus, minus, voltage)
    return ts.fixed_outflow(plus) / voltage, ts, plus, minus


@register
class Capacitor(Simulation):
    name = "capacitor"
    title = "Parallel-plate capacitor with fringing fields (2-D electrostatics)"
    domain = "electromagnetics"
    description = """Two plates at +-V/2 inside a grounded box, with a
    dielectric between them.  Solves div(eps grad V) = 0 and reports the
    capacitance per unit depth from Gauss's law and from the stored energy, how
    much fringing adds over the ideal eps*w/d, and the field in the gap."""
    params = [
        Param("plate_width", "16 mm", "m", "width of each plate", lo=0.002, hi=0.1),
        Param("gap", "4 mm", "m", "distance between the plates", lo=0.0005, hi=0.05),
        Param("plate_thickness", "2 mm", "m", "thickness of each plate", lo=0.0005, hi=0.02),
        Param("relative_permittivity", 1.0, "", "dielectric between the plates "
              "(air 1, PTFE 2.1, FR4 4.4, alumina 9.8)", lo=1, hi=100),
        Param("voltage", "10 V", "V", "potential difference between the plates",
              lo=0.1, hi=1000),
        Param("box_width", "32 mm", "m", "width of the grounded enclosure", lo=0.004, hi=0.5),
        Param("box_height", "32 mm", "m", "height of the grounded enclosure", lo=0.004, hi=0.5),
        Param("fringing", True, None, "True: finite plates in a grounded box; False: "
              "ideal capacitor (plates span the full width, no edge effects)"),
    ]
    default_resolution = 128
    min_resolution = 8
    primary = ("capacitance_gauss",)
    order_hint = 1          # plate corners are field singularities
    resolution_doc = "grid cells across the enclosure width (across the gap if ideal)"

    def validate(self):
        p = self.p
        for k in ("plate_width", "gap", "plate_thickness", "voltage", "box_width",
                  "box_height"):
            if getattr(p, k) <= 0:
                raise ParamError(f"{k} must be positive")
        if p.relative_permittivity < 1:
            raise ParamError("relative_permittivity must be at least 1")
        if p.fringing and (p.plate_width >= p.box_width
                           or p.gap + 2 * p.plate_thickness >= p.box_height):
            raise ParamError("the plates do not fit inside the grounded box "
                             "(contradictory geometry: they would touch the wall)")

    def _ideal_value(self):
        p = self.p
        return EPS0 * p.relative_permittivity * p.plate_width / p.gap

    def _build(self, n):
        p = self.p
        W, H = p.box_width, p.box_height
        ny = max(4, round(n * H / W))
        g = Grid(n, W, ny, H)
        xa, xb = W / 2 - p.plate_width / 2, W / 2 + p.plate_width / 2
        ya, yb = H / 2 - p.gap / 2, H / 2 + p.gap / 2
        plus = g.box(xa, xb, yb, yb + p.plate_thickness)
        minus = g.box(xa, xb, ya - p.plate_thickness, ya)
        gap = g.box(xa, xb, ya, yb)
        if not plus.any() or not minus.any() or gap.any(axis=0).sum() < 2:
            raise ValueError(
                f"grid too coarse: the gap spans {int(gap.any(axis=0).sum())} cells "
                "(need at least 2) - raise the resolution")
        eps = EPS0 * np.where(gap, p.relative_permittivity, 1.0)
        bcs = {s: dirichlet(0.0) for s in ("west", "east", "south", "north")}
        return g, eps, plus, minus, bcs

    def equations(self):
        length = self.q("gap")
        return {
            "Gauss's law div D = rho": {
                "eps*V/L^2": eps0_q * self.q("voltage") / length ** 2,
                "charge density": Q(1, "C/m^3")},
            "capacitance per unit depth": {
                "eps*w/d": eps0_q * self.q("plate_width") / self.q("gap"),
                "Q'/V": Q(1, "C/m") / self.q("voltage")}}

    def dimensionless(self):
        return {"aspect ratio w/d": self.q("plate_width") / self.q("gap"),
                "relative permittivity": self.q("relative_permittivity")}

    def well_posed(self):
        if not self.p.fringing:
            return [ck.truth("boundary conditions complete", ck.MATH, True,
                             "both electrodes hold a potential; side walls carry no flux")]
        g, eps, plus, minus, bcs = self._build(max(self.default_resolution, 32))
        prob = TransportProblem(g, eps, fixed_mask=plus | minus, bcs=bcs)
        return prob.well_posed(what="potential")

    def solve(self, n):
        p = self.p
        if p.fringing:
            g, eps, plus, minus, bcs = self._build(n)
            _, ts = _electrostatics(g, eps, plus, minus, p.voltage, bcs)
        else:
            _, ts, plus, minus = _ideal_capacitor(
                p.plate_width, [(p.gap, p.relative_permittivity)], p.voltage, n)
            g = ts.grid
        prob = ts.problem
        q_plus, q_minus = ts.fixed_outflow(plus), ts.fixed_outflow(minus)
        energy = 0.5 * ts.dissipation()
        ideal = self._ideal_value()
        # normal E at faces (D is continuous, E = D / eps on the free side)
        qx, qy = ts.face_flux()
        e_free = np.where(prob.fixed, np.inf, prob.K)
        ex = np.abs(qx[1:-1, :]) / np.minimum(e_free[:-1, :], e_free[1:, :])
        ey = np.abs(qy[:, 1:-1]) / np.minimum(e_free[:, :-1], e_free[:, 1:])
        ic = g.nx // 2
        jm = int(np.argmin(np.abs(g.yf - 0.5 * (g.yf[0] + g.yf[-1]))))
        cols = [ic - 1, ic] if g.nx % 2 == 0 else [ic]
        gap_field = float(np.mean(np.abs(qy[cols, jm]))) / (EPS0 * p.relative_permittivity)
        c_gauss = q_plus / p.voltage
        qoi = {
            "capacitance_gauss": (c_gauss * 1e12, "pF/m"),
            "capacitance_energy": (2 * energy / p.voltage ** 2 * 1e12, "pF/m"),
            "ideal_capacitance": (ideal * 1e12, "pF/m"),
            "fringing_ratio": (c_gauss / ideal, ""),
            "plate_charge": (q_plus * 1e9, "nC/m"),
            "gap_field": (gap_field / 1e3, "kV/m"),
            "peak_field": (float(max(ex.max(), ey.max())) / 1e3, "kV/m"),
        }
        gx, gy = ts.gradient()
        fields = [Field("potential", g.x * 1e3, ts.phi, g.y * 1e3, "V", "x [mm]", "y [mm]"),
                  Field("field magnitude", g.x * 1e3, np.hypot(gx, gy) / 1e3, g.y * 1e3,
                        "kV/m", "x [mm]", "y [mm]")]
        return Solution(qoi, fields, ts.residual, data={
            "ts": ts, "plus": plus, "minus": minus, "q": (q_plus, q_minus),
            "energy": energy})

    def known_solution(self, sol):
        p = self.p
        if not p.fringing:
            return [ck.close("known solution: ideal parallel plates", ck.UNIVERSAL,
                             sol.qoi["capacitance_gauss"][0], self._ideal_value() * 1e12,
                             1e-8, detail="exact eps*w/d", unit="pF/m")]
        er, w, d = p.relative_permittivity, p.plate_width, p.gap
        c1 = _ideal_capacitor(w, [(d, er)], p.voltage, 16)[0]
        e1, e2 = max(er, 2.0), 1.0
        c2 = _ideal_capacitor(w, [(0.4 * d, e1), (0.6 * d, e2)], p.voltage, 20)[0]
        series = EPS0 * w / (0.4 * d / e1 + 0.6 * d / e2)
        return [ck.close("known solution: ideal parallel plates", ck.UNIVERSAL,
                         c1 * 1e12, EPS0 * er * w / d * 1e12, 1e-8,
                         detail="same solver without fringing vs exact eps*w/d",
                         unit="pF/m"),
                ck.close("known solution: two dielectric layers in series", ck.UNIVERSAL,
                         c2 * 1e12, series * 1e12, 1e-8,
                         detail="same solver vs exact series-capacitor formula",
                         unit="pF/m")]

    def domain_checks(self, sol):
        p = self.p
        ts, plus, minus = sol.data["ts"], sol.data["plus"], sol.data["minus"]
        g = ts.grid
        q_plus, q_minus = sol.data["q"]
        scale = max(abs(q_plus), 1e-300)
        i0, i1, j0, j1 = _span(plus)
        half_gap = (j0 - _span(minus)[3]) // 2
        worst, count = 0.0, 0
        for k in (1, 2, 3, 5, 8):
            box = (max(i0 - k, 0), min(i1 + k, g.nx), j0 - min(k, half_gap),
                   min(j1 + k, g.ny))
            worst = max(worst, abs(closed_flux(ts, *box) - q_plus) / scale)
            count += 1
        out = [ck.small("Gauss's law over closed surfaces", ck.EM, worst, 1e-8,
                        f"flux of D through {count} nested closed surfaces around the "
                        f"+ plate vs its charge {q_plus * 1e9:.5g} nC/m; worst relative "
                        "mismatch")]
        both = closed_flux(ts, 0, g.nx, 0, g.ny)
        empty = None
        if p.fringing and i0 >= 3 and _span(minus)[2] >= 3:
            empty = abs(closed_flux(ts, 1, i0 - 1, 1, _span(minus)[2] - 1)) / scale
        out.append(ck.small(
            "Gauss's law: surface enclosing no / all conductors", ck.EM,
            max(abs(both - (q_plus + q_minus)) / scale, empty or 0.0), 1e-8,
            ("a surface around empty space carries no net flux; " if empty is not None
             else "") + "a surface around both plates carries Q+ + Q-; "
            "worst mismatch relative to Q+"))
        q_box = sum(ts.boundary_inflow().values())
        out.append(ck.small(
            "charge conservation", ck.EM, (q_plus + q_minus + q_box) / scale, 1e-8,
            f"Q+ {q_plus * 1e9:+.5g}, Q- {q_minus * 1e9:+.5g}, induced on the box "
            f"{q_box * 1e9:+.5g} nC/m; relative sum"))
        work = 0.5 * (q_plus - q_minus) * p.voltage / 2      # half sum of Q_i V_i
        out.append(ck.close(
            "field energy consistent with charge", ck.EM, sol.data["energy"], work, 1e-8,
            detail="integral of eps E^2 / 2 vs half the sum of Q V", unit="J/m"))
        ratio = sol.qoi["fringing_ratio"][0]
        out.append(ck.truth(
            "capacitance not below the ideal value", ck.EM, ratio >= 1 - 1e-9,
            f"C / (eps w / d) = {ratio:.4f}: fringing fields can only add capacitance"))
        return out

    def symmetry_checks(self, sol):
        ts = sol.data["ts"]
        out = ts.symmetry_checks()
        prob = ts.problem
        value = np.where(prob.fixed, prob.fixed_value, 0.0)
        if (np.allclose(value, -np.flip(value, 1))
                and np.allclose(prob.K, np.flip(prob.K, 1))
                and np.allclose(ts.grid.dys, ts.grid.dys[::-1])):
            out.append(ck.symmetry("antisymmetry about the gap mid-plane", ts.phi,
                                   -np.flip(ts.phi, 1), 1e-8,
                                   "plates at +V/2 and -V/2 mirror each other"))
        return out


# ---------------------------------------------------------------------------
# Magnetostatics
# ---------------------------------------------------------------------------
def _vector_potential(g, current_density, nu):
    """Solve div(nu grad A_z) = -J with A = 0 on the outer box."""
    bcs = {s: dirichlet(0.0) for s in ("west", "east", "south", "north")}
    prob = TransportProblem(g, nu, source=current_density, bcs=bcs)
    return prob.solve_steady()


def _b_cells(ts):
    """Cell-centred flux density B = curl(A z) = (dA/dy, -dA/dx)."""
    gx, gy = ts.gradient()
    return gy, -gx


def _div_b(ts) -> float:
    """Discrete divergence of B at the cell corners, relative to |B|/dx."""
    g, A = ts.grid, ts.phi
    bx = np.diff(A, axis=1) / np.diff(g.y)[None, :]        # at y-faces
    by = -np.diff(A, axis=0) / np.diff(g.x)[:, None]       # at x-faces
    div = (np.diff(bx, axis=0) / np.diff(g.x)[:, None]
           + np.diff(by, axis=1) / np.diff(g.y)[None, :])
    bmax = max(float(np.max(np.abs(bx))), float(np.max(np.abs(by))), 1e-300)
    return float(np.max(np.abs(div))) * g.dx / bmax


def coax_benchmark(n=128, a=2e-3, b=6e-3, c=7e-3, current=10.0) -> dict:
    """Coaxial cable through the same solver: inner conductor radius a, return
    shell b..c.  The field is known exactly and vanishes outside the shell."""
    half = 1.2 * c
    g = Grid(n, 2 * half, n, 2 * half)
    X, Y = g.mesh()
    r = np.hypot(X - half, Y - half)
    inner, shell = r < a, (r >= b) & (r < c)
    J = np.zeros(g.shape)
    J[inner] = current / float(np.sum(g.vol[inner]))
    J[shell] = -current / float(np.sum(g.vol[shell]))
    ts = _vector_potential(g, J, 1.0 / MU0)
    bx, by = _b_cells(ts)
    ang = np.linspace(0, 2 * math.pi, 24, endpoint=False) + 0.1

    def ring(radius):
        x, y = half + radius * np.cos(ang), half + radius * np.sin(ang)
        bphi = -_bilinear(g, bx, x, y) * np.sin(ang) + _bilinear(g, by, x, y) * np.cos(ang)
        return float(np.mean(bphi)), float(np.max(np.abs(bphi - np.mean(bphi))))

    def h_exact(rr):
        if rr < a:
            return current * rr / (2 * math.pi * a * a)
        if rr < b:
            return current / (2 * math.pi * rr)
        return current / (2 * math.pi * rr) * (c * c - rr * rr) / (c * c - b * b)

    energy = sum(quad(lambda rr: 0.5 * MU0 * h_exact(rr) ** 2 * 2 * math.pi * rr, lo, hi)[0]
                 for lo, hi in ((0, a), (a, b), (b, c)))
    r_gap, r_in = 0.5 * (a + b), 0.5 * a
    return {
        "b_gap": ring(r_gap)[0], "b_gap_exact": MU0 * h_exact(r_gap),
        "b_gap_ripple": ring(r_gap)[1] / (MU0 * h_exact(r_gap)),
        "b_in": ring(r_in)[0], "b_in_exact": MU0 * h_exact(r_in),
        "b_outside": abs(ring(0.5 * (c + half))[0]) / (MU0 * h_exact(r_gap)),
        "L": float(np.sum(J * ts.phi * g.vol)) / current ** 2,
        "L_exact": 2 * energy / current ** 2,
    }


def _conductors(spec) -> list:
    out = []
    for i, c in enumerate(spec or []):
        try:
            out.append({"name": str(c.get("name", f"conductor{i + 1}")),
                        "x": convert(c["x"], "m"), "y": convert(c["y"], "m"),
                        "w": convert(c["w"], "m"), "h": convert(c["h"], "m"),
                        "current": convert(c["current"], "A")})
        except KeyError as exc:
            raise ParamError(f"conductor {i + 1} is missing field {exc}") from exc
    return out


@register
class Magnetostatics(Simulation):
    name = "magnetostatics"
    title = "Busbars, coils and cores (2-D magnetostatics, vector potential)"
    domain = "electromagnetics"
    description = """Magnetic field of long straight conductors of rectangular
    cross-section (busbar pair, coil sides, a winding window with an iron
    core).  Solves div((1/mu) grad A_z) = -J with A = 0 on a far box and reports
    inductance per unit length, peak flux density and the force between
    conductors.  Currents are positive out of the page."""
    params = [
        Param("conductors", [
            {"name": "go", "x": "65 mm", "y": "80 mm", "w": "10 mm", "h": "40 mm",
             "current": "100 A"},
            {"name": "return", "x": "95 mm", "y": "80 mm", "w": "10 mm", "h": "40 mm",
             "current": "-100 A"}], None,
            "list of {name, x, y (centre), w, h, current} with units"),
        Param("core", None, None,
              "optional iron region: {x, y (centre), w, h, mu_r}"),
        Param("box_width", "160 mm", "m", "width of the far boundary box", lo=0.01, hi=2),
        Param("box_height", "160 mm", "m", "height of the far boundary box", lo=0.01, hi=2),
    ]
    default_resolution = 128
    min_resolution = 8
    primary = ("inductance_per_length",)
    resolution_doc = "grid cells across the box width"

    def validate(self):
        p = self.p
        self.cond = _conductors(p.conductors)
        if not self.cond:
            raise ParamError("at least one conductor is required")
        for c in self.cond:
            if c["w"] <= 0 or c["h"] <= 0:
                raise ParamError(f"conductor '{c['name']}' needs a positive cross-section")
            if (c["x"] - c["w"] / 2 <= 0 or c["x"] + c["w"] / 2 >= p.box_width
                    or c["y"] - c["h"] / 2 <= 0 or c["y"] + c["h"] / 2 >= p.box_height):
                raise ParamError(f"conductor '{c['name']}' touches or leaves the box")
        self.core = None
        if p.core:
            try:
                self.core = {k: convert(p.core[k], "m") for k in ("x", "y", "w", "h")}
                self.core["mu_r"] = float(p.core["mu_r"])
            except KeyError as exc:
                raise ParamError(f"core is missing field {exc}") from exc
            if self.core["mu_r"] <= 0:
                raise ParamError("core mu_r must be positive")

    def _loop_current(self):
        pos = sum(c["current"] for c in self.cond if c["current"] > 0)
        return pos if pos > 0 else max(abs(c["current"]) for c in self.cond)

    def _build(self, n):
        p = self.p
        ny = max(4, round(n * p.box_height / p.box_width))
        g = Grid(n, p.box_width, ny, p.box_height)
        J, cover = np.zeros(g.shape), []
        for c in self.cond:
            w = g.overlap(c["x"] - c["w"] / 2, c["x"] + c["w"] / 2,
                          c["y"] - c["h"] / 2, c["y"] + c["h"] / 2)
            J += w * c["current"] / (c["w"] * c["h"])
            cover.append(w)
        mu_r = np.ones(g.shape)
        if self.core:
            k = self.core
            mu_r[g.box(k["x"] - k["w"] / 2, k["x"] + k["w"] / 2,
                       k["y"] - k["h"] / 2, k["y"] + k["h"] / 2)] = k["mu_r"]
        return g, J, cover, 1.0 / (MU0 * mu_r)

    def equations(self):
        length = self.q("box_width")
        return {
            "Ampere's law curl H = J": {
                "B/(mu0*L)": Q(1, "T") / (mu0_q * length),
                "current density I/A": Q(1, "A") / Q(1, "m^2")},
            "energy per unit length": {
                "L'*I^2": Q(1, "H/m") * Q(1, "A") ** 2,
                "B^2*area/mu0": Q(1, "T") ** 2 * Q(1, "m^2") / mu0_q}}

    def dimensionless(self):
        return {"box aspect ratio": self.q("box_width") / self.q("box_height")}

    def well_posed(self):
        g, J, _, nu = self._build(32)
        bcs = {s: dirichlet(0.0) for s in ("west", "east", "south", "north")}
        out = TransportProblem(g, nu, source=J, bcs=bcs).well_posed(what="vector potential")
        net = sum(c["current"] for c in self.cond)
        loop = self._loop_current()
        out.append(ck.truth(
            "current return path", ck.MATH, abs(net) <= 1e-9 * loop,
            "currents sum to zero, so the circuit closes inside the domain"
            if abs(net) <= 1e-9 * loop else
            f"net current {net:.4g} A returns through the outer box: inductance and "
            "energy then depend on the box size", warn=True))
        return out

    def solve(self, n):
        g, J, cover, nu = self._build(n)
        ts = _vector_potential(g, J, nu)
        A = ts.phi
        bx, by = _b_cells(ts)
        energy = 0.5 * float(np.sum(J * A * g.vol))
        loop = self._loop_current()
        gx, gy = ts.gradient()
        first = self.cond[0]
        jc = cover[0] * first["current"] / (first["w"] * first["h"])
        fx = float(np.sum(jc * gx * g.vol))         # F = J x B = J grad(A)
        fy = float(np.sum(jc * gy * g.vol))
        bmag = np.hypot(bx, by)
        qoi = {
            "inductance_per_length": (2 * energy / loop ** 2 * 1e6, "uH/m"),
            "stored_energy": (energy, "J/m"),
            "peak_flux_density": (float(bmag.max()) * 1e3, "mT"),
            "force_x_per_length": (fx, "N/m"),
            "force_y_per_length": (fy, "N/m"),
        }
        fields = [Field("flux density |B|", g.x * 1e3, bmag * 1e3, g.y * 1e3, "mT",
                        "x [mm]", "y [mm]"),
                  Field("vector potential A_z (flux lines are its contours)",
                        g.x * 1e3, A * 1e6, g.y * 1e3, "uWb/m", "x [mm]", "y [mm]")]
        return Solution(qoi, fields, ts.residual, data={
            "ts": ts, "J": J, "cover": cover, "energy": energy})

    def known_solution(self, sol):
        c1, c2 = coax_benchmark(64), coax_benchmark(128)
        e1 = abs(c1["L"] / c1["L_exact"] - 1)
        e2 = abs(c2["L"] / c2["L_exact"] - 1)
        return [
            ck.close("known solution: coax field between the conductors", ck.UNIVERSAL,
                     c2["b_gap"] * 1e3, c2["b_gap_exact"] * 1e3, 5e-3,
                     detail="same solver on a coaxial cable vs mu0 I / (2 pi r)",
                     unit="mT"),
            ck.close("known solution: coax field inside the inner conductor", ck.UNIVERSAL,
                     c2["b_in"] * 1e3, c2["b_in_exact"] * 1e3, 2e-2,
                     detail="vs mu0 I r / (2 pi a^2)", unit="mT"),
            ck.small("known solution: no field outside the coax", ck.UNIVERSAL,
                     c2["b_outside"], 1e-2,
                     "field outside the return shell relative to the gap field"),
            ck.Check("known solution: coax inductance", ck.UNIVERSAL,
                     ck.PASS if e2 < 0.02 and e2 < e1 else ck.FAIL,
                     f"L' = {c2['L'] * 1e9:.4g} nH/m vs exact {c2['L_exact'] * 1e9:.4g} "
                     f"nH/m; error {100 * e1:.2g}% -> {100 * e2:.2g}% on grid refinement "
                     "(round conductors on a square grid; limit 2%)", e2, 0.0, 0.02)]

    def domain_checks(self, sol):
        ts, J, cover = sol.data["ts"], sol.data["J"], sol.data["cover"]
        g = ts.grid
        loop = self._loop_current()
        out = [ck.small("div B = 0", ck.EM, _div_b(ts), 1e-9,
                        "discrete divergence of B = curl A at every cell corner, "
                        "relative to |B|/dx")]
        worst, count = 0.0, 0
        boxes = [(0, g.nx, 0, g.ny), (1, max(2, g.nx // 8), 1, max(2, g.ny // 8))]
        for w in cover:
            i0, i1, j0, j1 = _span(w > 0)
            for k in (1, 2, 4, 8):
                boxes.append((max(i0 - k, 0), min(i1 + k, g.nx),
                              max(j0 - k, 0), min(j1 + k, g.ny)))
        for i0, i1, j0, j1 in boxes:
            enclosed = float(np.sum((J * g.vol)[i0:i1, j0:j1]))
            worst = max(worst, abs(closed_flux(ts, i0, i1, j0, j1) - enclosed) / loop)
            count += 1
        out.append(ck.small(
            "Ampere's law on closed contours", ck.EM, worst, 1e-8,
            f"circulation of H on {count} closed contours vs the current they "
            "enclose; worst mismatch relative to the loop current"))
        deposited = [float(np.sum(w * g.vol)) * c["current"] / (c["w"] * c["h"])
                     for w, c in zip(cover, self.cond)]
        err = max(abs(d - c["current"]) for d, c in zip(deposited, self.cond)) / loop
        out.append(ck.small(
            "charge conservation: currents on the grid", ck.EM, err, 1e-9,
            "current carried by every conductor on the grid vs the specified "
            f"current (net {sum(deposited):+.4g} A); worst relative error"))
        field_energy = 0.5 * ts.dissipation()
        out.append(ck.close(
            "field energy consistent with current", ck.EM, field_energy,
            sol.data["energy"], 1e-8,
            detail="integral of B.H / 2 vs integral of J.A / 2", unit="J/m"))
        return out

    def symmetry_checks(self, sol):
        ts, J = sol.data["ts"], sol.data["J"]
        out = ts.symmetry_checks()
        for axis, label in ((0, "x"), (1, "y")):
            if (np.any(J) and np.allclose(J, -np.flip(J, axis), atol=1e-12 * np.abs(J).max())
                    and np.allclose(ts.problem.K, np.flip(ts.problem.K, axis))):
                out.append(ck.symmetry(
                    f"antisymmetry about the {label} mid-plane", ts.phi,
                    -np.flip(ts.phi, axis), 1e-8,
                    "go and return conductors mirror each other"))
        return out


# ---------------------------------------------------------------------------
# EMI shield (1-D FDTD)
# ---------------------------------------------------------------------------
@register
class EmShield(Simulation):
    name = "em_shield"
    title = "EMI shielding of a lossy slab (1-D FDTD plane wave)"
    domain = "electromagnetics"
    description = """A plane-wave pulse hits a sheet of conductive or dielectric
    material at normal incidence.  Maxwell's equations are marched in time
    (Yee FDTD) with and without the sheet; the spectra at the chosen frequency
    give reflection, transmission, absorption and shielding effectiveness.
    Suited to lossy composites, coatings and absorbers - a solid metal sheet at
    GHz has a skin depth far too small to resolve and is rejected up front."""
    params = [
        Param("frequency", "1 GHz", "Hz", "frequency of interest", lo=1e6, hi=1e11),
        Param("thickness", "10 mm", "m", "sheet thickness", lo=1e-4, hi=0.5),
        Param("relative_permittivity", 4.0, "", "relative permittivity of the sheet",
              lo=1, hi=100),
        Param("conductivity", "5 S/m", "S/m",
              "conductivity (carbon-loaded plastic 1-100, sea water 4, copper 5.8e7)",
              lo=0, hi=1000),
        Param("courant", 0.5, None, "Courant number c*dt/dx (must not exceed 1)",
              lo=0.1, hi=1.2),
    ]
    default_resolution = 64
    min_resolution = 4
    primary = ("transmission",)
    resolution_doc = "grid cells per wavelength inside the sheet (or per 8 thicknesses)"

    MAX_STEPS = 100_000        # at the coarsest acceptable grid (resolution 16)

    def validate(self):
        p = self.p
        if p.frequency <= 0 or p.thickness <= 0:
            raise ParamError("frequency and thickness must be positive")
        if p.relative_permittivity < 1 or p.conductivity < 0:
            raise ParamError("need relative_permittivity >= 1 and conductivity >= 0")
        if p.courant <= 0:
            raise ParamError("courant must be positive")

    # -- material and grid ---------------------------------------------------
    def _material(self):
        p = self.p
        r_ex, t_ex, n_c = fdtd.slab_exact(p.frequency, p.relative_permittivity,
                                          p.conductivity, p.thickness)
        k0 = 2 * math.pi * p.frequency / C0
        alpha = k0 * abs(n_c.imag)
        return {"R": r_ex, "T": t_ex, "n": n_c, "lam0": C0 / p.frequency,
                "lam_m": C0 / p.frequency / n_c.real, "alpha": alpha,
                "delta": 1 / alpha if alpha > 0 else math.inf}

    def _layout(self, n):
        p, m = self.p, self._material()
        ell = min(m["lam_m"], 8 * p.thickness)
        base = max(1, round(16 * p.thickness / ell))
        n_slab = max(1, round(base * n / 16))
        dx = p.thickness / n_slab
        tau = 1.0 / p.frequency
        t0 = 3.5 * tau
        r12 = abs((1 - m["n"]) / (1 + m["n"]))
        rho = r12 ** 2 * math.exp(-2 * m["alpha"] * p.thickness)
        trips = 1 if rho < 1e-7 else min(400, math.ceil(math.log(1e-7) / math.log(rho)))
        t_end = 2 * t0 + trips * 2 * p.thickness * m["n"].real / C0 + 2 / p.frequency
        steps = int(math.ceil(t_end / (p.courant * dx / C0)))
        return {"n_slab": n_slab, "dx": dx, "tau": tau, "t0": t0, "steps": steps}

    def equations(self):
        e, h = Q(1, "V/m"), Q(1, "A/m")
        length, time = c0_q / self.q("frequency"), 1 / self.q("frequency")
        return {
            "Faraday's law": {"dE/dx": e / length, "mu0*dH/dt": mu0_q * h / time},
            "Ampere-Maxwell law": {
                "dH/dx": h / length, "eps*dE/dt": eps0_q * e / time,
                "sigma*E": self.q("conductivity") * e}}

    def dimensionless(self):
        w = 2 * math.pi * self.q("frequency")
        m = self._material()
        out = {"loss tangent sigma/(w*eps)": self.q("conductivity") / (
            w * eps0_q * self.q("relative_permittivity")),
            "thickness / free-space wavelength": self.q("thickness") * self.q("frequency") / c0_q}
        if math.isfinite(m["delta"]):
            out["thickness / skin depth"] = self.q("thickness") / Q(m["delta"], "m")
        return out

    def well_posed(self):
        p, m = self.p, self._material()
        steps = self._layout(16)["steps"]
        se = -10 * math.log10(max(m["T"], 1e-300))
        ok = steps <= self.MAX_STEPS
        if ok:
            msg = (f"wavelength in the sheet {m['lam_m'] * 1e3:.4g} mm, skin depth "
                   f"{m['delta'] * 1e3:.4g} mm: resolvable with an explicit time march")
        else:
            msg = (f"under-resolved: the skin depth ({m['delta'] * 1e6:.3g} um) is "
                   f"{m['lam0'] / m['delta']:.2g}x smaller than the free-space "
                   f"wavelength, so even the coarsest acceptable grid needs "
                   f"{steps:.2g} time steps. A solid metal at this frequency cannot be "
                   "resolved by explicit FDTD - use the exact slab formula (it gives "
                   f"SE = {se:.0f} dB here) or a surface-impedance model")
        return [ck.truth("resolvable: cells per skin depth and wavelength", ck.MATH,
                         ok, msg)]

    def solve(self, n):
        p, m, lay = self.p, self._material(), self._layout(n)
        if lay["steps"] > 6 * self.MAX_STEPS:
            raise ValueError(f"resolution {n} needs {lay['steps']:.2g} time steps - "
                             "lower the resolution")
        dx, ns = lay["dx"], lay["n_slab"]
        gap, pad = 8, 10
        i_src = pad
        i_ref = i_src + gap
        ia = i_ref + gap
        ib = ia + ns
        i_tr = ib + gap
        size = i_tr + pad + 1
        eps = np.ones(size)
        sig = np.zeros(size)
        eps[ia + 1:ib], sig[ia + 1:ib] = p.relative_permittivity, p.conductivity
        eps[[ia, ib]] = 0.5 * (1 + p.relative_permittivity)     # interface nodes
        sig[[ia, ib]] = 0.5 * p.conductivity

        def source(t):
            return fdtd.gauss_pulse(t, p.frequency, lay["tau"], lay["t0"])

        run = fdtd.run_1d(eps, sig, dx, p.courant, i_src, source, lay["steps"],
                          [i_ref, i_tr])
        ref = fdtd.run_1d(np.ones(size), np.zeros(size), dx, p.courant, i_src, source,
                          lay["steps"], [i_ref, i_tr])
        with np.errstate(all="ignore"):
            e_run = fdtd.dft(run.E, run.t, p.frequency)
            e_ref = fdtd.dft(ref.E, ref.t, p.frequency)
            trans = float(abs(e_run[1] / e_ref[1]) ** 2)
            refl = float(abs((e_run[0] - e_ref[0]) / e_ref[0]) ** 2)
            u_inc = float(np.sum(ref.E[:, 0] * ref.H[:, 0])) * run.dt
            u_ref = -float(np.sum((run.E[:, 0] - ref.E[:, 0])
                                  * (run.H[:, 0] - ref.H[:, 0]))) * run.dt
            u_tr = float(np.sum(run.E[:, 1] * run.H[:, 1])) * run.dt
            fwd = 0.5 * (ref.E[:, 1] + ETA0 * ref.H[:, 1])
            bwd = 0.5 * (ref.E[:, 1] - ETA0 * ref.H[:, 1])
            abc = float(np.sum(bwd ** 2) / np.sum(fwd ** 2))
            se = -10 * math.log10(trans) if trans > 0 else math.nan
            residual = run.energy_left / u_inc if u_inc else math.nan
        qoi = {
            "shielding_effectiveness": (se, "dB"),
            "transmission": (trans, ""),
            "reflection": (refl, ""),
            "absorption": (1 - refl - trans, ""),
            "wavelength_in_sheet": (m["lam_m"] * 1e3, "mm"),
        }
        if math.isfinite(m["delta"]):
            qoi["skin_depth"] = (m["delta"] * 1e3, "mm")
        shown = min(len(run.t), int(14 / p.frequency / run.dt))
        ts = np.linspace(0, run.t[shown - 1], 160)

        def trace(sig_):
            return np.interp(ts, run.t[:shown], np.nan_to_num(sig_[:shown]))

        fields = [Field("pulse at the probes", ts * 1e9, {
            "incident": trace(ref.E[:, 0]),
            "reflected": trace(run.E[:, 0] - ref.E[:, 0]),
            "transmitted": trace(run.E[:, 1])}, unit="V/m", xlabel="time [ns]",
            ylabel="E [arbitrary]")]
        return Solution(
            qoi, fields, residual,
            "field energy still on the grid when the march stops, relative to the "
            "incident pulse", 1e-4,
            cfl=(p.courant, 1.0, "Courant number c dt/dx of the explicit Yee scheme"),
            data={"u": (u_inc, u_ref, u_tr, run.absorbed), "abc": abc, "dx": dx,
                  "material": m})

    def known_solution(self, sol):
        m = sol.data["material"]
        se = -10 * math.log10(max(m["T"], 1e-300))
        return [
            ck.close("known solution: transmission", ck.UNIVERSAL,
                     sol.qoi["transmission"][0], m["T"], 0.02, 1e-9,
                     detail="exact Fresnel / transfer-matrix slab formula"),
            ck.close("known solution: reflection", ck.UNIVERSAL,
                     sol.qoi["reflection"][0], m["R"], 0.02, 2e-3,
                     detail="exact Fresnel / transfer-matrix slab formula"),
            ck.close("known solution: shielding effectiveness", ck.UNIVERSAL,
                     sol.qoi["shielding_effectiveness"][0], se, 0, 0.1,
                     detail="exact slab formula", unit="dB")]

    def domain_checks(self, sol):
        u_inc, u_ref, u_tr, u_abs = sol.data["u"]
        m, dx = sol.data["material"], sol.data["dx"]
        total = (u_ref + u_tr + u_abs) / u_inc if u_inc else math.nan
        refl, trans = sol.qoi["reflection"][0], sol.qoi["transmission"][0]
        out = [
            ck.close("Poynting energy balance", ck.EM, total, 1.0, 5e-3,
                     detail=f"reflected {u_ref / u_inc:.4f} + transmitted "
                     f"{u_tr / u_inc:.4f} + ohmic loss {u_abs / u_inc:.4f} (integral of "
                     "sigma E^2) as fractions of the incident pulse energy"),
            ck.truth("passive sheet: 0 <= R, T and R + T <= 1", ck.EM,
                     bool(refl >= -1e-6 and trans >= 0 and refl + trans <= 1 + 1e-3),
                     f"R = {refl:.4g}, T = {trans:.4g}, R + T = {refl + trans:.4g}"),
            ck.skip("div B = 0", ck.EM, "a plane wave in 1-D has a single transverse "
                    "B component that does not vary along itself"),
            ck.small("boundary handling: absorbing-end reflection", ck.MATH,
                     sol.data["abc"], 1e-4,
                     "backward-travelling energy in the empty reference run, "
                     "relative to the forward pulse"),
        ]
        per_wave = m["lam_m"] / dx
        out.append(ck.truth(
            "resolution: cells per wavelength in the sheet", ck.MATH, per_wave >= 12,
            f"{per_wave:.3g} cells per wavelength (need at least 12)"))
        if math.isfinite(m["delta"]):
            per_skin = m["delta"] / dx
            out.append(ck.truth(
                "resolution: cells per skin depth", ck.MATH, per_skin >= 2,
                f"{per_skin:.3g} cells per skin depth (need at least 2)"))
        else:
            out.append(ck.skip("resolution: cells per skin depth", ck.MATH,
                               "lossless sheet has no skin depth"))
        return out


# ---------------------------------------------------------------------------
# Antenna array (2-D FDTD)
# ---------------------------------------------------------------------------
def array_factor(theta, n, kd, delta):
    """Normalised |AF| of n isotropic elements; theta from the array axis."""
    psi = kd * np.cos(theta) + delta
    den = np.sin(psi / 2)
    safe = np.abs(den) > 1e-9
    return np.abs(np.where(safe, np.sin(n * psi / 2) / np.where(safe, den, 1.0), n)) / n


def _beam(power_db_ref, pattern):
    """(direction, half-power width) in degrees from a pattern sampled every
    degree on the full circle; the main lobe is searched in [0, 180]."""
    m = len(pattern)
    step = 360.0 / m
    k = int(np.argmax(pattern[: m // 2 + 1]))
    a, b, c = pattern[(k - 1) % m], pattern[k], pattern[(k + 1) % m]
    den = a - 2 * b + c
    shift = 0.5 * (a - c) / den if den != 0 else 0.0
    peak = b - 0.25 * (a - c) * shift
    direction = min(max((k + shift) * step, 0.0), 180.0)
    level = peak * power_db_ref

    def crossing(sign):
        prev = b
        for s in range(1, m):
            cur = pattern[(k + sign * s) % m]
            if cur < level:
                return s - 1 + (prev - level) / (prev - cur)
            prev = cur
        return m / 2

    width = min((crossing(1) + crossing(-1)) * step, 360.0)
    return direction, width


@register
class AntennaArray(Simulation):
    name = "antenna_array"
    title = "Phased antenna array radiation pattern (2-D FDTD)"
    domain = "electromagnetics"
    description = """N parallel line-current antennas in a row, fed with a
    progressive phase shift.  Maxwell's equations (TMz) are marched in time on
    a Yee grid; the field on a circle 6 wavelengths out gives the radiation
    pattern, beam direction and beamwidth.  Angles are measured from the
    array axis (90 deg = broadside)."""
    params = [
        Param("frequency", "2.4 GHz", "Hz", "operating frequency", lo=1e8, hi=6e10),
        Param("n_elements", 4, None, "number of elements (1-12)", lo=1, hi=12),
        Param("spacing", "62.5 mm", "m", "element spacing (half a wavelength is typical)",
              lo=0.001, hi=1.0),
        Param("phase_shift", "0 deg", "rad",
              "phase advance from one element to the next (steers the beam)",
              lo=-math.pi, hi=math.pi),
        Param("courant", 0.5, None, "Courant number c*dt/dx (2-D limit 0.707)",
              lo=0.1, hi=0.9),
    ]
    default_resolution = 24
    min_resolution = 5
    primary = ("half_power_beamwidth", "directivity")
    resolution_doc = "grid cells per wavelength"

    R1, RP, R2 = 3.0, 6.0, 6.5      # inner contour, probe circle, outer contour [wavelengths]

    def validate(self):
        p = self.p
        if p.frequency <= 0 or p.spacing <= 0 or p.courant <= 0:
            raise ParamError("frequency, spacing and courant must be positive")
        if not 1 <= p.n_elements <= 12:
            raise ParamError("n_elements must be between 1 and 12")
        lam = C0 / p.frequency
        if (p.n_elements - 1) * p.spacing / 2 > 2 * lam:
            raise ParamError(
                f"array is {(p.n_elements - 1) * p.spacing / lam:.3g} wavelengths long; "
                "this template measures the pattern 6 wavelengths out and handles "
                "arrays up to 4 wavelengths")

    def _kd(self):
        return 2 * math.pi * self.p.frequency / C0 * self.p.spacing

    def _positions(self):
        p = self.p
        return [(k - (p.n_elements - 1) / 2) * p.spacing for k in range(p.n_elements)]

    def _timing(self, n):
        p = self.p
        lam = C0 / p.frequency
        dx = lam / n
        tau = 1.0 / p.frequency
        t0 = 3.5 * tau
        t_end = 2 * t0 + (math.sqrt(2) * self.R2 + 1.5) * lam / C0
        half = (t_end * C0 + self.R2 * lam) / 2 + 0.5 * lam
        dt = p.courant * dx / C0
        return {"lam": lam, "dx": dx, "tau": tau, "t0": t0, "t_end": t_end,
                "n_half": int(math.ceil(half / dx)), "half": half,
                "steps": int(math.ceil(t_end / dt)), "dt": dt}

    def equations(self):
        e = Q(1, "V/m")
        lam = c0_q / self.q("frequency")
        return {"wave equation": {
            "d2E/dx2": e / lam ** 2,
            "(1/c^2) d2E/dt2": e * self.q("frequency") ** 2 / c0_q ** 2,
            "mu0*dJ/dt": mu0_q * Q(1, "A/m^2") * self.q("frequency")}}

    def dimensionless(self):
        return {"electrical spacing k*d": 2 * math.pi * self.q("frequency")
                * self.q("spacing") / c0_q,
                "phase shift per element": self.q("phase_shift")}

    def well_posed(self):
        kd, delta = self._kd(), self.p.phase_shift
        out = [ck.truth("boundary conditions complete", ck.MATH, True,
                        "radiation into open space: the outer wall is kept causally "
                        "disconnected from every probe (time gating)")]
        if self.p.n_elements > 1:
            grating = kd * (1 + abs(delta) / kd if kd else 1) >= 2 * math.pi
            out.append(ck.truth(
                "model validity: no grating lobes", ck.MATH, not grating,
                f"k d = {kd:.3g} rad" + (": spacing is wide enough for a second full "
                                          "lobe (grating lobe)" if grating else
                                          ": single main lobe"), warn=True))
        return out

    def solve(self, n):
        p, tm = self.p, self._timing(n)
        nodes = (2 * tm["n_half"] + 1) ** 2
        if nodes * tm["steps"] > 1.5e9:
            raise ValueError(f"resolution {n} needs {nodes:.2g} nodes x {tm['steps']} "
                             "steps - lower the resolution")
        lam, dx = tm["lam"], tm["dx"]
        xs = self._positions()
        sources = [(x, 0.0, (lambda t, ph=k * p.phase_shift: fdtd.gauss_pulse(
            t, p.frequency, tm["tau"], tm["t0"], ph))) for k, x in enumerate(xs)]
        theta = np.radians(np.arange(360.0))
        pts = np.c_[self.RP * lam * np.cos(theta), self.RP * lam * np.sin(theta)]
        m1, m2 = round(self.R1 * n), round(self.R2 * n)
        snap = int((tm["t0"] + 4.5 * lam / C0) / tm["dt"])
        run = fdtd.run_tmz(tm["n_half"], dx, p.courant, p.frequency, tm["steps"],
                           sources, [m1, m2], pts, snapshot_step=snap)
        k0 = 2 * math.pi / lam
        exact = np.zeros(len(theta), complex)
        for k, x in enumerate(xs):
            dist = np.hypot(pts[:, 0] - x, pts[:, 1])
            exact += (-(2 * math.pi * p.frequency * MU0 / 4) * run.source_dft[k]
                      * hankel2(0, k0 * dist))
        with np.errstate(all="ignore"):
            amp = np.abs(run.probe_dft)
            pattern = amp / amp.max()
            af = array_factor(theta, p.n_elements, self._kd(), p.phase_shift)
            if run.finite and np.all(np.isfinite(amp)):
                direction, width = _beam(0.5 ** 0.5, pattern)
                directivity = float(pattern.max() ** 2 / np.mean(pattern ** 2))
                broadside = 20 * math.log10(1.0 / max(float(pattern[90]), 1e-6))
            else:
                direction = width = directivity = broadside = math.nan
            residual = run.energy_inside / run.source_work if run.source_work else math.nan
        qoi = {
            "main_lobe_direction": (direction, "deg"),
            "half_power_beamwidth": (width, "deg"),
            "directivity": (directivity, ""),
            "peak_to_broadside": (broadside, "dB"),
            "radiated_energy": (run.contour_energy[1] * 1e9, "nJ/m"),
        }
        if p.n_elements == 1:           # omnidirectional: no beam to point
            del qoi["main_lobe_direction"], qoi["peak_to_broadside"]
        c = tm["n_half"]
        view = run.snapshot[c - m2:c + m2 + 1, c - m2:c + m2 + 1]
        axis = np.arange(-m2, m2 + 1) * dx / lam
        deg = np.degrees(theta)
        fields = [
            Field("|Ez| snapshot", axis, np.abs(np.nan_to_num(view)), axis, "V/m",
                  "x [wavelengths]", "y [wavelengths]"),
            Field("radiation pattern", deg, {
                "FDTD": np.nan_to_num(pattern),
                "exact line sources": np.abs(exact) / max(np.abs(exact).max(), 1e-300),
                "array factor (far field)": af}, unit="", xlabel="angle from array axis [deg]",
                ylabel="normalised |E|")]
        return Solution(
            qoi, fields, residual,
            "field energy still inside the outer contour when the march stops, "
            "relative to the radiated energy", 1e-4,
            cfl=(p.courant, 1 / math.sqrt(2),
                 "Courant number c dt/dx of the 2-D Yee scheme"),
            data={"run": run, "pattern": pattern, "amp": amp, "exact": np.abs(exact),
                  "af": af, "timing": tm, "theta": theta})

    def known_solution(self, sol):
        p, d = self.p, sol.data
        pattern, exact, af = d["pattern"], d["exact"], d["af"]
        ex_n = exact / exact.max()
        strong = exact > 0.3 * exact.max()
        with np.errstate(all="ignore"):
            rms_exact = float(np.sqrt(np.mean((pattern - ex_n) ** 2)))
            ratio = float(np.mean(d["amp"][strong] / exact[strong]))
            rms_af = float(np.sqrt(np.mean((pattern - af) ** 2)))
        out = [
            ck.small("known solution: pattern of exact line sources", ck.UNIVERSAL,
                     rms_exact, 0.02, "rms difference between the normalised FDTD "
                     "pattern and the Hankel-function solution at the same radius"),
            ck.close("known solution: absolute field amplitude", ck.UNIVERSAL, ratio, 1.0,
                     0.03, detail="FDTD / exact |Ez| on the probe circle (mean over "
                     "the lobes)"),
            ck.small("known solution: array factor", ck.UNIVERSAL, rms_af, 0.06,
                     "rms difference from the far-field array factor (the probe "
                     "circle is 6 wavelengths out, so a few percent is expected)"),
        ]
        cos0 = -p.phase_shift / self._kd()
        if p.n_elements > 1 and abs(cos0) <= 1:
            out.append(ck.close(
                "known solution: beam direction", ck.UNIVERSAL,
                sol.qoi["main_lobe_direction"][0], math.degrees(math.acos(cos0)), 0, 2.0,
                detail="array theory cos(theta0) = -delta / (k d)", unit="deg"))
        else:
            out.append(ck.skip("known solution: beam direction", ck.UNIVERSAL,
                               "a single element is omnidirectional" if p.n_elements == 1
                               else "phase shift beyond k d: no visible main lobe"))
        return out

    def domain_checks(self, sol):
        run, tm = sol.data["run"], sol.data["timing"]
        e1, e2 = run.contour_energy
        lam = tm["lam"]
        first_echo = (2 * tm["n_half"] * tm["dx"] - self.R2 * lam) / C0
        return [
            ck.close("Poynting flux through nested closed contours", ck.EM,
                     e1 * 1e9, e2 * 1e9, 1e-6,
                     detail=f"energy leaving the {self.R1:g}-wavelength contour vs the "
                     f"{self.R2:g}-wavelength contour", unit="nJ/m"),
            ck.close("Poynting flux equals the work done by the sources", ck.EM,
                     e2 * 1e9, run.source_work * 1e9, 1e-6,
                     detail="radiated energy vs minus the time integral of J.E",
                     unit="nJ/m"),
            ck.small("div B = 0", ck.EM, run.div_b, 1e-9,
                     "max discrete divergence of H on the Yee grid at the end of the "
                     "run, relative to |H|/dx"),
            ck.truth("boundary handling: wall echo time-gated", ck.MATH,
                     tm["steps"] * tm["dt"] <= first_echo,
                     f"march stops at {tm['steps'] * tm['dt'] * 1e9:.4g} ns; the first "
                     f"reflection from the outer wall could reach a probe at "
                     f"{first_echo * 1e9:.4g} ns"),
            ck.truth("resolution: cells per wavelength", ck.MATH,
                     lam / tm["dx"] >= 10 - 1e-9,
                     f"{lam / tm['dx']:.3g} cells per wavelength (need at least 10)"),
        ]

    def symmetry_checks(self, sol):
        pattern = sol.data["amp"]
        out = [ck.symmetry("symmetry about the array axis", pattern,
                           np.roll(pattern[::-1], 1), 1e-8,
                           "mirror image above and below the row of elements")]
        if self.p.n_elements == 1 or abs(self.p.phase_shift) < 1e-12:
            out.append(ck.symmetry(
                "symmetry about the broadside axis", pattern,
                np.roll(pattern[::-1], 181), 1e-8,
                "in-phase elements radiate equally to both ends of the array"))
        return out
