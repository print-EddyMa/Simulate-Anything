"""The live engine: turns building, testing and optimising a design into a
stream of small events an interface can render as they happen.

Event shapes are documented in docs/protocol.md.  The scripted demos here use
the same event stream as the live agent; their narration is written in
advance, but every number in it comes from simulations run at that moment.
"""
from __future__ import annotations

import copy
import time

from . import examples
from .design import DesignError, geometry_issues, normalise, part_by_id, summary
from .optimize import optimise_steps
from .scenarios import run_scenario, summarise


# -- building blocks ----------------------------------------------------------------
def status(phase, label):
    return {"type": "status", "phase": phase, "label": label}


def thought(text):
    return {"type": "thought", "text": text}


def design_event(design, prev=None):
    """Snapshot of a (normalised) design with what changed since ``prev``."""
    added, changed = [], []
    old = {p["id"]: p for p in (prev or {}).get("parts", [])}
    for p in design["parts"]:
        if p["id"] not in old:
            added.append(p["id"])
        elif old[p["id"]] != p:
            changed.append(p["id"])
    return {"type": "design", "design": design, "summary": summary(design),
            "issues": geometry_issues(design), "added": added, "changed": changed}


def auto_diagram(design):
    """A block diagram for designs that do not bring their own: one node per
    part, mechanical links from the assembly steps, test loads as inputs."""
    nodes = [{"id": p["id"], "label": p["name"], "kind": "component",
              "group": p.get("group") or "Parts", "part": p["id"]} for p in design["parts"]]
    edges, seen = [], set()
    for step in design.get("assembly", []):
        ids = step["parts"]
        for a, b in zip(ids, ids[1:]):
            if (a, b) not in seen:
                seen.add((a, b))
                edges.append({"from": a, "to": b, "label": "", "kind": "mechanical"})
    kinds = {"load": "mechanical", "drop": "mechanical", "vibration": "mechanical",
             "wind": "mechanical", "heat": "heat", "fire": "heat", "enclosure": "heat",
             "current": "power", "power": "power", "flow": "mechanical", "coverage": "rf"}
    for sc in design.get("scenarios", []):
        nid = f"env_{sc['id']}"
        nodes.append({"id": nid, "label": sc.get("title") or sc["type"], "kind": "environment",
                      "group": "Real world"})
        edges.append({"from": nid, "to": sc["part"], "label": sc["type"],
                      "kind": kinds.get(sc["type"], "mechanical")})
    return {"nodes": nodes, "edges": edges}


def diagram_event(design):
    return {"type": "diagram", "diagram": design.get("diagram") or auto_diagram(design)}


def test_events(design, verify=True, ids=None):
    """Yield test_started / test_result events; the last item yielded is
    ("evaluation", evaluation) - callers filter it out."""
    tests = []
    for sc in design["scenarios"]:
        if ids is not None and sc["id"] not in ids:
            continue
        yield {"type": "test_started", "id": sc["id"], "part": sc["part"],
               "test_type": sc["type"], "title": sc.get("title") or sc["type"].capitalize()}
        result = run_scenario(design, sc, verify=verify, detail=True)
        tests.append(result)
        yield {"type": "test_result", "test": result}
    yield ("evaluation", summarise(tests))


def run_tests(design, verify=True, ids=None):
    """(events, evaluation) without streaming."""
    events, ev = [], None
    for item in test_events(design, verify, ids):
        if isinstance(item, tuple):
            ev = item[1]
        else:
            events.append(item)
    return events, ev


def optimise_events(design, before=None):
    """Yield change/design events while optimising; the last item yielded is
    ("result", result)."""
    current = design
    for kind, payload, new in optimise_steps(design, before=before):
        if kind == "change":
            yield {"type": "change", **{k: payload[k] for k in
                                        ("change", "reason", "kind", "mass_g", "tests")}}
            yield design_event(new, current)
            current = new
        else:
            yield ("result", payload)


def _fmt_kg(grams):
    return f"{grams / 1000:.1f} kg" if grams >= 1000 else f"{grams:.0f} g"


def answer_markdown(design, before, after, steps, extra=None):
    s = summary(design)
    lines = []
    passed = after["counts"]["pass"]
    total = len(after["tests"])
    if after["all_pass"]:
        lines.append(f"**{design['name']}** passes all {total} real-world tests on verified "
                     "simulations.")
    else:
        lines.append(f"**{design['name']}** passes {passed} of {total} tests. The rest need a "
                     "different design idea; see below.")
    lines.append("")
    lines.append("| Test | Result |")
    lines.append("|---|---|")
    for t in after["tests"]:
        mark = "Pass" if t["status"] == "pass" else t["status"].capitalize()
        lines.append(f"| {t['title']} | {mark}: {t['headline']} |")
    if steps or extra:
        lines.append("")
        lines.append("**What changed**")
        for e in extra or []:
            lines.append(f"- {e}")
        for st in steps:
            lines.append(f"- {st['change']}")
    if before is not None:
        b = before["counts"]["pass"]
        lines.append("")
        lines.append(f"Before: {b} of {len(before['tests'])} tests passed. After: {passed} of "
                     f"{total}. Mass {_fmt_kg(s['mass_g'])}, materials about "
                     f"${s['material_cost']:.0f}.")
    return "\n".join(lines)


