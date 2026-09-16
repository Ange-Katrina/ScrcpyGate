"""Base SQLite schema/bootstrap primitives for :mod:`app.storage`.

**Single source of truth for newly created databases.**  This module owns the
canonical column definitions; :func:`app.storage_schema.create_base_schema`
creates the base tables and the compatibility migrations in
:mod:`app.storage` only bring *legacy* databases up to the same shape.  When a
column changes here, mirror it in the legacy path (and vice versa) — the
regression test ``test_migrated_legacy_schema_matches_fresh_schema`` builds a
pre-migration database and asserts the two definitions end up identical.

Domain migrations remain orchestrated by ``app.storage`` in this phase.  The
functions here only create the existing base tables and apply the generic
compatibility columns/indexes that are safe to run on every startup.
"""

from __future__ import annotations

import re
import sqlite3


def check_clauses(table_sql: str) -> list[str]:
    """Return the CHECK expressions of a ``CREATE TABLE`` statement.

    Balanced-parenthesis scan — a naive ``[^)]*`` regex truncates the first
    constraint that contains a nested parenthesis.
    """
    checks: list[str] = []
    for match in re.finditer(r"\bCHECK\s*\(", table_sql or "", re.IGNORECASE):
        start = match.end()
        depth = 1
        index = start
        while index < len(table_sql) and depth:
            char = table_sql[index]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            index += 1
        if depth == 0:
            checks.append(re.sub(r"\s+", " ", table_sql[start : index - 1]).strip())
    return sorted(checks)


def table_names(conn: sqlite3.Connection) -> list[str]:
    """Application tables (SQLite internals excluded)."""
    return [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]


def referencing_tables(conn: sqlite3.Connection, table: str) -> list[str]:
    """Tables whose foreign keys point at ``table`` (its children)."""
    children = []
    for name in table_names(conn):
        for row in conn.execute(f'PRAGMA foreign_key_list("{name}")'):
            if row[2] == table:
                children.append(name)
                break
    return children


