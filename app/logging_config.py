import atexit
import contextvars
import copy
import json
import logging
import math
import os
import queue
import re
import sys
import threading
import uuid
from datetime import datetime, timezone
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
from pathlib import Path


SERVICE_NAME = "scrcpygate"
DATA_DIR = Path(os.environ.get("WEB_SCRCPY_DATA_DIR", "data"))
LOG_FILE = DATA_DIR / "webscrcpy.log"
SENSITIVE_PROTOCOL_LOGGERS = (
    "uvicorn",
    "websockets",
    "uvicorn.error",
    "uvicorn.access",
)
NOISY_LIBRARY_LOGGERS = (
    "httpx",
    "httpx2",
    "httpcore",
)
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,95}$")
EVENT_NAME_RE = re.compile(r"[^a-z0-9_.-]+")
FIELD_NAME_RE = re.compile(r"[^a-zA-Z0-9_.-]+")
ANSI_ESCAPE_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
URL_USERINFO_RE = re.compile(r"(?i)\b(?P<scheme>(?:https?|wss?)://)[^/\s?#'\"]+@")
COOKIE_HEADER_RE = re.compile(r"(?i)\b(?P<prefix>(?:set-cookie|cookie)\s*:\s*)[^\r\n]*")
COOKIE_ASSIGNMENT_RE = re.compile(
    r"(?i)(?P<prefix>[\"']?(?:set[_-]?cookie|cookie)[\"']?\s*=\s*)"
    r"(?P<value>\"[^\"]*\"|'[^']*'|[^\r\n,}\]]+)"
)
SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)(?P<prefix>[\"']?(?:password|passwd|pwd|token|access[_-]?token|refresh[_-]?token|"
    r"api[_-]?key|secret|authorization|cookie|csrf(?:[_-]?token)?|session[_-]?id|sid|"
    r"private[_-]?key|database[_-]?url|connection[_-]?string|alas[_-]?token)[\"']?\s*[:=]\s*)"
    r"(?P<value>(?:Bearer|Basic)\s+[^\s,;}\]]+|\"[^\"]*\"|'[^']*'|[^\s,;}\]]+)"
)
BEARER_RE = re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+")
ADB_ENDPOINT_RE = re.compile(
    r"(?<![\w:])(?:\d{1,3}\.){3}\d{1,3}:\d{1,5}\b|\[[0-9A-Fa-f:]+\]:\d{1,5}"
)
ENDPOINT_ASSIGNMENT_RE = re.compile(
    r"(?i)(?P<prefix>[\"']?(?:address|serial|endpoint|device(?:[_-]?(?:id|address))?|"
    r"adb(?:[_-]?(?:address|endpoint|serial))?)[\"']?\s*[:=]\s*)"
    r"(?P<value>\"[^\"]*\"|'[^']*'|[^\s,;}\]]+)"
)
TRUNCATED_MARKER = "...[truncated]"

_SENSITIVE_FIELD_NAMES = {
    "password",
    "passwd",
    "pwd",
    "token",
    "access_token",
    "refresh_token",
    "api_key",
    "secret",
    "authorization",
    "cookie",
    "set_cookie",
    "csrf",
    "csrf_token",
    "session_id",
    "sid",
    "private_key",
    "database_url",
    "connection_string",
    "alas_token",
}
_SEVERITY_NUMBERS = {
    logging.DEBUG: 5,
    logging.INFO: 9,
    logging.WARNING: 13,
    logging.ERROR: 17,
    logging.CRITICAL: 21,
}
_log_context: contextvars.ContextVar[dict[str, object]] = contextvars.ContextVar(
    "scrcpygate_log_context", default={}
)
_configuration_lock = threading.RLock()
_drop_lock = threading.Lock()
_configured = False
_configured_signature: tuple | None = None
_listener: QueueListener | None = None
_queue_handler: QueueHandler | None = None
_output_handlers: list[logging.Handler] = []
_dropped_records = 0
_dropped_records_by_level: dict[str, int] = {}
_atexit_registered = False


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)) or default)
    except (TypeError, ValueError, OverflowError):
        value = default
    return max(minimum, min(value, maximum))


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _refresh_paths() -> None:
    global DATA_DIR, LOG_FILE
    DATA_DIR = Path(os.environ.get("WEB_SCRCPY_DATA_DIR", "data"))
    LOG_FILE = DATA_DIR / "webscrcpy.log"


def _normalized_field_name(name: object) -> str:
    raw = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(name or "").strip())
    return re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")


