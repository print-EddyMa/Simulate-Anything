"""Create Anything: design format, real-world tests, optimiser, demos, server, agent."""
import base64
import copy
import json
import struct
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import numpy as np
import pytest

import simulate_anything as sa
from create_anything import design as D
from create_anything import engine, examples, materials, stl
from create_anything.agent import Session
from create_anything.optimize import optimise
from create_anything.scenarios import evaluate, run_scenario
from create_anything.server import Handler


# -- the new simulations ---------------------------------------------------------------
@pytest.mark.parametrize("name", ["flame_exposure", "wire_heating", "drop_impact",
                                  "passive_radar", "enclosure_thermal", "solar_battery"])
def test_new_simulations_verify(name):
    res = sa.run(name)
    assert res.ok, res.to_text()


def test_passive_radar_physics():
    from simulate_anything.sims.realworld import PassiveRadarCoverage as P
    base = P(rcs="0.01 m^2", cancellation_db=30.0)
    screened = P(rcs="0.01 m^2", cancellation_db=30.0, front_to_back_db=25.0)
    r0, r1 = base._boresight_root(), screened._boresight_root()
    # transmitter straight behind: Rt = baseline + Rr, and Rt * Rr scales as N0^(-1/2)
    lam, tx, direct, thermal, n0 = base._consts()
    _, _, _, _, n1 = screened._consts()
    b = 20e3
    assert r1 * (b + r1) / (r0 * (b + r0)) == pytest.approx((n0 / n1) ** 0.5, rel=1e-6)
    # a bigger antenna does not help when the leak dominates
    big = P(rcs="0.01 m^2", cancellation_db=30.0, rx_gain_dbi=16.0)
    assert big._boresight_root() == pytest.approx(r0, rel=0.05)
    bad = sa.run("passive_radar", {"rcs": "0.01 m"})              # wrong units
    assert not bad.ok


def test_solar_battery_runtime_and_flame_cfl():
    dark = sa.run("solar_battery", {"weather_factor": 0.0, "days": 3})
    assert dark.value("energy_not_supplied") > 0
    assert dark.value("battery_only_runtime") == pytest.approx(640 * 0.9 / 13, rel=1e-6)
    hot = sa.run("flame_exposure", {"heat_flux": "60 kW/m^2"})
    assert hot.value("reaches_critical") == 1 and hot.value("time_to_critical") < 100


# -- design format -------------------------------------------------------------------------
def test_design_validation_messages():
    with pytest.raises(D.DesignError, match="at least one part"):
        D.normalise({"parts": []})
    with pytest.raises(D.DesignError, match="shape must be"):
        D.normalise({"parts": [{"id": "a", "shape": "blob"}]})
    with pytest.raises(D.DesignError, match="inner_radius"):
        D.normalise({"parts": [{"id": "a", "shape": "tube", "radius": 5, "inner_radius": 6,
                                "length": 10}]})
    with pytest.raises(D.DesignError, match="unknown material"):
        D.normalise({"parts": [{"id": "a", "shape": "sphere", "radius": 5,
                                "material": "unobtainium"}]})
    with pytest.raises(D.DesignError, match="unknown part"):
        D.normalise({"parts": [{"id": "a", "shape": "sphere", "radius": 5}],
                     "scenarios": [{"type": "load", "part": "b"}]})


def test_geometry_mass_and_issues():
    d = D.normalise({"parts": [
        {"id": "cube", "shape": "box", "size": [10, 10, 10], "material": "aluminium"},
        {"id": "far", "shape": "sphere", "radius": 5, "position": [500, 0, 0]}]})
    s = D.summary(d)
    assert s["parts"][0]["mass_g"] == pytest.approx(1000 * 1e-9 * 2700 * 1e3)
    msgs = " ".join(i["message"] for i in D.geometry_issues(d))
    assert "not attached" in msgs and "no real-world tests" in msgs
    rotated = D.normalise({"parts": [{"id": "r", "shape": "box", "size": [100, 10, 10],
                                      "rotation": [0, 0, 90]}]})
    lo, hi = D.aabb(rotated["parts"][0])
    assert hi - lo == pytest.approx([10, 100, 10], abs=1e-9)


