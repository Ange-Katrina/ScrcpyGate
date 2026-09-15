import json
import logging
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request

from . import storage
from . import alas_secrets
from .alas_embed_policy import ALAS_UPSTREAM_RESPONSE_MAX_BYTES
from .alas_embed_transport import resolve_base_url, runtime_url_candidates
from .alas_network import OutboundTargetError, build_outbound_opener
from .alas_response import ResponseBodyTooLarge, read_bounded_response
from .booleans import parse_bool_strict

log = logging.getLogger("webscrcpy.alas")

API_PREFIX = "/api/gyre"
# Keep the legacy core name while sharing the same bounded-response setting as
# the Embed transport and its deployment environment variable.
ALAS_API_RESPONSE_MAX_BYTES = ALAS_UPSTREAM_RESPONSE_MAX_BYTES
ALAS_API_RESPONSE_TOO_LARGE = "ALAS API response too large"
ALAS_API_UNREACHABLE_DETAIL = "ALAS API unreachable"
INVALID_RUNTIME_URL_DETAIL = "invalid ALAS runtime URL"


def _read_api_json(response):
    raw = read_bounded_response(response, ALAS_API_RESPONSE_MAX_BYTES).decode("utf-8", errors="replace")
    return json.loads(raw) if raw else {}


def _close_http_error(error: HTTPError) -> None:
    """Release an HTTPError response without masking the original outcome."""
    try:
        error.close()
    except Exception:
        # A broken cleanup object must not change the public ALAS result.
        pass


def _http_error_details(error: HTTPError) -> tuple[object | None, str]:
    """Consume a bounded error body without exposing Runtime-provided text."""
    try:
        # Keep the status code available to callers while discarding Runtime
        # body text, which may contain internal paths, URLs, or diagnostics.
        read_bounded_response(error, ALAS_API_RESPONSE_MAX_BYTES)
    except ResponseBodyTooLarge:
        raise
    except Exception:
        pass
    finally:
        _close_http_error(error)
    return None, f"ALAS API HTTP {error.code}"


def _build_api_request(
    settings: dict,
    path: str,
    method: str,
    params: dict | None,
    body: object | None,
) -> tuple[str, Request]:
    normalized_method = method.upper()
    url = settings["base_url"] + API_PREFIX + "/" + str(path or "").lstrip("/")
    if params:
        url += "?" + urlencode(params)
    data = None
    headers = {"X-Alas-Gyre-Token": settings["token"]}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif normalized_method not in ("GET", "HEAD"):
        data = b""
    return url, Request(url, data=data, method=normalized_method, headers=headers)


def _probe_connection_candidate(
    candidate: str,
    token: str,
    token_configured: bool,
) -> tuple[dict | None, bool]:
    """Probe one Runtime candidate; return (result, stop_search)."""
    url = candidate + API_PREFIX + "/status"
    headers = {"X-Alas-Gyre-Token": token} if token else {}
    req = Request(url, method="GET", headers=headers)
    try:
        opener, _target = build_outbound_opener(url)
        with opener.open(req, timeout=4.0) as resp:
            read_bounded_response(resp, ALAS_API_RESPONSE_MAX_BYTES)
            return (
                {
                    "ok": True,
                    "state": "connected",
                    "base_url": candidate,
                    "token_configured": token_configured,
                    "status_code": resp.getcode(),
                },
                True,
            )
    except HTTPError as exc:
        try:
            try:
                _read_api_json(exc)
            except ResponseBodyTooLarge:
                return (
                    {
                        "ok": False,
                        "state": "response_too_large",
                        "base_url": candidate,
                        "token_configured": token_configured,
                        "status_code": exc.code,
                    },
                    True,
                )
            except Exception:
                pass
            if exc.code in (401, 403):
                state = "token_missing" if not token else "token_invalid"
                return (
                    {
                        "ok": False,
                        "state": state,
                        "base_url": candidate,
                        "token_configured": token_configured,
                        "status_code": exc.code,
                    },
                    True,
                )
            return (
                {
                    "ok": False,
                    "state": "http_error",
                    "base_url": candidate,
                    "token_configured": token_configured,
                    "status_code": exc.code,
                },
                False,
            )
        finally:
            _close_http_error(exc)

    except ResponseBodyTooLarge:
        return (
            {
                "ok": False,
                "state": "response_too_large",
                "base_url": candidate,
                "token_configured": token_configured,
            },
            True,
        )
    except (URLError, TimeoutError, OSError):
        return None, False


