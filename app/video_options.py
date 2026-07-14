import json
import re
from typing import Any


VIDEO_LIMITS = {
    "video_bit_rate": (100000, 100000000),
    "max_size": (0, 8192),
    "max_fps": (0, 240),
}

STREAM_MODES = ("raw", "protocol", "legacy")
NORMAL_PROFILE_NAMES = ("smooth", "balanced", "sharp", "low_latency")
ALAS_PROFILE_NAMES = ("alas_smooth", "alas_balanced", "alas_sharp", "alas_low_latency")
PROFILE_NAMES = NORMAL_PROFILE_NAMES + ALAS_PROFILE_NAMES
PROFILE_FIELDS = ("video_bit_rate", "max_size", "max_fps")
MIN_PRESET_MAX_SIZE = 480
ALAS_PROFILE_MAX_SIZE = 1280
MAX_CUSTOM_PROFILES = 12
CUSTOM_PROFILE_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{1,31}$")

DEFAULT_VIDEO_OPTIONS = {
    "profile": "balanced",
    "adaptive": False,
    "video_bit_rate": 2400000,
    "max_size": 1280,
    "max_fps": 24,
    "scrcpy_stream_mode": "raw",
}

VIDEO_PROFILES = {
    "smooth": {"video_bit_rate": 1000000, "max_size": 960, "max_fps": 24},
    "balanced": {"video_bit_rate": 2400000, "max_size": 1280, "max_fps": 24},
    "sharp": {"video_bit_rate": 6000000, "max_size": 1920, "max_fps": 30},
    "low_latency": {"video_bit_rate": 1800000, "max_size": 960, "max_fps": 30},
    "alas_smooth": {"video_bit_rate": 1000000, "max_size": 960, "max_fps": 24},
    "alas_balanced": {"video_bit_rate": 2400000, "max_size": 1280, "max_fps": 24},
    "alas_sharp": {"video_bit_rate": 4000000, "max_size": 1280, "max_fps": 30},
    "alas_low_latency": {"video_bit_rate": 1800000, "max_size": 960, "max_fps": 30},
}

PROFILE_LABELS = {
    "smooth": "流畅",
    "balanced": "稳定",
    "sharp": "高清",
    "low_latency": "低延迟",
    "alas_smooth": "流畅",
    "alas_balanced": "稳定",
    "alas_sharp": "高清",
    "alas_low_latency": "低延迟",
}

BANDWIDTH_RECOMMENDATIONS = {
    "2mbps": {
        "smooth": {"video_bit_rate": 800000, "max_size": 854, "max_fps": 20},
        "balanced": {"video_bit_rate": 1000000, "max_size": 960, "max_fps": 24},
        "sharp": {"video_bit_rate": 1300000, "max_size": 1280, "max_fps": 24},
        "low_latency": {"video_bit_rate": 1000000, "max_size": 854, "max_fps": 30},
        "alas_smooth": {"video_bit_rate": 800000, "max_size": 854, "max_fps": 20},
        "alas_balanced": {"video_bit_rate": 1000000, "max_size": 960, "max_fps": 24},
        "alas_sharp": {"video_bit_rate": 1300000, "max_size": 1280, "max_fps": 20},
        "alas_low_latency": {"video_bit_rate": 1000000, "max_size": 854, "max_fps": 30},
    },
    "5mbps": {
        "smooth": {"video_bit_rate": 1200000, "max_size": 960, "max_fps": 24},
        "balanced": {"video_bit_rate": 2400000, "max_size": 1280, "max_fps": 24},
        "sharp": {"video_bit_rate": 3200000, "max_size": 1280, "max_fps": 30},
        "low_latency": {"video_bit_rate": 1800000, "max_size": 960, "max_fps": 30},
        "alas_smooth": {"video_bit_rate": 1200000, "max_size": 960, "max_fps": 24},
        "alas_balanced": {"video_bit_rate": 2400000, "max_size": 1280, "max_fps": 24},
        "alas_sharp": {"video_bit_rate": 3200000, "max_size": 1280, "max_fps": 30},
        "alas_low_latency": {"video_bit_rate": 1800000, "max_size": 960, "max_fps": 30},
    },
    "10mbps": {
        "smooth": {"video_bit_rate": 2400000, "max_size": 1280, "max_fps": 24},
        "balanced": {"video_bit_rate": 3500000, "max_size": 1280, "max_fps": 30},
        "sharp": {"video_bit_rate": 6500000, "max_size": 1920, "max_fps": 30},
        "low_latency": {"video_bit_rate": 3000000, "max_size": 1280, "max_fps": 30},
        "alas_smooth": {"video_bit_rate": 1800000, "max_size": 960, "max_fps": 24},
        "alas_balanced": {"video_bit_rate": 3000000, "max_size": 1280, "max_fps": 24},
        "alas_sharp": {"video_bit_rate": 4000000, "max_size": 1280, "max_fps": 30},
        "alas_low_latency": {"video_bit_rate": 2800000, "max_size": 1280, "max_fps": 30},
    },
    "20mbps": {
        "smooth": {"video_bit_rate": 3000000, "max_size": 1280, "max_fps": 24},
        "balanced": {"video_bit_rate": 5000000, "max_size": 1600, "max_fps": 30},
        "sharp": {"video_bit_rate": 8500000, "max_size": 1920, "max_fps": 30},
        "low_latency": {"video_bit_rate": 4000000, "max_size": 1280, "max_fps": 30},
        "alas_smooth": {"video_bit_rate": 2000000, "max_size": 960, "max_fps": 24},
        "alas_balanced": {"video_bit_rate": 3500000, "max_size": 1280, "max_fps": 30},
        "alas_sharp": {"video_bit_rate": 4500000, "max_size": 1280, "max_fps": 30},
        "alas_low_latency": {"video_bit_rate": 3000000, "max_size": 1280, "max_fps": 30},
    },
}


