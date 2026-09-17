"""Administrator UI settings and maintenance routes."""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, HTTPException, Request

from .. import alas, i18n, security, storage, workbench_features
from ..booleans import InvalidBooleanValue, parse_bool_strict
from ..http_helpers import parse_body, public_error_detail, video_option_error_message
from ..mirror_manager import manager
from ..services.audit_service import audit_request
from ..video_options import (
    PROFILE_NAMES,
    VideoOptionError,
    bandwidth_preset_value,
    enabled_profile_ids_value,
    enabled_stream_modes_value,
    int_value,
    max_size_limit_value,
    profile_payloads,
    stream_mode_value,
)

router = APIRouter()


def admin_ui_settings_payload() -> dict:
    settings = storage.get_ui_settings()
    now = int(time.time())
    theme = str(settings["ui_theme_mode"])
    if theme == "system":
        theme = "auto"
    return {
        "settings": {
            "systemName": settings["ui_system_name"],
            "language": settings["ui_language"] or "zh-CN",
            "themeMode": theme,
            "expiryReminderDays": settings["expiry_reminder_days"],
            "stopAlasOnExpiry": settings["stop_alas_on_expiry"],
            "logRetentionDays": settings["log_retention_days"],
        },
        "system": {"version": "2.0.0", "build": ""},
        "version": "2.0.0",
        "serverTime": now,
        "timezone": time.strftime("%Z"),
        "updatedAt": now,
        "health": {"service": {"status": "ok", "label": "运行中"}},
    }


# 这里只列出**真正生效**的系统级设置。`session_hours` / `max_mirror_hours` 曾经出现在
# 这个表里，但没有任何代码读取它们（登录会话寿命是 storage.SESSION_IDLE_SECONDS 常量，
# 投屏时长上限是 max_session_minutes）—— 继续暴露等于给 API 与导出文件一个假开关。
# 真正生效的投屏时长上限走 `_normalize_imported_video` 里的 max_session_minutes。
_UI_SETTING_FIELDS = (
    ("systemName", "ui_system_name", "text"),
    ("language", "ui_language", "locale"),
    ("themeMode", "ui_theme_mode", "theme"),
    ("expiryReminderDays", "expiry_reminder_days", ("int", 0, 90)),
    ("stopAlasOnExpiry", "stop_alas_on_expiry", "bool"),
    # 日志保存时长：固定档位（0 = 不清理），不是任意天数。
    ("logRetentionDays", "log_retention_days", ("choice", storage.LOG_RETENTION_DAY_OPTIONS)),
)


def _normalize_ui_setting_updates(payload: dict) -> dict:
    updates: dict[str, object] = {}
    for camel, key, kind in _UI_SETTING_FIELDS:
        if camel not in payload:
            continue
        value = payload[camel]
        if kind == "text":
            text = str(value or "").strip()
            if len(text) > 64:
                raise HTTPException(status_code=400, detail=i18n.translate("server.error.invalid_setting_value"))
            updates[key] = text
        elif kind == "locale":
            text = str(value or "").strip()
            if text and text not in ("zh-CN", "en-US"):
                raise HTTPException(status_code=400, detail=i18n.translate("server.error.invalid_setting_value"))
            updates[key] = text
        elif kind == "theme":
            text = str(value or "").strip()
            if text not in ("system", "auto", "light", "dark"):
                raise HTTPException(status_code=400, detail=i18n.translate("server.error.invalid_setting_value"))
            updates[key] = "system" if text == "auto" else text
        elif kind == "bool":
            try:
                updates[key] = parse_bool_strict(value)
            except InvalidBooleanValue:
                raise HTTPException(
                    status_code=400,
                    detail=i18n.translate("server.error.invalid_setting_value"),
                ) from None
        elif kind[0] == "choice":
            # 只接受列出的档位；不在这里做「就近收敛」，避免静默改写管理员的选择。
            allowed = tuple(int(option) for option in kind[1])
            try:
                candidate = int(str(value).strip())
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400,
                    detail=i18n.translate("server.error.invalid_setting_value"),
                ) from None
            if candidate not in allowed:
                raise HTTPException(status_code=400, detail=i18n.translate("server.error.invalid_setting_value"))
            updates[key] = candidate
        else:
            low, high = kind[1], kind[2]
            try:
                number = int(value)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail=i18n.translate("server.error.invalid_setting_value")) from None
            if number < low or number > high:
                raise HTTPException(status_code=400, detail=i18n.translate("server.error.invalid_setting_value"))
            updates[key] = number
    if not updates:
        raise HTTPException(status_code=400, detail=i18n.translate("server.error.no_settings_provided"))
    return updates


