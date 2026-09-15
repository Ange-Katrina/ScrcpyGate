"""Read-only ALAS binding projections.

This module owns the query and row-shaping side of ALAS bindings.  Database
initialization and all mutating transactions remain in :mod:`app.storage`.
The connection factory is injected so this module cannot create a second
database singleton or import the compatibility facade.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

from .storage_types import AlasConfigOwnershipError


ConnectionFactory = Callable[[], sqlite3.Connection]


def binding_payload(row: sqlite3.Row | dict | None) -> dict | None:
    """Convert one binding row to the stable public projection."""
    if not row:
        return None
    payload = {
        "username": row["username"],
        "config_name": row["config_name"],
        "device_id": row["device_id"] if "device_id" in row.keys() else None,
        "can_run": bool(row["can_run"]),
        "can_edit": bool(row["can_edit"]),
        "is_default": bool(row["is_default"]),
        "updated_at": int(row["updated_at"]),
    }
    if "role" in row.keys():
        payload["role"] = row["role"]
    if "device_name" in row.keys():
        payload["device_name"] = row["device_name"] or ""
    return payload


def config_name_key(value: object) -> str:
    """ALAS 配置名的**归属键**：去首尾空白后做大小写折叠。

    归属（谁拥有这个名字）必须大小写不敏感：`BobCfg` 与 `bobcfg` 在大小写不敏感的
    文件系统（Windows / 网络挂载）上就是同一个 ALAS 配置文件，如果两条绑定分别
    写进库里，两个用户就会操作同一份配置；仅凭大小写差异还能绕过「一个配置只有
    一个属主」的判定。

    用 ``str.lower()`` 而不是 ``str.casefold()``：casefold 会把 ``ß`` 与 ``ss``、
    ``ẞ`` 与 ``ss`` 合并，那是「同一性折叠」而不是「大小写折叠」，连大小写不敏感
    的文件系统也把这两对当成不同的名字。

    数据库里的列 ``user_alas_configs.config_key`` 保存本函数的结果，唯一索引
    ``ux_user_alas_configs_config_owner`` 建在它上面，所以这条规则只有一处实现。
    """
    return str(value or "").strip().lower()


def _binding_row_for_config(conn: sqlite3.Connection, config_name: str) -> sqlite3.Row | None:
    """按归属键取绑定行；行里的 ``config_name`` 始终是库里保存的原始拼写。"""
    return conn.execute(
        "SELECT username, config_name, is_default, device_id "
        "FROM user_alas_configs WHERE config_key=?",
        (config_name_key(config_name),),
    ).fetchone()


def config_owner(conn: sqlite3.Connection, config_name: str) -> str | None:
    row = _binding_row_for_config(conn, config_name)
    return row["username"] if row else None


def require_config_available(conn: sqlite3.Connection, username: str, config_name: str) -> sqlite3.Row | None:
    """确认该配置名没有被别的用户占用，并把属主自己的那一行返回给调用方。

    返回值让「同一属主换大小写重新提交」的写入路径可以直接改成 UPDATE，而不是
    再查一次；没有既有行时返回 ``None``（可以新建）。
    """
    row = _binding_row_for_config(conn, config_name)
    if row is not None and row["username"] != username:
        raise AlasConfigOwnershipError(config_name, row["username"])
    return row


def get_user_alas_binding(
    username: str,
    config_name: str,
    device_id: str | None = None,
    *,
    connect: ConnectionFactory,
) -> dict | None:
    params: list[str] = [(username or "").strip(), config_name_key(config_name)]
    device_filter = ""
    if device_id is not None:
        device_filter = " AND device_id=?"
        params.append((device_id or "").strip())
    with connect() as conn:
        row = conn.execute(
            f"""
            SELECT uac.*,d.name AS device_name
            FROM user_alas_configs uac
            LEFT JOIN devices d ON d.id=uac.device_id
            WHERE uac.username=? AND uac.config_key=?{device_filter}
            """,
            params,
        ).fetchone()
    return binding_payload(row)


def get_alas_binding_by_config(
    config_name: str,
    device_id: str | None = None,
    *,
    connect: ConnectionFactory,
) -> dict | None:
    params: list[str] = [config_name_key(config_name)]
    device_filter = ""
    if device_id is not None:
        device_filter = " AND uac.device_id=?"
        params.append((device_id or "").strip())
    with connect() as conn:
        row = conn.execute(
            f"""
            SELECT uac.*,u.role,d.name AS device_name
            FROM user_alas_configs uac
            JOIN users u ON u.username=uac.username
            LEFT JOIN devices d ON d.id=uac.device_id
            WHERE uac.config_key=?{device_filter}
            """,
            params,
        ).fetchone()
    return binding_payload(row)


def get_user_alas_config(
    username: str,
    device_id: str | None = None,
    *,
    connect: ConnectionFactory,
) -> dict | None:
    params: list[str] = [(username or "").strip()]
    device_filter = ""
    if device_id is not None:
        device_filter = " AND device_id=?"
        params.append((device_id or "").strip())
    with connect() as conn:
        row = conn.execute(
            f"""
            SELECT uac.*,d.name AS device_name
            FROM user_alas_configs uac
            LEFT JOIN devices d ON d.id=uac.device_id
            WHERE uac.username=?{device_filter}
            ORDER BY uac.is_default DESC,uac.config_name
            LIMIT 1
            """,
            params,
        ).fetchone()
    return binding_payload(row)


def list_user_alas_bindings(
    username: str | None = None,
    device_id: str | None = None,
    *,
    connect: ConnectionFactory,
) -> list[dict]:
    params: list[str] = []
    clauses: list[str] = []
    if username is not None:
        clauses.append("uac.username=?")
        params.append((username or "").strip())
    if device_id is not None:
        clauses.append("uac.device_id=?")
        params.append((device_id or "").strip())
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT uac.username,u.role,uac.config_name,uac.device_id,d.name AS device_name,
                   uac.can_run,uac.can_edit,uac.is_default,uac.updated_at
            FROM user_alas_configs uac
            JOIN users u ON u.username=uac.username
            LEFT JOIN devices d ON d.id=uac.device_id
            {where}
            ORDER BY uac.username,uac.is_default DESC,uac.config_name
            """,
            params,
        ).fetchall()
    return [binding_payload(row) for row in rows]


