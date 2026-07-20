import asyncio
import json
import logging
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlencode

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.gzip import GZipMiddleware
from starlette.requests import HTTPConnection

from . import alas, alas_embed, i18n, security, storage
from .account_access import account_connections, account_expiration_monitor
from .adb_monitor import adb_monitor
from .devices import devices_payload, public_adb_payload, session_payload, sessions_payload
from .logging_config import (
    bind_log_context,
    log_event,
    logging_health,
    normalize_request_id,
    reset_log_context,
    setup_logging,
    tail_log,
)
from .mirror import acquire_control_lock, control_socket, manager, release_control_lock, video_socket
from .video_options import (
    BANDWIDTH_RECOMMENDATIONS,
    FULLSCREEN_MIN_MAX_SIZE,
    MAX_PRESET_MAX_SIZE,
    MIN_PRESET_MAX_SIZE,
    PROFILE_FIELDS,
    PROFILE_NAMES,
    VIDEO_LIMITS,
    VideoOptionError,
    custom_profile_payloads,
    enabled_stream_modes_value,
    fullscreen_profile_value,
    normalize_video_options,
    normalize_custom_profile_payloads,
    normalize_profile_payloads,
    profile_label_payloads,
    profile_setting_key,
    profile_setting_keys,
    profile_payloads,
    public_video_options,
    serialize_custom_profiles,
    settings_to_video_options,
    signature,
    stream_mode_or_default,
)
from .workers import ALAS_OVERVIEW_MAX_WORKERS, alas_overview_executor

