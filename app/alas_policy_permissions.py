"""Pure query and static-resource permission helpers for ALAS Embed."""

from __future__ import annotations

from collections.abc import Iterable


CONFIG_QUERY_KEYS = ("config", "name", "config_name")
# ``sg_ctx`` is the current gateway context key; keep the historical spelling
# during rollout so cached URLs cannot leak a context selector upstream.
SCRCPYGATE_CONTEXT_QUERY_KEYS = frozenset({"device_id", "sg_ctx", "scrcpygate_context"})
STATIC_PATH_PREFIXES = ("static", "assets", "pywebio_static")


def iter_query_items(query_items) -> list[tuple[str, object]]:
    """Return repeated query items from QueryParams, dicts, or plain pairs."""
    if hasattr(query_items, "multi_items"):
        return list(query_items.multi_items())
    if isinstance(query_items, dict):
        items: list[tuple[str, object]] = []
        for key, value in query_items.items():
            if isinstance(value, (list, tuple)):
                items.extend((key, item) for item in value)
            else:
                items.append((key, value))
        return items
    if isinstance(query_items, Iterable):
        return list(query_items or [])
    return []


def config_query_values(query_items) -> list[str]:
    """Return all non-empty config selector values using case-insensitive keys."""
    return [
        str(value).strip()
        for key, value in iter_query_items(query_items)
        if str(key).lower() in CONFIG_QUERY_KEYS and str(value or "").strip()
    ]


def bound_config_query_items(query_items, decision) -> list[tuple[str, object]]:
    """Forward one canonical config selector for filtered or pinned sessions."""
    params: list[tuple[str, object]] = []
    inserted_config = False
    pin_config = bool(decision.filtered or decision.pin_config)
    for key, value in iter_query_items(query_items):
        lowered_key = str(key).lower()
        if lowered_key in SCRCPYGATE_CONTEXT_QUERY_KEYS:
            continue
        if pin_config and lowered_key in CONFIG_QUERY_KEYS:
            if decision.config_name and not inserted_config:
                params.append(("config", decision.config_name))
                inserted_config = True
            continue
        params.append((key, value))
    if pin_config and decision.config_name and not inserted_config:
        params.append(("config", decision.config_name))
    return params


def is_readonly_static_request(method: str, path: str) -> bool:
    """Return whether a request is a context-free, read-only Runtime asset."""
    if str(method or "GET").upper() not in {"GET", "HEAD"}:
        return False
    normalized = str(path or "").replace("\\", "/").lstrip("/")
    first_segment = normalized.split("/", 1)[0].lower()
    return normalized.lower() == "favicon.ico" or first_segment in STATIC_PATH_PREFIXES


__all__ = [
    "CONFIG_QUERY_KEYS",
    "SCRCPYGATE_CONTEXT_QUERY_KEYS",
    "STATIC_PATH_PREFIXES",
    "iter_query_items",
    "config_query_values",
    "bound_config_query_items",
    "is_readonly_static_request",
]
