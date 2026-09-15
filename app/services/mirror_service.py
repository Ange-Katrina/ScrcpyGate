"""Shared HTTP-facing helpers for mirror and control routes."""

from __future__ import annotations

import asyncio

from fastapi import HTTPException

from .. import i18n, storage
from ..devices import public_adb_payload, session_payload, sessions_payload
from ..mirror_manager import manager

__all__ = [
    "public_mirror_failure",
    "public_sessions_for_user",
    "resolve_device_or_404",
]


def resolve_device_or_404(device_ref: str) -> str:
    device_id = storage.resolve_device_ref(device_ref)
    if not device_id:
        raise HTTPException(status_code=404, detail=i18n.translate("server.error.device_not_found"))
    return device_id


async def public_sessions_for_user(user: dict) -> dict:
    devices = await asyncio.to_thread(
        storage.list_devices_for_user, user["username"], user["role"] == "admin"
    )
    sessions = await manager.snapshot()
    return sessions_payload(devices, sessions, public_id=True)


def public_mirror_failure(device_id: str, session: dict, adb_status: dict, default_error: str) -> dict:
    exposed_id = storage.public_device_id(device_id)
    safe_session = session_payload(session, device_id, exposed_id, public=True) or {}
    safe_adb = public_adb_payload(adb_status, exposed_id) or {}
    return {
        "error": safe_session.get("last_error") or default_error,
        "detail": safe_adb.get("detail") or safe_session.get("last_error") or "",
    }
