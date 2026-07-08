#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import asyncio
import html as html_utils
import ipaddress
import json
from dataclasses import dataclass
from html import escape
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, unquote, urlencode, urljoin, urlparse, urlunparse
from urllib.request import Request, build_opener, ProxyHandler

from fastapi import HTTPException, Request as FastAPIRequest
from fastapi.responses import Response
from websockets import connect as websocket_connect

ALAS_EMBED_PREFIX = "/alas/embed"
ALAS_DEFAULT_PORT = 22267
DOMAIN_FALLBACK_PORTS = (80, 443, 22267)
MANAGEMENT_MARKERS = ("管理", "admin", "manage", "management", "config_list", "alas.config_list", "settings.admin")
MANAGEMENT_MESSAGE_KEYS = ("event", "command", "method", "action", "path", "topic", "type", "op", "api", "route")
CONFIG_QUERY_KEYS = ("config", "name", "config_name")
BUSINESS_PATH_PREFIXES = ("api", "ajax", "pywebio")
STATIC_PATH_PREFIXES = ("static", "assets", "favicon.ico")
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
RUN_ACTION_MARKERS = ("start", "run", "stop", "pause", "resume", "restart", "deploy", "task", "job")
EDIT_ACTION_MARKERS = ("save", "update", "edit", "delete", "create", "set", "config", "settings")
ACTION_MESSAGE_KEYS = ("event", "command", "method", "action", "path", "topic", "type", "op", "api", "route")
READONLY_ACTION_MARKERS = ("status", "state", "log", "logs", "overview", "summary", "info", "list", "get", "query", "static", "assets")
HTML_CONTENT_TYPES = {"text/html", "application/xhtml+xml"}


@dataclass(frozen=True)
class ProxyDecision:
    """表示 ALAS 嵌入代理访问判定结果。"""

    allowed: bool
    status_code: int = 200
    config_name: str = ""
    filtered: bool = False
    reason: str = ""
    can_run: bool = True
    can_edit: bool = True


@dataclass(frozen=True)
class ParsedBody:
    """表示受限解析后的 HTTP 正文。"""

    value: object
    valid: bool = True


PARSED_BODY_INVALID = ParsedBody(None, False)


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


def _path_switches_config(path: str, config_name: str) -> bool:
    """判断普通用户代理路径是否尝试切换到非绑定 ALAS 配置。"""
    decoded_path = unquote(str(path or "")).replace("\\", "/")
    parts = [part.strip() for part in decoded_path.split("/") if part.strip()]
    for index, part in enumerate(parts[:-1]):
        if part.lower() != "config":
            continue
        requested_config = parts[index + 1]
        if requested_config and requested_config != config_name:
            return True
    return False


def _text_has_action_marker(text: str, markers: tuple[str, ...]) -> bool:
    """按分隔符切分文本，判断是否包含明确 ALAS 操作语义。"""
    normalized = "".join(char.lower() if char.isalnum() else " " for char in str(text or ""))
    tokens = {token for token in normalized.split() if token}
    return any(marker in tokens for marker in markers)


def _query_contains_action(query: dict, markers: tuple[str, ...]) -> bool:
    """检查查询参数键和值是否出现明确运行或编辑操作语义。"""
    for key, value in (query or {}).items():
        if str(key).lower() in CONFIG_QUERY_KEYS:
            continue
        if _text_has_action_marker(key, markers):
            return True
        values = value if isinstance(value, (list, tuple)) else (value,)
        for item in values:
            if _text_has_action_marker(str(item or ""), markers):
                return True
    return False


def _query_contains_management(query: dict) -> bool:
    """检查查询参数键和值是否包含管理操作标记。"""
    for key, value in (query or {}).items():
        if str(key).lower() in CONFIG_QUERY_KEYS:
            continue
        if _text_has_action_marker(key, MANAGEMENT_MARKERS):
            return True
        values = value if isinstance(value, (list, tuple)) else (value,)
        for item in values:
            text = str(item or "").lower()
            if any(marker in text for marker in MANAGEMENT_MARKERS):
                return True
    return False


