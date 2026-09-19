import asyncio
import ipaddress
import logging
import os
import queue
import secrets
import threading
import time
from functools import lru_cache
from urllib.parse import urlparse
from fastapi import HTTPException, Request, WebSocket

from . import alas_gateway, i18n, storage
from .booleans import InvalidBooleanValue, parse_bool_strict

SESSION_COOKIE = "wsid"
# 会触发「来源边界校验」的转发类头。`forwarded`（RFC 7239）**不参与来源判定**
# （client_ip 只读 X-Forwarded-For），但同样必须出现在这个集合里：不可信对端发任何
# 转发头都应当被明确拒绝（403），而不是因为「我们没解析它」而静默放行——
# 静默放行会让「到底谁在代理」这件事在排查时完全看不出来。
PROXY_HEADERS = ("x-forwarded-for", "x-forwarded-proto", "x-forwarded-host", "x-real-ip", "forwarded")
log = logging.getLogger("webscrcpy.security")
_LOGIN_FAILURES: dict[str, list[tuple[str, float]]] = {}
_LOGIN_LOCKOUTS: dict[str, float] = {}
_LOGIN_LOCKOUT_USERS: dict[str, set[str]] = {}
_LOGIN_STATE_RECENCY: dict[str, None] = {}
_LOGIN_RATE_LIMIT_LOCK = threading.RLock()
_LOGIN_STATE_OPERATIONS = 0
_MAX_LOGIN_RATE_LIMIT_KEYS = 10_000
_LOGIN_STATE_PRUNE_INTERVAL = 128
MAX_PENDING_AUDIT_EVENTS = 32


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def env_int(name: str, default: int, minimum: int = 0, maximum: int = 86400) -> int:
    try:
        value = int(os.environ.get(name, str(default)) or default)
    except Exception:
        value = default
    return max(minimum, min(value, maximum))


@lru_cache(maxsize=64)
def _public_base_url_from_raw(raw: str):
    raw = raw.strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    return parsed


def public_base_url():
    return _public_base_url_from_raw(os.environ.get("PUBLIC_BASE_URL", ""))


def host_from_header(value: str | None) -> str:
    value = (value or "").strip().lower().rstrip(".")
    if not value:
        return ""
    if value.startswith("["):
        return value[1:].split("]", 1)[0]
    return value.split(":", 1)[0]


def normalize_origin(value: str | None) -> str:
    try:
        parsed = urlparse((value or "").strip())
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return ""
        port = parsed.port
    except ValueError:
        return ""
    port = f":{port}" if port else ""
    host = parsed.hostname.lower().rstrip(".")
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{parsed.scheme}://{host}{port}"


@lru_cache(maxsize=64)
def _allowed_hosts_from_raw(allowed_hosts_raw: str, public_base_raw: str) -> frozenset[str]:
    hosts = {"localhost", "127.0.0.1", "::1", "web-scrcpy", "scrcpygate"}
    for item in (value.strip() for value in allowed_hosts_raw.split(",")):
        if item != "*":
            host = host_from_header(item)
            if host:
                hosts.add(host)
    public = _public_base_url_from_raw(public_base_raw)
    if public and public.hostname:
        hosts.add(public.hostname.lower().rstrip("."))
    return frozenset(hosts)


def allowed_hosts() -> frozenset[str]:
    hosts = set(_allowed_hosts_from_raw(
        os.environ.get("ALLOWED_HOSTS", ""),
        os.environ.get("PUBLIC_BASE_URL", ""),
    ))
    origin = alas_gateway.configured_origin()
    if origin:
        parsed = urlparse(origin)
        if parsed.hostname:
            hosts.add(parsed.hostname.lower().rstrip("."))
    return frozenset(hosts)


@lru_cache(maxsize=64)
def _allowed_origins_from_raw(allowed_origins_raw: str, public_base_raw: str) -> frozenset[str]:
    origins = set()
    for item in (value.strip() for value in allowed_origins_raw.split(",")):
        if item != "*":
            origin = normalize_origin(item)
            if origin:
                origins.add(origin)
    public = _public_base_url_from_raw(public_base_raw)
    if public:
        origins.add(normalize_origin(public.geturl()))
    return frozenset(origins)


def allowed_origins() -> frozenset[str]:
    origins = set(_allowed_origins_from_raw(
        os.environ.get("ALLOWED_ORIGINS", ""),
        os.environ.get("PUBLIC_BASE_URL", ""),
    ))
    origin = alas_gateway.configured_origin()
    if origin:
        origins.add(origin)
    return frozenset(origins)


def connection_origin(connection: Request | WebSocket) -> str:
    """Build the externally visible origin for a request or WebSocket."""
    headers = connection.headers
    remote = connection.client.host if connection.client else ""
    scheme = getattr(connection.url, "scheme", "http")
    if env_bool("TRUST_PROXY", False) and is_trusted_proxy(remote):
        forwarded = headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower()
        if forwarded in {"http", "https", "ws", "wss"}:
            scheme = forwarded.replace("ws", "http", 1)
    if scheme in {"ws", "wss"}:
        scheme = scheme.replace("ws", "http", 1)
    host = headers.get("host", "")
    return normalize_origin(f"{scheme}://{host}")


