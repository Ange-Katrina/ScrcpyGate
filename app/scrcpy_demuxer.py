from dataclasses import dataclass
import logging
import struct


PACKET_FLAG_CONFIG = 1 << 63
PACKET_FLAG_KEY_FRAME = 1 << 62
PACKET_PTS_MASK = PACKET_FLAG_KEY_FRAME - 1
ANNEX_B_PREFIXES = (b"\x00\x00\x00\x01", b"\x00\x00\x01")
log = logging.getLogger("webscrcpy.scrcpy_demuxer")


@dataclass
class ScrcpyPacket:
    payload: bytes
    pts: int = 0
    keyframe: bool = False
    config: bool = False


class ScrcpyProtocolDemuxer:
    """Parse scrcpy framed video stream into packet payloads.

    This parser is intentionally tolerant. It handles optional forward dummy
    byte, optional device metadata, codec metadata, and then media packets with
    the 12-byte scrcpy frame header.
    """

    DEVICE_NAME_SIZE = 64
    CODEC_META_SIZE = 12
    FRAME_HEADER_SIZE = 12

    def __init__(self, max_packet_size: int = 16 * 1024 * 1024) -> None:
        self.buffer = bytearray()
        self.max_packet_size = max_packet_size
        self.state = "maybe_dummy"
        self.expected = 1
        self.device_name = ""
        self.codec_id = ""
        self.width: int | None = None
        self.height: int | None = None
        self._current_header: tuple[int, bool, bool, int] | None = None

    def feed(self, data: bytes) -> list[ScrcpyPacket]:
        if not data:
            return []
        self.buffer.extend(data)
        packets: list[ScrcpyPacket] = []

        while True:
            if self.state == "maybe_dummy":
                if len(self.buffer) < 1:
                    break
                if self._looks_like_annexb(self.buffer):
                    self.state = "raw"
                    self.expected = 0
                    break
                if self.buffer[0] == 0:
                    del self.buffer[0]
                self.state = "device_meta"
                self.expected = self.DEVICE_NAME_SIZE

            elif self.state == "device_meta":
                if len(self.buffer) < self.expected:
                    break
                name = bytes(self.buffer[: self.DEVICE_NAME_SIZE])
                del self.buffer[: self.DEVICE_NAME_SIZE]
                self.device_name = name.decode("utf-8", "replace").rstrip("\x00")
                self.state = "codec_meta"
                self.expected = self.CODEC_META_SIZE

            elif self.state == "codec_meta":
                if len(self.buffer) < self.expected:
                    break
                meta = bytes(self.buffer[: self.CODEC_META_SIZE])
                del self.buffer[: self.CODEC_META_SIZE]
                self.codec_id = meta[:4].decode("ascii", "replace")
                self.width = struct.unpack(">i", meta[4:8])[0]
                self.height = struct.unpack(">i", meta[8:12])[0]
                log.info("SCRCPY_CODEC codec=%s width=%s height=%s", self.codec_id, self.width, self.height)
                self.state = "frame_header"
                self.expected = self.FRAME_HEADER_SIZE

            elif self.state == "frame_header":
                if len(self.buffer) < self.expected:
                    break
                header = bytes(self.buffer[: self.FRAME_HEADER_SIZE])
                del self.buffer[: self.FRAME_HEADER_SIZE]
                pts_flags = struct.unpack(">Q", header[:8])[0]
                size = struct.unpack(">i", header[8:12])[0]
                if size <= 0 or size > self.max_packet_size:
                    log.warning("SCRCPY_PACKET_INVALID size=%s state=%s codec=%s", size, self.state, self.codec_id)
                    self.reset()
                    break
                self._current_header = (
                    pts_flags & PACKET_PTS_MASK,
                    bool(pts_flags & PACKET_FLAG_KEY_FRAME),
                    bool(pts_flags & PACKET_FLAG_CONFIG),
                    size,
                )
                self.state = "packet"
                self.expected = size

            elif self.state == "packet":
                if len(self.buffer) < self.expected:
                    break
                payload = bytes(self.buffer[: self.expected])
                del self.buffer[: self.expected]
                pts, keyframe, config, _size = self._current_header or (0, False, False, self.expected)
                packets.append(ScrcpyPacket(payload=payload, pts=pts, keyframe=keyframe, config=config))
                self._current_header = None
                self.state = "frame_header"
                self.expected = self.FRAME_HEADER_SIZE

            elif self.state == "raw":
                break

        if len(self.buffer) > self.max_packet_size * 2:
            self.reset()
        return packets

    def reset(self) -> None:
        self.buffer.clear()
        self.state = "maybe_dummy"
        self.expected = 1
        self._current_header = None

    @staticmethod
    def _looks_like_annexb(data: bytes | bytearray) -> bool:
        return any(bytes(data[: len(prefix)]) == prefix for prefix in ANNEX_B_PREFIXES if len(data) >= len(prefix))
