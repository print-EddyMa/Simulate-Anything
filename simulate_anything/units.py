"""Dimensional analysis: quantities that carry SI dimensions.

Every physical input is parsed into a :class:`Quantity`.  Adding metres to
seconds raises :class:`DimensionError`, and every simulation declares the terms
of its governing equation so the checker can confirm they share one dimension.

    >>> k = Q("205 W/(m*K)")
    >>> (k * Q("10 K") / Q("2 mm")).to("W/m^2")
    1025000.0
"""
from __future__ import annotations

import ast
import math
import re
from fractions import Fraction

import numpy as np

BASE = ("m", "kg", "s", "A", "K", "mol", "cd")
ZERO = tuple(Fraction(0) for _ in BASE)


class DimensionError(ValueError):
    """Raised when units do not work out."""


def _dim(**kw) -> tuple:
    return tuple(Fraction(kw.get(b, 0)) for b in BASE)


def dim_str(dim) -> str:
    """Human-readable dimension, e.g. ``kg·m^2/s^3``."""
    num, den = [], []
    for b, e in zip(BASE, dim):
        if e == 0:
            continue
        mag = abs(e)
        txt = b if mag == 1 else f"{b}^{mag}"
        (num if e > 0 else den).append(txt)
    if not num and not den:
        return "1"
    s = "·".join(num) if num else "1"
    if den:
        s += "/" + ("·".join(den) if len(den) == 1 else "(" + "·".join(den) + ")")
    return s


class Quantity:
    """A value (float or array) in SI base units plus its dimension vector."""

    __slots__ = ("value", "dim")
    __array_priority__ = 1000

    def __init__(self, value, dim=ZERO):
        self.value = value
        self.dim = tuple(Fraction(d) for d in dim)

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _coerce(other) -> "Quantity":
        return other if isinstance(other, Quantity) else Quantity(other)

    @property
    def dimensionless(self) -> bool:
        return all(d == 0 for d in self.dim)

    def same_dim(self, other) -> bool:
        return self.dim == self._coerce(other).dim

    def to(self, unit: str):
        """Numeric value expressed in ``unit`` (raises if dimensions differ)."""
        scale, dim, offset = parse_unit(unit)
        if dim != self.dim:
            raise DimensionError(
                f"cannot express a quantity of dimension [{dim_str(self.dim)}] in "
                f"'{unit}' [{dim_str(dim)}]")
        return (self.value - offset) / scale

    # -- arithmetic --------------------------------------------------------
    def _add(self, other, sign):
        other = self._coerce(other)
        if other.dim != self.dim:
            raise DimensionError(
                f"cannot add [{dim_str(self.dim)}] and [{dim_str(other.dim)}]")
        return Quantity(self.value + sign * other.value, self.dim)

    def __add__(self, other):
        return self._add(other, 1)

    __radd__ = __add__

    def __sub__(self, other):
        return self._add(other, -1)

    def __rsub__(self, other):
        return self._coerce(other)._add(self, -1)

    def __mul__(self, other):
        other = self._coerce(other)
        return Quantity(self.value * other.value,
                        tuple(a + b for a, b in zip(self.dim, other.dim)))

    __rmul__ = __mul__

    def __truediv__(self, other):
        other = self._coerce(other)
        return Quantity(self.value / other.value,
                        tuple(a - b for a, b in zip(self.dim, other.dim)))

    def __rtruediv__(self, other):
        return self._coerce(other) / self

    def __pow__(self, p):
        p = Fraction(p).limit_denominator(12)
        return Quantity(self.value ** float(p), tuple(a * p for a in self.dim))

    def __neg__(self):
        return Quantity(-self.value, self.dim)

    def __abs__(self):
        return Quantity(abs(self.value), self.dim)

    def sqrt(self):
        return self ** Fraction(1, 2)

    def __float__(self):
        if not self.dimensionless:
            raise DimensionError(
                f"quantity with dimension [{dim_str(self.dim)}] is not a pure number")
        return float(self.value)

    def __repr__(self):
        return f"Quantity({self.value!r} [{dim_str(self.dim)}])"


# -- unit table -------------------------------------------------------------
# name -> (scale to SI, dimension, offset)
UNITS: dict[str, tuple] = {}
_PREFIXABLE: set[str] = set()
_PREFIXES = {"da": 1e1, "h": 1e2, "k": 1e3, "M": 1e6, "G": 1e9, "T": 1e12,
             "d": 1e-1, "c": 1e-2, "m": 1e-3, "u": 1e-6, "n": 1e-9, "p": 1e-12,
             "f": 1e-15}


