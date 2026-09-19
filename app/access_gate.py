"""访问网关判定（BAN → GEO）。

**固定顺序**（任务书 §3；顺序本身是契约，不要调整）：

1. 既有的来源/边界校验（``security.enforce_http_boundary``）——不受本模块影响；
2. 健康探针例外：**直连**环回地址 + ``GET``/``HEAD`` + 精确路径 ``/healthz``；
3. 手动 IP 封禁（BAN）→ 403 ``ip_banned``（临时封禁带 ``Retry-After``）；
4. 地域限制（GEO，E 阶段注册求值器）→ 403 ``geo_access_denied`` / 观察模式放行；
5. 既有鉴权与业务逻辑（完全不变）。

两条不可动摇的规则：

* GEO 的**允许**绝不授予任何业务权限，只表示「地域这一层不再拦」；
* GEO 的例外（allow_cidrs 等）**不能绕过 BAN**——BAN 先判，命中即拒绝。

本模块是**纯判定**：不写库、不记日志、不做清理，方便在 HTTP 中间件与 WebSocket 握手两处复用
同一份顺序（两处顺序不一致是最容易出现的越权口子）。
"""

from __future__ import annotations

import ipaddress
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

DECISION_ALLOW = "allow"
DECISION_BAN = "deny_ban"
DECISION_GEO = "deny_geo"
DECISION_GEO_OBSERVE = "observe_geo"
DECISION_GEO_UNAVAILABLE = "db_unavailable"

HEALTH_PATH = "/healthz"
HEALTH_METHODS = frozenset({"GET", "HEAD"})

_STATUS_BY_DECISION = {
    DECISION_BAN: 403,
    DECISION_GEO: 403,
    DECISION_GEO_UNAVAILABLE: 503,
}


@dataclass(frozen=True)
class GateDecision:
    """一次判定的结果。``reason`` 是给界面用的稳定码，不是给人看的错误串。"""

    allowed: bool
    decision: str = DECISION_ALLOW
    status_code: int = 200
    reason: str = ""
    message_key: str = ""
    retry_after: int | None = None
    # GEO 的观察/判定结果，供访问记录（VIS）落库；BAN 路径不带国家。
    country: str = ""
    geo_db_epoch: int | None = None
    extra: dict = field(default_factory=dict)

    def to_json(self, translate: Callable[[str], str] | None = None) -> dict:
        payload: dict[str, Any] = {"detail": self.reason or self.decision}
        if self.message_key:
            text = translate(self.message_key) if translate else self.message_key
            payload["detail"] = text or self.reason or self.decision
            payload["code"] = self.reason or self.decision
        if self.retry_after is not None:
            payload["retry_after"] = int(self.retry_after)
        return payload


ALLOW = GateDecision(allowed=True)


def _is_loopback(raw: str) -> bool:
    try:
        return ipaddress.ip_address(str(raw or "").strip()).is_loopback
    except ValueError:
        return False


def health_probe_exempt(*, peer_ip: str, method: str, path: str, source_ip: str = "", forwarded: bool = False) -> bool:
    """是否命中健康探针例外。

    必须同时满足：**直连**对端是环回地址（不看 ``X-Forwarded-For``，否则任何人都能用
    一个伪造的转发头给自己套上「不可封禁」的豁免）、方法是 GET/HEAD、路径精确等于
    ``/healthz``（``/healthz/../../`` 这类都不算）。
    """
    if str(method or "").upper() not in HEALTH_METHODS:
        return False
    if str(path or "") != HEALTH_PATH:
        return False
    return not forwarded and _is_loopback(peer_ip) and _is_loopback(source_ip)


# GEO 求值器：E 阶段由 ``app.geo_access`` 注册。签名
# ``(source_ip) -> GateDecision | None``，返回 None 表示「地域层不表态」。
_geo_evaluator: Callable[[str], GateDecision | None] | None = None


def set_geo_evaluator(evaluator: Callable[[str], GateDecision | None] | None) -> None:
    global _geo_evaluator
    _geo_evaluator = evaluator


def geo_evaluator() -> Callable[[str], GateDecision | None] | None:
    return _geo_evaluator


def evaluate(
    *,
    source_ip: str,
    method: str,
    path: str,
    peer_ip: str = "",
    forwarded: bool = False,
    now: int | None = None,
) -> GateDecision:
    """按固定顺序判定一次请求。健康探针例外优先于 BAN（否则容器自检会被自己封掉）。"""
    if health_probe_exempt(peer_ip=peer_ip, method=method, path=path, source_ip=source_ip, forwarded=forwarded):
        return ALLOW

    from . import ip_ban

    hit = ip_ban.is_banned(source_ip, now=now)
    if hit is not None:
        return GateDecision(
            allowed=False,
            decision=DECISION_BAN,
            status_code=_STATUS_BY_DECISION[DECISION_BAN],
            reason="ip_banned",
            message_key="server.error.ip_banned",
            retry_after=hit.get("retry_after"),
            extra={"permanent": bool(hit.get("permanent"))},
        )

    evaluator = _geo_evaluator
    if evaluator is None:
        return ALLOW
    try:
        geo = evaluator(source_ip)
    except Exception:  # pragma: no cover - 求值器自身异常不能放开访问
        # 宁可拒绝也不能「因为地域模块出错而放行」：这是安全默认值。
        return GateDecision(
            allowed=False,
            decision=DECISION_GEO_UNAVAILABLE,
            status_code=_STATUS_BY_DECISION[DECISION_GEO_UNAVAILABLE],
            reason="geo_database_unavailable",
            message_key="server.error.geo_database_unavailable",
        )
    if geo is None:
        return ALLOW
    return geo


__all__ = [
    "ALLOW",
    "DECISION_ALLOW",
    "DECISION_BAN",
    "DECISION_GEO",
    "DECISION_GEO_OBSERVE",
    "DECISION_GEO_UNAVAILABLE",
    "GateDecision",
    "evaluate",
    "geo_evaluator",
    "health_probe_exempt",
    "set_geo_evaluator",
]
