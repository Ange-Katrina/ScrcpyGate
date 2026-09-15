"""Notification producers.

管理动作（改账户、调权限、断开投屏、停用设备）在给用户留下审计记录之外，还应该
在他的工作台通知里看到。这里把「写给哪个用户、什么类型、跳到哪里」集中起来，调用方
只描述发生了什么；任何失败都只记日志 —— 通知永远不能影响主操作。
"""

from __future__ import annotations

import logging

from .. import storage
from ..logging_config import log_event

log = logging.getLogger("webscrcpy.notifications")


def _href_for(username: str) -> str:
    """普通用户没有 /users 权限，指向工作台而不是用户与权限页。"""
    try:
        user = storage.get_user(username)
    except Exception:
        user = None
    return "/users" if user and str(user["role"]) == "admin" else "/"


def _create(username: str, kind: str, *, severity: str, data: dict, dedupe_key: str = "") -> bool:
    name = str(username or "").strip()
    if not name:
        return False
    try:
        return storage.create_user_notification(
            name,
            kind,
            severity=severity,
            data=data,
            href=_href_for(name),
            dedupe_key=dedupe_key,
        )
    except Exception:
        log_event(log, "NOTIFICATION_WRITE_FAILED", level=logging.WARNING, user=name, kind=kind)
        return False


def notify_account_updated(username: str, *, actor: str, changed: list[str]) -> bool:
    """管理员改了你的账户（密码/角色/启用/到期）。"""
    return _create(
        username,
        "account_updated",
        severity="info",
        data={"actor": str(actor or ""), "changed": [str(item) for item in (changed or [])][:8]},
    )


def notify_permission_changed(
    username: str,
    *,
    scope: str,
    actor: str = "",
    detail: str = "",
    revoked: bool = False,
) -> bool:
    """设备或 ALAS 权限被调整（``revoked`` 表示这次是收回）。"""
    return _create(
        username,
        "permission_changed",
        severity="warning" if revoked else "info",
        data={
            "scope": str(scope or ""),
            "actor": str(actor or ""),
            "detail": str(detail or "")[:120],
            "revoked": bool(revoked),
        },
    )


def notify_session_disconnected(username: str, *, device_name: str, actor: str = "") -> bool:
    """管理员结束了你的投屏/控制会话。"""
    return _create(
        username,
        "session_disconnected",
        severity="warning",
        data={"device": str(device_name or ""), "actor": str(actor or "")},
    )


def notify_device_unavailable(username: str, *, device_name: str) -> bool:
    """你能使用的设备被停用或删除。"""
    return _create(
        username,
        "device_unavailable",
        severity="warning",
        data={"device": str(device_name or "")},
    )


__all__ = [
    "notify_account_updated",
    "notify_device_unavailable",
    "notify_permission_changed",
    "notify_session_disconnected",
]
