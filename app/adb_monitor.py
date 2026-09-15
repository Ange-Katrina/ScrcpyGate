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


def _adb_error_detail(adb: ADBManager) -> str:
    """Return a safe string for optional diagnostics from real or mocked ADB clients."""
    value = getattr(adb, "last_error", "")
    return value if isinstance(value, str) else ""


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
        # ADB_AUTOCONNECT only gates the `adb connect` fallback. The heartbeat
        # (TCP probe + `adb get-state`) always runs so the UI status stays fresh.
        self.autoconnect = _env_bool("ADB_AUTOCONNECT", True)
        self.interval = max(3, _env_int("ADB_HEARTBEAT_INTERVAL", 15))
        # One synchronous pass during startup initialises the status cache so the
        # first page load already shows real device state. Bounded so a slow or
        # offline device cannot hold the service startup hostage (0 disables).
        self.startup_probe_timeout = max(0.0, float(_env_int("ADB_STARTUP_PROBE_TIMEOUT", 10)))
        self.connect_timeout = max(2, _env_int("ADB_CONNECT_TIMEOUT", 8))
        self.reconnect_backoff = max(1, _env_int("ADB_RECONNECT_BACKOFF", 5))
        self.concurrency = max(1, min(32, _env_int("ADB_HEARTBEAT_CONCURRENCY", 4)))
        # 跟随设备转屏：scrcpy 起流时把采集方向锁定在那一刻，之后设备自己转了方向不会
        # 自动改变画面。这里按更短的周期读一次「正在投屏的设备」的显示方向，变了就重启
        # 一次采集（约 0.7s，观看端会拿到补发的关键帧）。0 表示关闭这个跟随。
        self.rotation_interval = max(0, _env_int("ADB_ROTATION_POLL_INTERVAL", 3))
        self._task: asyncio.Task | None = None
        self._device_locks: dict[str, asyncio.Lock] = {}
        self._statuses: dict[str, dict[str, Any]] = {}
        self._generations: dict[str, int] = {}
        self._retired_devices: set[str] = set()
        self._operation_counts: dict[str, int] = {}

    async def start(self) -> None:
        if self._task:
            return
        log.info(
            "ADB_MONITOR_START interval=%s timeout=%s backoff=%s autoconnect=%s startup_probe_timeout=%s",
            self.interval,
            self.connect_timeout,
            self.reconnect_backoff,
            self.autoconnect,
            self.startup_probe_timeout,
        )
        if self.startup_probe_timeout > 0:
            try:
                await asyncio.wait_for(self.check_all(), timeout=self.startup_probe_timeout)
            except asyncio.TimeoutError:
                log.warning("ADB_STARTUP_PROBE_TIMEOUT timeout=%s", self.startup_probe_timeout)
            except Exception:
                log.exception("ADB_STARTUP_PROBE_FAILED")
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

    def _cleanup_retired_device(self, device_id: str) -> None:
        """Drop lifecycle state after a deleted device has no active callers."""
        if self._operation_counts.get(device_id) or device_id not in self._retired_devices:
            return
        self._device_locks.pop(device_id, None)
        self._generations.pop(device_id, None)
        self._retired_devices.discard(device_id)
        self._statuses.pop(device_id, None)

    def _begin_operation(self, device_id: str) -> asyncio.Lock:
        lock = self._device_locks.setdefault(device_id, asyncio.Lock())
        self._operation_counts[device_id] = self._operation_counts.get(device_id, 0) + 1
        return lock

    def _end_operation(self, device_id: str, lock: asyncio.Lock) -> None:
        count = self._operation_counts.get(device_id, 0)
        if count <= 1:
            self._operation_counts.pop(device_id, None)
        else:
            self._operation_counts[device_id] = count - 1
            return
        # Only remove the lock that this operation registered. A newer operation
        # may have installed a replacement after a previous lifecycle ended.
        if self._device_locks.get(device_id) is lock:
            self._cleanup_retired_device(device_id)

    def forget_device(self, device_id: str) -> None:
        self._generations[device_id] = self._generations.get(device_id, 0) + 1
        self._retired_devices.add(device_id)
        self._statuses.pop(device_id, None)
        self._cleanup_retired_device(device_id)

    def invalidate_device(self, device_id: str) -> None:
        """Discard cached and in-flight results without retiring the device ID."""
        self._generations[device_id] = self._generations.get(device_id, 0) + 1
        self._statuses.pop(device_id, None)

    def restore_device(self, device_id: str) -> None:
        self._retired_devices.discard(device_id)
        self.invalidate_device(device_id)

    async def reconnect_device(self, device_id: str) -> dict[str, Any]:
        device = await asyncio.to_thread(storage.get_device, device_id)
        if not device:
            self.forget_device(device_id)
            return self._default_status(device_id)
        return await self.ensure_connected(dict(device), force=True)

    async def ensure_connected(self, device: dict[str, Any], force: bool = False) -> dict[str, Any]:
        device_id = str(device.get("id") or "").strip()
        address = str(device.get("address") or device_id).strip()
        if not device_id:
            return {"ok": False, "state": "unknown", "detail": "device id is empty"}
        lock = self._begin_operation(device_id)
        try:
            async with lock:
                if device_id in self._retired_devices:
                    return self._default_status(device_id)
                generation = self._generations.get(device_id, 0)
                persisted = await asyncio.to_thread(storage.get_device, device_id)
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
                    or not await asyncio.to_thread(storage.get_device, device_id)
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
        finally:
            self._end_operation(device_id, lock)

    async def check_all(self) -> None:
        semaphore = asyncio.Semaphore(self.concurrency)

        async def check(device: dict[str, Any]) -> None:
            async with semaphore:
                await self.ensure_connected(dict(device))

        devices = await asyncio.to_thread(storage.list_all_devices)
        await asyncio.gather(*(check(dict(device)) for device in devices))

    async def _run(self) -> None:
        last_full_pass = 0.0
        poll = self.rotation_interval if self.rotation_interval > 0 else self.interval
        while True:
            try:
                now = time.monotonic()
                if now - last_full_pass >= self.interval:
                    last_full_pass = now
                    await self.check_all()
                if self.rotation_interval > 0:
                    await self.sync_display_rotation()
            except Exception as exc:
                # Keep the monitor alive; individual failures are reflected on next pass.
                log.exception("ADB_MONITOR_ERROR error=%s", exc)
            await asyncio.sleep(poll)

    async def sync_display_rotation(self) -> dict[str, Any]:
        """重启「设备自己转了方向」的采集，让画面跟着设备转。

        scrcpy 在起流时确定采集方向（3.x 的 capture orientation 默认锁在起始方向），
        之后设备转屏不会改变已经跑着的编码器。这里拿会话里记录的「起流时方向」当基线，
        读到当前方向不同就重启该设备的采集 —— 观看端由既有重启路径补发关键帧。
        """
        from .mirror_manager import manager

        result: dict[str, Any] = {"checked": 0, "rotated": [], "failed": []}
        try:
            sessions = await manager.snapshot()
        except Exception:
            return result
        for device_id, session in sessions.items():
            if not session or not session.get("running"):
                continue
            if not session.get("viewer_count"):
                continue
            baseline = session.get("capture_display_rotation")
            if baseline is None:
                # 起流时读不到方向（老会话 / 设备不支持）：不猜，直接跳过。
                continue
            stored = await asyncio.to_thread(storage.get_device, device_id)
            if not stored:
                continue
            # storage 返回 sqlite3.Row（没有 .get），先转成普通 dict。
            device = dict(stored)
            if not _bool_value(device.get("enabled"), True):
                continue
            address = str(device.get("address") or device_id)
            result["checked"] += 1
            rotation = await asyncio.to_thread(ADBManager().device_display_rotation, address)
            if rotation is None:
                result["failed"].append({"device_id": device_id, "reason": "rotation_unknown"})
                continue
            if rotation == baseline:
                continue
            outcome = await manager.restart_for_display_rotation(device_id)
            entry = {"device_id": device_id, "from": baseline, "to": rotation, **outcome}
            if outcome.get("ok"):
                result["rotated"].append(entry)
                log.info("MIRROR_DISPLAY_ROTATION device=%s from=%s to=%s", device_id, baseline, rotation)
            else:
                result["failed"].append(entry)
        if result["checked"] or result["failed"]:
            log.debug(
                "MIRROR_DISPLAY_ROTATION_CHECK checked=%s rotated=%s failed=%s",
                result["checked"],
                len(result["rotated"]),
                len(result["failed"]),
            )
        return result

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
        adb_detail = _adb_error_detail(adb)
        # ADB_AUTOCONNECT=false 时只做只读探测（TCP + get-state），不主动 connect；
        # 设备需在宿主机侧预先 adb connect。
        if state != "device" and endpoint and self.autoconnect:
            ok, output = adb._run_adb_command(["connect", target], timeout=self.connect_timeout)
            if output:
                detail.append(output.strip())
            current_error = _adb_error_detail(adb)
            if current_error:
                adb_detail = current_error
            state = adb.get_device_state(target, timeout=self.connect_timeout)
            adb_detail = _adb_error_detail(adb) or adb_detail
            if not ok and not state:
                if adb_detail and adb_detail not in detail:
                    detail.append(adb_detail)
                return {"ok": False, "state": "offline", "detail": "\n".join(detail), "latency_ms": latency_ms}

        label = adb_state_label(state)
        if label == "unknown":
            devices = adb.get_devices(timeout=self.connect_timeout)
            adb_detail = _adb_error_detail(adb) or adb_detail
            for item in devices:
                if item.get("id") in {device_id, address}:
                    label = adb_state_label(item.get("state"))
                    detail.append(f"{item.get('id')} {item.get('state')}")
                    break
        if not detail and state:
            detail.append(f"{target}: {state}")
        if not detail and adb_detail:
            detail.append(adb_detail)
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
