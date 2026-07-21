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
import time
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
AUTHORIZATION_FIELD_RE = re.compile(
    r"(?i)\b(?P<prefix>[\"']?authorization[\"']?\s*[:=]\s*)[^\r\n]*"
)
COOKIE_ASSIGNMENT_RE = re.compile(
    r"(?i)(?P<prefix>[\"']?(?:set[_-]?cookie|cookie)[\"']?\s*=\s*)"
    r"(?P<value>\"(?:\\\\.|[^\"\\\\])*\"|'(?:\\\\.|[^'\\\\])*'|[^\r\n,}\]]+)"
)
SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)(?P<prefix>[\"']?(?:password|passwd|pwd|token|access[_-]?token|refresh[_-]?token|"
    r"api[_-]?key|secret|authorization|cookie|csrf(?:[_-]?token)?|session[_-]?id|sid|"
    r"private[_-]?key|database[_-]?url|connection[_-]?string|alas[_-]?token)[\"']?\s*[:=]\s*)"
    r"(?P<value>(?:Bearer|Basic)\s+[^\s,;}\]]+|"
    r'\"(?:\\.|[^\"\\])*\"|'
    r"'(?:\\.|[^'\\])*'|[^\s,;}\]]+)"
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
RUNTIME_LOG_SEVERITIES = ("debug", "info", "warning", "error", "critical")
RUNTIME_LOG_TEXT_FIELDS_MARKER = " scrcpygate_fields="
_TRUSTED_LOG_FIELD_NAMES = frozenset(
    {
        "request_id",
        "trace_id",
        "span_id",
        "actor",
        "source_ip",
        "service",
        "observed_timestamp",
    }
)
_LEGACY_RUNTIME_FIELD_NAMES = frozenset(
    {
        *_TRUSTED_LOG_FIELD_NAMES,
        "action",
        "component",
        "connection_id",
        "device_id",
        "device_ref",
        "duration_ms",
        "error_type",
        "http_method",
        "http_route",
        "http_status_code",
        "outcome",
        "queue_size",
        "stream_mode",
        "unfinished",
        "username",
    }
)
_LEGACY_RUNTIME_FIELD_PREFIXES = (
    "account_",
    "adb_",
    "alas_",
    "audit_",
    "control_",
    "exception_",
    "http_",
    "log_",
    "mirror_",
    "scrcpy_",
    "security_",
    "video_",
)
_RUNTIME_LOG_EVENT_TOKEN_RE = re.compile(
    r"^(?P<event>[A-Z][A-Z0-9_.-]{1,79})(?:\s+(?P<fields>.*))?$"
)
_RUNTIME_LOG_KEY_VALUE_RE = re.compile(
    r"(?<!\S)(?P<key>[A-Za-z][A-Za-z0-9_.-]{0,79})="
)
_RUNTIME_LOG_INTEGER_RE = re.compile(r"^[+-]?\d+$")
_RUNTIME_LOG_FLOAT_RE = re.compile(
    r"^[+-]?(?:(?:\d+\.\d*)|(?:\.\d+)|(?:\d+))(?:[eE][+-]?\d+)$|"
    r"^[+-]?(?:(?:\d+\.\d*)|(?:\.\d+))$"
)
_RUNTIME_LOG_MAX_INFERRED_FIELDS = 32
RUNTIME_LOG_SEVERITY_RANK = {
    "unknown": -1,
    "debug": 0,
    "info": 1,
    "warning": 2,
    "error": 3,
    "critical": 4,
}
_RUNTIME_LOG_SEVERITY_NUMBERS = {
    "unknown": 0,
    "debug": 5,
    "info": 9,
    "warning": 13,
    "error": 17,
    "critical": 21,
}
_RUNTIME_LOG_SEVERITY_ALIASES = {
    "trace": "debug",
    "debug": "debug",
    "notice": "info",
    "info": "info",
    "warn": "warning",
    "warning": "warning",
    "err": "error",
    "error": "error",
    "crit": "critical",
    "critical": "critical",
    "fatal": "critical",
    "panic": "critical",
}
_RUNTIME_LOG_PREFIX_RE = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2}[T ][^\[]*?)\s+"
    r"\[(?P<severity>[A-Za-z]+)\]\s+(?P<tail>.+)$"
)
_RUNTIME_LOG_CONTEXT_RE = re.compile(
    r"^(?P<logger>\S+)\s+request_id=(?P<request_id>\S+)\s+"
    r"event=(?P<event_name>[^:]+):\s?(?P<message>.*)$"
)
_RUNTIME_LOG_LEGACY_RE = re.compile(r"^(?P<logger>[^:]+):\s?(?P<message>.*)$")
_RUNTIME_LOG_EXCEPTION_RE = re.compile(
    r"^(?:(?:[A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception|Warning))|"
    r"KeyboardInterrupt|StopIteration|Caused by)(?::|\(|\s|$)"
)
_RUNTIME_LOG_EXCEPTION_CONTINUATION_PREFIXES = (
    "Traceback (most recent call last):",
    "During handling of the above exception",
    "The above exception was the direct cause",
    "Task exception was never retrieved",
    "future:",
    "Exception ignored in:",
)
_RUNTIME_LOG_ENTRY_MAX_CHARS = 16384
_RUNTIME_LOG_JSON_MARKER_FIELDS = frozenset(
    {
        "timestamp",
        "time",
        "ts",
        "severity_text",
        "severity_number",
        "severity",
        "level",
        "logger",
        "logger_name",
        "event_name",
        "event",
        "body",
        "message",
    }
)
_RUNTIME_LOG_JSON_CONTEXT_FIELDS = (
    "observed_timestamp",
    "service",
    "trace_id",
    "span_id",
    "actor",
    "source_ip",
)
_RUNTIME_LOG_JSON_CONSUMED_FIELDS = _RUNTIME_LOG_JSON_MARKER_FIELDS | frozenset(
    {"request_id", "attributes", *_RUNTIME_LOG_JSON_CONTEXT_FIELDS}
)
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
    text = AUTHORIZATION_FIELD_RE.sub(lambda match: f"{match.group('prefix')}<redacted>", text)
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


