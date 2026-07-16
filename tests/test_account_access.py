import asyncio
import threading
import unittest

from starlette.websockets import WebSocketState

from app.account_access import (
    ACCOUNT_EXPIRED_CLOSE_CODE,
    ACCOUNT_EXPIRED_CLOSE_REASON,
    UserConnectionRegistry,
    account_expiration_monitor,
    revoke_expired_user_connections,
)


class FakeWebSocket:
    def __init__(self) -> None:
        self.application_state = WebSocketState.CONNECTED
        self.close_calls: list[tuple[int, str]] = []
        self.closed_event = asyncio.Event()

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.close_calls.append((code, reason))
        self.application_state = WebSocketState.DISCONNECTED
        self.closed_event.set()


class CodeOnlyWebSocket:
    def __init__(self) -> None:
        self.close_calls: list[int] = []

    async def close(self, code: int = 1000) -> None:
        self.close_calls.append(code)


class FailedWebSocket:
    async def close(self, code: int = 1000, reason: str = "") -> None:
        raise RuntimeError("already gone")


class FlakyWebSocket(FakeWebSocket):
    def __init__(self) -> None:
        super().__init__()
        self.attempts = 0

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.attempts += 1
        if self.attempts == 1:
            raise RuntimeError("temporary close failure")
        await super().close(code=code, reason=reason)


class ClosedWebSocket:
    application_state = WebSocketState.DISCONNECTED

    async def close(self, code: int = 1000, reason: str = "") -> None:
        raise AssertionError("an already closed socket must not be closed again")


class HangingWebSocket:
    async def close(self, code: int = 1000, reason: str = "") -> None:
        await asyncio.Event().wait()


class BlockingWebSocket(FakeWebSocket):
    def __init__(self) -> None:
        super().__init__()
        self.close_started = asyncio.Event()
        self.release_close = asyncio.Event()

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.close_started.set()
        await self.release_close.wait()
        await super().close(code=code, reason=reason)


