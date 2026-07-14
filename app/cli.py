import os
import sys

from . import storage


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else "initial-password"
    if command == "bootstrap-admin":
        password = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("INITIAL_ADMIN_PASSWORD", "").strip()
        if not password:
            password = storage.generate_random_password()
        os.environ["INITIAL_ADMIN_PASSWORD"] = password
        admin_created = storage.init_db()
        print(storage.get_initial_admin_password_for_display(admin_created))
        return 0

    admin_created = storage.init_db()
    if command == "initial-password":
        password = storage.get_initial_admin_password_for_display(admin_created)
        if password:
            print(password)
            return 0
        print("")
        return 1
    if command == "reset-admin":
        password = sys.argv[2] if len(sys.argv) > 2 else storage.generate_random_password()
        storage.upsert_user("admin", password, "admin")
        print(password)
        return 0
    print(f"unknown command: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
