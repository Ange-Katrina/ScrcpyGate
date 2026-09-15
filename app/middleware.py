"""ASGI middleware used by the application factory."""

from __future__ import annotations

import asyncio
import logging
import re
import time

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.gzip import GZipMiddleware
from starlette.requests import HTTPConnection

from . import alas_gateway, i18n, security, storage
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
            if is_alas_origin and not security.alas_route_allowed(request.url.path):
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
        if is_static_asset:
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
        await flush_request_audit_events(request)
        reset_log_context(context_token)
