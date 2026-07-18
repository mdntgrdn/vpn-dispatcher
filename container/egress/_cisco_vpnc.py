#!/usr/bin/env python3
from __future__ import annotations

import ipaddress
import os
import subprocess


def call(*args: str) -> None:
    subprocess.run(args, check=True)


reason = os.environ.get("reason", "")
interface = os.environ.get("TUNDEV", "cisco0")

if reason in {"connect", "reconnect"}:
    address = os.environ.get("INTERNAL_IP4_ADDRESS", "")
    netmask = os.environ.get("INTERNAL_IP4_NETMASK", "255.255.255.255")
    if address:
        prefix = ipaddress.IPv4Network(f"0.0.0.0/{netmask}").prefixlen
        subprocess.run(["ip", "address", "flush", "dev", interface], check=False)
        call("ip", "address", "add", f"{address}/{prefix}", "dev", interface)
    call(
        "ip",
        "link",
        "set",
        "dev",
        interface,
        "mtu",
        os.environ.get("CISCO_MTU") or "1350",
        "up",
    )
elif reason == "disconnect":
    subprocess.run(["ip", "link", "set", "dev", interface, "down"], check=False)