def _is_sensitive_field(name: object) -> bool:
    normalized = _normalized_field_name(name)
    if normalized in _SENSITIVE_FIELD_NAMES:
        return True
    parts = set(normalized.split("_"))
    if parts.intersection({"password", "passwd", "pwd", "token", "secret", "authorization", "cookie", "csrf"}):
        return True
    return normalized.endswith(("_password", "_passwd", "_token", "_secret", "_cookie", "_private_key"))


def _header_name(value: object) -> str:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).decode("latin-1", errors="replace")
    return str(value or "")


def _redact_query_segments(text: str) -> str:
    """Redact query-like suffixes in one pass, without regex backtracking."""
    parts: list[str] = []
    cursor = 0
    length = len(text)
    while cursor < length:
        question = text.find("?", cursor)
        if question < 0:
            parts.append(text[cursor:])
            break
        parts.append(text[cursor:question])
        end = question + 1
        while end < length and not text[end].isspace() and text[end] not in "'\"":
            end += 1
        parts.append("?<redacted>")
        cursor = end
    return "".join(parts)


def sanitize_log_text(value: object, max_chars: int = 4096) -> str:
    """Return one bounded line safe for terminals, JSON logs, and admin display."""
    limit = max(64, min(int(max_chars), 65536))
    source_limit = min(65536, max(256, limit * 2))
    if isinstance(value, str):
        source_truncated = len(value) > source_limit
        text = value[:source_limit]
    elif isinstance(value, (bytes, bytearray, memoryview)):
        source_truncated = len(value) > source_limit
        text = bytes(value[:source_limit]).decode("utf-8", errors="replace")
    else:
        text = str(value if value is not None else "")
        source_truncated = len(text) > source_limit
        text = text[:source_limit]
    text = ANSI_ESCAPE_RE.sub("", text)
    text = COOKIE_HEADER_RE.sub(lambda match: f"{match.group('prefix')}<redacted>", text)
    text = text.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    text = CONTROL_RE.sub(" ", text)
    text = URL_USERINFO_RE.sub(lambda match: f"{match.group('scheme')}<redacted>@", text)
    text = _redact_query_segments(text)
    text = COOKIE_ASSIGNMENT_RE.sub(lambda match: f"{match.group('prefix')}<redacted>", text)
    text = SENSITIVE_ASSIGNMENT_RE.sub(lambda match: f"{match.group('prefix')}<redacted>", text)
    text = BEARER_RE.sub(lambda match: f"{match.group(1)} <redacted>", text)
    text = ENDPOINT_ASSIGNMENT_RE.sub(lambda match: f"{match.group('prefix')}<adb-endpoint>", text)
    text = ADB_ENDPOINT_RE.sub("<adb-endpoint>", text)
    text = re.sub(r" {2,}", " ", text).strip()
    if source_truncated or len(text) > limit:
        return f"{text[: max(0, limit - len(TRUNCATED_MARKER))]}{TRUNCATED_MARKER}"
    return text


