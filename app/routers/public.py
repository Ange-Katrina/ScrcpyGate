"""Public, authentication, workbench, mirror and user ALAS routes."""

from __future__ import annotations

import asyncio
import logging
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from .. import i18n, login_guard, security, storage, workbench_features
from ..booleans import InvalidBooleanValue, parse_bool_strict
from ..account_access import (
    SESSION_REVOKED_CLOSE_CODE,
    SESSION_REVOKED_CLOSE_REASON,
    account_connections,
)
from ..adb_monitor import adb_monitor
from ..devices import devices_payload, sessions_payload
from ..mirror_manager import manager
from ..runtime import auto_stop_minutes, runtime_for
from ..http_helpers import parse_body, parse_bool, public_error_detail, user_payload, video_option_error_message
from ..services.audit_service import audit_request
from ..services.alas_service import (
    alas_binding_for_user,
    alas_device_for_user,
    cached_public_alas_status,
    public_admin_alas_bindings,
)
from ..services.domain_helpers import (
    clear_session_cookie,
    default_video_options,
    set_session_cookie,
    user_video_fallback,
    user_video_options,
    video_profiles,
)
from ..video_options import (
    BANDWIDTH_PRESET_KEYS,
    FULLSCREEN_MIN_MAX_SIZE,
    RESOLUTION_CAP_OPTIONS,
    VIDEO_LIMITS,
    VideoOptionError,
    bandwidth_preset_value,
    bandwidth_profile_payload,
    bandwidth_profile_value,
    clamp_profiles,
    emergency_profile_payload,
    enabled_profile_ids,
    enabled_stream_modes_value,
    fullscreen_profile_value,
    max_size_limit_value,
    preset_catalog,
    profile_label_payloads,
    public_video_options,
)

log = logging.getLogger("webscrcpy.main")
router = APIRouter()


@router.get("/api/access-status")
@router.get("/alas/access-status")
async def access_status(request: Request):
    """Diagnostic for failed WS handshakes; HTTP middleware enforces BAN/GEO."""
    return JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})

@router.get("/healthz")
async def healthz():
    return {"ok": True}


