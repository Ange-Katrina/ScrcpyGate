"""Compatibility facade for the mirror runtime.

The implementation is owned by :mod:`app.mirror_runtime`,
:mod:`app.mirror_manager`, and :mod:`app.mirror_websocket`.  This module is
kept as a stable import surface for older integrations and tests.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import struct
import threading
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import WebSocket
from scrcpy import Scrcpy
from starlette.websockets import WebSocketDisconnect

from . import storage
from .adb_monitor import adb_monitor
from .mirror_manager import (
    MirrorManager,
    event_user_devices,
    event_users_with_view_access,
    exposed_snapshot,
    manager,
    public_event_payload,
)
from .mirror_runtime import (
    CONTROL_LEASE_VERIFY_INTERVAL,
    CONTROL_PERMISSION_RECHECK_INTERVAL,
    RAW_V2_FLAG_CONFIG,
    RAW_V2_FLAG_DISCONTINUITY,
    RAW_V2_FLAG_KEYFRAME,
    RAW_V2_HEADER,
    RAW_V2_HEADER_LENGTH,
    RAW_V2_MAGIC,
    RAW_V2_MAX_PACKET_BYTES,
    RAW_V2_VERSION,
    SCRCPY_ALLOWED_CLIENT_CONTROL_TYPES,
    SCRCPY_CLIENT_CLIPBOARD_MAX_BYTES,
    SCRCPY_CLIENT_CONTROL_MAX_BASE64_CHARS,
    SCRCPY_CLIENT_CONTROL_MAX_BYTES,
    SCRCPY_CLIENT_TEXT_MAX_BYTES,
    SCRCPY_RESET_VIDEO_MESSAGE,
    SCRCPY_STREAM_MODE,
    STREAM_HEALTH_TIMEOUT,
    STREAM_START_RESPONSE_TIMEOUT,
    VIDEO_PERMISSION_RECHECK_SECONDS,
    MAX_VIDEO_CONNECTIONS_PER_USER,
    VIDEO_QUEUE_MAXSIZE,
    VIDEO_QUEUE_MAX_BYTES,
    VIDEO_QUEUE_SOFT_LIMIT,
    VIDEO_RESET_COOLDOWN,
    VIDEO_SEND_TIMEOUT_SECONDS,
    VIEWER_DISCONNECT_GRACE,
    VIEWER_RESERVATION_TTL,
    ClientSession,
    ControlAuditCallback,
    EventClient,
    MirrorSession,
    RawV2Packet,
    StreamReset,
    StreamTermination,
    _bump_control_lock_epoch,
    _control_device_lock,
    _control_epoch_lock,
    _stream_mode,
    acquire_control_lock,
    control_lock_epoch,
    pack_raw_v2_packet,
    release_control_lock,
    renew_control_lock,
    unpack_raw_v2_packet,
)
from .mirror_websocket import (
    ControlLeaseState,
    _emit_control_audit,
    _u16,
    _u32,
    _video_connection_counts,
    control_socket,
    handle_control_bytes,
    handle_control_text,
    validate_client_control_payload,
    video_socket,
)
from .video_options import settings_to_video_options

log = logging.getLogger("webscrcpy.mirror")

__all__ = [
    "CONTROL_LEASE_VERIFY_INTERVAL",
    "CONTROL_PERMISSION_RECHECK_INTERVAL",
    "MAX_VIDEO_CONNECTIONS_PER_USER",
    "ClientSession",
    "ControlAuditCallback",
    "ControlLeaseState",
    "EventClient",
    "MirrorManager",
    "MirrorSession",
    "RAW_V2_FLAG_CONFIG",
    "RAW_V2_FLAG_DISCONTINUITY",
    "RAW_V2_FLAG_KEYFRAME",
    "RAW_V2_HEADER",
    "RAW_V2_HEADER_LENGTH",
    "RAW_V2_MAGIC",
    "RAW_V2_MAX_PACKET_BYTES",
    "RAW_V2_VERSION",
    "RawV2Packet",
    "SCRCPY_ALLOWED_CLIENT_CONTROL_TYPES",
    "SCRCPY_CLIENT_CLIPBOARD_MAX_BYTES",
    "SCRCPY_CLIENT_CONTROL_MAX_BASE64_CHARS",
    "SCRCPY_CLIENT_CONTROL_MAX_BYTES",
    "SCRCPY_CLIENT_TEXT_MAX_BYTES",
    "SCRCPY_RESET_VIDEO_MESSAGE",
    "SCRCPY_STREAM_MODE",
    "STREAM_HEALTH_TIMEOUT",
    "STREAM_START_RESPONSE_TIMEOUT",
    "StreamReset",
    "StreamTermination",
    "VIDEO_PERMISSION_RECHECK_SECONDS",
    "VIDEO_QUEUE_MAXSIZE",
    "VIDEO_QUEUE_MAX_BYTES",
    "VIDEO_QUEUE_SOFT_LIMIT",
    "VIDEO_RESET_COOLDOWN",
    "VIDEO_SEND_TIMEOUT_SECONDS",
    "VIEWER_DISCONNECT_GRACE",
    "VIEWER_RESERVATION_TTL",
    "_bump_control_lock_epoch",
    "_control_device_lock",
    "_control_epoch_lock",
    "_emit_control_audit",
    "_stream_mode",
    "_u16",
    "_u32",
    "_video_connection_counts",
    "acquire_control_lock",
    "control_lock_epoch",
    "control_socket",
    "event_user_devices",
    "event_users_with_view_access",
    "exposed_snapshot",
    "handle_control_bytes",
    "handle_control_text",
    "manager",
    "pack_raw_v2_packet",
    "public_event_payload",
    "release_control_lock",
    "renew_control_lock",
    "settings_to_video_options",
    "unpack_raw_v2_packet",
    "validate_client_control_payload",
    "video_socket",
    # Legacy module-level dependencies retained for integrations and tests.
    "asyncio",
    "base64",
    "json",
    "logging",
    "os",
    "struct",
    "threading",
    "time",
    "uuid",
    "asynccontextmanager",
    "WebSocket",
    "Scrcpy",
    "WebSocketDisconnect",
    "storage",
    "adb_monitor",
]
