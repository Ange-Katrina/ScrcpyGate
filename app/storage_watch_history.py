"""Viewer watch history domain operations.

The compatibility module ``app.storage`` owns the public call signatures and
passes its current connection factory into this module. Keeping the database
dependency explicit prevents a second SQLite singleton or an import cycle.
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from collections.abc import Callable


VIEWER_WATCH_HISTORY_DEFAULT_LIMIT = 10
VIEWER_WATCH_HISTORY_MAX_LIMIT = 100
VIEWER_WATCH_REASON_MAX_LENGTH = 48
SQLITE_INT_MAX = (1 << 63) - 1

ConnectionFactory = Callable[[], sqlite3.Connection]


def watch_timestamp_ms(value: object | None = None) -> int:
    if value is None:
        return max(0, min(int(time.time_ns() // 1_000_000), SQLITE_INT_MAX))
    try:
        normalized = int(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("invalid_watch_timestamp") from None
    return max(0, min(normalized, SQLITE_INT_MAX))


def start_viewer_watch(
    username: str,
    device_id: str | None = None,
    *,
    started_at_ms: int | None = None,
    session_id: str | None = None,
    connect: ConnectionFactory,
) -> str:
    """Persist the start of one authenticated video WebSocket watch."""
    normalized_user = str(username or "").strip()
    if not normalized_user:
        raise ValueError("invalid_username")
    normalized_device = str(device_id or "").strip() or None
    normalized_id = str(session_id or uuid.uuid4().hex).strip()
    if not normalized_id or len(normalized_id) > 96:
        raise ValueError("invalid_watch_session_id")
    started = watch_timestamp_ms(started_at_ms)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO viewer_watch_sessions(
                id, username, device_id, started_at_ms, ended_at_ms, duration_ms, end_reason
            ) VALUES(?,?,?,?,NULL,0,'')
            """,
            (normalized_id, normalized_user, normalized_device, started),
        )
        conn.commit()
    return normalized_id


def finish_viewer_watch(
    session_id: str,
    *,
    ended_at_ms: int | None = None,
    end_reason: str = "disconnect",
    connect: ConnectionFactory,
) -> dict | None:
    """Close one watch row idempotently and return its persisted values."""
    normalized_id = str(session_id or "").strip()
    if not normalized_id:
        return None
    ended = watch_timestamp_ms(ended_at_ms)
    reason = str(end_reason or "disconnect").strip()[:VIEWER_WATCH_REASON_MAX_LENGTH] or "disconnect"
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM viewer_watch_sessions WHERE id=?",
            (normalized_id,),
        ).fetchone()
        if row is None:
            return None
        if row["ended_at_ms"] is None:
            started = max(0, int(row["started_at_ms"] or 0))
            effective_end = max(started, ended)
            duration = max(0, effective_end - started)
            conn.execute(
                """
                UPDATE viewer_watch_sessions
                   SET ended_at_ms=?, duration_ms=?, end_reason=?
                 WHERE id=? AND ended_at_ms IS NULL
                """,
                (effective_end, duration, reason, normalized_id),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM viewer_watch_sessions WHERE id=?",
                (normalized_id,),
            ).fetchone()
    return dict(row) if row is not None else None


def finalize_open_viewer_watch_sessions(*, ended_at_ms: int | None = None, connect: ConnectionFactory) -> int:
    """Bound all rows left open by a process restart."""
    ended = watch_timestamp_ms(ended_at_ms)
    with connect() as conn:
        cursor = conn.execute(
            """
            UPDATE viewer_watch_sessions
               SET ended_at_ms=?,
                   duration_ms=CASE
                       WHEN ? > started_at_ms THEN ? - started_at_ms
                       ELSE 0
                   END,
                   end_reason='process_restart'
             WHERE ended_at_ms IS NULL
            """,
            (ended, ended, ended),
        )
        conn.commit()
        return int(cursor.rowcount or 0)


def empty_viewer_watch_stats() -> dict:
    return {
        "session_count": 0,
        "total_duration_ms": 0,
        "last_started_at_ms": None,
        "last_ended_at_ms": None,
        "last_watched_at_ms": None,
        "active_sessions": 0,
    }


