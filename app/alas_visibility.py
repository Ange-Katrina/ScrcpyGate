"""Hidden ALAS entries for bound users — **fixed in code, no switches**.

普通用户看不到哪些 ALAS 页面/入口，现在由本文件里的 ``DEFAULT_VISIBILITY_RULES``
一处写死：不再有后台开关、不再读写数据库设置，判定一律走 ``visibility_rules()``
的规范化快照（策略/内容过滤/客户端点击门都从这里取）。

为什么要写死：ALAS 2.0 即将发布，页面/入口会重新适配；在那之前维护两套可配置目录
（68 项游戏任务 + 17 条可见性条目）只会带来重复与歧义。适配 2.0 时直接改这里的
常量即可，改完把对应页面入口重新走一遍。

仍然硬编码、且不属于本文件的边界：其它配置入口、管理路径 403、设备地址打码。
"""

from __future__ import annotations

import threading

# 历史设置键名 `alas_visibility_rules` 已不再读取（隐藏范围写死）；同名的旧值可以留着不管。


class VisibilityRulesFixed(ValueError):
    """写死的隐藏清单不接受修改（旧的后台保存接口用它给出明确提示）。"""


# 普通用户看不到的 ALAS 入口 —— 现在**写死在这里**，只改这一处。
# 仍然硬编码、且不属于本文件的边界：其它配置入口（会看到别人的配置与设备地址）
# 与管理路径 403（proxy_decision 里的固定检查）。
HIDDEN_ALAS_ENTRY_RULES: dict[str, tuple[str, ...]] = {
    # 左侧导航里对普通用户隐藏的入口标签（配置列表/管理/更新器/远程控制）
    "labels": (
        "配置",
        "配置列表",
        "config",
        "configs",
        "configlist",
        "管理",
        "admin",
        "manage",
        "management",
        "更新器",
        "检查更新",
        "update",
        "updater",
        "checkupdate",
        "upgrade",
        "远程控制",
        "remote",
        "remotecontrol",
    ),
    # 路由/回调名里命中这些词即视为受限入口（命中即 403 / 断会话）
    "route_markers": (
        "configlist",
        "admin",
        "manage",
        "management",
        "updater",
        "checkupdate",
        "selfupdate",
        "remotecontrol",
    ),
    # 管理员专用游戏任务（daemon_ = 半自动点击 …）：当前**不隐藏**
    "field_prefixes": (),
    # ALAS 设置页里的项（模拟器/重启模拟器/性能优化/录屏等）：当前**不隐藏**
    "settings_markers": (),
    # 「Alas 设置」任务本身（配置编辑器）：**隐藏**（普通用户看不到、也进不去编辑页）
    "settings_tasks": ("alas", "alas设置", "alassettings"),
}

# 旧名字保留为同一份清单的别名（策略/测试仍在用这个名字）。
DEFAULT_VISIBILITY_RULES = HIDDEN_ALAS_ENTRY_RULES

RULE_KEYS: tuple[str, ...] = tuple(DEFAULT_VISIBILITY_RULES)

# 给管理员看的目录：把写死清单里的匹配 token 归纳成「ALAS 页面 / 功能」条目。
# 每个 token 只属于一个条目（见 tests/test_alas_visibility.py 的划分校验），
# /alas 的「隐藏范围」页签按条目显示当前状态（只读，改范围改常量）。
VISIBILITY_ENTRY_GROUPS: tuple[dict[str, str], ...] = (
    {
        "id": "nav",
        "label": "导航入口（整页）",
        "hint": "普通用户在 ALAS 左侧看不到这些入口，直接访问对应地址也会被拒绝（403）。",
    },
    {
        "id": "tasks",
        "label": "管理员专用任务",
        "hint": "普通用户的任务列表里不出现这些任务，也无法通过接口触发。",
    },
    {
        "id": "settings",
        "label": "模拟器与录制设置",
        "hint": "普通用户在 ALAS 设置页看不到这些项。",
    },
    {
        "id": "editor",
        "label": "配置编辑器",
        "hint": "「Alas 设置」任务本身 —— 打开后能直接编辑 ALAS 的配置文件。",
    },
)

