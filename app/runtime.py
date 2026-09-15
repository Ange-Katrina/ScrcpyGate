"""Application lifecycle and per-application runtime state.

The FastAPI entrypoint used to own these values as module globals.  Keeping
them on ``app.state`` makes app factories and isolated tests independent while
leaving the existing mirror/ADB singletons untouched.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol

from fastapi import FastAPI

from . import security, storage
from . import alas_visibility
from .account_access import account_expiration_monitor
from .adb_monitor import adb_monitor
from .audit_dispatcher import AuditDispatcher
from .logging_config import log_event
from .mirror_manager import manager
from .mirror_runtime import VIEWER_DISCONNECT_GRACE
from .services.alas_expiry_guard import stop_expired_user_alas


log = logging.getLogger("webscrcpy.main")
AUDIT_READ_BARRIER_TIMEOUT = 0.5
ALAS_STATUS_CACHE_TTL_SECONDS = 120
ALAS_STATUS_CACHE_MAX_ENTRIES = 256


@dataclass
class RuntimeState:
    """Mutable resources owned by one FastAPI application instance."""

    audit_dispatcher: AuditDispatcher | None = None
    mirror_autostop_task: asyncio.Task | None = None
    account_expiration_task: asyncio.Task | None = None
    alas_status_cache: dict[tuple[str, str, str], tuple[float, dict[str, Any]]] = field(default_factory=dict)
    alas_status_cache_ttl: int = ALAS_STATUS_CACHE_TTL_SECONDS
    alas_status_cache_max_entries: int = ALAS_STATUS_CACHE_MAX_ENTRIES


def install_runtime(app: FastAPI) -> RuntimeState:
    current = getattr(app.state, "runtime", None)
    if isinstance(current, RuntimeState):
        return current
    current = RuntimeState()
    app.state.runtime = current
    return current


class _AppConnection(Protocol):
    """Minimal Request/WebSocket shape needed to resolve app-owned runtime."""

    @property
    def app(self) -> FastAPI: ...


def runtime_for(connection: _AppConnection) -> RuntimeState:
    """Return the runtime owned by a request or WebSocket application.

    Runtime state is intentionally never shared through a process-global
    fallback.  Callers outside an application boundary must receive the
    runtime explicitly instead of silently mutating another app instance.
    """
    if connection is None:
        raise RuntimeError("application runtime is required")
    app = getattr(connection, "app", None)
    state = getattr(app, "state", None)
    runtime = getattr(state, "runtime", None)
    if not isinstance(runtime, RuntimeState):
        raise RuntimeError("application runtime is not installed")
    return runtime


def _bounded_minutes(raw: Any, default: int, maximum: int) -> int:
    """Parse a non-negative minute setting with one consistent upper bound."""
    try:
        value = int(raw)
    except Exception:
        return default
    return max(0, min(value, maximum))


def auto_stop_minutes() -> int:
    missing = object()
    raw = storage.get_setting("auto_stop_minutes", missing)
    if raw is missing:
        raw = storage.get_setting("auto_stop_time", "15")
    return _bounded_minutes(raw, default=15, maximum=1440)


def max_session_minutes() -> int:
    """Maximum duration of one mirror session; zero means unlimited."""
    raw = storage.get_setting("max_session_minutes", "0")
    return _bounded_minutes(raw, default=0, maximum=10080)


def disconnect_stop_delay_seconds() -> float:
    """How long an unwatched session may keep running after its last viewer leaves.

    ``auto_stop_minutes`` owns this window, matching the admin copy and the
    idle sweep in :func:`mirror_autostop_loop`.  A zero setting keeps only the
    short reconnect grace so an abandoned stream is still cleaned up.
    """
    minutes = auto_stop_minutes()
    return float(minutes * 60) if minutes > 0 else VIEWER_DISCONNECT_GRACE


async def mirror_autostop_loop() -> None:
    async def record_autostop_audit(device_id: str, metadata: dict[str, Any]) -> None:
        """Persist one stop event without aborting the rest of a sweep."""
        try:
            await asyncio.to_thread(
                storage.record_audit_event,
                "system",
                "mirror_auto_stop",
                actor_role="system",
                outcome="success",
                target_type="device",
                target_id=device_id,
                metadata=metadata,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # A single audit failure must not prevent other idle sessions from
            # being stopped.  Keep the device identifier out of this log.
            log_event(
                log,
                "mirror.autostop_audit_failed",
                level=logging.ERROR,
                error_type=type(exc).__name__,
            )

    while True:
        await asyncio.sleep(10)
        try:
            minutes = auto_stop_minutes()
            stopped = [] if minutes <= 0 else await manager.stop_idle_sessions(minutes * 60)
            for device_id in stopped:
                await record_autostop_audit(device_id, {"idle_minutes": minutes})
            session_limit = max_session_minutes()
            if session_limit > 0:
                expired = await manager.stop_expired_sessions(session_limit * 60)
                for device_id in expired:
                    await record_autostop_audit(
                        device_id,
                        {"reason": "session_limit", "limit_minutes": session_limit},
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log_event(
                log,
                "mirror.autostop_iteration_failed",
                level=logging.ERROR,
                error_type=type(exc).__name__,
            )


def persist_audit_event_checked(**event):
    result = storage.record_audit_event(**event)
    if result is None:
        raise RuntimeError("audit event persistence failed")
    return result


def revoke_expired_access_with_audit() -> set[str]:
    expired = storage.revoke_expired_access()
    for username in expired:
        user = storage.get_user(username)
        expires_at = user["expires_at"] if user else None
        storage.record_audit_event(
            "system",
            "account_expired",
            actor_role="system",
            outcome="success",
            target_type="account",
            target_id=username,
            metadata={"expires_at": expires_at},
            dedupe_key=f"account_expired:{username}:{expires_at}",
        )
        # 「到期后自动停止其 ALAS 配置」只有后台设置打开时才做事；停止是幂等的
        # （只停仍在运行的配置），所以每 10s 的监控轮询不会反复操作。
        for config_name in stop_expired_user_alas(username):
            storage.record_audit_event(
                "system",
                "account_expired_alas_stop",
                actor_role="system",
                outcome="success",
                target_type="alas_config",
                target_id=f"{username}:{config_name}",
                metadata={"expires_at": expires_at, "config_name": config_name},
                dedupe_key=f"account_expired_alas:{username}:{config_name}:{expires_at}",
            )
    return expired


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start and stop application-owned background resources."""
    state = install_runtime(app)
    overrides = getattr(app.state, "lifespan_overrides", {})
    dispatcher_factory = overrides.get("AuditDispatcher", AuditDispatcher)
    autostop_loop = overrides.get("mirror_autostop_loop", mirror_autostop_loop)
    expiration_monitor = overrides.get("account_expiration_monitor", account_expiration_monitor)
    revoke_expired = overrides.get("revoke_expired_access_with_audit", revoke_expired_access_with_audit)
    log.info("APP_STARTUP")
    storage.init_db()
    # 登录保护：读取管理后台保存的安全机制配置（未设置项回落环境变量默认值）。
    security.refresh_login_guard_config()
    # 登录保护：失败计数与 IP 封禁镜像到 SQLite（重启/发版不清零）。
    security.enable_login_guard_persistence()
    # ALAS 嵌入：普通用户可见性规则由后台配置，启动时水合成内存快照（逐帧策略读取它）。
    alas_visibility.load_visibility_rules()
    dispatcher = dispatcher_factory(
        persist_audit_event_checked,
        maxsize=security.env_int("AUDIT_QUEUE_SIZE", 512, 16, 65536),
    )
    state.audit_dispatcher = dispatcher
    primary_error: BaseException | None = None
    cleanup_error: BaseException | None = None

    def note_cleanup_error(component: str, error: BaseException) -> None:
        nonlocal cleanup_error
        if cleanup_error is None:
            cleanup_error = error
        log_event(
            log,
            "app.cleanup_failed",
            level=logging.ERROR,
            component=component,
            error_type=type(error).__name__,
        )

    try:
        dispatcher.start()
        await adb_monitor.start()
        state.mirror_autostop_task = asyncio.create_task(autostop_loop())
        state.account_expiration_task = asyncio.create_task(
            expiration_monitor(revoke_expired, maintenance=storage.run_storage_maintenance)
        )
        yield
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        log.info("APP_SHUTDOWN")
        tasks = (
            ("mirror_autostop", state.mirror_autostop_task),
            ("account_expiration", state.account_expiration_task),
        )
        state.mirror_autostop_task = None
        state.account_expiration_task = None
        for component, task in tasks:
            if task is None:
                continue
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except BaseException as exc:
                note_cleanup_error(component, exc)
        try:
            await manager.stop_all()
        except BaseException as exc:
            note_cleanup_error("mirror_manager", exc)
        try:
            await adb_monitor.stop()
        except BaseException as exc:
            note_cleanup_error("adb_monitor", exc)
        state.audit_dispatcher = None
        try:
            stopped = await asyncio.to_thread(dispatcher.stop, 5.0)
            if not stopped:
                log.critical("AUDIT_QUEUE_STOP_TIMEOUT")
        except BaseException as exc:
            note_cleanup_error("audit_dispatcher", exc)
        if cleanup_error is not None and primary_error is None:
            raise cleanup_error
