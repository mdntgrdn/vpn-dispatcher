from __future__ import annotations

from pathlib import Path

from container.common import enabled, require_env


class EgressPlugin:
    tag = ""
    display_name = ""
    interface = ""
    enabled_var = ""
    enabled_default = False
    mark_var = ""
    table_var = ""
    priority = ""
    policy_domains_var: str | None = None
    policy_cidrs_var: str | None = None
    fallback = False
    managed = False
    interactive_auth = False  # browser auth; tunnel may be down until login
    owns_policy_routing = True  # False when host owns fwmark/table
    owns_interface = True  # False for shared host uplink
    masquerade = True
    required_variables: tuple[str, ...] = ()
    secret_variables: tuple[str, ...] = ()
    required_secret_variables: tuple[str, ...] = ()

    def is_enabled(self) -> bool:
        return enabled(self.enabled_var, self.enabled_default)

    def mark(self) -> str:
        return require_env(self.mark_var)

    def table(self) -> str:
        return require_env(self.table_var)

    def link_exists(self) -> bool:
        return Path("/sys/class/net", self.interface).exists()

    def setup(self) -> None:
        """Create static interfaces before Xray starts."""

    def teardown(self) -> None:
        """Remove plugin-owned host rules/resources on shutdown."""

    def command(self) -> tuple[list[str], str | None] | None:
        """Return a long-running client command and optional stdin."""
        return None

    def public_key(self) -> str | None:
        return None
