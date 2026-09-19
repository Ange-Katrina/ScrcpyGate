"""Versioned PoW envelope; binding/replay protection stays outside adapters."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import math
import re
import secrets
import threading
import time

from . import storage
from .pow_provider import get_provider
from .security import login_guard_config

log = logging.getLogger("webscrcpy.login_guard")

CHALLENGE_TTL_SECONDS = 120.0
MAX_PAYLOAD_LENGTH = 8192
MAX_CHALLENGE_BYTES = 4096
CHALLENGE_ISSUE_INTERVAL_SECONDS = 2.0
ISSUE_PRUNE_EVERY = 32
MAX_ISSUE_SOURCES = 10_000

_ISSUE_LOCK = threading.Lock()
_LAST_ISSUE_AT: dict[str, float] = {}
_ISSUE_COUNT = 0


def challenge_ttl_seconds() -> float:
    return float(login_guard_config()["captcha_ttl_seconds"])


def challenge_issue_interval_seconds() -> float:
    return float(login_guard_config()["captcha_issue_interval_seconds"])


def bits_for_failures(failures: int) -> int:
    """难度阶梯：首题 ``base`` bits，之后每多失败一次加 ``step``，封顶 ``max``。

    阶梯与「触发人机验证的失败次数」绑定：第 ``captcha_after_failures`` 次失败拿到的
    就是首题难度。默认 14/2/18 与历史硬编码阶梯逐点一致（0-1 次失败 14 bits、
    2 次 16 bits、3 次及以上 18 bits），所以老配置的行为不会变。
    """
    config = login_guard_config()
    base = int(config["captcha_bits_base"])
    ceiling = max(base, int(config["captcha_bits_max"]))
    step = max(0, int(config["captcha_bits_step"]))
    after = max(1, int(config["captcha_after_failures"]))
    index = max(0, int(failures) - after)
    return max(base, min(ceiling, base + step * index))


def issue_challenge(ip: str, username: str, failures: int) -> dict:
    """Issue a fresh signed challenge for ``ip``/``username``.

    Issuance is rate-limited per IP (two seconds) to keep the challenge endpoint
    from becoming a cheap CPU-free amplification target.
    """
    global _ISSUE_COUNT
    now = time.time()
    interval = challenge_issue_interval_seconds()
    with _ISSUE_LOCK:
        issued_at = time.monotonic()
        # Entries stay in issuance order. Expiry uses a monotonic clock, not
        # wall time, and capacity pressure never evicts a live rate limit.
        while _LAST_ISSUE_AT:
            oldest = next(iter(_LAST_ISSUE_AT))
            if interval > 0 and issued_at - _LAST_ISSUE_AT[oldest] < interval:
                break
            _LAST_ISSUE_AT.pop(oldest)
        last = _LAST_ISSUE_AT.get(ip)
        if interval > 0:
            if last is not None:
                retry_ms = math.ceil((interval - (issued_at - last)) * 1000)
                return {"error": "challenge_rate_limited", "retry_after_ms": max(1, retry_ms)}
            if len(_LAST_ISSUE_AT) >= MAX_ISSUE_SOURCES:
                oldest_at = next(iter(_LAST_ISSUE_AT.values()))
                retry_ms = math.ceil((interval - (issued_at - oldest_at)) * 1000)
                return {"error": "challenge_rate_limited", "retry_after_ms": max(1, retry_ms)}
            _LAST_ISSUE_AT[ip] = issued_at
        # SQLite challenge lifetime is separate from the source cooldown.
        if _ISSUE_COUNT % ISSUE_PRUNE_EVERY == 0:
            try:
                storage.prune_login_challenges(now)
            except Exception:  # pragma: no cover - storage failures are logged, not fatal
                log.warning("LOGIN_CHALLENGE_PRUNE_FAILED", exc_info=True)
        _ISSUE_COUNT += 1

    bits = bits_for_failures(failures)
    expires = int(now + challenge_ttl_seconds())
    provider = get_provider()
    challenge_id = secrets.token_hex(16)
    try:
        parameters = provider.issue(challenge_id=challenge_id, bits=bits, expires_at=expires)
        if not isinstance(parameters, dict):
            raise ValueError("parameters")
        challenge = {"version": 1, "provider": provider.name, "purpose": "login", "id": challenge_id,
                     "expires_at": expires, "parameters": parameters}
        encoded = _canonical(challenge)
        if len(encoded) > MAX_CHALLENGE_BYTES:
            raise ValueError("challenge too large")
    except Exception:
        log.warning("POW_PROVIDER_ISSUE_FAILED")
        return {"error": "provider_unavailable"}
    signature = hmac.new(storage.ensure_login_guard_hmac_key(), b"ScrcpyGate:pow:envelope:v1\x00" + encoded,
                         hashlib.sha256).hexdigest()
    challenge["signature"] = signature
    if not storage.create_pow_challenge(challenge_id, provider.name, signature, expires, ip, username):
        return {"error": "challenge_rate_limited", "retry_after_ms": 5000}
    return challenge


def _canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")


def _unique_object(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def verify_proof(proof: object, ip: str, username: str) -> dict:
    """Authenticate the envelope before adapter work; consume only on success."""
    if not isinstance(proof, str) or not proof or len(proof) > MAX_PAYLOAD_LENGTH:
        return {"ok": False, "reason": "proof_invalid"}
    try:
        data = json.loads(base64.b64decode(proof, validate=True), object_pairs_hook=_unique_object)
        if not isinstance(data, dict) or set(data) != {"challenge", "solution"}:
            raise ValueError("payload shape")
        challenge, solution = data["challenge"], data["solution"]
        if not isinstance(challenge, dict) or set(challenge) != {
                "version", "provider", "purpose", "id", "expires_at", "parameters", "signature"}:
            raise ValueError("payload shape")
        if not isinstance(challenge["parameters"], dict):
            raise ValueError("parameters")
        if type(challenge["version"]) is not int or challenge["version"] != 1 or challenge["purpose"] != "login":
            raise ValueError("protocol")
        if challenge["provider"] != get_provider().name:
            raise ValueError("provider")
        if not isinstance(challenge["id"], str) or not re.fullmatch(r"[0-9a-f]{32}", challenge["id"]):
            raise ValueError("id")
        if type(challenge["expires_at"]) is not int or not 0 < challenge["expires_at"] < 2**53:
            raise ValueError("expiry")
        if not isinstance(challenge.get("signature"), str) or not re.fullmatch(r"[0-9a-f]{64}", challenge["signature"]):
            raise ValueError("signature")
        signature = challenge["signature"]
        unsigned = {key: value for key, value in challenge.items() if key != "signature"}
        encoded = _canonical(unsigned)
        if len(encoded) > MAX_CHALLENGE_BYTES:
            raise ValueError("challenge size")
        # Reject NaN/Infinity even if they occur only in the submitted solution.
        _canonical(data)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, binascii.Error, OverflowError):
        return {"ok": False, "reason": "proof_invalid"}
    expected = hmac.new(storage.ensure_login_guard_hmac_key(), b"ScrcpyGate:pow:envelope:v1\x00" + encoded,
                        hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return {"ok": False, "reason": "signature_mismatch"}
    row = storage.get_pow_challenge(challenge["id"])
    if row is None:
        return {"ok": False, "reason": "challenge_unknown"}
    if row["used"]:
        return {"ok": False, "reason": "challenge_reused"}
    if str(row["ip"]) != ip or str(row["username"]) != username:
        return {"ok": False, "reason": "challenge_binding_mismatch"}
    if time.time() >= float(row["expires"]) or time.time() >= challenge["expires_at"]:
        return {"ok": False, "reason": "challenge_expired"}
    if not hmac.compare_digest(signature, row["fingerprint"]) or challenge["provider"] != row["provider"]:
        return {"ok": False, "reason": "signature_mismatch"}
    try:
        valid = get_provider().verify(challenge_id=challenge["id"], parameters=challenge["parameters"], solution=solution)
    except Exception:
        log.warning("POW_PROVIDER_VERIFY_FAILED")
        return {"ok": False, "reason": "provider_unavailable"}
    if valid is not True:
        return {"ok": False, "reason": "insufficient_work"}
    if not storage.consume_pow_challenge(challenge["id"], signature, ip, username):
        return {"ok": False, "reason": "challenge_reused_or_expired"}
    return {"ok": True, "reason": "ok"}
