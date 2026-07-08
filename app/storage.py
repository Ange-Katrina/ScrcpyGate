import json
import os
import secrets
import sqlite3
import time
import hashlib
import hmac
from pathlib import Path

try:
    from werkzeug.security import check_password_hash as werkzeug_check_password_hash
except Exception:  # pragma: no cover
    werkzeug_check_password_hash = None

DATA_DIR = Path(os.environ.get("WEB_SCRCPY_DATA_DIR", "data"))
DB_PATH = DATA_DIR / "webscrcpy.db"
INITIAL_ADMIN_PASSWORD_FILE = DATA_DIR / "initial_admin_password.txt"
LEGACY_USERS_FILE = DATA_DIR / "users.json"
LEGACY_ENV_FILE = DATA_DIR / ".env"
MIN_PASSWORD_LENGTH = int(os.environ.get("MIN_PASSWORD_LENGTH", "12") or "12")
PASSWORD_PBKDF2_ITERATIONS = int(os.environ.get("PASSWORD_PBKDF2_ITERATIONS", "310000") or "310000")

DEFAULT_SETTINGS = {
    "video_profile": "balanced",
    "video_adaptive": "false",
    "video_bit_rate": "900000",
    "max_size": "540",
    "max_fps": "24",
    "scrcpy_stream_mode": "raw",
    "scrcpy_enabled_stream_modes": "raw",
    "video_preset_smooth_video_bit_rate": "700000",
    "video_preset_smooth_max_size": "480",
    "video_preset_smooth_max_fps": "24",
    "video_preset_balanced_video_bit_rate": "900000",
    "video_preset_balanced_max_size": "540",
    "video_preset_balanced_max_fps": "24",
    "video_preset_sharp_video_bit_rate": "1600000",
    "video_preset_sharp_max_size": "720",
    "video_preset_sharp_max_fps": "30",
    "video_preset_low_latency_video_bit_rate": "900000",
    "video_preset_low_latency_max_size": "480",
    "video_preset_low_latency_max_fps": "30",
    "video_custom_profiles": "{}",
    "auto_stop_time": "15",
    "auto_stop_minutes": "15",
    "alas_enabled": "false",
    "alas_base_url": "http://127.0.0.1:22267",
    "alas_current_config": "alas",
    "alas_token": "",
}

COMMON_WEAK_PASSWORDS = {"admin", "admin123", "password", "password123", "123456", "12345678", "qwerty123"}


def now_ts() -> int:
    return int(time.time())


def db_connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def parse_env_file(path: Path) -> dict:
    result = {}
    if not path.exists():
        return result
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def hash_password(password: str) -> str:
    salt = os.urandom(32)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_PBKDF2_ITERATIONS}${salt.hex()}${key.hex()}"


def password_hash_needs_upgrade(stored: str) -> bool:
    stored = stored or ""
    if not stored.startswith("pbkdf2_sha256$"):
        return True
    try:
        _, iterations_text, _salt_hex, _key_hex = stored.split("$", 3)
        return int(iterations_text) < PASSWORD_PBKDF2_ITERATIONS
    except Exception:
        return True


