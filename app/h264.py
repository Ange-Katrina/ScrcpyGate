import os
from dataclasses import dataclass


H264_NAL_NON_IDR = 1
H264_NAL_IDR = 5
H264_NAL_SEI = 6
H264_NAL_SPS = 7
H264_NAL_PPS = 8
H264_NAL_AUD = 9
ANNEXB_START4 = b"\x00\x00\x00\x01"
ANNEXB_START3 = b"\x00\x00\x01"
RAW_MAX_NAL_BYTES = max(64 * 1024, int(os.environ.get("SCRCPY_RAW_MAX_NAL_BYTES", "8388608") or "8388608"))
RAW_MAX_ACCESS_UNIT_BYTES = max(
    RAW_MAX_NAL_BYTES,
    int(os.environ.get("SCRCPY_RAW_MAX_FRAME_BYTES", "16777216") or "16777216"),
)
FIRST_MB_SCAN_BYTES = 4096


def find_start_code(data: bytes | bytearray, start: int = 0) -> int:
    # Let the C-level bytes/bytearray search do the hot-path scan.  A 3-byte
    # match inside a 4-byte prefix starts one byte later; only fold that byte
    # back when it is still inside the requested search range.
    offset = max(0, int(start))
    candidate = data.find(ANNEXB_START3, offset)
    if candidate < 0:
        return -1
    if candidate > offset and data[candidate - 1] == 0:
        return candidate - 1
    return candidate


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

    escaped = nal[prefix_len + 1:prefix_len + 1 + FIRST_MB_SCAN_BYTES]
    zero_count = 0
    leading_zero_bits: int | None = None
    suffix = 0
    suffix_bits = 0
    for value in escaped:
        if zero_count >= 2 and value == 3:
            continue
        zero_count = zero_count + 1 if value == 0 else 0
        for shift in range(7, -1, -1):
            bit = (value >> shift) & 1
            if leading_zero_bits is None:
                if bit:
                    leading_zero_bits = suffix_bits
                    suffix_bits = 0
                    if leading_zero_bits == 0:
                        return 0
                else:
                    suffix_bits += 1
                continue
            if suffix_bits < leading_zero_bits:
                suffix = (suffix << 1) | bit
                suffix_bits += 1
                if suffix_bits == leading_zero_bits:
                    return (1 << leading_zero_bits) - 1 + suffix
    # A malformed or adversarial NAL is bounded by FIRST_MB_SCAN_BYTES and is
    # rejected by the caller rather than forcing a full-buffer RBSP copy.
    return None


def is_first_vcl_nal(nal: bytes | bytearray) -> bool:
    return first_mb_in_slice(nal) == 0


def annexb_nal_units(data: bytes | bytearray) -> list[bytes]:
    """Return complete Annex-B NAL units from one already-delimited payload."""
    raw = bytes(data)
    first = find_start_code(raw)
    if first < 0:
        return []
    units: list[bytes] = []
    position = first
    while position >= 0:
        next_start = find_start_code(raw, position + max(1, start_code_length(raw, position)))
        end = len(raw) if next_start < 0 else next_start
        unit = raw[position:end]
        prefix_len = start_code_length(unit, 0)
        if prefix_len > 0 and len(unit) > prefix_len:
            units.append(unit)
        position = next_start
    return units


@dataclass(frozen=True)
class H264AccessUnit:
    payload: bytes
    keyframe: bool
    contains_config: bool
    nal_types: tuple[int, ...]


