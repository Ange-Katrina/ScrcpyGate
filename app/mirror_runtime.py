import asyncio
import logging
import os
import struct
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from fastapi import WebSocket

from scrcpy import Scrcpy
from . import storage
from .adb_monitor import adb_monitor
from .h264 import (
    H264AccessUnit,
    H264AccessUnitAssembler,
    H264AnnexBParser,
    H264_NAL_IDR,
    H264_NAL_PPS,
    H264_NAL_SPS,
    annexb_nal_units,
    annexb_nal_types,
    is_annexb,
    is_first_vcl_nal,
    nal_type,
    normalize_h264_payload,
)
from .scrcpy_demuxer import ScrcpyProtocolDemuxer, ScrcpyPacket
from .video_options import enabled_stream_modes_value, public_video_options, settings_to_video_options, signature, stream_mode_or_default

VIDEO_QUEUE_MAXSIZE = max(8, int(os.environ.get("VIDEO_QUEUE_MAXSIZE", "24") or "24"))
# Keep a small item-count soft limit even when no byte-level override is set.
# This preserves the slow-viewer recovery boundary used by the existing
# queue tests and avoids allowing a bounded queue to fill with stale frames.
#
# 8 items（60fps 下约 130ms）而不是 3（约 50ms）：多个观看端同时解码时，浏览器
# 主线程一次 GC/重排就可能让某个观看端 50ms 以上不读 socket，3 帧的阈值会把这种
# 抖动直接升级成「丢帧 → 补关键帧 → 请求编码器重置」，观看端就会看到反复黑闪。
# 代价是观看端真的卡住时要多缓冲几帧才会被重置（延迟上限略有上升，客户端还有
# 延迟回跳兜底），可用 VIDEO_QUEUE_SOFT_LIMIT 调回。
VIDEO_QUEUE_SOFT_LIMIT = max(0, int(os.environ.get("VIDEO_QUEUE_SOFT_LIMIT", "8") or "8"))
VIDEO_QUEUE_MAX_BYTES = max(256 * 1024, int(os.environ.get("VIDEO_QUEUE_MAX_BYTES", "8388608") or "8388608"))
RAW_V2_MAX_PACKET_BYTES = max(
    VIDEO_QUEUE_MAX_BYTES,
    int(os.environ.get("SCRCPY_RAW_MAX_PACKET_BYTES", "25165824") or "25165824"),
)
VIDEO_RESET_COOLDOWN = max(0.1, float(os.environ.get("VIDEO_RESET_COOLDOWN", "0.75") or "0.75"))
CONTROL_LEASE_VERIFY_INTERVAL = max(0.5, float(os.environ.get("CONTROL_LEASE_VERIFY_INTERVAL", "5") or "5"))
CONTROL_PERMISSION_RECHECK_INTERVAL = max(
    1.0,
    float(os.environ.get("CONTROL_PERMISSION_RECHECK_INTERVAL", "5") or "5"),
)
SCRCPY_RESET_VIDEO_MESSAGE = b"\x11"
SCRCPY_CLIENT_TEXT_MAX_BYTES = 300
SCRCPY_CLIENT_CONTROL_MAX_BYTES = 5 + SCRCPY_CLIENT_TEXT_MAX_BYTES
SCRCPY_CLIENT_CONTROL_MAX_BASE64_CHARS = ((SCRCPY_CLIENT_CONTROL_MAX_BYTES + 2) // 3) * 4
SCRCPY_ALLOWED_CLIENT_CONTROL_TYPES = frozenset({0, 1, 2, 3, 4})
SCRCPY_STREAM_MODE = os.environ.get("SCRCPY_STREAM_MODE", "raw").strip().lower() or "raw"
STREAM_HEALTH_TIMEOUT = float(os.environ.get("SCRCPY_STREAM_HEALTH_TIMEOUT", "5") or "5")
# Do not hold the HTTP start request until a video keyframe arrives.  ADB and
# WebSocket setup are the transport readiness boundary; the browser can then
# receive a cached keyframe or request a reset without the request timing out
# behind a slow encoder/network path.  Half a second still catches an encoder
# that is already producing its first IDR, while keeping the browser from
# waiting for the normal one-second scrcpy IDR interval before opening video.
STREAM_START_RESPONSE_TIMEOUT = max(
    0.25,
    min(
        STREAM_HEALTH_TIMEOUT,
        float(os.environ.get("SCRCPY_STREAM_START_RESPONSE_TIMEOUT", "0.5") or "0.5"),
    ),
)
VIEWER_RESERVATION_TTL = max(5.0, float(os.environ.get("MIRROR_VIEWER_RESERVATION_TTL", "15") or "15"))
VIEWER_DISCONNECT_GRACE = max(1.0, float(os.environ.get("MIRROR_VIEWER_DISCONNECT_GRACE", "5") or "5"))
VIDEO_PERMISSION_RECHECK_SECONDS = max(2.0, float(os.environ.get("VIDEO_PERMISSION_RECHECK_SECONDS", "10") or "10"))
# A client that cannot accept a frame for several seconds is already showing
# stale content. Drop it promptly so other viewers and control messages keep
# the event loop responsive; the browser's bounded reconnect path can recover.
VIDEO_SEND_TIMEOUT_SECONDS = max(1.0, float(os.environ.get("VIDEO_SEND_TIMEOUT_SECONDS", "3") or "3"))
# One browser tab keeps one video socket per grid tile, so the grid view needs
# a slot for every tile: the workspace grid is fixed at 12 slots (see
# ``static/shared/mirror-grid.js``) and the limit matches it. Raise the
# environment value only together with the client slot count, otherwise the
# extra tiles are rejected with close code 4429 and stay black.
MAX_VIDEO_CONNECTIONS_PER_USER = max(1, int(os.environ.get("MAX_VIDEO_CONNECTIONS_PER_USER", "12") or "12"))
try:
    _mirror_cancel_cleanup_timeout = float(
        os.environ.get("MIRROR_CANCEL_CLEANUP_TIMEOUT_SECONDS", "10") or "10"
    )
except (TypeError, ValueError):
    _mirror_cancel_cleanup_timeout = 10.0
MIRROR_CANCEL_CLEANUP_TIMEOUT_SECONDS = max(
    1.0,
    min(60.0, _mirror_cancel_cleanup_timeout),
)
_control_epoch_lock = threading.RLock()
_control_device_locks: dict[str, threading.Lock] = {}
_control_lock_epochs: dict[str, int] = {}
log = logging.getLogger("webscrcpy.mirror")
ControlAuditCallback = Callable[..., Awaitable[None]]


def _mirror_manager():
    """Resolve the process-wide manager lazily to keep the runtime acyclic."""
    from .mirror_manager import manager

    return manager

RAW_V2_MAGIC = b"SGV2"
RAW_V2_VERSION = 1
RAW_V2_HEADER_LENGTH = 32
RAW_V2_FLAG_KEYFRAME = 0x01
RAW_V2_FLAG_CONFIG = 0x02
RAW_V2_FLAG_DISCONTINUITY = 0x04
RAW_V2_HEADER = struct.Struct(">4sBBHIIQHHI")


def read_capture_display_rotation(address: str) -> int | None:
    """Best-effort read of the device's current display rotation (0..3).

    Baseline for the opt-in encoder compatibility workaround. Normal rotation is
    handled by scrcpy itself. Never raises: unknown rotation disables the workaround.
    """
    try:
        from adb_manager import ADBManager

        return ADBManager().device_display_rotation(address)
    except Exception:
        return None


def _raw_v2_sequence(payload: bytes | bytearray | memoryview) -> int | None:
    """Read a trusted Raw v2 sequence without copying the video payload."""
    raw = memoryview(payload)
    if len(raw) < RAW_V2_HEADER_LENGTH or bytes(raw[:4]) != RAW_V2_MAGIC:
        return None
    return int.from_bytes(raw[8:12], "big")


def _raw_sequence_is_newer(next_sequence: int, current_sequence: int | None) -> bool:
    if current_sequence is None:
        return True
    distance = (int(next_sequence) - int(current_sequence)) & 0xFFFFFFFF
    return distance != 0 and distance < 0x80000000


@dataclass(frozen=True)
class RawV2Packet:
    frame_sequence: int
    config_generation: int
    arrival_timestamp_us: int
    width: int
    height: int
    payload: bytes
    keyframe: bool = False
    contains_config: bool = False
    discontinuity: bool = False


def pack_raw_v2_packet(packet: RawV2Packet, *, max_packet_bytes: int = RAW_V2_MAX_PACKET_BYTES) -> bytes:
    payload = bytes(packet.payload)
    width = int(packet.width)
    height = int(packet.height)
    if len(payload) > 0xFFFFFFFF:
        raise ValueError("raw v2 payload is too large")
    if not 0 <= width <= 0xFFFF or not 0 <= height <= 0xFFFF:
        raise ValueError("raw v2 dimensions are out of range")
    if not 0 <= int(packet.frame_sequence) <= 0xFFFFFFFF:
        raise ValueError("raw v2 frame sequence is out of range")
    if not 0 <= int(packet.config_generation) <= 0xFFFFFFFF:
        raise ValueError("raw v2 config generation is out of range")
    timestamp = int(packet.arrival_timestamp_us)
    if not 0 <= timestamp <= 0xFFFFFFFFFFFFFFFF:
        raise ValueError("raw v2 timestamp is out of range")
    flags = 0
    if packet.keyframe:
        flags |= RAW_V2_FLAG_KEYFRAME
    if packet.contains_config:
        flags |= RAW_V2_FLAG_CONFIG
    if packet.discontinuity:
        flags |= RAW_V2_FLAG_DISCONTINUITY
    if RAW_V2_HEADER_LENGTH + len(payload) > int(max_packet_bytes):
        raise ValueError("raw v2 packet exceeds configured limit")
    return RAW_V2_HEADER.pack(
        RAW_V2_MAGIC,
        RAW_V2_VERSION,
        flags,
        RAW_V2_HEADER_LENGTH,
        int(packet.frame_sequence),
        int(packet.config_generation),
        timestamp,
        width,
        height,
        len(payload),
    ) + payload


def unpack_raw_v2_packet(data: bytes | bytearray, *, max_packet_bytes: int = RAW_V2_MAX_PACKET_BYTES) -> RawV2Packet:
    raw = bytes(data)
    if len(raw) < RAW_V2_HEADER_LENGTH:
        raise ValueError("raw v2 packet is shorter than its header")
    magic, version, flags, header_length, sequence, generation, timestamp, width, height, payload_length = (
        RAW_V2_HEADER.unpack_from(raw)
    )
    if magic != RAW_V2_MAGIC:
        raise ValueError("raw v2 packet has invalid magic")
    if version != RAW_V2_VERSION:
        raise ValueError("raw v2 packet has unsupported version")
    if header_length != RAW_V2_HEADER_LENGTH:
        raise ValueError("raw v2 packet has invalid header length")
    known_flags = RAW_V2_FLAG_KEYFRAME | RAW_V2_FLAG_CONFIG | RAW_V2_FLAG_DISCONTINUITY
    if flags & ~known_flags:
        raise ValueError("raw v2 packet has unknown flags")
    if payload_length > int(max_packet_bytes) - RAW_V2_HEADER_LENGTH:
        raise ValueError("raw v2 payload exceeds configured limit")
    if header_length + payload_length != len(raw):
        raise ValueError("raw v2 payload length does not match packet")
    return RawV2Packet(
        frame_sequence=sequence,
        config_generation=generation,
        arrival_timestamp_us=timestamp,
        width=width,
        height=height,
        payload=raw[header_length:],
        keyframe=bool(flags & RAW_V2_FLAG_KEYFRAME),
        contains_config=bool(flags & RAW_V2_FLAG_CONFIG),
        discontinuity=bool(flags & RAW_V2_FLAG_DISCONTINUITY),
    )

def _stream_mode() -> str:
    try:
        settings = storage.get_settings(
            ["scrcpy_stream_mode", "scrcpy_enabled_stream_modes"]
        )
        configured = settings.get("scrcpy_stream_mode", SCRCPY_STREAM_MODE)
        enabled = enabled_stream_modes_value(
            settings.get("scrcpy_enabled_stream_modes", "raw")
        )
    except Exception:
        configured = SCRCPY_STREAM_MODE
        enabled = ("raw",)
    configured = str(configured or "raw").strip().lower()
    configured = configured if configured in ("raw", "protocol", "legacy") else "raw"
    return stream_mode_or_default(configured, enabled)


def control_lock_epoch(device_id: str) -> int:
    with _control_epoch_lock:
        return _control_lock_epochs.get(device_id, 0)


def _bump_control_lock_epoch(device_id: str) -> int:
    epoch = _control_lock_epochs.get(device_id, 0) + 1
    _control_lock_epochs[device_id] = epoch
    return epoch


def _control_device_lock(device_id: str) -> threading.Lock:
    with _control_epoch_lock:
        return _control_device_locks.setdefault(device_id, threading.Lock())


def acquire_control_lock(device_id: str, username: str, client_id: str, *, force: bool = False) -> tuple[dict[str, Any], int]:
    with _control_device_lock(device_id):
        result = storage.acquire_lock(device_id, username, client_id, force=force)
        with _control_epoch_lock:
            epoch = _bump_control_lock_epoch(device_id) if result.get("ok") else _control_lock_epochs.get(device_id, 0)
        return result, epoch


def renew_control_lock(device_id: str, username: str, client_id: str, *, ttl_seconds: int = 90) -> tuple[bool, int]:
    with _control_device_lock(device_id):
        ok = storage.renew_lock(device_id, username, client_id, ttl_seconds=ttl_seconds)
        with _control_epoch_lock:
            return ok, _control_lock_epochs.get(device_id, 0)


def release_control_lock(
    device_id: str,
    username: str,
    *,
    force: bool = False,
    client_id: str | None = None,
    only_if_owned: bool = False,
) -> bool:
    with _control_device_lock(device_id):
        if only_if_owned and (
            client_id is None or not storage.lock_owned_by(device_id, username, client_id)
        ):
            return False
        ok = storage.release_lock(device_id, username, force=force, client_id=client_id)
        if ok:
            with _control_epoch_lock:
                _bump_control_lock_epoch(device_id)
        return ok


@dataclass(frozen=True)
class StreamReset:
    generation: int
    video: dict[str, Any] | None = None


@dataclass(frozen=True)
class StreamTermination:
    code: int = 4410
    reason: str = "stream stopped"


@dataclass(frozen=True)
class StreamNotice:
    """服务端经同一条视频 WebSocket 下发的 JSON 通知（控制面，不占视频语义）。

    用于「投屏记录」这类跨观看端协同：邀请、请上传、参与者状态、汇总回传。
    通知与视频帧共用队列，但在 clear_queue() 里被保留 —— 丢一帧无所谓，
    丢一条邀请/上传请求会让整次记录静默失败。
    """

    payload: dict[str, Any]


@dataclass
class ClientSession:
    id: str
    username: str
    websocket: WebSocket
    viewer_token: str = ""
    # 浏览器侧的稳定设备标识（localStorage 里的一份随机 id）：连接级 id 每次都变，
    # 用它才能判断「是不是同一台设备」（投屏记录面板与导出都显示它）。
    browser_id: str = ""
    # 宫格观看端：宫格一路多端混进单画面的记录里只会互相干扰，因此不参与投屏记录。
    grid_view: bool = False
    connected_at: int = field(default_factory=lambda: int(time.time()))
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=VIDEO_QUEUE_MAXSIZE))
    queue_max_bytes: int = VIDEO_QUEUE_MAX_BYTES
    queue_bytes: int = 0
    drops: int = 0
    needs_keyframe: bool = True
    last_raw_sequence_delivered: int | None = None
    terminated: bool = False

    def clear_queue(self) -> None:
        """Drop queued frames while preserving control-plane signals.

        A congested queue is cleared from the producer side while the video
        sender is blocked in ``send_bytes``.  Dropping the ``StreamTermination``
        (or the ``None`` stream-ended sentinel) in that window would leave the
        WebSocket open forever, and dropping a ``StreamNotice`` (投屏记录邀请/上传请求)
        would silently break cross-viewer coordination, so both survive a clear.
        """
        terminal: list[Any] = []
        queued_bytes = 0
        while True:
            try:
                queued = self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if isinstance(queued, (bytes, bytearray, memoryview)):
                queued_bytes += len(queued)
            elif queued is None or isinstance(queued, (StreamTermination, StreamNotice)):
                terminal.append(queued)
        self.queue_bytes = max(0, self.queue_bytes - queued_bytes)
        for item in terminal:
            try:
                self.queue.put_nowait(item)
            except asyncio.QueueFull:
                break

    def push_notice(self, payload: dict[str, Any]) -> bool:
        """Queue a JSON notice for this viewer; returns False when it cannot fit."""
        if self.terminated:
            return False
        notice = StreamNotice(payload)
        try:
            self.queue.put_nowait(notice)
            return True
        except asyncio.QueueFull:
            self.clear_queue()
            try:
                self.queue.put_nowait(notice)
                return True
            except asyncio.QueueFull:
                return False

    async def get_frame(self) -> bytes | StreamReset | StreamTermination | StreamNotice | None:
        frame = await self.queue.get()
        if isinstance(frame, (bytes, bytearray, memoryview)):
            self.queue_bytes = max(0, self.queue_bytes - len(frame))
            sequence = _raw_v2_sequence(frame)
            if sequence is not None:
                self.last_raw_sequence_delivered = sequence
        return frame

    def terminate(self, code: int = 4410, reason: str = "stream stopped") -> None:
        """Close this viewer: no further frames are accepted, termination is queued."""
        if self.terminated:
            return
        self.terminated = True
        self.clear_queue()
        try:
            self.queue.put_nowait(StreamTermination(code=code, reason=reason))
        except asyncio.QueueFull:
            pass

    def soft_limit(self) -> int:
        maxsize = self.queue.maxsize or VIDEO_QUEUE_MAXSIZE
        if VIDEO_QUEUE_SOFT_LIMIT <= 0:
            return maxsize
        if VIDEO_QUEUE_SOFT_LIMIT >= maxsize:
            return max(1, int(maxsize * 0.75))
        return max(1, VIDEO_QUEUE_SOFT_LIMIT)

    def _push_payload(self, payload: bytes, *, keyframe: bool = False) -> bool:
        if self.terminated:
            return False
        if self.needs_keyframe and not keyframe:
            return False
        if len(payload) > self.queue_max_bytes:
            self.drops += 1
            self.needs_keyframe = True
            return True
        if self.queue.qsize() >= self.soft_limit() or self.queue_bytes + len(payload) > self.queue_max_bytes:
            self.clear_queue()
            self.drops += 1
            self.needs_keyframe = True
            if not keyframe:
                return True
        try:
            self.queue.put_nowait(payload)
            self.queue_bytes += len(payload)
            if keyframe:
                self.needs_keyframe = False
        except asyncio.QueueFull:
            self.clear_queue()
            self.drops += 1
            self.needs_keyframe = True
            return True
        return False

    def push_frame(self, frame: bytes, keyframe: bool = False, config: bytes = b"") -> bool:
        payload = bytes(config) + bytes(frame) if keyframe and config else bytes(frame)
        return self._push_payload(payload, keyframe=keyframe)

    def push_access_unit(self, packet: bytes, *, keyframe: bool = False) -> bool:
        return self._push_payload(bytes(packet), keyframe=keyframe)

    def recover_with_raw_keyframe(self, packet: bytes) -> bool:
        """Replace a dropped client's queue with a newer decoder-ready packet."""
        if self.terminated or not self.needs_keyframe:
            return False
        sequence = _raw_v2_sequence(packet)
        if sequence is None or not _raw_sequence_is_newer(sequence, self.last_raw_sequence_delivered):
            return False
        self.clear_queue()
        self.needs_keyframe = True
        return not self.push_access_unit(packet, keyframe=True)

    def recover_with_legacy_keyframe(self, nal: bytes, config: bytes) -> bool:
        """Replace a dropped legacy client's queue with the latest keyframe."""
        if self.terminated or not self.needs_keyframe:
            return False
        self.clear_queue()
        self.needs_keyframe = True
        return not self.push_frame(nal, keyframe=True, config=config)

    def push_stream_reset(self, generation: int, video: dict[str, Any] | None = None) -> None:
        if self.terminated:
            return
        self.clear_queue()
        self.needs_keyframe = True
        try:
            self.queue.put_nowait(StreamReset(generation, video))
        except asyncio.QueueFull:
            self.clear_queue()
            self.queue.put_nowait(StreamReset(generation, video))

    def queue_snapshot(self) -> dict[str, int]:
        return {"items": self.queue.qsize(), "bytes": max(0, int(self.queue_bytes)), "drops": self.drops}


