"""Product version shared by the runtime, page renderer and image builds."""

from __future__ import annotations

import os
from pathlib import Path
import re

VERSION_FILE = Path(__file__).resolve().parent.parent / "VERSION"
_VERSION_RE = re.compile(
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
)


def validate_version(value: str) -> str:
    """Accept a Docker-tag-compatible SemVer, without a leading v."""
    match = _VERSION_RE.fullmatch(value)
    if not match or len(value) > 127:
        raise ValueError("Version must use MAJOR.MINOR.PATCH[-prerelease] without a leading v")
    prerelease = match.group(4)
    if prerelease and any(part.isdigit() and len(part) > 1 and part.startswith("0") for part in prerelease.split(".")):
        raise ValueError("Numeric prerelease identifiers must not have leading zeroes")
    return value


def get_version() -> str:
    """Prefer the checked-in/baked version over stale deployment environment state."""
    try:
        return validate_version(VERSION_FILE.read_text(encoding="utf-8").strip().removeprefix("v"))
    except (OSError, UnicodeError, ValueError):
        # Compatibility with old deployments that only supplied an environment marker.
        try:
            return validate_version(os.environ.get("SCRCPYGATE_VERSION", "").strip().removeprefix("v"))
        except ValueError:
            return "dev"


if __name__ == "__main__":
    print(get_version())