# video_adaptive 是**预留字段**：设置、DB 列、per-user 偏好、PUT 校验都存在，
# 但 mirror_runtime / mirror_websocket 从不读它，前端也从不发送它（前端那些
# adaptive* 是 CSS 自适应画面尺寸，同名不同事）。既然没有消费者，就不要把它写进
# 用户会打开看的配置文件——留在导出里等于一个假开关。
_VIDEO_IMPORT_BOOL_KEYS = ("video_user_custom_tuning",)
_VIDEO_IMPORT_INT_KEYS = ("video_bit_rate", "max_size", "max_fps")
_VIDEO_IMPORT_PROFILE_KEYS = ("video_profile", "video_fullscreen_profile")
_ALAS_IMPORT_FORBIDDEN_KEYS = ("token", "api_token", "clear_token")
_AUTO_STOP_MINUTES_MAX = 1440
# 与 admin_video._parse_video_limits 的上限保持一致（10080 分钟 = 7 天）。
_MAX_SESSION_MINUTES_MAX = 10080


def _normalize_imported_video(section: dict) -> dict[str, str]:
    """Validate an exported video section and return storage-ready values.

    Values are checked with the same helpers the video settings route uses, so
    an imported file cannot inject a profile, stream mode, resolution cap or
    numeric value that the runtime would otherwise reject.
    """
    updates: dict[str, str] = {}
    for key in _VIDEO_IMPORT_BOOL_KEYS:
        if key in section and section[key] is not None:
            updates[key] = "true" if parse_bool_strict(section[key]) else "false"
    for key in _VIDEO_IMPORT_INT_KEYS:
        if key in section and section[key] not in (None, ""):
            updates[key] = str(int_value(section[key], key, 0))
    if section.get("auto_stop_minutes") not in (None, ""):
        try:
            minutes = int(section["auto_stop_minutes"])
        except (TypeError, ValueError):
            raise VideoOptionError(
                "auto_stop_minutes must be integer",
                "server.video_error.must_be_integer",
                field="auto_stop_minutes",
            ) from None
        if minutes < 0 or minutes > _AUTO_STOP_MINUTES_MAX:
            raise VideoOptionError(
                "auto_stop_minutes out of range",
                "server.video_error.out_of_range",
                field="auto_stop_minutes",
            )
        updates["auto_stop_minutes"] = str(minutes)
    if section.get("max_session_minutes") not in (None, ""):
        try:
            session_minutes = int(section["max_session_minutes"])
        except (TypeError, ValueError):
            raise VideoOptionError(
                "max_session_minutes must be integer",
                "server.video_error.must_be_integer",
                field="max_session_minutes",
            ) from None
        if session_minutes < 0 or session_minutes > _MAX_SESSION_MINUTES_MAX:
            raise VideoOptionError(
                "max_session_minutes out of range",
                "server.video_error.out_of_range",
                field="max_session_minutes",
            )
        updates["max_session_minutes"] = str(session_minutes)
    if "scrcpy_stream_mode" in section:
        updates["scrcpy_stream_mode"] = stream_mode_value(section["scrcpy_stream_mode"])
    if "scrcpy_enabled_stream_modes" in section:
        updates["scrcpy_enabled_stream_modes"] = ",".join(
            enabled_stream_modes_value(section["scrcpy_enabled_stream_modes"])
        )
    if "video_max_size_limit" in section:
        updates["video_max_size_limit"] = str(
            max_size_limit_value(section["video_max_size_limit"], strict=True)
        )
    if "video_bandwidth_preset" in section:
        updates["video_bandwidth_preset"] = bandwidth_preset_value(
            section["video_bandwidth_preset"], strict=True
        )
    profiles = profile_payloads({**storage.get_settings(), **updates})
    for key in _VIDEO_IMPORT_PROFILE_KEYS:
        if key not in section:
            continue
        value = str(section[key] or "").strip()
        if not value and key == "video_fullscreen_profile":
            updates[key] = ""
            continue
        if value not in profiles and value not in ("custom", "auto"):
            raise VideoOptionError("profile is invalid", "server.video_error.profile_invalid")
        updates[key] = value
    if "video_enabled_presets" in section:
        # An empty allow-list means "every available preset", including custom
        # presets created later; keep that meaning instead of freezing a list.
        raw_enabled_presets = section["video_enabled_presets"]
        if raw_enabled_presets in (None, ""):
            updates["video_enabled_presets"] = ""
        else:
            updates["video_enabled_presets"] = ",".join(
                enabled_profile_ids_value(
                    raw_enabled_presets,
                    tuple(profiles),
                    default=tuple(PROFILE_NAMES),
                )
            )
    return updates


