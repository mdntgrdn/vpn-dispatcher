"""Render inbound awg-in.conf on the controller."""
from __future__ import annotations

import ipaddress
import json
import random
import subprocess
import tempfile
from pathlib import Path

import yaml

from scripts.lib.ansible import run_playbook
from scripts.lib.settings import ROOT, Settings

WORK_DIR = ROOT / ".work"


def _awg_parameters(settings: Settings, previous: dict) -> dict[str, str]:
    generator = random.SystemRandom()
    generated = {
        "jc": str(generator.randint(4, 8)),
        "jmin": str(generator.randint(40, 80)),
        "jmax": str(generator.randint(900, 1200)),
        "s1": str(generator.randint(15, 80)),
        "s2": str(generator.randint(15, 80)),
        "s3": str(generator.randint(15, 80)),
        "s4": str(generator.randint(15, 80)),
        "h1": str(generator.randint(5, 2**32 - 1)),
        "h2": str(generator.randint(5, 2**32 - 1)),
        "h3": str(generator.randint(5, 2**32 - 1)),
        "h4": str(generator.randint(5, 2**32 - 1)),
    }
    result: dict[str, str] = {}
    for key in generated:
        env_name = f"AWG_{key.upper()}"
        result[key] = (
            settings.get(env_name)
            or str(previous.get(key) or "").strip()
            or generated[key]
        )
    if int(result["jmin"]) > int(result["jmax"]):
        raise ValueError("AWG_JMIN must be less than or equal to AWG_JMAX")
    headers = [result[f"h{index}"] for index in range(1, 5)]
    if len(set(headers)) != 4:
        raise ValueError("AWG_H1 through AWG_H4 must be unique")
    for key in ("i1", "i2", "i3", "i4", "i5"):
        value = settings.get(f"AWG_{key.upper()}") or str(previous.get(key) or "").strip()
        if value:
            result[key] = value
    return result


def _pubkey(private_key: str) -> str:
    for command in (
        ["awg", "pubkey"],
        ["wg", "pubkey"],
    ):
        try:
            result = subprocess.run(
                command,
                input=private_key.strip() + "\n",
                text=True,
                capture_output=True,
                check=False,
            )
        except FileNotFoundError:
            continue
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    raise RuntimeError(
        "Need `awg pubkey` or `wg pubkey` on the controller to derive the "
        "server public key (or install wireguard-tools / amneziawg-tools)."
    )


def _post_hooks(settings: Settings) -> tuple[list[str], list[str]]:
    if not settings.enabled("AWG_OUT_ENABLED"):
        return [], []
    if not settings.enabled("HOST_NETWORK"):
        return [], []
    mark = settings.require("MARK_AWG")
    table = settings.require("TABLE_AWG")
    iface = settings.require("AWG_OUT_INTERFACE")
    snat = settings.require("AWG_OUT_SNAT")
    wan = settings.require("WAN_IFACE")
    post_up = [
        f"ip rule add fwmark {mark} table {table} || true",
        (
            f"iptables -t nat -C POSTROUTING -m mark --mark {mark} -o {iface} "
            f"-j SNAT --to-source {snat} 2>/dev/null || "
            f"iptables -t nat -A POSTROUTING -m mark --mark {mark} -o {iface} "
            f"-j SNAT --to-source {snat}"
        ),
        # Host FORWARD is often DROP; allow client ↔ Xray TUN.
        (
            f"iptables -C FORWARD -i awg-in -o xray0 -j ACCEPT 2>/dev/null || "
            f"iptables -I FORWARD 1 -i awg-in -o xray0 -j ACCEPT"
        ),
        (
            f"iptables -C FORWARD -i xray0 -o awg-in -j ACCEPT 2>/dev/null || "
            f"iptables -I FORWARD 1 -i xray0 -o awg-in -j ACCEPT"
        ),
        # Kill-switch: drop marked packets leaving via WAN only.
        (
            f"iptables -t mangle -C POSTROUTING -m mark --mark {mark} "
            f"-o {wan} -j DROP 2>/dev/null || "
            f"iptables -t mangle -A POSTROUTING -m mark --mark {mark} "
            f"-o {wan} -j DROP"
        ),
    ]
    post_down = [
        f"ip rule del fwmark {mark} table {table} || true",
        (
            f"iptables -t nat -D POSTROUTING -m mark --mark {mark} -o {iface} "
            f"-j SNAT --to-source {snat} || true"
        ),
        (
            f"iptables -D FORWARD -i awg-in -o xray0 -j ACCEPT || true"
        ),
        (
            f"iptables -D FORWARD -i xray0 -o awg-in -j ACCEPT || true"
        ),
        (
            f"iptables -t mangle -D POSTROUTING -m mark --mark {mark} "
            f"-o {wan} -j DROP || true"
        ),
    ]
    return post_up, post_down


