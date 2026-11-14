"""The verification vocabulary: a Check is one question the agent asks itself.

A check is never silently dropped.  If it does not apply it is recorded as
``skip`` together with the reason, so a report always shows what was and was
not verified.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

import numpy as np

PASS, WARN, FAIL, SKIP = "pass", "warn", "fail", "skip"
ICON = {PASS: "PASS", WARN: "WARN", FAIL: "FAIL", SKIP: "skip"}

# Categories mirror the checklist in the README.
UNIVERSAL, MATH = "every simulation", "math sanity"
HEAT, STRUCT, FLUID, EM = ("heat & thermodynamics", "structures", "fluids",
                           "electromagnetism")


@dataclass
class Check:
    name: str
    category: str
    status: str
    detail: str = ""
    value: float | None = None
    expected: float | None = None
    tolerance: float | None = None

    def to_dict(self):
        d = asdict(self)
        for k in ("value", "expected", "tolerance"):
            v = d[k]
            if v is not None:
                v = float(v)
                d[k] = v if math.isfinite(v) else None
        return d


def _fmt(x) -> str:
    return f"{x:.6g}"


def close(name, category, value, expected, rtol=1e-2, atol=0.0, detail="",
          unit="", warn_factor=None) -> Check:
    """Pass if ``value`` matches ``expected`` within rtol/atol.

    ``warn_factor`` widens the band in which a miss is a warning, not a failure.
    """
    value, expected = float(value), float(expected)
    err = abs(value - expected)
    tol = atol + rtol * abs(expected)
    if not math.isfinite(value):
        status = FAIL
    elif err <= tol:
        status = PASS
    elif warn_factor and err <= warn_factor * tol:
        status = WARN
    else:
        status = FAIL
    rel = err / abs(expected) if expected else float("nan")
    u = f" {unit}" if unit else ""
    msg = f"got {_fmt(value)}{u}, expected {_fmt(expected)}{u}"
    if expected and rtol:
        msg += f" (off by {100 * rel:.3g}%, allowed {100 * tol / abs(expected):.3g}%)"
    else:
        msg += f" (off by {_fmt(err)}{u}, allowed {_fmt(tol)}{u})"
    if detail:
        msg = f"{detail}: {msg}"
    return Check(name, category, status, msg, value, expected, rtol)


def small(name, category, value, limit, detail="", warn_factor=None) -> Check:
    """Pass if ``|value| <= limit`` (for residuals, imbalances, divergences)."""
    value = abs(float(value))
    if not math.isfinite(value):
        status = FAIL
    elif value <= limit:
        status = PASS
    elif warn_factor and value <= warn_factor * limit:
        status = WARN
    else:
        status = FAIL
    msg = f"{_fmt(value)} (limit {_fmt(limit)})"
    if detail:
        msg = f"{detail}: {msg}"
    return Check(name, category, status, msg, value, 0.0, limit)


def truth(name, category, ok, detail="", warn=False) -> Check:
    status = PASS if ok else (WARN if warn else FAIL)
    return Check(name, category, status, detail)


def skip(name, category, why) -> Check:
    return Check(name, category, SKIP, f"not applicable: {why}")


def bounds(name, category, lo, hi, vmin, vmax, slack=1e-6, detail="",
           unit="") -> Check:
    """Maximum-principle style check: the field must stay within [lo, hi]."""
    finite = [abs(v) for v in (lo, hi) if math.isfinite(v)]   # one-sided bounds allowed
    if len(finite) == 2:
        finite.append(abs(hi - lo))
    span = max(finite + [1e-300])
    tol = slack * span
    ok = (vmin >= lo - tol) and (vmax <= hi + tol)
    u = f" {unit}" if unit else ""
    msg = (f"field range [{_fmt(vmin)}, {_fmt(vmax)}]{u} vs allowed "
           f"[{_fmt(lo)}, {_fmt(hi)}]{u}")
    if detail:
        msg = f"{detail}: {msg}"
    return Check(name, category, PASS if ok else FAIL, msg)


def symmetry(name, field, mirrored, tol=1e-6, detail="") -> Check:
    """The solution must keep a symmetry the setup has."""
    field, mirrored = np.asarray(field, float), np.asarray(mirrored, float)
    scale = max(float(np.max(np.abs(field))), 1e-300)
    err = float(np.max(np.abs(field - mirrored))) / scale
    msg = f"max asymmetry {_fmt(err)} of field scale (limit {_fmt(tol)})"
    if detail:
        msg = f"{detail}: {msg}"
    return Check(name, MATH, PASS if err <= tol else FAIL, msg, err, 0.0, tol)


def convergence(name, values, tol=0.02, order_hint=None, labels=None) -> Check:
    """Grid-convergence study from three systematically refined solutions.

    ``values`` are a quantity of interest on coarse, medium and fine grids
    (refinement ratio 2).  Uses Richardson extrapolation to estimate the
    discretisation error left in the fine-grid answer.
    """
    f1, f2, f3 = (float(v) for v in values)
    e21, e32 = f2 - f1, f3 - f2
    scale = max(abs(f3), 1e-300)
    rel = abs(e32) / scale
    lab = labels or ("coarse", "medium", "fine")
    hist = ", ".join(f"{l}={_fmt(v)}" for l, v in zip(lab, (f1, f2, f3)))
    if not all(math.isfinite(v) for v in (f1, f2, f3)):
        return Check(name, UNIVERSAL, FAIL, f"non-finite values: {hist}")
    if rel < 1e-9:
        return Check(name, UNIVERSAL, PASS,
                     f"answer is grid-independent to solver precision ({hist})",
                     rel, 0.0, tol)
    if e21 * e32 > 0 and abs(e32) < abs(e21):
        p = math.log(abs(e21) / abs(e32)) / math.log(2)
        p_used = min(p, order_hint + 1) if order_hint else p
        extrap = f3 + e32 / (2 ** p_used - 1)
        est = abs(f3 - extrap) / max(abs(extrap), 1e-300)
        msg = (f"{hist}; observed order {p:.2f}, Richardson estimate {_fmt(extrap)}, "
               f"est. discretisation error {100 * est:.3g}% (limit {100 * tol:.3g}%)")
    else:
        est = rel
        kind = "oscillatory" if e21 * e32 <= 0 else "not yet asymptotic"
        msg = (f"{hist}; convergence {kind}, change on last refinement "
               f"{100 * rel:.3g}% (limit {100 * tol:.3g}%)")
    status = PASS if est <= tol else (WARN if est <= 3 * tol else FAIL)
    return Check(name, UNIVERSAL, status, msg, est, 0.0, tol)


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(self, *checks):
        for c in checks:
            if c is None:
                continue
            if isinstance(c, (list, tuple)):
                self.add(*c)
            else:
                self.checks.append(c)

    def count(self, status) -> int:
        return sum(c.status == status for c in self.checks)

    @property
    def ok(self) -> bool:
        return self.count(FAIL) == 0

    @property
    def verdict(self) -> str:
        if self.count(FAIL):
            return "FAILED"
        if self.count(WARN):
            return "PASSED WITH WARNINGS"
        return "VERIFIED"

    def failed(self):
        return [c for c in self.checks if c.status == FAIL]

    def summary(self) -> str:
        return (f"{self.verdict}: {self.count(PASS)} passed, {self.count(WARN)} "
                f"warnings, {self.count(FAIL)} failed, {self.count(SKIP)} not applicable")

    def to_dict(self):
        return {"verdict": self.verdict, "ok": self.ok, "summary": self.summary(),
                "checks": [c.to_dict() for c in self.checks]}

    def to_text(self) -> str:
        lines, cats = [], []
        for c in self.checks:
            if c.category not in cats:
                cats.append(c.category)
        for cat in cats:
            lines.append(f"  [{cat}]")
            for c in self.checks:
                if c.category == cat:
                    lines.append(f"    {ICON[c.status]:4}  {c.name}: {c.detail}")
        lines.append("  " + self.summary())
        return "\n".join(lines)