def rebuild_table_if_needed(
    conn: sqlite3.Connection,
    table: str,
    create_canonical,
    *,
    logger=None,
    fallbacks: dict[str, str] | None = None,
) -> bool:
    """Rebuild ``table`` to its canonical definition when it differs.

    The copy is derived from the canonical columns: columns the legacy table
    lacks are filled from ``fallbacks`` (a source-column expression, e.g. a
    legacy deadline column), then the canonical ``DEFAULT``, then ``NULL`` — so
    the caller does not hand-write per-table projections.  Safety properties:

    * foreign keys are switched off (outside the transaction — the pragma is a
      no-op inside one) whenever the table is a foreign-key parent, so dropping
      the old table cannot cascade into children;
    * indexes of the rebuilt table are recreated from their stored SQL;
    * ``PRAGMA foreign_key_check`` runs *before* commit — violations roll the
      rebuild back;
    * a CHECK that rejects existing rows (bad legacy data) rolls back and logs
      instead of failing the upgrade.
    """
    scratch = f"__canonical_{table}"
    if matches_canonical_definition(conn, table, lambda name: create_canonical(name), scratch):
        return False
    fallbacks = fallbacks or {}

    conn.commit()  # an implicit transaction from earlier migration DDL/DML
    children = referencing_tables(conn, table)
    foreign_keys_on = bool(conn.execute("PRAGMA foreign_keys").fetchone()[0])
    if children and foreign_keys_on:
        conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(f'DROP TABLE IF EXISTS "{scratch}"')
        create_canonical(scratch)
        canonical_columns = list(conn.execute(f'PRAGMA table_info("{scratch}")'))
        conn.execute(f'DROP TABLE "{scratch}"')
        source_columns = {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}
        expressions = []
        for row in canonical_columns:
            name, default, not_null = row[1], row[4], bool(row[3])
            if name in source_columns:
                expressions.append(f'"{name}"')
            elif name in fallbacks:
                expressions.append(fallbacks[name])
            elif default is not None:
                expressions.append(default)
            elif not_null:
                raise sqlite3.OperationalError(
                    f"{table}.{name} is NOT NULL without a default; cannot rebuild automatically"
                )
            else:
                expressions.append("NULL")
        # Index definitions are stored per table and would be dropped with it.
        index_sql = [
            row[0]
            for row in conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
                (table,),
            )
        ]
        target = f"{table}_new"
        conn.execute(f'DROP TABLE IF EXISTS "{target}"')
        create_canonical(target)
        conn.execute(
            f'INSERT INTO "{target}" SELECT {", ".join(expressions)} FROM "{table}"'
        )
        # Rebuild through the scratch/target pair so an existing ``_new`` table
        # from an interrupted run cannot collide.
        conn.execute(f'DROP TABLE "{table}"')
        conn.execute(f'ALTER TABLE "{target}" RENAME TO "{table}"')
        for statement in index_sql:
            conn.execute(statement)
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise sqlite3.IntegrityError(f"foreign_key_check reported {len(violations)} rows")
    except sqlite3.IntegrityError as exc:
        # 例如历史数据本身违反 CHECK：回滚并保留旧表，不让升级直接失败。
        conn.rollback()
        if logger is not None:
            logger.critical(
                "SCHEMA_REBUILD_REJECTED table=%s reason=%s",
                table,
                exc,
                extra={"event_name": "schema.rebuild_rejected"},
            )
        if children and foreign_keys_on:
            conn.execute("PRAGMA foreign_keys=ON")
        return False
    except Exception:
        conn.rollback()
        if children and foreign_keys_on:
            conn.execute("PRAGMA foreign_keys=ON")
        raise
    conn.commit()
    if children and foreign_keys_on:
        conn.execute("PRAGMA foreign_keys=ON")
    if logger is not None:
        logger.info(
            "SCHEMA_REBUILT table=%s",
            table,
            extra={"event_name": "schema.rebuilt"},
        )
    return True


DEVICES_COLUMNS = """
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            address TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
            created_at TEXT NOT NULL
"""


def create_devices_table(conn: sqlite3.Connection, name: str = "devices") -> None:
    conn.execute(f"CREATE TABLE IF NOT EXISTS {name} ({DEVICES_COLUMNS})")


USERS_COLUMNS = """
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('admin','user')),
            created_at TEXT NOT NULL,
            must_change_password INTEGER NOT NULL DEFAULT 0 CHECK(must_change_password IN (0, 1)),
            video_mode TEXT NOT NULL DEFAULT 'normal' CHECK(video_mode IN ('normal','alas')),
            expires_at INTEGER NULL,
            enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
            last_login_at INTEGER NULL,
            last_login_ip TEXT NULL,
            alas_visible INTEGER NOT NULL DEFAULT 1 CHECK(alas_visible IN (0, 1))
"""


def create_users_table(conn: sqlite3.Connection, name: str = "users") -> None:
    conn.execute(f"CREATE TABLE IF NOT EXISTS {name} ({USERS_COLUMNS})")


USER_DEVICES_COLUMNS = """
            username TEXT NOT NULL,
            device_id TEXT NOT NULL,
            can_view INTEGER NOT NULL DEFAULT 1 CHECK(can_view IN (0, 1)),
            can_control INTEGER NOT NULL DEFAULT 1 CHECK(can_control IN (0, 1)),
            source TEXT NOT NULL DEFAULT 'manual',
            PRIMARY KEY(username, device_id),
            FOREIGN KEY(username) REFERENCES users(username) ON DELETE CASCADE,
            FOREIGN KEY(device_id) REFERENCES devices(id) ON DELETE CASCADE
"""


def create_user_devices_table(conn: sqlite3.Connection, name: str = "user_devices") -> None:
    conn.execute(f"CREATE TABLE IF NOT EXISTS {name} ({USER_DEVICES_COLUMNS})")


