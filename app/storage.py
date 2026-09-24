import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
import hashlib
import hmac
import ipaddress
import uuid

from .logging_config import prune_rotated_log_files, sanitize_log_text, sanitize_log_value
from .alas_secrets import (
    AlasTokenError,
    TOKEN_KEY_ENV,
    TOKEN_PREVIOUS_KEY_ENV,
    decrypt_token,
    encrypt_token,
    is_encrypted_token,
    key_diagnostics,
    key_is_configured,
    key_is_valid,
    token_summary,
)
from . import (
    access_log,
    storage_access,
    storage_alas,
    storage_audit,
    storage_bans,
    storage_core,
    storage_notifications,
    storage_schema,
    storage_users,
    storage_watch_history,
)
from .storage_types import AlasConfigOwnershipError

try:
    from werkzeug.security import check_password_hash as werkzeug_check_password_hash
except Exception:  # pragma: no cover
    werkzeug_check_password_hash = None


def _bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)) or default)
    except (TypeError, ValueError, OverflowError):
        value = default
    return max(minimum, min(value, maximum))

# These explicit aliases preserve the long-standing ``app.storage`` import
# surface while the actual connection/path owner lives in ``storage_core``.
DATA_DIR = storage_core.DATA_DIR
DB_PATH = storage_core.DB_PATH
INITIAL_ADMIN_PASSWORD_FILE = storage_core.INITIAL_ADMIN_PASSWORD_FILE
LEGACY_USERS_FILE = storage_core.LEGACY_USERS_FILE
LEGACY_ENV_FILE = storage_core.LEGACY_ENV_FILE
now_ts = storage_core.now_ts
db_connect = storage_core.db_connect
readonly_db_connect = storage_core.readonly_db_connect
parse_env_file = storage_core.parse_env_file
_normalized_path = storage_core._normalized_path
_normalized_path_text = storage_core._normalized_path_text
_restrict_db_file_permissions = storage_core._restrict_db_file_permissions
_db_setup_lock = storage_core._db_setup_lock
_data_dirs_ready = storage_core._data_dirs_ready
_wal_configured = storage_core._wal_configured
# Keep malformed environment values from crashing import and cap the work
# factor before it reaches PBKDF2.  Stored hashes are validated separately so
# a corrupted database cannot turn a login attempt into unbounded CPU work.
MIN_PASSWORD_LENGTH = _bounded_env_int("MIN_PASSWORD_LENGTH", 12, 1, 256)
PASSWORD_PBKDF2_ITERATIONS = _bounded_env_int(
    "PASSWORD_PBKDF2_ITERATIONS",
    310000,
    100000,
    2000000,
)
PASSWORD_PBKDF2_MAX_ITERATIONS = 2000000
ACCOUNT_EXPIRING_WINDOW_SECONDS = 7 * 24 * 60 * 60
ACCOUNT_EXPIRING_WINDOW_DAYS_DEFAULT = ACCOUNT_EXPIRING_WINDOW_SECONDS // 86400
ACCOUNT_EXPIRING_WINDOW_DAYS_MAX = 365
MAX_ACCOUNT_EXPIRES_AT = 253402300799  # 9999-12-31T23:59:59Z
EXPIRATION_UNSET = object()
ENABLED_UNSET = object()
ALAS_VISIBLE_UNSET = object()
ALAS_DEVICE_UNCHANGED = object()
SESSION_IDLE_SECONDS = 12 * 60 * 60
SESSION_ABSOLUTE_SECONDS = 7 * 24 * 60 * 60
VIEWER_WATCH_HISTORY_DEFAULT_LIMIT = storage_watch_history.VIEWER_WATCH_HISTORY_DEFAULT_LIMIT
VIEWER_WATCH_HISTORY_MAX_LIMIT = storage_watch_history.VIEWER_WATCH_HISTORY_MAX_LIMIT
VIEWER_WATCH_REASON_MAX_LENGTH = storage_watch_history.VIEWER_WATCH_REASON_MAX_LENGTH


def _decode_hex_component(value: object, *, minimum_bytes: int = 1, maximum_bytes: int = 64) -> bytes | None:
    """Decode a bounded hash component without allocating attacker-sized data."""
    if not isinstance(value, str) or len(value) % 2 or len(value) > maximum_bytes * 2:
        return None
    try:
        decoded = bytes.fromhex(value)
    except (TypeError, ValueError):
        return None
    return decoded if minimum_bytes <= len(decoded) <= maximum_bytes else None


AUDIT_SCHEMA_VERSION = 2
# Application-schema generation, mirrored into ``PRAGMA user_version`` by
# :func:`init_db` and into the machine-readable contract in
# ``tests/fixtures/phase7_storage_schema_manifest.json``.  Bump it whenever the
# storage schema changes so operators can tell which generation a database is.
SCHEMA_GENERATION = 3
AUDIT_MAX_ROWS = _bounded_env_int("AUDIT_MAX_ROWS", 100000, 1000, 5000000)
# Retention for the durable alert projection.  Only alerts that are already
# handled or hidden are pruned — the visible unhandled ones are the operator's
# to-do queue and must survive.
AUDIT_ALERT_MAX_ROWS = _bounded_env_int("AUDIT_ALERT_MAX_ROWS", 20000, 100, 5000000)
# Viewer watch history retention in days (0 = keep forever).
VIEWER_WATCH_RETENTION_DAYS = _bounded_env_int("VIEWER_WATCH_RETENTION_DAYS", 180, 0, 3650)
# 「日志保存时长」由管理员在后台设置：固定档位（0 = 不清理，永久保留）。
# 档位而不是任意天数，是为了让「保存多久」在界面上是明确、可预期的一组选择。
LOG_RETENTION_DAY_OPTIONS = (0, 1, 3, 7, 15, 30)
LOG_RETENTION_DEFAULT_DAYS = 30
LOG_RETENTION_STATE_SETTING = "_log_retention_last_run_day"
# 访问记录（VIS）：保留档位与上限来自 app.access_log，避免两处各写一套。
ACCESS_RETENTION_DAY_OPTIONS = access_log.RETENTION_DAY_OPTIONS
ACCESS_RETENTION_DEFAULT_DAYS = access_log.DEFAULT_RETENTION_DAYS
ACCESS_MAX_DETAIL_ROWS = access_log.MAX_DETAIL_ROWS
ACCESS_RETENTION_STATE_SETTING = "_access_retention_last_run_day"
ACCESS_DROPPED_TOTAL_SETTING = "_access_log_dropped_total"
ACCESS_LAST_DROP_TS_SETTING = "_access_log_last_drop_ts"
# IP 封禁（BAN）：自定义时长上限 365 天，事件保留期与审计日志同量级。
BAN_MAX_SECONDS = storage_bans.MAX_BAN_SECONDS
BAN_EVENT_RETENTION_DAYS = storage_bans.DEFAULT_BAN_EVENT_RETENTION_DAYS
AUDIT_PRUNE_BATCH = 1000
AUDIT_OUTCOMES = {"success", "failure", "denied", "error", "unknown"}
AUDIT_SEVERITIES = {"debug", "info", "warning", "error", "critical"}
AUDIT_ACTOR_ROLES = {"admin", "user", "system", "anonymous", "unknown"}
AUDIT_NAME_RE = re.compile(r"[^a-z0-9_.:-]+")
AUDIT_LOGGER = logging.getLogger(__name__)
SQLITE_INT_MAX = (1 << 63) - 1

DEFAULT_SETTINGS = {
    "video_profile": "balanced",
    # Administrator-selected ordinary projection default.  Kept separate from
    # the legacy video_profile key so old clients and saved settings continue
    # to round-trip while the resolver can repair an invalid selection.
    "video_default_preset": "balanced",
    "video_adaptive": "false",
    "video_bit_rate": "2400000",
    "max_size": "1280",
    "max_fps": "24",
    "scrcpy_stream_mode": "raw",
    "scrcpy_enabled_stream_modes": "raw",
    "video_preset_smooth_video_bit_rate": "1000000",
    "video_preset_smooth_max_size": "854",
    "video_preset_smooth_max_fps": "24",
    "video_preset_balanced_video_bit_rate": "2400000",
    "video_preset_balanced_max_size": "1280",
    "video_preset_balanced_max_fps": "24",
    "video_preset_sharp_video_bit_rate": "6000000",
    "video_preset_sharp_max_size": "1920",
    "video_preset_sharp_max_fps": "30",
    "video_preset_low_latency_video_bit_rate": "1800000",
    "video_preset_low_latency_max_size": "960",
    "video_preset_low_latency_max_fps": "30",
    "video_custom_profiles": "{}",
    "video_fullscreen_profile": "sharp",
    # Empty means all available presets, including newly-created custom ones.
    # An explicit comma-separated value is the administrator allow-list.
    "video_enabled_presets": "",
    "video_bandwidth_preset": "",
    "video_bandwidth_profile": "",
    "video_max_size_limit": "1920",
    "video_user_custom_tuning": "false",
    "auto_stop_time": "15",
    "auto_stop_minutes": "15",
    "max_session_minutes": "0",
    "viewer_hidden_stop_enabled": "true",
    "viewer_hidden_stop_minutes": "5",
    "viewer_blur_stop_enabled": "false",
    "viewer_blur_stop_minutes": "5",
    "viewer_stop_settings_synced": "false",
    "alas_enabled": "false",
    "alas_base_url": "http://127.0.0.1:22267",
    "alas_current_config": "alas",
    "alas_token": "",
    # ALAS remains available to administrators and its Runtime is unaffected;
    # this only controls whether the ordinary workbench exposes ALAS controls.
    "workbench_alas_visible": "true",
    # 管理员可关闭的 ALAS 页面选项（JSON 列表），关闭后注入脚本隐藏对应 UI。
    "alas_feature_switches": "",
    "ui_system_name": "ScrcpyGate",
    "ui_language": "",
    "ui_theme_mode": "system",
    "expiry_reminder_days": "3",
    "stop_alas_on_expiry": "false",
    # 日志保存时长（天，0 = 永久保留）：同时约束审计日志行与运行日志的轮转段。
    # 运行日志仍保留按体积轮转（LOG_MAX_BYTES / LOG_BACKUP_COUNT）作为上限。
    "log_retention_days": "30",
    # 访问记录（VIS）：默认开启；明细保留 7 天（0 = 不按时间清理，仍受行数上限约束）。
    "access_log_enabled": "true",
    "access_retention_days": "7",
    # 地域限制（GEO）：默认关闭；国家清单默认 CN（HK/MO/TW 是彼此独立的值）。
    "geo_mode": "off",
    "geo_allowed_countries": "CN",
    "geo_unknown_action": "deny",
    "geo_allow_cidrs": "",
    # 采样丢弃的累计计数（写入失败也要能被管理员看到「记录不完整」）。
    "_access_log_dropped_total": "0",
    "_access_log_last_drop_ts": "0",
}

# The legacy ``data/.env`` file is an import source, not a second settings
# store.  Remember that its values have been consumed so a later application
# restart cannot overwrite settings changed through the current UI or restore
# a token that an administrator deliberately cleared.
LEGACY_ENV_MIGRATED_SETTING = "_legacy_env_migrated_v1"
LEGACY_ALAS_TOKEN_PENDING_SETTING = "_legacy_alas_token_pending_v1"
LEGACY_ALAS_TOKEN_KEY = "ALAS_GYRE_TOKEN"

VIDEO_QUALITY_MIGRATION_VERSION = "3"
NORMAL_VIDEO_PROFILES = ("smooth", "balanced", "sharp", "low_latency")
MIN_VIDEO_PROFILE_MAX_SIZE = 854
MAX_VIDEO_PROFILE_MAX_SIZE = 1920
LEGACY_VIDEO_PROFILE_MATRICES = (
    (
        {"smooth": (700000, 480, 24), "balanced": (900000, 540, 24), "sharp": (1600000, 720, 30), "low_latency": (900000, 480, 30)},
        {"smooth": (1000000, 854, 24), "balanced": (2400000, 1280, 24), "sharp": (6000000, 1920, 30), "low_latency": (1800000, 960, 30)},
    ),
    (
        {"smooth": (1000000, 960, 24), "balanced": (2400000, 1280, 24), "sharp": (6000000, 1920, 30), "low_latency": (1800000, 960, 30)},
        {"smooth": (1000000, 854, 24), "balanced": (2400000, 1280, 24), "sharp": (6000000, 1920, 30), "low_latency": (1800000, 960, 30)},
    ),
    (
        {"smooth": (450000, 720, 20), "balanced": (650000, 720, 24), "sharp": (1100000, 720, 24), "low_latency": (750000, 720, 30)},
        {"smooth": (800000, 854, 20), "balanced": (1000000, 960, 24), "sharp": (1300000, 1280, 24), "low_latency": (1000000, 854, 30)},
    ),
    (
        {"smooth": (700000, 720, 24), "balanced": (1200000, 720, 24), "sharp": (2200000, 720, 30), "low_latency": (1200000, 720, 30)},
        {"smooth": (1200000, 960, 24), "balanced": (2400000, 1280, 24), "sharp": (3200000, 1280, 30), "low_latency": (1800000, 960, 30)},
    ),
    (
        {"smooth": (900000, 720, 24), "balanced": (1800000, 720, 24), "sharp": (3500000, 960, 30), "low_latency": (1800000, 720, 30)},
        {"smooth": (2400000, 1280, 24), "balanced": (3500000, 1280, 30), "sharp": (6500000, 1920, 30), "low_latency": (3000000, 1280, 30)},
    ),
    (
        {"smooth": (1200000, 720, 24), "balanced": (2800000, 720, 30), "sharp": (5500000, 1280, 30), "low_latency": (2800000, 720, 30)},
        {"smooth": (3000000, 1280, 24), "balanced": (5000000, 1600, 30), "sharp": (8500000, 1920, 30), "low_latency": (4000000, 1280, 30)},
    ),
)

COMMON_WEAK_PASSWORDS = {"admin", "admin123", "password", "password123", "123456", "12345678", "qwerty123"}


def hash_password(password: str) -> str:
    salt = os.urandom(32)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_PBKDF2_ITERATIONS}${salt.hex()}${key.hex()}"


def password_hash_needs_upgrade(stored: str) -> bool:
    stored = stored or ""
    if not stored.startswith("pbkdf2_sha256$"):
        return True
    try:
        _, iterations_text, salt_hex, key_hex = stored.split("$", 3)
        iterations = int(iterations_text)
        salt = _decode_hex_component(salt_hex, minimum_bytes=16)
        digest_size = hashlib.sha256().digest_size
        key = _decode_hex_component(
            key_hex,
            minimum_bytes=digest_size,
            maximum_bytes=digest_size,
        )
        if not 1 <= iterations <= PASSWORD_PBKDF2_MAX_ITERATIONS:
            return True
        if salt is None or key is None:
            return True
        return iterations < PASSWORD_PBKDF2_ITERATIONS
    except (AttributeError, TypeError, ValueError, OverflowError):
        return True


def verify_password(password: str, stored: str) -> bool:
    stored = stored or ""
    if stored.startswith("pbkdf2_sha256$"):
        try:
            _, iterations_text, salt_hex, key_hex = stored.split("$", 3)
            iterations = int(iterations_text)
            if not 1 <= iterations <= PASSWORD_PBKDF2_MAX_ITERATIONS:
                return False
            salt = _decode_hex_component(salt_hex, minimum_bytes=16)
            stored_key = _decode_hex_component(
                key_hex,
                minimum_bytes=hashlib.sha256().digest_size,
                maximum_bytes=hashlib.sha256().digest_size,
            )
            if salt is None or stored_key is None:
                return False
            new_key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
            return secrets.compare_digest(stored_key, new_key)
        except (AttributeError, TypeError, ValueError, OverflowError):
            return False
    if ":" in stored and not stored.startswith(("pbkdf2:", "scrypt:")):
        try:
            salt_hex, key_hex = stored.split(":", 1)
            salt = _decode_hex_component(salt_hex, minimum_bytes=16)
            stored_key = _decode_hex_component(
                key_hex,
                minimum_bytes=hashlib.sha256().digest_size,
                maximum_bytes=hashlib.sha256().digest_size,
            )
            if salt is None or stored_key is None:
                return False
            new_key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100000)
            return secrets.compare_digest(stored_key, new_key)
        except Exception:
            return False
    if werkzeug_check_password_hash:
        try:
            return bool(werkzeug_check_password_hash(stored, password))
        except Exception:
            return False
    return False


def validate_password(password: str, username: str = "") -> str | None:
    if len(password or "") < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters"
    lowered = password.lower()
    if lowered in COMMON_WEAK_PASSWORDS:
        return "Password is too common"
    if username and lowered == username.lower():
        return "Password must not match username"
    return None


_AUDIT_COLUMN_DEFINITIONS = {
    "event_id": "TEXT",
    "request_id": "TEXT NOT NULL DEFAULT ''",
    "actor_role": "TEXT NOT NULL DEFAULT 'unknown'",
    "target_type": "TEXT NOT NULL DEFAULT ''",
    "target_id": "TEXT NOT NULL DEFAULT ''",
    "outcome": "TEXT NOT NULL DEFAULT 'unknown'",
    "reason": "TEXT NOT NULL DEFAULT ''",
    "severity": "TEXT NOT NULL DEFAULT 'info'",
    "source_ip": "TEXT NOT NULL DEFAULT ''",
    "user_agent": "TEXT NOT NULL DEFAULT ''",
    "metadata_json": "TEXT NOT NULL DEFAULT '{}'",
    "schema_version": "INTEGER NOT NULL DEFAULT 1",
    "prev_hash": "TEXT NOT NULL DEFAULT ''",
    "event_hash": "TEXT NOT NULL DEFAULT ''",
    "dedupe_key": "TEXT",
}
_AUDIT_STATE_COLUMN_DEFINITIONS = {
    "anchor_event_id": "INTEGER NOT NULL DEFAULT 0",
    "anchor_event_hash": "TEXT NOT NULL DEFAULT ''",
    "pruned_count": "INTEGER NOT NULL DEFAULT 0",
}


def _audit_clean_name(value: object, default: str, max_chars: int = 96) -> str:
    cleaned = sanitize_log_text(value, max_chars).lower()
    cleaned = AUDIT_NAME_RE.sub("_", cleaned).strip("_.:-")
    return cleaned[:max_chars] or default


def _audit_sqlite_int(value: object) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(0, min(parsed, SQLITE_INT_MAX))


def _audit_clean_ip(value: object) -> str:
    candidate = sanitize_log_text(value, 64)
    if not candidate:
        return ""
    try:
        return ipaddress.ip_address(candidate).compressed
    except ValueError:
        return ""


