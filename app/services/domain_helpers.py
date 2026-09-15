"""Compatibility exports for domain helpers moved to focused services.

Video preference and session-cookie helpers remain here until their own route
phases.  ALAS, Gateway and mirror helpers are static re-exports only; new code
must import their owning service directly.
"""

from __future__ import annotations

from fastapi import Response

from .. import security, storage
from ..http_helpers import parse_bool
from ..video_options import (
    DEFAULT_VIDEO_OPTIONS,
    EMERGENCY_PROFILE_ID,
    VideoOptionError,
    emergency_video_options,
    enabled_profile_ids,
    enabled_stream_modes_value,
    fullscreen_profile_value,
    is_emergency_profile_id,
    max_size_limit_value,
    normalize_video_options,
    profile_payloads,
    resolve_default_projection_profile,
    settings_to_video_options,
)
from .alas_service import (
    ADMIN_ALAS_OVERVIEW_CONCURRENCY,
    _admin_alas_call,
    _alas_overview_owners,
    _alas_runtime_unreachable,
    _alas_status_for_many,
    admin_alas_overview,
    admin_alas_overview_local,
    admin_alas_payload,
    admin_alas_permissions_payload,
    admin_alas_status_for_config,
    admin_overview_storage_payload,
    admin_runtime_alas_config_names,
    alas_binding_for_user,
    alas_device_for_user,
    bound_alas_config_names,
    cached_public_alas_status,
    clear_alas_status_cache,
    log_alas_embed_denied,
    log_alas_websocket_close,
    public_admin_alas_bindings,
    public_alas_status,
    public_user_alas_bindings,
    require_alas_binding,
)
from .gateway_service import (
    _alas_gateway_url,
    _gateway_context,
    _gateway_iframe_src,
    _gateway_request_context,
    _gateway_websocket_context,
)
from .mirror_service import public_mirror_failure, public_sessions_for_user, resolve_device_or_404


def default_video_options() -> dict:
    return settings_to_video_options(storage.get_settings())


def video_profiles(settings: dict | None = None) -> dict:
    return profile_payloads(settings or storage.get_settings())


def user_video_fallback(
    username: str,
    settings: dict | None = None,
    stored: dict | None = None,
) -> tuple[dict, dict]:
    """Resolve a user's saved preset without allowing stale state to break reads."""
    settings = settings or storage.get_settings()
    stored = stored if stored is not None else storage.get_user_video_preference(username)
    enabled_modes = enabled_stream_modes_value(settings.get("scrcpy_enabled_stream_modes", "raw"))
    enabled_presets = enabled_profile_ids(settings)
    max_size_limit = max_size_limit_value(settings.get("video_max_size_limit"))
    all_profiles = video_profiles(settings)
    projection_profiles = {
        name: values
        for name, values in all_profiles.items()
        if not bool(values.get("fullscreen_only", False))
    }
    resolved_default = resolve_default_projection_profile(settings, all_profiles, enabled_presets)
    if resolved_default["using_emergency"]:
        default_options = emergency_video_options(enabled_modes, max_size_limit)
    else:
        default_options = normalize_video_options(
            {"profile": resolved_default["profile_id"]},
            DEFAULT_VIDEO_OPTIONS,
            profiles=projection_profiles,
            enabled_stream_modes=enabled_modes,
            enabled_presets=enabled_presets,
            max_size_limit=max_size_limit,
            over_limit_error=False,
        )

    account = storage.get_user(username) or {}
    account_role = account["role"] if "role" in account.keys() else None
    allow_custom_tuning = bool(account_role == "admin") or parse_bool(
        settings.get("video_user_custom_tuning"), False
    )
    stored_profile = str((stored or {}).get("profile") or "").strip()
    stored_valid = bool(
        stored
        and (
            (stored_profile in projection_profiles and stored_profile in enabled_presets)
            or (stored_profile == "custom" and allow_custom_tuning)
        )
    )
    if stored_valid:
        try:
            stored_options = normalize_video_options(
                {},
                stored,
                profiles=projection_profiles,
                enabled_stream_modes=enabled_modes,
                enabled_presets=enabled_presets,
                max_size_limit=max_size_limit,
                over_limit_error=False,
            )
            if stored_options.get("profile") == stored_profile:
                return stored_options, {
                    "requested_preset_id": stored_profile,
                    "effective_preset_id": stored_profile,
                    "default_preset_id": resolved_default["profile_id"],
                    "configured_default_preset_id": resolved_default["configured_profile_id"],
                    "fallback_reason": "saved_preference",
                    "using_emergency": False,
                }
        except VideoOptionError:
            stored_valid = False

    return default_options, {
        "requested_preset_id": stored_profile,
        "effective_preset_id": default_options.get("profile", ""),
        "default_preset_id": resolved_default["profile_id"],
        "configured_default_preset_id": resolved_default["configured_profile_id"],
        "fallback_reason": "saved_preference_unavailable" if stored_profile else resolved_default["fallback_reason"],
        "using_emergency": bool(resolved_default["using_emergency"]),
    }