def is_alas_origin_request(connection: Request | WebSocket) -> bool:
    expected = alas_gateway.configured_origin()
    return bool(expected and connection_origin(connection) == expected)


def alas_route_allowed(path: str) -> bool:
    normalized = str(path or "")
    return normalized == "/healthz" or normalized in {"/alas/gateway", "/alas/gateway/", "/alas/access-status"} or normalized.startswith(
        "/alas/embed/proxy"
    )


def alas_cookie_secure() -> bool:
    origin = alas_gateway.configured_origin()
    return origin.startswith("https://")


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


@lru_cache(maxsize=64)
def _trusted_proxy_nets_from_raw(raw_value: str) -> tuple:
    nets = []
    for raw in (value.strip() for value in raw_value.split(",")):
        if raw == "*":
            continue
        try:
            nets.append(ipaddress.ip_network(raw, strict=False))
        except ValueError:
            pass
    return tuple(nets)


def trusted_proxy_nets() -> tuple:
    return _trusted_proxy_nets_from_raw(os.environ.get("TRUSTED_PROXY_IPS", ""))


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
        # XFF 由代理追加：最左值客户端可伪造，必须从右向左跳过可信代理，取第一个不可信地址。
        forwarded_chain = [canonical_ip(item) for item in request.headers.get("x-forwarded-for", "").split(",")]
        for candidate in reversed(forwarded_chain):
            if not candidate:
                continue
            if is_trusted_proxy(candidate):
                continue
            return candidate
        # 全链均为可信代理：回退到直连地址（此时 remote 即最外层代理）。
        return canonical_ip(remote) or "unknown"
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


_REQUEST_CACHE_MISS = object()


def get_current_session(request: Request) -> dict | None:
    """Return this request's session, resolved once per request.

    Handlers used to hit SQLite for every call; the workbench snapshot alone
    resolves the same session three times.  The value cannot change within one
    request, so it is cached on ``request.state``.
    """
    state = request.state
    cached = getattr(state, "current_session_cache", _REQUEST_CACHE_MISS)
    if cached is not _REQUEST_CACHE_MISS:
        return cached
    session = storage.get_session(request.cookies.get(SESSION_COOKIE))
    state.current_session_cache = session
    return session


def session_role(session: dict | None) -> str:
    """已确认会话对应的角色。

    会话行是 ``SELECT s.*`` + 账户的几个字段，**不含 role**，所以要按用户名查一次用户表。
    只用于展示/分类（访问记录身份），绝不作为授权判断依据——授权一律走 ``require_*``。
    """
    if not session:
        return ""
    username = str(session.get("username") or "")
    if not username:
        return ""
    try:
        user = storage.get_user(username)
    except Exception:  # pragma: no cover - 查角色失败不应影响请求
        return ""
    return str(user["role"] if user else "")


def get_current_user(request: Request) -> dict | None:
    state = request.state
    cached = getattr(state, "current_user_cache", _REQUEST_CACHE_MISS)
    if cached is not _REQUEST_CACHE_MISS:
        return cached
    sess = get_current_session(request)
    user = None
    if sess:
        row = storage.get_user(sess["username"])
        user = dict(row) if row else None
    state.current_user_cache = user
    return user


# must_change_password 生效期间仅放行自服务端点：改密、会话引导与登出不受影响。
PASSWORD_CHANGE_REQUIRED_EXEMPT_PATHS = frozenset(
    {
        "/api/account/password",
        "/api/me",
    }
)


def password_change_allowed(user: dict | None, path: str | None = None) -> bool:
    """Apply the forced-password-change policy to HTTP and WebSocket paths."""
    if not user or not user.get("must_change_password"):
        return bool(user)
    return bool(path and path in PASSWORD_CHANGE_REQUIRED_EXEMPT_PATHS)


def require_user(request: Request) -> dict:
    user = get_current_user(request)
    if not user:
        queue_audit_event(request, action="authentication", reason="login_required")
        raise HTTPException(status_code=401, detail=i18n.translate("server.security.login_required"))
    if not password_change_allowed(user, request.url.path):
        queue_audit_event(
            request,
            action="authentication",
            reason="password_change_required",
            username=str(user.get("username") or ""),
        )
        raise HTTPException(status_code=403, detail=i18n.translate("server.security.password_change_required"))
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


def require_active_user(request: Request) -> dict:
    """需要「未到期且未停用」的账户（投屏、ALAS 这类付费功能）。

    到期账户仍然可以登录并浏览自己的页面（便于续期/未来付款），但投屏（含仅观看）
    与 ALAS 一律拒绝。这里给出统一且可翻译的拒绝原因，避免用户只看到
    「无权访问此设备」而不知道是账户到期。
    """
    user = require_user(request)
    if not storage.user_is_active(user):
        queue_audit_event(
            request,
            action="authentication",
            reason="account_expired",
            username=str(user.get("username") or ""),
        )
        raise HTTPException(status_code=403, detail=i18n.translate("server.error.account_expired"))
    return user


