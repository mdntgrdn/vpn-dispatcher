#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.lib.forti_saml import reconnect
from scripts.lib.settings import Settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="forti_reconnect")
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Print the SAML URL but do not open a browser.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="Seconds to wait for forti0 (default 60).",
    )
    args = parser.parse_args(argv)
    try:
        settings = Settings()
        settings.validate(deployment=True)
        return reconnect(
            settings,
            open_browser=not args.no_browser,
            timeout=args.timeout,
        )
    except ValueError as exc:
        print(f"Configuration error:\n{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
