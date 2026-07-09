import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener, ProxyHandler

from . import storage
from .alas_embed import resolve_base_url

API_PREFIX = "/api/gyre"


def _settings(include_token: bool = False) -> dict:
    data = storage.get_settings()
    default_config = data.get("alas_current_config") or "alas"
    result = {
        "enabled": data.get("alas_enabled", "false").lower() in ("1", "true", "yes", "on"),
        "base_url": (data.get("alas_base_url") or "http://127.0.0.1:22267").rstrip("/"),
        "default_config": default_config,
        # Backward-compatible alias. The value is now treated as the admin/default
        # config, not a single global runtime owner.
        "current_config": default_config,
        "token_set": bool(data.get("alas_token")),
    }
    if include_token:
        result["token"] = data.get("alas_token", "")
    return result


def public_settings() -> dict:
    return _settings(False)


def save_settings(payload: dict) -> None:
    if "enabled" in payload:
        storage.set_setting("alas_enabled", "true" if payload.get("enabled") else "false")
    if payload.get("base_url") is not None:
        raw = str(payload.get("base_url") or "").strip().rstrip("/") or "http://127.0.0.1:22267"
        storage.set_setting("alas_base_url", resolve_base_url(raw))
    raw_default_config = payload.get("default_config", payload.get("current_config"))
    if raw_default_config is not None:
        config = sanitize_config_name(raw_default_config)
        storage.set_setting("alas_current_config", config)
    if payload.get("clear_token"):
        storage.set_setting("alas_token", "")
    elif str(payload.get("api_token") or "").strip():
        token = str(payload.get("api_token") or "").strip()
        if len(token) > 512 or any(ch in token for ch in "\r\n\t "):
            raise ValueError("invalid ALAS token")
        storage.set_setting("alas_token", token)


def sanitize_config_name(value: object) -> str:
    name = str(value or "").strip()
    if name.endswith(".json"):
        name = name[:-5]
    invalid = set('/\\:*?"<>|')
    if not name or name in (".", "..") or any(ch in invalid for ch in name) or "/" in name or "\\" in name or name.startswith("template") or len(name) > 120:
        raise ValueError("invalid config name")
    return name


def request_api(path: str, method: str = "GET", params: dict | None = None, body: object | None = None, timeout: float = 3.0):
    settings = _settings(True)
    if not settings.get("enabled"):
        return None, 400, "ALAS control is disabled"
    if not settings.get("token"):
        return None, 400, "ALAS API token is not configured"
    url = settings["base_url"] + API_PREFIX + "/" + str(path or "").lstrip("/")
    if params:
        url += "?" + urlencode(params)
    data = None
    headers = {"X-Alas-Gyre-Token": settings["token"]}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif method.upper() not in ("GET", "HEAD"):
        data = b""
    opener = build_opener(ProxyHandler({}))
    req = Request(url, data=data, method=method.upper(), headers=headers)
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else {}, resp.getcode(), None
    except HTTPError as exc:
        try:
            raw = exc.read().decode("utf-8", errors="replace")
            payload = json.loads(raw) if raw else {}
            err = payload.get("error") or payload.get("message") or f"ALAS API HTTP {exc.code}"
        except Exception:
            payload = None
            err = f"ALAS API HTTP {exc.code}"
        return payload, exc.code, err
    except (URLError, TimeoutError, OSError) as exc:
        return None, 502, f"ALAS API unreachable: {exc}"
    except json.JSONDecodeError:
        return None, 502, "ALAS API returned invalid JSON"


def status(include_configs: bool = False) -> dict:
    return status_for_config(_settings(True).get("current_config") or "alas", include_configs=include_configs)


def status_for_config(config_name: str, include_configs: bool = False) -> dict:
    settings = _settings(True)
    config_name = sanitize_config_name(config_name or settings.get("current_config") or "alas")
    result = {
        "ok": True,
        "settings": public_settings(),
        "configured": bool(settings.get("enabled") and settings.get("token_set")),
        "status": "disabled" if not settings.get("enabled") else "disconnected",
        "task": "",
        "config": config_name,
        "configs": [config_name],
    }
    if not settings.get("enabled"):
        return result
    if not settings.get("token_set"):
        result.update({"ok": False, "error": "ALAS API token is not configured"})
        return result
    if include_configs:
        payload, _, err = request_api("configs", timeout=2.0)
        if err:
            result.update({"ok": False, "error": err})
        elif isinstance(payload, dict) and isinstance(payload.get("configs"), list):
            result["configs"] = [str(item) for item in payload["configs"] if str(item)]
    payload, _, err = request_api("status", params={"config": config_name}, timeout=2.0)
    if err:
        result.update({"ok": False, "error": err})
        return result
    if isinstance(payload, dict):
        result["status"] = str(payload.get("status") or "disconnected")
        result["task"] = str(payload.get("task") or "")
        result["config"] = str(payload.get("config") or config_name)
    return result


def control(action: str) -> dict:
    current = _settings(True).get("current_config") or "alas"
    return control_for_config(action, current)


def control_for_config(action: str, config_name: str) -> dict:
    current = sanitize_config_name(config_name)
    if action == "toggle":
        current_status = status_for_config(current, False).get("status")
        action = "stop" if current_status == "running" else "restart"
    if action == "restart":
        payload, code, err = request_api("restart", method="POST", params={"config": current}, timeout=8.0)
        if err and code == 404:
            request_api("stop", method="POST", params={"config": current}, timeout=5.0)
            payload, code, err = request_api("start", method="POST", params={"config": current}, timeout=8.0)
    else:
        payload, code, err = request_api(action, method="POST", params={"config": current}, timeout=8.0)
    if err:
        return {"ok": False, "error": err, "status_code": code}
    return {"ok": True, "action": action, "config": current, "result": payload, "alas": status_for_config(current, False)}


def get_config(config_name: str | None = None) -> dict:
    current = sanitize_config_name(config_name or _settings(True).get("current_config") or "alas")
    payload, code, err = request_api("config", params={"config": current}, timeout=4.0)
    if err:
        return {"ok": False, "error": err, "status_code": code}
    return {"ok": True, "config": current, "data": payload.get("data", payload) if isinstance(payload, dict) else payload}


def save_config(source: str, target: str, data: object, update_current: bool = True) -> dict:
    source = sanitize_config_name(source)
    target = sanitize_config_name(target)
    st = status_for_config(target, False).get("status")
    if st == "running":
        return {"ok": False, "error": "Stop ALAS before saving config", "status_code": 409}
    payload, code, err = request_api("config", method="PUT", params={"config": source, "target": target}, body={"data": data}, timeout=6.0)
    if err:
        return {"ok": False, "error": err, "status_code": code}
    if update_current:
        storage.set_setting("alas_current_config", target)
    return {"ok": True, "config": target, "result": payload, "alas": status_for_config(target, True)}
