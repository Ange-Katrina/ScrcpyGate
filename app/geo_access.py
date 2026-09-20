"""地域限制（GEO）：MMDB 读取生命周期、策略与判定。

职责与顺序（与 `app.access_gate` 的契约）：

* 本模块只回答「这个来源地址在地域这一层是否放行」，**永不授予业务权限**；
* BAN 在顺序上先于 GEO，所以 `allow_cidrs` 例外也绕不过封禁；
* enforce 且库不可用时 **fail-closed**（503 `geo_database_unavailable`），绝不因为读不到库就放行；
* observe 只记录「本来会被拒绝」，不拦任何请求。

地址分类：

* ``public`` —— 交给 MMDB 查国家；查不到国家记 ``unknown``；
* ``private`` —— 环回/私网/链路本地/CGNAT 等不可定位地址，同样按 ``unknown`` 处理（设计如此：
  「私网/环回归 unknown」）。**这是启用 enforce 时最容易踩的坑**：内网访问会落到
  ``unknown_action``（默认 deny），因此界面必须显式提示，并提供 `allow_cidrs` 例外。
* ``invalid`` —— 解析不出来（例如直连对端是测试字符串），按 unknown 处理。

配置优先级（与 `LOGIN_GUARD_FIELDS` 一致）：`DB/UI > env 默认 > 内置默认`；
唯一反转是 `GEO_ENFORCE_DISABLED=true`（env 强制 off，用于误锁恢复）。
"""

from __future__ import annotations

import ipaddress
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import storage
from .access_gate import (
    DECISION_GEO,
    DECISION_GEO_OBSERVE,
    DECISION_GEO_UNAVAILABLE,
    GateDecision,
)

log = logging.getLogger("webscrcpy.geo")

GEO_DIR_ENV = "GEO_DATABASE_DIR"
DEFAULT_GEO_DIR = "/app/data/geoip"
FORCE_OFF_ENV = "GEO_ENFORCE_DISABLED"
LICENSE_KEY_ENV = "GEO_LICENSE_KEY"
LICENSE_KEY_PREVIOUS_ENV = "GEO_LICENSE_KEY_PREVIOUS"

MODE_OFF = "off"
MODE_OBSERVE = "observe"
MODE_ENFORCE = "enforce"
GEO_MODES = (MODE_OFF, MODE_OBSERVE, MODE_ENFORCE)

UNKNOWN_DENY = "deny"
UNKNOWN_ALLOW = "allow"
GEO_UNKNOWN_ACTIONS = (UNKNOWN_DENY, UNKNOWN_ALLOW)

# 设置键（DB/UI）
MODE_SETTING = "geo_mode"
COUNTRIES_SETTING = "geo_allowed_countries"
UNKNOWN_SETTING = "geo_unknown_action"
ALLOW_CIDRS_SETTING = "geo_allow_cidrs"
STATE_SETTING = "_geo_last_state"

DEFAULT_ALLOWED_COUNTRIES = ("CN",)
# 允许列表里的国家数量上限：防止把整份 ISO 清单粘进来（也避免设置值无限增长）。
MAX_ALLOWED_COUNTRIES = 250
MAX_ALLOW_CIDRS = 64
# 策略缓存 TTL：设置改动后最多 5 秒生效（本进程内改设置会主动失效）。
POLICY_TTL_SECONDS = 5.0
# 库文件：目录里最新的 *.mmdb 生效（原子替换会换 inode，因此每次装载都按路径重新打开）。
MMDB_SUFFIX = ".mmdb"
MAX_DB_BYTES = 512 * 1024 * 1024
MAX_DATABASE_AGE_SECONDS = 30 * 86400
READER_CHECK_SECONDS = 2.0
COUNTRY_DATABASE_TYPES = ("GeoLite2-Country", "GeoIP2-Country")
CITY_DATABASE_TYPES = ("GeoLite2-City", "GeoIP2-City")
ACCEPTED_DATABASE_TYPES = COUNTRY_DATABASE_TYPES + CITY_DATABASE_TYPES