# -- scripted demos ------------------------------------------------------------------
class _Pacer:
    def __init__(self, pace):
        self.pace = pace

    def __call__(self, seconds):
        if self.pace > 0:
            time.sleep(seconds * self.pace)


def _subset(design, ids):
    d = copy.deepcopy(design)
    d["parts"] = [p for p in design["parts"] if p["id"] in ids]
    d["assembly"] = [s for s in design["assembly"] if set(s["parts"]) <= set(ids)]
    d["scenarios"] = []
    return normalise(d)


def _diagram_subset(diagram, ids):
    nodes = [n for n in diagram["nodes"] if n["id"] in ids]
    edges = [e for e in diagram["edges"] if e["from"] in ids and e["to"] in ids]
    return {"type": "diagram", "diagram": {"nodes": nodes, "edges": edges}}


def demo_ghostlight(pace=1.0):
    wait = _Pacer(pace)
    full = normalise(examples.get("ghostlight")["design"])
    diagram = full["diagram"]
    yield status("understand", "Reading the request")
    wait(0.5)
    yield thought("Ghostlight is a passive radar: it never transmits. One antenna records a "
                  "TV broadcast; a second listens for the faint copies of it that bounce off "
                  "drones, aircraft and birds.")
    wait(0.9)
    yield thought("To stand alone at a substation it needs what the prototype lacks: a mast "
                  "that survives storms, a sealed box for the electronics, its own power, and "
                  "enough sensitivity to see a small drone well beyond the fence.")
    wait(0.9)
    yield status("design", "Sketching the system")
    wait(0.3)
    stages = [["tv", "drone", "ref", "surv"],
              ["tv", "drone", "ref", "surv", "sdr", "cpu", "dsp", "report"],
              ["tv", "drone", "ref", "surv", "sdr", "cpu", "dsp", "report", "mast",
               "solar", "battery"]]
    for ids in stages:
        yield _diagram_subset(diagram, ids)
        wait(0.7)
    yield status("build", "Building it part by part")
    groups = [
        (["tripod", "mast"], "A 2.6 m aluminium mast on a tripod lifts the antennas clear "
                             "of people and parked vehicles."),
        (["arm_clamp", "antenna_arm", "surv_antenna", "ref_clamp", "ref_arm", "ref_dipole"],
         "The surveillance antenna faces the area to protect; the reference dipole faces the "
         "TV tower behind it."),
        (["enclosure", "sdr", "computer", "coax_surv", "coax_ref"],
         "A sealed polycarbonate box holds a two-channel SDR on one clock and the computer "
         "that runs detection."),
        (["battery", "panel_bracket", "solar"],
         "A LiFePO4 battery and a solar panel keep it running without mains power."),
    ]
    built, prev = [], None
    for ids, text in groups:
        built += ids
        yield thought(text)
        snap = _subset(full, built)
        yield design_event(snap, prev)
        prev = snap
        wait(1.0)
    design = copy.deepcopy(full)
    design["parts"] = [p for p in full["parts"] if p["id"] != "screen"]
    design = normalise(design)
    yield design_event(design, prev)

    yield status("test", "Testing it against the real world")
    wait(0.3)
    first, ev = [], None
    for item in test_events(design, verify=True):
        if isinstance(item, tuple):
            ev = item[1]
        else:
            if item["type"] == "test_result":
                first.append(item["test"])
            yield item
            wait(0.35)
    before = ev
    fails = [t for t in ev["tests"] if t["status"] != "pass"]
    yield thought(f"{len(ev['tests']) - len(fails)} of {len(ev['tests'])} tests pass. "
                  f"Fixing the {len(fails)} that don't.")
    wait(0.6)

    cov = next(t for t in ev["tests"] if t["id"] == "coverage")
    rng = cov["sim"]["results"]["detection_range"]["value"] / 1e3
    rise = cov["sim"]["results"]["noise_floor_rise"]["value"]
    yield status("design", "Rethinking the antenna")
    yield thought(f"Detection only reaches {rng:.2f} km. The receiver isn't the limit: TV "
                  f"signal leaking into the back of the surveillance antenna raises its noise "
                  f"floor by {rise:.0f} dB. A bigger antenna wouldn't help, because it would "
                  "hear the leak just as loudly.")
    wait(1.2)
    yield thought("The fix is geometry, not gain: a wire-mesh screen between the antenna and "
                  "the tower shades it from the direct signal while echoes from the front "
                  "still get through.")
    wait(0.8)
    with_screen = copy.deepcopy(design)
    with_screen["parts"].append(copy.deepcopy(examples.GHOSTLIGHT_SCREEN))
    with_screen["assembly"].insert(2, {"parts": ["screen"], "action":
                                       "Hang the mesh screen on the arm, between the "
                                       "surveillance antenna and the TV tower"})
    with_screen = normalise(with_screen)
    yield design_event(with_screen, design)
    yield {"type": "diagram", "diagram": diagram}
    wait(0.6)
    for item in test_events(with_screen, verify=True, ids={"coverage"}):
        if not isinstance(item, tuple):
            yield item
            if item["type"] == "test_result":
                cov2 = item["test"]
    rng2 = cov2["sim"]["results"]["detection_range"]["value"] / 1e3
    yield thought(f"With the screen, the same drone shows up out to {rng2:.2f} km, "
                  f"{rng2 / rng:.1f} times farther.")
    wait(0.8)

    yield status("optimise", "Sizing the mast, battery and enclosure")
    yield thought("The rest is sizing. Trying changes and keeping only the ones the "
                  "simulations prove, then trimming anything heavier than it needs to be.")
    wait(0.5)
    result = None
    for item in optimise_events(with_screen):
        if isinstance(item, tuple):
            result = item[1]
        else:
            yield item
            wait(0.45)
    yield status("verify", "Final verification")
    wait(0.3)
    final = result["design"]
    for t in result["after"]["tests"]:
        yield {"type": "test_result", "test": t}
        wait(0.2)
    extra = [f"Added a direct-path screen: detection {rng:.2f} km -> {rng2:.2f} km"]
    yield status("done", "Done")
    yield {"type": "done", "design": final, "summary": summary(final),
           "evaluation": result["after"], "before": before,
           "answer": answer_markdown(final, before, result["after"], result["steps"], extra),
           "verified": result["all_pass"]}


