from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

from container.egress.registry import plugins


def _link(name: str) -> bool:
    return Path("/sys/class/net", name).exists()


def _xray() -> bool:
    path = Path("/run/vpn-dispatcher/xray.pid")
    if not path.is_file():
        return False
    try:
        os.kill(int(path.read_text().strip()), 0)
        return True
    except (ValueError, ProcessLookupError, PermissionError):
        return False


def _route(table: str) -> bool:
    result = subprocess.run(
        ["ip", "route", "show", "table", table, "default"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0 and "unreachable" not in result.stdout


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    checks = {"xray": _xray(), "awg-in": _link("awg-in"), "xray0": _link("xray0")}
    required = {"xray", "awg-in", "xray0"}
    active_plugins = plugins(enabled_only=True)
    for plugin in active_plugins:
        if plugin.owns_policy_routing:
            healthy = plugin.link_exists() and _route(plugin.table())
        else:
            healthy = plugin.link_exists()
        checks[plugin.tag] = healthy
        if plugin.fallback or not plugin.interactive_auth:
            required.add(plugin.tag)

    if args.verbose:
        for name, healthy in checks.items():
            print(f"{name}: {'ok' if healthy else 'down'}")
        for plugin in active_plugins:
            public_key = plugin.public_key()
            if public_key:
                print(f"{plugin.interface} client public key: {public_key}")
            if plugin.interactive_auth and not checks.get(plugin.tag):
                print(
                    f"{plugin.tag}: waiting for interactive auth "
                    "(not failing healthcheck)"
                )
    return 0 if all(checks[name] for name in required) else 1


if __name__ == "__main__":
    raise SystemExit(main())