def test_materials_catalog():
    assert materials.get("aluminum")["id"] == "aluminium"
    assert all(m["density"] > 0 and m["color"].startswith("#") for m in materials.catalog())


# -- tests and optimiser --------------------------------------------------------------------
def test_load_test_matches_beam_theory():
    d = D.normalise({"parts": [{"id": "arm", "shape": "box", "size": [200, 20, 10],
                                "material": "steel"}],
                     "scenarios": [{"id": "t", "type": "load", "part": "arm",
                                    "force": "100 N", "support": "cantilever"}]})
    r = run_scenario(d, d["scenarios"][0])
    e, i = 200e9, 0.02 * 0.01 ** 3 / 12
    weight = 7850 * 9.80665 * 0.2 * 0.02 * 0.01 / 0.2
    tip = 100 * 0.2 ** 3 / (3 * e * i) + weight * 0.2 ** 4 / (8 * e * i)
    assert r["metrics"][1]["value"] == pytest.approx(tip * 1e3, rel=1e-3)
    assert r["status"] == "pass" and r["checks"]["verdict"] == "VERIFIED"
    # the reproduce command re-runs the same simulation to the same answer
    import shlex
    from simulate_anything import cli
    argv = shlex.split(r["reproduce"])[1:] + ["--json"]
    import contextlib, io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert cli.main(argv) == 0
    rerun = json.loads(buf.getvalue())
    assert rerun["results"]["max_deflection"]["value"] == pytest.approx(r["metrics"][1]["value"])


def test_unverified_simulation_never_counts_as_pass():
    d = D.normalise({"parts": [{"id": "w", "shape": "cylinder", "radius": 0.5, "length": 100,
                                "material": "pla"}],
                     "scenarios": [{"id": "c", "type": "current", "part": "w",
                                    "current": "1 A"}]})
    r = run_scenario(d, d["scenarios"][0])
    assert r["status"] == "error" and "insulator" in r["headline"]


@pytest.mark.parametrize("example", sorted(examples.EXAMPLES))
def test_examples_optimise_to_verified_pass(example):
    d = D.normalise(examples.get(example)["design"])
    if example == "drone_radar":
        d["parts"].append(copy.deepcopy(examples.RADAR_SCREEN))
        d = D.normalise(d)
    before = evaluate(d, verify=False, detail=False)
    assert not before["all_pass"]
    out = optimise(d)
    assert out["all_pass"], out["note"]
    assert all(t["checks"]["verdict"] in ("VERIFIED", "PASSED WITH WARNINGS")
               for t in out["after"]["tests"])


def test_optimiser_keeps_contents_inside_housings():
    d = D.normalise(examples.get("drone_radar")["design"])
    d["parts"].append(copy.deepcopy(examples.RADAR_SCREEN))
    out = optimise(D.normalise(d))
    box = D.part_by_id(out["design"], "enclosure")
    for pid in ("sdr", "computer"):
        assert D.contains(box, D.part_by_id(out["design"], pid))


def test_radar_screen_is_the_coverage_fix():
    d = D.normalise(examples.get("drone_radar")["design"])
    sc = next(s for s in d["scenarios"] if s["id"] == "coverage")
    without = run_scenario(d, sc, verify=False, detail=False)
    d["parts"].append(copy.deepcopy(examples.RADAR_SCREEN))
    d = D.normalise(d)
    with_screen = run_scenario(d, sc, verify=False, detail=False)
    assert without["status"] == "fail" and with_screen["status"] == "pass"


# -- live engine -------------------------------------------------------------------------
@pytest.mark.parametrize("example", sorted(examples.EXAMPLES))
def test_demo_stream_is_well_formed(example):
    evs = list(engine.demo(example, pace=0))
    types = [e["type"] for e in evs]
    assert types[0] == "status" and types[-1] == "done"
    assert {"thought", "design", "test_result", "diagram"} <= set(types)
    assert evs[-1]["verified"] is True
    json.dumps(evs)
    started = {e["id"] for e in evs if e["type"] == "test_started"}
    assert started <= {e["test"]["id"] for e in evs if e["type"] == "test_result"}


