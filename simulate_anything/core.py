"""Simulation base class: parameters with units in, verified result out.

A concrete simulation only has to say how to solve itself at a resolution
``n`` and which physical laws its answer must obey.  :meth:`Simulation.run`
then drives the whole self-checking protocol:

1. dimensional analysis of inputs and of the governing equation,
2. well-posedness of the boundary-value problem (before any solve),
3. the solve itself and its residual,
4. a three-grid convergence study with Richardson extrapolation,
5. comparison with a known exact solution or textbook benchmark,
6. the conservation laws of the relevant physics,
7. time-step (CFL) and symmetry sanity checks.
"""
from __future__ import annotations

import math
import time
import traceback
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import numpy as np

from . import checks as ck
from .checks import Check, Report
from .units import DimensionError, Quantity, convert, dim_str, parse_unit


class ParamError(ValueError):
    """Bad or unknown simulation parameter."""


@dataclass
class Param:
    """One input.  ``unit`` is what the solver works in; users may pass any
    compatible unit as a string (``"2 mm"``, ``"85 degC"``) or a bare number
    already in ``unit``.  ``unit=None`` marks a non-physical option."""

    name: str
    default: Any
    unit: str | None = None
    doc: str = ""
    choices: list | None = None
    lo: float | None = None
    hi: float | None = None
    delta: bool = False      # temperature difference rather than absolute

    @property
    def kind(self) -> str:
        if self.choices:
            return "choice"
        if self.unit is not None:
            return "quantity"
        if isinstance(self.default, bool):
            return "bool"
        if isinstance(self.default, int):
            return "int"
        if isinstance(self.default, float):
            return "number"
        return "json"

    def parse(self, value):
        try:
            if self.kind == "choice":
                if value not in self.choices:
                    raise ParamError(
                        f"{self.name} must be one of {self.choices}, got {value!r}")
                return value
            if self.kind == "quantity":
                if isinstance(value, (list, tuple)):
                    return [convert(v, self.unit, delta=self.delta) for v in value]
                return convert(value, self.unit, delta=self.delta)
            if self.kind == "bool":
                if isinstance(value, str):
                    return value.strip().lower() in ("1", "true", "yes", "on")
                return bool(value)
            if self.kind == "int":
                return int(value)
            if self.kind == "number":
                return float(value)
            return value
        except DimensionError as exc:
            raise DimensionError(f"parameter '{self.name}': {exc}") from exc
        except (TypeError, ValueError) as exc:
            if isinstance(exc, ParamError):
                raise
            raise ParamError(f"parameter '{self.name}': {exc}") from exc

    def schema(self) -> dict:
        d = {"name": self.name, "kind": self.kind, "default": self.default,
             "unit": self.unit, "doc": self.doc}
        if self.choices:
            d["choices"] = self.choices
        if self.lo is not None:
            d["min"] = self.lo
        if self.hi is not None:
            d["max"] = self.hi
        return d


@dataclass
class Field:
    """Something to plot.  1-D: ``values`` is an array or ``{label: array}``
    over ``x``.  2-D: ``values[i, j]`` at ``(x[i], y[j])``."""

    name: str
    x: Any
    values: Any
    y: Any = None
    unit: str = ""
    xlabel: str = "x [m]"
    ylabel: str = "y [m]"

    def to_dict(self, max_pts=160) -> dict:
        def thin(a, axis_len):
            step = max(1, math.ceil(axis_len / max_pts))
            return step

        x = np.asarray(self.x, float)
        sx = thin(x, len(x))
        out = {"name": self.name, "unit": self.unit, "xlabel": self.xlabel,
               "ylabel": self.ylabel, "x": _clean(x[::sx])}
        if self.y is not None:
            y = np.asarray(self.y, float)
            sy = thin(y, len(y))
            v = np.asarray(self.values, float)[::sx, ::sy]
            out.update(kind="image", y=_clean(y[::sy]),
                       values=[_clean(row) for row in v])
        else:
            vals = self.values if isinstance(self.values, dict) else {self.name: self.values}
            out.update(kind="line", series={
                k: _clean(np.asarray(v, float)[::sx]) for k, v in vals.items()})
        return out


