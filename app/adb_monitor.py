import asyncio
import logging
import os
import socket
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


def adb_tcp_endpoint(address: str) -> tuple[str, int] | None:
    """Parse network ADB targets without treating USB/emulator serials as hosts."""
    target = (address or "").strip()
    if not target:
        return None
    host = ""
    port_text = ""
    if target.startswith("["):
        closing = target.find("]")
        if closing <= 1 or closing + 1 >= len(target) or target[closing + 1] != ":":
            return None
        host = target[1:closing]
        port_text = target[closing + 2 :]
    else:
        if target.count(":") != 1:
            return None
        host, port_text = target.rsplit(":", 1)
    try:
        port = int(port_text)
    except (TypeError, ValueError):
        return None
    if not host or not 1 <= port <= 65535:
        return None
    return host, port


class AdbDeviceMonitor:
    def __init__(self) -> None:
        self.enabled = _env_bool("ADB_AUTOCONNECT", True)
        self.interval = max(3, _env_int("ADB_HEARTBEAT_INTERVAL", 15))
        self.connect_timeout = max(2, _env_int("ADB_CONNECT_TIMEOUT", 8))
        self.reconnect_backoff = max(1, _env_int("ADB_RECONNECT_BACKOFF", 5))
        self.concurrency = max(1, min(32, _env_int("ADB_HEARTBEAT_CONCURRENCY", 4)))
        self._task: asyncio.Task | None = None
        self._device_locks: dict[str, asyncio.Lock] = {}
        self._statuses: dict[str, dict[str, Any]] = {}
        self._generations: dict[str, int] = {}
        self._retired_devices: set[str] = set()

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

    def forget_device(self, device_id: str) -> None:
        self._generations[device_id] = self._generations.get(device_id, 0) + 1
        self._retired_devices.add(device_id)
        self._statuses.pop(device_id, None)

    def invalidate_device(self, device_id: str) -> None:
        """Discard cached and in-flight results without retiring the device ID."""
        self._generations[device_id] = self._generations.get(device_id, 0) + 1
        self._statuses.pop(device_id, None)

    def restore_device(self, device_id: str) -> None:
        self._retired_devices.discard(device_id)
        self.invalidate_device(device_id)

    async def reconnect_device(self, device_id: str) -> dict[str, Any]:
        device = storage.get_device(device_id)
        if not device:
            self.forget_device(device_id)
            return self._default_status(device_id)
        return await self.ensure_connected(dict(device), force=True)

    async def ensure_connected(self, device: dict[str, Any], force: bool = False) -> dict[str, Any]:
        device_id = str(device.get("id") or "").strip()
        address = str(device.get("address") or device_id).strip()
        if not device_id:
            return {"ok": False, "state": "unknown", "detail": "device id is empty"}
        lock = self._device_locks.setdefault(device_id, asyncio.Lock())
        async with lock:
            if device_id in self._retired_devices:
                return self._default_status(device_id)
            generation = self._generations.get(device_id, 0)
            persisted = storage.get_device(device_id)
            if not persisted:
                return self._default_status(device_id)
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
                self._set_status(device_id, "checking", ok=False, detail="checking network and adb state")
            try:
                result = await asyncio.to_thread(self._probe, device_id, address)
            except Exception as exc:
                log.exception("ADB_PROBE_ERROR device=%s", device_id)
                result = {"ok": False, "state": "unknown", "detail": str(exc)}
            if (
                device_id in self._retired_devices
                or generation != self._generations.get(device_id, 0)
                or not storage.get_device(device_id)
            ):
                return self._default_status(device_id)
            return self._set_status(
                device_id,
                result["state"],
                ok=result["ok"],
                detail=result["detail"],
                address=address,
                latency_ms=result.get("latency_ms"),
            )

    async def check_all(self) -> None:
        semaphore = asyncio.Semaphore(self.concurrency)

        async def check(device: dict[str, Any]) -> None:
            async with semaphore:
                await self.ensure_connected(dict(device))

        await asyncio.gather(*(check(dict(device)) for device in storage.list_all_devices()))

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
        endpoint = adb_tcp_endpoint(target)
        latency_ms: float | None = None
        if endpoint:
            started = time.monotonic()
            try:
                with socket.create_connection(endpoint, timeout=min(float(self.connect_timeout), 3.0)):
                    latency_ms = round((time.monotonic() - started) * 1000, 1)
            except OSError as exc:
                return {
                    "ok": False,
                    "state": "network_unreachable",
                    "detail": str(exc),
                    "latency_ms": None,
                }
        state = adb.get_device_state(target, timeout=self.connect_timeout)
        if state != "device" and endpoint:
            ok, output = adb._run_adb_command(["connect", target], timeout=self.connect_timeout)
            if output:
                detail.append(output.strip())
            state = adb.get_device_state(target, timeout=self.connect_timeout)
            if not ok and not state:
                return {"ok": False, "state": "offline", "detail": "\n".join(detail), "latency_ms": latency_ms}

        label = adb_state_label(state)
        if label == "unknown":
            for item in adb.get_devices(timeout=self.connect_timeout):
                if item.get("id") in {device_id, address}:
                    label = adb_state_label(item.get("state"))
                    detail.append(f"{item.get('id')} {item.get('state')}")
                    break
        if not detail and state:
            detail.append(f"{target}: {state}")
        if label == "unknown":
            label = "offline"
        return {
            "ok": label == "online",
            "state": label,
            "detail": "\n".join(item for item in detail if item),
            "latency_ms": latency_ms,
        }

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
            "latency_ms": None,
        }

    def _set_status(
        self,
        device_id: str,
        state: str,
        *,
        ok: bool,
        detail: str = "",
        address: str = "",
        latency_ms: float | None = None,
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
            "latency_ms": latency_ms,
        }
        self._statuses[device_id] = status
        if old.get("state") != state or old.get("ok") != bool(ok):
            log.info("ADB_STATUS device=%s state=%s ok=%s detail=%s", device_id, state, bool(ok), detail)
        return dict(status)


adb_monitor = AdbDeviceMonitor()
