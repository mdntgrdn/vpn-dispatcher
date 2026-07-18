from __future__ import annotations

import signal
import subprocess
import sys
import threading
from pathlib import Path

from container.egress.base import EgressPlugin
from container.egress.registry import plugins
from container.network import (
    XRAY_CONFIG,
    attach_tun_routing,
    cleanup_network,
    initialize_network,
    set_tunnel_route,
)

STOP = threading.Event()
PROCESSES: set[subprocess.Popen] = set()
PROCESSES_LOCK = threading.Lock()


def _track(process: subprocess.Popen) -> None:
    with PROCESSES_LOCK:
        PROCESSES.add(process)


def _untrack(process: subprocess.Popen) -> None:
    with PROCESSES_LOCK:
        PROCESSES.discard(process)


def _vpn_worker(plugin: EgressPlugin) -> None:
    # Forti SAML: a couple of starts (~1 min with the 30s pause), then stop.
    max_attempts = 2 if plugin.interactive_auth else None
    attempt = 0
    while not STOP.is_set():
        attempt += 1
        delay = 30 if plugin.interactive_auth else 5
        try:
            process_config = plugin.command()
            if process_config is None:
                return
            command, stdin = process_config
            print(f"Starting {plugin.display_name} client", flush=True)
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE if stdin is not None else None,
                text=True,
            )
            _track(process)
            if stdin is not None and process.stdin is not None:
                process.stdin.write(stdin)
                process.stdin.close()
            return_code = process.wait()
            _untrack(process)
            if STOP.is_set():
                return
            if max_attempts is not None and attempt >= max_attempts:
                print(
                    f"{plugin.display_name} exited with {return_code}; "
                    f"giving up after {max_attempts} attempts "
                    "(run forti_reconnect)",
                    flush=True,
                )
                return
            print(
                f"{plugin.display_name} exited with {return_code}; "
                f"retrying in {delay} seconds",
                flush=True,
            )
        except Exception as exc:
            if STOP.is_set():
                return
            if max_attempts is not None and attempt >= max_attempts:
                print(
                    f"{plugin.display_name} start failed: {exc}; "
                    f"giving up after {max_attempts} attempts "
                    "(run forti_reconnect)",
                    file=sys.stderr,
                    flush=True,
                )
                return
            print(
                f"{plugin.display_name} start failed: {exc}; "
                f"retrying in {delay} seconds",
                file=sys.stderr,
                flush=True,
            )
        STOP.wait(delay)


def _route_monitor() -> None:
    states: dict[str, bool] = {}
    while not STOP.is_set():
        for plugin in plugins(enabled_only=True):
            if not plugin.owns_policy_routing:
                continue
            present = plugin.link_exists()
            if states.get(plugin.interface) != present:
                set_tunnel_route(
                    plugin.table(),
                    plugin.interface if present else None,
                )
                states[plugin.interface] = present
        STOP.wait(1)


def _stop(_signum: int, _frame) -> None:
    STOP.set()
    with PROCESSES_LOCK:
        processes = list(PROCESSES)
    for process in processes:
        process.terminate()


def main() -> int:
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    try:
        wan = initialize_network()
        print(f"Network initialized; WAN interface: {wan}", flush=True)

        test = subprocess.run(
            ["xray", "run", "-test", "-config", str(XRAY_CONFIG)],
            check=False,
        )
        if test.returncode:
            raise RuntimeError("Xray configuration validation failed")
        xray = subprocess.Popen(["xray", "run", "-config", str(XRAY_CONFIG)])
        _track(xray)
        Path("/run/vpn-dispatcher/xray.pid").write_text(str(xray.pid), encoding="utf-8")
        attach_tun_routing()

        threads = [threading.Thread(target=_route_monitor, daemon=True)]
        for plugin in plugins(enabled_only=True):
            if plugin.managed:
                threads.append(
                    threading.Thread(
                        target=_vpn_worker,
                        args=(plugin,),
                        daemon=True,
                    )
                )
        for thread in threads:
            thread.start()

        while not STOP.wait(1):
            return_code = xray.poll()
            if return_code is not None:
                raise RuntimeError(f"Xray exited unexpectedly with {return_code}")
        return 0
    except Exception as exc:
        print(f"Fatal gateway error: {exc}", file=sys.stderr, flush=True)
        return 1
    finally:
        _stop(signal.SIGTERM, None)
        cleanup_network()


if __name__ == "__main__":
    raise SystemExit(main())
