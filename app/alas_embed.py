#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import asyncio
import gzip
import hashlib
import html as html_utils
import ipaddress
import json
import logging
import re
import uuid
import zlib
from dataclasses import dataclass
from enum import Enum
from html import escape
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, unquote, urlencode, urljoin, urlparse, urlunparse
from urllib.request import Request, build_opener, ProxyHandler

from fastapi import HTTPException, Request as FastAPIRequest
from fastapi.responses import Response
from websockets import connect as websocket_connect

log = logging.getLogger("webscrcpy.alas_embed")

ALAS_EMBED_PREFIX = "/alas/embed"
ALAS_DEFAULT_PORT = 22267
DOMAIN_FALLBACK_PORTS = (80, 443, 22267)
MANAGEMENT_MARKERS = ("管理", "admin", "manage", "management", "config_list", "alas.config_list", "settings.admin")
MANAGEMENT_MESSAGE_KEYS = ("event", "command", "method", "action", "path", "topic", "type", "op", "api", "route")
CONFIG_QUERY_KEYS = ("config", "name", "config_name")
CONFIG_LIST_KEYS = ("configs", "config_list", "configlist", "config_names")
CONFIG_OPTION_KEYS = ("label", "value", "name", "title", "text", "caption", "key", "id")
SAFE_NON_CONFIG_OPTION_TEXTS = (
    "home",
    "homepage",
    "主页",
    "首页",
    "start",
    "stop",
    "pause",
    "resume",
    "restart",
    "status",
    "state",
    "overview",
    "dashboard",
    "summary",
    "info",
    "list",
    "get",
    "query",
    "normal",
    "campaign",
    "task",
    "data",
    "items",
    "children",
    "options",
    "spec",
    "content",
    "command",
    "scope",
    "type",
    "result",
    "ok",
    "success",
    "message",
    "error",
    "errors",
    "back",
    "cancel",
    "close",
    "启动",
    "停止",
    "暂停",
    "继续",
    "重启",
    "状态",
    "总览",
    "任务",
    "任务总览",
    "普通",
    "出击",
    "返回",
    "取消",
    "关闭",
)
RESTRICTED_USER_ENTRY_DENIED_REASON = "restricted user entry denied"
RESTRICTED_USER_ENTRY_LABELS = (
    "配置",
    "配置列表",
    "config",
    "configs",
    "configlist",
    "管理",
    "admin",
    "manage",
    "management",
    "更新器",
    "检查更新",
    "update",
    "updater",
    "checkupdate",
    "upgrade",
    "远程控制",
    "remote",
    "remotecontrol",
    "工具",
    "tool",
    "tools",
    "toolbox",
)
RESTRICTED_USER_ENTRY_ROUTE_MARKERS = (
    "configlist",
    "admin",
    "manage",
    "management",
    "updater",
    "checkupdate",
    "selfupdate",
    "remotecontrol",
    "toolbox",
)
RESTRICTED_USER_ENTRY_KEYS = (
    "name",
    "label",
    "title",
    "text",
    "caption",
    "menu",
    "category",
    "section",
    "module",
    "page",
    "route",
    "path",
    "scope",
    "tab",
    "value",
    "key",
    "id",
    "href",
    "url",
    "onclick",
    "data",
    "command",
    "action",
    "event",
    "method",
)
ALAS_SETTINGS_DENIED_REASON = "alas settings denied"
ALAS_SETTINGS_CONTEXT_KEYS = ("menu", "category", "section", "task", "module", "page", "route", "path", "scope")
ALAS_SETTINGS_FIELD_KEYS = ("key", "setting", "field", "argument", "option", "name")
ALAS_SETTINGS_FIELD_MARKERS = (
    "alas.emulator",
    "alas.restartemulator",
    "alas.optimization",
    "alas.droprecord",
    "emulator.serial",
)
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
SENSITIVE_DEVICE_ENDPOINT_PLACEHOLDER = "已隐藏"
ADB_ENDPOINT_RE = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}:\d{2,5}(?![\w.])")
ALAS_SETTINGS_MENU_KEYS = ("name", "label", "title", "text", "menu", "category", "section", "module")
ALAS_SETTINGS_PAGE_KEYS = ("page", "route", "path", "scope", "type", "tab")
ALAS_SETTINGS_TASK_KEYS = ("task", "tasks", "children", "items", "options", "pages", "tabs")
ALAS_SETTINGS_LABEL_KEYS = ("label", "title", "text", "caption", "aria-label", "placeholder")
ALAS_SETTINGS_ROUTE_KEYS = ("value", "key", "id", "href", "url", "onclick", "data", "command", "action")
UPDATE_NOTICE_TEXT_KEYS = ("label", "title", "text", "caption", "message", "content", "toast", "notification")
UPDATE_NOTICE_ROUTE_KEYS = ("value", "key", "id", "href", "url", "onclick", "data", "command", "action", "event", "method", "route", "path")
PYWEBIO_EVENTS = frozenset({"callback", "from_submit", "from_cancel", "input_event", "js_yield"})
PYWEBIO_YIELD_COMMANDS = frozenset({"pin_value", "pin_wait", "run_script"})
PYWEBIO_LOG_COMMANDS = frozenset(
    {"output", "input_group", "pin_onchange", "pin_value", "pin_wait", "run_script", "toast"}
)
PYWEBIO_CALLBACK_ID_KEYS = frozenset({"callback_id", "click_callback_id"})
PYWEBIO_INPUT_EVENTS = frozenset({"change", "blur"})
PYWEBIO_CONFIG_FIELDS = frozenset({"config", "config_name"})
_UNPARSED_WEBSOCKET_PAYLOAD = object()
PYWEBIO_RESTRICTED_FIELD_PREFIXES = (
    "daemon_",
    "opsidaemon_",
    "eventstory_",
    "benchmark_",
    "azurlaneuncensored_",
    "gamemanager_",
)


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


class WebSocketMessageAction(str, Enum):
    """Action taken for one client-to-ALAS WebSocket message."""

    FORWARD = "forward"
    DROP = "drop"
    CLOSE = "close"


@dataclass(frozen=True)
class WebSocketMessageDecision:
    """Structured result for one client-to-ALAS WebSocket message."""

    action: WebSocketMessageAction
    reason: str
    event: str = ""
    permission: str = ""
    task_ref: str = ""

    @property
    def allowed(self) -> bool:
        """Compatibility view for callers that only distinguish forwarding."""
        return self.action is WebSocketMessageAction.FORWARD

    @property
    def closes_connection(self) -> bool:
        return self.action is WebSocketMessageAction.CLOSE


@dataclass(frozen=True)
class CallbackCapability:
    """Permissions attached to a callback identifier emitted by PyWebIO."""

    restricted: bool = False
    requires_run: bool = False
    requires_edit: bool = False

    def merged(self, other: "CallbackCapability") -> "CallbackCapability":
        return CallbackCapability(
            restricted=self.restricted or other.restricted,
            requires_run=self.requires_run or other.requires_run,
            requires_edit=self.requires_edit or other.requires_edit,
        )

    @property
    def category(self) -> str:
        if self.restricted:
            return "restricted"
        if self.requires_run and self.requires_edit:
            return "run+edit"
        if self.requires_edit:
            return "edit"
        if self.requires_run:
            return "run"
        return "navigation"


@dataclass(frozen=True)
class PyWebIOTaskRegistration:
    """Server-issued PyWebIO task that the browser may answer."""

    kind: str
    fields: frozenset[str] = frozenset()
    restricted_fields: frozenset[str] = frozenset()
    restricted: bool = False


@dataclass(frozen=True)
class DownstreamObservation:
    """Metadata-only result of observing one ALAS-to-browser message."""

    forwarded: bool
    reason: str
    command: str = ""
    allowed_callbacks: int = 0
    denied_callbacks: int = 0
    allowed_tasks: int = 0
    denied_tasks: int = 0


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
  <meta name="theme-color" content="#111315">
  <title>{safe_title}</title>
  <script src="/static/js/theme-init.js?v=fd7b6ddc6cde"></script>
  <link rel="stylesheet" href="/static/css/ui-tokens.css?v=23c66068705b">
  <link rel="stylesheet" href="/static/css/alas-shell.css?v=8310dfe3dbdc">
  <script src="/static/js/alas-shell.js?v=3941af81f7e0" defer></script>
</head>
<body>
  <header class="alas-shell-bar">
    <div class="alas-shell-identity">
      <span class="alas-shell-brand" aria-hidden="true">SG</span>
      <div class="alas-shell-title-wrap">
        <strong class="alas-shell-title">{safe_title}</strong>
        <span class="alas-shell-message">{safe_message}</span>
      </div>
    </div>
    <div class="alas-shell-toolbar" aria-label="ALAS 页面操作">
      <span id="loadStatus" class="alas-shell-status" data-state="loading" role="status" aria-live="polite">正在连接</span>
      <button id="refreshFrame" class="alas-shell-button" type="button">刷新</button>
      <a class="alas-shell-button" href="{safe_src}" target="_blank" rel="noopener noreferrer">新窗口打开</a>
      <a class="alas-shell-button" href="/">返回</a>
    </div>
  </header>
  <main class="alas-shell-stage">
    <iframe
      id="alasFrame"
      class="alas-shell-frame"
      src="{safe_src}"
      title="{safe_title}"
      loading="eager"
      aria-busy="true"
    ></iframe>
    <section
      id="alasState"
      class="alas-shell-state"
      data-kind="loading"
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >
      <div class="alas-shell-state-card">
        <div class="alas-shell-spinner" aria-hidden="true"></div>
        <h1 id="stateTitle">正在加载 ALAS</h1>
        <p id="stateDetail">正在连接 ALAS Runtime，请稍候。</p>
        <div id="stateActions" class="alas-shell-state-actions" hidden>
          <button id="retryFrame" class="alas-shell-button primary" type="button">重试</button>
          <a class="alas-shell-button" href="/">返回 ScrcpyGate</a>
        </div>
      </div>
    </section>
    <noscript>
      <section class="alas-shell-state is-visible" role="alert">
        <div class="alas-shell-state-card">
          <h1>无法显示加载状态</h1>
          <p>请启用 JavaScript，或在新窗口中打开 ALAS 页面。</p>
          <div class="alas-shell-state-actions">
            <a class="alas-shell-button primary" href="{safe_src}" target="_blank" rel="noopener noreferrer">新窗口打开</a>
            <a class="alas-shell-button" href="/">返回 ScrcpyGate</a>
          </div>
        </div>
      </section>
    </noscript>
  </main>
