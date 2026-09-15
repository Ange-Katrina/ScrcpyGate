"""投屏管理：工作台功能开关与底部菜单编排（一级/二级）的管理员接口。"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request

from .. import i18n, security, storage, workbench_features
from ..http_helpers import parse_body
from ..services.audit_service import audit_request

router = APIRouter()


def _stored(key: str) -> str:
    return str(storage.get_setting(key, "") or "")


def _current_switches() -> dict[str, dict[str, bool]]:
    return workbench_features.switches_from(_stored(workbench_features.SETTING_KEY))


def _current_layout() -> dict[str, dict[str, list[str]]]:
    return workbench_features.layout_from(_stored(workbench_features.LAYOUT_KEY))


def _payload() -> dict[str, object]:
    return workbench_features.snapshot_payload(
        _stored(workbench_features.SETTING_KEY),
        _stored(workbench_features.LAYOUT_KEY),
    )


def _apply_layout_to_switches(
    switches: dict[str, dict[str, bool]],
    layout: dict[str, dict[str, list[str]]],
) -> dict[str, dict[str, bool]]:
    """编排即开关：放进某一级的功能视为启用，两块都没放视为停用。

    这样「拖到未启用区」不必再单独点开关，前端也就不会出现
    「开关开着但哪一级都没放」的隐形状态。
    """
    for role in workbench_features.WORKBENCH_ROLES:
        placed = set(layout[role]["level1"]) | set(layout[role]["level2"])
        for feature_id in workbench_features.DOCK_FEATURE_IDS:
            if feature_id == workbench_features.ALAS_ANCHOR:
                continue
            switches[role][feature_id] = feature_id in placed
    return switches


@router.get("/api/admin/workbench")
async def admin_workbench(request: Request):
    """读取两个角色的开关、菜单编排与功能目录。"""
    security.require_admin(request)
    await asyncio.to_thread(storage.get_setting, workbench_features.SETTING_KEY, "")
    return await asyncio.to_thread(_payload)


@router.put("/api/admin/workbench")
async def admin_save_workbench(request: Request):
    """保存开关与菜单编排；未提交的功能项保持原值。"""
    security.verify_csrf(request)
    admin = security.require_admin(request)
    payload = await parse_body(request)
    raw_features = payload.get("features")
    if raw_features is None and any(
        role in payload for role in workbench_features.WORKBENCH_ROLES
    ):
        # 兼容旧写法：{admin: {...}, user: {...}} 直接放在顶层。
        raw_features = {
            role: payload[role]
            for role in workbench_features.WORKBENCH_ROLES
            if role in payload
        }
    raw_layout = payload.get("layout")
    if raw_features is None and raw_layout is None:
        raise HTTPException(
            status_code=400,
            detail=i18n.translate("server.error.invalid_setting_value"),
        )
    submitted: dict[str, dict[str, bool]] = {}
    if raw_features is not None:
        if not isinstance(raw_features, dict) or not raw_features:
            raise HTTPException(
                status_code=400,
                detail=i18n.translate("server.error.invalid_setting_value"),
            )
        try:
            submitted = workbench_features.normalize_switches(raw_features, strict=True)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=i18n.translate("server.error.invalid_setting_value"),
            ) from exc
    layout: dict[str, dict[str, list[str]]] | None = None
    if raw_layout is not None:
        try:
            layout = await asyncio.to_thread(
                workbench_features.normalize_layout, raw_layout, strict=True
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=i18n.translate("server.error.invalid_setting_value"),
            ) from exc

    current_switches = await asyncio.to_thread(_current_switches)
    current_layout = await asyncio.to_thread(_current_layout)
    features = {
        role: {**current_switches[role], **(submitted.get(role) or {})}
        for role in workbench_features.WORKBENCH_ROLES
    }
    if layout is None:
        layout = current_layout
    else:
        features = _apply_layout_to_switches(features, layout)

    await asyncio.to_thread(
        storage.set_settings,
        {
            workbench_features.SETTING_KEY: workbench_features.serialize_switches(features),
            workbench_features.LAYOUT_KEY: workbench_features.serialize_layout(layout),
        },
    )
    audit_request(
        request,
        admin,
        "workbench_features_update",
        target_type="system_settings",
        target_id=workbench_features.SETTING_KEY,
        metadata={
            "changed_roles": sorted(
                role
                for role in workbench_features.WORKBENCH_ROLES
                if features[role] != current_switches[role]
                or layout[role] != current_layout[role]
            ),
            "disabled": sorted(
                f"{role}.{feature_id}"
                for role in workbench_features.WORKBENCH_ROLES
                for feature_id, enabled in features[role].items()
                if not enabled
            ),
            "level2": {
                role: list(layout[role]["level2"])
                for role in workbench_features.WORKBENCH_ROLES
            },
        },
    )
    return workbench_features.snapshot_payload(
        workbench_features.serialize_switches(features),
        workbench_features.serialize_layout(layout),
    )


@router.post("/api/admin/workbench/reset")
async def admin_reset_workbench(request: Request):
    """恢复默认：开关全部启用，菜单编排回到出厂排布。"""
    security.verify_csrf(request)
    admin = security.require_admin(request)
    switches = workbench_features.default_switches()
    layout = workbench_features.default_layout()
    await asyncio.to_thread(
        storage.set_settings,
        {
            workbench_features.SETTING_KEY: workbench_features.serialize_switches(switches),
            workbench_features.LAYOUT_KEY: workbench_features.serialize_layout(layout),
        },
    )
    audit_request(
        request,
        admin,
        "workbench_features_reset",
        target_type="system_settings",
        target_id=workbench_features.SETTING_KEY,
    )
    return workbench_features.snapshot_payload(
        workbench_features.serialize_switches(switches),
        workbench_features.serialize_layout(layout),
    )


__all__ = [name for name in globals() if name.startswith("admin_")] + ["router"]
