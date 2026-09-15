"""ALAS binding, status and administrator read-model services."""

from __future__ import annotations

import asyncio
import logging
import threading
import time

from fastapi import HTTPException

from .. import alas, i18n, storage
from ..http_helpers import (
    alas_embed_route_class,
    public_error_detail,
)
from ..runtime import RuntimeState
from ..workers import ALAS_OVERVIEW_MAX_WORKERS, alas_overview_executor
from .mirror_service import resolve_device_or_404

log = logging.getLogger("webscrcpy.main")
ADMIN_ALAS_OVERVIEW_CONCURRENCY = ALAS_OVERVIEW_MAX_WORKERS
# 管理页读模型的两个短 TTL 缓存（与公开状态缓存共用同一张表，`clear_alas_status_cache`
# 一并失效）。公开状态要保持 120s 的 WAF 友好间隔，管理页则希望「刚点开就是新的」：
# 10s 足够让重复进入/刷新瞬时返回，又不会让状态明显过期；目录 5s。
ALAS_ADMIN_STATUS_CACHE_TTL_SECONDS = 10.0
ALAS_ADMIN_CATALOG_CACHE_TTL_SECONDS = 5.0
# 过期后先返回旧值、同时在后台线程池里刷新（stale-while-revalidate）。这样管理页
# 永远走缓存路径（实测热 22ms），冷路径的 5 次上游查询被挪出请求线程；超过这个上界
# 就不再发旧值，回到同步查询，避免上游长期异常时一直展示陈旧状态。
ALAS_ADMIN_CACHE_MAX_STALE_SECONDS = 60.0
# 读模型缓存键的命名空间前缀。用户名不允许控制字符（storage 校验 ord < 32），
# 因此 "\x00" 开头的键永远不会和公开状态缓存的 (username, device, config) 撞车。
_ADMIN_CACHE_PREFIX = "\x00admin"

_refresh_lock = threading.Lock()
_refresh_inflight: set[tuple[int, tuple[str, str, str]]] = set()


def _status_cache_trim(runtime: RuntimeState) -> None:
    cache = runtime.alas_status_cache
    if len(cache) > runtime.alas_status_cache_max_entries:
        oldest = min(cache.items(), key=lambda item: item[1][0])[0]
        cache.pop(oldest, None)


def _schedule_admin_cache_refresh(
    runtime: RuntimeState,
    cache_key: tuple[str, str, str],
    loader,
) -> None:
    """在后台线程池里重算一个过期的读模型条目。

    同一条目同时只允许一个刷新任务（`_refresh_inflight` 去重），避免并发请求各起一个。
    刷新失败只记日志：过期条目仍在 `ALAS_ADMIN_CACHE_MAX_STALE_SECONDS` 内继续服务。
    """
    token = (id(runtime), cache_key)
    with _refresh_lock:
        if token in _refresh_inflight:
            return
        _refresh_inflight.add(token)

    def run() -> None:
        try:
            loader()
        except Exception as exc:  # noqa: BLE001 - 后台刷新绝不能让请求线程看到异常
            log.warning("ALAS_ADMIN_CACHE_REFRESH_FAILED error_type=%s", type(exc).__name__)
        finally:
            with _refresh_lock:
                _refresh_inflight.discard(token)

    try:
        alas_overview_executor.submit(run)
    except RuntimeError:  # 线程池已关闭（进程退出中）
        with _refresh_lock:
            _refresh_inflight.discard(token)


def _store_catalog_entry(runtime: RuntimeState, key: tuple[str, str, str]) -> None:
    """重算配置目录并写回缓存（后台刷新入口）。"""
    result = alas.list_configs()
    payload = dict(result or {})
    runtime.alas_status_cache[key] = (time.monotonic(), payload)
    _status_cache_trim(runtime)


