const bootstrap = JSON.parse(document.getElementById("scrcpygate-bootstrap").textContent);
const csrfToken = bootstrap.csrf_token;
const SELECTED_KEY = 'webscrcpy:v2:selectedDeviceId';
const SIDEBAR_COLLAPSED_KEY = 'scrcpygate:mirror:sidebar-collapsed';
const MOBILE_SIDEBAR_QUERY = '(max-width: 960px)';
const state = {
  user:bootstrap.user || null, devices:[], sessions:{}, selectedDeviceId:localStorage.getItem(SELECTED_KEY) || '', activeDeviceId:'',
  videoWs:null, controlWs:null, eventWs:null, jmuxer:null, input:null, hasControl:false, fit:'contain', screen:{w:1280,h:720}, alas:null,
  videoConnected:false, controlConnected:false, videoPrefs:null, eventConnected:false, eventSeq:0, eventReconnectTimer:null, calibrationTimer:null, recoveryTimer:null,
  playerResetTimer:null, controlKeepaliveTimer:null, lastPlayerResetAt:0, lastDelayTrimAt:0, layoutFrame:null, renderFrame:null,
  idleStopTimer:null, idleStopReason:'', starting:false, lastStartAt:0, videoSeq:0, controlSeq:0, qualityProfile:'balanced', qualityApplying:false, pageLeaving:false,
  deviceNodes:new Map(), qualityNodes:new Map(), sessionRevision:0, sessionRevisions:new Map(),
  devicesLoaded:false, deviceLoading:true, deviceLoadError:'', deviceQuery:'', deviceFilter:'all', connectionPhase:'idle', mirrorError:'',
  sidebarTrigger:null, toolTrigger:null, sidebarCollapsed:false, controlRequest:null
};
const resourceRequests = Object.create(null);
const actionRequests = new Map();
const mobileSidebarMedia = window.matchMedia ? window.matchMedia(MOBILE_SIDEBAR_QUERY) : {matches:false};
const $ = (id) => document.getElementById(id);
function wsUrl(path){ return `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}${path}`; }
function getDeviceId(device){ return String((device && (device.device_id || device.id)) || '').trim(); }
function currentDevice(){ return state.devices.find(d => getDeviceId(d) === state.selectedDeviceId) || null; }
function selectedSession(){ const id=state.selectedDeviceId; const device=currentDevice(); return id ? (state.sessions[id] || (device && device.session) || null) : null; }
function deviceLabel(device){ return device ? (device.display_name || device.name || '设备') : '未选择设备'; }
function deviceSelectable(device){
  return !!(device && getDeviceId(device) && device.enabled !== false && device.can_view !== false && device.adb_state !== 'unauthorized');
}
function show(message, ms=3200){ const n=$('notice'); n.textContent=message || '操作失败'; n.classList.add('show'); clearTimeout(show.t); show.t=setTimeout(()=>n.classList.remove('show'),ms); }
function chip(text, cls=''){ const s=document.createElement('span'); s.className=`chip ${cls}`.trim(); s.textContent=text; return s; }
function actionBusy(key){ return actionRequests.has(key); }
function setActionBusy(element, busy, label){
  if (!element) return;
  if (window.ScrcpyGateUI && typeof window.ScrcpyGateUI.setBusy === 'function') {
    window.ScrcpyGateUI.setBusy(element, busy, label);
    return;
  }
  element.disabled=!!busy;
  element.toggleAttribute('aria-busy', !!busy);
}
async function runBusyAction(key, element, label, action){
  if (actionRequests.has(key)) return undefined;
  if (key === 'mirror') state.mirrorError='';
  setActionBusy(element, true, label);
  const request=Promise.resolve().then(action);
  actionRequests.set(key, request);
  scheduleRender();
  try {
    return await request;
  } catch (error) {
    if (key === 'mirror') state.mirrorError=(error && error.message) || '投屏操作失败';
    scheduleRender();
    throw error;
  } finally {
    if (actionRequests.get(key) === request) actionRequests.delete(key);
    setActionBusy(element, false);
    scheduleRender();
  }
}
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
  const previous=state.selectedDeviceId;
  const ids = state.devices.filter(deviceSelectable).map(getDeviceId);
  if (!ids.length) { state.selectedDeviceId=''; localStorage.removeItem(SELECTED_KEY); return; }
  if (!state.selectedDeviceId || !ids.includes(state.selectedDeviceId)) state.selectedDeviceId = ids[0];
  if (state.selectedDeviceId !== previous) state.mirrorError='';
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
  state.mirrorError='';
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
      state.qualityNodes.set(profile, button);
    }
    button.onclick=()=>runBusyAction('quality', button, '正在应用画质', ()=>chooseQualityProfile(profile)).catch(e=>show(e.message));
    button.textContent=qualityProfileLabel(profile);
    grid.insertBefore(button, label);
  });
}
function renderQualityButtons(){
  ensureQualityButtons();
  document.querySelectorAll('[data-profile]').forEach(btn=>{
    btn.classList.toggle('active', btn.dataset.profile === state.qualityProfile);
    btn.disabled = !!state.qualityApplying || actionBusy('quality');
    btn.title = state.qualityApplying || actionBusy('quality') ? '画质正在应用，请稍等' : '点击切换投屏画质';
  });
  const select = $('qualityStreamMode');
  if (select) select.disabled = !!state.qualityApplying || actionBusy('quality');
}
async function chooseQualityProfile(profile){
  if (state.qualityApplying) return show('画质正在应用，请稍等');
  state.qualityProfile = profile;
  renderQualityButtons();
  await saveOrApplyQuality();
}
function normalizedDeviceQuery(){ return state.deviceQuery.trim().toLocaleLowerCase('zh-CN'); }
function deviceMatchesFilters(device){
  const online=(device.adb_state || 'unknown') === 'online';
  if (state.deviceFilter === 'online' && !online) return false;
  const query=normalizedDeviceQuery();
  if (!query) return true;
  const searchable=[deviceLabel(device), device.name, device.display_name, getDeviceId(device)]
    .filter(Boolean)
    .join(' ')
    .toLocaleLowerCase('zh-CN');
  return searchable.includes(query);
}
function updateDeviceFilterControls(){
  const search=$('deviceSearch');
  if (search && search.value !== state.deviceQuery) search.value=state.deviceQuery;
  [['deviceFilterAll','all'], ['deviceFilterOnline','online']].forEach(([id, value])=>{
    const button=$(id);
    if (!button) return;
    const active=state.deviceFilter === value;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', active ? 'true' : 'false');
  });
}
function ensureDeviceEmpty(box){
  let empty=box.querySelector('[data-device-empty]');
  if (empty) return empty;
  empty=document.createElement('div');
  empty.className='ui-empty device-list-empty';
  empty.dataset.deviceEmpty='true';
  const title=document.createElement('strong');
  title.dataset.deviceEmptyTitle='true';
  const description=document.createElement('span');
  description.dataset.deviceEmptyDescription='true';
  empty.append(title, description);
  box.appendChild(empty);
  return empty;
}
function renderDeviceEmpty(empty, visibleCount){
  let mode='';
  let title='';
  let description='';
  if (!state.devicesLoaded && !state.devices.length) {
    mode='loading'; title='正在加载设备'; description='正在获取可访问设备和连接状态';
  } else if (state.deviceLoadError && !state.devices.length) {
    mode='error'; title='设备加载失败'; description=state.deviceLoadError;
  } else if (!state.devices.length) {
    mode=state.user && !state.user.is_admin ? 'no-permission' : 'no-devices';
    title=mode === 'no-permission' ? '没有可访问的设备' : '暂无设备';
    description=mode === 'no-permission' ? '请联系管理员分配观看权限' : '请先在后台添加并启用设备';
  } else if (!visibleCount) {
    mode='filtered-empty'; title='没有匹配的设备'; description='请调整搜索词或在线状态筛选';
  }
  empty.dataset.emptyState=mode || 'available';
  const titleNode=empty.querySelector('[data-device-empty-title]');
  const descriptionNode=empty.querySelector('[data-device-empty-description]');
  if (titleNode) titleNode.textContent=title;
  if (descriptionNode) descriptionNode.textContent=description;
  const hidden=!mode;
  empty.hidden=hidden;
  if (hidden) empty.style.display='none';
  else empty.style.removeProperty('display');
}
function renderDevices(){
  const box=$('devices');
  if (!box) return;
  box.setAttribute('aria-busy', state.deviceLoading ? 'true' : 'false');
  updateDeviceFilterControls();
  const empty=ensureDeviceEmpty(box);
  const activeIds=new Set(state.devices.map(getDeviceId).filter(Boolean));
  state.deviceNodes.forEach((button, id)=>{
    if (!activeIds.has(id)) {
      button.remove();
      state.deviceNodes.delete(id);
    }
  });
  let cursor=empty.nextSibling;
  let visibleCount=0;
  state.devices.forEach(device => {
    const id=getDeviceId(device); const session=state.sessions[id] || device.session; const active=id===state.selectedDeviceId;
    const adbState = device.adb_state || 'unknown';
    const adbBlocked = adbState === 'offline' || adbState === 'unauthorized';
    const visible=deviceMatchesFilters(device);
    if (visible) visibleCount+=1;
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
    btn.dataset.online=adbState === 'online' ? 'true' : 'false';
    btn.hidden=!visible;
    if (visible) btn.style.removeProperty('display');
    else btn.style.display='none';
    btn.disabled=!deviceSelectable(device);
    parts.name.textContent=deviceLabel(device);
    parts.status.textContent=mirrorVisible ? '投屏中' : (device.enabled ? adbStateLabel(adbState) : '禁用');
    parts.status.className=`chip ${mirrorVisible ? 'ok' : adbState === 'online' ? 'ok' : adbBlocked ? 'danger' : 'warn'}`;
    parts.control.textContent=device.can_control ? '可控制' : '只观看';
    parts.control.className=`chip ${device.can_control ? 'ok' : 'warn'}`;
    parts.adb.textContent=`ADB: ${adbStateLabel(adbState)}`;
    parts.adb.className=`chip ${adbState === 'online' ? 'ok' : adbBlocked ? 'danger' : 'warn'}`;
    parts.clients.textContent=`${Number((session && session.clients) || 0)} 个观看端`;
    parts.clients.hidden=!(session && session.clients);
    btn.setAttribute('aria-label', `${deviceLabel(device)}，${parts.status.textContent}，${parts.control.textContent}`);
    btn.onclick=()=>selectDevice(id);
    if (btn !== cursor) box.insertBefore(btn, cursor);
    cursor=btn.nextSibling;
  });
  renderDeviceEmpty(empty, visibleCount);
}
function controlOwnershipState(device, session){
  const lock=session && session.control_lock;
  const username=state.user && state.user.username;
  if (!device) return {text:'未选择设备', tone:'neutral'};
  if (!device.can_control) return {text:'仅可观看', tone:'neutral'};
  if (state.hasControl || (lock && lock.username === username)) return {text:'你正在控制', tone:'owned'};
  if (lock && lock.username) return {text:`${lock.username} 正在控制`, tone:'occupied'};
  if (actionBusy('control') || (state.controlWs && state.controlWs.readyState === WebSocket.CONNECTING)) return {text:'正在连接控制通道', tone:'pending'};
  return {text:'控制权空闲', tone:'available'};
}
function renderControlOwnership(device, session){
  const target=$('controlOwnership');
  const ownership=controlOwnershipState(device, session);
  if (!target) return ownership;
  target.textContent=ownership.text;
  target.dataset.state=ownership.tone;
  target.classList.toggle('ok', ownership.tone === 'owned' || ownership.tone === 'available');
  target.classList.toggle('warn', ownership.tone === 'occupied' || ownership.tone === 'pending');
  target.setAttribute('aria-label', `控制权：${ownership.text}`);
  return ownership;
}
function stageEmptyState(device, session){
  const running=!!(session && session.running);
  const health=String((session && session.stream_health) || '').toLowerCase();
  if (!state.devicesLoaded && !state.devices.length) return {type:'loading', title:'正在加载工作台', description:'正在获取设备和投屏状态'};
  if (state.deviceLoadError && !state.devices.length) return {type:'error', title:'设备加载失败', description:state.deviceLoadError};
  if (!device) {
    if (!state.devices.length && state.user && !state.user.is_admin) return {type:'no-permission', title:'没有可访问的设备', description:'请联系管理员分配观看权限'};
    if (!state.devices.length) return {type:'no-devices', title:'暂无设备', description:'请先在后台添加并启用设备'};
    return {type:'idle', title:'选择一台设备', description:'从左侧设备列表选择后开始投屏'};
  }
  if (state.mirrorError || ['failed','adb_failed','invalid_h264'].includes(health)) {
    return {type:'error', title:'投屏出现问题', description:state.mirrorError || `当前状态：${streamHealthLabel(health)}`};
  }
  if (state.starting || health === 'starting') return {type:'starting', title:'正在启动投屏', description:'正在连接设备并准备视频流'};
  if (state.qualityApplying || state.connectionPhase === 'reconnecting' || device.adb_state === 'reconnecting') {
    return {type:'reconnecting', title:'正在重新连接', description:'画面会在连接恢复后自动显示'};
  }
  if (state.connectionPhase === 'connecting' || (!state.videoConnected && socketLive(state.videoWs))) {
    return {type:'connecting', title:'正在连接画面', description:'正在建立视频通道'};
  }
  if (running) return {type:'disconnected', title:'画面尚未连接', description:'投屏正在运行，点击“连接画面”继续'};
  return {type:'idle', title:'尚未开始投屏', description:'确认设备在线后点击“开始投屏”'};
}
function ensureStageEmptyNodes(empty){
  let title=$('emptyTitle');
  let description=$('emptyDescription');
  if (title && description) return {title, description};
  title=document.createElement('strong');
  title.id='emptyTitle';
  description=document.createElement('span');
  description.id='emptyDescription';
  empty.replaceChildren(title, description);
  return {title, description};
}
function renderStageEmpty(device, session){
  const empty=$('empty');
  if (!empty) return;
  const view=stageEmptyState(device, session);
  const nodes=ensureStageEmptyNodes(empty);
  nodes.title.textContent=view.title;
  nodes.description.textContent=view.description;
  empty.dataset.emptyState=view.type;
  empty.setAttribute('role', view.type === 'error' ? 'alert' : 'status');
  empty.setAttribute('aria-live', view.type === 'error' ? 'assertive' : 'polite');
  const hidden=state.videoConnected;
  empty.hidden=hidden;
  if (hidden) empty.style.display='none';
  else empty.style.removeProperty('display');
}
function renderStatus(){
  const device=currentDevice(); const session=selectedSession();
  const running=!!(session && session.running);
  const localConnected=!!(state.videoConnected || state.controlConnected || socketLive(state.videoWs) || socketLive(state.controlWs));
  const selectedTitle=$('selectedTitle'); if (selectedTitle) selectedTitle.textContent = deviceLabel(device);
  const selectedMeta=$('selectedMeta'); if (selectedMeta) selectedMeta.textContent = device ? qualitySummary() : '从设备列表选择一个设备后开始投屏';
  const ownership=renderControlOwnership(device, session);
  const topStatus=$('topStatus'); if (topStatus) topStatus.textContent='';
  const items=[];
  items.push(chip(running ? '投屏中' : '未投屏', running ? 'ok' : 'warn'));
  items.push(chip(state.videoConnected ? '视频已连接' : socketLive(state.videoWs) ? '视频连接中' : '视频未连接', state.videoConnected ? 'ok' : 'warn'));
  if (!$('controlOwnership')) items.push(chip(ownership.text, ownership.tone === 'owned' || ownership.tone === 'available' ? 'ok' : 'warn'));
  if (session && session.video) items.push(chip(`流: ${session.video.max_size || '原始'} / ${session.video.max_fps || '不限'}fps`));
  if (session && session.stream_mode) items.push(chip(`流模式: ${streamModeShortLabel(session.stream_mode)}/${streamHealthLabel(session.stream_health)}`, session.stream_health === 'healthy' ? 'ok' : 'warn'));
  if (device && device.adb_state) items.push(chip(`ADB: ${adbStateLabel(device.adb_state)}`, device.adb_state === 'online' ? 'ok' : 'warn'));
  if (state.alas) items.push(chip(`ALAS: ${alasStatusLabel(state.alas.status)}`, state.alas.status === 'running' ? 'ok' : state.alas.status === 'error' ? 'warn' : ''));
  if (topStatus) items.forEach(item=>topStatus.appendChild(item));
  const mirrorBusy=actionBusy('mirror');
  const startBtn=$('startBtn');
  if (startBtn) {
    setButtonLabel(startBtn, !device ? '开始投屏' : state.starting ? '启动中...' : running ? (state.videoConnected ? '刷新画面' : '连接画面') : '开始投屏');
    startBtn.disabled = mirrorBusy || state.starting || state.qualityApplying || !deviceSelectable(device);
  }
  const controlBtn=$('controlBtn');
  if (controlBtn) {
    setButtonLabel(controlBtn, actionBusy('control') ? '处理中...' : state.hasControl ? '释放控制' : '获取控制');
    controlBtn.disabled = actionBusy('control') || state.starting || !deviceSelectable(device) || !device.can_control;
  }
  const stopBtn=$('stopBtn');
  if (stopBtn) stopBtn.disabled = mirrorBusy || state.starting || state.qualityApplying || !device || (!running && !localConnected);
  renderAlasPanel();
  document.querySelectorAll('[data-fit]').forEach(btn=>btn.classList.toggle('active', btn.dataset.fit === state.fit));
  renderQualityButtons();
  renderStageEmpty(device, session);
  const phoneVideo=$('phoneVideo'); if (phoneVideo) phoneVideo.style.objectFit = 'contain';
  scheduleLayout();
}
function setButtonLabel(button, text){
  const label=button && button.querySelector('.action-label');
  if (label) label.textContent=text;
  else if (button) button.textContent=text;
}
function render(){
  renderDevices();
  renderStatus();
  renderAccountPanel();
  const roleChip=$('roleChip'); if (roleChip) roleChip.textContent = state.user && state.user.is_admin ? '管理员' : '普通用户';
  const adminLink=$('adminLink'); if (adminLink) adminLink.hidden = !(state.user && state.user.is_admin);
}
function renderAlasPanel(){
  const box=$('alasPanelStatus'); if(!box) return; box.textContent='';
  const a=state.alas || {};
  box.appendChild(chip(a.config ? `配置 ${a.config}` : '未绑定', a.config ? 'ok' : 'warn'));
  box.appendChild(chip(alasStatusLabel(a.status), a.status === 'running' ? 'ok' : a.status === 'error' ? 'warn' : ''));
  box.appendChild(chip(a.can_run ? '可运行' : '不可运行', a.can_run ? 'ok' : 'warn'));
  if(a.error) box.appendChild(chip(a.error, 'danger'));
  $('alasToggleRun').textContent = a.status === 'error' ? '重启 ALAS' : a.status === 'running' ? '停止 ALAS' : '启动 ALAS';
  $('alasToggleRun').disabled = !a.can_run || actionBusy('alas');
}
function renderAccountPanel(){
  const box=$('accountInfo'); if(!box) return; box.textContent='';
  const user=state.user || {};
  box.appendChild(chip(user.username || '未登录', 'ok'));
  box.appendChild(chip(user.is_admin ? '管理员' : '普通用户'));
}
function setToolPanelActive(panel, active){
  if (!panel) return;
  panel.classList.toggle('open', active);
  panel.hidden=!active;
  panel.setAttribute('aria-hidden', active ? 'false' : 'true');
  panel.toggleAttribute('inert', !active);
}
function syncToolTriggerState(activePanelId){
  [['toolBtn','tools'], ['alasBtn','alasTools'], ['accountBtn','accountTools']].forEach(([buttonId, panelId])=>{
    const button=$(buttonId);
    if (button) button.setAttribute('aria-expanded', activePanelId === panelId ? 'true' : 'false');
  });
}
function closeStatusDetails(){
  const details=$('statusDetails');
  if (details) details.open=false;
}
function closeToolPanels(exceptId){
  ['tools','alasTools','accountTools'].forEach(id=>{
    const el=$(id);
    if (el && id !== exceptId) setToolPanelActive(el, false);
  });
}
function closeToolDrawer(){
  const drawer=$('workspaceDrawer');
  const trigger=state.toolTrigger;
  state.toolTrigger=null;
  syncToolTriggerState('');
  closeToolPanels();
  if (drawer) {
    drawer.classList.remove('open','is-open');
    drawer.hidden=true;
    drawer.setAttribute('aria-hidden','true');
    drawer.setAttribute('inert','');
  }
  const backdrop=$('toolDrawerBackdrop');
  if (backdrop) {
    backdrop.classList.remove('open','is-open');
    backdrop.hidden=true;
    backdrop.setAttribute('aria-hidden','true');
  }
  const app=document.querySelector('.app');
  if (app) app.removeAttribute('inert');
  if (trigger && trigger.isConnected) requestAnimationFrame(()=>trigger.focus({preventScroll:true}));
}
function openToolDrawer(id, trigger){
  const el=$(id);
  if (!el) return;
  const drawer=$('workspaceDrawer');
  state.toolTrigger=trigger || document.activeElement;
  syncToolTriggerState(id);
  closeStatusDetails();
  const title=$('workspaceDrawerTitle');
  if (title) title.textContent=({tools:'画面设置', alasTools:'ALAS', accountTools:'账户设置'}[id]) || '工作区工具';
  closeToolPanels(id);
  setToolPanelActive(el, true);
  if (!drawer) return;
  const app=document.querySelector('.app');
  if (app) app.setAttribute('inert','');
  drawer.hidden=false;
  drawer.removeAttribute('inert');
  drawer.setAttribute('aria-hidden','false');
  drawer.classList.add('open','is-open');
  const backdrop=$('toolDrawerBackdrop');
  if (backdrop) {
    backdrop.hidden=false;
    backdrop.setAttribute('aria-hidden','false');
    backdrop.classList.add('open','is-open');
  }
  const focusTarget=drawer.querySelector('[autofocus]') || visibleLayerFocusables(drawer)[0] || drawer;
  if (focusTarget === drawer && !drawer.hasAttribute('tabindex')) drawer.setAttribute('tabindex','-1');
  requestAnimationFrame(()=>focusTarget.focus({preventScroll:true}));
}
function toggleToolPanel(id, trigger){
  const el=$(id);
  if (!el) return;
  const drawer=$('workspaceDrawer');
  const drawerOpen=drawer && !drawer.hidden && (drawer.classList.contains('open') || drawer.classList.contains('is-open'));
  if (el.classList.contains('open') && (!drawer || drawerOpen)) {
    if (drawer) closeToolDrawer();
    else { setToolPanelActive(el, false); syncToolTriggerState(''); }
    return;
  }
  openToolDrawer(id, trigger);
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
  state.deviceLoading=true;
  if (!state.devicesLoaded) scheduleRender();
  const requestRevision=state.sessionRevision;
  const request=requestResource('devices', '/api/devices', data=>{
    state.devices=data.devices || [];
    applySessionSnapshot(state.devices, data.sessions || {}, requestRevision);
    normalizeSelection();
    state.devicesLoaded=true;
    state.deviceLoading=false;
    state.deviceLoadError='';
    scheduleRender();
  }, {force});
  return request.catch(error=>{
    state.devicesLoaded=true;
    state.deviceLoading=false;
    state.deviceLoadError=(error && error.message) || '无法获取设备列表';
    scheduleRender();
    throw error;
  });
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
    if (data.ok === false) {
      state.mirrorError=data.detail || data.error || '投屏启动失败，请检查 ADB 连接';
      render();
      return show(state.mirrorError, 5200);
    }
    if (state.activeDeviceId === id && socketLive(state.videoWs)) {
      schedulePlayerReset();
      if (!socketLive(state.controlWs)) openControl(id);
      show('投屏已在运行，已刷新播放器');
      return;
    }
    reconnectSockets(id, 'connecting');
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
  state.connectionPhase='idle';
  state.mirrorError='';
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
      reconnectSockets(id, 'reconnecting');
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
function settleControlRequest(error){
  const pending=state.controlRequest;
  if (!pending) return;
  state.controlRequest=null;
  clearTimeout(pending.timer);
  if (error) pending.reject(error);
  else pending.resolve();
}
function closeControlSocket(){
  state.controlSeq += 1;
  const ws = state.controlWs;
  state.controlWs = null;
  state.controlConnected = false;
  setControlOwnership(false);
  destroyInput();
  if (ws) { try { ws.close(); } catch(_){} }
  settleControlRequest(new Error('控制通道已关闭'));
}
function closeVideo(){
  cancelInactiveStop();
  closeVideoSocket();
  closeControlSocket();
  state.activeDeviceId='';
  state.connectionPhase='idle';
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
  state.connectionPhase = 'idle';
  await new Promise(resolve=>setTimeout(resolve, 300));
  const result = await fetchJson(`/api/devices/${encodeURIComponent(id)}/mirror/idle-stop`, {method:'POST', body:{reason:reason || 'inactive'}});
  replaceMutationSessions(result.sessions);
  show(result.stopped ? '页面长时间未聚焦，投屏已自动停止' : '页面长时间未聚焦，已停止本浏览器观看');
  render();
}
function reconnectSockets(id, phase='connecting'){
  closeVideo();
  state.activeDeviceId=id;
  state.connectionPhase=phase;
  openVideo(id, {force:true, phase});
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
  state.connectionPhase=options.phase || 'connecting';
  state.mirrorError='';
  const token = ++state.videoSeq;
  const video=$('phoneVideo');
  state.jmuxer = new JMuxer(jmuxerConfig(video));
  const ws = new WebSocket(wsUrl(`/ws/devices/${encodeURIComponent(id)}/video`));
  state.videoWs = ws;
  ws.binaryType='arraybuffer';
  ws.onopen=()=>{ if (state.videoSeq !== token || state.videoWs !== ws) return; state.videoConnected=true; state.connectionPhase='connected'; state.mirrorError=''; render(); requestVideoPrime(ws); setTimeout(()=>{ if (state.videoSeq === token && state.videoWs === ws) requestVideoPrime(ws); }, 450); if (document.hidden || !document.hasFocus()) scheduleInactiveStop('open_in_background'); };
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
  ws.onerror=()=>{ if (state.videoSeq === token && state.videoWs === ws) { state.connectionPhase='error'; state.mirrorError='视频通道连接失败'; show(state.mirrorError); render(); } };
  ws.onclose = () => { if (state.videoSeq !== token || state.videoWs !== ws) return; state.videoConnected=false; state.videoWs=null; state.connectionPhase=selectedSession() && selectedSession().running ? 'disconnected' : 'idle'; render(); };
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
      if (msg.ok !== undefined) {
        if (state.controlRequest && state.controlRequest.ws === ws && state.controlRequest.kind === 'acquire') {
          if (msg.ok) settleControlRequest();
          else settleControlRequest(new Error(msg.owner ? `控制权正由 ${msg.owner} 使用` : '暂时无法获取控制权'));
        }
      }
    }
    if (msg.type === 'control_released') {
      setControlOwnership(false);
      if (state.controlRequest && state.controlRequest.ws === ws && state.controlRequest.kind === 'release') {
        if (msg.ok === false) settleControlRequest(new Error('释放控制权失败'));
        else settleControlRequest();
      }
    }
    if (msg.type === 'control_error') {
      const error=new Error(msg.error || '控制失败');
      settleControlRequest(error);
      show(error.message);
    }
    render();
  };
  ws.onerror=()=>{ if (state.controlSeq === token && state.controlWs === ws) { settleControlRequest(new Error('控制通道连接失败')); show('控制通道连接失败'); } };
  ws.onclose = () => { if (state.controlSeq !== token || state.controlWs !== ws) return; state.controlConnected=false; state.controlWs=null; setControlOwnership(false); destroyInput(); render(); settleControlRequest(new Error('控制通道已关闭')); };
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
  const stage=$('stage'); const area=$('screenArea') || stage; const wrap=$('videoWrap'); if (!area || !wrap) return;
  const style=getComputedStyle(area);
  const availW=Math.max(1, area.clientWidth - parseFloat(style.paddingLeft || 0) - parseFloat(style.paddingRight || 0));
  const availH=Math.max(1, area.clientHeight - parseFloat(style.paddingTop || 0) - parseFloat(style.paddingBottom || 0));
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
  await waitForSocketOpen(ws);
  if (state.controlWs !== ws || ws.readyState !== WebSocket.OPEN) throw new Error('控制通道未连接');
  const releasing=state.hasControl;
  if (releasing) setControlOwnership(false);
  const response=waitForControlResponse(ws, releasing ? 'release' : 'acquire');
  try {
    ws.send(JSON.stringify({type: releasing ? 'release_control' : 'acquire_control', force:false}));
  } catch (error) {
    settleControlRequest(error);
  }
  await response;
}
function waitForSocketOpen(ws, timeoutMs=8000){
  if (ws.readyState === WebSocket.OPEN) return Promise.resolve();
  if (ws.readyState !== WebSocket.CONNECTING) return Promise.reject(new Error('控制通道未连接'));
  return new Promise((resolve, reject)=>{
    let timer=null;
    const cleanup=()=>{
      clearTimeout(timer);
      ws.removeEventListener('open', onOpen);
      ws.removeEventListener('error', onError);
      ws.removeEventListener('close', onClose);
    };
    const onOpen=()=>{ cleanup(); resolve(); };
    const onError=()=>{ cleanup(); reject(new Error('控制通道连接失败')); };
    const onClose=()=>{ cleanup(); reject(new Error('控制通道已关闭')); };
    timer=setTimeout(()=>{ cleanup(); reject(new Error('控制通道连接超时')); }, timeoutMs);
    ws.addEventListener('open', onOpen, {once:true});
    ws.addEventListener('error', onError, {once:true});
    ws.addEventListener('close', onClose, {once:true});
  });
}
function waitForControlResponse(ws, kind){
  return new Promise((resolve, reject)=>{
    const timer=setTimeout(()=>{
      if (state.controlRequest && state.controlRequest.ws === ws) settleControlRequest(new Error('控制权操作超时'));
    }, 8000);
    state.controlRequest={ws, kind, resolve, reject, timer};
  });
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
function persistSidebarCollapsed(){
  try { localStorage.setItem(SIDEBAR_COLLAPSED_KEY, state.sidebarCollapsed ? 'true' : 'false'); } catch (_) {}
}
function setSidebarCollapsed(collapsed, persist=true){
  state.sidebarCollapsed=!!collapsed;
  const app=document.querySelector('.app');
  if (app) app.classList.toggle('sidebar-collapsed', state.sidebarCollapsed && !mobileSidebarMedia.matches);
  const button=$('sidebarCollapseBtn');
  if (button) {
    button.setAttribute('aria-expanded', state.sidebarCollapsed ? 'false' : 'true');
    button.setAttribute('aria-label', state.sidebarCollapsed ? '展开设备栏' : '收起设备栏');
    button.title=state.sidebarCollapsed ? '展开设备栏' : '收起设备栏';
  }
  if (persist) persistSidebarCollapsed();
  scheduleLayout();
}
function syncSidebarAccessibility(){
  const sidebar=$('sidebar');
  const backdrop=$('sidebarBackdrop');
  const menu=$('menuBtn');
  if (!sidebar) return;
  if (!mobileSidebarMedia.matches) {
    sidebar.classList.remove('open');
    sidebar.removeAttribute('inert');
    sidebar.setAttribute('aria-hidden','false');
    if (backdrop) {
      backdrop.classList.remove('open');
      backdrop.hidden=true;
      backdrop.setAttribute('aria-hidden','true');
    }
    if (menu) menu.setAttribute('aria-expanded','false');
    const viewer=document.querySelector('.viewer');
    if (viewer) viewer.removeAttribute('inert');
    setSidebarCollapsed(state.sidebarCollapsed, false);
    return;
  }
  const open=sidebar.classList.contains('open');
  const app=document.querySelector('.app'); if (app) app.classList.remove('sidebar-collapsed');
  sidebar.toggleAttribute('inert', !open);
  sidebar.setAttribute('aria-hidden', open ? 'false' : 'true');
  if (backdrop) {
    backdrop.classList.toggle('open', open);
    backdrop.hidden=!open;
    backdrop.setAttribute('aria-hidden', open ? 'false' : 'true');
  }
  if (menu) menu.setAttribute('aria-expanded', open ? 'true' : 'false');
  const viewer=document.querySelector('.viewer');
  if (viewer) viewer.toggleAttribute('inert', open);
  const collapseButton=$('sidebarCollapseBtn');
  if (collapseButton) {
    collapseButton.setAttribute('aria-expanded', open ? 'true' : 'false');
    collapseButton.setAttribute('aria-label','关闭设备栏');
    collapseButton.title='关闭设备栏';
  }
}
function openSidebar(trigger){
  if (!mobileSidebarMedia.matches) {
    setSidebarCollapsed(false);
    return;
  }
  const sidebar=$('sidebar');
  if (!sidebar) return;
  closeStatusDetails();
  state.sidebarTrigger=trigger || document.activeElement;
  sidebar.classList.add('open');
  syncSidebarAccessibility();
  const search=$('deviceSearch');
  const selectedButton=state.deviceNodes.get(state.selectedDeviceId);
  const searchVisible=search && search.getClientRects().length > 0;
  const focusTarget=searchVisible ? search : (selectedButton && !selectedButton.disabled ? selectedButton : visibleLayerFocusables(sidebar)[0]) || sidebar;
  if (focusTarget === sidebar && !sidebar.hasAttribute('tabindex')) sidebar.setAttribute('tabindex','-1');
  if (focusTarget) requestAnimationFrame(()=>{
    if (focusTarget === selectedButton && typeof selectedButton.scrollIntoView === 'function') selectedButton.scrollIntoView({block:'nearest'});
    focusTarget.focus({preventScroll:true});
  });
}
function closeSidebar(options={}){
  if (!mobileSidebarMedia.matches) return;
  const sidebar=$('sidebar');
  if (!sidebar) return;
  const trigger=state.sidebarTrigger;
  state.sidebarTrigger=null;
  sidebar.classList.remove('open');
  syncSidebarAccessibility();
  if (options.restoreFocus !== false && trigger && trigger.isConnected) requestAnimationFrame(()=>trigger.focus({preventScroll:true}));
}
function bindClick(id, handler){
  const element=$(id);
  if (element) element.onclick=(event)=>handler(event, element);
}
function visibleLayerFocusables(layer){
  if (!layer) return [];
  return Array.from(layer.querySelectorAll(
    'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
  )).filter(node=>!node.closest('[hidden], [inert], [aria-hidden="true"]') && node.getClientRects().length > 0);
}
function trapLayerFocus(event, layer){
  if (event.key !== 'Tab' || !layer) return false;
  const focusable=visibleLayerFocusables(layer);
  if (!focusable.length) {
    event.preventDefault();
    layer.focus({preventScroll:true});
    return true;
  }
  const first=focusable[0];
  const last=focusable[focusable.length - 1];
  if (!layer.contains(document.activeElement)) {
    event.preventDefault();
    (event.shiftKey ? last : first).focus({preventScroll:true});
    return true;
  }
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus({preventScroll:true});
    return true;
  }
  if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus({preventScroll:true});
    return true;
  }
  return false;
}
function initializeWorkspaceInteractions(){
  try { state.sidebarCollapsed=localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === 'true'; } catch (_) { state.sidebarCollapsed=false; }
  syncSidebarAccessibility();
  closeToolDrawer();
  bindClick('startBtn', (_, button)=>runBusyAction('mirror', button, '正在启动投屏', startMirror).catch(e=>show(e.message)));
  bindClick('stopBtn', (_, button)=>runBusyAction('mirror', button, '正在停止投屏', stopMirror).catch(e=>show(e.message)));
  bindClick('controlBtn', (_, button)=>runBusyAction('control', button, '正在更新控制权', toggleControl).catch(e=>show(e.message)));
  bindClick('refreshBtn', (_, button)=>runBusyAction('refresh', button, '正在刷新', ()=>loadAll({force:true})).catch(e=>show(e.message)));
  bindClick('menuBtn', (_, button)=>openSidebar(button));
  bindClick('sidebarCollapseBtn', ()=>{ if (mobileSidebarMedia.matches) closeSidebar(); else setSidebarCollapsed(!state.sidebarCollapsed); });
  bindClick('sidebarBackdrop', ()=>closeSidebar());
  bindClick('toolBtn', (_, button)=>toggleToolPanel('tools', button));
  bindClick('backBtn', ()=>sendKey(4));
  bindClick('homeBtn', ()=>sendKey(3));
  bindClick('recentBtn', ()=>sendKey(187));
  bindClick('alasBtn', (_, button)=>toggleToolPanel('alasTools', button));
  bindClick('accountBtn', (_, button)=>toggleToolPanel('accountTools', button));
  bindClick('toolDrawerCloseBtn', ()=>closeToolDrawer());
  bindClick('toolDrawerBackdrop', ()=>closeToolDrawer());
  bindClick('changePasswordBtn', (_, button)=>runBusyAction('password', button, '正在修改密码', changePassword).catch(e=>show(e.message)));
  bindClick('alasToggleRun', (_, button)=>runBusyAction('alas', button, '正在更新 ALAS', toggleAlas).catch(e=>show(e.message)));
  bindClick('alasReload', (_, button)=>runBusyAction('alas', button, '正在刷新 ALAS', reloadAlas).catch(e=>show(e.message)));
  const qualityStreamMode=$('qualityStreamMode');
  if (qualityStreamMode) qualityStreamMode.onchange=()=>runBusyAction('quality', qualityStreamMode, '正在应用画质', saveOrApplyQuality).catch(e=>show(e.message));
  const search=$('deviceSearch');
  if (search) search.addEventListener('input', event=>{ state.deviceQuery=event.currentTarget.value || ''; scheduleRender(); });
  bindClick('deviceFilterAll', ()=>{ state.deviceFilter='all'; scheduleRender(); });
  bindClick('deviceFilterOnline', ()=>{ state.deviceFilter='online'; scheduleRender(); });
  document.addEventListener('keydown', event=>{
    const drawer=$('workspaceDrawer');
    const drawerOpen=drawer && !drawer.hidden && drawer.classList.contains('open');
    const sidebar=$('sidebar');
    const sidebarOpen=mobileSidebarMedia.matches && sidebar && sidebar.classList.contains('open');
    if (event.key === 'Tab') {
      if (drawerOpen) trapLayerFocus(event, drawer);
      else if (sidebarOpen) trapLayerFocus(event, sidebar);
      return;
    }
    if (event.key !== 'Escape') return;
    if (drawerOpen) {
      event.preventDefault();
      closeToolDrawer();
      return;
    }
    if (sidebarOpen) {
      event.preventDefault();
      closeSidebar();
      return;
    }
    const details=$('statusDetails');
    if (details && details.open) { event.preventDefault(); details.open=false; }
  });
  render();
}
initializeWorkspaceInteractions();
document.querySelectorAll('[data-fit]').forEach(btn=>btn.onclick=()=>{ state.fit=btn.dataset.fit; handleViewportResize(); render(); });
window.addEventListener('resize', handleViewportResize);
if (window.visualViewport) {
  window.visualViewport.addEventListener('resize', handleViewportResize);
  window.visualViewport.addEventListener('scroll', handleViewportResize);
}
if (window.ResizeObserver) {
  const stageResizeObserver = new ResizeObserver(handleViewportResize);
  const layoutTarget=$('screenArea') || $('stage');
  if (layoutTarget) stageResizeObserver.observe(layoutTarget);
}
if (mobileSidebarMedia.addEventListener) mobileSidebarMedia.addEventListener('change', syncSidebarAccessibility);
else if (mobileSidebarMedia.addListener) mobileSidebarMedia.addListener(syncSidebarAccessibility);
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