</body>
</html>"""


def denied_page_html(message: str, redirect_url: str = "/alas/embed/", seconds: int = 3) -> str:
    """Render a friendly ALAS embed denial page inside the iframe."""
    safe_message = escape(message or "ALAS 访问被限制")
    safe_url = escape(redirect_url or "/alas/embed/", quote=True)
    safe_seconds = max(1, min(30, int(seconds or 3)))
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="{safe_seconds};url={safe_url}">
  <title>ALAS 访问受限</title>
  <style>
    :root {{ color-scheme: dark; }}
    html, body {{ margin:0; min-height:100%; background:#0f131a; color:#eef3fb; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
    body {{ display:grid; place-items:center; padding:24px; box-sizing:border-box; }}
    .panel {{ width:min(520px, 100%); border:1px solid #283244; background:#151b25; border-radius:8px; padding:24px; box-shadow:0 18px 60px rgba(0,0,0,.35); }}
    .badge {{ display:inline-flex; align-items:center; height:28px; padding:0 10px; border-radius:999px; background:#263247; color:#9fc3ff; font-size:13px; }}
    h1 {{ margin:18px 0 10px; font-size:24px; line-height:1.25; letter-spacing:0; }}
    p {{ margin:0; color:#aeb9c9; line-height:1.7; font-size:14px; }}
    .actions {{ display:flex; flex-wrap:wrap; gap:10px; margin-top:22px; }}
    a {{ display:inline-flex; align-items:center; justify-content:center; min-height:38px; padding:0 14px; border-radius:6px; color:#dfe9ff; text-decoration:none; background:#2563eb; }}
    a.secondary {{ background:#202938; color:#b9c6d9; }}
    .count {{ margin-top:14px; font-size:13px; color:#7f8da3; }}
  </style>
</head>
<body>
  <main class="panel">
    <span class="badge">ScrcpyGate ALAS</span>
    <h1>此入口不可访问</h1>
    <p>{safe_message}</p>
    <div class="actions">
      <a href="{safe_url}">返回我的 ALAS 页面</a>
      <a class="secondary" href="/" target="_top">返回 ScrcpyGate</a>
    </div>
    <div class="count"><span id="seconds">{safe_seconds}</span> 秒后自动返回。</div>
  </main>
  <script>
    (function() {{
      var left = {safe_seconds};
      var target = {json.dumps(redirect_url or "/alas/embed/")};
      var node = document.getElementById("seconds");
      window.setInterval(function() {{
        left -= 1;
        if (node) node.textContent = String(Math.max(left, 0));
        if (left <= 0) window.location.replace(target);
      }}, 1000);
    }})();
  </script>
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


def _iter_query_items(query_items) -> list[tuple[str, object]]:
    """Return repeated query items from Starlette QueryParams, dicts or plain pairs."""
    if hasattr(query_items, "multi_items"):
        return list(query_items.multi_items())
    if isinstance(query_items, dict):
        items = []
        for key, value in query_items.items():
            if isinstance(value, (list, tuple)):
                items.extend((key, item) for item in value)
            else:
                items.append((key, value))
        return items
    return list(query_items or [])


def bound_config_query_items(query_items, decision: ProxyDecision) -> list[tuple[str, object]]:
    """Append the bound ALAS config for filtered users when the browser omitted it."""
    params = []
    has_config = False
    for key, value in _iter_query_items(query_items):
        if str(key).lower() in CONFIG_QUERY_KEYS:
            if not str(value or "").strip():
                continue
            has_config = True
        params.append((key, value))
    if decision.filtered and decision.config_name and not has_config:
        params.append(("config", decision.config_name))
    return params


def _body_contains_management(value) -> bool:
    """递归检查 HTTP 正文是否包含管理操作标记。"""
    return _message_contains_management(value)


def _body_switches_config(value, config_name: str) -> bool:
    """递归检查 HTTP 正文是否请求非绑定配置。"""
    return _message_switches_config(value, config_name)


def _body_denied_by_action_permission(value, can_run: bool, can_edit: bool) -> bool:
    """根据 can_run/can_edit 判断 HTTP 正文是否触发被禁操作。"""
    return _message_denied_by_action_permission(value, can_run, can_edit)


def _filter_visible_html_text(html: str) -> str:
    """Redact visible static HTML text without modifying scripts or styles."""
    tokens = re.split(r"(<[^>]+>)", str(html or ""))
    filtered = []
    raw_text_element = ""
    for token in tokens:
        if not token:
            continue
        if token.startswith("<") and token.endswith(">"):
            tag = token.strip().lower()
            if tag.startswith("</script") or tag.startswith("</style"):
                filtered.append(token)
                raw_text_element = ""
                continue
            filtered.append(ADB_ENDPOINT_RE.sub(SENSITIVE_DEVICE_ENDPOINT_PLACEHOLDER, token))
            if tag.startswith("<script") and not tag.endswith("/>"):
                raw_text_element = "script"
            elif tag.startswith("<style") and not tag.endswith("/>"):
                raw_text_element = "style"
            continue
        if raw_text_element:
            filtered.append(token)
            continue
        text = ADB_ENDPOINT_RE.sub(SENSITIVE_DEVICE_ENDPOINT_PLACEHOLDER, token)
        for marker in MANAGEMENT_MARKERS:
            text = text.replace(marker, "")
        for marker in ("其它配置", "其他配置"):
            text = text.replace(marker, "")
        filtered.append(text)
    return "".join(filtered)


def _compact_text(value: object) -> str:
    """Return a case-folded text token without separators for fuzzy ALAS UI routing checks."""
    return "".join(char.lower() for char in str(value or "") if char.isalnum())


def _plain_text(value: object) -> str:
    return str(value or "").strip().lower()


def _is_alas_settings_task(value: object) -> bool:
    text = _compact_text(value)
    return text in {"alas", "alas设置", "alassettings"}


def _is_alas_settings_label(value: object) -> bool:
    text = _compact_text(value)
    return (
        text in {"alas设置", "alassettings", "alas設定"}
        or ("alas" in text and ("设置" in text or "setting" in text or "設定" in text))
    )


def _is_alas_settings_field(value: object) -> bool:
    text = _plain_text(value).replace("\\", ".").replace("/", ".")
    compact = _compact_text(text)
    if any(marker in text for marker in ALAS_SETTINGS_FIELD_MARKERS):
        return True
    return any(_compact_text(marker) in compact for marker in ALAS_SETTINGS_FIELD_MARKERS)


def _message_targets_alas_settings(value) -> bool:
    """Return True when a request explicitly targets the sensitive ALAS settings page."""
    if isinstance(value, dict):
        lowered = {str(key).lower(): item for key, item in value.items()}
        if any(
            key in lowered
            and (
                _is_alas_settings_label(lowered[key])
                or _is_alas_settings_field(lowered[key])
                or _is_alas_settings_task(lowered[key])
            )
            for key in (*ALAS_SETTINGS_CONTEXT_KEYS, *ALAS_SETTINGS_PAGE_KEYS, *ALAS_SETTINGS_ROUTE_KEYS)
        ):
            return True
        menu_like = any(
            key in lowered and _is_alas_settings_task(lowered[key])
            for key in ("menu", "category", "section", "module")
        )
        task_like = any(
            key in lowered and _is_alas_settings_task(lowered[key])
            for key in ("task", "page", "route", "path", "scope")
        )
        if menu_like and task_like:
            return True
        for key, item in lowered.items():
            if key in ALAS_SETTINGS_FIELD_KEYS and _is_alas_settings_field(item):
                return True
            if key in ALAS_SETTINGS_CONTEXT_KEYS and _is_alas_settings_field(item):
                return True
            if isinstance(item, (dict, list, tuple)) and _message_targets_alas_settings(item):
                return True
    if isinstance(value, (list, tuple)):
        return any(_message_targets_alas_settings(item) for item in value)
    return False


def _is_alas_settings_page(value: object) -> bool:
    compact = _compact_text(value)
    return compact in {"setting", "settings", "alas", "alas设置", "alassettings"}


def _collection_has_alas_settings_task(value) -> bool:
    if isinstance(value, dict):
        return _json_item_targets_alas_settings(value)
    if isinstance(value, (list, tuple)):
        return any(_collection_has_alas_settings_task(item) for item in value)
    return _is_alas_settings_task(value) or _is_alas_settings_field(value)


def _is_update_notice_text(value: object) -> bool:
    raw = str(value or "").strip().lower()
    compact = _compact_text(raw)
    if not compact:
        return False
    return (
        "有更新可用" in raw
        or "点击这里进行更新" in raw
        or "更新可用" in raw
        or "updateavailable" in compact
        or "newversionavailable" in compact
        or "upgradeavailable" in compact
    )


def _is_update_notice_action(value: object) -> bool:
    compact = _compact_text(value)
    return compact in {"update", "upgrade", "updater", "checkupdate", "selfupdate"}


def _is_restricted_user_entry_label(value: object) -> bool:
    compact = _compact_text(value)
    if not compact:
        return False
    return compact in {_compact_text(marker) for marker in RESTRICTED_USER_ENTRY_LABELS}


def _is_restricted_user_entry_route(value: object) -> bool:
    compact = _compact_text(value)
    if not compact:
        return False
    if _is_restricted_user_entry_label(value):
        return True
    return any(_compact_text(marker) in compact for marker in RESTRICTED_USER_ENTRY_ROUTE_MARKERS)


def _message_targets_restricted_user_entry(value) -> bool:
    """Return True when a request or UI payload targets a user-hidden ALAS entry."""
    if isinstance(value, dict):
        lowered = {str(key).lower(): item for key, item in value.items()}
        if any(
            key in lowered
            and (
                _is_restricted_user_entry_route(lowered[key])
                if key in RESTRICTED_USER_ENTRY_KEYS
                else False
            )
            for key in lowered
        ):
            return True
        for item in lowered.values():
            if isinstance(item, (dict, list, tuple)) and _message_targets_restricted_user_entry(item):
                return True
    if isinstance(value, (list, tuple)):
        return any(_message_targets_restricted_user_entry(item) for item in value)
    return False


def _json_item_targets_update_notice(value) -> bool:
    """Return True for downstream ALAS self-update prompts exposed to bound users."""
    if not isinstance(value, dict):
        return False
    lowered = {str(key).lower(): item for key, item in value.items()}
    if any(key in lowered and _is_update_notice_text(lowered[key]) for key in UPDATE_NOTICE_TEXT_KEYS):
        return True
    route_like = any(
        key in lowered and (_is_update_notice_action(lowered[key]) or _is_update_notice_text(lowered[key]))
        for key in UPDATE_NOTICE_ROUTE_KEYS
    )
    text_like = any(key in lowered and _is_update_notice_text(lowered[key]) for key in UPDATE_NOTICE_TEXT_KEYS)
    return route_like and text_like


def _json_item_targets_restricted_user_entry(value) -> bool:
    """Return True for downstream menu/page payloads hidden from bound users."""
    if isinstance(value, str):
        return _is_restricted_user_entry_label(value)
    if not isinstance(value, dict):
        return False
    lowered = {str(key).lower(): item for key, item in value.items()}
    return any(
        key in RESTRICTED_USER_ENTRY_KEYS and _is_restricted_user_entry_route(item)
        for key, item in lowered.items()
    )


def _downstream_config_value_is_ui_label(value) -> bool:
    """Return True when a downstream config-like value is page text, not a config name."""
    if isinstance(value, str):
        return _is_restricted_user_entry_label(value)
    return False


def _json_item_targets_alas_settings(value) -> bool:
    """Return True for downstream ALAS menu/page payloads that expose ALAS settings."""
    if not isinstance(value, dict):
        return False

    lowered = {str(key).lower(): item for key, item in value.items()}
    if any(_is_alas_settings_field(key) for key in lowered):
        return True
    if any(_is_alas_settings_label(key) for key in lowered):
        return True
    label_like = any(
        key in lowered and _is_alas_settings_label(lowered[key])
        for key in ALAS_SETTINGS_LABEL_KEYS
    )
    route_like = any(
        key in lowered
        and (
            _is_alas_settings_label(lowered[key])
            or _is_alas_settings_field(lowered[key])
            or _is_alas_settings_task(lowered[key])
        )
        for key in ALAS_SETTINGS_ROUTE_KEYS
    )
    if label_like:
        return True
    if route_like and any(
        key in lowered and (_is_alas_settings_task(lowered[key]) or _is_alas_settings_page(lowered[key]))
        for key in (*ALAS_SETTINGS_CONTEXT_KEYS, *ALAS_SETTINGS_PAGE_KEYS, *ALAS_SETTINGS_TASK_KEYS)
    ):
        return True
    name_like = any(
        key in lowered and (_is_alas_settings_task(lowered[key]) or _is_alas_settings_label(lowered[key]))
        for key in ALAS_SETTINGS_MENU_KEYS
    )
    page_like = any(
        key in lowered and _is_alas_settings_page(lowered[key])
        for key in ALAS_SETTINGS_PAGE_KEYS
    )
    task_like = any(
        key in lowered and _collection_has_alas_settings_task(lowered[key])
        for key in ALAS_SETTINGS_TASK_KEYS
    )
    if name_like and (page_like or task_like):
        return True
    return any(
        key in lowered and _is_alas_settings_field(lowered[key])
        for key in ALAS_SETTINGS_FIELD_KEYS
    )


def mask_sensitive_device_endpoints(value):
    """Mask ADB endpoint strings before they leave the ALAS embed proxy."""
    if isinstance(value, str):
        return ADB_ENDPOINT_RE.sub(SENSITIVE_DEVICE_ENDPOINT_PLACEHOLDER, value)
    if isinstance(value, dict):
        return {
            mask_sensitive_device_endpoints(key): mask_sensitive_device_endpoints(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [mask_sensitive_device_endpoints(item) for item in value]
    return value


def _query_targets_alas_settings(query: dict) -> bool:
    return _message_targets_alas_settings(query or {})


def _body_targets_alas_settings(value) -> bool:
    return _message_targets_alas_settings(value)


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

    if _is_restricted_user_entry_route(path) or _message_targets_restricted_user_entry(query or {}):
        return ProxyDecision(
            allowed=False,
            status_code=403,
            config_name=config_name,
            reason=RESTRICTED_USER_ENTRY_DENIED_REASON,
        )

    if body is not None and _body_contains_management(body):
        return ProxyDecision(
            allowed=False,
            status_code=403,
            config_name=config_name,
            reason="management path denied",
        )

    if body is not None and _message_targets_restricted_user_entry(body):
        return ProxyDecision(
            allowed=False,
            status_code=403,
            config_name=config_name,
            reason=RESTRICTED_USER_ENTRY_DENIED_REASON,
        )

    if _query_targets_alas_settings(query or {}) or (body is not None and _body_targets_alas_settings(body)):
        return ProxyDecision(
            allowed=False,
            status_code=403,
            config_name=config_name,
            reason=ALAS_SETTINGS_DENIED_REASON,
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
    """Prepare ALAS HTML for bound users without mutating application scripts."""
    filtered = _filter_visible_html_text(str(html or ""))
    if config_name and config_name not in filtered:
        escaped_config_name = html_utils.escape(config_name, quote=True)
        filtered = f"{filtered}<!-- bound ALAS config: {escaped_config_name} -->"
    filtered = inject_bound_config_script(filtered, config_name)
    return filtered


def inject_bound_config_script(html: str, config_name: str) -> str:
    """Inject a small bootstrap so PyWebIO child requests keep the bound config."""
    if not config_name or "data-scrcpygate-alas-bind" in str(html or ""):
        return str(html or "")
    config_json = (
        json.dumps(str(config_name), ensure_ascii=False)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )
    hidden_endpoint_json = (
        json.dumps(SENSITIVE_DEVICE_ENDPOINT_PLACEHOLDER, ensure_ascii=False)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )
    script = f"""
