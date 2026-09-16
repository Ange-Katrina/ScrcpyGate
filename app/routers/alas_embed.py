"""ALAS embedded shell and HTTP/WebSocket proxy routes."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import replace
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request, Response, WebSocket
from fastapi.responses import HTMLResponse

from .. import alas, alas_gateway, i18n, security, storage
from .. import alas_embed_content as embed_content
from .. import alas_embed_policy as embed_policy
from .. import alas_embed_transport as embed_transport
from ..account_access import account_connections
from ..http_helpers import (
    alas_embed_denial_detail,
    alas_embed_denied_html_response,
    alas_embed_denied_message,
    alas_embed_reason_code,
    alas_embed_route_class,
    redirect_to_login,
    register_current_user_websocket,
    requested_alas_config,
)
from ..services.audit_service import audit_request, audit_websocket_event
from ..services.alas_service import (
    alas_binding_for_user,
    alas_device_for_user,
    log_alas_embed_denied,
    log_alas_websocket_close,
)
from ..services.gateway_service import (
    _gateway_iframe_src,
    _gateway_request_context,
    _gateway_websocket_context,
    gateway_websocket_session_valid,
)

log = logging.getLogger("webscrcpy.main")
router = APIRouter()


def _static_cache_device_context(decision, binding: dict | None) -> str:
    """Keep device-scoped bootstrap assets isolated before ``device_id`` repeats."""
    context = str(getattr(decision, "device_context", "") or "").strip()
    if context or not getattr(decision, "filtered", False) or not binding:
        return context
    bound_device = str(binding.get("device_id") or "").strip()
    return storage.public_device_id(bound_device) if bound_device else ""

@router.get("/alas/embed")
@router.get("/alas/embed/", response_class=HTMLResponse)
async def alas_embed_page(request: Request):
    """返回 ALAS 原页面 iframe 外壳入口。"""
    redirect = redirect_to_login(request)
    if redirect:
        return redirect
    user = security.require_active_user(request)
    requested = str(request.query_params.get("config") or "").strip()
    device_id = await asyncio.to_thread(alas_device_for_user, user, request.query_params.get("device_id"))
    binding = await asyncio.to_thread(
        alas_binding_for_user,
        user,
        allow_admin_global=user.get("role") == "admin",
        config_name=requested or None,
        device_id=device_id,
    )
    if user.get("role") != "admin" and binding and device_id is None and binding.get("device_id"):
        bound_device_id = str(binding.get("device_id") or "").strip()
        if await asyncio.to_thread(storage.user_can, user["username"], bound_device_id, "view"):
            device_id = bound_device_id
        else:
            binding = None
    if user.get("role") != "admin" and not binding:
        audit_request(
            request,
            user,
            "alas_embed_denied",
            outcome="denied",
            reason="missing_binding",
            severity="warning",
            target_type="alas_shell",
            target_id="user",
            detail=alas_embed_denial_detail(request, "missing_binding", "/alas/embed/"),
        )
        raise HTTPException(status_code=403, detail=alas_embed_denied_message("missing_binding"))
    if not binding:
        raise HTTPException(
            status_code=400 if requested else 403,
            detail=i18n.translate("server.error.invalid_alas_config"),
        )
    if user.get("role") == "admin":
        config_name = alas.sanitize_config_name(binding.get("config_name"))
        proxy_params = {"config": config_name}
        if device_id:
            proxy_params["device_id"] = storage.public_device_id(device_id)
        fallback_src = f"/alas/embed/proxy/?{urlencode(proxy_params)}" if requested or device_id else "/alas/embed/proxy/"
        iframe_src = _gateway_iframe_src(
            request,
            user,
            config_name=config_name if requested or device_id else "",
            device_id=device_id,
            fallback=fallback_src,
        )
        audit_request(
            request,
            user,
            "alas_embed_open",
            target_type="alas_config" if requested else "alas_shell",
            target_id=config_name if requested else "admin",
        )
        return HTMLResponse(
            embed_content.embed_shell_html(
                i18n.translate("alas.shell.admin_title"),
                iframe_src,
                i18n.translate("alas.shell.admin_message"),
            )
        )
    config_name = alas.sanitize_config_name(binding.get("config_name"))
    proxy_params = {"config": config_name}
    if device_id:
        proxy_params["device_id"] = storage.public_device_id(device_id)
    fallback_src = f"/alas/embed/proxy/?{urlencode(proxy_params)}"
    iframe_src = _gateway_iframe_src(
        request,
        user,
        config_name=config_name,
        device_id=device_id,
        fallback=fallback_src,
    )
    audit_request(
        request,
        user,
        "alas_embed_open",
        target_type="alas_config",
        target_id=config_name,
    )
    return HTMLResponse(
        embed_content.embed_shell_html(
            i18n.translate("alas.shell.user_title", config=config_name),
            iframe_src,
            i18n.translate("alas.shell.bound_message", config=config_name),
        )
    )


def _denied_proxy_response(request: Request, user: dict, binding: dict | None, decision, path: str):
    """Audit one denied proxy request and return or raise its response."""
    reason_code = alas_embed_reason_code(decision.reason)
    denied_message = alas_embed_denied_message(decision.reason)
    log_alas_embed_denied(user, binding, "http", path or "/", reason_code)
    audit_request(
        request,
        user,
        "alas_embed_denied",
        outcome="denied",
        reason=reason_code,
        severity="warning",
        target_type="alas_proxy_route",
        target_id=alas_embed_route_class(path),
        detail=alas_embed_denial_detail(request, reason_code, path or "/"),
    )
    denied_html = alas_embed_denied_html_response(request, binding, decision.status_code, denied_message)
    if denied_html is not None:
        return denied_html
    raise HTTPException(status_code=decision.status_code, detail=denied_message)


async def _require_alas_proxy_enabled(request: Request, user: dict, binding: dict | None, path: str) -> dict:
    """Return ALAS settings or fail closed while the Runtime is unusable."""
    settings = await asyncio.to_thread(alas.public_settings)
    raw_enabled = await asyncio.to_thread(storage.get_setting, "alas_enabled", "false")
    if not settings.get("enabled") or str(raw_enabled).strip().lower() not in ("1", "true", "yes", "on"):
        log_alas_embed_denied(user, binding, "http", path or "/", "disabled")
        audit_request(
            request,
            user,
            "alas_embed_denied",
            outcome="denied",
            reason="disabled",
            severity="warning",
            target_type="alas_proxy_route",
            target_id=alas_embed_route_class(path),
            detail=alas_embed_denial_detail(request, "disabled", path or "/"),
        )
        raise HTTPException(status_code=400, detail=i18n.translate("server.status.alas_control_disabled"))
    if not settings.get("base_url"):
        log_alas_embed_denied(user, binding, "http", path or "/", "unconfigured")
        audit_request(
            request,
            user,
            "alas_embed_denied",
            outcome="failure",
            reason="unconfigured",
            severity="error",
            target_type="alas_proxy_route",
            target_id=alas_embed_route_class(path),
            detail=alas_embed_denial_detail(request, "unconfigured", path or "/"),
        )
        raise HTTPException(status_code=502, detail=i18n.translate("server.status.alas_runtime_not_configured"))
    return settings


def _cached_static_proxy_response(base_url, path: str, decision, cache_device_context: str):
    cached = embed_transport.static_proxy_cache_get(
        base_url,
        path,
        bool(decision and decision.filtered),
        config_name=decision.config_name,
        device_context=cache_device_context,
        gateway_context=decision.gateway_context,
    )
    if cached is None:
        return None
    cached_body, cached_status, cached_content_type = cached
    response = Response(
        content=cached_body,
        status_code=cached_status,
        headers={"Content-Type": cached_content_type} if cached_content_type else {},
    )
    response.headers["Cache-Control"] = "private, max-age=3600"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


async def _forward_proxy_response(
    request: Request,
    user: dict,
    settings: dict,
    path: str,
    decision,
    body: bytes,
    readonly_static_request: bool,
    cacheable_static: bool,
    cache_device_context: str,
    gateway_context,
):
    try:
        response = await embed_transport.proxy_http_request(request, settings.get("base_url"), path, decision, body=body)
        if readonly_static_request:
            response.headers["Cache-Control"] = "private, max-age=3600"
            response.headers["X-Content-Type-Options"] = "nosniff"
            if cacheable_static:
                embed_transport.static_proxy_cache_put(
                    settings.get("base_url"),
                    path,
                    response.body,
                    response.status_code,
                    response.headers.get("Content-Type", ""),
                    bool(decision and decision.filtered),
                    config_name=decision.config_name,
                    device_context=cache_device_context,
                    gateway_context=decision.gateway_context,
                )
        if gateway_context:
            public = security.public_base_url()
            frame_ancestors = security.normalize_origin(public.geturl()) if public else "'self'"
            response.headers["Content-Security-Policy"] = f"frame-ancestors {frame_ancestors}"
            response.headers["Referrer-Policy"] = "no-referrer"
        return response
    except HTTPException as exc:
        if exc.status_code == 502:
            response_too_large = exc.detail == embed_transport.UPSTREAM_RESPONSE_TOO_LARGE_DETAIL
            response_unfilterable = exc.detail == i18n.translate("server.proxy.encoded_response_unfilterable")
            failure_reason = (
                "upstream_response_too_large"
                if response_too_large
                else "encoded_response_unfilterable"
                if response_unfilterable
                else "upstream_unreachable"
            )
            audit_request(
                request,
                user,
                "alas_embed_proxy_failed",
                outcome="failure",
                reason=failure_reason,
                severity="error",
                target_type="alas_proxy_route",
                target_id=alas_embed_route_class(path),
                detail=alas_embed_denial_detail(request, failure_reason, path or "/"),
            )
            raise HTTPException(
                status_code=502,
                detail=(
                    embed_transport.UPSTREAM_RESPONSE_TOO_LARGE_DETAIL
                    if response_too_large
                    else exc.detail
                    if response_unfilterable
                    else i18n.translate("server.status.alas_runtime_unreachable")
                ),
            ) from exc
        raise


@router.api_route("/alas/embed/proxy", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
@router.api_route("/alas/embed/proxy/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
async def alas_embed_proxy(request: Request, path: str = ""):
    """执行 ALAS HTTP 代理权限检查并转发到 Runtime。"""
    gateway_context = _gateway_request_context(request)
    if security.is_alas_origin_request(request) and not gateway_context:
        raise HTTPException(status_code=401, detail=i18n.translate("server.security.login_required"))
    if not gateway_context and alas_gateway.enabled():
        # gateway 模式下主站 origin 不再是合法代理入口：阻断被诱导的顶层导航/跨站请求
        # 把上游 JS 带回主站 origin 执行（同站子域/端口场景下 SameSite=strict 无法兜底）。
        fetch_dest = request.headers.get("sec-fetch-dest", "").strip().lower()
        fetch_site = request.headers.get("sec-fetch-site", "").strip().lower()
        if fetch_dest != "iframe" or fetch_site != "same-origin":
            raise HTTPException(status_code=404, detail="Not Found")
    body = b""
    parsed_body = None
    if request.method.upper() not in embed_policy.SAFE_METHODS:
        if gateway_context:
            expected_origin = alas_gateway.configured_origin()
            if security.normalize_origin(request.headers.get("origin", "")) != expected_origin:
                raise HTTPException(status_code=403, detail=i18n.translate("server.security.forbidden"))
        else:
            security.verify_csrf(request)
        body = await embed_transport.read_limited_request_body(request)
        parsed_body = embed_transport.parse_limited_body(body, request.headers.get("content-type", ""))
    if gateway_context:
        user, gateway_record = gateway_context
    else:
        user = security.require_active_user(request)
        gateway_record = None
    requested_device_ref = request.query_params.get("device_id")
    if gateway_record and gateway_record.device_id:
        requested_device_id = (
            await asyncio.to_thread(alas_device_for_user, user, requested_device_ref)
            if requested_device_ref
            else None
        )
        if requested_device_id and requested_device_id != gateway_record.device_id:
            raise HTTPException(status_code=403, detail=i18n.translate("server.security.forbidden"))
        device_id = gateway_record.device_id
    else:
        device_id = await asyncio.to_thread(alas_device_for_user, user, requested_device_ref)
    query_params = {key: request.query_params.getlist(key) for key in request.query_params.keys()}
    requested_config = requested_alas_config(request.query_params)
    if gateway_record and gateway_record.config_name:
        if requested_config and requested_config != gateway_record.config_name:
            raise HTTPException(status_code=403, detail=i18n.translate("server.security.forbidden"))
        query_params.setdefault("config", [gateway_record.config_name])
        requested_config = gateway_record.config_name
    binding = await asyncio.to_thread(
        alas_binding_for_user,
        user,
        allow_admin_global=user.get("role") == "admin",
        config_name=requested_config,
        device_id=device_id,
    )
    if not binding and user.get("role") != "admin" and device_id is None:
        binding = await asyncio.to_thread(alas_binding_for_user, user)
    readonly_static_request = embed_policy.is_readonly_static_request(request.method, path)
    missing_device_context = bool(
        user.get("role") != "admin"
        and binding
        and binding.get("device_id")
        and device_id is None
        and not readonly_static_request
    )
    decision = (
        embed_policy.ProxyDecision(
            allowed=False,
            status_code=403,
            config_name=str(binding.get("config_name") or "") if binding else "",
            reason="missing device context",
        )
        if missing_device_context
        else embed_policy.proxy_decision(
            user,
            binding,
            path,
            query_params,
            method=request.method,
            body=parsed_body,
        )
    )
    if decision.allowed and (device_id or gateway_record):
        gateway_config = gateway_record.config_name if gateway_record else ""
        decision = replace(
            decision,
            config_name=gateway_config or decision.config_name,
            device_context=storage.public_device_id(device_id) if device_id else "",
            gateway_context=gateway_record.context_id if gateway_record else "",
            pin_config=bool(gateway_config),
        )
    if decision.allowed and not readonly_static_request and embed_policy.feature_gate_applies(user):
        # 后台「配置开关」只作用于普通用户：管理员始终看到完整的 ALAS 页面。
        # 只对非静态请求取一次（静态资源无需注入）。
        decision = replace(
            decision,
            hidden_matches=tuple(await asyncio.to_thread(alas.hidden_feature_targets)),
        )
    if not decision.allowed:
        return _denied_proxy_response(request, user, binding, decision, path)
    settings = await _require_alas_proxy_enabled(request, user, binding, path)
    cacheable_static = readonly_static_request and request.method.upper() in ("GET", "HEAD")
    cache_device_context = _static_cache_device_context(decision, binding)
    if cacheable_static:
        cached_response = _cached_static_proxy_response(
            settings.get("base_url"), path, decision, cache_device_context
        )
        if cached_response is not None:
            return cached_response
    return await _forward_proxy_response(
        request,
        user,
        settings,
        path,
        decision,
        body,
        readonly_static_request,
        cacheable_static,
        cache_device_context,
        gateway_context,
    )


@router.websocket("/alas/embed/proxy")
@router.websocket("/alas/embed/proxy/{path:path}")
async def alas_embed_websocket(websocket: WebSocket, path: str = ""):
    """执行 ALAS WebSocket 代理入口权限检查并转发到 Runtime。"""
    connection_id = uuid.uuid4().hex[:12]
    if not security.websocket_origin_allowed(websocket):
        log_alas_websocket_close(connection_id, None, "origin_denied", 4403)
        await audit_websocket_event(
            websocket,
            None,
            "websocket_access",
            outcome="denied",
            reason="origin_denied",
            target_id="/alas/embed/proxy/*",
        )
        await websocket.close(code=4403)
        return
    gateway_context = _gateway_websocket_context(websocket)
    if security.is_alas_origin_request(websocket):
        if security.normalize_origin(websocket.headers.get("origin", "")) != alas_gateway.configured_origin():
            gateway_context = None
        if not gateway_context:
            log_alas_websocket_close(connection_id, None, "authentication_required", 1008)
            await websocket.close(code=1008)
            return
    elif alas_gateway.enabled():
        # gateway 模式下主站 origin 不提供嵌入代理 WS，防上游脚本用绝对主站 URL 绕过注入补丁。
        log_alas_websocket_close(connection_id, None, "main_origin_disabled", 4403)
        await websocket.close(code=4403)
        return
    user = gateway_context[0] if gateway_context else security.get_current_user(websocket)
    if user and not security.password_change_allowed(dict(user), websocket.url.path):
        user = None
    gateway_record = gateway_context[1] if gateway_context else None
    if not user:
        log_alas_websocket_close(connection_id, None, "authentication_required", 1008)
        await audit_websocket_event(
            websocket,
            None,
            "websocket_access",
            outcome="denied",
            reason="authentication_required",
            target_id="/alas/embed/proxy/*",
        )
        await websocket.close(code=1008)
        return
    requested_device_ref = str(websocket.query_params.get("device_id") or "").strip()
    requested_device_id = (
        await asyncio.to_thread(storage.resolve_device_ref, requested_device_ref)
        if requested_device_ref
        else None
    )
    if gateway_record and gateway_record.device_id:
        if requested_device_id and requested_device_id != gateway_record.device_id:
            await websocket.close(code=1008)
            return
        device_id = gateway_record.device_id
    else:
        device_id = requested_device_id
    if requested_device_ref and (
        not device_id or not await asyncio.to_thread(storage.user_can, user["username"], device_id, "view")
    ):
        log_alas_websocket_close(connection_id, user, "device_permission_denied", 1008)
        await audit_websocket_event(
            websocket,
            user,
            "alas_embed_ws_denied",
            outcome="denied",
            reason="device_permission_denied",
            target_type="alas_proxy_route",
            target_id=alas_embed_route_class(path),
            metadata={"connection_id": connection_id},
        )
        await websocket.close(code=1008)
        return
    query_params = {key: websocket.query_params.getlist(key) for key in websocket.query_params.keys()}
    requested_config = requested_alas_config(websocket.query_params)
    if gateway_record and gateway_record.config_name:
        if requested_config and requested_config != gateway_record.config_name:
            await websocket.close(code=1008)
            return
        query_params.setdefault("config", [gateway_record.config_name])
        requested_config = gateway_record.config_name
    binding = await asyncio.to_thread(
        alas_binding_for_user,
        user,
        allow_admin_global=user.get("role") == "admin",
        config_name=requested_config,
        device_id=device_id,
    )
    if not binding and user.get("role") != "admin" and device_id is None:
        binding = await asyncio.to_thread(alas_binding_for_user, user)
    missing_device_context = bool(
        user.get("role") != "admin"
        and binding
        and binding.get("device_id")
        and device_id is None
    )
    decision = (
        embed_policy.ProxyDecision(
            allowed=False,
            status_code=403,
            config_name=str(binding.get("config_name") or "") if binding else "",
            reason="missing device context",
        )
        if missing_device_context
        else embed_policy.proxy_decision(user, binding, path, query_params, method="WEBSOCKET")
    )
    if decision.allowed and (device_id or gateway_record):
        gateway_config = gateway_record.config_name if gateway_record else ""
        decision = replace(
            decision,
            config_name=gateway_config or decision.config_name,
            device_context=storage.public_device_id(device_id) if device_id else "",
            gateway_context=gateway_record.context_id if gateway_record else "",
            pin_config=bool(gateway_config),
        )
    if decision.allowed and embed_policy.feature_gate_applies(user):
        # 后台「配置开关」在服务端也生效：关掉的功能不下发它的行，其 callback 也随之被拒。
        # 只对普通用户取一次（管理员始终看到完整页面）。
        decision = replace(
            decision,
            hidden_matches=tuple(await asyncio.to_thread(alas.hidden_feature_targets)),
        )
    if not decision.allowed:
        reason_code = alas_embed_reason_code(decision.reason)
        permission = "run" if reason_code == "run_permission_denied" else (
            "edit" if reason_code == "edit_permission_denied" else "restricted"
        )
        log_alas_websocket_close(connection_id, user, reason_code, 1008, permission=permission)
        await audit_websocket_event(
            websocket,
            user,
            "alas_embed_ws_denied",
            outcome="denied",
            reason=reason_code,
            target_type="alas_proxy_route",
            target_id=alas_embed_route_class(path),
            metadata={"connection_id": connection_id, "permission": permission},
        )
        await websocket.close(code=1008)
        return
    settings = await asyncio.to_thread(alas.public_settings)
    raw_enabled = await asyncio.to_thread(storage.get_setting, "alas_enabled", "false")
    raw_base_url = await asyncio.to_thread(storage.get_setting, "alas_base_url", "")
    if not settings.get("enabled") or str(raw_enabled).strip().lower() not in ("1", "true", "yes", "on"):
        log_alas_websocket_close(connection_id, user, "disabled", 1011, phase="configuration")
        await audit_websocket_event(
            websocket,
            user,
            "alas_embed_ws_denied",
            outcome="failure",
            reason="disabled",
            severity="error",
            target_type="alas_proxy_route",
            target_id=alas_embed_route_class(path),
            metadata={"connection_id": connection_id},
        )
        await websocket.close(code=1011)
        return
    if not raw_base_url.strip():
        log_alas_websocket_close(connection_id, user, "unconfigured", 1011, phase="configuration")
        await audit_websocket_event(
            websocket,
            user,
            "alas_embed_ws_denied",
            outcome="failure",
            reason="unconfigured",
            severity="error",
            target_type="alas_proxy_route",
            target_id=alas_embed_route_class(path),
            metadata={"connection_id": connection_id},
        )
        await websocket.close(code=1011)
        return

    async def refresh_binding():
        if not decision.filtered:
            return None

        def resolve_current_binding():
            if device_id and not storage.user_can(user.get("username", ""), device_id, "view"):
                return None
            return alas_binding_for_user(
                user,
                allow_admin_global=user.get("role") == "admin",
                config_name=decision.config_name,
                device_id=device_id,
            )

        return await asyncio.to_thread(resolve_current_binding)

    async def proxy_audit(action: str, **fields):
        metadata = dict(fields.pop("metadata", {}) or {})
        metadata["connection_id"] = connection_id
        await audit_websocket_event(
            websocket,
            user,
            action,
            target_type="alas_proxy_route",
            target_id=alas_embed_route_class(path),
            metadata=metadata,
            **fields,
        )

    username = user.get("username", "")
    session_id = (
        str(gateway_record.session_id or "").strip()
        if gateway_record
        else security.websocket_session_id(websocket)
    ) or None

    async def session_check() -> bool:
        if gateway_record:
            return await asyncio.to_thread(
                gateway_websocket_session_valid,
                websocket,
                gateway_record,
            )
        if session_id:
            return await asyncio.to_thread(
                security.websocket_session_valid,
                websocket,
                username,
                session_id,
            )
        return bool(security.get_current_user(websocket))

    if not await register_current_user_websocket(
        websocket,
        user,
        session_id=session_id,
        device_id=device_id,
        verifier=session_check,
    ):
        return
    try:
        await embed_transport.proxy_websocket(
            websocket,
            settings.get("base_url") or raw_base_url,
            path,
            decision,
            role=user.get("role", ""),
            connection_id=connection_id,
            authorization_check=refresh_binding if decision.filtered else None,
            session_check=session_check,
            audit_callback=proxy_audit,
        )
    finally:
        await account_connections.unregister(username, websocket, session_id)
