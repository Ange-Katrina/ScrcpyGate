"""GEO 库自动更新：12 小时 + 抖动、手动立即检查、原子替换、旧库清理与状态持久化。

安全与授权约束（任务书 §12 + MaxMind 条款）：

* License Key 来自环境变量或数据卷内权限受限的凭据文件，绝不写入数据库、日志、响应或镜像；
  `status()` 只报告「是否存在」。所有错误信息在返回前都会做密钥打码。
* 只从 MaxMind 官方下载端点取库，使用 Account ID 与 License Key 的 Basic Auth；
  跨主机重定向仅允许官方对象存储，并移除 Authorization。
* 不随产品分发 MMDB；本地验收用 MaxMind 官方公开**测试库**伪造一次「下载 → 校验 → 原子替换」。
* 更新尝试有上限（默认每天 30 次，包含失败），检查有最小间隔（默认 10 分钟）。

原子替换与 keep-last-good：

1. 下载到数据目录下的临时文件；
2. 校验 tar.gz 结构、只取其中的 ``.mmdb``，且**必须**能用 maxminddb 打开、
   且类型与构建时间满足 Country 库校验；
3. ``os.replace()`` 原子换入正式文件名（同目录内 rename，替换的是 inode，
   `geo_access` 重新加载 reader，外部同路径替换通过文件指纹检测）；
4. 先复制备份，替换失败保留旧库；激活失败从备份恢复。
"""

from __future__ import annotations

import io
import base64
import shutil
from urllib.parse import urlparse
import json
import logging
import os
import random
import re
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import geo_access, geo_credentials, security, storage

log = logging.getLogger("webscrcpy.geo.updater")

DOWNLOAD_BASE_ENV = "GEO_DOWNLOAD_BASE_URL"
DEFAULT_DOWNLOAD_BASE = "https://download.maxmind.com"
EDITION = "GeoLite2-Country"
# 官方端点固定路径与查询，凭据仅通过 Authorization 发送。
DOWNLOAD_PATH = f"/geoip/databases/{EDITION}/download?suffix=tar.gz"
ACCOUNT_ID_ENV = "GEO_ACCOUNT_ID"
_REDIRECT_HOST = "mm-prod-geoip-databases.a2649acb697e2c09b632799562c076f2.r2.cloudflarestorage.com"
LICENSE_KEY_ENV = geo_access.LICENSE_KEY_ENV
LICENSE_KEY_PREVIOUS_ENV = geo_access.LICENSE_KEY_PREVIOUS_ENV

# 自动更新节奏：12 小时 + 最多 30 分钟抖动（避免所有实例同一秒打同一个端点）。
DEFAULT_INTERVAL_HOURS = 12.0
MAX_INTERVAL_HOURS = 168.0
MIN_INTERVAL_HOURS = 1.0
DEFAULT_JITTER_SECONDS = 1800.0
# 手动检查的最小间隔与每日下载上限。
MIN_MANUAL_INTERVAL_SECONDS = 600.0
MAX_DOWNLOADS_PER_DAY = 30
# 单个下载的体积上限（GeoLite2-Country tar.gz 约 6 MB 量级；给足余量但不给无限）。
MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 120.0
# 应用采用构建后 30 天的保守有效期；这不是 MaxMind EULA 的原文期限定义。
OLD_DATABASE_MAX_AGE_DAYS = 30
STATE_SETTING = "_geo_update_state"
_USER_AGENT = "ScrcpyGate-GeoUpdater/1.0"
_MANAGED_NAME_RE = re.compile(r"^GeoLite2-Country.*\.mmdb$", re.IGNORECASE)
_MEMBER_RE = re.compile(r"(?:^|/)GeoLite2-Country\.mmdb$", re.IGNORECASE)
# 接受的库类型：GeoLite2-Country 是默认下载的免费库；持证用户也可以直接放 GeoIP2-Country
# （同一套 Country 数据结构，maxminddb 读取方式一致）。City/ASN 等其它类型一律拒绝——
# 它们不是「国家码」库，拿来做地域判定会得到非预期的结果。
_ACCEPTED_DATABASE_TYPES = ("GeoLite2-Country", "GeoIP2-Country")

