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
SOCKET_READY_TIMEOUT = max(1.0, float(os.environ.get("SCRCPY_SOCKET_READY_TIMEOUT", "8") or "8"))

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
        self.unexpected_exit_reason = ""
        self._exit_notify_lock = Lock()
        self._exit_notified = False
        
    @property
    def _adb_target(self):
        return self.device_address or self.device_id

    def find_available_port(self, start_port=BASE_PORT, max_attempts=100):
        """Find an available local TCP port."""
        for i in range(max_attempts):
            port = start_port + i
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
        if self.local_port:
            try:
                cmd = [self.adb_path]
                if self._adb_target:
                    cmd.extend(['-s', self._adb_target])
                cmd.extend(["forward", "--remove", f"tcp:{self.local_port}"])
                subprocess.run(cmd, check=False)  # Ignore failures; the forward may already be gone.
                log.info("ADB_FORWARD_CLEANED port=%s target=%s", self.local_port, self._adb_target)
            except Exception as e:
                log.warning("ADB_FORWARD_CLEAN_FAILED port=%s target=%s error=%s", self.local_port, self._adb_target, e)
            finally:
                self.local_port = None

    def push_server_to_device(self):
        log.info("SCRCPY_PUSH_SERVER target=%s", self._adb_target)
        cmd = [self.adb_path]
        if self._adb_target:
            cmd.extend(['-s', self._adb_target])
        cmd.extend(["push", SCRCPY_SERVER_PATH, DEVICE_SERVER_PATH])
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            log.error("SCRCPY_PUSH_SERVER_FAILED target=%s stderr=%s", self._adb_target, result.stderr.strip())
            return False
        return True

    def setup_adb_forward(self):
        # Clear any stale forward first.
        self.cleanup_adb_forward()
        
        # Allocate a fresh available local port.
        self.local_port = self.find_available_port()
        log.info("ADB_FORWARD_SETUP port=%s target=%s", self.local_port, self._adb_target)
        
        cmd = [self.adb_path]
        if self._adb_target:
            cmd.extend(['-s', self._adb_target])
        cmd.extend(["forward", f"tcp:{self.local_port}", "localabstract:scrcpy"])
        
        subprocess.run(cmd, check=True)

    def start_server(self):
        log.info("SCRCPY_SERVER_START target=%s mode=%s bitrate=%s max_size=%s max_fps=%s", self._adb_target, self.stream_mode, self.video_bit_rate, self.max_size, self.max_fps)
        cmd = [self.adb_path]
        if self._adb_target:
            cmd.extend(['-s', self._adb_target])
        server_cmd = self._build_server_command()
        cmd.extend(["shell", server_cmd])
        self.android_process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        while not self.stop:
            stderr_line = self.android_process.stderr.readline().decode().strip()
            if not stderr_line:
                break
            if stderr_line:
                self.last_error = stderr_line
                log.info("SCRCPY_SERVER target=%s line=%s", self._adb_target, stderr_line)
        self.android_process.wait()
        log.info("SCRCPY_SERVER_STOPPED target=%s returncode=%s", self._adb_target, self.android_process.returncode)
        if not self.stop:
            self._notify_stream_exit(f"scrcpy server exited with code {self.android_process.returncode}")

    def _build_server_command(self):
        server_cmd = (
            f"CLASSPATH={DEVICE_SERVER_PATH} app_process / "
            f"com.genymobile.scrcpy.Server 3.1 "
            f"tunnel_forward=true log_level=VERBOSE "
            f"video_bit_rate={self.video_bit_rate} "
            f"video_codec=h264 "
            f"video_codec_options=i-frame-interval={SCRCPY_I_FRAME_INTERVAL} "
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
            if receive_buffer:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, receive_buffer)
            if tcp_nodelay:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            try:
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
    ):
        self.video_bit_rate = video_bit_rate
        self.max_size = max_size
        self.max_fps = max_fps
        self.stream_mode = stream_mode if stream_mode in ("raw", "protocol", "legacy") else "raw"
        self.video_callback = video_callback
        self.stream_exit_callback = stream_exit_callback
        self.stop = False
        self.last_error = ""
        self.unexpected_exit_reason = ""
        self._exit_notified = False

        # Check device connection state and avoid misreading the adb devices header.
        state = self.adb_manager.get_device_state(self._adb_target)
        if state != 'device':
            self.last_error = f"Device {self._adb_target} not ready. state={state}"
            log.warning("SCRCPY_DEVICE_NOT_READY target=%s state=%s", self._adb_target, state)
            return False
        log.info("SCRCPY_DEVICE_READY target=%s state=%s", self._adb_target, state)

        if not self.push_server_to_device():
            log.error("SCRCPY_START_ABORT_PUSH_FAILED target=%s", self._adb_target)
            self.last_error = "failed to push scrcpy server"
            return False

        try:
            self.setup_adb_forward()
            self.android_thread = Thread(target=self.start_server, daemon=True)
            self.android_thread.start()

            # video connection
            self.video_socket = self._connect_forward_socket(receive_buffer=1024 * 1024, expect_dummy=True)
            log.info("SCRCPY_VIDEO_SOCKET_CONNECTED target=%s port=%s", self._adb_target, self.local_port)

            # control connection
            self.control_socket = self._connect_forward_socket(tcp_nodelay=True)
            log.info("SCRCPY_CONTROL_SOCKET_CONNECTED target=%s port=%s", self._adb_target, self.local_port)

            self.video_thread = Thread(target=self.receive_video_data, daemon=True)
            self.control_thread = Thread(target=self.handle_control_conn, daemon=True)
            self.video_thread.start()
            self.control_thread.start()
            log.info("SCRCPY_THREADS_STARTED target=%s mode=%s", self._adb_target, self.stream_mode)
            
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
        try:
            self.cleanup_adb_forward()
        except Exception as e:
            log.warning("SCRCPY_FORWARD_CLEAN_FAILED target=%s error=%s", self._adb_target, e)
        
        log.info("SCRCPY_STOPPED target=%s", self._adb_target)

    def scrcpy_send_control(self, data):
        try:
            if not hasattr(self, 'control_socket') or self.control_socket is None:
                log.warning("SCRCPY_CONTROL_SEND_NO_SOCKET target=%s", self._adb_target)
                return False
            
            # Check whether the control socket is still connected.
            try:
                self.control_socket.sendall(data)
                return True
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError) as e:
                log.warning("SCRCPY_CONTROL_SEND_LOST target=%s error=%s", self._adb_target, e)
                return False
            except Exception as e:
                log.warning("SCRCPY_CONTROL_SEND_FAILED target=%s error=%s", self._adb_target, e)
                return False
                
        except Exception as e:
            log.warning("SCRCPY_CONTROL_SEND_UNEXPECTED target=%s error=%s", self._adb_target, e)
            return False