@dataclass(frozen=True)
class GeoPolicy:
    mode: str = MODE_OFF
    allowed_countries: frozenset[str] = field(default_factory=lambda: frozenset(DEFAULT_ALLOWED_COUNTRIES))
    unknown_action: str = UNKNOWN_DENY
    allow_cidrs: tuple = ()
    forced_off: bool = False
    source: str = "default"

    @property
    def enforcing(self) -> bool:
        return self.mode == MODE_ENFORCE and not self.forced_off

    @property
    def observing(self) -> bool:
        return self.mode == MODE_OBSERVE and not self.forced_off

    @property
    def active(self) -> bool:
        return self.enforcing or self.observing


@dataclass(frozen=True)
class GeoLookup:
    status: str  # public / private / invalid / unknown / unavailable
    country: str = ""
    epoch: int = 0
    location: dict = field(default_factory=dict)


_lock = threading.RLock()
_reader = None
_reader_path: str = ""
_reader_epoch = 0
_reader_type = ""
_reader_error: str = ""
_reader_opened_at = 0.0
_reader_fingerprint = None
_reader_checked_at = 0.0
_policy: GeoPolicy | None = None
_policy_loaded_at = 0.0
# 统计（只用于诊断展示，不外泄地址）
_stats = {"lookups": 0, "database_loads": 0, "database_errors": 0, "denied": 0, "observed": 0}


def _env_text(name: str, default: str = "") -> str:
    return str(os.environ.get(name, default) or default).strip()


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(str(os.environ.get(name, "") or "").strip() or default)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


def normalize_country(value: object) -> str:
    """两位 ISO 国家码（大写）；``HK``/``MO``/``TW`` 与 ``CN`` 是彼此独立的值。"""
    text = str(value or "").strip().upper()
    if len(text) != 2 or not text.isascii() or not text.isalpha():
        return ""
    return text


def parse_country_list(raw: object) -> tuple[str, ...]:
    items = raw if isinstance(raw, (list, tuple, set)) else str(raw or "").replace("，", ",").split(",")
    parsed: list[str] = []
    for item in items:
        code = normalize_country(item)
        if code and code not in parsed:
            parsed.append(code)
        if len(parsed) >= MAX_ALLOWED_COUNTRIES:
            break
    return tuple(parsed)


def parse_cidrs(raw: object) -> tuple:
    """解析运维例外 CIDR 列表；非法项直接丢弃（宁可少一条例外，也不要整段策略失效）。"""
    items = raw if isinstance(raw, (list, tuple, set)) else str(raw or "").replace("，", ",").split(",")
    nets = []
    for item in items:
        text = str(item or "").strip()
        if not text:
            continue
        try:
            network = ipaddress.ip_network(text, strict=False)
            if network.prefixlen == 0:
                raise ValueError("universal exception")
            nets.append(network)
        except ValueError:
            log.warning("GEO_ALLOW_CIDR_IGNORED")
        if len(nets) >= MAX_ALLOW_CIDRS:
            break
    return tuple(nets)


def classify_ip(raw: object) -> tuple[str, str]:
    """返回 (分类, 规范化地址)：``public`` / ``private`` / ``invalid``。"""
    text = str(raw or "").strip()
    if not text:
        return "invalid", ""
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return "invalid", ""
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if address.is_loopback or address.is_unspecified or address.is_link_local or address.is_multicast:
        return "private", str(address)
    if not address.is_global:
        # 覆盖 RFC1918、CGNAT（100.64/10）、文档保留段等：都无法定位到国家。
        return "private", str(address)
    return "public", str(address)


# ---------------------------------------------------------------- MMDB 读取


def database_dir() -> Path:
    return Path(_env_text(GEO_DIR_ENV, DEFAULT_GEO_DIR) or DEFAULT_GEO_DIR)