setup_logging()
log = logging.getLogger("webscrcpy.main")
api_docs_enabled = security.env_bool("ENABLE_API_DOCS", False)
mirror_autostop_task: asyncio.Task | None = None
account_expiration_task: asyncio.Task | None = None
STATIC_ASSET_VERSION_RE = re.compile(r"[a-f0-9]{12}")
ADMIN_ALAS_OVERVIEW_CONCURRENCY = ALAS_OVERVIEW_MAX_WORKERS
ALAS_EMBED_DENIED_MESSAGE_KEYS = {
    "missing binding": "alas.denied.reason.missing_binding",
    "missing_binding": "alas.denied.reason.missing_binding",
    "missing request config": "alas.denied.reason.missing_request_config",
    "missing_request_config": "alas.denied.reason.missing_request_config",
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
    "account_expired_or_missing": "server.error.account_expired_or_missing",
    "Password is too common": "server.error.password_too_common",
    "Password must not match username": "server.error.password_matches_username",
    "invalid ALAS token": "server.error.invalid_alas_token",
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


def server_error_message(error: object) -> str:
    text = str(error or "").strip()
    key = SERVER_ERROR_MESSAGE_KEYS.get(text)
    if key:
        return i18n.translate(key)
    match = PASSWORD_MIN_LENGTH_ERROR_RE.fullmatch(text)
    if match:
        return i18n.translate("server.error.password_min_length", min=match.group("min"))
    if text.startswith("ALAS Runtime unreachable"):
        return i18n.translate("server.status.alas_runtime_unreachable")
    return text


def video_option_error_message(error: VideoOptionError) -> str:
    return error.localized()


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


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global account_expiration_task, mirror_autostop_task
    log.info("APP_STARTUP")
    storage.init_db()
    await adb_monitor.start()
    mirror_autostop_task = asyncio.create_task(mirror_autostop_loop())
    account_expiration_task = asyncio.create_task(account_expiration_monitor(storage.revoke_expired_access))
    try:
        yield
    finally:
        log.info("APP_SHUTDOWN")
        if mirror_autostop_task:
            mirror_autostop_task.cancel()
            try:
                await mirror_autostop_task
            except asyncio.CancelledError:
                pass
            mirror_autostop_task = None
        if account_expiration_task:
            account_expiration_task.cancel()
            try:
                await account_expiration_task
            except asyncio.CancelledError:
                pass
            account_expiration_task = None
        await manager.stop_all()
        await adb_monitor.stop()


app = FastAPI(
    title="ScrcpyGate",
    version="0.1.0",
    docs_url="/docs" if api_docs_enabled else None,
    redoc_url="/redoc" if api_docs_enabled else None,
    openapi_url="/openapi.json" if api_docs_enabled else None,
    lifespan=lifespan,
)
app.add_middleware(SelectiveGZipMiddleware, minimum_size=500)
app.add_middleware(LocaleContextMiddleware)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
templates.env.globals.update(
    t=i18n.translate,
    i18n_payload=i18n.browser_payload,
    default_locale=i18n.DEFAULT_LOCALE,
    current_locale=i18n.current_locale,
    supported_locales=i18n.SUPPORTED_LOCALES,
)


def auto_stop_minutes() -> int:
    raw = storage.get_setting("auto_stop_minutes", storage.get_setting("auto_stop_time", "15"))
    try:
        minutes = int(raw)
    except Exception:
        return 15
    return max(0, min(minutes, 1440))


async def mirror_autostop_loop() -> None:
    while True:
        await asyncio.sleep(10)
        minutes = auto_stop_minutes()
        if minutes <= 0:
            continue
        stopped = await manager.stop_idle_sessions(minutes * 60)
        for device_id in stopped:
            storage.audit("system", "mirror_auto_stop", f"{device_id}: no_viewers_for_{minutes}m")


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    request_id = normalize_request_id(request.headers.get("x-request-id", ""))
    request.state.request_id = request_id
    started = time.monotonic()
    request_locale = i18n.locale_from_request(request)
    is_alas_proxy = request.url.path.startswith("/alas/embed/proxy")
    is_static_asset = request.url.path.startswith("/static/")
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
        except HTTPException as exc:
            response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        else:
            response = await call_next(request)
        if is_static_asset:
            versions = request.query_params.getlist("v")
            if len(versions) == 1 and STATIC_ASSET_VERSION_RE.fullmatch(versions[0]):
                response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            else:
                response.headers["Cache-Control"] = "public, max-age=0, must-revalidate"
        elif not is_alas_proxy:
            response.headers.setdefault("Content-Language", request_locale)
            vary_values = [item.strip() for item in response.headers.get("Vary", "").split(",") if item.strip()]
            vary_names = {item.lower() for item in vary_values}
            for value in ("Cookie", "Accept-Language"):
                if value.lower() not in vary_names:
                    vary_values.append(value)
                    vary_names.add(value.lower())
            response.headers["Vary"] = ", ".join(vary_values)
        response.headers["X-Request-ID"] = request_id
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        if not is_alas_proxy:
            response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()")
        response.headers.setdefault(
            "Content-Security-Policy",
            "frame-ancestors 'self'" if is_alas_proxy else (
                "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                "font-src 'self' data:; img-src 'self' data:; connect-src 'self' ws: wss:; "
                "media-src 'self' blob:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
            ),
        )
        if security.secure_cookie_enabled():
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response
    except Exception:
        route = getattr(request.scope.get("route"), "path", None) or _safe_log_path(request.url.path)
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
            route = getattr(request.scope.get("route"), "path", None) or _safe_log_path(request.url.path)
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
        reset_log_context(context_token)


def _safe_log_path(path: str) -> str:
    normalized = str(path or "/").replace("\\", "/")
    if normalized.startswith("/static/"):
        return "/static/*"
    if normalized.startswith("/alas/embed/proxy"):
        return "/alas/embed/proxy/*"
    return normalized


def redirect_to_login(request: Request):
    if not security.get_current_user(request):
        return RedirectResponse("/login", status_code=302)
    return None


async def parse_body(request: Request) -> dict:
    ctype = request.headers.get("content-type", "")
    if "application/json" in ctype:
        try:
            return await request.json()
        except Exception:
            return {}
    body = await request.body()
    form = parse_qs(body.decode("utf-8", "ignore"), keep_blank_values=True)
    return {key: values[-1] if values else "" for key, values in form.items()}


def user_payload(user: dict) -> dict:
    payload = {
        "username": user["username"],
        "role": user["role"],
        "is_admin": user["role"] == "admin",
        "video_mode": "normal",
    }
    payload.update(storage.user_expiration_payload(user))
    return payload


async def register_current_user_websocket(websocket: WebSocket, user: dict) -> bool:
    """Register first, then recheck the session so expiration cannot slip between both steps."""
    username = str(user.get("username") or "").strip()
    await account_connections.register(username, websocket)
    if security.get_current_user(websocket):
        return True
    await account_connections.unregister(username, websocket)
    await websocket.close(code=4403, reason="account expired")
    return False


def parse_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def audit_detail(request: Request, extra: str = "") -> str:
    ip = security.client_ip(request)
    ua = (request.headers.get("user-agent") or "").replace("\r", " ").replace("\n", " ")[:180]
    parts = [f"ip={ip}"]
    if ua:
        parts.append(f"ua={ua}")
    if extra:
        parts.append(extra)
    return " ".join(parts)


def alas_embed_denial_detail(request: Request, reason: str, path: str = "") -> str:
    """生成 ALAS 嵌入拒绝审计详情，避免记录敏感正文和完整凭据。"""
    safe_reason = str(reason or "unknown").replace("\r", " ").replace("\n", " ")[:80]
    return audit_detail(request, f"reason={safe_reason} route={alas_embed_route_class(path)}")


def alas_embed_route_class(path: str) -> str:
    """只记录代理路径类别，避免配置名或设备 endpoint 进入日志。"""
    first_segment = str(path or "").replace("\\", "/").strip("/").split("/", 1)[0].lower()
    if first_segment in {"api", "ajax", "pywebio", "static", "assets", "config"}:
        return first_segment
    return "root" if not first_segment else "other"


def alas_embed_denied_message(reason: str) -> str:
    """将 ALAS 嵌入代理拒绝原因转换为用户可见中文文案。"""
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
        return f"/alas/embed/proxy/?{urlencode({'config': config_name})}"
    return "/alas/embed/"


def requested_alas_config(query_params) -> str | None:
    values = alas_embed.config_query_values(query_params)
    unique = {value for value in values}
    return values[0] if len(unique) == 1 else None


def alas_embed_denied_html_response(request: Request, binding: dict | None, status_code: int, message: str):
    accept = request.headers.get("accept", "")
    if request.method.upper() not in ("GET", "HEAD") or "text/html" not in accept.lower():
        return None
    return HTMLResponse(
        alas_embed.denied_page_html(message, alas_embed_return_url(binding), seconds=3),
        status_code=status_code,
    )


def alas_embed_reason_code(reason: str) -> str:
    """将内部拒绝原因规范化为审计日志代码。"""
    return {
        "missing binding": "missing_binding",
        "missing request config": "missing_request_config",
        "invalid body": "invalid_body",
        "config mismatch": "config_mismatch",
        "config path mismatch": "config_path_mismatch",
        "management path denied": "management_path_denied",
        "restricted user entry denied": "restricted_user_entry_denied",
        "alas settings denied": "alas_settings_denied",
        "run permission denied": "run_permission_denied",
        "edit permission denied": "edit_permission_denied",
    }.get(str(reason or ""), "denied")


def log_alas_embed_denied(user: dict, binding: dict | None, channel: str, path: str, reason: str) -> None:
    log.warning(
        "ALAS_EMBED_DENIED channel=%s user=%s role=%s bound=%s route=%s reason=%s",
        channel,
        (user or {}).get("username", ""),
        (user or {}).get("role", ""),
        bool(binding and binding.get("config_name")),
        alas_embed_route_class(path),
        reason,
    )


def log_alas_websocket_close(
    connection_id: str,
    user: dict | None,
    reason: str,
    code: int,
    *,
    permission: str = "none",
    phase: str = "authorization",
) -> None:
    """记录不含路径、查询参数或载荷的 WebSocket 关闭元数据。"""
    log.warning(
        "ALAS_WS_CLOSE connection=%s event=connect reason=%s permission=%s task=none",
        connection_id,
        reason,
        permission,
    )


def default_video_options() -> dict:
    return settings_to_video_options(storage.get_settings())


def video_profiles(settings: dict | None = None) -> dict:
    return profile_payloads(settings or storage.get_settings())


def user_video_options(username: str, payload: dict | None = None) -> dict:
    settings = storage.get_settings()
    enabled_modes = enabled_stream_modes_value(settings.get("scrcpy_enabled_stream_modes", "raw"))
    profiles = video_profiles(settings)
    stored = storage.get_user_video_preference(username)
    fallback = stored or default_video_options()
    if str(fallback.get("profile") or "") not in profiles:
        fallback = normalize_video_options(
            {"profile": "balanced"},
            default_video_options(),
            profiles=profiles,
            enabled_stream_modes=enabled_modes,
        )
    return normalize_video_options(payload or {}, fallback, profiles=profiles, enabled_stream_modes=enabled_modes)


def alas_binding_for_user(
    user: dict,
    allow_admin_global: bool = False,
    config_name: str | None = None,
) -> dict | None:
    requested = str(config_name or "").strip()
    if requested:
        try:
            requested = alas.sanitize_config_name(requested)
        except ValueError:
            return None
        binding = storage.get_user_alas_binding(user["username"], requested)
    else:
        binding = storage.get_user_alas_config(user["username"])
    if binding and binding.get("config_name"):
        return binding
    if allow_admin_global and user.get("role") == "admin":
        return {
            "username": user["username"],
            "config_name": requested or alas.legacy_config_name(),
            "can_run": True,
            "can_edit": True,
            "is_default": not requested,
            "updated_at": 0,
            "admin_fallback": True,
        }
    return None


def require_alas_binding(
    user: dict,
    *,
    config_name: str | None = None,
    run: bool = False,
    edit: bool = False,
) -> dict:
    requested = str(config_name or "").strip()
    if requested:
        try:
            requested = alas.sanitize_config_name(requested)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=i18n.translate("server.error.invalid_alas_config")) from exc
    binding = alas_binding_for_user(
        user,
        allow_admin_global=user.get("role") == "admin",
        config_name=requested or None,
    )
    if not binding:
        raise HTTPException(status_code=403, detail=i18n.translate("server.error.alas_config_not_bound"))
    if run and not binding.get("can_run"):
        raise HTTPException(status_code=403, detail=i18n.translate("server.error.alas_run_denied"))
    if edit and not binding.get("can_edit"):
        raise HTTPException(status_code=403, detail=i18n.translate("server.error.alas_edit_denied"))
    try:
        binding["config_name"] = alas.sanitize_config_name(binding.get("config_name"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=i18n.translate("server.error.invalid_bound_alas_config")) from exc
    return binding


def public_user_alas_bindings(user: dict) -> list[dict]:
    if user.get("role") == "admin":
        bindings = storage.list_user_alas_bindings(user["username"])
        if not bindings:
            bindings = [alas_binding_for_user(user, allow_admin_global=True)]
    else:
        bindings = storage.list_user_alas_bindings(user["username"])
    return [
        {
            "config_name": binding["config_name"],
            "can_run": bool(binding.get("can_run")),
            "can_edit": bool(binding.get("can_edit")),
            "is_default": bool(binding.get("is_default")),
        }
        for binding in bindings
        if binding and binding.get("config_name")
    ]


def public_alas_status(result: dict, binding: dict) -> dict:
    cleaned = dict(result or {})
    cleaned.pop("settings", None)
    cleaned.pop("configs", None)
    cleaned["config"] = binding.get("config_name") or cleaned.get("config") or ""
    cleaned["can_run"] = bool(binding.get("can_run"))
    cleaned["can_edit"] = bool(binding.get("can_edit"))
    if cleaned.get("error"):
        cleaned["error"] = server_error_message(cleaned["error"])
    return cleaned


def bound_alas_config_names(bindings: list[dict] | None = None) -> list[str]:
    """Return unique ALAS config names that are explicitly bound to users."""
    seen: set[str] = set()
    names: list[str] = []
    for binding in bindings if bindings is not None else storage.list_user_alas_bindings():
        raw = str(binding.get("config_name") or "").strip()
        if not raw:
            continue
        try:
            name = alas.sanitize_config_name(raw)
        except ValueError:
            continue
        if name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def admin_alas_status_for_config(config_name: str | None = None, bindings: list[dict] | None = None) -> dict:
    settings = alas.public_settings()
    configs = bound_alas_config_names(bindings)
    selected = str(config_name or "").strip()
    if selected:
        try:
            selected = alas.sanitize_config_name(selected)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=i18n.translate("server.error.invalid_alas_config_name")) from exc
    elif configs:
        selected = configs[0]
    if not selected:
        status = "disabled" if not settings.get("enabled") else "unknown"
        return {
            "ok": True,
            "settings": settings,
            "enabled": bool(settings.get("enabled")),
            "token_set": bool(settings.get("token_set")),
            "configured": bool(settings.get("enabled") and settings.get("token_set")),
            "status": status,
            "task": "",
            "config": "",
            "configs": [],
            "error": "" if settings.get("enabled") else i18n.translate("server.status.alas_control_disabled"),
        }
    result = alas.status_for_config(selected, include_configs=False)
    if result.get("error"):
        result["error"] = server_error_message(result["error"])
    result["enabled"] = bool(settings.get("enabled"))
    result["token_set"] = bool(settings.get("token_set"))
    return result


def _alas_overview_owners(assignments: list[dict]) -> dict[str, str]:
    owners: dict[str, str] = {}
    for binding in assignments:
        try:
            name = alas.sanitize_config_name(binding.get("config_name"))
        except ValueError:
            continue
        owners[name] = str(binding.get("username") or "").strip()
    return owners


