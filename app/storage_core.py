"""SQLite connection and filesystem primitives used by the storage layer.

This module deliberately contains no application-domain SQL.  The public
``app.storage`` module remains the compatibility entrypoint while this module
owns the process-local database connection setup and path configuration.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
from functools import lru_cache
from pathlib import Path


DATA_DIR = Path(os.environ.get("WEB_SCRCPY_DATA_DIR", "data"))
DB_PATH = DATA_DIR / "webscrcpy.db"
INITIAL_ADMIN_PASSWORD_FILE = DATA_DIR / "initial_admin_password.txt"
LEGACY_USERS_FILE = DATA_DIR / "users.json"
LEGACY_ENV_FILE = DATA_DIR / ".env"


def session_token_hash(token: str | None) -> str:
    """会话令牌在库中的存储形态：只存 SHA-256，原文只存在于浏览器 Cookie。

    放在 core 里是因为存储层与连接注册表（``app.account_access``）都要用它，
    必须只有一份实现，否则两边算出来的键会不一致（会导致"按会话踢出"失效）。
    """
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


_db_setup_lock = threading.Lock()
_data_dirs_ready: set[str] = set()
_wal_configured: set[str] = set()
_restricted_paths: set[str] = set()

# Keep the WAL from pinning its high-water mark forever.  SQLite truncates the
# file back to this size on the next checkpoint; without a limit the file stays
# at the largest size it ever reached (measured ~4 MB after a few thousand
# writes, and larger after bulk migrations).
WAL_JOURNAL_SIZE_LIMIT = 32 * 1024 * 1024

# Startup migration lock.  ``init_db()`` runs in the web process *and* in every
# ``app.cli`` command, so two processes can migrate the same database at once;
# SQLite serialises single statements but not a whole migration sequence.  A
# plain ``O_EXCL`` lock file works on every platform without extra dependencies,
# and a lock older than ``INIT_DB_LOCK_STALE_SECONDS`` is taken over so a killed
# process cannot block startup forever.
INIT_DB_LOCK_NAME = ".init-db.lock"
INIT_DB_LOCK_TIMEOUT_SECONDS = 30.0
INIT_DB_LOCK_STALE_SECONDS = 300.0


class StartupLock:
    """Best-effort exclusive lock for the startup migration sequence."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        timeout: float = INIT_DB_LOCK_TIMEOUT_SECONDS,
        stale_after: float = INIT_DB_LOCK_STALE_SECONDS,
    ) -> None:
        self.path = Path(path) if path is not None else DATA_DIR / INIT_DB_LOCK_NAME
        self.timeout = timeout
        self.stale_after = stale_after
        self.acquired = False

    def __enter__(self) -> "StartupLock":
        import time

        deadline = time.monotonic() + self.timeout
        while True:
            try:
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                if self._take_over_stale_lock():
                    continue
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"startup lock busy: {self.path}")
                time.sleep(0.05)
                continue
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(f"{os.getpid()}\n")
            self.acquired = True
            return self

    def _take_over_stale_lock(self) -> bool:
        import time

        try:
            age = time.time() - self.path.stat().st_mtime
        except OSError:
            return True  # it disappeared; retry the exclusive create
        if age <= self.stale_after:
            return False
        try:
            self.path.unlink()
        except OSError:
            return False
        return True

    def __exit__(self, exc_type, exc, traceback) -> bool:
        if self.acquired:
            try:
                self.path.unlink()
            except OSError:
                pass
            self.acquired = False
        return False


class ClosingConnection(sqlite3.Connection):
    """Connection that also releases the file handle when a ``with`` block ends.

    ``sqlite3.Connection.__exit__`` only commits/rolls back the transaction, so
    the 60+ ``with db_connect() as conn:`` call sites relied on reference
    counting to close the handle (measured: with GC disabled, 200 calls left 200
    live connections).  Using this as the connection factory makes the resource
    contract explicit instead of interpreter-dependent.
    """

    def __exit__(self, exc_type, exc, traceback) -> bool:  # type: ignore[override]
        try:
            return bool(super().__exit__(exc_type, exc, traceback))
        finally:
            self.close()


def now_ts() -> int:
    """Return the current Unix timestamp used by storage records."""
    import time

    return int(time.time())