def _add(name, scale, dim, offset=0.0, prefix=False):
    UNITS[name] = (float(scale), tuple(Fraction(d) for d in dim), float(offset))
    if prefix:
        _PREFIXABLE.add(name)


_add("m", 1, _dim(m=1), prefix=True)
_add("g", 1e-3, _dim(kg=1), prefix=True)
_add("kg", 1, _dim(kg=1))
_add("s", 1, _dim(s=1), prefix=True)
_add("A", 1, _dim(A=1), prefix=True)
_add("K", 1, _dim(K=1), prefix=True)
_add("mol", 1, _dim(mol=1), prefix=True)
_add("cd", 1, _dim(cd=1))
_add("rad", 1, ZERO)
_add("deg", math.pi / 180, ZERO)
_add("percent", 0.01, ZERO)
_add("one", 1, ZERO)
_add("Hz", 1, _dim(s=-1), prefix=True)
_add("rpm", 2 * math.pi / 60, _dim(s=-1))
_add("N", 1, _dim(kg=1, m=1, s=-2), prefix=True)
_add("Pa", 1, _dim(kg=1, m=-1, s=-2), prefix=True)
_add("J", 1, _dim(kg=1, m=2, s=-2), prefix=True)
_add("W", 1, _dim(kg=1, m=2, s=-3), prefix=True)
_add("C", 1, _dim(A=1, s=1), prefix=True)          # coulomb (use degC for Celsius)
_add("V", 1, _dim(kg=1, m=2, s=-3, A=-1), prefix=True)
_add("ohm", 1, _dim(kg=1, m=2, s=-3, A=-2), prefix=True)
_add("S", 1, _dim(kg=-1, m=-2, s=3, A=2), prefix=True)
_add("F", 1, _dim(kg=-1, m=-2, s=4, A=2), prefix=True)
_add("H", 1, _dim(kg=1, m=2, s=-2, A=-2), prefix=True)
_add("T", 1, _dim(kg=1, s=-2, A=-1), prefix=True)
_add("Wb", 1, _dim(kg=1, m=2, s=-2, A=-1), prefix=True)
_add("L", 1e-3, _dim(m=3), prefix=True)
_add("min", 60, _dim(s=1))
_add("h", 3600, _dim(s=1))
_add("hr", 3600, _dim(s=1))
_add("day", 86400, _dim(s=1))
_add("year", 365.25 * 86400, _dim(s=1))
_add("bar", 1e5, _dim(kg=1, m=-1, s=-2), prefix=True)
_add("atm", 101325, _dim(kg=1, m=-1, s=-2))
_add("psi", 6894.757293168, _dim(kg=1, m=-1, s=-2))
_add("mmHg", 133.322387415, _dim(kg=1, m=-1, s=-2))
_add("inch", 0.0254, _dim(m=1))
_add("ft", 0.3048, _dim(m=1))
_add("mil", 25.4e-6, _dim(m=1))
_add("lb", 0.45359237, _dim(kg=1))
_add("lbf", 4.4482216152605, _dim(kg=1, m=1, s=-2))
_add("Wh", 3600, _dim(kg=1, m=2, s=-2), prefix=True)
_add("Ah", 3600, _dim(A=1, s=1), prefix=True)
_add("eV", 1.602176634e-19, _dim(kg=1, m=2, s=-2), prefix=True)
_add("P", 0.1, _dim(kg=1, m=-1, s=-1), prefix=True)   # poise (cP = mPa*s)
_add("degC", 1, _dim(K=1), offset=273.15)
_add("degF", 5 / 9, _dim(K=1), offset=273.15 - 32 * 5 / 9)

_OFFSET_UNITS = {"degC", "degF"}
_NORMALISE = [("·", "*"), ("⋅", "*"), ("×", "*"), ("²", "^2"), ("³", "^3"),
              ("°C", "degC"), ("°F", "degF"), ("℃", "degC"), ("µ", "u"),
              ("μ", "u"), ("Ω", "ohm"), ("%", "percent")]


