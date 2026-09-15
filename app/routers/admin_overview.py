"""Administrator overview and dashboard routes."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time

from fastapi import APIRouter, Request

from .. import security, storage
from ..adb_monitor import adb_monitor
from ..devices import devices_payload
from ..http_helpers import parse_bool, public_error_detail, user_payload
from ..logging_config import logging_health
from ..mirror_manager import manager
from ..services.alas_service import admin_alas_overview, admin_overview_storage_payload

log = logging.getLogger("webscrcpy.main")
router = APIRouter()

_DASHBOARD_ACTION_LABELS = {
    "login": "登录",
    "login_success": "登录成功",
    "login_failed": "登录失败",
    "login_rate_limited": "登录请求被限流",
    "logout": "退出登录",
    "user_created": "创建用户",
    "user_updated": "更新用户",
    "user_deleted": "删除用户",
    "user_upsert": "保存用户",
    "user_create": "创建用户",
    "user_update": "更新用户",
    "user_delete": "删除用户",
    "password_changed": "修改密码",
    "password_change": "修改密码",
    "permission_set": "更新设备权限",
    "permission_granted": "授予设备权限",
    "permission_revoked": "撤销设备权限",
    "permission_updated": "更新设备权限",
    "permission_matrix_replace": "保存用户权限矩阵",
    "device_upsert": "保存设备配置",
    "device_created": "创建设备",
    "device_updated": "更新设备",
    "device_deleted": "删除设备",
    "device_delete": "删除设备",
    "device_adb_test": "检测 ADB 连接",
    "adb_test": "检测 ADB 连接",
    "device_adb_reconnect": "重连 ADB 设备",
    "adb_reconnect": "重连 ADB 设备",
    "mirror_start": "开始投屏",
    "mirror_stop": "停止投屏",
    "mirror_auto_stop": "自动停止投屏",
    "mirror_idle_stop": "空闲自动停止投屏",
    "mirror_viewer_stop": "停止观看端",
    "mirror_viewer_disconnect": "断开观看端",
    "mirror_settings": "更新投屏画质",
    "control_acquire": "获取控制权",
    "control_release": "释放控制权",
    "control_takeover": "接管控制权",
    "control_transfer": "转交控制权",
    "video_preferences": "更新画质偏好",
    "video_preference_save": "保存画质偏好",
    "video_settings": "保存画质与传输设置",
    "video_restart": "重启视频传输",
    "video_preset_create": "创建画质预设",
    "video_preset_update": "更新画质预设",
    "video_preset_delete": "删除画质预设",
    "video_preset_reset": "重置画质预设",
    "alas_binding_set": "绑定 ALAS 配置",
    "alas_binding_delete": "解除 ALAS 绑定",
    "alas_toggle": "切换 ALAS 运行状态",
    "alas_admin_toggle": "切换 ALAS 服务",
    "alas_connection_check": "检测 ALAS 连接",
    "alas_config_save": "保存 ALAS 配置",
    "alas_settings": "ALAS 设置",
    "alas_access": "访问 ALAS",
    "alas_embed_open": "打开 ALAS 页面",
    "alas_embed_close": "关闭 ALAS 页面",
    "alas_embed_error": "ALAS 页面异常",
    "alas_embed_denied": "拒绝访问 ALAS 页面",
    "alas_embed_proxy_failed": "ALAS 页面代理失败",
    "alas_embed_ws_denied": "拒绝 ALAS WebSocket",
    "alas_embed_ws_failed": "ALAS WebSocket 失败",
    "audit_export": "导出审计日志",
    "audit_integrity_check": "检查日志完整性",
    "alert_resolve": "处理告警",
    "admin_access": "访问管理后台",
    "authentication": "认证访问",
    "http_boundary": "HTTP 边界校验",
    "ui_settings_update": "更新界面设置",
    "ui_settings_import": "导入界面设置",
    "ui_settings_reset": "重置界面设置",
    "ui_settings_maintenance": "维护界面设置",
    "http_operation": "HTTP 请求失败",
    "http_request": "HTTP 请求",
    "websocket_access": "WebSocket 访问",
    "account_expired": "账户到期访问",
    "page_index": "进入投屏工作台",
    "page_admin": "进入管理后台",
    "mirror_switch_cleanup": "清理切换投屏",
    "csrf_validation": "安全校验",
    # Runtime lifecycle events are emitted by Mirror/ADB workers and can
    # appear in the recent-activity feed even though they are not alerts.
    "client_join": "观看端加入",
    "disconnect": "连接断开",
    "error": "发生错误",
    "player_reset": "重置播放器",
    "control_player_reset": "重置控制播放器",
    "process_restart": "重启服务进程",
    "quality_changed": "画质已切换",
    "stream_reset": "重置视频流",
    "video_client_drop": "观看端丢帧",
    "video_client_terminate": "终止观看端",
    "connect": "建立连接",
    "authorization": "授权校验",
    "downstream": "下游消息",
    "message": "消息处理",
    "exception": "发生异常",
}

_DASHBOARD_ACTION_ALIASES = {
    "user_save": "user_upsert",
    "account_upsert": "user_upsert",
    "account_create": "user_created",
    "account_update": "user_updated",
    "account_delete": "user_deleted",
    "permission_grant": "permission_granted",
    "permission_revoke": "permission_revoked",
    "permission_update": "permission_updated",
    "mirror_autostop": "mirror_auto_stop",
    "mirror_idle_autostop": "mirror_idle_stop",
    "viewer_stop": "mirror_viewer_stop",
    "viewer_disconnect": "mirror_viewer_disconnect",
    "adb_test": "device_adb_test",
    "adb_reconnect": "device_adb_reconnect",
    "control_take_over": "control_takeover",
    "video_preference": "video_preferences",
    "video_preset_save": "video_preset_update",
    "alas_embed": "alas_embed_open",
    "alas_proxy_failed": "alas_embed_proxy_failed",
    "alas_ws_denied": "alas_embed_ws_denied",
    "settings_update": "ui_settings_update",
    "settings_import": "ui_settings_import",
    "settings_reset": "ui_settings_reset",
    "settings_maintenance": "ui_settings_maintenance",
}


def _dashboard_action_label(value: object) -> str:
    """Return a stable human label for current and legacy audit action codes."""
    original = str(value or "").strip()
    raw = original.lower()
    raw = re.sub(r"(?:成功|通过|失败|错误|拒绝|阻止|超时)$", "", raw)
    raw = re.sub(r"\s+(?:successfully|success|failed|failure|error|denied|blocked|timed\s+out)$", "", raw)
    key = re.sub(r"[\s./:-]+", "_", raw).strip("_")
    key = re.sub(r"_+", "_", key)
    key = _DASHBOARD_ACTION_ALIASES.get(key, key)
    mapped = _DASHBOARD_ACTION_LABELS.get(key)
    if mapped:
        return mapped
    if not original:
        return "审计事件"
    # Do not expose opaque producer codes in the dashboard. Human-authored
    # labels remain intact so older integrations can still provide useful text.
    if re.fullmatch(r"[a-z0-9]+(?:[_.:-][a-z0-9]+)*(?:成功|失败|错误|拒绝|阻止|超时)?", original, re.IGNORECASE):
        return "管理员操作"
    # Human-authored legacy labels may carry the same result suffix as the
    # machine action code. Keep the useful words while avoiding a second
    # success/failure badge in the dashboard row.
    return re.sub(r"(?:成功|通过|失败|错误|拒绝|阻止|超时)$", "", original).strip() or original


def _dashboard_activity_projection(event: dict, device_by_ref: dict[str, dict]) -> dict:
    """Project one audit row into the compact, human-readable dashboard feed."""
    row = dict(event or {})
    metadata = row.get("metadata")
    if not isinstance(metadata, dict):
        metadata = row.get("metadata_json")
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (TypeError, ValueError):
                metadata = {}
    metadata = metadata if isinstance(metadata, dict) else {}
    action = str(
        row.get("action")
        or row.get("action_code")
        or row.get("event")
        or row.get("event_name")
        or "audit"
    )
    actor = str(
        row.get("username")
        or row.get("user_name")
        or row.get("operator")
        or row.get("actor_name")
        or row.get("actorName")
        or ""
    )
    target_type = str(row.get("target_type") or row.get("targetType") or "")
    target_id = str(
        row.get("target_id")
        or row.get("targetId")
        or row.get("target")
        or row.get("object")
        or row.get("resource")
        or ""
    )
    candidates = [target_id, metadata.get("device_id"), metadata.get("deviceId")]
    device = None
    for candidate in candidates:
        ref = str(candidate or "").strip()
        if not ref:
            continue
        # Viewer and permission audit rows use ``device:child`` composite
        # identifiers. Resolve the device portion so the dashboard can show
        # its friendly name instead of exposing an opaque internal id.
        refs = [ref]
        if target_type in {"device_viewer", "device_permission"} and ":" in ref:
            refs.insert(0, ref.split(":", 1)[0])
        for resolved_ref in refs:
            if resolved_ref in device_by_ref:
                device = device_by_ref[resolved_ref]
                break
        if device:
            break
    outcome = str(
        row.get("outcome")
        or row.get("result")
        or row.get("status")
        or row.get("outcome_code")
        or row.get("result_code")
        or "unknown"
    )
    severity = str(row.get("severity") or row.get("level") or "info")
    detail = str(row.get("detail") or row.get("summary") or row.get("message") or "")
    timestamp = row.get("ts")
    if timestamp is None:
        timestamp = row.get("timestamp") or row.get("created_at") or row.get("createdAt") or 0
    try:
        timestamp = int(timestamp or 0)
    except (TypeError, ValueError):
        timestamp = 0
    device_id = storage.public_device_id(str(device["id"])) if device else ""
    return {
        "event_id": str(row.get("event_id") or row.get("eventId") or row.get("id") or ""),
        "ts": timestamp,
        "username": actor,
        "actor_role": str(row.get("actor_role") or row.get("actorRole") or ""),
        "action": action,
        "action_code": action,
        "action_label": _dashboard_action_label(action),
        "target_type": target_type,
        "target_id": target_id,
        "device_id": device_id,
        "device_name": str(device.get("name") or "") if device else "",
        "outcome": outcome,
        "result": outcome,
        "severity": severity,
        "level": severity,
        "reason": str(row.get("reason") or ""),
        "request_id": str(row.get("request_id") or row.get("requestId") or row.get("session_id") or row.get("sessionId") or ""),
        "source_ip": str(row.get("source_ip") or row.get("sourceIp") or row.get("ip") or row.get("ipAddress") or ""),
        "detail": detail,
        "summary": detail,
        "metadata": metadata,
    }


def _dashboard_alert_projection(alert: dict, device_by_ref: dict[str, dict]) -> dict:
    """Add readable target aliases while preserving the durable alert shape."""
    row = dict(alert or {})
    projected = _dashboard_activity_projection(row, device_by_ref)
    projected.update(
        {
            "alert_id": str(row.get("event_id") or row.get("eventId") or row.get("id") or ""),
            "alert_type": str(row.get("alert_type") or row.get("alertType") or "service_failure"),
            "title": str(row.get("title") or ""),
            "summary": str(row.get("summary") or row.get("detail") or ""),
            "log_url": str(row.get("log_url") or row.get("logUrl") or "/logs"),
            "handled": bool(row.get("handled") or row.get("handled_at")),
            "handled_at": row.get("handled_at"),
            "handled_by": str(row.get("handled_by") or row.get("handledBy") or ""),
        }
    )
    # Camel-case aliases are consumed by the browser adapter and keep this
    # projection compatible with older dashboard clients.
    projected.update(
        {
            "alertId": projected["alert_id"],
            "alertType": projected["alert_type"],
            "logUrl": projected["log_url"],
            "handledAt": projected["handled_at"],
            "handledBy": projected["handled_by"],
        }
    )
    return projected


_ATTENTION_KIND_ORDER = {
    "account_expired": 0,
    "account_expiring": 1,
    "device_offline": 2,
    "alas_error": 3,
}

_DEVICE_OFFLINE_STATES = {
    "offline",
    "unauthorized",
    "network_unreachable",
    "disconnected",
    "missing",
    "unreachable",
    "error",
    "failed",
    "check-fail",
    "check_fail",
}

_ALAS_ERROR_STATES = {
    "error",
    "unreachable",
    "partial_error",
    "http_error",
    "token_missing",
    "token_invalid",
    "disconnected",
    "timeout",
}


def _attention_days(remaining_seconds: object, expires_at: object) -> int | None:
    """Return a whole-day count for the account-expiry rows."""
    if remaining_seconds is not None:
        try:
            remaining = int(remaining_seconds)
        except (TypeError, ValueError):
            remaining = 0
        if remaining > 0:
            return max(1, (remaining + 86399) // 86400)
    if expires_at:
        try:
            overdue = max(0, int(time.time()) - int(expires_at))
        except (TypeError, ValueError):
            return None
        return max(1, (overdue + 86399) // 86400)
    return None


def _dashboard_attention_items(devices, users, alas_status) -> list[dict]:
    """Build the state-based pending list: account expiry, offline devices, ALAS.

    These items describe the current state (they clear when the state is
    fixed), unlike the durable audit alerts which need an explicit resolve.
    """
    items: list[dict] = []
    for user in users or []:
        state = str(user.get("expiration_state") or "").strip().lower()
        if state not in {"expired", "expiring"}:
            continue
        items.append(
            {
                "kind": "account_expired" if state == "expired" else "account_expiring",
                "severity": "error" if state == "expired" else "warning",
                "target": str(user.get("username") or ""),
                "days": _attention_days(user.get("remaining_seconds"), user.get("expires_at")),
                "href": "/users",
            }
        )
    for device in devices or []:
        if device.get("enabled") is False:
            continue
        state = str(device.get("status_label") or device.get("adb_state") or "").strip().lower()
        if state not in _DEVICE_OFFLINE_STATES:
            continue
        items.append(
            {
                "kind": "device_offline",
                "severity": "warning",
                "target": str(device.get("name") or device.get("id") or ""),
                "device_id": str(device.get("id") or ""),
                "href": "/devices",
            }
        )
    alas = alas_status if isinstance(alas_status, dict) else {}
    if alas.get("enabled") and not alas.get("token_set"):
        items.append({"kind": "alas_error", "severity": "warning", "target": "", "href": "/alas"})
    elif str(alas.get("status") or "").strip().lower() in _ALAS_ERROR_STATES:
        items.append({"kind": "alas_error", "severity": "warning", "target": "", "href": "/alas"})
    items.sort(key=lambda item: (_ATTENTION_KIND_ORDER.get(str(item["kind"]), 9), str(item.get("target") or "")))
    return items


@router.get("/api/admin/overview")
async def admin_overview(request: Request):
    user = security.require_admin(request)
    devices, users, _alas_bindings, alas_status = await asyncio.to_thread(admin_overview_storage_payload)
    sessions = await manager.snapshot()
    statuses = adb_monitor.snapshot()
    active_clients = await manager.admin_client_snapshot()
    alerts, audit_rows = await asyncio.gather(
        asyncio.to_thread(storage.list_audit_alerts, limit=20),
        asyncio.to_thread(storage.recent_audit, 24),
    )
    # ``admin_overview_storage_payload`` already owns the complete device
    # catalog. Reuse it for the activity projection instead of opening a
    # second SQLite connection for the same rows.
    all_devices = devices
    device_by_ref: dict[str, dict] = {}
    for device in all_devices:
        internal_id = str(device["id"])
        device_by_ref[internal_id] = device
        device_by_ref[storage.public_device_id(internal_id)] = device
    if isinstance(alerts, dict):
        alert_items = alerts.get("items")
        if not isinstance(alert_items, list):
            alert_items = alerts.get("alerts") if isinstance(alerts.get("alerts"), list) else []
        projected_alerts = [_dashboard_alert_projection(row, device_by_ref) for row in alert_items]
        alerts = {**alerts, "items": projected_alerts, "alerts": projected_alerts}
    device_names = {str(row["id"]): str(row.get("name") or row["id"]) for row in all_devices}
    active_sessions = [
        {
            **row,
            "device_id": storage.public_device_id(str(row.get("device_id") or "")),
            "device_name": device_names.get(str(row.get("device_id") or ""), str(row.get("device_id") or "")),
            "controller": bool(row.get("has_control")),
        }
        for row in active_clients
    ]
    audit_rows = [
        row
        for row in audit_rows
        if storage.audit_event_dashboard_visible(row)
    ][-12:]
    recent_activities = [_dashboard_activity_projection(row, device_by_ref) for row in reversed(audit_rows)]
    device_payloads = devices_payload(devices, sessions, statuses)
    return {
        "user": user_payload(user),
        "devices": device_payloads,
        "sessions": sessions,
        "active_sessions": active_sessions,
        "users": users,
        "alas": alas_status,
        "alerts": alerts,
        # State-based pending items, kept separate from the durable audit alerts.
        "attention": _dashboard_attention_items(device_payloads, users, alas_status),
        "recent_activities": recent_activities,
        # This is a bounded operational summary only; it never includes log
        # messages, request payloads, credentials, or device addresses.
        "logging_health": logging_health(),
    }


@router.get("/api/admin/dashboard/snapshot")
async def admin_dashboard_snapshot(request: Request):
    """Dashboard read model; runtime ALAS probing is opt-in and bounded."""
    payload = await admin_overview(request)
    if parse_bool(request.query_params.get("runtime"), False):
        try:
            payload["alas"] = await admin_alas_overview()
        except Exception as exc:
            log.warning("ADMIN_DASHBOARD_ALAS_SNAPSHOT_FAILED error_type=%s", type(exc).__name__)
            payload["alas"] = {
                **(payload.get("alas") or {}),
                "status": "unreachable",
                "error": public_error_detail(exc),
            }
    payload["updated_at"] = int(time.time())
    # Recompute the pending list once the runtime ALAS state is known.
    payload["attention"] = _dashboard_attention_items(
        payload.get("devices") or [],
        payload.get("users") or [],
        payload.get("alas") or {},
    )
    return payload


@router.get("/api/admin/overview/alas")
async def admin_overview_alas(request: Request):
    security.require_admin(request)
    alas_bindings = await asyncio.to_thread(storage.list_user_alas_bindings)
    return await admin_alas_overview(alas_bindings)


__all__ = [
    "_DASHBOARD_ACTION_LABELS",
    "_dashboard_action_label",
    "_dashboard_activity_projection",
    "_dashboard_alert_projection",
    "_dashboard_attention_items",
    "admin_dashboard_snapshot",
    "admin_overview",
    "admin_overview_alas",
    "router",
]