def admin_alas_overview_local(bindings: list[dict] | None = None) -> dict:
    """Build a network-free ALAS summary while preserving the legacy fields."""
    settings = alas.public_settings()
    assignments = bindings if bindings is not None else storage.list_user_alas_bindings()
    config_names = bound_alas_config_names(assignments)
    owners = _alas_overview_owners(assignments)
    enabled = bool(settings.get("enabled"))
    token_set = bool(settings.get("token_set"))
    configured = bool(enabled and token_set)
    selected = config_names[0] if config_names else ""
    status = "unknown" if configured else "disabled" if not enabled else "error"
    error = "" if configured else i18n.translate(
        "server.status.alas_control_disabled" if not enabled else "server.status.alas_token_missing"
    )
    config_statuses = [
        {
            "config": name,
            "username": owners.get(name, ""),
            "status": status,
            "task": "",
            "ok": not enabled,
            "error": error,
        }
        for name in config_names
    ] if not configured else []
    return {
        "ok": not enabled or configured,
        "settings": settings,
        "enabled": enabled,
        "token_set": token_set,
        "configured": configured,
        "status": status,
        "task": "",
        "config": selected,
        "configs": config_names,
        "config_statuses": config_statuses,
        "config_count": len(config_names),
        "running_count": 0,
        "error": error,
        "catalog_error": "",
    }


def _alas_runtime_unreachable(error: object) -> bool:
    value = str(error or "").lower()
    return any(token in value for token in ("unreachable", "timed out", "timeout", "connection refused"))


async def _admin_alas_call(func, *args) -> dict:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(alas_overview_executor, func, *args)


async def admin_alas_overview(bindings: list[dict] | None = None) -> dict:
    """Load every discoverable ALAS config without delaying the base overview."""
    assignments = bindings if bindings is not None else await asyncio.to_thread(storage.list_user_alas_bindings)
    summary = await asyncio.to_thread(admin_alas_overview_local, assignments)
    if not summary["configured"]:
        return summary

    bound_configs = list(summary["configs"])
    owners = _alas_overview_owners(assignments)

    async def load_catalog() -> dict:
        try:
            return await _admin_alas_call(alas.list_configs)
        except Exception as exc:
            log.warning("ALAS_OVERVIEW_CATALOG_FAILED error=%s", exc)
            return {"ok": False, "configs": [], "error": str(exc)}

    async def load_status(name: str) -> tuple[str, dict]:
        try:
            return name, await _admin_alas_call(alas.status_for_config, name, False)
        except Exception as exc:
            log.warning("ALAS_OVERVIEW_STATUS_FAILED config=%s error=%s", name, exc)
            return name, {"ok": False, "status": "error", "task": "", "config": name, "error": str(exc)}

    def runtime_config_names(catalog: dict) -> list[str]:
        names: list[str] = []
        for raw in catalog.get("configs") or []:
            try:
                name = alas.sanitize_config_name(raw)
            except ValueError:
                continue
            if name not in names:
                names.append(name)
        return names

    if bound_configs:
        selected = bound_configs[0]
        catalog, selected_status_pair = await asyncio.gather(load_catalog(), load_status(selected))
        runtime_configs = runtime_config_names(catalog)
    else:
        catalog = await load_catalog()
        runtime_configs = runtime_config_names(catalog)
        catalog_error = str(catalog.get("error") or "")
        if not catalog.get("ok") or not runtime_configs:
            has_error = bool(catalog_error)
            return {
                **summary,
                "ok": not has_error,
                "status": "error" if has_error else "unknown",
                "configs": [],
                "config_statuses": [],
                "config_count": 0,
                "running_count": 0,
                "error": server_error_message(catalog_error),
                "catalog_error": server_error_message(catalog_error),
            }
        selected = runtime_configs[0]
        selected_status_pair = await load_status(selected)

    selected_name, selected_status = selected_status_pair
    config_names = list(runtime_configs)
    for name in bound_configs:
        if name and name not in config_names:
            config_names.append(name)

    status_by_config: dict[str, dict] = {selected_name: selected_status}
    pending = [name for name in config_names if name not in status_by_config]
    selected_error = str(selected_status.get("error") or "")
    if pending and _alas_runtime_unreachable(selected_error):
        for name in pending:
            status_by_config[name] = {
                "ok": False,
                "status": str(selected_status.get("status") or "disconnected"),
                "task": "",
                "config": name,
                "error": selected_error,
            }
    elif pending:
        for name, result in await asyncio.gather(*(load_status(name) for name in pending)):
            status_by_config[name] = result

    config_statuses = []
    errors = [str(catalog.get("error") or "")]
    for name in config_names:
        result = status_by_config.get(name) or {}
        item = {
            "config": name,
            "username": owners.get(name, ""),
            "status": str(result.get("status") or "unknown"),
            "task": str(result.get("task") or ""),
            "ok": bool(result.get("ok", not result.get("error"))),
            "error": server_error_message(result.get("error")),
        }
        config_statuses.append(item)
        errors.append(item["error"])
    running_count = sum(item["status"] == "running" for item in config_statuses)
    has_error = bool(catalog.get("error")) or any(not item["ok"] or item["status"] == "error" for item in config_statuses)
    overall_status = "error" if has_error else "running" if running_count else "stopped" if config_statuses else "unknown"
    first_error = server_error_message(next((error for error in errors if error), ""))
    primary_name = bound_configs[0] if bound_configs else config_names[0] if config_names else ""
    primary_status = status_by_config.get(primary_name) or {}
    return {
        **summary,
        "ok": not has_error,
        "status": overall_status,
        "task": str(primary_status.get("task") or ""),
        "config": primary_name,
        "configs": config_names,
        "config_statuses": config_statuses,
        "config_count": len(config_statuses),
        "running_count": running_count,
        "error": first_error,
        "catalog_error": server_error_message(catalog.get("error")),
    }


def admin_alas_payload(config_name: str | None = None) -> dict:
    bindings = storage.list_user_alas_configs()
    assignments = storage.list_user_alas_bindings()
    return {
        "settings": alas.public_settings(),
        "status": admin_alas_status_for_config(config_name, assignments),
        "bindings": bindings,
        "assignments": assignments,
        "bound_configs": bound_alas_config_names(assignments),
    }


def admin_alas_permissions_payload() -> dict:
    return {
        "bindings": storage.list_user_alas_configs(),
        "assignments": storage.list_user_alas_bindings(),
        "users": storage.list_users(),
    }


def admin_overview_storage_payload() -> tuple[list[dict], list[dict], list[dict], dict]:
    alas_bindings = storage.list_user_alas_bindings()
    return (
        storage.list_all_devices(),
        storage.list_users(),
        alas_bindings,
        admin_alas_overview_local(alas_bindings),
    )


def resolve_device_or_404(device_ref: str) -> str:
    device_id = storage.resolve_device_ref(device_ref)
    if not device_id:
        raise HTTPException(status_code=404, detail=i18n.translate("server.error.device_not_found"))
    return device_id


async def public_sessions_for_user(user: dict) -> dict:
    devices = storage.list_devices_for_user(user["username"], user["role"] == "admin")
    sessions = await manager.snapshot()
    return sessions_payload(devices, sessions, public_id=True)


def public_mirror_failure(device_id: str, session: dict, adb_status: dict, default_error: str) -> dict:
    exposed_id = storage.public_device_id(device_id)
    safe_session = session_payload(session, device_id, exposed_id, public=True) or {}
    safe_adb = public_adb_payload(adb_status, exposed_id) or {}
    return {
        "error": safe_session.get("last_error") or default_error,
        "detail": safe_adb.get("detail") or safe_session.get("last_error") or "",
    }


def set_session_cookie(response: Response, sid: str) -> None:
    response.set_cookie(
        security.SESSION_COOKIE,
        sid,
        httponly=True,
        secure=security.secure_cookie_enabled(),
        samesite="strict",
        max_age=43200,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(security.SESSION_COOKIE, path="/")


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if security.get_current_user(request):
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(request, "login.html", {"error": "", "username": ""})


@app.post("/login")
async def login(request: Request):
    data = await parse_body(request)
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))
    rate = security.login_rate_limit_status(request, username)
    if rate["limited"]:
        storage.audit(username or "anonymous", "login_rate_limited", audit_detail(request, f"retry_after={rate['retry_after']}"))
        response = templates.TemplateResponse(
            request,
            "login.html",
            {"error": i18n.translate("login.error.rate_limited"), "username": username},
            status_code=429,
        )
        response.headers["Retry-After"] = str(rate["retry_after"])
        return response
    user = storage.authenticate(username, password)
    if not user:
        rate = security.record_login_failure(request, username)
        storage.audit(username or "anonymous", "login_failed", audit_detail(request))
        if rate["limited"]:
            storage.audit(username or "anonymous", "login_rate_limited", audit_detail(request, f"retry_after={rate['retry_after']}"))
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": i18n.translate("login.error.invalid_credentials"), "username": username},
            status_code=401,
        )
    try:
        session = storage.create_session(user["username"])
    except ValueError:
        security.record_login_failure(request, username)
        storage.audit(username or "anonymous", "login_failed", audit_detail(request))
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": i18n.translate("login.error.invalid_credentials"), "username": username},
            status_code=401,
        )
    security.record_login_success(request, username)
    storage.audit(user["username"], "login_success", audit_detail(request))
    response = RedirectResponse("/", status_code=302)
    set_session_cookie(response, session["sid"])
    return response


