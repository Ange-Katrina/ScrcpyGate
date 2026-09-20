"""GEO 库自动更新：12 小时 + 抖动、手动立即检查、原子替换、旧库清理与状态持久化。

安全与授权约束（任务书 §12 + MaxMind 条款）：

* License Key 来自环境变量或数据卷内权限受限的凭据文件，绝不写入数据库、日志、响应或镜像；
  `status()` 只报告「是否存在」。所有错误信息在返回前都会做密钥打码。
* 只从 MaxMind 官方下载端点取库，使用 Account ID 与 License Key 的 Basic Auth；
  跨主机重定向仅允许官方对象存储，并移除 Authorization。
* 不随产品分发 MMDB；本地验收用 MaxMind 官方公开**测试库**伪造一次「下载 → 校验 → 原子替换」。
* 更新尝试有上限（每天 30 次，包含失败）；成功检查间隔 10 分钟，失败或配置变更后为 30 秒。

原子替换与 keep-last-good：

1. 下载到数据目录下的临时文件；
2. 校验 tar.gz 结构、只取其中的 ``.mmdb``，且**必须**能用 maxminddb 打开、
   且类型与构建时间满足 City 库校验；
3. ``os.replace()`` 原子换入正式文件名（同目录内 rename，替换的是 inode，
   `geo_access` 重新加载 reader，外部同路径替换通过文件指纹检测）；
4. 先复制备份，替换失败保留旧库；激活失败从备份恢复。
"""

from __future__ import annotations

import io
import hashlib
import base64
import shutil
from urllib.parse import urlparse
import json
import logging
import math
import os
import random
import re
import socket
import ssl
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from collections import deque

from . import geo_access, geo_credentials, geo_downloads, security, storage

log = logging.getLogger("webscrcpy.geo.updater")

DOWNLOAD_BASE_ENV = "GEO_DOWNLOAD_BASE_URL"
DEFAULT_DOWNLOAD_BASE = "https://download.maxmind.com"
EDITION = "GeoLite2-City"
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
RETRY_INTERVAL_SECONDS = 30.0
MAX_DOWNLOADS_PER_DAY = 30
# City is larger than Country; keep separate compressed and expanded bounds.
MAX_DOWNLOAD_BYTES = 128 * 1024 * 1024
MAX_DATABASE_BYTES = 256 * 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 600.0
# 应用采用构建后 30 天的保守有效期；这不是 MaxMind EULA 的原文期限定义。
OLD_DATABASE_MAX_AGE_DAYS = 30
STATE_SETTING = "_geo_update_state"
INTERVAL_SETTING = "_geo_update_interval_hours"
_USER_AGENT = "ScrcpyGate-GeoUpdater/1.0"
_MEMBER_RE = re.compile(r"(?:^|/)GeoLite2-City\.mmdb$", re.IGNORECASE)
_ACCEPTED_DATABASE_TYPES = geo_access.CITY_DATABASE_TYPES
_MANAGED_EDITIONS = ("GeoLite2-Country", EDITION)

_lock = threading.Lock()
_update_lock = threading.Lock()
_job_thread = None
_task: "threading.Thread | None" = None
_stop_event = threading.Event()
_wake_event = threading.Event()
_next_check_ts = 0.0

# 更新进度：只存在于内存（进程重启即清空），供后台界面显示进度条。
# stage 取值固定，便于前端做翻译映射：
#   idle → queued → credentials → download → verify → activate → cleanup → done / failed
# percent 是「整体完成度」，下载阶段按 Content-Length 实时换算；没有 Content-Length
# 时 total_bytes 为 0，前端显示为不确定进度。
PROGRESS_STAGES = ("idle", "queued", "credentials", "download", "verify", "activate", "cleanup", "done", "failed")
_PROGRESS_LOCK = threading.Lock()
_PROGRESS: dict[str, object] = {
    "stage": "idle", "percent": 0, "downloaded_bytes": 0, "total_bytes": 0, "updated_ts": 0.0,
}
_EVENTS: deque[dict] = deque(maxlen=120)
_EVENT_SEQ = 0
_TASK_STARTED = 0.0
_LAST_DOWNLOAD_EVENT = 0.0
_TASK_EDITION = ""
_TASK_INDEX = 0
_TASK_COUNT = 1


