from __future__ import annotations

from pathlib import Path

from container.common import enabled, env, read_secret, require_env, run, write_private
from container.egress.base import EgressPlugin
from container.egress.utils import persistent_private_key, start_awg


class AwgEgress(EgressPlugin):
    """Fallback via host uplink or local awg-out client, depending on HOST_NETWORK."""

    tag = "awg"
    display_name = "AWG fallback"
    enabled_var = "AWG_OUT_ENABLED"
    enabled_default = False
    mark_var = "MARK_AWG"
    table_var = "TABLE_AWG"
    priority = "1040"
    fallback = True
    managed = False
    secret_variables = ("AWG_OUT_PRIVATE_KEY", "AWG_OUT_PRESHARED_KEY")
    required_variables: tuple[str, ...] = ()

    @property
    def host_network(self) -> bool:
        return enabled("HOST_NETWORK")

    @property
    def owns_policy_routing(self) -> bool:
        return not self.host_network

    @property
    def owns_interface(self) -> bool:
        return not self.host_network

    @property
    def masquerade(self) -> bool:
        return not self.host_network

    @property
    def interface(self) -> str:
        if self.host_network:
            return require_env("AWG_OUT_INTERFACE")
        return "awg-out"

    def setup(self) -> None:
        if self.host_network:
            self._setup_host_uplink()
        else:
            self._setup_client()

    def _setup_host_uplink(self) -> None:
        iface = self.interface
        if not self.link_exists():
            raise RuntimeError(
                f"{iface!r} is missing. Use HOST_NETWORK=true and start the "
                "host AWG uplink before vpn-dispatcher."
            )
        print(
            f"AWG fallback (host): mark {self.mark()} via {iface}",
            flush=True,
        )

    def _setup_client(self) -> None:
        start_awg(self.interface)
        private_file = persistent_private_key(
            "awg_out_private_key",
            Path("/data/awg-out.key"),
            "awg",
        )
        command = ["awg", "set", self.interface, "private-key", str(private_file)]
        for key in ("jc", "jmin", "jmax", "s1", "s2", "h1", "h2", "h3", "h4"):
            command.extend([key, env(f"AWG_OUT_{key.upper()}")])
        for key in ("s3", "s4", "i1", "i2", "i3", "i4", "i5"):
            value = env(f"AWG_OUT_{key.upper()}")
            if value:
                command.extend([key, value])
        command.extend(
            [
                "peer",
                env("AWG_OUT_PEER_PUBLIC_KEY"),
                "endpoint",
                env("AWG_OUT_ENDPOINT"),
                "allowed-ips",
                env("AWG_OUT_ALLOWED_IPS", "0.0.0.0/0"),
                "persistent-keepalive",
                env("AWG_OUT_KEEPALIVE", "25"),
            ]
        )
        psk = read_secret("awg_out_preshared_key")
        if psk:
            psk_file = Path("/run/vpn-dispatcher/awg-out.psk")
            write_private(psk_file, psk)
            command.extend(["preshared-key", str(psk_file)])
        run(command)
        run(["ip", "address", "add", env("AWG_OUT_ADDRESS"), "dev", self.interface])
        run(
            [
                "ip",
                "link",
                "set",
                "dev",
                self.interface,
                "mtu",
                env("AWG_OUT_MTU", "1380"),
                "up",
            ]
        )
        print(
            f"AWG fallback (bridge): {self.interface} -> "
            f"{env('AWG_OUT_ENDPOINT')} public key: {self.public_key()}",
            flush=True,
        )

    def public_key(self) -> str | None:
        if self.host_network or not self.link_exists():
            return None
        result = run(["awg", "show", self.interface, "public-key"], check=False)
        return result.stdout.strip() if result.returncode == 0 else None


PLUGIN = AwgEgress()
