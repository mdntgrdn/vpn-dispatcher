from __future__ import annotations

import importlib
import pkgutil
from functools import lru_cache

from container.egress.base import EgressPlugin


@lru_cache(maxsize=1)
def all_plugins() -> tuple[EgressPlugin, ...]:
    package = importlib.import_module("container.egress")
    discovered: list[EgressPlugin] = []
    for module_info in pkgutil.iter_modules(package.__path__):
        if module_info.name.startswith("_") or module_info.name in {
            "base",
            "registry",
            "utils",
        }:
            continue
        module = importlib.import_module(f"{package.__name__}.{module_info.name}")
        plugin = getattr(module, "PLUGIN", None)
        if isinstance(plugin, EgressPlugin):
            discovered.append(plugin)
    discovered.sort(key=lambda plugin: int(plugin.priority))
    _validate(discovered)
    return tuple(discovered)


def _validate(discovered: list[EgressPlugin]) -> None:
    for attribute in ("tag", "interface", "mark_var", "table_var", "priority"):
        values = [str(getattr(plugin, attribute)) for plugin in discovered]
        if len(values) != len(set(values)):
            raise RuntimeError(f"Egress plugins have duplicate {attribute}")
    fallbacks = [plugin for plugin in discovered if plugin.fallback]
    if len(fallbacks) > 1:
        raise RuntimeError("At most one egress plugin may be marked as fallback")


def plugins(*, enabled_only: bool = False) -> tuple[EgressPlugin, ...]:
    result = all_plugins()
    if enabled_only:
        return tuple(plugin for plugin in result if plugin.is_enabled())
    return result
