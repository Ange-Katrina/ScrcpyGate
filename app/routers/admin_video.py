"""Administrator video settings and preset routes."""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from dataclasses import dataclass

from fastapi import APIRouter, HTTPException, Request

from .. import i18n, security, storage
from ..booleans import InvalidBooleanValue, parse_bool_strict
from ..http_helpers import parse_body, parse_bool, video_option_error_message
from ..mirror_manager import manager
from ..services.audit_service import audit_request
from ..video_options import (
    BANDWIDTH_PRESET_KEYS,
    BANDWIDTH_RECOMMENDATIONS,
    EMERGENCY_PROFILE_ID,
    FULLSCREEN_MIN_MAX_SIZE,
    MAX_PRESET_MAX_SIZE,
    MIN_PRESET_MAX_SIZE,
    PROFILE_FIELDS,
    PROFILE_NAMES,
    RESOLUTION_CAP_OPTIONS,
    VIDEO_LIMITS,
    VideoOptionError,
    bandwidth_preset_updates,
    bandwidth_preset_value,
    bandwidth_profile_payload,
    bandwidth_profile_value,
    clamp_profiles,
    contains_profile_id,
    custom_profile_payloads,
    emergency_profile_payload,
    emergency_video_options,
    enabled_profile_ids,
    enabled_profile_ids_value,
    enabled_stream_modes_value,
    fullscreen_profile_value,
    is_emergency_profile_id,
    max_size_limit_value,
    normalize_custom_profile_payloads,
    normalize_profile_id,
    normalize_profile_payloads,
    normalize_video_options,
    public_video_options,
    preset_catalog,
    profile_label_payloads,
    profile_payloads,
    profile_setting_key,
    profile_setting_keys,
    resolve_default_projection_profile,
    serialize_custom_profiles,
    settings_to_video_options,
    stream_mode_or_default,
)

router = APIRouter()

VIEWER_STOP_FIELDS = (
    "viewer_hidden_stop_enabled",
    "viewer_hidden_stop_minutes",
    "viewer_blur_stop_enabled",
    "viewer_blur_stop_minutes",
    "viewer_stop_settings_synced",
)


def _strict_viewer_bool(payload: dict, key: str, current: bool) -> tuple[bool, bool]:
    if key not in payload:
        return current, False
    value = payload[key]
    if type(value) is not bool:
        raise VideoOptionError(
            f"{key} must be boolean",
            "server.video_error.must_be_boolean",
            field=key,
        )
    return value, True


def _strict_viewer_minutes(payload: dict, key: str, current: int) -> tuple[int, bool]:
    if key not in payload:
        return current, False
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise VideoOptionError(
            f"{key} must be integer",
            "server.video_error.must_be_integer",
            field=key,
        )
    if value < 5 or value > 10:
        raise VideoOptionError(
            f"{key} out of range",
            "server.video_error.out_of_range",
            field=key,
        )
    return value, True


def _viewer_stop_updates(payload: dict, current_settings: dict) -> dict[str, str]:
    current = storage.viewer_stop_settings(current_settings)
    hidden_enabled, hidden_enabled_submitted = _strict_viewer_bool(
        payload, "viewer_hidden_stop_enabled", current["viewer_hidden_stop_enabled"]
    )
    hidden_minutes, hidden_minutes_submitted = _strict_viewer_minutes(
        payload, "viewer_hidden_stop_minutes", current["viewer_hidden_stop_minutes"]
    )
    blur_enabled, blur_enabled_submitted = _strict_viewer_bool(
        payload, "viewer_blur_stop_enabled", current["viewer_blur_stop_enabled"]
    )
    blur_minutes, blur_minutes_submitted = _strict_viewer_minutes(
        payload, "viewer_blur_stop_minutes", current["viewer_blur_stop_minutes"]
    )
    synced, synced_submitted = _strict_viewer_bool(
        payload, "viewer_stop_settings_synced", current["viewer_stop_settings_synced"]
    )
    any_submitted = any(
        (
            hidden_enabled_submitted,
            hidden_minutes_submitted,
            blur_enabled_submitted,
            blur_minutes_submitted,
            synced_submitted,
        )
    )
    if not any_submitted:
        return {}
    if synced:
        blur_enabled = hidden_enabled
        blur_minutes = hidden_minutes
        return {
            "viewer_hidden_stop_enabled": "true" if hidden_enabled else "false",
            "viewer_hidden_stop_minutes": str(hidden_minutes),
            "viewer_blur_stop_enabled": "true" if blur_enabled else "false",
            "viewer_blur_stop_minutes": str(blur_minutes),
            "viewer_stop_settings_synced": "true",
        }
    updates = {"viewer_stop_settings_synced": "false" if not synced else "true"}
    if hidden_enabled_submitted:
        updates["viewer_hidden_stop_enabled"] = "true" if hidden_enabled else "false"
    if hidden_minutes_submitted:
        updates["viewer_hidden_stop_minutes"] = str(hidden_minutes)
    if blur_enabled_submitted:
        updates["viewer_blur_stop_enabled"] = "true" if blur_enabled else "false"
    if blur_minutes_submitted:
        updates["viewer_blur_stop_minutes"] = str(blur_minutes)
    return updates

