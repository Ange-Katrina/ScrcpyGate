import re
import sqlite3
from typing import Any

from . import i18n, storage

# 网络 ADB 地址必须带端口；只有点号而没有冒号的输入通常是漏写了端口
# （例如把 192.0.2.10:5555 写成 192.0.2.105555），USB 序列号不含点号。
_HOST_WITHOUT_PORT = re.compile(r"^[0-9A-Za-z](?:[0-9A-Za-z.\-]*[0-9A-Za-z])?$")
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_BRACKET_ADDRESS = re.compile(r"^\[(?P<host>[^\]]+)\]:(?P<port>\d{1,6})$")

MAX_ADDRESS_LENGTH = 128
MAX_DEVICE_NAME_LENGTH = 64


def address_needs_port(address: Any) -> bool:
    """Return True when the value looks like a network host missing its port."""
    value = str(address or "").strip()
    if not value or ":" in value or "." not in value:
        return False
    return bool(_HOST_WITHOUT_PORT.match(value))


def address_error(address: Any) -> str | None:
    """Validate an ADB target address; returns an error token or None.

    Covers the gaps left by ``address_needs_port``: control characters (which
    would corrupt logs and JSON), unbounded length, and an out-of-range or
    non-numeric port (a device saved with ``:999999`` stays permanently
    offline, the same failure class as ISSUE-062).
    """
    value = str(address or "").strip()
    if not value:
        return "address_required"
    if len(value) > MAX_ADDRESS_LENGTH or _CONTROL_CHARS.search(value):
        return "address_invalid"
    if address_needs_port(value):
        return "address_needs_port"
    match = _BRACKET_ADDRESS.match(value)
    if match:
        port = int(match.group("port"))
    elif value.count(":") == 1:
        host, _, tail = value.partition(":")
        if not host.strip():
            return "address_invalid"
        if not tail.isdigit():
            return "address_port_invalid"
        port = int(tail)
    else:
        # USB serial (no colon) or an unbracketed IPv6 literal: no port to check.
        return None
    if not 1 <= port <= 65535:
        return "address_port_invalid"
    return None


def device_name_error(name: Any) -> str | None:
    """Reject control characters and over-long device names."""
    value = str(name or "").strip()
    if len(value) > MAX_DEVICE_NAME_LENGTH or _CONTROL_CHARS.search(value):
        return "device_name_invalid"
    return None


def device_id_of(device: dict[str, Any]) -> str:
    return str(device.get("device_id") or device.get("id") or device.get("address") or "").strip()


def bool_value(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    return str(value).strip().lower() in ("1", "true", "yes", "on")


PUBLIC_ADB_FIELDS = (
    "ok",
    "state",
    "adb_state",
    "adb_ok",
    "status_label",
    "last_checked_at",
    "last_seen_at",
    "latency_ms",
)


def public_adb_payload(status: dict[str, Any] | None, exposed_id: str) -> dict[str, Any] | None:
    if not isinstance(status, dict):
        return None
    payload = {key: status.get(key) for key in PUBLIC_ADB_FIELDS if key in status}
    payload["device_id"] = exposed_id
    if "detail" in status:
        payload["detail"] = "" if status.get("ok") else i18n.translate("server.status.adb_unavailable")
    if "last_error" in status:
        payload["last_error"] = "" if status.get("ok") else i18n.translate("server.status.adb_unavailable")
    return payload


def public_lock_payload(lock: dict[str, Any] | None, exposed_id: str) -> dict[str, Any] | None:
    if not isinstance(lock, dict):
        return None
    payload = {key: lock.get(key) for key in ("username", "acquired_at", "expires_at") if key in lock}
    payload["device_id"] = exposed_id
    return payload


def session_payload(
    session: dict[str, Any] | None,
    device_id: str,
    exposed_id: str,
    *,
    public: bool = False,
) -> dict[str, Any] | None:
    if not session:
        return None
    data = dict(session)
    data["device_id"] = exposed_id
    if public:
        data.pop("address", None)
        data.pop("real_device_id", None)
        if "last_error" in data:
            data["last_error"] = "" if not data.get("last_error") else i18n.translate("server.status.video_stream_unavailable")
        if "adb" in data:
            data["adb"] = public_adb_payload(data.get("adb"), exposed_id)
        if "control_lock" in data:
            data["control_lock"] = public_lock_payload(data.get("control_lock"), exposed_id)
    return data


def device_payload(
    device: dict[str, Any],
    sessions: dict[str, Any] | None = None,
    statuses: dict[str, Any] | None = None,
    *,
    include_address: bool = True,
    public_id: bool = False,
) -> dict[str, Any]:
    sessions = sessions or {}
    statuses = statuses or {}
    real_id = device_id_of(device)
    exposed_id = storage.public_device_id(real_id) if public_id else real_id
    session = session_payload(
        sessions.get(real_id) or device.get("session") or None,
        real_id,
        exposed_id,
        public=public_id or not include_address,
    )
    status = dict(statuses.get(real_id) or {})
    name = str(device.get("name") or "Device").strip() or "Device"
    payload = {
        "id": exposed_id,
        "device_id": exposed_id,
        "name": name,
        "display_name": name,
        "enabled": bool_value(device.get("enabled"), True),
        "can_view": bool_value(device.get("can_view"), True),
        "can_control": bool_value(device.get("can_control"), True),
        "adb_state": status.get("adb_state") or status.get("state") or "unknown",
        "adb_ok": bool(status.get("adb_ok") or status.get("ok")),
        "status_label": status.get("status_label") or status.get("state") or "unknown",
        "last_checked_at": status.get("last_checked_at"),
        "last_seen_at": status.get("last_seen_at"),
        "latency_ms": status.get("latency_ms"),
        "session": session,
    }
    if include_address:
        try:
            payload["public_id"] = storage.public_device_id(real_id)
        except (OSError, RuntimeError, sqlite3.OperationalError):
            # Pure payload tests may not initialize the storage salt yet; the
            # running service always has a database and returns the opaque ID.
            payload["public_id"] = real_id
        payload["address"] = str(device.get("address") or real_id)
        payload["real_device_id"] = real_id
        payload["adb_detail"] = status.get("detail", "")
        payload["last_error"] = status.get("last_error", "")
    return payload


def devices_payload(
    devices: list[dict[str, Any]],
    sessions: dict[str, Any] | None = None,
    statuses: dict[str, Any] | None = None,
    *,
    include_address: bool = True,
    public_id: bool = False,
) -> list[dict[str, Any]]:
    return [device_payload(device, sessions, statuses, include_address=include_address, public_id=public_id) for device in devices]


def sessions_payload(
    devices: list[dict[str, Any]],
    sessions: dict[str, Any] | None = None,
    *,
    public_id: bool = False,
) -> dict[str, Any]:
    sessions = sessions or {}
    payload: dict[str, Any] = {}
    for device in devices:
        real_id = device_id_of(device)
        if real_id not in sessions:
            continue
        exposed_id = storage.public_device_id(real_id) if public_id else real_id
        payload[exposed_id] = session_payload(sessions[real_id], real_id, exposed_id, public=public_id)
    return payload
