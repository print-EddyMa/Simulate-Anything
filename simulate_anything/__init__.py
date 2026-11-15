"""Simulate Anything: turn a physics problem into a simulation that checks itself.

    >>> import simulate_anything as sa
    >>> result = sa.run("heat_sink_fin", {"fin_length": "40 mm", "h": "30 W/(m^2*K)"})
    >>> print(result.to_text())
"""
from __future__ import annotations

import importlib

from . import core
from .checks import Check, Report
from .core import (Field, Param, ParamError, Result, Simulation, Solution,
                   register)
from .units import DimensionError, Q, Quantity

__version__ = "0.1.0"

# Every module of simulation templates.  Importing one registers its classes.
MODULES = ("electronics", "thermal", "mechanical", "fluids", "electromagnetics",
           "aerospace", "energy", "bio", "chemistry", "earth", "realworld")

_loaded = False


def load_all() -> None:
    """Import every template module (idempotent)."""
    global _loaded
    if not _loaded:
        for mod in MODULES:
            importlib.import_module(f"{__name__}.sims.{mod}")
        _loaded = True


def catalog() -> list:
    """Schemas of every available simulation, grouped by domain."""
    load_all()
    return core.catalog()


def get(name: str) -> type:
    load_all()
    return core.get(name)


def run(name: str, params: dict | None = None, resolution: int | None = None,
        verify: bool = True) -> Result:
    """Run a simulation and its full self-check protocol."""
    load_all()
    return core.run(name, params, resolution, verify)


__all__ = ["run", "get", "catalog", "load_all", "Simulation", "Param", "Field",
           "Solution", "Result", "register", "Check", "Report", "Q", "Quantity",
           "DimensionError", "ParamError"]