@app.post("/logout")
async def logout(request: Request):
    data = await parse_body(request)
    security.verify_csrf_token(request, str(data.get("csrf_token") or request.headers.get("x-csrf-token", "")))
    user = security.get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail=i18n.translate("server.security.login_required"))
    sess = security.get_current_session(request)
    if sess:
        storage.delete_session(sess["sid"])
    storage.audit(user["username"], "logout", audit_detail(request))
    response = RedirectResponse("/login", status_code=302)
    clear_session_cookie(response)
    return response


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    redirect = redirect_to_login(request)
    if redirect:
        return redirect
    sess = security.get_current_session(request)
    user = security.require_user(request)
    storage.audit(user["username"], "page_index", audit_detail(request))
    return templates.TemplateResponse(request, "index.html", {"user": user, "csrf_token": sess["csrf_token"]})


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    redirect = redirect_to_login(request)
    if redirect:
        return redirect
    user = security.require_admin(request)
    sess = security.get_current_session(request)
    storage.audit(user["username"], "page_admin", audit_detail(request))
    return templates.TemplateResponse(request, "admin.html", {"user": user, "csrf_token": sess["csrf_token"]})


@app.get("/api/me")
async def api_me(request: Request):
    user = security.require_user(request)
    return {"user": user_payload(user), "csrf_token": security.get_current_session(request)["csrf_token"]}


@app.get("/api/devices")
async def api_devices(request: Request):
    user = security.require_user(request)
    devices = storage.list_devices_for_user(user["username"], user["role"] == "admin")
    sessions = await manager.snapshot()
    statuses = adb_monitor.snapshot()
    return {
        "devices": devices_payload(devices, sessions, statuses, include_address=False, public_id=True),
        "sessions": sessions_payload(devices, sessions, public_id=True),
    }


@app.get("/api/video/preferences")
async def api_video_preferences(request: Request):
    user = security.require_user(request)
    settings = storage.get_settings()
    defaults = default_video_options()
    stored = storage.get_user_video_preference(user["username"])
    effective = user_video_options(user["username"])
    profiles = video_profiles(settings)
    labels = profile_label_payloads(settings)
    labels = {name: labels[name] for name in profiles if name in labels}
    fullscreen_profile = fullscreen_profile_value(settings.get("video_fullscreen_profile"), profiles)
    return {
        "defaults": public_video_options(defaults),
        "preferences": public_video_options(stored or defaults),
        "has_user_preference": stored is not None,
        "effective": public_video_options(effective),
        "profiles": profiles,
        "profile_labels": labels,
        "fullscreen_profile": fullscreen_profile,
        "fullscreen_min_max_size": FULLSCREEN_MIN_MAX_SIZE,
        "video_mode": "normal",
        "limits": VIDEO_LIMITS,
        "stream_modes": ["raw", "protocol", "legacy"],
        "enabled_stream_modes": list(enabled_stream_modes_value(settings.get("scrcpy_enabled_stream_modes", "raw"))),
        "auto_stop_minutes": auto_stop_minutes(),
    }


@app.put("/api/video/preferences")
async def api_save_video_preferences(request: Request):
    security.verify_csrf(request)
    user = security.require_user(request)
    payload = await parse_body(request)
    try:
        options = user_video_options(user["username"], payload)
    except VideoOptionError as exc:
        raise HTTPException(status_code=400, detail=video_option_error_message(exc)) from exc
    storage.set_user_video_preference(user["username"], options)
    storage.audit(user["username"], "video_preference_save", json.dumps(public_video_options(options), ensure_ascii=False))
    return {"ok": True, "preferences": public_video_options(options), "effective": public_video_options(options), "video_mode": "normal"}


@app.put("/api/account/password")
async def api_change_password(request: Request):
    security.verify_csrf(request)
    user = security.require_user(request)
    sess = security.get_current_session(request)
    payload = await parse_body(request)
    current_password = str(payload.get("current_password", ""))
    new_password = str(payload.get("new_password", ""))
    confirm_password = str(payload.get("confirm_password", ""))
    if new_password != confirm_password:
        raise HTTPException(status_code=400, detail=i18n.translate("server.error.passwords_mismatch"))
    try:
        storage.change_user_password(user["username"], current_password, new_password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=server_error_message(exc)) from exc
    removed = storage.delete_other_sessions(user["username"], sess["sid"] if sess else None)
    storage.audit(user["username"], "password_change", audit_detail(request, f"other_sessions_removed={removed}"))
    return {"ok": True, "other_sessions_removed": removed}


@app.post("/api/devices/{device_id}/mirror/start")
async def api_mirror_start(device_id: str, request: Request):
    security.verify_csrf(request)
    user = security.require_user(request)
    real_device_id = resolve_device_or_404(device_id)
    if not storage.user_can(user["username"], real_device_id, "view"):
        raise HTTPException(status_code=403, detail=i18n.translate("server.error.device_denied"))
    payload = await parse_body(request)
    try:
        options = user_video_options(user["username"], payload or None)
    except VideoOptionError as exc:
        raise HTTPException(status_code=400, detail=video_option_error_message(exc)) from exc
    if payload.get("save_preference"):
        storage.set_user_video_preference(user["username"], options)
    ok = await manager.start(real_device_id, options)
    storage.audit(user["username"], "mirror_start", real_device_id)
    if not ok:
        sessions = await public_sessions_for_user(user)
        session = (await manager.snapshot()).get(real_device_id) or {}
        adb_status = session.get("adb") or adb_monitor.snapshot(real_device_id)
        failure = public_mirror_failure(
            real_device_id,
            session,
            adb_status,
            i18n.translate("server.status.mirror_start_failed"),
        )
        return {
            "ok": False,
            "error": failure["error"],
            "adb_state": adb_status.get("state", "unknown"),
            "stream_mode": session.get("stream_mode", "none"),
            "detail": failure["detail"],
            "sessions": sessions,
        }
    visible_devices = storage.list_devices_for_user(user["username"], user["role"] == "admin")
    stopped = await manager.stop_other_no_client_sessions(real_device_id, [str(device["id"]) for device in visible_devices])
    if stopped:
        storage.audit(user["username"], "mirror_switch_cleanup", f"keep={real_device_id}; stopped={','.join(stopped)}")
    return {"ok": ok, "sessions": await public_sessions_for_user(user), "stopped": len(stopped)}


@app.put("/api/devices/{device_id}/mirror/settings")
async def api_mirror_settings(device_id: str, request: Request):
    security.verify_csrf(request)
    user = security.require_user(request)
    real_device_id = resolve_device_or_404(device_id)
    if not storage.user_can(user["username"], real_device_id, "view"):
        raise HTTPException(status_code=403, detail=i18n.translate("server.error.device_denied"))
    payload = await parse_body(request)
    try:
        options = user_video_options(user["username"], payload)
    except VideoOptionError as exc:
        raise HTTPException(status_code=400, detail=video_option_error_message(exc)) from exc
    sessions = await manager.snapshot()
    current_session = sessions.get(real_device_id) or {}
    running = bool(current_session.get("running"))
    current_video = current_session.get("video")
    restart_required = running and (
        not isinstance(current_video, dict) or signature(current_video) != signature(options)
    )
    storage.set_user_video_preference(user["username"], options)
    if running:
        restart_ok = await manager.start(real_device_id, options, force_restart=restart_required)
        if not restart_ok:
            session = (await manager.snapshot()).get(real_device_id) or {}
            adb_status = session.get("adb") or adb_monitor.snapshot(real_device_id)
            failure = public_mirror_failure(
                real_device_id,
                session,
                adb_status,
                i18n.translate("server.status.mirror_restart_failed"),
            )
            return {
                "ok": False,
                "restarted": False,
                "error": failure["error"],
                "adb_state": adb_status.get("state", "unknown"),
                "stream_mode": session.get("stream_mode", "none"),
                "detail": failure["detail"],
                "preferences": public_video_options(options),
                "sessions": await public_sessions_for_user(user),
            }
    storage.audit(user["username"], "mirror_settings", f"{real_device_id}:{json.dumps(public_video_options(options), ensure_ascii=False)}")
    return {"ok": True, "restarted": restart_required, "preferences": public_video_options(options), "sessions": await public_sessions_for_user(user)}


@app.post("/api/devices/{device_id}/mirror/stop")
async def api_mirror_stop(device_id: str, request: Request):
    security.verify_csrf(request)
    user = security.require_user(request)
    real_device_id = resolve_device_or_404(device_id)
    if not storage.user_can(user["username"], real_device_id, "view"):
        raise HTTPException(status_code=403, detail=i18n.translate("server.error.device_denied"))
    ok = await manager.stop(real_device_id)
    storage.audit(user["username"], "mirror_stop", real_device_id)
    return {"ok": ok, "sessions": await public_sessions_for_user(user)}