def csrf_valid(request: Request, provided: str) -> bool:
    sess = get_current_session(request)
    expected = str(sess.get("csrf_token", "")) if sess else ""
    return bool(sess and provided and expected and secrets.compare_digest(provided, expected))


def verify_csrf_token(request: Request, provided: str) -> None:
    # 没有有效会话时先给 401：到期/被撤销的会话拿到 400「安全校验失败」会让人
    # 去刷新页面，而前端只在 401 时跳登录页。
    if not get_current_session(request):
        queue_audit_event(request, action="authentication", reason="login_required")
        raise HTTPException(status_code=401, detail=i18n.translate("server.security.login_required"))
    if not csrf_valid(request, provided):
        queue_audit_event(request, action="csrf_validation", reason="token_invalid")
        raise HTTPException(status_code=400, detail=i18n.translate("server.security.csrf_failed"))


def verify_csrf(request: Request) -> None:
    verify_csrf_token(request, request.headers.get("x-csrf-token", ""))


async def websocket_user(ws: WebSocket) -> dict | None:
    sess = await asyncio.to_thread(websocket_session, ws)
    if not sess:
        return None
    user = await asyncio.to_thread(storage.get_user, sess["username"])
    user = dict(user) if user else None
    return user if password_change_allowed(user, ws.url.path) else None


def websocket_session_id(ws: WebSocket) -> str:
    return str(ws.cookies.get(SESSION_COOKIE) or "").strip()


def websocket_session(ws: WebSocket) -> dict | None:
    sid = websocket_session_id(ws)
    return storage.get_session(sid) if sid else None


def websocket_session_valid(ws: WebSocket, username: str, session_id: str) -> bool:
    expected_session_id = str(session_id or "").strip()
    if not expected_session_id:
        return False
    actual_session_id = websocket_session_id(ws)
    if not actual_session_id or not secrets.compare_digest(actual_session_id, expected_session_id):
        return False
    session = storage.get_session(expected_session_id)
    if not session or session.get("username") != username:
        return False
    user = storage.get_user(username)
    if not user:
        return False
    current_user = dict(user)
    return bool(
        password_change_allowed(current_user, getattr(ws.url, "path", None))
        and storage.user_is_active(user)
    )


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
    if not origin:
        return env_bool("ALLOW_MISSING_WEBSOCKET_ORIGIN", False)
    proto = ws.headers.get("x-forwarded-proto") or ws.url.scheme.replace("ws", "http", 1)
    if not origin_allowed(origin, f"{proto}://{host}"):
        return False
    return True


def websocket_access_decision(ws: WebSocket):
    """WebSocket 握手前的统一判定：先来源/Origin 校验，再走访问网关（BAN → GEO）。

    WS 握手**不经过 HTTP 中间件**，所以这里的顺序必须和 ``app.access_gate`` 里
    写给 HTTP 的那份完全一致；两处不一致就是最容易被绕过的口子。返回值是
    ``access_gate.GateDecision``：``allowed=False`` 时调用方必须在 ``accept()``
    之前 ``close(code=4403)``（uvicorn 会把这种拒绝变成握手阶段的 HTTP 403）。
    """
    from . import access_gate

    if not websocket_origin_allowed(ws):
        return access_gate.GateDecision(
            allowed=False,
            decision="deny_origin",
            status_code=403,
            reason="origin_denied",
        )
    remote = ws.client.host if ws.client else ""
    return access_gate.evaluate(
        source_ip=client_ip(ws),
        method="GET",
        path=ws.url.path,
        peer_ip=remote,
    )


def secure_cookie_enabled() -> bool:
    configured = os.environ.get("SESSION_COOKIE_SECURE", "").strip()
    if configured:
        return configured.lower() in ("1", "true", "yes", "on")
    public = public_base_url()
    return bool(public and public.scheme == "https")


# ---- 安全机制配置：管理后台设置覆盖环境变量默认值 ----
#
# 字段 -> (settings 键, 环境变量名, 默认值, 最小值, 最大值, 类型)。管理后台写入
# settings 表后立即生效；未写入的字段回落到环境变量/默认值。运行期读取走内存快照
# （避免在异步路径里同步读 SQLite），写入与启动时刷新。

