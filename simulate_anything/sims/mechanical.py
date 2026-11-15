"""Structures: beams, beam vibration, plane-stress brackets."""
from __future__ import annotations

import math

import numpy as np

from .. import checks as ck
from ..core import Field, Param, ParamError, Simulation, Solution, register
from ..solvers.fem import BeamFE, PlateQ4, beam_mesh
from ..units import Q, convert

_SUPPORTS = {
    "cantilever": lambda L: [(0.0, "fixed")],
    "simply_supported": lambda L: [(0.0, "pin"), (L, "roller")],
    "fixed_fixed": lambda L: [(0.0, "fixed"), (L, "fixed")],
    "fixed_pinned": lambda L: [(0.0, "fixed"), (L, "roller")],
}


def _restrained(supports) -> tuple:
    """(is stable, message) for a beam on the given supports."""
    xs = sorted({round(x, 12) for x, _ in supports})
    n_fixed = sum(kind == "fixed" for _, kind in supports)
    if not supports:
        return False, "ill-posed: the beam has no supports at all"
    if n_fixed >= 1 or len(xs) >= 2:
        return True, (f"{len(supports)} support(s) block both vertical translation "
                      "and rotation of the beam as a rigid body")
    return False, ("ill-posed: a single pin/roller support leaves the beam free to "
                   "rotate about it (a mechanism) - add a second support or clamp it")


def _closed_form(kind, L, EI, w, loads):
    """Textbook deflection curve (downward positive) for the four standard
    support cases under any set of point loads plus a uniform load."""
    def cant(x, P, a):
        return np.where(x <= a, P * x ** 2 * (3 * a - x), P * a ** 2 * (3 * x - a)) / (6 * EI)

    def point(x, P, a):
        b = L - a
        if kind == "cantilever":
            return cant(x, P, a)
        if kind == "simply_supported":
            return np.where(x <= a, P * b * x * (L ** 2 - b ** 2 - x ** 2),
                            P * a * (L - x) * (L ** 2 - a ** 2 - (L - x) ** 2)) / (6 * L * EI)
        if kind == "fixed_fixed":
            xr = L - x
            return np.where(x <= a, P * b ** 2 * x ** 2 * (3 * a * L - (3 * a + b) * x),
                            P * a ** 2 * xr ** 2 * (3 * b * L - (3 * b + a) * xr)) / (6 * L ** 3 * EI)
        rb = P * a ** 2 * (3 * L - a) / (2 * L ** 3)           # prop reaction
        return cant(x, P, a) - rb * x ** 2 * (3 * L - x) / (6 * EI)

    def uniform(x):
        if kind == "cantilever":
            return w * x ** 2 * (6 * L ** 2 - 4 * L * x + x ** 2) / (24 * EI)
        if kind == "simply_supported":
            return w * x * (L ** 3 - 2 * L * x ** 2 + x ** 3) / (24 * EI)
        if kind == "fixed_fixed":
            return w * x ** 2 * (L - x) ** 2 / (24 * EI)
        return w * x ** 2 * (3 * L ** 2 - 5 * L * x + 2 * x ** 2) / (48 * EI)

    def curve(x):
        x = np.asarray(x, float)
        return uniform(x) + sum(point(x, P, a) for a, P in loads)

    return curve


def _beam_benchmark() -> ck.Check:
    """Same element code on the textbook case 5 w L^4 / (384 E I)."""
    L, EI, w = 2.0, 3.0e4, 500.0
    fe = BeamFE(np.linspace(0, L, 7), EI)
    sol = fe.solve([0, 2 * 6], fe.load_vector(w=-w), w=-w)
    got = -float(sol.deflection(L / 2)[0])
    return ck.close("known solution: beam benchmark", ck.UNIVERSAL, got,
                    5 * w * L ** 4 / (384 * EI), 1e-9,
                    detail="same solver on a simply supported beam under uniform "
                           "load, 5wL^4/(384EI)", unit="m")


