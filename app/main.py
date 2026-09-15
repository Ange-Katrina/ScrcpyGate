"""Compatibility entry point for ScrcpyGate.

The application is assembled by :mod:`app.application`; route handlers live
in ``app.routers`` and domain code lives in ``app.services``.  This module is
kept intentionally small because ``uvicorn app.main:app`` is a public
deployment contract and older integrations still import a few helpers from
``app.main``.
"""

# Compatibility exports in this module are intentionally static imports.
# Route and service modules own their dependencies and never import ``main``.

from __future__ import annotations

from contextlib import asynccontextmanager

from .application import app, create_app
from . import alas, alas_gateway, security, storage
from .account_access import account_connections, account_expiration_monitor
from .adb_monitor import adb_monitor
from .audit_dispatcher import AuditDispatcher
from .middleware import LocaleContextMiddleware, SelectiveGZipMiddleware
from .mirror_manager import manager
from .mirror_runtime import acquire_control_lock, release_control_lock
from .mirror_websocket import control_socket, video_socket
from .http_helpers import (
    ALAS_EMBED_DENIED_DEFAULT_MESSAGE_KEY,
    ALAS_EMBED_DENIED_MESSAGE_KEYS,
    API_REQUEST_BODY_MAX_BYTES,
    REQUEST_BODY_IDLE_TIMEOUT_DETAIL,
    REQUEST_BODY_IDLE_TIMEOUT_SECONDS,
    REQUEST_BODY_TOO_LARGE_DETAIL,
    SERVER_ERROR_MESSAGE_KEYS,
    _locale_vary_header,
    _read_request_body_limited,
    alas_embed_denial_detail,
    alas_embed_denied_html_response,
    alas_embed_denied_message,
    alas_embed_reason_code,
    alas_embed_return_url,
    alas_embed_route_class,
    audit_detail,
    parse_bool,
    parse_body,
    redirect_to_login,
    register_current_user_websocket,
    server_error_message,
    user_payload,
    video_option_error_message,
)
from .runtime import (
    RuntimeState,
    auto_stop_minutes,
    lifespan as _runtime_lifespan,
    max_session_minutes,
    mirror_autostop_loop,
    persist_audit_event_checked as _persist_audit_event_checked,
    revoke_expired_access_with_audit as _revoke_expired_access_with_audit,
    runtime_for,
)
from .services.audit_service import (
    AUDIT_EXPORT_MAX_ROWS,
    audit_csv_cell as _audit_csv_cell,
    audit_filter_int as _audit_filter_int,
    audit_filters as _audit_filters,
    audit_request,
    audit_websocket_event,
    await_audit_read_barrier as _await_audit_read_barrier,
    collect_audit_export as _collect_audit_export,
    flush_request_audit_events as _flush_request_audit_events,
    safe_log_path as _safe_log_path,
    should_audit_http_failure as _should_audit_http_failure,
)
from .services.alas_service import (
    _admin_alas_call,
    admin_alas_payload,
    alas_binding_for_user,
)
from .services.domain_helpers import fullscreen_video_options, user_video_options
from .services.mirror_service import (
    public_mirror_failure,
    public_sessions_for_user,
    resolve_device_or_404,
)
from .video_options import profile_payloads, public_video_options
from .routers import admin as admin_router
from .routers import alas as alas_router


# A small number of integrations invoke this read-only route handler directly.
# The HTTP route itself is registered by ``public.router``.
api_alas_status = alas_router.api_alas_status


# These constants were imported by deployment diagnostics and focused tests
# before the module split.  Keep them as read-only compatibility values.
AUDIT_EXPORT_MAX_SECONDS = 31 * 24 * 60 * 60
ADMIN_ALAS_OVERVIEW_CONCURRENCY = admin_router.ADMIN_ALAS_OVERVIEW_CONCURRENCY


@asynccontextmanager
async def lifespan(application):
    """Compatibility lifecycle that delegates ownership to ``runtime``.

    Older tests and integrations patched lifecycle callables on ``app.main``.
    Passing those overrides through app state preserves that contract without
    moving resource ownership back into this compatibility module.
    """
    application.state.lifespan_overrides = {
        "AuditDispatcher": AuditDispatcher,
        "mirror_autostop_loop": mirror_autostop_loop,
        "account_expiration_monitor": account_expiration_monitor,
        "revoke_expired_access_with_audit": _revoke_expired_access_with_audit,
    }
    try:
        async with _runtime_lifespan(application):
            yield
    finally:
        try:
            del application.state.lifespan_overrides
        except AttributeError:
            pass


# ``app.main:app`` is the supported deployment target. Install the adapter
# only on that compatibility instance; callers of ``create_app`` get the
# native runtime lifecycle directly.
app.router.lifespan_context = lifespan


__all__ = [
    "app",
    "create_app",
    "lifespan",
    "RuntimeState",
    "security",
    "storage",
    "alas",
    "alas_gateway",
    "manager",
    "adb_monitor",
    "account_connections",
    "AuditDispatcher",
    "parse_body",
    "user_payload",
    "server_error_message",
    "user_video_options",
    "fullscreen_video_options",
    "profile_payloads",
    "public_video_options",
    # Stable direct-import compatibility names retained for the transition.
    "LocaleContextMiddleware",
    "SelectiveGZipMiddleware",
    "acquire_control_lock",
    "control_socket",
    "release_control_lock",
    "video_socket",
    "ALAS_EMBED_DENIED_DEFAULT_MESSAGE_KEY",
    "ALAS_EMBED_DENIED_MESSAGE_KEYS",
    "API_REQUEST_BODY_MAX_BYTES",
    "REQUEST_BODY_IDLE_TIMEOUT_DETAIL",
    "REQUEST_BODY_IDLE_TIMEOUT_SECONDS",
    "REQUEST_BODY_TOO_LARGE_DETAIL",
    "SERVER_ERROR_MESSAGE_KEYS",
    "_locale_vary_header",
    "_read_request_body_limited",
    "alas_embed_denial_detail",
    "alas_embed_denied_html_response",
    "alas_embed_denied_message",
    "alas_embed_reason_code",
    "alas_embed_return_url",
    "alas_embed_route_class",
    "audit_detail",
    "parse_bool",
    "redirect_to_login",
    "register_current_user_websocket",
    "video_option_error_message",
    "auto_stop_minutes",
    "max_session_minutes",
    "_persist_audit_event_checked",
    "runtime_for",
    "AUDIT_EXPORT_MAX_ROWS",
    "_audit_csv_cell",
    "_audit_filter_int",
    "_audit_filters",
    "audit_request",
    "audit_websocket_event",
    "_await_audit_read_barrier",
    "_collect_audit_export",
    "_flush_request_audit_events",
    "_safe_log_path",
    "_should_audit_http_failure",
    "_admin_alas_call",
    "admin_alas_payload",
    "alas_binding_for_user",
    "public_mirror_failure",
    "public_sessions_for_user",
    "resolve_device_or_404",
    "api_alas_status",
    "AUDIT_EXPORT_MAX_SECONDS",
    "ADMIN_ALAS_OVERVIEW_CONCURRENCY",
]
