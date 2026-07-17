const bootstrap = JSON.parse(document.getElementById("scrcpygate-bootstrap").textContent);
const csrfToken = bootstrap.csrf_token;
const mirrorI18n = window.ScrcpyGateI18n;
const mirrorT = (key, values) => (
  mirrorI18n && typeof mirrorI18n.t === 'function' ? mirrorI18n.t(key, values) : String(key || '')
);
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
function deviceLabel(device){ return device ? (device.display_name || device.name || mirrorT('mirror.device.fallback')) : mirrorT('mirror.device.not_selected'); }
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
function show(message, ms=3200){ const n=$('notice'); n.textContent=message || mirrorT('common.feedback.failed'); n.classList.add('show'); clearTimeout(show.t); show.t=setTimeout(()=>n.classList.remove('show'),ms); }
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
    if (key === 'mirror') state.mirrorError=(error && error.message) || mirrorT('mirror.actions.mirror_failed');
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
const BUILTIN_PROFILE_LABELS = {smooth:'mirror.profile.smooth', balanced:'mirror.profile.balanced', sharp:'mirror.profile.sharp', low_latency:'mirror.profile.low_latency'};
const ADB_STATE_LABELS = {online:'mirror.adb_state.online', offline:'mirror.adb_state.offline', unauthorized:'mirror.adb_state.unauthorized', reconnecting:'mirror.adb_state.reconnecting', unknown:'mirror.adb_state.unknown'};
const STREAM_HEALTH_LABELS = {healthy:'mirror.stream_health.healthy', idle:'mirror.stream_health.idle', starting:'mirror.stream_health.starting', config:'mirror.stream_health.config', invalid_h264:'mirror.stream_health.invalid_h264', adb_failed:'mirror.stream_health.adb_failed', failed:'mirror.stream_health.failed', stopped:'mirror.stream_health.stopped', unknown:'mirror.stream_health.unknown'};
const ALAS_STATUS_LABELS = {running:'mirror.alas_status.running', stopped:'mirror.alas_status.stopped', idle:'mirror.alas_status.idle', disabled:'mirror.alas_status.disabled', disconnected:'mirror.alas_status.disconnected', error:'mirror.alas_status.error', unavailable:'mirror.alas_status.unavailable', unbound:'mirror.alas_status.unbound', invalid_config:'mirror.alas_status.invalid_config', unknown:'mirror.alas_status.unknown'};
function labelFrom(map, value, fallback){
  const key = String(value || '').trim();
  return mirrorT(map[key] || fallback || 'common.status.unknown');
}
function adbStateLabel(value){ return labelFrom(ADB_STATE_LABELS, value); }
function streamModeLabel(mode){ return mirrorT(({raw:'mirror.stream_mode.raw', protocol:'mirror.stream_mode.protocol', legacy:'mirror.stream_mode.legacy'}[mode]) || 'common.status.unknown'); }
function streamModeShortLabel(mode){ return mirrorT(({raw:'mirror.stream_mode.raw_short', protocol:'mirror.stream_mode.protocol_short', legacy:'mirror.stream_mode.legacy_short', none:'mirror.stream_mode.none'}[mode]) || 'common.status.unknown'); }
function maxSizeQualityLabel(value){ const size=Number(value); if(!Number.isFinite(size) || size<=0) return mirrorT('mirror.quality.raw_size'); const height=Math.round(size*9/16); const labels={854:'480p',960:'540p',1280:'720p',1600:'900p',1920:'1080p'}; return labels[size] ? mirrorT('mirror.quality.size_label',{size,height,quality:labels[size]}) : mirrorT('mirror.quality.size_label_custom',{size,height}); }
function streamHealthLabel(value){ return labelFrom(STREAM_HEALTH_LABELS, value); }
function alasStatusLabel(value){ return labelFrom(ALAS_STATUS_LABELS, value); }
function qualitySummary(){
  const q = qualityPayload();
  const fps = q.max_fps > 0 ? q.max_fps : mirrorT('common.units.unlimited');
  return mirrorT('mirror.quality.summary',{size:maxSizeQualityLabel(q.max_size),bitrate:Math.round(q.video_bit_rate / 100000) / 10,fps,mode:streamModeShortLabel(q.scrcpy_stream_mode || 'raw')});
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
  return BUILTIN_PROFILE_LABELS[profile] ? mirrorT(BUILTIN_PROFILE_LABELS[profile]) : labels[profile] || profile;
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
    button.onclick=()=>runBusyAction('quality', button, mirrorT('mirror.actions.apply_quality'), ()=>chooseQualityProfile(profile)).catch(e=>show(e.message));
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
    btn.title = !ready ? mirrorT('mirror.actions.quality_loading_title') : state.qualityApplying || actionBusy('quality') ? mirrorT('mirror.actions.quality_title') : mirrorT('mirror.actions.quality_switch');
  });
  const select = $('qualityStreamMode');
  if (select) select.disabled = !ready || !!state.qualityApplying || actionBusy('quality');
}
async function chooseQualityProfile(profile){
  if (state.qualityApplying) return show(mirrorT('mirror.actions.quality_title'));
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
    mode='loading'; title=mirrorT('mirror.device.loading'); description=mirrorT('mirror.device.loading_detail');
  } else if (state.deviceLoadError && !state.devices.length) {
    mode='error'; title=mirrorT('mirror.device.load_failed'); description=state.deviceLoadError;
  } else if (!state.devices.length) {
    mode=state.user && !state.user.is_admin ? 'no-permission' : 'no-devices';
    title=mode === 'no-permission' ? mirrorT('mirror.device.no_permission') : mirrorT('mirror.device.empty');
    description=mode === 'no-permission' ? mirrorT('mirror.device.no_permission_detail') : mirrorT('mirror.device.empty_detail');
  } else if (!visibleCount) {
    mode='filtered-empty'; title=mirrorT('mirror.device.filtered_empty'); description=mirrorT('mirror.device.filtered_empty_detail');
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
    const statusText=mirrorVisible ? mirrorT('mirror.status.mirror_running') : (device.enabled === false ? mirrorT('mirror.device.disabled') : adbStateLabel(adbState));
    const statusTone=mirrorVisible ? 'ok' : device.enabled === false ? 'muted' : adbState === 'online' ? 'ok' : adbBlocked ? 'danger' : 'warn';
    parts.status.textContent=statusText;
    parts.status.dataset.tone=statusTone;
    parts.presence.dataset.tone=statusTone;
    btn.dataset.status=statusTone;
    const viewers=Number((session && session.clients) || 0);
    const summaryParts=[
      device.enabled === false ? mirrorT('mirror.device.disabled') : adbState === 'unauthorized' ? mirrorT('mirror.device.unauthorized_wait') : device.can_control ? mirrorT('mirror.device.can_control') : mirrorT('mirror.device.view_only')
    ];
    if (viewers > 0) summaryParts.push(mirrorT('mirror.device.viewers',{count:viewers}));
    parts.summary.textContent=summaryParts.join(' · ');
    btn.setAttribute('aria-label', `${deviceLabel(device)}，${statusText}，${parts.summary.textContent}`);
    btn.onclick=()=>selectDevice(id);
    if (btn !== cursor) box.insertBefore(btn, cursor);
    cursor=btn.nextSibling;
  });
  const summary=$('deviceSummary');
  if (summary) {
    const total=state.devices.length;
    const summaryText=!state.devicesLoaded && !total ? mirrorT('common.feedback.loading') : (visibleCount === total ? mirrorT('mirror.device.count',{count:total}) : mirrorT('mirror.device.count_fraction',{visible:visibleCount,total}));
    if (summary.textContent !== summaryText) summary.textContent=summaryText;
  }
  renderDeviceEmpty(empty, visibleCount);
}
function controlOwnershipState(device, session){
  const lock=session && session.control_lock;
  const username=state.user && state.user.username;
  if (!device) return {text:mirrorT('mirror.control.not_selected'), tone:'neutral'};
  if (!device.can_control) return {text:mirrorT('mirror.control.view_only'), tone:'neutral'};
  if (state.hasControl || (lock && lock.username === username)) return {text:mirrorT('mirror.control.owned'), tone:'owned'};
  if (lock && lock.username) return {text:mirrorT('mirror.control.occupied',{username:lock.username}), tone:'occupied'};
  if (actionBusy('control') || (state.controlWs && state.controlWs.readyState === WebSocket.CONNECTING)) return {text:mirrorT('mirror.control.pending'), tone:'pending'};
  return {text:mirrorT('mirror.control.available'), tone:'available'};
}
function renderControlOwnership(device, session){
  const target=$('controlOwnership');
  const ownership=controlOwnershipState(device, session);
  if (!target) return ownership;
  target.textContent=ownership.text;
  target.dataset.state=ownership.tone;
  target.classList.toggle('ok', ownership.tone === 'owned' || ownership.tone === 'available');
  target.classList.toggle('warn', ownership.tone === 'occupied' || ownership.tone === 'pending');
  target.setAttribute('aria-label', mirrorT('mirror.control.label',{text:ownership.text}));
  return ownership;
}
function stageEmptyState(device, session){
  const running=!!(session && session.running);
  const health=String((session && session.stream_health) || '').toLowerCase();
  if (!state.devicesLoaded && !state.devices.length) return {type:'loading', title:mirrorT('mirror.stage.workspace_loading'), description:mirrorT('mirror.stage.workspace_loading_detail')};
  if (state.deviceLoadError && !state.devices.length) return {type:'error', title:mirrorT('mirror.device.load_failed'), description:state.deviceLoadError};
  if (!device) {
    if (!state.devices.length && state.user && !state.user.is_admin) return {type:'no-permission', title:mirrorT('mirror.device.no_permission'), description:mirrorT('mirror.device.no_permission_detail')};
    if (!state.devices.length) return {type:'no-devices', title:mirrorT('mirror.device.empty'), description:mirrorT('mirror.device.empty_detail')};
    return {type:'idle', title:mirrorT('mirror.stage.select_device'), description:mirrorT('mirror.stage.select_device_detail')};
  }
  if (state.mirrorError || ['failed','adb_failed','invalid_h264'].includes(health)) {
    return {type:'error', title:mirrorT('mirror.stage.problem'), description:state.mirrorError || mirrorT('mirror.stage.current_state',{status:streamHealthLabel(health)})};
  }
  if (state.starting || health === 'starting') return {type:'starting', title:mirrorT('mirror.stage.starting'), description:mirrorT('mirror.stage.starting_detail')};
  if (state.qualityApplying || state.connectionPhase === 'reconnecting' || device.adb_state === 'reconnecting') {
    return {type:'reconnecting', title:mirrorT('mirror.stage.reconnecting'), description:mirrorT('mirror.stage.reconnecting_detail')};
  }
  if (state.connectionPhase === 'connecting' || (!state.videoConnected && socketLive(state.videoWs))) {
    return {type:'connecting', title:mirrorT('mirror.stage.connecting'), description:mirrorT('mirror.stage.connecting_detail')};
  }
  if (running) return {type:'disconnected', title:mirrorT('mirror.stage.disconnected'), description:mirrorT('mirror.stage.disconnected_detail')};
  return {type:'idle', title:mirrorT('mirror.stage.idle'), description:mirrorT('mirror.stage.idle_detail')};
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
  const selectedMeta=$('selectedMeta'); if (selectedMeta) selectedMeta.textContent = device ? qualitySummary() : mirrorT('mirror.stage.select_device_detail');
  const ownership=renderControlOwnership(device, session);
  const topStatus=$('topStatus'); if (topStatus) topStatus.textContent='';
  const items=[];
  items.push(chip(running ? mirrorT('mirror.status.mirror_running') : mirrorT('mirror.status.mirror_stopped'), running ? 'ok' : 'warn'));
  items.push(chip(state.videoConnected ? mirrorT('mirror.status.video_connected') : socketLive(state.videoWs) ? mirrorT('mirror.status.video_connecting') : mirrorT('mirror.status.video_disconnected'), state.videoConnected ? 'ok' : 'warn'));
  if (!$('controlOwnership')) items.push(chip(ownership.text, ownership.tone === 'owned' || ownership.tone === 'available' ? 'ok' : 'warn'));
  if (session && session.video) items.push(chip(mirrorT('mirror.status.stream',{value:`${maxSizeQualityLabel(session.video.max_size)} / ${session.video.max_fps || mirrorT('common.units.unlimited')}fps`})));
  if (session && session.stream_mode) items.push(chip(mirrorT('mirror.status.stream_mode',{mode:streamModeShortLabel(session.stream_mode),health:streamHealthLabel(session.stream_health)}), session.stream_health === 'healthy' ? 'ok' : 'warn'));
  if (device && device.adb_state) items.push(chip(mirrorT('mirror.status.adb',{value:adbStateLabel(device.adb_state)}), device.adb_state === 'online' ? 'ok' : 'warn'));
  if (state.alas) items.push(chip(mirrorT('mirror.status.alas',{value:alasStatusLabel(state.alas.status)}), state.alas.status === 'running' ? 'ok' : state.alas.status === 'error' ? 'warn' : ''));
  if (topStatus) items.forEach(item=>topStatus.appendChild(item));
  const mirrorBusy=actionBusy('mirror');
  const startBtn=$('startBtn');
  if (startBtn) {
    setButtonLabel(startBtn, !device ? mirrorT('mirror.actions.start_mirror') : state.starting ? mirrorT('mirror.actions.starting_mirror') : running ? (state.videoConnected ? mirrorT('mirror.actions.refresh_video') : mirrorT('mirror.actions.connect_video')) : mirrorT('mirror.actions.start_mirror'));
    startBtn.disabled = mirrorBusy || state.starting || state.qualityApplying || !deviceSelectable(device);
  }
  const controlBtn=$('controlBtn');
  if (controlBtn) {
    setButtonLabel(controlBtn, actionBusy('control') ? mirrorT('mirror.actions.busy') : state.hasControl ? mirrorT('mirror.actions.release_control') : mirrorT('mirror.actions.acquire_control'));
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
  const roleChip=$('roleChip'); if (roleChip) roleChip.textContent = mirrorT(state.user && state.user.is_admin ? 'common.roles.admin' : 'common.roles.user');
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
      option.textContent=`${binding.config_name}${binding.is_default ? mirrorT('mirror.alas_panel.default_suffix') : ''}`;
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
  label.textContent=mirrorT('mirror.alas_panel.current_config');
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
    if (state.alasConfigsLoading && !state.alasConfigsLoaded) setAlasPanelMessage(configState, mirrorT('mirror.alas_panel.loading_configs'));
    else if (state.alasConfigsError) setAlasPanelMessage(configState, mirrorT('mirror.alas_panel.config_load_unavailable'), 'danger');
    else if (!state.alasConfigsLoaded) setAlasPanelMessage(configState, mirrorT('mirror.alas_panel.load_when_opened'));
    else if (!configCount) setAlasPanelMessage(configState, mirrorT('mirror.alas_panel.no_authorized_config'), 'warn');
    else renderAlasConfigSummary(configState,state.selectedAlasConfig);
  }

  box.textContent='';
  if (binding) {
    box.appendChild(chip(mirrorT(binding.is_default ? 'mirror.alas_panel.default_config' : 'mirror.alas_panel.authorized')));
    box.appendChild(chip(mirrorT(binding.can_run ? 'mirror.alas_panel.can_run' : 'mirror.alas_panel.view_status_only'), binding.can_run ? 'ok' : 'warn'));
    box.appendChild(chip(mirrorT(binding.can_edit ? 'mirror.alas_panel.can_edit' : 'mirror.alas_panel.cannot_edit'), binding.can_edit ? 'ok' : ''));
  }
  if (a.status) box.appendChild(chip(alasStatusLabel(a.status), a.status === 'running' ? 'ok' : a.status === 'error' ? 'danger' : ''));

  if (state.alasConfigsLoading && !state.alasConfigsLoaded) setAlasPanelMessage(runtimeState, mirrorT('mirror.alas_panel.loading_permissions'));
  else if (state.alasConfigsError) setAlasPanelMessage(runtimeState, mirrorT('mirror.alas_panel.config_list_failed',{error:state.alasConfigsError}), 'danger');
  else if (state.alasConfigsLoaded && !configCount) setAlasPanelMessage(runtimeState, mirrorT('mirror.alas_panel.contact_admin'), 'warn');
  else if (state.alasStatusRefreshPending) setAlasPanelMessage(runtimeState, mirrorT('mirror.alas_panel.refresh_pending',{config:state.selectedAlasConfig}));
  else if (state.alasStatusLoading) setAlasPanelMessage(runtimeState, mirrorT(state.alasSwitching ? 'mirror.alas_panel.switching' : 'mirror.alas_panel.loading_status',{config:state.selectedAlasConfig}));
  else if (state.alasStatusError) setAlasPanelMessage(runtimeState, mirrorT('mirror.alas_panel.runtime_unavailable',{error:state.alasStatusError}), 'danger');
  else if (a.error) setAlasPanelMessage(runtimeState, mirrorT('mirror.alas_panel.runtime_error',{error:a.error}), 'danger');
  else if (binding && a.status) setAlasPanelMessage(runtimeState, mirrorT('mirror.alas_panel.status_synced',{config:state.selectedAlasConfig}), 'ok');
  else if (binding) setAlasPanelMessage(runtimeState, mirrorT('mirror.alas_panel.status_not_loaded'));
  else setAlasPanelMessage(runtimeState, '');

  const toggle=$('alasToggleRun');
  if (toggle) {
    toggle.textContent = mirrorT(a.status === 'error' ? 'mirror.alas_panel.restart' : a.status === 'running' ? 'mirror.alas_panel.stop' : 'mirror.alas_panel.start');
    toggle.disabled = !binding || !binding.can_run || state.alasStatusLoading || actionBusy('alas');
    toggle.title = binding && !binding.can_run ? mirrorT('mirror.alas_panel.run_denied') : '';
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
  box.appendChild(chip(user.username || mirrorT('mirror.account.not_logged_in'), 'ok'));
  box.appendChild(chip(mirrorT(user.is_admin ? 'common.roles.admin' : 'common.roles.user')));
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
  if (title) title.textContent=mirrorT(({tools:'mirror.tools.display', alasTools:'mirror.tools.alas', accountTools:'mirror.tools.account'}[id]) || 'mirror.tools.workspace');
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
  if (!payload.current_password || !payload.new_password || !payload.confirm_password) return show(mirrorT('mirror.account.fill_all_passwords'));
  if (payload.new_password !== payload.confirm_password) return show(mirrorT('mirror.account.password_mismatch'));
  const result = await fetchJson('/api/account/password', {method:'PUT', body:payload});
  clearPasswordForm();
  show(result.other_sessions_removed ? mirrorT('mirror.account.password_changed_sessions',{count:result.other_sessions_removed}) : mirrorT('mirror.account.password_changed'));
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
    state.deviceLoadError=(error && error.message) || mirrorT('mirror.errors.device_list');
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
  state.alasConfigsError=(error && error.message) || mirrorT('mirror.errors.config_permissions');
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
      state.alasStatusError=(error && error.message) || mirrorT('mirror.errors.runtime_status');
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
  if (!id) return show(mirrorT('mirror.actions.select_device'));
  if (state.starting) return show(mirrorT('mirror.actions.starting_wait'));
  if (state.qualityApplying) return show(mirrorT('mirror.actions.quality_wait'));
  const now = Date.now();
  if (now - state.lastStartAt < 1200) return show(mirrorT('mirror.actions.too_fast'));
  state.lastStartAt = now;
  state.starting = true;
  render();
  try {
    const data = await fetchJson(`/api/devices/${encodeURIComponent(id)}/mirror/start`, {method:'POST', body:qualityPayload()});
    replaceMutationSessions(data.sessions);
    if (data.ok === false) {
      state.mirrorError=data.detail || data.error || mirrorT('mirror.actions.start_failed');
      render();
      return show(state.mirrorError, 5200);
    }
    if (state.activeDeviceId === id && socketLive(state.videoWs)) {
      schedulePlayerReset();
      if (!socketLive(state.controlWs)) openControl(id);
      show(mirrorT('mirror.actions.already_running'));
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
  if (!id) return show(mirrorT('mirror.actions.select_device'));
  if (state.starting) return show(mirrorT('mirror.actions.starting_wait'));
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
  setQualityStatus(mirrorT(running ? 'mirror.actions.quality_switching' : 'mirror.actions.quality_saving'));
  render();
  try {
    if (!id) {
      const saved = await fetchJson('/api/video/preferences', {method:'PUT', body:payload});
      state.videoPrefs = Object.assign({}, state.videoPrefs || {}, saved);
      setQualityStatus('');
      show(mirrorT('mirror.actions.quality_saved'));
      return;
    }
    const result = await fetchJson(`/api/devices/${encodeURIComponent(id)}/mirror/settings`, {method:'PUT', body:payload});
    if (result.ok === false) {
      replaceMutationSessions(result.sessions);
      setQualityStatus(mirrorT('mirror.actions.quality_failed'));
      show(result.detail || result.error || mirrorT('mirror.actions.quality_recovery_failed'), 5200);
      return;
    }
    state.videoPrefs = Object.assign({}, state.videoPrefs || {}, {effective:result.preferences, preferences:result.preferences});
    replaceMutationSessions(result.sessions);
    if (result.restarted) {
      setQualityStatus(mirrorT('mirror.actions.quality_restarted'));
      show(mirrorT('mirror.actions.quality_restarted_notice'));
    } else if (running) {
      setQualityStatus('');
      show(mirrorT('mirror.actions.quality_unchanged'));
    } else {
      setQualityStatus('');
      show(mirrorT('mirror.actions.quality_saved_next'));
    }
  } catch (error) {
    setQualityStatus(mirrorT('mirror.actions.quality_failed'));
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
  settleControlRequest(new Error(mirrorT('mirror.control.channel_closed')));
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
  show(mirrorT('mirror.idle_stop.scheduled',{minutes}), 4200);
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
  show(result.stopped ? mirrorT('mirror.idle_stop.mirror_stopped') : mirrorT('mirror.idle_stop.viewer_stopped'));
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
    state.mirrorError=mirrorT('mirror.errors.video_reconnect_failed');
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
  ws.onerror=()=>{ if (state.videoSeq === token && state.videoWs === ws) { state.connectionPhase='error'; state.mirrorError=mirrorT('mirror.errors.video_channel_failed'); show(state.mirrorError); render(); } };
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
          else settleControlRequest(new Error(msg.owner ? mirrorT('mirror.control.owner',{username:msg.owner}) : mirrorT('mirror.control.acquire_failed')));
        }
      }
    }
    if (msg.type === 'control_released') {
      setControlOwnership(false);
      if (state.controlRequest && state.controlRequest.ws === ws && state.controlRequest.kind === 'release') {
        if (msg.ok === false) settleControlRequest(new Error(mirrorT('mirror.control.release_failed')));
        else settleControlRequest();
      }
    }
    if (msg.type === 'control_error') {
      const error=new Error(msg.error || mirrorT('mirror.control.failed'));
      settleControlRequest(error);
      show(error.message);
    }
    render();
  };
  ws.onerror=()=>{ if (state.controlSeq === token && state.controlWs === ws) { settleControlRequest(new Error(mirrorT('mirror.control.channel_failed'))); show(mirrorT('mirror.control.channel_failed')); } };
  ws.onclose = () => { if (state.controlSeq !== token || state.controlWs !== ws) return; state.controlConnected=false; state.controlWs=null; setControlOwnership(false); destroyInput(); render(); settleControlRequest(new Error(mirrorT('mirror.control.channel_closed'))); scheduleControlReconnect(id); };
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
  const id=state.selectedDeviceId; if (!id) return show(mirrorT('mirror.actions.select_device'));
  const ws = openControl(id);
  await waitForSocketOpen(ws);
  if (state.controlWs !== ws || ws.readyState !== WebSocket.OPEN) throw new Error(mirrorT('mirror.control.channel_not_connected'));
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
  if (ws.readyState !== WebSocket.CONNECTING) return Promise.reject(new Error(mirrorT('mirror.control.channel_not_connected')));
  return new Promise((resolve, reject)=>{
    let timer=null;
    const cleanup=()=>{
      clearTimeout(timer);
      ws.removeEventListener('open', onOpen);
      ws.removeEventListener('error', onError);
      ws.removeEventListener('close', onClose);
    };
    const onOpen=()=>{ cleanup(); resolve(); };
    const onError=()=>{ cleanup(); reject(new Error(mirrorT('mirror.control.channel_failed'))); };
    const onClose=()=>{ cleanup(); reject(new Error(mirrorT('mirror.control.channel_closed'))); };
    timer=setTimeout(()=>{ cleanup(); reject(new Error(mirrorT('mirror.control.channel_timeout'))); }, timeoutMs);
    ws.addEventListener('open', onOpen, {once:true});
    ws.addEventListener('error', onError, {once:true});
    ws.addEventListener('close', onClose, {once:true});
  });
}
function waitForControlResponse(ws, kind){
  return new Promise((resolve, reject)=>{
    const timer=setTimeout(()=>{
      if (state.controlRequest && state.controlRequest.ws === ws) settleControlRequest(new Error(mirrorT('mirror.control.operation_timeout')));
    }, 8000);
    state.controlRequest={ws, kind, resolve, reject, timer};
  });
}
function sendKey(code){ if (!state.hasControl || !state.input) return show(mirrorT('mirror.control.acquire_first')); if (state.input.sendKeyCodePress) state.input.sendKeyCodePress(code); }
function renderKeyboardControl(){
  const button=$('keyboardBtn');
  if (!button) return;
  const ready=!!(state.hasControl && state.input);
  const active=!!(ready && state.input.keyboardActive);
  button.disabled=!ready;
  button.dataset.active=String(active);
  button.title=mirrorT('mirror.control.open_keyboard');
  button.setAttribute('aria-label', button.title);
}
function openMobileKeyboard(){
  const input=state.input;
  if (!state.hasControl || !input) return show(mirrorT('mirror.control.acquire_first'));
  if (!input.openKeyboard()) {
    show(mirrorT('mirror.control.keyboard_blocked'));
  }
  renderKeyboardControl();
}
async function reloadAlas(){ await loadAlasPanel(true, {allowDuringAction:true}); }
async function toggleAlas(){
  const binding=currentAlasBinding();
  if (!binding) return;
  if (!binding.can_run) {
    state.alasStatusError=mirrorT('mirror.alas_panel.run_denied');
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
    if(result.ok === false) throw new Error(result.error || mirrorT('mirror.errors.alas_operation'));
    if (!isAlasOperationCurrent(operationSeq, state.alasOperationSeq, configName, state.selectedAlasConfig)) return;
    invalidateAlasStatusRequest();
    const status=result.alas || result;
    state.alas=Object.assign({}, status, {config:(status && status.config) || configName});
    state.alasStatusError='';
    show(mirrorT(state.alas.status === 'running' ? 'mirror.alas_action.started' : 'mirror.alas_action.updated',{config:configName}));
  } catch (error) {
  if (isAlasOperationCurrent(operationSeq, state.alasOperationSeq, configName, state.selectedAlasConfig)) state.alasStatusError=(error && error.message) || mirrorT('mirror.errors.alas_operation');
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
    button.setAttribute('aria-label', state.sidebarCollapsed ? mirrorT('mirror.ui.expand_sidebar') : mirrorT('mirror.ui.collapse_sidebar'));
    button.title=state.sidebarCollapsed ? mirrorT('mirror.ui.expand_sidebar') : mirrorT('mirror.ui.collapse_sidebar');
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
    collapseButton.setAttribute('aria-label',mirrorT('mirror.ui.close_sidebar'));
    collapseButton.title=mirrorT('mirror.ui.close_sidebar');
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
      show(mirrorT('mirror.actions.fullscreen_quality_unavailable'), 5200);
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
    toggle.setAttribute('aria-label', state.immersiveRailOpen ? mirrorT('mirror.actions.hide_rail') : mirrorT('mirror.actions.show_rail'));
    toggle.title=state.immersiveRailOpen ? mirrorT('mirror.actions.hide_rail') : mirrorT('mirror.actions.show_rail');
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
  button.setAttribute('aria-label', state.immersive ? mirrorT('mirror.fullscreen.exit') : mirrorT('mirror.fullscreen.enter'));
  button.title=state.immersive ? mirrorT('mirror.fullscreen.exit') : mirrorT('mirror.fullscreen.enter');
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
      show(mirrorT('mirror.fullscreen.fallback'));
    }
  }
  if (!state.immersive || epoch!==state.immersiveEpoch) {
    if (fullscreenElement()===app) {
      const exit=document.exitFullscreen || document.webkitExitFullscreen;
      if (exit) Promise.resolve(exit.call(document)).catch(()=>{});
    }
    return;
  }
  ensureFullscreenQuality(epoch).catch(error=>show(error.message || mirrorT('mirror.fullscreen.quality_failed'), 5200));
}
async function exitImmersiveMode(){
  state.immersiveEpoch+=1;
  const exit=document.exitFullscreen || document.webkitExitFullscreen;
  let exitError=null;
  if (exit) {
    try { await exit.call(document); } catch (error) { exitError=error; }
  }
  if (fullscreenElement()) {
    if (exitError) show(mirrorT('mirror.fullscreen.exit_failed'));
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
  bindClick('startBtn', (_, button)=>runBusyAction('mirror', button, mirrorT('mirror.busy.starting'), startMirror).catch(e=>show(e.message)));
  bindClick('stopBtn', (_, button)=>runBusyAction('mirror', button, mirrorT('mirror.busy.stopping'), stopMirror).catch(e=>show(e.message)));
  bindClick('controlBtn', (_, button)=>runBusyAction('control', button, mirrorT('mirror.busy.control'), toggleControl).catch(e=>show(e.message)));
  bindClick('refreshBtn', (_, button)=>runBusyAction('refresh', button, mirrorT('mirror.busy.refresh'), ()=>loadAll({force:true, refreshAlasCatalog:true})).catch(e=>show(e.message)));
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
  bindClick('immersiveStopBtn', (_, button)=>runBusyAction('mirror', button, mirrorT('mirror.busy.stopping'), stopMirror).catch(e=>show(e.message)));
  bindClick('exitFullscreenBtn', ()=>exitImmersiveMode());
  bindClick('alasBtn', (_, button)=>{
    toggleToolPanel('alasTools', button);
    const panel=$('alasTools');
    if (panel && panel.classList.contains('open') && !state.alasConfigsLoaded) loadAlasPanel().catch(()=>{});
  });
  bindClick('accountBtn', (_, button)=>toggleToolPanel('accountTools', button));
  bindClick('toolDrawerCloseBtn', ()=>closeToolDrawer());
  bindClick('toolDrawerBackdrop', ()=>closeToolDrawer());
  bindClick('changePasswordBtn', (_, button)=>runBusyAction('password', button, mirrorT('mirror.busy.password'), changePassword).catch(e=>show(e.message)));
  bindClick('alasToggleRun', (_, button)=>runBusyAction('alas', button, mirrorT('mirror.busy.alas_update'), toggleAlas).catch(()=>{}));
  bindClick('alasReload', (_, button)=>runBusyAction('alas', button, mirrorT('mirror.busy.alas_refresh'), reloadAlas).catch(()=>{}));
  const alasConfigSelect=$('alasConfigSelect');
  if (alasConfigSelect) alasConfigSelect.onchange=event=>selectAlasConfig(event.currentTarget.value).catch(()=>{});
  const qualityStreamMode=$('qualityStreamMode');
  if (qualityStreamMode) qualityStreamMode.onchange=()=>runBusyAction('quality', qualityStreamMode, mirrorT('mirror.actions.apply_quality'), saveOrApplyQuality).catch(e=>show(e.message));
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
