"""Administrator OnePush configuration and delivery diagnostics."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request

from .. import security
from ..http_helpers import parse_body
from ..services import alas_push, push_service
from ..services.audit_service import audit_request

router = APIRouter()


@router.get("/api/admin/push/deliveries")
async def read_deliveries(request: Request):
    security.require_admin(request)
    return {"items": await asyncio.to_thread(push_service.recent_deliveries)}


@router.get("/api/admin/push/alas-template")
async def read_alas_template(request: Request):
    security.require_admin(request)
    return await asyncio.to_thread(alas_push.template_settings)


@router.put("/api/admin/push/alas-template")
async def write_alas_template(request: Request):
    security.verify_csrf(request)
    admin = security.require_admin(request)
    payload = await parse_body(request)
    try:
        result = await asyncio.to_thread(
            alas_push.save_template, str(payload.get("config_name") or ""),
            str(payload.get("recipient_field") or ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit_request(request, admin, "alas_push_template_update", target_type="settings", target_id="alas_onepush")
    return result


@router.get("/api/admin/push")
async def read_push(request: Request):
    security.require_admin(request)
    try:
        return await asyncio.to_thread(push_service.public_config)
    except (ValueError, OSError):
        raise HTTPException(status_code=503, detail="Notification configuration is unavailable") from None


@router.put("/api/admin/push")
async def write_push(request: Request):
    security.verify_csrf(request)
    admin = security.require_admin(request)
    payload = await parse_body(request)
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid configuration")
    try:
        result = await asyncio.to_thread(push_service.save_config, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit_request(request, admin, "push_config_update", target_type="settings", target_id="onepush")
    return result


@router.post("/api/admin/push/test")
async def test_push(request: Request):
    security.verify_csrf(request)
    admin = security.require_admin(request)
    payload = await parse_body(request)
    qq = str(payload.get("qq") or "").strip()
    if not qq.isascii() or not qq.isdecimal() or not 5 <= len(qq) <= 15:
        raise HTTPException(status_code=400, detail="Invalid QQ number")
    try:
        config = await asyncio.to_thread(push_service.load_config)
        if not config:
            raise HTTPException(status_code=409, detail="Configure OnePush first")
        await asyncio.to_thread(push_service._send, config, {
            "event": "test", "username": str(admin["username"]), "qq": qq,
            "device": "", "title": "ScrcpyGate", "message": "Test notification",
            "expires_at": "", "days": "",
        })
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Notification failed: {type(exc).__name__}") from None
    audit_request(request, admin, "push_test", target_type="settings", target_id="onepush")
    return {"ok": True}
