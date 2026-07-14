import asyncio
import logging
import os
import time
from typing import Any

from adb_manager import ADBManager

from . import storage

log = logging.getLogger("webscrcpy.adb")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)) or default)
    except Exception:
        return default


def _bool_value(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def adb_state_label(raw_state: str | None) -> str:
    state = (raw_state or "").strip().lower()
    if state == "device":
        return "online"
    if state in ("offline", "unauthorized"):
        return state
    return "unknown"


def _now() -> int:
    return int(time.time())


class AdbDeviceMonitor:
    def __init__(self) -> None:
        self.enabled = _env_bool("ADB_AUTOCONNECT", True)
        self.interval = max(3, _env_int("ADB_HEARTBEAT_INTERVAL", 15))
        self.connect_timeout = max(2, _env_int("ADB_CONNECT_TIMEOUT", 8))
        self.reconnect_backoff = max(1, _env_int("ADB_RECONNECT_BACKOFF", 5))
        self._task: asyncio.Task | None = None
        self._device_locks: dict[str, asyncio.Lock] = {}
        self._statuses: dict[str, dict[str, Any]] = {}

    async def start(self) -> None:
        if not self.enabled or self._task:
            return
        log.info("ADB_MONITOR_START interval=%s timeout=%s backoff=%s", self.interval, self.connect_timeout, self.reconnect_backoff)
        self._task = asyncio.create_task(self._run(), name="adb-device-monitor")

    async def stop(self) -> None:
        if not self._task:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        log.info("ADB_MONITOR_STOP")

    def snapshot(self, device_id: str | None = None) -> dict[str, Any]:
        if device_id:
            return dict(self._statuses.get(device_id) or self._default_status(device_id))
        return {key: dict(value) for key, value in self._statuses.items()}

    async def reconnect_device(self, device_id: str) -> dict[str, Any]:
        device = storage.get_device(device_id)
        if not device:
            return self._set_status(device_id, "unknown", ok=False, detail="device not found")
        return await self.ensure_connected(dict(device), force=True)

    async def ensure_connected(self, device: dict[str, Any], force: bool = False) -> dict[str, Any]:
        device_id = str(device.get("id") or "").strip()
        address = str(device.get("address") or device_id).strip()
        if not device_id:
            return {"ok": False, "state": "unknown", "detail": "device id is empty"}
        lock = self._device_locks.setdefault(device_id, asyncio.Lock())
        async with lock:
            persisted = storage.get_device(device_id)
            if persisted:
                device = dict(persisted)
                address = str(device.get("address") or device_id).strip()
            if not _bool_value(device.get("enabled"), True):
                return self._set_status(device_id, "disabled", ok=False, detail="device disabled")

            current = self._statuses.get(device_id) or {}
            if (
                not force
                and current.get("state") == "online"
                and _now() - int(current.get("last_checked_at") or 0) < self.reconnect_backoff
            ):
                return dict(current)

            if force or current.get("state") != "online":
                self._set_status(device_id, "reconnecting", ok=False, detail="checking adb state")
            try:
                result = await asyncio.to_thread(self._probe, device_id, address)
            except Exception as exc:
                log.exception("ADB_PROBE_ERROR device=%s", device_id)
                result = {"ok": False, "state": "unknown", "detail": str(exc)}
            return self._set_status(
                device_id,
                result["state"],
                ok=result["ok"],
                detail=result["detail"],
                address=address,
            )

    async def check_all(self) -> None:
        for device in storage.list_all_devices():
            await self.ensure_connected(dict(device))

    async def _run(self) -> None:
        while True:
            try:
                await self.check_all()
            except Exception as exc:
                # Keep the monitor alive; individual failures are reflected on next pass.
                log.exception("ADB_MONITOR_ERROR error=%s", exc)
            await asyncio.sleep(self.interval)

    def _probe(self, device_id: str, address: str) -> dict[str, Any]:
        adb = ADBManager()
        detail: list[str] = []
        target = address or device_id
        state = adb.get_device_state(target, timeout=self.connect_timeout)
        if state != "device" and target and ":" in target:
            ok, output = adb._run_adb_command(["connect", target], timeout=self.connect_timeout)
            if output:
                detail.append(output.strip())
            state = adb.get_device_state(target, timeout=self.connect_timeout)
            if not ok and not state:
                return {"ok": False, "state": "unknown", "detail": "\n".join(detail)}

        label = adb_state_label(state)
        if label == "unknown":
            for item in adb.get_devices(timeout=self.connect_timeout):
                if item.get("id") in {device_id, address}:
                    label = adb_state_label(item.get("state"))
                    detail.append(f"{item.get('id')} {item.get('state')}")
                    break
        if not detail and state:
            detail.append(f"{target}: {state}")
        return {"ok": label == "online", "state": label, "detail": "\n".join(item for item in detail if item)}

    def _default_status(self, device_id: str) -> dict[str, Any]:
        return {
            "device_id": device_id,
            "ok": False,
            "state": "unknown",
            "adb_state": "unknown",
            "adb_ok": False,
            "status_label": "unknown",
            "detail": "",
            "last_error": "",
            "last_checked_at": None,
            "last_seen_at": None,
        }

    def _set_status(
        self,
        device_id: str,
        state: str,
        *,
        ok: bool,
        detail: str = "",
        address: str = "",
    ) -> dict[str, Any]:
        now = _now()
        old = self._statuses.get(device_id) or {}
        status = {
            "device_id": device_id,
            "address": address or old.get("address", ""),
            "ok": bool(ok),
            "state": state,
            "adb_state": state,
            "adb_ok": bool(ok),
            "status_label": state,
            "detail": detail,
            "last_error": "" if ok else detail,
            "last_checked_at": now,
            "last_seen_at": now if ok else old.get("last_seen_at"),
        }
        self._statuses[device_id] = status
        if old.get("state") != state or old.get("ok") != bool(ok):
            log.info("ADB_STATUS device=%s state=%s ok=%s detail=%s", device_id, state, bool(ok), detail)
        return dict(status)


adb_monitor = AdbDeviceMonitor()
