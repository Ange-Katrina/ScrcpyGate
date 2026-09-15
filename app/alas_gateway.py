"""Short-lived ALAS gateway tickets and origin-scoped sessions.

The gateway deliberately keeps its state in memory.  A ticket is only a
browser hand-off between the authenticated ScrcpyGate page and the ALAS
origin; it is never persisted or included in a WebSocket URL.
"""

from __future__ import annotations

import os
import secrets
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlparse


ALAS_ORIGIN_ENV = "ALAS_EMBED_ORIGIN"
ALAS_COOKIE = "scrcpygate_alas_sid"
ALAS_CONTEXT_QUERY = "sg_ctx"
TICKET_TTL_SECONDS = 60
GATEWAY_SESSION_TTL_SECONDS = 12 * 60 * 60
MAX_TICKETS = 4096
MAX_GATEWAY_SESSIONS = 4096


@dataclass(frozen=True)
class GatewayRecord:
    session_id: str
    username: str
    role: str
    config_name: str
    device_id: str
    expires_at: float
    gateway_id: str = ""
    context_id: str = ""


_lock = threading.RLock()
_tickets: dict[str, GatewayRecord] = {}
_sessions: dict[str, GatewayRecord] = {}


def configured_origin() -> str:
    """Return a normalized ALAS origin, or an empty string when disabled."""
    raw = str(os.environ.get(ALAS_ORIGIN_ENV, "") or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return ""
        if parsed.username or parsed.password or parsed.path not in ("", "/"):
            return ""
        if parsed.params or parsed.query or parsed.fragment:
            return ""
        port = parsed.port
    except ValueError:
        return ""
    host = parsed.hostname.lower().rstrip(".")
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    default_port = 443 if parsed.scheme == "https" else 80
    suffix = f":{port}" if port and port != default_port else ""
    return f"{parsed.scheme}://{host}{suffix}"


def enabled() -> bool:
    return bool(configured_origin())


def _prune(now: float) -> None:
    for collection in (_tickets, _sessions):
        for key, record in tuple(collection.items()):
            if record.expires_at <= now:
                collection.pop(key, None)
    while len(_tickets) > MAX_TICKETS:
        _tickets.pop(next(iter(_tickets)), None)
    while len(_sessions) > MAX_GATEWAY_SESSIONS:
        _sessions.pop(next(iter(_sessions)), None)


def issue_ticket(
    *,
    session_id: str,
    username: str,
    role: str,
    config_name: str = "",
    device_id: str = "",
    ttl_seconds: int = TICKET_TTL_SECONDS,
) -> str:
    now = time.time()
    record = GatewayRecord(
        session_id=str(session_id or ""),
        username=str(username or ""),
        role=str(role or ""),
        config_name=str(config_name or ""),
        device_id=str(device_id or ""),
        expires_at=now + max(1, min(int(ttl_seconds), 300)),
    )
    token = secrets.token_urlsafe(32)
    with _lock:
        _prune(now)
        _tickets[token] = record
    return token


def redeem_ticket(token: str) -> GatewayRecord | None:
    value = str(token or "").strip()
    if not value:
        return None
    now = time.time()
    with _lock:
        _prune(now)
        record = _tickets.pop(value, None)
        if not record or record.expires_at <= now:
            return None
        # The ticket is a one-time, short-lived hand-off. Once redeemed, the
        # origin-scoped gateway session gets its own full lifetime.
        expires_at = now + GATEWAY_SESSION_TTL_SECONDS
        gateway_record = GatewayRecord(
            session_id=record.session_id,
            username=record.username,
            role=record.role,
            config_name=record.config_name,
            device_id=record.device_id,
            expires_at=expires_at,
            context_id=secrets.token_urlsafe(12),
        )
        sid = secrets.token_urlsafe(32)
        _sessions[sid] = gateway_record
        return GatewayRecord(
            session_id=gateway_record.session_id,
            username=gateway_record.username,
            role=gateway_record.role,
            config_name=gateway_record.config_name,
            device_id=gateway_record.device_id,
            expires_at=gateway_record.expires_at,
            gateway_id=sid,
            context_id=gateway_record.context_id,
        )


def cookie_name(context_id: str | None) -> str:
    """Return the per-tab cookie name for a validated public context selector."""
    value = str(context_id or "").strip()
    if not value or len(value) > 64 or any(not (char.isalnum() or char in "_-") for char in value):
        return ""
    return f"{ALAS_COOKIE}_{value}"


def get_session(gateway_sid: str | None) -> GatewayRecord | None:
    value = str(gateway_sid or "").strip()
    if not value:
        return None
    now = time.time()
    with _lock:
        _prune(now)
        record = _sessions.get(value)
        if not record or record.expires_at <= now:
            _sessions.pop(value, None)
            return None
        return record


def get_context_session(context_id: str | None, gateway_sid: str | None) -> GatewayRecord | None:
    """Resolve a gateway session only when its public selector matches the cookie session."""
    value = str(context_id or "").strip()
    if not cookie_name(value):
        return None
    record = get_session(gateway_sid)
    if not record or not secrets.compare_digest(record.context_id, value):
        return None
    return record


def revoke_session(gateway_sid: str | None) -> None:
    value = str(gateway_sid or "").strip()
    if value:
        with _lock:
            _sessions.pop(value, None)


def revoke_user_contexts(username: str | None) -> int:
    """Revoke pending tickets and redeemed sessions for one account."""
    value = str(username or "").strip()
    if not value:
        return 0
    removed = 0
    with _lock:
        for collection in (_tickets, _sessions):
            for key, record in tuple(collection.items()):
                if secrets.compare_digest(str(record.username), value):
                    collection.pop(key, None)
                    removed += 1
    return removed


def revoke_device_contexts(device_id: str | None, username: str | None = None) -> int:
    """Revoke Gateway contexts bound to a device, optionally for one user."""
    device = str(device_id or "").strip()
    owner = str(username or "").strip()
    if not device:
        return 0
    removed = 0
    with _lock:
        for collection in (_tickets, _sessions):
            for key, record in tuple(collection.items()):
                if str(record.device_id) != device:
                    continue
                if owner and not secrets.compare_digest(str(record.username), owner):
                    continue
                collection.pop(key, None)
                removed += 1
    return removed


def clear() -> None:
    with _lock:
        _tickets.clear()
        _sessions.clear()
