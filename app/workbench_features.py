"""投屏工作台功能开关与菜单编排：按角色（管理员 / 普通用户）控制工作台界面。

两个设置键：

* ``workbench_feature_switches`` —— 启用/停用（按角色分组的布尔表）。
* ``workbench_menu_layout`` —— 底部菜单编排：一级（控制栏）/ 二级（「更多」菜单）
  各自的功能顺序。拖进某一级即启用并归入该级，拖出到未启用区即停用。

「投屏行为」（``WORKBENCH_DISPLAY_FEATURES``，如全屏打开控制栏时画面上移）只是开关：
不放进菜单编排，所以没有 ``levels``，也不参与 ``_apply_layout_to_switches`` 的编排折算。

设计边界：

* 只控制**前端可见性与可用性**（底部控制栏按钮、顶部状态条与通知中心）。
  权限判定完全不看这里：接口鉴权、控制权锁、ALAS 权限一律走原有逻辑。
* 缺省即启用 / 缺省即当前排布。键缺失、角色缺失、取值非法、JSON 损坏都退化为默认，
  老部署升级后工作台与之前完全一致，脏数据也不会锁死界面。
* 未知功能 id 会被忽略（不写入存储）。
* 状态条按**功能块**（设备连接 / 观看端 / 剩余时长 / ALAS / 画质）各一个开关，
  不做字段级拆分。
* ALAS 入口的显隐仍由 ``workbench_alas_visible``（/alas 页面的「工作台显示」）控制；
  这里的 ``@alas`` 只是一个**位置锚点**（没有开关），避免两个真值来源。
"""

from __future__ import annotations

import json

# 存储键：JSON 字符串，按角色分组。
SETTING_KEY = "workbench_feature_switches"
# 菜单编排（一级/二级 + 顺序），同样是按角色分组的 JSON。
LAYOUT_KEY = "workbench_menu_layout"

WORKBENCH_ROLES: tuple[str, ...] = ("admin", "user")

ROLE_LABELS: dict[str, str] = {
    "admin": "管理员",
    "user": "普通用户",
}

# ALAS 入口只作为一级菜单里的位置锚点参与排序，没有独立开关。
ALAS_ANCHOR = "@alas"

WORKBENCH_FEATURE_GROUPS: tuple[dict[str, str], ...] = (
    {
        "id": "topbar",
        "label": "顶部状态条",
        "hint": "按工作台里的分组控制：每一块可以单独关闭，关闭后整块（含分隔线）都不显示。",
    },
    {
        "id": "dock",
        "label": "底部控制栏",
        "hint": "一级菜单显示在画面下方，二级菜单收在「更多」里；拖进对应块即启用，并可调整顺序。",
    },
    {
        "id": "display",
        "label": "投屏行为",
        "hint": "画面与控制栏的相处方式；只影响观感，不参与任何权限判定",
    },
)

# 状态条分组：``preview`` 是后台页面按工作台样式画预览用的分段文本。
# kind: label=小标题、value=数值、state=带圆点的状态、sub=次级灰、mono=等宽数值。
WORKBENCH_TOP_STATUS_FEATURES: tuple[dict[str, object], ...] = (
    {
        "id": "status",
        "group": "topbar",
        "label": "设备状态条",
        "icon": "panel-top",
        "hint": "整条状态条的总开关，关闭后下面各块都不显示",
    },
    {
        "id": "status_device",
        "group": "topbar",
        "parent": "status",
        "label": "设备连接",
        "icon": "smartphone",
        "hint": "设备名称、在线圆点与连接状态",
        "preview": [
            {"text": "设备连接", "kind": "label"},
            {"text": "123", "kind": "value"},
            {"text": "在线", "kind": "state"},
        ],
    },
    {
        "id": "status_viewers",
        "group": "topbar",
        "parent": "status",
        "label": "观看端",
        "icon": "users",
        "hint": "观看端小标题与当前人数",
        "preview": [
            {"text": "观看端", "kind": "label"},
            {"text": "0", "kind": "value"},
        ],
    },
    {
        "id": "status_countdown",
        "group": "topbar",
        "parent": "status",
        "label": "剩余时长",
        "icon": "hourglass",
        "hint": "本次投屏的剩余时长（仅在有上限时出现）",
        "preview": [
            {"text": "剩余", "kind": "label"},
            {"text": "45:00", "kind": "mono"},
        ],
    },
    {
        "id": "status_alas",
        "group": "topbar",
        "parent": "status",
        "label": "ALAS",
        "icon": "bot",
        "hint": "ALAS 小标题、圆点与运行状态（仅绑定后出现）",
        "preview": [
            {"text": "ALAS", "kind": "label"},
            {"text": "运行中", "kind": "state"},
        ],
    },
    {
        "id": "status_quality",
        "group": "topbar",
        "parent": "status",
        "label": "画质",
        "icon": "sliders-horizontal",
        "hint": "画质小标题、档位名称与分辨率/码率摘要",
        "preview": [
            {"text": "画质", "kind": "label"},
            {"text": "均衡", "kind": "value"},
            {"text": "720p · 3.5 Mbps", "kind": "sub"},
        ],
    },
    {
        "id": "notifications",
        "group": "topbar",
        "label": "通知中心",
        "icon": "bell",
        "hint": "顶部右侧的通知铃铛与消息面板",
    },
)

