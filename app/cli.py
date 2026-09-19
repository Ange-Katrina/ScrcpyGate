import json
import os
import sys

from . import storage


def _show_generated_password() -> bool:
    """Require an explicit, process-scoped opt-in before emitting a password."""
    return os.environ.get("SCRCPYGATE_SHOW_GENERATED_PASSWORD", "").strip().lower() == "true"


def _print_generated_password(password: str) -> None:
    print(password if _show_generated_password() else "")


def _require_generated_password_display_authorization() -> int:
    if _show_generated_password():
        return 0
    print("generated password output requires explicit authorization", file=sys.stderr)
    return 2


def _reject_password_arguments(arguments: list[str]) -> int:
    if not arguments:
        return 0
    print("password arguments are not accepted; use INITIAL_ADMIN_PASSWORD", file=sys.stderr)
    return 2


def _redacted_migration_status() -> int:
    """Print migration state without exposing credentials or exception text."""
    try:
        status = storage.get_alas_token_migration_status()
    except Exception:
        status = {
            "ok": False,
            "state": "migration_failed",
            "format": "unknown",
            "decryptable": False,
            "needs_rotation": True,
        }
    print(json.dumps(status, sort_keys=True))
    return 0 if status.get("ok") else 1


def _redacted_migration_status_failure() -> int:
    print(
        json.dumps(
            {
                "ok": False,
                "state": "migration_failed",
                "format": "unknown",
                "decryptable": False,
                "needs_rotation": True,
            },
            sort_keys=True,
        )
    )
    return 1