def _audit_metadata_json(value: object) -> str:
    if not isinstance(value, dict):
        value = {}
    safe = sanitize_log_value(value)
    if not isinstance(safe, dict):
        safe = {}
    return json.dumps(safe, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _is_alert_event(outcome: object, severity: object) -> bool:
    """Keep actionable failures in the durable alert queue."""
    normalized_outcome = str(outcome or "").strip().lower()
    normalized_severity = str(severity or "").strip().lower()
    return normalized_outcome in {"failure", "denied", "error"} or normalized_severity in {
        "warning",
        "error",
        "critical",
    }


def _migrate_audit_alerts(conn: sqlite3.Connection) -> None:
    """Create a durable alert projection without mutating the audit chain."""
    # 定义来自 storage_schema 的唯一一份（含 dashboard_visible 的 CHECK）。
    storage_schema.create_audit_alerts_table(conn)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(audit_alerts)").fetchall()}
    definitions = {
        "actor_role": "TEXT NOT NULL DEFAULT 'unknown'",
        "target_type": "TEXT NOT NULL DEFAULT ''",
        "target_id": "TEXT NOT NULL DEFAULT ''",
        "outcome": "TEXT NOT NULL DEFAULT 'unknown'",
        "reason": "TEXT NOT NULL DEFAULT ''",
        "severity": "TEXT NOT NULL DEFAULT 'warning'",
        "request_id": "TEXT NOT NULL DEFAULT ''",
        "source_ip": "TEXT NOT NULL DEFAULT ''",
        "detail": "TEXT NOT NULL DEFAULT ''",
        "metadata_json": "TEXT NOT NULL DEFAULT '{}'",
        "alert_type": "TEXT NOT NULL DEFAULT 'service_failure'",
        "title": "TEXT NOT NULL DEFAULT '系统异常'",
        "summary": "TEXT NOT NULL DEFAULT ''",
        "log_url": "TEXT NOT NULL DEFAULT '/logs'",
        "dashboard_visible": "INTEGER NOT NULL DEFAULT 1",
        "handled_at": "INTEGER",
        "handled_by": "TEXT NOT NULL DEFAULT ''",
    }
    for name, definition in definitions.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE audit_alerts ADD COLUMN {name} {definition}")
    existing = conn.execute("SELECT event_id,action,reason,metadata_json,detail,outcome,target_type FROM audit_alerts").fetchall()
    for row in existing:
        event = dict(row)
        event["metadata"] = event.pop("metadata_json", "{}")
        projection = _alert_projection(event)
        if projection is None:
            conn.execute("UPDATE audit_alerts SET dashboard_visible=0 WHERE event_id=?", (row["event_id"],))
        else:
            conn.execute(
                "UPDATE audit_alerts SET alert_type=?,title=?,summary=?,log_url=?,dashboard_visible=? WHERE event_id=?",
                (projection["alert_type"], projection["title"], projection["summary"], projection["log_url"], projection["dashboard_visible"], row["event_id"]),
            )
    # Backfill from the audit chain and classify **only the newly inserted
    # rows** (``RETURNING``).  The previous implementation walked the whole
    # projection table a second time, which doubled startup work: measured 1106
    # UPDATE statements for 530 alerts (ISSUE-131 P2-3).
    imported = conn.execute(
        """
        INSERT OR IGNORE INTO audit_alerts(
            event_id, audit_id, ts, username, actor_role, action, target_type,
            target_id, outcome, reason, severity, request_id, source_ip,
            detail, metadata_json, alert_type, title, summary, log_url, dashboard_visible
        )
        SELECT event_id, id, ts, username, actor_role, action, target_type,
               target_id, outcome, reason, severity, request_id, source_ip,
               detail, metadata_json, 'service_failure', '系统异常', detail, '/logs', 1
        FROM audit_log
        WHERE (outcome IN ('failure','denied','error')
           OR severity IN ('warning','error','critical'))
        RETURNING event_id, action, reason, metadata_json, detail, outcome, target_type
        """
    ).fetchall()
    for row in imported:
        event = dict(row)
        event["metadata"] = event.pop("metadata_json", "{}")
        projection = _alert_projection(event)
        if projection is None:
            conn.execute("UPDATE audit_alerts SET dashboard_visible=0 WHERE event_id=?", (row["event_id"],))
        else:
            conn.execute(
                "UPDATE audit_alerts SET alert_type=?,title=?,summary=?,log_url=?,dashboard_visible=? WHERE event_id=?",
                (projection["alert_type"], projection["title"], projection["summary"], projection["log_url"], projection["dashboard_visible"], row["event_id"]),
            )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_alerts_pending ON audit_alerts(handled_at, ts DESC, audit_id DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_alerts_ts ON audit_alerts(ts DESC, audit_id DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_alerts_audit_id ON audit_alerts(audit_id)")
    _prune_audit_alerts(conn)


def _migrate_audit_log(conn: sqlite3.Connection) -> None:
    """Add the versioned audit schema and establish its chain exactly once."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(audit_log)").fetchall()}
    missing = [name for name in _AUDIT_COLUMN_DEFINITIONS if name not in columns]
    for name in missing:
        conn.execute(f"ALTER TABLE audit_log ADD COLUMN {name} {_AUDIT_COLUMN_DEFINITIONS[name]}")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_integrity_state (
            singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
            head_event_id INTEGER NOT NULL DEFAULT 0,
            head_event_hash TEXT NOT NULL DEFAULT '',
            event_count INTEGER NOT NULL DEFAULT 0,
            updated_at INTEGER NOT NULL
        )
        """
    )
    state_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(audit_integrity_state)").fetchall()
    }
    for name, definition in _AUDIT_STATE_COLUMN_DEFINITIONS.items():
        if name not in state_columns:
            conn.execute(
                f"ALTER TABLE audit_integrity_state ADD COLUMN {name} {definition}"
            )

    rows_missing_ids = conn.execute(
        "SELECT id FROM audit_log WHERE event_id IS NULL OR event_id='' ORDER BY id"
    ).fetchall()
    for row in rows_missing_ids:
        conn.execute(
            "UPDATE audit_log SET event_id=? WHERE id=?",
            (f"legacy-{row['id']}-{uuid.uuid4().hex}", row["id"]),
        )

    state = conn.execute(
        "SELECT * FROM audit_integrity_state WHERE singleton=1"
    ).fetchone()
    # A mismatch between the recorded chain length and the rows on disk used to
    # disable pruning forever (fail-closed ``AUDIT_RETENTION_SKIPPED`` on every
    # write) with no way back except deleting the state row by hand.  Repair it
    # here instead: re-deriving the chain from the stored anchor is exactly what
    # the rebuild below does.
    if state is not None:
        recorded = int(state["event_count"] or 0)
        actual = int(conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0])
        if recorded != actual:
            AUDIT_LOGGER.critical(
                "AUDIT_INTEGRITY_STATE_MISMATCH state_count=%s row_count=%s rebuilding_chain",
                recorded,
                actual,
                extra={"event_name": "audit.integrity_state_mismatch"},
            )
            needs_rebuild = True
        else:
            needs_rebuild = bool(missing) or bool(rows_missing_ids)
    else:
        needs_rebuild = True
    if needs_rebuild:
        if state is None:
            first_row = conn.execute(
                "SELECT id, prev_hash FROM audit_log ORDER BY id LIMIT 1"
            ).fetchone()
            # state 丢失时锚点取自表内首行 prev_hash——该列可被本地写库者篡改，
            # 无法与"链从未存在"区分，必须留下显眼告警供事后追查。
            if first_row is not None:
                AUDIT_LOGGER.critical(
                    "AUDIT_INTEGRITY_STATE_MISSING rows=%s first_id=%s anchor_rebuilt_from_table",
                    conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0],
                    first_row["id"],
                )
            anchor_event_hash = str(first_row["prev_hash"] or "") if first_row else ""
            anchor_event_id = max(0, int(first_row["id"]) - 1) if anchor_event_hash and first_row else 0
            pruned_count = anchor_event_id
        else:
            anchor_event_hash = str(state["anchor_event_hash"] or "")
            anchor_event_id = int(state["anchor_event_id"] or 0)
            pruned_count = int(state["pruned_count"] or 0)
        previous_hash = anchor_event_hash
        head_id = 0
        count = 0
        rows = conn.execute("SELECT * FROM audit_log ORDER BY id").fetchall()
        for row in rows:
            event_hash = _audit_event_hash(previous_hash, row)
            conn.execute(
                "UPDATE audit_log SET prev_hash=?, event_hash=? WHERE id=?",
                (previous_hash, event_hash, row["id"]),
            )
            previous_hash = event_hash
            head_id = int(row["id"])
            count += 1
        conn.execute(
            """
            INSERT INTO audit_integrity_state(
                singleton, anchor_event_id, anchor_event_hash, head_event_id,
                head_event_hash, event_count, pruned_count, updated_at
            ) VALUES(1,?,?,?,?,?,?,?)
            ON CONFLICT(singleton) DO UPDATE SET
                anchor_event_id=excluded.anchor_event_id,
                anchor_event_hash=excluded.anchor_event_hash,
                head_event_id=excluded.head_event_id,
                head_event_hash=excluded.head_event_hash,
                event_count=excluded.event_count,
                pruned_count=excluded.pruned_count,
                updated_at=excluded.updated_at
            """,
            (anchor_event_id, anchor_event_hash, head_id, previous_hash, count, pruned_count, now_ts()),
        )
    else:
        conn.execute(
            """
            INSERT OR IGNORE INTO audit_integrity_state(
                singleton, head_event_id, head_event_hash, event_count, updated_at
            ) VALUES(1,0,'',0,?)
            """,
            (now_ts(),),
        )

    # Projection first: the retention prune below also maintains
    # ``audit_alerts`` (it blanks links to trimmed events and bounds the table),
    # so that table must exist by then.
    _migrate_audit_alerts(conn)

    state = conn.execute(
        "SELECT * FROM audit_integrity_state WHERE singleton=1"
    ).fetchone()
    if state is not None and int(state["event_count"] or 0) > AUDIT_MAX_ROWS:
        _prune_audit_prefix(conn, state, reserve_rows=0)

    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_audit_event_id ON audit_log(event_id)")
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_audit_dedupe_key
        ON audit_log(dedupe_key) WHERE dedupe_key IS NOT NULL AND dedupe_key != ''
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_ts_id ON audit_log(ts DESC, id DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_actor_id ON audit_log(username, id DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_action_id ON audit_log(action, id DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_outcome_id ON audit_log(outcome, id DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_severity_id ON audit_log(severity, id DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_request_id ON audit_log(request_id, id DESC)")


def init_db() -> bool:
    storage_core.DATA_DIR.mkdir(parents=True, exist_ok=True)
    _remove_initial_password_file()
    # The web process and every `app.cli` command call init_db(); the lock keeps
    # two migration sequences from interleaving (SQLite only serialises single
    # statements).  Timeout/stale handling lives in storage_core.StartupLock.
    with storage_core.StartupLock():
        return _init_db_locked()


def _init_db_locked() -> bool:
    with db_connect() as conn:
        storage_schema.create_base_schema(conn)
        _migrate_user_alas_configs(conn)
        _migrate_audit_log(conn)
        storage_schema.ensure_compatibility_schema(conn, logger=AUDIT_LOGGER)
        for key, value in DEFAULT_SETTINGS.items():
            if key.startswith("geo_"):
                continue  # Absence means env/default; explicit UI settings remain authoritative.
            conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)", (key, value))
        _migrate_video_defaults(conn)
        # 一次性：把历史上明文存储的会话换成哈希存储形态（见 session_token_hash）。
        migrate_session_token_storage(conn)
        # Schema generation marker: lets operators (and the schema contract
        # test) tell which generation a database file is at without diffing it.
        conn.execute(f"PRAGMA user_version={int(SCHEMA_GENERATION)}")
        conn.commit()
    # A process restart is the only reliable boundary available for a viewer
    # whose WebSocket disappeared without reaching its normal ``finally``.
    # Close those rows before accepting new connections so the admin history
    # never reports a permanently active session.
    finalize_open_viewer_watch_sessions()
    admin_created = migrate_legacy_data()
    migrate_alas_token_storage()
    with db_connect() as conn:
        _migrate_video_quality_presets(conn)
        conn.commit()
    _compact_database()
    # Retention sweep at startup: drop sessions that every deadline has passed
    # and watch history older than the retention window.
    run_storage_maintenance()
    return admin_created


def _compact_database() -> None:
    """Reclaim the WAL and refresh planner statistics after migrations.

    ``journal_size_limit`` only takes effect on a checkpoint, and no other code
    path ever checkpoints, so the WAL stayed at its high-water mark (ISSUE-131).
    Both statements are best effort: another process holding a read transaction
    may make the checkpoint return ``busy`` (and a concurrent CLI run is
    explicitly supported), and ``PRAGMA optimize`` is cheap when there is
    nothing to do.
    """
    try:
        with db_connect() as conn:
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass
            try:
                conn.execute("PRAGMA optimize")
            except sqlite3.Error:
                pass
    except (sqlite3.Error, OSError):  # pragma: no cover - maintenance is optional
        pass


def _legacy_env_has_alas_token() -> bool:
    """Return whether the legacy import file still contains a token assignment."""
    try:
        if storage_core.LEGACY_ENV_FILE.is_symlink():
            return True
        env = storage_core.parse_env_file(storage_core.LEGACY_ENV_FILE)
    except (OSError, UnicodeError):
        return True
    return bool(str(env.get(LEGACY_ALAS_TOKEN_KEY, "")).strip())


def _clear_legacy_alas_token() -> bool:
    """Atomically remove the legacy token assignment after DB migration."""
    path = storage_core.LEGACY_ENV_FILE
    try:
        if not path.exists():
            return False
        if path.is_symlink():
            raise RuntimeError("legacy ALAS environment file must not be a symlink")
        source = path.read_text(encoding="utf-8", errors="surrogateescape")
    except FileNotFoundError:
        return False
    except (OSError, UnicodeError) as exc:
        raise RuntimeError("legacy ALAS environment file cannot be read") from exc

    kept: list[str] = []
    removed = False
    for line in source.splitlines(keepends=True):
        probe = line.rstrip("\r\n")
        if "=" in probe:
            name = probe.split("=", 1)[0].strip()
            if name.startswith("export") and len(name) > len("export") and name[len("export")].isspace():
                name = name[len("export") :].strip()
            if name == LEGACY_ALAS_TOKEN_KEY:
                removed = True
                continue
        kept.append(line)
    if not removed:
        return False

    temporary = path.with_name(f".{path.name}.scrcpygate-token-{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", errors="surrogateescape", newline="") as handle:
            handle.write("".join(kept))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        try:
            directory_fd = os.open(str(path.parent), os.O_RDONLY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except (OSError, UnicodeError) as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise RuntimeError("legacy ALAS token could not be removed atomically") from exc
    return True


def migrate_alas_token_storage() -> dict[str, object]:
    """Migrate legacy ALAS tokens at the controlled database-init boundary.

    Credential failures do not abort startup: an unreadable credential must not take the
    whole service down (that also blocks ``reset-admin``, so an operator could
    not even get in to fix it).  The stored value is left untouched and a loud,
    actionable warning is logged instead; the ALAS runtime paths already report
    a token they cannot decrypt, so the UI stays truthful while the admin either
    restores the matching key or clears the token. Database failures still
    propagate so that an unavailable database cannot be reported as healthy.
    """
    with db_connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key='alas_token'").fetchone()
        raw = str(row["value"] or "") if row else ""
        pending = conn.execute(
            "SELECT value FROM settings WHERE key=?", (LEGACY_ALAS_TOKEN_PENDING_SETTING,)
        ).fetchone()
        if not raw and pending and pending["value"] == "true":
            # Other legacy settings/permissions stay migrated. Retry only the
            # credential whose encryption failed, without replaying grants.
            try:
                if storage_core.LEGACY_ENV_FILE.is_symlink():
                    raise OSError("legacy file must not be a symlink")
                legacy = storage_core.parse_env_file(storage_core.LEGACY_ENV_FILE)
                token = str(legacy.get(LEGACY_ALAS_TOKEN_KEY, "") or "")
                if not token:
                    return {"ok": False, "action": "legacy_token_pending"}
                raw = encrypt_token(token)
            except (OSError, AlasTokenError):
                AUDIT_LOGGER.warning("ALAS_TOKEN_LEGACY_PENDING restore a valid key and retry migration")
                return {"ok": False, "action": "legacy_token_pending"}
            conn.execute("UPDATE settings SET value=? WHERE key='alas_token'", (raw,))
            conn.execute("DELETE FROM settings WHERE key=?", (LEGACY_ALAS_TOKEN_PENDING_SETTING,))
            conn.commit()
        if not raw:
            marker = conn.execute(
                "SELECT value FROM settings WHERE key=?",
                (LEGACY_ENV_MIGRATED_SETTING,),
            ).fetchone()
            if marker and str(marker["value"] or "").lower() in ("1", "true", "yes", "on"):
                # A legacy file may have survived a rollback even after the
                # administrator cleared the DB value.  Remove that stale
                # credential without importing it again.
                conn.commit()
                _clear_legacy_alas_token_safely()
            return {"ok": True, "action": "empty"}
        if is_encrypted_token(raw):
            try:
                _token, source = decrypt_token(raw, allow_legacy=True)
            except AlasTokenError as exc:
                # 保留原文：换回正确密钥后仍能解密。这里只降级并说清"该怎么修"。
                AUDIT_LOGGER.warning(
                    "ALAS_TOKEN_UNDECRYPTABLE error=%s token=%s key=%s hint=%s",
                    exc,
                    json.dumps(token_summary(raw), sort_keys=True),
                    json.dumps(key_diagnostics(), sort_keys=True),
                    "restore the matching key (ALAS_TOKEN_ENCRYPTION_KEY / "
                    "ALAS_TOKEN_ENCRYPTION_KEY_PREVIOUS / data/.alas-token-encryption-key, "
                    "e.g. from a deployment backup) and restart, or clear the token with "
                    "`python -m app.cli clear-alas-token` and re-enter it in the ALAS settings",
                )
                return {"ok": False, "action": "undecryptable", "error": str(exc)}
            if source != TOKEN_KEY_ENV:
                try:
                    encrypted = encrypt_token(_token)
                except AlasTokenError as exc:
                    AUDIT_LOGGER.warning(
                        "ALAS_TOKEN_ROTATION_SKIPPED error=%s key=%s hint=%s",
                        exc,
                        json.dumps(key_diagnostics(), sort_keys=True),
                        "set ALAS_TOKEN_ENCRYPTION_KEY to rotate the token; it stays readable "
                        "through ALAS_TOKEN_ENCRYPTION_KEY_PREVIOUS in the meantime",
                    )
                    return {"ok": False, "action": "rotation_skipped", "error": str(exc)}
                conn.execute("UPDATE settings SET value=? WHERE key='alas_token'", (encrypted,))
                conn.commit()
            _clear_legacy_alas_token_safely()
            return {"ok": True, "action": "encrypted", "source": source}
        if not key_is_configured():
            # 明文凭据留在库里确实是安全问题，但把服务整体打挂并不能解决它：
            # 明确告警 + 让管理员进来设置密钥或清空该值。
            AUDIT_LOGGER.warning(
                "ALAS_TOKEN_PLAINTEXT_WITHOUT_KEY token=%s key=%s hint=%s",
                json.dumps(token_summary(raw), sort_keys=True),
                json.dumps(key_diagnostics(), sort_keys=True),
                "set ALAS_TOKEN_ENCRYPTION_KEY (or run `python -m app.cli generate-alas-key`) "
                "and restart to encrypt it, or clear it with `python -m app.cli clear-alas-token`",
            )
            return {"ok": False, "action": "plaintext_without_key"}
        try:
            encrypted = encrypt_token(raw)
        except AlasTokenError as exc:
            AUDIT_LOGGER.warning(
                "ALAS_TOKEN_ENCRYPT_FAILED error=%s key=%s",
                exc,
                json.dumps(key_diagnostics(), sort_keys=True),
            )
            return {"ok": False, "action": "encrypt_failed", "error": str(exc)}
        conn.execute("UPDATE settings SET value=? WHERE key='alas_token'", (encrypted,))
        conn.commit()
    _clear_legacy_alas_token_safely()
    return {"ok": True, "action": "encrypted", "source": "migrated"}


def _clear_legacy_alas_token_safely() -> bool:
    """Best-effort legacy-token cleanup: never let it stop startup.

    The legacy env file is an old-deployment artifact.  If it cannot be read or
    rewritten (symlink, permissions, read-only mount) the DB migration itself
    has already succeeded, so log loudly and keep booting — the operator still
    has to remove that plaintext credential by hand.
    """
    try:
        return bool(_clear_legacy_alas_token())
    except RuntimeError as exc:
        AUDIT_LOGGER.warning(
            "ALAS_TOKEN_LEGACY_FILE_KEPT error=%s hint=%s",
            exc,
            "remove the ALAS_GYRE_TOKEN assignment from the legacy env file by hand",
        )
        return False


def clear_alas_token() -> dict[str, object]:
    """Clear the stored ALAS token (recovery path when its encryption key is lost).

    Returns a redacted summary so the CLI can report what happened without ever
    printing the credential.
    """
    with db_connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key='alas_token'").fetchone()
        previous = str(row["value"] or "") if row else ""
        conn.execute("UPDATE settings SET value='' WHERE key='alas_token'")
        conn.execute("DELETE FROM settings WHERE key=?", (LEGACY_ALAS_TOKEN_PENDING_SETTING,))
        conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?, 'true')", (LEGACY_ENV_MIGRATED_SETTING,))
        conn.commit()
    legacy_removed = _clear_legacy_alas_token_safely()
    summary = {
        "ok": True,
        "action": "cleared",
        "cleared": bool(previous),
        "previous": token_summary(previous),
        "legacy_file_removed": legacy_removed,
    }
    AUDIT_LOGGER.warning("ALAS_TOKEN_CLEARED cleared=%s legacy_removed=%s", bool(previous), legacy_removed)
    return summary


def get_alas_token_migration_status() -> dict[str, object]:
    """Return a redacted, machine-readable ALAS token migration status."""
    row = None
    marker = None
    database_state = ""
    database_path = storage_core.DB_PATH
    if database_path.is_symlink():
        database_state = "database_invalid"
    elif not database_path.is_file():
        database_state = "database_missing"
    else:
        try:
            with readonly_db_connect() as conn:
                row = conn.execute("SELECT value FROM settings WHERE key='alas_token'").fetchone()
                marker = conn.execute(
                    "SELECT value FROM settings WHERE key=?",
                    (LEGACY_ENV_MIGRATED_SETTING,),
                ).fetchone()
        except (OSError, sqlite3.Error):
            database_state = "database_unreadable"
    raw = str(row["value"] or "") if row else ""
    current_key_configured = key_is_configured()
    current_key_valid = key_is_valid(TOKEN_KEY_ENV)
    previous_key_configured = bool(os.environ.get(TOKEN_PREVIOUS_KEY_ENV, "").strip())
    previous_key_valid = not previous_key_configured or key_is_valid(TOKEN_PREVIOUS_KEY_ENV)
    status: dict[str, object] = {
        "ok": not database_state,
        "state": database_state or ("empty" if not raw else "unknown"),
        "format": "empty" if not raw else "unknown",
        "decryptable": not bool(raw),
        "key_source": None,
        "needs_rotation": False,
        "current_key_configured": current_key_configured,
        "current_key_valid": current_key_valid,
        "previous_key_configured": previous_key_configured,
        "previous_key_valid": previous_key_valid,
        "legacy_env_token_present": _legacy_env_has_alas_token(),
        "legacy_env_migrated": bool(marker and str(marker["value"] or "").lower() in ("1", "true", "yes", "on")),
    }
    if database_state:
        return status
    if not raw:
        if not current_key_valid:
            status.update(ok=False, state="key_missing_or_invalid", needs_rotation=True)
        elif not previous_key_valid:
            status.update(ok=False, state="invalid_previous_key", needs_rotation=True)
        if status["legacy_env_token_present"]:
            status.update(ok=False, state="legacy_env_pending", needs_rotation=True)
        return status
    if not is_encrypted_token(raw):
        return {
            **status,
            "ok": False,
            "state": "plaintext_legacy",
            "format": "legacy",
            "decryptable": False,
            "needs_rotation": True,
        }
    status["format"] = "v1"
    try:
        _token, source = decrypt_token(raw)
    except AlasTokenError:
        return {
            **status,
            "ok": False,
            "state": "unreadable",
            "decryptable": False,
            "needs_rotation": True,
        }
    status.update(
        state="current" if source == "ALAS_TOKEN_ENCRYPTION_KEY" else "previous",
        decryptable=True,
        key_source="current" if source == "ALAS_TOKEN_ENCRYPTION_KEY" else "previous",
        needs_rotation=source != "ALAS_TOKEN_ENCRYPTION_KEY",
    )
    if status["legacy_env_token_present"]:
        status.update(ok=False, state="legacy_env_pending", needs_rotation=True)
    return status


def _create_user_alas_configs_table(conn: sqlite3.Connection, name: str = "user_alas_configs") -> None:
    """Create the ALAS binding table from the shared canonical definition.

    The column list lives in :mod:`app.storage_schema` so a fresh install and a
    legacy table rebuild cannot drift apart (ISSUE-132).
    """
    storage_schema.create_user_alas_configs_table(conn, name)


def _ensure_user_alas_default(conn: sqlite3.Connection, username: str) -> None:
    rows = conn.execute(
        "SELECT config_name, is_default FROM user_alas_configs WHERE username=? ORDER BY is_default DESC, config_name",
        (username,),
    ).fetchall()
    if not rows:
        return
    defaults = [row["config_name"] for row in rows if row["is_default"]]
    if len(defaults) == 1:
        return
    selected = defaults[0] if defaults else rows[0]["config_name"]
    conn.execute("UPDATE user_alas_configs SET is_default=0 WHERE username=?", (username,))
    conn.execute(
        "UPDATE user_alas_configs SET is_default=1 WHERE username=? AND config_name=?",
        (username, selected),
    )


def _prune_orphan_alas_bindings(conn: sqlite3.Connection) -> None:
    """Drop ALAS bindings whose owner or device no longer exists.

    新库由外键（CASCADE / SET NULL）保证，但历史库里 user_alas_configs 可能是
    没有外键的旧表：这样的孤儿行既不出现在管理页（列表都 JOIN users），又会被
    `config_owner` 判成占位属主 —— 该配置名从此谁都绑不上，管理员也无从处理。
    """
    conn.execute("DELETE FROM user_alas_configs WHERE username NOT IN (SELECT username FROM users)")
    conn.execute(
        "UPDATE user_alas_configs SET device_id=NULL "
        "WHERE device_id IS NOT NULL AND device_id NOT IN (SELECT id FROM devices)"
    )
    storage_notifications.prune_orphan_notifications(conn)


def _backfill_alas_config_keys(conn: sqlite3.Connection) -> int:
    """Fill (or repair) ``config_key`` for rows written before the column existed.

    键是 Python 侧的大小写折叠，SQLite 的 ``lower()`` 只处理 ASCII，所以这里逐行算，
    保证迁移结果与运行时写入的键完全一致。绑定行很少，代价可以忽略。
    """
    changed = 0
    rows = conn.execute("SELECT rowid,config_name,config_key FROM user_alas_configs").fetchall()
    for row in rows:
        key = storage_alas.config_name_key(row["config_name"])
        if row["config_key"] != key:
            conn.execute("UPDATE user_alas_configs SET config_key=? WHERE rowid=?", (key, row["rowid"]))
            changed += 1
    return changed


def _deduplicate_alas_config_owners(conn: sqlite3.Connection) -> None:
    """Keep one deterministic owner for each config **key** (config name is case-insensitive).

    保留规则：``updated_at`` 最新者胜；并列时用户名小者胜；同一用户出现两条同键行时
    （历史库里手工写入的大小写变体）按 rowid 小者胜 —— 全部确定性，重启不会来回改。
    """
    conn.execute(
        """
        DELETE FROM user_alas_configs
        WHERE rowid IN (
            SELECT candidate.rowid
            FROM user_alas_configs candidate
            JOIN user_alas_configs preferred
              ON preferred.config_key = candidate.config_key
             AND (
                  preferred.updated_at > candidate.updated_at
                  OR (
                      preferred.updated_at = candidate.updated_at
                      AND (
                          preferred.username < candidate.username
                          OR (preferred.username = candidate.username AND preferred.rowid < candidate.rowid)
                      )
                  )
             )
        )
        """
    )


def _ensure_alas_config_owner_index(conn: sqlite3.Connection) -> None:
    """Keep the one-owner-per-config index built on ``config_key``.

    历史库里的同名索引建在 ``config_name`` 上（大小写敏感），列换了但名字没换，
    所以 ``CREATE UNIQUE INDEX IF NOT EXISTS`` 不会重建它 —— 这里显式比对列。
    """
    name = "ux_user_alas_configs_config_owner"
    existing = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
        (name,),
    ).fetchone()
    if existing:
        columns = [row[2] for row in conn.execute(f"PRAGMA index_info('{name}')").fetchall()]
        if columns != ["config_key"]:
            conn.execute(f"DROP INDEX {name}")
            existing = None
    if not existing:
        conn.execute(f"CREATE UNIQUE INDEX {name} ON user_alas_configs(config_key)")


def _migrate_user_alas_configs(conn: sqlite3.Connection) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        columns = conn.execute("PRAGMA table_info(user_alas_configs)").fetchall()
        column_names = {row[1] for row in columns}
        primary_key = [row[1] for row in sorted((row for row in columns if row[5]), key=lambda row: row[5])]
        # Rebuild whenever the table is not *definitionally* identical to the
        # canonical DDL (missing/renamed columns, wrong primary key, no foreign
        # keys or CHECK constraints from an older CREATE TABLE).  Comparing
        # against a scratch table keeps the legacy path and the fresh-install
        # definition in sync without hand-written expectations (ISSUE-132).
        canonical = storage_schema.matches_canonical_definition(
            conn,
            "user_alas_configs",
            lambda scratch: storage_schema.create_user_alas_configs_table(conn, scratch),
            "__canonical_user_alas_configs",
        )
        if not canonical or primary_key != ["username", "config_name"]:
            conn.execute("DROP TABLE IF EXISTS user_alas_configs_new")
            _create_user_alas_configs_table(conn, "user_alas_configs_new")
            migrated_device_id = "old.device_id" if "device_id" in column_names else "NULL"
            # 旧表可能没有 config_key（甚至没有 is_default），键在 Python 里补；
            # 同键重复行交给下面的 _deduplicate_alas_config_owners 统一收敛。
            legacy_rows = conn.execute(
                f"""
                SELECT old.username AS username,
                       old.config_name AS config_name,
                       {migrated_device_id} AS device_id,
                       CASE WHEN old.can_run <> 0 THEN 1 ELSE 0 END AS can_run,
                       CASE WHEN old.can_edit <> 0 THEN 1 ELSE 0 END AS can_edit,
                       old.updated_at AS updated_at
                FROM user_alas_configs old JOIN users u ON u.username=old.username
                WHERE TRIM(old.config_name) <> ''
                """
            ).fetchall()
            for row in legacy_rows:
                conn.execute(
                    """
                    INSERT INTO user_alas_configs_new
                        (username,config_name,config_key,device_id,can_run,can_edit,is_default,updated_at)
                    VALUES(?,?,?,?,?,?,1,?)
                    """,
                    (
                        row["username"],
                        row["config_name"],
                        storage_alas.config_name_key(row["config_name"]),
                        row["device_id"],
                        int(row["can_run"]),
                        int(row["can_edit"]),
                        row["updated_at"],
                    ),
                )
            conn.execute("DROP TABLE user_alas_configs")
            conn.execute("ALTER TABLE user_alas_configs_new RENAME TO user_alas_configs")
        elif "device_id" not in column_names:
            # Existing assignments predate device-aware ALAS bindings. Keep them
            # explicitly unassigned instead of guessing when a user has multiple
            # devices; an administrator can associate them deliberately.
            conn.execute(
                "ALTER TABLE user_alas_configs "
                "ADD COLUMN device_id TEXT NULL REFERENCES devices(id) ON DELETE SET NULL"
            )
        if "config_key" not in {row[1] for row in conn.execute("PRAGMA table_info(user_alas_configs)").fetchall()}:
            conn.execute("ALTER TABLE user_alas_configs ADD COLUMN config_key TEXT NOT NULL DEFAULT ''")
        _prune_orphan_alas_bindings(conn)
        _backfill_alas_config_keys(conn)
        _deduplicate_alas_config_owners(conn)
        usernames = conn.execute("SELECT DISTINCT username FROM user_alas_configs ORDER BY username").fetchall()
        for row in usernames:
            _ensure_user_alas_default(conn, row["username"])
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_user_alas_configs_one_default "
            "ON user_alas_configs(username) WHERE is_default=1"
        )
        _ensure_alas_config_owner_index(conn)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_alas_configs_device "
            "ON user_alas_configs(device_id)"
        )
    except Exception:
        conn.rollback()
        raise
    conn.commit()


def _setting_value(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def _migrate_video_defaults(conn: sqlite3.Connection) -> None:
    old_defaults = {
        "video_profile": "balanced",
        "video_bit_rate": "4000000",
        "max_size": "1280",
        "max_fps": "30",
    }
    if all(_setting_value(conn, key) == value for key, value in old_defaults.items()):
        for key in ("video_bit_rate", "max_size", "max_fps"):
            conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, DEFAULT_SETTINGS[key]))