def _validate_imported_alas(section: dict) -> dict:
    """Return a validated ALAS settings payload; credentials are never imported."""
    for key in _ALAS_IMPORT_FORBIDDEN_KEYS:
        if key in section:
            raise HTTPException(
                status_code=400,
                detail=i18n.translate("server.error.invalid_setting_value"),
            )
    payload: dict = {}
    if "enabled" in section:
        parse_bool_strict(section["enabled"])
        payload["enabled"] = section["enabled"]
    if section.get("base_url") is not None:
        raw = str(section.get("base_url") or "").strip().rstrip("/") or "http://127.0.0.1:22267"
        try:
            alas.resolve_base_url(raw)
        except ValueError as exc:
            # Match save_settings: a syntax error is rejected, an unreachable
            # Runtime still saves so it can become reachable later.
            if "unreachable" not in str(exc):
                raise
        payload["base_url"] = section["base_url"]
    if "workbench_alas_visible" in section:
        parse_bool_strict(section["workbench_alas_visible"])
        payload["workbench_alas_visible"] = section["workbench_alas_visible"]
    return payload


def _validate_imported_workbench(section: dict) -> dict[str, str]:
    """校验导入的投屏管理工作台开关与菜单编排（未知功能 id / 非法层级一律拒绝）。"""
    raw_features = section.get("features", section)
    raw_layout = section.get("layout")
    try:
        switches = workbench_features.normalize_switches(
            raw_features if isinstance(raw_features, dict) and "level1" not in raw_features else None,
            strict=True,
        )
        if raw_layout is None and isinstance(raw_features, dict) and "level1" in raw_features:
            raw_layout = raw_features
        layout = (
            workbench_features.normalize_layout(raw_layout, strict=True)
            if raw_layout is not None
            else workbench_features.default_layout()
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=i18n.translate("server.error.invalid_setting_value"),
        ) from exc
    return {
        workbench_features.SETTING_KEY: workbench_features.serialize_switches(switches),
        workbench_features.LAYOUT_KEY: workbench_features.serialize_layout(layout),
    }


@router.get("/api/admin/settings")
async def admin_ui_settings(request: Request):
    security.require_admin(request)
    return await asyncio.to_thread(admin_ui_settings_payload)


# ---- 登录保护（管理员查看/解锁） ----

@router.get("/api/admin/login-guard")
async def admin_login_guard(request: Request):
    security.require_admin(request)
    return await asyncio.to_thread(security.login_guard_snapshot)


@router.put("/api/admin/login-guard")
async def admin_save_login_guard(request: Request):
    """保存安全机制配置：管理后台取值覆盖环境变量默认值，立即生效。"""
    security.verify_csrf(request)
    admin = security.require_admin(request)
    payload = await parse_body(request)
    try:
        saved = await asyncio.to_thread(security.save_login_guard_config, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=public_error_detail(exc)) from exc
    audit_request(
        request,
        admin,
        "login_guard_settings",
        target_type="system_settings",
        target_id="login_guard",
        metadata={"config": saved},
    )
    return await asyncio.to_thread(security.login_guard_snapshot)


