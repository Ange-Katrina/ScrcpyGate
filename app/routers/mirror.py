"""Mirror and control HTTP routes."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request, Response

from .. import device_metrics, i18n, security, storage
from ..adb_monitor import adb_monitor
from ..booleans import InvalidBooleanValue, parse_bool_strict
from ..mirror_manager import manager
from ..mirror_runtime import acquire_control_lock, release_control_lock
from ..http_helpers import parse_body, video_option_error_message
from ..services.audit_service import audit_request
from ..services.domain_helpers import fullscreen_video_options, user_video_options
from ..services.mirror_service import public_mirror_failure, public_sessions_for_user, resolve_device_or_404
from ..video_options import VideoOptionError, public_video_options

router = APIRouter()


@router.get("/api/devices/{device_id}/metrics")
async def api_device_metrics(device_id: str, request: Request, response: Response):
    security.require_admin(request)
    real_id = await asyncio.to_thread(resolve_device_or_404, device_id)
    device = await asyncio.to_thread(storage.get_device, real_id)
    if not device:
        raise HTTPException(status_code=404, detail=i18n.translate("server.error.device_not_found"))
    result = await asyncio.to_thread(device_metrics.snapshot, str(device["address"] or real_id))
    security.require_admin(request)
    response.headers["Cache-Control"] = "no-store"
    return result


def _optional_bool(payload: dict, key: str) -> bool:
    """Read an optional boolean field, rejecting non-boolean text.

    An absent field is ``False``; a present but ambiguous value is a 400 so a
    caller can never persist or act on ``"false"`` as if it were true.
    """
    if key not in payload:
        return False
    try:
        return parse_bool_strict(payload.get(key))
    except InvalidBooleanValue:
        raise HTTPException(
            status_code=400,
            detail=i18n.translate("server.error.invalid_setting_value"),
        ) from None


@router.post("/api/devices/{device_id}/mirror/start")
async def api_mirror_start(device_id: str, request: Request):
    security.verify_csrf(request)
    user = security.require_active_user(request)
    real_device_id = await asyncio.to_thread(resolve_device_or_404, device_id)
    if not await asyncio.to_thread(storage.user_can, user["username"], real_device_id, "view"):
        audit_request(
            request,
            user,
            "mirror_start",
            outcome="denied",
            reason="device_permission_denied",
            severity="warning",
            target_type="device",
            target_id=real_device_id,
        )
        raise HTTPException(status_code=403, detail=i18n.translate("server.error.device_denied"))
    payload = await parse_body(request)
    try:
        options = await asyncio.to_thread(user_video_options, user["username"], payload or None)
    except VideoOptionError as exc:
        raise HTTPException(status_code=400, detail=video_option_error_message(exc)) from exc
    if _optional_bool(payload, "save_preference"):
        await asyncio.to_thread(storage.set_user_video_preference, user["username"], options)
    viewer_token = await manager.reserve_viewer(real_device_id, user["username"])
    ok = False
    reservation_finalized = False
    try:
        ok = await manager.start(real_device_id, options, reservation_token=viewer_token)
    finally:
        if viewer_token:
            reservation_finalized = await asyncio.shield(
                manager.refresh_viewer_reservation(real_device_id, viewer_token, user["username"])
                if ok
                else manager.release_viewer_reservation(real_device_id, viewer_token, user["username"])
            )
    if not ok:
        sessions = await public_sessions_for_user(user)
        session = (await manager.snapshot()).get(real_device_id) or {}
        adb_status = session.get("adb") or adb_monitor.snapshot(real_device_id)
        failure = public_mirror_failure(
            real_device_id,
            session,
            adb_status,
            i18n.translate("server.status.mirror_start_failed"),
        )
        audit_request(
            request,
            user,
            "mirror_start",
            outcome="failure",
            reason="stream_start_failed",
            severity="error",
            target_type="device",
            target_id=real_device_id,
            metadata={
                "adb_state": adb_status.get("state", "unknown"),
                "stream_mode": session.get("stream_mode", "none"),
            },
        )
        return {
            "ok": False,
            "error": failure["error"],
            "adb_state": adb_status.get("state", "unknown"),
            "stream_mode": session.get("stream_mode", "none"),
            "detail": failure["detail"],
            "sessions": sessions,
        }
    if viewer_token and not reservation_finalized:
        viewer_token = ""
    audit_request(
        request,
        user,
        "mirror_start",
        target_type="device",
        target_id=real_device_id,
        metadata={"video": public_video_options(options)},
    )
    return {
        "ok": ok,
        "viewer_token": viewer_token,
        "sessions": await public_sessions_for_user(user),
        "stopped": 0,
    }


@router.put("/api/devices/{device_id}/mirror/settings")
async def api_mirror_settings(device_id: str, request: Request):
    security.verify_csrf(request)
    user = security.require_active_user(request)
    real_device_id = await asyncio.to_thread(resolve_device_or_404, device_id)
    if not await asyncio.to_thread(storage.user_can, user["username"], real_device_id, "view"):
        audit_request(
            request,
            user,
            "mirror_settings",
            outcome="denied",
            reason="device_permission_denied",
            severity="warning",
            target_type="device",
            target_id=real_device_id,
        )
        raise HTTPException(status_code=403, detail=i18n.translate("server.error.device_denied"))
    payload = await parse_body(request)
    # 全屏画质开关：true = 切到后台配置的「全屏预设」，false = 回到该用户自己的画质，
    # 两者都只在本次会话生效（不写回用户偏好），否则关掉浏览器时会把全屏画质留在账号里。
    fullscreen_switch = payload.get("fullscreen") if isinstance(payload, dict) else None
    if fullscreen_switch is not None:
        _optional_bool(payload, "fullscreen")
    fullscreen_profile = ""
    try:
        if fullscreen_switch is True:
            options, fullscreen_profile = await asyncio.to_thread(
                fullscreen_video_options, user["username"]
            )
        elif fullscreen_switch is False:
            options = await asyncio.to_thread(user_video_options, user["username"], None)
        else:
            options = await asyncio.to_thread(user_video_options, user["username"], payload)
    except VideoOptionError as exc:
        raise HTTPException(status_code=400, detail=video_option_error_message(exc)) from exc
    client_id = str(payload.get("client_id") or "").strip()
    if fullscreen_switch is None:
        await asyncio.to_thread(storage.set_user_video_preference, user["username"], options)
    apply_result = await manager.apply_video_options(real_device_id, options, client_id, user["username"])
    restart_required = bool(apply_result.get("restart_required"))
    deferred = bool(apply_result.get("deferred"))
    viewer_count = int(apply_result.get("viewer_count") or 0)
    if not apply_result.get("ok"):
        session = (await manager.snapshot()).get(real_device_id) or {}
        adb_status = session.get("adb") or adb_monitor.snapshot(real_device_id)
        failure = public_mirror_failure(
            real_device_id,
            session,
            adb_status,
            i18n.translate("server.status.mirror_restart_failed"),
        )
        audit_request(
            request,
            user,
            "mirror_settings",
            outcome="failure",
            reason="stream_restart_failed",
            severity="error",
            target_type="device",
            target_id=real_device_id,
            metadata={"restart_required": restart_required},
        )
        return {
            "ok": False,
            "restarted": False,
            "error": failure["error"],
            "adb_state": adb_status.get("state", "unknown"),
            "stream_mode": session.get("stream_mode", "none"),
            "detail": failure["detail"],
            "preferences": public_video_options(options),
            "sessions": await public_sessions_for_user(user),
        }
    audit_request(
        request,
        user,
        "mirror_settings",
        target_type="device",
        target_id=real_device_id,
        metadata={
            "restart_required": restart_required,
            "deferred": deferred,
            "video": public_video_options(options),
            # 全屏开关只影响本次会话，审计里区分开，便于解释「为什么画质变了」。
            "fullscreen": bool(fullscreen_switch is True and fullscreen_profile),
            "fullscreen_profile": fullscreen_profile,
        },
    )
    effective = apply_result.get("effective") or options
    return {
        "ok": True,
        "restarted": bool(apply_result.get("restarted")),
        "deferred": deferred,
        "viewer_count": viewer_count,
        "effective": public_video_options(effective),
        "preferences": public_video_options(options),
        # fullscreen=true 但 fullscreen_profile 为空 = 后台没有可用的全屏预设，
        # 前端据此提示「未配置全屏画质」而不是假装已经提升。
        "fullscreen": bool(fullscreen_switch is True),
        "fullscreen_profile": fullscreen_profile,
        "sessions": await public_sessions_for_user(user),
    }


@router.post("/api/devices/{device_id}/mirror/stop")
async def api_mirror_stop(device_id: str, request: Request):
    security.verify_csrf(request)
    user = security.require_admin(request)
    real_device_id = await asyncio.to_thread(resolve_device_or_404, device_id)
    if not await asyncio.to_thread(storage.user_can, user["username"], real_device_id, "view"):
        audit_request(
            request,
            user,
            "mirror_stop",
            outcome="denied",
            reason="device_permission_denied",
            severity="warning",
            target_type="device",
            target_id=real_device_id,
        )
        raise HTTPException(status_code=403, detail=i18n.translate("server.error.device_denied"))
    ok = await manager.stop(real_device_id)
    audit_request(
        request,
        user,
        "mirror_stop",
        outcome="success" if ok else "failure",
        reason="" if ok else "stop_failed",
        severity="info" if ok else "error",
        target_type="device",
        target_id=real_device_id,
    )
    return {"ok": ok, "sessions": await public_sessions_for_user(user)}


@router.post("/api/devices/{device_id}/mirror/stop-self")
async def api_mirror_stop_self(device_id: str, request: Request):
    """Stop only the authenticated user's viewer connection."""
    security.verify_csrf(request)
    user = security.require_active_user(request)
    real_device_id = await asyncio.to_thread(resolve_device_or_404, device_id)
    if not await asyncio.to_thread(storage.user_can, user["username"], real_device_id, "view"):
        raise HTTPException(status_code=403, detail=i18n.translate("server.error.device_denied"))
    payload = await parse_body(request)
    client_id = str(payload.get("client_id") or "").strip()
    viewer_token = str(payload.get("viewer_token") or "").strip()
    if not client_id and not viewer_token:
        raise HTTPException(status_code=400, detail=i18n.translate("server.error.session_not_found"))
    disconnected = await manager.disconnect_client_for_user(
        real_device_id,
        client_id,
        user["username"],
        viewer_token,
    )
    if disconnected is None:
        raise HTTPException(status_code=404, detail=i18n.translate("server.error.session_not_found"))
    audit_request(
        request,
        user,
        "mirror_viewer_stop",
        target_type="device_viewer",
        target_id=f"{real_device_id}:{disconnected.get('client_id') or 'reservation'}",
        metadata={"username": user["username"]},
    )
    return {"ok": True, "stopped": True, "viewer": disconnected, "sessions": await public_sessions_for_user(user)}


