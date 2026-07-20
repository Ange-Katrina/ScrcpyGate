import threading
import time
import unittest

from app.audit_dispatcher import AuditDispatcher


class AuditDispatcherTests(unittest.TestCase):
    def test_single_worker_writes_events_and_flushes(self):
        written = []
        dispatcher = AuditDispatcher(lambda **event: written.append(event), maxsize=16)
        dispatcher.start()
        try:
            for index in range(5):
                self.assertTrue(dispatcher.submit({"action": f"event_{index}", "severity": "info"}))
            self.assertTrue(dispatcher.flush(1))
        finally:
            self.assertTrue(dispatcher.stop(1))

        self.assertEqual([event["action"] for event in written], [f"event_{index}" for index in range(5)])
        self.assertEqual(dispatcher.stats()["processed"], 5)

    def test_full_queue_preserves_higher_severity_and_counts_drops(self):
        release_writer = threading.Event()
        writer_started = threading.Event()
        written = []

        def writer(**event):
            writer_started.set()
            release_writer.wait(1)
            written.append(event)

        dispatcher = AuditDispatcher(writer, maxsize=16)
        dispatcher.start()
        try:
            self.assertTrue(dispatcher.submit({"action": "active", "severity": "info"}))
            self.assertTrue(writer_started.wait(1))
            for index in range(16):
                self.assertTrue(dispatcher.submit({"action": f"queued_{index}", "severity": "info"}))
            self.assertFalse(dispatcher.submit({"action": "extra_info", "severity": "info"}))
            self.assertTrue(dispatcher.submit({"action": "critical", "severity": "critical"}))
            release_writer.set()
            self.assertTrue(dispatcher.flush(2))
        finally:
            release_writer.set()
            self.assertTrue(dispatcher.stop(2))

        actions = {event["action"] for event in written}
        self.assertIn("critical", actions)
        self.assertNotIn("extra_info", actions)
        stats = dispatcher.stats()
        self.assertEqual(stats["dropped_total"], 2)
        self.assertEqual(stats["dropped_by_severity"], {"info": 2})

    def test_submit_never_waits_for_blocked_writer(self):
        release_writer = threading.Event()
        writer_started = threading.Event()

        def writer(**_event):
            writer_started.set()
            release_writer.wait(1)

        dispatcher = AuditDispatcher(writer, maxsize=16)
        dispatcher.start()
        try:
            self.assertTrue(dispatcher.submit({"action": "active"}))
            self.assertTrue(writer_started.wait(1))
            started = time.perf_counter()
            for index in range(16):
                dispatcher.submit({"action": f"queued_{index}"})
            elapsed = time.perf_counter() - started
            self.assertLess(elapsed, 0.1)
        finally:
            release_writer.set()
            self.assertTrue(dispatcher.stop(2))

    def test_barrier_waits_for_pending_events_and_times_out_bounded(self):
        release_writer = threading.Event()
        writer_started = threading.Event()

        def writer(**_event):
            writer_started.set()
            release_writer.wait(1)

        dispatcher = AuditDispatcher(writer, maxsize=16)
        dispatcher.start()
        try:
            self.assertTrue(dispatcher.submit({"action": "pending"}))
            self.assertTrue(writer_started.wait(1))
            started = time.perf_counter()
            self.assertFalse(dispatcher.barrier(0.02))
            self.assertLess(time.perf_counter() - started, 0.2)
            release_writer.set()
            self.assertTrue(dispatcher.barrier(1))
        finally:
            release_writer.set()
            self.assertTrue(dispatcher.stop(2))

    def test_writer_returning_none_is_not_assumed_to_be_failure(self):
        dispatcher = AuditDispatcher(lambda **_event: None, maxsize=16)
        dispatcher.start()
        try:
            self.assertTrue(dispatcher.submit({"action": "accepted"}))
            self.assertTrue(dispatcher.flush(1))
        finally:
            self.assertTrue(dispatcher.stop(1))
        self.assertEqual(dispatcher.stats()["failures"], 0)


if __name__ == "__main__":
    unittest.main()