<script data-scrcpygate-alas-bind>
(function() {{
  var boundConfig = {config_json};
  var hiddenEndpointText = {hidden_endpoint_json};
  var proxyPrefix = "{ALAS_EMBED_PREFIX}/proxy";
  var configKeys = ["config", "name", "config_name"];
  var adbEndpointPattern = /(?:\\d{{1,3}}\\.){{3}}\\d{{1,3}}:\\d{{2,5}}/g;
  function hasConfig(url) {{
    return configKeys.some(function(key) {{ return url.searchParams.has(key); }});
  }}
  function shouldProxyPath(pathname) {{
    return pathname.indexOf(proxyPrefix) === 0 ||
      pathname.indexOf("/pywebio") === 0 ||
      pathname.indexOf("/api") === 0 ||
      pathname.indexOf("/ajax") === 0 ||
      pathname.indexOf("/static/") === 0 ||
      pathname.indexOf("/assets/") === 0 ||
      pathname === "/favicon.ico";
  }}
  function isSameScrcpyGateHost(url) {{
    if (url.origin === window.location.origin) return true;
    if (url.host !== window.location.host) return false;
    if (window.location.protocol === "https:" && url.protocol === "wss:") return true;
    if (window.location.protocol === "http:" && url.protocol === "ws:") return true;
    return false;
  }}
  function patchUrl(value) {{
    var raw = value && value.url ? value.url : value;
    if (typeof raw !== "string") return value;
    try {{
      var url = new URL(raw, window.location.href);
      if (!isSameScrcpyGateHost(url) || !shouldProxyPath(url.pathname)) return value;
      if (url.pathname.indexOf(proxyPrefix) !== 0) {{
        url.pathname = proxyPrefix + (url.pathname.charAt(0) === "/" ? url.pathname : "/" + url.pathname);
      }}
      if (!hasConfig(url)) url.searchParams.append("config", boundConfig);
      if (typeof Request !== "undefined" && value instanceof Request) return new Request(url.href, value);
      return url.href;
    }} catch (err) {{
      return value;
    }}
  }}
  if (window.fetch) {{
    var nativeFetch = window.fetch;
    window.fetch = function(input, init) {{ return nativeFetch.call(this, patchUrl(input), init); }};
  }}
  if (window.XMLHttpRequest) {{
    var nativeOpen = window.XMLHttpRequest.prototype.open;
    window.XMLHttpRequest.prototype.open = function(method, url) {{
      arguments[1] = patchUrl(url);
      return nativeOpen.apply(this, arguments);
    }};
  }}
  if (window.WebSocket) {{
    var NativeWebSocket = window.WebSocket;
    window.WebSocket = function(url, protocols) {{
      return protocols === undefined ? new NativeWebSocket(patchUrl(url)) : new NativeWebSocket(patchUrl(url), protocols);
    }};
    window.WebSocket.prototype = NativeWebSocket.prototype;
  }}
  if (window.EventSource) {{
    var NativeEventSource = window.EventSource;
    window.EventSource = function(url, options) {{ return new NativeEventSource(patchUrl(url), options); }};
    window.EventSource.prototype = NativeEventSource.prototype;
  }}
  function compactText(value) {{
    return String(value || "").replace(/[^0-9a-zA-Z\\u4e00-\\u9fff]+/g, "").toLowerCase();
  }}
  var actionableSelector = [
    "a", "button", "li", "[onclick]", "[tabindex]",
    "[role='button']", "[role='menuitem']", "[role='option']", "[role='radio']", "[role='tab']", "[role='treeitem']",
    ".ant-menu-item", ".ant-select-item-option", ".ant-radio-wrapper", ".el-menu-item", ".v-list-item", ".q-item", ".menu-item"
  ].join(",");
  function textTargetsAlasSettings(value) {{
    var text = compactText(value);
    return text.indexOf("alas设置") !== -1 ||
      text.indexOf("alassettings") !== -1 ||
      text.indexOf("alas設定") !== -1 ||
      (text.indexOf("alas") !== -1 && (text.indexOf("setting") !== -1 || text.indexOf("设置") !== -1));
  }}
  function textTargetsRestrictedUserEntry(value) {{
    var text = compactText(value);
    var restricted = {{
      "配置": true,
      "配置列表": true,
      "config": true,
      "configs": true,
      "configlist": true,
      "管理": true,
      "admin": true,
      "manage": true,
      "management": true,
      "更新器": true,
      "检查更新": true,
      "update": true,
      "updater": true,
      "checkupdate": true,
      "upgrade": true,
      "远程控制": true,
      "remote": true,
      "remotecontrol": true,
      "工具": true,
      "tool": true,
      "tools": true,
      "toolbox": true
    }};
    return !!restricted[text];
  }}
  function shallowElementText(element) {{
    if (!element || !element.childNodes) return "";
    var parts = [];
    for (var i = 0; i < element.childNodes.length; i += 1) {{
      var child = element.childNodes[i];
      if (child && child.nodeType === 3 && child.nodeValue) parts.push(child.nodeValue);
    }}
    return parts.join(" ");
  }}
  function collectElementSignal(element) {{
    if (!element) return "";
    var parts = [
      shallowElementText(element) || element.innerText || element.textContent || "",
      element.value || "",
      element.getAttribute && element.getAttribute("placeholder") || "",
      element.getAttribute && element.getAttribute("title") || "",
      element.getAttribute && element.getAttribute("aria-label") || "",
      element.getAttribute && element.getAttribute("data-key") || "",
      element.getAttribute && element.getAttribute("data-value") || "",
      element.getAttribute && element.getAttribute("data-name") || "",
      element.getAttribute && element.getAttribute("href") || "",
      element.getAttribute && element.getAttribute("onclick") || "",
      element.id || "",
      typeof element.className === "string" ? element.className : ""
    ];
    return parts.join(" ");
  }}
  function elementFromEvent(event) {{
    var target = event && event.target;
    if (!target) return null;
    return target.nodeType === 1 ? target : target.parentElement;
  }}
  function closestActionable(element) {{
    if (!element) return null;
    try {{
      return element.closest ? element.closest(actionableSelector) : null;
    }} catch (err) {{
      return null;
    }}
  }}
  function isCompactActionable(element) {{
    if (!element || !element.getBoundingClientRect) return false;
    var rect = element.getBoundingClientRect();
    if (!rect || rect.width <= 0 || rect.height <= 0) return true;
    return rect.width <= Math.max(280, window.innerWidth * 0.45) && rect.height <= 140;
  }}
  function sanitizeSensitiveValue(value) {{
    if (typeof value !== "string") return value;
    if (!adbEndpointPattern.test(value)) {{
      adbEndpointPattern.lastIndex = 0;
      return value;
    }}
    adbEndpointPattern.lastIndex = 0;
    var sanitized = value.replace(adbEndpointPattern, hiddenEndpointText);
    adbEndpointPattern.lastIndex = 0;
    return sanitized;
  }}
  function maskTextNode(node) {{
    if (!node || !node.nodeValue) return;
    var sanitized = sanitizeSensitiveValue(node.nodeValue);
    if (sanitized !== node.nodeValue) node.nodeValue = sanitized;
  }}
  function maskSensitiveField(field) {{
    if (!field || !("value" in field) || typeof field.value !== "string") return;
    var sanitized = sanitizeSensitiveValue(field.value);
    if (sanitized !== field.value) field.value = sanitized;
  }}
  function installSensitiveValueGuard(prototype) {{
    if (!prototype || !Object.getOwnPropertyDescriptor || !Object.defineProperty) return;
    var descriptor = Object.getOwnPropertyDescriptor(prototype, "value");
    if (!descriptor || !descriptor.get || !descriptor.set || descriptor.set.scrcpyGateGuard) return;
    var guardedSetter = function(value) {{
      descriptor.set.call(this, sanitizeSensitiveValue(value));
    }};
    guardedSetter.scrcpyGateGuard = true;
    try {{
      Object.defineProperty(prototype, "value", {{
        configurable: descriptor.configurable,
        enumerable: descriptor.enumerable,
        get: descriptor.get,
        set: guardedSetter
      }});
    }} catch (err) {{
      // Input/change listeners and mutation cleanup remain as fallbacks.
    }}
  }}
  function maskSensitiveText(root) {{
    if (!root) return;
    if (root.nodeType === 3) {{
      maskTextNode(root);
      return;
    }}
    if (root.matches && root.matches("input,textarea")) maskSensitiveField(root);
    if (document.createTreeWalker) {{
      var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      var textNode = walker.nextNode();
      while (textNode) {{
        maskTextNode(textNode);
        textNode = walker.nextNode();
      }}
    }}
    if (!root.querySelectorAll) return;
    var fields = root.querySelectorAll("input,textarea");
    for (var i = 0; i < fields.length; i += 1) maskSensitiveField(fields[i]);
  }}
  function blocksSensitiveEvent(event) {{
    var item = closestActionable(elementFromEvent(event));
    if (!item || !isCompactActionable(item)) return false;
    var signal = collectElementSignal(item);
    return textTargetsAlasSettings(signal) ||
      textTargetsRestrictedUserEntry(signal);
  }}
  document.addEventListener("click", function(event) {{
    if (!blocksSensitiveEvent(event)) return;
    event.preventDefault();
    event.stopImmediatePropagation();
  }}, true);
  document.addEventListener("touchstart", function(event) {{
    if (!blocksSensitiveEvent(event)) return;
    event.preventDefault();
    event.stopImmediatePropagation();
  }}, true);
  function runScrcpyGateFilters(root) {{
    maskSensitiveText(root || document.body || document.documentElement);
  }}
  installSensitiveValueGuard(window.HTMLInputElement && window.HTMLInputElement.prototype);
  installSensitiveValueGuard(window.HTMLTextAreaElement && window.HTMLTextAreaElement.prototype);
  runScrcpyGateFilters(document.body || document.documentElement);
  if (window.MutationObserver && document.documentElement) {{
    new MutationObserver(function(mutations) {{
      for (var i = 0; i < mutations.length; i += 1) {{
        var mutation = mutations[i];
        if (mutation.type === "characterData") {{
          maskTextNode(mutation.target);
          continue;
        }}
        if (mutation.type === "attributes") {{
          maskSensitiveField(mutation.target);
          continue;
        }}
        for (var j = 0; j < mutation.addedNodes.length; j += 1) {{
          runScrcpyGateFilters(mutation.addedNodes[j]);
        }}
      }}
    }}).observe(document.documentElement, {{
      childList:true,
      subtree:true,
      characterData:true,
      attributes:true,
      attributeFilter:["value"]
    }});
  }}
  document.addEventListener("input", function(event) {{ maskSensitiveField(event.target); }}, true);
  document.addEventListener("change", function(event) {{ maskSensitiveField(event.target); }}, true);
}})();
</script>"""
    original = str(html or "")
    body_index = original.lower().rfind("</body>")
    if body_index >= 0:
        return f"{original[:body_index]}{script}{original[body_index:]}"
    return f"{original}{script}"


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


def _append_config_to_embed_url(value: str, config_name: str) -> str:
    if not config_name:
        return value
    parsed = urlparse(str(value or ""))
    if not parsed.path.startswith(f"{ALAS_EMBED_PREFIX}/proxy"):
        return value
    query = parse_qs(parsed.query, keep_blank_values=True)
    if any(key in query and any(str(item or "").strip() for item in query[key]) for key in CONFIG_QUERY_KEYS):
        return value
    pairs = []
    for key, values in query.items():
        if values:
            pairs.extend((key, item) for item in values)
        else:
            pairs.append((key, ""))
    pairs.append(("config", config_name))
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
                value = _append_config_to_embed_url(str(value), decision.config_name)
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


def _text_targets_restricted_user_entry(text: str) -> bool:
    lowered_text = str(text or "").lower()
    plain_markers = ("/config", "/configs", "/updater", "/remote", "/tool", "route=config", "route=tool")
    return any(marker in lowered_text for marker in plain_markers)


def _text_targets_alas_settings_ui(text: str) -> bool:
    return _is_alas_settings_label(text) or _is_alas_settings_field(text)


def websocket_message_decision(
    message: str | bytes,
    config_name: str,
    can_run: bool = True,
    can_edit: bool = True,
) -> WebSocketMessageDecision:
    """Apply the stateless policy used by non-PyWebIO compatibility messages."""
    payload = _parse_websocket_message(message)
    if payload is None:
        if isinstance(message, bytes):
            return WebSocketMessageDecision(WebSocketMessageAction.CLOSE, "invalid_binary")
        text = str(message or "")
        if text.lstrip().startswith(("{", "[")):
            return WebSocketMessageDecision(WebSocketMessageAction.CLOSE, "invalid_json")
        if _text_contains_management_command(text):
            return WebSocketMessageDecision(WebSocketMessageAction.CLOSE, "management_denied")
        if _text_targets_restricted_user_entry(text):
            return WebSocketMessageDecision(WebSocketMessageAction.CLOSE, "restricted_entry_denied")
        if _text_targets_alas_settings_ui(text):
            return WebSocketMessageDecision(WebSocketMessageAction.CLOSE, "alas_settings_denied")
        if not can_run and _text_has_action_marker(text, RUN_ACTION_MARKERS):
            return WebSocketMessageDecision(
                WebSocketMessageAction.CLOSE,
                "run_permission_denied",
                permission="run",
            )
        if not can_edit and _text_has_action_marker(text, EDIT_ACTION_MARKERS):
            return WebSocketMessageDecision(
                WebSocketMessageAction.CLOSE,
                "edit_permission_denied",
                permission="edit",
            )
        return WebSocketMessageDecision(WebSocketMessageAction.FORWARD, "plain_text_allowed")
    if isinstance(payload, dict) and str(payload.get("event") or "").strip().lower() in PYWEBIO_EVENTS:
        event = str(payload.get("event") or "").strip().lower()
        return WebSocketMessageDecision(
            WebSocketMessageAction.CLOSE,
            "protocol_state_required",
            event=event,
        )
    if _message_contains_management(payload):
        return WebSocketMessageDecision(WebSocketMessageAction.CLOSE, "management_denied")
    if _message_targets_restricted_user_entry(payload):
        return WebSocketMessageDecision(WebSocketMessageAction.CLOSE, "restricted_entry_denied")
    if _message_targets_alas_settings(payload):
        return WebSocketMessageDecision(WebSocketMessageAction.CLOSE, "alas_settings_denied")
    if _message_switches_config(payload, config_name):
        return WebSocketMessageDecision(WebSocketMessageAction.CLOSE, "config_mismatch")
    if not can_run and _message_contains_action(payload, RUN_ACTION_MARKERS):
        return WebSocketMessageDecision(
            WebSocketMessageAction.CLOSE,
            "run_permission_denied",
            permission="run",
        )
    if not can_edit and _message_contains_action(payload, EDIT_ACTION_MARKERS):
        return WebSocketMessageDecision(
            WebSocketMessageAction.CLOSE,
            "edit_permission_denied",
            permission="edit",
        )
    return WebSocketMessageDecision(WebSocketMessageAction.FORWARD, "structured_message_allowed")


def websocket_message_allowed(message: str | bytes, config_name: str, can_run: bool = True, can_edit: bool = True) -> bool:
    """Compatibility boolean wrapper around :func:`websocket_message_decision`."""
    return websocket_message_decision(message, config_name, can_run=can_run, can_edit=can_edit).allowed


def _is_config_list_key(key: object) -> bool:
    normalized = "".join(char.lower() for char in str(key or "") if char.isalnum() or char == "_")
    return normalized in CONFIG_LIST_KEYS


def _dict_has_bound_config_key(value: dict, config_name: str) -> bool:
    return bool(config_name and any(str(key or "").strip() == config_name for key in value))


def _dict_key_is_other_config_choice(key: object, config_name: str, has_bound_config_key: bool) -> bool:
    if not has_bound_config_key:
        return False
    raw = str(key or "").strip()
    if not raw or raw == config_name:
        return False
    if _looks_like_config_choice_text(raw, config_name):
        return True
    return not _json_item_is_safe_non_config_option(raw)


def _json_item_matches_bound_config(value, config_name: str) -> bool:
    if isinstance(value, dict):
        for key in CONFIG_QUERY_KEYS:
            if key in value and str(value.get(key) or "").strip():
                return not _config_value_mismatches(value.get(key), config_name)
        return not _message_switches_config(value, config_name)
    if isinstance(value, (list, tuple)):
        return not _config_value_mismatches(value, config_name)
    requested_config = str(value or "").strip()
    return not requested_config or requested_config == config_name


def _json_item_is_config_option(value, config_name: str) -> bool:
    """Return True for select/menu items that look like ALAS config choices."""
    if isinstance(value, str):
        return _looks_like_config_choice_text(value, config_name)
    if not isinstance(value, dict) or not config_name:
        return False
    option_values = []
    for key, item in value.items():
        normalized_key = "".join(char.lower() for char in str(key or "") if char.isalnum() or char == "_")
        if normalized_key in CONFIG_OPTION_KEYS and isinstance(item, (str, int, float)):
            option_values.append(str(item).strip())
    if not option_values:
        return False
    return any(_looks_like_config_choice_text(item, config_name) for item in option_values)


def _json_item_option_texts(value) -> list[str]:
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    if isinstance(value, (int, float)):
        return [str(value)]
    if not isinstance(value, dict):
        return []
    texts = []
    for key, item in value.items():
        normalized_key = "".join(char.lower() for char in str(key or "") if char.isalnum() or char == "_")
        if normalized_key in CONFIG_OPTION_KEYS and isinstance(item, (str, int, float)):
            text = str(item).strip()
            if text:
                texts.append(text)
    return texts


def _json_item_is_option_shaped(value) -> bool:
    return bool(_json_item_option_texts(value))


def _json_item_is_safe_non_config_option(value) -> bool:
    texts = _json_item_option_texts(value)
    if not texts:
        return False
    safe_markers = {_compact_text(item) for item in SAFE_NON_CONFIG_OPTION_TEXTS}
    for text in texts:
        compact = _compact_text(text)
        if not compact:
            continue
        if compact in safe_markers:
            return True
        if len(compact) <= 3 and not compact.isdigit():
            return True
        if "-" in str(text) and len(compact) <= 8 and any(char.isdigit() for char in compact):
            return True
    return False


def _json_item_is_other_config_choice(value, config_name: str, has_bound_config_option: bool) -> bool:
    if not has_bound_config_option or _json_item_is_bound_config_option(value, config_name):
        return False
    if _json_item_is_config_option(value, config_name):
        return True
    if not _json_item_is_option_shaped(value):
        return False
    return not _json_item_is_safe_non_config_option(value)


def _is_alas_instance_scope(value: object) -> bool:
    return "alas-instance" in str(value or "").lower()


def _is_alas_instance_aside_style(value: object) -> bool:
    return "--aside-" in str(value or "").lower()


def _json_buttons_target_other_alas_instance(buttons, config_name: str) -> bool:
    if not isinstance(buttons, list):
        return False
    labels = [
        str(button.get("label") or "").strip()
        for button in buttons
        if isinstance(button, dict) and str(button.get("label") or "").strip()
    ]
    if not labels:
        return False
    if any(label == config_name for label in labels):
        return False
    return any(
        _json_item_is_config_option(label, config_name)
        or not _json_item_is_safe_non_config_option(label)
        for label in labels
    )


def _json_item_directly_targets_other_alas_instance(value, config_name: str) -> bool:
    """Return True for one PyWebIO ALAS instance button that exposes another config."""
    if not isinstance(value, dict):
        return False
    lowered = {str(key).lower(): item for key, item in value.items()}
    buttons = lowered.get("buttons")
    if buttons is None:
        return False
    context_values = (
        lowered.get("scope"),
        lowered.get("container"),
        lowered.get("dom_id"),
        lowered.get("style"),
        lowered.get("color"),
        lowered.get("type"),
    )
    looks_like_instance = (
        any(_is_alas_instance_scope(item) for item in context_values)
        or any(_is_alas_instance_aside_style(item) for item in context_values)
        or any(
            isinstance(button, dict) and str(button.get("color") or "").lower() == "aside"
            for button in buttons
        )
    )
    return looks_like_instance and _json_buttons_target_other_alas_instance(buttons, config_name)


def _json_item_is_aside_icon_output(value) -> bool:
    """Return True for ALAS' direct or PyWebIO-wrapped aside SVG output."""
    if not isinstance(value, dict):
        return False
    item_type = str(value.get("type") or "").strip().lower()
    content = str(value.get("content") or "").strip().lower()
    if item_type == "html":
        return "<svg" in content and ("aside-icon" in content or "icon-setting" in content)
    if item_type != "custom_widget":
        return False
    data = value.get("data")
    contents = data.get("contents") if isinstance(data, dict) else None
    return (
        isinstance(contents, list)
        and len(contents) == 1
        and _json_item_is_aside_icon_output(contents[0])
    )


