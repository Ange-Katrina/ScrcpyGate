import importlib
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def reset_app_modules(names):
    package = sys.modules.get("app")
    for name in names:
        sys.modules.pop(name, None)
        if package is not None and name.startswith("app."):
            attr = name.rsplit(".", 1)[1]
            if hasattr(package, attr):
                delattr(package, attr)


class AuditRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="scrcpygate-audit-routes-"))
        os.environ["WEB_SCRCPY_DATA_DIR"] = str(self.tmp)
        os.environ["ALLOWED_HOSTS"] = "testserver"
        os.environ["SESSION_COOKIE_SECURE"] = "false"
        reset_app_modules([
            "app.main",
            "app.mirror",
            "app.devices",
            "app.storage",
            "app.security",
            "app.account_access",
        ])
        self.storage = importlib.import_module("app.storage")
        self.storage.init_db()
        self.storage.upsert_user("admin", "AdminPassword123", "admin")
        self.main = importlib.import_module("app.main")
        self.client = TestClient(self.main.app)

    def tearDown(self):
        self.client.close()
        shutil.rmtree(self.tmp, ignore_errors=True)
        for key in ("WEB_SCRCPY_DATA_DIR", "ALLOWED_HOSTS", "SESSION_COOKIE_SECURE"):
            os.environ.pop(key, None)
        reset_app_modules([
            "app.main",
            "app.mirror",
            "app.devices",
            "app.storage",
            "app.security",
            "app.account_access",
        ])

    def login(self, username="admin"):
        session = self.storage.create_session(username)
        self.client.cookies.set("wsid", session["sid"])
        return session

    def test_login_failure_records_request_context_and_result(self):
        response = self.client.post(
            "/login",
            data={"username": "admin", "password": "wrong-password"},
            headers={"x-request-id": "request-12345678", "user-agent": "AuditBrowser/1.0"},
        )

        self.assertEqual(response.status_code, 401)
        event = self.storage.query_audit_events(action="login_failed", limit=1)["items"][0]
        self.assertEqual(event["username"], "admin")
        self.assertEqual(event["outcome"], "denied")
        self.assertEqual(event["reason"], "invalid_credentials")
        self.assertEqual(event["request_id"], "request-12345678")
        self.assertEqual(event["user_agent"], "AuditBrowser/1.0")

    def test_csrf_and_admin_access_denials_are_persisted(self):
        self.storage.upsert_user("alice", "AlicePassword123", "user")
        session = self.login("alice")

        csrf = self.client.put("/api/admin/users", json={"username": "bob"})
        admin = self.client.put(
            "/api/admin/users",
            json={"username": "bob"},
            headers={"x-csrf-token": session["csrf_token"]},
        )

        self.assertEqual(csrf.status_code, 400)
        self.assertEqual(admin.status_code, 403)
        actions = self.storage.query_audit_events(actor="alice", limit=10)["items"]
        by_action = {event["action"]: event for event in actions}
        self.assertEqual(by_action["csrf_validation"]["reason"], "token_invalid")
        self.assertEqual(by_action["admin_access"]["reason"], "admin_required")

    def test_admin_can_filter_page_and_open_audit_details(self):
        for index in range(3):
            self.storage.record_audit_event(
                "alice",
                "permission_set",
                outcome="denied" if index == 1 else "success",
                request_id=f"request-{index:08d}",
                ts=100 + index,
            )
        self.login()

        first = self.client.get("/api/admin/logs?actor=alice&action=permission_set&limit=2")
        self.assertEqual(first.status_code, 200)
        data = first.json()
        self.assertEqual(len(data["logs"]), 2)
        self.assertTrue(data["page"]["has_more"])
        self.assertIn("summary", data)
        self.assertEqual(first.headers["cache-control"], "no-store")
        second = self.client.get(
            f"/api/admin/logs?actor=alice&action=permission_set&limit=2&before={data['page']['next_cursor']}"
        )
        first_ids = {event["id"] for event in data["logs"]}
        second_ids = {event["id"] for event in second.json()["logs"]}
        self.assertFalse(first_ids & second_ids)
        detail = self.client.get(f"/api/admin/logs/{data['logs'][0]['event_id']}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["event"]["action"], "permission_set")
        self.assertEqual(detail.headers["cache-control"], "no-store")

    def test_csv_export_blocks_formula_injection_and_disables_cache(self):
        self.storage.record_audit_event("=cmd", "export_probe", detail="=HYPERLINK(1)")
        session = self.login()

        response = self.client.post(
            "/api/admin/logs/export",
            json={"format": "csv"},
            headers={"x-csrf-token": session["csrf_token"]},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("'=cmd", response.text)
        self.assertIn("'=HYPERLINK(1)", response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn("attachment", response.headers["content-disposition"])

    def test_export_rejects_oversized_range_and_normal_user(self):
        session = self.login()
        too_wide = self.client.post(
            "/api/admin/logs/export",
            json={"format": "json", "from_ts": 1, "to_ts": 1 + self.main.AUDIT_EXPORT_MAX_SECONDS + 1},
            headers={"x-csrf-token": session["csrf_token"]},
        )
        self.assertEqual(too_wide.status_code, 400)

        self.storage.upsert_user("alice", "AlicePassword123", "user")
        alice = self.login("alice")
        denied = self.client.post(
            "/api/admin/logs/export",
            json={"format": "json"},
            headers={"x-csrf-token": alice["csrf_token"]},
        )
        self.assertEqual(denied.status_code, 403)

    def test_integrity_endpoint_reports_local_tampering(self):
        self.storage.record_audit_event("admin", "before_tamper")
        with sqlite3.connect(self.storage.DB_PATH) as conn:
            conn.execute("UPDATE audit_log SET detail='changed' WHERE action='before_tamper'")
        session = self.login()

        response = self.client.post(
            "/api/admin/logs/integrity-check",
            headers={"x-csrf-token": session["csrf_token"]},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["ok"])
        self.assertIn(response.json()["error"], {"event_hash_mismatch", "chain_link_mismatch", "chain_head_mismatch"})

    def test_audit_write_failure_does_not_change_completed_response(self):
        self.login()

        with patch.object(self.storage, "record_audit_event", side_effect=RuntimeError("audit offline")):
            response = self.client.get("/")

        self.assertEqual(response.status_code, 200)

    def test_account_expiration_event_is_deduplicated(self):
        expires_at = int(time.time()) - 1
        self.storage.upsert_user("alice", "AlicePassword123", "user", expires_at=expires_at)

        self.main._revoke_expired_access_with_audit()
        self.main._revoke_expired_access_with_audit()

        events = self.storage.query_audit_events(action="account_expired", actor="system", limit=10)["items"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["target_id"], "alice")
        self.assertEqual(events[0]["metadata"]["expires_at"], expires_at)

    def test_websocket_origin_and_auth_rejections_record_channel_categories(self):
        paths = (
            ("/ws/events", "events"),
            ("/ws/devices/dev_1/video", "video"),
            ("/ws/devices/dev_1/control", "control"),
        )
        for path, _socket in paths:
            with self.subTest(reason="origin_denied", path=path):
                with self.assertRaises(WebSocketDisconnect) as context:
                    with self.client.websocket_connect(
                        path,
                        headers={"origin": "https://evil.example"},
                    ):
                        pass
                self.assertEqual(context.exception.code, 4403)

        origin_events = [
            event
            for event in self.storage.query_audit_events(action="websocket_access", limit=20)["items"]
            if event["reason"] == "origin_denied"
        ]
        self.assertEqual({event["metadata"]["socket"] for event in origin_events}, {"events", "video", "control"})

        for path, _socket in paths:
            with self.subTest(reason="authentication_required", path=path):
                with self.assertRaises(WebSocketDisconnect) as context:
                    with self.client.websocket_connect(path):
                        pass
                self.assertEqual(context.exception.code, 4401)

        auth_events = [
            event
            for event in self.storage.query_audit_events(action="websocket_access", limit=20)["items"]
            if event["reason"] == "authentication_required"
        ]
        self.assertEqual({event["metadata"]["socket"] for event in auth_events}, {"events", "video", "control"})

    def test_device_websocket_permission_denials_are_audited_per_channel(self):
        self.storage.upsert_user("alice", "AlicePassword123", "user")
        self.storage.upsert_device("dev_1", "Device", "127.0.0.1:5555", True)
        self.login("alice")

        for path in ("/ws/devices/dev_1/video", "/ws/devices/dev_1/control"):
            with self.subTest(path=path):
                with self.assertRaises(WebSocketDisconnect) as context:
                    with self.client.websocket_connect(path):
                        pass
                self.assertEqual(context.exception.code, 4403)

        events = [
            event
            for event in self.storage.query_audit_events(
                actor="alice", action="websocket_access", limit=10
            )["items"]
            if event["reason"] == "device_permission_denied"
        ]
        self.assertEqual(len(events), 2)
        self.assertEqual({event["metadata"]["socket"] for event in events}, {"video", "control"})

    def test_control_websocket_acquire_and_release_are_audited_once(self):
        self.storage.upsert_device("dev_1", "Device", "127.0.0.1:5555", True)
        self.login()

        with self.client.websocket_connect("/ws/devices/dev_1/control") as websocket:
            self.assertEqual(websocket.receive_json()["type"], "hello")
            websocket.send_json({"type": "acquire_control"})
            self.assertTrue(websocket.receive_json()["ok"])
            websocket.send_json({"type": "release_control"})
            self.assertTrue(websocket.receive_json()["ok"])

        events = self.storage.query_audit_events(actor="admin", limit=20)["items"]
        controls = [event for event in events if event["action"] in {"control_acquire", "control_release"}]
        self.assertEqual([event["action"] for event in reversed(controls)], ["control_acquire", "control_release"])
        self.assertTrue(all(event["metadata"]["channel"] == "websocket" for event in controls))
        self.assertTrue(all(event["metadata"]["socket"] == "control" for event in controls))

    def test_normal_user_cannot_read_audit_list_or_event_detail(self):
        event = self.storage.record_audit_event("admin", "protected_event")
        self.storage.upsert_user("alice", "AlicePassword123", "user")
        self.login("alice")

        self.assertEqual(self.client.get("/api/admin/logs").status_code, 403)
        self.assertEqual(
            self.client.get(f"/api/admin/logs/{event['event_id']}").status_code,
            403,
        )

    def test_json_export_is_bounded_and_disables_cache(self):
        for index in range(4):
            self.storage.record_audit_event("admin", f"export_{index}")
        session = self.login()

        with patch.object(self.main, "AUDIT_EXPORT_MAX_ROWS", 3):
            response = self.client.post(
                "/api/admin/logs/export",
                json={"format": "json"},
                headers={"x-csrf-token": session["csrf_token"]},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["events"]), 3)
        self.assertEqual(payload["exported_count"], 3)
        self.assertTrue(payload["truncated"])
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn("scrcpygate-audit.json", response.headers["content-disposition"])

    def test_admin_log_cursor_bounds_are_validated(self):
        self.login("admin")
        response = self.client.get("/api/admin/logs", params={"before": 2**63})
        self.assertEqual(response.status_code, 422)

    def test_admin_log_read_waits_for_previously_accepted_events(self):
        self.login("admin")
        dispatcher = self.main.AuditDispatcher(self.storage.record_audit_event, maxsize=16)
        dispatcher.start()
        self.main.audit_dispatcher = dispatcher
        try:
            self.assertTrue(dispatcher.submit({"username": "admin", "action": "queued_event"}))
            response = self.client.get("/api/admin/logs", params={"action": "queued_event"})
        finally:
            self.main.audit_dispatcher = None
            self.assertTrue(dispatcher.stop(1))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["x-audit-consistent"], "true")
        self.assertEqual([event["action"] for event in response.json()["logs"]], ["queued_event"])


if __name__ == "__main__":
    unittest.main()
