"""Strict boolean parsing for request and settings values.

``http_helpers.parse_bool`` keeps its permissive legacy contract: unknown text
is read as ``False``.  Request handlers that persist or act on a value must use
``parse_bool_strict`` instead, so that the text ``"false"`` can never be read
as true.

This module has no application imports so that low-level modules (for example
``app.alas``, which ``app.http_helpers`` already imports) can reuse it without
introducing an import cycle.
"""

from __future__ import annotations


class InvalidBooleanValue(ValueError):
    """Raised when a value is not an explicit boolean."""


TRUE_TEXT_VALUES = frozenset({"1", "true", "yes", "on"})
FALSE_TEXT_VALUES = frozenset({"0", "false", "no", "off"})


def parse_bool_strict(value: object) -> bool:
    """Return the boolean for an explicit true/false value.

    Accepts real booleans, the integers ``0``/``1`` and the case-insensitive
    text forms ``true/false``, ``yes/no``, ``on/off`` and ``1/0``.  Every other
    value raises :class:`InvalidBooleanValue` instead of silently becoming
    ``False``.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        if value in (0, 1):
            return bool(value)
        raise InvalidBooleanValue(str(value))
    if value is None:
        raise InvalidBooleanValue("missing")
    text = str(value).strip().lower()
    if text in TRUE_TEXT_VALUES:
        return True
    if text in FALSE_TEXT_VALUES:
        return False
    raise InvalidBooleanValue(text)


__all__ = [
    "FALSE_TEXT_VALUES",
    "TRUE_TEXT_VALUES",
    "InvalidBooleanValue",
    "parse_bool_strict",
]
