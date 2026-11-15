"""Command line: ``create-anything materials | tests | check | optimise | design | serve``.

``check`` and ``optimise`` exit non-zero unless every test passes on verified
simulations, so a design file can gate a script or CI job.
"""
from __future__ import annotations

import json
import sys

from . import load, materials
from .design import DesignError, geometry_issues, summary
from .optimize import optimise
from .scenarios import DESCRIPTIONS, evaluate

MARK = {"pass": "PASS", "fail": "FAIL", "unverified": "UNVERIFIED", "error": "ERROR"}


def _print_tests(ev: dict) -> None:
    for t in ev["tests"]:
        print(f"  {MARK[t['status']]:10} {t['title']}  [{t['id']}, {t['part']}]")
        print(f"             {t['headline']}")
        for m in t["metrics"]:
            sign = ">=" if m["kind"] == "min" else "<="
            print(f"             {'ok ' if m['ok'] else 'NO '} {m['label']}: "
                  f"{m['value']:.4g} {m['unit']} (needs {sign} {m['limit']:.4g})")
        for n in t["notes"]:
            print(f"             note: {n}")
        if t.get("checks"):
            print(f"             simulation {t['simulation']}: {t['checks']['summary']}")
    c = ev["counts"]
    print(f"\n  {c['pass']} passed, {c['fail']} failed, {c['unverified']} unverified, "
          f"{c['error']} could not be run")


def _print_summary(d: dict, s: dict, issues: list) -> None:
    print(f"{d['name']}\n{d['description']}\n" if d["description"] else f"{d['name']}\n")
    print("Parts")
    for r in s["parts"]:
        size = " x ".join(f"{v:g}" for v in r["size_mm"])
        qty = f"{r['quantity']} x " if r["quantity"] > 1 else ""
        print(f"  {qty}{r['name']:24} {r['material']:24} {size} mm  "
              f"{r['mass_g']:.1f} g  ${r['cost']:.2f}  ({r['process'].replace('_', ' ')})")
    size = " x ".join(f"{v:.0f}" for v in s["size_mm"])
    print(f"  total {s['mass_g']:.1f} g, material ${s['material_cost']:.2f}, {size} mm")
    if d["assembly"]:
        print("\nAssembly")
        for i, step in enumerate(d["assembly"], 1):
            print(f"  {i}. {step['action']}")
    if issues:
        print("\nGeometry")
        for i in issues:
            print(f"  WARN  {i['message']}")


def cmd_materials(args) -> int:
    cat = materials.catalog()
    if args.json:
        print(json.dumps(cat, indent=1))
        return 0
    print(f"  {'id':14} {'name':24} {'density':>8} {'E GPa':>6} {'MPa':>5} {'k':>6} "
          f"{'safe C':>6}  processes")
    for m in cat:
        print(f"  {m['id']:14} {m['name']:24} {m['density']:8.0f} "
              f"{m['youngs_modulus'] / 1e9:6.1f} {m['strength'] / 1e6:5.0f} "
              f"{m['conductivity']:6.3g} {m['service_temp']:6.0f}  {', '.join(m['processes'])}")
    return 0


def cmd_tests(args) -> int:
    if args.json:
        print(json.dumps(DESCRIPTIONS, indent=1))
        return 0
    for name, text in DESCRIPTIONS.items():
        print(f"  {name:10} {text}\n")
    return 0


def cmd_check(args) -> int:
    d = load(args.design)
    s, issues = summary(d), geometry_issues(d)
    ev = evaluate(d, verify=not args.no_verify, detail=False)
    if args.json:
        print(json.dumps({"design": d, "summary": s, "issues": issues, "tests": ev}, indent=1))
    else:
        _print_summary(d, s, issues)
        print("\nReal-world tests")
        _print_tests(ev)
        if args.no_verify:
            print("  (unverified run: --no-verify skipped the simulations' self-checks)")
    return 0 if ev["all_pass"] and not args.no_verify else 1


def cmd_optimise(args) -> int:
    d = load(args.design)
    out = optimise(d, max_rounds=args.rounds, slim=not args.no_trim)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            json.dump(out["design"], fh, indent=2)
        print(f"optimised design written to {args.output}", file=sys.stderr)
    if args.json:
        print(json.dumps(out, indent=1))
        return 0 if out["all_pass"] else 1
    print(f"{d['name']}: {out['mass_g'][0]:.1f} g -> {out['mass_g'][1]:.1f} g\n")
    if out["steps"]:
        print("Changes")
        for s in out["steps"]:
            print(f"  {s['kind']:4}  {s['change']}")
            print(f"        {s['reason']}")
    else:
        print("No changes needed.")
    print("\nReal-world tests (final design, verified)")
    _print_tests(out["after"])
    for i in out["issues"]:
        print(f"  WARN  {i['message']}")
    if out["note"]:
        print(f"\n{out['note']}")
    return 0 if out["all_pass"] else 1


def cmd_design(args) -> int:
    from .agent import Designer

    def show(kind, payload):
        if args.quiet:
            return
        if kind == "tool":
            print(f"  {payload['name']}", file=sys.stderr)
        elif kind in ("check", "submit"):
            c = payload["tests"]["counts"]
            print(f"    -> {c['pass']} passed, {c['fail']} failed, {c['unverified']} "
                  f"unverified, {c['error']} errors", file=sys.stderr)

    try:
        res = Designer(model=args.model, on_event=show).create(args.request)
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
    print(res.text)
    if args.output and res.design:
        with open(args.output, "w", encoding="utf-8") as fh:
            json.dump(res.design, fh, indent=2)
        print(f"design written to {args.output}", file=sys.stderr)
    return 0 if res.all_pass else 1


def cmd_serve(args) -> int:
    from .server import serve
    serve(args.host, args.port)
    return 0


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        prog="create-anything",
        description="Design a physical thing and prove it survives the real world.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("materials", help="list the material library")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_materials)

    p = sub.add_parser("tests", help="list the real-world test types")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_tests)

    p = sub.add_parser("check", help="run every real-world test of a design file")
    p.add_argument("design", help="design JSON file")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-verify", action="store_true",
                   help="skip the simulations' self-checks (faster; never exits 0)")
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("optimise", aliases=["optimize"],
                       help="resize and re-test until every test passes")
    p.add_argument("design", help="design JSON file")
    p.add_argument("-o", "--output", metavar="FILE", help="write the optimised design here")
    p.add_argument("--rounds", type=int, default=12, help="maximum fix rounds (12)")
    p.add_argument("--no-trim", action="store_true", help="do not shave material back off")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_optimise)

    p = sub.add_parser("design", help="describe a thing in plain language (needs an API key)")
    p.add_argument("request")
    p.add_argument("-o", "--output", metavar="FILE", help="write the final design here")
    p.add_argument("--model", default="claude-opus-5-5")
    p.add_argument("-q", "--quiet", action="store_true", help="hide progress")
    p.set_defaults(fn=cmd_design)

    p = sub.add_parser("serve", help="start the design web app")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8001)
    p.set_defaults(fn=cmd_serve)

    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except (DesignError, json.JSONDecodeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
