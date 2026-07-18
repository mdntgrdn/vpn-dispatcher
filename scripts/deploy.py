#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.lib.ansible import run_playbook
from scripts.lib.render_awg_in import build_awg_artifacts, write_work_artifacts
from scripts.lib.settings import Settings


def main(argv: list[str] | None = None) -> int:
    prog = Path(sys.argv[0]).stem if argv is None else "deploy"
    parser = argparse.ArgumentParser(prog=prog)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Run the deploy playbook in Ansible check mode.",
    )
    args = parser.parse_args(argv)

    settings = Settings()
    try:
        print("Rendering awg-in.conf on the controller...", flush=True)
        artifacts = build_awg_artifacts(settings)
        write_work_artifacts(artifacts)
        return run_playbook(
            settings,
            "deploy.yml",
            check=args.check,
            extra_vars={
                "awg_in_conf": artifacts["awg_in_conf"],
                "awg_server_yml": artifacts["server_yml"],
                "awg_server_key": artifacts["private_key"],
            },
        )
    except ValueError as exc:
        print(f"Configuration error:\n{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
