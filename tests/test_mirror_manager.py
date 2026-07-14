import asyncio
import base64
import json
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import mirror
from app.mirror import ClientSession, ControlLeaseState, EventClient, MirrorManager, MirrorSession


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


class FakeEventWebSocket:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail
        self.closed = False

    async def send_text(self, payload):
        if self.fail:
            raise RuntimeError("disconnected")
        self.sent.append(json.loads(payload))

    async def close(self, code=1000):
        self.closed = True


class FakeEventRegistrationWebSocket:
    def __init__(self):
        self.sent = []

    async def send_json(self, payload):
        self.sent.append(("json", payload))

    async def send_text(self, payload):
        self.sent.append(("text", json.loads(payload)))

    async def receive_text(self):
        raise mirror.WebSocketDisconnect()


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
    def test_exposed_video_snapshot_uses_public_session_sanitizer(self):
        session = SimpleNamespace(
            device_id="real-device",
            snapshot=lambda: {
                "device_id": "real-device",
                "running": True,
                "last_error": "192.0.2.10:5555 offline",
                "adb": {"address": "192.0.2.10:5555", "detail": "private", "state": "offline"},
            },
        )

        payload = mirror.exposed_snapshot(session, "dev_public")

        self.assertEqual(payload["device_id"], "dev_public")
        self.assertEqual(payload["last_error"], "设备视频流不可用")
        self.assertNotIn("address", payload["adb"])
        self.assertEqual(payload["adb"]["detail"], "ADB 连接不可用")
        direct_payload = mirror.exposed_snapshot(session, "real-device")
        self.assertNotIn("address", direct_payload["adb"])
        self.assertNotIn("192.0.2.10", json.dumps(direct_payload, ensure_ascii=False))

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

    def test_device_event_is_permission_filtered_and_uses_public_id(self):
        async def run():
            manager = MirrorManager()
            allowed = FakeEventWebSocket()
            denied = FakeEventWebSocket()
            manager.events = {
                "allowed": EventClient("alice", allowed, ready=True),
                "denied": EventClient("bob", denied, ready=True),
            }
            message = {
                "type": "mirror_status",
                "device_id": "real-device",
                "session": {
                    "device_id": "real-device",
                    "last_error": "192.0.2.10:5555 failed",
                    "adb": {
                        "device_id": "real-device",
                        "address": "192.0.2.10:5555",
                        "detail": "192.0.2.10:5555 device",
                        "state": "online",
                        "ok": True,
                    },
                    "control_lock": {"device_id": "real-device", "username": "alice", "client_id": "private-client"},
                },
            }

            with (
                patch.object(mirror.storage, "user_can", side_effect=lambda username, *_: username == "alice") as user_can,
                patch.object(mirror.storage, "public_device_id", return_value="dev_public") as public_device_id,
            ):
                await manager.broadcast(message)

            self.assertEqual(len(allowed.sent), 1)
            self.assertEqual(denied.sent, [])
            self.assertEqual(allowed.sent[0]["device_id"], "dev_public")
            self.assertEqual(allowed.sent[0]["session"]["device_id"], "dev_public")
            self.assertEqual(allowed.sent[0]["session"]["control_lock"]["device_id"], "dev_public")
            self.assertEqual(allowed.sent[0]["session"]["adb"]["device_id"], "dev_public")
            self.assertEqual(allowed.sent[0]["session"]["adb"]["state"], "online")
            encoded = json.dumps(allowed.sent[0], ensure_ascii=False)
            self.assertNotIn("192.0.2.10", encoded)
            self.assertNotIn("private-client", encoded)
            self.assertEqual(allowed.sent[0]["session"]["last_error"], "设备视频流不可用")
            self.assertEqual(message["device_id"], "real-device")
            self.assertEqual(message["session"]["device_id"], "real-device")
            public_device_id.assert_called_once_with("real-device")
            user_can.assert_has_calls(
                [
                    unittest.mock.call("alice", "real-device", "view"),
                    unittest.mock.call("bob", "real-device", "view"),
                ],
                any_order=True,
            )

        asyncio.run(run())

    def test_device_event_rechecks_permission_on_every_broadcast(self):
        async def run():
            manager = MirrorManager()
            websocket = FakeEventWebSocket()
            manager.events = {"client": EventClient("alice", websocket, ready=True)}
            message = {"type": "control_lock", "device_id": "real-device", "lock": None}

            with (
                patch.object(mirror.storage, "user_can", side_effect=[True, False]) as user_can,
                patch.object(mirror.storage, "public_device_id", return_value="dev_public"),
            ):
                await manager.broadcast(message)
                await manager.broadcast(message)

            self.assertEqual(len(websocket.sent), 1)
            self.assertEqual(user_can.call_count, 2)

        asyncio.run(run())

    def test_global_event_broadcasts_without_device_permission_check(self):
        async def run():
            manager = MirrorManager()
            first = FakeEventWebSocket()
            second = FakeEventWebSocket()
            manager.events = {
                "first": EventClient("alice", first, ready=True),
                "second": EventClient("bob", second, ready=True),
            }
            message = {"type": "settings_changed", "scope": "global"}

            with patch.object(mirror.storage, "user_can") as user_can:
                await manager.broadcast(message)

            self.assertEqual(first.sent, [message])
            self.assertEqual(second.sent, [message])
            user_can.assert_not_called()

        asyncio.run(run())

    def test_broadcast_removes_disconnected_event_client(self):
        async def run():
            manager = MirrorManager()
            dead = FakeEventWebSocket(fail=True)
            manager.events = {
                "dead": EventClient("alice", dead, ready=True),
                "alive": EventClient("bob", FakeEventWebSocket(), ready=True),
            }

            await manager.broadcast({"type": "heartbeat"})

            self.assertNotIn("dead", manager.events)
            self.assertIn("alive", manager.events)
            self.assertTrue(dead.closed)

        asyncio.run(run())

    def test_event_registration_queues_updates_until_after_hello(self):
        async def run():
            manager = MirrorManager()
            websocket = FakeEventRegistrationWebSocket()

            async def snapshot_with_event():
                await manager.broadcast(
                    {
                        "type": "mirror_status",
                        "device_id": "real-device",
                        "running": True,
                        "session": {"device_id": "real-device", "running": True},
                    }
                )
                return {"real-device": {"device_id": "real-device", "running": True}}

            manager.snapshot = snapshot_with_event
            with (
                patch.object(
                    mirror,
                    "event_user_devices",
                    return_value=[{"id": "real-device"}],
                ),
                patch.object(mirror.storage, "user_can", return_value=True),
                patch.object(mirror.storage, "public_device_id", return_value="dev_public"),
            ):
                await manager.register_event_ws(websocket, "alice")

            self.assertEqual([kind for kind, _ in websocket.sent], ["json", "text"])
            self.assertEqual(websocket.sent[0][1]["type"], "hello")
            self.assertEqual(websocket.sent[1][1]["type"], "mirror_status")
            self.assertEqual(websocket.sent[1][1]["device_id"], "dev_public")
            self.assertEqual(manager.events, {})

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

    def test_stop_all_stops_every_session_without_short_circuiting(self):
        async def run():
            manager = MirrorManager()
            first = FakeMirrorSession()
            second = FakeMirrorSession()
            manager.sessions = {"first": first, "second": second}

            await manager.stop_all()

            self.assertTrue(first.stopped)
            self.assertTrue(second.stopped)

        asyncio.run(run())


