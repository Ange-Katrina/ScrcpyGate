#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import html as html_utils
import ipaddress
from dataclasses import dataclass
from html import escape
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urljoin, urlparse, urlunparse
from urllib.request import Request, build_opener, ProxyHandler

from fastapi import HTTPException, Request as FastAPIRequest
from fastapi.responses import Response

ALAS_EMBED_PREFIX = "/alas/embed"
ALAS_DEFAULT_PORT = 22267
DOMAIN_FALLBACK_PORTS = (80, 443, 22267)
MANAGEMENT_MARKERS = ("管理", "Manage", "Settings.Admin", "alas.config_list")
CONFIG_QUERY_KEYS = ("config", "name", "config_name")
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
OMITTED_RESPONSE_HEADERS = HOP_BY_HOP_HEADERS | {"content-length"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
HTML_CONTENT_TYPES = {"text/html", "application/xhtml+xml"}


@dataclass(frozen=True)
class ProxyDecision:
    """表示 ALAS 嵌入代理访问判定结果。"""

    allowed: bool
    status_code: int = 200
    config_name: str = ""
    filtered: bool = False
    reason: str = ""


def embed_shell_html(title: str, iframe_src: str, message: str = "") -> str:
    """生成 ScrcpyGate ALAS iframe 外壳页面。"""
    safe_title = escape(title)
    safe_src = escape(iframe_src, quote=True)
    safe_message = escape(message)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title}</title>
  <style>
    html, body {{ margin:0; height:100%; background:#0c0f14; color:#f3f6fb; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
    .bar {{ min-height:44px; display:flex; align-items:center; justify-content:space-between; gap:12px; padding:0 14px; background:#111722; border-bottom:1px solid #394254; }}
    .bar a {{ color:#86b4ff; text-decoration:none; }}
    iframe {{ width:100%; height:calc(100vh - 45px); border:0; display:block; background:#202635; }}
    .msg {{ color:#9aa6ba; font-size:13px; }}
  </style>
</head>
<body>
  <div class="bar"><strong>{safe_title}</strong><span class="msg">{safe_message}</span><a href="/">返回 ScrcpyGate</a></div>
  <iframe src="{safe_src}" title="{safe_title}"></iframe>
</body>
</html>"""


def _query_values(query, key):
    """读取查询参数所有非空字符串值，兼容列表值与单值。"""
    value = query.get(key)
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    stripped_value = str(value).strip()
    if not stripped_value:
        return []
    return [stripped_value]


def proxy_decision(user: dict, binding: dict | None, path: str, query: dict) -> ProxyDecision:
    """根据用户角色、绑定配置、路径与查询参数判定代理访问策略。"""
    role = str((user or {}).get("role", ""))
    if role == "admin":
        return ProxyDecision(allowed=True)

    if not binding or not binding.get("config_name"):
        return ProxyDecision(allowed=False, status_code=403, reason="missing binding")

    config_name = str(binding.get("config_name")).strip()
    if not config_name:
        return ProxyDecision(allowed=False, status_code=403, reason="missing binding")

    lowered_path = str(path or "").lower()
    if "admin" in lowered_path or "manage" in lowered_path:
        return ProxyDecision(
            allowed=False,
            status_code=403,
            config_name=config_name,
            reason="management path denied",
        )

    for key in CONFIG_QUERY_KEYS:
        for requested_config in _query_values(query or {}, key):
            if requested_config != config_name:
                return ProxyDecision(
                    allowed=False,
                    status_code=403,
                    config_name=config_name,
                    reason="config mismatch",
                )

    return ProxyDecision(allowed=True, config_name=config_name, filtered=True)


def filter_user_html(html: str, config_name: str) -> str:
    """对普通用户 HTML 做最小外观过滤，隐藏管理与其他配置入口。"""
    filtered = str(html or "")
    for marker in MANAGEMENT_MARKERS:
        filtered = filtered.replace(marker, "")
    for config_marker in ("其它配置", "其他配置"):
        filtered = filtered.replace(config_marker, "")
    if config_name and config_name not in filtered:
        escaped_config_name = html_utils.escape(config_name, quote=True)
        filtered = f"{filtered}<!-- bound ALAS config: {escaped_config_name} -->"
    return filtered


def _parse_runtime_url(raw_url: str):
    """解析运行时地址，并补齐无协议输入的默认协议。"""
    value = str(raw_url or "").strip()
    if not value:
        raise ValueError("invalid ALAS runtime URL")
    has_explicit_scheme = "://" in value
    if not has_explicit_scheme:
        value = f"http://{value}"
    return urlparse(value), has_explicit_scheme


def _is_ip_address(hostname: str) -> bool:
    """判断主机名是否为 IP 地址。"""
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return True


def _format_host(hostname: str) -> str:
    """格式化 URL 主机部分，确保 IPv6 地址带方括号。"""
    if ":" in hostname and not hostname.startswith("["):
        return f"[{hostname}]"
    return hostname


def runtime_url_candidates(raw_url: str) -> list[str]:
    """根据用户输入生成 ALAS 运行时访问地址候选列表。"""
    parsed, has_explicit_scheme = _parse_runtime_url(raw_url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("invalid ALAS runtime URL scheme")
    if not parsed.hostname:
        raise ValueError("invalid ALAS runtime URL host")
    if parsed.username or parsed.password:
        raise ValueError("ALAS runtime URL must not include credentials")
    if parsed.path not in ("", "/") or parsed.params or parsed.query or parsed.fragment:
        raise ValueError("ALAS runtime URL must not include path, params, query or fragment")

    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid ALAS runtime URL port") from exc

    host = _format_host(parsed.hostname)
    if port is not None:
        return [f"{parsed.scheme}://{host}:{port}"]

    if _is_ip_address(parsed.hostname) or "." not in parsed.hostname:
        return [f"{parsed.scheme}://{host}:{ALAS_DEFAULT_PORT}"]

    if has_explicit_scheme:
        default_port = 443 if parsed.scheme == "https" else 80
        return [
            f"{parsed.scheme}://{host}:{default_port}",
            f"{parsed.scheme}://{host}:{ALAS_DEFAULT_PORT}",
        ]

    return [
        f"http://{host}:{DOMAIN_FALLBACK_PORTS[0]}",
        f"https://{host}:{DOMAIN_FALLBACK_PORTS[1]}",
        f"http://{host}:{DOMAIN_FALLBACK_PORTS[2]}",
    ]


def probe_runtime_url(url: str, timeout: float = 2.0) -> bool:
    """探测 ALAS Runtime 根路径是否可连通。"""
    opener = build_opener(ProxyHandler({}))
    req = Request(url, method="GET")
    try:
        with opener.open(req, timeout=timeout) as resp:
            return 200 <= resp.getcode() < 500
    except HTTPError as exc:
        return 400 <= exc.code < 500
    except (URLError, TimeoutError, OSError):
        return False


def resolve_base_url(raw_url: str, probe=probe_runtime_url) -> str:
    """解析并返回第一个可连通的 ALAS Runtime 基础地址。"""
    candidates = runtime_url_candidates(raw_url)
    for candidate in candidates:
        if probe(candidate, timeout=2.0):
            return candidate
    raise ValueError(f"ALAS Runtime unreachable: {', '.join(candidates)}")


def build_upstream_url(base_url: str, path: str, query_items) -> str:
    """按基础地址、代理路径和查询参数安全构造上游请求 URL。"""
    parsed_base = urlparse(str(base_url or "").rstrip("/") + "/")
    if parsed_base.scheme not in ("http", "https") or not parsed_base.netloc:
        raise ValueError("invalid ALAS upstream URL")
    raw_path = str(path or "").replace("\\", "/")
    parsed_path = urlparse(raw_path)
    if parsed_path.scheme or parsed_path.netloc or "\x00" in raw_path:
        raise ValueError("invalid ALAS proxy path")
    safe_parts = []
    for part in raw_path.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise ValueError("invalid ALAS proxy path")
        safe_parts.append(quote(part, safe="!$&'()*+,;=:@"))
    base_path = parsed_base.path.rstrip("/")
    upstream_path = "/".join(part for part in (base_path.lstrip("/"), "/".join(safe_parts)) if part)
    url = urlunparse((parsed_base.scheme, parsed_base.netloc, f"/{upstream_path}" if upstream_path else "/", "", "", ""))
    params = []
    if hasattr(query_items, "multi_items"):
        params = list(query_items.multi_items())
    elif isinstance(query_items, dict):
        for key, value in query_items.items():
            if isinstance(value, (list, tuple)):
                params.extend((key, item) for item in value)
            else:
                params.append((key, value))
    else:
        params = list(query_items or [])
    if params:
        url = f"{url}?{urlencode(params, doseq=True)}"
    return url


def _connection_header_names(headers) -> set[str]:
    """解析 Connection 头中声明的扩展逐跳头名称。"""
    names = set()
    connection_value = ""
    for key, value in headers.items():
        if key.lower() == "connection":
            connection_value = str(value or "")
            break
    for item in connection_value.split(","):
        name = item.strip().lower()
        if name:
            names.add(name)
    return names


def _proxy_request_headers(headers) -> dict:
    """过滤客户端请求头，移除逐跳头、Host、Content-Length 与压缩协商。"""
    result = {}
    omitted = HOP_BY_HOP_HEADERS | _connection_header_names(headers)
    omitted.update({"host", "content-length", "accept-encoding"})
    for key, value in headers.items():
        if key.lower() not in omitted:
            result[key] = value
    return result


def _proxy_response_headers(headers, request_url: str) -> dict:
    """过滤上游响应头，并将 Location 改写为嵌入代理路径。"""
    result = {}
    omitted = OMITTED_RESPONSE_HEADERS | _connection_header_names(headers)
    for key, value in headers.items():
        lowered = key.lower()
        if lowered in omitted:
            continue
        if lowered == "location":
            value = rewrite_location_header(str(value), request_url)
        result[key] = value
    return result


def rewrite_location_header(location: str, base_url: str) -> str:
    """将同源且位于上游基础路径下的 Location 改写到嵌入代理路径。"""
    request_url = str(base_url or "")
    resolved = urljoin(request_url, str(location or ""))
    parsed = urlparse(resolved)
    base = urlparse(request_url)
    if parsed.scheme != base.scheme or parsed.netloc != base.netloc:
        return f"{ALAS_EMBED_PREFIX}/proxy/"

    proxy_base_path = base.path if base.path.endswith("/") else base.path.rsplit("/", 1)[0] + "/"
    path = parsed.path.lstrip("/")
    for index in range(len(proxy_base_path.strip("/").split("/")), -1, -1):
        candidate = "/".join(proxy_base_path.strip("/").split("/")[:index])
        prefix = f"/{candidate}/" if candidate else "/"
        if parsed.path.startswith(prefix):
            path = parsed.path[len(prefix):].lstrip("/")
            break
    rewritten = f"{ALAS_EMBED_PREFIX}/proxy/"
    if path:
        rewritten = f"{rewritten}{path}"
    if parsed.query:
        rewritten = f"{rewritten}?{parsed.query}"
    if parsed.fragment:
        rewritten = f"{rewritten}#{parsed.fragment}"
    return rewritten


def _content_type_media_type(content_type: str) -> str:
    """提取 Content-Type 媒体类型并统一为小写。"""
    return str(content_type or "").split(";", 1)[0].strip().lower()


def _content_type_charset(content_type: str) -> str:
    """从 Content-Type 提取 charset，缺省时使用 UTF-8。"""
    for item in str(content_type or "").split(";")[1:]:
        key, separator, value = item.strip().partition("=")
        if separator and key.strip().lower() == "charset" and value.strip():
            return value.strip().strip('"')
    return "utf-8"


async def proxy_http_request(request: FastAPIRequest, base_url: str, path: str, decision: ProxyDecision) -> Response:
    """转发 HTTP 请求到 ALAS Runtime，并按权限策略过滤 HTML 响应。"""
    if request.headers.get("upgrade") or "upgrade" in request.headers.get("connection", "").lower():
        raise HTTPException(status_code=501, detail="ALAS websocket proxy is not implemented")
    method = request.method.upper()
    body = await request.body()
    data = None if method in ("GET", "HEAD") else body
    try:
        target = build_upstream_url(base_url, path, request.query_params)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    req = Request(
        target,
        data=data,
        headers=_proxy_request_headers(request.headers),
        method=method,
    )
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(req, timeout=15.0) as resp:
            raw = resp.read()
            status = resp.getcode()
            out_headers = _proxy_response_headers(resp.headers, target)
    except HTTPError as exc:
        raw = exc.read()
        status = exc.code
        out_headers = _proxy_response_headers(exc.headers, target)
    except (URLError, TimeoutError, OSError) as exc:
        raise HTTPException(status_code=502, detail="ALAS Runtime unreachable") from exc

    content_type = ""
    content_encoding = ""
    for key, value in out_headers.items():
        lowered = key.lower()
        if lowered == "content-type":
            content_type = value
        if lowered == "content-encoding":
            content_encoding = value
    should_filter = (
        decision.filtered
        and not content_encoding
        and _content_type_media_type(content_type) in HTML_CONTENT_TYPES
    )
    if should_filter:
        charset = _content_type_charset(content_type)
        text = raw.decode(charset, errors="replace")
        raw = filter_user_html(text, decision.config_name).encode(charset, errors="xmlcharrefreplace")
        out_headers["Content-Type"] = f"{_content_type_media_type(content_type)}; charset={charset}"
    return Response(content=raw, status_code=status, headers=out_headers)