def admin_video_quality_payload(settings: dict | None = None) -> dict:
    """画质控制增强字段(白名单/带宽预设/分辨率上限), 供管理端读写响应复用。"""
    settings = settings or storage.get_settings()
    max_size_limit = max_size_limit_value(settings.get("video_max_size_limit"))
    enabled_presets = enabled_profile_ids(settings)
    profiles = profile_payloads(settings)
    resolved_default = resolve_default_projection_profile(settings, profiles, enabled_presets)
    fullscreen_profile = fullscreen_profile_value(
        settings.get("video_fullscreen_profile"),
        {name: values for name, values in profiles.items() if name in enabled_presets},
        max_size_limit=max_size_limit,
    )
    effective = settings_to_video_options(settings)
    profile_id = str(effective.get("profile") or "")
    selected = profile_id if profile_id in profiles else ""
    labels = profile_label_payloads(settings)
    catalog = preset_catalog(settings, max_size_limit=max_size_limit)
    for row in catalog:
        row["is_default"] = row.get("id") == resolved_default["profile_id"]
    viewer_stop = storage.viewer_stop_settings(settings)
    return {
        "enabled_presets": list(enabled_presets),
        "max_size_limit": max_size_limit,
        "resolution_cap_options": [
            {"value": value, "label": label, "description": description}
            for value, label, description in RESOLUTION_CAP_OPTIONS
        ],
        "bandwidth_preset": bandwidth_preset_value(
            settings.get("video_bandwidth_profile") or settings.get("video_bandwidth_preset")
        ),
        "bandwidth_profile": bandwidth_profile_payload(
            settings.get("video_bandwidth_profile") or settings.get("video_bandwidth_preset")
        ),
        "bandwidth_profile_id": bandwidth_profile_value(
            settings.get("video_bandwidth_profile") or settings.get("video_bandwidth_preset")
        ),
        "bandwidth_presets": [
            {
                "id": f"h264:{key}",
                "key": key,
                "codec": "h264",
                "tier": key,
                "label": i18n.translate(f"server.bandwidth.{key}.label"),
                "description": i18n.translate(f"server.bandwidth.{key}.description"),
            }
            for key in BANDWIDTH_PRESET_KEYS
        ],
        "user_custom_tuning": parse_bool(settings.get("video_user_custom_tuning"), False),
        "fullscreen_profile": fullscreen_profile,
        "preset_catalog": catalog,
        "selected_preset_id": selected,
        "selected_preset_name": labels.get(selected, "") if selected else "",
        "default_preset": resolved_default["profile_id"],
        "default_preset_id": resolved_default["profile_id"],
        "configured_default_preset": resolved_default["configured_profile_id"],
        "configured_default_preset_id": resolved_default["configured_profile_id"],
        "default_preset_fallback_reason": resolved_default["fallback_reason"],
        "using_emergency_preset": bool(resolved_default["using_emergency"]),
        "emergency_preset": emergency_profile_payload(max_size_limit),
        **viewer_stop,
    }


@dataclass(frozen=True)
class _VideoCatalogContext:
    max_size_limit: int
    cap_submitted: bool
    bandwidth_submitted: bool
    bandwidth_profile_id: str
    bandwidth_key: str
    enabled_modes_submitted: bool
    enabled_modes: tuple[str, ...]
    raw_presets: dict | None
    presets: dict
    serialized_custom_profiles: str
    profiles: dict
    enabled_submitted: bool
    enabled_presets: tuple[str, ...]
    profile_settings: dict


@dataclass(frozen=True)
class _VideoResolvedContext:
    submitted_profile: str
    default_submitted: bool
    resolved_default: dict
    fullscreen_profile: str
    options: dict


@dataclass(frozen=True)
class _VideoLimitsContext:
    auto_stop_submitted: bool
    auto_stop: int
    max_session_submitted: bool
    max_session: int
    stream_mode_submitted: bool
    stream_mode: str


def _prepare_video_catalog(payload: dict, current_settings: dict) -> _VideoCatalogContext:
    cap_submitted = "max_size_limit" in payload or "video_max_size_limit" in payload
    max_size_limit = max_size_limit_value(
        payload.get("max_size_limit", payload.get("video_max_size_limit")),
        max_size_limit_value(current_settings.get("video_max_size_limit")),
        strict=cap_submitted,
    )
    bandwidth_submitted = any(
        key in payload
        for key in (
            "bandwidth_preset",
            "video_bandwidth_preset",
            "bandwidth_profile",
            "bandwidth_profile_id",
            "video_bandwidth_profile",
        )
    )
    bandwidth_profile_id = bandwidth_profile_value(
        payload.get(
            "bandwidth_profile_id",
            payload.get(
                "bandwidth_profile",
                payload.get(
                    "video_bandwidth_profile",
                    payload.get("bandwidth_preset", payload.get("video_bandwidth_preset")),
                ),
            ),
        ),
        current_settings.get("video_bandwidth_profile") or current_settings.get("video_bandwidth_preset"),
        strict=bandwidth_submitted,
    )
    bandwidth_key = bandwidth_profile_id.split(":", 1)[1] if bandwidth_profile_id else ""

    enabled_modes_submitted = "scrcpy_enabled_stream_modes" in payload or "enabled_stream_modes" in payload
    raw_enabled_modes = payload.get(
        "scrcpy_enabled_stream_modes",
        payload.get("enabled_stream_modes", current_settings.get("scrcpy_enabled_stream_modes", "raw")),
    )
    enabled_modes = enabled_stream_modes_value(raw_enabled_modes)
    current_profiles = profile_payloads(current_settings)
    raw_presets = payload.get("presets") if "presets" in payload else None
    if raw_presets is not None and not isinstance(raw_presets, dict):
        raise VideoOptionError("presets must be an object", "server.video_error.presets_object")
    if raw_presets is not None and any(is_emergency_profile_id(profile) for profile in raw_presets):
        raise VideoOptionError(
            "emergency profile is read-only",
            "server.video_error.emergency_profile_readonly",
        )
    presets = normalize_profile_payloads(raw_presets, current_profiles)
    custom_profiles = (
        normalize_custom_profile_payloads(payload.get("custom_profiles"))
        if "custom_profiles" in payload
        else custom_profile_payloads(current_settings)
    )
    available_profile_ids = tuple(PROFILE_NAMES) + tuple(custom_profiles)
    enabled_submitted = "enabled_presets" in payload or "video_enabled_presets" in payload
    raw_enabled_presets = payload.get(
        "enabled_presets",
        payload.get("video_enabled_presets", current_settings.get("video_enabled_presets")),
    )
    if enabled_submitted and contains_profile_id(raw_enabled_presets, EMERGENCY_PROFILE_ID):
        raise VideoOptionError(
            "emergency profile is read-only",
            "server.video_error.emergency_profile_readonly",
        )
    enabled_presets = enabled_profile_ids_value(
        raw_enabled_presets,
        available_profile_ids,
        default=tuple(PROFILE_NAMES),
    )

    profile_settings = dict(current_settings)
    for profile, values in presets.items():
        for field, value in values.items():
            profile_settings[profile_setting_key(profile, field)] = str(value)
    serialized_custom_profiles = serialize_custom_profiles(custom_profiles)
    profile_settings["video_custom_profiles"] = serialized_custom_profiles
    profiles = profile_payloads(profile_settings)
    if bandwidth_key:
        updates = bandwidth_preset_updates(
            {**profile_settings, "video_max_size_limit": str(max_size_limit)},
            bandwidth_key,
        )
        profile_settings.update(updates)
        profiles = profile_payloads(profile_settings)

    return _VideoCatalogContext(
        max_size_limit=max_size_limit,
        cap_submitted=cap_submitted,
        bandwidth_submitted=bandwidth_submitted,
        bandwidth_profile_id=bandwidth_profile_id,
        bandwidth_key=bandwidth_key,
        enabled_modes_submitted=enabled_modes_submitted,
        enabled_modes=enabled_modes,
        raw_presets=raw_presets,
        presets=presets,
        serialized_custom_profiles=serialized_custom_profiles,
        profiles=profiles,
        enabled_submitted=enabled_submitted,
        enabled_presets=enabled_presets,
        profile_settings=profile_settings,
    )


