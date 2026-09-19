"""访问记录（VIS）的写入、查询、异常线索与保留清理。

与 ``storage_audit`` 同样约定：**连接工厂由调用方注入**（``connect``），本模块不 import
``app.storage``，所以它不拥有数据库生命周期，也不会与兼容门面形成循环依赖。

写入是单写线程的批量事务：一次 ``BEGIN IMMEDIATE`` 里 ``executemany`` 插明细，并按
(小时桶, IP) 聚合后一次 ``executemany`` upsert 汇总。这样每个批次只有一次提交，
不与审计写入争抢 SQLite 的写锁。
"""

from __future__ import annotations

import sqlite3
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping

ConnectionFactory = Callable[[], sqlite3.Connection]

BUCKET_SECONDS = 3600

# 异常线索阈值：有界、可解释，第一版不据此自动封禁。
SCAN_WINDOW_SECONDS = 600
SCAN_MAX_ROWS = 20_000
MANY_404_THRESHOLD = 20
AUTH_FAILURE_THRESHOLD = 10
RATE_LIMITED_THRESHOLD = 5
SENSITIVE_PATHS_THRESHOLD = 8
NOT_FOUND_PATHS_THRESHOLD = 30
MAX_HINTS = 50

# 查询上限
MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 50
MAX_RANGE_SECONDS = 31 * 24 * 60 * 60
EXPORT_MAX_ROWS = 10_000
# 保留清理：每次最多删几批，避免一次维护把写锁占满
PRUNE_BATCH = 1000
PRUNE_MAX_BATCHES = 5

_RECORD_COLUMNS = (
    "ts",
    "source_ip",
    "ip_version",
    "country",
    "geo_db_epoch",
    "identity",
    "account",
    "method",
    "kind",
    "route_template",
    "path_sample",
    "status",
    "duration_ms",
    "decision",
    "request_id",
    "user_agent",
)


def _record_tuple(record: Mapping) -> tuple:
    return tuple(record.get(column) for column in _RECORD_COLUMNS)


def bucket_start(ts: int) -> int:
    return int(ts) - (int(ts) % BUCKET_SECONDS)


def summary_deltas(records: Iterable[Mapping]) -> list[tuple]:
    """把一批记录按 (桶, IP) 聚合成汇总表需要的增量行。"""
    grouped: dict[tuple[int, str], dict[str, object]] = defaultdict(
        lambda: {
            "country": "",
            "first_seen_ts": None,
            "last_seen_ts": None,
            "requests": 0,
            "errors_4xx": 0,
            "errors_5xx": 0,
            "denied_ban": 0,
            "denied_geo": 0,
            "observe_geo": 0,
        }
    )
    for record in records:
        ts = int(record.get("ts") or 0)
        ip = str(record.get("source_ip") or "")
        if not ip:
            continue
        row = grouped[(bucket_start(ts), ip)]
        if not row["country"]:
            row["country"] = str(record.get("country") or "")
        first = row["first_seen_ts"]
        last = row["last_seen_ts"]
        row["first_seen_ts"] = ts if first is None else min(int(first), ts)
        row["last_seen_ts"] = ts if last is None else max(int(last), ts)
        row["requests"] = int(row["requests"]) + 1
        status = int(record.get("status") or 0)
        if 400 <= status < 500:
            row["errors_4xx"] = int(row["errors_4xx"]) + 1
        if status >= 500:
            row["errors_5xx"] = int(row["errors_5xx"]) + 1
        decision = str(record.get("decision") or "")
        if decision == "deny_ban":
            row["denied_ban"] = int(row["denied_ban"]) + 1
        elif decision == "deny_geo":
            row["denied_geo"] = int(row["denied_geo"]) + 1
        elif decision == "observe_geo":
            row["observe_geo"] = int(row["observe_geo"]) + 1
    return [
        (
            bucket,
            ip,
            str(values["country"] or ""),
            int(values["first_seen_ts"] or 0),
            int(values["last_seen_ts"] or 0),
            int(values["requests"]),
            int(values["errors_4xx"]),
            int(values["errors_5xx"]),
            int(values["denied_ban"]),
            int(values["denied_geo"]),
            int(values["observe_geo"]),
        )
        for (bucket, ip), values in grouped.items()
    ]