def _settings(include_token: bool = False) -> dict:
    data = storage.get_settings()
    legacy_config = data.get("alas_current_config") or "alas"
    raw_workbench_visible = str(data.get("workbench_alas_visible", "true") or "").strip().lower()
    result = {
        "enabled": data.get("alas_enabled", "false").lower() in ("1", "true", "yes", "on"),
        "base_url": (data.get("alas_base_url") or "http://127.0.0.1:22267").rstrip("/"),
        # Legacy fallback for old deployments that only had one ALAS config.
        # New UI paths use explicit per-user bindings instead.
        "current_config": legacy_config,
        "token_set": bool(data.get("alas_token")),
        "workbench_alas_visible": raw_workbench_visible in ("1", "true", "yes", "on"),
    }
    if include_token:
        try:
            result["token"], _source = alas_secrets.decrypt_token(data.get("alas_token", ""))
        except alas_secrets.AlasTokenError:
            result["token"] = ""
            result["token_error"] = True
    return result


def token_key_state() -> dict:
    """Redacted ALAS token encryption-key state for the administrator UI.

    Booleans and the source only — never the key, and never a filesystem path.
    """
    try:
        configured = alas_secrets.key_is_configured()
    except alas_secrets.AlasTokenError:
        configured = True
    return {
        "configured": configured,
        "valid": alas_secrets.key_is_valid(),
        "source": "env" if alas_secrets.injected_key_present() else ("file" if configured else "none"),
    }


def public_settings() -> dict:
    result = _settings(False)
    result.pop("current_config", None)
    return result


def legacy_config_name() -> str:
    return _settings(False).get("current_config") or "alas"


# ---- ALAS 页面功能开关（已改为写死） -----------------------------------------
# 目录（68 项 ALAS 任务）保留下来只用于**只读展示**：普通用户隐藏哪些游戏任务，现在由
# 下面的 HIDDEN_ALAS_TASKS 一处写死，不再有后台开关、不再读写数据库设置（历史键名
# `alas_feature_switches` 已不再读取）。
# 隐藏实现在两处：注入到被代理 HTML 的脚本（按 `--menu-<key>--` / 文字隐藏那一行），
# 以及 WebSocket 下游的按行摘除（`_frame_targets_hidden_feature`）。
# ALAS 2.0 适配时改 HIDDEN_ALAS_TASKS 即可。

# 对普通用户隐藏的游戏任务（写死，按目录里的条目 id）。
#   alas_settings      「Alas 设置」= 配置编辑器（同时由 alas_visibility 的 settings_tasks
#                      在服务端拦掉，这里一起标出来只是让只读展示与实际一致）
#   tools_perf_test    性能测试
#   tools_game_manager 游戏管理器（未完成）
# 页面/入口级的隐藏见 app/alas_visibility.py 的 HIDDEN_ALAS_ENTRY_RULES。
# ALAS 2.0 适配时改这里 + HIDDEN_ALAS_ENTRY_RULES 两处。
HIDDEN_ALAS_TASKS: tuple[str, ...] = (
    "alas_settings",
    "tools_perf_test",
    "tools_game_manager",
)


class FeatureSwitchesFixed(ValueError):
    """写死的游戏任务隐藏清单不接受修改（旧的后台保存接口用它给出明确提示）。"""