def _safe_http_details(value: object) -> dict:
    if not isinstance(value, dict):
        return {}
    status_code = value.get("status")
    if type(status_code) is not int or not 100 <= status_code <= 599:
        return {}
    return {"status": status_code,
            "method": value.get("method") if value.get("method") in ("HEAD", "GET") else "",
            "endpoint": value.get("endpoint") if value.get("endpoint") in ("maxmind", "storage") else ""}


def _event(stage: str, *, code: str = "", downloaded: int = 0, total: int = 0, http: dict | None = None) -> None:
    """Fixed event fields only: never accept request URLs, headers or exception text."""
    global _EVENT_SEQ
    with _PROGRESS_LOCK:
        _EVENT_SEQ += 1
        _EVENTS.append({"id": _EVENT_SEQ, "ts": int(time.time()), "stage": stage,
                        "edition": _TASK_EDITION, "code": code if re.fullmatch(r"[a-z_]{1,48}", code) else "",
                        "downloaded_bytes": max(0, downloaded), "total_bytes": max(0, total), "http": _safe_http_details(http),
                        "elapsed_seconds": round(max(0, time.monotonic() - _TASK_STARTED), 1) if _TASK_STARTED else 0})


def events() -> list[dict]:
    with _PROGRESS_LOCK:
        return [{**item, "http": dict(item["http"])} for item in _EVENTS]


def _set_progress(
    stage: str,
    percent: int,
    *,
    downloaded: int | None = None,
    total: int | None = None,
) -> None:
    """记录一次进度（stage 非法时归一到 idle，绝不写入非预期内容）。"""
    safe_stage = stage if stage in PROGRESS_STAGES else "idle"
    global _LAST_DOWNLOAD_EVENT
    with _PROGRESS_LOCK:
        changed = _PROGRESS["stage"] != safe_stage
        _PROGRESS["stage"] = safe_stage
        overall = ((_TASK_INDEX - 1) * 100 + percent) / _TASK_COUNT if _TASK_INDEX else percent
        _PROGRESS["percent"] = max(0, min(100, int(overall)))
        _PROGRESS.update(edition=_TASK_EDITION, index=_TASK_INDEX, count=_TASK_COUNT)
        if downloaded is not None:
            _PROGRESS["downloaded_bytes"] = max(0, int(downloaded))
        if total is not None:
            _PROGRESS["total_bytes"] = max(0, int(total))
        _PROGRESS["updated_ts"] = time.time()
    now = time.monotonic()
    if changed or (safe_stage == "download" and (now - _LAST_DOWNLOAD_EVENT >= 2 or (total and downloaded == total))):
        _LAST_DOWNLOAD_EVENT = now
        _event(safe_stage, downloaded=downloaded or 0, total=total or 0)


def reset_progress() -> None:
    """开始新任务前清掉上一次的进度，避免界面显示过期进度条。"""
    global _TASK_STARTED, _TASK_EDITION, _TASK_INDEX, _TASK_COUNT, _LAST_DOWNLOAD_EVENT
    with _PROGRESS_LOCK:
        _EVENTS.clear()
        _TASK_STARTED = 0.0
        _TASK_EDITION, _TASK_INDEX, _TASK_COUNT = "", 0, 1
        _LAST_DOWNLOAD_EVENT = 0.0
        _PROGRESS.clear()
        _PROGRESS.update(stage="idle", percent=0, downloaded_bytes=0, total_bytes=0, updated_ts=0.0)


def progress() -> dict:
    with _PROGRESS_LOCK:
        return dict(_PROGRESS)