LOGIN_GUARD_FIELDS: dict[str, tuple[str, str, object, int, int, str]] = {
    "enabled": ("login_guard_enabled", "LOGIN_RATE_LIMIT_ENABLED", True, 0, 0, "bool"),
    "captcha_enabled": ("login_guard_captcha_enabled", "LOGIN_CAPTCHA_ENABLED", True, 0, 0, "bool"),
    "captcha_after_failures": ("login_guard_captcha_after_failures", "LOGIN_CAPTCHA_AFTER_FAILURES", 1, 1, 100, "int"),
    "lockout_threshold": ("login_guard_lockout_threshold", "LOGIN_RATE_LIMIT_MAX", 3, 1, 100, "int"),
    # 下限沿用既有环境变量语义（窗口/封禁最小 10 秒）；管理后台的输入框以分钟呈现，
    # 界面侧最低 1 分钟，接口仍接受 10 秒以上的值以免改变已部署配置的含义。
    "failure_window_seconds": ("login_guard_failure_window_seconds", "LOGIN_RATE_LIMIT_WINDOW_SECONDS", 900, 10, 86400, "int"),
    "lockout_seconds": ("login_guard_lockout_seconds", "LOGIN_LOCKOUT_SECONDS", 300, 10, 86400, "int"),
    "captcha_ttl_seconds": ("login_guard_captcha_ttl_seconds", "LOGIN_CAPTCHA_TTL_SECONDS", 120, 10, 3600, "int"),
    "captcha_issue_interval_seconds": ("login_guard_captcha_issue_interval_seconds", "LOGIN_CAPTCHA_ISSUE_INTERVAL_SECONDS", 2, 0, 60, "int"),
    # 工作量证明难度阶梯：首题 base bits，之后每多失败一次 +step，封顶 max。默认
    # 14/2/18 与历史硬编码阶梯完全一致（0-1 次失败 14、2 次 16、3 次及以上 18）。
    # bits 是期望 2^bits 次 SHA-256，直接决定攻击者单次尝试的成本；上限 28 已经
    # 超出寻常浏览器可等待的范围，这里留出 26/28 供极端场景，但界面上会给出耗时预警。
    "captcha_bits_base": ("login_guard_captcha_bits_base", "LOGIN_CAPTCHA_BITS_BASE", 14, 8, 26, "int"),
    "captcha_bits_step": ("login_guard_captcha_bits_step", "LOGIN_CAPTCHA_BITS_STEP", 2, 0, 8, "int"),
    "captcha_bits_max": ("login_guard_captcha_bits_max", "LOGIN_CAPTCHA_BITS_MAX", 18, 8, 28, "int"),
}
LOGIN_GUARD_SETTING_KEYS = tuple(field[0] for field in LOGIN_GUARD_FIELDS.values())

_LOGIN_GUARD_CONFIG_CACHE: dict[str, object] | None = None


def _settings_bool(raw: str, default: bool) -> bool:
    value = str(raw or "").strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    return default


def _settings_int(raw: str, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(raw).strip())
    except (TypeError, ValueError, OverflowError):
        return default
    return max(minimum, min(maximum, parsed))


def _load_login_guard_config() -> dict[str, object]:
    try:
        stored = storage.get_settings(list(LOGIN_GUARD_SETTING_KEYS))
    except Exception:  # pragma: no cover - storage unavailable during early import
        stored = {}
        log.warning("LOGIN_GUARD_SETTINGS_READ_FAILED", exc_info=True)
    config: dict[str, object] = {}
    for name, (setting_key, env_name, default, minimum, maximum, kind) in LOGIN_GUARD_FIELDS.items():
        raw = str(stored.get(setting_key) or "").strip()
        if kind == "bool":
            if raw:
                config[name] = _settings_bool(raw, bool(default))
            else:
                config[name] = env_bool(env_name, bool(default))
        else:
            if raw:
                config[name] = _settings_int(raw, int(default), int(minimum), int(maximum))
            else:
                config[name] = env_int(env_name, int(default), int(minimum), int(maximum))
    if not config["enabled"]:
        config["captcha_enabled"] = False
    return config


def login_guard_config() -> dict[str, object]:
    global _LOGIN_GUARD_CONFIG_CACHE
    if _LOGIN_GUARD_CONFIG_CACHE is None:
        _LOGIN_GUARD_CONFIG_CACHE = _load_login_guard_config()
    return dict(_LOGIN_GUARD_CONFIG_CACHE)


def refresh_login_guard_config() -> dict[str, object]:
    global _LOGIN_GUARD_CONFIG_CACHE
    _LOGIN_GUARD_CONFIG_CACHE = _load_login_guard_config()
    return dict(_LOGIN_GUARD_CONFIG_CACHE)


def login_guard_defaults() -> dict[str, object]:
    """Environment/default values, used by the admin panel's reset action."""
    defaults: dict[str, object] = {}
    for name, (_setting_key, env_name, default, minimum, maximum, kind) in LOGIN_GUARD_FIELDS.items():
        if kind == "bool":
            defaults[name] = env_bool(env_name, bool(default))
        else:
            defaults[name] = env_int(env_name, int(default), int(minimum), int(maximum))
    if not defaults["enabled"]:
        defaults["captcha_enabled"] = False
    return defaults