@router.get("/api/auth/challenge")
async def api_auth_challenge(request: Request):
    """Issue a signed proof-of-work challenge (public).

    While the source IP is locked out the endpoint returns the remaining time
    so the login page can paint the countdown banner without attempting a
    login first.
    """
    status = security.login_rate_limit_status(request, "")
    if status["limited"]:
        return JSONResponse(
            status_code=200,
            content={
                "locked": {
                    "retry_after_ms": status["retry_after"] * 1000,
                    "failures": status["failures"],
                },
                "challenge": None,
            },
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )
    if not security.login_captcha_enabled():
        return JSONResponse(
            status_code=200,
            content={"challenge": None, "captcha_required": False},
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )
    if request.query_params.get("status") == "1":
        return JSONResponse(
            content={"challenge": None, "captcha_required": status["captcha_required"]},
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )
    username = (request.query_params.get("user") or "").strip()[:64]
    issued = await asyncio.to_thread(
        login_guard.issue_challenge, security.client_ip(request), username, status["failures"]
    )
    if issued.get("error") == "provider_unavailable":
        return JSONResponse(status_code=503, content={"code": "CAPTCHA_UNAVAILABLE", "message": "Verification unavailable"},
                            headers={"Retry-After": "5", "Cache-Control": "no-store"})
    if issued.get("error") == "challenge_rate_limited":
        return JSONResponse(
            status_code=429,
            content={"code": "CAPTCHA_ISSUE_RATE_LIMITED", "retry_after_ms": issued["retry_after_ms"]},
            headers={
                "Retry-After": str(max(1, int(issued["retry_after_ms"]) // 1000 + 1)),
                "Cache-Control": "no-store",
                "Pragma": "no-cache",
            },
        )
    return JSONResponse(
        status_code=200,
        content={"challenge": issued, "captcha_required": status["captcha_required"]},
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@router.post("/api/auth/login")
async def api_auth_login(request: Request):
    data = await parse_body(request)
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))
    client_ip = security.client_ip(request)
    status = security.login_rate_limit_status(request, username)
    if status["limited"]:
        audit_request(
            request,
            username or "anonymous",
            "login_rate_limited",
            outcome="denied",
            reason="rate_limited",
            severity="warning",
            target_type="account",
            target_id=username or "anonymous",
            metadata={"retry_after": status["retry_after"]},
        )
        return JSONResponse(
            status_code=429,
            content={"code": "RATE_LIMITED", "message": i18n.translate("login.error.rate_limited")},
            headers={"Retry-After": str(status["retry_after"]), "Cache-Control": "no-store", "Pragma": "no-cache"},
        )
    # 失败 1 次起要求验证码：先校验签名挑战与 PoW，再做密码校验。
    if security.login_captcha_enabled() and status["captcha_required"]:
        proof = data.get("proof")
        if not isinstance(proof, str) or not proof:
            audit_request(
                request,
                username or "anonymous",
                "captcha_required",
                outcome="denied",
                reason="captcha_required",
                severity="info",
                target_type="account",
                target_id=username or "anonymous",
                metadata={"failures": status["failures"]},
            )
            return JSONResponse(
                status_code=400,
                content={
                    "code": "CAPTCHA_REQUIRED",
                    "message": i18n.translate("login.error.captcha_required"),
                    "failures": status["failures"],
                },
                headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
            )
        verified = await asyncio.to_thread(login_guard.verify_proof, proof, client_ip, username)
        if not verified["ok"]:
            audit_request(
                request,
                username or "anonymous",
                "captcha_failed",
                outcome="denied",
                reason=str(verified.get("reason") or "captcha_failed"),
                severity="warning",
                target_type="account",
                target_id=username or "anonymous",
                metadata={"failures": status["failures"]},
            )
            return JSONResponse(
                status_code=400,
                content={
                    "code": "CAPTCHA_INVALID",
                    "message": i18n.translate("login.error.captcha_invalid"),
                    "reason": verified.get("reason"),
                },
                headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
            )
    reservation = security.reserve_login_attempt(request, username)
    if not reservation.get("allowed"):
        audit_request(
            request,
            username or "anonymous",
            "login_rate_limited",
            outcome="denied",
            reason="rate_limited",
            severity="warning",
            target_type="account",
            target_id=username or "anonymous",
            metadata={"retry_after": reservation["retry_after"]},
        )
        return JSONResponse(
            status_code=429,
            content={"code": "RATE_LIMITED", "message": i18n.translate("login.error.rate_limited")},
            headers={"Retry-After": str(reservation["retry_after"]), "Cache-Control": "no-store", "Pragma": "no-cache"},
        )
    # PBKDF2 属 CPU 密集同步调用：移入线程池避免阻塞事件循环；失败额度已由预占记账。
    user = await asyncio.to_thread(storage.authenticate, username, password)
    if not user:
        failed = security.login_failure_from_reservation(request, username, reservation)
        audit_request(
            request,
            username or "anonymous",
            "login_failed",
            outcome="denied",
            reason="rate_limited" if failed["limited"] else "invalid_credentials",
            severity="warning",
            target_type="account",
            target_id=username or "anonymous",
            metadata={"failures": failed["failures"], "locked": failed["limited"]},
        )
        if failed["limited"]:
            return JSONResponse(
                status_code=429,
                content={"code": "RATE_LIMITED", "message": i18n.translate("login.error.rate_limited"), "locked": True},
                headers={"Retry-After": str(failed["retry_after"]), "Cache-Control": "no-store", "Pragma": "no-cache"},
            )
        # 告诉登录页下一次提交是否会要求验证码；登录保护关闭时不返回次数/阈值，
        # 避免前端显示「第 0 次…将封禁」这类与服务端不一致的提示。
        next_status = security.login_rate_limit_status(request, username)
        content = {
            "code": "AUTH_INVALID",
            "message": i18n.translate("login.error.invalid_credentials"),
            "captcha_required": next_status["captcha_required"],
        }
        if security.login_guard_config()["enabled"]:
            content["failures"] = failed["failures"]
            content["lock_after"] = failed["max_attempts"]
        return JSONResponse(
            status_code=401,
            content=content,
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )
    try:
        session = await asyncio.to_thread(
            storage.create_session,
            user["username"],
            client_ip=security.client_ip(request),
            user_agent=request.headers.get("user-agent"),
            # 浏览器侧稳定设备标识（前端 api.js 统一带上）：安全页据此把同一台设备的
            # 多条会话归并成一行。
            device_id=request.headers.get("x-device-id"),
        )
    except ValueError:
        audit_request(
            request,
            username or "anonymous",
            "login_failed",
            outcome="denied",
            reason="invalid_credentials",
            severity="warning",
            target_type="account",
            target_id=username or "anonymous",
        )
        return JSONResponse(
            status_code=401,
            content={"code": "AUTH_INVALID", "message": i18n.translate("login.error.invalid_credentials")},
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )
    security.refund_login_attempt(request, username, reservation)
    security.record_login_success(request, username)
    login_at = storage.now_ts()
    await asyncio.to_thread(storage.record_last_login, user["username"], security.client_ip(request), ts=login_at)
    user["last_login_at"] = login_at
    user["last_login_ip"] = security.client_ip(request)
    audit_request(
        request,
        user,
        "login_success",
        ts=login_at,
        target_type="account",
        target_id=user["username"],
    )
    response = JSONResponse({"user": user_payload(user), "csrf_token": session["csrf_token"]})
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    set_session_cookie(response, session["sid"])
    return response


@router.post("/api/auth/logout")
async def api_auth_logout(request: Request):
    security.verify_csrf(request)
    user = security.get_current_user(request)
    sess = security.get_current_session(request)
    if sess:
        session_id = sess["sid"]
        await asyncio.to_thread(storage.delete_session, session_id)
        await account_connections.close_session_connections(
            sess["username"],
            session_id,
            code=SESSION_REVOKED_CLOSE_CODE,
            reason=SESSION_REVOKED_CLOSE_REASON,
        )
    if user:
        audit_request(request, user, "logout", target_type="account", target_id=user["username"])
    response = JSONResponse({"ok": True})
    response.headers["Cache-Control"] = "no-store"
    clear_session_cookie(response)
    return response


@router.post("/logout")
async def logout(request: Request):
    data = await parse_body(request)
    security.verify_csrf_token(request, str(data.get("csrf_token") or request.headers.get("x-csrf-token", "")))
    user = security.get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail=i18n.translate("server.security.login_required"))
    sess = security.get_current_session(request)
    if sess:
        session_id = sess["sid"]
        await asyncio.to_thread(storage.delete_session, session_id)
        await account_connections.close_session_connections(
            sess["username"],
            session_id,
            code=SESSION_REVOKED_CLOSE_CODE,
            reason=SESSION_REVOKED_CLOSE_REASON,
        )
    audit_request(request, user, "logout", target_type="account", target_id=user["username"])
    response = RedirectResponse("/login", status_code=302)
    clear_session_cookie(response)
    return response