def _stored_profile_matrix(conn: sqlite3.Connection) -> dict[str, tuple[int, int, int]] | None:
    result = {}
    for profile in NORMAL_VIDEO_PROFILES:
        try:
            result[profile] = tuple(
                int(_setting_value(conn, f"video_preset_{profile}_{field}"))
                for field in ("video_bit_rate", "max_size", "max_fps")
            )
        except (TypeError, ValueError):
            return None
    return result


def _normalized_video_max_size(value, default: int = 1280) -> int:
    try:
        max_size = int(value)
    except (TypeError, ValueError):
        max_size = int(default)
    if max_size <= 0:
        return MAX_VIDEO_PROFILE_MAX_SIZE
    return min(MAX_VIDEO_PROFILE_MAX_SIZE, max(MIN_VIDEO_PROFILE_MAX_SIZE, max_size))


def _migrate_video_quality_presets(conn: sqlite3.Connection) -> None:
    conn.execute("UPDATE users SET video_mode='normal' WHERE video_mode<>'normal'")
    try:
        current_version = int(_setting_value(conn, "video_quality_migration_version", "0") or "0")
        target_version = int(VIDEO_QUALITY_MIGRATION_VERSION)
    except ValueError:
        current_version = 0
        target_version = int(VIDEO_QUALITY_MIGRATION_VERSION)
    current_matrix = _stored_profile_matrix(conn)
    migration_matrices = LEGACY_VIDEO_PROFILE_MATRICES if current_version < target_version else ()
    for legacy_matrix, upgraded_matrix in migration_matrices:
        if current_matrix != legacy_matrix:
            continue
        for profile, upgraded in upgraded_matrix.items():
            for field, value in zip(("video_bit_rate", "max_size", "max_fps"), upgraded):
                conn.execute(
                    "INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)",
                    (f"video_preset_{profile}_{field}", str(value)),
                )

        default_profile = _setting_value(conn, "video_profile", "balanced")
        if default_profile in legacy_matrix:
            try:
                current_default = tuple(
                    int(_setting_value(conn, field)) for field in ("video_bit_rate", "max_size", "max_fps")
                )
            except (TypeError, ValueError):
                current_default = None
            if current_default == legacy_matrix[default_profile]:
                for field, value in zip(("video_bit_rate", "max_size", "max_fps"), upgraded_matrix[default_profile]):
                    conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (field, str(value)))

        rows = conn.execute(
            "SELECT username,profile,video_bit_rate,max_size,max_fps FROM user_video_preferences"
        ).fetchall()
        for row in rows:
            profile = str(row["profile"] or "")
            try:
                current = (int(row["video_bit_rate"]), int(row["max_size"]), int(row["max_fps"]))
            except (TypeError, ValueError):
                continue
            if profile in legacy_matrix and current == legacy_matrix[profile]:
                conn.execute(
                    "UPDATE user_video_preferences SET video_bit_rate=?,max_size=?,max_fps=? WHERE username=?",
                    (*upgraded_matrix[profile], row["username"]),
                )
        break

    current_matrix = _stored_profile_matrix(conn) or {}
    default_profile = str(_setting_value(conn, "video_profile", "balanced") or "balanced")
    if default_profile.startswith("alas_"):
        default_profile = default_profile.removeprefix("alas_")
        if default_profile not in NORMAL_VIDEO_PROFILES:
            default_profile = "balanced"
        conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('video_profile',?)", (default_profile,))
        if default_profile in current_matrix:
            for field, value in zip(("video_bit_rate", "max_size", "max_fps"), current_matrix[default_profile]):
                conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (field, str(value)))

    for profile in NORMAL_VIDEO_PROFILES:
        key = f"video_preset_{profile}_max_size"
        raw_max_size = _setting_value(conn, key, DEFAULT_SETTINGS[key])
        normalized = _normalized_video_max_size(raw_max_size, int(DEFAULT_SETTINGS[key]))
        if str(raw_max_size) != str(normalized):
            conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, str(normalized)))

    raw_default_max_size = _setting_value(conn, "max_size", DEFAULT_SETTINGS["max_size"])
    default_max_size = _normalized_video_max_size(raw_default_max_size, int(DEFAULT_SETTINGS["max_size"]))
    if str(raw_default_max_size) != str(default_max_size):
        conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('max_size',?)", (str(default_max_size),))

    raw_custom_profiles = _setting_value(conn, "video_custom_profiles", "{}") or "{}"
    try:
        custom_profiles = json.loads(raw_custom_profiles)
    except Exception:
        custom_profiles = {}
    if isinstance(custom_profiles, dict):
        changed = False
        for values in custom_profiles.values():
            if not isinstance(values, dict):
                continue
            raw_max_size = values.get("max_size")
            normalized = _normalized_video_max_size(raw_max_size)
            if raw_max_size != normalized:
                values["max_size"] = normalized
                changed = True
        if changed:
            conn.execute(
                "INSERT OR REPLACE INTO settings(key,value) VALUES('video_custom_profiles',?)",
                (json.dumps(custom_profiles, ensure_ascii=False, separators=(",", ":")),),
            )

    normal_matrix = _stored_profile_matrix(conn) or {}
    rows = conn.execute("SELECT username,profile,max_size FROM user_video_preferences").fetchall()
    for row in rows:
        profile = str(row["profile"] or "")
        if profile.startswith("alas_"):
            mapped = profile.removeprefix("alas_")
            if mapped not in normal_matrix:
                mapped = "balanced"
            values = normal_matrix.get(mapped)
            if values:
                conn.execute(
                    "UPDATE user_video_preferences SET profile=?,video_bit_rate=?,max_size=?,max_fps=? WHERE username=?",
                    (mapped, *values, row["username"]),
                )
            continue
        raw_max_size = row["max_size"]
        fallback_max_size = normal_matrix.get(profile, (0, 1280, 0))[1]
        normalized = _normalized_video_max_size(raw_max_size, fallback_max_size)
        if str(raw_max_size) != str(normalized):
            conn.execute("UPDATE user_video_preferences SET max_size=? WHERE username=?", (normalized, row["username"]))

    conn.execute(
        "INSERT OR REPLACE INTO settings(key,value) VALUES('video_quality_migration_version',?)",
        (str(max(current_version, target_version)),),
    )


def _remove_initial_password_file() -> None:
    try:
        storage_core.INITIAL_ADMIN_PASSWORD_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def generate_random_password() -> str:
    return secrets.token_urlsafe(24)


def _write_initial_password_file(password: str) -> bool:
    """把自动生成的初始管理员密码落盘（0600），避免"生成了却无处可取"把人锁在外面。

    只写一次（O_EXCL），并且 init_db() 每次启动都会先删除该文件，因此它只在
    「首次初始化到下一次重启」这个窗口内存在。用 SCRCPYGATE_ADMIN_PASSWORD_FILE=false
    可以关闭。
    """
    if os.environ.get("SCRCPYGATE_ADMIN_PASSWORD_FILE", "true").strip().lower() in ("0", "false", "no", "off"):
        return False
    path = storage_core.INITIAL_ADMIN_PASSWORD_FILE
    try:
        descriptor = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except OSError:
        return False
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(password + "\n")
    except OSError:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    return True


def _generate_initial_password() -> str:
    _remove_initial_password_file()
    configured = os.environ.get("INITIAL_ADMIN_PASSWORD", "").strip()
    if configured:
        error = validate_password(configured, "admin")
        if error:
            raise ValueError(error)
        return configured
    generated = generate_random_password()
    if _write_initial_password_file(generated):
        AUDIT_LOGGER.warning(
            "INITIAL_ADMIN_PASSWORD_GENERATED file=%s note=%s",
            storage_core.INITIAL_ADMIN_PASSWORD_FILE,
            "read it and change the password; the file is removed on the next start",
        )
    else:
        AUDIT_LOGGER.warning(
            "INITIAL_ADMIN_PASSWORD_GENERATED file=<none> note=%s",
            "set INITIAL_ADMIN_PASSWORD or run 'python -m app.cli reset-admin' to recover access",
        )
    return generated


def get_initial_admin_password_for_display(admin_created: bool = False) -> str:
    if not admin_created:
        return ""
    password = os.environ.get("INITIAL_ADMIN_PASSWORD", "").strip()
    if not password:
        return ""
    with db_connect() as conn:
        row = conn.execute("SELECT password_hash FROM users WHERE username='admin'").fetchone()
    if not row or not verify_password(password, row["password_hash"]):
        return ""
    return password


def migrate_legacy_data() -> bool:
    admin_created = False
    try:
        env = storage_core.parse_env_file(storage_core.LEGACY_ENV_FILE)
    except OSError as exc:
        raise RuntimeError("legacy ALAS environment file cannot be read safely") from exc
    with db_connect() as conn:
        user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if user_count == 0:
            if storage_core.LEGACY_USERS_FILE.exists():
                try:
                    users = json.loads(storage_core.LEGACY_USERS_FILE.read_text(encoding="utf-8"))
                except Exception:
                    users = {}
                for username, info in users.items():
                    role = info.get("role") or ("admin" if info.get("is_admin") else "user")
                    if role not in ("admin", "user"):
                        role = "user"
                    migrated_hash = str(info.get("password_hash") or "")
                    raw_password = str(info.get("password") or "")
                    if not migrated_hash and raw_password:
                        migrated_hash = hash_password(raw_password)
                    conn.execute(
                        "INSERT OR REPLACE INTO users(username,password_hash,role,created_at,must_change_password) VALUES(?,?,?,?,?)",
                        (
                            str(username),
                            migrated_hash,
                            role,
                            str(info.get("created_at") or time.strftime("%Y-%m-%d %H:%M:%S")),
                            1 if info.get("must_change_password") else 0,
                        ),
                    )
        if conn.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0] == 0:
            password = _generate_initial_password()
            existing_admin = conn.execute("SELECT username FROM users WHERE username='admin'").fetchone()
            if existing_admin:
                conn.execute(
                    "UPDATE users SET password_hash=?, role='admin', must_change_password=0 WHERE username='admin'",
                    (hash_password(password),),
                )
            else:
                conn.execute(
                    "INSERT INTO users(username,password_hash,role,created_at,must_change_password) VALUES(?,?,?,?,?)",
                    ("admin", hash_password(password), "admin", time.strftime("%Y-%m-%d %H:%M:%S"), 0),
                )
            admin_created = True
        if env:
            marker = conn.execute(
                "SELECT value FROM settings WHERE key=?",
                (LEGACY_ENV_MIGRATED_SETTING,),
            ).fetchone()
            already_migrated = str(marker["value"] or "").lower() in ("1", "true", "yes", "on") if marker else False
            if not already_migrated:
                for key in ("video_bit_rate", "max_size", "max_fps", "auto_stop_time", "auto_stop_minutes"):
                    raw = env.get(key.upper()) or env.get(key)
                    if raw is not None:
                        conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, str(raw)))
                if env.get("ALAS_GYRE_ENABLED") is not None:
                    enabled = env.get("ALAS_GYRE_ENABLED", "").lower() in ("1", "true", "yes", "on")
                    conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('alas_enabled',?)", ("true" if enabled else "false",))
                for legacy, key in (("ALAS_GYRE_BASE_URL", "alas_base_url"), ("ALAS_GYRE_CONFIG", "alas_current_config")):
                    if env.get(legacy) is not None:
                        conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, env[legacy]))
                if env.get("ALAS_GYRE_TOKEN") is not None:
                    current_token = conn.execute(
                        "SELECT value FROM settings WHERE key='alas_token'"
                    ).fetchone()
                    # Never replace a configured or encrypted token with the
                    # legacy plaintext value.  An empty default is imported
                    # only during this one-time migration.
                    if not current_token or not str(current_token["value"] or "").strip():
                        legacy_token = str(env.get("ALAS_GYRE_TOKEN") or "")
                        if legacy_token and not key_is_configured():
                            conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?, 'true')", (LEGACY_ALAS_TOKEN_PENDING_SETTING,))
                            # 旧 .env 里的明文 token + 没有密钥：跳过导入并告警，不要让
                            # 整个服务（以及 reset-admin）起不来。
                            AUDIT_LOGGER.warning(
                                "ALAS_TOKEN_LEGACY_ENV_SKIPPED key=%s hint=%s",
                                json.dumps(key_diagnostics(), sort_keys=True),
                                "set ALAS_TOKEN_ENCRYPTION_KEY (or run `python -m app.cli "
                                "generate-alas-key`) and restart to import ALAS_GYRE_TOKEN, then "
                                "remove it from the legacy env file",
                            )
                        else:
                            if legacy_token:
                                try:
                                    legacy_token = encrypt_token(legacy_token)
                                except AlasTokenError as exc:
                                    conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?, 'true')", (LEGACY_ALAS_TOKEN_PENDING_SETTING,))
                                    AUDIT_LOGGER.warning(
                                        "ALAS_TOKEN_LEGACY_ENV_SKIPPED error=%s key=%s",
                                        exc,
                                        json.dumps(key_diagnostics(), sort_keys=True),
                                    )
                                    legacy_token = ""
                            if legacy_token:
                                conn.execute(
                                    "INSERT OR REPLACE INTO settings(key,value) VALUES('alas_token',?)",
                                    (legacy_token,),
                                )
                conn.execute(
                    "INSERT OR REPLACE INTO settings(key,value) VALUES(?, 'true')",
                    (LEGACY_ENV_MIGRATED_SETTING,),
                )
                # 遗留设备与授权只能导入一次：data/.env 迁移后不会被删除，
                # 早期实现把这段放在标记之外，导致每次启动都重建已删设备、
                # 并把 (can_view, can_control) 重新发给**所有**用户（含迁移后
                # 新建的账号），管理员撤销的权限会在重启后自行复活。
                devices = []
                fixed = env.get("FIXED_DEVICE", "").strip()
                if fixed:
                    devices.append((fixed, fixed, fixed))
                raw_devices = env.get("ADB_DEVICES", "")
                if raw_devices:
                    try:
                        parsed = json.loads(raw_devices)
                        if isinstance(parsed, dict):
                            for name, address in parsed.items():
                                address = str(address).strip()
                                if address:
                                    devices.append((address, str(name), address))
                    except Exception:
                        pass
                seen = set()
                for device_id, name, address in devices:
                    if device_id in seen:
                        continue
                    seen.add(device_id)
                    conn.execute(
                        "INSERT OR IGNORE INTO devices(id,name,address,enabled,created_at) VALUES(?,?,?,?,?)",
                        (device_id, name or device_id, address, 1, time.strftime("%Y-%m-%d %H:%M:%S")),
                    )
                admins = [row["username"] for row in conn.execute("SELECT username FROM users WHERE role='admin'")]
                users = [row["username"] for row in conn.execute("SELECT username FROM users")]
                all_devices = [row["id"] for row in conn.execute("SELECT id FROM devices")]
                for username in admins:
                    for device_id in all_devices:
                        conn.execute(
                            "INSERT OR IGNORE INTO user_devices(username,device_id,can_view,can_control) VALUES(?,?,1,1)",
                            (username, device_id),
                        )
                for username in users:
                    for device_id, _, _ in devices[:1]:
                        conn.execute(
                            "INSERT OR IGNORE INTO user_devices(username,device_id,can_view,can_control) VALUES(?,?,1,1)",
                            (username, device_id),
                        )
        conn.commit()
    return admin_created


