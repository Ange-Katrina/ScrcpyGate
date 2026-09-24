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
import os
import tempfile
from pathlib import Path

import anyio

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from .. import geo_access, geo_downloads, geo_updater, i18n, security
from ..http_helpers import parse_body
from ..services.audit_service import audit_request

router = APIRouter()

# 与 geo_access / geo_updater 的错误码对应到 HTTP 状态：
# 缺密钥与限频是「你现在的状态不允许」→ 409；网络类问题 → 502；其余 → 400。
_UPDATE_STATUS_BY_CODE = {
    "update_in_progress": 409,
    "database_in_use": 409,
    "database_not_found": 404,
    "database_path_unsafe": 409,
    "database_restore_failed": 503,
    "update_io_failed": 503,
    "credentials_managed": 409,
    "credentials_write_failed": 503,
    "download_settings_write_failed": 503,
    "download_settings_unreadable": 409,
    "download_settings_permissions": 409,
    "downloads_disabled": 409,
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
    "dns_error": 502,
    "tls_error": 502,
    "connection_refused": 502,
    "proxy_error": 502,
    "download_forbidden": 502,
    "upstream_rate_limited": 502,
    "upstream_unavailable": 502,
    "release_metadata_invalid": 502,
    "http_error": 502,
    "timeout": 502,
    "http_401": 502,
    "http_403": 502,
    "directory_unwritable": 500,
    "download_too_large": 413,
    "upload_timeout": 408,
}


def _update_error_response(exc: geo_updater.GeoUpdateError) -> HTTPException:
    status_code = _UPDATE_STATUS_BY_CODE.get(exc.code, 400)
    detail = i18n.translate("server.error.geo_update_failed")
    headers = {"X-Geo-Update-Error": exc.code}
    if exc.code == "too_soon":
        headers["Retry-After"] = str(max(1, geo_updater.retry_after_seconds()))
    return HTTPException(
        status_code=status_code,
        detail=detail,
        headers=headers,
    )


@router.get("/api/admin/geo/status")
async def admin_geo_status(request: Request):
    """Admin-only status, including the editable saved proxy URL."""
    security.require_admin(request)
    payload = await asyncio.to_thread(geo_updater.status)
    payload["download_settings"] = await asyncio.to_thread(geo_downloads.admin_status)
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


@router.put("/api/admin/geo/schedule")
async def admin_geo_schedule(request: Request):
    security.verify_csrf(request)
    admin = security.require_admin(request)
    body = await parse_body(request)
    if not isinstance(body, dict) or set(body) != {"interval_hours"}:
        raise HTTPException(status_code=400, detail="Invalid schedule fields")
    try:
        result = await asyncio.to_thread(geo_updater.configure_schedule, body["interval_hours"])
    except geo_updater.GeoUpdateError as exc:
        raise _update_error_response(exc) from None
    audit_request(request, admin, "geo_schedule_update", target_type="geo_database", metadata=result)
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.put("/api/admin/geo/downloads")
async def admin_geo_downloads(request: Request):
    security.verify_csrf(request)
    admin = security.require_admin(request)
    body = await parse_body(request)
    if (not isinstance(body, dict) or not {"editions", "proxy_mode"} <= set(body)
            or set(body) - {"source", "editions", "proxy_mode", "proxy_url", "clear_proxy"}):
        raise HTTPException(status_code=400, detail="Invalid download settings")
    try:
        result = await asyncio.to_thread(geo_updater.configure_downloads, body["editions"], body["proxy_mode"],
                                        body.get("proxy_url", ""), clear_proxy=body.get("clear_proxy", False),
                                        source=body.get("source"))
    except geo_updater.GeoUpdateError as exc:
        audit_request(request, admin, "geo_download_settings_update", outcome="failure", reason=exc.code)
        raise _update_error_response(exc) from None
    audit_request(request, admin, "geo_download_settings_update", target_type="geo_database",
                  metadata={"source": result["source"], "editions": result["editions"], "proxy_mode": result["proxy_mode"]})
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
            target_id="selected_geo_databases",
            outcome="failure",
            reason=exc.code,
        )
        raise _update_error_response(exc) from None
    audit_request(
        request,
        admin,
        "geo_database_update_requested",
        target_type="geo_database",
        target_id="selected_geo_databases",
        metadata={
            "trigger": "manual",
            "database_epoch": int(result.get("database_epoch") or 0),
            "size_bytes": int(result.get("database_size_bytes") or 0),
        },
    )
    return JSONResponse({"ok": True, "status": result}, status_code=202, headers={"Cache-Control": "no-store"})


