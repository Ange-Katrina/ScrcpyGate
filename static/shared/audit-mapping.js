/* ScrcpyGate dashboard presentation helpers.
 * The dashboard page owns data loading and interaction; this file owns only
 * localized labels and safe display formatting for alerts and recent activity.
 */
(function (global) {
  'use strict';

  var ACTION_TITLES = {
    geo_database_import: '手动上传地区库',
    geo_database_delete: '删除地区库',
    login: '登录', login_success: '登录成功', login_failed: '登录失败', login_rate_limited: '登录请求被限流', logout: '退出登录',
    user_created: '创建用户', user_updated: '更新用户', user_deleted: '删除用户', user_upsert: '保存用户', user_create: '创建用户', user_update: '更新用户', user_delete: '删除用户',
    password_changed: '修改密码', password_change: '修改密码', permission_set: '更新设备权限', permission_granted: '授予设备权限', permission_revoked: '撤销设备权限', permission_updated: '更新设备权限',
    permission_matrix_replace: '保存用户权限矩阵', device_upsert: '保存设备配置',
    device_created: '创建设备', device_updated: '更新设备', device_deleted: '删除设备', device_delete: '删除设备', device_create: '创建设备', device_update: '更新设备', device_adb_test: '检测 ADB 连接', adb_test: '检测 ADB 连接',
    device_adb_reconnect: '重连 ADB 设备', adb_reconnect: '重连 ADB 设备', mirror_start: '开始投屏', mirror_stop: '停止投屏', mirror_auto_stop: '自动停止投屏', mirror_idle_stop: '空闲自动停止投屏',
    mirror_viewer_stop: '停止观看端', mirror_viewer_disconnect: '断开观看端',
    mirror_settings: '更新投屏画质', control_acquire: '获取控制权',
    control_release: '释放控制权', control_transfer: '转交控制权', control_takeover: '接管控制权',
    video_preferences: '更新画质偏好', video_preference_save: '保存画质偏好', video_settings: '保存画质与传输设置',
    video_restart: '重启视频传输', video_preset_create: '创建画质预设', video_preset_update: '更新画质预设', video_preset_delete: '删除画质预设', video_preset_reset: '重置画质预设', alas_binding_set: '绑定 ALAS 配置',
    alas_binding_delete: '解除 ALAS 绑定', alas_toggle: '切换 ALAS 运行状态',
    alas_connection_check: '检测 ALAS 连接', alas_config_save: '保存 ALAS 配置',
    alas_embed_open: '打开 ALAS 页面', alas_embed_close: '关闭 ALAS 页面',
    alas_embed_error: 'ALAS 页面异常', alas_embed_denied: '拒绝访问 ALAS 页面', alas_embed_proxy_failed: 'ALAS 页面代理失败', alas_embed_ws_denied: '拒绝 ALAS WebSocket', alas_embed_ws_failed: 'ALAS WebSocket 失败', alas_settings: 'ALAS 设置', alas_access: '访问 ALAS', alas_admin_toggle: '切换 ALAS 服务',
    alas_binding: '绑定 ALAS 配置', audit_export: '导出审计日志', audit_integrity_check: '检查日志完整性', integrity_check: '检查日志完整性', alert_resolve: '处理告警',
    ui_settings_update: '更新界面设置', ui_settings_import: '导入界面设置', ui_settings_reset: '重置界面设置', ui_settings_maintenance: '维护界面设置', admin_access: '访问管理后台', authentication: '认证访问', account_expired: '账户到期访问', page_index: '进入投屏工作台', page_admin: '进入管理后台', mirror_switch_cleanup: '清理切换投屏', audit: '审计操作', csrf_validation: '安全校验', http_boundary: 'HTTP 边界校验', http_operation: 'HTTP 请求失败', http_request: 'HTTP 请求', websocket_access: 'WebSocket 访问', connect: '建立连接', authorization: '授权校验', downstream: '下游消息', message: '消息处理', exception: '发生异常'
  };
  var ACTION_TITLES_EN = {
    geo_database_import: 'Upload geolocation database',
    geo_database_delete: 'Delete geolocation database',
    login: 'Sign in', login_success: 'Sign in', login_failed: 'Sign in failed', login_rate_limited: 'Sign-in rate limited', logout: 'Sign out',
    user_created: 'Create user', user_updated: 'Update user', user_deleted: 'Delete user', user_upsert: 'Save user', user_create: 'Create user', user_update: 'Update user', user_delete: 'Delete user',
    password_changed: 'Change password', password_change: 'Change password', permission_set: 'Update device access', permission_granted: 'Grant device access', permission_revoked: 'Revoke device access', permission_updated: 'Update device access',
    permission_matrix_replace: 'Save user permissions', device_upsert: 'Save device settings',
    device_created: 'Create device', device_updated: 'Update device', device_deleted: 'Delete device', device_delete: 'Delete device', device_create: 'Create device', device_update: 'Update device', device_adb_test: 'Check ADB connection', adb_test: 'Check ADB connection',
    device_adb_reconnect: 'Reconnect ADB device', adb_reconnect: 'Reconnect ADB device', mirror_start: 'Start casting', mirror_stop: 'Stop casting', mirror_auto_stop: 'Auto-stop casting', mirror_idle_stop: 'Auto-stop idle cast',
    mirror_viewer_stop: 'Stop viewer', mirror_viewer_disconnect: 'Disconnect viewer',
    mirror_settings: 'Update cast quality', control_acquire: 'Take control',
    control_release: 'Release control', control_transfer: 'Transfer control', control_takeover: 'Take over control',
    video_preferences: 'Update quality preference', video_preference_save: 'Save quality preference', video_settings: 'Save video settings',
    video_restart: 'Restart video transport', video_preset_create: 'Create quality preset', video_preset_update: 'Update quality preset', video_preset_delete: 'Delete quality preset', video_preset_reset: 'Reset quality preset', alas_binding_set: 'Bind ALAS config',
    alas_binding_delete: 'Unbind ALAS config', alas_toggle: 'Change ALAS state',
    alas_connection_check: 'Check ALAS connection', alas_config_save: 'Save ALAS config',
    alas_embed_open: 'Open ALAS page', alas_embed_close: 'Close ALAS page',
    alas_embed_error: 'ALAS page error', alas_embed_denied: 'Deny ALAS page', alas_embed_proxy_failed: 'ALAS proxy failed', alas_embed_ws_denied: 'Deny ALAS WebSocket', alas_embed_ws_failed: 'ALAS WebSocket failed', alas_settings: 'ALAS settings', alas_access: 'Open ALAS', alas_admin_toggle: 'Change ALAS service state',
    alas_binding: 'Bind ALAS config', audit_export: 'Export audit log', audit_integrity_check: 'Check log integrity', integrity_check: 'Check log integrity', alert_resolve: 'Resolve alert',
    ui_settings_update: 'Update UI settings', ui_settings_import: 'Import UI settings', ui_settings_reset: 'Reset UI settings', ui_settings_maintenance: 'Maintain UI settings', admin_access: 'Open admin panel', authentication: 'Authentication access', account_expired: 'Expired account access', page_index: 'Open casting workspace', page_admin: 'Open admin panel', mirror_switch_cleanup: 'Clean up switched cast', audit: 'Audit action', csrf_validation: 'Security validation', http_boundary: 'HTTP boundary check', http_operation: 'HTTP request failed', http_request: 'HTTP request', websocket_access: 'WebSocket access', connect: 'Establish connection', authorization: 'Authorization check', downstream: 'Downstream message', message: 'Message handling', exception: 'Exception'
  };
  // Runtime events are also shown in the dashboard. Keep the producer's
  // machine code out of the visible title while retaining it on the record.
  Object.assign(ACTION_TITLES, {
    viewer_watch_start: '观看连接建立', viewer_watch_end: '观看连接结束',
    client_join: '观看端加入', disconnect: '连接断开', error: '发生错误',
    player_reset: '重置播放器', control_player_reset: '重置控制播放器',
    process_restart: '重启服务进程', quality_changed: '画质已切换',
    stream_reset: '重置视频流', video_client_drop: '观看端丢帧',
    video_client_terminate: '终止观看端'
  });
  Object.assign(ACTION_TITLES_EN, {
    viewer_watch_start: 'Viewer connected', viewer_watch_end: 'Viewer disconnected',
    client_join: 'Viewer joined', disconnect: 'Connection closed', error: 'Error occurred',
    player_reset: 'Reset player', control_player_reset: 'Reset control player',
    process_restart: 'Restart service process', quality_changed: 'Quality changed',
    stream_reset: 'Reset video stream', video_client_drop: 'Viewer dropped frames',
    video_client_terminate: 'Terminate viewer'
  });
  var REASON_LABELS = {
    invalid_credentials: '凭据无效', account_disabled: '账户已停用', account_expired: '账户已到期',
    permission_denied: '权限不足', forbidden: '访问被拒绝', device_offline: '设备离线',
    device_unavailable: '设备不可用', upstream_exception: '上游服务异常', timeout: '请求超时',
    upstream_connection_closed: 'ALAS 上游连接异常关闭', upstream_handshake_failed: 'ALAS WebSocket 握手失败',
    upstream_tls_failed: 'ALAS 上游 TLS 校验失败', upstream_dns_failed: 'ALAS 上游域名解析失败',
    upstream_connection_refused: 'ALAS 上游拒绝连接', upstream_connect_timeout: '连接 ALAS 上游超时',
    upstream_timeout: 'ALAS 上游通信超时', upstream_network_error: 'ALAS 上游网络异常', proxy_internal_error: 'ALAS 代理内部异常',
    request_timeout: '请求超时', filtered: '内容被策略过滤', origin_forbidden: '来源不被允许',
    csrf_failed: '安全校验失败', policy_denied: '策略拒绝', payload_too_large: '内容超过大小限制', payload_too_complex: '内容结构过于复杂',
    edit_permission_denied: '编辑权限不足', run_permission_denied: '运行权限不足', management_path_denied: '管理路径不可访问', management_denied: '管理操作被拒绝', restricted_entry_denied: '受限入口不可访问', restricted_user_entry_denied: '普通用户入口不可访问', alas_settings_denied: 'ALAS 设置不可访问',
    invalid_json: 'JSON 内容无效', invalid_binary: '二进制内容无效', invalid_body: '请求内容无效', invalid_request: '请求无效', missing_request_config: '缺少请求配置', config_mismatch: '配置不匹配', config_path_mismatch: '配置路径不匹配',
    missing_binding: '缺少配置绑定', unconfigured: '未配置', disabled: '已停用', expired: '已到期', account_locked: '账户已锁定',
    runtime_operation_failed: '运行时操作失败', stream_start_failed: '投屏启动失败', stream_restart_failed: '投屏重启失败', stop_failed: '停止失败', control_occupied: '控制权已被占用', not_lock_owner: '不是控制权持有者',
    device_not_found: '设备不存在', device_disabled: '设备已停用', device_permission_denied: '设备权限不足', authentication_required: '需要登录', login_required: '需要登录', admin_required: '需要管理员权限', password_change_required: '需要先修改密码', token_invalid: '令牌无效', host_rejected: '主机不被允许', origin_denied: '来源不被允许', origin_rejected: '来源不被允许', untrusted_proxy_headers: '代理来源未被信任', rate_limited: '请求过于频繁', adb_unavailable: 'ADB 不可用', adb_failed: 'ADB 操作失败', missing_device_context: '缺少设备上下文', response_too_large: '响应内容过大', encoded_response_unfilterable: '响应无法安全过滤', connection_failed: '连接失败', not_found: '资源不存在', internal_error: '服务内部错误', http_status: '请求返回 HTTP 错误', unhandled_exception: '服务处理异常', upstream_unreachable: '上游服务不可达', state_missing: '缺少完整性状态', chain_link_mismatch: '审计链连接不一致', event_hash_mismatch: '审计事件校验失败', chain_head_mismatch: '审计链头校验失败', protocol_state_required: '协议状态不完整', restricted_input_field: '包含受限输入字段', missing_task_id: '缺少任务标识', denied_task_id: '任务标识被拒绝', unknown_task_id: '任务标识未知', task_kind_mismatch: '任务类型不匹配', unknown_input_field: '包含未知输入字段', missing_event: '缺少事件类型', unknown_protocol_event: '协议事件未知', denied_callback_id: '回调标识被拒绝', unknown_callback_id: '回调标识未知', restricted_callback: '回调属于受限操作', invalid_event_data: '事件数据无效', invalid_input_event: '输入事件无效', invalid_downstream: '上游响应格式无效', update_notice_dropped: '更新入口已被过滤', other_instance_dropped: '其他实例内容已被过滤', restricted_entry_dropped: '受限入口内容已被过滤', alas_settings_dropped: 'ALAS 设置内容已被过滤', config_mismatch_dropped: '其他配置内容已被过滤', downstream_dropped: '不安全内容已被过滤',
    binding_revoked: '绑定已撤销', invalid_target: '目标无效', session_revoked: '会话已撤销', permission_revoked: '权限已撤销', too_many_video_connections: '视频连接数过多', stream_ended: '视频流已结束', stream_terminated: '视频流已终止', video_stream_ended: '视频流已结束', connection_closed: '连接已关闭', client_disconnect: '客户端已断开', cancelled: '操作已取消', control_error: '控制操作失败', open_timeout: '打开页面超时', adb_unreachable: 'ADB 不可达', device_unreachable: '设备不可达', account_status: '账户状态异常', alas_embed_ws_failed: 'ALAS WebSocket 失败', http_error: 'HTTP 请求失败', token_error: '令牌处理失败', dispatcher_unavailable: '审计服务不可用', migration_failed: '迁移失败', parse_failed: '解析失败', quality_change_failed: '画质切换失败', viewer_reservation_expired: '观看预约已过期', verification_failed: '完整性校验失败', catalog_error: '配置目录读取失败', saved_preference_unavailable: '保存的画质偏好不可用', default_unavailable: '默认配置不可用'
  };
  var REASON_LABELS_EN = {
    invalid_credentials: 'Invalid credentials', account_disabled: 'Account disabled', account_expired: 'Account expired',
    permission_denied: 'Insufficient permission', forbidden: 'Access denied', device_offline: 'Device offline',
    device_unavailable: 'Device unavailable', upstream_exception: 'Upstream service error', timeout: 'Request timed out',
    upstream_connection_closed: 'ALAS upstream connection closed unexpectedly', upstream_handshake_failed: 'ALAS WebSocket handshake failed',
    upstream_tls_failed: 'ALAS upstream TLS verification failed', upstream_dns_failed: 'ALAS upstream DNS lookup failed',
    upstream_connection_refused: 'ALAS upstream refused the connection', upstream_connect_timeout: 'ALAS upstream connection timed out',
    upstream_timeout: 'ALAS upstream communication timed out', upstream_network_error: 'ALAS upstream network error', proxy_internal_error: 'ALAS proxy internal error',
    request_timeout: 'Request timed out', filtered: 'Content filtered by policy', origin_forbidden: 'Origin not allowed',
    csrf_failed: 'Security check failed', policy_denied: 'Policy denied', payload_too_large: 'Payload too large', payload_too_complex: 'Payload is too complex',
    edit_permission_denied: 'Edit permission denied', run_permission_denied: 'Run permission denied', management_path_denied: 'Management path denied', management_denied: 'Management action denied', restricted_entry_denied: 'Restricted entry denied', restricted_user_entry_denied: 'Restricted user entry denied', alas_settings_denied: 'ALAS settings unavailable',
    invalid_json: 'Invalid JSON', invalid_binary: 'Invalid binary payload', invalid_body: 'Invalid request body', invalid_request: 'Invalid request', missing_request_config: 'Missing request configuration', config_mismatch: 'Configuration mismatch', config_path_mismatch: 'Configuration path mismatch',
    missing_binding: 'Missing binding', unconfigured: 'Not configured', disabled: 'Disabled', expired: 'Expired', account_locked: 'Account locked',
    runtime_operation_failed: 'Runtime operation failed', stream_start_failed: 'Cast start failed', stream_restart_failed: 'Cast restart failed', stop_failed: 'Stop failed', control_occupied: 'Control is occupied', not_lock_owner: 'Not the control owner',
    device_not_found: 'Device not found', device_disabled: 'Device disabled', device_permission_denied: 'Insufficient device permission', authentication_required: 'Sign-in required', login_required: 'Sign-in required', admin_required: 'Administrator permission required', password_change_required: 'Password change required', token_invalid: 'Invalid token', host_rejected: 'Host not allowed', origin_denied: 'Origin not allowed', origin_rejected: 'Origin not allowed', untrusted_proxy_headers: 'Untrusted proxy headers', rate_limited: 'Too many requests', adb_unavailable: 'ADB unavailable', adb_failed: 'ADB operation failed', missing_device_context: 'Missing device context', response_too_large: 'Response too large', encoded_response_unfilterable: 'Response could not be filtered safely', connection_failed: 'Connection failed', not_found: 'Resource not found', internal_error: 'Internal server error', http_status: 'Request returned an HTTP error', unhandled_exception: 'Unhandled service error', upstream_unreachable: 'Upstream service is unreachable', state_missing: 'Audit integrity state is missing', chain_link_mismatch: 'Audit chain link mismatch', event_hash_mismatch: 'Audit event verification failed', chain_head_mismatch: 'Audit chain head mismatch', protocol_state_required: 'Protocol state is incomplete', restricted_input_field: 'Restricted input field', missing_task_id: 'Missing task identifier', denied_task_id: 'Task identifier denied', unknown_task_id: 'Unknown task identifier', task_kind_mismatch: 'Task type mismatch', unknown_input_field: 'Unknown input field', missing_event: 'Missing event type', unknown_protocol_event: 'Unknown protocol event', denied_callback_id: 'Callback identifier denied', unknown_callback_id: 'Unknown callback identifier', restricted_callback: 'Restricted callback', invalid_event_data: 'Invalid event data', invalid_input_event: 'Invalid input event', invalid_downstream: 'Invalid upstream response', update_notice_dropped: 'Update entry filtered', other_instance_dropped: 'Content for another instance filtered', restricted_entry_dropped: 'Restricted entry filtered', alas_settings_dropped: 'ALAS settings content filtered', config_mismatch_dropped: 'Content for another configuration filtered', downstream_dropped: 'Unsafe content filtered',
    binding_revoked: 'Binding revoked', invalid_target: 'Invalid target', session_revoked: 'Session revoked', permission_revoked: 'Permission revoked', too_many_video_connections: 'Too many video connections', stream_ended: 'Video stream ended', stream_terminated: 'Video stream terminated', video_stream_ended: 'Video stream ended', connection_closed: 'Connection closed', client_disconnect: 'Client disconnected', cancelled: 'Operation cancelled', control_error: 'Control operation failed', open_timeout: 'Open timed out', adb_unreachable: 'ADB unreachable', device_unreachable: 'Device unreachable', account_status: 'Account status error', alas_embed_ws_failed: 'ALAS WebSocket failed', http_error: 'HTTP request failed', token_error: 'Token processing failed', dispatcher_unavailable: 'Audit service unavailable', migration_failed: 'Migration failed', parse_failed: 'Parse failed', quality_change_failed: 'Quality change failed', viewer_reservation_expired: 'Viewer reservation expired', verification_failed: 'Integrity check failed', catalog_error: 'Config catalog unavailable', saved_preference_unavailable: 'Saved quality preference unavailable', default_unavailable: 'Default configuration unavailable'
  };
  Object.assign(REASON_LABELS, {
    client_join: '观看端已加入', disconnect: '连接已断开', player_reset: '播放器已重置',
    control_player_reset: '控制播放器已重置', process_restart: '服务进程已重启',
    quality_changed: '画质已切换', stream_reset: '视频流已重置',
    video_client_drop: '观看端出现丢帧', video_client_terminate: '观看端已终止'
  });
  Object.assign(REASON_LABELS_EN, {
    client_join: 'Viewer joined', disconnect: 'Connection closed', player_reset: 'Player reset',
    control_player_reset: 'Control player reset', process_restart: 'Service process restarted',
    quality_changed: 'Quality changed', stream_reset: 'Video stream reset',
    video_client_drop: 'Viewer dropped frames', video_client_terminate: 'Viewer terminated'
  });

  var TARGET_LABELS = {
    device: '设备', device_viewer: '设备观看端', device_permission: '设备权限', account: '账户', account_permissions: '账户权限', user: '用户', audit_alert: '告警', audit_log: '审计日志',
    mirror: '投屏', viewer: '观看端', video_transport: '视频传输', video_preset: '画质预设', alas: 'ALAS', alas_config: 'ALAS 配置', alas_runtime: 'ALAS 运行时',
    alas_binding: 'ALAS 绑定', alas_shell: 'ALAS 页面', alas_proxy_route: 'ALAS 代理', system: '系统', system_settings: '系统设置', route: '路由', session: '会话', http_request: 'HTTP 请求', websocket: 'WebSocket'
  };
  var TARGET_LABELS_EN = {
    device: 'Device', device_viewer: 'Device viewer', device_permission: 'Device permission', account: 'Account', account_permissions: 'Account permissions', user: 'User', audit_alert: 'Alert', audit_log: 'Audit log',
    mirror: 'Cast', viewer: 'Viewer', video_transport: 'Video transport', video_preset: 'Quality preset', alas: 'ALAS', alas_config: 'ALAS config', alas_runtime: 'ALAS runtime',
    alas_binding: 'ALAS binding', alas_shell: 'ALAS page', alas_proxy_route: 'ALAS proxy', system: 'System', system_settings: 'System settings', route: 'Route', session: 'Session', http_request: 'HTTP request', websocket: 'WebSocket'
  };
  var TARGET_VALUE_LABELS = {
    alas: 'ALAS', alas_settings: 'ALAS 设置', alas_config: 'ALAS 配置', alas_runtime: 'ALAS 运行时',
    settings: '设置', config: '配置', dashboard: '仪表盘', admin: '管理后台',
    mirror: '投屏', device: '设备', session: '会话', system: '系统',
    audit_log: '审计日志', audit_alert: '告警', viewer: '观看端', video_transport: '视频传输', all: '全部', clean_sessions: '清理会话', connection: '连接', local_hash_chain: '本地日志链', reload_config: '重新加载配置', ui: '界面设置', user: '用户', video: '视频'
  };
  var TARGET_VALUE_LABELS_EN = {
    alas: 'ALAS', alas_settings: 'ALAS settings', alas_config: 'ALAS config', alas_runtime: 'ALAS runtime',
    settings: 'Settings', config: 'Config', dashboard: 'Dashboard', admin: 'Admin panel',
    mirror: 'Cast', device: 'Device', session: 'Session', system: 'System',
    audit_log: 'Audit log', audit_alert: 'Alert', viewer: 'Viewer', video_transport: 'Video transport', all: 'All', clean_sessions: 'Clean sessions', connection: 'Connection', local_hash_chain: 'Local audit chain', reload_config: 'Reload config', ui: 'UI settings', user: 'User', video: 'Video'
  };
  var CATEGORY_LABELS = {
    login: '登录与退出审计', authfail: '认证失败', acct: '账户状态变更', perm: '用户权限调整',
    dev: '设备操作', quality: '画质与传输配置修改', alas: 'ALAS 配置与运行操作', admin: '管理员操作'
  };
  var CATEGORY_LABELS_EN = {
    login: 'Sign-in and sign-out', authfail: 'Authentication failures', acct: 'Account state changes', perm: 'Permission changes',
    dev: 'Device actions', quality: 'Quality and transport changes', alas: 'ALAS config and runtime', admin: 'Administrator actions'
  };
  var RESULT_LABELS = { success: '成功', ok: '成功', passed: '成功', fail: '失败', failed: '失败', failure: '失败', abnormal: '失败', error: '错误', denied: '拒绝', blocked: '阻止', timeout: '超时', timed_out: '超时', cancelled: '已取消', canceled: '已取消', pending: '处理中', unknown: '未知' };
  var RESULT_LABELS_EN = { success: 'Success', ok: 'Success', passed: 'Success', fail: 'Failed', failed: 'Failed', failure: 'Failed', abnormal: 'Failed', error: 'Error', denied: 'Denied', blocked: 'Blocked', timeout: 'Timed out', timed_out: 'Timed out', cancelled: 'Cancelled', canceled: 'Cancelled', pending: 'Pending', unknown: 'Unknown' };
  var REASON_TITLES = { invalid_credentials: '登录失败', account_disabled: '账户已停用', account_expired: '账户已到期', device_offline: '设备离线', upstream_exception: '上游服务异常' };
  var REASON_TITLES_EN = { invalid_credentials: 'Sign in failed', account_disabled: 'Account disabled', account_expired: 'Account expired', device_offline: 'Device offline', upstream_exception: 'Upstream service error' };
  var ALERT_TITLES = {
    device_offline: '设备离线', account_status: '账户状态异常', account_disabled: '账户已停用',
    account_expired: '账户已到期', device_unavailable: '设备不可用', alas_error: 'ALAS 异常',
    alas_unavailable: 'ALAS 不可达', mirror_failure: '投屏异常', stream_start_failed: '投屏启动失败',
    connection_failed: '连接失败', permission_revoked: '权限已撤销', control_occupied: '控制权被占用',
    service_failure: '服务异常'
  };
  var ALERT_TITLES_EN = {
    device_offline: 'Device offline', account_status: 'Account status error', account_disabled: 'Account disabled',
    account_expired: 'Account expired', device_unavailable: 'Device unavailable', alas_error: 'ALAS error',
    alas_unavailable: 'ALAS unavailable', mirror_failure: 'Casting error', stream_start_failed: 'Cast start failed',
    connection_failed: 'Connection failed', permission_revoked: 'Permission revoked', control_occupied: 'Control is occupied',
    service_failure: 'Service error'
  };
  // Only these reason codes represent an incident.  Operational lifecycle
  // reasons such as client_join/disconnect/player_reset are activity rows,
  // even though they also have readable labels.
  var ALERT_REASON_KEYS = {
    invalid_credentials: true, account_status: true, account_disabled: true, account_expired: true,
    permission_denied: true, forbidden: true, device_offline: true, device_unavailable: true,
    upstream_exception: true, timeout: true, request_timeout: true, filtered: true,
    upstream_connection_closed: true, upstream_handshake_failed: true, upstream_tls_failed: true, upstream_dns_failed: true,
    upstream_connection_refused: true, upstream_connect_timeout: true, upstream_timeout: true, upstream_network_error: true, proxy_internal_error: true,
    origin_forbidden: true, csrf_failed: true, policy_denied: true, payload_too_large: true,
    payload_too_complex: true, edit_permission_denied: true, run_permission_denied: true,
    management_path_denied: true, management_denied: true, restricted_entry_denied: true,
    restricted_user_entry_denied: true, alas_settings_denied: true, invalid_json: true,
    invalid_binary: true, invalid_body: true, invalid_request: true, missing_request_config: true,
    config_mismatch: true, config_path_mismatch: true, missing_binding: true, unconfigured: true,
    disabled: true, expired: true, account_locked: true, runtime_operation_failed: true,
    stream_start_failed: true, stream_restart_failed: true, stop_failed: true, control_occupied: true,
    not_lock_owner: true, device_not_found: true, device_disabled: true, device_permission_denied: true,
    authentication_required: true, login_required: true, admin_required: true, password_change_required: true,
    token_invalid: true, host_rejected: true, origin_denied: true, origin_rejected: true,
    untrusted_proxy_headers: true, rate_limited: true, adb_unavailable: true, adb_failed: true,
    missing_device_context: true, response_too_large: true, encoded_response_unfilterable: true,
    connection_failed: true, invalid_target: true, adb_unreachable: true, device_unreachable: true, account_status: true, not_found: true, internal_error: true, http_status: true,
    unhandled_exception: true, upstream_unreachable: true, state_missing: true, chain_link_mismatch: true,
    event_hash_mismatch: true, chain_head_mismatch: true, protocol_state_required: true,
    restricted_input_field: true, invalid_event_data: true, invalid_input_event: true,
    invalid_downstream: true, downstream_dropped: true, update_notice_dropped: true,
    other_instance_dropped: true, restricted_entry_dropped: true, alas_settings_dropped: true,
    config_mismatch_dropped: true, too_many_video_connections: true, control_error: true,
    token_error: true, dispatcher_unavailable: true, migration_failed: true, parse_failed: true,
    quality_change_failed: true, viewer_reservation_expired: true, verification_failed: true,
    catalog_error: true, saved_preference_unavailable: true, default_unavailable: true
  };
  var GENERIC_TITLES = { '账户状态异常': true, '设备异常': true, 'ALAS 异常': true, '投屏异常': true, '服务异常': true, '系统异常': true, 'account status error': true, 'device error': true, 'alas error': true, 'casting error': true, 'service error': true, 'system error': true };
  var ACTION_ALIASES = {
    user_save: 'user_upsert', account_upsert: 'user_upsert', account_create: 'user_created', account_update: 'user_updated', account_delete: 'user_deleted',
    permission_grant: 'permission_granted', permission_revoke: 'permission_revoked', permission_update: 'permission_updated',
    mirror_autostop: 'mirror_auto_stop', mirror_idle_autostop: 'mirror_idle_stop', viewer_stop: 'mirror_viewer_stop', viewer_disconnect: 'mirror_viewer_disconnect',
    adb_test: 'device_adb_test', adb_reconnect: 'device_adb_reconnect',
    control_take_over: 'control_takeover', video_preference: 'video_preferences', video_preset_save: 'video_preset_update',
    alas_embed: 'alas_embed_open', alas_proxy_failed: 'alas_embed_proxy_failed', alas_ws_denied: 'alas_embed_ws_denied',
    settings_update: 'ui_settings_update', settings_import: 'ui_settings_import', settings_reset: 'ui_settings_reset', settings_maintenance: 'ui_settings_maintenance'
  };
  var CATEGORY_ALIASES = {
    auth: 'authfail', authentication: 'authfail', auth_failure: 'authfail', security: 'authfail',
    account: 'acct', user: 'acct', users: 'acct', permission: 'perm', permissions: 'perm',
    device: 'dev', devices: 'dev', mirror: 'dev', viewer: 'dev', video: 'quality', transport: 'quality',
    alas_runtime: 'alas', alert: 'admin', activity: 'admin'
  };
  var ACTION_LABEL_LOOKUP = null;

  function isEnglish() {
    return !!(global.ScrcpyGateI18n && typeof global.ScrcpyGateI18n.getLang === 'function' && global.ScrcpyGateI18n.getLang() === 'en-US');
  }
  function pick(zh, en) { return isEnglish() ? (en || zh) : zh; }
  function token(value) { return String(value == null ? '' : value).trim().toLowerCase(); }
  function code(value) { return token(value).replace(/[\s./:-]+/g, '_').replace(/_+/g, '_').replace(/^_|_$/g, ''); }
  function normalizeActionLabel(value) {
    return token(value)
      .replace(/(?:成功|通过|失败|错误|拒绝|阻止|超时|取消)$/g, '')
      .replace(/\s+(?:successfully|success|failed|failure|error|denied|blocked|timed\s+out|cancelled|canceled)$/i, '')
      .replace(/\s+/g, ' ')
      .trim();
  }
  function actionLabelLookup() {
    if (ACTION_LABEL_LOOKUP) return ACTION_LABEL_LOOKUP;
    ACTION_LABEL_LOOKUP = {};
    [ACTION_TITLES, ACTION_TITLES_EN].forEach(function (map) {
      Object.keys(map).forEach(function (key) {
        var label = normalizeActionLabel(map[key]);
        if (label && !ACTION_LABEL_LOOKUP[label]) ACTION_LABEL_LOOKUP[label] = key;
      });
    });
    return ACTION_LABEL_LOOKUP;
  }
  function reverseActionLabel(value) {
    var label = normalizeActionLabel(value);
    return label ? (actionLabelLookup()[label] || '') : '';
  }
  function canonicalAction(value) {
    // Some older audit producers append the outcome directly to the action
    // (for example ``alas_SETTINGS成功``). Strip that presentation suffix
    // before resolving the stable action code.
    var localized = reverseActionLabel(value);
    if (localized) return localized;
    var raw = normalizeActionLabel(value);
    var key = code(raw);
    if (!key) return '';
    if (ACTION_ALIASES[key]) return ACTION_ALIASES[key];
    if (ACTION_TITLES[key] || ACTION_TITLES_EN[key]) return key;
    var base = key.replace(/_(?:success|ok|failed|failure|error|denied|blocked|timeout)$/, '');
    return (ACTION_TITLES[base] || ACTION_TITLES_EN[base]) ? base : key;
  }
  function isOpaqueLabel(value) {
    var text = String(value == null ? '' : value).trim();
    if (!text) return false;
    // Audit producers before the dashboard projection sometimes put the
    // result directly after an action (for example ``alas_SETTINGS成功``).
    // Treat machine-shaped labels as codes so they can be mapped instead of
    // rendered verbatim. Human labels containing spaces or normal punctuation
    // remain untouched.
    return /^[a-z0-9]+(?:[_.:-][a-z0-9]+)*(?:成功|失败|错误|拒绝|阻止|超时)?$/i.test(text) ||
      /^[a-z0-9]+(?:[_.:-][a-z0-9]+)*(?:\s+(?:successfully|success|failed|failure|error|denied|blocked|timed\s+out))$/i.test(text);
  }
  function translateText(value) {
    var text = String(value == null ? '' : value).trim();
    if (!text || !isEnglish()) return text;
    return global.ScrcpyGateI18n && typeof global.ScrcpyGateI18n.t === 'function' ? global.ScrcpyGateI18n.t(text) : text;
  }
  function localized(zhMap, enMap, key, fallbackZh, fallbackEn) {
    return (isEnglish() ? enMap[key] : zhMap[key]) || pick(fallbackZh, fallbackEn);
  }
  function action(a) {
    return canonicalAction(a && (a.action || a.event || a.event_name || a.action_code || a.actionLabel || a.action_label));
  }
  function resultFromAction(value) {
    var raw = token(value);
    if (!raw) return '';
    if (/(?:成功|通过)$/.test(raw) || /(?:^|[_\s-])(?:success|successful|successfully|ok)$/.test(raw)) return 'success';
    if (/(?:失败|错误|拒绝|阻止|超时)$/.test(raw) || /(?:^|[_\s-])(?:failed|failure|error|denied|blocked|timeout|timed\s+out)$/.test(raw)) {
      if (/(?:拒绝|denied)$/.test(raw)) return 'denied';
      if (/(?:阻止|blocked)$/.test(raw)) return 'blocked';
      if (/(?:超时|timeout|timed\s+out)$/.test(raw)) return 'timeout';
      return 'failure';
    }
    return '';
  }
  function result(a) {
    var raw = a && (a.result || a.outcome || a.status || a.result_code || a.outcome_code);
    if (!raw && a && typeof a.ok === 'boolean') raw = a.ok ? 'success' : 'failure';
    if (!raw && a && typeof a.success === 'boolean') raw = a.success ? 'success' : 'failure';
    if (!raw && a) raw = resultFromAction(a.action || a.event || a.event_name || a.action_code || a.actionLabel || a.action_label);
    var key = code(raw || 'unknown');
    return ({ '成功': 'success', '通过': 'success', '正常': 'success', '失败': 'failure', '错误': 'error', '拒绝': 'denied', '阻止': 'blocked', '超时': 'timeout', '取消': 'cancelled', '处理中': 'pending', timed_out: 'timeout', timedout: 'timeout', successful: 'success', successfully: 'success', failed: 'failure', abnormal: 'failure' })[key] || key;
  }
  function displayResult(a) {
    var value = result(a), level = severity(a);
    if (value === 'abnormal' || value === '异常' || value === '不正常') value = 'failure';
    // A producer can accidentally leave the default success outcome on
    // an error-level alert. Keep the raw result available to diagnostics, but
    // make every user-facing status reflect the stronger severity signal.
    if ((level === 'error' || level === 'critical') &&
        (value === 'success' || value === 'ok' || value === 'passed' || value === 'unknown' || !value)) {
      return 'failure';
    }
    // An alert reason/type is authoritative even when a legacy producer
    // omitted the outcome or incorrectly left it as success. Never paint an
    // incident green in the activity feed; warning-level incidents stay in
    // the orange/denied visual state.
    if (isAlertRecord(a) &&
        (value === 'success' || value === 'ok' || value === 'passed' || value === 'unknown' || !value)) {
      return level === 'warning' || level === 'warn' ? 'denied' : 'failure';
    }
    return value;
  }
  function severity(a) {
    var value = code(a && (a.level || a.severity || a.severity_code));
    return ({ warning: 'warning', warn: 'warning', fatal: 'critical' })[value] || value;
  }
  function isAlertRecord(a) {
    if (typeof a === 'string') a = { action: a };
    if (!a || typeof a !== 'object') return false;
    if (a.alert === true || a.alertType || a.alert_type || a.isAlert === true || a.kind === 'alert') return true;
    var level = severity(a);
    if (['warning', 'warn', 'error', 'critical', 'high', 'fatal'].indexOf(level) >= 0) return true;
    var outcome = result(a);
    if (['fail', 'failed', 'failure', 'error', 'denied', 'blocked', 'timeout', 'timed_out'].indexOf(outcome) >= 0) return true;
    var alertType = code(a.alertType || a.alert_type);
    if (alertType && (ALERT_TITLES[alertType] || ALERT_TITLES_EN[alertType])) return true;
    var reason = code(a.reason || a.error_code || a.failure_reason || a.code);
    return !!(reason && ALERT_REASON_KEYS[reason]);
  }
  function resultLabel(a) {
    var key = displayResult(a);
    if (RESULT_LABELS[key]) return localized(RESULT_LABELS, RESULT_LABELS_EN, key, '未知', 'Unknown');
    var raw = a && (a.resultLabel || a.outcomeLabel);
    var rawKey = token(raw);
    if (RESULT_LABELS[rawKey]) return localized(RESULT_LABELS, RESULT_LABELS_EN, rawKey, '未知', 'Unknown');
    var readable = readableText(raw);
    return readable ? translateText(readable) : pick('未知', 'Unknown');
  }
  function resultClass(a) {
    var value = displayResult(a);
    var level = severity(a);
    if (level === 'error' || level === 'critical') return 'fail';
    if (value === 'success' || value === 'ok' || value === 'passed') return 'success';
    if (value === 'denied' || value === 'blocked' || value === 'timeout') return 'denied';
    if (value === 'fail' || value === 'failure' || value === 'error') return 'fail';
    if (value === 'pending') return 'pending';
    return 'unknown';
  }
  function category(a) {
    if (typeof a === 'string') a = { action: a };
    var explicit = code(a && (a.category || a.cat || a.type || a.eventType));
    if (CATEGORY_LABELS[explicit]) return explicit;
    if (CATEGORY_ALIASES[explicit]) return CATEGORY_ALIASES[explicit];
    var event = action(a);
    if (event.indexOf('viewer_watch_') === 0) return 'dev';
    if (event === 'login' || event === 'login_success' || event === 'logout' || event === 'page_index' || event === 'page_admin') return 'login';
    if (event === 'login_failed' || event === 'login_rate_limited' || event === 'authentication' || event === 'csrf_validation') return 'authfail';
    if (event.indexOf('user_') === 0 || event.indexOf('account_') === 0 || event.indexOf('password') === 0 || event === 'account_expired') return 'acct';
    if (event.indexOf('permission') === 0) return 'perm';
    if (event === 'client_join' || event === 'disconnect' || event === 'player_reset' || event === 'control_player_reset' || event.indexOf('device') === 0 || event.indexOf('adb') === 0 || event.indexOf('control') === 0 || event.indexOf('mirror') === 0 || event.indexOf('session') === 0) return 'dev';
    if (event === 'quality_changed' || event.indexOf('video') === 0 || event.indexOf('quality') === 0) return 'quality';
    if (event.indexOf('alas') === 0) return 'alas';
    return 'admin';
  }
  function categoryLabel(a) {
    var key = category(a);
    return localized(CATEGORY_LABELS, CATEGORY_LABELS_EN, key, '管理员操作', 'Administrator actions');
  }
  function readableActor(a) {
    var raw = a && (a.operatorLabel || a.actorName || a.actor || a.operator || a.username);
    var value = token(raw);
    if (!value) return pick('系统', 'System');
    if (value === '__boundary_probe__' || value === 'boundary_probe') return pick('测试探针', 'Test probe');
    return String(raw);
  }
  function readableText(value) {
    var text = String(value == null ? '' : value).trim();
    return !text || isOpaqueLabel(text) ? '' : text;
  }
  function reasonLabel(a) {
    var rawReason = a && (a.reason || a.error_code || a.failure_reason || a.code);
    var key = code(rawReason);
    if (REASON_LABELS[key]) return localized(REASON_LABELS, REASON_LABELS_EN, key, '需查看详情', 'See details');
    var readableReason = readableText(rawReason);
    if (readableReason) return translateText(readableReason);
    var summary = readableText(a && (a.summary || a.shortSummary));
    return summary ? translateText(summary) : (key ? pick('需查看详情', 'See details') : '');
  }
  function targetLabel(a) {
    var actorRaw = a && (a.operatorLabel || a.actorName || a.actor || a.operator || a.username);
    var actor = readableActor(a);
    var system = pick('系统', 'System');
    var actorSuffix = actor !== system ? pick(' · 操作者 ', ' · Actor ') + actor : '';
    var device = a && (a.deviceName || a.device_name);
    var target = a && (a.target || a.targetId || a.target_id);
    var targetType = code(a && (a.targetType || a.target_type));
    if (device) return pick('设备 ', 'Device ') + String(device) + actorSuffix;
    if (target && token(target) !== token(actorRaw)) {
      var typeLabel = localized(TARGET_LABELS, TARGET_LABELS_EN, targetType, '', '');
      var targetKey = token(target);
      var targetValue = localized(TARGET_VALUE_LABELS, TARGET_VALUE_LABELS_EN, targetKey, String(target), String(target));
      var semanticTarget = !!TARGET_VALUE_LABELS[targetKey] || targetKey === targetType;
      return (typeLabel && !semanticTarget ? typeLabel + ' ' : '') + targetValue + actorSuffix;
    }
    var event = action(a);
    if (event.indexOf('login') >= 0 || event.indexOf('logout') >= 0 || targetType === 'account' || targetType === 'user') {
      return actor !== system ? pick('账号 ', 'Account ') + actor : '';
    }
    return actorSuffix ? pick('操作者 ', 'Actor ') + actor : '';
  }
  function stripOutcomeSuffix(value, a) {
    var text = String(value == null ? '' : value).trim();
    var outcome = result(a);
    if (!text || (!outcome && action(a) !== 'login_success' && action(a) !== 'login_failed')) return text;
    return isEnglish() ? text.replace(/\s+(?:successfully|success|failed|failure|error|denied|blocked|timed\s+out)$/i, '').trim() : text.replace(/(?:成功|通过|失败|错误|拒绝|阻止|超时|取消)$/, '').trim();
  }
  function fallbackActionTitle(value) {
    var event = token(value);
    if (event.indexOf('alas') === 0) return pick('ALAS 操作', 'ALAS action');
    if (event.indexOf('mirror') === 0 || event.indexOf('video') === 0) return pick('投屏操作', 'Casting action');
    if (event.indexOf('device') === 0 || event.indexOf('adb') === 0) return pick('设备操作', 'Device action');
    if (event.indexOf('user') === 0 || event.indexOf('account') === 0) return pick('账户操作', 'Account action');
    return pick('系统操作', 'System action');
  }
  function looksLikeActionTitle(value) {
    var text = String(value == null ? '' : value).trim();
    if (!text) return false;
    var base = text.replace(/(?:成功|通过|失败|错误|拒绝|阻止|超时|取消)$/g, '')
      .replace(/\s+(?:successfully|success|failed|failure|error|denied|blocked|timed\s+out|cancelled|canceled)$/i, '').trim();
    return Object.keys(ACTION_TITLES).some(function (key) {
      return ACTION_TITLES[key] === text || ACTION_TITLES_EN[key] === text ||
        ACTION_TITLES[key] === base || ACTION_TITLES_EN[key] === base;
    });
  }
  function title(a) {
    var event = action(a);
    var reason = code(a && (a.reason || a.error_code || a.failure_reason || a.code));
    var alertType = code(a && (a.alertType || a.alert_type));
    var isAlert = isAlertRecord(a);
    var explicit = a && a.title;
    var value = explicit && !GENERIC_TITLES[String(explicit)] && !isOpaqueLabel(explicit) ? String(explicit) : '';
    if (isAlert && looksLikeActionTitle(value)) value = '';
    // Alert cards should lead with the actual problem (for example
    // "设备离线" or "账户已停用"), not the operation that happened to
    // trigger it (for example "开始投屏"). Activity rows keep the action
    // first because they describe an operation rather than an incident.
    if (isAlert) {
      var alertReason = reasonLabel(a);
      if (alertReason && alertReason !== pick('需查看详情', 'See details')) value = alertReason;
      if (!value && ALERT_TITLES[alertType]) value = localized(ALERT_TITLES, ALERT_TITLES_EN, alertType, '告警', 'Alert');
    }
    if (!value && ACTION_TITLES[event]) value = localized(ACTION_TITLES, ACTION_TITLES_EN, event, '审计事件', 'Audit event');
    if (!value && REASON_TITLES[reason]) value = localized(REASON_TITLES, REASON_TITLES_EN, reason, '审计事件', 'Audit event');
    if (!value && ALERT_TITLES[alertType]) value = localized(ALERT_TITLES, ALERT_TITLES_EN, alertType, '告警', 'Alert');
    if (value) {
      value = stripOutcomeSuffix(value, a);
      if (event === 'login_success' || event === 'login_failed') {
        var actor = readableActor(a);
        if (actor !== pick('系统', 'System')) return actor + ' ' + value;
      }
      return translateText(value);
    }
    var raw = a && (a.actionLabel || a.action_label || a.action || a.message || a.event || explicit);
    var readable = readableText(raw);
    var mappedRaw = canonicalAction(raw);
    if (mappedRaw && (ACTION_TITLES[mappedRaw] || ACTION_TITLES_EN[mappedRaw])) {
      return localized(ACTION_TITLES, ACTION_TITLES_EN, mappedRaw, '审计事件', 'Audit event');
    }
    // Older producers sometimes send only a localized action label such as
    // "开始投屏成功" without the stable action code. Keep that label readable
    // while removing the duplicated outcome suffix from the title.
    return readable ? translateText(stripOutcomeSuffix(readable, a)) : fallbackActionTitle(event);
  }
  function meta(a) {
    var parts = [];
    var subject = targetLabel(a);
    var reason = reasonLabel(a);
    if (subject) parts.push(subject);
    if (reason) parts.push(pick('原因：', 'Reason: ') + reason);
    if (!parts.length) {
      var summary = readableText(a && (a.summary || a.shortSummary));
      if (summary) parts.push(translateText(summary));
    }
    return parts.join(' · ') || pick('无附加信息', 'No additional details');
  }
  function severityLabel(a) {
    var value = severity(a);
    return (isEnglish() ? { critical: 'Critical', error: 'Error', warning: 'Warning', warn: 'Warning', info: 'Info', debug: 'Debug' } : { critical: '严重', error: '错误', warning: '警告', warn: '警告', info: '信息', debug: '调试' })[value] || pick('需关注', 'Needs attention');
  }
  var RUNTIME_EVENT_LABELS = {
    app_cleanup_failed: '应用清理失败',
    http_request: 'HTTP 请求',
    http_request_failed: 'HTTP 请求失败',
    mirror_autostop_audit_failed: '自动停止审计失败',
    mirror_autostop_iteration_failed: '自动停止任务失败',
    security_audit_persist_failed: '审计写入失败',
    security_audit_read_barrier_timeout: '审计读取等待超时'
  };
  var RUNTIME_EVENT_LABELS_EN = {
    app_cleanup_failed: 'Application cleanup failed',
    http_request: 'HTTP request',
    http_request_failed: 'HTTP request failed',
    mirror_autostop_audit_failed: 'Auto-stop audit failed',
    mirror_autostop_iteration_failed: 'Auto-stop iteration failed',
    security_audit_persist_failed: 'Audit write failed',
    security_audit_read_barrier_timeout: 'Audit read barrier timed out'
  };
  function runtimeEventLabel(value) {
    var key = code(value);
    if (RUNTIME_EVENT_LABELS[key] || RUNTIME_EVENT_LABELS_EN[key]) {
      return localized(RUNTIME_EVENT_LABELS, RUNTIME_EVENT_LABELS_EN, key, '系统运行事件', 'Runtime event');
    }
    return pick('系统运行事件', 'Runtime event');
  }

  // Status values arrive from several generations of the API.  Normalize them
  // once at the presentation boundary so every dashboard section uses the
  // same wording and tone, including after a locale switch.
  var STATUS_ALIASES = {
    running: 'running', healthy: 'running', ok: 'running', online: 'online', connected: 'connected',
    normal: 'normal', healthy_state: 'normal', expiring: 'expiring',
    idle: 'stopped', stopped: 'stopped', disabled: 'disabled', expired: 'expired', unconfigured: 'unconfigured',
    unknown: 'unknown', unbound: 'unbound', unlinked: 'unbound', error: 'error', failed: 'error', failure: 'error',
    partial: 'partial_error', partial_error: 'partial_error', degraded: 'partial_error',
    unreachable: 'unreachable', disconnected: 'unreachable', timeout: 'timeout',
    connecting: 'connecting', pending: 'connecting', check_fail: 'check_fail', check_failed: 'check_fail',
    token_missing: 'token_missing', token_invalid: 'token_invalid', http_error: 'http_error', invalid_config: 'error',
    '运行中': 'running', '正常': 'normal', '在线': 'online', '已连接': 'connected', '已停止': 'stopped',
    '已停用': 'disabled', '已到期': 'expired', '未配置': 'unconfigured', '未检查': 'unknown', '未关联': 'unbound',
    '异常': 'error', '部分异常': 'partial_error', '不可达': 'unreachable', '已断开': 'unreachable',
    '连接中': 'connecting', '检测失败': 'check_fail', '连接超时': 'timeout', 'Token 缺失': 'token_missing', 'Token 无效': 'token_invalid', '响应异常': 'http_error'
  };
  var STATUS_LABELS = {
    running: '运行中', online: '在线', connected: '已连接', normal: '正常', expiring: '即将到期', connecting: '连接中', check_fail: '检测失败', stopped: '已停止', disabled: '已停用', expired: '已到期',
    unconfigured: '未配置', unknown: '未检查', unbound: '未关联', error: '异常', partial_error: '部分异常',
    unreachable: '不可达', timeout: '连接超时', token_missing: 'Token 缺失', token_invalid: 'Token 无效', http_error: '响应异常'
  };
  var STATUS_LABELS_EN = {
    running: 'Running', online: 'Online', connected: 'Connected', normal: 'Normal', expiring: 'Expiring soon', connecting: 'Connecting', check_fail: 'Check failed', stopped: 'Stopped', disabled: 'Disabled', expired: 'Expired',
    unconfigured: 'Not configured', unknown: 'Not checked', unbound: 'Unlinked', error: 'Error',
    partial_error: 'Partially degraded', unreachable: 'Unreachable', timeout: 'Timed out',
    token_missing: 'Token missing', token_invalid: 'Invalid token', http_error: 'Invalid response'
  };
  function statusKey(value) {
    var raw = String(value == null ? '' : value).trim();
    if (!raw) return 'unknown';
    if (STATUS_ALIASES[raw]) return STATUS_ALIASES[raw];
    var normalized = code(raw);
    return STATUS_ALIASES[normalized] || normalized || 'unknown';
  }
  function statusLabel(value, fallbackZh, fallbackEn) {
    var key = statusKey(value);
    return localized(STATUS_LABELS, STATUS_LABELS_EN, key, fallbackZh || '未检查', fallbackEn || 'Not checked');
  }
  function formatCount(value) {
    var number = Number(value);
    if (!isFinite(number) || number < 0) number = 0;
    number = Math.floor(number);
    try {
      if (typeof Intl !== 'undefined' && Intl.NumberFormat) {
        return new Intl.NumberFormat(isEnglish() ? 'en-US' : 'zh-CN').format(number);
      }
    } catch (error) { /* Older browsers may not expose Intl. */ }
    return String(number);
  }
  function parseDateValue(value) {
    if (value === null || value === undefined || value === '') return '';
    if (value instanceof Date) return isNaN(value.getTime()) ? null : value;
    var raw = String(value).trim();
    var date;
    if (/^[+-]?\d+(?:\.\d+)?$/.test(raw)) {
      var numeric = Number(raw);
      date = new Date(Math.abs(numeric) >= 1000000000000 ? numeric : numeric * 1000);
    } else {
      date = new Date(raw.replace(' ', 'T'));
    }
    return isNaN(date.getTime()) ? null : date;
  }
  function dateTime(value) {
    var date = parseDateValue(value);
    return date ? date.toISOString() : '';
  }
  function formatTime(value) {
    var date = parseDateValue(value);
    if (!date) return value === null || value === undefined || value === '' ? '' : String(value);
    try {
      if (typeof Intl !== 'undefined' && Intl.DateTimeFormat) {
        return new Intl.DateTimeFormat(isEnglish() ? 'en-US' : 'zh-CN', {
          year: 'numeric', month: '2-digit', day: '2-digit',
          hour: '2-digit', minute: '2-digit', second: '2-digit'
        }).format(date);
      }
    } catch (error) { /* Older browsers may not expose the requested formatter. */ }
    return date.toLocaleString();
  }
  function timeLabel(value, now) {
    var date = parseDateValue(value);
    if (!date) return value === null || value === undefined || value === '' ? '—' : String(value);
    var delta = date.getTime() - (now == null ? Date.now() : Number(now));
    if (!isFinite(delta)) return formatTime(value);
    var absolute = Math.abs(delta);
    if (absolute < 45000) return pick('刚刚', 'Just now');
    var unit = 1000, name = 'second';
    if (absolute >= 86400000) { unit = 86400000; name = 'day'; }
    else if (absolute >= 3600000) { unit = 3600000; name = 'hour'; }
    else if (absolute >= 60000) { unit = 60000; name = 'minute'; }
    // Preserve the direction when rounding. Clamping the signed value with
    // Math.max(1, ...) turns past events into future events (for example,
    // "1 minute ago" becomes "1 minute from now").
    // Round the magnitude first so negative values are symmetric with future
    // values (90 seconds is two minutes in either direction).
    var amount = Math.round(absolute / unit) * (delta < 0 ? -1 : 1);
    if (amount === 0) amount = delta < 0 ? -1 : 1;
    try {
      if (typeof Intl !== 'undefined' && Intl.RelativeTimeFormat) {
        return new Intl.RelativeTimeFormat(isEnglish() ? 'en-US' : 'zh-CN', { numeric: 'always', style: 'short' }).format(amount, name);
      }
    } catch (error) { /* Fall back to a deterministic label below. */ }
    var count = Math.abs(amount);
    if (isEnglish()) {
      var suffix = count === 1 ? name : name + 's';
      return amount < 0 ? count + ' ' + suffix + ' ago' : 'in ' + count + ' ' + suffix;
    }
    var zhUnit = name === 'day' ? '天' : (name === 'hour' ? '小时' : (name === 'minute' ? '分钟' : '秒'));
    return count + ' ' + zhUnit + (amount < 0 ? '前' : '后');
  }

  var mapping = {
    actionTitles: ACTION_TITLES,
    reasonLabels: REASON_LABELS,
    targetLabels: TARGET_LABELS,
    categoryLabels: CATEGORY_LABELS,
    resultLabels: RESULT_LABELS,
    isEnglish: isEnglish,
    code: code,
    canonicalAction: canonicalAction,
    reverseActionLabel: reverseActionLabel,
    action: action,
    result: result,
    displayResult: displayResult,
    severity: severity,
    isAlertRecord: isAlertRecord,
    resultLabel: resultLabel,
    resultClass: resultClass,
    category: category,
    categoryLabel: categoryLabel,
    readableActor: readableActor,
    readableText: readableText,
    reasonLabel: reasonLabel,
    targetLabel: targetLabel,
    title: title,
    meta: meta,
    severityLabel: severityLabel,
    runtimeEventLabel: runtimeEventLabel,
    statusKey: statusKey,
    statusLabel: statusLabel,
    formatCount: formatCount,
    dateTime: dateTime,
    formatTime: formatTime,
    timeLabel: timeLabel,
    dotClass: function (a) {
      var value = displayResult(a), level = severity(a);
      if (level === 'error' || level === 'critical' || value === 'failure' || value === 'fail' || value === 'error') return 'red';
      if (level === 'warning' || level === 'warn' || level === 'high' || value === 'denied' || value === 'blocked' || value === 'timeout') return 'orange';
      if (value === 'success' || value === 'ok' || value === 'passed') return 'green';
      return 'blue';
    }
  };

  // Explicit aliases keep the split owner compatible with the historical
  // dashboard vocabulary while exposing the new fixed audit-mapping API.
  mapping.actionKey = action;
  mapping.category = category;
  mapping.reason = reasonLabel;
  mapping.result = result;
  mapping.resultLabel = resultLabel;
  mapping.resultClass = resultClass;
  mapping.severity = severity;
  mapping.isAlert = isAlertRecord;
  mapping.readableText = readableText;

  var owner = Object.freeze(mapping);
  global.ScrcpyGateAuditMapping = owner;
  // Pages that still include the legacy dashboard asset get the same object;
  // the compatibility proxy is deliberately not replaced.
  if (!global.ScrcpyGateDashboard || global.ScrcpyGateDashboard.__auditCompatibilityProxy === true) {
    global.ScrcpyGateDashboard = owner;
  }
})(window);
