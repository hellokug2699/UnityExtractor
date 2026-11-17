"""Lazy adapter registry shared by frontends."""
from __future__ import annotations

import importlib
from typing import Any

_ADAPTER_FACTORIES = {
    "phigros": ("unity_extractor.adapters.phigros", "PhigrosAdapter"),
}


def available_adapters() -> list[str]:
    return sorted(_ADAPTER_FACTORIES)


def create_adapter(name: str | None) -> Any | None:
    if not name:
        return None
    try:
        module_name, class_name = _ADAPTER_FACTORIES[name]
    except KeyError as exc:
        raise ValueError(f"Unknown adapter: {name}") from exc
    module = importlib.import_module(module_name)
    return getattr(module, class_name)()
