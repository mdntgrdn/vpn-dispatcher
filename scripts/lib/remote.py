from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from scripts.lib.settings import Settings


def ssh_base(settings: Settings) -> list[str]:
    command = [
        "ssh",
        "-p",
        settings.get("DEPLOY_PORT", "22"),
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
    ]
    key = settings.get("DEPLOY_SSH_KEY")
    if key:
        command.extend(["-i", str(Path(key).expanduser())])
    command.append(f"{settings.get('DEPLOY_USER')}@{settings.get('DEPLOY_HOST')}")
    return command


def container_name(settings: Settings) -> str:
    return settings.get("CONTAINER_NAME") or "vpn-dispatcher"


def link_ready(settings: Settings, interface: str) -> bool:
    result = subprocess.run(
        [
            *ssh_base(settings),
            "docker",
            "exec",
            container_name(settings),
            "ip",
            "link",
            "show",
            "dev",
            interface,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def vpn_kill(settings: Settings, process_name: str) -> int:
    """Stop one VPN client process inside the container (restart loop brings it back)."""
    result = subprocess.run(
        [
            *ssh_base(settings),
            "docker",
            "exec",
            container_name(settings),
            "pkill",
            "-f",
            process_name,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode not in {0, 1}:
        detail = (result.stderr or result.stdout or "").strip()
        print(
            f"Failed to stop {process_name} (exit {result.returncode}): {detail}",
            file=sys.stderr,
        )
        return 1
    if result.returncode == 1:
        print(f"{process_name} was not running; waiting for restart loop.", flush=True)
    else:
        print(f"Stopped {process_name} inside the container.", flush=True)
    return 0


def wait_link(settings: Settings, interface: str, timeout: int) -> int:
    deadline = time.monotonic() + timeout
    print(f"Waiting for {interface}...", flush=True)
    try:
        while time.monotonic() < deadline:
            if link_ready(settings, interface):
                print(f"{interface} is up.", flush=True)
                return 0
            time.sleep(2)
    except KeyboardInterrupt:
        print("\nAborted.", flush=True)
        return 130
    print(f"Timed out after {timeout}s waiting for {interface}.", file=sys.stderr)
    return 1


def cisco_reconnect(settings: Settings, *, timeout: int) -> int:
    if not settings.enabled("CISCO_ENABLED"):
        print("CISCO_ENABLED is false.", file=sys.stderr)
        return 2
    was_up = link_ready(settings, "cisco0")
    killed = vpn_kill(settings, "openconnect")
    if killed != 0:
        return killed
    print(
        "Container stays up; Cisco restart loop will respawn openconnect "
        "(~5s) with password+TOTP.",
        flush=True,
    )
    deadline = time.monotonic() + timeout
    if was_up:
        print("Waiting for cisco0 to drop...", flush=True)
        while time.monotonic() < deadline:
            if not link_ready(settings, "cisco0"):
                break
            time.sleep(1)
        else:
            print("Timed out waiting for cisco0 to drop.", file=sys.stderr)
            return 1
    remaining = max(1, int(deadline - time.monotonic()))
    return wait_link(settings, "cisco0", remaining)
