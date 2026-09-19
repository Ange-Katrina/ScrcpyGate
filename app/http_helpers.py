"""HTTP boundary helpers shared by route modules and middleware."""

from __future__ import annotations

import asyncio
import inspect
import json
import re
from collections.abc import Awaitable, Callable
from functools import lru_cache
from urllib.parse import parse_qs

from fastapi import HTTPException, Request, WebSocket
from fastapi.responses import HTMLResponse, RedirectResponse

from . import alas, i18n, security, storage
from .alas_embed_content import denied_page_html
from .alas_embed_policy import config_query_values
from .account_access import account_connections
from .video_options import VideoOptionError
from .http_body import (
    REQUEST_BODY_IDLE_TIMEOUT_SECONDS as _REQUEST_BODY_IDLE_TIMEOUT_SECONDS,
    RequestBodyIdleTimeout,
    RequestBodyTooLarge,
    read_request_body_limited,
)


WebSocketVerifier = Callable[[], bool | Awaitable[bool]]


ALAS_EMBED_DENIED_MESSAGE_KEYS = {
    "missing binding": "alas.denied.reason.missing_binding",
    "missing_binding": "alas.denied.reason.missing_binding",
    "missing request config": "alas.denied.reason.missing_request_config",
    "missing_request_config": "alas.denied.reason.missing_request_config",
    "missing device context": "alas.denied.reason.missing_device_context",
    "missing_device_context": "alas.denied.reason.missing_device_context",
    "invalid body": "alas.denied.reason.invalid_body",
    "invalid_body": "alas.denied.reason.invalid_body",
    "config mismatch": "alas.denied.reason.config_mismatch",
    "config path mismatch": "alas.denied.reason.config_mismatch",
    "management path denied": "alas.denied.reason.management_path_denied",
    "restricted user entry denied": "alas.denied.reason.restricted_user_entry_denied",
    "alas settings denied": "alas.denied.reason.alas_settings_denied",
    "run permission denied": "alas.denied.reason.run_permission_denied",
    "edit permission denied": "alas.denied.reason.edit_permission_denied",
}
ALAS_EMBED_DENIED_DEFAULT_MESSAGE_KEY = "alas.denied.reason.default"
SERVER_ERROR_MESSAGE_KEYS = {
    "invalid_expires_at": "server.error.invalid_expires_at",
    "invalid_extension_days": "server.error.invalid_extension_days",
    "invalid_username": "server.error.invalid_username",
    "invalid_role": "server.error.invalid_role",
    "password_required": "server.error.password_required",
    "last_admin_required": "server.error.last_admin_required",
    "last_permanent_admin_required": "server.error.last_permanent_admin_required",
    "invalid_user": "server.error.user_not_found",
    "current_password_invalid": "server.error.current_password_incorrect",
    "new_password_must_be_different": "server.error.new_password_different",
    "invalid_config_name": "server.error.invalid_config_name",
    "invalid_alas_binding": "server.error.invalid_alas_binding",
    "invalid_device": "server.error.invalid_device",
    "invalid_login_guard_config": "server.error.invalid_request",
    "invalid_login_guard_field": "server.error.invalid_request",
    "invalid_login_guard_boolean": "server.error.invalid_boolean",
    "invalid_login_guard_value": "server.error.invalid_guard_value",
    "device_view_permission_required": "server.error.device_view_permission_required",
    "account_expired_or_missing": "server.error.account_expired_or_missing",
    "Password is too common": "server.error.password_too_common",
    "Password must not match username": "server.error.password_matches_username",
    "invalid ALAS token": "server.error.invalid_alas_token",
    "ALAS token encryption is not configured": "server.error.alas_token_encryption",
    "ALAS token encryption is unavailable": "server.error.alas_token_encryption",
    "invalid ALAS runtime URL": "server.error.invalid_alas_runtime_url",
    "invalid ALAS runtime URL scheme": "server.error.invalid_alas_runtime_scheme",
    "invalid ALAS runtime URL host": "server.error.invalid_alas_runtime_host",
    "ALAS runtime URL must not include credentials": "server.error.alas_runtime_credentials",
    "ALAS runtime URL must not include path, params, query or fragment": "server.error.alas_runtime_path",
    "invalid ALAS runtime URL port": "server.error.invalid_alas_runtime_port",
    "ALAS control is disabled": "server.status.alas_control_disabled",
    "ALAS API token is not configured": "server.status.alas_token_missing",
}
PASSWORD_MIN_LENGTH_ERROR_RE = re.compile(r"^Password must be at least (?P<min>\d+) characters$")
API_REQUEST_BODY_MAX_BYTES = security.env_int(
    "API_REQUEST_BODY_MAX_BYTES",
    1024 * 1024,
    1024,
    16 * 1024 * 1024,
)
REQUEST_BODY_TOO_LARGE_DETAIL = "Request body too large"
REQUEST_BODY_IDLE_TIMEOUT_DETAIL = "Request body idle timeout"
REQUEST_BODY_IDLE_TIMEOUT_SECONDS = _REQUEST_BODY_IDLE_TIMEOUT_SECONDS
PUBLIC_ERROR_DEFAULT_KEY = "server.error.request_failed"