# (分组 id, 分组名, ((选项 id, 选项文字, ALAS 任务 key), ...))
# 选项文字用于显示与文字回退匹配（按空白压缩后比较）；ALAS 任务 key 对应页面上
# div[style*="--menu-<key>--"] 标记，用来精确定位某一行，避免同名/前缀文字互相误伤。
FEATURE_CATALOG: tuple[tuple[str, str, tuple[tuple[str, str, str], ...]], ...] = (
    ("alas", "Alas", (
        ("alas_settings", "Alas 设置", "Alas"),
        ("alas_general", "通用设置", "General"),
        ("alas_restart", "重启设置", "Restart"),
    )),
    ("sortie", "出击", (
        ("sortie_main", "主线图", "Main"),
        ("sortie_main_2", "主线图 - 2", "Main2"),
        ("sortie_main_3", "主线图 - 3", "Main3"),
        ("sortie_urgent", "刷紧急委托", "GemsFarming"),
    )),
    ("event", "活动", (
        ("event_general", "活动通用设置", "EventGeneral"),
        ("event_map", "活动图", "Event"),
        ("event_map_2", "活动图 - 2", "Event2"),
        ("event_coop", "共斗活动", "Raid"),
        ("event_valley_letter", "深谷来信", "Hospital"),
        ("event_ghost", "怪谈纪实", "Coalition"),
        ("event_shop", "活动商店", "EventShop"),
        ("event_archive", "作战档案", "WarArchives"),
    )),
    ("event_daily", "活动每日", (
        ("event_daily_a", "活动每日 A 图", "EventA"),
        ("event_daily_b", "活动每日 B 图", "EventB"),
        ("event_daily_c", "活动每日 C 图", "EventC"),
        ("event_daily_d", "活动每日 D 图", "EventD"),
        ("event_daily_sp", "活动每日 SP 图", "EventSp"),
        ("event_daily_coop", "共斗活动每日", "RaidDaily"),
        ("event_daily_ghost_sp", "怪谈纪实每日 SP", "CoalitionSp"),
    )),
    ("reward", "收获", (
        ("reward_commission", "委托", "Commission"),
        ("reward_tactical", "战术学院", "Tactical"),
        ("reward_research", "科研", "Research"),
        ("reward_dorm", "后宅", "Dorm"),
        ("reward_meowfficer", "指挥喵", "Meowfficer"),
        ("reward_guild", "大舰队", "Guild"),
        ("reward_harvest", "收获", "Reward"),
        ("reward_cognition", "认知觉醒", "Awaken"),
    )),
    ("daily", "每日任务", (
        ("daily_mission", "每日任务", "Daily"),
        ("daily_hard", "困难图", "Hard"),
        ("daily_exercise", "演习", "Exercise"),
        ("daily_shop", "通用商店", "ShopFrequent"),
        ("daily_shop_other", "其他商店", "ShopOnce"),
        ("daily_dockyard", "开发船坞", "Shipyard"),
        ("daily_gacha", "每日抽卡", "Gacha"),
        ("daily_free_reward", "白嫖奖励", "Freebies"),
        ("daily_minigame", "小游戏", "Minigame"),
        ("daily_dorm_plan", "宿舍计划", "PrivateQuarters"),
    )),
    ("world", "大世界", (
        ("world_general", "通用设置", "OpsiGeneral"),
        ("world_meta_battle", "META 作战", "OpsiAshBeacon"),
        ("world_meta_support", "META 支援", "OpsiAshAssist"),
        ("world_monthly", "每月开荒", "OpsiExplore"),
        ("world_shop", "大世界商店", "OpsiShop"),
        ("world_exchange_shop", "兑换商店", "OpsiVoucher"),
        ("world_daily", "大世界每日", "OpsiDaily"),
        ("world_hidden_zone", "隐秘海域", "OpsiObscure"),
        ("world_abyss", "深渊海域", "OpsiAbyssal"),
        ("world_archive_coord", "档案坐标", "OpsiArchive"),
        ("world_siren_fortress", "塞壬要塞", "OpsiStronghold"),
        ("world_monthly_boss", "月度 Boss", "OpsiMonthBoss"),
        ("world_short_cat", "短猫相接", "OpsiMeowfficerFarming"),
        ("world_erosion_1", "侵蚀 1 练级", "OpsiHazard1Leveling"),
        ("world_cross_month", "跨月每日", "OpsiCrossMonth"),
    )),
    ("island", "岛屿计划", (
        ("island_produce", "岛屿生产", "IslandProduction"),
        ("island_order", "岛屿订单", "IslandOrder"),
        ("island_supply", "每日补给", "IslandFreebie"),
        ("island_collect", "每日采集", "IslandCollect"),
        ("island_develop", "开发计划", "IslandSeasonTask"),
        ("island_shop", "店铺经营", "IslandBusiness"),
    )),
    ("tools", "工具", (
        ("tools_semi_click", "半自动点击", "Daemon"),
        ("tools_world_semi", "大世界半自动", "OpsiDaemon"),
        ("tools_event_story", "活动剧情", "EventStory"),
        ("tools_island_planner", "岛屿生产规划器", "IslandProductionPlanner"),
        ("tools_perf_test", "性能测试", "Benchmark"),
        ("anti_harmony", "反和谐", "AzurLaneUncensored"),
        ("tools_game_manager", "游戏管理器 (未完成)", "GameManager"),
    )),
)