def _prune_audit_alerts(conn: sqlite3.Connection, *, max_rows: int | None = None) -> int:
    """Bound the durable alert projection without dropping open work items.

    Only alerts that are already handled or hidden are removed; visible
    unhandled alerts are the dashboard's to-do list and always survive.  Growth
    is therefore bounded by "alerts an operator actually acts on".
    """
    limit = max(1, int(AUDIT_ALERT_MAX_ROWS if max_rows is None else max_rows))
    total = int(conn.execute("SELECT COUNT(*) FROM audit_alerts").fetchone()[0])
    if total <= limit:
        return 0
    excess = total - limit
    cursor = conn.execute(
        """
        DELETE FROM audit_alerts WHERE event_id IN (
            SELECT event_id FROM audit_alerts
            WHERE handled_at IS NOT NULL OR dashboard_visible = 0
            ORDER BY audit_id
            LIMIT ?
        )
        """,
        (excess,),
    )
    removed = int(cursor.rowcount or 0)
    if removed:
        AUDIT_LOGGER.info("AUDIT_ALERTS_PRUNED removed=%s total=%s limit=%s", removed, total, limit)
    remaining = total - removed
    if remaining > limit:
        AUDIT_LOGGER.warning(
            "AUDIT_ALERTS_RETENTION_BLOCKED total=%s limit=%s pending=%s",
            remaining,
            limit,
            conn.execute(
                "SELECT COUNT(*) FROM audit_alerts WHERE dashboard_visible=1 AND handled_at IS NULL"
            ).fetchone()[0],
        )
    return removed


def prune_expired_sessions(now: int | None = None) -> int:
    """Delete session rows that ``get_session()`` would reject anyway.

    Sessions were only cleaned lazily (on a later request carrying the same
    cookie) or when the whole account expired/vanished, so abandoned rows from
    active accounts accumulated forever (ISSUE-131: 562 of 1018 rows in the
    preview database were already expired).
    """
    current = int(now if now is not None else now_ts())
    with db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            """
            DELETE FROM sessions
            WHERE expires_at < ?
               OR idle_expires_at < ?
               OR absolute_expires_at < ?
            """,
            (current, current, current),
        )
        removed = int(cursor.rowcount or 0)
        conn.commit()
    if removed:
        AUDIT_LOGGER.info("SESSION_RETENTION_PRUNED removed=%s", removed)
    return removed


def prune_viewer_watch_history(now_ms: int | None = None) -> int:
    """Drop finished watch sessions older than the retention window.

    ``VIEWER_WATCH_RETENTION_DAYS=0`` keeps history forever.  Rows that are
    still open (``ended_at_ms IS NULL``) are never touched.
    """
    days = int(VIEWER_WATCH_RETENTION_DAYS)
    if days <= 0:
        return 0
    current = int(now_ms if now_ms is not None else time.time() * 1000)
    cutoff = current - days * 86400_000
    with db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            "DELETE FROM viewer_watch_sessions WHERE ended_at_ms IS NOT NULL AND ended_at_ms < ?",
            (cutoff,),
        )
        removed = int(cursor.rowcount or 0)
        conn.commit()
    if removed:
        AUDIT_LOGGER.info("VIEWER_WATCH_RETENTION_PRUNED removed=%s days=%s", removed, days)
    return removed


def prune_audit_log_by_age(days: int, now: float | None = None) -> int:
    """Trim audit rows older than the retention window without breaking the chain.

    审计哈希链要求「删前缀 + 前移锚点」，所以这里沿用 :func:`_prune_audit_prefix`
    的记账方式（锚点、event_count、pruned_count、告警投影链接），只是把「按行数超限」
    换成「按天超龄」。``days <= 0`` 表示永久保留。
    """
    days = max(0, int(days))
    if days <= 0:
        return 0
    cutoff = int(now if now is not None else time.time()) - days * 86400
    with db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        state = conn.execute("SELECT * FROM audit_integrity_state WHERE singleton=1").fetchone()
        if state is None:
            conn.commit()
            return 0
        # Wall-clock corrections can put a newer event before an older one.
        # Only remove a contiguous expired prefix, never a retained event.
        boundary = conn.execute(
            """SELECT id, event_hash FROM audit_log
               WHERE ts < ? AND id < COALESCE(
                   (SELECT MIN(id) FROM audit_log WHERE ts >= ?),
                   (SELECT COALESCE(MAX(id), 0) + 1 FROM audit_log)
               ) ORDER BY id DESC LIMIT 1""",
            (cutoff, cutoff),
        ).fetchone()
        if boundary is None:
            conn.commit()
            return 0
        cutoff_id = int(boundary["id"])
        prune_count = int(
            conn.execute("SELECT COUNT(*) FROM audit_log WHERE id <= ?", (cutoff_id,)).fetchone()[0]
        )
        if prune_count <= 0:
            conn.commit()
            return 0
        # 与按行数裁剪同样的自检：记账行数与实际行数不一致时宁可不裁剪。
        actual_count = int(conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0])
        current_count = max(0, int(state["event_count"] or 0))
        if actual_count != current_count:
            conn.commit()
            AUDIT_LOGGER.critical(
                "AUDIT_AGE_RETENTION_SKIPPED state_count=%s row_count=%s",
                current_count,
                actual_count,
                extra={"event_name": "audit.retention_skipped"},
            )
            return 0
        conn.execute("DELETE FROM audit_log WHERE id <= ?", (cutoff_id,))
        conn.execute(
            "UPDATE audit_alerts SET log_url='' WHERE audit_id <= ? AND log_url <> ''",
            (cutoff_id,),
        )
        _prune_audit_alerts(conn)
        conn.execute(
            """
            UPDATE audit_integrity_state
            SET anchor_event_id=?, anchor_event_hash=?,
                event_count=event_count-?, pruned_count=pruned_count+?, updated_at=?
            WHERE singleton=1
            """,
            (
                cutoff_id,
                str(boundary["event_hash"] or ""),
                prune_count,
                prune_count,
                now_ts(),
            ),
        )
        conn.commit()
    AUDIT_LOGGER.info(
        "AUDIT_AGE_RETENTION_PRUNED count=%s days=%s",
        prune_count,
        days,
        extra={
            "event_name": "audit.age_retention_pruned",
            "event_fields": {"pruned_count": prune_count, "retention_days": days},
        },
    )
    return prune_count


def normalize_log_retention_days(value: object) -> int:
    """把任意存量/输入值收敛到允许的档位。

    合法档位直接返回；非数字退回默认档位；其余按「不超过它的最大档位」收敛
    （2 → 1、45 → 30、负数 → 0），这样老库里留下的任意天数不会让维护逻辑拿到
    一个界面无法表达的值。
    """
    try:
        days = int(str(value).strip())
    except (TypeError, ValueError):
        return LOG_RETENTION_DEFAULT_DAYS
    if days in LOG_RETENTION_DAY_OPTIONS:
        return days
    if days <= 0:
        return 0
    allowed = [option for option in LOG_RETENTION_DAY_OPTIONS if 0 < option <= days]
    return max(allowed) if allowed else LOG_RETENTION_DAY_OPTIONS[-1]


def log_retention_days() -> int:
    """Current 「日志保存时长」in days (0 = 不清理 / keep forever)."""
    return normalize_log_retention_days(get_setting("log_retention_days", LOG_RETENTION_DEFAULT_DAYS))


def run_log_retention(now: float | None = None, *, force: bool = False) -> dict:
    """按「日志保存时长」清理审计日志与运行日志轮转段（每天最多一次）。

    维护循环每 10 秒调用一次 :func:`run_storage_maintenance`，所以这里用持久化的
    「上次执行日期」做闸门：既不会每次维护都全表扫描，也不会因重启反复裁剪。
    """
    days = log_retention_days()
    if days <= 0:
        return {"log_retention": "disabled", "audit_rows": 0, "log_files": 0}
    current = time.localtime(now if now is not None else time.time())
    today = time.strftime("%Y-%m-%d", current)
    if not force and str(get_setting(LOG_RETENTION_STATE_SETTING, "") or "") == today:
        return {"log_retention": "skipped", "audit_rows": 0, "log_files": 0}
    audit_rows = prune_audit_log_by_age(days, now)
    log_files = prune_rotated_log_files(days, now)
    set_setting(LOG_RETENTION_STATE_SETTING, today)
    if log_files:
        AUDIT_LOGGER.info(
            "RUNTIME_LOG_RETENTION_PRUNED files=%s days=%s",
            log_files,
            days,
            extra={
                "event_name": "runtime_log.retention_pruned",
                "event_fields": {"removed_files": log_files, "retention_days": days},
            },
        )
    return {"log_retention": "pruned", "audit_rows": audit_rows, "log_files": log_files, "days": days}


def normalize_access_retention_days(value: object) -> int:
    """把存量/输入值收敛到允许的保留档位（与日志保留同一套「就近」规则）。"""
    try:
        days = int(str(value).strip())
    except (TypeError, ValueError):
        return ACCESS_RETENTION_DEFAULT_DAYS
    if days in ACCESS_RETENTION_DAY_OPTIONS:
        return days
    if days <= 0:
        return 0
    allowed = [option for option in ACCESS_RETENTION_DAY_OPTIONS if 0 < option <= days]
    return max(allowed) if allowed else ACCESS_RETENTION_DAY_OPTIONS[-1]


def access_retention_days() -> int:
    """当前访问明细保留天数（0 = 不按时间清理，仅受行数上限约束）。"""
    return normalize_access_retention_days(get_setting("access_retention_days", ACCESS_RETENTION_DEFAULT_DAYS))


def access_log_enabled() -> bool:
    return str(get_setting("access_log_enabled", "true") or "true").strip().lower() in ("1", "true", "yes", "on")


def access_drop_state() -> dict:
    """累计丢弃数与最近丢弃时间（演示「记录不完整」状态，不冒充总数）。"""
    try:
        total = int(str(get_setting(ACCESS_DROPPED_TOTAL_SETTING, "0") or "0"))
    except (TypeError, ValueError):
        total = 0
    try:
        last_ts = int(str(get_setting(ACCESS_LAST_DROP_TS_SETTING, "0") or "0"))
    except (TypeError, ValueError):
        last_ts = 0
    return {"dropped_total": max(0, total), "last_drop_ts": max(0, last_ts), "sampled": total > 0}


def record_access_batch(records: list, dropped: int = 0, dropped_ts: int = 0) -> int:
    """写入一批访问明细（+ 汇总 upsert）；由单写线程调用。"""
    return storage_access.record_access_batch(
        records, connect=db_connect, dropped=dropped, dropped_ts=dropped_ts, max_rows=ACCESS_MAX_DETAIL_ROWS
    )


def query_access_records(**kwargs) -> dict:
    return storage_access.query_access_records(connect=db_connect, **kwargs)


def query_access_ip_summaries(**kwargs) -> dict:
    return storage_access.query_access_ip_summaries(connect=db_connect, **kwargs)


def access_status_counts(**kwargs) -> dict:
    return storage_access.access_status_counts(connect=db_connect, **kwargs)


def access_scan_hints(**kwargs) -> dict:
    return storage_access.access_scan_hints(connect=db_connect, **kwargs)


def prune_access_records(days: int, now: float | None = None) -> dict:
    return storage_access.prune_access_records(
        connect=db_connect, days=days, now=now, max_rows=ACCESS_MAX_DETAIL_ROWS
    )


# ---------------------------------------------------------------- IP 封禁（BAN）

def get_ban(ip: str) -> dict | None:
    return storage_bans.get_ban(connect=db_connect, ip=ip)


def get_active_ban(ip: str, now: int | None = None) -> dict | None:
    return storage_bans.get_active_ban(connect=db_connect, ip=ip, now=now)


def active_bans(now: int | None = None, limit: int | None = None) -> list:
    return storage_bans.active_bans(connect=db_connect, now=now, limit=limit)


def list_bans(**kwargs) -> dict:
    return storage_bans.list_bans(connect=db_connect, **kwargs)


def upsert_ban(**kwargs) -> dict:
    return storage_bans.upsert_ban(connect=db_connect, **kwargs)


def revoke_ban(**kwargs) -> dict | None:
    return storage_bans.revoke_ban(connect=db_connect, **kwargs)


def record_ban_event(**kwargs) -> int:
    return storage_bans.record_ban_event(connect=db_connect, **kwargs)


def expire_bans(now: int | None = None, batch: int = storage_bans.PRUNE_BATCH) -> int:
    return storage_bans.expire_bans(connect=db_connect, now=now, batch=batch)


def list_ban_events(**kwargs) -> dict:
    return storage_bans.list_ban_events(connect=db_connect, **kwargs)


def ban_counters(now: int | None = None) -> dict:
    return storage_bans.ban_counters(connect=db_connect, now=now)


def prune_ban_history(retention_days: int | None = None, now: int | None = None) -> int:
    days = BAN_EVENT_RETENTION_DAYS if retention_days is None else int(retention_days)
    return storage_bans.prune_ban_history(connect=db_connect, retention_days=days, now=now)


def run_access_retention(now: float | None = None, *, force: bool = False) -> dict:
    """按「访问记录保留天数」清理明细与汇总（每天最多一次，分批有界）。"""
    days = access_retention_days()
    current = time.localtime(now if now is not None else time.time())
    today = time.strftime("%Y-%m-%d", current)
    if not force and str(get_setting(ACCESS_RETENTION_STATE_SETTING, "") or "") == today:
        removed = prune_access_records(0, now)
        return {"access_retention": "capacity_checked", **removed}
    removed = prune_access_records(days, now)
    if not removed.get("pending_age"):
        set_setting(ACCESS_RETENTION_STATE_SETTING, today)
    total_removed = int(removed.get("records_removed_by_age") or 0) + int(removed.get("records_removed_by_cap") or 0)
    if total_removed or removed.get("summary_rows_removed"):
        AUDIT_LOGGER.info(
            "ACCESS_RETENTION_PRUNED records=%s summary=%s days=%s",
            total_removed,
            removed.get("summary_rows_removed"),
            days,
            extra={
                "event_name": "access.retention_pruned",
                "event_fields": {
                    "removed_records": total_removed,
                    "removed_summary_rows": int(removed.get("summary_rows_removed") or 0),
                    "retention_days": days,
                },
            },
        )
    return {"access_retention": "pruned" if total_removed else "clean", "records_removed": total_removed, **removed}


def run_storage_maintenance() -> dict:
    """Periodic retention sweep (startup + the account monitor loop).

    The alert projection is pruned from the audit-log prune path instead: it
    only needs attention when the log itself is trimmed.
    """
    result = {
        "sessions": prune_expired_sessions(),
        "watch_sessions": prune_viewer_watch_history(),
    }
    result.update(run_log_retention())
    result.update(run_access_retention())
    return result


