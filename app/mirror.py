import asyncio
import base64
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from scrcpy import Scrcpy
from . import storage
from .adb_monitor import adb_monitor
from .devices import public_adb_payload, public_lock_payload, session_payload, sessions_payload
from .h264 import (
    H264AnnexBParser,
    H264_NAL_IDR,
    H264_NAL_PPS,
    H264_NAL_SPS,
    annexb_nal_types,
    is_annexb,
    is_first_vcl_nal,
    nal_type,
    normalize_h264_payload,
)
from .scrcpy_demuxer import ScrcpyProtocolDemuxer, ScrcpyPacket
from .video_options import enabled_stream_modes_value, public_video_options, settings_to_video_options, signature, stream_mode_or_default

VIDEO_QUEUE_MAXSIZE = max(8, int(os.environ.get("VIDEO_QUEUE_MAXSIZE", "24") or "24"))
VIDEO_QUEUE_SOFT_LIMIT = int(os.environ.get("VIDEO_QUEUE_SOFT_LIMIT", "8") or "0")
VIDEO_RESET_COOLDOWN = max(0.1, float(os.environ.get("VIDEO_RESET_COOLDOWN", "0.75") or "0.75"))
CONTROL_LEASE_VERIFY_INTERVAL = max(0.1, float(os.environ.get("CONTROL_LEASE_VERIFY_INTERVAL", "0.5") or "0.5"))
SCRCPY_RESET_VIDEO_MESSAGE = b"\x11"
SCRCPY_STREAM_MODE = os.environ.get("SCRCPY_STREAM_MODE", "raw").strip().lower() or "raw"
STREAM_HEALTH_TIMEOUT = float(os.environ.get("SCRCPY_STREAM_HEALTH_TIMEOUT", "5") or "5")
_start_lock = threading.Lock()
_control_epoch_lock = threading.RLock()
_control_device_locks: dict[str, threading.Lock] = {}
_control_lock_epochs: dict[str, int] = {}
log = logging.getLogger("webscrcpy.mirror")


def _stream_mode() -> str:
    try:
        configured = storage.get_setting("scrcpy_stream_mode", SCRCPY_STREAM_MODE)
        enabled = enabled_stream_modes_value(storage.get_setting("scrcpy_enabled_stream_modes", "raw"))
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
) -> bool:
    with _control_device_lock(device_id):
        ok = storage.release_lock(device_id, username, force=force, client_id=client_id)
        if ok:
            with _control_epoch_lock:
                _bump_control_lock_epoch(device_id)
        return ok


@dataclass(frozen=True)
class StreamReset:
    generation: int


@dataclass
class ClientSession:
    id: str
    username: str
    websocket: WebSocket
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=VIDEO_QUEUE_MAXSIZE))
    drops: int = 0
    needs_keyframe: bool = True

    def clear_queue(self) -> None:
        while True:
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    def soft_limit(self) -> int:
        maxsize = self.queue.maxsize or VIDEO_QUEUE_MAXSIZE
        if VIDEO_QUEUE_SOFT_LIMIT <= 0:
            return maxsize
        if VIDEO_QUEUE_SOFT_LIMIT >= maxsize:
            return max(1, int(maxsize * 0.75))
        return max(1, VIDEO_QUEUE_SOFT_LIMIT)

    def push_frame(self, frame: bytes, keyframe: bool = False, config: bytes = b"") -> bool:
        if self.needs_keyframe and not keyframe:
            return False
        if self.queue.qsize() >= self.soft_limit():
            self.clear_queue()
            self.drops += 1
            self.needs_keyframe = True
            if not keyframe:
                return True
        payload = config + frame if keyframe and config else frame
        try:
            self.queue.put_nowait(payload)
            if keyframe:
                self.needs_keyframe = False
        except asyncio.QueueFull:
            self.clear_queue()
            self.drops += 1
            self.needs_keyframe = True
            return True
        return False

    def push_stream_reset(self, generation: int) -> None:
        self.clear_queue()
        self.needs_keyframe = True
        self.queue.put_nowait(StreamReset(generation))