@register
class Beam(Simulation):
    name = "beam"
    title = "Beam bending (Euler-Bernoulli)"
    domain = "mechanical"
    description = """Deflection, bending moment, shear and stress in a straight
    beam with a rectangular section: E I v'''' = w.  Choose a standard support
    case with a point load and/or uniform load, or pass arbitrary `supports`
    and `point_loads` lists.  Loads are positive downward."""
    params = [
        Param("length", "1 m", "m", "beam length", lo=0.05, hi=20),
        Param("youngs_modulus", "200 GPa", "Pa",
              "Young's modulus (steel 200 GPa, aluminium 69, PLA 3.5)", lo=1e9, hi=4e11),
        Param("width", "40 mm", "m", "section width b", lo=0.001, hi=0.5),
        Param("height", "60 mm", "m", "section height h (in the bending direction)",
              lo=0.001, hi=0.5),
        Param("yield_strength", "250 MPa", "Pa", "yield strength for the safety factor",
              lo=1e7, hi=2e9),
        Param("support", "simply_supported", None, "standard support case",
              choices=list(_SUPPORTS)),
        Param("point_load", "1000 N", "N", "point load, positive downward", lo=0, hi=1e5),
        Param("load_position", 0.5, "", "point-load position as a fraction of the length",
              lo=0, hi=1),
        Param("distributed_load", "0 N/m", "N/m", "uniform load, positive downward",
              lo=0, hi=1e5),
        Param("supports", [], None,
              "optional general supports, overrides `support`: "
              "[{x: '0.2 m', type: 'pin'|'roller'|'fixed'}, ...]"),
        Param("point_loads", [], None,
              "optional general point loads, overrides `point_load`: "
              "[{x: '0.3 m', force: '500 N'}, ...] (downward positive)"),
    ]
    default_resolution = 32
    min_resolution = 2
    primary = ("max_deflection", "max_bending_moment")
    resolution_doc = "number of beam elements"

    def validate(self):
        p = self.p
        L = p.length
        if L <= 0 or p.width <= 0 or p.height <= 0 or p.youngs_modulus <= 0:
            raise ParamError("length, section and Young's modulus must be positive")
        if p.supports:
            self.kind, self.sup = None, []
            for i, s in enumerate(p.supports):
                try:
                    x, kind = convert(s["x"], "m"), str(s.get("type", "pin"))
                except KeyError as exc:
                    raise ParamError(f"support {i + 1} is missing field {exc}") from exc
                if kind not in ("pin", "roller", "fixed"):
                    raise ParamError(f"support {i + 1}: type must be pin, roller or fixed")
                if not -1e-12 <= x <= L + 1e-12:
                    raise ParamError(f"support {i + 1} at x = {x:g} m is off the beam")
                self.sup.append((min(max(x, 0.0), L), kind))
        else:
            self.kind, self.sup = p.support, _SUPPORTS[p.support](L)
        if p.point_loads:
            self.loads = []
            for i, ld in enumerate(p.point_loads):
                try:
                    x, f = convert(ld["x"], "m"), convert(ld["force"], "N")
                except KeyError as exc:
                    raise ParamError(f"point load {i + 1} is missing field {exc}") from exc
                if not -1e-12 <= x <= L + 1e-12:
                    raise ParamError(f"point load {i + 1} at x = {x:g} m is off the beam")
                self.loads.append((min(max(x, 0.0), L), f))
        else:
            if not 0 <= p.load_position <= 1:
                raise ParamError("load_position must be between 0 and 1")
            self.loads = [(p.load_position * L, p.point_load)] if p.point_load else []
        if not self.loads and not p.distributed_load:
            raise ParamError("no load applied: give a point_load or distributed_load")
        self.EI = p.youngs_modulus * p.width * p.height ** 3 / 12

    def _inertia(self):
        return self.q("width") * self.q("height") ** 3 / 12

    def equations(self):
        E, I, L = self.q("youngs_modulus"), self._inertia(), self.q("length")
        return {
            "beam equation E*I*v'''' = w": {
                "E*I*v/L^4": E * I * Q(1, "m") / L ** 4, "distributed load w": Q(1, "N/m"),
                "point load P/L": self.q("point_load") / L},
            "bending stress": {
                "M*c/I": Q(1, "N*m") * self.q("height") / I,
                "yield strength": self.q("yield_strength")}}

    def dimensionless(self):
        return {"slenderness L/h": self.q("length") / self.q("height")}

    def well_posed(self):
        ok, msg = _restrained(self.sup)
        slender = self.p.length / self.p.height
        return [ck.truth("supports restrain rigid-body motion", ck.MATH, ok, msg),
                ck.truth("model validity: slender beam", ck.MATH, slender >= 10,
                         f"L/h = {slender:.3g} (Euler-Bernoulli theory neglects shear "
                         "deformation; wants L/h >= 10)", warn=True)]

    def solve(self, n):
        p = self.p
        x = beam_mesh(p.length, [s[0] for s in self.sup] + [ld[0] for ld in self.loads], n)
        fe = BeamFE(x, self.EI)
        f = fe.load_vector(w=-p.distributed_load, points=[(a, -P) for a, P in self.loads])
        fixed = []
        for xs, kind in self.sup:
            node = fe.node_at(xs)
            fixed.append(2 * node)
            if kind == "fixed":
                fixed.append(2 * node + 1)
        sol = fe.solve(fixed, f, w=-p.distributed_load)
        vmax, _ = sol.max_deflection()
        x_load = self.loads[0][0] if self.loads else p.load_position * p.length
        m_max = sol.max_moment()
        sigma = m_max * (p.height / 2) / (p.width * p.height ** 3 / 12)
        react = float(sum(sol.r[2 * fe.node_at(xs)] for xs, _ in self.sup))
        qoi = {
            "max_deflection": (vmax * 1e3, "mm"),
            "deflection_at_load": (-float(sol.deflection(x_load)[0]) * 1e3, "mm"),
            "max_bending_moment": (m_max, "N*m"),
            "max_bending_stress": (sigma / 1e6, "MPa"),
            "safety_factor": (p.yield_strength / sigma if sigma else math.inf, ""),
            "total_reaction": (react, "N"),
        }
        xs = np.linspace(0, p.length, 401)
        fields = [
            Field("deflection", xs, sol.deflection(xs) * 1e3, unit="mm",
                  xlabel="x [m]", ylabel="deflection [mm] (negative = down)"),
            Field("bending moment", xs, sol.moment(xs), unit="N*m",
                  xlabel="x [m]", ylabel="M [N*m]"),
            Field("shear force", xs, sol.shear(xs), unit="N",
                  xlabel="x [m]", ylabel="dM/dx [N]"),
        ]
        return Solution(qoi, fields, sol.residual, data={"sol": sol, "fe": fe})

    # -- applied load totals (upward positive, moments about x = 0) ---------
    def _applied(self):
        p = self.p
        force = -(sum(P for _, P in self.loads) + p.distributed_load * p.length)
        moment = -(sum(P * a for a, P in self.loads)
                   + p.distributed_load * p.length ** 2 / 2)
        return force, moment

    def _reactions(self, sol, fe):
        out = []
        for xs, kind in self.sup:
            node = fe.node_at(xs)
            out.append((xs, "force", float(sol.r[2 * node])))
            if kind == "fixed":
                out.append((xs, "moment", float(sol.r[2 * node + 1])))
        return out

    def known_solution(self, sol):
        if self.kind is None:
            return [_beam_benchmark()]
        p, s = self.p, sol.data["sol"]
        curve = _closed_form(self.kind, p.length, self.EI, p.distributed_load, self.loads)
        xs = np.linspace(0, p.length, 4001)
        exact, got = curve(xs), -s.deflection(xs)
        scale = float(np.max(np.abs(exact)))
        label = self.kind.replace("_", " ")
        if scale == 0:
            return [ck.skip("known solution: beam-theory formula", ck.UNIVERSAL,
                            "every load sits on a support, so nothing deflects")]
        return [
            ck.close("known solution: beam-theory formula (max deflection)",
                     ck.UNIVERSAL, sol.qoi["max_deflection"][0], scale * 1e3, 1e-6,
                     detail=f"closed-form {label} beam", unit="mm"),
            ck.small("known solution: beam-theory deflection curve", ck.UNIVERSAL,
                     float(np.max(np.abs(got - exact))) / scale, 1e-7,
                     f"largest gap to the closed-form {label} curve, relative to the "
                     "peak deflection"),
        ]

    def domain_checks(self, sol):
        s, fe = sol.data["sol"], sol.data["fe"]
        f_app, m_app = self._applied()
        react = self._reactions(s, fe)
        r_force = sum(v for _, k, v in react if k == "force")
        r_mom = sum(v * x for x, k, v in react if k == "force") \
            + sum(v for _, k, v in react if k == "moment")
        fscale = max(abs(f_app), sum(abs(v) for _, k, v in react if k == "force"), 1e-300)
        mscale = max(abs(m_app), fscale * self.p.length)
        out = [
            ck.small("force balance (sum F = 0)", ck.STRUCT, (r_force + f_app) / fscale,
                     1e-9, f"reactions {r_force:+.6g} N + applied {f_app:+.6g} N; "
                     "relative imbalance"),
            ck.small("moment balance (sum M = 0)", ck.STRUCT, (r_mom + m_app) / mscale,
                     1e-9, f"moments about x = 0: reactions {r_mom:+.6g} N*m + applied "
                     f"{m_app:+.6g} N*m; relative imbalance"),
        ]
        cols = [(1.0, x) if k == "force" else (0.0, 1.0) for x, k, _ in react]
        A = np.array(cols).T if cols else np.zeros((2, 0))
        if A.shape[1] == 2 and abs(np.linalg.det(A)) > 1e-12:
            hand = np.linalg.solve(A, [-f_app, -m_app])       # statics alone
            got = np.array([v for *_, v in react])
            err = float(np.max(np.abs(got - hand))) / max(float(np.max(np.abs(hand))), 1e-300)
            listing = ", ".join(f"{k} at x={x:g} m: {v:.6g}" for x, k, v in react)
            out.append(ck.small("reaction forces equal applied loads", ck.STRUCT, err, 1e-9,
                                f"statically determinate, so each reaction is known "
                                f"from statics alone ({listing}); largest relative gap"))
        else:
            out.append(ck.close("reaction forces equal applied loads", ck.STRUCT,
                                r_force, -f_app, 1e-9,
                                detail="statically indeterminate: sum of support "
                                       "reactions vs total applied load", unit="N"))
        return out

    def symmetry_checks(self, sol):
        L = self.p.length

        def cls(kind):
            return "fixed" if kind == "fixed" else "simple"

        sup = sorted((round(x / L, 9), cls(k)) for x, k in self.sup)
        mir = sorted((round(1 - x / L, 9), cls(k)) for x, k in self.sup)
        lds = sorted((round(a / L, 9), P) for a, P in self.loads)
        lmir = sorted((round(1 - a / L, 9), P) for a, P in self.loads)
        if sup != mir or lds != lmir:
            return []
        xs = np.linspace(0, L, 801)
        s = sol.data["sol"]
        return [ck.symmetry("symmetry about midspan", s.deflection(xs),
                            s.deflection(L - xs), 1e-8,
                            "supports and loads are mirror-symmetric, so is the "
                            "deflection")]


