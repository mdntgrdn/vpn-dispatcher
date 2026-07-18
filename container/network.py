from __future__ import annotations

import ipaddress
import json
import shutil
import time
from pathlib import Path

import yaml

from container.common import enabled, env, require_env, run
from container.egress.registry import plugins
from container.egress.utils import delete_link

RUN_DIR = Path("/run/vpn-dispatcher")
AWG_DIR = Path("/data/awg")
AWG_IN_CONF = AWG_DIR / "awg-in.conf"
XRAY_CONFIG = RUN_DIR / "xray.json"
# Steer inbound AWG clients into Xray TUN.
TUN_IFACE = "xray0"
TUN_TABLE = "200"
TUN_RULE_PRIORITY = "900"

def detect_wan_interface() -> str:
    override = env("WAN_IFACE")
    if override:
        if run(["ip", "link", "show", "dev", override], check=False).returncode:
            raise RuntimeError(f"WAN_IFACE={override!r} does not exist in the container")
        return override
    routes = json.loads(run(["ip", "-json", "-4", "route", "show", "default"]).stdout)
    candidates = [route for route in routes if route.get("dev")]
    if not candidates:
        raise RuntimeError("No IPv4 default route; set WAN_IFACE explicitly")
    best_metric = min(int(route.get("metric", 0)) for route in candidates)
    interfaces = {
        str(route["dev"])
        for route in candidates
        if int(route.get("metric", 0)) == best_metric
    }
    if len(interfaces) != 1:
        raise RuntimeError(
            "Default route is ambiguous; set WAN_IFACE explicitly: "
            + ", ".join(sorted(interfaces))
        )
    return interfaces.pop()


def setup_inbound_awg() -> dict:
    """Bring up controller-rendered awg-in.conf."""
    AWG_DIR.mkdir(parents=True, exist_ok=True)
    if not AWG_IN_CONF.is_file():
        raise RuntimeError(
            f"{AWG_IN_CONF} is missing. Render it on the controller and copy "
            "via Ansible (scripts.lib.render_awg_in)."
        )
    state_file = AWG_DIR / "server.yml"
    state = yaml.safe_load(state_file.read_text(encoding="utf-8")) if state_file.is_file() else {}
    if not isinstance(state, dict):
        state = {}
    run(["awg-quick", "down", str(AWG_IN_CONF)], check=False)
    run(["awg-quick", "up", str(AWG_IN_CONF)])
    return state
def _replace_rule(mark: str, table: str, priority: str) -> None:
    while (
        run(
            ["ip", "rule", "delete", "priority", priority],
            check=False,
        ).returncode
        == 0
    ):
        pass
    run(
        [
            "ip",
            "rule",
            "add",
            "priority",
            priority,
            "fwmark",
            mark,
            "table",
            table,
        ]
    )


def set_tunnel_route(table: str, interface: str | None) -> None:
    run(["ip", "route", "flush", "table", table], check=False)
    if interface and run(["ip", "link", "show", "dev", interface], check=False).returncode == 0:
        run(["ip", "route", "add", "default", "dev", interface, "table", table])
    else:
        run(["ip", "route", "add", "unreachable", "default", "table", table])


def setup_policy_routing(wan_interface: str) -> None:
    _replace_rule(require_env("MARK_DIRECT"), "main", "1000")
    active_plugins = plugins(enabled_only=True)
    for plugin in active_plugins:
        if not plugin.owns_policy_routing:
            continue
        _replace_rule(plugin.mark(), plugin.table(), plugin.priority)
        set_tunnel_route(
            plugin.table(),
            plugin.interface if plugin.link_exists() else None,
        )

    # Tunnel ifaces always masquerade. On host network, WAN is mark-scoped.
    host_network = env("HOST_NETWORK", "0").lower() in {"1", "true", "yes", "on"}
    tunnel_ifaces = [
        plugin.interface
        for plugin in active_plugins
        if plugin.masquerade and plugin.owns_interface
    ]
    if host_network:
        mark_direct = require_env("MARK_DIRECT")
        lines = [
            f"    meta mark {mark_direct} oifname {json.dumps(wan_interface)} masquerade"
        ]
        lines.extend(
            f"    oifname {json.dumps(name)} masquerade" for name in tunnel_ifaces
        )
        postrouting = "\n".join(lines)
    else:
        interfaces = [wan_interface, *tunnel_ifaces]
        joined = ", ".join(json.dumps(name) for name in interfaces)
        postrouting = f"    oifname {{ {joined} }} masquerade"

    nftables = f"""
table inet vpn_dispatcher {{
  chain postrouting {{
    type nat hook postrouting priority srcnat; policy accept;
{postrouting}
  }}
}}
"""
    nft_file = RUN_DIR / "nftables.conf"
    nft_file.write_text(nftables, encoding="utf-8")
    run(["nft", "delete", "table", "inet", "vpn_dispatcher"], check=False)
    run(["nft", "-f", str(nft_file)])


