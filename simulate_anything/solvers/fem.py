"""Finite elements for structures.

* :class:`BeamFE`  - Euler-Bernoulli beam, Hermite cubic elements, consistent
  loads and mass.  Nodal values are exact for point and uniform loads; the
  in-element fields are completed with the fixed-end particular solution so
  deflection, moment and shear are exact everywhere.
* :class:`PlateQ4` - 2-D plane stress/strain on a rectangle with bilinear
  quadrilaterals enriched by incompatible modes (Wilson-Taylor), which removes
  the shear and Poisson locking a plain Q4 shows in bending.
"""
from __future__ import annotations

import math

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla


# ---------------------------------------------------------------------------
# Euler-Bernoulli beam
# ---------------------------------------------------------------------------
def beam_mesh(length: float, breakpoints, n: int) -> np.ndarray:
    """Node positions with a node at every breakpoint (supports, loads)."""
    pts = sorted({0.0, float(length), *[float(b) for b in breakpoints]})
    merged = [pts[0]]
    for p in pts[1:]:
        if p - merged[-1] > 1e-12 * length:
            merged.append(p)
    merged[-1] = float(length)
    x = [merged[0]]
    for a, b in zip(merged[:-1], merged[1:]):
        m = max(1, int(round(n * (b - a) / length)))
        x.extend(a + (b - a) * np.arange(1, m + 1) / m)
    return np.asarray(x)


class BeamFE:
    """Deflection ``v`` is positive upward, rotation ``theta = dv/dx``.
    DOF order per node: (v, theta)."""

    def __init__(self, x, EI: float, rhoA: float = 0.0):
        self.x = np.asarray(x, float)
        self.EI, self.rhoA = float(EI), float(rhoA)
        self.ne = len(self.x) - 1
        self.ndof = 2 * len(self.x)
        self.le = np.diff(self.x)
        K = np.zeros((self.ndof, self.ndof))
        M = np.zeros_like(K)
        for e, L in enumerate(self.le):
            ke = EI / L ** 3 * np.array([
                [12, 6 * L, -12, 6 * L], [6 * L, 4 * L * L, -6 * L, 2 * L * L],
                [-12, -6 * L, 12, -6 * L], [6 * L, 2 * L * L, -6 * L, 4 * L * L]])
            sl = slice(2 * e, 2 * e + 4)
            K[sl, sl] += ke
            if rhoA:
                M[sl, sl] += rhoA * L / 420 * np.array([
                    [156, 22 * L, 54, -13 * L], [22 * L, 4 * L * L, 13 * L, -3 * L * L],
                    [54, 13 * L, 156, -22 * L], [-13 * L, -3 * L * L, -22 * L, 4 * L * L]])
        self.K, self.M = K, M

    def node_at(self, xq: float) -> int:
        i = int(np.argmin(np.abs(self.x - xq)))
        if abs(self.x[i] - xq) > 1e-9 * (self.x[-1] - self.x[0]):
            raise ValueError(f"no mesh node at x = {xq}")
        return i

    def load_vector(self, w: float = 0.0, points=()) -> np.ndarray:
        """Uniform load ``w`` [N/m] and point forces ``(x, F)``, both upward."""
        f = np.zeros(self.ndof)
        for e, L in enumerate(self.le):
            f[2 * e:2 * e + 4] += w * L / 2 * np.array([1, L / 6, 1, -L / 6])
        for xq, F in points:
            f[2 * self.node_at(xq)] += F
        return f

    def solve(self, fixed, f, w: float = 0.0) -> "BeamSolution":
        fixed = np.asarray(sorted(set(fixed)), int)
        free = np.setdiff1d(np.arange(self.ndof), fixed)
        u = np.zeros(self.ndof)
        Kff = self.K[np.ix_(free, free)]
        u[free] = np.linalg.solve(Kff, f[free])
        r = self.K @ u - f
        res = np.linalg.norm(r[free]) / max(np.linalg.norm(f[free]),
                                            np.linalg.norm(Kff.diagonal() * u[free]),
                                            1e-300)
        return BeamSolution(self, u, r, fixed, float(res), w)

    def modes(self, fixed, count: int):
        """Lowest ``count`` (omega^2, mode) pairs of K phi = omega^2 M phi."""
        import scipy.linalg as sla
        fixed = np.asarray(sorted(set(fixed)), int)
        free = np.setdiff1d(np.arange(self.ndof), fixed)
        Kff, Mff = self.K[np.ix_(free, free)], self.M[np.ix_(free, free)]
        lam, vec = sla.eigh(Kff, Mff)
        phi = np.zeros((self.ndof, len(lam)))
        phi[free, :] = vec
        return lam, phi, Kff, Mff, free

    def shape(self, u, xq) -> np.ndarray:
        """Hermite interpolation of a nodal vector at points ``xq``."""
        xq = np.atleast_1d(np.asarray(xq, float))
        e = np.clip(np.searchsorted(self.x, xq, side="right") - 1, 0, self.ne - 1)
        L = self.le[e]
        s = (xq - self.x[e]) / L
        n1, n2 = 1 - 3 * s ** 2 + 2 * s ** 3, L * (s - 2 * s ** 2 + s ** 3)
        n3, n4 = 3 * s ** 2 - 2 * s ** 3, L * (-s ** 2 + s ** 3)
        return n1 * u[2 * e] + n2 * u[2 * e + 1] + n3 * u[2 * e + 2] + n4 * u[2 * e + 3]


