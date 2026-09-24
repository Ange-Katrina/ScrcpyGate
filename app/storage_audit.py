"""Read-only audit projections and queries.

Audit writes, retention and schema migrations stay in :mod:`app.storage`.
Every query receives the connection factory explicitly so the compatibility
facade remains the only owner of the process database lifecycle.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from collections.abc import Callable


ConnectionFactory = Callable[[], sqlite3.Connection]
TextSanitizer = Callable[[object, int], str]
NameCleaner = Callable[[object, str], str]
IntegerCleaner = Callable[[object], int]


def hash_payload(row: dict | sqlite3.Row) -> dict:
    return {
        "event_id": str(row["event_id"] or ""),
        "ts": int(row["ts"]),
        "username": str(row["username"] or ""),
        "actor_role": str(row["actor_role"] or ""),
        "action": str(row["action"] or ""),
        "target_type": str(row["target_type"] or ""),
        "target_id": str(row["target_id"] or ""),
        "outcome": str(row["outcome"] or ""),
        "reason": str(row["reason"] or ""),
        "severity": str(row["severity"] or ""),
        "request_id": str(row["request_id"] or ""),
        "source_ip": str(row["source_ip"] or ""),
        "user_agent": str(row["user_agent"] or ""),
        "detail": str(row["detail"] or ""),
        "metadata_json": str(row["metadata_json"] or "{}"),
        "schema_version": int(row["schema_version"] or 1),
        "dedupe_key": str(row["dedupe_key"] or ""),
    }


def event_hash(previous_hash: str, row: dict | sqlite3.Row) -> str:
    canonical = json.dumps(hash_payload(row), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{previous_hash}\n{canonical}".encode("utf-8")).hexdigest()


def event_dashboard_visible(event: dict | sqlite3.Row | None) -> bool:
    """Return whether an audit event is actionable enough for the dashboard."""
    if not event:
        return False
    action = str(event.get("action", "") if hasattr(event, "get") else event["action"] or "").strip().lower()
    reason = str(event.get("reason", "") if hasattr(event, "get") else event["reason"] or "").strip().lower()
    metadata = event.get("metadata", {}) if hasattr(event, "get") else {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (TypeError, ValueError, json.JSONDecodeError):
            metadata = {}
    metadata = metadata if isinstance(metadata, dict) else {}
    permission = str(metadata.get("permission") or "").strip().lower()
    event_name = str(metadata.get("event") or "").strip().lower()
    if action == "alas_embed_ws_denied" and (
        reason == "filtered" or permission == "restricted" or event_name == "run_script"
    ):
        return False
    if action in {"authentication", "admin_access", "csrf_validation", "http_boundary"}:
        return False
    # 404/405 are client or scanner noise (probed routes, already-gone
    # sessions), not actionable service failures. They stay in /logs.
    if action == "http_operation" and reason in {"http_404", "http_405"}:
        return False
    return True


def alert_projection(event: dict) -> dict | None:
    if not event_dashboard_visible(event):
        return None
    action = str(event.get("action") or "event").lower()
    target_type = str(event.get("target_type") or "").lower()
    outcome = str(event.get("outcome") or "").lower()
    reason = str(event.get("reason") or "").lower()
    if "device" in action or target_type == "device":
        alert_type, title = "device_offline", "设备异常"
    elif "user" in action or target_type in {"account", "account_permissions"} or "account" in reason:
        alert_type, title = "account_status", "账户状态异常"
    elif "alas" in action or target_type.startswith("alas") or "alas" in reason:
        alert_type, title = "alas_error", "ALAS 异常"
    elif "mirror" in action or "video" in action or target_type in {"mirror", "video_transport"}:
        alert_type, title = "mirror_failure", "投屏异常"
    elif outcome in {"failure", "error"}:
        alert_type, title = "service_failure", "服务异常"
    else:
        alert_type, title = "service_failure", "系统异常"
    detail = str(event.get("detail") or "").strip()
    summary = (detail or str(event.get("reason") or "操作未成功").strip() or "请查看日志了解详情")[:160]
    event_id = str(event.get("event_id") or "")
    return {
        "alert_type": alert_type,
        "title": title,
        "summary": summary,
        "log_url": f"/logs?event_id={event_id}" if event_id else "/logs",
        "dashboard_visible": 1,
    }


def row_to_dict(row: sqlite3.Row | dict | None) -> dict | None:
    if row is None:
        return None
    result = dict(row)
    raw_metadata = result.pop("metadata_json", "{}") or "{}"
    try:
        metadata = json.loads(raw_metadata)
    except (TypeError, ValueError, json.JSONDecodeError):
        metadata = {"_invalid": True}
    result["metadata"] = metadata if isinstance(metadata, dict) else {"value": metadata}
    return result


def alert_row_to_dict(row: sqlite3.Row | dict | None) -> dict | None:
    if row is None:
        return None
    result = dict(row)
    raw_metadata = result.pop("metadata_json", "{}") or "{}"
    try:
        metadata = json.loads(raw_metadata)
    except (TypeError, ValueError, json.JSONDecodeError):
        metadata = {"_invalid": True}
    result["metadata"] = metadata if isinstance(metadata, dict) else {"value": metadata}
    result["handled"] = result.get("handled_at") is not None
    result["dashboard_visible"] = bool(result.get("dashboard_visible", 1))
    result["alert_type"] = str(result.get("alert_type") or "service_failure")
    result["title"] = str(result.get("title") or "系统异常")
    result["summary"] = str(result.get("summary") or result.get("detail") or "请查看日志了解详情")[:160]
    result["log_url"] = str(
        result.get("log_url")
        or (f"/logs?event_id={result.get('event_id')}" if result.get("event_id") else "/logs")
    )
    return result


def recent_audit(limit: int, *, connect: ConnectionFactory) -> list[dict]:
    page_size = max(10, min(int(limit), 1000))
    with connect() as conn:
        rows = conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (page_size,)).fetchall()
    return [row_to_dict(row) for row in reversed(rows)]


def list_audit_alerts(*, limit: int = 100, include_handled: bool = False, connect: ConnectionFactory) -> dict:
    page_size = max(1, min(int(limit), 500))
    where = " WHERE dashboard_visible=1" if include_handled else " WHERE dashboard_visible=1 AND handled_at IS NULL"
    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM audit_alerts{where} ORDER BY ts DESC, audit_id DESC LIMIT ?",
            (page_size,),
        ).fetchall()
        pending = conn.execute(
            "SELECT COUNT(*) FROM audit_alerts WHERE dashboard_visible=1 AND handled_at IS NULL"
        ).fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM audit_alerts WHERE dashboard_visible=1").fetchone()[0]
    items = [alert_row_to_dict(row) for row in rows]
    return {"items": items, "alerts": items, "pending_count": int(pending or 0), "total": int(total or 0)}


def resolve_audit_alert(
    event_id: str,
    handled_by: str,
    *,
    connect: ConnectionFactory,
    sanitize_text: TextSanitizer,
    now: Callable[[], int],
) -> dict | None:
    normalized_id = sanitize_text(event_id, 128)
    actor = sanitize_text(handled_by, 128)
    if not normalized_id or not actor:
        return None
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM audit_alerts WHERE event_id=?", (normalized_id,)).fetchone()
        if row is None:
            conn.rollback()
            return None
        if row["handled_at"] is None:
            conn.execute(
                "UPDATE audit_alerts SET handled_at=?, handled_by=? WHERE event_id=?",
                (now(), actor, normalized_id),
            )
        updated = conn.execute("SELECT * FROM audit_alerts WHERE event_id=?", (normalized_id,)).fetchone()
        conn.commit()
    return alert_row_to_dict(updated)


def query_audit_events(
    *,
    before_id: int | None = None,
    actor: str = "",
    action: str = "",
    outcome: str = "",
    severity: str = "",
    request_id: str = "",
    device_id: str = "",
    source_ip: str = "",
    target: str = "",
    from_ts: int | None = None,
    to_ts: int | None = None,
    limit: int = 100,
    connect: ConnectionFactory,
    sanitize_text: TextSanitizer,
    clean_name: NameCleaner,
    sqlite_int: IntegerCleaner,
) -> dict:
    page_size = max(1, min(int(limit), 500))
    clauses: list[str] = []
    params: list[object] = []
    if before_id is not None:
        clauses.append("id < ?")
        params.append(sqlite_int(before_id))
    if actor == "none":
        clauses.append("username IN ('', '?', 'anonymous')")
    elif actor:
        clauses.append("username = ?")
        params.append(sanitize_text(actor, 128))
    if action:
        clauses.append("action = ?")
        params.append(clean_name(action, "event"))
    if outcome:
        clauses.append("outcome = ?")
        params.append(clean_name(outcome, "unknown"))
    if severity:
        clauses.append("severity = ?")
        params.append(clean_name(severity, "info"))
    if request_id:
        clauses.append("request_id = ?")
        params.append(sanitize_text(request_id, 96))
    if device_id:
        device = sanitize_text(device_id, 256)
        clauses.append("((target_type = 'device' AND target_id = ?) "
                       "OR (target_type = 'device_viewer' AND substr(target_id, 1, length(?) + 1) = ? || ':') "
                       "OR json_extract(CASE WHEN json_valid(metadata_json) THEN metadata_json ELSE '{}' END, '$.device_id') = ?)")
        params.extend([device, device, device, device])
    if source_ip:
        clauses.append("instr(source_ip, ?) > 0")
        params.append(sanitize_text(source_ip, 64))
    if target:
        clauses.append("instr(target_id, ?) > 0")
        params.append(sanitize_text(target, 256))
    if from_ts is not None:
        clauses.append("ts >= ?")
        params.append(sqlite_int(from_ts))
    if to_ts is not None:
        clauses.append("ts <= ?")
        params.append(sqlite_int(to_ts))
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(page_size + 1)
    with connect() as conn:
        rows = conn.execute(f"SELECT * FROM audit_log{where} ORDER BY id DESC LIMIT ?", params).fetchall()
    has_more = len(rows) > page_size
    page = rows[:page_size]
    items = [row_to_dict(row) for row in page]
    return {
        "items": items,
        "has_more": has_more,
        "next_before_id": int(page[-1]["id"]) if has_more and page else None,
    }


def audit_facets(*, connect: ConnectionFactory) -> dict:
    """Keep selectors independent of the current filtered page, including deleted actors."""
    with connect() as conn:
        actors = conn.execute(
            "SELECT username FROM users UNION SELECT DISTINCT username FROM audit_log ORDER BY username LIMIT 500"
        ).fetchall()
        devices = conn.execute(
            "SELECT id, name FROM devices UNION SELECT DISTINCT target_id, target_id FROM audit_log "
            "WHERE target_type='device' AND target_id NOT IN (SELECT id FROM devices) ORDER BY name LIMIT 500"
        ).fetchall()
    return {
        "actors": [{"id": row["username"], "username": row["username"]} for row in actors],
        "devices": [{"id": row["id"], "name": row["name"]} for row in devices],
    }


def get_audit_event(
    audit_id: int | str,
    *,
    connect: ConnectionFactory,
    sanitize_text: TextSanitizer,
    sqlite_int: IntegerCleaner,
) -> dict | None:
    with connect() as conn:
        if isinstance(audit_id, int) or str(audit_id).isdecimal():
            row = conn.execute("SELECT * FROM audit_log WHERE id=?", (sqlite_int(audit_id),)).fetchone()
        else:
            row = conn.execute("SELECT * FROM audit_log WHERE event_id=?", (sanitize_text(audit_id, 128),)).fetchone()
    return row_to_dict(row)


def audit_summary(*, from_ts: int | None = None, to_ts: int | None = None, connect: ConnectionFactory) -> dict:
    clauses: list[str] = []
    params: list[object] = []
    if from_ts is not None:
        clauses.append("ts >= ?")
        params.append(max(0, int(from_ts)))
    if to_ts is not None:
        clauses.append("ts <= ?")
        params.append(max(0, int(to_ts)))
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with connect() as conn:
        totals = conn.execute(
            f"""
            SELECT COUNT(*) AS total, MAX(id) AS latest_id,
                   SUM(CASE WHEN outcome='denied' OR severity IN ('error','critical') THEN 1 ELSE 0 END) AS high_risk
            FROM audit_log{where}
            """,
            params,
        ).fetchone()
        outcome_rows = conn.execute(f"SELECT outcome, COUNT(*) AS count FROM audit_log{where} GROUP BY outcome", params).fetchall()
        severity_rows = conn.execute(f"SELECT severity, COUNT(*) AS count FROM audit_log{where} GROUP BY severity", params).fetchall()
        action_rows = conn.execute(
            f"SELECT action, COUNT(*) AS count FROM audit_log{where} GROUP BY action ORDER BY count DESC, action LIMIT 10",
            params,
        ).fetchall()
    return {
        "total": int(totals["total"] or 0),
        "latest_id": int(totals["latest_id"] or 0),
        "high_risk": int(totals["high_risk"] or 0),
        "by_outcome": {row["outcome"]: int(row["count"]) for row in outcome_rows},
        "by_severity": {row["severity"]: int(row["count"]) for row in severity_rows},
        "top_actions": [{"action": row["action"], "count": int(row["count"])} for row in action_rows],
    }


def verify_audit_integrity(*, connect: ConnectionFactory, on_error=None) -> dict:
    """Verify the local SHA-256 chain and stored head."""
    try:
        with connect() as conn:
            conn.execute("BEGIN")
            state = conn.execute("SELECT * FROM audit_integrity_state WHERE singleton=1").fetchone()
            if state is None:
                return {"ok": False, "checked": 0, "error": "state_missing"}
            previous_hash = str(state["anchor_event_hash"] or "")
            checked = 0
            head_id = 0
            for row in conn.execute("SELECT * FROM audit_log ORDER BY id"):
                if str(row["prev_hash"] or "") != previous_hash:
                    return {"ok": False, "checked": checked, "error": "chain_link_mismatch", "audit_id": int(row["id"])}
                expected_hash = event_hash(previous_hash, row)
                if not hmac.compare_digest(str(row["event_hash"] or ""), expected_hash):
                    return {"ok": False, "checked": checked, "error": "event_hash_mismatch", "audit_id": int(row["id"])}
                previous_hash = expected_hash
                head_id = int(row["id"])
                checked += 1
            if (
                int(state["event_count"]) != checked
                or int(state["head_event_id"]) != head_id
                or not hmac.compare_digest(str(state["head_event_hash"] or ""), previous_hash)
            ):
                return {"ok": False, "checked": checked, "error": "chain_head_mismatch"}
            return {
                "ok": True,
                "checked": checked,
                "head_event_id": head_id,
                "head_event_hash": previous_hash,
                "anchor_event_id": int(state["anchor_event_id"] or 0),
                "pruned_count": int(state["pruned_count"] or 0),
            }
    except Exception:
        if on_error is not None:
            try:
                on_error()
            except Exception:
                # Logging must never turn an integrity result into a second
                # failure or make the public verification endpoint unstable.
                pass
        return {"ok": False, "checked": 0, "error": "verification_failed"}


__all__ = [
    "hash_payload",
    "event_hash",
    "event_dashboard_visible",
    "alert_projection",
    "row_to_dict",
    "alert_row_to_dict",
    "recent_audit",
    "list_audit_alerts",
    "resolve_audit_alert",
    "query_audit_events",
    "get_audit_event",
    "audit_summary",
    "verify_audit_integrity",
]
