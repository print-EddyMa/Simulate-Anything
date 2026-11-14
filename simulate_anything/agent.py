"""The agent: plain-language problem in, verified simulation out.

The language model never does the physics itself.  It chooses a simulation
template, fills in parameters with units, runs it, and then has to read the
self-check report.  A failed check sends it back to fix the setup; it may not
present an unverified number as an answer.

Requires ``pip install anthropic`` and credentials (``ANTHROPIC_API_KEY`` or
an ``ant auth login`` profile).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import simulate_anything as sa

DEFAULT_MODEL = "claude-opus-5-5"

SYSTEM = """You are Simulate Anything, an engineering simulation agent. People describe a \
physical problem in plain language - a hot chip, a bending bracket, flow in a pipe, a \
capacitor, a CubeSat, drug diffusion, a textbook exercise - and you answer it by running \
a real numerical simulation and checking the result, not by estimating in your head.

How you work
1. Read the problem. Identify the physics, the quantity the person actually wants, and \
the numbers they gave. Call list_simulations, then describe_simulation for the template \
that fits. If several could fit, prefer the one whose governing equation matches the \
dominant physics, and say why.
2. Build the parameters. Pass every physical value as a string with its unit \
("2 mm", "85 degC", "12 W/(m^2*K)"). Temperatures are absolute: write "25 degC" or \
"298 K". Use the person's numbers exactly. Where they left something out, choose a \
sensible engineering value and remember it as an assumption to report.
3. Run it with run_simulation and read the whole verification report. Every run is \
checked for dimensional consistency, well-posed boundary conditions, solver residual, \
grid convergence, agreement with a known solution, the conservation laws of that \
physics, time-step stability and symmetry.
4. Treat a failed check as information about your setup, not as noise. A dimensional \
failure means a unit is wrong. A well-posedness failure means a boundary condition is \
missing or contradictory. A convergence failure or warning means the grid is too coarse, \
so raise the resolution. A CFL failure means the time step is too large. A regime \
warning (for example a turbulent Reynolds number in a laminar model) means the model \
does not apply as set up. Fix the cause and run again. Do not adjust inputs just to \
make a check pass if that changes the problem the person asked about.
5. Answer once the report is clean, or once you have established that it cannot be made \
clean. Never present numbers from a run with failed checks as the answer; say plainly \
what failed and what that means.

If no template fits the problem, say so directly, name the closest one and what it would \
and would not capture, and stop there or run that one only as a clearly labelled \
approximation. Do not force a problem into a template that models different physics.

Your final answer is read by an engineer or student who has not seen the tool output. \
Lead with the result they asked for, with units. Then give the setup and the assumptions \
you made, the other useful numbers, and a short account of how the result was verified: \
which checks passed, any warnings and why they are or are not a concern, and the limits \
of the model. Keep it compact and concrete."""

TOOLS = [
    {
        "name": "list_simulations",
        "description": (
            "List every available simulation template with its domain and a short "
            "description of the physics it solves. Call this first to choose a template."),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "describe_simulation",
        "description": (
            "Get the full parameter schema of one template: each parameter's name, the "
            "unit the solver expects, its default, allowed choices and what it means. "
            "Call this before run_simulation so parameter names and units are right."),
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "template name"}},
            "required": ["name"], "additionalProperties": False},
    },
    {
        "name": "run_simulation",
        "description": (
            "Run a simulation and its full self-check protocol. Returns the results "
            "(quantities of interest with units) and a verification report listing "
            "every check as pass, warn, fail or skip with an explanation. Omitted "
            "parameters take their defaults. Wrong units are reported as a failed "
            "dimensional-analysis check rather than an error."),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "template name"},
                "params": {
                    "type": "object",
                    "description": (
                        "parameter values keyed by name; physical quantities as strings "
                        "with units, e.g. {\"fin_length\": \"40 mm\"}")},
                "resolution": {
                    "type": "integer",
                    "description": (
                        "grid resolution; omit for the template default, raise it when "
                        "the mesh-convergence check warns or fails")},
            },
            "required": ["name"], "additionalProperties": False},
    },
]


@dataclass
class Answer:
    text: str
    runs: list = field(default_factory=list)       # every Result, in order
    stop_reason: str = "end_turn"

    @property
    def result(self):
        """The last run (normally the verified one the answer is based on)."""
        return self.runs[-1] if self.runs else None

    @property
    def verified(self) -> bool:
        return bool(self.runs) and self.runs[-1].ok


class Agent:
    def __init__(self, model: str = DEFAULT_MODEL, client=None, max_turns: int = 16,
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
        self.runs: list = []

    # -- tools -----------------------------------------------------------------
    def _tool(self, name: str, args: dict) -> str:
        if name == "list_simulations":
            return json.dumps([{k: s[k] for k in ("name", "title", "domain", "description")}
                               for s in sa.catalog()])
        if name == "describe_simulation":
            return json.dumps(sa.get(args["name"]).schema())
        if name == "run_simulation":
            res = sa.run(args["name"], args.get("params") or {}, args.get("resolution"))
            self.runs.append(res)
            self.on_event("run", res)
            return json.dumps(res.to_dict(with_fields=False))
        raise ValueError(f"unknown tool '{name}'")

    def _call_tool(self, block) -> dict:
        args = block.input if isinstance(block.input, dict) else {}
        self.on_event("tool", {"name": block.name, "input": args})
        try:
            return {"type": "tool_result", "tool_use_id": block.id,
                    "content": self._tool(block.name, args)}
        except Exception as exc:    # the model can read the error and correct itself
            return {"type": "tool_result", "tool_use_id": block.id, "is_error": True,
                    "content": f"{type(exc).__name__}: {exc}"}

    # -- loop ------------------------------------------------------------------
    def solve(self, problem: str) -> Answer:
        """Run the agent on one problem statement."""
        self.runs = []
        messages = [{"role": "user", "content": problem}]
        response = None
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
            for block in response.content:
                if block.type == "text" and block.text.strip():
                    self.on_event("text", block.text)
            if response.stop_reason != "tool_use":
                break
            # keep the assistant turn intact (thinking blocks included)
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": [
                self._call_tool(b) for b in response.content if b.type == "tool_use"]})
        text = "\n".join(b.text for b in response.content if b.type == "text").strip()
        reason = response.stop_reason
        if reason == "refusal":
            text = text or "The model declined this request."
        elif reason == "max_tokens":
            text += "\n\n[answer cut off: output limit reached]"
        elif reason == "tool_use":
            text += "\n\n[stopped: reached the limit of agent steps before finishing]"
        return Answer(text, list(self.runs), reason)


def ask(problem: str, **kwargs) -> Answer:
    """One-shot helper: ``ask("How hot does a 5 W chip get on a ...?")``."""
    return Agent(**kwargs).solve(problem)
