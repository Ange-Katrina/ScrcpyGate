from typing import Any

from . import storage


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


def session_payload(session: dict[str, Any] | None, device_id: str, exposed_id: str) -> dict[str, Any] | None:
    if not session:
        return None
    data = dict(session)
    data["device_id"] = exposed_id
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
    session = session_payload(sessions.get(real_id) or device.get("session") or None, real_id, exposed_id)
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
        "session": session,
    }
    if include_address:
        payload["address"] = str(device.get("address") or real_id)
        payload["real_device_id"] = real_id
        payload["adb_detail"] = status.get("detail", "")
        payload["last_error"] = status.get("last_error", "")
        payload["last_checked_at"] = status.get("last_checked_at")
        payload["last_seen_at"] = status.get("last_seen_at")
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
        payload[exposed_id] = session_payload(sessions[real_id], real_id, exposed_id)
    return payload