def normalize_login_guard_config(payload: object, base: dict[str, object] | None = None) -> dict[str, object]:
    """Validate an admin-submitted security configuration (partial updates allowed)."""
    if not isinstance(payload, dict):
        raise ValueError("invalid_login_guard_config")
    unknown = {str(key) for key in payload} - set(LOGIN_GUARD_FIELDS)
    if unknown:
        raise ValueError("invalid_login_guard_field")
    config = dict(base or login_guard_config())
    for name, raw in payload.items():
        _setting_key, _env_name, _default, minimum, maximum, kind = LOGIN_GUARD_FIELDS[name]
        if raw is None:
            continue
        if kind == "bool":
            try:
                config[name] = parse_bool_strict(raw)
            except InvalidBooleanValue as exc:
                raise ValueError("invalid_login_guard_boolean") from exc
        else:
            try:
                if isinstance(raw, bool):
                    raise ValueError
                parsed = int(str(raw).strip())
            except (TypeError, ValueError):
                raise ValueError("invalid_login_guard_value") from None
            if not minimum <= parsed <= maximum:
                raise ValueError("invalid_login_guard_value")
            config[name] = parsed
    # 难度阶梯自洽性：首题难度高于上限是自相矛盾的配置（阶梯无处可升）。
    # 递增步长不设限：超过「上限 - 首题」时阶梯一步到顶，这是合法配置。
    if int(config["captcha_bits_base"]) > int(config["captcha_bits_max"]):
        raise ValueError("invalid_login_guard_difficulty")
    if not config["enabled"]:
        config["captcha_enabled"] = False
    return config


def save_login_guard_config(payload: object) -> dict[str, object]:
    config = normalize_login_guard_config(payload)
    storage.set_settings({LOGIN_GUARD_FIELDS[name][0]: _config_storage_value(value) for name, value in config.items()})
    return refresh_login_guard_config()


def _config_storage_value(value: object) -> str:
    return "true" if value is True else "false" if value is False else str(value)


def _login_rate_limit_values() -> tuple[int, int, int]:
    config = login_guard_config()
    return (
        int(config["lockout_threshold"]),
        int(config["failure_window_seconds"]),
        int(config["lockout_seconds"]),
    )


def login_captcha_enabled() -> bool:
    config = login_guard_config()
    return bool(config["enabled"]) and bool(config["captcha_enabled"])


def login_captcha_after() -> int:
    return int(login_guard_config()["captcha_after_failures"])


def _login_keys(request: Request, username: str) -> list[str]:
    ip = client_ip(request) or "unknown"
    normalized_user = (username or "").strip().lower()[:64] or "anonymous"
    # ip:<ip> 承载「按来源 IP 封禁」；user:<user> 是跨 IP 的账号维度计数，
    # 只用于升级验证码要求，从不硬锁账号（避免攻击者故意锁死管理员）。
    return [f"ip:{ip}", f"user:{normalized_user}"]


def _prune_login_failures(key: str, now: float, window_seconds: int) -> list[tuple[str, float]]:
    with _LOGIN_RATE_LIMIT_LOCK:
        attempts = [entry for entry in _LOGIN_FAILURES.get(key, []) if now - entry[1] <= window_seconds]
        if attempts:
            _LOGIN_FAILURES[key] = attempts
        else:
            _LOGIN_FAILURES.pop(key, None)
            if key not in _LOGIN_LOCKOUTS:
                _LOGIN_STATE_RECENCY.pop(key, None)
        return attempts


def _touch_login_state(key: str) -> None:
    _LOGIN_STATE_RECENCY.pop(key, None)
    _LOGIN_STATE_RECENCY[key] = None


def _prune_login_state(now: float, window_seconds: int) -> None:
    for key in tuple(_LOGIN_FAILURES):
        _prune_login_failures(key, now, window_seconds)
    for key, lockout_until in tuple(_LOGIN_LOCKOUTS.items()):
        if lockout_until <= now:
            _LOGIN_LOCKOUTS.pop(key, None)
            _LOGIN_LOCKOUT_USERS.pop(key, None)
            if key not in _LOGIN_FAILURES:
                _LOGIN_STATE_RECENCY.pop(key, None)


def _bound_login_state() -> None:
    while len(_LOGIN_STATE_RECENCY) > _MAX_LOGIN_RATE_LIMIT_KEYS:
        oldest_key = next(iter(_LOGIN_STATE_RECENCY))
        _LOGIN_STATE_RECENCY.pop(oldest_key, None)
        _LOGIN_FAILURES.pop(oldest_key, None)
        _LOGIN_LOCKOUTS.pop(oldest_key, None)
        _LOGIN_LOCKOUT_USERS.pop(oldest_key, None)


def _maintain_login_state(now: float, window_seconds: int) -> None:
    global _LOGIN_STATE_OPERATIONS
    _LOGIN_STATE_OPERATIONS += 1
    # Expiry scans are periodic; an exactly full cache is already bounded.
    if (
        _LOGIN_STATE_OPERATIONS % _LOGIN_STATE_PRUNE_INTERVAL == 0
        or len(_LOGIN_STATE_RECENCY) > _MAX_LOGIN_RATE_LIMIT_KEYS
    ):
        _prune_login_state(now, window_seconds)
    _bound_login_state()


