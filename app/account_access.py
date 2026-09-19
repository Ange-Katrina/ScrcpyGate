import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any, TypeAlias

from starlette.websockets import WebSocketState

from .storage_core import session_token_hash


log = logging.getLogger("webscrcpy.account_access")

ACCOUNT_EXPIRED_CLOSE_CODE = 4403
ACCOUNT_EXPIRED_CLOSE_REASON = "account expired"
SESSION_REVOKED_CLOSE_CODE = 4403
SESSION_REVOKED_CLOSE_REASON = "session revoked"
PERMISSION_REVOKED_CLOSE_CODE = 4403
PERMISSION_REVOKED_CLOSE_REASON = "permission revoked"
# 来源 IP 被封禁：与上面几个 4403 语义一致，客户端见到 4403 会停止重连。
IP_BANNED_CLOSE_CODE = 4403
IP_BANNED_CLOSE_REASON = "ip banned"
DEFAULT_EXPIRATION_CHECK_INTERVAL = 10.0
DEFAULT_CONNECTION_CLOSE_TIMEOUT = 1.0

ExpiredUsernames: TypeAlias = Iterable[str]
ExpiredUsernamesQuery: TypeAlias = Callable[
    [],
    ExpiredUsernames | Awaitable[ExpiredUsernames],
]


@dataclass(frozen=True)
class _RegisteredConnection:
    websocket: Any
    session_id: str | None = None
    device_id: str | None = None
    # 注册时记录来源 IP：封禁某个地址时要在同一个进程里找到它的长连接并关闭。
    # 这里只保存规范化后的字符串（判定与展示都由 ip_ban 负责）。
    source_ip: str = ""


def _state_is_disconnected(state: Any) -> bool:
    if state is WebSocketState.DISCONNECTED:
        return True
    name = str(getattr(state, "name", "") or "").strip().lower()
    value = str(getattr(state, "value", state) or "").strip().lower()
    return name == "disconnected" or value == "disconnected"


def _websocket_is_closed(websocket: Any) -> bool:
    if getattr(websocket, "closed", None) is True:
        return True
    return any(
        _state_is_disconnected(getattr(websocket, attribute, None))
        for attribute in ("application_state", "client_state")
    )


async def _close_websocket(
    websocket: Any,
    *,
    code: int,
    reason: str,
    timeout: float,
) -> bool:
    if _websocket_is_closed(websocket):
        return True

    async def close() -> None:
        try:
            await websocket.close(code=code, reason=reason)
        except TypeError:
            # Some WebSocket-compatible test and upstream objects only accept code.
            await websocket.close(code=code)

    try:
        await asyncio.wait_for(close(), timeout=timeout)
        return True
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.debug("ACCOUNT_CONNECTION_CLOSE_FAILED error=%s", exc)
        return False


