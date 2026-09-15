"""ALAS Gateway context validation and iframe URL helpers."""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import Request, WebSocket

from .. import alas_gateway, security, storage


def _record_value(record: object, key: str, default: object = None) -> object:
    """Read a field from either a mapping or a sqlite3.Row."""
    getter = getattr(record, "get", None)
    if callable(getter):
        return getter(key, default)
    try:
        return record[key]  # type: ignore[index]
    except (KeyError, IndexError, TypeError):
        return default


def _record_dict(record: object) -> dict:
    """Copy mapping-like rows without relying on sqlite3.Row.get()."""
    if isinstance(record, dict):
        return record
    keys = getattr(record, "keys", None)
    if callable(keys):
        return {key: record[key] for key in keys()}  # type: ignore[index]
    try:
        return dict(record)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return {}


def _gateway_context(
    context_id: str | None,
    cookie_value: str | None,
) -> tuple[dict, alas_gateway.GatewayRecord] | None:
    """Resolve an ALAS-origin cookie and revalidate its primary session."""
    record = alas_gateway.get_context_session(context_id, cookie_value)
    if not record:
        return None
    source_session = storage.get_session(record.session_id)
    user = storage.get_user(record.username)
    if (
        not source_session
        or _record_value(source_session, "username") != record.username
        or not user
        or str(_record_value(user, "role") or "") != record.role
        or not security.password_change_allowed(_record_dict(user))
        or not storage.user_is_active(user)
    ):
        alas_gateway.revoke_session(cookie_value)
        return None
    # Gateway contexts can outlive an object-level permission change.  Recheck
    # the bound device at every HTTP/WS entry so revoking view access (or
    # disabling/deleting the device) invalidates the existing context too.
    if record.device_id and not storage.user_can(record.username, record.device_id, "view"):
        alas_gateway.revoke_session(cookie_value)
        return None
    return dict(user), record


def _gateway_request_context(request: Request) -> tuple[dict, alas_gateway.GatewayRecord] | None:
    if not security.is_alas_origin_request(request):
        return None
    context_id = request.query_params.get(alas_gateway.ALAS_CONTEXT_QUERY)
    cookie = alas_gateway.cookie_name(context_id)
    return _gateway_context(context_id, request.cookies.get(cookie) if cookie else None)


def _gateway_websocket_context(websocket: WebSocket) -> tuple[dict, alas_gateway.GatewayRecord] | None:
    if not security.is_alas_origin_request(websocket):
        return None
    context_id = websocket.query_params.get(alas_gateway.ALAS_CONTEXT_QUERY)
    cookie = alas_gateway.cookie_name(context_id)
    return _gateway_context(context_id, websocket.cookies.get(cookie) if cookie else None)


def gateway_websocket_session_valid(
    websocket: WebSocket,
    expected_record: alas_gateway.GatewayRecord,
) -> bool:
    """Revalidate an ALAS-origin socket against its original gateway context."""
    context = _gateway_websocket_context(websocket)
    if not context:
        return False
    user, record = context
    return bool(
        record.session_id == expected_record.session_id
        and record.username == expected_record.username
        and record.role == expected_record.role
        and record.config_name == expected_record.config_name
        and record.device_id == expected_record.device_id
        and record.context_id == expected_record.context_id
        and user.get("username") == expected_record.username
        and user.get("role") == expected_record.role
    )


def _alas_gateway_url(ticket: str) -> str:
    origin = alas_gateway.configured_origin()
    query = urlencode({"ticket": ticket})
    return f"{origin}/alas/gateway?{query}" if origin else f"/alas/gateway?{query}"


def _gateway_iframe_src(
    request: Request,
    user: dict,
    *,
    config_name: str = "",
    device_id: str | None = None,
    fallback: str,
) -> str:
    if not alas_gateway.enabled():
        return fallback
    session = security.get_current_session(request)
    if not session:
        return fallback
    ticket = alas_gateway.issue_ticket(
        session_id=session["sid"],
        username=user["username"],
        role=user["role"],
        config_name=config_name,
        device_id=device_id or "",
    )
    return _alas_gateway_url(ticket)


__all__ = [
    "_alas_gateway_url",
    "_gateway_context",
    "_gateway_iframe_src",
    "_gateway_request_context",
    "_gateway_websocket_context",
    "gateway_websocket_session_valid",
]