def login_rate_limit_status(request: Request, username: str) -> dict:
    if not login_guard_config()["enabled"]:
        return {"limited": False, "retry_after": 0, "failures": 0, "captcha_required": False}
    _max_attempts, window_seconds, _lockout_seconds = _login_rate_limit_values()
    now = time.time()
    retry_after = 0
    ip_failures = 0
    user_failures = 0
    with _LOGIN_RATE_LIMIT_LOCK:
        _maintain_login_state(now, window_seconds)
        for key in _login_keys(request, username):
            lockout_until = _LOGIN_LOCKOUTS.get(key, 0)
            if lockout_until <= now:
                _LOGIN_LOCKOUTS.pop(key, None)
                attempts = _prune_login_failures(key, now, window_seconds)
                if attempts:
                    _touch_login_state(key)
                if key.startswith("ip:"):
                    ip_failures = len(attempts)
                else:
                    user_failures = len(attempts)
                continue
            _touch_login_state(key)
            if key.startswith("ip:"):
                retry_after = max(retry_after, int(lockout_until - now) + 1)
    captcha_required = login_captcha_enabled() and (
        ip_failures >= login_captcha_after() or user_failures >= login_captcha_after()
    )
    return {
        "limited": retry_after > 0,
        "retry_after": retry_after,
        "failures": ip_failures,
        "captcha_required": captcha_required,
    }


def _charge_entry(key: str, username: str, now: float) -> tuple[str, float]:
    return (_normalized_user(username), now)


def _normalized_user(username: str) -> str:
    return (username or "").strip().lower()[:64] or "anonymous"


def record_login_failure(request: Request, username: str) -> dict:
    max_attempts, window_seconds, lockout_seconds = _login_rate_limit_values()
    if not login_guard_config()["enabled"]:
        return {"limited": False, "retry_after": 0, "failures": 0, "max_attempts": max_attempts}
    now = time.time()
    limited = False
    retry_after = 0
    failures = 0
    with _LOGIN_RATE_LIMIT_LOCK:
        _maintain_login_state(now, window_seconds)
        for key in _login_keys(request, username):
            attempts = _prune_login_failures(key, now, window_seconds)
            attempts.append(_charge_entry(key, username, now))
            _LOGIN_FAILURES[key] = attempts
            if key.startswith("ip:") and len(attempts) >= max_attempts:
                _LOGIN_LOCKOUT_USERS[key] = {entry[0] for entry in attempts}
                _LOGIN_LOCKOUTS[key] = now + lockout_seconds
                _LOGIN_FAILURES.pop(key, None)
                limited = True
                retry_after = max(retry_after, lockout_seconds)
                failures = max_attempts
            elif key.startswith("ip:"):
                failures = len(attempts)
            _touch_login_state(key)
            _bound_login_state()
    _schedule_login_guard_persist()
    return {"limited": limited, "retry_after": retry_after, "failures": failures, "max_attempts": max_attempts}


def record_login_success(request: Request, username: str) -> None:
    """Clear the account's own failure entries.

    The shared ``ip:`` counter keeps the entries of *other* accounts, so a
    successful login can no longer reset the per-IP spray counter (ISSUE-065).
    A lockout tripped only by this account's own failures is released, because
    the user just proved they know the password.
    """
    if not login_guard_config()["enabled"]:
        return
    ip = client_ip(request) or "unknown"
    normalized_user = _normalized_user(username)
    with _LOGIN_RATE_LIMIT_LOCK:
        _LOGIN_FAILURES.pop(f"user:{normalized_user}", None)
        _LOGIN_LOCKOUTS.pop(f"user:{normalized_user}", None)
        _LOGIN_STATE_RECENCY.pop(f"user:{normalized_user}", None)
        ip_key = f"ip:{ip}"
        attempts = _LOGIN_FAILURES.get(ip_key, [])
        remaining = [entry for entry in attempts if entry[0] != normalized_user]
        if remaining:
            _LOGIN_FAILURES[ip_key] = remaining
        else:
            _LOGIN_FAILURES.pop(ip_key, None)
        if ip_key in _LOGIN_LOCKOUTS and _LOGIN_LOCKOUT_USERS.get(ip_key, set()) <= {normalized_user}:
            _LOGIN_LOCKOUTS.pop(ip_key, None)
            _LOGIN_LOCKOUT_USERS.pop(ip_key, None)
            if ip_key not in _LOGIN_FAILURES:
                _LOGIN_STATE_RECENCY.pop(ip_key, None)
    _schedule_login_guard_persist()


