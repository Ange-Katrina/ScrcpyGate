import ipaddress
import logging
import os
import secrets
from functools import wraps
from urllib.parse import urlparse
from fastapi import HTTPException, Request, WebSocket
from fastapi.responses import RedirectResponse

from . import storage

SESSION_COOKIE = "wsid"
PROXY_HEADERS = ("x-forwarded-for", "x-forwarded-proto", "x-forwarded-host", "x-real-ip")
log = logging.getLogger("webscrcpy.security")


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def env_list(name: str) -> list[str]:
    return [item.strip() for item in os.environ.get(name, "").split(",") if item.strip()]


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
    hosts = {"localhost", "127.0.0.1", "::1", "web-scrcpy", "web-scrcpy-v2"}
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


def client_ip(request: Request) -> str:
    remote = request.client.host if request.client else ""
    if env_bool("TRUST_PROXY", False) and is_trusted_proxy(remote):
        forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
        if forwarded:
            return forwarded
    return remote


def enforce_http_boundary(request: Request) -> None:
    if request.url.path == "/healthz":
        return
    remote = request.client.host if request.client else ""
    if not proxy_headers_allowed(request.headers, remote, request.url.path):
        raise HTTPException(status_code=403, detail="Forbidden")
    if not request_host_allowed(request):
        log.warning("HOST_REJECT path=%s host=%s allowed=%s", request.url.path, request.headers.get("host", ""), sorted(allowed_hosts()))
        raise HTTPException(status_code=400, detail="Bad Request")
    origin = request.headers.get("origin")
    if origin and not origin_allowed(origin, str(request.base_url).rstrip("/")):
        log.warning("ORIGIN_REJECT path=%s origin=%s base=%s allowed=%s", request.url.path, origin, str(request.base_url).rstrip("/"), sorted(allowed_origins()))
        raise HTTPException(status_code=403, detail="Forbidden")


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
        raise HTTPException(status_code=401, detail="login required")
    return user


def require_admin(request: Request) -> dict:
    user = require_user(request)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="admin required")
    return user


def verify_csrf(request: Request) -> None:
    sess = get_current_session(request)
    provided = request.headers.get("x-csrf-token", "")
    expected = str(sess.get("csrf_token", "")) if sess else ""
    if not sess or not provided or not expected or not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=400, detail="CSRF failed")


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
