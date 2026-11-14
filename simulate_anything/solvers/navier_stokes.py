"""2-D incompressible Navier-Stokes on a staggered (MAC) grid.

Chorin projection method, explicit in time, second-order central in space,
marched to steady state.  Velocities live on cell faces and pressure in cell
centres, so the discrete divergence of the projected velocity is zero to
round-off in every cell - which is what the mass-conservation check tests.

Two configurations are supported:

* closed box with a moving lid (lid-driven cavity);
* channel: prescribed inflow on the west side, outflow (zero gradient,
  p = 0) on the east side, no-slip walls south and north.

The pressure Poisson equation is solved directly: the 5-point Laplacian on a
uniform rectangle separates, so two small 1-D eigen-decompositions are
computed once and every solve is four dense matrix products.
"""
from __future__ import annotations

import math

import numpy as np


def _lap1d(n: int, h: float, dirichlet_hi: bool = False):
    """Eigen-system of the 1-D cell-centred Laplacian (Neumann ends, or a
    Dirichlet value of zero on the high-side face)."""
    main = -2.0 * np.ones(n)
    main[0] = -1.0
    main[-1] = -3.0 if dirichlet_hi else -1.0
    A = (np.diag(main) + np.diag(np.ones(n - 1), 1) + np.diag(np.ones(n - 1), -1)) / h ** 2
    return A, *np.linalg.eigh(A)


class Poisson2D:
    """Direct solver for lap(p) = rhs with homogeneous Neumann walls and an
    optional p = 0 condition on the east face."""

    def __init__(self, nx, ny, dx, dy, east_dirichlet=False):
        self.Ax, lx, self.Qx = _lap1d(nx, dx, east_dirichlet)
        self.Ay, ly, self.Qy = _lap1d(ny, dy)
        lam = lx[:, None] + ly[None, :]
        self.singular = not east_dirichlet
        if self.singular:          # constant mode: pressure level is arbitrary
            k = np.unravel_index(np.argmax(lam), lam.shape)
            lam[k] = 1.0
            self.inv = 1.0 / lam
            self.inv[k] = 0.0
        else:
            self.inv = 1.0 / lam

    def solve(self, rhs):
        return self.Qx @ ((self.Qx.T @ rhs @ self.Qy) * self.inv) @ self.Qy.T

    def apply(self, p):
        return self.Ax @ p + p @ self.Ay.T


