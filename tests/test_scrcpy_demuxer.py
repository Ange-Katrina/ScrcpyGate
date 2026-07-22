import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.scrcpy_demuxer import PACKET_FLAG_CONFIG, PACKET_FLAG_KEY_FRAME, ScrcpyProtocolDemuxer


class ScrcpyProtocolDemuxerTests(unittest.TestCase):
    def make_stream(self, payloads):
        buf = bytearray()
        buf.append(0)
        name = b"test-device"
        buf.extend(name + b"\x00" * (64 - len(name)))
        buf.extend(struct.pack(">iii", 0x68323634, 1280, 720))
        for pts, flags, payload in payloads:
            buf.extend(struct.pack(">Qi", pts | flags, len(payload)))
            buf.extend(payload)
        return bytes(buf)

    def test_demuxes_dummy_metadata_and_packets(self):
        config = b"\x00\x00\x00\x01\x67\x42"
        frame = b"\x00\x00\x00\x01\x65\x88"
        stream = self.make_stream([(0, PACKET_FLAG_CONFIG, config), (33333, PACKET_FLAG_KEY_FRAME, frame)])

        demuxer = ScrcpyProtocolDemuxer()
        packets = demuxer.feed(stream)

        self.assertEqual(demuxer.device_name, "test-device")
        self.assertEqual(demuxer.width, 1280)
        self.assertEqual(demuxer.height, 720)
        self.assertEqual(len(packets), 2)
        self.assertTrue(packets[0].config)
        self.assertTrue(packets[1].keyframe)
        self.assertEqual(packets[1].payload, frame)

    def test_fragmented_feed(self):
        frame = b"\x00\x00\x00\x01\x65\x88"
        stream = self.make_stream([(0, PACKET_FLAG_KEY_FRAME, frame)])
        demuxer = ScrcpyProtocolDemuxer()
        out = []
        for i in range(0, len(stream), 5):
            out.extend(demuxer.feed(stream[i:i + 5]))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].payload, frame)


if __name__ == "__main__":
    unittest.main()