@dataclass
class EventClient:
    username: str
    websocket: WebSocket
    ready: bool = False
    pending: list[str] = field(default_factory=list)
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass
class ControlLeaseState:
    verified_until: float = 0.0
    epoch: int = -1

    def mark_verified(self, epoch: int) -> None:
        self.epoch = epoch
        self.verified_until = time.monotonic() + CONTROL_LEASE_VERIFY_INTERVAL

    def clear(self) -> None:
        self.epoch = -1
        self.verified_until = 0.0

    def is_verified(self, epoch: int) -> bool:
        return self.epoch == epoch and time.monotonic() < self.verified_until


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
        self.protocol_demuxer: ScrcpyProtocolDemuxer | None = None
        self.sps = b""
        self.pps = b""
        self.config_packet = b""
        self.video_options = settings_to_video_options(storage.get_settings())
        self.stream_mode = _stream_mode()
        self.effective_stream_mode = "none"
        self.stream_health = "idle"
        self.last_keyframe_at: int | None = None
        self.last_client_left_at: float | None = None
        self.last_adb_status: dict[str, Any] | None = None
        self._lock = asyncio.Lock()
        self._control_send_lock = threading.Lock()
        self._video_reset_lock = asyncio.Lock()
        self._video_reset_task: asyncio.Task | None = None
        self._transport_cleanup_task: asyncio.Task | None = None
        self._last_video_reset_at = 0.0
        self._video_generation = 0
        self._protocol_invalid_logged = False
        self._protocol_payload_logged = False

    def snapshot(self) -> dict[str, Any]:
        lock = storage.get_lock(self.device_id)
        return {
            "device_id": self.device_id,
            "running": self.running,
            "clients": len(self.clients),
            "client_drops": sum(client.drops for client in self.clients.values()),
            "queue_soft_limit": VIDEO_QUEUE_SOFT_LIMIT,
            "last_error": self.last_error,
            "stream_mode": self.effective_stream_mode,
            "stream_health": self.stream_health,
            "last_keyframe_at": self.last_keyframe_at,
            "idle_since": int(self.last_client_left_at) if self.last_client_left_at else None,
            "adb": self.last_adb_status,
            "control_lock": lock,
            "video": public_video_options(self.video_options),
        }

    async def start(self, options: dict[str, Any] | None = None, force_restart: bool = False) -> bool:
        target_options = public_video_options(options or settings_to_video_options(storage.get_settings()))
        async with self._lock:
            if not self.available:
                self.running = False
                self.last_error = "device unavailable"
                return False
            if self.running:
                if signature(self.video_options) == signature(target_options):
                    self.video_options = target_options
                    log.info("MIRROR_ALREADY_RUNNING device=%s mode=%s", self.device_id, self.effective_stream_mode)
                    return True
                scpy = self.scrcpy
                self.scrcpy = None
                self.running = False
                self._video_generation += 1
                if self._video_reset_task and not self._video_reset_task.done():
                    self._video_reset_task.cancel()
                self._video_reset_task = None
                if scpy:
                    await asyncio.to_thread(scpy.scrcpy_stop)
            if self._transport_cleanup_task and not self._transport_cleanup_task.done():
                await self._transport_cleanup_task
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
            mode = stream_mode_or_default(mode, enabled_stream_modes_value(storage.get_setting("scrcpy_enabled_stream_modes", "raw")))
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
                generation = self._video_generation
                ok = await asyncio.to_thread(self._start_scrcpy, bit_rate, max_size, max_fps, mode)
                if generation != self._video_generation:
                    ok = False
                self.running = ok
                if ok:
                    healthy = await self._wait_for_stream_health(mode)
                    if healthy or mode == "legacy":
                        self.last_error = ""
                        log.info("MIRROR_HEALTHY device=%s mode=%s last_keyframe_at=%s", self.device_id, mode, self.last_keyframe_at)
                        return True
                    last_error = f"{mode} stream did not become healthy"
                    log.warning("MIRROR_STREAM_UNHEALTHY device=%s mode=%s health=%s last_error=%s", self.device_id, mode, self.stream_health, self.last_error)
                    await self._stop_scrcpy_only()
                else:
                    last_error = self.last_error or f"failed to start scrcpy in {mode} mode"
                    log.warning("MIRROR_START_FAILED device=%s mode=%s error=%s", self.device_id, mode, last_error)
            self.running = False
            self.stream_health = "failed"
            self.last_error = last_error or "failed to start scrcpy"
            log.error("MIRROR_FAILED device=%s error=%s", self.device_id, self.last_error)
            return False

    def _start_scrcpy(self, bit_rate: int, max_size: int, max_fps: int, mode: str) -> bool:
        with _start_lock:
            scpy = Scrcpy()
            scpy.device_id = self.device_id
            scpy.device_address = self.address
            generation = self._video_generation
            ok = scpy.scrcpy_start(
                lambda data: self.publish_video(data, generation),
                bit_rate,
                max_size,
                max_fps,
                stream_mode=mode,
                stream_exit_callback=lambda reason: self._on_stream_transport_closed(generation, scpy, reason),
            )
            if ok and scpy.unexpected_exit_reason:
                self.last_error = scpy.unexpected_exit_reason
                scpy.scrcpy_stop()
                return False
            if ok:
                self.scrcpy = scpy
            else:
                self.last_error = scpy.last_error or f"failed to start scrcpy in {mode} mode"
            return ok

    def _on_stream_transport_closed(self, generation: int, scrcpy: Scrcpy, reason: str) -> None:
        self.loop.call_soon_threadsafe(self._handle_stream_transport_closed, generation, scrcpy, reason)

    def _handle_stream_transport_closed(self, generation: int, scrcpy: Scrcpy, reason: str) -> None:
        if generation != self._video_generation or self.scrcpy is not scrcpy:
            return
        self.running = False
        self.scrcpy = None
        self.stream_health = "failed"
        self.last_error = reason or "video stream ended"
        self._video_generation += 1
        failed_generation = self._video_generation
        if self._video_reset_task and not self._video_reset_task.done():
            self._video_reset_task.cancel()
        self._video_reset_task = None
        for client in self.clients.values():
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
        await asyncio.to_thread(scrcpy.scrcpy_stop)
        if failed_generation != self._video_generation or self.running:
            return
        await manager.broadcast(
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
        self.protocol_demuxer = ScrcpyProtocolDemuxer() if mode == "protocol" else None
        self.sps = b""
        self.pps = b""
        self.config_packet = b""
        self._video_generation += 1
        self._last_video_reset_at = 0.0
        if self._video_reset_task and not self._video_reset_task.done():
            self._video_reset_task.cancel()
        self._video_reset_task = None
        self.stream_health = "starting"
        self.effective_stream_mode = mode
        self.last_keyframe_at = None
        self._protocol_invalid_logged = False
        self._protocol_payload_logged = False
        for client in self.clients.values():
            client.push_stream_reset(self._video_generation)

    async def _wait_for_stream_health(self, mode: str) -> bool:
        deadline = time.monotonic() + STREAM_HEALTH_TIMEOUT
        while time.monotonic() < deadline:
            if self.stream_health == "healthy" and self.last_keyframe_at and self.codec_config():
                return True
            if not self.running:
                return False
            await asyncio.sleep(0.1)
        return False

    async def _stop_scrcpy_only(self) -> None:
        scpy = self.scrcpy
        self.scrcpy = None
        self.running = False
        if scpy:
            await asyncio.to_thread(scpy.scrcpy_stop)

    async def stop(self) -> bool:
        async with self._lock:
            log.info("MIRROR_STOP device=%s mode=%s", self.device_id, self.effective_stream_mode)
            scpy = self.scrcpy
            self.scrcpy = None
            self.running = False
            self._video_generation += 1
            if self._video_reset_task and not self._video_reset_task.done():
                self._video_reset_task.cancel()
            self._video_reset_task = None
            if scpy:
                await asyncio.to_thread(scpy.scrcpy_stop)
            self.stream_health = "stopped"
            self.last_client_left_at = None
            release_control_lock(self.device_id, "", force=True)
            for client in self.clients.values():
                client.clear_queue()
                client.needs_keyframe = True
            return True

    async def restart(self) -> bool:
        await self.stop()
        return await self.start(self.video_options, force_restart=True)

    def codec_config(self) -> bytes:
        if self.config_packet:
            return self.config_packet
        return self.sps + self.pps if self.sps and self.pps else b""

    def publish_video(self, data: bytes, generation: int | None = None) -> None:
        if generation is not None and generation != self._video_generation:
            return
        if self.protocol_demuxer is not None:
            for packet in self.protocol_demuxer.feed(data):
                self._process_packet(packet, generation)
            return
        for nal in self.video_parser.feed(data):
            self._process_nal(nal, generation)

    def _process_packet(self, packet: ScrcpyPacket, generation: int | None = None) -> None:
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
        if keyframe:
            self.last_keyframe_at = int(time.time())
            self.stream_health = "healthy"
        self.loop.call_soon_threadsafe(self.publish_nal, payload, keyframe, config, generation)

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
        if generation is not None and generation != self._video_generation:
            return
        reset_needed = False
        for client in list(self.clients.values()):
            before = client.drops
            dropped = client.push_frame(nal, keyframe=keyframe, config=config)
            if client.drops != before:
                log.warning("VIDEO_CLIENT_DROP device=%s client=%s user=%s drops=%s", self.device_id, client.id, client.username, client.drops)
            reset_needed = reset_needed or dropped
        if reset_needed:
            self.schedule_video_reset("queue_overflow")

    def add_client(self, client: ClientSession) -> None:
        self.clients[client.id] = client
        self.last_client_left_at = None
        log.info("VIDEO_CLIENT_ADD device=%s client=%s user=%s clients=%s", self.device_id, client.id, client.username, len(self.clients))
        self.prime_client(client, reason="client_join")

    def prime_client(self, client: ClientSession, reason: str = "player_reset") -> None:
        client.clear_queue()
        client.needs_keyframe = True
        log.info("VIDEO_CLIENT_WAIT_FRESH_KEYFRAME device=%s client=%s user=%s reason=%s", self.device_id, client.id, client.username, reason)
        self.schedule_video_reset(reason)

    def schedule_video_reset(self, reason: str) -> None:
        if not self.running or not self.scrcpy:
            return
        if self._video_reset_task and not self._video_reset_task.done():
            return
        generation = self._video_generation
        task = self.loop.create_task(self.request_video_reset(reason, generation))
        self._video_reset_task = task

        def clear_task(completed: asyncio.Task) -> None:
            if self._video_reset_task is completed:
                self._video_reset_task = None

        task.add_done_callback(clear_task)

    async def request_video_reset(self, reason: str, generation: int | None = None) -> bool:
        expected_generation = self._video_generation if generation is None else generation
        async with self._video_reset_lock:
            if expected_generation != self._video_generation or not self.running or not self.scrcpy:
                return False
            wait_seconds = VIDEO_RESET_COOLDOWN - (time.monotonic() - self._last_video_reset_at)
            if wait_seconds > 0:
                await asyncio.sleep(wait_seconds)
            if expected_generation != self._video_generation or not self.running or not self.scrcpy:
                return False
            self._last_video_reset_at = time.monotonic()
            scrcpy = self.scrcpy
            try:
                ok = await asyncio.to_thread(self._send_control_to, scrcpy, SCRCPY_RESET_VIDEO_MESSAGE)
            except Exception as exc:
                self.last_error = str(exc)
                log.warning("VIDEO_RESET_FAILED device=%s reason=%s generation=%s error=%s", self.device_id, reason, expected_generation, exc)
                return False
            if ok:
                log.info("VIDEO_RESET_REQUESTED device=%s reason=%s generation=%s", self.device_id, reason, expected_generation)
            else:
                log.warning("VIDEO_RESET_FAILED device=%s reason=%s generation=%s error=%s", self.device_id, reason, expected_generation, self.last_error)
            return ok

    def remove_client(self, client_id: str) -> None:
        self.clients.pop(client_id, None)
        if self.running and not self.clients:
            self.last_client_left_at = time.monotonic()
        log.info("VIDEO_CLIENT_REMOVE device=%s client=%s clients=%s", self.device_id, client_id, len(self.clients))

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


class MirrorManager:
    def __init__(self):
        self.sessions: dict[str, MirrorSession] = {}
        self.events: dict[str, EventClient] = {}
        self._lock = asyncio.Lock()

    async def get_or_create(self, device_id: str) -> MirrorSession:
        async with self._lock:
            cached = self.sessions.get(device_id)
            if cached and cached.available:
                return cached
            device = storage.get_device(device_id)
            if not device or not bool(device["enabled"]):
                raise KeyError("unknown or disabled device")
            if cached:
                self.sessions.pop(device_id, None)
            loop = asyncio.get_running_loop()
            session = MirrorSession(device_id, device["address"], loop)
            self.sessions[device_id] = session
            return session

    async def start(self, device_id: str, options: dict[str, Any] | None = None, force_restart: bool = False) -> bool:
        try:
            session = await self.get_or_create(device_id)
        except KeyError:
            return False
        ok = await session.start(options, force_restart=force_restart)
        await self.broadcast({"type": "mirror_status", "device_id": device_id, "running": ok, "session": session.snapshot()})
        return ok

    async def stop(self, device_id: str) -> bool:
        session = await self.get_or_create(device_id)
        ok = await session.stop()
        await self.broadcast({"type": "mirror_status", "device_id": device_id, "running": False, "session": session.snapshot()})
        return ok

    @staticmethod
    def _terminate_video_clients(session: MirrorSession, device_id: str, reason: str) -> None:
        for client in list(session.clients.values()):
            try:
                client.clear_queue()
                client.queue.put_nowait(None)
            except Exception:
                log.exception("VIDEO_CLIENT_TERMINATE_FAILED device=%s reason=%s", device_id, reason)

    async def reconfigure_device(self, device_id: str, address: str) -> bool:
        """Stop a cached transport before changing its mutable ADB endpoint."""
        async with self._lock:
            session = self.sessions.get(device_id)
        if not session:
            return False
        was_running = session.running
        session.available = False
        try:
            await session.stop()
        except Exception:
            log.exception("MIRROR_RECONFIGURE_STOP_FAILED device=%s", device_id)
            release_control_lock(device_id, "", force=True)
            self._terminate_video_clients(session, device_id, "device_updated")
            async with self._lock:
                if self.sessions.get(device_id) is session:
                    self.sessions.pop(device_id, None)
            await self.broadcast(
                {"type": "mirror_status", "device_id": device_id, "running": False, "reason": "device_updated"}
            )
            return False
        session.address = address
        session.available = True
        if was_running:
            await self.broadcast(
                {
                    "type": "mirror_status",
                    "device_id": device_id,
                    "running": False,
                    "session": session.snapshot(),
                    "reason": "device_updated",
                }
            )
        return True

    async def event_usernames_for_device(self, device_id: str) -> set[str]:
        usernames = {client.username for client in self.events.values()}
        if not usernames:
            return set()
        return await asyncio.to_thread(event_users_with_view_access, usernames, device_id)

    async def remove_device(
        self,
        device_id: str,
        reason: str = "device_deleted",
        notify_users: set[str] | None = None,
    ) -> bool:
        async with self._lock:
            session = self.sessions.pop(device_id, None)
            if session:
                session.available = False
        if session:
            try:
                await session.stop()
            except Exception:
                log.exception("MIRROR_REMOVE_STOP_FAILED device=%s reason=%s", device_id, reason)
                release_control_lock(device_id, "", force=True)
            self._terminate_video_clients(session, device_id, reason)
        else:
            release_control_lock(device_id, "", force=True)
        message = {"type": "mirror_status", "device_id": device_id, "running": False, "reason": reason}
        if session:
            try:
                message["session"] = session.snapshot()
            except Exception:
                log.exception("MIRROR_REMOVE_SNAPSHOT_FAILED device=%s reason=%s", device_id, reason)
        await self.broadcast(message, allowed_users=notify_users)
        return session is not None

    async def stop_other_no_client_sessions(self, keep_device_id: str, allowed_device_ids: list[str]) -> list[str]:
        allowed = set(allowed_device_ids)
        stopped: list[str] = []
        for device_id, session in list(self.sessions.items()):
            if device_id == keep_device_id or device_id not in allowed:
                continue
            if not session.running or session.clients:
                continue
            ok = await session.stop()
            if ok:
                stopped.append(device_id)
                await self.broadcast({"type": "mirror_status", "device_id": device_id, "running": False, "session": session.snapshot(), "reason": "switch"})
        return stopped

    async def stop_if_no_clients(self, device_id: str, wait_seconds: float = 0) -> bool:
        session = await self.get_or_create(device_id)
        deadline = time.monotonic() + max(0, wait_seconds)
        while session.running and session.clients and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
        if not session.running or session.clients:
            return False
        ok = await session.stop()
        await self.broadcast({"type": "mirror_status", "device_id": device_id, "running": False, "session": session.snapshot(), "reason": "idle"})
        return ok

    async def stop_idle_sessions(self, idle_seconds: int) -> list[str]:
        if idle_seconds <= 0:
            return []
        now = time.monotonic()
        stopped: list[str] = []
        for device_id, session in list(self.sessions.items()):
            if not session.running or session.clients:
                continue
            if session.last_client_left_at is None:
                session.last_client_left_at = now
                continue
            if now - session.last_client_left_at >= idle_seconds:
                ok = await session.stop()
                if ok:
                    stopped.append(device_id)
                    await self.broadcast({"type": "mirror_status", "device_id": device_id, "running": False, "session": session.snapshot(), "reason": "idle"})
        return stopped

    async def snapshot(self) -> dict[str, Any]:
        return {device_id: session.snapshot() for device_id, session in self.sessions.items()}

    async def stop_all(self) -> None:
        sessions = list(self.sessions.items())
        if not sessions:
            return
        results = await asyncio.gather(*(session.stop() for _, session in sessions), return_exceptions=True)
        for (device_id, _), result in zip(sessions, results):
            if isinstance(result, Exception):
                log.warning("MIRROR_SHUTDOWN_STOP_FAILED device=%s error=%s", device_id, result)

    async def broadcast(self, message: dict[str, Any], allowed_users: set[str] | None = None) -> None:
        clients = list(self.events.items())
        if not clients:
            return
        real_device_id = str(message.get("device_id") or "").strip()
        public_device_id = ""
        if real_device_id:
            public_device_id = storage.public_device_id(real_device_id)
            if allowed_users is None:
                usernames = {client.username for _, client in clients}
                allowed_users = await asyncio.to_thread(event_users_with_view_access, usernames, real_device_id)
        device_encoded = json.dumps(public_event_payload(message, real_device_id, public_device_id), ensure_ascii=False) if real_device_id else ""
        global_encoded = json.dumps(message, ensure_ascii=False) if not real_device_id else ""
        recipients = [
            (client_id, client, device_encoded if real_device_id else global_encoded)
            for client_id, client in clients
            if allowed_users is None or client.username in allowed_users
        ]

        async def send_event(client: EventClient, encoded: str) -> None:
            async with client.send_lock:
                if not client.ready:
                    client.pending.append(encoded)
                    return
                await client.websocket.send_text(encoded)

        results = await asyncio.gather(
            *(asyncio.wait_for(send_event(client, encoded), timeout=1.0) for _, client, encoded in recipients),
            return_exceptions=True,
        )
        for (client_id, client, _), result in zip(recipients, results):
            if isinstance(result, Exception):
                self.events.pop(client_id, None)
                try:
                    await asyncio.wait_for(client.websocket.close(code=1011), timeout=0.5)
                except Exception:
                    pass

    async def register_event_ws(self, websocket: WebSocket, username: str) -> None:
        client_id = str(uuid.uuid4())
        client = EventClient(username=username, websocket=websocket)
        self.events[client_id] = client
        try:
            devices = await asyncio.to_thread(event_user_devices, username)
            snapshot = sessions_payload(devices, await self.snapshot(), public_id=True)
            async with client.send_lock:
                await websocket.send_json({"type": "hello", "client_id": client_id, "username": username, "sessions": snapshot})
                for encoded in client.pending:
                    await websocket.send_text(encoded)
                client.pending.clear()
                client.ready = True
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            self.events.pop(client_id, None)


def event_user_devices(username: str) -> list[dict[str, Any]]:
    user = storage.get_user(username)
    return storage.list_devices_for_user(username, bool(user and user["role"] == "admin"))


def event_users_with_view_access(usernames: set[str], device_id: str) -> set[str]:
    allowed: set[str] = set()
    for username in usernames:
        try:
            if storage.user_can(username, device_id, "view"):
                allowed.add(username)
                continue
            user = storage.get_user(username)
            if user and user["role"] == "admin" and storage.user_is_active(user):
                allowed.add(username)
        except Exception:
            log.exception("EVENT_PERMISSION_CHECK_FAILED user=%s device=%s", username, device_id)
    return allowed


manager = MirrorManager()


def public_event_payload(value: Any, real_device_id: str, public_device_id: str) -> Any:
    if isinstance(value, dict):
        payload: dict[str, Any] = {}
        for key, item in value.items():
            if key == "device_id" and item == real_device_id:
                payload[key] = public_device_id
            elif key == "session" and isinstance(item, dict):
                payload[key] = session_payload(item, real_device_id, public_device_id, public=True)
            elif key in {"lock", "control_lock"} and isinstance(item, dict):
                payload[key] = public_lock_payload(item, public_device_id)
            elif key == "adb" and isinstance(item, dict):
                payload[key] = public_adb_payload(item, public_device_id)
            else:
                payload[key] = public_event_payload(item, real_device_id, public_device_id)
        return payload
    if isinstance(value, list):
        return [public_event_payload(item, real_device_id, public_device_id) for item in value]
    return value


def exposed_snapshot(session: MirrorSession, exposed_device_id: str | None = None) -> dict[str, Any]:
    data = session.snapshot()
    if exposed_device_id:
        return session_payload(
            data,
            session.device_id,
            exposed_device_id,
            public=True,
        ) or {}
    return data


async def video_socket(websocket: WebSocket, user: dict, device_id: str, exposed_device_id: str | None = None):
    if not storage.user_can(user["username"], device_id, "view"):
        await websocket.close(code=4403)
        return
    await websocket.accept()
    try:
        session = await manager.get_or_create(device_id)
    except KeyError:
        await websocket.close(code=4403)
        return
    client = ClientSession(str(uuid.uuid4()), user["username"], websocket)
    session.add_client(client)
    public_id = exposed_device_id or device_id
    await websocket.send_json({"type": "hello", "client_id": client.id, "device_id": public_id, "session": exposed_snapshot(session, public_id)})

    async def sender() -> None:
        try:
            while True:
                frame = await client.queue.get()
                if frame is None:
                    await websocket.close(code=1011, reason="video stream ended")
                    return
                if isinstance(frame, StreamReset):
                    await websocket.send_json({"type": "stream_reset", "generation": frame.generation})
                    continue
                await websocket.send_bytes(frame)
        except (WebSocketDisconnect, asyncio.CancelledError):
            raise
        except Exception as exc:
            log.warning("VIDEO_CLIENT_SEND_ERROR device=%s client=%s user=%s error=%s", device_id, client.id, user["username"], exc)

    async def receiver() -> None:
        try:
            while True:
                text = await websocket.receive_text()
                try:
                    msg = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if msg.get("type") == "player_reset":
                    session.prime_client(client, reason="player_reset")
        except (WebSocketDisconnect, asyncio.CancelledError):
            raise
        except Exception as exc:
            log.warning("VIDEO_CLIENT_RECV_ERROR device=%s client=%s user=%s error=%s", device_id, client.id, user["username"], exc)

    try:
        sender_task = asyncio.ensure_future(sender())
        receiver_task = asyncio.ensure_future(receiver())
        done, pending = await asyncio.wait([sender_task, receiver_task], return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            try:
                task.result()
            except (WebSocketDisconnect, asyncio.CancelledError):
                pass
            except Exception as exc:
                log.warning("VIDEO_CLIENT_TASK_ERROR device=%s client=%s user=%s error=%s", device_id, client.id, user["username"], exc)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        session.remove_client(client.id)


async def control_socket(websocket: WebSocket, user: dict, device_id: str, exposed_device_id: str | None = None):
    if not storage.user_can(user["username"], device_id, "control"):
        await websocket.close(code=4403)
        return
    await websocket.accept()
    client_id = str(uuid.uuid4())
    lease = ControlLeaseState()
    await websocket.send_json({"type": "hello", "client_id": client_id, "device_id": exposed_device_id or device_id, "lock": storage.get_lock(device_id)})
    try:
        while True:
            message = await websocket.receive()
            if not await asyncio.to_thread(storage.user_can, user["username"], device_id, "control"):
                await websocket.close(code=4403)
                return
            if message.get("text") is not None:
                await handle_control_text(websocket, user, device_id, client_id, message["text"], lease)
            elif message.get("bytes") is not None:
                await handle_control_bytes(websocket, user, device_id, client_id, message["bytes"], lease)
    except WebSocketDisconnect:
        pass
    except KeyError:
        await websocket.close(code=4403)
    finally:
        release_control_lock(device_id, user["username"], force=False, client_id=client_id)
        await manager.broadcast({"type": "control_lock", "device_id": device_id, "lock": storage.get_lock(device_id)})


async def handle_control_text(
    websocket: WebSocket,
    user: dict,
    device_id: str,
    client_id: str,
    text: str,
    lease: ControlLeaseState | None = None,
):
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        await websocket.send_json({"type": "error", "error": "invalid json"})
        return
    msg_type = data.get("type")
    if msg_type == "acquire_control":
        force = bool(data.get("force") and user.get("role") == "admin")
        result, epoch = acquire_control_lock(device_id, user["username"], client_id, force=force)
        if lease:
            if result.get("ok"):
                lease.mark_verified(epoch)
            else:
                lease.clear()
        await websocket.send_json({"type": "control_lock", **result, "lock": storage.get_lock(device_id)})
        await manager.broadcast({"type": "control_lock", "device_id": device_id, "lock": storage.get_lock(device_id)})
        return
    if msg_type == "control_keepalive":
        ok, epoch = renew_control_lock(device_id, user["username"], client_id, ttl_seconds=90)
        if lease:
            if ok:
                lease.mark_verified(epoch)
            else:
                lease.clear()
        lock = storage.get_lock(device_id)
        await websocket.send_json(
            {
                "type": "control_lock",
                "ok": ok,
                "owner": lock.get("username") if lock else None,
                "expires_at": lock.get("expires_at") if lock else None,
                "lock": lock,
            }
        )
        return
    if msg_type == "release_control":
        ok = release_control_lock(device_id, user["username"], force=user.get("role") == "admin", client_id=client_id)
        if lease:
            lease.clear()
        await websocket.send_json({"type": "control_released", "ok": ok})
        await manager.broadcast({"type": "control_lock", "device_id": device_id, "lock": storage.get_lock(device_id)})
        return
    if msg_type == "control_base64":
        encoded = data.get("data")
        if not isinstance(encoded, str) or not encoded:
            await websocket.send_json({"type": "error", "error": "invalid control payload"})
            return
        try:
            payload = base64.b64decode(encoded, validate=True)
        except Exception:
            await websocket.send_json({"type": "error", "error": "invalid control payload"})
            return
        if not payload:
            await websocket.send_json({"type": "error", "error": "invalid control payload"})
            return
        await handle_control_bytes(websocket, user, device_id, client_id, payload, lease)
        return
    if msg_type == "player_reset":
        session = await manager.get_or_create(device_id)
        for client in session.clients.values():
            if client.username == user["username"]:
                session.prime_client(client, reason="control_player_reset")
        return
    await websocket.send_json({"type": "error", "error": "unknown control message"})


async def handle_control_bytes(
    websocket: WebSocket,
    user: dict,
    device_id: str,
    client_id: str,
    payload: bytes,
    lease: ControlLeaseState | None = None,
):
    if not payload:
        await websocket.send_json({"type": "error", "error": "invalid control payload"})
        return
    epoch = control_lock_epoch(device_id)
    verified = bool(lease and lease.is_verified(epoch))
    if not verified:
        verified, epoch = await asyncio.to_thread(renew_control_lock, device_id, user["username"], client_id, ttl_seconds=90)
        if lease:
            if verified:
                lease.mark_verified(epoch)
            else:
                lease.clear()
    if not verified:
        lock = await asyncio.to_thread(storage.get_lock, device_id)
        await websocket.send_json({"type": "control_lock", "ok": False, "lock": lock})
        return
    session = await manager.get_or_create(device_id)
    ok = await asyncio.to_thread(session.send_control_for_epoch, payload, epoch)
    if not ok:
        if control_lock_epoch(device_id) != epoch:
            lock = await asyncio.to_thread(storage.get_lock, device_id)
            await websocket.send_json({"type": "control_lock", "ok": False, "lock": lock})
            return
        await websocket.send_json({"type": "control_error", "error": session.last_error or "control failed"})