@app.post("/api/devices/{device_id}/mirror/idle-stop")
async def api_mirror_idle_stop(device_id: str, request: Request):
    security.verify_csrf(request)
    user = security.require_user(request)
    real_device_id = resolve_device_or_404(device_id)
    if not storage.user_can(user["username"], real_device_id, "view"):
        raise HTTPException(status_code=403, detail=i18n.translate("server.error.device_denied"))
    ok = await manager.stop_if_no_clients(real_device_id, wait_seconds=2)
    storage.audit(user["username"], "mirror_idle_stop", f"{real_device_id}: stopped={ok}")
    return {"ok": True, "stopped": ok, "sessions": await public_sessions_for_user(user)}


@app.post("/api/devices/{device_id}/control/acquire")
async def api_control_acquire(device_id: str, request: Request):
    security.verify_csrf(request)
    user = security.require_user(request)
    real_device_id = resolve_device_or_404(device_id)
    if not storage.user_can(user["username"], real_device_id, "control"):
        raise HTTPException(status_code=403, detail=i18n.translate("server.error.device_denied"))
    payload = await parse_body(request)
    force = bool(payload.get("force") and user["role"] == "admin")
    result, _epoch = acquire_control_lock(real_device_id, user["username"], "http", force=force)
    await manager.broadcast({"type": "control_lock", "device_id": real_device_id, "lock": storage.get_lock(real_device_id)})
    return result


@app.post("/api/devices/{device_id}/control/release")
async def api_control_release(device_id: str, request: Request):
    security.verify_csrf(request)
    user = security.require_user(request)
    real_device_id = resolve_device_or_404(device_id)
    ok = release_control_lock(real_device_id, user["username"], force=user["role"] == "admin", client_id="http")
    await manager.broadcast({"type": "control_lock", "device_id": real_device_id, "lock": storage.get_lock(real_device_id)})
    return {"ok": ok}


@app.get("/api/alas/status")
async def api_alas_status(request: Request):
    user = security.require_user(request)
    requested = str(request.query_params.get("config") or "").strip()
    binding = alas_binding_for_user(
        user,
        allow_admin_global=user.get("role") == "admin",
        config_name=requested or None,
    )
    if requested and not binding:
        raise HTTPException(status_code=403, detail=i18n.translate("server.error.alas_config_not_bound"))
    if not binding:
        return {
            "ok": False,
            "configured": False,
            "status": "unbound",
            "task": "",
            "config": "",
            "can_run": False,
            "can_edit": False,
            "error": i18n.translate("server.error.alas_config_not_bound"),
        }
    try:
        config_name = alas.sanitize_config_name(binding.get("config_name"))
    except ValueError:
        return {"ok": False, "configured": False, "status": "invalid_config", "config": "", "can_run": False, "can_edit": False}
    result = await asyncio.to_thread(alas.status_for_config, config_name, False)
    binding["config_name"] = config_name
    return public_alas_status(result, binding)


@app.get("/api/alas/configs")
async def api_alas_configs(request: Request):
    user = security.require_user(request)
    configs = public_user_alas_bindings(user)
    default_config = next((item["config_name"] for item in configs if item["is_default"]), "")
    if not default_config and len(configs) == 1:
        default_config = configs[0]["config_name"]
    return {"configs": configs, "default_config": default_config}


@app.post("/api/alas/toggle")
async def api_alas_toggle(request: Request):
    security.verify_csrf(request)
    user = security.require_user(request)
    payload = await parse_body(request)
    requested = str(payload.get("config_name") or payload.get("config") or "").strip()
    binding = require_alas_binding(user, config_name=requested or None, run=True)
    result = await asyncio.to_thread(alas.control_for_config, "toggle", binding["config_name"])
    if not result.get("ok"):
        storage.audit(user["username"], "alas_toggle_failed", f"{binding['config_name']}:{json.dumps(result, ensure_ascii=False)[:300]}")
        return {
            "ok": False,
            "error": server_error_message(result.get("error")) or i18n.translate("server.status.alas_operation_failed"),
            "status_code": result.get("status_code"),
            "config": binding["config_name"],
        }
    if isinstance(result.get("alas"), dict):
        result["alas"] = public_alas_status(result["alas"], binding)
    storage.audit(user["username"], "alas_toggle", f"{binding['config_name']}:{json.dumps(result, ensure_ascii=False)[:300]}")
    return {"ok": True, "action": result.get("action"), "config": binding["config_name"], "alas": result.get("alas")}


@app.get("/alas/embed")
@app.get("/alas/embed/", response_class=HTMLResponse)
async def alas_embed_page(request: Request):
    """返回 ALAS 原页面 iframe 外壳入口。"""
    redirect = redirect_to_login(request)
    if redirect:
        return redirect
    user = security.require_user(request)
    requested = str(request.query_params.get("config") or "").strip()
    binding = alas_binding_for_user(
        user,
        allow_admin_global=user.get("role") == "admin",
        config_name=requested or None,
    )
    if user.get("role") != "admin" and not binding:
        storage.audit(
            user["username"],
            "alas_embed_denied",
            alas_embed_denial_detail(request, "missing_binding", "/alas/embed/"),
        )
        raise HTTPException(status_code=403, detail=alas_embed_denied_message("missing_binding"))
    if not binding:
        raise HTTPException(
            status_code=400 if requested else 403,
            detail=i18n.translate("server.error.invalid_alas_config"),
        )
    if user.get("role") == "admin":
        config_name = alas.sanitize_config_name(binding.get("config_name"))
        iframe_src = f"/alas/embed/proxy/?{urlencode({'config': config_name})}" if requested else "/alas/embed/proxy/"
        storage.audit(user["username"], "alas_embed_open", f"admin:{config_name}" if requested else "admin")
        return HTMLResponse(
            alas_embed.embed_shell_html(
                i18n.translate("alas.shell.admin_title"),
                iframe_src,
                i18n.translate("alas.shell.admin_message"),
            )
        )
    config_name = alas.sanitize_config_name(binding.get("config_name"))
    storage.audit(user["username"], "alas_embed_open", config_name)
    return HTMLResponse(
        alas_embed.embed_shell_html(
            i18n.translate("alas.shell.user_title", config=config_name),
            f"/alas/embed/proxy/?{urlencode({'config': config_name})}",
            i18n.translate("alas.shell.bound_message", config=config_name),
        )
    )


