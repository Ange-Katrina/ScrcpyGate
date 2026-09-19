"""ScrcpyGate 新 Web 前端(Vite MPA)的页面路由与运行时配置注入。

前端页面源位于 static/pages/*.html(由前端仓库构建后经 tools/sync-to-fastapi.ps1 同步),
本模块负责:
- clean 路由(/login / /mirror /admin ...)按鉴权规则提供页面;
- 在 </head> 前注入 <meta name="csrf-token"> 与 window.ScrcpyGateConfig(endpoints 映射);
- 工作台页额外注入视频/控制所需的共享脚本。
"""

import json
import re
import secrets
from html import escape as escape_html
from pathlib import Path

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from . import i18n
from .static_assets import asset_url
from .version import get_version

STATIC_PAGES = Path(__file__).resolve().parent.parent / "static" / "pages"

# 无 src 的内联 <script>（含 <script id="..."> 形态）：服务时统一附加一次性 nonce。
_INLINE_SCRIPT_RE = re.compile(r"<script(?![^>]*\bsrc=)([^>]*)>")

# 品牌蓝圆角图标(Pinguo token #007aff), 供浏览器默认请求的 /favicon.ico 使用。
_FAVICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
    '<rect width="64" height="64" rx="14" fill="#007aff"/>'
    '<rect x="24" y="14" width="16" height="34" rx="4" fill="#ffffff"/>'
    '<rect x="27" y="18" width="10" height="26" rx="2" fill="#007aff"/>'
    "</svg>"
)

