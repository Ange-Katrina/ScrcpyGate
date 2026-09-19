"""ASGI middleware used by the application factory."""

from __future__ import annotations

import asyncio
import logging
import re
import time

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.middleware.gzip import GZipMiddleware
from starlette.requests import HTTPConnection

from . import access_gate, access_log, alas_gateway, geo_access, i18n, security, storage
from .http_helpers import _locale_vary_header
from .logging_config import bind_log_context, log_event, normalize_request_id, reset_log_context
from .services.audit_service import audit_request, flush_request_audit_events, safe_log_path, should_audit_http_failure


log = logging.getLogger("webscrcpy.main")
STATIC_ASSET_VERSION_RE = re.compile(r"(?:[a-f0-9]{12}|[0-9]{1,8})")


class SelectiveGZipMiddleware(GZipMiddleware):
    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and str(scope.get("path") or "").startswith("/alas/embed/proxy"):
            await self.app(scope, receive, send)
            return
        await super().__call__(scope, receive, send)


class LocaleContextMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        token = i18n.set_current_locale(i18n.locale_from_request(HTTPConnection(scope)))
        try:
            await self.app(scope, receive, send)
        finally:
            i18n.reset_current_locale(token)


# 访问记录开关：带 TTL 的本地缓存，避免每个请求读一次 SQLite 设置。
_ACCESS_ENABLED_TTL_SECONDS = 10.0
_access_enabled_state = {"value": True, "checked_at": 0.0}


def _access_recording_enabled() -> bool:
    now = time.monotonic()
    state = _access_enabled_state
    if now - float(state["checked_at"]) < _ACCESS_ENABLED_TTL_SECONDS:
        return bool(state["value"])
    try:
        state["value"] = storage.access_log_enabled()
    except Exception:  # pragma: no cover - 读设置失败时保持上一次判定
        log.debug("access log setting read failed", exc_info=True)
    state["checked_at"] = now
    return bool(state["value"])


def _record_access(
    request: Request,
    *,
    status_code: int,
    route: str,
    duration_ms: float,
    source_ip: str,
) -> None:
    """把这一次请求交给访问记录队列（VIS）。

    绝不阻塞、绝不抛出：队列有界，写线程独立；本函数失败只记 debug 日志。
    拒绝请求（403/404/429/5xx，以及后续的 BAN/GEO 拒绝）也走这里，
    判定由 ``request.state.access_decision`` 提供。
    """
    try:
        path = request.url.path
        kind = access_log.classify_request(path, is_static=access_log.is_static_path(path))
        if not access_log.should_record(kind, status_code):
            return
        if not _access_recording_enabled():
            return
        from .runtime import runtime_for

        writer = getattr(runtime_for(request), "access_writer", None)
        if writer is None:
            return
        lookup = getattr(request.state, "access_geo_lookup", None)
        country = ""
        geo_epoch = None
        if callable(lookup):
            country, geo_epoch = lookup(source_ip)
        # 只有真正匹配到路由时才把路由模板当模板；未匹配（404）绝不用原始路径冒充模板，
        # 否则 /secret-abc... 这类扫描路径会原样落库。样本一律走 safe_log_path + 脱敏。
        route_object = request.scope.get("route")
        template = str(getattr(route_object, "path", "") or "") if route_object is not None else ""
        writer.submit(
            access_log.http_record(
                ts=int(time.time()),
                source_ip=source_ip,
                method=request.method,
                path=safe_log_path(path),
                route_template=template,
                status=status_code,
                duration_ms=duration_ms,
                request_id=str(getattr(request.state, "request_id", "") or ""),
                user_agent=request.headers.get("user-agent", ""),
                session=security.get_current_session(request),
                role=security.session_role(security.get_current_session(request)),
                decision=str(getattr(request.state, "access_decision", access_log.DECISION_ALLOW)),
                country=country,
                geo_db_epoch=geo_epoch,
                kind=kind,
            )
        )
    except Exception:  # pragma: no cover - 访问记录绝不能影响请求
        log.debug("access record submission failed", exc_info=True)


