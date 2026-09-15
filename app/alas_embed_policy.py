"""ALAS Embed access, binding and downstream message policy."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import deque
from dataclasses import dataclass
from enum import Enum
from urllib.parse import unquote

from . import alas_policy_budget, alas_policy_json, alas_policy_permissions, alas_visibility
from .alas_policy_permissions import (
    bound_config_query_items,
    config_query_values,
    is_readonly_static_request,
)

ALAS_POLICY_MAX_DEPTH = alas_policy_budget.ALAS_POLICY_MAX_DEPTH
ALAS_POLICY_MAX_NODES = alas_policy_budget.ALAS_POLICY_MAX_NODES
ALAS_WS_MESSAGE_MAX_BYTES = alas_policy_budget.ALAS_WS_MESSAGE_MAX_BYTES
PayloadBudgetExceeded = alas_policy_budget.PayloadBudgetExceeded
ensure_websocket_message_size = alas_policy_budget.ensure_websocket_message_size
validate_policy_payload = alas_policy_budget.validate_policy_payload

ALAS_EMBED_PREFIX = "/alas/embed"
ALAS_DEFAULT_PORT = 22267
DOMAIN_FALLBACK_PORTS = (80, 443, 22267)
MANAGEMENT_MARKERS = ("管理", "admin", "manage", "management", "config_list", "alas.config_list", "settings.admin")
MANAGEMENT_MESSAGE_KEYS = ("event", "command", "method", "action", "path", "topic", "type", "op", "api", "route")
LEGACY_PLAIN_TEXT_MESSAGES = frozenset({"ping", "pong"})
# 与 alas_policy_permissions 共用同一份定义：早先这里少一个成员（scrcpygate_context），
# 于是「钉配置」路径会剥掉它、重写 URL 的路径不会。
CONFIG_QUERY_KEYS = alas_policy_permissions.CONFIG_QUERY_KEYS
CONFIG_LIST_KEYS = ("configs", "config_list", "configlist", "config_names")
SCRCPYGATE_CONTEXT_QUERY_KEYS = alas_policy_permissions.SCRCPYGATE_CONTEXT_QUERY_KEYS
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
# 受限过滤规则由后台配置（app/alas_visibility.py）。判定一律走 visibility_rules()
# 的缓存快照；这里不再保留任何「默认值副本」——早先那几个副本（
# RESTRICTED_USER_ENTRY_LABELS/ROUTE_MARKERS、ALAS_SETTINGS_FIELD_MARKERS、
# PYWEBIO_RESTRICTED_FIELD_PREFIXES）无人引用，且默认值改成「选项层全开」后它们只
# 剩下空元组，留着只会误导（见 2026-09-11 审计）。
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
OMITTED_RESPONSE_HEADERS = HOP_BY_HOP_HEADERS | {"content-length", "set-cookie", "set-cookie2"}
# 浏览器凭据绝不出站：上游通过 X-Alas-Gyre-Token 鉴权，转发会话 Cookie 等同把主站会话交给内网上游。
CREDENTIAL_REQUEST_HEADERS = frozenset({"cookie", "authorization", "proxy-authorization", "x-csrf-token"})
SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
ALAS_PROXY_REQUEST_MAX_BYTES = 64 * 1024
ALAS_REQUEST_BODY_TOO_LARGE_DETAIL = "ALAS proxy request body too large"
UPSTREAM_RESPONSE_TOO_LARGE_DETAIL = "ALAS Runtime response exceeds the configured size limit"
log = logging.getLogger("webscrcpy.alas_embed")


# 与 alas_policy_budget 共用同一份实现（早先每个模块各自抄一份，policy 的副本还漏了
# OverflowError 分支）。
_bounded_env_int = alas_policy_budget.bounded_env_int


# Callback/task identifiers are learned from downstream frames and remain
# valid for the lifetime of one WebSocket.  Bound the per-session registry so
# a long-lived Runtime cannot grow process memory without limit.
#
# 4096 was too small for a real ALAS session: one bound-user navigation renders
# ~1000 options, and PyWebIO re-announces every option with a fresh callback id
# (measured: 981 options × 3 renders + 146 navigation callbacks in a single
# sitting ⇒ 4328 ids).  Once the ceiling was hit, ``_allow_callback`` could not
# record the newly advertised ids at all, so the widget stayed on screen while
# every click on it was dropped as ``unknown_callback_id``.  The default now
# leaves headroom for several page renders, and the ceiling itself evicts the
# oldest allowed entry instead of voiding new ones (see ``_reserve_allowed_slot``).
ALAS_POLICY_REGISTRY_MAX_ENTRIES = _bounded_env_int(
    "ALAS_POLICY_REGISTRY_MAX_ENTRIES",
    16_384,
    64,
    100_000,
)


_ensure_websocket_message_size = ensure_websocket_message_size
_validate_policy_payload = validate_policy_payload

ALAS_UPSTREAM_RESPONSE_MAX_BYTES = _bounded_env_int(
    "ALAS_UPSTREAM_RESPONSE_MAX_BYTES",
    16 * 1024 * 1024,
    64 * 1024,
    64 * 1024 * 1024,
)
# 解压输出独立上限：压缩字节上限无法约束 gzip 炸弹的解压放大。
ALAS_UPSTREAM_DECOMPRESSED_MAX_BYTES = _bounded_env_int(
    "ALAS_UPSTREAM_DECOMPRESSED_MAX_BYTES",
    64 * 1024 * 1024,
    1024 * 1024,
    1024 * 1024 * 1024,
)
UPSTREAM_READ_CHUNK_BYTES = 64 * 1024
# 运行/编辑类动作标记。中文标签也要认：ALAS 的调度按钮就是「启动 / 停止」，
# 只列英文会让 can_run=False 的普通用户照样启停任务（实测 scheduler_btn 的「启动」
# 被判成 navigation 而放行）。匹配是整词比较，所以「重启设置 / 更新器」这类中文
# 导航标签不会被误判成动作。
RUN_ACTION_MARKERS = (
    "start",
    "run",
    "stop",
    "pause",
    "resume",
    "restart",
    "deploy",
    "task",
    "job",
    "启动",
    "停止",
    "开始",
    "暂停",
    "继续",
    "重启",
    "运行",
    "执行",
)
EDIT_ACTION_MARKERS = (
    "save",
    "update",
    "edit",
    "delete",
    "create",
    "set",
    "config",
    "settings",
    "保存",
    "应用",
    "删除",
    "新建",
    "编辑",
    "更新",
)
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
PYWEBIO_EVENTS = frozenset({"callback", "from_submit", "from_cancel", "input_event", "js_yield"})
PYWEBIO_YIELD_COMMANDS = frozenset({"pin_value", "pin_wait", "run_script"})
PYWEBIO_LOG_COMMANDS = frozenset(
    {"output", "input_group", "pin_onchange", "pin_value", "pin_wait", "run_script", "toast"}
)
PYWEBIO_CALLBACK_ID_KEYS = frozenset({"callback_id", "click_callback_id"})
PYWEBIO_INPUT_EVENTS = frozenset({"change", "blur"})
PYWEBIO_CONFIG_FIELDS = frozenset({"config", "config_name"})
_UNPARSED_WEBSOCKET_PAYLOAD = object()


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
    device_context: str = ""
    gateway_context: str = ""
    pin_config: bool = False
    # 管理员关闭的 ALAS 页面选项（{key, match} 目标，或旧的纯文字）；代理 HTML 时注入脚本隐藏它们。
    hidden_matches: tuple = ()


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


def _body_contains_management(value) -> bool:
    """递归检查 HTTP 正文是否包含管理操作标记。"""
    return _message_contains_management(value)


def _body_switches_config(value, config_name: str) -> bool:
    """递归检查 HTTP 正文是否请求非绑定配置。"""
    return _message_switches_config(value, config_name)


def _body_denied_by_action_permission(value, can_run: bool, can_edit: bool) -> bool:
    """根据 can_run/can_edit 判断 HTTP 正文是否触发被禁操作。"""
    return _message_denied_by_action_permission(value, can_run, can_edit)
def _compact_text(value: object) -> str:
    """Return a case-folded text token without separators for fuzzy ALAS UI routing checks."""
    return "".join(char.lower() for char in str(value or "") if char.isalnum())


def _plain_text(value: object) -> str:
    return str(value or "").strip().lower()


def _is_alas_settings_task(value: object) -> bool:
    text = _compact_text(value)
    return text in alas_visibility.visibility_rules()["settings_tasks"]


def _is_alas_settings_label(value: object) -> bool:
    """「Alas 设置」标签：只有在可配置规则里仍列为受限时才算受限。

    `settings_tasks` 里既有标签形式（Alas设置 / AlasSettings），也有任务名（裸 alas）。
    裸 alas 归 `_is_alas_settings_task` 管，**不能当标签**：ALAS 的默认配置名就是
    `alas`，把它当设置标签会让普通用户的侧边栏配置按钮被整块摘掉（实测普通用户
    只剩「主页」，看不到自己的配置）。
    模糊回退（alas + 设置/setting）同样挂在列表上：管理员把列表清空即放开。
    """
    text = _compact_text(value)
    tasks = alas_visibility.visibility_rules()["settings_tasks"]
    if not tasks:
        return False
    label_tokens = {
        item for item in tasks if "设置" in item or "setting" in item or "設定" in item
    }
    if text in label_tokens:
        return True
    return "alas" in text and ("设置" in text or "setting" in text or "設定" in text)


def _is_alas_settings_field(value: object) -> bool:
    text = _plain_text(value).replace("\\", ".").replace("/", ".")
    compact = _compact_text(text)
    rules = alas_visibility.visibility_rules()
    if any(marker in text for marker in rules["settings_markers"]):
        return True
    return any(marker in compact for marker in rules["settings_markers_compact"])


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


def _is_restricted_user_entry_label(value: object) -> bool:
    compact = _compact_text(value)
    if not compact:
        return False
    return compact in alas_visibility.visibility_rules()["labels"]


def _route_marker_matches_words(value: object, marker: str) -> bool:
    """Return True when ``marker`` names a whole word of ``value``.

    Thin policy-side alias of the shared matcher in :mod:`app.alas_policy_json`, so the
    HTTP layer (``proxy_decision`` / query / body checks) and the WebSocket layer use
    exactly one implementation.  ALAS names its options in CamelCase, and a plain
    substring test let the management marker ``manage`` match ``GameManager``: the two
    ``GameManager_*`` options were then treated as restricted entries, so a bound user
    saw them on the page while every click or edit was denied.  CJK markers keep plain
    substring semantics because Chinese text has no word separators
    (``管理员工具`` has to match ``管理``).
    """
    return alas_policy_json.marker_matches_words(value, marker)


def _is_restricted_user_entry_route(value: object) -> bool:
    compact = _compact_text(value)
    if not compact:
        return False
    if _is_restricted_user_entry_label(value):
        return True
    return any(
        _route_marker_matches_words(value, marker)
        for marker in alas_visibility.visibility_rules()["route_markers"]
    )


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
    """Return True for downstream ALAS self-update prompts exposed to bound users.

    实际规则只有「提示文字命中」：原先还写了一个路由 + 文字的双重门槛，但其中文字那一半
    与上面的判断是同一个推导式，走到那里必然为 False —— 那半个函数（连同只给它用的
    路由键与动作判定）永远不会生效，已随本次审计删除。行为与修复前一致。
    """
    if not isinstance(value, dict):
        return False
    lowered = {str(key).lower(): item for key, item in value.items()}
    return any(key in lowered and _is_update_notice_text(lowered[key]) for key in UPDATE_NOTICE_TEXT_KEYS)


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
    """判断 HTTP 请求是否明显为只读页面、静态资源或状态查询。

    只看**方法 + 路径**：查询串的键名是客户端可控的，早先把它也算作证据时，
    `GET api/task/start?list=1` 会因为出现只读词 `list` 被当成只读请求，从而整段
    跳过 can_run/can_edit 检查（can_run=False 也能跑到运行类接口）。query 参数保留
    只为兼容既有调用方。
    """
    upper_method = str(method or "GET").upper()
    if upper_method not in SAFE_METHODS:
        return False
    path_text = str(path or "").strip()
    if not path_text:
        return True
    return _text_has_action_marker(path_text, READONLY_ACTION_MARKERS)


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


def feature_gate_applies(user: dict | None) -> bool:
    """后台「配置开关」是否作用于这次请求：只挡普通用户，管理员始终看到完整页面。"""
    return str((user or {}).get("role", "")) != "admin"


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

    # Validate once before any policy helper recursively inspects the body.
    # HTTP JSON is size-limited by the route, but the node/depth budget also
    # protects direct callers and prevents unbounded policy walks.
    if body is not None:
        try:
            _validate_policy_payload(body)
        except PayloadBudgetExceeded:
            return ProxyDecision(
                allowed=False,
                status_code=400,
                config_name=config_name,
                reason="invalid body",
            )

    # 管理路径同样是「整词」判断：原先的内联子串检查会把 /GameManager、
    # /assets/manager.js 这类正常路径当成管理入口直接 403，和 WebSocket 层
    # （已按整词匹配）互相矛盾。
    if _path_targets_management(path) or _query_contains_management(query or {}):
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

    for requested_config in config_query_values(query or {}):
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
# Pure recursive JSON checks are owned by ``alas_policy_json``.  Keep the
# local names as explicit compatibility aliases because the policy decision
# code and older callers still use the underscored helpers.
_text_has_action_marker = alas_policy_json.text_has_marker


def _query_contains_action(query, markers):
    return alas_policy_json.query_contains_action(query, markers, CONFIG_QUERY_KEYS)


def _query_contains_management(query):
    return alas_policy_json.query_contains_management(query, MANAGEMENT_MARKERS, CONFIG_QUERY_KEYS)


def _path_targets_management(path) -> bool:
    """判定代理路径是否指向 ALAS 管理入口（整词匹配，见 MANAGEMENT_MARKERS）。"""
    return alas_policy_json.text_has_word_marker(path, MANAGEMENT_MARKERS)


def _message_contains_management(value):
    return alas_policy_json.message_contains_management(value, MANAGEMENT_MESSAGE_KEYS, MANAGEMENT_MARKERS)


def _message_contains_action(value, markers):
    return alas_policy_json.message_contains_action(value, markers, ACTION_MESSAGE_KEYS, CONFIG_QUERY_KEYS)


def _message_denied_by_action_permission(value, can_run, can_edit):
    return alas_policy_json.message_denied_by_action_permission(
        value,
        can_run=can_run,
        can_edit=can_edit,
        run_markers=RUN_ACTION_MARKERS,
        edit_markers=EDIT_ACTION_MARKERS,
        action_keys=ACTION_MESSAGE_KEYS,
        config_keys=CONFIG_QUERY_KEYS,
    )


_config_value_mismatches = alas_policy_json.config_value_mismatches


def _message_switches_config(value, config_name):
    return alas_policy_json.message_switches_config(value, CONFIG_QUERY_KEYS, config_name)


def _parse_websocket_message(message):
    return alas_policy_json.parse_json_message(
        message,
        ensure_size=_ensure_websocket_message_size,
        validate_payload=_validate_policy_payload,
        budget_error=PayloadBudgetExceeded,
    )


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


def _legacy_plain_text_allowed(text: str) -> bool:
    """Allow only the legacy heartbeat messages that need non-JSON compatibility."""
    return str(text or "").strip().lower() in LEGACY_PLAIN_TEXT_MESSAGES


def websocket_message_decision(
    message: str | bytes,
    config_name: str,
    can_run: bool = True,
    can_edit: bool = True,
) -> WebSocketMessageDecision:
    """Apply the stateless policy used by non-PyWebIO compatibility messages."""
    try:
        payload = _parse_websocket_message(message)
    except PayloadBudgetExceeded as exc:
        return WebSocketMessageDecision(WebSocketMessageAction.CLOSE, exc.reason)
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
        if _legacy_plain_text_allowed(text):
            return WebSocketMessageDecision(WebSocketMessageAction.FORWARD, "plain_text_allowed")
        return WebSocketMessageDecision(WebSocketMessageAction.CLOSE, "invalid_text")
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


def _mapping_own_scalar_targets_restricted_entry(value) -> bool:
    """映射**自身**字段里的标量是否就是受限入口。

    只看标量：`data`/`value` 这类键常常装着整棵子树，对它们做 `_is_restricted_user_entry_route`
    会把容器一起判成受限入口（混合容器里只该摘掉受限项，其余必须保留）。
    显式配置选择器（Pin 的 `config` 字段名）沿用既有例外，不当成受限入口。
    """
    if not isinstance(value, dict):
        return False
    for key, item in value.items():
        if not isinstance(item, str) or str(key or "").lower() not in RESTRICTED_USER_ENTRY_KEYS:
            continue
        normalized_field = "".join(
            char.lower() for char in item if char.isalnum() or char == "_"
        )
        if normalized_field in PYWEBIO_CONFIG_FIELDS:
            continue
        if _is_restricted_user_entry_route(item):
            return True
    return False


def _filter_user_json_payload(value, config_name: str):
    """Filter obvious ALAS config-list payloads down to the bound config.

    与 WebSocket 下游过滤保持同一套规则：映射**自身**的标量字段也要过受限入口/管理判定
    （早先只查嵌套项，于是 `{"label": "管理"}` 在 HTTP JSON 出口原样下发，而同样的东西
    作为请求会被 403 —— 正是「看得见点不动」的成因之一）。命中时保留键、清空内容，
    与下游「容器帧保留骨架」的处理一致。
    """
    if isinstance(value, dict):
        if (
            _message_contains_management(value)
            or _mapping_own_scalar_targets_restricted_entry(value)
            or _json_item_targets_alas_settings(value)
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
                    filtered_inputs.append(_filter_user_json_payload(entry, config_name))
                filtered[filtered_key] = filtered_inputs
                continue
            if _is_config_list_key(key):
                if isinstance(item, list):
                    filtered[filtered_key] = [
                        _filter_user_json_payload(entry, config_name)
                        for entry in item
                        if _json_item_matches_bound_config(entry, config_name)
                        and not _json_item_targets_restricted_user_entry(entry)
                        and not _json_item_targets_alas_settings(entry)
                        and not _json_item_targets_update_notice(entry)
                        and not _json_item_targets_other_alas_instance(entry, config_name)
                    ]
                elif _json_item_matches_bound_config(item, config_name):
                    filtered[filtered_key] = _filter_user_json_payload(item, config_name)
                else:
                    filtered[filtered_key] = [] if isinstance(item, (list, tuple)) else None
                continue
            filtered[filtered_key] = _filter_user_json_payload(item, config_name)
        return filtered
    if isinstance(value, list):
        has_bound_config_option = any(_json_item_is_bound_config_option(item, config_name) for item in value)
        return [
            _filter_user_json_payload(item, config_name)
            for item in value
            if not _message_contains_management(item)
            and not _json_item_targets_restricted_user_entry(item)
            and not _json_item_targets_alas_settings(item)
            and not _json_item_targets_update_notice(item)
            and not _json_item_directly_targets_restricted_sidebar_widget(item)
            and not _json_item_targets_other_alas_instance(item, config_name)
            and not _json_item_is_other_config_choice(item, config_name, has_bound_config_option)
        ]
    if isinstance(value, tuple):
        has_bound_config_option = any(_json_item_is_bound_config_option(item, config_name) for item in value)
        return [
            _filter_user_json_payload(item, config_name)
            for item in value
            if not _message_contains_management(item)
            and not _json_item_targets_restricted_user_entry(item)
            and not _json_item_targets_alas_settings(item)
            and not _json_item_targets_update_notice(item)
            and not _json_item_directly_targets_restricted_sidebar_widget(item)
            and not _json_item_targets_other_alas_instance(item, config_name)
            and not _json_item_is_other_config_choice(item, config_name, has_bound_config_option)
        ]
    return mask_sensitive_device_endpoints(value)


def filter_user_json_payload(value, config_name: str):
    """Filter a bounded JSON-like payload while preserving the public API."""
    _validate_policy_payload(value)
    return _filter_user_json_payload(value, config_name)


def filter_user_json_response(value, config_name: str):
    """对代理的 JSON 响应套用与 WebSocket 下游**完全相同**的判定。

    直接复用帧过滤：命中管理/受限入口/其它配置/开关配置时返回 None（调用方回空对象或
    空数组），否则返回递归清空受限内容后的载荷。这样同一份数据不会出现「HTTP 出口保留、
    WebSocket 出口丢弃」的两套规则。
    """
    _validate_policy_payload(value)
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    filtered, _filtered_payload = _filter_user_websocket_downstream_payload(text, value, config_name)
    if filtered is None:
        return None
    return json.loads(filtered)


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


_DOWNSTREAM_BLANK_CONTENT_KEYS = (
    "contents",
    "buttons",
    "text",
    "title",
    "value",
    "options",
    "items",
    "callback_id",
)


def _blank_downstream_message(message, payload):
    """保留帧骨架、清空内容，替代"整帧丢弃" —— 只针对**容器帧**。

    PyWebIO 的 output 帧是往 scope 里追加节点的指令：把整帧丢掉，scope 就不会被创建，
    而后续帧仍会引用它（实测 `#pywebio-scope-menu` 被丢后，紧随的两帧仍在往里写，
    客户端直接显示「发生错误」，配置页要十几秒才恢复）。清空内容既能隐藏受限项，
    又让 scope 与位置保持有效。

    叶子帧（选项项、提示文本等）不创建 scope，保持原来的丢弃行为。
    """
    if not isinstance(payload, dict):
        return None, None
    spec = payload.get("spec")
    if not _downstream_frame_is_container(spec):
        return None, None
    blanked = {
        key: value
        for key, value in payload.items()
        if key not in {"spec", "data"}
    }
    if isinstance(spec, dict):
        new_spec = {
            key: value
            for key, value in spec.items()
            if key not in set(_DOWNSTREAM_BLANK_CONTENT_KEYS) | {"data"}
        }
        if "buttons" in spec:
            new_spec["buttons"] = []
        data = spec.get("data")
        if isinstance(data, dict):
            new_data = {
                key: value
                for key, value in data.items()
                if key not in _DOWNSTREAM_BLANK_CONTENT_KEYS
            }
            if "contents" in data:
                new_data["contents"] = []
            new_spec["data"] = new_data
        blanked["spec"] = new_spec
    data = payload.get("data")
    if isinstance(data, dict):
        blanked["data"] = {
            key: value
            for key, value in data.items()
            if key not in _DOWNSTREAM_BLANK_CONTENT_KEYS
        }
    text = json.dumps(blanked, ensure_ascii=False, separators=(",", ":"))
    return (text.encode("utf-8") if isinstance(message, bytes) else text), blanked


def _downstream_frame_is_container(spec) -> bool:
    """帧是否创建/复用持久 scope（只有这类帧丢不得）。"""
    if not isinstance(spec, dict):
        return False
    if "scope" in spec or isinstance(spec.get("buttons"), list):
        return True
    data = spec.get("data")
    return isinstance(data, dict) and isinstance(data.get("contents"), list)


_FEATURE_TEXT_KEYS = frozenset(
    {"label", "title", "text", "content", "caption", "message", "toast", "placeholder", "aria-label"}
)


def _hidden_feature_pairs(hidden_features) -> tuple[tuple[str, str], ...]:
    """规范化管理员关掉的游戏任务目标为 ((key, match), …)。"""
    pairs: list[tuple[str, str]] = []
    for item in hidden_features or ():
        if isinstance(item, dict):
            key = str(item.get("key") or "").strip()
            match = re.sub(r"\s+", "", str(item.get("match") or ""))
        else:
            key, match = "", re.sub(r"\s+", "", str(item or ""))
        if key or match:
            pairs.append((key, match))
    return tuple(pairs)


def _iter_feature_texts(node):
    """产出 `label/title/text/...` 这些「可见文字」字段的字符串值。"""
    if isinstance(node, dict):
        for name, child in node.items():
            if isinstance(child, str) and str(name or "").lower() in _FEATURE_TEXT_KEYS:
                yield child
            elif isinstance(child, (dict, list, tuple)):
                yield from _iter_feature_texts(child)
    elif isinstance(node, (list, tuple)):
        for child in node:
            yield from _iter_feature_texts(child)


def _feature_text_hit(value, matches) -> bool:
    compact = re.sub(r"\s+", "", str(value or ""))
    return bool(compact) and any(match in compact for match in matches)


def _feature_item_matches(item, keys, matches) -> bool:
    """单个条目（一行）是否属于被关掉的功能。

    只看这一条**自己**的字符串与按钮标签：工具组是一个容器帧装着 7 行，整帧清空会把
    「半自动点击」等未关的功能一起吃掉（实测）。
    """
    if not isinstance(item, dict):
        return False
    for key, child in item.items():
        if isinstance(child, str):
            if keys and any(f"--menu-{key}--" in child for key in keys):
                return True
            if str(key or "").lower() in _FEATURE_TEXT_KEYS and _feature_text_hit(child, matches):
                return True
        elif isinstance(child, dict):
            for sub in child.values():
                if isinstance(sub, str) and keys and any(f"--menu-{key}--" in sub for key in keys):
                    return True
    buttons = item.get("buttons")
    if isinstance(buttons, list):
        for button in buttons:
            if not isinstance(button, dict):
                continue
            if keys and any(
                f"--menu-{key}--" in str(value)
                for value in button.values() if isinstance(value, str)
                for key in keys
            ):
                return True
            if _feature_text_hit(button.get("label"), matches):
                return True
    return False


def _prune_hidden_feature_items(value, keys, matches) -> tuple[bool, object]:
    """递归摘掉命中隐藏功能的条目，其余原样保留。"""
    changed = False
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            if isinstance(child, list):
                kept = []
                for item in child:
                    if _feature_item_matches(item, keys, matches):
                        changed = True
                        continue
                    item_changed, item_value = _prune_hidden_feature_items(item, keys, matches)
                    changed = changed or item_changed
                    kept.append(item_value)
                result[key] = kept
            elif isinstance(child, dict):
                child_changed, child_value = _prune_hidden_feature_items(child, keys, matches)
                changed = changed or child_changed
                result[key] = child_value
            else:
                result[key] = child
        return changed, result
    if isinstance(value, list):
        kept = []
        for item in value:
            if _feature_item_matches(item, keys, matches):
                changed = True
                continue
            item_changed, item_value = _prune_hidden_feature_items(item, keys, matches)
            changed = changed or item_changed
            kept.append(item_value)
        return changed, kept
    return changed, value


def _frame_targets_hidden_feature(payload, hidden_features) -> bool:
    """整帧是否就是被关掉的那一行（叶子帧 / 只提到该功能的脚本帧）。"""
    pairs = _hidden_feature_pairs(hidden_features)
    if not pairs:
        return False
    keys = tuple(key for key, _match in pairs if key)
    matches = tuple(match for _key, match in pairs if match)

    def visit(node) -> bool:
        if isinstance(node, dict):
            for name, child in node.items():
                if isinstance(child, str):
                    if keys and "--menu-" in child and any(f"--menu-{key}--" in child for key in keys):
                        return True
                    if matches and str(name or "").lower() in _FEATURE_TEXT_KEYS and _feature_text_hit(child, matches):
                        return True
                elif isinstance(child, (dict, list, tuple)) and visit(child):
                    return True
            return False
        if isinstance(node, (list, tuple)):
            return any(visit(child) for child in node)
        return False

    return visit(payload)


def _filter_user_websocket_downstream_payload(
    message: str | bytes,
    payload,
    config_name: str,
    hidden_features=(),
) -> tuple[str | bytes | None, object | None]:
    """Filter ALAS-to-browser WebSocket messages for bound users.

    Downstream messages are UI render payloads from PyWebIO.  They may contain
    broad scope names such as "Alas" even when the browser is not navigating to
    the sensitive ALAS settings page, so this path must clean fields and menu
    items instead of reusing the stricter upstream request blocker.
    """
    if payload is not None:
        _validate_policy_payload(payload)
        hidden_pairs = _hidden_feature_pairs(hidden_features)
        if hidden_pairs:
            hidden_keys = tuple(key for key, _match in hidden_pairs if key)
            hidden_matches = tuple(match for _key, match in hidden_pairs if match)
            # 先按条目摘除：同一个容器帧里往往装着整组功能，整帧清空会误伤未关的项
            # （实测工具组一个容器帧装着 7 行）。
            pruned_changed, pruned_payload = _prune_hidden_feature_items(
                payload, hidden_keys, hidden_matches
            )
            if pruned_changed:
                text = json.dumps(pruned_payload, ensure_ascii=False, separators=(",", ":"))
                return (
                    text.encode("utf-8") if isinstance(message, bytes) else text,
                    pruned_payload,
                )
            if _frame_targets_hidden_feature(pruned_payload, hidden_features):
                blanked_message, blanked_payload = _blank_downstream_message(message, pruned_payload)
                if blanked_message is None:
                    return None, None
                return blanked_message, blanked_payload
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
                return _blank_downstream_message(message, payload)
            if _json_item_directly_targets_restricted_sidebar_widget(spec):
                return _blank_downstream_message(message, payload)
            data = spec.get("data")
            title = data.get("title") if isinstance(data, dict) else ""
            scope = str(spec.get("scope") or "").strip().lower()
            if _is_restricted_user_entry_label(title) and scope.endswith("pywebio-scope-menu"):
                return _blank_downstream_message(message, payload)
    if _json_item_targets_update_notice(payload):
        return None, None
    if _json_item_directly_targets_other_alas_instance(payload, config_name):
        # 容器帧（带 scope）改成清空内容：丢帧会让后续引用该 scope 的帧失效。
        return _blank_downstream_message(message, payload)
    filtered = _filter_user_json_payload(payload, config_name)
    if _json_item_is_other_config_choice(filtered, config_name, True):
        return _blank_downstream_message(message, payload)
    if _message_switches_downstream_config(filtered, config_name):
        return _blank_downstream_message(message, payload)
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
            return _blank_downstream_message(message, payload)
    if filtered == payload:
        return message, payload
    text = json.dumps(filtered, ensure_ascii=False, separators=(",", ":"))
    if isinstance(message, bytes):
        return text.encode("utf-8"), filtered
    return text, filtered


def filter_user_websocket_downstream(
    message: str | bytes,
    config_name: str,
    hidden_features=(),
) -> str | bytes | None:
    """Filter one downstream frame while preserving the public compatibility API."""
    try:
        payload = _parse_websocket_message(message)
        filtered, _filtered_payload = _filter_user_websocket_downstream_payload(
            message,
            payload,
            config_name,
            hidden_features,
        )
        return filtered
    except (PayloadBudgetExceeded, RecursionError):
        return None


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
        normalized_field.startswith(alas_visibility.visibility_rules()["field_prefixes"])
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


def _collect_pin_callback_ids(value, found: dict[str, str]) -> None:
    """Map each ALAS option (``pin_onchange`` name) to the callback id it now uses.

    PyWebIO re-announces every option with a **fresh** callback id on each render
    (measured: 981 options × 3 frames per navigation), so the same option name shows
    up repeatedly while the previous id is already dead in the browser.  Tracking the
    name lets the session release the superseded id instead of keeping thousands of
    unusable entries that push the registry into saturation.
    """
    if isinstance(value, dict):
        if str(value.get("command") or "").strip().lower() == "pin_onchange":
            spec = value.get("spec")
            if isinstance(spec, dict):
                name = _pywebio_identifier(spec.get("name"))
                callback_id = _pywebio_identifier(spec.get("callback_id"))
                if name and callback_id:
                    found[name] = callback_id
        for child in value.values():
            if isinstance(child, (dict, list, tuple)):
                _collect_pin_callback_ids(child, found)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _collect_pin_callback_ids(child, found)


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
        # 每个 ALAS 选项（pin_onchange 名字）当前有效的回调 id，用于释放被重新渲染覆盖的旧 id。
        self.pin_callbacks: dict[str, str] = {}
        # 已允许条目的登记顺序，容量到顶时按「最旧优先」腾位置。
        self._allowed_order: deque[tuple[str, str]] = deque()
        self._eviction_logged = False
        self.registry_capacity = ALAS_POLICY_REGISTRY_MAX_ENTRIES
        self._registry_saturated = False

    def _registry_size(self) -> int:
        return (
            len(self.callbacks)
            + len(self.denied_callbacks)
            + len(self.tasks)
            + len(self.denied_tasks)
        )

    def _allowed_size(self) -> int:
        return len(self.callbacks) + len(self.tasks)

    def _registry_can_add(self) -> bool:
        if self._registry_size() >= self.registry_capacity:
            self._registry_saturated = True
            return False
        return True

    def _reserve_allowed_slot(self) -> bool:
        """Reserve room for a newly advertised callback/task.

        The ceiling still bounds memory, but it must never turn an id the browser
        just received into an unknown one: such a widget stays visible and every
        click on it is dropped silently (``unknown_callback_id``).  When the
        registry is full the oldest allowed entry is released instead, so the
        controls of the current render keep working.
        """
        if self._allowed_size() < self.registry_capacity:
            return True
        self._registry_saturated = True
        if not self._eviction_logged:
            self._eviction_logged = True
            log.warning(
                "ALAS_POLICY_REGISTRY_EVICTED capacity=%s allowed=%s denied=%s "
                "reason=oldest_allowed_released",
                self.registry_capacity,
                self._allowed_size(),
                len(self.denied_callbacks) + len(self.denied_tasks),
            )
        return self._evict_oldest_allowed()

    def _evict_oldest_allowed(self) -> bool:
        while self._allowed_order:
            kind, identifier = self._allowed_order.popleft()
            if kind == "callback":
                if identifier in self.callbacks:
                    del self.callbacks[identifier]
                    return True
            elif identifier in self.tasks:
                del self.tasks[identifier]
                return True
        return False

    def _trim_allowed_order(self) -> None:
        """Compact the eviction queue once released ids start piling up in it."""
        if len(self._allowed_order) <= 2 * self.registry_capacity:
            return
        self._allowed_order = deque(
            [("callback", identifier) for identifier in self.callbacks]
            + [("task", identifier) for identifier in self.tasks]
        )

    def _forget_callback(self, callback_id: str) -> None:
        """Release a callback the browser can no longer use.

        The id is only forgotten, never marked denied: a stale click on it is then
        dropped like any other unknown id instead of closing the whole session
        (which a ``denied_callback_id`` would do).
        """
        if callback_id:
            self.callbacks.pop(callback_id, None)

    def _deny_callback(self, callback_id: str) -> None:
        self.callbacks.pop(callback_id, None)
        if callback_id in self.denied_callbacks:
            return
        if not self._registry_can_add():
            return
        self.denied_callbacks.add(callback_id)

    def _allow_callback(self, callback_id: str, capability: CallbackCapability) -> None:
        if callback_id in self.denied_callbacks or capability.restricted:
            self._deny_callback(callback_id)
            return
        current = self.callbacks.get(callback_id)
        if current is None:
            if not self._reserve_allowed_slot():
                self._deny_callback(callback_id)
                return
            self._allowed_order.append(("callback", callback_id))
        self.callbacks[callback_id] = current.merged(capability) if current else capability

    def _deny_task(self, task_id: str) -> None:
        self.tasks.pop(task_id, None)
        if task_id in self.denied_tasks:
            return
        if not self._registry_can_add():
            return
        self.denied_tasks.add(task_id)

    def _allow_task(self, task_id: str, registration: PyWebIOTaskRegistration) -> None:
        if task_id in self.denied_tasks or registration.restricted:
            self._deny_task(task_id)
            return
        current = self.tasks.get(task_id)
        if current is None:
            if not self._reserve_allowed_slot():
                self._deny_task(task_id)
                return
            self._allowed_order.append(("task", task_id))
        if current:
            registration = _merge_task_registration(current, registration)
        if registration.restricted:
            self._deny_task(task_id)
            return
        self.tasks[task_id] = registration

    def _release_superseded_pins(self, pins: dict[str, str]) -> None:
        """Drop the previous callback id of options that were rendered again."""
        for name, callback_id in pins.items():
            previous = self.pin_callbacks.get(name)
            if previous and previous != callback_id:
                self._forget_callback(previous)
            self.pin_callbacks[name] = callback_id
        self._trim_allowed_order()

    def observe_downstream(
        self,
        original,
        filtered,
        *,
        original_payload=_UNPARSED_WEBSOCKET_PAYLOAD,
        filtered_payload=_UNPARSED_WEBSOCKET_PAYLOAD,
    ) -> DownstreamObservation:
        if original_payload is not _UNPARSED_WEBSOCKET_PAYLOAD:
            _validate_policy_payload(original_payload)
        if filtered_payload is not _UNPARSED_WEBSOCKET_PAYLOAD and filtered_payload is not None:
            _validate_policy_payload(filtered_payload)
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

        # 同一个选项被重新渲染时，旧回调 id 在浏览器里已经失效：立即释放，别让它长期占着登记表。
        pins: dict[str, str] = {}
        if isinstance(filtered_payload, dict):
            _collect_pin_callback_ids(filtered_payload, pins)
        if pins:
            self._release_superseded_pins(pins)

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
        try:
            payload = _parse_websocket_message(message)
        except PayloadBudgetExceeded as exc:
            return self._decision(WebSocketMessageAction.CLOSE, exc.reason, "unknown")
        if not isinstance(payload, dict):
            # Stateful JSON objects use the capability state machine. Preserve the
            # legacy plain-text compatibility path, but fail closed for JSON that
            # parses to a scalar/array or starts like a malformed JSON object.
            if isinstance(message, bytes):
                reason = "invalid_binary"
            elif payload is not None or str(message or "").lstrip().startswith(("{", "[")):
                reason = "invalid_json"
            else:
                return websocket_message_decision(
                    message,
                    self.config_name,
                    can_run=self.can_run,
                    can_edit=self.can_edit,
                )
            return self._decision(WebSocketMessageAction.CLOSE, reason, "unknown")
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

__all__ = [
    "ProxyDecision", "WebSocketMessageAction", "WebSocketMessageDecision",
    "CallbackCapability", "PyWebIOTaskRegistration", "DownstreamObservation",
    "config_query_values", "bound_config_query_items", "proxy_decision",
    "is_readonly_static_request", "filter_user_json_payload", "filter_user_json_response",
    "filter_user_websocket_downstream", "websocket_message_decision",
    "websocket_message_allowed", "PyWebIOSessionPolicy",
]