def _resolve_fullscreen_profile(
    payload: dict,
    submitted_fullscreen: object,
    fullscreen_profiles: dict,
    max_size_limit: int,
) -> str:
    if "fullscreen_profile" in payload:
        try:
            return fullscreen_profile_value(submitted_fullscreen, fullscreen_profiles, strict=True)
        except VideoOptionError:
            if max_size_limit < FULLSCREEN_MIN_MAX_SIZE:
                return ""
            raise
    if max_size_limit >= FULLSCREEN_MIN_MAX_SIZE:
        try:
            return fullscreen_profile_value(submitted_fullscreen, fullscreen_profiles, strict=True)
        except VideoOptionError:
            # A removed profile may fall back, while an edited profile below 720p must fail closed.
            if str(submitted_fullscreen or "").strip() in fullscreen_profiles:
                raise
            return fullscreen_profile_value(
                submitted_fullscreen,
                fullscreen_profiles,
                max_size_limit=max_size_limit,
            )
    return fullscreen_profile_value(
        submitted_fullscreen,
        fullscreen_profiles,
        max_size_limit=max_size_limit,
    )


def _resolve_video_options(
    payload: dict,
    current_settings: dict,
    catalog: _VideoCatalogContext,
) -> _VideoResolvedContext:
    submitted_profile = str(payload.get("profile", payload.get("video_profile", "")) or "").strip()
    if submitted_profile and is_emergency_profile_id(submitted_profile):
        raise VideoOptionError(
            "emergency profile is read-only",
            "server.video_error.emergency_profile_readonly",
        )
    if submitted_profile in catalog.profiles and bool(
        catalog.profiles[submitted_profile].get("fullscreen_only", False)
    ):
        raise VideoOptionError(
            "fullscreen-only preset cannot be used for ordinary projection",
            "server.video_error.profile_fullscreen_only",
            profile=submitted_profile,
        )

    default_submitted = any(
        key in payload for key in ("default_preset", "default_preset_id", "video_default_preset")
    )
    requested_default = payload.get(
        "default_preset",
        payload.get(
            "default_preset_id",
            payload.get("video_default_preset", current_settings.get("video_default_preset")),
        ),
    )
    if default_submitted and is_emergency_profile_id(requested_default):
        raise VideoOptionError(
            "emergency profile is read-only",
            "server.video_error.emergency_profile_readonly",
        )
    resolution_settings = {
        **catalog.profile_settings,
        "video_custom_profiles": catalog.serialized_custom_profiles,
        "video_enabled_presets": ",".join(catalog.enabled_presets),
        "video_default_preset": str(requested_default or "").strip(),
    }
    resolved_default = resolve_default_projection_profile(
        resolution_settings,
        catalog.profiles,
        catalog.enabled_presets,
    )
    submitted_fullscreen = payload.get(
        "fullscreen_profile", current_settings.get("video_fullscreen_profile", "sharp")
    )
    fullscreen_profiles = {
        name: values for name, values in catalog.profiles.items() if name in catalog.enabled_presets
    }
    fullscreen_profile = _resolve_fullscreen_profile(
        payload,
        submitted_fullscreen,
        fullscreen_profiles,
        catalog.max_size_limit,
    )

    if resolved_default["using_emergency"]:
        options = emergency_video_options(catalog.enabled_modes, catalog.max_size_limit)
    else:
        option_payload = dict(payload)
        if not option_payload.get("profile") and option_payload.get("video_profile"):
            option_payload["profile"] = option_payload["video_profile"]
        if not option_payload.get("profile"):
            option_payload["profile"] = resolved_default["profile_id"]
        options = normalize_video_options(
            option_payload,
            settings_to_video_options(current_settings),
            profiles=catalog.profiles,
            enabled_stream_modes=catalog.enabled_modes,
            max_size_limit=catalog.max_size_limit,
            over_limit_error=False,
        )
    return _VideoResolvedContext(
        submitted_profile=submitted_profile,
        default_submitted=default_submitted,
        resolved_default=resolved_default,
        fullscreen_profile=fullscreen_profile,
        options=options,
    )


def _parse_video_limits(payload: dict, current_settings: dict, enabled_modes: tuple[str, ...]) -> _VideoLimitsContext:
    auto_stop_submitted = "auto_stop_minutes" in payload or "auto_stop_time" in payload
    raw_auto_stop = payload.get(
        "auto_stop_minutes",
        payload.get("auto_stop_time", current_settings.get("auto_stop_minutes", "15")),
    )
    try:
        auto_stop = int(raw_auto_stop)
    except Exception as exc:
        raise VideoOptionError(
            "auto_stop_minutes must be integer",
            "server.video_error.must_be_integer",
            field="auto_stop_minutes",
        ) from exc
    if auto_stop < 0 or auto_stop > 1440:
        raise VideoOptionError(
            "auto_stop_minutes out of range",
            "server.video_error.out_of_range",
            field="auto_stop_minutes",
        )

    max_session_submitted = "max_session_minutes" in payload
    raw_max_session = payload.get("max_session_minutes", current_settings.get("max_session_minutes", "0"))
    try:
        max_session = int(raw_max_session)
    except Exception as exc:
        raise VideoOptionError(
            "max_session_minutes must be integer",
            "server.video_error.must_be_integer",
            field="max_session_minutes",
        ) from exc
    if max_session < 0 or max_session > 10080:
        raise VideoOptionError(
            "max_session_minutes out of range",
            "server.video_error.out_of_range",
            field="max_session_minutes",
        )

    stream_mode_submitted = "scrcpy_stream_mode" in payload or "stream_mode" in payload
    stream_mode = str(
        payload.get("scrcpy_stream_mode", payload.get("stream_mode", current_settings.get("scrcpy_stream_mode", "raw")))
    ).strip().lower()
    if stream_mode not in ("raw", "protocol", "legacy"):
        raise VideoOptionError(
            "scrcpy_stream_mode must be raw, protocol, or legacy",
            "server.video_error.invalid_stream_mode",
        )
    return _VideoLimitsContext(
        auto_stop_submitted=auto_stop_submitted,
        auto_stop=auto_stop,
        max_session_submitted=max_session_submitted,
        max_session=max_session,
        stream_mode_submitted=stream_mode_submitted,
        stream_mode=stream_mode_or_default(stream_mode, enabled_modes),
    )