class MirrorRawRecoveryTests(unittest.TestCase):
    @staticmethod
    def make_session(loop):
        with (
            patch.object(mirror.storage, "get_settings", return_value={}),
            patch.object(mirror, "_stream_mode", return_value="raw"),
        ):
            return MirrorSession("dev1", "127.0.0.1:5555", loop)

    def test_new_client_waits_for_fresh_keyframe_and_requests_reset_once(self):
        async def run():
            session = self.make_session(asyncio.get_running_loop())
            send_control = Mock(return_value=True)
            session.running = True
            session.scrcpy = SimpleNamespace(scrcpy_send_control=send_control)
            client = ClientSession("client", "alice", None)
            client.needs_keyframe = False
            client.queue.put_nowait(b"stale-idr")

            session.add_client(client)
            first_task = session._video_reset_task
            session.prime_client(client, reason="duplicate_request")

            self.assertIs(session._video_reset_task, first_task)
            await first_task
            self.assertTrue(client.needs_keyframe)
            self.assertTrue(client.queue.empty())
            send_control.assert_called_once_with(b"\x11")

        asyncio.run(run())

    def test_queue_overflow_requests_fresh_keyframe(self):
        async def run():
            session = self.make_session(asyncio.get_running_loop())
            send_control = Mock(return_value=True)
            session.running = True
            session.scrcpy = SimpleNamespace(scrcpy_send_control=send_control)
            client = ClientSession("client", "alice", None)
            client.queue = asyncio.Queue(maxsize=4)
            client.needs_keyframe = False
            session.clients[client.id] = client
            for payload in (b"p1", b"p2", b"p3"):
                client.queue.put_nowait(payload)

            session.publish_nal(b"p4", keyframe=False, config=b"")
            await session._video_reset_task

            self.assertEqual(client.drops, 1)
            self.assertTrue(client.needs_keyframe)
            self.assertTrue(client.queue.empty())
            send_control.assert_called_once_with(mirror.SCRCPY_RESET_VIDEO_MESSAGE)

        asyncio.run(run())

    def test_multi_slice_idr_prepends_config_only_to_first_slice(self):
        async def run():
            session = self.make_session(asyncio.get_running_loop())
            client = ClientSession("client", "alice", None)
            session.clients[client.id] = client
            sps = b"\x00\x00\x00\x01\x67\x42"
            pps = b"\x00\x00\x00\x01\x68\xce"
            first_slice = b"\x00\x00\x00\x01\x65\x80"
            later_slice = b"\x00\x00\x00\x01\x65\x40"

            for nal in (sps, pps, first_slice, later_slice):
                session._process_nal(nal)
            await asyncio.sleep(0)

            self.assertFalse(client.needs_keyframe)
            self.assertEqual(client.queue.get_nowait(), sps + pps + first_slice)
            self.assertEqual(client.queue.get_nowait(), later_slice)
            self.assertEqual(session.stream_health, "healthy")

        asyncio.run(run())

    def test_idr_waits_until_both_sps_and_pps_are_available(self):
        async def run():
            session = self.make_session(asyncio.get_running_loop())
            client = ClientSession("client", "alice", None)
            session.clients[client.id] = client
            session._process_nal(b"\x00\x00\x00\x01\x67\x42")
            session._process_nal(b"\x00\x00\x00\x01\x65\x80")
            await asyncio.sleep(0)

            self.assertTrue(client.needs_keyframe)
            self.assertTrue(client.queue.empty())
            self.assertEqual(session.stream_health, "config")
            self.assertEqual(session.codec_config(), b"")

        asyncio.run(run())

    def test_video_transport_exit_marks_failed_and_closes_client_queue(self):
        async def run():
            session = self.make_session(asyncio.get_running_loop())
            scrcpy = SimpleNamespace(scrcpy_stop=Mock())
            client = ClientSession("client", "alice", None)
            client.needs_keyframe = False
            session.clients[client.id] = client
            session.running = True
            session.scrcpy = scrcpy

            with patch.object(mirror.manager, "broadcast", new=AsyncMock()) as broadcast:
                session._handle_stream_transport_closed(session._video_generation, scrcpy, "video socket closed")
                await session._transport_cleanup_task

            self.assertFalse(session.running)
            self.assertIsNone(session.scrcpy)
            self.assertEqual(session.stream_health, "failed")
            self.assertEqual(client.queue.get_nowait(), None)
            scrcpy.scrcpy_stop.assert_called_once_with()
            broadcast.assert_awaited_once()

        asyncio.run(run())


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
            handle_bytes.assert_awaited_once()
            args = handle_bytes.await_args.args
            self.assertEqual(args[:5], (websocket, user, "dev1", "client-raw", b"payload"))
            self.assertIsInstance(args[5], ControlLeaseState)
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

            handle_bytes.assert_awaited_once_with(websocket, user, "dev1", "client-base64", b"payload", None)

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

            session = SimpleNamespace(
                send_control=Mock(side_effect=send_control),
                send_control_for_epoch=Mock(side_effect=lambda payload, _epoch: send_control(payload)),
                last_error="",
            )
            with (
                patch.object(mirror.storage, "renew_lock", side_effect=renew_lock) as renew,
                patch.object(mirror.manager, "get_or_create", new=AsyncMock(return_value=session)),
            ):
                await mirror.handle_control_bytes(websocket, user, "dev1", "client-a", b"payload")

            renew.assert_called_once_with("dev1", "alice", "client-a", ttl_seconds=90)
            self.assertEqual(events, ["renew", ("send", b"payload")])
            self.assertEqual(websocket.sent, [])

        asyncio.run(run())

    def test_cached_control_lease_skips_database_write_for_touch_burst(self):
        async def run():
            websocket = FakeControlWebSocket()
            user = {"username": "alice", "role": "user"}
            session = SimpleNamespace(send_control=Mock(return_value=True), last_error="")
            lease = ControlLeaseState()
            lease.mark_verified(mirror.control_lock_epoch("dev1"))
            session.send_control_for_epoch = Mock(return_value=True)
            with (
                patch.object(mirror.storage, "renew_lock") as renew_lock,
                patch.object(mirror.manager, "get_or_create", new=AsyncMock(return_value=session)),
            ):
                await mirror.handle_control_bytes(websocket, user, "dev1", "client-a", b"payload", lease)

            renew_lock.assert_not_called()
            session.send_control_for_epoch.assert_called_once_with(b"payload", mirror.control_lock_epoch("dev1"))
            self.assertEqual(websocket.sent, [])

        asyncio.run(run())

    def test_force_takeover_epoch_invalidates_cached_control_lease(self):
        lease = ControlLeaseState()
        initial_epoch = mirror.control_lock_epoch("dev-epoch")
        lease.mark_verified(initial_epoch)

        with patch.object(mirror.storage, "acquire_lock", return_value={"ok": True}):
            _result, takeover_epoch = mirror.acquire_control_lock("dev-epoch", "admin", "admin-client", force=True)

        self.assertGreater(takeover_epoch, initial_epoch)
        self.assertFalse(lease.is_verified(takeover_epoch))

    def test_blocked_control_send_does_not_hold_other_device_epoch_lock(self):
        started = threading.Event()
        release = threading.Event()

        class BlockingScrcpy:
            def scrcpy_send_control(self, _payload):
                started.set()
                release.wait(timeout=2)
                return True

        session = SimpleNamespace(
            device_id="dev-blocked",
            running=True,
            scrcpy=BlockingScrcpy(),
            _control_send_lock=threading.Lock(),
            last_error="",
        )
        session._send_control_to = lambda _scrcpy, _payload: (started.set(), release.wait(timeout=2), True)[-1]
        session.send_control = MirrorSession.send_control.__get__(session, MirrorSession)
        session.send_control_for_epoch = MirrorSession.send_control_for_epoch.__get__(session, MirrorSession)

        with patch.object(mirror, "control_lock_epoch", return_value=0):
            worker = threading.Thread(target=session.send_control_for_epoch, args=(b"payload", 0))
            worker.start()
            self.assertTrue(started.wait(timeout=1))
            self.assertEqual(mirror.control_lock_epoch("dev-other"), 0)
            release.set()
            worker.join(timeout=2)

        self.assertFalse(worker.is_alive())


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