# ---------------------------------------------------------------------------
_BETA = {  # (beta L) of the first three elastic modes
    "cantilever": (1.8751040687, 4.6940911330, 7.8547574382),
    "simply_supported": (math.pi, 2 * math.pi, 3 * math.pi),
    "fixed_fixed": (4.7300407449, 7.8532046241, 10.9956078380),
    "free_free": (4.7300407449, 7.8532046241, 10.9956078380),
}


@register
class BeamVibration(Simulation):
    name = "beam_vibration"
    title = "Beam vibration modes (natural frequencies)"
    domain = "mechanical"
    description = """Natural frequencies and mode shapes of a uniform beam in
    bending: E I v'''' + rho A v_tt = 0, solved as the generalized eigenproblem
    K phi = omega^2 M phi with a consistent mass matrix."""
    params = [
        Param("length", "0.5 m", "m", "beam length", lo=0.02, hi=10),
        Param("youngs_modulus", "69 GPa", "Pa", "Young's modulus", lo=1e9, hi=4e11),
        Param("density", "2700 kg/m^3", "kg/m^3", "material density", lo=500, hi=20000),
        Param("width", "30 mm", "m", "section width b", lo=0.001, hi=0.5),
        Param("height", "5 mm", "m", "section thickness h in the vibration direction",
              lo=0.0005, hi=0.2),
        Param("support", "cantilever", None, "end conditions", choices=list(_BETA)),
    ]
    default_resolution = 32
    min_resolution = 2
    primary = ("f1", "f3")
    order_hint = 4
    resolution_doc = "number of beam elements"

    def validate(self):
        p = self.p
        if min(p.length, p.youngs_modulus, p.density, p.width, p.height) <= 0:
            raise ParamError("all beam properties must be positive")
        self.EI = p.youngs_modulus * p.width * p.height ** 3 / 12
        self.rhoA = p.density * p.width * p.height

    def equations(self):
        I = self.q("width") * self.q("height") ** 3 / 12
        A = self.q("width") * self.q("height")
        return {"frequency scale": {
            "f": Q(1, "Hz"),
            "sqrt(E*I/(rho*A))/L^2": (self.q("youngs_modulus") * I
                                      / (self.q("density") * A)).sqrt()
            / self.q("length") ** 2}}

    def dimensionless(self):
        return {"slenderness L/h": self.q("length") / self.q("height")}

    def well_posed(self):
        ratio = self.p.length / (3 * self.p.height)
        return [ck.truth("eigenproblem well-defined", ck.MATH, True,
                         "positive mass and stiffness; end conditions from the "
                         f"'{self.p.support}' case"),
                ck.truth("model validity: slender beam", ck.MATH, ratio >= 10,
                         f"third-mode half-wavelength / thickness = {ratio:.3g} "
                         "(Euler-Bernoulli wants >= 10; rotary inertia and shear "
                         "lower the real frequencies otherwise)", warn=True)]

    def _fixed(self, n):
        kind = self.p.support
        if kind == "cantilever":
            return [0, 1]
        if kind == "simply_supported":
            return [0, 2 * n]
        if kind == "fixed_fixed":
            return [0, 1, 2 * n, 2 * n + 1]
        return []

    def solve(self, n):
        p = self.p
        fe = BeamFE(np.linspace(0, p.length, n + 1), self.EI, self.rhoA)
        lam, phi, Kff, Mff, free = fe.modes(self._fixed(n), 3)
        nrb = 2 if p.support == "free_free" else 0
        sel = slice(nrb, nrb + 3)
        freqs = np.sqrt(np.maximum(lam[sel], 0.0)) / (2 * math.pi)
        vec = phi[free][:, sel]
        mv = Mff @ vec                 # backward error of each eigenpair
        res = float(np.max(
            np.linalg.norm(Kff @ vec - mv * lam[sel], axis=0)
            / ((np.linalg.norm(Kff, 1) + np.abs(lam[sel]) * np.linalg.norm(Mff, 1))
               * np.linalg.norm(vec, axis=0))))
        xs = np.linspace(0, p.length, 201)
        shapes = {}
        for k in range(3):
            y = fe.shape(phi[:, nrb + k], xs)
            y = y / np.max(np.abs(y))
            if y[np.argmax(np.abs(y))] < 0:
                y = -y
            shapes[f"mode {k + 1} ({freqs[k]:.4g} Hz)"] = y
        qoi = {f"f{k + 1}": (float(freqs[k]), "Hz") for k in range(3)}
        fields = [Field("mode shapes", xs, shapes, xlabel="x [m]",
                        ylabel="normalised deflection")]
        return Solution(qoi, fields, res,
                        "eigenpair backward error |K phi - w^2 M phi| / "
                        "((|K| + w^2 |M|) |phi|)",
                        data={"lam": lam, "vec": phi[free], "K": Kff, "M": Mff,
                              "nrb": nrb, "shapes": list(shapes.values())})

    def _exact(self):
        p = self.p
        c = math.sqrt(self.EI / self.rhoA) / (2 * math.pi * p.length ** 2)
        return [b ** 2 * c for b in _BETA[p.support]]

    def known_solution(self, sol):
        label = self.p.support.replace("_", " ")
        return [ck.close(f"known solution: mode {k + 1} frequency", ck.UNIVERSAL,
                         sol.qoi[f"f{k + 1}"][0], f, 1e-4,
                         detail=f"(beta L)^2 formula for a {label} beam", unit="Hz")
                for k, f in enumerate(self._exact())]

    def domain_checks(self, sol):
        d = sol.data
        lam, vec, nrb = d["lam"], d["vec"], d["nrb"]
        m = nrb + 3
        V = vec[:, :m]
        gram = V.T @ d["M"] @ V
        off = float(np.max(np.abs(gram - np.eye(m))))
        kg = V[:, nrb:].T @ d["K"] @ V[:, nrb:]
        koff = float(np.max(np.abs(kg - np.diag(lam[nrb:m])))) / float(lam[m - 1])
        tol = 1e-6 * float(lam[nrb])
        zero = int(np.sum(np.abs(lam) < tol))
        psd = bool(lam.min() > -tol) and zero == nrb
        Ve = V[:, nrb:]
        rayleigh = np.einsum("ij,ij->j", Ve, d["K"] @ Ve) / np.einsum(
            "ij,ij->j", Ve, d["M"] @ Ve)
        ray = float(np.max(np.abs(rayleigh / lam[nrb:m] - 1)))
        return [
            ck.small("Rayleigh quotient reproduces the eigenvalues", ck.STRUCT, ray, 1e-7,
                     "phi^T K phi / phi^T M phi vs omega^2 (strain energy = kinetic "
                     "energy in each mode); largest relative gap"),
            ck.small("mode orthogonality (mass matrix)", ck.STRUCT, off, 1e-8,
                     "largest deviation of phi_i^T M phi_j from the identity"),
            ck.small("mode orthogonality (stiffness matrix)", ck.STRUCT, koff, 1e-8,
                     "largest off-diagonal of phi_i^T K phi_j relative to omega_3^2"),
            ck.truth("stiffness positive semi-definite, rigid-body modes", ck.STRUCT,
                     psd, f"{zero} zero-frequency mode(s) found, {nrb} expected for "
                     f"'{self.p.support}'; smallest eigenvalue {lam.min():.3g}"),
        ]

    def symmetry_checks(self, sol):
        if self.p.support == "cantilever":
            return []
        worst = 0.0
        for y in sol.data["shapes"]:
            worst = max(worst, min(float(np.max(np.abs(y - y[::-1]))),
                                   float(np.max(np.abs(y + y[::-1])))))
        return [ck.Check("symmetry of mode shapes", ck.MATH,
                         ck.PASS if worst <= 1e-6 else ck.FAIL,
                         "symmetric end conditions, so every mode is either "
                         f"symmetric or antisymmetric about midspan: worst deviation "
                         f"{worst:.3g} (limit 1e-06)", worst, 0.0, 1e-6)]