@lru_cache(maxsize=64)
def _locale_vary_header(existing: str) -> str:
    values = [item.strip() for item in existing.split(",") if item.strip()]
    names = {item.lower() for item in values}
    for value in ("Cookie", "Accept-Language"):
        if value.lower() not in names:
            values.append(value)
            names.add(value.lower())
    return ", ".join(values)


def server_error_message(error: object) -> str:
    """Return the legacy mapped message without changing its public symbol."""
    text = str(error or "").strip()
    key = SERVER_ERROR_MESSAGE_KEYS.get(text)
    if key:
        return i18n.translate(key)
    match = PASSWORD_MIN_LENGTH_ERROR_RE.fullmatch(text)
    if match:
        return i18n.translate("server.error.password_min_length", min=match.group("min"))
    if text.startswith(("ALAS Runtime unreachable", "ALAS API unreachable")):
        return i18n.translate("server.status.alas_runtime_unreachable")
    return text


def public_error_detail(error: object) -> str:
    """Return a localized, non-diagnostic message suitable for HTTP responses."""
    text = str(error or "").strip()
    if not text:
        return ""
    mapped = server_error_message(text)
    if mapped != text:
        return mapped
    return i18n.translate(PUBLIC_ERROR_DEFAULT_KEY)


def video_option_error_message(error: VideoOptionError) -> str:
    return error.localized()


def redirect_to_login(request: Request):
    if not security.get_current_user(request):
        return RedirectResponse("/login", status_code=302)
    return None


async def _read_request_body_limited(request: Request, limit: int = API_REQUEST_BODY_MAX_BYTES) -> bytes:
    try:
        return await read_request_body_limited(
            request,
            limit,
            idle_timeout=REQUEST_BODY_IDLE_TIMEOUT_SECONDS,
        )
    except RequestBodyTooLarge as exc:
        raise HTTPException(status_code=413, detail=REQUEST_BODY_TOO_LARGE_DETAIL) from exc
    except RequestBodyIdleTimeout as exc:
        raise HTTPException(status_code=408, detail=REQUEST_BODY_IDLE_TIMEOUT_DETAIL) from exc


async def parse_body(request: Request) -> dict:
    body = await _read_request_body_limited(request)
    ctype = request.headers.get("content-type", "")
    if "application/json" in ctype:
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, TypeError) as exc:
            raise HTTPException(
                status_code=400,
                detail=i18n.translate("server.error.invalid_json"),
            ) from exc
        if not isinstance(payload, dict):
            raise HTTPException(
                status_code=400,
                detail=i18n.translate("server.error.invalid_json_object"),
            )
        return payload
    form = parse_qs(body.decode("utf-8", "ignore"), keep_blank_values=True)
    return {key: values[-1] if values else "" for key, values in form.items()}


def parse_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def user_payload(user: dict) -> dict:
    payload = {
        "username": user["username"],
        "role": user["role"],
        "is_admin": user["role"] == "admin",
        "video_mode": "normal",
        "must_change_password": bool(user.get("must_change_password")),
        "last_login_at": user.get("last_login_at"),
        "last_login_ip": user.get("last_login_ip") or "",
        "enabled": bool(user.get("enabled", 1)),
        # 每个账号各自的 ALAS 可见性（有些账号用不上 ALAS，由管理员在用户列表里设置）。
        # 只影响界面显隐，不参与任何权限判定。
        "alas_visible": bool(user.get("alas_visible", 1)),
    }
    payload.update(storage.user_expiration_payload(user))
    return payload


async def diagnose_websocket_session(websocket: WebSocket) -> str:
    """握手前先只读诊断会话状态。

    必须在 ``security.websocket_user()`` **之前**调用：那把校验发现账户到期就会
    立刻回收会话（删行），之后再也问不出原因。
    """
    try:
        info = await asyncio.to_thread(storage.describe_session, security.websocket_session_id(websocket))
        return str(info.get("state") or "missing")
    except Exception:
        return "missing"


async def reject_unauthenticated_websocket(websocket: WebSocket, target_id: str, *, state: str | None = None) -> None:
    """未通过鉴权的 WebSocket 统一拒绝：区分「没登录」与「账户不可用」。

    账户到期/停用/被删用 4403 + ``account expired``（与在线撤销路径同一套语义），
    待改密用 4403 + ``password_change_required``，其余（无会话/会话过期）保持
    4401 + ``authentication_required``。审计里因此不再把到期写成「未登录」。
    ``state`` 可以由 :func:`diagnose_websocket_session` 提前取好。
    """
    from .services.audit_service import audit_websocket_event

    if state is None:
        state = await diagnose_websocket_session(websocket)
    if state in ("account_expired", "account_disabled", "account_missing"):
        await audit_websocket_event(
            websocket,
            None,
            "websocket_access",
            outcome="denied",
            reason="account_expired",
            target_id=target_id,
        )
        await websocket.close(code=4403, reason="account expired")
        return
    if state == "password_change_required":
        await audit_websocket_event(
            websocket,
            None,
            "websocket_access",
            outcome="denied",
            reason="password_change_required",
            target_id=target_id,
        )
        await websocket.close(code=4403, reason="password change required")
        return
    await audit_websocket_event(
        websocket,
        None,
        "websocket_access",
        outcome="denied",
        reason="authentication_required",
        target_id=target_id,
    )
    await websocket.close(code=4401)


