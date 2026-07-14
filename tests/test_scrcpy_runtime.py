import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scrcpy import Scrcpy


class ScrcpyRuntimeTests(unittest.TestCase):
    @staticmethod
    def make_scrcpy(mode="raw"):
        runtime = object.__new__(Scrcpy)
        runtime.video_bit_rate = 2_000_000
        runtime.max_size = 720
        runtime.max_fps = 30
        runtime.stream_mode = mode
        return runtime

    def test_raw_command_keeps_forward_handshake_and_disables_only_stream_metadata(self):
        runtime = self.make_scrcpy("raw")

        with patch("scrcpy.SCRCPY_I_FRAME_INTERVAL", 1):
            command = runtime._build_server_command()

        self.assertIn("video_codec_options=i-frame-interval=1", command)
        self.assertIn("send_device_meta=false", command)
        self.assertIn("send_frame_meta=false", command)
        self.assertIn("send_codec_meta=false", command)
        self.assertNotIn("raw_stream=true", command)
        self.assertNotIn("send_dummy_byte=false", command)

    def test_protocol_command_preserves_scrcpy_packet_metadata(self):
        runtime = self.make_scrcpy("protocol")

        command = runtime._build_server_command()

        self.assertNotIn("send_device_meta=false", command)
        self.assertNotIn("send_frame_meta=false", command)
        self.assertNotIn("send_codec_meta=false", command)
        self.assertIn("max_size=720", command)
        self.assertIn("max_fps=30", command)

    def test_forward_handshake_consumes_dummy_only_when_requested(self):
        class FakeSocket:
            def __init__(self, dummy=b"\x00"):
                self.dummy = dummy
                self.recv_calls = 0
                self.closed = False

            def setsockopt(self, *_args):
                pass

            def settimeout(self, _value):
                pass

            def connect(self, _address):
                pass

            def recv(self, _size):
                self.recv_calls += 1
                return self.dummy

            def close(self):
                self.closed = True

        runtime = object.__new__(Scrcpy)
        runtime.stop = False
        runtime.android_process = None
        runtime.local_port = 6666
        video_socket = FakeSocket()
        control_socket = FakeSocket()

        with patch("scrcpy.socket.socket", side_effect=[video_socket, control_socket]):
            self.assertIs(runtime._connect_forward_socket(expect_dummy=True), video_socket)
            self.assertIs(runtime._connect_forward_socket(expect_dummy=False), control_socket)

        self.assertEqual(video_socket.recv_calls, 1)
        self.assertEqual(control_socket.recv_calls, 0)

    def test_forward_handshake_retries_after_adb_forward_dummy_eof(self):
        class FakeSocket:
            def __init__(self, dummy):
                self.dummy = dummy
                self.closed = False

            def setsockopt(self, *_args):
                pass

            def settimeout(self, _value):
                pass

            def connect(self, _address):
                pass

            def recv(self, _size):
                return self.dummy

            def close(self):
                self.closed = True

        runtime = object.__new__(Scrcpy)
        runtime.stop = False
        runtime.android_process = None
        runtime.local_port = 6666
        early = FakeSocket(b"")
        ready = FakeSocket(b"\x00")

        with (
            patch("scrcpy.socket.socket", side_effect=[early, ready]),
            patch("scrcpy.time.sleep"),
        ):
            connected = runtime._connect_forward_socket(expect_dummy=True)

        self.assertTrue(early.closed)
        self.assertIs(connected, ready)


if __name__ == "__main__":
    unittest.main()