class BeamSolution:
    def __init__(self, fe: BeamFE, u, r, fixed, residual, w):
        self.fe, self.u, self.r, self.fixed = fe, u, r, fixed
        self.residual, self.w = residual, float(w)
        # element end forces: F1 = EI v'''(0) = dM/dx at the left end, M1 = -M(0)
        ends = np.zeros((fe.ne, 4))
        for e, L in enumerate(fe.le):
            sl = slice(2 * e, 2 * e + 4)
            ke = fe.EI / L ** 3 * np.array([
                [12, 6 * L, -12, 6 * L], [6 * L, 4 * L * L, -6 * L, 2 * L * L],
                [-12, -6 * L, 12, -6 * L], [6 * L, 2 * L * L, -6 * L, 4 * L * L]])
            ends[e] = ke @ u[sl] - self.w * L / 2 * np.array([1, L / 6, 1, -L / 6])
        self.ends = ends

    def _loc(self, xq, side="right"):
        fe = self.fe
        xq = np.atleast_1d(np.asarray(xq, float))
        e = np.clip(np.searchsorted(fe.x, xq, side=side) - 1, 0, fe.ne - 1)
        return xq, e, xq - fe.x[e]

    def deflection(self, xq) -> np.ndarray:
        """Exact deflection (Hermite part + fixed-end particular solution)."""
        xq, e, s = self._loc(xq)
        L = self.fe.le[e]
        return self.fe.shape(self.u, xq) + self.w * s ** 2 * (L - s) ** 2 / (24 * self.fe.EI)

    def moment(self, xq, side="right") -> np.ndarray:
        """Bending moment M = EI v'' (sagging positive for upward v)."""
        xq, e, s = self._loc(xq, side)
        return -self.ends[e, 1] + self.ends[e, 0] * s + self.w * s ** 2 / 2

    def shear(self, xq, side="right") -> np.ndarray:
        """dM/dx (jumps at point loads and supports)."""
        xq, e, s = self._loc(xq, side)
        return self.ends[e, 0] + self.w * s

    def max_moment(self) -> float:
        fe, best = self.fe, 0.0
        for e, L in enumerate(fe.le):
            f1, m1 = self.ends[e, 0], self.ends[e, 1]
            cand = [0.0, L]
            if self.w and 0 < -f1 / self.w < L:
                cand.append(-f1 / self.w)
            for s in cand:
                best = max(best, abs(-m1 + f1 * s + self.w * s * s / 2))
        return best

    def max_deflection(self, samples: int = 2001):
        """(largest |v|, where) with a parabolic refinement of the peak."""
        xs = np.linspace(self.fe.x[0], self.fe.x[-1], samples)
        v = np.abs(self.deflection(xs))
        i = int(np.argmax(v))
        if 0 < i < samples - 1:
            a, b, c = v[i - 1], v[i], v[i + 1]
            den = a - 2 * b + c
            if den < 0:
                t = 0.5 * (a - c) / den
                return float(b - 0.25 * (a - c) * t), float(xs[i] + t * (xs[1] - xs[0]))
        return float(v[i]), float(xs[i])


