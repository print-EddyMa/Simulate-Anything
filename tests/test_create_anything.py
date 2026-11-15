"""Create Anything: the design format, the real-world tests, the optimiser and the apps.

As elsewhere in the suite, faults are injected on purpose (a wrong unit, an
insulator asked to carry current, overlapping parts) and the matching failure
is required.
"""
import copy
import json
import math
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest

import create_anything as ca
from create_anything import cli, materials
from create_anything.agent import Designer
from create_anything.design import DesignError, geometry_issues, normalise, summary
from create_anything.optimize import MIN_FEATURE, optimise
from create_anything.scenarios import G0, evaluate, run_scenario, section
from create_anything.server import Handler


def bar(**scenario):
    """A PLA bar 150 x 20 x 4 mm, with one scenario on it."""
    sc = {"id": "t", "part": "bar", **scenario}
    return normalise({"name": "bar", "parts": [
        {"id": "bar", "shape": "box", "size": [150, 20, 4], "material": "pla"}],
        "scenarios": [sc]})


def one(design):
    return run_scenario(design, design["scenarios"][0], verify=True, detail=False)


# -- design document -------------------------------------------------------------
@pytest.mark.parametrize("bad, message", [
    ({"parts": []}, "at least one part"),
    ({"parts": [{"id": "a", "shape": "cone"}]}, "shape must be one of"),
    ({"parts": [{"id": "a", "shape": "box", "size": [1, 1, 1]},
                {"id": "a", "shape": "box", "size": [1, 1, 1]}]}, "duplicate id"),
    ({"parts": [{"id": "a", "shape": "box", "size": [10, 10, 10], "material": "unobtanium"}]},
     "unknown material"),
    ({"parts": [{"id": "a", "shape": "box", "size": [10, 10, 10], "wall": 5}]}, "wall is thicker"),
    ({"parts": [{"id": "a", "shape": "tube", "radius": 3, "inner_radius": 4, "length": 9}]},
     "inner_radius must be smaller"),
    ({"parts": [{"id": "a", "shape": "box", "size": [10, -1, 10]}]}, "must be positive"),
    ({"parts": [{"id": "a", "shape": "box", "size": [1, 1, 1]}],
      "scenarios": [{"type": "load", "part": "b"}]}, "unknown part 'b'"),
    ({"parts": [{"id": "a", "shape": "box", "size": [1, 1, 1]}],
      "assembly": [{"parts": ["z"], "action": "glue"}]}, "unknown part 'z'"),
])
def test_malformed_designs_are_rejected(bad, message):
    with pytest.raises(DesignError, match=message):
        normalise(bad)


def test_defaults_and_aliases():
    d = normalise({"parts": [{"shape": "sphere", "radius": 10, "material": "Aluminum"}]})
    p = d["parts"][0]
    assert p["id"] == "part1" and p["material"] == "aluminium"
    assert p["process"] == materials.get("aluminium")["processes"][0]
    assert p["position"] == [0, 0, 0] and p["quantity"] == 1


def test_mass_and_cost_from_geometry():
    d = normalise({"parts": [
        {"id": "s", "shape": "box", "size": [100, 100, 10], "material": "steel"},
        {"id": "t", "shape": "tube", "radius": 10, "inner_radius": 8, "length": 100,
         "material": "copper", "quantity": 2, "position": [0, 0, 20]}]})
    s = summary(d)
    steel = 100 * 100 * 10 * 1e-9 * 7850 * 1e3
    copper = math.pi * (10 ** 2 - 8 ** 2) * 100 * 1e-9 * 8960 * 1e3 * 2
    assert s["parts"][0]["mass_g"] == pytest.approx(steel)
    assert s["parts"][1]["mass_g"] == pytest.approx(copper)
    assert s["material_cost"] == pytest.approx(steel / 1e3 * 1.5 + copper / 1e3 * 10)


