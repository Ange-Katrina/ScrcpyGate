"""Bounded inspection primitives shared by the ALAS policy layer.

This module deliberately depends only on the standard library.  Keeping the
budget checks here prevents transport and policy code from growing separate
limits or recursive validators.
"""

from __future__ import annotations

import os


def bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)) or default)
    except (TypeError, ValueError, OverflowError):
        value = default
    return max(minimum, min(value, maximum))


ALAS_WS_MESSAGE_MAX_BYTES = bounded_env_int(
    "ALAS_WS_MESSAGE_MAX_BYTES",
    1024 * 1024,
    64 * 1024,
    8 * 1024 * 1024,
)
ALAS_POLICY_MAX_DEPTH = bounded_env_int("ALAS_POLICY_MAX_DEPTH", 128, 1, 256)
ALAS_POLICY_MAX_NODES = bounded_env_int("ALAS_POLICY_MAX_NODES", 20_000, 256, 1_000_000)


class PayloadBudgetExceeded(ValueError):
    """A policy payload cannot be inspected within the configured budget."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def ensure_websocket_message_size(message: str | bytes) -> None:
    if isinstance(message, bytes):
        size = len(message)
    elif isinstance(message, str):
        size = len(message.encode("utf-8", "replace"))
    else:
        return
    if size > ALAS_WS_MESSAGE_MAX_BYTES:
        raise PayloadBudgetExceeded("payload_too_large")


def validate_policy_payload(value) -> None:
    """Validate a JSON-like payload without recursive traversal."""
    stack: list[tuple[object, int, bool]] = [(value, 0, False)]
    active: set[int] = set()
    nodes = 0
    while stack:
        item, depth, exiting = stack.pop()
        if exiting:
            active.discard(id(item))
            continue
        nodes += 1
        if nodes > ALAS_POLICY_MAX_NODES:
            raise PayloadBudgetExceeded("payload_too_complex")
        if depth > ALAS_POLICY_MAX_DEPTH:
            raise PayloadBudgetExceeded("payload_too_complex")
        if not isinstance(item, (dict, list, tuple)):
            continue
        identity = id(item)
        if identity in active:
            raise PayloadBudgetExceeded("payload_cycle")
        active.add(identity)
        stack.append((item, depth, True))
        remaining_nodes = ALAS_POLICY_MAX_NODES - nodes
        if len(item) > remaining_nodes:
            raise PayloadBudgetExceeded("payload_too_complex")
        if isinstance(item, dict):
            children = (item[key] for key in reversed(item))
        else:
            children = reversed(item)
        for child in children:
            stack.append((child, depth + 1, False))


__all__ = [
    "ALAS_WS_MESSAGE_MAX_BYTES",
    "ALAS_POLICY_MAX_DEPTH",
    "ALAS_POLICY_MAX_NODES",
    "PayloadBudgetExceeded",
    "bounded_env_int",
    "ensure_websocket_message_size",
    "validate_policy_payload",
]