class H264AccessUnitAssembler:
    """Group raw Annex-B NAL units into complete decoder access units."""

    def __init__(
        self,
        *,
        max_nal_bytes: int = RAW_MAX_NAL_BYTES,
        max_access_unit_bytes: int = RAW_MAX_ACCESS_UNIT_BYTES,
    ) -> None:
        self.max_nal_bytes = max(1, int(max_nal_bytes))
        self.max_access_unit_bytes = max(self.max_nal_bytes, int(max_access_unit_bytes))
        self.parser = H264AnnexBParser(max_buffer_size=self.max_nal_bytes)
        self._pending: list[bytes] = []
        self._pending_bytes = 0
        self._has_vcl = False
        self._keyframe = False
        self._contains_config = False
        self._nal_types: list[int] = []
        self.nals_seen = 0
        self.access_units_emitted = 0
        self.dropped_nals = 0
        self.dropped_access_units = 0
        self.resyncs = 0
        self.peak_access_unit_bytes = 0

    @property
    def stats(self) -> dict[str, int]:
        return {
            "nals_seen": self.nals_seen,
            "access_units_emitted": self.access_units_emitted,
            "dropped_nals": self.dropped_nals,
            "dropped_access_units": self.dropped_access_units,
            "resyncs": self.resyncs,
            "parser_resyncs": self.parser.resyncs,
            "parser_discarded_bytes": self.parser.discarded_bytes,
            "peak_parser_buffer_bytes": self.parser.peak_buffer_bytes,
            "peak_access_unit_bytes": self.peak_access_unit_bytes,
            "pending_bytes": self._pending_bytes,
        }

    def feed(self, data: bytes) -> list[H264AccessUnit]:
        units: list[H264AccessUnit] = []
        parser_resyncs = self.parser.resyncs
        for nal in self.parser.feed(data):
            emitted = self._append_nal(nal)
            if emitted is not None:
                units.append(emitted)
        self.resyncs += self.parser.resyncs - parser_resyncs
        return units

    def flush(self) -> list[H264AccessUnit]:
        units: list[H264AccessUnit] = []
        for nal in self.parser.flush():
            emitted = self._append_nal(nal)
            if emitted is not None:
                units.append(emitted)
        emitted = self._emit_pending()
        if emitted is not None:
            units.append(emitted)
        return units

    def _append_nal(self, nal: bytes) -> H264AccessUnit | None:
        self.nals_seen += 1
        if len(nal) > self.max_nal_bytes:
            self.dropped_nals += 1
            self.resyncs += 1
            self._clear_pending()
            return None
        ntype = nal_type(nal)
        if ntype < 0:
            self.dropped_nals += 1
            self.resyncs += 1
            self._clear_pending()
            return None
        is_vcl = ntype in (H264_NAL_NON_IDR, H264_NAL_IDR)
        first_vcl = is_vcl and is_first_vcl_nal(nal)
        is_boundary = self._has_vcl and (
            first_vcl or ntype in (H264_NAL_AUD, H264_NAL_SPS, H264_NAL_PPS)
        )
        emitted = self._emit_pending() if is_boundary else None
        if emitted is not None:
            if not self._append_to_pending(nal, ntype, is_vcl, first_vcl):
                self.dropped_access_units += 1
                self.resyncs += 1
            return emitted
        if not self._append_to_pending(nal, ntype, is_vcl, first_vcl):
            self.dropped_access_units += 1
            self.resyncs += 1
        return None

    def _append_to_pending(self, nal: bytes, ntype: int, is_vcl: bool, first_vcl: bool) -> bool:
        projected = self._pending_bytes + len(nal)
        if projected > self.max_access_unit_bytes:
            self._clear_pending()
            return False
        self._pending.append(nal)
        self._pending_bytes = projected
        self.peak_access_unit_bytes = max(self.peak_access_unit_bytes, projected)
        self._nal_types.append(ntype)
        self._contains_config = self._contains_config or ntype in (H264_NAL_SPS, H264_NAL_PPS)
        self._has_vcl = self._has_vcl or is_vcl
        self._keyframe = self._keyframe or (is_vcl and ntype == H264_NAL_IDR and first_vcl)
        return True

    def _emit_pending(self) -> H264AccessUnit | None:
        if not self._pending or not self._has_vcl:
            return None
        unit = H264AccessUnit(
            payload=b"".join(self._pending),
            keyframe=self._keyframe,
            contains_config=self._contains_config,
            nal_types=tuple(self._nal_types),
        )
        self.access_units_emitted += 1
        self._clear_pending()
        return unit

    def _clear_pending(self) -> None:
        self._pending.clear()
        self._pending_bytes = 0
        self._has_vcl = False
        self._keyframe = False
        self._contains_config = False
        self._nal_types.clear()


class H264AnnexBParser:
    def __init__(self, max_buffer_size: int = 8 * 1024 * 1024):
        self.buffer = bytearray()
        self.max_buffer_size = max(1, int(max_buffer_size))
        self.resyncs = 0
        self.discarded_bytes = 0
        self.peak_buffer_bytes = 0

    def feed(self, data: bytes) -> list[bytes]:
        if not data:
            return []
        self.buffer.extend(data)
        self.peak_buffer_bytes = max(self.peak_buffer_bytes, len(self.buffer))
        first = find_start_code(self.buffer)
        if first < 0:
            self._trim_without_start_code()
            return []
        if first > 0:
            self.discarded_bytes += first
            self.resyncs += 1
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

    def flush(self) -> list[bytes]:
        if not self.buffer:
            return []
        prefix_len = start_code_length(self.buffer, 0)
        if prefix_len <= 0 or len(self.buffer) <= prefix_len:
            self.buffer.clear()
            return []
        nal = bytes(self.buffer)
        self.buffer.clear()
        return [nal]

    def _trim_without_start_code(self) -> None:
        if len(self.buffer) > self.max_buffer_size:
            keep = min(4, len(self.buffer))
            discarded = len(self.buffer) - keep
            if discarded > 0:
                self.discarded_bytes += discarded
                self.resyncs += 1
                del self.buffer[:discarded]

    def _trim_oversized_partial_nal(self) -> None:
        if len(self.buffer) <= self.max_buffer_size:
            return
        last = find_start_code(self.buffer, max(0, len(self.buffer) - 128))
        if last > 0:
            self.discarded_bytes += last
            self.resyncs += 1
            del self.buffer[:last]
        else:
            self.discarded_bytes += len(self.buffer)
            self.resyncs += 1
            self.buffer.clear()