# ---------------------------------------------------------------------------
def _patch_test() -> ck.Check:
    """Uniform tension must be reproduced exactly by the element."""
    L, H, t, E, nu, F = 2.0, 1.0, 0.1, 1.0e7, 0.3, 500.0
    fe = PlateQ4(L, H, 4, 2, E, nu, t)
    left = fe.node(0, np.arange(3))
    sol = fe.solve(list(2 * left) + [2 * fe.node(0, 1) + 1], fe.edge_load(F, 0.0))
    sx = sol.stress(0.3, -0.7)[..., 0]
    err = float(np.max(np.abs(sx - F / (H * t)))) / (F / (H * t))
    return ck.small("known solution: patch test", ck.UNIVERSAL, err, 1e-9,
                    "same element under uniform tension must give sigma = F/A "
                    "everywhere; largest relative error")


@register
class PlateStress(Simulation):
    name = "plate_stress"
    title = "Bracket / plate stress (2-D plane-stress FEM)"
    domain = "mechanical"
    description = """A flat rectangular bracket clamped along its left edge and
    loaded on its right edge by a shear force and/or an axial force.  Solves
    2-D elasticity with quadrilateral finite elements (incompatible modes, no
    shear locking) and compares with beam theory where that applies."""
    params = [
        Param("length", "100 mm", "m", "bracket length (clamp to loaded edge)",
              lo=0.005, hi=2),
        Param("height", "20 mm", "m", "bracket height", lo=0.002, hi=1),
        Param("thickness", "5 mm", "m", "out-of-plane thickness", lo=0.0005, hi=0.1),
        Param("youngs_modulus", "69 GPa", "Pa",
              "Young's modulus (aluminium 69 GPa, steel 200, PLA 3.5)", lo=1e9, hi=4e11),
        Param("poisson_ratio", 0.33, "", "Poisson's ratio", lo=0, hi=0.49),
        Param("shear_force", "200 N", "N", "transverse end load, positive downward",
              lo=0, hi=1e5),
        Param("axial_force", "0 N", "N", "axial end load, positive in tension",
              lo=0, hi=1e6),
        Param("yield_strength", "275 MPa", "Pa", "yield strength", lo=1e7, hi=2e9),
        Param("mode", "plane_stress", None, "2-D idealisation",
              choices=["plane_stress", "plane_strain"]),
    ]
    default_resolution = 80
    min_resolution = 4
    resolution_doc = "elements along the length"

    def validate(self):
        p = self.p
        if min(p.length, p.height, p.thickness, p.youngs_modulus) <= 0:
            raise ParamError("geometry and Young's modulus must be positive")
        if not 0 <= p.poisson_ratio < 0.5:
            raise ParamError("poisson_ratio must be in [0, 0.5)")
        if not p.shear_force and not p.axial_force:
            raise ParamError("no load applied: give a shear_force or axial_force")
        self.primary = (("tip_deflection", "fibre_stress") if p.shear_force
                        else ("axial_elongation",))
        if p.mode == "plane_strain":     # equivalent beam constants
            self.E_eff = p.youngs_modulus / (1 - p.poisson_ratio ** 2)
            self.nu_eff = p.poisson_ratio / (1 - p.poisson_ratio)
        else:
            self.E_eff, self.nu_eff = p.youngs_modulus, p.poisson_ratio

    def equations(self):
        E, L, H, t = (self.q(k) for k in ("youngs_modulus", "length", "height", "thickness"))
        I = t * H ** 3 / 12
        return {
            "tip deflection": {"delta": Q(1, "m"),
                               "P*L^3/(E*I)": self.q("shear_force") * L ** 3 / (E * I),
                               "F*L/(E*A)": self.q("axial_force") * L / (E * H * t)},
            "stress": {"sigma": Q(1, "Pa"),
                       "M*c/I": self.q("shear_force") * L * (H / 2) / I,
                       "F/A": self.q("axial_force") / (H * t)}}

    def dimensionless(self):
        return {"slenderness L/H": self.q("length") / self.q("height"),
                "Poisson ratio": self.q("poisson_ratio")}

    def well_posed(self):
        return [ck.truth("supports restrain rigid-body motion", ck.MATH, True,
                         "the clamped edge blocks both translations and the rotation")]

    def _mesh(self, n):
        p = self.p
        ny = max(2, 2 * int(round(n * p.height / p.length / 2)))
        return PlateQ4(p.length, p.height, n, ny, p.youngs_modulus, p.poisson_ratio,
                       p.thickness, p.mode == "plane_stress")

    def _fibre(self, sol, fe):
        """sigma_x on the top surface at mid-length."""
        j = fe.ny - 1
        if fe.nx % 2 == 0:
            i = fe.nx // 2
            return 0.5 * (sol.stress(1.0, 1.0)[i - 1, j, 0] + sol.stress(-1.0, 1.0)[i, j, 0])
        return sol.stress(0.0, 1.0)[fe.nx // 2, j, 0]

    def solve(self, n):
        p = self.p
        fe = self._mesh(n)
        left = fe.node(0, np.arange(fe.ny + 1))
        f = fe.edge_load(p.axial_force, -p.shear_force)
        sol = fe.solve(list(2 * left) + list(2 * left + 1), f)
        right = fe.node(fe.nx, np.arange(fe.ny + 1))
        wgt = np.full(fe.ny + 1, 1.0 / fe.ny)
        wgt[[0, -1]] *= 0.5
        tip = -float(np.sum(wgt * sol.u[2 * right + 1]))
        elong = float(np.sum(wgt * sol.u[2 * right]))
        fibre = float(self._fibre(sol, fe))
        vm = sol.von_mises()
        peak = max(float(sol.von_mises(a, b).max())
                   for a, b in ((0, 0), (-1, -1), (1, -1), (1, 1), (-1, 1)))
        nominal = (6 * abs(p.shear_force) * p.length / (p.thickness * p.height ** 2)
                   + abs(p.axial_force) / (p.thickness * p.height))
        qoi = {
            "tip_deflection": (tip * 1e3, "mm"),
            "axial_elongation": (elong * 1e3, "mm"),
            "fibre_stress": (fibre / 1e6, "MPa"),
            "peak_von_mises": (peak / 1e6, "MPa"),
            "safety_factor_nominal": (p.yield_strength / nominal, ""),
            "strain_energy": (sol.strain_energy(), "J"),
        }
        xc = (np.arange(fe.nx) + 0.5) * 2 * fe.a
        yc = (np.arange(fe.ny) + 0.5) * 2 * fe.b
        mid = fe.node(np.arange(fe.nx + 1), fe.ny // 2)
        fields = [
            Field("von Mises stress", xc * 1e3, vm / 1e6, yc * 1e3, "MPa",
                  "x [mm]", "y [mm]"),
            Field("deflection of the mid-line", np.arange(fe.nx + 1) * 2 * fe.a * 1e3,
                  sol.u[2 * mid + 1] * 1e3, unit="mm", xlabel="x [mm]",
                  ylabel="vertical displacement [mm]"),
        ]
        return Solution(qoi, fields, sol.residual,
                        data={"sol": sol, "fe": fe, "tip": tip, "elong": elong})

    def _beam(self):
        """Timoshenko tip deflection, fibre stress at mid-length, F L / (E A)."""
        p = self.p
        I = p.thickness * p.height ** 3 / 12
        area = p.thickness * p.height
        G = self.E_eff / (2 * (1 + self.nu_eff))
        tip = (p.shear_force * p.length ** 3 / (3 * self.E_eff * I)
               + p.shear_force * p.length / (5 / 6 * G * area))
        fibre = p.shear_force * (p.length / 2) * (p.height / 2) / I + p.axial_force / area
        return tip, fibre, p.axial_force * p.length / (self.E_eff * area)

    def known_solution(self, sol):
        p = self.p
        out = [_patch_test()]
        tip, fibre, elong = self._beam()
        slender = p.length / p.height
        if slender < 4:
            out.append(ck.skip("known solution: beam theory", ck.UNIVERSAL,
                               f"L/H = {slender:.3g} is too stocky for beam theory"))
            return out
        if p.shear_force:
            out.append(ck.close(
                "known solution: beam-theory tip deflection", ck.UNIVERSAL,
                sol.qoi["tip_deflection"][0], tip * 1e3, 0.03,
                detail="Timoshenko beam, P L^3/(3 E I) + P L/(kappa G A)", unit="mm"))
        out.append(ck.close(
            "known solution: beam-theory fibre stress", ck.UNIVERSAL,
            sol.qoi["fibre_stress"][0], fibre / 1e6, 0.02,
            detail="M c / I + F / A at mid-length, top surface", unit="MPa"))
        if p.axial_force:
            out.append(ck.close(
                "known solution: axial elongation F L/(E A)", ck.UNIVERSAL,
                sol.qoi["axial_elongation"][0], elong * 1e3, 0.015,
                detail="bar formula (the clamp restrains Poisson contraction, so the "
                       "plate is very slightly stiffer)", unit="mm"))
        return out

    def domain_checks(self, sol):
        p, s, fe = self.p, sol.data["sol"], sol.data["fe"]
        left = fe.node(0, np.arange(fe.ny + 1))
        rx, ry = s.r[2 * left], s.r[2 * left + 1]
        yl = fe.yn[left] - p.height / 2
        fscale = max(abs(p.shear_force), abs(p.axial_force))
        fx_app, fy_app = p.axial_force, -p.shear_force
        m_react = -float(np.sum(yl * rx))                  # about (0, H/2)
        m_app = p.length * fy_app
        mscale = abs(p.shear_force) * p.length + abs(p.axial_force) * p.height
        out = [
            ck.small("force balance (sum F = 0)", ck.STRUCT,
                     math.hypot(rx.sum() + fx_app, ry.sum() + fy_app) / fscale, 1e-9,
                     f"clamp reactions ({rx.sum():+.6g}, {ry.sum():+.6g}) N + applied "
                     f"({fx_app:+.6g}, {fy_app:+.6g}) N; relative imbalance"),
            ck.small("moment balance (sum M = 0)", ck.STRUCT,
                     (m_react + m_app) / mscale, 1e-9,
                     f"about the clamp centre: reaction moment {m_react:+.6g} N*m + "
                     f"applied {m_app:+.6g} N*m; relative imbalance"),
            ck.small("reaction forces equal applied loads", ck.STRUCT,
                     max(abs(-rx.sum() - fx_app), abs(-ry.sum() - fy_app)) / fscale, 1e-9,
                     "nodal reactions summed over the clamped edge vs the end load; "
                     "relative gap"),
        ]
        n_cut, v_cut, m_cut, x_cut = s.section_resultants(fe.nx // 2)
        gap = max(abs(n_cut - fx_app), abs(v_cut - fy_app)) / fscale
        m_exp = p.shear_force * (p.length - x_cut)
        if m_exp:
            gap = max(gap, abs(m_cut - m_exp) / abs(m_exp))
        out.append(ck.small(
            "internal stress resultants equal applied loads", ck.STRUCT, gap, 1e-3,
            f"stresses integrated over a cut at x = {x_cut * 1e3:.4g} mm give "
            f"N = {n_cut:.5g} N, V = {v_cut:.5g} N, M = {m_cut:.5g} N*m; largest "
            "relative gap to the loads they must carry", warn_factor=3))
        work = 0.5 * (p.shear_force * sol.data["tip"] + p.axial_force * sol.data["elong"])
        out.append(ck.close("strain energy = half the external work", ck.STRUCT,
                            sol.qoi["strain_energy"][0], work, 1e-8,
                            detail="integral of sigma:epsilon/2 over Gauss points vs "
                                   "load x displacement / 2", unit="J"))
        out.append(ck.skip("peak stress convergence", ck.STRUCT,
                           "the clamped corners are stress singularities, so "
                           "peak_von_mises grows with refinement - use fibre_stress "
                           "or the nominal safety factor for design"))
        return out

    def symmetry_checks(self, sol):
        p, s, fe = self.p, sol.data["sol"], sol.data["fe"]
        if p.shear_force and p.axial_force:
            return []
        ux = s.u[0::2].reshape(fe.nx + 1, fe.ny + 1)
        uy = s.u[1::2].reshape(fe.nx + 1, fe.ny + 1)
        sign = 1.0 if p.axial_force else -1.0      # u_x even (axial) / odd (bending)
        field = np.stack([ux, uy])
        mirrored = np.stack([sign * ux[:, ::-1], -sign * uy[:, ::-1]])
        what = ("axial load: u_x mirror-symmetric, u_y antisymmetric about the mid-line"
                if p.axial_force else
                "shear load: u_y mirror-symmetric, u_x antisymmetric about the mid-line")
        return [ck.symmetry("symmetry about the mid-line", field, mirrored, 1e-8, what)]
