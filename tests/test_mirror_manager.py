import asyncio
import base64
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import mirror
from app.mirror import ClientSession, MirrorManager


class FakeControlWebSocket:
    def __init__(self, messages=None):
        self.messages = list(messages or [])
        self.sent = []
        self.accepted = False

    async def accept(self):
        self.accepted = True

    async def receive(self):
        if self.messages:
            return self.messages.pop(0)
        raise mirror.WebSocketDisconnect()

    async def send_json(self, payload):
        self.sent.append(payload)


class FakeMirrorSession:
    def __init__(self, running=True, clients=0):
        self.running = running
        self.clients = {str(i): object() for i in range(clients)}
        self.stopped = False

    async def stop(self):
        self.stopped = True
        self.running = False
        return True

    def snapshot(self):
        return {"running": self.running, "clients": len(self.clients)}


class MirrorManagerTests(unittest.TestCase):
    def test_stop_other_no_client_sessions_keeps_current_and_viewed_sessions(self):
        async def run():
            manager = MirrorManager()
            broadcasts = []

            async def broadcast(message):
                broadcasts.append(message)

            manager.broadcast = broadcast
            manager.sessions = {
                "keep": FakeMirrorSession(running=True, clients=0),
                "idle": FakeMirrorSession(running=True, clients=0),
                "viewed": FakeMirrorSession(running=True, clients=1),
                "denied": FakeMirrorSession(running=True, clients=0),
            }

            stopped = await manager.stop_other_no_client_sessions("keep", ["keep", "idle", "viewed"])

            self.assertEqual(stopped, ["idle"])
            self.assertFalse(manager.sessions["idle"].running)
            self.assertTrue(manager.sessions["idle"].stopped)
            self.assertTrue(manager.sessions["keep"].running)
            self.assertTrue(manager.sessions["viewed"].running)
            self.assertTrue(manager.sessions["denied"].running)
            self.assertEqual(len(broadcasts), 1)
            self.assertEqual(broadcasts[0]["reason"], "switch")

        asyncio.run(run())

    def test_client_queue_soft_limit_drops_until_next_keyframe(self):
        client = ClientSession("client", "user", None)
        client.queue = asyncio.Queue(maxsize=4)
        client.needs_keyframe = False

        client.push_frame(b"p1")
        client.push_frame(b"p2")
        client.push_frame(b"p3")
        client.push_frame(b"p4")
        self.assertEqual(client.drops, 1)
        self.assertTrue(client.needs_keyframe)
        self.assertEqual(client.queue.qsize(), 0)

        client.push_frame(b"p5")
        self.assertEqual(client.queue.qsize(), 0)

        client.push_frame(b"idr", keyframe=True, config=b"cfg")
        self.assertFalse(client.needs_keyframe)
        self.assertEqual(client.queue.get_nowait(), b"cfgidr")


