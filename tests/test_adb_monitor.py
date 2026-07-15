import unittest
from unittest.mock import patch

from app.adb_monitor import AdbDeviceMonitor


class AdbDeviceMonitorTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_device_records_disabled_without_probe(self):
        monitor = AdbDeviceMonitor()
        with (
            patch("app.adb_monitor.storage.get_device", return_value=None),
            patch.object(monitor, "_probe") as probe,
        ):
            result = await monitor.ensure_connected(
                {"id": "disabled-device", "address": "192.0.2.10:30100", "enabled": False},
                force=True,
            )

        probe.assert_not_called()
        self.assertEqual(result["state"], "disabled")
        self.assertFalse(result["ok"])

    async def test_probe_exception_becomes_unknown_status(self):
        monitor = AdbDeviceMonitor()
        with (
            patch("app.adb_monitor.storage.get_device", return_value=None),
            patch.object(monitor, "_probe", side_effect=RuntimeError("probe failed")),
        ):
            result = await monitor.ensure_connected(
                {"id": "new-device", "address": "192.0.2.10:30100", "enabled": True},
                force=True,
            )

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


if __name__ == "__main__":
    unittest.main()
