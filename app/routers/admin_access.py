"""Administrator user, device, session, and permission routes."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request

from .. import alas, alas_gateway, i18n, security, storage
from ..account_access import (
    SESSION_REVOKED_CLOSE_CODE,
    SESSION_REVOKED_CLOSE_REASON,
    account_connections,
)
from ..adb_monitor import adb_monitor
from ..booleans import InvalidBooleanValue, parse_bool_strict
from ..devices import address_error, device_name_error, devices_payload
from ..http_helpers import parse_body, public_error_detail
from ..mirror_manager import manager
from ..mirror_runtime import acquire_control_lock
from ..services.alas_service import admin_alas_permissions_payload
from ..services.audit_service import audit_request
from ..services.mirror_service import resolve_device_or_404
from ..services.notification_service import (
    notify_account_updated,
    notify_device_unavailable,
    notify_permission_changed,
    notify_session_disconnected,
)

router = APIRouter()

_DEVICE_ADDRESS_ERROR_KEYS = {
    "address_required": "server.error.address_required",
    "address_needs_port": "server.error.address_needs_port",
    "address_invalid": "server.error.address_invalid",
    "address_port_invalid": "server.error.address_port_invalid",
    "device_name_invalid": "server.error.device_name_invalid",
}


def _strict_bool(payload: dict, key: str) -> bool:
    """Parse an admin-submitted boolean strictly; invalid values become 400.

    The lenient :func:`parse_bool` maps an unrecognised string to False, so a
    typo such as ``"enabled": "maybe"`` silently disabled an account or device.
    """
    try:
        return parse_bool_strict(payload.get(key))
    except InvalidBooleanValue as exc:
        raise HTTPException(
            status_code=400,
            detail=i18n.translate("server.error.invalid_boolean"),
        ) from exc


def _reject_invalid_device_fields(address: str, name: str) -> None:
    for token in (address_error(address), device_name_error(name)):
        if token:
            raise HTTPException(
                status_code=400,
                detail=i18n.translate(_DEVICE_ADDRESS_ERROR_KEYS.get(token, "server.error.invalid_device")),
            )


async def _notify_device_unavailable(device_name: str, usernames) -> None:
    """设备停用/删除后通知曾经能看到它的用户。"""
    for username in sorted({str(item or "").strip() for item in (usernames or set())} - {""}):
        await asyncio.to_thread(notify_device_unavailable, username, device_name=device_name)


@router.get("/api/admin/users")
async def admin_users(request: Request):
    security.require_admin(request)
    return {"users": await asyncio.to_thread(storage.list_users, include_watch_data=True)}


@router.put("/api/admin/users")
async def admin_upsert_user(request: Request):
    security.verify_csrf(request)
    admin = security.require_admin(request)
    payload = await parse_body(request)
    username = str(payload.get("username", "")).strip()
    password = payload.get("password")
    password = str(password) if password else None
    previous = await asyncio.to_thread(storage.get_user, username)
    role = str(payload["role"]) if "role" in payload else str(previous["role"]) if previous else "user"
    enabled = (
        _strict_bool(payload, "enabled")
        if "enabled" in payload
        else None
    )
    must_change_password = _strict_bool(payload, "must_change_password") if "must_change_password" in payload else None
    # 用户列表里的「显示 ALAS」：只影响界面显隐，不参与权限判定。
    alas_visible = _strict_bool(payload, "alas_visible") if "alas_visible" in payload else None
    try:
        if "expires_at" in payload:
            await asyncio.to_thread(
                storage.upsert_user,
                username,
                password,
                role,
                expires_at=payload.get("expires_at"),
                must_change_password=must_change_password,
                enabled=enabled if enabled is not None else storage.ENABLED_UNSET,
                alas_visible=alas_visible if alas_visible is not None else storage.ALAS_VISIBLE_UNSET,
            )
        else:
            await asyncio.to_thread(
                storage.upsert_user,
                username,
                password,
                role,
                must_change_password=must_change_password,
                enabled=enabled if enabled is not None else storage.ENABLED_UNSET,
                alas_visible=alas_visible if alas_visible is not None else storage.ALAS_VISIBLE_UNSET,
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=public_error_detail(exc)) from exc
    updated = await asyncio.to_thread(storage.get_user, username)
    gateway_contexts_revoked = alas_gateway.revoke_user_contexts(username)
    should_close_connections = (
        "expires_at" in payload
        or "enabled" in payload
        or bool(password)
        or bool(previous and updated and previous["role"] != updated["role"])
        or not storage.user_is_active(updated)
        or bool(previous and not storage.user_is_active(previous))
    )
    if should_close_connections:
        await account_connections.close_user_connections(username)
    # 被改的账户自己在通知里看到这次变更（管理员改自己时不提醒）。
    if updated and username != admin["username"]:
        changed_fields = []
        if previous is None:
            changed_fields.append("created")
        else:
            if bool(previous["enabled"]) != bool(updated["enabled"]):
                changed_fields.append("enabled")
            if str(previous["role"]) != str(updated["role"]):
                changed_fields.append("role")
            if previous["expires_at"] != updated["expires_at"]:
                changed_fields.append("expires_at")
            # get_user 返回 sqlite3.Row：只能用 keys()/下标，不能用 dict.get。
            previous_alas_visible = bool(previous["alas_visible"]) if "alas_visible" in previous.keys() else True
            updated_alas_visible = (
                bool(updated["alas_visible"]) if updated is not None and "alas_visible" in updated.keys() else True
            )
            if previous_alas_visible != updated_alas_visible:
                changed_fields.append("alas_visible")
        if password:
            changed_fields.append("password")
        if changed_fields:
            await asyncio.to_thread(
                notify_account_updated,
                username,
                actor=str(admin["username"]),
                changed=changed_fields,
            )
    audit_request(
        request,
        admin,
        "user_upsert",
        target_type="account",
        target_id=username,
        metadata={
            "created": previous is None,
            "password_changed": bool(password),
            "role_before": previous["role"] if previous else None,
            "role_after": updated["role"] if updated else role,
            "expires_at_before": previous["expires_at"] if previous else None,
            "expires_at_after": updated["expires_at"] if updated else None,
            "enabled_before": bool(previous["enabled"]) if previous and "enabled" in previous.keys() else True,
            "enabled_after": bool(updated["enabled"]) if updated and "enabled" in updated.keys() else True,
            "alas_visible_after": bool(updated["alas_visible"]) if updated and "alas_visible" in updated.keys() else True,
            "connections_revoked": should_close_connections,
            "gateway_contexts_revoked": gateway_contexts_revoked,
        },
    )
    return {"ok": True, "users": await asyncio.to_thread(storage.list_users)}


@router.delete("/api/admin/users/{username}")
async def admin_delete_user(username: str, request: Request):
    security.verify_csrf(request)
    admin = security.require_admin(request)
    if username == admin["username"]:
        raise HTTPException(status_code=403, detail=i18n.translate("server.error.cannot_delete_current_admin"))
    try:
        await asyncio.to_thread(storage.delete_user, username)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=public_error_detail(exc)) from exc
    alas_gateway.revoke_user_contexts(username)
    await account_connections.close_user_connections(username)
    audit_request(request, admin, "user_delete", target_type="account", target_id=username)
    return {"ok": True, "users": await asyncio.to_thread(storage.list_users)}


@router.get("/api/admin/devices")
async def admin_devices(request: Request):
    security.require_admin(request)
    sessions = await manager.snapshot()
    devices = await asyncio.to_thread(storage.list_all_devices)
    return {"devices": devices_payload(devices, sessions, adb_monitor.snapshot()), "sessions": sessions}


@router.get("/api/admin/sessions")
async def admin_sessions(request: Request):
    security.require_admin(request)
    rows = await manager.admin_client_snapshot()
    all_devices = await asyncio.to_thread(storage.list_all_devices)
    devices = {str(device["id"]): device for device in all_devices}
    items = []
    for row in rows:
        device_id = str(row.get("device_id") or "")
        device = devices.get(device_id) or {}
        public_id = storage.public_device_id(device_id)
        items.append(
            {
                **row,
                "id": row.get("client_id"),
                "device_id": public_id,
                "device_name": str(device.get("name") or device_id),
            }
        )
    return {"sessions": items, "items": items, "total": len(items)}


@router.get("/api/admin/login-sessions")
async def admin_login_sessions(request: Request):
    """登录会话清单（与上面的投屏观看端会话不同：这里是"谁登录了系统"）。"""
    security.require_admin(request)
    current = security.get_current_session(request) or {}
    sessions = await asyncio.to_thread(
        storage.list_login_sessions,
        None,
        current.get("sid"),
    )
    return {
        "sessions": sessions,
        "total": len(sessions),
        "max_per_user": storage.MAX_SESSIONS_PER_USER,
    }


@router.delete("/api/admin/login-sessions/{session_id}")
async def admin_revoke_login_session(session_id: str, request: Request):
    """踢出一条登录会话：删库中的哈希行 + 关掉该会话正在使用的 WebSocket。"""
    security.verify_csrf(request)
    admin = security.require_admin(request)
    username = await asyncio.to_thread(storage.revoke_session_by_hash, session_id)
    if username is None:
        raise HTTPException(status_code=404, detail=i18n.translate("server.error.session_not_found"))
    await account_connections.close_session_connections(
        username,
        session_id,
        code=SESSION_REVOKED_CLOSE_CODE,
        reason=SESSION_REVOKED_CLOSE_REASON,
    )
    audit_request(
        request,
        admin,
        "login_session_revoked",
        target_type="account",
        target_id=username,
        metadata={"session": session_id[:12]},
    )
    return {"ok": True, "username": username}


@router.post("/api/admin/devices/{device_id}/viewers/{client_id}/disconnect")
async def admin_disconnect_viewer(device_id: str, client_id: str, request: Request):
    security.verify_csrf(request)
    admin = security.require_admin(request)
    real_device_id = await asyncio.to_thread(resolve_device_or_404, device_id)
    disconnected = await manager.disconnect_client(real_device_id, client_id)
    if disconnected is None:
        raise HTTPException(status_code=404, detail=i18n.translate("server.error.session_not_found"))
    audit_request(
        request,
        admin,
        "mirror_viewer_disconnect",
        target_type="device_viewer",
        target_id=f"{real_device_id}:{client_id}",
        metadata={"username": disconnected.get("username")},
    )
    viewer = str(disconnected.get("username") or "").strip()
    if viewer and viewer != admin["username"]:
        device = await asyncio.to_thread(storage.get_device, real_device_id)
        await asyncio.to_thread(
            notify_session_disconnected,
            viewer,
            device_name=str((device["name"] if device else real_device_id) or real_device_id),
            actor=str(admin["username"]),
        )
    return {"ok": True, "disconnected": disconnected}


@router.post("/api/admin/devices/{device_id}/control/transfer")
async def admin_transfer_control(device_id: str, request: Request):
    """Move the device control lease to a currently connected viewer."""
    security.verify_csrf(request)
    admin = security.require_admin(request)
    real_device_id = await asyncio.to_thread(resolve_device_or_404, device_id)
    payload = await parse_body(request)
    target_client_id = str(payload.get("target_client_id") or payload.get("targetClientId") or "").strip()
    if not target_client_id:
        raise HTTPException(status_code=400, detail=i18n.translate("server.error.session_not_found"))
    clients = await manager.admin_client_snapshot()
    target = next(
        (
            row
            for row in clients
            if str(row.get("device_id") or "") == real_device_id
            and str(row.get("client_id") or "") == target_client_id
        ),
        None,
    )
    if target is None:
        raise HTTPException(status_code=404, detail=i18n.translate("server.error.session_not_found"))
    target_user = str(target.get("username") or "").strip()
    if not target_user or not await asyncio.to_thread(storage.user_can, target_user, real_device_id, "control"):
        raise HTTPException(status_code=403, detail=i18n.translate("server.error.device_denied"))
    result, _epoch = await asyncio.to_thread(
        acquire_control_lock, real_device_id, target_user, target_client_id, force=True
    )
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=i18n.translate("server.error.control_occupied"))
    await manager.broadcast(
        {
            "type": "control_lock",
            "device_id": real_device_id,
            "lock": await asyncio.to_thread(storage.get_lock, real_device_id),
        }
    )
    audit_request(
        request,
        admin,
        "control_transfer",
        target_type="device_viewer",
        target_id=f"{real_device_id}:{target_client_id}",
        metadata={"username": target_user},
    )
    return result


@router.put("/api/admin/devices")
async def admin_upsert_device(request: Request):
    security.verify_csrf(request)
    admin = security.require_admin(request)
    payload = await parse_body(request)
    device_id = str(payload.get("device_id", "")).strip()
    address = str(payload.get("address", "")).strip()
    name = str(payload.get("name", "")).strip()
    previous = await asyncio.to_thread(storage.get_device, device_id) if device_id else None
    if device_id and not previous:
        raise HTTPException(status_code=404, detail=i18n.translate("server.error.device_not_found"))
    # 部分更新：这里是「设备管理」的编辑/停用/启用共用的端点，只改 enabled 的请求
    # 不带 address，必须沿用库里的原值——否则会被 address_required 拒掉（停用/启用
    # 按钮直接失效），或者更糟：把已有地址写成空串。
    if previous is not None:
        address = address or str(previous["address"] or "")
        name = name or str(previous["name"] or "")
    _reject_invalid_device_fields(address, name)
    name = name or address
    if "enabled" in payload:
        enabled = _strict_bool(payload, "enabled")
    elif previous is not None:
        # 同理：不带 enabled 的更新不能把已停用的设备悄悄重新启用。
        enabled = bool(previous["enabled"])
    else:
        enabled = True
    disabling = bool(previous and bool(previous["enabled"]) and not enabled)
    notify_users = await manager.event_usernames_for_device(device_id) if disabling else None
    if previous:
        adb_monitor.invalidate_device(device_id)
        if not await asyncio.to_thread(storage.update_device, device_id, name, address, enabled):
            raise HTTPException(status_code=404, detail=i18n.translate("server.error.device_not_found"))
    else:
        device_id = await asyncio.to_thread(storage.create_device, name, address, enabled)
    if admin["role"] == "admin":
        await asyncio.to_thread(storage.set_permission, admin["username"], device_id, True, True)
    address_changed = bool(previous and str(previous["address"]) != address)
    audit_request(
        request,
        admin,
        "device_upsert",
        target_type="device",
        target_id=device_id,
        metadata={"created": previous is None, "enabled": enabled, "address_changed": address_changed},
    )
    if previous and not enabled and (bool(previous["enabled"]) or address_changed):
        alas_gateway.revoke_device_contexts(device_id)
        await manager.remove_device(device_id, reason="device_disabled", notify_users=notify_users)
        await _notify_device_unavailable(name or device_id, notify_users or set())
    elif address_changed:
        await manager.reconfigure_device(device_id, address)
    await adb_monitor.reconnect_device(device_id)
    sessions = await manager.snapshot()
    devices = await asyncio.to_thread(storage.list_all_devices)
    return {
        "ok": True,
        "device_id": device_id,
        "devices": devices_payload(devices, sessions, adb_monitor.snapshot()),
    }


@router.post("/api/admin/devices/{device_id}/adb/test")
async def admin_test_adb_device(device_id: str, request: Request):
    security.verify_csrf(request)
    admin = security.require_admin(request)
    device = await asyncio.to_thread(storage.get_device, device_id)
    if not device:
        raise HTTPException(status_code=404, detail=i18n.translate("server.error.device_not_found"))
    result = await adb_monitor.reconnect_device(device_id)
    audit_request(
        request,
        admin,
        "device_adb_test",
        outcome="success" if result.get("ok") else "failure",
        reason="" if result.get("ok") else "adb_unavailable",
        severity="info" if result.get("ok") else "warning",
        target_type="device",
        target_id=device_id,
        metadata={"state": result.get("state", "unknown")},
    )
    return result


@router.get("/api/admin/adb/status")
async def admin_adb_status(request: Request):
    security.require_admin(request)
    return {"devices": adb_monitor.snapshot()}


@router.post("/api/admin/devices/{device_id}/adb/reconnect")
async def admin_reconnect_adb_device(device_id: str, request: Request):
    security.verify_csrf(request)
    admin = security.require_admin(request)
    if not await asyncio.to_thread(storage.get_device, device_id):
        raise HTTPException(status_code=404, detail=i18n.translate("server.error.device_not_found"))
    result = await adb_monitor.reconnect_device(device_id)
    audit_request(
        request,
        admin,
        "device_adb_reconnect",
        outcome="success" if result.get("ok") else "failure",
        reason="" if result.get("ok") else "adb_unavailable",
        severity="info" if result.get("ok") else "warning",
        target_type="device",
        target_id=device_id,
        metadata={"state": result.get("state", "unknown")},
    )
    return result


@router.delete("/api/admin/devices/{device_id}")
async def admin_delete_device(device_id: str, request: Request):
    security.verify_csrf(request)
    admin = security.require_admin(request)
    device_row = await asyncio.to_thread(storage.get_device, device_id)
    if not device_row:
        raise HTTPException(status_code=404, detail=i18n.translate("server.error.device_not_found"))
    notify_users = await manager.event_usernames_for_device(device_id)
    adb_monitor.forget_device(device_id)
    try:
        if not await asyncio.to_thread(storage.delete_device, device_id):
            raise HTTPException(status_code=404, detail=i18n.translate("server.error.device_not_found"))
    except Exception:
        adb_monitor.restore_device(device_id)
        raise
    alas_gateway.revoke_device_contexts(device_id)
    await manager.remove_device(device_id, notify_users=notify_users)
    audit_request(request, admin, "device_delete", target_type="device", target_id=device_id)
    await _notify_device_unavailable(str(device_row["name"] or device_id), notify_users or set())
    sessions = await manager.snapshot()
    devices = await asyncio.to_thread(storage.list_all_devices)
    return {"ok": True, "devices": devices_payload(devices, sessions, adb_monitor.snapshot())}


@router.get("/api/admin/permissions")
async def admin_permissions(request: Request):
    security.require_admin(request)
    sessions = await manager.snapshot()
    raw_permissions = await asyncio.to_thread(storage.list_permissions)
    users = await asyncio.to_thread(storage.list_users)
    all_devices = await asyncio.to_thread(storage.list_all_devices)
    permission_map = {(str(row["username"]), str(row["device_id"])): row for row in raw_permissions}
    permissions = []
    for user in users:
        if user.get("role") == "admin":
            continue
        # 停用/已到期的账号写了权限也不会生效（user_can 先看账户状态），
        # 读模型必须把这一点标出来，否则管理员会以为已经授权成功。
        user_active = storage.user_is_active(user)
        for device in all_devices:
            internal_id = str(device["id"])
            row = permission_map.get((str(user["username"]), internal_id))
            can_view = bool(row and row.get("can_view"))
            can_control = bool(row and row.get("can_control"))
            permissions.append(
                {
                    "username": user["username"],
                    "device_id": internal_id,
                    "public_device_id": storage.public_device_id(internal_id),
                    "can_view": can_view,
                    "can_control": can_control,
                    "assigned": bool(row),
                    "user_active": user_active,
                    "device_enabled": bool(device.get("enabled")),
                    "effective": bool(can_view and user_active and device.get("enabled")),
                }
            )
    return {
        "permissions": permissions,
        "users": users,
        "devices": devices_payload(all_devices, sessions, adb_monitor.snapshot()),
    }


@router.put("/api/admin/permissions")
async def admin_set_permission(request: Request):
    security.verify_csrf(request)
    admin = security.require_admin(request)
    payload = await parse_body(request)
    username = str(payload.get("username", "")).strip()
    device_id = await asyncio.to_thread(resolve_device_or_404, str(payload.get("device_id", "")).strip())
    enabled = _strict_bool(payload, "enabled") if "enabled" in payload else True
    can_view = _strict_bool(payload, "can_view") if "can_view" in payload else True
    can_control = _strict_bool(payload, "can_control") if "can_control" in payload else False
    if not enabled:
        can_view = False
        can_control = False
    # 撤销控制权限同样要断开该设备上的既有连接：镜像控制只在握手时校验
    # can_control，锁层（storage.acquire_lock/renew_lock）只校验账户状态，
    # 不断开的话被撤销的用户还能继续发输入、继续续租控制锁。
    permissions_before = await asyncio.to_thread(storage.list_permissions)
    previous = next(
        (
            row
            for row in permissions_before
            if str(row.get("username") or "") == username and str(row.get("device_id") or "") == device_id
        ),
        None,
    )
    had_control = bool(previous and previous.get("can_control"))
    try:
        await asyncio.to_thread(storage.set_permission, username, device_id, can_view, can_control)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=public_error_detail(exc)) from exc
    gateway_contexts_revoked = alas_gateway.revoke_device_contexts(device_id, username)
    connections_revoked = 0
    target_user = await asyncio.to_thread(storage.get_user, username)
    permission_revoked = (not can_view) or (had_control and not can_control)
    if permission_revoked and target_user and target_user["role"] != "admin":
        connections_revoked = await account_connections.close_device_connections(username, device_id)
    if target_user and target_user["role"] != "admin":
        device = await asyncio.to_thread(storage.get_device, device_id)
        await asyncio.to_thread(
            notify_permission_changed,
            username,
            scope="device",
            actor=str(admin["username"]),
            detail=str((device["name"] if device else device_id) or device_id),
            revoked=permission_revoked,
        )
    audit_request(
        request,
        admin,
        "permission_set",
        target_type="device_permission",
        target_id=f"{username}:{device_id}",
        metadata={
            "username": username,
            "can_view": can_view,
            "can_control": can_control,
            "control_revoked": bool(had_control and not can_control),
            "gateway_contexts_revoked": gateway_contexts_revoked,
            "connections_revoked": connections_revoked,
        },
    )
    return {"ok": True, "permissions": await asyncio.to_thread(storage.list_permissions)}


@router.put("/api/admin/users/{username}/permissions")
async def admin_replace_user_permissions(username: str, request: Request):
    """Atomically replace one user's complete device and ALAS permission set."""
    security.verify_csrf(request)
    admin = security.require_admin(request)
    payload = await parse_body(request)
    target = str(username or payload.get("username") or "").strip()
    previous_rows = await asyncio.to_thread(storage.list_permissions)
    previous_view_devices = {
        str(row["device_id"])
        for row in previous_rows
        if str(row.get("username") or "") == target and bool(row.get("can_view"))
    }
    previous_control_devices = {
        str(row["device_id"])
        for row in previous_rows
        if str(row.get("username") or "") == target and bool(row.get("can_control"))
    }
    device_permissions = payload.get("device_permissions", payload.get("devicePermissions", []))
    # 只有显式带上 ALAS 字段才替换绑定：设备权限的调用方（或缺少该字段的旧客户端）
    # 不应该顺手把用户的 ALAS 归属清空。显式传 [] 依旧表示「清空全部绑定」。
    alas_submitted = "alas_assignments" in payload or "alasAssignments" in payload
    alas_assignments = payload.get("alas_assignments", payload.get("alasAssignments"))
    if not isinstance(device_permissions, list) or (alas_submitted and not isinstance(alas_assignments, list)):
        raise HTTPException(status_code=400, detail="permissions must be arrays")

    async def resolve_rows(rows, device_keys):
        normalized = []
        for item in rows:
            if not isinstance(item, dict):
                normalized.append(item)
                continue
            copy = dict(item)
            for key in device_keys:
                if key in copy and copy[key]:
                    resolved = await asyncio.to_thread(storage.resolve_device_ref, str(copy[key]))
                    if not resolved:
                        raise ValueError("invalid_device")
                    copy["device_id"] = resolved
                    break
            normalized.append(copy)
        return normalized

    try:
        normalized_devices = await resolve_rows(device_permissions, ("device_id", "deviceId"))
        normalized_alas = None
        if alas_submitted:
            normalized_alas = await resolve_rows(alas_assignments, ("device_id", "deviceId"))
            # 与单条绑定接口同一套规范化：否则可以写入 "Alas.json"/".."/"a/b" 这类名字，
            # 之后删除通路 sanitize 后会「报成功却没删」或删掉另一条同名配置。
            for row in normalized_alas:
                if isinstance(row, dict) and row.get("config_name"):
                    try:
                        row["config_name"] = alas.sanitize_config_name(row["config_name"])
                    except ValueError as exc:
                        raise HTTPException(
                            status_code=400,
                            detail=i18n.translate("server.error.invalid_alas_config_name"),
                        ) from exc
        result = await asyncio.to_thread(storage.replace_user_permissions, target, normalized_devices, normalized_alas)
    except storage.AlasConfigOwnershipError as exc:
        raise HTTPException(
            status_code=409,
            detail=i18n.translate("server.error.alas_config_owned", config=exc.config_name, owner=exc.owner),
        ) from exc
    except ValueError as exc:
        status = 409 if str(exc) == "device_view_permission_required" else 400
        raise HTTPException(status_code=status, detail=public_error_detail(exc)) from exc
    current_rows = await asyncio.to_thread(storage.list_permissions)
    current_view_devices = {
        str(row["device_id"])
        for row in current_rows
        if str(row.get("username") or "") == target and bool(row.get("can_view"))
    }
    current_control_devices = {
        str(row["device_id"])
        for row in current_rows
        if str(row.get("username") or "") == target and bool(row.get("can_control"))
    }
    # 查看或控制任一被撤销都要断开该设备上的既有连接（控制权限只在握手时校验）。
    revoked_devices = (previous_view_devices - current_view_devices) | (
        previous_control_devices - current_control_devices
    )
    connections_revoked = 0
    for device_id in revoked_devices:
        connections_revoked += await account_connections.close_device_connections(target, device_id)
    target_user = await asyncio.to_thread(storage.get_user, target)
    if target_user and target_user["role"] != "admin" and target != admin["username"]:
        await asyncio.to_thread(
            notify_permission_changed,
            target,
            scope="device",
            actor=str(admin["username"]),
            detail=f"{len(current_view_devices)} 台设备",
            revoked=bool(revoked_devices),
        )
    gateway_contexts_revoked = alas_gateway.revoke_user_contexts(target)
    audit_request(
        request,
        admin,
        "permission_matrix_replace",
        target_type="account_permissions",
        target_id=target,
        metadata={
            "device_permissions": result["device_permissions"],
            "alas_assignments": result["alas_assignments"],
            "gateway_contexts_revoked": gateway_contexts_revoked,
            "connections_revoked": connections_revoked,
        },
    )
    permissions = await asyncio.to_thread(storage.list_permissions)
    alas_permissions = await asyncio.to_thread(admin_alas_permissions_payload)
    return {"ok": True, **result, "permissions": permissions, "alas": alas_permissions}


__all__ = [name for name in globals() if name.startswith("admin_")] + ["router"]
