import json
import logging
import re
from functools import lru_cache
from pathlib import Path


DEFAULT_LOCALE = "zh-CN"
SUPPORTED_LOCALES = (DEFAULT_LOCALE,)
LOCALE_DIR = Path(__file__).resolve().parents[1] / "static" / "i18n"
log = logging.getLogger("webscrcpy.i18n")
INTERPOLATION_RE = re.compile(r"\{([a-zA-Z0-9_]+)\}")


def normalize_locale(locale: object = None) -> str:
    value = str(locale or DEFAULT_LOCALE).strip()
    return value if value in SUPPORTED_LOCALES else DEFAULT_LOCALE


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


def load_catalog(locale: str = DEFAULT_LOCALE) -> dict:
    return _load_catalog(normalize_locale(locale))


def clear_catalog_cache() -> None:
    _load_catalog.cache_clear()


def resolve_message(key: object, locale: str = DEFAULT_LOCALE) -> str | None:
    current: object = load_catalog(locale)
    for part in str(key or "").split("."):
        if not part or not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current if isinstance(current, str) else None


def translate(key: object, locale: str = DEFAULT_LOCALE, **values) -> str:
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


def browser_payload(locale: str = DEFAULT_LOCALE) -> dict:
    selected = normalize_locale(locale)
    return {"locale": selected, "messages": load_catalog(selected)}


def browser_payload_json(locale: str = DEFAULT_LOCALE) -> str:
    payload = json.dumps(browser_payload(locale), ensure_ascii=False, separators=(",", ":"))
    return payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
