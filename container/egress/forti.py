from __future__ import annotations

import os
import subprocess
from pathlib import Path

from container.common import enabled, env, write_private
from container.egress.base import EgressPlugin


class FortiEgress(EgressPlugin):
    """FortiGate SSL VPN via openfortivpn (browser SAML)."""

    tag = "forti"
    display_name = "FortiGate SAML"
    interface = "forti0"
    enabled_var = "FORTI_ENABLED"
    mark_var = "MARK_FORTI"
    table_var = "TABLE_FORTI"
    priority = "1010"
    policy_domains_var = "POLICY_FORTI_DOMAINS"
    policy_cidrs_var = "POLICY_FORTI_CIDRS"
    managed = True
    interactive_auth = True
    required_variables = ("FORTI_HOST",)
    secret_variables = ()
    required_secret_variables = ()

    def setup(self) -> None:
        self._ensure_saml_proxy()

    def command(self) -> tuple[list[str], str | None]:
        self._ensure_saml_proxy()
        saml_port = env("FORTI_SAML_PORT", "8020")
        host = env("FORTI_HOST")
        port = env("FORTI_PORT", "443")
        realm = env("FORTI_REALM")
        realm_query = f"&realm={realm}" if realm else ""
        login_url = (
            f"https://{host}:{port}/remote/saml/start?redirect=1{realm_query}"
        )
        print(
            "Forti SAML is waiting for browser login.\n"
            f"Open: {login_url}\n"
            f"Or run: poetry run python scripts/forti_reconnect.py\n"
            f"Browser must redirect to http://127.0.0.1:{saml_port}/",
            flush=True,
        )

        lines = [
            f"host = {host}",
            f"port = {port}",
            f"saml-login = {saml_port}",
            "set-routes = 0",
            "set-dns = 0",
            "pppd-use-peerdns = 0",
            f"pppd-ifname = {self.interface}",
            "persistent = 0",
        ]
        if env("FORTI_USER"):
            lines.append(f"username = {env('FORTI_USER')}")
        if realm:
            lines.append(f"realm = {realm}")
        if env("FORTI_TRUSTED_CERT"):
            lines.append(f"trusted-cert = {env('FORTI_TRUSTED_CERT')}")
        path = Path("/run/vpn-dispatcher/forti.conf")
        write_private(path, "\n".join(lines) + "\n")
        return ["openfortivpn", "-c", str(path)], None

    def _ensure_saml_proxy(self) -> None:
        """Forward published SAML port to openfortivpn on loopback (bridge mode only)."""
        if enabled("HOST_NETWORK"):
            return
        saml_port = env("FORTI_SAML_PORT", "8020")
        proxy_port = env("FORTI_SAML_PROXY_PORT", "18020")
        marker = Path("/run/vpn-dispatcher/forti-saml-proxy.pid")
        if marker.is_file():
            try:
                pid = int(marker.read_text().strip())
                os.kill(pid, 0)
                return
            except (ValueError, ProcessLookupError, OSError):
                marker.unlink(missing_ok=True)

        process = subprocess.Popen(
            [
                "socat",
                f"TCP-LISTEN:{proxy_port},bind=0.0.0.0,fork,reuseaddr",
                f"TCP:127.0.0.1:{saml_port}",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        marker.write_text(str(process.pid), encoding="utf-8")
        print(
            f"Forti SAML proxy listening on 0.0.0.0:{proxy_port} -> "
            f"127.0.0.1:{saml_port}",
            flush=True,
        )


PLUGIN = FortiEgress()