AUDIT_ALERTS_COLUMNS = """
            event_id TEXT PRIMARY KEY,
            audit_id INTEGER NOT NULL,
            ts INTEGER NOT NULL,
            username TEXT NOT NULL DEFAULT '',
            actor_role TEXT NOT NULL DEFAULT 'unknown',
            action TEXT NOT NULL DEFAULT 'event',
            target_type TEXT NOT NULL DEFAULT '',
            target_id TEXT NOT NULL DEFAULT '',
            outcome TEXT NOT NULL DEFAULT 'unknown',
            reason TEXT NOT NULL DEFAULT '',
            severity TEXT NOT NULL DEFAULT 'warning',
            request_id TEXT NOT NULL DEFAULT '',
            source_ip TEXT NOT NULL DEFAULT '',
            detail TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            alert_type TEXT NOT NULL DEFAULT 'service_failure',
            title TEXT NOT NULL DEFAULT '系统异常',
            summary TEXT NOT NULL DEFAULT '',
            log_url TEXT NOT NULL DEFAULT '/logs',
            dashboard_visible INTEGER NOT NULL DEFAULT 1 CHECK(dashboard_visible IN (0, 1)),
            handled_at INTEGER,
            handled_by TEXT NOT NULL DEFAULT ''
"""


def create_audit_alerts_table(conn: sqlite3.Connection, name: str = "audit_alerts") -> None:
    conn.execute(f"CREATE TABLE IF NOT EXISTS {name} ({AUDIT_ALERTS_COLUMNS})")


def table_definition(conn: sqlite3.Connection, name: str) -> tuple:
    """Definition-level fingerprint of one table (column definitions + constraints).

    Used to decide whether a legacy table must be rebuilt: comparing against a
    scratch table created from the canonical DDL keeps the two definitions
    honest without hand-maintained expectations (ISSUE-132).
    """
    columns = [
        (row[1], (row[2] or "").upper(), bool(row[3]), row[4], int(row[5] or 0))
        for row in conn.execute(f'PRAGMA table_info("{name}")')
    ]
    foreign_keys = sorted(
        (row[2], row[3], row[4], row[5], row[6])
        for row in conn.execute(f'PRAGMA foreign_key_list("{name}")')
    )
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    return (tuple(columns), tuple(foreign_keys), tuple(check_clauses(row[0] if row else "")))


def matches_canonical_definition(
    conn: sqlite3.Connection,
    name: str,
    create_canonical,
    scratch_name: str,
) -> bool:
    """Return whether ``name`` has exactly the canonical definition.

    ``create_canonical`` is called with the scratch table name; the scratch
    table is always removed again.
    """
    conn.execute(f'DROP TABLE IF EXISTS "{scratch_name}"')
    create_canonical(scratch_name)
    try:
        return table_definition(conn, name) == table_definition(conn, scratch_name)
    finally:
        conn.execute(f'DROP TABLE IF EXISTS "{scratch_name}"')


# The ALAS binding table is created through a shared helper so the fresh-install
# definition (``create_base_schema``) and the legacy table-rebuild path
# (``app.storage._migrate_user_alas_configs``) can never drift apart.
USER_ALAS_CONFIGS_COLUMNS = """
            username TEXT NOT NULL,
            config_name TEXT NOT NULL,
            device_id TEXT NULL,
            can_run INTEGER NOT NULL DEFAULT 1 CHECK(can_run IN (0, 1)),
            can_edit INTEGER NOT NULL DEFAULT 0 CHECK(can_edit IN (0, 1)),
            is_default INTEGER NOT NULL DEFAULT 0 CHECK(is_default IN (0, 1)),
            updated_at INTEGER NOT NULL,
            -- Appended to match the legacy ``ADD COLUMN`` order (see the users
            -- table comment); the default keeps both paths identical.
            config_key TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(username, config_name),
            FOREIGN KEY(username) REFERENCES users(username) ON DELETE CASCADE,
            FOREIGN KEY(device_id) REFERENCES devices(id) ON DELETE SET NULL
"""


