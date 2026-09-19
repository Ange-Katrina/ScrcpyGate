"""ALAS Embed HTML shell and downstream content rewriting."""

from __future__ import annotations

import html as html_utils
import json
import logging
import re
from html import escape
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

from . import alas_gateway, alas_visibility, i18n
from .alas_embed_policy import (
    ADB_ENDPOINT_RE,
    ALAS_EMBED_PREFIX,
    CONFIG_QUERY_KEYS,
    MANAGEMENT_MARKERS,
    SCRCPYGATE_CONTEXT_QUERY_KEYS,
    SENSITIVE_DEVICE_ENDPOINT_PLACEHOLDER,
)
from .static_assets import asset_url

log = logging.getLogger("webscrcpy.alas_embed")

def embed_shell_html(title: str, iframe_src: str, message: str = "") -> str:
    """生成 ScrcpyGate ALAS iframe 外壳页面。"""
    safe_title = escape(title)
    safe_src = escape(iframe_src, quote=True)
    safe_message = escape(message)
    safe_toolbar_label = escape(i18n.translate("alas.shell.toolbar_label"), quote=True)
    safe_connecting = escape(i18n.translate("alas.shell.connecting"))
    safe_refresh = escape(i18n.translate("common.actions.refresh"))
    safe_open_new_window = escape(i18n.translate("common.actions.open_new_window"))
    safe_back = escape(i18n.translate("common.actions.back"))
    safe_loading_title = escape(i18n.translate("alas.shell.loading_title"))
    safe_loading_detail = escape(i18n.translate("alas.shell.loading_detail"))
    safe_retry = escape(i18n.translate("common.actions.retry"))
    safe_return_scrcpygate = escape(i18n.translate("common.actions.return_scrcpygate"))
    safe_locale_label = escape(i18n.translate("common.language.label"), quote=True)
    safe_zh_label = escape(i18n.translate("common.language.zh_cn"))
    safe_en_label = escape(i18n.translate("common.language.en_us"))
    safe_noscript_title = escape(i18n.translate("alas.shell.noscript_title"))
    safe_noscript_detail = escape(i18n.translate("alas.shell.noscript_detail"))
    shell_assets = {
        "theme": escape(asset_url("js/theme-init.js"), quote=True),
        "tokens": escape(asset_url("css/ui-tokens.css"), quote=True),
        "components": escape(asset_url("css/ui-components.css"), quote=True),
        "shell": escape(asset_url("css/alas-shell.css"), quote=True),
        "i18n": escape(asset_url("js/i18n.js"), quote=True),
        "core": escape(asset_url("js/ui-core.js"), quote=True),
        "alas": escape(asset_url("js/alas-shell.js"), quote=True),
        "icons": escape(asset_url("icons/lucide.svg"), quote=True),
    }
    locale_payload = i18n.browser_payload_json()
    selected_locale = i18n.current_locale()
    selected_zh = " selected" if selected_locale == "zh-CN" else ""
    selected_en = " selected" if selected_locale == "en-US" else ""
    return f"""<!doctype html>
<html lang="{i18n.current_locale()}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover, interactive-widget=resizes-content">
  <meta name="theme-color" content="#111315">
  <meta name="scrcpygate-icons" content="{shell_assets['icons']}">
  <title>{safe_title}</title>
  <script src="{shell_assets['theme']}"></script>
  <link rel="stylesheet" href="{shell_assets['tokens']}">
  <link rel="stylesheet" href="{shell_assets['components']}">
  <link rel="stylesheet" href="{shell_assets['shell']}">
  <script type="application/json" id="scrcpygate-i18n">{locale_payload}</script>
  <script src="{shell_assets['i18n']}" defer></script>
  <script src="{shell_assets['core']}" defer></script>
  <script src="{shell_assets['alas']}" defer></script>
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
    <div class="alas-shell-toolbar" aria-label="{safe_toolbar_label}">
      <span id="loadStatus" class="alas-shell-status" data-state="loading" role="status" aria-live="polite">{safe_connecting}</span>
      <div class="ui-locale-picker compact-locale-picker" title="{safe_locale_label}">
        <svg class="ui-icon ui-locale-mark" aria-hidden="true"><use href="{shell_assets['icons']}#languages"></use></svg>
        <span class="ui-sr-only">{safe_locale_label}</span>
        <select id="localeSelect" data-ui-locale-select aria-label="{safe_locale_label}">
          <option value="zh-CN"{selected_zh}>{safe_zh_label}</option>
          <option value="en-US"{selected_en}>{safe_en_label}</option>
        </select>
      </div>
      <button id="refreshFrame" class="alas-shell-button" type="button">{safe_refresh}</button>
      <a class="alas-shell-button" href="{safe_src}" target="_blank" rel="noopener noreferrer">{safe_open_new_window}</a>
      <a class="alas-shell-button" href="/">{safe_back}</a>
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
        <h1 id="stateTitle">{safe_loading_title}</h1>
        <p id="stateDetail">{safe_loading_detail}</p>
        <div id="stateActions" class="alas-shell-state-actions" hidden>
          <button id="retryFrame" class="alas-shell-button primary" type="button">{safe_retry}</button>
          <a class="alas-shell-button" href="/">{safe_return_scrcpygate}</a>
        </div>
      </div>
    </section>
    <noscript>
      <section class="alas-shell-state is-visible" role="alert">
        <div class="alas-shell-state-card">
          <h1>{safe_noscript_title}</h1>
          <p>{safe_noscript_detail}</p>
          <div class="alas-shell-state-actions">
            <a class="alas-shell-button primary" href="{safe_src}" target="_blank" rel="noopener noreferrer">{safe_open_new_window}</a>
            <a class="alas-shell-button" href="/">{safe_return_scrcpygate}</a>
          </div>
        </div>
      </section>
    </noscript>
  </main>
</body>
</html>"""