@dataclass
class EventClient:
    username: str
    websocket: WebSocket
    ready: bool = False
    pending: list[str] = field(default_factory=list)
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

class MirrorSession:
    def __init__(self, device_id: str, address: str, loop: asyncio.AbstractEventLoop):
        self.device_id = device_id
        self.address = address
        self.loop = loop
        self.scrcpy: Scrcpy | None = None
        self.clients: dict[str, ClientSession] = {}
        self.running = False
        self.last_error = ""
        self.available = True
        self.video_parser = H264AnnexBParser()
        self.video_assembler: H264AccessUnitAssembler | None = None
        self.protocol_demuxer: ScrcpyProtocolDemuxer | None = None
        self.sps = b""
        self.pps = b""
        self.config_packet = b""
        self._pending_sps = b""
        self._pending_pps = b""
        self._raw_config_signature = b""
        self._raw_config_generation = 0
        self._raw_frame_sequence = 0
        self._last_published_frame_at: float | None = None
        self._last_published_frame_gap_ms: float | None = None
        self._max_published_frame_gap_ms = 0.0
        self._published_frame_count = 0
        self._video_width = 0
        self._video_height = 0
        # 起流时设备所处的显示方向（0..3；读不到就是 None）。跟随设备转屏的基线。
        self.capture_display_rotation: int | None = None
        # Keep the newest decoder-ready keyframe so a viewer that joins after
        # startup does not have to wait for the next encoder IDR.  The stream
        # can produce its first keyframe before the HTTP start request returns.
        self._latest_raw_keyframe: bytes | None = None
        self._latest_legacy_keyframe: tuple[bytes, bytes] | None = None
        self.video_options = settings_to_video_options(storage.get_settings())
        self.stream_mode = _stream_mode()
        self.effective_stream_mode = "none"
        self.stream_health = "idle"
        self.last_keyframe_at: int | None = None
        self.stream_started_at: int | None = None
        self.last_client_left_at: float | None = None
        # 本次拉流成功时刻（monotonic）：供连续投屏时间上限强制停止使用。
        self.started_at: float | None = None
        self.last_adb_status: dict[str, Any] | None = None
        self.startup_timings: dict[str, float] = {}
        self._lock = asyncio.Lock()
        self._clients_lock = threading.RLock()
        self._viewer_reservations: dict[str, tuple[str, float | None]] = {}
        self._restart_in_progress = False
        self._control_send_lock = threading.Lock()
        self._video_reset_lock = asyncio.Lock()
        self._video_reset_task: asyncio.Task | None = None
        self._transport_cleanup_task: asyncio.Task | None = None
        self._last_video_reset_at = 0.0
        self._video_generation_lock = threading.Lock()
        self._video_generation = 0
        self._protocol_invalid_logged = False
        self._protocol_payload_logged = False

    def _current_video_generation(self) -> int:
        with self._video_generation_lock:
            return self._video_generation

    def _advance_video_generation(self) -> int:
        with self._video_generation_lock:
            self._video_generation += 1
            return self._video_generation

    def _generation_is_current(self, generation: int) -> bool:
        with self._video_generation_lock:
            return generation == self._video_generation

    def _publish_scrcpy_if_current(self, scpy: Scrcpy, generation: int) -> bool:
        """Publish a worker-created transport atomically with generation invalidation."""
        with self._video_generation_lock:
            if generation != self._video_generation:
                return False
            self.scrcpy = scpy
            return True

    def _record_scrcpy_timing(self, stage: str, elapsed_ms: float) -> None:
        """Emit redacted startup timings for diagnostics without changing state."""
        normalized_stage = str(stage or "").strip().lower()
        if not normalized_stage:
            return
        value = max(0.0, float(elapsed_ms))
        self.startup_timings[normalized_stage] = round(value, 1)
        log.info(
            "MIRROR_TIMING device=%s stage=%s elapsed_ms=%.1f",
            self.device_id,
            normalized_stage,
            value,
        )

    def _record_scrcpy_timing_for_generation(
        self,
        generation: int,
        stage: str,
        elapsed_ms: float,
    ) -> None:
        if not self._generation_is_current(generation):
            return
        self._record_scrcpy_timing(stage, elapsed_ms)

    def snapshot(self) -> dict[str, Any]:
        lock = storage.get_lock(self.device_id)
        with self._clients_lock:
            self._purge_viewer_reservations_locked()
            clients = list(self.clients.values())
            pending_viewers = len(self._viewer_reservations)
        return {
            "device_id": self.device_id,
            "running": self.running,
            "clients": len(clients),
            "viewer_count": len(clients),
            "pending_viewers": pending_viewers,
            "client_drops": sum(client.drops for client in clients),
            "queue_soft_limit": VIDEO_QUEUE_SOFT_LIMIT,
            "queue_max_bytes": VIDEO_QUEUE_MAX_BYTES,
            "client_queue_bytes": sum(client.queue_bytes for client in clients),
            "client_queues": {client.id: client.queue_snapshot() for client in clients},
            "last_error": self.last_error,
            "stream_mode": self.effective_stream_mode,
            "stream_health": self.stream_health,
            "config_generation": self._raw_config_generation,
            "frame_sequence": self._raw_frame_sequence,
            "published_frame_count": self._published_frame_count,
            "last_published_frame_gap_ms": self._last_published_frame_gap_ms,
            "max_published_frame_gap_ms": round(self._max_published_frame_gap_ms, 1),
            "raw_parser": self.video_assembler.stats if self.video_assembler is not None else None,
            "last_keyframe_at": self.last_keyframe_at,
            "stream_started_at": self.stream_started_at,
            "startup_timings": dict(self.startup_timings),
            "continuous_duration_seconds": (
                max(0, int(time.monotonic() - self.started_at))
                if self.running and self.started_at is not None
                else 0
            ),
            "idle_since": int(self.last_client_left_at) if self.last_client_left_at else None,
            "adb": self.last_adb_status,
            "capture_display_rotation": self.capture_display_rotation,
            "control_lock": lock,
            "video": public_video_options(self.video_options),
        }

    def client_snapshots(self) -> list[dict[str, Any]]:
        lock = storage.get_lock(self.device_id)
        owner = str(lock.get("username") or "") if lock else ""
        owner_client = str(lock.get("client_id") or "") if lock else ""
        with self._clients_lock:
            clients = list(self.clients.values())
        return [
            {
                "client_id": client.id,
                "username": client.username,
                "connected_at": client.connected_at,
                "drops": client.drops,
                "queue_size": client.queue.qsize(),
                "queue_bytes": client.queue_bytes,
                "needs_keyframe": client.needs_keyframe,
                # Prefer the client-specific lock identity; the username
                # fallback keeps snapshots compatible with legacy rows that
                # predate client_id persistence.
                "has_control": bool(
                    (owner_client and owner_client == client.id)
                    or (not owner_client and owner and owner == client.username)
                ),
            }
            for client in clients
        ]

    def _purge_viewer_reservations_locked(self, now: float | None = None) -> None:
        current = time.monotonic() if now is None else now
        expired = [
            token
            for token, (_username, expires_at) in self._viewer_reservations.items()
            if expires_at is not None and expires_at <= current
        ]
        for token in expired:
            self._viewer_reservations.pop(token, None)
        if expired and self.running and not self.clients and not self._viewer_reservations:
            self.last_client_left_at = current

    def reserve_viewer(self, username: str, ttl_seconds: float | None = None) -> str:
        with self._clients_lock:
            if self._restart_in_progress:
                return ""
            token = uuid.uuid4().hex
            expires_at = None if ttl_seconds is None else time.monotonic() + max(0.1, float(ttl_seconds))
            self._purge_viewer_reservations_locked()
            self._viewer_reservations[token] = (str(username or ""), expires_at)
            self.last_client_left_at = None
        return token

    def refresh_viewer_reservation(
        self,
        token: str,
        username: str,
        ttl_seconds: float = VIEWER_RESERVATION_TTL,
    ) -> bool:
        normalized = str(token or "").strip()
        owner = str(username or "")
        if not normalized:
            return False
        with self._clients_lock:
            self._purge_viewer_reservations_locked()
            reservation = self._viewer_reservations.get(normalized)
            if reservation is None or reservation[0] != owner:
                return False
            self._viewer_reservations[normalized] = (
                owner,
                time.monotonic() + max(0.1, float(ttl_seconds)),
            )
            return True

    def release_viewer_reservation(self, token: str, username: str = "") -> bool:
        normalized = str(token or "").strip()
        if not normalized:
            return False
        with self._clients_lock:
            self._purge_viewer_reservations_locked()
            reservation = self._viewer_reservations.get(normalized)
            if reservation is None or (username and reservation[0] != username):
                return False
            self._viewer_reservations.pop(normalized, None)
            if self.running and not self.clients and not self._viewer_reservations:
                self.last_client_left_at = time.monotonic()
            return True

    def _consume_viewer_reservation_locked(self, token: str, username: str) -> bool:
        normalized = str(token or "").strip()
        owner = str(username or "")
        self._purge_viewer_reservations_locked()
        if normalized:
            reservation = self._viewer_reservations.get(normalized)
            if reservation is None or reservation[0] != owner:
                return False
            self._viewer_reservations.pop(normalized, None)
            return True
        # A tokenless reconnect may belong to another tab using the same
        # account, so it must not consume an explicit start reservation.
        return False

    def consume_viewer_reservation(self, token: str, username: str) -> bool:
        with self._clients_lock:
            return self._consume_viewer_reservation_locked(token, username)

    def _single_client_reset_allowed_locked(self) -> bool:
        self._purge_viewer_reservations_locked()
        return len(self.clients) == 1 and not self._viewer_reservations

    def single_client_reset_allowed(self) -> bool:
        with self._clients_lock:
            return self._single_client_reset_allowed_locked()

    def has_clients_or_reservations(self) -> bool:
        with self._clients_lock:
            self._purge_viewer_reservations_locked()
            return bool(self.clients or self._viewer_reservations)

    async def start(
        self,
        options: dict[str, Any] | None = None,
        force_restart: bool = False,
        reservation_token: str | None = None,
    ) -> bool:
        if not options:
            stored = await asyncio.to_thread(storage.get_settings)
            options = settings_to_video_options(stored)
        target_options = public_video_options(options)
        async with self._lock:
            return await self._start_locked(target_options, force_restart, reservation_token=reservation_token)

    async def apply_video_options(self, options: dict[str, Any], client_id: str, username: str) -> dict[str, Any]:
        """Apply a live quality change only when it cannot disrupt another viewer."""
        target_options = public_video_options(options)
        normalized_client_id = str(client_id or "").strip()
        normalized_username = str(username or "")
        async with self._lock:
            running = self.running
            current_options = public_video_options(self.video_options)
            restart_required = running and signature(current_options) != signature(target_options)
            with self._clients_lock:
                self._purge_viewer_reservations_locked()
                client_count = len(self.clients)
                pending_count = len(self._viewer_reservations)
                viewer_count = client_count + pending_count
                requester = self.clients.get(normalized_client_id) if normalized_client_id else None
                requester_is_only_viewer = (
                    client_count == 1
                    and pending_count == 0
                    and requester is not None
                    and requester.username == normalized_username
                )
                safe_to_restart = viewer_count == 0 or requester_is_only_viewer
                deferred = bool(restart_required and not safe_to_restart)
                if restart_required and safe_to_restart:
                    # Reservation and WebSocket registration check this flag,
                    # closing the snapshot-to-restart race without holding a
                    # synchronous lock across ADB and scrcpy awaits.
                    self._restart_in_progress = True
            if deferred:
                log.info(
                    "MIRROR_QUALITY_DEFERRED device=%s user=%s clients=%s pending=%s",
                    self.device_id,
                    normalized_username,
                    client_count,
                    pending_count,
                )
                return {
                    "ok": True,
                    "running": running,
                    "restart_required": restart_required,
                    "restarted": False,
                    "deferred": True,
                    "viewer_count": viewer_count,
                    "effective": current_options,
                }
            if not running:
                return {
                    "ok": True,
                    "running": False,
                    "restart_required": False,
                    "restarted": False,
                    "deferred": False,
                    "viewer_count": viewer_count,
                    "effective": target_options,
                }
            try:
                ok = await self._start_locked(target_options, restart_required)
            finally:
                if restart_required:
                    with self._clients_lock:
                        self._restart_in_progress = False
            return {
                "ok": ok,
                "running": self.running,
                "restart_required": restart_required,
                "restarted": bool(ok and restart_required),
                "deferred": False,
                "viewer_count": viewer_count,
                "effective": target_options if ok else current_options,
            }

    async def _start_locked(
        self,
        target_options: dict[str, Any],
        force_restart: bool,
        reservation_token: str | None = None,
    ) -> bool:
        if not self.available:
            self.running = False
            self.last_error = "device unavailable"
            return False
        if self.running:
            if signature(self.video_options) == signature(target_options) and not force_restart:
                self.video_options = target_options
                log.info("MIRROR_ALREADY_RUNNING device=%s mode=%s", self.device_id, self.effective_stream_mode)
                return True
            if not force_restart:
                with self._clients_lock:
                    self._purge_viewer_reservations_locked()
                    # 观看端可能还没注册 WebSocket（并发的第二次 mirror/start），
                    # 预约令牌仍能证明「已经有人在看」，此时必须保持共享流。
                    other_viewers = any(
                        token != reservation_token for token in self._viewer_reservations
                    )
                    idle = not self.clients and not other_viewers
                if not idle:
                    log.info(
                        "MIRROR_JOIN_RUNNING device=%s clients=%s pending=%s mode=%s",
                        self.device_id,
                        len(self.clients),
                        len(self._viewer_reservations),
                        self.effective_stream_mode,
                    )
                    return True
                # 确实没有观看端时让本次请求的画质生效：宫格用 1fps 缩略档位起流，
                # 之后打开单画面必须能恢复用户自己的画质，而不是继承缩略档位。
                log.info("MIRROR_RESTART_FOR_OPTIONS device=%s mode=%s", self.device_id, self.effective_stream_mode)
                force_restart = True
            self._advance_video_generation()
            scpy = self.scrcpy
            self.scrcpy = None
            self.running = False
            if self._video_reset_task and not self._video_reset_task.done():
                self._video_reset_task.cancel()
            self._video_reset_task = None
            if scpy:
                await self._stop_scrcpy_bounded(scpy, "restart")
        await self._await_transport_cleanup_bounded("start")
        self.last_client_left_at = None
        adb_status = await adb_monitor.ensure_connected({"id": self.device_id, "address": self.address, "enabled": True})
        self.last_adb_status = adb_status
        if not adb_status.get("ok"):
            self.running = False
            self.stream_health = "adb_failed"
            self.last_error = adb_status.get("detail") or f"ADB {adb_status.get('state', 'unknown')}"
            log.warning("MIRROR_ADB_FAILED device=%s state=%s detail=%s", self.device_id, adb_status.get("state"), self.last_error)
            return False
        self.video_options = target_options
        bit_rate = int(target_options["video_bit_rate"])
        max_size = int(target_options["max_size"])
        max_fps = int(target_options["max_fps"])
        mode = str(target_options.get("scrcpy_stream_mode") or _stream_mode()).strip().lower()
        if mode not in ("raw", "protocol", "legacy"):
            mode = "raw"
        enabled_modes = await asyncio.to_thread(storage.get_setting, "scrcpy_enabled_stream_modes", "raw")
        mode = stream_mode_or_default(mode, enabled_stream_modes_value(enabled_modes))
        modes = [mode]
        last_error = ""
        for mode in modes:
            log.info(
                "MIRROR_START device=%s address=%s mode=%s bitrate=%s max_size=%s max_fps=%s",
                self.device_id,
                self.address,
                mode,
                bit_rate,
                max_size,
                max_fps,
            )
            self._reset_video_state(mode)
            generation = self._current_video_generation()
            start_task = asyncio.create_task(
                asyncio.to_thread(self._start_scrcpy, bit_rate, max_size, max_fps, mode, generation)
            )
            try:
                ok = await asyncio.shield(start_task)
                if not self._generation_is_current(generation):
                    ok = False
                self.running = ok
                healthy = (
                    await self._wait_for_stream_health(mode, timeout=STREAM_START_RESPONSE_TIMEOUT)
                    if ok
                    else False
                )
            except asyncio.CancelledError:
                await self._cleanup_cancelled_start(start_task)
                raise
            if ok:
                if healthy or mode == "legacy":
                    self.last_error = ""
                    self.started_at = time.monotonic()
                    self.stream_started_at = int(time.time())
                    # Only the opt-in encoder workaround needs an ADB baseline.
                    self.capture_display_rotation = None
                    if str(os.environ.get("ADB_ROTATION_RESTART_FALLBACK", "false")).strip().lower() in ("1", "true", "yes", "on"):
                        self.capture_display_rotation = await asyncio.to_thread(
                            read_capture_display_rotation, self.address
                        )
                    log.info(
                        "MIRROR_HEALTHY device=%s mode=%s last_keyframe_at=%s display_rotation=%s",
                        self.device_id,
                        mode,
                        self.last_keyframe_at,
                        self.capture_display_rotation,
                    )
                    self._reprime_clients_after_restart(force_restart)
                    return True
                # The transport is usable even when the first keyframe has
                # not arrived during the short HTTP warm-up window.  Keep the
                # stream alive and let the video WebSocket/Raw v2 watchdog
                # finish startup; returning here prevents the UI from waiting
                # on an HTTP request that cannot make progress by itself.
                if self.running and self.scrcpy and self.stream_health not in {"failed", "adb_failed"}:
                    self.stream_health = "warming"
                    self.last_error = ""
                    self.started_at = time.monotonic()
                    self.stream_started_at = int(time.time())
                    log.warning(
                        "MIRROR_WARMING device=%s mode=%s health=%s timeout=%.2fs",
                        self.device_id,
                        mode,
                        self.stream_health,
                        STREAM_START_RESPONSE_TIMEOUT,
                    )
                    self._reprime_clients_after_restart(force_restart)
                    return True
                last_error = f"{mode} stream did not become healthy"
                log.warning("MIRROR_STREAM_UNHEALTHY device=%s mode=%s health=%s last_error=%s", self.device_id, mode, self.stream_health, self.last_error)
                await self._stop_scrcpy_only()
            else:
                last_error = self.last_error or f"failed to start scrcpy in {mode} mode"
                log.warning("MIRROR_START_FAILED device=%s mode=%s error=%s", self.device_id, mode, last_error)
        self.running = False
        self.stream_health = "failed"
        self.started_at = None
        self.stream_started_at = None
        self.last_error = last_error or "failed to start scrcpy"
        log.error("MIRROR_FAILED device=%s error=%s", self.device_id, self.last_error)
        return False

    async def _await_transport_cleanup_bounded(self, operation: str) -> bool:
        """Wait for transport cleanup without holding the lifecycle lock forever."""
        cleanup_task = self._transport_cleanup_task
        if cleanup_task is None or cleanup_task.done() or cleanup_task is asyncio.current_task():
            return True
        deadline = time.monotonic() + MIRROR_CANCEL_CLEANUP_TIMEOUT_SECONDS
        while not cleanup_task.done():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                log.warning(
                    "MIRROR_TRANSPORT_CLEANUP_TIMEOUT device=%s operation=%s",
                    self.device_id,
                    operation,
                )
                self._detach_background_cleanup(cleanup_task, f"transport_{operation}")
                return False
            try:
                await asyncio.wait_for(asyncio.shield(cleanup_task), timeout=remaining)
            except asyncio.TimeoutError:
                log.warning(
                    "MIRROR_TRANSPORT_CLEANUP_TIMEOUT device=%s operation=%s",
                    self.device_id,
                    operation,
                )
                self._detach_background_cleanup(cleanup_task, f"transport_{operation}")
                return False
            except asyncio.CancelledError:
                self._detach_background_cleanup(cleanup_task, f"transport_{operation}")
                raise
        try:
            cleanup_task.result()
        except asyncio.CancelledError:
            return False
        except Exception:
            log.exception(
                "MIRROR_TRANSPORT_CLEANUP_FAILED device=%s operation=%s",
                self.device_id,
                operation,
            )
            return False
        return True

    async def _cleanup_cancelled_start(self, start_task: asyncio.Task) -> None:
        # Cancelling asyncio.to_thread() does not stop its worker. Invalidate
        # the generation before waiting so late callbacks cannot publish into
        # a new stream, then wait only within a bounded cleanup budget.
        deadline = time.monotonic() + MIRROR_CANCEL_CLEANUP_TIMEOUT_SECONDS
        self._advance_video_generation()
        if self._video_reset_task and not self._video_reset_task.done():
            self._video_reset_task.cancel()
        self._video_reset_task = None
        scpy = self.scrcpy
        self.scrcpy = None
        self.running = False
        self.stream_health = "stopped"
        self.started_at = None
        self.stream_started_at = None
        self.last_error = "mirror start cancelled"

        while not start_task.done():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                log.warning("MIRROR_CANCELLED_START_TIMEOUT device=%s", self.device_id)
                break
            try:
                await asyncio.wait_for(asyncio.shield(start_task), timeout=remaining)
            except asyncio.CancelledError:
                continue
            except asyncio.TimeoutError:
                log.warning("MIRROR_CANCELLED_START_TIMEOUT device=%s", self.device_id)
                break
            except Exception:
                break
        if start_task.done():
            try:
                start_task.result()
            except asyncio.CancelledError:
                pass
            except Exception:
                log.exception("MIRROR_CANCELLED_START_WORKER_FAILED device=%s", self.device_id)
        else:
            self._detach_background_cleanup(start_task, "start")

        # A test double or an older worker may have published a transport just
        # before returning. Detach it as well; the real worker is generation
        # guarded below and will stop its own stale transport.
        late_scpy = self.scrcpy
        self.scrcpy = None
        if late_scpy is not None:
            scpy = late_scpy
        if scpy is None:
            return

        stop_task = asyncio.create_task(asyncio.to_thread(scpy.scrcpy_stop))
        while not stop_task.done():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                log.warning("MIRROR_CANCELLED_START_CLEANUP_TIMEOUT device=%s", self.device_id)
                self._detach_background_cleanup(stop_task, "stop")
                return
            try:
                await asyncio.wait_for(asyncio.shield(stop_task), timeout=remaining)
            except asyncio.CancelledError:
                continue
            except asyncio.TimeoutError:
                log.warning("MIRROR_CANCELLED_START_CLEANUP_TIMEOUT device=%s", self.device_id)
                self._detach_background_cleanup(stop_task, "stop")
                return
            except Exception:
                break
        try:
            stop_task.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("MIRROR_CANCELLED_START_CLEANUP_FAILED device=%s", self.device_id)

    def _detach_background_cleanup(self, task: asyncio.Task, operation: str) -> None:
        def finish(completed: asyncio.Task) -> None:
            try:
                completed.result()
            except asyncio.CancelledError:
                pass
            except Exception:
                log.exception(
                    "MIRROR_BACKGROUND_CLEANUP_FAILED device=%s operation=%s",
                    self.device_id,
                    operation,
                )

        task.add_done_callback(finish)

    async def _stop_scrcpy_bounded(self, scpy: Scrcpy | None, operation: str) -> bool:
        """Stop a scrcpy transport without allowing a blocking worker to stall lifecycle work."""
        if scpy is None:
            return True
        stop_task = asyncio.create_task(asyncio.to_thread(scpy.scrcpy_stop))
        deadline = time.monotonic() + MIRROR_CANCEL_CLEANUP_TIMEOUT_SECONDS
        while not stop_task.done():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                log.warning(
                    "MIRROR_SCRCPY_STOP_TIMEOUT device=%s operation=%s",
                    self.device_id,
                    operation,
                )
                self._detach_background_cleanup(stop_task, operation)
                return False
            try:
                await asyncio.wait_for(asyncio.shield(stop_task), timeout=remaining)
            except asyncio.TimeoutError:
                log.warning(
                    "MIRROR_SCRCPY_STOP_TIMEOUT device=%s operation=%s",
                    self.device_id,
                    operation,
                )
                self._detach_background_cleanup(stop_task, operation)
                return False
            except asyncio.CancelledError:
                self._detach_background_cleanup(stop_task, operation)
                raise
            except Exception:
                log.exception(
                    "MIRROR_SCRCPY_STOP_FAILED device=%s operation=%s",
                    self.device_id,
                    operation,
                )
                raise
        try:
            stop_task.result()
        except asyncio.CancelledError:
            return False
        except Exception:
            log.exception(
                "MIRROR_SCRCPY_STOP_FAILED device=%s operation=%s",
                self.device_id,
                operation,
            )
            raise
        return True

    def _start_scrcpy(
        self,
        bit_rate: int,
        max_size: int,
        max_fps: int,
        mode: str,
        expected_generation: int | None = None,
    ) -> bool:
        scpy = Scrcpy()
        scpy.device_id = self.device_id
        scpy.device_address = self.address
        generation = self._current_video_generation() if expected_generation is None else expected_generation
        ok = scpy.scrcpy_start(
            lambda data: self.publish_video(data, generation),
            bit_rate,
            max_size,
            max_fps,
            stream_mode=mode,
            stream_exit_callback=lambda reason: self._on_stream_transport_closed(generation, scpy, reason),
            timing_callback=lambda stage, elapsed: self._record_scrcpy_timing_for_generation(
                generation,
                stage,
                elapsed,
            ),
        )
        if not self._generation_is_current(generation):
            if ok:
                try:
                    scpy.scrcpy_stop()
                except Exception:
                    log.exception("MIRROR_STALE_START_STOP_FAILED device=%s", self.device_id)
            return False
        if ok and scpy.unexpected_exit_reason:
            if self._generation_is_current(generation):
                self.last_error = scpy.unexpected_exit_reason
            scpy.scrcpy_stop()
            return False
        if ok and self._publish_scrcpy_if_current(scpy, generation):
            return True
        if ok:
            # The start worker finished after cancellation/restart.  It owns
            # this stale transport and stops it without publishing state.
            try:
                scpy.scrcpy_stop()
            except Exception:
                log.exception("MIRROR_STALE_START_STOP_FAILED device=%s", self.device_id)
            return False
        else:
            self.last_error = scpy.last_error or f"failed to start scrcpy in {mode} mode"
        return ok

    def _on_stream_transport_closed(self, generation: int, scrcpy: Scrcpy, reason: str) -> None:
        self.loop.call_soon_threadsafe(self._handle_stream_transport_closed, generation, scrcpy, reason)

    def _handle_stream_transport_closed(self, generation: int, scrcpy: Scrcpy, reason: str) -> None:
        if not self._generation_is_current(generation) or self.scrcpy is not scrcpy:
            return
        self.running = False
        self.scrcpy = None
        self.stream_health = "failed"
        self.started_at = None
        self.stream_started_at = None
        self.last_error = reason or "video stream ended"
        failed_generation = self._advance_video_generation()
        if self._video_reset_task and not self._video_reset_task.done():
            self._video_reset_task.cancel()
        self._video_reset_task = None
        with self._clients_lock:
            clients = list(self.clients.values())
        for client in clients:
            client.clear_queue()
            client.needs_keyframe = True
            client.queue.put_nowait(None)
        log.warning("MIRROR_TRANSPORT_CLOSED device=%s error=%s", self.device_id, self.last_error)
        task = self.loop.create_task(self._finalize_stream_transport_failure(scrcpy, failed_generation))
        self._transport_cleanup_task = task

        def clear_cleanup(completed: asyncio.Task) -> None:
            if self._transport_cleanup_task is completed:
                self._transport_cleanup_task = None

        task.add_done_callback(clear_cleanup)

    async def _finalize_stream_transport_failure(self, scrcpy: Scrcpy, failed_generation: int) -> None:
        await self._stop_scrcpy_bounded(scrcpy, "transport_failure")
        if not self._generation_is_current(failed_generation) or self.running:
            return
        await _mirror_manager().broadcast(
            {
                "type": "mirror_status",
                "device_id": self.device_id,
                "running": False,
                "session": self.snapshot(),
                "reason": "transport_closed",
            }
        )

    def _reset_video_state(self, mode: str) -> None:
        self.video_parser = H264AnnexBParser()
        self.video_assembler = H264AccessUnitAssembler() if mode == "raw" else None
        self.protocol_demuxer = ScrcpyProtocolDemuxer() if mode == "protocol" else None
        self.sps = b""
        self.pps = b""
        self.config_packet = b""
        self._pending_sps = b""
        self._pending_pps = b""
        self._raw_config_signature = b""
        self._raw_config_generation = 0
        self._raw_frame_sequence = 0
        self._last_published_frame_at = None
        self._last_published_frame_gap_ms = None
        self._max_published_frame_gap_ms = 0.0
        self._published_frame_count = 0
        self.startup_timings = {}
        self._video_width = 0
        self._video_height = 0
        self._latest_raw_keyframe = None
        self._latest_legacy_keyframe = None
        generation = self._advance_video_generation()
        self._last_video_reset_at = 0.0
        if self._video_reset_task and not self._video_reset_task.done():
            self._video_reset_task.cancel()
        self._video_reset_task = None
        self.stream_health = "starting"
        self.effective_stream_mode = mode
        self.last_keyframe_at = None
        self.started_at = None
        self.stream_started_at = None
        self._protocol_invalid_logged = False
        self._protocol_payload_logged = False
        for client in self.clients.values():
            client.push_stream_reset(generation, public_video_options(self.video_options))

    async def _wait_for_stream_health(self, mode: str, timeout: float | None = None) -> bool:
        deadline = time.monotonic() + (STREAM_HEALTH_TIMEOUT if timeout is None else max(0.1, float(timeout)))
        while time.monotonic() < deadline:
            if self.stream_health == "healthy" and self.last_keyframe_at and self.codec_config():
                return True
            if not self.running:
                return False
            # Polling at 50 ms keeps the short start window responsive without
            # creating a busy loop; the actual first frame remains asynchronous
            # and is delivered from the cached keyframe path when available.
            await asyncio.sleep(0.05)
        return False

    async def _stop_scrcpy_only(self) -> None:
        scpy = self.scrcpy
        self.scrcpy = None
        self.running = False
        if scpy:
            await self._stop_scrcpy_bounded(scpy, "start_failure")

    async def _stop_locked(self) -> bool:
        await self._await_transport_cleanup_bounded("stop")
        log.info("MIRROR_STOP device=%s mode=%s", self.device_id, self.effective_stream_mode)
        self._advance_video_generation()
        scpy = self.scrcpy
        self.scrcpy = None
        self.running = False
        with self._clients_lock:
            self._viewer_reservations.clear()
        if self._video_reset_task and not self._video_reset_task.done():
            self._video_reset_task.cancel()
        self._video_reset_task = None
        if scpy:
            await self._stop_scrcpy_bounded(scpy, "stop")
        self.stream_health = "stopped"
        self.started_at = None
        self.stream_started_at = None
        self.last_client_left_at = None
        release_control_lock(self.device_id, "", force=True)
        with self._clients_lock:
            for client in self.clients.values():
                client.terminate(4410, "stream stopped by administrator")
                client.needs_keyframe = True
        return True

    async def stop(self) -> bool:
        async with self._lock:
            self._cancel_record_session("stream_stopped")
            return await self._stop_locked()

    async def stop_if_no_clients(self) -> bool:
        async with self._lock:
            if not self.running or self.has_clients_or_reservations():
                return False
            self._cancel_record_session("stream_stopped")
            return await self._stop_locked()

    def _cancel_record_session(self, reason: str) -> None:
        """设备停流时终止该设备上的「多端投屏记录」并通知参与者（内存态，不落盘）。"""
        try:
            from .mirror_record import record_registry

            record_registry.cancel_for_device(self.device_id, reason)
        except Exception:  # noqa: BLE001 - 清理失败不能掩盖停流本身
            log.exception("MIRROR_RECORD_CANCEL_FAILED device=%s reason=%s", self.device_id, reason)

    async def restart(self) -> bool:
        await self.stop()
        return await self.start(self.video_options, force_restart=True)

    def codec_config(self) -> bytes:
        if self.config_packet:
            return self.config_packet
        return self.sps + self.pps if self.sps and self.pps else b""

    def publish_video(self, data: bytes, generation: int | None = None) -> None:
        if generation is not None and not self._generation_is_current(generation):
            return
        if self.protocol_demuxer is not None:
            for packet in self.protocol_demuxer.feed(data):
                self._process_packet(packet, generation)
            return
        if self.video_assembler is not None:
            for access_unit in self.video_assembler.feed(data):
                self._process_access_unit(access_unit, generation)
            return
        for nal in self.video_parser.feed(data):
            self._process_nal(nal, generation)

    def _process_packet(self, packet: ScrcpyPacket, generation: int | None = None) -> None:
        packet_width = int(getattr(packet, "width", 0) or 0)
        packet_height = int(getattr(packet, "height", 0) or 0)
        if 0 < packet_width <= 0xFFFF and 0 < packet_height <= 0xFFFF:
            self._video_width = packet_width
            self._video_height = packet_height
        payload = normalize_h264_payload(packet.payload)
        if packet.config:
            if not is_annexb(payload):
                self._log_invalid_protocol_payload("config", payload)
                return
            nal_types = annexb_nal_types(payload)
            if H264_NAL_SPS not in nal_types or H264_NAL_PPS not in nal_types:
                self.config_packet = b""
                self.stream_health = "config"
                self.last_error = "protocol codec config is missing SPS or PPS"
                log.warning("VIDEO_CONFIG_INCOMPLETE device=%s types=%s bytes=%s", self.device_id, nal_types, len(payload))
                return
            self.config_packet = payload
            self.stream_health = "config"
            log.info(
                "VIDEO_CONFIG_PACKET device=%s mode=%s bytes=%s normalized=%s types=%s head=%s",
                self.device_id,
                self.effective_stream_mode,
                len(payload),
                len(payload) != len(packet.payload) or payload[:16] != packet.payload[:16],
                annexb_nal_types(payload),
                payload[:16].hex(),
            )
            return
        if not is_annexb(payload):
            self._log_invalid_protocol_payload("frame", payload)
            return
        nal_types = annexb_nal_types(payload)
        has_idr = H264_NAL_IDR in nal_types
        config = self.codec_config() if has_idr else b""
        keyframe = has_idr and bool(config)
        if not self._protocol_payload_logged:
            self._protocol_payload_logged = True
            log.info(
                "VIDEO_PROTOCOL_PAYLOAD device=%s mode=%s bytes=%s normalized=%s keyflag=%s types=%s head=%s",
                self.device_id,
                self.effective_stream_mode,
                len(payload),
                len(payload) != len(packet.payload) or payload[:16] != packet.payload[:16],
                packet.keyframe,
                nal_types,
                payload[:16].hex(),
            )
        if packet.keyframe and nal_types and not has_idr:
            log.debug(
                "VIDEO_PROTOCOL_KEYFLAG_WITHOUT_IDR device=%s types=%s bytes=%s",
                self.device_id,
                nal_types,
                len(payload),
            )
        discontinuity = False
        payload_to_send = payload
        if keyframe:
            if config != self._raw_config_signature:
                self._raw_config_signature = config
                self._raw_config_generation = (self._raw_config_generation + 1) & 0xFFFFFFFF or 1
                discontinuity = True
            payload_to_send = config + payload
            self.last_keyframe_at = int(time.time())
            self.stream_health = "healthy"
        self.loop.call_soon_threadsafe(
            lambda: self.publish_access_unit(
                payload_to_send,
                keyframe=keyframe,
                contains_config=bool(config),
                discontinuity=discontinuity,
                generation=generation,
                width=self._video_width,
                height=self._video_height,
            )
        )

    def _process_access_unit(self, access_unit: H264AccessUnit, generation: int | None = None) -> None:
        payload = access_unit.payload
        contains_sps = False
        contains_pps = False
        for nal in annexb_nal_units(payload):
            ntype = nal_type(nal)
            if ntype == H264_NAL_SPS:
                self._pending_sps = nal
                contains_sps = True
            elif ntype == H264_NAL_PPS:
                self._pending_pps = nal
                contains_pps = True
        contains_config = access_unit.contains_config or contains_sps or contains_pps
        discontinuity = False
        if contains_config:
            self.stream_health = "waiting_keyframe" if access_unit.keyframe else "waiting_config"
        config = b""
        if access_unit.keyframe and self._pending_sps and self._pending_pps:
            config = self._pending_sps + self._pending_pps
            if config != self._raw_config_signature:
                self._raw_config_signature = config
                self._raw_config_generation = (self._raw_config_generation + 1) & 0xFFFFFFFF or 1
                discontinuity = True
            self.sps = self._pending_sps
            self.pps = self._pending_pps
            self.config_packet = b""
        elif access_unit.keyframe:
            config = self.codec_config()
        if access_unit.keyframe and config and not contains_config:
            payload = config + payload
            contains_config = True
        if access_unit.keyframe and config:
            self.last_keyframe_at = int(time.time())
            self.stream_health = "healthy"
        elif not self.codec_config():
            self.stream_health = "waiting_config"
        elif not self.last_keyframe_at:
            self.stream_health = "waiting_keyframe"
        self.loop.call_soon_threadsafe(
            lambda: self.publish_access_unit(
                payload,
                keyframe=access_unit.keyframe and bool(config),
                contains_config=contains_config,
                discontinuity=discontinuity,
                generation=generation,
                width=self._video_width,
                height=self._video_height,
            )
        )

    def _log_invalid_protocol_payload(self, kind: str, payload: bytes) -> None:
        self.stream_health = "invalid_h264"
        self.last_error = f"protocol {kind} payload is not Annex-B H264"
        if self._protocol_invalid_logged:
            return
        self._protocol_invalid_logged = True
        log.warning(
            "VIDEO_PROTOCOL_INVALID device=%s kind=%s bytes=%s head=%s",
            self.device_id,
            kind,
            len(payload),
            payload[:16].hex(),
        )

    def _process_nal(self, nal: bytes, generation: int | None = None) -> None:
        ntype = nal_type(nal)
        if ntype == H264_NAL_SPS:
            self.sps = nal
            self.stream_health = "config"
            log.info("VIDEO_SPS device=%s mode=%s bytes=%s", self.device_id, self.effective_stream_mode, len(nal))
        elif ntype == H264_NAL_PPS:
            self.pps = nal
            self.stream_health = "config"
            log.info("VIDEO_PPS device=%s mode=%s bytes=%s", self.device_id, self.effective_stream_mode, len(nal))
        first_idr = ntype == H264_NAL_IDR and is_first_vcl_nal(nal)
        config = self.codec_config() if first_idr else b""
        keyframe = first_idr and bool(config)
        if keyframe:
            self.last_keyframe_at = int(time.time())
            self.stream_health = "healthy"
        if first_idr:
            log.info("VIDEO_IDR device=%s mode=%s bytes=%s config_bytes=%s", self.device_id, self.effective_stream_mode, len(nal), len(config))
        self.loop.call_soon_threadsafe(self.publish_nal, nal, keyframe, config, generation)

    def publish_nal(self, nal: bytes, keyframe: bool, config: bytes, generation: int | None = None) -> None:
        if generation is not None and not self._generation_is_current(generation):
            return
        if keyframe and config:
            # Legacy Annex-B clients need both codec configuration and the IDR
            # NAL; retain them together for late viewers.
            self._latest_legacy_keyframe = (bytes(nal), bytes(config))
        reset_needed = False
        with self._clients_lock:
            clients = list(self.clients.values())
        for client in clients:
            before = client.drops
            dropped = client.push_frame(nal, keyframe=keyframe, config=config)
            recovered = False
            if dropped:
                recovered = self._recover_client_from_cached_keyframe(client)
            if client.drops != before:
                log.warning(
                    "VIDEO_CLIENT_DROP device=%s client=%s user=%s drops=%s recovered=%s",
                    self.device_id,
                    client.id,
                    client.username,
                    client.drops,
                    recovered,
                )
            reset_needed = reset_needed or dropped
        if reset_needed and self.single_client_reset_allowed():
            self.schedule_video_reset("queue_overflow")

    def _recover_client_from_cached_keyframe(self, client: ClientSession) -> bool:
        """Recover one viewer without resetting a stream shared by other viewers."""
        with self._clients_lock:
            cached_raw = self._latest_raw_keyframe
            cached_legacy = self._latest_legacy_keyframe
            stream_mode = self.effective_stream_mode
        if stream_mode == "legacy" and cached_legacy:
            return client.recover_with_legacy_keyframe(cached_legacy[0], cached_legacy[1])
        if stream_mode != "legacy" and cached_raw:
            return client.recover_with_raw_keyframe(cached_raw)
        return False

    def publish_access_unit(
        self,
        payload: bytes,
        *,
        keyframe: bool,
        contains_config: bool = False,
        discontinuity: bool = False,
        generation: int | None = None,
        width: int = 0,
        height: int = 0,
    ) -> None:
        if generation is not None and not self._generation_is_current(generation):
            return
        if not payload:
            return
        now = time.monotonic()
        if self._last_published_frame_at is not None:
            gap_ms = max(0.0, (now - self._last_published_frame_at) * 1000.0)
            self._last_published_frame_gap_ms = round(gap_ms, 1)
            self._max_published_frame_gap_ms = max(self._max_published_frame_gap_ms, gap_ms)
        self._last_published_frame_at = now
        self._published_frame_count += 1
        self._raw_frame_sequence = (self._raw_frame_sequence + 1) & 0xFFFFFFFF or 1
        packet_width = int(width or self._video_width)
        packet_height = int(height or self._video_height)
        packet_width = packet_width if 0 <= packet_width <= 0xFFFF else 0
        packet_height = packet_height if 0 <= packet_height <= 0xFFFF else 0
        try:
            packet = pack_raw_v2_packet(
                RawV2Packet(
                    frame_sequence=self._raw_frame_sequence,
                    config_generation=self._raw_config_generation,
                    arrival_timestamp_us=int(time.time() * 1_000_000),
                    width=packet_width,
                    height=packet_height,
                    payload=payload,
                    keyframe=keyframe,
                    contains_config=contains_config,
                    discontinuity=discontinuity,
                )
            )
        except ValueError as exc:
            self.stream_health = "invalid_h264"
            self.last_error = str(exc)
            log.warning("VIDEO_RAW_V2_PACKET_DROP device=%s error=%s bytes=%s", self.device_id, exc, len(payload))
            return
        if keyframe and contains_config:
            # Store the complete packet, including its Raw v2 header, so the
            # sequence and config generation remain consistent for a viewer
            # joining after this frame was produced.
            self._latest_raw_keyframe = packet
        reset_needed = False
        with self._clients_lock:
            clients = list(self.clients.values())
        for client in clients:
            before = client.drops
            dropped = client.push_access_unit(packet, keyframe=keyframe)
            recovered = False
            if dropped:
                recovered = self._recover_client_from_cached_keyframe(client)
            if client.drops != before:
                log.warning(
                    "VIDEO_CLIENT_DROP device=%s client=%s user=%s drops=%s queue_bytes=%s recovered=%s",
                    self.device_id,
                    client.id,
                    client.username,
                    client.drops,
                    client.queue_bytes,
                    recovered,
                )
            reset_needed = reset_needed or dropped
        if reset_needed and self.single_client_reset_allowed():
            self.schedule_video_reset("queue_overflow")

    def add_client(self, client: ClientSession) -> None:
        with self._clients_lock:
            self.clients[client.id] = client
            self.last_client_left_at = None
            client_count = len(self.clients)
        log.info("VIDEO_CLIENT_ADD device=%s client=%s user=%s clients=%s", self.device_id, client.id, client.username, client_count)
        self.prime_client(client, reason="client_join")

    def attach_client(self, client: ClientSession, reservation_token: str = "") -> bool:
        normalized = str(reservation_token or "").strip()
        with self._clients_lock:
            if not self.running or self._restart_in_progress:
                return False
            if normalized and not self._consume_viewer_reservation_locked(normalized, client.username):
                return False
            client.viewer_token = normalized
            self.clients[client.id] = client
            self.last_client_left_at = None
            client_count = len(self.clients)
        log.info("VIDEO_CLIENT_ADD device=%s client=%s user=%s clients=%s", self.device_id, client.id, client.username, client_count)
        self.prime_client(client, reason="client_join")
        return True

    def _reprime_clients_after_restart(self, was_restart: bool) -> None:
        """Quality change restarted the encoder: existing viewers hold a dead decoder state.

        ``_reset_video_state()`` clears the cached keyframes, so without this the viewer
        waits for the encoder's next *natural* IDR.  Measured on the RK3588 cloud phones
        that gap is ~5s (irregular: 0.2-0.5s pairs then ~5.1-5.3s), and with a static
        screen it can be much longer because scrcpy only encodes on screen change.
        Re-priming pushes the freshly cached keyframe immediately and asks for a reset,
        which the encoder answers with an IDR right away (measured 5ms).
        """
        if not was_restart:
            return
        with self._clients_lock:
            clients = list(self.clients.values())
        for client in clients:
            # keep_boundary: 不要把 _advance_video_generation 放进队列的 StreamReset 冲掉，
            # 客户端要靠它重建解码器（既有测试也盯着这个契约）。
            self.prime_client(client, reason="quality_restart", keep_boundary=True)

    def prime_client(
        self,
        client: ClientSession,
        reason: str = "player_reset",
        *,
        keep_boundary: bool = False,
    ) -> None:
        with self._clients_lock:
            if not keep_boundary:
                client.clear_queue()
            client.needs_keyframe = True
            reset_allowed = self._single_client_reset_allowed_locked()
            cached_raw = self._latest_raw_keyframe
            cached_legacy = self._latest_legacy_keyframe
            stream_mode = self.effective_stream_mode
        # Feed a decoder-ready frame immediately when one is available.  The
        # reset request below is still sent for a fresh IDR, but a slow or
        # unsupported reset no longer leaves the UI stuck at "waiting for
        # keyframe".
        if stream_mode == "legacy" and cached_legacy:
            client.push_frame(cached_legacy[0], keyframe=True, config=cached_legacy[1])
        elif stream_mode != "legacy" and cached_raw:
            client.push_access_unit(cached_raw, keyframe=True)
        log.info("VIDEO_CLIENT_WAIT_FRESH_KEYFRAME device=%s client=%s user=%s reason=%s", self.device_id, client.id, client.username, reason)
        if reset_allowed:
            self.schedule_video_reset(reason)

    def schedule_video_reset(self, reason: str) -> None:
        if not self.running or not self.scrcpy:
            return
        if self._video_reset_task and not self._video_reset_task.done():
            return
        generation = self._current_video_generation()
        task = self.loop.create_task(self.request_video_reset(reason, generation))
        self._video_reset_task = task

        def clear_task(completed: asyncio.Task) -> None:
            if self._video_reset_task is completed:
                self._video_reset_task = None

        task.add_done_callback(clear_task)

    async def request_video_reset(self, reason: str, generation: int | None = None) -> bool:
        expected_generation = self._current_video_generation() if generation is None else generation
        async with self._video_reset_lock:
            if (
                not self._generation_is_current(expected_generation)
                or not self.running
                or not self.scrcpy
                or not self.single_client_reset_allowed()
            ):
                return False
            wait_seconds = VIDEO_RESET_COOLDOWN - (time.monotonic() - self._last_video_reset_at)
            if wait_seconds > 0:
                await asyncio.sleep(wait_seconds)
            if (
                not self._generation_is_current(expected_generation)
                or not self.running
                or not self.scrcpy
                or not self.single_client_reset_allowed()
            ):
                return False
            self._last_video_reset_at = time.monotonic()
            scrcpy = self.scrcpy
            try:
                ok = await asyncio.to_thread(
                    self._send_video_reset_if_single_client,
                    scrcpy,
                    expected_generation,
                )
            except Exception as exc:
                self.last_error = str(exc)
                log.warning("VIDEO_RESET_FAILED device=%s reason=%s generation=%s error=%s", self.device_id, reason, expected_generation, exc)
                return False
            if ok:
                log.info("VIDEO_RESET_REQUESTED device=%s reason=%s generation=%s", self.device_id, reason, expected_generation)
            else:
                log.warning("VIDEO_RESET_FAILED device=%s reason=%s generation=%s error=%s", self.device_id, reason, expected_generation, self.last_error)
            return ok

    def _send_video_reset_if_single_client(self, scrcpy: Scrcpy, expected_generation: int) -> bool:
        with self._clients_lock:
            if (
                not self._generation_is_current(expected_generation)
                or not self.running
                or self.scrcpy is not scrcpy
                or not self._single_client_reset_allowed_locked()
            ):
                return False
            return self._send_control_to(scrcpy, SCRCPY_RESET_VIDEO_MESSAGE)

    def remove_client(self, client_id: str) -> None:
        with self._clients_lock:
            self.clients.pop(client_id, None)
            self._purge_viewer_reservations_locked()
            if self.running and not self.clients and not self._viewer_reservations:
                self.last_client_left_at = time.monotonic()
            client_count = len(self.clients)
        log.info("VIDEO_CLIENT_REMOVE device=%s client=%s clients=%s", self.device_id, client_id, client_count)

    def resolve_departing_client(self, client_id: str, username: str, viewer_token: str = "") -> tuple[str, bool]:
        normalized_id = str(client_id or "").strip()
        normalized_user = str(username or "")
        normalized_token = str(viewer_token or "").strip()
        with self._clients_lock:
            if normalized_id:
                departing = self.clients.get(normalized_id)
                if departing is not None and departing.username == normalized_user:
                    return normalized_id, False
                return "", bool(departing is not None or self.clients)
            if normalized_token:
                for candidate in self.clients.values():
                    if candidate.username == normalized_user and candidate.viewer_token == normalized_token:
                        return candidate.id, False
                return "", bool(self.clients)
            # Legacy callers may not know the WebSocket client id. Let the
            # bounded wait below prove that the session has no viewers before
            # stopping it; stop_if_no_clients() remains the final guard.
            return "", False

    def has_client(self, client_id: str) -> bool:
        with self._clients_lock:
            return client_id in self.clients

    def has_clients(self) -> bool:
        with self._clients_lock:
            return bool(self.clients)

    def send_control(self, payload: bytes) -> bool:
        return self._send_control_to(self.scrcpy, payload)

    def _send_control_to(self, scrcpy: Scrcpy | None, payload: bytes) -> bool:
        with self._control_send_lock:
            if not self.running or not scrcpy or self.scrcpy is not scrcpy:
                self.last_error = "mirror is not running"
                return False
            return bool(scrcpy.scrcpy_send_control(payload))

    def send_control_for_epoch(self, payload: bytes, expected_epoch: int) -> bool:
        with _control_device_lock(self.device_id):
            with _control_epoch_lock:
                if _control_lock_epochs.get(self.device_id, 0) != expected_epoch:
                    self.last_error = "control lock changed"
                    return False
            return self.send_control(payload)