# 底部菜单功能：``levels`` 是允许出现的层级，``level`` 是默认层级；
# ``dock_group`` 决定控制栏里在哪两个按钮之间画分隔线；
# ``anchor`` 只参与排序；``container`` 是二级菜单的容器（「更多」按钮）。
WORKBENCH_DOCK_FEATURES: tuple[dict[str, object], ...] = (
    {
        "id": "watch",
        "group": "dock",
        "label": "开始投屏",
        "hint": "开始 / 停止观看实时画面",
        "icon": "play",
        "dock_group": "session",
        "levels": [1, 2],
        "level": 1,
    },
    {
        "id": "acquire",
        "group": "dock",
        "label": "获取控制",
        "hint": "申请或释放设备控制权",
        "icon": "mouse-pointer-click",
        "dock_group": "control",
        "accent": "primary",
        "levels": [1, 2],
        "level": 1,
    },
    {
        "id": "keyboard",
        "group": "dock",
        "label": "键盘输入",
        "hint": "把电脑键盘输入发送到设备（放到二级就是「键盘输入」菜单项）",
        "icon": "keyboard",
        "dock_group": "control",
        "levels": [1, 2],
        "level": 1,
    },
    {
        "id": ALAS_ANCHOR,
        "group": "dock",
        "label": "ALAS 入口",
        "hint": "位置锚点，显隐由「ALAS 管理 → 工作台显示」控制",
        "icon": "bot",
        "dock_group": "alas",
        "levels": [1],
        "level": 1,
        "anchor": True,
    },
    {
        "id": "nav",
        "group": "dock",
        "label": "导航键",
        "hint": "返回 / 主页 / 多任务三个按键（放到二级会变成三个菜单项）",
        "icon": "arrow-left",
        "dock_group": "nav",
        "levels": [1, 2],
        "level": 1,
        "parts": ["返回", "主页", "多任务"],
    },
    {
        "id": "fullscreen",
        "group": "dock",
        "label": "全屏显示",
        "hint": "进入全屏并自动横竖屏（按钮上带「窗口 / 全屏」小字）",
        "icon": "maximize",
        "dock_group": "view",
        "levels": [1, 2],
        "level": 1,
        "small_sample": "窗口",
    },
    {
        "id": "shot",
        "group": "dock",
        "label": "截图",
        "hint": "截取当前画面（二级菜单里带「复制」小字）",
        "icon": "camera",
        "dock_group": "more",
        "levels": [1, 2],
        "level": 2,
        "small_sample": "复制",
    },
    {
        "id": "alt_keyboard",
        "group": "dock",
        "label": "备用键盘",
        "hint": "键盘失灵时的补救入口（放到一级就是一个普通按钮）",
        "icon": "keyboard",
        "dock_group": "more",
        "levels": [1, 2],
        "level": 2,
        "small_sample": "备用",
    },
    {
        "id": "more",
        "group": "dock",
        "label": "更多菜单",
        "hint": "二级菜单的容器；二级里有功能时才会出现",
        "icon": "ellipsis",
        "dock_group": "more",
        "levels": [1],
        "level": 1,
        "container": True,
    },
)

# 投屏行为：不参与底部菜单编排，只有开关（放哪一级/顺序都无意义，所以没有 levels）。
WORKBENCH_DISPLAY_FEATURES: tuple[dict[str, object], ...] = (
    {
        "id": "fullscreen_dock_lift",
        "group": "display",
        "label": "全屏打开控制栏时画面上移",
        "icon": "move-vertical",
        "hint": "全屏下展开底部控制栏时画面自动上移一小段（用顶部本就空着的留白换出底部空间），收起后归位",
    },
)

WORKBENCH_FEATURES: tuple[dict[str, object], ...] = (
    WORKBENCH_TOP_STATUS_FEATURES + WORKBENCH_DOCK_FEATURES + WORKBENCH_DISPLAY_FEATURES
)