def denied_page_html(message: str, redirect_url: str = "/alas/embed/", seconds: int = 3) -> str:
    """Render a friendly ALAS embed denial page inside the iframe."""
    safe_message = escape(message or i18n.translate("alas.denied.default_message"))
    safe_url = escape(redirect_url or "/alas/embed/", quote=True)
    safe_seconds = max(1, min(30, int(seconds or 3)))
    safe_page_title = escape(i18n.translate("alas.denied.page_title"))
    safe_heading = escape(i18n.translate("alas.denied.heading"))
    safe_return_alas = escape(i18n.translate("alas.denied.return_alas"))
    safe_return_scrcpygate = escape(i18n.translate("common.actions.return_scrcpygate"))
    auto_return_template = i18n.translate("alas.denied.auto_return")
    safe_auto_return_template = escape(auto_return_template, quote=True)
    safe_auto_return = escape(i18n.translate("alas.denied.auto_return", seconds=safe_seconds))
    return f"""<!doctype html>
<html lang="{i18n.DEFAULT_LOCALE}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover, interactive-widget=resizes-content">
  <meta http-equiv="refresh" content="{safe_seconds};url={safe_url}">
  <title>{safe_page_title}</title>
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
    <h1>{safe_heading}</h1>
    <p>{safe_message}</p>
    <div class="actions">
      <a href="{safe_url}">{safe_return_alas}</a>
      <a class="secondary" href="/" target="_top">{safe_return_scrcpygate}</a>
    </div>
    <div id="countdown" class="count" data-template="{safe_auto_return_template}">{safe_auto_return}</div>
  </main>
  <script>
    (function() {{
      var left = {safe_seconds};
      var target = {json.dumps(redirect_url or "/alas/embed/")};
      var node = document.getElementById("countdown");
      var template = node ? node.getAttribute("data-template") : "{{seconds}}";
      window.setInterval(function() {{
        left -= 1;
        if (node) node.textContent = template.replace("{{seconds}}", String(Math.max(left, 0)));
        if (left <= 0) window.location.replace(target);
      }}, 1000);
    }})();
  </script>
</body>
</html>"""
def _filter_visible_html_text(html: str) -> str:
    """Redact visible static HTML text; scripts keep syntax but leak no endpoints, styles stay raw."""
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
        if raw_text_element == "script":
            # 脚本体可能内嵌 PyWebIO 初始状态等数据：掩盖 endpoint（出现在字符串字面量中，
            # 替换不破坏语法），但不做管理文案移除以免改坏 JS 代码。
            filtered.append(ADB_ENDPOINT_RE.sub(SENSITIVE_DEVICE_ENDPOINT_PLACEHOLDER, token))
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
class _BoundAssetHTMLRewriter(HTMLParser):
    """Rewrite initial Runtime resource URLs before browser scripts can run."""

    URL_ATTRIBUTES = frozenset({"src", "href", "action"})

    def __init__(
        self,
        config_name: str,
        device_context: str,
        page_path: str = "",
        gateway_context: str = "",
    ):
        super().__init__(convert_charrefs=False)
        self.config_name = str(config_name or "")
        self.device_context = str(device_context or "")
        self.page_path = str(page_path or "")
        self.gateway_context = str(gateway_context or "")
        self.parts: list[str] = []

    def _rewrite_url(self, value: str) -> str:
        raw = str(value or "")
        if not raw or raw.startswith(("#", "data:", "javascript:", "mailto:", "tel:")):
            return raw
        parsed = urlparse(raw)
        proxy_prefix = f"{ALAS_EMBED_PREFIX}/proxy"
        if parsed.scheme or parsed.netloc:
            # PyWebIO 页面默认从 jsDelivr CDN 加载资源; CDN 在部分网络环境
            # (国内/受限出口) 不可达时页面会弹 "Failed to load resource"。
            # 将其改写为 Runtime 本地资源并经代理提供, 彻底摆脱 CDN。
            local_path = _cdn_asset_local_path(raw)
            if not local_path:
                return raw
            path = local_path
            parsed = parsed._replace(scheme="", netloc="", path=path)
        else:
            path = parsed.path.replace("\\", "/")
            if path and not path.startswith("/"):
                base = "/" if not self.page_path else "/" + self.page_path.strip("/") + "/"
                path = urljoin(base, path)

        if path.startswith(proxy_prefix):
            proxy_path = path
        elif path.startswith("/"):
            proxy_path = f"{proxy_prefix}{path}"
        else:
            return raw

        query = parse_qs(parsed.query, keep_blank_values=True)
        pairs: list[tuple[str, str]] = []
        for key, values in query.items():
            if key.lower() in CONFIG_QUERY_KEYS or key.lower() in SCRCPYGATE_CONTEXT_QUERY_KEYS:
                continue
            pairs.extend((key, item) for item in values or [""])
        if self.config_name:
            pairs.append(("config", self.config_name))
        if self.device_context:
            pairs.append(("device_id", self.device_context))
        if self.gateway_context:
            pairs.append((alas_gateway.ALAS_CONTEXT_QUERY, self.gateway_context))
        return urlunparse(("", "", proxy_path, parsed.params, urlencode(pairs, doseq=True), parsed.fragment))

    def _append_tag(self, tag: str, attrs, *, closed: bool = False) -> None:
        rendered = [f"<{tag}"]
        for name, value in attrs:
            rendered.append(f" {name}")
            if value is not None:
                rewritten = self._rewrite_url(value) if name.lower() in self.URL_ATTRIBUTES else value
                rendered.append(f'="{html_utils.escape(str(rewritten), quote=True)}"')
        rendered.append("/>" if closed else ">")
        self.parts.append("".join(rendered))

    def handle_starttag(self, tag, attrs):
        self._append_tag(tag, attrs)

    def handle_startendtag(self, tag, attrs):
        self._append_tag(tag, attrs, closed=True)

    def handle_endtag(self, tag):
        self.parts.append(f"</{tag}>")

    def handle_data(self, data):
        self.parts.append(data)

    def handle_entityref(self, name):
        self.parts.append(f"&{name};")

    def handle_charref(self, name):
        self.parts.append(f"&#{name};")

    def handle_comment(self, data):
        self.parts.append(f"<!--{data}-->")

    def handle_decl(self, decl):
        self.parts.append(f"<!{decl}>")

    def handle_pi(self, data):
        self.parts.append(f"<?{data}>")

    def unknown_decl(self, data):
        """Re-emit declared sections (`<![CDATA[…]]>`, Office conditional sections).

        `html.parser` only calls `unknown_decl` and it already consumed the closing
        delimiter, so the family has to be inferred: CDATA-style sections end with
        `]]>`, the MS Office conditional sections (`<![if …]>`, `<![endif]>`) with `]>`.
        The previous override was named `handle_unknown_decl` — not a real hook — so
        every declared section was silently dropped from rewritten pages.
        """
        text = str(data or "")
        section_name = text.split("[", 1)[0].strip().upper()
        if section_name in {"CDATA", "IGNORE", "INCLUDE", "RCDATA", "TEMP"}:
            self.parts.append(f"<![{text}]]>")
        else:
            self.parts.append(f"<![{text}]>")

    def result(self) -> str:
        return "".join(self.parts)