def runtime_catalog(runtime: RuntimeState | None = None) -> dict:
    """Runtime 配置目录（`configs` 接口），带短 TTL 缓存 + 过期后台刷新。

    管理页首屏与「配置目录」页签都要它；不做缓存时每次都要多打一次上游（实测 ~31ms）。
    """
    if runtime is None:
        return alas.list_configs()
    key = (_ADMIN_CACHE_PREFIX + "-catalog", "", "")
    now = time.monotonic()
    cached = runtime.alas_status_cache.get(key)
    if cached:
        age = now - cached[0]
        if age < ALAS_ADMIN_CATALOG_CACHE_TTL_SECONDS:
            return dict(cached[1])
        if age < ALAS_ADMIN_CACHE_MAX_STALE_SECONDS:
            # 目录变化远慢于运行状态：先给旧目录，后台换成新的。
            _schedule_admin_cache_refresh(runtime, key, lambda: _store_catalog_entry(runtime, key))
            return dict(cached[1])
    try:
        result = alas.list_configs()
    except Exception as exc:  # noqa: BLE001 - 上游不可达是页面状态，不是 500
        log.warning("ALAS_CATALOG_FAILED error_type=%s", type(exc).__name__)
        result = {"ok": False, "configs": [], "error": _alas_internal_error(exc)}
    payload = dict(result or {})
    runtime.alas_status_cache[key] = (now, payload)
    _status_cache_trim(runtime)
    return dict(payload)


def _alas_internal_error(error: BaseException) -> str:
    """Reduce upstream exceptions to stable categories before exposing status."""
    if isinstance(error, (OSError, TimeoutError, ConnectionError)):
        return "ALAS Runtime unreachable"
    return "ALAS Runtime request failed"


__all__ = [
    "ADMIN_ALAS_OVERVIEW_CONCURRENCY",
    "ALAS_ADMIN_CACHE_MAX_STALE_SECONDS",
    "ALAS_ADMIN_CATALOG_CACHE_TTL_SECONDS",
    "ALAS_ADMIN_STATUS_CACHE_TTL_SECONDS",
    "admin_alas_overview",
    "admin_alas_overview_local",
    "admin_alas_payload",
    "admin_alas_permissions_payload",
    "runtime_catalog",
    "admin_alas_status_for_config",
    "admin_overview_storage_payload",
    "admin_runtime_alas_config_names",
    "alas_binding_for_user",
    "alas_device_for_user",
    "bound_alas_config_names",
    "cached_public_alas_status",
    "clear_alas_status_cache",
    "log_alas_embed_denied",
    "log_alas_websocket_close",
    "public_admin_alas_bindings",
    "public_alas_status",
    "public_user_alas_bindings",
    "require_alas_binding",
]


def log_alas_embed_denied(user: dict, binding: dict | None, channel: str, path: str, reason: str) -> None:
    """Write a redacted ALAS embed denial record without request payloads."""
    log.warning(
        "ALAS_EMBED_DENIED channel=%s user=%s role=%s bound=%s route=%s reason=%s",
        channel,
        (user or {}).get("username", ""),
        (user or {}).get("role", ""),
        bool(binding and binding.get("config_name")),
        alas_embed_route_class(path),
        reason,
    )


def log_alas_websocket_close(
    connection_id: str,
    user: dict | None,
    reason: str,
    code: int,
    *,
    permission: str = "none",
    phase: str = "authorization",
) -> None:
    """Record WebSocket close metadata without paths, queries, or payloads."""
    # Username and role are routing context, not secrets; tokens, query strings
    # and message bodies must never be logged here.
    log.warning(
        "ALAS_WS_CLOSE connection=%s event=close phase=%s code=%s reason=%s permission=%s user=%s role=%s task=none",
        connection_id,
        phase,
        code,
        reason,
        permission,
        (user or {}).get("username", ""),
        (user or {}).get("role", ""),
    )


def alas_binding_for_user(
    user: dict,
    allow_admin_global: bool = False,
    config_name: str | None = None,
    device_id: str | None = None,
) -> dict | None:
    requested = str(config_name or "").strip()
    if requested:
        try:
            requested = alas.sanitize_config_name(requested)
        except ValueError:
            return None
        if user.get("role") == "admin" and device_id is not None:
            binding = storage.get_alas_binding_by_config(requested, device_id)
        else:
            binding = storage.get_user_alas_binding(user["username"], requested, device_id)
    else:
        if user.get("role") == "admin" and device_id is not None:
            bindings = storage.list_user_alas_bindings(device_id=device_id)
            binding = bindings[0] if bindings else None
        else:
            binding = storage.get_user_alas_config(user["username"], device_id)
    if binding and binding.get("config_name"):
        if user.get("role") == "admin":
            binding = {**binding, "can_run": True, "can_edit": True}
        return binding
    if user.get("role") == "admin" and device_id is not None:
        # Administrators inherit ALAS access globally.  A device-scoped row is
        # optional; keep the selected device in the fallback so the embed
        # proxy never loses its context.
        return {
            "username": user["username"],
            "config_name": requested or alas.legacy_config_name(),
            "device_id": device_id,
            "device_name": "",
            "can_run": True,
            "can_edit": True,
            "is_default": not requested,
            "updated_at": 0,
            "admin_fallback": True,
        }
    if allow_admin_global and user.get("role") == "admin" and device_id is None:
        return {
            "username": user["username"],
            "config_name": requested or alas.legacy_config_name(),
            "can_run": True,
            "can_edit": True,
            "is_default": not requested,
            "updated_at": 0,
            "admin_fallback": True,
        }
    return None