def _video_option_storage_value(value: object) -> str:
    return "true" if value is True else "false" if value is False else str(value)


def _apply_video_catalog_flags(
    settings_updates: dict,
    payload: dict,
    catalog: _VideoCatalogContext,
    resolved: _VideoResolvedContext,
) -> None:
    if "max_size_limit" in payload or "video_max_size_limit" in payload:
        settings_updates["video_max_size_limit"] = str(catalog.max_size_limit)
    if catalog.bandwidth_submitted:
        settings_updates["video_bandwidth_preset"] = catalog.bandwidth_key
        settings_updates["video_bandwidth_profile"] = catalog.bandwidth_profile_id
    if catalog.enabled_submitted:
        settings_updates["video_enabled_presets"] = ",".join(catalog.enabled_presets)
    if "user_custom_tuning" in payload or "video_user_custom_tuning" in payload:
        tuning = parse_bool(payload.get("user_custom_tuning", payload.get("video_user_custom_tuning")), False)
        settings_updates["video_user_custom_tuning"] = "true" if tuning else "false"
    if catalog.enabled_modes_submitted:
        settings_updates["scrcpy_enabled_stream_modes"] = ",".join(catalog.enabled_modes)
    if "custom_profiles" in payload:
        settings_updates["video_custom_profiles"] = catalog.serialized_custom_profiles
    if "fullscreen_profile" in payload:
        settings_updates["video_fullscreen_profile"] = resolved.fullscreen_profile


def _apply_video_preset_updates(
    settings_updates: dict,
    current_settings: dict,
    catalog: _VideoCatalogContext,
) -> None:
    if catalog.raw_presets is not None:
        for profile, values in catalog.raw_presets.items():
            if profile not in catalog.presets or not isinstance(values, dict):
                continue
            for field in PROFILE_FIELDS:
                if field in values:
                    settings_updates[profile_setting_key(profile, field)] = str(catalog.presets[profile][field])
    if catalog.bandwidth_profile_id:
        settings_updates.update(
            bandwidth_preset_updates(
                {**current_settings, "video_max_size_limit": str(catalog.max_size_limit)},
                catalog.bandwidth_profile_id,
            )
        )


def _clamp_custom_presets_to_cap(serialized: str, cap: int) -> str:
    """Lower every custom preset's max_size to the resolution cap (keeps aspect)."""
    try:
        customs = json.loads(serialized or "{}")
    except Exception:
        return serialized
    if not isinstance(customs, dict):
        return serialized
    changed = False
    for values in customs.values():
        if not isinstance(values, dict):
            continue
        try:
            edge = int(values.get("max_size") or 0) or max(
                int(values.get("width") or 0), int(values.get("height") or 0)
            )
        except Exception:
            continue
        if edge > cap:
            values["max_size"] = cap
            changed = True
    return serialize_custom_profiles(customs) if changed else serialized


def _apply_catalog_video_updates(
    settings_updates: dict,
    payload: dict,
    current_settings: dict,
    catalog: _VideoCatalogContext,
    resolved: _VideoResolvedContext,
    limits: _VideoLimitsContext,
) -> None:
    _apply_video_catalog_flags(settings_updates, payload, catalog, resolved)
    if catalog.cap_submitted:
        # 上限下调时把「已经存下来的分辨率」一起收紧：内置预设的存储行、自定义预设
        # 的 max_size、以及全局 max_size。否则会出现「上限 720p，预设定义还是 1080p」
        # 这种后台看着超限、实际靠 clamp 兜着的情况。
        for profile in PROFILE_NAMES:
            values = catalog.profiles.get(profile) or {}
            try:
                # 预设分辨率有下限（MIN_PRESET_MAX_SIZE，480p）：上限再低也只能收到这里，
                # 否则写回 640 会让下次保存被 normalize_profile_payloads 拒成 400。
                edge = max(
                    min(int(values.get("max_size") or 0), catalog.max_size_limit),
                    MIN_PRESET_MAX_SIZE,
                )
            except Exception:
                continue
            if edge > 0:
                settings_updates[profile_setting_key(profile, "max_size")] = str(edge)
        settings_updates["video_custom_profiles"] = _clamp_custom_presets_to_cap(
            catalog.serialized_custom_profiles, max(catalog.max_size_limit, MIN_PRESET_MAX_SIZE)
        )
        try:
            current_max = int(current_settings.get("max_size") or 0)
        except Exception:
            current_max = 0
        if current_max > catalog.max_size_limit:
            settings_updates["max_size"] = str(catalog.max_size_limit)
    if limits.auto_stop_submitted:
        settings_updates["auto_stop_minutes"] = str(limits.auto_stop)
        settings_updates["auto_stop_time"] = str(limits.auto_stop)
    if limits.max_session_submitted:
        settings_updates["max_session_minutes"] = str(limits.max_session)
    _apply_video_preset_updates(settings_updates, current_settings, catalog)


def _apply_public_video_updates(
    settings_updates: dict,
    payload: dict,
    public_options: dict,
    max_size_limit: int,
) -> None:
    def submitted_max_size_over_cap() -> bool:
        try:
            return int(payload.get("max_size") or 0) > max_size_limit
        except Exception:
            return False

    quality_fields = ("video_bit_rate", "max_size", "max_fps")
    # `adaptive` / `video_adaptive` 是预留字段：仍接受并落库，但运行时无消费者
    # （见 video_options.DEFAULT_VIDEO_OPTIONS 的说明），也不再进导出文件。
    storage_keys = {"profile": "video_profile", "adaptive": "video_adaptive"}
    profile_submitted = "profile" in payload
    if profile_submitted:
        keys = ("profile", "adaptive", *quality_fields)
    else:
        keys = ("adaptive", *quality_fields)
        if any(key in payload for key in quality_fields):
            settings_updates["video_profile"] = str(public_options["profile"])
    for key in keys:
        if not profile_submitted and key not in payload:
            continue
        if key == "max_size" and submitted_max_size_over_cap():
            continue
        settings_updates[storage_keys.get(key, key)] = _video_option_storage_value(public_options[key])