def _cdn_asset_local_path(raw: str) -> str:
    """将 jsDelivr 上的 PyWebIO 静态资源地址映射为 Runtime 本地根路径。

    例如 https://cdn.jsdelivr.net/gh/xxx/PyWebIO-assets@v1.8.4/js/pywebio.min.js
    映射为 /js/pywebio.min.js(PyWebIO 以 cdn=false 提供资源时的本地布局)。
    """
    match = re.search(r"/PyWebIO-assets@[^/]+/(.+)$", str(raw or ""))
    if not match:
        return ""
    suffix = match.group(1).split("?")[0].replace("\\", "/")
    if suffix.startswith(("js/", "css/", "image/", "codemirror/")):
        return f"/{suffix}"
    return ""


def rewrite_bound_html_urls(
    html: str,
    config_name: str,
    device_context: str = "",
    page_path: str = "",
    gateway_context: str = "",
) -> str:
    """Rewrite initial Runtime asset URLs to absolute proxy paths.

    无论是否绑定配置都执行改写: 绑定用户附加 config/device_id 上下文,
    管理员等无绑定场景也使用绝对 /alas/embed/proxy/ 路径, 避免浏览器
    因页面基址(尾斜杠缺失、反向代理/WAF 路径规范化)把相对资源解析到错误地址。
    相对资源按被代理页面的目录(page_path)解析。
    """
    parser = _BoundAssetHTMLRewriter(config_name, device_context, page_path, gateway_context)
    try:
        parser.feed(str(html or ""))
        parser.close()
    except Exception:
        log.exception("ALAS_HTML_URL_REWRITE_FAILED")
        return str(html or "")
    return parser.result()