def require_alas_binding(
    user: dict,
    *,
    config_name: str | None = None,
    device_id: str | None = None,
    run: bool = False,
    edit: bool = False,
) -> dict:
    requested = str(config_name or "").strip()
    if requested:
        try:
            requested = alas.sanitize_config_name(requested)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=i18n.translate("server.error.invalid_alas_config")) from exc
    binding = alas_binding_for_user(
        user,
        allow_admin_global=user.get("role") == "admin",
        config_name=requested or None,
        device_id=device_id,
    )
    if not binding:
        raise HTTPException(status_code=403, detail=i18n.translate("server.error.alas_config_not_bound"))
    if run and not binding.get("can_run"):
        raise HTTPException(status_code=403, detail=i18n.translate("server.error.alas_run_denied"))
    if edit and not binding.get("can_edit"):
        raise HTTPException(status_code=403, detail=i18n.translate("server.error.alas_edit_denied"))
    try:
        binding["config_name"] = alas.sanitize_config_name(binding.get("config_name"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=i18n.translate("server.error.invalid_bound_alas_config")) from exc
    return binding


def public_user_alas_bindings(user: dict, device_id: str | None = None) -> list[dict]:
    if user.get("role") == "admin":
        bindings = (
            storage.list_user_alas_bindings(device_id=device_id)
            if device_id is not None
            else storage.list_user_alas_bindings(user["username"])
        )
        if not bindings and device_id is None:
            bindings = [alas_binding_for_user(user, allow_admin_global=True)]
    else:
        bindings = storage.list_user_alas_bindings(user["username"], device_id)
    return [
        {
            "config_name": binding["config_name"],
            "device_id": storage.public_device_id(binding.get("device_id")) if binding.get("device_id") else "",
            "device_name": str(binding.get("device_name") or ""),
            "can_run": bool(user.get("role") == "admin" or binding.get("can_run")),
            "can_edit": bool(user.get("role") == "admin" or binding.get("can_edit")),
            "is_default": bool(binding.get("is_default")),
        }
        for binding in bindings
        if binding and binding.get("config_name")
    ]


def public_admin_alas_bindings(bindings: list[dict]) -> list[dict]:
    """Expose ALAS assignments without leaking internal device identifiers."""
    result = []
    for binding in bindings:
        item = dict(binding)
        internal_device_id = str(item.pop("device_id", "") or "").strip()
        item["device_id"] = storage.public_device_id(internal_device_id) if internal_device_id else ""
        item["device_name"] = str(item.get("device_name") or "")
        result.append(item)
    return result


def public_alas_status(result: dict, binding: dict) -> dict:
    cleaned = dict(result or {})
    cleaned.pop("settings", None)
    cleaned.pop("configs", None)
    cleaned["config"] = binding.get("config_name") or cleaned.get("config") or ""
    cleaned["can_run"] = bool(binding.get("can_run"))
    cleaned["can_edit"] = bool(binding.get("can_edit"))
    if cleaned.get("error"):
        cleaned["error"] = public_error_detail(cleaned["error"])
    return cleaned


async def cached_public_alas_status(
    user: dict,
    binding: dict,
    device_id: str | None = None,
    *,
    runtime: RuntimeState,
) -> dict:
    """Read one ALAS status per config/device within the WAF-friendly TTL."""
    config_name = str(binding.get("config_name") or "").strip()
    if not config_name:
        return {
            "ok": False,
            "configured": False,
            "status": "unbound",
            "config": "",
            "can_run": False,
            "can_edit": False,
            "error": i18n.translate("server.error.alas_config_not_bound"),
        }
    try:
        config_name = alas.sanitize_config_name(config_name)
    except ValueError:
        return {
            "ok": False,
            "configured": False,
            "status": "invalid_config",
            "config": "",
            "can_run": False,
            "can_edit": False,
        }
    key = (str(user.get("username") or ""), str(device_id or ""), config_name)
    state = runtime
    now = time.monotonic()
    cache = state.alas_status_cache
    cached = cache.get(key)
    if cached and now - cached[0] < state.alas_status_cache_ttl:
        return dict(cached[1])
    try:
        result = await asyncio.to_thread(alas.status_for_config, config_name, False)
    except Exception as exc:  # Runtime reachability is a user-visible state, not a 500.
        log.warning("ALAS_STATUS_FAILED config=%s error_type=%s", config_name, type(exc).__name__)
        result = {
            "ok": False,
            "status": "unreachable",
            "config": config_name,
            "error": _alas_internal_error(exc),
        }
    payload = public_alas_status(result, {**binding, "config_name": config_name})
    payload["checked_at"] = int(time.time())
    cache[key] = (now, payload)
    if len(cache) > state.alas_status_cache_max_entries:
        oldest = min(cache.items(), key=lambda item: item[1][0])[0]
        cache.pop(oldest, None)
    return dict(payload)


def clear_alas_status_cache(runtime: RuntimeState) -> None:
    """Invalidate runtime status after an explicit ALAS operation."""
    runtime.alas_status_cache.clear()


def bound_alas_config_names(bindings: list[dict] | None = None) -> list[str]:
    """Return unique ALAS config names that are explicitly bound to users."""
    seen: set[str] = set()
    names: list[str] = []
    for binding in bindings if bindings is not None else storage.list_user_alas_bindings():
        raw = str(binding.get("config_name") or "").strip()
        if not raw:
            continue
        try:
            name = alas.sanitize_config_name(raw)
        except ValueError:
            continue
        if name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def admin_runtime_alas_config_names() -> list[str]:
    """Return the configured Runtime catalog for administrator fallback access."""
    settings = alas.public_settings()
    if not settings.get("enabled") or not settings.get("token_set"):
        return []
    try:
        result = alas.list_configs()
    except Exception as exc:
        log.warning("ALAS_ADMIN_CATALOG_FAILED error_type=%s", type(exc).__name__)
        return []
    names: list[str] = []
    for raw in result.get("configs") or []:
        try:
            name = alas.sanitize_config_name(raw)
        except ValueError:
            continue
        if name not in names:
            names.append(name)
    return names


def _status_for_one(name: str) -> dict:
    """单个配置的 Runtime 状态（错误信息已脱敏）。"""
    status = alas.status_for_config(name, include_configs=False)
    if not isinstance(status, dict):
        status = {"ok": False, "status": "disconnected", "task": "", "config": name}
    raw_error = status.get("error")
    if raw_error:
        status = {**status, "error": public_error_detail(raw_error)}
    return status


def _status_for_many_concurrent(names: list[str]) -> dict[str, dict]:
    """并发查询多个配置状态（每个配置一次上游请求）。

    串行时 5 个配置 ≈ 253ms（每个 ~51ms），并发后 ≈ 140ms（ALAS 端是单实例，收益有限但
    仍省掉一半等待）。首个「Runtime 不可达」时短路：其余按同一状态补全并取消尚未开始的
    请求，避免上游挂掉时把整页拖到超时。
    """
    futures = {
        name: alas_overview_executor.submit(alas.status_for_config, name, False)
        for name in names
    }
    statuses: dict[str, dict] = {}
    unreachable: dict | None = None
    for name in names:
        if unreachable is not None:
            statuses[name] = {**unreachable, "config": name}
            continue
        try:
            status = _status_for_one_from(futures[name])
        except Exception as exc:  # noqa: BLE001 - 兜底：线程池里的意外异常
            status = {
                "ok": False,
                "status": "disconnected",
                "task": "",
                "config": name,
                "error": _alas_internal_error(exc),
            }
        statuses[name] = status
        if status.get("error") and _alas_runtime_unreachable(status["error"]):
            unreachable = {key: value for key, value in status.items() if key != "config"}
            for other in names:
                if other not in statuses:
                    futures[other].cancel()
    return statuses


def _status_for_one_from(future) -> dict:
    """线程池结果 → 脱敏后的单个状态。"""
    status = future.result()
    if not isinstance(status, dict):
        status = {}
    raw_error = status.get("error")
    if raw_error:
        status = {**status, "error": public_error_detail(raw_error)}
    return status


def _admin_status_payload(statuses: dict[str, dict], names: list[str]) -> dict:
    """把逐配置状态整理成缓存里的读模型（`result` = 首个配置的原始状态）。"""
    return {
        "result": dict(statuses[names[0]]),
        "config_statuses": [
            {
                "config": name,
                "status": str(statuses[name].get("status") or "disconnected"),
                "task": str(statuses[name].get("task") or ""),
                "ok": bool(statuses[name].get("ok", not statuses[name].get("error"))),
                "error": str(statuses[name].get("error") or ""),
            }
            for name in names
        ],
    }


def _admin_status_from_cache(payload: dict) -> tuple[dict, list[dict]]:
    return dict(payload["result"]), [dict(item) for item in payload["config_statuses"]]


def _store_admin_status_entry(
    runtime: RuntimeState,
    cache_key: tuple[str, str, str],
    names: list[str],
) -> None:
    """重算多个配置状态并写回缓存（后台刷新入口）。"""
    statuses = (
        {names[0]: _status_for_one(names[0])}
        if len(names) == 1
        else _status_for_many_concurrent(names)
    )
    runtime.alas_status_cache[cache_key] = (time.monotonic(), _admin_status_payload(statuses, names))
    _status_cache_trim(runtime)


def _alas_status_for_many(names: list[str], runtime: RuntimeState | None = None) -> tuple[dict, list[dict]]:
    """逐个查询 Runtime 配置状态;首个不可达时短路,避免串行慢请求拖垮页面。

    `runtime` 传入时结果进入短 TTL 缓存：TTL 内直接命中；刚过期则**先返回旧值**并在
    后台线程池刷新（stale-while-revalidate），因此管理页的重复进入/刷新都不会再等
    上游的 5 次查询；超过 `ALAS_ADMIN_CACHE_MAX_STALE_SECONDS` 才回到同步查询。
    """
    if not names:
        return {}, []
    cache_key = (_ADMIN_CACHE_PREFIX + "-status", names[0], "\n".join(names))
    if runtime is not None:
        cached = runtime.alas_status_cache.get(cache_key)
        if cached:
            age = time.monotonic() - cached[0]
            if age < ALAS_ADMIN_STATUS_CACHE_TTL_SECONDS:
                return _admin_status_from_cache(cached[1])
            if age < ALAS_ADMIN_CACHE_MAX_STALE_SECONDS:
                _schedule_admin_cache_refresh(
                    runtime,
                    cache_key,
                    lambda: _store_admin_status_entry(runtime, cache_key, names),
                )
                return _admin_status_from_cache(cached[1])

    statuses = (
        {names[0]: _status_for_one(names[0])}
        if len(names) == 1
        else _status_for_many_concurrent(names)
    )
    payload = _admin_status_payload(statuses, names)
    result = payload["result"]
    config_statuses = payload["config_statuses"]
    if runtime is not None:
        runtime.alas_status_cache[cache_key] = (time.monotonic(), payload)
        _status_cache_trim(runtime)
    return result, config_statuses


def admin_alas_status_for_config(
    config_name: str | None = None,
    bindings: list[dict] | None = None,
    runtime: RuntimeState | None = None,
) -> dict:
    settings = alas.public_settings()
    bound_configs = bound_alas_config_names(bindings)
    selected = str(config_name or "").strip()
    if selected:
        try:
            selected = alas.sanitize_config_name(selected)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=i18n.translate("server.error.invalid_alas_config_name")) from exc
    elif bound_configs:
        selected = bound_configs[0]
    enabled = bool(settings.get("enabled"))
    token_set = bool(settings.get("token_set"))
    configured = bool(enabled and token_set)
    if not configured:
        status = "disabled" if not enabled else "error"
        error = i18n.translate(
            "server.status.alas_control_disabled" if not enabled else "server.status.alas_token_missing"
        )
        config_names = list(bound_configs)
        if selected and selected not in config_names:
            config_names.insert(0, selected)
        return {
            "ok": not enabled,
            "settings": settings,
            "enabled": enabled,
            "token_set": token_set,
            "configured": False,
            "status": status,
            "task": "",
            "config": selected,
            "configs": config_names,
            "bound_configs": list(bound_configs),
            "runtime_configs": [],
            "config_statuses": [
                {
                    "config": name,
                    "status": status,
                    "task": "",
                    "ok": not enabled,
                    "error": error,
                }
                for name in config_names
            ],
            "error": error,
        }
    if not selected:
        base = {
            "ok": True,
            "settings": settings,
            "enabled": enabled,
            "token_set": token_set,
            "configured": True,
            "status": "unknown",
            "task": "",
            "config": "",
            "configs": [],
            "bound_configs": list(bound_configs),
            "runtime_configs": [],
            "error": "",
        }
        # 尚未建立任何配置归属时,回退查询 Runtime 配置目录:
        # 管理页"同步/刷新"仍应显示真实配置与运行状态,而不是永远空目录 + 未检查。
        try:
            catalog = runtime_catalog(runtime)
        except Exception as exc:
            base["ok"] = False
            base["error"] = public_error_detail(str(exc) or "ALAS Runtime unreachable")
            return base
        names = [str(item) for item in (catalog.get("configs") or []) if str(item)]
        if catalog.get("error"):
            base["ok"] = False
            base["error"] = public_error_detail(str(catalog.get("error")))
            if not names:
                return base
        if not names:
            base["error"] = i18n.translate("server.status.alas_runtime_unreachable")
            return base
        selected = names[0]
        result, config_statuses = _alas_status_for_many(names, runtime)
        result["enabled"] = base["enabled"]
        result["token_set"] = base["token_set"]
        result["configs"] = names
        result["bound_configs"] = list(bound_configs)
        result["runtime_configs"] = names
        result["config_statuses"] = config_statuses
        return result
    bound_names = list(bound_configs) if bound_configs else [selected]
    try:
        catalog = runtime_catalog(runtime)
    except Exception:
        catalog = None
    catalog_names = [str(item) for item in ((catalog or {}).get("configs") or []) if str(item)]
    names: list[str] = []
    for name in bound_names + catalog_names:
        if name not in names:
            names.append(name)
    result, config_statuses = _alas_status_for_many(names, runtime)
    if selected not in names:
        result = alas.status_for_config(selected, include_configs=False)
        if result.get("error"):
            result["error"] = public_error_detail(result["error"])
    result["enabled"] = bool(settings.get("enabled"))
    result["token_set"] = bool(settings.get("token_set"))
    result["configs"] = names
    result["bound_configs"] = list(bound_configs)
    result["runtime_configs"] = catalog_names
    result["config_statuses"] = config_statuses
    return result