def create_user_alas_configs_table(conn: sqlite3.Connection, name: str = "user_alas_configs") -> None:
    """Create the ALAS binding table with the canonical definition.

    ``name`` is used by the legacy rebuild path, which materialises a
    ``user_alas_configs_new`` table before swapping it in.
    """
    conn.execute(f"CREATE TABLE IF NOT EXISTS {name} ({USER_ALAS_CONFIGS_COLUMNS})")


def create_base_schema(conn: sqlite3.Connection) -> None:
    """Create the base tables on an existing connection.

    Tables whose definition the compatibility path may have to rebuild are
    created through the shared ``create_*_table`` helpers; the rest live in the
    script below.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
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
        CREATE TABLE IF NOT EXISTS viewer_watch_sessions (
            id TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            device_id TEXT,
            started_at_ms INTEGER NOT NULL,
            ended_at_ms INTEGER,
            duration_ms INTEGER NOT NULL DEFAULT 0,
            end_reason TEXT NOT NULL DEFAULT '',
            FOREIGN KEY(username) REFERENCES users(username) ON DELETE CASCADE,
            FOREIGN KEY(device_id) REFERENCES devices(id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS user_alas_preferences (
            username TEXT PRIMARY KEY,
            restart_on_exit INTEGER NOT NULL DEFAULT 0 CHECK(restart_on_exit IN (0, 1)),
            updated_at INTEGER NOT NULL,
            FOREIGN KEY(username) REFERENCES users(username) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS login_challenges (
            challenge_id TEXT PRIMARY KEY,
            salt TEXT NOT NULL,
            bits INTEGER NOT NULL,
            max_number INTEGER NOT NULL,
            expires REAL NOT NULL,
            ip TEXT NOT NULL,
            username TEXT NOT NULL,
            used INTEGER NOT NULL DEFAULT 0 CHECK(used IN (0, 1)),
            issued_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS login_guard_state (
            singleton TEXT PRIMARY KEY,
            failures_json TEXT NOT NULL,
            locked_until_json TEXT NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS user_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            kind TEXT NOT NULL,
            severity TEXT NOT NULL DEFAULT 'info',
            data_json TEXT NOT NULL DEFAULT '{}',
            href TEXT NOT NULL DEFAULT '',
            dedupe_key TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL,
            read_at INTEGER NULL,
            FOREIGN KEY(username) REFERENCES users(username) ON DELETE CASCADE
        );
        """
    )
    # Order matters: parents before the children that reference them.
    create_users_table(conn)
    create_devices_table(conn)
    create_user_devices_table(conn)
    create_user_video_preferences_table(conn)
    create_user_alas_configs_table(conn)
    create_sessions_table(conn)
    create_audit_alerts_table(conn)


def ensure_base_indexes(conn: sqlite3.Connection) -> None:
    """Create the unchanged user indexes used by account lookups."""
    conn.execute("CREATE INDEX IF NOT EXISTS idx_users_enabled ON users(enabled)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_users_expires_at ON users(expires_at)")
    # Account-scoped session sweeps (``DELETE FROM sessions WHERE username=?``)
    # otherwise scan the whole table.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_username ON sessions(username)")
    # ``user_devices.device_id`` is a foreign key (ON DELETE CASCADE) and the
    # devices page asks "who can view this device?" — both need a leading index.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_user_devices_device ON user_devices(device_id)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_viewer_watch_user_started "
        "ON viewer_watch_sessions(username, started_at_ms DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_viewer_watch_device_started "
        "ON viewer_watch_sessions(device_id, started_at_ms DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_viewer_watch_active "
        "ON viewer_watch_sessions(ended_at_ms) WHERE ended_at_ms IS NULL"
    )
    # Retention deletes finished rows by end time; without this the sweep scans
    # the whole history table on every maintenance tick.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_viewer_watch_ended ON viewer_watch_sessions(ended_at_ms)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_login_challenges_expires ON login_challenges(expires)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_notifications_inbox "
        "ON user_notifications(username, id DESC)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_user_notifications_dedupe "
        "ON user_notifications(username, dedupe_key) WHERE dedupe_key != ''"
    )