def latest_database() -> Path | None:
    """Collect each stat once; external atomic replacements may race directory scans."""
    candidates = []
    try:
        for entry in database_dir().iterdir():
            try:
                stat = entry.stat()
                if entry.is_file() and entry.suffix.lower() == MMDB_SUFFIX and stat.st_size <= MAX_DB_BYTES:
                    candidates.append((stat.st_mtime_ns, entry.name, entry))
            except OSError:
                continue
    except OSError:
        return None
    return max(candidates)[2] if candidates else None


def _import_maxminddb():
    try:
        import maxminddb  # type: ignore
    except Exception:  # pragma: no cover - 依赖缺失时按「库不可用」处理
        return None
    # maxminddb 3.x 的入口是 open_database；旧版是 open_database / Reader。
    if hasattr(maxminddb, "open_database"):
        return maxminddb.open_database
    if hasattr(maxminddb, "Reader"):  # pragma: no cover - 兼容旧版
        return maxminddb.Reader
    return None


def _ensure_reader() -> tuple[object | None, int, str]:
    """Open a validated Country/City reader; policy always uses country.iso_code."""
    global _reader, _reader_path, _reader_epoch, _reader_error, _reader_opened_at
    global _reader_fingerprint, _reader_checked_at, _reader_type
    with _lock:
        moment = time.time()
        if _reader is None and _reader_error and time.monotonic() - _reader_checked_at < READER_CHECK_SECONDS:
            return None, 0, _reader_error
        if _reader is not None and time.monotonic() - _reader_checked_at < READER_CHECK_SECONDS:
            if not (0 < _reader_epoch <= moment + 86400 and moment - _reader_epoch <= MAX_DATABASE_AGE_SECONDS):
                return None, _reader_epoch, "database_expired"
            return _reader, _reader_epoch, _reader_error
        _reader_checked_at = time.monotonic()
        path = latest_database()
        if path is None:
            close()
            _reader_checked_at = time.monotonic()
            _reader_error = "database_missing"
            return None, 0, _reader_error
        try:
            stat = path.stat()
            fingerprint = (str(path), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
        except OSError:
            _reader_error = "database_unreadable"
            return None, 0, _reader_error
        if _reader is not None and fingerprint == _reader_fingerprint:
            if 0 < _reader_epoch <= moment + 86400 and moment - _reader_epoch <= MAX_DATABASE_AGE_SECONDS:
                _reader_error = ""
                return _reader, _reader_epoch, ""
            _reader_error = "database_expired"
            return None, _reader_epoch, _reader_error
        opener = _import_maxminddb()
        if opener is None:
            _reader_error = "dependency_missing"
            return None, 0, _reader_error
        candidate = None
        try:
            candidate = opener(str(path))
            metadata = candidate.metadata()
            epoch = int(metadata.get("build_epoch", 0) if isinstance(metadata, dict) else metadata.build_epoch)
            db_type = str(metadata.get("database_type", "") if isinstance(metadata, dict) else metadata.database_type)
            if db_type not in ACCEPTED_DATABASE_TYPES:
                raise ValueError("database_type")
            if not (0 < epoch <= moment + 86400 and moment - epoch <= MAX_DATABASE_AGE_SECONDS):
                candidate.close()
                _reader_error = "database_expired"
                return None, epoch, _reader_error
        except Exception:
            if candidate is not None:
                try:
                    candidate.close()
                except Exception:
                    pass
            _stats["database_errors"] += 1
            _reader_error = "database_unreadable"
            return None, 0, _reader_error
        previous = _reader
        _reader, _reader_path, _reader_epoch = candidate, str(path), epoch
        _reader_type = db_type
        _reader_fingerprint = fingerprint
        _reader_error = ""
        _reader_opened_at = moment
        _stats["database_loads"] += 1
        if previous is not None:
            try:
                previous.close()
            except Exception:
                pass
        return _reader, epoch, ""


def _location_names(node: object) -> dict:
    """Keep bounded display names only; no coordinates, postal data or network traits."""
    names = node.get("names") if isinstance(node, dict) else None
    if not isinstance(names, dict):
        return {}
    return {locale: " ".join(names[locale].split())[:100] for locale in ("zh-CN", "en")
            if isinstance(names.get(locale), str) and names[locale].strip()}


def _location(record: dict) -> dict:
    subdivisions = record.get("subdivisions")
    return {
        "country_names": _location_names(record.get("country")),
        "subdivisions": [_location_names(node) for node in subdivisions[:2]] if isinstance(subdivisions, list) else [],
        "city_names": _location_names(record.get("city")),
    }


def lookup(raw: object, *, include_location: bool = False) -> GeoLookup:
    """A missing country is distinct from an unavailable database."""
    _stats["lookups"] += 1
    kind, address = classify_ip(raw)
    with _lock:
        reader, epoch, error = _ensure_reader()
        if reader is None or error:
            return GeoLookup(status="unavailable", epoch=epoch)
        if kind != "public":
            return GeoLookup(status=kind, epoch=epoch)
        try:
            record = reader.get(address)
        except Exception:
            _stats["database_errors"] += 1
            return GeoLookup(status="unavailable", epoch=epoch)
    country = ""
    if isinstance(record, dict):
        node = record.get("country") or {}
        if isinstance(node, dict):
            country = normalize_country(node.get("iso_code"))
    return GeoLookup(status="public" if country else "unknown", country=country, epoch=epoch,
                     location=_location(record) if country and include_location else {})


def access_geo_lookup(raw: object) -> tuple[str, int | None]:
    """VIS 用的 (country, epoch)：库不可用或查不到国家时返回空国家。"""
    result = lookup(raw)
    if result.status != "public" or not result.country:
        return "", (result.epoch or None)
    return result.country, (result.epoch or None)


# ---------------------------------------------------------------- 策略


def _read_policy() -> GeoPolicy:
    force_off = str(os.environ.get(FORCE_OFF_ENV, "") or "").strip().lower() in ("1", "true", "yes", "on")
    mode = str(storage.get_setting(MODE_SETTING, "") or "").strip().lower()
    source = "db"
    if mode not in GEO_MODES:
        mode = _env_text("GEO_MODE", MODE_OFF).lower()
        source = "env"
        if mode not in GEO_MODES:
            mode = MODE_OFF
            source = "default"
    countries_raw = storage.get_setting(COUNTRIES_SETTING, None)
    if countries_raw is None:
        countries_raw = _env_text("GEO_ALLOWED_COUNTRIES", "CN")
    countries = parse_country_list(countries_raw) or DEFAULT_ALLOWED_COUNTRIES
    unknown = str(storage.get_setting(UNKNOWN_SETTING, None) or _env_text("GEO_UNKNOWN_ACTION", UNKNOWN_DENY)).lower()
    if unknown not in GEO_UNKNOWN_ACTIONS:
        unknown = UNKNOWN_DENY
    cidrs_raw = storage.get_setting(ALLOW_CIDRS_SETTING, None)
    if cidrs_raw is None:
        cidrs_raw = _env_text("GEO_ALLOW_CIDRS", "")
    return GeoPolicy(
        mode=mode,
        allowed_countries=frozenset(countries),
        unknown_action=unknown,
        allow_cidrs=parse_cidrs(cidrs_raw),
        forced_off=force_off,
        source=source,
    )


def policy(*, force: bool = False) -> GeoPolicy:
    global _policy, _policy_loaded_at
    now = time.monotonic()
    with _lock:
        if force or _policy is None or now - _policy_loaded_at >= POLICY_TTL_SECONDS:
            try:
                _policy = _read_policy()
            except Exception:  # pragma: no cover - 读设置失败时保持上一次策略
                log.exception("GEO_POLICY_READ_FAILED")
                if _policy is None:
                    _policy = GeoPolicy(mode=MODE_OFF)
            _policy_loaded_at = now
        return _policy


def invalidate_policy() -> None:
    """设置改动后立刻生效（本进程内）。"""
    global _policy_loaded_at
    with _lock:
        _policy_loaded_at = 0.0


def _in_allow_cidrs(address: str, nets: tuple) -> bool:
    if not nets or not address:
        return False
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(parsed in net for net in nets)


def _decision(country: str, *, allow: bool, status_code: int, mode: str) -> GateDecision:
    if mode == MODE_OBSERVE:
        return GateDecision(allowed=True, decision=DECISION_GEO_OBSERVE, country=country)
    if allow:
        return GateDecision(allowed=True, decision=DECISION_GEO_OBSERVE if mode == MODE_OBSERVE else "allow", country=country)
    return GateDecision(
        allowed=False,
        decision=DECISION_GEO,
        status_code=403,
        reason="geo_access_denied",
        message_key="server.error.geo_access_denied",
        country=country,
    )


def evaluate(raw: object) -> GateDecision | None:
    return evaluate_policy(policy(), raw)


def evaluate_policy(current: GeoPolicy, raw: object) -> GateDecision | None:
    """`access_gate` 的 GEO 求值器：返回 None 表示「地域层不表态」。"""
    if not current.active:
        return None
    kind, address = classify_ip(raw)
    if _in_allow_cidrs(address, current.allow_cidrs):
        # 运维例外：仅在地域层放行（BAN 已经先判过，绕不过去）。私网地址同样适用。
        return GateDecision(allowed=True, decision="allow", country=lookup(address).country)
    result = lookup(raw)
    if current.observing:
        would_deny = result.country not in current.allowed_countries if result.country else current.unknown_action == UNKNOWN_DENY
        if would_deny:
            _stats["observed"] += 1
        return GateDecision(allowed=True, decision=DECISION_GEO_OBSERVE, country=result.country)
    # enforce：库不可用时 fail-closed
    if result.status == "unavailable":
        return GateDecision(
            allowed=False,
            decision=DECISION_GEO_UNAVAILABLE,
            status_code=503,
            reason="geo_database_unavailable",
            message_key="server.error.geo_database_unavailable",
        )
    if result.country:
        if result.country in current.allowed_countries:
            return GateDecision(allowed=True, decision="allow", country=result.country, geo_db_epoch=result.epoch or None)
        _stats["denied"] += 1
        return _decision(result.country, allow=False, status_code=403, mode=current.mode)
    if current.unknown_action == UNKNOWN_ALLOW:
        return GateDecision(allowed=True, decision="allow", country="", geo_db_epoch=result.epoch or None)
    _stats["denied"] += 1
    return _decision("", allow=False, status_code=403, mode=current.mode)


def _simulate_with(current: GeoPolicy, raw: object) -> dict:
    kind, address = classify_ip(raw)
    decision = evaluate_policy(current, raw)
    with _lock:
        reader, epoch, error = _ensure_reader()
    unavailable = decision is not None and decision.reason == "geo_database_unavailable"
    return {
        "ip": address, "ip_class": kind,
        "country": decision.country if decision else "",
        "would": "allow" if decision is None or decision.allowed else "deny",
        "reason": decision.reason if decision else "policy_off",
        "status_code": decision.status_code if decision else 200,
        "mode": current.mode, "forced_off": current.forced_off,
        "unknown_action": current.unknown_action,
        "allowed_countries": sorted(current.allowed_countries),
        "database_available": reader is not None and not error and not unavailable,
        "database_epoch": epoch, "database_error": error or ("database_lookup_failed" if unavailable else ""),
    }


def simulate(raw: object) -> dict:
    """启用前的预演：只回答「这个地址会被怎样处理」，不改任何状态。"""
    return _simulate_with(policy(), raw)


def provisional(raw: object, *, mode: object = None, allowed_countries: object = None, unknown_action: object = None, allow_cidrs: object = None) -> dict:
    """用「即将写入的设置」预演一个地址。

    用途是启用 enforce 之前的**自锁预检**：如果按新设置调用者自己都会被拒绝，
    就必须先明确确认，否则管理员一点保存就把自己关在门外（而且是唯一的管理员）。
    """
    current = policy()
    next_mode = str(mode).strip().lower() if mode not in (None, "") else current.mode
    if next_mode not in GEO_MODES:
        next_mode = current.mode
    next_countries = (
        frozenset(parse_country_list(allowed_countries)) or frozenset(DEFAULT_ALLOWED_COUNTRIES)
        if allowed_countries is not None
        else current.allowed_countries
    )
    next_unknown = str(unknown_action).strip().lower() if unknown_action not in (None, "") else current.unknown_action
    if next_unknown not in GEO_UNKNOWN_ACTIONS:
        next_unknown = current.unknown_action
    next_cidrs = parse_cidrs(allow_cidrs) if allow_cidrs is not None else current.allow_cidrs
    return _simulate_with(
        GeoPolicy(
            mode=next_mode,
            allowed_countries=next_countries,
            unknown_action=next_unknown,
            allow_cidrs=next_cidrs,
            forced_off=current.forced_off,
            source="provisional",
        ),
        raw,
    )


def status() -> dict:
    """诊断信息：**不含** License Key、下载地址或任何凭据。"""
    current = policy(force=True)
    reader, epoch, error = _ensure_reader()
    path = latest_database()
    size = 0
    modified = 0
    name = ""
    if path is not None:
        try:
            stats = path.stat()
            size = int(stats.st_size)
            modified = int(stats.st_mtime)
            name = path.name
        except OSError:  # pragma: no cover - 竞态下文件可能刚被替换
            name = path.name
    return {
        "mode": current.mode,
        "forced_off": bool(current.forced_off),
        "policy_source": current.source,
        "allowed_countries": sorted(current.allowed_countries),
        "unknown_action": current.unknown_action,
        "allow_cidrs": [str(net) for net in current.allow_cidrs],
        "database": {
            "directory": str(database_dir()),
            "file": name,
            "size_bytes": size,
            "built_ts": int(epoch or 0),
            "modified_ts": modified,
            "max_age_days": MAX_DATABASE_AGE_SECONDS // 86400,
            "epoch": int(epoch or 0),
            "type": _reader_type if reader is not None and not error else "",
            "city_available": bool(reader is not None and not error and _reader_type in CITY_DATABASE_TYPES),
            "available": reader is not None and not error,
            "error": error,
        },
        "license_key_present": bool(_env_text(LICENSE_KEY_ENV)),
        "stats": dict(_stats),
    }


def self_check() -> dict:
    """启动自检：只报告模式与库是否可用，不做任何网络访问。"""
    current = policy(force=True)
    reader, epoch, error = _ensure_reader()
    return {
        "mode": current.mode,
        "enforcing": current.enforcing,
        "database_available": reader is not None and not error,
        "database_epoch": int(epoch or 0),
        "database_error": error,
        "allowed_countries": sorted(current.allowed_countries),
        "unknown_action": current.unknown_action,
    }


def close() -> None:
    """关闭 reader（进程退出/测试用）。"""
    global _reader, _reader_path, _reader_epoch, _reader_fingerprint, _reader_checked_at, _reader_type
    with _lock:
        if _reader is not None:
            try:
                _reader.close()
            except Exception:  # pragma: no cover
                log.debug("GEO_DATABASE_CLOSE_FAILED", exc_info=True)
        _reader = None
        _reader_path = ""
        _reader_epoch = 0
        _reader_type = ""
        _reader_fingerprint = None
        _reader_checked_at = 0.0


__all__ = [
    "MODE_ENFORCE",
    "MODE_OFF",
    "MODE_OBSERVE",
    "GEO_MODES",
    "GEO_UNKNOWN_ACTIONS",
    "UNKNOWN_ALLOW",
    "UNKNOWN_DENY",
    "GeoLookup",
    "GeoPolicy",
    "access_geo_lookup",
    "classify_ip",
    "close",
    "database_dir",
    "evaluate",
    "invalidate_policy",
    "latest_database",
    "lookup",
    "normalize_country",
    "parse_cidrs",
    "parse_country_list",
    "policy",
    "provisional",
    "self_check",
    "simulate",
    "status",
]
