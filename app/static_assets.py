"""Shared immutable static-asset URL helpers."""

import hashlib
from functools import lru_cache
from pathlib import Path


STATIC_ROOT = Path(__file__).resolve().parent.parent / "static"


@lru_cache(maxsize=256)
def asset_version(relative_path: str) -> str:
    """Return a deterministic short SHA-256 version for one static asset."""
    normalized = str(relative_path).replace("\\", "/").lstrip("/")
    path = STATIC_ROOT / normalized
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        digest = "0" * 12
    return digest[:12]


def asset_url(relative_path: str) -> str:
    """Build a canonical immutable ``/static`` URL for an asset."""
    normalized = str(relative_path).replace("\\", "/").lstrip("/")
    return f"/static/{normalized}?v={asset_version(normalized)}"
