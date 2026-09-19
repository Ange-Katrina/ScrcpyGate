"""Administrator visitor-access records (VIS): query, hints, observation health, export.

Read-only apart from the export, which is audited like the audit-log export.  Everything
returned here was sanitized at write time (no query strings, no Referer, no bodies,
bounded path and UA); the renderer must escape it again.
"""

from __future__ import annotations

import asyncio
import csv
import io
import time

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from .. import i18n, security, storage
from ..http_helpers import parse_body
from ..runtime import runtime_for
from ..services.audit_service import SQLITE_INT_MAX, audit_csv_cell, audit_request

router = APIRouter()

ACCESS_EXPORT_MAX_ROWS = 10_000
ACCESS_EXPORT_MAX_SECONDS = 31 * 24 * 60 * 60
ACCESS_LIST_MAX_PAGE = 200
ACCESS_EXPORT_FORMATS = {"csv", "json"}


def _window(from_ts: object, to_ts: object) -> tuple[int, int]:
    """Clamp the query window server-side so a wide range cannot be requested."""
    now = int(time.time())
    try:
        end = int(to_ts) if to_ts not in (None, "") else now
    except (TypeError, ValueError):
        end = now
    if end <= 0:
        end = now
    try:
        start = int(from_ts) if from_ts not in (None, "") else max(0, end - ACCESS_EXPORT_MAX_SECONDS)
    except (TypeError, ValueError):
        start = max(0, end - ACCESS_EXPORT_MAX_SECONDS)
    if end < start:
        start, end = end, start
    if end - start > ACCESS_EXPORT_MAX_SECONDS:
        start = end - ACCESS_EXPORT_MAX_SECONDS
    return start, end


def _filters(
    *,
    source_ip: str = "",
    country: str = "",
    ban_state: str = "",
    identity: str = "",
    status: str = "",
    status_class: str = "",
    decision: str = "",
    kind: str = "",
    account: str = "",
    request_id: str = "",
    q: str = "",
    include_admin_poll: object = 0,
) -> dict:
    status_class = status_class.strip().lower()
    return {
        "source_ip": source_ip.strip()[:45],
        "country": country.strip()[:8].upper(),
        "ban_state": ban_state if ban_state in ("active", "clear") else "",
        "identity": identity.strip().lower()[:16],
        "status": int(status) if str(status).strip().isdigit() else 0,
        "status_class": status_class if status_class in {"4xx", "5xx"} else "",
        "decision": decision.strip()[:16],
        "kind": kind.strip()[:16],
        "account": account.strip()[:64],
        "request_id": request_id.strip()[:64],
        "q": q.strip()[:128],
        "include_admin_poll": bool(include_admin_poll),
    }


@router.get("/api/admin/access/summary")
async def admin_access_summary(
    request: Request,
    from_ts: int | None = Query(default=None, ge=0, le=SQLITE_INT_MAX),
    to_ts: int | None = Query(default=None, ge=0, le=SQLITE_INT_MAX),
    limit: int = Query(default=50, ge=1, le=ACCESS_LIST_MAX_PAGE),
    offset: int = Query(default=0, ge=0, le=1_000_000),
    source_ip: str = "",
    country: str = "",
    ban_state: str = "",
    status: str = "",
    decision: str = "",
    status_class: str = "",
    include_admin_poll: int = Query(default=0, ge=0, le=1),
):
    """IP-first view: per-IP counters for the window, plus the status strip and hints."""
    security.require_admin(request)
    start, end = _window(from_ts, to_ts)
    filters = _filters(source_ip=source_ip, country=country, ban_state=ban_state, status=status, decision=decision, status_class=status_class, include_admin_poll=include_admin_poll)
    page, counts, hints, drop_state, enabled, retention = await asyncio.gather(
        asyncio.to_thread(
            storage.query_access_ip_summaries,
            limit=limit, offset=offset, from_ts=start, to_ts=end, filters=filters,
        ),
        asyncio.to_thread(storage.access_status_counts, from_ts=start, to_ts=end, filters=filters),
        asyncio.to_thread(storage.access_scan_hints, now=end, include_admin_poll=bool(include_admin_poll)),
        asyncio.to_thread(storage.access_drop_state),
        asyncio.to_thread(storage.access_log_enabled),
        asyncio.to_thread(storage.access_retention_days),
    )
    return JSONResponse(
        {
            "items": page["items"],
            "page": {"has_more": page["has_more"], "offset": page["offset"], "limit": limit},
            "window": {"from_ts": start, "to_ts": end},
            "stats": counts,
            "hints": hints,
            "observation": {
                "enabled": enabled,
                "retention_days": retention,
                "row_cap": storage.ACCESS_MAX_DETAIL_ROWS,
                "sampled": bool(drop_state["sampled"] or counts.get("sampled")),
                "dropped_total": int(drop_state["dropped_total"]),
                "last_drop_ts": int(drop_state["last_drop_ts"]),
            },
            "settings": {"accessLogEnabled": enabled, "accessRetentionDays": retention},
        },
        headers={"Cache-Control": "no-store"},
    )