def reserve_login_attempt(request: Request, username: str) -> dict:
    """Atomically check the limit and pre-charge one attempt for in-thread auth.

    PBKDF2 校验将移出事件循环并发执行，失败不能再等认证返回后补记：
    预占额度保证并发窗口内尝试次数仍受 max_attempts 约束（无 check-then-act 间隙）。
    成功路径必须调用 refund_login_attempt 退还本次预占。
    """
    if not login_guard_config()["enabled"]:
        return {"allowed": True, "limited": False, "retry_after": 0, "charge_ts": None, "charged": ()}
    max_attempts, window_seconds, lockout_seconds = _login_rate_limit_values()
    now = time.time()
    retry_after = 0
    with _LOGIN_RATE_LIMIT_LOCK:
        _maintain_login_state(now, window_seconds)
        keys = _login_keys(request, username)
        for key in keys:
            lockout_until = _LOGIN_LOCKOUTS.get(key, 0)
            if lockout_until > now:
                _touch_login_state(key)
                # 只有 ip: 键承担封禁；账号键只驱动验证码，不拒绝尝试。
                if key.startswith("ip:"):
                    retry_after = max(retry_after, int(lockout_until - now) + 1)
        if retry_after > 0:
            return {"allowed": False, "limited": True, "retry_after": retry_after, "charge_ts": None, "charged": ()}
        charged: list[tuple[str, float, tuple[str, ...]]] = []
        for key in keys:
            attempts = _prune_login_failures(key, now, window_seconds)
            attempts.append(_charge_entry(key, username, now))
            _LOGIN_FAILURES[key] = attempts
            _touch_login_state(key)
            lockout_until = 0.0
            contributors: tuple[str, ...] = ()
            if key.startswith("ip:") and len(attempts) >= max_attempts:
                lockout_until = now + lockout_seconds
                contributors = tuple(sorted({entry[0] for entry in attempts}))
                _LOGIN_LOCKOUT_USERS[key] = set(contributors)
                _LOGIN_LOCKOUTS[key] = lockout_until
                _LOGIN_FAILURES.pop(key, None)
            charged.append((key, lockout_until, contributors))
            _bound_login_state()
    return {"allowed": True, "limited": False, "retry_after": 0, "charge_ts": now, "charged": tuple(charged)}


def refund_login_attempt(request: Request, username: str, reservation: dict) -> None:
    """Undo a successful attempt's pre-charge.

    A lockout tripped by this very charge is released only when every failure
    that contributed to it came from the successful account itself.  Other
    accounts' failures keep the shared per-IP lockout in place, so a valid
    account can no longer reset the spray counter for the whole IP (ISSUE-065).
    """
    charge_ts = reservation.get("charge_ts") if isinstance(reservation, dict) else None
    charged = reservation.get("charged") if isinstance(reservation, dict) else None
    if charge_ts is None or not charged:
        return
    normalized_user = _normalized_user(username)
    with _LOGIN_RATE_LIMIT_LOCK:
        for key, lockout_until, contributors in charged:
            if lockout_until:
                if _LOGIN_LOCKOUTS.get(key) == lockout_until and set(contributors) <= {normalized_user}:
                    _LOGIN_LOCKOUTS.pop(key, None)
                    _LOGIN_LOCKOUT_USERS.pop(key, None)
                continue
            attempts = _LOGIN_FAILURES.get(key)
            if not attempts:
                continue
            try:
                attempts.remove((normalized_user, charge_ts))
            except ValueError:
                continue
            if attempts:
                _LOGIN_FAILURES[key] = attempts
            else:
                _LOGIN_FAILURES.pop(key, None)
                if key not in _LOGIN_LOCKOUTS:
                    _LOGIN_STATE_RECENCY.pop(key, None)
    _schedule_login_guard_persist()


def clear_login_rate_limits() -> None:
    global _LOGIN_STATE_OPERATIONS
    with _LOGIN_RATE_LIMIT_LOCK:
        _LOGIN_FAILURES.clear()
        _LOGIN_LOCKOUTS.clear()
        _LOGIN_LOCKOUT_USERS.clear()
        _LOGIN_STATE_RECENCY.clear()
        _LOGIN_STATE_OPERATIONS = 0
    _schedule_login_guard_persist()


# ---- 登录保护状态持久化与后台快照 ----
#
# 生产入口通过 enable_login_guard_persistence() 启用：失败计数与 IP 封禁会
# 镜像到 SQLite（login_guard_state 表），重启/发版不清零。写入走后台线程
# 队列合并，不阻塞事件循环；单元测试不启用，内存行为保持不变。

_LOGIN_GUARD_PERSISTENCE = False
_LOGIN_GUARD_PERSIST_THREAD: threading.Thread | None = None
_LOGIN_GUARD_PERSIST_QUEUE: "queue.Queue[None] | None" = None


def _persist_worker() -> None:
    while True:
        try:
            _LOGIN_GUARD_PERSIST_QUEUE.get()
        except Exception:  # pragma: no cover - queue teardown
            return
        # 合并同一批内多次变更，只写最终状态。
        while not _LOGIN_GUARD_PERSIST_QUEUE.empty():
            try:
                _LOGIN_GUARD_PERSIST_QUEUE.get_nowait()
            except Exception:
                break
        try:
            with _LOGIN_RATE_LIMIT_LOCK:
                failures = {key: list(entries) for key, entries in _LOGIN_FAILURES.items()}
                lockouts = dict(_LOGIN_LOCKOUTS)
            storage.save_login_guard_state(failures, lockouts)
        except Exception:
            log.warning("LOGIN_GUARD_PERSIST_FAILED", exc_info=True)


def _schedule_login_guard_persist() -> None:
    global _LOGIN_GUARD_PERSIST_THREAD
    if not _LOGIN_GUARD_PERSISTENCE or _LOGIN_GUARD_PERSIST_QUEUE is None:
        return
    if _LOGIN_GUARD_PERSIST_QUEUE.empty():
        _LOGIN_GUARD_PERSIST_QUEUE.put(None)


