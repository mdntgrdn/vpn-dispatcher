from __future__ import annotations

import ipaddress
import os
import re
from pathlib import Path

from container.egress.registry import plugins

ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / ".env"

# Only non-routing deploy conveniences. Marks/tables/iface must be set in .env.
DEFAULTS: dict[str, str] = {
    "DEPLOY_PORT": "22",
    "DEPLOY_PATH": "/opt/vpn-dispatcher",
    "DEPLOY_SUDO": "false",
    "AWG_LISTEN_PORT": "51820",
    "AWG_SUBNET": "10.77.0.0/30",
    "AWG_DNS": "1.1.1.1",
    "AWG_MTU": "1380",
    "CONTAINER_NAME": "vpn-dispatcher",
    "DOCKER_IMAGE": "vpn-dispatcher:local",
}

_AWG_OUT_CLIENT_REQUIRED = (
    "AWG_OUT_ADDRESS",
    "AWG_OUT_PEER_PUBLIC_KEY",
    "AWG_OUT_ENDPOINT",
    "AWG_OUT_JC",
    "AWG_OUT_JMIN",
    "AWG_OUT_JMAX",
    "AWG_OUT_S1",
    "AWG_OUT_S2",
    "AWG_OUT_H1",
    "AWG_OUT_H2",
    "AWG_OUT_H3",
    "AWG_OUT_H4",
)

SECRET_NAMES = {
    "AWG_SERVER_PRIVATE_KEY",
    *(name for plugin in plugins() for name in plugin.secret_variables),
}


