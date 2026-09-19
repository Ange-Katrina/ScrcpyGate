"""IP 封禁（BAN）策略：预设时长、地址规范化、保护地址校验、内存快照与连接清理。

三层职责分开，避免判定路径上出现惊喜：

* **数据库是权威状态**（``storage_bans`` 写 ``ip_bans`` + ``ip_ban_events``），重启后仍然生效。
* **内存只放快照**（``ip -> expires_ts``），每请求判定走快照，避免每请求读库；
  本进程内的任何变更都会立刻刷新快照，跨进程变更由 TTL（默认 5 秒）兜底。
* **判定是纯读**：``is_banned()`` 不改状态、不写库，也不做任何清理动作。

多进程限制（必须诚实说明）：快照与连接注册表都在进程内，因此封禁只对**本进程**生效；
本项目只支持单容器 + ``--workers 1`` 拓扑（``deploy.sh`` 强制）。跨副本封禁需要共享存储 +
广播，当前未实现。

永不封禁的地址：环回、未指定、组播、链路本地地址，以及配置里的可信代理地址——否则一次误操作
就可能把容器自己的健康探针或反向代理挡在门外（任务书要求健康探针不可能被名单命中）。
"""

from __future__ import annotations

import ipaddress
import logging
import os
import socket
import threading
import time

from . import storage
from .storage_bans import BAN_ACTION_BAN, BAN_ACTION_UPDATE

log = logging.getLogger("webscrcpy.ip_ban")

# 预设时长（秒）。永久封禁在数据里用 permanent=1 + expires_ts NULL 表示，
# 不用超大时间戳，避免各类比较和展示都要处理魔法值。
BAN_PRESET_SECONDS: dict[str, int | None] = {
    "15m": 15 * 60,
    "1h": 60 * 60,
    "24h": 24 * 60 * 60,
    "7d": 7 * 24 * 60 * 60,
    "permanent": None,
}
BAN_MAX_SECONDS = storage.BAN_MAX_SECONDS
BAN_MIN_SECONDS = 1

# 快照 TTL：跨进程/外部改动最多 5 秒后生效；本进程内的改动立即生效。
SNAPSHOT_TTL_SECONDS = 5.0

_REASON_MAX = 200
_ACTOR_MAX = 64

_lock = threading.RLock()
_snapshot: dict[str, int | None] = {}
_snapshot_loaded_at = 0.0
_snapshot_loaded = False
_protected_cache: tuple[float, frozenset[str]] = (0.0, frozenset())
PROTECTED_TTL_SECONDS = 60.0


class BanError(ValueError):
    """封禁参数不合法（时长、地址或保护地址）。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def normalize_ip(raw: object) -> str:
    """规范化地址：IPv4 映射的 IPv6（``::ffff:1.2.3.4``）折回 IPv4，其余保留压缩写法。

    返回空字符串表示无法解析——调用方必须把「解析不出来」当成不封禁处理，
    绝不能把原始字符串写进名单（否则名单里会出现永远匹配不上的垃圾行）。
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    # 去掉可能的方括号与端口（X-Forwarded-For 里偶尔会带端口）
    if text.startswith("["):
        text = text[1:].split("]", 1)[0]
    elif text.count(":") == 1 and "." in text:
        text = text.split(":", 1)[0]
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return ""
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return str(address)


def _own_addresses() -> frozenset[str]:
    """本机地址 + 配置的可信代理地址（缓存 60 秒；解析失败不影响判定）。"""
    global _protected_cache
    now = time.monotonic()
    if _protected_cache[0] and now - _protected_cache[0] < PROTECTED_TTL_SECONDS:
        return _protected_cache[1]
    found: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            candidate = normalize_ip(info[4][0])
            if candidate:
                found.add(candidate)
    except Exception:  # pragma: no cover - 主机名解析在精简容器里可能失败
        log.debug("BAN_OWN_ADDRESS_LOOKUP_FAILED", exc_info=True)
    for entry in str(os.environ.get("TRUSTED_PROXY_IPS", "") or "").split(","):
        candidate = normalize_ip(entry.strip())
        if candidate:
            found.add(candidate)
    resolved = frozenset(found)
    _protected_cache = (now, resolved)
    return resolved


def is_protected_ip(raw: object) -> tuple[bool, str]:
    """判断某地址是否禁止写入名单，返回 (是否保护, 原因)。"""
    text = normalize_ip(raw)
    if not text:
        return True, "invalid"
    address = ipaddress.ip_address(text)
    if address.is_loopback:
        return True, "loopback"
    if address.is_unspecified:
        return True, "unspecified"
    if address.is_multicast:
        return True, "multicast"
    if address.is_link_local:
        return True, "link_local"
    if text in _own_addresses():
        return True, "local_interface"
    return False, ""