def _clean(arr) -> list:
    return [float(v) if math.isfinite(v) else None for v in np.asarray(arr, float).ravel()]


@dataclass
class Solution:
    """What one solve at one resolution returns."""

    qoi: dict                       # name -> (value, unit)
    fields: list = field(default_factory=list)
    residual: float | None = None   # relative solver residual
    residual_note: str = ""
    residual_limit: float = 1e-8
    cfl: tuple | None = None        # (number, limit, description) for transients
    data: dict = field(default_factory=dict)   # raw solver output for the checks


@dataclass
class Result:
    name: str
    title: str
    domain: str
    params: dict
    resolution: int
    qoi: dict
    fields: list
    report: Report
    elapsed: float = 0.0
    notes: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.report.ok

    def value(self, key: str) -> float:
        return self.qoi[key][0]

    def to_dict(self, with_fields=True) -> dict:
        d = {"name": self.name, "title": self.title, "domain": self.domain,
             "params": self.params, "resolution": self.resolution,
             "results": {k: {"value": _num(v[0]), "unit": v[1]}
                         for k, v in self.qoi.items()},
             "verification": self.report.to_dict(), "notes": self.notes,
             "elapsed_s": round(self.elapsed, 3)}
        if with_fields:
            d["fields"] = [f.to_dict() for f in self.fields]
        return d

    def to_text(self) -> str:
        lines = [f"{self.title}  [{self.name}, resolution {self.resolution}]", "",
                 "Results"]
        for k, (v, u) in self.qoi.items():
            lines.append(f"  {k:32} {v:.6g} {u}")
        for n in self.notes:
            lines.append(f"  note: {n}")
        lines += ["", "Self-checks", self.report.to_text()]
        return "\n".join(lines)


def _num(v):
    v = float(v)
    return v if math.isfinite(v) else None


