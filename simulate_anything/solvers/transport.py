"""Conservative finite-volume solver for the general scalar transport equation

    C dphi/dt + div(v phi) = div(K grad phi) + S - P phi

on 1-D (cartesian, cylindrical or spherical) and 2-D cartesian grids.

One solver, many physics: heat conduction (phi = T, K = k), mass diffusion
(phi = c, K = D), groundwater (phi = head, K = transmissivity), electrostatics
(phi = V, K = permittivity, S = charge density), magnetostatics (phi = A_z,
K = 1/mu, S = J) and fully developed duct flow (phi = u, K = mu, S = -dp/dx).

The discretisation is flux-conservative with harmonic-mean face coefficients,
so the discrete solution satisfies the integral balance law to solver
precision - which is exactly what the conservation checks then test.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla

from .. import checks as ck

SIDES = ("west", "east", "south", "north")


@dataclass
class BC:
    kind: str = "neumann"      # dirichlet | neumann | robin
    value: object = 0.0        # boundary value | inward flux | ambient value
    h: object = 0.0            # robin transfer coefficient


def dirichlet(value) -> BC:
    return BC("dirichlet", value)


def neumann(flux_in=0.0) -> BC:
    return BC("neumann", flux_in)


def robin(h, ambient) -> BC:
    return BC("robin", ambient, h)


def insulated() -> BC:
    return BC("neumann", 0.0)


class Grid:
    """Cell-centred grid (uniform by default, or from explicit face positions).

    In 1-D cartesian ``ly`` is the cross-section area; in radial coordinates
    ``x`` is the radius and totals are per unit angle measure (multiply by
    ``measure``: 2*pi per unit length for a cylinder, 4*pi for a sphere)."""

    def __init__(self, nx, lx, ny=1, ly=1.0, coord="cartesian", x0=0.0,
                 xf=None, yf=None):
        m = {"cartesian": 0, "cylindrical": 1, "spherical": 2}[coord]
        self.xf = (x0 + lx / nx * np.arange(nx + 1) if xf is None
                   else np.asarray(xf, float))
        self.yf = (ly / ny * np.arange(ny + 1) if yf is None
                   else np.asarray(yf, float))
        nx, ny = len(self.xf) - 1, len(self.yf) - 1
        if m and ny != 1:
            raise ValueError("radial coordinates are 1-D only")
        self.nx, self.ny, self.coord = nx, ny, coord
        self.lx, self.ly = self.xf[-1] - self.xf[0], self.yf[-1] - self.yf[0]
        self.dxs, self.dys = np.diff(self.xf), np.diff(self.yf)
        self.dx, self.dy = float(self.dxs.min()), float(self.dys.min())
        self.x = 0.5 * (self.xf[1:] + self.xf[:-1])
        self.y = 0.5 * (self.yf[1:] + self.yf[:-1])
        if m == 0:
            self.Ax = np.ones((nx + 1, 1)) * self.dys[None, :]
            self.vol = np.outer(self.dxs, self.dys)
            self.measure = 1.0
        else:
            self.Ax = (self.xf ** m)[:, None] * np.ones((1, ny))
            self.vol = ((self.xf[1:] ** (m + 1) - self.xf[:-1] ** (m + 1))
                        / (m + 1))[:, None] * np.ones((1, ny))
            self.measure = 2 * math.pi if m == 1 else 4 * math.pi
        self.Ay = self.dxs[:, None] * np.ones((1, ny + 1))
        self.shape = (nx, ny)

    @classmethod
    def layered(cls, thicknesses, n, x0=0.0, coord="cartesian", area=1.0):
        """1-D grid whose faces coincide with the layer interfaces.  Returns
        (grid, layer index of every cell)."""
        t = np.asarray(thicknesses, float)
        cells = np.maximum(2, np.rint(n * t / t.sum()).astype(int))
        xf, layer, pos = [x0], [], x0
        for i, (ti, ci) in enumerate(zip(t, cells)):
            xf.extend(pos + ti * np.arange(1, ci + 1) / ci)
            layer.extend([i] * ci)
            pos += ti
        return cls(len(layer), pos - x0, ly=area, coord=coord, xf=xf), np.array(layer)

    def full(self, value) -> np.ndarray:
        return np.broadcast_to(np.asarray(value, float), self.shape).copy()

    def box(self, x0, x1, y0=-math.inf, y1=math.inf) -> np.ndarray:
        """Mask of cells whose centres lie in the rectangle."""
        X, Y = np.meshgrid(self.x, self.y, indexing="ij")
        return (X >= x0) & (X <= x1) & (Y >= y0) & (Y <= y1)

    def overlap(self, x0, x1, y0=-math.inf, y1=math.inf) -> np.ndarray:
        """Fraction of every cell covered by a rectangle (exact area weights,
        so deposited totals do not depend on how the grid cuts the shape)."""
        fx = np.clip(np.minimum(self.xf[1:], x1) - np.maximum(self.xf[:-1], x0),
                     0, None) / self.dxs
        fy = np.clip(np.minimum(self.yf[1:], y1) - np.maximum(self.yf[:-1], y0),
                     0, None) / self.dys
        return np.outer(fx, fy)

    def mesh(self):
        return np.meshgrid(self.x, self.y, indexing="ij")


class TransportProblem:
    def __init__(self, grid: Grid, K, C=1.0, source=0.0, sink=0.0, bcs=None,
                 ux=None, uy=None, fixed_mask=None, fixed_value=0.0,
                 scheme="upwind"):
        self.grid = g = grid
        self.K = g.full(K)
        self.C = g.full(C)
        self.source = g.full(source)
        self.sink = g.full(sink)
        self.bcs = {s: insulated() for s in SIDES}
        self.bcs.update(bcs or {})
        # face-normal velocities (already multiplied by any capacity factor)
        self.Fx = None if ux is None else np.broadcast_to(
            np.asarray(ux, float), g.Ax.shape) * g.Ax
        self.Fy = None if uy is None else np.broadcast_to(
            np.asarray(uy, float), g.Ay.shape) * g.Ay
        self.scheme = scheme
        self.fixed = (np.zeros(g.shape, bool) if fixed_mask is None
                      else np.asarray(fixed_mask, bool))
        self.fixed_value = g.full(fixed_value)
        self._cache = None

    def face_U(self, axis: int) -> np.ndarray:
        """Conductance per unit area of interior faces (two half-cells in
        series).  A fixed-value cell is a perfect conductor, so its surface -
        not its centre - carries the fixed value."""
        K, f, g = self.K, self.fixed, self.grid
        if axis == 0:
            a, b, fa, fb = K[:-1, :], K[1:, :], f[:-1, :], f[1:, :]
            da, db = g.dxs[:-1, None] / 2, g.dxs[1:, None] / 2
        else:
            a, b, fa, fb = K[:, :-1], K[:, 1:], f[:, :-1], f[:, 1:]
            da, db = g.dys[None, :-1] / 2, g.dys[None, 1:] / 2
        ra = np.where(fa, 0.0, da / a)
        rb = np.where(fb, 0.0, db / b)
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(fa & fb, 0.0, 1.0 / np.where(fa & fb, 1.0, ra + rb))

    # -- boundary bookkeeping ----------------------------------------------
    def _side(self, side):
        """(cell slice, face area, half spacing, outward volumetric flux)."""
        g = self.grid
        if side == "west":
            sl, A, d = (0, slice(None)), g.Ax[0, :], g.dxs[0] / 2
            F = None if self.Fx is None else -self.Fx[0, :]
        elif side == "east":
            sl, A, d = (-1, slice(None)), g.Ax[-1, :], g.dxs[-1] / 2
            F = None if self.Fx is None else self.Fx[-1, :]
        elif side == "south":
            sl, A, d = (slice(None), 0), g.Ay[:, 0], g.dys[0] / 2
            F = None if self.Fy is None else -self.Fy[:, 0]
        else:
            sl, A, d = (slice(None), -1), g.Ay[:, -1], g.dys[-1] / 2
            F = None if self.Fy is None else self.Fy[:, -1]
        if g.ny == 1 and side in ("south", "north"):
            A = np.zeros_like(A)          # a 1-D problem has no lateral faces
        return sl, A, d, (np.zeros_like(A) if F is None else F)

    def bc_terms(self, side):
        """Inflow through a side is ``rhs - coef * phi_cell`` (arrays)."""
        sl, A, d, F = self._side(side)
        bc, K = self.bcs[side], self.K[sl]
        out, inn = np.maximum(F, 0.0), np.maximum(-F, 0.0)
        val = np.broadcast_to(np.asarray(bc.value, float), A.shape)
        if bc.kind == "dirichlet":
            G = A * K / d
            return G + out, (G + inn) * val
        if bc.kind == "robin":
            h = np.broadcast_to(np.asarray(bc.h, float), A.shape)
            U = A * h * K / (K + h * d)
            return U + out, U * val
        if bc.kind == "neumann":
            return out, val * A
        raise ValueError(f"unknown boundary condition '{bc.kind}'")

    # -- assembly ------------------------------------------------------------
    def assemble(self):
        """Steady operator: ``A phi = b`` (before fixed cells are imposed)."""
        if self._cache is not None:
            return self._cache
        g = self.grid
        nx, ny = g.shape
        idx = np.arange(nx * ny).reshape(nx, ny)
        diag = self.sink * g.vol
        b = self.source * g.vol
        rows, cols, vals = [], [], []

        def faces(G, F, P, E, dP, dE):
            if self.scheme == "central":
                Fp = Fm = 0.5 * F
            else:
                Fp, Fm = np.maximum(F, 0.0), np.minimum(F, 0.0)
            dP += G + Fp
            dE += G - Fm
            rows.extend([P.ravel(), E.ravel()])
            cols.extend([E.ravel(), P.ravel()])
            vals.extend([(-G + Fm).ravel(), (-G - Fp).ravel()])

        if nx > 1:
            G = g.Ax[1:-1, :] * self.face_U(0)
            F = 0.0 * G if self.Fx is None else self.Fx[1:-1, :]
            faces(G, F, idx[:-1, :], idx[1:, :], diag[:-1, :], diag[1:, :])
        if ny > 1:
            G = g.Ay[:, 1:-1] * self.face_U(1)
            F = 0.0 * G if self.Fy is None else self.Fy[:, 1:-1]
            faces(G, F, idx[:, :-1], idx[:, 1:], diag[:, :-1], diag[:, 1:])
        for side in SIDES:
            sl, *_ = self._side(side)
            coef, rhs = self.bc_terms(side)
            diag[sl] += coef
            b[sl] += rhs
        rows.append(idx.ravel())
        cols.append(idx.ravel())
        vals.append(diag.ravel())
        A = sp.csr_matrix((np.concatenate(vals), (np.concatenate(rows),
                                                  np.concatenate(cols))),
                          shape=(nx * ny, nx * ny))
        self._cache = (A, b.ravel())
        return self._cache

    def _impose_fixed(self, A, b):
        if not self.fixed.any():
            return A, b
        m = self.fixed.ravel().astype(float)
        A = sp.diags(1.0 - m) @ A + sp.diags(m)
        b = np.where(self.fixed.ravel(), self.fixed_value.ravel(), b)
        return A, b

    # -- well-posedness & symmetry -----------------------------------------
    def has_reference(self) -> bool:
        """Is the level of phi pinned (Dirichlet, Robin, fixed cell or sink)?"""
        if self.fixed.any() or np.any(self.sink > 0):
            return True
        for side in SIDES:
            _, A, _, _ = self._side(side)
            bc = self.bcs[side]
            if not np.any(A > 0):
                continue
            if bc.kind == "dirichlet":
                return True
            if bc.kind == "robin" and np.any(np.asarray(bc.h, float) > 0):
                return True
        return False

    def net_specified_input(self) -> float:
        """Total source plus all prescribed boundary fluxes."""
        g = self.grid
        tot = float(np.sum(self.source * g.vol))
        for side in SIDES:
            if self.bcs[side].kind == "neumann":
                _, A, _, _ = self._side(side)
                tot += float(np.sum(np.broadcast_to(
                    np.asarray(self.bcs[side].value, float), A.shape) * A))
        return tot

    def well_posed(self, steady=True, what="quantity") -> list:
        out = []
        ok = bool(np.all(self.K > 0) and np.all(np.isfinite(self.K)))
        out.append(ck.truth("coefficients physical", ck.MATH, ok,
                            "transport coefficient is positive everywhere" if ok else
                            "transport coefficient must be positive and finite"))
        if np.any(self.sink < 0):
            out.append(ck.truth("coefficients physical: sink", ck.MATH, False,
                                "negative loss coefficient (unbounded growth)"))
        for side in SIDES:
            bc = self.bcs[side]
            if bc.kind == "robin" and np.any(np.asarray(bc.h, float) < 0):
                out.append(ck.truth(f"boundary {side}", ck.MATH, False,
                                    "negative transfer coefficient"))
        if not steady:
            ok = bool(np.all(self.C > 0))
            out.append(ck.truth("boundary conditions complete", ck.MATH, ok,
                                "initial-value problem with a condition on every side"
                                if ok else "capacity must be positive for a transient"))
        elif self.has_reference():
            out.append(ck.truth(
                "boundary conditions complete", ck.MATH, True,
                f"every side has a condition and the {what} level is pinned"))
        else:
            net = self.net_specified_input()
            scale = float(np.sum(np.abs(self.source * self.grid.vol))) + 1e-300
            if abs(net) > 1e-9 * scale:
                msg = (f"ill-posed: every boundary prescribes a flux and the net "
                       f"input is {net:.4g} (not zero), so no steady state exists")
            else:
                msg = (f"ill-posed: only fluxes are prescribed, so the {what} is "
                       "undetermined up to a constant - fix a value somewhere")
            out.append(ck.truth("boundary conditions complete", ck.MATH, False, msg))
        return out

    def mirror_symmetric(self, axis: int) -> bool:
        """True if the whole setup is unchanged by reflecting along ``axis``."""
        def same(a):
            return np.allclose(a, np.flip(a, axis), rtol=1e-12, atol=0)

        if not all(same(a) for a in (self.K, self.C, self.source, self.sink,
                                     self.fixed.astype(float),
                                     np.where(self.fixed, self.fixed_value, 0.0))):
            return False
        if self.Fx is not None or self.Fy is not None:
            return False if axis == 0 else (
                (self.Fy is None or not np.any(self.Fy))
                and (self.Fx is None or same(self.Fx)))
        pair = ("west", "east") if axis == 0 else ("south", "north")
        others = ("south", "north") if axis == 0 else ("west", "east")
        a, b = self.bcs[pair[0]], self.bcs[pair[1]]
        if a.kind != b.kind or not np.allclose(a.value, b.value) \
                or not np.allclose(a.h, b.h):
            return False
        for s in others:
            for v in (self.bcs[s].value, self.bcs[s].h):
                v = np.asarray(v, float)
                if v.ndim and not np.allclose(v, v[::-1]):
                    return False
        return True

    # -- solves ---------------------------------------------------------------
    def solve_steady(self) -> "TransportSolution":
        A0, b0 = self.assemble()
        A, b = self._impose_fixed(A0, b0)
        phi = spla.spsolve(A.tocsc(), b)
        res = np.linalg.norm(A @ phi - b)
        scale = max(np.linalg.norm(b), np.linalg.norm(A.diagonal() * phi), 1e-300)
        return TransportSolution(self, phi.reshape(self.grid.shape), res / scale)

    def solve_transient(self, phi0, t_end, dt, theta=1.0, callback=None):
        """Theta-scheme (1 = backward Euler, 0.5 = Crank-Nicolson, 0 = explicit)."""
        g = self.grid
        A0, b0 = self.assemble()
        M = (self.C * g.vol).ravel()
        nsteps = max(1, int(math.ceil(t_end / dt - 1e-9)))
        dt = t_end / nsteps
        fixed = self.fixed.ravel()
        lhs, _ = self._impose_fixed(sp.diags(M / dt) + theta * A0, b0)
        lu = spla.splu(lhs.tocsc())
        phi = np.where(fixed, self.fixed_value.ravel(), g.full(phi0).ravel())
        start = phi.copy()
        rate_old = self._net_rate(phi, A0, b0)
        gained, res_max = 0.0, 0.0
        hist = {"t": [0.0], "min": [phi.min()], "max": [phi.max()],
                "mean": [float(np.sum(M * phi) / np.sum(M))]}
        if callback:
            callback(0.0, phi.reshape(g.shape))
        for k in range(nsteps):
            rhs = M / dt * phi - (1.0 - theta) * (A0 @ phi) + b0
            rhs = np.where(fixed, self.fixed_value.ravel(), rhs)
            new = lu.solve(rhs)
            if k == nsteps - 1:
                r = lhs @ new - rhs
                res_max = np.linalg.norm(r) / max(np.linalg.norm(rhs), 1e-300)
            rate_new = self._net_rate(new, A0, b0)
            gained += dt * (theta * rate_new + (1.0 - theta) * rate_old)
            phi, rate_old = new, rate_new
            hist["t"].append((k + 1) * dt)
            hist["min"].append(phi.min())
            hist["max"].append(phi.max())
            hist["mean"].append(float(np.sum(M * phi) / np.sum(M)))
            if callback:
                callback((k + 1) * dt, phi.reshape(g.shape))
        sol = TransportSolution(self, phi.reshape(g.shape), res_max)
        sol.history = {k: np.asarray(v) for k, v in hist.items()}
        sol.stored_change = float(np.sum(M * (phi - start)))
        sol.net_input = float(gained)
        sol.dt, sol.theta, sol.nsteps = dt, theta, nsteps
        # positivity limit of the explicit part: (1-theta) dt A_ii / M_i <= 1
        sol.mesh_number = float(dt * np.max(A0.diagonal()[~fixed] / M[~fixed]))
        sol.start = start.reshape(g.shape)
        return sol

    def _net_rate(self, phi, A0, b0) -> float:
        """Net rate of gain from the boundary formulas, sources and fixed cells."""
        g = self.grid
        p2 = phi.reshape(g.shape)
        rate = float(np.sum((self.source - self.sink * p2) * g.vol))
        for side in SIDES:
            sl, *_ = self._side(side)
            coef, rhs = self.bc_terms(side)
            rate += float(np.sum(rhs - coef * p2[sl]))
        if self.fixed.any():   # what the fixed cells feed into the free field
            rate += float(np.sum((A0 @ phi - b0)[self.fixed.ravel()]))
        return rate


class TransportSolution:
    def __init__(self, problem: TransportProblem, phi, residual):
        self.problem, self.phi, self.residual = problem, phi, float(residual)
        self.history = None
        self.stored_change = self.net_input = None
        self.dt = self.theta = self.nsteps = self.mesh_number = None

    @property
    def grid(self) -> Grid:
        return self.problem.grid

    # -- integral quantities --------------------------------------------------
    def boundary_inflow(self) -> dict:
        out = {}
        for side in SIDES:
            sl, *_ = self.problem._side(side)
            coef, rhs = self.problem.bc_terms(side)
            out[side] = float(np.sum(rhs - coef * self.phi[sl]))
        return out

    def boundary_exchange(self) -> list:
        """Per-face (flow leaving the domain, value of the environment)."""
        out = []
        for side in SIDES:
            p = self.problem
            sl, A, _, _ = p._side(side)
            if not np.any(A > 0):
                continue
            coef, rhs = p.bc_terms(side)
            bc = p.bcs[side]
            env = (self.phi[sl] if bc.kind == "neumann" else
                   np.broadcast_to(np.asarray(bc.value, float), A.shape))
            out.append((-(rhs - coef * self.phi[sl]), np.asarray(env, float)))
        return out

    def source_total(self) -> float:
        p = self.problem
        return float(np.sum((p.source * self.grid.vol)[~p.fixed]))

    def sink_total(self) -> float:
        p = self.problem
        return float(np.sum((p.sink * self.phi * self.grid.vol)[~p.fixed]))

    def fixed_outflow(self, mask=None) -> float:
        """Net flow leaving a set of fixed cells into the rest of the field
        (charge on an electrode, current fed by a conductor...)."""
        p = self.problem
        A0, b0 = p.assemble()
        r = (A0 @ self.phi.ravel() - b0).reshape(self.grid.shape)
        mask = p.fixed if mask is None else (np.asarray(mask, bool) & p.fixed)
        return float(np.sum(r[mask]))

    def balance(self, storage_rate=0.0):
        """(imbalance, throughput) of the steady integral conservation law."""
        p = self.problem
        A0, b0 = p.assemble()
        net = p._net_rate(self.phi.ravel(), A0, b0) - storage_rate
        gross = (abs(self.source_total()) + abs(self.sink_total())
                 + sum(float(np.sum(np.abs(q))) for q, _ in self.boundary_exchange())
                 + (float(np.sum(np.abs((A0 @ self.phi.ravel() - b0)[p.fixed.ravel()])))
                    if p.fixed.any() else 0.0))
        return net, max(gross / 2, 1e-300)

    # -- fluxes & sampling ----------------------------------------------------
    def face_flux(self):
        """Diffusive flux density at faces: (qx[(nx+1), ny], qy[nx, (ny+1)])."""
        p, g = self.problem, self.grid
        qx, qy = np.zeros(g.Ax.shape), np.zeros(g.Ay.shape)
        if g.nx > 1:
            qx[1:-1, :] = -p.face_U(0) * np.diff(self.phi, axis=0)
        if g.ny > 1:
            qy[:, 1:-1] = -p.face_U(1) * np.diff(self.phi, axis=1)
        for side, sign in (("west", 1), ("east", -1), ("south", 1), ("north", -1)):
            sl, A, _, _ = p._side(side)
            coef, rhs = p.bc_terms(side)
            with np.errstate(divide="ignore", invalid="ignore"):
                q = np.where(A > 0, sign * (rhs - coef * self.phi[sl]) / np.where(A > 0, A, 1), 0)
            if side == "west":
                qx[0, :] = q
            elif side == "east":
                qx[-1, :] = q
            elif side == "south":
                qy[:, 0] = q
            else:
                qy[:, -1] = q
        return qx, qy

    def gradient(self):
        """Cell-centred gradient (one-sided at the walls)."""
        g = self.grid
        gx = np.gradient(self.phi, g.x, axis=0) if g.nx > 1 else np.zeros(g.shape)
        gy = np.gradient(self.phi, g.y, axis=1) if g.ny > 1 else np.zeros(g.shape)
        return gx, gy

    def dissipation(self) -> float:
        """sum over faces of G (delta phi)^2 = integral of K |grad phi|^2
        (twice the stored field energy in electro/magnetostatics)."""
        p, g = self.problem, self.grid
        tot = 0.0
        if g.nx > 1:
            G = g.Ax[1:-1, :] * p.face_U(0)
            tot += float(np.sum(G * np.diff(self.phi, axis=0) ** 2))
        if g.ny > 1:
            G = g.Ay[:, 1:-1] * p.face_U(1)
            tot += float(np.sum(G * np.diff(self.phi, axis=1) ** 2))
        for side in SIDES:
            bc = p.bcs[side]
            if bc.kind != "dirichlet":
                continue
            sl, A, d, _ = p._side(side)
            val = np.broadcast_to(np.asarray(bc.value, float), A.shape)
            tot += float(np.sum(A * p.K[sl] / d * (val - self.phi[sl]) ** 2))
        return tot

    def sample(self, x, y=None) -> float:
        """(Bi)linear interpolation of phi at a point."""
        g = self.grid
        if g.ny == 1:
            return float(np.interp(x, g.x, self.phi[:, 0]))
        i = int(np.clip(np.searchsorted(g.x, x) - 1, 0, g.nx - 2))
        j = int(np.clip(np.searchsorted(g.y, y) - 1, 0, g.ny - 2))
        tx = float(np.clip((x - g.x[i]) / (g.x[i + 1] - g.x[i]), 0, 1))
        ty = float(np.clip((y - g.y[j]) / (g.y[j + 1] - g.y[j]), 0, 1))
        f = self.phi
        return float((1 - tx) * (1 - ty) * f[i, j] + tx * (1 - ty) * f[i + 1, j]
                     + (1 - tx) * ty * f[i, j + 1] + tx * ty * f[i + 1, j + 1])

    # -- reusable checks --------------------------------------------------------
    def conservation_check(self, name, category, unit="", tol=1e-8) -> ck.Check:
        """In - out + generated - stored = 0."""
        if self.history is not None:
            gross = max(abs(self.stored_change), abs(self.net_input), 1e-300)
            return ck.small(name, category,
                            (self.stored_change - self.net_input) / gross, tol,
                            f"stored {self.stored_change:.6g}{unit} vs time-integrated "
                            f"net input {self.net_input:.6g}{unit}; relative mismatch")
        net, gross = self.balance()
        inflow = self.boundary_inflow()
        parts = ", ".join(f"{s} {v:+.4g}" for s, v in inflow.items() if v)
        return ck.small(name, category, net / gross, tol,
                        f"sources {self.source_total():+.4g}, losses "
                        f"{-self.sink_total():+.4g}, boundaries [{parts}]{unit}; "
                        f"relative imbalance")

    def symmetry_checks(self, tol=1e-8) -> list:
        out = []
        g = self.grid
        for axis, n, label in ((0, g.nx, "x"), (1, g.ny, "y")):
            if n > 1 and g.coord == "cartesian" and self.problem.mirror_symmetric(axis):
                out.append(ck.symmetry(f"symmetry about the {label} mid-plane",
                                       self.phi, np.flip(self.phi, axis), tol,
                                       "setup is mirror-symmetric, so is the solution"))
        return out


def manufactured_error(n: int) -> float:
    """Max error of the 2-D operator on a manufactured solution with variable
    K, a loss term and mixed Dirichlet/Neumann boundaries (unit square)."""
    g = Grid(n, 1.0, n, 1.0)
    X, Y = g.mesh()
    exact = np.sin(np.pi * X) * np.cos(np.pi * Y) + 2.0
    K, s = 1.0 + 0.5 * X, 2.0
    f = (2 * K * np.pi ** 2 * np.sin(np.pi * X) * np.cos(np.pi * Y)
         - 0.5 * np.pi * np.cos(np.pi * X) * np.cos(np.pi * Y) + s * exact)
    prob = TransportProblem(g, K, source=f, sink=s,
                            bcs={"west": dirichlet(2.0), "east": dirichlet(2.0)})
    return float(np.max(np.abs(prob.solve_steady().phi - exact)))


def operator_benchmark(category=ck.UNIVERSAL) -> ck.Check:
    """Known-solution check for setups with no closed form of their own: the
    same discrete operator must reproduce a manufactured solution at 2nd order."""
    e1, e2 = manufactured_error(16), manufactured_error(32)
    order = math.log(e1 / e2) / math.log(2)
    ok = e2 < 5e-3 and order > 1.8
    return ck.Check("known solution: manufactured benchmark", category,
                    ck.PASS if ok else ck.FAIL,
                    f"same solver on a manufactured exact solution: error {e1:.3g} -> "
                    f"{e2:.3g} when the grid is halved (order {order:.2f}, expect 2)",
                    order, 2.0, 0.2)


def transient_error(n: int) -> float:
    """Max error against the semi-infinite-solid solution erfc(x / 2 sqrt(t))
    for a step change of surface value (unit diffusivity, t = 0.01)."""
    g = Grid(n, 1.0)
    prob = TransportProblem(g, 1.0, bcs={"west": dirichlet(1.0)})
    t_end = 0.01
    sol = prob.solve_transient(0.0, t_end, t_end / (n * n / 16), theta=1.0)
    exact = np.array([math.erfc(x / (2 * math.sqrt(t_end))) for x in g.x])
    return float(np.max(np.abs(sol.phi[:, 0] - exact)))


def transient_benchmark(category=ck.UNIVERSAL) -> ck.Check:
    e1, e2 = transient_error(32), transient_error(64)
    order = math.log(e1 / e2) / math.log(2)
    ok = e2 < 5e-3 and order > 1.6
    return ck.Check("known solution: transient benchmark", category,
                    ck.PASS if ok else ck.FAIL,
                    f"same time-stepper on the exact erfc solution of a suddenly "
                    f"heated half-space: error {e1:.3g} -> {e2:.3g} on refinement "
                    f"(order {order:.2f}, expect 2)", order, 2.0, 0.4)
