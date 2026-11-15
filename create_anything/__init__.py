"""Create Anything: describe a thing, get a design that has been tested.

A design is a JSON document of parts, materials and assembly steps, plus the
real-world tests it has to survive (a load, a drop, a flame, a current...).
Each test is run as a verified Simulate Anything simulation on the part's real
dimensions and material, and the optimiser changes sizes and materials until
every test passes on verified simulations.

    >>> import create_anything as ca
    >>> design = ca.load(ca.example_path("shelf_bracket"))
    >>> report = ca.check(design)
    >>> print(report["tests"]["counts"])
"""
from __future__ import annotations

import json
from pathlib import Path

from . import materials
from .design import DesignError, geometry_issues, normalise, summary
from .optimize import optimise
from .scenarios import DESCRIPTIONS, ScenarioError, evaluate

__version__ = "0.1.0"


def load(path) -> dict:
    """Read and normalise a design JSON file."""
    with open(Path(path), encoding="utf-8") as fh:
        return normalise(json.load(fh))


EXAMPLES = Path(__file__).parent / "examples"


def examples() -> list:
    """Names of the bundled example designs."""
    return sorted(p.stem for p in EXAMPLES.glob("*.json"))


def example_path(name: str) -> Path:
    path = EXAMPLES / f"{name}.json"
    if name not in examples():
        raise KeyError(f"unknown example '{name}'; available: {examples()}")
    return path


def check(design: dict, verify: bool = True, detail: bool = False) -> dict:
    """Geometry, bill of materials and every real-world test of a design."""
    d = normalise(design)
    return {"design": d, "summary": summary(d), "issues": geometry_issues(d),
            "tests": evaluate(d, verify=verify, detail=detail)}


__all__ = ["load", "check", "examples", "example_path", "normalise", "evaluate", "optimise", "summary",
           "geometry_issues", "materials", "DesignError", "ScenarioError", "DESCRIPTIONS"]