def filter_user_html(
    html: str,
    config_name: str,
    device_context: str = "",
    page_path: str = "",
    gateway_context: str = "",
    hidden_matches: list | tuple | None = None,
) -> str:
    """Prepare ALAS HTML for bound users.

    会改写页面内的 URL、注入绑定/点击门脚本，并在可见文本里遮蔽管理文案与设备地址
    （`<script>` 正文只做地址遮蔽，见 `_filter_visible_html_text`）。
    """
    filtered = _filter_visible_html_text(str(html or ""))
    if config_name and config_name not in filtered:
        escaped_config_name = html_utils.escape(config_name, quote=True)
        filtered = f"{filtered}<!-- bound ALAS config: {escaped_config_name} -->"
    filtered = rewrite_bound_html_urls(filtered, config_name, device_context, page_path, gateway_context)
    filtered = inject_bound_config_script(filtered, config_name, device_context, gateway_context)
    filtered = inject_feature_gate_script(filtered, hidden_matches)
    return inject_alas_ready_script(filtered)


def _feature_targets(hidden_matches: object) -> list[dict]:
    """Normalize hidden-feature input into [{key, match}] targets.

    Accepts plain strings (legacy text-only callers/tests) and mappings carrying
    the ALAS task key, which pinpoints a row through its
    ``div[style*="--menu-<key>--"]`` marker.
    """
    targets: list[dict] = []
    for item in hidden_matches or ():
        if isinstance(item, dict):
            key = str(item.get("key") or "").strip()
            match = re.sub(r"\s+", "", str(item.get("match") or ""))
        else:
            key = ""
            match = re.sub(r"\s+", "", str(item or ""))
        if key or match:
            targets.append({"key": key, "match": match})
    return targets