@router.get("/api/me")
async def api_me(request: Request):
    user = security.require_user(request)
    return {"user": user_payload(user), "csrf_token": security.get_current_session(request)["csrf_token"]}


@router.get("/api/security/sessions")
async def api_my_login_sessions(request: Request):
    """当前用户自己的登录会话（普通用户自助查看；管理员用 /api/admin/login-sessions）。"""
    user = security.require_user(request)
    current = security.get_current_session(request) or {}
    sessions = await asyncio.to_thread(
        storage.list_login_sessions,
        user["username"],
        current.get("sid"),
    )
    return {
        "sessions": sessions,
        "total": len(sessions),
        "max_per_user": storage.MAX_SESSIONS_PER_USER,
    }


@router.delete("/api/security/sessions/{session_id}")
async def api_revoke_my_login_session(session_id: str, request: Request):
    """结束自己的一条登录会话；如果踢的是当前会话，则同时清掉 Cookie。"""
    security.verify_csrf(request)
    user = security.require_user(request)
    current = security.get_current_session(request) or {}
    current_hash = storage.session_token_hash(current.get("sid"))
    username = await asyncio.to_thread(
        storage.revoke_session_by_hash,
        session_id,
        username=user["username"],
    )
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
        user,
        "login_session_revoked",
        target_type="account",
        target_id=username,
        metadata={"session": session_id[:12], "self": True},
    )
    response = JSONResponse({"ok": True, "current": session_id == current_hash})
    if session_id == current_hash:
        clear_session_cookie(response)
    return response


