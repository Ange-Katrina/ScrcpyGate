const bootstrap = JSON.parse(document.getElementById("scrcpygate-bootstrap").textContent);
const csrfToken = bootstrap.csrf_token;
const currentUsername = bootstrap.user.username;
const adminI18n = window.ScrcpyGateI18n;
const adminT = (key, values) => (
  adminI18n && typeof adminI18n.t === 'function' ? adminI18n.t(key, values) : String(key || '')
);
const state = { overview:null, users:[], devices:[], permissions:[], logs:[], runtimeLogs:[], video:{}, alas:null };
const NORMAL_PROFILE_NAMES = ['smooth','balanced','sharp','low_latency'];
const profileLabels = {smooth:'mirror.profile.smooth', balanced:'mirror.profile.balanced', sharp:'mirror.profile.sharp', low_latency:'mirror.profile.low_latency'};
const profileHints = {smooth:'admin.profile_hint.smooth', balanced:'admin.profile_hint.balanced', sharp:'admin.profile_hint.sharp', low_latency:'admin.profile_hint.low_latency'};
const STANDARD_OUTPUT_SIZES = Object.freeze([
  {maxSize:854,width:854,height:480,quality:'480p'},
  {maxSize:960,width:960,height:540,quality:'540p'},
  {maxSize:1280,width:1280,height:720,quality:'720p'},
  {maxSize:1600,width:1600,height:900,quality:'900p'},
  {maxSize:1920,width:1920,height:1080,quality:'1080p'}
]);
const CUSTOM_OUTPUT_SIZE = 'custom';
const MIN_OUTPUT_SIZE = 854;
const MAX_OUTPUT_SIZE = 1920;
const MIN_OUTPUT_HEIGHT = 480;
const MAX_OUTPUT_HEIGHT = 1080;
const customProfiles = {};
const $ = (id)=>document.getElementById(id);
const ADMIN_TAB_KEY = 'scrcpygate:admin:tab';
const ACCESS_VIEW_KEY = 'scrcpygate:admin:access:view';
const PERMISSION_USER_KEY = 'scrcpygate:admin:permissions:user';
const ALAS_USER_KEY = 'scrcpygate:admin:alas:user';
const ALAS_CONFIG_KEY = 'scrcpygate:admin:alas:config';
const USER_PAGE_SIZE = 20;
const USER_EXPIRY_REFRESH_INTERVAL = 30000;
const DEVICE_STATUS_POLL_INTERVAL = 5000;
const OVERVIEW_REFRESH_INTERVAL = 60000;
const STATUS_LABELS = {running:'admin.status.running', healthy:'admin.status.healthy', starting:'admin.status.starting', stopped:'admin.status.stopped', idle:'admin.status.idle', error:'admin.status.error', disabled:'admin.status.disabled', disconnected:'admin.status.disconnected', unknown:'admin.status.unknown', unbound:'admin.status.unbound'};
const ADB_STATUS_META = Object.freeze({
  online:{label:'admin.adb.online',tone:'ok'},
  offline:{label:'admin.adb.offline',tone:'danger'},
  network_unreachable:{label:'admin.adb.network_unreachable',tone:'danger'},
  unauthorized:{label:'admin.adb.unauthorized',tone:'warn'},
  checking:{label:'admin.adb.checking',tone:'warn'},
  reconnecting:{label:'admin.adb.reconnecting',tone:'warn'},
  disabled:{label:'admin.adb.disabled',tone:''},
  unknown:{label:'admin.adb.unknown',tone:'warn'}
});
const resourceRequests = new Map();
const resourceSequences = new Map();
const loadedResources = new Set();
const deviceProbeRequests = new Set();
const TAB_RESOURCES = {
  overview:['overview','overviewAlas'],
  devices:['devices'],
  users:['users','permissions'],
  video:['video'],
  alas:['alas'],
  logs:['logs','runtimeLogs']
};
let activeTab = 'overview';
let activeAccessView = localStorage.getItem(ACCESS_VIEW_KEY)==='permissions' ? 'permissions' : 'accounts';
let userAccountPage = 1;
let editingUsername = '';
let selectedPermissionUsername = localStorage.getItem(PERMISSION_USER_KEY) || '';
const permissionDrafts = new Map();
const permissionIndex = new Map();
let permissionsEpoch = 0;
let permissionsMutations = 0;
let permissionsNeedsRefresh = false;
let permissionsLoadPhase = 'idle';
let permissionsLoadError = '';
let activeAlasView = 'users';
let selectedAlasUsername = localStorage.getItem(ALAS_USER_KEY) || '';
let selectedAlasConfigName = localStorage.getItem(ALAS_CONFIG_KEY) || '';
let editingAlasAssignment = null;
let alasStatusLoading = false;
let alasStatusLoadingConfig = '';
let alasStatusSequence = 0;
let alasToggleSequence = 0;
let alasConfigDrawerTarget = '';
let alasConfigReadSequence = 0;
let alasConfigReadController = null;
let alasPermissionsEpoch = 0;
let alasPermissionsMutations = 0;
let alasPermissionsNeedsRefresh = false;
const adminNavMedia = window.matchMedia('(max-width: 920px)');
let adminNavReturnFocus = null;
let pendingConfirmation = null;
let deviceStatusPollTimer = null;
let deviceStatusPollGeneration = 0;
let overviewRefreshTimer = null;
let overviewRefreshGeneration = 0;
const EDITOR_DRAWERS = ['device','user','alasConnection','alasAssignment','alasConfig'];
function profileLabel(profile, labels={}){ return labels[profile] || (profileLabels[profile] ? adminT(profileLabels[profile]) : profile); }
function profileHint(profile){ return profileHints[profile] ? adminT(profileHints[profile]) : ''; }

