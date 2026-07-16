import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable, Iterable
from typing import Any, TypeAlias

from starlette.websockets import WebSocketState


log = logging.getLogger("webscrcpy.account_access")

ACCOUNT_EXPIRED_CLOSE_CODE = 4403
ACCOUNT_EXPIRED_CLOSE_REASON = "account expired"
DEFAULT_EXPIRATION_CHECK_INTERVAL = 10.0
DEFAULT_CONNECTION_CLOSE_TIMEOUT = 1.0

ExpiredUsernames: TypeAlias = Iterable[str]
ExpiredUsernamesQuery: TypeAlias = Callable[
    [],
    ExpiredUsernames | Awaitable[ExpiredUsernames],
]


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
        self._connections: dict[str, set[Any]] = {}
        self._pending_revocations: dict[str, set[Any]] = {}
        self._lock = asyncio.Lock()

    async def register(self, username: str, websocket: Any) -> None:
        normalized = self._normalize_username(username)
        if websocket is None:
            raise ValueError("websocket is required")
        async with self._lock:
            self._connections.setdefault(normalized, set()).add(websocket)

    async def unregister(self, username: str, websocket: Any) -> None:
        normalized = self._normalize_username(username)
        async with self._lock:
            connections = self._connections.get(normalized)
            if not connections:
                return
            connections.discard(websocket)
            if not connections:
                self._connections.pop(normalized, None)
            pending = self._pending_revocations.get(normalized)
            if pending:
                pending.discard(websocket)
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
            normalized: tuple(websocket for websocket, success in zip(connections, closed) if success)
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
            (websocket for _username, websocket in indexed_connections),
            code=code,
            reason=reason,
        )
        successful: dict[str, list[Any]] = {}
        for (username, websocket), success in zip(indexed_connections, closed):
            if success:
                successful.setdefault(username, []).append(websocket)
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
            (websocket for _username, websocket in indexed_connections),
            code=code,
            reason=reason,
        )
        successful: dict[str, list[Any]] = {}
        for (username, websocket), success in zip(indexed_connections, closed):
            if success:
                successful.setdefault(username, []).append(websocket)
        await self._discard_closed(successful)
        return len(indexed_connections)

    async def _snapshot_for_revocation(self, usernames: Iterable[str]) -> dict[str, tuple[Any, ...]]:
        async with self._lock:
            batches = {}
            for username in usernames:
                connections = tuple(self._connections.get(username, ()))
                if connections:
                    self._pending_revocations.setdefault(username, set()).update(connections)
                batches[username] = connections
            return batches

    async def _discard_closed(self, batches: dict[str, Iterable[Any]]) -> None:
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

    async def _close_connections(self, connections: Iterable[Any], *, code: int, reason: str) -> tuple[bool, ...]:
        return tuple(await asyncio.gather(
            *(
                _close_websocket(
                    websocket,
                    code=code,
                    reason=reason,
                    timeout=self._close_timeout,
                )
                for websocket in connections
            )
        ))

    @staticmethod
    def _normalize_username(username: str) -> str:
        normalized = str(username or "").strip()
        if not normalized:
            raise ValueError("username is required")
        return normalized


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
) -> None:
    """Periodically revoke WebSockets belonging to expired accounts."""

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
            await registry.retry_pending_revocations(code=code, reason=reason)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("ACCOUNT_REVOCATION_RETRY_ERROR")
        await asyncio.sleep(interval)