@router.post("/api/devices/{device_id}/mirror/idle-stop")
async def api_mirror_idle_stop(device_id: str, request: Request):
    security.verify_csrf(request)
    user = security.require_active_user(request)
    real_device_id = await asyncio.to_thread(resolve_device_or_404, device_id)
    if not await asyncio.to_thread(storage.user_can, user["username"], real_device_id, "view"):
        audit_request(
            request,
            user,
            "mirror_idle_stop",
            outcome="denied",
            reason="device_permission_denied",
            severity="warning",
            target_type="device",
            target_id=real_device_id,
        )
        raise HTTPException(status_code=403, detail=i18n.translate("server.error.device_denied"))
    payload = await parse_body(request)
    departing_client_id = str(payload.get("client_id") or "").strip()
    departing_viewer_token = str(payload.get("viewer_token") or "").strip()
    ok = await manager.stop_if_no_clients(
        real_device_id,
        wait_seconds=2,
        departing_client_id=departing_client_id,
        departing_username=user["username"],
        departing_viewer_token=departing_viewer_token,
    )
    audit_request(
        request,
        user,
        "mirror_idle_stop",
        target_type="device",
        target_id=real_device_id,
        metadata={"stopped": ok},
    )
    return {"ok": True, "stopped": ok, "sessions": await public_sessions_for_user(user)}