@router.get("/api/devices")
async def api_devices(request: Request):
    user = security.require_user(request)
    devices = await asyncio.to_thread(
        storage.list_devices_for_user, user["username"], user["role"] == "admin"
    )
    sessions = await manager.snapshot()
    statuses = adb_monitor.snapshot()
    return {
        "devices": devices_payload(devices, sessions, statuses, include_address=False, public_id=True),
        "sessions": sessions_payload(devices, sessions, public_id=True),
    }


@router.get("/api/workbench/snapshot")
async def workbench_snapshot(request: Request):
    """Return one bounded workbench read model instead of several parallel GETs."""
    user = security.require_user(request)
    devices_result = await api_devices(request)
    quality_result = await api_video_preferences(request)
    device_ref = str(request.query_params.get("device_id") or "").strip()
    selected_device_id = alas_device_for_user(user, device_ref) if device_ref else None
    alas_result = {
        "ok": False,
        "configured": False,
        "status": "not_selected" if not selected_device_id else "unbound",
        "config": "",
        "can_run": False,
        "can_edit": False,
        "checked_at": int(time.time()),
    }
    if selected_device_id:
        requested_config = str(request.query_params.get("config") or "").strip() or None
        binding = alas_binding_for_user(
            user,
            allow_admin_global=user.get("role") == "admin",
            config_name=requested_config,
            device_id=selected_device_id,
        )
        if binding:
            alas_result = await cached_public_alas_status(
                user,
                binding,
                selected_device_id,
                runtime=runtime_for(request),
            )
        else:
            alas_result["error"] = i18n.translate("server.error.alas_config_not_bound")
    admin_devices = []
    admin_sessions = []
    admin_alas_assignments = []
    if user.get("role") == "admin":
        all_devices = await asyncio.to_thread(storage.list_all_devices)
        sessions = await manager.snapshot()
        admin_devices = devices_payload(all_devices, sessions, adb_monitor.snapshot())
        device_names = {str(row["id"]): str(row.get("name") or row["id"]) for row in all_devices}
        for row in await manager.admin_client_snapshot():
            internal_id = str(row.get("device_id") or "")
            admin_sessions.append(
                {
                    **row,
                    "device_id": storage.public_device_id(internal_id),
                    "device_name": device_names.get(internal_id, internal_id),
                }
            )
        admin_alas_assignments = public_admin_alas_bindings(
            await asyncio.to_thread(storage.list_user_alas_bindings)
        )
    return {
        "user": user_payload(user),
        "devices": devices_result.get("devices", []),
        "sessions": devices_result.get("sessions", {}),
        "selected_device_id": storage.public_device_id(selected_device_id) if selected_device_id else "",
        "alas": alas_result,
        # Non-sensitive presentation preference.  It does not enable/disable
        # ALAS or alter the user's binding; it only controls workbench chrome.
        # 两个来源取交集：后台的全局开关（ALAS 管理 → 工作台显示）与用户列表里
        # 该账号自己的「显示 ALAS」。
        "workbench_alas_visible": bool(
            str(await asyncio.to_thread(storage.get_setting, "workbench_alas_visible", "true")).strip().lower()
            in ("1", "true", "yes", "on")
        )
        and bool(user.get("alas_visible", 1)),
        # 投屏管理：按当前用户角色下发的工作台功能开关与底部菜单编排（缺省 = 默认）。
        # 只影响前端可见性，不参与任何权限判定。
        "workbench_features": workbench_features.switches_for_role(
            await asyncio.to_thread(storage.get_setting, workbench_features.SETTING_KEY, ""),
            user.get("role"),
        ),
        "workbench_layout": workbench_features.layout_for_role(
            await asyncio.to_thread(storage.get_setting, workbench_features.LAYOUT_KEY, ""),
            user.get("role"),
            stored_switches=await asyncio.to_thread(storage.get_setting, workbench_features.SETTING_KEY, ""),
        ),
        "admin_devices": admin_devices,
        "admin_sessions": admin_sessions,
        "alas_assignments": admin_alas_assignments,
        "quality": quality_result,
        "updated_at": int(time.time()),
    }