async def register_current_user_websocket(
    websocket: WebSocket,
    user: dict,
    *,
    session_id: str | None = None,
    device_id: str | None = None,
    verifier: WebSocketVerifier | None = None,
) -> bool:
    """Register a socket, then recheck its session before accepting traffic."""
    from .services.audit_service import audit_websocket_event

    username = str(user.get("username") or "").strip()
    normalized_device_id = str(device_id or "").strip() or None
    await account_connections.register(
        username, websocket, session_id,
        device_id=normalized_device_id, source_ip=security.client_ip(websocket),
    )
    try:
        if verifier is not None:
            verified = verifier()
            if inspect.isawaitable(verified):
                verified = await verified
        elif session_id is not None:
            verified = await asyncio.to_thread(
                security.websocket_session_valid,
                websocket,
                username,
                session_id,
            )
        else:
            verified = security.get_current_user(websocket)
    except Exception:
        verified = False
    handshake = security.websocket_access_decision(websocket)
    if not handshake.allowed:
        await account_connections.unregister(username, websocket, session_id)
        from starlette.websockets import WebSocketState

        if websocket.application_state is not WebSocketState.DISCONNECTED:
            await websocket.close(code=4403, reason=handshake.reason or "access denied")
        return False
    if verified:
        return True
    if session_id is None:
        await account_connections.unregister(username, websocket)
    else:
        await account_connections.unregister(username, websocket, session_id)
    await audit_websocket_event(
        websocket,
        user,
        "websocket_access",
        outcome="denied",
        reason="account_expired",
        target_id=websocket.url.path,
    )
    await websocket.close(code=4403, reason="account expired")
    return False


def audit_detail(request: Request, extra: str = "") -> str:
    ip = security.client_ip(request)
    ua = (request.headers.get("user-agent") or "").replace("\r", " ").replace("\n", " ")[:180]
    parts = [f"ip={ip}"]
    if ua:
        parts.append(f"ua={ua}")
    if extra:
        parts.append(extra)
    return " ".join(parts)


def alas_embed_route_class(path: str) -> str:
    first_segment = str(path or "").replace("\\", "/").strip("/").split("/", 1)[0].lower()
    if first_segment in {"api", "ajax", "pywebio", "pywebio_static", "static", "assets", "config"}:
        return first_segment
    return "root" if not first_segment else "other"


def alas_embed_denial_detail(request: Request, reason: str, path: str = "") -> str:
    safe_reason = str(reason or "unknown").replace("\r", " ").replace("\n", " ")[:80]
    return audit_detail(request, f"reason={safe_reason} route={alas_embed_route_class(path)}")


def alas_embed_denied_message(reason: str) -> str:
    message_key = ALAS_EMBED_DENIED_MESSAGE_KEYS.get(
        str(reason or ""),
        ALAS_EMBED_DENIED_DEFAULT_MESSAGE_KEY,
    )
    return i18n.translate(message_key)


def alas_embed_return_url(binding: dict | None) -> str:
    config_name = ""
    if binding and binding.get("config_name"):
        try:
            config_name = alas.sanitize_config_name(binding.get("config_name"))
        except ValueError:
            config_name = ""
    if config_name:
        params = {"config": config_name}
        if binding and binding.get("device_id"):
            params["device_id"] = storage.public_device_id(binding["device_id"])
        from urllib.parse import urlencode

        return f"/alas/embed/proxy/?{urlencode(params)}"
    return "/alas/embed/"


def requested_alas_config(query_params) -> str | None:
    values = config_query_values(query_params)
    unique = {value for value in values}
    return values[0] if len(unique) == 1 else None


def alas_embed_denied_html_response(request: Request, binding: dict | None, status_code: int, message: str):
    accept = request.headers.get("accept", "")
    if request.method.upper() not in ("GET", "HEAD") or "text/html" not in accept.lower():
        return None
    return HTMLResponse(
        denied_page_html(message, alas_embed_return_url(binding), seconds=3),
        status_code=status_code,
    )


def alas_embed_reason_code(reason: str) -> str:
    return {
        "missing binding": "missing_binding",
        "missing request config": "missing_request_config",
        "missing device context": "missing_device_context",
        "invalid body": "invalid_body",
        "config mismatch": "config_mismatch",
        "config path mismatch": "config_path_mismatch",
        "management path denied": "management_path_denied",
        "restricted user entry denied": "restricted_user_entry_denied",
        "alas settings denied": "alas_settings_denied",
        "run permission denied": "run_permission_denied",
        "edit permission denied": "edit_permission_denied",
    }.get(str(reason or ""), "denied")