def render_awg_in_conf(
    *,
    private_key: str,
    listen_port: int,
    mtu: int,
    address: str,
    parameters: dict[str, str],
    peers: list[dict],
    settings: Settings,
) -> str:
    lines = [
        "[Interface]",
        f"Address = {address}",
        f"PrivateKey = {private_key.strip()}",
        f"ListenPort = {listen_port}",
        f"MTU = {mtu}",
        "Table = off",
        "",
        f"Jc = {parameters['jc']}",
        f"Jmin = {parameters['jmin']}",
        f"Jmax = {parameters['jmax']}",
        f"S1 = {parameters['s1']}",
        f"S2 = {parameters['s2']}",
        f"S3 = {parameters['s3']}",
        f"S4 = {parameters['s4']}",
        f"H1 = {parameters['h1']}",
        f"H2 = {parameters['h2']}",
        f"H3 = {parameters['h3']}",
        f"H4 = {parameters['h4']}",
    ]
    for key in ("i1", "i2", "i3", "i4", "i5"):
        value = parameters.get(key)
        if value:
            lines.append(f"{key.upper()} = {value}")

    post_up, post_down = _post_hooks(settings)
    if post_up:
        lines.append("")
    for command in post_up:
        lines.append(f"PostUp = {command}")
    for command in post_down:
        lines.append(f"PostDown = {command}")

    for row in peers:
        if not isinstance(row, dict):
            continue
        if not row.get("public_key") or not row.get("address"):
            continue
        username = str(row.get("username") or "").strip()
        lines.append("")
        if username:
            lines.append(f"# {username}")
        lines.extend(
            [
                "[Peer]",
                f"PublicKey = {row['public_key']}",
                f"AllowedIPs = {row['address']}",
            ]
        )
    return "\n".join(lines) + "\n"


def fetch_awg_bundle(settings: Settings) -> dict:
    """Pull server.yml / clients.yml / server.key from the host (create key if needed)."""
    with tempfile.NamedTemporaryFile(
        prefix="vpn-dispatcher-awg-",
        suffix=".json",
        delete=False,
    ) as temporary:
        result_file = Path(temporary.name)
    try:
        code = run_playbook(
            settings,
            "prepare_awg.yml",
            extra_vars={"awg_result_file": str(result_file)},
        )
        if code != 0:
            raise RuntimeError(f"prepare_awg playbook failed with exit code {code}")
        payload = json.loads(result_file.read_text(encoding="utf-8"))
    finally:
        result_file.unlink(missing_ok=True)
    if not isinstance(payload, dict):
        raise RuntimeError("Invalid prepare_awg payload")
    return payload


def build_awg_artifacts(
    settings: Settings,
    *,
    clients: list[dict] | None = None,
    remote: dict | None = None,
) -> dict[str, str]:
    """Build awg-in.conf + server.yml + private key on the controller."""
    remote = remote if remote is not None else fetch_awg_bundle(settings)
    previous = remote.get("server") if isinstance(remote.get("server"), dict) else {}
    remote_clients = remote.get("clients") if isinstance(remote.get("clients"), list) else []
    peers = clients if clients is not None else remote_clients

    private_key = (
        str(remote.get("private_key") or "").strip()
        or settings.secret("AWG_SERVER_PRIVATE_KEY").strip()
    )
    if not private_key:
        raise RuntimeError(
            "No AWG server private key on the host and AWG_SERVER_PRIVATE_KEY is empty"
        )

    network = ipaddress.ip_network(settings.get("AWG_SUBNET"), strict=True)
    if not isinstance(network, ipaddress.IPv4Network):
        raise ValueError("AWG_SUBNET must be IPv4")
    server_address = next(network.hosts())
    parameters = _awg_parameters(settings, previous)
    public_key = str(remote.get("public_key") or "").strip()
    if not public_key:
        public_key = _pubkey(private_key)
    listen_port = int(settings.get("AWG_LISTEN_PORT", "51820"))
    mtu = int(settings.get("AWG_MTU", "1380"))
    state = {
        **parameters,
        "public_key": public_key,
        "public_host": settings.get("PUBLIC_HOST"),
        "listen_port": listen_port,
        "subnet": str(network),
        "dns": settings.get("AWG_DNS", "1.1.1.1"),
        "mtu": mtu,
    }
    conf = render_awg_in_conf(
        private_key=private_key,
        listen_port=listen_port,
        mtu=mtu,
        address=f"{server_address}/{network.prefixlen}",
        parameters=parameters,
        peers=[row for row in peers if isinstance(row, dict)],
        settings=settings,
    )
    return {
        "private_key": private_key.strip() + "\n",
        "server_yml": yaml.safe_dump(state, sort_keys=False),
        "awg_in_conf": conf,
    }


def write_work_artifacts(artifacts: dict[str, str]) -> Path:
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    conf_path = WORK_DIR / "awg-in.conf"
    conf_path.write_text(artifacts["awg_in_conf"], encoding="utf-8")
    conf_path.chmod(0o600)
    (WORK_DIR / "server.yml").write_text(artifacts["server_yml"], encoding="utf-8")
    key_path = WORK_DIR / "server.key"
    key_path.write_text(artifacts["private_key"], encoding="utf-8")
    key_path.chmod(0o600)
    return WORK_DIR