# ---------------------------------------------------------------------------
# Plane stress / strain rectangle, Q4 + incompatible modes
# ---------------------------------------------------------------------------
_GP = 1.0 / math.sqrt(3.0)
_XI = np.array([-1.0, 1.0, 1.0, -1.0])
_ETA = np.array([-1.0, -1.0, 1.0, 1.0])


class PlateQ4:
    """Rectangle ``length x height`` meshed with ``nx x ny`` identical elements.
    Node (i, j) sits at (i dx, j dy); DOFs are (u_x, u_y) per node."""

    def __init__(self, length, height, nx, ny, E, nu, thickness, plane_stress=True):
        self.L, self.H, self.nx, self.ny, self.t = length, height, nx, ny, thickness
        self.a, self.b = length / nx / 2, height / ny / 2
        if plane_stress:
            self.D = E / (1 - nu ** 2) * np.array(
                [[1, nu, 0], [nu, 1, 0], [0, 0, (1 - nu) / 2]])
        else:
            c = E / ((1 + nu) * (1 - 2 * nu))
            self.D = c * np.array([[1 - nu, nu, 0], [nu, 1 - nu, 0],
                                   [0, 0, (1 - 2 * nu) / 2]])
        self.nn = (nx + 1) * (ny + 1)
        self.ndof = 2 * self.nn
        i, j = np.meshgrid(np.arange(nx), np.arange(ny), indexing="ij")
        n0 = (i * (ny + 1) + j).ravel()
        nodes = np.stack([n0, n0 + ny + 1, n0 + ny + 2, n0 + 1], axis=1)
        self.conn = nodes
        self.edofs = np.stack([2 * nodes, 2 * nodes + 1], axis=2).reshape(-1, 8)
        I, J = np.meshgrid(np.arange(nx + 1), np.arange(ny + 1), indexing="ij")
        self.xn = (I * 2 * self.a).ravel()
        self.yn = (J * 2 * self.b).ravel()
        self._element()
        ne = nx * ny
        rows = np.repeat(self.edofs, 8, axis=1).ravel()
        cols = np.tile(self.edofs, (1, 8)).ravel()
        self.K = sp.csr_matrix((np.tile(self.Ke.ravel(), ne), (rows, cols)),
                               shape=(self.ndof, self.ndof))

    def node(self, i, j) -> int:
        return i * (self.ny + 1) + j

    def B(self, xi, eta):
        """Strain-displacement matrices (compatible 3x8, incompatible 3x4)."""
        a, b = self.a, self.b
        dnx = _XI * (1 + eta * _ETA) / (4 * a)
        dny = _ETA * (1 + xi * _XI) / (4 * b)
        Bm = np.zeros((3, 8))
        Bm[0, 0::2], Bm[1, 1::2] = dnx, dny
        Bm[2, 0::2], Bm[2, 1::2] = dny, dnx
        G = np.zeros((3, 4))
        G[0, 0] = -2 * xi / a          # u: (1 - xi^2)
        G[2, 1] = -2 * eta / b         # u: (1 - eta^2)
        G[2, 2] = -2 * xi / a          # v: (1 - xi^2)
        G[1, 3] = -2 * eta / b         # v: (1 - eta^2)
        return Bm, G

    def _element(self):
        Kuu, Kua, Kaa = np.zeros((8, 8)), np.zeros((8, 4)), np.zeros((4, 4))
        dv = self.a * self.b * self.t
        for xi in (-_GP, _GP):
            for eta in (-_GP, _GP):
                Bm, G = self.B(xi, eta)
                Kuu += Bm.T @ self.D @ Bm * dv
                Kua += Bm.T @ self.D @ G * dv
                Kaa += G.T @ self.D @ G * dv
        self.alpha_map = -np.linalg.solve(Kaa, Kua.T)      # alpha = map @ u_e
        self.Ke = Kuu + Kua @ self.alpha_map

    def edge_load(self, fx_total=0.0, fy_total=0.0) -> np.ndarray:
        """Uniform tractions on the right edge with the given resultants."""
        f = np.zeros(self.ndof)
        wgt = np.full(self.ny + 1, 1.0 / self.ny)
        wgt[[0, -1]] *= 0.5
        nodes = self.node(self.nx, np.arange(self.ny + 1))
        f[2 * nodes] = fx_total * wgt
        f[2 * nodes + 1] = fy_total * wgt
        return f

    def solve(self, fixed, f) -> "PlateSolution":
        fixed = np.asarray(sorted(set(int(d) for d in fixed)), int)
        free = np.setdiff1d(np.arange(self.ndof), fixed)
        u = np.zeros(self.ndof)
        Kff = self.K[free][:, free].tocsc()
        u[free] = spla.spsolve(Kff, f[free])
        r = self.K @ u - f
        res = np.linalg.norm(r[free]) / max(np.linalg.norm(f[free]), 1e-300)
        return PlateSolution(self, u, r, fixed, f, float(res))