def test_geometry_finds_floating_and_buried_parts():
    d = normalise({"parts": [
        {"id": "a", "shape": "box", "size": [10, 10, 10]},
        {"id": "b", "shape": "box", "size": [10, 10, 10], "position": [2, 0, 0]},
        {"id": "c", "shape": "box", "size": [10, 10, 10], "position": [100, 0, 0]}]})
    text = " ".join(i["message"] for i in geometry_issues(d))
    assert "occupy the same space" in text
    assert "'c' is not attached" in text
    # a part inside a hollow shell is housed, not buried
    d = normalise({"parts": [
        {"id": "shell", "shape": "box", "size": [50, 50, 50], "wall": 2},
        {"id": "board", "shape": "box", "size": [30, 30, 2]}],
        "scenarios": [{"type": "fire", "part": "shell"}]})
    assert geometry_issues(d) == []


def test_rotation_moves_the_bounding_box():
    d = normalise({"parts": [{"id": "a", "shape": "box", "size": [100, 10, 10],
                              "rotation": [0, 0, 90]}]})
    assert summary(d)["size_mm"] == pytest.approx([10, 100, 10])


# -- real-world tests -------------------------------------------------------------
def test_load_matches_hand_calculation():
    d = bar(type="load", force="5 N")
    t = run_scenario(d, d["scenarios"][0], verify=True, detail=True)
    assert t["status"] in ("pass", "fail") and t["checks"]["verdict"] == "VERIFIED"
    L, b, h, F = 0.150, 0.020, 0.004, 5.0
    w = 1240 * G0 * b * h                                   # self-weight per length
    sigma = 6 * (F * L + w * L ** 2 / 2) / (b * h ** 2)
    sf = next(m for m in t["metrics"] if m["label"] == "safety factor")
    assert sf["value"] == pytest.approx(50e6 / sigma, rel=1e-3)


def test_round_section_keeps_stiffness_and_outer_fibre():
    part = normalise({"parts": [{"id": "r", "shape": "cylinder", "radius": 5,
                                 "length": 200, "axis": "x"}]})["parts"][0]
    length, width, height, _ = section(part, {})
    assert length == pytest.approx(0.2) and height == pytest.approx(0.010)
    assert width * height ** 3 / 12 == pytest.approx(math.pi * 0.010 ** 4 / 64)


def test_wrong_unit_is_an_error_not_a_crash():
    t = one(bar(type="load", force="5 m"))
    assert t["status"] == "error" and "force" in t["headline"]


def test_insulator_cannot_carry_current():
    t = one(bar(type="current", current="2 A"))
    assert t["status"] == "error" and "insulator" in t["headline"]


def test_scenario_on_the_wrong_shape_is_an_error():
    assert one(bar(type="flow", flow_rate="1 L/min"))["status"] == "error"
    assert one(bar(type="teleport"))["status"] == "error"


def test_unverified_simulation_is_never_a_pass(monkeypatch):
    from create_anything import scenarios
    real = scenarios._run

    def broken(name, params, verify):
        res = real(name, params, verify)
        res.report.checks[0].status = "fail"     # the simulation fails a self-check
        return res
    monkeypatch.setattr(scenarios, "_run", broken)
    t = one(bar(type="load", force="0.01 N"))
    assert t["status"] == "unverified" and "not trusted" in t["notes"][-1]


@pytest.mark.parametrize("name", ca.examples())
def test_every_test_type_in_the_examples_runs_verified(name):
    report = ca.check(ca.load(ca.example_path(name)))
    for t in report["tests"]["tests"]:
        assert t["status"] in ("pass", "fail"), t["headline"]
        assert t["checks"]["verdict"] == "VERIFIED"