def inject_feature_gate_script(html: str, hidden_matches: object = None) -> str:
    """Hide admin-disabled ALAS page options.

    Each target is located by its ALAS task key first (``--menu-<key>--`` marker,
    exact row, no name collisions) and falls back to matching the visible text
    with whitespace compacted.  The ALAS web UI renders its options dynamically,
    so the injected script also re-runs on DOM mutations.
    """
    targets = _feature_targets(hidden_matches)
    original = str(html or "")
    if not targets or "data-scrcpygate-alas-features" in original:
        return original
    payload = (
        json.dumps(targets, ensure_ascii=False)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )
    script = """
<script data-scrcpygate-alas-features>
(function() {
  var targets = %s;
  if (!targets.length || typeof document === "undefined") return;
  function compact(value) { return String(value || "").replace(/\\s+/g, ""); }
  function skip(node) {
    var parent = node.parentElement;
    if (!parent) return true;
    var tag = parent.tagName;
    if (tag === "SCRIPT" || tag === "STYLE" || tag === "NOSCRIPT") return true;
    return !!(parent.closest && parent.closest("[data-scrcpygate-alas-features]"));
  }
  function hide(element) {
    if (!element || element.nodeType !== 1) return;
    if (element.hasAttribute("data-scrcpygate-feature-hidden")) return;
    // 只打标记，靠注入的样式表隐藏：直接改 element.style 会重写 style 属性，
    // 把 ALAS 自己的 --menu-<Task>-- 标记抹掉（那是它菜单逻辑用的）。
    element.setAttribute("data-scrcpygate-feature-hidden", "1");
  }
  function ensureStyle() {
    if (document.getElementById("scrcpygate-alas-feature-style")) return;
    var style = document.createElement("style");
    style.id = "scrcpygate-alas-feature-style";
    style.textContent = "[data-scrcpygate-feature-hidden]{display:none !important;}";
    (document.head || document.documentElement).appendChild(style);
  }
  function containerFor(node, needle) {
    var element = node.parentElement;
    var best = element;
    var limit = needle.length * 6 + 60;
    while (element && element !== document.body) {
      if (compact(element.textContent).length > limit) break;
      best = element;
      element = element.parentElement;
    }
    return best;
  }
  // ALAS 每行任务带 --menu-<Task>-- 标记，直接命中该行即可，不会误伤同名前缀项。
  function hideByKey(key) {
    if (!key) return false;
    var rows = document.querySelectorAll('[style*="--menu-' + key + '--"]');
    if (!rows.length) return false;
    for (var index = 0; index < rows.length; index += 1) hide(rows[index]);
    return true;
  }
  function scanText(match) {
    if (!match) return;
    var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null);
    var pending = [];
    var node;
    while ((node = walker.nextNode())) {
      var text = compact(node.nodeValue);
      if (!text || skip(node)) continue;
      if (text.indexOf(match) >= 0) pending.push(node);
    }
    for (var index = 0; index < pending.length; index += 1) hide(containerFor(pending[index], match));
  }
  function scan() {
    if (!document.body || !document.createTreeWalker) return;
    ensureStyle();
    for (var index = 0; index < targets.length; index += 1) {
      var target = targets[index];
      // 标记缺失（例如 ALAS 换版）时回退到文字匹配，保证仍能隐藏。
      if (hideByKey(target.key)) continue;
      scanText(target.match);
    }
  }
  var timer = null;
  function schedule() {
    if (timer) return;
    timer = setTimeout(function () { timer = null; try { scan(); } catch (_) {} }, 120);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", schedule, {once: true});
  else schedule();
  function observe() {
    try { new MutationObserver(schedule).observe(document.documentElement, {childList: true, subtree: true}); } catch (_) {}
  }
  if (document.documentElement) observe();
  else document.addEventListener("DOMContentLoaded", observe, {once: true});
})();
</script>""" % payload
    head_match = re.search(r"<head(?:\s[^>]*)?>", original, flags=re.IGNORECASE)
    if head_match:
        return f"{original[:head_match.end()]}{script}{original[head_match.end():]}"
    body_index = original.lower().rfind("</body>")
    if body_index >= 0:
        return f"{original[:body_index]}{script}{original[body_index:]}"
    return f"{original}{script}"