_lock = threading.Lock()
_update_lock = threading.Lock()
_job_thread = None
_task: "threading.Thread | None" = None
_stop_event = threading.Event()


class GeoUpdateError(RuntimeError):
    """更新失败（携带稳定错误码，绝不携带密钥）。"""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = redact(detail)


def redact(text: object) -> str:
    """把 License Key 从任意文本里抹掉（错误信息、日志、响应统一走这里）。"""
    raw = str(text or "")
    try:
        saved_key = geo_credentials.resolve()[1]
    except geo_credentials.CredentialError:
        saved_key = ""
    for secret in (saved_key, os.environ.get(LICENSE_KEY_ENV, "").strip(), os.environ.get(LICENSE_KEY_PREVIOUS_ENV, "").strip()):
        if secret and secret in raw:
            raw = raw.replace(secret, "***")
    # 兜底：形如 license_key=xxxx 的查询串
    raw = re.sub(r"(license_key=)[^&\s]+", r"\1***", raw, flags=re.IGNORECASE)
    return raw[:400]


def license_key_present() -> bool:
    return geo_credentials.status()["license_key_present"]


def download_base() -> str:
    """Production downloads use only the official origin."""
    return DEFAULT_DOWNLOAD_BASE


def interval_seconds() -> float:
    hours = security.env_int("GEO_UPDATE_INTERVAL_HOURS", int(DEFAULT_INTERVAL_HOURS), int(MIN_INTERVAL_HOURS), int(MAX_INTERVAL_HOURS))
    return float(hours) * 3600.0


def jitter_seconds() -> float:
    raw = str(os.environ.get("GEO_UPDATE_JITTER_SECONDS", "") or "").strip()
    try:
        value = float(raw) if raw else DEFAULT_JITTER_SECONDS
    except ValueError:
        value = DEFAULT_JITTER_SECONDS
    return max(0.0, min(6 * 3600.0, value))


def enabled() -> bool:
    return security.env_bool("GEO_UPDATE_ENABLED", True)


def _state_path() -> Path:
    return geo_access.database_dir()


def _load_state() -> dict:
    try:
        raw = storage.get_setting(STATE_SETTING, "")
    except Exception:
        raise GeoUpdateError("state_read_failed") from None
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _save_state(state: dict) -> None:
    try:
        storage.set_settings({STATE_SETTING: json.dumps(state, ensure_ascii=False, sort_keys=True)[:2000]})
    except Exception:
        raise GeoUpdateError("state_persist_failed") from None


def _today(now: float | None = None) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(now if now is not None else time.time()))


def _downloads_today(state: dict, now: float | None = None) -> int:
    if state.get("day") != _today(now):
        return 0
    try:
        return int(state.get("downloads") or 0)
    except (TypeError, ValueError):
        return 0


def _download_url(key: str = "") -> str:
    return f"{DEFAULT_DOWNLOAD_BASE}{DOWNLOAD_PATH}"


def _check_download_url(url: str) -> None:
    parsed = urlparse(url)
    if (parsed.scheme != "https" or parsed.hostname not in ("download.maxmind.com", _REDIRECT_HOST)
            or parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise GeoUpdateError("download_destination_rejected")


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, deadline: float):
        self.deadline = deadline

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_download_url(newurl)
        if time.monotonic() >= self.deadline:
            raise GeoUpdateError("timeout")
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            if req.get_method() == "HEAD":
                redirected.method = "HEAD"
            if urlparse(req.full_url).hostname != urlparse(newurl).hostname:
                redirected.remove_header("Authorization")
        return redirected


