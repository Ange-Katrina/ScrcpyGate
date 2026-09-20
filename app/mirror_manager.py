"""Mirror session manager and event broadcast runtime."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import math
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from . import storage
from .devices import public_adb_payload, public_lock_payload, session_payload, sessions_payload
from .mirror_runtime import (
    VIEWER_DISCONNECT_GRACE,
    VIEWER_RESERVATION_TTL,
    EventClient,
    MirrorSession,
    release_control_lock,
)
from .video_options import public_video_options, settings_to_video_options

log = logging.getLogger("webscrcpy.mirror")

def _event_session_recheck_seconds() -> float:
    """Parse the event-session check interval without making startup fragile."""
    raw_value = os.environ.get("EVENT_SESSION_RECHECK_SECONDS", "10")
    try:
        value = float(raw_value or "10")
    except (TypeError, ValueError):
        return 10.0
    if not math.isfinite(value):
        return 10.0
    return min(60.0, max(2.0, value))


EVENT_SESSION_RECHECK_SECONDS = _event_session_recheck_seconds()

# 设备转屏触发的采集重启：同一台设备在这段时间内只重启一次，避免来回抖动。
ROTATION_RESTART_COOLDOWN = 8.0

class MirrorManager:
    def __init__(self):
        self.sessions: dict[str, MirrorSession] = {}
        self.events: dict[str, EventClient] = {}
        self._lock = asyncio.Lock()
        self._target_locks: dict[str, asyncio.Lock] = {}
        self._broadcast_locks: dict[str | None, asyncio.Lock] = {}
        self._disconnect_stop_tasks: dict[str, asyncio.Task] = {}
        self._reservation_expiry_tasks: dict[tuple[str, str], asyncio.Task] = {}
        self._last_rotation_restart: dict[str, float] = {}

    @staticmethod
    def _target_key(session: MirrorSession) -> str:
        address = getattr(session, "address", "")
        device_id = getattr(session, "device_id", "")
        return str(address or device_id or f"session:{id(session)}").strip().lower()

    @staticmethod
    def _transport_active(session: MirrorSession) -> bool:
        cleanup = getattr(session, "_transport_cleanup_task", None)
        return bool(getattr(session, "running", False) or (cleanup and not cleanup.done()))

    @asynccontextmanager
    async def target_guard(self, session: MirrorSession):
        while True:
            target_key = self._target_key(session)
            async with self._lock:
                target_lock = self._target_locks.setdefault(target_key, asyncio.Lock())
            await target_lock.acquire()
            if self._target_key(session) == target_key:
                break
            target_lock.release()
        try:
            yield target_key
        finally:
            target_lock.release()

    def cancel_reservation_expiry(self, device_id: str, token: str) -> None:
        key = (device_id, str(token or "").strip())
        task = self._reservation_expiry_tasks.pop(key, None)
        if task and not task.done():
            task.cancel()

    def cancel_device_reservation_expiries(self, device_id: str) -> None:
        keys = [key for key in self._reservation_expiry_tasks if key[0] == device_id]
        for key in keys:
            self.cancel_reservation_expiry(*key)

    def schedule_reservation_expiry(
        self,
        device_id: str,
        session: MirrorSession,
        token: str,
        username: str,
        delay_seconds: float = VIEWER_RESERVATION_TTL,
    ) -> asyncio.Task:
        normalized_token = str(token or "").strip()
        key = (device_id, normalized_token)
        self.cancel_reservation_expiry(*key)

        async def expire_later() -> bool:
            try:
                await asyncio.sleep(max(0, delay_seconds))
                async with self._lock:
                    current = self.sessions.get(device_id)
                if current is not None and current is not session:
                    return False
                session.release_viewer_reservation(normalized_token, username)
                async with self.target_guard(session):
                    ok = await session.stop_if_no_clients()
                if ok:
                    try:
                        await self.broadcast(
                            {
                                "type": "mirror_status",
                                "device_id": device_id,
                                "running": False,
                                "session": session.snapshot(),
                                "reason": "viewer_reservation_expired",
                            }
                        )
                    except Exception:
                        log.exception("MIRROR_RESERVATION_EXPIRY_BROADCAST_FAILED device=%s", device_id)
                return ok
            except asyncio.CancelledError:
                return False
            except Exception:
                log.exception("MIRROR_RESERVATION_EXPIRY_FAILED device=%s", device_id)
                return False
            finally:
                if self._reservation_expiry_tasks.get(key) is task:
                    self._reservation_expiry_tasks.pop(key, None)

        task = asyncio.create_task(expire_later())
        self._reservation_expiry_tasks[key] = task
        return task

    def cancel_disconnect_stop(self, device_id: str) -> None:
        task = self._disconnect_stop_tasks.pop(device_id, None)
        if task and not task.done():
            task.cancel()

    def schedule_disconnect_stop(
        self,
        device_id: str,
        session: MirrorSession,
        delay_seconds: float = VIEWER_DISCONNECT_GRACE,
    ) -> asyncio.Task:
        self.cancel_disconnect_stop(device_id)

        async def stop_later() -> bool:
            try:
                await asyncio.sleep(max(0, delay_seconds))
                async with self._lock:
                    current = self.sessions.get(device_id)
                if current is not None and current is not session:
                    return False
                async with self.target_guard(session):
                    ok = await session.stop_if_no_clients()
                if ok:
                    try:
                        await self.broadcast(
                            {
                                "type": "mirror_status",
                                "device_id": device_id,
                                "running": False,
                                "session": session.snapshot(),
                                "reason": "viewer_disconnected",
                            }
                        )
                    except Exception:
                        log.exception("MIRROR_DISCONNECT_STOP_BROADCAST_FAILED device=%s", device_id)
                return ok
            except asyncio.CancelledError:
                return False
            except Exception:
                log.exception("MIRROR_DISCONNECT_STOP_FAILED device=%s", device_id)
                return False
            finally:
                if self._disconnect_stop_tasks.get(device_id) is task:
                    self._disconnect_stop_tasks.pop(device_id, None)

        task = asyncio.create_task(stop_later())
        self._disconnect_stop_tasks[device_id] = task
        return task

    async def get_or_create(self, device_id: str) -> MirrorSession:
        async with self._lock:
            cached = self.sessions.get(device_id)
            if cached and cached.available:
                return cached
            device = await asyncio.to_thread(storage.get_device, device_id)
            if not device or not bool(device["enabled"]):
                raise KeyError("unknown or disabled device")
            if cached:
                self.sessions.pop(device_id, None)
            loop = asyncio.get_running_loop()
            session = MirrorSession(device_id, device["address"], loop)
            self.sessions[device_id] = session
            return session

    async def reserve_viewer(self, device_id: str, username: str) -> str:
        try:
            session = await self.get_or_create(device_id)
        except KeyError:
            return ""
        return session.reserve_viewer(username)

    async def release_viewer_reservation(self, device_id: str, token: str, username: str = "") -> bool:
        self.cancel_reservation_expiry(device_id, token)
        async with self._lock:
            session = self.sessions.get(device_id)
        if session is None:
            return False
        return session.release_viewer_reservation(token, username)

    async def refresh_viewer_reservation(self, device_id: str, token: str, username: str) -> bool:
        async with self._lock:
            session = self.sessions.get(device_id)
        if session is None:
            return False
        refreshed = session.refresh_viewer_reservation(token, username)
        if refreshed:
            self.schedule_reservation_expiry(device_id, session, token, username)
        return refreshed

    async def start(
        self,
        device_id: str,
        options: dict[str, Any] | None = None,
        force_restart: bool = False,
        reservation_token: str | None = None,
    ) -> bool:
        try:
            session = await self.get_or_create(device_id)
        except KeyError:
            return False
        async with self.target_guard(session) as target_key:
            async with self._lock:
                target_in_use = any(
                    other_device_id != device_id
                    and self._transport_active(other)
                    and self._target_key(other) == target_key
                    for other_device_id, other in self.sessions.items()
                )
            if target_in_use:
                session.last_error = "ADB target is already streaming as another device"
                log.warning("MIRROR_TARGET_BUSY device=%s target=%s", device_id, target_key)
                ok = False
            else:
                ok = await session.start(options, force_restart=force_restart, reservation_token=reservation_token)
        await self.broadcast({"type": "mirror_status", "device_id": device_id, "running": ok, "session": session.snapshot()})
        return ok

    async def apply_video_options(
        self,
        device_id: str,
        options: dict[str, Any],
        client_id: str,
        username: str,
    ) -> dict[str, Any]:
        try:
            session = await self.get_or_create(device_id)
        except KeyError:
            return {
                "ok": False,
                "running": False,
                "restart_required": False,
                "restarted": False,
                "deferred": False,
                "viewer_count": 0,
                "effective": public_video_options(options),
            }
        async with self.target_guard(session):
            result = await session.apply_video_options(options, client_id, username)
        if result["restart_required"] and not result["deferred"]:
            reason = "quality_changed" if result["restarted"] else "quality_change_failed"
            if not result["running"]:
                self._terminate_video_clients(session, device_id, reason)
            await self.broadcast(
                {
                    "type": "mirror_status",
                    "device_id": device_id,
                    "running": result["running"],
                    "session": session.snapshot(),
                    "reason": reason,
                }
            )
        return result

    async def restart_for_display_rotation(self, device_id: str) -> dict[str, Any]:
        """Explicit compatibility fallback; normal scrcpy rotation needs no restart.

        Existing viewers keep their slots and are re-primed as for a quality restart.
        Only the opt-in ADB rotation monitor calls this workaround.
        """
        now = time.monotonic()
        if now - self._last_rotation_restart.get(device_id, 0.0) < ROTATION_RESTART_COOLDOWN:
            return {"ok": False, "reason": "cooldown"}
        async with self._lock:
            session = self.sessions.get(device_id)
        if session is None or not session.running:
            return {"ok": False, "reason": "not_running"}
        with session._clients_lock:
            session._purge_viewer_reservations_locked()
            viewer_count = len(session.clients) + len(session._viewer_reservations)
        if viewer_count <= 0:
            return {"ok": False, "reason": "no_viewers"}
        options = dict(session.video_options or {})
        if not options:
            return {"ok": False, "reason": "no_options"}
        self._last_rotation_restart[device_id] = now
        async with self.target_guard(session):
            async with session._lock:
                ok = await session._start_locked(options, force_restart=True)
        await self.broadcast(
            {
                "type": "mirror_status",
                "device_id": device_id,
                "running": session.running,
                "session": session.snapshot(),
                "reason": "display_rotation" if ok else "display_rotation_failed",
            }
        )
        return {"ok": bool(ok), "viewer_count": viewer_count, "error": session.last_error if not ok else ""}

    async def restart_streams_safely(self, options: dict[str, Any] | None = None) -> dict[str, list[dict[str, Any]]]:
        """Apply global options without disrupting streams shared by multiple viewers."""
        if not options:
            stored = await asyncio.to_thread(storage.get_settings)
            options = settings_to_video_options(stored)
        target_options = public_video_options(options)
        result: dict[str, list[dict[str, Any]]] = {"restarted": [], "deferred": [], "failed": []}
        async with self._lock:
            sessions = list(self.sessions.items())
        for device_id, session in sessions:
            if not session.running:
                continue
            async with self.target_guard(session):
                async with session._lock:
                    with session._clients_lock:
                        session._purge_viewer_reservations_locked()
                        viewer_count = len(session.clients) + len(session._viewer_reservations)
                    row = {"device_id": device_id, "viewer_count": viewer_count}
                    if viewer_count > 1:
                        result["deferred"].append(row)
                        continue
                    ok = await session._start_locked(target_options, force_restart=True)
                    row["stream_mode"] = session.effective_stream_mode
                    if ok:
                        result["restarted"].append(row)
                    else:
                        row["error"] = session.last_error
                        result["failed"].append(row)
            await self.broadcast(
                {
                    "type": "mirror_status",
                    "device_id": device_id,
                    "running": session.running,
                    "session": session.snapshot(),
                    "reason": "admin_quality_restart" if session.running else "quality_change_failed",
                }
            )
        return result

    async def stop(self, device_id: str) -> bool:
        self.cancel_disconnect_stop(device_id)
        self.cancel_device_reservation_expiries(device_id)
        session = await self.get_or_create(device_id)
        async with self.target_guard(session):
            ok = await session.stop()
        await self.broadcast({"type": "mirror_status", "device_id": device_id, "running": False, "session": session.snapshot()})
        return ok

    async def admin_client_snapshot(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for device_id, session in list(self.sessions.items()):
            for client in session.client_snapshots():
                rows.append({"device_id": device_id, **client})
        return rows

    async def disconnect_client(self, device_id: str, client_id: str) -> dict[str, Any] | None:
        async with self._lock:
            session = self.sessions.get(device_id)
        if session is None:
            return None
        with session._clients_lock:
            client = session.clients.get(client_id)
            if client is None:
                return None
            payload = {
                "client_id": client.id,
                "username": client.username,
                "connected_at": client.connected_at,
            }
            client.terminate(4411, "viewer disconnected by administrator")
        release_control_lock(device_id, client.username, force=True)
        return payload

    async def disconnect_client_for_user(
        self,
        device_id: str,
        client_id: str,
        username: str,
        viewer_token: str = "",
    ) -> dict[str, Any] | None:
        """Terminate only the caller's viewer connection."""
        async with self._lock:
            session = self.sessions.get(device_id)
        if session is None:
            return None
        normalized_client_id = str(client_id or "").strip()
        token = str(viewer_token or "").strip()
        client = None
        with session._clients_lock:
            if normalized_client_id:
                client = session.clients.get(normalized_client_id)
            elif token:
                client = next(
                    (
                        candidate
                        for candidate in session.clients.values()
                        if candidate.username == username and candidate.viewer_token == token
                    ),
                    None,
                )
            if client is not None and client.username != username:
                return None
            if client is not None and token and client.viewer_token and token != client.viewer_token:
                return None
            if client is not None:
                payload = {
                    "client_id": client.id,
                    "username": client.username,
                    "connected_at": client.connected_at,
                }
                client.terminate(4412, "viewer stopped")
            else:
                payload = None
        if payload is not None:
            release_control_lock(device_id, username, force=False, client_id=payload["client_id"])
            return payload
        if not token:
            return None
        self.cancel_reservation_expiry(device_id, token)
        if not session.release_viewer_reservation(token, username):
            return None
        async with self.target_guard(session):
            stopped = await session.stop_if_no_clients()
        if stopped:
            await self.broadcast(
                {
                    "type": "mirror_status",
                    "device_id": device_id,
                    "running": False,
                    "session": session.snapshot(),
                    "reason": "viewer_cancelled",
                }
            )
        return {"client_id": "", "username": username, "connected_at": None, "reservation": True}

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
        self.cancel_disconnect_stop(device_id)
        self.cancel_device_reservation_expiries(device_id)
        async with self._lock:
            session = self.sessions.get(device_id)
        if not session:
            return False
        async with self.target_guard(session):
            return await self._reconfigure_device_guarded(session, device_id, address)

    async def _reconfigure_device_guarded(self, session: MirrorSession, device_id: str, address: str) -> bool:
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
        self.cancel_disconnect_stop(device_id)
        self.cancel_device_reservation_expiries(device_id)
        async with self._lock:
            session = self.sessions.pop(device_id, None)
            if session:
                session.available = False
        if session:
            async with self.target_guard(session):
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

    async def stop_if_no_clients(
        self,
        device_id: str,
        wait_seconds: float = 0,
        departing_client_id: str = "",
        departing_username: str = "",
        departing_viewer_token: str = "",
    ) -> bool:
        session = await self.get_or_create(device_id)
        async with self.target_guard(session):
            return await self._stop_if_no_clients_guarded(
                session,
                device_id,
                wait_seconds,
                departing_client_id,
                departing_username,
                departing_viewer_token,
            )

    async def _stop_if_no_clients_guarded(
        self,
        session: MirrorSession,
        device_id: str,
        wait_seconds: float,
        departing_client_id: str,
        departing_username: str,
        departing_viewer_token: str,
    ) -> bool:
        reservation_token = str(departing_viewer_token or "").strip()
        if reservation_token:
            session.release_viewer_reservation(reservation_token, departing_username)
        client_id = str(departing_client_id or "").strip()
        if hasattr(session, "resolve_departing_client"):
            validated_client_id, conflicting_client = session.resolve_departing_client(
                client_id,
                departing_username,
                reservation_token,
            )
        else:
            validated_client_id = ""
            conflicting_client = False
            if client_id:
                departing = session.clients.get(client_id)
                if departing is not None and departing.username == departing_username:
                    validated_client_id = client_id
                else:
                    conflicting_client = bool(session.clients)
            elif reservation_token:
                for candidate_id, candidate in session.clients.items():
                    if (
                        getattr(candidate, "username", "") == departing_username
                        and getattr(candidate, "viewer_token", "") == reservation_token
                    ):
                        validated_client_id = candidate_id
                        break
                conflicting_client = not validated_client_id and bool(session.clients)
        if conflicting_client:
            return False

        deadline = time.monotonic() + max(0, wait_seconds)
        while session.running and time.monotonic() < deadline:
            if validated_client_id:
                attached = session.has_client(validated_client_id) if hasattr(session, "has_client") else validated_client_id in session.clients
                if not attached:
                    break
            elif not (session.has_clients() if hasattr(session, "has_clients") else session.clients):
                break
            await asyncio.sleep(0.1)
        if validated_client_id and (session.has_client(validated_client_id) if hasattr(session, "has_client") else validated_client_id in session.clients):
            return False
        ok = await session.stop_if_no_clients()
        if not ok:
            return False
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
                async with self.target_guard(session):
                    ok = await session.stop_if_no_clients()
                if ok:
                    stopped.append(device_id)
                    await self.broadcast({"type": "mirror_status", "device_id": device_id, "running": False, "session": session.snapshot(), "reason": "idle"})
        return stopped

    async def stop_expired_sessions(self, max_seconds: int) -> list[str]:
        """强制停止运行时长超过上限的会话（连续投屏时间上限，0/负数禁用）。"""
        if max_seconds <= 0:
            return []
        now = time.monotonic()
        stopped: list[str] = []
        for device_id, session in list(self.sessions.items()):
            if not session.running or session.started_at is None:
                continue
            if now - session.started_at < max_seconds:
                continue
            async with self.target_guard(session):
                ok = await session.stop()
            if ok:
                stopped.append(device_id)
                await self.broadcast(
                    {
                        "type": "mirror_status",
                        "device_id": device_id,
                        "running": session.running,
                        "session": session.snapshot(),
                        "reason": "session_limit",
                    }
                )
        return stopped

    async def snapshot(self) -> dict[str, Any]:
        return {device_id: session.snapshot() for device_id, session in self.sessions.items()}

    async def stop_all(self) -> None:
        disconnect_tasks = list(self._disconnect_stop_tasks.values())
        self._disconnect_stop_tasks.clear()
        reservation_tasks = list(self._reservation_expiry_tasks.values())
        self._reservation_expiry_tasks.clear()
        background_tasks = disconnect_tasks + reservation_tasks
        for task in background_tasks:
            task.cancel()
        if background_tasks:
            await asyncio.gather(*background_tasks, return_exceptions=True)
        sessions = list(self.sessions.items())
        if not sessions:
            return
        results = await asyncio.gather(*(session.stop() for _, session in sessions), return_exceptions=True)
        for (device_id, _), result in zip(sessions, results):
            if isinstance(result, Exception):
                log.warning("MIRROR_SHUTDOWN_STOP_FAILED device=%s error=%s", device_id, result)

    async def broadcast(self, message: dict[str, Any], allowed_users: set[str] | None = None) -> None:
        real_device_id = str(message.get("device_id") or "").strip()
        broadcast_key = real_device_id or None
        broadcast_lock = self._broadcast_locks.setdefault(broadcast_key, asyncio.Lock())
        async with broadcast_lock:
            clients = list(self.events.items())
            if not clients:
                return
            public_device_id = ""
            if real_device_id:
                public_device_id = storage.public_device_id(real_device_id)
                if allowed_users is None:
                    usernames = {client.username for _, client in clients}
                    allowed_users = await asyncio.to_thread(event_users_with_view_access, usernames, real_device_id)
            device_encoded = (
                json.dumps(public_event_payload(message, real_device_id, public_device_id), ensure_ascii=False)
                if real_device_id
                else ""
            )
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

    def event_connection_count(self, username: str) -> int:
        """Open ``/ws/events`` connections for one user (page presence)."""
        return sum(1 for client in self.events.values() if client.username == username)

    async def register_event_ws(self, websocket: WebSocket, username: str, session_check=None) -> None:
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
                try:
                    await asyncio.wait_for(
                        websocket.receive_text(),
                        timeout=EVENT_SESSION_RECHECK_SECONDS,
                    )
                except asyncio.TimeoutError:
                    if session_check is None:
                        continue
                    try:
                        result = session_check()
                        if inspect.isawaitable(result):
                            result = await result
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        log.exception(
                            "EVENT_WEBSOCKET_SESSION_CHECK_FAILED user=%s client=%s",
                            username,
                            client_id,
                        )
                        result = False
                    if not result:
                        await websocket.close(code=4403, reason="session revoked")
                        return
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