@router.post("/api/admin/login-guard/unlock")
async def admin_login_guard_unlock(request: Request):
    security.verify_csrf(request)
    admin = security.require_admin(request)
    payload = await parse_body(request)
    key = str(payload.get("key") or "").strip()
    if not key.startswith(("ip:", "user:")) or len(key) > 128:
        raise HTTPException(status_code=400, detail=i18n.translate("server.error.invalid_request"))
    cleared = security.clear_login_guard_key(key)
    audit_request(
        request,
        admin,
        "login_guard_unlock",
        target_type="login_guard",
        target_id=key,
        metadata={"cleared": cleared},
    )
    return {"ok": True, "cleared": cleared, "snapshot": await asyncio.to_thread(security.login_guard_snapshot)}


@router.put("/api/admin/settings")
async def admin_save_ui_settings(request: Request):
    security.verify_csrf(request)
    admin = security.require_admin(request)
    payload = await parse_body(request)
    updates = _normalize_ui_setting_updates(payload)
    try:
        await asyncio.to_thread(storage.save_ui_settings, updates)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=i18n.translate("server.error.invalid_setting_value")) from exc
    audit_request(
        request,
        admin,
        "ui_settings_update",
        target_type="system_settings",
        target_id="ui",
        metadata={"changed_fields": sorted(updates.keys())},
    )
    return await asyncio.to_thread(admin_ui_settings_payload)


@router.post("/api/admin/settings/maintenance")
async def admin_ui_settings_maintenance(request: Request):
    security.verify_csrf(request)
    admin = security.require_admin(request)
    payload = await parse_body(request)
    action = str(payload.get("action") or "").strip()
    if action == "clean-sessions":
        stopped = 0
        failed = 0
        snapshot = await manager.snapshot()
        for device_id, session in snapshot.items():
            if session.get("running"):
                try:
                    await manager.stop(device_id)
                    stopped += 1
                except Exception:
                    failed += 1
        audit_request(
            request,
            admin,
            "ui_settings_maintenance",
            outcome="failure" if failed else "success",
            target_type="system_settings",
            target_id="clean-sessions",
            metadata={"stopped": stopped, "failed": failed},
        )
        return {
            "ok": not failed,
            "action": action,
            "stopped": stopped,
            "failed": failed,
            **(await asyncio.to_thread(admin_ui_settings_payload)),
        }
    if action == "reload-config":
        audit_request(
            request,
            admin,
            "ui_settings_maintenance",
            outcome="success",
            target_type="system_settings",
            target_id="reload-config",
        )
        return {"ok": True, "action": action, **(await asyncio.to_thread(admin_ui_settings_payload))}
    raise HTTPException(status_code=400, detail=i18n.translate("server.error.maintenance_unsupported"))


@router.post("/api/admin/settings/reset")
async def admin_ui_settings_reset(request: Request):
    security.verify_csrf(request)
    admin = security.require_admin(request)
    await asyncio.to_thread(storage.reset_ui_settings)
    audit_request(
        request,
        admin,
        "ui_settings_reset",
        target_type="system_settings",
        target_id="ui",
    )
    return await asyncio.to_thread(admin_ui_settings_payload)


@router.post("/api/admin/settings/restart")
async def admin_ui_settings_restart(request: Request):
    security.verify_csrf(request)
    security.require_admin(request)
    raise HTTPException(status_code=400, detail=i18n.translate("server.error.maintenance_unsupported"))


def _admin_ui_settings_export_payload() -> dict:
    settings = storage.get_ui_settings()
    video_keys = (
        "video_profile",
        "video_bit_rate",
        "max_size",
        "max_fps",
        "scrcpy_stream_mode",
        "scrcpy_enabled_stream_modes",
        "video_enabled_presets",
        "video_bandwidth_preset",
        "video_max_size_limit",
        "video_user_custom_tuning",
        "video_fullscreen_profile",
        "auto_stop_minutes",
        # 真正生效的单次投屏时长上限（0 = 不限）。此前导出只带了失效的
        # maxMirrorHours，导致「导出→导入」会把管理员设的时长上限悄悄丢成默认值。
        "max_session_minutes",
    )
    video = {key: storage.get_settings([key]).get(key) for key in video_keys}
    export_theme = str(settings["ui_theme_mode"])
    if export_theme == "system":
        export_theme = "auto"
    return {
        "settings": {
            "systemName": settings["ui_system_name"],
            "language": settings["ui_language"] or "zh-CN",
            "themeMode": export_theme,
            "expiryReminderDays": settings["expiry_reminder_days"],
            "stopAlasOnExpiry": settings["stop_alas_on_expiry"],
            "logRetentionDays": settings["log_retention_days"],
        },
        "video": video,
        "alas": alas.public_settings(),
        "workbench": {
            "features": workbench_features.switches_from(
                storage.get_setting(workbench_features.SETTING_KEY, "")
            ),
            "layout": workbench_features.layout_from(
                storage.get_setting(workbench_features.LAYOUT_KEY, "")
            ),
        },
        "exportedAt": int(time.time()),
        "filename": "scrcpygate-config.json",
    }


