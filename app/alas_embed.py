#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import html as html_utils
import ipaddress
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, build_opener, ProxyHandler

ALAS_EMBED_PREFIX = "/alas/embed"
ALAS_DEFAULT_PORT = 22267
DOMAIN_FALLBACK_PORTS = (80, 443, 22267)
MANAGEMENT_MARKERS = ("管理", "Manage", "Settings.Admin", "alas.config_list")
CONFIG_QUERY_KEYS = ("config", "name", "config_name")


def embed_shell_html(title: str, iframe_src: str, message: str = "") -> str:
    """生成 ALAS 嵌入入口页面的完整 HTML 外壳。"""
    safe_title = html_utils.escape(str(title or ""), quote=True)
    safe_iframe_src = html_utils.escape(str(iframe_src or ""), quote=True)
    safe_message = html_utils.escape(str(message or ""), quote=True)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title}</title>
  <style>
    html, body {{ margin: 0; height: 100%; background: #0f172a; color: #e5e7eb; font-family: system-ui, sans-serif; }}
    .bar {{ box-sizing: border-box; min-height: 48px; padding: 12px 16px; display: flex; align-items: center; gap: 12px; background: #111827; border-bottom: 1px solid #334155; }}
    .title {{ font-weight: 700; }}
    .message {{ color: #cbd5e1; font-size: 14px; }}
    .home {{ margin-left: auto; color: #93c5fd; text-decoration: none; }}
    iframe {{ display: block; width: 100%; height: calc(100vh - 49px); border: 0; background: #ffffff; }}
  </style>
</head>
<body>
  <div class="bar"><span class="title">{safe_title}</span><span class="message">{safe_message}</span><a class="home" href="/">ScrcpyGate</a></div>
  <iframe src="{safe_iframe_src}" title="{safe_title}"></iframe>
</body>
</html>"""


@dataclass(frozen=True)
class ProxyDecision:
    """表示 ALAS 嵌入代理访问判定结果。"""

    allowed: bool
    status_code: int = 200
    config_name: str = ""
    filtered: bool = False
    reason: str = ""


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