def _request_requires_explicit_config(method: str, path: str) -> bool:
    """判断普通用户 HTTP 请求是否必须显式携带绑定配置。"""
    upper_method = str(method or "GET").upper()
    normalized_path = unquote(str(path or "")).replace("\\", "/").strip("/").lower()
    first_segment = normalized_path.split("/", 1)[0] if normalized_path else ""
    if upper_method not in SAFE_METHODS:
        return True
    if not normalized_path:
        return False
    if first_segment in STATIC_PATH_PREFIXES:
        return False
    return first_segment in BUSINESS_PATH_PREFIXES


def _has_explicit_bound_config(query: dict, config_name: str) -> bool:
    """判断请求查询参数是否显式指定了绑定配置。"""
    for key in CONFIG_QUERY_KEYS:
        values = _query_values(query or {}, key)
        if values and all(value == config_name for value in values):
            return True
    return False


def _body_contains_management(value) -> bool:
    """递归检查 HTTP 正文是否包含管理操作标记。"""
    return _message_contains_management(value)


def _body_switches_config(value, config_name: str) -> bool:
    """递归检查 HTTP 正文是否请求非绑定配置。"""
    return _message_switches_config(value, config_name)


def _body_denied_by_action_permission(value, can_run: bool, can_edit: bool) -> bool:
    """根据 can_run/can_edit 判断 HTTP 正文是否触发被禁操作。"""
    return _message_denied_by_action_permission(value, can_run, can_edit)


def _is_readonly_http_request(method: str, path: str, query: dict) -> bool:
    """判断 HTTP 请求是否明显为只读页面、静态资源或状态查询。"""
    upper_method = str(method or "GET").upper()
    if upper_method not in SAFE_METHODS:
        return False
    request_text = " ".join([str(path or ""), " ".join(str(key) for key in (query or {}).keys())])
    return not request_text.strip() or _text_has_action_marker(request_text, READONLY_ACTION_MARKERS)


def _denied_by_binding_action_permission(binding: dict, method: str, path: str, query: dict) -> str:
    """根据普通用户绑定 can_run/can_edit 判断 HTTP 运行和编辑类请求是否应拒绝。"""
    if _is_readonly_http_request(method, path, query):
        return ""
    action_text = str(path or "")
    has_run_action = _text_has_action_marker(action_text, RUN_ACTION_MARKERS) or _query_contains_action(query, RUN_ACTION_MARKERS)
    has_edit_action = _text_has_action_marker(action_text, EDIT_ACTION_MARKERS) or _query_contains_action(query, EDIT_ACTION_MARKERS)
    if has_run_action and not binding.get("can_run", True):
        return "run permission denied"
    if has_edit_action and not binding.get("can_edit", False):
        return "edit permission denied"
    return ""


