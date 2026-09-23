"""Video and control WebSocket implementation for the mirror runtime."""

from __future__ import annotations

import asyncio
import base64
import inspect
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from . import i18n, storage
from .booleans import InvalidBooleanValue, parse_bool_strict
from .mirror_manager import exposed_snapshot, manager
from .mirror_runtime import (
    CONTROL_LEASE_VERIFY_INTERVAL,
    CONTROL_PERMISSION_RECHECK_INTERVAL,
    MAX_VIDEO_CONNECTIONS_PER_USER,
    SCRCPY_ALLOWED_CLIENT_CONTROL_TYPES,
    SCRCPY_CLIENT_CLIPBOARD_MAX_BYTES,
    SCRCPY_CLIENT_CONTROL_MAX_BASE64_CHARS,
    SCRCPY_CLIENT_CONTROL_MAX_BYTES,
    SCRCPY_CLIENT_TEXT_MAX_BYTES,
    VIDEO_PERMISSION_RECHECK_SECONDS,
    VIDEO_SEND_TIMEOUT_SECONDS,
    ClientSession,
    ControlAuditCallback,
    StreamNotice,
    StreamReset,
    StreamTermination,
    acquire_control_lock,
    control_lock_epoch,
    release_control_lock,
    renew_control_lock,
)
from .devices import public_lock_payload
from .mirror_record import record_registry
from .runtime import disconnect_stop_delay_seconds

log = logging.getLogger("webscrcpy.mirror")

_video_connection_counts: dict[str, int] = {}


def _reserve_video_connection(username: str) -> bool:
    """Claim one video slot without awaiting, so handshakes cannot race.

    The check and the increment happen in the same synchronous step; every
    ``await`` of a handshake therefore happens after the slot is already owned.
    """
    current = _video_connection_counts.get(username, 0)
    if current >= MAX_VIDEO_CONNECTIONS_PER_USER:
        return False
    _video_connection_counts[username] = current + 1
    return True


def _release_video_connection(username: str) -> None:
    remaining = _video_connection_counts.get(username, 0) - 1
    if remaining > 0:
        _video_connection_counts[username] = remaining
    else:
        _video_connection_counts.pop(username, None)


async def _run_session_check(session_check) -> bool:
    if session_check is None:
        return True
    try:
        result = session_check()
        if inspect.isawaitable(result):
            result = await result
        return bool(result)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("WEBSOCKET_SESSION_CHECK_FAILED")
        return False


def _u16(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset:offset + 2], "big", signed=False)


def _u32(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset:offset + 4], "big", signed=False)


def _validate_keycode_payload(payload: bytes) -> tuple[bool, str]:
    # inject keycode: type + action + keycode + repeat + meta
    if len(payload) != 14:
        return False, "invalid_length"
    if payload[1] not in (0, 1):
        return False, "invalid_action"
    return True, ""


def _validate_text_payload(payload: bytes) -> tuple[bool, str]:
    # inject text: type + u32 length + UTF-8 bytes
    if len(payload) < 5:
        return False, "invalid_length"
    text_length = _u32(payload, 1)
    if text_length > SCRCPY_CLIENT_TEXT_MAX_BYTES:
        return False, "text_too_large"
    if len(payload) != 5 + text_length:
        return False, "invalid_length"
    try:
        payload[5:].decode("utf-8")
    except UnicodeDecodeError:
        return False, "invalid_text"
    return True, ""


def _validate_touch_payload(payload: bytes) -> tuple[bool, str]:
    # inject touch: fixed scrcpy 3.1 message layout
    if len(payload) != 32:
        return False, "invalid_length"
    if payload[1] not in (0, 1, 2):
        return False, "invalid_action"
    if _u16(payload, 18) == 0 or _u16(payload, 20) == 0:
        return False, "invalid_position"
    return True, ""


def _validate_scroll_payload(payload: bytes) -> tuple[bool, str]:
    # inject scroll: fixed scrcpy 3.1 message layout
    if len(payload) != 21:
        return False, "invalid_length"
    if _u16(payload, 9) == 0 or _u16(payload, 11) == 0:
        return False, "invalid_position"
    return True, ""


def _validate_action_payload(payload: bytes) -> tuple[bool, str]:
    # Back/wake action or display power: type + one byte in {0, 1}.
    if len(payload) != 2:
        return False, "invalid_length"
    if payload[1] not in (0, 1):
        return False, "invalid_action"
    return True, ""