@router.delete("/api/admin/geo/databases/{edition}")
async def admin_geo_database_delete(request: Request, edition: str):
    security.verify_csrf(request)
    admin = security.require_admin(request)
    try:
        result = await asyncio.to_thread(geo_updater.delete_database, edition)
    except geo_updater.GeoUpdateError as exc:
        audit_request(request, admin, "geo_database_delete", target_type="geo_database", target_id=edition[:32],
                      outcome="failure", reason=exc.code)
        raise _update_error_response(exc) from None
    audit_request(request, admin, "geo_database_delete", target_type="geo_database", target_id=edition,
                  metadata={"removed_bytes": result["removed_bytes"], "cleanup_pending": result["cleanup_pending"]})
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/api/admin/geo/upload")
async def admin_geo_upload(request: Request, edition: str = Query(...), format: str = Query("mmdb")):
    """Bounded raw upload: authenticate before reading, ignore user filenames entirely."""
    security.verify_csrf(request)
    admin = security.require_admin(request)
    if edition not in geo_downloads.EDITIONS or format not in ("mmdb", "tar.gz"):
        raise HTTPException(400, "Invalid database edition or format")
    if request.headers.get("content-type", "").split(";")[0] != "application/octet-stream":
        raise HTTPException(415, "Expected application/octet-stream")
    limit = geo_updater.MAX_DOWNLOAD_BYTES if format == "tar.gz" else geo_updater.MAX_DATABASE_BYTES
    try:
        length = int(request.headers.get("content-length", "0"))
    except ValueError:
        raise HTTPException(400, "Invalid Content-Length") from None
    if length < 0 or length > limit:
        raise _update_error_response(geo_updater.GeoUpdateError("download_too_large"))
    if not geo_updater._update_lock.acquire(blocking=False):
        raise _update_error_response(geo_updater.GeoUpdateError("update_in_progress"))
    temp = None
    try:
        directory = geo_access.database_dir()
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".geo-upload-", suffix=".tmp", delete=False) as handle:
            temp = Path(handle.name)
            received = 0
            with anyio.fail_after(600):
                async for chunk in request.stream():
                    received += len(chunk)
                    if received > limit:
                        raise geo_updater.GeoUpdateError("download_too_large")
                    handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        if received == 0:
            raise geo_updater.GeoUpdateError("empty_database")
        if length and received != length:
            raise geo_updater.GeoUpdateError("download_size_mismatch")
        # Shield activation: don't release the shared lock while its worker still runs.
        task = asyncio.create_task(asyncio.to_thread(geo_updater.import_database, temp, edition=edition, archive=format == "tar.gz"))
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
        result = task.result()
        if cancelled:
            raise asyncio.CancelledError
        audit_request(request, admin, "geo_database_import", target_type="geo_database", target_id=edition,
                      metadata={"size_bytes": result["size_bytes"], "unchanged": result["unchanged"]})
        return JSONResponse(result, headers={"Cache-Control": "no-store"})
    except (geo_updater.GeoUpdateError, OSError, TimeoutError) as exc:
        error = exc if isinstance(exc, geo_updater.GeoUpdateError) else geo_updater.GeoUpdateError(
            "upload_timeout" if isinstance(exc, TimeoutError) else "update_io_failed")
        audit_request(request, admin, "geo_database_import", target_type="geo_database", target_id=edition,
                      outcome="failure", reason=error.code)
        raise _update_error_response(error) from None
    finally:
        try:
            if temp is not None:
                temp.unlink(missing_ok=True)
        finally:
            geo_updater._update_lock.release()
            geo_updater._wake_event.set()


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