def viewer_watch_stats_from_conn(
    conn: sqlite3.Connection,
    username: str | None = None,
    now_ms: int | None = None,
) -> dict[str, dict] | dict:
    current = watch_timestamp_ms(now_ms)
    clauses = ""
    params: list[object] = []
    if username is not None:
        clauses = " WHERE username=?"
        params.append(str(username or "").strip())
    rows = conn.execute(
        f"""
        SELECT username,
               COUNT(*) AS session_count,
               COALESCE(SUM(CASE WHEN ended_at_ms IS NULL
                                  THEN MAX(0, ? - started_at_ms)
                                  ELSE MAX(0, duration_ms) END), 0) AS total_duration_ms,
               MAX(started_at_ms) AS last_started_at_ms,
               MAX(ended_at_ms) AS last_ended_at_ms,
               MAX(CASE WHEN ended_at_ms IS NULL THEN started_at_ms ELSE ended_at_ms END) AS last_watched_at_ms,
               SUM(CASE WHEN ended_at_ms IS NULL THEN 1 ELSE 0 END) AS active_sessions
          FROM viewer_watch_sessions{clauses}
         GROUP BY username
        """,
        [current, *params],
    ).fetchall()

    def normalize(row: sqlite3.Row | None) -> dict:
        if row is None:
            return empty_viewer_watch_stats()
        return {
            "session_count": int(row["session_count"] or 0),
            "total_duration_ms": max(0, int(row["total_duration_ms"] or 0)),
            "last_started_at_ms": int(row["last_started_at_ms"]) if row["last_started_at_ms"] is not None else None,
            "last_ended_at_ms": int(row["last_ended_at_ms"]) if row["last_ended_at_ms"] is not None else None,
            "last_watched_at_ms": int(row["last_watched_at_ms"]) if row["last_watched_at_ms"] is not None else None,
            "active_sessions": int(row["active_sessions"] or 0),
        }

    if username is not None:
        return normalize(rows[0] if rows else None)
    return {str(row["username"]): normalize(row) for row in rows}


def viewer_watch_stats(username: str | None = None, *, connect: ConnectionFactory) -> dict:
    with connect() as conn:
        return viewer_watch_stats_from_conn(conn, username)


def viewer_watch_row(row: sqlite3.Row, now_ms: int | None = None) -> dict:
    current = watch_timestamp_ms(now_ms)
    started = max(0, int(row["started_at_ms"] or 0))
    ended = int(row["ended_at_ms"]) if row["ended_at_ms"] is not None else None
    duration = max(0, int(row["duration_ms"] or 0))
    if ended is None:
        duration = max(0, current - started)
    device_name = str(row["device_name"] or "") if "device_name" in row.keys() else None
    return {
        "id": str(row["id"]),
        "username": str(row["username"]),
        "device_id": str(row["device_id"] or "") or None,
        "device_name": device_name or None,
        "started_at_ms": started,
        "ended_at_ms": ended,
        "duration_ms": duration,
        "end_reason": str(row["end_reason"] or "") or None,
        "active": ended is None,
    }


def viewer_watch_history_from_conn(
    conn: sqlite3.Connection,
    username: str,
    page_size: int,
    page_offset: int,
    now_ms: int,
) -> list[dict]:
    rows = conn.execute(
        """
        SELECT v.*, d.name AS device_name
          FROM viewer_watch_sessions AS v
          LEFT JOIN devices AS d ON d.id=v.device_id
         WHERE v.username=?
         ORDER BY v.started_at_ms DESC, v.id DESC
         LIMIT ? OFFSET ?
        """,
        (username, page_size, page_offset),
    ).fetchall()
    return [viewer_watch_row(row, now_ms) for row in rows]


def bounded_watch_page(limit: object, offset: object) -> tuple[int, int]:
    try:
        page_size = max(1, min(int(limit), VIEWER_WATCH_HISTORY_MAX_LIMIT))
    except (TypeError, ValueError, OverflowError):
        page_size = VIEWER_WATCH_HISTORY_DEFAULT_LIMIT
    try:
        page_offset = max(0, int(offset))
    except (TypeError, ValueError, OverflowError):
        page_offset = 0
    return page_size, page_offset


def viewer_watch_history(
    username: str,
    *,
    limit: int = VIEWER_WATCH_HISTORY_DEFAULT_LIMIT,
    offset: int = 0,
    connect: ConnectionFactory,
) -> list[dict]:
    normalized_user = str(username or "").strip()
    if not normalized_user:
        return []
    page_size, page_offset = bounded_watch_page(limit, offset)
    with connect() as conn:
        return viewer_watch_history_from_conn(
            conn,
            normalized_user,
            page_size,
            page_offset,
            watch_timestamp_ms(),
        )


__all__ = [
    "VIEWER_WATCH_HISTORY_DEFAULT_LIMIT",
    "VIEWER_WATCH_HISTORY_MAX_LIMIT",
    "VIEWER_WATCH_REASON_MAX_LENGTH",
    "watch_timestamp_ms",
    "start_viewer_watch",
    "finish_viewer_watch",
    "finalize_open_viewer_watch_sessions",
    "empty_viewer_watch_stats",
    "viewer_watch_stats_from_conn",
    "viewer_watch_stats",
    "viewer_watch_row",
    "viewer_watch_history_from_conn",
    "bounded_watch_page",
    "viewer_watch_history",
]