def sanitize_log_multiline_text(value: object, max_chars: int = 16384) -> str:
    """Sanitize a bounded traceback while retaining line breaks and indentation."""
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
    text = AUTHORIZATION_FIELD_RE.sub(lambda match: f"{match.group('prefix')}<redacted>", text)
    text = CONTROL_RE.sub(" ", text)
    text = URL_USERINFO_RE.sub(lambda match: f"{match.group('scheme')}<redacted>@", text)
    text = _redact_query_segments(text)
    text = COOKIE_ASSIGNMENT_RE.sub(lambda match: f"{match.group('prefix')}<redacted>", text)
    text = SENSITIVE_ASSIGNMENT_RE.sub(lambda match: f"{match.group('prefix')}<redacted>", text)
    text = BEARER_RE.sub(lambda match: f"{match.group(1)} <redacted>", text)
    text = ENDPOINT_ASSIGNMENT_RE.sub(lambda match: f"{match.group('prefix')}<adb-endpoint>", text)
    text = ADB_ENDPOINT_RE.sub("<adb-endpoint>", text)
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


def _safe_event_fields(record: logging.LogRecord) -> dict[str, object]:
    fields = sanitize_log_value(getattr(record, "event_fields", {}))
    if not isinstance(fields, dict):
        fields = {"value": fields}
    return _without_trusted_log_fields(fields)


