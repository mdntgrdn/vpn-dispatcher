# Egress plugins

Each outbound VPN client lives in its own module:

- `awg.py` — fallback: host uplink (`HOST_NETWORK`) or local `awg-out` client;
- `wireguard.py`;
- `forti.py`;
- `cisco.py`.

The registry auto-discovers any module in this directory that exports a
`PLUGIN` instance of `EgressPlugin`. Routing tables, Xray outbounds, policies,
healthcheck, restart loop, and secret delivery are wired automatically.

Minimal OpenVPN client scaffold:

```python
from container.common import env, read_secret, write_private
from container.egress.base import EgressPlugin


class OpenVpnEgress(EgressPlugin):
    tag = "openvpn"
    display_name = "OpenVPN"
    interface = "ovpn0"
    enabled_var = "OPENVPN_ENABLED"
    mark_var = "MARK_OPENVPN"
    table_var = "TABLE_OPENVPN"
    priority = "1050"
    policy_domains_var = "POLICY_OPENVPN_DOMAINS"
    policy_cidrs_var = "POLICY_OPENVPN_CIDRS"
    managed = True
    required_variables = ("OPENVPN_REMOTE",)
    secret_variables = ("OPENVPN_CONFIG",)
    required_secret_variables = secret_variables

    def command(self):
        config = "/run/vpn-dispatcher-secrets/openvpn_config"
        return [
            "openvpn",
            "--config", config,
            "--dev", self.interface,
            "--route-noexec",
            "--ifconfig-noexec",
        ], None


PLUGIN = OpenVpnEgress()
```

Then:

1. add the client binary to the `Dockerfile`;
2. add its variables to `.env.example`;
3. if the client does not configure the interface address itself, do it in
   `command()` or a helper up-script.

Other egress modules and the central runtime do not need changes.