def _json_item_directly_targets_other_alas_instance_widget(value, config_name: str) -> bool:
    """Return True when one aside widget pairs an icon with another ALAS instance."""
    if not isinstance(value, dict) or str(value.get("type") or "").strip().lower() != "custom_widget":
        return False
    data = value.get("data")
    contents = data.get("contents") if isinstance(data, dict) else None
    if not isinstance(contents, list):
        return False
    return any(_json_item_is_aside_icon_output(item) for item in contents) and any(
        _json_item_directly_targets_other_alas_instance(item, config_name)
        for item in contents
    )


def _json_item_directly_targets_restricted_sidebar_widget(value) -> bool:
    """Return True when one custom widget pairs a sidebar icon with restricted buttons."""
    if not isinstance(value, dict) or str(value.get("type") or "").strip().lower() != "custom_widget":
        return False
    data = value.get("data")
    contents = data.get("contents") if isinstance(data, dict) else None
    if not isinstance(contents, list):
        return False
    has_aside_icon = False
    has_restricted_button = False
    for item in contents:
        if not isinstance(item, dict):
            continue
        scope = str(item.get("scope") or "").strip().lower()
        style = str(item.get("style") or "").strip().lower()
        if _json_item_is_aside_icon_output(item):
            has_aside_icon = True
        item_type = str(item.get("type") or "").strip().lower()
        if item_type != "buttons" or not isinstance(item.get("buttons"), list):
            continue
        buttons = item["buttons"]
        aside_context = (
            "aside" in scope
            or "menu" in scope
            or "--aside-" in style
            or "--menu-" in style
            or any(
                isinstance(button, dict) and str(button.get("color") or "").strip().lower() in {"aside", "menu"}
                for button in buttons
            )
        )
        if aside_context and any(
            isinstance(button, dict)
            and (
                _json_item_targets_restricted_user_entry(button)
                or _json_item_targets_alas_settings(button)
            )
            for button in buttons
        ):
            has_restricted_button = True
    return has_aside_icon and has_restricted_button