def record_access_batch(
    records: list[Mapping],
    *,
    connect: ConnectionFactory,
    dropped: int = 0,
    dropped_ts: int = 0,
    max_rows: int = 200_000,
) -> int:
    """一个事务里写明细 + 汇总；返回写入的明细条数。"""
    if not records and not dropped:
        return 0
    records = records[-max(1, int(max_rows)):]
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if records:
            conn.executemany(
                f"INSERT INTO access_records({','.join(_RECORD_COLUMNS)}) VALUES({','.join('?' for _ in _RECORD_COLUMNS)})",
                [_record_tuple(record) for record in records],
            )
            conn.executemany(
                """
                INSERT INTO access_ip_summary(
                    bucket_ts, source_ip, country, first_seen_ts, last_seen_ts,
                    requests, errors_4xx, errors_5xx, denied_ban, denied_geo, observe_geo, dropped
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,0)
                ON CONFLICT(bucket_ts, source_ip) DO UPDATE SET
                    country=CASE WHEN excluded.country != '' THEN excluded.country ELSE country END,
                    first_seen_ts=MIN(first_seen_ts, excluded.first_seen_ts),
                    last_seen_ts=MAX(last_seen_ts, excluded.last_seen_ts),
                    requests=requests + excluded.requests,
                    errors_4xx=errors_4xx + excluded.errors_4xx,
                    errors_5xx=errors_5xx + excluded.errors_5xx,
                    denied_ban=denied_ban + excluded.denied_ban,
                    denied_geo=denied_geo + excluded.denied_geo,
                    observe_geo=observe_geo + excluded.observe_geo
                """,
                summary_deltas(records),
            )
        if dropped > 0 and dropped_ts > 0:
            # 采样丢弃记到当时所在的小时桶里（IP 用空串表示「非单 IP」的丢弃计数），
            # 让「记录不完整」这件事在时间线上也看得见。
            conn.execute(
                """
                INSERT INTO access_ip_summary(
                    bucket_ts, source_ip, country, first_seen_ts, last_seen_ts,
                    requests, errors_4xx, errors_5xx, denied_ban, denied_geo, observe_geo, dropped
                ) VALUES(?, '', '', ?, ?, 0, 0, 0, 0, 0, 0, ?)
                ON CONFLICT(bucket_ts, source_ip) DO UPDATE SET dropped=dropped + excluded.dropped
                """,
                (bucket_start(dropped_ts), dropped_ts, dropped_ts, int(dropped)),
            )
        if dropped > 0:
            conn.execute("""INSERT INTO settings(key,value) VALUES('_access_log_dropped_total', ?)
                ON CONFLICT(key) DO UPDATE SET value=CAST(CAST(value AS INTEGER)+? AS TEXT)""",
                (str(int(dropped)), int(dropped)))
            conn.execute("""INSERT INTO settings(key,value) VALUES('_access_log_last_drop_ts', ?)
                ON CONFLICT(key) DO UPDATE SET value=CAST(MAX(CAST(value AS INTEGER),?) AS TEXT)""",
                (str(int(dropped_ts)), int(dropped_ts)))
        _enforce_capacity(conn, max_rows)
        conn.commit()
    return len(records)


def _enforce_capacity(conn: sqlite3.Connection, max_rows: int) -> tuple[int, int]:
    """Enforce both budgets in the same transaction as inserts, regardless of age sweeps."""
    cap = max(1, int(max_rows))
    boundary = conn.execute("SELECT id FROM access_records ORDER BY id DESC LIMIT 1 OFFSET ?", (cap,)).fetchone()
    removed = 0
    if boundary is not None:
        removed = conn.execute("DELETE FROM access_records WHERE id <= ?", (boundary[0],)).rowcount
    # Summaries are historical projections, not authoritative filtered totals.
    cursor = conn.execute("""DELETE FROM access_ip_summary WHERE rowid IN (
        SELECT rowid FROM access_ip_summary ORDER BY bucket_ts DESC, rowid DESC LIMIT -1 OFFSET ?)
        """, (cap,))
    return removed, cursor.rowcount


def _clamp_window(from_ts: object, to_ts: object, *, now: int | None = None) -> tuple[int, int]:
    current = int(time.time()) if now is None else int(now)
    end = current if to_ts is None else int(to_ts)
    start = max(0, end - MAX_RANGE_SECONDS) if from_ts is None else int(from_ts)
    if end < start:
        start, end = end, start
    if end - start > MAX_RANGE_SECONDS:
        start = end - MAX_RANGE_SECONDS
    return start, end