# resource.action -> v2 真实端点。响应形状归一化与特殊桥接在 static/shared/v2-adapter.js。
# 注意: ":name" 占位由前端 api.js 依据 opts.params 替换(如 :deviceId)。
ENDPOINTS_MAP = {
    # 会话与账户
    "auth.login": {"path": "/api/auth/login", "method": "POST"},
    "auth.challenge": {"path": "/api/auth/challenge", "method": "GET"},
    "auth.logout": {"path": "/api/auth/logout", "method": "POST"},
    "session.current": {"path": "/api/me", "method": "GET"},
    "account.password": {"path": "/api/account/password", "method": "PUT"},
    # 登录会话（"谁登录了系统"）：管理员在安全页看全部并可踢出；account.sessions 是用户自助入口。
    "login.sessions": {"path": "/api/admin/login-sessions", "method": "GET"},
    "login.sessions.revoke": {"path": "/api/admin/login-sessions/:sessionId", "method": "DELETE"},
    "account.sessions": {"path": "/api/security/sessions", "method": "GET"},
    "account.sessions.revoke": {"path": "/api/security/sessions/:sessionId", "method": "DELETE"},
    "login.guard": {"path": "/api/admin/login-guard", "method": "GET"},
    "login.guard.update": {"path": "/api/admin/login-guard", "method": "PUT"},
    "login.guard.unlock": {"path": "/api/admin/login-guard/unlock", "method": "POST"},
    "users.permissions": {"path": "/api/admin/permissions", "method": "GET"},
    "users.permissions.update": {"path": "/api/admin/users/:username/permissions", "method": "PUT"},
    # 工作台右上角通知中心（每个用户自己的收件箱）
    "notifications.list": {"path": "/api/notifications", "method": "GET"},
    "notifications.markRead": {"path": "/api/notifications/read", "method": "POST"},
    # 账户到期策略（提醒提前天数 / 到期自动停止 ALAS），读写 /api/admin/settings 的两个字段。
    "account.policy": {"path": "/api/admin/settings", "method": "GET"},
    "account.policy.update": {"path": "/api/admin/settings", "method": "PUT"},
    # 日志保存时长（/logs 页面）：与账户策略共用同一个系统设置端点，只读写字段时间不同。
    "logs.retention": {"path": "/api/admin/settings", "method": "GET"},
    "logs.retention.update": {"path": "/api/admin/settings", "method": "PUT"},
    # 设备 / 会话 / 控制
    "devices.list": {"path": "/api/devices", "method": "GET"},
    "sessions.list": {"path": "/api/devices", "method": "GET"},
    "sessions.create": {"path": "/api/devices/:deviceId/mirror/start", "method": "POST"},
    "sessions.stop": {"path": "/api/devices/:deviceId/mirror/stop", "method": "POST"},
    "sessions.action": {"path": "/api/devices/:deviceId/control/action", "method": "POST"},
    "sessions.disconnect-all": {"path": "/api/admin/sessions/disconnect-all", "method": "POST"},
    "sessions.control.acquire": {"path": "/api/devices/:deviceId/control/acquire", "method": "POST"},
    "sessions.control.takeover": {"path": "/api/devices/:deviceId/control/takeover", "method": "POST"},
    "sessions.control.release": {"path": "/api/devices/:deviceId/control/release", "method": "POST"},
    "sessions.control.transfer": {"path": "/api/admin/devices/:deviceId/control/transfer", "method": "POST"},
    "workbench.snapshot": {"path": "/api/workbench/snapshot", "method": "GET"},
    # 画质
    "quality.config": {"path": "/api/video/preferences", "method": "GET"},
    "quality.update": {"path": "/api/video/preferences", "method": "PUT"},
    # 全屏画质：切到后台配置的「全屏预设」/ 退出全屏回到用户自己的画质（仅本次会话生效）
    "mirror.fullscreen": {"path": "/api/devices/:deviceId/mirror/settings", "method": "PUT"},
    "quality.presets.list": {"path": "/api/admin/video/presets", "method": "GET"},
    "quality.presets.create": {"path": "/api/admin/video/presets", "method": "POST"},
    "quality.presets.update": {"path": "/api/admin/video/presets/:presetId", "method": "PUT"},
    "quality.presets.delete": {"path": "/api/admin/video/presets/:presetId", "method": "DELETE"},
    "quality.admin.config": {"path": "/api/admin/video", "method": "GET"},
    "quality.admin.update": {"path": "/api/admin/video", "method": "PUT"},
    "quality.status": {"path": "/api/admin/video/status", "method": "GET"},
    "quality.clients": {"path": "/api/admin/video/status", "method": "GET"},
    "quality.reset": {"path": "/api/admin/video", "method": "PUT"},
    "quality.restart": {"path": "/api/admin/video/restart", "method": "POST"},
    # 投屏管理：工作台功能开关（底部控制栏 / 顶部状态条与通知，按角色）
    "workbench.features": {"path": "/api/admin/workbench", "method": "GET"},
    "workbench.features.update": {"path": "/api/admin/workbench", "method": "PUT"},
    "workbench.features.reset": {"path": "/api/admin/workbench/reset", "method": "POST"},
    # ALAS(工作台)
    "alas.configs": {"path": "/api/alas/configs", "method": "GET"},
    "alas.status": {"path": "/api/alas/status", "method": "GET"},
    "alas.toggle": {"path": "/api/alas/toggle", "method": "POST"},
    "alas.exitGuard": {"path": "/api/alas/exit-guard", "method": "GET"},
    "alas.exitGuard.update": {"path": "/api/alas/exit-guard", "method": "PUT"},
    # 管理后台
    "dashboard.overview": {"path": "/api/admin/dashboard/snapshot", "method": "GET"},
    "dashboard.overview.alas": {"path": "/api/admin/overview/alas", "method": "GET"},
    "devices.check": {"path": "/api/admin/devices/:id/adb/test", "method": "POST"},
    "devices.create": {"path": "/api/admin/devices", "method": "PUT"},
    "devices.update": {"path": "/api/admin/devices", "method": "PUT"},
    "devices.delete": {"path": "/api/admin/devices/:id", "method": "DELETE"},
    "devices.permissions.update": {"path": "/api/admin/permissions", "method": "PUT"},
    "devices.alas.bind": {"path": "/api/admin/alas/permissions", "method": "PUT"},
    "permissions.catalog": {"path": "/api/admin/permissions", "method": "GET"},
    "users.list": {"path": "/api/admin/users", "method": "GET"},
    "users.create": {"path": "/api/admin/users", "method": "PUT"},
    "users.update": {"path": "/api/admin/users", "method": "PUT"},
    "users.delete": {"path": "/api/admin/users/:id", "method": "DELETE"},
    "users.reset-password": {"path": "/api/admin/users", "method": "PUT"},
    "logs.list": {"path": "/api/admin/runtime-logs", "method": "GET"},
    "logs.audit": {"path": "/api/admin/logs", "method": "GET"},
    # 访问记录（VIS）：匿名/已认证都统计；明细与汇总都只对管理员开放。
    "access.summary": {"path": "/api/admin/access/summary", "method": "GET"},
    "access.records": {"path": "/api/admin/access/records", "method": "GET"},
    "access.hints": {"path": "/api/admin/access/hints", "method": "GET"},
    "access.status": {"path": "/api/admin/access/status", "method": "GET"},
    "ban.list": {"path": "/api/admin/ip-bans", "method": "GET"},
    "ban.create": {"path": "/api/admin/ip-bans", "method": "POST"},
    "ban.lift": {"path": "/api/admin/ip-bans/:ip", "method": "DELETE"},
    "ban.events": {"path": "/api/admin/ip-bans/:ip/events", "method": "GET"},
    "geo.status": {"path": "/api/admin/geo/status", "method": "GET"},
    "geo.check": {"path": "/api/admin/geo/check", "method": "POST"},
    "geo.simulate": {"path": "/api/admin/geo/simulate", "method": "GET"},
    "geo.preview": {"path": "/api/admin/geo/preview", "method": "POST"},
    "access.export": {"path": "/api/admin/access/export", "method": "POST"},
    "logs.audit.detail": {"path": "/api/admin/logs/:id", "method": "GET"},
    "alerts.list": {"path": "/api/admin/alerts", "method": "GET"},
    "alerts.resolve": {"path": "/api/admin/alerts/:id/resolve", "method": "POST"},
    "admin.dashboard.snapshot": {"path": "/api/admin/dashboard/snapshot", "method": "GET"},
    # 系统更新检查（只读）：后台只显示「有没有更新」，应用更新在宿主机跑 deploy.sh --update。
    "system.update": {"path": "/api/admin/update-check", "method": "GET"},
    "logs.export": {"path": "/api/admin/logs/export", "method": "POST"},
    "logs.integrity": {"path": "/api/admin/logs/integrity-check", "method": "POST"},
    "alas.overview": {"path": "/api/admin/alas/permissions", "method": "GET"},
    "alas.permissions.catalog": {"path": "/api/admin/alas/permissions", "method": "GET"},
    "alas.catalog": {"path": "/api/admin/alas/configs", "method": "GET"},
    "alas.settings.update": {"path": "/api/admin/alas", "method": "PUT"},
    "alas.config.action": {"path": "/api/admin/alas/toggle", "method": "POST"},
    "alas.relations.update": {"path": "/api/admin/alas/permissions", "method": "PUT"},
    "alas.relations.create": {"path": "/api/admin/alas/permissions", "method": "PUT"},
    "alas.relations.delete": {"path": "/api/admin/alas/permissions", "method": "PUT"},
    "alas.relations.bulkUpdate": {"path": "/api/admin/alas/permissions", "method": "PUT"},
    "alas.connection.check": {"path": "/api/admin/alas/check", "method": "POST"},
    "alas.features": {"path": "/api/admin/alas/features", "method": "GET"},
    "alas.features.update": {"path": "/api/admin/alas/features", "method": "PUT"},
    "alas.visibility": {"path": "/api/admin/alas/visibility", "method": "GET"},
    "alas.visibility.update": {"path": "/api/admin/alas/visibility", "method": "PUT"},
    "alas.config.status": {"path": "/api/admin/alas", "method": "GET"},
}

