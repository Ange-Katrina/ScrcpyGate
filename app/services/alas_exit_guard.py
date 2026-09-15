"""One-shot ALAS guard that runs after the last workbench page goes away.

The workbench (and the other authenticated pages) keep a ``/ws/events``
connection open while they are loaded.  When a user closes the browser the
connection drops; if the user enabled 「退出浏览器后自动检测」 we wait a grace
period and then run exactly one check pass:

* only configs the user is bound to and allowed to run are considered;
* only ``stopped`` / ``idle`` / ``error`` states are restarted, so a healthy
  ALAS is left alone;
* a reconnect inside the grace window (page refresh, navigation) cancels the
  pass, and a user can trigger at most one pass per interval.  The guard never
  schedules a second pass on its own, which keeps a failing ALAS from being
  restarted in a loop.
"""

from __future__ import annotations

import asyncio
import logging
import time

from .. import alas, storage
from ..logging_config import log_event

log = logging.getLogger("webscrcpy.alas")

EXIT_GUARD_GRACE_SECONDS = 60.0
EXIT_GUARD_MIN_INTERVAL_SECONDS = 600.0
RESTARTABLE_STATUSES = frozenset({"stopped", "idle", "error"})

_state: dict[str, dict] = {}


def _entry(username: str) -> dict:
    entry = _state.get(username)
    if entry is None:
        # 「从未运行」用 None 表示，不能用 0.0：下面的间隔比较用的是
        # time.monotonic()（起点是主机开机），拿 0.0 当哨兵会让「开机后不足一个
        # 最小间隔」的首次检测被误判成「间隔内」而跳过（ISSUE-161：
        # 主机 uptime < EXIT_GUARD_MIN_INTERVAL_SECONDS 时，关浏览器不会触发重启检测）。
        entry = {"task": None, "last_run_at": None}
        _state[username] = entry
    return entry


def reset_state() -> None:
    """Cancel pending passes (test hook)."""
    for entry in _state.values():
        task = entry.get("task")
        if task is not None and not task.done():
            task.cancel()
        entry["task"] = None
    _state.clear()


def note_connected(username: str) -> None:
    """A page for this user is online: cancel any pending pass."""
    if not username:
        return
    entry = _entry(username)
    task = entry.get("task")
    if task is not None and not task.done():
        task.cancel()
    entry["task"] = None


def note_disconnected(username: str, *, has_other_connections: bool) -> None:
    """The last page for this user went away: arm the one-shot pass."""
    if not username or has_other_connections:
        return
    entry = _entry(username)
    task = entry.get("task")
    if task is not None and not task.done():
        return
    try:
        loop = asyncio.get_running_loop()
        entry["task"] = loop.create_task(_run_after_grace(username, entry))
    except RuntimeError:
        # 事件循环正在关闭：不再安排检测。
        entry["task"] = None


async def _run_after_grace(username: str, entry: dict) -> None:
    try:
        await asyncio.sleep(EXIT_GUARD_GRACE_SECONDS)
    except asyncio.CancelledError:
        entry["task"] = None
        raise
    entry["task"] = None
    now = time.monotonic()
    last_run_at = entry.get("last_run_at")
    if last_run_at is not None and now - float(last_run_at) < EXIT_GUARD_MIN_INTERVAL_SECONDS:
        log_event(log, "ALAS_EXIT_GUARD_SKIPPED", user=username, reason="min_interval")
        return
    try:
        preference = await asyncio.to_thread(storage.get_user_alas_preference, username)
    except Exception:
        log_event(log, "ALAS_EXIT_GUARD_PREFERENCE_FAILED", level=logging.WARNING, user=username)
        return
    if not preference.get("restart_on_exit"):
        return
    user_row = await asyncio.to_thread(storage.get_user, username)
    if not user_row:
        return
    user = dict(user_row)
    if not storage.user_is_active(user):
        return
    entry["last_run_at"] = time.monotonic()
    try:
        restarted = await asyncio.to_thread(_restart_stopped_configs, username, user)
    except Exception:
        log_event(log, "ALAS_EXIT_GUARD_FAILED", level=logging.WARNING, user=username)
        return
    log_event(log, "ALAS_EXIT_GUARD_RUN", user=username, restarted=restarted)


def _restart_stopped_configs(username: str, user: dict) -> list[str]:
    """Restart each bound config that is stopped or unhealthy (blocking)."""
    restarted: list[str] = []
    try:
        bindings = storage.list_user_alas_bindings(username) or []
    except Exception:
        log_event(log, "ALAS_EXIT_GUARD_BINDINGS_FAILED", level=logging.WARNING, user=username)
        return restarted
    is_admin = user.get("role") == "admin"
    seen: set[str] = set()
    for binding in bindings:
        name = str(binding.get("config_name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        if not is_admin and not binding.get("can_run"):
            continue
        try:
            status = str(alas.status_for_config(name, False).get("status") or "")
        except Exception:
            log_event(log, "ALAS_EXIT_GUARD_STATUS_FAILED", level=logging.WARNING, user=username, config=name)
            continue
        if status not in RESTARTABLE_STATUSES:
            continue
        try:
            result = alas.control_for_config("restart", name)
        except Exception:
            log_event(log, "ALAS_EXIT_GUARD_RESTART_FAILED", level=logging.WARNING, user=username, config=name)
            continue
        ok = bool(result.get("ok"))
        log_event(
            log,
            "ALAS_EXIT_GUARD_RESTART",
            level=logging.INFO if ok else logging.WARNING,
            user=username,
            config=name,
            status=status,
            ok=ok,
        )
        if ok:
            restarted.append(name)
    return restarted


__all__ = [
    "EXIT_GUARD_GRACE_SECONDS",
    "EXIT_GUARD_MIN_INTERVAL_SECONDS",
    "RESTARTABLE_STATUSES",
    "note_connected",
    "note_disconnected",
    "reset_state",
]
