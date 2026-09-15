from threading import Lock, Thread
import logging
import os
import subprocess
import socket
import time
from adb_manager import ADBManager

log = logging.getLogger("webscrcpy.scrcpy")

SCRCPY_SERVER_PATH = "scrcpy-server"
DEVICE_SERVER_PATH = "/data/local/tmp/scrcpy-server.jar"
BASE_PORT = 6666  # Base local forward port; avoid conflict with 5555.
SCRCPY_I_FRAME_INTERVAL = max(1, int(os.environ.get("SCRCPY_I_FRAME_INTERVAL", "1") or "1"))
# 上游 scrcpy（PR #6670）把这两个 MediaFormat 提示当作默认值下发：
#   priority=0 —— 声明实时优先级（Android 文档：0 为 realtime，适用于采集/实时通信场景）
#   latency=1  —— 编码器排队 1 帧就应输出（文档明确：编码器不支持时该键被忽略）
# 未实现这两个键的编码器会直接忽略，因此默认开启与上游一致；保留开关是为了让
# 真机 A/B 或异常设备（例如 legacy OMX 编码器）能整体退回，而不必改代码。
SCRCPY_LOW_LATENCY_ENCODER_HINTS = str(
    os.environ.get("SCRCPY_LOW_LATENCY_ENCODER_HINTS", "1") or "1"
).strip().lower() not in ("0", "false", "no", "off")
# scrcpy server 的日志级别。此前硬编码 VERBOSE，而当时的读取路径只读 stderr ——
# 实测 Ln 把 VERBOSE/DEBUG/INFO 写到 stdout、WARN/ERROR 写到 stderr，于是这些行
# 既没进日志、也没被排空（见 start_server 的说明，那个管道会被写满并阻塞子进程）。
# 现在 stdout/stderr 已合并读干，级别只是一个音量旋钮：
#   实测每个会话 VERBOSE=8 行、INFO=2 行（都是启动期一次性输出，不含逐帧日志），
#   而 VERBOSE 多出来的正是排障最有用的几行：选中的编码器型号与实际生效的
#   video_codec_options。成本可忽略，故默认保持 VERBOSE，需要安静时再调低。
# 注意：服务端用 Ln.Level.valueOf() 解析，非法值会直接让 server 启动失败，
# 所以这里必须白名单校验，绝不能把用户的任意字符串透传下去。
SCRCPY_SERVER_LOG_LEVELS = ("VERBOSE", "DEBUG", "INFO", "WARN", "ERROR")
SCRCPY_SERVER_LOG_LEVEL = str(
    os.environ.get("SCRCPY_SERVER_LOG_LEVEL", "VERBOSE") or "VERBOSE"
).strip().upper()
if SCRCPY_SERVER_LOG_LEVEL not in SCRCPY_SERVER_LOG_LEVELS:
    log.warning(
        "SCRCPY_SERVER_LOG_LEVEL=%r 不是合法级别，回退到 VERBOSE（可选：%s）",
        os.environ.get("SCRCPY_SERVER_LOG_LEVEL"),
        "/".join(SCRCPY_SERVER_LOG_LEVELS),
    )
    SCRCPY_SERVER_LOG_LEVEL = "VERBOSE"
# 判断某一行是否值得当作 last_error（scrcpy server 的 Ln 输出形如 "ERROR: ..."）。
_SERVER_ERROR_MARKERS = ("ERROR", "WARN", "Exception", "error", "failed", "Failed")
SOCKET_READY_TIMEOUT = max(1.0, float(os.environ.get("SCRCPY_SOCKET_READY_TIMEOUT", "8") or "8"))
ADB_COMMAND_TIMEOUT = max(1.0, float(os.environ.get("SCRCPY_ADB_COMMAND_TIMEOUT", "15") or "15"))
SCRCPY_CONTROL_SEND_TIMEOUT_SECONDS = max(
    0.1,
    min(30.0, float(os.environ.get("SCRCPY_CONTROL_SEND_TIMEOUT_SECONDS", "3") or "3")),
)
SOCKET_KEEPALIVE_ENABLED = str(os.environ.get("SCRCPY_SOCKET_KEEPALIVE", "true") or "true").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
SOCKET_KEEPALIVE_IDLE = max(5, int(os.environ.get("SCRCPY_SOCKET_KEEPALIVE_IDLE", "30") or "30"))
SOCKET_KEEPALIVE_INTERVAL = max(1, int(os.environ.get("SCRCPY_SOCKET_KEEPALIVE_INTERVAL", "10") or "10"))
SOCKET_KEEPALIVE_COUNT = max(1, int(os.environ.get("SCRCPY_SOCKET_KEEPALIVE_COUNT", "3") or "3"))
_FORWARD_SETUP_LOCK = Lock()
_RESERVED_FORWARD_PORTS: set[int] = set()
_ORPHAN_FORWARD_PORTS: dict[int, tuple[str, str]] = {}