# 页面 -> (文件名, 是否仅管理员)
PAGE_ROUTES = {
    "/login": ("login.html", False),
    "/": ("mirror.html", False),
    "/mirror": ("mirror.html", False),
    "/admin": ("admin.html", True),
    "/devices": ("devices.html", True),
    "/users": ("users.html", True),
    "/quality": ("quality.html", True),
    "/mirror-admin": ("mirror-admin.html", True),
    "/alas": ("alas.html", True),
    "/security": ("security.html", True),
    "/logs": ("logs.html", True),
}

_LEGACY_ASSET_RE = re.compile(
    r"(?<!/static)(?P<prefix>\.\./|\./|/)(?P<root>shared|vendor|css)/(?P<file>[^\"'\s?<>]+)(?:\?v=[A-Za-z0-9_-]+)?"
)
_STATIC_ASSET_RE = re.compile(
    r"/static/(?P<file>(?:shared|vendor|css)/[^\"'\s?<>]+)(?:\?v=[A-Za-z0-9_-]+)?"
)


def canonicalize_asset_paths(html: str) -> str:
    """Rewrite legacy relative asset references at the single page boundary."""
    def replace(match: re.Match[str]) -> str:
        return asset_url(f"{match.group('root')}/{match.group('file')}")

    html = _LEGACY_ASSET_RE.sub(replace, html)
    return _STATIC_ASSET_RE.sub(lambda match: asset_url(match.group("file")), html)