@lru_cache(maxsize=64)
def _normalized_path_text(path_text: str) -> str:
    return os.path.normcase(str(Path(path_text).expanduser().resolve(strict=False)))


def _normalized_path(path: str | Path) -> str:
    """Normalize a path for process-local setup caches."""
    return _normalized_path_text(str(path))


def _restrict_db_file_permissions() -> None:
    """Restrict database files to the application account where supported.

    Only paths that exist *and* have not been restricted in this process are
    chmod-ed: the previous implementation ran five ``chmod`` calls on **every**
    connection, which measured at 0.60 ms of the 0.84 ms per-connection cost
    (ISSUE-131).  Sidecars created later are still caught the first time a
    connection sees them.
    """
    for candidate in (DB_PATH, Path(f"{DB_PATH}-wal"), Path(f"{DB_PATH}-shm"), DATA_DIR):
        key = _normalized_path(candidate)
        if key in _restricted_paths:
            continue
        if not candidate.exists():
            # It may appear later (WAL/SHM are created on first write); retry on
            # the next connection instead of caching a miss.
            continue
        try:
            os.chmod(candidate, 0o600 if candidate != DATA_DIR else 0o700)
        except OSError:
            pass
        _restricted_paths.add(key)


def db_connect() -> sqlite3.Connection:
    """Open an independent SQLite connection with the existing safeguards."""
    data_dir_key = _normalized_path(DATA_DIR)
    if data_dir_key not in _data_dirs_ready:
        with _db_setup_lock:
            if data_dir_key not in _data_dirs_ready:
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                _data_dirs_ready.add(data_dir_key)

    conn = sqlite3.connect(DB_PATH, factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(f"PRAGMA journal_size_limit={WAL_JOURNAL_SIZE_LIMIT}")
        db_path_key = _normalized_path(DB_PATH)
        if db_path_key not in _wal_configured:
            with _db_setup_lock:
                if db_path_key not in _wal_configured:
                    row = conn.execute("PRAGMA journal_mode=WAL").fetchone()
                    mode = str(row[0]).lower() if row else ""
                    if mode == "wal":
                        _wal_configured.add(db_path_key)
        # WAL and SHM files can be created after the first connection.  Apply
        # the filesystem boundary on every open so a later connection cannot
        # leave newly-created SQLite sidecars with inherited permissions.
        _restrict_db_file_permissions()
        return conn
    except Exception:
        conn.close()
        raise


def readonly_db_connect() -> sqlite3.Connection:
    """Open an existing database without creating or modifying SQLite state."""
    if DB_PATH.is_symlink():
        raise OSError("database file must not be a symlink")
    if not DB_PATH.is_file():
        raise FileNotFoundError(DB_PATH)
    database_uri = DB_PATH.resolve(strict=True).as_uri() + "?mode=ro"
    conn = sqlite3.connect(database_uri, uri=True, factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA query_only=ON")
        return conn
    except Exception:
        conn.close()
        raise


def parse_env_file(path: Path) -> dict:
    """Parse the legacy ``.env`` format used by the first-install migrator."""
    result = {}
    if path.is_symlink():
        raise OSError("legacy environment file must not be a symlink")
    if not path.exists():
        return result
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export") and len(key) > len("export") and key[len("export")].isspace():
            key = key[len("export") :].strip()
        result[key] = value.strip().strip('"').strip("'")
    return result


__all__ = [
    "DATA_DIR",
    "DB_PATH",
    "INITIAL_ADMIN_PASSWORD_FILE",
    "session_token_hash",
    "LEGACY_USERS_FILE",
    "LEGACY_ENV_FILE",
    "WAL_JOURNAL_SIZE_LIMIT",
    "INIT_DB_LOCK_NAME",
    "INIT_DB_LOCK_TIMEOUT_SECONDS",
    "INIT_DB_LOCK_STALE_SECONDS",
    "StartupLock",
    "ClosingConnection",
    "now_ts",
    "db_connect",
    "readonly_db_connect",
    "parse_env_file",
    "_normalized_path",
    "_normalized_path_text",
    "_restrict_db_file_permissions",
    "_db_setup_lock",
    "_data_dirs_ready",
    "_wal_configured",
    "_restricted_paths",
]
