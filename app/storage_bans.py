"""IP 封禁（BAN）的持久化：``ip_bans`` 当前状态 + ``ip_ban_events`` 不可变事件流。

与 ``storage_access`` 同样的约定：**连接工厂由调用方注入**（``connect``），本模块不 import
``app.storage``，因此不拥有数据库生命周期，也不会与兼容门面形成循环依赖。

设计要点：

* 封禁的**权威状态在数据库**（重启后仍生效），内存里只放一份带 TTL 的快照用于每请求判定；
  快照由 ``app/ip_ban.py`` 维护，本模块只负责读写真值。
* 每次变更都追加一条 ``ip_ban_events``（``ban`` / ``update`` / ``unban`` / ``expire``），
  事件是审计口径的事实来源，**不因为解封而删除**。
* 到期判定以 ``expires_ts`` 为准：``permanent=1`` 或 ``expires_ts IS NULL`` 表示永久；
  已到期的行保留在表里（便于「曾封禁过」的追溯），由 ``prune_ban_history`` 按保留期清理。
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable, Mapping

ConnectionFactory = Callable[[], sqlite3.Connection]

MAX_LIST_PAGE = 200
DEFAULT_LIST_PAGE = 50
# 单次封禁时长上限：365 天（自定义档位的上限，永久封禁走 permanent 标志而不是超长秒数）
MAX_BAN_SECONDS = 365 * 24 * 60 * 60
# 事件保留期：与审计日志同量级，默认 180 天
DEFAULT_BAN_EVENT_RETENTION_DAYS = 180
PRUNE_BATCH = 500
PRUNE_MAX_BATCHES = 4

BAN_ACTION_BAN = "ban"
BAN_ACTION_UPDATE = "update"
BAN_ACTION_UNBAN = "unban"
BAN_ACTION_EXPIRE = "expire"


def _now(now: int | None = None) -> int:
    return int(time.time()) if now is None else int(now)


def _row_to_ban(row: sqlite3.Row | Mapping | None, *, now: int) -> dict | None:
    if row is None:
        return None
    expires_ts = row["expires_ts"]
    permanent = bool(row["permanent"]) or expires_ts is None
    return {
        "ip": str(row["ip"]),
        "created_ts": int(row["created_ts"]),
        "updated_ts": int(row["updated_ts"]),
        "expires_ts": None if permanent else int(expires_ts),
        "permanent": permanent,
        "reason": str(row["reason"] or ""),
        "actor": str(row["actor"] or ""),
        "revoked_ts": None if row["revoked_ts"] is None else int(row["revoked_ts"]),
        "revoked_by": str(row["revoked_by"] or ""),
        # 剩余秒数只作为展示字段；判定一律用 expires_ts，避免调用方各自算错边界。
        "remaining_seconds": None if permanent else max(0, int(expires_ts) - now),
        "active": bool(row["revoked_ts"] is None and (permanent or int(expires_ts) > now)),
    }


def get_ban(*, connect: ConnectionFactory, ip: str) -> dict | None:
    """读单个 IP 的封禁行（含已过期/已解除的行，供详情视图使用）。"""
    with connect() as conn:
        row = conn.execute("SELECT * FROM ip_bans WHERE ip = ?", (str(ip),)).fetchone()
    return _row_to_ban(row, now=_now())


def get_active_ban(*, connect: ConnectionFactory, ip: str, now: int | None = None) -> dict | None:
    """只返回当前生效的封禁（未解除且未到期）。"""
    moment = _now(now)
    with connect() as conn:
        row = conn.execute("SELECT * FROM ip_bans WHERE ip = ?", (str(ip),)).fetchone()
    ban = _row_to_ban(row, now=moment)
    if ban is None or not ban["active"]:
        return None
    return ban


def active_bans(*, connect: ConnectionFactory, now: int | None = None, limit: int | None = None) -> list[dict]:
    """当前生效的封禁（用于启动时装载内存快照；上限防御性设置）。"""
    moment = _now(now)
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM ip_bans
            WHERE revoked_ts IS NULL AND (permanent = 1 OR expires_ts > ?)
            ORDER BY updated_ts DESC, ip ASC
            LIMIT ?
            """,
            (moment, -1 if limit is None else max(1, min(int(limit), 20000))),
        ).fetchall()
    return [ban for ban in (_row_to_ban(row, now=moment) for row in rows) if ban is not None]


def list_bans(
    *,
    connect: ConnectionFactory,
    limit: int = DEFAULT_LIST_PAGE,
    offset: int = 0,
    include_inactive: bool = False,
    now: int | None = None,
) -> dict:
    """封禁列表：默认只看生效中的，``include_inactive`` 时连已解除/已过期的一起列出。"""
    moment = _now(now)
    page_size = max(1, min(int(limit), MAX_LIST_PAGE))
    page_offset = max(0, int(offset))
    where = "" if include_inactive else "WHERE revoked_ts IS NULL AND (permanent = 1 OR expires_ts > ?)"
    params: list[object] = [] if include_inactive else [moment]
    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM ip_bans {where} ORDER BY updated_ts DESC, ip ASC LIMIT ? OFFSET ?",
            params + [page_size + 1, page_offset],
        ).fetchall()
    has_more = len(rows) > page_size
    return {
        "items": [ban for ban in (_row_to_ban(row, now=moment) for row in rows[:page_size]) if ban is not None],
        "has_more": has_more,
        "offset": page_offset,
        "limit": page_size,
        "now": moment,
    }


