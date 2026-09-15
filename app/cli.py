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
    storage.init_db()
    print(f"unknown command: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
