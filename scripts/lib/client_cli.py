from __future__ import annotations

import argparse
import sys
from pathlib import Path

from scripts.lib.clients import add_clients, list_clients, parse_names, remove_clients
from scripts.lib.settings import ROOT, Settings


def parser_for(action: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=f"{action}_clients")
    if action in {"add", "remove"}:
        parser.add_argument(
            "-u",
            dest="u_names",
            action="append",
            default=[],
            metavar="NAMES",
            help='Client names, comma-separated; "-u" may be repeated.',
        )
    if action == "add":
        parser.add_argument(
            "-o",
            "--output-dir",
            type=Path,
            default=ROOT / "clients",
            help=f"Directory for generated .conf files (default: {ROOT / 'clients'}).",
        )
    parser.add_argument(
        "--format",
        choices=["table", "json", "yaml"],
        default="table",
    )
    return parser


def run(action: str, argv: list[str] | None = None) -> int:
    args = parser_for(action).parse_args(argv)
    try:
        settings = Settings()
        if action == "add":
            names = parse_names(args.u_names)
            paths = add_clients(names, settings, output_dir=args.output_dir)
            for path in paths:
                print(f"Wrote {path}")
            return 0
        if action == "remove":
            names = parse_names(args.u_names)
            print(f"Removed {remove_clients(names, settings)} client(s).")
            return 0
        sys.stdout.write(list_clients(args.format, settings))
        return 0
    except (ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
