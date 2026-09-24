"""Administrator ALAS configuration and permission routes."""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, Request

from .. import alas, alas_visibility, i18n, security, storage
from ..booleans import InvalidBooleanValue
from ..http_helpers import parse_body, parse_bool, public_error_detail
from ..runtime import runtime_for
from ..services.alas_service import (
    admin_alas_payload,
    admin_alas_permissions_payload,
    auto_bind_admin_configs,
    bound_alas_config_names,
    clear_alas_status_cache,
    public_config_matches,
    runtime_catalog,
)
from ..services.audit_service import audit_request
from ..services.mirror_service import resolve_device_or_404
from ..services.notification_service import notify_permission_changed

log = logging.getLogger("webscrcpy.main")
router = APIRouter()


def _public_alas_result(result: object) -> object:
    """Remove upstream diagnostics before returning an ALAS management result."""
    if not isinstance(result, dict):
        return result
    cleaned = dict(result)
    if cleaned.get("error"):
        cleaned["error"] = public_error_detail(cleaned["error"])
    nested = cleaned.get("alas")
    if isinstance(nested, dict) and nested.get("error"):
        cleaned["alas"] = {**nested, "error": public_error_detail(nested["error"])}
    return cleaned


@router.get("/api/admin/alas")
async def admin_alas(request: Request):
    security.require_admin(request)
    config_name = request.query_params.get("config")
    # 传入 runtime：Runtime 状态与配置目录走短 TTL 缓存 + 并发查询（管理页最慢的一条路径）。
    runtime = runtime_for(request)
    if request.query_params.get("refresh") == "1":
        clear_alas_status_cache(runtime)
        return await asyncio.to_thread(admin_alas_payload, config_name, None)
    return await asyncio.to_thread(admin_alas_payload, config_name, runtime)


@router.put("/api/admin/alas")
async def admin_save_alas(request: Request):
    security.verify_csrf(request)
    admin = security.require_admin(request)
    payload = await parse_body(request)
    try:
        await asyncio.to_thread(alas.save_settings, payload)
    except InvalidBooleanValue:
        raise HTTPException(
            status_code=400,
            detail=i18n.translate("server.error.invalid_setting_value"),
        ) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=public_error_detail(exc)) from exc
    clear_alas_status_cache(runtime_for(request))
    audit_request(
        request,
        admin,
        "alas_settings",
        target_type="system_settings",
        target_id="alas",
        metadata={
            "changed_fields": sorted(
                str(key)
                for key in payload
                if str(key).lower() not in {"token", "api_token"}
            )[:32],
            "token_updated": bool(str(payload.get("api_token") or payload.get("token") or "").strip())
            or bool(payload.get("clear_token")),
        },
    )
    return {"ok": True, **await asyncio.to_thread(admin_alas_payload, None, runtime_for(request))}


@router.post("/api/admin/alas/check")
async def admin_alas_check(request: Request):
    security.verify_csrf(request)
    admin = security.require_admin(request)
    payload = await parse_body(request)
    base_url = str(payload.get("base_url") or payload.get("runtime_url") or "").strip()
    result = await asyncio.to_thread(alas.check_connection, base_url or None)
    audit_request(
        request,
        admin,
        "alas_connection_check",
        outcome="success" if result.get("ok") else "failure",
        reason=str(result.get("state") or ""),
        severity="info" if result.get("ok") else "warning",
        target_type="alas_runtime",
        target_id="connection",
        metadata={"state": result.get("state"), "status_code": result.get("status_code")},
    )
    return result


@router.post("/api/admin/alas/toggle")
async def admin_toggle_alas(request: Request):
    security.verify_csrf(request)
    admin = security.require_admin(request)
    payload = await parse_body(request)
    config_name = str(payload.get("config_name") or payload.get("config") or "").strip()
    action = str(payload.get("action") or "toggle").strip().lower()
    if action not in {"toggle", "start", "stop", "restart"}:
        raise HTTPException(status_code=400, detail="invalid ALAS action")
    if not config_name:
        raise HTTPException(status_code=400, detail=i18n.translate("server.error.alas_config_name_required"))
    try:
        config_name = alas.sanitize_config_name(config_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=i18n.translate("server.error.invalid_alas_config_name")) from exc
    result = await asyncio.to_thread(alas.control_for_config, action, config_name)
    clear_alas_status_cache(runtime_for(request))
    audit_request(
        request,
        admin,
        "alas_admin_toggle",
        outcome="success" if result.get("ok") else "failure",
        reason="" if result.get("ok") else "runtime_operation_failed",
        severity="info" if result.get("ok") else "error",
        target_type="alas_config",
        target_id=config_name,
        metadata={"action": result.get("action"), "status_code": result.get("status_code")},
    )
    if result.get("error"):
        result["error"] = public_error_detail(result["error"])
    if isinstance(result.get("alas"), dict) and result["alas"].get("error"):
        result["alas"]["error"] = public_error_detail(result["alas"]["error"])
    return result