def _apply_default_video_updates(
    settings_updates: dict,
    current_settings: dict,
    catalog: _VideoCatalogContext,
    resolved: _VideoResolvedContext,
) -> None:
    current_default_id = str(
        current_settings.get("video_default_preset") or current_settings.get("video_profile") or ""
    ).strip()
    default_changed = resolved.default_submitted or resolved.resolved_default["profile_id"] != current_default_id
    if default_changed:
        settings_updates["video_default_preset"] = str(resolved.resolved_default["profile_id"])
        settings_updates["video_profile"] = str(resolved.resolved_default["profile_id"])
    if default_changed and resolved.resolved_default["using_emergency"]:
        emergency_options = emergency_video_options(catalog.enabled_modes, catalog.max_size_limit)
        settings_updates.update(
            {
                "video_bit_rate": str(emergency_options["video_bit_rate"]),
                "max_size": str(emergency_options["max_size"]),
                "max_fps": str(emergency_options["max_fps"]),
            }
        )
    elif default_changed:
        selected_default_values = catalog.profiles.get(resolved.resolved_default["profile_id"]) or {}
        settings_updates.update(
            {
                "video_bit_rate": str(
                    selected_default_values.get("video_bit_rate", resolved.options["video_bit_rate"])
                ),
                "max_size": str(selected_default_values.get("max_size", resolved.options["max_size"])),
                "max_fps": str(selected_default_values.get("max_fps", resolved.options["max_fps"])),
            }
        )


def _build_video_updates(payload: dict, current_settings: dict) -> dict:
    catalog = _prepare_video_catalog(payload, current_settings)
    resolved = _resolve_video_options(payload, current_settings, catalog)
    limits = _parse_video_limits(payload, current_settings, catalog.enabled_modes)
    settings_updates = {}
    _apply_catalog_video_updates(settings_updates, payload, current_settings, catalog, resolved, limits)
    public_options = public_video_options(resolved.options)
    if limits.stream_mode_submitted or catalog.enabled_modes_submitted:
        settings_updates["scrcpy_stream_mode"] = limits.stream_mode
    _apply_public_video_updates(settings_updates, payload, public_options, catalog.max_size_limit)
    _apply_default_video_updates(settings_updates, current_settings, catalog, resolved)
    settings_updates.update(_viewer_stop_updates(payload, current_settings))
    return settings_updates


@router.get("/api/admin/video")
async def admin_video_settings(request: Request):
    security.require_admin(request)
    keys = ["video_profile", "video_default_preset", "video_adaptive", "video_bit_rate", "max_size", "max_fps", "auto_stop_minutes", "max_session_minutes", *VIEWER_STOP_FIELDS, "scrcpy_stream_mode", "scrcpy_enabled_stream_modes", "video_custom_profiles", "video_fullscreen_profile", "video_enabled_presets", "video_bandwidth_preset", "video_bandwidth_profile", "video_max_size_limit", "video_user_custom_tuning"] + profile_setting_keys()
    settings = await asyncio.to_thread(storage.get_settings, keys)
    enabled_modes = enabled_stream_modes_value(settings.get("scrcpy_enabled_stream_modes", "raw"))
    stream_mode = str(settings.get("scrcpy_stream_mode") or "raw").strip().lower()
    if stream_mode not in ("raw", "protocol", "legacy"):
        stream_mode = "raw"
    stream_mode = stream_mode_or_default(stream_mode, enabled_modes)
    settings["scrcpy_stream_mode"] = stream_mode
    settings["scrcpy_enabled_stream_modes"] = ",".join(enabled_modes)
    max_size_limit = max_size_limit_value(settings.get("video_max_size_limit"))
    profiles = profile_payloads(settings)
    clamped_profiles = clamp_profiles(profiles, max_size_limit)
    settings["video_max_size_limit"] = str(max_size_limit)
    quality = admin_video_quality_payload(settings)
    settings["video_fullscreen_profile"] = quality["fullscreen_profile"] or settings["video_fullscreen_profile"]
    return {
        "settings": settings,
        "defaults": public_video_options(settings_to_video_options(settings)),
        "profiles": clamped_profiles,
        "stored_profiles": profiles,
        "profile_labels": profile_label_payloads(settings),
        "custom_profiles": clamp_profiles(custom_profile_payloads(settings), max_size_limit),
        "bandwidth_recommendations": BANDWIDTH_RECOMMENDATIONS,
        "limits": VIDEO_LIMITS,
        "stream_modes": ["raw", "protocol", "legacy"],
        "enabled_stream_modes": list(enabled_modes),
        "stream_mode": stream_mode,
        "profile_names": list(PROFILE_NAMES),
        "profile_fields": list(PROFILE_FIELDS),
        "min_preset_max_size": MIN_PRESET_MAX_SIZE,
        "max_preset_max_size": MAX_PRESET_MAX_SIZE,
        "fullscreen_min_max_size": FULLSCREEN_MIN_MAX_SIZE,
        "fullscreen_profile": quality["fullscreen_profile"],
        "enabled_presets": quality["enabled_presets"],
        "max_size_limit": quality["max_size_limit"],
        "resolution_cap_options": quality["resolution_cap_options"],
        "bandwidth_preset": quality["bandwidth_preset"],
        "bandwidth_profile": quality["bandwidth_profile"],
        "bandwidth_profile_id": quality["bandwidth_profile_id"],
        "bandwidth_presets": quality["bandwidth_presets"],
        "user_custom_tuning": quality["user_custom_tuning"],
        "preset_catalog": quality["preset_catalog"],
        "selected_preset_id": quality["selected_preset_id"],
        "selected_preset_name": quality["selected_preset_name"],
        "default_preset": quality["default_preset"],
        "default_preset_id": quality["default_preset_id"],
        "configured_default_preset": quality["configured_default_preset"],
        "configured_default_preset_id": quality["configured_default_preset_id"],
        "default_preset_fallback_reason": quality["default_preset_fallback_reason"],
        "using_emergency_preset": quality["using_emergency_preset"],
        "emergency_preset": quality["emergency_preset"],
        **{key: quality[key] for key in VIEWER_STOP_FIELDS},
    }


def _runtime_device_rows(sessions: dict) -> list[dict]:
    rows = []
    for device_id, session in sessions.items():
        parser = session.get("raw_parser") or {}
        rows.append(
            {
                "device_id": storage.public_device_id(device_id),
                "running": bool(session.get("running")),
                "health": session.get("stream_health") or "idle",
                "mode": session.get("stream_mode") or "none",
                "last_keyframe_at": session.get("last_keyframe_at"),
                "stream_started_at": session.get("stream_started_at"),
                "config_generation": int(session.get("config_generation") or 0),
                "frame_sequence": int(session.get("frame_sequence") or 0),
                "parser": parser,
                "clients": int(session.get("viewer_count") or session.get("clients") or 0),
                "queue_frames": sum(int(row.get("items") or 0) for row in (session.get("client_queues") or {}).values()),
                "queue_bytes": int(session.get("client_queue_bytes") or 0),
                "drops": int(session.get("client_drops") or 0),
                "last_error": str(session.get("last_error") or ""),
            }
        )
    return rows


