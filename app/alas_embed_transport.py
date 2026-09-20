"""ALAS Embed Runtime URL resolution and HTTP/WebSocket transport."""

from __future__ import annotations

import asyncio
import inspect
import ipaddress
import json
import logging
import re
import socket
import ssl
import time
import uuid
import zlib
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlparse, urlunparse
from urllib.request import Request, build_opener

from fastapi import HTTPException, Request as FastAPIRequest
from fastapi.responses import Response
from starlette.websockets import WebSocketDisconnect, WebSocketState
from websockets import connect as websocket_connect
from websockets.exceptions import ConnectionClosed, ConnectionClosedOK, InvalidHandshake

from . import alas_gateway, i18n
from .alas_embed_content import (
    filter_user_html,
    inject_alas_ready_script,
    inject_feature_gate_script,
    rewrite_bound_html_urls,
)
from .alas_embed_policy import (
    ALAS_DEFAULT_PORT,
    ALAS_EMBED_PREFIX,
    ALAS_PROXY_REQUEST_MAX_BYTES,
    ALAS_REQUEST_BODY_TOO_LARGE_DETAIL,
    ALAS_UPSTREAM_DECOMPRESSED_MAX_BYTES,
    ALAS_UPSTREAM_RESPONSE_MAX_BYTES,
    CONFIG_QUERY_KEYS,
    CREDENTIAL_REQUEST_HEADERS,
    DOMAIN_FALLBACK_PORTS,
    HOP_BY_HOP_HEADERS,
    HTML_CONTENT_TYPES,
    OMITTED_RESPONSE_HEADERS,
    PayloadBudgetExceeded,
    PARSED_BODY_INVALID,
    ParsedBody,
    PYWEBIO_LOG_COMMANDS,
    ProxyDecision,
    PyWebIOSessionPolicy,
    SCRCPYGATE_CONTEXT_QUERY_KEYS,
    UPSTREAM_READ_CHUNK_BYTES,
    UPSTREAM_RESPONSE_TOO_LARGE_DETAIL,
    WebSocketMessageAction,
    WebSocketMessageDecision,
    _ensure_websocket_message_size,
    _filter_user_websocket_downstream_payload,
    bound_config_query_items,
    filter_user_json_response,
    _parse_websocket_message,
)
from .alas_network import OutboundTarget, OutboundTargetError, build_outbound_opener, validate_outbound_url
from .alas_response import ResponseBodyTooLarge, read_bounded_response
from .http_body import (
    REQUEST_BODY_IDLE_TIMEOUT_SECONDS,
    RequestBodyIdleTimeout,
    RequestBodyTooLarge,
    read_request_body_limited,
)

log = logging.getLogger("webscrcpy.alas_embed")
ALAS_WS_TASK_CLEANUP_TIMEOUT_SECONDS = 2.0
ALAS_WS_AUTHORIZATION_RECHECK_SECONDS = 15.0


_STATIC_PROXY_CACHE_TTL_SECONDS = 600.0
_STATIC_PROXY_CACHE_MAX_ENTRIES = 128
_static_proxy_cache: dict[tuple[str, str, bool, str, str, str], tuple[bytes, int, str, float]] = {}


def _static_proxy_cache_key(
    base_url: str,
    path: str,
    filtered: bool = False,
    *,
    config_name: str = "",
    device_context: str = "",
    gateway_context: str = "",
) -> tuple[str, str, bool, str, str, str]:
    """Keep cached static responses isolated by the context used upstream."""
    return (
        str(base_url or ""),
        str(path or ""),
        bool(filtered),
        str(config_name or ""),
        str(device_context or ""),
        str(gateway_context or ""),
    )


def static_proxy_cache_get(
    base_url: str,
    path: str,
    filtered: bool = False,
    *,
    config_name: str = "",
    device_context: str = "",
    gateway_context: str = "",
) -> tuple[bytes, int, str] | None:
    key = _static_proxy_cache_key(
        base_url,
        path,
        filtered,
        config_name=config_name,
        device_context=device_context,
        gateway_context=gateway_context,
    )
    entry = _static_proxy_cache.get(key)
    if entry is None:
        return None
    body, status, content_type, expires = entry
    if time.monotonic() >= expires:
        _static_proxy_cache.pop(key, None)
        return None
    # 只回放完整的 200：304 是「客户端缓存仍然有效」的应答，本身没有正文与
    # Content-Type，回放给没有缓存的客户端会让浏览器拒绝加载（实测样式表被拒，
    # 普通用户的 ALAS 页面只剩空白主页）。
    if status != 200 or not body or not content_type:
        _static_proxy_cache.pop(key, None)
        return None
    return body, status, content_type


