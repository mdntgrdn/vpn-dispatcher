from __future__ import annotations

from pathlib import Path

from container.common import env, parse_totp_uri, read_secret, write_private
from container.egress.base import EgressPlugin


class CiscoEgress(EgressPlugin):
    tag = "cisco"
    display_name = "Cisco AnyConnect"
    interface = "cisco0"
    enabled_var = "CISCO_ENABLED"
    mark_var = "MARK_CISCO"
    table_var = "TABLE_CISCO"
    priority = "1020"
    policy_domains_var = "POLICY_CISCO_DOMAINS"
    policy_cidrs_var = "POLICY_CISCO_CIDRS"
    managed = True
    required_variables = ("CISCO_HOST", "CISCO_USER")
    secret_variables = ("CISCO_PASSWORD", "CISCO_TOTP_URI")
    required_secret_variables = secret_variables

    def command(self) -> tuple[list[str], str | None]:
        values = parse_totp_uri(read_secret("cisco_totp_uri"))
        token_file = Path("/run/vpn-dispatcher/cisco.totp")
        write_private(token_file, f"base32:{values['secret']}")
        endpoint = f"https://{env('CISCO_HOST')}:{env('CISCO_PORT', '443')}"
        command = [
            "openconnect",
            "--non-inter",
            "--passwd-on-stdin",
            "--protocol",
            env("CISCO_PROTOCOL", "anyconnect"),
            "--user",
            env("CISCO_USER"),
            "--token-mode",
            "totp",
            "--token-secret",
            f"@{token_file}",
            "--interface",
            self.interface,
            "--script",
            "/app/container/egress/_cisco_vpnc.py",
            "--mtu",
            env("CISCO_MTU") or "1350",
        ]
        if env("CISCO_GROUP"):
            command.extend(["--authgroup", env("CISCO_GROUP")])
        if env("CISCO_SERVER_CERT"):
            command.extend(["--servercert", env("CISCO_SERVER_CERT")])
        command.append(endpoint)
        return command, read_secret("cisco_password") + "\n"


PLUGIN = CiscoEgress()