def enable_login_guard_persistence() -> None:
    """Hydrate login guard state from SQLite and mirror future changes back."""
    global _LOGIN_GUARD_PERSISTENCE, _LOGIN_GUARD_PERSIST_THREAD
    if _LOGIN_GUARD_PERSISTENCE:
        return
    _LOGIN_GUARD_PERSISTENCE = True
    _LOGIN_GUARD_PERSIST_QUEUE = queue.Queue()
    try:
        loaded = storage.load_login_guard_state()
    except Exception:
        loaded = None
    if loaded:
        failures, lockouts = loaded
        now = time.time()
        _max_attempts, window_seconds, _lockout_seconds = _login_rate_limit_values()
        with _LOGIN_RATE_LIMIT_LOCK:
            for key, entries in failures.items():
                if key.startswith("ip:") or key.startswith("user:"):
                    _LOGIN_FAILURES.setdefault(key, []).extend(
                        (entry[0], entry[1]) for entry in entries if now - entry[1] <= window_seconds
                    )
                    if _LOGIN_FAILURES.get(key):
                        _touch_login_state(key)
            for key, lockout_until in lockouts.items():
                if key.startswith("ip:") and lockout_until > now:
                    _LOGIN_LOCKOUTS[key] = lockout_until
                    _touch_login_state(key)
    if _LOGIN_GUARD_PERSIST_THREAD is None:
        _LOGIN_GUARD_PERSIST_THREAD = threading.Thread(
            target=_persist_worker, name="login-guard-persist", daemon=True
        )
        _LOGIN_GUARD_PERSIST_THREAD.start()
    _schedule_login_guard_persist()


def clear_login_guard_key(key: str) -> bool:
    """Remove one failure/lockout key (admin unlock). Only guard-owned keys."""
    if not key.startswith(("ip:", "user:")):
        return False
    with _LOGIN_RATE_LIMIT_LOCK:
        _LOGIN_FAILURES.pop(key, None)
        _LOGIN_LOCKOUTS.pop(key, None)
        _LOGIN_LOCKOUT_USERS.pop(key, None)
        _LOGIN_STATE_RECENCY.pop(key, None)
    _schedule_login_guard_persist()
    return True


def login_failure_from_reservation(request: Request, username: str, reservation: dict) -> dict:
    """Confirm that a pre-charged attempt failed.

    The reservation charge is the failure record: nothing is added here, we only
    report whether this very attempt tripped the per-IP lockout, so the route
    can answer 429 with the correct Retry-After instead of a plain 401.
    """
    if not (isinstance(reservation, dict) and reservation.get("charged")):
        return record_login_failure(request, username)
    max_attempts, window_seconds, _lockout_seconds = _login_rate_limit_values()
    now = time.time()
    ip_key = f"ip:{client_ip(request) or 'unknown'}"
    retry_after = 0
    with _LOGIN_RATE_LIMIT_LOCK:
        _maintain_login_state(now, window_seconds)
        for key, lockout_until, _contributors in reservation["charged"]:
            if key == ip_key and lockout_until and _LOGIN_LOCKOUTS.get(ip_key) == lockout_until:
                retry_after = int(lockout_until - now) + 1
        failures = len(_LOGIN_FAILURES.get(ip_key, []))
    _schedule_login_guard_persist()
    return {
        "limited": retry_after > 0,
        "retry_after": retry_after,
        "failures": max(failures, max_attempts if retry_after else 0),
        "max_attempts": max_attempts,
    }


def login_guard_snapshot() -> dict:
    """Admin-facing view of the login guard: active IP lockouts and counters."""
    now = time.time()
    _max_attempts, window_seconds, lockout_seconds = _login_rate_limit_values()
    with _LOGIN_RATE_LIMIT_LOCK:
        _maintain_login_state(now, window_seconds)
        locked = []
        for key, lockout_until in sorted(_LOGIN_LOCKOUTS.items()):
            if key.startswith("ip:") and lockout_until > now:
                locked.append({
                    "key": key,
                    "ip": key[len("ip:"):],
                    "retry_after": int(lockout_until - now) + 1,
                    "locked_until": lockout_until,
                })
        active = []
        for key, entries in sorted(_LOGIN_FAILURES.items()):
            if key.startswith("ip:") and entries:
                active.append({"key": key, "ip": key[len("ip:"):], "failures": len(entries)})
    from .pow_provider import get_provider

    return {
        "pow_provider": get_provider().name,
        "locked": locked,
        "active": active,
        "config": login_guard_config(),
        "defaults": login_guard_defaults(),
        "overridden": _login_guard_overridden_fields(),
        "persisted": _LOGIN_GUARD_PERSISTENCE,
    }


def _login_guard_overridden_fields() -> list[str]:
    """Fields whose stored value differs from the env/default value."""
    stored = {}
    try:
        stored = storage.get_settings(list(LOGIN_GUARD_SETTING_KEYS))
    except Exception:  # pragma: no cover - storage unavailable
        stored = {}
    defaults = login_guard_defaults()
    effective = login_guard_config()
    return [
        name
        for name in LOGIN_GUARD_FIELDS
        if str(stored.get(LOGIN_GUARD_FIELDS[name][0]) or "").strip() and effective[name] != defaults[name]
    ]