def _runtime_parser_totals(running: list[dict]) -> dict:
    return {
        "parse_failures": sum(
            int((row.get("raw_parser") or {}).get("dropped_nals") or 0)
            + int((row.get("raw_parser") or {}).get("dropped_access_units") or 0)
            for row in running
        ),
        "discarded_frames": sum(
            int((row.get("raw_parser") or {}).get("dropped_access_units") or 0) for row in running
        ),
        "resync_count": sum(int((row.get("raw_parser") or {}).get("resyncs") or 0) for row in running),
        "cache_peak_bytes": max(
            (
                max(
                    int((row.get("raw_parser") or {}).get("peak_parser_buffer_bytes") or 0),
                    int((row.get("raw_parser") or {}).get("peak_access_unit_bytes") or 0),
                )
                for row in running
            ),
            default=0,
        ),
    }


def _runtime_health(running: list[dict]) -> str:
    if not running:
        return "idle"
    health_states = [str(row.get("stream_health") or "starting") for row in running]
    error_states = {"failed", "invalid_h264", "adb_failed"}
    if any(state in error_states for state in health_states):
        return "error"
    if all(state == "healthy" for state in health_states):
        return "healthy"
    return "degraded"


def _runtime_public_clients(clients) -> list[dict]:
    return [
        {
            **client,
            "device_id": storage.public_device_id(str(client.get("device_id") or "")),
        }
        for client in clients
    ]


def _runtime_status_payload(
    running: list[dict],
    device_rows: list[dict],
    public_clients: list[dict],
    parser_totals: dict,
    now: int,
) -> dict:
    active = max(running, key=lambda row: int(row.get("last_keyframe_at") or 0), default={})
    modes = sorted({str(row.get("stream_mode") or "none") for row in running})
    health = _runtime_health(running)
    last_keyframe = int(active.get("last_keyframe_at") or 0)
    started = min((int(row.get("stream_started_at") or now) for row in running), default=0)
    runtime_errors = [
        f"{row['device_id']}: {row['last_error']}"
        for row in device_rows
        if row.get("last_error")
    ]
    error_text = "; ".join(runtime_errors[:5])
    return {
        "ok": True,
        "status": {
            "status": health,
            "ok": health == "healthy",
            "mode": modes[0] if len(modes) == 1 else ("mixed" if modes else "none"),
            "message": f"{len(running)} 路视频流运行中" if running else "暂无视频流",
            "keyframeAge": max(0, now - last_keyframe) if last_keyframe else None,
            "uptime": max(0, now - started) if started else 0,
            "generation": int(active.get("config_generation") or 0),
            "frameSequence": int(active.get("frame_sequence") or 0),
            "parseFailures": parser_totals["parse_failures"],
            "discardedFrames": parser_totals["discarded_frames"],
            "resyncCount": parser_totals["resync_count"],
            "cachePeak": round(parser_totals["cache_peak_bytes"] / 1024, 1),
            "clientCount": len(public_clients),
            "queueFrames": sum(int(row.get("queue_size") or 0) for row in public_clients),
            "queueBytes": round(sum(int(row.get("queue_bytes") or 0) for row in public_clients) / 1024, 1),
            "droppedFrames": sum(int(row.get("drops") or 0) for row in public_clients),
            "queueState": "正常" if all(not row.get("needs_keyframe") for row in public_clients) else "等待关键帧",
            "error": error_text,
            "errorDetail": error_text,
            "recovery": "无需恢复" if health == "healthy" else ("等待视频流" if not running else "恢复中"),
            "recoveryDetail": "指标来自当前运行会话",
        },
        "devices": device_rows,
        "clients": public_clients,
        "updatedAt": now,
    }


async def admin_video_runtime_payload() -> dict:
    sessions = await manager.snapshot()
    clients = await manager.admin_client_snapshot()
    now = int(time.time())
    running = [session for session in sessions.values() if session.get("running")]
    device_rows = _runtime_device_rows(sessions)
    parser_totals = _runtime_parser_totals(running)
    public_clients = _runtime_public_clients(clients)
    return _runtime_status_payload(running, device_rows, public_clients, parser_totals, now)


@router.get("/api/admin/video/status")
async def admin_video_runtime_status(request: Request):
    security.require_admin(request)
    return await admin_video_runtime_payload()


@router.post("/api/admin/video/restart")
async def admin_video_restart(request: Request):
    security.verify_csrf(request)
    admin = security.require_admin(request)
    restart_settings = await asyncio.to_thread(storage.get_settings)
    result = await manager.restart_streams_safely(settings_to_video_options(restart_settings))
    for rows in result.values():
        for row in rows:
            row["device_id"] = storage.public_device_id(str(row.get("device_id") or ""))
    audit_request(
        request,
        admin,
        "video_restart",
        target_type="video_transport",
        target_id="all",
        metadata={key: len(value) for key, value in result.items()},
    )
    payload = await admin_video_runtime_payload()
    payload.update(result)
    payload["ok"] = not result["failed"]
    return payload


