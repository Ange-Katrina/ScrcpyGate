"""Scrcpy video, control and event WebSocket routes."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket

from .. import security, storage
from ..account_access import account_connections
from ..http_helpers import (
    diagnose_websocket_session,
    register_current_user_websocket,
    reject_unauthenticated_websocket,
)
from ..services.alas_exit_guard import note_connected, note_disconnected
from ..services.audit_service import audit_websocket_event
from ..mirror_manager import manager
from ..mirror_websocket import control_socket, video_socket

log = logging.getLogger("webscrcpy.main")
router = APIRouter()

@router.websocket("/ws/devices/{device_id}/video")
async def ws_video(websocket: WebSocket, device_id: str):
    if not security.websocket_origin_allowed(websocket):
        await audit_websocket_event(websocket, None, "websocket_access", outcome="denied", reason="origin_denied", target_id="/ws/devices/*/video")
        await websocket.close(code=4403)
        return
    session_state = await diagnose_websocket_session(websocket)
    user = await security.websocket_user(websocket)
    if not user:
        await reject_unauthenticated_websocket(websocket, "/ws/devices/*/video", state=session_state)
        return
    real_device_id = await asyncio.to_thread(storage.resolve_device_ref, device_id)
    if not real_device_id:
        await audit_websocket_event(websocket, user, "websocket_access", outcome="failure", reason="device_not_found", target_type="device", target_id=device_id)
        await websocket.close(code=4404)
        return
    # 到期账户仍可登录浏览，但投屏（含仅观看）必须拒绝；给明确的关码原因，
    # 前端据此「不再重连」而不是当成网络抖动反复重试。
    if not await asyncio.to_thread(storage.user_is_active, user):
        await audit_websocket_event(websocket, user, "websocket_access", outcome="denied", reason="account_expired", target_type="device", target_id=real_device_id)
        await websocket.close(code=4403, reason="account expired")
        return
    if not await asyncio.to_thread(storage.user_can, user["username"], real_device_id, "view"):
        await audit_websocket_event(websocket, user, "websocket_access", outcome="denied", reason="device_permission_denied", target_type="device", target_id=real_device_id)
        await websocket.close(code=4403)
        return
    username = user["username"]
    session_id = security.websocket_session_id(websocket)

    async def session_check() -> bool:
        return await asyncio.to_thread(security.websocket_session_valid, websocket, username, session_id)

    if not await register_current_user_websocket(
        websocket,
        user,
        session_id=session_id,
        device_id=real_device_id,
    ):
        return
    try:
        await video_socket(
            websocket,
            user,
            real_device_id,
            exposed_device_id=device_id,
            session_check=session_check,
        )
    finally:
        await account_connections.unregister(username, websocket, session_id)


@router.websocket("/ws/devices/{device_id}/control")
async def ws_control(websocket: WebSocket, device_id: str):
    if not security.websocket_origin_allowed(websocket):
        await audit_websocket_event(websocket, None, "websocket_access", outcome="denied", reason="origin_denied", target_id="/ws/devices/*/control")
        await websocket.close(code=4403)
        return
    session_state = await diagnose_websocket_session(websocket)
    user = await security.websocket_user(websocket)
    if not user:
        await reject_unauthenticated_websocket(websocket, "/ws/devices/*/control", state=session_state)
        return
    real_device_id = await asyncio.to_thread(storage.resolve_device_ref, device_id)
    if not real_device_id:
        await audit_websocket_event(websocket, user, "websocket_access", outcome="failure", reason="device_not_found", target_type="device", target_id=device_id)
        await websocket.close(code=4404)
        return
    # 与视频通道同一套语义：到期账户不能控制设备（即使它曾经有控制权限）。
    if not await asyncio.to_thread(storage.user_is_active, user):
        await audit_websocket_event(websocket, user, "websocket_access", outcome="denied", reason="account_expired", target_type="device", target_id=real_device_id)
        await websocket.close(code=4403, reason="account expired")
        return
    if not await asyncio.to_thread(storage.user_can, user["username"], real_device_id, "control"):
        await audit_websocket_event(websocket, user, "websocket_access", outcome="denied", reason="device_permission_denied", target_type="device", target_id=real_device_id)
        await websocket.close(code=4403)
        return
    username = user["username"]
    session_id = security.websocket_session_id(websocket)

    async def session_check() -> bool:
        return await asyncio.to_thread(security.websocket_session_valid, websocket, username, session_id)

    if not await register_current_user_websocket(
        websocket,
        user,
        session_id=session_id,
        device_id=real_device_id,
    ):
        return

    async def control_audit(action: str, **fields):
        await audit_websocket_event(
            websocket,
            user,
            action,
            target_type="device",
            target_id=real_device_id,
            **fields,
        )

    try:
        await control_socket(
            websocket,
            user,
            real_device_id,
            exposed_device_id=device_id,
            audit_callback=control_audit,
            session_check=session_check,
        )
    finally:
        await account_connections.unregister(username, websocket, session_id)


@router.websocket("/ws/events")
async def ws_events(websocket: WebSocket):
    if not security.websocket_origin_allowed(websocket):
        await audit_websocket_event(websocket, None, "websocket_access", outcome="denied", reason="origin_denied", target_id="/ws/events")
        await websocket.close(code=4403)
        return
    session_state = await diagnose_websocket_session(websocket)
    user = await security.websocket_user(websocket)
    if not user:
        await reject_unauthenticated_websocket(websocket, "/ws/events", state=session_state)
        return
    username = user["username"]
    session_id = security.websocket_session_id(websocket)

    async def session_check() -> bool:
        return await asyncio.to_thread(security.websocket_session_valid, websocket, username, session_id)

    if not await register_current_user_websocket(websocket, user, session_id=session_id):
        return
    note_connected(username)
    try:
        await websocket.accept()
        await manager.register_event_ws(websocket, username, session_check=session_check)
    finally:
        await account_connections.unregister(username, websocket, session_id)
        note_disconnected(username, has_other_connections=manager.event_connection_count(username) > 0)
