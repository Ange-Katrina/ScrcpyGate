"""User-facing ALAS and origin Gateway HTTP routes."""

from __future__ import annotations

import asyncio
import time
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from .. import alas, alas_gateway, i18n, security, storage
from ..http_helpers import parse_body, public_error_detail
from ..runtime import runtime_for
from ..services.alas_service import (
    admin_runtime_alas_config_names,
    alas_binding_for_user,
    alas_device_for_user,
    clear_alas_status_cache,
    public_alas_status,
    public_user_alas_bindings,
    require_alas_binding,
)
from ..services import alas_exit_guard
from ..services.audit_service import audit_request

router = APIRouter()
gateway_router = APIRouter()


@gateway_router.get("/alas/gateway", response_class=RedirectResponse)
@gateway_router.get("/alas/gateway/", response_class=RedirectResponse)
async def alas_gateway_exchange(request: Request):
    """Redeem a one-time primary-origin ticket into an ALAS-only cookie."""
    if not security.is_alas_origin_request(request) or not alas_gateway.enabled():
        raise HTTPException(status_code=404, detail="Not Found")
    record = alas_gateway.redeem_ticket(request.query_params.get("ticket"))
    if not record:
        raise HTTPException(status_code=401, detail=i18n.translate("server.security.login_required"))
    source_session = await asyncio.to_thread(storage.get_session, record.session_id)
    user = await asyncio.to_thread(storage.get_user, record.username)
    if (
        not source_session
        or source_session.get("username") != record.username
        or not user
        or not storage.user_is_active(user)
    ):
        alas_gateway.revoke_session(record.gateway_id)
        raise HTTPException(status_code=401, detail=i18n.translate("server.security.login_required"))
    if not security.password_change_allowed(dict(user)):
        alas_gateway.revoke_session(record.gateway_id)
        raise HTTPException(status_code=403, detail=i18n.translate("server.security.password_change_required"))
    params: dict[str, str] = {}
    if record.config_name:
        params["config"] = record.config_name
    if record.device_id:
        params["device_id"] = storage.public_device_id(record.device_id)
    params[alas_gateway.ALAS_CONTEXT_QUERY] = record.context_id
    target = "/alas/embed/proxy/"
    if params:
        target = f"{target}?{urlencode(params)}"
    response = RedirectResponse(target, status_code=303)
    max_age = max(1, min(43200, int(record.expires_at - time.time())))
    response.set_cookie(
        alas_gateway.cookie_name(record.context_id),
        record.gateway_id,
        httponly=True,
        secure=security.alas_cookie_secure(),
        samesite="none" if security.alas_cookie_secure() else "lax",
        max_age=max_age,
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@router.get("/api/alas/status")
async def api_alas_status(request: Request):
    user = security.require_user(request)
    requested = str(request.query_params.get("config") or "").strip()
    device_id = await asyncio.to_thread(alas_device_for_user, user, request.query_params.get("device_id"))
    binding = await asyncio.to_thread(
        alas_binding_for_user,
        user,
        allow_admin_global=user.get("role") == "admin",
        config_name=requested or None,
        device_id=device_id,
    )
    if requested and not binding:
        raise HTTPException(status_code=403, detail=i18n.translate("server.error.alas_config_not_bound"))
    if not binding:
        return {
            "ok": False,
            "configured": False,
            "status": "unbound",
            "task": "",
            "config": "",
            "can_run": False,
            "can_edit": False,
            "error": i18n.translate("server.error.alas_config_not_bound"),
        }
    try:
        config_name = alas.sanitize_config_name(binding.get("config_name"))
    except ValueError:
        return {
            "ok": False,
            "configured": False,
            "status": "invalid_config",
            "config": "",
            "can_run": False,
            "can_edit": False,
        }
    result = await asyncio.to_thread(alas.status_for_config, config_name, False)
    binding["config_name"] = config_name
    return public_alas_status(result, binding)


@router.get("/api/alas/configs")
async def api_alas_configs(request: Request):
    user = security.require_user(request)
    device_id = await asyncio.to_thread(alas_device_for_user, user, request.query_params.get("device_id"))
    configs = await asyncio.to_thread(public_user_alas_bindings, user, device_id)
    if user.get("role") == "admin" and device_id is not None and not configs:
        names = await asyncio.to_thread(admin_runtime_alas_config_names)
        if not names:
            names = [alas.legacy_config_name()]
        configs = [
            {
                "config_name": name,
                "device_id": storage.public_device_id(device_id),
                "device_name": "",
                "can_run": True,
                "can_edit": True,
                "is_default": index == 0,
                "admin_fallback": True,
            }
            for index, name in enumerate(names)
        ]
    default_config = next((item["config_name"] for item in configs if item["is_default"]), "")
    if not default_config and len(configs) == 1:
        default_config = configs[0]["config_name"]
    return {"configs": configs, "default_config": default_config}


@router.post("/api/alas/toggle")
async def api_alas_toggle(request: Request):
    security.verify_csrf(request)
    user = security.require_user(request)
    payload = await parse_body(request)
    requested = str(payload.get("config_name") or payload.get("config") or "").strip()
    action = str(payload.get("action") or "toggle").strip().lower()
    if action not in {"toggle", "start", "stop", "restart"}:
        raise HTTPException(status_code=400, detail="invalid ALAS action")
    device_id = await asyncio.to_thread(alas_device_for_user, user, payload.get("device_id"))
    binding = await asyncio.to_thread(
        require_alas_binding, user, config_name=requested or None, device_id=device_id, run=True
    )
    result = await asyncio.to_thread(alas.control_for_config, action, binding["config_name"])
    clear_alas_status_cache(runtime_for(request))
    if not result.get("ok"):
        audit_request(
            request,
            user,
            "alas_toggle",
            outcome="failure",
            reason="runtime_operation_failed",
            severity="error",
            target_type="alas_config",
            target_id=binding["config_name"],
            metadata={"status_code": result.get("status_code")},
        )
        return {
            "ok": False,
            "error": public_error_detail(result.get("error"))
            or i18n.translate("server.status.alas_operation_failed"),
            "status_code": result.get("status_code"),
            "config": binding["config_name"],
        }
    if isinstance(result.get("alas"), dict):
        result["alas"] = public_alas_status(result["alas"], binding)
    audit_request(
        request,
        user,
        "alas_toggle",
        target_type="alas_config",
        target_id=binding["config_name"],
        metadata={"action": result.get("action")},
    )
    return {
        "ok": True,
        "action": result.get("action"),
        "config": binding["config_name"],
        "alas": result.get("alas"),
    }


@router.get("/api/alas/exit-guard")
async def api_alas_exit_guard(request: Request):
    """Per-user 「退出浏览器后自动检测」 switch for the workbench ALAS menu."""
    user = security.require_user(request)
    preference = await asyncio.to_thread(storage.get_user_alas_preference, user["username"])
    return {
        "ok": True,
        "enabled": bool(preference.get("restart_on_exit")),
        "grace_seconds": int(alas_exit_guard.EXIT_GUARD_GRACE_SECONDS),
    }


@router.put("/api/alas/exit-guard")
async def api_save_alas_exit_guard(request: Request):
    security.verify_csrf(request)
    user = security.require_user(request)
    payload = await parse_body(request)
    raw = payload.get("enabled")
    if isinstance(raw, bool):
        enabled = raw
    else:
        text = str(raw).strip().lower()
        if text not in {"true", "false", "1", "0"}:
            raise HTTPException(status_code=400, detail="invalid exit guard flag")
        enabled = text in {"true", "1"}
    preference = await asyncio.to_thread(storage.set_user_alas_restart_on_exit, user["username"], enabled)
    audit_request(
        request,
        user,
        "alas_settings",
        target_type="alas_config",
        target_id=user["username"],
        metadata={"restart_on_exit": bool(preference.get("restart_on_exit"))},
    )
    return {
        "ok": True,
        "enabled": bool(preference.get("restart_on_exit")),
        "grace_seconds": int(alas_exit_guard.EXIT_GUARD_GRACE_SECONDS),
    }


__all__ = [
    "alas_gateway_exchange",
    "api_alas_configs",
    "api_alas_status",
    "api_alas_toggle",
    "api_alas_exit_guard",
    "api_save_alas_exit_guard",
    "gateway_router",
    "router",
]
