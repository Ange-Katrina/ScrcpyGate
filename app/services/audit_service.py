"""Audit request helpers and bounded audit export operations."""

from __future__ import annotations

import asyncio
import json
import logging
from fastapi import HTTPException, Request, WebSocket

from .. import security, storage
from ..logging_config import normalize_request_id, log_event
from ..runtime import AUDIT_READ_BARRIER_TIMEOUT, RuntimeState, runtime_for


log = logging.getLogger("webscrcpy.main")
AUDIT_EXPORT_MAX_ROWS = 10000
SQLITE_INT_MAX = (1 << 63) - 1


def audit_request(
    request: Request,
    user: dict | str | None,
    action: str,
    *,
    detail: str = "",
    outcome: str = "success",
    reason: str = "",
    severity: str = "info",
    target_type: str = "route",
    target_id: str = "",
    metadata: dict | None = None,
    dedupe_key: str = "",
) -> bool:
    if isinstance(user, dict):
        username = str(user.get("username") or "")
        role = str(user.get("role") or "")
    else:
        username = str(user or "")
        role = "system" if username == "system" else "unknown"
    return security.queue_audit_event(
        request,
        username=username,
        actor_role=role,
        action=action,
        detail=detail,
        outcome=outcome,
        reason=reason,
        severity=severity,
        target_type=target_type,
        target_id=target_id,
        metadata=metadata,
        dedupe_key=dedupe_key,
    )


async def audit_websocket_event(
    websocket: WebSocket,
    user: dict | None,
    action: str,
    *,
    outcome: str,
    reason: str = "",
    severity: str = "warning",
    target_type: str = "websocket",
    target_id: str = "",
    metadata: dict | None = None,
) -> None:
    path = str(getattr(getattr(websocket, "url", None), "path", "") or "")
    if path.startswith("/alas/embed/proxy"):
        socket_name = "alas"
    elif path == "/ws/events":
        socket_name = "events"
    elif path.endswith("/video"):
        socket_name = "video"
    elif path.endswith("/control"):
        socket_name = "control"
    else:
        socket_name = "websocket"
    event_metadata = {"channel": "websocket", "socket": socket_name}
    event_metadata.update(metadata or {})
    event = {
        "username": str((user or {}).get("username") or "anonymous"),
        "action": action,
        "actor_role": str((user or {}).get("role") or "anonymous"),
        "outcome": outcome,
        "reason": reason,
        "severity": severity,
        "target_type": target_type,
        "target_id": target_id,
        "request_id": normalize_request_id(websocket.headers.get("x-request-id", "")),
        "source_ip": security.client_ip(websocket),
        "user_agent": str(websocket.headers.get("user-agent", "") or ""),
        "metadata": event_metadata,
    }
    try:
        dispatcher = runtime_for(websocket).audit_dispatcher
        if dispatcher is not None:
            dispatcher.submit(event)
            return
        await asyncio.to_thread(storage.record_audit_event, **event)
    except Exception as exc:
        log_event(
            log,
            "security.audit.persist_failed",
            level=logging.CRITICAL,
            error_type=type(exc).__name__,
        )


def safe_log_path(path: str) -> str:
    normalized = str(path or "/").replace("\\", "/")
    if normalized.startswith("/static/"):
        return "/static/*"
    if normalized.startswith("/alas/embed/proxy"):
        return "/alas/embed/proxy/*"
    return normalized


def should_audit_http_failure(request: Request, status_code: int) -> bool:
    if status_code < 400 or request.url.path.startswith(("/static/", "/healthz")):
        return False
    if request.method.upper() not in {"GET", "HEAD", "OPTIONS"}:
        return True
    return request.url.path.startswith(("/admin", "/api/admin", "/alas/embed", "/api/alas"))


async def flush_request_audit_events(request: Request) -> None:
    events = security.pop_audit_events(request)
    dispatcher = runtime_for(request).audit_dispatcher
    if dispatcher is not None:
        for event in events:
            dispatcher.submit(event)
        return
    for event in events:
        try:
            await asyncio.to_thread(storage.record_audit_event, **event)
        except Exception as exc:
            log_event(
                log,
                "security.audit.persist_failed",
                level=logging.CRITICAL,
                error_type=type(exc).__name__,
            )


async def await_audit_read_barrier(runtime: RuntimeState) -> bool:
    dispatcher = runtime.audit_dispatcher
    if dispatcher is None:
        return True
    consistent = await asyncio.to_thread(dispatcher.barrier, AUDIT_READ_BARRIER_TIMEOUT)
    if not consistent:
        stats = dispatcher.stats()
        log_event(
            log,
            "security.audit.read_barrier_timeout",
            level=logging.WARNING,
            queue_size=stats.get("queue_size", 0),
            unfinished=stats.get("unfinished", 0),
        )
    return consistent


def audit_filter_int(value: object, field: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid {field}") from exc
    if parsed < 0 or parsed > SQLITE_INT_MAX:
        raise HTTPException(status_code=400, detail=f"invalid {field}")
    return parsed


def audit_filters(values: dict) -> dict:
    return {
        "actor": str(values.get("actor") or "").strip(),
        "action": str(values.get("action") or "").strip(),
        "outcome": str(values.get("outcome") or "").strip(),
        "severity": str(values.get("severity") or "").strip(),
        "request_id": str(values.get("request_id") or "").strip(),
        "from_ts": audit_filter_int(values.get("from_ts"), "from_ts"),
        "to_ts": audit_filter_int(values.get("to_ts"), "to_ts"),
    }


def audit_csv_cell(value: object) -> str:
    if isinstance(value, (dict, list, tuple)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    else:
        text = str(value if value is not None else "")
    if text[:1] in {"=", "+", "-", "@"}:
        return f"'{text}"
    return text


async def collect_audit_export(filters: dict) -> list[dict]:
    events: list[dict] = []
    before_id = None
    while len(events) < AUDIT_EXPORT_MAX_ROWS:
        page = await asyncio.to_thread(
            storage.query_audit_events,
            before_id=before_id,
            limit=min(500, AUDIT_EXPORT_MAX_ROWS - len(events)),
            **filters,
        )
        items = page.get("items") or []
        events.extend(items)
        before_id = page.get("next_before_id")
        if not page.get("has_more") or not items or before_id is None:
            break
    return events[:AUDIT_EXPORT_MAX_ROWS]
