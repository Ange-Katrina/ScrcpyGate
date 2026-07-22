H264_NAL_NON_IDR = 1
H264_NAL_IDR = 5
H264_NAL_SEI = 6
H264_NAL_SPS = 7
H264_NAL_PPS = 8
H264_NAL_AUD = 9
ANNEXB_START4 = b"\x00\x00\x00\x01"
ANNEXB_START3 = b"\x00\x00\x01"


def find_start_code(data: bytes | bytearray, start: int = 0) -> int:
    size = len(data)
    i = max(0, start)
    while i <= size - 3:
        if data[i] == 0 and data[i + 1] == 0:
            if data[i + 2] == 1:
                return i
            if i + 3 < size and data[i + 2] == 0 and data[i + 3] == 1:
                return i
        i += 1
    return -1


def is_annexb(data: bytes | bytearray) -> bool:
    return bytes(data[:4]) == ANNEXB_START4 or bytes(data[:3]) == ANNEXB_START3


def length_prefixed_to_annexb(data: bytes | bytearray, length_size: int = 4) -> bytes:
    raw = bytes(data)
    if is_annexb(raw):
        return raw
    if length_size not in (2, 4):
        return raw
    out = bytearray()
    offset = 0
    size = len(raw)
    while offset + length_size <= size:
        if length_size == 4:
            nal_size = int.from_bytes(raw[offset:offset + 4], "big")
        else:
            nal_size = int.from_bytes(raw[offset:offset + 2], "big")
        offset += length_size
        if nal_size <= 0 or offset + nal_size > size:
            return raw
        out.extend(ANNEXB_START4)
        out.extend(raw[offset:offset + nal_size])
        offset += nal_size
    return bytes(out) if out and offset == size else raw


def avcc_config_to_annexb(data: bytes | bytearray) -> bytes:
    """Convert an AVCDecoderConfigurationRecord to Annex-B SPS/PPS bytes."""
    raw = bytes(data)
    if len(raw) < 7 or raw[0] != 1:
        return raw
    offset = 5
    sps_count = raw[offset] & 0x1F
    offset += 1
    out = bytearray()
    for _ in range(sps_count):
        if offset + 2 > len(raw):
            return raw
        size = int.from_bytes(raw[offset:offset + 2], "big")
        offset += 2
        if size <= 0 or offset + size > len(raw):
            return raw
        out.extend(ANNEXB_START4)
        out.extend(raw[offset:offset + size])
        offset += size
    if offset >= len(raw):
        return raw
    pps_count = raw[offset]
    offset += 1
    for _ in range(pps_count):
        if offset + 2 > len(raw):
            return raw
        size = int.from_bytes(raw[offset:offset + 2], "big")
        offset += 2
        if size <= 0 or offset + size > len(raw):
            return raw
        out.extend(ANNEXB_START4)
        out.extend(raw[offset:offset + size])
        offset += size
    return bytes(out) if out and offset == len(raw) else raw


def normalize_h264_payload(data: bytes | bytearray) -> bytes:
    """Return H264 payload in Annex-B form when it can be detected safely."""
    raw = bytes(data)
    if not raw or is_annexb(raw):
        return raw

    converted = avcc_config_to_annexb(raw)
    if converted is not raw and is_annexb(converted):
        return converted

    for length_size in (4, 2):
        converted = length_prefixed_to_annexb(raw, length_size)
        if converted != raw and is_annexb(converted):
            return converted

    first_type = raw[0] & 0x1F
    if first_type in {H264_NAL_NON_IDR, H264_NAL_IDR, H264_NAL_SEI, H264_NAL_SPS, H264_NAL_PPS, H264_NAL_AUD}:
        return ANNEXB_START4 + raw

    return raw