def inject_alas_ready_script(html: str) -> str:
    """Notify the ScrcpyGate shell after a proxied HTML document is ready."""
    original = str(html or "")
    if "data-scrcpygate-alas-ready" in original:
        return original
    guard = """
<script data-scrcpygate-access-guard>
(function() {
  var Native = window.WebSocket, fetchStatus = window.fetch && window.fetch.bind(window);
  if (!Native) return;
  var failures = 0, stopped = false, pending = null;
  function notify(message) {
    stopped = true;
    function show() {
      if (document.getElementById('scrcpygate-access-stopped')) return;
      var box = document.createElement('div');
      box.id = 'scrcpygate-access-stopped'; box.setAttribute('role', 'alert');
      box.style.cssText = 'position:fixed;inset:0;z-index:2147483647;background:#202020;color:white;padding:32px;font:16px sans-serif';
      box.textContent = message || '连接已停止 / Connection stopped. ';
      var retry = document.createElement('button'); retry.textContent = '重试 / Retry';
      retry.onclick = function() { location.reload(); };
      box.appendChild(retry); document.body.appendChild(box);
    }
    if (document.body) show(); else document.addEventListener('DOMContentLoaded', show, {once:true});
  }
  function diagnose() {
    if (!fetchStatus || pending) return;
    var controller = new AbortController();
    var timer = setTimeout(function() { controller.abort(); }, 5000);
    pending = fetchStatus('/alas/access-status', {credentials:'same-origin',cache:'no-store',signal:controller.signal,headers:{Accept:'application/json'}})
      .then(function(response) {
        if ([401,403,429,503].indexOf(response.status) < 0) return;
        return response.json().catch(function() { return {}; }).then(function(data) {
          notify(typeof data.detail === 'string' ? data.detail : '访问被拒绝 / Access denied. ');
        });
      }).catch(function() {}).then(function() { clearTimeout(timer); pending = null; });
  }
  window.WebSocket = function(url, protocols) {
    if (stopped || failures >= 6) { notify(); throw new Error('access_connection_stopped'); }
    var socket = protocols === undefined ? new Native(url) : new Native(url, protocols);
    var opened = 0;
    socket.addEventListener('open', function() { opened = Date.now(); });
    socket.addEventListener('close', function(event) {
      if (opened && event.wasClean && (event.code === 1000 || event.code === 1001)) {
        failures = 0;
        return;
      }
      if (opened && Date.now() - opened > 60000) failures = 0;
      failures += 1;
      if (event.code === 4403 || event.code === 1006) diagnose();
      if (failures >= 6) notify();
    });
    return socket;
  };
  window.WebSocket.prototype = Native.prototype;
  Object.setPrototypeOf(window.WebSocket, Native);
})();
</script>
"""
    head_end = original.lower().find(">", original.lower().find("<head")) if "<head" in original.lower() else -1
    original = original[:head_end + 1] + guard + original[head_end + 1:] if head_end >= 0 else guard + original
    script = """
<script data-scrcpygate-alas-ready>
(function() {
  var readyType = "scrcpygate:alas-ready";
  function validHttpOrigin(value) {
    var text = String(value || "");
    if (!text) return "";
    try {
      var parsed = new URL(text, window.location.href);
      if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return "";
      if (parsed.origin === "null") return "";
      return parsed.origin;
    } catch (_) {
      return "";
    }
  }
  function contextParentOrigin() {
    var origin = validHttpOrigin(document.referrer);
    if (origin) return origin;
    try {
      var ancestors = window.location.ancestorOrigins || [];
      for (var index = 0; index < ancestors.length; index += 1) {
        origin = validHttpOrigin(ancestors[index]);
        if (origin) return origin;
      }
    } catch (_) {}
    return "";
  }
  function notifyParent(targetOrigin) {
    try {
      if (window.parent && window.parent !== window) {
        var origin = validHttpOrigin(targetOrigin) || contextParentOrigin();
        if (!origin) return;
        window.parent.postMessage({type: readyType}, origin);
      }
    } catch (_) {}
  }
  window.addEventListener("message", function(event) {
    if (event.source !== window.parent || !event.data ||
        event.data.type !== readyType + "-request") return;
    var origin = validHttpOrigin(event.origin);
    if (origin) notifyParent(origin);
  });
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", notifyParent, {once: true});
  } else {
    notifyParent();
  }
})();
</script>"""
    head_match = re.search(r"<head(?:\s[^>]*)?>", original, flags=re.IGNORECASE)
    if head_match:
        return f"{original[:head_match.end()]}{script}{original[head_match.end():]}"
    body_index = original.lower().rfind("</body>")
    if body_index >= 0:
        return f"{original[:body_index]}{script}{original[body_index:]}"
    return f"{original}{script}"


