"""Read-only checks of published GHCR images; never apply an update.

Only validated registry tags with an available manifest produce a host command.
Network failures remain visible without preventing use of the admin dashboard.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import threading
import time
import urllib.error
import urllib.request

from .version import get_version

DEFAULT_REPO = "Ange-Katrina/ScrcpyGate"
DEFAULT_IMAGE = "ghcr.io/ange-katrina/scrcpygate"
REQUEST_TIMEOUT_SECONDS = 4.0
# 离线部署时不要让后台面板长时间停在「检查中…」：三个来源共用这个总预算。
TOTAL_BUDGET_SECONDS = 8.0
CACHE_TTL_SECONDS = 600.0
_USER_AGENT = "ScrcpyGate-UpdateCheck/1.0"
_SEMVER_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")
_STABLE_TAG_RE = re.compile(r"^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_DOCKER_TAG_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}")
_request_context = threading.local()

_cache_lock = threading.Lock()
_cache: dict[str, object] = {"payload": None, "expires_at": 0.0, "key": None}


def _env(name: str, default: str = "") -> str:
    return str(os.environ.get(name, default) or "").strip()


def update_repo() -> str:
    """owner/repo used by GitHub queries (fork-friendly via env)."""
    repo = _env("SCRCPYGATE_UPDATE_REPO", DEFAULT_REPO).strip("/")
    return repo if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) else DEFAULT_REPO


def image_reference() -> str:
    """Container image the update targets, without tag/digest.

    ``SCRCPYGATE_UPDATE_IMAGE``（或 ``SCRCPYGATE_IMAGE``）里带仓库路径时用它，
    否则退回项目默认的 GHCR 仓库——本地构建的 ``scrcpygate:local`` 不是远端引用。
    """
    configured = (_env("SCRCPYGATE_UPDATE_IMAGE") or _env("SCRCPYGATE_IMAGE")).split("@", 1)[0]
    if configured.startswith("ghcr.io/"):
        repository = configured[len("ghcr.io/"):].split(":", 1)[0]
        if re.fullmatch(r"[a-z0-9_.-]+/[a-z0-9_.-]+", repository):
            return f"ghcr.io/{repository}"
    return DEFAULT_IMAGE


def current_version() -> dict[str, str]:
    image = _env("SCRCPYGATE_IMAGE") or "scrcpygate:local"
    return {"version": get_version(), "image": image}


def resolve_channel(channel: str, current: dict[str, str]) -> str:
    """Follow development deployments without silently switching them to stable."""
    if channel not in {"auto", "stable", "dev", "edge"}:
        raise ValueError("Unsupported update channel")
    if channel != "auto":
        return channel
    image_tag = current["image"].rsplit(":", 1)[-1]
    if image_tag in {"dev", "edge"}:
        return image_tag
    if "-dev.dev." in current["version"]:
        return "dev"
    if "-dev.main." in current["version"]:
        return "edge"
    return "stable"


def _semver_key(value: str) -> tuple[int, int, int] | None:
    match = _SEMVER_RE.fullmatch(str(value or "").strip())
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _is_newer(candidate: str, current: str) -> bool | None:
    """True/False when both sides are comparable semver, None when they are not."""
    left, right = _semver_key(candidate), _semver_key(current)
    if left is None or right is None:
        return None
    if left != right:
        return left > right
    # Stable candidates supersede release candidates with the same numbers.
    if _STABLE_TAG_RE.fullmatch(candidate) and "-" in current.split("+", 1)[0]:
        return True
    return False


def _http_get_json(url: str, headers: dict[str, str] | None = None) -> object:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT, "Accept": "application/json", **(headers or {})})
    # 显式忽略环境代理探测失败：与 container_health 一样按直连处理。
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    remaining = getattr(_request_context, "deadline", time.monotonic() + TOTAL_BUDGET_SECONDS) - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("update check budget exhausted")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=min(REQUEST_TIMEOUT_SECONDS, remaining)) as response:
        raw = response.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("update response too large")
    return json.loads(raw.decode("utf-8", errors="replace"))


def _ghcr_latest_tag(image: str, channel: str = "stable") -> tuple[dict[str, object] | None, str]:
    """List GHCR tags with an anonymous pull token (public packages only)."""
    if not image.startswith("ghcr.io/"):
        return None, "ghcr-not-applicable"
    repository = image[len("ghcr.io/") :]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        return None, "ghcr-invalid-repository"
    try:
        token_payload = _http_get_json(f"https://ghcr.io/token?scope=repository:{repository}:pull&service=ghcr.io")
        token = str((token_payload or {}).get("token") or "") if isinstance(token_payload, dict) else ""
        if not token:
            return None, "ghcr-token-missing"
        payload = _http_get_json(f"https://ghcr.io/v2/{repository}/tags/list", {"Authorization": f"Bearer {token}"})
    except urllib.error.HTTPError as exc:
        return None, f"ghcr-http-{exc.code}"
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None, "ghcr-unreachable"
    tags = payload.get("tags") if isinstance(payload, dict) else None
    if not isinstance(tags, list):
        return None, "ghcr-tags-empty"
    best: tuple[tuple[int, int, int], str] | None = None
    has_latest = False
    for name in tags:
        text = str(name)
        if text == "latest":
            has_latest = True
        key = _semver_key(text) if _STABLE_TAG_RE.fullmatch(text) else None
        if key is None:
            continue
        if best is None or key > best[0]:
            best = (key, text)
    if channel in {"dev", "edge"}:
        if channel not in tags:
            return None, "ghcr-no-release-tag"
        selected = channel
    elif best is None:
        if not has_latest:
            return None, "ghcr-no-release-tag"
        selected = "latest"
    else:
        selected = best[1]
    # A Git tag is not a published image. Confirm the registry manifest before
    # exposing a command, and never interpolate arbitrary upstream tag text.
    if not _DOCKER_TAG_RE.fullmatch(selected):
        return None, "ghcr-invalid-tag"
    try:
        manifest = _http_get_json(
            f"https://ghcr.io/v2/{repository}/manifests/{selected}",
            {"Authorization": f"Bearer {token}", "Accept":
             "application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, "
             "application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json"},
        )
    except urllib.error.HTTPError as exc:
        return None, f"ghcr-manifest-http-{exc.code}"
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None, "ghcr-manifest-unreachable"
    if not isinstance(manifest, dict) or manifest.get("schemaVersion") != 2:
        return None, "ghcr-invalid-manifest"
    if best is None or channel in {"dev", "edge"}:
        return (
            {
                "version": selected,
                "tag": selected,
                "source": "ghcr-tag",
                "published_at": "",
                "url": f"https://github.com/{update_repo()}/pkgs/container/scrcpygate",
                "notes": "",
                "prerelease": False,
            },
            "",
        )
    return (
        {
            "version": best[1],
            "tag": best[1],
            "source": "ghcr-tag",
            "published_at": "",
            "url": f"https://github.com/{update_repo()}/pkgs/container/scrcpygate",
            "notes": "",
            "prerelease": False,
        },
        "",
    )


def check_for_update(*, force: bool = False, now: float | None = None, channel: str = "auto") -> dict[str, object]:
    """Return read-only status for a validated channel; network failures become data."""
    current_time = time.time() if now is None else float(now)
    repo = update_repo()
    image = image_reference()
    current = current_version()
    selected_channel = resolve_channel(channel, current)
    cache_key = (repo, image, selected_channel, current["version"], current["image"])
    with _cache_lock:
        cached = _cache.get("payload")
        if (not force and _cache.get("key") == cache_key and isinstance(cached, dict)
                and float(_cache.get("expires_at") or 0) > current_time):
            payload = dict(cached)
            payload["cached"] = True
            return payload

    _request_context.deadline = time.monotonic() + TOTAL_BUDGET_SECONDS
    try:
        latest, reason = _ghcr_latest_tag(image, selected_channel)
    finally:
        del _request_context.deadline
    # 上游 404「还没有发布版本」与「连不上」是两回事：前者可达、只是没有版本可更新。
    reachable = latest is not None or reason == "ghcr-no-release-tag"
    payload: dict[str, object] = {
        "ok": reachable,
        "reachable": reachable,
        "no_release": bool(reachable and latest is None),
        "checked_at": int(current_time),
        "cached": False,
        "channel": selected_channel,
        "current": current,
        "image": image,
        "repository": repo,
        "latest": latest,
        "update_available": None,
        "status": "no_release" if reachable else "unavailable",
        "host_command": "",
        "release_url": f"https://github.com/{repo}/releases",
        "error": "" if latest is not None else reason,
    }
    if latest is not None:
        comparison = _is_newer(str(latest.get("version") or ""), str(current.get("version") or ""))
        payload["update_available"] = comparison
        if comparison is None:
            payload["status"] = "floating"
        elif comparison:
            payload["status"] = "update_available"
        elif _semver_key(str(current["version"])) > _semver_key(str(latest["version"])):
            payload["status"] = "current_newer"
        else:
            payload["status"] = "up_to_date"
        tag = str(latest.get("tag") or latest.get("version") or "")
        reference = f"{image}:{tag}" if tag and tag != "latest" else f"{image}:latest"
        if payload["status"] != "current_newer":
            payload["host_command"] = f"sh ./deploy.sh --update --image {shlex.quote(reference)}"

    with _cache_lock:
        _cache["payload"] = payload
        _cache["key"] = cache_key
        _cache["expires_at"] = current_time + CACHE_TTL_SECONDS
    return payload