@pytest.mark.parametrize("scenario, part", [
    ({"type": "vibration", "excitation": "20 Hz"}, "bar"),
    ({"type": "drop", "height": "0.5 m", "mass": "0.1 kg"}, "bar"),
    ({"type": "heat", "hot_temperature": "120 degC"}, "rod"),
    ({"type": "current", "current": "15 A", "voltage": "12 V"}, "rod"),
    ({"type": "flow", "flow_rate": "3 L/min"}, "pipe"),
    ({"type": "fire", "duration": "20 s"}, "bar"),
])
def test_each_test_type_runs_on_a_suitable_part(scenario, part):
    d = normalise({"parts": [
        {"id": "bar", "shape": "box", "size": [150, 20, 4], "material": "pla"},
        {"id": "rod", "shape": "cylinder", "radius": 3, "length": 100, "axis": "x",
         "material": "copper", "position": [0, 0, 5]},
        {"id": "pipe", "shape": "tube", "radius": 6, "inner_radius": 5, "length": 300,
         "axis": "x", "material": "copper", "position": [0, 0, 13]}],
        "scenarios": [{"part": part, **scenario}]})
    t = one(d)
    assert t["status"] in ("pass", "fail"), t["headline"] + str(t["notes"])
    assert t["metrics"] and t["headline"]


# -- optimiser -------------------------------------------------------------------
def test_optimiser_fixes_the_bracket_on_verified_runs():
    d = ca.load(ca.example_path("shelf_bracket"))
    out = optimise(d)
    assert not ca.check(d)["tests"]["all_pass"]
    assert out["all_pass"] and out["after"]["all_pass"]
    assert all(t["checks"]["verified"] for t in out["after"]["tests"])
    for pid in ("screw_top", "screw_bottom", "wall_plate"):     # locked or untested
        before = next(p for p in d["parts"] if p["id"] == pid)
        after = next(p for p in out["design"]["parts"] if p["id"] == pid)
        assert before == after
    arm = next(p for p in out["design"]["parts"] if p["id"] == "arm")
    assert arm["process"] in materials.get(arm["material"])["processes"]


def test_optimiser_trims_but_not_below_what_the_process_can_make():
    d = ca.load(ca.example_path("sensor_enclosure"))
    out = optimise(d)
    assert out["all_pass"] and out["mass_g"][1] < out["mass_g"][0]
    shell = next(p for p in out["design"]["parts"] if p["id"] == "shell")
    assert shell["wall"] >= MIN_FEATURE[shell["process"]]


def test_locked_part_is_never_changed_and_failure_is_explained():
    d = bar(type="load", force="500 N")
    d["parts"][0]["locked"] = True
    out = optimise(d)
    assert not out["all_pass"] and not out["steps"]
    assert out["design"]["parts"] == d["parts"]
    assert "different design idea" in out["note"]


# -- apps -----------------------------------------------------------------------
def test_cli_exit_codes(tmp_path, capsys):
    path = str(ca.example_path("shelf_bracket"))
    assert cli.main(["check", path]) == 1                   # fails as drawn
    out = tmp_path / "fixed.json"
    assert cli.main(["optimise", path, "-o", str(out)]) == 0
    assert cli.main(["check", str(out)]) == 0
    assert cli.main(["check", str(out), "--no-verify"]) == 1   # unverified never gates green
    assert cli.main(["materials"]) == 0 and cli.main(["tests", "--json"]) == 0
    assert cli.main(["check", str(tmp_path / "missing.json")]) == 2
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"parts": []}))
    assert cli.main(["check", str(bad)]) == 2
    capsys.readouterr()


@pytest.fixture(scope="module")
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def _get(url):
    with urllib.request.urlopen(url) as r:
        return r.status, r.read()


