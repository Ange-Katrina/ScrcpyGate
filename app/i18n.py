import json
import logging
import re
from contextvars import ContextVar, Token
from functools import lru_cache
from pathlib import Path


DEFAULT_LOCALE = "zh-CN"
SUPPORTED_LOCALES = (DEFAULT_LOCALE, "en-US")
LOCALE_COOKIE = "scrcpygate_locale"
LOCALE_STORAGE_KEY = "scrcpygate:locale"
LOCALE_DIR = Path(__file__).resolve().parents[1] / "static" / "i18n"
log = logging.getLogger("webscrcpy.i18n")
INTERPOLATION_RE = re.compile(r"\{([a-zA-Z0-9_]+)\}")
CURRENT_LOCALE: ContextVar[str] = ContextVar("scrcpygate_locale", default=DEFAULT_LOCALE)


def normalize_locale(locale: object = None) -> str:
    value = str(locale or "").strip().replace("_", "-")
    if not value:
        return DEFAULT_LOCALE
    for supported in SUPPORTED_LOCALES:
        if value.lower() == supported.lower():
            return supported
    language = value.split("-", 1)[0].lower()
    return next((supported for supported in SUPPORTED_LOCALES if supported.split("-", 1)[0].lower() == language), DEFAULT_LOCALE)


def current_locale() -> str:
    return normalize_locale(CURRENT_LOCALE.get())


def set_current_locale(locale: object) -> Token:
    return CURRENT_LOCALE.set(normalize_locale(locale))


def reset_current_locale(token: Token) -> None:
    CURRENT_LOCALE.reset(token)


def locale_from_request(request) -> str:
    cookies = getattr(request, "cookies", {}) or {}
    stored = str(cookies.get(LOCALE_COOKIE) or "").strip()
    if stored and any(stored.lower() == item.lower() for item in SUPPORTED_LOCALES):
        return normalize_locale(stored)
    headers = getattr(request, "headers", {}) or {}
    candidates = []
    for index, item in enumerate(str(headers.get("accept-language") or "").split(",")):
        language, _, raw_parameters = item.strip().partition(";")
        if not language:
            continue
        quality = 1.0
        for parameter in raw_parameters.split(";"):
            name, separator, value = parameter.strip().partition("=")
            if name.lower() != "q" or not separator:
                continue
            try:
                quality = max(0.0, min(1.0, float(value.strip())))
            except ValueError:
                quality = 0.0
            break
        candidates.append((quality, -index, language))
    for quality, _index, language in sorted(candidates, reverse=True):
        if quality <= 0:
            continue
        if language.strip() == "*":
            continue
        normalized = normalize_locale(language)
        requested_primary = language.replace("_", "-").split("-", 1)[0].lower()
        if any(item.split("-", 1)[0].lower() == requested_primary for item in SUPPORTED_LOCALES):
            return normalized
    return DEFAULT_LOCALE


@lru_cache(maxsize=len(SUPPORTED_LOCALES))
def _load_catalog(selected: str) -> dict:
    path = LOCALE_DIR / f"{selected}.json"
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            raise ValueError("catalog root must be an object")
        return payload
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        log.error("I18N_CATALOG_LOAD_FAILED locale=%s error=%s", selected, type(exc).__name__)
        return {}


def load_catalog(locale: str | None = None) -> dict:
    return _load_catalog(normalize_locale(locale or current_locale()))


def clear_catalog_cache() -> None:
    _load_catalog.cache_clear()


def resolve_message(key: object, locale: str | None = None) -> str | None:
    current: object = load_catalog(locale)
    for part in str(key or "").split("."):
        if not part or not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current if isinstance(current, str) else None


def translate(key: object, locale: str | None = None, **values) -> str:
    message = resolve_message(key, locale)
    if message is None:
        return str(key or "")
    if not values:
        return message

    def replace(match: re.Match) -> str:
        name = match.group(1)
        if name not in values:
            return match.group(0)
        try:
            return str(values[name])
        except Exception:
            return match.group(0)

    return INTERPOLATION_RE.sub(replace, message)


def browser_payload(locale: str | None = None) -> dict:
    selected = normalize_locale(locale or current_locale())
    return {
        "locale": selected,
        "supported_locales": list(SUPPORTED_LOCALES),
        "storage_key": LOCALE_STORAGE_KEY,
        "cookie_name": LOCALE_COOKIE,
        "messages": load_catalog(selected),
    }


def browser_payload_json(locale: str | None = None) -> str:
    payload = json.dumps(browser_payload(locale), ensure_ascii=False, separators=(",", ":"))
    return payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
