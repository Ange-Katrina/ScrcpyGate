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
    nal_type,
    normalize_h264_payload,
)
from .scrcpy_demuxer import ScrcpyProtocolDemuxer, ScrcpyPacket
from .video_options import enabled_stream_modes_value, public_video_options, settings_to_video_options, signature, stream_mode_or_default

VIDEO_QUEUE_MAXSIZE = max(8, int(os.environ.get("VIDEO_QUEUE_MAXSIZE", "60") or "60"))
VIDEO_QUEUE_SOFT_LIMIT = int(os.environ.get("VIDEO_QUEUE_SOFT_LIMIT", str(max(8, int(VIDEO_QUEUE_MAXSIZE * 0.75)))) or "0")
SCRCPY_STREAM_MODE = os.environ.get("SCRCPY_STREAM_MODE", "raw").strip().lower() or "raw"
STREAM_HEALTH_TIMEOUT = float(os.environ.get("SCRCPY_STREAM_HEALTH_TIMEOUT", "5") or "5")
_start_lock = threading.Lock()
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

    def push_frame(self, frame: bytes, keyframe: bool = False, config: bytes = b"") -> None:
        if self.needs_keyframe and not keyframe:
            return
        if self.queue.qsize() >= self.soft_limit():
            self.clear_queue()
            self.drops += 1
            self.needs_keyframe = True
            if not keyframe:
                return
        payload = config + frame if keyframe and config else frame
        try:
            self.queue.put_nowait(payload)
            if keyframe:
                self.needs_keyframe = False
        except asyncio.QueueFull:
            self.clear_queue()
            self.drops += 1
            self.needs_keyframe = True


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
        self.video_parser = H264AnnexBParser()
        self.protocol_demuxer: ScrcpyProtocolDemuxer | None = None
        self.sps = b""
        self.pps = b""
        self.config_packet = b""
        self.last_keyframe_payload = b""
        self.video_options = settings_to_video_options(storage.get_settings())
        self.stream_mode = _stream_mode()
        self.effective_stream_mode = "none"
        self.stream_health = "idle"
        self.last_keyframe_at: int | None = None
        self.last_client_left_at: float | None = None
        self.last_adb_status: dict[str, Any] | None = None
        self._lock = asyncio.Lock()
        self._control_send_lock = threading.Lock()
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
        if self.running:
            if not force_restart and signature(self.video_options) == signature(target_options):
                log.info("MIRROR_ALREADY_RUNNING device=%s mode=%s", self.device_id, self.effective_stream_mode)
                return True
            await self.stop()
        async with self._lock:
            if self.running:
                return True
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
                ok = await asyncio.to_thread(self._start_scrcpy, bit_rate, max_size, max_fps, mode)
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
            ok = scpy.scrcpy_start(self.publish_video, bit_rate, max_size, max_fps, stream_mode=mode)
            if ok:
                self.scrcpy = scpy
            else:
                self.last_error = scpy.last_error or f"failed to start scrcpy in {mode} mode"
            return ok

    def _reset_video_state(self, mode: str) -> None:
        self.video_parser = H264AnnexBParser()
        self.protocol_demuxer = ScrcpyProtocolDemuxer() if mode == "protocol" else None
        self.sps = b""
        self.pps = b""
        self.config_packet = b""
        self.last_keyframe_payload = b""
        self.stream_health = "starting"
        self.effective_stream_mode = mode
        self.last_keyframe_at = None
        self._protocol_invalid_logged = False
        self._protocol_payload_logged = False
        for client in self.clients.values():
            client.clear_queue()
            client.needs_keyframe = True

    async def _wait_for_stream_health(self, mode: str) -> bool:
        deadline = time.monotonic() + STREAM_HEALTH_TIMEOUT
        while time.monotonic() < deadline:
            if self.stream_health == "healthy" and self.last_keyframe_payload:
                return True
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
            if not self.scrcpy:
                self.running = False
                return True
            log.info("MIRROR_STOP device=%s mode=%s", self.device_id, self.effective_stream_mode)
            scpy = self.scrcpy
            self.scrcpy = None
            self.running = False
            await asyncio.to_thread(scpy.scrcpy_stop)
            self.stream_health = "stopped"
            self.last_client_left_at = None
            storage.release_lock(self.device_id, "", force=True)
            for client in self.clients.values():
                client.clear_queue()
                client.needs_keyframe = True
            return True

    async def restart(self) -> bool:
        await self.stop()
        return await self.start(self.video_options, force_restart=True)

    def codec_config(self) -> bytes:
        return self.config_packet or (self.sps + self.pps)

    def publish_video(self, data: bytes) -> None:
        if self.protocol_demuxer is not None:
            for packet in self.protocol_demuxer.feed(data):
                self._process_packet(packet)
            return
        for nal in self.video_parser.feed(data):
            self._process_nal(nal)

    def _process_packet(self, packet: ScrcpyPacket) -> None:
        payload = normalize_h264_payload(packet.payload)
        if packet.config:
            if not is_annexb(payload):
                self._log_invalid_protocol_payload("config", payload)
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
        keyframe = has_idr or (packet.keyframe and not nal_types)
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
        if has_idr:
            self.last_keyframe_at = int(time.time())
            self.stream_health = "healthy"
        config = self.codec_config() if keyframe else b""
        self.loop.call_soon_threadsafe(self.publish_nal, payload, keyframe, config)

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

    def _process_nal(self, nal: bytes) -> None:
        ntype = nal_type(nal)
        if ntype == H264_NAL_SPS:
            self.sps = nal
            self.stream_health = "config"
            log.info("VIDEO_SPS device=%s mode=%s bytes=%s", self.device_id, self.effective_stream_mode, len(nal))
        elif ntype == H264_NAL_PPS:
            self.pps = nal
            self.stream_health = "config"
            log.info("VIDEO_PPS device=%s mode=%s bytes=%s", self.device_id, self.effective_stream_mode, len(nal))
        keyframe = ntype == H264_NAL_IDR
        if keyframe:
            self.last_keyframe_at = int(time.time())
            self.stream_health = "healthy"
        config = self.codec_config() if keyframe else b""
        if keyframe:
            log.info("VIDEO_IDR device=%s mode=%s bytes=%s config_bytes=%s", self.device_id, self.effective_stream_mode, len(nal), len(config))
        self.loop.call_soon_threadsafe(self.publish_nal, nal, keyframe, config)

    def publish_nal(self, nal: bytes, keyframe: bool, config: bytes) -> None:
        if keyframe:
            self.last_keyframe_payload = config + nal if config else nal
            log.info("VIDEO_KEYFRAME_CACHED device=%s bytes=%s clients=%s", self.device_id, len(self.last_keyframe_payload), len(self.clients))
        for client in list(self.clients.values()):
            before = client.drops
            client.push_frame(nal, keyframe=keyframe, config=config)
            if client.drops != before:
                log.warning("VIDEO_CLIENT_DROP device=%s client=%s user=%s drops=%s", self.device_id, client.id, client.username, client.drops)

    def add_client(self, client: ClientSession) -> None:
        self.clients[client.id] = client
        self.last_client_left_at = None
        log.info("VIDEO_CLIENT_ADD device=%s client=%s user=%s clients=%s", self.device_id, client.id, client.username, len(self.clients))
        self.prime_client(client)

    def prime_client(self, client: ClientSession) -> None:
        if not self.last_keyframe_payload:
            client.clear_queue()
            client.needs_keyframe = True
            log.info("VIDEO_CLIENT_WAIT_KEYFRAME device=%s client=%s user=%s", self.device_id, client.id, client.username)
            return
        client.clear_queue()
        client.needs_keyframe = False
        try:
            client.queue.put_nowait(self.last_keyframe_payload)
            log.info(
                "VIDEO_CLIENT_PRIMED device=%s client=%s user=%s bytes=%s",
                self.device_id,
                client.id,
                client.username,
                len(self.last_keyframe_payload),
            )
        except asyncio.QueueFull:
            client.clear_queue()
            client.drops += 1
            client.needs_keyframe = True

    def remove_client(self, client_id: str) -> None:
        self.clients.pop(client_id, None)
        if self.running and not self.clients:
            self.last_client_left_at = time.monotonic()
        log.info("VIDEO_CLIENT_REMOVE device=%s client=%s clients=%s", self.device_id, client_id, len(self.clients))

    def send_control(self, payload: bytes) -> bool:
        with self._control_send_lock:
            if not self.running or not self.scrcpy:
                self.last_error = "mirror is not running"
                return False
            return bool(self.scrcpy.scrcpy_send_control(payload))


