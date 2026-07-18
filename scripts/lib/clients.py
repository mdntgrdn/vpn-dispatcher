"""Inbound AWG client registry (controller-side, applied via Ansible)."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import tempfile
from pathlib import Path

import yaml

from scripts.lib.ansible import run_playbook
from scripts.lib.render_awg_in import build_awg_artifacts, write_work_artifacts
from scripts.lib.settings import Settings


def parse_names(values: list[str]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for raw in values:
        for part in str(raw).split(","):
            name = part.strip()
            if not name:
                continue
            key = name.casefold()
            if key in seen:
                raise ValueError(f"Duplicate client name: {name!r}")
            seen.add(key)
            names.append(name)
    if not names:
        raise ValueError('No client names. Use -u "Name" or -u "A,B".')
    return names


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_.-]", "_", name.strip())
    slug = re.sub(r"^[_.-]+|[_.-]+$", "", slug)
    if not slug:
        slug = f"peer-{hashlib.sha256(name.encode()).hexdigest()[:8]}"
    return slug


def format_rows(rows: list[dict], fmt: str) -> str:
    payload = {"clients": rows}
    if fmt == "json":
        return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if fmt == "yaml":
        return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
    lines = [f"clients: {len(rows)}", ""]
    if not rows:
        return "\n".join(lines + ["(empty)"]) + "\n"
    header = f"{'#':<4} {'username':<24} {'address':<18} public_key"
    lines.extend([header, "-" * len(header)])
    for index, row in enumerate(rows, 1):
        lines.append(
            f"{index:<4} {str(row.get('username', ''))[:24]:<24} "
            f"{str(row.get('address', ''))[:18]:<18} {row.get('public_key', '')}"
        )
    return "\n".join(lines) + "\n"


def public_rows(rows: list[dict]) -> list[dict]:
    return [
        {
            "username": row.get("username", ""),
            "address": row.get("address", ""),
            "public_key": row.get("public_key", ""),
            "slug": row.get("slug", ""),
        }
        for row in rows
    ]


def _run_clients_playbook(settings: Settings, extra_vars: dict) -> str:
    with tempfile.NamedTemporaryFile(
        prefix="vpn-dispatcher-clients-",
        suffix=".out",
        delete=False,
    ) as temporary:
        result_file = Path(temporary.name)
    try:
        payload = {**extra_vars, "client_result_file": str(result_file)}
        code = run_playbook(settings, "clients.yml", extra_vars=payload)
        if code != 0:
            raise RuntimeError(f"Ansible clients playbook failed with exit code {code}")
        if result_file.is_file() and result_file.stat().st_size:
            return result_file.read_text(encoding="utf-8")
        return ""
    finally:
        result_file.unlink(missing_ok=True)


def _apply(settings: Settings, extra_vars: dict) -> None:
    with tempfile.NamedTemporaryFile(
        prefix="vpn-dispatcher-apply-",
        suffix=".out",
        delete=False,
    ) as temporary:
        result_file = Path(temporary.name)
    try:
        code = run_playbook(
            settings,
            "clients.yml",
            extra_vars={**extra_vars, "client_result_file": str(result_file)},
        )
        if code != 0:
            raise RuntimeError(f"Ansible clients playbook failed with exit code {code}")
    finally:
        result_file.unlink(missing_ok=True)


def fetch_state(settings: Settings | None = None) -> tuple[dict, list[dict]]:
    settings = settings or Settings()
    raw = _run_clients_playbook(settings, {"client_action": "fetch"})
    payload = json.loads(raw)
    server = payload.get("server") or {}
    clients = payload.get("clients") or []
    if not isinstance(server, dict):
        raise RuntimeError("Invalid server.yml payload from remote")
    if not isinstance(clients, list):
        raise RuntimeError("Invalid clients.yml payload from remote")
    return server, [row for row in clients if isinstance(row, dict)]


def _client_config(row: dict, state: dict) -> str:
    lines = [
        "[Interface]",
        f"PrivateKey = {row['private_key']}",
        f"Address = {row['address']}",
        f"DNS = {state['dns']}",
        f"MTU = {state['mtu']}",
        "",
        f"Jc = {state['jc']}",
        f"Jmin = {state['jmin']}",
        f"Jmax = {state['jmax']}",
        f"S1 = {state['s1']}",
        f"S2 = {state['s2']}",
        f"S3 = {state['s3']}",
        f"S4 = {state['s4']}",
        f"H1 = {state['h1']}",
        f"H2 = {state['h2']}",
        f"H3 = {state['h3']}",
        f"H4 = {state['h4']}",
    ]
    for key in ("i1", "i2", "i3", "i4", "i5"):
        value = state.get(key)
        if value:
            lines.append(f"{key.upper()} = {value}")
    lines.extend(
        [
            "",
            "[Peer]",
            f"PublicKey = {state['public_key']}",
            f"Endpoint = {state['public_host']}:{state['listen_port']}",
            "AllowedIPs = 0.0.0.0/0",
            "PersistentKeepalive = 25",
            "",
        ]
    )
    return "\n".join(lines)

def _next_address(network: ipaddress.IPv4Network, rows: list[dict]) -> str:
    used = {
        ipaddress.ip_address(str(row.get("address", "")).split("/", 1)[0])
        for row in rows
        if row.get("address")
    }
    hosts = network.hosts()
    next(hosts, None)
    for address in hosts:
        if address not in used:
            return f"{address}/32"
    raise RuntimeError(f"No free client addresses remain in {network}")


def _keygen(settings: Settings, names: list[str]) -> dict[str, tuple[str, str]]:
    raw = _run_clients_playbook(
        settings,
        {"client_action": "keygen", "client_names": names},
    )
    keys: dict[str, tuple[str, str]] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        name, private_key, public_key = line.split("\t", 2)
        keys[name] = (private_key, public_key)
    missing = [name for name in names if name not in keys]
    if missing:
        raise RuntimeError(f"Keygen missing results for: {', '.join(missing)}")
    return keys


def add_clients(
    names: list[str],
    settings: Settings | None = None,
    *,
    output_dir: Path | None = None,
) -> list[Path]:
    settings = settings or Settings()
    server, rows = fetch_state(settings)
    required = {
        "public_key",
        "public_host",
        "listen_port",
        "subnet",
        "dns",
        "mtu",
        "jc",
        "jmin",
        "jmax",
        "s1",
        "s2",
        "s3",
        "s4",
        "h1",
        "h2",
        "h3",
        "h4",
    }
    missing = sorted(required - set(server))
    if missing:
        raise RuntimeError(f"Inbound AWG state is incomplete: {', '.join(missing)}")

    network = ipaddress.ip_network(str(server["subnet"]), strict=True)
    if not isinstance(network, ipaddress.IPv4Network):
        raise RuntimeError("Only an IPv4 AWG_SUBNET is currently supported")

    existing_names = {str(row.get("username", "")).casefold() for row in rows}
    existing_slugs = {str(row.get("slug", "")) for row in rows}
    for name in names:
        if name.casefold() in existing_names:
            raise ValueError(f"Client {name!r} already exists")
        slug = slugify(name)
        if slug in existing_slugs:
            raise ValueError(f"Client slug {slug!r} already exists")

    out_dir = output_dir or (settings.env_file.parent / "clients")
    out_dir.mkdir(parents=True, exist_ok=True)

    keys = _keygen(settings, names)
    created: list[dict] = []
    conf_files: list[dict] = []
    written: list[Path] = []
    for name in names:
        private_key, public_key = keys[name]
        row = {
            "username": name,
            "slug": slugify(name),
            "address": _next_address(network, rows + created),
            "private_key": private_key,
            "public_key": public_key,
        }
        created.append(row)
        content = _client_config(row, server)
        conf_files.append({"slug": row["slug"], "content": content})
        path = out_dir / f"{row['slug']}.conf"
        path.write_text(content, encoding="utf-8")
        path.chmod(0o600)
        written.append(path)

    registry_yaml = yaml.safe_dump(
        {"clients": rows + created},
        sort_keys=False,
        allow_unicode=True,
    )
    artifacts = build_awg_artifacts(
        settings,
        clients=rows + created,
    )
    write_work_artifacts(artifacts)
    _apply(
        settings,
        {
            "client_action": "apply_add",
            "client_registry_yaml": registry_yaml,
            "client_conf_files": conf_files,
            "awg_in_conf": artifacts["awg_in_conf"],
            "client_peers": [
                {
                    "username": row["username"],
                    "public_key": row["public_key"],
                    "address": row["address"],
                    "slug": row["slug"],
                }
                for row in created
            ],
        },
    )
    return written


def remove_clients(names: list[str], settings: Settings | None = None) -> int:
    settings = settings or Settings()
    _, rows = fetch_state(settings)
    wanted = {name.casefold() for name in names}
    selected = [
        row for row in rows if str(row.get("username", "")).casefold() in wanted
    ]
    found = {str(row.get("username", "")).casefold() for row in selected}
    missing = sorted(wanted - found)
    if missing:
        raise ValueError(f"Unknown clients: {', '.join(missing)}")
    remaining = [row for row in rows if row not in selected]
    registry_yaml = yaml.safe_dump(
        {"clients": remaining},
        sort_keys=False,
        allow_unicode=True,
    )
    artifacts = build_awg_artifacts(settings, clients=remaining)
    write_work_artifacts(artifacts)
    _apply(
        settings,
        {
            "client_action": "apply_remove",
            "client_registry_yaml": registry_yaml,
            "awg_in_conf": artifacts["awg_in_conf"],
            "client_peers": [
                {
                    "username": row.get("username", ""),
                    "public_key": row.get("public_key", ""),
                    "slug": row.get("slug", ""),
                }
                for row in selected
            ],
        },
    )
    return len(selected)


def list_clients(fmt: str = "table", settings: Settings | None = None) -> str:
    _, rows = fetch_state(settings)
    return format_rows(public_rows(rows), fmt)