def _validate_set_clipboard_payload(payload: bytes) -> tuple[bool, str]:
    # set clipboard: type + u64 sequence + paste flag + u32 length + UTF-8 text.
    # The text carrier for CJK and emoji: the device-side inject-text message
    # cannot resolve them through its key character map.
    if len(payload) < 14:
        return False, "invalid_length"
    if payload[9] not in (0, 1):
        return False, "invalid_paste_flag"
    text_length = _u32(payload, 10)
    if text_length > SCRCPY_CLIENT_CLIPBOARD_MAX_BYTES:
        return False, "text_too_large"
    if len(payload) != 14 + text_length:
        return False, "invalid_length"
    try:
        payload[14:].decode("utf-8")
    except UnicodeDecodeError:
        return False, "invalid_text"
    return True, ""


_CONTROL_PAYLOAD_VALIDATORS = {
    0: _validate_keycode_payload,
    1: _validate_text_payload,
    2: _validate_touch_payload,
    3: _validate_scroll_payload,
    4: _validate_action_payload,
    9: _validate_set_clipboard_payload,
    10: _validate_action_payload,
}


def validate_client_control_payload(payload: bytes) -> tuple[bool, str]:
    """Allow only the scrcpy control messages generated by ScrcpyInput."""
    if not payload:
        return False, "empty"
    if len(payload) > SCRCPY_CLIENT_CONTROL_MAX_BYTES:
        return False, "too_large"
    msg_type = payload[0]
    if msg_type not in SCRCPY_ALLOWED_CLIENT_CONTROL_TYPES:
        return False, "unsupported_type"
    validator = _CONTROL_PAYLOAD_VALIDATORS.get(msg_type)
    return validator(payload) if validator else (False, "unsupported_type")


@dataclass
class ControlLeaseState:
    """Short-lived verification state for a control WebSocket connection."""

    verified_until: float = 0.0
    epoch: int = -1
    permission_verified_until: float = 0.0

    def mark_verified(self, epoch: int) -> None:
        self.epoch = epoch
        self.verified_until = time.monotonic() + CONTROL_LEASE_VERIFY_INTERVAL

    def clear(self) -> None:
        self.epoch = -1
        self.verified_until = 0.0

    def mark_permission_verified(self) -> None:
        self.permission_verified_until = time.monotonic() + CONTROL_PERMISSION_RECHECK_INTERVAL

    def permission_is_verified(self) -> bool:
        return time.monotonic() < self.permission_verified_until

    def is_verified(self, epoch: int) -> bool:
        return self.epoch == epoch and time.monotonic() < self.verified_until


async def _emit_control_audit(
    callback: ControlAuditCallback | None,
    action: str,
    **fields: Any,
) -> None:
    if callback is None:
        return
    try:
        await callback(action, **fields)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("CONTROL_AUDIT_CALLBACK_FAILED action=%s", action)


@dataclass
class _VideoStreamState:
    """Reason shared by the concurrent video sender/receiver tasks."""

    end_reason: str = "disconnect"


def _video_hello_payload(session, client: ClientSession, public_id: str) -> dict:
    legacy = session.effective_stream_mode == "legacy"
    return {
        "type": "hello",
        "client_id": client.id,
        "device_id": public_id,
        "video_transport": "legacy-annexb" if legacy else "raw-v2",
        "video_packet_unit": "nal" if legacy else "access_unit",
        "video_codec": "h264",
        "video_protocol_version": 0 if legacy else 1,
        "session": exposed_snapshot(session, public_id),
    }


async def _video_sender(
    websocket: WebSocket,
    client: ClientSession,
    device_id: str,
    user: dict,
    state: _VideoStreamState,
) -> None:
    try:
        while True:
            frame = await client.get_frame()
            if frame is None:
                state.end_reason = "stream_ended"
                await asyncio.wait_for(
                    websocket.close(code=1011, reason="video stream ended"),
                    timeout=VIDEO_SEND_TIMEOUT_SECONDS,
                )
                return
            if isinstance(frame, StreamReset):
                payload = {"type": "stream_reset", "generation": frame.generation}
                if frame.video is not None:
                    payload["video"] = frame.video
                await asyncio.wait_for(websocket.send_json(payload), timeout=VIDEO_SEND_TIMEOUT_SECONDS)
                continue
            if isinstance(frame, StreamNotice):
                # 控制面通知（投屏记录邀请/上传请求/参与者状态/汇总回传）。
                await asyncio.wait_for(websocket.send_json(frame.payload), timeout=VIDEO_SEND_TIMEOUT_SECONDS)
                continue
            if isinstance(frame, StreamTermination):
                state.end_reason = "stream_terminated"
                await asyncio.wait_for(
                    websocket.close(code=frame.code, reason=frame.reason),
                    timeout=VIDEO_SEND_TIMEOUT_SECONDS,
                )
                return
            await asyncio.wait_for(websocket.send_bytes(frame), timeout=VIDEO_SEND_TIMEOUT_SECONDS)
    except (WebSocketDisconnect, asyncio.CancelledError):
        raise
    except Exception as exc:
        state.end_reason = "error"
        log.warning(
            "VIDEO_CLIENT_SEND_ERROR device=%s client=%s user=%s error=%s",
            device_id,
            client.id,
            user["username"],
            exc,
        )


