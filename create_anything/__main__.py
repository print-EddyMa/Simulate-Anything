"""Command line: ``python -m create_anything serve | demo | evaluate | optimise``."""
from __future__ import annotations

import argparse
import json
import sys

from . import engine, examples


def _print_event(ev):
    t = ev.get("type")
    if t == "status":
        print(f"\n== {ev['label']}")
    elif t == "thought":
        print(f"   {ev['text']}")
    elif t == "test_started":
        print(f"   ... {ev['title']}")
    elif t == "test_result":
        r = ev["test"]
        print(f"   [{r['status'].upper():4}] {r['title']}: {r['headline']}")
    elif t == "change":
        print(f"   -> {ev['change']}")
    elif t == "design" and ev.get("added"):
        print(f"   + {', '.join(ev['added'])}")
    elif t == "done":
        print("\n" + ev["answer"])
    elif t == "error":
        print(f"error: {ev['message']}", file=sys.stderr)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="create-anything",
                                 description="Design anything; test it against the real world.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("serve", help="start the web app")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p = sub.add_parser("demo", help="run a built-in example in the terminal")
    p.add_argument("example", choices=sorted(examples.EXAMPLES))
    p.add_argument("--fast", action="store_true", help="no pauses")
    p = sub.add_parser("evaluate", help="test a design JSON file")
    p.add_argument("file")
    p = sub.add_parser("optimise", help="optimise a design JSON file and print the result")
    p.add_argument("file")
    p.add_argument("-o", "--out", help="write the optimised design here")
    args = ap.parse_args(argv)

    if args.cmd == "serve":
        from .server import serve
        serve(args.host, args.port)
        return 0
    if args.cmd == "demo":
        last = None
        for ev in engine.demo(args.example, pace=0 if args.fast else 0.5):
            _print_event(ev)
            last = ev
        return 0 if last and last.get("verified") else 1
    with open(args.file, encoding="utf-8") as fh:
        design = json.load(fh)
    if args.cmd == "evaluate":
        out = engine.evaluate_design(design)
        for t in out["evaluation"]["tests"]:
            print(f"[{t['status'].upper():4}] {t['title']}: {t['headline']}")
        return 0 if out["evaluation"]["all_pass"] else 1
    last = None
    for ev in engine.optimise_stream(design):
        _print_event(ev)
        last = ev
    if args.out and last and last.get("type") == "done":
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(last["design"], fh, indent=1)
    return 0 if last and last.get("verified") else 1


if __name__ == "__main__":
    sys.exit(main())
