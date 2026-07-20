import json
import logging
import os
import queue
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from app import logging_config


class LoggingConfigTests(unittest.TestCase):
    def test_sanitizer_blocks_injection_credentials_queries_and_adb_endpoints(self):
        raw = (
            "\x1b[31mLOGIN token=top-secret\r\nFORGED "
            "url=https://example.test/run?token=url-secret&config=private "
            "serial=192.0.2.32:5555"
        )

        safe = logging_config.sanitize_log_text(raw)

        self.assertNotIn("\x1b", safe)
        self.assertNotIn("\r", safe)
        self.assertNotIn("\n", safe)
        self.assertNotIn("top-secret", safe)
        self.assertNotIn("url-secret", safe)
        self.assertNotIn("192.0.2.32:5555", safe)
        self.assertIn("token=<redacted>", safe)
        self.assertIn("?<redacted>", safe)
        self.assertIn("<adb-endpoint>", safe)

    def test_sanitizer_redacts_complete_cookie_userinfo_and_named_adb_endpoints(self):
        raw = (
            "Cookie: theme=dark; remember=second-cookie-secret\r\n"
            "url=https://user:embedded-secret@example.test/path "
            "device=pixel-lab.internal:5555 "
            "adb_endpoint=2001:db8::10:5555"
        )

        safe = logging_config.sanitize_log_text(raw)

        for secret in (
            "theme=dark",
            "second-cookie-secret",
            "user:embedded-secret",
            "pixel-lab.internal:5555",
            "2001:db8::10:5555",
        ):
            self.assertNotIn(secret, safe)
        self.assertIn("Cookie: <redacted>", safe)
        self.assertIn("https://<redacted>@example.test/path", safe)
        self.assertEqual(safe.count("<adb-endpoint>"), 2)

    def test_sanitizer_redacts_url_password_through_last_authority_at_sign(self):
        safe = logging_config.sanitize_log_text("https://user:p@ss@example.test/path")

        self.assertEqual(safe, "https://<redacted>@example.test/path")
        self.assertNotIn("p@ss", safe)

    def test_structured_sanitizer_redacts_asgi_raw_header_pairs(self):
        safe = logging_config.sanitize_log_value(
            [(b"accept", b"application/json"), (b"cookie", b"session=synthetic-secret")]
        )

        self.assertEqual(safe[0], ["accept", "application/json"])
        self.assertEqual(safe[1], ["cookie", "<redacted>"])
        self.assertNotIn("synthetic-secret", json.dumps(safe))

    def test_structured_redaction_recognizes_camel_case_secret_keys(self):
        safe = logging_config.sanitize_log_value(
            {
                "accessToken": "a",
                "refreshToken": "r",
                "apiKey": "k",
                "csrfToken": "c",
                "sessionId": "s",
                "privateKey": "p",
                "authToken": "z",
                "authorizationHeader": "Bearer hidden",
                "cookieHeader": "session=hidden",
                "sessionCount": 3,
            }
        )

        secret_keys = (
            "accessToken",
            "refreshToken",
            "apiKey",
            "csrfToken",
            "sessionId",
            "privateKey",
            "authToken",
            "authorizationHeader",
            "cookieHeader",
        )
        for key in secret_keys:
            self.assertEqual(safe[key], "<redacted>", key)
        self.assertEqual(safe["sessionCount"], 3)

    def test_json_formatter_emits_correlated_bounded_structured_fields(self):
        record = logging.LogRecord(
            "webscrcpy.test",
            logging.WARNING,
            __file__,
            42,
            "authorization=Bearer raw-secret",
            (),
            None,
            "test_formatter",
        )
        record.event_name = "security.authorization.denied"
        record.event_fields = {
            "outcome": "failure",
            "password": "never-log-me",
            "target": "192.0.2.32:5555",
            "nested": {"csrf_token": "csrf-secret", "reason": "policy"},
        }
        record._scrcpygate_context = {
            "request_id": "req-12345678",
            "actor": "alice\r\nadmin",
            "source_ip": "203.0.113.10",
        }

        payload = json.loads(logging_config.SafeJsonFormatter().format(record))

        self.assertEqual(payload["event_name"], "security.authorization.denied")
        self.assertEqual(payload["severity_text"], "WARNING")
        self.assertEqual(payload["severity_number"], 13)
        self.assertEqual(payload["request_id"], "req-12345678")
        self.assertEqual(payload["actor"], "alice admin")
        self.assertEqual(payload["source_ip"], "203.0.113.10")
        self.assertNotIn("raw-secret", payload["body"])
        self.assertEqual(payload["attributes"]["password"], "<redacted>")
        self.assertEqual(payload["attributes"]["nested"]["csrf_token"], "<redacted>")
        self.assertNotIn("192.0.2.32:5555", payload["attributes"]["target"])
        self.assertNotIn("\n", json.dumps(payload, ensure_ascii=False))

    def test_queue_prepare_redacts_a_copy_without_breaking_original_record_args(self):
        record = logging.LogRecord(
            "webscrcpy.logging-test",
            logging.WARNING,
            __file__,
            1,
            "token=%s\r\nFORGED",
            ("factory-secret",),
            None,
        )

        prepared = logging_config.ResilientQueueHandler(queue.Queue()).prepare(record)

        self.assertEqual(record.args, ("factory-secret",))
        self.assertEqual(record.msg, "token=%s\r\nFORGED")
        self.assertEqual(prepared.args, ())
        self.assertNotIn("factory-secret", prepared.msg)
        self.assertNotIn("\r", prepared.msg)
        self.assertIn("token=<redacted>", prepared.msg)

    def test_context_is_copied_into_record_before_queue_thread_handoff(self):
        token = logging_config.bind_log_context(
            request_id="request-12345678",
            actor="alice",
            csrf_token="never-copy-this",
        )
        try:
            record = logging.getLogger("webscrcpy.context-test").makeRecord(
                "webscrcpy.context-test",
                logging.INFO,
                __file__,
                70,
                "context event",
                (),
                None,
            )
            prepared = logging_config.ResilientQueueHandler(queue.Queue()).prepare(record)
        finally:
            logging_config.reset_log_context(token)

        self.assertFalse(hasattr(record, "_scrcpygate_context"))
        self.assertEqual(prepared._scrcpygate_context["request_id"], "request-12345678")
        self.assertEqual(prepared._scrcpygate_context["actor"], "alice")
        self.assertEqual(prepared._scrcpygate_context["csrf_token"], "<redacted>")

    def test_protocol_loggers_share_the_safe_root_pipeline(self):
        parent = logging.getLogger("uvicorn")
        access = logging.getLogger("uvicorn.access")
        old_parent_handlers = list(parent.handlers)
        old_parent_propagate = parent.propagate
        old_handlers = list(access.handlers)
        old_propagate = access.propagate
        parent.addHandler(logging.NullHandler())
        parent.propagate = False
        probe = logging.NullHandler()
        access.addHandler(probe)
        try:
            logging_config._protect_protocol_logs()
            self.assertTrue(parent.propagate)
            self.assertEqual(parent.handlers, [])
            self.assertTrue(access.propagate)
            self.assertEqual(access.handlers, [])
        finally:
            parent.handlers[:] = old_parent_handlers
            parent.propagate = old_parent_propagate
            access.handlers[:] = old_handlers
            access.propagate = old_propagate

    def test_text_formatter_sanitizes_dynamic_logger_names(self):
        record = logging.LogRecord("evil\r\nFORGED\x1b[31m", logging.INFO, __file__, 1, "event", (), None)
        record._scrcpygate_context = {}

        rendered = logging_config.SafeTextFormatter().format(record)

        self.assertNotIn("\r", rendered)
        self.assertNotIn("\n", rendered)
        self.assertNotIn("\x1b", rendered)

    def test_non_finite_numbers_remain_valid_json(self):
        record = logging.LogRecord("webscrcpy.test", logging.INFO, __file__, 1, "event", (), None)
        record.event_name = "numeric.event"
        record.event_fields = {"nan": float("nan"), "infinity": float("inf")}
        record._scrcpygate_context = {}

        rendered = logging_config.SafeJsonFormatter().format(record)
        payload = json.loads(rendered, parse_constant=lambda value: self.fail(value))

        self.assertIsNone(payload["attributes"]["nan"])
        self.assertIsNone(payload["attributes"]["infinity"])

    def test_exception_messages_are_redacted_after_queue_handoff(self):
        try:
            raise ValueError("token=exception-secret")
        except ValueError:
            record = logging.getLogger("uvicorn.error").makeRecord(
                "uvicorn.error",
                logging.ERROR,
                __file__,
                1,
                "request failed",
                (),
                sys.exc_info(),
            )
        prepared = logging_config.ResilientQueueHandler(queue.Queue()).prepare(record)

        rendered = logging_config.SafeJsonFormatter().format(prepared)

        self.assertNotIn("exception-secret", rendered)
        self.assertIn("<redacted>", rendered)

    def test_bounded_queue_counts_drops_without_blocking(self):
        log_queue = queue.Queue(maxsize=1)
        handler = logging_config.ResilientQueueHandler(log_queue)
        record = logging.LogRecord("webscrcpy.test", logging.INFO, __file__, 1, "event", (), None)
        before = logging_config.logging_health()["dropped_records"]

        handler.enqueue(record)
        handler.enqueue(record)

        self.assertEqual(log_queue.qsize(), 1)
        self.assertEqual(logging_config.logging_health()["dropped_records"], before + 1)

    def test_full_queue_never_writes_synchronously_for_error_records(self):
        log_queue = queue.Queue(maxsize=1)
        handler = logging_config.ResilientQueueHandler(log_queue)
        record = logging.LogRecord("webscrcpy.test", logging.ERROR, __file__, 1, "failed", (), None)
        handler.enqueue(record)

        with mock.patch.object(sys.stderr, "write", side_effect=AssertionError("blocking fallback")):
            handler.enqueue(record)

        self.assertEqual(log_queue.qsize(), 1)
        self.assertEqual(log_queue.get_nowait().levelno, logging.ERROR)

    def test_priority_enqueue_never_replaces_listener_sentinel(self):
        log_queue = queue.Queue(maxsize=1)
        handler = logging_config.ResilientQueueHandler(log_queue)
        record = logging.LogRecord("webscrcpy.test", logging.ERROR, __file__, 1, "failed", (), None)
        log_queue.put_nowait(None)

        handler.enqueue(record)

        self.assertEqual(log_queue.qsize(), 1)
        self.assertIsNone(log_queue.get_nowait())

    def test_queue_reserves_capacity_for_high_severity_records(self):
        log_queue = queue.Queue(maxsize=8)
        handler = logging_config.ResilientQueueHandler(log_queue)
        info = logging.LogRecord("webscrcpy.test", logging.INFO, __file__, 1, "info", (), None)
        error = logging.LogRecord("webscrcpy.test", logging.ERROR, __file__, 1, "error", (), None)

        for _index in range(8):
            handler.enqueue(info)
        handler.enqueue(error)

        queued = list(log_queue.queue)
        self.assertEqual(len(queued), 8)
        self.assertEqual(sum(record.levelno >= logging.ERROR for record in queued), 1)
        self.assertGreaterEqual(logging_config.logging_health()["dropped_records_by_level"]["info"], 1)

    def test_structured_sequences_only_consume_the_bounded_prefix(self):
        class CountingSet(set):
            yielded = 0

            def __iter__(self):
                for item in super().__iter__():
                    type(self).yielded += 1
                    yield item

        values = CountingSet(range(1000))

        safe = logging_config.sanitize_log_value(values)

        self.assertEqual(CountingSet.yielded, 33)
        self.assertEqual(len(safe), 33)
        self.assertEqual(safe[-1], logging_config.TRUNCATED_MARKER)

    def test_text_sanitizer_bounds_work_before_redaction(self):
        raw = "prefix " + ("x" * 100000) + " token=never-reached"

        safe = logging_config.sanitize_log_text(raw, 128)

        self.assertEqual(len(safe), 128)
        self.assertTrue(safe.endswith(logging_config.TRUNCATED_MARKER))
        self.assertNotIn("never-reached", safe)

    def test_relative_url_query_redaction_is_linear_for_slash_heavy_input(self):
        started = time.perf_counter()

        safe = logging_config.sanitize_log_text("/" * 65536, 65536)

        self.assertLess(time.perf_counter() - started, 0.5)
        self.assertEqual(len(safe), 65536)

    def test_query_redaction_is_linear_for_repeated_absolute_url_prefixes(self):
        raw = "http://a" * 8192
        started = time.perf_counter()

        safe = logging_config.sanitize_log_text(raw, 65536)

        self.assertLess(time.perf_counter() - started, 0.5)
        self.assertEqual(len(safe), 65536)

    def test_tail_log_reads_only_the_requested_bounded_tail(self):
        previous = os.environ.get("WEB_SCRCPY_DATA_DIR")
        with tempfile.TemporaryDirectory(prefix="scrcpygate-log-tail-") as temporary:
            os.environ["WEB_SCRCPY_DATA_DIR"] = temporary
            path = Path(temporary) / "webscrcpy.log"
            path.write_text("".join(f"line-{index}\n" for index in range(35)), encoding="utf-8")
            try:
                lines = logging_config.tail_log(20)
            finally:
                if previous is None:
                    os.environ.pop("WEB_SCRCPY_DATA_DIR", None)
                else:
                    os.environ["WEB_SCRCPY_DATA_DIR"] = previous
                logging_config._refresh_paths()

        self.assertEqual(len(lines), 20)
        self.assertEqual(lines[0], "line-15")
        self.assertEqual(lines[-1], "line-34")


if __name__ == "__main__":
    unittest.main()