def proxy_decision(user: dict, binding: dict | None, path: str, query: dict, method: str = "GET", body=None) -> ProxyDecision:
    """根据用户角色、绑定配置、路径、方法、查询参数与正文判定代理访问策略。"""
    role = str((user or {}).get("role", ""))
    if role == "admin":
        return ProxyDecision(allowed=True)

    if isinstance(body, ParsedBody):
        if not body.valid:
            return ProxyDecision(allowed=False, status_code=400, reason="invalid body")
        body = body.value

    if not binding or not binding.get("config_name"):
        return ProxyDecision(allowed=False, status_code=403, reason="missing binding")

    config_name = str(binding.get("config_name")).strip()
    if not config_name:
        return ProxyDecision(allowed=False, status_code=403, reason="missing binding")

    lowered_path = str(path or "").lower()
    if "admin" in lowered_path or "manage" in lowered_path or _query_contains_management(query or {}):
        return ProxyDecision(
            allowed=False,
            status_code=403,
            config_name=config_name,
            reason="management path denied",
        )

    if body is not None and _body_contains_management(body):
        return ProxyDecision(
            allowed=False,
            status_code=403,
            config_name=config_name,
            reason="management path denied",
        )

    if _path_switches_config(path, config_name):
        return ProxyDecision(
            allowed=False,
            status_code=403,
            config_name=config_name,
            reason="config path mismatch",
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

    if body is not None and _body_switches_config(body, config_name):
        return ProxyDecision(
            allowed=False,
            status_code=403,
            config_name=config_name,
            reason="config mismatch",
        )

    if _request_requires_explicit_config(method, path) and not _has_explicit_bound_config(query or {}, config_name):
        return ProxyDecision(
            allowed=False,
            status_code=403,
            config_name=config_name,
            reason="missing request config",
        )

    permission_denial = _denied_by_binding_action_permission(binding, method, path, query or {})
    if permission_denial:
        return ProxyDecision(
            allowed=False,
            status_code=403,
            config_name=config_name,
            reason=permission_denial,
        )

    if body is not None and _body_denied_by_action_permission(
        body,
        bool(binding.get("can_run", True)),
        bool(binding.get("can_edit", False)),
    ):
        if not binding.get("can_run", True) and _message_contains_action(body, RUN_ACTION_MARKERS):
            reason = "run permission denied"
        else:
            reason = "edit permission denied"
        return ProxyDecision(
            allowed=False,
            status_code=403,
            config_name=config_name,
            reason=reason,
        )

    return ProxyDecision(
        allowed=True,
        config_name=config_name,
        filtered=True,
        can_run=bool(binding.get("can_run", True)),
        can_edit=bool(binding.get("can_edit", False)),
    )


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
    """探测 ALAS Runtime 根路径是否返回 2xx/3xx 可达状态。"""
    opener = build_opener(ProxyHandler({}))
    req = Request(url, method="GET")
    try:
        with opener.open(req, timeout=timeout) as resp:
            return 200 <= resp.getcode() < 400
    except HTTPError as exc:
        return 200 <= exc.code < 400
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
    safe_location = f"{ALAS_EMBED_PREFIX}/proxy/"
    request_url = str(base_url or "")
    resolved = urljoin(request_url, str(location or ""))
    parsed = urlparse(resolved)
    base = urlparse(request_url)
    if parsed.scheme != base.scheme or parsed.netloc != base.netloc:
        return safe_location

    current_dir = base.path if base.path.endswith("/") else base.path.rsplit("/", 1)[0] + "/"
    base_segments = [part for part in current_dir.split("/") if part]
    base_path = f"/{base_segments[0]}/" if base_segments else "/"
    if base_path != "/" and parsed.path != base_path.rstrip("/") and not parsed.path.startswith(base_path):
        return safe_location

    path = parsed.path.lstrip("/")
    for index in range(len(base_segments), 0, -1):
        candidate = "/".join(base_segments[:index])
        prefix = f"/{candidate}/"
        if parsed.path.startswith(prefix):
            path = parsed.path[len(prefix):].lstrip("/")
            break
    else:
        if base_path != "/":
            return safe_location

    rewritten = safe_location
    if path:
        rewritten = f"{rewritten}{path}"
    if parsed.query:
        rewritten = f"{rewritten}?{parsed.query}"
    if parsed.fragment:
        rewritten = f"{rewritten}#{parsed.fragment}"
    return rewritten


def _message_contains_management(value) -> bool:
    """递归检查 WebSocket 消息明确管理字段是否包含管理操作标记。"""
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in MANAGEMENT_MESSAGE_KEYS:
                text = str(item or "").lower()
                if any(marker in text for marker in MANAGEMENT_MARKERS):
                    return True
            if isinstance(item, (dict, list, tuple)) and _message_contains_management(item):
                return True
    if isinstance(value, (list, tuple)):
        return any(_message_contains_management(item) for item in value)
    return False


def _message_contains_action(value, markers: tuple[str, ...]) -> bool:
    """递归检查 WebSocket 消息关键字段是否包含明显运行或编辑操作语义。"""
    if isinstance(value, dict):
        for key, item in value.items():
            lowered_key = str(key).lower()
            if lowered_key in ACTION_MESSAGE_KEYS and _text_has_action_marker(str(item or ""), markers):
                return True
            if lowered_key not in CONFIG_QUERY_KEYS and _text_has_action_marker(lowered_key, markers) and not isinstance(item, (dict, list, tuple)):
                return True
            if isinstance(item, (dict, list, tuple)) and _message_contains_action(item, markers):
                return True
    if isinstance(value, (list, tuple)):
        return any(_message_contains_action(item, markers) for item in value)
    return False


def _message_denied_by_action_permission(payload, can_run: bool, can_edit: bool) -> bool:
    """根据 can_run/can_edit 判断 WebSocket JSON 载荷是否触发被禁操作。"""
    if not can_run and _message_contains_action(payload, RUN_ACTION_MARKERS):
        return True
    if not can_edit and _message_contains_action(payload, EDIT_ACTION_MARKERS):
        return True
    return False


def _message_switches_config(value, config_name: str) -> bool:
    """递归检查 WebSocket JSON 载荷是否请求非绑定配置。"""
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in CONFIG_QUERY_KEYS and _config_value_mismatches(item, config_name):
                return True
            if isinstance(item, (dict, list, tuple)) and _message_switches_config(item, config_name):
                return True
    if isinstance(value, (list, tuple)):
        return any(_message_switches_config(item, config_name) for item in value)
    return False


def _config_value_mismatches(value, config_name: str) -> bool:
    """递归检查配置字段值，只要存在非空且非绑定配置即视为越权。"""
    if isinstance(value, dict):
        return any(_config_value_mismatches(item, config_name) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_config_value_mismatches(item, config_name) for item in value)
    requested_config = str(value or "").strip()
    return bool(requested_config and requested_config != config_name)


def _parse_websocket_message(message):
    """将 WebSocket 文本或 UTF-8 bytes 消息解析为 JSON 载荷。"""
    if isinstance(message, bytes):
        try:
            message = message.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if not isinstance(message, str):
        return None
    try:
        return json.loads(message)
    except Exception:
        return None


def _text_contains_management_command(text: str) -> bool:
    """检查非 JSON 文本是否包含明显管理路径或命令标记。"""
    lowered_text = str(text or "").lower()
    plain_markers = ("/admin", "/manage", "alas.config_list")
    return any(marker in lowered_text for marker in plain_markers)


def websocket_message_allowed(message: str | bytes, config_name: str, can_run: bool = True, can_edit: bool = True) -> bool:
    """检查 WebSocket 消息是否试图越权访问配置、管理或运行编辑操作。"""
    payload = _parse_websocket_message(message)
    if payload is None:
        if isinstance(message, bytes):
            return False
        text = str(message or "")
        if _text_contains_management_command(text):
            return False
        if not can_run and _text_has_action_marker(text, RUN_ACTION_MARKERS):
            return False
        if not can_edit and _text_has_action_marker(text, EDIT_ACTION_MARKERS):
            return False
        return True
    if _message_contains_management(payload):
        return False
    if _message_switches_config(payload, config_name):
        return False
    if _message_denied_by_action_permission(payload, can_run, can_edit):
        return False
    return True


def websocket_target_url(base_url: str, path: str, query_items) -> str:
    """将 HTTP Runtime URL 转换为 WebSocket 目标 URL。"""
    target = build_upstream_url(base_url, path, query_items)
    if target.startswith("https://"):
        return "wss://" + target[len("https://"):]
    if target.startswith("http://"):
        return "ws://" + target[len("http://"):]
    raise ValueError("invalid ALAS websocket URL")


async def _close_websocket_safely(websocket, code: int) -> None:
    """忽略已关闭连接错误，按指定关闭码关闭客户端 WebSocket。"""
    try:
        await websocket.close(code=code)
    except Exception:
        pass


async def _close_upstream_safely(upstream, code: int = 1000) -> None:
    """忽略已关闭连接错误，按指定关闭码关闭上游 WebSocket。"""
    try:
        await upstream.close(code=code)
    except Exception:
        pass


async def proxy_websocket(websocket, base_url: str, path: str, decision: ProxyDecision) -> None:
    """双向转发 ScrcpyGate 客户端与 ALAS Runtime 的 WebSocket 消息。"""
    try:
        target = websocket_target_url(base_url, path, websocket.query_params.multi_items())
    except ValueError:
        await websocket.close(code=1011)
        return

    await websocket.accept()
    try:
        async with websocket_connect(target, open_timeout=10.0) as upstream:
            async def client_to_upstream() -> None:
                """转发客户端文本或二进制消息到上游，并执行普通用户配置越权检查。"""
                while True:
                    message = await websocket.receive()
                    if message.get("type") == "websocket.disconnect":
                        await _close_upstream_safely(upstream)
                        return 1000
                    if "text" in message:
                        text = message["text"]
                        if decision.filtered and not websocket_message_allowed(
                            text,
                            decision.config_name,
                            can_run=getattr(decision, "can_run", True),
                            can_edit=getattr(decision, "can_edit", True),
                        ):
                            await _close_upstream_safely(upstream, code=1008)
                            await _close_websocket_safely(websocket, 1008)
                            return 1008
                        await upstream.send(text)
                    elif "bytes" in message:
                        data = message["bytes"]
                        if decision.filtered and not websocket_message_allowed(
                            data,
                            decision.config_name,
                            can_run=getattr(decision, "can_run", True),
                            can_edit=getattr(decision, "can_edit", True),
                        ):
                            await _close_upstream_safely(upstream, code=1008)
                            await _close_websocket_safely(websocket, 1008)
                            return 1008
                        await upstream.send(data)

            async def upstream_to_client() -> None:
                """转发上游文本或二进制消息回客户端。"""
                async for message in upstream:
                    if isinstance(message, bytes):
                        await websocket.send_bytes(message)
                    else:
                        await websocket.send_text(str(message))

            tasks = [
                asyncio.create_task(client_to_upstream()),
                asyncio.create_task(upstream_to_client()),
            ]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            close_code = 1000
            for task in done:
                result = task.result()
                if isinstance(result, int):
                    close_code = result
            await _close_upstream_safely(upstream, code=close_code)
            await _close_websocket_safely(websocket, close_code)
    except Exception:
        await _close_websocket_safely(websocket, 1011)


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


def parse_limited_body(body: bytes, content_type: str, limit: int = 65536):
    """在大小限制内解析 JSON 或 form-urlencoded 正文，并保留解析状态。"""
    if not body:
        return ParsedBody({})
    if len(body) > limit:
        return PARSED_BODY_INVALID
    media_type = _content_type_media_type(content_type)
    if media_type == "application/json":
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            return PARSED_BODY_INVALID
        if not isinstance(payload, (dict, list)):
            return PARSED_BODY_INVALID
        return ParsedBody(payload)
    if media_type == "application/x-www-form-urlencoded":
        try:
            form = parse_qs(body.decode("utf-8"), keep_blank_values=True)
        except UnicodeDecodeError:
            return PARSED_BODY_INVALID
        return ParsedBody({key: values[-1] if values else "" for key, values in form.items()})
    return None


async def proxy_http_request(request: FastAPIRequest, base_url: str, path: str, decision: ProxyDecision, body: bytes | None = None) -> Response:
    """转发 HTTP 请求到 ALAS Runtime，并按权限策略过滤 HTML 响应。"""
    if request.headers.get("upgrade") or "upgrade" in request.headers.get("connection", "").lower():
        raise HTTPException(status_code=501, detail="ALAS websocket proxy is not implemented")
    method = request.method.upper()
    if body is None:
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
    out_headers.pop("X-Frame-Options", None)
    out_headers["Content-Security-Policy"] = "frame-ancestors 'self'"
    return Response(content=raw, status_code=status, headers=out_headers)
