"""Per-user notification inbox (create, list, mark read, prune).

The mirror workbench bell reads this inbox.  Rows are owned by one username and
carry a ``kind`` plus a small JSON payload so the client can render localised
text instead of storing prose in the database.  Writes and retention stay in
:mod:`app.storage`; this module only shapes rows and runs the statements it is
handed a connection for.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable

ConnectionFactory = Callable[[], sqlite3.Connection]

DEFAULT_NOTIFICATION_LIMIT = 20
MAX_NOTIFICATION_LIMIT = 100
KEEP_NOTIFICATIONS_PER_USER = 100

# 只允许已知类型落库，避免前端拿到无法渲染的 kind。
KINDS = frozenset(
    {
        "account_expiring",
        "account_expired",
        "account_updated",
        "permission_changed",
        "session_disconnected",
        "device_unavailable",
    }
)
SEVERITIES = frozenset({"info", "success", "warning", "error"})


def notification_payload(row: sqlite3.Row | dict) -> dict:
    """Convert one inbox row into the stable client projection."""
    keys = row.keys() if hasattr(row, "keys") else row
    raw_data = row["data_json"] if "data_json" in keys else ""
    try:
        data = json.loads(raw_data) if raw_data else {}
    except (TypeError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    read_at = row["read_at"] if "read_at" in keys else None
    return {
        "id": int(row["id"]),
        "kind": str(row["kind"] or ""),
        "severity": str(row["severity"] or "info"),
        "data": data,
        "href": str(row["href"] or ""),
        "created_at": int(row["created_at"] or 0),
        "read": read_at is not None,
    }


def create_notification(
    conn: sqlite3.Connection,
    username: str,
    kind: str,
    *,
    severity: str = "info",
    data: dict | None = None,
    href: str = "",
    dedupe_key: str = "",
    created_at: int | None = None,
) -> int | None:
    """Insert one inbox row; returns its id, or None when it already existed."""
    username = str(username or "").strip()
    kind = str(kind or "").strip()
    if not username or kind not in KINDS:
        return None
    severity = severity if severity in SEVERITIES else "info"
    payload = data if isinstance(data, dict) else {}
    try:
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)[:2000]
    except (TypeError, ValueError):
        payload_json = "{}"
    existing = None
    dedupe = str(dedupe_key or "").strip()
    if dedupe:
        existing = conn.execute(
            "SELECT id FROM user_notifications WHERE username=? AND dedupe_key=?",
            (username, dedupe),
        ).fetchone()
    if existing:
        return None
    try:
        cursor = conn.execute(
            "INSERT INTO user_notifications(username,kind,severity,data_json,href,dedupe_key,created_at,read_at) "
            "VALUES(?,?,?,?,?,?,?,NULL)",
            (
                username,
                kind,
                severity,
                payload_json,
                str(href or "")[:200],
                dedupe,
                int(created_at if created_at is not None else 0),
            ),
        )
    except sqlite3.IntegrityError:
        # 并发写入同一 dedupe_key：视为已存在。
        return None
    return int(cursor.lastrowid)


def list_notifications(
    conn: sqlite3.Connection,
    username: str,
    *,
    limit: int = DEFAULT_NOTIFICATION_LIMIT,
) -> list[dict]:
    username = str(username or "").strip()
    if not username:
        return []
    try:
        bounded = int(limit)
    except (TypeError, ValueError):
        bounded = DEFAULT_NOTIFICATION_LIMIT
    bounded = max(1, min(bounded, MAX_NOTIFICATION_LIMIT))
    rows = conn.execute(
        "SELECT id,username,kind,severity,data_json,href,created_at,read_at FROM user_notifications "
        "WHERE username=? ORDER BY id DESC LIMIT ?",
        (username, bounded),
    ).fetchall()
    return [notification_payload(row) for row in rows]


def unread_count(conn: sqlite3.Connection, username: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM user_notifications WHERE username=? AND read_at IS NULL",
        (str(username or "").strip(),),
    ).fetchone()
    return int(row[0] or 0) if row else 0


def total_count(conn: sqlite3.Connection, username: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM user_notifications WHERE username=?",
        (str(username or "").strip(),),
    ).fetchone()
    return int(row[0] or 0) if row else 0


def mark_read(
    conn: sqlite3.Connection,
    username: str,
    *,
    ids: list[int] | None = None,
    mark_all: bool = False,
    now: int = 0,
) -> int:
    """Mark specific rows (or everything) as read; returns how many changed."""
    username = str(username or "").strip()
    if not username:
        return 0
    if mark_all:
        cursor = conn.execute(
            "UPDATE user_notifications SET read_at=? WHERE username=? AND read_at IS NULL",
            (int(now), username),
        )
        return int(cursor.rowcount or 0)
    normalized = []
    for value in ids or []:
        try:
            normalized.append(int(value))
        except (TypeError, ValueError):
            continue
    if not normalized:
        return 0
    placeholders = ",".join("?" for _ in normalized)
    cursor = conn.execute(
        f"UPDATE user_notifications SET read_at=? WHERE username=? AND read_at IS NULL AND id IN ({placeholders})",
        [int(now), username, *normalized],
    )
    return int(cursor.rowcount or 0)


def prune_notifications(conn: sqlite3.Connection, username: str, *, keep: int = KEEP_NOTIFICATIONS_PER_USER) -> int:
    """Keep only the newest ``keep`` rows for one user."""
    username = str(username or "").strip()
    if not username:
        return 0
    limit = max(1, int(keep))
    cursor = conn.execute(
        "DELETE FROM user_notifications WHERE username=? AND id NOT IN "
        "(SELECT id FROM user_notifications WHERE username=? ORDER BY id DESC LIMIT ?)",
        (username, username, limit),
    )
    return int(cursor.rowcount or 0)


def prune_orphan_notifications(conn: sqlite3.Connection) -> int:
    """Drop inbox rows whose owner no longer exists (legacy FK-less databases)."""
    cursor = conn.execute(
        "DELETE FROM user_notifications WHERE username NOT IN (SELECT username FROM users)"
    )
    return int(cursor.rowcount or 0)


__all__ = [
    "DEFAULT_NOTIFICATION_LIMIT",
    "KEEP_NOTIFICATIONS_PER_USER",
    "KINDS",
    "MAX_NOTIFICATION_LIMIT",
    "create_notification",
    "list_notifications",
    "mark_read",
    "notification_payload",
    "prune_notifications",
    "prune_orphan_notifications",
    "total_count",
    "unread_count",
]
