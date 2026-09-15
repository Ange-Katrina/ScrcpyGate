"""Static compatibility exports for the split administrator routers.

The application registers the six focused routers directly.  This module is
kept for older integrations and tests that still import ``app.routers.admin``;
it contains no route implementations and is never registered by the factory.
"""

from __future__ import annotations

from fastapi import APIRouter

from .. import alas, i18n, security, storage
from ..account_access import account_connections
from ..adb_monitor import adb_monitor
from ..devices import devices_payload
from ..http_helpers import (
    parse_body,
    parse_bool,
    server_error_message,
    user_payload,
    video_option_error_message,
)
from ..logging_config import logging_health
from ..mirror_manager import manager
from ..mirror_runtime import acquire_control_lock
from ..runtime import runtime_for
from ..services.alas_service import (
    _admin_alas_call,
    admin_alas_overview,
    admin_alas_payload,
    admin_alas_permissions_payload,
    admin_overview_storage_payload,
    bound_alas_config_names,
    clear_alas_status_cache,
)
from ..services.audit_service import (
    AUDIT_EXPORT_MAX_ROWS,
    SQLITE_INT_MAX,
    audit_csv_cell as _audit_csv_cell,
    audit_filter_int as _audit_filter_int,
    audit_filters as _audit_filters,
    audit_request,
    audit_websocket_event,
    await_audit_read_barrier as _await_audit_read_barrier,
    collect_audit_export as _collect_audit_export,
)
from ..services.mirror_service import resolve_device_or_404
from ..workers import ALAS_OVERVIEW_MAX_WORKERS
from .admin_access import (
    admin_adb_status,
    admin_delete_device,
    admin_delete_user,
    admin_devices,
    admin_disconnect_viewer,
    admin_permissions,
    admin_reconnect_adb_device,
    admin_replace_user_permissions,
    admin_sessions,
    admin_set_permission,
    admin_test_adb_device,
    admin_transfer_control,
    admin_upsert_device,
    admin_upsert_user,
    admin_users,
    router as access_router,
)
from .admin_alas import (
    admin_alas,
    admin_alas_check,
    admin_alas_config,
    admin_alas_configs,
    admin_alas_features,
    admin_alas_permissions,
    admin_alas_visibility,
    admin_save_alas,
    admin_save_alas_config,
    admin_save_alas_features,
    admin_save_alas_visibility,
    admin_set_alas_permission,
    admin_toggle_alas,
    router as alas_router,
)
from .admin_audit import (
    admin_alerts,
    admin_audit_event,
    admin_export_logs,
    admin_logs,
    admin_resolve_alert,
    admin_runtime_logs,
    admin_verify_audit_integrity,
    router as audit_router,
)
from .admin_overview import (
    _DASHBOARD_ACTION_LABELS,
    _dashboard_activity_projection,
    admin_dashboard_snapshot,
    admin_overview,
    admin_overview_alas,
    router as overview_router,
)
from .admin_settings import (
    _UI_SETTING_FIELDS,
    _normalize_ui_setting_updates,
    admin_login_guard,
    admin_login_guard_unlock,
    admin_save_login_guard,
    admin_ui_settings,
    admin_ui_settings_export,
    admin_ui_settings_import,
    admin_ui_settings_maintenance,
    admin_ui_settings_reset,
    admin_ui_settings_restart,
    admin_ui_settings_payload,
    admin_save_ui_settings,
    router as settings_router,
)
from .admin_video import (
    _admin_video_preset_payload,
    _custom_preset_values,
    _new_custom_preset_id,
    admin_save_video_settings,
    admin_video_preset_create,
    admin_video_preset_delete,
    admin_video_preset_reset,
    admin_video_preset_update,
    admin_video_presets,
    admin_video_quality_payload,
    admin_video_restart,
    admin_video_runtime_payload,
    admin_video_runtime_status,
    admin_video_settings,
    router as video_router,
)
from .admin_workbench import (
    admin_reset_workbench,
    admin_save_workbench,
    admin_workbench,
    router as workbench_router,
)

