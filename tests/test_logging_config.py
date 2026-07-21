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

    def test_sensitive_text_redaction_handles_escaped_values_and_digest_headers(self):
        samples = (
            '{"password":"abc\\\"def","other":"visible"}',
            '{"cookie":"abc\\\"def","other":"visible"}',
            "authorization: Digest username=alice,response=secret,nonce=opaque",
            "authorization=Digest username=alice,response=secret",
        )
        for sample in samples:
            with self.subTest(sample=sample):
                safe = logging_config.sanitize_log_text(sample)
                self.assertNotIn("abc", safe)
                self.assertNotIn("secret", safe)
                self.assertNotIn("alice", safe)
                self.assertIn("<redacted>", safe)

    def test_exception_stacktrace_keeps_newlines_and_indentation_after_redaction(self):
        try:
            raise RuntimeError("token=stacktrace-secret")
        except RuntimeError:
            record = logging.LogRecord(
                "webscrcpy.runtime",
                logging.ERROR,
                __file__,
                48,
                "operation failed",
                (),
                sys.exc_info(),
                "test_multiline_exception",
            )
        rendered = logging_config.SafeJsonFormatter().format(record)
        payload = json.loads(rendered)
        stacktrace = payload["attributes"]["exception.stacktrace"]
        self.assertIn("\n", stacktrace)
        self.assertIn("  File", stacktrace)
        self.assertNotIn("stacktrace-secret", stacktrace)

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

    def test_internal_tail_snapshot_preserves_crlf_final_newline_and_reports_line_limit(self):
        previous = os.environ.get("WEB_SCRCPY_DATA_DIR")
        with tempfile.TemporaryDirectory(prefix="scrcpygate-log-raw-text-") as temporary:
            os.environ["WEB_SCRCPY_DATA_DIR"] = temporary
            path = Path(temporary) / "webscrcpy.log"
            records = [f"line-{index}\r\n" for index in range(24)] + ["line-24"]
            path.write_bytes("".join(records).encode("utf-8"))
            try:
                lines, raw_text, meta = logging_config._tail_log_snapshot(20)
            finally:
                if previous is None:
                    os.environ.pop("WEB_SCRCPY_DATA_DIR", None)
                else:
                    os.environ["WEB_SCRCPY_DATA_DIR"] = previous
                logging_config._refresh_paths()

        self.assertEqual(lines, [f"line-{index}" for index in range(5, 25)])
        self.assertEqual(raw_text, "".join(records[-20:]))
        self.assertFalse(raw_text.endswith(("\r", "\n")))
        self.assertTrue(meta["line_limit_hit"])
        self.assertFalse(meta["byte_limit_hit"])
        self.assertFalse(meta["partial_first_line"])

    def test_tail_snapshot_splits_only_crlf_and_keeps_other_control_separators(self):
        previous = os.environ.get("WEB_SCRCPY_DATA_DIR")
        with tempfile.TemporaryDirectory(prefix="scrcpygate-log-separators-") as temporary:
            os.environ["WEB_SCRCPY_DATA_DIR"] = temporary
            path = Path(temporary) / "webscrcpy.log"
            path.write_bytes(b"first\x0bpart\x1ccontent\r\nsecond\n")
            try:
                lines, raw_text, _meta = logging_config._tail_log_snapshot(20)
            finally:
                if previous is None:
                    os.environ.pop("WEB_SCRCPY_DATA_DIR", None)
                else:
                    os.environ["WEB_SCRCPY_DATA_DIR"] = previous
                logging_config._refresh_paths()

        self.assertEqual(lines, ["first\x0bpart\x1ccontent", "second"])
        self.assertEqual(raw_text, "first\x0bpart\x1ccontent\r\nsecond\n")

    def test_byte_limited_tail_discards_incomplete_first_line_and_marks_snapshot(self):
        previous_dir = os.environ.get("WEB_SCRCPY_DATA_DIR")
        previous_limit = os.environ.get("LOG_TAIL_MAX_BYTES")
        with tempfile.TemporaryDirectory(prefix="scrcpygate-log-byte-tail-") as temporary:
            os.environ["WEB_SCRCPY_DATA_DIR"] = temporary
            os.environ["LOG_TAIL_MAX_BYTES"] = str(64 * 1024)
            path = Path(temporary) / "webscrcpy.log"
            records = [f"line-{index}:" + (str(index) * 20000) + "\r\n" for index in range(10)]
            path.write_bytes("".join(records).encode("utf-8"))
            try:
                raw_lines, entries, meta = logging_config.runtime_log_snapshot(20)
            finally:
                if previous_dir is None:
                    os.environ.pop("WEB_SCRCPY_DATA_DIR", None)
                else:
                    os.environ["WEB_SCRCPY_DATA_DIR"] = previous_dir
                if previous_limit is None:
                    os.environ.pop("LOG_TAIL_MAX_BYTES", None)
                else:
                    os.environ["LOG_TAIL_MAX_BYTES"] = previous_limit
                logging_config._refresh_paths()

        self.assertEqual(raw_lines, [record.rstrip("\r\n") for record in records[-3:]])
        self.assertEqual(meta["raw_text"], "".join(records[-3:]))
        self.assertEqual(len(entries), 3)
        self.assertTrue(meta["byte_limit_hit"])
        self.assertFalse(meta["line_limit_hit"])
        self.assertTrue(meta["partial_first_line"])
        self.assertTrue(meta["truncated"])

    def test_runtime_log_parser_keeps_mixed_formats_and_continuations(self):
        structured = json.dumps(
            {
                "timestamp": "2026-07-20T08:00:00.000Z",
                "severity_text": "INFO",
                "severity_number": 17,
                "logger": "scrcpygate.test",
                "event_name": "request_failed",
                "body": "request failed",
                "request_id": "request-12345678",
                "attributes": {"token": "top-secret", "status_code": 500},
            }
        )
        lines = [
            "unstructured password=plain-secret",
            structured,
            "2026-07-20 16:00:01,500 [WARNING] legacy.worker: operation delayed",
            "Traceback (most recent call last):",
            '  File "/srv/app.py", line 10, in run',
        ]

        entries, meta = logging_config.parse_runtime_log_lines(lines)

        self.assertEqual(len(entries), 3)
        self.assertTrue(entries[0]["parse_failed"])
        self.assertEqual(entries[0]["severity"], "unknown")
        self.assertNotIn("plain-secret", entries[0]["message"])
        self.assertEqual(entries[1]["severity"], "error")
        self.assertEqual(entries[1]["attributes"]["token"], "<redacted>")
        self.assertEqual(entries[2]["severity"], "warning")
        self.assertIn("Traceback", entries[2]["message"])
        self.assertIn("/srv/app.py", entries[2]["message"])
        self.assertEqual(entries[2]["attributes"]["log.continuation_lines"], 2)
        self.assertEqual(meta["scanned_lines"], 5)
        self.assertEqual(meta["unclassified_lines"], 3)
        self.assertEqual(meta["severity_counts"]["error"], 1)
        self.assertEqual(meta["severity_counts"]["warning"], 1)
        self.assertEqual(meta["severity_counts"]["unknown"], 1)

    def test_runtime_log_parser_merges_asyncio_and_base_exception_continuations(self):
        rows = [
            "2026-07-20 16:00:01,500 [ERROR] asyncio: background task failed",
            "Task exception was never retrieved",
            "future: <Task finished exception=StopIteration()>",
            "Traceback (most recent call last):",
            '  File "/srv/app.py", line 10, in run',
            "StopIteration",
            "Exception ignored in: <function cleanup at 0x1>",
            "KeyboardInterrupt",
        ]

        entries, meta = logging_config.parse_runtime_log_lines(rows)

        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["severity"], "error")
        self.assertIn("\nTask exception was never retrieved", entry["message"])
        self.assertIn("\nfuture:", entry["message"])
        self.assertIn('\n  File "/srv/app.py"', entry["message"])
        self.assertIn("\nException ignored in:", entry["message"])
        self.assertTrue(entry["message"].endswith("\nKeyboardInterrupt"))
        self.assertEqual(entry["attributes"]["log.continuation_lines"], len(rows) - 1)
        self.assertEqual(meta["severity_counts"]["error"], 1)

    def test_runtime_log_continuation_truncation_is_explicit_and_keeps_indentation(self):
        rows = [
            "2026-07-20 16:00:01,500 [ERROR] worker: failed",
            "  first indented frame",
            *["    " + ("x" * 4092) for _index in range(6)],
        ]

        entries, _meta = logging_config.parse_runtime_log_lines(rows)

        entry = entries[0]
        self.assertIn("\n  first indented frame", entry["message"])
        self.assertEqual(len(entry["message"]), logging_config._RUNTIME_LOG_ENTRY_MAX_CHARS)
        self.assertTrue(entry["message"].endswith(logging_config.TRUNCATED_MARKER))
        self.assertTrue(entry["raw"].endswith(logging_config.TRUNCATED_MARKER))
        self.assertIs(entry["attributes"]["log.truncated"], True)
        self.assertEqual(entry["attributes"]["log.continuation_lines"], len(rows) - 1)

    def test_runtime_log_parser_round_trips_native_json_context(self):
        record = logging.LogRecord(
            "webscrcpy.runtime",
            logging.INFO,
            __file__,
            42,
            "request completed",
            (),
            None,
            "test_runtime_json",
        )
        record.event_name = "http.request"
        record.event_fields = {
            "http_method": "GET",
            "http_route": "/api/admin/runtime-logs",
            "http_status_code": 200,
            "duration_ms": 12.5,
            "actor": "spoofed",
        }
        record._scrcpygate_context = {
            "request_id": "request-12345678",
            "trace_id": "trace-12345678",
            "span_id": "span-12345678",
            "actor": "alice",
            "source_ip": "203.0.113.10",
        }

        rendered = logging_config.SafeJsonFormatter().format(record)
        entries, _meta = logging_config.parse_runtime_log_lines([rendered])

        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertFalse(entry["parse_failed"])
        self.assertEqual(entry["request_id"], "request-12345678")
        self.assertEqual(entry["attributes"]["trace_id"], "trace-12345678")
        self.assertEqual(entry["attributes"]["span_id"], "span-12345678")
        self.assertEqual(entry["attributes"]["actor"], "alice")
        self.assertEqual(entry["attributes"]["source_ip"], "203.0.113.10")
        self.assertEqual(entry["attributes"]["http_method"], "GET")
        self.assertEqual(entry["attributes"]["http_route"], "/api/admin/runtime-logs")

    def test_runtime_log_parser_round_trips_text_event_fields(self):
        record = logging.LogRecord(
            "webscrcpy.runtime",
            logging.WARNING,
            __file__,
            42,
            "request completed",
            (),
            None,
            "test_runtime_text",
        )
        record.event_name = "http.request"
        record.event_fields = {
            "http_method": "POST",
            "http_route": "/api/action",
            "http_status_code": 503,
            "duration_ms": 87.25,
            "nested": {"retry": True},
            "actor": "spoofed",
            "note": "contains scrcpygate_fields= marker",
        }
        record._scrcpygate_context = {
            "request_id": "request-87654321",
            "trace_id": "trace-text-1234",
            "span_id": "span-text-1234",
            "actor": "trusted",
            "source_ip": "203.0.113.11",
        }

        rendered = logging_config.SafeTextFormatter().format(record)
        entries, _meta = logging_config.parse_runtime_log_lines([rendered])
        legacy_rendered = (
            "2026-07-20 16:00:00,000 [INFO] webscrcpy.runtime "
            'request_id=legacy-request event=http.request: legacy request {"http_method":"PATCH"}'
        )
        legacy_entries, _legacy_meta = logging_config.parse_runtime_log_lines([legacy_rendered])

        empty_record = logging.LogRecord(
            "webscrcpy.runtime",
            logging.INFO,
            __file__,
            43,
            "",
            (),
            None,
            "test_runtime_text_empty",
        )
        empty_record.event_name = "http.request"
        empty_record.event_fields = {"http_method": "GET", "http_route": "/health"}
        empty_record._scrcpygate_context = {"request_id": "request-empty"}
        empty_rendered = logging_config.SafeTextFormatter().format(empty_record)
        empty_entries, _empty_meta = logging_config.parse_runtime_log_lines([empty_rendered])

        json_message_record = logging.LogRecord(
            "webscrcpy.runtime",
            logging.INFO,
            __file__,
            44,
            '{"kind":"message"}',
            (),
            None,
            "test_runtime_text_json_message",
        )
        json_message_record.event_name = "message.event"
        json_message_record.event_fields = {}
        json_message_record._scrcpygate_context = {"request_id": "request-json-message"}
        json_message_rendered = logging_config.SafeTextFormatter().format(json_message_record)
        json_message_entries, _json_message_meta = logging_config.parse_runtime_log_lines(
            [json_message_rendered]
        )

        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["format"], "text")
        self.assertEqual(entry["message"], "request completed")
        self.assertEqual(entry["request_id"], "request-87654321")
        self.assertEqual(entry["attributes"]["http_method"], "POST")
        self.assertEqual(entry["attributes"]["http_route"], "/api/action")
        self.assertEqual(entry["attributes"]["http_status_code"], 503)
        self.assertEqual(entry["attributes"]["duration_ms"], 87.25)
        self.assertEqual(entry["attributes"]["nested"], {"retry": True})
        self.assertEqual(entry["attributes"]["trace_id"], "trace-text-1234")
        self.assertEqual(entry["attributes"]["span_id"], "span-text-1234")
        self.assertEqual(entry["attributes"]["actor"], "trusted")
        self.assertEqual(entry["attributes"]["source_ip"], "203.0.113.11")
        self.assertEqual(entry["attributes"]["note"], "contains scrcpygate_fields= marker")
        self.assertIn(logging_config.RUNTIME_LOG_TEXT_FIELDS_MARKER, rendered)
        self.assertEqual(legacy_entries[0]["message"], "legacy request")
        self.assertEqual(legacy_entries[0]["attributes"]["http_method"], "PATCH")
        self.assertEqual(empty_entries[0]["message"], "")
        self.assertEqual(empty_entries[0]["attributes"]["http_method"], "GET")
        self.assertEqual(empty_entries[0]["attributes"]["http_route"], "/health")
        self.assertEqual(json_message_entries[0]["message"], '{"kind":"message"}')
        self.assertEqual(json_message_entries[0]["attributes"], {})

    def test_text_field_marker_survives_many_marker_sequences_inside_attributes(self):
        record = logging.LogRecord(
            "webscrcpy.runtime",
            logging.INFO,
            __file__,
            45,
            "marker stress",
            (),
            None,
            "test_runtime_text_many_markers",
        )
        note = logging_config.RUNTIME_LOG_TEXT_FIELDS_MARKER * 40
        record.event_name = "marker.event"
        record.event_fields = {"note": note, "count": 40}
        record._scrcpygate_context = {"request_id": "request-many-markers"}

        rendered = logging_config.SafeTextFormatter().format(record)
        entries, _meta = logging_config.parse_runtime_log_lines([rendered])

        self.assertEqual(entries[0]["message"], "marker stress")
        self.assertEqual(entries[0]["attributes"]["note"], note.strip())
        self.assertEqual(entries[0]["attributes"]["count"], 40)

    def test_legacy_trailing_json_requires_recognizable_log_fields(self):
        business = '2026-07-20 16:00:00,000 [INFO] worker: payload {"kind":"business"}'
        runtime = (
            "2026-07-20 16:00:01,000 [INFO] worker: request complete "
            '{"http_method":"GET","http_status_code":200}'
        )

        business_entries, _business_meta = logging_config.parse_runtime_log_lines([business])
        runtime_entries, _runtime_meta = logging_config.parse_runtime_log_lines([runtime])

        self.assertEqual(business_entries[0]["message"], 'payload {"kind":"business"}')
        self.assertEqual(business_entries[0]["attributes"], {})
        self.assertEqual(runtime_entries[0]["message"], "request complete")
        self.assertEqual(runtime_entries[0]["attributes"]["http_method"], "GET")
        self.assertEqual(runtime_entries[0]["attributes"]["http_status_code"], 200)

    def test_event_fields_cannot_spoof_trusted_log_context(self):
        record = logging.LogRecord(
            "webscrcpy.runtime",
            logging.INFO,
            __file__,
            46,
            "trusted context",
            (),
            None,
            "test_runtime_trusted_context",
        )
        record.event_name = "context.event"
        record.event_fields = {
            "request_id": "spoofed-request",
            "trace_id": "spoofed-trace",
            "span_id": "spoofed-span",
            "actor": "spoofed-actor",
            "source_ip": "198.51.100.200",
            "service": "spoofed-service",
            "observed_timestamp": "spoofed-time",
            "outcome": "success",
        }
        record._scrcpygate_context = {
            "request_id": "trusted-request",
            "trace_id": "trusted-trace",
            "span_id": "trusted-span",
            "actor": "trusted-actor",
            "source_ip": "203.0.113.20",
        }

        json_payload = json.loads(logging_config.SafeJsonFormatter().format(record))
        text_rendered = logging_config.SafeTextFormatter().format(record)
        text_entries, _meta = logging_config.parse_runtime_log_lines([text_rendered])
        text_entry = text_entries[0]

        self.assertEqual(json_payload["request_id"], "trusted-request")
        self.assertEqual(json_payload["trace_id"], "trusted-trace")
        self.assertEqual(json_payload["span_id"], "trusted-span")
        self.assertEqual(json_payload["actor"], "trusted-actor")
        self.assertEqual(json_payload["source_ip"], "203.0.113.20")
        self.assertEqual(json_payload["service"], logging_config.SERVICE_NAME)
        for key in logging_config._TRUSTED_LOG_FIELD_NAMES:
            self.assertNotIn(key, json_payload["attributes"])
        self.assertEqual(json_payload["attributes"]["outcome"], "success")
        self.assertEqual(text_entry["request_id"], "trusted-request")
        self.assertEqual(text_entry["attributes"]["trace_id"], "trusted-trace")
        self.assertEqual(text_entry["attributes"]["span_id"], "trusted-span")
        self.assertEqual(text_entry["attributes"]["actor"], "trusted-actor")
        self.assertEqual(text_entry["attributes"]["source_ip"], "203.0.113.20")
        self.assertNotIn("service", text_entry["attributes"])
        self.assertNotIn("observed_timestamp", text_entry["attributes"])

        legacy = (
            "2026-07-20 16:00:00,000 [INFO] worker request_id=trusted-legacy "
            'event=context.event: completed {"request_id":"spoofed-legacy","http_method":"GET"}'
        )
        legacy_entries, _legacy_meta = logging_config.parse_runtime_log_lines([legacy])
        self.assertEqual(legacy_entries[0]["request_id"], "trusted-legacy")
        self.assertNotIn("request_id", legacy_entries[0]["attributes"])
        self.assertEqual(legacy_entries[0]["attributes"]["http_method"], "GET")

    def test_text_formatter_preserves_structured_exception_fields(self):
        try:
            raise RuntimeError("token=text-exception-secret")
        except RuntimeError:
            record = logging.LogRecord(
                "webscrcpy.runtime",
                logging.ERROR,
                __file__,
                47,
                "operation failed",
                (),
                sys.exc_info(),
                "test_runtime_text_exception",
            )
        record.event_name = "operation.failed"
        record.event_fields = {}
        record._scrcpygate_context = {"request_id": "request-text-exception"}

        rendered = logging_config.SafeTextFormatter().format(record)
        entries, _meta = logging_config.parse_runtime_log_lines([rendered])
        entry = entries[0]

        self.assertNotIn("text-exception-secret", rendered)
        self.assertEqual(entry["message"], "operation failed")
        self.assertEqual(entry["attributes"]["exception.type"], "RuntimeError")
        self.assertIn("<redacted>", entry["attributes"]["exception.message"])
        self.assertIn("RuntimeError", entry["attributes"]["exception.stacktrace"])

    def test_runtime_log_parser_infers_bounded_typed_legacy_message_fields(self):
        structured = json.dumps(
            {
                "severity_text": "INFO",
                "event_name": "adb_status",
                "body": (
                    "ADB_STATUS device=device-alias state=offline ok=False "
                    "detail=network timeout count=3 ratio=1.25 token=top-secret"
                ),
                "attributes": {"state": "explicit-state"},
            }
        )
        legacy = (
            "2026-07-20 16:00:01,000 [INFO] webscrcpy.mirror: "
            "VIDEO_CLIENT_ADD device=device-alias client=client-1 user=alice clients=3"
        )

        structured_entries, _structured_meta = logging_config.parse_runtime_log_lines([structured])
        legacy_entries, _legacy_meta = logging_config.parse_runtime_log_lines([legacy])
        entry = structured_entries[0]

        self.assertEqual(entry["attributes"]["device"], "<adb-endpoint>")
        self.assertEqual(entry["attributes"]["state"], "explicit-state")
        self.assertIs(entry["attributes"]["ok"], False)
        self.assertEqual(entry["attributes"]["detail"], "network timeout")
        self.assertEqual(entry["attributes"]["count"], 3)
        self.assertEqual(entry["attributes"]["ratio"], 1.25)
        self.assertEqual(entry["attributes"]["token"], "<redacted>")
        self.assertNotIn("top-secret", entry["message"])
        self.assertEqual(legacy_entries[0]["event_name"], "video_client_add")
        self.assertEqual(legacy_entries[0]["attributes"]["clients"], 3)
        self.assertEqual(legacy_entries[0]["attributes"]["user"], "alice")

    def test_runtime_log_message_field_inference_caps_work_and_output(self):
        message = "BULK_EVENT " + " ".join(
            f"field_{index}={index}" for index in range(1000)
        )

        entries, _meta = logging_config.parse_runtime_log_lines(
            [
                json.dumps(
                    {
                        "severity_text": "INFO",
                        "body": message,
                    }
                )
            ]
        )
        entry = entries[0]

        self.assertEqual(entry["event_name"], "bulk_event")
        self.assertEqual(len(entry["attributes"]), logging_config._RUNTIME_LOG_MAX_INFERRED_FIELDS)
        self.assertEqual(entry["attributes"]["field_0"], 0)
        self.assertEqual(entry["attributes"]["field_31"], 31)
        self.assertNotIn("field_32", entry["attributes"])

    def test_runtime_log_parser_marks_non_log_json_as_unclassified(self):
        rendered = json.dumps({"foo": "bar", "token": "top-secret"})

        entries, meta = logging_config.parse_runtime_log_lines([rendered])

        self.assertEqual(len(entries), 1)
        self.assertTrue(entries[0]["parse_failed"])
        self.assertEqual(entries[0]["format"], "unknown")
        self.assertNotIn("top-secret", entries[0]["message"])
        self.assertEqual(meta["format_counts"]["unknown"], 1)
        self.assertEqual(meta["unclassified_lines"], 1)

    def test_runtime_log_snapshot_scans_before_filtering_rare_errors(self):
        previous = os.environ.get("WEB_SCRCPY_DATA_DIR")
        with tempfile.TemporaryDirectory(prefix="scrcpygate-runtime-log-") as temporary:
            os.environ["WEB_SCRCPY_DATA_DIR"] = temporary
            path = Path(temporary) / "webscrcpy.log"
            rows = [
                "2026-07-20 16:00:00,000 [ERROR] worker: early failure",
                *[
                    f"2026-07-20 16:00:{index:02d},000 [INFO] worker: event-{index}"
                    for index in range(1, 36)
                ],
            ]
            path.write_bytes(("\n".join(rows) + "\n").encode("utf-8"))
            try:
                raw_lines, entries, meta = logging_config.runtime_log_snapshot(20, "error")
                minimum_raw, minimum_entries, minimum_meta = logging_config.runtime_log_snapshot(1)
            finally:
                if previous is None:
                    os.environ.pop("WEB_SCRCPY_DATA_DIR", None)
                else:
                    os.environ["WEB_SCRCPY_DATA_DIR"] = previous
                logging_config._refresh_paths()

        self.assertEqual(len(raw_lines), 20)
        self.assertNotIn("early failure", "\n".join(raw_lines))
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["severity"], "error")
        self.assertIn("early failure", entries[0]["message"])
        self.assertEqual(meta["scanned_lines"], 36)
        self.assertEqual(meta["matching_entries"], 1)
        self.assertEqual(meta["returned_entries"], 1)
        self.assertEqual(meta["min_severity"], "error")
        self.assertEqual(minimum_raw, rows[-20:])
        self.assertEqual(len(minimum_entries), 20)
        self.assertEqual(minimum_meta["returned_entries"], 20)
        self.assertEqual(minimum_meta["raw_text"], "\n".join(rows[-20:]) + "\n")
        self.assertTrue(minimum_meta["line_limit_hit"])
        self.assertFalse(minimum_meta["byte_limit_hit"])

    def test_runtime_log_read_barrier_reports_pending_queue_when_timeout_expires(self):
        previous_handler = logging_config._queue_handler
        previous_outputs = logging_config._output_handlers
        log_queue = queue.Queue(maxsize=2)
        log_queue.put_nowait(
            logging.LogRecord("webscrcpy.pending", logging.INFO, __file__, 1, "pending", (), None)
        )
        logging_config._queue_handler = logging_config.ResilientQueueHandler(log_queue)
        logging_config._output_handlers = []
        try:
            completed, pending = logging_config._runtime_log_read_barrier(0)
        finally:
            logging_config._queue_handler = previous_handler
            logging_config._output_handlers = previous_outputs
            log_queue.get_nowait()
            log_queue.task_done()

        self.assertFalse(completed)
        self.assertEqual(pending, 1)

    def test_runtime_log_filter_rejects_unknown_levels(self):
        with self.assertRaises(ValueError):
            logging_config.normalize_runtime_log_filter("verbose")

    def test_runtime_log_parser_does_not_merge_corrupt_rows_or_crash_on_bad_counts(self):
        rows = [
            "2026-07-20 16:01:00,000 [INFO] worker: started",
            "corrupt row without a timestamp",
            json.dumps(
                {
                    "severity_text": "ERROR",
                    "body": "failed",
                    "attributes": {"log.continuation_lines": "not-a-number"},
                }
            ),
            "Traceback (most recent call last):",
        ]

        entries, meta = logging_config.parse_runtime_log_lines(rows)

        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[1]["severity"], "unknown")
        self.assertEqual(entries[2]["severity"], "error")
        self.assertEqual(entries[2]["attributes"]["log.continuation_lines"], 1)
        self.assertEqual(meta["severity_counts"]["unknown"], 1)


if __name__ == "__main__":
    unittest.main()