async def _video_receiver(
    websocket: WebSocket,
    client: ClientSession,
    session,
    device_id: str,
    username: str,
    session_check,
    state: _VideoStreamState,
) -> None:
    # Incoming messages must not extend the authorization recheck deadline.
    next_check = time.monotonic() + VIDEO_PERMISSION_RECHECK_SECONDS
    try:
        while True:
            if time.monotonic() >= next_check:
                if not await _run_session_check(session_check):
                    state.end_reason = "session_revoked"
                    log.info(
                        "VIDEO_CLIENT_SESSION_REVOKED device=%s client=%s user=%s",
                        device_id,
                        client.id,
                        username,
                    )
                    await asyncio.wait_for(
                        websocket.close(code=4403, reason="session revoked"),
                        timeout=VIDEO_SEND_TIMEOUT_SECONDS,
                    )
                    return
                allowed = await asyncio.to_thread(storage.user_can, username, device_id, "view")
                if not allowed:
                    state.end_reason = "permission_revoked"
                    log.info(
                        "VIDEO_CLIENT_PERMISSION_REVOKED device=%s client=%s user=%s",
                        device_id,
                        client.id,
                        username,
                    )
                    await asyncio.wait_for(
                        websocket.close(code=4403, reason="permission revoked"),
                        timeout=VIDEO_SEND_TIMEOUT_SECONDS,
                    )
                    return
                next_check = time.monotonic() + VIDEO_PERMISSION_RECHECK_SECONDS
            try:
                text = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=max(0.001, next_check - time.monotonic()),
                )
            except asyncio.TimeoutError:
                continue
            try:
                msg = json.loads(text)
            except json.JSONDecodeError:
                continue
            if msg.get("type") == "player_reset":
                session.prime_client(client, reason="player_reset")
    except (WebSocketDisconnect, asyncio.CancelledError):
        raise
    except Exception as exc:
        state.end_reason = "error"
        log.warning(
            "VIDEO_CLIENT_RECV_ERROR device=%s client=%s user=%s error=%s",
            device_id,
            client.id,
            username,
            exc,
        )


