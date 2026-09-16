"""多观看端「投屏记录」的 HTTP 接口。

分工：
  - start / stop：管理员，要求 CSRF 并记录操作审计；
  - status：管理员只读查询；
  - respond（是否参与）/ upload（上传自己那份完整记录）：任意有该设备观看权限的登录用户
    （被邀请者通常是普通用户），同样要求 CSRF。
服务端不落盘，只在内存里中继；/upload 收到的记录会被**原样**转发给发起端浏览器。
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request

from .. import i18n, security, storage
from ..http_helpers import parse_body
from ..mirror_record import record_registry
from ..services.audit_service import audit_request
from ..services.mirror_service import resolve_device_or_404

router = APIRouter()

_ERROR_STATUS = {
    "record_invalid_payload": 400,
    "record_client_required": 400,
    "record_no_viewers": 409,
    "record_session_exists": 409,
    "record_already_stopped": 409,
    "record_already_uploaded": 409,
    "record_window_closed": 409,
    "record_session_missing": 404,
    "record_not_invited": 403,
    "record_not_accepted": 403,
    "record_not_initiator": 403,
    "record_payload_too_large": 413,
    "record_too_many_entries": 413,
}


def _error_message(code: str) -> str:
    translated = i18n.translate(f"server.error.{code}")
    return translated if translated and translated != f"server.error.{code}" else code


def _raise_for_error(code: str) -> None:
    raise HTTPException(status_code=_ERROR_STATUS.get(code, 400), detail=_error_message(code))


async def _viewer_or_403(request: Request, device_id: str) -> tuple[dict, str]:
    user = security.require_active_user(request)
    real_device_id = await asyncio.to_thread(resolve_device_or_404, device_id)
    allowed = await asyncio.to_thread(storage.user_can, user["username"], real_device_id, "view")
    if not allowed:
        audit_request(
            request,
            user,
            "mirror_record",
            outcome="denied",
            reason="device_permission_denied",
            severity="warning",
            target_type="device",
            target_id=real_device_id,
        )
        raise HTTPException(status_code=403, detail=i18n.translate("server.error.device_denied"))
    return user, real_device_id


@router.post("/api/devices/{device_id}/mirror/record/start")
async def api_mirror_record_start(device_id: str, request: Request):
    security.verify_csrf(request)
    admin = security.require_admin(request)
    real_device_id = await asyncio.to_thread(resolve_device_or_404, device_id)
    payload = await parse_body(request)
    client_id = str((payload or {}).get("client_id") or "").strip()
    result = await asyncio.to_thread(
        record_registry.start, real_device_id, client_id, str(admin.get("username") or "")
    )
    audit_request(
        request,
        admin,
        "mirror_record_start",
        outcome="success" if result.get("ok") else "denied",
        reason=str(result.get("error") or ""),
        severity="info" if result.get("ok") else "warning",
        target_type="device",
        target_id=real_device_id,
        metadata={"invited": result.get("invited", 0), "delivered": result.get("delivered", 0)},
    )
    if not result.get("ok"):
        _raise_for_error(str(result.get("error") or "record_invalid_payload"))
    return result


@router.post("/api/devices/{device_id}/mirror/record/stop")
async def api_mirror_record_stop(device_id: str, request: Request):
    security.verify_csrf(request)
    admin = security.require_admin(request)
    real_device_id = await asyncio.to_thread(resolve_device_or_404, device_id)
    payload = await parse_body(request)
    session_id = str((payload or {}).get("session") or "").strip()
    result = await asyncio.to_thread(
        record_registry.stop, session_id, str(admin.get("username") or "")
    )
    audit_request(
        request,
        admin,
        "mirror_record_stop",
        outcome="success" if result.get("ok") else "denied",
        reason=str(result.get("error") or ""),
        severity="info" if result.get("ok") else "warning",
        target_type="device",
        target_id=real_device_id,
        metadata={"upload_requests": result.get("upload_requests", 0)},
    )
    if not result.get("ok"):
        _raise_for_error(str(result.get("error") or "record_session_missing"))
    return result


@router.get("/api/devices/{device_id}/mirror/record/status")
async def api_mirror_record_status(device_id: str, request: Request):
    security.require_admin(request)
    real_device_id = await asyncio.to_thread(resolve_device_or_404, device_id)
    return await asyncio.to_thread(record_registry.status, real_device_id)


@router.post("/api/devices/{device_id}/mirror/record/attach")
async def api_mirror_record_attach(device_id: str, request: Request):
    """观看端接入视频通道后对齐记录会话（补邀请 / 补上传请求 / 发起端重连拿回主导权）。

    服务端据此把「邀请」从「start 那一刻在线」扩展到「整段记录期间任何新接入的观看端」，
    解决同一账号的其它设备（手机端）晚一步进入工作台时收不到邀请的问题。
    """
    security.verify_csrf(request)
    user, real_device_id = await _viewer_or_403(request, device_id)
    payload = await parse_body(request)
    body = payload or {}
    result = await asyncio.to_thread(
        record_registry.attach_client,
        real_device_id,
        str(body.get("client_id") or "").strip(),
        str(user.get("username") or ""),
        str(body.get("session") or "").strip(),
        str(body.get("role") or "").strip(),
    )
    if not result.get("ok"):
        _raise_for_error(str(result.get("error") or "record_client_required"))
    return result


@router.post("/api/devices/{device_id}/mirror/record/respond")
async def api_mirror_record_respond(device_id: str, request: Request):
    """被邀请的观看端表示是否参与（参与者通常是普通用户）。"""
    security.verify_csrf(request)
    user, real_device_id = await _viewer_or_403(request, device_id)
    payload = await parse_body(request)
    body = payload or {}
    session_id = str(body.get("session") or "").strip()
    client_id = str(body.get("client_id") or "").strip()
    accept = bool(body.get("accept"))
    result = await asyncio.to_thread(
        record_registry.respond, session_id, client_id, str(user.get("username") or ""), accept
    )
    audit_request(
        request,
        user,
        "mirror_record_respond",
        outcome="success" if result.get("ok") else "denied",
        reason=str(result.get("error") or ""),
        severity="info" if result.get("ok") else "warning",
        target_type="device",
        target_id=real_device_id,
        metadata={"accept": accept},
    )
    if not result.get("ok"):
        _raise_for_error(str(result.get("error") or "record_session_missing"))
    return result


@router.post("/api/devices/{device_id}/mirror/record/upload")
async def api_mirror_record_upload(device_id: str, request: Request):
    """参与者上传自己那份**完整**记录；服务端原样转发给发起端。"""
    security.verify_csrf(request)
    user, real_device_id = await _viewer_or_403(request, device_id)
    payload = await parse_body(request)
    body = payload or {}
    session_id = str(body.get("session") or "").strip()
    client_id = str(body.get("client_id") or "").strip()
    result = await asyncio.to_thread(
        record_registry.upload,
        session_id,
        client_id,
        str(user.get("username") or ""),
        body.get("timeline"),
    )
    audit_request(
        request,
        user,
        "mirror_record_upload",
        outcome="success" if result.get("ok") else "denied",
        reason=str(result.get("error") or ""),
        severity="info" if result.get("ok") else "warning",
        target_type="device",
        target_id=real_device_id,
        metadata={
            "entries": result.get("entries", 0),
            "bytes": result.get("bytes", 0),
            "delivered": bool(result.get("delivered")),
        },
    )
    if not result.get("ok"):
        _raise_for_error(str(result.get("error") or "record_invalid_payload"))
    return result