def static_proxy_cache_put(
    base_url: str,
    path: str,
    body: bytes,
    status: int,
    content_type: str,
    filtered: bool = False,
    *,
    config_name: str = "",
    device_context: str = "",
    gateway_context: str = "",
) -> None:
    key = _static_proxy_cache_key(
        base_url,
        path,
        filtered,
        config_name=config_name,
        device_context=device_context,
        gateway_context=gateway_context,
    )
    if len(_static_proxy_cache) >= _STATIC_PROXY_CACHE_MAX_ENTRIES:
        _static_proxy_cache.clear()
    # 只缓存完整的 200：上游对「If-None-Match / If-Modified-Since」会回 304，
    # 那种空正文 + 无 Content-Type 的应答一旦进缓存，就会被回放给所有后续客户端
    # （它们并没有对应的本地缓存），浏览器会拒绝加载，页面直接残缺。
    if int(status) != 200 or not body or not str(content_type or "").strip():
        _static_proxy_cache.pop(key, None)
        return
    _static_proxy_cache[key] = (
        bytes(body or b""),
        int(status),
        str(content_type or ""),
        time.monotonic() + _STATIC_PROXY_CACHE_TTL_SECONDS,
    )
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
    try:
        opener, _target = build_outbound_opener(url, build_opener)
    except OutboundTargetError:
        return False
    req = Request(url, method="GET")
    try:
        with opener.open(req, timeout=timeout) as resp:
            return 200 <= resp.getcode() < 400
    except HTTPError as exc:
        try:
            return 200 <= exc.code < 400
        finally:
            try:
                exc.close()
            except Exception:
                # A broken cleanup object must not change the probe result.
                pass
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
    """过滤客户端请求头，移除逐跳头、Host、Content-Length、压缩协商与浏览器凭据。

    `If-None-Match` / `If-Modified-Since` 也必须丢掉：代理自己缓存静态资源，
    把浏览器的校验器转给上游会让上游回 304（空正文、无 Content-Type），
    这种应答对「本地没有缓存」的客户端不可用 —— 曾经导致普通用户的 ALAS
    页面样式表被浏览器拒绝，只剩一个空白主页。
    """
    result = {}
    omitted = HOP_BY_HOP_HEADERS | _connection_header_names(headers)
    omitted.update({"host", "content-length", "accept-encoding"})
    omitted.update({"if-none-match", "if-modified-since"})
    omitted.update(CREDENTIAL_REQUEST_HEADERS)
    for key, value in headers.items():
        if key.lower() not in omitted:
            result[key] = value
    return result


def _append_config_to_embed_url(
    value: str,
    config_name: str,
    device_context: str = "",
    gateway_context: str = "",
) -> str:
    if not config_name and not device_context and not gateway_context:
        return value
    parsed = urlparse(str(value or ""))
    if not parsed.path.startswith(f"{ALAS_EMBED_PREFIX}/proxy"):
        return value
    query = parse_qs(parsed.query, keep_blank_values=True)
    pairs = []
    for key, values in query.items():
        if str(key).lower() in CONFIG_QUERY_KEYS or str(key).lower() in SCRCPYGATE_CONTEXT_QUERY_KEYS:
            continue
        if values:
            pairs.extend((key, item) for item in values)
        else:
            pairs.append((key, ""))
    if config_name:
        pairs.append(("config", config_name))
    if device_context:
        pairs.append(("device_id", device_context))
    if gateway_context:
        pairs.append((alas_gateway.ALAS_CONTEXT_QUERY, gateway_context))
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, urlencode(pairs, doseq=True), parsed.fragment))


def _proxy_response_headers(headers, request_url: str, decision: ProxyDecision | None = None) -> dict:
    """过滤上游响应头，并将 Location 改写为嵌入代理路径。"""
    result = {}
    omitted = OMITTED_RESPONSE_HEADERS | _connection_header_names(headers)
    for key, value in headers.items():
        lowered = key.lower()
        if lowered in omitted:
            continue
        if lowered == "location":
            value = rewrite_location_header(str(value), request_url)
            if decision and decision.filtered:
                value = _append_config_to_embed_url(
                    str(value),
                    decision.config_name,
                    decision.device_context,
                    decision.gateway_context,
                )
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


def _prefer_proxy_close_code(current: int, candidate: object) -> int:
    """Keep an authorization/policy close code from being masked by normal EOF."""
    if not isinstance(candidate, int):
        return current
    if current != 1000:
        return current
    return candidate


def _safe_connection_id(value: object) -> str:
    return "".join(char for char in str(value or "") if char.isalnum() or char in "-_")[:32]


def _log_upstream_decision(
    connection_id: str,
    decision: WebSocketMessageDecision,
) -> None:
    log_method = log.debug if decision.action is WebSocketMessageAction.FORWARD else log.warning
    log_method(
        "ALAS_WS_POLICY connection=%s event=%s reason=%s permission=%s task=%s",
        connection_id,
        decision.event or "legacy",
        decision.reason,
        decision.permission or "none",
        decision.task_ref or "none",
    )


def _websocket_connect_kwargs(target: OutboundTarget) -> dict[str, object]:
    """Build connector options while supporting multiple websockets versions."""
    connect_kwargs: dict[str, object] = {"open_timeout": 10.0}
    try:
        connect_parameters = inspect.signature(websocket_connect).parameters
    except (TypeError, ValueError):
        connect_parameters = {}
    supports_extra = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in connect_parameters.values()
    )
    if target.address and ("host" in connect_parameters or supports_extra):
        connect_kwargs.update(host=target.address, port=target.port)
    if "proxy" in connect_parameters:
        connect_kwargs["proxy"] = None
    return connect_kwargs


def _websocket_failure(exc: Exception, phase: str) -> tuple[str, dict]:
    """Classify transport failures without retaining addresses, headers or close reasons."""
    info = {"phase": phase, "exception_type": type(exc).__name__}
    if isinstance(exc, ConnectionClosed):
        info["close_code"] = getattr(exc.rcvd, "code", 1006)
        if exc.sent is not None:
            info["sent_close_code"] = exc.sent.code
        return "upstream_connection_closed", info
    if isinstance(exc, InvalidHandshake):
        status = getattr(exc, "status_code", None)
        if status is None:
            status = getattr(getattr(exc, "response", None), "status_code", None)
        if isinstance(status, int) and 100 <= status <= 599:
            info["upstream_status"] = status
        return "upstream_handshake_failed", info
    if isinstance(exc, ssl.SSLError):
        return "upstream_tls_failed", info
    if isinstance(exc, socket.gaierror):
        return "upstream_dns_failed", info
    if isinstance(exc, ConnectionRefusedError):
        return "upstream_connection_refused", info
    if isinstance(exc, TimeoutError):
        return "upstream_connect_timeout" if phase == "connect" else "upstream_timeout", info
    if isinstance(exc, OSError):
        return "upstream_network_error", info
    return "proxy_internal_error", info