@router.get("/api/admin/alas/config")
async def admin_alas_config(request: Request):
    security.require_admin(request)
    config_name = str(request.query_params.get("config") or "").strip()
    if not config_name:
        raise HTTPException(status_code=400, detail=i18n.translate("server.error.alas_config_name_required"))
    try:
        config_name = alas.sanitize_config_name(config_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=i18n.translate("server.error.invalid_alas_config_name")) from exc
    result = await asyncio.to_thread(alas.get_config, config_name)
    return _public_alas_result(result)


@router.post("/api/admin/alas/auto-bind")
async def admin_alas_auto_bind(request: Request):
    """按 ALAS 配置里的模拟器 ADB 地址，补齐全缺失的管理员配置关联。

    打开 ALAS 管理页时调用一次：只认完全一致的 `host:port`，回环地址跳过，
    只补当前管理员缺失的绑定（手工绑定优先，绝不覆盖），普通用户的数据不动。
    """
    security.verify_csrf(request)
    admin = security.require_admin(request)
    username = str(admin.get("username") or "")
    result = await asyncio.to_thread(auto_bind_admin_configs, username, runtime_for(request))
    created = list(result.get("created") or [])
    skipped = list(result.get("skipped") or [])
    audit_request(
        request,
        admin,
        "alas_auto_bind",
        outcome="success" if result.get("ok") else "failure",
        reason="" if result.get("ok") else str(result.get("error") or "auto_bind_failed"),
        severity="info" if result.get("ok") else "warning",
        target_type="alas_config",
        target_id=username,
        # 只记条数与配置名，不记设备地址（日志里的地址另有 <adb-endpoint> 脱敏）。
        metadata={
            "configs": int(result.get("configs") or 0),
            "devices": int(result.get("devices") or 0),
            "created": [str(item.get("config_name") or "") for item in created],
            "skipped": len(skipped),
        },
    )
    return result


@router.get("/api/admin/alas/config-matches")
async def admin_alas_config_matches(request: Request):
    """只读：Runtime 配置里的模拟器 ADB 地址 ↔ 本机设备的配对结果。

    用户与权限页用它做「选设备自动带出配置 / 选配置自动带出设备」。与自动绑定共用
    同一份判定代码（`match_runtime_configs_to_devices`），且只返回公开设备 id。
    """
    security.require_admin(request)
    return await asyncio.to_thread(public_config_matches, runtime_for(request))


@router.get("/api/admin/alas/configs")
async def admin_alas_configs(request: Request):
    security.require_admin(request)
    bound_configs = await asyncio.to_thread(bound_alas_config_names)
    settings = await asyncio.to_thread(alas.public_settings)
    runtime_configs: list[str] = []
    error = ""
    if settings.get("enabled") and settings.get("token_set"):
        try:
            # 走短 TTL 缓存：管理页首屏刚拉过配置目录时这里直接命中。
            result = await asyncio.to_thread(runtime_catalog, runtime_for(request))
            error = public_error_detail(result.get("error"))
            for raw in result.get("configs") or []:
                try:
                    name = alas.sanitize_config_name(raw)
                except ValueError:
                    continue
                if name not in runtime_configs:
                    runtime_configs.append(name)
        except Exception as exc:
            log.warning("ALAS_CONFIG_CATALOG_FAILED error_type=%s", type(exc).__name__)
            error = public_error_detail(exc)
    configs = list(bound_configs)
    for name in runtime_configs:
        if name not in configs:
            configs.append(name)
    return {
        "configs": configs,
        "runtime_configs": runtime_configs,
        "bound_configs": bound_configs,
        "error": error,
    }


