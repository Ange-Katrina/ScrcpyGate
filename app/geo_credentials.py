"""Write-only MaxMind configuration, persisted like geoipupdate's private config.

Keep this file in the private data volume, never in SQLite/settings exports.
Environment credentials override the entire saved pair (never mix sources).
No additional encryption key is required for reinstall/restore.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import tempfile
import threading

_lock = threading.RLock()
FILE_NAME = ".geo-credentials.json"


class CredentialError(ValueError):
    """Stable, secret-free configuration error."""


def _path() -> Path:
    return Path(os.environ.get("WEB_SCRCPY_DATA_DIR", "data") or "data") / FILE_NAME


def environment_present() -> bool:
    return any(os.environ.get(name, "").strip() for name in ("GEO_ACCOUNT_ID", "GEO_LICENSE_KEY"))


def _validate(account: object, key: object) -> tuple[str, str]:
    if not isinstance(account, str) or not re.fullmatch(r"[0-9]{1,20}", account):
        raise CredentialError("account_id_invalid")
    if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", key):
        raise CredentialError("license_key_invalid")
    return account, key


def _read() -> tuple[str, str]:
    path = _path()
    try:
        meta = path.lstat()
        if not stat.S_ISREG(meta.st_mode) or meta.st_size > 4096:
            raise CredentialError("credentials_unreadable")
        # Linux containers must never consume a group/world-readable secret file.
        if os.name != "nt" and meta.st_mode & 0o077:
            raise CredentialError("credentials_permissions")
        with path.open(encoding="utf-8") as handle:
            raw = handle.read(4097)
        if len(raw) > 4096:
            raise CredentialError("credentials_unreadable")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise CredentialError("credentials_unreadable")
        return _validate(data.get("account_id"), data.get("license_key"))
    except FileNotFoundError:
        return "", ""
    except CredentialError:
        raise
    except (OSError, UnicodeError, ValueError):
        raise CredentialError("credentials_unreadable") from None


def resolve() -> tuple[str, str]:
    with _lock:
        if environment_present():
            return os.environ.get("GEO_ACCOUNT_ID", "").strip(), os.environ.get("GEO_LICENSE_KEY", "").strip()
        return _read()


def status() -> dict:
    error = ""
    try:
        account, key = resolve()
        if account:
            _validate(account, "placeholder")
        if key:
            _validate("0", key)
    except CredentialError as exc:
        account = key = ""
        error = str(exc)
    return {
        "credential_source": "environment" if environment_present() else "file" if _path().exists() else "none",
        "credentials_managed": environment_present(),
        "credentials_saved": _path().exists(),
        "credentials_error": error,
        "account_id_present": bool(account),
        "license_key_present": bool(key),
    }


def save(account: object, key: object) -> None:
    with _lock:
        if environment_present():
            raise CredentialError("credentials_managed")
        if not isinstance(account, str) or not isinstance(key, str):
            raise CredentialError("credentials_invalid")
        account, key = account.strip(), key.strip()
        # Blank fields retain the saved pair. Changing account requires a new key.
        if not account or not key:
            old_account, old_key = _read()
            if account and account != old_account and not key:
                raise CredentialError("license_key_required")
            account, key = account or old_account, key or old_key
        account, key = _validate(account, key)
        path = _path()
        temp = None
        try:
            if path.is_symlink() or (path.exists() and not path.is_file()):
                raise CredentialError("credentials_unreadable")
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, name = tempfile.mkstemp(prefix=".geo-credentials-", suffix=".tmp", dir=path.parent)
            temp = Path(name)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                os.chmod(temp, 0o600)
                json.dump({"account_id": account, "license_key": key}, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
        except OSError:
            raise CredentialError("credentials_write_failed") from None
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)


def clear() -> None:
    with _lock:
        try:
            _path().unlink(missing_ok=True)
        except OSError:
            raise CredentialError("credentials_write_failed") from None