@router.get("/api/video/preferences")
async def api_video_preferences(request: Request):
    user = security.require_user(request)
    settings = await asyncio.to_thread(storage.get_settings)
    viewer_stop = storage.viewer_stop_settings(settings)
    defaults = default_video_options()
    stored = await asyncio.to_thread(storage.get_user_video_preference, user["username"])
    effective, resolution = user_video_fallback(user["username"], settings, stored)
    max_size_limit = max_size_limit_value(settings.get("video_max_size_limit"))
    enabled_presets = enabled_profile_ids(settings)
    all_profiles = video_profiles(settings)
    enabled_profiles = {
        name: values for name, values in all_profiles.items() if name in enabled_presets
    }
    profiles = {
        name: values
        for name, values in enabled_profiles.items()
        if not bool(values.get("fullscreen_only", False))
    }
    profiles = clamp_profiles(profiles, max_size_limit)
    labels = profile_label_payloads(settings)
    labels = {name: labels[name] for name in profiles if name in labels}
    fullscreen_profile = fullscreen_profile_value(
        settings.get("video_fullscreen_profile"), enabled_profiles, max_size_limit=max_size_limit
    )
    effective_profile = str(effective.get("profile") or "")
    selected_preset_id = effective_profile if effective_profile in profiles else ""
    limits = dict(VIDEO_LIMITS)
    limits["max_size"] = (limits["max_size"][0], max_size_limit)
    return {
        "defaults": public_video_options(defaults),
        # Return the repaired value so a stale disabled/deleted preset is not
        # written back by a browser that simply refreshes the workbench.
        "preferences": public_video_options(effective),
        "has_user_preference": stored is not None,
        "effective": public_video_options(effective),
        "profiles": profiles,
        "profile_labels": labels,
        "fullscreen_profile": fullscreen_profile,
        "fullscreen_min_max_size": FULLSCREEN_MIN_MAX_SIZE,
        "video_mode": "normal",
        "limits": limits,
        # 工作台「微调」的输出尺寸档位与后台同源，同样受分辨率上限约束。
        "resolution_cap_options": [
            {"value": value, "label": label, "description": description}
            for value, label, description in RESOLUTION_CAP_OPTIONS
        ],
        "stream_modes": ["raw", "protocol", "legacy"],
        "enabled_stream_modes": list(enabled_stream_modes_value(settings.get("scrcpy_enabled_stream_modes", "raw"))),
        "enabled_presets": list(enabled_presets),
        "max_size_limit": max_size_limit,
        "bandwidth_preset": bandwidth_preset_value(settings.get("video_bandwidth_profile") or settings.get("video_bandwidth_preset")),
        "bandwidth_profile": bandwidth_profile_payload(
            settings.get("video_bandwidth_profile") or settings.get("video_bandwidth_preset")
        ),
        "bandwidth_profile_id": bandwidth_profile_value(
            settings.get("video_bandwidth_profile") or settings.get("video_bandwidth_preset")
        ),
        "bandwidth_presets": [
            {"id": f"h264:{key}", "key": key, "codec": "h264", "tier": key,
             "label": i18n.translate(f"server.bandwidth.{key}.label"),
             "description": i18n.translate(f"server.bandwidth.{key}.description")}
            for key in BANDWIDTH_PRESET_KEYS
        ],
        "allow_custom_tuning": parse_bool(settings.get("video_user_custom_tuning"), False),
        "auto_stop_minutes": await asyncio.to_thread(auto_stop_minutes),
        **viewer_stop,
        "preset_catalog": preset_catalog(
            settings,
            max_size_limit=max_size_limit,
            allowed_profiles=set(all_profiles),
        ),
        "selected_preset_id": selected_preset_id,
        "selected_preset_name": labels.get(selected_preset_id, "") if selected_preset_id else "",
        "default_preset": resolution["default_preset_id"],
        "default_preset_id": resolution["default_preset_id"],
        "configured_default_preset": resolution["configured_default_preset_id"],
        "configured_default_preset_id": resolution["configured_default_preset_id"],
        "effective_preset_id": resolution["effective_preset_id"],
        "requested_preset_id": resolution["requested_preset_id"],
        "fallback_reason": resolution["fallback_reason"],
        "using_emergency_preset": bool(resolution["using_emergency"]),
        "emergency_preset": emergency_profile_payload(max_size_limit),
    }