@router.put("/api/admin/alas/config")
async def admin_save_alas_config(request: Request):
    security.verify_csrf(request)
    admin = security.require_admin(request)
    payload = await parse_body(request)
    source = str(payload.get("source", ""))
    target = str(payload.get("target") or source)
    if not source.strip() or not target.strip():
        raise HTTPException(status_code=400, detail=i18n.translate("server.error.alas_config_name_required"))
    try:
        source = alas.sanitize_config_name(source)
        target = alas.sanitize_config_name(target)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=i18n.translate("server.error.invalid_alas_config_name")) from exc
    data = payload.get("data")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=i18n.translate("server.error.config_valid_json")) from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail=i18n.translate("server.error.config_json_object"))
    result = await asyncio.to_thread(alas.save_config, source, target, data, False)
    # 保存配置可能改名/新增，缓存里的配置目录与状态随即过期。
    clear_alas_status_cache(runtime_for(request))
    audit_request(
        request,
        admin,
        "alas_config_save",
        outcome="success" if result.get("ok", True) else "failure",
        reason="" if result.get("ok", True) else "runtime_operation_failed",
        severity="info" if result.get("ok", True) else "error",
        target_type="alas_config",
        target_id=target or source,
    )
    return _public_alas_result(result)


@router.get("/api/admin/alas/permissions")
async def admin_alas_permissions(request: Request):
    security.require_admin(request)
    return await asyncio.to_thread(admin_alas_permissions_payload)


@router.put("/api/admin/alas/permissions")
async def admin_set_alas_permission(request: Request):
    security.verify_csrf(request)
    admin = security.require_admin(request)
    payload = await parse_body(request)
    return await asyncio.to_thread(_set_alas_permission_sync, request, admin, payload)


def _set_alas_permission_sync(request: Request, admin: dict, payload: dict):
    username = str(payload.get("username", "")).strip()
    config_name = str(payload.get("config_name", "")).strip()
    multi_config_request = "is_default" in payload
    device_context_submitted = "device_id" in payload
    requested_device_ref = str(payload.get("device_id") or "").strip()
    device_id = resolve_device_or_404(requested_device_ref) if requested_device_ref else None
    enabled = parse_bool(payload.get("enabled"), bool(config_name))
    if not storage.get_user(username):
        raise HTTPException(status_code=400, detail=i18n.translate("server.error.invalid_username"))
    if config_name:
        try:
            config_name = alas.sanitize_config_name(config_name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=i18n.translate("server.error.invalid_alas_config_name")) from exc
    if not enabled:
        delete_all = parse_bool(payload.get("delete_all"), False)
        if config_name:
            storage.delete_user_alas_binding(username, config_name)
            detail = f"{username}:{config_name}"
        elif delete_all:
            storage.delete_user_alas_config(username)
            detail = username
        else:
            raise HTTPException(status_code=400, detail=i18n.translate("server.error.alas_config_name_required"))
        audit_request(
            request,
            admin,
            "alas_binding_delete",
            target_type="alas_binding",
            target_id=detail,
            metadata={"username": username, "config_name": config_name},
        )
        # 归属变化后读模型里的 config_statuses / bound_configs 立刻过期。
        clear_alas_status_cache(runtime_for(request))
        notify_permission_changed(
            username,
            scope="alas",
            actor=str(admin["username"]),
            detail=config_name or "全部配置",
            revoked=True,
        )
        return {"ok": True, **admin_alas_permissions_payload()}
    if not config_name:
        raise HTTPException(status_code=400, detail=i18n.translate("server.error.alas_config_name_required"))
    can_run = parse_bool(payload.get("can_run"), True)
    can_edit = parse_bool(payload.get("can_edit"), False)
    grant_view = parse_bool(payload.get("grant_view"), False)
    raw_default = payload.get("is_default")
    is_default = None if raw_default is None else parse_bool(raw_default, False)
    selected_device_id = device_id
    # 「把配置改绑到另一台设备」是移动而不是新增：前端文案承诺不会静默覆盖，
    # 所以没有显式确认时先用 409 把当前/目标设备报回去，由界面二次确认。
    try:
        existing_binding = storage.get_user_alas_binding(username, config_name)
    except Exception:
        existing_binding = None
    if device_context_submitted and existing_binding:
        current_device_id = str(existing_binding.get("device_id") or "").strip()
        target_device_id = str(device_id or "").strip()
        if current_device_id and target_device_id and current_device_id != target_device_id:
            if not parse_bool(payload.get("confirm_move"), False):
                # 提示里用设备名而不是内部 id：管理员看到的是设备列表里的名字。
                def _device_label(device_ref: str) -> str:
                    row = storage.get_device(device_ref)
                    name = str((row["name"] if row else "") or "").strip()
                    return name or storage.public_device_id(device_ref)

                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "alas_binding_move_confirm",
                        "message": i18n.translate(
                            "server.error.alas_binding_move_confirm",
                            config=config_name,
                            device=_device_label(current_device_id),
                            target=_device_label(target_device_id),
                        ),
                        "config_name": config_name,
                        "device_id": storage.public_device_id(current_device_id),
                        "target_device_id": storage.public_device_id(target_device_id),
                    },
                )
    try:
        if multi_config_request:
            if device_context_submitted:
                storage.upsert_user_alas_binding_with_view(
                    username,
                    config_name,
                    can_run,
                    can_edit,
                    is_default,
                    device_id,
                    grant_view=grant_view,
                )
            else:
                storage.upsert_user_alas_binding(username, config_name, can_run, can_edit, is_default)
        else:
            if device_context_submitted:
                # Device-scoped bindings use the same atomic permission path
                # as the multi-config UI, even for legacy callers that omit
                # ``is_default``.  This prevents an ALAS binding from being
                # created without the required device view grant.
                storage.upsert_user_alas_binding_with_view(
                    username,
                    config_name,
                    can_run,
                    can_edit,
                    None,
                    device_id,
                    grant_view=grant_view,
                )
            else:
                existing = storage.get_user_alas_binding(username, config_name)
                selected_device_id = (
                    str(existing.get("device_id") or "").strip() or None
                    if existing
                    else None
                )
                storage.set_user_alas_config(
                    username,
                    config_name,
                    can_run,
                    can_edit,
                    selected_device_id,
                )
    except storage.AlasConfigOwnershipError as exc:
        detail = i18n.translate("server.error.alas_config_owned", config=exc.config_name, owner=exc.owner)
        raise HTTPException(status_code=409, detail=detail) from exc
    except ValueError as exc:
        status_code = 409 if str(exc) == "device_view_permission_required" else 400
        raise HTTPException(status_code=status_code, detail=public_error_detail(exc)) from exc
    audit_request(
        request,
        admin,
        "alas_binding_set",
        target_type="alas_binding",
        target_id=f"{username}:{config_name}",
        metadata={
            "username": username,
            "device_id": storage.public_device_id(selected_device_id) if selected_device_id else "",
            "can_run": can_run,
            "can_edit": can_edit,
            "is_default": bool(is_default),
        },
    )
    clear_alas_status_cache(runtime_for(request))
    notify_permission_changed(
        username,
        scope="alas",
        actor=str(admin["username"]),
        detail=f"{config_name}（{'可运行' if can_run else '不可运行'}{'，可编辑' if can_edit else ''}）",
        revoked=not can_run and not can_edit,
    )
    return {"ok": True, **admin_alas_permissions_payload()}