class VideoOptionError(ValueError):
    pass


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
        raise VideoOptionError(f"{key} must be integer") from exc
    minimum, maximum = VIDEO_LIMITS[key]
    if parsed < minimum or parsed > maximum:
        raise VideoOptionError(f"{key} out of range")
    return parsed


def stream_mode_value(value: Any, default: str = "raw") -> str:
    mode = str(value if value is not None and value != "" else default).strip().lower()
    if mode not in STREAM_MODES:
        raise VideoOptionError("scrcpy_stream_mode must be raw, protocol, or legacy")
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


def profile_setting_key(profile: str, field: str) -> str:
    return f"video_preset_{profile}_{field}"


def profile_setting_keys() -> list[str]:
    return [profile_setting_key(profile, field) for profile in PROFILE_NAMES for field in PROFILE_FIELDS]


def label_for_profile(profile: str, values: dict[str, Any] | None = None) -> str:
    values = values or {}
    label = str(values.get("label") or PROFILE_LABELS.get(profile) or profile).strip()
    return label[:32] or profile


def normalize_profile_id(value: Any) -> str:
    profile_id = str(value or "").strip()
    if not CUSTOM_PROFILE_RE.fullmatch(profile_id):
        raise VideoOptionError("custom profile id is invalid")
    if profile_id in PROFILE_NAMES or profile_id in ("custom", "auto"):
        raise VideoOptionError("custom profile id is reserved")
    return profile_id