class MirrorControlTests(unittest.TestCase):
    def test_control_socket_passes_client_id_for_binary_messages(self):
        async def run():
            websocket = FakeControlWebSocket([{"bytes": b"payload"}])
            user = {"username": "alice", "role": "user"}
            handle_bytes = AsyncMock()
            broadcast = AsyncMock()
            with (
                patch.object(mirror.storage, "user_can", return_value=True),
                patch.object(mirror.storage, "get_lock", return_value=None),
                patch.object(mirror.storage, "release_lock", return_value=True) as release_lock,
                patch.object(mirror.uuid, "uuid4", return_value="client-raw"),
                patch.object(mirror, "handle_control_bytes", new=handle_bytes),
                patch.object(mirror.manager, "broadcast", new=broadcast),
            ):
                await mirror.control_socket(websocket, user, "dev1")

            self.assertTrue(websocket.accepted)
            handle_bytes.assert_awaited_once_with(websocket, user, "dev1", "client-raw", b"payload")
            release_lock.assert_called_once_with("dev1", "alice", force=False, client_id="client-raw")

        asyncio.run(run())

    def test_control_base64_passes_client_id(self):
        async def run():
            websocket = FakeControlWebSocket()
            user = {"username": "alice", "role": "user"}
            handle_bytes = AsyncMock()
            encoded = base64.b64encode(b"payload").decode("ascii")
            with patch.object(mirror, "handle_control_bytes", new=handle_bytes):
                await mirror.handle_control_text(
                    websocket,
                    user,
                    "dev1",
                    "client-base64",
                    json.dumps({"type": "control_base64", "data": encoded}),
                )

            handle_bytes.assert_awaited_once_with(websocket, user, "dev1", "client-base64", b"payload")

        asyncio.run(run())

    def test_invalid_control_base64_does_not_reach_control_handler_or_renew(self):
        async def run():
            user = {"username": "alice", "role": "user"}
            for invalid in ("", None, 123, "%%%", "cGF5bG9hZA"):
                with self.subTest(payload=invalid):
                    websocket = FakeControlWebSocket()
                    handle_bytes = AsyncMock()
                    with (
                        patch.object(mirror, "handle_control_bytes", new=handle_bytes),
                        patch.object(mirror.storage, "renew_lock") as renew_lock,
                    ):
                        await mirror.handle_control_text(
                            websocket,
                            user,
                            "dev1",
                            "client-base64",
                            json.dumps({"type": "control_base64", "data": invalid}),
                        )

                    handle_bytes.assert_not_awaited()
                    renew_lock.assert_not_called()
                    self.assertEqual(
                        websocket.sent,
                        [{"type": "error", "error": "invalid control payload"}],
                    )

        asyncio.run(run())

    def test_control_keepalive_renews_exact_owner_and_reports_lock(self):
        async def run():
            websocket = FakeControlWebSocket()
            user = {"username": "alice", "role": "user"}
            lock = {"device_id": "dev1", "username": "alice", "client_id": "client-a", "expires_at": 1090}
            with (
                patch.object(mirror.storage, "renew_lock", return_value=True) as renew_lock,
                patch.object(mirror.storage, "get_lock", return_value=lock),
            ):
                await mirror.handle_control_text(
                    websocket,
                    user,
                    "dev1",
                    "client-a",
                    json.dumps({"type": "control_keepalive"}),
                )

            renew_lock.assert_called_once_with("dev1", "alice", "client-a", ttl_seconds=90)
            self.assertEqual(
                websocket.sent,
                [{"type": "control_lock", "ok": True, "owner": "alice", "expires_at": 1090, "lock": lock}],
            )

        asyncio.run(run())

    def test_control_bytes_renew_failure_does_not_send_control(self):
        async def run():
            websocket = FakeControlWebSocket()
            user = {"username": "alice", "role": "user"}
            current = {"device_id": "dev1", "username": "bob", "client_id": "client-b", "expires_at": 1090}
            get_session = AsyncMock()
            with (
                patch.object(mirror.storage, "renew_lock", return_value=False) as renew_lock,
                patch.object(mirror.storage, "get_lock", return_value=current),
                patch.object(mirror.manager, "get_or_create", new=get_session),
            ):
                await mirror.handle_control_bytes(websocket, user, "dev1", "client-a", b"payload")

            renew_lock.assert_called_once_with("dev1", "alice", "client-a", ttl_seconds=90)
            get_session.assert_not_awaited()
            self.assertEqual(websocket.sent, [{"type": "control_lock", "ok": False, "lock": current}])

        asyncio.run(run())

    def test_empty_control_bytes_are_rejected_before_renewal(self):
        async def run():
            websocket = FakeControlWebSocket()
            user = {"username": "alice", "role": "user"}
            get_session = AsyncMock()
            with (
                patch.object(mirror.storage, "renew_lock") as renew_lock,
                patch.object(mirror.manager, "get_or_create", new=get_session),
            ):
                await mirror.handle_control_bytes(websocket, user, "dev1", "client-a", b"")

            renew_lock.assert_not_called()
            get_session.assert_not_awaited()
            self.assertEqual(
                websocket.sent,
                [{"type": "error", "error": "invalid control payload"}],
            )

        asyncio.run(run())

    def test_control_bytes_renews_before_sending_control(self):
        async def run():
            websocket = FakeControlWebSocket()
            user = {"username": "alice", "role": "user"}
            events = []

            def renew_lock(*args, **kwargs):
                events.append("renew")
                return True

            def send_control(payload):
                events.append(("send", payload))
                return True

            session = SimpleNamespace(send_control=Mock(side_effect=send_control), last_error="")
            with (
                patch.object(mirror.storage, "renew_lock", side_effect=renew_lock) as renew,
                patch.object(mirror.manager, "get_or_create", new=AsyncMock(return_value=session)),
            ):
                await mirror.handle_control_bytes(websocket, user, "dev1", "client-a", b"payload")

            renew.assert_called_once_with("dev1", "alice", "client-a", ttl_seconds=90)
            self.assertEqual(events, ["renew", ("send", b"payload")])
            self.assertEqual(websocket.sent, [])

        asyncio.run(run())


class MirrorControlTemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = (ROOT / "static" / "js" / "mirror.js").read_text(encoding="utf-8")

    def test_control_keepalive_is_single_30_second_timer(self):
        self.assertIn("controlKeepaliveTimer:null", self.template)
        self.assertIn("function startControlKeepalive()", self.template)
        self.assertIn("if (state.controlKeepaliveTimer) return;", self.template)
        self.assertIn("JSON.stringify({type:'control_keepalive'})", self.template)
        self.assertIn("}, 30000);", self.template)

    def test_control_ownership_failures_stop_keepalive(self):
        self.assertIn("function setControlOwnership(ok)", self.template)
        self.assertIn("if (msg.ok !== undefined) setControlOwnership(msg.ok);", self.template)
        self.assertIn("if (releasing) setControlOwnership(false);", self.template)
        self.assertGreaterEqual(self.template.count("setControlOwnership(false)"), 4)


if __name__ == "__main__":
    unittest.main()