async def _cancel_and_join_proxy_tasks(
    tasks: list[asyncio.Task],
    connection_id: str,
) -> bool:
    """Cancel both proxy pumps and consume their results within a deadline."""

    def consume_late_result(task: asyncio.Task) -> None:
        """Consume a cancellation-resistant task's eventual result."""
        if task.cancelled():
            return
        try:
            task.result()
        except BaseException:
            # The proxy already recorded the bounded cleanup failure.  A
            # late task result must not become an unhandled event-loop error.
            pass

    cancelled = False
    for task in tasks:
        if not task.done():
            task.cancel()
    joined = asyncio.gather(*tasks, return_exceptions=True)
    deadline = time.monotonic() + ALAS_WS_TASK_CLEANUP_TIMEOUT_SECONDS
    while not joined.done():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            for task in tasks:
                if not task.done():
                    task.add_done_callback(consume_late_result)
            log.warning(
                "ALAS_WS_TASK_CLEANUP_TIMEOUT connection=%s event=cleanup reason=task_timeout permission=none task=none",
                connection_id,
            )
            return cancelled
        try:
            await asyncio.wait_for(asyncio.shield(joined), timeout=remaining)
        except asyncio.CancelledError:
            cancelled = True
            # A cancellation delivered while the proxy is already unwinding
            # must not turn the bounded cleanup wait into a tight loop.  Save
            # the cancellation for the caller and let gather consume both
            # task results within the same monotonic deadline.
            current_task = asyncio.current_task()
            if current_task is not None:
                while current_task.cancelling():
                    current_task.uncancel()
            continue
        except asyncio.TimeoutError:
            for task in tasks:
                if not task.done():
                    task.add_done_callback(consume_late_result)
            log.warning(
                "ALAS_WS_TASK_CLEANUP_TIMEOUT connection=%s event=cleanup reason=task_timeout permission=none task=none",
                connection_id,
            )
            return cancelled
    return cancelled


