import importlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_storage(data_dir: Path):
    os.environ["WEB_SCRCPY_DATA_DIR"] = str(data_dir)
    sys.modules.pop("app.storage", None)
    return importlib.import_module("app.storage")


class AuditStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="scrcpygate-audit-test-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("WEB_SCRCPY_DATA_DIR", None)

    def test_legacy_database_migrates_once_and_keeps_old_rows(self):
        db_path = self.tmp / "webscrcpy.db"
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE audit_log(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts INTEGER NOT NULL,
                    username TEXT NOT NULL,
                    action TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.executemany(
                "INSERT INTO audit_log(ts,username,action,detail) VALUES(?,?,?,?)",
                [(10, "old-a", "login", "one"), (20, "old-b", "logout", "two")],
            )
        storage = load_storage(self.tmp)
        storage.init_db()
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            first = [dict(row) for row in conn.execute("SELECT * FROM audit_log ORDER BY id")]
            state_first = dict(conn.execute("SELECT * FROM audit_integrity_state").fetchone())
        storage.init_db()
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            second = [dict(row) for row in conn.execute("SELECT * FROM audit_log ORDER BY id")]
            state_second = dict(conn.execute("SELECT * FROM audit_integrity_state").fetchone())

        self.assertEqual(first, second)
        self.assertEqual(state_first, state_second)
        self.assertEqual([row["outcome"] for row in first], ["unknown", "unknown"])
        self.assertEqual([row["schema_version"] for row in first], [1, 1])
        self.assertTrue(all(row["event_id"] and row["event_hash"] for row in first))
        self.assertTrue(storage.verify_audit_integrity()["ok"])

    def test_schema_upgrade_after_retention_keeps_existing_anchor(self):
        storage = load_storage(self.tmp)
        storage.init_db()
        with (
            patch.object(storage, "AUDIT_MAX_ROWS", 3),
            patch.object(storage, "AUDIT_PRUNE_BATCH", 1),
        ):
            for index in range(5):
                storage.record_audit_event("admin", f"event_{index}")
        with patch.dict(storage._AUDIT_COLUMN_DEFINITIONS, {"future_field": "TEXT NOT NULL DEFAULT ''"}):
            storage.init_db()
        self.assertTrue(storage.verify_audit_integrity()["ok"])
        self.assertEqual(storage.audit_summary()["total"], 3)

    def test_legacy_migration_applies_retention_cap(self):
        db_path = self.tmp / "webscrcpy.db"
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE audit_log(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts INTEGER NOT NULL,
                    username TEXT NOT NULL,
                    action TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.executemany(
                "INSERT INTO audit_log(ts,username,action,detail) VALUES(?,?,?,?)",
                [(index, "legacy", f"event_{index}", "") for index in range(5)],
            )
        storage = load_storage(self.tmp)
        with patch.object(storage, "AUDIT_MAX_ROWS", 3):
            storage.init_db()
            self.assertEqual(storage.audit_summary()["total"], 3)
            self.assertTrue(storage.verify_audit_integrity()["ok"])

    def test_legacy_audit_and_recent_contract_is_preserved(self):
        storage = load_storage(self.tmp)
        storage.init_db()
        for index in range(12):
            self.assertIsNone(storage.audit("legacy", f"event_{index}", str(index)))

        rows = storage.recent_audit(10)
        self.assertEqual(len(rows), 10)
        self.assertEqual([row["action"] for row in rows], [f"event_{index}" for index in range(2, 12)])
        self.assertEqual([row["detail"] for row in rows], [str(index) for index in range(2, 12)])
        self.assertTrue(all("metadata_json" not in row and row["metadata"] == {} for row in rows))

    def test_record_sanitizes_control_characters_and_sensitive_values(self):
        storage = load_storage(self.tmp)
        storage.init_db()
        event = storage.record_audit_event(
            "alice\r\nforged",
            "User Delete",
            "line1\r\nline2 token=super-secret \x1b[31mred",
            actor_role="admin",
            target_type="User Account",
            target_id="victim\nforged",
            outcome="denied",
            reason="password=hunter2",
            severity="warning",
            request_id="request-12345678",
            source_ip="2001:db8::1",
            user_agent="Browser\r\nInjected: yes",
            metadata={
                "password": "plain-secret",
                "accessToken": "access-secret",
                "nested": {"csrfToken": "csrf-secret", "safe": "ok\nnext"},
            },
        )

        serialized = json.dumps(event, ensure_ascii=False)
        for secret in ("super-secret", "hunter2", "plain-secret", "access-secret", "csrf-secret"):
            self.assertNotIn(secret, serialized)
        self.assertNotIn("\r", serialized)
        self.assertNotIn("\n", serialized)
        self.assertNotIn("\x1b", serialized)
        self.assertEqual(event["username"], "alice forged")
        self.assertEqual(event["action"], "user_delete")
        self.assertEqual(event["target_type"], "user_account")
        self.assertEqual(event["source_ip"], "2001:db8::1")
        self.assertEqual(event["metadata"]["password"], "<redacted>")
        self.assertEqual(event["metadata"]["accessToken"], "<redacted>")

    def test_query_filters_and_keyset_pages_do_not_overlap(self):
        storage = load_storage(self.tmp)
        storage.init_db()
        for index in range(6):
            storage.record_audit_event(
                "alice" if index % 2 == 0 else "bob",
                "login" if index < 5 else "logout",
                outcome="denied" if index in (2, 4) else "success",
                severity="warning" if index in (2, 4) else "info",
                request_id=f"request-{index:08d}",
                ts=100 + index,
            )

        first = storage.query_audit_events(actor="alice", action="login", limit=2)
        second = storage.query_audit_events(
            actor="alice", action="login", before_id=first["next_before_id"], limit=2
        )
        first_ids = {row["id"] for row in first["items"]}
        second_ids = {row["id"] for row in second["items"]}
        self.assertTrue(first["has_more"])
        self.assertFalse(first_ids & second_ids)
        self.assertEqual(len(first_ids | second_ids), 3)
        denied = storage.query_audit_events(
            outcome="denied", severity="warning", from_ts=102, to_ts=104, limit=10
        )
        self.assertEqual([row["ts"] for row in denied["items"]], [104, 102])
        request = storage.query_audit_events(request_id="request-00000003", limit=10)
        self.assertEqual(len(request["items"]), 1)

    def test_dedupe_key_is_idempotent(self):
        storage = load_storage(self.tmp)
        storage.init_db()
        first = storage.record_audit_event("admin", "user_delete", dedupe_key="delete:42")
        second = storage.record_audit_event("admin", "different", dedupe_key="delete:42")

        self.assertEqual(first["id"], second["id"])
        self.assertEqual(second["action"], "user_delete")
        self.assertEqual(storage.audit_summary()["total"], 1)
        self.assertTrue(storage.verify_audit_integrity()["ok"])

    def test_write_failure_is_logged_and_never_raised(self):
        storage = load_storage(self.tmp)
        storage.init_db()
        with patch.object(storage, "db_connect", side_effect=sqlite3.OperationalError("database locked")):
            with self.assertLogs(storage.AUDIT_LOGGER, level="CRITICAL") as captured:
                result = storage.record_audit_event("admin", "user_delete")
        self.assertIsNone(result)
        self.assertTrue(any("AUDIT_WRITE_FAILED" in line for line in captured.output))

    def test_integrity_detects_update_middle_delete_and_tail_delete(self):
        for scenario in ("update", "middle_delete", "tail_delete"):
            with self.subTest(scenario=scenario):
                data_dir = self.tmp / scenario
                storage = load_storage(data_dir)
                storage.init_db()
                for index in range(3):
                    storage.record_audit_event("admin", f"event_{index}")
                self.assertTrue(storage.verify_audit_integrity()["ok"])
                with sqlite3.connect(storage.DB_PATH) as conn:
                    if scenario == "update":
                        conn.execute("UPDATE audit_log SET detail='changed' WHERE id=2")
                    elif scenario == "middle_delete":
                        conn.execute("DELETE FROM audit_log WHERE id=2")
                    else:
                        conn.execute("DELETE FROM audit_log WHERE id=(SELECT MAX(id) FROM audit_log)")
                result = storage.verify_audit_integrity()
                self.assertFalse(result["ok"])
                self.assertIn(result["error"], {"event_hash_mismatch", "chain_link_mismatch", "chain_head_mismatch"})

    def test_summary_and_get_return_json_ready_metadata(self):
        storage = load_storage(self.tmp)
        storage.init_db()
        created = storage.record_audit_event(
            "admin", "permission_set", outcome="denied", severity="error", metadata={"count": 2}
        )
        fetched = storage.get_audit_event(created["event_id"])
        summary = storage.audit_summary()

        self.assertEqual(fetched["metadata"], {"count": 2})
        self.assertNotIn("metadata_json", fetched)
        self.assertEqual(summary["total"], 1)
        self.assertEqual(summary["high_risk"], 1)
        self.assertEqual(summary["by_outcome"], {"denied": 1})
        json.dumps({"event": fetched, "summary": summary})

    def test_retention_cap_preserves_anchor_and_integrity(self):
        storage = load_storage(self.tmp)
        storage.init_db()
        with (
            patch.object(storage, "AUDIT_MAX_ROWS", 3),
            patch.object(storage, "AUDIT_PRUNE_BATCH", 1),
        ):
            for index in range(5):
                storage.record_audit_event("admin", f"event_{index}")

        page = storage.query_audit_events(limit=10)
        self.assertEqual([row["action"] for row in page["items"]], ["event_4", "event_3", "event_2"])
        integrity = storage.verify_audit_integrity()
        self.assertTrue(integrity["ok"])
        self.assertEqual(integrity["checked"], 3)
        self.assertEqual(integrity["pruned_count"], 2)
        self.assertGreater(integrity["anchor_event_id"], 0)

    def test_integrity_check_uses_one_snapshot_during_concurrent_append(self):
        storage = load_storage(self.tmp)
        storage.init_db()
        storage.record_audit_event("admin", "before_check")
        original_connect = storage.db_connect
        verify_connection = original_connect()
        injected = False

        class ConnectionProxy:
            def __enter__(self):
                verify_connection.__enter__()
                return self

            def __exit__(self, exc_type, exc, traceback):
                return verify_connection.__exit__(exc_type, exc, traceback)

            def execute(self, sql, *args):
                nonlocal injected
                cursor = verify_connection.execute(sql, *args)
                if not injected and "SELECT * FROM audit_integrity_state" in sql:
                    injected = True
                    storage.record_audit_event("concurrent", "during_check")
                return cursor

        first_connection = True

        def connect_for_test():
            nonlocal first_connection
            if first_connection:
                first_connection = False
                return ConnectionProxy()
            return original_connect()

        with patch.object(storage, "db_connect", side_effect=connect_for_test):
            result = storage.verify_audit_integrity()

        self.assertTrue(result["ok"])
        self.assertEqual(result["checked"], 1)
        self.assertEqual(storage.audit_summary()["total"], 2)

    def test_large_numeric_filters_are_clamped_for_direct_storage_calls(self):
        storage = load_storage(self.tmp)
        storage.init_db()
        storage.record_audit_event("admin", "bounded")
        self.assertEqual(len(storage.query_audit_events(before_id=2**100)["items"]), 1)
        self.assertEqual(len(storage.query_audit_events(to_ts=2**100)["items"]), 1)
        self.assertIsNone(storage.get_audit_event(str(2**100)))


if __name__ == "__main__":
    unittest.main()