@app.api_route("/alas/embed/proxy", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
@app.api_route("/alas/embed/proxy/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
async def alas_embed_proxy(request: Request, path: str = ""):
    """执行 ALAS HTTP 代理权限检查并转发到 Runtime。"""
    body = b""
    parsed_body = None
    if request.method.upper() not in alas_embed.SAFE_METHODS:
        security.verify_csrf(request)
        body = await request.body()
        parsed_body = alas_embed.parse_limited_body(body, request.headers.get("content-type", ""))
    user = security.require_user(request)
    query_params = {key: request.query_params.getlist(key) for key in request.query_params.keys()}
    binding = alas_binding_for_user(
        user,
        allow_admin_global=user.get("role") == "admin",
        config_name=requested_alas_config(request.query_params),
    )
    if not binding and user.get("role") != "admin":
        binding = alas_binding_for_user(user)
    decision = alas_embed.proxy_decision(user, binding, path, query_params, method=request.method, body=parsed_body)
    if not decision.allowed:
        reason_code = alas_embed_reason_code(decision.reason)
        denied_message = alas_embed_denied_message(decision.reason)
        log_alas_embed_denied(user, binding, "http", path or "/", reason_code)
        storage.audit(user["username"], "alas_embed_denied", alas_embed_denial_detail(request, reason_code, path or "/"))
        denied_html = alas_embed_denied_html_response(request, binding, decision.status_code, denied_message)
        if denied_html is not None:
            return denied_html
        raise HTTPException(status_code=decision.status_code, detail=denied_message)
    settings = alas.public_settings()
    raw_enabled = storage.get_setting("alas_enabled", "false")
    if not settings.get("enabled") or str(raw_enabled).strip().lower() not in ("1", "true", "yes", "on"):
        log_alas_embed_denied(user, binding, "http", path or "/", "disabled")
        storage.audit(user["username"], "alas_embed_denied", alas_embed_denial_detail(request, "disabled", path or "/"))
        raise HTTPException(status_code=400, detail=i18n.translate("server.status.alas_control_disabled"))
    if not settings.get("base_url"):
        log_alas_embed_denied(user, binding, "http", path or "/", "unconfigured")
        storage.audit(user["username"], "alas_embed_denied", alas_embed_denial_detail(request, "unconfigured", path or "/"))
        raise HTTPException(status_code=502, detail=i18n.translate("server.status.alas_runtime_not_configured"))
    try:
        return await alas_embed.proxy_http_request(request, settings.get("base_url"), path, decision, body=body)
    except HTTPException as exc:
        if exc.status_code == 502:
            storage.audit(
                user["username"],
                "alas_embed_proxy_failed",
                alas_embed_denial_detail(request, "upstream_unreachable", path or "/"),
            )
            raise HTTPException(
                status_code=502,
                detail=i18n.translate("server.status.alas_runtime_unreachable"),
            ) from exc
        raise


@app.websocket("/alas/embed/proxy")
@app.websocket("/alas/embed/proxy/{path:path}")
async def alas_embed_websocket(websocket: WebSocket, path: str = ""):
    """执行 ALAS WebSocket 代理入口权限检查并转发到 Runtime。"""
    connection_id = uuid.uuid4().hex[:12]
    if not security.websocket_origin_allowed(websocket):
        log_alas_websocket_close(connection_id, None, "origin_denied", 4403)
        await websocket.close(code=4403)
        return
    user = security.get_current_user(websocket)
    if not user:
        log_alas_websocket_close(connection_id, None, "authentication_required", 1008)
        await websocket.close(code=1008)
        return
    query_params = {key: websocket.query_params.getlist(key) for key in websocket.query_params.keys()}
    binding = alas_binding_for_user(
        user,
        allow_admin_global=user.get("role") == "admin",
        config_name=requested_alas_config(websocket.query_params),
    )
    if not binding and user.get("role") != "admin":
        binding = alas_binding_for_user(user)
    decision = alas_embed.proxy_decision(user, binding, path, query_params, method="WEBSOCKET")
    if not decision.allowed:
        reason_code = alas_embed_reason_code(decision.reason)
        permission = "run" if reason_code == "run_permission_denied" else (
            "edit" if reason_code == "edit_permission_denied" else "restricted"
        )
        log_alas_websocket_close(connection_id, user, reason_code, 1008, permission=permission)
        storage.audit(user["username"], "alas_embed_ws_denied", alas_embed_denial_detail(websocket, reason_code, path or "/"))
        await websocket.close(code=1008)
        return
    settings = alas.public_settings()
    raw_enabled = storage.get_setting("alas_enabled", "false")
    raw_base_url = storage.get_setting("alas_base_url", "")
    if not settings.get("enabled") or str(raw_enabled).strip().lower() not in ("1", "true", "yes", "on"):
        log_alas_websocket_close(connection_id, user, "disabled", 1011, phase="configuration")
        storage.audit(user["username"], "alas_embed_ws_denied", alas_embed_denial_detail(websocket, "disabled", path or "/"))
        await websocket.close(code=1011)
        return
    if not raw_base_url.strip():
        log_alas_websocket_close(connection_id, user, "unconfigured", 1011, phase="configuration")
        storage.audit(user["username"], "alas_embed_ws_denied", alas_embed_denial_detail(websocket, "unconfigured", path or "/"))
        await websocket.close(code=1011)
        return

    async def refresh_binding():
        if not decision.filtered:
            return None
        return await asyncio.to_thread(
            storage.get_user_alas_binding,
            user.get("username", ""),
            decision.config_name,
        )

    username = user.get("username", "")
    if not await register_current_user_websocket(websocket, user):
        return
    try:
        await alas_embed.proxy_websocket(
            websocket,
            settings.get("base_url") or raw_base_url,
            path,
            decision,
            actor=username,
            role=user.get("role", ""),
            connection_id=connection_id,
            authorization_check=refresh_binding if decision.filtered else None,
        )
    finally:
        await account_connections.unregister(username, websocket)


@app.get("/api/admin/overview")
async def admin_overview(request: Request):
    user = security.require_admin(request)
    devices, users, _alas_bindings, alas_status = await asyncio.to_thread(admin_overview_storage_payload)
    sessions = await manager.snapshot()
    statuses = adb_monitor.snapshot()
    return {
        "user": user_payload(user),
        "devices": devices_payload(devices, sessions, statuses),
        "sessions": sessions,
        "users": users,
        "alas": alas_status,
    }


@app.get("/api/admin/overview/alas")
async def admin_overview_alas(request: Request):
    security.require_admin(request)
    alas_bindings = await asyncio.to_thread(storage.list_user_alas_bindings)
    return await admin_alas_overview(alas_bindings)


@app.get("/api/admin/users")
async def admin_users(request: Request):
    security.require_admin(request)
    return {"users": storage.list_users()}


@app.put("/api/admin/users")
async def admin_upsert_user(request: Request):
    security.verify_csrf(request)
    admin = security.require_admin(request)
    payload = await parse_body(request)
    username = str(payload.get("username", "")).strip()
    password = payload.get("password")
    password = str(password) if password else None
    role = str(payload.get("role", "user"))
    previous = storage.get_user(username)
    try:
        if "expires_at" in payload:
            storage.upsert_user(username, password, role, expires_at=payload.get("expires_at"))
        else:
            storage.upsert_user(username, password, role)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=server_error_message(exc)) from exc
    updated = storage.get_user(username)
    should_close_connections = "expires_at" in payload or bool(password) or bool(
        previous and updated and previous["role"] != updated["role"]
    ) or not storage.user_is_active(updated) or bool(previous and not storage.user_is_active(previous))
    if should_close_connections:
        await account_connections.close_user_connections(username)
    storage.audit(admin["username"], "user_upsert", username)
    return {"ok": True, "users": storage.list_users()}


@app.delete("/api/admin/users/{username}")
async def admin_delete_user(username: str, request: Request):
    security.verify_csrf(request)
    admin = security.require_admin(request)
    if username == admin["username"]:
        raise HTTPException(status_code=403, detail=i18n.translate("server.error.cannot_delete_current_admin"))
    try:
        storage.delete_user(username)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=server_error_message(exc)) from exc
    await account_connections.close_user_connections(username)
    storage.audit(admin["username"], "user_delete", username)
    return {"ok": True, "users": storage.list_users()}


@app.get("/api/admin/devices")
async def admin_devices(request: Request):
    security.require_admin(request)
    sessions = await manager.snapshot()
    return {"devices": devices_payload(storage.list_all_devices(), sessions, adb_monitor.snapshot()), "sessions": sessions}


@app.put("/api/admin/devices")
async def admin_upsert_device(request: Request):
    security.verify_csrf(request)
    admin = security.require_admin(request)
    payload = await parse_body(request)
    device_id = str(payload.get("device_id", "")).strip()
    address = str(payload.get("address", "")).strip()
    if not address:
        raise HTTPException(status_code=400, detail=i18n.translate("server.error.address_required"))
    name = str(payload.get("name", "")).strip() or address
    enabled = parse_bool(payload.get("enabled"), True)
    previous = storage.get_device(device_id) if device_id else None
    if device_id and not previous:
        raise HTTPException(status_code=404, detail=i18n.translate("server.error.device_not_found"))
    disabling = bool(previous and bool(previous["enabled"]) and not enabled)
    notify_users = await manager.event_usernames_for_device(device_id) if disabling else None
    if previous:
        adb_monitor.invalidate_device(device_id)
        if not storage.update_device(device_id, name, address, enabled):
            raise HTTPException(status_code=404, detail=i18n.translate("server.error.device_not_found"))
    else:
        device_id = storage.create_device(name, address, enabled)
    if admin["role"] == "admin":
        storage.set_permission(admin["username"], device_id, True, True)
    storage.audit(admin["username"], "device_upsert", device_id)
    address_changed = bool(previous and str(previous["address"]) != address)
    if previous and not enabled and (bool(previous["enabled"]) or address_changed):
        await manager.remove_device(device_id, reason="device_disabled", notify_users=notify_users)
    elif address_changed:
        await manager.reconfigure_device(device_id, address)
    await adb_monitor.reconnect_device(device_id)
    sessions = await manager.snapshot()
    return {
        "ok": True,
        "device_id": device_id,
        "devices": devices_payload(storage.list_all_devices(), sessions, adb_monitor.snapshot()),
    }


@app.post("/api/admin/devices/{device_id}/adb/test")
async def admin_test_adb_device(device_id: str, request: Request):
    security.verify_csrf(request)
    admin = security.require_admin(request)
    device = storage.get_device(device_id)
    if not device:
        raise HTTPException(status_code=404, detail=i18n.translate("server.error.device_not_found"))
    result = await adb_monitor.reconnect_device(device_id)
    storage.audit(admin["username"], "device_adb_test", f"{device_id}:{result.get('state')}")
    return result


@app.get("/api/admin/adb/status")
async def admin_adb_status(request: Request):
    security.require_admin(request)
    return {"devices": adb_monitor.snapshot()}


@app.post("/api/admin/devices/{device_id}/adb/reconnect")
async def admin_reconnect_adb_device(device_id: str, request: Request):
    security.verify_csrf(request)
    admin = security.require_admin(request)
    if not storage.get_device(device_id):
        raise HTTPException(status_code=404, detail=i18n.translate("server.error.device_not_found"))
    result = await adb_monitor.reconnect_device(device_id)
    storage.audit(admin["username"], "device_adb_reconnect", f"{device_id}:{result.get('state')}")
    return result