class _WsAuditEmitter:
    """Emit bounded ALAS WebSocket policy categories once per connection.

    Never forwards message bodies: at most 16 distinct (action, reason,
    permission, event) categories per connection, and a failing callback is
    logged instead of tearing down the proxy.
    """

    def __init__(self, audit_callback, connection_id: str):
        self._callback = audit_callback
        self._connection_id = connection_id
        self._seen: set[tuple[str, str, str, str]] = set()

    async def emit(
        self,
        action: str,
        *,
        outcome: str,
        reason: str,
        severity: str = "warning",
        permission: str = "",
        event: str = "",
        diagnostics: dict | None = None,
    ) -> None:
        if self._callback is None:
            return
        key = (action, reason, permission, event)
        if key in self._seen or len(self._seen) >= 16:
            return
        self._seen.add(key)
        safe_event = event if re.fullmatch(r"[a-z][a-z0-9_]{0,31}", event or "") else "none"
        metadata = {
            "permission": permission or "none",
            "event": safe_event,
        }
        # Only structural diagnostics; never forward exception messages or URLs.
        for name in ("phase", "exception_type"):
            value = (diagnostics or {}).get(name)
            if isinstance(value, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", value):
                metadata[name] = value
        for name in ("close_code", "sent_close_code", "upstream_status"):
            value = (diagnostics or {}).get(name)
            if type(value) is int and 100 <= value <= 4999:
                metadata[name] = value
        try:
            result = self._callback(
                action,
                outcome=outcome,
                reason=reason,
                severity=severity,
                metadata=metadata,
            )
            if inspect.isawaitable(result):
                await result
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception(
                "ALAS_WS_AUDIT_FAILED connection=%s event=%s reason=callback_failed permission=none task=none",
                self._connection_id,
                event or "audit",
            )


async def _ws_refresh_authorization(policy, authorization_check, connection_id: str, emit_audit) -> bool:
    """Revalidate the ALAS binding and refresh the run/edit policy."""
    if policy is None or authorization_check is None:
        return True
    try:
        current = authorization_check()
        if inspect.isawaitable(current):
            current = await current
    except Exception:
        current = None
    if not current or str(current.get("config_name") or "").strip() != policy.config_name:
        log.warning(
            "ALAS_WS_POLICY connection=%s event=authorization reason=binding_revoked permission=restricted task=none",
            connection_id,
        )
        await emit_audit(
            "alas_embed_ws_denied",
            outcome="denied",
            reason="binding_revoked",
            permission="restricted",
            event="authorization",
        )
        return False
    policy.can_run = bool(current.get("can_run"))
    policy.can_edit = bool(current.get("can_edit"))
    return True


async def _ws_refresh_session(session_check, connection_id: str, emit_audit) -> bool:
    """Revalidate the source session; a revoked session closes with 4403."""
    if session_check is None:
        return True
    try:
        current = session_check()
        if inspect.isawaitable(current):
            current = await current
    except asyncio.CancelledError:
        raise
    except Exception:
        current = False
    if current:
        return True
    log.warning(
        "ALAS_WS_CLOSE connection=%s event=authorization reason=session_revoked permission=none task=none",
        connection_id,
    )
    await emit_audit(
        "alas_embed_ws_denied",
        outcome="denied",
        reason="session_revoked",
        permission="none",
        event="authorization",
    )
    return False


async def _ws_refresh_access(
    session_check,
    authorization_check,
    policy,
    connection_id: str,
    emit_audit,
) -> int | None:
    """Return the close code when session or binding authorization is gone."""
    if not await _ws_refresh_session(session_check, connection_id, emit_audit):
        return 4403
    if not await _ws_refresh_authorization(policy, authorization_check, connection_id, emit_audit):
        return 1008
    return None


class _WebSocketProxySession:
    """One proxied ALAS WebSocket connection and its two forwarding pumps."""

    def __init__(
        self,
        *,
        websocket,
        upstream,
        decision: ProxyDecision,
        policy,
        connection_id: str,
        audit: _WsAuditEmitter,
        session_check,
        authorization_check,
        authorization_lock: asyncio.Lock,
    ):
        self.websocket = websocket
        self.upstream = upstream
        self.decision = decision
        self.policy = policy
        self.connection_id = connection_id
        self.audit = audit
        self.session_check = session_check
        self.authorization_check = authorization_check
        self.authorization_lock = authorization_lock
        self.client_message_seen = asyncio.Event()
        self.client_message_done = asyncio.Event()
        self.client_message_done.set()
        self._access_checked_at = 0.0

    def access_check_due(self) -> bool:
        """Whether the downstream loop must re-run the session/binding check.

        Every upstream frame used to re-run it, which costs a thread hop plus a
        session lookup; PyWebIO streams thousands of small frames while it
        renders a page, so that stretched a 3 s load into tens of seconds.
        Restricted users kept the per-frame refresh after the first fix, because
        their payload filtering was tied to the same refresh — but that filtering
        only needs the configured rules, and permissions are enforced per client
        message (`client_to_upstream`) and by the idle watchdog, both of which
        still check unconditionally.  The interval therefore applies to everyone,
        so a revoked session or binding takes effect within
        ALAS_WS_AUTHORIZATION_RECHECK_SECONDS for bound users too.
        """
        return (time.monotonic() - self._access_checked_at) >= ALAS_WS_AUTHORIZATION_RECHECK_SECONDS

    async def refresh_access(self) -> int | None:
        return await _ws_refresh_access(
            self.session_check,
            self.authorization_check,
            self.policy,
            self.connection_id,
            self.audit.emit,
        )

    async def client_to_upstream(self) -> None:
        """转发客户端文本或二进制消息到上游，并执行普通用户配置越权检查。"""
        while True:
            message = await self.websocket.receive()
            self.client_message_seen.set()
            self.client_message_done.clear()
            if message.get("type") == "websocket.disconnect":
                await _close_upstream_safely(self.upstream)
                self.client_message_done.set()
                return 1000
            incoming = message.get("text") if "text" in message else message.get("bytes")
            try:
                _ensure_websocket_message_size(incoming)
            except PayloadBudgetExceeded as exc:
                log.warning(
                    "ALAS_WS_CLOSE connection=%s event=policy reason=%s permission=restricted task=none",
                    self.connection_id,
                    exc.reason,
                )
                await _close_upstream_safely(self.upstream, code=1008)
                await _close_websocket_safely(self.websocket, 1008)
                await self.audit.emit(
                    "alas_embed_ws_denied",
                    outcome="denied",
                    reason=exc.reason,
                    permission="restricted",
                    event="message",
                )
                self.client_message_done.set()
                return 1008
            if "text" in message:
                text = message["text"]
                if self.session_check is not None or self.policy is not None:
                    async with self.authorization_lock:
                        close_reason = await self.refresh_access()
                        if close_reason is not None:
                            await _close_upstream_safely(
                                self.upstream,
                                code=close_reason,
                            )
                            await _close_websocket_safely(
                                self.websocket,
                                close_reason,
                            )
                            self.client_message_done.set()
                            return close_reason
                        message_decision = self.policy.evaluate_upstream(text) if self.policy is not None else None
                else:
                    message_decision = None
                if message_decision is not None:
                    _log_upstream_decision(self.connection_id, message_decision)
                    if message_decision.closes_connection:
                        await _close_upstream_safely(self.upstream, code=1008)
                        await _close_websocket_safely(self.websocket, 1008)
                        await self.audit.emit(
                            "alas_embed_ws_denied",
                            outcome="denied",
                            reason=message_decision.reason,
                            permission=message_decision.permission or "restricted",
                            event=message_decision.event,
                        )
                        self.client_message_done.set()
                        return 1008
                    if message_decision.action is not WebSocketMessageAction.FORWARD:
                        await self.audit.emit(
                            "alas_embed_ws_denied",
                            outcome="denied",
                            reason=message_decision.reason,
                            permission=message_decision.permission or "restricted",
                            event=message_decision.event,
                        )
                    if message_decision.action is WebSocketMessageAction.DROP:
                        self.client_message_done.set()
                        continue
                await self.upstream.send(text)
                self.client_message_done.set()
            elif "bytes" in message:
                data = message["bytes"]
                if self.session_check is not None or self.policy is not None:
                    async with self.authorization_lock:
                        close_reason = await self.refresh_access()
                        if close_reason is not None:
                            await _close_upstream_safely(
                                self.upstream,
                                code=close_reason,
                            )
                            await _close_websocket_safely(
                                self.websocket,
                                close_reason,
                            )
                            self.client_message_done.set()
                            return close_reason
                        message_decision = self.policy.evaluate_upstream(data) if self.policy is not None else None
                else:
                    message_decision = None
                if message_decision is not None:
                    _log_upstream_decision(self.connection_id, message_decision)
                    if message_decision.closes_connection:
                        await _close_upstream_safely(self.upstream, code=1008)
                        await _close_websocket_safely(self.websocket, 1008)
                        await self.audit.emit(
                            "alas_embed_ws_denied",
                            outcome="denied",
                            reason=message_decision.reason,
                            permission=message_decision.permission or "restricted",
                            event=message_decision.event,
                        )
                        self.client_message_done.set()
                        return 1008
                    if message_decision.action is not WebSocketMessageAction.FORWARD:
                        await self.audit.emit(
                            "alas_embed_ws_denied",
                            outcome="denied",
                            reason=message_decision.reason,
                            permission=message_decision.permission or "restricted",
                            event=message_decision.event,
                        )
                    if message_decision.action is WebSocketMessageAction.DROP:
                        self.client_message_done.set()
                        continue
                await self.upstream.send(data)
                self.client_message_done.set()
            else:
                self.client_message_done.set()

    async def upstream_to_client(self) -> None:
        """转发上游文本或二进制消息回客户端。"""
        async for message in self.upstream:
            try:
                _ensure_websocket_message_size(message)
            except PayloadBudgetExceeded as exc:
                log.warning(
                    "ALAS_WS_DOWNSTREAM connection=%s event=policy reason=%s permission=%s task=none",
                    self.connection_id,
                    exc.reason,
                    "none" if self.policy is None else "restricted",
                )
                await self.audit.emit(
                    "alas_embed_ws_denied",
                    outcome="denied",
                    reason=exc.reason,
                    permission="restricted",
                    event="downstream",
                )
                if self.policy is None:
                    await _close_upstream_safely(self.upstream, code=1008)
                    await _close_websocket_safely(self.websocket, 1008)
                    return 1008
                continue
            if self.session_check is not None or self.policy is not None:
                try:
                    async with self.authorization_lock:
                        # 会话/绑定校验按固定间隔执行（所有人一致，见 access_check_due），
                        # 逐帧只做转发与过滤。
                        if self.access_check_due():
                            close_reason = await self.refresh_access()
                            self._access_checked_at = time.monotonic()
                            if close_reason is not None:
                                await _close_upstream_safely(
                                    self.upstream,
                                    code=close_reason,
                                )
                                await _close_websocket_safely(
                                    self.websocket,
                                    close_reason,
                                )
                                return close_reason
                        if self.policy is not None:
                            original_message = message
                            original_payload = _parse_websocket_message(original_message)
                            message, filtered_payload = _filter_user_websocket_downstream_payload(
                                original_message,
                                original_payload,
                                self.decision.config_name,
                                self.decision.hidden_matches,
                            )
                            observation = self.policy.observe_downstream(
                                original_message,
                                message,
                                original_payload=original_payload,
                                filtered_payload=filtered_payload,
                            )
                except PayloadBudgetExceeded as exc:
                    log.warning(
                        "ALAS_WS_DOWNSTREAM connection=%s event=policy reason=%s permission=restricted task=none",
                        self.connection_id,
                        exc.reason,
                    )
                    await self.audit.emit(
                        "alas_embed_ws_denied",
                        outcome="denied",
                        reason=exc.reason,
                        permission="restricted",
                        event="downstream",
                    )
                    continue
                if self.policy is not None:
                    # forwarded / filtered：帧仍然转发（后者只是清空了受限内容），
                    # 属于普通用户的正常渲染路径，降为 debug；真正丢弃（*_dropped）
                    # 才记 warning。过滤本身另有 alas_embed_ws_denied 审计事件。
                    downstream_log = (
                        log.debug
                        if observation.reason in {"forwarded", "filtered"}
                        else log.warning
                    )
                    downstream_event = (
                        observation.command
                        if observation.command in PYWEBIO_LOG_COMMANDS
                        else "unknown"
                    )
                    downstream_log(
                        "ALAS_WS_DOWNSTREAM connection=%s event=%s reason=%s permission=%s task=none",
                        self.connection_id,
                        downstream_event,
                        observation.reason,
                        "none" if observation.reason == "forwarded" else "restricted",
                    )
                    if observation.reason != "forwarded":
                        await self.audit.emit(
                            "alas_embed_ws_denied",
                            outcome="denied",
                            reason=observation.reason,
                            permission="restricted",
                            event=downstream_event,
                        )
                    if message is None:
                        continue
            if isinstance(message, bytes):
                await self.websocket.send_bytes(message)
            else:
                await self.websocket.send_text(str(message))

    async def authorization_watchdog(self) -> int:
        """Recheck session and binding authorization while both sides are idle."""
        while True:
            await asyncio.sleep(ALAS_WS_AUTHORIZATION_RECHECK_SECONDS)
            async with self.authorization_lock:
                close_reason = await self.refresh_access()
            if close_reason is None:
                continue
            await _close_upstream_safely(self.upstream, code=close_reason)
            await _close_websocket_safely(self.websocket, close_reason)
            return close_reason

    async def supervise(self) -> int:
        client_task = asyncio.create_task(self._run_pump(self.client_to_upstream))
        upstream_task = asyncio.create_task(self._run_pump(self.upstream_to_client))
        tasks = [client_task, upstream_task]
        if self.session_check is not None or self.authorization_check is not None:
            tasks.append(asyncio.create_task(self.authorization_watchdog()))
        close_code = 1000
        wait_cancelled = False
        task_exception: BaseException | None = None
        try:
            try:
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            except asyncio.CancelledError:
                wait_cancelled = True
                if self.client_message_seen.is_set() and not self.client_message_done.is_set():
                    current_task = asyncio.current_task()
                    if current_task is not None:
                        while current_task.cancelling():
                            current_task.uncancel()
                    grace_deadline = time.monotonic() + 1.0
                    while not self.client_message_done.is_set():
                        remaining = grace_deadline - time.monotonic()
                        if remaining <= 0:
                            break
                        try:
                            await asyncio.wait_for(
                                asyncio.shield(self.client_message_done.wait()),
                                timeout=remaining,
                            )
                        except asyncio.CancelledError:
                            if current_task is not None:
                                while current_task.cancelling():
                                    current_task.uncancel()
                            continue
                        except asyncio.TimeoutError:
                            break
                done = {task for task in tasks if task.done()}
                pending = set(tasks) - done
            upstream_result = None
            for task in done:
                if task.cancelled():
                    continue
                try:
                    result = task.result()
                except asyncio.CancelledError:
                    continue
                except Exception as exc:
                    # Consume the failed pump only after both tasks enter
                    # the common cleanup path below.  Re-raise after the
                    # join so the outer handler keeps the existing 1011
                    # failure semantics without leaving a sibling task.
                    task_exception = exc
                    continue
                if task is upstream_task:
                    upstream_result = result
                if isinstance(result, int):
                    close_code = _prefer_proxy_close_code(close_code, result)

            if upstream_task in done and upstream_result is None and client_task in pending:
                # Upstream EOF can race with several client frames already
                # queued by the browser. Drain them for one bounded second;
                # this preserves policy decisions without keeping a dead
                # upstream connection alive indefinitely.
                drain_deadline = time.monotonic() + 1.0
                while not client_task.done():
                    remaining = drain_deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    if self.client_message_seen.is_set() and not self.client_message_done.is_set():
                        try:
                            await asyncio.wait_for(
                                asyncio.shield(self.client_message_done.wait()),
                                timeout=remaining,
                            )
                        except asyncio.TimeoutError:
                            close_code = 1008
                            log.warning(
                                "ALAS_WS_CLOSE connection=%s event=policy reason=evaluation_timeout permission=restricted task=none",
                                self.connection_id,
                            )
                            break
                        continue
                    self.client_message_seen.clear()
                    try:
                        await asyncio.wait_for(
                            asyncio.shield(self.client_message_seen.wait()),
                            timeout=remaining,
                        )
                    except asyncio.TimeoutError:
                        break
                if client_task.done() and not client_task.cancelled():
                    try:
                        result = client_task.result()
                    except asyncio.CancelledError:
                        pass
                    except Exception as exc:
                        task_exception = task_exception or exc
                    else:
                        if isinstance(result, int):
                            close_code = _prefer_proxy_close_code(close_code, result)
        finally:
            cleanup_cancelled = await _cancel_and_join_proxy_tasks(tasks, self.connection_id)
            completed_close_code = None
            completed_normally = False
            for task in tasks:
                if task.done() and not task.cancelled():
                    try:
                        result = task.result()
                    except BaseException:
                        continue
                    completed_normally = True
                    if isinstance(result, int):
                        completed_close_code = _prefer_proxy_close_code(
                            completed_close_code if completed_close_code is not None else 1000,
                            result,
                        )
            if completed_close_code is not None:
                close_code = _prefer_proxy_close_code(close_code, completed_close_code)
            if (wait_cancelled or cleanup_cancelled) and not completed_normally:
                await _close_upstream_safely(self.upstream, code=1000)
                await _close_websocket_safely(self.websocket, 1000)
                raise asyncio.CancelledError
        if task_exception is not None:
            raise task_exception
        await _close_upstream_safely(self.upstream, code=close_code)
        await _close_websocket_safely(self.websocket, close_code)
        log.debug(
            "ALAS_WS_CLOSE connection=%s event=close reason=completed permission=none task=none",
            self.connection_id,
        )
        return close_code

    async def _run_pump(self, pump) -> int | None:
        """Treat peer departure as lifecycle completion, not an upstream incident."""
        try:
            return await pump()
        except WebSocketDisconnect:
            # ASGI may discover a departed browser while sending, before receive()
            # has delivered websocket.disconnect (including network loss / 1006).
            return 1000
        except ConnectionClosedOK:
            return 1000
        except RuntimeError as exc:
            closed = (
                getattr(self.websocket, "application_state", None) is WebSocketState.DISCONNECTED
                or getattr(self.websocket, "client_state", None) is WebSocketState.DISCONNECTED
            )
            if closed and str(exc) in {
                'Cannot call "send" once a close message has been sent.',
                'Cannot call "receive" once a disconnect message has been received.',
            }:
                return 1000
            raise


async def proxy_websocket(
    websocket,
    base_url: str,
    path: str,
    decision: ProxyDecision,
    *,
    role: str = "",
    connection_id: str = "",
    authorization_check=None,
    session_check=None,
    audit_callback=None,
) -> None:
    """双向转发 ScrcpyGate 客户端与 ALAS Runtime 的 WebSocket 消息。"""
    connection_id = _safe_connection_id(connection_id) or uuid.uuid4().hex[:12]
    audit = _WsAuditEmitter(audit_callback, connection_id)

    try:
        target = websocket_target_url(base_url, path, bound_config_query_items(websocket.query_params, decision))
        validated_target = validate_outbound_url(target, frozenset({"ws", "wss"}))
    except (OutboundTargetError, ValueError):
        log.warning(
            "ALAS_WS_CLOSE connection=%s event=connect reason=invalid_target permission=none task=none",
            connection_id,
        )
        await audit.emit(
            "alas_embed_ws_failed",
            outcome="failure",
            reason="invalid_target",
            severity="error",
            event="connect",
        )
        await websocket.close(code=1011)
        return

    policy = (
        PyWebIOSessionPolicy(
            decision.config_name,
            can_run=getattr(decision, "can_run", True),
            can_edit=getattr(decision, "can_edit", True),
        )
        if decision.filtered
        else None
    )
    authorization_lock = asyncio.Lock()
    phase = "accept"
    try:
        await websocket.accept()
        log.debug("ALAS_WS_OPEN connection=%s event=connect reason=accepted permission=none task=none", connection_id)
        phase = "connect"
        connect_kwargs = _websocket_connect_kwargs(validated_target)
        async with websocket_connect(target, **connect_kwargs) as upstream:
            phase = "transfer"
            session = _WebSocketProxySession(
                websocket=websocket,
                upstream=upstream,
                decision=decision,
                policy=policy,
                connection_id=connection_id,
                audit=audit,
                session_check=session_check,
                authorization_check=authorization_check,
                authorization_lock=authorization_lock,
            )
            await session.supervise()
    except (WebSocketDisconnect, ConnectionClosedOK):
        log.debug("ALAS_WS_CLOSE connection=%s event=close reason=peer_departed", connection_id)
        await _close_websocket_safely(websocket, 1000)
    except Exception as exc:
        reason, diagnostics = _websocket_failure(exc, phase)
        log.warning(
            "ALAS_WS_CLOSE connection=%s event=exception reason=%s phase=%s exception_type=%s",
            connection_id,
            reason,
            phase,
            type(exc).__name__,
        )
        await audit.emit(
            "alas_embed_ws_failed",
            outcome="failure",
            reason=reason,
            severity="error",
            event="exception",
            diagnostics=diagnostics,
        )
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


def _pop_header_case_insensitive(headers: dict, header_name: str) -> None:
    lowered_name = header_name.lower()
    for key in list(headers.keys()):
        if str(key).lower() == lowered_name:
            headers.pop(key, None)


class UpstreamDecompressedTooLarge(Exception):
    """Decompressed upstream body exceeds the configured output limit."""


def _pump_decompressor(decompressor: zlib.decompressobj, data: bytes, budget: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while True:
        out = decompressor.decompress(data, budget - size + 1)
        size += len(out)
        if size > budget:
            raise UpstreamDecompressedTooLarge
        chunks.append(out)
        data = decompressor.unconsumed_tail
        if not data:
            break
    tail = decompressor.flush()
    if size + len(tail) > budget:
        raise UpstreamDecompressedTooLarge
    chunks.append(tail)
    return b"".join(chunks)


def _bounded_gzip_decompress(raw: bytes) -> bytes:
    chunks: list[bytes] = []
    size = 0
    data = raw
    while data:
        decompressor = zlib.decompressobj(31)
        out = _pump_decompressor(decompressor, data, ALAS_UPSTREAM_DECOMPRESSED_MAX_BYTES - size)
        size += len(out)
        chunks.append(out)
        # gzip 允许多成员串联，成员间以 NUL 填充对齐。
        data = decompressor.unused_data.lstrip(b"\x00")
    return b"".join(chunks)


def _bounded_deflate_decompress(raw: bytes) -> bytes:
    try:
        return _pump_decompressor(zlib.decompressobj(15), raw, ALAS_UPSTREAM_DECOMPRESSED_MAX_BYTES)
    except zlib.error:
        return _pump_decompressor(zlib.decompressobj(-zlib.MAX_WBITS), raw, ALAS_UPSTREAM_DECOMPRESSED_MAX_BYTES)


def _decode_content_encoding(raw: bytes, content_encoding: str) -> bytes:
    """Decode upstream response encodings before filtered HTML/JSON leaves the proxy."""
    decoded = raw
    encodings = [
        item.strip().lower()
        for item in str(content_encoding or "").split(",")
        if item.strip()
    ]
    for encoding in reversed(encodings):
        if encoding in {"identity", "none"}:
            continue
        if encoding in {"gzip", "x-gzip"}:
            decoded = _bounded_gzip_decompress(decoded)
            continue
        if encoding == "deflate":
            decoded = _bounded_deflate_decompress(decoded)
            continue
        raise ValueError(f"unsupported upstream content encoding: {encoding}")
    return decoded


async def read_limited_request_body(
    request: FastAPIRequest,
    limit: int | None = None,
) -> bytes:
    """Read an ALAS proxy request without buffering beyond its hard limit."""
    effective_limit = ALAS_PROXY_REQUEST_MAX_BYTES if limit is None else max(0, int(limit))
    try:
        return await read_request_body_limited(
            request,
            effective_limit,
            idle_timeout=REQUEST_BODY_IDLE_TIMEOUT_SECONDS,
        )
    except RequestBodyTooLarge as exc:
        raise HTTPException(status_code=413, detail=ALAS_REQUEST_BODY_TOO_LARGE_DETAIL) from exc
    except RequestBodyIdleTimeout as exc:
        raise HTTPException(status_code=408, detail="ALAS proxy request body idle timeout") from exc


def parse_limited_body(
    body: bytes,
    content_type: str,
    limit: int = ALAS_PROXY_REQUEST_MAX_BYTES,
):
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


UpstreamResponseTooLarge = ResponseBodyTooLarge


def _read_limited_upstream_body(response, limit: int) -> bytes:
    try:
        return read_bounded_response(response, limit, chunk_size=UPSTREAM_READ_CHUNK_BYTES)
    except ResponseBodyTooLarge as exc:
        raise UpstreamResponseTooLarge from exc


def _read_upstream_response(
    opener,
    request: Request,
    timeout: float,
    limit: int | None = None,
):
    """Perform the blocking urllib request and consume a bounded response."""
    effective_limit = ALAS_UPSTREAM_RESPONSE_MAX_BYTES if limit is None else max(0, int(limit))
    try:
        with opener.open(request, timeout=timeout) as response:
            return (
                _read_limited_upstream_body(response, effective_limit),
                response.getcode(),
                response.headers,
            )
    except HTTPError as exc:
        try:
            return _read_limited_upstream_body(exc, effective_limit), exc.code, exc.headers
        finally:
            try:
                exc.close()
            except Exception:
                # A broken cleanup object must not mask the bounded response result.
                pass


async def proxy_http_request(request: FastAPIRequest, base_url: str, path: str, decision: ProxyDecision, body: bytes | None = None) -> Response:
    """转发 HTTP 请求到 ALAS Runtime，并按权限策略过滤 HTML 响应。"""
    if request.headers.get("upgrade") or "upgrade" in request.headers.get("connection", "").lower():
        raise HTTPException(status_code=501, detail=i18n.translate("server.proxy.websocket_not_implemented"))
    method = request.method.upper()
    if body is None:
        body = await read_limited_request_body(request)
    data = None if method in ("GET", "HEAD") else body
    try:
        target = build_upstream_url(base_url, path, bound_config_query_items(request.query_params, decision))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=i18n.translate("server.proxy.invalid_request")) from exc
    req = Request(
        target,
        data=data,
        headers=_proxy_request_headers(request.headers),
        method=method,
    )
    try:
        opener, _target = build_outbound_opener(target, build_opener)
        raw, status, upstream_headers = await asyncio.to_thread(_read_upstream_response, opener, req, 15.0)
        out_headers = _proxy_response_headers(upstream_headers, target, decision)
    except OutboundTargetError as exc:
        raise HTTPException(status_code=400, detail=i18n.translate("server.proxy.invalid_request")) from exc
    except UpstreamResponseTooLarge as exc:
        raise HTTPException(status_code=502, detail=UPSTREAM_RESPONSE_TOO_LARGE_DETAIL) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise HTTPException(status_code=502, detail=i18n.translate("server.proxy.runtime_unreachable")) from exc

    content_type = ""
    content_encoding = ""
    for key, value in out_headers.items():
        lowered = key.lower()
        if lowered == "content-type":
            content_type = value
        if lowered == "content-encoding":
            content_encoding = value
    media_type = _content_type_media_type(content_type)
    is_html = media_type in HTML_CONTENT_TYPES
    should_filter = decision.filtered and is_html
    # 管理员(无绑定过滤)也要把 HTML 内资源 URL 改写为绝对代理路径,
    # 但不做可见文本脱敏, 保证管理员能看到真实的设备地址等信息。
    should_rewrite_only = (not decision.filtered) and decision.allowed and is_html
    if (decision.filtered or should_rewrite_only) and content_encoding and media_type in (*HTML_CONTENT_TYPES, "application/json"):
        try:
            raw = _decode_content_encoding(raw, content_encoding)
        except UpstreamDecompressedTooLarge as exc:
            raise HTTPException(status_code=502, detail=UPSTREAM_RESPONSE_TOO_LARGE_DETAIL) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=i18n.translate("server.proxy.encoded_response_unfilterable")) from exc
        _pop_header_case_insensitive(out_headers, "Content-Encoding")
        content_encoding = ""
    if should_filter:
        charset = _content_type_charset(content_type)
        text = raw.decode(charset, errors="replace")
        raw = filter_user_html(
            text,
            decision.config_name,
            decision.device_context,
            path,
            decision.gateway_context,
            hidden_matches=decision.hidden_matches,
        ).encode(charset, errors="xmlcharrefreplace")
        out_headers["Content-Type"] = f"{media_type}; charset={charset}"
    elif should_rewrite_only:
        charset = _content_type_charset(content_type)
        text = raw.decode(charset, errors="replace")
        text = rewrite_bound_html_urls(
            text,
            decision.config_name,
            decision.device_context,
            path,
            decision.gateway_context,
        )
        text = inject_feature_gate_script(text, decision.hidden_matches)
        raw = inject_alas_ready_script(text).encode(charset, errors="xmlcharrefreplace")
        out_headers["Content-Type"] = f"{media_type}; charset={charset}"
    elif decision.filtered and media_type == "application/json":
        charset = _content_type_charset(content_type)
        try:
            payload = json.loads(raw.decode(charset, errors="replace"))
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=i18n.translate("server.proxy.encoded_response_unfilterable"),
            ) from exc
        if not isinstance(payload, (dict, list)):
            raise HTTPException(
                status_code=502,
                detail=i18n.translate("server.proxy.encoded_response_unfilterable"),
            )
        original_is_dict = isinstance(payload, dict)
        try:
            # 与 WebSocket 下游同一套规则（filter_user_json_response 内部复用帧过滤）：
            # 命中管理/受限入口/其它配置时给出空对象/空数组，而不是原样下发。
            filtered_payload = filter_user_json_response(payload, decision.config_name)
        except PayloadBudgetExceeded as exc:
            raise HTTPException(
                status_code=502,
                detail=i18n.translate("server.proxy.encoded_response_unfilterable"),
            ) from exc
        payload = filtered_payload if filtered_payload is not None else ({} if original_is_dict else [])
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(charset, errors="xmlcharrefreplace")
        out_headers["Content-Type"] = f"application/json; charset={charset}"
    out_headers.pop("X-Frame-Options", None)
    out_headers["Content-Security-Policy"] = "frame-ancestors 'self'"
    return Response(content=raw, status_code=status, headers=out_headers)

__all__ = [
    "runtime_url_candidates", "probe_runtime_url", "resolve_base_url",
    "build_upstream_url", "websocket_target_url", "proxy_http_request",
    "proxy_websocket", "read_limited_request_body", "parse_limited_body",
    "static_proxy_cache_get", "static_proxy_cache_put",
]
