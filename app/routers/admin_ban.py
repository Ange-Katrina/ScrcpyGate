"""管理员 IP 封禁接口（BAN）：查询、封禁、解封、事件流。

约定与其它后台写接口一致：管理员会话 + CSRF，写操作写审计事件（``ip_ban_*``）。
判定与清理动作都在 ``app.ip_ban`` 里，本模块只做参数校验、权限与响应组装。

两个必须做对的产品细节：

* **误封提醒**：如果被封的地址就是当前管理员自己的来源地址，响应里带 ``self_ban=True``
  并把提示写进审计；界面据此显示「你把自己封了」的显式警告（否则管理员会一脸茫然）。
* **健康探针不可封**：环回/本机/可信代理地址由 ``ip_ban`` 直接拒绝；``/healthz`` 本身
  在网关上就有豁免，所以即使名单里出现也不会让容器自检失败。
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from .. import i18n, ip_ban, security
from ..http_helpers import parse_body

router = APIRouter()

BAN_LIST_MAX_PAGE = 200
BAN_HTTP_CODE_BY_ERROR = {
    "invalid_ip": 400,
    "invalid_preset": 400,
    "invalid_duration": 400,
    "missing_duration": 400,
    "duration_too_long": 400,
    "protected_ip": 400,
}


def _ban_payload(row: dict) -> dict:
    return {
        "ip": row["ip"],
        "permanent": bool(row["permanent"]),
        "expires_ts": row["expires_ts"],
        "remaining_seconds": row["remaining_seconds"],
        "created_ts": row["created_ts"],
        "updated_ts": row["updated_ts"],
        "reason": row["reason"],
        "actor": row["actor"],
        "active": bool(row["active"]),
        "revoked_ts": row["revoked_ts"],
        "revoked_by": row["revoked_by"],
    }


@router.get("/api/admin/ip-bans")
async def admin_list_ip_bans(
    request: Request,
    include_inactive: int = Query(default=0, ge=0, le=1),
    limit: int = Query(default=50, ge=1, le=BAN_LIST_MAX_PAGE),
    offset: int = Query(default=0, ge=0, le=1_000_000),
    ip: str = "",
):
    """封禁列表 + 计数 + 可用档位；``ip`` 有值时只返回该地址（含历史）。"""
    security.require_admin(request)
    if ip.strip():
        row = await asyncio.to_thread(ip_ban.get_ban, ip)
        items = [_ban_payload(row)] if row else []
        page = {"has_more": False, "offset": 0, "limit": 1}
    else:
        listing = await asyncio.to_thread(
            ip_ban.list_bans, include_inactive=bool(include_inactive), limit=limit, offset=offset
        )
        items = [_ban_payload(row) for row in listing["items"]]
        page = {"has_more": listing["has_more"], "offset": listing["offset"], "limit": listing["limit"]}
    counters = await asyncio.to_thread(ip_ban.counters)
    return JSONResponse(
        {
            "items": items,
            "page": page,
            "counters": counters,
            "presets": [
                {"id": key, "seconds": value} for key, value in ip_ban.BAN_PRESET_SECONDS.items()
            ],
            "max_seconds": ip_ban.BAN_MAX_SECONDS,
            "snapshot_ttl_seconds": ip_ban.SNAPSHOT_TTL_SECONDS,
        },
        headers={"Cache-Control": "no-store"},
    )


@router.post("/api/admin/ip-bans")
async def admin_ban_ip(request: Request):
    """封禁（或改期）一个来源地址；重复封禁＝更新期限并留一条事件。"""
    security.verify_csrf(request)
    admin = security.require_admin(request)
    payload = await parse_body(request)
    payload = payload if isinstance(payload, dict) else {}
    target = str(payload.get("ip") or "").strip()
    reason = str(payload.get("reason") or "").strip()[:200]
    preset = payload.get("preset")
    seconds = payload.get("seconds")
    permanent = payload.get("permanent") is True
    actor = str(admin.get("username") or "admin")
    normalized = ip_ban.normalize_ip(target)
    if normalized and normalized == ip_ban.normalize_ip(security.client_ip(request)) and payload.get("confirmSelfBan") is not True:
        return JSONResponse(
            {"code": "self_ban_confirmation_required", "self_ban": True},
            status_code=409, headers={"Cache-Control": "no-store", "X-Ban-Error": "self_ban_confirmation_required"},
        )
    try:
        row = await asyncio.to_thread(
            ip_ban.ban,
            target,
            seconds=seconds,
            permanent=permanent,
            preset=preset,
            reason=reason,
            actor=actor,
        )
    except ip_ban.BanError as exc:
        status_code = BAN_HTTP_CODE_BY_ERROR.get(exc.code, 400)
        key = "server.error.ip_ban_protected" if exc.code == "protected_ip" else "server.error.ip_ban_failed"
        raise HTTPException(status_code=status_code, detail=i18n.translate(key), headers={"X-Ban-Error": exc.code}) from None
    # 已有的长连接必须立刻断开（否则「封禁」只对新请求生效）。不碰账号、会话与控制权。
    closed = await ip_ban.close_ip_connections(row["ip"])
    self_ban = ip_ban.normalize_ip(security.client_ip(request)) == row["ip"]
    return JSONResponse(
        {
            "ok": True,
            "ban": _ban_payload(row),
            "closed_connections": int(closed),
            "self_ban": bool(self_ban),
            "counters": await asyncio.to_thread(ip_ban.counters),
        },
        headers={"Cache-Control": "no-store"},
    )


@router.delete("/api/admin/ip-bans/{ip}")
async def admin_unban_ip(request: Request, ip: str):
    """解封；未处于封禁状态时返回 404（而不是假装成功）。"""
    security.verify_csrf(request)
    admin = security.require_admin(request)
    actor = str(admin.get("username") or "admin")
    try:
        row = await asyncio.to_thread(ip_ban.unban, ip, actor=actor)
    except ip_ban.BanError:
        raise HTTPException(status_code=400, detail=i18n.translate("server.error.ip_ban_failed")) from None
    if row is None:
        raise HTTPException(status_code=404, detail=i18n.translate("server.error.ip_ban_failed"))
    return JSONResponse(
        {
            "ok": True,
            "ban": _ban_payload(row),
            "counters": await asyncio.to_thread(ip_ban.counters),
        },
        headers={"Cache-Control": "no-store"},
    )


@router.get("/api/admin/ip-bans/{ip}/events")
async def admin_ip_ban_events(
    request: Request,
    ip: str,
    limit: int = Query(default=50, ge=1, le=BAN_LIST_MAX_PAGE),
    offset: int = Query(default=0, ge=0, le=1_000_000),
):
    """某个地址的封禁事件流（封禁/改期/解封/自然到期）。"""
    security.require_admin(request)
    listing = await asyncio.to_thread(ip_ban.list_events, ip=ip, limit=limit, offset=offset)
    return JSONResponse(listing, headers={"Cache-Control": "no-store"})


__all__ = ["router"]
