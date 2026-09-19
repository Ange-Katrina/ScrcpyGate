"""到达应用的访问记录（VIS）：有界采集、脱敏、批量落库。

设计依据：`docs/organized/01-tasks/SG-ACCESS-20260917_PHASE_A_DESIGN.md`。

约定：

* **记录范围**是「到达应用的访问」。雷池提前拦截、TLS 失败、ASGI 之前被拒的请求不会出现在这里。
* **每请求只提交一次**：提交点在 HTTP 中间件的 ``finally``，因此边界 403/404、账户状态拒绝、
  以及后续的 BAN/GEO 拒绝都会走到同一条路径，不会漏记也不会重复计数。
* **生产者永不阻塞**：有界队列，满则丢弃并计数（采样状态如实上报，绝不把采样数当总数）。
* **单写线程 + 批量 executemany**：避免与审计写入抢 SQLite 写锁（SQLite 只有一个写者）。
* **落库前完成脱敏**：不采集 query / Referer / body / Cookie / Authorization；
  路径优先用路由模板，未匹配路径限长并折叠长片段（票据、哈希、base64 段）。
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import Counter, deque
from collections.abc import Callable, Mapping

log = logging.getLogger("webscrcpy.access")

# 保留档位与默认值（0 = 不清理明细，仅受行数上限约束）
RETENTION_DAY_OPTIONS = (0, 1, 3, 7, 15, 30)
DEFAULT_RETENTION_DAYS = 7
# 明细行数上限（另有磁盘预算检查）
MAX_DETAIL_ROWS = 200_000
# 单字段上限
MAX_USER_AGENT_CHARS = 256
MAX_PATH_CHARS = 512
MAX_ACCOUNT_CHARS = 64
# 队列与批
QUEUE_MAX = 4096
BATCH_MAX = 200
FLUSH_INTERVAL_SECONDS = 1.0

STATIC_PREFIXES = ("/static/", "/shared/", "/vendor/", "/css/")
HEALTH_PATHS = ("/healthz",)
# 后台自己的轮询：不算访客活跃度，也不参与「疑似扫描」线索
ADMIN_POLL_PREFIXES = (
    "/api/admin/logs",
    "/api/admin/runtime-logs",
    "/api/admin/access",
    "/api/admin/dashboard",
    "/api/admin/overview",
    "/api/admin/alerts",
    "/api/notifications",
)
IDENTITY_ANONYMOUS = "anonymous"
IDENTITY_ACCOUNT = "account"
IDENTITY_ADMIN = "admin"
DECISION_ALLOW = "allow"
DECISION_DENY_BAN = "deny_ban"
DECISION_DENY_GEO = "deny_geo"
DECISION_OBSERVE_GEO = "observe_geo"
DECISION_DB_UNAVAILABLE = "db_unavailable"
KIND_PAGE = "page"
KIND_API = "api"
KIND_STATIC = "static"
KIND_HEALTH = "health"
KIND_ADMIN_POLL = "admin_poll"
KIND_WS = "websocket"

# 长到不像正常路径片段的段（票据/哈希/base64）折叠掉，避免把秘密写进日志
_LONG_SEGMENT_RE = re.compile(r"[A-Za-z0-9_\-]{24,}")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def is_health_path(path: str) -> bool:
    return str(path or "") in HEALTH_PATHS


def is_static_path(path: str) -> bool:
    return str(path or "").startswith(STATIC_PREFIXES) or str(path or "") == "/favicon.ico"


def is_admin_poll_path(path: str) -> bool:
    return str(path or "").startswith(ADMIN_POLL_PREFIXES)


def sanitize_path(path: str) -> str:
    """未匹配路由时的路径样本：去 query、折叠长片段、限长、去控制字符。"""
    raw = str(path or "")
    raw = raw.split("?", 1)[0].split("#", 1)[0]
    raw = _CONTROL_RE.sub("", raw)
    raw = _LONG_SEGMENT_RE.sub("<segment>", raw)
    if len(raw) > MAX_PATH_CHARS:
        raw = raw[:MAX_PATH_CHARS] + "…"
    return raw


def sanitize_user_agent(value: object) -> str:
    text = _CONTROL_RE.sub("", str(value or "").strip())
    return text[:MAX_USER_AGENT_CHARS]


def classify_request(path: str, *, is_static: bool = False) -> str:
    if is_health_path(path) or is_static:
        return KIND_HEALTH if is_health_path(path) else KIND_STATIC
    if is_admin_poll_path(path):
        return KIND_ADMIN_POLL
    if str(path or "").startswith("/api/"):
        return KIND_API
    return KIND_PAGE


def should_record(kind: str, status_code: int) -> bool:
    """哪些请求落明细。

    * 健康探针：不记（不计访客数），但计入 writer 的 health 计数。
    * 静态资源：正常不记；**异常静态访问**（>=400）照记，用于异常统计。
    * 其余一律记录（含 401/403/404/429/5xx 与匿名首页/登录页）。
    """
    if kind == KIND_HEALTH:
        return False
    if kind == KIND_STATIC:
        return int(status_code) >= 400
    return True


def _ip_version(ip: str) -> int:
    return 6 if ":" in str(ip or "") else 4


def http_record(
    *,
    ts: int,
    source_ip: str,
    method: str,
    path: str,
    route_template: str = "",
    status: int = 0,
    duration_ms: float = 0.0,
    request_id: str = "",
    user_agent: object = "",
    session: Mapping | None = None,
    role: str = "",
    decision: str = DECISION_ALLOW,
    country: str = "",
    geo_db_epoch: int | None = None,
    kind: str | None = None,
) -> dict:
    """构造一条访问明细（纯函数，便于单测）。

    身份只来自服务端已确认的会话对象，绝不根据 Cookie 自报内容判断；
    路径优先用路由模板，未匹配路由时用脱敏后的样本。
    """
    from .ip_ban import normalize_ip

    ip = normalize_ip(source_ip)
    resolved_kind = kind or classify_request(path, is_static=is_static_path(path))
    identity = IDENTITY_ANONYMOUS
    account = ""
    if session:
        account = str(session.get("username") or "")[:MAX_ACCOUNT_CHARS]
        # 会话行本身不含 role（SELECT s.* + 账户的几个字段），所以角色由调用方按用户名查好后传入。
        identity = IDENTITY_ADMIN if str(role or "").strip().lower() == "admin" else IDENTITY_ACCOUNT
    template = str(route_template or "")[:MAX_PATH_CHARS]
    return {
        "ts": int(ts),
        "source_ip": ip,
        "ip_version": _ip_version(ip),
        "country": str(country or "")[:8],
        "geo_db_epoch": int(geo_db_epoch) if geo_db_epoch else None,
        "identity": identity,
        "account": account,
        "method": str(method or "")[:8].upper(),
        "kind": resolved_kind,
        # 模板本身也要脱敏：真实模板不含长随机段，但防御性处理没有代价。
        "route_template": sanitize_path(template) if template else "",
        "path_sample": sanitize_path(template or path),
        "status": int(status),
        "duration_ms": int(max(0.0, float(duration_ms))),
        "decision": str(decision or DECISION_ALLOW)[:16],
        "request_id": str(request_id or "")[:64],
        "user_agent": sanitize_user_agent(user_agent),
    }


class AccessLogWriter:
    """有界队列 + 单写线程 + 批量落库。

    ``writer`` 接收一个记录列表（一次批量），内部自己开事务。任何写入异常都被吞掉并计数，
    绝不影响请求路径；失败不会无限重试，也不会递归记错。
    """

    def __init__(
        self,
        writer: Callable[[list[dict]], object],
        *,
        on_drops: Callable[[int, float], object] | None = None,
        maxsize: int = QUEUE_MAX,
        batch_max: int = BATCH_MAX,
        flush_interval: float = FLUSH_INTERVAL_SECONDS,
    ) -> None:
        self._writer = writer
        self._on_drops = on_drops
        self._maxsize = max(64, min(int(maxsize), 65536))
        self._batch_max = max(16, min(int(batch_max), 2000))
        self._flush_interval = max(0.05, float(flush_interval))
        self._items: deque[dict] = deque()
        self._condition = threading.Condition()
        self._thread: threading.Thread | None = None
        self._stopping = False
        self._unfinished = 0
        self._accepted = 0
        self._processed = 0
        self._batches = 0
        self._failures = 0
        self._health_skipped = 0
        self._dropped: Counter[str] = Counter()
        self._dropped_total = 0
        self._last_drop_ts = 0.0
        self._reported_drops = 0

    # ---- 生产者 ----
    def submit(self, record: Mapping) -> bool:
        if not isinstance(record, Mapping):
            raise TypeError("access record must be a mapping")
        event = dict(record)
        kind = str(event.get("kind") or KIND_PAGE)
        if kind == KIND_HEALTH:
            with self._condition:
                self._health_skipped += 1
                return False
        accepted = False
        with self._condition:
            if self._stopping or not self._thread or not self._thread.is_alive():
                accepted = self._note_drop_locked(kind)
            elif len(self._items) >= self._maxsize:
                accepted = self._note_drop_locked(kind)
            else:
                self._items.append(event)
                self._unfinished += 1
                self._accepted += 1
                self._condition.notify()
                accepted = True
        return accepted

    def _note_drop_locked(self, kind: str) -> bool:
        self._dropped[kind] += 1
        self._dropped_total += 1
        self._last_drop_ts = time.time()
        return False

    def _report_drops(self) -> None:
        # Only called by the consumer; never hold the queue lock during SQLite I/O.
        with self._condition:
            total, stamp = self._dropped_total, self._last_drop_ts
            delta = total - self._reported_drops
        if not delta:
            return
        try:
            if self._on_drops is not None:
                self._on_drops(delta, stamp)
        except Exception:
            log.warning("ACCESS_DROP_COUNTER_WRITE_FAILED")
            return
        with self._condition:
            self._reported_drops = total
            self._condition.notify_all()

    # ---- 写线程 ----
    def start(self) -> None:
        with self._condition:
            if self._thread and self._thread.is_alive():
                return
            self._stopping = False
            self._thread = threading.Thread(target=self._run, name="scrcpygate-access-writer", daemon=True)
            self._thread.start()

    def _drain_locked(self) -> list[dict]:
        batch: list[dict] = []
        for _ in range(self._batch_max):
            if not self._items:
                break
            batch.append(self._items.popleft())
        return batch

    def _run(self) -> None:
        next_report = 0.0
        while True:
            with self._condition:
                if not self._items and not self._stopping:
                    self._condition.wait(self._flush_interval)
                stopping = self._stopping and not self._items
                batch = self._drain_locked()
            if batch:
                failed = False
                try:
                    self._writer(batch)
                except Exception:
                    failed = True
                    log.warning("ACCESS_LOG_WRITE_FAILED count=%s", len(batch))
                with self._condition:
                    self._processed += len(batch) if not failed else 0
                    self._batches += 0 if failed else 1
                    self._unfinished -= len(batch)
                    if failed:
                        self._failures += 1
                        for record in batch:
                            self._note_drop_locked(str(record.get("kind") or KIND_PAGE))
                    self._condition.notify_all()
            if stopping or time.monotonic() >= next_report:
                self._report_drops()
                next_report = time.monotonic() + self._flush_interval
            if stopping:
                return

    # ---- 读栅栏 / 收尾 ----
    def barrier(self, timeout: float = 0.5) -> bool:
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._condition:
            while self._unfinished:
                thread = self._thread
                if not thread or not thread.is_alive():
                    return False
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True

    flush = barrier

    def stop(self, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._condition:
            self._stopping = True
            self._condition.notify_all()
            thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(max(0.0, deadline - time.monotonic()))
        return not bool(thread and thread.is_alive())

    def stats(self) -> dict:
        with self._condition:
            thread = self._thread
            return {
                "running": bool(thread and thread.is_alive() and not self._stopping),
                "queue_size": len(self._items),
                "queue_capacity": self._maxsize,
                "unfinished": self._unfinished,
                "accepted": self._accepted,
                "processed": self._processed,
                "batches": self._batches,
                "failures": self._failures,
                "health_skipped": self._health_skipped,
                "dropped_total": self._dropped_total,
                "dropped_by_kind": dict(self._dropped),
                "last_drop_ts": int(self._last_drop_ts),
                "sampled": self._dropped_total > 0,
            }