class UserConnectionRegistry:
    """Track authenticated WebSockets so an account can be revoked immediately."""

    def __init__(self, *, close_timeout: float = DEFAULT_CONNECTION_CLOSE_TIMEOUT) -> None:
        if close_timeout <= 0:
            raise ValueError("close_timeout must be positive")
        self._close_timeout = float(close_timeout)
        self._connections: dict[str, set[_RegisteredConnection]] = {}
        self._pending_revocations: dict[str, set[_RegisteredConnection]] = {}
        self._lock = asyncio.Lock()

    async def register(
        self,
        username: str,
        websocket: Any,
        session_id: str | None = None,
        *,
        device_id: str | None = None,
        source_ip: str | None = None,
    ) -> None:
        normalized = self._normalize_username(username)
        if websocket is None:
            raise ValueError("websocket is required")
        from .ip_ban import normalize_ip

        registration = _RegisteredConnection(
            websocket,
            self._normalize_session_id(session_id),
            self._normalize_device_id(device_id),
            normalize_ip(source_ip),
        )
        async with self._lock:
            self._connections.setdefault(normalized, set()).add(registration)

    async def unregister(self, username: str, websocket: Any, session_id: str | None = None) -> None:
        normalized = self._normalize_username(username)
        normalized_session_id = self._normalize_session_id(session_id)
        async with self._lock:
            connections = self._connections.get(normalized)
            if connections:
                to_remove = {
                    registration
                    for registration in connections
                    if registration.websocket is websocket
                    and (normalized_session_id is None or registration.session_id == normalized_session_id)
                }
                connections.difference_update(to_remove)
                if not connections:
                    self._connections.pop(normalized, None)
            pending = self._pending_revocations.get(normalized)
            if pending:
                to_remove = {
                    registration
                    for registration in pending
                    if registration.websocket is websocket
                    and (normalized_session_id is None or registration.session_id == normalized_session_id)
                }
                pending.difference_update(to_remove)
                if not pending:
                    self._pending_revocations.pop(normalized, None)

    async def close_user_connections(
        self,
        username: str,
        *,
        code: int = ACCOUNT_EXPIRED_CLOSE_CODE,
        reason: str = ACCOUNT_EXPIRED_CLOSE_REASON,
    ) -> int:
        normalized = self._normalize_username(username)
        batches = await self._snapshot_for_revocation((normalized,))
        connections = batches.get(normalized, ())
        closed = await self._close_connections(connections, code=code, reason=reason)
        await self._discard_closed({
            normalized: tuple(registration for registration, success in zip(connections, closed) if success)
        })
        return len(connections)

    async def close_session_connections(
        self,
        username: str,
        session_id: str,
        *,
        code: int = ACCOUNT_EXPIRED_CLOSE_CODE,
        reason: str = ACCOUNT_EXPIRED_CLOSE_REASON,
    ) -> int:
        normalized = self._normalize_username(username)
        normalized_session_id = self._normalize_session_id(session_id)
        if normalized_session_id is None:
            raise ValueError("session_id is required")
        batches = await self._snapshot_for_revocation((normalized,), session_id=normalized_session_id)
        connections = batches.get(normalized, ())
        closed = await self._close_connections(connections, code=code, reason=reason)
        await self._discard_closed({
            normalized: tuple(registration for registration, success in zip(connections, closed) if success)
        })
        return len(connections)

    async def close_device_connections(
        self,
        username: str,
        device_id: str,
        *,
        code: int = PERMISSION_REVOKED_CLOSE_CODE,
        reason: str = PERMISSION_REVOKED_CLOSE_REASON,
    ) -> int:
        """Close only sockets registered for one user's device."""
        normalized = self._normalize_username(username)
        normalized_device_id = self._normalize_device_id(device_id)
        if normalized_device_id is None:
            raise ValueError("device_id is required")
        batches = await self._snapshot_for_revocation(
            (normalized,),
            device_id=normalized_device_id,
        )
        connections = batches.get(normalized, ())
        closed = await self._close_connections(connections, code=code, reason=reason)
        await self._discard_closed({
            normalized: tuple(registration for registration, success in zip(connections, closed) if success)
        })
        return len(connections)

    async def close_many(
        self,
        usernames: Iterable[str],
        *,
        code: int = ACCOUNT_EXPIRED_CLOSE_CODE,
        reason: str = ACCOUNT_EXPIRED_CLOSE_REASON,
    ) -> int:
        normalized = tuple(dict.fromkeys(self._normalize_username(username) for username in usernames))
        if not normalized:
            return 0
        batches = await self._snapshot_for_revocation(normalized)
        indexed_connections = tuple(
            (username, websocket)
            for username, connections in batches.items()
            for websocket in connections
        )
        closed = await self._close_connections(
            (registration for _username, registration in indexed_connections),
            code=code,
            reason=reason,
        )
        successful: dict[str, list[_RegisteredConnection]] = {}
        for (username, registration), success in zip(indexed_connections, closed):
            if success:
                successful.setdefault(username, []).append(registration)
        await self._discard_closed(successful)
        return len(indexed_connections)

    async def retry_pending_revocations(
        self,
        *,
        code: int = ACCOUNT_EXPIRED_CLOSE_CODE,
        reason: str = ACCOUNT_EXPIRED_CLOSE_REASON,
    ) -> int:
        async with self._lock:
            batches = {
                username: tuple(connections)
                for username, connections in self._pending_revocations.items()
                if connections
            }
        indexed_connections = tuple(
            (username, websocket)
            for username, connections in batches.items()
            for websocket in connections
        )
        closed = await self._close_connections(
            (registration for _username, registration in indexed_connections),
            code=code,
            reason=reason,
        )
        successful: dict[str, list[_RegisteredConnection]] = {}
        for (username, registration), success in zip(indexed_connections, closed):
            if success:
                successful.setdefault(username, []).append(registration)
        await self._discard_closed(successful)
        return len(indexed_connections)

    async def enforce_ip_bans(self) -> None:
        from . import ip_ban

        async with self._lock:
            sources = {entry.source_ip for entries in self._connections.values() for entry in entries if entry.source_ip}
        def banned_sources():
            return [source for source in sources if ip_ban.is_banned(source)]
        for source in await asyncio.to_thread(banned_sources):
            await self.close_ip_connections(source)

    async def close_ip_connections(
        self,
        source_ip: str,
        *,
        code: int = IP_BANNED_CLOSE_CODE,
        reason: str = IP_BANNED_CLOSE_REASON,
    ) -> int:
        """按来源 IP 关闭长连接（封禁生效后清理已有连接）。

        只关闭来源 IP 匹配的连接：同一账户从别的地址连进来的观看端不受影响
        （任务书 A10「不影响其他观看端」）。返回**匹配到**的连接数，
        不管关闭调用本身是否成功——调用方据此提示「已清理 N 条」。
        """
        target = str(source_ip or "").strip()
        if not target:
            return 0
        async with self._lock:
            batches = {
                username: tuple(
                    registration
                    for registration in connections
                    if registration.source_ip and registration.source_ip == target
                )
                for username, connections in self._connections.items()
            }
            batches = {username: connections for username, connections in batches.items() if connections}
            for username, connections in batches.items():
                self._pending_revocations.setdefault(username, set()).update(connections)
        indexed = tuple(
            (username, registration)
            for username, connections in batches.items()
            for registration in connections
        )
        if not indexed:
            return 0
        closed = await self._close_connections(
            (registration for _username, registration in indexed),
            code=code,
            reason=reason,
        )
        successful: dict[str, list[_RegisteredConnection]] = {}
        for (username, registration), success in zip(indexed, closed):
            if success:
                successful.setdefault(username, []).append(registration)
        await self._discard_closed(successful)
        return len(indexed)

    async def _snapshot_for_revocation(
        self,
        usernames: Iterable[str],
        *,
        session_id: str | None = None,
        device_id: str | None = None,
    ) -> dict[str, tuple[_RegisteredConnection, ...]]:
        normalized_device_id = self._normalize_device_id(device_id)
        async with self._lock:
            batches = {}
            for username in usernames:
                connections = tuple(
                    registration
                    for registration in self._connections.get(username, ())
                    if session_id is None or registration.session_id == session_id
                    if normalized_device_id is None or registration.device_id == normalized_device_id
                )
                if connections:
                    self._pending_revocations.setdefault(username, set()).update(connections)
                batches[username] = connections
            return batches

    async def _discard_closed(self, batches: dict[str, Iterable[_RegisteredConnection]]) -> None:
        async with self._lock:
            for username, closed in batches.items():
                connections = self._connections.get(username)
                if not connections:
                    continue
                connections.difference_update(closed)
                if not connections:
                    self._connections.pop(username, None)
                pending = self._pending_revocations.get(username)
                if pending:
                    pending.difference_update(closed)
                    if not pending:
                        self._pending_revocations.pop(username, None)

    async def _close_connections(
        self,
        connections: Iterable[_RegisteredConnection],
        *,
        code: int,
        reason: str,
    ) -> tuple[bool, ...]:
        return tuple(await asyncio.gather(
            *(
                _close_websocket(
                    registration.websocket,
                    code=code,
                    reason=reason,
                    timeout=self._close_timeout,
                )
                for registration in connections
            )
        ))

    @staticmethod
    def _normalize_username(username: str) -> str:
        normalized = str(username or "").strip()
        if not normalized:
            raise ValueError("username is required")
        return normalized

    @staticmethod
    def _normalize_session_id(session_id: str | None) -> str | None:
        """注册表里统一使用会话哈希作为键。

        库里的 ``sessions.sid`` 已经是 sha256(令牌)，所以连接注册表也用同一个值：
        这样"按会话踢出"（登录会话列表里的那一行）才能真正把该会话正在投屏的
        WebSocket 一并关掉。传 null 表示"不限会话"（按用户整体关闭）。
        """
        normalized = str(session_id or "").strip()
        return session_token_hash(normalized) if normalized else None

    @staticmethod
    def _normalize_device_id(device_id: str | None) -> str | None:
        normalized = str(device_id or "").strip()
        return normalized or None