def _filter_clauses(filters: Mapping) -> tuple[list[str], list[object]]:
    clauses: list[str] = []
    params: list[object] = []
    if filters.get("source_ip"):
        clauses.append("source_ip = ?")
        params.append(str(filters["source_ip"]))
    if filters.get("country"):
        clauses.append("country = ?")
        params.append(str(filters["country"]))
    if filters.get("identity"):
        clauses.append("identity = ?")
        params.append(str(filters["identity"]))
    if filters.get("status"):
        clauses.append("status = ?")
        params.append(int(filters["status"]))
    if filters.get("status_class") == "4xx":
        clauses.append("status >= 400 AND status < 500")
    elif filters.get("status_class") == "5xx":
        clauses.append("status >= 500")
    if filters.get("decision"):
        clauses.append("decision = ?")
        params.append(str(filters["decision"]))
    if filters.get("kind"):
        clauses.append("kind = ?")
        params.append(str(filters["kind"]))
    if filters.get("account"):
        clauses.append("account = ?")
        params.append(str(filters["account"]))
    if filters.get("request_id"):
        clauses.append("request_id = ?")
        params.append(str(filters["request_id"]))
    if filters.get("q"):
        needle = str(filters["q"])[:128]
        clauses.append("(path_sample LIKE ? OR route_template LIKE ?)")
        params.extend([f"%{needle}%", f"%{needle}%"])
    if not filters.get("include_admin_poll", False):
        clauses.append("kind != 'admin_poll'")
    if not filters.get("kind"):
        clauses.append("kind != 'websocket_end'")
    if filters.get("ban_state") in ("active", "clear"):
        exists = "EXISTS" if filters["ban_state"] == "active" else "NOT EXISTS"
        clauses.append(f"{exists} (SELECT 1 FROM ip_bans b WHERE b.ip=access_records.source_ip AND b.revoked_ts IS NULL AND (b.permanent=1 OR b.expires_ts>?))")
        params.append(int(time.time()))
    return clauses, params


def query_access_records(
    *,
    connect: ConnectionFactory,
    limit: int = DEFAULT_PAGE_SIZE,
    before_id: int | None = None,
    from_ts: object = None,
    to_ts: object = None,
    filters: Mapping | None = None,
    now: int | None = None,
) -> dict:
    """明细的 keyset 分页（按 id 倒序），返回 {items, has_more, next_before_id}。"""
    page_size = max(1, min(int(limit), MAX_PAGE_SIZE))
    start, end = _clamp_window(from_ts, to_ts, now=now)
    clauses, params = _filter_clauses(filters or {})
    clauses.extend(["ts >= ?", "ts <= ?"])
    params.extend([start, end])
    if before_id is not None:
        clauses.append("id < ?")
        params.append(int(before_id))
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(page_size + 1)
    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM access_records{where} ORDER BY id DESC LIMIT ?", params
        ).fetchall()
    has_more = len(rows) > page_size
    page = rows[:page_size]
    return {
        "items": [dict(row) for row in page],
        "has_more": has_more,
        "next_before_id": int(page[-1]["id"]) if has_more and page else None,
        "window": {"from_ts": start, "to_ts": end},
    }


def query_access_ip_summaries(
    *,
    connect: ConnectionFactory,
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
    from_ts: object = None,
    to_ts: object = None,
    filters: Mapping | None = None,
    now: int | None = None,
) -> dict:
    """按 IP 汇总（默认视图）：窗口内的请求数、错误分布、首末时间。"""
    page_size = max(1, min(int(limit), MAX_PAGE_SIZE))
    page_offset = max(0, int(offset))
    start, end = _clamp_window(from_ts, to_ts, now=now)
    clauses, params = _filter_clauses(filters or {})
    clauses.extend(["ts >= ?", "ts <= ?", "source_ip != ''"])
    params.extend([start, end])
    where = " WHERE " + " AND ".join(clauses)
    moment = int(time.time()) if now is None else int(now)
    with connect() as conn:
        rows = conn.execute(
            f"""SELECT source_ip, MAX(country) AS country, MIN(ts) AS first_seen_ts,
                MAX(ts) AS last_seen_ts, COUNT(*) AS requests,
                SUM(status >= 400 AND status < 500) AS errors_4xx,
                SUM(status >= 500 AND status < 600) AS errors_5xx,
                SUM(decision = 'deny_ban') AS denied_ban,
                SUM(decision = 'deny_geo') AS denied_geo,
                SUM(decision = 'observe_geo') AS observe_geo, 0 AS dropped
                FROM access_records{where} GROUP BY source_ip
                ORDER BY MAX(ts) DESC, source_ip ASC LIMIT ? OFFSET ?""",
            params + [page_size + 1, page_offset],
        ).fetchall()
        rows = [dict(row) for row in rows]
        for row in rows:
            ban = conn.execute("SELECT expires_ts, permanent FROM ip_bans WHERE ip=? AND revoked_ts IS NULL AND (permanent=1 OR expires_ts>?)", (row["source_ip"], moment)).fetchone()
            row["ban_active"] = ban is not None
            row["ban_expires_ts"] = ban["expires_ts"] if ban else None
            row["ban_permanent"] = bool(ban["permanent"]) if ban else False
    has_more = len(rows) > page_size
    page = rows[:page_size]
    return {
        "items": [dict(row) for row in page],
        "has_more": has_more,
        "offset": page_offset,
        "window": {"from_ts": start, "to_ts": end},
    }