def annexb_nal_types(data: bytes | bytearray, limit: int = 8) -> list[int]:
    raw = bytes(data)
    types: list[int] = []
    pos = find_start_code(raw)
    while pos >= 0 and len(types) < limit:
        prefix_len = start_code_length(raw, pos)
        if prefix_len <= 0:
            break
        nal_start = pos + prefix_len
        if nal_start >= len(raw):
            break
        types.append(raw[nal_start] & 0x1F)
        pos = find_start_code(raw, nal_start + 1)
    return types


def start_code_length(data: bytes | bytearray, index: int = 0) -> int:
    if index + 2 < len(data) and data[index] == 0 and data[index + 1] == 0 and data[index + 2] == 1:
        return 3
    if index + 3 < len(data) and data[index] == 0 and data[index + 1] == 0 and data[index + 2] == 0 and data[index + 3] == 1:
        return 4
    return 0


def nal_type(nal: bytes | bytearray) -> int:
    prefix_len = start_code_length(nal, 0)
    if prefix_len <= 0 or prefix_len >= len(nal):
        return -1
    return nal[prefix_len] & 0x1F


def first_mb_in_slice(nal: bytes | bytearray) -> int | None:
    """Return H.264 first_mb_in_slice for VCL NAL units."""
    prefix_len = start_code_length(nal, 0)
    if prefix_len <= 0 or prefix_len + 1 >= len(nal):
        return None
    if nal[prefix_len] & 0x1F not in (H264_NAL_NON_IDR, H264_NAL_IDR):
        return None

    escaped = bytes(nal[prefix_len + 1:])
    rbsp = bytearray()
    zero_count = 0
    for value in escaped:
        if zero_count >= 2 and value == 3:
            continue
        rbsp.append(value)
        zero_count = zero_count + 1 if value == 0 else 0

    bit_count = len(rbsp) * 8
    bit_index = 0
    leading_zero_bits = 0
    while bit_index < bit_count:
        value = (rbsp[bit_index // 8] >> (7 - bit_index % 8)) & 1
        bit_index += 1
        if value:
            break
        leading_zero_bits += 1
    else:
        return None

    if bit_index + leading_zero_bits > bit_count:
        return None
    suffix = 0
    for _ in range(leading_zero_bits):
        suffix = (suffix << 1) | ((rbsp[bit_index // 8] >> (7 - bit_index % 8)) & 1)
        bit_index += 1
    return (1 << leading_zero_bits) - 1 + suffix


def is_first_vcl_nal(nal: bytes | bytearray) -> bool:
    return first_mb_in_slice(nal) == 0


def is_keyframe(nal: bytes | bytearray) -> bool:
    return nal_type(nal) == H264_NAL_IDR


def is_config(nal: bytes | bytearray) -> bool:
    return nal_type(nal) in (H264_NAL_SPS, H264_NAL_PPS)


class H264AnnexBParser:
    def __init__(self, max_buffer_size: int = 8 * 1024 * 1024):
        self.buffer = bytearray()
        self.max_buffer_size = max_buffer_size

    def feed(self, data: bytes) -> list[bytes]:
        if not data:
            return []
        self.buffer.extend(data)
        first = find_start_code(self.buffer)
        if first < 0:
            self._trim_without_start_code()
            return []
        if first > 0:
            del self.buffer[:first]

        nal_units = []
        while True:
            prefix_len = start_code_length(self.buffer, 0)
            if prefix_len <= 0:
                self.buffer.clear()
                break
            next_start = find_start_code(self.buffer, prefix_len)
            if next_start < 0:
                self._trim_oversized_partial_nal()
                break
            nal = bytes(self.buffer[:next_start])
            if len(nal) > prefix_len:
                nal_units.append(nal)
            del self.buffer[:next_start]
        return nal_units

    def _trim_without_start_code(self) -> None:
        if len(self.buffer) > self.max_buffer_size:
            del self.buffer[:-4]

    def _trim_oversized_partial_nal(self) -> None:
        if len(self.buffer) <= self.max_buffer_size:
            return
        last = find_start_code(self.buffer, max(0, len(self.buffer) - 128))
        if last > 0:
            del self.buffer[:last]
        else:
            self.buffer.clear()
