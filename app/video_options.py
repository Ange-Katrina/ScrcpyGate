import json
import logging
import re
from types import MappingProxyType
from typing import Any

from . import i18n


log = logging.getLogger("webscrcpy.video")


VIDEO_LIMITS = {
    # The bit-rate ceiling is aligned with the encoder that the target hardware
    # actually exposes: Rockchip's `OMX.rk.video_encoder.avc`/`hevc` declare
    # bitrate range="1-10000000" (10 Mbps) in media_codecs.xml. Going above it
    # neither fails nor takes effect (a measured 50 Mbps was written into the
    # encoder and was meaningless). Every built-in preset, including the top
    # BANDWIDTH_RECOMMENDATIONS row (8.5 Mbps), stays inside this ceiling, so
    # narrowing it removes no preset. Change this single constant for hardware
    # that declares more -- both API payloads publish `limits`, and the pages
    # follow it instead of hard-coding their own numbers.
    "video_bit_rate": (100000, 10000000),
    "max_size": (640, 1920),
    "max_fps": (0, 240),
}

STREAM_MODES = ("raw", "protocol", "legacy")
DEFAULT_VIDEO_CODEC = "h264"
NORMAL_PROFILE_NAMES = ("smooth", "balanced", "sharp", "low_latency")
LEGACY_PROFILE_ALIASES = {
    "alas_smooth": "smooth",
    "alas_balanced": "balanced",
    "alas_sharp": "sharp",
    "alas_low_latency": "low_latency",
}
PROFILE_NAMES = NORMAL_PROFILE_NAMES
PROFILE_FIELDS = ("video_bit_rate", "max_size", "max_fps")
# Four resolution floors live here on purpose -- do not casually copy one value
# into another:
#   1. `VIDEO_LIMITS["max_size"]` 640..1920 -- the absolute request-level bound.
#   2. Stored presets `MIN_PRESET_MAX_SIZE` 854..`MAX_PRESET_MAX_SIZE` 1920 --
#      narrower than the absolute bound, because a preset is a real quality tier
#      on the quality page and sub-480p sizes should never be stored as one.
#   3. The emergency profile at 640 (== floor 1) -- deliberately allowed *under*
#      the preset floor: it is the automatic last resort after a preset was
#      removed/disabled, where a blurry picture still beats no picture at all.
#      See EMERGENCY_VIDEO_PROFILE.
#   4. Fullscreen `FULLSCREEN_MIN_MAX_SIZE` 1280 -- fullscreen has its own bar and
#      never accepts a tier under 720p.
# validate_resolution_floor_invariants() asserts these relations at import time,
# so a later edit that breaks the ordering fails immediately instead of silently.
MIN_PRESET_MAX_SIZE = 854
MAX_PRESET_MAX_SIZE = 1920
# Stored presets are edited through inputs that already declare 15..240 fps
# (quality.html / mirror.html).  Keeping the stored-preset range identical to
# that UI range stops a preset from holding a frame rate the page can neither
# show nor save again.  Request-level options keep the wider VIDEO_LIMITS range
# because the grid thumbnail stream deliberately asks for 1 fps.
MIN_PRESET_MAX_FPS = 15
MAX_PRESET_MAX_FPS = VIDEO_LIMITS["max_fps"][1]
FULLSCREEN_MIN_MAX_SIZE = 1280
DEFAULT_FULLSCREEN_PROFILE = "sharp"
MAX_CUSTOM_PROFILES = 12
CUSTOM_PROFILE_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{1,31}$")

# 全局分辨率上限: 长边像素对应的标准 p 档位。
RESOLUTION_CAP_OPTIONS = (
    (640, "360p", "640 x 360"),
    (854, "480p", "854 x 480"),
    (960, "540p", "960 x 540"),
    (1280, "720p", "1280 x 720"),
    (1600, "900p", "1600 x 900"),
    (1920, "1080p", "1920 x 1080"),
)
RESOLUTION_CAP_VALUES = tuple(value for value, _label, _desc in RESOLUTION_CAP_OPTIONS)
DEFAULT_MAX_SIZE_LIMIT = 1920

DEFAULT_VIDEO_OPTIONS = {
    "profile": "balanced",
    # 预留字段：贯穿设置、per-user 偏好、导出/导入与请求 payload，但运行时的
    # mirror_runtime / mirror_websocket 从不读它（自适应码率尚未实现）。实现它或
    # 带迁移删掉它之前，不要把它当成可用的开关，也不要往导出文件里写。
    "adaptive": False,
    "video_bit_rate": 2400000,
    "max_size": 1280,
    "max_fps": 24,
    "scrcpy_stream_mode": "raw",
}

# The emergency profile is deliberately kept outside the administrator preset
# catalogue.  It is a read-only last resort for devices whose saved preset was
# removed/disabled or when no ordinary projection preset remains usable.
EMERGENCY_PROFILE_ID = "emergency"
EMERGENCY_VIDEO_PROFILE = MappingProxyType(
    {
        "video_bit_rate": 800000,
        "max_size": 640,
        "max_fps": 15,
    }
)
EMERGENCY_PROFILE_LABEL = "应急兼容"

VIDEO_PROFILES = {
    "smooth": {"video_bit_rate": 1000000, "max_size": 854, "max_fps": 24},
    "balanced": {"video_bit_rate": 2400000, "max_size": 1280, "max_fps": 24},
    "sharp": {"video_bit_rate": 6000000, "max_size": 1920, "max_fps": 30},
    "low_latency": {"video_bit_rate": 1800000, "max_size": 960, "max_fps": 30},
}

PROFILE_LABEL_KEYS = {
    "smooth": "server.profile.smooth",
    "balanced": "server.profile.balanced",
    "sharp": "server.profile.sharp",
    "low_latency": "server.profile.low_latency",
}

