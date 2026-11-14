"""Finite-difference time-domain (Yee) solvers for Maxwell's equations.

``run_1d``  - plane wave (Ey, Hz) travelling along x through layered, lossy
              media, with first-order Mur absorbing ends.
``run_tmz`` - 2-D TMz fields (Ez, Hx, Hy) radiated by line currents in free
              space.  The outer wall is a perfect conductor; callers time-gate
              so that its reflection never reaches a probe.

Both record what the energy checks need: Poynting flux through probes or
closed contours, ohmic loss, the work done by the sources and the discrete
divergence of B.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

C0 = 299792458.0
MU0 = 4e-7 * math.pi
EPS0 = 1.0 / (MU0 * C0 ** 2)
ETA0 = MU0 * C0


def gauss_pulse(t, f0, tau, t0, phase=0.0):
    """Gaussian-windowed carrier: exp(-((t-t0)/tau)^2) sin(2 pi f0 (t-t0) + phase)."""
    return np.exp(-((t - t0) / tau) ** 2) * np.sin(2 * np.pi * f0 * (t - t0) + phase)


def dft(series, t, f0):
    """Single-frequency Fourier transform of sampled time series (axis 0)."""
    w = np.exp(-2j * np.pi * f0 * np.asarray(t))
    return np.tensordot(w, np.asarray(series), axes=(0, 0)) * (t[1] - t[0])


# ---------------------------------------------------------------------------
# 1-D
# ---------------------------------------------------------------------------
@dataclass
class Run1D:
    t: np.ndarray            # times of the recorded E samples
    E: np.ndarray            # E at the probes, shape (steps, n_probes)
    H: np.ndarray            # H co-located with E (space/time average)
    absorbed: float          # integral of sigma E^2 over space and time [J/m^2]
    energy_left: float       # field energy still on the grid at the end [J/m^2]
    dt: float


def run_1d(eps_r, sigma, dx, courant, src_index, src_fn, n_steps, probes) -> Run1D:
    """March Ey/Hz on a 1-D Yee grid.  ``eps_r``/``sigma`` live on the E nodes."""
    eps_r = np.asarray(eps_r, float)
    sigma = np.asarray(sigma, float)
    n = len(eps_r)
    dt = courant * dx / C0
    loss = sigma * dt / (2 * EPS0 * eps_r)
    ca = (1 - loss) / (1 + loss)
    cb = courant * ETA0 / eps_r / (1 + loss)
    ch = courant / ETA0
    mur = (courant - 1.0) / (courant + 1.0)
    E, H = np.zeros(n), np.zeros(n - 1)
    p = np.asarray(probes, int)
    e_rec = np.zeros((n_steps, len(p)))
    h_rec = np.zeros((n_steps, len(p)))
    lossy = np.nonzero(sigma)[0]
    sl = slice(lossy.min(), lossy.max() + 1) if lossy.size else None
    absorbed = 0.0
    with np.errstate(all="ignore"):
        for k in range(n_steps):
            h_old = H[p] + H[p - 1]
            H -= ch * (E[1:] - E[:-1])
            h_rec[k] = 0.25 * (h_old + H[p] + H[p - 1])
            e_rec[k] = E[p]
            e0, e1, em, en = E[0], E[1], E[-2], E[-1]
            if sl is not None:
                e_old = E[sl].copy()
            E[1:-1] = ca[1:-1] * E[1:-1] - cb[1:-1] * (H[1:] - H[:-1])
            E[src_index] += src_fn((k + 0.5) * dt)
            E[0] = e1 + mur * (E[1] - e0)
            E[-1] = em + mur * (E[-2] - en)
            if sl is not None:
                absorbed += float(np.sum(sigma[sl] * (0.5 * (E[sl] + e_old)) ** 2))
            if k % 64 == 0 and not math.isfinite(E[n // 2]):
                e_rec[k:], h_rec[k:] = np.nan, np.nan     # unstable: stop marching
                break
        left = 0.5 * EPS0 * float(np.sum(eps_r * E ** 2)) * dx \
            + 0.5 * MU0 * float(np.sum(H ** 2)) * dx
    return Run1D(np.arange(n_steps) * dt, e_rec, h_rec, absorbed * dx * dt, left, dt)


def slab_exact(f0, eps_r, sigma, thickness):
    """Exact reflectance / transmittance of a lossy slab in vacuum at normal
    incidence (Fresnel coefficients + multiple reflections)."""
    w = 2 * math.pi * f0
    n_c = np.sqrt(complex(eps_r, -sigma / (w * EPS0)))     # exp(+j w t) convention
    if n_c.imag > 0:
        n_c = -n_c
    k = w / C0 * n_c
    r12 = (1 - n_c) / (1 + n_c)
    ph = np.exp(-2j * k * thickness)
    r = r12 * (1 - ph) / (1 - r12 ** 2 * ph)
    t = (1 - r12 ** 2) * np.exp(-1j * k * thickness) / (1 - r12 ** 2 * ph)
    return abs(r) ** 2, abs(t) ** 2, n_c


# ---------------------------------------------------------------------------
# 2-D TMz
# ---------------------------------------------------------------------------
@dataclass
class RunTMz:
    dt: float
    probe_dft: np.ndarray            # complex Ez(f0) at the probe points
    source_dft: np.ndarray           # complex I_k(f0) of every source
    contour_energy: list             # energy that left each closed contour [J/m]
    source_work: float               # work done by the sources on the field [J/m]
    energy_inside: float             # field energy left inside the outer contour
    div_b: float                     # max |div B| * dx / max |B|
    snapshot: np.ndarray = field(default=None)   # Ez at ``snapshot_step``
    finite: bool = True


def run_tmz(n_half, dx, courant, f0, n_steps, sources, contours, probe_xy,
            snapshot_step=None) -> RunTMz:
    """2-D TMz Yee march on a square of (2 n_half + 1)^2 Ez nodes centred on
    the origin.

    sources   : list of (x, y, current_fn) - line currents [A] along z
    contours  : half-widths (in cells) of square contours for the Poynting flux
    probe_xy  : (M, 2) points where Ez is Fourier-transformed at ``f0``
    """
    n = 2 * n_half + 1
    dt = courant * dx / C0
    ce, chh = dt / (EPS0 * dx), dt / (MU0 * dx)
    Ez = np.zeros((n, n))
    Hx = np.zeros((n, n - 1))        # at (i, j + 1/2)
    Hy = np.zeros((n - 1, n))        # at (i + 1/2, j)

    def weights(x, y):
        """Bilinear interpolation stencil: flat node indices and weights."""
        fx, fy = x / dx + n_half, y / dx + n_half
        i, j = int(math.floor(fx)), int(math.floor(fy))
        tx, ty = fx - i, fy - j
        idx = [(i, j), (i + 1, j), (i, j + 1), (i + 1, j + 1)]
        w = [(1 - tx) * (1 - ty), tx * (1 - ty), (1 - tx) * ty, tx * ty]
        keep = [(a, b) for a, b in zip(idx, w) if b > 1e-12]
        return ([a[0] * n + a[1] for a, _ in keep], [b for _, b in keep])

    src = [(np.array(ix), np.array(w), fn) for (x, y, fn) in sources
           for ix, w in [weights(x, y)]]
    pi_, pw_ = [], []
    for x, y in probe_xy:
        ix, w = weights(x, y)
        ix, w = (ix + [ix[0]] * 4)[:4], (w + [0.0] * 4)[:4]
        pi_.append(ix)
        pw_.append(w)
    pi_, pw_ = np.array(pi_), np.array(pw_)
    acc = np.zeros(len(probe_xy), complex)
    src_acc = np.zeros(len(src), complex)
    energy = [0.0] * len(contours)
    work = 0.0
    snapshot = None
    c = n_half

    def lines(m):
        """Ez on the two node lines straddling each side of contour ``m``."""
        a, b = c - m, c + m + 1                       # node range along a side
        return (0.5 * (Ez[c + m, a:b] + Ez[c + m + 1, a:b]),      # east
                0.5 * (Ez[c - m, a:b] + Ez[c - m - 1, a:b]),      # west
                0.5 * (Ez[a:b, c + m] + Ez[a:b, c + m + 1]),      # north
                0.5 * (Ez[a:b, c - m] + Ez[a:b, c - m - 1]))      # south

    flat = Ez.reshape(-1)
    with np.errstate(all="ignore"):
        for k in range(n_steps):
            Hx -= chh * (Ez[:, 1:] - Ez[:, :-1])
            Hy += chh * (Ez[1:, :] - Ez[:-1, :])
            old_lines = [lines(m) for m in contours]
            old_src = [float(np.dot(flat[ix], w)) for ix, w, _ in src]
            Ez[1:-1, 1:-1] += ce * ((Hy[1:, 1:-1] - Hy[:-1, 1:-1])
                                    - (Hx[1:-1, 1:] - Hx[1:-1, :-1]))
            t_half = (k + 0.5) * dt
            for s, (ix, w, fn) in enumerate(src):
                cur = fn(t_half)
                flat[ix] -= dt / EPS0 * cur * w / dx ** 2
                e_mid = 0.5 * (old_src[s] + float(np.dot(flat[ix], w)))
                work -= cur * e_mid * dt
                src_acc[s] += cur * np.exp(-2j * np.pi * f0 * t_half) * dt
            for q, m in enumerate(contours):
                a, b = c - m, c + m + 1
                e, w_, nn, s_ = (0.5 * (o + v) for o, v in zip(old_lines[q], lines(m)))
                out = (np.sum(-e * Hy[c + m, a:b]) - np.sum(-w_ * Hy[c - m - 1, a:b])
                       + np.sum(nn * Hx[a:b, c + m]) - np.sum(s_ * Hx[a:b, c - m - 1]))
                energy[q] += float(out) * dx * dt
            acc += (flat[pi_] * pw_).sum(axis=1) * np.exp(
                -2j * np.pi * f0 * (k + 1) * dt) * dt
            if snapshot_step is not None and k == snapshot_step:
                snapshot = Ez.copy()
            if k % 32 == 0 and not np.isfinite(work):
                acc[:] = np.nan                          # unstable: stop marching
                break
        m = max(contours) if contours else n_half - 1
        a, b = c - m, c + m + 1
        inside = (0.5 * EPS0 * float(np.sum(Ez[a:b, a:b] ** 2))
                  + 0.5 * MU0 * float(np.sum(Hx[a:b, a:b - 1] ** 2)
                                      + np.sum(Hy[a:b - 1, a:b] ** 2))) * dx ** 2
        div = (Hx[1:, :] - Hx[:-1, :]) + (Hy[:, 1:] - Hy[:, :-1])
        hmax = max(float(np.max(np.abs(Hx))), float(np.max(np.abs(Hy))), 1e-300)
        div_b = float(np.max(np.abs(div))) / hmax
    ok = bool(np.all(np.isfinite(Ez)))
    return RunTMz(dt, acc, src_acc, energy, work, inside, div_b,
                  snapshot if snapshot is not None else Ez.copy(), ok)
