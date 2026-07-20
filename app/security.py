import ipaddress
import logging
import os
import secrets
import time
from functools import wraps
from urllib.parse import urlparse
from fastapi import HTTPException, Request, WebSocket
from fastapi.responses import RedirectResponse

from . import i18n, storage

SESSION_COOKIE = "wsid"
PROXY_HEADERS = ("x-forwarded-for", "x-forwarded-proto", "x-forwarded-host", "x-real-ip")
log = logging.getLogger("webscrcpy.security")
_LOGIN_FAILURES: dict[str, list[float]] = {}
_LOGIN_LOCKOUTS: dict[str, float] = {}
MAX_PENDING_AUDIT_EVENTS = 32


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def env_list(name: str) -> list[str]:
    return [item.strip() for item in os.environ.get(name, "").split(",") if item.strip()]


def env_int(name: str, default: int, minimum: int = 0, maximum: int = 86400) -> int:
    try:
        value = int(os.environ.get(name, str(default)) or default)
    except Exception:
        value = default
    return max(minimum, min(value, maximum))


def public_base_url():
    raw = os.environ.get("PUBLIC_BASE_URL", "").strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    return parsed


def host_from_header(value: str | None) -> str:
    value = (value or "").strip().lower().rstrip(".")
    if not value:
        return ""
    if value.startswith("["):
        return value[1:].split("]", 1)[0]
    return value.split(":", 1)[0]


