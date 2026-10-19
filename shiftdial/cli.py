"""Command line: python -m shiftdial.cli convert FILE... --to FMT  |  tool FILE... --name ID  |  formats FILE..."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from shiftdial.engines import JobContext, apply_tool, convert
from shiftdial.registry import formats_for, tools_for


def _progress(fraction: float, stage: str) -> None:
    print(f"\r{fraction:6.1%}  {stage[:60]:<60}", end="", file=sys.stderr, flush=True)


def _parse_params(pairs: list[str]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep:
            raise SystemExit(f"error: --param needs KEY=VALUE, got {pair!r}")
        try:
            params[key] = json.loads(value)
        except json.JSONDecodeError:
            params[key] = value
    return params


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # unicode filenames on a cp1252 console
        stream.reconfigure(errors="replace")  # type: ignore[union-attr]
    ap = argparse.ArgumentParser(prog="shiftdial")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("convert", help="convert files")
    c.add_argument("files", nargs="+", type=Path)
    c.add_argument("--to", required=True, help="target format, e.g. webp, mp4, zip")
    t = sub.add_parser("tool", help="run a tool, e.g. --name video:trim --param start=1 --param end=3")
    t.add_argument("files", nargs="+", type=Path)
    t.add_argument("--name", required=True, help="tool id, see the 'formats' command")
    t.add_argument("--param", action="append", default=[], metavar="KEY=VALUE",
                   help="JSON values are parsed: box=[0,0,0.5,0.5]")
    f = sub.add_parser("formats", help="list formats and tools valid for these files")
    f.add_argument("files", nargs="+", type=Path)
    args = ap.parse_args(argv)

    files = [p.resolve() for p in args.files]
    if missing := [p for p in files if not p.exists()]:
        ap.error(f"not found: {', '.join(map(str, missing))}")
    if args.cmd == "formats":
        print("formats:", " ".join(formats_for(files)))
        print("tools:  ", " ".join(tools_for(files)) or "-")
        return 0
    try:
        if args.cmd == "tool":
            outputs = apply_tool(files, args.name, _parse_params(args.param), JobContext(_progress))
        else:
            outputs = convert(files, args.to, JobContext(_progress))
    except KeyboardInterrupt:
        return 130
    except Exception as e:  # noqa: BLE001 - report any engine failure as a CLI error
        print(f"\nerror: {e}", file=sys.stderr)
        return 1
    print(file=sys.stderr)
    for p in outputs:
        print(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