BANDWIDTH_RECOMMENDATIONS = {
    "2mbps": {
        "smooth": {"video_bit_rate": 800000, "max_size": 854, "max_fps": 20},
        "balanced": {"video_bit_rate": 1000000, "max_size": 960, "max_fps": 24},
        "sharp": {"video_bit_rate": 1300000, "max_size": 1280, "max_fps": 24},
        "low_latency": {"video_bit_rate": 1000000, "max_size": 854, "max_fps": 30},
    },
    "5mbps": {
        "smooth": {"video_bit_rate": 1200000, "max_size": 960, "max_fps": 24},
        "balanced": {"video_bit_rate": 2400000, "max_size": 1280, "max_fps": 24},
        "sharp": {"video_bit_rate": 3200000, "max_size": 1280, "max_fps": 30},
        "low_latency": {"video_bit_rate": 1800000, "max_size": 960, "max_fps": 30},
    },
    "10mbps": {
        "smooth": {"video_bit_rate": 2400000, "max_size": 1280, "max_fps": 24},
        "balanced": {"video_bit_rate": 3500000, "max_size": 1280, "max_fps": 30},
        "sharp": {"video_bit_rate": 6500000, "max_size": 1920, "max_fps": 30},
        "low_latency": {"video_bit_rate": 3000000, "max_size": 1280, "max_fps": 30},
    },
    "20mbps": {
        "smooth": {"video_bit_rate": 3000000, "max_size": 1280, "max_fps": 24},
        "balanced": {"video_bit_rate": 5000000, "max_size": 1600, "max_fps": 30},
        "sharp": {"video_bit_rate": 8500000, "max_size": 1920, "max_fps": 30},
        "low_latency": {"video_bit_rate": 4000000, "max_size": 1280, "max_fps": 30},
    },
}

BANDWIDTH_PRESET_KEYS = tuple(BANDWIDTH_RECOMMENDATIONS)
BANDWIDTH_PROFILE_RECOMMENDATIONS = {
    DEFAULT_VIDEO_CODEC: BANDWIDTH_RECOMMENDATIONS,
}


class VideoOptionError(ValueError):
    def __init__(self, message: str, message_key: str, **message_values: Any):
        super().__init__(message)
        self.message_key = message_key
        self.message_values = message_values

    def localized(self, locale: str = i18n.DEFAULT_LOCALE) -> str:
        return i18n.translate(self.message_key, locale, **self.message_values)


def is_emergency_profile_id(value: Any) -> bool:
    """Return whether a request refers to the reserved last-resort profile."""
    return str(value or "").strip().lower() == EMERGENCY_PROFILE_ID


def contains_profile_id(value: Any, profile_id: str) -> bool:
    """Check list or legacy comma-separated profile input without coercion surprises."""
    expected = str(profile_id or "").strip().lower()
    if not expected:
        return False
    if isinstance(value, (list, tuple, set)):
        return any(str(item or "").strip().lower() == expected for item in value)
    return any(
        part.strip().lower() == expected
        for part in str(value or "").replace(";", ",").split(",")
        if part.strip()
    )


def validate_emergency_profile_definition() -> None:
    """Fail fast if the immutable last-resort definition is ever corrupted."""
    expected = {"video_bit_rate": 800000, "max_size": 640, "max_fps": 15}
    if dict(EMERGENCY_VIDEO_PROFILE) != expected:
        raise RuntimeError("emergency profile definition is corrupted")


def validate_resolution_floor_invariants() -> None:
    """Keep the four resolution floors consistent instead of four loose numbers.

    Every floor below is intentional (see the comment on MIN_PRESET_MAX_SIZE);
    the failure mode this guards against is a later edit that silently breaks the
    ordering, e.g. raising the preset floor above the fullscreen floor, or letting
    a stored preset hold a value the API would reject.
    """
    api_min, api_max = VIDEO_LIMITS["max_size"]
    if MAX_PRESET_MAX_SIZE != api_max:
        raise RuntimeError("preset resolution ceiling must match the API ceiling")
    if not api_min <= MIN_PRESET_MAX_SIZE <= MAX_PRESET_MAX_SIZE:
        raise RuntimeError("preset resolution floor is outside the API range")
    if not MIN_PRESET_MAX_SIZE <= FULLSCREEN_MIN_MAX_SIZE <= MAX_PRESET_MAX_SIZE:
        raise RuntimeError("fullscreen resolution floor must sit inside the preset range")
    emergency_edge = int(EMERGENCY_VIDEO_PROFILE["max_size"])
    if not api_min <= emergency_edge <= api_max:
        raise RuntimeError("emergency resolution must stay inside the API range")
    if emergency_edge > MIN_PRESET_MAX_SIZE:
        raise RuntimeError("emergency resolution must not exceed the preset floor")


validate_resolution_floor_invariants()


def validate_emergency_video_options(
    options: dict[str, Any],
    max_size_limit: int | None = None,
) -> dict[str, Any]:
    """Validate every runtime fallback result before it reaches scrcpy."""
    validate_emergency_profile_definition()
    edge = int(EMERGENCY_VIDEO_PROFILE["max_size"])
    if max_size_limit is not None:
        edge = min(edge, max_size_limit_value(max_size_limit))
    expected = {
        "profile": EMERGENCY_PROFILE_ID,
        "adaptive": False,
        "video_bit_rate": int(EMERGENCY_VIDEO_PROFILE["video_bit_rate"]),
        "max_size": edge,
        "max_fps": int(EMERGENCY_VIDEO_PROFILE["max_fps"]),
    }
    if any(options.get(key) != value for key, value in expected.items()):
        raise RuntimeError("emergency runtime options are not immutable")
    if options.get("scrcpy_stream_mode") not in STREAM_MODES:
        raise RuntimeError("emergency stream mode is invalid")
    if "codec" in options and str(options.get("codec") or "").strip().lower() != DEFAULT_VIDEO_CODEC:
        raise RuntimeError("emergency codec is invalid")
    return options


