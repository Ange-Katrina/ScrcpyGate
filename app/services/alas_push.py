"""Apply an ALAS-owned OnePush template to bound user configurations."""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import re

from .. import alas, storage
from ..logging_config import log_event

log = logging.getLogger("webscrcpy.alas_push")
SETTING = "alas_push_template"


def _onepush_text(data: dict) -> str:
    try:
        return str(data["Alas"]["Error"].get("OnePushConfig") or "")
    except (KeyError, TypeError, AttributeError):
        raise ValueError("ALAS OnePushConfig is unavailable") from None


def _parse(text: str) -> dict:
    import yaml

    if len(text) > 8192:
        raise ValueError("ALAS OnePushConfig is too large")
    try:
        documents = list(yaml.safe_load_all(text))
    except yaml.YAMLError as exc:
        raise ValueError("ALAS OnePushConfig is invalid YAML") from exc
    if any(not isinstance(item, dict) for item in documents):
        raise ValueError("ALAS OnePushConfig must contain mappings")
    merged: dict = {}
    for item in documents:
        merged.update(item)
    if not merged.get("provider") or merged["provider"] == "null":
        raise ValueError("ALAS template does not have a provider")
    return merged


def _field(config: dict, path: str, *, value=None):
    current = config
    parts = path.split(".")
    for part in parts[:-1]:
        current = current.get(part) if isinstance(current, dict) else None
        if not isinstance(current, dict):
            raise ValueError("ALAS recipient field is missing")
    if parts[-1] not in current:
        raise ValueError("ALAS recipient field is missing")
    if value is not None:
        current[parts[-1]] = value
    return current[parts[-1]]


def template_settings() -> dict:
    try:
        saved = json.loads(storage.get_setting(SETTING, "{}"))
    except (TypeError, ValueError):
        saved = {}
    return saved if isinstance(saved, dict) else {}


def save_template(config_name: str, recipient_field: str) -> dict:
    if not config_name:
        storage.set_setting(SETTING, "{}")
        return template_settings()
    name = alas.sanitize_config_name(config_name)
    path = str(recipient_field or "").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*){0,3}", path):
        raise ValueError("Invalid ALAS recipient field")
    result = alas.get_config(name)
    if not result.get("ok") or not isinstance(result.get("data"), dict):
        raise ValueError("ALAS template cannot be read")
    parsed = _parse(_onepush_text(result["data"]))
    _field(parsed, path)
    saved = {"config_name": name, "recipient_field": path}
    storage.set_setting(SETTING, json.dumps(saved))
    return saved


def clear_managed_config(config_name: str) -> dict:
    key = f"alas_push_managed:{config_name}"
    last_hash = storage.get_setting(key)
    if not last_hash:
        return {"status": "skipped", "reason": "not_managed"}
    result = alas.get_config(config_name)
    if not result.get("ok") or not isinstance(result.get("data"), dict):
        return {"status": "error", "reason": "runtime_unavailable"}
    target_data = result["data"]
    current = _onepush_text(target_data).strip()
    if hashlib.sha256(current.encode()).hexdigest() != last_hash:
        return {"status": "skipped", "reason": "manual_config_preserved"}
    updated = copy.deepcopy(target_data)
    updated["Alas"]["Error"]["OnePushConfig"] = "provider: null"
    saved = alas.save_config(config_name, config_name, updated, False)
    if not saved.get("ok"):
        return {"status": "error", "reason": "runtime_rejected"}
    storage.set_setting(key, "")
    log_event(log, "ALAS_PUSH_CONFIG_REMOVED", config=config_name)
    return {"status": "removed"}


def apply_to_binding(username: str, config_name: str) -> dict:
    user = storage.get_user(username)
    qq = str(user["qq"] or "") if user and "qq" in user.keys() else ""
    if not qq:
        return clear_managed_config(config_name)
    settings = template_settings()
    source = settings.get("config_name")
    if not source or source == config_name:
        return {"status": "skipped", "reason": "no_template_or_source"}
    import yaml

    target_result = alas.get_config(config_name)
    if not target_result.get("ok"):
        return {"status": "error", "reason": "runtime_unavailable"}
    target_data = target_result.get("data")
    if not isinstance(target_data, dict):
        return {"status": "error", "reason": "invalid_runtime_config"}
    current = _onepush_text(target_data).strip()
    last_hash = storage.get_setting(f"alas_push_managed:{config_name}")
    managed = bool(last_hash and hashlib.sha256(current.encode()).hexdigest() == last_hash)
    if current and current != "provider: null" and not managed:
        return {"status": "skipped", "reason": "manual_config_preserved"}
    source_result = alas.get_config(source)
    if not source_result.get("ok") or not isinstance(source_result.get("data"), dict):
        return {"status": "error", "reason": "runtime_unavailable"}
    parsed = _parse(_onepush_text(source_result["data"]))
    _field(parsed, settings["recipient_field"], value=int(qq) if parsed.get("provider") == "gocqhttp" else qq)
    rendered = yaml.safe_dump(parsed, allow_unicode=True, sort_keys=False).strip()
    updated = copy.deepcopy(target_data)
    updated["Alas"]["Error"]["OnePushConfig"] = rendered
    result = alas.save_config(config_name, config_name, updated, False)
    if not result.get("ok"):
        return {"status": "error", "reason": "runtime_rejected"}
    storage.set_setting(f"alas_push_managed:{config_name}", hashlib.sha256(rendered.encode()).hexdigest())
    log_event(log, "ALAS_PUSH_CONFIG_APPLIED", config=config_name, user=username)
    return {"status": "applied"}


def sync_user_bindings(username: str, config_names: list[str] | None = None) -> dict:
    names = config_names if config_names is not None else [
        str(row["config_name"]) for row in storage.list_user_alas_bindings(username)
    ]
    if not names:
        return {"status": "skipped", "reason": "no_bindings"}
    outcomes = {}
    for name in dict.fromkeys(names):
        try:
            outcomes[name] = apply_to_binding(username, name)
        except Exception as exc:
            log_event(log, "ALAS_PUSH_CONFIG_FAILED", level=logging.WARNING, error_type=type(exc).__name__)
            outcomes[name] = {"status": "error", "reason": "setup_failed"}
    if any(item["status"] == "error" for item in outcomes.values()):
        status, reason = "error", "partial_failure"
    elif any(item.get("reason") == "manual_config_preserved" for item in outcomes.values()):
        status, reason = "skipped", "manual_config_preserved"
    elif all(item["status"] == "skipped" for item in outcomes.values()):
        status, reason = "skipped", "no_action"
    else:
        status, reason = "applied", ""
    return {"status": status, "reason": reason, "configs": outcomes}


def clear_unbound_configs(config_names: list[str]) -> dict:
    outcomes = {}
    for name in dict.fromkeys(config_names):
        try:
            if storage.get_alas_binding_by_config(name):
                continue
            outcomes[name] = clear_managed_config(name)
        except Exception as exc:
            log_event(log, "ALAS_PUSH_CONFIG_FAILED", level=logging.WARNING, error_type=type(exc).__name__)
            outcomes[name] = {"status": "error", "reason": "setup_failed"}
    return {
        "status": "error" if any(item["status"] == "error" for item in outcomes.values()) else "skipped",
        "reason": "manual_config_preserved" if any(
            item.get("reason") == "manual_config_preserved" for item in outcomes.values()
        ) else "",
        "configs": outcomes,
    }
