import asyncio
import threading
import unittest
from unittest.mock import MagicMock, patch

from app.adb_monitor import AdbDeviceMonitor, adb_tcp_endpoint


class AdbDeviceMonitorTests(unittest.IsolatedAsyncioTestCase):
    def test_tcp_endpoint_parser_supports_hosts_and_bracketed_ipv6(self):
        self.assertEqual(adb_tcp_endpoint("example.test:5555"), ("example.test", 5555))
        self.assertEqual(adb_tcp_endpoint("[2001:db8::10]:30100"), ("2001:db8::10", 30100))
        self.assertIsNone(adb_tcp_endpoint("emulator-5554"))
        self.assertIsNone(adb_tcp_endpoint("2001:db8::10:30100"))
        self.assertIsNone(adb_tcp_endpoint("example.test:70000"))

    def test_tcp_failure_is_network_unreachable_before_adb_commands(self):
        monitor = AdbDeviceMonitor()
        adb = MagicMock()
        with (
            patch("app.adb_monitor.ADBManager", return_value=adb),
            patch("app.adb_monitor.socket.create_connection", side_effect=OSError("refused")),
        ):
            result = monitor._probe("dev-a", "192.0.2.10:30100")

        self.assertEqual(result["state"], "network_unreachable")
        self.assertFalse(result["ok"])
        adb.get_device_state.assert_not_called()

    def test_reachable_adb_port_reports_online_and_latency(self):
        monitor = AdbDeviceMonitor()
        adb = MagicMock()
        adb.get_device_state.return_value = "device"
        connection = MagicMock()
        with (
            patch("app.adb_monitor.ADBManager", return_value=adb),
            patch("app.adb_monitor.socket.create_connection", return_value=connection),
            patch("app.adb_monitor.time.monotonic", side_effect=[10.0, 10.012]),
        ):
            result = monitor._probe("dev-a", "192.0.2.10:30100")

        self.assertEqual(result["state"], "online")
        self.assertEqual(result["latency_ms"], 12.0)
        connection.__enter__.assert_called_once()

    def test_reachable_port_without_adb_transport_is_offline(self):
        monitor = AdbDeviceMonitor()
        adb = MagicMock()
        adb.get_device_state.return_value = None
        adb._run_adb_command.return_value = (False, "cannot connect")
        with (
            patch("app.adb_monitor.ADBManager", return_value=adb),
            patch("app.adb_monitor.socket.create_connection", return_value=MagicMock()),
        ):
            result = monitor._probe("dev-a", "192.0.2.10:30100")

        self.assertEqual(result["state"], "offline")
        self.assertFalse(result["ok"])

    async def test_check_all_uses_bounded_concurrency(self):
        monitor = AdbDeviceMonitor()
        monitor.concurrency = 2
        active = 0
        peak = 0

        async def check(_device):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1

        devices = [{"id": f"dev-{index}", "enabled": True} for index in range(6)]
        with (
            patch("app.adb_monitor.storage.list_all_devices", return_value=devices),
            patch.object(monitor, "ensure_connected", side_effect=check),
        ):
            await monitor.check_all()

        self.assertEqual(peak, 2)

    def test_last_seen_is_retained_after_a_failed_heartbeat(self):
        monitor = AdbDeviceMonitor()
        with patch("app.adb_monitor._now", side_effect=[100, 120]):
            monitor._set_status("dev-a", "online", ok=True, latency_ms=5.0)
            status = monitor._set_status("dev-a", "offline", ok=False, detail="offline", latency_ms=7.0)

        self.assertEqual(status["last_checked_at"], 120)
        self.assertEqual(status["last_seen_at"], 100)
        self.assertEqual(status["latency_ms"], 7.0)

    async def test_disabled_device_records_disabled_without_probe(self):
        monitor = AdbDeviceMonitor()
        device = {"id": "disabled-device", "address": "192.0.2.10:30100", "enabled": False}
        with (
            patch("app.adb_monitor.storage.get_device", return_value=device),
            patch.object(monitor, "_probe") as probe,
        ):
            result = await monitor.ensure_connected(device, force=True)

        probe.assert_not_called()
        self.assertEqual(result["state"], "disabled")
        self.assertFalse(result["ok"])

    async def test_probe_exception_becomes_unknown_status(self):
        monitor = AdbDeviceMonitor()
        device = {"id": "new-device", "address": "192.0.2.10:30100", "enabled": True}
        with (
            patch("app.adb_monitor.storage.get_device", return_value=device),
            patch.object(monitor, "_probe", side_effect=RuntimeError("probe failed")),
        ):
            result = await monitor.ensure_connected(device, force=True)

        self.assertEqual(result["state"], "unknown")
        self.assertFalse(result["ok"])
        self.assertEqual(result["detail"], "probe failed")

    async def test_stale_queued_snapshot_uses_latest_persisted_address(self):
        monitor = AdbDeviceMonitor()
        latest = {"id": "edited-device", "address": "192.0.2.20:30100", "enabled": True}
        with (
            patch("app.adb_monitor.storage.get_device", return_value=latest),
            patch.object(monitor, "_probe", return_value={"ok": True, "state": "online", "detail": "device"}) as probe,
        ):
            result = await monitor.ensure_connected(
                {"id": "edited-device", "address": "192.0.2.10:30100", "enabled": True},
                force=True,
            )

        probe.assert_called_once_with("edited-device", "192.0.2.20:30100")
        self.assertEqual(result["address"], "192.0.2.20:30100")

    async def test_stale_disabled_heartbeat_cannot_override_enabled_device(self):
        monitor = AdbDeviceMonitor()
        stale = {"id": "enabled-device", "address": "192.0.2.10:30100", "enabled": False}
        latest = {"id": "enabled-device", "address": "192.0.2.20:30100", "enabled": True}
        with (
            patch("app.adb_monitor.storage.list_all_devices", return_value=[stale]),
            patch("app.adb_monitor.storage.get_device", return_value=latest),
            patch.object(monitor, "_probe", return_value={"ok": True, "state": "online", "detail": "device"}) as probe,
        ):
            await monitor.check_all()

        probe.assert_called_once_with("enabled-device", "192.0.2.20:30100")
        self.assertEqual(monitor.snapshot("enabled-device")["state"], "online")

    async def test_deleted_device_cannot_be_restored_by_inflight_probe(self):
        monitor = AdbDeviceMonitor()
        device = {"id": "deleted-device", "address": "192.0.2.10:30100", "enabled": True}
        started = threading.Event()
        release = threading.Event()

        def delayed_probe(_device_id, _address):
            started.set()
            release.wait(1)
            return {"ok": True, "state": "online", "detail": "device"}

        with (
            patch("app.adb_monitor.storage.get_device", return_value=device),
            patch.object(monitor, "_probe", side_effect=delayed_probe),
        ):
            task = asyncio.create_task(monitor.ensure_connected(device, force=True))
            await asyncio.to_thread(started.wait, 1)
            monitor.forget_device("deleted-device")
            release.set()
            result = await task

        self.assertEqual(result["state"], "unknown")
        self.assertNotIn("deleted-device", monitor.snapshot())

    async def test_reconnect_missing_device_does_not_cache_ghost_status(self):
        monitor = AdbDeviceMonitor()
        with patch("app.adb_monitor.storage.get_device", return_value=None):
            result = await monitor.reconnect_device("missing-device")

        self.assertEqual(result["state"], "unknown")
        self.assertNotIn("missing-device", monitor.snapshot())

    async def test_reconnect_cannot_clear_retired_state_after_concurrent_delete(self):
        monitor = AdbDeviceMonitor()
        device = {"id": "deleted-device", "address": "192.0.2.10:30100", "enabled": True}
        first_read = threading.Event()
        continue_read = threading.Event()
        calls = 0

        def get_device(_device_id):
            nonlocal calls
            calls += 1
            if calls == 1:
                first_read.set()
                continue_read.wait(1)
                return device
            return None

        def delete_after_read():
            first_read.wait(1)
            monitor.forget_device("deleted-device")
            continue_read.set()

        worker = threading.Thread(target=delete_after_read)
        worker.start()
        try:
            with (
                patch("app.adb_monitor.storage.get_device", side_effect=get_device),
                patch.object(monitor, "_probe") as probe,
            ):
                result = await monitor.reconnect_device("deleted-device")
        finally:
            worker.join(timeout=1)

        self.assertEqual(result["state"], "unknown")
        self.assertNotIn("deleted-device", monitor.snapshot())
        probe.assert_not_called()

    def test_missing_usb_serial_is_reported_offline_without_tcp_probe(self):
        monitor = AdbDeviceMonitor()
        adb = MagicMock()
        adb.get_device_state.return_value = None
        adb.get_devices.return_value = []
        with (
            patch("app.adb_monitor.ADBManager", return_value=adb),
            patch("app.adb_monitor.socket.create_connection") as create_connection,
        ):
            result = monitor._probe("usb-serial", "usb-serial")

        self.assertEqual(result["state"], "offline")
        create_connection.assert_not_called()


if __name__ == "__main__":
    unittest.main()