class MirrorManager:
    def __init__(self):
        self.sessions: dict[str, MirrorSession] = {}
        self.events: dict[str, EventClient] = {}
        self._lock = asyncio.Lock()

    async def get_or_create(self, device_id: str) -> MirrorSession:
        async with self._lock:
            if device_id in self.sessions:
                return self.sessions[device_id]
            device = storage.get_device(device_id)
            if not device:
                raise KeyError("unknown device")
            loop = asyncio.get_running_loop()
            session = MirrorSession(device_id, device["address"], loop)
            self.sessions[device_id] = session
            return session

    async def start(self, device_id: str, options: dict[str, Any] | None = None, force_restart: bool = False) -> bool:
        session = await self.get_or_create(device_id)
        ok = await session.start(options, force_restart=force_restart)
        await self.broadcast({"type": "mirror_status", "device_id": device_id, "running": ok, "session": session.snapshot()})
        return ok

    async def stop(self, device_id: str) -> bool:
        session = await self.get_or_create(device_id)
        ok = await session.stop()
        await self.broadcast({"type": "mirror_status", "device_id": device_id, "running": False, "session": session.snapshot()})
        return ok

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

    async def broadcast(self, message: dict[str, Any]) -> None:
        clients = list(self.events.items())
        if not clients:
            return
        real_device_id = str(message.get("device_id") or "").strip()
        public_device_id = ""
        allowed_users: set[str] | None = None
        if real_device_id:
            usernames = {client.username for _, client in clients}
            public_device_id, allowed_users = await asyncio.to_thread(event_public_context, usernames, real_device_id)
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
        except Exception:
            log.exception("EVENT_PERMISSION_CHECK_FAILED user=%s device=%s", username, device_id)
    return allowed