def _prune_audit_prefix(
    conn: sqlite3.Connection,
    state: sqlite3.Row,
    *,
    reserve_rows: int = 1,
) -> sqlite3.Row:
    """Bound retained rows while preserving a verifiable chain anchor."""
    max_rows = max(1, int(AUDIT_MAX_ROWS))
    current_count = max(0, int(state["event_count"] or 0))
    target_count = max(0, max_rows - max(0, int(reserve_rows)))
    if current_count <= target_count:
        return state
    actual_count = int(conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0])
    if actual_count != current_count:
        AUDIT_LOGGER.critical(
            "AUDIT_RETENTION_SKIPPED state_count=%s row_count=%s",
            current_count,
            actual_count,
            extra={"event_name": "audit.retention_skipped"},
        )
        return state
    excess = current_count - target_count
    preferred_batch = min(max(1, int(AUDIT_PRUNE_BATCH)), max(1, max_rows // 10))
    prune_count = min(current_count, max(excess, preferred_batch))
    boundary = conn.execute(
        "SELECT id, event_hash FROM audit_log ORDER BY id LIMIT 1 OFFSET ?",
        (prune_count - 1,),
    ).fetchone()
    if boundary is None:
        return state
    conn.execute("DELETE FROM audit_log WHERE id <= ?", (int(boundary["id"]),))
    # The alert projection keeps its own copy of title/summary/detail, so it
    # survives log trimming — but a link into /logs for a trimmed event would
    # dead-end (the detail API answers 404).  Blank the stored link so the UI
    # stops offering it, and bound the projection in the same pass.
    conn.execute(
        "UPDATE audit_alerts SET log_url='' WHERE audit_id <= ? AND log_url <> ''",
        (int(boundary["id"]),),
    )
    _prune_audit_alerts(conn)
    conn.execute(
        """
        UPDATE audit_integrity_state
        SET anchor_event_id=?, anchor_event_hash=?,
            event_count=event_count-?, pruned_count=pruned_count+?, updated_at=?
        WHERE singleton=1
        """,
        (
            int(boundary["id"]),
            str(boundary["event_hash"] or ""),
            prune_count,
            prune_count,
            now_ts(),
        ),
    )
    AUDIT_LOGGER.info(
        "AUDIT_RETENTION_PRUNED count=%s",
        prune_count,
        extra={
            "event_name": "audit.retention_pruned",
            "event_fields": {"pruned_count": prune_count},
        },
    )
    return conn.execute(
        "SELECT * FROM audit_integrity_state WHERE singleton=1"
    ).fetchone()


def record_audit_event(
    username: str = "",
    action: str = "",
    detail: str = "",
    *,
    actor: str | None = None,
    actor_role: str = "unknown",
    target_type: str = "",
    target_id: str = "",
    outcome: str = "success",
    reason: str = "",
    severity: str = "info",
    request_id: str = "",
    source_ip: str = "",
    user_agent: str = "",
    metadata: dict | None = None,
    event_id: str = "",
    dedupe_key: str | None = None,
    ts: int | None = None,
) -> dict | None:
    """Append one bounded, redacted audit event without breaking business work."""
    safe_action = _audit_clean_name(action, "event")
    try:
        safe_username = sanitize_log_text(actor if actor is not None else username, 128) or "?"
        safe_role = _audit_clean_name(actor_role, "unknown", 24)
        if safe_role not in AUDIT_ACTOR_ROLES:
            safe_role = "unknown"
        safe_outcome = _audit_clean_name(outcome, "unknown", 24)
        if safe_outcome not in AUDIT_OUTCOMES:
            safe_outcome = "unknown"
        safe_severity = _audit_clean_name(severity, "info", 24)
        if safe_severity not in AUDIT_SEVERITIES:
            safe_severity = "info"
        safe_target_type = _audit_clean_name(target_type, "", 64) if target_type else ""
        safe_target_id = sanitize_log_text(target_id, 256)
        safe_request_id = sanitize_log_text(request_id, 96)
        if safe_request_id and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,95}", safe_request_id):
            safe_request_id = ""
        safe_event_id = sanitize_log_text(event_id, 128)
        if not safe_event_id or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", safe_event_id):
            safe_event_id = uuid.uuid4().hex
        safe_dedupe_key = sanitize_log_text(dedupe_key, 160) if dedupe_key else None
        event_ts = now_ts() if ts is None else max(0, int(ts))
        event = {
            "event_id": safe_event_id,
            "ts": event_ts,
            "username": safe_username,
            "actor_role": safe_role,
            "action": safe_action,
            "target_type": safe_target_type,
            "target_id": safe_target_id,
            "outcome": safe_outcome,
            "reason": sanitize_log_text(reason, 512),
            "severity": safe_severity,
            "request_id": safe_request_id,
            "source_ip": _audit_clean_ip(source_ip),
            "user_agent": sanitize_log_text(user_agent, 512),
            "detail": sanitize_log_text(detail, 2048),
            "metadata_json": _audit_metadata_json(metadata),
            "schema_version": AUDIT_SCHEMA_VERSION,
            "dedupe_key": safe_dedupe_key,
        }

        with db_connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if safe_dedupe_key:
                existing = conn.execute(
                    "SELECT * FROM audit_log WHERE dedupe_key=?", (safe_dedupe_key,)
                ).fetchone()
                if existing:
                    conn.commit()
                    return _audit_row_to_dict(existing)
            state = conn.execute(
                "SELECT * FROM audit_integrity_state WHERE singleton=1"
            ).fetchone()
            if state is None:
                raise RuntimeError("audit integrity state is unavailable")
            state = _prune_audit_prefix(conn, state)
            previous_hash = str(state["head_event_hash"] or "")
            event_hash = _audit_event_hash(previous_hash, event)
            cursor = conn.execute(
                """
                INSERT INTO audit_log(
                    event_id, ts, username, actor_role, action, target_type,
                    target_id, outcome, reason, severity, request_id, source_ip,
                    user_agent, detail, metadata_json, schema_version, prev_hash,
                    event_hash, dedupe_key
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    event["event_id"], event["ts"], event["username"], event["actor_role"],
                    event["action"], event["target_type"], event["target_id"], event["outcome"],
                    event["reason"], event["severity"], event["request_id"], event["source_ip"],
                    event["user_agent"], event["detail"], event["metadata_json"],
                    event["schema_version"], previous_hash, event_hash, event["dedupe_key"],
                ),
            )
            audit_id = int(cursor.lastrowid)
            conn.execute(
                """
                UPDATE audit_integrity_state
                SET head_event_id=?, head_event_hash=?, event_count=event_count+1, updated_at=?
                WHERE singleton=1
                """,
                (audit_id, event_hash, now_ts()),
            )
            inserted = conn.execute("SELECT * FROM audit_log WHERE id=?", (audit_id,)).fetchone()
            projection = _alert_projection(event) if _is_alert_event(event["outcome"], event["severity"]) else None
            if projection:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO audit_alerts(
                        event_id, audit_id, ts, username, actor_role, action,
                        target_type, target_id, outcome, reason, severity,
                        request_id, source_ip, detail, metadata_json,
                        alert_type, title, summary, log_url, dashboard_visible
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        event["event_id"], audit_id, event["ts"], event["username"],
                        event["actor_role"], event["action"], event["target_type"],
                        event["target_id"], event["outcome"], event["reason"],
                        event["severity"], event["request_id"], event["source_ip"],
                        event["detail"], event["metadata_json"],
                        projection["alert_type"], projection["title"], projection["summary"],
                        projection["log_url"], projection["dashboard_visible"],
                    ),
                )
            conn.commit()
        return _audit_row_to_dict(inserted)
    except Exception:
        AUDIT_LOGGER.critical(
            "AUDIT_WRITE_FAILED",
            exc_info=True,
            extra={"event_name": "audit.write_failed", "event_fields": {"action": safe_action}},
        )
        return None


def audit(username: str, action: str, detail: str = "") -> None:
    record_audit_event(
        username,
        action,
        detail,
        outcome="unknown",
        actor_role="unknown",
    )


def get_setting(key: str, default: str = "") -> str:
    with db_connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with db_connect() as conn:
        conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, str(value)))
        conn.commit()


def set_settings(values: dict[str, object]) -> None:
    if not values:
        return
    with db_connect() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)",
            [(str(key), str(value)) for key, value in values.items()],
        )
        conn.commit()


# ---- 登录保护：PoW 签名挑战与失败封禁的持久化 ----

LOGIN_GUARD_HMAC_KEY_SETTING = "login_captcha_hmac_key"


def ensure_login_guard_hmac_key() -> bytes:
    """Return the persistent 32-byte HMAC key that signs login PoW challenges.

    Generated once per install and stored in ``settings`` so a restart does not
    invalidate the HMAC signature of already-issued challenges.
    """
    with db_connect() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key=?", (LOGIN_GUARD_HMAC_KEY_SETTING,)
        ).fetchone()
        if row and str(row["value"]).strip():
            try:
                key = bytes.fromhex(str(row["value"]).strip())
                if len(key) >= 32:
                    return key
            except ValueError:
                pass
        candidate = secrets.token_bytes(32)
        conn.execute(
            "INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)",
            (LOGIN_GUARD_HMAC_KEY_SETTING, candidate.hex()),
        )
        row = conn.execute(
            "SELECT value FROM settings WHERE key=?", (LOGIN_GUARD_HMAC_KEY_SETTING,)
        ).fetchone()
        conn.commit()
        return bytes.fromhex(str(row["value"]))


def create_login_challenge(
    challenge_id: str,
    salt: str,
    bits: int,
    max_number: int,
    expires: float,
    ip: str,
    username: str,
) -> None:
    with db_connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO login_challenges("
            "challenge_id,salt,bits,max_number,expires,ip,username,used,issued_at"
            ") VALUES(?,?,?,?,?,?,?,0,?)",
            (challenge_id, salt, bits, max_number, expires, ip, username, time.time()),
        )
        conn.commit()


def consume_login_challenge(challenge_id: str) -> dict[str, object]:
    """Atomically mark a challenge used; returns its row with the consumed flag."""
    with db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM login_challenges WHERE challenge_id=?", (challenge_id,)
        ).fetchone()
        if not row:
            conn.rollback()
            return {"status": "unknown"}
        if bool(row["used"]):
            conn.rollback()
            return {"status": "reused", "row": row}
        cursor = conn.execute(
            "UPDATE login_challenges SET used=1 WHERE challenge_id=? AND used=0",
            (challenge_id,),
        )
        if cursor.rowcount != 1:
            conn.rollback()
            return {"status": "reused", "row": row}
        conn.commit()
        return {"status": "consumed", "row": row}


def prune_login_challenges(now: float) -> int:
    with db_connect() as conn:
        cursor = conn.execute("DELETE FROM login_challenges WHERE expires < ?", (now,))
        conn.commit()
        return max(0, cursor.rowcount)


def create_pow_challenge(challenge_id: str, provider: str, fingerprint: str,
                         expires: float, ip: str, username: str) -> bool:
    """Bound persistent state globally and per source, including used proofs.

    BEGIN IMMEDIATE prevents concurrent issuance from overshooting either cap.
    Expired entries are pruned via an index; live replay state is never evicted.
    """
    with db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM login_pow_challenges WHERE expires<=?", (time.time(),))
        total = conn.execute("SELECT COUNT(*) FROM login_pow_challenges").fetchone()[0]
        source = conn.execute("SELECT COUNT(*) FROM login_pow_challenges WHERE ip=?", (ip,)).fetchone()[0]
        if total >= 10_000 or source >= 64:
            conn.commit()
            return False
        conn.execute(
            "INSERT INTO login_pow_challenges(challenge_id,provider,fingerprint,expires,ip,username) VALUES(?,?,?,?,?,?)",
            (challenge_id, provider, fingerprint, expires, ip, username),
        )
        conn.commit()
        return True


def get_pow_challenge(challenge_id: str) -> dict | None:
    with db_connect() as conn:
        row = conn.execute("SELECT * FROM login_pow_challenges WHERE challenge_id=?", (challenge_id,)).fetchone()
        return dict(row) if row else None


def consume_pow_challenge(challenge_id: str, fingerprint: str, ip: str, username: str) -> bool:
    """Consume only a verified, unexpired proof; concurrent replay wins once."""
    with db_connect() as conn:
        cursor = conn.execute(
            "UPDATE login_pow_challenges SET used=1 WHERE challenge_id=? AND fingerprint=? "
            "AND ip=? AND username=? AND used=0 AND expires>?",
            (challenge_id, fingerprint, ip, username, time.time()),
        )
        conn.commit()
        return cursor.rowcount == 1


def save_login_guard_state(
    failures: dict[str, list[tuple[str, float]]],
    locked_until: dict[str, float],
) -> None:
    with db_connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO login_guard_state(singleton,failures_json,locked_until_json,updated_at) "
            "VALUES('state',?,?,?)",
            (json.dumps(failures), json.dumps(locked_until), time.time()),
        )
        conn.commit()


def load_login_guard_state() -> tuple[dict[str, list[tuple[str, float]]], dict[str, float]] | None:
    with db_connect() as conn:
        row = conn.execute(
            "SELECT * FROM login_guard_state WHERE singleton='state'"
        ).fetchone()
    if not row:
        return None
    try:
        failures_raw = json.loads(str(row["failures_json"]))
        locked_raw = json.loads(str(row["locked_until_json"]))
    except ValueError:
        return None
    if not isinstance(failures_raw, dict) or not isinstance(locked_raw, dict):
        return None
    failures: dict[str, list[tuple[str, float]]] = {}
    for key, entries in failures_raw.items():
        if not isinstance(entries, list):
            continue
        cleaned = []
        for entry in entries:
            if isinstance(entry, list) and len(entry) == 2:
                cleaned.append((str(entry[0]), float(entry[1])))
        if cleaned:
            failures[str(key)] = cleaned
    locked: dict[str, float] = {}
    for key, value in locked_raw.items():
        try:
            locked[str(key)] = float(value)
        except (TypeError, ValueError):
            continue
    return failures, locked



def update_settings(mutator) -> dict[str, str]:
    with db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute("SELECT key,value FROM settings").fetchall()
        settings = dict(DEFAULT_SETTINGS)
        settings.update({row["key"]: row["value"] for row in rows})
        values = mutator(dict(settings)) or {}
        normalized = {str(key): str(value) for key, value in values.items()}
        if normalized:
            conn.executemany(
                "INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)",
                list(normalized.items()),
            )
            settings.update(normalized)
        conn.commit()
        return settings


_public_salt_cache: dict[str, str] = {}
_public_salt_lock = threading.Lock()


def get_or_create_public_salt() -> str:
    """Return one stable public-ID salt per database path.

    The process lock avoids duplicate reads during a threaded cold start. The
    conditional upsert remains necessary across processes and only replaces a
    missing or explicitly empty value.
    """
    db_path_key = _normalized_path(storage_core.DB_PATH)
    cached = _public_salt_cache.get(db_path_key)
    if cached:
        return cached

    with _public_salt_lock:
        cached = _public_salt_cache.get(db_path_key)
        if cached:
            return cached
        with db_connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key='device_public_salt'").fetchone()
            salt = str(row["value"] or "") if row else ""
            if not salt:
                conn.execute(
                    """
                    INSERT INTO settings(key,value) VALUES('device_public_salt',?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value
                    WHERE settings.value=''
                    """,
                    (secrets.token_urlsafe(32),),
                )
                conn.commit()
                row = conn.execute(
                    "SELECT value FROM settings WHERE key='device_public_salt'"
                ).fetchone()
                salt = str(row["value"] or "") if row else ""
        if not salt:
            raise RuntimeError("device public salt initialization failed")
        _public_salt_cache[db_path_key] = salt
        return salt


def public_device_id(device_id: str) -> str:
    raw = (device_id or "").strip()
    if not raw:
        return ""
    salt = get_or_create_public_salt().encode("utf-8")
    digest = hmac.new(salt, raw.encode("utf-8"), hashlib.sha256).hexdigest()[:18]
    return f"dev_{digest}"


def resolve_device_ref(device_ref: str) -> str | None:
    """Resolve an internal or opaque public ID with one device-list query."""
    ref = (device_ref or "").strip()
    if not ref:
        return None
    devices = list_all_devices()
    if any(str(device["id"]) == ref for device in devices):
        return ref
    for device in devices:
        if hmac.compare_digest(public_device_id(device["id"]), ref):
            return device["id"]
    return None


def get_settings(keys: list[str] | None = None) -> dict:
    with db_connect() as conn:
        if keys:
            rows = conn.execute("SELECT key,value FROM settings WHERE key IN (%s)" % ",".join("?" for _ in keys), keys).fetchall()
        else:
            rows = conn.execute("SELECT key,value FROM settings").fetchall()
    data = dict(DEFAULT_SETTINGS)
    for key in ("geo_mode", "geo_allowed_countries", "geo_unknown_action", "geo_allow_cidrs"):
        data[key] = os.environ.get(key.upper(), DEFAULT_SETTINGS[key])
    data.update({row["key"]: row["value"] for row in rows})
    return data


VIEWER_STOP_SETTING_KEYS = (
    "viewer_hidden_stop_enabled",
    "viewer_hidden_stop_minutes",
    "viewer_blur_stop_enabled",
    "viewer_blur_stop_minutes",
    "viewer_stop_settings_synced",
)


def _setting_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    raw = str(value or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def _viewer_stop_minutes(value: object, default: int = 5) -> int:
    try:
        minutes = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return minutes if 5 <= minutes <= 10 else default


def viewer_stop_settings(settings: dict | None = None) -> dict:
    """Return the bounded viewer stop policy consumed by browser clients."""
    source = settings if settings is not None else get_settings(list(VIEWER_STOP_SETTING_KEYS))
    hidden_enabled = _setting_bool(source.get("viewer_hidden_stop_enabled"), True)
    hidden_minutes = _viewer_stop_minutes(source.get("viewer_hidden_stop_minutes"), 5)
    blur_enabled = _setting_bool(source.get("viewer_blur_stop_enabled"), False)
    blur_minutes = _viewer_stop_minutes(source.get("viewer_blur_stop_minutes"), 5)
    synced = _setting_bool(source.get("viewer_stop_settings_synced"), False)
    if synced:
        blur_enabled = hidden_enabled
        blur_minutes = hidden_minutes
    return {
        "viewer_hidden_stop_enabled": hidden_enabled,
        "viewer_hidden_stop_minutes": hidden_minutes,
        "viewer_blur_stop_enabled": blur_enabled,
        "viewer_blur_stop_minutes": blur_minutes,
        "viewer_stop_settings_synced": synced,
    }


UI_SETTING_KEYS = (
    "ui_system_name",
    "ui_language",
    "ui_theme_mode",
    "expiry_reminder_days",
    "stop_alas_on_expiry",
    "log_retention_days",
    "access_log_enabled",
    "access_retention_days",
    "geo_mode",
    "geo_allowed_countries",
    "geo_unknown_action",
    "geo_allow_cidrs",
)


def _int_setting(settings: dict, key: str, default: int) -> int:
    try:
        return int(str(settings.get(key) or default))
    except (TypeError, ValueError):
        return default


def get_ui_settings() -> dict[str, object]:
    settings = get_settings(list(UI_SETTING_KEYS))
    for key in ("geo_mode", "geo_allowed_countries", "geo_unknown_action", "geo_allow_cidrs"):
        if key not in settings:
            settings[key] = os.environ.get(key.upper(), DEFAULT_SETTINGS[key])
    return {
        "ui_system_name": str(settings.get("ui_system_name") or "ScrcpyGate").strip() or "ScrcpyGate",
        "ui_language": str(settings.get("ui_language") or "").strip(),
        "ui_theme_mode": str(settings.get("ui_theme_mode") or "system").strip() or "system",
        "expiry_reminder_days": _int_setting(settings, "expiry_reminder_days", 3),
        "stop_alas_on_expiry": str(settings.get("stop_alas_on_expiry") or "false").lower() in ("1", "true", "yes", "on"),
        "log_retention_days": normalize_log_retention_days(settings.get("log_retention_days")),
        "access_log_enabled": str(settings.get("access_log_enabled") or "true").lower() in ("1", "true", "yes", "on"),
        "access_retention_days": normalize_access_retention_days(settings.get("access_retention_days")),
        "geo_mode": str(settings.get("geo_mode") or DEFAULT_SETTINGS["geo_mode"]).strip().lower(),
        "geo_allowed_countries": str(settings.get("geo_allowed_countries") or DEFAULT_SETTINGS["geo_allowed_countries"]).strip().upper(),
        "geo_unknown_action": str(settings.get("geo_unknown_action") or DEFAULT_SETTINGS["geo_unknown_action"]).strip().lower(),
        "geo_allow_cidrs": str(settings.get("geo_allow_cidrs") or "").strip(),
    }


def save_ui_settings(updates: dict[str, object]) -> None:
    values: dict[str, str] = {}
    for key, value in updates.items():
        if key not in UI_SETTING_KEYS:
            raise ValueError("invalid ui setting: " + str(key))
        if key in ("ui_system_name", "ui_language", "ui_theme_mode"):
            values[key] = str(value or "").strip()
        elif key in ("geo_mode", "geo_unknown_action", "geo_allowed_countries", "geo_allow_cidrs"):
            # GEO 的档位与清单按文本存储；取值合法性由调用方（admin_settings）校验，
            # 这里只做长度防御，避免超长设置把 DB 撑大。
            values[key] = str(value or "").strip()[:2000]
        elif key in ("stop_alas_on_expiry", "access_log_enabled"):
            if isinstance(value, str):
                parsed = value.strip().lower() in ("1", "true", "yes", "on")
            else:
                parsed = bool(value)
            values[key] = "true" if parsed else "false"
        else:
            try:
                values[key] = str(int(value or 0))
            except (TypeError, ValueError) as exc:
                raise ValueError("invalid ui setting value: " + str(key)) from exc
    set_settings(values)


def reset_ui_settings() -> None:
    set_settings({key: str(DEFAULT_SETTINGS[key]) for key in UI_SETTING_KEYS})


def get_user(username: str):
    with db_connect() as conn:
        return conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()


_watch_timestamp_ms = storage_watch_history.watch_timestamp_ms

# Watch-history behavior lives in ``storage_watch_history``.  Keep these
# private aliases in the compatibility module because ``list_users`` builds
# its user projection inside the same connection/transaction.
_empty_viewer_watch_stats = storage_watch_history.empty_viewer_watch_stats
_viewer_watch_stats_from_conn = storage_watch_history.viewer_watch_stats_from_conn
_viewer_watch_row = storage_watch_history.viewer_watch_row
_viewer_watch_history_from_conn = storage_watch_history.viewer_watch_history_from_conn
_bounded_watch_page = storage_watch_history.bounded_watch_page


def start_viewer_watch(
    username: str,
    device_id: str | None = None,
    *,
    started_at_ms: int | None = None,
    session_id: str | None = None,
) -> str:
    """Persist the start of one authenticated video WebSocket watch."""
    return storage_watch_history.start_viewer_watch(
        username,
        device_id,
        started_at_ms=started_at_ms,
        session_id=session_id,
        connect=db_connect,
    )


def finish_viewer_watch(
    session_id: str,
    *,
    ended_at_ms: int | None = None,
    end_reason: str = "disconnect",
) -> dict | None:
    """Close one watch row idempotently and return its persisted values."""
    return storage_watch_history.finish_viewer_watch(
        session_id,
        ended_at_ms=ended_at_ms,
        end_reason=end_reason,
        connect=db_connect,
    )


def finalize_open_viewer_watch_sessions(*, ended_at_ms: int | None = None) -> int:
    """Bound all rows left open by a process restart."""
    return storage_watch_history.finalize_open_viewer_watch_sessions(
        ended_at_ms=ended_at_ms,
        connect=db_connect,
    )


def viewer_watch_stats(username: str | None = None) -> dict:
    """Return watch totals for one user or an all-user mapping."""
    return storage_watch_history.viewer_watch_stats(username, connect=db_connect)


def viewer_watch_history(username: str, *, limit: int = VIEWER_WATCH_HISTORY_DEFAULT_LIMIT, offset: int = 0) -> list[dict]:
    """Return newest watch sessions for an administrator-facing user view."""
    return storage_watch_history.viewer_watch_history(
        username,
        limit=limit,
        offset=offset,
        connect=db_connect,
    )


def list_users(*, include_watch_data: bool = False, watch_history_limit: int = VIEWER_WATCH_HISTORY_DEFAULT_LIMIT) -> list[dict]:
    with db_connect() as conn:
        users = [
            dict(row)
            for row in conn.execute(
                "SELECT username,role,created_at,must_change_password,expires_at,enabled,alas_visible,"
                "last_login_at,last_login_ip FROM users ORDER BY username"
            )
        ]
        current = now_ts()
        expiring_window = account_expiring_window_seconds(conn)
        watch_stats = _viewer_watch_stats_from_conn(conn) if include_watch_data else {}
        watch_now_ms = _watch_timestamp_ms() if include_watch_data else 0
        watch_page_size, _watch_page_offset = _bounded_watch_page(watch_history_limit, 0)
        for user in users:
            user["enabled"] = bool(user.get("enabled", 1))
            user["alas_visible"] = bool(user.get("alas_visible", 1))
            user["video_mode"] = "normal"
            user.update(storage_users.user_expiration_payload(user, now=current, expiring_window_seconds=expiring_window))
            if include_watch_data:
                username = str(user["username"])
                user["watch_stats"] = watch_stats.get(
                    username,
                    _empty_viewer_watch_stats(),
                )
                history_rows = _viewer_watch_history_from_conn(
                    conn,
                    username,
                    watch_page_size,
                    0,
                    watch_now_ms,
                )
                # Do not expose internal device identifiers through the user
                # list; administrators only need a stable display name.
                user["watch_history"] = [
                    {
                        "id": row["id"],
                        "device_name": row["device_name"] or "未知设备",
                        "started_at_ms": row["started_at_ms"],
                        "ended_at_ms": row["ended_at_ms"],
                        "duration_ms": row["duration_ms"],
                        "end_reason": row["end_reason"],
                        "active": row["active"],
                    }
                    for row in history_rows
                ]
        return users


def record_last_login(username: str, source_ip: str = "", *, ts: int | None = None) -> None:
    """Store the latest successful login context without retaining credentials."""
    username = str(username or "").strip()
    if not username:
        return
    source_ip = str(source_ip or "").strip()[:64]
    with db_connect() as conn:
        conn.execute(
            "UPDATE users SET last_login_at=?, last_login_ip=? WHERE username=?",
            (now_ts() if ts is None else ts, source_ip or None, username),
        )
        conn.commit()


def admin_count(conn: sqlite3.Connection | None = None) -> int:
    if conn is not None:
        return int(conn.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0])
    with db_connect() as local_conn:
        return admin_count(local_conn)


def permanent_admin_count(conn: sqlite3.Connection | None = None) -> int:
    if conn is not None:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM users WHERE role='admin' AND expires_at IS NULL"
            ).fetchone()[0]
        )
    with db_connect() as local_conn:
        return permanent_admin_count(local_conn)


def _ensure_permanent_admin_transition(
    conn: sqlite3.Connection,
    current,
    final_role: str,
    final_expires_at: int | None,
    final_enabled: bool | None = None,
) -> None:
    if not current:
        return
    currently_permanent = current["role"] == "admin" and current["expires_at"] is None
    finally_permanent = final_role == "admin" and final_expires_at is None
    finally_enabled = bool(current["enabled"]) if final_enabled is None and "enabled" in current.keys() else True if final_enabled is None else bool(final_enabled)
    if currently_permanent and (not finally_permanent or not finally_enabled) and available_permanent_admin_count(conn) <= 1:
        raise ValueError("last_permanent_admin_required")


def available_permanent_admin_count(conn: sqlite3.Connection | None = None) -> int:
    """Count permanent, enabled administrators that can recover the system."""
    query = "SELECT COUNT(*) FROM users WHERE role='admin' AND expires_at IS NULL AND enabled=1"
    if conn is not None:
        return int(conn.execute(query).fetchone()[0])
    with db_connect() as local_conn:
        return available_permanent_admin_count(local_conn)


def _revoke_user_access(conn: sqlite3.Connection, username: str) -> tuple[int, int]:
    sessions = conn.execute("DELETE FROM sessions WHERE username=?", (username,)).rowcount
    locks = conn.execute("DELETE FROM control_locks WHERE username=?", (username,)).rowcount
    return int(sessions or 0), int(locks or 0)


def upsert_user(
    username: str,
    password: str | None,
    role: str,
    video_mode: str | None = None,
    expires_at=EXPIRATION_UNSET,
    must_change_password: bool | None = None,
    enabled=ENABLED_UNSET,
    alas_visible=ALAS_VISIBLE_UNSET,
) -> None:
    _ = video_mode  # 兼容旧调用；统一画质不再按用户保存模式。
    username = (username or "").strip()
    if (
        not username
        or len(username) > 64
        or any(ch.isspace() for ch in username)
        or any(ch in username for ch in ("/", "\\"))
        or any(ord(ch) < 32 or ord(ch) == 127 for ch in username)
    ):
        # 控制字符与路径分隔符会让账号无法通过 /api/admin/users/{username}
        # 管理（也能污染审计与日志），因此在存储层统一拒绝。
        raise ValueError("invalid_username")
    if role not in ("admin", "user"):
        raise ValueError("invalid_role")
    if password:
        error = validate_password(password, username)
        if error:
            raise ValueError(error)
    with db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute(
            "SELECT username,role,expires_at,must_change_password,enabled,alas_visible FROM users WHERE username=?", (username,)
        ).fetchone()
        final_must_change_password = (
            bool(current["must_change_password"])
            if must_change_password is None and current
            else bool(must_change_password)
        )
        normalized_expires_at = (
            _user_expires_at(current)
            if expires_at is EXPIRATION_UNSET and current
            else None
            if expires_at is EXPIRATION_UNSET
            else normalize_expires_at(expires_at)
        )
        normalized_enabled = (
            bool(current["enabled"])
            if enabled is ENABLED_UNSET and current
            else True
            if enabled is ENABLED_UNSET
            else bool(enabled)
        )
        # 用户列表里的「显示 ALAS」：只影响界面显隐，不参与权限判定。
        normalized_alas_visible = (
            bool(current["alas_visible"])
            if alas_visible is ALAS_VISIBLE_UNSET and current
            else True
            if alas_visible is ALAS_VISIBLE_UNSET
            else bool(alas_visible)
        )
        if current:
            if current and current["role"] == "admin" and role != "admin" and admin_count(conn) <= 1:
                raise ValueError("last_admin_required")
            _ensure_permanent_admin_transition(conn, current, role, normalized_expires_at, normalized_enabled)
            was_expired = not user_is_active(current)
            will_be_expired = normalized_expires_at is not None and normalized_expires_at <= now_ts()
            if password:
                conn.execute(
                    "UPDATE users SET password_hash=?, role=?, video_mode='normal', expires_at=?, "
                    "must_change_password=?, enabled=?, alas_visible=? WHERE username=?",
                    (
                        hash_password(password),
                        role,
                        normalized_expires_at,
                        int(final_must_change_password),
                        int(normalized_enabled),
                        int(normalized_alas_visible),
                        username,
                    ),
                )
                conn.execute("DELETE FROM sessions WHERE username=?", (username,))
            else:
                conn.execute(
                    "UPDATE users SET role=?, video_mode='normal', expires_at=?, must_change_password=?, enabled=?, "
                    "alas_visible=? WHERE username=?",
                    (
                        role,
                        normalized_expires_at,
                        int(final_must_change_password),
                        int(normalized_enabled),
                        int(normalized_alas_visible),
                        username,
                    ),
                )
            if was_expired or will_be_expired or not normalized_enabled:
                _revoke_user_access(conn, username)
            if current["role"] == "admin" and role != "admin":
                # 管理员建号/建设备时会拿到全设备 (can_view, can_control) 行
                # （_grant_device_to_admins）。降级为普通用户后这些行会让他继续
                # 以普通身份控制全部设备，因此降级即回收；重新提权后管理员本来
                # 就全局放行，需要显式授权时会由管理员重新分配。
                conn.execute("DELETE FROM user_devices WHERE username=?", (username,))
        else:
            if not password:
                raise ValueError("password_required")
            if role == "admin" and normalized_expires_at is not None and available_permanent_admin_count(conn) < 1:
                raise ValueError("last_permanent_admin_required")
            if role == "admin" and normalized_expires_at is None and not normalized_enabled and available_permanent_admin_count(conn) < 1:
                raise ValueError("last_permanent_admin_required")
            conn.execute(
                "INSERT INTO users(username,password_hash,role,created_at,must_change_password,expires_at,enabled,alas_visible) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (
                    username,
                    hash_password(password),
                    role,
                    time.strftime("%Y-%m-%d %H:%M:%S"),
                    int(final_must_change_password),
                    normalized_expires_at,
                    int(normalized_enabled),
                    int(normalized_alas_visible),
                ),
            )
        conn.commit()


def revoke_expired_access(now: int | None = None) -> set[str]:
    """回收**停用**账户的会话，并返回所有不可用账户（含仅到期）供上层收尾。

    到期不等于封号：账户保留登录态与全部授权（续期后原样恢复），投屏与 ALAS 由
    功能闸门按 ``user_is_active`` 拒绝。返回值仍包含到期账户，调用方据此停止其
    ALAS 配置、断开其长连接并记账；只有 ``enabled=0`` 才会删会话。
    """
    current = now_ts() if now is None else int(now)
    with db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        usernames = {
            str(row["username"])
            for row in conn.execute(
                "SELECT username FROM users WHERE enabled=0 OR (expires_at IS NOT NULL AND expires_at<=?)",
                (current,),
            ).fetchall()
        }
        conn.execute("DELETE FROM sessions WHERE username IN (SELECT username FROM users WHERE enabled=0)")
        conn.execute(
            "DELETE FROM control_locks WHERE username IN "
            "(SELECT username FROM users WHERE enabled=0 OR (expires_at IS NOT NULL AND expires_at<=?))",
            (current,),
        )
        conn.commit()
    return usernames


def change_user_password(username: str, current_password: str, new_password: str) -> None:
    username = (username or "").strip()
    user = get_user(username)
    if not user:
        raise ValueError("invalid_user")
    if not verify_password(current_password or "", user["password_hash"]):
        raise ValueError("current_password_invalid")
    error = validate_password(new_password or "", username)
    if error:
        raise ValueError(error)
    if verify_password(new_password, user["password_hash"]):
        raise ValueError("new_password_must_be_different")
    with db_connect() as conn:
        conn.execute(
            "UPDATE users SET password_hash=?, must_change_password=0 WHERE username=?",
            (hash_password(new_password), username),
        )
        conn.commit()


def delete_user(username: str) -> None:
    username = (username or "").strip()
    with db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        user = conn.execute("SELECT role,expires_at,enabled FROM users WHERE username=?", (username,)).fetchone()
        if user and user["role"] == "admin" and admin_count(conn) <= 1:
            raise ValueError("last_admin_required")
        if user and user["role"] == "admin" and user["expires_at"] is None and bool(user["enabled"]) and available_permanent_admin_count(conn) <= 1:
            raise ValueError("last_permanent_admin_required")
        conn.execute("DELETE FROM users WHERE username=?", (username,))
        conn.execute("DELETE FROM sessions WHERE username=?", (username,))
        conn.execute("DELETE FROM control_locks WHERE username=?", (username,))
        conn.commit()


def list_devices_for_user(username: str, is_admin: bool = False) -> list[dict]:
    with db_connect() as conn:
        if is_admin:
            rows = conn.execute("SELECT d.*, 1 AS can_view, 1 AS can_control FROM devices d ORDER BY d.id").fetchall()
        else:
            rows = conn.execute(
                """
                SELECT d.*, ud.can_view, ud.can_control
                FROM devices d JOIN user_devices ud ON d.id=ud.device_id
                WHERE ud.username=? AND ud.can_view=1 AND d.enabled=1
                ORDER BY d.id
                """,
                (username,),
            ).fetchall()
    return [dict(row) for row in rows]


def get_user_video_preference(username: str) -> dict | None:
    with db_connect() as conn:
        row = conn.execute("SELECT * FROM user_video_preferences WHERE username=?", (username,)).fetchone()
    if not row:
        return None
    return {
        "profile": row["profile"],
        "adaptive": bool(row["adaptive"]),
        "video_bit_rate": int(row["video_bit_rate"]),
        "max_size": int(row["max_size"]),
        "max_fps": int(row["max_fps"]),
        "scrcpy_stream_mode": row["scrcpy_stream_mode"] or "raw",
        "updated_at": int(row["updated_at"]),
    }


def set_user_video_preference(username: str, options: dict) -> None:
    with db_connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO user_video_preferences
            (username,profile,adaptive,video_bit_rate,max_size,max_fps,scrcpy_stream_mode,updated_at)
            VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                username,
                str(options.get("profile", "custom")),
                1 if options.get("adaptive") else 0,
                int(options["video_bit_rate"]),
                int(options["max_size"]),
                int(options["max_fps"]),
                str(options.get("scrcpy_stream_mode") or "raw"),
                now_ts(),
            ),
        )
        conn.commit()


def get_user_alas_preference(username: str) -> dict:
    """Per-user ALAS automation preference (currently only the exit guard)."""
    with db_connect() as conn:
        row = conn.execute(
            "SELECT restart_on_exit,updated_at FROM user_alas_preferences WHERE username=?",
            (username,),
        ).fetchone()
    if not row:
        return {"restart_on_exit": False, "updated_at": 0}
    return {"restart_on_exit": bool(row["restart_on_exit"]), "updated_at": int(row["updated_at"] or 0)}


def set_user_alas_restart_on_exit(username: str, enabled: bool) -> dict:
    with db_connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO user_alas_preferences (username,restart_on_exit,updated_at) VALUES(?,?,?)",
            (username, 1 if enabled else 0, now_ts()),
        )
        conn.commit()
    return get_user_alas_preference(username)


# _alas_binding_payload 的实际实现在 app/storage_alas.py：模块底部统一重绑该名字
# （早先这里还留着一份与重绑结果完全相同的副本，永远走不到，2026-09-11 审计删除）。


# ALAS 归属判定（谁能拥有一个配置名）只有一处实现，在 app/storage_alas.py：
# config_name_key() 负责大小写折叠，binding_row_for_config()/config_owner() 按折叠后的
# config_key 取行，require_config_available() 既校验占用又把属主自己的行交给调用方。
# （这里原有 _alas_config_owner/_require_alas_config_available 两份逐字重复的副本，
# 2026-09-12 随大小写不敏感归属一起删除。）


def upsert_user_alas_binding(
    username: str,
    config_name: str,
    can_run: bool,
    can_edit: bool,
    is_default: bool | None = None,
    device_id: str | None | object = ALAS_DEVICE_UNCHANGED,
) -> None:
    username = (username or "").strip()
    config_name = (config_name or "").strip()
    if not username:
        raise ValueError("invalid_username")
    if not config_name:
        raise ValueError("invalid_config_name")
    with db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if not conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                raise ValueError("invalid_username")
            # 既校验占用，又拿到属主自己的那一行（大小写不敏感）。
            existing = storage_alas.require_config_available(conn, username, config_name)
            if device_id is ALAS_DEVICE_UNCHANGED:
                selected_device_id = existing["device_id"] if existing else None
            else:
                selected_device_id = (str(device_id or "").strip() or None)
                if selected_device_id and not conn.execute(
                    "SELECT 1 FROM devices WHERE id=?",
                    (selected_device_id,),
                ).fetchone():
                    raise ValueError("invalid_device")
            count = conn.execute("SELECT COUNT(*) FROM user_alas_configs WHERE username=?", (username,)).fetchone()[0]
            default_value = bool(existing and existing["is_default"]) if is_default is None else bool(is_default)
            if count == 0:
                default_value = True
            if default_value:
                conn.execute("UPDATE user_alas_configs SET is_default=0 WHERE username=?", (username,))
            values = (
                config_name,
                selected_device_id,
                1 if can_run else 0,
                1 if can_edit else 0,
                1 if default_value else 0,
                now_ts(),
            )
            if existing:
                # 同一属主重新提交（可能只差大小写）：改这一行，并把拼写更新成最新提交的
                # 名字 —— 否则管理员发现自己写错大小写时无法纠正。
                conn.execute(
                    "UPDATE user_alas_configs SET config_name=?,device_id=?,can_run=?,can_edit=?,"
                    "is_default=?,updated_at=? WHERE config_key=?",
                    (*values, storage_alas.config_name_key(config_name)),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO user_alas_configs
                        (username,config_name,config_key,device_id,can_run,can_edit,is_default,updated_at)
                    VALUES(?,?,?,?,?,?,?,?)
                    """,
                    (username, values[0], storage_alas.config_name_key(config_name), *values[1:]),
                )
            _ensure_user_alas_default(conn, username)
        except sqlite3.IntegrityError as exc:
            owner = storage_alas.config_owner(conn, config_name)
            conn.rollback()
            if owner is not None and owner != username:
                raise AlasConfigOwnershipError(config_name, owner) from exc
            raise ValueError("invalid_alas_binding") from exc
        except Exception:
            conn.rollback()
            raise
        conn.commit()


def upsert_user_alas_binding_with_view(
    username: str,
    config_name: str,
    can_run: bool,
    can_edit: bool,
    is_default: bool | None = None,
    device_id: str | None = None,
    grant_view: bool = False,
) -> None:
    """Atomically bind an ALAS config and, when requested, grant device view access."""
    username = (username or "").strip()
    config_name = (config_name or "").strip()
    device_id = (str(device_id or "").strip() or None)
    if not username:
        raise ValueError("invalid_username")
    if not config_name:
        raise ValueError("invalid_config_name")
    with db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            user = conn.execute("SELECT role FROM users WHERE username=?", (username,)).fetchone()
            if not user:
                raise ValueError("invalid_username")
            existing = storage_alas.require_config_available(conn, username, config_name)
            if device_id and not conn.execute("SELECT 1 FROM devices WHERE id=?", (device_id,)).fetchone():
                raise ValueError("invalid_device")
            if device_id and user["role"] != "admin":
                permission = conn.execute(
                    "SELECT can_view,can_control FROM user_devices WHERE username=? AND device_id=?",
                    (username, device_id),
                ).fetchone()
                if not permission or not permission["can_view"]:
                    if not grant_view:
                        raise ValueError("device_view_permission_required")
                    # 只有「由 ALAS 新建」的行才标记来源为 alas；已有的手动授权
                    # 只补 can_view，来源保持不变。
                    conn.execute(
                        "INSERT INTO user_devices(username,device_id,can_view,can_control,source) VALUES(?,?,1,?, 'alas') "
                        "ON CONFLICT(username,device_id) DO UPDATE SET can_view=1",
                        (username, device_id, 1 if permission and permission["can_control"] else 0),
                    )
            previous_device_id = str((existing["device_id"] if existing else "") or "").strip()
            count = conn.execute("SELECT COUNT(*) FROM user_alas_configs WHERE username=?", (username,)).fetchone()[0]
            default_value = bool(existing and existing["is_default"]) if is_default is None else bool(is_default)
            if count == 0:
                default_value = True
            if default_value:
                conn.execute("UPDATE user_alas_configs SET is_default=0 WHERE username=?", (username,))
            values = (
                config_name,
                device_id,
                1 if can_run else 0,
                1 if can_edit else 0,
                1 if default_value else 0,
                now_ts(),
            )
            if existing:
                # 同一属主重新提交（可能只差大小写）：改这一行，并把拼写更新成最新提交的
                # 名字 —— 否则管理员发现自己写错大小写时无法纠正。
                conn.execute(
                    "UPDATE user_alas_configs SET config_name=?,device_id=?,can_run=?,can_edit=?,"
                    "is_default=?,updated_at=? WHERE config_key=?",
                    (*values, storage_alas.config_name_key(config_name)),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO user_alas_configs
                        (username,config_name,config_key,device_id,can_run,can_edit,is_default,updated_at)
                    VALUES(?,?,?,?,?,?,?,?)
                    """,
                    (username, values[0], storage_alas.config_name_key(config_name), *values[1:]),
                )
            _ensure_user_alas_default(conn, username)
            if previous_device_id and previous_device_id != str(device_id or "").strip():
                _reclaim_alas_device_views(conn, username, [previous_device_id])
        except sqlite3.IntegrityError as exc:
            owner = storage_alas.config_owner(conn, config_name)
            conn.rollback()
            if owner is not None and owner != username:
                raise AlasConfigOwnershipError(config_name, owner) from exc
            raise ValueError("invalid_alas_binding") from exc
        except Exception:
            conn.rollback()
            raise
        conn.commit()


def _reclaim_alas_device_views(conn: sqlite3.Connection, username: str, device_ids: list[str]) -> int:
    """回收「仅由 ALAS 顺带授予」的设备查看权。

    `grant_view=True` 会给用户写一条 ``user_devices``（来源标记 ``alas``）。解除绑定、
    或者把配置移到别的设备之后，这条授权如果已经没有绑定支撑就该消失 —— 否则用户在
    ALAS 权限页看不到它，却仍然能观看该设备。管理员手动授予的行（``manual``）不动。
    """
    removed = 0
    for device_id in {str(item or "").strip() for item in device_ids} - {""}:
        cursor = conn.execute(
            """
            DELETE FROM user_devices
            WHERE username=? AND device_id=? AND source='alas'
              AND NOT EXISTS (
                  SELECT 1 FROM user_alas_configs
                  WHERE username=user_devices.username AND device_id=user_devices.device_id
              )
            """,
            (username, device_id),
        )
        removed += int(cursor.rowcount or 0)
    return removed


def set_default_user_alas_config(username: str, config_name: str) -> None:
    username = (username or "").strip()
    config_name = (config_name or "").strip()
    with db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if not conn.execute(
                "SELECT 1 FROM user_alas_configs WHERE username=? AND config_key=?",
                (username, storage_alas.config_name_key(config_name)),
            ).fetchone():
                raise ValueError("invalid_alas_binding")
            conn.execute("UPDATE user_alas_configs SET is_default=0 WHERE username=?", (username,))
            updated = conn.execute(
                "UPDATE user_alas_configs SET is_default=1,updated_at=? WHERE username=? AND config_key=?",
                (now_ts(), username, storage_alas.config_name_key(config_name)),
            )
            if updated.rowcount != 1:
                raise ValueError("invalid_alas_binding")
            _ensure_user_alas_default(conn, username)
        except Exception:
            conn.rollback()
            raise
        conn.commit()


def delete_user_alas_binding(username: str, config_name: str) -> None:
    username = (username or "").strip()
    config_name = (config_name or "").strip()
    with db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            device_ids = [
                str(row["device_id"] or "")
                for row in conn.execute(
                    "SELECT device_id FROM user_alas_configs WHERE username=? AND config_key=?",
                    (username, storage_alas.config_name_key(config_name)),
                ).fetchall()
            ]
            conn.execute(
                "DELETE FROM user_alas_configs WHERE username=? AND config_key=?",
                (username, storage_alas.config_name_key(config_name)),
            )
            _ensure_user_alas_default(conn, username)
            _reclaim_alas_device_views(conn, username, device_ids)
        except Exception:
            conn.rollback()
            raise
        conn.commit()


def set_user_alas_config(
    username: str,
    config_name: str,
    can_run: bool,
    can_edit: bool,
    device_id: str | None = None,
) -> None:
    username = (username or "").strip()
    config_name = (config_name or "").strip()
    if not username:
        raise ValueError("invalid_username")
    if not config_name:
        raise ValueError("invalid_config_name")
    with db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if not conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                raise ValueError("invalid_username")
            storage_alas.require_config_available(conn, username, config_name)
            device_id = (device_id or "").strip() or None
            if device_id and not conn.execute("SELECT 1 FROM devices WHERE id=?", (device_id,)).fetchone():
                raise ValueError("invalid_device")
            previous_device_ids = [
                str(row["device_id"] or "")
                for row in conn.execute(
                    "SELECT device_id FROM user_alas_configs WHERE username=?",
                    (username,),
                ).fetchall()
            ]
            conn.execute("DELETE FROM user_alas_configs WHERE username=?", (username,))
            conn.execute(
                """
                INSERT INTO user_alas_configs
                    (username,config_name,config_key,device_id,can_run,can_edit,is_default,updated_at)
                VALUES(?,?,?,?,?,?,1,?)
                """,
                (
                    username,
                    config_name,
                    storage_alas.config_name_key(config_name),
                    device_id,
                    1 if can_run else 0,
                    1 if can_edit else 0,
                    now_ts(),
                ),
            )
            _reclaim_alas_device_views(conn, username, previous_device_ids)
        except sqlite3.IntegrityError as exc:
            owner = storage_alas.config_owner(conn, config_name)
            conn.rollback()
            if owner is not None and owner != username:
                raise AlasConfigOwnershipError(config_name, owner) from exc
            raise ValueError("invalid_alas_binding") from exc
        except Exception:
            conn.rollback()
            raise
        conn.commit()


def delete_user_alas_config(username: str) -> None:
    username = (username or "").strip()
    with db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            device_ids = [
                str(row["device_id"] or "")
                for row in conn.execute(
                    "SELECT device_id FROM user_alas_configs WHERE username=?",
                    (username,),
                ).fetchall()
            ]
            conn.execute("DELETE FROM user_alas_configs WHERE username=?", (username,))
            _reclaim_alas_device_views(conn, username, device_ids)
        except Exception:
            conn.rollback()
            raise
        conn.commit()


def list_all_devices() -> list[dict]:
    with db_connect() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM devices ORDER BY id")]


def generate_device_id() -> str:
    """Return an opaque device ID candidate.

    Product code should use ``create_device`` so collision handling and the
    insert happen in one transaction.
    """
    return f"device_{secrets.token_hex(8)}"


def _grant_device_to_admins(conn: sqlite3.Connection, device_id: str) -> None:
    admins = conn.execute("SELECT username FROM users WHERE role='admin'").fetchall()
    for row in admins:
        conn.execute(
            "INSERT OR IGNORE INTO user_devices(username,device_id,can_view,can_control) VALUES(?,?,1,1)",
            (row["username"], device_id),
        )


def create_device(name: str, address: str, enabled: bool = True) -> str:
    """Create a device with an opaque ID without ever overwriting an existing row."""
    created_at = time.strftime("%Y-%m-%d %H:%M:%S")
    with db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        for _ in range(10):
            device_id = generate_device_id()
            try:
                conn.execute(
                    "INSERT INTO devices(id,name,address,enabled,created_at) VALUES(?,?,?,?,?)",
                    (device_id, name or address or device_id, address, 1 if enabled else 0, created_at),
                )
            except sqlite3.IntegrityError:
                continue
            _grant_device_to_admins(conn, device_id)
            conn.commit()
            return device_id
        conn.rollback()
    raise RuntimeError("unable to allocate a unique device id")


def update_device(device_id: str, name: str, address: str, enabled: bool = True) -> bool:
    """Update an existing device while preserving its identity and permissions."""
    with db_connect() as conn:
        cursor = conn.execute(
            "UPDATE devices SET name=?, address=?, enabled=? WHERE id=?",
            (name or address or device_id, address, 1 if enabled else 0, device_id),
        )
        conn.commit()
        return cursor.rowcount == 1


def upsert_device(device_id: str, name: str, address: str, enabled: bool = True) -> None:
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO devices(id,name,address,enabled,created_at) VALUES(?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
                address=excluded.address,
                enabled=excluded.enabled
            """,
            (device_id, name or address or device_id, address or device_id, 1 if enabled else 0, time.strftime("%Y-%m-%d %H:%M:%S")),
        )
        _grant_device_to_admins(conn, device_id)
        conn.commit()


def delete_device(device_id: str) -> bool:
    with db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM control_locks WHERE device_id=?", (device_id,))
        cursor = conn.execute("DELETE FROM devices WHERE id=?", (device_id,))
        conn.commit()
        return cursor.rowcount == 1


def get_device(device_id: str):
    with db_connect() as conn:
        return conn.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone()


def user_can(username: str, device_id: str, action: str) -> bool:
    col = "can_control" if action == "control" else "can_view"
    current = now_ts()
    with db_connect() as conn:
        user = conn.execute("SELECT role,expires_at,enabled FROM users WHERE username=?", (username,)).fetchone()
        if not user:
            return False
        if not user_is_active(user, current):
            return False
        if user["role"] == "admin":
            row = conn.execute("SELECT enabled FROM devices WHERE id=?", (device_id,)).fetchone()
            return bool(row and row["enabled"])
        row = conn.execute(
            f"""
            SELECT ud.{col}
            FROM user_devices ud
            JOIN devices d ON d.id=ud.device_id
            WHERE ud.username=? AND ud.device_id=? AND d.enabled=1
            """,
            (username, device_id),
        ).fetchone()
    return bool(row and row[col])


def list_permissions() -> list[dict]:
    with db_connect() as conn:
        rows = conn.execute("SELECT username,device_id,can_view,can_control FROM user_devices ORDER BY username,device_id").fetchall()
    return [dict(row) for row in rows]


def set_permission(username: str, device_id: str, can_view: bool, can_control: bool) -> None:
    username = (username or "").strip()
    device_id = (device_id or "").strip()
    if not username or not get_user(username):
        raise ValueError("invalid_username")
    if not device_id or not get_device(device_id):
        raise ValueError("invalid_device")
    # Control access can never outlive view access.  Treat an all-disabled
    # update as removal so the UI can create permissions for devices that did
    # not previously have a row and clean them up again without a second API.
    can_view = bool(can_view)
    can_control = bool(can_control and can_view)
    with db_connect() as conn:
        if not can_view and not can_control:
            conn.execute(
                "DELETE FROM user_devices WHERE username=? AND device_id=?",
                (username, device_id),
            )
        else:
            # 管理员显式授予 = 手动来源，之后解除 ALAS 绑定不会再回收它。
            conn.execute(
                "INSERT OR REPLACE INTO user_devices(username,device_id,can_view,can_control,source) VALUES(?,?,?,?, 'manual')",
                (username, device_id, 1 if can_view else 0, 1 if can_control else 0),
            )
        conn.commit()


def _permission_bool(value, default=False):
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _normalize_device_permissions(conn, device_permissions: list[dict]) -> dict[str, tuple[bool, bool]]:
    """Validate every device row before the matrix is replaced."""
    normalized: dict[str, tuple[bool, bool]] = {}
    for item in device_permissions:
        if not isinstance(item, dict):
            raise ValueError("invalid_device_permission")
        raw_id = str(item.get("device_id") or item.get("deviceId") or "").strip()
        if not raw_id:
            continue
        row = conn.execute("SELECT id FROM devices WHERE id=?", (raw_id,)).fetchone()
        if not row:
            raise ValueError("invalid_device")
        can_view = _permission_bool(item.get("can_view", item.get("canView")), False)
        can_control = _permission_bool(item.get("can_control", item.get("canControl")), False) and can_view
        if can_view or can_control:
            normalized[raw_id] = (can_view, can_control)
    return normalized


def _normalize_alas_bindings(
    conn,
    username: str,
    role: str,
    alas_assignments: list[dict],
    normalized_devices: dict[str, tuple[bool, bool]],
) -> list[tuple[str, str | None, bool, bool, bool, bool]]:
    """Validate ALAS rows and grant the view permission they imply."""
    normalized: list[tuple[str, str | None, bool, bool, bool, bool]] = []
    seen_configs: set[str] = set()
    for item in alas_assignments:
        if not isinstance(item, dict):
            raise ValueError("invalid_alas_binding")
        config_name = str(item.get("config_name") or item.get("configName") or "").strip()
        if not config_name or len(config_name) > 128 or any(ch.isspace() for ch in config_name):
            raise ValueError("invalid_config_name")
        # 重复检测按归属键（大小写不敏感）：只差大小写的两行是同一个配置，
        # 否则第二行会撞上唯一索引、把一次正常的整体保存变成 500。
        config_key = storage_alas.config_name_key(config_name)
        if config_key in seen_configs:
            raise ValueError("duplicate_alas_binding")
        seen_configs.add(config_key)
        if item.get("enabled") is False:
            continue
        storage_alas.require_config_available(conn, username, config_name)
        raw_device = str(item.get("device_id") or item.get("deviceId") or "").strip()
        device_id = raw_device or None
        if device_id and not conn.execute("SELECT 1 FROM devices WHERE id=?", (device_id,)).fetchone():
            raise ValueError("invalid_device")
        can_run = _permission_bool(item.get("can_run", item.get("canRun")), True)
        can_edit = _permission_bool(item.get("can_edit", item.get("canEdit")), False)
        grant_view = _permission_bool(item.get("grant_view", item.get("grantView")), False)
        if device_id and role != "admin" and not normalized_devices.get(device_id, (False, False))[0]:
            if not grant_view:
                raise ValueError("device_view_permission_required")
            normalized_devices[device_id] = (True, normalized_devices.get(device_id, (False, False))[1])
        normalized.append(
            (
                config_name,
                device_id,
                can_run,
                can_edit,
                _permission_bool(item.get("is_default", item.get("isDefault")), False),
                grant_view,
            )
        )
    return normalized


def replace_user_permissions(
    username: str,
    device_permissions: list[dict] | None,
    alas_assignments: list[dict] | None,
) -> dict:
    """Replace a user's device and ALAS grants in one SQLite transaction.

    ``alas_assignments=None`` 表示「不涉及 ALAS 归属」：只替换设备权限，
    现有绑定原样保留。显式传 ``[]`` 仍然表示清空（管理页矩阵的语义不变），
    这样只发设备字段的调用方不会静默删掉用户的 ALAS 授权。
    """
    username = str(username or "").strip()
    device_permissions = device_permissions if isinstance(device_permissions, list) else []
    replace_alas = isinstance(alas_assignments, list)
    if not isinstance(alas_assignments, list):
        alas_assignments = []
    if not username:
        raise ValueError("invalid_username")
    with db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        user = conn.execute("SELECT role FROM users WHERE username=?", (username,)).fetchone()
        if not user:
            raise ValueError("invalid_username")
        try:
            # Validate and normalize every device first so a malformed row
            # cannot leave a partially replaced permission matrix.
            normalized_devices = _normalize_device_permissions(conn, device_permissions)
            normalized_alas = (
                _normalize_alas_bindings(conn, username, str(user["role"]), alas_assignments, normalized_devices)
                if replace_alas
                else None
            )

            conn.execute("DELETE FROM user_devices WHERE username=?", (username,))
            if user["role"] != "admin":
                conn.executemany(
                    "INSERT INTO user_devices(username,device_id,can_view,can_control) VALUES(?,?,?,?)",
                    [(username, device_id, int(view), int(control)) for device_id, (view, control) in normalized_devices.items()],
                )
            if replace_alas:
                conn.execute("DELETE FROM user_alas_configs WHERE username=?", (username,))
                if normalized_alas:
                    defaults = [row for row in normalized_alas if row[4]]
                    default_name = defaults[0][0] if defaults else normalized_alas[0][0]
                    default_key = storage_alas.config_name_key(default_name)
                    conn.executemany(
                        "INSERT INTO user_alas_configs(username,config_name,config_key,device_id,can_run,can_edit,is_default,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                        [
                            (
                                username,
                                config_name,
                                storage_alas.config_name_key(config_name),
                                device_id,
                                int(can_run),
                                int(can_edit),
                                int(storage_alas.config_name_key(config_name) == default_key),
                                now_ts(),
                            )
                            for config_name, device_id, can_run, can_edit, _is_default, _grant_view in normalized_alas
                        ],
                    )
            conn.commit()
            return {
                "username": username,
                "device_permissions": len(normalized_devices) if user["role"] != "admin" else 0,
                "alas_assignments": len(normalized_alas) if replace_alas else len(_binding_rows(conn, username)),
                "alas_assignments_replaced": replace_alas,
            }
        except Exception:
            conn.rollback()
            raise


def _binding_rows(conn: sqlite3.Connection, username: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT 1 FROM user_alas_configs WHERE username=?",
        (username,),
    ).fetchall()


SESSION_TOKEN_HASH_MARKER = "session_token_hash_v1"

# 会话令牌在库中的存储形态（实现见 storage_core，连接注册表也用同一份）。
session_token_hash = storage_core.session_token_hash

# 单账户并发登录会话上限；0 = 不限制。超出的按创建时间从最旧开始淘汰，
# 只在登录时计算一次，不产生任何每请求开销。
MAX_SESSIONS_PER_USER = max(0, int(os.environ.get("MAX_SESSIONS_PER_USER", "5") or "5"))

# 会话列表里最多展示多少行（纯展示上限，避免管理员页面被大量会话拖慢）。
SESSION_LIST_LIMIT = max(1, min(500, int(os.environ.get("SESSION_LIST_LIMIT", "200") or "200")))

# 会话行里记录的客户端信息长度上限（只用于展示，避免恶意头把行撑大）。
SESSION_CLIENT_IP_MAX_LENGTH = 64
SESSION_USER_AGENT_MAX_LENGTH = 200


def migrate_session_token_storage(conn: sqlite3.Connection) -> int:
    """一次性迁移：丢掉历史上以明文存储的会话，并释放其残留页。

    会话重建成本极低（重新登录），且旧行在哈希化之后本来也匹配不上；
    这里显式清空并做一次 VACUUM，把明文令牌从空闲页里抹掉。
    """
    row = conn.execute("SELECT value FROM settings WHERE key=?", (SESSION_TOKEN_HASH_MARKER,)).fetchone()
    if row is not None:
        return 0
    dropped = 0
    try:
        dropped = int(conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] or 0)
    except sqlite3.Error:
        dropped = 0
    conn.execute("DELETE FROM sessions")
    conn.execute(
        "INSERT OR REPLACE INTO settings(key, value) VALUES(?, ?)",
        (SESSION_TOKEN_HASH_MARKER, "1"),
    )
    conn.commit()
    try:
        conn.execute("VACUUM")
    except sqlite3.Error as exc:  # 迁移失败不影响可用性，只是残留页未立即回收
        AUDIT_LOGGER.warning("SESSION_TOKEN_PURGE_VACUUM_FAILED error=%s", exc.__class__.__name__)
    if dropped:
        AUDIT_LOGGER.info("SESSION_TOKEN_HASH_MIGRATION dropped_plaintext_sessions=%s", dropped)
    return dropped