@router.get("/api/admin/access/records")
async def admin_access_records(
    request: Request,
    from_ts: int | None = Query(default=None, ge=0, le=SQLITE_INT_MAX),
    to_ts: int | None = Query(default=None, ge=0, le=SQLITE_INT_MAX),
    limit: int = Query(default=50, ge=1, le=ACCESS_LIST_MAX_PAGE),
    before_id: int | None = Query(default=None, ge=0, le=SQLITE_INT_MAX),
    source_ip: str = "",
    country: str = "",
    ban_state: str = "",
    identity: str = "",
    status: str = "",
    status_class: str = "",
    decision: str = "",
    kind: str = "",
    account: str = "",
    request_id: str = "",
    q: str = "",
    include_admin_poll: int = Query(default=0, ge=0, le=1),
):
    """Detail rows (keyset by id, newest first) with the same filters."""
    security.require_admin(request)
    start, end = _window(from_ts, to_ts)
    page = await asyncio.to_thread(
        storage.query_access_records,
        limit=limit, before_id=before_id, from_ts=start, to_ts=end,
        filters=_filters(
            source_ip=source_ip, country=country, ban_state=ban_state, identity=identity, status=status,
            status_class=status_class, decision=decision, kind=kind, account=account,
            request_id=request_id, q=q, include_admin_poll=include_admin_poll,
        ),
    )
    return JSONResponse(
        {
            "items": page["items"],
            "page": {"has_more": page["has_more"], "next_before_id": page["next_before_id"]},
            "window": page["window"],
        },
        headers={"Cache-Control": "no-store"},
    )


@router.post("/api/admin/access/export")
async def admin_access_export(request: Request):
    """Export the current filter view as CSV or JSON (capped rows and range)."""
    security.verify_csrf(request)
    admin = security.require_admin(request)
    payload = await parse_body(request)
    payload = payload if isinstance(payload, dict) else {}
    export_format = str(payload.get("format") or "csv").strip().lower()
    if export_format not in ACCESS_EXPORT_FORMATS:
        raise HTTPException(status_code=400, detail=i18n.translate("server.error.invalid_request"))
    start, end = _window(payload.get("from_ts"), payload.get("to_ts"))
    filters = _filters(**{
        key: payload.get(key, "")
        for key in ("source_ip", "country", "ban_state", "identity", "status", "status_class",
                    "decision", "kind", "account", "request_id", "q")
    }, include_admin_poll=payload.get("include_admin_poll"))

    def collect() -> list[dict]:
        collected: list[dict] = []
        cursor: int | None = None
        while len(collected) < ACCESS_EXPORT_MAX_ROWS:
            page = storage.query_access_records(
                limit=min(500, ACCESS_EXPORT_MAX_ROWS - len(collected)),
                before_id=cursor, from_ts=start, to_ts=end, filters=filters,
            )
            collected.extend(page["items"])
            cursor = page["next_before_id"]
            if not page["has_more"] or cursor is None:
                break
        return collected

    events = await asyncio.to_thread(collect)
    truncated = len(events) >= ACCESS_EXPORT_MAX_ROWS
    audit_request(
        request, admin, "access_export", target_type="access_records", target_id=export_format,
        metadata={"format": export_format, "exported_count": len(events), "from_ts": start, "to_ts": end},
    )
    headers = {
        "Cache-Control": "no-store",
        "Pragma": "no-cache",
        "Content-Disposition": f'attachment; filename="scrcpygate-access.{export_format}"',
    }
    if export_format == "json":
        return JSONResponse(
            {"records": list(reversed(events)), "exported_count": len(events), "truncated": truncated},
            headers=headers,
        )
    columns = (
        "ts", "source_ip", "ip_version", "country", "identity", "account", "method", "kind",
        "route_template", "path_sample", "status", "duration_ms", "decision", "request_id", "user_agent",
    )
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for event in reversed(events):
        writer.writerow({key: audit_csv_cell(event.get(key)) for key in columns})
    return Response(content="\ufeff" + stream.getvalue(), media_type="text/csv; charset=utf-8", headers=headers)


@router.get("/api/admin/access/status")
async def admin_access_status(request: Request):
    """Observation health: queue pressure, drops, retention, and the on/off switch."""
    security.require_admin(request)
    writer = getattr(runtime_for(request), "access_writer", None)
    stats = writer.stats() if writer is not None else {"running": False}
    drop_state, enabled, retention = await asyncio.gather(
        asyncio.to_thread(storage.access_drop_state),
        asyncio.to_thread(storage.access_log_enabled),
        asyncio.to_thread(storage.access_retention_days),
    )
    sampled = bool(drop_state["sampled"])
    return JSONResponse(
        {
            "enabled": enabled,
            "writer": stats,
            "retention_days": retention,
            "row_cap": storage.ACCESS_MAX_DETAIL_ROWS,
            "dropped": drop_state,
            "degraded": bool(sampled or stats.get("failures")),
        },
        headers={"Cache-Control": "no-store", "X-Access-Observation": "degraded" if sampled else "ok"},
    )


@router.get("/api/admin/access/hints")
async def admin_access_hints(
    request: Request,
    window_seconds: int = Query(default=600, ge=60, le=86_400),
    include_admin_poll: int = Query(default=0, ge=0, le=1),
):
    """Suspected-scan hints with the explicit thresholds and the scan bound."""
    security.require_admin(request)
    hints = await asyncio.to_thread(
        storage.access_scan_hints, window_seconds=window_seconds, include_admin_poll=bool(include_admin_poll)
    )
    return JSONResponse(hints, headers={"Cache-Control": "no-store"})


__all__ = [name for name in globals() if name.startswith("admin_")] + ["router"]