def _escape_html(value: object) -> str:
    return (
        str(value or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _gate_headers(decision) -> dict:
    """拒绝响应的头：临时封禁给出 ``Retry-After``，并且一律不可缓存。"""
    headers = {"Cache-Control": "no-store"}
    if decision.retry_after is not None:
        headers["Retry-After"] = str(max(1, int(decision.retry_after)))
    return headers


def record_websocket_access(
    websocket,
    *,
    accepted: bool,
    decision: str = access_log.DECISION_ALLOW,
    reason: str = "",
    status_code: int | None = None,
    duration_ms: float = 0.0,
    ended: bool = False,
) -> None:
    """记录一次 WebSocket **握手**结果（VIS）。

    WS 不经过 HTTP 中间件，所以长连接的接受/拒绝要在这里补记：被 BAN/GEO 拒绝的握手
    也必须能在访问记录里查到（状态码用 403/101 表达，``kind='websocket'``）。
    与 HTTP 路径同样约定：绝不阻塞、绝不抛出。
    """
    try:
        if not _access_recording_enabled():
            return
        from .runtime import runtime_for

        writer = getattr(runtime_for(websocket), "access_writer", None)
        if writer is None:
            return
        path = str(getattr(getattr(websocket, "url", None), "path", "") or "")
        source_ip = security.client_ip(websocket)
        country = ""
        geo_epoch = None
        country, geo_epoch = geo_access.access_geo_lookup(source_ip)
        writer.submit(
            access_log.http_record(
                ts=int(time.time()),
                source_ip=source_ip,
                method="GET",
                path=safe_log_path(path),
                route_template=str(getattr(websocket.scope.get("route"), "path", "") or ""),
                status=status_code if status_code is not None else (101 if accepted else 403),
                duration_ms=duration_ms,
                request_id=str(getattr(websocket.state, "request_id", "") or ""),
                user_agent=websocket.headers.get("user-agent", ""),
                session=security.get_current_session(websocket),
                role=security.session_role(security.get_current_session(websocket)),
                decision=decision if accepted else (decision or access_log.DECISION_BAN),
                country=country,
                geo_db_epoch=geo_epoch,
                kind="websocket_end" if ended else access_log.KIND_WS,
            )
        )
        if not accepted and reason:
            # 拒绝原因只进日志，不进访问记录明细（明细是脱敏的统计口径）。
            log.info("WS_HANDSHAKE_DENIED reason=%s path=%s", reason, safe_log_path(path))
    except Exception:  # pragma: no cover - 记录失败绝不能影响握手结果
        log.debug("websocket access record failed", exc_info=True)


class WebSocketAccessMiddleware:
    """Observe actual ASGI handshakes and recheck policy at the accept boundary."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "websocket":
            return await self.app(scope, receive, send)
        from starlette.websockets import WebSocketDisconnect

        connection = HTTPConnection(scope)
        started = time.monotonic()
        accepted = recorded = False
        close_code = 1006

        async def observed_receive():
            nonlocal close_code
            message = await receive()
            if message["type"] == "websocket.disconnect":
                close_code = int(message.get("code", 1006))
            return message

        async def observed_send(message):
            nonlocal accepted, recorded, close_code
            kind = message["type"]
            if kind == "websocket.accept":
                gate = security.websocket_access_decision(connection)
                if not gate.allowed:
                    await send({"type": "websocket.close", "code": 4403, "reason": gate.reason})
                    record_websocket_access(connection, accepted=False, decision=gate.decision, reason=gate.reason)
                    recorded = True
                    raise WebSocketDisconnect(4403)
                await send(message)
                accepted = recorded = True
                record_websocket_access(connection, accepted=True, decision=gate.decision)
                # A ban may be committed while the ASGI server sends accept.
                gate = security.websocket_access_decision(connection)
                if not gate.allowed:
                    close_code = 4403
                    await send({"type": "websocket.close", "code": 4403, "reason": gate.reason})
                    raise WebSocketDisconnect(4403)
                return
            if kind == "websocket.close":
                close_code = int(message.get("code", 1000))
            await send(message)
            if not recorded and kind in ("websocket.close", "websocket.http.response.start"):
                gate = security.websocket_access_decision(connection)
                record_websocket_access(
                    connection, accepted=False,
                    decision=gate.decision if not gate.allowed else "deny_auth",
                    status_code=int(message.get("status", 403)),
                )
                recorded = True

        try:
            await self.app(scope, observed_receive, observed_send)
        except WebSocketDisconnect:
            pass
        finally:
            if accepted:
                record_websocket_access(connection, accepted=True, ended=True, status_code=close_code,
                                        duration_ms=(time.monotonic() - started) * 1000)
            elif not recorded:
                record_websocket_access(connection, accepted=False, decision="error", status_code=500)


def _wants_html(request: Request) -> bool:
    """浏览器地址栏导航（Accept 里有 text/html）才给 HTML 拒绝页。

    接口/脚本调用一律保持 JSON：客户端要靠 code 做判断，塞 HTML 会让他们解析失败。
    """
    if request.method not in {"GET", "HEAD"}:
        return False
    accept = request.headers.get("accept", "")
    return "text/html" in accept.lower()


def _denial_response(request: Request, decision) -> JSONResponse | HTMLResponse:
    headers = _gate_headers(decision)
    detail = i18n.translate(decision.message_key) if decision.message_key else (decision.reason or decision.decision)
    if not _wants_html(request):
        return JSONResponse(decision.to_json(i18n.translate), status_code=decision.status_code, headers=headers)
    reason_text = str(detail or "")
    retry_text = ""
    if decision.retry_after is not None:
        minutes = max(1, round(int(decision.retry_after) / 60))
        retry_text = i18n.translate("server.error.ip_banned_retry_minutes").replace("{minutes}", str(minutes))
    # 不放任何内部信息（不显示封禁原因、操作者、名单详情），只说明「现在不能访问」。
    body = (
        "<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<meta name=\"deny-code\" content=\"{decision.reason or decision.decision}\">"
        "<title>" + _escape_html(i18n.translate("server.error.access_denied_title")) + "</title>"
        "<style>html,body{height:100%;margin:0}body{display:flex;align-items:center;justify-content:center;"
        "background:#f5f5f7;color:#1d1d1f;font:15px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}"
        ".card{max-width:420px;padding:32px 28px;border-radius:18px;background:#fff;box-shadow:0 8px 30px rgba(0,0,0,.08);text-align:center}"
        "h1{margin:0 0 8px;font-size:18px}p{margin:0;color:#6e6e73}@media(prefers-color-scheme:dark)"
        "{body{background:#000;color:#f5f5f7}.card{background:#1c1c1e;box-shadow:none}p{color:#a1a1a6}}</style>"
        "</head><body><main class=\"card\"><h1>" + _escape_html(i18n.translate("server.error.access_denied_title"))
        + "</h1><p>" + _escape_html(reason_text) + "</p>"
        + (f"<p>{_escape_html(retry_text)}</p>" if retry_text else "")
        + "</main></body></html>"
    )
    return HTMLResponse(body, status_code=decision.status_code, headers=headers)


async def security_middleware(request: Request, call_next):
    request_id = normalize_request_id(request.headers.get("x-request-id", ""))
    request.state.request_id = request_id
    started = time.monotonic()
    request_locale = i18n.locale_from_request(request)
    is_alas_proxy = request.url.path.startswith("/alas/embed/proxy")
    is_alas_shell = request.url.path in {"/alas/embed", "/alas/embed/"}
    is_alas_origin = security.is_alas_origin_request(request)
    is_static_asset = request.url.path.startswith(("/static/", "/shared/", "/vendor/", "/css/"))
    source_ip = security.client_ip(request)
    context_token = bind_log_context(
        request_id=request_id,
        source_ip=source_ip,
        http_method=request.method,
    )
    response = None
    try:
        try:
            security.enforce_http_boundary(request)
        except Exception as exc:
            from fastapi import HTTPException

            if isinstance(exc, HTTPException):
                response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
            else:
                raise
        else:
            # 访问网关：健康探针例外 → BAN → GEO。放在既有边界校验之后、鉴权之前，
            # 顺序与 WebSocket 握手路径共用同一份实现（app.access_gate）。
            gate = access_gate.evaluate(
                source_ip=source_ip,
                method=request.method,
                path=request.url.path,
                peer_ip=getattr(request.client, "host", "") or "",
                forwarded=any(name in request.headers for name in ("forwarded", "x-forwarded-for", "x-real-ip", "cf-connecting-ip")),
            )
            request.state.access_decision = gate.decision
            # 只有地域策略真正启用时才挂查询回调（off 时不产生任何查库开销）。
            request.state.access_geo_lookup = geo_access.access_geo_lookup
            if not gate.allowed:
                response = _denial_response(request, gate)
            elif is_alas_origin and not security.alas_route_allowed(request.url.path):
                response = JSONResponse({"detail": "Not Found"}, status_code=404)
            else:
                response = await call_next(request)
                # WebSockets never enter this HTTP middleware path.  Refresh
                # only successful, non-static user activity so hidden pages,
                # background assets, health probes, and failed requests do not
                # keep an abandoned login alive.
                if (
                    response.status_code < 400
                    and request.method not in {"OPTIONS", "HEAD"}
                    and request.url.path != "/healthz"
                    and not is_static_asset
                ):
                    try:
                        await asyncio.to_thread(
                            storage.renew_session,
                            request.cookies.get(security.SESSION_COOKIE),
                        )
                    except Exception:  # pragma: no cover - session refresh is best effort
                        log.debug("session idle renewal failed", exc_info=True)
        if is_static_asset and response.status_code < 400:
            versions = request.query_params.getlist("v")
            if len(versions) == 1 and STATIC_ASSET_VERSION_RE.fullmatch(versions[0]):
                response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            else:
                response.headers["Cache-Control"] = "public, max-age=0, must-revalidate"
        elif not is_alas_proxy:
            response.headers.setdefault("Content-Language", request_locale)
            response.headers["Vary"] = _locale_vary_header(response.headers.get("Vary", ""))
        response.headers["X-Request-ID"] = request_id
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        if is_alas_shell:
            # The workbench embeds this same-origin shell in its desktop
            # floating window; the Runtime proxy remains separately gated.
            response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        elif not is_alas_proxy:
            response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()")
        if is_alas_proxy:
            public = security.public_base_url()
            frame_ancestors = security.normalize_origin(public.geturl()) if public else "'self'"
        else:
            frame_ancestors = ""
        alas_origin = alas_gateway.configured_origin()
        csp_nonce = getattr(request.state, "csp_nonce", "")
        script_src = f"'self' 'nonce-{csp_nonce}'" if csp_nonce else "'self'"
        main_csp = (
            "default-src 'self'; script-src " + script_src + "; style-src 'self' 'unsafe-inline'; "
            "font-src 'self' data:; img-src 'self' data:; connect-src 'self' ws: wss:; "
            "media-src 'self' blob:; frame-src 'self'"
            + (f" {alas_origin}" if alas_origin else "")
            + "; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        )
        if is_alas_proxy:
            csp_value = f"frame-ancestors {frame_ancestors}"
        elif is_alas_shell:
            csp_value = main_csp.replace("frame-ancestors 'none'", "frame-ancestors 'self'")
        else:
            csp_value = main_csp
        response.headers.setdefault("Content-Security-Policy", csp_value)
        if request.url.path in {"/alas/gateway", "/alas/gateway/"}:
            response.headers.setdefault("Cache-Control", "no-store")
        if security.secure_cookie_enabled():
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response
    except Exception:
        route = getattr(request.scope.get("route"), "path", None) or safe_log_path(request.url.path)
        log_event(
            log,
            "http.request.failed",
            level=logging.ERROR,
            http_method=request.method,
            http_route=route,
            outcome="error",
            duration_ms=round((time.monotonic() - started) * 1000, 2),
        )
        raise
    finally:
        if response is not None:
            status_code = int(response.status_code)
            route = getattr(request.scope.get("route"), "path", None) or safe_log_path(request.url.path)
            level = (
                logging.WARNING
                if status_code >= 400
                else logging.DEBUG
                if request.url.path == "/healthz" or is_static_asset or is_alas_proxy
                else logging.INFO
            )
            log_event(
                log,
                "http.request",
                level=level,
                http_method=request.method,
                http_route=route,
                http_status_code=status_code,
                outcome="success" if status_code < 400 else "failure",
                duration_ms=round((time.monotonic() - started) * 1000, 2),
            )
            if should_audit_http_failure(request, status_code) and not security.has_pending_audit_events(request):
                audit_request(
                    request,
                    None,
                    "http_operation",
                    outcome="failure" if status_code >= 500 else "denied",
                    reason=f"http_{status_code}",
                    severity="error" if status_code >= 500 else "warning",
                    target_type="route",
                    target_id=route,
                    metadata={"http_status_code": status_code},
                )
            _record_access(
                request,
                status_code=status_code,
                route=route,
                duration_ms=round((time.monotonic() - started) * 1000, 2),
                source_ip=source_ip,
            )
        elif not security.has_pending_audit_events(request):
            audit_request(
                request,
                None,
                "http_operation",
                outcome="failure",
                reason="unhandled_exception",
                severity="error",
                target_type="route",
                target_id=getattr(request.scope.get("route"), "path", None) or safe_log_path(request.url.path),
            )
        if response is None:
            # Record exceptions even when the handler already queued an audit event.
            _record_access(
                request,
                status_code=500,
                route=getattr(request.scope.get("route"), "path", None) or safe_log_path(request.url.path),
                duration_ms=round((time.monotonic() - started) * 1000, 2),
                source_ip=source_ip,
            )
        await flush_request_audit_events(request)
        reset_log_context(context_token)