def _session_payload(row, effective_expires_at: int, sid: str | None = None) -> dict:
    return {
        # 库里的 sid 是哈希；对外始终回显调用方传入的原始令牌（写 Cookie 用）。
        "sid": sid if sid is not None else row["sid"],
        "username": row["username"],
        "csrf_token": row["csrf_token"],
        "created_at": row["created_at"],
        "expires_at": effective_expires_at,
        "idle_expires_at": int(row["idle_expires_at"] or row["expires_at"]),
        "absolute_expires_at": int(row["absolute_expires_at"] or row["created_at"] + SESSION_ABSOLUTE_SECONDS),
        "last_seen_at": int(row["last_seen_at"] or row["created_at"] or 0) if "last_seen_at" in row.keys() else 0,
    }


def create_session(
    username: str,
    ttl_seconds: int = SESSION_IDLE_SECONDS,
    *,
    client_ip: str | None = None,
    user_agent: str | None = None,
    device_id: str | None = None,
) -> dict:
    """Create a session with a bounded idle lifetime and a fixed absolute cap.

    ``client_ip`` / ``user_agent`` / ``device_id`` are recorded for the session list only;
    they are truncated so a hostile header cannot bloat the row.  When
    ``MAX_SESSIONS_PER_USER`` is set, the oldest sessions beyond the cap are dropped in
    the same transaction (login-time only, no per-request cost).
    """
    try:
        requested_idle_seconds = int(ttl_seconds)
    except (TypeError, ValueError, OverflowError):
        requested_idle_seconds = SESSION_IDLE_SECONDS
    requested_idle_seconds = max(1, min(SESSION_IDLE_SECONDS, requested_idle_seconds))
    sid = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    ts = now_ts()
    idle_expires_at = ts + requested_idle_seconds
    absolute_expires_at = ts + SESSION_ABSOLUTE_SECONDS
    expires_at = min(idle_expires_at, absolute_expires_at)
    recorded_ip = str(client_ip or "").strip()[:SESSION_CLIENT_IP_MAX_LENGTH] or None
    recorded_agent = str(user_agent or "").strip()[:SESSION_USER_AGENT_MAX_LENGTH] or None
    recorded_device = str(device_id or "").strip()[:64] or None
    with db_connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO sessions(
                sid,username,csrf_token,created_at,expires_at,idle_expires_at,absolute_expires_at,
                last_seen_at,client_ip,user_agent,device_id
            )
            SELECT ?,?,?,?,?,?,?,?,?,?,?
            FROM users
            WHERE username=? AND enabled=1
            """,
            (
                session_token_hash(sid),
                username,
                csrf,
                ts,
                expires_at,
                idle_expires_at,
                absolute_expires_at,
                ts,
                recorded_ip,
                recorded_agent,
                recorded_device,
                username,
            ),
        )
        if cursor.rowcount != 1:
            conn.rollback()
            # 只有「账号不存在」或管理员显式停用会走到这里；到期账户允许登录。
            raise ValueError("account_expired_or_missing")
        if MAX_SESSIONS_PER_USER:
            # 用 rowid 而不是 sid 作为次序兜底：同一秒内登录的多个会话 created_at 相同，
            # 若按哈希排序会变成随机淘汰（实测踩到），rowid 才是真正的插入顺序。
            conn.execute(
                """
                DELETE FROM sessions
                WHERE username=? AND rowid NOT IN (
                    SELECT rowid FROM sessions WHERE username=? ORDER BY created_at DESC, rowid DESC LIMIT ?
                )
                """,
                (username, username, MAX_SESSIONS_PER_USER),
            )
        conn.commit()
    return {
        "sid": sid,
        "csrf_token": csrf,
        "username": username,
        "created_at": ts,
        "expires_at": expires_at,
        "idle_expires_at": idle_expires_at,
        "absolute_expires_at": absolute_expires_at,
        "last_seen_at": ts,
    }


def list_login_sessions(username: str | None = None, current_sid: str | None = None) -> list[dict]:
    """登录会话清单（管理员看全部，普通用户看自己）。

    对外暴露的 ``id`` 是库中的哈希，不是令牌：它无法用来登录（服务端会把提交值
    再哈希一次），只是一个可安全展示与引用的会话标识。
    """
    current_hash = session_token_hash(current_sid) if current_sid else ""
    params: list[object] = []
    where = ""
    if username:
        where = "WHERE s.username=?"
        params.append(username)
    with db_connect() as conn:
        rows = conn.execute(
            f"""
            SELECT s.sid, s.username, s.created_at, s.last_seen_at, s.expires_at,
                   s.idle_expires_at, s.absolute_expires_at, s.client_ip, s.user_agent, s.device_id,
                   u.role AS account_role, u.enabled AS account_enabled
            FROM sessions s
            LEFT JOIN users u ON u.username=s.username
            {where}
            ORDER BY s.created_at DESC, s.rowid DESC
            LIMIT ?
            """,
            (*params, SESSION_LIST_LIMIT),
        ).fetchall()
    return [
        {
            "id": str(row["sid"]),
            "username": str(row["username"] or ""),
            "role": str(row["account_role"] or ""),
            "account_enabled": bool(row["account_enabled"]) if row["account_enabled"] is not None else False,
            "created_at": int(row["created_at"] or 0),
            "last_seen_at": int(row["last_seen_at"] or row["created_at"] or 0),
            "expires_at": int(row["expires_at"] or 0),
            "idle_expires_at": int(row["idle_expires_at"] or row["expires_at"] or 0),
            "absolute_expires_at": int(row["absolute_expires_at"] or 0),
            "client_ip": str(row["client_ip"] or ""),
            "user_agent": str(row["user_agent"] or ""),
            # 浏览器侧稳定设备标识：安全页据此把同一台设备的多条会话归并成一行。
            "device_id": str(row["device_id"] or ""),
            "current": bool(current_hash) and str(row["sid"]) == current_hash,
        }
        for row in rows
    ]


def revoke_session_by_hash(session_hash: str, *, username: str | None = None) -> str | None:
    """按会话标识（哈希）删除一条登录会话，返回被删会话所属用户名。

    ``username`` 非空时只允许删除该用户自己的会话（普通用户自助踢出用）。
    调用方负责把该用户的实时连接（WebSocket）一并关掉。
    """
    session_hash = str(session_hash or "").strip()
    if not session_hash:
        return None
    with db_connect() as conn:
        if username:
            row = conn.execute(
                "SELECT username FROM sessions WHERE sid=? AND username=?",
                (session_hash, username),
            ).fetchone()
        else:
            row = conn.execute("SELECT username FROM sessions WHERE sid=?", (session_hash,)).fetchone()
        if not row:
            return None
        conn.execute("DELETE FROM sessions WHERE sid=?", (session_hash,))
        conn.commit()
        return str(row["username"] or "")



def get_session(sid: str | None) -> dict | None:
    if not sid:
        return None
    current = now_ts()
    sid_hash = session_token_hash(sid)
    with db_connect() as conn:
        row = conn.execute(
            """
            SELECT s.*, u.username AS account_username, u.expires_at AS account_expires_at,
                   u.enabled AS account_enabled
            FROM sessions s
            LEFT JOIN users u ON u.username=s.username
            WHERE s.sid=?
            """,
            (sid_hash,),
        ).fetchone()
        if not row:
            return None
        idle_expires_at = int(row["idle_expires_at"] or row["expires_at"])
        absolute_expires_at = int(row["absolute_expires_at"] or row["created_at"] + SESSION_ABSOLUTE_SECONDS)
        effective_expires_at = min(int(row["expires_at"]), idle_expires_at, absolute_expires_at)
        if effective_expires_at <= current:
            conn.execute("DELETE FROM sessions WHERE sid=?", (sid_hash,))
            conn.commit()
            return None
        account_missing = row["account_username"] is None
        account_disabled = "account_enabled" in row.keys() and not bool(row["account_enabled"])
        # 到期不再使会话失效：账户保持登录态（能看到期提示、等待续期），投屏与 ALAS
        # 由各自的 user_is_active 闸门拒绝。只有停用/被删才回收会话。
        if account_missing or account_disabled:
            _revoke_user_access(conn, row["username"])
            conn.commit()
            return None
    return _session_payload(row, effective_expires_at, sid)


def describe_session(sid: str | None) -> dict:
    """只读诊断一个会话 cookie 为什么有效/无效（不删任何行）。

    `get_session()` 在校验失败时会顺手删掉会话并回收权限，因此调用方拿不到
    「是没登录还是账户到期」。WebSocket 鉴权需要区分这两种情况来决定关码与审计
    原因，所以这里单独提供一份不产生副作用的判断。
    """
    sid = str(sid or "").strip()
    if not sid:
        return {"state": "missing", "username": ""}
    current = now_ts()
    sid_hash = session_token_hash(sid)
    with db_connect() as conn:
        row = conn.execute(
            """
            SELECT s.*, u.username AS account_username, u.expires_at AS account_expires_at,
                   u.enabled AS account_enabled, u.must_change_password AS account_must_change_password
            FROM sessions s
            LEFT JOIN users u ON u.username=s.username
            WHERE s.sid=?
            """,
            (sid_hash,),
        ).fetchone()
        if not row:
            return {"state": "missing", "username": ""}
        username = str(row["account_username"] or row["username"] or "")
        if row["account_username"] is None:
            return {"state": "account_missing", "username": username}
        if row["account_expires_at"] is not None and int(row["account_expires_at"]) <= current:
            return {"state": "account_expired", "username": username}
        if "account_enabled" in row.keys() and not bool(row["account_enabled"]):
            return {"state": "account_disabled", "username": username}
        idle_expires_at = int(row["idle_expires_at"] or row["expires_at"])
        absolute_expires_at = int(row["absolute_expires_at"] or row["created_at"] + SESSION_ABSOLUTE_SECONDS)
        if min(int(row["expires_at"]), idle_expires_at, absolute_expires_at) <= current:
            return {"state": "session_expired", "username": username}
        if "account_must_change_password" in row.keys() and bool(row["account_must_change_password"]):
            return {"state": "password_change_required", "username": username}
    return {"state": "ok", "username": username}


def renew_session(sid: str | None) -> dict | None:
    """Renew only the idle deadline for a currently valid HTTP session."""
    if not sid:
        return None
    current = now_ts()
    sid_hash = session_token_hash(sid)
    with db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT s.*, u.username AS account_username, u.expires_at AS account_expires_at,
                   u.enabled AS account_enabled
            FROM sessions s
            LEFT JOIN users u ON u.username=s.username
            WHERE s.sid=?
            """,
            (sid_hash,),
        ).fetchone()
        if not row:
            conn.rollback()
            return None
        idle_expires_at = int(row["idle_expires_at"] or row["expires_at"])
        absolute_expires_at = int(row["absolute_expires_at"] or row["created_at"] + SESSION_ABSOLUTE_SECONDS)
        effective_expires_at = min(int(row["expires_at"]), idle_expires_at, absolute_expires_at)
        account_missing = row["account_username"] is None
        account_disabled = "account_enabled" in row.keys() and not bool(row["account_enabled"])
        # 与 get_session 同一套语义：到期账户的会话继续续期（不封号），停用/被删则回收。
        if effective_expires_at <= current or account_missing or account_disabled:
            if account_missing or account_disabled:
                _revoke_user_access(conn, row["username"])
            conn.execute("DELETE FROM sessions WHERE sid=?", (sid_hash,))
            conn.commit()
            return None
        next_idle_expires_at = min(current + SESSION_IDLE_SECONDS, absolute_expires_at)
        next_expires_at = min(next_idle_expires_at, absolute_expires_at)
        # last_seen_at 与续期同一条 UPDATE：不额外增加写放大。
        conn.execute(
            "UPDATE sessions SET idle_expires_at=?, expires_at=?, last_seen_at=? WHERE sid=?",
            (next_idle_expires_at, next_expires_at, current, sid_hash),
        )
        conn.commit()
        payload = dict(row)
        payload["idle_expires_at"] = next_idle_expires_at
        payload["expires_at"] = next_expires_at
        payload["absolute_expires_at"] = absolute_expires_at
    return _session_payload(payload, next_expires_at, sid)