def demo_generic(example_id, pace=1.0):
    wait = _Pacer(pace)
    ex = examples.get(example_id)
    design = normalise(ex["design"])
    yield status("understand", "Reading the request")
    yield thought(ex["design"]["description"])
    wait(0.6)
    yield status("design", "Sketching the design")
    yield diagram_event(design)
    wait(0.5)
    yield status("build", "Building it part by part")
    built, prev = [], None
    for step in design["assembly"] or [{"parts": [p["id"] for p in design["parts"]],
                                        "action": "Assemble"}]:
        built += [pid for pid in step["parts"] if pid not in built]
        yield thought(step["action"])
        snap = _subset(design, built)
        yield design_event(snap, prev)
        prev = snap
        wait(0.8)
    yield design_event(design, prev)
    yield status("test", "Testing it against the real world")
    before = None
    for item in test_events(design, verify=True):
        if isinstance(item, tuple):
            before = item[1]
        else:
            yield item
            wait(0.3)
    fails = [t for t in before["tests"] if t["status"] != "pass"]
    if fails:
        yield thought(f"{len(fails)} of {len(before['tests'])} tests fail. Trying changes and "
                      "keeping only what the simulations prove.")
        yield status("optimise", "Improving the design")
    else:
        yield thought("Every test passes. Checking whether any material can come off.")
        yield status("optimise", "Trimming excess material")
    result = None
    for item in optimise_events(design, before=before):
        if isinstance(item, tuple):
            result = item[1]
        else:
            yield item
            wait(0.4)
    yield status("verify", "Final verification")
    for t in result["after"]["tests"]:
        yield {"type": "test_result", "test": t}
        wait(0.15)
    final = result["design"]
    yield status("done", "Done")
    yield {"type": "done", "design": final, "summary": summary(final),
           "evaluation": result["after"], "before": before,
           "answer": answer_markdown(final, before, result["after"], result["steps"]),
           "verified": result["all_pass"]}


def demo(example_id, pace=1.0):
    """Event stream for a built-in example."""
    if example_id == "ghostlight":
        yield from demo_ghostlight(pace)
    else:
        yield from demo_generic(example_id, pace)


def optimise_stream(raw_design):
    try:
        design = normalise(raw_design)
    except DesignError as exc:
        yield {"type": "error", "message": str(exc)}
        return
    yield status("test", "Testing the current design")
    before = None
    for item in test_events(design, verify=True):
        if isinstance(item, tuple):
            before = item[1]
        else:
            yield item
    yield status("optimise", "Improving the design")
    result = None
    for item in optimise_events(design, before=before):
        if isinstance(item, tuple):
            result = item[1]
        else:
            yield item
    final = result["design"]
    yield status("done", "Done")
    yield {"type": "done", "design": final, "summary": summary(final),
           "evaluation": result["after"], "before": before,
           "answer": answer_markdown(final, before, result["after"], result["steps"]),
           "verified": result["all_pass"]}


def evaluate_design(raw_design, verify=True):
    design = normalise(raw_design)
    _, ev = run_tests(design, verify)
    return {"design": design, "summary": summary(design), "issues": geometry_issues(design),
            "diagram": design.get("diagram") or auto_diagram(design), "evaluation": ev}