@app.delete("/api/admin/devices/{device_id}")
async def admin_delete_device(device_id: str, request: Request):
    security.verify_csrf(request)
    admin = security.require_admin(request)
    if not storage.get_device(device_id):
        raise HTTPException(status_code=404, detail=i18n.translate("server.error.device_not_found"))
    notify_users = await manager.event_usernames_for_device(device_id)
    adb_monitor.forget_device(device_id)
    try:
        if not storage.delete_device(device_id):
            raise HTTPException(status_code=404, detail=i18n.translate("server.error.device_not_found"))
    except Exception:
        adb_monitor.restore_device(device_id)
        raise
    await manager.remove_device(device_id, notify_users=notify_users)
    storage.audit(admin["username"], "device_delete", device_id)
    sessions = await manager.snapshot()
    return {"ok": True, "devices": devices_payload(storage.list_all_devices(), sessions, adb_monitor.snapshot())}


@app.get("/api/admin/permissions")
async def admin_permissions(request: Request):
    security.require_admin(request)
    sessions = await manager.snapshot()
    return {"permissions": storage.list_permissions(), "users": storage.list_users(), "devices": devices_payload(storage.list_all_devices(), sessions, adb_monitor.snapshot())}


@app.put("/api/admin/permissions")
async def admin_set_permission(request: Request):
    security.verify_csrf(request)
    admin = security.require_admin(request)
    payload = await parse_body(request)
    username = str(payload.get("username", "")).strip()
    device_id = str(payload.get("device_id", "")).strip()
    can_view = parse_bool(payload.get("can_view"), True)
    can_control = parse_bool(payload.get("can_control"), False)
    try:
        storage.set_permission(username, device_id, can_view, can_control)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=server_error_message(exc)) from exc
    storage.audit(admin["username"], "permission_set", f"{username}:{device_id}:{can_view}:{can_control}")
    return {"ok": True, "permissions": storage.list_permissions()}


@app.get("/api/admin/video")
async def admin_video_settings(request: Request):
    security.require_admin(request)
    keys = ["video_profile", "video_adaptive", "video_bit_rate", "max_size", "max_fps", "auto_stop_minutes", "scrcpy_stream_mode", "scrcpy_enabled_stream_modes", "video_custom_profiles", "video_fullscreen_profile"] + profile_setting_keys()
    settings = storage.get_settings(keys)
    enabled_modes = enabled_stream_modes_value(settings.get("scrcpy_enabled_stream_modes", "raw"))
    stream_mode = str(settings.get("scrcpy_stream_mode") or "raw").strip().lower()
    if stream_mode not in ("raw", "protocol", "legacy"):
        stream_mode = "raw"
    stream_mode = stream_mode_or_default(stream_mode, enabled_modes)
    settings["scrcpy_stream_mode"] = stream_mode
    settings["scrcpy_enabled_stream_modes"] = ",".join(enabled_modes)
    profiles = profile_payloads(settings)
    fullscreen_profile = fullscreen_profile_value(settings.get("video_fullscreen_profile"), profiles)
    settings["video_fullscreen_profile"] = fullscreen_profile
    return {
        "settings": settings,
        "defaults": public_video_options(settings_to_video_options(settings)),
        "profiles": profiles,
        "profile_labels": profile_label_payloads(settings),
        "custom_profiles": custom_profile_payloads(settings),
        "bandwidth_recommendations": BANDWIDTH_RECOMMENDATIONS,
        "limits": VIDEO_LIMITS,
        "stream_modes": ["raw", "protocol", "legacy"],
        "enabled_stream_modes": list(enabled_modes),
        "stream_mode": stream_mode,
        "profile_names": list(PROFILE_NAMES),
        "profile_fields": list(PROFILE_FIELDS),
        "min_preset_max_size": MIN_PRESET_MAX_SIZE,
        "max_preset_max_size": MAX_PRESET_MAX_SIZE,
        "fullscreen_profile": fullscreen_profile,
        "fullscreen_min_max_size": FULLSCREEN_MIN_MAX_SIZE,
    }


@app.put("/api/admin/video")
async def admin_save_video_settings(request: Request):
    security.verify_csrf(request)
    admin = security.require_admin(request)
    payload = await parse_body(request)
    keys = ["video_profile", "video_adaptive", "video_bit_rate", "max_size", "max_fps", "auto_stop_minutes", "scrcpy_stream_mode", "scrcpy_enabled_stream_modes", "video_custom_profiles", "video_fullscreen_profile"] + profile_setting_keys()

    def build_video_updates(current_settings):
        enabled_modes_submitted = "scrcpy_enabled_stream_modes" in payload or "enabled_stream_modes" in payload
        raw_enabled_modes = payload.get("scrcpy_enabled_stream_modes", payload.get("enabled_stream_modes", current_settings.get("scrcpy_enabled_stream_modes", "raw")))
        enabled_modes = enabled_stream_modes_value(raw_enabled_modes)
        current_profiles = profile_payloads(current_settings)
        raw_presets = payload.get("presets") if "presets" in payload else None
        if raw_presets is not None and not isinstance(raw_presets, dict):
            raise VideoOptionError("presets must be an object", "server.video_error.presets_object")
        presets = normalize_profile_payloads(raw_presets, current_profiles)
        custom_profiles = (
            normalize_custom_profile_payloads(payload.get("custom_profiles"))
            if "custom_profiles" in payload
            else custom_profile_payloads(current_settings)
        )
        profile_settings = dict(current_settings)
        for profile, values in presets.items():
            for field, value in values.items():
                profile_settings[profile_setting_key(profile, field)] = str(value)
        serialized_custom_profiles = serialize_custom_profiles(custom_profiles)
        profile_settings["video_custom_profiles"] = serialized_custom_profiles
        profiles = profile_payloads(profile_settings)
        fullscreen_profile = fullscreen_profile_value(
            payload.get("fullscreen_profile", current_settings.get("video_fullscreen_profile", "sharp")),
            profiles,
            strict=True,
        )
        options = normalize_video_options(
            payload,
            settings_to_video_options(current_settings),
            profiles=profiles,
            enabled_stream_modes=enabled_modes,
        )
        auto_stop_submitted = "auto_stop_minutes" in payload or "auto_stop_time" in payload
        raw_auto_stop = payload.get("auto_stop_minutes", payload.get("auto_stop_time", current_settings.get("auto_stop_minutes", "15")))
        try:
            auto_stop = int(raw_auto_stop)
        except Exception as exc:
            raise VideoOptionError(
                "auto_stop_minutes must be integer",
                "server.video_error.must_be_integer",
                field="auto_stop_minutes",
            ) from exc
        if auto_stop < 0 or auto_stop > 1440:
            raise VideoOptionError(
                "auto_stop_minutes out of range",
                "server.video_error.out_of_range",
                field="auto_stop_minutes",
            )
        stream_mode_submitted = "scrcpy_stream_mode" in payload or "stream_mode" in payload
        stream_mode = str(payload.get("scrcpy_stream_mode", payload.get("stream_mode", current_settings.get("scrcpy_stream_mode", "raw")))).strip().lower()
        if stream_mode not in ("raw", "protocol", "legacy"):
            raise VideoOptionError(
                "scrcpy_stream_mode must be raw, protocol, or legacy",
                "server.video_error.invalid_stream_mode",
            )
        stream_mode = stream_mode_or_default(stream_mode, enabled_modes)
        settings_updates = {}
        if enabled_modes_submitted:
            settings_updates["scrcpy_enabled_stream_modes"] = ",".join(enabled_modes)
        if "custom_profiles" in payload:
            settings_updates["video_custom_profiles"] = serialized_custom_profiles
        if "fullscreen_profile" in payload:
            settings_updates["video_fullscreen_profile"] = fullscreen_profile
        if auto_stop_submitted:
            settings_updates["auto_stop_minutes"] = str(auto_stop)
            settings_updates["auto_stop_time"] = str(auto_stop)
        if raw_presets is not None:
            for profile, values in raw_presets.items():
                if profile not in presets or not isinstance(values, dict):
                    continue
                for field in PROFILE_FIELDS:
                    if field in values:
                        settings_updates[profile_setting_key(profile, field)] = str(presets[profile][field])
        public_options = public_video_options(options)
        quality_fields = ("video_bit_rate", "max_size", "max_fps")
        if "profile" in payload:
            for key in ("profile", "adaptive", *quality_fields):
                value = public_options[key]
                settings_updates[{"profile": "video_profile", "adaptive": "video_adaptive"}.get(key, key)] = "true" if value is True else "false" if value is False else str(value)
        else:
            if any(key in payload for key in quality_fields):
                settings_updates["video_profile"] = str(public_options["profile"])
            for key in ("adaptive", *quality_fields):
                if key in payload:
                    value = public_options[key]
                    settings_updates[{"adaptive": "video_adaptive"}.get(key, key)] = "true" if value is True else "false" if value is False else str(value)
        if stream_mode_submitted or enabled_modes_submitted:
            settings_updates["scrcpy_stream_mode"] = stream_mode
        return settings_updates

    try:
        storage.update_settings(build_video_updates)
    except VideoOptionError as exc:
        raise HTTPException(status_code=400, detail=video_option_error_message(exc)) from exc
    storage.audit(admin["username"], "video_settings", json.dumps(payload, ensure_ascii=False)[:400])
    settings = storage.get_settings(keys)
    saved_profiles = profile_payloads(settings)
    saved_fullscreen_profile = fullscreen_profile_value(settings.get("video_fullscreen_profile"), saved_profiles)
    saved_enabled_modes = enabled_stream_modes_value(settings.get("scrcpy_enabled_stream_modes", "raw"))
    return {
        "ok": True,
        "settings": settings,
        "defaults": public_video_options(settings_to_video_options(settings)),
        "profiles": saved_profiles,
        "profile_labels": profile_label_payloads(settings),
        "custom_profiles": custom_profile_payloads(settings),
        "bandwidth_recommendations": BANDWIDTH_RECOMMENDATIONS,
        "enabled_stream_modes": list(saved_enabled_modes),
        "fullscreen_profile": saved_fullscreen_profile,
        "fullscreen_min_max_size": FULLSCREEN_MIN_MAX_SIZE,
    }


