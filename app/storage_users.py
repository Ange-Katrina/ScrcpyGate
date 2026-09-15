"""Pure user-account state rules used by the storage compatibility facade."""

from __future__ import annotations

import math
from collections.abc import Callable


def normalize_expires_at(value, *, max_expires_at: int) -> int | None:
    """Normalize the nullable UTC epoch used for account expiration."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("invalid_expires_at")
    if isinstance(value, float) and (not math.isfinite(value) or not value.is_integer()):
        raise ValueError("invalid_expires_at")
    if isinstance(value, str) and not value.strip().isdecimal():
        raise ValueError("invalid_expires_at")
    try:
        expires_at = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("invalid_expires_at") from exc
    if expires_at < 0 or expires_at > max_expires_at:
        raise ValueError("invalid_expires_at")
    return expires_at


def user_expires_at(user) -> int | None:
    if not user:
        return None
    try:
        value = user["expires_at"]
    except (KeyError, IndexError):
        return None
    return int(value) if value is not None else None


def user_is_active(user, *, now: int, now_fn: Callable[[], int] | None = None) -> bool:
    if not user:
        return False
    try:
        if "enabled" in user and not bool(user["enabled"]):
            return False
    except (KeyError, IndexError, TypeError):
        return False
    expires_at = user_expires_at(user)
    current = now if now is not None else (now_fn() if now_fn else 0)
    return expires_at is None or expires_at > int(current)


def user_expiration_payload(
    user,
    *,
    now: int,
    expiring_window_seconds: int,
    now_fn: Callable[[], int] | None = None,
) -> dict:
    """Return the stable public expiration state for one stored user."""
    current = int(now if now is not None else (now_fn() if now_fn else 0))
    expires_at = user_expires_at(user)
    enabled = bool(user.get("enabled", 1)) if hasattr(user, "get") else True
    if not enabled:
        return {
            "expires_at": expires_at,
            "expiration_state": "disabled",
            "remaining_seconds": None if expires_at is None else max(0, expires_at - current),
            "is_active": False,
        }
    if expires_at is None:
        return {
            "expires_at": None,
            "expiration_state": "permanent",
            "remaining_seconds": None,
            "is_active": True,
        }
    remaining = max(0, expires_at - current)
    if remaining <= 0:
        state = "expired"
    elif remaining <= expiring_window_seconds:
        state = "expiring"
    else:
        state = "active"
    return {
        "expires_at": expires_at,
        "expiration_state": state,
        "remaining_seconds": remaining,
        "is_active": remaining > 0,
    }


__all__ = ["normalize_expires_at", "user_expires_at", "user_is_active", "user_expiration_payload"]