def _fetch(url: str, *, method: str = "GET", deadline: float | None = None) -> tuple[bytes, str]:
    """Bounded official HTTPS download; credentials never enter URLs or redirected headers."""
    _check_download_url(url)
    deadline = deadline if deadline is not None else time.monotonic() + DOWNLOAD_TIMEOUT_SECONDS
    try:
        account, key = geo_credentials.resolve()
    except geo_credentials.CredentialError:
        raise GeoUpdateError("credentials_unreadable") from None
    credential = base64.b64encode(f"{account}:{key}".encode()).decode("ascii")
    request = urllib.request.Request(url, method=method, headers={
        "User-Agent": _USER_AGENT, "Accept": "application/octet-stream",
        "Authorization": "Basic " + credential,
    })
    chunks, total = [], 0
    try:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise GeoUpdateError("timeout")
        opener = urllib.request.build_opener(_SafeRedirect(deadline))
        with opener.open(request, timeout=min(10.0, remaining)) as response:
            version = str(response.headers.get("Last-Modified", ""))[:128]
            if method == "HEAD":
                return b"", version
            while True:
                if time.monotonic() >= deadline or _stop_event.is_set():
                    raise GeoUpdateError("timeout")
                # read1 returns available data, so a slow peer cannot keep a large read alive forever.
                chunk = response.read1(256 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_DOWNLOAD_BYTES:
                    raise GeoUpdateError("download_too_large")
                chunks.append(chunk)
    except urllib.error.HTTPError as error:
        raise GeoUpdateError("license_rejected" if error.code in (401, 403) else "http_error") from None
    except (TimeoutError, urllib.error.URLError, OSError):
        raise GeoUpdateError("network_error") from None
    if not chunks:
        raise GeoUpdateError("empty_download")
    return b"".join(chunks), version


def _extract_mmdb(payload: bytes) -> bytes:
    """从 tar.gz 里取出 GeoLite2-Country.mmdb（只读成员，不解压到磁盘，天然免疫路径穿越）。"""
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            target = None
            expanded = 0
            for index, member in enumerate(archive):
                expanded += member.size
                if index > 128 or expanded > MAX_DOWNLOAD_BYTES * 2:
                    raise GeoUpdateError("archive_too_large")
                if not member.isfile():
                    continue
                if _MEMBER_RE.search(member.name.replace("\\", "/")):
                    target = member
                    break
            if target is None or target.size > MAX_DOWNLOAD_BYTES:
                raise GeoUpdateError("archive_missing_database")
            handle = archive.extractfile(target)
            if handle is None:
                raise GeoUpdateError("archive_missing_database")
            data = handle.read(MAX_DOWNLOAD_BYTES + 1)
    except GeoUpdateError:
        raise
    except (tarfile.TarError, OSError, EOFError):
        raise GeoUpdateError("archive_unreadable") from None
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise GeoUpdateError("download_too_large")
    if not data:
        raise GeoUpdateError("empty_database")
    return data


def _validate_mmdb(path: Path) -> int:
    """用 maxminddb 打开并确认是 Country 库；返回 build epoch。"""
    try:
        import maxminddb  # type: ignore
    except Exception:
        raise GeoUpdateError("dependency_missing") from None
    try:
        reader = maxminddb.open_database(str(path))
    except Exception as exc:
        raise GeoUpdateError("database_invalid", type(exc).__name__) from None
    try:
        metadata = getattr(reader, "metadata", None)
        if callable(metadata):
            metadata = metadata()
        database_type = str(getattr(metadata, "database_type", "") or "")
        if database_type not in _ACCEPTED_DATABASE_TYPES:
            raise GeoUpdateError("unexpected_database_type", database_type)
        return int(getattr(metadata, "build_epoch", 0) or 0)
    finally:
        try:
            reader.close()
        except Exception:  # pragma: no cover
            log.debug("GEO_NEW_DATABASE_CLOSE_FAILED", exc_info=True)


def _cleanup_old_databases(keep: Path | None, *, now: float | None = None) -> int:
    """Only mutate our exact files in managed mode; external directories are read-only."""
    if not enabled():
        return 0
    moment = time.time() if now is None else now
    removed = 0
    try:
        entries = list(_state_path().iterdir())
    except OSError:
        return 0
    for entry in entries:
        try:
            if entry.is_symlink() or not entry.is_file():
                continue
            owned_temp = entry.name.startswith(f".{EDITION}-") and entry.suffix == ".tmp"
            if owned_temp:
                stale = entry.stat().st_mtime < moment - 3600
            elif entry.name in (f"{EDITION}.mmdb", f"{EDITION}.mmdb.bak"):
                try:
                    epoch = _validate_mmdb(entry)
                except GeoUpdateError:
                    continue
                stale = epoch > 0 and moment - epoch > geo_access.MAX_DATABASE_AGE_SECONDS
                if stale and str(entry) == geo_access._reader_path:
                    geo_access.close()
            else:
                continue
            if stale:
                entry.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def can_update_now(*, now: float | None = None) -> tuple[bool, str]:
    moment = time.time() if now is None else now
    state = _load_state()
    if not enabled():
        return False, "updater_disabled"
    credentials = geo_credentials.status()
    if credentials["credentials_error"]:
        return False, credentials["credentials_error"]
    if not credentials["license_key_present"]:
        return False, "license_key_missing"
    if not credentials["account_id_present"]:
        return False, "account_id_missing"
    last = float(state.get("last_attempt_ts") or 0)
    if last and moment - last < MIN_MANUAL_INTERVAL_SECONDS:
        return False, "too_soon"
    if state.get("day") == _today(moment) and int(state.get("attempts") or 0) >= MAX_DOWNLOADS_PER_DAY:
        return False, "daily_limit"
    return True, ""


def _reserve(trigger: str, moment: float) -> dict:
    allowed, reason = can_update_now(now=moment)
    if not allowed:
        raise GeoUpdateError(reason)
    state = _load_state()
    if state.get("day") != _today(moment):
        state.update(day=_today(moment), attempts=0, downloads=0)
    state.update(last_attempt_ts=int(moment), trigger=str(trigger)[:24],
                 attempts=int(state.get("attempts") or 0) + 1, last_error="", job_state="running")
    _save_state(state)
    return state


def _audit_result(actor: str, outcome: str, reason: str) -> None:
    try:
        storage.record_audit_event(username=actor or "system", action="geo_database_update_result",
                                   actor_role="admin" if actor else "system", outcome=outcome,
                                   reason=reason, target_type="geo_database", target_id=EDITION)
    except Exception:
        log.warning("GEO_UPDATE_AUDIT_FAILED")


def _perform_update(state: dict, *, moment: float, actor: str = "") -> dict:
    temp_path = backup_temp = None
    deadline = time.monotonic() + DOWNLOAD_TIMEOUT_SECONDS
    try:
        directory = _state_path()
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{EDITION}.mmdb"
        _, version = _fetch(_download_url(), method="HEAD", deadline=deadline)
        reader, _, error = geo_access._ensure_reader()
        if version and version == state.get("remote_version") and reader is not None and not error:
            state.update(job_state="unchanged", last_success_ts=int(moment), last_error="")
            _save_state(state)
            _audit_result(actor, "success", "unchanged")
            return status(now=moment)
        payload, downloaded_version = _fetch(_download_url(), deadline=deadline)
        state["downloads"] = int(state.get("downloads") or 0) + 1
        data = _extract_mmdb(payload)
        with tempfile.NamedTemporaryFile(dir=directory, prefix=f".{EDITION}-", suffix=".tmp", delete=False) as handle:
            temp_path = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        epoch = _validate_mmdb(temp_path)
        if not (0 < epoch <= moment + 86400 and moment - epoch <= geo_access.MAX_DATABASE_AGE_SECONDS):
            raise GeoUpdateError("database_expired")
        if time.monotonic() >= deadline:
            raise GeoUpdateError("timeout")
        # Copy rather than move the active file: failed final replace cannot remove it.
        with geo_access._lock:
            if target.exists():
                with tempfile.NamedTemporaryFile(dir=directory, prefix=f".{EDITION}-", suffix=".tmp", delete=False) as handle:
                    backup_temp = Path(handle.name)
                shutil.copyfile(target, backup_temp)
                os.replace(backup_temp, directory / f"{EDITION}.mmdb.bak")
            os.replace(temp_path, target)
            geo_access.close()
            reader, reader_epoch, reader_error = geo_access._ensure_reader()
            if reader is None or reader_error:
                backup = directory / f"{EDITION}.mmdb.bak"
                if backup.exists():
                    with tempfile.NamedTemporaryFile(dir=directory, prefix=f".{EDITION}-", suffix=".tmp", delete=False) as handle:
                        backup_temp = Path(handle.name)
                    shutil.copyfile(backup, backup_temp)
                    os.replace(backup_temp, target)
                    geo_access.close()
                    geo_access._ensure_reader()
                raise GeoUpdateError("database_load_failed")
        state.update(last_download_ts=int(moment), last_success_ts=int(moment), last_error="",
                     database_epoch=reader_epoch, database_size_bytes=target.stat().st_size,
                     removed_old_databases=_cleanup_old_databases(target, now=moment),
                     remote_version=downloaded_version or version, job_state="completed")
        _save_state(state)
        _audit_result(actor, "success", "updated")
        return status(now=moment)
    except Exception as exc:
        code = exc.code if isinstance(exc, GeoUpdateError) else "update_io_failed"
        state.update(last_error=code, last_error_ts=int(moment), job_state="failed")
        _save_state(state)
        _audit_result(actor, "failure", code)
        raise GeoUpdateError(code) from None
    finally:
        for path in (temp_path, backup_temp):
            if path is not None:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass


def update_now(*, trigger: str = "manual", actor: str = "", now: float | None = None) -> dict:
    if not _update_lock.acquire(blocking=False):
        raise GeoUpdateError("update_in_progress")
    try:
        moment = time.time() if now is None else now
        _perform_update(_reserve(trigger, moment), moment=moment, actor=actor)
    finally:
        _update_lock.release()
    return status(now=moment)


def enqueue_update(*, trigger: str = "manual", actor: str = "") -> dict:
    global _job_thread
    if not _update_lock.acquire(blocking=False):
        return {"job_state": "running", "running": True}
    try:
        moment = time.time()
        state = _reserve(trigger, moment)
        def run():
            try:
                _perform_update(state, moment=moment, actor=actor)
            except GeoUpdateError as exc:
                log.warning("GEO_UPDATE_FAILED code=%s", exc.code)
            finally:
                _update_lock.release()
        _job_thread = threading.Thread(target=run, name="geo-update-job", daemon=True)
        _job_thread.start()
    except Exception:
        _update_lock.release()
        raise
    return {"job_state": "running", "running": True}


def cleanup_old(*, now: float | None = None) -> int:
    if not _update_lock.acquire(blocking=False):
        return 0
    try:
        with geo_access._lock:
            return _cleanup_old_databases(None, now=now)
    finally:
        _update_lock.release()


def status(*, now: float | None = None) -> dict:
    """更新状态摘要：**永不包含** License Key 或带 key 的完整 URL。"""
    moment = now if now is not None else time.time()
    state = _load_state()
    base = geo_access.status()
    allowed, reason = can_update_now(now=moment)
    last_success = float(state.get("last_success_ts") or 0)
    next_due = 0.0
    if last_success:
        next_due = last_success + interval_seconds()
    return {
        "enabled": enabled(),
        "running": _update_lock.locked(),
        "job_state": "running" if _update_lock.locked() else ("interrupted" if state.get("job_state") == "running" else state.get("job_state", "idle")),
        "attempts_today": int(state.get("attempts") or 0) if state.get("day") == _today(moment) else 0,
        **geo_credentials.status(),
        "download_base": download_base(),
        "edition": EDITION,
        "interval_hours": round(interval_seconds() / 3600.0, 2),
        "jitter_seconds": int(jitter_seconds()),
        "min_manual_interval_seconds": int(MIN_MANUAL_INTERVAL_SECONDS),
        "max_downloads_per_day": MAX_DOWNLOADS_PER_DAY,
        "downloads_today": _downloads_today(state, moment),
        "can_update_now": allowed and not _update_lock.locked(),
        "blocked_reason": "update_in_progress" if _update_lock.locked() else reason,
        "last_attempt_ts": int(state.get("last_attempt_ts") or 0),
        "last_success_ts": int(last_success),
        "last_download_ts": int(state.get("last_download_ts") or 0),
        "last_error": redact(state.get("last_error") or ""),
        "last_error_ts": int(state.get("last_error_ts") or 0),
        "database_epoch": int(state.get("database_epoch") or 0),
        "database_size_bytes": int(state.get("database_size_bytes") or 0),
        "removed_old_databases": int(state.get("removed_old_databases") or 0),
        "next_due_ts": int(next_due),
        "old_database_max_age_days": OLD_DATABASE_MAX_AGE_DAYS,
        "database": base["database"],
        "policy": {
            "mode": base["mode"],
            "forced_off": base["forced_off"],
            "allowed_countries": base["allowed_countries"],
            "unknown_action": base["unknown_action"],
            "allow_cidrs": base["allow_cidrs"],
        },
    }


def configure_credentials(account: object = "", key: object = "", *, clear: bool = False) -> dict:
    """Serialize credential rotation with downloads; never change an in-flight pair."""
    if not _update_lock.acquire(blocking=False):
        raise GeoUpdateError("update_in_progress")
    try:
        if clear:
            geo_credentials.clear()
        else:
            geo_credentials.save(account, key)
    except geo_credentials.CredentialError as exc:
        raise GeoUpdateError(str(exc)) from None
    finally:
        _update_lock.release()
    start()
    return geo_credentials.status()


def next_delay_seconds(*, now: float | None = None) -> float:
    """下一次自动更新的等待时长（含抖动）。"""
    moment = now if now is not None else time.time()
    state = _load_state()
    last_success = float(state.get("last_success_ts") or 0)
    base = interval_seconds()
    if last_success:
        remaining = max(60.0, base - (moment - last_success))
    else:
        remaining = 120.0  # 首次启动稍等一会儿，避开启动尖峰
    jitter = jitter_seconds()
    return remaining + (random.uniform(0, jitter) if jitter else 0.0)


def maybe_update_once(*, now: float | None = None) -> dict | None:
    """到点就更新一次；未到点/未启用/无密钥时返回 None。"""
    if not enabled():
        return None
    moment = now if now is not None else time.time()
    if not license_key_present():
        return None
    state = _load_state()
    last_success = float(state.get("last_success_ts") or 0)
    if last_success and moment - last_success < interval_seconds():
        return None
    allowed, _reason = can_update_now(now=moment)
    if not allowed:
        return None
    try:
        return update_now(trigger="scheduled", now=moment)
    except GeoUpdateError as exc:
        log.warning("GEO_UPDATE_FAILED code=%s detail=%s", exc.code, redact(exc.detail))
        return None


def _loop() -> None:  # pragma: no cover - 线程体，靠集成验收覆盖
    while not _stop_event.is_set():
        delay = next_delay_seconds()
        if _stop_event.wait(delay):
            return
        try:
            maybe_update_once()
        except Exception:
            log.exception("GEO_UPDATE_LOOP_ERROR")


def start() -> bool:
    """启动后台更新线程（幂等）。未启用/无密钥时返回 False。"""
    global _task
    if not enabled() or not license_key_present():
        log.info("GEO_UPDATE_DISABLED enabled=%s key_present=%s", enabled(), license_key_present())
        return False
    with _lock:
        if _task is not None and _task.is_alive():
            return True
        _stop_event.clear()
        _task = threading.Thread(target=_loop, name="geo-updater", daemon=True)
        _task.start()
    return True


def stop() -> None:
    global _task
    with _lock:
        _stop_event.set()
        task = _task
        _task = None
    if task is not None and task.is_alive():
        task.join(timeout=5.0)
    if _job_thread is not None and _job_thread.is_alive():
        _job_thread.join(timeout=5.0)


__all__ = [
    "DEFAULT_DOWNLOAD_BASE",
    "EDITION",
    "GeoUpdateError",
    "can_update_now",
    "cleanup_old",
    "enabled",
    "enqueue_update",
    "license_key_present",
    "maybe_update_once",
    "next_delay_seconds",
    "redact",
    "start",
    "status",
    "stop",
    "update_now",
]