def _json_item_targets_other_alas_instance(value, config_name: str) -> bool:
    """Return True for PyWebIO ALAS instance payloads that expose another config."""
    if isinstance(value, dict):
        if _json_item_directly_targets_other_alas_instance(value, config_name):
            return True
        return any(
            _json_item_targets_other_alas_instance(item, config_name)
            for item in value.values()
            if isinstance(item, (dict, list, tuple))
        )
    if isinstance(value, (list, tuple)):
        return any(_json_item_targets_other_alas_instance(item, config_name) for item in value)
    return False


def _json_item_is_bound_config_option(value, config_name: str) -> bool:
    if isinstance(value, str):
        return value.strip() == config_name
    if not isinstance(value, dict):
        return False
    return any(str(value.get(key) or "").strip() == config_name for key in CONFIG_OPTION_KEYS)


def _looks_like_config_choice_text(value: object, config_name: str) -> bool:
    raw = str(value or "").strip()
    if not raw:
        return False
    if raw == config_name:
        return True
    compact = _compact_text(raw)
    bound_compact = _compact_text(config_name)
    if compact == bound_compact:
        return True
    if compact == "alas":
        return True
    if compact.isdigit() and 4 <= len(compact) <= 32:
        return True
    return False


def filter_user_json_payload(value, config_name: str):
    """Filter obvious ALAS config-list payloads down to the bound config."""
    if isinstance(value, dict):
        if (
            _json_item_targets_alas_settings(value)
            or _json_item_targets_update_notice(value)
            or _json_item_directly_targets_other_alas_instance(value, config_name)
            or _json_item_directly_targets_other_alas_instance_widget(value, config_name)
            or _json_item_directly_targets_restricted_sidebar_widget(value)
        ):
            return {}
        filtered = {}
        has_bound_config_key = _dict_has_bound_config_key(value, config_name)
        for key, item in value.items():
            if _dict_key_is_other_config_choice(key, config_name, has_bound_config_key):
                continue
            filtered_key = mask_sensitive_device_endpoints(key)
            if str(key or "").strip().lower() == "inputs" and isinstance(item, (list, tuple)):
                filtered_inputs = []
                for entry in item:
                    if not isinstance(entry, dict):
                        continue
                    field_name = _pywebio_identifier(entry.get("name"))
                    normalized_field = "".join(
                        char.lower() for char in field_name if char.isalnum() or char == "_"
                    )
                    explicit_config_selector = normalized_field in PYWEBIO_CONFIG_FIELDS
                    if (
                        (not explicit_config_selector and _json_item_targets_restricted_user_entry(entry))
                        or _json_item_targets_alas_settings(entry)
                        or _json_item_targets_update_notice(entry)
                        or _json_item_targets_other_alas_instance(entry, config_name)
                    ):
                        continue
                    filtered_inputs.append(filter_user_json_payload(entry, config_name))
                filtered[filtered_key] = filtered_inputs
                continue
            if _is_config_list_key(key):
                if isinstance(item, list):
                    filtered[filtered_key] = [
                        filter_user_json_payload(entry, config_name)
                        for entry in item
                        if _json_item_matches_bound_config(entry, config_name)
                        and not _json_item_targets_restricted_user_entry(entry)
                        and not _json_item_targets_alas_settings(entry)
                        and not _json_item_targets_update_notice(entry)
                        and not _json_item_targets_other_alas_instance(entry, config_name)
                    ]
                elif _json_item_matches_bound_config(item, config_name):
                    filtered[filtered_key] = filter_user_json_payload(item, config_name)
                else:
                    filtered[filtered_key] = [] if isinstance(item, (list, tuple)) else None
                continue
            filtered[filtered_key] = filter_user_json_payload(item, config_name)
        return filtered
    if isinstance(value, list):
        has_bound_config_option = any(_json_item_is_bound_config_option(item, config_name) for item in value)
        return [
            filter_user_json_payload(item, config_name)
            for item in value
            if not _json_item_targets_restricted_user_entry(item)
            and not _json_item_targets_alas_settings(item)
            and not _json_item_targets_update_notice(item)
            and not _json_item_directly_targets_restricted_sidebar_widget(item)
            and not _json_item_targets_other_alas_instance(item, config_name)
            and not _json_item_is_other_config_choice(item, config_name, has_bound_config_option)
        ]
    if isinstance(value, tuple):
        has_bound_config_option = any(_json_item_is_bound_config_option(item, config_name) for item in value)
        return [
            filter_user_json_payload(item, config_name)
            for item in value
            if not _json_item_targets_restricted_user_entry(item)
            and not _json_item_targets_alas_settings(item)
            and not _json_item_targets_update_notice(item)
            and not _json_item_directly_targets_restricted_sidebar_widget(item)
            and not _json_item_targets_other_alas_instance(item, config_name)
            and not _json_item_is_other_config_choice(item, config_name, has_bound_config_option)
        ]
    return mask_sensitive_device_endpoints(value)


def _message_switches_downstream_config(value, config_name: str) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if (
                str(key).lower() in ("config", "config_name")
                and not _downstream_config_value_is_ui_label(item)
                and _config_value_mismatches(item, config_name)
            ):
                return True
            if isinstance(item, (dict, list, tuple)) and _message_switches_downstream_config(item, config_name):
                return True
    if isinstance(value, (list, tuple)):
        return any(_message_switches_downstream_config(item, config_name) for item in value)
    return False