@router.post("/api/devices/{device_id}/control/acquire")
async def api_control_acquire(device_id: str, request: Request):
    security.verify_csrf(request)
    user = security.require_active_user(request)
    real_device_id = await asyncio.to_thread(resolve_device_or_404, device_id)
    if not await asyncio.to_thread(storage.user_can, user["username"], real_device_id, "control"):
        audit_request(
            request,
            user,
            "control_acquire",
            outcome="denied",
            reason="device_permission_denied",
            severity="warning",
            target_type="device",
            target_id=real_device_id,
            metadata={"channel": "http"},
        )
        raise HTTPException(status_code=403, detail=i18n.translate("server.error.device_denied"))
    payload = await parse_body(request)
    force = user["role"] == "admin" and _optional_bool(payload, "force")
    result, _epoch = await asyncio.to_thread(
        acquire_control_lock, real_device_id, user["username"], "http", force=force
    )
    audit_request(
        request,
        user,
        "control_acquire",
        outcome="success" if result.get("ok") else "denied",
        reason="" if result.get("ok") else "control_occupied",
        severity="info" if result.get("ok") else "warning",
        target_type="device",
        target_id=real_device_id,
        metadata={"channel": "http", "force": force},
    )
    await manager.broadcast(
        {
            "type": "control_lock",
            "device_id": real_device_id,
            "lock": await asyncio.to_thread(storage.get_lock, real_device_id),
        }
    )
    return result