account_connections = UserConnectionRegistry()


async def revoke_expired_user_connections(
    expired_usernames_query: ExpiredUsernamesQuery,
    *,
    registry: UserConnectionRegistry = account_connections,
    code: int = ACCOUNT_EXPIRED_CLOSE_CODE,
    reason: str = ACCOUNT_EXPIRED_CLOSE_REASON,
) -> int:
    """Query expired accounts without blocking the event loop, then revoke them."""

    def query_in_worker() -> tuple[str, ...] | Awaitable[ExpiredUsernames]:
        result = expired_usernames_query()
        if inspect.isawaitable(result):
            return result
        return tuple(result or ())

    result = await asyncio.to_thread(query_in_worker)
    if inspect.isawaitable(result):
        result = await result
    return await registry.close_many(result or (), code=code, reason=reason)


async def account_expiration_monitor(
    expired_usernames_query: ExpiredUsernamesQuery,
    *,
    registry: UserConnectionRegistry = account_connections,
    interval: float = DEFAULT_EXPIRATION_CHECK_INTERVAL,
    code: int = ACCOUNT_EXPIRED_CLOSE_CODE,
    reason: str = ACCOUNT_EXPIRED_CLOSE_REASON,
    maintenance: Callable[[], object] | None = None,
) -> None:
    """Periodically revoke WebSockets belonging to expired accounts.

    ``maintenance`` (the storage retention sweep) runs on the same cadence; it
    is executed in a worker thread because it performs blocking SQLite deletes.
    """

    if interval <= 0:
        raise ValueError("interval must be positive")
    while True:
        try:
            await revoke_expired_user_connections(
                expired_usernames_query,
                registry=registry,
                code=code,
                reason=reason,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("ACCOUNT_EXPIRATION_MONITOR_ERROR")
        try:
            await registry.enforce_ip_bans()
            await registry.retry_pending_revocations(code=code, reason=reason)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("ACCOUNT_REVOCATION_RETRY_ERROR")
        if maintenance is not None:
            try:
                await asyncio.to_thread(maintenance)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("STORAGE_MAINTENANCE_ERROR")
        await asyncio.sleep(interval)