def _forward_command(adb_path, target, *args):
    cmd = [adb_path]
    if target:
        cmd.extend(["-s", target])
    cmd.extend(["forward", *args])
    return cmd


def _forward_is_listed(adb_path, target, port):
    """Return whether this target still owns the local forward, or None if unknown."""
    try:
        result = subprocess.run(
            [adb_path, "forward", "--list"],
            capture_output=True,
            text=True,
            check=False,
            timeout=ADB_COMMAND_TIMEOUT,
        )
    except Exception as exc:
        log.warning("ADB_FORWARD_LIST_FAILED port=%s target=%s error=%s", port, target, exc)
        return None
    if result.returncode != 0:
        log.warning(
            "ADB_FORWARD_LIST_FAILED port=%s target=%s returncode=%s",
            port,
            target,
            result.returncode,
        )
        return None
    local = f"tcp:{port}"
    for line in (result.stdout or "").splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[0] == target and fields[1] == local:
            return True
    return False


def _remove_adb_forward(adb_path, target, port):
    """Remove a forward and verify ambiguous failures against adb's forward table."""
    try:
        result = subprocess.run(
            _forward_command(adb_path, target, "--remove", f"tcp:{port}"),
            check=False,
            timeout=ADB_COMMAND_TIMEOUT,
        )
        if result.returncode == 0:
            return True
        log.warning(
            "ADB_FORWARD_CLEAN_FAILED port=%s target=%s returncode=%s",
            port,
            target,
            result.returncode,
        )
    except Exception as exc:
        log.warning("ADB_FORWARD_CLEAN_FAILED port=%s target=%s error=%s", port, target, exc)
    listed = _forward_is_listed(adb_path, target, port)
    if listed is False:
        log.info("ADB_FORWARD_ALREADY_ABSENT port=%s target=%s", port, target)
        return True
    return False


def _remember_orphan_forward(adb_path, target, port):
    with _FORWARD_SETUP_LOCK:
        _ORPHAN_FORWARD_PORTS[port] = (adb_path, target)
    log.warning("ADB_FORWARD_CLEAN_PENDING port=%s target=%s", port, target)


def _forget_orphan_forward(adb_path, target, port):
    with _FORWARD_SETUP_LOCK:
        if _ORPHAN_FORWARD_PORTS.get(port) == (adb_path, target):
            _ORPHAN_FORWARD_PORTS.pop(port, None)


def _retry_orphan_forward_cleanup():
    with _FORWARD_SETUP_LOCK:
        pending = list(_ORPHAN_FORWARD_PORTS.items())
    for port, (adb_path, target) in pending:
        if not _remove_adb_forward(adb_path, target, port):
            continue
        _forget_orphan_forward(adb_path, target, port)
        log.info("ADB_FORWARD_ORPHAN_CLEANED port=%s target=%s", port, target)


def _configure_connected_socket(sock: socket.socket, *, receive_buffer: int = 0, tcp_nodelay: bool = False) -> None:
    """Apply low-latency and dead-peer detection options without platform assumptions."""
    if receive_buffer:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, receive_buffer)
    if tcp_nodelay and hasattr(socket, "TCP_NODELAY"):
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    if not SOCKET_KEEPALIVE_ENABLED or not hasattr(socket, "SO_KEEPALIVE"):
        return
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        # Linux, Windows and recent BSD/Python builds expose these names. A
        # platform that does not support one of them simply keeps the base
        # SO_KEEPALIVE setting.
        for option_name, value in (
            ("TCP_KEEPIDLE", SOCKET_KEEPALIVE_IDLE),
            ("TCP_KEEPINTVL", SOCKET_KEEPALIVE_INTERVAL),
            ("TCP_KEEPCNT", SOCKET_KEEPALIVE_COUNT),
        ):
            option = getattr(socket, option_name, None)
            if option is not None:
                try:
                    sock.setsockopt(socket.IPPROTO_TCP, option, value)
                except OSError:
                    log.debug("SCRCPY_SOCKET_OPTION_UNSUPPORTED option=%s", option_name)
        # QUICKACK is a Linux optimisation for the receiving side. It is
        # intentionally best-effort because it is absent on Windows/macOS.
        quickack = getattr(socket, "TCP_QUICKACK", None)
        if quickack is not None:
            try:
                sock.setsockopt(socket.IPPROTO_TCP, quickack, 1)
            except OSError:
                log.debug("SCRCPY_SOCKET_OPTION_UNSUPPORTED option=TCP_QUICKACK")
    except OSError:
        log.debug("SCRCPY_SOCKET_KEEPALIVE_UNAVAILABLE")


