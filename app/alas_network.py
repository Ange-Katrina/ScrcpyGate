"""Outbound destination validation and DNS-pinned ALAS transports."""

from __future__ import annotations

import http.client
import ipaddress
import os
import socket
from dataclasses import dataclass
from urllib.parse import urlparse
from urllib.request import HTTPHandler, HTTPRedirectHandler, HTTPSHandler, ProxyHandler, build_opener as _build_opener


DEFAULT_ALLOWED_CIDRS = (
    "127.0.0.0/8",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "::1/128",
    "fc00::/7",
    "fe80::/10",
)
class OutboundTargetError(ValueError):
    """Raised when an ALAS destination is not safe to contact."""


@dataclass(frozen=True)
class OutboundTarget:
    url: str
    scheme: str
    hostname: str
    port: int
    addresses: tuple[str, ...]

    @property
    def address(self) -> str | None:
        return self.addresses[0] if self.addresses else None


def _configured_values(name: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in os.environ.get(name, "").split(",") if item.strip())


def _normalize_hostname(value: str) -> str:
    hostname = str(value or "").strip().rstrip(".").lower()
    if hostname.startswith("[") and hostname.endswith("]"):
        hostname = hostname[1:-1]
    try:
        return hostname.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise OutboundTargetError("invalid ALAS upstream host") from exc


def _allowed_hosts() -> tuple[str, ...]:
    return tuple(_normalize_hostname(item) for item in _configured_values("ALAS_ALLOWED_HOSTS"))


def _allowed_networks() -> tuple[ipaddress._BaseNetwork, ...]:
    values = _configured_values("ALAS_ALLOWED_CIDRS") or DEFAULT_ALLOWED_CIDRS
    networks = []
    for value in values:
        try:
            networks.append(ipaddress.ip_network(value, strict=False))
        except ValueError as exc:
            raise OutboundTargetError("invalid ALAS allowed CIDR") from exc
    return tuple(networks)


def _host_is_explicitly_allowed(hostname: str, allowed_hosts: tuple[str, ...]) -> bool:
    for entry in allowed_hosts:
        if entry.startswith("*.") and hostname.endswith(entry[1:]):
            return True
        if hostname == entry:
            return True
    return False


def _resolve_addresses(hostname: str, port: int) -> tuple[str, ...]:
    try:
        results = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except (OSError, socket.gaierror) as exc:
        raise OutboundTargetError("unable to resolve ALAS upstream host") from exc
    addresses = []
    for result in results:
        address = result[4][0]
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise OutboundTargetError("unable to resolve ALAS upstream host")
    return tuple(addresses)


def validate_outbound_url(url: str, schemes: set[str] | frozenset[str] = frozenset({"http", "https"})) -> OutboundTarget:
    """Validate an ALAS URL and resolve its destination before connecting."""
    parsed = urlparse(str(url or ""))
    scheme = parsed.scheme.lower()
    if scheme not in schemes or parsed.username or parsed.password or parsed.fragment:
        raise OutboundTargetError("invalid ALAS upstream URL")
    try:
        hostname = _normalize_hostname(parsed.hostname or "")
        port = parsed.port or (443 if scheme in {"https", "wss"} else 80)
    except (TypeError, ValueError) as exc:
        raise OutboundTargetError("invalid ALAS upstream URL") from exc
    if not hostname or not 1 <= port <= 65535:
        raise OutboundTargetError("invalid ALAS upstream URL")

    allowed_hosts = _allowed_hosts()
    explicit_host = _host_is_explicitly_allowed(hostname, allowed_hosts)
    try:
        literal_ip = ipaddress.ip_address(hostname)
    except ValueError:
        literal_ip = None
    if literal_ip is not None:
        addresses = (hostname,)
    elif hostname == "localhost" or hostname.endswith(".localhost"):
        # localhost 约定映射回环；hosts 文件可覆盖，但解析结果仍须经过 CIDR 校验。
        try:
            addresses = _resolve_addresses(hostname, port)
        except OutboundTargetError:
            addresses = ("127.0.0.1",)
    else:
        # A hostname allowlist is not an address pin. Reject DNS failure
        # instead of letting the HTTP client resolve it again outside this check.
        addresses = _resolve_addresses(hostname, port)

    if literal_ip is not None:
        if not explicit_host and not any(literal_ip in network for network in _allowed_networks()):
            raise OutboundTargetError("ALAS upstream host is not allowed")
    elif not explicit_host:
        networks = _allowed_networks()
        if not addresses or any(
            not any(ipaddress.ip_address(address) in network for network in networks)
            for address in addresses
        ):
            raise OutboundTargetError("ALAS upstream host is not allowed")
    return OutboundTarget(str(url), scheme, hostname, port, addresses)


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host, port=None, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None, *, resolved_ip=None, **kwargs):
        self._resolved_ip = resolved_ip
        super().__init__(host, port, timeout, source_address)

    def _create_connection(self, address, timeout, source_address=None):
        return socket.create_connection(
            (self._resolved_ip or address[0], address[1]),
            timeout,
            source_address,
        )


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, port=None, key_file=None, cert_file=None, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None, *, context=None, check_hostname=None, blocksize=8192, resolved_ip=None, **kwargs):
        self._resolved_ip = resolved_ip
        super().__init__(
            host,
            port,
            key_file=key_file,
            cert_file=cert_file,
            timeout=timeout,
            source_address=source_address,
            context=context,
            check_hostname=check_hostname,
            blocksize=blocksize,
        )

    def _create_connection(self, address, timeout, source_address=None):
        return socket.create_connection(
            (self._resolved_ip or address[0], address[1]),
            timeout,
            source_address,
        )


class _PinnedHTTPHandler(HTTPHandler):
    def __init__(self, resolved_ip: str):
        super().__init__()
        self.resolved_ip = resolved_ip

    def http_open(self, req):
        return self.do_open(
            lambda host, **kwargs: _PinnedHTTPConnection(host, resolved_ip=self.resolved_ip, **kwargs),
            req,
        )


class _PinnedHTTPSHandler(HTTPSHandler):
    def __init__(self, resolved_ip: str):
        super().__init__()
        self.resolved_ip = resolved_ip

    def https_open(self, req):
        return self.do_open(
            lambda host, **kwargs: _PinnedHTTPSConnection(host, resolved_ip=self.resolved_ip, **kwargs),
            req,
        )


class _NoRedirectHandler(HTTPRedirectHandler):
    """上游 3xx 不自动跟随：交由代理层改写 Location 后让浏览器逐跳重走校验。

    钉定的连接只保证首个目标，跟随重定向会在同一 opener 上以新 Host 连旧 IP；
    返回 None 让 urllib 把 3xx 响应原样交给调用方。
    """

    def redirect_request(self, req, _fp, code, msg, headers, _newurl):
        return None


def build_outbound_opener(url: str, opener_factory=_build_opener):
    """Build a proxy-disabled, redirect-free opener pinned to the validated first address."""
    target = validate_outbound_url(url)
    handlers = [ProxyHandler({}), _NoRedirectHandler()]
    if target.address:
        handlers.extend((_PinnedHTTPHandler(target.address), _PinnedHTTPSHandler(target.address)))
    return opener_factory(*handlers), target