def feature_catalog() -> list[dict]:
    """固定目录：分组 + 每项 {id, label, match, key, shared}。"""
    def compact(value: object) -> str:
        return re.sub(r"\s+", "", str(value or ""))

    occurrences: dict[str, int] = {}
    for _group_id, _group_label, items in FEATURE_CATALOG:
        for _item_id, label, _key in items:
            compacted = compact(label)
            occurrences[compacted] = occurrences.get(compacted, 0) + 1
    groups: list[dict] = []
    for group_id, group_label, items in FEATURE_CATALOG:
        groups.append({
            "id": group_id,
            "label": group_label,
            "items": [
                {
                    "id": item_id,
                    "label": label,
                    "match": label,
                    "key": key,
                    "shared": occurrences[compact(label)] > 1,
                }
                for item_id, label, key in items
            ],
        })
    return groups


def _catalog_ids() -> set[str]:
    return {item["id"] for group in feature_catalog() for item in group["items"]}


def _feature_enabled_map() -> dict[str, bool]:
    """写死的隐藏清单：HIDDEN_ALAS_TASKS 里的 id 为关闭，其余全部启用。"""
    hidden = {str(item).strip() for item in HIDDEN_ALAS_TASKS if str(item).strip()}
    return {identifier: identifier not in hidden for identifier in _catalog_ids()}


def feature_switches() -> list[dict]:
    """目录 + 写死的启停状态 + 分组汇总（只读展示用）。"""
    enabled_map = _feature_enabled_map()
    groups = feature_catalog()
    for group in groups:
        for item in group["items"]:
            item["enabled"] = enabled_map.get(item["id"], True)
        group["total"] = len(group["items"])
        group["enabled_count"] = sum(1 for item in group["items"] if item["enabled"])
        group["enabled"] = group["enabled_count"] == group["total"]
        group["partial"] = 0 < group["enabled_count"] < group["total"]
    return groups


def save_feature_switches(items: object) -> list[dict]:
    """游戏任务隐藏清单已写死，不再接受修改。"""
    raise FeatureSwitchesFixed("hidden ALAS tasks are fixed in code")


def hidden_feature_targets() -> list[dict]:
    """写死隐藏清单对应的隐藏目标：优先按 ALAS 任务 key 精确定位，文字作为回退。

    文字按空白压缩后比较 —— ALAS 页面上的按钮文字没有空格（如「主线图-2」），
    而目录里的显示文字带空格（「主线图 - 2」），压缩后两者一致。
    """
    enabled_map = _feature_enabled_map()
    targets: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for group in feature_catalog():
        for item in group["items"]:
            if enabled_map.get(item["id"], True):
                continue
            match = re.sub(r"\s+", "", str(item.get("match") or ""))
            key = str(item.get("key") or "")
            if not match and not key:
                continue
            signature = (key, match)
            if signature in seen:
                continue
            seen.add(signature)
            targets.append({"key": key, "match": match})
    return targets