class IncompressibleFlow2D:
    """u[(nx+1), ny] on x-faces, v[nx, (ny+1)] on y-faces, p[nx, ny] (p / rho)."""

    def __init__(self, nx, ny, lx, ly, nu, lid=0.0, inflow=None):
        self.nx, self.ny, self.lx, self.ly, self.nu = nx, ny, lx, ly, nu
        self.dx, self.dy = lx / nx, ly / ny
        self.lid = lid
        self.outflow = inflow is not None
        self.u = np.zeros((nx + 1, ny))
        self.v = np.zeros((nx, ny + 1))
        self.p = np.zeros((nx, ny))
        if self.outflow:
            self.u[:, :] = np.broadcast_to(np.asarray(inflow, float), (ny,))[None, :]
        self.poisson = Poisson2D(nx, ny, self.dx, self.dy, self.outflow)
        self.x = (np.arange(nx) + 0.5) * self.dx
        self.y = (np.arange(ny) + 0.5) * self.dy
        self.t, self.steps = 0.0, 0
        self.steady_residual = math.inf
        self.blown_up = False

    # -- stability -------------------------------------------------------------
    def dt_limit(self, speed: float) -> float:
        """Largest stable step of the explicit central scheme: viscous limit,
        the convection-diffusion limit 2 nu / |u|^2 and the Courant limit."""
        dt = 1.0 / (2.0 * self.nu * (1 / self.dx ** 2 + 1 / self.dy ** 2))
        if speed > 0:
            dt = min(dt, 2.0 * self.nu / speed ** 2,
                     1.0 / (speed / self.dx + speed / self.dy))
        return dt

    def max_speed(self) -> float:
        return float(max(np.max(np.abs(self.u)), np.max(np.abs(self.v)), abs(self.lid)))

    # -- one projection step ---------------------------------------------------
    def step(self, dt: float):
        nx, ny, dx, dy, nu = self.nx, self.ny, self.dx, self.dy, self.nu
        u, v = self.u, self.v
        m = nx if self.outflow else nx - 1          # last u-face that is advanced
        ue = np.vstack([u, u[-1:]]) if self.outflow else u
        ug = np.empty((ue.shape[0], ny + 2))        # ghost rows enforce no-slip
        ug[:, 1:-1] = ue
        ug[:, 0] = -ue[:, 0]
        ug[:, -1] = 2.0 * self.lid - ue[:, -1]
        vg = np.empty((nx + 2, ny + 1))
        vg[1:-1] = v
        vg[0] = -v[0]
        vg[-1] = v[-1] if self.outflow else -v[-1]

        # x-momentum on faces 1..m
        uc, uw, ueast = ue[1:m + 1], ue[0:m], ue[2:m + 2]
        ugc = ug[1:m + 1]
        un = 0.5 * (ugc[:, 1:-1] + ugc[:, 2:])
        us = 0.5 * (ugc[:, :-2] + ugc[:, 1:-1])
        vn = 0.5 * (vg[1:m + 1, 1:] + vg[2:m + 2, 1:])
        vs = 0.5 * (vg[1:m + 1, :-1] + vg[2:m + 2, :-1])
        conv = ((0.5 * (uc + ueast)) ** 2 - (0.5 * (uw + uc)) ** 2) / dx \
            + (un * vn - us * vs) / dy
        lap = (ueast - 2 * uc + uw) / dx ** 2 \
            + (ugc[:, 2:] - 2 * ugc[:, 1:-1] + ugc[:, :-2]) / dy ** 2
        ustar = u.copy()
        ustar[1:m + 1] = uc + dt * (nu * lap - conv)

        # y-momentum on faces 1..ny-1
        vc, vsouth, vnorth = v[:, 1:-1], v[:, :-2], v[:, 2:]
        uec = 0.5 * (u[1:, :-1] + u[1:, 1:])
        uwc = 0.5 * (u[:-1, :-1] + u[:-1, 1:])
        ve = 0.5 * (vg[1:-1, 1:-1] + vg[2:, 1:-1])
        vw = 0.5 * (vg[:-2, 1:-1] + vg[1:-1, 1:-1])
        conv = (uec * ve - uwc * vw) / dx \
            + ((0.5 * (vc + vnorth)) ** 2 - (0.5 * (vsouth + vc)) ** 2) / dy
        lap = (vg[2:, 1:-1] - 2 * vc + vg[:-2, 1:-1]) / dx ** 2 \
            + (vnorth - 2 * vc + vsouth) / dy ** 2
        vstar = v.copy()
        vstar[:, 1:-1] = vc + dt * (nu * lap - conv)

        # projection
        div = (ustar[1:] - ustar[:-1]) / dx + (vstar[:, 1:] - vstar[:, :-1]) / dy
        p = self.poisson.solve(div / dt)
        unew, vnew = ustar, vstar
        unew[1:nx] -= dt * (p[1:] - p[:-1]) / dx
        if self.outflow:
            unew[nx] -= dt * (0.0 - p[-1]) / (dx / 2)
        vnew[:, 1:-1] -= dt * (p[:, 1:] - p[:, :-1]) / dy
        change = max(float(np.max(np.abs(unew - u))), float(np.max(np.abs(vnew - v))))
        self.u, self.v, self.p = unew, vnew, p
        self.t += dt
        self.steps += 1
        return change / dt

    def run(self, dt, t_max, tol, accel_scale, check_every=25):
        """March until max |du/dt| < tol * accel_scale or t_max is reached."""
        while self.t < t_max:
            rate = 0.0
            for _ in range(check_every):
                rate = self.step(dt)
            if not math.isfinite(rate) or rate > 1e12 * accel_scale:
                self.blown_up = True
                self.steady_residual = math.inf
                break
            self.steady_residual = rate / accel_scale
            if self.steady_residual < tol:
                break
        return self

    # -- diagnostics -------------------------------------------------------------
    def divergence(self):
        return (self.u[1:] - self.u[:-1]) / self.dx + (self.v[:, 1:] - self.v[:, :-1]) / self.dy

    def poisson_residual(self) -> float:
        """Relative residual of the last pressure solve (direct solver)."""
        # after projection div(u) = 0 exactly iff the Poisson solve was exact
        scale = max(self.max_speed() / min(self.dx, self.dy), 1e-300)
        return float(np.max(np.abs(self.divergence()))) / scale

    def stream_function(self):
        """psi at cell corners, (nx+1, ny+1); psi = 0 on the south wall."""
        psi = np.zeros((self.nx + 1, self.ny + 1))
        psi[:, 1:] = np.cumsum(self.u * self.dy, axis=1)
        return psi

    def centre_velocity(self):
        return 0.5 * (self.u[1:] + self.u[:-1]), 0.5 * (self.v[:, 1:] + self.v[:, :-1])
