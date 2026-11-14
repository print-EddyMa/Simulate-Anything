"""Checks shared by every heat-transfer simulation (temperatures in kelvin)."""
from __future__ import annotations

import numpy as np

from .. import checks as ck
from ..solvers.transport import TransportSolution

SIGMA = 5.670374419e-8      # Stefan-Boltzmann constant, W/(m^2 K^4)


def to_c(kelvin: float) -> float:
    return float(kelvin) - 273.15


def entropy_check(ts: TransportSolution, generation=None, loss=None,
                  name="entropy generation >= 0") -> ck.Check:
    """Second law: entropy handed to the surroundings minus entropy received
    with the heat input, plus what the body stores, must not be negative.

    Closed systems only: heat crossing a boundary is divided by the
    temperature it crosses at.  With flow through the domain the stream also
    carries entropy (m cp ln T), which needs its own budget - see
    ``cooling_channel`` in sims/fluids.py.

    ``generation`` is the true volumetric heating [W/m^3] (defaults to the
    problem source); ``loss`` = (volumetric loss coefficient, ambient K) for a
    distributed convective loss that was folded into source/sink terms.
    """
    p, g, T = ts.problem, ts.grid, ts.phi
    if np.any(T <= 0):
        return ck.truth(name, ck.HEAT, False, "non-positive absolute temperature")
    free = ~p.fixed
    terms = []
    for q_out, t_env in ts.boundary_exchange():
        terms.append(q_out / t_env)                       # to/from surroundings
    gen = p.source if generation is None else g.full(generation)
    terms.append((-(gen * g.vol) / T)[free])              # heat released at local T
    if loss is not None:
        hv, t_inf = loss
        terms.append((g.full(hv) * g.vol * (T - t_inf) / t_inf)[free])
    A0, b0 = p.assemble()
    stored = (b0 - A0 @ T.ravel()).reshape(g.shape)       # C V dT/dt per cell
    terms.append((stored / T)[free])
    flat = np.concatenate([np.ravel(t) for t in terms])
    s_gen, scale = float(flat.sum()), float(np.abs(flat).sum()) + 1e-300
    ok = s_gen >= -1e-9 * scale
    return ck.Check(name, ck.HEAT, ck.PASS if ok else ck.FAIL,
                    f"total entropy production {s_gen:.4g} W/K "
                    f"({'heat only flows from hot to cold' if ok else 'second law violated'})",
                    s_gen, 0.0)


def bounds_check(ts: TransportSolution, lo=None, hi=None, why="") -> ck.Check:
    """Maximum principle: without sources nothing gets colder than the coldest
    or hotter than the hottest temperature imposed on the problem."""
    name = "temperature bounds (maximum principle)"
    vmin, vmax = float(ts.phi.min()), float(ts.phi.max())
    if lo is None and hi is None:
        return ck.skip(name, ck.HEAT, why or "sources of both signs, no a-priori bound")
    c = ck.bounds(name, ck.HEAT, -np.inf if lo is None else lo,
                  np.inf if hi is None else hi, vmin, vmax, unit="K")
    if why:
        c.detail += f" ({why})"
    return c


def heat_checks(ts: TransportSolution, lo=None, hi=None, generation=None,
                loss=None, why="") -> list:
    ambient = None if loss is None else loss[1]
    unit = " W" if ts.history is None else " J"      # a transient balances energy
    return [ts.conservation_check("energy balance (in = out + stored)", ck.HEAT, unit,
                                  ambient=ambient),
            bounds_check(ts, lo, hi, why),
            entropy_check(ts, generation, loss)]