def user_video_options(username: str, payload: dict | None = None) -> dict:
    settings = storage.get_settings()
    enabled_modes = enabled_stream_modes_value(settings.get("scrcpy_enabled_stream_modes", "raw"))
    enabled_presets = enabled_profile_ids(settings)
    max_size_limit = max_size_limit_value(settings.get("video_max_size_limit"))
    all_profiles = video_profiles(settings)
    profiles = {
        name: values
        for name, values in all_profiles.items()
        if not bool(values.get("fullscreen_only", False))
    }
    account = storage.get_user(username) or {}
    account_role = account["role"] if "role" in account.keys() else None
    allow_custom_tuning = bool(account_role == "admin") or parse_bool(
        settings.get("video_user_custom_tuning"), False
    )
    stored = storage.get_user_video_preference(username)
    fallback, _resolution = user_video_fallback(username, settings, stored)
    request_payload = payload or {}
    requested_profile = str(
        request_payload.get("profile", request_payload.get("video_profile", "")) or ""
    ).strip()
    if requested_profile and is_emergency_profile_id(requested_profile):
        raise VideoOptionError(
            "emergency profile is read-only",
            "server.video_error.emergency_profile_readonly",
        )
    if fallback.get("profile") == EMERGENCY_PROFILE_ID and not requested_profile:
        options = dict(fallback)
    else:
        options = normalize_video_options(
            request_payload,
            fallback,
            profiles=profiles,
            enabled_stream_modes=enabled_modes,
            enabled_presets=enabled_presets,
            max_size_limit=max_size_limit,
        )
    if not allow_custom_tuning and options.get("profile") == "custom":
        raise VideoOptionError(
            "custom tuning is disabled for users",
            "server.video_error.custom_tuning_disabled",
        )
    return options


def fullscreen_video_options(username: str) -> tuple[dict, str]:
    """Resolve the administrator's fullscreen preset for one account.

    返回 ``(options, profile_id)``。``profile_id`` 为空表示当前没有可用的全屏
    预设（未配置，或分辨率上限低于全屏要求），此时原样返回该用户自己的画质设置，
    调用方据此知道「这次没有提升」，而不是静默换了别的参数。

    提升只覆盖编码三件套（profile / video_bit_rate / max_size / max_fps）；
    ``adaptive`` 与 ``scrcpy_stream_mode`` 沿用用户当前设置，避免全屏顺手切换
    传输模式（raw / protocol / legacy）导致画面二次重连。
    """
    settings = storage.get_settings()
    enabled_modes = enabled_stream_modes_value(settings.get("scrcpy_enabled_stream_modes", "raw"))
    enabled_presets = enabled_profile_ids(settings)
    max_size_limit = max_size_limit_value(settings.get("video_max_size_limit"))
    all_profiles = video_profiles(settings)
    enabled_profiles = {
        name: values for name, values in all_profiles.items() if name in enabled_presets
    }
    stored = storage.get_user_video_preference(username)
    fallback, _resolution = user_video_fallback(username, settings, stored)
    if fallback.get("profile") == EMERGENCY_PROFILE_ID:
        # 应急画质是只读的最后手段，全屏也不例外。
        return dict(fallback), ""
    profile_id = fullscreen_profile_value(
        settings.get("video_fullscreen_profile"),
        enabled_profiles,
        max_size_limit=max_size_limit,
    )
    if not profile_id or profile_id not in enabled_profiles:
        return dict(fallback), ""
    options = normalize_video_options(
        {"profile": profile_id},
        fallback,
        profiles=enabled_profiles,
        enabled_stream_modes=enabled_modes,
        enabled_presets=enabled_presets,
        max_size_limit=max_size_limit,
    )
    return options, profile_id


def set_session_cookie(response: Response, sid: str) -> None:
    response.set_cookie(
        security.SESSION_COOKIE,
        sid,
        httponly=True,
        secure=security.secure_cookie_enabled(),
        samesite="strict",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(security.SESSION_COOKIE, path="/")


__all__ = [
    "ADMIN_ALAS_OVERVIEW_CONCURRENCY",
    "_admin_alas_call",
    "_alas_gateway_url",
    "_alas_overview_owners",
    "_alas_runtime_unreachable",
    "_alas_status_for_many",
    "_gateway_context",
    "_gateway_iframe_src",
    "_gateway_request_context",
    "_gateway_websocket_context",
    "admin_alas_overview",
    "admin_alas_overview_local",
    "admin_alas_payload",
    "admin_alas_permissions_payload",
    "admin_alas_status_for_config",
    "admin_overview_storage_payload",
    "admin_runtime_alas_config_names",
    "alas_binding_for_user",
    "alas_device_for_user",
    "bound_alas_config_names",
    "cached_public_alas_status",
    "clear_alas_status_cache",
    "clear_session_cookie",
    "default_video_options",
    "log_alas_embed_denied",
    "log_alas_websocket_close",
    "public_admin_alas_bindings",
    "public_alas_status",
    "public_mirror_failure",
    "public_sessions_for_user",
    "public_user_alas_bindings",
    "require_alas_binding",
    "resolve_device_or_404",
    "set_session_cookie",
    "user_video_fallback",
    "user_video_options",
    "video_profiles",
]