def _alas_overview_owners(assignments: list[dict]) -> dict[str, str]:
    owners: dict[str, str] = {}
    for binding in assignments:
        try:
            name = alas.sanitize_config_name(binding.get("config_name"))
        except ValueError:
            continue
        owners[name] = str(binding.get("username") or "").strip()
    return owners


def admin_alas_overview_local(bindings: list[dict] | None = None) -> dict:
    """Build a network-free ALAS summary while preserving the legacy fields."""
    settings = alas.public_settings()
    assignments = bindings if bindings is not None else storage.list_user_alas_bindings()
    config_names = bound_alas_config_names(assignments)
    owners = _alas_overview_owners(assignments)
    enabled = bool(settings.get("enabled"))
    token_set = bool(settings.get("token_set"))
    configured = bool(enabled and token_set)
    selected = config_names[0] if config_names else ""
    status = "unknown" if configured else "disabled" if not enabled else "error"
    error = "" if configured else i18n.translate(
        "server.status.alas_control_disabled" if not enabled else "server.status.alas_token_missing"
    )
    config_statuses = [
        {
            "config": name,
            "username": owners.get(name, ""),
            "device_id": next((storage.public_device_id(str(item.get("device_id"))) for item in assignments if str(item.get("config_name") or "") == name and item.get("device_id")), ""),
            "status": status,
            "task": "",
            "ok": not enabled,
            "error": error,
        }
        for name in config_names
    ] if not configured else []
    return {
        "ok": not enabled or configured,
        "settings": settings,
        "enabled": enabled,
        "token_set": token_set,
        "configured": configured,
        "status": status,
        "task": "",
        "config": selected,
        "configs": config_names,
        "bound_configs": list(config_names),
        "runtime_configs": [],
        "config_statuses": config_statuses,
        "config_count": len(config_names),
        "running_count": 0,
        "error": error,
        "catalog_error": "",
        "bindings": public_admin_alas_bindings(assignments),
    }