@router.put("/api/video/preferences")
async def api_save_video_preferences(request: Request):
    security.verify_csrf(request)
    user = security.require_user(request)
    payload = await parse_body(request)
    if "adaptive" in payload:
        # 布尔字段严格解析，避免拼写错误被静默当成 False。
        try:
            payload["adaptive"] = parse_bool_strict(payload["adaptive"])
        except InvalidBooleanValue as exc:
            raise HTTPException(status_code=400, detail=i18n.translate("server.error.invalid_boolean")) from exc
    try:
        options = await asyncio.to_thread(user_video_options, user["username"], payload)
    except VideoOptionError as exc:
        raise HTTPException(status_code=400, detail=video_option_error_message(exc)) from exc
    await asyncio.to_thread(storage.set_user_video_preference, user["username"], options)
    audit_request(
        request,
        user,
        "video_preference_save",
        target_type="account",
        target_id=user["username"],
        metadata={"video": public_video_options(options)},
    )
    settings = await asyncio.to_thread(storage.get_settings)
    _fallback, resolution = user_video_fallback(user["username"], settings, options)
    return {
        "ok": True,
        "preferences": public_video_options(options),
        "effective": public_video_options(options),
        "video_mode": "normal",
        "effective_preset_id": resolution["effective_preset_id"],
        "default_preset": resolution["default_preset_id"],
        "default_preset_id": resolution["default_preset_id"],
        "fallback_reason": resolution["fallback_reason"],
        "using_emergency_preset": bool(resolution["using_emergency"]),
    }


@router.put("/api/account/password")
async def api_change_password(request: Request):
    security.verify_csrf(request)
    user = security.require_user(request)
    sess = security.get_current_session(request)
    payload = await parse_body(request)
    current_password = str(payload.get("current_password", ""))
    new_password = str(payload.get("new_password", ""))
    confirm_password = str(payload.get("confirm_password", ""))
    if new_password != confirm_password:
        raise HTTPException(status_code=400, detail=i18n.translate("server.error.passwords_mismatch"))
    try:
        await asyncio.to_thread(storage.change_user_password, user["username"], current_password, new_password)
    except ValueError as exc:
        # ISSUE-066：校验当前密码失败也计入登录失败计数并写审计，堵住持会话者
        # 无限次试错当前密码的口子（下一次登录同样会要求验证码）。
        if str(exc) == "current_password_invalid":
            security.record_login_failure(request, user["username"])
            audit_request(
                request,
                user,
                "password_check_failed",
                outcome="denied",
                reason="current_password_invalid",
                severity="warning",
                target_type="account",
                target_id=user["username"],
            )
        raise HTTPException(status_code=400, detail=public_error_detail(exc)) from exc
    removed = await asyncio.to_thread(
        storage.delete_other_sessions, user["username"], sess["sid"] if sess else None
    )
    await account_connections.close_user_connections(
        user["username"],
        code=SESSION_REVOKED_CLOSE_CODE,
        reason=SESSION_REVOKED_CLOSE_REASON,
    )
    audit_request(
        request,
        user,
        "password_change",
        target_type="account",
        target_id=user["username"],
        metadata={"other_sessions_removed": removed},
    )
    return {"ok": True, "other_sessions_removed": removed}


from . import alas as alas_routes  # noqa: E402  (compatibility view after route declarations)
from . import mirror as mirror_routes  # noqa: E402

# Preserve the phase-two registration order exactly: Gateway follows health,
# while ordinary ALAS APIs remain after the mirror routes in application.py.
_source_router = router
router = APIRouter()
for _route in _source_router.routes:
    router.routes.append(_route)
    if getattr(_route, "path", "") == "/healthz":
        router.routes.extend(alas_routes.gateway_router.routes)

gateway_router = alas_routes.gateway_router
alas_router = alas_routes.router
mirror_router = mirror_routes.router
alas_gateway_exchange = alas_routes.alas_gateway_exchange
api_alas_status = alas_routes.api_alas_status
api_alas_configs = alas_routes.api_alas_configs
api_alas_toggle = alas_routes.api_alas_toggle
api_alas_exit_guard = alas_routes.api_alas_exit_guard
api_save_alas_exit_guard = alas_routes.api_save_alas_exit_guard

all_router = APIRouter()
all_router.routes.extend(router.routes)
all_router.routes.extend(mirror_router.routes)
all_router.routes.extend(alas_router.routes)
