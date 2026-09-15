from __future__ import annotations

import logging
import threading
import time
from collections import Counter, deque
from collections.abc import Callable, Mapping


log = logging.getLogger("webscrcpy.audit")
_SEVERITY_PRIORITY = {
    "debug": 0,
    "info": 1,
    "warning": 2,
    "error": 3,
    "critical": 4,
}


class AuditDispatcher:
    """Single-writer, bounded audit queue for production request paths.

    The dispatcher never blocks producers. When full, a higher-severity event
    may replace the oldest lower-severity event; otherwise the incoming event
    is dropped and counted. Drop reports are logarithmically sampled so an
    attacker cannot turn queue pressure into an unbounded secondary log flood.
    """

    def __init__(
        self,
        writer: Callable[..., object],
        *,
        maxsize: int = 512,
        thread_name: str = "scrcpygate-audit-writer",
    ) -> None:
        self._writer = writer
        self._maxsize = max(16, min(int(maxsize), 65536))
        self._thread_name = thread_name
        self._items: deque[tuple[int, dict]] = deque()
        self._pending_tickets: set[int] = set()
        self._next_ticket = 0
        self._condition = threading.Condition()
        self._thread: threading.Thread | None = None
        self._stopping = False
        self._unfinished = 0
        self._accepted = 0
        self._processed = 0
        self._failures = 0
        self._dropped: Counter[str] = Counter()

    @staticmethod
    def _severity(event: Mapping) -> str:
        value = str(event.get("severity") or "info").strip().lower()
        return value if value in _SEVERITY_PRIORITY else "info"

    @staticmethod
    def _clone_event(event: Mapping) -> dict:
        cloned = dict(event)
        metadata = cloned.get("metadata")
        if isinstance(metadata, dict):
            cloned["metadata"] = dict(metadata)
        return cloned

    def start(self) -> None:
        with self._condition:
            if self._thread and self._thread.is_alive():
                return
            self._stopping = False
            self._thread = threading.Thread(
                target=self._run,
                name=self._thread_name,
                daemon=True,
            )
            self._thread.start()

    def _note_drop_locked(self, severity: str) -> tuple[int, bool]:
        self._dropped[severity] += 1
        total = sum(self._dropped.values())
        return total, total > 0 and (total & (total - 1)) == 0

    def _enqueue_locked(self, event: dict) -> None:
        self._next_ticket += 1
        ticket = self._next_ticket
        self._items.append((ticket, event))
        self._pending_tickets.add(ticket)
        self._unfinished += 1
        self._accepted += 1

    def submit(self, event: Mapping) -> bool:
        if not isinstance(event, Mapping):
            raise TypeError("audit event must be a mapping")
        cloned = self._clone_event(event)
        severity = self._severity(cloned)
        report: tuple[int, str, str] | None = None
        accepted = False
        with self._condition:
            if not self._thread or not self._thread.is_alive() or self._stopping:
                total, should_report = self._note_drop_locked(severity)
                if should_report:
                    report = (total, severity, "dispatcher_unavailable")
            elif len(self._items) >= self._maxsize:
                incoming_priority = _SEVERITY_PRIORITY[severity]
                replace_index = None
                replace_priority = incoming_priority
                for index, (_ticket, queued) in enumerate(self._items):
                    queued_priority = _SEVERITY_PRIORITY[self._severity(queued)]
                    if queued_priority < replace_priority:
                        replace_priority = queued_priority
                        replace_index = index
                        if queued_priority == 0:
                            break
                if replace_index is None:
                    total, should_report = self._note_drop_locked(severity)
                    if should_report:
                        report = (total, severity, "queue_full")
                else:
                    evicted_ticket, evicted = self._items[replace_index]
                    del self._items[replace_index]
                    self._pending_tickets.discard(evicted_ticket)
                    self._unfinished -= 1
                    evicted_severity = self._severity(evicted)
                    total, should_report = self._note_drop_locked(evicted_severity)
                    if should_report:
                        report = (total, evicted_severity, "priority_eviction")
                    self._enqueue_locked(cloned)
                    accepted = True
                    self._condition.notify_all()
            else:
                self._enqueue_locked(cloned)
                accepted = True
                self._condition.notify()
        if report:
            total, dropped_severity, reason = report
            log.warning(
                "AUDIT_QUEUE_DROPPED total=%s severity=%s reason=%s",
                total,
                dropped_severity,
                reason,
                extra={
                    "event_name": "audit.queue_dropped",
                    "event_fields": {
                        "dropped_total": total,
                        "dropped_severity": dropped_severity,
                        "reason": reason,
                    },
                },
            )
        return accepted

    def _run(self) -> None:
        while True:
            with self._condition:
                while not self._items and not self._stopping:
                    self._condition.wait()
                if self._stopping and not self._items:
                    return
                ticket, event = self._items.popleft()
            failed = False
            try:
                self._writer(**event)
            except Exception:
                failed = True
                log.critical(
                    "AUDIT_QUEUE_WRITE_FAILED",
                    exc_info=True,
                    extra={"event_name": "audit.queue_write_failed"},
                )
            finally:
                with self._condition:
                    self._processed += 1
                    if failed:
                        self._failures += 1
                    self._pending_tickets.discard(ticket)
                    self._unfinished -= 1
                    self._condition.notify_all()

    def barrier(self, timeout: float = 0.5) -> bool:
        """Wait only for events accepted before this call, up to ``timeout``."""
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._condition:
            target_ticket = self._next_ticket
            while any(ticket <= target_ticket for ticket in self._pending_tickets):
                thread = self._thread
                if not thread or not thread.is_alive():
                    return False
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True

    def flush(self, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._condition:
            while self._unfinished:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True

    def stop(self, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._condition:
            self._stopping = True
            self._condition.notify_all()
            thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(max(0.0, deadline - time.monotonic()))
        return not bool(thread and thread.is_alive())

    def stats(self) -> dict:
        with self._condition:
            thread = self._thread
            return {
                "running": bool(thread and thread.is_alive() and not self._stopping),
                "queue_size": len(self._items),
                "queue_capacity": self._maxsize,
                "unfinished": self._unfinished,
                "accepted": self._accepted,
                "processed": self._processed,
                "failures": self._failures,
                "dropped_total": sum(self._dropped.values()),
                "dropped_by_severity": dict(self._dropped),
            }
