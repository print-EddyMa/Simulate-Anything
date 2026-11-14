"""Local web app: pick a simulation, drag a slider, watch the checks re-run.

Pure standard library.  Binds to localhost by default.
"""
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import simulate_anything as sa

PAGE = Path(__file__).parent / "web" / "index.html"
MAX_BODY = 1_000_000


def agent_available() -> bool:
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                or (Path.home() / ".config" / "anthropic").exists())


def api_run(body: dict) -> dict:
    res = sa.run(body["name"], body.get("params") or {}, body.get("resolution"))
    return res.to_dict()


def api_ask(body: dict) -> dict:
    from .agent import Agent
    problem = str(body.get("problem", "")).strip()
    if not problem:
        raise ValueError("describe the problem to simulate")
    ans = Agent(model=body.get("model") or os.environ.get("SIMULATE_ANYTHING_MODEL")
                or "claude-opus-5-5").solve(problem)
    return {"answer": ans.text, "verified": ans.verified, "problem": problem,
            "results": [r.to_dict() for r in ans.runs]}


class Handler(BaseHTTPRequestHandler):
    server_version = "SimulateAnything"

    def log_message(self, fmt, *args):   # quiet unless asked
        if os.environ.get("SIMULATE_ANYTHING_LOG"):
            super().log_message(fmt, *args)

    def _send(self, code, payload, ctype="application/json"):
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/catalog":
            self._send(200, {"simulations": sa.catalog(), "agent": agent_available(),
                             "version": sa.__version__})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        routes = {"/api/run": api_run, "/api/ask": api_ask}
        fn = routes.get(self.path.split("?")[0])
        if fn is None:
            return self._send(404, {"error": "not found"})
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length > MAX_BODY:
                return self._send(413, {"error": "request too large"})
            body = json.loads(self.rfile.read(length) or b"{}")
            self._send(200, fn(body))
        except Exception as exc:
            self._send(400, {"error": f"{type(exc).__name__}: {exc}"})


def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    sa.load_all()
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"Simulate Anything is running at http://{host}:{port}  (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
