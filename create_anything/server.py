"""Local web server for Create Anything (standard library only).

Serves the built interface from ``web/dist`` and the API in docs/protocol.md.
Binds to localhost by default.
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import simulate_anything as sa

from . import __version__, engine, examples, materials, stl
from .agent import DEFAULT_MODEL
from .scenarios import DESCRIPTIONS

DIST = Path(__file__).parent / "web" / "dist"
MAX_BODY = 40_000_000

FALLBACK = b"""<!doctype html><meta charset="utf-8"><title>Margin</title>
<body style="font:16px system-ui;margin:3rem;max-width:40rem">
<h1>Margin</h1><p>Create anything</p><p>The interface has not been built. From the repository root run
<code>cd web && npm install && npm run build</code>, then reload. The API is running.</p>"""


def agent_available() -> bool:
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                or (Path.home() / ".config" / "anthropic").exists())


class Handler(BaseHTTPRequestHandler):
    server_version = "Margin"

    def log_message(self, fmt, *args):
        if os.environ.get("CREATE_ANYTHING_LOG"):
            super().log_message(fmt, *args)

    # -- helpers ---------------------------------------------------------------------
    def _send(self, code, payload, ctype="application/json"):
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _stream(self, events):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        try:
            for ev in events:
                self.wfile.write(b"data: " + json.dumps(ev).encode() + b"\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return            # the browser went away
        except Exception as exc:
            msg = {"type": "error", "message": f"{type(exc).__name__}: {exc}"}
            try:
                self.wfile.write(b"data: " + json.dumps(msg).encode() + b"\n\n")
            except OSError:
                pass

    def _body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length > MAX_BODY:
            raise ValueError("request too large")
        return json.loads(self.rfile.read(length) or b"{}")

    # -- routes ----------------------------------------------------------------------
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/status":
            return self._send(200, {"agent": agent_available(), "version": __version__,
                                    "model": os.environ.get("CREATE_ANYTHING_MODEL")
                                    or DEFAULT_MODEL})
        if path == "/api/examples":
            return self._send(200, examples.listing())
        if path == "/api/materials":
            return self._send(200, materials.catalog())
        if path == "/api/test-types":
            return self._send(200, DESCRIPTIONS)
        if path.startswith("/api/"):
            return self._send(404, {"error": "not found"})
        rel = path.lstrip("/") or "index.html"
        target = (DIST / rel).resolve()
        if not str(target).startswith(str(DIST.resolve())) or not target.is_file():
            target = DIST / "index.html"
        if not target.is_file():
            return self._send(200, FALLBACK, "text/html; charset=utf-8")
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype)

    def do_POST(self):
        path = self.path.split("?")[0]
        try:
            body = self._body()
        except Exception as exc:
            return self._send(400, {"error": f"bad request: {exc}"})
        try:
            if path.startswith("/api/demo/"):
                example = path.rsplit("/", 1)[-1]
                examples.get(example)                     # raises for an unknown id
                pace = float(body.get("pace", 1.0))
                return self._stream(engine.demo(example, pace=max(0.0, min(pace, 3.0))))
            if path == "/api/create":
                if not agent_available():
                    return self._send(503, {"error": "live design needs an Anthropic API key "
                                                     "(set ANTHROPIC_API_KEY and restart)"})
                from .agent import Session
                session = Session(model=os.environ.get("CREATE_ANYTHING_MODEL") or DEFAULT_MODEL)
                return self._stream(session.events(str(body.get("prompt", "")),
                                                   body.get("image"), body.get("mesh"),
                                                   body.get("design")))
            if path == "/api/evaluate":
                return self._send(200, engine.evaluate_design(body["design"]))
            if path == "/api/optimise":
                return self._stream(engine.optimise_stream(body["design"]))
            if path == "/api/import":
                data = base64.b64decode(body["data"].split(",")[-1])
                return self._send(200, {"part": stl.to_part(
                    data, body.get("name") or "Imported part", body.get("units", "mm"))})
            if path == "/api/simulate":
                res = sa.run(body["name"], body.get("params") or {}, body.get("resolution"))
                return self._send(200, res.to_dict())
            return self._send(404, {"error": "not found"})
        except Exception as exc:
            return self._send(400, {"error": f"{type(exc).__name__}: {exc}"})


def serve(host="127.0.0.1", port=8000):
    sa.load_all()
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"Margin is running at http://{host}:{port}  (Ctrl+C to stop)")
    if not (DIST / "index.html").is_file():
        print("note: the web interface is not built yet (cd web && npm install && npm run build)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