def delete_session(sid: str | None) -> None:
    if not sid:
        return
    with db_connect() as conn:
        conn.execute("DELETE FROM sessions WHERE sid=?", (session_token_hash(sid),))
        conn.commit()


def delete_other_sessions(username: str, keep_sid: str | None) -> int:
    username = (username or "").strip()
    if not username:
        return 0
    with db_connect() as conn:
        if keep_sid:
            cur = conn.execute(
                "DELETE FROM sessions WHERE username=? AND sid<>?",
                (username, session_token_hash(keep_sid)),
            )
        else:
            cur = conn.execute("DELETE FROM sessions WHERE username=?", (username,))
        conn.commit()
        return int(cur.rowcount or 0)


# 固定哑哈希：用户不存在时也执行一次同等代价的校验，消除登录响应时间差带来的用户名枚举。
_DUMMY_PASSWORD_HASH = hash_password(secrets.token_hex(16))


def authenticate(username: str, password: str) -> dict | None:
    user = get_user(username)
    if not user:
        verify_password(password, _DUMMY_PASSWORD_HASH)
        return None
    if not verify_password(password, user["password_hash"]):
        return None
    # 到期账户仍可登录（付费服务可能续期/接入付款），投屏与 ALAS 由各自闸门拦截；
    # 只有管理员显式停用才不允许登录。
    if not user_login_allowed(user):
        return None
    if password_hash_needs_upgrade(user["password_hash"]):
        with db_connect() as conn:
            conn.execute("UPDATE users SET password_hash=? WHERE username=?", (hash_password(password), user["username"]))
            conn.commit()
    return dict(user)