@router.post("/api/devices/{device_id}/control/takeover")
async def api_control_takeover(device_id: str, request: Request):
    """Explicitly replace the current controller after a user confirmation."""
    security.verify_csrf(request)
    user = security.require_active_user(request)
    real_device_id = await asyncio.to_thread(resolve_device_or_404, device_id)
    if not await asyncio.to_thread(storage.user_can, user["username"], real_device_id, "control"):
        audit_request(
            request,
            user,
            "control_takeover",
            outcome="denied",
            reason="device_permission_denied",
            severity="warning",
            target_type="device",
            target_id=real_device_id,
            metadata={"channel": "http", "takeover": True},
        )
        raise HTTPException(status_code=403, detail=i18n.translate("server.error.device_denied"))
    result, _epoch = await asyncio.to_thread(
        acquire_control_lock, real_device_id, user["username"], "http", force=True
    )
    audit_request(
        request,
        user,
        "control_takeover",
        outcome="success" if result.get("ok") else "denied",
        reason="" if result.get("ok") else "control_occupied",
        severity="info" if result.get("ok") else "warning",
        target_type="device",
        target_id=real_device_id,
        metadata={"channel": "http", "takeover": True, "force": True},
    )
    await manager.broadcast(
        {
            "type": "control_lock",
            "device_id": real_device_id,
            "lock": await asyncio.to_thread(storage.get_lock, real_device_id),
        }
    )
    return result


@router.post("/api/devices/{device_id}/control/release")
async def api_control_release(device_id: str, request: Request):
    security.verify_csrf(request)
    user = security.require_active_user(request)
    real_device_id = await asyncio.to_thread(resolve_device_or_404, device_id)
    # 与其它控制入口一致：对设备没有任何权限的人不该够到这条路（也顺带不再用
    # 404/200 区分「设备是否存在」）。这里要求的是「可观看」而不是「可控制」：
    # 控制权刚被收回的人仍然应该能释放自己手上的锁，否则锁会空占 90 秒。
    if not await asyncio.to_thread(storage.user_can, user["username"], real_device_id, "view"):
        audit_request(
            request,
            user,
            "control_release",
            outcome="denied",
            reason="device_permission_denied",
            severity="warning",
            target_type="device",
            target_id=real_device_id,
            metadata={"channel": "http"},
        )
        raise HTTPException(status_code=403, detail=i18n.translate("server.error.device_denied"))
    ok = await asyncio.to_thread(
        release_control_lock,
        real_device_id,
        user["username"],
        force=user["role"] == "admin",
        client_id="http",
    )
    audit_request(
        request,
        user,
        "control_release",
        outcome="success" if ok else "denied",
        reason="" if ok else "not_lock_owner",
        severity="info" if ok else "warning",
        target_type="device",
        target_id=real_device_id,
        metadata={"channel": "http", "force": user["role"] == "admin"},
    )
    await manager.broadcast(
        {
            "type": "control_lock",
            "device_id": real_device_id,
            "lock": await asyncio.to_thread(storage.get_lock, real_device_id),
        }
    )
    return {"ok": ok}

__all__ = [
    "api_control_acquire",
    "api_control_release",
    "api_control_takeover",
    "api_mirror_idle_stop",
    "api_mirror_settings",
    "api_mirror_start",
    "api_mirror_stop",
    "api_mirror_stop_self",
    "router",
]