class Scrcpy:
    def __init__(self):
        self.video_socket = None
        self.audio_socket = None
        self.control_socket = None

        self.android_thread = None
        self.video_thread = None
        self.audio_thread = None
        self.control_thread = None
        self.android_process = None
        
        self.adb_manager = ADBManager()
        self.adb_path = self.adb_manager.adb_path
        self.device_id = None
        self.device_address = None
        self.stream_mode = "raw"
        self.last_error = ""
        self.local_port = None  # Dynamically allocated local port.
        self.stream_exit_callback = None
        self.timing_callback = None
        self._android_server_started_at = None
        self.unexpected_exit_reason = ""
        self._exit_notify_lock = Lock()
        self._exit_notified = False
        
    @property
    def _adb_target(self):
        return self.device_address or self.device_id

    def _emit_timing(self, stage: str, started_at: float) -> None:
        """Report bounded startup timings without making instrumentation required."""
        elapsed_ms = round(max(0.0, (time.monotonic() - started_at) * 1000), 1)
        callback = getattr(self, "timing_callback", None)
        if callback is None:
            return
        try:
            callback(str(stage), elapsed_ms)
        except Exception:
            log.debug("SCRCPY_TIMING_CALLBACK_FAILED stage=%s", stage, exc_info=True)

    def find_available_port(self, start_port=BASE_PORT, max_attempts=100, excluded_ports=None):
        """Find an available local TCP port."""
        excluded = set(excluded_ports or ())
        for i in range(max_attempts):
            port = start_port + i
            if port in excluded:
                continue
            try:
                # Check whether the port can be bound locally.
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(('127.0.0.1', port))
                    return port
            except OSError:
                continue
        raise Exception(f"Unable to find an available port after {max_attempts} attempts")
        
    def cleanup_adb_forward(self):
        """Remove the active ADB port forward."""
        port = self.local_port
        if not port:
            return True
        target = self._adb_target
        if not _remove_adb_forward(self.adb_path, target, port):
            _remember_orphan_forward(self.adb_path, target, port)
            return False
        self.local_port = None
        _forget_orphan_forward(self.adb_path, target, port)
        log.info("ADB_FORWARD_CLEANED port=%s target=%s", port, self._adb_target)
        return True

    def push_server_to_device(self):
        log.info("SCRCPY_PUSH_SERVER target=%s", self._adb_target)
        cmd = [self.adb_path]
        if self._adb_target:
            cmd.extend(['-s', self._adb_target])
        cmd.extend(["push", SCRCPY_SERVER_PATH, DEVICE_SERVER_PATH])
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=ADB_COMMAND_TIMEOUT)
        except subprocess.TimeoutExpired:
            self.last_error = f"adb push timed out after {ADB_COMMAND_TIMEOUT:g}s"
            log.error("SCRCPY_PUSH_SERVER_TIMEOUT target=%s timeout=%s", self._adb_target, ADB_COMMAND_TIMEOUT)
            return False
        if result.returncode != 0:
            log.error("SCRCPY_PUSH_SERVER_FAILED target=%s stderr=%s", self._adb_target, result.stderr.strip())
            return False
        return True

    def setup_adb_forward(self):
        stale_port = self.local_port
        if not self.cleanup_adb_forward():
            raise RuntimeError(f"Unable to remove stale ADB forward tcp:{stale_port}")
        _retry_orphan_forward_cleanup()
        with _FORWARD_SETUP_LOCK:
            excluded_ports = _RESERVED_FORWARD_PORTS | set(_ORPHAN_FORWARD_PORTS)
            reserved_port = self.find_available_port(excluded_ports=excluded_ports)
            _RESERVED_FORWARD_PORTS.add(reserved_port)
        log.info("ADB_FORWARD_SETUP port=%s target=%s", reserved_port, self._adb_target)

        cmd = _forward_command(
            self.adb_path,
            self._adb_target,
            f"tcp:{reserved_port}",
            "localabstract:scrcpy",
        )

        try:
            subprocess.run(cmd, check=True, timeout=ADB_COMMAND_TIMEOUT)
        except subprocess.TimeoutExpired:
            _remember_orphan_forward(self.adb_path, self._adb_target, reserved_port)
            self.local_port = None
            raise
        except Exception:
            self.local_port = None
            raise
        else:
            self.local_port = reserved_port
        finally:
            with _FORWARD_SETUP_LOCK:
                _RESERVED_FORWARD_PORTS.discard(reserved_port)

    def start_server(self):
        log.info("SCRCPY_SERVER_START target=%s mode=%s bitrate=%s max_size=%s max_fps=%s", self._adb_target, self.stream_mode, self.video_bit_rate, self.max_size, self.max_fps)
        cmd = [self.adb_path]
        if self._adb_target:
            cmd.extend(['-s', self._adb_target])
        server_cmd = self._build_server_command()
        cmd.extend(["shell", server_cmd])
        # scrcpy server 的 Ln 把 VERBOSE/DEBUG/INFO 写到 stdout、WARN/ERROR(含堆栈) 写到 stderr，
        # 用的是两个分别指向 FileDescriptor.out/err 的 PrintStream。此前我们只读 stderr 且
        # stdout=PIPE 从不排空，于是有两个后果：
        #   ① [server] INFO: Device/Encoder 这些有用的启动诊断一行都拿不到；
        #   ② 未被读取的 stdout 管道会被写满（Linux 默认 64KB），子进程随后阻塞在
        #      System.out 上 —— 若该次 Ln 调用发生在采集/编码路径，就会直接影响推流。
        # 合并成一个管道按顺序读干：既保留全部诊断，也彻底消除阻塞风险，
        # 并且让 SCRCPY_SERVER_LOG_LEVEL 可以安全地调高用于排障。
        self.android_process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        server_started_at = getattr(self, "_android_server_started_at", None)
        if server_started_at is not None:
            self._emit_timing("android_server", server_started_at)
        last_any_line = self._drain_server_output()
        self.android_process.wait()
        if not self.last_error and last_any_line:
            # 全程没有错误行时，至少留下最后一行，便于判断 server 是怎么退出的。
            self.last_error = last_any_line
        log.info("SCRCPY_SERVER_STOPPED target=%s returncode=%s", self._adb_target, self.android_process.returncode)
        if not self.stop:
            self._notify_stream_exit(f"scrcpy server exited with code {self.android_process.returncode}")

    def _drain_server_output(self) -> str:
        """读干 server 的合并输出，返回最后一行原文（调用方决定要不要当 last_error）。

        stdout 与 stderr 已合并到同一个管道（见 start_server），所以这里既能拿到
        INFO 级诊断，也能拿到 WARN/ERROR。
        """
        last_any_line = ""
        while not self.stop:
            server_line = self.android_process.stdout.readline().decode(errors="replace").strip()
            if not server_line:
                break
            last_any_line = server_line
            # 只有像样的错误/告警行才覆盖 last_error：否则 INFO 诊断与 Java 堆栈的
            # 后续行（"\tat com.genymobile...") 会把真正的原因挤掉。
            if any(marker in server_line for marker in _SERVER_ERROR_MARKERS):
                self.last_error = server_line
            log.info("SCRCPY_SERVER target=%s line=%s", self._adb_target, server_line)
        return last_any_line

    def _build_server_command(self):
        codec_options = f"i-frame-interval={SCRCPY_I_FRAME_INTERVAL}"
        if SCRCPY_LOW_LATENCY_ENCODER_HINTS:
            codec_options += ",priority=0,latency=1"
        server_cmd = (
            f"CLASSPATH={DEVICE_SERVER_PATH} app_process / "
            f"com.genymobile.scrcpy.Server 3.1 "
            f"tunnel_forward=true log_level={SCRCPY_SERVER_LOG_LEVEL} "
            f"video_bit_rate={self.video_bit_rate} "
            f"video_codec=h264 "
            f"video_codec_options={codec_options} "
            f"audio=false"
        )
        if self.stream_mode == "raw":
            server_cmd += " send_device_meta=false send_frame_meta=false send_codec_meta=false"
        if self.max_size > 0:
            server_cmd += f" max_size={self.max_size}"
        if self.max_fps > 0:
            server_cmd += f" max_fps={self.max_fps}"
        return server_cmd

    def _connect_forward_socket(self, *, receive_buffer=0, tcp_nodelay=False, expect_dummy=False):
        deadline = time.monotonic() + SOCKET_READY_TIMEOUT
        last_error = None
        while time.monotonic() < deadline and not self.stop:
            process = self.android_process
            if process is not None and process.poll() is not None:
                raise ConnectionError(self.last_error or f"scrcpy server exited with code {process.returncode}")
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                _configure_connected_socket(sock, receive_buffer=receive_buffer, tcp_nodelay=tcp_nodelay)
                sock.settimeout(min(1.0, max(0.1, deadline - time.monotonic())))
                sock.connect(('localhost', self.local_port))
                if expect_dummy:
                    dummy = sock.recv(1)
                    if dummy != b"\x00":
                        raise ConnectionError("scrcpy server did not confirm the forwarded connection")
                sock.settimeout(None)
                return sock
            except (OSError, ConnectionError, socket.error) as exc:
                last_error = exc
                sock.close()
                time.sleep(0.1)
        raise ConnectionError(f"scrcpy socket was not ready: {last_error or 'timeout'}")

    def _notify_stream_exit(self, reason):
        callback = self.stream_exit_callback
        if self.stop or not callback:
            return
        with self._exit_notify_lock:
            if self._exit_notified or self.stop:
                return
            self._exit_notified = True
            self.unexpected_exit_reason = reason
        try:
            callback(reason)
        except Exception as exc:
            log.warning("SCRCPY_EXIT_CALLBACK_FAILED target=%s error=%s", self._adb_target, exc)

    def receive_video_data(self):
        log.info("SCRCPY_VIDEO_RECV_START target=%s mode=%s", self._adb_target, self.stream_mode)
        exit_reason = "video socket closed"
        try:
            while not self.stop:
                try:
                    data = self.video_socket.recv(65536)
                    if not data:
                        break
                    self.video_callback(data)
                except (OSError, ConnectionError, socket.error) as e:
                    if not self.stop:
                        self.last_error = str(e)
                        exit_reason = f"video socket error: {e}"
                        log.warning("SCRCPY_VIDEO_SOCKET_ERROR target=%s error=%s", self._adb_target, e)
                    break
        except (OSError, ConnectionError, socket.error) as e:
            if not self.stop:
                self.last_error = str(e)
                exit_reason = f"video socket error: {e}"
                log.warning("SCRCPY_VIDEO_SOCKET_INIT_ERROR target=%s error=%s", self._adb_target, e)
        log.info("SCRCPY_VIDEO_RECV_STOP target=%s", self._adb_target)
        if not self.stop:
            self._notify_stream_exit(exit_reason)

    def receive_audio_data(self):
        log.info("SCRCPY_AUDIO_RECV_START target=%s", self._adb_target)
        try:
            self.audio_socket.recv(1)
            while not self.stop:
                try:
                    data = self.audio_socket.recv(1024)
                    if not data:
                        break
                except (OSError, ConnectionError, socket.error) as e:
                    if not self.stop:
                        log.warning("SCRCPY_AUDIO_SOCKET_ERROR target=%s error=%s", self._adb_target, e)
                    break
        except (OSError, ConnectionError, socket.error) as e:
            if not self.stop:
                log.warning("SCRCPY_AUDIO_SOCKET_INIT_ERROR target=%s error=%s", self._adb_target, e)
        log.info("SCRCPY_AUDIO_RECV_STOP target=%s", self._adb_target)

    def handle_control_conn(self):
        log.info("SCRCPY_CONTROL_RECV_START target=%s", self._adb_target)
        try:
            while not self.stop:
                try:
                    data = self.control_socket.recv(1024)
                    if not data:
                        break
                    log.debug("SCRCPY_CONTROL_RECV target=%s bytes=%s", self._adb_target, len(data))
                except socket.timeout:
                    # ``scrcpy_send_control`` temporarily bounds a write on
                    # the same full-duplex socket.  A concurrent recv can see
                    # that timeout; keep the reader alive and return to its
                    # normal blocking mode once the writer restores it.
                    if not self.stop:
                        continue
                    break
                except (OSError, ConnectionError, socket.error) as e:
                    if not self.stop:
                        log.warning("SCRCPY_CONTROL_SOCKET_ERROR target=%s error=%s", self._adb_target, e)
                    break
        except (OSError, ConnectionError, socket.error) as e:
            if not self.stop:
                log.warning("SCRCPY_CONTROL_SOCKET_INIT_ERROR target=%s error=%s", self._adb_target, e)
        log.info("SCRCPY_CONTROL_RECV_STOP target=%s", self._adb_target)

    def scrcpy_start(
        self,
        video_callback,
        video_bit_rate,
        max_size=0,
        max_fps=0,
        stream_mode="raw",
        stream_exit_callback=None,
        timing_callback=None,
    ):
        self.video_bit_rate = video_bit_rate
        self.max_size = max_size
        self.max_fps = max_fps
        self.stream_mode = stream_mode if stream_mode in ("raw", "protocol", "legacy") else "raw"
        self.video_callback = video_callback
        self.stream_exit_callback = stream_exit_callback
        self.timing_callback = timing_callback
        self.stop = False
        self.last_error = ""
        self.unexpected_exit_reason = ""
        self._exit_notified = False
        startup_started_at = time.monotonic()

        # Check device connection state and avoid misreading the adb devices header.
        step_started_at = time.monotonic()
        try:
            state = self.adb_manager.get_device_state(self._adb_target, timeout=ADB_COMMAND_TIMEOUT)
        finally:
            self._emit_timing("adb_state", step_started_at)
        if state != 'device':
            adb_detail = getattr(self.adb_manager, "last_error", "")
            detail = f"; adb_error={adb_detail}" if isinstance(adb_detail, str) and adb_detail else ""
            self.last_error = f"Device {self._adb_target} not ready. state={state}{detail}"
            log.warning("SCRCPY_DEVICE_NOT_READY target=%s state=%s detail=%s", self._adb_target, state, self.last_error)
            return False
        log.info("SCRCPY_DEVICE_READY target=%s state=%s", self._adb_target, state)

        step_started_at = time.monotonic()
        try:
            pushed = self.push_server_to_device()
        finally:
            self._emit_timing("server_push", step_started_at)
        if not pushed:
            log.error("SCRCPY_START_ABORT_PUSH_FAILED target=%s", self._adb_target)
            if not self.last_error:
                self.last_error = "failed to push scrcpy server"
            return False

        try:
            step_started_at = time.monotonic()
            try:
                self.setup_adb_forward()
            finally:
                self._emit_timing("adb_forward", step_started_at)
            self._android_server_started_at = time.monotonic()
            self.android_thread = Thread(target=self.start_server, daemon=True)
            self.android_thread.start()

            # Video is interactive as well as high-throughput. Keep TCP from
            # coalescing the small tail of an access unit on low-latency
            # profiles.
            step_started_at = time.monotonic()
            try:
                self.video_socket = self._connect_forward_socket(
                    receive_buffer=1024 * 1024,
                    tcp_nodelay=True,
                    expect_dummy=True,
                )
            finally:
                self._emit_timing("video_socket", step_started_at)
            log.info("SCRCPY_VIDEO_SOCKET_CONNECTED target=%s port=%s", self._adb_target, self.local_port)

            # control connection
            step_started_at = time.monotonic()
            try:
                self.control_socket = self._connect_forward_socket(tcp_nodelay=True)
            finally:
                self._emit_timing("control_socket", step_started_at)
            log.info("SCRCPY_CONTROL_SOCKET_CONNECTED target=%s port=%s", self._adb_target, self.local_port)

            self.video_thread = Thread(target=self.receive_video_data, daemon=True)
            self.control_thread = Thread(target=self.handle_control_conn, daemon=True)
            self.video_thread.start()
            self.control_thread.start()
            log.info("SCRCPY_THREADS_STARTED target=%s mode=%s", self._adb_target, self.stream_mode)
            self._emit_timing("transport_ready", startup_started_at)
            
            return True
            
        except Exception as e:
            self.last_error = str(e)
            log.error("SCRCPY_SOCKET_CONNECT_FAILED target=%s error=%s", self._adb_target, e)
            self.scrcpy_stop()
            return False

    def scrcpy_stop(self):
        log.info("SCRCPY_STOP target=%s", self._adb_target)
        self.stop = True
        
        # Close socket connections safely.
        sockets_to_close = [
            ('video_socket', self.video_socket),
            ('audio_socket', self.audio_socket),
            ('control_socket', self.control_socket)
        ]
        
        for socket_name, sock in sockets_to_close:
            if sock:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except (OSError, socket.error):
                    pass
                try:
                    sock.close()
                except (OSError, socket.error):
                    pass

        # Wait for worker threads to finish.
        threads_to_join = [
            ('video_thread', self.video_thread),
            ('audio_thread', self.audio_thread),
            ('control_thread', self.control_thread)
        ]
        
        for thread_name, thread in threads_to_join:
            if thread and thread.is_alive():
                try:
                    thread.join(timeout=3)
                    if thread.is_alive():
                        log.warning("SCRCPY_THREAD_JOIN_TIMEOUT target=%s thread=%s", self._adb_target, thread_name)
                except Exception as e:
                    log.warning("SCRCPY_THREAD_JOIN_FAILED target=%s thread=%s error=%s", self._adb_target, thread_name, e)
            
        # Terminate the Android-side process.
        if self.android_process:
            try:
                self.android_process.terminate()
                # Give the process a moment to exit cleanly.
                try:
                    self.android_process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    log.warning("SCRCPY_ANDROID_PROCESS_KILL target=%s", self._adb_target)
                    self.android_process.kill()
            except Exception as e:
                log.warning("SCRCPY_ANDROID_PROCESS_TERM_FAILED target=%s error=%s", self._adb_target, e)
                
        if self.android_thread and self.android_thread.is_alive():
            try:
                self.android_thread.join(timeout=3)
                if self.android_thread.is_alive():
                    log.warning("SCRCPY_ANDROID_THREAD_JOIN_TIMEOUT target=%s", self._adb_target)
            except Exception as e:
                log.warning("SCRCPY_ANDROID_THREAD_JOIN_FAILED target=%s error=%s", self._adb_target, e)
            
        # Remove ADB port forwarding.
        forward_cleaned = self.cleanup_adb_forward()
        if forward_cleaned:
            log.info("SCRCPY_STOPPED target=%s", self._adb_target)
        else:
            log.warning(
                "SCRCPY_STOPPED_FORWARD_CLEAN_PENDING target=%s port=%s",
                self._adb_target,
                self.local_port,
            )
        return forward_cleaned

    def scrcpy_send_control(self, data):
        try:
            sock = getattr(self, "control_socket", None)
            if sock is None:
                log.warning("SCRCPY_CONTROL_SEND_NO_SOCKET target=%s", self._adb_target)
                return False

            # Check whether the control socket is still connected.
            get_timeout = getattr(sock, "gettimeout", None)
            set_timeout = getattr(sock, "settimeout", None)
            previous_timeout = get_timeout() if callable(get_timeout) else None
            try:
                # A stalled device-side control reader must not retain an
                # asyncio.to_thread worker indefinitely.  Restore the
                # receive timeout after the bounded write so the control
                # reader keeps its existing blocking behavior.
                if callable(set_timeout):
                    set_timeout(SCRCPY_CONTROL_SEND_TIMEOUT_SECONDS)
                sock.sendall(data)
                return True
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, socket.timeout, TimeoutError) as e:
                log.warning("SCRCPY_CONTROL_SEND_LOST target=%s error=%s", self._adb_target, e)
                return False
            except Exception as e:
                log.warning("SCRCPY_CONTROL_SEND_FAILED target=%s error=%s", self._adb_target, e)
                return False
            finally:
                if callable(set_timeout):
                    try:
                        set_timeout(previous_timeout)
                    except Exception:
                        log.debug("SCRCPY_CONTROL_TIMEOUT_RESTORE_FAILED target=%s", self._adb_target)
                
        except Exception as e:
            log.warning("SCRCPY_CONTROL_SEND_UNEXPECTED target=%s error=%s", self._adb_target, e)
            return False