AUDIT_EXPORT_MAX_SECONDS = 31 * 24 * 60 * 60
ADMIN_ALAS_OVERVIEW_CONCURRENCY = ALAS_OVERVIEW_MAX_WORKERS


# Compatibility aggregate only.  The application factory does not register
# this object; each focused router is registered exactly once by application.py.
all_router = APIRouter()
for _group_router in (
    overview_router,
    access_router,
    video_router,
    alas_router,
    settings_router,
    workbench_router,
    audit_router,
):
    all_router.include_router(_group_router)
router = all_router


__all__ = [
    "ADMIN_ALAS_OVERVIEW_CONCURRENCY",
    "AUDIT_EXPORT_MAX_ROWS",
    "AUDIT_EXPORT_MAX_SECONDS",
    "SQLITE_INT_MAX",
    "_DASHBOARD_ACTION_LABELS",
    "_UI_SETTING_FIELDS",
    "_admin_alas_call",
    "_admin_video_preset_payload",
    "_audit_csv_cell",
    "_audit_filter_int",
    "_audit_filters",
    "_await_audit_read_barrier",
    "_collect_audit_export",
    "_custom_preset_values",
    "_dashboard_activity_projection",
    "_new_custom_preset_id",
    "_normalize_ui_setting_updates",
    "access_router",
    "account_connections",
    "acquire_control_lock",
    "admin_adb_status",
    "admin_alas",
    "admin_alas_check",
    "admin_alas_config",
    "admin_alas_configs",
    "admin_alas_features",
    "admin_alas_permissions",
    "admin_alas_visibility",
    "admin_dashboard_snapshot",
    "admin_delete_device",
    "admin_delete_user",
    "admin_disconnect_viewer",
    "admin_devices",
    "admin_export_logs",
    "admin_login_guard",
    "admin_login_guard_unlock",
    "admin_overview",
    "admin_overview_alas",
    "admin_permissions",
    "admin_reconnect_adb_device",
    "admin_replace_user_permissions",
    "admin_reset_workbench",
    "admin_resolve_alert",
    "admin_save_alas",
    "admin_save_alas_config",
    "admin_save_alas_features",
    "admin_save_alas_visibility",
    "admin_save_login_guard",
    "admin_save_ui_settings",
    "admin_save_video_settings",
    "admin_save_workbench",
    "admin_set_alas_permission",
    "admin_set_permission",
    "admin_test_adb_device",
    "admin_toggle_alas",
    "admin_transfer_control",
    "admin_ui_settings",
    "admin_ui_settings_export",
    "admin_ui_settings_import",
    "admin_ui_settings_maintenance",
    "admin_ui_settings_payload",
    "admin_ui_settings_reset",
    "admin_ui_settings_restart",
    "admin_upsert_device",
    "admin_upsert_user",
    "admin_users",
    "admin_video_preset_create",
    "admin_video_preset_delete",
    "admin_video_preset_reset",
    "admin_video_preset_update",
    "admin_video_presets",
    "admin_video_quality_payload",
    "admin_video_restart",
    "admin_video_runtime_payload",
    "admin_video_runtime_status",
    "admin_video_settings",
    "admin_workbench",
    "admin_verify_audit_integrity",
    "admin_audit_event",
    "admin_alerts",
    "admin_logs",
    "admin_runtime_logs",
    "admin_sessions",
    "alas_router",
    "audit_request",
    "audit_router",
    "bound_alas_config_names",
    "clear_alas_status_cache",
    "devices_payload",
    "i18n",
    "logging_health",
    "manager",
    "overview_router",
    "parse_body",
    "parse_bool",
    "resolve_device_or_404",
    "router",
    "runtime_for",
    "security",
    "server_error_message",
    "settings_router",
    "storage",
    "user_payload",
    "video_option_error_message",
    "video_router",
    "workbench_router",
    "alas",
    "adb_monitor",
    "admin_alas_overview",
    "admin_alas_payload",
    "admin_alas_permissions_payload",
    "admin_overview_storage_payload",
    "audit_websocket_event",
]