def validate_emergency_profile_payload(
    payload: dict[str, Any],
    max_size_limit: int | None = None,
) -> dict[str, Any]:
    """Validate the read-only metadata exposed to management and workbench UIs."""
    validate_emergency_profile_definition()
    edge = int(EMERGENCY_VIDEO_PROFILE["max_size"])
    if max_size_limit is not None:
        edge = min(edge, max_size_limit_value(max_size_limit))
    width, height = profile_dimensions(EMERGENCY_VIDEO_PROFILE, edge)
    expected = {
        "id": EMERGENCY_PROFILE_ID,
        "preset_id": EMERGENCY_PROFILE_ID,
        "name": EMERGENCY_PROFILE_LABEL,
        "display_name": EMERGENCY_PROFILE_LABEL,
        "kind": "emergency",
        "builtin": False,
        "readonly": True,
        "editable": False,
        "enabled": True,
        "fallback_only": True,
        "immutable": True,
        "codec": DEFAULT_VIDEO_CODEC,
        "tier": "compatibility",
        "fullscreen_only": False,
        "projection_allowed": False,
        "fullscreen_allowed": False,
        "width": width,
        "height": height,
        "max_size": edge,
        "max_fps": int(EMERGENCY_VIDEO_PROFILE["max_fps"]),
        "video_bit_rate": int(EMERGENCY_VIDEO_PROFILE["video_bit_rate"]),
        "fps": int(EMERGENCY_VIDEO_PROFILE["max_fps"]),
        "bitrate_mbps": round(int(EMERGENCY_VIDEO_PROFILE["video_bit_rate"]) / 1000000, 3),
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        raise RuntimeError("emergency profile metadata is not immutable")
    return payload


def bool_value(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def int_value(value: Any, key: str, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        parsed = int(value)
    except Exception as exc:
        raise VideoOptionError(
            f"{key} must be integer",
            "server.video_error.must_be_integer",
            field=key,
        ) from exc
    minimum, maximum = VIDEO_LIMITS[key]
    if parsed < minimum or parsed > maximum:
        raise VideoOptionError(f"{key} out of range", "server.video_error.out_of_range", field=key)
    return parsed


def stream_mode_value(value: Any, default: str = "raw") -> str:
    mode = str(value if value is not None and value != "" else default).strip().lower()
    if mode not in STREAM_MODES:
        raise VideoOptionError(
            "scrcpy_stream_mode must be raw, protocol, or legacy",
            "server.video_error.invalid_stream_mode",
        )
    return mode


def enabled_stream_modes_value(value: Any, default: tuple[str, ...] = ("raw",)) -> tuple[str, ...]:
    if isinstance(value, (list, tuple, set)):
        parts = [str(item).strip().lower() for item in value]
    else:
        parts = [part.strip().lower() for part in str(value or "").replace(";", ",").split(",")]
    modes = [mode for mode in STREAM_MODES if mode in parts]
    if not modes:
        modes = [mode for mode in STREAM_MODES if mode in default]
    if "raw" not in modes:
        modes.insert(0, "raw")
    return tuple(mode for mode in STREAM_MODES if mode in modes)


def stream_mode_or_default(mode: str, enabled_modes: tuple[str, ...] | None = None) -> str:
    enabled_modes = enabled_modes or STREAM_MODES
    if mode in enabled_modes:
        return mode
    return "raw" if "raw" in enabled_modes else enabled_modes[0]


def enabled_presets_value(value: Any, default: tuple[str, ...] | None = None) -> tuple[str, ...]:
    """解析"用户可切换的预设"白名单, 结果为 NORMAL_PROFILE_NAMES 的有序子集。"""
    default = tuple(default or NORMAL_PROFILE_NAMES)
    if isinstance(value, (list, tuple, set)):
        parts = [str(item).strip().lower() for item in value]
    else:
        parts = [part.strip().lower() for part in str(value or "").replace(";", ",").split(",")]
    presets = [name for name in NORMAL_PROFILE_NAMES if name in parts]
    if not presets:
        presets = [name for name in NORMAL_PROFILE_NAMES if name in default]
    return tuple(presets)


def enabled_profile_ids_value(
    value: Any,
    available: Any = None,
    default: tuple[str, ...] | None = None,
) -> tuple[str, ...]:
    """Parse the administrator allow-list for builtin and custom profiles."""
    available_ids = [str(item).strip() for item in (available or ()) if str(item).strip()]
    fallback = tuple(default or available_ids or NORMAL_PROFILE_NAMES)
    if isinstance(value, (list, tuple, set)):
        parts = [str(item).strip() for item in value if str(item).strip()]
    else:
        parts = [part.strip() for part in str(value or "").replace(";", ",").split(",") if part.strip()]
    # No allow-list means every available preset is enabled. This keeps new
    # custom presets usable by default while an explicit list remains strict.
    if not parts:
        return tuple(available_ids or fallback)
    selected = set(parts)
    ordered = tuple(item for item in available_ids if item in selected)
    if ordered:
        return ordered
    return tuple(item for item in fallback if item in available_ids or not available_ids)


def enabled_profile_ids(settings: dict[str, Any] | None = None) -> tuple[str, ...]:
    """Return the administrator allow-list for builtin and custom profiles."""
    settings = settings or {}
    custom = custom_profile_payloads(settings)
    available = tuple(PROFILE_NAMES) + tuple(custom)
    return enabled_profile_ids_value(
        settings.get("video_enabled_presets"),
        available,
        default=tuple(PROFILE_NAMES),
    )


def emergency_profile_payload(max_size_limit: int | None = None) -> dict[str, Any]:
    """Return immutable metadata for the last-resort compatibility profile."""
    validate_emergency_profile_definition()
    edge = int(EMERGENCY_VIDEO_PROFILE["max_size"])
    if max_size_limit is not None:
        edge = min(edge, max_size_limit_value(max_size_limit))
    width, height = profile_dimensions(EMERGENCY_VIDEO_PROFILE, edge)
    payload = {
        "id": EMERGENCY_PROFILE_ID,
        "preset_id": EMERGENCY_PROFILE_ID,
        "name": EMERGENCY_PROFILE_LABEL,
        "display_name": EMERGENCY_PROFILE_LABEL,
        "kind": "emergency",
        "builtin": False,
        "readonly": True,
        "editable": False,
        "enabled": True,
        "fallback_only": True,
        "immutable": True,
        "codec": DEFAULT_VIDEO_CODEC,
        "tier": "compatibility",
        "fullscreen_only": False,
        "projection_allowed": False,
        "fullscreen_allowed": False,
        "width": width,
        "height": height,
        "max_size": edge,
        "max_fps": int(EMERGENCY_VIDEO_PROFILE["max_fps"]),
        "video_bit_rate": int(EMERGENCY_VIDEO_PROFILE["video_bit_rate"]),
        "fps": int(EMERGENCY_VIDEO_PROFILE["max_fps"]),
        "bitrate_mbps": round(int(EMERGENCY_VIDEO_PROFILE["video_bit_rate"]) / 1000000, 3),
    }
    return validate_emergency_profile_payload(payload, max_size_limit)


def emergency_video_options(
    enabled_stream_modes: tuple[str, ...] | None = None,
    max_size_limit: int | None = None,
) -> dict[str, Any]:
    """Build the immutable runtime options used by the emergency fallback."""
    validate_emergency_profile_definition()
    mode = stream_mode_or_default("raw", enabled_stream_modes or STREAM_MODES)
    edge = int(EMERGENCY_VIDEO_PROFILE["max_size"])
    if max_size_limit is not None:
        edge = min(edge, max_size_limit_value(max_size_limit))
    options = {
        "profile": EMERGENCY_PROFILE_ID,
        "adaptive": False,
        "video_bit_rate": int(EMERGENCY_VIDEO_PROFILE["video_bit_rate"]),
        "max_size": edge,
        "max_fps": int(EMERGENCY_VIDEO_PROFILE["max_fps"]),
        "scrcpy_stream_mode": mode,
    }
    return validate_emergency_video_options(options, max_size_limit)


def projection_profile_ids(
    settings: dict[str, Any] | None = None,
    profiles: dict[str, dict[str, Any]] | None = None,
    enabled_ids: tuple[str, ...] | None = None,
) -> tuple[str, ...]:
    """Return enabled profiles that are valid for ordinary projection."""
    settings = settings or {}
    profiles = profiles or profile_payloads(settings)
    enabled_ids = enabled_ids or enabled_profile_ids(settings)
    return tuple(
        profile_id
        for profile_id in enabled_ids
        if profile_id in profiles
        and profile_id != EMERGENCY_PROFILE_ID
        and not bool(profiles[profile_id].get("fullscreen_only", False))
    )


def resolve_default_projection_profile(
    settings: dict[str, Any] | None = None,
    profiles: dict[str, dict[str, Any]] | None = None,
    enabled_ids: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Resolve the administrator default without ever raising on a bad read.

    The configured default is preferred when it is enabled and ordinary-
    projection compatible.  Otherwise the first enabled ordinary profile is
    used, with the immutable emergency profile as the final fallback.
    """
    settings = settings or {}
    profiles = profiles or profile_payloads(settings)
    enabled_ids = enabled_ids or enabled_profile_ids(settings)
    candidates = projection_profile_ids(settings, profiles, enabled_ids)
    configured = str(
        settings.get("video_default_preset")
        or settings.get("video_profile")
        or DEFAULT_VIDEO_OPTIONS["profile"]
    ).strip()
    if configured in candidates:
        return {
            "profile_id": configured,
            "configured_profile_id": configured,
            "fallback_reason": "configured",
            "using_emergency": False,
            "candidates": candidates,
        }
    if candidates:
        return {
            "profile_id": candidates[0],
            "configured_profile_id": configured,
            "fallback_reason": "default_unavailable",
            "using_emergency": False,
            "candidates": candidates,
        }
    validate_emergency_profile_definition()
    return {
        "profile_id": EMERGENCY_PROFILE_ID,
        "configured_profile_id": configured,
        "fallback_reason": "no_projection_presets",
        "using_emergency": True,
        "candidates": (),
    }


def max_size_limit_value(value: Any, default: int = DEFAULT_MAX_SIZE_LIMIT, *, strict: bool = False) -> int:
    """解析全局分辨率上限(长边像素), 必须是标准 p 档位之一。"""
    if value in (None, ""):
        return int(default)
    try:
        parsed = int(value)
    except Exception as exc:
        if strict:
            raise VideoOptionError(
                "max_size_limit must be integer",
                "server.video_error.must_be_integer",
                field="max_size_limit",
            ) from exc
        return int(default)
    if parsed not in RESOLUTION_CAP_VALUES:
        if strict:
            raise VideoOptionError(
                "max_size_limit must be one of standard resolutions",
                "server.video_error.invalid_resolution_limit",
                limit=", ".join(str(item) for item in RESOLUTION_CAP_VALUES),
            )
        return int(default)
    return parsed


def bandwidth_profile_value(value: Any, default: str = "", *, strict: bool = False) -> str:
    """Normalize a codec-aware bandwidth profile while accepting legacy tier keys."""
    if value is None:
        value = default
    if isinstance(value, dict):
        value = value.get("id") or value.get("profile_id") or value.get("tier") or ""
    key = str(value or "").strip().lower()
    if not key:
        return ""
    if ":" not in key:
        key = f"{DEFAULT_VIDEO_CODEC}:{key}"
    codec, tier = key.split(":", 1)
    if codec not in BANDWIDTH_PROFILE_RECOMMENDATIONS or tier not in BANDWIDTH_PROFILE_RECOMMENDATIONS[codec]:
        if strict:
            raise VideoOptionError(
                "bandwidth_preset is invalid",
                "server.video_error.invalid_bandwidth_preset",
                preset=key,
            )
        return bandwidth_profile_value(default, "", strict=False) if default else ""
    return f"{codec}:{tier}"


def bandwidth_profile_payload(value: Any, default: str = "") -> dict[str, Any] | None:
    profile_id = bandwidth_profile_value(value, default)
    if not profile_id:
        return None
    codec, tier = profile_id.split(":", 1)
    return {
        "id": profile_id,
        "codec": codec,
        "tier": tier,
        "legacy_key": tier if codec == DEFAULT_VIDEO_CODEC else profile_id,
    }


def bandwidth_preset_value(value: Any, default: str = "", *, strict: bool = False) -> str:
    """Return the legacy tier key; codec-aware callers should use bandwidth_profile_value."""
    profile_id = bandwidth_profile_value(value, default, strict=strict)
    return profile_id.split(":", 1)[1] if profile_id else ""


def bandwidth_preset_updates(settings: dict[str, Any] | None, tier: str | dict[str, Any]) -> dict[str, str]:
    """按 codec-aware 带宽档生成内置预设更新(分辨率受全局上限约束)。"""
    profile_id = bandwidth_profile_value(tier, strict=True)
    codec, tier_key = profile_id.split(":", 1)
    values = BANDWIDTH_PROFILE_RECOMMENDATIONS[codec][tier_key]
    cap = max_size_limit_value((settings or {}).get("video_max_size_limit"))
    updates: dict[str, str] = {}
    for profile in NORMAL_PROFILE_NAMES:
        for field in PROFILE_FIELDS:
            recommended = int(values[profile][field])
            if field == "max_size":
                recommended = min(recommended, cap)
            updates[profile_setting_key(profile, field)] = str(recommended)
    return updates


def profile_setting_key(profile: str, field: str) -> str:
    return f"video_preset_{profile}_{field}"


def profile_setting_keys() -> list[str]:
    return [profile_setting_key(profile, field) for profile in PROFILE_NAMES for field in PROFILE_FIELDS]


def label_for_profile(
    profile: str,
    values: dict[str, Any] | None = None,
    locale: str = i18n.DEFAULT_LOCALE,
) -> str:
    values = values or {}
    label = str(values.get("label") or "").strip()
    if not label and profile in PROFILE_LABEL_KEYS:
        label = i18n.translate(PROFILE_LABEL_KEYS[profile], locale)
    label = label or profile
    return label[:32] or profile


def normalize_profile_id(value: Any) -> str:
    profile_id = str(value or "").strip()
    if not CUSTOM_PROFILE_RE.fullmatch(profile_id):
        raise VideoOptionError("custom profile id is invalid", "server.video_error.invalid_custom_profile_id")
    if is_emergency_profile_id(profile_id):
        raise VideoOptionError("emergency profile is read-only", "server.video_error.emergency_profile_readonly")
    if profile_id in PROFILE_NAMES or profile_id in LEGACY_PROFILE_ALIASES or profile_id in ("custom", "auto") or profile_id.startswith("alas_"):
        raise VideoOptionError("custom profile id is reserved", "server.video_error.reserved_custom_profile_id")
    return profile_id


def custom_profile_payloads(settings: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    settings = settings or {}
    raw = settings.get("video_custom_profiles", "")
    if not raw:
        return {}
    try:
        parsed = json.loads(str(raw))
    except Exception as exc:
        log.warning("VIDEO_CUSTOM_PRESETS_UNREADABLE all custom presets ignored: %s", exc)
        return {}
    if not isinstance(parsed, dict):
        log.warning("VIDEO_CUSTOM_PRESETS_UNREADABLE stored value is not an object; all custom presets ignored")
        return {}
    result: dict[str, dict[str, Any]] = {}
    for name, values in parsed.items():
        if len(result) >= MAX_CUSTOM_PROFILES or not isinstance(values, dict):
            if not isinstance(values, dict):
                log.warning(
                    "VIDEO_CUSTOM_PRESET_DROPPED name=%r reason=not an object; remaining presets skipped",
                    name,
                )
            break
        try:
            profile_id = normalize_profile_id(name)
            normalized = normalize_single_profile(values, VIDEO_PROFILES["balanced"])
        except VideoOptionError as exc:
            log.warning("VIDEO_CUSTOM_PRESET_DROPPED name=%r reason=%s", name, exc)
            continue
        normalized["label"] = label_for_profile(profile_id, values)
        normalized["fullscreen_only"] = bool_value(values.get("fullscreen_only"), False)
        result[profile_id] = normalized
    return result


def normalize_single_profile(values: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field in PROFILE_FIELDS:
        value = int_value(values.get(field), field, int(fallback[field]))
        if field == "max_size" and not MIN_PRESET_MAX_SIZE <= value <= MAX_PRESET_MAX_SIZE:
            raise VideoOptionError(
                f"max_size must be between {MIN_PRESET_MAX_SIZE} and {MAX_PRESET_MAX_SIZE}",
                "server.video_error.max_size_range",
                min=MIN_PRESET_MAX_SIZE,
                max=MAX_PRESET_MAX_SIZE,
            )
        if field == "max_fps" and not MIN_PRESET_MAX_FPS <= value <= MAX_PRESET_MAX_FPS:
            raise VideoOptionError(
                f"max_fps must be between {MIN_PRESET_MAX_FPS} and {MAX_PRESET_MAX_FPS}",
                "server.video_error.fps_range",
                min=MIN_PRESET_MAX_FPS,
                max=MAX_PRESET_MAX_FPS,
            )
        result[field] = value
    # Presets are encoded by scrcpy using the longest edge, but retaining the
    # requested dimensions keeps the management page honest when a custom
    # preset is edited or selected again.  Older records do not have these
    # optional fields and continue to use the 16:9 display fallback.
    for field, minimum, maximum in (("width", 320, 3840), ("height", 240, 2160)):
        if field not in values or values.get(field) in (None, ""):
            continue
        try:
            dimension = int(values[field])
        except Exception as exc:
            raise VideoOptionError(
                f"{field} must be integer",
                "server.video_error.must_be_integer",
                field=field,
            ) from exc
        if dimension < minimum or dimension > maximum:
            raise VideoOptionError(
                f"{field} out of range",
                "server.video_error.out_of_range",
                field=field,
            )
        result[field] = dimension
    result["fullscreen_only"] = bool_value(values.get("fullscreen_only"), bool(fallback.get("fullscreen_only", False)))
    return result


def profile_payloads(settings: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    profiles = {name: dict(VIDEO_PROFILES[name]) for name in PROFILE_NAMES}
    settings = settings or {}
    for profile in PROFILE_NAMES:
        for field in PROFILE_FIELDS:
            key = profile_setting_key(profile, field)
            if key not in settings:
                continue
            try:
                value = int_value(settings.get(key), field, int(profiles[profile][field]))
            except VideoOptionError as exc:
                log.warning(
                    "VIDEO_PRESET_VALUE_DROPPED key=%s value=%r reason=%s; using built-in default %s",
                    key,
                    settings.get(key),
                    exc,
                    profiles[profile][field],
                )
                continue
            if field == "max_size" and not MIN_PRESET_MAX_SIZE <= value <= MAX_PRESET_MAX_SIZE:
                log.warning(
                    "VIDEO_PRESET_VALUE_DROPPED key=%s value=%s allowed=%s..%s; using built-in default %s",
                    key,
                    value,
                    MIN_PRESET_MAX_SIZE,
                    MAX_PRESET_MAX_SIZE,
                    profiles[profile][field],
                )
                continue
            if field == "max_fps" and not MIN_PRESET_MAX_FPS <= value <= MAX_PRESET_MAX_FPS:
                log.warning(
                    "VIDEO_PRESET_VALUE_DROPPED key=%s value=%s allowed=%s..%s; using built-in default %s",
                    key,
                    value,
                    MIN_PRESET_MAX_FPS,
                    MAX_PRESET_MAX_FPS,
                    profiles[profile][field],
                )
                continue
            profiles[profile][field] = value
    for profile, values in custom_profile_payloads(settings).items():
        profiles[profile] = {
            **{field: int(values[field]) for field in PROFILE_FIELDS},
            **{field: int(values[field]) for field in ("width", "height") if field in values},
            **({"label": values["label"]} if "label" in values else {}),
            "fullscreen_only": bool(values.get("fullscreen_only", False)),
        }
    return profiles


def normalize_profile_payloads(payload: dict[str, Any] | None, fallback: dict[str, dict[str, int]] | None = None) -> dict[str, dict[str, int]]:
    fallback = fallback or profile_payloads()
    payload = payload or {}
    result = {name: dict(fallback.get(name) or VIDEO_PROFILES[name]) for name in PROFILE_NAMES}
    for profile in PROFILE_NAMES:
        values = payload.get(profile) or {}
        if not isinstance(values, dict):
            raise VideoOptionError(
                f"{profile} preset must be an object",
                "server.video_error.preset_object",
                profile=profile,
            )
        for field in PROFILE_FIELDS:
            value = int_value(values.get(field), field, int(result[profile][field]))
            if field == "max_size" and not MIN_PRESET_MAX_SIZE <= value <= MAX_PRESET_MAX_SIZE:
                raise VideoOptionError(
                    f"{profile} max_size must be between {MIN_PRESET_MAX_SIZE} and {MAX_PRESET_MAX_SIZE}",
                    "server.video_error.max_size_range",
                    min=MIN_PRESET_MAX_SIZE,
                    max=MAX_PRESET_MAX_SIZE,
                )
            if field == "max_fps" and not MIN_PRESET_MAX_FPS <= value <= MAX_PRESET_MAX_FPS:
                raise VideoOptionError(
                    f"{profile} max_fps must be between {MIN_PRESET_MAX_FPS} and {MAX_PRESET_MAX_FPS}",
                    "server.video_error.fps_range",
                    min=MIN_PRESET_MAX_FPS,
                    max=MAX_PRESET_MAX_FPS,
                )
            result[profile][field] = value
    return result


def normalize_custom_profile_payloads(payload: Any) -> dict[str, dict[str, Any]]:
    if payload in (None, ""):
        return {}
    if not isinstance(payload, dict):
        raise VideoOptionError("custom_profiles must be an object", "server.video_error.custom_profiles_object")
    if len(payload) > MAX_CUSTOM_PROFILES:
        raise VideoOptionError(
            f"custom_profiles cannot exceed {MAX_CUSTOM_PROFILES}",
            "server.video_error.custom_profiles_limit",
            max=MAX_CUSTOM_PROFILES,
        )
    result: dict[str, dict[str, Any]] = {}
    for name, values in payload.items():
        profile_id = normalize_profile_id(name)
        if not isinstance(values, dict):
            raise VideoOptionError(
                f"{profile_id} profile must be an object",
                "server.video_error.profile_object",
                profile=profile_id,
            )
        normalized = normalize_single_profile(values, VIDEO_PROFILES["balanced"])
        normalized["label"] = label_for_profile(profile_id, values)
        normalized["fullscreen_only"] = bool_value(values.get("fullscreen_only"), False)
        result[profile_id] = normalized
    return result


def serialize_custom_profiles(profiles: dict[str, dict[str, Any]]) -> str:
    return json.dumps(profiles, ensure_ascii=False, separators=(",", ":"))


def profile_label_payloads(
    settings: dict[str, Any] | None = None,
    locale: str = i18n.DEFAULT_LOCALE,
) -> dict[str, str]:
    labels = {name: label_for_profile(name, locale=locale) for name in PROFILE_NAMES}
    for name, values in custom_profile_payloads(settings).items():
        labels[name] = label_for_profile(name, values)
    return labels


def profile_dimensions(values: dict[str, Any], max_size: int | None = None) -> tuple[int, int]:
    """Return stable display dimensions without changing scrcpy's max_size semantics.

    These are the *requested* dimensions, derived from the 16:9 assumption above.
    The device encoder may align the picture to its own granularity: Rockchip's
    `OMX.rk.video_encoder.avc` declares `alignment 16x8`, so the 854 preset is
    really encoded as 848x480 (measured via /proc/mpp_service/sessions-summary).

    Do NOT bake a vendor alignment in here. The rule is SoC/encoder specific
    (16x8 on Rockchip, other values elsewhere), so a hard-coded 848 would simply
    be a wrong number on other hardware. If the truly encoded size is ever needed
    in the UI, it has to come from the device (e.g. the codec config), not from a
    constant. The 6px difference is also why this is documented rather than
    "fixed" by changing the preset value: 854x480 is the standard 480p label.
    """
    original_edge = int(values.get("max_size") or 1280)
    edge = int(max_size if max_size is not None else original_edge)
    width = int(values.get("width") or original_edge)
    height = int(values.get("height") or round(original_edge * 9 / 16))
    if original_edge > 0 and edge < original_edge:
        scale = edge / original_edge
        width = round(width * scale)
        height = round(height * scale)
    return max(1, width), max(1, height)


def preset_catalog(
    settings: dict[str, Any] | None = None,
    *,
    max_size_limit: int | None = None,
    allowed_profiles: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Build one ordered, de-duplicated catalog for admin and workbench UIs."""
    settings = settings or {}
    cap = max_size_limit_value(settings.get("video_max_size_limit")) if max_size_limit is None else int(max_size_limit)
    profiles = profile_payloads(settings)
    labels = profile_label_payloads(settings)
    custom = custom_profile_payloads(settings)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    seen_custom_specs: set[tuple[Any, ...]] = set()
    ordered = list(PROFILE_NAMES) + list(custom)
    for profile in ordered:
        if profile in seen or profile not in profiles:
            continue
        if allowed_profiles is not None and profile not in allowed_profiles:
            continue
        values = profiles[profile]
        if profile not in PROFILE_NAMES:
            custom_spec = (
                values.get("label"),
                values.get("width"),
                values.get("height"),
                values.get("max_size"),
                values.get("max_fps"),
                values.get("video_bit_rate"),
                values.get("fullscreen_only", False),
            )
            if custom_spec in seen_custom_specs:
                continue
            seen_custom_specs.add(custom_spec)
        seen.add(profile)
        effective_edge = min(int(values.get("max_size") or 0), cap)
        width, height = profile_dimensions(values, effective_edge)
        builtin = profile in PROFILE_NAMES
        rows.append(
            {
                "id": profile,
                "preset_id": profile,
                "name": labels.get(profile, profile),
                "display_name": labels.get(profile, profile),
                "kind": "builtin" if builtin else "custom",
                "builtin": builtin,
                "readonly": False,
                "editable": True,
                "enabled": profile in enabled_profile_ids_value(
                    settings.get("video_enabled_presets"),
                    ordered,
                    default=tuple(PROFILE_NAMES),
                ),
                "fullscreen_only": bool(values.get("fullscreen_only", False)) if not builtin else False,
                "projection_allowed": bool(profile in enabled_profile_ids_value(
                    settings.get("video_enabled_presets"),
                    ordered,
                    default=tuple(PROFILE_NAMES),
                ) and not bool(values.get("fullscreen_only", False))),
                "fullscreen_allowed": bool(profile in enabled_profile_ids_value(
                    settings.get("video_enabled_presets"),
                    ordered,
                    default=tuple(PROFILE_NAMES),
                ) and effective_edge >= FULLSCREEN_MIN_MAX_SIZE),
                "width": width,
                "height": height,
                "max_size": effective_edge,
                "max_fps": int(values.get("max_fps") or 0),
                "video_bit_rate": int(values.get("video_bit_rate") or 0),
                "fps": int(values.get("max_fps") or 0),
                "bitrate_mbps": round(int(values.get("video_bit_rate") or 0) / 1000000, 3),
            }
        )
    return rows


def clamp_profiles(profiles: dict[str, dict[str, int]], cap: int) -> dict[str, dict[str, int]]:
    """按全局分辨率上限 clamp 各预设的有效分辨率(不改存储值)。"""
    return {
        name: {**values, "max_size": min(int(values.get("max_size") or 0), cap)}
        for name, values in profiles.items()
    }


def fullscreen_profile_value(
    value: Any,
    profiles: dict[str, dict[str, int]] | None = None,
    *,
    strict: bool = False,
    max_size_limit: int | None = None,
) -> str:
    profiles = profiles or profile_payloads()
    if max_size_limit is not None:
        profiles = clamp_profiles(profiles, max_size_limit)
    requested = str(value or DEFAULT_FULLSCREEN_PROFILE).strip()
    selected = profiles.get(requested)
    if selected and int(selected.get("max_size") or 0) >= FULLSCREEN_MIN_MAX_SIZE:
        return requested
    if strict:
        if requested not in profiles:
            raise VideoOptionError("fullscreen profile is invalid", "server.video_error.fullscreen_profile_invalid")
        raise VideoOptionError(
            f"fullscreen profile max_size must be at least {FULLSCREEN_MIN_MAX_SIZE}",
            "server.video_error.fullscreen_profile_minimum",
            min=FULLSCREEN_MIN_MAX_SIZE,
        )
    for candidate in (DEFAULT_FULLSCREEN_PROFILE, "balanced"):
        values = profiles.get(candidate)
        if values and int(values.get("max_size") or 0) >= FULLSCREEN_MIN_MAX_SIZE:
            return candidate
    eligible = [
        name
        for name, values in profiles.items()
        if int(values.get("max_size") or 0) >= FULLSCREEN_MIN_MAX_SIZE
    ]
    if eligible:
        return max(eligible, key=lambda name: int(profiles[name].get("max_size") or 0))
    # 分辨率上限不足 720p 时全屏画质档无法成立, 返回空串表示不可用。
    return ""


def normalize_video_options(
    payload: dict[str, Any] | None = None,
    fallback: dict[str, Any] | None = None,
    profiles: dict[str, dict[str, int]] | None = None,
    enabled_stream_modes: tuple[str, ...] | None = None,
    enabled_presets: tuple[str, ...] | None = None,
    max_size_limit: int | None = None,
    over_limit_error: bool = True,
) -> dict[str, Any]:
    fallback = fallback or DEFAULT_VIDEO_OPTIONS
    profiles = profiles or profile_payloads()
    if max_size_limit is not None:
        profiles = clamp_profiles(profiles, max_size_limit)
    payload = payload or {}
    profile = str(payload.get("profile", fallback.get("profile", DEFAULT_VIDEO_OPTIONS["profile"])) or "balanced").strip()
    profile = LEGACY_PROFILE_ALIASES.get(profile, profile)
    if is_emergency_profile_id(profile):
        raise VideoOptionError("emergency profile is read-only", "server.video_error.emergency_profile_readonly")
    if profile not in profiles and profile not in ("custom", "auto"):
        raise VideoOptionError("profile is invalid", "server.video_error.profile_invalid")
    if enabled_presets is not None and profile in profiles and profile not in enabled_presets:
        raise VideoOptionError(
            f"profile {profile} is not enabled for users",
            "server.video_error.profile_not_enabled",
            profile=profile,
        )

    base = dict(DEFAULT_VIDEO_OPTIONS)
    base.update({key: fallback[key] for key in DEFAULT_VIDEO_OPTIONS if key in fallback})
    if profile in profiles and not payload.get("custom"):
        base.update(profiles[profile])

    options = {
        "profile": profile,
        "adaptive": bool_value(payload.get("adaptive"), bool_value(base.get("adaptive"), False)),
        "video_bit_rate": int_value(payload.get("video_bit_rate"), "video_bit_rate", int(base["video_bit_rate"])),
        "max_size": int_value(payload.get("max_size"), "max_size", int(base["max_size"])),
        "max_fps": int_value(payload.get("max_fps"), "max_fps", int(base["max_fps"])),
        "scrcpy_stream_mode": stream_mode_value(
            payload.get("scrcpy_stream_mode", payload.get("stream_mode")),
            str(base.get("scrcpy_stream_mode", "raw")),
        ),
    }
    if max_size_limit is not None:
        if "max_size" in payload and int(options["max_size"]) > max_size_limit:
            if over_limit_error:
                raise VideoOptionError(
                    f"max_size exceeds the resolution limit {max_size_limit}",
                    "server.video_error.max_size_exceeds_limit",
                    limit=max_size_limit,
                )
            options["max_size"] = max_size_limit
        elif "max_size" not in payload:
            options["max_size"] = min(int(options["max_size"]), max_size_limit)
    if enabled_stream_modes is not None:
        options["scrcpy_stream_mode"] = stream_mode_or_default(str(options["scrcpy_stream_mode"]), enabled_stream_modes)
    if profile in profiles:
        preset = profiles[profile]
        for key in ("video_bit_rate", "max_size", "max_fps"):
            if key in payload and int(options[key]) != int(preset[key]):
                options["profile"] = "custom"
                break
    return options


def settings_to_video_options(settings: dict[str, Any]) -> dict[str, Any]:
    profiles = profile_payloads(settings)
    enabled_modes = enabled_stream_modes_value(settings.get("scrcpy_enabled_stream_modes", "raw"))
    max_size_limit = max_size_limit_value(settings.get("video_max_size_limit"))
    stream_mode = str(settings.get("scrcpy_stream_mode") or DEFAULT_VIDEO_OPTIONS["scrcpy_stream_mode"]).strip().lower()
    if stream_mode not in STREAM_MODES:
        stream_mode = DEFAULT_VIDEO_OPTIONS["scrcpy_stream_mode"]
    stream_mode = stream_mode_or_default(stream_mode, enabled_modes)
    enabled_ids = enabled_profile_ids(settings)
    resolved_default = resolve_default_projection_profile(settings, profiles, enabled_ids)
    configured_profile = resolved_default["profile_id"]
    if resolved_default["using_emergency"]:
        return emergency_video_options(enabled_modes, max_size_limit)

    # The selected preset is the source of truth for a default session.  This
    # also makes a newly selected admin default effective even when legacy
    # top-level bitrate/size fields still contain the previous preset values.
    selected_values = profiles.get(configured_profile) or DEFAULT_VIDEO_OPTIONS
    payload = {
        "profile": configured_profile,
        "adaptive": settings.get("video_adaptive", DEFAULT_VIDEO_OPTIONS["adaptive"]),
        "video_bit_rate": selected_values.get(
            "video_bit_rate", settings.get("video_bit_rate", DEFAULT_VIDEO_OPTIONS["video_bit_rate"])
        ),
        "max_size": selected_values.get("max_size", settings.get("max_size", DEFAULT_VIDEO_OPTIONS["max_size"])),
        "max_fps": selected_values.get("max_fps", settings.get("max_fps", DEFAULT_VIDEO_OPTIONS["max_fps"])),
        "scrcpy_stream_mode": stream_mode,
    }
    # 存储的默认分辨率超出新上限时按上限收敛, 读取路径不报错。
    try:
        payload["max_size"] = min(int(payload["max_size"]), max_size_limit)
    except Exception:
        pass
    return normalize_video_options(
        payload,
        DEFAULT_VIDEO_OPTIONS,
        profiles=profiles,
        enabled_stream_modes=enabled_modes,
        enabled_presets=enabled_ids,
        max_size_limit=max_size_limit,
    )


def signature(options: dict[str, Any] | None) -> tuple[int, int, int, str]:
    data = options or DEFAULT_VIDEO_OPTIONS
    if is_emergency_profile_id(data.get("profile")):
        # Mirror restart decisions are a runtime boundary too.  Validate the
        # immutable fallback before comparing or forwarding its values.
        validate_emergency_video_options(data)
    return int(data["video_bit_rate"]), int(data["max_size"]), int(data["max_fps"]), str(data.get("scrcpy_stream_mode", "raw"))


def public_video_options(options: dict[str, Any] | None) -> dict[str, Any]:
    base = normalize_video_options({}, DEFAULT_VIDEO_OPTIONS)
    data = dict(base)
    data.update(options or {})
    profile = str(data.get("profile") or base["profile"])
    profile = LEGACY_PROFILE_ALIASES.get(profile, profile)
    if is_emergency_profile_id(profile):
        # All public snapshots and WebSocket state pass through this helper.
        # Do not let a hand-built or mutated emergency object reach a client.
        data["profile"] = EMERGENCY_PROFILE_ID
        validate_emergency_video_options(data)
        profile = EMERGENCY_PROFILE_ID
    if profile not in PROFILE_NAMES and not CUSTOM_PROFILE_RE.fullmatch(profile) and profile not in ("custom", "auto"):
        profile = "custom"
    return {
        "profile": profile,
        "adaptive": bool_value(data.get("adaptive"), False),
        "video_bit_rate": int_value(data.get("video_bit_rate"), "video_bit_rate", DEFAULT_VIDEO_OPTIONS["video_bit_rate"]),
        "max_size": int_value(data.get("max_size"), "max_size", DEFAULT_VIDEO_OPTIONS["max_size"]),
        "max_fps": int_value(data.get("max_fps"), "max_fps", DEFAULT_VIDEO_OPTIONS["max_fps"]),
        "scrcpy_stream_mode": stream_mode_value(data.get("scrcpy_stream_mode"), DEFAULT_VIDEO_OPTIONS["scrcpy_stream_mode"]),
    }