VISIBILITY_ENTRY_CATALOG: tuple[dict[str, object], ...] = (
    {
        "id": "nav_config_list",
        "group": "nav",
        "label": "配置列表",
        "hint": "列出并切换 ALAS 配置（/configlist）",
        "tokens": {
            "labels": ("配置", "配置列表", "config", "configs", "configlist"),
            "route_markers": ("configlist",),
        },
    },
    {
        "id": "nav_manage",
        "group": "nav",
        "label": "管理",
        "hint": "ALAS 后台管理入口（/admin、/manage）",
        "tokens": {
            "labels": ("管理", "admin", "manage", "management"),
            "route_markers": ("admin", "manage", "management"),
        },
    },
    {
        "id": "nav_updater",
        "group": "nav",
        "label": "更新器",
        "hint": "检查并执行 ALAS 更新（/updater、/checkupdate）",
        "tokens": {
            "labels": ("更新器", "检查更新", "update", "updater", "checkupdate", "upgrade"),
            "route_markers": ("updater", "checkupdate", "selfupdate"),
        },
    },
    {
        "id": "nav_remote",
        "group": "nav",
        "label": "远程控制",
        "hint": "远程操控 ALAS（/remotecontrol）",
        "tokens": {
            "labels": ("远程控制", "remote", "remotecontrol"),
            "route_markers": ("remotecontrol",),
        },
    },
    {
        "id": "nav_toolbox",
        "group": "nav",
        "label": "工具",
        "hint": "脚本工具箱页面（/toolbox）",
        "tokens": {
            "labels": ("工具", "tool", "tools", "toolbox"),
            "route_markers": ("toolbox",),
        },
    },
    {
        "id": "task_daemon",
        "group": "tasks",
        "label": "半自动点击",
        "hint": "daemon_ 系列任务（模拟点击辅助）",
        "tokens": {"field_prefixes": ("daemon_",)},
    },
    {
        "id": "task_opsi_daemon",
        "group": "tasks",
        "label": "大世界半自动",
        "hint": "opsidaemon_ 系列任务",
        "tokens": {"field_prefixes": ("opsidaemon_",)},
    },
    {
        "id": "task_event_story",
        "group": "tasks",
        "label": "活动剧情",
        "hint": "eventstory_ 系列任务",
        "tokens": {"field_prefixes": ("eventstory_",)},
    },
    {
        "id": "task_benchmark",
        "group": "tasks",
        "label": "性能测试",
        "hint": "benchmark_ 系列任务",
        "tokens": {"field_prefixes": ("benchmark_",)},
    },
    {
        "id": "task_uncensored",
        "group": "tasks",
        "label": "反和谐",
        "hint": "AzurLaneUncensored（azurlaneuncensored_）",
        "tokens": {"field_prefixes": ("azurlaneuncensored_",)},
    },
    {
        "id": "task_game_manager",
        "group": "tasks",
        "label": "游戏管理器",
        "hint": "GameManager（gamemanager_，ALAS 里尚未完成）",
        "tokens": {"field_prefixes": ("gamemanager_",)},
    },
    {
        "id": "setting_emulator",
        "group": "settings",
        "label": "模拟器",
        "hint": "alas.emulator（模拟器路径与启动参数）",
        "tokens": {"settings_markers": ("alas.emulator",)},
    },
    {
        "id": "setting_restart_emulator",
        "group": "settings",
        "label": "重启模拟器",
        "hint": "alas.restartemulator",
        "tokens": {"settings_markers": ("alas.restartemulator",)},
    },
    {
        "id": "setting_optimization",
        "group": "settings",
        "label": "性能优化",
        "hint": "alas.optimization",
        "tokens": {"settings_markers": ("alas.optimization",)},
    },
    {
        "id": "setting_drop_record",
        "group": "settings",
        "label": "录屏",
        "hint": "alas.droprecord（战斗录屏）",
        "tokens": {"settings_markers": ("alas.droprecord",)},
    },
    {
        "id": "setting_emulator_serial",
        "group": "settings",
        "label": "模拟器序列号",
        "hint": "emulator.serial（绑定模拟器实例）",
        "tokens": {"settings_markers": ("emulator.serial",)},
    },
    {
        "id": "settings_task_alas",
        "group": "editor",
        "label": "Alas 设置（配置编辑器）",
        "hint": "任务 alas / Alas设置 / AlasSettings",
        "tokens": {"settings_tasks": ("alas", "alas设置", "alassettings")},
    },
)