def attach_tun_routing(*, timeout: float = 15.0) -> None:
    """Wait for Xray TUN and send only awg-in traffic into it."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if Path("/sys/class/net", TUN_IFACE).exists():
            break
        time.sleep(0.1)
    else:
        raise RuntimeError(f"Xray TUN interface {TUN_IFACE!r} did not appear")

    run(["ip", "link", "set", "dev", TUN_IFACE, "up"], check=False)
    while (
        run(
            ["ip", "rule", "delete", "priority", TUN_RULE_PRIORITY],
            check=False,
        ).returncode
        == 0
    ):
        pass
    run(["ip", "route", "flush", "table", TUN_TABLE], check=False)
    run(["ip", "route", "add", "default", "dev", TUN_IFACE, "table", TUN_TABLE])
    run(
        [
            "ip",
            "rule",
            "add",
            "priority",
            TUN_RULE_PRIORITY,
            "iif",
            "awg-in",
            "table",
            TUN_TABLE,
        ]
    )
    print(
        f"Steering awg-in -> {TUN_IFACE} (table {TUN_TABLE}, priority {TUN_RULE_PRIORITY})",
        flush=True,
    )

def _policy_values(variable: str, *, cidr: bool = False) -> list[str]:
    values = [value.strip() for value in env(variable).split(",") if value.strip()]
    if cidr:
        for value in values:
            ipaddress.ip_network(value, strict=False)
    else:
        values = [
            value
            if value.startswith(("domain:", "full:", "regexp:", "keyword:"))
            else f"domain:{value}"
            for value in values
        ]
    return values


def write_xray_config() -> None:
    active_plugins = plugins(enabled_only=True)
    fallback_plugins = [plugin for plugin in active_plugins if plugin.fallback]
    if len(fallback_plugins) > 1:
        raise RuntimeError("At most one enabled egress plugin may be the fallback")
    default_tag = fallback_plugins[0].tag if fallback_plugins else "direct"
    # User policies first so geoip/geosite does not steal corp destinations.
    rules: list[dict] = []
    for plugin in active_plugins:
        if plugin.fallback:
            continue
        domains = (
            _policy_values(plugin.policy_domains_var)
            if plugin.policy_domains_var
            else []
        )
        cidrs = (
            _policy_values(plugin.policy_cidrs_var, cidr=True)
            if plugin.policy_cidrs_var
            else []
        )
        if domains:
            rules.append(
                {"type": "field", "domain": domains, "outboundTag": plugin.tag}
            )
        if cidrs:
            rules.append({"type": "field", "ip": cidrs, "outboundTag": plugin.tag})
    # Geo → direct: categories from .env. Master switch XRAY_DIRECT_GEO
    # (default true). Set false/off to send all unmatched traffic to fallback.
    if enabled("XRAY_DIRECT_GEO", True):
        direct_geosite = (
            env("XRAY_DIRECT_GEOSITE") or env("XRAY_RU_GEOSITE", "category-ru")
        )
        direct_geoip = env("XRAY_DIRECT_GEOIP", "ru")
        if direct_geosite:
            rules.append(
                {
                    "type": "field",
                    "domain": [f"geosite:{direct_geosite}"],
                    "outboundTag": "direct",
                }
            )
        if direct_geoip:
            rules.append(
                {
                    "type": "field",
                    "ip": [f"geoip:{direct_geoip}"],
                    "outboundTag": "direct",
                }
            )
    rules.append(
        {
            "type": "field",
            "network": "tcp,udp",
            "outboundTag": default_tag,
        }
    )
    marks = {"direct": require_env("MARK_DIRECT")}
    marks.update({plugin.tag: plugin.mark() for plugin in active_plugins})
    outbounds = [
        {
            "tag": tag,
            "protocol": "freedom",
            "settings": {"domainStrategy": "UseIP"},
            "streamSettings": {"sockopt": {"mark": int(mark)}},
        }
        for tag, mark in marks.items()
    ]
    config = {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "tag": "tun-in",
                "protocol": "tun",
                "settings": {
                    "name": TUN_IFACE,
                    "mtu": int(env("AWG_MTU", "1380")),
                },
                "sniffing": {
                    "enabled": True,
                    "routeOnly": True,
                    "destOverride": ["http", "tls", "quic"],
                },
            }
        ],
        "outbounds": outbounds,
        "routing": {
            "domainStrategy": "IPIfNonMatch",
            "rules": rules,
        },
    }
    XRAY_CONFIG.write_text(json.dumps(config, indent=2), encoding="utf-8")
def initialize_network() -> str:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    wan = detect_wan_interface()
    setup_inbound_awg()
    for plugin in plugins(enabled_only=True):
        plugin.setup()
    setup_policy_routing(wan)
    write_xray_config()
    return wan


def cleanup_network() -> None:
    while (
        run(
            ["ip", "rule", "delete", "priority", TUN_RULE_PRIORITY],
            check=False,
        ).returncode
        == 0
    ):
        pass
    run(["ip", "route", "flush", "table", TUN_TABLE], check=False)
    run(["nft", "delete", "table", "inet", "vpn_dispatcher"], check=False)
    run(["awg-quick", "down", str(AWG_IN_CONF)], check=False)
    delete_link("awg-in")
    delete_link(TUN_IFACE)
    for plugin in plugins():
        plugin.teardown()
        if plugin.owns_interface:
            delete_link(plugin.interface)
    shutil.rmtree(RUN_DIR, ignore_errors=True)