def test_stl_import_cube():
    v = np.array([[0, 0, 0], [20, 0, 0], [20, 20, 0], [0, 20, 0], [0, 0, 20], [20, 0, 20],
                  [20, 20, 20], [0, 20, 20]], float)
    faces = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4), (1, 2, 6),
             (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    data = b"\0" * 80 + struct.pack("<I", len(faces)) + b"".join(
        struct.pack("<12fH", 0, 0, 0, *v[a], *v[b], *v[c], 0) for a, b, c in faces)
    part = stl.to_part(data, "Cube")
    assert part["mesh"]["size"] == [20.0, 20.0, 20.0]
    assert part["mesh"]["volume"] == pytest.approx(8000.0)
    ascii_stl = "solid c\n" + "".join(
        "facet normal 0 0 0\nouter loop\n" + "".join(f"vertex {x} {y} {z}\n" for x, y, z in
                                                     (v[a], v[b], v[c])) + "endloop\nendfacet\n"
        for a, b, c in faces) + "endsolid c\n"
    assert stl.to_part(ascii_stl.encode(), "Cube")["mesh"]["volume"] == pytest.approx(8000.0)
    D.normalise({"parts": [part]})


def test_server_endpoints():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def post(path, body):
        req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST")
        return urllib.request.urlopen(req)

    try:
        assert json.load(urllib.request.urlopen(base + "/api/status"))["version"]
        ids = [e["id"] for e in json.load(urllib.request.urlopen(base + "/api/examples"))]
        assert ids[0] == "drone_radar"
        out = json.load(post("/api/evaluate", {"design": examples.get("heatsink")["design"]}))
        assert out["evaluation"]["tests"][0]["status"] == "fail"
        raw = post("/api/demo/heatsink", {"pace": 0}).read().decode()
        evs = [json.loads(line[6:]) for line in raw.splitlines() if line.startswith("data: ")]
        assert evs[-1]["type"] == "done" and evs[-1]["verified"]
        cube = base64.b64encode(b"\0" * 80 + struct.pack("<I", 1) + struct.pack(
            "<12fH", 0, 0, 0, 0, 0, 0, 10, 0, 0, 0, 10, 0, 0)).decode()
        assert json.load(post("/api/import", {"data": cube}))["part"]["shape"] == "mesh"
    finally:
        httpd.shutdown()
        httpd.server_close()


# -- agent loop with a scripted stand-in model ---------------------------------------------
class _FakeMessages:
    def __init__(self, script):
        self.script, self.calls = script, []

    def create(self, **kwargs):
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        reason, blocks = self.script[len(self.calls) - 1]
        content = []
        for i, b in enumerate(blocks):
            if b[0] == "text":
                content.append(SimpleNamespace(type="text", text=b[1]))
            else:
                content.append(SimpleNamespace(type="tool_use", id=f"t{len(self.calls)}{i}",
                                               name=b[0], input=b[1]))
        return SimpleNamespace(content=content, stop_reason=reason)


def test_agent_builds_tests_and_optimises():
    sink = examples.get("heatsink")["design"]
    bad = copy.deepcopy(sink)
    bad["parts"][0]["shape"] = "blob"
    fake = _FakeMessages([
        ("tool_use", [("text", "Sketching it."),
                      ("set_diagram", {"nodes": [{"id": "s", "label": "Sink", "kind":
                                                  "component"}], "edges": []})]),
        ("tool_use", [("set_design", bad)]),             # invalid: model must fix it
        ("tool_use", [("set_design", sink)]),
        ("tool_use", [("run_tests", {})]),
        ("tool_use", [("optimise", {})]),
        ("end_turn", [("text", "Done: it now stays below 85 C.")]),
    ])
    session = Session(client=SimpleNamespace(beta=SimpleNamespace(messages=fake)))
    events = list(session.events("A heat sink for a 40 W LED"))
    types = [e["type"] for e in events]
    assert types[-1] == "done" and events[-1]["verified"]
    assert {"diagram", "design", "test_result", "change", "tool", "thought"} <= set(types)
    error_result = fake.calls[2]["messages"][-1]["content"][0]
    assert error_result.get("is_error") and "shape must be" in error_result["content"]
    assert fake.calls[0]["model"] == "claude-opus-5-5"
