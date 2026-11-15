"""The designer agent: a request (words, a photo, an imported part) in, a
tested and improved design out, streamed as events while it works.

Claude does the design thinking: what the thing is, its parts and materials,
how it goes together, and which real-world situations it has to survive.  It
never does the physics.  Every number comes from the verified simulations,
and a design is only called done when its tests pass on them.
"""
from __future__ import annotations

import copy
import json
import queue
import threading

from . import engine, materials
from .design import DesignError, LOOKS, SHAPES, geometry_issues, normalise, summary
from .scenarios import DESCRIPTIONS

DEFAULT_MODEL = "claude-opus-5-5"

SYSTEM = f"""You are Margin, an engineering design agent that can create anything. People describe \
something they want to build, show a photo of it, or import a part, and you design it: \
its parts, materials, dimensions and assembly, and the real-world situations it has to \
survive. Simulations then test it, and an optimiser improves it.

How you work
1. Understand the request. Work out what the thing must do, where it will be used and \
what could break it. Say it in one or two short sentences.
2. Sketch the system with set_diagram: the main components, what flows between them \
(signals, power, heat, force) and anything from the outside world that matters.
3. Build the design with set_design. Add parts in a sensible order and call \
update_design as you refine it, so the person watching sees it come together. Use real \
dimensions in millimetres, z up, positions at the centre of each part's bounding box, \
parts touching where they join. Use catalogue materials. Give every part a short \
purpose and a group (its subsystem). Use specs for datasheet values of bought parts \
(power, gain, noise figure, capacity). Use look hints so it renders recognisably.
4. Define the real-world tests in the design's scenarios: the loads, drops, heat, fire, \
current, flow, wind, sun, power or radio coverage it must survive, with realistic values \
and a clear pass limit. Pick the three to six that matter most for this object.
5. run_tests. Read every result. A failed test is information about the design. When a \
test fails for a reason that sizing cannot fix (the wrong kind of part, a missing part, \
the wrong layout), change the design yourself and test again. Explain the reason in a \
sentence first, citing the number that showed it.
6. When the remaining failures are about size, thickness or material, call optimise. It \
tries changes and keeps only those the simulations prove.
When the request comes with a current design, change that design (keep its ids, \
parts and tests unless asked) rather than starting over: use update_design, then \
run_tests.

7. Finish with a short summary for an engineer who has not seen the work: what it is, \
the key numbers with units, what changed and why, and the limits of the models used. \
Never present a result from a failed or unverified test as a pass.

Shapes: {", ".join(SHAPES)}. Looks: {", ".join(LOOKS)}.
Materials: {", ".join(sorted(materials.MATERIALS))}.
Test types:
""" + "\n".join(f"- {k}: {v}" for k, v in DESCRIPTIONS.items()) + """

Keep narration short: one sentence before a tool call, saying what you are doing and \
why. The person sees every step live."""

PART_SCHEMA = {
    "type": "object",
    "description": "a part; see the system prompt for shapes and fields (all lengths mm)",
    "properties": {"id": {"type": "string"}, "name": {"type": "string"},
                   "shape": {"type": "string", "enum": list(SHAPES)}},
    "required": ["id", "name", "shape"],
}