def _alas_runtime_unreachable(error: object) -> bool:
    value = str(error or "").lower()
    return any(token in value for token in ("unreachable", "timed out", "timeout", "connection refused"))


async def _admin_alas_call(func, *args) -> dict:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(alas_overview_executor, func, *args)


async def admin_alas_overview(bindings: list[dict] | None = None) -> dict:
    """Load every discoverable ALAS config without delaying the base overview."""
    assignments = bindings if bindings is not None else await asyncio.to_thread(storage.list_user_alas_bindings)
    summary = await asyncio.to_thread(admin_alas_overview_local, assignments)
    if not summary["configured"]:
        return summary

    bound_configs = list(summary["bound_configs"])
    owners = _alas_overview_owners(assignments)

    async def load_catalog() -> dict:
        try:
            return await _admin_alas_call(alas.list_configs)
        except Exception as exc:
            log.warning("ALAS_OVERVIEW_CATALOG_FAILED error_type=%s", type(exc).__name__)
            return {"ok": False, "configs": [], "error": _alas_internal_error(exc)}

    async def load_status(name: str) -> tuple[str, dict]:
        try:
            return name, await _admin_alas_call(alas.status_for_config, name, False)
        except Exception as exc:
            log.warning("ALAS_OVERVIEW_STATUS_FAILED config=%s error_type=%s", name, type(exc).__name__)
            return name, {
                "ok": False,
                "status": "error",
                "task": "",
                "config": name,
                "error": _alas_internal_error(exc),
            }

    def runtime_config_names(catalog: dict) -> list[str]:
        names: list[str] = []
        for raw in catalog.get("configs") or []:
            try:
                name = alas.sanitize_config_name(raw)
            except ValueError:
                continue
            if name not in names:
                names.append(name)
        return names

    if bound_configs:
        selected = bound_configs[0]
        catalog, selected_status_pair = await asyncio.gather(load_catalog(), load_status(selected))
        runtime_configs = runtime_config_names(catalog)
    else:
        catalog = await load_catalog()
        runtime_configs = runtime_config_names(catalog)
        catalog_error = str(catalog.get("error") or "")
        if not catalog.get("ok") or not runtime_configs:
            has_error = bool(catalog_error)
            return {
                **summary,
                "ok": not has_error,
                "status": "error" if has_error else "unknown",
                "configs": [],
                "bound_configs": [],
                "runtime_configs": [],
                "config_statuses": [],
                "config_count": 0,
                "running_count": 0,
                "error": public_error_detail(catalog_error),
                "catalog_error": public_error_detail(catalog_error),
            }
        selected = runtime_configs[0]
        selected_status_pair = await load_status(selected)

    selected_name, selected_status = selected_status_pair
    config_names = list(runtime_configs)
    for name in bound_configs:
        if name and name not in config_names:
            config_names.append(name)

    status_by_config: dict[str, dict] = {selected_name: selected_status}
    pending = [name for name in config_names if name not in status_by_config]
    selected_error = str(selected_status.get("error") or "")
    if pending and _alas_runtime_unreachable(selected_error):
        for name in pending:
            status_by_config[name] = {
                "ok": False,
                "status": str(selected_status.get("status") or "disconnected"),
                "task": "",
                "config": name,
                "error": selected_error,
            }
    elif pending:
        for name, result in await asyncio.gather(*(load_status(name) for name in pending)):
            status_by_config[name] = result

    config_statuses = []
    errors = [str(catalog.get("error") or "")]
    for name in config_names:
        result = status_by_config.get(name) or {}
        binding = next((item for item in assignments if str(item.get("config_name") or "") == name and item.get("device_id")), None)
        item = {
            "config": name,
            "username": owners.get(name, ""),
            "device_id": storage.public_device_id(str(binding.get("device_id"))) if binding else "",
            "status": str(result.get("status") or "unknown"),
            "task": str(result.get("task") or ""),
            "ok": bool(result.get("ok", not result.get("error"))),
            "error": public_error_detail(result.get("error")),
        }
        config_statuses.append(item)
        errors.append(item["error"])
    running_count = sum(item["status"] == "running" for item in config_statuses)
    has_error = bool(catalog.get("error")) or any(not item["ok"] or item["status"] == "error" for item in config_statuses)
    overall_status = "error" if has_error else "running" if running_count else "stopped" if config_statuses else "unknown"
    first_error = public_error_detail(next((error for error in errors if error), ""))
    primary_name = bound_configs[0] if bound_configs else config_names[0] if config_names else ""
    primary_status = status_by_config.get(primary_name) or {}
    return {
        **summary,
        "ok": not has_error,
        "status": overall_status,
        "task": str(primary_status.get("task") or ""),
        "config": primary_name,
        "configs": config_names,
        "bound_configs": bound_configs,
        "runtime_configs": runtime_configs,
        "config_statuses": config_statuses,
        "config_count": len(config_statuses),
        "running_count": running_count,
        "error": first_error,
        "catalog_error": public_error_detail(catalog.get("error")),
        "bindings": public_admin_alas_bindings(assignments),
    }