def access_status_counts(
    *,
    connect: ConnectionFactory,
    from_ts: object = None,
    to_ts: object = None,
    filters: Mapping | None = None,
    now: int | None = None,
) -> dict:
    """窗口内的总量与状态分布（含 BAN/GEO 拒绝与采样丢弃）。"""
    start, end = _clamp_window(from_ts, to_ts, now=now)
    clauses, params = _filter_clauses(filters or {})
    clauses.extend(["ts >= ?", "ts <= ?"])
    params.extend([start, end])
    where = " AND ".join(clauses)
    with connect() as conn:
        row = conn.execute(f"""SELECT COUNT(*) AS requests,
            COALESCE(SUM(status >= 400 AND status < 500),0) AS errors_4xx,
            COALESCE(SUM(status >= 500 AND status < 600),0) AS errors_5xx,
            COALESCE(SUM(decision='deny_ban'),0) AS denied_ban,
            COALESCE(SUM(decision='deny_geo'),0) AS denied_geo,
            COALESCE(SUM(decision='observe_geo'),0) AS observe_geo,
            COUNT(DISTINCT source_ip) AS distinct_ips
            FROM access_records WHERE {where}""", params).fetchone()
        dropped_row = conn.execute("SELECT COALESCE(SUM(dropped),0) FROM access_ip_summary WHERE bucket_ts>=? AND bucket_ts<=?", (bucket_start(start), end)).fetchone()
    observed_drops = int(dropped_row[0])
    payload = dict(row)
    payload.update(dropped=None if observed_drops else 0, sampled=observed_drops > 0,
                   dropped_in_overlapping_hours=observed_drops, drop_count_scope="overlapping_hours",
                   window={"from_ts": start, "to_ts": end})
    return payload