@app.get("/api/admin/alas")
async def admin_alas(request: Request):
    security.require_admin(request)
    config_name = request.query_params.get("config")
    return await asyncio.to_thread(admin_alas_payload, config_name)


@app.put("/api/admin/alas")
async def admin_save_alas(request: Request):
    security.verify_csrf(request)
    admin = security.require_admin(request)
    payload = await parse_body(request)
    try:
        await asyncio.to_thread(alas.save_settings, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=server_error_message(exc)) from exc
    storage.audit(admin["username"], "alas_settings", "updated")
    return {"ok": True, **await asyncio.to_thread(admin_alas_payload)}


@app.post("/api/admin/alas/toggle")
async def admin_toggle_alas(request: Request):
    security.verify_csrf(request)
    admin = security.require_admin(request)
    payload = await parse_body(request)
    config_name = str(payload.get("config_name") or payload.get("config") or "").strip()
    if not config_name:
        raise HTTPException(status_code=400, detail=i18n.translate("server.error.alas_config_name_required"))
    try:
        config_name = alas.sanitize_config_name(config_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=i18n.translate("server.error.invalid_alas_config_name")) from exc
    result = await asyncio.to_thread(alas.control_for_config, "toggle", config_name)
    storage.audit(admin["username"], "alas_admin_toggle", json.dumps(result, ensure_ascii=False)[:400])
    if result.get("error"):
        result["error"] = server_error_message(result["error"])
    if isinstance(result.get("alas"), dict) and result["alas"].get("error"):
        result["alas"]["error"] = server_error_message(result["alas"]["error"])
    return result


@app.get("/api/admin/alas/config")
async def admin_alas_config(request: Request):
    security.require_admin(request)
    config_name = str(request.query_params.get("config") or "").strip()
    if not config_name:
        raise HTTPException(status_code=400, detail=i18n.translate("server.error.alas_config_name_required"))
    return await asyncio.to_thread(alas.get_config, config_name)


@app.get("/api/admin/alas/configs")
async def admin_alas_configs(request: Request):
    security.require_admin(request)
    bound_configs = await asyncio.to_thread(bound_alas_config_names)
    settings = alas.public_settings()
    runtime_configs: list[str] = []
    error = ""
    if settings.get("enabled") and settings.get("token_set"):
        try:
            result = await asyncio.to_thread(alas.list_configs)
            error = server_error_message(result.get("error"))
            for raw in result.get("configs") or []:
                try:
                    name = alas.sanitize_config_name(raw)
                except ValueError:
                    continue
                if name not in runtime_configs:
                    runtime_configs.append(name)
        except Exception as exc:
            log.warning("ALAS_CONFIG_CATALOG_FAILED error=%s", exc)
            error = server_error_message(exc)
    configs = list(bound_configs)
    for name in runtime_configs:
        if name not in configs:
            configs.append(name)
    return {
        "configs": configs,
        "runtime_configs": runtime_configs,
        "bound_configs": bound_configs,
        "error": error,
    }


@app.put("/api/admin/alas/config")
async def admin_save_alas_config(request: Request):
    security.verify_csrf(request)
    admin = security.require_admin(request)
    payload = await parse_body(request)
    source = str(payload.get("source", ""))
    target = str(payload.get("target") or source)
    if not source.strip() or not target.strip():
        raise HTTPException(status_code=400, detail=i18n.translate("server.error.alas_config_name_required"))
    data = payload.get("data")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=i18n.translate("server.error.config_valid_json")) from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail=i18n.translate("server.error.config_json_object"))
    result = await asyncio.to_thread(alas.save_config, source, target, data, False)
    storage.audit(admin["username"], "alas_config_save", target or source)
    return result


@app.get("/api/admin/alas/permissions")
async def admin_alas_permissions(request: Request):
    security.require_admin(request)
    return admin_alas_permissions_payload()


@app.put("/api/admin/alas/permissions")
async def admin_set_alas_permission(request: Request):
    security.verify_csrf(request)
    admin = security.require_admin(request)
    payload = await parse_body(request)
    username = str(payload.get("username", "")).strip()
    config_name = str(payload.get("config_name", "")).strip()
    multi_config_request = "is_default" in payload
    enabled = parse_bool(payload.get("enabled"), bool(config_name))
    if not storage.get_user(username):
        raise HTTPException(status_code=400, detail=i18n.translate("server.error.invalid_username"))
    if config_name:
        try:
            config_name = alas.sanitize_config_name(config_name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=i18n.translate("server.error.invalid_alas_config_name")) from exc
    if not enabled:
        if multi_config_request and config_name:
            storage.delete_user_alas_binding(username, config_name)
            detail = f"{username}:{config_name}"
        else:
            storage.delete_user_alas_config(username)
            detail = username
        storage.audit(admin["username"], "alas_binding_delete", detail)
        return {"ok": True, **admin_alas_permissions_payload()}
    if not config_name:
        raise HTTPException(status_code=400, detail=i18n.translate("server.error.alas_config_name_required"))
    can_run = parse_bool(payload.get("can_run"), True)
    can_edit = parse_bool(payload.get("can_edit"), False)
    raw_default = payload.get("is_default")
    is_default = None if raw_default is None else parse_bool(raw_default, False)
    try:
        if multi_config_request:
            storage.upsert_user_alas_binding(username, config_name, can_run, can_edit, is_default)
        else:
            storage.set_user_alas_config(username, config_name, can_run, can_edit)
    except storage.AlasConfigOwnershipError as exc:
        detail = i18n.translate("server.error.alas_config_owned", config=exc.config_name, owner=exc.owner)
        raise HTTPException(status_code=409, detail=detail) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=server_error_message(exc)) from exc
    storage.audit(admin["username"], "alas_binding_set", f"{username}:{config_name}:{can_run}:{can_edit}:{bool(is_default)}")
    return {"ok": True, **admin_alas_permissions_payload()}


@app.get("/api/admin/logs")
async def admin_logs(request: Request):
    security.require_admin(request)
    return {"logs": storage.recent_audit(500)}


@app.get("/api/admin/runtime-logs")
async def admin_runtime_logs(request: Request, lines: int = 300):
    security.require_admin(request)
    return {"logs": await asyncio.to_thread(tail_log, lines), "meta": logging_health()}


@app.websocket("/ws/devices/{device_id}/video")
async def ws_video(websocket: WebSocket, device_id: str):
    if not security.websocket_origin_allowed(websocket):
        await websocket.close(code=4403)
        return
    user = await security.websocket_user(websocket)
    if not user:
        await websocket.close(code=4401)
        return
    real_device_id = storage.resolve_device_ref(device_id)
    if not real_device_id:
        await websocket.close(code=4404)
        return
    username = user["username"]
    if not await register_current_user_websocket(websocket, user):
        return
    try:
        await video_socket(websocket, user, real_device_id, exposed_device_id=device_id)
    finally:
        await account_connections.unregister(username, websocket)


@app.websocket("/ws/devices/{device_id}/control")
async def ws_control(websocket: WebSocket, device_id: str):
    if not security.websocket_origin_allowed(websocket):
        await websocket.close(code=4403)
        return
    user = await security.websocket_user(websocket)
    if not user:
        await websocket.close(code=4401)
        return
    real_device_id = storage.resolve_device_ref(device_id)
    if not real_device_id:
        await websocket.close(code=4404)
        return
    username = user["username"]
    if not await register_current_user_websocket(websocket, user):
        return
    try:
        await control_socket(websocket, user, real_device_id, exposed_device_id=device_id)
    finally:
        await account_connections.unregister(username, websocket)


@app.websocket("/ws/events")
async def ws_events(websocket: WebSocket):
    if not security.websocket_origin_allowed(websocket):
        await websocket.close(code=4403)
        return
    user = await security.websocket_user(websocket)
    if not user:
        await websocket.close(code=4401)
        return
    username = user["username"]
    if not await register_current_user_websocket(websocket, user):
        return
    try:
        await websocket.accept()
        await manager.register_event_ws(websocket, username)
    finally:
        await account_connections.unregister(username, websocket)
