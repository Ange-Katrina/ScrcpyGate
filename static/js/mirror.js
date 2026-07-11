const bootstrap = JSON.parse(document.getElementById("scrcpygate-bootstrap").textContent);
const csrfToken = bootstrap.csrf_token;
const SELECTED_KEY = 'webscrcpy:v2:selectedDeviceId';
const state = {
  user:null, devices:[], sessions:{}, selectedDeviceId:localStorage.getItem(SELECTED_KEY) || '', activeDeviceId:'',
  videoWs:null, controlWs:null, eventWs:null, jmuxer:null, input:null, hasControl:false, fit:'contain', screen:{w:1280,h:720}, alas:null,
  videoConnected:false, controlConnected:false, videoPrefs:null, eventConnected:false, eventSeq:0, eventReconnectTimer:null, calibrationTimer:null, recoveryTimer:null,
  playerResetTimer:null, controlKeepaliveTimer:null, lastPlayerResetAt:0, lastDelayTrimAt:0, layoutFrame:null, renderFrame:null,
  idleStopTimer:null, idleStopReason:'', starting:false, lastStartAt:0, videoSeq:0, controlSeq:0, qualityProfile:'balanced', qualityApplying:false, pageLeaving:false,
  deviceNodes:new Map(), qualityNodes:new Map(), sessionRevision:0, sessionRevisions:new Map()
};
const resourceRequests = Object.create(null);
const $ = (id) => document.getElementById(id);
function wsUrl(path){ return `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}${path}`; }
function getDeviceId(device){ return String((device && (device.device_id || device.id)) || '').trim(); }
function currentDevice(){ return state.devices.find(d => getDeviceId(d) === state.selectedDeviceId) || null; }
function selectedSession(){ const id=state.selectedDeviceId; const device=currentDevice(); return id ? (state.sessions[id] || (device && device.session) || null) : null; }
function deviceLabel(device){ return device ? (device.display_name || device.name || '设备') : '未选择设备'; }
function show(message, ms=3200){ const n=$('notice'); n.textContent=message || '操作失败'; n.classList.add('show'); clearTimeout(show.t); show.t=setTimeout(()=>n.classList.remove('show'),ms); }
function chip(text, cls=''){ const s=document.createElement('span'); s.className=`chip ${cls}`.trim(); s.textContent=text; return s; }
async function fetchJson(url, options={}){
  const opts = Object.assign({headers:{}}, options);
  if (opts.body && typeof opts.body !== 'string') { opts.headers['content-type']='application/json'; opts.body = JSON.stringify(opts.body); }
  if (!['GET','HEAD'].includes((opts.method || 'GET').toUpperCase())) opts.headers['x-csrf-token'] = csrfToken;
  const res = await fetch(url, opts);
  const text = await res.text();
  let data = {}; try { data = text ? JSON.parse(text) : {}; } catch (_) { data = {detail:text}; }
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
  return data;
}
function requestResource(name, url, apply, options={}){
  const slot = resourceRequests[name] || (resourceRequests[name] = {sequence:0, controller:null, promise:null});
  const force = !!options.force;
  if (slot.promise && !force) return slot.promise;
  if (force && slot.controller) slot.controller.abort();
  const sequence = ++slot.sequence;
  const controller = new AbortController();
  slot.controller = controller;
  const promise = fetchJson(url, {signal:controller.signal}).then(data=>{
    if (sequence !== slot.sequence) return undefined;
    apply(data);
    return data;
  }).catch(error=>{
    if (error && error.name === 'AbortError') return undefined;
    throw error;
  }).finally(()=>{
    if (sequence === slot.sequence) {
      slot.controller = null;
      slot.promise = null;
    }
  });
  slot.promise = promise;
  return promise;
}
function invalidateResource(name){
  const slot=resourceRequests[name];
  if (!slot) return;
  slot.sequence+=1;
  if (slot.controller) slot.controller.abort();
  slot.controller=null;
  slot.promise=null;
}
function scheduleRender(){
  if (state.renderFrame) return;
  state.renderFrame=requestAnimationFrame(()=>{
    state.renderFrame=null;
    render();
  });
}
function normalizeSelection(){
  const ids = state.devices.map(getDeviceId).filter(Boolean);
  if (!ids.length) { state.selectedDeviceId=''; localStorage.removeItem(SELECTED_KEY); return; }
  if (!state.selectedDeviceId || !ids.includes(state.selectedDeviceId)) state.selectedDeviceId = ids[0];
  localStorage.setItem(SELECTED_KEY, state.selectedDeviceId);
}
function selectDevice(id){
  if (!id || id === state.selectedDeviceId) return;
  const oldActive = state.activeDeviceId;
  if (state.videoWs || state.controlWs) {
    closeVideo();
    if (oldActive && oldActive !== id) stopSwitchedMirror(oldActive).catch(()=>{});
  }
  state.selectedDeviceId=id;
  localStorage.setItem(SELECTED_KEY, id);
  closeSidebar();
  render();
}
async function stopSwitchedMirror(id){
  const session = state.sessions[id];
  if (!session || !session.running) return;
  await new Promise(resolve=>setTimeout(resolve, 250));
  const result = await fetchJson(`/api/devices/${encodeURIComponent(id)}/mirror/idle-stop`, {method:'POST', body:{reason:'switch_device'}});
  replaceMutationSessions(result.sessions);
  render();
}
function mergeSessions(devices, sessions){
  const merged = Object.assign({}, sessions || {});
  devices.forEach(d => { const id=getDeviceId(d); if (id && d.session) merged[id]=d.session; });
  return merged;
}
function replaceRealtimeSessions(sessions){
  const revision=++state.sessionRevision;
  state.sessions=Object.assign({}, sessions || {});
  Object.keys(state.sessions).forEach(id=>state.sessionRevisions.set(id, revision));
}
function updateRealtimeSession(id, session){
  if (!id) return;
  const revision=++state.sessionRevision;
  state.sessions[id]=session;
  state.sessionRevisions.set(id, revision);
}
function applySessionSnapshot(devices, sessions, requestRevision){
  const incoming=mergeSessions(devices, sessions);
  const next={};
  devices.forEach(device=>{
    const id=getDeviceId(device);
    if (!id) return;
    const eventRevision=state.sessionRevisions.get(id) || 0;
    if (eventRevision > requestRevision && state.sessions[id]) next[id]=state.sessions[id];
    else if (incoming[id]) next[id]=incoming[id];
  });
  state.sessions=next;
}
function replaceMutationSessions(sessions){
  if (!sessions) return;
  replaceRealtimeSessions(sessions);
  invalidateResource('devices');
}
function socketLive(ws){
  return !!ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING);
}
const BUILTIN_PROFILE_LABELS = {smooth:'流畅', balanced:'稳定', sharp:'高清', low_latency:'低延迟'};
const ADB_STATE_LABELS = {online:'在线', offline:'离线', unauthorized:'未授权', reconnecting:'重连中', unknown:'未知'};
const STREAM_HEALTH_LABELS = {healthy:'正常', idle:'空闲', starting:'启动中', config:'等待配置', invalid_h264:'视频异常', adb_failed:'ADB 失败', failed:'失败', stopped:'已停止', unknown:'未知'};
const ALAS_STATUS_LABELS = {running:'运行中', stopped:'已停止', error:'异常', unknown:'未知'};
function labelFrom(map, value, fallback){
  const key = String(value || '').trim();
  return map[key] || fallback || key || '未知';
}
function adbStateLabel(value){ return labelFrom(ADB_STATE_LABELS, value, '未知'); }
function streamModeLabel(mode){ return ({raw:'原始流 raw', protocol:'协议流 protocol', legacy:'诊断 legacy'}[mode]) || mode; }
function streamModeShortLabel(mode){ return ({raw:'原始流', protocol:'协议流', legacy:'诊断流', none:'无'}[mode]) || mode || '未知'; }
function streamHealthLabel(value){ return labelFrom(STREAM_HEALTH_LABELS, value, '未知'); }
function alasStatusLabel(value){ return labelFrom(ALAS_STATUS_LABELS, value, '未知'); }
function qualitySummary(){
  const q = qualityPayload();
  const size = q.max_size > 0 ? q.max_size : '原始';
  const fps = q.max_fps > 0 ? q.max_fps : '不限';
  return `输出 ${size} / ${Math.round(q.video_bit_rate / 100000) / 10}Mbps / ${fps}fps / ${streamModeShortLabel(q.scrcpy_stream_mode || 'raw')}`;
}
function enabledStreamModes(){
  const modes = state.videoPrefs && state.videoPrefs.enabled_stream_modes;
  return Array.isArray(modes) && modes.length ? modes : ['raw'];
}
function renderQualityStreamMode(selected){
  const select = $('qualityStreamMode');
  if (!select) return;
  const modes = enabledStreamModes();
  const options=new Map(Array.from(select.options).map(option=>[option.value, option]));
  options.forEach((option, mode)=>{ if (!modes.includes(mode)) option.remove(); });
  modes.forEach(mode=>{
    let option=options.get(mode);
    if (!option) {
      option=document.createElement('option');
      option.value=mode;
    }
    option.textContent=streamModeLabel(mode);
    select.appendChild(option);
  });
  select.value = modes.includes(selected) ? selected : modes[0];
}
function qualityPayload(){
  const profile = state.qualityProfile || 'balanced';
  const presets = state.videoPrefs && state.videoPrefs.profiles;
  const preset = (presets && presets[profile]) || (presets && presets.balanced) || {video_bit_rate:900000, max_size:540, max_fps:24};
  const modes = enabledStreamModes();
  return {
    profile,
    adaptive:false,
    video_bit_rate:Number(preset.video_bit_rate || 900000),
    max_size:Number(preset.max_size == null ? 540 : preset.max_size),
    max_fps:Number(preset.max_fps == null ? 24 : preset.max_fps),
    scrcpy_stream_mode:modes.includes($('qualityStreamMode').value) ? $('qualityStreamMode').value : modes[0]
  };
}
function applyQualityToForm(options){
  const q = options || (state.videoPrefs && state.videoPrefs.effective) || {profile:'balanced', adaptive:false, video_bit_rate:900000, max_size:540, max_fps:24};
  const profiles = (state.videoPrefs && state.videoPrefs.profiles) || {};
  state.qualityProfile = profiles[q.profile] ? q.profile : 'balanced';
  renderQualityStreamMode(q.scrcpy_stream_mode || 'raw');
  renderQualityButtons();
}
function qualityProfileOrder(){
  const profiles = (state.videoPrefs && state.videoPrefs.profiles) || {};
  const base = ['smooth','balanced','sharp','low_latency'].filter(name=>profiles[name] || ['smooth','balanced','sharp','low_latency'].includes(name));
  const custom = Object.keys(profiles).filter(name=>!base.includes(name)).sort();
  return base.concat(custom);
}
function qualityProfileLabel(profile){
  const labels = (state.videoPrefs && state.videoPrefs.profile_labels) || {};
  return BUILTIN_PROFILE_LABELS[profile] || labels[profile] || profile;
}
function setQualityStatus(text){
  const el = $('qualityStatus');
  if (el) el.textContent = text || '';
}
function ensureQualityButtons(){
  const grid = $('qualityProfiles');
  if (!grid) return;
  const label = grid.querySelector('label');
  const profiles=qualityProfileOrder();
  const active=new Set(profiles);
  grid.querySelectorAll('[data-profile]').forEach(button=>{
    const profile=button.dataset.profile;
    if (!state.qualityNodes.has(profile)) state.qualityNodes.set(profile, button);
  });
  state.qualityNodes.forEach((button, profile)=>{
    if (!active.has(profile)) {
      button.remove();
      state.qualityNodes.delete(profile);
    }
  });
  profiles.forEach(profile=>{
    let button=state.qualityNodes.get(profile);
    if (!button) {
      button=document.createElement('button');
      button.className='btn';
      button.dataset.profile=profile;
      button.type='button';
      button.onclick=()=>chooseQualityProfile(profile).catch(e=>show(e.message));
      state.qualityNodes.set(profile, button);
    }
    button.textContent=qualityProfileLabel(profile);
    grid.insertBefore(button, label);
  });
}
function renderQualityButtons(){
  ensureQualityButtons();
  document.querySelectorAll('[data-profile]').forEach(btn=>{
    btn.classList.toggle('active', btn.dataset.profile === state.qualityProfile);
    btn.disabled = !!state.qualityApplying;
    btn.title = state.qualityApplying ? '画质正在应用，请稍等' : '点击切换投屏画质';
  });
  const select = $('qualityStreamMode');
  if (select) select.disabled = !!state.qualityApplying;
}
async function chooseQualityProfile(profile){
  if (state.qualityApplying) return show('画质正在应用，请稍等');
  state.qualityProfile = profile;
  renderQualityButtons();
  await saveOrApplyQuality();
}
function renderDevices(){
  const box=$('devices');
  let empty=box.querySelector('[data-device-empty]');
  if (!empty) {
    empty=chip('暂无可用设备','warn');
    empty.dataset.deviceEmpty='true';
    box.appendChild(empty);
  }
  empty.hidden=state.devices.length > 0;
  const activeIds=new Set(state.devices.map(getDeviceId).filter(Boolean));
  state.deviceNodes.forEach((button, id)=>{
    if (!activeIds.has(id)) {
      button.remove();
      state.deviceNodes.delete(id);
    }
  });
  let cursor=empty.nextSibling;
  state.devices.forEach(device => {
    const id=getDeviceId(device); const session=state.sessions[id] || device.session; const active=id===state.selectedDeviceId;
    const adbState = device.adb_state || 'unknown';
    const adbBlocked = adbState === 'offline' || adbState === 'unauthorized';
    const mirrorVisible = !!(session && session.running && (Number(session.clients || 0) > 0 || (id === state.activeDeviceId && state.videoConnected)));
    let btn=state.deviceNodes.get(id);
    if (!btn) {
      btn=document.createElement('button');
      btn.className='device-card';
      btn.type='button';
      btn.dataset.deviceId=id;
      const title=document.createElement('div'); title.className='device-title';
      const name=document.createElement('strong');
      const status=chip('');
      title.append(name, status);
      const metaText=document.createElement('div'); metaText.className='device-meta'; metaText.textContent='ADB 地址已隐藏';
      const meta=document.createElement('div'); meta.className='chips';
      const control=chip('');
      const adb=chip('');
      const clients=chip('');
      meta.append(control, adb, clients);
      btn.append(title, metaText, meta);
      btn._scrcpygate={name,status,control,adb,clients};
      state.deviceNodes.set(id, btn);
    }
    const parts=btn._scrcpygate;
    btn.classList.toggle('active', active);
    btn.setAttribute('aria-current', active ? 'true' : 'false');
    btn.disabled=!id || !device.enabled || adbState === 'unauthorized';
    parts.name.textContent=deviceLabel(device);
    parts.status.textContent=mirrorVisible ? '投屏中' : (device.enabled ? adbStateLabel(adbState) : '禁用');
    parts.status.className=`chip ${mirrorVisible ? 'ok' : adbState === 'online' ? 'ok' : adbBlocked ? 'danger' : 'warn'}`;
    parts.control.textContent=device.can_control ? '可控制' : '只观看';
    parts.control.className=`chip ${device.can_control ? 'ok' : 'warn'}`;
    parts.adb.textContent=`ADB: ${adbStateLabel(adbState)}`;
    parts.adb.className=`chip ${adbState === 'online' ? 'ok' : adbBlocked ? 'danger' : 'warn'}`;
    parts.clients.textContent=`${Number((session && session.clients) || 0)} 个观看端`;
    parts.clients.hidden=!(session && session.clients);
    btn.onclick=()=>selectDevice(id);
    if (btn !== cursor) box.insertBefore(btn, cursor);
    cursor=btn.nextSibling;
  });
}
function renderStatus(){
  const device=currentDevice(); const session=selectedSession();
  const running=!!(session && session.running);
  const localConnected=!!(state.videoConnected || state.controlConnected || socketLive(state.videoWs) || socketLive(state.controlWs));
  $('selectedTitle').textContent = deviceLabel(device);
  $('selectedMeta').textContent = device ? qualitySummary() : '从设备列表选择一个设备后开始投屏';
  const topStatus=$('topStatus'); topStatus.textContent='';
  const items=[];
  items.push(chip(device ? deviceLabel(device) : '未选择', device ? '' : 'warn'));
  items.push(chip(running ? '投屏中' : '未投屏', running ? 'ok' : 'warn'));
  items.push(chip(state.videoConnected ? '视频已连接' : '视频未连接', state.videoConnected ? 'ok' : 'warn'));
  items.push(chip(state.controlConnected ? '控制通道已连接' : '控制通道未连接', state.controlConnected ? 'ok' : 'warn'));
  if (session && session.control_lock) items.push(chip(`控制: ${session.control_lock.username}`, session.control_lock.username === (state.user && state.user.username) ? 'ok' : 'warn'));
  else items.push(chip('控制空闲'));
  if (session && session.video) items.push(chip(`流: ${session.video.max_size || '原始'} / ${session.video.max_fps || '不限'}fps`));
  if (session && session.stream_mode) items.push(chip(`流模式: ${streamModeShortLabel(session.stream_mode)}/${streamHealthLabel(session.stream_health)}`, session.stream_health === 'healthy' ? 'ok' : 'warn'));
  if (device && device.adb_state) items.push(chip(`ADB: ${adbStateLabel(device.adb_state)}`, device.adb_state === 'online' ? 'ok' : 'warn'));
  if (state.alas) items.push(chip(`ALAS: ${alasStatusLabel(state.alas.status)}`, state.alas.status === 'running' ? 'ok' : state.alas.status === 'error' ? 'warn' : ''));
  items.forEach(item=>topStatus.appendChild(item));
  $('startBtn').textContent = !device ? '开始投屏' : state.starting ? '启动中...' : running ? (state.videoConnected ? '刷新画面' : '连接画面') : '开始投屏';
  $('startBtn').disabled = state.starting || state.qualityApplying || !device;
  $('controlBtn').textContent = state.hasControl ? '释放控制' : '获取控制';
  $('controlBtn').disabled = state.starting || !device || !device.can_control;
  $('stopBtn').disabled = state.starting || state.qualityApplying || !device || (!running && !localConnected);
  $('alasBtn').textContent = 'ALAS';
  renderAlasPanel();
  document.querySelectorAll('[data-fit]').forEach(btn=>btn.classList.toggle('active', btn.dataset.fit === state.fit));
  renderQualityButtons();
  $('empty').textContent = device ? (running ? '点击连接画面' : '点击开始投屏') : '选择设备后开始投屏';
  $('empty').style.display = state.videoConnected ? 'none' : 'grid';
  $('phoneVideo').style.objectFit = 'contain';
  scheduleLayout();
}
function render(){ renderDevices(); renderStatus(); renderAccountPanel(); $('roleChip').textContent = state.user && state.user.is_admin ? '管理员' : '普通用户'; $('adminLink').hidden = !(state.user && state.user.is_admin); }
function renderAlasPanel(){
  const box=$('alasPanelStatus'); if(!box) return; box.textContent='';
  const a=state.alas || {};
  box.appendChild(chip(a.config ? `配置 ${a.config}` : '未绑定', a.config ? 'ok' : 'warn'));
  box.appendChild(chip(alasStatusLabel(a.status), a.status === 'running' ? 'ok' : a.status === 'error' ? 'warn' : ''));
  box.appendChild(chip(a.can_run ? '可运行' : '不可运行', a.can_run ? 'ok' : 'warn'));
  if(a.error) box.appendChild(chip(a.error, 'danger'));
  $('alasToggleRun').textContent = a.status === 'error' ? '重启 ALAS' : a.status === 'running' ? '停止 ALAS' : '启动 ALAS';
  $('alasToggleRun').disabled = !a.can_run;
}
function renderAccountPanel(){
  const box=$('accountInfo'); if(!box) return; box.textContent='';
  const user=state.user || {};
  box.appendChild(chip(user.username || '未登录', 'ok'));
  box.appendChild(chip(user.is_admin ? '管理员' : '普通用户'));
}
function closeToolPanels(exceptId){
  ['tools','alasTools','accountTools'].forEach(id=>{
    const el=$(id);
    if (el && id !== exceptId) el.classList.remove('open');
  });
}
function toggleToolPanel(id){
  const el=$(id);
  if (!el) return;
  const willOpen = !el.classList.contains('open');
  closeToolPanels(id);
  el.classList.toggle('open', willOpen);
}
function clearPasswordForm(){
  ['currentPassword','newPassword','confirmPassword'].forEach(id=>{ if($(id)) $(id).value=''; });
}
async function changePassword(){
  const payload={
    current_password:$('currentPassword').value,
    new_password:$('newPassword').value,
    confirm_password:$('confirmPassword').value
  };
  if (!payload.current_password || !payload.new_password || !payload.confirm_password) return show('请完整填写密码');
  if (payload.new_password !== payload.confirm_password) return show('两次输入的新密码不一致');
  const result = await fetchJson('/api/account/password', {method:'PUT', body:payload});
  clearPasswordForm();
  show(result.other_sessions_removed ? `密码已修改，已清理 ${result.other_sessions_removed} 个其它会话` : '密码已修改');
}
function loadUser(force=false){
  return requestResource('user', '/api/me', data=>{
    state.user=data.user;
    scheduleRender();
  }, {force});
}
function loadDevices(force=false){
  const requestRevision=state.sessionRevision;
  return requestResource('devices', '/api/devices', data=>{
    state.devices=data.devices || [];
    applySessionSnapshot(state.devices, data.sessions || {}, requestRevision);
    normalizeSelection();
    scheduleRender();
  }, {force});
}
function loadVideoPreferences(force=false){
  return requestResource('video', '/api/video/preferences', data=>{
    state.videoPrefs=data;
    applyQualityToForm(data.effective);
    scheduleRender();
  }, {force});
}
function loadAlasStatus(force=false){
  return requestResource('alas', '/api/alas/status', data=>{
    state.alas=data;
    scheduleRender();
  }, {force});
}
async function loadAll(options={}){
  const force=!!options.force;
  const results=await Promise.allSettled([
    loadUser(force),
    loadDevices(force),
    loadVideoPreferences(force),
    loadAlasStatus(force)
  ]);
  const failures=results.filter(result=>result.status === 'rejected');
  if (failures.length === results.length) throw failures[0].reason;
  return results;
}
async function startMirror(){
  const id=state.selectedDeviceId;
  if (!id) return show('请先选择设备');
  if (state.starting) return show('投屏正在启动，请稍等');
  if (state.qualityApplying) return show('画质正在应用，请稍等');
  const now = Date.now();
  if (now - state.lastStartAt < 1200) return show('操作过快，请稍等');
  state.lastStartAt = now;
  state.starting = true;
  render();
  try {
    const data = await fetchJson(`/api/devices/${encodeURIComponent(id)}/mirror/start`, {method:'POST', body:qualityPayload()});
    replaceMutationSessions(data.sessions);
    if (data.ok === false) return show(data.detail || data.error || '投屏启动失败，请检查 ADB 连接', 5200);
    if (state.activeDeviceId === id && socketLive(state.videoWs)) {
      schedulePlayerReset();
      if (!socketLive(state.controlWs)) openControl(id);
      show('投屏已在运行，已刷新播放器');
      return;
    }
    reconnectSockets(id);
  } finally {
    state.starting = false;
    render();
  }
}
async function stopMirror(){
  const id=state.selectedDeviceId;
  if (!id) return show('请先选择设备');
  if (state.starting) return show('投屏正在启动，请稍等');
  const result=await fetchJson(`/api/devices/${encodeURIComponent(id)}/mirror/stop`, {method:'POST'});
  replaceMutationSessions(result.sessions);
  closeVideo();
  render();
}
async function saveOrApplyQuality(){
  if (state.qualityApplying) return show('画质正在应用，请稍等');
  const id=state.selectedDeviceId;
  const payload = qualityPayload();
  const running = !!(selectedSession() && selectedSession().running);
  invalidateResource('video');
  state.qualityApplying = true;
  setQualityStatus(running ? '正在切换画质，投屏流会短暂重启...' : '正在保存画质偏好...');
  render();
  try {
    if (!id) {
      const saved = await fetchJson('/api/video/preferences', {method:'PUT', body:payload});
      state.videoPrefs = Object.assign({}, state.videoPrefs || {}, saved);
      setQualityStatus('');
      show('画质偏好已保存');
      return;
    }
    const result = await fetchJson(`/api/devices/${encodeURIComponent(id)}/mirror/settings`, {method:'PUT', body:payload});
    if (result.ok === false) {
      replaceMutationSessions(result.sessions);
      setQualityStatus('画质应用失败');
      show(result.detail || result.error || '画质应用失败，投屏未恢复', 5200);
      return;
    }
    state.videoPrefs = Object.assign({}, state.videoPrefs || {}, {effective:result.preferences, preferences:result.preferences});
    replaceMutationSessions(result.sessions);
    if (result.restarted) {
      reconnectSockets(id);
      setQualityStatus('画质已应用，投屏流已重启');
      show('画质已应用，投屏已按新参数重启');
    } else {
      setQualityStatus('');
      show('画质偏好已保存，下次启动投屏生效');
    }
  } catch (error) {
    setQualityStatus('画质应用失败');
    throw error;
  } finally {
    state.qualityApplying = false;
    render();
  }
}
function closeVideoSocket(){
  state.videoSeq += 1;
  const ws = state.videoWs;
  state.videoWs = null;
  state.videoConnected = false;
  if (ws) { try { ws.close(); } catch(_){} }
  if (state.jmuxer) { try { state.jmuxer.destroy(); } catch(_){} state.jmuxer=null; }
  resetVideoElement();
  if (state.playerResetTimer) { clearTimeout(state.playerResetTimer); state.playerResetTimer=null; }
}
function stopControlKeepalive(){
  if (state.controlKeepaliveTimer) clearInterval(state.controlKeepaliveTimer);
  state.controlKeepaliveTimer = null;
}
function startControlKeepalive(){
  if (state.controlKeepaliveTimer) return;
  const ws = state.controlWs;
  if (!state.hasControl || !ws || ws.readyState !== WebSocket.OPEN) return;
  state.controlKeepaliveTimer = setInterval(()=>{
    if (!state.hasControl || state.controlWs !== ws || ws.readyState !== WebSocket.OPEN) {
      stopControlKeepalive();
      return;
    }
    ws.send(JSON.stringify({type:'control_keepalive'}));
  }, 30000);
}
function setControlOwnership(ok){
  state.hasControl = !!ok;
  if (state.hasControl) startControlKeepalive();
  else stopControlKeepalive();
}
function destroyInput(){
  const input=state.input;
  state.input=null;
  if (input && typeof input.destroy === 'function') {
    try { input.destroy(); } catch (_) {}
  }
}
function closeControlSocket(){
  state.controlSeq += 1;
  const ws = state.controlWs;
  state.controlWs = null;
  state.controlConnected = false;
  setControlOwnership(false);
  destroyInput();
  if (ws) { try { ws.close(); } catch(_){} }
}
function closeVideo(){
  cancelInactiveStop();
  closeVideoSocket();
  closeControlSocket();
  state.activeDeviceId='';
}
function autoStopDelayMs(){
  const minutes = Number((state.videoPrefs && state.videoPrefs.auto_stop_minutes) || 15);
  return minutes > 0 ? minutes * 60 * 1000 : 0;
}
function cancelInactiveStop(){
  if (state.idleStopTimer) clearTimeout(state.idleStopTimer);
  state.idleStopTimer = null;
  state.idleStopReason = '';
}
function scheduleInactiveStop(reason){
  cancelInactiveStop();
  const delay = autoStopDelayMs();
  if (delay <= 0 || !socketLive(state.videoWs)) return;
  state.idleStopReason = reason || 'inactive';
  const minutes = Math.round(delay / 60000);
  show(`页面未聚焦，${minutes} 分钟后自动停止本浏览器观看以节省上行`, 4200);
  state.idleStopTimer = setTimeout(()=>idleStopMirror(state.idleStopReason).catch(()=>{}), delay);
}
async function idleStopMirror(reason){
  const id = state.activeDeviceId || state.selectedDeviceId;
  if (!id) return;
  closeVideoSocket();
  closeControlSocket();
  state.activeDeviceId = '';
  await new Promise(resolve=>setTimeout(resolve, 300));
  const result = await fetchJson(`/api/devices/${encodeURIComponent(id)}/mirror/idle-stop`, {method:'POST', body:{reason:reason || 'inactive'}});
  replaceMutationSessions(result.sessions);
  show(result.stopped ? '页面长时间未聚焦，投屏已自动停止' : '页面长时间未聚焦，已停止本浏览器观看');
  render();
}
function reconnectSockets(id){
  closeVideo();
  state.activeDeviceId=id;
  openVideo(id, {force:true});
  openControl(id, {force:true});
}
function sendPlayerReset(){
  const msg=JSON.stringify({type:'player_reset'});
  if (state.videoWs && state.videoWs.readyState === WebSocket.OPEN) { try { state.videoWs.send(msg); } catch(_){} }
}
function requestVideoPrime(ws){
  const target = ws || state.videoWs;
  if (target && target.readyState === WebSocket.OPEN) {
    try { target.send(JSON.stringify({type:'player_reset'})); } catch(_) {}
  }
}
function resetVideoElement(){
  const video = $('phoneVideo');
  if (!video) return;
  try { video.pause(); } catch(_) {}
  try { video.removeAttribute('src'); video.load(); } catch(_) {}
}
function jmuxerConfig(video){
  const q = qualityPayload();
  const fps = q.max_fps > 0 ? q.max_fps : 24;
  return {
    node: video,
    mode:'video',
    flushingTime: Math.max(33, Math.round(1000 / fps)),
    maxDelay:220,
    fps,
    clearBuffer:true,
    onError:(error)=>{ console.warn('JMuxer error', error); schedulePlayerReset(); }
  };
}
function completeAnnexBChunk(data){
  const bytes = data instanceof Uint8Array ? data : new Uint8Array(data);
  const out = new Uint8Array(bytes.length + 4);
  out.set(bytes, 0);
  out.set([0, 0, 0, 1], bytes.length);
  return out;
}
function schedulePlayerReset(){
  if (state.playerResetTimer) return;
  const now = Date.now();
  if (now - state.lastPlayerResetAt < 1400) {
    requestVideoPrime();
    return;
  }
  state.lastPlayerResetAt = now;
  state.playerResetTimer=setTimeout(()=>{
    state.playerResetTimer=null;
    const video=$('phoneVideo');
    if (state.jmuxer) { try { state.jmuxer.destroy(); } catch(_){} }
    resetVideoElement();
    state.jmuxer = new JMuxer(jmuxerConfig(video));
    sendPlayerReset();
  }, 350);
}
function trimPlaybackDelay(video){
  try {
    const now = Date.now();
    if (now - state.lastDelayTrimAt < 800) return;
    if (!video || !video.buffered || !video.buffered.length) return;
    const end = video.buffered.end(video.buffered.length - 1);
    const delay = end - video.currentTime;
    if (delay > 0.45) {
      state.lastDelayTrimAt = now;
      video.currentTime = Math.max(0, end - 0.18);
    }
  } catch (_) {}
}
function frameDurationMs(){
  const fps = Number(qualityPayload().max_fps || 24);
  return Math.round(1000 / Math.max(1, fps));
}
function openVideo(id, options={}){
  if (!options.force && state.activeDeviceId === id && socketLive(state.videoWs)) return state.videoWs;
  closeVideoSocket();
  state.activeDeviceId=id;
  const token = ++state.videoSeq;
  const video=$('phoneVideo');
  state.jmuxer = new JMuxer(jmuxerConfig(video));
  const ws = new WebSocket(wsUrl(`/ws/devices/${encodeURIComponent(id)}/video`));
  state.videoWs = ws;
  ws.binaryType='arraybuffer';
  ws.onopen=()=>{ if (state.videoSeq !== token || state.videoWs !== ws) return; state.videoConnected=true; render(); requestVideoPrime(ws); setTimeout(()=>{ if (state.videoSeq === token && state.videoWs === ws) requestVideoPrime(ws); }, 450); if (document.hidden || !document.hasFocus()) scheduleInactiveStop('open_in_background'); };
  ws.onmessage = async (event) => {
    if (state.videoSeq !== token || state.videoWs !== ws) return;
    if (typeof event.data === 'string') { const msg=JSON.parse(event.data); if (msg.session) updateRealtimeSession(id, msg.session); render(); requestVideoPrime(ws); return; }
    const buf = event.data instanceof Blob ? await event.data.arrayBuffer() : event.data;
    if (state.videoSeq !== token || state.videoWs !== ws) return;
    if (!state.jmuxer) return;
    state.jmuxer.feed({video:completeAnnexBChunk(buf), duration:frameDurationMs()});
    trimPlaybackDelay(video);
    if (video.paused) video.play().catch(()=>{});
  };
  ws.onerror=()=>{ if (state.videoSeq === token && state.videoWs === ws) show('视频通道连接失败'); };
  ws.onclose = () => { if (state.videoSeq !== token || state.videoWs !== ws) return; state.videoConnected=false; state.videoWs=null; render(); };
  video.onloadedmetadata = () => updateInputSize();
  video.onresize = () => updateInputSize();
  return ws;
}
function openControl(id, options={}){
  if (!options.force && state.activeDeviceId === id && socketLive(state.controlWs)) return state.controlWs;
  closeControlSocket();
  state.activeDeviceId=id;
  const token = ++state.controlSeq;
  const ws = new WebSocket(wsUrl(`/ws/devices/${encodeURIComponent(id)}/control`));
  state.controlWs = ws;
  ws.binaryType='arraybuffer';
  ws.onopen = () => { if (state.controlSeq !== token || state.controlWs !== ws) return; state.controlConnected=true; setupInput(); render(); };
  ws.onmessage = (event) => {
    if (state.controlSeq !== token || state.controlWs !== ws) return;
    if (typeof event.data !== 'string') return;
    const msg=JSON.parse(event.data);
    if (msg.type === 'hello' && state.sessions[id]) {
      const session=Object.assign({}, state.sessions[id], {control_lock:msg.lock || null});
      updateRealtimeSession(id, session);
    }
    if (msg.type === 'control_lock') {
      if ('lock' in msg && state.sessions[id]) {
        const session=Object.assign({}, state.sessions[id], {control_lock:msg.lock || null});
        updateRealtimeSession(id, session);
      }
      if (msg.ok !== undefined) setControlOwnership(msg.ok);
    }
    if (msg.type === 'control_released') setControlOwnership(false);
    if (msg.type === 'control_error') show(msg.error || '控制失败');
    render();
  };
  ws.onerror=()=>{ if (state.controlSeq === token && state.controlWs === ws) show('控制通道连接失败'); };
  ws.onclose = () => { if (state.controlSeq !== token || state.controlWs !== ws) return; state.controlConnected=false; state.controlWs=null; setControlOwnership(false); destroyInput(); render(); };
  return ws;
}
function updateInputSize(){
  const video=$('phoneVideo');
  const w=video.videoWidth || state.screen.w;
  const h=video.videoHeight || state.screen.h;
  state.screen={w,h};
  if (state.input && state.input.resizeScreen) state.input.resizeScreen(w,h);
  scheduleLayout();
}
function layoutVideo(){
  const stage=$('stage'); const wrap=$('videoWrap'); if (!stage || !wrap) return;
  const style=getComputedStyle(stage);
  const availW=stage.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
  const availH=stage.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom);
  const aspect=(state.screen.w || 1280) / Math.max(1, state.screen.h || 720);
  let width=availW; let height=width / aspect;
  if (state.fit === 'original') {
    const naturalW = state.screen.w || 1280;
    const naturalH = state.screen.h || 720;
    const scale = Math.min(1, availW / naturalW, availH / naturalH);
    width = naturalW * scale;
    height = naturalH * scale;
  } else if (height > availH) {
    height=availH; width=height * aspect;
  }
  wrap.style.width=`${Math.max(1, Math.floor(width))}px`;
  wrap.style.height=`${Math.max(1, Math.floor(height))}px`;
  if (state.input && state.input.invalidateGeometry) state.input.invalidateGeometry();
}
function scheduleLayout(){
  if (state.layoutFrame) return;
  state.layoutFrame=requestAnimationFrame(()=>{
    state.layoutFrame=null;
    layoutVideo();
  });
}
function handleViewportResize(){
  scheduleLayout();
}
function setupInput(){
  const video=$('phoneVideo');
  destroyInput();
  updateInputSize();
  state.input = new ScrcpyInput((data) => {
    if (!state.hasControl || !state.controlWs || state.controlWs.readyState !== WebSocket.OPEN) return;
    const bytes = data instanceof ArrayBuffer ? new Uint8Array(data) : null;
    const isTouchMove = bytes && bytes[0] === 2 && bytes[1] === 2;
    if (isTouchMove && state.controlWs.bufferedAmount > 32768) return;
    state.controlWs.send(data);
  }, video, state.screen.w, state.screen.h);
}
async function toggleControl(){
  const id=state.selectedDeviceId; if (!id) return show('请先选择设备');
  const ws = openControl(id);
  const send = () => {
    if (state.controlWs !== ws || ws.readyState !== WebSocket.OPEN) return;
    const releasing = state.hasControl;
    if (releasing) setControlOwnership(false);
    ws.send(JSON.stringify({type: releasing ? 'release_control' : 'acquire_control', force:false}));
  };
  if (ws.readyState === WebSocket.OPEN) send();
  else ws.addEventListener('open', send, {once:true});
}
function sendKey(code){ if (!state.hasControl || !state.input) return show('请先获取控制'); if (state.input.sendKeyCodePress) state.input.sendKeyCodePress(code); }
async function reloadAlas(){ await loadAlasStatus(true); }
async function toggleAlas(){ invalidateResource('alas'); const result = await fetchJson('/api/alas/toggle', {method:'POST'}); if(result.ok === false) return show(result.error || 'ALAS 操作失败'); state.alas = result.alas || result; render(); show(state.alas.status === 'running' ? 'ALAS 已启动' : 'ALAS 状态已更新'); }
function applyEventMessage(msg){
  if (msg.sessions) replaceRealtimeSessions(msg.sessions);
  const id=String(msg.device_id || '').trim();
  if (id && msg.type === 'mirror_status') {
    if (msg.session) updateRealtimeSession(id, msg.session);
    else updateRealtimeSession(id, Object.assign({}, state.sessions[id] || {}, {device_id:id, running:!!msg.running}));
  }
  if (id && msg.type === 'control_lock') {
    const device=state.devices.find(item=>getDeviceId(item) === id);
    const session=Object.assign({}, state.sessions[id] || (device && device.session) || {device_id:id});
    session.control_lock=msg.lock || null;
    updateRealtimeSession(id, session);
    if (!msg.lock || msg.lock.username !== (state.user && state.user.username)) setControlOwnership(false);
  }
  scheduleRender();
}
function loadDynamicStatus(force=false){
  return Promise.allSettled([
    loadDevices(force),
    loadVideoPreferences(force),
    loadAlasStatus(force)
  ]);
}
function clearRefreshTimers(){
  if (state.calibrationTimer) clearTimeout(state.calibrationTimer);
  if (state.recoveryTimer) clearTimeout(state.recoveryTimer);
  state.calibrationTimer=null;
  state.recoveryTimer=null;
}
function scheduleCalibration(){
  if (state.calibrationTimer) clearTimeout(state.calibrationTimer);
  state.calibrationTimer=null;
  if (document.hidden || !state.eventConnected || state.pageLeaving) return;
  state.calibrationTimer=setTimeout(async()=>{
    state.calibrationTimer=null;
    if (!document.hidden && state.eventConnected) await loadDynamicStatus(true);
    scheduleCalibration();
  }, 60000);
}
function scheduleRecovery(){
  if (state.recoveryTimer) clearTimeout(state.recoveryTimer);
  state.recoveryTimer=null;
  if (document.hidden || state.eventConnected || state.pageLeaving) return;
  state.recoveryTimer=setTimeout(async()=>{
    state.recoveryTimer=null;
    if (!document.hidden && !state.eventConnected) await loadDynamicStatus(true);
    scheduleRecovery();
  }, 5000);
}
function syncRefreshLoops(){
  clearRefreshTimers();
  if (document.hidden) return;
  if (state.eventConnected) scheduleCalibration();
  else scheduleRecovery();
}
function openEvents(){
  if (state.pageLeaving || socketLive(state.eventWs)) return state.eventWs;
  if (state.eventReconnectTimer) { clearTimeout(state.eventReconnectTimer); state.eventReconnectTimer=null; }
  const token=++state.eventSeq;
  const ws=new WebSocket(wsUrl('/ws/events'));
  state.eventWs=ws;
  ws.onopen=()=>{
    if (state.eventSeq !== token || state.eventWs !== ws) return;
    state.eventConnected=true;
    syncRefreshLoops();
  };
  ws.onmessage = (event) => {
    if (state.eventSeq !== token || state.eventWs !== ws) return;
    try { applyEventMessage(JSON.parse(event.data)); } catch (_) {}
  };
  ws.onclose = () => {
    if (state.eventSeq !== token || state.eventWs !== ws) return;
    state.eventWs=null;
    state.eventConnected=false;
    syncRefreshLoops();
    if (!state.pageLeaving) state.eventReconnectTimer=setTimeout(openEvents, 2500);
  };
  return ws;
}
function closeEvents(){
  state.eventSeq+=1;
  const ws=state.eventWs;
  state.eventWs=null;
  state.eventConnected=false;
  if (state.eventReconnectTimer) clearTimeout(state.eventReconnectTimer);
  state.eventReconnectTimer=null;
  clearRefreshTimers();
  if (ws) { try { ws.close(); } catch (_) {} }
}
function openSidebar(){ $('sidebar').classList.add('open'); $('sidebarBackdrop').classList.add('open'); }
function closeSidebar(){ $('sidebar').classList.remove('open'); $('sidebarBackdrop').classList.remove('open'); }
$('startBtn').onclick=()=>startMirror().catch(e=>show(e.message));
$('stopBtn').onclick=()=>stopMirror().catch(e=>show(e.message));
$('controlBtn').onclick=()=>toggleControl().catch(e=>show(e.message));
$('refreshBtn').onclick=()=>loadAll({force:true}).catch(e=>show(e.message));
$('menuBtn').onclick=()=>openSidebar();
$('sidebarBackdrop').onclick=()=>closeSidebar();
$('toolBtn').onclick=()=>toggleToolPanel('tools');
$('backBtn').onclick=()=>sendKey(4);
$('homeBtn').onclick=()=>sendKey(3);
$('recentBtn').onclick=()=>sendKey(187);
$('alasBtn').onclick=()=>toggleToolPanel('alasTools');
$('accountBtn').onclick=()=>toggleToolPanel('accountTools');
$('changePasswordBtn').onclick=()=>changePassword().catch(e=>show(e.message));
$('alasToggleRun').onclick=()=>toggleAlas().catch(e=>show(e.message));
$('alasReload').onclick=()=>reloadAlas().catch(e=>show(e.message));
$('qualityStreamMode').onchange=()=>saveOrApplyQuality().catch(e=>show(e.message));
document.querySelectorAll('[data-fit]').forEach(btn=>btn.onclick=()=>{ state.fit=btn.dataset.fit; handleViewportResize(); render(); });
window.addEventListener('resize', handleViewportResize);
if (window.visualViewport) {
  window.visualViewport.addEventListener('resize', handleViewportResize);
  window.visualViewport.addEventListener('scroll', handleViewportResize);
}
if (window.ResizeObserver) {
  const stageResizeObserver = new ResizeObserver(handleViewportResize);
  stageResizeObserver.observe($('stage'));
}
window.addEventListener('blur', ()=>scheduleInactiveStop('window_blur'));
window.addEventListener('focus', ()=>cancelInactiveStop());
document.addEventListener('visibilitychange', ()=>{
  if (document.hidden) scheduleInactiveStop('document_hidden');
  else cancelInactiveStop();
  syncRefreshLoops();
});
window.addEventListener('pagehide', ()=>{
  state.pageLeaving=true;
  closeEvents();
  closeVideo();
});
window.addEventListener('pageshow', event=>{
  if (!event.persisted) return;
  state.pageLeaving=false;
  openEvents();
  loadAll({force:true}).catch(e=>show(e.message));
});
openEvents();
loadAll().catch(e=>show(e.message));