async def video_socket(
    websocket: WebSocket,
    user: dict,
    device_id: str,
    exposed_device_id: str | None = None,
    session_check=None,
):
    if not await _run_session_check(session_check):
        await websocket.close(code=4403, reason="session revoked")
        return
    if not await asyncio.to_thread(storage.user_can, user["username"], device_id, "view"):
        await websocket.close(code=4403)
        return
    username = user["username"]
    if not _reserve_video_connection(username):
        log.warning(
            "VIDEO_CLIENT_LIMIT_REACHED user=%s device=%s limit=%d",
            username,
            device_id,
            MAX_VIDEO_CONNECTIONS_PER_USER,
        )
        await websocket.close(code=4429, reason="too many video connections")
        return
    established = False
    try:
        await websocket.accept()
        try:
            session = await manager.get_or_create(device_id)
        except KeyError:
            await websocket.close(code=4403)
            return
        except Exception as exc:
            log.warning(
                "VIDEO_SESSION_INIT_FAILED device=%s user=%s error=%s",
                device_id,
                username,
                type(exc).__name__,
            )
            await websocket.close(code=4403)
            return
        query_params = getattr(websocket, "query_params", {})
        reservation_token = str(query_params.get("viewer_token") or "").strip()
        browser_id = str(query_params.get("browser_id") or "").strip()[:64]
        grid_view = str(query_params.get("view") or "").strip().lower() == "grid"
        client = ClientSession(
            str(uuid.uuid4()), user["username"], websocket, browser_id=browser_id, grid_view=grid_view
        )
        if not session.attach_client(client, reservation_token):
            await websocket.close(code=4403 if reservation_token else 1013)
            return
        established = True
    finally:
        if not established:
            _release_video_connection(username)
    watch_session_id: str | None = None
    stream_state = _VideoStreamState()
    try:
        watch_session_id = await asyncio.to_thread(
            storage.start_viewer_watch,
            username,
            device_id,
            started_at_ms=time.time_ns() // 1_000_000,
        )
    except Exception as exc:
        # History is observability only; a storage hiccup must never tear down
        # an already-authorized video connection.
        log.warning(
            "VIDEO_WATCH_START_FAILED device=%s user=%s error=%s",
            device_id,
            username,
            type(exc).__name__,
        )
    if reservation_token:
        manager.cancel_reservation_expiry(device_id, reservation_token)
    manager.cancel_disconnect_stop(device_id)
    try:
        public_id = exposed_device_id or device_id
        await websocket.send_json(_video_hello_payload(session, client, public_id))
        # 多端投屏记录：晚一步接入的观看端在这里补一条邀请，发起端重连后在这里拿回主导权
        # （前端把「本机会话 id + 角色」存在 localStorage，重连时作为 claim 带上来）。
        try:
            record_registry.attach_client(device_id, client.id, username)
        except Exception:  # noqa: BLE001 - 记录协同失败不能影响观看
            log.exception("MIRROR_RECORD_ATTACH_FAILED device=%s client=%s", device_id, client.id)
        sender_task = asyncio.ensure_future(_video_sender(websocket, client, device_id, user, stream_state))
        receiver_task = asyncio.ensure_future(
            _video_receiver(websocket, client, session, device_id, username, session_check, stream_state)
        )
        try:
            await manager.broadcast(
                {
                    "type": "mirror_status",
                    "device_id": device_id,
                    "running": True,
                    "session": session.snapshot(),
                    "reason": "viewer_joined",
                }
            )
        except Exception as exc:
            log.warning("VIDEO_CLIENT_JOIN_BROADCAST_FAILED device=%s client=%s error=%s", device_id, client.id, exc)
        done, pending = await asyncio.wait([sender_task, receiver_task], return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            try:
                task.result()
            except WebSocketDisconnect:
                stream_state.end_reason = "client_disconnect"
            except asyncio.CancelledError:
                stream_state.end_reason = "cancelled"
            except Exception as exc:
                log.warning(
                    "VIDEO_CLIENT_TASK_ERROR device=%s client=%s user=%s error=%s",
                    device_id,
                    client.id,
                    user["username"],
                    exc,
                )
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
    except WebSocketDisconnect:
        stream_state.end_reason = "client_disconnect"
    except Exception as exc:
        stream_state.end_reason = "error"
        log.warning(
            "VIDEO_CLIENT_HANDSHAKE_ERROR device=%s client=%s user=%s error=%s",
            device_id,
            client.id,
            user["username"],
            exc,
        )
    finally:
        if watch_session_id:
            try:
                await asyncio.to_thread(
                    storage.finish_viewer_watch,
                    watch_session_id,
                    end_reason=stream_state.end_reason,
                )
            except Exception as exc:
                log.warning(
                    "VIDEO_WATCH_FINISH_FAILED device=%s user=%s error=%s",
                    device_id,
                    username,
                    type(exc).__name__,
                )
        _release_video_connection(username)
        session.remove_client(client.id)
        # 多端投屏记录：把这个观看端标成已离开，并告知发起端（不落盘，仅内存中继）。
        try:
            record_registry.note_client_left(device_id, client.id)
        except Exception:  # noqa: BLE001 - 记录协同失败不能影响观看端清理
            log.exception("MIRROR_RECORD_NOTE_LEFT_FAILED device=%s client=%s", device_id, client.id)
        # 无观看端自动停止时间由设置决定；0 时只保留重连宽限。
        disconnect_delay = await asyncio.to_thread(disconnect_stop_delay_seconds)
        manager.schedule_disconnect_stop(device_id, session, delay_seconds=disconnect_delay)
        try:
            await manager.broadcast(
                {
                    "type": "mirror_status",
                    "device_id": device_id,
                    "running": session.running,
                    "session": session.snapshot(),
                    "reason": "viewer_left",
                }
            )
        except Exception as exc:
            log.warning("VIDEO_CLIENT_LEAVE_BROADCAST_FAILED device=%s client=%s error=%s", device_id, client.id, exc)


async def control_socket(
    websocket: WebSocket,
    user: dict,
    device_id: str,
    exposed_device_id: str | None = None,
    audit_callback: ControlAuditCallback | None = None,
    session_check=None,
):
    if not await _run_session_check(session_check):
        await websocket.close(code=4403, reason="session revoked")
        return
    if not await asyncio.to_thread(storage.user_can, user["username"], device_id, "control"):
        await _emit_control_audit(
            audit_callback,
            "control_acquire",
            outcome="denied",
            reason="device_permission_denied",
            severity="warning",
            metadata={"channel": "websocket"},
        )
        await websocket.close(code=4403)
        return
    await websocket.accept()
    client_id = str(uuid.uuid4())
    lease = ControlLeaseState()
    hello_lock = public_lock_payload(
        await asyncio.to_thread(storage.get_lock, device_id),
        exposed_device_id or device_id,
    )
    await websocket.send_json(
        {
            "type": "hello",
            "client_id": client_id,
            "device_id": exposed_device_id or device_id,
            "lock": hello_lock,
        }
    )
    try:
        session_verified_until = 0.0
        while True:
            # Do not let an idle control connection bypass revocation checks.
            # A receive timeout gives the session and permission checks a
            # bounded upper latency even when the browser sends no input.
            try:
                message = await asyncio.wait_for(
                    websocket.receive(),
                    timeout=CONTROL_PERMISSION_RECHECK_INTERVAL,
                )
            except asyncio.TimeoutError:
                message = None
            if message is not None and message.get("type") == "websocket.disconnect":
                break
            if time.monotonic() >= session_verified_until:
                if not await _run_session_check(session_check):
                    await _emit_control_audit(
                        audit_callback,
                        "control_release",
                        outcome="denied",
                        reason="session_revoked",
                        severity="warning",
                        metadata={"channel": "websocket"},
                    )
                    await websocket.close(code=4403, reason="session revoked")
                    return
                session_verified_until = time.monotonic() + CONTROL_PERMISSION_RECHECK_INTERVAL
            if not lease.permission_is_verified():
                if not await asyncio.to_thread(storage.user_can, user["username"], device_id, "control"):
                    await _emit_control_audit(
                        audit_callback,
                        "control_release",
                        outcome="denied",
                        reason="permission_revoked",
                        severity="warning",
                        metadata={"channel": "websocket"},
                    )
                    await websocket.close(code=4403)
                    return
                lease.mark_permission_verified()
            if message is None:
                continue
            if message.get("text") is not None:
                await handle_control_text(
                    websocket,
                    user,
                    device_id,
                    client_id,
                    message["text"],
                    lease,
                    audit_callback=audit_callback,
                    exposed_device_id=exposed_device_id,
                )
            elif message.get("bytes") is not None:
                await handle_control_bytes(
                    websocket,
                    user,
                    device_id,
                    client_id,
                    message["bytes"],
                    lease,
                    exposed_device_id=exposed_device_id,
                )
    except WebSocketDisconnect:
        pass
    except KeyError:
        await websocket.close(code=4403)
    finally:
        released = release_control_lock(
            device_id,
            user["username"],
            force=False,
            client_id=client_id,
            only_if_owned=True,
        )
        if released:
            await _emit_control_audit(
                audit_callback,
                "control_release",
                outcome="success",
                reason="connection_closed",
                severity="info",
                metadata={"channel": "websocket", "automatic": True},
            )
        closed_lock = await asyncio.to_thread(storage.get_lock, device_id)
        await manager.broadcast({"type": "control_lock", "device_id": device_id, "lock": closed_lock})


async def handle_control_text(
    websocket: WebSocket,
    user: dict,
    device_id: str,
    client_id: str,
    text: str,
    lease: ControlLeaseState | None = None,
    audit_callback: ControlAuditCallback | None = None,
    exposed_device_id: str | None = None,
):
    public_device_id = exposed_device_id or device_id
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        await websocket.send_json({"type": "error", "error": "invalid json"})
        return
    if not isinstance(data, dict):
        await websocket.send_json({"type": "error", "error": "invalid json"})
        return
    msg_type = data.get("type")
    if msg_type in {"acquire_control", "takeover_control"}:
        takeover = msg_type == "takeover_control"
        try:
            force = takeover or (
                user.get("role") == "admin"
                and "force" in data
                and parse_bool_strict(data.get("force"))
            )
        except InvalidBooleanValue:
            await websocket.send_json({"type": "error", "error": "invalid control message"})
            return
        result, epoch = acquire_control_lock(device_id, user["username"], client_id, force=force)
        await _emit_control_audit(
            audit_callback,
            "control_takeover" if takeover else "control_acquire",
            outcome="success" if result.get("ok") else "denied",
            reason="" if result.get("ok") else "control_occupied",
            severity="info" if result.get("ok") else "warning",
            metadata={"channel": "websocket", "force": force, "takeover": takeover},
        )
        if lease:
            if result.get("ok"):
                lease.mark_verified(epoch)
            else:
                lease.clear()
        acquired_lock = public_lock_payload(
            await asyncio.to_thread(storage.get_lock, device_id),
            public_device_id,
        )
        await websocket.send_json(
            {
                "type": "control_lock",
                **result,
                "lock": acquired_lock,
            }
        )
        await manager.broadcast({"type": "control_lock", "device_id": device_id, "lock": acquired_lock})
        return
    if msg_type == "control_keepalive":
        ok, epoch = renew_control_lock(device_id, user["username"], client_id, ttl_seconds=90)
        if lease:
            if ok:
                lease.mark_verified(epoch)
            else:
                lease.clear()
        lock = public_lock_payload(await asyncio.to_thread(storage.get_lock, device_id), public_device_id)
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
        await _emit_control_audit(
            audit_callback,
            "control_release",
            outcome="success" if ok else "denied",
            reason="" if ok else "not_lock_owner",
            severity="info" if ok else "warning",
            metadata={"channel": "websocket", "force": user.get("role") == "admin"},
        )
        if lease:
            lease.clear()
        await websocket.send_json({"type": "control_released", "ok": ok})
        released_lock = await asyncio.to_thread(storage.get_lock, device_id)
        await manager.broadcast({"type": "control_lock", "device_id": device_id, "lock": released_lock})
        return
    if msg_type == "control_base64":
        encoded = data.get("data")
        if not isinstance(encoded, str) or not encoded or len(encoded) > SCRCPY_CLIENT_CONTROL_MAX_BASE64_CHARS:
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
        await handle_control_bytes(
            websocket,
            user,
            device_id,
            client_id,
            payload,
            lease,
            exposed_device_id=exposed_device_id,
        )
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
    exposed_device_id: str | None = None,
):
    public_device_id = exposed_device_id or device_id
    if not payload:
        await websocket.send_json({"type": "error", "error": "invalid control payload"})
        return
    valid_payload, invalid_reason = validate_client_control_payload(payload)
    if not valid_payload:
        log.debug(
            "CONTROL_PAYLOAD_REJECTED device=%s user=%s reason=%s type=%s bytes=%s",
            device_id,
            user.get("username"),
            invalid_reason,
            payload[0] if payload else None,
            len(payload),
        )
        await websocket.send_json({"type": "error", "error": "invalid control payload"})
        return
    epoch = control_lock_epoch(device_id)
    verified = bool(lease and lease.is_verified(epoch))
    if not verified:
        verified, epoch = await asyncio.to_thread(
            renew_control_lock,
            device_id,
            user["username"],
            client_id,
            ttl_seconds=90,
        )
        if lease:
            if verified:
                lease.mark_verified(epoch)
            else:
                lease.clear()
    if not verified:
        lock = await asyncio.to_thread(storage.get_lock, device_id)
        await websocket.send_json(
            {
                "type": "control_lock",
                "ok": False,
                "lock": public_lock_payload(lock, public_device_id),
            }
        )
        return
    session = await manager.get_or_create(device_id)
    ok = await asyncio.to_thread(session.send_control_for_epoch, payload, epoch)
    if not ok:
        if control_lock_epoch(device_id) != epoch:
            lock = await asyncio.to_thread(storage.get_lock, device_id)
            await websocket.send_json(
                {
                    "type": "control_lock",
                    "ok": False,
                    "lock": public_lock_payload(lock, public_device_id),
                }
            )
            return
        log.warning(
            "CONTROL_SEND_FAILED device=%s user=%s error=%s",
            device_id,
            user.get("username"),
            session.last_error,
        )
        await websocket.send_json(
            {
                "type": "control_error",
                "error": i18n.translate("server.error.request_failed"),
            }
        )
