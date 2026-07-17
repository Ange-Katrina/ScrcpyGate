const bootstrap = JSON.parse(document.getElementById("scrcpygate-bootstrap").textContent);
const csrfToken = bootstrap.csrf_token;
const SELECTED_KEY = 'scrcpygate:selectedDeviceId';
const SIDEBAR_COLLAPSED_KEY = 'scrcpygate:mirror:sidebar-collapsed';
const ALAS_CONFIG_KEY_PREFIX = 'scrcpygate:alas:selected-config:';
const MOBILE_SIDEBAR_QUERY = '(max-width: 960px)';
const FULLSCREEN_MIN_MAX_SIZE = 1280;
const state = {
  user:bootstrap.user || null, devices:[], sessions:{}, selectedDeviceId:localStorage.getItem(SELECTED_KEY) || '', activeDeviceId:'',
  videoWs:null, controlWs:null, eventWs:null, jmuxer:null, input:null, hasControl:false, fit:'contain', screen:{w:1280,h:720}, alas:null,
  alasConfigs:[], alasConfigsLoaded:false, alasConfigsLoading:false, alasConfigsError:'', alasCatalogRevision:0, selectedAlasConfig:'', alasStatusLoading:false, alasStatusError:'', alasSwitching:false, alasStatusRefreshPending:false, alasStatusEpoch:0, alasOperationSeq:0,
  videoConnected:false, controlConnected:false, videoPrefs:null, eventConnected:false, eventSeq:0, eventReconnectTimer:null, calibrationTimer:null, recoveryTimer:null,
  playerResetTimer:null, playerSeq:0, streamGeneration:0, videoReconnectTimer:null, videoReconnectAttempts:0, controlReconnectTimer:null, controlReconnectAttempts:0, controlKeepaliveTimer:null, lastPlayerResetAt:0, lastKeyframeRequestAt:0, lastDelayTrimAt:0, videoSpsSignature:'', videoReconfiguring:false, layoutFrame:null, renderFrame:null,
  idleStopTimer:null, idleStopReason:'', starting:false, lastStartAt:0, videoSeq:0, controlSeq:0, qualityProfile:'balanced', qualityApplying:false, qualityPromise:null, pendingQualityPayload:null, pageLeaving:false, resumeDeviceId:'',
  deviceNodes:new Map(), qualityNodes:new Map(), sessionRevision:0, sessionRevisions:new Map(),
  devicesLoaded:false, deviceLoading:true, deviceLoadError:'', deviceQuery:'', deviceFilter:'all', connectionPhase:'idle', mirrorError:'',
  sidebarTrigger:null, toolTrigger:null, sidebarCollapsed:false, controlRequest:null,
  immersive:false, systemFullscreen:false, immersiveRailOpen:false, immersiveTrigger:null, immersiveEpoch:0, fullscreenQualityEpoch:0, fullscreenQualityPromise:null
};
const resourceRequests = Object.create(null);
const actionRequests = new Map();
const mobileSidebarMedia = window.matchMedia ? window.matchMedia(MOBILE_SIDEBAR_QUERY) : {matches:false};
const $ = (id) => document.getElementById(id);
function alasConfigStorageKey(){
  const username=String((state.user && state.user.username) || '').trim();
  return username ? `${ALAS_CONFIG_KEY_PREFIX}${encodeURIComponent(username)}` : '';
}
function readStoredAlasConfig(){
  const key=alasConfigStorageKey();
  if (!key) return '';
  try { return String(localStorage.getItem(key) || '').trim(); } catch (_) { return ''; }
}
function persistAlasConfig(configName){
  const key=alasConfigStorageKey();
  if (!key) return;
  try {
    if (configName) localStorage.setItem(key, configName);
    else localStorage.removeItem(key);
  } catch (_) {}
}
function wsUrl(path){ return `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}${path}`; }
function getDeviceId(device){ return String((device && (device.device_id || device.id)) || '').trim(); }
function currentDevice(){ return state.devices.find(d => getDeviceId(d) === state.selectedDeviceId) || null; }
function selectedSession(){ const id=state.selectedDeviceId; const device=currentDevice(); return id ? (state.sessions[id] || (device && device.session) || null) : null; }
function deviceLabel(device){ return device ? (device.display_name || device.name || '设备') : '未选择设备'; }
function deviceIdentifier(device){
  if (!device) return '';
  const primary=String(deviceLabel(device) || '').trim();
  return [device.name, getDeviceId(device)]
    .map(value=>String(value || '').trim())
    .find(value=>value && value !== primary && !/^dev_[a-f0-9]+$/i.test(value)) || '';
}
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
    const completed=actionRequests.get(key) === request;
    if (completed) actionRequests.delete(key);
    setActionBusy(element, false);
    if (key === 'alas' && completed) flushPendingAlasStatusRefresh();
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
const NORMAL_PROFILE_NAMES = ['smooth','balanced','sharp','low_latency'];
const BUILTIN_PROFILE_LABELS = {smooth:'流畅', balanced:'稳定', sharp:'高清', low_latency:'低延迟'};
const ADB_STATE_LABELS = {online:'在线', offline:'离线', unauthorized:'未授权', reconnecting:'重连中', unknown:'未知'};
const STREAM_HEALTH_LABELS = {healthy:'正常', idle:'空闲', starting:'启动中', config:'等待配置', invalid_h264:'视频异常', adb_failed:'ADB 失败', failed:'失败', stopped:'已停止', unknown:'未知'};
const ALAS_STATUS_LABELS = {running:'运行中', stopped:'已停止', idle:'空闲', disabled:'服务未启用', disconnected:'未连接', error:'异常', unavailable:'不可达', unbound:'未授权', invalid_config:'配置无效', unknown:'未知'};
function labelFrom(map, value, fallback){
  const key = String(value || '').trim();
  return map[key] || fallback || key || '未知';
}
function adbStateLabel(value){ return labelFrom(ADB_STATE_LABELS, value, '未知'); }
function streamModeLabel(mode){ return ({raw:'原始流 raw', protocol:'协议流 protocol', legacy:'诊断 legacy'}[mode]) || mode; }
function streamModeShortLabel(mode){ return ({raw:'原始流', protocol:'协议流', legacy:'诊断流', none:'无'}[mode]) || mode || '未知'; }
function maxSizeQualityLabel(value){ const size=Number(value); if(!Number.isFinite(size) || size<=0) return '原始尺寸'; const height=Math.round(size*9/16); const labels={854:'480p',960:'540p',1280:'720p',1600:'900p',1920:'1080p'}; return `${size} × ${height}${labels[size] ? `（${labels[size]}）` : ''}`; }
function streamHealthLabel(value){ return labelFrom(STREAM_HEALTH_LABELS, value, '未知'); }
function alasStatusLabel(value){ return labelFrom(ALAS_STATUS_LABELS, value, '未知'); }
function qualitySummary(){
  const q = qualityPayload();
  const fps = q.max_fps > 0 ? q.max_fps : '不限';
  return `上限 ${maxSizeQualityLabel(q.max_size)} / ${Math.round(q.video_bit_rate / 100000) / 10}Mbps / ${fps}fps / ${streamModeShortLabel(q.scrcpy_stream_mode || 'raw')}`;
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
  const preset = (presets && presets[profile]) || (presets && presets.balanced) || {video_bit_rate:2400000, max_size:1280, max_fps:24};
  const modes = enabledStreamModes();
  return {
    profile,
    adaptive:false,
    video_bit_rate:Number(preset.video_bit_rate || 2400000),
    max_size:Number(preset.max_size == null ? 1280 : preset.max_size),
    max_fps:Number(preset.max_fps == null ? 24 : preset.max_fps),
    scrcpy_stream_mode:modes.includes($('qualityStreamMode').value) ? $('qualityStreamMode').value : modes[0]
  };
}
function applyQualityToForm(options){
  const q = options || (state.videoPrefs && state.videoPrefs.effective) || {profile:'balanced', adaptive:false, video_bit_rate:2400000, max_size:1280, max_fps:24};
  const profiles = (state.videoPrefs && state.videoPrefs.profiles) || {};
  state.qualityProfile = profiles[q.profile] ? q.profile : 'balanced';
  renderQualityStreamMode(q.scrcpy_stream_mode || 'raw');
  renderQualityButtons();
}
function qualityProfileOrder(){
  return NORMAL_PROFILE_NAMES.slice();
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
  const profiles=(state.videoPrefs && state.videoPrefs.profiles) || {};
  const ready=qualityProfileOrder().every(name=>profiles[name]);
  const grid=$('qualityProfiles');
  if(grid) grid.setAttribute('aria-busy', ready ? 'false' : 'true');
  document.querySelectorAll('[data-profile]').forEach(btn=>{
    btn.classList.toggle('active', btn.dataset.profile === state.qualityProfile);
    btn.disabled = !ready || !!state.qualityApplying || actionBusy('quality');
    btn.title = !ready ? '正在加载画质档位' : state.qualityApplying || actionBusy('quality') ? '画质正在应用，请稍等' : '点击切换投屏画质';
  });
  const select = $('qualityStreamMode');
  if (select) select.disabled = !ready || !!state.qualityApplying || actionBusy('quality');
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
      const title=document.createElement('span'); title.className='device-title';
      const presence=document.createElement('span'); presence.className='device-presence'; presence.setAttribute('aria-hidden','true');
      const nameBlock=document.createElement('span'); nameBlock.className='device-name-block';
      const name=document.createElement('strong');
      const identifier=document.createElement('span'); identifier.className='device-identifier';
      const status=document.createElement('span'); status.className='device-status';
      const summary=document.createElement('span'); summary.className='device-card-summary';
      nameBlock.append(name, identifier);
      title.append(presence, nameBlock, status);
      btn.append(title, summary);
      btn._scrcpygate={name,identifier,presence,status,summary};
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
    const identifier=deviceIdentifier(device);
    parts.identifier.textContent=identifier;
    parts.identifier.hidden=!identifier;
    const statusText=mirrorVisible ? '投屏中' : (device.enabled === false ? '已禁用' : adbStateLabel(adbState));
    const statusTone=mirrorVisible ? 'ok' : device.enabled === false ? 'muted' : adbState === 'online' ? 'ok' : adbBlocked ? 'danger' : 'warn';
    parts.status.textContent=statusText;
    parts.status.dataset.tone=statusTone;
    parts.presence.dataset.tone=statusTone;
    btn.dataset.status=statusTone;
    const viewers=Number((session && session.clients) || 0);
    const summaryParts=[
      device.enabled === false ? '设备已停用' : adbState === 'unauthorized' ? '等待设备端授权' : device.can_control ? '可控制' : '仅观看'
    ];
    if (viewers > 0) summaryParts.push(`${viewers} 个观看端`);
    parts.summary.textContent=summaryParts.join(' · ');
    btn.setAttribute('aria-label', `${deviceLabel(device)}，${statusText}，${parts.summary.textContent}`);
    btn.onclick=()=>selectDevice(id);
    if (btn !== cursor) box.insertBefore(btn, cursor);
    cursor=btn.nextSibling;
  });
  const summary=$('deviceSummary');
  if (summary) {
    const total=state.devices.length;
    const summaryText=!state.devicesLoaded && !total ? '加载中' : (visibleCount === total ? `${total} 台` : `${visibleCount}/${total} 台`);
    if (summary.textContent !== summaryText) summary.textContent=summaryText;
  }
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
  if (session && session.video) items.push(chip(`流: ${maxSizeQualityLabel(session.video.max_size)} / ${session.video.max_fps || '不限'}fps`));
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
  const stopDisabled=mirrorBusy || state.starting || state.qualityApplying || !device || (!running && !localConnected);
  const stopBtn=$('stopBtn');
  if (stopBtn) stopBtn.disabled = stopDisabled;
  const immersiveStopBtn=$('immersiveStopBtn');
  if (immersiveStopBtn) immersiveStopBtn.disabled = stopDisabled;
  renderKeyboardControl();
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
function currentAlasBinding(){
  return state.alasConfigs.find(item=>item.config_name === state.selectedAlasConfig) || null;
}
function syncAlasConfigSelect(){
  const select=$('alasConfigSelect');
  if (!select) return;
  const revision=String(state.alasCatalogRevision);
  if (select.dataset.catalogRevision !== revision) {
    const allowed=new Set(state.alasConfigs.map(item=>item.config_name));
    const existing=new Map(Array.from(select.options).map(option=>[option.value, option]));
    existing.forEach((option, value)=>{ if (!allowed.has(value)) option.remove(); });
    state.alasConfigs.forEach(binding=>{
      let option=existing.get(binding.config_name);
      if (!option) {
        option=document.createElement('option');
        option.value=binding.config_name;
      }
      option.textContent=`${binding.config_name}${binding.is_default ? '（默认）' : ''}`;
      select.appendChild(option);
    });
    select.dataset.catalogRevision=revision;
  }
  if (select.value !== state.selectedAlasConfig) select.value=state.selectedAlasConfig;
  const disabled=actionBusy('alas');
  if (select.disabled !== disabled) select.disabled=disabled;
  const busyValue=state.alasStatusLoading ? 'true' : 'false';
  if (select.getAttribute('aria-busy') !== busyValue) select.setAttribute('aria-busy', busyValue);
}
function setAlasPanelMessage(element, message, tone=''){
  if (!element) return;
  element.classList.remove('is-config-summary');
  element.classList.add('is-message');
  element.textContent=message;
  if (tone) element.dataset.tone=tone;
  else element.removeAttribute('data-tone');
}
function renderAlasConfigSummary(element, configName){
  if(!element) return;
  element.textContent='';
  element.classList.remove('is-message');
  element.classList.add('is-config-summary');
  element.dataset.tone='ok';
  const dot=document.createElement('span');
  dot.className='alas-config-state__dot';
  dot.setAttribute('aria-hidden','true');
  const copy=document.createElement('span');
  copy.className='alas-config-state__copy';
  const label=document.createElement('span');
  label.className='alas-config-state__label';
  label.textContent='当前配置';
  const name=document.createElement('strong');
  name.className='alas-config-state__name';
  name.textContent=configName;
  copy.append(label,name);
  element.append(dot,copy);
}
function renderAlasPanel(){
  const box=$('alasPanelStatus');
  if(!box) return;
  const binding=currentAlasBinding();
  const a=state.alas && state.alas.config === state.selectedAlasConfig ? state.alas : {};
  const picker=$('alasConfigPicker');
  const configState=$('alasConfigState');
  const runtimeState=$('alasRuntimeState');
  const configCount=state.alasConfigs.length;
  const showPicker=state.alasConfigsLoaded && !state.alasConfigsError && configCount > 1;
  if (picker) picker.hidden=!showPicker;
  syncAlasConfigSelect();
  if (configState) {
    configState.hidden=showPicker;
    if (state.alasConfigsLoading && !state.alasConfigsLoaded) setAlasPanelMessage(configState, '正在读取可用配置…');
    else if (state.alasConfigsError) setAlasPanelMessage(configState, '暂时无法读取授权配置', 'danger');
    else if (!state.alasConfigsLoaded) setAlasPanelMessage(configState, '打开面板后加载你的 ALAS 配置');
    else if (!configCount) setAlasPanelMessage(configState, '未授权任何 ALAS 配置', 'warn');
    else renderAlasConfigSummary(configState,state.selectedAlasConfig);
  }

  box.textContent='';
  if (binding) {
    box.appendChild(chip(binding.is_default ? '默认配置' : '已授权'));
    box.appendChild(chip(binding.can_run ? '可启停' : '仅查看运行状态', binding.can_run ? 'ok' : 'warn'));
    box.appendChild(chip(binding.can_edit ? '可编辑配置' : '不可编辑配置', binding.can_edit ? 'ok' : ''));
  }
  if (a.status) box.appendChild(chip(alasStatusLabel(a.status), a.status === 'running' ? 'ok' : a.status === 'error' ? 'danger' : ''));

  if (state.alasConfigsLoading && !state.alasConfigsLoaded) setAlasPanelMessage(runtimeState, '正在加载配置权限…');
  else if (state.alasConfigsError) setAlasPanelMessage(runtimeState, `配置列表加载失败：${state.alasConfigsError}`, 'danger');
  else if (state.alasConfigsLoaded && !configCount) setAlasPanelMessage(runtimeState, '请联系管理员分配一个或多个 ALAS 配置。', 'warn');
  else if (state.alasStatusRefreshPending) setAlasPanelMessage(runtimeState, `当前操作完成后将刷新 ${state.selectedAlasConfig} 的运行状态…`);
  else if (state.alasStatusLoading) setAlasPanelMessage(runtimeState, state.alasSwitching ? `正在切换到 ${state.selectedAlasConfig}…` : `正在读取 ${state.selectedAlasConfig} 的运行状态…`);
  else if (state.alasStatusError) setAlasPanelMessage(runtimeState, `ALAS Runtime 暂时不可达：${state.alasStatusError}`, 'danger');
  else if (a.error) setAlasPanelMessage(runtimeState, `ALAS Runtime 返回异常：${a.error}`, 'danger');
  else if (binding && a.status) setAlasPanelMessage(runtimeState, `${state.selectedAlasConfig} 的运行状态已同步`, 'ok');
  else if (binding) setAlasPanelMessage(runtimeState, '尚未读取运行状态');
  else setAlasPanelMessage(runtimeState, '');

  const toggle=$('alasToggleRun');
  if (toggle) {
    toggle.textContent = a.status === 'error' ? '重启 ALAS' : a.status === 'running' ? '停止 ALAS' : '启动 ALAS';
    toggle.disabled = !binding || !binding.can_run || state.alasStatusLoading || actionBusy('alas');
    toggle.title = binding && !binding.can_run ? '管理员未授予此配置的启停权限' : '';
  }
  const reload=$('alasReload');
  if (reload) reload.disabled=state.alasConfigsLoading || actionBusy('alas');
  const openLink=$('alasOpenLink');
  if (openLink) {
    if (binding) {
      openLink.href=`/alas/embed/?config=${encodeURIComponent(binding.config_name)}`;
      openLink.removeAttribute('aria-disabled');
      openLink.removeAttribute('tabindex');
    } else {
      openLink.removeAttribute('href');
      openLink.setAttribute('aria-disabled','true');
      openLink.setAttribute('tabindex','-1');
    }
  }
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
function sameAlasCatalog(left, right){
  if (!Array.isArray(left) || !Array.isArray(right) || left.length !== right.length) return false;
  return left.every((item, index)=>{
    const other=right[index] || {};
    return item.config_name === other.config_name
      && !!item.can_run === !!other.can_run
      && !!item.can_edit === !!other.can_edit
      && !!item.is_default === !!other.is_default;
  });
}
function selectAuthorizedAlasConfig(configs, current, stored, serverDefault){
  const allowed=new Set(configs.map(item=>item.config_name));
  const bindingDefault=(configs.find(item=>item.is_default) || {}).config_name || '';
  if (allowed.has(current)) return current;
  if (allowed.has(stored)) return stored;
  if (allowed.has(serverDefault)) return serverDefault;
  if (allowed.has(bindingDefault)) return bindingDefault;
  return (configs[0] || {}).config_name || '';
}
function mayStartAlasStatus(actionInFlight, allowDuringAction=false){
  return !actionInFlight || !!allowDuringAction;
}
function isAlasStatusResponseCurrent(requestEpoch, currentEpoch, requestConfig, selectedConfig){
  return requestEpoch === currentEpoch && requestConfig === selectedConfig;
}
function isAlasOperationCurrent(operationSeq, currentSeq, operationConfig, selectedConfig){
  return operationSeq === currentSeq && operationConfig === selectedConfig;
}
function replaceAlasConfigCatalog(configs){
  if (sameAlasCatalog(state.alasConfigs, configs)) return false;
  state.alasConfigs=configs;
  state.alasCatalogRevision+=1;
  return true;
}
function invalidateAlasStatusRequest(){
  state.alasStatusEpoch+=1;
  invalidateResource('alasStatus');
  return state.alasStatusEpoch;
}
function invalidateAlasOperation(){
  state.alasOperationSeq+=1;
  return state.alasOperationSeq;
}
function normalizeAlasConfigBindings(data){
  const seen=new Set();
  const configs=[];
  (Array.isArray(data && data.configs) ? data.configs : []).forEach(item=>{
    const configName=String((item && item.config_name) || '').trim();
    if (!configName || seen.has(configName)) return;
    seen.add(configName);
    configs.push({
      config_name:configName,
      can_run:!!item.can_run,
      can_edit:!!item.can_edit,
      is_default:!!item.is_default
    });
  });
  return configs;
}
function applyAlasConfigCatalog(data){
  const configs=normalizeAlasConfigBindings(data);
  const allowed=new Set(configs.map(item=>item.config_name));
  const stored=readStoredAlasConfig();
  const serverDefault=String((data && data.default_config) || '').trim();
  if (allowed.has(serverDefault)) configs.forEach(item=>{ item.is_default=item.config_name === serverDefault; });
  const selected=selectAuthorizedAlasConfig(configs, state.selectedAlasConfig, stored, serverDefault);
  if (selected !== state.selectedAlasConfig) {
    invalidateAlasStatusRequest();
    invalidateAlasOperation();
    state.alas=null;
    state.alasStatusLoading=false;
    state.alasStatusError='';
    state.alasSwitching=false;
    state.alasStatusRefreshPending=!!selected && actionBusy('alas');
  }
  replaceAlasConfigCatalog(configs);
  state.selectedAlasConfig=selected;
  state.alasConfigsLoaded=true;
  state.alasConfigsLoading=false;
  state.alasConfigsError='';
  persistAlasConfig(selected);
  scheduleRender();
}
function handleAlasCatalogFailure(error){
  replaceAlasConfigCatalog([]);
  state.alasConfigsLoaded=true;
  state.alasConfigsLoading=false;
  state.alasConfigsError=(error && error.message) || '无法读取配置权限';
  state.selectedAlasConfig='';
  state.alas=null;
  state.alasStatusLoading=false;
  state.alasStatusError='';
  state.alasSwitching=false;
  state.alasStatusRefreshPending=false;
  invalidateAlasStatusRequest();
  invalidateAlasOperation();
  scheduleRender();
}
function flushPendingAlasStatusRefresh(){
  if (!state.alasStatusRefreshPending || actionBusy('alas')) return null;
  if (!state.selectedAlasConfig || state.alasConfigsError) {
    state.alasStatusRefreshPending=false;
    return null;
  }
  state.alasStatusRefreshPending=false;
  return loadAlasStatus(true).catch(()=>null);
}
function loadAlasConfigs(force=false){
  state.alasConfigsLoading=true;
  state.alasConfigsError='';
  scheduleRender();
  const request=requestResource('alasConfigs', '/api/alas/configs', applyAlasConfigCatalog, {force});
  return request.catch(error=>{
    handleAlasCatalogFailure(error);
    throw error;
  });
}
function loadAlasStatus(force=false, options={}){
  if (!mayStartAlasStatus(actionBusy('alas'), options.allowDuringAction)) return Promise.resolve(null);
  const configName=state.selectedAlasConfig;
  if (!configName) {
    state.alas=null;
    state.alasStatusLoading=false;
    state.alasStatusError='';
    scheduleRender();
    return Promise.resolve(null);
  }
  state.alasStatusLoading=true;
  state.alasStatusRefreshPending=false;
  state.alasStatusError='';
  scheduleRender();
  const requestEpoch=state.alasStatusEpoch;
  const url=`/api/alas/status?config=${encodeURIComponent(configName)}`;
  const request=requestResource('alasStatus', url, data=>{
    if (!isAlasStatusResponseCurrent(requestEpoch, state.alasStatusEpoch, configName, state.selectedAlasConfig)) return;
    state.alas=Object.assign({}, data || {}, {config:(data && data.config) || configName});
    state.alasStatusLoading=false;
    state.alasStatusError='';
    state.alasSwitching=false;
    scheduleRender();
  }, {force});
  return request.catch(error=>{
    if (isAlasStatusResponseCurrent(requestEpoch, state.alasStatusEpoch, configName, state.selectedAlasConfig)) {
      state.alas=null;
      state.alasStatusLoading=false;
      state.alasStatusError=(error && error.message) || '无法获取运行状态';
      state.alasSwitching=false;
      scheduleRender();
    }
    throw error;
  });
}
async function loadAlasPanel(force=false, options={}){
  await loadAlasConfigs(force);
  if (state.alasConfigsLoading || state.alasConfigsError || !state.selectedAlasConfig) return null;
  return loadAlasStatus(force, options);
}
async function selectAlasConfig(configName){
  const selected=String(configName || '').trim();
  if (!state.alasConfigs.some(item=>item.config_name === selected) || selected === state.selectedAlasConfig) return;
  invalidateAlasStatusRequest();
  invalidateAlasOperation();
  state.selectedAlasConfig=selected;
  state.alas=null;
  state.alasStatusError='';
  state.alasStatusLoading=true;
  state.alasSwitching=true;
  persistAlasConfig(selected);
  scheduleRender();
  await loadAlasStatus(true);
}
async function loadAll(options={}){
  const force=!!options.force;
  const alasRequests=[];
  if (state.alasConfigsLoaded && !actionBusy('alas')) {
    if (options.refreshAlasCatalog) alasRequests.push(loadAlasPanel(force));
    else if (state.selectedAlasConfig) alasRequests.push(loadAlasStatus(force));
  }
  const results=await Promise.allSettled([
    loadUser(force),
    loadDevices(force),
    loadVideoPreferences(force),
    ...alasRequests
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
async function performQualityApply(payload){
  const id=state.selectedDeviceId;
  const running = !!(selectedSession() && selectedSession().running);
  invalidateResource('video');
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
      setQualityStatus('画质已应用，投屏流已重启');
      show('画质已应用，投屏已按新参数重启');
    } else if (running) {
      setQualityStatus('');
      show('画质设置已确认，当前投屏参数未变化');
    } else {
      setQualityStatus('');
      show('画质偏好已保存，下次启动投屏生效');
    }
  } catch (error) {
    setQualityStatus('画质应用失败');
    throw error;
  }
}
function queueQualityApply(payload, options={}){
  state.pendingQualityPayload={payload, isCurrent:options.isCurrent || null, onStart:options.onStart || null};
  if (state.qualityPromise) return state.qualityPromise;
  state.qualityApplying=true;
  state.qualityPromise=(async()=>{
    while(state.pendingQualityPayload){
      const next=state.pendingQualityPayload;
      state.pendingQualityPayload=null;
      if (next.isCurrent && !next.isCurrent()) continue;
      if (next.onStart) next.onStart();
      await performQualityApply(next.payload);
    }
  })().finally(()=>{
    state.pendingQualityPayload=null;
    state.qualityApplying=false;
    state.qualityPromise=null;
    render();
  });
  render();
  return state.qualityPromise;
}
function saveOrApplyQuality(options={}){ return queueQualityApply(qualityPayload(), options); }
function closeVideoSocket(){
  if (state.videoReconnectTimer) { clearTimeout(state.videoReconnectTimer); state.videoReconnectTimer=null; }
  state.videoReconnectAttempts=0;
  state.videoSeq += 1;
  const ws = state.videoWs;
  state.videoWs = null;
  state.videoConnected = false;
  if (ws) { try { ws.close(); } catch(_){} }
  state.playerSeq += 1;
  if (state.jmuxer) { try { state.jmuxer.destroy(); } catch(_){} state.jmuxer=null; }
  state.streamGeneration=0;
  state.videoSpsSignature='';
  state.videoReconfiguring=false;
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
  else {
    stopControlKeepalive();
    if (state.input && typeof state.input.closeKeyboard === 'function') state.input.closeKeyboard();
  }
  renderKeyboardControl();
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
  if (state.controlReconnectTimer) { clearTimeout(state.controlReconnectTimer); state.controlReconnectTimer=null; }
  state.controlReconnectAttempts=0;
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
function resetVideoElement(){
  const video = $('phoneVideo');
  if (!video) return;
  try { video.pause(); } catch(_) {}
  try { video.removeAttribute('src'); video.load(); } catch(_) {}
}
function jmuxerConfig(video, playerSeq){
  const q = qualityPayload();
  const fps = q.max_fps > 0 ? q.max_fps : 24;
  return {
    node: video,
    mode:'video',
    flushingTime: Math.max(33, Math.round(1000 / fps)),
    maxDelay:220,
    fps,
    clearBuffer:true,
    onError:(error)=>{ if(state.playerSeq!==playerSeq) return; console.warn('JMuxer error', error); schedulePlayerReset(playerSeq); },
    onMissingVideoFrames:()=>requestVideoKeyframe(playerSeq)
  };
}
function annexBNalUnit(data, targetType){
  const bytes=data instanceof Uint8Array ? data : new Uint8Array(data);
  let cursor=0;
  while(cursor<bytes.length-2){
    let start=-1;
    let prefix=0;
    for(let i=cursor;i<bytes.length-2;i+=1){
      if(bytes[i]!==0 || bytes[i+1]!==0) continue;
      if(bytes[i+2]===1){ start=i; prefix=3; break; }
      if(i+3<bytes.length && bytes[i+2]===0 && bytes[i+3]===1){ start=i; prefix=4; break; }
    }
    if(start<0) break;
    const payloadStart=start+prefix;
    if(payloadStart>=bytes.length) break;
    const nalType=bytes[payloadStart]&0x1f;
    if(nalType===targetType){
      let end=bytes.length;
      for(let i=payloadStart+1;i<bytes.length-2;i+=1){
        if(bytes[i]===0 && bytes[i+1]===0 && (bytes[i+2]===1 || (i+3<bytes.length && bytes[i+2]===0 && bytes[i+3]===1))){ end=i; break; }
      }
      return bytes.slice(payloadStart,end);
    }
    if(nalType>=1 && nalType<=5) return null;
    cursor=payloadStart+1;
  }
  if(bytes.length && (bytes[0]&0x1f)===targetType) return bytes.slice();
  return null;
}
function nalUnitSignature(bytes){
  if(!bytes || !bytes.length) return '';
  let signature=`${bytes.length}:`;
  for(let i=0;i<bytes.length;i+=1) signature+=bytes[i].toString(16).padStart(2,'0');
  return signature;
}
function videoConfigurationChanged(data){
  const sps=annexBNalUnit(data, 7);
  if(!sps) return false;
  const signature=nalUnitSignature(sps);
  const previous=state.videoSpsSignature;
  state.videoSpsSignature=signature;
  return !!previous && previous!==signature;
}
function completeAnnexBChunk(data){
  const bytes = data instanceof Uint8Array ? data : new Uint8Array(data);
  const out = new Uint8Array(bytes.length + 4);
  out.set(bytes, 0);
  out.set([0, 0, 0, 1], bytes.length);
  return out;
}
function recreateVideoPlayer(video){
  if(state.playerResetTimer){ clearTimeout(state.playerResetTimer); state.playerResetTimer=null; }
  const playerSeq=++state.playerSeq;
  if(state.jmuxer){ try { state.jmuxer.destroy(); } catch(_){} }
  resetVideoElement();
  state.jmuxer=new JMuxer(jmuxerConfig(video, playerSeq));
}
function requestVideoKeyframe(playerSeq=state.playerSeq){
  if(state.playerSeq!==playerSeq) return;
  const now=Date.now();
  if(now-state.lastKeyframeRequestAt<1000) return;
  state.lastKeyframeRequestAt=now;
  sendPlayerReset();
}
function schedulePlayerReset(playerSeq=state.playerSeq){
  if(state.playerSeq!==playerSeq) return;
  if (state.playerResetTimer) return;
  const now = Date.now();
  if (now - state.lastPlayerResetAt < 1400) return;
  state.lastPlayerResetAt = now;
  state.playerResetTimer=setTimeout(()=>{
    state.playerResetTimer=null;
    if(state.playerSeq!==playerSeq) return;
    const video=$('phoneVideo');
    recreateVideoPlayer(video);
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
const RECONNECT_DELAYS=[500, 1000, 2000, 4000, 8000];
function canReconnectChannel(id){
  const session=state.sessions[id];
  return !state.pageLeaving && !document.hidden && state.activeDeviceId === id && state.selectedDeviceId === id && !!(session && session.running);
}
function scheduleVideoReconnect(id){
  if (state.videoReconnectTimer || !canReconnectChannel(id)) return;
  const attempt=state.videoReconnectAttempts;
  if (attempt >= RECONNECT_DELAYS.length) {
    state.connectionPhase='error';
    state.mirrorError='视频通道多次重连失败，请重新开始投屏';
    render();
    return;
  }
  state.videoReconnectAttempts=attempt + 1;
  state.connectionPhase='reconnecting';
  render();
  state.videoReconnectTimer=setTimeout(()=>{
    state.videoReconnectTimer=null;
    if (canReconnectChannel(id)) openVideo(id, {force:true, phase:'reconnecting', reconnect:true});
  }, RECONNECT_DELAYS[attempt]);
}
function scheduleControlReconnect(id){
  if (state.controlReconnectTimer || !canReconnectChannel(id)) return;
  const attempt=state.controlReconnectAttempts;
  if (attempt >= RECONNECT_DELAYS.length) return;
  state.controlReconnectAttempts=attempt + 1;
  state.controlReconnectTimer=setTimeout(()=>{
    state.controlReconnectTimer=null;
    if (canReconnectChannel(id)) openControl(id, {force:true, reconnect:true});
  }, RECONNECT_DELAYS[attempt]);
}
function pauseReconnectTimers(){
  if (state.videoReconnectTimer) {
    clearTimeout(state.videoReconnectTimer);
    state.videoReconnectTimer=null;
    state.videoReconnectAttempts=Math.max(0, state.videoReconnectAttempts - 1);
  }
  if (state.controlReconnectTimer) {
    clearTimeout(state.controlReconnectTimer);
    state.controlReconnectTimer=null;
    state.controlReconnectAttempts=Math.max(0, state.controlReconnectAttempts - 1);
  }
}
function openVideo(id, options={}){
  if (!options.force && state.activeDeviceId === id && socketLive(state.videoWs)) return state.videoWs;
  if (options.reconnect) {
    state.videoSeq += 1;
    const previous=state.videoWs;
    state.videoWs=null;
    state.videoConnected=false;
    if (previous) { try { previous.close(); } catch(_){} }
    state.playerSeq += 1;
    if (state.jmuxer) { try { state.jmuxer.destroy(); } catch(_){} state.jmuxer=null; }
    resetVideoElement();
  } else closeVideoSocket();
  state.activeDeviceId=id;
  state.connectionPhase=options.phase || 'connecting';
  state.mirrorError='';
  state.videoSpsSignature='';
  state.streamGeneration=0;
  state.videoReconfiguring=true;
  const token = ++state.videoSeq;
  const video=$('phoneVideo');
  recreateVideoPlayer(video);
  const ws = new WebSocket(wsUrl(`/ws/devices/${encodeURIComponent(id)}/video`));
  state.videoWs = ws;
  ws.binaryType='arraybuffer';
  ws.onopen=()=>{ if (state.videoSeq !== token || state.videoWs !== ws) return; state.videoConnected=true; state.connectionPhase='connected'; state.mirrorError=''; render(); if (document.hidden || !document.hasFocus()) scheduleInactiveStop('open_in_background'); };
  ws.onmessage = async (event) => {
    if (state.videoSeq !== token || state.videoWs !== ws) return;
    if (typeof event.data === 'string') {
      let msg=null;
      try { msg=JSON.parse(event.data); } catch(_) { return; }
      if(msg && msg.type==='stream_reset'){
        const generation=Number(msg.generation)||0;
        if(generation>state.streamGeneration){
          state.streamGeneration=generation;
          state.videoSpsSignature='';
          state.videoReconfiguring=true;
          recreateVideoPlayer(video);
        }
        return;
      }
      if (msg && msg.session) updateRealtimeSession(id, msg.session);
      render();
      return;
    }
    const buf = event.data instanceof Blob ? await event.data.arrayBuffer() : event.data;
    if (state.videoSeq !== token || state.videoWs !== ws) return;
    if (!state.jmuxer) return;
    state.videoReconnectAttempts=0;
    const configurationChanged=videoConfigurationChanged(buf);
    if(configurationChanged){ state.videoReconfiguring=true; recreateVideoPlayer(video); }
    state.jmuxer.feed({video:completeAnnexBChunk(buf)});
    trimPlaybackDelay(video);
    if (video.paused) video.play().catch(()=>{});
  };
  ws.onerror=()=>{ if (state.videoSeq === token && state.videoWs === ws) { state.connectionPhase='error'; state.mirrorError='视频通道连接失败'; show(state.mirrorError); render(); } };
  ws.onclose = () => { if (state.videoSeq !== token || state.videoWs !== ws) return; state.videoConnected=false; state.videoWs=null; state.playerSeq+=1; if (state.jmuxer) { try { state.jmuxer.destroy(); } catch(_){} state.jmuxer=null; } state.streamGeneration=0; state.videoSpsSignature=''; state.videoReconfiguring=false; resetVideoElement(); state.connectionPhase=selectedSession() && selectedSession().running ? 'disconnected' : 'idle'; render(); scheduleVideoReconnect(id); };
  video.onloadedmetadata = () => updateInputSize();
  video.onresize = () => updateInputSize();
  return ws;
}
function openControl(id, options={}){
  if (!options.force && state.activeDeviceId === id && socketLive(state.controlWs)) return state.controlWs;
  if (options.reconnect) {
    state.controlSeq += 1;
    const previous=state.controlWs;
    state.controlWs=null;
    state.controlConnected=false;
    setControlOwnership(false);
    destroyInput();
    if (previous) { try { previous.close(); } catch(_){} }
  } else closeControlSocket();
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
      state.controlReconnectAttempts=0;
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
  ws.onclose = () => { if (state.controlSeq !== token || state.controlWs !== ws) return; state.controlConnected=false; state.controlWs=null; setControlOwnership(false); destroyInput(); render(); settleControlRequest(new Error('控制通道已关闭')); scheduleControlReconnect(id); };
  return ws;
}
function updateInputSize(){
  const video=$('phoneVideo');
  const metadataW=Number(video.videoWidth) || 0;
  const metadataH=Number(video.videoHeight) || 0;
  const w=metadataW || state.screen.w;
  const h=metadataH || state.screen.h;
  state.screen={w,h};
  if(metadataW>0 && metadataH>0) state.videoReconfiguring=false;
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
    const isTouch = bytes && bytes[0] === 2;
    if (state.videoReconfiguring && isTouch) return;
    const isTouchMove = bytes && bytes[0] === 2 && bytes[1] === 2;
    if (isTouchMove && state.controlWs.bufferedAmount > 32768) return;
    state.controlWs.send(data);
  }, video, state.screen.w, state.screen.h, false, renderKeyboardControl);
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
function renderKeyboardControl(){
  const button=$('keyboardBtn');
  if (!button) return;
  const ready=!!(state.hasControl && state.input);
  const active=!!(ready && state.input.keyboardActive);
  button.disabled=!ready;
  button.dataset.active=String(active);
  button.title='打开键盘';
  button.setAttribute('aria-label', button.title);
}
function openMobileKeyboard(){
  const input=state.input;
  if (!state.hasControl || !input) return show('请先获取控制');
  if (!input.openKeyboard()) {
    show('浏览器未允许打开键盘，请再次点击键盘按钮');
  }
  renderKeyboardControl();
}
async function reloadAlas(){ await loadAlasPanel(true, {allowDuringAction:true}); }
async function toggleAlas(){
  const binding=currentAlasBinding();
  if (!binding) return;
  if (!binding.can_run) {
    state.alasStatusError='管理员未授予此配置的启停权限';
    scheduleRender();
    return;
  }
  const configName=binding.config_name;
  const operationSeq=invalidateAlasOperation();
  invalidateAlasStatusRequest();
  state.alasStatusLoading=true;
  state.alasStatusError='';
  state.alasSwitching=false;
  scheduleRender();
  try {
    const result=await fetchJson('/api/alas/toggle', {method:'POST', body:{config_name:configName}});
    if(result.ok === false) throw new Error(result.error || 'ALAS 操作失败');
    if (!isAlasOperationCurrent(operationSeq, state.alasOperationSeq, configName, state.selectedAlasConfig)) return;
    invalidateAlasStatusRequest();
    const status=result.alas || result;
    state.alas=Object.assign({}, status, {config:(status && status.config) || configName});
    state.alasStatusError='';
    show(state.alas.status === 'running' ? `${configName} 已启动` : `${configName} 状态已更新`);
  } catch (error) {
    if (isAlasOperationCurrent(operationSeq, state.alasOperationSeq, configName, state.selectedAlasConfig)) state.alasStatusError=(error && error.message) || 'ALAS 操作失败';
    throw error;
  } finally {
    if (isAlasOperationCurrent(operationSeq, state.alasOperationSeq, configName, state.selectedAlasConfig)) state.alasStatusLoading=false;
    flushPendingAlasStatusRefresh();
    scheduleRender();
  }
}
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
  const requests=[
    loadDevices(force),
    loadVideoPreferences(force)
  ];
  if (state.alasConfigsLoaded && state.selectedAlasConfig && !actionBusy('alas')) requests.push(loadAlasStatus(force));
  return Promise.allSettled(requests);
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
  const selectedVisible=selectedButton && !selectedButton.disabled && !selectedButton.hidden && selectedButton.getClientRects().length > 0;
  const visibleDevice=Array.from(state.deviceNodes.values()).find(button=>!button.disabled && !button.hidden && button.getClientRects().length > 0);
  const focusTarget=searchVisible ? search : (selectedVisible ? selectedButton : visibleDevice || visibleLayerFocusables(sidebar)[0]) || sidebar;
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
function fullscreenElement(){ return document.fullscreenElement || document.webkitFullscreenElement || null; }
function fullscreenQualitySelection(){
  const prefs=state.videoPrefs || {};
  const profiles=prefs.profiles || {};
  const minimum=Math.max(FULLSCREEN_MIN_MAX_SIZE, Number(prefs.fullscreen_min_max_size || FULLSCREEN_MIN_MAX_SIZE));
  const configured=String(prefs.fullscreen_profile || 'sharp');
  if (profiles[configured] && Number(profiles[configured].max_size)>=minimum) return {name:configured, values:profiles[configured], minimum};
  const eligible=Object.keys(profiles).filter(name=>Number(profiles[name] && profiles[name].max_size)>=minimum);
  if (!eligible.length) return null;
  eligible.sort((left,right)=>Number(profiles[right].max_size)-Number(profiles[left].max_size));
  const name=eligible.includes('sharp') ? 'sharp' : eligible[0];
  return {name, values:profiles[name], minimum};
}
function qualityMatches(left,right){
  if (!left || !right) return false;
  return ['profile','video_bit_rate','max_size','max_fps'].every(key=>String(left[key])===String(right[key]));
}
async function ensureFullscreenQuality(epoch=state.immersiveEpoch){
  if (state.fullscreenQualityPromise) {
    if (state.fullscreenQualityEpoch===epoch) return state.fullscreenQualityPromise;
    try { await state.fullscreenQualityPromise; } catch (_) {}
    if (!state.immersive || epoch!==state.immersiveEpoch) return;
  }
  const promise=(async()=>{
    if (!state.videoPrefs) await loadVideoPreferences();
    if (!state.immersive || epoch!==state.immersiveEpoch) return;
    const selection=fullscreenQualitySelection();
    if (!selection) {
      show('后台未配置可用的 720p 或更高全屏画质', 5200);
      return;
    }
    const target={...qualityPayload(), profile:selection.name, ...selection.values};
    if (!state.qualityPromise && qualityMatches((state.videoPrefs || {}).effective, target)) {
      state.qualityProfile=selection.name;
      render();
      return;
    }
    await queueQualityApply(target, {
      isCurrent:()=>state.immersive && epoch===state.immersiveEpoch,
      onStart:()=>{ state.qualityProfile=selection.name; renderQualityButtons(); },
    });
  })();
  state.fullscreenQualityEpoch=epoch;
  state.fullscreenQualityPromise=promise;
  try {
    return await promise;
  } finally {
    if (state.fullscreenQualityPromise===promise) state.fullscreenQualityPromise=null;
  }
}
function syncImmersiveRail(open=state.immersiveRailOpen){
  const rail=$('immersiveRail');
  const toggle=$('immersiveRailToggle');
  const controls=$('immersiveControls');
  state.immersiveRailOpen=!!(state.immersive && open);
  if (rail) rail.dataset.open=state.immersiveRailOpen ? 'true' : 'false';
  if (toggle) {
    toggle.setAttribute('aria-expanded', state.immersiveRailOpen ? 'true' : 'false');
    toggle.setAttribute('aria-hidden', state.immersive ? 'false' : 'true');
    toggle.setAttribute('aria-label', state.immersiveRailOpen ? '隐藏侧边控制' : '显示侧边控制');
    toggle.title=state.immersiveRailOpen ? '隐藏侧边控制' : '显示侧边控制';
  }
  if (controls) {
    const hidden=state.immersive && !state.immersiveRailOpen;
    controls.setAttribute('aria-hidden', hidden ? 'true' : 'false');
    controls.toggleAttribute('inert', hidden);
  }
}
function syncFullscreenButton(){
  const button=$('fullscreenBtn');
  if (!button) return;
  button.setAttribute('aria-pressed', state.immersive ? 'true' : 'false');
  button.setAttribute('aria-label', state.immersive ? '退出全屏' : '进入全屏');
  button.title=state.immersive ? '退出全屏' : '进入全屏';
  const use=button.querySelector('use');
  if (use) use.setAttribute('href', String(use.getAttribute('href') || '').replace(/#[^#]+$/, state.immersive ? '#minimize-2' : '#maximize-2'));
}
function setImmersiveMode(enabled, options={}){
  const wasImmersive=state.immersive;
  const trigger=state.immersiveTrigger;
  if (wasImmersive!==!!enabled) state.immersiveEpoch+=1;
  if (enabled && !wasImmersive) state.immersiveTrigger=options.trigger || document.activeElement;
  state.immersive=!!enabled;
  const app=$('mirrorApp');
  if (app) app.classList.toggle('is-immersive', state.immersive);
  if (state.immersive) {
    if (state.input && typeof state.input.closeKeyboard === 'function') state.input.closeKeyboard();
    closeStatusDetails();
    closeToolDrawer();
    closeSidebar({restoreFocus:false});
  }
  syncFullscreenButton();
  syncImmersiveRail(false);
  scheduleLayout();
  if (state.immersive && !wasImmersive) {
    const toggle=$('immersiveRailToggle');
    if (toggle) requestAnimationFrame(()=>toggle.focus({preventScroll:true}));
  } else if (!state.immersive && wasImmersive) {
    state.immersiveTrigger=null;
    const target=(trigger && trigger.isConnected ? trigger : $('fullscreenBtn'));
    if (target) requestAnimationFrame(()=>target.focus({preventScroll:true}));
  }
}
async function enterImmersiveMode(trigger){
  if (state.immersive) return;
  const app=$('mirrorApp');
  setImmersiveMode(true, {trigger});
  const epoch=state.immersiveEpoch;
  const request=app && (app.requestFullscreen || app.webkitRequestFullscreen);
  if (request) {
    try {
      await request.call(app, {navigationUI:'hide'});
    } catch (_) {
      show('浏览器未开放系统全屏，已使用沉浸布局');
    }
  }
  if (!state.immersive || epoch!==state.immersiveEpoch) {
    if (fullscreenElement()===app) {
      const exit=document.exitFullscreen || document.webkitExitFullscreen;
      if (exit) Promise.resolve(exit.call(document)).catch(()=>{});
    }
    return;
  }
  ensureFullscreenQuality(epoch).catch(error=>show(error.message || '全屏画质应用失败', 5200));
}
async function exitImmersiveMode(){
  state.immersiveEpoch+=1;
  const exit=document.exitFullscreen || document.webkitExitFullscreen;
  let exitError=null;
  if (exit) {
    try { await exit.call(document); } catch (error) { exitError=error; }
  }
  if (fullscreenElement()) {
    if (exitError) show('浏览器未能退出系统全屏，请再次按 Esc');
    return false;
  }
  state.systemFullscreen=false;
  setImmersiveMode(false);
  return true;
}
function toggleImmersiveMode(trigger){ return state.immersive ? exitImmersiveMode() : enterImmersiveMode(trigger); }
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
  bindClick('refreshBtn', (_, button)=>runBusyAction('refresh', button, '正在刷新', ()=>loadAll({force:true, refreshAlasCatalog:true})).catch(e=>show(e.message)));
  bindClick('menuBtn', (_, button)=>openSidebar(button));
  bindClick('sidebarCollapseBtn', ()=>{ if (mobileSidebarMedia.matches) closeSidebar(); else setSidebarCollapsed(!state.sidebarCollapsed); });
  bindClick('sidebarBackdrop', ()=>closeSidebar());
  bindClick('toolBtn', (_, button)=>toggleToolPanel('tools', button));
  bindClick('backBtn', ()=>sendKey(4));
  bindClick('homeBtn', ()=>sendKey(3));
  bindClick('recentBtn', ()=>sendKey(187));
  bindClick('keyboardBtn', openMobileKeyboard);
  bindClick('fullscreenBtn', (_, button)=>toggleImmersiveMode(button));
  bindClick('immersiveRailToggle', ()=>syncImmersiveRail(!state.immersiveRailOpen));
  bindClick('immersiveStopBtn', (_, button)=>runBusyAction('mirror', button, '正在停止投屏', stopMirror).catch(e=>show(e.message)));
  bindClick('exitFullscreenBtn', ()=>exitImmersiveMode());
  bindClick('alasBtn', (_, button)=>{
    toggleToolPanel('alasTools', button);
    const panel=$('alasTools');
    if (panel && panel.classList.contains('open') && !state.alasConfigsLoaded) loadAlasPanel().catch(()=>{});
  });
  bindClick('accountBtn', (_, button)=>toggleToolPanel('accountTools', button));
  bindClick('toolDrawerCloseBtn', ()=>closeToolDrawer());
  bindClick('toolDrawerBackdrop', ()=>closeToolDrawer());
  bindClick('changePasswordBtn', (_, button)=>runBusyAction('password', button, '正在修改密码', changePassword).catch(e=>show(e.message)));
  bindClick('alasToggleRun', (_, button)=>runBusyAction('alas', button, '正在更新 ALAS', toggleAlas).catch(()=>{}));
  bindClick('alasReload', (_, button)=>runBusyAction('alas', button, '正在刷新 ALAS', reloadAlas).catch(()=>{}));
  const alasConfigSelect=$('alasConfigSelect');
  if (alasConfigSelect) alasConfigSelect.onchange=event=>selectAlasConfig(event.currentTarget.value).catch(()=>{});
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
    if (state.immersive) {
      event.preventDefault();
      exitImmersiveMode();
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
['fullscreenchange','webkitfullscreenchange'].forEach(name=>document.addEventListener(name,()=>{
  const active=fullscreenElement() === $('mirrorApp');
  if (active) {
    if (!state.immersive) {
      const exit=document.exitFullscreen || document.webkitExitFullscreen;
      if (exit) Promise.resolve(exit.call(document)).catch(()=>{});
      return;
    }
    state.systemFullscreen=true;
    return;
  }
  if (state.systemFullscreen) {
    state.systemFullscreen=false;
    setImmersiveMode(false);
  }
}));
window.addEventListener('blur', ()=>scheduleInactiveStop('window_blur'));
window.addEventListener('focus', ()=>cancelInactiveStop());
document.addEventListener('visibilitychange', ()=>{
  if (document.hidden) {
    pauseReconnectTimers();
    scheduleInactiveStop('document_hidden');
  }
  else {
    cancelInactiveStop();
    const id=state.activeDeviceId;
    if (id && canReconnectChannel(id)) {
      if (!socketLive(state.videoWs)) scheduleVideoReconnect(id);
      if (!socketLive(state.controlWs)) scheduleControlReconnect(id);
    }
  }
  syncRefreshLoops();
});
window.addEventListener('pagehide', ()=>{
  state.pageLeaving=true;
  state.resumeDeviceId=state.activeDeviceId;
  closeEvents();
  closeVideo();
});
window.addEventListener('pageshow', event=>{
  if (!event.persisted) return;
  state.pageLeaving=false;
  const resumeDeviceId=state.resumeDeviceId;
  state.resumeDeviceId='';
  openEvents();
  loadAll({force:true}).then(()=>{
    const session=resumeDeviceId && state.sessions[resumeDeviceId];
    if (!resumeDeviceId || !session || !session.running || document.hidden) return;
    state.selectedDeviceId=resumeDeviceId;
    localStorage.setItem(SELECTED_KEY, resumeDeviceId);
    reconnectSockets(resumeDeviceId, 'reconnecting');
  }).catch(e=>show(e.message));
});
openEvents();
loadAll().catch(e=>show(e.message));