def _without_trusted_log_fields(fields: dict[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in fields.items()
        if _normalized_field_name(key) not in _TRUSTED_LOG_FIELD_NAMES
    }


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
        fields = _safe_event_fields(record)
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
            attributes["exception.stacktrace"] = sanitize_log_multiline_text(
                self.formatException(record.exc_info), 16384
            )
        payload["attributes"] = attributes
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class SafeTextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        context = sanitize_log_value(getattr(record, "_scrcpygate_context", {}) or {})
        if not isinstance(context, dict):
            context = {}
        request_id = sanitize_log_text(context.pop("request_id", "-"), 96)
        trusted_context: dict[str, object] = {}
        for key in ("trace_id", "span_id", "actor", "source_ip"):
            value = context.pop(key, None)
            if value not in (None, ""):
                trusted_context[key] = value
        event_name = event_name_for_record(record)
        fields = _safe_event_fields(record)
        attributes = {**context, **fields, **trusted_context}
        if record.exc_info:
            exc_type, exc_value, _traceback = record.exc_info
            attributes["exception.type"] = sanitize_log_text(
                getattr(exc_type, "__name__", str(exc_type)), 160
            )
            attributes["exception.message"] = sanitize_log_text(exc_value, 2048)
            attributes["exception.stacktrace"] = sanitize_log_multiline_text(
                self.formatException(record.exc_info), 16384
            )
        suffix = RUNTIME_LOG_TEXT_FIELDS_MARKER + json.dumps(
            attributes, ensure_ascii=False, separators=(",", ":")
        )
        message = sanitize_log_text(record.getMessage())
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


def _runtime_log_read_barrier(timeout: float = 0.5) -> tuple[bool, int]:
    """Wait briefly for records already accepted by the queue, then flush outputs."""
    queue_handler = _queue_handler
    if queue_handler is None:
        return True, 0
    log_queue = queue_handler.queue
    deadline = time.monotonic() + max(0.0, min(float(timeout), 2.0))
    pending = 0
    condition = getattr(log_queue, "all_tasks_done", None)
    if condition is not None:
        with condition:
            while True:
                pending = max(0, int(getattr(log_queue, "unfinished_tasks", 0) or 0))
                if pending <= 0:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                condition.wait(timeout=min(remaining, 0.05))
    else:
        try:
            pending = max(0, int(log_queue.qsize()))
        except Exception:
            pending = 0
    for handler in tuple(_output_handlers):
        try:
            handler.flush()
        except Exception:
            pass
    return pending <= 0, pending


def _strip_log_line_ending(record: bytes) -> bytes:
    if record.endswith(b"\r\n"):
        return record[:-2]
    if record.endswith((b"\r", b"\n")):
        return record[:-1]
    return record


def _split_text_log_records(raw_text: str) -> list[str]:
    """Split only CR/LF separators; Unicode line-separator characters are log content."""
    records: list[str] = []
    start = 0
    index = 0
    while index < len(raw_text):
        char = raw_text[index]
        if char == "\r":
            index += 2 if index + 1 < len(raw_text) and raw_text[index + 1] == "\n" else 1
            records.append(raw_text[start:index])
            start = index
            continue
        if char == "\n":
            index += 1
            records.append(raw_text[start:index])
            start = index
            continue
        index += 1
    if start < len(raw_text):
        records.append(raw_text[start:])
    return records


def _split_binary_log_records(raw_data: bytes) -> list[bytes]:
    """Split bytes only on CR/LF so control separators remain log content."""
    records: list[bytes] = []
    start = 0
    index = 0
    while index < len(raw_data):
        byte = raw_data[index]
        if byte == 0x0D:
            index += 2 if index + 1 < len(raw_data) and raw_data[index + 1] == 0x0A else 1
            records.append(raw_data[start:index])
            start = index
            continue
        if byte == 0x0A:
            index += 1
            records.append(raw_data[start:index])
            start = index
            continue
        index += 1
    if start < len(raw_data):
        records.append(raw_data[start:])
    return records


def _binary_log_record_count(raw_data: bytes) -> int:
    """Count CR/LF-delimited records without allocating slices during tail reads."""
    if not raw_data:
        return 0
    separators = raw_data.count(b"\r") + raw_data.count(b"\n") - raw_data.count(b"\r\n")
    return separators + (1 if raw_data[-1] not in {0x0D, 0x0A} else 0)


def _tail_log_snapshot(max_lines: int = 300) -> tuple[list[str], str, dict[str, object]]:
    """Read a bounded tail while retaining exact separators for the selected lines."""
    _refresh_paths()
    limit = max(20, min(int(max_lines), 2000))
    if not LOG_FILE.exists():
        return [], "", {
            "byte_limit_hit": False,
            "line_limit_hit": False,
            "partial_first_line": False,
        }
    max_read = _env_int("LOG_TAIL_MAX_BYTES", 2 * 1024 * 1024, 64 * 1024, 16 * 1024 * 1024)
    block_size = 65536
    data = b""
    position = 0
    file_size = 0
    previous = b""
    try:
        with LOG_FILE.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            file_size = handle.tell()
            position = file_size
            while position > 0 and _binary_log_record_count(data) <= limit and len(data) < max_read:
                read_size = min(block_size, position, max_read - len(data))
                if read_size <= 0:
                    break
                position -= read_size
                handle.seek(position)
                data = handle.read(read_size) + data
            if position > 0:
                handle.seek(position - 1)
                previous = handle.read(1)
    except OSError:
        return [], "", {
            "byte_limit_hit": False,
            "line_limit_hit": False,
            "partial_first_line": False,
        }
    bytes_read = len(data)
    byte_limit_hit = position > 0 and len(data) >= max_read
    records = _split_binary_log_records(data)
    line_threshold_hit = len(records) > limit
    partial_first_line = False
    if position > 0 and data:
        if previous == b"\r" and data.startswith(b"\n"):
            data = data[1:]
        elif previous not in {b"\r", b"\n"}:
            partial_first_line = True
            first_ending = re.search(br"\r\n|\r|\n", data)
            data = data[first_ending.end() :] if first_ending else b""

    records = _split_binary_log_records(data)
    line_limit_hit = line_threshold_hit or len(records) > limit
    selected = records[-limit:]
    raw_text = b"".join(selected).decode("utf-8", "replace")
    lines = [
        _strip_log_line_ending(record).decode("utf-8", "replace")
        for record in selected
    ]
    return lines, raw_text, {
        "byte_limit_hit": byte_limit_hit,
        "line_limit_hit": line_limit_hit,
        "partial_first_line": partial_first_line,
        "file_size_bytes": file_size,
        "tail_bytes_read": bytes_read,
    }


def tail_log(max_lines: int = 300) -> list[str]:
    """Read only the bounded tail of the active cache file, never the whole log."""
    lines, _raw_text, _meta = _tail_log_snapshot(max_lines)
    return lines


def _runtime_log_severity_number(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if 1 <= number <= 24 else None


def normalize_runtime_log_severity(value: object = "", severity_number: object = None) -> str:
    """Normalize OpenTelemetry or text log levels for the admin log viewer."""
    number = _runtime_log_severity_number(severity_number)
    if number is not None:
        if number <= 8:
            return "debug"
        if number <= 12:
            return "info"
        if number <= 16:
            return "warning"
        if number <= 20:
            return "error"
        return "critical"
    return _RUNTIME_LOG_SEVERITY_ALIASES.get(str(value or "").strip().lower(), "unknown")


def normalize_runtime_log_filter(value: object = "") -> str:
    candidate = str(value or "").strip().lower()
    if not candidate:
        return ""
    normalized = _RUNTIME_LOG_SEVERITY_ALIASES.get(candidate, candidate)
    if normalized not in RUNTIME_LOG_SEVERITIES:
        raise ValueError("invalid runtime log severity")
    return normalized


def _runtime_log_scalar(value: str, field_name: str) -> object:
    candidate = value.strip()
    if len(candidate) >= 2 and candidate[0] == candidate[-1] and candidate[0] in {"'", '"'}:
        candidate = candidate[1:-1]
    lowered = candidate.lower()
    if lowered == "true":
        parsed: object = True
    elif lowered == "false":
        parsed = False
    elif lowered in {"none", "null"}:
        parsed = None
    elif len(candidate) <= 20 and _RUNTIME_LOG_INTEGER_RE.fullmatch(candidate):
        try:
            parsed = int(candidate)
        except (TypeError, ValueError, OverflowError):
            parsed = candidate
    elif len(candidate) <= 32 and _RUNTIME_LOG_FLOAT_RE.fullmatch(candidate):
        try:
            numeric = float(candidate)
        except (TypeError, ValueError, OverflowError):
            parsed = candidate
        else:
            parsed = numeric if math.isfinite(numeric) else candidate
    else:
        parsed = candidate
    return sanitize_log_value(parsed, field_name)


def _runtime_log_message_fields(message: str) -> tuple[str, dict[str, object]]:
    """Recover bounded fields from legacy `EVENT key=value` messages."""
    event_match = _RUNTIME_LOG_EVENT_TOKEN_RE.fullmatch(message)
    if not event_match:
        return "", {}
    event_name = normalize_event_name(event_match.group("event"))
    field_text = event_match.group("fields") or ""
    matches = []
    for match in _RUNTIME_LOG_KEY_VALUE_RE.finditer(field_text):
        matches.append(match)
        if len(matches) > _RUNTIME_LOG_MAX_INFERRED_FIELDS:
            break
    if not matches or matches[0].start() != 0:
        return event_name, {}
    inferred: dict[str, object] = {}
    usable = min(len(matches), _RUNTIME_LOG_MAX_INFERRED_FIELDS)
    for index in range(usable):
        match = matches[index]
        key = FIELD_NAME_RE.sub("_", match.group("key")).strip("_")[:80]
        if not key or _normalized_field_name(key) in _TRUSTED_LOG_FIELD_NAMES:
            continue
        value_end = matches[index + 1].start() if index + 1 < len(matches) else len(field_text)
        raw_value = field_text[match.end() : value_end].strip()
        inferred[key] = _runtime_log_scalar(raw_value, key)
    return event_name, inferred


def _terminal_json_object(candidate: str) -> tuple[int, dict[str, object]] | None:
    """Find and decode one JSON object ending at the end of a bounded log line."""
    if not candidate.endswith("}"):
        return None
    depth = 0
    in_string = False
    object_start = -1
    for index in range(len(candidate) - 1, -1, -1):
        char = candidate[index]
        if char == '"':
            backslashes = 0
            cursor = index - 1
            while cursor >= 0 and candidate[cursor] == "\\":
                backslashes += 1
                cursor -= 1
            if backslashes % 2 == 0:
                in_string = not in_string
            continue
        if in_string:
            continue
        if char == "}":
            depth += 1
        elif char == "{":
            depth -= 1
            if depth == 0:
                object_start = index
                break
            if depth < 0:
                return None
    if object_start < 0 or depth != 0 or in_string:
        return None
    try:
        payload = json.loads(candidate[object_start:])
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return object_start, payload


def _looks_like_legacy_runtime_fields(payload: dict[str, object]) -> bool:
    for raw_key in payload:
        key = _normalized_field_name(raw_key)
        if key in _LEGACY_RUNTIME_FIELD_NAMES or key.startswith(_LEGACY_RUNTIME_FIELD_PREFIXES):
            return True
    return False


def _runtime_log_entry(
    *,
    timestamp: object = "",
    severity_text: object = "",
    severity_number: object = None,
    logger: object = "",
    event_name: object = "",
    message: object = "",
    request_id: object = "",
    attributes: object = None,
    raw: object = "",
    entry_format: str,
    parse_failed: bool = False,
) -> dict[str, object]:
    severity = normalize_runtime_log_severity(severity_text, severity_number)
    normalized_number = _runtime_log_severity_number(severity_number)
    safe_message = sanitize_log_text(message, 16384)
    inferred_event_name, inferred_attributes = _runtime_log_message_fields(safe_message)
    safe_attributes = sanitize_log_value(attributes or {})
    if not isinstance(safe_attributes, dict):
        safe_attributes = {"value": safe_attributes}
    merged_attributes = {**inferred_attributes, **safe_attributes}
    return {
        "timestamp": sanitize_log_text(timestamp, 96),
        "severity": severity,
        "severity_number": normalized_number or _RUNTIME_LOG_SEVERITY_NUMBERS[severity],
        "logger": sanitize_log_text(logger, 160),
        "event_name": sanitize_log_text(event_name or inferred_event_name, 160),
        "message": safe_message,
        "request_id": sanitize_log_text(request_id, 96),
        "attributes": merged_attributes,
        "raw": sanitize_log_text(raw, 16384),
        "format": entry_format,
        "parse_failed": bool(parse_failed),
    }


def _parse_runtime_json_line(line: str) -> dict[str, object] | None:
    if not line.lstrip().startswith("{"):
        return None
    try:
        payload = json.loads(line)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if not any(key in payload for key in _RUNTIME_LOG_JSON_MARKER_FIELDS):
        return _runtime_log_entry(
            message=line,
            raw=line,
            entry_format="unknown",
            parse_failed=True,
        )
    raw_attributes = payload.get("attributes")
    if isinstance(raw_attributes, dict):
        attributes = _without_trusted_log_fields(dict(raw_attributes))
    elif raw_attributes not in (None, ""):
        attributes = {"log.attributes": raw_attributes}
    else:
        attributes = {}
    for key in _RUNTIME_LOG_JSON_CONTEXT_FIELDS:
        value = payload.get(key)
        if value not in (None, ""):
            attributes[key] = value
    for key, value in payload.items():
        if key not in _RUNTIME_LOG_JSON_CONSUMED_FIELDS:
            attributes.setdefault(f"json.{key}", value)
    return _runtime_log_entry(
        timestamp=payload.get("timestamp") or payload.get("time") or payload.get("ts"),
        severity_text=payload.get("severity_text") or payload.get("level") or payload.get("severity"),
        severity_number=payload.get("severity_number"),
        logger=payload.get("logger") or payload.get("logger_name"),
        event_name=payload.get("event_name") or payload.get("event"),
        message=payload.get("body") if payload.get("body") is not None else payload.get("message"),
        request_id=payload.get("request_id"),
        attributes=attributes,
        raw=line,
        entry_format="json",
    )


def _split_runtime_text_fields(message: str) -> tuple[str, dict[str, object]]:
    candidate = message.rstrip()
    terminal = _terminal_json_object(candidate)
    if terminal is None:
        return message, {}
    object_start, payload = terminal
    marker_start = object_start - len(RUNTIME_LOG_TEXT_FIELDS_MARKER)
    if marker_start >= 0 and candidate[marker_start:object_start] == RUNTIME_LOG_TEXT_FIELDS_MARKER:
        return candidate[:marker_start], payload
    if (
        object_start > 0
        and candidate[object_start - 1].isspace()
        and _looks_like_legacy_runtime_fields(payload)
    ):
        return candidate[:object_start].rstrip(), _without_trusted_log_fields(payload)
    return message, {}


def _parse_runtime_text_line(line: str) -> dict[str, object] | None:
    prefix = _RUNTIME_LOG_PREFIX_RE.match(line)
    if not prefix:
        return None
    tail = prefix.group("tail")
    context = _RUNTIME_LOG_CONTEXT_RE.match(tail)
    if context:
        values = context.groupdict()
        message, attributes = _split_runtime_text_fields(values["message"])
        return _runtime_log_entry(
            timestamp=prefix.group("timestamp"),
            severity_text=prefix.group("severity"),
            logger=values["logger"],
            event_name=values["event_name"],
            message=message,
            request_id=values["request_id"],
            attributes=attributes,
            raw=line,
            entry_format="text",
        )
    legacy = _RUNTIME_LOG_LEGACY_RE.match(tail)
    if legacy:
        values = legacy.groupdict()
        message, attributes = _split_runtime_text_fields(values["message"])
        return _runtime_log_entry(
            timestamp=prefix.group("timestamp"),
            severity_text=prefix.group("severity"),
            logger=values["logger"].strip(),
            message=message,
            attributes=attributes,
            raw=line,
            entry_format="legacy",
        )
    return _runtime_log_entry(
        timestamp=prefix.group("timestamp"),
        severity_text=prefix.group("severity"),
        message=tail,
        raw=line,
        entry_format="legacy",
    )


def _append_runtime_continuation(entry: dict[str, object], line: str) -> None:
    raw_line = str(line or "")
    indentation_match = re.match(r"^[ \t]*", raw_line)
    indentation = indentation_match.group(0) if indentation_match else ""
    body = raw_line[len(indentation) :]
    continuation = indentation + sanitize_log_text(
        body, max(64, 4096 - min(len(indentation), 4032))
    )
    current_message = str(entry.get("message") or "")
    combined_message = f"{current_message}\n{continuation}" if current_message else continuation
    current_raw = str(entry.get("raw") or "")
    combined_raw = f"{current_raw}\n{continuation}" if current_raw else continuation
    attributes = entry.get("attributes")
    if not isinstance(attributes, dict):
        attributes = {}
        entry["attributes"] = attributes
    truncated = len(combined_message) > _RUNTIME_LOG_ENTRY_MAX_CHARS or len(combined_raw) > _RUNTIME_LOG_ENTRY_MAX_CHARS
    if truncated:
        content_limit = _RUNTIME_LOG_ENTRY_MAX_CHARS - len(TRUNCATED_MARKER)
        entry["message"] = f"{combined_message[:content_limit]}{TRUNCATED_MARKER}"
        entry["raw"] = f"{combined_raw[:content_limit]}{TRUNCATED_MARKER}"
        attributes["log.truncated"] = True
    else:
        entry["message"] = combined_message
        entry["raw"] = combined_raw
    try:
        current_count = max(0, int(attributes.get("log.continuation_lines", 0) or 0))
    except (TypeError, ValueError, OverflowError):
        current_count = 0
    attributes["log.continuation_lines"] = current_count + 1


def _is_runtime_log_continuation(line: str) -> bool:
    if not line.strip():
        return True
    if line[:1].isspace():
        return True
    return line.startswith(_RUNTIME_LOG_EXCEPTION_CONTINUATION_PREFIXES) or bool(
        _RUNTIME_LOG_EXCEPTION_RE.match(line)
    )


def parse_runtime_log_lines(lines: list[str]) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Parse a bounded mixed-format log tail without dropping unknown content."""
    entries: list[dict[str, object]] = []
    unclassified_lines = 0
    format_counts = {"json": 0, "text": 0, "legacy": 0, "unknown": 0}
    for raw_line in lines:
        line = str(raw_line or "")
        entry = _parse_runtime_json_line(line) or _parse_runtime_text_line(line)
        if entry is not None:
            if entry.get("parse_failed"):
                unclassified_lines += 1
            entries.append(entry)
            entry_format = str(entry.get("format") or "unknown")
            format_counts[entry_format] = format_counts.get(entry_format, 0) + 1
            continue
        unclassified_lines += 1
        if entries and _is_runtime_log_continuation(line):
            _append_runtime_continuation(entries[-1], line)
            continue
        entries.append(
            _runtime_log_entry(
                message=line,
                raw=line,
                entry_format="unknown",
                parse_failed=True,
            )
        )
        format_counts["unknown"] += 1

    severity_counts = {severity: 0 for severity in (*RUNTIME_LOG_SEVERITIES, "unknown")}
    for entry in entries:
        severity = str(entry.get("severity") or "unknown")
        severity_counts[severity if severity in severity_counts else "unknown"] += 1
    return entries, {
        "scanned_lines": len(lines),
        "parsed_entries": len(entries),
        "unclassified_lines": unclassified_lines,
        "severity_counts": severity_counts,
        "format_counts": format_counts,
    }


def runtime_log_snapshot(
    max_entries: int = 300, min_severity: object = ""
) -> tuple[list[str], list[dict[str, object]], dict[str, object]]:
    """Return legacy raw lines plus filtered structured entries from a bounded tail."""
    limit = max(20, min(int(max_entries), 2000))
    severity_filter = normalize_runtime_log_filter(min_severity)
    queue_flush_completed, pending_queue = _runtime_log_read_barrier()
    raw_lines, scanned_raw_text, tail_meta = _tail_log_snapshot(2000)
    entries, meta = parse_runtime_log_lines(raw_lines)
    if severity_filter:
        minimum_rank = RUNTIME_LOG_SEVERITY_RANK[severity_filter]
        matching = [
            entry
            for entry in entries
            if RUNTIME_LOG_SEVERITY_RANK.get(str(entry.get("severity") or "unknown"), -1)
            >= minimum_rank
        ]
    else:
        matching = entries
    returned = matching[-limit:]
    selected_raw_lines = raw_lines[-limit:]
    selected_raw_records = _split_text_log_records(scanned_raw_text)[-limit:]
    raw_text = "".join(selected_raw_records)
    line_limit_hit = bool(tail_meta.get("line_limit_hit")) or len(raw_lines) > limit or len(matching) > limit
    byte_limit_hit = bool(tail_meta.get("byte_limit_hit"))
    meta.update(
        {
            "returned_entries": len(returned),
            "matching_entries": len(matching),
            "min_severity": severity_filter,
            "truncated": line_limit_hit or byte_limit_hit or bool(tail_meta.get("partial_first_line")),
            "byte_limit_hit": byte_limit_hit,
            "line_limit_hit": line_limit_hit,
            "partial_first_line": bool(tail_meta.get("partial_first_line")),
            "raw_text": raw_text,
            "queue_flush_completed": queue_flush_completed,
            "pending_queue": pending_queue,
            "file_size_bytes": tail_meta.get("file_size_bytes", 0),
            "tail_bytes_read": tail_meta.get("tail_bytes_read", 0),
        }
    )
    return selected_raw_lines, returned, meta