TOOLS = [
    {"name": "set_diagram",
     "description": "Show the system block diagram: components, what flows between them, "
                    "and outside influences. Replaces the previous diagram.",
     "input_schema": {"type": "object", "properties": {
         "nodes": {"type": "array", "items": {"type": "object", "properties": {
             "id": {"type": "string"}, "label": {"type": "string"},
             "kind": {"type": "string", "enum": ["component", "signal", "software",
                                                  "environment", "power"]},
             "group": {"type": "string"}, "part": {"type": "string"}},
             "required": ["id", "label", "kind"]}},
         "edges": {"type": "array", "items": {"type": "object", "properties": {
             "from": {"type": "string"}, "to": {"type": "string"},
             "label": {"type": "string"},
             "kind": {"type": "string", "enum": ["rf", "data", "power", "mechanical",
                                                  "heat"]}},
             "required": ["from", "to", "kind"]}}},
         "required": ["nodes", "edges"]}},
    {"name": "set_design",
     "description": "Replace the whole design: name, description, parts, assembly steps and "
                    "scenarios (real-world tests). Returns the mass, cost and any geometry "
                    "problems, or the validation error to fix.",
     "input_schema": {"type": "object", "properties": {
         "name": {"type": "string"}, "description": {"type": "string"},
         "parts": {"type": "array", "items": PART_SCHEMA},
         "assembly": {"type": "array", "items": {"type": "object", "properties": {
             "parts": {"type": "array", "items": {"type": "string"}},
             "action": {"type": "string"}}, "required": ["parts", "action"]}},
         "scenarios": {"type": "array", "items": {"type": "object", "properties": {
             "id": {"type": "string"}, "type": {"type": "string"},
             "part": {"type": "string"}, "title": {"type": "string"}},
             "required": ["type", "part"]}}},
         "required": ["name", "parts"]}},
    {"name": "update_design",
     "description": "Change the current design: add parts, remove parts by id, or modify "
                    "fields of existing parts; optionally replace assembly or scenarios.",
     "input_schema": {"type": "object", "properties": {
         "add": {"type": "array", "items": PART_SCHEMA},
         "remove": {"type": "array", "items": {"type": "string"}},
         "modify": {"type": "array", "items": {"type": "object", "properties": {
             "id": {"type": "string"}}, "required": ["id"]},
             "description": "objects with an id and the fields to change"},
         "assembly": {"type": "array", "items": {"type": "object"}},
         "scenarios": {"type": "array", "items": {"type": "object"}}}}},
    {"name": "run_tests",
     "description": "Run every real-world test on verified simulations. Returns each "
                    "test's status, headline, metrics, notes and any failed self-checks.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "optimise",
     "description": "Let the optimiser fix sizing and material problems: it changes "
                    "dimensions and materials of the tested parts, keeps only changes the "
                    "simulations prove, trims excess material, and re-verifies everything.",
     "input_schema": {"type": "object", "properties": {}}},
]


def _compact(test):
    out = {k: test[k] for k in ("id", "title", "status", "headline", "metrics", "notes")}
    sim = test.get("sim") or {}
    failed = [c for c in sim.get("verification", {}).get("checks", [])
              if c["status"] in ("fail", "warn")]
    if failed:
        out["self_check_problems"] = [f"{c['status']}: {c['name']}: {c['detail']}"
                                      for c in failed]
    return out


