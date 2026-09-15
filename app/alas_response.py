"""Bounded response-body reads shared by ALAS transports."""

from __future__ import annotations


class ResponseBodyTooLarge(Exception):
    """Raised when an upstream response exceeds the configured byte limit."""


def _response_content_length(response) -> int | None:
    try:
        headers = response.headers
        raw = headers.get("Content-Length")
    except AttributeError:
        return None
    if raw is None:
        return None
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def read_bounded_response(response, limit: int, *, chunk_size: int = 64 * 1024) -> bytes:
    """Read at most ``limit`` bytes, including responses without a length header."""
    effective_limit = max(0, int(limit))
    declared_length = _response_content_length(response)
    if declared_length is not None and declared_length > effective_limit:
        raise ResponseBodyTooLarge

    chunks: list[bytes] = []
    size = 0
    while True:
        read_size = min(max(1, int(chunk_size)), effective_limit - size + 1)
        chunk = response.read(read_size)
        if not chunk:
            return b"".join(chunks)
        size += len(chunk)
        if size > effective_limit:
            raise ResponseBodyTooLarge
        chunks.append(chunk)


__all__ = ["ResponseBodyTooLarge", "read_bounded_response"]
