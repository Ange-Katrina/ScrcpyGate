"""Stop the ALAS configs of an account whose expiry has just been enforced.

后台设置 ``stop_alas_on_expiry`` 打开时，账户到期回收（会话与控制锁被清空）之后
把该用户仍能运行的 ALAS 配置停掉；关闭时什么都不做（默认关闭）。只停「看起来还
在跑」的配置，因此 10s 一次的到期监控可以重复调用而不会反复停止已停的配置。

与 :mod:`app.services.alas_exit_guard` 的关系：那个负责「最后一个页面关闭后重启
掉线的配置」，本模块只负责「账户到期后停止配置」，两者互补。
"""

from __future__ import annotations

import logging

from .. import alas, storage
from ..logging_config import log_event

log = logging.getLogger("webscrcpy.alas")

# 与 alas_exit_guard.RESTARTABLE_STATUSES 互补：只有明确「不在跑」的状态才跳过，
# 其余（running/未知状态）都按「在跑」处理，避免因为不认识的状态而漏停。
NON_RUNNING_ALAS_STATUSES = frozenset({"stopped", "idle", "error", "disconnected", "unknown", ""})


def stop_expired_user_alas(username: str) -> list[str]:
    """Stop every ALAS config the expired account may still be running.

    Returns the config names that were actually stopped (empty when the feature
    is disabled, the account is gone, or nothing was running).
    """
    name = str(username or "").strip()
    if not name:
        return []
    try:
        if not storage.get_ui_settings().get("stop_alas_on_expiry"):
            return []
    except Exception:
        log_event(log, "ALAS_EXPIRY_GUARD_SETTINGS_FAILED", level=logging.WARNING, user=name)
        return []
    user = storage.get_user(name)
    if not user:
        return []
    is_admin = str(user["role"]) == "admin"
    try:
        bindings = storage.list_user_alas_bindings(name) or []
    except Exception:
        log_event(log, "ALAS_EXPIRY_GUARD_BINDINGS_FAILED", level=logging.WARNING, user=name)
        return []

    stopped: list[str] = []
    seen: set[str] = set()
    for binding in bindings:
        config_name = str(binding.get("config_name") or "").strip()
        if not config_name or config_name in seen:
            continue
        seen.add(config_name)
        if not is_admin and not binding.get("can_run"):
            continue
        try:
            status = str(alas.status_for_config(config_name, False).get("status") or "")
        except Exception:
            log_event(log, "ALAS_EXPIRY_GUARD_STATUS_FAILED", level=logging.WARNING, user=name, config=config_name)
            continue
        if status.strip().lower() in NON_RUNNING_ALAS_STATUSES:
            continue
        try:
            result = alas.control_for_config("stop", config_name)
        except Exception:
            log_event(log, "ALAS_EXPIRY_GUARD_STOP_FAILED", level=logging.WARNING, user=name, config=config_name)
            continue
        ok = bool(result.get("ok"))
        log_event(
            log,
            "ALAS_EXPIRY_GUARD_STOP",
            level=logging.INFO if ok else logging.WARNING,
            user=name,
            config=config_name,
            status=status,
            ok=ok,
        )
        if ok:
            stopped.append(config_name)
    return stopped


__all__ = ["NON_RUNNING_ALAS_STATUSES", "stop_expired_user_alas"]