def custom_profile_payloads(settings: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    settings = settings or {}
    raw = settings.get("video_custom_profiles", "")
    if not raw:
        return {}
    try:
        parsed = json.loads(str(raw))
    except Exception:
        return {}
    if not isinstance(parsed, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for name, values in parsed.items():
        if len(result) >= MAX_CUSTOM_PROFILES or not isinstance(values, dict):
            break
        try:
            profile_id = normalize_profile_id(name)
            normalized = normalize_single_profile(values, VIDEO_PROFILES["balanced"])
        except VideoOptionError:
            continue
        normalized["label"] = label_for_profile(profile_id, values)
        result[profile_id] = normalized
    return result


def normalize_single_profile(values: dict[str, Any], fallback: dict[str, Any]) -> dict[str, int]:
    result: dict[str, int] = {}
    for field in PROFILE_FIELDS:
        value = int_value(values.get(field), field, int(fallback[field]))
        if field == "max_size" and value and value < MIN_PRESET_MAX_SIZE:
            raise VideoOptionError(f"max_size must be at least {MIN_PRESET_MAX_SIZE}")
        result[field] = value
    return result


def profile_payloads(settings: dict[str, Any] | None = None) -> dict[str, dict[str, int]]:
    profiles = {name: dict(VIDEO_PROFILES[name]) for name in PROFILE_NAMES}
    settings = settings or {}
    for profile in PROFILE_NAMES:
        for field in PROFILE_FIELDS:
            key = profile_setting_key(profile, field)
            if key not in settings:
                continue
            try:
                value = int_value(settings.get(key), field, int(profiles[profile][field]))
            except VideoOptionError:
                continue
            if field == "max_size" and value and value < MIN_PRESET_MAX_SIZE:
                continue
            if profile in ALAS_PROFILE_NAMES and field == "max_size":
                value = min(value or ALAS_PROFILE_MAX_SIZE, ALAS_PROFILE_MAX_SIZE)
            profiles[profile][field] = value
    for profile, values in custom_profile_payloads(settings).items():
        profiles[profile] = {field: int(values[field]) for field in PROFILE_FIELDS}
    return profiles


def normalize_profile_payloads(payload: dict[str, Any] | None, fallback: dict[str, dict[str, int]] | None = None) -> dict[str, dict[str, int]]:
    fallback = fallback or profile_payloads()
    payload = payload or {}
    result = {name: dict(fallback.get(name) or VIDEO_PROFILES[name]) for name in PROFILE_NAMES}
    for profile in PROFILE_NAMES:
        values = payload.get(profile) or {}
        if not isinstance(values, dict):
            raise VideoOptionError(f"{profile} preset must be an object")
        for field in PROFILE_FIELDS:
            value = int_value(values.get(field), field, int(result[profile][field]))
            if field == "max_size" and value and value < MIN_PRESET_MAX_SIZE:
                raise VideoOptionError(f"{profile} max_size must be at least {MIN_PRESET_MAX_SIZE}")
            if profile in ALAS_PROFILE_NAMES and field == "max_size" and not 0 < value <= ALAS_PROFILE_MAX_SIZE:
                raise VideoOptionError(f"{profile} max_size must be between {MIN_PRESET_MAX_SIZE} and {ALAS_PROFILE_MAX_SIZE}")
            result[profile][field] = value
    return result


def normalize_custom_profile_payloads(payload: Any) -> dict[str, dict[str, Any]]:
    if payload in (None, ""):
        return {}
    if not isinstance(payload, dict):
        raise VideoOptionError("custom_profiles must be an object")
    if len(payload) > MAX_CUSTOM_PROFILES:
        raise VideoOptionError(f"custom_profiles cannot exceed {MAX_CUSTOM_PROFILES}")
    result: dict[str, dict[str, Any]] = {}
    for name, values in payload.items():
        profile_id = normalize_profile_id(name)
        if not isinstance(values, dict):
            raise VideoOptionError(f"{profile_id} profile must be an object")
        normalized = normalize_single_profile(values, VIDEO_PROFILES["balanced"])
        normalized["label"] = label_for_profile(profile_id, values)
        result[profile_id] = normalized
    return result


def serialize_custom_profiles(profiles: dict[str, dict[str, Any]]) -> str:
    return json.dumps(profiles, ensure_ascii=False, separators=(",", ":"))


def profile_label_payloads(settings: dict[str, Any] | None = None) -> dict[str, str]:
    labels = {name: PROFILE_LABELS[name] for name in PROFILE_NAMES}
    for name, values in custom_profile_payloads(settings).items():
        labels[name] = label_for_profile(name, values)
    return labels


def normalize_video_options(
    payload: dict[str, Any] | None = None,
    fallback: dict[str, Any] | None = None,
    profiles: dict[str, dict[str, int]] | None = None,
    enabled_stream_modes: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    fallback = fallback or DEFAULT_VIDEO_OPTIONS
    profiles = profiles or profile_payloads()
    payload = payload or {}
    profile = str(payload.get("profile", fallback.get("profile", DEFAULT_VIDEO_OPTIONS["profile"])) or "balanced").strip()
    if profile not in profiles and profile not in ("custom", "auto"):
        raise VideoOptionError("profile is invalid")

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
    if int(options["max_size"]) and int(options["max_size"]) < MIN_PRESET_MAX_SIZE:
        raise VideoOptionError(f"max_size must be 0 or at least {MIN_PRESET_MAX_SIZE}")
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
    stream_mode = str(settings.get("scrcpy_stream_mode") or DEFAULT_VIDEO_OPTIONS["scrcpy_stream_mode"]).strip().lower()
    if stream_mode not in STREAM_MODES:
        stream_mode = DEFAULT_VIDEO_OPTIONS["scrcpy_stream_mode"]
    stream_mode = stream_mode_or_default(stream_mode, enabled_modes)
    payload = {
        "profile": settings.get("video_profile") or DEFAULT_VIDEO_OPTIONS["profile"],
        "adaptive": settings.get("video_adaptive", DEFAULT_VIDEO_OPTIONS["adaptive"]),
        "video_bit_rate": settings.get("video_bit_rate", DEFAULT_VIDEO_OPTIONS["video_bit_rate"]),
        "max_size": settings.get("max_size", DEFAULT_VIDEO_OPTIONS["max_size"]),
        "max_fps": settings.get("max_fps", DEFAULT_VIDEO_OPTIONS["max_fps"]),
        "scrcpy_stream_mode": stream_mode,
    }
    return normalize_video_options(payload, DEFAULT_VIDEO_OPTIONS, profiles=profiles, enabled_stream_modes=enabled_modes)


def signature(options: dict[str, Any] | None) -> tuple[int, int, int, str]:
    data = options or DEFAULT_VIDEO_OPTIONS
    return int(data["video_bit_rate"]), int(data["max_size"]), int(data["max_fps"]), str(data.get("scrcpy_stream_mode", "raw"))


def public_video_options(options: dict[str, Any] | None) -> dict[str, Any]:
    base = normalize_video_options({}, DEFAULT_VIDEO_OPTIONS)
    data = dict(base)
    data.update(options or {})
    profile = str(data.get("profile") or base["profile"])
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
