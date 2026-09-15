"""Bounded request-body reading shared by HTTP and ALAS proxy boundaries."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator


def _bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


REQUEST_BODY_IDLE_TIMEOUT_SECONDS = _bounded_env_int(
    "REQUEST_BODY_IDLE_TIMEOUT_SECONDS",
    15,
    1,
    300,
)


class RequestBodyTooLarge(Exception):
    """The request body exceeded its configured byte limit."""


class RequestBodyIdleTimeout(Exception):
    """No next request-body chunk arrived within the idle budget."""


def _declared_content_length(request) -> int | None:
    raw = request.headers.get("content-length")
    if raw is None:
        return None
    try:
        value = int(raw.strip())
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


async def _next_chunk(iterator: AsyncIterator[bytes], timeout: float) -> bytes:
    """Wait for one body chunk while allowing normal task cancellation through."""
    try:
        return await asyncio.wait_for(iterator.__anext__(), timeout=timeout)
    except TimeoutError as exc:
        raise RequestBodyIdleTimeout from exc


async def read_request_body_limited(
    request,
    limit: int,
    *,
    idle_timeout: float = REQUEST_BODY_IDLE_TIMEOUT_SECONDS,
) -> bytes:
    """Read a streamed request body with byte and per-chunk idle limits."""
    effective_limit = max(0, int(limit))
    declared_length = _declared_content_length(request)
    if declared_length is not None and declared_length > effective_limit:
        raise RequestBodyTooLarge

    timeout = max(1e-3, float(idle_timeout))
    chunks: list[bytes] = []
    size = 0
    iterator = request.stream().__aiter__()
    while True:
        try:
            chunk = await _next_chunk(iterator, timeout)
        except StopAsyncIteration:
            break
        size += len(chunk)
        if size > effective_limit:
            raise RequestBodyTooLarge
        if chunk:
            chunks.append(chunk)
    return b"".join(chunks)


__all__ = [
    "REQUEST_BODY_IDLE_TIMEOUT_SECONDS",
    "RequestBodyIdleTimeout",
    "RequestBodyTooLarge",
    "read_request_body_limited",
]