def record_ban_event(
    *,
    connect: ConnectionFactory,
    ip: str,
    action: str,
    actor: str = "",
    detail: Mapping | None = None,
    ts: int | None = None,
) -> int:
    """追加一条封禁事件（同一事务里与状态变更一起提交由调用方负责）。"""
    payload = json.dumps(detail or {}, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    with connect() as conn:
        cursor = conn.execute(
            "INSERT INTO ip_ban_events (ts, ip, action, actor, detail_json) VALUES (?, ?, ?, ?, ?)",
            (_now(ts), str(ip), str(action)[:32], str(actor or "")[:64], payload[:2000]),
        )
        return int(cursor.lastrowid or 0)


def upsert_ban(
    *,
    connect: ConnectionFactory,
    ip: str,
    expires_ts: int | None,
    permanent: bool,
    reason: str = "",
    actor: str = "",
    action: str = BAN_ACTION_BAN,
    now: int | None = None,
    detail: Mapping | None = None,
) -> dict:
    """新建或修改封禁，并在同一事务里写事件。返回写入后的行。"""
    moment = _now(now)
    target = str(ip)
    # 永久封禁一律把 expires_ts 记成 NULL；有限封禁必须带到期时间，避免出现「永不过期的临时封禁」。
    stored_expires = None if permanent else int(expires_ts) if expires_ts is not None else None
    if not permanent and stored_expires is None:
        raise ValueError("finite ban requires expires_ts")
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            existing = conn.execute("SELECT created_ts FROM ip_bans WHERE ip = ?", (target,)).fetchone()
            created = int(existing["created_ts"]) if existing is not None else moment
            conn.execute(
                """
                INSERT INTO ip_bans (ip, created_ts, updated_ts, expires_ts, permanent, reason, actor, revoked_ts, revoked_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, NULL, '')
                ON CONFLICT(ip) DO UPDATE SET
                    updated_ts = excluded.updated_ts,
                    expires_ts = excluded.expires_ts,
                    permanent = excluded.permanent,
                    reason = excluded.reason,
                    actor = excluded.actor,
                    revoked_ts = NULL,
                    revoked_by = ''
                """,
                (
                    target,
                    created,
                    moment,
                    stored_expires,
                    1 if permanent else 0,
                    str(reason or "")[:200],
                    str(actor or "")[:64],
                ),
            )
            payload = dict(detail or {})
            payload.setdefault("expires_ts", stored_expires)
            payload.setdefault("permanent", bool(permanent))
            payload.setdefault("reason", str(reason or "")[:200])
            conn.execute(
                "INSERT INTO ip_ban_events (ts, ip, action, actor, detail_json) VALUES (?, ?, ?, ?, ?)",
                (
                    moment,
                    target,
                    str(action)[:32],
                    str(actor or "")[:64],
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)[:2000],
                ),
            )
            row = conn.execute("SELECT * FROM ip_bans WHERE ip = ?", (target,)).fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    ban = _row_to_ban(row, now=moment)
    assert ban is not None
    return ban


def revoke_ban(
    *,
    connect: ConnectionFactory,
    ip: str,
    actor: str = "",
    now: int | None = None,
    detail: Mapping | None = None,
) -> dict | None:
    """解除封禁：置 ``revoked_ts``（保留行与事件，不删除历史）。"""
    moment = _now(now)
    target = str(ip)
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute("SELECT * FROM ip_bans WHERE ip = ?", (target,)).fetchone()
            if row is None or row["revoked_ts"] is not None:
                conn.rollback()
                return None
            conn.execute(
                "UPDATE ip_bans SET revoked_ts = ?, revoked_by = ?, updated_ts = ? WHERE ip = ?",
                (moment, str(actor or "")[:64], moment, target),
            )
            conn.execute(
                "INSERT INTO ip_ban_events (ts, ip, action, actor, detail_json) VALUES (?, ?, ?, ?, ?)",
                (
                    moment,
                    target,
                    BAN_ACTION_UNBAN,
                    str(actor or "")[:64],
                    json.dumps(dict(detail or {}), ensure_ascii=False, separators=(",", ":"), sort_keys=True)[:2000],
                ),
            )
            updated = conn.execute("SELECT * FROM ip_bans WHERE ip = ?", (target,)).fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return _row_to_ban(updated, now=moment)