def list_user_alas_configs(*, connect: ConnectionFactory) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            WITH ranked_bindings AS (
                SELECT uac.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY uac.username
                           ORDER BY uac.is_default DESC,uac.config_name
                       ) AS binding_rank
                FROM user_alas_configs uac
            )
            SELECT u.username,u.role,rb.config_name,rb.device_id,d.name AS device_name,
                   rb.can_run,rb.can_edit,rb.is_default,rb.updated_at
            FROM users u
            LEFT JOIN ranked_bindings rb
                   ON rb.username=u.username AND rb.binding_rank=1
            LEFT JOIN devices d ON d.id=rb.device_id
            ORDER BY u.username
            """
        ).fetchall()
    return [
        {
            "username": row["username"],
            "role": row["role"],
            "config_name": str(row["config_name"] or ""),
            "device_id": row["device_id"],
            "device_name": str(row["device_name"] or ""),
            "can_run": bool(row["can_run"]),
            "can_edit": bool(row["can_edit"]),
            "is_default": bool(row["is_default"]),
            "updated_at": int(row["updated_at"] or 0),
        }
        for row in rows
    ]


__all__ = [
    "AlasConfigOwnershipError",
    "binding_payload",
    "config_name_key",
    "config_owner",
    "require_config_available",
    "get_user_alas_binding",
    "get_alas_binding_by_config",
    "get_user_alas_config",
    "list_user_alas_bindings",
    "list_user_alas_configs",
]
