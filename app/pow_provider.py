"""Trusted, separately installed PoW adapters. No third-party adapter is bundled.

An adapter is executable server code, installed by the operator, never selected
by a login request. The login guard owns signing, binding and one-time use.
"""
from __future__ import annotations

from functools import lru_cache
import hashlib
import hmac
from importlib.metadata import entry_points
import os
from pathlib import Path
import re
import secrets
from typing import Protocol

DOMAIN = "ScrcpyGate:login:pow:v1"
PUZZLES = 4


class PowProvider(Protocol):
    api_version: int
    name: str
    static_dir: Path | None

    def issue(self, *, challenge_id: str, bits: int, expires_at: int) -> dict: ...

    def verify(self, *, challenge_id: str, parameters: dict, solution: object) -> bool: ...


class BuiltinPow:
    api_version = 1
    name = "builtin"
    static_dir = None

    def issue(self, *, challenge_id: str, bits: int, expires_at: int) -> dict:
        # Four uniformly sampled bounded preimages have about 2**bits total
        # expected hashes, half the relative standard deviation of one puzzle.
        maximum = (1 << (bits - 1)) - 1
        salt = secrets.token_hex(16)
        targets = [
            hashlib.sha256(
                f"{DOMAIN}:{challenge_id}:{salt}:{index}:{secrets.randbelow(maximum + 1)}".encode("ascii")
            ).hexdigest()
            for index in range(PUZZLES)
        ]
        return {"algorithm": "SHA-256", "salt": salt, "max_number": maximum, "targets": targets}

    def verify(self, *, challenge_id: str, parameters: dict, solution: object) -> bool:
        if not isinstance(solution, list) or len(solution) != PUZZLES:
            return False
        maximum = parameters["max_number"]
        salt = parameters["salt"]
        for index, number in enumerate(solution):
            if type(number) is not int or not 0 <= number <= maximum:
                return False
            digest = hashlib.sha256(f"{DOMAIN}:{challenge_id}:{salt}:{index}:{number}".encode("ascii")).hexdigest()
            if not hmac.compare_digest(digest, parameters["targets"][index]):
                return False
        return True


@lru_cache(maxsize=1)
def get_provider() -> PowProvider:
    """Resolve once at startup. A typo/missing adapter fails closed."""
    name = os.getenv("LOGIN_POW_PROVIDER", "builtin").strip()
    if name == "builtin":
        return BuiltinPow()
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", name):
        raise RuntimeError("Invalid LOGIN_POW_PROVIDER")
    matches = tuple(entry_points(group="scrcpygate.pow", name=name))
    if len(matches) != 1:
        raise RuntimeError("Configured PoW adapter is missing or ambiguous; install its extension package")
    provider = matches[0].load()()
    if (getattr(provider, "api_version", None) != 1 or getattr(provider, "name", None) != name
            or not callable(getattr(provider, "issue", None)) or not callable(getattr(provider, "verify", None))):
        raise RuntimeError("PoW adapter does not implement the ScrcpyGate v1 extension API")
    assets = Path(provider.static_dir).resolve()
    if not assets.is_dir() or not (assets / "client.js").is_file():
        raise RuntimeError("PoW adapter must include a dedicated static directory with client.js")
    return provider