_cache: dict | None = None
_lock = threading.Lock()


def _compact(value: object) -> str:
    """与 alas_embed_policy._compact_text 完全一致的模糊匹配归一化。

    只保留字母数字并转小写：ALAS 的字段名用下划线（Alas_Emulator_Serial）、
    设置项用点号（alas.emulator），两边必须按同一种形式比较。
    """
    return "".join(char.lower() for char in str(value or "") if char.isalnum())


def _normalized_field(value: object) -> str:
    return "".join(char.lower() for char in str(value or "") if char.isalnum() or char == "_")


def _defaults() -> dict[str, list[str]]:
    return {key: list(value) for key, value in DEFAULT_VISIBILITY_RULES.items()}


def _build(raw: dict[str, list[str]]) -> dict:
    """Precompute the normalized forms the policy checks on every message."""
    return {
        "raw": raw,
        "labels": frozenset(_compact(item) for item in raw["labels"]),
        "route_markers": tuple(_compact(item) for item in raw["route_markers"]),
        "field_prefixes": tuple(_normalized_field(item) for item in raw["field_prefixes"]),
        "settings_markers": tuple(raw["settings_markers"]),
        "settings_markers_compact": tuple(_compact(item) for item in raw["settings_markers"]),
        "settings_tasks": frozenset(_compact(item) for item in raw["settings_tasks"]),
    }


def _load() -> dict:
    """写死的隐藏清单（不再读写数据库设置）。"""
    return _build(_defaults())


def visibility_rules() -> dict:
    """Cached normalized rules (safe on the per-message policy path)."""
    global _cache
    cached = _cache
    if cached is None:
        with _lock:
            cached = _cache
            if cached is None:
                cached = _load()
                _cache = cached
    return cached


def refresh_visibility_rules() -> dict:
    """Reload from storage (startup hydration and after a write)."""
    global _cache
    with _lock:
        _cache = _load()
        return _cache


def load_visibility_rules() -> dict:
    return refresh_visibility_rules()


def visibility_snapshot() -> dict:
    """Admin view of the **fixed** hidden set: the lists plus the named catalog.

    `hardcoded` 告诉界面不要再提供开关：改隐藏范围要改代码（ALAS 2.0 适配时）。
    只读页面用 `rules`（判定当前状态）+ `entry_groups`/`entries`（展示目录）。
    """
    raw = visibility_rules()["raw"]
    return {
        "hardcoded": True,
        "rules": {key: list(raw[key]) for key in RULE_KEYS},
        "entry_groups": [dict(group) for group in VISIBILITY_ENTRY_GROUPS],
        "entries": [
            {
                "id": entry["id"],
                "group": entry["group"],
                "label": entry["label"],
                "hint": entry["hint"],
                "tokens": {key: list(value) for key, value in dict(entry["tokens"]).items()},
            }
            for entry in VISIBILITY_ENTRY_CATALOG
        ],
    }


def save_visibility_rules(payload: object) -> dict:
    """隐藏清单已写死，不再接受修改。"""
    raise VisibilityRulesFixed("hidden ALAS entries are fixed in code")
