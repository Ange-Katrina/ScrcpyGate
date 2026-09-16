/**
 * ScrcpyGate 全局国际化引擎
 * - 存储键 scrcpygate-lang：zh-CN / en-US
 * - 翻译策略：精确匹配优先 → 数值复合正则 → 最长短语替换
 * - 属性翻译：placeholder / title / aria-label / alt（精确匹配）
 * - MutationObserver 监听动态内容（Toast / 列表刷新等）
 * - 绑定 [data-lang-switch] 下拉框
 */
(function () {
  'use strict';

  var STORE_KEY = 'scrcpygate-lang';
  var LANG_MAP = { 'zh-CN': 'zh', 'zh': 'zh', 'en-US': 'en', 'en': 'en' };
  var current = 'zh';
  try {
    var storedLocale = localStorage.getItem(STORE_KEY);
    current = LANG_MAP[storedLocale] || 'zh';
    if (storedLocale && !LANG_MAP[storedLocale]) localStorage.setItem(STORE_KEY, 'zh-CN');
  } catch (e) { }

  /* ================= 词典：中文 → English ================= */
  var EN = {
    // 登录页
    '登录': 'Sign In', '界面语言': 'Language', '简体中文': '简体中文', '主题': 'Theme',
    '跟随系统': 'System', '浅色': 'Light', '深色': 'Dark', '远程投屏控制台': 'Remote Casting Console',
    '用户名': 'Username', '密码': 'Password', '用户名或密码错误': 'Invalid username or password',
    '请输入用户名': 'Enter username', '请输入密码': 'Enter password', '显示密码': 'Show password', '隐藏密码': 'Hide password',
    // 登录保护（滑块 + 工作量证明 + 封禁）
    '人机验证': 'Human verification', '等待拖动滑块': 'Waiting for the slider',
    '按住滑块，拖动到最右侧': 'Drag the slider to the far right',
    '未拖到最右侧，请重试': 'Release at the far right to continue',
    '已确认，正在计算人机验证…': 'Confirmed, computing human verification…',
    '已确认': 'Confirmed',
    '正在申请验证挑战…': 'Requesting a verification challenge…',
    '正在准备人机验证…': 'Preparing human verification…',
    '验证已就绪，可直接登录': 'Verification ready — you can sign in now',
    '人机验证计算失败，点「登录」重试': 'Human verification failed to compute. Press "Sign in" to retry.',
    '计算完成': 'Computation complete',
    '登录已暂时封禁': 'Sign-in temporarily blocked',
    '封禁已结束；仍需完成人机验证后才能登录。': 'The block ended; complete human verification to sign in.',
    '人机验证未通过，请重试。': 'Human verification failed. Try again.',
    // 管理面板 · 登录保护
    '登录保护': 'Login protection', '按来源 IP 封禁，管理员可一键解除': 'Per-source-IP blocks; one-click admin unlock',
    '当前没有封禁或失败计数': 'No blocks or failure counts right now', '解除': 'Unblock', '清除': 'Clear',
    // 管理面板 · 安全机制（开关 / 阈值 / 时长）
    '安全机制': 'Security', '总开关': 'Master switch', '阈值与时长': 'Thresholds & durations',
    '高级': 'Advanced', '当前封禁': 'Current blocks', '未修改': 'Unchanged', '有未保存的修改': 'Unsaved changes',
    '停用': 'Off', '宽松': 'Relaxed', '标准': 'Standard', '严格': 'Strict',
    '触发人机验证': 'Require human verification', '封禁阈值': 'Block threshold', '统计窗口': 'Counting window',
    '封禁时长': 'Block duration', '验证码有效期': 'Captcha lifetime', '挑战签发间隔': 'Challenge issue interval',
    '已关闭': 'Off', '仅封禁': 'Block only', '人机验证已启用': 'Human verification on',
    '加载失败': 'Load failed', '解除失败': 'Unblock failed',
    '已保存并立即生效': 'Saved and applied', '已恢复为环境变量默认值': 'Restored to environment defaults',
    '失败计数、人机验证与封禁的总开关；关闭后登录不再有任何限制': 'Master switch for failure counting, human verification and blocking; when off, sign-in is unrestricted',
    '失败后要求完成滑块 + 工作量证明；关闭则只保留失败计数与封禁': 'Requires a slider plus proof-of-work after a failure; when off only counting and blocking remain',
    '连续失败多少次后开始要求验证码': 'Failed attempts before the captcha is required',
    '统计窗口内失败多少次后暂时封禁来源 IP': 'Failures inside the window before the source IP is blocked',
    '失败次数在这个时间窗内累计': 'Failures accumulate inside this window',
    '达到阈值后拒绝该 IP 登录的时长（期间正确密码也拒绝）': 'How long the IP is refused after the threshold (the correct password is refused too)',
    '签发的挑战在多少秒内有效': 'How long an issued challenge stays valid',
    '同一 IP 两次签发挑战的最小间隔；设为 0 表示不限流': 'Minimum interval between challenges for one IP; 0 disables the limit',
    '常用强度预设': 'Common strength presets', '触发人机验证的失败次数': 'Failures before the captcha', '封禁阈值次数': 'Block threshold failures',
    '统计窗口分钟数': 'Counting window in minutes', '封禁时长分钟数': 'Block duration in minutes',
    '验证码有效期秒数': 'Captcha lifetime in seconds', '挑战签发间隔秒数': 'Challenge issue interval in seconds',
    // 投屏工作台
    '镜像工作区': 'Mirror Workspace', '投屏工作台': 'Casting Workspace', '观看会话': 'View Sessions',
    '管理员': 'Administrator', '画面': 'Screen', '画质与显示': 'Quality & Display',
    '配置与运行': 'Config & Runtime', '账户': 'Account', '密码与资料': 'Password & Profile',
    '账号到期时间': 'Account expiry', '设备权限': 'Device access', '修改密码': 'Change password',
    '语言': 'Language', '退出登录': 'Sign out', '后台管理': 'Admin Panel', '设备与系统': 'Devices & System',
    // 账户菜单
    '资料、偏好与安全': 'Profile, preferences & security', '账户信息': 'Account info', '当前登录': 'Signed in',
    '由管理员分配': 'Assigned by admin', '暂无设备权限': 'No device access yet', '请联系管理员为你分配设备': 'Contact the administrator to grant device access',
    '权限信息不可用': 'Permission info unavailable', '简体中文': '简体中文', '浅色': 'Light', '深色': 'Dark',
    '至少 12 位': 'Min 12 characters', '至少 6 位': 'Min 6 characters', '当前密码': 'Current password', '新密码': 'New password', '确认新密码': 'Confirm new password',
    '保存新密码': 'Save password', '新密码至少 12 位': 'New password needs at least 12 characters', '新密码至少 6 位': 'New password needs at least 6 characters',
    '两次输入的新密码不一致': 'New passwords do not match', '正在保存…': 'Saving…', '密码已更新': 'Password updated',
    '保存失败': 'Save failed', '账户信息已同步': 'Account info synced',
    '账号有效期': 'Account validity', '长期有效': 'No expiry', '无到期限制': 'No expiry limit',
    '剩余': 'remaining', '天': 'days', '已过期': 'Expired', '申请续期': 'Request renewal', '申请已提交': 'Submitted',
    '正在提交续期申请…': 'Submitting renewal request…', '续期申请已提交,等待管理员处理': 'Renewal request submitted, awaiting administrator',
    '上次登录': 'Last sign-in', '提交失败': 'Request failed',
    '请输入当前密码': 'Enter your current password',
    '画质服务未连接,使用内置默认值': 'Quality service not connected, using built-in defaults',
    '画质服务未连接': 'Quality service is unavailable',
    'ALAS 服务未连接': 'ALAS service not connected',
    'ALAS 服务未连接,显示本地状态': 'ALAS service not connected, showing local state',
    '账户服务未连接': 'Account service is unavailable',
    '设备权限与账号状态由管理员维护;退出后需要重新登录才能继续使用。': 'Device access and account status are managed by the administrator; sign in again after signing out.',
    '设备': 'Devices', '在线': 'Online', '有控制权限': 'Control access', '自用': 'Personal',
    '备用机': 'Backup', '可获取控制': 'Control available', '退出': 'Exit', '退出当前账户': 'Sign out of this account',
    '尚未开始观看': 'Not watching yet', '正在连接': 'Connecting', '等待画面': 'Waiting for video',
    '等待关键帧': 'Waiting for keyframe', '正在恢复': 'Recovering', '设备离线': 'Device offline',
    '无设备权限': 'No device access', '连接失败': 'Connection failed', '账号已到期': 'Account expired',
    '正常观看中': 'Streaming normally', '设备连接': 'Device link', '控制权': 'Control',
    '控制中': 'In control', '观看端': 'Viewers', '剩余': 'left', '运行中': 'Running', '画质': 'Quality',
    '高清': 'HD', '选择左侧设备后开始观看实时画面': 'Select a device on the left to start watching',
    '开始观看': 'Start watching', '正在连接设备': 'Connecting to device', '请稍候': 'Please wait',
    '取消': 'Cancel', '正在等待画面': 'Waiting for video', '设备已连接，等待视频流就绪': 'Device connected, waiting for stream',
    '正在等待首个关键帧，画面即将出现': 'Waiting for the first keyframe', '连接中断，正在自动恢复播放': 'Connection lost, auto-recovering',
    '重试连接': 'Retry', '已断开连接，请检查设备状态': 'Disconnected, check the device', '选择其他设备': 'Choose another device',
    '你没有该设备的控制权限，请联系管理员授权': 'You have no control access, contact the administrator',
    '申请权限': 'Request access', '无法建立连接，可能是网络或设备问题': 'Cannot connect, network or device issue',
    '重试': 'Retry', '查看日志': 'View logs', '观看权限已到期，续期后可继续使用': 'Viewing access expired, renew to continue',
    '前往续期': 'Renew', '实时画面': 'Live view', '点击画面可控制设备': 'Click the screen to control the device',
    '将在': 'will stop in', '分钟后停止观看': 'minutes', '延长': 'Extend', '分钟': 'min',
    '获取控制': 'Take control', '占用中': 'In use', '释放控制': 'Release control',
    '接管控制': 'Take over control', '确认接管控制': 'Confirm taking control', '当前控制权由其他用户持有': 'Another user currently controls this device',
    // 只有观看权限时的控制按钮文案与提示。
    '仅观看': 'View only', '没有该设备的控制权限，请联系管理员授权': 'No control permission for this device — ask an administrator',
    // 全屏自动获取控制（画面「更多」菜单里的本浏览器偏好）。
    '全屏后默认获取控制': 'Get control automatically in fullscreen',
    '全屏后默认获取控制（已开启）': 'Get control automatically in fullscreen (on)',
    '全屏后默认获取控制（已关闭）': 'Get control automatically in fullscreen (off)',
    '全屏后默认获取控制已开启': 'Fullscreen will take control automatically',
    '全屏后默认获取控制已关闭': 'Fullscreen will not take control automatically',
    '设备正被其他用户控制，可点「接管控制」': 'Another user controls this device — use "Take over control"',
    '接管后，其他用户将立即失去控制权，但仍可继续观看视频。': 'Taking over will immediately revoke their control, but they can keep watching the video.',
    '确认接管': 'Confirm takeover', '控制权已接管': 'Control taken over', '控制权接管失败': 'Control takeover failed', '正在接管控制…': 'Taking control…',
    '已释放控制': 'Control released', '截图': 'Screenshot', '录制': 'Record',
    '音频': 'Audio', '剪贴板': 'Clipboard', '电源键': 'Power', '音量': 'Volume', '停止设备投屏': 'Stop device casting',
    '管理台': 'Console', '停止观看': 'Stop watching', '旋转画面': 'Rotate', '键盘输入': 'Keyboard',
    '截图已复制到剪贴板': 'Screenshot copied to clipboard',
    '浏览器不支持复制图片，已改为下载': 'This browser cannot copy images; downloaded instead',
    '浏览器未授权复制图片，已改为下载': 'Clipboard permission denied; downloaded instead',
    '当前没有可截取的画面': 'Nothing to capture yet', '截图失败，请重试': 'Screenshot failed, please retry',
    '恢复画面方向': 'Reset view', '配置码率/实测码率': 'Configured / measured bitrate',
    '返回': 'Back', '主页': 'Home', '多任务': 'Recents', '更多': 'More', '全屏': 'Fullscreen',
    '确认停止全部投屏': 'Stop all casting sessions',
    // 管理后台 侧边栏 / 顶栏
    '工作台': 'Workspace', '仪表盘': 'Dashboard', '概览统计': 'Overview', '活跃会话': 'Active sessions',
    '观看端与延迟': 'Viewers & latency', '资源管理': 'Resources', '设备管理': 'Devices',
    '投屏与状态': 'Casting & status', '用户与权限': 'Users & permissions', '角色与到期': 'Roles & expiry',
    '管理': 'Manage', '配置与绑定': 'Config & binding', '系统': 'System', '画质与传输': 'Quality & transfer',
    '分辨率与码率': 'Resolution & bitrate', '日志审计': 'Log audit', '记录与追踪': 'Records & tracking',
    '系统设置': 'Settings', '通用参数': 'General', '返回投屏工作台': 'Back to workspace', '前往画面与控制': 'View & control',
    '帮助': 'Help', '使用文档': 'Docs', '首页': 'Home', '管理后台': 'Admin', '账户设置': 'Account settings',
    '设备、用户与系统运行状态总览': 'Overview of devices, users and system health',
    '设备可用性': 'Device availability', '异常': 'Abnormal', '离线': 'Offline', '最近检查': 'Last check',
    '投屏会话': 'Casting sessions', '路投屏': 'casts', '个控制中': 'in control', '个超时预警': 'timeout alerts',
    '服务': 'Service', '正常': 'OK', '配置在线': 'Config online', '已配置': 'Configured', '待处理事项': 'Pending items',
    '项待处理': 'pending', '个账号将到期': 'accounts expiring', '个长时间投屏': 'long casts', '条安全告警': 'alerts',
    '设备列表': 'Devices', '添加设备': 'Add device', '全部': 'All', '投屏中': 'Casting', '设备名称': 'Name',
    '投屏状态': 'Casting status', '状态': 'Status', '上次心跳': 'Last heartbeat', '操作': 'Actions', '心跳': 'Heartbeat',
    '昨天': 'yesterday', '打开': 'Open', '断开': 'Disconnect', '重启投屏': 'Restart cast', '编辑': 'Edit',
    '复制设备': 'Copy device', '小时前': 'h ago', '分钟前': 'min ago', '空闲': 'Idle', '未配置': 'Not configured',
    '当前无更多异常': 'No more issues', '活跃投屏': 'Active casts', '个会话': 'sessions', '查看全部': 'View all',
    '已投屏': 'Casting', '告警': 'Alerts', '需要处理的异常': 'Issues to handle', '账号已过期': 'Account expired',
    '业务状态与服务异常': 'Business state and service issues', '业务待处理': 'Business items', '服务异常': 'Service issues',
    '等待概览数据': 'Waiting for overview data',
    '手动微调': 'Manual tuning', '管理员尚未启用任何画质预设': 'No preset has been enabled by the administrator',
    '当前使用手动微调参数，可在「微调」页签修改。': 'Using manually tuned parameters; adjust them in the Tuning tab.',
    '带 * 的字段为必填项': 'Fields marked with * are required',
    'ADB 地址需为 主机:端口 格式，例如 192.0.2.10:5555（USB 设备直接填序列号）': 'The ADB address must be host:port, for example 192.0.2.10:5555 (use the serial for USB devices)',
    '停止投屏': 'Stop casting', '未投屏': 'Not casting', '刷新设备': 'Refresh devices', '控制': 'Control',
    '正在开始投屏…': 'Starting cast…', '正在连接…': 'Connecting…', '设备离线，无法投屏': 'Device is offline; cannot cast',
    '没有该设备的观看权限': 'No permission to view this device', '最多同时预览 ': 'Up to ',
    ' 路，请先停止其他格子': ' live tiles at once; stop another tile first',
    '服务端未启动投屏': 'The server did not start casting', '开始投屏失败': 'Failed to start casting',
    '无法建立视频连接': 'Could not open the video connection', '观看已结束，请重新开始投屏': 'Viewing ended; start casting again',
    '视频连接已断开': 'Video connection lost', ' 人观看': ' viewers', '控制：': 'Control: ',
    '全部停止': 'Stop all',
    '开始截图': 'Start snapshots', '停止截图': 'Stop snapshots', '未截图': 'No snapshot',
    '正在开始截图…': 'Starting snapshots…', '开始截图失败': 'Failed to start snapshots',
    '全部截图': 'Snapshot all', '刷新截图': 'Refresh snapshots',
    '点击「开始截图」后按所选帧率抓取画面，最多同时预览 {0} 路': 'Tiles capture at the selected frame rate; up to {0} at once',
    '帧率': 'Frame rate', '同时显示': 'Tiles',
    '空坑位': 'Empty slot', '未添加设备': 'No device yet', '添加 ADB 设备': 'Add ADB device',
    '宫格固定 12 个坑位，空坑位可直接添加设备': 'The grid always keeps 12 slots; add a device from any empty slot',
    '需要管理员权限，请联系管理员添加设备': 'Administrator access required — ask an administrator to add the device',
    '画面方向': 'Tile shape', '宫格画面方向': 'Grid tile shape',
    '跟随设备': 'Follow device', '统一竖屏': 'Force portrait',
    '管理员工具与后台页': 'Admin tools and back office',
    '只挡普通用户：管理员始终看到完整的 ALAS 页面': 'Normal users only; administrators always see the full ALAS page',
    '隐藏范围': 'Hidden scope',
    '写死在代码里，只读；改范围请改对应的常量': 'Fixed in code and read-only; edit the constants to change it',
    '可见': 'Visible', '受限': 'Restricted', '项对普通用户可见': 'visible to normal users',
    '导航入口（整页）': 'Navigation pages',
    '普通用户在 ALAS 左侧看不到这些入口，直接访问对应地址也会被拒绝（403）。': 'Normal users lose these entries in the ALAS sidebar and get 403 when opening the URL directly.',
    '管理员专用任务': 'Administrator-only tasks',
    '普通用户的任务列表里不出现这些任务，也无法通过接口触发。': 'These tasks disappear from a normal user s task list and cannot be triggered through the API.',
    '模拟器与录制设置': 'Emulator and recording settings',
    '普通用户在 ALAS 设置页看不到这些项。': 'Normal users cannot see these keys on the ALAS settings page.',
    '配置编辑器': 'Config editor',
    '「Alas 设置」任务本身 —— 打开后能直接编辑 ALAS 的配置文件。': 'The "Alas settings" task itself opens the ALAS config files for direct editing.',
    '已开启': 'On', '已隐藏': 'Hidden',
    '个开关': ' switches',
    '开关名称，例如 反和谐': 'Switch name, e.g. Anti-censorship',
    '修改仅在点击保存后生效': 'Changes apply only after saving',
    '退出浏览器后自动检测': 'Check after the browser closes',
    '只在你关闭浏览器后检测一次：ALAS 已停止或异常时自动重启，不会持续检测': 'Runs once after you close the browser: restarts ALAS only when it stopped or is unhealthy, never in a loop',
    '退出浏览器后自动检测 ALAS': 'Check ALAS after the browser closes',
    '正在开启退出后检测…': 'Enabling the post-exit check…', '正在关闭退出后检测…': 'Disabling the post-exit check…',
    '退出后检测已开启 · 关闭浏览器后检测一次': 'Post-exit check enabled · runs once when the browser closes',
    '退出后检测已关闭': 'Post-exit check disabled',
    '没有可投屏的设备': 'No device is available to cast', '没有正在投屏的格子': 'No tile is casting right now',
    '已开始 {0} 台投屏，最多同时预览 {1} 路': 'Started {0} tiles; up to {1} at once',
    '已开始 {0} 台投屏，{1} 台设备离线或不可用': 'Started {0} tiles; {1} device(s) offline or unavailable',
    '需续期处理': 'Needs renewal', '账号即将到期': 'Account expiring', '台设备离线': 'devices offline',
    '长时间投屏': 'Long cast', '小时': 'h', '条待处理': 'pending', '查看全部异常': 'View all issues',
    '最近活动': 'Recent activity', '已上线': 'Online', '登录管理后台': 'Sign in to admin', '今天': 'today',
    '建立新镜像会话': 'Start new mirror session', '重新连接': 'Reconnect', '已离线': 'Offline', '快捷操作': 'Quick actions',
    // 活跃会话
    '断开全部会话': 'Disconnect all', '总会话数': 'Total sessions', '控制中会话': 'In control',
    '观看端总数': 'Total viewers', '高延迟会话': 'High latency', '最近刷新': 'Last refresh',
    '会话列表': 'Session list', '搜索、筛选并选择会话': 'Search, filter and select a session',
    '全部状态': 'All status', '观看中': 'Watching', '高延迟': 'High latency', '会话状态刷新失败': 'Failed to refresh sessions',
    '请选择会话': 'Select a session', '转交控制权': 'Transfer control', '断开该会话': 'Disconnect session',
    '关联设备': 'Linked device', '客户端类型': 'Client type', '地址': 'Address', '开始时间': 'Started',
    '会话时长': 'Duration', '延迟': 'Latency', '数据速率': 'Data rate', '控制权状态': 'Control state',
    '会话信息': 'Session info', '会话': 'Session', '浏览器': 'Browser', '客户端': 'Client',
    '加入时间': 'Joined', '最近心跳': 'Last heartbeat', '角色': 'Role', '该用户当前拥有控制权': 'This user currently holds control',
    '设备信息': 'Device info', '关联投屏设备': 'Linked casting device', '查看设备详情': 'View device details',
    '维护操作': 'Maintenance', '对选中会话执行管理操作': 'Manage the selected session',
    '刷新会话状态': 'Refresh status', '断开会话': 'Disconnect', '选择新的控制用户': 'Choose a new controller',
    '确认转交': 'Confirm transfer', '确定断开该会话': 'Disconnect this session?', '确认断开': 'Confirm disconnect',
    '确定断开全部活跃会话': 'Disconnect all active sessions?',
    '全部断开': 'Disconnect all',
    // 设备管理
    '投屏设备、连接状态与观看会话管理': 'Manage casting devices, connectivity and viewing sessions',
    '新增设备': 'Add device', '在线设备': 'Online devices', '需要处理': 'Needs attention',
    '搜索、筛选和选择设备': 'Search, filter and select a device', '禁用': 'Disabled', '连接中': 'Connecting',
    '检测失败': 'Check failed', '全部投屏': 'All casting', '设备状态刷新失败': 'Failed to refresh devices',
    '请选择设备': 'Select a device', '检测连接': 'Test connection', '停用设备': 'Disable device',
    '在线状态': 'Online state', '启用状态': 'Enabled state', '关联': 'Linked',
    '最近错误': 'Last error', '无错误': 'No error', '设备心跳超时，建议检查': 'Heartbeat timeout, check the ',
    '连接': 'connection', '该设备暂无观看会话': 'No viewing sessions for this device', '关联入口': 'Related entries',
    '快速跳转到相关管理': 'Jump to related management', '用户权限': 'User permissions',
    '查看可观看和可控制用户': 'View users with viewing/control access', '当前未绑定': 'Not bound',
    '配置': 'Config', '设备日志': 'Device logs', '连接、投屏和控制事件': 'Connection, casting and control events',
    '对选中设备执行低频操作': 'Run low-frequency operations', '重新读取设备信息': 'Re-read device info',
    '释放当前控制权': 'Release control', '停止当前投屏会话': 'Stop current cast', '删除设备': 'Delete device',
    '设备名称、ADB': 'Device name, ADB', '地址和启用状态': 'address and enabled state',
    '设备显示名称': 'Display name', '示例：adb-host:5555': 'e.g. adb-host:5555', '备注': 'Notes',
    '启用设备': 'Enable device', '停用时设备将标记为禁用，不可投屏': 'Disabled devices cannot cast',
    '保存设备': 'Save device', '确定删除该设备': 'Delete this device?', '确定停用该设备': 'Disable this device?',
    '确认停用': 'Confirm disable', '例如：设备名称': 'e.g. device name', '用途 / 位置等补充说明': 'Notes (purpose / location)',
    // 用户与权限
    '用户、角色、设备与': 'Users, roles, devices and ', '权限管理': 'permission management', '全部角色': 'All roles',
    '普通用户': 'Standard user', '即将到期': 'Expiring soon', '已到期': 'Expired', '新增用户': 'Add user',
    '用户列表': 'User list', '用户': 'User', '账户状态': 'Account state', '到期时间': 'Expiry',
    '每页': 'Per page', '姓名': 'Name', '初始密码': 'Initial password', '强制下次登录修改密码': 'Force password change on next sign-in',
    '创建用户': 'Create user', '编辑用户': 'Edit user', '管理员将获得全部设备与': 'Admins get all device and ',
    '权限': 'permissions', '保存': 'Save', '删除用户': 'Delete user', '即将删除用户': 'About to delete user',
    '删除后该用户将无法登录，此操作不可撤销': 'The user cannot sign in afterwards. This cannot be undone.',
    '确认删除': 'Confirm delete', '重置密码': 'Reset password', '新密码': 'New password',
    '强制用户下次登录修改密码': 'Force password change on next sign-in', '添加设备权限': 'Add device access',
    '确定添加': 'Confirm add', '添加': 'Add', '配置权限': 'Config permissions', '已绑定设备': 'Bound devices',
    '权限变更摘要': 'Permission change summary', '暂无待保存的变更': 'No pending changes', '变更在点击': 'Changes apply after clicking ',
    '后生效': '', '如 username': 'e.g. username', '显示名称': 'Display name', '至少 6 位': 'At least 6 characters',
    '搜索用户名或姓名': 'Search by username or name',
    // ALAS 管理
    '管理配置归属、设备关联与运行状态': 'Manage config ownership, device binding and runtime status',
    '连接设置': 'Connection settings', '未检查': 'Not checked', '服务状态': 'Service state', '已启用': 'Enabled',
    '服务端未配置 ALAS Token 加密密钥，保存 Token 会被拒绝；请注入 ALAS_TOKEN_ENCRYPTION_KEY 或运行 python -m app.cli generate-alas-key': 'The server has no ALAS token encryption key, so saving a token will be rejected. Inject ALAS_TOKEN_ENCRYPTION_KEY or run python -m app.cli generate-alas-key.',
    '开关目录为空': 'The switch catalog is empty',
    '与其它分组同名；按任务标记可精确区分，文字回退时会一起隐藏': 'Same text as another group; the task marker keeps them apart, the text fallback hides both',
    '导航入口标签': 'Navigation entry labels', '路由标记': 'Route markers', '管理员专用任务前缀': 'Administrator-only task prefixes',
    '受限设置项': 'Restricted settings', '受限设置任务': 'Restricted settings task',
    '默认值': 'Default',
    '有未保存的修改': 'Unsaved changes', '未修改': 'Unchanged',
    '配置目录': 'Config directory', '个配置': 'configs', '个归属冲突': 'ownership conflicts', '最近同步': 'Last sync', '发现 ': 'Found ', '条配置归属冲突': 'config ownership conflicts', '工作台显示': 'Workbench visible', '工作台已隐藏': 'Workbench hidden', '请选择用户': 'Select a user', '暂无可显示的用户': 'No user data available', '暂无配置关联': 'No config associations', '账户已到期，请检查已关联的 ALAS 配置运行状态': 'The account has expired. Check the runtime status of its linked ALAS configs.',
    '从未': 'Never', '用户与归属': 'Users & ownership', '个可授权账户': 'authorizable accounts', '全部用户': 'All users',
    '已关联': 'Linked', '未关联': 'Not linked', '归属冲突': 'Ownership conflicts', '发现': 'Found',
    '条配置归属冲突': 'config conflicts', '筛选': 'Filter', '新增关联': 'Add binding',
    '存在历史归属冲突': 'Historical conflicts exist', '处理冲突': 'Resolve conflicts',
    '管理员自动获得全部': 'Admins automatically get all ', '配置权限，无需单独建立配置关联': 'config permissions, no binding needed',
    '账户到期后其': 'After expiry, its ', '配置已自动停止': 'configs stop automatically',
    '配置关联': 'Config bindings', '保存用户': 'Save user', '权限修改仅在点击保存后生效': 'Permission changes apply after saving',
    '与归属记录': 'and ownership records', '中打开': 'open in ', '所属用户': 'Owner user', '绑定设备': 'Bound device',
    '默认配置': 'Default config', '运行权限': 'Run permission', '编辑权限': 'Edit permission',
    '当前任务': 'Current task', '配置内容由': 'Config content is managed on the ', '页面管理': 'page',
    '刷新状态': 'Refresh', '启动配置': 'Start config', '地址与访问凭据': 'Address & credentials', '启用': 'Enable',
    '当前已启用': 'Currently enabled', '检查连接': 'Test connection', '最近检查：': 'Last check: ', '缺失': 'Missing',
    '更新': 'Update', '清除已保存': 'Clear saved', '保存连接设置': 'Save settings',
    '新增配置关联': 'New config binding', '用户、设备与': 'User, device and ', '关联用户': 'Linked user',
    '手动填写配置名称': 'Or type a config name', '设备未绑定': 'Device not bound',
    '允许运行配置': 'Allow running', '允许该用户在': 'Allow this user to run it on the ', '中运行此配置': '',
    '允许编辑配置': 'Allow editing', '允许该用户修改配置内容': 'Allow the user to edit config content',
    '设为默认配置': 'Set as default', '设置为该用户的默认': 'Set as this user\'s default ', '配置更新时间': 'Config update time',
    '保存配置关联': 'Save binding',
    '确认操作': 'Confirm action', '确认': 'Confirm', '配置内容由 ALAS': 'Config content is managed on the ALAS ',
    // 画质与传输
    '画质预设': 'Quality presets', '点击即应用': 'Click to apply', '内置预设': 'Built-in presets',
    '流畅': 'Smooth', '均衡': 'Balanced', '低延迟': 'Low latency', '自定义预设': 'Custom presets',
    '应急兼容': 'Emergency compatibility', '普通用户默认预设': 'Default preset for standard users',
    '用户未保存预设或保存的预设失效时自动使用；仅显示启用且允许普通投屏的预设': 'Used when a user has no saved preset or it is unavailable; only enabled ordinary-projection presets are listed',
    '应急兼容仅在所有普通预设失效时自动使用，且不可修改。': 'Emergency compatibility is used only when every ordinary preset fails and cannot be edited.',
    '当前没有可用普通预设，将自动使用只读应急兼容配置。': 'No ordinary preset is available; the read-only emergency compatibility configuration will be used.',
    '应急兼容预设仅供系统自动回退使用，不可修改或主动选择': 'The emergency compatibility preset is an immutable system fallback and cannot be selected or edited',
    '新增': 'Add', '暂无自定义预设': 'No custom presets', '新增自定义预设': 'New custom preset',
    '全屏默认画质': 'Default fullscreen quality', '进入全屏观看时的默认画质档位': 'Quality used when entering fullscreen',
    '参数配置': 'Parameter settings', '应用预设：均衡': 'Applied preset: Balanced', '基础画质': 'Base quality',
    '输出尺寸': 'Output size', '决定画面像素尺寸，更高分辨率消耗更多带宽': 'Higher resolution uses more bandwidth',
    '自定义': 'Custom', '自定义宽度和高度': 'Custom width & height', '宽度需在': 'Width must be ',
    '高度需在': 'Height must be ', '之间': '', '帧率': 'FPS', '每秒画面刷新次数，越高越流畅但更耗资源': 'Higher FPS is smoother but heavier',
    '帧率需在': 'FPS must be ', '码率': 'Bitrate', '视频编码比特率，越高画质越好占用带宽越大': 'Higher bitrate, better quality, more bandwidth',
    '码率需在': 'Bitrate must be ', '传输模式': 'Transfer mode', '默认传输模式': 'Default transfer mode',
    '默认使用': 'Uses ', '切换将中断当前会话并需手动重启': 'Switching interrupts the current session',
    '始终可用）': 'always available)', '编码状态': 'Encoder', '硬件': 'Hardware',
    '编码由服务端固定启用，当前版本不支持在此修改': 'Fixed on the server, cannot be changed in this version',
    '只读': 'Read-only', '会话时限': 'Session limits', '无观看端自动停止时间': 'Auto-stop with no viewers',
    '最后一个观看端离开后，超过该时长自动停止投屏': 'Stop casting this long after the last viewer leaves',
    '0 表示仅保留 5 秒重连宽限': '0 keeps only a 5 s reconnect grace',
    '0 为仅保留 5 秒重连宽限': '0 keeps only a 5 s reconnect grace',
    '仅 5 秒重连宽限': '5 s reconnect grace',
    '分钟需在': 'Must be between ', '秒需在': 'Must be between ', '连续投屏时间上限': 'Max cast duration',
    '单次投屏会话允许的最长持续时间，到达后自动结束': 'Longest allowed duration for one session',
    '小时需在': 'Must be between ', '默认': 'Default', '原生传输协议，显著降低编码延迟': 'Native protocol, much lower latency',
    '关闭后默认传输模式将不再可选': 'Not selectable after disabling', '备用': 'Fallback',
    '由管理员手动启用，启用后可切换使用': 'Manually enabled by the administrator', '已停用': 'Disabled',
    '启用后可在默认传输模式中选择': 'Selectable after enabling', '兼容旧版传输，由管理员手动启用': 'Legacy protocol, manually enabled',
    '传输状态': 'Transfer status', '客户端详情': 'Client details', '刷新': 'Refresh',
    '当前健康状态': 'Health', '传输中': 'Transferring', '连接正常': 'Connected',
    '当前传输模式': 'Current mode', '默认协议': 'Default protocol', '最近关键帧时间': 'Last keyframe',
    '关键帧间隔': 'Keyframe interval', '连续运行时间': 'Uptime', '自本次会话开始': 'Since this session',
    '详细诊断': 'Diagnostics', '配置版本': 'Config version', '自上次保存': 'Since last save',
    '帧序号': 'Frame seq', '当前接收帧计数': 'Received frame count', '解析失败': 'Parse failures',
    '本轮会话累计': 'This session', '解析器丢弃': 'Parser drops', '解析器主动丢弃': 'Actively dropped',
    '重同步次数': 'Resyncs', '解析器重同步累计': 'Total parser resyncs', '缓存峰值': 'Peak buffer',
    '接收缓冲最大深度': 'Max receive buffer depth', '客户端数量': 'Client count', '当前观看端': 'Current viewers',
    '队列帧数': 'Queued frames', '当前': 'Current', '上限': 'limit', '队列字节数': 'Queued bytes',
    '丢帧数量': 'Dropped frames', '最近': 'Recent', '自上次重启后': 'Since last restart', '恢复状态': 'Recovery',
    '无异常需要恢复': 'Nothing to recover', '指标约': 'Metrics refresh about every ', '自动更新': 'auto-update',
    '待重启': 'Restart needed', '传输模式已变更，当前会话需要手动重启传输才能生效': 'Transfer mode changed, restart the transfer to apply',
    '立即重启': 'Restart now', '稍后手动处理': 'Later', '当前生效配置': 'Active config',
    // 画质页底部：原「当前生效」改叫「默认画质预设」（值仍是当前生效的那套画质）。
    '默认画质预设': 'Default quality preset', '当前配置': 'Current config',
    '待保存变更摘要': 'Pending changes', '暂无未保存的变更': 'No unsaved changes', '更多': 'More',
    '重置为默认设置': 'Reset to defaults', '保存设置': 'Save settings', '预设名称': 'Preset name',
    '请输入预设名称': 'Enter a preset name', '输出分辨率': 'Output resolution', '宽度': 'Width', '高度': 'Height',
    '请选择或输入有效的分辨率': 'Choose or enter a valid resolution', '创建预设': 'Create preset',
    '编辑自定义预设': 'Edit preset', '保存修改': 'Save changes', '删除自定义预设': 'Delete preset',
    '删除后无法恢复，请确认是否继续': 'Cannot be undone, continue?', '切换传输模式': 'Switch transfer mode',
    '切换传输模式将中断当前投屏会话并重新建立连接': 'Switching interrupts the current session',
    '确认后，系统将应用新的传输模式并提示手动重启传输': 'The new mode will apply and ask to restart the transfer',
    '确认切换': 'Confirm switch', '将恢复内置': 'This restores built-in ', '预设（1920': 'presets (1920',
    '为当前配置与保存基线，': 'as the baseline. ', '未保存的修改将被丢弃': 'Unsaved changes will be lost',
    '此操作会覆盖当前表单中的全部参数': 'This overwrites all current form parameters',
    '确认重置': 'Confirm reset', '客户端队列与丢帧详情': 'Client queue & drop details',
    '当前会话各观看端的连接、队列与丢帧情况，约': 'Per-viewer connection, queue and drops, about ',
    '异常客户端恢复': 'Abnormal client recovery', '关闭': 'Close',
    // 日志审计
    '运行日志与安全审计事件记录': 'Runtime logs and security audit events', '当前筛选结果': 'Filtered results',
    '运行日志': 'Runtime logs', '全部记录': 'All records', '审计': 'Audit', '审计异常数量': 'Audit anomalies',
    '失败或拒绝': 'Failed or denied', '建议关注': 'Watch out', '运行日志与安全审计': 'Runtime logs & security audit',
    '实时记录': 'Live records', '自动刷新': 'Auto refresh', '最后更新': 'Last update', '检查完整性': 'Check integrity',
    '导出当前视图': 'Export view', '安全审计': 'Security audit', '时间范围': 'Time range', '全部时间': 'All time',
    '严重级别': 'Severity', '调试': 'Debug', '信息': 'Info', '警告': 'Warning', '错误': 'Error',
    '来源模块': 'Module', '认证': 'Auth', '画质传输': 'Quality transfer', '全部设备': 'All devices',
    '无设备': 'No devices', '无用户': 'No users', '事件类型': 'Event type', '全部事件类型': 'All event types',
    '登录与退出审计': 'Sign-in/out', '认证失败': 'Auth failures', '账户状态变更': 'Account state changes',
    '用户权限调整': 'Permission changes', '设备操作': 'Device actions', '画质与传输配置修改': 'Quality config changes',
    '配置与运行操作': 'Config & runtime', '管理员操作': 'Admin actions', '执行结果': 'Result',
    '成功': 'Success', '失败': 'Failed', '拒绝': 'Denied', '操作者': 'Actor', '全部操作者': 'All actors',
    '未认证用户': 'Unauthenticated user', '刷新失败': 'Refresh failed', '无法获取最新日志，请重试': 'Could not fetch logs, retry',
    '日志服务不可用': 'Log service unavailable', '数据可能延迟，恢复后自动同步': 'Data may be delayed, syncs automatically',
    '导出失败': 'Export failed', '无法生成导出文件，请稍后重试': 'Cannot generate export, retry later',
    '当前账号无导出权限': 'No export permission', '导出请求已被拒绝': 'Export denied',
    '时间': 'Time', '级别': 'Level', '来源': 'Source', '内容摘要': 'Summary', '无匹配日志': 'No matching logs',
    '清除筛选': 'Clear filters', '结构化视图': 'Structured view', '原始日志视图': 'Raw view',
    '条失败或拒绝事件': 'failed/denied events', '操作对象': 'Target', '自动换行': 'Word wrap',
    '无匹配审计记录': 'No matching audit records', '日志完整性检查': 'Log integrity check',
    '校验日志文件是否被篡改或缺失，与安全事件失败相互独立': 'Verifies log files are not tampered with or missing',
    '检查状态': 'Check state', '已完成': 'Done', '最近检查时间': 'Last checked', '检查范围': 'Scope',
    '天日志': '-day logs', '检查结果': 'Result', '通过': 'Passed', '已验证记录': 'Verified records',
    '检查失败记录': 'Failed records', '异常记录详情': 'Anomaly details', '显示': 'Show', '加载更多': 'Load more',
    '日志详情': 'Log details', '完整日志内容': 'Full log content', '日志上下文': 'Log context',
    '事件': 'Event', '原始内容仅供参考与排障': 'Raw content for reference & debugging',
    '复制原始内容': 'Copy raw content', '安全审计记录详情': 'Audit record details', '审计详情': 'Audit details',
    '原始审计内容': 'Raw audit content', '完整性异常记录': 'Integrity anomaly records',
    '以下为完整性检查覆盖范围内发现的失败或拒绝审计事件': 'Failed/denied events found within the integrity scope',
    '当前无完整性异常记录': 'No integrity anomalies', '将导出当前筛选视图中的记录': 'Exports the records in the current view',
    '文件': 'File', '原始文本': 'Raw text', '小时发现': 'hours found', '自动换行已开启': 'Word wrap on',
    // 系统设置
    '有未保存的变更': 'You have unsaved changes', '放弃变更': 'Discard changes', '基本信息': 'Basic info',
    '系统标识与服务器信息': 'Identity & server info', '系统名称': 'System name',
    '系统版本与构建信息': 'Version & build info', '当前运行版本、构建号与最新版本状态': 'Current version, build number and update status',
    '已是最新版本': 'Up to date',
    '服务器时间与时区': 'Server time & timezone', '与系统时钟自动同步': 'Auto-synced with the system clock',
    '同步': 'Sync', '默认界面语言': 'Default language', '新用户首次登录时使用的界面语言': 'Language used on first sign-in',
    '默认主题模式': 'Default theme', '新用户首次登录时使用的主题外观': 'Theme used on first sign-in',
    '会话与到期策略': 'Session & expiry policy', '登录会话与账号生命周期': 'Login session and account lifecycle',
    '登录会话有效期': 'Session validity', '管理端登录会话保持有效的时间，到期后需重新登录': 'How long an admin session stays valid',
    '用户到期提醒提前天数': 'Expiry reminder lead', '账号到期前多少天开始提醒用户续期，0': 'Days before expiry to remind. 0 ',
    '表示不提醒': 'means no reminder', '用户到期后自动停止': 'Auto-stop after expiry',
    '自动化任务': 'automation tasks', '开启': 'On',
    '账户到期策略': 'Account expiry policy', '即将到期的判定与到期后的自动处理': 'Expiring window and post-expiry handling',
    '到期提醒提前天数': 'Expiry reminder lead', '列表与仪表盘在这段时间内标记为「即将到期」，0 表示不提前提醒。':
      'Rows and the dashboard show "expiring soon" within this window; 0 disables the reminder.',
    '账户到期后自动停止其 ALAS 配置': 'Stop the account\'s ALAS configs on expiry',
    '开启后：账户到期（会话与控制锁被回收）时，其仍能运行的 ALAS 配置会被自动停止。':
      'When on, the account\'s running ALAS configs are stopped as soon as its sessions and control locks are revoked.',
    '保存策略': 'Save policy', '账户到期策略已保存': 'Expiry policy saved',
    '到期提醒提前天数必须是 0-90 的整数': 'Reminder lead must be an integer between 0 and 90',
    '账户即将到期': 'Account expiring soon', '账户已到期': 'Account expired',
    '账户已到期：投屏与 ALAS 已停用，请联系管理员续期': 'Account expired: casting and ALAS are disabled. Ask an administrator to renew it.',
    '知道了（今天不再提醒）': 'Got it (hide today)',
    '连续投屏自动停止时长': 'Max cast duration', '单会话连续投屏超过设定时长后自动停止，0': 'Stop casts longer than this. 0 ',
    '表示不限制': 'means unlimited', '运行状态': 'Runtime status', '服务与资源健康度': 'Service & resource health',
    '服务运行状态': 'Service state', '已运行': 'Running', '数据库状态': 'Database', '磁盘空间使用情况': 'Disk usage',
    '已用': 'used', '日志目录占用空间': 'Log directory size', '约占磁盘': 'about ', '保留': 'Keep',
    '清理': 'Clean up', '清理、诊断与服务控制': 'Cleanup, diagnostics & service control', '清理过期会话': 'Clean expired sessions',
    '立即清理': 'Clean now',
    '清理历史运行日志': 'Clean old logs', '删除': 'Delete', '天前的日志记录以释放空间': 'days of logs to free space',
    '清理无效临时文件': 'Clean temp files', '移除中断会话残留的临时缓存文件': 'Remove leftover temp cache files',
    '系统健康检查': 'Health check', '检查服务、数据库、磁盘与网络连通性': 'Check service, database, disk and network',
    '立即执行': 'Run now', '重新加载系统配置': 'Reload system config', '无需重启服务，热重载已保存的配置': 'Hot-reload saved config, no restart needed',
    '重新加载': 'Reload', '重启': 'Restart', '重启核心服务，当前投屏会话将被中断': 'Restarts core services, current casts will be interrupted',
    '重启服务': 'Restart service', '导出系统配置': 'Export system config', '将当前全部设置导出为备份文件': 'Export all settings as a backup',
    '导出配置': 'Export config', '导入系统配置': 'Import system config', '从备份文件恢复系统设置': 'Restore settings from a backup',
    '导入配置': 'Import config', '恢复默认设置': 'Restore defaults', '将所有参数重置为出厂默认值': 'Reset all parameters to factory defaults',
    '恢复默认': 'Restore defaults', '已保存': 'Saved', '无未保存变更': 'No unsaved changes', '上次保存于': 'Last saved ',
    '等待服务端配置': 'Waiting for server config', '设置配置接口未配置': 'Settings config API is not configured',
    '设置已刷新': 'Settings refreshed',
    '当前没有需要保存的变更': 'No changes to save', '设置已保存并生效': 'Settings saved and applied',
    '保存接口未配置': 'Save API is not configured', '已放弃未保存的变更': 'Unsaved changes discarded',
    '需关注': 'Needs attention', '待配置': 'Not configured', '运行时长': 'Uptime',
    '健康检查接口未配置': 'Health check API is not configured', '健康检查完成': 'Health check completed',
    '操作请求已提交': 'Action request submitted', '服务重启请求已提交': 'Restart request submitted',
    '配置导出已就绪': 'Config export is ready', '已读取配置文件，可提交导入': 'Config file loaded, ready to import',
    '配置文件格式无效': 'Invalid config file format', '配置导入已提交并应用': 'Config import submitted and applied',
    '该功能暂不可用': 'This feature is not available',
    '权限申请已提交': 'Permission request submitted', '续期申请已提交': 'Renewal request submitted',
    '观看时长已延长': 'Viewing time extended', '请先开始观看会话': 'Start a viewing session first',
    '处理中…': 'Processing...', '检查中…': 'Checking...', '同步中…': 'Syncing...', '刷新中…': 'Refreshing...', '保存中…': 'Saving...',
    'ALAS 服务未启用': 'ALAS service is not enabled', 'ALAS 数据已刷新': 'ALAS data refreshed',
    '配置关联已保存': 'Config binding saved', '停止配置': 'Stop config', '重启异常配置': 'Restart failed config',
    '配置已停止': 'Config stopped', '配置已重启': 'Config restarted', '配置已启动': 'Config started',
    '配置归属已更新': 'Config ownership updated', '关联已删除': 'Binding deleted',
    '配置目录已同步': 'Config directory synced', 'Runtime 配置目录为空': 'Runtime config directory is empty',
    'ALAS 服务已启用': 'ALAS service enabled', 'ALAS 服务已停用': 'ALAS service disabled',
    '连接检查成功': 'Connection test succeeded', 'Token 缺失，无法连接': 'Token missing; cannot connect',
    '连接超时': 'Connection timed out', '服务不可达': 'Service unreachable',
    // 工作台 ALAS 状态与动作：底部工具栏按钮的 title/aria-label 也走精确匹配，
    // 之前这些状态词只在中文里出现，英文界面会漏出中文。
    '已停止': 'Stopped', '检查中': 'Checking', '已禁用': 'Disabled', '已断开': 'Disconnected', '不可达': 'Unreachable', '未绑定': 'Not bound',
    '启动 ALAS': 'Start ALAS', '停止 ALAS': 'Stop ALAS', '重启 ALAS': 'Restart ALAS', 'ALAS 未绑定': 'No ALAS config bound',
    // 全屏体验：画质切换、菜单收纳与方向自动复位
    '全屏画质已提升': 'Fullscreen quality boosted',
    '有其他观看端，全屏画质将在你独享时切换': 'Other viewers are watching; fullscreen quality switches when you are alone',
    '后台未配置可用的全屏画质，保持当前设置': 'No fullscreen quality preset is configured; keeping the current settings',
    '已退出全屏 · 恢复原画质': 'Left fullscreen · original quality restored',
    '全屏画质切换失败': 'Fullscreen quality switch failed',
    '展开菜单': 'Show menu', '收起菜单': 'Hide menu', '菜单': 'Menu', '收起': 'Hide', '画面已旋转': 'View rotated',
    '展开控制栏': 'Show controls', '收起控制栏': 'Hide controls',
    '展开控制栏并获取控制': 'Show controls and take control',
    // 画质预设：点卡片只选中预览，点「应用」才写入表单
    '应用': 'Apply', '已应用': 'Applied', '已选中：': 'Selected: ',
    '工作台可选': 'Available in workbench', '自定义': 'Custom',
    '相对预设：': 'Relative to preset: ', '待应用预设：': 'Preset to apply: ',
    '开启后普通用户可在工作台「画面」面板中选择该预设': 'When on, this preset appears in the workbench picture panel',
    '点「应用」把该预设写入下方表单，保存后生效': 'Press Apply to load this preset into the form; save to make it effective',
    '应用预设：': 'Preset in use: ',
    '连接设置已保存': 'Connection settings saved', '请输入 API Token': 'Enter an API token',
    '清除 API Token': 'Clear API token', 'API Token 已清除': 'API token cleared',
    '删除配置关联': 'Delete config binding', '已更新运行权限，点击“保存用户 ALAS 权限”生效': 'Run permission updated. Click "Save user ALAS permissions" to apply',
    '已更新编辑权限，点击“保存用户 ALAS 权限”生效': 'Edit permission updated. Click "Save user ALAS permissions" to apply',
    '请先在左侧选择用户': 'Select a user on the left first', '管理员无需单独建立配置关联': 'Admins do not need separate config bindings',
    '请先选择用户': 'Select a user first', '用户 ALAS 权限已保存': 'User ALAS permissions saved',
    '状态已刷新': 'Status refreshed',
    '已筛选归属冲突相关的用户': 'Filtered users related to ownership conflicts',
    '变更摘要': 'Change summary', '此操作不可撤销，确定继续': 'This cannot be undone, continue?', '确认执行': 'Confirm',
    '保存前变更摘要': 'Changes summary', '点击': 'Click ', '后变更将立即生效': ' to apply immediately', '返回修改': 'Back',
    '选择或拖入配置文件': 'Choose or drag in a config file', '支持': 'Supports ', '备份文件': 'backup files',
    '最大': 'Max', '导入前将自动备份当前配置': 'Current config is backed up automatically', '导入会覆盖现有全部设置': 'Import overwrites all current settings',
    '导入并应用': 'Import & apply',
    // 状态/数值词
    '和': 'and', '天': 'days', '台': 'units', '位': 'digits', '共': '',
    '已': '', '路': '', '项': '', // 「个 / 条 / 次 / 秒」等单字词条见 EXACT_EN（只整段匹配）
    // 属性专用
    '页面导航': 'Page navigation', '切换主题': 'Toggle theme', '通知': 'Notifications', '通知中心': 'Notification center', '全部已读': 'Mark all as read', '暂无通知': 'No notifications', '新的通知将在出现时显示': 'New notifications will appear here', '通知接口未配置': 'Notification service is not configured', '连接数据服务后将显示通知': 'Connect the data service to show notifications', '系统事件': 'System event', '未知': 'Unknown', '主导航': 'Main navigation',
    '收起侧边栏': 'Collapse sidebar', '展开侧边栏': 'Expand sidebar', '工作区导航': 'Workspace navigation',
    '账户菜单': 'Account menu', '管理导航': 'Admin navigation', '设备管理面板': 'Device panel',
    '资源导航': 'Resources navigation', '系统导航': 'System navigation', '面包屑': 'Breadcrumb',
    '连接状态': 'Connection state', '连接状态说明': 'Connection state description', '设备状态': 'Device state',
    '设备观看画面': 'Device screen', '关闭提醒': 'Dismiss', '观看控制': 'Watch controls',
    '该设备正被其他用户控制': 'This device is controlled by another user', '更多操作': 'More actions',
    'ScrcpyGate 管理控制台': 'ScrcpyGate Admin Console', '2 个账号即将到期': '2 accounts expiring soon',
    '切换到浅色模式': 'Switch to light mode', '切换到深色模式': 'Switch to dark mode', '核心统计': 'Core stats',
    '投屏会话，跳转投屏工作区': 'Casting sessions, go to workspace',
    '待处理事项，跳转告警与到期记录': 'Pending items, go to alerts',
    '搜索': 'Search', '搜索设备': 'Search devices', '按状态筛选设备': 'Filter by status',
    '配置：日常委托': 'Config: Daily commission', '打开设备详情': 'Open device details', '设备已离线，无法断开': 'Device offline, cannot disconnect',
    '配置：活动刷图': 'Config: Event farming', '配置：主线推图': 'Config: Story clear', '未绑定 ALAS 配置': 'No ALAS config bound',
    '前往设备列表处理': 'Go to devices to handle', '前往投屏工作区': 'Go to casting workspace',
    '查看设备详情': 'View device details', '查看用户记录': 'View user record',
    '查看投屏会话': 'View cast session', '查看离线原因': 'View offline reason',
    '测试设备心跳': 'Test device heartbeat', '刷新全部会话': 'Refresh all sessions', '会话状态摘要': 'Session summary',
    '会话列表与详情': 'Session list & details', '刷新全部会话状态': 'Refresh all session states',
    '搜索会话': 'Search sessions',
    '按状态筛选会话': 'Filter sessions by status', '关闭抽屉': 'Close drawer', '关闭弹窗': 'Close dialog',
    '刷新全部设备': 'Refresh all devices', '设备状态摘要': 'Device summary', '设备列表与详情': 'Device list & details',
    '刷新全部设备状态': 'Refresh all device states', '搜索设备名或 ADB 地址': 'Search name or ADB address',
    '按投屏状态筛选设备': 'Filter by casting state', '新增或编辑设备': 'Add or edit device', '用户筛选工具栏': 'User filter toolbar',
    '按角色筛选': 'Filter by role', '按账户状态筛选': 'Filter by account state', '分页': 'Pagination',
    'ALAS 状态摘要': 'ALAS summary', 'ALAS 管理视图': 'ALAS management view', '搜索用户名': 'Search username',
    '关联状态': 'Linked state', '刷新目录': 'Refresh directory', '搜索配置名称': 'Search config name',
    'ALAS 连接设置': 'ALAS connection settings', '关闭连接设置': 'Close connection settings', '留空则保持当前 Token': 'Leave empty to keep the current token',
    '显示或隐藏 Token': 'Show or hide token', '配置关联': 'Config bindings',
    '关闭配置关联': 'Close bindings', '暂未进入 Runtime 的配置名称': 'Config names not yet in the runtime',
    '无观看端自动停止时间单位': 'Auto-stop time unit', '连续投屏时间上限单位': 'Max duration unit', 'Raw v2 传输模式': 'Raw v2 transfer mode',
    'protocol 备用模式': 'protocol fallback mode', 'legacy 备用模式': 'legacy fallback mode', '如：日常流畅': 'e.g. Daily smooth',
    '日志审计统计': 'Log audit stats', '检查日志完整性': 'Check log integrity', '手动刷新': 'Manual refresh',
    '导出当前筛选视图': 'Export current view', '日志视图切换': 'Log view switch', '搜索关键词': 'Search keyword',
    '搜索日志关键词': 'Search log keyword', '自定义开始时间': 'Custom start time', '自定义结束时间': 'Custom end time',
    '设备': 'Device', '用户': 'User', '搜索审计关键词': 'Search audit keyword', '审计时间范围': 'Audit time range',
    '筛选 IP 地址': 'Filter IP address', '筛选操作对象': 'Filter target', '关闭提示': 'Dismiss', '审计视图模式': 'Audit view mode',
    '上一条': 'Previous', '下一条': 'Next', '到期后自动停止 ALAS': 'Auto-stop ALAS after expiry',
    '到期提醒提前天数': 'Expiry reminder lead', '连续投屏自动停止时长': 'Max cast duration', '登录会话有效期': 'Session validity',
    // 其他高频
    '已复制': 'Copied', '复制失败': 'Copy failed', '已保存设置': 'Settings saved', '保存失败': 'Save failed',
    '无更多数据': 'No more data', '暂无数据': 'No data', '加载中': 'Loading', '已清除': 'Cleared',
    '设置已应用': 'Settings applied', '需要重启生效': 'Restart required', '未找到结果': 'No results found',
    '全部设备': 'All devices', '无设备': 'No devices', '无用户': 'No users',
    // 投屏管理（/mirror-admin）：按角色开关工作台菜单与控件
    '投屏管理': 'Casting management', '菜单与控件': 'Menus & controls',
    // 已失效的近义旧词条（页面文案早已改写）一律删除：它们和真文案只差一两个字，
    // 留着会让精确匹配静默失败，英文界面退化成逐词替换。
    '正在加载开关…': 'Loading switches…', '没有可配置的功能项': 'No configurable features',
    '数据服务不可用，请刷新页面': 'Data service unavailable — refresh the page',
    '加载失败': 'Load failed', '未知错误': 'Unknown error', '全部开启': 'All enabled',
    '已保存并立即生效': 'Saved and applied immediately', '已恢复默认（全部开启）': 'Defaults restored (all enabled)',
    '底部控制栏': 'Bottom control bar',
    // 投屏管理（逐项）：状态条内容 + 控制栏按钮 + 预览示例值
    '顶部状态条': 'Top status bar', '按角色配置': 'Configure per role',
    '按角色控制投屏画面：顶部状态条的内容、底部控制栏的按钮与全屏下的画面让位行为': 'Control the casting screen per role: what the top status bar shows, which bottom-bar buttons appear, and how the screen makes room in fullscreen',
    '工作台上方的设备状态条与通知': 'Device status bar and notifications above the workspace',
    '工作台画面下方的按钮': 'Buttons below the casting screen',
    '设备分组的小标题': 'Small caption for the device group',
    '当前设备的名称': 'Name of the selected device',
    '在线圆点与连接状态文字': 'Online dot and connection state text',
    '「观看端」小标题': 'The Viewers caption',
    '当前观看端数量': 'Number of viewers right now',
    '本次投屏的剩余时长（仅在有上限时出现）': 'Remaining time for this cast (only when a limit applies)',
    '「ALAS」小标题': 'The ALAS caption',
    '「画质」小标题': 'The Quality caption',
    '分辨率、帧率与码率摘要': 'Resolution, frame rate and bitrate summary',
    // 控制权块（替代「剩余时长」）：显示谁在控制，以及实测帧率。
    '当前持有控制权的用户（我 / 某个用户名 / 空闲）': 'Who currently holds control (me / a username / free)',
    '我 · 控制中': 'Me · controlling', '空闲（可获取）': 'Free (can acquire)',
    '实测帧率（客户端每秒收到的帧数）': 'Measured frame rate (frames received per second)',
    '投屏已停止，但这次投屏记录仍在进行；重新开始投屏后会自动继续':
      'Casting stopped, but the record session is still running; it resumes when casting restarts',
    '同一台设备': 'Same device', '个会话': 'sessions', '踢出该设备': 'Revoke this device',
    '设备标识': 'Device ID', '来源 IP': 'Source IPs',
    '历史记录': 'History', '还没有历史记录。结束一次记录后，它会出现在这里。':
      'No history yet. Records show up here after a session ends.',
    '观看端标题': 'Viewers caption', '观看人数': 'Viewer count', '剩余时长': 'Remaining time',
    'ALAS 标题': 'ALAS caption', 'ALAS 运行状态': 'ALAS state', '画质标题': 'Quality caption',
    '画质名称': 'Quality preset', '画质详情': 'Quality details',
    '都会显示': 'Everything is shown', '都不会显示': 'Nothing is shown',
    '剩余 45:00': 'Remaining 45:00', '返回 主页 多任务': 'Back Home Recents',
    '键盘': 'Keyboard', '旋转': 'Rotate',
    // 投屏管理：功能块开关 + 底部菜单一级/二级编排
    '按工作台里的分组控制：每一块可以单独关闭，关闭后整块（含分隔线）都不显示。': 'Grouped the same way as the workspace: each block can be turned off on its own, and the whole block (with its divider) disappears.',
    '一级菜单显示在画面下方，二级菜单收在「更多」里；拖进对应块即启用，并可调整顺序。': 'Level 1 sits under the screen, level 2 lives inside “More”; drop a feature into a block to enable it, and drag to reorder.',
    '一级菜单': 'Level 1', '二级菜单': 'Level 2', '编排菜单': 'Arrange menu', '编排底部菜单': 'Arrange bottom menu',
    '画面下方的一排按钮': 'The row of buttons below the screen', '收在「更多」菜单里': 'Grouped inside the “More” menu',
    '未启用': 'Not enabled', '拖到这里即停用（工作台不显示）': 'Drop here to disable (hidden in the workspace)',
    '一级菜单（控制栏）': 'Level 1 (control bar)', '二级菜单（「更多」）': 'Level 2 (“More” menu)',
    '显示在画面下方的一排按钮': 'Shown as the row of buttons under the screen', '收在「更多」弹出菜单里': 'Grouped inside the “More” pop-up menu',
    '把功能拖到一级或二级即启用，拖回左侧即停用；也可以用每项后面的按钮。': 'Drag a feature into level 1 or 2 to enable it, or back to the left to disable it — the buttons on each chip do the same.',
    '当前角色：': 'Current role: ', '改动随「保存」一起生效': 'Changes apply when you press Save',
    '移到一级': 'Move to level 1', '移到二级': 'Move to level 2', '停用': 'Disable', '上移': 'Move up', '下移': 'Move down',
    '放到一级': 'Move to level 1', '放到二级': 'Move to level 2', '启用到': 'Enable in', '所在层级': 'Level',
    '二级容器': 'Level 2 container', '位置锚点': 'Position anchor', '拖动排序：': 'Drag to reorder: ',
    '全部功能都已启用': 'Every feature is enabled', '拖功能到这里': 'Drop features here', '暂无功能': 'No features yet',
    '都不显示': 'Nothing is shown', '二级菜单为空时，工作台不会显示「更多」按钮。': 'When level 2 is empty the workspace hides the “More” button.',
    '设备名称': 'Device name', '设备状态条': 'Device status bar', '截图': 'Screenshot', '备用键盘': 'Backup keyboard',
    '更多菜单': 'More menu', 'ALAS 入口': 'ALAS entry',
    '设备名称、在线圆点与连接状态': 'Device name, online dot and connection state',
    '观看端小标题与当前人数': 'The Viewers caption and the current count',
    '本次投屏的剩余时长（仅在有上限时出现）': 'Remaining time for this cast (only when a limit applies)',
    'ALAS 小标题、圆点与运行状态（仅绑定后出现）': 'ALAS caption, dot and state (only when bound)',
    '画质小标题、档位名称与分辨率/码率摘要': 'Quality caption, preset name and resolution/bitrate summary',
    '整条状态条的总开关，关闭后下面各块都不显示': 'Master switch for the whole bar — turning it off hides every block below',
    '开始 / 停止观看实时画面': 'Start or stop watching the live screen',
    '申请或释放设备控制权': 'Request or release device control',
    '进入全屏并自动横竖屏（按钮上带「窗口 / 全屏」小字）': 'Enter fullscreen with automatic orientation (the button shows a Window/Fullscreen hint)',
    '手动旋转显示方向': 'Rotate the display manually',
    '截取当前画面（二级菜单里带「复制」小字）': 'Capture the current frame (the menu entry shows a Copy hint)',
    '二级菜单的容器；二级里有功能时才会出现': 'Container for level 2; it only appears when level 2 has entries',
    '位置锚点，显隐由「ALAS 管理 → 工作台显示」控制': 'Position anchor — visibility is controlled by ALAS → Show in workspace',
    '工作台上方的设备状态条与通知': 'Device status bar and notifications above the workspace',
    '开始投屏': 'Start casting', '导航键': 'Navigation keys', '全屏显示': 'Fullscreen', '设备状态条': 'Device status bar',
    '开始 / 停止观看实时画面': 'Start or stop watching the live screen',
    '申请或释放设备控制权': 'Request or release device control',
    '把电脑键盘输入发送到设备': 'Send this keyboard to the device',
    '进入全屏并自动横竖屏': 'Enter fullscreen with automatic orientation',
    '手动旋转显示方向': 'Rotate the display manually',
    '顶部通知铃铛与消息面板': 'Notification bell and message panel',
    // 投屏管理（逐字对齐）：页面/目录/脚本三处文案只要有一处改动就必须同步这里，
    // 否则精确匹配失败会退化成逐词替换，英文界面会出现“按Role生效”这种半截混排。
    '开关只影响前端的显示与可用性，不改变任何权限判定；ALAS 入口的显示由「ALAS 管理 → 工作台显示」控制。': 'Switches only change what the workspace shows or enables — never permissions. The ALAS entry follows “Show in workspace” on the ALAS page.',
    '投屏管理 - ScrcpyGate': 'Casting management - ScrcpyGate',
    '一级显示在画面下方，二级收在「更多」菜单里': 'Level 1 sits under the screen, level 2 is folded into “More”',
    '拖到这里即停用，工作台不显示': 'Drop here to disable — the workspace hides it',
    '当前二级菜单为空，工作台不会显示「更多」按钮。': 'Level 2 is empty, so the workspace hides the “More” button.',
    '一级': 'Level 1', '二级': 'Level 2', '完成': 'Done',
    // 目录里工作台按钮上的小字示例
    '窗口': 'Window', '复制': 'Copy', '预览': 'Preview',
    '选中条目后用 ↑ / ↓ 调整顺序。': 'Select a chip, then use ↑ / ↓ to reorder.',
    '返回 / 主页 / 多任务三个按键（放到二级会变成三个菜单项）': 'Back / Home / Recents — three keys, or three menu entries at level 2',
    '把电脑键盘输入发送到设备（放到二级就是「键盘输入」菜单项）': 'Send this keyboard to the device (a “Keyboard” menu entry at level 2)',
    '键盘失灵时的补救入口（放到一级就是一个普通按钮）': 'Recovery entry when the keyboard stops responding (a plain button at level 1)',
    '顶部右侧的通知铃铛与消息面板': 'Notification bell and message panel at the top right',
    // 投屏管理：投屏行为（只有开关，不参与菜单编排）
    '投屏行为': 'Casting behaviour',
    '画面与控制栏的相处方式；只影响观感，不参与任何权限判定': 'How the screen makes room for the control bar — visual only, never permissions',
    '全屏打开控制栏时画面上移': 'Lift the screen while the control bar is open in fullscreen',
    '全屏下展开底部控制栏时画面自动上移一小段（用顶部本就空着的留白换出底部空间），收起后归位': 'In fullscreen, opening the bottom control bar lifts the screen a little to trade the free space at the top for room at the bottom; it returns when the bar closes',
    // 控制台外壳：侧栏与顶栏此前缺词条，英文界面会漏出中文
    'ALAS 管理': 'ALAS management', '登录防护': 'Login guard', '当前账户': 'Current account',
    '正在加载账户信息': 'Loading account…', '打开菜单': 'Open menu',
    // 新增的短词条（窗口/复制/完成）会参与逐词替换，这里补齐受影响的整句，
    // 避免其它页面从“整句中文”退化成“半截中英混排”。
    '拖动滑块完成验证': 'Drag the slider to verify',
    '请求被雷池网关拦截，请稍后重试或完成人机验证': 'Blocked by the gateway — retry later or complete the human check',
    '连接检测完成': 'Connection check complete', '日志完整性检查已完成': 'Log integrity check complete',
    '请求未完成': 'Request incomplete',
    '适应窗口': 'Fit window', '最大化 ALAS 窗口': 'Maximize the ALAS window', '还原 ALAS 窗口': 'Restore the ALAS window',
    '窗口失去焦点后停止观看': 'Stop watching when the window loses focus',
    '窗口失去焦点后停止观看时长': 'Stop when the window loses focus after', '窗口内': 'in window',
    '页面可见但窗口失去焦点后停止当前浏览器观看；默认关闭，避免系统弹窗等场景误停': 'Stops this browser when the page stays visible but the window loses focus; off by default to avoid accidental stops',
    '复制事件 JSON': 'Copy event JSON',

    // ---- 批次 2：节点级配对补齐的整句 / 片段（英文模式残留中文的根因）----
    '查看选中告警或活动的完整字段。': 'View every field of the selected alert or activity.',
    '最近 24 小时未发现高风险事件': 'No high-risk events in the last 24h',
    // 由页面脚本拼句的片段（tr(A) + 动态值 + tr(B)）：补全片段词条后，
    // 英文界面拼出来才是完整英文（见 ISSUE-127 的片段拼接检查）。
    '自动刷新已关闭': 'Auto refresh off',
    '等待服务端数据': 'Waiting for server data',
    '传输状态已刷新': 'Transfer status refreshed',
    '手动维护各预设参数': 'Maintain preset parameters manually',
    '已选择': 'Selected',
    '切换到': 'Switch to',
    '档后，内置预设将变为：': '— built-in presets become:',
    '档，保存后自动调整全部预设参数': '— saving adjusts every preset parameter',

    // ---- 批次 3：aria-label / title / placeholder 整值词条（属性不做子串替换）----
    'ALAS 服务，跳转 ALAS 管理': 'ALAS service — open ALAS management',
    '待处理事项视图切换': 'Pending items view switch',
    '待处理事项，跳转日志审计': 'Pending items — open the audit log',
    '设备可用性，跳转设备管理': 'Device availability — open device management',
    '权限与绑定类型': 'Permissions and bindings type',
    '设备权限与 ALAS 绑定': 'Device permissions and ALAS bindings',
    '输入设备名称': 'Enter device name',
    '上一页': 'Previous page',
    '下一页': 'Next page',
    '快速设置到期时间': 'Set the expiry quickly',
    '管理员账号不可删除': 'Administrator accounts cannot be deleted',
    '自定义增加天数': 'Custom days to add',
    '自定义天数': 'Custom days',
    '输入用户名': 'Enter username',
    '仅恢复此预设默认值': 'Restore this preset to its defaults',
    '切换标签页后停止观看时长': 'Duration before stopping when tabs switch',
    '均衡 · 适合家庭宽带与少量并发观看': 'Balanced · for home broadband with a few concurrent viewers',
    '极省上行 · 弱网或单设备低码率场景': 'Minimal uplink · for weak networks or a single device at low bitrate',
    '极致 · 上行充裕时获得最佳画质': 'Maximum · best quality when the uplink has headroom',
    '画质与传输分区': 'Quality & transfer section',
    '当前封禁与失败计数': 'Current blocks and failure counts',
    '查看事件详情': 'View event details',
    '输入新 Token 以更新，留空保持不变': 'Enter a new token to update; leave empty to keep the current one',

    // ---- 批次 4：交互态/提示类文案（Toast、状态行、详情弹窗、批量授权模板）----
    '批量设置已授权设备': 'Batch-set authorized devices',
    '批量设置': 'Batch set',
    '批量选择已授权设备': 'Select authorized devices in bulk',
    '全选': 'Select all',
    '反选': 'Invert selection',
    '应用批量设置': 'Apply batch settings',
    '管理员拥有全部 ALAS 配置权限，无需单独授权。': 'Administrators hold every ALAS config permission; no separate grant is needed.',
    '管理员拥有全部设备权限，无需单独授权。': 'Administrators hold every device permission; no separate grant is needed.',
    '密码已重置': 'Password reset',
    '权限变更已保存': 'Permission changes saved',
    '用户信息已更新': 'User info updated',
    '用户已删除': 'User deleted',
    '没有待保存的变更': 'No pending changes',
    '保存失败，请检查表单填写': 'Save failed — check the form',
    '未选择设备': 'No device selected',
    '请先选择设备': 'Select a device first',
    '请先在左侧选择设备': 'Select a device on the left first',
    '请从左侧设备列表选择其他设备': 'Pick another device from the list on the left',
    '暂无内置预设数据': 'No built-in preset data',
    '预设由后端服务提供': 'Presets are provided by the backend service',
    '暂无客户端数据': 'No client data',
    '客户端状态将在连接后显示': 'Client status appears after connecting',
    '没有可预览的内置预设': 'No built-in preset to preview',
    '至少保留一个可切换预设': 'Keep at least one switchable preset',
    '请选择一个内置预设后再恢复默认': 'Pick a built-in preset before restoring defaults',
    '已切换为自定义，不再自动调整预设参数': 'Switched to custom; presets are no longer adjusted automatically',
    '已恢复当前预设默认设置': 'Restored the current preset defaults',
    '已保留待重启状态': 'Kept the restart-needed state',
    '手动维护各预设参数，不自动调整': 'Maintain preset parameters manually; no automatic adjustments',
    '传输已重启': 'Transfer restarted',
    '传输未重启': 'Transfer not restarted',
    '重启后恢复传输': 'Restore the transfer after restarting',
    '重启中…': 'Restarting…',
    '当前没有需要重启的投屏': 'No cast needs a restart right now',
    '保存失败，请检查参数': 'Save failed — check the parameters',
    '保存失败，请检查输入': 'Save failed — check your input',
    '新增失败，请检查输入': 'Create failed — check your input',
    '错误原因': 'Error reason',
    '未知设备': 'unknown device',
    '事件详情已加载': 'Event details loaded',
    '审计记录已刷新': 'Audit records refreshed',
    '日志已刷新': 'Logs refreshed',
    '单行显示': 'Single-line view',
    '正在加载完整事件详情…': 'Loading the full event details…',
    '该事件已被日志保留策略裁剪，可按事件 ID 在审计链里核对': 'This event was trimmed by log retention; use the event ID to check the audit chain',
    '条记录': 'records',
    '当前显示列表中的事件详情': 'Details of the events in the current list',
    '导出任务已提交': 'Export job submitted',
    '导出内容不可用': 'Export content unavailable',
    '导出下载链接来源未被允许': 'The export download origin is not allowed',
    '正在加载用户数据…': 'Loading user data…',
    '该用户暂无关联的 ALAS 配置': 'No ALAS config is linked to this user',
    'Token 解密失败': 'Token decryption failed',
    '该配置已关联给用户': 'Config linked to the user',
    '请选择配置': 'Select a config',
    '观看时长已用完，当前观看会话已结束': 'Watch time used up; this viewing session ended',
    '观看时长已用完，正在结束…': 'Watch time used up; ending…',
    '设备列表可能已过期,将在下一次刷新时重试': 'The device list may be stale; retrying on the next refresh',
    '键盘未响应，请在更多菜单中重试': 'The keyboard is not responding — retry from the More menu',
    '操作已发送': 'Action sent',
    '菜单栏位置已复位': 'Menu position reset',
    '菜单位置已复位': 'Menu position reset',
    '退出失败，请重试': 'Exit failed — retry',
    '退出服务不可用，请重试': 'Exit service unavailable — retry',
    '该配置仅可查看状态,无运行权限': 'You can only view this config state; run permission is missing',
    '已保存 · 当前有多个观看端，视频重启已延后': 'Saved · several viewers are watching, so the video restart is deferred',
    '正在查看设备日志': 'Opening device logs',
    '确定删除设备「': 'Delete device "',
    '」？删除后该设备将从列表中移除，且无法恢复。': '"? The device is removed from the list and this cannot be undone.',
    '确定停用设备「': 'Disable device "',
    '」？停用后该设备将不可投屏，且无法进行连接检测与状态刷新。': '"? A disabled device cannot cast, and connection checks and status refreshes stop.',
    '确定删除自定义预设 ': 'Delete preset ',
    '登录中…': 'Signing in…',
    '登录已暂时封禁，请在倒计时结束后重试。': 'Sign-in is temporarily blocked; retry when the countdown ends.',
    '请输入用户名和密码': 'Enter username and password',
    '认证服务不可用': 'Authentication service unavailable',
    '数据服务不可用': 'Data service unavailable',
    '数据接口尚未配置，请先连接后端服务': 'The data API is not configured — connect the backend service first',
    '正在加载…': 'Loading…',

    // ---- 批次 4b：拼接样本验证暴露的漏网文案（含 api.js 的错误文案）----
    '选择用户': 'Select a user',
    '选择配置': 'Select a config',
    '保存失败：': 'Save failed: ',
    '用户已创建，但初始权限保存失败：': 'User created, but saving the initial permissions failed: ',
    '该配置当前绑定到其他设备。保存时不会静默覆盖。': 'This config is already bound to another device. Saving will not overwrite it silently.',
    '该配置当前绑定到其他设备，保存会把它移动过来。': 'This config is already bound to another device; saving will move it over.',
    '视频重启已延后': 'video restart deferred',
    '已保存 · 视频重启已延后': 'Saved · video restart deferred',
    '部分异常': 'Partially degraded',
    '状态来自 ALAS 连接服务': 'status comes from the ALAS connection service',
    '接口地址协议无效': 'Invalid API address protocol',
    '接口地址无效': 'Invalid API address',
    '数据服务返回了无效 JSON': 'The data service returned invalid JSON',
    '请求超时': 'Request timed out',
    '无法连接数据服务': 'Cannot reach the data service',
    '页面处于后台，已暂停自动刷新': 'The page is in the background; auto-refresh is paused',
    '自动刷新正在退避中': 'Auto-refresh is backing off',
    '请求失败': 'Request failed',
    '接口来源未被允许，请检查后端接口配置': 'The API origin is not allowed — check the backend API config',
    '接口地址无效，请检查后端接口配置': 'Invalid API address — check the backend API config',
    '数据服务响应超时，请重试': 'The data service timed out — retry',
    '无法连接数据服务，请检查网络或服务状态': 'Cannot reach the data service — check the network or service state',
    '请求被雷池网关拒绝，请稍后重试': 'Rejected by the gateway — retry later',
    '网关返回了非数据响应，请稍后重试': 'The gateway returned a non-data response — retry later',
    '数据服务响应格式异常，请稍后重试': 'Unexpected data service response format — retry later',
    '请求被拦截（HTTP 405）：请确认反向代理或 WAF 放行 PUT/PATCH/DELETE 方法': 'Request blocked (HTTP 405): make sure the reverse proxy or WAF allows PUT/PATCH/DELETE',
    '请求被拒绝（HTTP 400）：安全校验未通过，请刷新页面后重试': 'Request rejected (HTTP 400): the security check failed — refresh and retry',
    '登录状态已失效，请重新登录（HTTP 401）': 'Your session expired — sign in again (HTTP 401)',
    '请求被拒绝（HTTP 403）：权限不足或安全校验未通过': 'Request rejected (HTTP 403): missing permission or the security check failed',
    '请求的资源不存在（HTTP 404）': 'The requested resource does not exist (HTTP 404)',
    '请求失败，请稍后重试': 'Request failed — retry later',

    // ---- 批次 5：交互态探针暴露的剩余 UI 文案（权限抽屉 / ALAS 列表空态与确认框）----
    '当前设备暂无 ALAS 绑定': 'No ALAS binding for this device',
    '主动断开': 'Actively disconnected',
    '未命名配置': 'Unnamed config',
    '没有匹配的用户': 'No matching users',
    '暂无用户数据': 'No user data',
    '没有匹配的配置': 'No matching configs',
    '暂无配置数据': 'No config data',
    '仅关联记录': 'binding record only',
    '确定对配置「': 'Run this action on config "',
    '」执行此操作？': '"?',
    '确定删除「': 'Delete "',
    '」对配置「': '" from config "',
    '」的关联？': '"?',
    '列出并切换 ALAS 配置（/configlist）': 'List and switch ALAS configs (/configlist)',
    'ALAS 后台管理入口（/admin、/manage）': 'ALAS admin panel entries (/admin, /manage)',
    '检查并执行 ALAS 更新（/updater、/checkupdate）': 'Check for and run ALAS updates (/updater, /checkupdate)',
    '远程操控 ALAS（/remotecontrol）': 'Control ALAS remotely (/remotecontrol)',
    '脚本工具箱页面（/toolbox）': 'Script toolbox page (/toolbox)',
    'daemon_ 系列任务（模拟点击辅助）': 'daemon_ task family (assisted tapping)',
    'opsidaemon_ 系列任务': 'opsidaemon_ task family',
    'eventstory_ 系列任务': 'eventstory_ task family',
    'benchmark_ 系列任务': 'benchmark_ task family',
    'AzurLaneUncensored（azurlaneuncensored_）': 'AzurLaneUncensored (azurlaneuncensored_)',
    'GameManager（gamemanager_，ALAS 里尚未完成）': 'GameManager (gamemanager_, not finished in ALAS yet)',
    'alas.emulator（模拟器路径与启动参数）': 'alas.emulator (emulator path and launch arguments)',
    'alas.droprecord（战斗录屏）': 'alas.droprecord (battle recording)',
    'emulator.serial（绑定模拟器实例）': 'emulator.serial (bound emulator instance)',
    '任务 alas / Alas设置 / AlasSettings': 'task alas / AlasSettings / AlasSettings',
    'Raw v2 实时画面，获取控制后点击画面可使用物理键盘': 'Raw v2 live view; take control, then click the screen to use a physical keyboard',
    '全部设备宫格': 'All-device grid',
    '单画面': 'Single view',
    '单画面视图': 'Single-view mode',
    '同时显示数量': 'Simultaneous tile count',
    '宫格显示设置': 'Grid display settings',
    '宫格画面缩放百分比': 'Grid zoom percentage',
    '宫格视图（全部设备）': 'Grid view (all devices)',
    '宫格：一次显示全部设备': 'Grid: show every device at once',
    '显示尺寸': 'Display size',
    '最大化': 'Maximize',
    '画面缩放（百分比）': 'Screen zoom (percent)',
    '视图模式': 'View mode',
    '标记为已处理': 'Mark as resolved',
    '启停 ALAS 配置': 'Start / stop ALAS configs',
    '一个配置只能绑定一台设备': 'One config can bind only one device',
    '允许用户修改 ALAS 配置内容。': 'Allow users to edit the ALAS config content.',
    '允许用户启动、停止并编辑该配置。': 'Allow users to start, stop and edit this config.',
    '允许用户启动和停止该配置。': 'Allow users to start and stop this config.',
    '允许编辑': 'Edit allowed',
    '允许运行': 'Run allowed',
    '同时授予': 'Grant as well',
    '当前设备已固定，只需选择用户和 Runtime 配置。': 'The device is fixed here; just pick a user and a Runtime config.',
    '所选配置已绑定其他设备，保存时不会静默覆盖。': 'This config is already bound to another device; saving will not overwrite it silently.',
    '所选配置已绑定其他设备，保存时需要确认换绑。': 'This config is already bound to another device; saving asks you to confirm the rebind.',
    '权限与绑定': 'Permissions and bindings',
    '暂无绑定': 'No bindings',
    '无观看端': 'No viewers',
    '该用户尚无当前设备的观看权限。': 'This user cannot view the current device yet.',
    '不关联': 'Not linked',
    '先选择可观看设备': 'Select a viewable device first',
    '关联后默认允许运行与编辑；ALAS 必须绑定到该用户可观看的设备。': 'Linking grants run and edit by default; ALAS must be bound to a device this user can view.',
    '初始权限': 'Initial permissions',
    '可以留空，创建后仍可在权限管理中补充。': 'Can be left empty; you can still add permissions in permission management later.',
    '同时关联 ALAS': 'Also link ALAS',
    '可选': 'Optional',
    '永久有效（不设置到期时间）': 'Never expires (no expiry time)',
    '管理员自动拥有全部设备与 ALAS 权限，无需单独设置。': 'Administrators already hold every device and ALAS permission; no separate setup needed.',
    '该用户尚不能观看所选设备，可在保存关联时一并授予。': 'This user cannot view the selected device yet; you can grant it while saving the link.',
    '修改后按自定义应用': 'Edits apply as a custom preset',
    '仅用于全屏': 'Fullscreen only',
    '切换带宽档位': 'Switch bandwidth tier',
    '切换标签页后停止观看': 'Stop watching after switching tabs',
    '同步两个停止设置': 'Sync the two stop settings',
    '将从服务端重新加载默认配置为当前配置与保存基线，': 'Reloads the server defaults as the current config and save baseline; ',
    '开启后不会出现在普通投屏选择中': 'When on it never appears in the normal casting picker',
    '开启后以“切换标签页后停止观看”为主设置，同步两项的启用状态和时长；关闭后保留最后同步值': 'When on, "Stop watching after switching tabs" is the master setting and both stop toggles stay in sync; when off the last synced values are kept',
    '指标来自当前运行会话': 'Metrics from the current run session',
    '等待视频流': 'Waiting for the video stream',
    '观看离开或超时后自动结束投屏': 'Casting ends automatically when viewers leave or time out',
    '跟随切换标签页': 'Follow tab switches',
    '选择默认协议，并控制哪些协议可用': 'Pick the default protocol and control which protocols are available',
    '页面隐藏后停止当前浏览器观看；到期前固定保留 60 秒提醒，可设置 5 至 10 分钟': 'Stop watching in this browser once the page is hidden; a reminder is always kept 60 s before expiry, configurable from 5 to 10 min',
    '点卡片选中预览，点「应用」载入参数，保存后生效': 'Click a card to preview it, click "Apply" to load the parameters, then save',
    '保存后，服务端会按推荐值覆盖全部内置预设的码率、分辨率与帧率；自定义预设不受影响。': 'Saving overwrites the bitrate, resolution and FPS of every built-in preset with the recommended values; custom presets are untouched.',
    '设为默认': 'Set as default',
    's 前': 's ago',
    '一般无需修改': 'Usually no changes needed',
    '失败累计后触发验证与封禁': 'Accumulated failures trigger verification and blocking',
    '元数据': 'Metadata',
    '动作': 'Action',
    '用户代理': 'User agent',
    '完整性信息': 'Integrity info',
    '结构版本': 'Schema version',
    '选择记录后加载完整详情': 'Select a record to load the full details',
    '用于核对本地审计链；通常无需人工阅读。': 'For verifying the local audit chain; normally not read by hand.',
    'Raw v2 播放器将在连接成功后接管此画面': 'The Raw v2 player takes over this screen once the connection succeeds',
    '仅浏览器': 'Browser only',
    '其他用户占用中': 'In use by another user',
    '正在连接所选设备，请稍候…': 'Connecting to the selected device, please wait…',
    '显示与画质设置': 'Display and quality settings',
    '暂无视频流': 'No video stream',
    '画质设置已同步': 'Quality settings synced',
    '联系管理员在后台"ALAS 管理"中绑定配置': 'Ask an administrator to bind a config under "ALAS management"',
    '该设备未绑定 ALAS 配置': 'No ALAS config is bound to this device',
    '工作台内设置': 'Settings inside the workbench',
    '等待真实视频流': 'Waiting for a real video stream',
    '所选设备已断开连接，请检查设备状态': 'The selected device is disconnected — check the device',
    'ALAS 里的页面和功能': 'ALAS page and feature',
    '下面按': 'Boundaries below are listed by',
    '列出边界（': '(',
    '）：标记为受限的入口，普通用户看不到、直接访问也会被拒（服务端 403）。写死在这里，改范围请改': '): entries marked Restricted are invisible to standard users and direct access is denied (server 403). The list is hard-coded here; to change the scope edit ',
    '隐藏范围已改为代码写死': 'Hidden scope is hard-coded',
    '，这里只做只读展示：普通用户看不到哪些 ALAS 页面/游戏任务由': ', read-only here: which ALAS pages / game tasks standard users cannot see is determined by ',
    '决定（ALAS 2.0 适配时改这两处）。下面按 ALAS 任务树分组，仅供对照。': ' (change these two places when adapting ALAS 2.0). The ALAS task tree below is for reference only.',
    '关联信息已完整': 'Link info complete',
    '分配配置': 'Assign config',
    '在 ALAS 中打开': 'Open in ALAS',
    '在工作台显示 ALAS': 'Show ALAS in the workbench',
    '已进入 Runtime': 'In Runtime',
    '未分配 · 设备未绑定': 'Unassigned · no device bound',
    '标记「已隐藏」的游戏任务对普通用户不可见': 'Game tasks marked "Hidden" are invisible to standard users',
    '补充设备观看权限': 'Grant device view permission',
    '这些规则是普通用户的分权边界：放开条目会把对应的导航入口、管理员工具或模拟器设置暴露给普通用户，改动前请确认影响。': 'These rules are the permission boundary for standard users: opening an entry exposes the matching navigation items, admin tools or emulator settings to standard users — check the impact first.',
    '运行与编辑': 'Run and edit',
    '写死的清单，页面只读': 'Hard-coded list; this page is read-only',
    '最后一个观看端离开后，超过该时长自动停止投屏；0 表示仅保留 5 秒重连宽限': 'Stop casting once the last viewer has been gone for this long; 0 keeps only the 5 s reconnect grace period',
    '从该设备进入 ALAS 时优先使用此配置。': 'This config is used first when entering ALAS from this device.',

    // ---- 补齐缺失的整句/片段（英文模式下中英混杂的根因）----
    '保存后立即生效；未自定义的项目沿用环境变量默认值。「停用」表示登录不再有任何限制（不计数、不要求人机验证、不封禁）。': 'Applies immediately. Unset items keep their environment defaults. "Disabled" removes every sign-in restriction (no counting, no human verification, no blocking).',
    'ALAS 配置与绑定由管理员在后台"ALAS 管理"中维护;启动后 ALAS 将接管设备的自动化任务。': 'ALAS configs and bindings are maintained by admins under "ALAS management"; once started, ALAS takes over the device automation tasks.',
    '限制所有预设与用户参数的分辨率（最长边像素）；低于 720p 时全屏默认画质档不可用': 'Caps the resolution (longest edge, px) of every preset and user override; below 720p the default fullscreen quality preset is unavailable',
    '点击档位后保存，将自动调整全部画质预设的码率、分辨率与帧率，避免占满上行带宽': 'Saving after picking a tier adjusts bitrate, resolution and FPS across all presets so the uplink is not saturated',
    '已保存的 Token 不会回显；在此输入新值并点击“保存连接设置”即可更新': 'A saved token is never shown again; type a new value and click "Save connection settings" to update it',
    '依次选择用户、设备和 Runtime 配置，其他权限保持推荐默认值。': 'Pick a user, a device and a Runtime config in order; leave the other permissions at their recommended defaults.',
    '预设来自后台"画质与传输"管理页,修改参数会自动切换到自定义并保存。': 'Presets come from the "Quality & transfer" admin page; editing a parameter switches to custom and saves.',
    '关闭后工作台仅显示预设卡片，隐藏码率 / 分辨率 / 帧率微调入口': 'When off, the workbench shows preset cards only and hides the bitrate / resolution / FPS controls',
    '登录爆破防护的开关、阈值与时长；用不到的部署可以整体关闭': 'Switches, thresholds and windows for brute-force sign-in protection; deployments that do not need it can turn it all off',
    '画质预设点卡片选中预览，点「应用」载入参数，保存后生效': 'Click a preset card to preview it, click "Apply" to load its parameters, then save to apply',
    '顶部状态条预览工作台上方的设备状态条与通知': 'Preview of the device status bar and notifications shown above the workbench',
    '运行日志与安全审计实时记录 · 按需刷新': 'Runtime logs and the security audit trail are recorded live · refresh on demand',
    '选择预设后可调整参数，点击底部保存后生效': 'Pick a preset, adjust the parameters, then click save at the bottom',
    '只改变本页显示尺寸,不会重启视频流。': 'Changes the display size on this page only; the video stream is not restarted.',
    '分别管理用户访问和 ALAS 关联': 'Manage user access and ALAS bindings separately',
    '阈值与时长失败累计后触发验证与封禁': 'Human verification and blocking start once the threshold and window are exceeded',
    '为用户连接设备与 ALAS 配置': 'Connect devices and ALAS configs to a user',
    '高清 · 适合高速宽带与多路并发': 'HD · for fast broadband with several concurrent streams',
    'Runtime 地址与访问凭据': 'Runtime address and access credentials',
    '控制权限会自动包含观看权限。': 'Control permission always includes view permission.',
    '画质档位、传输链路与会话策略': 'Quality tiers, transport link and session policy',
    '在线 · ALAS 未加载': 'Online · ALAS not loaded',
    '填写设备服务要求的连接地址': 'Enter the connection address required by the device service',
    '建立可用的 ALAS 入口': 'Set up a usable ALAS entry point',
    '当前未绑定 ALAS 配置': 'No ALAS config bound yet',
    'ALAS 配置与运行操作': 'ALAS configs and runtime operations',
    '自动授权 · 无需关联': 'Auto-granted · no binding needed',
    'ALAS 状态已同步': 'ALAS status synced',
    '一个配置绑定一台设备': 'One config binds one device',
    '打开 ALAS 面板': 'Open the ALAS panel',
    '用于工作台访问上下文': 'Used as the workbench access context',
    '正在读取访问关系…': 'Loading access relations…',
    '允许普通用户微调': 'Allow standard users to fine-tune',
    '活跃投屏会话列表': 'Active cast session list',
    '高级一般无需修改': 'Advanced settings rarely need changes',
    '该设备唯一配置': 'The only config for this device',
    '管理员无需关联': 'Admins need no binding',
    '暂无活跃会话': 'No active sessions',
    '当前未运行': 'Not running',
    '权限或安全策略拒绝': 'Denied by permission or security policy',
    '设置已提交，等待服务端回传最新配置': 'Settings submitted, waiting for the server to return the latest config',
    '失败或错误结果': 'Failed or errored outcomes',
    '应用预设：稳定': 'Applied preset: Balanced',
    '稳定': 'Balanced',
    '未设置': 'Not set',
    '未加载': 'not loaded',
    '访问': 'access',
    '分': 'min',
    '暂无记录': 'No records',
    '没有记录': 'No records',
    '需优先核查': 'review first',
    '需处理': 'to handle',
    '未运行': 'Not running',
    '严重度': 'Severity',
    '分辨率': 'Resolution',
    '总时长': 'Total time',
    '自动化': 'automation',
    '高风险': 'high-risk',
    '无需': 'not needed',
    '生效': 'apply',
    '选择': 'Select',
    '列表': 'list',
    '面板': 'panel',
    '档位': 'tier',
    '按需': 'on demand',
    '策略': 'policy',
    '对象': 'Target',
    '结果': 'Result',
    '请求': 'Request',
    '详情': 'Details',
    '诊断': 'diagnostics',
    '设置': 'Settings',
    '创建': 'Create',
    '微调': 'Fine-tune',
    '执行': 'Run',
    '无人': 'no controller',
    '暂无': 'None',
    '至少': 'at least',
    '运行': 'Run',
    '重置': 'Reset',
    '预设': 'preset',
    '观看时长': 'Watch time',
    '观看记录': 'Watch history',
    '连接诊断': 'Connection diagnostics',
    '原始尺寸': 'Original size',
    '参数微调': 'Fine-tune parameters',
    '带宽预设': 'Bandwidth preset',
    '会话策略': 'Session policy',
    '传输链路': 'Transport link',
    '创建关联': 'Create binding',
    '执行失败': 'Run failed',
    '已拒绝': 'Rejected'
  };

  /* ================= 数值 / 复合句式 正则规则 ================= */
  var RULES_EN = [
    [/将在\s*(\d+)\s*秒后停止观看/g, 'will stop in $1 s'],
    [/同时显示上限已改为\s*(\d+)\s*路，当前超出的格子需要手动停止/g, 'Tile limit changed to $1; tiles above it must be stopped manually'],
    [/截图帧率已改为\s*(\d+)\s*fps，正在重新开始截图/g, 'Screenshot FPS changed to $1; restarting captures'],
    [/设备已切换为横屏\s*·\s*画面方向已自动复位/g, 'Switched to landscape · orientation reset automatically'],
    [/设备已切换为竖屏\s*·\s*画面方向已自动复位/g, 'Switched to portrait · orientation reset automatically'],
    // 「跟随设备」：自动选的方向不一定是正向，带上角度
    [/设备已切换为横屏\s*·\s*画面已自动旋转\s*(\d+)°/g, 'Switched to landscape · view auto-rotated $1°'],
    [/设备已切换为竖屏\s*·\s*画面已自动旋转\s*(\d+)°/g, 'Switched to portrait · view auto-rotated $1°'],
    [/该配置当前绑定到\s*(\S+?)。保存时不会静默覆盖。/g, 'This config is currently bound to $1. Saving will not overwrite it silently.'],
    [/接口尚未配置：\s*(\S+)/g, 'API not configured: $1'],
    [/重启失败\s*(\d+)\s*台：\s*(\S+)/g, 'Restart failed on $1: $2'],
    [/已重启\s*(\d+)\s*台，\s*(\d+)\s*台因多人观看延迟重启/g, 'Restarted $1; $2 deferred while viewers are watching'],
    [/难度\s*(\d+)\s*bit\s*｜\s*已计算\s*(\S+)\s*｜\s*耗时\s*(\S+)/g, 'Difficulty $1 bit · computed $2 · took $3'],
    [/正在计算\s*(\S+)\s*次/g, 'Computing $1 hashes'],
    [/服务端错误（HTTP\s*(\d+)），请查看服务日志/g, 'Server error (HTTP $1) — check the service log'],
    [/登录已暂时封禁，剩余\s*(\S+)/g, 'Sign-in blocked; $1 left'],
    [/登录已暂时封禁，请在\s*(\S+)\s*后重试。/g, 'Sign-in blocked; retry in $1.'],
    [/接口配置无效：\s*(\S+)/g, 'Invalid API config: $1'],
    [/接口来源未被允许：\s*(\S+)/g, 'API origin not allowed: $1'],
    [/请求失败（HTTP\s*(\d+)）/g, 'Request failed (HTTP $1)'],
    [/(\d+)\s*位用户可观看\s*·\s*(\d+)\s*个 ALAS 关联/g, '$1 users can view · $2 ALAS bindings'],
    [/到期\s*(\d{4}-\d{2}-\d{2})/g, 'Expires $1'],
    // ---- 专用复合句式：必须先于下面的通用数值 / 单位规则执行，否则会被通用规则先吃掉 ----
    [/已选择\s*(\d+)\s*台/g, 'Selected $1 units'],
    [/最近\s*(\d+)\s*小时发现\s*(\d+)\s*条高风险事件，请优先核查/g, '$2 high-risk events in the last $1h — review first'],
    [/已显示\s*(\d+)\s*条\s*·\s*还有更早记录/g, 'Showing $1 · older records available'],
    [/共\s*(\d+)\s*条\s*·\s*显示\s*(\d+)\s*-\s*(\d+)/g, '$1 total · showing $2–$3'],
    [/将导出当前筛选视图中的记录：\s*(\d+)\s*条/g, 'Exports $1 records from the current view'],
    [/通知（\s*(\d+)\s*）/g, 'Notifications ($1)'],
    [/(\d+)\s*条记录/g, '$1 records'],
    [/近\s*(\d+)\s*天/g, 'Last $1 days'],
    [/(\d+)\s*项指标/g, '$1 metrics'],
    [/当前封禁\s*(\d+)\s*个封禁\s*·\s*(\d+)\s*个失败计数/g, 'Blocked now: $1 · $2 failures'],
    [/(\d+)\s*个封禁\s*·\s*(\d+)\s*个失败计数/g, 'Blocked now: $1 · $2 failures'],
    [/共\s*(\d+)\s*人/g, '$1 users'],
    [/共\s*(\d+)\s*条\s*·\s*每页\s*(\d+)\s*条/g, '$1 total · $2 per page'],
    [/(\d+)\s*个配置（自动授权）/g, '$1 configs (auto-granted)'],
    [/活跃投屏\s*(\d+)\s*个会话/g, 'Active casts: $1 sessions'],
    [/总时长\s*(\d+)\s*秒/g, 'Total $1 s'],
    [/(\d+)\s*个客户端/g, '$1 clients'],
    [/(\d+)\s*台需处理/g, '$1 to handle'],
    [/(\d+)\s*台离线\s*·\s*数据来自设备服务/g, '$1 devices offline · data from the device service'],
    [/(\d+)\s*个账号即将到期\s*·\s*(\d+)\s*个 ALAS 异常/g, '$1 accounts expiring · $2 ALAS issues'],
    [/1\s*个账号即将到期\s*·\s*1\s*个 ALAS 异常/g, '1 account expiring · 1 ALAS issue'],
    [/未设置备注\s*·\s*([0-9a-fA-F.:]+)/g, 'No notes · $1'],
    [/最近检查：\s*(\S+)/g, 'Last check: $1'],
    [/今天\s*(\d{4}-\d{2}-\d{2}\s*\d{0,2}:?\d{0,2})/g, 'Today $1'],
    [/提前\s*(\d+)\s*天提醒\s*·\s*到期自动停止 ALAS：已关闭/g, '$1-day reminder · ALAS auto-stop on expiry: off'],
    [/提前\s*(\d+)\s*天提醒\s*·\s*到期自动停止 ALAS：已开启/g, '$1-day reminder · ALAS auto-stop on expiry: on'],
    [/宽度需在\s*(\d+)\s*-\s*(\d+)\s*、\s*高度需在\s*(\d+)\s*-\s*(\d+)\s*之间，\s*最长边至少\s*(\d+)px/g, 'Width $1–$2, height $3–$4; longest edge at least $5px'],
    [/分钟需在\s*(\d+)\s*-\s*(\d+)\s*之间，\s*0\s*为仅保留\s*5\s*秒重连宽限/g, 'Must be $1–$2; 0 keeps a 5 s reconnect grace only'],
    [/小时需在\s*(\d+)\s*-\s*(\d+)\s*、\s*分钟需在\s*(\d+)\s*-\s*(\d+)\s*之间，\s*0\s*为不限制/g, 'Hours $1–$2, minutes $3–$4; 0 means no limit'],
    [/时长需为\s*(\d+)\s*至\s*(\d+)\s*分钟的整数/g, 'Must be a whole number between $1 and $2 min'],
    [/已连接\s*·\s*/g, 'Connected · '],
    [/已配置\s*·\s*更新于\s*/g, 'Configured · Updated '],
    [/(\d+)\s*次/g, '$1 times'],
    [/(\d+)\s*分(?!钟)/g, '$1 min'],

    // 长句式 / 复合规则（必须先于通用数值单位规则执行）
    [/将在\s*(\d+)\s*分钟后停止观看/g, 'will stop in $1 min'], [/延长\s*(\d+)\s*分钟/g, 'extend by $1 min'],
    [/剩\s*(\d+)\s*天/g, '$1 days left'], [/剩余\s*(\d+)\s*天/g, '$1 days left'],
    [/宽度需在\s*(\d+)\s*和\s*(\d+)\s*之间/g, 'Width must be $1–$2'],
    [/高度需在\s*(\d+)\s*和\s*(\d+)\s*之间/g, 'Height must be $1–$2'],
    [/帧率需在\s*(\d+)\s*和\s*(\d+)\s*之间/g, 'FPS must be $1–$2'],
    [/码率需在\s*(\d+)\s*和\s*(\d+)\s*之间/g, 'Bitrate must be $1–$2'],
    [/分钟需在\s*(\d+)\s*和\s*(\d+)\s*之间/g, 'Must be $1–$2 min'],
    [/秒需在\s*(\d+)\s*和\s*(\d+)\s*之间/g, 'Must be $1–$2 s'],
    [/小时需在\s*(\d+)\s*和\s*(\d+)\s*之间/g, 'Must be $1–$2 h'],
    [/最长\s*(\d+)\s*小时/g, 'max $1 h'],
    // 计数复合
    [/(\d+)\s*路投屏/g, '$1 casts'], [/(\d+)\s*个控制中/g, '$1 in control'],
    [/(\d+)\s*个超时预警/g, '$1 timeout alerts'], [/(\d+)\s*个观看端/g, '$1 viewers'],
    [/(\d+)\s*个会话/g, '$1 sessions'], [/(\d+)\s*台设备离线/g, '$1 devices offline'],
    [/(\d+)\s*个账号将到期/g, '$1 accounts expiring'], [/(\d+)\s*个长时间投屏/g, '$1 long casts'],
    [/(\d+)\s*条安全告警/g, '$1 security alerts'], [/(\d+)\s*项待处理/g, '$1 pending items'],
    [/(\d+)\s*个配置/g, '$1 configs'], [/(\d+)\s*个归属冲突/g, '$1 ownership conflicts'],
    // 账户到期策略（用户页）：提前 N 天提醒 · 到期自动停止 ALAS：开/关
    [/提前\s*(\d+)\s*天提醒/g, '$1-day reminder'],
    [/到期自动停止 ALAS：已开启/g, 'ALAS auto-stop on expiry: on'],
    [/到期自动停止 ALAS：已关闭/g, 'ALAS auto-stop on expiry: off'],
    // 到期提醒弹窗正文（含日期与天数）
    [/你的账户将于\s*(\d{4}-\d{2}-\d{2})\s*到期，还剩\s*(\d+)\s*天。/g, 'Expires on $1 · $2 days left.'],
    [/你的账户将于\s*(\d{4}-\d{2}-\d{2})\s*到期。/g, 'Expires on $1.'],
    [/你的账户即将到期，还剩\s*(\d+)\s*天。/g, 'Expiring soon · $1 days left.'],
    [/你的账户即将到期。/g, 'Expiring soon.'],
    [/你的账户已于\s*(\d{4}-\d{2}-\d{2})\s*到期。/g, 'Expired on $1.'],
    [/你的账户已到期。/g, 'Account expired.'],
    [/到期后设备投屏与 ALAS 权限会立即失效，请联系管理员续期。/g, 'Device and ALAS access stops at expiry — ask an administrator to renew.'],
    [/设备投屏与 ALAS 权限已失效，请联系管理员续期。/g, 'Device and ALAS access has stopped — ask an administrator to renew.'],
    [/(\d+)\s*个可授权账户/g, '$1 accounts'], [/(\d+)\s*条配置归属冲突/g, '$1 config conflicts'],
    [/(\d+)\s*条待处理/g, '$1 pending'], [/(\d+)\s*小时发现/g, '$1h window'],
    [/(\d+)\s*条失败或拒绝事件/g, '$1 failed/denied events'], [/(\d+)\s*天日志/g, '$1-day logs'],
    // 时间单位（带前后缀者优先于纯单位）
    [/(\d+)\s*分钟前/g, '$1 min ago'], [/(\d+)\s*小时前/g, '$1 h ago'], [/(\d+)\s*天前/g, '$1 days ago'],
    [/(\d+)\s*分钟/g, '$1 min'], [/(\d+)\s*小时/g, '$1 h'], [/(\d+)\s*秒/g, '$1 s'], [/(\d+)\s*天/g, '$1 days'],
    [/(\d+)\s*台/g, '$1 units'], [/(\d+)\s*位/g, '$1 digits'],
    [/已关闭\s*(\d+)\s*项/g, '$1 disabled'],
    [/共\s*/g, ''],
  ];
  /* 只整段匹配的短词条：这些词作为句内子串出现时会把句子改坏
     （例如单位「个 / 条 / 次 / 帧 / 秒」、连接词「的 / 与」、单字「是」，
       以及会出现在动态句子开头的「确定 / 原因」——它们做子串替换会把
       「确定删除该设备？」这类弹窗改成半截中英混排）。 */
  var EXACT_EN = {
    '个': '', '条': 'entries', '次': 'times', '帧': 'frames', '秒': 's',
    '宽': 'Width', '高': 'Height', '至': 'to', '是': 'Yes', '允许': 'Allowed',
    '开': 'On', '关': 'Off',
    '确定': 'OK', '原因': 'Reason',
    '观看': 'View',
    '未授权': 'Not granted',
    '永久有效': 'Never expires',
    '配置列表': 'Config list',
    '更新器': 'Updater',
    '远程控制': 'Remote control',
    '工具': 'Tools',
    '半自动点击': 'Assisted tapping',
    '大世界半自动': 'Open-world semi-auto',
    '活动剧情': 'Event story',
    '性能测试': 'Benchmark',
    '反和谐': 'Uncensored patch',
    '游戏管理器': 'Game manager',
    '模拟器': 'Emulator',
    '重启模拟器': 'Restart emulator',
    '性能优化': 'Performance tuning',
    '录屏': 'Screen recording',
    '模拟器序列号': 'Emulator serial',
    'Alas 设置（配置编辑器）': 'Alas settings (config editor)',
    '丢帧': 'Dropped',
    '队列': 'Queue',
    '查看': 'View',
    '无权限': 'No permission',
    '未记录': 'Not recorded',
    '已封禁': 'Blocked',
    '手动配置': 'Manual config',
    '按推荐值': 'Use recommended',
    '恢复进度': 'Recovery progress',
    '地址无效': 'Invalid address',
    '已显示': 'Showing',
    '难度': 'Difficulty',
    '吗？': '?',
    '的': ' → ', '与': ' and '
  };
  var KEYS_EN = Object.keys(EN).sort(function (a, b) { return b.length - a.length; });
  var ATTRS = ['placeholder', 'title', 'aria-label', 'alt'];

  function norm(s) { return (s || '').replace(/\s+/g, ' ').trim(); }

  function escapeRe(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }

  function translateText(str) {
    if (!str || !/[\u4e00-\u9fff]/.test(str)) return str;
    if (current === 'zh') return str; // 中文界面返回原文，不做翻译
    // 精确匹配优先（与文件头的策略一致）：整段就是某条词条时直接返回，
    // 不再走下面的数值规则——否则「…保留 5 秒重连宽限」这类整句词条会被
    // /(\d+)\s*秒/ 先改写，导致整句词条永远匹配不上。
    var whole = norm(str);
    var exactValue = Object.prototype.hasOwnProperty.call(EN, whole) ? EN[whole]
      : (Object.prototype.hasOwnProperty.call(EXACT_EN, whole) ? EXACT_EN[whole] : undefined);
    if (exactValue !== undefined) {
      var raw = str.trim();
      return raw ? str.replace(raw, function () { return exactValue; }) : str;
    }
    var rules = RULES_EN;
    var out = str;
    for (var i = 0; i < rules.length; i++) {
      if (rules[i][0].test(out)) out = out.replace(rules[i][0], rules[i][1]);
    }
    if (!/[\u4e00-\u9fff]/.test(out)) return out;
    // 单次正则交替匹配：每个位置取最长词条，替换结果不会被再次扫描。
    var re = translateText._reCache && translateText._reCache.en;
    if (!re) {
      re = new RegExp('(' + KEYS_EN.filter(function (k) { return k.length >= 2; }).map(escapeRe).join('|') + ')', 'g');
      translateText._reCache = translateText._reCache || {};
      translateText._reCache.en = re;
    }
    out = out.replace(re, function (m) {
      var v = EN[m];
      return (v === undefined || v === null) ? m : v;
    });
    return out;
  }

  var isSkippable = function (el) {
    if (!el) return true;
    var tag = el.tagName;
    if (tag === 'SCRIPT' || tag === 'STYLE' || tag === 'TEXTAREA') return true;
    // 只跳过显式标记与**语言切换器本身**（它的选项必须永远显示各自的语言）。
    // 早先这里把 SELECT/OPTION 一律跳过，导致全站筛选下拉框的选项永远不翻译
    // （例如「全部状态」「在线」「空闲」「即将到期」在英文界面里仍是中文）。
    if (el.closest && (el.closest('[data-i18n-skip]') || el.closest('[data-lang-switch]'))) return true;
    return false;
  };

  // 可逆翻译：记录每个节点/属性的原始中文，切换语言时从原文重新翻译，切回中文时还原
  var originals = new WeakMap();       // Text 节点 -> 原始文本
  var attrOriginals = new WeakMap();   // Element -> { attr: 原始值 }
  var lastTargets = new WeakMap();     // Text 节点 -> 我们最后一次写入的值（用于区分自身写入）
  var lastAttrTargets = new WeakMap(); // Element -> { attr: 最后一次写入的值 }
  var origTitle = null;

  function ensureOriginalText(node) {
    var val = node.nodeValue;
    if (!val) return;
    var stored = originals.get(node);
    if (stored === undefined) {
      originals.set(node, val);
    } else if (current !== 'zh' && /[\u4e00-\u9fff]/.test(val) && val !== stored && val !== lastTargets.get(node)) {
      // 页面脚本写入了新的中文（倒计时 / Toast / 动态列表等），
      // 而非我们自己的翻译写入（lastTargets 记录的是我们上次写入的值）
      originals.set(node, val);
    }
  }

  function ensureOriginalAttr(el, an) {
    var map = attrOriginals.get(el);
    if (!map) { map = {}; attrOriginals.set(el, map); }
    var av = el.getAttribute(an);
    if (map[an] === undefined) {
      map[an] = av;
    } else if (current !== 'zh' && av && /[\u4e00-\u9fff]/.test(av) && av !== map[an]) {
      var last = lastAttrTargets.get(el);
      if (!last || last[an] !== av) map[an] = av;
    }
  }

  function translateNode(root) {
    // 文本节点
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: function (node) {
        var p = node.parentElement;
        if (isSkippable(p)) return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      }
    });
    var nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    for (var i = 0; i < nodes.length; i++) {
      var node = nodes[i];
      ensureOriginalText(node);
      var stored = originals.get(node) || node.nodeValue;
      var target = current === 'zh' ? stored : translateText(stored);
      if (target !== node.nodeValue) {
        node.nodeValue = target;
        lastTargets.set(node, target);
      }
    }
    // 属性翻译（精确匹配原文）
    var els = (root.querySelectorAll ? root.querySelectorAll('*') : []);
    for (var e = 0; e < els.length; e++) {
      var el = els[e];
      if (isSkippable(el)) continue;
      for (var a = 0; a < ATTRS.length; a++) {
        var an = ATTRS[a];
        if (!el.hasAttribute(an)) continue;
        ensureOriginalAttr(el, an);
        var orig = (attrOriginals.get(el) || {})[an];
        if (!orig || !/[\u4e00-\u9fff]/.test(orig)) continue;
        var nv = current === 'zh' ? orig : translateText(norm(orig));
        if (nv !== el.getAttribute(an)) {
          el.setAttribute(an, nv);
          var lm = lastAttrTargets.get(el);
          if (!lm) { lm = {}; lastAttrTargets.set(el, lm); }
          lm[an] = nv;
        }
      }
    }
    // 页面标题
    if (root === document.body) {
      if (origTitle === null && document.title) origTitle = document.title;
      if (origTitle) document.title = current === 'zh' ? origTitle : translateText(origTitle);
    }
  }

  function apply() {
    if (!document.body) return;
    translateNode(document.body);
  }

  // 语言选择器绑定
  function syncSelectValue(sel) {
    var desired = current === 'en' ? 'en-US' : 'zh-CN';
    var found = false;
    var opts = sel.options;
    for (var i = 0; i < opts.length; i++) {
      if (opts[i].value === desired) { sel.value = desired; found = true; break; }
    }
    if (!found) {
      var prefix = desired.split('-')[0];
      for (var j = 0; j < opts.length; j++) {
        if (opts[j].value.split('-')[0] === prefix) { sel.value = opts[j].value; found = true; break; }
      }
    }
  }
  function bindSelects() {
    var selects = document.querySelectorAll('[data-lang-switch]');
    for (var i = 0; i < selects.length; i++) {
      (function (sel) {
        syncSelectValue(sel);
        sel.addEventListener('change', function () {
          setLang(sel.value);
        });
      })(selects[i]);
    }
  }

  // 自动注入顶栏语言切换器（管理后台 / 投屏工作台；登录与设置页已有原生控件则跳过）
  function injectSwitcherStyle() {
    if (document.getElementById('sg-lang-style')) return;
    var style = document.createElement('style');
    style.id = 'sg-lang-style';
    style.textContent = [
      '.sg-lang-wrap{display:inline-flex;align-items:center;gap:5px;height:30px;padding:0 8px;border:1px solid var(--c-border,rgba(0,0,0,.09));border-radius:999px;background:var(--c-surface-soft,rgba(128,128,128,.08));cursor:pointer;flex:0 0 auto}',
      '.sg-lang-wrap .sg-lang-icon{width:14px;height:14px;color:var(--c-text-faint,#8e8e93)}',
      '.sg-lang-select{border:0;background:transparent;color:var(--c-text,#3c3c43);font-size:12px;font-weight:500;outline:none;cursor:pointer;max-width:76px}',
      '.sg-lang-select option{color:#1d1d1f;background:#fff}',
      '.dark .sg-lang-select{color:#d5d8de}',
      '.dark .sg-lang-select option{color:#f5f5f7;background:#1c1c1e}',
      '@media (max-width:480px){.sg-lang-wrap{display:none}}'
    ].join('\n');
    document.head.appendChild(style);
  }

  function injectSwitcher() {
    if (document.querySelector('[data-lang-switch]')) return;
    var host = document.querySelector('.admin-topbar .topbar-actions') || document.querySelector('.topnav-actions');
    if (!host) return;
    var wrap = document.createElement('label');
    wrap.className = 'sg-lang-wrap';
    wrap.setAttribute('aria-label', 'Language');
    var icon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    icon.setAttribute('viewBox', '0 0 24 24');
    icon.setAttribute('fill', 'none');
    icon.setAttribute('stroke', 'currentColor');
    icon.setAttribute('stroke-width', '1.8');
    icon.setAttribute('stroke-linecap', 'round');
    icon.setAttribute('stroke-linejoin', 'round');
    icon.classList.add('sg-lang-icon');
    icon.innerHTML = '<circle cx="12" cy="12" r="10"/><path d="M2 12h20"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>';
    var select = document.createElement('select');
    select.className = 'sg-lang-select';
    select.setAttribute('data-lang-switch', '');
    select.setAttribute('aria-label', 'Language');
    var opts = [['zh-CN', '简体中文'], ['en-US', 'English']];
    for (var i = 0; i < opts.length; i++) {
      var o = document.createElement('option');
      o.value = opts[i][0];
      o.textContent = opts[i][1];
      select.appendChild(o);
    }
    wrap.appendChild(icon);
    wrap.appendChild(select);
    host.insertBefore(wrap, host.firstChild);
  }

  function setLang(code) {
    current = LANG_MAP[code] || 'zh';
    var locale = current === 'en' ? 'en-US' : 'zh-CN';
    try { localStorage.setItem(STORE_KEY, locale); } catch (e) { }
    document.documentElement.lang = locale;
    apply();
    bindSelects();
    if (window.ScrcpyGateI18n) window.ScrcpyGateI18n._emit();
  }

  // 对外接口
  window.ScrcpyGateI18n = {
    getLang: function () { return current === 'en' ? 'en-US' : 'zh-CN'; },
    setLang: setLang,
    t: translateText,
    _listeners: [],
    on: function (fn) { this._listeners.push(fn); },
    _emit: function () { for (var i = 0; i < this._listeners.length; i++) try { this._listeners[i](this.getLang()); } catch (e) { } }
  };

  // 初始应用
  document.documentElement.lang = current === 'en' ? 'en-US' : 'zh-CN';
  injectSwitcherStyle();
  function boot() { apply(); injectSwitcher(); bindSelects(); }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

  // 动态内容监听
  if (window.MutationObserver) {
    var mo = new MutationObserver(function (mutations) {
      if (current === 'zh') return;
      var dirty = false;
      for (var i = 0; i < mutations.length; i++) {
        var m = mutations[i];
        if (m.type === 'characterData') { dirty = true; break; }
        if (m.addedNodes && m.addedNodes.length) { dirty = true; break; }
      }
      if (dirty) {
        clearTimeout(mo._t);
        mo._t = setTimeout(function () { translateNode(document.body); }, 60);
      }
    });
    if (document.body) { mo.observe(document.body, { childList: true, subtree: true, characterData: true }); }
    else {
      document.addEventListener('DOMContentLoaded', function () {
        mo.observe(document.body, { childList: true, subtree: true, characterData: true });
      });
    }
  }
})();
