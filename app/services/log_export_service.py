"""Export retained log records without pagination, display mappings or secret files."""
from __future__ import annotations

from contextlib import ExitStack, closing
from dataclasses import dataclass, field
import base64
import hashlib
import json
import os
import re
import stat
import tempfile
import threading
import time
from typing import BinaryIO
import zipfile

from .. import logging_config, security, storage

_export_slot = threading.BoundedSemaphore(1)


class LogExportError(Exception):
    pass


@dataclass
class LogArchive:
    file: BinaryIO
    size: int
    counts: dict
    _closed: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def close(self):
        with self._lock:
            if not self._closed:
                self._closed = True
                try:
                    self.file.close()
                finally:
                    _export_slot.release()

    def chunks(self, *, encoded: bool = False):
        try:
            if encoded:
                yield b'{"filename":"scrcpygate-logs-full.zip","encoding":"base64","content":"'
            # Multiple of three: intermediate base64 chunks have no padding.
            while chunk := self.file.read(48 * 1024):
                yield base64.b64encode(chunk) if encoded else chunk
            if encoded:
                yield b'"}'
        finally:
            self.close()


def build_log_archive(*, audit_consistent: bool) -> LogArchive:
    if not _export_slot.acquire(blocking=False):
        raise LogExportError("export_busy")
    output = None
    try:
        output = tempfile.TemporaryFile(mode="w+b")
        started = time.time()
        deadline = time.monotonic() + 120
        max_bytes = security.env_int("LOG_EXPORT_MAX_BYTES", 512 * 1024 * 1024, 1024 * 1024, 2 * 1024 * 1024 * 1024)
        total = 0
        files = []
        counts = {}

        def account(data):
            nonlocal total
            total += len(data)
            if total > max_bytes:
                raise LogExportError("export_too_large")
            if time.monotonic() > deadline:
                raise LogExportError("export_timeout")

        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=3) as archive:
            # A single read transaction keeps audit and alert tables consistent
            # even if retention or new events run while the export is generated.
            with closing(storage.readonly_db_connect()) as conn:
                conn.execute("BEGIN")
                for table, name, order in (("audit_log", "audit.jsonl", "id"), ("audit_alerts", "alerts.jsonl", "event_id"),
                                           ("audit_integrity_state", "integrity.jsonl", "singleton")):
                    cursor = conn.execute(f"SELECT * FROM {table} ORDER BY {order}")
                    count, size = 0, 0
                    digest = hashlib.sha256()
                    with archive.open(name, "w", force_zip64=True) as member:
                        while rows := cursor.fetchmany(500):
                            for row in rows:
                                record = dict(row)
                                # Preserve every stored column, including metadata_json
                                # and hash-chain fields, regardless of known event names.
                                data = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
                                account(data)
                                member.write(data)
                                digest.update(data)
                                size += len(data)
                                count += 1
                    files.append({"name": name, "bytes": size, "sha256": digest.hexdigest(), "rows": count})
                    counts[table] = count
                conn.rollback()

            logging_config._refresh_paths()
            base = logging_config.LOG_FILE
            # Open only the product's active log and numeric rotation files. Never
            # traverse data directories or include DBs, settings or credential files.
            with ExitStack() as stack:
                sources = []
                candidates = sorted(base.parent.glob(base.name + "*"))
                for path in candidates:
                    if not re.fullmatch(re.escape(base.name) + r"(?:\.\d+)?", path.name):
                        continue
                    meta = path.lstat()
                    if not stat.S_ISREG(meta.st_mode):
                        raise LogExportError("export_log_unreadable")
                    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
                    handle = stack.enter_context(os.fdopen(descriptor, "rb"))
                    opened = os.fstat(handle.fileno())
                    if (opened.st_dev, opened.st_ino) != (meta.st_dev, meta.st_ino):
                        raise LogExportError("export_logs_changed")
                    sources.append((path.name, handle, opened.st_size))
                for name, handle, length in sources:
                    remaining = length
                    digest = hashlib.sha256()
                    with archive.open("runtime/" + name, "w", force_zip64=True) as member:
                        while remaining:
                            data = handle.read(min(64 * 1024, remaining))
                            if not data:
                                raise LogExportError("export_logs_changed")
                            account(data)
                            member.write(data)
                            digest.update(data)
                            remaining -= len(data)
                    files.append({"name": "runtime/" + name, "bytes": length, "sha256": digest.hexdigest()})
                counts["runtime_files"] = len(sources)

            manifest = {
                "schema_version": 1, "started_at": int(started), "finished_at": int(time.time()),
                "scope": "all_retained_logs", "truncated": False, "audit_consistent": audit_consistent,
                "counts": counts, "files": files,
                "notes": ["Audit and alerts share one database read snapshot; runtime files are captured afterward at their opening lengths.",
                          "Retention-deleted records and console-only logs cannot be recovered. No environment, credential or database files are included.",
                          "Unknown event codes and all stored columns are preserved. metadata_json contains the original stored JSON text."],
            }
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        size = output.tell()
        output.seek(0)
        return LogArchive(output, size, counts)
    except BaseException:
        if output is not None:
            output.close()
        _export_slot.release()
        raise