def _load_snapshot_locked(force: bool = False) -> None:
    global _snapshot, _snapshot_loaded_at, _snapshot_loaded
    now = time.monotonic()
    if not force and _snapshot_loaded and now - _snapshot_loaded_at < SNAPSHOT_TTL_SECONDS:
        return
    moment = int(time.time())
    try:
        rows = storage.active_bans(now=moment)
    except Exception:
        # 读库失败时保留旧快照：宁可短暂放行，也不因为一次读失败而把所有人挡在门外。
        log.exception("BAN_SNAPSHOT_LOAD_FAILED")
        _snapshot_loaded_at = now
        _snapshot_loaded = True
        return
    _snapshot = {
        normalize_ip(row["ip"]): (None if row["permanent"] else int(row["expires_ts"]))
        for row in rows
        if normalize_ip(row["ip"])
    }
    _snapshot_loaded_at = now
    _snapshot_loaded = True


def refresh_snapshot() -> None:
    """强制重新装载快照（封禁/解封后调用）。"""
    with _lock:
        _load_snapshot_locked(force=True)


def is_banned(raw: object, *, now: int | None = None) -> dict | None:
    """返回命中信息或 None。命中信息含 ``retry_after``（永久封禁为 None）。"""
    text = normalize_ip(raw)
    if not text:
        return None
    moment = int(time.time()) if now is None else int(now)
    with _lock:
        _load_snapshot_locked()
        if text not in _snapshot:
            return None
        expires_ts = _snapshot[text]
        if expires_ts is not None and int(expires_ts) <= moment:
            # 到期即从快照里摘掉（数据库里的 expire 事件由维护任务补写）。
            _snapshot.pop(text, None)
            return None
    return {
        "ip": text,
        "permanent": expires_ts is None,
        "expires_ts": None if expires_ts is None else int(expires_ts),
        "retry_after": None if expires_ts is None else max(0, int(expires_ts) - moment),
    }


def normalize_duration(seconds: object = None, *, permanent: bool = False, preset: object = None) -> tuple[int | None, bool]:
    """把预设名/秒数解析成 (expires 相对秒数, 是否永久)，非法输入抛 ``BanError``。

    「非法时长」必须显式报错而不是就近收敛：封禁时长被静默改写会让管理员以为
    自己封了 7 天、实际封了 15 分钟。
    """
    if preset not in (None, ""):
        key = str(preset).strip().lower()
        if key not in BAN_PRESET_SECONDS:
            raise BanError("invalid_preset", f"unknown ban preset: {preset}")
        value = BAN_PRESET_SECONDS[key]
        return (None, True) if value is None else (int(value), False)
    if permanent:
        return None, True
    if seconds in (None, ""):
        raise BanError("missing_duration", "ban duration is required")
    try:
        value = int(str(seconds).strip())
    except (TypeError, ValueError, OverflowError):
        raise BanError("invalid_duration", "invalid ban duration") from None
    if value < BAN_MIN_SECONDS:
        raise BanError("invalid_duration", "ban duration must be positive")
    if value > BAN_MAX_SECONDS:
        raise BanError("duration_too_long", f"ban duration above {BAN_MAX_SECONDS} seconds")
    return value, False


def _audit(action: str, ip: str, *, actor: str, detail: dict) -> None:
    try:
        storage.record_audit_event(
            action=action,
            username=str(actor or "")[:_ACTOR_MAX],
            actor_role="admin",
            target_type="ip",
            target_id=ip,
            outcome="success",
            metadata=detail,
        )
    except Exception:  # pragma: no cover - 审计失败不阻断封禁本身
        log.exception("BAN_AUDIT_WRITE_FAILED")