def verify_password(password: str, stored: str) -> bool:
    stored = stored or ""
    if stored.startswith("pbkdf2_sha256$"):
        try:
            _, iterations_text, salt_hex, key_hex = stored.split("$", 3)
            iterations = int(iterations_text)
            salt = bytes.fromhex(salt_hex)
            stored_key = bytes.fromhex(key_hex)
            new_key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
            return secrets.compare_digest(stored_key, new_key)
        except Exception:
            return False
    if ":" in stored and not stored.startswith(("pbkdf2:", "scrypt:")):
        try:
            salt_hex, key_hex = stored.split(":", 1)
            salt = bytes.fromhex(salt_hex)
            stored_key = bytes.fromhex(key_hex)
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


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _remove_initial_password_file()
    with db_connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('admin','user')),
                created_at TEXT NOT NULL,
                must_change_password INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS devices (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                address TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS user_devices (
                username TEXT NOT NULL,
                device_id TEXT NOT NULL,
                can_view INTEGER NOT NULL DEFAULT 1,
                can_control INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY(username, device_id),
                FOREIGN KEY(username) REFERENCES users(username) ON DELETE CASCADE,
                FOREIGN KEY(device_id) REFERENCES devices(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS user_video_preferences (
                username TEXT PRIMARY KEY,
                profile TEXT NOT NULL,
                adaptive INTEGER NOT NULL DEFAULT 0,
                video_bit_rate INTEGER NOT NULL,
                max_size INTEGER NOT NULL,
                max_fps INTEGER NOT NULL,
                scrcpy_stream_mode TEXT NOT NULL DEFAULT 'raw',
                updated_at INTEGER NOT NULL,
                FOREIGN KEY(username) REFERENCES users(username) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS user_alas_configs (
                username TEXT PRIMARY KEY,
                config_name TEXT NOT NULL,
                can_run INTEGER NOT NULL DEFAULT 1,
                can_edit INTEGER NOT NULL DEFAULT 0,
                updated_at INTEGER NOT NULL,
                FOREIGN KEY(username) REFERENCES users(username) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS sessions (
                sid TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                csrf_token TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS control_locks (
                device_id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                client_id TEXT NOT NULL,
                acquired_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER NOT NULL,
                username TEXT NOT NULL,
                action TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT ''
            );
            """
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(user_video_preferences)").fetchall()}
        if "scrcpy_stream_mode" not in columns:
            conn.execute("ALTER TABLE user_video_preferences ADD COLUMN scrcpy_stream_mode TEXT NOT NULL DEFAULT 'raw'")
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)", (key, value))
        _migrate_video_defaults(conn)
        conn.commit()
    migrate_legacy_data()


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


def _remove_initial_password_file() -> None:
    try:
        INITIAL_ADMIN_PASSWORD_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def generate_random_password() -> str:
    return secrets.token_urlsafe(24)


def _generate_initial_password() -> str:
    _remove_initial_password_file()
    configured = os.environ.get("INITIAL_ADMIN_PASSWORD", "").strip()
    if configured:
        error = validate_password(configured, "admin")
        if error:
            raise ValueError(error)
        return configured
    return generate_random_password()


def get_initial_admin_password_for_display() -> str:
    password = os.environ.get("INITIAL_ADMIN_PASSWORD", "").strip()
    if not password:
        return ""
    with db_connect() as conn:
        row = conn.execute("SELECT password_hash FROM users WHERE username='admin'").fetchone()
    if not row or not verify_password(password, row["password_hash"]):
        return ""
    return password


def migrate_legacy_data() -> None:
    with db_connect() as conn:
        user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if user_count == 0:
            if LEGACY_USERS_FILE.exists():
                try:
                    users = json.loads(LEGACY_USERS_FILE.read_text(encoding="utf-8"))
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
            else:
                password = _generate_initial_password()
                conn.execute(
                    "INSERT INTO users(username,password_hash,role,created_at,must_change_password) VALUES(?,?,?,?,?)",
                    ("admin", hash_password(password), "admin", time.strftime("%Y-%m-%d %H:%M:%S"), 0),
                )
        env = parse_env_file(LEGACY_ENV_FILE)
        for key in ("video_bit_rate", "max_size", "max_fps", "auto_stop_time", "auto_stop_minutes"):
            raw = env.get(key.upper()) or env.get(key)
            if raw is not None:
                conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, str(raw)))
        if env.get("ALAS_GYRE_ENABLED") is not None:
            enabled = env.get("ALAS_GYRE_ENABLED", "").lower() in ("1", "true", "yes", "on")
            conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('alas_enabled',?)", ("true" if enabled else "false",))
        for legacy, key in (("ALAS_GYRE_BASE_URL", "alas_base_url"), ("ALAS_GYRE_CONFIG", "alas_current_config"), ("ALAS_GYRE_TOKEN", "alas_token")):
            if env.get(legacy) is not None:
                conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, env[legacy]))
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
                conn.execute("INSERT OR IGNORE INTO user_devices(username,device_id,can_view,can_control) VALUES(?,?,1,1)", (username, device_id))
        for username in users:
            for device_id, _, _ in devices[:1]:
                conn.execute("INSERT OR IGNORE INTO user_devices(username,device_id,can_view,can_control) VALUES(?,?,1,1)", (username, device_id))
        conn.commit()


def audit(username: str, action: str, detail: str = "") -> None:
    with db_connect() as conn:
        conn.execute("INSERT INTO audit_log(ts,username,action,detail) VALUES(?,?,?,?)", (now_ts(), username or "?", action, detail or ""))
        conn.commit()


def get_setting(key: str, default: str = "") -> str:
    with db_connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with db_connect() as conn:
        conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, str(value)))
        conn.commit()


def get_or_create_public_salt() -> str:
    salt = get_setting("device_public_salt", "")
    if salt:
        return salt
    salt = secrets.token_urlsafe(32)
    set_setting("device_public_salt", salt)
    return salt


def public_device_id(device_id: str) -> str:
    raw = (device_id or "").strip()
    if not raw:
        return ""
    salt = get_or_create_public_salt().encode("utf-8")
    digest = hmac.new(salt, raw.encode("utf-8"), hashlib.sha256).hexdigest()[:18]
    return f"dev_{digest}"


def resolve_device_ref(device_ref: str) -> str | None:
    ref = (device_ref or "").strip()
    if not ref:
        return None
    if get_device(ref):
        return ref
    for device in list_all_devices():
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
    data.update({row["key"]: row["value"] for row in rows})
    return data


def get_user(username: str):
    with db_connect() as conn:
        return conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()


def list_users() -> list[dict]:
    with db_connect() as conn:
        return [dict(row) for row in conn.execute("SELECT username,role,created_at,must_change_password FROM users ORDER BY username")]


def admin_count(conn: sqlite3.Connection | None = None) -> int:
    if conn is not None:
        return int(conn.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0])
    with db_connect() as local_conn:
        return admin_count(local_conn)


def upsert_user(username: str, password: str | None, role: str) -> None:
    username = (username or "").strip()
    if not username or len(username) > 64 or any(ch.isspace() for ch in username):
        raise ValueError("invalid_username")
    if role not in ("admin", "user"):
        raise ValueError("invalid_role")
    if password:
        error = validate_password(password, username)
        if error:
            raise ValueError(error)
    with db_connect() as conn:
        exists = conn.execute("SELECT username FROM users WHERE username=?", (username,)).fetchone()
        if exists:
            current = conn.execute("SELECT role FROM users WHERE username=?", (username,)).fetchone()
            if current and current["role"] == "admin" and role != "admin" and admin_count(conn) <= 1:
                raise ValueError("last_admin_required")
            if password:
                conn.execute("UPDATE users SET password_hash=?, role=? WHERE username=?", (hash_password(password), role, username))
            else:
                conn.execute("UPDATE users SET role=? WHERE username=?", (role, username))
        else:
            if not password:
                raise ValueError("password_required")
            conn.execute("INSERT INTO users(username,password_hash,role,created_at,must_change_password) VALUES(?,?,?,?,0)", (username, hash_password(password), role, time.strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()


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
        user = conn.execute("SELECT role FROM users WHERE username=?", (username,)).fetchone()
        if user and user["role"] == "admin" and admin_count(conn) <= 1:
            raise ValueError("last_admin_required")
        conn.execute("DELETE FROM users WHERE username=?", (username,))
        conn.execute("DELETE FROM sessions WHERE username=?", (username,))
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


def get_user_alas_config(username: str) -> dict | None:
    with db_connect() as conn:
        row = conn.execute("SELECT * FROM user_alas_configs WHERE username=?", (username,)).fetchone()
    if not row:
        return None
    return {
        "username": row["username"],
        "config_name": row["config_name"],
        "can_run": bool(row["can_run"]),
        "can_edit": bool(row["can_edit"]),
        "updated_at": int(row["updated_at"]),
    }


def list_user_alas_configs() -> list[dict]:
    with db_connect() as conn:
        rows = conn.execute(
            """
            SELECT u.username, u.role, uac.config_name, uac.can_run, uac.can_edit, uac.updated_at
            FROM users u LEFT JOIN user_alas_configs uac ON u.username=uac.username
            ORDER BY u.username
            """
        ).fetchall()
    result = []
    for row in rows:
        result.append(
            {
                "username": row["username"],
                "role": row["role"],
                "config_name": row["config_name"] or "",
                "can_run": bool(row["can_run"]) if row["config_name"] else False,
                "can_edit": bool(row["can_edit"]) if row["config_name"] else False,
                "updated_at": int(row["updated_at"]) if row["updated_at"] else 0,
            }
        )
    return result


def set_user_alas_config(username: str, config_name: str, can_run: bool, can_edit: bool) -> None:
    username = (username or "").strip()
    config_name = (config_name or "").strip()
    if not username or not get_user(username):
        raise ValueError("invalid_username")
    if not config_name:
        raise ValueError("invalid_config_name")
    with db_connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO user_alas_configs(username,config_name,can_run,can_edit,updated_at)
            VALUES(?,?,?,?,?)
            """,
            (username, config_name, 1 if can_run else 0, 1 if can_edit else 0, now_ts()),
        )
        conn.commit()


def delete_user_alas_config(username: str) -> None:
    with db_connect() as conn:
        conn.execute("DELETE FROM user_alas_configs WHERE username=?", ((username or "").strip(),))
        conn.commit()


def list_all_devices() -> list[dict]:
    with db_connect() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM devices ORDER BY id")]


def upsert_device(device_id: str, name: str, address: str, enabled: bool = True) -> None:
    with db_connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO devices(id,name,address,enabled,created_at) VALUES(?,?,?,?,COALESCE((SELECT created_at FROM devices WHERE id=?),?))",
            (device_id, name or device_id, address or device_id, 1 if enabled else 0, device_id, time.strftime("%Y-%m-%d %H:%M:%S")),
        )
        admins = conn.execute("SELECT username FROM users WHERE role='admin'").fetchall()
        for row in admins:
            conn.execute("INSERT OR IGNORE INTO user_devices(username,device_id,can_view,can_control) VALUES(?,?,1,1)", (row["username"], device_id))
        conn.commit()


def delete_device(device_id: str) -> None:
    with db_connect() as conn:
        conn.execute("DELETE FROM devices WHERE id=?", (device_id,))
        conn.commit()


def get_device(device_id: str):
    with db_connect() as conn:
        return conn.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone()


def user_can(username: str, device_id: str, action: str) -> bool:
    user = get_user(username)
    if user and user["role"] == "admin":
        return True
    col = "can_control" if action == "control" else "can_view"
    with db_connect() as conn:
        row = conn.execute(f"SELECT {col} FROM user_devices WHERE username=? AND device_id=?", (username, device_id)).fetchone()
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
    with db_connect() as conn:
        conn.execute("INSERT OR REPLACE INTO user_devices(username,device_id,can_view,can_control) VALUES(?,?,?,?)", (username, device_id, 1 if can_view else 0, 1 if can_control else 0))
        conn.commit()


def create_session(username: str, ttl_seconds: int = 43200) -> dict:
    sid = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    ts = now_ts()
    with db_connect() as conn:
        conn.execute("INSERT INTO sessions(sid,username,csrf_token,created_at,expires_at) VALUES(?,?,?,?,?)", (sid, username, csrf, ts, ts + ttl_seconds))
        conn.commit()
    return {"sid": sid, "csrf_token": csrf, "username": username, "expires_at": ts + ttl_seconds}


def get_session(sid: str | None) -> dict | None:
    if not sid:
        return None
    with db_connect() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE sid=?", (sid,)).fetchone()
        if not row:
            return None
        if row["expires_at"] < now_ts():
            conn.execute("DELETE FROM sessions WHERE sid=?", (sid,))
            conn.commit()
            return None
    return dict(row)


def delete_session(sid: str | None) -> None:
    if not sid:
        return
    with db_connect() as conn:
        conn.execute("DELETE FROM sessions WHERE sid=?", (sid,))
        conn.commit()


def delete_other_sessions(username: str, keep_sid: str | None) -> int:
    username = (username or "").strip()
    if not username:
        return 0
    with db_connect() as conn:
        if keep_sid:
            cur = conn.execute("DELETE FROM sessions WHERE username=? AND sid<>?", (username, keep_sid))
        else:
            cur = conn.execute("DELETE FROM sessions WHERE username=?", (username,))
        conn.commit()
        return int(cur.rowcount or 0)


def authenticate(username: str, password: str) -> dict | None:
    user = get_user(username)
    if not user:
        return None
    if not verify_password(password, user["password_hash"]):
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
        current = conn.execute("SELECT * FROM control_locks WHERE device_id=?", (device_id,)).fetchone()
        if current and current["expires_at"] >= ts and current["username"] != username and not force:
            return {"ok": False, "owner": current["username"], "expires_at": current["expires_at"]}
        conn.execute("INSERT OR REPLACE INTO control_locks(device_id,username,client_id,acquired_at,expires_at) VALUES(?,?,?,?,?)", (device_id, username, client_id, ts, expires))
        conn.commit()
    return {"ok": True, "owner": username, "expires_at": expires}


def release_lock(device_id: str, username: str, force: bool = False, client_id: str | None = None) -> bool:
    with db_connect() as conn:
        row = conn.execute("SELECT * FROM control_locks WHERE device_id=?", (device_id,)).fetchone()
        if not row:
            return True
        if force:
            conn.execute("DELETE FROM control_locks WHERE device_id=?", (device_id,))
            conn.commit()
            return True
        if row["username"] != username:
            return False
        if client_id is not None and row["client_id"] != client_id:
            return False
        conn.execute("DELETE FROM control_locks WHERE device_id=?", (device_id,))
        conn.commit()
    return True


def get_lock(device_id: str) -> dict | None:
    ts = now_ts()
    with db_connect() as conn:
        row = conn.execute("SELECT * FROM control_locks WHERE device_id=?", (device_id,)).fetchone()
        if row and row["expires_at"] < ts:
            conn.execute("DELETE FROM control_locks WHERE device_id=?", (device_id,))
            conn.commit()
            return None
    return dict(row) if row else None


def lock_owned_by(device_id: str, username: str) -> bool:
    lock = get_lock(device_id)
    return bool(lock and lock["username"] == username)


def recent_audit(limit: int = 100) -> list[dict]:
    limit = max(10, min(int(limit), 1000))
    with db_connect() as conn:
        rows = conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(row) for row in reversed(rows)]