def admin_alas_payload(config_name: str | None = None, runtime: RuntimeState | None = None) -> dict:
    """ALAS 管理页首屏读模型。

    `runtime` 传入时，Runtime 状态与配置目录走短 TTL 缓存 + 并发查询（管理页最慢的一条
    路径）；不传时行为与以前一致（每次都打上游）。
    """
    bindings = storage.list_user_alas_configs()
    assignments = storage.list_user_alas_bindings()
    return {
        # token_key 只是密钥状态（是否配置/是否有效/来源），不含密钥本身。
        "settings": {**alas.public_settings(), "token_key": alas.token_key_state()},
        "status": admin_alas_status_for_config(config_name, assignments, runtime),
        "bindings": public_admin_alas_bindings(bindings),
        "assignments": public_admin_alas_bindings(assignments),
        "bound_configs": bound_alas_config_names(assignments),
    }


def _with_effective(bindings: list[dict], active_usernames: set[str]) -> list[dict]:
    """给归属行标出「这个账号现在到底能不能用这条授权」。

    停用/已到期的账号（``user_is_active`` 为假）写了绑定也不会生效，读模型里必须
    区分开，否则管理员看到的是一条看起来正常、实际全部被 user_can / require_alas_binding
    拒绝的授权。
    """
    rows = []
    for binding in bindings:
        item = dict(binding)
        username = str(item.get("username") or "")
        item["user_active"] = bool(username and username in active_usernames)
        item["effective"] = bool(item["user_active"] and item.get("config_name"))
        rows.append(item)
    return rows


