"""Administrator audit log, alert, and runtime log routes."""

from __future__ import annotations

import asyncio
import csv
import io
import time

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from .. import security, storage
from ..http_helpers import parse_body, public_error_detail
from ..logging_config import logging_health, runtime_log_snapshot
from ..runtime import runtime_for
from ..services.log_export_service import LogExportError, build_log_archive
from ..services.audit_service import (
    AUDIT_EXPORT_MAX_ROWS,
    SQLITE_INT_MAX,
    audit_csv_cell as _audit_csv_cell,
    audit_filters as _audit_filters,
    audit_request,
    await_audit_read_barrier as _await_audit_read_barrier,
    collect_audit_export as _collect_audit_export,
)

router = APIRouter()
AUDIT_EXPORT_MAX_SECONDS = 31 * 24 * 60 * 60


class _LogArchiveResponse(StreamingResponse):
    def __init__(self, artifact, **kwargs):
        self.artifact = artifact
        super().__init__(artifact.chunks(encoded=True), **kwargs)

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.artifact.close()

@router.get("/api/admin/logs")
async def admin_logs(
    request: Request,
    limit: int = Query(default=100, ge=1, le=200),
    before: int | None = Query(default=None, ge=0, le=SQLITE_INT_MAX),
    actor: str = "",
    action: str = "",
    outcome: str = "",
    severity: str = "",
    request_id: str = "",
    from_ts: int | None = Query(default=None, ge=0, le=SQLITE_INT_MAX),
    to_ts: int | None = Query(default=None, ge=0, le=SQLITE_INT_MAX),
):
    security.require_admin(request)
    audit_consistent = await _await_audit_read_barrier(runtime_for(request))
    filters = {
        "actor": actor,
        "action": action,
        "outcome": outcome,
        "severity": severity,
        "request_id": request_id,
        "from_ts": from_ts,
        "to_ts": to_ts,
    }
    page, summary = await asyncio.gather(
        asyncio.to_thread(
            storage.query_audit_events,
            before_id=before,
            limit=max(1, min(limit, 200)),
            **filters,
        ),
        asyncio.to_thread(
            storage.audit_summary,
            from_ts=max(0, int(time.time()) - 86400),
            to_ts=int(time.time()),
        ),
    )
    response = JSONResponse(
        {
            "logs": list(reversed(page["items"])),
            "page": {
                "has_more": bool(page["has_more"]),
                "next_cursor": page["next_before_id"],
            },
            "summary": summary,
        }
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Audit-Consistent"] = "true" if audit_consistent else "false"
    return response


@router.get("/api/admin/alerts")
async def admin_alerts(request: Request, include_handled: bool = False, limit: int = Query(default=100, ge=1, le=500)):
    """Return durable pending alerts independently of the audit time window."""
    security.require_admin(request)
    response = JSONResponse(await asyncio.to_thread(storage.list_audit_alerts, limit=limit, include_handled=include_handled))
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/api/admin/alerts/{event_id}/resolve")
async def admin_resolve_alert(event_id: str, request: Request):
    security.verify_csrf(request)
    admin = security.require_admin(request)
    resolved = await asyncio.to_thread(storage.resolve_audit_alert, event_id, admin["username"])
    if resolved is None:
        raise HTTPException(status_code=404, detail="alert not found")
    audit_request(
        request,
        admin,
        "alert_resolve",
        target_type="audit_alert",
        target_id=str(resolved.get("event_id") or event_id),
        metadata={"handled_at": resolved.get("handled_at")},
    )
    pending = await asyncio.to_thread(storage.list_audit_alerts, limit=1)
    response = JSONResponse({"ok": True, "alert": resolved, "pending_count": pending.get("pending_count", 0)})
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/api/admin/logs/export")
async def admin_export_logs(request: Request):
    security.verify_csrf(request)
    admin = security.require_admin(request)
    audit_consistent = await _await_audit_read_barrier(runtime_for(request))
    payload = await parse_body(request)
    export_format = str(payload.get("format") or "csv").strip().lower()
    if export_format not in {"csv", "json"}:
        raise HTTPException(status_code=400, detail="invalid audit export format")
    filters = _audit_filters(payload)
    now = int(time.time())
    to_ts = filters["to_ts"] if filters["to_ts"] is not None else now
    from_ts = filters["from_ts"] if filters["from_ts"] is not None else max(0, to_ts - AUDIT_EXPORT_MAX_SECONDS)
    if from_ts > to_ts or to_ts - from_ts > AUDIT_EXPORT_MAX_SECONDS:
        raise HTTPException(status_code=400, detail="audit export range must not exceed 31 days")
    filters["from_ts"] = from_ts
    filters["to_ts"] = to_ts
    events = await _collect_audit_export(filters)
    chronological = list(reversed(events))
    audit_request(
        request,
        admin,
        "audit_export",
        target_type="audit_log",
        target_id=export_format,
        metadata={"format": export_format, "exported_count": len(chronological), "from_ts": from_ts, "to_ts": to_ts},
    )
    headers = {
        "Cache-Control": "no-store",
        "Pragma": "no-cache",
        "Content-Disposition": f'attachment; filename="scrcpygate-audit.{export_format}"',
        "X-Audit-Consistent": "true" if audit_consistent else "false",
    }
    if export_format == "json":
        return JSONResponse(
            {"events": chronological, "exported_count": len(chronological), "truncated": len(events) >= AUDIT_EXPORT_MAX_ROWS},
            headers=headers,
        )
    columns = (
        "ts", "event_id", "username", "actor_role", "action", "target_type", "target_id",
        "outcome", "reason", "severity", "request_id", "source_ip", "detail", "metadata",
    )
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for event in chronological:
        writer.writerow({column: _audit_csv_cell(event.get(column)) for column in columns})
    return Response(content="\ufeff" + stream.getvalue(), media_type="text/csv; charset=utf-8", headers=headers)


@router.post("/api/admin/logs/export-full")
async def admin_export_full_logs(request: Request):
    security.verify_csrf(request)
    admin = security.require_admin(request)
    consistent = await _await_audit_read_barrier(runtime_for(request))
    if not consistent:
        raise HTTPException(status_code=503, detail="审计写入尚未完成，请稍后重新导出")
    job = asyncio.create_task(asyncio.to_thread(build_log_archive, audit_consistent=consistent))
    try:
        artifact = await asyncio.shield(job)
    except asyncio.CancelledError:
        def cleanup(completed):
            if not completed.cancelled() and completed.exception() is None:
                completed.result().close()
        job.add_done_callback(cleanup)
        raise
    except LogExportError as exc:
        messages = {
            "export_busy": (409, "已有完整日志正在导出，请稍后重试"),
            "export_too_large": (413, "日志超过 LOG_EXPORT_MAX_BYTES 限制，未生成不完整文件；请调整限额后重试"),
            "export_timeout": (503, "完整日志导出超时，未生成不完整文件"),
            "export_logs_changed": (409, "运行日志正在轮转，请重新导出"),
            "export_log_unreadable": (503, "运行日志文件不可读取，请检查文件类型与权限"),
        }
        status, detail = messages.get(str(exc), (503, "完整日志导出失败，请稍后重试"))
        raise HTTPException(status_code=status, detail=detail) from None
    except Exception:
        raise HTTPException(status_code=503, detail="完整日志导出失败，请检查日志目录权限与临时磁盘空间") from None
    try:
        audit_request(request, admin, "audit_export", target_type="audit_log", target_id="full_zip",
                      metadata={"format": "zip", "scope": "all_retained_logs", **artifact.counts})
        # JSON transport prevents download-manager extensions from replaying the
        # authenticated, CSRF-protected POST as a separate attachment request.
        return _LogArchiveResponse(artifact, media_type="application/json", headers={
            "Cache-Control": "no-store", "Pragma": "no-cache",
            "X-Audit-Consistent": "true", "X-Content-Type-Options": "nosniff",
        })
    except BaseException:
        artifact.close()
        raise


@router.post("/api/admin/logs/integrity-check")
async def admin_verify_audit_integrity(request: Request):
    security.verify_csrf(request)
    admin = security.require_admin(request)
    audit_consistent = await _await_audit_read_barrier(runtime_for(request))
    result = await asyncio.to_thread(storage.verify_audit_integrity)
    audit_request(
        request,
        admin,
        "audit_integrity_check",
        outcome="success" if result.get("ok") else "failure",
        reason="" if result.get("ok") else str(result.get("error") or "verification_failed"),
        severity="info" if result.get("ok") else "critical",
        target_type="audit_log",
        target_id="local_hash_chain",
        metadata={"checked": result.get("checked", 0)},
    )
    response = JSONResponse(result)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Audit-Consistent"] = "true" if audit_consistent else "false"
    return response


@router.get("/api/admin/logs/{event_id}")
async def admin_audit_event(event_id: str, request: Request):
    security.require_admin(request)
    audit_consistent = await _await_audit_read_barrier(runtime_for(request))
    event = await asyncio.to_thread(storage.get_audit_event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="audit event not found")
    response = JSONResponse({"event": event})
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Audit-Consistent"] = "true" if audit_consistent else "false"
    return response


@router.get("/api/admin/runtime-logs")
async def admin_runtime_logs(request: Request, lines: int = 300, min_severity: str = ""):
    security.require_admin(request)
    try:
        raw_logs, entries, runtime_meta = await asyncio.to_thread(
            runtime_log_snapshot, lines, min_severity
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=public_error_detail(exc)) from exc
    meta = logging_health()
    raw_text = runtime_meta.pop("raw_text", None)
    if not isinstance(raw_text, str):
        raw_text = "\n".join(raw_logs)
    meta.update(runtime_meta)
    dispatcher = runtime_for(request).audit_dispatcher
    meta["audit_queue"] = (
        dispatcher.stats()
        if dispatcher is not None
        else {"running": False, "queue_size": 0, "dropped_total": 0}
    )
    response = JSONResponse({"logs": raw_logs, "entries": entries, "meta": meta, "raw_text": raw_text})
    response.headers["Cache-Control"] = "no-store"
    return response

__all__ = [name for name in globals() if name.startswith("admin_")] + ["router"]
