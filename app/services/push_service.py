"""Account-scoped external notifications using OnePush providers."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import socket
import time
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from .. import storage
from ..alas_network import _NoRedirectHandler, _PinnedHTTPHandler, _PinnedHTTPSHandler
from ..alas_response import ResponseBodyTooLarge, read_bounded_response
from ..alas_secrets import decrypt_push_config, encrypt_push_config
from ..logging_config import log_event

log = logging.getLogger("webscrcpy.push")
SETTING = "push_onepush_config"
EVENTS = ("device_offline", "account_expiring", "account_expired")
VARIABLES = ("event", "username", "qq", "device", "title", "message", "expires_at", "days")
DEFAULT_TEMPLATE = {"user_id": "{qq}", "title": "{title}", "content": "{message}", "event": "{event}"}


def _resolved_target(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("invalid_notification_url")
    try:
        port = parsed.port
        if port is not None and not 1 <= port <= 65535:
            raise ValueError("invalid_notification_url")
        addresses = socket.getaddrinfo(parsed.hostname, port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
        private_allowed = {host.strip().lower() for host in os.environ.get("NOTIFICATION_ALLOWED_HOSTS", "").split(",") if host.strip()}
        if not addresses or not all(
            parsed.scheme == "https" if ipaddress.ip_address(entry[4][0]).is_global
            else parsed.hostname.lower() in private_allowed
            for entry in addresses
        ):
            raise ValueError("notification_target_unavailable")
        return addresses[0][4][0]
    except OSError as exc:
        raise ValueError("notification_target_unavailable") from exc


def _valid_url_syntax(url: str) -> bool:
    try:
        if any(ord(char) < 32 or ord(char) == 127 for char in url):
            return False
        parsed = urlsplit(url)
        return (
            parsed.scheme in ("http", "https") and bool(parsed.hostname)
            and not parsed.username and not parsed.password and not parsed.fragment
            and (parsed.port is None or 1 <= parsed.port <= 65535)
        )
    except ValueError:
        return False


def validate_config(payload: dict) -> dict:
    provider = str(payload.get("provider") or "custom").strip()
    if provider not in ("custom", "gocqhttp"):
        raise ValueError("unsupported_provider")
    url = str(payload.get("url") or "").strip()
    if len(url) > 2048 or not _valid_url_syntax(url):
        raise ValueError("invalid_notification_url")
    if provider == "gocqhttp" and urlsplit(url).query:
        raise ValueError("invalid_notification_url")
    events = payload.get("events", [])
    if not isinstance(events, list) or any(event not in EVENTS for event in events):
        raise ValueError("invalid_notification_events")
    headers = payload.get("headers", {})
    if not isinstance(headers, dict) or len(headers) > 8 or any(
        not isinstance(key, str) or not isinstance(value, str) or not key.isascii()
        or not key.replace("-", "").isalnum() or len(key) > 80 or len(value) > 512
        or key.lower() in ("host", "content-length", "connection", "transfer-encoding", "proxy-authorization")
        or "\r" in value or "\n" in value
        for key, value in headers.items()
    ):
        raise ValueError("invalid_notification_headers")
    template = payload.get("template") or DEFAULT_TEMPLATE
    if not isinstance(template, dict) or len(json.dumps(template, ensure_ascii=False)) > 4096:
        raise ValueError("invalid_notification_template")
    method = str(payload.get("method") or "POST").upper()
    if method not in ("GET", "POST"):
        raise ValueError("invalid_notification_method")
    token = str(payload.get("token") or "")
    if len(token) > 512:
        raise ValueError("invalid_notification_token")
    return {
        "provider": provider,
        "url": url,
        "token": token,
        "headers": headers,
        "template": template,
        "method": method,
        "events": list(dict.fromkeys(events)),
        "enabled": payload.get("enabled") is True,
    }


def save_config(payload: dict) -> dict:
    previous = load_config()
    if previous and str(payload.get("provider") or "custom") == previous.get("provider"):
        payload = dict(payload)
        clear = payload.get("clear") if isinstance(payload.get("clear"), list) else []
        for key in ("url", "token", "headers", "template"):
            if not payload.get(key) and key not in clear:
                payload[key] = previous.get(key)
    config = validate_config(payload)
    storage.set_setting(SETTING, encrypt_push_config(json.dumps(config, ensure_ascii=False)))
    return public_config(config)


def load_config() -> dict | None:
    encrypted = storage.get_setting(SETTING)
    if not encrypted:
        return None
    clear = decrypt_push_config(encrypted)
    config = json.loads(clear)
    return config if isinstance(config, dict) else None


def public_config(config: dict | None = None) -> dict:
    config = config if config is not None else load_config()
    return {
        "configured": bool(config),
        "provider": config.get("provider", "custom") if config else "custom",
        "enabled": bool(config and config.get("enabled")),
        "events": config.get("events", []) if config else [],
        "method": config.get("method", "POST") if config else "POST",
        "endpoint_host": urlsplit(config["url"]).hostname if config else "",
        "variables": VARIABLES,
        "template_set": bool(config and config.get("template")),
    }


def _replace(value, variables: dict[str, str]):
    if isinstance(value, dict):
        return {key: _replace(item, variables) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace(item, variables) for item in value]
    if isinstance(value, str):
        for key, item in variables.items():
            value = value.replace("{" + key + "}", item)
    return value


def _send(config: dict, variables: dict[str, str]) -> None:
    import requests
    from onepush import get_notifier

    url = config["url"].rstrip("/") if config["provider"] == "gocqhttp" else config["url"]
    notifier = get_notifier(config["provider"])

    def bounded_request(method, target, _proxies=None, **kwargs):
        if target != (url + "/send_msg" if config["provider"] == "gocqhttp" else url):
            raise ValueError("notification_target_changed")
        address = _resolved_target(target)
        headers = dict(config.get("headers") or {})
        body = None
        if "params" in kwargs:
            query = urlencode({key: value for key, value in kwargs["params"].items() if value is not None})
            target += ("&" if urlsplit(target).query else "?") + query
        elif "json" in kwargs:
            body = json.dumps(kwargs["json"], ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        else:
            raise ValueError("notification_request_unsupported")
        request = Request(target, data=body, headers=headers, method=method.upper())
        opener = build_opener(
            ProxyHandler({}), _NoRedirectHandler(), _PinnedHTTPHandler(address), _PinnedHTTPSHandler(address)
        )
        try:
            with opener.open(request, timeout=5) as upstream:
                content = read_bounded_response(upstream, 65536, chunk_size=8192)
                status_code = upstream.status
                response_headers = dict(upstream.headers)
        except HTTPError as exc:
            exc.close()
            raise ValueError("notification_http_error") from None
        except ResponseBodyTooLarge:
            raise ValueError("notification_response_too_large") from None
        response = requests.Response()
        response.status_code = status_code
        response.url = target
        response.headers.update(response_headers)
        response._content = content
        return response

    notifier.request = bounded_request
    if config["provider"] == "gocqhttp":
        response = notifier.notify(
            endpoint=url, token=config.get("token"), user_id=int(variables["qq"]),
            message_type="private", title=variables["title"], content=variables["message"],
        )
    else:
        response = notifier.notify(
            url=url, method=config.get("method", "POST"), datatype="json",
            data=_replace(config["template"], variables),
        )
    if response is None:
        raise RuntimeError("notification_no_response")
    if config["provider"] == "gocqhttp":
        try:
            result = response.json()
        except ValueError as exc:
            raise RuntimeError("notification_invalid_response") from exc
        if not isinstance(result, dict) or result.get("retcode") not in (0, "0") or result.get("status") == "failed":
            raise RuntimeError("notification_rejected")


def _enqueue(event_key: str, username: str, event: str, data: dict) -> None:
    with storage.db_connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO push_deliveries(event_key,username,event,data_json,created_at) VALUES(?,?,?,?,?)",
            (event_key, username, event, json.dumps(data, ensure_ascii=False), int(time.time())),
        )
        conn.commit()


def queue_device_offline(device_id: str, device_name: str, incident: int) -> None:
    if not (config := load_config()) or not config.get("enabled") or "device_offline" not in config["events"]:
        return
    with storage.db_connect() as conn:
        users = conn.execute(
            "SELECT DISTINCT u.username FROM users u LEFT JOIN user_devices ud ON ud.username=u.username "
            "WHERE u.qq!='' AND u.enabled=1 AND (u.expires_at IS NULL OR u.expires_at>?) "
            "AND (u.role='admin' OR (ud.device_id=? AND ud.can_view=1))",
            (int(time.time()), device_id),
        ).fetchall()
    for user in users:
        _enqueue(f"device_offline:{device_id}:{incident}:{user['username']}", user["username"],
                 "device_offline", {"device": device_name})


def queue_account_events() -> None:
    if not (config := load_config()) or not config.get("enabled"):
        return
    with storage.db_connect() as conn:
        users = conn.execute(
            "SELECT username,qq,expires_at,enabled,role FROM users "
            "WHERE qq!='' AND enabled=1 AND expires_at IS NOT NULL"
        ).fetchall()
    for user in users:
        payload = storage.user_expiration_payload(user)
        state = payload.get("expiration_state")
        event = f"account_{state}"
        if event not in config["events"] or state not in ("expiring", "expired"):
            continue
        expires = int(user["expires_at"])
        days = max(0, (int(payload.get("remaining_seconds") or 0) + 86399) // 86400)
        _enqueue(f"{event}:{user['username']}:{expires}", user["username"], event,
                 {"expires_at": expires, "days": days})


def deliver_pending() -> None:
    config = load_config()
    if not config or not config.get("enabled"):
        return
    now = int(time.time())
    with storage.db_connect() as conn:
        conn.execute("DELETE FROM push_deliveries WHERE created_at<? OR username NOT IN (SELECT username FROM users)",
                     (now - 90 * 86400,))
        rows = conn.execute(
            "SELECT p.*,u.qq,u.enabled,u.expires_at FROM push_deliveries p JOIN users u ON u.username=p.username "
            "WHERE p.state='pending' AND p.next_attempt_at<=? AND u.qq!='' ORDER BY p.id LIMIT 8", (now,),
        ).fetchall()
        conn.commit()
    for row in rows:
        event = row["event"]
        data = json.loads(row["data_json"])
        stale = (
            event not in config["events"]
            or not bool(row["enabled"])
            or (event.startswith("account_") and data.get("expires_at") != row["expires_at"])
            or (event == "account_expiring" and now >= int(data.get("expires_at") or 0))
            or (event == "device_offline" and (
                now - row["created_at"] > 3600
                or (row["expires_at"] is not None and row["expires_at"] <= now)
            ))
        )
        if stale:
            with storage.db_connect() as conn:
                conn.execute("UPDATE push_deliveries SET state='skipped' WHERE id=?", (row["id"],))
                conn.commit()
            continue
        title = {"device_offline": "设备离线", "account_expiring": "账户即将到期", "account_expired": "账户已到期"}[event]
        expires_at = (
            datetime.fromtimestamp(int(data["expires_at"]), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            if data.get("expires_at") else ""
        )
        message = data.get("device", "") if event == "device_offline" else f"{data.get('days', 0)} 天 · {expires_at}"
        variables = {key: str(value) for key, value in {
            "event": event, "username": row["username"], "qq": row["qq"],
            "device": data.get("device", ""), "title": title, "message": message,
            "expires_at": expires_at, "days": data.get("days", ""),
        }.items()}
        try:
            _send(config, variables)
            state, next_at = "sent", 0
        except (ValueError, OSError, RuntimeError, ImportError) as exc:
            state = "failed" if row["attempts"] >= 5 else "pending"
            next_at = now + min(3600, 60 * 2 ** min(row["attempts"], 6))
            log_event(log, "PUSH_SEND_FAILED", level=logging.WARNING, error_type=type(exc).__name__, event=event)
        with storage.db_connect() as conn:
            conn.execute(
                "UPDATE push_deliveries SET state=?,attempts=attempts+1,next_attempt_at=? WHERE id=?",
                (state, next_at, row["id"]),
            )
            conn.commit()


def recent_deliveries() -> list[dict]:
    with storage.db_connect() as conn:
        rows = conn.execute(
            "SELECT username,event,state,attempts,created_at FROM push_deliveries ORDER BY id DESC LIMIT 20"
        ).fetchall()
    return [dict(row) for row in rows]


async def push_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(queue_account_events)
            await asyncio.to_thread(deliver_pending)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log_event(log, "PUSH_SWEEP_FAILED", level=logging.WARNING, error_type=type(exc).__name__)
        await asyncio.sleep(60)
