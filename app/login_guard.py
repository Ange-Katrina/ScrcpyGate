"""Login guard: signed proof-of-work challenges and policy constants.

Protocol v2, field naming aligned with ALTCHA (algorithm / challenge / salt /
signature / maxNumber) plus a protocol version and IP/username binding. Every
parameter that matters is HMAC-signed with a persistent server-side key, so the
client cannot tamper with the difficulty, the expiry or the binding — changing
any field fails the signature check before any hashing work is considered.

The challenge registry lives in SQLite (:mod:`app.storage`) so one-time use
survives restarts, and the HMAC key is generated once per install and stored in
the settings table.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import threading
import time

from . import storage
from .security import login_guard_config

log = logging.getLogger("webscrcpy.login_guard")

PROTOCOL_VERSION = 2
ALGORITHM = "SHA-256"
CHALLENGE_TTL_SECONDS = 120.0
MAX_NUMBER = 1_000_000
CHALLENGE_ISSUE_INTERVAL_SECONDS = 2.0
ISSUE_PRUNE_EVERY = 32

_ISSUE_LOCK = threading.Lock()
_LAST_ISSUE_AT: dict[str, float] = {}
_ISSUE_COUNT = 0


def challenge_ttl_seconds() -> float:
    return float(login_guard_config()["captcha_ttl_seconds"])


def challenge_issue_interval_seconds() -> float:
    return float(login_guard_config()["captcha_issue_interval_seconds"])


def bits_for_failures(failures: int) -> int:
    if failures >= 3:
        return 18
    if failures >= 2:
        return 16
    return 14


def canonical(item: dict) -> str:
    return "|".join(
        str(item[key])
        for key in ("v", "algorithm", "challenge", "salt", "bits", "maxNumber", "expires", "ip", "user")
    )


def sign_challenge(item: dict, key: bytes) -> str:
    return hmac.new(key, canonical(item).encode("utf-8"), hashlib.sha256).hexdigest()


def leading_zero_bits(digest: bytes) -> int:
    count = 0
    for byte in digest:
        if byte == 0:
            count += 8
            continue
        for bit in range(7, -1, -1):
            if byte & (1 << bit):
                return count
            count += 1
        return count
    return count


def issue_challenge(ip: str, username: str, failures: int) -> dict:
    """Issue a fresh signed challenge for ``ip``/``username``.

    Issuance is rate-limited per IP (two seconds) to keep the challenge endpoint
    from becoming a cheap CPU-free amplification target.
    """
    global _ISSUE_COUNT
    now = time.time()
    interval = challenge_issue_interval_seconds()
    with _ISSUE_LOCK:
        last = _LAST_ISSUE_AT.get(ip, 0.0)
        if interval > 0 and now - last < interval:
            return {"error": "challenge_rate_limited", "retry_after_ms": int((interval - (now - last)) * 1000)}
        _LAST_ISSUE_AT[ip] = now
        # Opportunistic pruning so the registry never grows without bound.
        if _ISSUE_COUNT % ISSUE_PRUNE_EVERY == 0:
            try:
                storage.prune_login_challenges(now)
            except Exception:  # pragma: no cover - storage failures are logged, not fatal
                log.warning("LOGIN_CHALLENGE_PRUNE_FAILED", exc_info=True)
        _ISSUE_COUNT += 1

    item = {
        "v": PROTOCOL_VERSION,
        "algorithm": ALGORITHM,
        "challenge": "ch_" + secrets.token_hex(8),
        "salt": secrets.token_hex(16),
        "bits": bits_for_failures(failures),
        "maxNumber": MAX_NUMBER,
        "expires": now + challenge_ttl_seconds(),
        "ip": ip,
        "user": username,
    }
    item["signature"] = sign_challenge(item, storage.ensure_login_guard_hmac_key())
    storage.create_login_challenge(
        item["challenge"], item["salt"], item["bits"], item["maxNumber"],
        item["expires"], ip, username,
    )
    return item


def verify_proof(proof: object, ip: str, username: str) -> dict:
    """Verify a submitted solution; the challenge is consumed atomically.

    Order follows the verified reference implementation: unknown -> reused ->
    binding -> expired -> signature -> number range -> hash difficulty.
    """
    if not isinstance(proof, dict):
        return {"ok": False, "reason": "proof_invalid"}
    challenge_id = str(proof.get("challenge") or "")
    if not challenge_id:
        return {"ok": False, "reason": "proof_invalid"}

    consumed = storage.consume_login_challenge(challenge_id)
    if consumed["status"] == "unknown":
        return {"ok": False, "reason": "challenge_unknown"}
    if consumed["status"] == "reused":
        return {"ok": False, "reason": "challenge_reused"}
    row = consumed["row"]
    if str(row["ip"]) != ip or str(row["username"]) != username:
        return {"ok": False, "reason": "challenge_binding_mismatch"}
    if time.time() > float(row["expires"]):
        return {"ok": False, "reason": "challenge_expired"}

    # 签名按服务端存储的挑战参数重算：客户端回显的任何字段都改不动难度、
    # 有效期与绑定关系 —— 篡改即 signature_mismatch（恒定时间比较）。
    try:
        expected = sign_challenge(
            {
                "v": PROTOCOL_VERSION,
                "algorithm": ALGORITHM,
                "challenge": str(row["challenge_id"]),
                "salt": str(row["salt"]),
                "bits": int(row["bits"]),
                "maxNumber": int(row["max_number"]),
                "expires": float(row["expires"]),
                "ip": str(row["ip"]),
                "user": str(row["username"]),
            },
            storage.ensure_login_guard_hmac_key(),
        )
    except (TypeError, ValueError):
        return {"ok": False, "reason": "proof_invalid"}
    if not hmac.compare_digest(expected, str(proof.get("signature") or "")):
        return {"ok": False, "reason": "signature_mismatch"}

    try:
        number = int(proof.get("number"))
    except (TypeError, ValueError):
        return {"ok": False, "reason": "number_invalid"}
    if number < 0 or number > int(row["max_number"]):
        return {"ok": False, "reason": "number_out_of_range"}

    digest = hashlib.sha256(f"{challenge_id}:{row['salt']}:{number}".encode("utf-8")).digest()
    zeros = leading_zero_bits(digest)
    if zeros < int(row["bits"]):
        return {"ok": False, "reason": "insufficient_work", "zero_bits": zeros, "required": int(row["bits"])}
    return {"ok": True, "reason": "ok", "zero_bits": zeros}