@router.get("/api/admin/settings/export")
async def admin_ui_settings_export(request: Request):
    security.require_admin(request)
    return await asyncio.to_thread(_admin_ui_settings_export_payload)


@router.post("/api/admin/settings/import")
async def admin_ui_settings_import(request: Request):
    security.verify_csrf(request)
    admin = security.require_admin(request)
    payload = await parse_body(request)
    source = payload.get("settings")
    if not isinstance(source, dict):
        raise HTTPException(status_code=400, detail=i18n.translate("server.error.invalid_setting_value"))
    raw_video = payload.get("video")
    if raw_video is not None and not isinstance(raw_video, dict):
        raise HTTPException(status_code=400, detail=i18n.translate("server.error.invalid_setting_value"))
    raw_alas = payload.get("alas")
    if raw_alas is not None and not isinstance(raw_alas, dict):
        raise HTTPException(status_code=400, detail=i18n.translate("server.error.invalid_setting_value"))
    raw_workbench = payload.get("workbench")
    if raw_workbench is not None and not isinstance(raw_workbench, dict):
        raise HTTPException(status_code=400, detail=i18n.translate("server.error.invalid_setting_value"))
    updates = _normalize_ui_setting_updates(source)
    try:
        video_updates = (
            await asyncio.to_thread(_normalize_imported_video, raw_video) if raw_video else {}
        )
    except InvalidBooleanValue:
        raise HTTPException(
            status_code=400,
            detail=i18n.translate("server.error.invalid_setting_value"),
        ) from None
    except VideoOptionError as exc:
        raise HTTPException(status_code=400, detail=video_option_error_message(exc)) from exc
    try:
        alas_updates = (
            await asyncio.to_thread(_validate_imported_alas, raw_alas) if raw_alas else {}
        )
    except InvalidBooleanValue:
        raise HTTPException(
            status_code=400,
            detail=i18n.translate("server.error.invalid_setting_value"),
        ) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=public_error_detail(exc)) from exc

    workbench_updates = (
        await asyncio.to_thread(_validate_imported_workbench, raw_workbench)
        if raw_workbench
        else {}
    )

    applied_sections = ["settings"]
    try:
        await asyncio.to_thread(storage.save_ui_settings, updates)
        if video_updates:
            await asyncio.to_thread(storage.set_settings, video_updates)
            applied_sections.append("video")
        if alas_updates:
            await asyncio.to_thread(alas.save_settings, alas_updates)
            applied_sections.append("alas")
        if workbench_updates:
            await asyncio.to_thread(storage.set_settings, workbench_updates)
            applied_sections.append("workbench")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=public_error_detail(exc)) from exc
    audit_request(
        request,
        admin,
        "ui_settings_import",
        target_type="system_settings",
        target_id="ui",
        metadata={
            "changed_fields": sorted(updates.keys()),
            "applied_sections": applied_sections,
            "video_fields": sorted(video_updates.keys()),
            "alas_fields": sorted(alas_updates.keys()),
            "workbench_fields": sorted(workbench_updates.keys()),
        },
    )
    return {
        **(await asyncio.to_thread(admin_ui_settings_payload)),
        "imported": {
            "applied_sections": applied_sections,
            "video_fields": sorted(video_updates.keys()),
            "alas_fields": sorted(alas_updates.keys()),
            "workbench_fields": sorted(workbench_updates.keys()),
        },
    }

__all__ = [name for name in globals() if name.startswith("admin_")] + ["router"]