def sanitize_log_value(value: object, field_name: object = "", *, depth: int = 0) -> object:
    if _is_sensitive_field(field_name):
        return "<redacted>"
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        if depth >= 3:
            return {"_truncated": True}
        result: dict[str, object] = {}
        for index, (raw_key, raw_value) in enumerate(value.items()):
            if index >= 32:
                result["_truncated"] = True
                break
            key = FIELD_NAME_RE.sub("_", str(raw_key or "field")).strip("_")[:80] or "field"
            result[key] = sanitize_log_value(raw_value, key, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        if depth >= 3:
            return [TRUNCATED_MARKER]
        if isinstance(value, (list, tuple)) and len(value) == 2:
            header_name = _header_name(value[0])
            if _is_sensitive_field(header_name):
                return [sanitize_log_text(header_name, 160), "<redacted>"]
        iterator = iter(value)
        items: list[object] = []
        for _index in range(33):
            try:
                items.append(next(iterator))
            except StopIteration:
                break
        sanitized = [sanitize_log_value(item, field_name, depth=depth + 1) for item in items[:32]]
        if len(items) > 32:
            sanitized.append(TRUNCATED_MARKER)
        return sanitized
    if depth >= 3:
        return sanitize_log_text(value, 512)
    return sanitize_log_text(value, 2048)


def normalize_request_id(value: object = "") -> str:
    candidate = sanitize_log_text(value, 96)
    if candidate and REQUEST_ID_RE.fullmatch(candidate):
        return candidate
    return uuid.uuid4().hex


def normalize_event_name(value: object) -> str:
    event = EVENT_NAME_RE.sub("_", str(value or "event").strip().lower()).strip("_.-")
    return event[:80] or "event"


def event_name_for_record(record: logging.LogRecord) -> str:
    explicit = getattr(record, "event_name", "")
    if explicit:
        return normalize_event_name(explicit)
    first_token = str(record.getMessage() or "").split(" ", 1)[0].rstrip(":")
    if re.fullmatch(r"[A-Z][A-Z0-9_.-]{1,79}", first_token):
        return normalize_event_name(first_token)
    return normalize_event_name(f"{record.name}.log")


def bind_log_context(**values: object):
    current = dict(_log_context.get())
    for raw_key, value in values.items():
        key = FIELD_NAME_RE.sub("_", str(raw_key)).strip("_")[:80]
        if key and value not in (None, ""):
            current[key] = sanitize_log_value(value, key)
    return _log_context.set(current)


def reset_log_context(token) -> None:
    _log_context.reset(token)


def current_log_context() -> dict[str, object]:
    return dict(_log_context.get())


def _sanitized_record_copy(record: logging.LogRecord) -> logging.LogRecord:
    safe = copy.copy(record)
    if isinstance(safe.args, dict):
        safe.args = sanitize_log_value(safe.args)
    elif isinstance(safe.args, tuple):
        safe.args = tuple(sanitize_log_value(value) for value in safe.args)
    try:
        rendered = safe.getMessage()
    except Exception:
        rendered = str(safe.msg)
    safe.msg = sanitize_log_text(rendered)
    safe.args = ()
    safe._scrcpygate_context = {
        key: sanitize_log_value(value, key) for key, value in _log_context.get().items()
    }
    return safe


def _rfc3339(epoch_seconds: float) -> str:
    return datetime.fromtimestamp(epoch_seconds, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _severity_number(level: int) -> int:
    if level >= logging.CRITICAL:
        return _SEVERITY_NUMBERS[logging.CRITICAL]
    if level >= logging.ERROR:
        return _SEVERITY_NUMBERS[logging.ERROR]
    if level >= logging.WARNING:
        return _SEVERITY_NUMBERS[logging.WARNING]
    if level >= logging.INFO:
        return _SEVERITY_NUMBERS[logging.INFO]
    return _SEVERITY_NUMBERS[logging.DEBUG]


class SafeJsonFormatter(logging.Formatter):
    """NDJSON formatter aligned with common OpenTelemetry log fields."""

    def format(self, record: logging.LogRecord) -> str:
        observed = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        context = sanitize_log_value(getattr(record, "_scrcpygate_context", {}))
        if not isinstance(context, dict):
            context = {}
        raw_fields = getattr(record, "event_fields", {})
        fields = sanitize_log_value(raw_fields)
        if not isinstance(fields, dict):
            fields = {"value": fields}
        event_name = event_name_for_record(record)
        payload: dict[str, object] = {
            "timestamp": _rfc3339(record.created),
            "observed_timestamp": observed,
            "severity_text": record.levelname,
            "severity_number": _severity_number(record.levelno),
            "event_name": event_name,
            "body": sanitize_log_text(record.getMessage()),
            "logger": sanitize_log_text(record.name, 160),
            "service": SERVICE_NAME,
        }
        for key in ("request_id", "trace_id", "span_id", "actor", "source_ip"):
            value = context.pop(key, None)
            if value not in (None, ""):
                payload[key] = value
        attributes = {
            "code.module": sanitize_log_text(record.module, 160),
            "code.function": sanitize_log_text(record.funcName, 160),
            "code.line": record.lineno,
            **context,
            **fields,
        }
        if record.exc_info:
            exc_type, exc_value, _traceback = record.exc_info
            attributes["exception.type"] = getattr(exc_type, "__name__", str(exc_type))
            attributes["exception.message"] = sanitize_log_text(exc_value, 2048)
            attributes["exception.stacktrace"] = sanitize_log_text(self.formatException(record.exc_info), 16384)
        payload["attributes"] = attributes
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class SafeTextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        context = getattr(record, "_scrcpygate_context", {}) or {}
        request_id = sanitize_log_text(context.get("request_id", "-"), 96)
        event_name = event_name_for_record(record)
        fields = sanitize_log_value(getattr(record, "event_fields", {}))
        suffix = ""
        if fields:
            suffix = " " + json.dumps(fields, ensure_ascii=False, separators=(",", ":"))
        message = sanitize_log_text(record.getMessage())
        if record.exc_info:
            message = sanitize_log_text(f"{message} {self.formatException(record.exc_info)}", 16384)
        level_name = sanitize_log_text(record.levelname, 24)
        logger_name = sanitize_log_text(record.name, 160)
        return f"{_rfc3339(record.created)} [{level_name}] {logger_name} request_id={request_id} event={event_name}: {message}{suffix}"


class SecureRotatingFileHandler(RotatingFileHandler):
    def _open(self):
        stream = super()._open()
        try:
            os.chmod(self.baseFilename, 0o600)
        except OSError:
            pass
        return stream


class ResilientQueueHandler(QueueHandler):
    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        return _sanitized_record_copy(record)

    def enqueue(self, record: logging.LogRecord) -> None:
        maxsize = int(getattr(self.queue, "maxsize", 0) or 0)
        if record.levelno < logging.ERROR and maxsize >= 8:
            reserved = max(1, min(64, maxsize // 8))
            if self.queue.qsize() >= maxsize - reserved:
                _count_dropped_record(record)
                return
        try:
            self.queue.put_nowait(record)
        except queue.Full:
            if record.levelno >= logging.ERROR:
                enqueued, evicted = self._enqueue_over_lower_priority(record)
                if enqueued:
                    if evicted is not None:
                        _count_dropped_record(evicted)
                    return
            _count_dropped_record(record)

    def _enqueue_over_lower_priority(
        self, record: logging.LogRecord
    ) -> tuple[bool, logging.LogRecord | None]:
        log_queue = self.queue
        if type(log_queue) is not queue.Queue:
            return False, None
        with log_queue.mutex:
            if log_queue.maxsize <= 0 or log_queue._qsize() < log_queue.maxsize:
                log_queue._put(record)
                log_queue.unfinished_tasks += 1
                log_queue.not_empty.notify()
                return True, None
            candidate_index = -1
            candidate_level = record.levelno
            for index, queued_record in enumerate(log_queue.queue):
                if not isinstance(queued_record, logging.LogRecord):
                    continue
                queued_level = int(getattr(queued_record, "levelno", logging.NOTSET))
                if queued_level < candidate_level:
                    candidate_index = index
                    candidate_level = queued_level
            if candidate_index < 0:
                return False, None
            evicted = log_queue.queue[candidate_index]
            del log_queue.queue[candidate_index]
            log_queue._put(record)
            return True, evicted


def _count_dropped_record(record: logging.LogRecord) -> None:
    global _dropped_records
    level = str(logging.getLevelName(record.levelno)).lower()
    with _drop_lock:
        _dropped_records += 1
        _dropped_records_by_level[level] = _dropped_records_by_level.get(level, 0) + 1


class FlushableQueueListener(QueueListener):
    def enqueue_sentinel(self) -> None:
        try:
            self.queue.put(self._sentinel, timeout=5)
        except queue.Full:
            try:
                self.queue.get_nowait()
                self.queue.task_done()
            except (queue.Empty, ValueError):
                pass
            self.queue.put_nowait(self._sentinel)


def _protect_protocol_logs() -> None:
    """Keep WebSocket frames, URLs with credentials, and debug payloads out of logs."""
    for logger_name in SENSITIVE_PROTOCOL_LOGGERS:
        protocol_logger = logging.getLogger(logger_name)
        if protocol_logger.level == logging.NOTSET or protocol_logger.level < logging.INFO:
            protocol_logger.setLevel(logging.INFO)
        if logger_name == "uvicorn" or logger_name.startswith("uvicorn."):
            for handler in list(protocol_logger.handlers):
                protocol_logger.removeHandler(handler)
            protocol_logger.propagate = True
    for logger_name in NOISY_LIBRARY_LOGGERS:
        library_logger = logging.getLogger(logger_name)
        if library_logger.level == logging.NOTSET or library_logger.level < logging.WARNING:
            library_logger.setLevel(logging.WARNING)


def _configuration_values() -> tuple:
    level_name = os.environ.get("LOG_LEVEL", "INFO").strip().upper() or "INFO"
    level = getattr(logging, level_name, logging.INFO)
    output_format = os.environ.get("LOG_FORMAT", "json").strip().lower()
    if output_format not in {"json", "text"}:
        output_format = "json"
    return (
        str(LOG_FILE.resolve()),
        level,
        output_format,
        _env_int("LOG_QUEUE_SIZE", 2048, 128, 65536),
        _env_int("LOG_MAX_BYTES", 5 * 1024 * 1024, 256 * 1024, 1024 * 1024 * 1024),
        _env_int("LOG_BACKUP_COUNT", 5, 1, 50),
        _env_bool("LOG_FILE_ENABLED", True),
    )


def _stop_logging_locked() -> None:
    global _configured, _configured_signature, _listener, _queue_handler, _output_handlers
    root = logging.getLogger()
    if _queue_handler is not None:
        try:
            root.removeHandler(_queue_handler)
        except Exception:
            pass
    listener = _listener
    _listener = None
    if listener is not None:
        try:
            listener.stop()
        except Exception:
            pass
    for handler in _output_handlers:
        try:
            handler.flush()
            handler.close()
        except Exception:
            pass
    _output_handlers = []
    _queue_handler = None
    _configured = False
    _configured_signature = None


def setup_logging() -> None:
    global _configured, _configured_signature, _listener, _queue_handler, _output_handlers, _atexit_registered
    _refresh_paths()
    _protect_protocol_logs()
    signature = _configuration_values()
    with _configuration_lock:
        if _configured and _configured_signature == signature:
            return
        if _configured:
            _stop_logging_locked()
        _path, level, output_format, queue_size, max_bytes, backup_count, file_enabled = signature
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        formatter: logging.Formatter = SafeJsonFormatter() if output_format == "json" else SafeTextFormatter()

        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        stream.setLevel(level)
        handlers: list[logging.Handler] = [stream]
        if file_enabled:
            try:
                file_handler = SecureRotatingFileHandler(
                    LOG_FILE,
                    maxBytes=max_bytes,
                    backupCount=backup_count,
                    encoding="utf-8",
                )
                file_handler.setFormatter(formatter)
                file_handler.setLevel(level)
                handlers.append(file_handler)
            except OSError as exc:
                try:
                    sys.stderr.write(f"ScrcpyGate file logging unavailable: {sanitize_log_text(exc, 300)}\n")
                except Exception:
                    pass

        log_queue: queue.Queue = queue.Queue(maxsize=queue_size)
        queue_handler = ResilientQueueHandler(log_queue)
        queue_handler.setLevel(level)
        listener = FlushableQueueListener(log_queue, *handlers, respect_handler_level=True)
        root = logging.getLogger()
        root.setLevel(level)
        root.addHandler(queue_handler)
        listener.start()

        _queue_handler = queue_handler
        _listener = listener
        _output_handlers = handlers
        _configured_signature = signature
        _configured = True
        if not _atexit_registered:
            atexit.register(shutdown_logging)
            _atexit_registered = True


def shutdown_logging() -> None:
    with _configuration_lock:
        _stop_logging_locked()


def log_event(logger: logging.Logger, event_name: str, *, level: int = logging.INFO, **fields: object) -> None:
    normalized = normalize_event_name(event_name)
    logger.log(
        level,
        normalized,
        extra={"event_name": normalized, "event_fields": fields},
        stacklevel=2,
    )


def logging_health() -> dict[str, object]:
    listener_thread = getattr(_listener, "_thread", None)
    with _drop_lock:
        dropped = _dropped_records
        dropped_by_level = dict(_dropped_records_by_level)
    queue_depth = 0
    queue_capacity = 0
    if _queue_handler is not None:
        try:
            queue_depth = _queue_handler.queue.qsize()
            queue_capacity = _queue_handler.queue.maxsize
        except Exception:
            pass
    return {
        "configured": _configured,
        "format": (_configured_signature or (None, None, "json"))[2],
        "listener_alive": bool(listener_thread and listener_thread.is_alive()),
        "queue_depth": queue_depth,
        "queue_capacity": queue_capacity,
        "dropped_records": dropped,
        "dropped_records_by_level": dropped_by_level,
        "file_configured": bool(_configured_signature and _configured_signature[-1]),
        "file_active": any(isinstance(handler, SecureRotatingFileHandler) for handler in _output_handlers),
    }


def tail_log(max_lines: int = 300) -> list[str]:
    """Read only the bounded tail of the active cache file, never the whole log."""
    _refresh_paths()
    limit = max(20, min(int(max_lines), 2000))
    if not LOG_FILE.exists():
        return []
    max_read = _env_int("LOG_TAIL_MAX_BYTES", 2 * 1024 * 1024, 64 * 1024, 16 * 1024 * 1024)
    block_size = 8192
    data = b""
    try:
        with LOG_FILE.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            position = handle.tell()
            while position > 0 and data.count(b"\n") <= limit and len(data) < max_read:
                read_size = min(block_size, position, max_read - len(data))
                if read_size <= 0:
                    break
                position -= read_size
                handle.seek(position)
                data = handle.read(read_size) + data
    except OSError:
        return []
    return [line.decode("utf-8", "replace") for line in data.splitlines()[-limit:]]