class PlateSolution:
    def __init__(self, fe: PlateQ4, u, r, fixed, f, residual):
        self.fe, self.u, self.r, self.fixed, self.f = fe, u, r, fixed, f
        self.residual = residual
        self.ue = u[fe.edofs]                               # (ne, 8)
        self.alpha = self.ue @ fe.alpha_map.T               # (ne, 4)

    def stress(self, xi=0.0, eta=0.0) -> np.ndarray:
        """(sx, sy, txy) of every element at local point (xi, eta): (nx, ny, 3)."""
        Bm, G = self.fe.B(xi, eta)
        eps = self.ue @ Bm.T + self.alpha @ G.T
        return (eps @ self.fe.D.T).reshape(self.fe.nx, self.fe.ny, 3)

    def von_mises(self, xi=0.0, eta=0.0) -> np.ndarray:
        s = self.stress(xi, eta)
        return np.sqrt(s[..., 0] ** 2 - s[..., 0] * s[..., 1] + s[..., 1] ** 2
                       + 3 * s[..., 2] ** 2)

    def strain_energy(self) -> float:
        """Integral of 1/2 sigma:epsilon over Gauss points (not via K)."""
        fe, tot = self.fe, 0.0
        for xi in (-_GP, _GP):
            for eta in (-_GP, _GP):
                Bm, G = fe.B(xi, eta)
                eps = self.ue @ Bm.T + self.alpha @ G.T
                tot += 0.5 * float(np.sum((eps @ fe.D.T) * eps)) * fe.a * fe.b * fe.t
        return tot

    def section_resultants(self, col: int):
        """(axial force, shear force, moment about mid-height, x of the cut)
        integrated through the element column ``col`` with 2-point Gauss."""
        fe = self.fe
        yc = (np.arange(fe.ny) + 0.5) * 2 * fe.b
        n = v = m = 0.0
        for eta in (-_GP, _GP):
            s = self.stress(0.0, eta)[col]                 # (ny, 3)
            y = yc + eta * fe.b - fe.H / 2
            n += float(np.sum(s[:, 0])) * fe.b * fe.t
            v += float(np.sum(s[:, 2])) * fe.b * fe.t
            m += float(np.sum(s[:, 0] * y)) * fe.b * fe.t
        return n, v, m, (col + 0.5) * 2 * fe.a
