"""The catalogue as a whole, plus the CLI, report, web server and agent loop."""
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest

import simulate_anything as sa
from simulate_anything import cli
from simulate_anything.agent import Agent
from simulate_anything.report import to_html
from simulate_anything.server import Handler

CATALOG = sa.catalog()
NAMES = [s["name"] for s in CATALOG]

# Every area the README promises must have at least one working template.
DOMAINS = {"electronics", "mechanical", "fluids", "electromagnetics", "aerospace",
           "bio & medicine", "climate & energy", "chemistry", "education"}


def test_every_promised_domain_is_covered():
    assert DOMAINS <= {s["domain"] for s in CATALOG}


@pytest.mark.parametrize("schema", CATALOG, ids=NAMES)
def test_schema_is_documented(schema):
    assert schema["title"] and schema["description"] and schema["domain"]
    for p in schema["params"]:
        assert p["doc"], f"{schema['name']}.{p['name']} has no description"
    json.dumps(schema)


@pytest.mark.parametrize("name", NAMES)
def test_default_setup_passes_its_own_checks(name):
    res = sa.run(name)
    assert res.ok, res.to_text()
    assert res.qoi, "no results reported"
    cats = {c.category for c in res.report.checks}
    assert {"every simulation", "math sanity"} <= cats
    names = " | ".join(c.name for c in res.report.checks)
    for item in ("dimensional analysis", "mesh convergence", "known solution", "residual"):
        assert item in names, f"{name}: no '{item}' entry in the report"
    json.dumps(res.to_dict())


def test_readme_lists_every_simulation():
    from pathlib import Path
    readme = (Path(__file__).parent.parent / "README.md").read_text(encoding="utf-8")
    missing = [n for n in NAMES if f"`{n}`" not in readme]
    assert not missing, f"README does not mention {missing}"


def test_cli_run_and_list(capsys):
    assert cli.main(["run", "heat_sink_fin", "-p", "fin_length=40 mm", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["verification"]["ok"] and out["params"]["fin_length"] == "40 mm"
    assert cli.main(["run", "heat_sink_fin", "-p", "h=5 m"]) == 1      # bad units
    capsys.readouterr()
    assert cli.main(["list"]) == 0
    assert "heat_sink_fin" in capsys.readouterr().out
    assert cli.main(["describe", "pcb_thermal"]) == 0


def test_html_report_embeds_the_result():
    page = to_html(sa.run("heat_sink_fin"), answer="about 4 W per fin")
    assert "window.__STATIC__" in page and "heat_per_fin" in page
    assert "about 4 W per fin" in page


def test_web_api_roundtrip():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        cat = json.load(urllib.request.urlopen(base + "/api/catalog"))
        assert len(cat["simulations"]) == len(NAMES)
        assert b"Simulate Anything" in urllib.request.urlopen(base + "/").read()
        req = urllib.request.Request(
            base + "/api/run", method="POST",
            data=json.dumps({"name": "heat_sink_fin", "params": {"h": "40 W/(m^2*K)"}}).encode())
        res = json.load(urllib.request.urlopen(req))
        assert res["verification"]["verdict"] == "VERIFIED" and res["fields"]
    finally:
        httpd.shutdown()
        httpd.server_close()


class _FakeMessages:
    """Scripted model: first a bad-unit run, then a corrected one, then an answer."""

    def __init__(self):
        self.calls = []
        self.script = [
            ("tool_use", [("run_simulation", {"name": "heat_sink_fin",
                                              "params": {"h": "30 W/(m*K)"}})]),
            ("tool_use", [("run_simulation", {"name": "heat_sink_fin",
                                              "params": {"h": "30 W/(m^2*K)"}})]),
            ("end_turn", "Each fin removes about 4.9 W."),
        ]

    def create(self, **kwargs):
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})   # snapshot
        reason, payload = self.script[len(self.calls) - 1]
        if reason == "end_turn":
            content = [SimpleNamespace(type="text", text=payload)]
        else:
            content = [SimpleNamespace(type="tool_use", id=f"t{len(self.calls)}{i}",
                                       name=n, input=a) for i, (n, a) in enumerate(payload)]
        return SimpleNamespace(content=content, stop_reason=reason)


def test_agent_loop_feeds_failed_checks_back_to_the_model():
    fake = _FakeMessages()
    client = SimpleNamespace(beta=SimpleNamespace(messages=fake))
    ans = Agent(client=client).solve("How much heat does one fin remove at h = 30?")
    assert len(ans.runs) == 2 and not ans.runs[0].ok and ans.runs[1].ok
    assert ans.verified and "4.9 W" in ans.text
    # the failed dimensional check went back to the model as the tool result
    first_result = fake.calls[1]["messages"][-1]["content"][0]["content"]
    assert "dimensional analysis" in first_result and '"ok": false' in first_result
    assert fake.calls[0]["model"] == "claude-opus-5-5"