def save_settings(payload: dict) -> None:
    if "enabled" in payload:
        enabled = parse_bool_strict(payload.get("enabled"))
        storage.set_setting("alas_enabled", "true" if enabled else "false")
    if payload.get("base_url") is not None:
        raw = str(payload.get("base_url") or "").strip().rstrip("/") or "http://127.0.0.1:22267"
        try:
            resolved = resolve_base_url(raw)
        except ValueError as exc:
            # 仅 URL 语法类错误(非法协议/主机/端口/凭据/路径)才拒绝保存;
            # Runtime 暂不可达不阻断配置: 保存规范化候选地址, 状态页继续显示不可达,
            # 待 Runtime 启动后按该地址生效(端口未指明时首选默认端口候选)。
            if "unreachable" not in str(exc):
                raise
            candidates = runtime_url_candidates(raw)
            resolved = candidates[0]
        storage.set_setting("alas_base_url", resolved)
    raw_default_config = payload.get("default_config", payload.get("current_config"))
    if raw_default_config is not None:
        config = sanitize_config_name(raw_default_config)
        storage.set_setting("alas_current_config", config)
    visible_value = payload.get("workbench_alas_visible", payload.get("workbenchAlasVisible"))
    if visible_value is not None:
        if isinstance(visible_value, bool):
            visible = visible_value
        else:
            visible = str(visible_value).strip().lower() in ("1", "true", "yes", "on")
        storage.set_setting("workbench_alas_visible", "true" if visible else "false")
    if "clear_token" in payload and parse_bool_strict(payload.get("clear_token")):
        storage.set_setting("alas_token", "")
    elif str(payload.get("api_token") or "").strip():
        token = str(payload.get("api_token") or "").strip()
        if len(token) > 512 or any(ch in token for ch in "\r\n\t "):
            raise ValueError("invalid ALAS token")
        try:
            encrypted = alas_secrets.encrypt_token(token)
        except alas_secrets.AlasTokenError as exc:
            raise ValueError("ALAS token encryption is not configured") from exc
        storage.set_setting("alas_token", encrypted)


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
    if settings.get("token_error"):
        return None, 503, "ALAS token encryption is unavailable"
    if not settings.get("token"):
        return None, 400, "ALAS API token is not configured"
    url, req = _build_api_request(settings, path, method, params, body)
    try:
        opener, _target = build_outbound_opener(url)
        with opener.open(req, timeout=timeout) as resp:
            return _read_api_json(resp), resp.getcode(), None
    except HTTPError as exc:
        try:
            payload, err = _http_error_details(exc)
        except ResponseBodyTooLarge:
            return None, 502, ALAS_API_RESPONSE_TOO_LARGE
        return payload, exc.code, err
    except ResponseBodyTooLarge:
        return None, 502, ALAS_API_RESPONSE_TOO_LARGE
    except OutboundTargetError:
        return None, 400, "ALAS API target is not allowed"
    except (URLError, TimeoutError, OSError):
        # Keep transport diagnostics in server logs only.  The Runtime URL
        # and lower-level exception may contain internal topology or secrets.
        return None, 502, ALAS_API_UNREACHABLE_DETAIL
    except (json.JSONDecodeError, RecursionError):
        return None, 502, "ALAS API returned invalid JSON"


def status(include_configs: bool = False) -> dict:
    return status_for_config(_settings(True).get("current_config") or "alas", include_configs=include_configs)


def check_connection(base_url: str | None = None) -> dict:
    """真实探测 Runtime 连接：区分可达性、Token 缺失/无效与成功。

    base_url 仅用于本次探测（抽屉里未保存的地址），不落盘；
    未提供时探测已保存的 base_url。探测使用已保存的 Token。
    """
    settings = _settings(True)
    token_configured = bool(settings.get("token"))
    # 不检查 enabled：抽屉流程是"先填地址检查、再启用保存"，显式管理操作且经 CSRF。
    if settings.get("token_error"):
        return {"ok": False, "state": "token_error", "token_configured": token_configured}
    raw = str(base_url or "").strip()
    if raw:
        try:
            candidates = runtime_url_candidates(raw)
        except ValueError:
            return {
                "ok": False,
                "state": "invalid_url",
                "detail": INVALID_RUNTIME_URL_DETAIL,
                "token_configured": token_configured,
            }
    else:
        candidates = [settings["base_url"]]
    result = {"ok": False, "state": "unreachable", "token_configured": token_configured}
    for candidate in candidates:
        candidate_result, stop_search = _probe_connection_candidate(
            candidate,
            settings.get("token", ""),
            token_configured,
        )
        if candidate_result is not None:
            result = candidate_result
        if stop_search:
            return candidate_result or result
    return result


def list_configs() -> dict:
    """Return the Runtime config catalog without also querying a config status."""
    payload, _, err = request_api("configs", timeout=2.0)
    if err:
        return {"ok": False, "configs": [], "error": err}
    if not isinstance(payload, dict) or not isinstance(payload.get("configs"), list):
        return {"ok": False, "configs": [], "error": "ALAS API returned an invalid config catalog"}
    configs: list[str] = []
    seen: set[str] = set()
    for item in payload["configs"]:
        if not isinstance(item, str):
            continue
        name = item.strip()
        if name and name not in seen:
            configs.append(name)
            seen.add(name)
    return {"ok": True, "configs": configs}


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