def event_public_context(usernames: set[str], device_id: str) -> tuple[str, set[str]]:
    return storage.public_device_id(device_id), event_users_with_view_access(usernames, device_id)


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
    session = await manager.get_or_create(device_id)
    client = ClientSession(str(uuid.uuid4()), user["username"], websocket)
    session.add_client(client)
    public_id = exposed_device_id or device_id
    await websocket.send_json({"type": "hello", "client_id": client.id, "device_id": public_id, "session": exposed_snapshot(session, public_id)})
    session.prime_client(client)

    async def sender() -> None:
        try:
            while True:
                frame = await client.queue.get()
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
                    session.prime_client(client)
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
    await websocket.send_json({"type": "hello", "client_id": client_id, "device_id": exposed_device_id or device_id, "lock": storage.get_lock(device_id)})
    try:
        while True:
            message = await websocket.receive()
            if message.get("text") is not None:
                await handle_control_text(websocket, user, device_id, client_id, message["text"])
            elif message.get("bytes") is not None:
                await handle_control_bytes(websocket, user, device_id, client_id, message["bytes"])
    except WebSocketDisconnect:
        pass
    finally:
        storage.release_lock(device_id, user["username"], force=False, client_id=client_id)
        await manager.broadcast({"type": "control_lock", "device_id": device_id, "lock": storage.get_lock(device_id)})


async def handle_control_text(websocket: WebSocket, user: dict, device_id: str, client_id: str, text: str):
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        await websocket.send_json({"type": "error", "error": "invalid json"})
        return
    msg_type = data.get("type")
    if msg_type == "acquire_control":
        force = bool(data.get("force") and user.get("role") == "admin")
        result = storage.acquire_lock(device_id, user["username"], client_id, force=force)
        await websocket.send_json({"type": "control_lock", **result, "lock": storage.get_lock(device_id)})
        await manager.broadcast({"type": "control_lock", "device_id": device_id, "lock": storage.get_lock(device_id)})
        return
    if msg_type == "control_keepalive":
        ok = storage.renew_lock(device_id, user["username"], client_id, ttl_seconds=90)
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
        ok = storage.release_lock(device_id, user["username"], force=user.get("role") == "admin", client_id=client_id)
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
        await handle_control_bytes(websocket, user, device_id, client_id, payload)
        return
    if msg_type == "player_reset":
        session = await manager.get_or_create(device_id)
        for client in session.clients.values():
            if client.username == user["username"]:
                session.prime_client(client)
        return
    await websocket.send_json({"type": "error", "error": "unknown control message"})


async def handle_control_bytes(websocket: WebSocket, user: dict, device_id: str, client_id: str, payload: bytes):
    if not payload:
        await websocket.send_json({"type": "error", "error": "invalid control payload"})
        return
    if not storage.renew_lock(device_id, user["username"], client_id, ttl_seconds=90):
        await websocket.send_json({"type": "control_lock", "ok": False, "lock": storage.get_lock(device_id)})
        return
    session = await manager.get_or_create(device_id)
    ok = await asyncio.to_thread(session.send_control, payload)
    if not ok:
        await websocket.send_json({"type": "control_error", "error": session.last_error or "control failed"})
