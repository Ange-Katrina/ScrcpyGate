#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import ipaddress
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, build_opener, ProxyHandler

ALAS_EMBED_PREFIX = "/alas/embed"
ALAS_DEFAULT_PORT = 22267
DOMAIN_FALLBACK_PORTS = (80, 443, 22267)


def _parse_runtime_url(raw_url: str):
    """解析运行时地址，并补齐无协议输入的默认协议。"""
    value = str(raw_url or "").strip()
    if not value:
        raise ValueError("invalid ALAS runtime URL")
    if "://" not in value:
        value = f"http://{value}"
    return urlparse(value)


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
    parsed = _parse_runtime_url(raw_url)
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