@router.put("/api/admin/video")
async def admin_save_video_settings(request: Request):
    security.verify_csrf(request)
    admin = security.require_admin(request)
    payload = await parse_body(request)
    if "adaptive" in payload:
        # 布尔字段严格解析：拼写错误不能被静默当成 False 存进设置。
        # 注意 `adaptive` 是**预留字段**（无运行时消费者，见 video_options 的说明）：
        # 这里保持严格校验只是为了不改变既有 API 契约，不代表它现在有效果。
        try:
            payload["adaptive"] = parse_bool_strict(payload["adaptive"])
        except InvalidBooleanValue as exc:
            raise HTTPException(status_code=400, detail=i18n.translate("server.error.invalid_boolean")) from exc
    keys = ["video_profile", "video_default_preset", "video_adaptive", "video_bit_rate", "max_size", "max_fps", "auto_stop_minutes", "max_session_minutes", *VIEWER_STOP_FIELDS, "scrcpy_stream_mode", "scrcpy_enabled_stream_modes", "video_custom_profiles", "video_fullscreen_profile", "video_enabled_presets", "video_bandwidth_preset", "video_bandwidth_profile", "video_max_size_limit", "video_user_custom_tuning"] + profile_setting_keys()

    def build_video_updates(current_settings):
        return _build_video_updates(payload, current_settings)

    try:
        await asyncio.to_thread(storage.update_settings, build_video_updates)
    except VideoOptionError as exc:
        raise HTTPException(status_code=400, detail=video_option_error_message(exc)) from exc
    audit_request(
        request,
        admin,
        "video_settings",
        target_type="system_settings",
        target_id="video",
        metadata={"changed_fields": sorted(str(key) for key in payload)[:64]},
    )
    settings = await asyncio.to_thread(storage.get_settings, keys)
    saved_max_size_limit = max_size_limit_value(settings.get("video_max_size_limit"))
    saved_profiles = profile_payloads(settings)
    saved_clamped_profiles = clamp_profiles(saved_profiles, saved_max_size_limit)
    saved_enabled_ids = enabled_profile_ids(settings)
    saved_fullscreen_profile = fullscreen_profile_value(
        settings.get("video_fullscreen_profile"),
        {name: values for name, values in saved_profiles.items() if name in saved_enabled_ids},
        max_size_limit=saved_max_size_limit,
    )
    saved_enabled_modes = enabled_stream_modes_value(settings.get("scrcpy_enabled_stream_modes", "raw"))
    quality = admin_video_quality_payload(settings)
    return {
        "ok": True,
        "settings": settings,
        "defaults": public_video_options(settings_to_video_options(settings)),
        "profiles": saved_clamped_profiles,
        "stored_profiles": saved_profiles,
        "profile_labels": profile_label_payloads(settings),
        "custom_profiles": clamp_profiles(custom_profile_payloads(settings), saved_max_size_limit),
        "bandwidth_recommendations": BANDWIDTH_RECOMMENDATIONS,
        "enabled_stream_modes": list(saved_enabled_modes),
        "fullscreen_profile": saved_fullscreen_profile,
        "fullscreen_min_max_size": FULLSCREEN_MIN_MAX_SIZE,
        "enabled_presets": quality["enabled_presets"],
        "max_size_limit": quality["max_size_limit"],
        "resolution_cap_options": quality["resolution_cap_options"],
        "bandwidth_preset": quality["bandwidth_preset"],
        "bandwidth_profile": quality["bandwidth_profile"],
        "bandwidth_profile_id": quality["bandwidth_profile_id"],
        "bandwidth_presets": quality["bandwidth_presets"],
        "user_custom_tuning": quality["user_custom_tuning"],
        "preset_catalog": quality["preset_catalog"],
        "selected_preset_id": quality["selected_preset_id"],
        "selected_preset_name": quality["selected_preset_name"],
        "default_preset": quality["default_preset"],
        "default_preset_id": quality["default_preset_id"],
        "configured_default_preset": quality["configured_default_preset"],
        "configured_default_preset_id": quality["configured_default_preset_id"],
        "default_preset_fallback_reason": quality["default_preset_fallback_reason"],
        "using_emergency_preset": quality["using_emergency_preset"],
        "emergency_preset": quality["emergency_preset"],
        **{key: quality[key] for key in VIEWER_STOP_FIELDS},
    }


def _admin_video_preset_payload(settings: dict | None = None) -> dict:
    settings = settings or storage.get_settings()
    quality = admin_video_quality_payload(settings)
    catalog = quality["preset_catalog"]
    return {
        "ok": True,
        "presets": catalog,
        "items": catalog,
        "builtin_presets": [row for row in catalog if row["builtin"]],
        "custom_presets": [row for row in catalog if not row["builtin"]],
        "custom_profiles": clamp_profiles(
            custom_profile_payloads(settings),
            quality["max_size_limit"],
        ),
        "selected_preset_id": quality["selected_preset_id"],
        "selected_preset_name": quality["selected_preset_name"],
        "default_preset": quality["default_preset"],
        "default_preset_id": quality["default_preset_id"],
        "configured_default_preset": quality["configured_default_preset"],
        "configured_default_preset_id": quality["configured_default_preset_id"],
        "using_emergency_preset": quality["using_emergency_preset"],
        "emergency_preset": quality["emergency_preset"],
    }


def _custom_preset_values(payload: dict) -> dict:
    """Normalize the management form into the storage shape used by runtime."""
    name = str(payload.get("name", payload.get("label", "")) or "").strip()
    if not name or len(name) > 32:
        raise VideoOptionError("preset name is invalid", "server.video_error.profile_object", profile="name")
    try:
        width = int(payload.get("width"))
        height = int(payload.get("height"))
        fps = int(payload.get("fps", payload.get("max_fps")))
    except (TypeError, ValueError) as exc:
        raise VideoOptionError("preset dimensions must be integers", "server.video_error.must_be_integer", field="尺寸") from exc
    raw_bitrate = payload.get("bitrate", payload.get("bitrate_mbps"))
    if raw_bitrate is None and payload.get("video_bit_rate") is not None:
        raw_bitrate = float(payload["video_bit_rate"]) / 1000000
    try:
        bitrate = float(raw_bitrate)
    except (TypeError, ValueError) as exc:
        raise VideoOptionError("bitrate must be a number", "server.video_error.must_be_integer", field="码率") from exc
    max_size = max(width, height)
    values = {
        "label": name,
        "width": width,
        "height": height,
        "max_size": max_size,
        "max_fps": fps,
        "video_bit_rate": round(bitrate * 1000000),
        "fullscreen_only": parse_bool(payload.get("fullscreen_only"), False),
    }
    normalized = normalize_custom_profile_payloads({"custom_preset": values})["custom_preset"]
    return normalized


def _new_custom_preset_id(name: str, existing: dict[str, dict]) -> str:
    base = re.sub(r"[^a-zA-Z0-9_-]+", "_", name).strip("_-").lower()
    if not base or not base[0].isalpha():
        base = "custom_" + base
    base = base[:24] or "custom"
    candidate = base
    index = 2
    while candidate in existing or candidate in PROFILE_NAMES:
        candidate = f"{base[: max(1, 31 - len(str(index)) - 1)]}_{index}"
        index += 1
    try:
        return normalize_profile_id(candidate)
    except VideoOptionError:
        return "custom_" + uuid.uuid4().hex[:12]


@router.get("/api/admin/video/presets")
async def admin_video_presets(request: Request):
    security.require_admin(request)
    return _admin_video_preset_payload()


