"""Command line: ``simulate-anything list | describe | run | ask | serve``."""
from __future__ import annotations

import argparse
import json
import sys

import simulate_anything as sa


def _parse_value(text: str):
    try:
        return json.loads(text)       # numbers, booleans, lists, objects
    except json.JSONDecodeError:
        return text                   # "40 mm", "convective", ...


def _params(args) -> dict:
    params = {}
    if args.params_json:
        src = args.params_json
        if not src.lstrip().startswith("{"):
            with open(src, encoding="utf-8") as fh:
                src = fh.read()
        params.update(json.loads(src))
    for item in args.param or []:
        if "=" not in item:
            raise SystemExit(f"-p expects name=value, got '{item}'")
        key, val = item.split("=", 1)
        params[key.strip()] = _parse_value(val.strip())
    return params


def cmd_list(args) -> int:
    cat = sa.catalog()
    if args.json:
        print(json.dumps(cat, indent=1))
        return 0
    domain = None
    for s in cat:
        if s["domain"] != domain:
            domain = s["domain"]
            print(f"\n{domain.upper()}")
        print(f"  {s['name']:22} {s['title']}")
    print(f"\n{len(cat)} simulations.  `simulate-anything describe NAME` for parameters.")
    return 0


def cmd_describe(args) -> int:
    s = sa.get(args.name).schema()
    if args.json:
        print(json.dumps(s, indent=1))
        return 0
    print(f"{s['title']}  [{s['name']}, {s['domain']}]\n\n{s['description']}\n\nParameters")
    for p in s["params"]:
        default = p["default"] if isinstance(p["default"], str) else json.dumps(p["default"])
        unit = f" [{p['unit']}]" if p.get("unit") else ""
        choices = f"  choices: {p['choices']}" if p.get("choices") else ""
        print(f"  {p['name']}{unit} = {default}\n      {p['doc']}{choices}")
    print(f"\nresolution: {s['default_resolution']} ({s['resolution_meaning']})")
    return 0


def cmd_run(args) -> int:
    res = sa.run(args.name, _params(args), args.resolution, verify=not args.no_verify)
    if args.html:
        from .report import save_html
        print(f"report written to {save_html(args.html, res)}", file=sys.stderr)
    print(json.dumps(res.to_dict(with_fields=args.fields), indent=1) if args.json
          else res.to_text())
    return 0 if res.ok else 1


def cmd_ask(args) -> int:
    from .agent import Agent

    def show(kind, payload):
        if args.quiet:
            return
        if kind == "tool" and payload["name"] == "run_simulation":
            print(f"  running {payload['input'].get('name')} "
                  f"{json.dumps(payload['input'].get('params') or {})}", file=sys.stderr)
        elif kind == "tool":
            print(f"  {payload['name']} {payload['input'].get('name', '')}", file=sys.stderr)
        elif kind == "run":
            print(f"    -> {payload.report.summary()}", file=sys.stderr)

    try:
        ans = Agent(model=args.model, on_event=show).solve(args.problem)
    except RuntimeError as exc:      # SDK not installed
        raise SystemExit(str(exc)) from exc
    except Exception as exc:
        import anthropic
        if isinstance(exc, anthropic.AuthenticationError):
            raise SystemExit("no valid credentials: set ANTHROPIC_API_KEY") from exc
        if isinstance(exc, anthropic.RateLimitError):
            raise SystemExit("rate limited by the API; try again shortly") from exc
        if isinstance(exc, anthropic.APIStatusError):
            raise SystemExit(f"API error {exc.status_code}: {exc.message}") from exc
        if isinstance(exc, anthropic.APIConnectionError):
            raise SystemExit("could not reach the API (network)") from exc
        raise
    print(ans.text)
    if ans.result is not None:
        print("\n" + ans.result.to_text())
    if args.html and ans.runs:
        from .report import save_html
        print(f"report written to {save_html(args.html, ans.runs, ans.text, args.problem)}",
              file=sys.stderr)
    return 0 if ans.verified else 1


def cmd_serve(args) -> int:
    from .server import serve
    serve(args.host, args.port)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="simulate-anything",
        description="Turn a physics problem into a simulation that checks itself.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="list available simulations")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("describe", help="show a simulation's parameters")
    p.add_argument("name")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_describe)

    p = sub.add_parser("run", help="run a simulation and its self-checks")
    p.add_argument("name")
    p.add_argument("-p", "--param", action="append", metavar="NAME=VALUE",
                   help='parameter with units, e.g. -p fin_length="40 mm" (repeatable)')
    p.add_argument("--params-json", metavar="JSON|FILE", help="parameters as JSON")
    p.add_argument("-n", "--resolution", type=int, help="grid resolution")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--fields", action="store_true", help="include field data in --json")
    p.add_argument("--html", metavar="FILE", help="also write a standalone HTML report")
    p.add_argument("--no-verify", action="store_true",
                   help="skip the grid study and physics checks (faster, unverified)")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("ask", help="describe a problem in plain language (needs an API key)")
    p.add_argument("problem")
    p.add_argument("--model", default="claude-opus-5-5")
    p.add_argument("--html", metavar="FILE", help="write a standalone HTML report")
    p.add_argument("-q", "--quiet", action="store_true", help="hide progress")
    p.set_defaults(fn=cmd_ask)

    p = sub.add_parser("serve", help="start the interactive web app")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(fn=cmd_serve)

    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except (sa.ParamError, sa.DimensionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