def _parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ValueError(f"{path}:{number}: expected NAME=value")
        name, value = line.split("=", 1)
        name = name.strip()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
            raise ValueError(f"{path}:{number}: invalid variable name {name!r}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[name] = value
    return values


class Settings:
    def __init__(self, env_file: Path = ENV_FILE) -> None:
        self.env_file = env_file
        if not env_file.is_file():
            raise ValueError(
                f"Missing {env_file}. Copy .env.example to .env and fill it in."
            )
        file_values = _parse_env(env_file)
        known_names = set(DEFAULTS) | set(_parse_env(ROOT / ".env.example")) | set(file_values)
        environment = {name: os.environ[name] for name in known_names if name in os.environ}
        self.values = {**DEFAULTS, **file_values, **environment}

    def get(self, name: str, default: str = "") -> str:
        return str(self.values.get(name, default)).strip()

    def require(self, name: str) -> str:
        value = self.get(name)
        if not value:
            raise ValueError(f"{name} is required")
        return value

    def enabled(self, name: str, default: bool = False) -> bool:
        value = self.get(name, "true" if default else "false").casefold()
        if value in {"1", "true", "yes", "on"}:
            return True
        if value in {"0", "false", "no", "off", ""}:
            return False
        raise ValueError(f"{name} must be true or false, got {value!r}")

    def secret(self, name: str) -> str:
        return self.get(name)

    def public_values(self) -> dict[str, str]:
        return {
            key: value
            for key, value in self.values.items()
            if key not in SECRET_NAMES and isinstance(value, str)
        }

    def secrets(self) -> dict[str, str]:
        return {name: self.secret(name) for name in sorted(SECRET_NAMES)}

    def runtime_env(self) -> str:
        values = {
            key: value
            for key, value in self.public_values().items()
            if not key.startswith("DEPLOY_")
        }
        for key, value in values.items():
            if "\n" in value or "\r" in value:
                raise ValueError(f"{key} must not contain a newline")
        return "".join(f"{key}={value}\n" for key, value in sorted(values.items()))

    def validate(self, *, deployment: bool = True) -> None:
        errors: list[str] = []

        required = ["PUBLIC_HOST", "HOST_NETWORK", "MARK_DIRECT"]
        if deployment:
            required += ["DEPLOY_HOST", "DEPLOY_USER", "DEPLOY_PATH", "CONTAINER_NAME"]
        for name in required:
            if not self.get(name):
                errors.append(f"{name} is required")

        for name in ("DEPLOY_PORT", "AWG_LISTEN_PORT"):
            try:
                value = int(self.get(name))
                if not 1 <= value <= 65535:
                    raise ValueError
            except ValueError:
                errors.append(f"{name} must be an integer from 1 to 65535")

        try:
            self.enabled("HOST_NETWORK")
        except ValueError as exc:
            errors.append(str(exc))

        try:
            awg_network = ipaddress.ip_network(self.get("AWG_SUBNET"), strict=True)
            if awg_network.version != 4 or awg_network.num_addresses < 4:
                errors.append("AWG_SUBNET must be an IPv4 network with at least 4 addresses")
        except ValueError as exc:
            errors.append(f"AWG_SUBNET is invalid: {exc}")

        policy_cidr_names = {
            plugin.policy_cidrs_var
            for plugin in plugins()
            if plugin.policy_cidrs_var
        }
        for name in sorted(policy_cidr_names):
            for value in (item.strip() for item in self.get(name).split(",")):
                if not value:
                    continue
                try:
                    ipaddress.ip_network(value, strict=False)
                except ValueError as exc:
                    errors.append(f"{name} contains invalid CIDR {value!r}: {exc}")

        ssh_key = self.get("DEPLOY_SSH_KEY")
        if deployment and ssh_key and not Path(ssh_key).expanduser().is_file():
            errors.append(f"DEPLOY_SSH_KEY does not exist: {ssh_key}")

        active_plugins = []
        for plugin in plugins():
            flag = plugin.enabled_var
            try:
                active = self.enabled(flag, plugin.enabled_default)
            except ValueError as exc:
                errors.append(str(exc))
                continue
            if not active:
                continue
            active_plugins.append(plugin)
            for name in plugin.required_variables:
                if not self.get(name):
                    errors.append(f"{name} is required when {flag}=true")
            for name in plugin.required_secret_variables:
                try:
                    if not self.secret(name):
                        errors.append(f"{name} is required when {flag}=true")
                except ValueError as exc:
                    errors.append(str(exc))
            if plugin.mark_var and not self.get(plugin.mark_var):
                errors.append(f"{plugin.mark_var} is required when {flag}=true")
            if (
                plugin.table_var
                and self._plugin_owns_policy_routing(plugin)
                and not self.get(plugin.table_var)
            ):
                errors.append(f"{plugin.table_var} is required when {flag}=true")

        for label, values in (
            (
                "routing marks",
                [self.get("MARK_DIRECT"), *[
                    self.get(plugin.mark_var) for plugin in active_plugins if plugin.mark_var
                ]],
            ),
            (
                "routing tables",
                [
                    self.get(plugin.table_var)
                    for plugin in active_plugins
                    if plugin.table_var and self._plugin_owns_policy_routing(plugin)
                ],
            ),
        ):
            values = [value for value in values if value]
            if values and len(values) != len(set(values)):
                errors.append(f"Enabled egress plugins have duplicate {label}")

        if self.enabled("AWG_OUT_ENABLED"):
            # PostUp / Xray always need mark+table when AWG fallback is on.
            for name in ("MARK_AWG", "TABLE_AWG"):
                if not self.get(name):
                    errors.append(f"{name} is required when AWG_OUT_ENABLED=true")
            if self.enabled("HOST_NETWORK"):
                for name in ("WAN_IFACE", "AWG_OUT_INTERFACE", "AWG_OUT_SNAT"):
                    if not self.get(name):
                        errors.append(
                            f"{name} is required when HOST_NETWORK=true and "
                            "AWG_OUT_ENABLED=true"
                        )
            else:
                for name in _AWG_OUT_CLIENT_REQUIRED:
                    if not self.get(name):
                        errors.append(
                            f"{name} is required when HOST_NETWORK=false and "
                            "AWG_OUT_ENABLED=true"
                        )

        if errors:
            raise ValueError("\n".join(f"- {error}" for error in errors))

    def _plugin_owns_policy_routing(self, plugin) -> bool:
        if plugin.enabled_var != "AWG_OUT_ENABLED":
            return bool(getattr(plugin, "owns_policy_routing", True))
        return not self.enabled("HOST_NETWORK")
