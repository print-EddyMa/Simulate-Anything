"""The design agent: "a bracket that holds 4 kg of books" in, a tested design out.

The language model writes the design document (parts, materials, assembly and
the real-world tests it has to survive) and nothing else.  Every number about
how the design behaves comes from the tools: ``check_design`` runs each test as
a verified simulation, ``optimise_design`` resizes and re-tests.  The model may
not claim a design works unless the last check says every test passed.

Requires ``pip install anthropic`` and credentials (``ANTHROPIC_API_KEY`` or an
``ant auth login`` profile).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import simulate_anything as sa
from simulate_anything.agent import TOOLS as SIM_TOOLS

from . import materials
from .design import geometry_issues, normalise, summary
from .optimize import optimise
from .scenarios import DESCRIPTIONS, evaluate

DEFAULT_MODEL = "claude-opus-5-5"

SYSTEM = """You are Plimsoll, a design engineer. People describe a physical thing \
they want made - anything: a shelf bracket, a lamp, a drone arm, an enclosure, a bus bar, a \
heat spreader, a pipe run, a CubeSat panel - and you turn it into a design document and \
prove, with verified simulations, that it survives the real world it will live in.

How you work
1. Work out what the thing must do and what will happen to it: loads, drops, vibration, \
heat, fire, current, flow, or any other physics in the simulation catalogue. Use the \
person's numbers; where they gave none, pick sensible engineering values and remember \
them as assumptions.
2. Call list_materials and list_test_types. Write the design as JSON: parts (shape, \
dimensions in mm, position of the bounding-box centre with z up, rotation in degrees, \
material, process, purpose), assembly steps, and one scenario per real-world test. Use \
the shape that matches the real part: an L-bracket, T-beam, channel or any other outline \
is an "extrusion" with its true profile, not a box. Parts must touch where they join. \
Mark bought-in parts (screws, boards, LEDs, motors) with "locked": true so they are \
never resized.
3. Pick tests. Use a built-in test type when one fits. When none does, use type \
"simulation": call list_simulations and describe_simulation, choose the template whose \
physics matches, and bind its inputs to the part ("bind": {"thickness": "size.2", \
"conductivity": "material.conductivity"}) so the result depends on the design and the \
optimiser can change it. Give every other input in "params" with units, and state the \
requirement in "require" with units.
4. Call check_design. Each test reports a load: the share of its allowance it uses, \
where 100 % is exactly at the limit. Read every test and geometry issue. "error" means the scenario \
does not fit the part or the simulation rejected the setup - read the message and fix \
it. "unverified" means the simulation failed its own checks; do not trust that number.
5. If tests fail, either change the design yourself (a better idea beats a thicker part) \
or call optimise_design to resize and swap materials, then check again. Move parts the \
optimiser made overlap or float.
6. Finish by calling submit_design with the one design you recommend - it is checked \
again and is what the person sees. You may check several candidates along the way; \
submit the one your answer describes. Never say a design passes unless the \
submit_design result says so. If no design passes, submit the closest one and say what \
fails.

If you are given a current design, change it as asked rather than starting over.