def _provision_alas_key() -> int:
    """Provision the server-side ALAS token key; the key value is never printed."""
    from . import alas_secrets

    if alas_secrets.injected_key_present():
        if not alas_secrets.key_is_valid():
            print(json.dumps({"ok": False, "action": "failed", "error": "invalid_injected_key"}, sort_keys=True))
            return 1
        print(json.dumps({"ok": True, "action": "environment", "source": "environment"}, sort_keys=True))
        return 0
    try:
        summary = alas_secrets.provision_key_file()
    except alas_secrets.AlasTokenError as exc:
        print(json.dumps({"ok": False, "action": "failed", "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(summary, sort_keys=True))
    return 0


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else "initial-password"
    if command == "alas-token-status":
        extra = sys.argv[2:]
        if extra and extra != ["--check"]:
            print("unknown option for token migration status", file=sys.stderr)
            return 2
        # Status is deliberately read-only: it must not initialize SQLite or
        # import/rotate a legacy credential as a side effect of a check.
        return _redacted_migration_status()

    if command == "generate-alas-key":
        if sys.argv[2:]:
            print("unknown option for ALAS key provisioning", file=sys.stderr)
            return 2
        return _provision_alas_key()

    if command == "migrate-alas-token":
        if sys.argv[2:]:
            print("unknown option for ALAS token migration", file=sys.stderr)
            return 2
        try:
            # The migration command owns initialization, legacy import,
            # current/previous key rotation, and legacy-file cleanup.
            storage.init_db()
        except Exception:
            return _redacted_migration_status_failure()
        return _redacted_migration_status()

    if command == "clear-alas-token":
        if sys.argv[2:]:
            print("unknown option for ALAS token clearing", file=sys.stderr)
            return 2
        # 换不回密钥时的恢复出口：清掉存不出来的密文，管理员重新在 ALAS 设置里填写。
        # 只打印脱敏摘要（是否清掉了、原值的指纹），永远不回显凭据本身。
        try:
            storage.init_db()
        except Exception:
            return _redacted_migration_status_failure()
        try:
            summary = storage.clear_alas_token()
        except Exception:
            print(json.dumps({"ok": False, "action": "failed", "error": "token_clear_failed"}, sort_keys=True))
            return 1
        print(json.dumps(summary, sort_keys=True, ensure_ascii=False))
        return 0

    if command == "bootstrap-admin":
        if _reject_password_arguments(sys.argv[2:]):
            return 2
        if _require_generated_password_display_authorization():
            return 2
        password = os.environ.get("INITIAL_ADMIN_PASSWORD", "").strip()
        if not password:
            password = storage.generate_random_password()
        os.environ["INITIAL_ADMIN_PASSWORD"] = password
        try:
            admin_created = storage.init_db()
            _print_generated_password(storage.get_initial_admin_password_for_display(admin_created))
        finally:
            os.environ.pop("INITIAL_ADMIN_PASSWORD", None)
        return 0

    if command == "reset-admin":
        if _reject_password_arguments(sys.argv[2:]):
            return 2
        if _require_generated_password_display_authorization():
            return 2
        storage.init_db()
        password = storage.generate_random_password()
        storage.upsert_user("admin", password, "admin", must_change_password=False)
        _print_generated_password(password)
        return 0

    if command == "initial-password":
        if _reject_password_arguments(sys.argv[2:]):
            return 2
        if _require_generated_password_display_authorization():
            return 2
        admin_created = storage.init_db()
        password = storage.get_initial_admin_password_for_display(admin_created)
        if password:
            print(password)
            return 0
        print("")
        return 1

    # ---- 服务器侧恢复入口（BAN）----
    # 容器/服务器上没有后台可用时（例如管理员把自己封了），这三条命令是唯一的出口。
    # 它们直接改数据库：运行中的进程按内存快照判定，跨进程改动最多 5 秒（SNAPSHOT_TTL）后生效。
    if command == "ban-list":
        if sys.argv[2:]:
            print("unknown option for ban list", file=sys.stderr)
            return 2
        from . import ip_ban

        storage.init_db()
        listing = ip_ban.list_bans(include_inactive=True, limit=200)
        counters = ip_ban.counters()
        print(json.dumps(
            {
                "ok": True,
                "counters": counters,
                "bans": [
                    {
                        "ip": row["ip"],
                        "active": row["active"],
                        "permanent": row["permanent"],
                        "expires_ts": row["expires_ts"],
                        "remaining_seconds": row["remaining_seconds"],
                        "reason": row["reason"],
                        "actor": row["actor"],
                    }
                    for row in listing["items"]
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        ))
        return 0

    if command == "unban":
        target = sys.argv[2] if len(sys.argv) > 2 else ""
        if not target or len(sys.argv) > 3:
            print("usage: python -m app.cli unban <ip>", file=sys.stderr)
            return 2
        from . import ip_ban

        storage.init_db()
        try:
            row = ip_ban.unban(target, actor="cli")
        except ip_ban.BanError as exc:
            print(json.dumps({"ok": False, "error": exc.code}, sort_keys=True))
            return 1
        if row is None:
            print(json.dumps({"ok": False, "error": "not_banned", "ip": target}, sort_keys=True))
            return 1
        print(json.dumps({"ok": True, "ip": row["ip"], "active": row["active"]}, ensure_ascii=False, sort_keys=True))
        return 0

    if command == "ban":
        from . import ip_ban

        arguments = sys.argv[2:]
        if not arguments:
            print("usage: python -m app.cli ban <ip> [--preset 15m|1h|24h|7d|permanent] [--seconds N] [--reason TEXT]", file=sys.stderr)
            return 2
        target = arguments[0]
        options = {"preset": None, "seconds": None, "reason": ""}
        index = 1
        while index < len(arguments):
            flag = arguments[index]
            value = arguments[index + 1] if index + 1 < len(arguments) else ""
            if flag == "--preset":
                options["preset"] = value
            elif flag == "--seconds":
                options["seconds"] = value
            elif flag == "--reason":
                options["reason"] = value
            else:
                print(f"unknown option for ban: {flag}", file=sys.stderr)
                return 2
            index += 2
        storage.init_db()
        try:
            row = ip_ban.ban(
                target,
                seconds=options["seconds"],
                preset=options["preset"],
                reason=options["reason"],
                actor="cli",
            )
        except ip_ban.BanError as exc:
            print(json.dumps({"ok": False, "error": exc.code}, sort_keys=True))
            return 1
        print(json.dumps(
            {
                "ok": True,
                "ip": row["ip"],
                "permanent": row["permanent"],
                "expires_ts": row["expires_ts"],
                "reason": row["reason"],
            },
            ensure_ascii=False,
            sort_keys=True,
        ))
        return 0

    if command == "geo-status":
        if sys.argv[2:]:
            print("unknown option for geo status", file=sys.stderr)
            return 2
        from . import geo_access

        storage.init_db()
        print(json.dumps(geo_access.status(), ensure_ascii=False, sort_keys=True))
        return 0

    if command == "geo-off":
        if sys.argv[2:]:
            print("unknown option for geo off", file=sys.stderr)
            return 2
        from . import geo_access

        storage.init_db()
        # 误锁恢复出口：GEO 在 enforce 且缺库时会拒绝**所有**请求（含管理接口），
        # 此时只能靠这条命令（或环境变量 GEO_ENFORCE_DISABLED=true）把策略改回 off。
        # 写库后运行中的进程按策略缓存 TTL（≤5 秒）生效，不需要重启。
        before = geo_access.policy(force=True).mode
        storage.save_ui_settings({"geo_mode": "off"})
        geo_access.invalidate_policy()
        after = geo_access.policy(force=True).mode
        print(json.dumps({"ok": after == "off", "mode_before": before, "mode_after": after}, sort_keys=True))
        return 0 if after == "off" else 1

    storage.init_db()
    print(f"unknown command: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