function managedLayerOpen(){
  return !!document.querySelector('.admin-nav.is-open, .ui-drawer.is-open, dialog[open]');
}
function syncLayerScrollLock(){
  document.documentElement.classList.toggle('ui-layer-open', managedLayerOpen());
}
function setAdminWorkspaceHidden(hidden){
  const workspace=document.querySelector('.admin-workspace');
  if(!workspace) return;
  if(hidden){
    workspace.setAttribute('aria-hidden','true');
    workspace.setAttribute('inert','');
  } else {
    workspace.removeAttribute('aria-hidden');
    workspace.removeAttribute('inert');
  }
}
function focusableElements(container){
  if(!container) return [];
  return [...container.querySelectorAll('a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])')]
    .filter(element=>!element.hidden && element.getAttribute('aria-hidden')!=='true');
}
function syncAdminNavMode(){
  const nav=$('adminNav');
  const backdrop=$('adminNavBackdrop');
  const toggle=$('adminNavToggle');
  if(!nav || !backdrop || !toggle) return;
  if(!adminNavMedia.matches){
    clearTimeout(syncAdminNavMode.hideTimer);
    setAdminWorkspaceHidden(false);
    nav.classList.remove('is-open');
    nav.removeAttribute('aria-hidden');
    nav.removeAttribute('inert');
    backdrop.classList.remove('is-open');
    backdrop.hidden=true;
    toggle.setAttribute('aria-expanded','false');
    adminNavReturnFocus=null;
    syncLayerScrollLock();
    return;
  }
  if(!nav.classList.contains('is-open')){
    setAdminWorkspaceHidden(false);
    if(nav.contains(document.activeElement)) toggle.focus({preventScroll:true});
    nav.setAttribute('aria-hidden','true');
    nav.setAttribute('inert','');
    backdrop.hidden=true;
    toggle.setAttribute('aria-expanded','false');
  }
}
function openAdminNav(trigger=$('adminNavToggle')){
  if(!adminNavMedia.matches) return;
  const nav=$('adminNav');
  const backdrop=$('adminNavBackdrop');
  clearTimeout(syncAdminNavMode.hideTimer);
  adminNavReturnFocus=trigger || document.activeElement;
  nav.removeAttribute('inert');
  nav.setAttribute('aria-hidden','false');
  nav.classList.add('is-open');
  backdrop.hidden=false;
  requestAnimationFrame(()=>backdrop.classList.add('is-open'));
  $('adminNavToggle').setAttribute('aria-expanded','true');
  document.documentElement.classList.add('ui-layer-open');
  const active=nav.querySelector('[data-tab].active');
  requestAnimationFrame(()=>{
    if(!nav.classList.contains('is-open')) return;
    (active || focusableElements(nav)[0] || nav).focus({preventScroll:true});
    setAdminWorkspaceHidden(true);
  });
}
function closeAdminNav(restoreFocus=true){
  if(!adminNavMedia.matches) return;
  const nav=$('adminNav');
  const backdrop=$('adminNavBackdrop');
  const returnFocus=adminNavReturnFocus;
  nav.classList.remove('is-open');
  setAdminWorkspaceHidden(false);
  const focusTarget=restoreFocus && returnFocus && returnFocus.isConnected ? returnFocus : $('adminNavToggle');
  if(nav.contains(document.activeElement) && focusTarget) focusTarget.focus({preventScroll:true});
  nav.setAttribute('aria-hidden','true');
  nav.setAttribute('inert','');
  backdrop.classList.remove('is-open');
  $('adminNavToggle').setAttribute('aria-expanded','false');
  clearTimeout(syncAdminNavMode.hideTimer);
  syncAdminNavMode.hideTimer=setTimeout(()=>{
    if(!nav.classList.contains('is-open')) backdrop.hidden=true;
    syncLayerScrollLock();
  },180);
  adminNavReturnFocus=null;
}
function trapAdminNavFocus(event){
  const nav=$('adminNav');
  if(event.key!=='Tab' || !adminNavMedia.matches || !nav.classList.contains('is-open')) return;
  const focusable=focusableElements(nav);
  if(!focusable.length){ event.preventDefault(); nav.focus(); return; }
  const first=focusable[0];
  const last=focusable[focusable.length-1];
  if(event.shiftKey && document.activeElement===first){ event.preventDefault(); last.focus(); }
  else if(!event.shiftKey && document.activeElement===last){ event.preventDefault(); first.focus(); }
}
function closeEditorDrawer(name){
  const drawer=$(`${name}Drawer`);
  if(!drawer) return;
  if(name==='alasConfig') invalidateAlasConfigRead(true);
  if(name==='alasAssignment'){
    editingAlasAssignment=null;
    $('alasBindConfigCustom').value='';
    $('alasBindConfigCustomField').hidden=true;
    $('alasBindConfigCustom').disabled=true;
  }
  if(window.ScrcpyGateUI) window.ScrcpyGateUI.closeDrawer(drawer);
  else {
    drawer.classList.remove('is-open');
    drawer.hidden=true;
    drawer.setAttribute('aria-hidden','true');
    drawer.setAttribute('inert','');
    const backdrop=$(`${name}DrawerBackdrop`);
    if(backdrop){ backdrop.classList.remove('is-open'); backdrop.hidden=true; }
    syncLayerScrollLock();
  }
}
function openEditorDrawer(name, trigger=document.activeElement){
  closeAdminNav(false);
  EDITOR_DRAWERS.filter(other=>other!==name).forEach(closeEditorDrawer);
  const drawer=$(`${name}Drawer`);
  if(!drawer) return;
  drawer.dataset.uiBackdrop=`${name}DrawerBackdrop`;
  if(window.ScrcpyGateUI) window.ScrcpyGateUI.openDrawer(drawer, trigger);
  else {
    const backdrop=$(`${name}DrawerBackdrop`);
    if(backdrop){ backdrop.hidden=false; backdrop.classList.add('is-open'); }
    drawer.hidden=false;
    drawer.removeAttribute('inert');
    drawer.setAttribute('aria-hidden','false');
    drawer.classList.add('is-open');
    document.documentElement.classList.add('ui-layer-open');
    requestAnimationFrame(()=>((focusableElements(drawer)[0] || drawer).focus({preventScroll:true})));
  }
}
function restoreConfirmationFocus(trigger){
  if(!trigger || !trigger.isConnected) return;
  requestAnimationFrame(()=>requestAnimationFrame(()=>{
    if(trigger.isConnected && !trigger.disabled) trigger.focus({preventScroll:true});
  }));
}
function settleConfirmation(confirmed){
  const pending=pendingConfirmation;
  if(!pending) return;
  pendingConfirmation=null;
  if(window.ScrcpyGateUI) window.ScrcpyGateUI.closeDialog($('confirmDialog'), confirmed?'confirm':'cancel');
  else if($('confirmDialog').open) $('confirmDialog').close(confirmed?'confirm':'cancel');
  pending.resolve(confirmed);
  if(!confirmed) restoreConfirmationFocus(pending.trigger);
}
function confirmDanger({title=adminT('admin.actions.confirm_danger'), message, confirmText=adminT('admin.actions.confirm'), trigger=document.activeElement}){
  if(pendingConfirmation) settleConfirmation(false);
  $('confirmDialogTitle').textContent=title;
  $('confirmDialogMessage').textContent=message || adminT('admin.actions.irreversible');
  $('confirmDialogConfirm').textContent=confirmText;
  return new Promise(resolve=>{
    pendingConfirmation={resolve, trigger};
    if(window.ScrcpyGateUI) window.ScrcpyGateUI.openDialog($('confirmDialog'), trigger);
    else $('confirmDialog').showModal();
  });
}
function applyTableLabels(target){
  const table=target && (target.matches('table') ? target : target.closest('table'));
  if(!table) return;
  const labels=[...table.querySelectorAll('thead th')].map(header=>header.textContent.trim());
  table.querySelectorAll('tbody tr').forEach(row=>{
    [...row.children].forEach((cell,index)=>cell.setAttribute('data-label', labels[index] || ''));
  });
}
const LOG_VIEWS = {
  logs:{status:'auditLogsStatus', target:'logRows', label:'admin.logs.audit'},
  runtimeLogs:{status:'runtimeLogsStatus', target:'runtimeLogs', label:'admin.logs.runtime'}
};
function setLogLoadState(name, phase, message){
  const view=LOG_VIEWS[name];
  if(!view) return;
  const status=$(view.status);
  const target=$(view.target);
  status.dataset.state=phase;
  status.textContent=message;
  status.setAttribute('role', phase==='error'?'alert':'status');
  if(phase==='loading') target.setAttribute('aria-busy','true');
  else target.removeAttribute('aria-busy');
}
function logReadyMessage(label, count){
  return `${adminT('admin.actions.logs_loaded',{label:adminT(label),count})} · ${new Date().toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'})}`;
}
function getDeviceId(device){ return String((device && (device.device_id || device.id || device.address)) || '').trim(); }
function statusLabel(value){ const key=String(value || 'unknown').trim(); return adminT(STATUS_LABELS[key] || 'admin.status.unknown'); }
function setBusy(el, busy, text=adminT('admin.actions.processing')){
  if(!el) return;
  if(busy) {
    el.dataset.readyText=el.textContent;
    el.dataset.busyText=text;
  }
  el.disabled=!!busy;
  el.classList.toggle('busy', !!busy);
  if(busy) el.textContent=text;
  else if(!el.dataset.busyText || el.textContent===el.dataset.busyText) el.textContent=el.dataset.readyText;
  if(!busy) delete el.dataset.busyText;
}
async function withBusy(el, fn, text=adminT('admin.actions.processing')){
  setBusy(el, true, text);
  try { return await fn(); }
  finally { setBusy(el, false); }
}
function bindAction(id, fn, text){
  const el=$(id);
  if(!el) return;
  el.onclick=()=>withBusy(el, fn, text).catch(e=>show(e.message));
}
function show(message){ const n=$('notice'); n.textContent=message || adminT('admin.actions.failed'); n.classList.add('show'); clearTimeout(show.t); show.t=setTimeout(()=>n.classList.remove('show'),3200); }
const API_ERROR_MESSAGES = Object.freeze({
  last_permanent_admin_required:'admin.api_error.last_permanent_admin_required',
  last_admin_required:'admin.api_error.last_admin_required',
  invalid_expires_at:'admin.api_error.invalid_expires_at',
  password_required:'admin.api_error.password_required'
});
function apiErrorMessage(detail){ const key=API_ERROR_MESSAGES[String(detail || '')]; return key ? adminT(key) : detail; }
async function api(url, options={}){ const opts=Object.assign({}, options, {headers:Object.assign({}, options.headers || {})}); if(opts.body && typeof opts.body !== 'string'){ opts.headers['content-type']='application/json'; opts.body=JSON.stringify(opts.body); } if(!['GET','HEAD'].includes((opts.method||'GET').toUpperCase())) opts.headers['x-csrf-token']=csrfToken; const res=await fetch(url, opts); const text=await res.text(); let data={}; try{ data=text?JSON.parse(text):{}; }catch(_){ data={detail:text}; } if(!res.ok) throw new Error(apiErrorMessage(data.detail) || `HTTP ${res.status}`); return data; }
function isAbortError(error){ return !!error && (error.name === 'AbortError' || error.code === 20); }
function reportRequestError(error, prefix=adminT('admin.actions.data_load_failed')){
  if(!isAbortError(error)) show(`${prefix}${error && error.message ? `：${error.message}` : ''}`);
}
function requestResource(name, request, apply, options={}){
  const force=!!options.force;
  const previous=resourceRequests.get(name);
  if(previous && !force) return previous.promise;
  if(previous) previous.controller.abort();
  const controller=new AbortController();
  const sequence=(resourceSequences.get(name) || 0) + 1;
  resourceSequences.set(name, sequence);
  const record={controller, sequence, promise:null};
  const promise=(async()=>{
    try{
      const data=await request(controller.signal);
      if(controller.signal.aborted || resourceSequences.get(name)!==sequence) return undefined;
      apply(data || {});
      loadedResources.add(name);
      return data;
    } finally {
      if(resourceRequests.get(name)===record) resourceRequests.delete(name);
    }
  })();
  record.promise=promise;
  resourceRequests.set(name, record);
  return promise;
}
function loadIfNeeded(name, loader, options={}){
  if(!options.force && loadedResources.has(name)) return Promise.resolve();
  return loader(options);
}
function markResourceStale(name){
  loadedResources.delete(name);
  resourceSequences.set(name, (resourceSequences.get(name) || 0) + 1);
  const previous=resourceRequests.get(name);
  if(previous) previous.controller.abort();
  resourceRequests.delete(name);
}
function clear(el){ el.textContent=''; }
function chip(text, cls=''){ const s=document.createElement('span'); s.className=`chip ${cls}`.trim(); s.textContent=text; return s; }
function td(text){ const cell=document.createElement('td'); cell.textContent=text == null ? '' : String(text); return cell; }
function btn(text, cls, fn){ const b=document.createElement('button'); b.className=`btn ${cls||''}`.trim(); b.type='button'; b.textContent=text; b.onclick=()=>{ try{ const result=fn && fn(); if(result && typeof result.then==='function') withBusy(b, ()=>result).catch(e=>show(e.message)); }catch(e){ show(e.message); } }; return b; }
function heartbeatField(label,key,value){ const row=document.createElement('div'); const term=document.createElement('dt'); term.textContent=label; const detail=document.createElement('dd'); detail.setAttribute(`data-device-${key}`,''); detail.textContent=value; row.append(term,detail); return row; }
function ts(value){ return value ? new Date(value * 1000).toLocaleString() : ''; }
function relativeTs(value){ const timestamp=Number(value); if(!Number.isFinite(timestamp) || timestamp<=0) return ''; const seconds=Math.max(0,Math.round(Date.now()/1000-timestamp)); if(seconds<10) return adminT('admin.time.just_now'); if(seconds<60) return adminT('admin.time.seconds_ago',{value:seconds}); if(seconds<3600) return adminT('admin.time.minutes_ago',{value:Math.floor(seconds/60)}); if(seconds<86400) return adminT('admin.time.hours_ago',{value:Math.floor(seconds/3600)}); return new Date(timestamp*1000).toLocaleString([], {month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'}); }
function actionText(action){ const key=`admin.audit.${action}`; return adminI18n && typeof adminI18n.has === 'function' && adminI18n.has(key) ? adminT(key) : action; }
function activeSessions(){ return state.devices.filter(d => d.session && d.session.running).length; }
function overviewDevices(){ return state.overview && Array.isArray(state.overview.devices) ? state.overview.devices : []; }
function overviewUsers(){ return state.overview && Array.isArray(state.overview.users) ? state.overview.users : []; }
function updateText(element,value){ const text=String(value == null ? '' : value); if(element.textContent!==text) element.textContent=text; }
function alasOverviewErrorText(value){
  const text=String(value || '');
  const normalized=text.toLowerCase();
  if(normalized.includes('control is disabled')) return adminT('admin.overview.alas_control_disabled');
  if(normalized.includes('token is not configured')) return adminT('admin.overview.alas_token_missing');
  if(normalized.includes('unreachable') || normalized.includes('connection refused')) return adminT('admin.overview.alas_unreachable');
  if(normalized.includes('timed out') || normalized.includes('timeout')) return adminT('admin.overview.alas_timeout');
  if(normalized.includes('invalid config catalog')) return adminT('admin.overview.alas_catalog_invalid');
  return text;
}
function updateOverviewRows(container,items,emptyMessage){
  const existing=new Map([...container.querySelectorAll('.overview-row[data-overview-key]')].map(row=>[row.dataset.overviewKey,row]));
  const empty=container.querySelector('.overview-empty');
  if(!items.length){
    existing.forEach(row=>row.remove());
    const placeholder=empty || document.createElement('p');
    placeholder.className='overview-empty';
    updateText(placeholder,emptyMessage);
    if(!placeholder.parentNode) container.appendChild(placeholder);
    return;
  }
  if(empty) empty.remove();
  let nextNode=container.firstElementChild;
  items.forEach(item=>{
    const key=String(item.key);
    let row=existing.get(key);
    if(!row){
      row=document.createElement('div');
      row.className='overview-row';
      row.dataset.overviewKey=key;
      const identity=document.createElement('div');
      identity.className='overview-row__identity';
      const name=document.createElement('strong');
      const meta=document.createElement('small');
      identity.append(name,meta);
      const badge=document.createElement('span');
      const detail=document.createElement('div');
      detail.className='overview-row__detail';
      row.append(identity,badge,detail);
    }
    const identity=row.querySelector('.overview-row__identity');
    updateText(identity.querySelector('strong'),item.title);
    updateText(identity.querySelector('small'),item.subtitle);
    const badge=row.querySelector('span');
    badge.className=`chip ${item.tone || ''}`.trim();
    updateText(badge,item.badge);
    const detail=row.querySelector('.overview-row__detail');
    updateText(detail,item.detail || '');
    detail.hidden=!item.detail;
    if(row!==nextNode) container.insertBefore(row,nextNode);
    nextNode=row.nextElementSibling;
    existing.delete(key);
  });
  existing.forEach(row=>row.remove());
}
function renderOverviewHeader(){
  const devices=overviewDevices();
  const sessionCount=devices.filter(device=>device.session&&device.session.running).length;
  const viewerCount=devices.reduce((total,device)=>total+Number(device.session&&device.session.running&&device.session.clients || 0),0);
  $('summaryMirror').textContent=adminT('admin.overview.mirror_summary',{sessions:sessionCount,viewers:viewerCount});
  $('summaryMirror').className='chip ' + (sessionCount?'ok':'');
  const overviewAlas=state.overview && state.overview.alas;
  const alasStatus=(overviewAlas && overviewAlas.status) || (state.alas && state.alas.status && state.alas.status.status) || 'unknown';
  const alasConfigCount=Number(overviewAlas&&overviewAlas.config_count || 0);
  const alasRunningCount=Number(overviewAlas&&overviewAlas.running_count || 0);
  $('summaryAlas').textContent=overviewAlas&&overviewAlas.enabled ? `ALAS ${alasRunningCount}/${alasConfigCount}` : `ALAS ${statusLabel(alasStatus)}`;
  $('summaryAlas').className='chip ' + (alasStatus==='running'?'ok':alasStatus==='error'?'warn':'');
}
function renderOverviewDevices(){
  const devices=overviewDevices();
  const onlineDevices=devices.filter(device=>deviceAdbMeta(device).state==='online').length;
  updateText($('overviewDevicesMeta'),adminT('admin.overview.devices_summary',{online:onlineDevices,total:devices.length}));
  const priority={offline:0,network_unreachable:0,unauthorized:1,checking:2,reconnecting:2,unknown:3,disabled:4,online:5};
  const rows=[...devices].sort((a,b)=>(priority[deviceAdbMeta(a).state]??9)-(priority[deviceAdbMeta(b).state]??9)||String(a.name||getDeviceId(a)).localeCompare(String(b.name||getDeviceId(b)))).map(device=>{
    const heartbeat=deviceAdbMeta(device);
    const hasLatency=device.latency_ms!==null&&device.latency_ms!==undefined&&Number.isFinite(Number(device.latency_ms));
    const checked=relativeTs(device.last_checked_at) || adminT('admin.devices.not_checked');
    const seen=relativeTs(device.last_seen_at) || adminT('admin.devices.not_seen');
    return {key:getDeviceId(device),title:device.name||getDeviceId(device),subtitle:`${hasLatency?`${Math.round(Number(device.latency_ms))} ms`:adminT('admin.overview.no_latency')} · ${checked}`,badge:heartbeat.label,tone:heartbeat.tone,detail:adminT('admin.overview.last_online',{value:seen})};
  });
  updateOverviewRows($('overviewDevices'),rows,adminT('admin.overview.no_devices'));
}
function renderOverviewUsers(){
  const users=overviewUsers();
  const expirationOrder={expired:0,expiring:1,active:2,permanent:3};
  const sorted=[...users].sort((a,b)=>(expirationOrder[userExpirationState(a)]??9)-(expirationOrder[userExpirationState(b)]??9)||a.username.localeCompare(b.username));
  const expiredUsers=sorted.filter(user=>userExpirationState(user)==='expired').length;
  updateText($('overviewUsersMeta'),adminT('admin.overview.users_summary',{total:sorted.length,expired:expiredUsers}));
  const rows=sorted.map(user=>{
    const expiryState=userExpirationState(user);
    const label=adminT(`admin.time.${expiryState}`);
    const tone=expiryState==='expired'?'danger':expiryState==='expiring'?'warn':'ok';
    const remaining=user.expires_at==null ? '' : formatRemainingSeconds(Number(user.expires_at)-Math.floor(Date.now()/1000));
    const detail=user.expires_at==null ? adminT('admin.overview.no_expiry') : expiryState==='expired' ? adminT('admin.overview.expired_at',{value:formatUserExpiryDate(user.expires_at)}) : adminT('admin.overview.valid_until',{date:formatUserExpiryDate(user.expires_at),remaining});
    return {key:user.username,title:user.username,subtitle:accessRoleLabel(user.role),badge:label,tone,detail};
  });
  updateOverviewRows($('overviewUsers'),rows,adminT('admin.overview.no_users'));
}
function renderOverviewMirrors(){
  const running=overviewDevices().filter(device=>device.session&&device.session.running);
  const viewerCount=running.reduce((total,device)=>total+Math.max(0,Number(device.session.clients)||0),0);
  updateText($('overviewMirrorMeta'),adminT('admin.overview.mirrors_summary',{sessions:running.length,viewers:viewerCount}));
  const rows=running.map(device=>{
    const session=device.session;
    const viewers=Math.max(0,Number(session.clients)||0);
    const lock=session.control_lock;
    return {key:getDeviceId(device),title:device.name||getDeviceId(device),subtitle:`${statusLabel(session.stream_health||'running')} · ${session.stream_mode||'raw'}`,badge:adminT('admin.overview.viewer_count',{count:viewers}),tone:viewers?'ok':'',detail:lock?adminT('admin.overview.control_owner',{owner:lock.username||lock.owner||adminT('admin.overview.occupied')}):adminT('admin.overview.control_available')};
  });
  updateOverviewRows($('overviewMirror'),rows,adminT('admin.overview.no_mirror'));
}
function renderOverviewAlas(){
  const overviewAlas=state.overview&&state.overview.alas;
  const alasConfigCount=Number(overviewAlas&&overviewAlas.config_count || 0);
  const alasRunningCount=Number(overviewAlas&&overviewAlas.running_count || 0);
  updateText($('overviewAlasMeta'),adminT('admin.overview.alas_summary',{running:alasRunningCount,total:alasConfigCount}));
  const legacyConfigs=Array.isArray(overviewAlas&&overviewAlas.configs)&&overviewAlas.configs.every(item=>item&&typeof item==='object') ? overviewAlas.configs : [];
  const configs=Array.isArray(overviewAlas&&overviewAlas.config_statuses) ? overviewAlas.config_statuses : legacyConfigs;
  const priority={running:0,starting:1,error:2,disconnected:3,stopped:4,idle:5,disabled:6,unknown:7};
  const rows=[...configs].sort((a,b)=>(priority[String(a.status||'unknown')]??9)-(priority[String(b.status||'unknown')]??9)||String(a.config||'').localeCompare(String(b.config||''))).map(config=>{
    const status=String(config.status || 'unknown');
    const tone=status==='running'?'ok':status==='error'||!config.ok?'danger':status==='disabled'||status==='disconnected'?'warn':'';
    const owner=config.username ? adminT('admin.overview.owner',{username:config.username}) : adminT('admin.overview.unassigned_user');
    const detail=config.task ? adminT('admin.overview.current_task',{task:config.task}) : alasOverviewErrorText(config.error) || adminT('admin.overview.no_task');
    return {key:config.config||'unnamed',title:config.config||adminT('admin.overview.unnamed_config'),subtitle:owner,badge:statusLabel(status),tone,detail};
  });
  const emptyMessage=overviewAlas&&overviewAlas.error&&overviewAlas.enabled ? adminT('admin.overview.status_failed',{error:alasOverviewErrorText(overviewAlas.error)}) : overviewAlas&&overviewAlas.enabled ? adminT('admin.overview.loading_runtime') : adminT('admin.overview.alas_disabled');
  updateOverviewRows($('overviewAlas'),rows,emptyMessage);
}
function renderOverview(){
  renderOverviewHeader();
  renderOverviewDevices();
  renderOverviewUsers();
  renderOverviewMirrors();
  renderOverviewAlas();
}
function renderDevices(){
  const box=$('deviceCards');
  clear(box);
  if(!state.devices.length){ box.appendChild(chip(adminT('admin.overview.no_devices'),'warn')); return; }
  state.devices.forEach(device=>{
    const id=getDeviceId(device);
    const card=document.createElement('div');
    card.className='device-card';
    card.dataset.deviceId=id;
    const heading=document.createElement('div');
    heading.className='device-card__heading';
    const title=document.createElement('h3');
    title.textContent=device.name || id;
    const adbMeta=deviceAdbMeta(device);
    const statusChip=chip(adbMeta.label,adbMeta.tone);
    statusChip.dataset.deviceStatus='1';
    heading.append(title,statusChip);
    const address=document.createElement('div');
    address.className='device-address';
    address.textContent=`ADB · ${device.address || id}`;
    const statuses=document.createElement('div');
    statuses.className='chips';
    statuses.appendChild(chip(adminT(device.enabled?'admin.devices.enabled':'admin.devices.disabled'), device.enabled?'ok':'warn'));
    statuses.appendChild(chip(adminT(device.session&&device.session.running?'admin.devices.mirroring':'admin.devices.not_mirroring'), device.session&&device.session.running?'ok':''));
    if(device.session&&device.session.control_lock) statuses.appendChild(chip(adminT('admin.devices.control_owner',{owner:device.session.control_lock.username || adminT('admin.overview.occupied')}), 'warn'));
    const heartbeat=document.createElement('dl');
    heartbeat.className='device-heartbeat';
    heartbeat.append(heartbeatField(adminT('admin.devices.network_latency'),'latency','—'),heartbeatField(adminT('admin.devices.last_checked'),'checked',adminT('admin.devices.not_checked')),heartbeatField(adminT('admin.devices.last_seen'),'seen',adminT('admin.devices.not_seen')));
    const actions=document.createElement('div');
    actions.className='actions';
    const probeButton=btn(adminT(deviceProbeRequests.has(id)?'admin.devices.checking':'admin.devices.check_now'),'',()=>testDevice(id));
    probeButton.dataset.deviceProbe='1';
    probeButton.disabled=deviceProbeRequests.has(id);
    actions.append(
      btn(adminT('admin.devices.edit'),'',()=>editDevice(device)),
      probeButton,
      btn(adminT('admin.devices.delete'),'danger',()=>deleteDevice(id))
    );
    card.append(heading,address,statuses,heartbeat,actions);
    box.appendChild(card);
    updateDeviceCardHeartbeat(device);
  });
}
function deviceAdbMeta(device){
  const raw=device && device.enabled===false ? 'disabled' : String(device && (device.adb_state || device.status_label) || 'unknown').toLowerCase();
  const meta=ADB_STATUS_META[raw] || ADB_STATUS_META.unknown;
  return {state:raw,label:adminT(meta.label),tone:meta.tone};
}
function applyDeviceAdbResult(id,result={}){
  const device=state.devices.find(item=>getDeviceId(item)===id);
  if(!device) return null;
  const nextState=String(result.adb_state || result.state || 'unknown').toLowerCase();
  Object.assign(device,{
    adb_state:nextState,
    adb_ok:Boolean(result.adb_ok ?? result.ok),
    status_label:result.status_label || nextState,
    adb_detail:result.detail || '',
    last_error:result.last_error || (result.ok ? '' : result.detail || ''),
    last_checked_at:result.last_checked_at || device.last_checked_at || null,
    last_seen_at:result.last_seen_at || device.last_seen_at || null,
    latency_ms:result.latency_ms ?? null
  });
  updateDeviceCardHeartbeat(device);
  return device;
}
function updateDeviceCardHeartbeat(device){
  const id=getDeviceId(device);
  const card=$('deviceCards').querySelector(`.device-card[data-device-id="${CSS.escape(id)}"]`);
  if(!card) return;
  const meta=deviceAdbMeta(device);
  const badge=card.querySelector('[data-device-status]');
  if(badge){ badge.textContent=meta.label; badge.className=`chip ${meta.tone || ''}`.trim(); badge.dataset.deviceStatus='1'; }
  const latency=card.querySelector('[data-device-latency]');
  const checked=card.querySelector('[data-device-checked]');
  const seen=card.querySelector('[data-device-seen]');
  const hasLatency=device.latency_ms!==null && device.latency_ms!==undefined && device.latency_ms!=='' && Number.isFinite(Number(device.latency_ms));
  if(latency) latency.textContent=hasLatency ? `${Math.round(Number(device.latency_ms))} ms` : '—';
  if(checked){ checked.textContent=relativeTs(device.last_checked_at) || adminT('admin.devices.not_checked'); checked.title=ts(device.last_checked_at); }
  if(seen){ seen.textContent=relativeTs(device.last_seen_at) || adminT('admin.devices.not_seen'); seen.title=ts(device.last_seen_at); }
  const probe=card.querySelector('[data-device-probe]');
  if(probe){ probe.textContent=adminT(deviceProbeRequests.has(id)?'admin.devices.checking':'admin.devices.check_now'); probe.disabled=deviceProbeRequests.has(id); }
}
function applyDeviceStatuses(data){
  const statuses=data && data.devices || {};
  Object.entries(statuses).forEach(([id,status])=>applyDeviceAdbResult(id,status || {}));
}
function bitrateBpsToMbps(value){ const bps=Number(value); return Number.isFinite(bps) ? bps / 1000000 : ''; }
function bitrateMbpsToBps(value){ const mbps=Number(value); return Number.isFinite(mbps) ? Math.round(mbps * 1000000) : 0; }
function outputSizeMeta(value){ const maxSize=Math.min(MAX_OUTPUT_SIZE,Math.max(MIN_OUTPUT_SIZE,Math.round(Number(value)||MIN_OUTPUT_SIZE))); const standard=STANDARD_OUTPUT_SIZES.find(item=>item.maxSize===maxSize); return standard || {maxSize,width:maxSize,height:Math.round(maxSize*9/16),quality:''}; }
function dimensionsFromHeight(value){ const height=Math.min(MAX_OUTPUT_HEIGHT,Math.max(MIN_OUTPUT_HEIGHT,Math.round(Number(value)||MIN_OUTPUT_HEIGHT))); const width=Math.min(MAX_OUTPUT_SIZE,Math.max(MIN_OUTPUT_SIZE,Math.ceil(height*16/9))); return {maxSize:width,width,height:Math.round(width*9/16),quality:''}; }
function outputSizeOptionLabel(item){ return `${item.width} × ${item.height}${item.quality ? `（${item.quality}）` : ''}`; }
function maxSizeQualityLabel(value){ const size=Number(value); if(!Number.isFinite(size) || size<=0) return adminT('mirror.quality.raw_size'); const item=outputSizeMeta(size); return adminT('admin.video.size_long_edge',{size:outputSizeOptionLabel(item),edge:item.maxSize}); }
function presetFieldDisplayValue(field, value){ return field==='video_bit_rate' ? bitrateBpsToMbps(value) : value; }
function populateOutputSizeSelect(select,value){ clear(select); STANDARD_OUTPUT_SIZES.forEach(item=>{ const option=document.createElement('option'); option.value=String(item.maxSize); option.textContent=outputSizeOptionLabel(item); select.appendChild(option); }); const custom=document.createElement('option'); custom.value=CUSTOM_OUTPUT_SIZE; custom.textContent=adminT('admin.video.custom_size_option'); select.appendChild(custom); select.value=STANDARD_OUTPUT_SIZES.some(item=>item.maxSize===Number(value)) ? String(value) : CUSTOM_OUTPUT_SIZE; }
function syncPresetSizeControl(control,value,forceCustom=false){ const select=control.querySelector('select'); const editor=control.querySelector('.custom-size-editor'); const width=control.querySelector('[data-custom-width]'); const height=control.querySelector('[data-custom-height]'); const output=control.querySelector('output'); const item=outputSizeMeta(value); const standard=!forceCustom && STANDARD_OUTPUT_SIZES.some(option=>option.maxSize===item.maxSize); select.value=standard ? String(item.maxSize) : CUSTOM_OUTPUT_SIZE; width.value=String(item.width); height.value=String(item.height); editor.hidden=select.value!==CUSTOM_OUTPUT_SIZE; width.disabled=editor.hidden; height.disabled=editor.hidden; output.value=adminT('admin.video.long_edge',{value:item.maxSize}); output.textContent=output.value; }
function syncPresetCustomDimensions(control,source){ const width=control.querySelector('[data-custom-width]'); const height=control.querySelector('[data-custom-height]'); const output=control.querySelector('output'); const sourceInput=source==='height' ? height : width; if(!sourceInput.checkValidity()){ output.value=source==='height' ? adminT('admin.video.height_range',{min:MIN_OUTPUT_HEIGHT,max:MAX_OUTPUT_HEIGHT}) : adminT('admin.video.width_range',{min:MIN_OUTPUT_SIZE,max:MAX_OUTPUT_SIZE}); output.textContent=output.value; return; } const item=source==='height' ? dimensionsFromHeight(height.value) : outputSizeMeta(width.value); if(source==='height'){ width.value=String(item.width); height.value=String(item.height); } else height.value=String(item.height); output.value=adminT('admin.video.long_edge',{value:Math.max(Number(width.value),Number(height.value))}); output.textContent=output.value; }
function presetSizeControl(profile,value){ const control=document.createElement('div'); control.className='preset-size-control'; const select=document.createElement('select'); select.dataset.presetProfile=profile; select.dataset.presetField='max_size'; const label=profileLabel(profile); select.setAttribute('aria-label',`${label}${adminT('admin.video.output_size')}`); populateOutputSizeSelect(select,value); const editor=document.createElement('div'); editor.className='custom-size-editor'; const pair=document.createElement('div'); pair.className='custom-size-pair'; pair.setAttribute('role','group'); pair.setAttribute('aria-label',`${label}${adminT('admin.video.custom_output_size')}`); const widthLabel=document.createElement('label'); widthLabel.className='custom-size-field'; const widthText=document.createElement('span'); widthText.textContent=adminT('admin.video.width_px'); const width=document.createElement('input'); width.type='number'; width.min=String(MIN_OUTPUT_SIZE); width.max=String(MAX_OUTPUT_SIZE); width.step='1'; width.inputMode='numeric'; width.required=true; width.dataset.customWidth='1'; width.setAttribute('aria-label',`${label}${adminT('admin.video.custom_width')}`); widthLabel.append(widthText,width); const separator=document.createElement('span'); separator.className='custom-size-separator'; separator.setAttribute('aria-hidden','true'); separator.textContent='×'; const heightLabel=document.createElement('label'); heightLabel.className='custom-size-field'; const heightText=document.createElement('span'); heightText.textContent=adminT('admin.video.height_px'); const height=document.createElement('input'); height.type='number'; height.min=String(MIN_OUTPUT_HEIGHT); height.max=String(MAX_OUTPUT_HEIGHT); height.step='1'; height.inputMode='numeric'; height.required=true; height.dataset.customHeight='1'; height.setAttribute('aria-label',`${label}${adminT('admin.video.custom_height')}`); heightLabel.append(heightText,height); const output=document.createElement('output'); output.setAttribute('aria-live','polite'); pair.append(widthLabel,separator,heightLabel); editor.append(pair,output); control.append(select,editor); select.onchange=()=>{ syncPresetSizeControl(control,select.value===CUSTOM_OUTPUT_SIZE ? width.value : select.value,select.value===CUSTOM_OUTPUT_SIZE); refreshFullscreenProfilesFromForm(); }; width.oninput=()=>{ syncPresetCustomDimensions(control,'width'); refreshFullscreenProfilesFromForm(); }; height.oninput=()=>{ syncPresetCustomDimensions(control,'height'); refreshFullscreenProfilesFromForm(); }; syncPresetSizeControl(control,value); return control; }
function presetInput(profile, field, value){ if(field==='max_size') return presetSizeControl(profile,value); const input=document.createElement('input'); input.type='number'; input.value=value == null ? '' : presetFieldDisplayValue(field, value); input.dataset.presetProfile=profile; input.dataset.presetField=field; if(field==='video_bit_rate'){ input.min='0.1'; input.max='100'; input.step='0.05'; input.inputMode='decimal'; } else { input.min='1'; input.max='60'; input.step='1'; } return input; }
function setPresetValue(profile, field, value){ const input=document.querySelector(`[data-preset-profile="${profile}"][data-preset-field="${field}"]`); if(!input) return; if(field==='max_size'){ syncPresetSizeControl(input.closest('.preset-size-control'),value); return; } input.value=presetFieldDisplayValue(field, value); }
function renderBandwidthActions(recommendations){ const box=$('bandwidthPresetActions'); if(!box) return; clear(box); Object.keys(recommendations).sort((a,b)=>parseInt(a)-parseInt(b)).forEach(name=>box.appendChild(btn(name.toUpperCase(), 'warn', ()=>applyBandwidthRecommendation(name)))); }
function applyBandwidthRecommendation(name){ const rec=((state.video || {}).bandwidth_recommendations || {})[name]; if(!rec) return; Object.entries(rec).forEach(([profile, values])=>['video_bit_rate','max_size','max_fps'].forEach(field=>setPresetValue(profile, field, values[field]))); refreshFullscreenProfilesFromForm(); show(adminT('admin.video.bandwidth_filled',{name:name.toUpperCase()})); }
async function removeCustomProfile(id){
  const confirmed=await confirmDanger({
    title:adminT('admin.actions.profile_remove'),
    message:adminT('admin.video.remove_profile_message',{id}),
    confirmText:adminT('admin.actions.profile_remove_confirm')
  });
  if(!confirmed) return;
  delete customProfiles[id];
  renderCustomProfiles();
  refreshFullscreenProfilesFromForm();
  show(adminT('admin.video.profile_removed'));
}
function syncCustomProfileSizeEditor(source='select'){ const select=$('customProfileSizeSelect'); const width=$('customProfileWidth'); const height=$('customProfileHeight'); const wrap=$('customProfileSizeCustomWrap'); const output=$('customProfileSizeReference'); if(!select || !width || !height || !wrap || !output) return; const custom=select.value===CUSTOM_OUTPUT_SIZE; wrap.hidden=!custom; width.disabled=!custom; height.disabled=!custom; if(!custom){ const item=outputSizeMeta(select.value); width.value=String(item.width); height.value=String(item.height); output.value=adminT('admin.video.long_edge',{value:item.maxSize}); } else { const sourceInput=source==='height' ? height : width; if(!sourceInput.checkValidity()){ output.value=source==='height' ? adminT('admin.video.height_range',{min:MIN_OUTPUT_HEIGHT,max:MAX_OUTPUT_HEIGHT}) : adminT('admin.video.width_range',{min:MIN_OUTPUT_SIZE,max:MAX_OUTPUT_SIZE}); output.textContent=output.value; return; } const item=source==='height' ? dimensionsFromHeight(height.value) : outputSizeMeta(width.value); if(source==='height'){ width.value=String(item.width); height.value=String(item.height); } else height.value=String(item.height); output.value=adminT('admin.video.long_edge',{value:Math.max(Number(width.value),Number(height.value))}); } output.textContent=output.value; }
function setCustomProfileSize(value){ const select=$('customProfileSizeSelect'); const width=$('customProfileWidth'); const height=$('customProfileHeight'); if(!select || !width || !height) return; const item=outputSizeMeta(value); const standard=STANDARD_OUTPUT_SIZES.some(option=>option.maxSize===Number(value)); select.value=standard ? String(item.maxSize) : CUSTOM_OUTPUT_SIZE; width.value=String(item.width); height.value=String(item.height); syncCustomProfileSizeEditor(); }
function customProfileSizeValue(){ const select=$('customProfileSizeSelect'); if(select && select.value!==CUSTOM_OUTPUT_SIZE) return outputSizeMeta(select.value).maxSize; return Math.max(Number($('customProfileWidth').value),Number($('customProfileHeight').value)); }
function renderCustomProfiles(){
  const rows=$('customProfileRows');
  if(!rows) return;
  clear(rows);
  Object.keys(customProfiles).sort().forEach(id=>{
    const profile=customProfiles[id] || {};
    const tr=document.createElement('tr');
    tr.append(td(id), td(profile.label || id), td(bitrateBpsToMbps(profile.video_bit_rate)), td(maxSizeQualityLabel(profile.max_size)), td(profile.max_fps));
    const actions=document.createElement('td');
    actions.className='actions';
    actions.append(
      btn(adminT('admin.devices.edit'),'',()=>{
        $('customProfileId').value=id;
        $('customProfileLabel').value=profile.label || id;
        $('customProfileBitrate').value=bitrateBpsToMbps(profile.video_bit_rate || 900000);
        setCustomProfileSize(profile.max_size || 960);
        $('customProfileFps').value=profile.max_fps || 24;
      }),
      btn(adminT('admin.devices.delete'),'danger',()=>removeCustomProfile(id))
    );
    tr.appendChild(actions);
    rows.appendChild(tr);
  });
  applyTableLabels(rows);
}
function streamModeLabel(mode){ return mode === 'legacy' ? adminT('admin.video.legacy_label') : mode; }
function renderVideoStreamModes(data, selected){ const modes=data.stream_modes || ['raw','protocol','legacy']; const enabled=new Set(data.enabled_stream_modes || ['raw']); enabled.add('raw'); const toggles=$('streamModeToggles'); if(toggles){ clear(toggles); modes.forEach(mode=>{ const label=document.createElement('label'); label.className='stream-mode-toggle'; const input=document.createElement('input'); input.type='checkbox'; input.value=mode; input.checked=enabled.has(mode); input.disabled=mode==='raw'; input.dataset.streamModeToggle='1'; label.append(input, document.createTextNode(streamModeLabel(mode))); toggles.appendChild(label); }); } const select=$('videoStreamMode'); if(select){ clear(select); modes.filter(mode=>enabled.has(mode)).forEach(mode=>{ const option=document.createElement('option'); option.value=mode; option.textContent=streamModeLabel(mode); select.appendChild(option); }); select.value=enabled.has(selected) ? selected : 'raw'; } }
function collectEnabledStreamModes(){ const modes=['raw']; document.querySelectorAll('[data-stream-mode-toggle]').forEach(input=>{ if(input.checked && !modes.includes(input.value)) modes.push(input.value); }); return modes; }
function renderFullscreenProfiles(data,selected){ const select=$('videoFullscreenProfile'); if(!select) return; const profiles=data.profiles || {}; const labels=data.profile_labels || {}; const minimum=Number(data.fullscreen_min_max_size || 1280); const names=NORMAL_PROFILE_NAMES.concat(Object.keys(profiles).filter(name=>!NORMAL_PROFILE_NAMES.includes(name)).sort()).filter(name=>profiles[name] && Number(profiles[name].max_size)>=minimum); clear(select); names.forEach(name=>{ const option=document.createElement('option'); option.value=name; option.textContent=`${profileLabel(name,labels)} · ${maxSizeQualityLabel(profiles[name].max_size)}`; select.appendChild(option); }); if(!names.length){ const option=document.createElement('option'); option.value=''; option.textContent=adminT('admin.video.fullscreen_profile_unavailable'); option.disabled=true; select.appendChild(option); select.disabled=true; return; } select.disabled=false; select.value=names.includes(selected) ? selected : (names.includes('sharp') ? 'sharp' : names[0]); }
function refreshFullscreenProfilesFromForm(){ const select=$('videoFullscreenProfile'); if(!select) return; try { const profiles={...collectVideoPresets(),...customProfiles}; renderFullscreenProfiles({...state.video,profiles},select.value); } catch (_) {} }
function renderVideo(){
  const data=state.video || {};
  const settings=data.settings || {};
  const defaults=data.defaults || {};
  const labels=data.profile_labels || {};
  Object.keys(customProfiles).forEach(key=>delete customProfiles[key]);
  Object.assign(customProfiles, data.custom_profiles || {});
  if($('videoProfile')){
    clear($('videoProfile'));
    NORMAL_PROFILE_NAMES.concat(Object.keys(customProfiles).sort()).forEach(name=>{
      const option=document.createElement('option');
      option.value=name;
      option.textContent=profileLabel(name, labels);
      $('videoProfile').appendChild(option);
    });
    $('videoProfile').value=settings.video_profile || defaults.profile || 'balanced';
  }
  renderFullscreenProfiles(data, settings.video_fullscreen_profile || data.fullscreen_profile || 'sharp');
  renderVideoStreamModes(data, settings.scrcpy_stream_mode || data.stream_mode || defaults.scrcpy_stream_mode || 'raw');
  if($('videoAutoStop')) $('videoAutoStop').value=settings.auto_stop_minutes || '15';
  renderBandwidthActions(data.bandwidth_recommendations || {});
  const rows=$('videoPresetRows');
  if(rows){
    clear(rows);
    const profiles=data.profiles || {};
    NORMAL_PROFILE_NAMES.forEach(name=>{
      const profile=profiles[name] || {};
      const tr=document.createElement('tr');
      tr.append(td(profileLabel(name, labels)));
      ['video_bit_rate','max_size','max_fps'].forEach(field=>{
        const cell=document.createElement('td');
        cell.appendChild(presetInput(name, field, profile[field]));
        tr.appendChild(cell);
      });
      tr.append(td(profileHint(name)));
      rows.appendChild(tr);
    });
    applyTableLabels(rows);
  }
  renderCustomProfiles();
}
function setDeviceDrawerContext(device=null){
  const id=device ? getDeviceId(device) : '';
  $('deviceDrawerTitle').textContent=adminT(device?'admin.devices.edit':'admin.ui.devices.create');
  $('deviceDrawerContext').textContent=device ? adminT('admin.devices.editing',{name:device.name || id}) : adminT('admin.devices.create_hint');
}
function editDevice(device){
  const id=getDeviceId(device);
  $('deviceId').value=id;
  $('deviceName').value=device.name || id;
  $('deviceAddress').value=device.address || id;
  $('deviceEnabled').value=device.enabled ? 'true' : 'false';
  setDeviceDrawerContext(device);
  openEditorDrawer('device', document.activeElement);
}
function clearDeviceForm(){
  $('deviceId').value='';
  $('deviceName').value='';
  $('deviceAddress').value='';
  $('deviceEnabled').value='true';
  setDeviceDrawerContext();
}
function openNewDeviceDrawer(){
  clearDeviceForm();
  openEditorDrawer('device', $('openDeviceDrawer'));
}
function clearUserForm(){
  editingUsername='';
  $('newUsername').value='';
  $('newUsername').readOnly=false;
  $('newPassword').value='';
  $('newRole').value='user';
  $('userExpiryMode').value='permanent';
  $('userExpiresAt').value='';
  syncUserExpiryFields();
  $('userDrawerTitle').textContent=adminT('admin.users.create_title');
  $('userDrawerContext').textContent=adminT('admin.users.create_context');
}
function openNewUserDrawer(){
  clearUserForm();
  openEditorDrawer('user', $('openUserDrawer'));
}
function editUser(user){
  editingUsername=user.username;
  $('newUsername').value=user.username;
  $('newUsername').readOnly=true;
  $('newPassword').value='';
  $('newRole').value=user.role;
  $('userExpiryMode').value=user.expires_at == null ? 'permanent' : 'scheduled';
  $('userExpiresAt').value=user.expires_at == null ? '' : epochToLocalInput(user.expires_at);
  syncUserExpiryFields();
  $('userDrawerTitle').textContent=adminT('admin.users.edit_title');
  $('userDrawerContext').textContent=adminT('admin.users.edit_context',{username:user.username});
  openEditorDrawer('user', document.activeElement);
}
function accessRoleLabel(role){ return adminT(role==='admin'?'common.roles.admin':'common.roles.user'); }
function epochToLocalInput(value){
  const date=new Date(Number(value)*1000);
  if(!Number.isFinite(date.getTime())) return '';
  const pad=part=>String(part).padStart(2,'0');
  return `${date.getFullYear()}-${pad(date.getMonth()+1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}
function localInputToEpoch(value){
  const timestamp=new Date(String(value || '')).getTime();
  return Number.isFinite(timestamp) ? Math.floor(timestamp/1000) : null;
}
function userExpirationState(user, now=Math.floor(Date.now()/1000)){
  if(user.expires_at == null) return 'permanent';
  const remaining=Number(user.expires_at)-now;
  if(remaining<=0) return 'expired';
  return remaining<=7*24*60*60 ? 'expiring' : 'active';
}
function formatUserExpiryDate(value){
  const date=new Date(Number(value)*1000);
  if(!Number.isFinite(date.getTime())) return adminT('admin.users.invalid_time');
  return new Intl.DateTimeFormat((adminI18n && adminI18n.locale) || 'zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(date);
}
function formatRemainingSeconds(seconds){
  const remaining=Math.max(0,Math.floor(Number(seconds) || 0));
  const days=Math.floor(remaining/86400);
  if(days>=1) return adminT('admin.users.days',{value:days});
  const hours=Math.floor(remaining/3600);
  if(hours>=1) return adminT('admin.users.hours',{value:hours});
  return adminT('admin.users.minutes',{value:Math.max(1,Math.ceil(remaining/60))});
}
function userExpiryCell(user){
  const wrapper=document.createElement('div');
  wrapper.className='user-expiry';
  wrapper.dataset.userExpiry=user.username;
  updateUserExpiryNode(wrapper,user);
  return wrapper;
}
function updateUserExpiryNode(wrapper,user){
  const stateName=userExpirationState(user);
  wrapper.dataset.expirationState=stateName;
  wrapper.replaceChildren();
  const badge=document.createElement('span');
  badge.className=`chip ${stateName==='expired'?'danger':stateName==='expiring'?'warn':'ok'}`;
  badge.textContent=adminT(`admin.time.${stateName}`);
  wrapper.appendChild(badge);
  if(user.expires_at != null){
    const detail=document.createElement('small');
    const remaining=Math.max(0,Number(user.expires_at)-Math.floor(Date.now()/1000));
    detail.textContent=stateName==='expired' ? formatUserExpiryDate(user.expires_at) : adminT('admin.users.expiry_detail',{date:formatUserExpiryDate(user.expires_at),remaining:formatRemainingSeconds(remaining)});
    wrapper.appendChild(detail);
  }
}
function refreshUserExpirationStatuses(){
  if(document.visibilityState==='hidden' || !loadedResources.has('users')) return;
  const now=Math.floor(Date.now()/1000);
  let stateChanged=false;
  state.users.forEach(user=>{
    const next=userExpirationState(user,now);
    if(user.expiration_state && user.expiration_state!==next) stateChanged=true;
    user.expiration_state=next;
    user.is_active=next!=='expired';
    user.remaining_seconds=user.expires_at == null ? null : Math.max(0,Number(user.expires_at)-now);
  });
  if(stateChanged && $('userExpiryFilter').value!=='all'){
    renderUsers();
  } else {
    const users=new Map(state.users.map(user=>[user.username,user]));
    document.querySelectorAll('[data-user-expiry]').forEach(node=>{
      const user=users.get(node.dataset.userExpiry);
      if(user) updateUserExpiryNode(node,user);
    });
  }
  if($('userDrawer').classList.contains('is-open')) updateUserExpiryPreview();
  if(loadedResources.has('overview')) renderOverviewUsers();
}
function updateUserExpiryPreview(){
  const preview=$('userExpiryPreview');
  if($('userExpiryMode').value==='permanent'){
    preview.textContent=adminT('admin.users.permanent_preview');
    preview.dataset.state='permanent';
    return;
  }
  const expiresAt=localInputToEpoch($('userExpiresAt').value);
  if(expiresAt == null){
    preview.textContent=adminT('admin.users.select_expiry');
    preview.dataset.state='empty';
    return;
  }
  const remaining=expiresAt-Math.floor(Date.now()/1000);
  preview.textContent=remaining<=0 ? adminT('admin.users.immediate_expiry',{date:formatUserExpiryDate(expiresAt)}) : adminT('admin.users.valid_until',{date:formatUserExpiryDate(expiresAt),remaining:formatRemainingSeconds(remaining)});
  preview.dataset.state=remaining<=0?'expired':remaining<=7*86400?'expiring':'active';
}
function syncUserExpiryFields(){
  const scheduled=$('userExpiryMode').value==='scheduled';
  $('userExpiresAtField').hidden=!scheduled;
  $('userExpiresAt').disabled=!scheduled;
  $('userExpiryShortcuts').querySelectorAll('button').forEach(button=>{ button.disabled=!scheduled; });
  updateUserExpiryPreview();
}
function extendUserExpiry(days){
  const now=Math.floor(Date.now()/1000);
  const current=localInputToEpoch($('userExpiresAt').value);
  const next=Math.max(now,current || 0)+Number(days)*86400;
  $('userExpiryMode').value='scheduled';
  $('userExpiresAt').value=epochToLocalInput(next);
  syncUserExpiryFields();
}
function appendTableEmpty(rows, columns, message){
  const row=document.createElement('tr');
  row.className='access-empty-row';
  const cell=document.createElement('td');
  cell.className='access-empty-cell';
  cell.colSpan=columns;
  cell.textContent=message;
  row.appendChild(cell);
  rows.appendChild(row);
}
function filteredAccountUsers(){
  const query=String($('userSearch').value || '').trim().toLocaleLowerCase();
  const role=$('userRoleFilter').value || 'all';
  const expiry=$('userExpiryFilter').value || 'all';
  return state.users.filter(user=>(!query || user.username.toLocaleLowerCase().includes(query)) && (role==='all' || user.role===role) && (expiry==='all' || userExpirationState(user)===expiry));
}
function renderUsers(){
  const rows=$('userRows');
  const filtered=filteredAccountUsers();
  const pageCount=Math.max(1,Math.ceil(filtered.length/USER_PAGE_SIZE));
  userAccountPage=Math.max(1,Math.min(userAccountPage,pageCount));
  const pageUsers=filtered.slice((userAccountPage-1)*USER_PAGE_SIZE,userAccountPage*USER_PAGE_SIZE);
  clear(rows);
  pageUsers.forEach(user=>{
    const tr=document.createElement('tr');
    const expiry=document.createElement('td');
    expiry.appendChild(userExpiryCell(user));
    tr.append(td(user.username), td(accessRoleLabel(user.role)), expiry, td(user.created_at));
    const actions=document.createElement('td');
    actions.className='actions';
    actions.append(btn(adminT('admin.devices.edit'),'',()=>editUser(user)));
    if(user.username !== currentUsername) actions.append(btn(adminT('admin.devices.delete'),'danger',()=>deleteUser(user.username)));
    tr.appendChild(actions);
    rows.appendChild(tr);
  });
  if(!pageUsers.length) appendTableEmpty(rows,5,adminT(state.users.length?'admin.users.no_filter_match':'admin.users.no_users'));
  $('userResultCount').textContent=filtered.length===state.users.length ? adminT('admin.users.count',{count:filtered.length}) : adminT('admin.users.count_filtered',{visible:filtered.length,total:state.users.length});
  $('userPageStatus').textContent=adminT('admin.users.page',{page:userAccountPage,total:pageCount});
  $('userPagePrevious').disabled=userAccountPage<=1;
  $('userPageNext').disabled=userAccountPage>=pageCount || !filtered.length;
  applyTableLabels(rows);
}
function fillSelect(sel, values, label){ clear(sel); values.forEach(v=>{ const o=document.createElement('option'); o.value=v.value; o.textContent=label(v); sel.appendChild(o); }); }
function permissionDraftKey(username,deviceId){ return `${username}\u0000${deviceId}`; }
function rebuildPermissionIndex(permissions=state.permissions){
  permissionIndex.clear();
  permissions.forEach(permission=>{
    const username=String(permission && permission.username || '').trim();
    const deviceId=String(permission && permission.device_id || '').trim();
    if(username && deviceId) permissionIndex.set(permissionDraftKey(username,deviceId),permission);
  });
}
function explicitPermissionFor(username,deviceId){
  return permissionIndex.get(permissionDraftKey(username,deviceId)) || null;
}
function basePermissionFor(user,deviceId){
  if(user && user.role==='admin') return {can_view:true,can_control:true};
  const permission=user ? explicitPermissionFor(user.username,deviceId) : null;
  return {can_view:!!(permission && permission.can_view),can_control:!!(permission && permission.can_control)};
}
function effectivePermissionFor(user,deviceId){
  if(!user) return {can_view:false,can_control:false};
  const base=basePermissionFor(user,deviceId);
  if(user.role==='admin') return base;
  const draft=permissionDrafts.get(permissionDraftKey(user.username,deviceId));
  return draft ? {can_view:!!draft.can_view,can_control:!!draft.can_control} : base;
}
function permissionsAreReady(){ return permissionsLoadPhase==='ready' && loadedResources.has('permissions'); }
function permissionChangesForUser(username){
  const deviceIds=new Set(state.devices.map(getDeviceId));
  return [...permissionDrafts.values()].filter(change=>change.username===username && deviceIds.has(change.device_id));
}
function permissionDraftCounts(){
  const counts=new Map();
  const deviceIds=new Set(state.devices.map(getDeviceId));
  permissionDrafts.forEach(change=>{
    if(!deviceIds.has(change.device_id)) return;
    counts.set(change.username,(counts.get(change.username) || 0) + 1);
  });
  return counts;
}
function permissionDraftCount(username,counts=permissionDraftCounts()){ return counts.get(username) || 0; }
function totalPermissionDraftCount(counts=permissionDraftCounts()){ return [...counts.values()].reduce((total,count)=>total+count,0); }
function permissionUserMeta(user,count=permissionDraftCount(user.username)){
  return `${accessRoleLabel(user.role)}${count ? adminT('admin.permissions.draft_suffix',{count}) : ''}`;
}
function syncPermissionDraftIndicators(){
  const counts=permissionDraftCounts();
  const total=totalPermissionDraftCount(counts);
  const users=new Map(state.users.map(user=>[user.username,user]));
  const summary=$('accessPermissionDraftSummary');
  summary.textContent=total ? adminT('admin.permissions.draft_count',{count:total}) : adminT('admin.permissions.by_user');
  $('accessPermissionsTab').classList.toggle('has-drafts',!!total);
  $('permissionUserList').querySelectorAll('.permission-user-choice').forEach(option=>{
    const user=users.get(option.dataset.username);
    if(!user) return;
    const count=permissionDraftCount(user.username,counts);
    option.classList.toggle('has-drafts',!!count);
    option.dataset.draftCount=String(count);
    const meta=option.querySelector('.permission-user-choice__meta');
    if(meta) meta.textContent=permissionUserMeta(user,count);
  });
  const select=$('permUser');
  [...select.options].forEach(option=>{
    const user=users.get(option.value);
    if(user) option.textContent=`${user.username} · ${permissionUserMeta(user,permissionDraftCount(user.username,counts))}`;
  });
}
function updatePermissionDraft(user,deviceId,field,value){
  if(!user || user.role==='admin') return;
  const key=permissionDraftKey(user.username,deviceId);
  const current=effectivePermissionFor(user,deviceId);
  const next={username:user.username,device_id:deviceId,can_view:current.can_view,can_control:current.can_control,[field]:!!value};
  const base=basePermissionFor(user,deviceId);
  if(next.can_view===base.can_view && next.can_control===base.can_control) permissionDrafts.delete(key);
  else permissionDrafts.set(key,next);
}
function reconcilePermissionDrafts(){
  const users=new Map(state.users.map(user=>[user.username,user]));
  const deviceIds=new Set(state.devices.map(getDeviceId));
  permissionDrafts.forEach((change,key)=>{
    const user=users.get(change.username);
    if(!user || user.role==='admin' || !deviceIds.has(change.device_id)){ permissionDrafts.delete(key); return; }
    const base=basePermissionFor(user,change.device_id);
    if(base.can_view===change.can_view && base.can_control===change.can_control) permissionDrafts.delete(key);
  });
}
function permissionUsersMatchingSearch(){
  const query=String($('permissionUserSearch').value || '').trim().toLocaleLowerCase();
  return state.users.filter(user=>!query || user.username.toLocaleLowerCase().includes(query));
}
function selectedPermissionUser(){ return state.users.find(user=>user.username===selectedPermissionUsername) || null; }
function ensureSelectedPermissionUser(visibleUsers){
  if(!state.users.length) return;
  if(!state.users.some(user=>user.username===selectedPermissionUsername)) selectedPermissionUsername=(visibleUsers[0] || state.users[0] || {}).username || '';
  if(visibleUsers.length && !visibleUsers.some(user=>user.username===selectedPermissionUsername)) selectedPermissionUsername=visibleUsers[0].username;
  if(selectedPermissionUsername) localStorage.setItem(PERMISSION_USER_KEY,selectedPermissionUsername);
  else localStorage.removeItem(PERMISSION_USER_KEY);
}
function disabledPermissionOption(message){
  const option=document.createElement('div');
  option.className='permission-user-empty';
  option.setAttribute('role','option');
  option.setAttribute('aria-disabled','true');
  option.tabIndex=-1;
  option.textContent=message;
  return option;
}
function fillPermissionUserSelect(draftCounts=permissionDraftCounts()){
  const select=$('permUser');
  clear(select);
  if(!state.users.length){
    const option=document.createElement('option');
    option.value='';
    option.textContent=adminT('admin.users.no_users');
    option.disabled=true;
    option.selected=true;
    select.appendChild(option);
    select.disabled=true;
    return;
  }
  select.disabled=false;
  state.users.forEach(user=>{
    const option=document.createElement('option');
    option.value=user.username;
    option.textContent=`${user.username} · ${permissionUserMeta(user,permissionDraftCount(user.username,draftCounts))}`;
    select.appendChild(option);
  });
  select.value=selectedPermissionUsername;
}
function renderPermissionUserList(focusUsername=''){
  const list=$('permissionUserList');
  const draftCounts=permissionDraftCounts();
  const active=document.activeElement;
  const previousFocus=list.contains(active) && active.classList.contains('permission-user-choice') ? active.dataset.username : '';
  const visibleUsers=permissionUsersMatchingSearch();
  ensureSelectedPermissionUser(visibleUsers);
  clear(list);
  visibleUsers.forEach(user=>{
    const selected=user.username===selectedPermissionUsername;
    const option=document.createElement('button');
    option.type='button';
    option.className='permission-user-choice';
    option.dataset.username=user.username;
    option.setAttribute('role','option');
    option.setAttribute('aria-selected',String(selected));
    option.tabIndex=selected?0:-1;
    const draftCount=permissionDraftCount(user.username,draftCounts);
    option.classList.toggle('has-drafts',draftCount>0);
    const name=document.createElement('strong');
    name.textContent=user.username;
    const meta=document.createElement('small');
    meta.className='permission-user-choice__meta';
    meta.textContent=permissionUserMeta(user,draftCount);
    option.append(name,meta);
    option.onclick=()=>selectPermissionUser(user.username,true);
    list.appendChild(option);
  });
  if(!visibleUsers.length) list.appendChild(disabledPermissionOption(adminT(state.users.length?'admin.users.no_search_match':'admin.users.no_users')));
  $('permissionUserResultCount').textContent=visibleUsers.length===state.users.length ? adminT('admin.users.count',{count:visibleUsers.length}) : adminT('admin.users.count_filtered',{visible:visibleUsers.length,total:state.users.length});
  fillPermissionUserSelect(draftCounts);
  syncPermissionDraftIndicators();
  const targetUsername=focusUsername || previousFocus;
  if(targetUsername) requestAnimationFrame(()=>{
    const target=[...list.querySelectorAll('.permission-user-choice')].find(option=>option.dataset.username===targetUsername);
    if(target) target.focus({preventScroll:true});
  });
}
function selectPermissionUser(username,focus=false){
  if(!state.users.some(user=>user.username===username)) return;
  selectedPermissionUsername=username;
  localStorage.setItem(PERMISSION_USER_KEY,username);
  renderPermissionUserList(focus?username:'');
  renderPermissionDetail();
}
function permissionDeviceCell(device){
  const cell=document.createElement('td');
  const identity=document.createElement('div');
  identity.className='permission-device-identity';
  const name=document.createElement('strong');
  name.textContent=device.name || getDeviceId(device);
  const meta=document.createElement('small');
  meta.textContent=`${getDeviceId(device)} · ${adminT(device.enabled?'admin.permissions.device_enabled':'admin.permissions.device_disabled')}`;
  identity.append(name,meta);
  cell.appendChild(identity);
  return cell;
}
function permissionCheckboxCell(user,device,field,labelText,checked){
  const cell=document.createElement('td');
  const label=document.createElement('label');
  label.className='permission-toggle';
  const input=document.createElement('input');
  input.type='checkbox';
  input.checked=checked;
  input.disabled=!user || user.role==='admin' || permissionsMutations>0;
  input.dataset.deviceId=getDeviceId(device);
  input.dataset.permissionField=field;
  input.setAttribute('aria-label',`${device.name || getDeviceId(device)}：${labelText}`);
  const text=document.createElement('span');
  text.textContent=labelText;
  input.onchange=()=>{
    updatePermissionDraft(user,getDeviceId(device),field,input.checked);
    syncPermissionDraftIndicators();
    renderPermissionDetail({deviceId:getDeviceId(device),field});
  };
  label.append(input,text);
  cell.appendChild(label);
  return cell;
}
function permissionDevicesFor(user){
  const query=String($('permissionDeviceSearch').value || '').trim().toLocaleLowerCase();
  const filter=$('permissionDeviceFilter').value || 'all';
  return state.devices.filter(device=>{
    const id=getDeviceId(device);
    const name=String(device.name || id);
    const permission=effectivePermissionFor(user,id);
    const queryMatches=!query || `${name}\n${id}`.toLocaleLowerCase().includes(query);
    const filterMatches=filter==='all' || (filter==='view' && permission.can_view) || (filter==='control' && permission.can_control) || (filter==='none' && !permission.can_view && !permission.can_control);
    return queryMatches && filterMatches;
  });
}
function renderPermissionLoadState(){
  const status=$('permissionLoadState');
  const container=status.parentElement;
  const ready=permissionsAreReady();
  const phase=ready?'ready':permissionsLoadPhase;
  container.dataset.state=phase;
  if(phase==='loading') status.textContent=adminT('admin.permissions.loading');
  else if(phase==='error') status.textContent=adminT('admin.permissions.load_failed',{error:permissionsLoadError || adminT('common.feedback.unknown_error')});
  else if(phase==='ready') status.textContent=adminT('admin.permissions.synced');
  else status.textContent=adminT('admin.permissions.waiting');
  status.setAttribute('role',phase==='error'?'alert':'status');
  $('permissionRetry').hidden=phase!=='error';
}
function renderPermissionDetail(focusTarget=null){
  const user=selectedPermissionUser();
  const rows=$('permissionRows');
  const isAdmin=!!user && user.role==='admin';
  const ready=permissionsAreReady();
  const permissionKnown=!!user && (isAdmin || ready);
  const dirtyCount=user ? permissionChangesForUser(user.username).length : 0;
  renderPermissionLoadState();
  $('permissionSelectedUser').textContent=user ? user.username : adminT('admin.permissions.select_user');
  $('permissionUserRole').textContent=user ? accessRoleLabel(user.role) : adminT('admin.permissions.not_selected');
  $('permissionUserRole').className=`chip ${isAdmin?'ok':''}`.trim();
  $('permissionAdminNotice').hidden=!isAdmin;
  $('permissionDirtyCount').textContent=adminT('admin.permissions.draft_count',{count:dirtyCount});
  $('permissionDirtyCount').className=`chip ${dirtyCount?'warn':''}`.trim();
  const visibleDevices=permissionKnown ? permissionDevicesFor(user) : [];
  const totalDevices=state.devices.length;
  const allowedView=permissionKnown ? state.devices.filter(device=>effectivePermissionFor(user,getDeviceId(device)).can_view).length : 0;
  const allowedControl=permissionKnown ? state.devices.filter(device=>effectivePermissionFor(user,getDeviceId(device)).can_control).length : 0;
  $('permissionSelectedUserMeta').textContent=!user ? adminT('admin.permissions.select_user_hint') : isAdmin ? adminT('admin.permissions.admin_coverage',{total:totalDevices}) : !ready ? adminT(permissionsLoadPhase==='error'?'admin.permissions.read_failed':'admin.permissions.reading_user') : adminT('admin.permissions.user_summary',{total:totalDevices,view:allowedView,control:allowedControl});
  $('permissionDeviceResultCount').textContent=permissionKnown ? (visibleDevices.length===totalDevices ? adminT('admin.permissions.device_count',{count:visibleDevices.length}) : adminT('admin.permissions.device_count_filtered',{visible:visibleDevices.length,total:totalDevices})) : adminT('admin.permissions.device_count_unknown');
  clear(rows);
  visibleDevices.forEach(device=>{
    const id=getDeviceId(device);
    const permission=effectivePermissionFor(user,id);
    const row=document.createElement('tr');
    row.dataset.deviceId=id;
    row.classList.toggle('is-dirty',permissionDrafts.has(permissionDraftKey(user.username,id)));
    row.append(permissionDeviceCell(device),permissionCheckboxCell(user,device,'can_view',adminT('admin.permissions.allow_view'),permission.can_view),permissionCheckboxCell(user,device,'can_control',adminT('admin.permissions.allow_control'),permission.can_control));
    rows.appendChild(row);
  });
  if(!visibleDevices.length){
    const emptyMessage=adminT(!user?'admin.permissions.select_user_first':!permissionKnown?(permissionsLoadPhase==='error'?'admin.permissions.cannot_confirm':'admin.permissions.loading'):totalDevices?'admin.permissions.no_device_match':'admin.permissions.no_devices');
    appendTableEmpty(rows,3,emptyMessage);
  }
  applyTableLabels(rows);
  const saveButton=$('savePermission');
  saveButton.disabled=!user || isAdmin || !ready || !dirtyCount || permissionsMutations>0;
  $('permissionSaveStatus').textContent=!user ? adminT('admin.permissions.select_user_first') : isAdmin ? adminT('admin.permissions.admin_no_save') : !ready ? (dirtyCount?adminT('admin.permissions.drafts_blocked',{count:dirtyCount}):adminT('admin.permissions.load_before_edit')) : dirtyCount ? adminT('admin.permissions.unsaved_devices',{count:dirtyCount}) : adminT('admin.permissions.current_synced');
  if(focusTarget) requestAnimationFrame(()=>{
    const selector=`input[data-device-id="${CSS.escape(focusTarget.deviceId)}"][data-permission-field="${focusTarget.field}"]`;
    const target=rows.querySelector(selector);
    if(target) target.focus({preventScroll:true});
    else $('permissionDeviceFilter').focus({preventScroll:true});
  });
}
function renderPermissions(){
  reconcilePermissionDrafts();
  renderPermissionUserList();
  renderPermissionDetail();
}
function alasBindings(){ return (state.alas && Array.isArray(state.alas.bindings) && state.alas.bindings) || []; }
function alasAssignments(){
  const hasDetailedAssignments=!!(state.alas && Array.isArray(state.alas.assignments));
  const source=hasDetailedAssignments ? state.alas.assignments : alasBindings();
  return source.filter(item=>String(item && item.config_name || '').trim()).map(item=>({
    ...item,
    username:String(item.username || '').trim(),
    config_name:String(item.config_name || '').trim(),
    can_run:!!item.can_run,
    can_edit:!!item.can_edit,
    is_default:item.is_default === undefined ? !hasDetailedAssignments : !!item.is_default,
    updated_at:Number(item.updated_at || 0)
  }));
}
function alasCatalog(){ return (state.alas && state.alas.catalog) || {}; }
function configKey(value){
  const name=String(value || '').trim();
  return name.endsWith('.json') ? name.slice(0,-5) : name;
}
function uniqueNames(values){
  const seen=new Set();
  const result=[];
  values.forEach(raw=>{
    const name=String(raw || '').trim();
    const key=configKey(name);
    if(name && !seen.has(key)){ seen.add(key); result.push(name); }
  });
  return result;
}
function uniqueAlasConfigs(){
  const catalog=alasCatalog();
  return uniqueNames([...(catalog.configs || []), ...(catalog.runtime_configs || []), ...((state.alas && state.alas.bound_configs) || []), ...alasAssignments().map(item=>item.config_name)]);
}
function runtimeAlasConfigs(){ return new Set((alasCatalog().runtime_configs || []).map(configKey)); }
function alasUsers(){
  const users=new Map();
  [...state.users, ...alasBindings(), ...alasAssignments()].forEach(item=>{
    const username=String(item && item.username || '').trim();
    if(!username) return;
    const previous=users.get(username) || {};
    users.set(username, {username, role:item.role || previous.role || 'user'});
  });
  return [...users.values()].sort((a,b)=>a.username.localeCompare(b.username, 'zh-CN'));
}
function assignmentsForUser(username){ return alasAssignments().filter(item=>item.username===username); }
function assignmentsForConfig(configName){ const key=configKey(configName); return alasAssignments().filter(item=>configKey(item.config_name)===key); }
function configOwnership(configName){
  const bindings=assignmentsForConfig(configName);
  const owners=uniqueNames(bindings.map(item=>item.username));
  return {bindings, owners, owner:owners.length===1?owners[0]:'', conflict:owners.length>1};
}
function selectedAlasConfig(){ return String(selectedAlasConfigName || uniqueAlasConfigs()[0] || '').trim(); }
function syncAlasConfigSelectors(configName){
  const config=String(configName || '').trim();
  selectedAlasConfigName=config;
  if(config) localStorage.setItem(ALAS_CONFIG_KEY, config);
  else localStorage.removeItem(ALAS_CONFIG_KEY);
  if($('alasOperateConfig')) $('alasOperateConfig').value=config;
  if($('configSource')) $('configSource').value=config;
  if($('configTarget')) $('configTarget').value=config;
  return config;
}
function formatAlasUpdated(value){
  const stamp=Number(value || 0);
  return stamp ? new Date(stamp * 1000).toLocaleString([], {year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit'}) : adminT('admin.users.no_record');
}
function alasRoleLabel(role){ return accessRoleLabel(role); }
function emptyMessage(text){ const p=document.createElement('p'); p.className='alas-empty'; p.textContent=text; return p; }
function emptyListboxOption(text){
  const option=document.createElement('div');
  option.className='alas-empty';
  option.setAttribute('role','option');
  option.setAttribute('aria-disabled','true');
  option.setAttribute('aria-selected','false');
  option.tabIndex=-1;
  option.textContent=text;
  return option;
}
function focusedAlasChoiceKey(list){
  const active=document.activeElement;
  return active && list && list.contains(active) && active.classList.contains('alas-choice') ? String(active.dataset.choiceKey || '') : '';
}
function restoreAlasChoiceFocus(list, key){
  if(!list || !key) return;
  requestAnimationFrame(()=>{
    const choices=[...list.querySelectorAll('.alas-choice')];
    const target=choices.find(choice=>choice.dataset.choiceKey===key) || choices.find(choice=>choice.classList.contains('is-selected')) || choices[0];
    if(target) target.focus({preventScroll:true});
  });
}
function focusAlasUserChoice(username){
  const list=$('alasUserList');
  if(!list || !username) return;
  requestAnimationFrame(()=>{
    const target=[...list.querySelectorAll('.alas-choice')].find(choice=>choice.dataset.choiceKey===username);
    if(target) target.focus({preventScroll:true});
    else if($('openAlasAssignmentDetail') && !$('openAlasAssignmentDetail').disabled) $('openAlasAssignmentDetail').focus({preventScroll:true});
  });
}
function createAlasChoice({title, meta, kind, badges, selected, tabStop=selected, onSelect}){
  const button=document.createElement('button');
  button.type='button';
  button.className='alas-choice';
  button.setAttribute('role','option');
  button.setAttribute('aria-selected',String(selected));
  button.tabIndex=tabStop?0:-1;
  if(selected) button.classList.add('is-selected');
  const main=document.createElement('span');
  main.className='alas-choice__main';
  const type=document.createElement('span');
  type.className='alas-choice__kind';
  type.textContent=kind;
  const strong=document.createElement('strong');
  strong.textContent=title;
  const small=document.createElement('small');
  small.textContent=meta;
  main.append(type,strong,small);
  const side=document.createElement('span');
  side.className='alas-choice__badges';
  (badges || []).forEach(label=>side.appendChild(chip(label)));
  button.append(main,side);
  button.dataset.choiceKey=title;
  button.onclick=()=>onSelect(button);
  button.onkeydown=event=>{
    if(!['ArrowDown','ArrowUp','Home','End'].includes(event.key)) return;
    const list=button.parentElement;
    const choices=list ? [...list.querySelectorAll('.alas-choice')] : [];
    const index=choices.indexOf(button);
    if(index<0 || !choices.length) return;
    event.preventDefault();
    const next=event.key==='Home'?0:event.key==='End'?choices.length-1:event.key==='ArrowDown'?(index+1)%choices.length:(index-1+choices.length)%choices.length;
    choices[next].focus({preventScroll:true});
    choices[next].click();
  };
  return button;
}
function renderAlasSummary(){
  const settings=(state.alas && state.alas.settings) || {};
  const catalog=alasCatalog();
  const configs=uniqueAlasConfigs();
  const assignments=alasAssignments();
  const authorizedUsers=new Set(assignments.map(item=>item.username));
  const assignedConfigs=new Set(assignments.map(item=>configKey(item.config_name)));
  const catalogError=String(catalog.error || (state.alas && state.alas.catalog_error) || '');
  const detailsError=String(state.alas && state.alas.details_error || '');
  let runtimeLabel=adminT('admin.alas_ui.not_configured');
  let runtimeMeta=adminT('admin.alas_ui.configure_runtime');
  if(detailsError){
    runtimeLabel=adminT('admin.alas_ui.data_error');
    runtimeMeta=detailsError;
  } else if(settings.enabled && settings.token_set){
    runtimeLabel=adminT(catalogError?'admin.alas_ui.connection_error':'admin.alas_ui.connected');
    runtimeMeta=catalogError || adminT('admin.alas_ui.runtime_config_count',{count:(catalog.runtime_configs || []).length});
  } else if(settings.enabled){
    runtimeLabel=adminT('admin.alas_ui.token_missing');
    runtimeMeta=adminT('admin.alas_ui.token_missing_detail');
  }
  $('alasRuntimeSummary').textContent=runtimeLabel;
  $('alasRuntimeSummary').className=catalogError || !settings.enabled || !settings.token_set ? 'is-warning' : 'is-ok';
  $('alasRuntimeMeta').textContent=runtimeMeta;
  $('alasConfigCount').textContent=String(configs.length);
  $('alasUserCount').textContent=String(authorizedUsers.size);
  $('alasAssignmentCount').textContent=String(assignedConfigs.size);
}
function renderAlasUserList(){
  const list=$('alasUserList');
  const focusedKey=focusedAlasChoiceKey(list);
  const allUsers=alasUsers();
  const query=String($('alasUserSearch').value || '').trim().toLocaleLowerCase();
  const filter=$('alasUserFilter').value || 'all';
  const visible=allUsers.filter(user=>{
    const count=assignmentsForUser(user.username).length;
    return (!query || user.username.toLocaleLowerCase().includes(query)) && (filter==='all' || (filter==='assigned' ? count>0 : count===0));
  });
  if(!allUsers.some(user=>user.username===selectedAlasUsername)) selectedAlasUsername=(allUsers[0] && allUsers[0].username) || '';
  if(visible.length && !visible.some(user=>user.username===selectedAlasUsername)) selectedAlasUsername=visible[0].username;
  if(selectedAlasUsername) localStorage.setItem(ALAS_USER_KEY, selectedAlasUsername);
  clear(list);
  visible.forEach(user=>{
    const count=assignmentsForUser(user.username).length;
    list.appendChild(createAlasChoice({
      title:user.username,
      meta:alasRoleLabel(user.role),
      kind:adminT('admin.alas_ui.user_account'),
      badges:[adminT('admin.alas_ui.config_count',{count})],
      selected:user.username===selectedAlasUsername,
      onSelect:()=>{ selectedAlasUsername=user.username; localStorage.setItem(ALAS_USER_KEY,user.username); renderAlasUserList(); renderAlasUserDetail(); }
    }));
  });
  if(!visible.length) list.appendChild(emptyListboxOption(adminT(query || filter!=='all' ? 'admin.alas_ui.no_user_match' : 'admin.alas_ui.no_assignable_users')));
  $('alasUserResultCount').textContent=adminT('admin.users.count',{count:visible.length});
  restoreAlasChoiceFocus(list,focusedKey);
}
function renderAlasUserDetail(){
  const user=alasUsers().find(item=>item.username===selectedAlasUsername);
  const rows=$('alasBindRows');
  clear(rows);
  $('openAlasAssignmentDetail').disabled=!user;
  $('openAlasAssignment').disabled=!user;
  if(!user){
    $('alasSelectedUser').textContent=adminT('admin.permissions.select_user');
    $('alasSelectedUserMeta').textContent=adminT('admin.alas_ui.select_user_hint');
    rows.appendChild(emptyMessage(adminT('admin.alas_ui.no_display_users')));
    return;
  }
  const assignments=assignmentsForUser(user.username);
  $('alasSelectedUser').textContent=user.username;
  $('alasSelectedUserMeta').textContent=adminT('admin.alas_ui.user_config_summary',{role:alasRoleLabel(user.role),count:assignments.length});
  assignments.forEach(binding=>{
    const ownership=configOwnership(binding.config_name);
    const otherOwners=ownership.owners.filter(owner=>owner!==binding.username);
    const row=document.createElement('article');
    row.className='alas-assignment-row';
    const main=document.createElement('div');
    main.className='alas-assignment-row__main';
    const kind=document.createElement('span');
    kind.className='alas-assignment-row__kind';
    kind.textContent=adminT('admin.alas_ui.runtime_config');
    const title=document.createElement('h4');
    title.textContent=binding.config_name;
    const badges=document.createElement('div');
    badges.className='chips';
    if(binding.is_default) badges.appendChild(chip(adminT('admin.alas_ui.default'),'ok'));
    badges.appendChild(chip(adminT(binding.can_run?'admin.alas_ui.can_run':'admin.alas_ui.view_only'),binding.can_run?'ok':''));
    badges.appendChild(chip(adminT(binding.can_edit?'admin.alas_ui.can_edit':'admin.alas_ui.cannot_edit'),binding.can_edit?'ok':''));
    main.append(kind,title,badges);
    const meta=document.createElement('div');
    meta.className='alas-assignment-row__meta';
    const ownerText=document.createElement('span');
    ownerText.textContent=otherOwners.length ? adminT('admin.alas_ui.owner_conflict',{owners:otherOwners.join(adminT('admin.alas_ui.list_separator'))}) : adminT('admin.alas_ui.exclusive_owner',{username:binding.username});
    const updated=document.createElement('span');
    updated.textContent=adminT('admin.alas_ui.updated_at',{value:formatAlasUpdated(binding.updated_at)});
    meta.append(ownerText,updated);
    const actions=document.createElement('div');
    actions.className='actions alas-assignment-row__actions';
    actions.append(btn(adminT('admin.devices.edit'),'',()=>openAlasAssignmentDrawer(binding,document.activeElement)), btn(adminT('admin.alas_ui.remove'),'danger',()=>removeAlasBinding(binding)));
    row.append(main,meta,actions);
    rows.appendChild(row);
  });
  if(!assignments.length) rows.appendChild(emptyMessage(adminT('admin.alas_ui.user_no_configs')));
}
function renderAlasConfigList(){
  const list=$('alasConfigLibrary');
  const focusedKey=focusedAlasChoiceKey(list);
  const configs=uniqueAlasConfigs();
  const runtime=runtimeAlasConfigs();
  const query=String($('alasConfigSearch').value || '').trim().toLocaleLowerCase();
  const visible=configs.filter(name=>!query || name.toLocaleLowerCase().includes(query));
  const statusConfig=String(state.alas && state.alas.status && state.alas.status.config || '').trim();
  if(!configs.some(name=>configKey(name)===configKey(selectedAlasConfigName))){
    selectedAlasConfigName=configs.find(name=>configKey(name)===configKey(statusConfig)) || configs[0] || '';
  }
  syncAlasConfigSelectors(selectedAlasConfigName);
  clear(list);
  const selectedVisible=visible.some(name=>configKey(name)===configKey(selectedAlasConfigName));
  visible.forEach((name,index)=>{
    const ownership=configOwnership(name);
    list.appendChild(createAlasChoice({
      title:name,
      meta:adminT(runtime.has(configKey(name))?'admin.alas_ui.in_runtime':'admin.alas_ui.ownership_only'),
      kind:adminT('admin.alas_ui.runtime_config'),
      badges:[adminT(ownership.conflict?'admin.alas_ui.ownership_conflict':ownership.owner?'admin.alas_ui.owner':'admin.alas_ui.unassigned',{owner:ownership.owner})],
      selected:configKey(name)===configKey(selectedAlasConfigName),
      tabStop:configKey(name)===configKey(selectedAlasConfigName) || (!selectedVisible && index===0),
      onSelect:()=>selectAlasConfig(name)
    }));
  });
  if(!visible.length) list.appendChild(emptyListboxOption(adminT(query ? 'admin.alas_ui.no_config_match' : 'admin.alas_ui.no_configs')));
  $('alasConfigResultCount').textContent=adminT('admin.alas_ui.config_count',{count:visible.length});
  const catalog=alasCatalog();
  const error=String(catalog.error || (state.alas && state.alas.catalog_error) || '');
  $('alasCatalogState').textContent=error ? adminT('admin.alas_ui.catalog_failed',{error}) : adminT('admin.alas_ui.catalog_summary',{runtime:(catalog.runtime_configs || []).length,owned:((state.alas && state.alas.bound_configs) || []).length});
  $('alasCatalogState').dataset.state=error?'error':'ready';
  restoreAlasChoiceFocus(list,focusedKey);
}
function renderAlasConfigDetail(){
  const config=selectedAlasConfig();
  const settings=(state.alas && state.alas.settings) || {};
  const status=(state.alas && state.alas.status) || {};
  const statusMatches=config && configKey(status.config)===configKey(config);
  const ownership=config?configOwnership(config):{bindings:[],owners:[],owner:'',conflict:false};
  const runtime=runtimeAlasConfigs();
  const loadingCurrent=alasStatusLoading && configKey(alasStatusLoadingConfig)===configKey(config);
  $('alasCurrentConfigName').textContent=config || adminT('admin.alas_ui.select_config');
  $('alasCurrentConfigMeta').textContent=config ? `${adminT(runtime.has(configKey(config))?'admin.alas_ui.in_runtime':'admin.alas_ui.ownership_only')} · ${adminT(ownership.conflict?'admin.alas_ui.ownership_conflict':ownership.owner?'admin.alas_ui.owner':'admin.alas_ui.unassigned',{owner:ownership.owner})}` : adminT('admin.alas_ui.select_config_status_hint');
  const statusBox=$('alasStatus');
  clear(statusBox);
  if(!config) statusBox.appendChild(chip(adminT('admin.alas_ui.no_config'),'warn'));
  else if(loadingCurrent) statusBox.appendChild(chip(adminT('admin.alas_ui.reading'),'warn'));
  else if(!statusMatches) statusBox.appendChild(chip(adminT('admin.alas_ui.status_not_read')));
  else {
    statusBox.appendChild(chip(statusLabel(status.status),status.status==='running'?'ok':status.status==='error'?'danger':''));
    if(status.task) statusBox.appendChild(chip(String(status.task)));
    if(status.error) statusBox.appendChild(chip(String(status.error),'danger'));
  }
  if(config && ownership.conflict) $('alasImpactNote').textContent=adminT('admin.alas_ui.impact_conflict',{config,owners:ownership.owners.join(adminT('admin.alas_ui.list_separator'))});
  else if(config && ownership.owner) $('alasImpactNote').textContent=adminT('admin.alas_ui.impact_owner',{config,owner:ownership.owner});
  else if(config) $('alasImpactNote').textContent=adminT('admin.alas_ui.impact_unassigned',{config});
  else $('alasImpactNote').textContent=adminT('admin.alas_ui.impact_current');
  $('toggleAlas').disabled=!config || !settings.enabled || !settings.token_set || loadingCurrent;
  $('toggleAlas').textContent=adminT(statusMatches && status.status==='running'?'admin.alas_ui.stop':statusMatches && status.status==='error'?'admin.alas_ui.restart':'admin.alas_ui.start');
  $('openConfigEditor').disabled=!config;
  $('alasOpenCurrent').href=config?`/alas/embed/?config=${encodeURIComponent(config)}`:'/alas/embed/';
  $('alasOpenCurrent').setAttribute('aria-disabled',String(!config));
  $('alasOpenCurrent').tabIndex=config?0:-1;
  const currentUsers=$('alasCurrentUsers');
  clear(currentUsers);
  if(ownership.owner){
    const binding=ownership.bindings.find(item=>item.username===ownership.owner) || {};
    const pill=document.createElement('span');
    pill.className='alas-user-pill';
    const label=document.createElement('strong');
    label.textContent=binding.username;
    const permissions=document.createElement('small');
      permissions.textContent=[binding.is_default?adminT('admin.alas_ui.default'):null,adminT(binding.can_run?'admin.alas_ui.can_run':'admin.alas_ui.view_only'),binding.can_edit?adminT('admin.alas_ui.can_edit'):null].filter(Boolean).join(' · ');
    pill.append(label,permissions);
    currentUsers.appendChild(pill);
  } else if(ownership.conflict) currentUsers.appendChild(emptyMessage(adminT('admin.alas_ui.historical_conflict',{owners:ownership.owners.join(adminT('admin.alas_ui.list_separator'))})));
  else currentUsers.appendChild(emptyMessage(adminT('admin.alas_ui.config_unassigned')));
  $('alasCurrentUserCount').textContent=adminT(ownership.conflict?'admin.alas_ui.needs_attention':ownership.owner?'admin.alas_ui.assigned':'admin.alas_ui.unassigned');
}
function alasAssignmentUsername(){
  return editingAlasAssignment ? editingAlasAssignment.username : String($('alasBindUser').value || '').trim();
}
function alasAssignmentManualSelected(){
  const selected=$('alasBindConfig').selectedOptions[0];
  return !!selected && selected.dataset.manual==='true';
}
function alasAssignmentConfigName(){
  if(editingAlasAssignment) return configKey(editingAlasAssignment.config_name);
  return configKey(alasAssignmentManualSelected() ? $('alasBindConfigCustom').value : $('alasBindConfig').value);
}
function syncAlasAssignmentConfigMode(focusCustom=false){
  const select=$('alasBindConfig');
  const customField=$('alasBindConfigCustomField');
  const customInput=$('alasBindConfigCustom');
  const manual=!editingAlasAssignment && alasAssignmentManualSelected();
  customField.hidden=!manual;
  customInput.disabled=!manual;
  customInput.required=manual;
  if(manual && focusCustom) requestAnimationFrame(()=>customInput.focus({preventScroll:true}));
}
function renderAlasAssignmentConfigOptions(preferredValue=null){
  const select=$('alasBindConfig');
  const previous=preferredValue===null ? String(select.value || '') : String(preferredValue || '');
  const previousManual=preferredValue===null && alasAssignmentManualSelected();
  const username=alasAssignmentUsername();
  const unassigned=[];
  const ownedByCurrent=[];
  const occupied=[];
  uniqueAlasConfigs().forEach(name=>{
    const ownership=configOwnership(name);
    const otherOwners=ownership.owners.filter(owner=>owner!==username);
    const entry={name,ownership,otherOwners};
    if(ownership.conflict || otherOwners.length) occupied.push(entry);
    else if(ownership.owner===username) ownedByCurrent.push(entry);
    else unassigned.push(entry);
  });
  clear(select);
  const placeholder=document.createElement('option');
  placeholder.value='';
  placeholder.textContent=adminT('admin.alas_ui.select_runtime_config');
  placeholder.disabled=true;
  select.appendChild(placeholder);
  if(unassigned.length){
    const group=document.createElement('optgroup');
    group.label=adminT('admin.alas_ui.unassigned_configs');
    unassigned.forEach(({name})=>{
      const option=document.createElement('option');
      option.value=name;
      option.textContent=adminT('admin.alas_ui.config_unassigned_option',{name});
      group.appendChild(option);
    });
    select.appendChild(group);
  }
  if(ownedByCurrent.length){
    const group=document.createElement('optgroup');
    group.label=adminT('admin.alas_ui.current_user_owned');
    ownedByCurrent.forEach(({name})=>{
      const option=document.createElement('option');
      option.value=name;
      option.textContent=adminT('admin.alas_ui.config_owned_current',{name});
      group.appendChild(option);
    });
    select.appendChild(group);
  }
  if(occupied.length){
    const group=document.createElement('optgroup');
    group.label=adminT('admin.alas_ui.owned_by_others');
    occupied.forEach(({name,ownership,otherOwners})=>{
      const option=document.createElement('option');
      option.value=name;
      option.textContent=ownership.conflict ? adminT('admin.alas_ui.config_conflict_option',{name,owners:ownership.owners.join(adminT('admin.alas_ui.list_separator'))}) : adminT('admin.alas_ui.config_owned_option',{name,owners:otherOwners.join(adminT('admin.alas_ui.list_separator'))});
      option.disabled=true;
      group.appendChild(option);
    });
    select.appendChild(group);
  }
  const manual=document.createElement('option');
  manual.value='';
  manual.dataset.manual='true';
  manual.textContent=adminT('admin.alas_ui.manual_config');
  select.appendChild(manual);
  select.disabled=!!editingAlasAssignment;
  if(editingAlasAssignment){
    const current=[...select.options].find(option=>configKey(option.value)===configKey(editingAlasAssignment.config_name));
    if(current) select.value=current.value;
  } else if(previousManual){
    manual.selected=true;
  } else {
    const current=[...select.options].find(option=>!option.disabled && configKey(option.value)===configKey(previous));
    select.value=current ? current.value : '';
  }
  syncAlasAssignmentConfigMode(false);
}
function renderAlasCompatibilityFields(){
  const configs=uniqueAlasConfigs();
  const select=$('alasOperateConfig');
  clear(select);
  configs.forEach(name=>{ const option=document.createElement('option'); option.value=name; option.textContent=name; select.appendChild(option); });
  select.value=selectedAlasConfig();
  const userSelect=$('alasBindUser');
  const previous=userSelect.value || selectedAlasUsername;
  fillSelect(userSelect,alasUsers().map(user=>({value:user.username,text:`${user.username} · ${alasRoleLabel(user.role)}`})),item=>item.text);
  if([...userSelect.options].some(option=>option.value===previous)) userSelect.value=previous;
  renderAlasAssignmentConfigOptions();
  updateAlasAssignmentOwnerHint();
  syncAlasAssignmentSummary();
}
async function removeAlasBinding(binding){
  const confirmed=await confirmDanger({
    title:adminT('admin.alas_ui.remove_ownership'),
    message:adminT('admin.alas_ui.remove_ownership_message',{config:binding.config_name,username:binding.username}),
    confirmText:adminT('admin.alas_ui.remove_ownership_confirm')
  });
  if(!confirmed) return;
  await mutateAlasPermissions({username:binding.username, config_name:binding.config_name, enabled:false, is_default:false});
  focusAlasUserChoice(binding.username);
  show(adminT('admin.alas_ui.ownership_removed'));
  await refreshDomains('overview','overviewAlas');
}
function renderAlas(){
  const alas=state.alas || {};
  const settings=alas.settings || {};
  if(!$('alasConnectionDrawer').classList.contains('is-open')){
    $('alasBaseUrl').value=settings.base_url || '';
    $('alasToken').value='';
  }
  renderAlasCompatibilityFields();
  renderAlasSummary();
  renderAlasUserList();
  renderAlasUserDetail();
  renderAlasConfigList();
  renderAlasConfigDetail();
}
function activateAlasView(view, focus=false){
  const next=view==='configs'?'configs':'users';
  activeAlasView=next;
  const usersSelected=next==='users';
  $('alasUsersTab').classList.toggle('active',usersSelected);
  $('alasUsersTab').setAttribute('aria-selected',String(usersSelected));
  $('alasUsersTab').tabIndex=usersSelected?0:-1;
  $('alasConfigsTab').classList.toggle('active',!usersSelected);
  $('alasConfigsTab').setAttribute('aria-selected',String(!usersSelected));
  $('alasConfigsTab').tabIndex=usersSelected?-1:0;
  $('alasUsersView').hidden=!usersSelected;
  $('alasConfigsView').hidden=usersSelected;
  if(focus) (usersSelected?$('alasUsersTab'):$('alasConfigsTab')).focus({preventScroll:true});
  if(!usersSelected){
    renderAlasConfigList();
    renderAlasConfigDetail();
    loadIfNeeded('alasCatalog',loadAlasCatalog).catch(error=>reportRequestError(error,adminT('admin.alas_ui.library_load_failed'))).finally(()=>{
      const statusConfig=String(state.alas && state.alas.status && state.alas.status.config || '');
      if(selectedAlasConfig() && configKey(statusConfig)!==configKey(selectedAlasConfig())) loadAlasStatusForConfig(selectedAlasConfig());
    });
  }
}
function selectAlasConfig(configName){
  const config=String(configName || '').trim();
  if(!config) return;
  const changed=configKey(config)!==configKey(selectedAlasConfigName);
  syncAlasConfigSelectors(config);
  renderAlasConfigList();
  renderAlasConfigDetail();
  const statusConfig=String(state.alas && state.alas.status && state.alas.status.config || '');
  if(changed || configKey(statusConfig)!==configKey(config)) loadAlasStatusForConfig(config);
}
async function loadAlasStatusForConfig(configName){
  const config=String(configName || '').trim();
  if(!config) return;
  const sequence=++alasStatusSequence;
  alasStatusLoading=true;
  alasStatusLoadingConfig=config;
  renderAlasConfigDetail();
  try{
    await requestResource('alasStatus',signal=>api(`/api/admin/alas?config=${encodeURIComponent(config)}`,{signal}),data=>{
      const catalog=alasCatalog();
      state.alas={...(state.alas || {}),...data,catalog};
      renderAlasSummary();
    },{force:true});
  } catch(error){
    if(!isAbortError(error)) show(adminT('admin.alas_ui.status_read_failed',{error:error.message}));
  } finally {
    if(sequence===alasStatusSequence && configKey(config)===configKey(selectedAlasConfig()) && configKey(alasStatusLoadingConfig)===configKey(config)){
      alasStatusLoading=false;
      alasStatusLoadingConfig='';
      renderAlasConfigDetail();
    }
  }
}
function openAlasConnectionDrawer(trigger=document.activeElement){
  const settings=(state.alas && state.alas.settings) || {};
  $('alasBaseUrl').value=settings.base_url || '';
  $('alasToken').value='';
  $('alasConnectionDrawerContext').textContent=settings.enabled ? adminT('admin.alas_ui.current_runtime',{url:settings.base_url || adminT('admin.alas_ui.address_missing')}) : adminT('admin.ui.drawers.alas_connection.context');
  openEditorDrawer('alasConnection',trigger);
}
function openAlasAssignmentDrawer(binding=null,trigger=document.activeElement){
  const users=alasUsers();
  if(!users.length) return show(adminT('admin.alas_ui.create_user_first'));
  editingAlasAssignment=binding ? {username:binding.username,config_name:binding.config_name} : null;
  $('alasBindConfigCustom').value='';
  renderAlasCompatibilityFields();
  const username=binding && binding.username || selectedAlasUsername || users[0].username;
  $('alasBindUser').value=username;
  $('alasBindUser').disabled=!!binding;
  renderAlasAssignmentConfigOptions(binding && binding.config_name || '');
  $('alasBindRun').value=binding && binding.can_run ? 'true' : 'false';
  $('alasBindEdit').value=binding && binding.can_edit ? 'true' : 'false';
  $('alasBindDefault').value='keep';
  $('alasBindEnabled').value='true';
  $('alasAssignmentDrawerTitle').textContent=adminT(binding?'admin.alas_ui.edit_ownership':'admin.alas_ui.assign_ownership');
  $('alasAssignmentDrawerContext').textContent=binding ? adminT('admin.alas_ui.edit_ownership_context',{username:binding.username,config:binding.config_name}) : adminT('admin.alas_ui.assign_ownership_context',{username});
  updateAlasAssignmentOwnerHint();
  syncAlasAssignmentSummary();
  openEditorDrawer('alasAssignment',trigger);
  loadIfNeeded('alasCatalog',loadAlasCatalog).catch(error=>reportRequestError(error,adminT('admin.alas_ui.suggestions_load_failed')));
}
function syncAlasAssignmentSummary(){
  const username=alasAssignmentUsername();
  const configName=alasAssignmentConfigName();
  const user=alasUsers().find(item=>item.username===username);
  $('alasAssignmentUserName').textContent=username || adminT('admin.alas_ui.pending_user');
  $('alasAssignmentUserMeta').textContent=user ? adminT('admin.alas_ui.assignment_user_meta',{role:alasRoleLabel(user.role),count:assignmentsForUser(username).length}) : adminT('admin.alas_ui.select_recipient');
  $('alasAssignmentConfigName').textContent=configName || adminT('admin.alas_ui.pending_config');
  const summary=$('alasAssignmentConfigSummary');
  let stateName='idle';
  let meta=adminT('admin.alas_ui.enter_or_select_config');
  if(configName){
    const ownership=configOwnership(configName);
    const otherOwners=ownership.owners.filter(owner=>owner!==username);
    if(otherOwners.length){
      stateName='error';
      meta=adminT('admin.alas_ui.owned_by',{owners:otherOwners.join(adminT('admin.alas_ui.list_separator'))});
    } else if(ownership.owner===username){
      stateName='ready';
      meta=adminT('admin.alas_ui.update_current_permissions');
    } else {
      stateName='available';
      meta=adminT('admin.alas_ui.available_for_assignment');
    }
  }
  summary.dataset.state=stateName;
  $('alasAssignmentConfigMeta').textContent=meta;
}
function updateAlasAssignmentOwnerHint(){
  const hint=$('alasAssignmentOwnerHint');
  if(!hint) return;
  const username=alasAssignmentUsername();
  const configName=alasAssignmentConfigName();
  let blocked=false;
  let stateName='ready';
  if(!configName){
    hint.textContent=adminT('admin.alas_ui.exclusive_hint');
    stateName='idle';
  } else {
    const ownership=configOwnership(configName);
    const otherOwners=ownership.owners.filter(owner=>owner!==username);
    blocked=otherOwners.length>0;
    if(blocked){
      hint.textContent=adminT('admin.alas_ui.assignment_blocked',{config:configName,owners:otherOwners.join(adminT('admin.alas_ui.list_separator')),username:username || adminT('admin.alas_ui.current_user')});
      stateName='error';
    } else if(ownership.owner===username){
      hint.textContent=adminT('admin.alas_ui.assignment_update',{config:configName});
    } else {
      hint.textContent=adminT('admin.alas_ui.assignment_available',{config:configName,username:username || adminT('admin.alas_ui.selected_user')});
    }
  }
  hint.dataset.state=stateName;
  const saveButton=$('saveAlasBinding');
  if(saveButton && !saveButton.classList.contains('busy')) saveButton.disabled=blocked || !username || !configName;
}
function requireAlasSuccess(result, fallback=adminT('mirror.errors.alas_operation')){
  if(result && result.ok===false) throw new Error(result.error || result.detail || fallback);
  return result || {};
}
function invalidateAlasConfigRead(clearTarget=false){
  alasConfigReadSequence+=1;
  if(alasConfigReadController) alasConfigReadController.abort();
  alasConfigReadController=null;
  if(clearTarget) alasConfigDrawerTarget='';
}
function beginAlasConfigRead(target){
  invalidateAlasConfigRead(false);
  const controller=new AbortController();
  alasConfigReadController=controller;
  return {target, sequence:alasConfigReadSequence, controller};
}
function alasConfigReadIsCurrent(request){
  return !!request
    && !request.controller.signal.aborted
    && request.sequence===alasConfigReadSequence
    && configKey(request.target)===configKey(alasConfigDrawerTarget)
    && $('alasConfigDrawer').classList.contains('is-open');
}
function openAlasConfigDrawer(trigger=document.activeElement){
  const config=selectedAlasConfig();
  if(!config) return show(adminT('admin.alas_ui.select_from_library'));
  const ownership=configOwnership(config);
  invalidateAlasConfigRead(false);
  alasConfigDrawerTarget=config;
  $('configSource').value=config;
  $('configTarget').value=config;
  $('alasConfigDrawerContext').textContent=adminT('admin.alas_ui.editing_config',{config});
  $('alasConfigDrawerImpact').textContent=ownership.conflict ? adminT('admin.alas_ui.save_conflict_impact',{config,owners:ownership.owners.join(adminT('admin.alas_ui.list_separator'))}) : ownership.owner ? adminT('admin.alas_ui.save_owner_impact',{config,owner:ownership.owner}) : adminT('admin.alas_ui.save_unassigned_impact',{config});
  $('configEditor').value='';
  openEditorDrawer('alasConfig',trigger);
  withBusy($('loadConfig'),loadConfig,adminT('admin.busy.reading')).catch(error=>{ if(!isAbortError(error)) show(error.message); });
}
function renderRuntimeLogs(){
  $('runtimeLogs').textContent=(state.runtimeLogs || []).join('\n');
}
function renderAuditLogs(){
  const rows=$('logRows');
  clear(rows);
  [...state.logs].reverse().forEach(log=>{
    const tr=document.createElement('tr');
    tr.append(td(ts(log.ts)), td(log.username), td(actionText(log.action)), td(log.detail));
    rows.appendChild(tr);
  });
  applyTableLabels(rows);
}
function renderLogs(){ renderRuntimeLogs(); renderAuditLogs(); }
function render(){ renderOverview(); renderDevices(); renderVideo(); renderUsers(); renderPermissions(); renderAlas(); renderLogs(); }
function applyOverview(data){
  const next=data || {};
  const previous=state.overview || {};
  const keepDevices=loadedResources.has('overviewDevices') && Array.isArray(previous.devices);
  const keepAlas=loadedResources.has('overviewAlas') && previous.alas;
  state.overview={
    ...previous,
    ...next,
    devices:keepDevices ? previous.devices : next.devices || [],
    sessions:keepDevices ? previous.sessions || {} : next.sessions || {},
    alas:keepAlas ? previous.alas : next.alas || {}
  };
  renderOverview();
}
function applyOverviewAlas(data){ state.overview={...(state.overview || {}),alas:data || {}}; renderOverviewHeader(); renderOverviewAlas(); }
function applyOverviewDevices(data){ state.overview={...(state.overview || {}),devices:data.devices || [],sessions:data.sessions || {}}; renderOverviewHeader(); renderOverviewDevices(); renderOverviewMirrors(); }
function applyDevices(data){ state.devices=data.devices || []; renderDevices(); if(loadedResources.has('permissions')) renderPermissions(); }
function applyUsers(data){ state.users=data.users || []; renderUsers(); if(loadedResources.has('permissions')) renderPermissions(); if(loadedResources.has('alas')) renderAlas(); }
function applyPermissions(data,epoch=permissionsEpoch){
  if(epoch!==permissionsEpoch) return false;
  state.permissions=Array.isArray(data.permissions) ? data.permissions : [];
  rebuildPermissionIndex(state.permissions);
  if(data.users && !loadedResources.has('users')) state.users=data.users;
  if(data.devices && !loadedResources.has('devices')) state.devices=data.devices;
  renderPermissions();
  if(loadedResources.has('users')) renderUsers();
  if(loadedResources.has('devices')) renderDevices();
  return true;
}
function applyVideo(data){ state.video=data; renderVideo(); }
function applyAlas(data){
  const previous=state.alas || {};
  const catalog=previous.catalog;
  state.alas={...previous,...(data || {})};
  if(catalog && !state.alas.catalog) state.alas.catalog=catalog;
  if(!resourceRequests.has('alasStatus')){
    alasStatusLoading=false;
    alasStatusLoadingConfig='';
  }
  renderAlas();
}
function applyAlasDetails(data, statusSequence){
  const payload={...(data || {})};
  if(statusSequence!==alasStatusSequence){
    if(state.alas && Object.prototype.hasOwnProperty.call(state.alas,'status')) payload.status=state.alas.status;
    else delete payload.status;
  }
  applyAlas(payload);
}
function applyAlasPermissions(data, epoch=alasPermissionsEpoch){
  if(epoch!==alasPermissionsEpoch) return false;
  const previous=state.alas || {};
  state.alas={
    ...previous,
    bindings:Array.isArray(data && data.bindings) ? data.bindings : (previous.bindings || []),
    assignments:Array.isArray(data && data.assignments) ? data.assignments : (previous.assignments || [])
  };
  if(data && Array.isArray(data.users)) state.users=data.users;
  loadedResources.add('alas');
  renderAlas();
  return true;
}
function applyAlasCatalog(data){
  state.alas={...(state.alas || {}),catalog:{...(data || {}),loaded:true},catalog_error:''};
  renderAlas();
}
function applyLogs(data){
  state.logs=data.logs || [];
  renderAuditLogs();
  setLogLoadState('logs','ready',logReadyMessage('admin.logs.audit', state.logs.length));
}
function applyRuntimeLogs(data){
  state.runtimeLogs=data.logs || [];
  renderRuntimeLogs();
  setLogLoadState('runtimeLogs','ready',logReadyMessage('admin.logs.runtime', state.runtimeLogs.length));
}
function overviewRequestPromises(){ return ['overview','overviewAlas'].map(name=>resourceRequests.get(name)).filter(Boolean).map(record=>record.promise); }
function trackOverviewRequest(promise){
  const generation=overviewRefreshGeneration;
  void promise.finally(()=>{
    if(generation===overviewRefreshGeneration && overviewPollingAllowed() && !overviewRequestPromises().length) scheduleOverviewRefresh();
  }).catch(()=>{});
  return promise;
}
function loadOverview(options={}){ return trackOverviewRequest(requestResource('overview', signal=>api('/api/admin/overview',{signal}), applyOverview, options)); }
function loadOverviewAlas(options={}){ return trackOverviewRequest(requestResource('overviewAlas', signal=>api('/api/admin/overview/alas',{signal}), applyOverviewAlas, options)); }
function loadOverviewDevices(options={}){ return requestResource('overviewDevices', signal=>api('/api/admin/devices',{signal}), applyOverviewDevices, options); }
function loadDevices(options={}){ return requestResource('devices', signal=>api('/api/admin/devices',{signal}), applyDevices, options); }
function loadDeviceStatuses(options={}){ return requestResource('deviceStatuses', signal=>api('/api/admin/adb/status',{signal}), applyDeviceStatuses, options); }
function deviceStatusPollingAllowed(){ return (activeTab==='devices' || activeTab==='overview') && !document.hidden; }
function stopDeviceStatusPolling(abort=false){
  clearTimeout(deviceStatusPollTimer);
  deviceStatusPollTimer=null;
  if(abort){
    deviceStatusPollGeneration+=1;
    markResourceStale('deviceStatuses');
    markResourceStale('overviewDevices');
  }
}
function scheduleDeviceStatusPoll(){
  stopDeviceStatusPolling(false);
  if(!deviceStatusPollingAllowed()) return;
  deviceStatusPollTimer=setTimeout(refreshDeviceStatuses,DEVICE_STATUS_POLL_INTERVAL);
}
async function refreshDeviceStatuses(){
  if(!deviceStatusPollingAllowed()) return stopDeviceStatusPolling(true);
  const generation=deviceStatusPollGeneration;
  try{
    if(activeTab==='overview') await loadOverviewDevices({force:true});
    else await loadDeviceStatuses({force:true});
  }
  catch(error){ if(!isAbortError(error)) console.warn(adminT('admin.errors.device_heartbeat_refresh'),error); }
  finally{ if(generation===deviceStatusPollGeneration) scheduleDeviceStatusPoll(); }
}
function syncDeviceStatusPolling(){
  stopDeviceStatusPolling(true);
  if(!deviceStatusPollingAllowed()) return;
  if(activeTab==='devices') refreshDeviceStatuses();
  else scheduleDeviceStatusPoll();
}
function overviewPollingAllowed(){ return activeTab==='overview' && !document.hidden; }
function stopOverviewPolling(abort=false){
  clearTimeout(overviewRefreshTimer);
  overviewRefreshTimer=null;
  if(abort){
    overviewRefreshGeneration+=1;
    markResourceStale('overview');
    markResourceStale('overviewAlas');
  }
}
function scheduleOverviewRefresh(){
  stopOverviewPolling(false);
  if(!overviewPollingAllowed()) return;
  overviewRefreshTimer=setTimeout(refreshOverviewStatus,OVERVIEW_REFRESH_INTERVAL);
}
async function refreshOverviewStatus(){
  if(!overviewPollingAllowed()) return stopOverviewPolling(true);
  const generation=overviewRefreshGeneration;
  try{
    const pending=overviewRequestPromises();
    const results=await Promise.allSettled(pending.length ? pending : [loadOverview({force:true}),loadOverviewAlas({force:true})]);
    results.forEach(result=>{ if(result.status==='rejected'&&!isAbortError(result.reason)) console.warn(adminT('admin.errors.overview_refresh'),result.reason); });
  }
  finally{ if(generation===overviewRefreshGeneration) scheduleOverviewRefresh(); }
}
function syncOverviewPolling(){
  if(!overviewPollingAllowed()) return stopOverviewPolling(true);
  stopOverviewPolling(false);
  const pending=overviewRequestPromises();
  if(pending.length){
    const generation=overviewRefreshGeneration;
    Promise.allSettled(pending).then(()=>{
      if(generation===overviewRefreshGeneration && overviewPollingAllowed()) scheduleOverviewRefresh();
    });
    return;
  }
  refreshOverviewStatus();
}
function loadUsers(options={}){ return requestResource('users', signal=>api('/api/admin/users',{signal}), applyUsers, options); }
function loadPermissions(options={}){
  const epoch=permissionsEpoch;
  permissionsLoadPhase='loading';
  permissionsLoadError='';
  renderPermissionDetail();
  const request=requestResource('permissions',signal=>api('/api/admin/permissions',{signal}),data=>{
    if(permissionsMutations || epoch!==permissionsEpoch) return;
    applyPermissions(data,epoch);
  },options);
  return request.then(data=>{
    if(data===undefined || permissionsMutations || epoch!==permissionsEpoch) return data;
    permissionsLoadPhase='ready';
    permissionsLoadError='';
    renderPermissions();
    return data;
  }).catch(error=>{
    if(!isAbortError(error) && !permissionsMutations && epoch===permissionsEpoch){
      permissionsLoadPhase='error';
      permissionsLoadError=String(error && error.message || adminT('common.feedback.unknown_error'));
      renderPermissions();
    }
    throw error;
  });
}
function beginPermissionsMutation(){
  permissionsMutations+=1;
  permissionsEpoch+=1;
  permissionsNeedsRefresh=true;
  permissionsLoadPhase='loading';
  permissionsLoadError='';
  markResourceStale('permissions');
  renderPermissionDetail();
  return permissionsEpoch;
}
async function finishPermissionsMutation(epoch){
  permissionsMutations=Math.max(0,permissionsMutations-1);
  if(epoch!==permissionsEpoch) permissionsNeedsRefresh=true;
  if(permissionsMutations || !permissionsNeedsRefresh){ renderPermissionDetail(); return; }
  permissionsNeedsRefresh=false;
  markResourceStale('permissions');
  try{
    await loadPermissions({force:true});
  } catch(error){
    if(isAbortError(error) && permissionsMutations){ permissionsNeedsRefresh=true; return; }
    throw error;
  } finally {
    renderPermissionDetail();
  }
}
function loadVideo(options={}){ return requestResource('video', signal=>api('/api/admin/video',{signal}), applyVideo, options); }
function loadAlasPermissions(options={}){
  const epoch=alasPermissionsEpoch;
  return requestResource('alasPermissions',signal=>api('/api/admin/alas/permissions',{signal}),data=>{
    if(alasPermissionsMutations || epoch!==alasPermissionsEpoch) return;
    applyAlasPermissions(data,epoch);
  },options);
}

function beginAlasPermissionsMutation(){
  alasPermissionsMutations+=1;
  alasPermissionsEpoch+=1;
  markResourceStale('alasPermissions');
  return alasPermissionsEpoch;
}

async function finishAlasPermissionsMutation(epoch, result, succeeded){
  alasPermissionsMutations=Math.max(0,alasPermissionsMutations-1);
  if(succeeded && epoch===alasPermissionsEpoch){
    markResourceStale('alasPermissions');
    applyAlasPermissions(result,epoch);
  } else if(epoch!==alasPermissionsEpoch){
    alasPermissionsNeedsRefresh=true;
  }
  if(!alasPermissionsMutations && alasPermissionsNeedsRefresh){
    alasPermissionsNeedsRefresh=false;
    try{ await loadAlasPermissions({force:true}); }
    catch(error){ reportRequestError(error,adminT('admin.errors.alas_ownership_refresh')); }
  }
}

async function mutateAlasPermissions(body){
  const epoch=beginAlasPermissionsMutation();
  try{
    const result=await api('/api/admin/alas/permissions',{method:'PUT',body});
    await finishAlasPermissionsMutation(epoch,result,true);
    return result;
  } catch(error){
    await finishAlasPermissionsMutation(epoch,null,false);
    throw error;
  }
}
function loadAlasDetails(options={}){
  markResourceStale('alasStatus');
  alasStatusLoading=false;
  alasStatusLoadingConfig='';
  const statusSequence=alasStatusSequence;
  const config=String(options.config === undefined ? selectedAlasConfig() : options.config || '').trim();
  const url='/api/admin/alas' + (config ? `?config=${encodeURIComponent(config)}` : '');
  return requestResource('alasDetails',signal=>api(url,{signal}),data=>applyAlasDetails(data,statusSequence),options);
}
async function loadAlas(options={}){
  const results=await Promise.allSettled([loadAlasPermissions(options),loadAlasDetails(options)]);
  if(results.every(result=>result.status==='rejected')) throw results[0].reason;
  state.alas={
    ...(state.alas || {}),
    details_error:results[1].status==='rejected' ? String(results[1].reason && results[1].reason.message || adminT('admin.errors.alas_data_read')) : ''
  };
  loadedResources.add('alas');
  renderAlas();
  return state.alas;
}
function loadAlasCatalog(options={}){
  return requestResource('alasCatalog',signal=>api('/api/admin/alas/configs',{signal}),applyAlasCatalog,options).catch(error=>{
    if(!isAbortError(error)){
      state.alas={...(state.alas || {}),catalog_error:String(error && error.message || adminT('admin.errors.catalog_read'))};
      renderAlas();
    }
    throw error;
  });
}
function loadLogs(options={}){
  setLogLoadState('logs','loading',adminT('admin.logs.loading_audit'));
  return requestResource('logs', signal=>api('/api/admin/logs',{signal}), applyLogs, options).catch(error=>{
    if(!isAbortError(error)) setLogLoadState('logs','error',adminT('admin.logs.audit_failed',{error:error.message || adminT('common.feedback.unknown_error')}));
    throw error;
  });
}
function loadRuntimeLogs(options={}){
  setLogLoadState('runtimeLogs','loading',adminT('admin.logs.loading_runtime'));
  return requestResource('runtimeLogs', signal=>api('/api/admin/runtime-logs?lines=400',{signal}), applyRuntimeLogs, options).catch(error=>{
    if(!isAbortError(error)) setLogLoadState('runtimeLogs','error',adminT('admin.logs.runtime_failed',{error:error.message || adminT('common.feedback.unknown_error')}));
    throw error;
  });
}
const RESOURCE_LOADERS = {overview:loadOverview, overviewAlas:loadOverviewAlas, devices:loadDevices, users:loadUsers, permissions:loadPermissions, video:loadVideo, alas:loadAlas, logs:loadLogs, runtimeLogs:loadRuntimeLogs};
async function loadTab(tabId, options={}){
  const names=TAB_RESOURCES[tabId] || TAB_RESOURCES.overview;
  const force=!!options.force;
  const results=await Promise.allSettled(names.map(name=>loadIfNeeded(name, RESOURCE_LOADERS[name], {force})));
  results.forEach((result, index)=>{ if(result.status==='rejected') reportRequestError(result.reason, adminT('admin.errors.resource_load',{name:names[index]})); });
  return results;
}
async function loadAll(){
  const visible=new Set(TAB_RESOURCES[activeTab] || []);
  const names=Object.keys(RESOURCE_LOADERS).filter(name=>name==='overview' || loadedResources.has(name) || visible.has(name));
  const results=await Promise.allSettled(names.map(name=>RESOURCE_LOADERS[name]({force:true})));
  results.forEach((result, index)=>{ if(result.status==='rejected') reportRequestError(result.reason, adminT('admin.errors.resource_load',{name:names[index]})); });
  return results;
}
async function refreshDomains(...names){
  const unique=[...new Set(names)];
  const visible=new Set(TAB_RESOURCES[activeTab] || []);
  const refresh=[];
  unique.forEach(name=>{
    const wasLoaded=loadedResources.has(name);
    markResourceStale(name);
    if(name === 'overview' || wasLoaded || visible.has(name)) refresh.push(name);
  });
  const results=await Promise.allSettled(refresh.map(name=>RESOURCE_LOADERS[name]({force:true})));
  results.forEach((result, index)=>{ if(result.status==='rejected') reportRequestError(result.reason, adminT('admin.errors.resource_refresh',{name:refresh[index]})); });
}
async function saveUser(){
  const scheduled=$('userExpiryMode').value==='scheduled';
  const expiresAt=scheduled ? localInputToEpoch($('userExpiresAt').value) : null;
  if(scheduled && expiresAt == null) throw new Error(adminT('admin.users.invalid_expiry'));
  const username=editingUsername || $('newUsername').value.trim();
  await api('/api/admin/users',{method:'PUT', body:{username, password:$('newPassword').value, role:$('newRole').value, expires_at:expiresAt}});
  show(adminT('admin.users.saved'));
  closeEditorDrawer('user');
  if(username===currentUsername && expiresAt != null && expiresAt<=Math.floor(Date.now()/1000)){
    window.location.assign('/login');
    return;
  }
  await refreshDomains('overview','overviewAlas','users','permissions','alas');
}
async function deleteUser(username){
  const confirmed=await confirmDanger({
    title:adminT('admin.users.delete'),
    message:adminT('admin.users.delete_message',{username}),
    confirmText:adminT('admin.users.delete')
  });
  if(!confirmed) return;
  await api(`/api/admin/users/${encodeURIComponent(username)}`,{method:'DELETE'});
  show(adminT('admin.users.deleted'));
  await refreshDomains('overview','overviewAlas','users','permissions','alas');
}
async function saveDevice(){
  stopDeviceStatusPolling(true);
  try{
    const id=$('deviceId').value.trim();
    const address=$('deviceAddress').value.trim();
    const body={name:$('deviceName').value.trim() || address,address,enabled:$('deviceEnabled').value==='true'};
    if(id) body.device_id=id;
    const result=await api('/api/admin/devices',{method:'PUT',body});
    markResourceStale('devices');
    applyDevices(result);
    loadedResources.add('devices');
    markResourceStale('overview');
    const savedId=String(result.device_id || id).trim();
    const saved=state.devices.find(device=>getDeviceId(device)===savedId);
    show(adminT('admin.devices.saved',{status:deviceAdbMeta(saved).label}));
    closeEditorDrawer('device');
    clearDeviceForm();
    await refreshDomains('permissions');
  } finally {
    scheduleDeviceStatusPoll();
  }
}
async function deleteDevice(id){
  const confirmed=await confirmDanger({
    title:adminT('admin.devices.delete_title'),
    message:adminT('admin.devices.delete_message',{id}),
    confirmText:adminT('admin.devices.delete_title')
  });
  if(!confirmed) return;
  stopDeviceStatusPolling(true);
  try{
    await api(`/api/admin/devices/${encodeURIComponent(id)}`,{method:'DELETE'});
    show(adminT('admin.devices.deleted'));
    await refreshDomains('overview','devices','permissions');
  } finally {
    scheduleDeviceStatusPoll();
  }
}
async function testDevice(id){
  if(deviceProbeRequests.has(id)) return;
  stopDeviceStatusPolling(true);
  deviceProbeRequests.add(id);
  applyDeviceAdbResult(id,{state:'checking',ok:false});
  try{
    const result=await api(`/api/admin/devices/${encodeURIComponent(id)}/adb/test`,{method:'POST'});
    applyDeviceAdbResult(id,result);
    show(`${deviceAdbMeta(result).label}${result.detail ? ` · ${result.detail}` : ''}`,5200);
  } catch(error){
    applyDeviceAdbResult(id,{state:'unknown',ok:false,detail:error.message || adminT('admin.devices.check_failed')});
    throw error;
  } finally {
    deviceProbeRequests.delete(id);
    const device=state.devices.find(item=>getDeviceId(item)===id);
    if(device) updateDeviceCardHeartbeat(device);
    scheduleDeviceStatusPoll();
  }
}
async function savePermission(){
  const user=selectedPermissionUser();
  if(!user) throw new Error(adminT('admin.permissions.select_user_first'));
  if(user.role==='admin') throw new Error(adminT('admin.permissions.admin_no_save_short'));
  if(!permissionsAreReady()) throw new Error(adminT('admin.permissions.not_ready'));
  const changes=permissionChangesForUser(user.username);
  if(!changes.length) return show(adminT('admin.permissions.no_changes'));
  const epoch=beginPermissionsMutation();
  let results=[];
  let refreshError=null;
  try{
    results=await Promise.allSettled(changes.map(change=>api('/api/admin/permissions',{
      method:'PUT',
      body:{username:change.username,device_id:change.device_id,can_view:change.can_view,can_control:change.can_control}
    })));
  } finally {
    try{ await finishPermissionsMutation(epoch); }
    catch(error){ refreshError=error; }
  }
  if(refreshError) throw new Error(adminT('admin.permissions.refresh_failed',{error:refreshError.message || adminT('common.feedback.unknown_error')}));
  const failed=results.filter(result=>result.status==='rejected');
  if(failed.length) throw new Error(adminT('admin.permissions.save_failed',{count:failed.length}));
  show(adminT('admin.permissions.saved',{count:changes.length}));
}
function collectVideoPresets(){ const presets={}; document.querySelectorAll('[data-preset-profile]').forEach(input=>{ const name=input.dataset.presetProfile; const field=input.dataset.presetField; let value=input.value; if(field==='video_bit_rate') value=bitrateMbpsToBps(value); else if(field==='max_size' && value===CUSTOM_OUTPUT_SIZE){ const control=input.closest('.preset-size-control'); const width=control.querySelector('[data-custom-width]'); const height=control.querySelector('[data-custom-height]'); if(!width.checkValidity() || !height.checkValidity()){ const invalid=!width.checkValidity() ? width : height; invalid.reportValidity(); throw new Error(adminT('admin.video.custom_size_invalid')); } value=Math.max(Number(width.value),Number(height.value)); } else value=Number(value); presets[name]=presets[name] || {}; presets[name][field]=value; }); return presets; }
function addCustomProfile(){ const id=($('customProfileId').value || '').trim(); if(!/^[a-zA-Z][a-zA-Z0-9_-]{1,31}$/.test(id)) return show(adminT('admin.video.invalid_profile_id')); if(NORMAL_PROFILE_NAMES.concat(['custom','auto']).includes(id) || id.startsWith('alas_')) return show(adminT('admin.video.reserved_profile_id')); const width=$('customProfileWidth'); const height=$('customProfileHeight'); if($('customProfileSizeSelect').value===CUSTOM_OUTPUT_SIZE && (!width.checkValidity() || !height.checkValidity())){ const invalid=!width.checkValidity() ? width : height; invalid.reportValidity(); return show(adminT('admin.video.custom_size_invalid')); } customProfiles[id]={label:($('customProfileLabel').value || id).trim(), video_bit_rate:bitrateMbpsToBps($('customProfileBitrate').value || 0.9), max_size:customProfileSizeValue(), max_fps:Number($('customProfileFps').value || 24)}; renderCustomProfiles(); refreshFullscreenProfilesFromForm(); show(adminT('admin.video.profile_added')); }
async function saveVideo(){ const presets=collectVideoPresets(); const profile=$('videoProfile').value || 'balanced'; const selected=presets[profile] || customProfiles[profile] || presets.balanced || {}; const fullscreenProfile=$('videoFullscreenProfile').value || 'sharp'; const payload={profile, fullscreen_profile:fullscreenProfile, adaptive:false, scrcpy_stream_mode:$('videoStreamMode').value || 'raw', scrcpy_enabled_stream_modes:collectEnabledStreamModes(), auto_stop_minutes:Number($('videoAutoStop').value || 15), video_bit_rate:selected.video_bit_rate, max_size:selected.max_size, max_fps:selected.max_fps, presets, custom_profiles:customProfiles}; markResourceStale('video'); const result=await api('/api/admin/video',{method:'PUT', body:payload}); applyVideo(result); loadedResources.add('video'); show(adminT('admin.video.saved')); }
async function saveAlas(){ await api('/api/admin/alas',{method:'PUT', body:{enabled:true, base_url:$('alasBaseUrl').value, api_token:$('alasToken').value}}); closeEditorDrawer('alasConnection'); show(adminT('admin.alas_ui.connection_saved')); await refreshDomains('overview','overviewAlas','alas'); }
async function reloadAlas(){
  const requests=[loadAlas({force:true})];
  if(activeAlasView==='configs') requests.push(loadAlasCatalog({force:true}));
  const results=await Promise.allSettled(requests);
  if(results.every(result=>result.status==='rejected')) throw results[0].reason;
  return results;
}
async function toggleAlas(){
  const config=selectedAlasConfig();
  if(!config) return show(adminT('admin.alas_ui.select_from_library'));
  const sequence=++alasToggleSequence;
  const result=requireAlasSuccess(await api('/api/admin/alas/toggle',{method:'POST', body:{config_name:config}}),adminT('admin.alas_ui.toggle_failed'));
  if(sequence!==alasToggleSequence) return;
  const current=selectedAlasConfig();
  show(configKey(current)===configKey(config) ? adminT('admin.alas_ui.status_updated',{config}) : adminT('admin.alas_ui.operation_completed',{config,current:current || adminT('admin.alas_ui.other_config')}));
  const statusRefresh=configKey(current)===configKey(config) ? loadAlasStatusForConfig(config) : Promise.resolve();
  await Promise.allSettled([statusRefresh,refreshDomains('overview','overviewAlas')]);
  return result;
}
async function loadConfig(){
  const config=alasConfigDrawerTarget;
  if(!config) throw new Error(adminT('admin.alas_ui.editor_target_missing'));
  const request=beginAlasConfigRead(config);
  try{
    const data=requireAlasSuccess(await api(`/api/admin/alas/config?config=${encodeURIComponent(config)}`,{signal:request.controller.signal}),adminT('admin.alas_ui.config_read_failed'));
    if(!alasConfigReadIsCurrent(request)) return;
    $('configEditor').value=JSON.stringify(data.data || {}, null, 2);
    show(adminT('admin.alas_ui.config_read',{config}));
    return data;
  } finally {
    if(alasConfigReadController===request.controller) alasConfigReadController=null;
  }
}
async function saveConfig(){
  const config=alasConfigDrawerTarget;
  if(!config) return show(adminT('admin.alas_ui.editor_target_missing'));
  let data;
  try{ data=JSON.parse($('configEditor').value); }
  catch(e){ show(adminT('admin.alas_ui.json_invalid')); return; }
  const request=beginAlasConfigRead(config);
  try{
    const result=requireAlasSuccess(await api('/api/admin/alas/config',{method:'PUT', body:{source:config, target:config, data}, signal:request.controller.signal}),adminT('admin.alas_ui.config_save_failed'));
    if(!alasConfigReadIsCurrent(request)) return;
    closeEditorDrawer('alasConfig');
    show(adminT('admin.alas_ui.config_saved',{config}));
    await refreshDomains('alas');
    return result;
  } finally {
    if(alasConfigReadController===request.controller) alasConfigReadController=null;
  }
}
async function saveAlasBinding(){
  const wasEditing=!!editingAlasAssignment;
  const username=alasAssignmentUsername();
  const configName=alasAssignmentConfigName();
  if(!username) return show(adminT('admin.permissions.select_user'));
  if(!configName) return show(adminT('admin.alas_ui.select_or_enter'));
  const otherOwners=configOwnership(configName).owners.filter(owner=>owner!==username);
  if(otherOwners.length) return show(adminT('admin.alas_ui.remove_before_assign',{config:configName,owners:otherOwners.join(adminT('admin.alas_ui.list_separator'))}));
  const payload={
    username,
    config_name:configName,
    enabled:true,
    can_run:$('alasBindRun').value==='true',
    can_edit:$('alasBindEdit').value==='true',
    is_default:$('alasBindDefault').value==='true' ? true : null
  };
  await mutateAlasPermissions(payload);
  selectedAlasUsername=username;
  localStorage.setItem(ALAS_USER_KEY,username);
  editingAlasAssignment=null;
  closeEditorDrawer('alasAssignment');
  if(wasEditing) focusAlasUserChoice(username);
  show(adminT('admin.alas_ui.ownership_saved',{username,config:configName}));
  await refreshDomains('overview','overviewAlas');
}
function activateTab(tabId, save=true, focusPanel=false){
  const target=$(tabId) ? tabId : 'overview';
  activeTab=target;
  document.querySelectorAll('[data-tab]').forEach(tab=>{
    const selected=tab.dataset.tab===target;
    tab.classList.toggle('active', selected);
    tab.setAttribute('aria-selected', String(selected));
    tab.tabIndex=selected?0:-1;
    if(selected) tab.setAttribute('aria-current','page');
    else tab.removeAttribute('aria-current');
  });
  document.querySelectorAll('.panel').forEach(panel=>{
    const selected=panel.id===target;
    panel.classList.toggle('active', selected);
    panel.hidden=!selected;
  });
  if(save) localStorage.setItem(ADMIN_TAB_KEY, target);
  loadTab(target).catch(error=>reportRequestError(error));
  syncDeviceStatusPolling();
  syncOverviewPolling();
  if(focusPanel) requestAnimationFrame(()=>$(target).focus({preventScroll:true}));
}
function initializeTabs(){
  const tablist=document.querySelector('.tabs');
  const tabs=[...document.querySelectorAll('[data-tab]')];
  if(tablist){
    tablist.setAttribute('role','tablist');
    tablist.setAttribute('aria-orientation','vertical');
  }
  tabs.forEach((tab,index)=>{
    tab.setAttribute('role','tab');
    const panel=$(tab.dataset.tab);
    if(panel){
      panel.setAttribute('role','tabpanel');
      panel.setAttribute('aria-labelledby',tab.id);
    }
    tab.onclick=()=>{
      const mobileNavOpen=adminNavMedia.matches && $('adminNav').classList.contains('is-open');
      activateTab(tab.dataset.tab, true, mobileNavOpen);
      if(mobileNavOpen) closeAdminNav(false);
    };
    tab.onkeydown=event=>{
      const keys=['ArrowDown','ArrowRight','ArrowUp','ArrowLeft','Home','End'];
      if(!keys.includes(event.key)) return;
      event.preventDefault();
      let next=index;
      if(event.key==='Home') next=0;
      else if(event.key==='End') next=tabs.length-1;
      else if(event.key==='ArrowDown' || event.key==='ArrowRight') next=(index+1)%tabs.length;
      else next=(index-1+tabs.length)%tabs.length;
      tabs[next].focus();
      activateTab(tabs[next].dataset.tab);
    };
  });
}
function initializeAdminNavigation(){
  $('adminNavToggle').onclick=()=>{
    if($('adminNav').classList.contains('is-open')) closeAdminNav();
    else openAdminNav($('adminNavToggle'));
  };
  $('adminNavClose').onclick=()=>closeAdminNav();
  $('adminNavBackdrop').onclick=()=>closeAdminNav();
  $('adminNav').addEventListener('keydown',trapAdminNavFocus);
  document.addEventListener('keydown',event=>{
    if(event.key==='Escape' && adminNavMedia.matches && $('adminNav').classList.contains('is-open')){
      event.preventDefault();
      closeAdminNav();
    }
  });
  if(adminNavMedia.addEventListener) adminNavMedia.addEventListener('change',syncAdminNavMode);
  else adminNavMedia.addListener(syncAdminNavMode);
  syncAdminNavMode();
}
function initializeEditorDrawers(){
  EDITOR_DRAWERS.forEach(name=>{
    const drawer=$(`${name}Drawer`);
    const backdrop=$(`${name}DrawerBackdrop`);
    drawer.dataset.uiBackdrop=`${name}DrawerBackdrop`;
    drawer.hidden=true;
    backdrop.hidden=true;
    backdrop.onclick=()=>closeEditorDrawer(name);
  });
  $('openDeviceDrawer').onclick=openNewDeviceDrawer;
  $('openUserDrawer').onclick=openNewUserDrawer;
  $('deviceDrawerClose').onclick=()=>closeEditorDrawer('device');
  $('cancelDeviceForm').onclick=()=>closeEditorDrawer('device');
  $('userDrawerClose').onclick=()=>closeEditorDrawer('user');
  $('cancelUserForm').onclick=()=>closeEditorDrawer('user');
  $('alasConnectionDrawerClose').onclick=()=>closeEditorDrawer('alasConnection');
  $('cancelAlasConnection').onclick=()=>closeEditorDrawer('alasConnection');
  $('alasAssignmentDrawerClose').onclick=()=>closeEditorDrawer('alasAssignment');
  $('cancelAlasAssignment').onclick=()=>closeEditorDrawer('alasAssignment');
  $('alasAssignmentDrawer').addEventListener('keydown',event=>{
    if(event.key!=='Escape') return;
    event.preventDefault();
    event.stopPropagation();
    closeEditorDrawer('alasAssignment');
  });
  $('alasConfigDrawerClose').onclick=()=>closeEditorDrawer('alasConfig');
  $('cancelAlasConfig').onclick=()=>closeEditorDrawer('alasConfig');
}
function activateAccessView(view,focus=false){
  activeAccessView=view==='permissions'?'permissions':'accounts';
  const accountsSelected=activeAccessView==='accounts';
  $('accessAccountsTab').classList.toggle('active',accountsSelected);
  $('accessAccountsTab').setAttribute('aria-selected',String(accountsSelected));
  $('accessAccountsTab').tabIndex=accountsSelected?0:-1;
  $('accessPermissionsTab').classList.toggle('active',!accountsSelected);
  $('accessPermissionsTab').setAttribute('aria-selected',String(!accountsSelected));
  $('accessPermissionsTab').tabIndex=accountsSelected?-1:0;
  $('accessAccountsView').hidden=!accountsSelected;
  $('accessPermissionsView').hidden=accountsSelected;
  localStorage.setItem(ACCESS_VIEW_KEY,activeAccessView);
  if(!accountsSelected) renderPermissions();
  if(focus) (accountsSelected?$('accessAccountsTab'):$('accessPermissionsTab')).focus({preventScroll:true});
}
function handlePermissionUserListKeydown(event){
  const choices=[...$('permissionUserList').querySelectorAll('.permission-user-choice')];
  if(!choices.length) return;
  const active=event.target.closest && event.target.closest('.permission-user-choice');
  const index=Math.max(0,choices.indexOf(active));
  let target=null;
  if(event.key==='Home') target=choices[0];
  else if(event.key==='End') target=choices[choices.length-1];
  else if(event.key==='ArrowDown' || event.key==='ArrowRight') target=choices[(index+1)%choices.length];
  else if(event.key==='ArrowUp' || event.key==='ArrowLeft') target=choices[(index-1+choices.length)%choices.length];
  else if((event.key==='Enter' || event.key===' ') && active) target=active;
  if(!target) return;
  event.preventDefault();
  selectPermissionUser(target.dataset.username,true);
}
function handlePermissionBeforeUnload(event){
  if(!totalPermissionDraftCount()) return;
  event.preventDefault();
  event.returnValue='';
}
function initializeAccessWorkspace(){
  const tabs=[$('accessAccountsTab'),$('accessPermissionsTab')];
  tabs.forEach((tab,index)=>{
    tab.onclick=()=>activateAccessView(index?'permissions':'accounts');
    tab.onkeydown=event=>{
      if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
      event.preventDefault();
      const next=event.key==='Home'?0:event.key==='End'?tabs.length-1:event.key==='ArrowRight'?(index+1)%tabs.length:(index-1+tabs.length)%tabs.length;
      activateAccessView(next?'permissions':'accounts',true);
    };
  });
  $('userSearch').oninput=()=>{ userAccountPage=1; renderUsers(); };
  $('userRoleFilter').onchange=()=>{ userAccountPage=1; renderUsers(); };
  $('userExpiryFilter').onchange=()=>{ userAccountPage=1; renderUsers(); };
  $('userPagePrevious').onclick=()=>{ userAccountPage=Math.max(1,userAccountPage-1); renderUsers(); };
  $('userPageNext').onclick=()=>{ userAccountPage+=1; renderUsers(); };
  $('userExpiryMode').onchange=syncUserExpiryFields;
  $('userExpiresAt').oninput=updateUserExpiryPreview;
  $('userExpiryShortcuts').querySelectorAll('[data-days]').forEach(button=>{
    button.onclick=()=>extendUserExpiry(Number(button.dataset.days));
  });
  $('permissionUserSearch').oninput=()=>{ renderPermissionUserList(); renderPermissionDetail(); };
  $('permissionUserList').onkeydown=handlePermissionUserListKeydown;
  $('permUser').onchange=()=>selectPermissionUser($('permUser').value);
  $('permissionDeviceSearch').oninput=()=>renderPermissionDetail();
  $('permissionDeviceFilter').onchange=()=>renderPermissionDetail();
  $('permissionRetry').onclick=()=>withBusy($('permissionRetry'),()=>loadPermissions({force:true}),adminT('admin.busy.loading')).catch(error=>reportRequestError(error,adminT('admin.permissions.load_prefix')));
  window.addEventListener('beforeunload',handlePermissionBeforeUnload);
  activateAccessView(activeAccessView);
}
function initializeAlasWorkspace(){
  $('alasUsersTab').onclick=()=>activateAlasView('users');
  $('alasConfigsTab').onclick=()=>activateAlasView('configs');
  [$('alasUsersTab'),$('alasConfigsTab')].forEach((tab,index,tabs)=>{
    tab.onkeydown=event=>{
      if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
      event.preventDefault();
      const next=event.key==='Home'?0:event.key==='End'?tabs.length-1:event.key==='ArrowRight'?(index+1)%tabs.length:(index-1+tabs.length)%tabs.length;
      activateAlasView(next?'configs':'users',true);
    };
  });
  $('alasUserSearch').oninput=()=>{ renderAlasUserList(); renderAlasUserDetail(); };
  $('alasUserFilter').onchange=()=>{ renderAlasUserList(); renderAlasUserDetail(); };
  $('alasConfigSearch').oninput=()=>renderAlasConfigList();
  $('alasBindUser').onchange=()=>{ renderAlasAssignmentConfigOptions(); updateAlasAssignmentOwnerHint(); syncAlasAssignmentSummary(); };
  $('alasBindConfig').onchange=()=>{ syncAlasAssignmentConfigMode(true); updateAlasAssignmentOwnerHint(); syncAlasAssignmentSummary(); };
  $('alasBindConfigCustom').oninput=()=>{ updateAlasAssignmentOwnerHint(); syncAlasAssignmentSummary(); };
  $('openAlasAssignment').onclick=event=>openAlasAssignmentDrawer(null,event.currentTarget);
  $('openAlasAssignmentDetail').onclick=event=>openAlasAssignmentDrawer(null,event.currentTarget);
  $('openAlasConnection').onclick=event=>openAlasConnectionDrawer(event.currentTarget);
  $('openConfigEditor').onclick=event=>openAlasConfigDrawer(event.currentTarget);
  $('alasOpenCurrent').onclick=event=>{
    if(selectedAlasConfig()) return;
    event.preventDefault();
    show(adminT('admin.alas_ui.select_from_library'));
  };
}
function initializeConfirmDialog(){
  const dialog=$('confirmDialog');
  $('confirmDialogCancel').onclick=()=>settleConfirmation(false);
  $('confirmDialogConfirm').onclick=()=>settleConfirmation(true);
  dialog.addEventListener('cancel',event=>{
    event.preventDefault();
    settleConfirmation(false);
  });
  dialog.addEventListener('close',()=>{
    if(!pendingConfirmation) return;
    const pending=pendingConfirmation;
    pendingConfirmation=null;
    pending.resolve(false);
    restoreConfirmationFocus(pending.trigger);
  });
}
function initializeVideoSizeControls(){ const select=$('customProfileSizeSelect'); const width=$('customProfileWidth'); const height=$('customProfileHeight'); if(select) select.onchange=()=>syncCustomProfileSizeEditor('select'); if(width) width.oninput=()=>syncCustomProfileSizeEditor('width'); if(height) height.oninput=()=>syncCustomProfileSizeEditor('height'); syncCustomProfileSizeEditor(); }
initializeAdminNavigation();
initializeEditorDrawers();
initializeConfirmDialog();
initializeVideoSizeControls();
initializeTabs();
initializeAccessWorkspace();
initializeAlasWorkspace();
document.addEventListener('visibilitychange',syncDeviceStatusPolling);
document.addEventListener('visibilitychange',syncOverviewPolling);
document.addEventListener('visibilitychange',()=>{ if(document.visibilityState==='visible') refreshUserExpirationStatuses(); });
setInterval(refreshUserExpirationStatuses,USER_EXPIRY_REFRESH_INTERVAL);
const savedInitialTab=$(localStorage.getItem(ADMIN_TAB_KEY)) ? localStorage.getItem(ADMIN_TAB_KEY) : 'overview';
activateTab('overview', false);
bindAction('reloadAll', loadAll, adminT('admin.busy.refreshing'));
bindAction('saveUser', saveUser, adminT('admin.busy.saving'));
bindAction('saveDevice', saveDevice, adminT('admin.busy.saving'));
$('clearDeviceForm').onclick=()=>clearDeviceForm();
bindAction('reloadRuntimeLogs', ()=>loadRuntimeLogs({force:true}), adminT('admin.busy.refreshing'));
bindAction('reloadAuditLogs', ()=>loadLogs({force:true}), adminT('admin.busy.refreshing'));
$('savePermission').onclick=()=>withBusy($('savePermission'),savePermission,adminT('admin.busy.saving')).catch(error=>show(error.message)).finally(()=>renderPermissionDetail());
bindAction('saveVideo', saveVideo, adminT('admin.busy.saving'));
bindAction('addCustomProfile', async()=>addCustomProfile(), adminT('admin.busy.adding'));
bindAction('saveAlas', saveAlas, adminT('admin.busy.saving'));
bindAction('reloadAlas', reloadAlas, adminT('admin.busy.refreshing'));
bindAction('toggleAlas', toggleAlas, adminT('admin.busy.executing'));
bindAction('loadConfig', loadConfig, adminT('admin.busy.reading'));
bindAction('saveConfig', saveConfig, adminT('admin.busy.saving'));
bindAction('saveAlasBinding', saveAlasBinding, adminT('admin.busy.saving'));
loadOverview()
  .then(()=>{ if(savedInitialTab !== 'overview') activateTab(savedInitialTab, false); })
  .catch(error=>reportRequestError(error));