def _filter_user_websocket_downstream_payload(
    message: str | bytes,
    payload,
    config_name: str,
) -> tuple[str | bytes | None, object | None]:
    """Filter ALAS-to-browser WebSocket messages for bound users.

    Downstream messages are UI render payloads from PyWebIO.  They may contain
    broad scope names such as "Alas" even when the browser is not navigating to
    the sensitive ALAS settings page, so this path must clean fields and menu
    items instead of reusing the stricter upstream request blocker.
    """
    if payload is None:
        if isinstance(message, bytes):
            return None, None
        text = str(message or "")
        if _text_contains_management_command(text):
            return None, None
        if _text_targets_restricted_user_entry(text):
            return None, None
        if _text_targets_alas_settings_ui(text):
            return None, None
        if _is_update_notice_text(text):
            return None, None
        if not ADB_ENDPOINT_RE.search(text):
            return message, None
        filtered_text = ADB_ENDPOINT_RE.sub(SENSITIVE_DEVICE_ENDPOINT_PLACEHOLDER, text)
        return filtered_text, None
    if isinstance(payload, dict) and str(payload.get("command") or "").strip().lower() == "pin_onchange":
        filtered_pin = mask_sensitive_device_endpoints(payload)
        if filtered_pin == payload:
            return message, payload
        text = json.dumps(filtered_pin, ensure_ascii=False, separators=(",", ":"))
        return (text.encode("utf-8") if isinstance(message, bytes) else text), filtered_pin
    if isinstance(payload, dict) and str(payload.get("command") or "").strip().lower() == "output":
        spec = payload.get("spec")
        if isinstance(spec, dict) and str(spec.get("type") or "").strip().lower() == "custom_widget":
            if _json_item_directly_targets_other_alas_instance_widget(spec, config_name):
                return None, None
            if _json_item_directly_targets_restricted_sidebar_widget(spec):
                return None, None
            data = spec.get("data")
            title = data.get("title") if isinstance(data, dict) else ""
            scope = str(spec.get("scope") or "").strip().lower()
            if _is_restricted_user_entry_label(title) and scope.endswith("pywebio-scope-menu"):
                return None, None
    if _json_item_targets_update_notice(payload):
        return None, None
    if _json_item_directly_targets_other_alas_instance(payload, config_name):
        return None, None
    filtered = filter_user_json_payload(payload, config_name)
    if _json_item_is_other_config_choice(filtered, config_name, True):
        return None, None
    if _message_switches_downstream_config(filtered, config_name):
        return None, None
    if (
        isinstance(filtered, dict)
        and isinstance(payload, dict)
        and str(payload.get("command") or "").strip().lower() == "output"
    ):
        original_spec = payload.get("spec")
        filtered_spec = filtered.get("spec")
        if (
            isinstance(original_spec, dict)
            and isinstance(original_spec.get("buttons"), list)
            and original_spec.get("buttons")
            and isinstance(filtered_spec, dict)
            and filtered_spec.get("buttons") == []
        ):
            return None, None
    if filtered == payload:
        return message, payload
    text = json.dumps(filtered, ensure_ascii=False, separators=(",", ":"))
    if isinstance(message, bytes):
        return text.encode("utf-8"), filtered
    return text, filtered


def filter_user_websocket_downstream(message: str | bytes, config_name: str) -> str | bytes | None:
    """Filter one downstream frame while preserving the public compatibility API."""
    payload = _parse_websocket_message(message)
    filtered, _filtered_payload = _filter_user_websocket_downstream_payload(
        message,
        payload,
        config_name,
    )
    return filtered


def _short_task_ref(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:12]


def _pywebio_identifier(value: object) -> str:
    if isinstance(value, (dict, list, tuple)):
        return ""
    identifier = str(value or "").strip()
    if not identifier or len(identifier) > 512:
        return ""
    return identifier


def _policy_texts(value) -> list[str]:
    keys = set(CONFIG_OPTION_KEYS) | set(ACTION_MESSAGE_KEYS) | {
        "menu",
        "category",
        "section",
        "module",
        "page",
        "tab",
    }
    texts: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            lowered_key = str(key or "").lower()
            if lowered_key in PYWEBIO_CALLBACK_ID_KEYS or lowered_key == "task_id":
                continue
            if lowered_key in keys and not isinstance(item, (dict, list, tuple)):
                text = str(item or "").strip()
                if text:
                    texts.append(text)
            if isinstance(item, (dict, list, tuple)):
                texts.extend(_policy_texts(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            texts.extend(_policy_texts(item))
    return texts


def _field_is_restricted(field_name: object) -> bool:
    normalized_field = "".join(
        char.lower() for char in str(field_name or "") if char.isalnum() or char == "_"
    )
    if normalized_field in PYWEBIO_CONFIG_FIELDS:
        return False
    return (
        normalized_field.startswith(PYWEBIO_RESTRICTED_FIELD_PREFIXES)
        or _is_alas_settings_field(field_name)
        or _is_restricted_user_entry_route(field_name)
    )


def _pywebio_data_has_restricted_field(value) -> bool:
    """Detect restricted field identifiers without inspecting ordinary values."""
    if isinstance(value, dict):
        for key, item in value.items():
            lowered_key = str(key or "").lower()
            if _field_is_restricted(key):
                return True
            if lowered_key in {"field", "key", "name", "setting"} and not isinstance(
                item,
                (dict, list, tuple),
            ):
                if _field_is_restricted(item):
                    return True
            if isinstance(item, (dict, list, tuple)) and _pywebio_data_has_restricted_field(item):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_pywebio_data_has_restricted_field(item) for item in value)
    return False


def _pywebio_contains_explicit_action(value, markers: tuple[str, ...]) -> bool:
    """Inspect protocol action fields, not arbitrary form values or result text."""
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key or "").lower() in ACTION_MESSAGE_KEYS and _text_has_action_marker(
                str(item or ""),
                markers,
            ):
                return True
            if isinstance(item, (dict, list, tuple)) and _pywebio_contains_explicit_action(
                item,
                markers,
            ):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_pywebio_contains_explicit_action(item, markers) for item in value)
    return False


def _pywebio_explicit_config_mismatch(value, config_name: str) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key or "").lower() in PYWEBIO_CONFIG_FIELDS and _config_value_mismatches(item, config_name):
                return True
            if isinstance(item, (dict, list, tuple)) and _pywebio_explicit_config_mismatch(item, config_name):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_pywebio_explicit_config_mismatch(item, config_name) for item in value)
    return False


def _callback_capability(value, config_name: str) -> CallbackCapability:
    restricted = (
        _json_item_targets_restricted_user_entry(value)
        or _json_item_targets_alas_settings(value)
        or _json_item_targets_update_notice(value)
        or _json_item_targets_other_alas_instance(value, config_name)
        or _pywebio_explicit_config_mismatch(value, config_name)
    )
    action_text = " ".join(_policy_texts(value))
    return CallbackCapability(
        restricted=restricted,
        requires_run=_text_has_action_marker(action_text, RUN_ACTION_MARKERS),
        requires_edit=_text_has_action_marker(action_text, EDIT_ACTION_MARKERS),
    )


def _merge_callback_capability(target: dict[str, CallbackCapability], callback_id: str, capability: CallbackCapability) -> None:
    current = target.get(callback_id)
    target[callback_id] = current.merged(capability) if current else capability


def _collect_callback_capabilities(value, config_name: str) -> dict[str, CallbackCapability]:
    found: dict[str, CallbackCapability] = {}

    def visit(item, editable_callback_id: str = "") -> None:
        if isinstance(item, dict):
            callback_ids: list[str] = []
            for key, child in item.items():
                if str(key or "").lower() in PYWEBIO_CALLBACK_ID_KEYS:
                    callback_id = _pywebio_identifier(child)
                    if callback_id:
                        callback_ids.append(callback_id)
            if callback_ids:
                capability = _callback_capability(item, config_name)
                for callback_id in callback_ids:
                    callback_capability = (
                        capability.merged(CallbackCapability(requires_edit=True))
                        if callback_id == editable_callback_id
                        else capability
                    )
                    _merge_callback_capability(found, callback_id, callback_capability)
            command = str(item.get("command") or "").strip().lower()
            spec = item.get("spec")
            pin_callback_id = ""
            if command == "pin_onchange" and isinstance(spec, dict):
                pin_callback_id = _pywebio_identifier(spec.get("callback_id"))
            for child in item.values():
                if isinstance(child, (dict, list, tuple)):
                    visit(child, pin_callback_id if child is spec else "")
        elif isinstance(item, (list, tuple)):
            for child in item:
                visit(child)

    visit(value)
    return found


def _task_fields_from_spec(command: str, spec) -> tuple[frozenset[str], frozenset[str]]:
    fields: set[str] = set()
    if not isinstance(spec, dict):
        return frozenset(), frozenset()
    if command == "input_group":
        inputs = spec.get("inputs")
        if isinstance(inputs, list):
            for item in inputs:
                if not isinstance(item, dict):
                    continue
                name = _pywebio_identifier(item.get("name"))
                if name:
                    fields.add(name)
    elif command in PYWEBIO_YIELD_COMMANDS:
        name = _pywebio_identifier(spec.get("name"))
        if name:
            fields.add(name)
        names = spec.get("names")
        if isinstance(names, (list, tuple)):
            for item in names:
                name = _pywebio_identifier(item)
                if name:
                    fields.add(name)
    restricted = {name for name in fields if _field_is_restricted(name)}
    return frozenset(fields), frozenset(restricted)


def _collect_task_registrations(value, config_name: str) -> dict[str, PyWebIOTaskRegistration]:
    if not isinstance(value, dict):
        return {}
    command = str(value.get("command") or "").strip().lower()
    if command != "input_group" and command not in PYWEBIO_YIELD_COMMANDS:
        return {}
    task_id = _pywebio_identifier(value.get("task_id"))
    if not task_id:
        return {}
    fields, restricted_fields = _task_fields_from_spec(command, value.get("spec"))
    registration = PyWebIOTaskRegistration(
        kind="input" if command == "input_group" else "yield",
        fields=fields,
        restricted_fields=restricted_fields,
        restricted=bool(restricted_fields) or _pywebio_explicit_config_mismatch(value, config_name),
    )
    return {task_id: registration}


def _merge_task_registration(
    current: PyWebIOTaskRegistration,
    other: PyWebIOTaskRegistration,
) -> PyWebIOTaskRegistration:
    if current.kind != other.kind:
        return PyWebIOTaskRegistration(kind="conflict", restricted=True)
    return PyWebIOTaskRegistration(
        kind=current.kind,
        fields=frozenset(set(current.fields) | set(other.fields)),
        restricted_fields=frozenset(set(current.restricted_fields) | set(other.restricted_fields)),
        restricted=current.restricted or other.restricted,
    )


