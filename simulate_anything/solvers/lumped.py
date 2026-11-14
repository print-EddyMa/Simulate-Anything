"""Lumped thermal networks: nodes joined by conductive and radiative links.

    C_i dT_i/dt = Q_i(t) - sum_j G_ij (T_i - T_j) - sum_j sigma R_ij (T_i^4 - T_j^4)

Nodes either carry a heat capacity (unknowns) or a fixed temperature (deep
space, ambient air...).  The solver is implicit (theta scheme with Newton
iteration, so the T^4 terms are handled exactly) and keeps a running energy
tally built from the *link formulas* rather than from the matrix, which is
what the energy-balance check then compares against the stored energy.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

SIGMA = 5.670374419e-8      # Stefan-Boltzmann constant, W/(m^2 K^4)


@dataclass
class SteadyResult:
    T: np.ndarray               # all node temperatures [K]
    residual: float             # max nodal imbalance / gross heat flow
    iterations: int
    converged: bool


@dataclass
class MarchResult:
    t: np.ndarray               # times, len nsteps + 1
    T: np.ndarray               # temperatures [len(t), n_nodes]
    absorbed: float             # time-integrated heat input to free nodes [J]
    emitted: float              # energy handed to fixed-temperature nodes [J]
    stored: float               # sum C (T_end - T_start) [J]
    cond_energy: np.ndarray     # energy carried i -> j by every conductive link
    rad_energy: np.ndarray      # same for the radiative links
    sensitivity: np.ndarray | None = None   # d T_end / d T_start (free nodes)
    newton_max: int = 0
    notes: list = field(default_factory=list)


class ThermalNetwork:
    def __init__(self):
        self.names, self._C, self._fixed = [], [], []
        self._cond, self._rad = [], []
        self._ready = False

    # -- construction ----------------------------------------------------------
    def node(self, name, capacity=0.0, fixed=None) -> int:
        """Add a node.  ``fixed`` (kelvin) makes it a boundary node."""
        self.names.append(name)
        self._C.append(float(capacity))
        self._fixed.append(None if fixed is None else float(fixed))
        self._ready = False
        return len(self.names) - 1

    def conductance(self, i, j, G):
        """Linear link: heat i -> j is G (Ti - Tj), G in W/K."""
        self._cond.append((int(i), int(j), float(G)))
        self._ready = False

    def radiation(self, i, j, area_factor):
        """Radiative link: heat i -> j is sigma R (Ti^4 - Tj^4) with
        R = emissivity x area x view factor in m^2."""
        self._rad.append((int(i), int(j), float(area_factor)))
        self._ready = False

    def _prep(self):
        if self._ready:
            return
        n = self.n = len(self.names)
        self.C = np.array(self._C)
        self.is_fixed = np.array([f is not None for f in self._fixed])
        self.T_fixed = np.array([0.0 if f is None else f for f in self._fixed])
        self.free = np.flatnonzero(~self.is_fixed)
        self.fix = np.flatnonzero(self.is_fixed)

        def cols(links):
            if not links:
                return (np.zeros(0, int), np.zeros(0, int), np.zeros(0))
            a = np.array(links, float)
            return a[:, 0].astype(int), a[:, 1].astype(int), a[:, 2]

        self.ci, self.cj, self.cG = cols(self._cond)
        self.ri, self.rj, self.rR = cols(self._rad)
        self._ready = True

    def full(self, T_free) -> np.ndarray:
        """All node temperatures from the free-node values."""
        self._prep()
        T = self.T_fixed.copy()
        T[self.free] = T_free
        return T

    # -- physics ---------------------------------------------------------------
    def link_flows(self, T):
        """Heat carried i -> j by each conductive and each radiative link [W]."""
        self._prep()
        qc = self.cG * (T[self.ci] - T[self.cj])
        qr = SIGMA * self.rR * (T[self.ri] ** 4 - T[self.rj] ** 4)
        return qc, qr

    def net_inflow(self, T) -> np.ndarray:
        """Heat arriving at every node through the links [W]."""
        qc, qr = self.link_flows(T)
        n = self.n
        return (np.bincount(self.cj, qc, n) - np.bincount(self.ci, qc, n)
                + np.bincount(self.rj, qr, n) - np.bincount(self.ri, qr, n))

    def jacobian(self, T) -> np.ndarray:
        """d net_inflow / dT as a dense matrix."""
        self._prep()
        n = self.n
        J = np.zeros((n, n))
        for i, j, di, dj in ((self.ci, self.cj, self.cG, self.cG),
                             (self.ri, self.rj,
                              4 * SIGMA * self.rR * T[self.ri] ** 3,
                              4 * SIGMA * self.rR * T[self.rj] ** 3)):
            np.add.at(J, (i, i), -di)
            np.add.at(J, (i, j), dj)
            np.add.at(J, (j, i), di)
            np.add.at(J, (j, j), -dj)
        return J

    def to_fixed(self, qc, qr) -> float:
        """Net heat (or energy) leaving the free nodes for the fixed ones,
        given per-link flows i -> j."""
        f = self.is_fixed
        out = 0.0
        for i, j, q in ((self.ci, self.cj, qc), (self.ri, self.rj, qr)):
            out += float(np.sum(q[f[j] & ~f[i]]) - np.sum(q[f[i] & ~f[j]]))
        return out

    def rates(self, T) -> np.ndarray:
        """Decay rates [1/s] of small perturbations about state ``T``."""
        self._prep()
        fr = self.free
        J = self.jacobian(T)[np.ix_(fr, fr)]
        return np.sort(np.real(np.linalg.eigvals(-J / self.C[fr][:, None])))

    # -- steady state ----------------------------------------------------------
    def steady(self, inputs, guess=300.0, tol=1e-13, maxit=200) -> SteadyResult:
        """Newton solve of ``inputs + net_inflow(T) = 0`` on the free nodes.

        ``inputs`` is an array over all nodes [W] or a callable
        ``T -> (Q, dQ/dT)`` for temperature-dependent heating."""
        self._prep()
        fr = self.free
        T = self.full(np.broadcast_to(np.asarray(guess, float), fr.shape).copy())

        def load(T):
            if callable(inputs):
                Q, dQ = inputs(T)
                return np.asarray(Q, float), np.asarray(dQ, float)
            return np.asarray(inputs, float), np.zeros(self.n)

        def resid(T):
            Q, dQ = load(T)
            return (Q + self.net_inflow(T))[fr], dQ, Q

        F, dQ, Q = resid(T)
        it, converged = 0, False
        for it in range(1, maxit + 1):
            J = self.jacobian(T)[np.ix_(fr, fr)] + np.diag(dQ[fr])
            try:
                step = np.linalg.solve(J, -F)
            except np.linalg.LinAlgError:
                break
            lam, accepted = 1.0, None
            while lam > 1e-8:           # damped: keep absolute temperatures positive
                trial = T.copy()
                trial[fr] = T[fr] + lam * step
                if np.all(trial[fr] > 0) and np.all(np.isfinite(trial)):
                    Ft, dQt, Qt = resid(trial)
                    accepted = (trial, Ft, dQt, Qt)
                    if np.linalg.norm(Ft) <= np.linalg.norm(F) * (1 - 1e-4 * lam) \
                            or lam < 1e-3:
                        break
                lam *= 0.5
            if accepted is None:
                break
            T, F, dQ, Q = accepted
            if np.max(np.abs(lam * step)) <= tol * np.max(np.abs(T[fr])):
                converged = True
                break
        qc, qr = self.link_flows(T)
        gross = float(np.sum(np.abs(Q[fr])) + np.sum(np.abs(qc)) + np.sum(np.abs(qr)))
        res = float(np.max(np.abs(F))) / max(gross / 2, 1e-300) if len(fr) else 0.0
        return SteadyResult(T, res, it, converged)

    # -- transient -------------------------------------------------------------
    def march(self, T0, times, q_avg, theta=0.5, tol=1e-12,
              sensitivity=False) -> MarchResult:
        """Integrate over the time points ``times``.

        ``q_avg(t0, t1)`` returns the heat input to every node averaged over a
        step (so on/off loads such as eclipses are integrated exactly).  With
        ``theta = 0.5`` this is the trapezoidal rule (2nd order); ``1`` gives
        backward Euler."""
        self._prep()
        fr, C = self.free, self.C[self.free]
        times = np.asarray(times, float)
        T = self.full(np.asarray(T0, float))
        hist = np.empty((len(times), self.n))
        hist[0] = T
        qc_old, qr_old = self.link_flows(T)
        N_old = self.net_inflow(T)[fr]
        J_old = self.jacobian(T)[np.ix_(fr, fr)] if sensitivity else None
        S = np.eye(len(fr)) if sensitivity else None
        absorbed, Ec, Er = 0.0, np.zeros(len(self.cG)), np.zeros(len(self.rR))
        newton_max = 0
        for k in range(len(times) - 1):
            dt = times[k + 1] - times[k]
            Qbar = np.asarray(q_avg(times[k], times[k + 1]), float)
            const = C * T[fr] / dt + Qbar[fr] + (1 - theta) * N_old
            new = T.copy()
            for it in range(1, 30):
                N = self.net_inflow(new)[fr]
                F = C * new[fr] / dt - theta * N - const
                J = np.diag(C / dt) - theta * self.jacobian(new)[np.ix_(fr, fr)]
                dx = np.linalg.solve(J, -F)
                new[fr] += dx
                if np.max(np.abs(dx)) <= tol * np.max(np.abs(new[fr])):
                    break
            newton_max = max(newton_max, it)
            qc, qr = self.link_flows(new)
            N_new = self.net_inflow(new)[fr]
            if sensitivity:
                J_new = self.jacobian(new)[np.ix_(fr, fr)]
                S = np.linalg.solve(np.diag(C / dt) - theta * J_new,
                                    (np.diag(C / dt) + (1 - theta) * J_old) @ S)
                J_old = J_new
            absorbed += dt * float(np.sum(Qbar[fr]))
            Ec += dt * (theta * qc + (1 - theta) * qc_old)
            Er += dt * (theta * qr + (1 - theta) * qr_old)
            T, qc_old, qr_old, N_old = new, qc, qr, N_new
            hist[k + 1] = T
        stored = float(np.sum(C * (hist[-1, fr] - hist[0, fr])))
        return MarchResult(times, hist, absorbed, self.to_fixed(Ec, Er), stored,
                           Ec, Er, S, newton_max)
