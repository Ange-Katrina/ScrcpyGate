"""Pure JSON policy primitives used by the ALAS compatibility facade.

The functions in this module do not know about Storage, FastAPI or a runtime
connection.  Callers provide the protocol key and marker sets so the policy
composition layer remains the owner of its public compatibility semantics.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

# CamelCase / acronym word boundaries: "ALASConfigList" -> "ALAS Config List",
# "checkUpdate" -> "check Update".
_WORD_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_WORD_SPLIT = re.compile(r"[^0-9a-z]+")


def compact_text(value: object) -> str:
    """Case-folded text without separators, matching ``alas_visibility._compact``."""
    return "".join(char.lower() for char in str(value or "") if char.isalnum())


def marker_matches_words(value: object, marker: str) -> bool:
    """Return True when ``marker`` names a whole word of ``value``.

    ASCII markers must line up with word boundaries, because ALAS names its options in
    CamelCase: a plain substring test let the management marker ``manage`` match
    ``GameManager``, so ordinary options were refused (403 over HTTP, a closed session
    over the WebSocket).  Neighbouring words are concatenated before the comparison so
    the compact spellings of the real entries (``AlasConfigList``, ``CheckUpdate``,
    ``Remote Control``, ``alas.config_list``) still match.  CJK markers keep plain
    substring semantics because Chinese text has no word separators.
    """
    if not marker:
        return False
    text = str(value or "")
    if not marker.isascii():
        return marker in compact_text(text)
    words = _WORD_BOUNDARY.sub(" ", text).lower()
    tokens = [token for token in _WORD_SPLIT.split(words) if token]
    for start in range(len(tokens)):
        joined = ""
        for token in tokens[start:]:
            joined += token
            if joined == marker:
                return True
            if len(joined) >= len(marker):
                break
    return False


def text_has_word_marker(text: object, markers: Iterable[str]) -> bool:
    """Whole-word variant of :func:`text_has_marker` for route/entry markers.

    Markers are compacted first so the hand-written spellings (``config_list``,
    ``alas.config_list``, ``settings.admin``) keep working.
    """
    compact_markers = tuple(compact_text(marker) for marker in markers if str(marker or "").strip())
    return any(marker_matches_words(text, marker) for marker in compact_markers)


def text_has_marker(text: object, markers: Iterable[str]) -> bool:
    normalized = "".join(char.lower() if char.isalnum() else " " for char in str(text or ""))
    tokens = {token for token in normalized.split() if token}
    return any(marker in tokens for marker in markers)


def query_contains_action(query: dict, markers: tuple[str, ...], config_keys: Iterable[str]) -> bool:
    config_keys = {str(key).lower() for key in config_keys}
    for key, value in (query or {}).items():
        if str(key).lower() in config_keys:
            continue
        if text_has_marker(key, markers):
            return True
        values = value if isinstance(value, (list, tuple)) else (value,)
        if any(text_has_marker(item, markers) for item in values):
            return True
    return False


def query_contains_management(
    query: dict,
    markers: tuple[str, ...],
    config_keys: Iterable[str],
) -> bool:
    config_keys = {str(key).lower() for key in config_keys}
    for key, value in (query or {}).items():
        if str(key).lower() in config_keys:
            continue
        if text_has_word_marker(key, markers):
            return True
        values = value if isinstance(value, (list, tuple)) else (value,)
        for item in values:
            if text_has_word_marker(item, markers):
                return True
    return False


def message_contains_management(value: Any, keys: Iterable[str], markers: Iterable[str]) -> bool:
    keys = {str(key).lower() for key in keys}
    markers = tuple(str(marker) for marker in markers)
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in keys and text_has_word_marker(item, markers):
                return True
            if isinstance(item, (dict, list, tuple)) and message_contains_management(item, keys, markers):
                return True
    if isinstance(value, (list, tuple)):
        return any(message_contains_management(item, keys, markers) for item in value)
    return False


def message_contains_action(
    value: Any,
    markers: tuple[str, ...],
    keys: Iterable[str],
    config_keys: Iterable[str],
) -> bool:
    keys = {str(key).lower() for key in keys}
    config_keys = {str(key).lower() for key in config_keys}
    if isinstance(value, dict):
        for key, item in value.items():
            lowered_key = str(key).lower()
            if lowered_key in keys and text_has_marker(str(item or ""), markers):
                return True
            if lowered_key not in config_keys and text_has_marker(lowered_key, markers) and not isinstance(item, (dict, list, tuple)):
                return True
            if isinstance(item, (dict, list, tuple)) and message_contains_action(item, markers, keys, config_keys):
                return True
    if isinstance(value, (list, tuple)):
        return any(message_contains_action(item, markers, keys, config_keys) for item in value)
    return False


def message_denied_by_action_permission(
    payload: Any,
    *,
    can_run: bool,
    can_edit: bool,
    run_markers: tuple[str, ...],
    edit_markers: tuple[str, ...],
    action_keys: Iterable[str],
    config_keys: Iterable[str],
) -> bool:
    if not can_run and message_contains_action(payload, run_markers, action_keys, config_keys):
        return True
    if not can_edit and message_contains_action(payload, edit_markers, action_keys, config_keys):
        return True
    return False


def config_value_mismatches(value: Any, config_name: str) -> bool:
    if isinstance(value, dict):
        return any(config_value_mismatches(item, config_name) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(config_value_mismatches(item, config_name) for item in value)
    requested_config = str(value or "").strip()
    return bool(requested_config and requested_config != config_name)


def message_switches_config(value: Any, config_keys: Iterable[str], config_name: str) -> bool:
    config_keys = {str(key).lower() for key in config_keys}
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in config_keys and config_value_mismatches(item, config_name):
                return True
            if isinstance(item, (dict, list, tuple)) and message_switches_config(item, config_keys, config_name):
                return True
    if isinstance(value, (list, tuple)):
        return any(message_switches_config(item, config_keys, config_name) for item in value)
    return False


def parse_json_message(
    message: str | bytes,
    *,
    ensure_size,
    validate_payload,
    budget_error: type[Exception],
):
    """Decode one bounded JSON message without changing policy decisions."""
    ensure_size(message)
    if isinstance(message, bytes):
        try:
            message = message.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if not isinstance(message, str):
        return None
    try:
        payload = json.loads(message)
    except RecursionError as exc:
        raise budget_error("payload_too_complex") from exc
    except Exception:
        return None
    validate_payload(payload)
    return payload


__all__ = [
    "compact_text",
    "marker_matches_words",
    "text_has_marker",
    "text_has_word_marker",
    "query_contains_action",
    "query_contains_management",
    "message_contains_management",
    "message_contains_action",
    "message_denied_by_action_permission",
    "config_value_mismatches",
    "message_switches_config",
    "parse_json_message",
]