class Simulation:
    """Base class for every simulation template."""

    name = ""
    title = ""
    domain = ""                 # e.g. "electronics", "mechanical", "fluids"
    description = ""
    params: list[Param] = []
    default_resolution = 64
    min_resolution = 4
    primary: tuple = ()         # QoI names used in the grid-convergence study
    convergence_tol = 0.02
    order_hint: float | None = 2
    no_mesh_reason = ""         # why a grid study does not apply (if primary is empty)
    resolution_doc = "grid cells per direction"

    def __init__(self, **kwargs):
        known = {p.name: p for p in self.params}
        unknown = sorted(set(kwargs) - set(known))
        if unknown:
            raise ParamError(
                f"unknown parameter(s) {unknown} for '{self.name}'; "
                f"valid: {sorted(known)}")
        self.p = SimpleNamespace()
        self.given = {}
        for prm in self.params:
            raw = kwargs.get(prm.name, prm.default)
            setattr(self.p, prm.name, prm.parse(raw))
            self.given[prm.name] = (raw if prm.unit is None or isinstance(raw, str)
                                    or isinstance(raw, (list, tuple))
                                    else f"{raw:g} {prm.unit}".strip())
        self.validate()

    # -- hooks for subclasses ---------------------------------------------
    def validate(self):
        """Raise ParamError for inputs that make no physical sense."""

    def solve(self, n: int) -> Solution:
        raise NotImplementedError

    def equations(self) -> dict:
        """``{equation: {term: Quantity}}`` - all terms must share a dimension."""
        return {}

    def dimensionless(self) -> dict:
        """``{group name: Quantity}`` - each must come out a pure number."""
        return {}

    def well_posed(self) -> list:
        """Checks that run *before* solving (complete, consistent BCs...)."""
        return []

    def known_solution(self, sol: Solution) -> list:
        """Compare with an exact solution, or solve a textbook benchmark."""
        return []

    def domain_checks(self, sol: Solution) -> list:
        """Conservation laws and bounds for this physics."""
        return []

    def symmetry_checks(self, sol: Solution) -> list:
        return []

    # -- helpers -----------------------------------------------------------
    def q(self, name: str) -> Quantity:
        """Parameter as a dimension-carrying Quantity."""
        prm = next(p for p in self.params if p.name == name)
        scale, dim, offset = parse_unit(prm.unit or "")
        if prm.delta:
            offset = 0.0
        return Quantity(getattr(self.p, name) * scale + offset, dim)

    @classmethod
    def schema(cls) -> dict:
        return {"name": cls.name, "title": cls.title, "domain": cls.domain,
                "description": " ".join(cls.description.split()),
                "default_resolution": cls.default_resolution,
                "resolution_meaning": cls.resolution_doc,
                "params": [p.schema() for p in cls.params]}

    def _dimension_checks(self) -> list:
        out = []
        try:
            for eq, terms in self.equations().items():
                dims = {t: dim_str(Quantity._coerce(q).dim) for t, q in terms.items()}
                same = len(set(dims.values())) == 1
                listing = "; ".join(f"{t} -> [{d}]" for t, d in dims.items())
                out.append(ck.truth(f"dimensional analysis: {eq}", ck.UNIVERSAL, same,
                                    ("all terms share one dimension: " if same else
                                     "terms have different dimensions: ") + listing))
            for grp, qty in self.dimensionless().items():
                qty = Quantity._coerce(qty)
                out.append(ck.truth(
                    f"dimensional analysis: {grp} is dimensionless", ck.UNIVERSAL,
                    qty.dimensionless,
                    f"{grp} = {float(qty.value):.4g}" if qty.dimensionless
                    else f"{grp} has leftover dimension [{dim_str(qty.dim)}]"))
        except DimensionError as exc:
            out.append(ck.truth("dimensional analysis", ck.UNIVERSAL, False, str(exc)))
        if not out:
            out.append(ck.truth("dimensional analysis", ck.UNIVERSAL, True,
                                "every input was parsed and converted with units"))
        return out

    def _levels(self, n: int) -> tuple:
        return (max(n // 4, 1), max(n // 2, 1), n)

    # -- the protocol ------------------------------------------------------
    def run(self, resolution: int | None = None, verify: bool = True) -> Result:
        n = int(resolution or self.default_resolution)
        t0 = time.perf_counter()
        universal, domain, math_checks, notes = [], [], [], []
        qoi, fields = {}, []

        universal += self._dimension_checks()
        posed = self._guard(self.well_posed, "well-posedness", ck.MATH)
        if not posed:
            posed = [ck.truth("well-posedness", ck.MATH, True,
                              "inputs describe a complete, consistent problem")]
        math_checks += posed
        blocked = any(c.status == ck.FAIL for c in universal + posed)
        if n < 4 * self.min_resolution and self.primary:
            n = 4 * self.min_resolution
            notes.append(f"resolution raised to {n} so a 3-grid study is possible")

        sol = None
        if blocked:
            notes.append("not solved: fix the failed pre-solve checks first")
        else:
            try:
                sol = self.solve(n)
            except Exception as exc:  # a crash is a failed check, not a traceback
                universal.append(ck.truth(
                    "solver ran", ck.UNIVERSAL, False,
                    f"{type(exc).__name__}: {exc}"))
                notes.append(traceback.format_exc(limit=3))

        if sol is not None:
            qoi, fields = sol.qoi, sol.fields
            bad = [k for k, v in qoi.items() if not math.isfinite(float(v[0]))]
            if bad:
                universal.append(ck.truth("finite results", ck.UNIVERSAL, False,
                                          f"non-finite outputs: {bad}"))
            if verify:
                universal += self._mesh_checks(sol, n)
                known = self._guard(lambda: self.known_solution(sol),
                                    "known solution", ck.UNIVERSAL)
                universal += known or [ck.skip(
                    "known solution", ck.UNIVERSAL,
                    "no exact solution or benchmark registered for this setup")]
            if sol.residual is None:
                universal.append(ck.skip("solver residual", ck.UNIVERSAL,
                                         sol.residual_note or "closed-form evaluation"))
            else:
                universal.append(ck.small(
                    "solver residual", ck.UNIVERSAL, sol.residual, sol.residual_limit,
                    sol.residual_note or "relative residual of the discrete equations"))
            if verify:
                domain += self._guard(lambda: self.domain_checks(sol),
                                      "physics checks", ck.UNIVERSAL)
                if sol.cfl is None:
                    math_checks.append(ck.skip("time-step (CFL) condition", ck.MATH,
                                               "steady-state problem, no time stepping"))
                else:
                    num, lim, what = sol.cfl
                    math_checks.append(ck.Check(
                        "time-step (CFL) condition", ck.MATH,
                        ck.PASS if num <= lim * (1 + 1e-9) else ck.FAIL,
                        f"{what}: {num:.4g} (stability limit {lim:.4g})", num, lim))
                sym = self._guard(lambda: self.symmetry_checks(sol), "symmetry", ck.MATH)
                math_checks += sym or [ck.skip(
                    "symmetry", ck.MATH, "the setup has no mirror symmetry to preserve")]

        report = Report()
        report.add(universal, domain, math_checks)
        return Result(self.name, self.title, self.domain, dict(self.given), n,
                      {k: (float(v[0]), v[1]) for k, v in qoi.items()}, fields,
                      report, time.perf_counter() - t0, notes)

    def _guard(self, fn, label, category) -> list:
        try:
            return list(fn() or [])
        except Exception as exc:
            return [ck.truth(label, category, False,
                             f"check crashed: {type(exc).__name__}: {exc}")]

    def _mesh_checks(self, sol: Solution, n: int) -> list:
        if not self.primary:
            return [ck.skip("mesh convergence", ck.UNIVERSAL,
                            self.no_mesh_reason or "no spatial or temporal grid")]
        levels = self._levels(n)
        try:
            coarse = [self.solve(m) for m in levels[:-1]]
        except Exception as exc:
            return [ck.truth("mesh convergence", ck.UNIVERSAL, False,
                             f"coarse-grid solve failed: {type(exc).__name__}: {exc}")]
        out = []
        labels = tuple(f"n={m}" for m in levels)
        for key in self.primary:
            vals = [s.qoi[key][0] for s in coarse] + [sol.qoi[key][0]]
            out.append(ck.convergence(f"mesh convergence: {key}", vals,
                                      self.convergence_tol, self.order_hint, labels))
        return out


# -- registry ----------------------------------------------------------------
REGISTRY: dict[str, type] = {}


def register(cls):
    if not cls.name:
        raise ValueError("simulation needs a name")
    REGISTRY[cls.name] = cls
    return cls


def get(name: str) -> type:
    if name not in REGISTRY:
        raise ParamError(f"unknown simulation '{name}'. Available: {sorted(REGISTRY)}")
    return REGISTRY[name]


def catalog() -> list:
    return [REGISTRY[k].schema() for k in sorted(
        REGISTRY, key=lambda k: (REGISTRY[k].domain, k))]


def run(name: str, params: dict | None = None, resolution: int | None = None,
        verify: bool = True) -> Result:
    """Run a registered simulation.  Bad units come back as a failed check."""
    cls = get(name)
    try:
        sim = cls(**(params or {}))
    except (DimensionError, ParamError) as exc:
        kind = ("dimensional analysis" if isinstance(exc, DimensionError)
                else "well-posedness")
        cat = ck.UNIVERSAL if isinstance(exc, DimensionError) else ck.MATH
        report = Report([Check(kind, cat, ck.FAIL, str(exc))])
        return Result(cls.name, cls.title, cls.domain, dict(params or {}), 0, {}, [],
                      report, 0.0, ["not solved: the inputs were rejected"])
    return sim.run(resolution, verify)