SESSIONS_COLUMNS = """
            sid TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            csrf_token TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            idle_expires_at INTEGER NOT NULL,
            absolute_expires_at INTEGER NOT NULL,
            last_seen_at INTEGER,
            client_ip TEXT,
            user_agent TEXT,
            device_id TEXT
"""


USER_VIDEO_PREFERENCES_COLUMNS = """
            username TEXT PRIMARY KEY,
            profile TEXT NOT NULL,
            adaptive INTEGER NOT NULL DEFAULT 0 CHECK(adaptive IN (0, 1)),
            video_bit_rate INTEGER NOT NULL,
            max_size INTEGER NOT NULL,
            max_fps INTEGER NOT NULL,
            scrcpy_stream_mode TEXT NOT NULL DEFAULT 'raw',
            updated_at INTEGER NOT NULL,
            FOREIGN KEY(username) REFERENCES users(username) ON DELETE CASCADE
"""


def create_sessions_table(conn: sqlite3.Connection, name: str = "sessions") -> None:
    """Create the session table with the canonical definition."""
    conn.execute(f"CREATE TABLE IF NOT EXISTS {name} ({SESSIONS_COLUMNS})")


def create_user_video_preferences_table(conn: sqlite3.Connection, name: str = "user_video_preferences") -> None:
    """Create the per-user video preference table with the canonical definition."""
    conn.execute(f"CREATE TABLE IF NOT EXISTS {name} ({USER_VIDEO_PREFERENCES_COLUMNS})")


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row[1] == column for row in conn.execute(f'PRAGMA table_info("{table}")'))