def ban(
    raw: object,
    *,
    seconds: object = None,
    permanent: bool = False,
    preset: object = None,
    reason: str = "",
    actor: str = "",
    now: int | None = None,
) -> dict:
    """新建或修改封禁；重复封禁＝更新期限并留一条 ``update`` 事件。"""
    text = normalize_ip(raw)
    if not text:
        raise BanError("invalid_ip", f"invalid ip address: {raw}")
    protected, why = is_protected_ip(text)
    if protected:
        # 保护地址不能封：环回/本机/可信代理被封会让健康探针或反代一起失效。
        raise BanError("protected_ip", f"refusing to ban protected address ({why})")
    duration, is_permanent = normalize_duration(seconds, permanent=permanent, preset=preset)
    moment = int(time.time()) if now is None else int(now)
    expires_ts = None if is_permanent else moment + int(duration or 0)
    existing = storage.get_active_ban(text, now=moment)
    action = BAN_ACTION_UPDATE if existing else BAN_ACTION_BAN
    row = storage.upsert_ban(
        ip=text,
        expires_ts=expires_ts,
        permanent=is_permanent,
        reason=str(reason or "")[:_REASON_MAX],
        actor=str(actor or "")[:_ACTOR_MAX],
        action=action,
        now=moment,
        detail={"source": "admin"},
    )
    refresh_snapshot()
    _audit(
        "ip_ban_update" if existing else "ip_ban_create",
        text,
        actor=actor,
        detail={
            "action": action,
            "permanent": is_permanent,
            "expires_ts": expires_ts,
            "seconds": None if is_permanent else int(duration or 0),
            "reason": str(reason or "")[:_REASON_MAX],
            "previous_expires_ts": (existing or {}).get("expires_ts"),
        },
    )
    return row


def unban(raw: object, *, actor: str = "", now: int | None = None) -> dict | None:
    """解除封禁；返回解除后的行，未处于封禁状态时返回 None。"""
    text = normalize_ip(raw)
    if not text:
        raise BanError("invalid_ip", f"invalid ip address: {raw}")
    moment = int(time.time()) if now is None else int(now)
    row = storage.revoke_ban(ip=text, actor=str(actor or "")[:_ACTOR_MAX], now=moment)
    refresh_snapshot()
    if row is not None:
        _audit("ip_ban_lift", text, actor=actor, detail={"revoked_ts": moment})
    return row


def list_bans(*, include_inactive: bool = False, limit: int = 50, offset: int = 0, now: int | None = None) -> dict:
    return storage.list_bans(include_inactive=include_inactive, limit=limit, offset=offset, now=now)


def get_ban(raw: object) -> dict | None:
    """按地址读一行（含已解除/已过期的历史行；地址会先规范化）。"""
    text = normalize_ip(raw) or str(raw or "").strip()
    if not text:
        return None
    return storage.get_ban(text)


def list_events(*, ip: str = "", limit: int = 50, offset: int = 0) -> dict:
    return storage.list_ban_events(ip=normalize_ip(ip) or str(ip or ""), limit=limit, offset=offset)


def counters(now: int | None = None) -> dict:
    return storage.ban_counters(now=now)


def expire_due(now: int | None = None) -> int:
    """把已到期的封禁落成 ``expire`` 事件（维护任务调用），返回处理条数。"""
    changed = storage.expire_bans(now=now)
    if changed:
        refresh_snapshot()
    return changed


async def close_ip_connections(raw: object, *, registry=None) -> int:
    """关闭某个来源 IP 的长连接（封禁生效后清理已有连接）。

    只关闭该地址的连接；不解账号、不停投屏会话、不释放控制权——那些是账号/设备维度
    的动作，跟「这个地址不能访问」是两回事（避免一次封禁误伤其他观看端或正在投屏的会话）。
    """
    text = normalize_ip(raw)
    if not text:
        return 0
    if registry is None:
        from .account_access import account_connections

        registry = account_connections
    try:
        return await registry.close_ip_connections(text)
    except Exception:  # pragma: no cover - 清理失败不应让封禁本身失败
        log.exception("BAN_CLOSE_CONNECTIONS_FAILED ip=%s", text)
        return 0


def self_check() -> dict:
    """启动自检信息（供 CLI/诊断输出，不泄露任何凭据）。"""
    with _lock:
        _load_snapshot_locked()
        active = len(_snapshot)
    return {
        "active_bans": active,
        "presets": sorted(BAN_PRESET_SECONDS),
        "max_seconds": BAN_MAX_SECONDS,
        "snapshot_ttl_seconds": SNAPSHOT_TTL_SECONDS,
        "protected_addresses": sorted(_own_addresses()),
    }


__all__ = [
    "BAN_MAX_SECONDS",
    "BAN_PRESET_SECONDS",
    "BanError",
    "ban",
    "close_ip_connections",
    "counters",
    "expire_due",
    "get_ban",
    "is_banned",
    "is_protected_ip",
    "list_bans",
    "list_events",
    "normalize_duration",
    "normalize_ip",
    "refresh_snapshot",
    "self_check",
    "unban",
]