def _post(url, body):
    req = urllib.request.Request(url, json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _wait(server, job_id):
    import time
    for _ in range(600):
        job = json.loads(_get(f"{server}/api/job/{job_id}?since=0")[1])
        if job["done"]:
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_server_pages_and_api(server):
    status, page = _get(server + "/")
    assert status == 200 and b"importmap" in page
    assert _get(server + "/vendor/OrbitControls.js")[0] == 200
    info = json.loads(_get(server + "/api/info")[1])
    assert set(info["examples"]) == set(ca.examples()) and info["materials"]
    design = json.loads(_get(server + "/api/example/led_heatsink")[1])
    status, rep = _post(server + "/api/check", {"design": design, "detail": True})
    assert status == 200 and rep["tests"]["tests"][0]["sim"]["name"] == "heat_sink_fin"
    status, started = _post(server + "/api/optimise", {"design": design})
    assert status == 200
    job = _wait(server, started["job"])
    assert job["error"] is None and job["result"]["all_pass"]
    assert _post(server + "/api/check", {"design": {"parts": []}})[0] == 400


@pytest.mark.parametrize("path", ["/vendor/../server.py", "/../create_anything/server.py",
                                  "/vendor/THREE-LICENSE.txt", "/api/example/..%2Fserver"])
def test_server_serves_only_listed_files(server, path):
    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(server + path)
    assert exc.value.code == 404


# -- agent ------------------------------------------------------------------------
def _response(blocks, stop):
    return SimpleNamespace(content=blocks, stop_reason=stop)


def test_designer_loop_runs_the_tools_and_reports_the_checked_design():
    design = json.loads(ca.example_path("led_heatsink").read_text())
    script = [
        _response([SimpleNamespace(type="tool_use", id="1", name="list_materials", input={}),
                   SimpleNamespace(type="tool_use", id="2", name="check_design",
                                   input={"design": {"parts": []}})], "tool_use"),
        _response([SimpleNamespace(type="tool_use", id="3", name="optimise_design",
                                   input={"design": design})], "tool_use"),
        _response([SimpleNamespace(type="tool_use", id="4", name="check_design",
                                   input={"design": design})], "tool_use"),
        _response([SimpleNamespace(type="text", text="Done.")], "end_turn"),
    ]
    seen = []

    def create(**kw):
        seen.append(copy.deepcopy([m["content"] for m in kw["messages"] if m["role"] == "user"]))
        return script[len(seen) - 1]
    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
    res = Designer(client=client).create("a heat sink for a 15 W LED")
    assert res.text == "Done." and res.calls == ["list_materials", "check_design",
                                                 "optimise_design", "check_design"]
    first = seen[1][-1]
    assert first[1]["is_error"] and "at least one part" in first[1]["content"]
    assert res.design["name"] == "LED lamp heat sink" and res.report is not None
    assert res.all_pass is False      # the last check was on the unoptimised design


# -- any simulation as a test -------------------------------------------------------
def spreader(**over):
    sc = {"id": "chip", "type": "simulation", "part": "plate", "simulation": "pcb_thermal",
          "params": {"h": "10 W/(m^2*K)", "chips": [
              {"name": "LED", "x": "30 mm", "y": "20 mm", "w": "10 mm", "h": "10 mm",
               "power": "4 W"}]},
          "bind": {"board_length": "size.x", "board_width": "size.y", "thickness": "size.z",
                   "conductivity": "material.conductivity"},
          "require": [{"qoi": "max_temperature", "max": "70 degC"}]}
    sc.update(over)
    return normalise({"name": "spreader", "parts": [
        {"id": "plate", "name": "Spreader", "shape": "box", "size": [60, 40, 1],
         "material": "aluminium"}], "scenarios": [sc]})


def test_any_simulation_bound_to_the_part_runs_and_is_judged():
    t = one(spreader())
    assert t["status"] == "fail" and t["checks"]["verdict"] in ("VERIFIED", "PASSED WITH WARNINGS")
    m = t["metrics"][0]
    assert m["unit"] == "degC" and m["limit"] == pytest.approx(70) and m["value"] > 70
    assert "board_length = size.x" in t["notes"][0]
    # copper conducts better than aluminium, so the same plate runs cooler
    d = spreader()
    d["parts"][0]["material"] = "copper"
    assert one(d)["metrics"][0]["value"] < m["value"]


@pytest.mark.parametrize("over, message", [
    ({"bind": {}}, "must bind at least one parameter"),
    ({"simulation": "warp_drive"}, "unknown simulation"),
    ({"bind": {"nonsense": "size.0"}}, "has no parameter 'nonsense'"),
    ({"bind": {"thickness": "size.9"}}, "index must be 0-2"),
    ({"bind": {"thickness": "colour"}}, "unknown binding source"),
    ({"bind": {"conductivity": "material.flavour"}}, "unknown material property"),
    ({"require": [{"qoi": "max_temperature", "max": "70 m"}]}, "dimension"),
    ({"require": [{"qoi": "max_temperature", "max": 70}]}, "give a unit"),
    ({"require": [{"qoi": "happiness", "max": "1"}]}, "reports no result 'happiness'"),
    ({"require": [{"qoi": "max_temperature", "min": "1 K", "max": "2 K"}]}, "exactly one"),
    ({"params": {"h": "10 W/(m^2*K)"}}, "rejected this setup"),    # default chips off the board
])
def test_bad_simulation_tests_are_errors_the_model_can_read(over, message):
    t = one(spreader(**over))
    assert t["status"] == "error" and message in t["headline"], t["headline"]


def test_bare_numbers_need_units():
    t = one(bar(type="heat", hot_temperature=120))
    assert t["status"] == "error" and "give a unit" in t["headline"]


def test_optimiser_resizes_bound_dimensions():
    out = optimise(spreader())
    assert out["all_pass"]
    labels = " ".join(s["change"] for s in out["steps"])
    assert "size" in labels or "Copper" in labels
    assert out["after"]["tests"][0]["metrics"][0]["value"] <= 70


def test_a_crashing_simulation_is_an_error_not_a_crash(monkeypatch):
    from create_anything import scenarios

    def boom(*a, **k):
        raise FloatingPointError("singular matrix")
    monkeypatch.setattr(scenarios, "_run", boom)
    t = one(bar(type="load", force="5 N"))
    assert t["status"] == "error" and "crashed" in t["headline"]


# -- extrusions -----------------------------------------------------------------------
def test_rectangle_extrusion_matches_a_box():
    d = normalise({"parts": [
        {"id": "e", "shape": "extrusion", "profile": [[0, 0], [20, 0], [20, 4], [0, 4]],
         "length": 150, "axis": "x", "material": "pla"},
        {"id": "b", "shape": "box", "size": [150, 20, 4], "material": "pla",
         "position": [0, 0, 10]}],
        "scenarios": [{"id": "le", "type": "load", "part": "e", "force": "5 N"},
                      {"id": "lb", "type": "load", "part": "b", "force": "5 N"}]})
    s = summary(d)
    assert s["parts"][0]["size_mm"] == s["parts"][1]["size_mm"] == [150, 20, 4]
    assert s["parts"][0]["mass_g"] == pytest.approx(s["parts"][1]["mass_g"])
    e, b = evaluate(d, detail=False)["tests"]
    for m_e, m_b in zip(e["metrics"], b["metrics"]):
        assert m_e["value"] == pytest.approx(m_b["value"], rel=1e-6)


def test_angle_profile_section_and_beam():
    from create_anything.design import profile_section
    d = normalise({"parts": [{"id": "L", "shape": "extrusion", "length": 400, "axis": "x",
                              "material": "aluminium",
                              "profile": [[0, 0], [30, 0], [30, 3], [3, 3], [3, 30], [0, 30]]}],
                   "scenarios": [{"type": "load", "part": "L", "force": "300 N"}]})
    ps = profile_section(d["parts"][0])
    assert ps["area"] == pytest.approx(171)
    assert ps["I_about_u"] == pytest.approx(ps["I_about_v"])          # equal-leg angle
    assert ps["I_about_u"] == pytest.approx(14580.4, rel=1e-4)
    t = one(d)
    assert t["status"] in ("pass", "fail") and "outer-fibre" in t["notes"][0]
    out = optimise(d)
    assert out["all_pass"] and any("profile" in s["change"] for s in out["steps"])


@pytest.mark.parametrize("profile, message", [
    ([[0, 0], [1, 1]], "at least 3"),
    ([[0, 0], [1, 1], [2, 2]], "no area"),
    ([[0, 0], [1, "a"], [2, 0]], "two numbers"),
])
def test_bad_profiles_are_rejected(profile, message):
    with pytest.raises(DesignError, match=message):
        normalise({"parts": [{"shape": "extrusion", "profile": profile, "length": 10}]})


def test_extrusion_fire_test_needs_a_wall():
    d = normalise({"parts": [{"id": "e", "shape": "extrusion", "length": 50,
                              "profile": [[0, 0], [20, 0], [20, 20], [0, 20]]}],
                   "scenarios": [{"type": "fire", "part": "e"}]})
    assert "wall" in one(d)["headline"]
    d["parts"][0]["wall"] = 2.0
    assert one(d)["status"] in ("pass", "fail")


# -- jobs, submit_design --------------------------------------------------------------
def test_design_job_streams_checks_and_returns_the_submitted_design(server, monkeypatch):
    from create_anything import server as srv
    good = json.loads(ca.example_path("sensor_enclosure").read_text())
    other = json.loads(ca.example_path("led_heatsink").read_text())

    class Fake:
        def __init__(self, on_event):
            self.on_event = on_event

        def create(self, request, design=None):
            d = Designer.__new__(Designer)
            d.on_event, d.design, d.report, d.submitted = self.on_event, None, None, False
            d.tool("check_design", {"design": good})
            d.tool("submit_design", {"design": good})
            d.tool("check_design", {"design": other})       # checked after: not the answer
            from create_anything.agent import Result
            return Result("ok", d.design, d.report, "end_turn", [], {}, d.submitted)
    monkeypatch.setattr(srv, "make_designer", lambda on_event: Fake(on_event))
    monkeypatch.setattr(srv, "agent_available", lambda: True)
    status, started = _post(server + "/api/design", {"request": "an enclosure"})
    assert status == 200
    job = _wait(server, started["job"])
    assert job["error"] is None
    kinds = [e["kind"] for e in job["events"]]
    assert kinds == ["check", "submit", "check"]
    assert job["result"]["design"]["name"] == good["name"] and job["result"]["submitted"]
    later = json.loads(_get(f"{server}/api/job/{started['job']}?since=2")[1])
    assert [e["kind"] for e in later["events"]] == ["check"]


def test_design_endpoint_explains_a_missing_key(server, monkeypatch):
    from create_anything import server as srv
    monkeypatch.setattr(srv, "agent_available", lambda: False)
    status, body = _post(server + "/api/design", {"request": "a lamp"})
    assert status == 400 and "ANTHROPIC_API_KEY" in body["error"]
    with pytest.raises(urllib.error.HTTPError):
        _get(server + "/api/job/999")


def test_trimming_an_angle_stops_at_what_the_process_can_make():
    from create_anything.optimize import _thinnest
    d = normalise({"parts": [{"id": "L", "shape": "extrusion", "length": 300, "axis": "x",
                              "material": "aluminium", "process": "extrude",
                              "profile": [[0, 0], [60, 0], [60, 8], [8, 8], [8, 60], [0, 60]]}],
                   "scenarios": [{"type": "load", "part": "L", "force": "20 N"}]})
    assert _thinnest(d["parts"][0]) == pytest.approx(2 * 896 / 240)     # = 7.47 mm, not 60
    out = optimise(d)
    assert out["all_pass"] and out["mass_g"][1] < out["mass_g"][0]
    assert _thinnest(next(p for p in out["design"]["parts"])) >= MIN_FEATURE["extrude"]


def test_server_accepts_only_json_posts(server):
    req = urllib.request.Request(server + "/api/design", b'{"request": "a lamp"}',
                                 {"Content-Type": "text/plain"})
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req)
    assert exc.value.code == 415