def ensure_compatibility_schema(conn: sqlite3.Connection, *, logger=None) -> None:
    """Apply the existing generic columns and indexes to legacy databases.

    Tables the compatibility path can extend with ``ALTER TABLE`` (``users``,
    ``user_devices``, ``devices``) keep a canonical column order that matches the
    ALTER result, so both paths agree on column order and defaults.  Constraints
    cannot be added by ALTER, so tables that need ``CHECK``s the legacy shape
    lacks are rebuilt through :func:`rebuild_table_if_needed` (``users`` is a
    foreign-key parent of six tables — the helper disables foreign keys around
    the swap and re-checks them before committing).
    """
    user_columns = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "video_mode" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN video_mode TEXT NOT NULL DEFAULT 'normal'")
    if "expires_at" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN expires_at INTEGER NULL")
    if "last_login_at" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN last_login_at INTEGER NULL")
    if "last_login_ip" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN last_login_ip TEXT NULL")
    if "enabled" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1")
    # 每个用户的 ALAS 可见性（有些账号用不上 ALAS）：只影响界面显隐，不参与权限判定。
    if "alas_visible" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN alas_visible INTEGER NOT NULL DEFAULT 1")
    conn.execute("UPDATE users SET enabled=1 WHERE enabled IS NULL")
    conn.execute("UPDATE users SET alas_visible=1 WHERE alas_visible IS NULL")

    # 登录会话列表需要展示「最近活动 / 来源 IP / 客户端」，老库补齐这三列（可空，无默认值，
    # 因此 ALTER 不会重写既有行，成本可忽略）。device_id 是浏览器侧的稳定设备标识，
    # 用来把「同一台设备的多条会话」在安全页里归并成一行。
    session_columns = {row[1] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()}
    if session_columns:
        if "last_seen_at" not in session_columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN last_seen_at INTEGER")
        if "client_ip" not in session_columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN client_ip TEXT")
        if "user_agent" not in session_columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN user_agent TEXT")
        if "device_id" not in session_columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN device_id TEXT")

    # 设备权限的来源：'manual'（管理员显式授予）或 'alas'（ALAS 绑定顺带授予）。
    # 历史库里的行无法追溯来源，一律按 'manual' 处理（保守：不会误删既有授权）。
    device_columns = {row[1] for row in conn.execute("PRAGMA table_info(user_devices)").fetchall()}
    if device_columns and "source" not in device_columns:
        conn.execute("ALTER TABLE user_devices ADD COLUMN source TEXT NOT NULL DEFAULT 'manual'")

    # 约束补齐只能靠重建：布尔列在旧库里没有 CHECK（ISSUE-132 D）。
    # 重建前先清掉外键孤儿行 —— 它们会让 PRAGMA foreign_key_check 不通过而挡住重建，
    # 而且这些行在管理页本来就不可见（列表都 JOIN users/devices）。
    orphans = conn.execute(
        "DELETE FROM user_devices WHERE username NOT IN (SELECT username FROM users)"
        " OR device_id NOT IN (SELECT id FROM devices)"
    ).rowcount
    if orphans and logger is not None:
        logger.warning("USER_DEVICES_ORPHANS_REMOVED rows=%s", orphans)
    orphans = conn.execute(
        "DELETE FROM user_video_preferences WHERE username NOT IN (SELECT username FROM users)"
    ).rowcount
    if orphans and logger is not None:
        logger.warning("USER_VIDEO_PREFERENCES_ORPHANS_REMOVED rows=%s", orphans)

    # 顺序：先子表后父表，减少外键开关的次数。
    rebuild_table_if_needed(
        conn, "user_video_preferences",
        lambda name: create_user_video_preferences_table(conn, name), logger=logger,
    )
    rebuild_table_if_needed(
        conn, "user_alas_configs",
        lambda name: create_user_alas_configs_table(conn, name), logger=logger,
    )
    rebuild_table_if_needed(
        conn,
        "sessions",
        lambda name: create_sessions_table(conn, name),
        logger=logger,
        # 旧库只有一个 expires_at；两个新死线由它派生（与旧 ALTER+UPDATE 语义一致）。
        fallbacks={
            "idle_expires_at": "COALESCE(idle_expires_at, expires_at)"
            if _has_column(conn, "sessions", "idle_expires_at")
            else "expires_at",
            "absolute_expires_at": "COALESCE(absolute_expires_at, created_at + 604800)"
            if _has_column(conn, "sessions", "absolute_expires_at")
            else "created_at + 604800",
        },
    )
    rebuild_table_if_needed(conn, "user_devices", lambda name: create_user_devices_table(conn, name), logger=logger)
    rebuild_table_if_needed(conn, "audit_alerts", lambda name: create_audit_alerts_table(conn, name), logger=logger)
    rebuild_table_if_needed(conn, "devices", lambda name: create_devices_table(conn, name), logger=logger)
    rebuild_table_if_needed(conn, "users", lambda name: create_users_table(conn, name), logger=logger)

    # 索引放最后：上面的表重建会连带丢掉该表的索引。
    ensure_base_indexes(conn)


__all__ = [
    "create_base_schema",
    "create_audit_alerts_table",
    "create_devices_table",
    "create_sessions_table",
    "create_user_alas_configs_table",
    "create_user_devices_table",
    "create_user_video_preferences_table",
    "create_users_table",
    "ensure_compatibility_schema",
    "ensure_base_indexes",
    "rebuild_table_if_needed",
    "check_clauses",
    "matches_canonical_definition",
    "referencing_tables",
    "table_definition",
    "table_names",
    "AUDIT_ALERTS_COLUMNS",
    "DEVICES_COLUMNS",
    "USER_ALAS_CONFIGS_COLUMNS",
    "USER_DEVICES_COLUMNS",
    "USER_VIDEO_PREFERENCES_COLUMNS",
    "USERS_COLUMNS",
    "SESSIONS_COLUMNS",
]