def _downstream_observation_reason(
    original,
    filtered,
    original_payload,
    filtered_payload,
    config_name: str,
) -> tuple[str, str]:
    payload = original_payload
    command = str(payload.get("command") or "").strip().lower() if isinstance(payload, dict) else ""
    if filtered is not None:
        unchanged = filtered == original or (
            payload is not None and filtered_payload is not None and payload == filtered_payload
        )
        return ("forwarded" if unchanged else "filtered", command)
    if payload is None:
        return "invalid_downstream", command
    if _json_item_targets_update_notice(payload):
        return "update_notice_dropped", command
    if _json_item_targets_other_alas_instance(payload, config_name):
        return "other_instance_dropped", command
    if _json_item_targets_restricted_user_entry(payload):
        return "restricted_entry_dropped", command
    if _json_item_targets_alas_settings(payload):
        return "alas_settings_dropped", command
    if _message_switches_downstream_config(payload, config_name):
        return "config_mismatch_dropped", command
    return "downstream_dropped", command


class PyWebIOSessionPolicy:
    """Stateful authorization for one filtered ALAS/PyWebIO WebSocket."""

    def __init__(self, config_name: str, can_run: bool = True, can_edit: bool = True) -> None:
        self.config_name = str(config_name or "").strip()
        self.can_run = bool(can_run)
        self.can_edit = bool(can_edit)
        self.callbacks: dict[str, CallbackCapability] = {}
        self.denied_callbacks: set[str] = set()
        self.tasks: dict[str, PyWebIOTaskRegistration] = {}
        self.denied_tasks: set[str] = set()

    def _deny_callback(self, callback_id: str) -> None:
        self.callbacks.pop(callback_id, None)
        self.denied_callbacks.add(callback_id)

    def _allow_callback(self, callback_id: str, capability: CallbackCapability) -> None:
        if callback_id in self.denied_callbacks or capability.restricted:
            self._deny_callback(callback_id)
            return
        current = self.callbacks.get(callback_id)
        self.callbacks[callback_id] = current.merged(capability) if current else capability

    def _deny_task(self, task_id: str) -> None:
        self.tasks.pop(task_id, None)
        self.denied_tasks.add(task_id)

    def _allow_task(self, task_id: str, registration: PyWebIOTaskRegistration) -> None:
        if task_id in self.denied_tasks or registration.restricted:
            self._deny_task(task_id)
            return
        current = self.tasks.get(task_id)
        if current:
            registration = _merge_task_registration(current, registration)
        if registration.restricted:
            self._deny_task(task_id)
            return
        self.tasks[task_id] = registration

    def observe_downstream(
        self,
        original,
        filtered,
        *,
        original_payload=_UNPARSED_WEBSOCKET_PAYLOAD,
        filtered_payload=_UNPARSED_WEBSOCKET_PAYLOAD,
    ) -> DownstreamObservation:
        if original_payload is _UNPARSED_WEBSOCKET_PAYLOAD:
            original_payload = _parse_websocket_message(original)
        if filtered_payload is _UNPARSED_WEBSOCKET_PAYLOAD:
            if filtered is None:
                filtered_payload = None
            elif filtered is original or filtered == original:
                filtered_payload = original_payload
            else:
                filtered_payload = _parse_websocket_message(filtered)
        original_callbacks = _collect_callback_capabilities(original_payload, self.config_name)
        original_tasks = _collect_task_registrations(original_payload, self.config_name)
        if filtered_payload is original_payload:
            allowed_callbacks = original_callbacks
            allowed_tasks = original_tasks
        else:
            allowed_callbacks = _collect_callback_capabilities(filtered_payload, self.config_name)
            allowed_tasks = _collect_task_registrations(filtered_payload, self.config_name)

        for callback_id, capability in original_callbacks.items():
            if callback_id not in allowed_callbacks or capability.restricted:
                self._deny_callback(callback_id)
        for callback_id, capability in allowed_callbacks.items():
            original_capability = original_callbacks.get(callback_id, capability)
            self._allow_callback(callback_id, capability.merged(original_capability))

        for task_id, registration in original_tasks.items():
            allowed_registration = allowed_tasks.get(task_id)
            if not allowed_registration or registration.kind != allowed_registration.kind:
                self._deny_task(task_id)
        for task_id, registration in allowed_tasks.items():
            original_registration = original_tasks.get(task_id, registration)
            if registration.kind == "input":
                removed_fields = set(original_registration.fields) - set(registration.fields)
                registration = PyWebIOTaskRegistration(
                    kind="input",
                    fields=registration.fields,
                    restricted_fields=frozenset(
                        set(registration.restricted_fields)
                        | set(original_registration.restricted_fields)
                        | removed_fields
                    ),
                    restricted=False,
                )
            elif original_registration.restricted:
                registration = PyWebIOTaskRegistration(
                    kind=registration.kind,
                    fields=registration.fields,
                    restricted_fields=original_registration.restricted_fields,
                    restricted=True,
                )
            self._allow_task(task_id, registration)

        reason, command = _downstream_observation_reason(
            original,
            filtered,
            original_payload,
            filtered_payload,
            self.config_name,
        )
        return DownstreamObservation(
            forwarded=filtered is not None,
            reason=reason,
            command=command,
            allowed_callbacks=len(self.callbacks),
            denied_callbacks=len(self.denied_callbacks),
            allowed_tasks=len(self.tasks),
            denied_tasks=len(self.denied_tasks),
        )

    def _decision(
        self,
        action: WebSocketMessageAction,
        reason: str,
        event: str,
        task_id: str = "",
        permission: str = "",
    ) -> WebSocketMessageDecision:
        return WebSocketMessageDecision(
            action=action,
            reason=reason,
            event=event if event in PYWEBIO_EVENTS else ("unknown" if event else "legacy"),
            permission=permission,
            task_ref=_short_task_ref(task_id),
        )

    def _explicit_violation(
        self,
        payload: dict,
        event: str,
        task_id: str,
    ) -> WebSocketMessageDecision | None:
        inspected_payload = {
            key: value for key, value in payload.items() if str(key).lower() != "data"
        }
        structured_data = payload.get("data")
        inspect_structured_data = event != "js_yield" and isinstance(
            structured_data,
            (dict, list, tuple),
        )
        if _message_contains_management(inspected_payload) or (
            inspect_structured_data and _message_contains_management(structured_data)
        ):
            return self._decision(WebSocketMessageAction.CLOSE, "management_denied", event, task_id)
        if _message_targets_restricted_user_entry(inspected_payload) or (
            inspect_structured_data and _message_targets_restricted_user_entry(structured_data)
        ):
            return self._decision(
                WebSocketMessageAction.CLOSE,
                "restricted_entry_denied",
                event,
                task_id,
                "restricted",
            )
        if _message_targets_alas_settings(inspected_payload) or (
            inspect_structured_data and _message_targets_alas_settings(structured_data)
        ):
            return self._decision(
                WebSocketMessageAction.CLOSE,
                "alas_settings_denied",
                event,
                task_id,
                "restricted",
            )
        if _message_switches_config(inspected_payload, self.config_name) or (
            inspect_structured_data
            and _pywebio_explicit_config_mismatch(structured_data, self.config_name)
        ):
            return self._decision(
                WebSocketMessageAction.CLOSE,
                "config_mismatch",
                event,
                task_id,
                "restricted",
            )
        if event != "js_yield" and _pywebio_data_has_restricted_field(payload.get("data")):
            return self._decision(
                WebSocketMessageAction.CLOSE,
                "restricted_input_field",
                event,
                task_id,
                "restricted",
            )

        action_payload = inspected_payload
        scalar_data = payload.get("data")
        scalar_action = (
            event == "callback" or event not in PYWEBIO_EVENTS
        ) and not isinstance(scalar_data, (dict, list, tuple))
        if scalar_action:
            scalar_text = str(scalar_data or "")
            scalar_label = _compact_text(scalar_data)
            if scalar_label in {"manage", "admin", "management"} or _text_contains_management_command(
                scalar_text
            ):
                return self._decision(
                    WebSocketMessageAction.CLOSE,
                    "management_denied",
                    event,
                    task_id,
                    "restricted",
                )
            if _is_restricted_user_entry_label(scalar_data) or _is_restricted_user_entry_route(
                scalar_text
            ):
                return self._decision(
                    WebSocketMessageAction.CLOSE,
                    "restricted_entry_denied",
                    event,
                    task_id,
                    "restricted",
                )
            if (
                _is_alas_settings_task(scalar_data)
                or _is_alas_settings_label(scalar_data)
                or _is_alas_settings_field(scalar_text)
            ):
                return self._decision(
                    WebSocketMessageAction.CLOSE,
                    "alas_settings_denied",
                    event,
                    task_id,
                    "restricted",
                )
        if not self.can_run and (
            _pywebio_contains_explicit_action(action_payload, RUN_ACTION_MARKERS)
            or (
                inspect_structured_data
                and _pywebio_contains_explicit_action(structured_data, RUN_ACTION_MARKERS)
            )
            or (scalar_action and _text_has_action_marker(str(scalar_data or ""), RUN_ACTION_MARKERS))
        ):
            return self._decision(
                WebSocketMessageAction.CLOSE,
                "run_permission_denied",
                event,
                task_id,
                "run",
            )
        if not self.can_edit and (
            _pywebio_contains_explicit_action(action_payload, EDIT_ACTION_MARKERS)
            or (
                inspect_structured_data
                and _pywebio_contains_explicit_action(structured_data, EDIT_ACTION_MARKERS)
            )
            or (scalar_action and _text_has_action_marker(str(scalar_data or ""), EDIT_ACTION_MARKERS))
        ):
            return self._decision(
                WebSocketMessageAction.CLOSE,
                "edit_permission_denied",
                event,
                task_id,
                "edit",
            )
        return None

    def _registered_task(self, task_id: str, kind: str, event: str) -> tuple[PyWebIOTaskRegistration | None, WebSocketMessageDecision | None]:
        if not task_id:
            return None, self._decision(WebSocketMessageAction.CLOSE, "missing_task_id", event)
        if task_id in self.denied_tasks:
            return None, self._decision(
                WebSocketMessageAction.CLOSE,
                "denied_task_id",
                event,
                task_id,
                "restricted",
            )
        registration = self.tasks.get(task_id)
        if not registration:
            return None, self._decision(
                WebSocketMessageAction.DROP,
                "unknown_task_id",
                event,
                task_id,
            )
        if registration.kind != kind:
            return None, self._decision(
                WebSocketMessageAction.DROP,
                "task_kind_mismatch",
                event,
                task_id,
            )
        return registration, None

    def _check_input_fields(
        self,
        registration: PyWebIOTaskRegistration,
        values: dict,
        event: str,
        task_id: str,
    ) -> WebSocketMessageDecision | None:
        submitted_fields = {str(key or "") for key in values.keys()}
        if not submitted_fields.issubset(set(registration.fields)):
            return self._decision(
                WebSocketMessageAction.CLOSE,
                "unknown_input_field",
                event,
                task_id,
                "edit",
            )
        for field_name, value in values.items():
            normalized_field = "".join(
                char.lower() for char in str(field_name or "") if char.isalnum() or char == "_"
            )
            if field_name in registration.restricted_fields or _field_is_restricted(field_name):
                return self._decision(
                    WebSocketMessageAction.CLOSE,
                    "restricted_input_field",
                    event,
                    task_id,
                    "restricted",
                )
            if normalized_field in PYWEBIO_CONFIG_FIELDS and _config_value_mismatches(value, self.config_name):
                return self._decision(
                    WebSocketMessageAction.CLOSE,
                    "config_mismatch",
                    event,
                    task_id,
                    "edit",
                )
        return None

    def evaluate_upstream(self, message: str | bytes) -> WebSocketMessageDecision:
        payload = _parse_websocket_message(message)
        if not isinstance(payload, dict):
            return websocket_message_decision(
                message,
                self.config_name,
                can_run=self.can_run,
                can_edit=self.can_edit,
            )
        if "event" not in payload:
            if "task_id" in payload or "data" in payload:
                return self._decision(
                    WebSocketMessageAction.CLOSE,
                    "missing_event",
                    "unknown",
                    _pywebio_identifier(payload.get("task_id")),
                )
            return websocket_message_decision(
                message,
                self.config_name,
                can_run=self.can_run,
                can_edit=self.can_edit,
            )

        event = str(payload.get("event") or "").strip().lower()
        if not event:
            return self._decision(WebSocketMessageAction.CLOSE, "missing_event", "unknown")
        task_id = _pywebio_identifier(payload.get("task_id"))
        if not task_id:
            return self._decision(WebSocketMessageAction.CLOSE, "missing_task_id", event)
        violation = self._explicit_violation(payload, event, task_id)
        if violation:
            return violation
        if event not in PYWEBIO_EVENTS:
            return self._decision(
                WebSocketMessageAction.DROP,
                "unknown_protocol_event",
                event,
                task_id,
            )
        if event == "callback":
            if task_id in self.denied_callbacks:
                return self._decision(
                    WebSocketMessageAction.CLOSE,
                    "denied_callback_id",
                    event,
                    task_id,
                    "restricted",
                )
            capability = self.callbacks.get(task_id)
            if not capability:
                return self._decision(
                    WebSocketMessageAction.DROP,
                    "unknown_callback_id",
                    event,
                    task_id,
                )
            if capability.restricted:
                return self._decision(
                    WebSocketMessageAction.CLOSE,
                    "restricted_callback",
                    event,
                    task_id,
                    capability.category,
                )
            if capability.requires_run and not self.can_run:
                return self._decision(
                    WebSocketMessageAction.CLOSE,
                    "run_permission_denied",
                    event,
                    task_id,
                    capability.category,
                )
            if capability.requires_edit and not self.can_edit:
                return self._decision(
                    WebSocketMessageAction.CLOSE,
                    "edit_permission_denied",
                    event,
                    task_id,
                    capability.category,
                )
            return self._decision(
                WebSocketMessageAction.FORWARD,
                "callback_allowed",
                event,
                task_id,
                capability.category,
            )

        if event == "from_cancel":
            _registration, denial = self._registered_task(task_id, "input", event)
            if denial:
                return denial
            return self._decision(
                WebSocketMessageAction.FORWARD,
                "cancel_allowed",
                event,
                task_id,
                "navigation",
            )

        if event == "js_yield":
            _registration, denial = self._registered_task(task_id, "yield", event)
            if denial:
                return denial
            return self._decision(
                WebSocketMessageAction.FORWARD,
                "yield_allowed",
                event,
                task_id,
                "navigation",
            )

        registration, denial = self._registered_task(task_id, "input", event)
        if denial:
            return denial
        if not self.can_edit:
            return self._decision(
                WebSocketMessageAction.CLOSE,
                "edit_permission_denied",
                event,
                task_id,
                "edit",
            )
        data = payload.get("data")
        if not isinstance(data, dict):
            return self._decision(
                WebSocketMessageAction.CLOSE,
                "invalid_event_data",
                event,
                task_id,
                "edit",
            )

        if event == "input_event":
            event_name = str(data.get("event_name") or "").strip().lower()
            field_name = _pywebio_identifier(data.get("name"))
            if event_name not in PYWEBIO_INPUT_EVENTS or not field_name:
                return self._decision(
                    WebSocketMessageAction.CLOSE,
                    "invalid_input_event",
                    event,
                    task_id,
                    "edit",
                )
            field_denial = self._check_input_fields(
                registration,
                {field_name: data.get("value")},
                event,
                task_id,
            )
            if field_denial:
                return field_denial
            return self._decision(
                WebSocketMessageAction.FORWARD,
                "input_event_allowed",
                event,
                task_id,
                "edit",
            )

        field_denial = self._check_input_fields(registration, data, event, task_id)
        if field_denial:
            return field_denial
        return self._decision(
            WebSocketMessageAction.FORWARD,
            "submit_allowed",
            event,
            task_id,
            "edit",
        )


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


