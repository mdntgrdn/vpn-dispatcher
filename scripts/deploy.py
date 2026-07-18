#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.lib.ansible import run_playbook
from scripts.lib.forti_saml import login as forti_saml_login
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
    parser.add_argument(
        "--skip-forti",
        action="store_true",
        help="Do not run Forti SAML login after a successful deploy.",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="With Forti SAML: print the URL but do not open a browser.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="Seconds to wait for Forti SAML / forti0 (default 120).",
    )
    args = parser.parse_args(argv)

    settings = Settings()
    try:
        print("Rendering awg-in.conf on the controller...", flush=True)
        artifacts = build_awg_artifacts(settings)
        write_work_artifacts(artifacts)
        code = run_playbook(
            settings,
            "deploy.yml",
            check=args.check,
            extra_vars={
                "awg_in_conf": artifacts["awg_in_conf"],
                "awg_server_yml": artifacts["server_yml"],
                "awg_server_key": artifacts["private_key"],
            },
        )
        if code != 0 or args.check or args.skip_forti:
            return code
        if not settings.enabled("FORTI_ENABLED"):
            return code
        print("Starting Forti SAML login...", flush=True)
        return forti_saml_login(
            settings,
            open_browser=not args.no_browser,
            timeout=args.timeout,
        )
    except ValueError as exc:
        print(f"Configuration error:\n{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