def _json_for_script(value) -> str:
    """JSON-encode a value for an inline `<script>` (no `</script>` escape hatch)."""
    return (
        json.dumps(value, ensure_ascii=False)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )


def inject_bound_config_script(
    html: str,
    config_name: str,
    device_context: str = "",
    gateway_context: str = "",
) -> str:
    """Inject a small bootstrap so PyWebIO child requests keep the bound config."""
    if not config_name or "data-scrcpygate-alas-bind" in str(html or ""):
        return str(html or "")
    config_json = (
        json.dumps(str(config_name), ensure_ascii=False)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )
    device_context_json = (
        json.dumps(str(device_context or ""), ensure_ascii=False)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )
    gateway_context_json = (
        json.dumps(str(gateway_context or ""), ensure_ascii=False)
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
    # 客户端点击门必须跟着**后台可配置的规则**走：早先这里内置了一份 22 条标签快照
    # （还包含默认已放开的 工具/tool/toolbox），管理员改规则后它不会变，会把服务端
    # 允许的入口在浏览器里挡掉（实测「工具」被挡 → 看得见点不动）。
    restricted_labels_json = _json_for_script(
        sorted(alas_visibility.visibility_rules()["labels"])
    )
    settings_tokens_json = _json_for_script(
        sorted(alas_visibility.visibility_rules()["settings_tasks"])
    )
    config_keys_json = _json_for_script(list(CONFIG_QUERY_KEYS))
    script = f"""
<script data-scrcpygate-alas-bind>
(function() {{
  var boundConfig = {config_json};
  var deviceContext = {device_context_json};
  var gatewayContext = {gateway_context_json};
  var hiddenEndpointText = {hidden_endpoint_json};
  var proxyPrefix = "{ALAS_EMBED_PREFIX}/proxy";
  var gatewayOrigin = window.location.origin;
  var configKeys = {config_keys_json};
  var restrictedLabels = {restricted_labels_json};
  var settingsTokens = {settings_tokens_json};
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
    if (url.origin === gatewayOrigin || url.origin === window.location.origin) return true;
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
      if (deviceContext) url.searchParams.set("device_id", deviceContext);
      if (gatewayContext) url.searchParams.set("{alas_gateway.ALAS_CONTEXT_QUERY}", gatewayContext);
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
    if (!text || !settingsTokens.length) return false;
    for (var i = 0; i < settingsTokens.length; i += 1) {{
      var token = settingsTokens[i];
      var isLabel = token.indexOf("设置") !== -1 || token.indexOf("setting") !== -1 || token.indexOf("設定") !== -1;
      if (isLabel && text === token) return true;
    }}
    return text.indexOf("alas") !== -1 &&
      (text.indexOf("设置") !== -1 || text.indexOf("setting") !== -1 || text.indexOf("設定") !== -1);
  }}
  function textTargetsRestrictedUserEntry(value) {{
    var text = compactText(value);
    if (!text || !restrictedLabels.length) return false;
    return restrictedLabels.indexOf(text) !== -1;
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
    head_match = re.search(r"<head(?:\s[^>]*)?>", original, flags=re.IGNORECASE)
    if head_match:
        return f"{original[:head_match.end()]}{script}{original[head_match.end():]}"
    body_index = original.lower().rfind("</body>")
    if body_index >= 0:
        return f"{original[:body_index]}{script}{original[body_index:]}"
    return f"{original}{script}"

__all__ = [
    "embed_shell_html", "denied_page_html", "rewrite_bound_html_urls",
    "filter_user_html", "inject_alas_ready_script", "inject_bound_config_script",
]
