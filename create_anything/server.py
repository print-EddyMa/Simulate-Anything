"""Local web app: describe a thing, watch it get designed and tested, refine it.

Pure standard library.  Binds to localhost by default.  The API key is read from
the server's environment only; the page never sees it.  Static files are served
from a fixed list, never by joining a URL path onto a directory.

The designer and the optimiser take seconds to minutes, so they run as
background jobs: POST returns a job id, and ``GET /api/job/<id>?since=n``
returns the events since ``n`` (each design check, tool call and note) plus the
result once it is done.
"""
from __future__ import annotations

import itertools
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import simulate_anything as sa
from simulate_anything.server import agent_available

from . import __version__, example_path, examples, materials
from .design import geometry_issues, normalise, summary
from .optimize import optimise
from .scenarios import DESCRIPTIONS, evaluate

WEB = Path(__file__).parent / "web"
STATIC = {
    "/": (WEB / "index.html", "text/html; charset=utf-8"),
    "/index.html": (WEB / "index.html", "text/html; charset=utf-8"),
    "/vendor/three.module.min.js": (WEB / "vendor" / "three.module.min.js",
                                    "text/javascript; charset=utf-8"),
    "/vendor/OrbitControls.js": (WEB / "vendor" / "OrbitControls.js",
                                 "text/javascript; charset=utf-8"),
}
MAX_BODY = 2_000_000
MAX_RUNNING, KEEP = 2, 20


def report(design: dict, detail: bool = False) -> dict:
    d = normalise(design)
    return {"design": d, "summary": summary(d), "issues": geometry_issues(d),
            "tests": evaluate(d, verify=True, detail=detail)}


def api_check(body: dict) -> dict:
    return report(body.get("design"), detail=bool(body.get("detail", False)))


# -- background jobs ----------------------------------------------------------------
_jobs: dict = {}
_lock = threading.Lock()
_ids = itertools.count(1)


class Job:
    def __init__(self, kind: str):
        self.id, self.kind, self.started = str(next(_ids)), kind, time.time()
        self.events, self.done, self.result, self.error = [], False, None, None

    def emit(self, kind: str, **payload):
        with _lock:
            self.events.append({"kind": kind, "t": round(time.time() - self.started, 1),
                                **payload})

    def view(self, since: int) -> dict:
        with _lock:
            return {"id": self.id, "kind": self.kind, "events": self.events[since:],
                    "next": len(self.events), "done": self.done, "result": self.result,
                    "error": self.error}


def start_job(kind: str, work) -> dict:
    with _lock:
        if sum(not j.done for j in _jobs.values()) >= MAX_RUNNING:
            raise RuntimeError(f"{MAX_RUNNING} jobs are already running; wait for one")
        job = Job(kind)
        _jobs[job.id] = job
        for old in sorted(_jobs, key=int)[:-KEEP]:
            if _jobs[old].done:
                del _jobs[old]

    def run():
        try:
            result = work(job)
            with _lock:
                job.result = result
        except Exception as exc:                # reported to the page, never swallowed
            with _lock:
                job.error = f"{type(exc).__name__}: {exc}"
        finally:
            with _lock:
                job.done = True

    threading.Thread(target=run, daemon=True).start()
    return {"job": job.id}


def api_job(job_id: str, since: int) -> dict:
    with _lock:
        job = _jobs.get(job_id)
    if job is None:
        raise KeyError(f"no job '{job_id}'")
    return job.view(since)


def api_optimise(body: dict) -> dict:
    design = normalise(body.get("design"))

    def work(job):
        job.emit("status", text="running every test, then trying changes and re-testing each")
        out = optimise(design, max_rounds=int(body.get("rounds", 12)),
                       slim=body.get("trim", True))
        d = out["design"]
        out.update(summary=summary(d), all_issues=geometry_issues(d))
        return out
    return start_job("optimise", work)


def make_designer(on_event):
    from .agent import Designer
    return Designer(model=os.environ.get("CREATE_ANYTHING_MODEL") or "claude-opus-5-5",
                    on_event=on_event)


def api_design(body: dict) -> dict:
    request = str(body.get("request", "")).strip()
    if not request:
        raise ValueError("describe the thing you want to make")
    if not agent_available():
        raise RuntimeError("designing from a description needs the anthropic package and "
                           "ANTHROPIC_API_KEY set where the server runs")
    current = normalise(body["design"]) if body.get("design") else None

    def work(job):
        def on_event(kind, payload):
            if kind == "tool":
                job.emit("tool", name=payload["name"],
                         detail=str(payload["input"].get("name", "")))
            elif kind in ("check", "submit"):
                job.emit(kind, report=payload)
            elif kind == "optimise":
                job.emit("optimise", all_pass=payload["all_pass"],
                         changes=[s["change"] for s in payload["steps"]])
            elif kind == "text":
                job.emit("text", text=payload)
            elif kind == "tool_error":
                job.emit("tool_error", name=payload["name"], error=payload["error"])
        res = make_designer(on_event).create(request, design=current)
        out = {"answer": res.text, "all_pass": res.all_pass, "stop_reason": res.stop_reason,
               "usage": res.usage, "submitted": res.submitted}
        if res.design is not None:
            out.update(report(res.design))
        return out
    return start_job("design", work)


def api_example(name: str) -> dict:
    with open(example_path(name), encoding="utf-8") as fh:
        return normalise(json.load(fh))


class Handler(BaseHTTPRequestHandler):
    server_version = "CreateAnything"

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
        path, _, query = self.path.partition("?")
        if path in STATIC:
            file, ctype = STATIC[path]
            return self._send(200, file.read_bytes(), ctype)
        if path == "/api/info":
            return self._send(200, {"materials": materials.catalog(), "tests": DESCRIPTIONS,
                                    "examples": examples(), "agent": agent_available(),
                                    "version": __version__})
        if path.startswith("/api/job/"):
            try:
                since = int(query.partition("since=")[2] or 0)
                return self._send(200, api_job(path.rsplit("/", 1)[1], since))
            except (KeyError, ValueError) as exc:
                return self._send(404, {"error": str(exc.args[0] if exc.args else exc)})
        if path.startswith("/api/example/"):
            try:
                return self._send(200, api_example(path.rsplit("/", 1)[1]))
            except KeyError as exc:
                return self._send(404, {"error": str(exc.args[0])})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        routes = {"/api/check": api_check, "/api/optimise": api_optimise,
                  "/api/design": api_design}
        fn = routes.get(self.path.split("?")[0])
        if fn is None:
            return self._send(404, {"error": "not found"})
        # JSON only: a form or text/plain POST from another site could otherwise start
        # a paid design run without a CORS preflight
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            return self._send(415, {"error": "send application/json"})
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length > MAX_BODY:
                return self._send(413, {"error": "request too large"})
            body = json.loads(self.rfile.read(length) or b"{}")
            self._send(200, fn(body))
        except Exception as exc:
            self._send(400, {"error": f"{type(exc).__name__}: {exc}"})


def serve(host: str = "127.0.0.1", port: int = 8001) -> None:
    sa.load_all()
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"Create Anything is running at http://{host}:{port}  (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