async def proxy_websocket(
    websocket,
    base_url: str,
    path: str,
    decision: ProxyDecision,
    *,
    actor: str = "",
    role: str = "",
    connection_id: str = "",
) -> None:
    """双向转发 ScrcpyGate 客户端与 ALAS Runtime 的 WebSocket 消息。"""
    connection_id = _safe_connection_id(connection_id) or uuid.uuid4().hex[:12]
    try:
        target = websocket_target_url(base_url, path, bound_config_query_items(websocket.query_params, decision))
    except ValueError:
        log.warning(
            "ALAS_WS_CLOSE connection=%s event=connect reason=invalid_target permission=none task=none",
            connection_id,
        )
        await websocket.close(code=1011)
        return

    await websocket.accept()
    policy = (
        PyWebIOSessionPolicy(
            decision.config_name,
            can_run=getattr(decision, "can_run", True),
            can_edit=getattr(decision, "can_edit", True),
        )
        if decision.filtered
        else None
    )
    log.debug(
        "ALAS_WS_OPEN connection=%s event=connect reason=accepted permission=none task=none",
        connection_id,
    )
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
                        if policy is not None:
                            message_decision = policy.evaluate_upstream(text)
                            _log_upstream_decision(connection_id, message_decision)
                            if message_decision.action is WebSocketMessageAction.DROP:
                                continue
                            if message_decision.closes_connection:
                                await _close_upstream_safely(upstream, code=1008)
                                await _close_websocket_safely(websocket, 1008)
                                return 1008
                        await upstream.send(text)
                    elif "bytes" in message:
                        data = message["bytes"]
                        if policy is not None:
                            message_decision = policy.evaluate_upstream(data)
                            _log_upstream_decision(connection_id, message_decision)
                            if message_decision.action is WebSocketMessageAction.DROP:
                                continue
                            if message_decision.closes_connection:
                                await _close_upstream_safely(upstream, code=1008)
                                await _close_websocket_safely(websocket, 1008)
                                return 1008
                        await upstream.send(data)

            async def upstream_to_client() -> None:
                """转发上游文本或二进制消息回客户端。"""
                async for message in upstream:
                    if policy is not None:
                        original_message = message
                        original_payload = _parse_websocket_message(original_message)
                        message, filtered_payload = _filter_user_websocket_downstream_payload(
                            original_message,
                            original_payload,
                            decision.config_name,
                        )
                        observation = policy.observe_downstream(
                            original_message,
                            message,
                            original_payload=original_payload,
                            filtered_payload=filtered_payload,
                        )
                        downstream_log = log.debug if observation.reason == "forwarded" else log.warning
                        downstream_event = (
                            observation.command
                            if observation.command in PYWEBIO_LOG_COMMANDS
                            else "unknown"
                        )
                        downstream_log(
                            "ALAS_WS_DOWNSTREAM connection=%s event=%s reason=%s permission=%s task=none",
                            connection_id,
                            downstream_event,
                            observation.reason,
                            "none" if observation.reason == "forwarded" else "restricted",
                        )
                        if message is None:
                            continue
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
            log.debug(
                "ALAS_WS_CLOSE connection=%s event=close reason=completed permission=none task=none",
                connection_id,
            )
    except Exception:
        log.warning(
            "ALAS_WS_CLOSE connection=%s event=exception reason=exception permission=none task=none",
            connection_id,
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
            decoded = gzip.decompress(decoded)
            continue
        if encoding == "deflate":
            try:
                decoded = zlib.decompress(decoded)
            except zlib.error:
                decoded = zlib.decompress(decoded, -zlib.MAX_WBITS)
            continue
        raise ValueError(f"unsupported upstream content encoding: {encoding}")
    return decoded


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


def _read_upstream_response(opener, request: Request, timeout: float):
    """Perform the blocking urllib request and consume its response."""
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.read(), response.getcode(), response.headers
    except HTTPError as exc:
        return exc.read(), exc.code, exc.headers


async def proxy_http_request(request: FastAPIRequest, base_url: str, path: str, decision: ProxyDecision, body: bytes | None = None) -> Response:
    """转发 HTTP 请求到 ALAS Runtime，并按权限策略过滤 HTML 响应。"""
    if request.headers.get("upgrade") or "upgrade" in request.headers.get("connection", "").lower():
        raise HTTPException(status_code=501, detail="ALAS websocket proxy is not implemented")
    method = request.method.upper()
    if body is None:
        body = await request.body()
    data = None if method in ("GET", "HEAD") else body
    try:
        target = build_upstream_url(base_url, path, bound_config_query_items(request.query_params, decision))
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
        raw, status, upstream_headers = await asyncio.to_thread(_read_upstream_response, opener, req, 15.0)
        out_headers = _proxy_response_headers(upstream_headers, target, decision)
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
    media_type = _content_type_media_type(content_type)
    should_filter = decision.filtered and media_type in HTML_CONTENT_TYPES
    if decision.filtered and content_encoding and media_type in (*HTML_CONTENT_TYPES, "application/json"):
        try:
            raw = _decode_content_encoding(raw, content_encoding)
        except Exception as exc:
            raise HTTPException(status_code=502, detail="ALAS encoded response could not be filtered") from exc
        _pop_header_case_insensitive(out_headers, "Content-Encoding")
        content_encoding = ""
    if should_filter:
        charset = _content_type_charset(content_type)
        text = raw.decode(charset, errors="replace")
        raw = filter_user_html(text, decision.config_name).encode(charset, errors="xmlcharrefreplace")
        out_headers["Content-Type"] = f"{media_type}; charset={charset}"
    elif decision.filtered and media_type == "application/json":
        charset = _content_type_charset(content_type)
        try:
            payload = json.loads(raw.decode(charset, errors="replace"))
        except Exception:
            payload = None
        if isinstance(payload, (dict, list)):
            payload = filter_user_json_payload(payload, decision.config_name)
            raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(charset, errors="xmlcharrefreplace")
            out_headers["Content-Type"] = f"application/json; charset={charset}"
    out_headers.pop("X-Frame-Options", None)
    out_headers["Content-Security-Policy"] = "frame-ancestors 'self'"
    return Response(content=raw, status_code=status, headers=out_headers)