def _lookup(name: str):
    if name in UNITS:
        return UNITS[name]
    for pre in sorted(_PREFIXES, key=len, reverse=True):
        rest = name[len(pre):]
        if name.startswith(pre) and rest in _PREFIXABLE:
            scale, dim, off = UNITS[rest]
            return (scale * _PREFIXES[pre], dim, off)
    raise DimensionError(f"unknown unit '{name}'")


def _eval(node):
    """Evaluate a parsed unit expression to (scale, dim)."""
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Name):
        scale, dim, _ = _lookup(node.id)
        return scale, dim
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value), ZERO
    if isinstance(node, ast.BinOp):
        if isinstance(node.op, ast.Pow):
            s, d = _eval(node.left)
            p = _number(node.right)
            return s ** float(p), tuple(x * p for x in d)
        s1, d1 = _eval(node.left)
        s2, d2 = _eval(node.right)
        if isinstance(node.op, ast.Mult):
            return s1 * s2, tuple(a + b for a, b in zip(d1, d2))
        if isinstance(node.op, ast.Div):
            return s1 / s2, tuple(a - b for a, b in zip(d1, d2))
    raise DimensionError("unsupported unit expression")


def _number(node) -> Fraction:
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_number(node.operand)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return Fraction(node.value).limit_denominator(12)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return _number(node.left) / _number(node.right)
    raise DimensionError("unit exponents must be plain numbers")


def parse_unit(unit: str):
    """Return ``(scale, dimension, offset)`` for a unit string like ``W/(m*K)``."""
    text = (unit or "").strip()
    for a, b in _NORMALISE:
        text = text.replace(a, b)
    if text in ("", "1", "-"):
        return 1.0, ZERO, 0.0
    if text in _OFFSET_UNITS:
        return UNITS[text]
    text = re.sub(r"\bin\b", "inch", text).replace("^", "**")
    text = re.sub(r"(?<=[\w)])\s+(?=[\w(])", "*", text)
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise DimensionError(f"cannot parse unit '{unit}'") from exc
    scale, dim = _eval(tree)
    return scale, dim, 0.0


_NUM = re.compile(r"^\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*(.*)$")


def split_quantity(text: str):
    """``"30 mm"`` -> ``(30.0, "mm")``."""
    m = _NUM.match(text)
    if not m:
        raise DimensionError(f"cannot parse quantity '{text}'")
    return float(m.group(1)), m.group(2).strip()


def Q(value, unit: str | None = None) -> Quantity:
    """Build a quantity: ``Q(5, "mm")``, ``Q("5 mm")`` or ``Q("25 degC")``."""
    if isinstance(value, Quantity):
        return value
    if isinstance(value, str):
        m = _NUM.match(value)
        if not m:
            raise DimensionError(f"cannot parse quantity '{value}'")
        value, unit_in = float(m.group(1)), m.group(2).strip()
        unit = unit_in or unit
    scale, dim, offset = parse_unit(unit or "")
    if isinstance(value, (list, tuple)):
        value = np.asarray(value, dtype=float)
    return Quantity(value * scale + offset, dim)


def convert(value, to_unit: str, default_unit: str | None = None, delta=False) -> float:
    """Convert a number / "value unit" string into ``to_unit``, checking dimensions.

    Bare numbers are assumed to already be in ``default_unit`` (or ``to_unit``).
    With ``delta=True`` a temperature such as ``"10 degC"`` is read as a
    difference rather than an absolute temperature.
    """
    if isinstance(value, bool):
        raise DimensionError("expected a physical quantity, got a boolean")
    if isinstance(value, str):
        m = _NUM.match(value)
        if not m:
            raise DimensionError(f"cannot parse quantity '{value}'")
        num, unit_in = float(m.group(1)), m.group(2).strip() or (default_unit or to_unit)
    else:
        num, unit_in = float(value), (default_unit or to_unit)
    s_in, d_in, o_in = parse_unit(unit_in)
    s_out, d_out, o_out = parse_unit(to_unit)
    if d_in != d_out:
        raise DimensionError(
            f"'{value}' has dimension [{dim_str(d_in)}] but [{dim_str(d_out)}] "
            f"({to_unit}) is required")
    if delta:
        o_in = o_out = 0.0
    return (num * s_in + o_in - o_out) / s_out


def same_dimension(quantities) -> bool:
    qs = [Quantity._coerce(q) for q in quantities]
    return all(q.dim == qs[0].dim for q in qs)