def normalize_origin(value: str | None) -> str:
    parsed = urlparse((value or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return ""
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{parsed.hostname.lower().rstrip('.')}{port}"


def allowed_hosts() -> set[str]:
    hosts = {"localhost", "127.0.0.1", "::1", "web-scrcpy", "scrcpygate"}
    for item in env_list("ALLOWED_HOSTS"):
        if item != "*":
            host = host_from_header(item)
            if host:
                hosts.add(host)
    public = public_base_url()
    if public and public.hostname:
        hosts.add(public.hostname.lower().rstrip("."))
    return hosts


def allowed_origins() -> set[str]:
    origins = set()
    for item in env_list("ALLOWED_ORIGINS"):
        if item != "*":
            origin = normalize_origin(item)
            if origin:
                origins.add(origin)
    public = public_base_url()
    if public:
        origins.add(normalize_origin(public.geturl()))
    return origins


def request_host_allowed(request: Request) -> bool:
    host = host_from_header(request.headers.get("host"))
    return bool(host and host in allowed_hosts())


def origin_check_exempt(request: Request) -> bool:
    # Logout is CSRF-protected by a per-session token in the route itself.
    # Some WAF/iframe/browser combinations submit the form with Origin: null;
    # let the route validate the token instead of failing before it can logout.
    return (
        request.method.upper() == "POST"
        and request.url.path == "/logout"
        and request.headers.get("origin", "").strip().lower() == "null"
    )


def origin_allowed(origin: str | None, request_host_url: str | None = None) -> bool:
    if origin is None or not str(origin).strip():
        return True
    if str(origin).strip().lower() == "null":
        return env_bool("ALLOW_NULL_ORIGIN", False)
    normalized = normalize_origin(origin)
    if not normalized:
        return False
    if normalized in allowed_origins():
        return True
    if request_host_url:
        return normalized == normalize_origin(request_host_url.rstrip("/"))
    return False


def is_lan_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    return bool(ip.is_private or ip.is_loopback or ip.is_link_local)


def trusted_proxy_nets():
    nets = []
    for raw in env_list("TRUSTED_PROXY_IPS"):
        if raw == "*":
            continue
        try:
            nets.append(ipaddress.ip_network(raw, strict=False))
        except ValueError:
            pass
    return nets


def is_trusted_proxy(remote: str) -> bool:
    try:
        ip = ipaddress.ip_address(remote)
    except ValueError:
        return False
    return any(ip in net for net in trusted_proxy_nets())


def canonical_ip(value: object) -> str:
    candidate = str(value or "").strip()
    if candidate.startswith("[") and candidate.endswith("]"):
        candidate = candidate[1:-1]
    try:
        return ipaddress.ip_address(candidate).compressed
    except ValueError:
        return ""


def client_ip(request: Request) -> str:
    remote = request.client.host if request.client else ""
    if env_bool("TRUST_PROXY", False) and is_trusted_proxy(remote):
        forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
        forwarded_ip = canonical_ip(forwarded)
        if forwarded_ip:
            return forwarded_ip
    return canonical_ip(remote) or "unknown"


def _audit_route(request: Request) -> str:
    route = getattr(request.scope.get("route"), "path", "") if hasattr(request, "scope") else ""
    path = str(route or getattr(getattr(request, "url", None), "path", "/") or "/")
    if path.startswith("/alas/embed/proxy"):
        return "/alas/embed/proxy/*"
    return path[:240]


def queue_audit_event(
    request: Request,
    *,
    action: str,
    outcome: str = "denied",
    reason: str = "",
    severity: str = "warning",
    username: str = "",
    actor_role: str = "",
    target_type: str = "route",
    target_id: str = "",
    detail: str = "",
    metadata: dict | None = None,
    dedupe_key: str = "",
) -> bool:
    """Queue one bounded audit event for the HTTP middleware to persist safely."""
    try:
        state = request.state
    except Exception:
        return False
    events = getattr(state, "audit_events", None)
    if not isinstance(events, list):
        events = []
        state.audit_events = events
    if len(events) >= MAX_PENDING_AUDIT_EVENTS:
        return False
    if not username:
        try:
            user = get_current_user(request)
        except Exception:
            user = None
        if user:
            username = str(user.get("username") or "")
            actor_role = actor_role or str(user.get("role") or "")
    events.append(
        {
            "username": username or "anonymous",
            "actor_role": actor_role or "unknown",
            "action": action,
            "detail": detail,
            "outcome": outcome,
            "reason": reason,
            "severity": severity,
            "target_type": target_type,
            "target_id": target_id or _audit_route(request),
            "request_id": str(getattr(state, "request_id", "") or ""),
            "source_ip": client_ip(request),
            "user_agent": str(request.headers.get("user-agent", "") or ""),
            "metadata": {"http_method": str(getattr(request, "method", "") or "").upper(), **(metadata or {})},
            "dedupe_key": dedupe_key,
        }
    )
    return True


def pop_audit_events(request: Request) -> list[dict]:
    try:
        events = request.state.audit_events
        request.state.audit_events = []
    except Exception:
        return []
    return list(events) if isinstance(events, list) else []


def has_pending_audit_events(request: Request) -> bool:
    try:
        return bool(request.state.audit_events)
    except Exception:
        return False


def enforce_http_boundary(request: Request) -> None:
    if request.url.path == "/healthz":
        return
    remote = request.client.host if request.client else ""
    if not proxy_headers_allowed(request.headers, remote, request.url.path):
        queue_audit_event(request, action="http_boundary", reason="untrusted_proxy_headers")
        raise HTTPException(status_code=403, detail=i18n.translate("server.security.forbidden"))
    if not request_host_allowed(request):
        log.warning("HOST_REJECT path=%s host=%s allowed=%s", request.url.path, request.headers.get("host", ""), sorted(allowed_hosts()))
        queue_audit_event(request, action="http_boundary", reason="host_rejected")
        raise HTTPException(status_code=400, detail=i18n.translate("server.security.bad_request"))
    origin = request.headers.get("origin")
    if origin and not origin_check_exempt(request) and not origin_allowed(origin, str(request.base_url).rstrip("/")):
        log.warning("ORIGIN_REJECT path=%s origin=%s base=%s allowed=%s", request.url.path, origin, str(request.base_url).rstrip("/"), sorted(allowed_origins()))
        queue_audit_event(request, action="http_boundary", reason="origin_rejected")
        raise HTTPException(status_code=403, detail=i18n.translate("server.security.forbidden"))


def get_current_session(request: Request) -> dict | None:
    return storage.get_session(request.cookies.get(SESSION_COOKIE))


def get_current_user(request: Request) -> dict | None:
    sess = get_current_session(request)
    if not sess:
        return None
    user = storage.get_user(sess["username"])
    return dict(user) if user else None


def require_user(request: Request) -> dict:
    user = get_current_user(request)
    if not user:
        queue_audit_event(request, action="authentication", reason="login_required")
        raise HTTPException(status_code=401, detail=i18n.translate("server.security.login_required"))
    return user


def require_admin(request: Request) -> dict:
    user = require_user(request)
    if user.get("role") != "admin":
        queue_audit_event(
            request,
            action="admin_access",
            reason="admin_required",
            username=str(user.get("username") or ""),
            actor_role=str(user.get("role") or ""),
        )
        raise HTTPException(status_code=403, detail=i18n.translate("server.security.admin_required"))
    return user


def csrf_valid(request: Request, provided: str) -> bool:
    sess = get_current_session(request)
    expected = str(sess.get("csrf_token", "")) if sess else ""
    return bool(sess and provided and expected and secrets.compare_digest(provided, expected))


def verify_csrf_token(request: Request, provided: str) -> None:
    if not csrf_valid(request, provided):
        queue_audit_event(request, action="csrf_validation", reason="token_invalid")
        raise HTTPException(status_code=400, detail=i18n.translate("server.security.csrf_failed"))


def verify_csrf(request: Request) -> None:
    verify_csrf_token(request, request.headers.get("x-csrf-token", ""))


def redirect_if_not_logged_in(request: Request):
    if get_current_user(request):
        return None
    return RedirectResponse("/login", status_code=302)


async def websocket_user(ws: WebSocket) -> dict | None:
    sid = ws.cookies.get(SESSION_COOKIE)
    sess = storage.get_session(sid)
    if not sess:
        return None
    user = storage.get_user(sess["username"])
    return dict(user) if user else None


def proxy_headers_allowed(headers, remote: str, path: str) -> bool:
    has_proxy = any(headers.get(header) for header in PROXY_HEADERS)
    if not has_proxy:
        return True
    if env_bool("TRUST_PROXY", False) and is_trusted_proxy(remote):
        return True
    used = ",".join(header for header in PROXY_HEADERS if headers.get(header))
    log.warning("PROXY_HEADER_REJECT path=%s remote=%s headers=%s trust_proxy=%s", path, remote, used, env_bool("TRUST_PROXY", False))
    return False


def websocket_origin_allowed(ws: WebSocket) -> bool:
    host = ws.headers.get("host")
    origin = ws.headers.get("origin")
    remote = ws.client.host if ws.client else ""
    if not proxy_headers_allowed(ws.headers, remote, ws.url.path):
        return False
    if host_from_header(host) not in allowed_hosts():
        return False
    proto = ws.headers.get("x-forwarded-proto") or ws.url.scheme.replace("ws", "http", 1)
    if origin and not origin_allowed(origin, f"{proto}://{host}"):
        return False
    return True


def secure_cookie_enabled() -> bool:
    return env_bool("SESSION_COOKIE_SECURE", True)


def _login_rate_limit_values() -> tuple[int, int, int]:
    max_attempts = env_int("LOGIN_RATE_LIMIT_MAX", 6, 1, 100)
    window_seconds = env_int("LOGIN_RATE_LIMIT_WINDOW_SECONDS", 300, 10, 86400)
    lockout_seconds = env_int("LOGIN_LOCKOUT_SECONDS", 600, 10, 86400)
    return max_attempts, window_seconds, lockout_seconds


def _login_keys(request: Request, username: str) -> list[str]:
    ip = client_ip(request) or "unknown"
    normalized_user = (username or "").strip().lower()[:64] or "anonymous"
    return [f"ip:{ip}", f"userip:{ip}:{normalized_user}"]


def _prune_login_failures(key: str, now: float, window_seconds: int) -> list[float]:
    attempts = [ts for ts in _LOGIN_FAILURES.get(key, []) if now - ts <= window_seconds]
    if attempts:
        _LOGIN_FAILURES[key] = attempts
    else:
        _LOGIN_FAILURES.pop(key, None)
    return attempts


def login_rate_limit_status(request: Request, username: str) -> dict:
    if not env_bool("LOGIN_RATE_LIMIT_ENABLED", True):
        return {"limited": False, "retry_after": 0}
    _max_attempts, window_seconds, _lockout_seconds = _login_rate_limit_values()
    now = time.time()
    retry_after = 0
    for key in _login_keys(request, username):
        lockout_until = _LOGIN_LOCKOUTS.get(key, 0)
        if lockout_until <= now:
            _LOGIN_LOCKOUTS.pop(key, None)
            _prune_login_failures(key, now, window_seconds)
            continue
        retry_after = max(retry_after, int(lockout_until - now) + 1)
    return {"limited": retry_after > 0, "retry_after": retry_after}


def record_login_failure(request: Request, username: str) -> dict:
    if not env_bool("LOGIN_RATE_LIMIT_ENABLED", True):
        return {"limited": False, "retry_after": 0}
    max_attempts, window_seconds, lockout_seconds = _login_rate_limit_values()
    now = time.time()
    limited = False
    retry_after = 0
    for key in _login_keys(request, username):
        attempts = _prune_login_failures(key, now, window_seconds)
        attempts.append(now)
        _LOGIN_FAILURES[key] = attempts
        if len(attempts) >= max_attempts:
            _LOGIN_LOCKOUTS[key] = now + lockout_seconds
            _LOGIN_FAILURES.pop(key, None)
            limited = True
            retry_after = max(retry_after, lockout_seconds)
    return {"limited": limited, "retry_after": retry_after}


def record_login_success(request: Request, username: str) -> None:
    if not env_bool("LOGIN_RATE_LIMIT_ENABLED", True):
        return
    ip = client_ip(request) or "unknown"
    normalized_user = (username or "").strip().lower()[:64] or "anonymous"
    key = f"userip:{ip}:{normalized_user}"
    _LOGIN_FAILURES.pop(key, None)
    _LOGIN_LOCKOUTS.pop(key, None)


def clear_login_rate_limits() -> None:
    _LOGIN_FAILURES.clear()
    _LOGIN_LOCKOUTS.clear()