class GeoUpdateError(RuntimeError):
    """更新失败（携带稳定错误码，绝不携带密钥）。"""

    def __init__(self, code: str, detail: str = "", *, http: dict | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = redact(detail)
        self.http = _safe_http_details(http)


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
    saved = storage.get_setting(INTERVAL_SETTING, "")
    if str(saved).isdigit() and MIN_INTERVAL_HOURS <= int(saved) <= MAX_INTERVAL_HOURS:
        hours = int(saved)
    return float(hours) * 3600.0


def configure_schedule(hours: object) -> dict:
    """An explicit admin setting overrides the environment's initial default."""
    global _next_check_ts
    if type(hours) is not int or not MIN_INTERVAL_HOURS <= hours <= MAX_INTERVAL_HOURS:
        raise GeoUpdateError("interval_invalid")
    try:
        storage.set_settings({INTERVAL_SETTING: str(hours)})
    except Exception:
        raise GeoUpdateError("state_persist_failed") from None
    _next_check_ts = 0.0
    _wake_event.set()
    start()
    return {"interval_hours": hours}


def _credential_fingerprint() -> str:
    try:
        account, key = geo_credentials.resolve()
    except geo_credentials.CredentialError:
        return ""
    if not account or not key:
        return ""
    return hashlib.sha256((account + "\0" + key).encode()).hexdigest()


def _verification_status(state: dict, credentials: dict) -> dict:
    fingerprint = _credential_fingerprint()
    matches = bool(fingerprint and fingerprint == state.get("credential_fingerprint")
                   and state.get("credential_edition") == ",".join(geo_downloads.public_status()["editions"]))
    configured = credentials["account_id_present"] and credentials["license_key_present"]
    return {
        "credential_verification": (state.get("credential_verification", "unverified") if matches
                                    else "unverified" if configured else "unconfigured"),
        "credential_checked_ts": int(state.get("credential_checked_ts") or 0) if matches else 0,
        "credential_verified_ts": int(state.get("credential_verified_ts") or 0) if matches else 0,
    }


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
        payload = json.dumps(state, ensure_ascii=False, sort_keys=True)
        if len(payload) > 8192:
            raise ValueError("state_too_large")
        storage.set_settings({STATE_SETTING: payload})
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


def _download_url(key: str = "", *, edition: str = EDITION) -> str:
    if edition not in geo_downloads.EDITIONS:
        raise GeoUpdateError("download_editions_invalid")
    return f"{DEFAULT_DOWNLOAD_BASE}/geoip/databases/{edition}/download?suffix=tar.gz"


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


def _network_error_code(error: BaseException) -> str:
    """Classify connectivity failures without exposing URLs or exception text."""
    current = error
    for _ in range(8):
        if isinstance(current, ssl.SSLError):
            return "tls_error"
        if isinstance(current, socket.gaierror):
            return "dns_error"
        if isinstance(current, TimeoutError):
            return "timeout"
        if isinstance(current, ConnectionRefusedError):
            return "connection_refused"
        nested = getattr(current, "reason", None)
        current = nested if isinstance(nested, BaseException) else current.__cause__ or current.__context__
        if current is None:
            break
    return "network_error"


def _http_error_code(status_code: int, host: str | None) -> str:
    if status_code in (401, 403):
        return "license_rejected" if host == "download.maxmind.com" else "download_forbidden"
    if status_code == 407:
        return "proxy_error"
    if status_code == 429:
        return "upstream_rate_limited"
    if status_code >= 500:
        return "upstream_unavailable"
    return "http_error"


def _http_failure(status_code: int, url: str, method: str) -> GeoUpdateError:
    host = urlparse(url).hostname
    endpoint = "maxmind" if host == "download.maxmind.com" else "storage" if host == _REDIRECT_HOST else ""
    return GeoUpdateError(_http_error_code(status_code, host),
                          http={"status": status_code, "method": method, "endpoint": endpoint})


def _fetch(
    url: str,
    *,
    method: str = "GET",
    deadline: float | None = None,
    on_progress=None,
) -> tuple[bytes, str]:
    """Bounded official HTTPS download; credentials never enter URLs or redirected headers.

    ``on_progress(downloaded, total)`` 在每个分片后回调（最多 4 次/秒），用于界面进度条；
    total 为 0 表示上游没给 Content-Length，调用方应按「不确定进度」显示。
    """
    _check_download_url(url)
    deadline = deadline if deadline is not None else time.monotonic() + DOWNLOAD_TIMEOUT_SECONDS
    try:
        account, key = geo_credentials.resolve()
    except geo_credentials.CredentialError:
        raise GeoUpdateError("credentials_unreadable") from None
    try:
        download_config = geo_downloads.resolve()
    except geo_downloads.DownloadConfigError as exc:
        raise GeoUpdateError(str(exc)) from None
    credential = base64.b64encode(f"{account}:{key}".encode()).decode("ascii")
    if download_config["proxy_mode"] == "custom":
        return _fetch_via_proxy(url, method=method, deadline=deadline, on_progress=on_progress,
                                proxy=download_config["proxy_url"], credential=credential)
    request = urllib.request.Request(url, method=method, headers={
        "User-Agent": _USER_AGENT, "Accept": "*/*",
        "Authorization": "Basic " + credential,
    })
    chunks, total = [], 0
    reported_at = 0.0
    try:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise GeoUpdateError("timeout")
        proxy_handler = urllib.request.ProxyHandler({}) if download_config["proxy_mode"] == "direct" else urllib.request.ProxyHandler()
        opener = urllib.request.build_opener(proxy_handler, _SafeRedirect(deadline))
        with opener.open(request, timeout=min(30.0, remaining)) as response:
            if response.status != 200:
                raise _http_failure(response.status, response.url, method)
            version = str(response.headers.get("Last-Modified", ""))[:128]
            if method == "HEAD":
                return b"", version
            try:
                expected = max(0, int(response.headers.get("Content-Length") or 0))
            except (TypeError, ValueError):
                expected = 0
            if expected:
                # 长度已知就先报一次 0%，界面立刻从「检查凭据」切到下载阶段。
                reported_at = time.monotonic()
                if on_progress is not None:
                    on_progress(0, expected)
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
                now = time.monotonic()
                if on_progress is not None and (now - reported_at >= 0.25 or total == expected):
                    reported_at = now
                    on_progress(total, expected)
    except urllib.error.HTTPError as error:
        raise _http_failure(error.code, error.url, method) from None
    except (TimeoutError, urllib.error.URLError, OSError) as error:
        raise GeoUpdateError(_network_error_code(error)) from None
    if not chunks:
        raise GeoUpdateError("empty_download")
    return b"".join(chunks), version


def _fetch_via_proxy(url: str, *, method: str, deadline: float, on_progress, proxy: str, credential: str) -> tuple[bytes, str]:
    # HTTPX supports both HTTP CONNECT and TLS-to-proxy with verified target TLS.
    import httpx

    # Do not let library debug logs expose signed redirect URLs or proxy auth.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    headers = {"User-Agent": _USER_AGENT, "Accept": "*/*", "Authorization": "Basic " + credential}
    try:
        with httpx.Client(proxy=proxy, trust_env=False, follow_redirects=False) as client:
            for _ in range(6):
                _check_download_url(url)
                remaining = deadline - time.monotonic()
                if remaining <= 0 or _stop_event.is_set():
                    raise GeoUpdateError("timeout")
                with client.stream(method, url, headers=headers, timeout=min(30.0, remaining)) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        target = str(response.url.join(response.headers.get("location", "")))
                        _check_download_url(target)
                        if urlparse(url).hostname != urlparse(target).hostname:
                            headers.pop("Authorization", None)
                        url = target
                        continue
                    if response.status_code != 200:
                        raise _http_failure(response.status_code, url, method)
                    version = response.headers.get("last-modified", "")[:128]
                    if method == "HEAD":
                        return b"", version
                    try:
                        expected = max(0, int(response.headers.get("content-length", "0")))
                    except ValueError:
                        expected = 0
                    if expected > MAX_DOWNLOAD_BYTES:
                        raise GeoUpdateError("download_too_large")
                    chunks, downloaded, reported = [], 0, 0.0
                    if on_progress:
                        on_progress(0, expected)
                    for chunk in response.iter_raw():
                        now = time.monotonic()
                        if now >= deadline or _stop_event.is_set():
                            raise GeoUpdateError("timeout")
                        downloaded += len(chunk)
                        if downloaded > MAX_DOWNLOAD_BYTES:
                            raise GeoUpdateError("download_too_large")
                        chunks.append(chunk)
                        if on_progress and (now - reported >= .25 or downloaded == expected):
                            reported = now
                            on_progress(downloaded, expected)
                    if not downloaded:
                        raise GeoUpdateError("empty_download")
                    return b"".join(chunks), version
            raise GeoUpdateError("download_destination_rejected")
    except httpx.ProxyError:
        raise GeoUpdateError("proxy_error") from None
    except httpx.TimeoutException:
        raise GeoUpdateError("timeout") from None
    except (httpx.HTTPError, ValueError, OSError) as error:
        raise GeoUpdateError(_network_error_code(error)) from None


def _extract_mmdb(payload: bytes, *, edition: str = EDITION) -> bytes:
    """Read only the selected member; never extract archive paths to disk."""
    if edition not in geo_downloads.EDITIONS:
        raise GeoUpdateError("download_editions_invalid")
    member_pattern = re.compile(r"(?:^|/)" + re.escape(edition) + r"\.mmdb$", re.IGNORECASE)
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            target = None
            expanded = 0
            for index, member in enumerate(archive):
                expanded += member.size
                if index > 128 or expanded > MAX_DATABASE_BYTES * 2:
                    raise GeoUpdateError("archive_too_large")
                if not member.isfile():
                    continue
                if member_pattern.search(member.name.replace("\\", "/")):
                    target = member
                    break
            if target is None or target.size > MAX_DATABASE_BYTES:
                raise GeoUpdateError("archive_missing_database")
            handle = archive.extractfile(target)
            if handle is None:
                raise GeoUpdateError("archive_missing_database")
            data = handle.read(MAX_DATABASE_BYTES + 1)
    except GeoUpdateError:
        raise
    except (tarfile.TarError, OSError, EOFError):
        raise GeoUpdateError("archive_unreadable") from None
    if len(data) > MAX_DATABASE_BYTES:
        raise GeoUpdateError("download_too_large")
    if not data:
        raise GeoUpdateError("empty_database")
    return data


def _validate_mmdb(path: Path, *, accepted_types: tuple = _ACCEPTED_DATABASE_TYPES) -> int:
    """Validate the edition, then return its build epoch."""
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
        if database_type not in accepted_types:
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
            if keep is not None and entry == keep:
                continue
            owned_temp = any(entry.name.startswith(f".{edition}-") for edition in _MANAGED_EDITIONS) and entry.suffix == ".tmp"
            if owned_temp:
                stale = entry.stat().st_mtime < moment - 3600
            elif entry.name in tuple(name for edition in _MANAGED_EDITIONS for name in (f"{edition}.mmdb", f"{edition}.mmdb.bak")):
                try:
                    epoch = _validate_mmdb(entry, accepted_types=geo_access.ACCEPTED_DATABASE_TYPES)
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


def retry_after_seconds(*, now: float | None = None, state: dict | None = None) -> int:
    state = _load_state() if state is None else state
    moment = time.time() if now is None else now
    last = float(state.get("last_attempt_ts") or 0)
    retry = bool(state.get("last_error") or state.get("job_state") == "failed" or state.get("retry_configuration_changed"))
    interval = RETRY_INTERVAL_SECONDS if retry else MIN_MANUAL_INTERVAL_SECONDS
    return max(0, math.ceil(last + interval - moment)) if last else 0


def can_update_now(*, now: float | None = None) -> tuple[bool, str]:
    config = geo_downloads.public_status()
    if config["error"]:
        return False, config["error"]
    if not config["editions"]:
        return False, "downloads_disabled"
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
    if retry_after_seconds(now=moment, state=state):
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
                 attempts=int(state.get("attempts") or 0) + 1, last_error="", last_http_error={}, job_state="running", retry_configuration_changed=False)
    _save_state(state)
    reset_progress()
    _set_progress("queued", 1)
    return state