class Session:
    """One design conversation.  ``events()`` yields protocol events."""

    def __init__(self, client=None, model=DEFAULT_MODEL, max_turns=30):
        if client is None:
            try:
                import anthropic
            except ImportError as exc:
                raise RuntimeError("the agent needs the Anthropic SDK: pip install anthropic") \
                    from exc
            client = anthropic.Anthropic()
        self.client, self.model, self.max_turns = client, model, max_turns
        self.design = None
        self.diagram = None
        self.before = None
        self.evaluation = None
        self.steps = []

    # -- tools: each returns (result for the model, list of events) ------------------
    def _set_design(self, args):
        raw = copy.deepcopy(args)
        if self.diagram:
            raw.setdefault("diagram", self.diagram)
        new = normalise(raw)
        ev = engine.design_event(new, self.design)
        self.design = new
        return self._design_result(new), [ev]

    def _update_design(self, args):
        if self.design is None:
            raise DesignError("there is no design yet; call set_design first")
        raw = copy.deepcopy(self.design)
        drop = set(args.get("remove") or [])
        raw["parts"] = [p for p in raw["parts"] if p["id"] not in drop]
        by_id = {p["id"]: p for p in raw["parts"]}
        for mod in args.get("modify") or []:
            if mod.get("id") not in by_id:
                raise DesignError(f"cannot modify unknown part '{mod.get('id')}'")
            by_id[mod["id"]].update({k: v for k, v in mod.items() if k != "id"})
        raw["parts"] += args.get("add") or []
        for key in ("assembly", "scenarios"):
            if args.get(key) is not None:
                raw[key] = args[key]
        raw["assembly"] = [s for s in raw.get("assembly", [])
                           if set(s.get("parts", [])) <= {p["id"] for p in raw["parts"]}]
        new = normalise(raw)
        ev = engine.design_event(new, self.design)
        self.design = new
        return self._design_result(new), [ev]

    def _design_result(self, d):
        s = summary(d)
        return {"ok": True, "parts": len(d["parts"]), "mass_g": round(s["mass_g"], 1),
                "material_cost": round(s["material_cost"], 2), "size_mm": s["size_mm"],
                "geometry_issues": [i["message"] for i in geometry_issues(d)],
                "tests": [sc["id"] for sc in d["scenarios"]]}

    def _set_diagram(self, args):
        self.diagram = {"nodes": args.get("nodes", []), "edges": args.get("edges", [])}
        if self.design is not None:
            self.design["diagram"] = self.diagram
        return {"ok": True}, [{"type": "diagram", "diagram": self.diagram}]

    def _run_tests(self, args, emit):
        if self.design is None:
            raise DesignError("there is no design yet; call set_design first")
        ev = None
        for item in engine.test_events(self.design, verify=True):
            if isinstance(item, tuple):
                ev = item[1]
            else:
                emit(item)
        if self.before is None:
            self.before = ev
        self.evaluation = ev
        return {"counts": ev["counts"], "tests": [_compact(t) for t in ev["tests"]]}, []

    def _optimise(self, args, emit):
        if self.design is None:
            raise DesignError("there is no design yet; call set_design first")
        result = None
        for item in engine.optimise_events(self.design):
            if isinstance(item, tuple):
                result = item[1]
            else:
                emit(item)
        self.design = result["design"]
        self.evaluation = result["after"]
        self.steps += result["steps"]
        return {"all_pass": result["all_pass"], "note": result["note"],
                "changes": [s["change"] for s in result["steps"]],
                "mass_g": [round(m, 1) for m in result["mass_g"]],
                "tests": [_compact(t) for t in result["after"]["tests"]]}, []

    def _call(self, block, emit):
        name, args = block.name, (block.input if isinstance(block.input, dict) else {})
        labels = {"set_design": "Laying out the parts", "update_design": "Changing the design",
                  "set_diagram": "Drawing the system", "run_tests": "Running the real-world tests",
                  "optimise": "Optimising against the tests"}
        emit({"type": "tool", "name": name, "label": labels.get(name, name)})
        phase = {"set_diagram": ("design", "Sketching the system"),
                 "set_design": ("build", "Building the design"),
                 "update_design": ("build", "Refining the design"),
                 "run_tests": ("test", "Testing it against the real world"),
                 "optimise": ("optimise", "Optimising")}.get(name)
        if phase:
            emit(engine.status(*phase))
        try:
            if name == "set_design":
                result, evs = self._set_design(args)
            elif name == "update_design":
                result, evs = self._update_design(args)
            elif name == "set_diagram":
                result, evs = self._set_diagram(args)
            elif name == "run_tests":
                result, evs = self._run_tests(args, emit)
            elif name == "optimise":
                result, evs = self._optimise(args, emit)
            else:
                raise ValueError(f"unknown tool '{name}'")
            for e in evs:
                emit(e)
            return {"type": "tool_result", "tool_use_id": block.id,
                    "content": json.dumps(result)}
        except Exception as exc:          # the model reads the error and fixes its input
            return {"type": "tool_result", "tool_use_id": block.id, "is_error": True,
                    "content": f"{type(exc).__name__}: {exc}"}

    # -- the loop -------------------------------------------------------------------
    def _content(self, prompt, image=None, mesh=None):
        blocks = []
        if image and image.startswith("data:") and ";base64," in image:
            head, data = image.split(";base64,", 1)
            media = head[5:]
            if media in ("image/jpeg", "image/png", "image/gif", "image/webp"):
                blocks.append({"type": "image", "source": {"type": "base64",
                                                           "media_type": media, "data": data}})
        text = prompt.strip() or "Design what is shown."
        if mesh:
            m = mesh.get("mesh", {})
            text += ("\n\nThe person imported a part from an STL file. Use it as part "
                     f"'{mesh['id']}' unchanged (shape mesh, size {m.get('size')} mm, volume "
                     f"{m.get('volume', 0):.0f} mm^3); I will attach its triangles. Design "
                     "around it and test it.")
            self.imported = mesh
        blocks.append({"type": "text", "text": text})
        return blocks

    def run(self, prompt, emit, image=None, mesh=None, design=None):
        """Drive the conversation, calling ``emit(event)`` for every event.  With
        ``design`` the agent edits that design instead of starting over."""
        self.imported = None
        content = self._content(prompt, image, mesh)
        if design:
            self.design = normalise(design)
            self.diagram = self.design.get("diagram")
            emit(engine.design_event(self.design))
            content.insert(0, {"type": "text", "text": "Current design (edit it):\n"
                               + json.dumps(self.design)})
        messages = [{"role": "user", "content": content}]
        emit(engine.status("understand", "Reading the request"))
        response = None
        for _ in range(self.max_turns):
            response = self.client.beta.messages.create(
                model=self.model, max_tokens=16000, system=SYSTEM, tools=TOOLS,
                messages=messages,
                thinking={"type": "adaptive", "display": "summarized"},
                output_config={"effort": "high"},
                cache_control={"type": "ephemeral"},
                betas=["server-side-fallback-2026-07-01"], fallbacks="default")
            for block in response.content:
                if block.type == "thinking" and getattr(block, "thinking", ""):
                    emit({"type": "thinking", "text": block.thinking})
                elif block.type == "text" and block.text.strip():
                    emit({"type": "thought", "text": block.text.strip()})
            if response.stop_reason != "tool_use":
                break
            messages.append({"role": "assistant", "content": response.content})
            results = []
            for block in response.content:
                if block.type == "tool_use":
                    if self.imported and block.name in ("set_design", "update_design"):
                        self._attach_mesh(block.input)
                    results.append(self._call(block, emit))
            messages.append({"role": "user", "content": results})
        text = "\n".join(b.text for b in response.content if b.type == "text").strip()
        if response.stop_reason == "refusal":
            emit({"type": "error", "message": text or "The model declined this request."})
            return
        if self.design is None:
            emit({"type": "error", "message": text or "No design was produced."})
            return
        if self.evaluation is None:
            _, self.evaluation = engine.run_tests(self.design)
        emit(engine.status("done", "Done"))
        emit({"type": "done", "design": self.design, "summary": summary(self.design),
              "evaluation": self.evaluation, "before": self.before,
              "answer": text or engine.answer_markdown(self.design, self.before,
                                                       self.evaluation, self.steps),
              "verified": bool(self.evaluation and self.evaluation["all_pass"])})

    def _attach_mesh(self, args):
        for p in (args.get("parts") or []) + (args.get("add") or []):
            if p.get("id") == self.imported["id"]:
                p["mesh"] = self.imported["mesh"]
                p["shape"] = "mesh"

    def events(self, prompt, image=None, mesh=None, design=None):
        """Generator form of :meth:`run` (runs the loop on a worker thread)."""
        q = queue.Queue()
        done = object()

        def work():
            try:
                self.run(prompt, q.put, image, mesh, design)
            except Exception as exc:
                q.put({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
            finally:
                q.put(done)

        threading.Thread(target=work, daemon=True).start()
        while True:
            item = q.get()
            if item is done:
                return
            yield item
