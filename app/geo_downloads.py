"""Private GeoIP download preferences; proxy readback is restricted to admin routes."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import tempfile
from urllib.parse import urlsplit

FILE_NAME = ".geo-downloads.json"
EDITIONS = ("GeoLite2-Country", "GeoLite2-City")
DEFAULT = {"source": "github", "editions": ["GeoLite2-City"], "proxy_mode": "system", "proxy_url": ""}


class DownloadConfigError(ValueError):
    """Only stable codes, never include supplied addresses or credentials."""


def _path() -> Path:
    return Path(os.environ.get("WEB_SCRCPY_DATA_DIR", "data") or "data") / FILE_NAME


def validate_proxy(value: object) -> str:
    if not isinstance(value, str) or len(value) > 2048:
        raise DownloadConfigError("proxy_invalid")
    value = value.strip()
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.port is None or not 1 <= parsed.port <= 65535
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment
                or re.search(r"[\s\\\x00-\x1f\x7f]", value)
                or re.search(r"%(?![0-9a-fA-F]{2})", value)
                or re.search(r"[\x00-\x20\x7f]", parsed.hostname)
                or (parsed.username is not None and not parsed.username)):
            raise ValueError
        # Avoid parser differences (especially encoded authority delimiters).
        if "%" in parsed.hostname or "@" in parsed.hostname:
            raise ValueError
        parsed.hostname.encode("idna")
    except (ValueError, UnicodeError):
        raise DownloadConfigError("proxy_invalid") from None
    return value


def _validate(data: object) -> dict:
    if (not isinstance(data, dict) or not {"editions", "proxy_mode", "proxy_url"} <= set(data)
            or set(data) - {"source", "editions", "proxy_mode", "proxy_url"}):
        raise DownloadConfigError("download_settings_invalid")
    source = data.get("source", DEFAULT["source"])
    if not isinstance(source, str) or source not in ("github", "maxmind"):
        raise DownloadConfigError("download_source_invalid")
    editions = data["editions"]
    if (not isinstance(editions, list) or len(editions) > 2
            or any(not isinstance(item, str) or item not in EDITIONS for item in editions)
            or len(set(editions)) != len(editions)):
        raise DownloadConfigError("download_editions_invalid")
    mode = data["proxy_mode"]
    if not isinstance(mode, str) or mode not in ("system", "direct", "custom"):
        raise DownloadConfigError("proxy_mode_invalid")
    proxy = validate_proxy(data["proxy_url"])
    if mode == "custom" and not proxy:
        raise DownloadConfigError("proxy_required")
    return {"source": source, "editions": [item for item in EDITIONS if item in editions], "proxy_mode": mode, "proxy_url": proxy}


def resolve() -> dict:
    try:
        path = _path()
        meta = path.lstat()
        if not stat.S_ISREG(meta.st_mode) or meta.st_size > 8192:
            raise DownloadConfigError("download_settings_unreadable")
        if os.name != "nt" and meta.st_mode & 0o077:
            raise DownloadConfigError("download_settings_permissions")
        with path.open(encoding="utf-8") as handle:
            raw = handle.read(8193)
        if len(raw) > 8192:
            raise DownloadConfigError("download_settings_unreadable")
        return _validate(json.loads(raw))
    except FileNotFoundError:
        return {**DEFAULT, "editions": list(DEFAULT["editions"])}
    except DownloadConfigError:
        raise
    except (OSError, UnicodeError, ValueError):
        raise DownloadConfigError("download_settings_unreadable") from None


def public_status() -> dict:
    try:
        data = resolve()
    except DownloadConfigError as exc:
        return {"source": DEFAULT["source"], "editions": [], "proxy_mode": "system", "proxy_configured": False, "error": str(exc)}
    return {"source": data["source"], "editions": data["editions"], "proxy_mode": data["proxy_mode"],
            "proxy_configured": bool(data["proxy_url"]), "error": ""}


def admin_status() -> dict:
    """Called only after administrator authentication; never use for logs/audits."""
    try:
        data = resolve()
    except DownloadConfigError as exc:
        return {"source": DEFAULT["source"], "editions": [], "proxy_mode": "system", "proxy_configured": False, "proxy_url": "", "error": str(exc)}
    return {**data, "proxy_configured": bool(data["proxy_url"]), "error": ""}


def save(editions: object, mode: object, proxy: object = "", *, clear_proxy: bool = False, source: object = None) -> None:
    if type(clear_proxy) is not bool or not isinstance(proxy, str):
        raise DownloadConfigError("download_settings_invalid")
    if clear_proxy and proxy.strip():
        raise DownloadConfigError("download_settings_invalid")
    proxy = proxy.strip()
    if not proxy and not clear_proxy:
        proxy = resolve()["proxy_url"]
    if source is None:
        source = resolve()["source"]
    data = _validate({"source": source, "editions": editions, "proxy_mode": mode, "proxy_url": "" if clear_proxy else proxy})
    path = _path()
    temp = None
    try:
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise DownloadConfigError("download_settings_unreadable")
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=".geo-downloads-", suffix=".tmp", dir=path.parent)
        temp = Path(name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            os.chmod(temp, 0o600)
            json.dump(data, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except OSError:
        raise DownloadConfigError("download_settings_write_failed") from None
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