def feature_payload(groups: list[dict]) -> dict:
    """固定目录 + 启停状态的分组视图。"""
    return {
        "groups": groups,
        "total": sum(len(group["items"]) for group in groups),
        "enabled_count": sum(group["enabled_count"] for group in groups),
    }


@router.get("/api/admin/alas/features")
async def admin_alas_features(request: Request):
    """ALAS 游戏任务的隐藏清单（**已写死**：只读展示，不再有开关）。"""
    security.require_admin(request)
    return {**feature_payload(await asyncio.to_thread(alas.feature_switches)), "hardcoded": True}


@router.put("/api/admin/alas/features")
async def admin_save_alas_features(request: Request):
    """隐藏清单已写死：保存接口关闭（前端不再调用，保留路由以给出明确提示）。"""
    security.verify_csrf(request)
    security.require_admin(request)
    raise HTTPException(
        status_code=409,
        detail=i18n.translate("server.error.alas_hidden_config_fixed"),
    )


@router.get("/api/admin/alas/visibility")
async def admin_alas_visibility(request: Request):
    """普通用户看不到的 ALAS 页面/入口（**已写死**：只读展示）。"""
    security.require_admin(request)
    return await asyncio.to_thread(alas_visibility.visibility_snapshot)


@router.put("/api/admin/alas/visibility")
async def admin_save_alas_visibility(request: Request):
    """隐藏清单已写死：保存接口关闭（前端不再调用，保留路由以给出明确提示）。"""
    security.verify_csrf(request)
    security.require_admin(request)
    raise HTTPException(
        status_code=409,
        detail=i18n.translate("server.error.alas_hidden_config_fixed"),
    )


__all__ = [name for name in globals() if name.startswith("admin_")] + ["router"]