def acquire_lock(device_id: str, username: str, client_id: str, force: bool = False, ttl_seconds: int = 90) -> dict:
    ts = now_ts()
    expires = ts + ttl_seconds
    with db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        account = conn.execute("SELECT expires_at,enabled FROM users WHERE username=?", (username,)).fetchone()
        if not user_is_active(account, ts):
            conn.execute("DELETE FROM control_locks WHERE username=?", (username,))
            conn.commit()
            return {"ok": False, "owner": "", "expires_at": 0, "error": "account_disabled" if account and "enabled" in account.keys() and not bool(account["enabled"]) else "account_expired"}
        current = conn.execute("SELECT * FROM control_locks WHERE device_id=?", (device_id,)).fetchone()
        if current and current["expires_at"] < ts:
            conn.execute("DELETE FROM control_locks WHERE device_id=?", (device_id,))
            current = None

        if current and not force:
            same_user = current["username"] == username
            same_client = same_user and current["client_id"] == client_id
            http_upgrade = same_user and current["client_id"] == "http" and client_id != "http"
            if not same_client and not http_upgrade:
                conn.commit()
                return {"ok": False, "owner": current["username"], "expires_at": current["expires_at"]}

            conn.execute(
                "UPDATE control_locks SET client_id=?, expires_at=? WHERE device_id=?",
                (client_id, expires, device_id),
            )
            conn.commit()
            return {"ok": True, "owner": username, "expires_at": expires}

        conn.execute("INSERT OR REPLACE INTO control_locks(device_id,username,client_id,acquired_at,expires_at) VALUES(?,?,?,?,?)", (device_id, username, client_id, ts, expires))
        conn.commit()
    return {"ok": True, "owner": username, "expires_at": expires}


def renew_lock(device_id: str, username: str, client_id: str, ttl_seconds: int = 90) -> bool:
    ts = now_ts()
    expires = ts + ttl_seconds
    with db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        account = conn.execute("SELECT expires_at,enabled FROM users WHERE username=?", (username,)).fetchone()
        if not user_is_active(account, ts):
            conn.execute("DELETE FROM control_locks WHERE username=?", (username,))
            conn.commit()
            return False
        row = conn.execute("SELECT * FROM control_locks WHERE device_id=?", (device_id,)).fetchone()
        if not row:
            conn.commit()
            return False
        if row["expires_at"] < ts:
            conn.execute("DELETE FROM control_locks WHERE device_id=?", (device_id,))
            conn.commit()
            return False
        if row["username"] != username or row["client_id"] != client_id:
            conn.commit()
            return False
        conn.execute("UPDATE control_locks SET expires_at=? WHERE device_id=?", (expires, device_id))
        conn.commit()
    return True


def release_lock(device_id: str, username: str, force: bool = False, client_id: str | None = None) -> bool:
    ts = now_ts()
    with db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM control_locks WHERE device_id=?", (device_id,)).fetchone()
        if not row:
            conn.commit()
            return True
        if row["expires_at"] < ts:
            conn.execute("DELETE FROM control_locks WHERE device_id=?", (device_id,))
            conn.commit()
            return True
        if force:
            conn.execute("DELETE FROM control_locks WHERE device_id=?", (device_id,))
            conn.commit()
            return True
        if row["username"] != username or client_id is None or row["client_id"] != client_id:
            conn.commit()
            return False
        conn.execute(
            "DELETE FROM control_locks WHERE device_id=? AND username=? AND client_id=?",
            (device_id, username, client_id),
        )
        conn.commit()
    return True


def get_lock(device_id: str) -> dict | None:
    ts = now_ts()
    with db_connect() as conn:
        row = conn.execute(
            """
            SELECT l.*, u.enabled AS owner_enabled, u.expires_at AS owner_expires_at
            FROM control_locks l
            LEFT JOIN users u ON u.username=l.username
            WHERE l.device_id=?
            """,
            (device_id,),
        ).fetchone()
        if row and row["expires_at"] < ts:
            conn.execute("DELETE FROM control_locks WHERE device_id=?", (device_id,))
            conn.commit()
            return None
        if row and (row["owner_enabled"] is None or not bool(row["owner_enabled"]) or (row["owner_expires_at"] is not None and int(row["owner_expires_at"]) <= ts)):
            conn.execute("DELETE FROM control_locks WHERE device_id=?", (device_id,))
            conn.commit()
            return None
    return dict(row) if row else None


def lock_owned_by(device_id: str, username: str, client_id: str) -> bool:
    lock = get_lock(device_id)
    return bool(lock and lock["username"] == username and lock["client_id"] == client_id)


# Read-only ALAS binding projections live in ``storage_alas``.  The aliases
# below keep existing internal callers working while the public wrappers
# preserve the historical ``app.storage`` import path and signatures.
_alas_binding_payload = storage_alas.binding_payload


def get_user_alas_binding(username: str, config_name: str, device_id: str | None = None) -> dict | None:
    return storage_alas.get_user_alas_binding(
        username,
        config_name,
        device_id,
        connect=db_connect,
    )


def get_alas_binding_by_config(config_name: str, device_id: str | None = None) -> dict | None:
    return storage_alas.get_alas_binding_by_config(
        config_name,
        device_id,
        connect=db_connect,
    )


def get_user_alas_config(username: str, device_id: str | None = None) -> dict | None:
    return storage_alas.get_user_alas_config(
        username,
        device_id,
        connect=db_connect,
    )


def list_user_alas_bindings(username: str | None = None, device_id: str | None = None) -> list[dict]:
    return storage_alas.list_user_alas_bindings(
        username,
        device_id,
        connect=db_connect,
    )


def list_user_alas_configs() -> list[dict]:
    return storage_alas.list_user_alas_configs(connect=db_connect)


# Audit projections and read queries are owned by ``storage_audit``.  Keep
# these aliases after the legacy implementations so existing write paths use
# the same canonical hash and projection functions without a second state.
_audit_event_hash = storage_audit.event_hash
audit_event_dashboard_visible = storage_audit.event_dashboard_visible
_alert_projection = storage_audit.alert_projection
_audit_row_to_dict = storage_audit.row_to_dict


def recent_audit(limit: int = 100) -> list[dict]:
    return storage_audit.recent_audit(limit, connect=db_connect)


def list_audit_alerts(*, limit: int = 100, include_handled: bool = False) -> dict:
    return storage_audit.list_audit_alerts(
        limit=limit,
        include_handled=include_handled,
        connect=db_connect,
    )


def resolve_audit_alert(event_id: str, handled_by: str) -> dict | None:
    return storage_audit.resolve_audit_alert(
        event_id,
        handled_by,
        connect=db_connect,
        sanitize_text=sanitize_log_text,
        now=now_ts,
    )


def query_audit_events(
    *,
    before_id: int | None = None,
    actor: str = "",
    action: str = "",
    outcome: str = "",
    severity: str = "",
    request_id: str = "",
    device_id: str = "",
    source_ip: str = "",
    target: str = "",
    from_ts: int | None = None,
    to_ts: int | None = None,
    limit: int = 100,
) -> dict:
    return storage_audit.query_audit_events(
        before_id=before_id,
        actor=actor,
        action=action,
        outcome=outcome,
        severity=severity,
        request_id=request_id,
        device_id=device_id,
        source_ip=source_ip,
        target=target,
        from_ts=from_ts,
        to_ts=to_ts,
        limit=limit,
        connect=db_connect,
        sanitize_text=sanitize_log_text,
        clean_name=_audit_clean_name,
        sqlite_int=_audit_sqlite_int,
    )


def audit_facets() -> dict:
    return storage_audit.audit_facets(connect=db_connect)


def get_audit_event(audit_id: int | str) -> dict | None:
    return storage_audit.get_audit_event(
        audit_id,
        connect=db_connect,
        sanitize_text=sanitize_log_text,
        sqlite_int=_audit_sqlite_int,
    )


def audit_summary(*, from_ts: int | None = None, to_ts: int | None = None) -> dict:
    return storage_audit.audit_summary(from_ts=from_ts, to_ts=to_ts, connect=db_connect)


def verify_audit_integrity() -> dict:
    return storage_audit.verify_audit_integrity(
        connect=db_connect,
        on_error=lambda: AUDIT_LOGGER.critical("AUDIT_VERIFY_FAILED", exc_info=True),
    )


# Account expiration rules are pure and are owned by ``storage_users``.
_user_expires_at = storage_users.user_expires_at


def normalize_expires_at(value) -> int | None:
    return storage_users.normalize_expires_at(value, max_expires_at=MAX_ACCOUNT_EXPIRES_AT)


def user_is_active(user, now: int | None = None) -> bool:
    current = now_ts() if now is None else int(now)
    return storage_users.user_is_active(user, now=current)


def user_login_allowed(user) -> bool:
    """到期不封号：只有管理员显式「停用」才拒绝登录。

    投屏/ALAS 的闸门仍然只看 :func:`user_is_active`，所以到期账户能登录、
    能看自己的页面和到期提示，但用不了投屏（含仅观看）和 ALAS。
    """
    return storage_users.user_login_allowed(user)


def account_expiring_window_seconds(conn: sqlite3.Connection | None = None) -> int:
    """「即将到期」窗口：由后台设置 expiry_reminder_days（天）决定。

    历史实现把窗口硬编码成 7 天，于是后台把「到期提醒提前天数」改成 1 或 30
    都不生效（仪表盘待处理与用户页胶囊仍按 7 天判定）。0 表示不提前提醒。
    读不到设置（未初始化/表缺失）时回落到设置项自带的默认值，绝不让调用方失败。
    """
    default_days = ACCOUNT_EXPIRING_WINDOW_DAYS_DEFAULT
    try:
        default_days = int(str(DEFAULT_SETTINGS.get("expiry_reminder_days") or default_days))
    except (TypeError, ValueError):
        pass
    if conn is None:
        try:
            with db_connect() as local_conn:
                return account_expiring_window_seconds(local_conn)
        except Exception:
            return max(0, min(default_days, ACCOUNT_EXPIRING_WINDOW_DAYS_MAX)) * 86400
    try:
        days = int(str(_setting_value(conn, "expiry_reminder_days", str(default_days))).strip())
    except Exception:
        days = default_days
    return max(0, min(days, ACCOUNT_EXPIRING_WINDOW_DAYS_MAX)) * 86400


def user_expiration_payload(user, now: int | None = None) -> dict:
    current = now_ts() if now is None else int(now)
    return storage_users.user_expiration_payload(
        user,
        now=current,
        expiring_window_seconds=account_expiring_window_seconds(),
    )


# ---------------------------------------------------------------------------
# 每用户通知收件箱（工作台右上角「通知」）
# ---------------------------------------------------------------------------

NOTIFICATION_ACCOUNT_EXPIRING = "account_expiring"
NOTIFICATION_ACCOUNT_EXPIRED = "account_expired"


def create_user_notification(
    username: str,
    kind: str,
    *,
    severity: str = "info",
    data: dict | None = None,
    href: str = "",
    dedupe_key: str = "",
) -> bool:
    """Append one notification for a user; a repeated dedupe_key is a no-op."""
    username = str(username or "").strip()
    if not username:
        return False
    with db_connect() as conn:
        created = storage_notifications.create_notification(
            conn,
            username,
            kind,
            severity=severity,
            data=data,
            href=href,
            dedupe_key=dedupe_key,
            created_at=now_ts(),
        )
        if created is None:
            return False
        storage_notifications.prune_notifications(
            conn,
            username,
            keep=storage_notifications.KEEP_NOTIFICATIONS_PER_USER,
        )
        conn.commit()
    return True


def list_user_notifications(username: str, limit: int = storage_notifications.DEFAULT_NOTIFICATION_LIMIT) -> dict:
    username = str(username or "").strip()
    if not username:
        return {"items": [], "unread": 0, "total": 0}
    with db_connect() as conn:
        items = storage_notifications.list_notifications(conn, username, limit=limit)
        unread = storage_notifications.unread_count(conn, username)
        total = storage_notifications.total_count(conn, username)
    return {"items": items, "unread": unread, "total": total, "limit": limit}


def mark_user_notifications_read(
    username: str,
    *,
    ids: list | None = None,
    mark_all: bool = False,
) -> dict:
    username = str(username or "").strip()
    if not username:
        return {"marked": 0, "unread": 0}
    with db_connect() as conn:
        marked = storage_notifications.mark_read(
            conn,
            username,
            ids=ids,
            mark_all=mark_all,
            now=now_ts(),
        )
        unread = storage_notifications.unread_count(conn, username)
        conn.commit()
    return {"marked": marked, "unread": unread}


def sync_account_notifications(username: str) -> int:
    """派生「即将到期 / 已到期」通知。

    账户状态是持续状态而不是一次性事件，所以不做定时投递：每次读取收件箱时按当前
    的 expires_at 与后台提醒窗口补一条（dedupe_key 绑定到具体到期时间，续期后
    会重新提醒）。返回新建条数。
    """
    username = str(username or "").strip()
    if not username:
        return 0
    user = get_user(username)
    if not user:
        return 0
    payload = user_expiration_payload(user)
    state = str(payload.get("expiration_state") or "")
    if state not in ("expiring", "expired"):
        return 0
    expires_at = payload.get("expires_at")
    if expires_at is None:
        return 0
    is_admin = str(user["role"]) == "admin"
    # 普通用户没有 /users 权限，指向工作台；管理员直接进用户与权限。
    href = "/users" if is_admin else "/"
    if state == "expired":
        created = create_user_notification(
            username,
            NOTIFICATION_ACCOUNT_EXPIRED,
            severity="error",
            data={"expires_at": int(expires_at)},
            href=href,
            dedupe_key=f"account_expired:{int(expires_at)}",
        )
        return 1 if created else 0
    remaining = int(payload.get("remaining_seconds") or 0)
    days = max(1, (remaining + 86399) // 86400) if remaining > 0 else 0
    created = create_user_notification(
        username,
        NOTIFICATION_ACCOUNT_EXPIRING,
        severity="warning",
        data={"expires_at": int(expires_at), "days": days},
        href=href,
        dedupe_key=f"account_expiring:{int(expires_at)}",
    )
    return 1 if created else 0
