import sys
import unittest
import importlib
import os
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.devices import device_payload, devices_payload


class DevicePayloadTests(unittest.TestCase):
    def test_device_payload_exposes_id_and_device_id(self):
        payload = device_payload(
            {"id": "192.0.2.10:30100", "name": "emu", "address": "192.0.2.10:30100", "enabled": 1},
            {"192.0.2.10:30100": {"running": True}},
        )

        self.assertEqual(payload["id"], "192.0.2.10:30100")
        self.assertEqual(payload["device_id"], "192.0.2.10:30100")
        self.assertEqual(payload["name"], "emu")
        self.assertTrue(payload["enabled"])
        self.assertTrue(payload["can_view"])
        self.assertTrue(payload["can_control"])
        self.assertEqual(payload["session"], {"running": True, "device_id": "192.0.2.10:30100"})

    def test_devices_payload_accepts_legacy_device_id_field(self):
        payload = devices_payload([{"device_id": "dev-a", "address": "127.0.0.1:5555", "can_control": 0}])

        self.assertEqual(payload[0]["id"], "dev-a")
        self.assertEqual(payload[0]["device_id"], "dev-a")
        self.assertFalse(payload[0]["can_control"])

    def test_bool_fields_parse_string_zero(self):
        payload = device_payload({"id": "dev-b", "enabled": "0", "can_view": "false", "can_control": "off"})

        self.assertFalse(payload["enabled"])
        self.assertFalse(payload["can_view"])
        self.assertFalse(payload["can_control"])

    def test_public_payload_includes_adb_state_without_address(self):
        payload = device_payload(
            {"id": "dev-c", "name": "emu", "address": "192.0.2.10:30100", "enabled": 1},
            statuses={"dev-c": {"state": "online", "ok": True}},
            include_address=False,
        )

        self.assertEqual(payload["adb_state"], "online")
        self.assertTrue(payload["adb_ok"])
        self.assertNotIn("address", payload)

    def test_public_payload_hides_adb_address(self):
        tmp = Path(tempfile.mkdtemp(prefix="webscrcpy-v2-device-payload-"))
        try:
            os.environ["WEB_SCRCPY_DATA_DIR"] = str(tmp)
            for name in ["app.devices", "app.storage"]:
                sys.modules.pop(name, None)
            storage = importlib.import_module("app.storage")
            storage.init_db()
            devices_mod = importlib.import_module("app.devices")
            payload = devices_mod.device_payload(
                {"id": "192.0.2.10:30100", "name": "emu", "address": "192.0.2.10:30100", "enabled": 1},
                {
                    "192.0.2.10:30100": {
                        "running": True,
                        "device_id": "192.0.2.10:30100",
                        "last_error": "192.0.2.10:30100 offline",
                        "adb": {
                            "device_id": "192.0.2.10:30100",
                            "address": "192.0.2.10:30100",
                            "detail": "192.0.2.10:30100 device",
                            "state": "online",
                        },
                        "control_lock": {
                            "device_id": "192.0.2.10:30100",
                            "username": "alice",
                            "client_id": "private-client",
                        },
                    }
                },
                include_address=False,
                public_id=True,
            )

            self.assertNotIn("address", payload)
            self.assertNotIn("real_device_id", payload)
            self.assertTrue(payload["id"].startswith("dev_"))
            self.assertEqual(payload["id"], payload["device_id"])
            self.assertEqual(payload["session"]["device_id"], payload["id"])
            self.assertEqual(payload["session"]["adb"]["device_id"], payload["id"])
            self.assertEqual(payload["session"]["control_lock"]["device_id"], payload["id"])
            self.assertEqual(payload["session"]["last_error"], "设备视频流不可用")
            self.assertNotIn("address", payload["session"]["adb"])
            self.assertEqual(payload["session"]["adb"]["detail"], "ADB 连接不可用")
            self.assertNotIn("client_id", payload["session"]["control_lock"])
            self.assertNotIn("192.0.2.10", payload["id"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            os.environ.pop("WEB_SCRCPY_DATA_DIR", None)
            for name in ["app.devices", "app.storage"]:
                sys.modules.pop(name, None)


if __name__ == "__main__":
    unittest.main()
