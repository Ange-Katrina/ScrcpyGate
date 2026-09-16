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


def _enabled_flag(user) -> bool:
    """Read ``users.enabled`` from a dict, a sqlite3.Row or any mapping.

    ``"enabled" in row`` is NOT a column check for ``sqlite3.Row`` (it compares
    against the row's *values*), so the old membership test silently treated a
    disabled account as enabled whenever storage handed over a raw Row.  Read the
    column by name instead; a caller that did not select it keeps the previous
    "assume enabled" behaviour.
    """
    try:
        value = user["enabled"]
    except (KeyError, IndexError, TypeError):
        return True
    return bool(value)


def user_is_active(user, *, now: int, now_fn: Callable[[], int] | None = None) -> bool:
    if not user:
        return False
    if not _enabled_flag(user):
        return False
    expires_at = user_expires_at(user)
    current = now if now is not None else (now_fn() if now_fn else 0)
    return expires_at is None or expires_at > int(current)


def user_login_allowed(user) -> bool:
    """Sign-in is blocked only by an explicit administrator disable.

    Expiry is a paid-service state, not a ban: the account keeps its identity,
    its grants and its ability to sign in (so it can be renewed, or reach a
    future payment flow).  Mirroring and ALAS stay closed to it through
    :func:`user_is_active`, which every feature gate keeps using.
    """
    if not user:
        return False
    return _enabled_flag(user)


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
    enabled = _enabled_flag(user)
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


__all__ = [
    "normalize_expires_at",
    "user_expires_at",
    "user_is_active",
    "user_login_allowed",
    "user_expiration_payload",
]