def expire_bans(*, connect: ConnectionFactory, now: int | None = None, batch: int = PRUNE_BATCH) -> int:
    """把已到期的封禁标记为到期（写 expire 事件），返回处理条数。

    到期是「事实」而不是「删除」：状态行保留（active 变 False），事件流多一条 ``expire``，
    这样界面能回答「这个 IP 什么时候被封过、什么时候自然到期」。
    """
    moment = _now(now)
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            rows = conn.execute(
                """
                SELECT ip FROM ip_bans
                WHERE revoked_ts IS NULL AND permanent = 0 AND expires_ts IS NOT NULL AND expires_ts <= ?
                LIMIT ?
                """,
                (moment, max(1, min(int(batch), 5000))),
            ).fetchall()
            for row in rows:
                conn.execute(
                    "UPDATE ip_bans SET revoked_ts = ?, revoked_by = '', updated_ts = ? WHERE ip = ?",
                    (moment, moment, row["ip"]),
                )
                conn.execute(
                    "INSERT INTO ip_ban_events (ts, ip, action, actor, detail_json) VALUES (?, ?, ?, '', ?)",
                    (moment, row["ip"], BAN_ACTION_EXPIRE, json.dumps({"reason": "expired"})),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return len(rows)


def list_ban_events(
    *,
    connect: ConnectionFactory,
    ip: str = "",
    limit: int = DEFAULT_LIST_PAGE,
    offset: int = 0,
    from_ts: int | None = None,
    to_ts: int | None = None,
) -> dict:
    """事件流（倒序），可按 IP 过滤；用于界面上的「这个地址发生过什么」。"""
    page_size = max(1, min(int(limit), MAX_LIST_PAGE))
    page_offset = max(0, int(offset))
    clauses: list[str] = []
    params: list[object] = []
    if ip:
        clauses.append("ip = ?")
        params.append(str(ip))
    if from_ts is not None:
        clauses.append("ts >= ?")
        params.append(int(from_ts))
    if to_ts is not None:
        clauses.append("ts <= ?")
        params.append(int(to_ts))
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM ip_ban_events{where} ORDER BY id DESC LIMIT ? OFFSET ?",
            params + [page_size + 1, page_offset],
        ).fetchall()
    has_more = len(rows) > page_size
    items = []
    for row in rows[:page_size]:
        try:
            detail = json.loads(row["detail_json"] or "{}")
        except (TypeError, ValueError):
            detail = {}
        items.append(
            {
                "id": int(row["id"]),
                "ts": int(row["ts"]),
                "ip": str(row["ip"]),
                "action": str(row["action"]),
                "actor": str(row["actor"] or ""),
                "detail": detail if isinstance(detail, dict) else {},
            }
        )
    return {"items": items, "has_more": has_more, "offset": page_offset, "limit": page_size}


def ban_counters(*, connect: ConnectionFactory, now: int | None = None) -> dict:
    """界面上方的计数：生效中 / 永久 / 今日新增 / 事件总数。"""
    moment = _now(now)
    day_start = moment - (moment % 86400)
    with connect() as conn:
        row = conn.execute(
            """
            SELECT
                SUM(CASE WHEN revoked_ts IS NULL AND (permanent = 1 OR expires_ts > ?) THEN 1 ELSE 0 END) AS active,
                SUM(CASE WHEN revoked_ts IS NULL AND permanent = 1 THEN 1 ELSE 0 END) AS permanent,
                SUM(CASE WHEN created_ts >= ? THEN 1 ELSE 0 END) AS created_today,
                COUNT(*) AS total
            FROM ip_bans
            """,
            (moment, day_start),
        ).fetchone()
        events = conn.execute("SELECT COUNT(*) AS count FROM ip_ban_events").fetchone()
    return {
        "active": int(row["active"] or 0),
        "permanent": int(row["permanent"] or 0),
        "created_today": int(row["created_today"] or 0),
        "total": int(row["total"] or 0),
        "events": int(events["count"] or 0),
        "now": moment,
    }


def prune_ban_history(
    *, connect: ConnectionFactory, retention_days: int = DEFAULT_BAN_EVENT_RETENTION_DAYS, now: int | None = None
) -> int:
    """按保留期清理已结束的封禁行与旧事件（生效中的行永不清理）。"""
    moment = _now(now)
    days = max(1, int(retention_days))
    cutoff = moment - days * 86400
    removed = 0
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            for _ in range(PRUNE_MAX_BATCHES):
                cursor = conn.execute(
                    """
                    DELETE FROM ip_ban_events WHERE id IN (
                        SELECT id FROM ip_ban_events WHERE ts < ? ORDER BY id ASC LIMIT ?
                    )
                    """,
                    (cutoff, PRUNE_BATCH),
                )
                removed += int(cursor.rowcount or 0)
                if int(cursor.rowcount or 0) < PRUNE_BATCH:
                    break
            conn.execute(
                """
                DELETE FROM ip_bans
                WHERE revoked_ts IS NOT NULL AND revoked_ts < ?
                  AND NOT EXISTS (SELECT 1 FROM ip_ban_events WHERE ip_ban_events.ip = ip_bans.ip AND ts >= ?)
                """,
                (cutoff, cutoff),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return removed