FEATURE_IDS: tuple[str, ...] = tuple(str(item["id"]) for item in WORKBENCH_FEATURES)
# 开关只覆盖真实功能（锚点没有开关）。
SWITCH_FEATURE_IDS: tuple[str, ...] = tuple(
    str(item["id"]) for item in WORKBENCH_FEATURES if not item.get("anchor")
)
DOCK_FEATURE_IDS: tuple[str, ...] = tuple(
    str(item["id"]) for item in WORKBENCH_DOCK_FEATURES
)

_TRUTHY = ("1", "true", "yes", "on")


def feature_id_set() -> set[str]:
    return set(FEATURE_IDS)


def switch_feature_id_set() -> set[str]:
    return set(SWITCH_FEATURE_IDS)


def feature_catalog(*, dock_only: bool = False) -> tuple[dict[str, object], ...]:
    return WORKBENCH_DOCK_FEATURES if dock_only else WORKBENCH_FEATURES


def feature_levels(feature_id: str) -> list[int]:
    for item in WORKBENCH_DOCK_FEATURES:
        if str(item["id"]) == feature_id:
            return [int(level) for level in item.get("levels") or (1,)]
    return []


def _coerce_flag(value: object, default: bool = True) -> bool:
    """把存储值转成布尔；无法识别时回落到默认值（启用）。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUTHY:
            return True
        if text in ("0", "false", "no", "off", ""):
            return False
    return default


def default_switches() -> dict[str, dict[str, bool]]:
    """全部启用 —— 也是「恢复默认」的目标值。"""
    return {
        role: {feature_id: True for feature_id in SWITCH_FEATURE_IDS}
        for role in WORKBENCH_ROLES
    }


def normalize_switches(raw: object, *, strict: bool = False) -> dict[str, dict[str, bool]]:
    """规范化开关快照；未知键丢弃、缺失键补默认值、脏数据退化为启用。"""
    switches = default_switches()
    if raw is None:
        return switches
    if not isinstance(raw, dict):
        if strict:
            raise ValueError("workbench_features_invalid")
        return switches
    known = switch_feature_id_set()
    for role, values in raw.items():
        role_key = str(role or "").strip()
        if role_key not in switches:
            if strict:
                raise ValueError("workbench_feature_role_unknown")
            continue
        if not isinstance(values, dict):
            if strict:
                raise ValueError("workbench_features_invalid")
            continue
        for feature_id, value in values.items():
            feature_key = str(feature_id or "").strip()
            if feature_key not in known:
                if strict:
                    raise ValueError("workbench_feature_unknown")
                continue
            switches[role_key][feature_key] = _coerce_flag(value)
    return switches


def switches_from(stored: object) -> dict[str, dict[str, bool]]:
    if isinstance(stored, (dict, list)):
        return normalize_switches(stored)
    text = str(stored or "").strip()
    if not text:
        return default_switches()
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return default_switches()
    return normalize_switches(parsed)


def switches_for_role(stored: object, role: object) -> dict[str, bool]:
    snapshot = switches_from(stored)
    role_key = str(role or "").strip()
    if role_key not in snapshot:
        role_key = "user"
    return dict(snapshot[role_key])


def default_layout() -> dict[str, dict[str, list[str]]]:
    """默认排布 = 目录里声明的默认层级，顺序即目录顺序。"""
    layout: dict[str, dict[str, list[str]]] = {}
    for role in WORKBENCH_ROLES:
        level1: list[str] = []
        level2: list[str] = []
        for item in WORKBENCH_DOCK_FEATURES:
            feature_id = str(item["id"])
            target = level2 if int(item.get("level") or 1) == 2 else level1
            target.append(feature_id)
        layout[role] = {"level1": level1, "level2": level2}
    return layout


def _normalize_level(raw: object, *, strict: bool = False) -> list[str]:
    """把一个层级的列表规范化：去重、丢弃未知/不允许该层级的功能、补回锚点。"""
    order: list[str] = []
    if isinstance(raw, (list, tuple)):
        for value in raw:
            feature_id = str(value or "").strip()
            if not feature_id or feature_id in order:
                continue
            if feature_id == ALAS_ANCHOR:
                if 1 not in feature_levels(ALAS_ANCHOR):
                    continue
                order.append(feature_id)
                continue
            if feature_id not in DOCK_FEATURE_IDS:
                if strict:
                    raise ValueError("workbench_layout_feature_unknown")
                continue
            if 1 not in feature_levels(feature_id):
                if strict:
                    raise ValueError("workbench_layout_level_invalid")
                continue
            order.append(feature_id)
    return order


def _normalize_level2(raw: object, *, strict: bool = False) -> list[str]:
    order: list[str] = []
    if isinstance(raw, (list, tuple)):
        for value in raw:
            feature_id = str(value or "").strip()
            if not feature_id or feature_id in order:
                continue
            if feature_id == ALAS_ANCHOR:
                if strict:
                    raise ValueError("workbench_layout_level_invalid")
                continue
            if feature_id not in DOCK_FEATURE_IDS:
                if strict:
                    raise ValueError("workbench_layout_feature_unknown")
                continue
            if 2 not in feature_levels(feature_id):
                if strict:
                    raise ValueError("workbench_layout_level_invalid")
                continue
            order.append(feature_id)
    return order


def normalize_layout(raw: object, *, strict: bool = False) -> dict[str, dict[str, list[str]]]:
    """规范化菜单编排。

    提交了某个角色 = 以它提交的两级列表为准（**不补回**缺失功能：没放进任何一级
    就是「未启用」，这正是拖到左侧未启用区的语义）；未提交的角色保持默认排布。
    锚点 ``@alas`` 必须恰好出现在一级一次（它没有开关，显隐由 ALAS 页控制）。
    未知功能 id（例如已撤掉的 ``rotate``，见 ISSUE-156）会被静默丢弃。
    """
    layout = default_layout()
    if raw is None:
        return layout
    if not isinstance(raw, dict):
        if strict:
            raise ValueError("workbench_layout_invalid")
        return layout
    for role in WORKBENCH_ROLES:
        if role not in raw:
            continue
        section = raw.get(role)
        if not isinstance(section, dict):
            if strict:
                raise ValueError("workbench_layout_invalid")
            continue
        level1 = _normalize_level(section.get("level1"), strict=strict)
        level2 = _normalize_level2(section.get("level2"), strict=strict)
        # 只允许出现在一个层级：一级优先（前端从一级拖到二级时会先移除）。
        level2 = [item for item in level2 if item not in level1]
        if ALAS_ANCHOR not in level1:
            default_index = default_layout()[role]["level1"].index(ALAS_ANCHOR)
            level1.insert(min(len(level1), default_index), ALAS_ANCHOR)
        layout[role] = {"level1": level1, "level2": level2}
    return layout


def layout_from(stored: object) -> dict[str, dict[str, list[str]]]:
    if isinstance(stored, (dict, list)):
        return normalize_layout(stored)
    text = str(stored or "").strip()
    if not text:
        return default_layout()
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return default_layout()
    return normalize_layout(parsed)


def layout_for_role(stored: object, role: object) -> dict[str, list[str]]:
    layout = layout_from(stored)
    role_key = str(role or "").strip()
    if role_key not in layout:
        role_key = "user"
    return {"level1": list(layout[role_key]["level1"]), "level2": list(layout[role_key]["level2"])}


def serialize_switches(switches: dict[str, dict[str, bool]]) -> str:
    return json.dumps(switches, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def serialize_layout(layout: dict[str, dict[str, list[str]]]) -> str:
    return json.dumps(layout, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def catalog_payload() -> list[dict[str, object]]:
    """后台页面用的分组目录（含分组内功能项，保留图标/层级/预览片段）。"""
    groups: list[dict[str, object]] = []
    for group in WORKBENCH_FEATURE_GROUPS:
        items = [dict(item) for item in WORKBENCH_FEATURES if item["group"] == group["id"]]
        groups.append({**group, "items": items})
    return groups


def snapshot_payload(stored_switches: object, stored_layout: object = None) -> dict[str, object]:
    """后台读取接口的响应体。"""
    return {
        "ok": True,
        "features": switches_from(stored_switches),
        "layout": layout_from(stored_layout),
        "defaults": default_switches(),
        "default_layout": default_layout(),
        "roles": [{"id": role, "label": ROLE_LABELS[role]} for role in WORKBENCH_ROLES],
        "groups": catalog_payload(),
        "setting_key": SETTING_KEY,
        "layout_key": LAYOUT_KEY,
        "alas_anchor": ALAS_ANCHOR,
    }


__all__ = [
    "ALAS_ANCHOR",
    "DOCK_FEATURE_IDS",
    "FEATURE_IDS",
    "LAYOUT_KEY",
    "ROLE_LABELS",
    "SETTING_KEY",
    "SWITCH_FEATURE_IDS",
    "WORKBENCH_DISPLAY_FEATURES",
    "WORKBENCH_DOCK_FEATURES",
    "WORKBENCH_FEATURES",
    "WORKBENCH_FEATURE_GROUPS",
    "WORKBENCH_ROLES",
    "WORKBENCH_TOP_STATUS_FEATURES",
    "catalog_payload",
    "default_layout",
    "default_switches",
    "feature_catalog",
    "feature_id_set",
    "feature_levels",
    "layout_for_role",
    "layout_from",
    "normalize_layout",
    "normalize_switches",
    "serialize_layout",
    "serialize_switches",
    "snapshot_payload",
    "switch_feature_id_set",
    "switches_for_role",
    "switches_from",
]
