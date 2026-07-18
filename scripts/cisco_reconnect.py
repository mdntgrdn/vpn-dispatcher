#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.lib.remote import cisco_reconnect
from scripts.lib.settings import Settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cisco_reconnect")
    parser.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="Seconds to wait for cisco0 (default 600).",
    )
    args = parser.parse_args(argv)
    try:
        settings = Settings()
        settings.validate(deployment=True)
        return cisco_reconnect(settings, timeout=args.timeout)
    except ValueError as exc:
        print(f"Configuration error:\n{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