def _audit_result(actor: str, outcome: str, reason: str) -> None:
    try:
        storage.record_audit_event(username=actor or "system", action="geo_database_update_result",
                                   actor_role="admin" if actor else "system", outcome=outcome,
                                   reason=reason, target_type="geo_database",
                                   target_id=",".join(geo_downloads.public_status()["editions"]))
    except Exception:
        log.warning("GEO_UPDATE_AUDIT_FAILED")


def _perform_update(state: dict, *, moment: float, actor: str = "") -> dict:
    global _TASK_STARTED, _TASK_EDITION, _TASK_INDEX, _TASK_COUNT
    reset_progress()
    _TASK_STARTED = time.monotonic()
    previous_success = state.get("last_success_ts", 0)
    try:
        config = geo_downloads.resolve()
        editions = config["editions"]
        if not editions:
            raise GeoUpdateError("downloads_disabled")
        _TASK_COUNT = len(editions)
        _set_progress("queued", 1)
        _event("connection", code=config["proxy_mode"])
        outcomes = []
        for index, edition in enumerate(editions, 1):
            _TASK_EDITION, _TASK_INDEX = edition, index
            _set_progress("queued", 1, downloaded=0, total=0)
            outcomes.append(_perform_edition(state, edition=edition, moment=moment, actor=actor))
        state.update(job_state="completed" if "completed" in outcomes else "unchanged", last_error="", last_error_ts=0, last_http_error={})
        _save_state(state)
        _audit_result(actor, "success", state["job_state"])
        _set_progress("done", 100)
        return status(now=moment)
    except (GeoUpdateError, geo_downloads.DownloadConfigError) as exc:
        code = exc.code if isinstance(exc, GeoUpdateError) else str(exc)
        http = exc.http if isinstance(exc, GeoUpdateError) else {}
        state.update(job_state="failed", last_error=code, last_http_error=http, last_error_ts=int(moment), last_success_ts=previous_success)
        _save_state(state)
        # Keep the overall percentage rather than rescaling it a second time.
        with _PROGRESS_LOCK:
            _PROGRESS["stage"] = "failed"
            _PROGRESS["updated_ts"] = time.time()
        _event("failed", code=code, http=http)
        raise GeoUpdateError(code, http=http) from None


