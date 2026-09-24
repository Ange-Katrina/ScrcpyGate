"""Administrator-only update availability check.

This route is deliberately read-only: it answers "is there a newer published
release, and which host command applies it" and performs no download, no file
write and no container action.  Applying an update stays on the host
(``deploy.sh --update``), because the application runs unprivileged inside a
read-only image with no Docker access.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, Query, Request

from .. import security, update_check
from ..logging_config import log_event

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/admin/update-check")
async def admin_update_check(
    request: Request,
    refresh: int = Query(default=0, ge=0, le=1),
    channel: Literal["auto", "stable", "dev", "edge"] = "auto",
):
    """Report the current version and the newest published release (read-only)."""
    admin = security.require_admin(request)
    # Run bounded registry requests outside the event loop.
    payload = await asyncio.to_thread(update_check.check_for_update, force=bool(refresh), channel=channel)
    if refresh:
        log_event(
            log,
            "admin.update_check",
            username=str(admin.get("username") or ""),
            ok=payload.get("ok"),
            update_available=payload.get("update_available"),
        )
    return payload
