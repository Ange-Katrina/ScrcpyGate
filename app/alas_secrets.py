"""Encryption helpers for ALAS credentials stored in SQLite."""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import re
import secrets
import stat
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


TOKEN_PREFIX = "v1:"
TOKEN_AAD = b"scrcpygate:alas-token:v1"
TOKEN_KEY_ENV = "ALAS_TOKEN_ENCRYPTION_KEY"
TOKEN_PREVIOUS_KEY_ENV = "ALAS_TOKEN_ENCRYPTION_KEY_PREVIOUS"
TOKEN_KEY_FILE_ENV = "ALAS_TOKEN_ENCRYPTION_KEY_FILE"
TOKEN_KEY_FILE_NAME = ".alas-token-encryption-key"


class AlasTokenError(ValueError):
    """Raised when an ALAS token cannot be safely encrypted or decrypted."""


def _decode_urlsafe_base64(value: str) -> bytes:
    """Decode canonical URL-safe Base64 without silently discarding invalid bytes."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", value):
        raise ValueError("invalid base64")
    unpadded = value.rstrip("=")
    padding = len(value) - len(unpadded)
    if len(unpadded) % 4 == 1:
        raise ValueError("invalid base64")
    expected_padding = (-len(unpadded)) % 4
    if padding not in (0, expected_padding):
        raise ValueError("invalid base64")
    padded = unpadded + "=" * expected_padding
    decoded = base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)
    if base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=") != unpadded:
        raise ValueError("invalid base64")
    return decoded


def _decode_key(raw: str, name: str) -> bytes:
    value = str(raw or "").strip()
    if not value:
        raise AlasTokenError(f"{name} is not configured")
    try:
        if len(value) == 64 and all(char in "0123456789abcdefABCDEF" for char in value):
            key = bytes.fromhex(value)
        else:
            key = _decode_urlsafe_base64(value)
    except (ValueError, UnicodeError, binascii.Error) as exc:
        raise AlasTokenError(f"{name} is invalid") from exc
    if len(key) != 32:
        raise AlasTokenError(f"{name} must decode to 32 bytes")
    return key


def _token_key_file_path() -> Path:
    """Return the server-side key path used when no key env value is injected."""
    configured = os.environ.get(TOKEN_KEY_FILE_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    data_dir = os.environ.get("WEB_SCRCPY_DATA_DIR", "data").strip() or "data"
    return Path(data_dir).expanduser() / TOKEN_KEY_FILE_NAME


def _read_provisioned_key() -> str:
    """Read a bounded, regular key file without accepting symlinked secrets."""
    path = _token_key_file_path()
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return ""
    except OSError as exc:
        raise AlasTokenError(f"{TOKEN_KEY_ENV} file is unavailable") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise AlasTokenError(f"{TOKEN_KEY_ENV} file is invalid")
    try:
        with path.open("r", encoding="ascii") as handle:
            value = handle.read(4097)
    except (OSError, UnicodeError) as exc:
        raise AlasTokenError(f"{TOKEN_KEY_ENV} file is unavailable") from exc
    if len(value) > 4096:
        raise AlasTokenError(f"{TOKEN_KEY_ENV} file is invalid")
    return value.strip()


def _current_key_raw() -> str:
    """Resolve the current key, preferring explicit process injection."""
    injected = os.environ.get(TOKEN_KEY_ENV, "")
    if injected.strip():
        return injected
    return _read_provisioned_key()


def _key_candidates() -> list[tuple[str, bytes]]:
    current = _current_key_raw()
    previous = os.environ.get(TOKEN_PREVIOUS_KEY_ENV, "")
    candidates = []
    if current.strip():
        candidates.append((TOKEN_KEY_ENV, _decode_key(current, TOKEN_KEY_ENV)))
    if previous.strip():
        candidates.append((TOKEN_PREVIOUS_KEY_ENV, _decode_key(previous, TOKEN_PREVIOUS_KEY_ENV)))
    return candidates


def injected_key_present() -> bool:
    """Whether the key arrives through the process environment."""
    return bool(os.environ.get(TOKEN_KEY_ENV, "").strip())


def _restrict_key_file(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError as exc:
        raise AlasTokenError(f"{TOKEN_KEY_ENV} file permissions could not be restricted") from exc


def provision_key_file() -> dict[str, object]:
    """Create the server-side key file once, mirroring deploy.sh's contract.

    Returns a redacted summary (never the key). An existing path is reused when
    it already holds a valid key, and rejected when it is a symlink, not a
    regular file, or holds an invalid key — provisioning never silently
    replaces a broken key, because the old one may still decrypt stored tokens.
    """
    path = _token_key_file_path()
    if path.is_symlink():
        raise AlasTokenError(f"{TOKEN_KEY_ENV} file must not be a symlink")
    if path.exists():
        if not path.is_file():
            raise AlasTokenError(f"{TOKEN_KEY_ENV} path is not a regular file")
        try:
            existing = path.read_text(encoding="ascii", errors="replace").strip()
            _decode_key(existing, TOKEN_KEY_ENV)
        except (OSError, UnicodeError) as exc:
            raise AlasTokenError(f"{TOKEN_KEY_ENV} file is unreadable") from exc
        except AlasTokenError as exc:
            raise AlasTokenError(
                f"{TOKEN_KEY_ENV} file is invalid; restore a valid matching key from backup"
            ) from exc
        _restrict_key_file(path)
        return {"ok": True, "action": "reused", "path": str(path), "source": "file"}

    path.parent.mkdir(parents=True, exist_ok=True)
    value = secrets.token_hex(32)
    temp_path = path.parent / f"{path.name}.{secrets.token_hex(4)}.tmp"
    descriptor = os.open(temp_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as handle:
            handle.write(value + "\n")
        os.replace(temp_path, path)
    except BaseException:
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise
    _restrict_key_file(path)
    return {"ok": True, "action": "created", "path": str(path), "source": "file"}


def key_is_configured() -> bool:
    try:
        return bool(_current_key_raw())
    except AlasTokenError:
        # An invalid but present file is still configured; callers can expose a
        # redacted "invalid" state instead of misreporting it as missing.
        return True


def key_is_valid(name: str = TOKEN_KEY_ENV) -> bool:
    """Return whether an injected or provisioned key decodes to 32 bytes."""
    try:
        raw = _current_key_raw() if name == TOKEN_KEY_ENV else os.environ.get(name, "")
    except AlasTokenError:
        return False
    if not raw.strip():
        return False
    try:
        _decode_key(raw, name)
    except AlasTokenError:
        return False
    return True


def encrypt_token(token: str) -> str:
    candidates = _key_candidates()
    if not candidates or candidates[0][0] != TOKEN_KEY_ENV:
        raise AlasTokenError(f"{TOKEN_KEY_ENV} is not configured")
    value = str(token or "")
    if not value:
        return ""
    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(candidates[0][1]).encrypt(nonce, value.encode("utf-8"), TOKEN_AAD)
    payload = base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii").rstrip("=")
    return TOKEN_PREFIX + payload


def is_encrypted_token(value: str) -> bool:
    return str(value or "").startswith(TOKEN_PREFIX)


def key_diagnostics() -> dict[str, object]:
    """Describe the key material this process can see — never the key itself.

    Used by startup warnings and the CLI status command so an operator can tell
    *which* key source is in play when a stored token cannot be decrypted
    (env var vs key file vs nothing, and whether a rotation key is present).
    """
    diagnostics: dict[str, object] = {
        "injected": injected_key_present(),
        "previous_injected": bool(os.environ.get(TOKEN_PREVIOUS_KEY_ENV, "").strip()),
        "key_file": "",
        "key_file_exists": False,
    }
    try:
        path = _token_key_file_path()
    except Exception:  # noqa: BLE001 - diagnostics must never raise
        return diagnostics
    diagnostics["key_file"] = str(path)
    try:
        diagnostics["key_file_exists"] = bool(path.is_file())
    except OSError:
        diagnostics["key_file_exists"] = False
    return diagnostics


def token_summary(value: str) -> dict[str, object]:
    """Redacted fingerprint of a stored token (ciphertext hash, never plaintext).

    Lets an operator match "the token in this database" against a backup or a
    key without ever printing the credential.
    """
    raw = str(value or "")
    if not raw:
        return {"present": False}
    return {
        "present": True,
        "encrypted": is_encrypted_token(raw),
        "length": len(raw),
        "fingerprint": hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:12],
    }


def decrypt_token(value: str, *, allow_legacy: bool = False) -> tuple[str, str]:
    """Decrypt a stored token, requiring an explicit opt-in for legacy data.

    Legacy plaintext values are accepted only by the one-time storage
    migration path.  Normal runtime callers must never receive plaintext
    credentials from an un-migrated database or configuration file.
    """
    raw = str(value or "")
    if not raw:
        return "", TOKEN_KEY_ENV
    if not is_encrypted_token(raw):
        if not allow_legacy:
            raise AlasTokenError("legacy ALAS token requires migration")
        return raw, "legacy"
    encoded = raw[len(TOKEN_PREFIX):]
    try:
        payload = _decode_urlsafe_base64(encoded)
    except (ValueError, UnicodeError, binascii.Error) as exc:
        raise AlasTokenError("ALAS token ciphertext is invalid") from exc
    if len(payload) <= 12:
        raise AlasTokenError("ALAS token ciphertext is invalid")
    nonce, ciphertext = payload[:12], payload[12:]
    candidates = _key_candidates()
    if not candidates:
        raise AlasTokenError(f"{TOKEN_KEY_ENV} is not configured")
    for name, key in candidates:
        try:
            plaintext = AESGCM(key).decrypt(nonce, ciphertext, TOKEN_AAD)
            return plaintext.decode("utf-8"), name
        except Exception:
            continue
    raise AlasTokenError("ALAS token ciphertext cannot be authenticated")