def _perform_edition(state: dict, *, edition: str, moment: float, actor: str = "") -> str:
    temp_path = backup_temp = None
    head_verified = False
    fingerprint = _credential_fingerprint()
    if fingerprint != state.get("credential_fingerprint") or state.get("credential_edition") != ",".join(geo_downloads.resolve()["editions"]):
        state.update(credential_verified_ts=0, credential_verification="unverified")
    state.update(credential_fingerprint=fingerprint, credential_checked_ts=int(moment), credential_edition=",".join(geo_downloads.resolve()["editions"]))
    deadline = time.monotonic() + DOWNLOAD_TIMEOUT_SECONDS
    try:
        directory = _state_path()
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{edition}.mmdb"
        _set_progress("credentials", 5)
        version = ""
        try:
            _, version = _fetch(_download_url(edition=edition), method="HEAD", deadline=deadline)
            head_verified = True
            state.update(credential_verification="verified", credential_verified_ts=int(moment))
        except GeoUpdateError as exc:
            # Some gateways reject HEAD while accepting GET. Retry only that case,
            # under the same deadline and attempt budget; never bypass auth errors.
            if exc.http.get("status") not in (405, 501):
                raise
            _event("head_unsupported", http=exc.http)
        versions = state.get("edition_versions", {})
        remote_version = versions.get(edition, state.get("remote_version") if state.get("edition") == edition else "")
        if version and version == remote_version and target.is_file():
            try:
                epoch = _validate_mmdb(target, accepted_types=geo_access.CITY_DATABASE_TYPES if edition == EDITION else geo_access.COUNTRY_DATABASE_TYPES)
                fresh = 0 < epoch <= moment + 86400 and moment - epoch <= geo_access.MAX_DATABASE_AGE_SECONDS
            except GeoUpdateError:
                fresh = False
            if fresh:
                # Process Country before City. A current City must become the active
                # reader again if a Country download just replaced the newest file.
                with geo_access._lock:
                    reader, _, error = geo_access._ensure_reader()
                    if geo_access._reader_path != str(target) or error or reader is None:
                        os.utime(target, None)
                        geo_access.close()
                        reader, _, error = geo_access._ensure_reader()
                    if reader is None or error or geo_access._reader_path != str(target):
                        raise GeoUpdateError("database_load_failed")
                state.update(last_success_ts=int(moment), last_error="")
                _save_state(state)
                _event("unchanged")
                return "unchanged"

        def on_download(downloaded: int, total: int) -> None:
            # 下载占总进度 5% → 75%：这是最长的一段，真实字节数比阶段文字更有用。
            if total > 0:
                percent = 5 + int(min(1.0, downloaded / total) * 70)
            else:
                percent = 5
            _set_progress("download", percent, downloaded=downloaded, total=total)

        _set_progress("download", 5, downloaded=0, total=0)
        payload, downloaded_version = _fetch(_download_url(edition=edition), deadline=deadline, on_progress=on_download)
        head_verified = True
        state.update(credential_verification="verified", credential_verified_ts=int(moment))
        state["downloads"] = int(state.get("downloads") or 0) + 1
        _set_progress("verify", 76, downloaded=len(payload), total=len(payload))
        data = _extract_mmdb(payload, edition=edition)
        with tempfile.NamedTemporaryFile(dir=directory, prefix=f".{edition}-", suffix=".tmp", delete=False) as handle:
            temp_path = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        epoch = _validate_mmdb(temp_path, accepted_types=geo_access.CITY_DATABASE_TYPES if edition == EDITION else geo_access.COUNTRY_DATABASE_TYPES)
        if not (0 < epoch <= moment + 86400 and moment - epoch <= geo_access.MAX_DATABASE_AGE_SECONDS):
            raise GeoUpdateError("database_expired")
        if time.monotonic() >= deadline:
            raise GeoUpdateError("timeout")
        _set_progress("activate", 88, downloaded=len(payload), total=len(payload))
        # Copy rather than move the active file: failed final replace cannot remove it.
        with geo_access._lock:
            had_target = target.exists()
            if had_target:
                _event("backup")
                with tempfile.NamedTemporaryFile(dir=directory, prefix=f".{edition}-", suffix=".tmp", delete=False) as handle:
                    backup_temp = Path(handle.name)
                shutil.copyfile(target, backup_temp)
                os.replace(backup_temp, directory / f"{edition}.mmdb.bak")
                _event("backup_saved")
            os.replace(temp_path, target)
            geo_access.close()
            reader, reader_epoch, reader_error = geo_access._ensure_reader()
            if reader is None or reader_error or geo_access._reader_path != str(target):
                backup = directory / f"{edition}.mmdb.bak"
                geo_access.close()
                if had_target and backup.exists():
                    with tempfile.NamedTemporaryFile(dir=directory, prefix=f".{edition}-", suffix=".tmp", delete=False) as handle:
                        backup_temp = Path(handle.name)
                    shutil.copyfile(backup, backup_temp)
                    os.replace(backup_temp, target)
                else:
                    target.unlink(missing_ok=True)
                geo_access._ensure_reader()
                raise GeoUpdateError("database_load_failed")
        _set_progress("cleanup", 96)
        state.update(last_download_ts=int(moment), last_success_ts=int(moment), last_error="",
                     database_epoch=reader_epoch, database_size_bytes=target.stat().st_size,
                     removed_old_databases=_cleanup_old_databases(target, now=moment),
                     remote_version=downloaded_version or version, edition=edition)
        state.setdefault("edition_versions", {})[edition] = downloaded_version or version
        _save_state(state)
        _event("updated", downloaded=len(payload), total=len(payload))
        return "completed"
    except Exception as exc:
        code = exc.code if isinstance(exc, GeoUpdateError) else "update_io_failed"
        if code == "license_rejected":
            state["credential_verification"] = "rejected"
        elif not head_verified:
            state["credential_verification"] = "unavailable"
        http = exc.http if isinstance(exc, GeoUpdateError) else {}
        state.update(last_error=code, last_http_error=http, last_error_ts=int(moment), job_state="failed")
        _save_state(state)
        _audit_result(actor, "failure", code)
        # 失败时保留停在哪个阶段，界面据此显示「在哪一步失败」，而不是把进度清零。
        raise GeoUpdateError(code, http=http) from None
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
        _wake_event.set()
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
                log.warning("GEO_UPDATE_FAILED code=%s http_status=%s method=%s endpoint=%s",
                            exc.code, exc.http.get("status", 0), exc.http.get("method", ""), exc.http.get("endpoint", ""))
            finally:
                _update_lock.release()
                _wake_event.set()
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
    credentials = geo_credentials.status()
    next_due = 0.0
    if enabled() and credentials["account_id_present"] and credentials["license_key_present"] and geo_downloads.public_status()["editions"]:
        next_due = _next_check_ts or (last_success + interval_seconds() if last_success else 0)
    return {
        "enabled": enabled(),
        "running": _update_lock.locked(),
        "job_state": "running" if _update_lock.locked() else ("interrupted" if state.get("job_state") == "running" else state.get("job_state", "idle")),
        # 进度只用于显示：进程重启后回到 idle/0%，界面据此隐藏进度条。
        "progress": progress(),
        "events": events(),
        "download_settings": geo_downloads.public_status(),
        "attempts_today": int(state.get("attempts") or 0) if state.get("day") == _today(moment) else 0,
        **credentials,
        **_verification_status(state, credentials),
        "download_base": download_base(),
        "edition": EDITION,
        "interval_hours": round(interval_seconds() / 3600.0, 2),
        "jitter_seconds": int(jitter_seconds()),
        "min_manual_interval_seconds": int(MIN_MANUAL_INTERVAL_SECONDS),
        "retry_interval_seconds": int(RETRY_INTERVAL_SECONDS),
        "retry_after_seconds": retry_after_seconds(now=moment, state=state),
        "max_downloads_per_day": MAX_DOWNLOADS_PER_DAY,
        "downloads_today": _downloads_today(state, moment),
        "can_update_now": allowed and not _update_lock.locked(),
        "blocked_reason": "update_in_progress" if _update_lock.locked() else reason,
        "last_attempt_ts": int(state.get("last_attempt_ts") or 0),
        "last_success_ts": int(last_success),
        "last_download_ts": int(state.get("last_download_ts") or 0),
        "last_error": redact(state.get("last_error") or ""),
        "last_http_error": _safe_http_details(state.get("last_http_error")),
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


def configure_downloads(editions: object, mode: object, proxy: object = "", *, clear_proxy: bool = False) -> dict:
    global _next_check_ts
    if not _update_lock.acquire(blocking=False):
        raise GeoUpdateError("update_in_progress")
    try:
        try:
            previous = geo_downloads.resolve()
        except geo_downloads.DownloadConfigError:
            previous = None
        geo_downloads.save(editions, mode, proxy, clear_proxy=clear_proxy)
        if previous != geo_downloads.resolve():
            state = _load_state()
            state.update(job_state="idle", last_error="", last_error_ts=0, last_http_error={}, retry_configuration_changed=True)
            _save_state(state)
            reset_progress()
    except geo_downloads.DownloadConfigError as exc:
        raise GeoUpdateError(str(exc)) from None
    finally:
        _update_lock.release()
    _next_check_ts = 0.0
    _wake_event.set()
    start()
    return geo_downloads.public_status()


def configure_credentials(account: object = "", key: object = "", *, clear: bool = False) -> dict:
    """Serialize credential rotation with downloads; never change an in-flight pair."""
    if not _update_lock.acquire(blocking=False):
        raise GeoUpdateError("update_in_progress")
    try:
        old_fingerprint = _credential_fingerprint()
        if clear:
            geo_credentials.clear()
        else:
            geo_credentials.save(account, key)
        if old_fingerprint != _credential_fingerprint():
            state = _load_state()
            for field in ("credential_fingerprint", "credential_verification", "credential_checked_ts", "credential_verified_ts"):
                state.pop(field, None)
            state.update(last_error="", last_error_ts=0, last_http_error={}, job_state="idle", retry_configuration_changed=True)
            _save_state(state)
            reset_progress()
    except geo_credentials.CredentialError as exc:
        raise GeoUpdateError(str(exc)) from None
    finally:
        _update_lock.release()
    start()
    _wake_event.set()
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
    global _next_check_ts
    while not _stop_event.is_set():
        try:
            delay = next_delay_seconds()
            _next_check_ts = time.time() + delay
            if _wake_event.wait(delay):
                _wake_event.clear()
                continue
            if _stop_event.is_set():
                break
            maybe_update_once()
        except Exception:
            log.warning("GEO_UPDATE_LOOP_ERROR")
            _wake_event.wait(60)
            _wake_event.clear()
    _next_check_ts = 0.0


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
        _wake_event.set()
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