@router.post("/api/admin/video/presets/{preset_id}/reset")
async def admin_video_preset_reset(preset_id: str, request: Request):
    """Reset exactly one builtin preset; custom presets have no system default."""
    security.verify_csrf(request)
    admin = security.require_admin(request)
    preset_id = str(preset_id or "").strip()
    if is_emergency_profile_id(preset_id):
        raise HTTPException(
            status_code=400,
            detail=i18n.translate("server.video_error.emergency_profile_readonly"),
        )
    if preset_id not in PROFILE_NAMES:
        raise HTTPException(status_code=400, detail="只有内置画质预设可以恢复默认")

    def mutate(settings):
        defaults = profile_payloads({})[preset_id]
        return {
            profile_setting_key(preset_id, field): str(defaults[field])
            for field in PROFILE_FIELDS
        }

    settings = await asyncio.to_thread(storage.update_settings, mutate)
    audit_request(
        request,
        admin,
        "video_preset_reset",
        target_type="video_preset",
        target_id=preset_id,
    )
    payload = _admin_video_preset_payload(settings)
    payload["reset_preset_id"] = preset_id
    payload["preset"] = next((row for row in payload["presets"] if row["id"] == preset_id), None)
    return payload


@router.post("/api/admin/video/presets")
async def admin_video_preset_create(request: Request):
    security.verify_csrf(request)
    admin = security.require_admin(request)
    payload = await parse_body(request)
    try:
        values = _custom_preset_values(payload)
    except VideoOptionError as exc:
        raise HTTPException(status_code=400, detail=video_option_error_message(exc)) from exc
    created_id = ""

    def mutate(settings):
        nonlocal created_id
        customs = custom_profile_payloads(settings)
        created_id = _new_custom_preset_id(str(values["label"]), customs)
        customs[created_id] = {**values}
        # 自定义预设同样受分辨率上限约束（超出时收紧 max_size，保持宽高比）。
        cap = max_size_limit_value(settings.get("video_max_size_limit"))
        return {"video_custom_profiles": _clamp_custom_presets_to_cap(serialize_custom_profiles(customs), cap)}

    settings = await asyncio.to_thread(storage.update_settings, mutate)
    audit_request(request, admin, "video_preset_create", target_type="video_preset", target_id=created_id)
    return {**_admin_video_preset_payload(settings), "preset": next((row for row in preset_catalog(settings) if row["id"] == created_id), None)}


@router.put("/api/admin/video/presets/{preset_id}")
async def admin_video_preset_update(preset_id: str, request: Request):
    security.verify_csrf(request)
    admin = security.require_admin(request)
    payload = await parse_body(request)
    try:
        preset_id = normalize_profile_id(preset_id)
    except VideoOptionError as exc:
        raise HTTPException(status_code=400, detail=video_option_error_message(exc)) from exc
    values = _custom_preset_values(payload)

    def mutate(settings):
        customs = custom_profile_payloads(settings)
        if preset_id not in customs:
            raise KeyError(preset_id)
        customs[preset_id] = {**values}
        cap = max_size_limit_value(settings.get("video_max_size_limit"))
        updates = {"video_custom_profiles": _clamp_custom_presets_to_cap(serialize_custom_profiles(customs), cap)}
        next_settings = {**settings, **updates}
        resolved_default = resolve_default_projection_profile(
            next_settings,
            profile_payloads(next_settings),
            enabled_profile_ids(next_settings),
        )
        if str(settings.get("video_default_preset") or "") == preset_id or resolved_default["profile_id"] != str(settings.get("video_default_preset") or ""):
            updates["video_default_preset"] = str(resolved_default["profile_id"])
            updates["video_profile"] = str(resolved_default["profile_id"])
        return updates

    try:
        settings = await asyncio.to_thread(storage.update_settings, mutate)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="自定义画质预设不存在") from exc
    audit_request(request, admin, "video_preset_update", target_type="video_preset", target_id=preset_id)
    return {**_admin_video_preset_payload(settings), "preset": next((row for row in preset_catalog(settings) if row["id"] == preset_id), None)}


@router.delete("/api/admin/video/presets/{preset_id}")
async def admin_video_preset_delete(preset_id: str, request: Request):
    security.verify_csrf(request)
    admin = security.require_admin(request)
    try:
        preset_id = normalize_profile_id(preset_id)
    except VideoOptionError as exc:
        raise HTTPException(status_code=400, detail=video_option_error_message(exc)) from exc

    def mutate(settings):
        customs = custom_profile_payloads(settings)
        if preset_id not in customs:
            raise KeyError(preset_id)
        customs.pop(preset_id, None)
        updates = {"video_custom_profiles": serialize_custom_profiles(customs)}
        enabled = [item for item in enabled_profile_ids(settings) if item != preset_id]
        updates["video_enabled_presets"] = ",".join(enabled)
        if str(settings.get("video_profile") or "") == preset_id:
            updates["video_profile"] = "balanced"
            values = profile_payloads(settings).get("balanced") or {}
            updates.update({field: str(values[field]) for field in PROFILE_FIELDS})
        if str(settings.get("video_fullscreen_profile") or "") == preset_id:
            cap = max_size_limit_value(settings.get("video_max_size_limit"))
            updates["video_fullscreen_profile"] = "balanced" if cap >= FULLSCREEN_MIN_MAX_SIZE else ""
        next_settings = {**settings, **updates}
        resolved_default = resolve_default_projection_profile(
            next_settings,
            profile_payloads(next_settings),
            tuple(enabled),
        )
        updates["video_default_preset"] = str(resolved_default["profile_id"])
        updates["video_profile"] = str(resolved_default["profile_id"])
        if resolved_default["using_emergency"]:
            emergency_options = emergency_video_options(
                enabled_stream_modes_value(next_settings.get("scrcpy_enabled_stream_modes", "raw")),
                max_size_limit_value(next_settings.get("video_max_size_limit")),
            )
            updates.update(
                {
                    "video_bit_rate": str(emergency_options["video_bit_rate"]),
                    "max_size": str(emergency_options["max_size"]),
                    "max_fps": str(emergency_options["max_fps"]),
                }
            )
        return updates

    try:
        settings = await asyncio.to_thread(storage.update_settings, mutate)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="自定义画质预设不存在") from exc
    audit_request(request, admin, "video_preset_delete", target_type="video_preset", target_id=preset_id)
    return {**_admin_video_preset_payload(settings), "deleted": preset_id}

__all__ = [name for name in globals() if name.startswith("admin_")] + ["router"]
