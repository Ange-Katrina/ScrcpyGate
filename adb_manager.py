import os
import platform
import re
import shutil
import subprocess
import time
from typing import Optional, Tuple


class ADBManager:
    def __init__(self):
        self.adb_path = self._get_adb_path()
        self.current_device = None
        self.is_tcp_mode = False

    def _get_adb_path(self) -> str:
        env_adb = os.environ.get("ADB_PATH", "")
        if env_adb and os.path.isfile(env_adb):
            return env_adb

        system = platform.system().lower()
        current_dir = os.path.dirname(os.path.abspath(__file__))

        if system == "linux":
            sys_adb = shutil.which("adb")
            if sys_adb:
                return sys_adb
            return os.path.join(current_dir, "adb", "linux", "adb")
        if system == "windows":
            return os.path.join(current_dir, "adb", "windows", "adb.exe")
        if system == "darwin":
            return os.path.join(current_dir, "adb", "darwin", "adb")
        raise RuntimeError(f"Unsupported operating system: {system}")

    def _run_adb_command(self, command: list[str], device_id: str | None = None, timeout: int | None = None) -> Tuple[bool, str]:
        try:
            cmd = [self.adb_path]
            if device_id:
                cmd.extend(["-s", device_id])
            cmd.extend(command)
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
            output = result.stdout if result.returncode == 0 else result.stderr
            return result.returncode == 0, output or ""
        except subprocess.TimeoutExpired:
            return False, f"adb timeout after {timeout}s: {' '.join(command)}"
        except Exception as exc:
            return False, str(exc)

    @staticmethod
    def parse_devices_output(output: str) -> list[dict]:
        devices = []
        for line in (output or "").splitlines()[1:]:
            line = line.strip()
            if not line:
                continue
            parts = re.split(r"\s+", line, maxsplit=1)
            if len(parts) >= 2:
                devices.append(
                    {
                        "id": parts[0],
                        "state": parts[1].split()[0],
                        "is_tcp": ":" in parts[0],
                    }
                )
        return devices

    def get_devices(self, timeout: int | None = None) -> list[dict]:
        success, output = self._run_adb_command(["devices"], timeout=timeout)
        if not success:
            return []
        return self.parse_devices_output(output)

    def get_device_state(self, device_id: str | None = None, timeout: int | None = None) -> Optional[str]:
        success, output = self._run_adb_command(["get-state"], device_id, timeout=timeout)
        if not success:
            return None
        return (output or "").strip()

    def is_device_ready(self, device_id: str | None = None) -> bool:
        return self.get_device_state(device_id) == "device"

    def get_device_ip(self) -> Optional[str]:
        success, output = self._run_adb_command(["shell", "ip", "route"])
        if not success:
            return None
        match = re.search(r"src (\d+\.\d+\.\d+\.\d+)", output)
        return match.group(1) if match else None

    def connect_to_device(self, ip: str, port: int = 5555) -> tuple[bool, str]:
        address = f"{ip}:{port}"
        success, output = self._run_adb_command(["connect", address])
        out_lower = (output or "").lower()
        if success and ("connected" in out_lower or "already connected" in out_lower):
            self.current_device = address
            self.is_tcp_mode = True
            return True, output
        return False, output

    def disconnect_device(self, ip: str | None = None, port: int = 5555) -> bool:
        command = ["disconnect", f"{ip}:{port}"] if ip else ["disconnect"]
        success, _ = self._run_adb_command(command)
        if success:
            self.current_device = None
            self.is_tcp_mode = False
        return success

    def enable_tcp_mode(self) -> Tuple[bool, Optional[str]]:
        devices = self.get_devices()
        usb_devices = [device for device in devices if not device["is_tcp"]]
        if not usb_devices:
            return False, "No USB-connected device found"

        ip = self.get_device_ip()
        if not ip:
            return False, "Unable to detect device IP address"

        success, output = self._run_adb_command(["tcpip", "5555"])
        if not success:
            return False, f"Failed to enable ADB TCP/IP mode: {output}"

        time.sleep(1)
        connected, detail = self.connect_to_device(ip)
        if connected:
            return True, ip
        return False, detail or "ADB TCP/IP connection failed"

    def get_current_connection_info(self) -> dict:
        return {
            "device": self.current_device,
            "is_tcp_mode": self.is_tcp_mode,
            "all_devices": self.get_devices(),
        }


if __name__ == "__main__":
    adb = ADBManager()
    print("Connected devices:", adb.get_devices())
