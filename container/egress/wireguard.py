from __future__ import annotations

from pathlib import Path

from container.common import env, read_secret, run, write_private
from container.egress.base import EgressPlugin
from container.egress.utils import delete_link, persistent_private_key


class WireGuardEgress(EgressPlugin):
    tag = "wg"
    display_name = "WireGuard"
    interface = "wg-out"
    enabled_var = "WG_OUT_ENABLED"
    mark_var = "MARK_WG"
    table_var = "TABLE_WG"
    priority = "1030"
    policy_domains_var = "POLICY_WG_DOMAINS"
    policy_cidrs_var = "POLICY_WG_CIDRS"
    required_variables = (
        "WG_OUT_ADDRESS",
        "WG_OUT_PEER_PUBLIC_KEY",
        "WG_OUT_ENDPOINT",
    )
    secret_variables = ("WG_OUT_PRIVATE_KEY", "WG_OUT_PRESHARED_KEY")

    def setup(self) -> None:
        delete_link(self.interface)
        run(["ip", "link", "add", "dev", self.interface, "type", "wireguard"])
        private_file = persistent_private_key(
            "wg_out_private_key",
            Path("/data/wg-out.key"),
            "wg",
        )
        command = [
            "wg",
            "set",
            self.interface,
            "private-key",
            str(private_file),
            "peer",
            env("WG_OUT_PEER_PUBLIC_KEY"),
            "endpoint",
            env("WG_OUT_ENDPOINT"),
            "allowed-ips",
            env("WG_OUT_ALLOWED_IPS", "0.0.0.0/0"),
            "persistent-keepalive",
            env("WG_OUT_KEEPALIVE", "25"),
        ]
        psk = read_secret("wg_out_preshared_key")
        if psk:
            psk_file = Path("/run/vpn-dispatcher/wg-out.psk")
            write_private(psk_file, psk)
            command.extend(["preshared-key", str(psk_file)])
        run(command)
        run(["ip", "address", "add", env("WG_OUT_ADDRESS"), "dev", self.interface])
        run(
            [
                "ip",
                "link",
                "set",
                "dev",
                self.interface,
                "mtu",
                env("WG_OUT_MTU", "1380"),
                "up",
            ]
        )
        print(f"{self.interface} client public key: {self.public_key()}", flush=True)

    def public_key(self) -> str | None:
        if not self.link_exists():
            return None
        result = run(["wg", "show", self.interface, "public-key"], check=False)
        return result.stdout.strip() if result.returncode == 0 else None


PLUGIN = WireGuardEgress()
