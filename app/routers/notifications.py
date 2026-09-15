"""Per-user notification inbox routes for the workbench bell."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request

from .. import i18n, security, storage
from ..http_helpers import parse_body
from ..storage_notifications import DEFAULT_NOTIFICATION_LIMIT, MAX_NOTIFICATION_LIMIT

router = APIRouter()


def _bounded_limit(raw: object) -> int:
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_NOTIFICATION_LIMIT
    return max(1, min(value, MAX_NOTIFICATION_LIMIT))


@router.get("/api/notifications")
async def api_notifications(request: Request):
    """当前用户的通知收件箱（含派生的账户到期提醒）。"""
    user = security.require_user(request)
    username = str(user["username"])

    def read_inbox() -> dict:
        storage.sync_account_notifications(username)
        return storage.list_user_notifications(username, limit=_bounded_limit(request.query_params.get("limit")))

    return await asyncio.to_thread(read_inbox)


@router.post("/api/notifications/read")
async def api_notifications_read(request: Request):
    """标记已读：``{"ids": [...]}`` 或 ``{"all": true}``。"""
    security.verify_csrf(request)
    user = security.require_user(request)
    payload = await parse_body(request)
    raw_ids = payload.get("ids")
    if raw_ids is not None and not isinstance(raw_ids, list):
        raise HTTPException(status_code=400, detail=i18n.translate("server.error.invalid_request"))
    mark_all = bool(payload.get("all")) or bool(payload.get("mark_all"))
    if not mark_all and not raw_ids:
        raise HTTPException(status_code=400, detail=i18n.translate("server.error.invalid_request"))
    result = await asyncio.to_thread(
        storage.mark_user_notifications_read,
        str(user["username"]),
        ids=raw_ids or [],
        mark_all=mark_all,
    )
    return {"ok": True, **result}


__all__ = ["router"]