def access_scan_hints(
    *,
    connect: ConnectionFactory,
    now: int | None = None,
    window_seconds: int = SCAN_WINDOW_SECONDS,
    include_admin_poll: bool = False,
) -> dict:
    """窗口内的「疑似扫描」线索：有界扫描 + 显式阈值，第一版不据此自动封禁。"""
    current = int(time.time()) if now is None else int(now)
    start = current - max(60, int(window_seconds))
    clauses = ["ts >= ?", "ts <= ?"]
    if not include_admin_poll:
        clauses.append("kind != 'admin_poll'")
    where = " AND ".join(clauses)
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT source_ip,
                   COUNT(*) AS total,
                   SUM(CASE WHEN status = 404 THEN 1 ELSE 0 END) AS not_found,
                   SUM(CASE WHEN status IN (401, 403) THEN 1 ELSE 0 END) AS auth_failures,
                   SUM(CASE WHEN status = 429 THEN 1 ELSE 0 END) AS rate_limited,
                   COUNT(DISTINCT CASE WHEN route_template = '' THEN path_sample END) AS unknown_paths,
                   COUNT(DISTINCT CASE WHEN route_template LIKE '/admin%' OR route_template LIKE '/api/admin%'
                                       THEN route_template END) AS sensitive_paths
            FROM (SELECT * FROM access_records WHERE {where} ORDER BY id DESC LIMIT ?)
            GROUP BY source_ip
            HAVING not_found >= ? OR auth_failures >= ? OR rate_limited >= ?
                OR unknown_paths >= ? OR sensitive_paths >= ?
            ORDER BY total DESC
            LIMIT ?
            """,
            (start, current, SCAN_MAX_ROWS, MANY_404_THRESHOLD, AUTH_FAILURE_THRESHOLD,
             RATE_LIMITED_THRESHOLD, NOT_FOUND_PATHS_THRESHOLD, SENSITIVE_PATHS_THRESHOLD, MAX_HINTS),
        ).fetchall()
    hints: list[dict] = []
    for row in rows:
        item = dict(row)
        kinds: list[dict] = []
        if int(item.get("not_found") or 0) >= MANY_404_THRESHOLD:
            kinds.append({"kind": "many_404", "count": int(item["not_found"]), "threshold": MANY_404_THRESHOLD})
        if int(item.get("auth_failures") or 0) >= AUTH_FAILURE_THRESHOLD:
            kinds.append({"kind": "auth_failures", "count": int(item["auth_failures"]), "threshold": AUTH_FAILURE_THRESHOLD})
        if int(item.get("rate_limited") or 0) >= RATE_LIMITED_THRESHOLD:
            kinds.append({"kind": "rate_limited", "count": int(item["rate_limited"]), "threshold": RATE_LIMITED_THRESHOLD})
        if int(item.get("unknown_paths") or 0) >= NOT_FOUND_PATHS_THRESHOLD:
            kinds.append({"kind": "many_unknown_paths", "count": int(item["unknown_paths"]), "threshold": NOT_FOUND_PATHS_THRESHOLD})
        if int(item.get("sensitive_paths") or 0) >= SENSITIVE_PATHS_THRESHOLD:
            kinds.append({"kind": "sensitive_entrypoints", "count": int(item["sensitive_paths"]), "threshold": SENSITIVE_PATHS_THRESHOLD})
        hints.append({
            "source_ip": str(item.get("source_ip") or ""),
            "window_seconds": max(60, int(window_seconds)),
            "total_requests": int(item.get("total") or 0),
            "reasons": kinds,
            "basis": {
                "not_found": int(item.get("not_found") or 0),
                "auth_failures": int(item.get("auth_failures") or 0),
                "rate_limited": int(item.get("rate_limited") or 0),
                "unknown_paths": int(item.get("unknown_paths") or 0),
                "sensitive_paths": int(item.get("sensitive_paths") or 0),
            },
        })
    return {
        "hints": hints,
        "window": {"from_ts": start, "to_ts": current, "seconds": max(60, int(window_seconds))},
        "thresholds": {
            "many_404": MANY_404_THRESHOLD,
            "auth_failures": AUTH_FAILURE_THRESHOLD,
            "rate_limited": RATE_LIMITED_THRESHOLD,
            "many_unknown_paths": NOT_FOUND_PATHS_THRESHOLD,
            "sensitive_entrypoints": SENSITIVE_PATHS_THRESHOLD,
        },
        "scanned_rows_limit": SCAN_MAX_ROWS,
        "note": "线索只描述统计特征，不等于入侵或被攻击结论。",
    }


def prune_access_records(
    *,
    connect: ConnectionFactory,
    days: int,
    now: int | None = None,
    max_rows: int = 200_000,
    batch: int = PRUNE_BATCH,
    max_batches: int = PRUNE_MAX_BATCHES,
) -> dict:
    """按天裁剪明细 + 按行数上限兜底；都是「删最旧前缀」，分批且有界。

    ``days <= 0`` 表示不按时间清理（仍受行数上限约束）。
    """
    current = int(time.time()) if now is None else int(now)
    removed_age = 0
    removed_cap = 0
    batches = 0
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if int(days) > 0:
            cutoff = current - int(days) * 86400
            while batches < max_batches:
                boundary = conn.execute(
                    "SELECT id FROM access_records WHERE ts < ? ORDER BY id LIMIT 1 OFFSET ?",
                    (cutoff, batch - 1),
                ).fetchone()
                if boundary is None:
                    row = conn.execute(
                        "SELECT COUNT(*) AS c FROM access_records WHERE ts < ?", (cutoff,)
                    ).fetchone()
                    count = int(row["c"]) if row else 0
                    if count:
                        conn.execute("DELETE FROM access_records WHERE ts < ?", (cutoff,))
                        removed_age += count
                        batches += 1
                    break
                conn.execute("DELETE FROM access_records WHERE id <= ? AND ts < ?", (int(boundary["id"]), cutoff))
                removed_age += batch
                batches += 1
        removed_cap, _ = _enforce_capacity(conn, max_rows)
        # 汇总表与明细同窗口：删掉早于最老明细的桶
        oldest = conn.execute("SELECT MIN(ts) AS m FROM access_records").fetchone()
        oldest_ts = int(oldest["m"]) if oldest and oldest["m"] is not None else current
        summary_cursor = conn.execute(
            "DELETE FROM access_ip_summary WHERE bucket_ts < ?", (bucket_start(oldest_ts),)
        )
        removed_summary = int(summary_cursor.rowcount or 0)
        pending_age = bool(int(days) > 0 and conn.execute("SELECT 1 FROM access_records WHERE ts < ? LIMIT 1", (current - int(days) * 86400,)).fetchone())
        conn.commit()
    return {
        "pending_age": pending_age,
        "records_removed_by_age": removed_age,
        "records_removed_by_cap": removed_cap,
        "summary_rows_removed": removed_summary,
        "batches": batches,
        "retention_days": int(days),
        "row_cap": int(max_rows),
    }