Your final answer is read by someone who will build the thing. Lead with whether it \
passed. Then the parts with material, size and process, the assembly steps, the tests \
with their results and margins, the assumptions you made and the limits of the models \
(idealised sections, no fatigue, no joints). Keep it compact."""

_DESIGN = {"type": "object", "description": (
    "the design document: {name, description, parts: [...], assembly: [...], "
    "scenarios: [...]}; dimensions in mm, z up, position is the bounding-box centre")}

TOOLS = [t for t in SIM_TOOLS if t["name"] in ("list_simulations", "describe_simulation")] + [
    {"name": "list_materials",
     "description": "Every material with density, stiffness, strength, thermal and "
                    "electrical properties, safe temperatures, cost and the processes "
                    "that make it.",
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "list_test_types",
     "description": "The real-world tests a scenario can ask for, the part shapes each "
                    "applies to and their parameters (with units) and defaults. Also "
                    "lists the part shapes and their dimension fields.",
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "check_design",
     "description": "Validate a design, compute its mass and cost, find geometry "
                    "problems and run every scenario as a verified simulation. Returns "
                    "each test's status (pass | fail | unverified | error), headline, "
                    "metrics against their limits and the simulation's check summary.",
     "input_schema": {"type": "object", "properties": {"design": _DESIGN},
                      "required": ["design"], "additionalProperties": False}},
    {"name": "submit_design",
     "description": "Submit the final design you recommend. It is checked again with full "
                    "verification and becomes the design the person sees; your answer must "
                    "match this result. Call it once, at the end.",
     "input_schema": {"type": "object", "properties": {"design": _DESIGN},
                      "required": ["design"], "additionalProperties": False}},
    {"name": "optimise_design",
     "description": "Resize the tested parts and try other materials until every test "
                    "passes, then trim excess material. Only measured results count. "
                    "Returns the new design, every change made and the final verified "
                    "results. Locked parts are never changed.",
     "input_schema": {"type": "object", "properties": {"design": _DESIGN},
                      "required": ["design"], "additionalProperties": False}},
]

SHAPES_HELP = {
    "box": "size [x, y, z]; optional wall (hollow shell)",
    "cylinder": "radius, length, axis (x | y | z)",
    "tube": "radius, inner_radius, length, axis",
    "sphere": "radius",
    "heatsink": "base [width, depth, thickness], fin_height, fin_thickness, n_fins",
    "extrusion": "profile [[u, v], ...] (any outline, mm), length, axis, optional wall "
                 "(thinnest wall, needed for fire tests); for axis z u=x v=y, axis x u=y "
                 "v=z, axis y u=z v=x",
    "mesh": "mesh {size [x, y, z], volume} (an imported shape)",
}


def _brief_tests(ev: dict) -> list:
    return [{k: t.get(k) for k in ("id", "type", "part", "status", "load", "headline",
                                   "metrics", "notes", "checks")} for t in ev["tests"]]


@dataclass
class Result:
    text: str
    design: dict | None = None       # the submitted design (else the last one checked)
    report: dict | None = None       # its check report
    stop_reason: str = "end_turn"
    calls: list = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    submitted: bool = False          # the model named its final design

    @property
    def all_pass(self) -> bool:
        return bool(self.report and self.report["tests"]["all_pass"])


class Designer:
    def __init__(self, model: str = DEFAULT_MODEL, client=None, max_turns: int = 24,
                 on_event=None):
        if client is None:
            try:
                import anthropic
            except ImportError as exc:
                raise RuntimeError(
                    "the agent needs the Anthropic SDK: pip install anthropic") from exc
            client = anthropic.Anthropic()
        self.client, self.model, self.max_turns = client, model, max_turns
        self.on_event = on_event or (lambda kind, payload: None)
        self.design = self.report = None
        self.submitted = False

    # -- tools -----------------------------------------------------------------
    def tool(self, name: str, args: dict) -> str:
        if name == "list_simulations":
            return json.dumps([{k: c[k] for k in ("name", "title", "domain", "description")}
                               for c in sa.catalog()])
        if name == "describe_simulation":
            return json.dumps(sa.get(args["name"]).schema())
        if name == "list_materials":
            return json.dumps(materials.catalog())
        if name == "list_test_types":
            return json.dumps({"tests": DESCRIPTIONS, "shapes": SHAPES_HELP})
        if name in ("check_design", "submit_design"):
            d = normalise(args["design"])
            ev = evaluate(d, verify=True, detail=False)
            rep = {"design": d, "summary": summary(d), "issues": geometry_issues(d),
                   "tests": ev}
            if name == "submit_design" or not self.submitted:
                self.design, self.report = d, rep
            if name == "submit_design":
                self.submitted = True
            self.on_event("submit" if name == "submit_design" else "check", rep)
            return json.dumps({"summary": rep["summary"], "issues": rep["issues"],
                               "counts": ev["counts"], "all_pass": ev["all_pass"],
                               "tests": _brief_tests(ev)})
        if name == "optimise_design":
            out = optimise(normalise(args["design"]))
            self.on_event("optimise", out)
            return json.dumps({"design": out["design"], "steps": out["steps"],
                               "all_pass": out["all_pass"], "mass_g": out["mass_g"],
                               "note": out["note"], "issues": out["issues"],
                               "tests": _brief_tests(out["after"])})
        raise ValueError(f"unknown tool '{name}'")

    def _call_tool(self, block) -> dict:
        args = block.input if isinstance(block.input, dict) else {}
        self.on_event("tool", {"name": block.name, "input": args})
        try:
            return {"type": "tool_result", "tool_use_id": block.id,
                    "content": self.tool(block.name, args)}
        except Exception as exc:
            # the model can read the error and correct the design
            msg = f"{type(exc).__name__}: {exc}"
            self.on_event("tool_error", {"name": block.name, "error": msg})
            return {"type": "tool_result", "tool_use_id": block.id, "is_error": True,
                    "content": msg}

    # -- loop ------------------------------------------------------------------
    def create(self, request: str, design: dict | None = None) -> Result:
        """Design, test and refine one thing described in plain language.  With
        ``design``, change that design as the request asks instead."""
        self.design = self.report = None
        self.submitted = False
        content = request
        if design is not None:
            content = (f"Current design:\n{json.dumps(design)}\n\nChange it as follows: "
                       f"{request}")
        messages = [{"role": "user", "content": content}]
        calls, response, usage = [], None, {"input_tokens": 0, "output_tokens": 0,
                                    "cache_read_input_tokens": 0,
                                    "cache_creation_input_tokens": 0}
        for _ in range(self.max_turns):
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=SYSTEM,
                tools=TOOLS,
                messages=messages,
                thinking={"type": "adaptive"},
                output_config={"effort": "high"},
                cache_control={"type": "ephemeral"},
                # if a safety classifier declines, retry on the fallback model
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            u = getattr(response, "usage", None)
            for k in usage:
                usage[k] += int(getattr(u, k, 0) or 0)
            for block in response.content:
                if block.type == "text" and block.text.strip():
                    self.on_event("text", block.text)
            if response.stop_reason != "tool_use":
                break
            messages.append({"role": "assistant", "content": response.content})
            results = [self._call_tool(b) for b in response.content if b.type == "tool_use"]
            calls += [b.name for b in response.content if b.type == "tool_use"]
            messages.append({"role": "user", "content": results})
        text = "\n".join(b.text for b in response.content if b.type == "text").strip()
        reason = response.stop_reason
        if reason == "refusal":
            text = text or "The model declined this request."
        elif reason == "max_tokens":
            text += "\n\n[answer cut off: output limit reached]"
        elif reason == "tool_use":
            text += "\n\n[stopped: reached the limit of agent steps before finishing]"
        return Result(text, self.design, self.report, reason, calls, usage, self.submitted)