class UserConnectionRegistryTests(unittest.IsolatedAsyncioTestCase):
    async def test_register_is_idempotent_and_unregister_removes_empty_user(self):
        registry = UserConnectionRegistry()
        websocket = FakeWebSocket()

        await registry.register("alice", websocket)
        await registry.register("alice", websocket)
        await registry.unregister("alice", websocket)

        self.assertEqual(await registry.close_user_connections("alice"), 0)
        self.assertEqual(websocket.close_calls, [])

    async def test_close_user_detaches_and_closes_only_that_users_connections(self):
        registry = UserConnectionRegistry()
        alice_first = FakeWebSocket()
        alice_second = FakeWebSocket()
        bob = FakeWebSocket()
        for websocket in (alice_first, alice_second):
            await registry.register("alice", websocket)
        await registry.register("bob", bob)

        closed = await registry.close_user_connections("alice")

        self.assertEqual(closed, 2)
        self.assertEqual(
            alice_first.close_calls,
            [(ACCOUNT_EXPIRED_CLOSE_CODE, ACCOUNT_EXPIRED_CLOSE_REASON)],
        )
        self.assertEqual(
            alice_second.close_calls,
            [(ACCOUNT_EXPIRED_CLOSE_CODE, ACCOUNT_EXPIRED_CLOSE_REASON)],
        )
        self.assertEqual(bob.close_calls, [])
        self.assertEqual(await registry.close_user_connections("alice"), 0)
        self.assertEqual(await registry.close_user_connections("bob"), 1)

    async def test_close_many_deduplicates_users_and_retries_failed_socket(self):
        registry = UserConnectionRegistry()
        working = FakeWebSocket()
        code_only = CodeOnlyWebSocket()
        failed = FailedWebSocket()
        closed = ClosedWebSocket()
        for websocket in (working, code_only, failed, closed):
            await registry.register("alice", websocket)

        count = await registry.close_many(("alice", "alice"), code=4401, reason="revoked")

        self.assertEqual(count, 4)
        self.assertEqual(working.close_calls, [(4401, "revoked")])
        self.assertEqual(code_only.close_calls, [4401])
        self.assertEqual(await registry.close_user_connections("alice"), 1)

    async def test_close_timeout_cannot_stall_revocation_and_remains_retryable(self):
        registry = UserConnectionRegistry(close_timeout=0.01)
        await registry.register("alice", HangingWebSocket())

        count = await asyncio.wait_for(registry.close_user_connections("alice"), timeout=1)

        self.assertEqual(count, 1)
        self.assertEqual(
            await asyncio.wait_for(registry.close_user_connections("alice"), timeout=1),
            1,
        )

    async def test_failed_connection_stays_pending_until_independent_retry_succeeds(self):
        registry = UserConnectionRegistry()
        websocket = FlakyWebSocket()
        await registry.register("alice", websocket)

        self.assertEqual(await registry.close_user_connections("alice"), 1)
        self.assertEqual(websocket.attempts, 1)
        self.assertEqual(await registry.retry_pending_revocations(), 1)
        self.assertEqual(websocket.attempts, 2)
        self.assertEqual(await registry.retry_pending_revocations(), 0)
        self.assertEqual(await registry.close_user_connections("alice"), 0)

    async def test_close_many_preserves_new_registration_while_snapshot_closes(self):
        registry = UserConnectionRegistry()
        old = BlockingWebSocket()
        new = FakeWebSocket()
        await registry.register("alice", old)

        closing = asyncio.create_task(registry.close_user_connections("alice"))
        await asyncio.wait_for(old.close_started.wait(), timeout=1)
        await registry.register("alice", new)
        old.release_close.set()
        await closing

        self.assertEqual(await registry.close_user_connections("alice"), 1)
        self.assertEqual(new.close_calls, [(ACCOUNT_EXPIRED_CLOSE_CODE, ACCOUNT_EXPIRED_CLOSE_REASON)])

    async def test_sync_expiration_query_runs_outside_event_loop_thread(self):
        registry = UserConnectionRegistry()
        websocket = FakeWebSocket()
        await registry.register("alice", websocket)
        loop_thread = threading.get_ident()
        query_threads: list[int] = []

        def expired_usernames():
            query_threads.append(threading.get_ident())
            return ["alice"]

        count = await revoke_expired_user_connections(expired_usernames, registry=registry)

        self.assertEqual(count, 1)
        self.assertNotEqual(query_threads, [loop_thread])

    async def test_async_expiration_query_is_supported(self):
        registry = UserConnectionRegistry()
        websocket = FakeWebSocket()
        await registry.register("alice", websocket)

        async def expired_usernames():
            await asyncio.sleep(0)
            return ["alice"]

        self.assertEqual(
            await revoke_expired_user_connections(expired_usernames, registry=registry),
            1,
        )

    async def test_monitor_recovers_after_query_error_and_is_cancellable(self):
        registry = UserConnectionRegistry()
        websocket = FakeWebSocket()
        await registry.register("alice", websocket)
        calls = 0

        def expired_usernames():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("temporary database failure")
            return ["alice"]

        with self.assertLogs("webscrcpy.account_access", level="ERROR") as logs:
            task = asyncio.create_task(
                account_expiration_monitor(expired_usernames, registry=registry, interval=0.01)
            )
            try:
                await asyncio.wait_for(websocket.closed_event.wait(), timeout=1)
                await asyncio.sleep(0.02)
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task

        self.assertGreaterEqual(calls, 2)
        self.assertTrue(any("ACCOUNT_EXPIRATION_MONITOR_ERROR" in line for line in logs.output))

    async def test_monitor_retries_pending_connection_even_when_expiration_query_fails(self):
        registry = UserConnectionRegistry()
        websocket = FlakyWebSocket()
        await registry.register("deleted-user", websocket)
        self.assertEqual(await registry.close_user_connections("deleted-user"), 1)

        def unavailable_query():
            raise RuntimeError("database unavailable")

        with self.assertLogs("webscrcpy.account_access", level="ERROR"):
            task = asyncio.create_task(
                account_expiration_monitor(unavailable_query, registry=registry, interval=0.01)
            )
            try:
                await asyncio.wait_for(websocket.closed_event.wait(), timeout=1)
                await asyncio.sleep(0.02)
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task

        self.assertEqual(websocket.attempts, 2)
        self.assertEqual(await registry.retry_pending_revocations(), 0)


if __name__ == "__main__":
    unittest.main()
