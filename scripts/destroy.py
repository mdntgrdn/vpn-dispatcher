#!/usr/bin/env python3
"""Tear down the gateway: awg-quick down (PostDown) then compose down."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.lib.ansible import run_operate
from scripts.lib.settings import Settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="destroy",
        description=(
            "Bring down inbound AWG first (so PostDown hooks run: ip rule, "
            "SNAT, FORWARD, kill-switch), then docker compose down. "
            "Does not delete $DEPLOY_PATH or data/."
        ),
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        return run_operate(Settings(), "destroy", check=args.check)
    except ValueError as exc:
        print(f"Configuration error:\n{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