def _script_json(value: object) -> str:
    """Serialize JSON so values cannot terminate the surrounding script tag."""
    return (
        json.dumps(value, ensure_ascii=False)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


# 每页额外注入的脚本(在页面自身脚本之前执行)。资源版本由内容生成，避免手写 hash 失效。
AUDIT_MAPPING_SCRIPT_TAG = f'<script src="{asset_url("shared/audit-mapping.js")}"></script>'
DASHBOARD_STATE_SCRIPT_TAG = f'<script src="{asset_url("shared/dashboard-state.js")}"></script>'
V2_ADAPTER_SCRIPT_TAG = f'<script src="{asset_url("shared/v2-adapter.js")}"></script>'
# 宫格视图与适配器共用同一份 Raw v2 解析器；必须在适配器之前加载。
RAW_V2_SCRIPT_TAG = f'<script src="{asset_url("shared/raw-v2.js")}"></script>'
MIRROR_GRID_SCRIPT_TAG = f'<script src="{asset_url("shared/mirror-grid.js")}"></script>'
# 登录页验证码：工作量证明求解器（WebCrypto），需在登录页脚本之前加载。
POW_SOLVER_SCRIPT_TAG = f'<script src="{asset_url("shared/pow-solver.js")}"></script>'
# 页面在线标记：所有已登录页面共用，供 ALAS「退出后检测」判定在线/离线。
PRESENCE_SCRIPT_TAG = f'<script src="{asset_url("shared/presence.js")}"></script>'
# 账户到期提醒弹窗：所有已登录页面共用（登录页没有会话，不加载）。
EXPIRY_NOTICE_STYLE_TAG = f'<link rel="stylesheet" href="{asset_url("css/expiry-notice.css")}">'
EXPIRY_NOTICE_SCRIPT_TAG = f'<script src="{asset_url("shared/expiry-notice.js")}"></script>'
PAGE_RUNTIME_SCRIPT_TAG = f'<script src="{asset_url("shared/page-runtime.js")}"></script>'
PAGE_RUNTIME_STYLE_TAG = f'<link rel="stylesheet" href="{asset_url("css/page-runtime.css")}">'
# 统一开关组件（投屏管理样式）：所有页面共用一份，页面不再各写一套开关。
SWITCH_STYLE_TAG = f'<link rel="stylesheet" href="{asset_url("css/switch.css")}">'
# 分段控件（画质页与投屏管理页共用一份）。
SEGMENTED_STYLE_TAG = f'<link rel="stylesheet" href="{asset_url("css/segmented.css")}">'
# Dashboard code binds to elements in the page body.  Pages are assembled by
# injecting these assets into ``<head>``, so defer execution until parsing has
# completed; this also keeps the dashboard vocabulary available before the
# deferred page controller runs.
ADMIN_DASHBOARD_SCRIPT_TAG = f'<script defer src="{asset_url("shared/admin-dashboard.js")}"></script>'
ADMIN_DASHBOARD_STYLE_TAG = f'<link rel="stylesheet" href="{asset_url("css/admin-dashboard.css")}">'
ADMIN_PAGE_SCRIPT_TAG = f'<script defer src="{asset_url("shared/admin-page.js")}"></script>'
ADMIN_SHELL_SCRIPT_TAG = f'<script defer src="{asset_url("shared/admin-shell.js")}"></script>'
ADMIN_PAGE_STYLE_TAG = f'<link rel="stylesheet" href="{asset_url("css/admin-page.css")}">'
# 管理后台的公共外壳层（设计令牌 + 侧边栏/顶栏/内容区 + 面板与表格基元）。
# 兄弟页面把这一层各自打包进 <page>-page.css；/security 只带页面自身的规则，
# 因此显式引用这份共享样式。
ADMIN_SHELL_STYLE_TAG = f'<link rel="stylesheet" href="{asset_url("css/admin-shell.css")}">'
PAGE_STYLE_TAGS = {
    name: f'<link rel="stylesheet" href="{asset_url(f"css/{name}-page.css")}">'
    for name in ("alas", "devices", "logs", "mirror", "mirror-admin", "quality", "security", "users", "login")
}
PAGE_SCRIPT_TAGS = {
    name: f'<script defer src="{asset_url(f"shared/{name}-page.js")}"></script>'
    for name in ("alas", "devices", "logs", "mirror", "mirror-admin", "quality", "security", "users", "login")
}
ACCESS_STATUS_SCRIPT_TAG = f'<script src="{asset_url("shared/access-status.js")}"></script>'
COMMON_PAGE_ASSETS = [PAGE_RUNTIME_STYLE_TAG, PAGE_RUNTIME_SCRIPT_TAG, ACCESS_STATUS_SCRIPT_TAG, SWITCH_STYLE_TAG, SEGMENTED_STYLE_TAG]
# 访问记录面板（VIS）：只在安全机制页使用，因此不放进 COMMON_PAGE_ASSETS。
ACCESS_LOG_SCRIPT_TAG = f'<script defer src="{asset_url("shared/access-log-panel.js")}"></script>'
# IP 封禁面板（BAN）：同样只在安全机制页使用。
BAN_SCRIPT_TAG = f'<script defer src="{asset_url("shared/ban-panel.js")}"></script>'
# 地域限制面板（GEO）：同样只在安全机制页使用。
GEO_SCRIPT_TAG = f'<script defer src="{asset_url("shared/geo-panel.js")}"></script>'
# 读模型预取清单：这些页面在 <head> 里同步发起读请求，api.js 复用其 Response。
# 只放「首屏必读、且已确认是 GET」的接口；写接口不能预取。
PAGE_PREFETCH_URLS = {
    "alas.html": [
        "/api/admin/alas/permissions",
        "/api/admin/alas",
        "/api/admin/permissions",
    ],
}
READ_PREFETCH_SCRIPT_TAG = f'<script src="{asset_url("shared/read-prefetch.js")}"></script>'
EXTRA_SCRIPTS = {
    "mirror.html": COMMON_PAGE_ASSETS + [
        PAGE_STYLE_TAGS["mirror"],
        f'<script src="{asset_url("vendor/jmuxer.min.js")}"></script>',
        f'<script src="{asset_url("shared/input.js")}"></script>',
        f'<script src="{asset_url("shared/mirror-notifications.js")}"></script>',
        f'<script src="{asset_url("shared/mirror-devices.js")}"></script>',
        f'<script src="{asset_url("shared/mirror-controls.js")}"></script>',
        # 显示状态层（画面方向自动摆正 / 源尺寸与可用空间的换算）必须在页面控制器之前。
        f'<script src="{asset_url("shared/display-control.js")}"></script>',
        RAW_V2_SCRIPT_TAG,
        ADMIN_DASHBOARD_SCRIPT_TAG,
        AUDIT_MAPPING_SCRIPT_TAG,
        DASHBOARD_STATE_SCRIPT_TAG,
        V2_ADAPTER_SCRIPT_TAG,
        MIRROR_GRID_SCRIPT_TAG,
        PAGE_SCRIPT_TAGS["mirror"],
    ],
    "admin.html": COMMON_PAGE_ASSETS + [
        ADMIN_DASHBOARD_STYLE_TAG,
        ADMIN_PAGE_STYLE_TAG,
        ADMIN_SHELL_SCRIPT_TAG,
        ADMIN_DASHBOARD_SCRIPT_TAG,
        AUDIT_MAPPING_SCRIPT_TAG,
        DASHBOARD_STATE_SCRIPT_TAG,
        V2_ADAPTER_SCRIPT_TAG,
        ADMIN_PAGE_SCRIPT_TAG,
    ],
    "devices.html": COMMON_PAGE_ASSETS + [PAGE_STYLE_TAGS["devices"], ADMIN_DASHBOARD_SCRIPT_TAG, AUDIT_MAPPING_SCRIPT_TAG, DASHBOARD_STATE_SCRIPT_TAG, V2_ADAPTER_SCRIPT_TAG, ADMIN_SHELL_SCRIPT_TAG, PAGE_SCRIPT_TAGS["devices"]],
    "users.html": COMMON_PAGE_ASSETS + [PAGE_STYLE_TAGS["users"], ADMIN_DASHBOARD_SCRIPT_TAG, AUDIT_MAPPING_SCRIPT_TAG, DASHBOARD_STATE_SCRIPT_TAG, V2_ADAPTER_SCRIPT_TAG, ADMIN_SHELL_SCRIPT_TAG, PAGE_SCRIPT_TAGS["users"]],
    "alas.html": COMMON_PAGE_ASSETS + [PAGE_STYLE_TAGS["alas"], ADMIN_DASHBOARD_SCRIPT_TAG, AUDIT_MAPPING_SCRIPT_TAG, DASHBOARD_STATE_SCRIPT_TAG, V2_ADAPTER_SCRIPT_TAG, ADMIN_SHELL_SCRIPT_TAG, PAGE_SCRIPT_TAGS["alas"]],
    "quality.html": COMMON_PAGE_ASSETS + [PAGE_STYLE_TAGS["quality"], ADMIN_DASHBOARD_SCRIPT_TAG, AUDIT_MAPPING_SCRIPT_TAG, DASHBOARD_STATE_SCRIPT_TAG, V2_ADAPTER_SCRIPT_TAG, ADMIN_SHELL_SCRIPT_TAG, PAGE_SCRIPT_TAGS["quality"]],
    "security.html": COMMON_PAGE_ASSETS + [ADMIN_SHELL_STYLE_TAG, PAGE_STYLE_TAGS["security"], V2_ADAPTER_SCRIPT_TAG, ADMIN_SHELL_SCRIPT_TAG, PAGE_SCRIPT_TAGS["security"], ACCESS_LOG_SCRIPT_TAG, BAN_SCRIPT_TAG, GEO_SCRIPT_TAG, POW_SOLVER_SCRIPT_TAG],
    "mirror-admin.html": COMMON_PAGE_ASSETS + [ADMIN_SHELL_STYLE_TAG, PAGE_STYLE_TAGS["mirror-admin"], V2_ADAPTER_SCRIPT_TAG, ADMIN_SHELL_SCRIPT_TAG, PAGE_SCRIPT_TAGS["mirror-admin"]],
    "logs.html": COMMON_PAGE_ASSETS + [PAGE_STYLE_TAGS["logs"], ADMIN_DASHBOARD_SCRIPT_TAG, AUDIT_MAPPING_SCRIPT_TAG, DASHBOARD_STATE_SCRIPT_TAG, V2_ADAPTER_SCRIPT_TAG, ADMIN_SHELL_SCRIPT_TAG, PAGE_SCRIPT_TAGS["logs"]],
    "login.html": COMMON_PAGE_ASSETS + [PAGE_STYLE_TAGS["login"], AUDIT_MAPPING_SCRIPT_TAG, V2_ADAPTER_SCRIPT_TAG, POW_SOLVER_SCRIPT_TAG, PAGE_SCRIPT_TAGS["login"]],
}

# Load shared administration refinements after the legacy page styles.
for _page in ("admin", "devices", "users", "alas", "quality", "mirror-admin", "logs"):
    EXTRA_SCRIPTS[f"{_page}.html"].append(
        f'<link rel="stylesheet" href="{asset_url("css/admin-workspace.css")}">'
    )


def _injection(request: Request, file: str, nonce: str = "") -> str:
    # 延迟导入: 测试中 app.security 会被重置重建, 模块级绑定会指向旧模块。
    from . import security

    session = security.get_current_session(request)
    csrf = session["csrf_token"] if session else ""
    meta = (
        f'<meta name="csrf-token" content="{escape_html(str(csrf), quote=True)}">'
        if csrf
        else ""
    )
    config = {
        "baseUrl": "",
        "locale": i18n.current_locale(),
        "endpoints": ENDPOINTS_MAP,
    }
    nonce_attr = f' nonce="{escape_html(nonce, quote=True)}"' if nonce else ""
    config_json = _script_json(config)
    script = "<script" + nonce_attr + ">window.ScrcpyGateConfig = " + config_json + ";</script>"
    extras = "".join(EXTRA_SCRIPTS.get(file, []))
    if file != "login.html":
        # 已登录页面统一带在线标记脚本（登录页没有会话，连接必然被拒）
        # 与账户到期提醒弹窗（同样只在有会话时才有意义）。
        extras = PRESENCE_SCRIPT_TAG + EXPIRY_NOTICE_STYLE_TAG + EXPIRY_NOTICE_SCRIPT_TAG + extras
    return meta + script + extras


def _prefetch_head_injection(file: str, nonce: str = "") -> str:
    """页面读模型预取：清单 + 预取脚本，注入到 ``<head>`` 之后。

    必须紧跟在 ``<head>`` 后：这样它在页面自己的同步脚本之前执行，请求与 JS 加载并行，
    实测比放到 ``</head>`` 前早 ~150ms 发出（api.js 的 request() 之后复用这个响应）。
    """
    urls = PAGE_PREFETCH_URLS.get(file, ())
    if not urls:
        return ""
    nonce_attr = f' nonce="{escape_html(nonce, quote=True)}"' if nonce else ""
    return (
        "<script" + nonce_attr + ">window.ScrcpyGatePrefetchUrls = " + _script_json(list(urls)) + ";</script>"
        + READ_PREFETCH_SCRIPT_TAG
    )


def _make_handler(file: str, admin_only: bool):
    async def handler(request: Request):
        # 延迟导入: 测试中 app.security 会被重置重建, 模块级绑定会指向旧模块。
        from . import security

        user = security.get_current_user(request)
        if file == "login.html":
            if user:
                return RedirectResponse("/", status_code=302)
        else:
            if not user:
                return RedirectResponse("/login", status_code=302)
            if admin_only and user.get("role") != "admin":
                return RedirectResponse("/", status_code=302)
        path = STATIC_PAGES / file
        if not path.exists():
            return HTMLResponse("页面资源缺失: " + file, status_code=404)
        # 每响应一次性 nonce：页面构建产物中的内联脚本在服务时附加，CSP 据此去掉 unsafe-inline。
        nonce = secrets.token_urlsafe(16)
        html = path.read_text(encoding="utf-8")
        version = escape_html(get_version())
        label = f"v{version}" if version != "dev" else version
        html = html.replace('<div class="side-version">ScrcpyGate</div>', f'<div class="side-version" data-product-version>ScrcpyGate {label}</div>')
        if file == "login.html":
            html = html.replace("© ScrcpyGate", f"© ScrcpyGate · {label}")
        html = canonicalize_asset_paths(html)
        html = _INLINE_SCRIPT_RE.sub(lambda match: f'<script{match.group(1)} nonce="{nonce}">', html)
        head_injection = _prefetch_head_injection(file, nonce)
        head_tail = head_injection
        if head_injection:
            # 注入到 <head> 之后，与页面自己的同步脚本并行；没有 <head> 时 inserted=0，
            # 退回 </head> 前注入（功能不变，只是晚一点发出请求）。
            html, inserted = re.subn(r"<head[^>]*>", lambda match: match.group(0) + head_injection, html, count=1)
            if inserted:
                head_tail = ""
        html = html.replace("</head>", _injection(request, file, nonce) + head_tail + "</head>", 1)
        request.state.csp_nonce = nonce
        response = HTMLResponse(html)
        if file == "login.html":
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
        return response

    return handler


def install(app) -> None:
    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        response = Response(_FAVICON_SVG, media_type="image/svg+xml")
        response.headers["Cache-Control"] = "public, max-age=86400"
        return response

    for route, (file, admin_only) in PAGE_ROUTES.items():
        handler = _make_handler(file, admin_only)
        handler.__name__ = "page_" + (route.strip("/") or "index")
        app.add_api_route(route, handler, methods=["GET"], response_class=HTMLResponse)
