import logging
import os
import platform
import re
import shlex
import shutil
import subprocess
import time
from typing import Any, Optional, Sequence, Tuple


log = logging.getLogger("webscrcpy.adb")


class ADBCommandError(RuntimeError):
    """A structured failure from an ADB subprocess invocation."""

    def __init__(
        self,
        command: Sequence[str],
        *,
        device_id: str | None = None,
        detail: str = "adb command failed",
        returncode: int | None = None,
        timed_out: bool = False,
    ) -> None:
        self.command = tuple(str(item) for item in command)
        self.device_id = device_id
        self.detail = detail
        self.returncode = returncode
        self.timed_out = timed_out
        super().__init__(detail)


class ADBManager:
    def __init__(self):
        self.adb_path = self._get_adb_path()
        self.current_device = None
        self.is_tcp_mode = False
        self.last_error = ""
        self.last_error_info: dict[str, Any] | None = None

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

    def _command_text(self, command: Sequence[str], device_id: str | None = None) -> str:
        args = [self.adb_path]
        if device_id:
            args.extend(["-s", device_id])
        args.extend(str(item) for item in command)
        return shlex.join(args)

    def _record_command_error(
        self,
        error: ADBCommandError,
        *,
        timeout: int | None,
        cause: BaseException | None = None,
        raise_on_error: bool = False,
    ) -> None:
        self.last_error = error.detail
        self.last_error_info = {
            "command": self._command_text(error.command, error.device_id),
            "device_id": error.device_id or "",
            "detail": error.detail,
            "returncode": error.returncode,
            "timed_out": error.timed_out,
            "timeout": timeout,
            "exception_type": type(cause).__name__ if cause else "",
        }
        fields = {
            "command": self.last_error_info["command"],
            "device": error.device_id or "",
            "returncode": error.returncode,
            "timeout": timeout,
            "detail": error.detail,
        }
        if cause is not None:
            # Keep the complete traceback in the runtime log. The returned
            # status remains a short, safe detail for API/UI consumers.
            log.error(
                "ADB_COMMAND_EXCEPTION %s",
                " ".join(f"{key}={value!s}" for key, value in fields.items()),
                exc_info=(type(cause), cause, cause.__traceback__),
            )
        else:
            log.warning("ADB_COMMAND_FAILED %s", " ".join(f"{key}={value!s}" for key, value in fields.items()))
        if raise_on_error:
            if cause is not None:
                raise error from cause
            raise error

    def _run_adb_command(
        self,
        command: list[str],
        device_id: str | None = None,
        timeout: int | None = None,
        *,
        raise_on_error: bool = False,
    ) -> Tuple[bool, str]:
        """Run ADB while retaining the legacy tuple contract.

        Callers that can distinguish infrastructure failures from an offline
        device may pass ``raise_on_error=True`` and handle ``ADBCommandError``
        at their boundary. Existing best-effort callers keep the old result
        shape, but every subprocess exception now has a traceback in logs.
        """
        self.last_error = ""
        self.last_error_info = None
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
            output = output or ""
            if result.returncode == 0:
                return True, output
            error = ADBCommandError(
                command,
                device_id=device_id,
                detail=output or f"adb exited with code {result.returncode}",
                returncode=result.returncode,
            )
            self._record_command_error(error, timeout=timeout, raise_on_error=raise_on_error)
            return False, error.detail
        except ADBCommandError:
            # _record_command_error raises this intentionally when the caller
            # requested strict failure semantics. Preserve its original type.
            raise
        except subprocess.TimeoutExpired as exc:
            error = ADBCommandError(
                command,
                device_id=device_id,
                detail=f"adb timeout after {timeout}s: {' '.join(command)}",
                timed_out=True,
            )
            self._record_command_error(error, timeout=timeout, cause=exc, raise_on_error=raise_on_error)
            return False, error.detail
        except Exception as exc:
            error = ADBCommandError(command, device_id=device_id, detail=str(exc) or type(exc).__name__)
            self._record_command_error(error, timeout=timeout, cause=exc, raise_on_error=raise_on_error)
            return False, error.detail

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

    def get_devices(self, timeout: int | None = None, *, raise_on_error: bool = False) -> list[dict]:
        success, output = self._run_adb_command(["devices"], timeout=timeout, raise_on_error=raise_on_error)
        if not success:
            return []
        return self.parse_devices_output(output)

    def get_device_state(
        self,
        device_id: str | None = None,
        timeout: int | None = None,
        *,
        raise_on_error: bool = False,
    ) -> Optional[str]:
        success, output = self._run_adb_command(
            ["get-state"], device_id, timeout=timeout, raise_on_error=raise_on_error
        )
        if not success:
            return None
        return (output or "").strip()

    def is_device_ready(self, device_id: str | None = None, *, raise_on_error: bool = False) -> bool:
        return self.get_device_state(device_id, raise_on_error=raise_on_error) == "device"

    def get_device_ip(self, *, raise_on_error: bool = False) -> Optional[str]:
        success, output = self._run_adb_command(["shell", "ip", "route"], raise_on_error=raise_on_error)
        if not success:
            return None
        match = re.search(r"src (\d+\.\d+\.\d+\.\d+)", output)
        return match.group(1) if match else None

    def connect_to_device(self, ip: str, port: int = 5555, *, raise_on_error: bool = False) -> tuple[bool, str]:
        address = f"{ip}:{port}"
        success, output = self._run_adb_command(["connect", address], raise_on_error=raise_on_error)
        out_lower = (output or "").lower()
        if success and ("connected" in out_lower or "already connected" in out_lower):
            self.current_device = address
            self.is_tcp_mode = True
            return True, output
        return False, output

    def disconnect_device(self, ip: str | None = None, port: int = 5555, *, raise_on_error: bool = False) -> bool:
        command = ["disconnect", f"{ip}:{port}"] if ip else ["disconnect"]
        success, _ = self._run_adb_command(command, raise_on_error=raise_on_error)
        if success:
            self.current_device = None
            self.is_tcp_mode = False
        return success

    def enable_tcp_mode(self, *, raise_on_error: bool = False) -> Tuple[bool, Optional[str]]:
        devices = self.get_devices(raise_on_error=raise_on_error)
        usb_devices = [device for device in devices if not device["is_tcp"]]
        if not usb_devices:
            return False, "No USB-connected device found"

        ip = self.get_device_ip(raise_on_error=raise_on_error)
        if not ip:
            return False, "Unable to detect device IP address"

        success, output = self._run_adb_command(["tcpip", "5555"], raise_on_error=raise_on_error)
        if not success:
            return False, f"Failed to enable ADB TCP/IP mode: {output}"

        time.sleep(1)
        connected, detail = self.connect_to_device(ip, raise_on_error=raise_on_error)
        if connected:
            return True, ip
        return False, detail or "ADB TCP/IP connection failed"

    def get_current_connection_info(self, *, raise_on_error: bool = False) -> dict:
        return {
            "device": self.current_device,
            "is_tcp_mode": self.is_tcp_mode,
            "all_devices": self.get_devices(raise_on_error=raise_on_error),
            "last_error": self.last_error,
        }

    def device_display_rotation(
        self,
        device_id: str | None = None,
        *,
        timeout: int = 8,
        raise_on_error: bool = False,
    ) -> Optional[int]:
        """Return the device's current display rotation (0..3), or None when unknown.

        ``dumpsys display`` reports ``mCurrentOrientation`` for the default display;
        it reflects the *actual* rotation (auto-rotate or a window-manager override),
        unlike ``settings get system user_rotation`` which stays 0 while auto-rotate
        is on. Used to follow a device that rotated its own screen.
        """
        success, output = self._run_adb_command(
            ["shell", "dumpsys display"], device_id, timeout=timeout, raise_on_error=raise_on_error
        )
        if not success:
            return None
        match = re.search(r"mCurrentOrientation=(\d)", output or "")
        if not match:
            return None
        value = int(match.group(1))
        return value if 0 <= value <= 3 else None


if __name__ == "__main__":
    adb = ADBManager()
    print("Connected devices:", adb.get_devices())
