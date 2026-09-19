"""地域限制（GEO）管理接口：状态、立即检查更新、启用前预演。

设置本身走 `/api/admin/settings`（geo_* 字段已登记，含 csrf 与审计），这里只放
「读状态、触发更新、预演」三类动作。

约束：

* 状态与错误信息一律经过 `geo_updater.redact()`，**永不**返回 License Key 或带 key 的 URL；
* 立即更新有去重与限频（服务端判断，不依赖前端），并写一条审计事件；
* 预演（simulate）是只读的：回答「某个地址在当前/拟定策略下会被怎样处理」，
  用于启用 enforce 之前评估影响（含内网地址这类无法定位的来源）。
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from .. import geo_access, geo_updater, i18n, security
from ..http_helpers import parse_body
from ..services.audit_service import audit_request

router = APIRouter()

# 与 geo_access / geo_updater 的错误码对应到 HTTP 状态：
# 缺密钥与限频是「你现在的状态不允许」→ 409；网络类问题 → 502；其余 → 400。
_UPDATE_STATUS_BY_CODE = {
    "update_in_progress": 409,
    "credentials_managed": 409,
    "credentials_write_failed": 503,
    "credentials_unreadable": 409,
    "license_key_missing": 409,
    "account_id_missing": 409,
    "updater_disabled": 409,
    "state_read_failed": 503,
    "state_persist_failed": 503,
    "license_rejected": 409,
    "license_forbidden": 409,
    "too_soon": 429,
    "daily_limit": 429,
    "network_error": 502,
    "timeout": 502,
    "http_401": 502,
    "http_403": 502,
    "directory_unwritable": 500,
}


def _update_error_response(exc: geo_updater.GeoUpdateError) -> HTTPException:
    status_code = _UPDATE_STATUS_BY_CODE.get(exc.code, 400)
    detail = i18n.translate("server.error.geo_update_failed")
    return HTTPException(
        status_code=status_code,
        detail=detail,
        headers={"X-Geo-Update-Error": exc.code},
    )


@router.get("/api/admin/geo/status")
async def admin_geo_status(request: Request):
    """地域策略 + 库状态 + 更新器状态（不含任何凭据）。"""
    security.require_admin(request)
    payload = await asyncio.to_thread(geo_updater.status)
    payload["simulation_supported"] = True
    payload["max_allowed_countries"] = geo_access.MAX_ALLOWED_COUNTRIES
    payload["max_allow_cidrs"] = geo_access.MAX_ALLOW_CIDRS
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})


@router.put("/api/admin/geo/credentials")
async def admin_geo_credentials(request: Request):
    """Save credentials without readback; blank fields keep saved values."""
    security.verify_csrf(request)
    admin = security.require_admin(request)
    body = await parse_body(request)
    if not isinstance(body, dict) or set(body) - {"account_id", "license_key"}:
        raise HTTPException(status_code=400, detail="Invalid credential fields")
    try:
        result = await asyncio.to_thread(geo_updater.configure_credentials,
                                        body.get("account_id", ""), body.get("license_key", ""))
    except geo_updater.GeoUpdateError as exc:
        audit_request(request, admin, "geo_credentials_update", outcome="failure", reason=exc.code)
        raise _update_error_response(exc) from None
    audit_request(request, admin, "geo_credentials_update", target_type="geo_database")
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.delete("/api/admin/geo/credentials")
async def admin_geo_credentials_clear(request: Request):
    security.verify_csrf(request)
    admin = security.require_admin(request)
    try:
        result = await asyncio.to_thread(geo_updater.configure_credentials, clear=True)
    except geo_updater.GeoUpdateError as exc:
        audit_request(request, admin, "geo_credentials_clear", outcome="failure", reason=exc.code)
        raise _update_error_response(exc) from None
    audit_request(request, admin, "geo_credentials_clear", target_type="geo_database")
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/api/admin/geo/check")
async def admin_geo_check(request: Request):
    """立即检查并更新库（服务端限频；失败返回稳定错误码）。"""
    security.verify_csrf(request)
    admin = security.require_admin(request)
    try:
        result = await asyncio.to_thread(geo_updater.enqueue_update, trigger="manual", actor=str(admin.get("username") or ""))
    except geo_updater.GeoUpdateError as exc:
        audit_request(
            request,
            admin,
            "geo_database_update_requested",
            target_type="geo_database",
            target_id=geo_updater.EDITION,
            outcome="failure",
            reason=exc.code,
        )
        raise _update_error_response(exc) from None
    audit_request(
        request,
        admin,
        "geo_database_update_requested",
        target_type="geo_database",
        target_id=geo_updater.EDITION,
        metadata={
            "trigger": "manual",
            "database_epoch": int(result.get("database_epoch") or 0),
            "size_bytes": int(result.get("database_size_bytes") or 0),
        },
    )
    return JSONResponse({"ok": True, "status": result}, status_code=202, headers={"Cache-Control": "no-store"})


@router.get("/api/admin/geo/simulate")
async def admin_geo_simulate(
    request: Request,
    ip: str = Query(default="", max_length=64),
    mode: str = Query(default="", max_length=16),
    countries: str = Query(default="", max_length=600),
    unknown_action: str = Query(default="", max_length=16),
    allow_cidrs: str = Query(default="", max_length=2000),
):
    """预演：给定地址在当前策略（或拟定策略）下的处理结果。

    管理员还可以传 `ip=auto` 来预演**自己当前的来源地址**——这正是启用 enforce 前
    最该先看的一行（否则很容易把自己关在门外）。
    """
    security.require_admin(request)
    target = str(ip or "").strip()
    if target.lower() in ("auto", "self", "me", ""):
        target = security.client_ip(request)
    overrides = {
        "mode": mode if "mode" in request.query_params else None,
        "allowed_countries": countries if "countries" in request.query_params else None,
        "unknown_action": unknown_action if "unknown_action" in request.query_params else None,
        "allow_cidrs": allow_cidrs if "allow_cidrs" in request.query_params else None,
    }
    use_overrides = any(value is not None for value in overrides.values())
    payload = await asyncio.to_thread(
        geo_access.provisional if use_overrides else geo_access.simulate,
        target,
        **({k: v for k, v in overrides.items() if v is not None} if use_overrides else {}),
    )
    target_norm = geo_access.classify_ip(target)[1]
    payload["self"] = bool(target_norm) and target_norm == geo_access.classify_ip(security.client_ip(request))[1]
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})


@router.post("/api/admin/geo/preview")
async def admin_geo_preview(request: Request):
    """按请求体里的拟定设置预演（界面在保存前用；等价于 simulate 的 POST 形式）。"""
    security.verify_csrf(request)
    security.require_admin(request)
    payload = await parse_body(request)
    payload = payload if isinstance(payload, dict) else {}
    target = str(payload.get("ip") or "auto").strip() or "auto"
    if target.lower() in ("auto", "self", "me"):
        target = security.client_ip(request)
    overrides = {
        "mode": payload.get("mode", payload.get("geoMode")),
        "allowed_countries": payload.get("countries", payload.get("geoAllowedCountries")),
        "unknown_action": payload.get("unknownAction", payload.get("unknown_action", payload.get("geoUnknownAction"))),
        "allow_cidrs": payload.get("allowCidrs", payload.get("allow_cidrs", payload.get("geoAllowCidrs"))),
    }
    use_overrides = any(value is not None for value in overrides.values())
    result = await asyncio.to_thread(
        geo_access.provisional if use_overrides else geo_access.simulate,
        target,
        **({k: v for k, v in overrides.items() if v is not None} if use_overrides else {}),
    )
    return JSONResponse(
        {"ok": True, "simulation": result, "license_key_present": geo_updater.license_key_present()},
        headers={"Cache-Control": "no-store"},
    )


__all__ = ["router"]
