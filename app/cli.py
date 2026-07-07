import sys

from . import storage


def main() -> int:
    storage.init_db()
    command = sys.argv[1] if len(sys.argv) > 1 else "initial-password"
    if command == "initial-password":
        password = storage.get_initial_admin_password_for_display()
        if password:
            print(password)
            return 0
        print("")
        return 1
    if command == "reset-admin":
        password = sys.argv[2] if len(sys.argv) > 2 else storage._generate_initial_password()
        storage.upsert_user("admin", password, "admin")
        print(password)
        return 0
    print(f"unknown command: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())