def admin_alas_permissions_payload() -> dict:
    devices = storage.list_all_devices()
    users = storage.list_users()
    active_usernames = {str(user["username"]) for user in users if storage.user_is_active(user)}
    return {
        "bindings": _with_effective(public_admin_alas_bindings(storage.list_user_alas_configs()), active_usernames),
        "assignments": _with_effective(public_admin_alas_bindings(storage.list_user_alas_bindings()), active_usernames),
        "users": users,
        "devices": [
            {
                "device_id": storage.public_device_id(device["id"]),
                "name": str(device.get("name") or ""),
                "enabled": bool(device.get("enabled")),
            }
            for device in devices
        ],
    }


def admin_overview_storage_payload() -> tuple[list[dict], list[dict], list[dict], dict]:
    alas_bindings = storage.list_user_alas_bindings()
    return (
        storage.list_all_devices(),
        storage.list_users(),
        alas_bindings,
        admin_alas_overview_local(alas_bindings),
    )


def alas_device_for_user(user: dict, device_ref: str | None) -> str | None:
    """Resolve an optional public device reference and enforce view access."""
    ref = str(device_ref or "").strip()
    if not ref:
        return None
    device_id = resolve_device_or_404(ref)
    if not storage.user_can(user["username"], device_id, "view"):
        raise HTTPException(status_code=403, detail=i18n.translate("server.error.device_denied"))
    return device_id
