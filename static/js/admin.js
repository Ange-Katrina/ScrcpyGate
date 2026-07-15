const bootstrap = JSON.parse(document.getElementById("scrcpygate-bootstrap").textContent);
const csrfToken = bootstrap.csrf_token;
const currentUsername = bootstrap.user.username;
const state = { overview:null, users:[], devices:[], permissions:[], logs:[], runtimeLogs:[], video:{}, alas:null };
const NORMAL_PROFILE_NAMES = ['smooth','balanced','sharp','low_latency'];
const profileLabels = {smooth:'\u6d41\u7545', balanced:'\u7a33\u5b9a', sharp:'\u9ad8\u6e05', low_latency:'\u4f4e\u5ef6\u8fdf'};
const profileHints = {smooth:'\u6700\u4f4e\u4e0a\u884c\u8d1f\u8f7d', balanced:'\u65e5\u5e38\u9ed8\u8ba4', sharp:'\u753b\u9762\u66f4\u6e05\u6670', low_latency:'\u64cd\u4f5c\u4f18\u5148'};
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
const DEVICE_STATUS_POLL_INTERVAL = 5000;
const STATUS_LABELS = {running:'运行中', stopped:'已停止', idle:'空闲', error:'异常', disabled:'未启用', disconnected:'未连接', unknown:'未知', unbound:'未绑定配置'};
const ADB_STATUS_META = Object.freeze({
  online:{label:'ADB 在线',tone:'ok'},
  offline:{label:'ADB 离线',tone:'danger'},
  network_unreachable:{label:'网络不可达',tone:'danger'},
  unauthorized:{label:'ADB 等待授权',tone:'warn'},
  checking:{label:'正在检测 ADB',tone:'warn'},
  reconnecting:{label:'正在检测 ADB',tone:'warn'},
  disabled:{label:'ADB 未启用',tone:''},
  unknown:{label:'ADB 未检测',tone:'warn'}
});
const resourceRequests = new Map();
const resourceSequences = new Map();
const loadedResources = new Set();
const deviceProbeRequests = new Set();
const TAB_RESOURCES = {
  overview:['overview'],
  devices:['devices'],
  users:['users','permissions'],
  video:['video'],
  alas:['alas'],
  logs:['logs','runtimeLogs']
};
let activeTab = 'overview';
let activeAccessView = localStorage.getItem(ACCESS_VIEW_KEY)==='permissions' ? 'permissions' : 'accounts';
let userAccountPage = 1;
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
const EDITOR_DRAWERS = ['device','user','alasConnection','alasAssignment','alasConfig'];

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
function confirmDanger({title='确认危险操作', message, confirmText='确认操作', trigger=document.activeElement}){
  if(pendingConfirmation) settleConfirmation(false);
  $('confirmDialogTitle').textContent=title;
  $('confirmDialogMessage').textContent=message || '此操作可能无法撤销，请确认后继续。';
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
  logs:{status:'auditLogsStatus', target:'logRows', label:'审计日志'},
  runtimeLogs:{status:'runtimeLogsStatus', target:'runtimeLogs', label:'运行日志'}
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
  return `${label}已加载 ${count} 条 · ${new Date().toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'})}`;
}
function getDeviceId(device){ return String((device && (device.device_id || device.id || device.address)) || '').trim(); }
function statusLabel(value){ const key=String(value || 'unknown').trim(); return STATUS_LABELS[key] || key || '未知'; }
function setBusy(el, busy, text='处理中'){
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
async function withBusy(el, fn, text='处理中'){
  setBusy(el, true, text);
  try { return await fn(); }
  finally { setBusy(el, false); }
}
function bindAction(id, fn, text){
  const el=$(id);
  if(!el) return;
  el.onclick=()=>withBusy(el, fn, text).catch(e=>show(e.message));
}
function show(message){ const n=$('notice'); n.textContent=message || '操作失败'; n.classList.add('show'); clearTimeout(show.t); show.t=setTimeout(()=>n.classList.remove('show'),3200); }
async function api(url, options={}){ const opts=Object.assign({}, options, {headers:Object.assign({}, options.headers || {})}); if(opts.body && typeof opts.body !== 'string'){ opts.headers['content-type']='application/json'; opts.body=JSON.stringify(opts.body); } if(!['GET','HEAD'].includes((opts.method||'GET').toUpperCase())) opts.headers['x-csrf-token']=csrfToken; const res=await fetch(url, opts); const text=await res.text(); let data={}; try{ data=text?JSON.parse(text):{}; }catch(_){ data={detail:text}; } if(!res.ok) throw new Error(data.detail || `HTTP ${res.status}`); return data; }
function isAbortError(error){ return !!error && (error.name === 'AbortError' || error.code === 20); }
function reportRequestError(error, prefix='数据加载失败'){
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
function relativeTs(value){ const timestamp=Number(value); if(!Number.isFinite(timestamp) || timestamp<=0) return ''; const seconds=Math.max(0,Math.round(Date.now()/1000-timestamp)); if(seconds<10) return '刚刚'; if(seconds<60) return `${seconds} 秒前`; if(seconds<3600) return `${Math.floor(seconds/60)} 分钟前`; if(seconds<86400) return `${Math.floor(seconds/3600)} 小时前`; return new Date(timestamp*1000).toLocaleString([], {month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'}); }
function actionText(action){ return ({login_success:'登录成功', login_failed:'登录失败', logout:'退出登录', password_change:'修改密码', page_index:'进入投屏页', page_admin:'进入后台', mirror_start:'开始投屏', mirror_stop:'停止投屏', mirror_settings:'画质设置', user_upsert:'保存用户', user_delete:'删除用户', permission_set:'设备权限', device_upsert:'保存设备', device_delete:'删除设备', alas_toggle:'ALAS 操作', alas_admin_toggle:'后台 ALAS 操作', alas_settings:'ALAS 设置', alas_binding_set:'ALAS 绑定', alas_binding_delete:'取消 ALAS 绑定', alas_embed_open:'打开 ALAS 页面', alas_embed_denied:'ALAS 嵌入拒绝', alas_embed_proxy_failed:'ALAS 页面代理失败', alas_embed_ws_denied:'ALAS WebSocket 拒绝'})[action] || action; }
function activeSessions(){ return state.devices.filter(d => d.session && d.session.running).length; }
function renderOverview(){
  const sessionCount=activeSessions();
  $('summaryMirror').textContent=`投屏 ${sessionCount}`;
  $('summaryMirror').className='chip ' + (sessionCount?'ok':'');
  const alasStatus=(state.alas && state.alas.status && state.alas.status.status) || (state.overview && state.overview.alas && state.overview.alas.status) || 'unknown';
  $('summaryAlas').textContent=`ALAS ${statusLabel(alasStatus)}`;
  $('summaryAlas').className='chip ' + (alasStatus==='running'?'ok':alasStatus==='error'?'warn':'');

  const devices=$('overviewDevices');
  clear(devices);
  state.devices.forEach(device=>devices.appendChild(chip(`${device.name || getDeviceId(device)}: ${device.enabled ? '启用' : '禁用'}`, device.enabled?'ok':'warn')));
  if(!state.devices.length) devices.appendChild(chip('暂无设备','warn'));

  const mirrors=$('overviewMirror');
  clear(mirrors);
  state.devices.forEach(device=>{
    const running=!!(device.session && device.session.running);
    mirrors.appendChild(chip(`${device.name || getDeviceId(device)}: ${running?'投屏中':'未投屏'}`, running?'ok':''));
  });
  if(!state.devices.length) mirrors.appendChild(chip('暂无投屏'));

  const controls=$('overviewControl');
  clear(controls);
  const lockedDevices=state.devices.filter(device=>device.session && device.session.control_lock);
  lockedDevices.forEach(device=>{
    const lock=device.session.control_lock || {};
    const owner=lock.username || lock.owner || '已占用';
    controls.appendChild(chip(`${device.name || getDeviceId(device)}: ${owner}`, 'warn'));
  });
  if(!lockedDevices.length) controls.appendChild(chip('暂无控制占用','ok'));

  const alas=$('overviewAlas');
  clear(alas);
  const overviewAlas=state.overview && state.overview.alas;
  if(overviewAlas){
    alas.appendChild(chip(overviewAlas.enabled?'已启用':'未启用', overviewAlas.enabled?'ok':'warn'));
    alas.appendChild(chip(statusLabel(overviewAlas.status), overviewAlas.status==='running'?'ok':overviewAlas.status==='error'?'warn':''));
    if(overviewAlas.config) alas.appendChild(chip(`配置 ${overviewAlas.config}`));
  } else {
    alas.appendChild(chip('状态未知','warn'));
  }
}
function renderDevices(){
  const box=$('deviceCards');
  clear(box);
  if(!state.devices.length){ box.appendChild(chip('暂无设备','warn')); return; }
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
    statuses.appendChild(chip(device.enabled?'启用':'禁用', device.enabled?'ok':'warn'));
    statuses.appendChild(chip(device.session&&device.session.running?'投屏中':'未投屏', device.session&&device.session.running?'ok':''));
    if(device.session&&device.session.control_lock) statuses.appendChild(chip(`控制: ${device.session.control_lock.username || '已占用'}`, 'warn'));
    const heartbeat=document.createElement('dl');
    heartbeat.className='device-heartbeat';
    heartbeat.append(heartbeatField('网络延迟','latency','—'),heartbeatField('最后检测','checked','尚未检测'),heartbeatField('最后在线','seen','尚未在线'));
    const actions=document.createElement('div');
    actions.className='actions';
    const probeButton=btn(deviceProbeRequests.has(id)?'检测中':'立即检测','',()=>testDevice(id));
    probeButton.dataset.deviceProbe='1';
    probeButton.disabled=deviceProbeRequests.has(id);
    actions.append(
      btn('编辑','',()=>editDevice(device)),
      probeButton,
      btn('删除','danger',()=>deleteDevice(id))
    );
    card.append(heading,address,statuses,heartbeat,actions);
    box.appendChild(card);
    updateDeviceCardHeartbeat(device);
  });
}
function deviceAdbMeta(device){
  const raw=device && device.enabled===false ? 'disabled' : String(device && (device.adb_state || device.status_label) || 'unknown').toLowerCase();
  return Object.assign({state:raw}, ADB_STATUS_META[raw] || ADB_STATUS_META.unknown);
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
  if(checked){ checked.textContent=relativeTs(device.last_checked_at) || '尚未检测'; checked.title=ts(device.last_checked_at); }
  if(seen){ seen.textContent=relativeTs(device.last_seen_at) || '尚未在线'; seen.title=ts(device.last_seen_at); }
  const probe=card.querySelector('[data-device-probe]');
  if(probe){ probe.textContent=deviceProbeRequests.has(id)?'检测中':'立即检测'; probe.disabled=deviceProbeRequests.has(id); }
}
function applyDeviceStatuses(data){
  const statuses=data && data.devices || {};
  Object.entries(statuses).forEach(([id,status])=>applyDeviceAdbResult(id,status || {}));
  renderOverview();
}
function bitrateBpsToMbps(value){ const bps=Number(value); return Number.isFinite(bps) ? bps / 1000000 : ''; }
function bitrateMbpsToBps(value){ const mbps=Number(value); return Number.isFinite(mbps) ? Math.round(mbps * 1000000) : 0; }
function outputSizeMeta(value){ const maxSize=Math.min(MAX_OUTPUT_SIZE,Math.max(MIN_OUTPUT_SIZE,Math.round(Number(value)||MIN_OUTPUT_SIZE))); const standard=STANDARD_OUTPUT_SIZES.find(item=>item.maxSize===maxSize); return standard || {maxSize,width:maxSize,height:Math.round(maxSize*9/16),quality:''}; }
function dimensionsFromHeight(value){ const height=Math.min(MAX_OUTPUT_HEIGHT,Math.max(MIN_OUTPUT_HEIGHT,Math.round(Number(value)||MIN_OUTPUT_HEIGHT))); const width=Math.min(MAX_OUTPUT_SIZE,Math.max(MIN_OUTPUT_SIZE,Math.ceil(height*16/9))); return {maxSize:width,width,height:Math.round(width*9/16),quality:''}; }
function outputSizeOptionLabel(item){ return `${item.width} × ${item.height}${item.quality ? `（${item.quality}）` : ''}`; }
function maxSizeQualityLabel(value){ const size=Number(value); if(!Number.isFinite(size) || size<=0) return '原始尺寸'; const item=outputSizeMeta(size); return `${outputSizeOptionLabel(item)} · 最长边 ${item.maxSize}px`; }
function presetFieldDisplayValue(field, value){ return field==='video_bit_rate' ? bitrateBpsToMbps(value) : value; }
function populateOutputSizeSelect(select,value){ clear(select); STANDARD_OUTPUT_SIZES.forEach(item=>{ const option=document.createElement('option'); option.value=String(item.maxSize); option.textContent=outputSizeOptionLabel(item); select.appendChild(option); }); const custom=document.createElement('option'); custom.value=CUSTOM_OUTPUT_SIZE; custom.textContent='自定义尺寸…'; select.appendChild(custom); select.value=STANDARD_OUTPUT_SIZES.some(item=>item.maxSize===Number(value)) ? String(value) : CUSTOM_OUTPUT_SIZE; }
function syncPresetSizeControl(control,value,forceCustom=false){ const select=control.querySelector('select'); const editor=control.querySelector('.custom-size-editor'); const width=control.querySelector('[data-custom-width]'); const height=control.querySelector('[data-custom-height]'); const output=control.querySelector('output'); const item=outputSizeMeta(value); const standard=!forceCustom && STANDARD_OUTPUT_SIZES.some(option=>option.maxSize===item.maxSize); select.value=standard ? String(item.maxSize) : CUSTOM_OUTPUT_SIZE; width.value=String(item.width); height.value=String(item.height); editor.hidden=select.value!==CUSTOM_OUTPUT_SIZE; width.disabled=editor.hidden; height.disabled=editor.hidden; output.value=`将使用最长边 ${item.maxSize}px`; output.textContent=output.value; }
function syncPresetCustomDimensions(control,source){ const width=control.querySelector('[data-custom-width]'); const height=control.querySelector('[data-custom-height]'); const output=control.querySelector('output'); const sourceInput=source==='height' ? height : width; if(!sourceInput.checkValidity()){ output.value=source==='height' ? `高度需为 ${MIN_OUTPUT_HEIGHT}–${MAX_OUTPUT_HEIGHT}px` : `宽度需为 ${MIN_OUTPUT_SIZE}–${MAX_OUTPUT_SIZE}px`; output.textContent=output.value; return; } const item=source==='height' ? dimensionsFromHeight(height.value) : outputSizeMeta(width.value); if(source==='height'){ width.value=String(item.width); height.value=String(item.height); } else height.value=String(item.height); output.value=`将使用最长边 ${Math.max(Number(width.value),Number(height.value))}px`; output.textContent=output.value; }
function presetSizeControl(profile,value){ const control=document.createElement('div'); control.className='preset-size-control'; const select=document.createElement('select'); select.dataset.presetProfile=profile; select.dataset.presetField='max_size'; select.setAttribute('aria-label',`${profileLabels[profile] || profile}输出尺寸`); populateOutputSizeSelect(select,value); const editor=document.createElement('div'); editor.className='custom-size-editor'; const pair=document.createElement('div'); pair.className='custom-size-pair'; pair.setAttribute('role','group'); pair.setAttribute('aria-label',`${profileLabels[profile] || profile}自定义输出尺寸`); const widthLabel=document.createElement('label'); widthLabel.className='custom-size-field'; const widthText=document.createElement('span'); widthText.textContent='宽度 px'; const width=document.createElement('input'); width.type='number'; width.min=String(MIN_OUTPUT_SIZE); width.max=String(MAX_OUTPUT_SIZE); width.step='1'; width.inputMode='numeric'; width.required=true; width.dataset.customWidth='1'; width.setAttribute('aria-label',`${profileLabels[profile] || profile}自定义宽度`); widthLabel.append(widthText,width); const separator=document.createElement('span'); separator.className='custom-size-separator'; separator.setAttribute('aria-hidden','true'); separator.textContent='×'; const heightLabel=document.createElement('label'); heightLabel.className='custom-size-field'; const heightText=document.createElement('span'); heightText.textContent='高度 px'; const height=document.createElement('input'); height.type='number'; height.min=String(MIN_OUTPUT_HEIGHT); height.max=String(MAX_OUTPUT_HEIGHT); height.step='1'; height.inputMode='numeric'; height.required=true; height.dataset.customHeight='1'; height.setAttribute('aria-label',`${profileLabels[profile] || profile}自定义高度`); heightLabel.append(heightText,height); const output=document.createElement('output'); output.setAttribute('aria-live','polite'); pair.append(widthLabel,separator,heightLabel); editor.append(pair,output); control.append(select,editor); select.onchange=()=>syncPresetSizeControl(control,select.value===CUSTOM_OUTPUT_SIZE ? width.value : select.value,select.value===CUSTOM_OUTPUT_SIZE); width.oninput=()=>syncPresetCustomDimensions(control,'width'); height.oninput=()=>syncPresetCustomDimensions(control,'height'); syncPresetSizeControl(control,value); return control; }
function presetInput(profile, field, value){ if(field==='max_size') return presetSizeControl(profile,value); const input=document.createElement('input'); input.type='number'; input.value=value == null ? '' : presetFieldDisplayValue(field, value); input.dataset.presetProfile=profile; input.dataset.presetField=field; if(field==='video_bit_rate'){ input.min='0.1'; input.max='100'; input.step='0.05'; input.inputMode='decimal'; } else { input.min='1'; input.max='60'; input.step='1'; } return input; }
function setPresetValue(profile, field, value){ const input=document.querySelector(`[data-preset-profile="${profile}"][data-preset-field="${field}"]`); if(!input) return; if(field==='max_size'){ syncPresetSizeControl(input.closest('.preset-size-control'),value); return; } input.value=presetFieldDisplayValue(field, value); }
function renderBandwidthActions(recommendations){ const box=$('bandwidthPresetActions'); if(!box) return; clear(box); Object.keys(recommendations).sort((a,b)=>parseInt(a)-parseInt(b)).forEach(name=>box.appendChild(btn(name.toUpperCase(), 'warn', ()=>applyBandwidthRecommendation(name)))); }
function applyBandwidthRecommendation(name){ const rec=((state.video || {}).bandwidth_recommendations || {})[name]; if(!rec) return; Object.entries(rec).forEach(([profile, values])=>['video_bit_rate','max_size','max_fps'].forEach(field=>setPresetValue(profile, field, values[field]))); show(`${name.toUpperCase()} \u63a8\u8350\u503c\u5df2\u586b\u5165\uff0c\u786e\u8ba4\u540e\u70b9\u4fdd\u5b58`); }
async function removeCustomProfile(id){
  const confirmed=await confirmDanger({
    title:'删除自定义档位',
    message:`将自定义档位 ${id} 从当前设置中移除。保存画质设置后生效。`,
    confirmText:'删除档位'
  });
  if(!confirmed) return;
  delete customProfiles[id];
  renderCustomProfiles();
  show('自定义档位已移除，确认后点保存');
}
function syncCustomProfileSizeEditor(source='select'){ const select=$('customProfileSizeSelect'); const width=$('customProfileWidth'); const height=$('customProfileHeight'); const wrap=$('customProfileSizeCustomWrap'); const output=$('customProfileSizeReference'); if(!select || !width || !height || !wrap || !output) return; const custom=select.value===CUSTOM_OUTPUT_SIZE; wrap.hidden=!custom; width.disabled=!custom; height.disabled=!custom; if(!custom){ const item=outputSizeMeta(select.value); width.value=String(item.width); height.value=String(item.height); output.value=`将使用最长边 ${item.maxSize}px`; } else { const sourceInput=source==='height' ? height : width; if(!sourceInput.checkValidity()){ output.value=source==='height' ? `高度需为 ${MIN_OUTPUT_HEIGHT}–${MAX_OUTPUT_HEIGHT}px` : `宽度需为 ${MIN_OUTPUT_SIZE}–${MAX_OUTPUT_SIZE}px`; output.textContent=output.value; return; } const item=source==='height' ? dimensionsFromHeight(height.value) : outputSizeMeta(width.value); if(source==='height'){ width.value=String(item.width); height.value=String(item.height); } else height.value=String(item.height); output.value=`将使用最长边 ${Math.max(Number(width.value),Number(height.value))}px`; } output.textContent=output.value; }
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
      btn('编辑','',()=>{
        $('customProfileId').value=id;
        $('customProfileLabel').value=profile.label || id;
        $('customProfileBitrate').value=bitrateBpsToMbps(profile.video_bit_rate || 900000);
        setCustomProfileSize(profile.max_size || 960);
        $('customProfileFps').value=profile.max_fps || 24;
      }),
      btn('删除','danger',()=>removeCustomProfile(id))
    );
    tr.appendChild(actions);
    rows.appendChild(tr);
  });
  applyTableLabels(rows);
}
function streamModeLabel(mode){ return mode === 'legacy' ? 'legacy 诊断' : mode; }
function renderVideoStreamModes(data, selected){ const modes=data.stream_modes || ['raw','protocol','legacy']; const enabled=new Set(data.enabled_stream_modes || ['raw']); enabled.add('raw'); const toggles=$('streamModeToggles'); if(toggles){ clear(toggles); modes.forEach(mode=>{ const label=document.createElement('label'); label.className='stream-mode-toggle'; const input=document.createElement('input'); input.type='checkbox'; input.value=mode; input.checked=enabled.has(mode); input.disabled=mode==='raw'; input.dataset.streamModeToggle='1'; label.append(input, document.createTextNode(streamModeLabel(mode))); toggles.appendChild(label); }); } const select=$('videoStreamMode'); if(select){ clear(select); modes.filter(mode=>enabled.has(mode)).forEach(mode=>{ const option=document.createElement('option'); option.value=mode; option.textContent=streamModeLabel(mode); select.appendChild(option); }); select.value=enabled.has(selected) ? selected : 'raw'; } }
function collectEnabledStreamModes(){ const modes=['raw']; document.querySelectorAll('[data-stream-mode-toggle]').forEach(input=>{ if(input.checked && !modes.includes(input.value)) modes.push(input.value); }); return modes; }
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
      option.textContent=labels[name] || profileLabels[name] || name;
      $('videoProfile').appendChild(option);
    });
    $('videoProfile').value=settings.video_profile || defaults.profile || 'balanced';
  }
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
      tr.append(td(labels[name] || profileLabels[name] || name));
      ['video_bit_rate','max_size','max_fps'].forEach(field=>{
        const cell=document.createElement('td');
        cell.appendChild(presetInput(name, field, profile[field]));
        tr.appendChild(cell);
      });
      tr.append(td(profileHints[name] || ''));
      rows.appendChild(tr);
    });
    applyTableLabels(rows);
  }
  renderCustomProfiles();
}
function setDeviceDrawerContext(device=null){
  const id=device ? getDeviceId(device) : '';
  $('deviceDrawerTitle').textContent=device?'编辑设备':'新建设备';
  $('deviceDrawerContext').textContent=device ? `正在编辑 ${device.name || id}` : '填写名称、ADB 地址与启用状态';
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
  $('newUsername').value='';
  $('newPassword').value='';
  $('newRole').value='user';
  $('userDrawerTitle').textContent='新建用户';
  $('userDrawerContext').textContent='创建新的后台账户并选择角色';
}
function openNewUserDrawer(){
  clearUserForm();
  openEditorDrawer('user', $('openUserDrawer'));
}
function editUser(user){
  $('newUsername').value=user.username;
  $('newPassword').value='';
  $('newRole').value=user.role;
  $('userDrawerTitle').textContent='编辑用户';
  $('userDrawerContext').textContent=`正在编辑 ${user.username}，留空密码将保留原密码`;
  openEditorDrawer('user', document.activeElement);
}
function accessRoleLabel(role){ return role==='admin'?'管理员':'普通用户'; }
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
  return state.users.filter(user=>(!query || user.username.toLocaleLowerCase().includes(query)) && (role==='all' || user.role===role));
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
    tr.append(td(user.username), td(accessRoleLabel(user.role)), td(user.created_at));
    const actions=document.createElement('td');
    actions.className='actions';
    actions.append(btn('编辑','',()=>editUser(user)));
    if(user.username !== currentUsername) actions.append(btn('删除','danger',()=>deleteUser(user.username)));
    tr.appendChild(actions);
    rows.appendChild(tr);
  });
  if(!pageUsers.length) appendTableEmpty(rows,4,state.users.length?'没有符合筛选条件的用户。':'暂无用户。');
  $('userResultCount').textContent=filtered.length===state.users.length ? `${filtered.length} 位` : `${filtered.length} / ${state.users.length} 位`;
  $('userPageStatus').textContent=`第 ${userAccountPage} / ${pageCount} 页`;
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
  return `${accessRoleLabel(user.role)}${count ? ` · ${count} 项待保存` : ''}`;
}
function syncPermissionDraftIndicators(){
  const counts=permissionDraftCounts();
  const total=totalPermissionDraftCount(counts);
  const users=new Map(state.users.map(user=>[user.username,user]));
  const summary=$('accessPermissionDraftSummary');
  summary.textContent=total ? `${total} 项待保存` : '按用户配置';
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
    option.textContent='暂无用户';
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
  if(!visibleUsers.length) list.appendChild(disabledPermissionOption(state.users.length?'没有符合搜索条件的用户。':'暂无用户。'));
  $('permissionUserResultCount').textContent=visibleUsers.length===state.users.length ? `${visibleUsers.length} 位` : `${visibleUsers.length} / ${state.users.length} 位`;
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
  meta.textContent=`${getDeviceId(device)} · ${device.enabled?'已启用':'已禁用'}`;
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
  if(phase==='loading') status.textContent='正在加载设备权限…';
  else if(phase==='error') status.textContent=`设备权限加载失败：${permissionsLoadError || '未知错误'}`;
  else if(phase==='ready') status.textContent='设备权限已与服务器同步。';
  else status.textContent='等待加载设备权限。';
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
  $('permissionSelectedUser').textContent=user ? user.username : '请选择用户';
  $('permissionUserRole').textContent=user ? accessRoleLabel(user.role) : '未选择';
  $('permissionUserRole').className=`chip ${isAdmin?'ok':''}`.trim();
  $('permissionAdminNotice').hidden=!isAdmin;
  $('permissionDirtyCount').textContent=`${dirtyCount} 项待保存`;
  $('permissionDirtyCount').className=`chip ${dirtyCount?'warn':''}`.trim();
  const visibleDevices=permissionKnown ? permissionDevicesFor(user) : [];
  const totalDevices=state.devices.length;
  const allowedView=permissionKnown ? state.devices.filter(device=>effectivePermissionFor(user,getDeviceId(device)).can_view).length : 0;
  const allowedControl=permissionKnown ? state.devices.filter(device=>effectivePermissionFor(user,getDeviceId(device)).can_control).length : 0;
  $('permissionSelectedUserMeta').textContent=!user ? '选择用户后，仅显示该用户的设备访问权限。' : isAdmin ? `角色权限已覆盖 ${totalDevices} 台设备，当前页面仅供核对。` : !ready ? (permissionsLoadPhase==='error'?'设备权限读取失败，当前数据不可编辑。':'正在读取该用户的设备权限…') : `共 ${totalDevices} 台设备 · 可查看 ${allowedView} 台 · 可控制 ${allowedControl} 台`;
  $('permissionDeviceResultCount').textContent=permissionKnown ? (visibleDevices.length===totalDevices ? `${visibleDevices.length} 台` : `${visibleDevices.length} / ${totalDevices} 台`) : '— 台';
  clear(rows);
  visibleDevices.forEach(device=>{
    const id=getDeviceId(device);
    const permission=effectivePermissionFor(user,id);
    const row=document.createElement('tr');
    row.dataset.deviceId=id;
    row.classList.toggle('is-dirty',permissionDrafts.has(permissionDraftKey(user.username,id)));
    row.append(permissionDeviceCell(device),permissionCheckboxCell(user,device,'can_view','允许查看',permission.can_view),permissionCheckboxCell(user,device,'can_control','允许控制',permission.can_control));
    rows.appendChild(row);
  });
  if(!visibleDevices.length){
    const emptyMessage=!user?'请先选择用户。':!permissionKnown?(permissionsLoadPhase==='error'?'设备权限加载失败，无法确认当前权限。':'正在加载设备权限…'):totalDevices?'没有符合筛选条件的设备。':'暂无设备。';
    appendTableEmpty(rows,3,emptyMessage);
  }
  applyTableLabels(rows);
  const saveButton=$('savePermission');
  saveButton.disabled=!user || isAdmin || !ready || !dirtyCount || permissionsMutations>0;
  $('permissionSaveStatus').textContent=!user ? '请先选择用户。' : isAdmin ? '管理员权限由角色统一授予，不需要保存。' : !ready ? (dirtyCount?`仍有 ${dirtyCount} 台设备的草稿；权限重新加载成功后才能保存。`:'权限加载成功后才能编辑和保存。') : dirtyCount ? `有 ${dirtyCount} 台设备的权限尚未保存。` : '当前权限已与服务器同步。';
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
  return stamp ? new Date(stamp * 1000).toLocaleString([], {year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit'}) : '暂无记录';
}
function alasRoleLabel(role){ return role==='admin'?'管理员':'普通用户'; }
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
  let runtimeLabel='未配置';
  let runtimeMeta='请打开连接设置配置 Runtime';
  if(detailsError){
    runtimeLabel='数据异常';
    runtimeMeta=detailsError;
  } else if(settings.enabled && settings.token_set){
    runtimeLabel=catalogError?'连接异常':'已连接';
    runtimeMeta=catalogError || `${(catalog.runtime_configs || []).length} 个 Runtime 配置`;
  } else if(settings.enabled){
    runtimeLabel='缺少 Token';
    runtimeMeta='Runtime 已启用，访问 Token 未配置';
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
      kind:'用户账号',
      badges:[`${count} 个配置`],
      selected:user.username===selectedAlasUsername,
      onSelect:()=>{ selectedAlasUsername=user.username; localStorage.setItem(ALAS_USER_KEY,user.username); renderAlasUserList(); renderAlasUserDetail(); }
    }));
  });
  if(!visible.length) list.appendChild(emptyListboxOption(query || filter!=='all' ? '没有符合筛选条件的用户。' : '暂无可分配用户。'));
  $('alasUserResultCount').textContent=`${visible.length} 位`;
  restoreAlasChoiceFocus(list,focusedKey);
}
function renderAlasUserDetail(){
  const user=alasUsers().find(item=>item.username===selectedAlasUsername);
  const rows=$('alasBindRows');
  clear(rows);
  $('openAlasAssignmentDetail').disabled=!user;
  $('openAlasAssignment').disabled=!user;
  if(!user){
    $('alasSelectedUser').textContent='请选择用户';
    $('alasSelectedUserMeta').textContent='选择左侧用户后管理其配置权限。';
    rows.appendChild(emptyMessage('没有可显示的用户。'));
    return;
  }
  const assignments=assignmentsForUser(user.username);
  $('alasSelectedUser').textContent=user.username;
  $('alasSelectedUserMeta').textContent=`${alasRoleLabel(user.role)} · 拥有 ${assignments.length} 个配置`;
  assignments.forEach(binding=>{
    const ownership=configOwnership(binding.config_name);
    const otherOwners=ownership.owners.filter(owner=>owner!==binding.username);
    const row=document.createElement('article');
    row.className='alas-assignment-row';
    const main=document.createElement('div');
    main.className='alas-assignment-row__main';
    const kind=document.createElement('span');
    kind.className='alas-assignment-row__kind';
    kind.textContent='Runtime 配置';
    const title=document.createElement('h4');
    title.textContent=binding.config_name;
    const badges=document.createElement('div');
    badges.className='chips';
    if(binding.is_default) badges.appendChild(chip('默认','ok'));
    badges.appendChild(chip(binding.can_run?'可运行':'仅查看',binding.can_run?'ok':''));
    badges.appendChild(chip(binding.can_edit?'可编辑':'不可编辑',binding.can_edit?'ok':''));
    main.append(kind,title,badges);
    const meta=document.createElement('div');
    meta.className='alas-assignment-row__meta';
    const ownerText=document.createElement('span');
    ownerText.textContent=otherOwners.length ? `归属冲突：还关联 ${otherOwners.join('、')}` : `独占归属：${binding.username}`;
    const updated=document.createElement('span');
    updated.textContent=`更新于 ${formatAlasUpdated(binding.updated_at)}`;
    meta.append(ownerText,updated);
    const actions=document.createElement('div');
    actions.className='actions alas-assignment-row__actions';
    actions.append(btn('编辑','',()=>openAlasAssignmentDrawer(binding,document.activeElement)), btn('移除','danger',()=>removeAlasBinding(binding)));
    row.append(main,meta,actions);
    rows.appendChild(row);
  });
  if(!assignments.length) rows.appendChild(emptyMessage('此用户尚未分配 ALAS 配置。点击“分配配置”开始。'));
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
      meta:runtime.has(configKey(name))?'Runtime 中存在':'仅存在归属记录',
      kind:'Runtime 配置',
      badges:[ownership.conflict?'归属冲突':ownership.owner?`归属 ${ownership.owner}`:'未分配'],
      selected:configKey(name)===configKey(selectedAlasConfigName),
      tabStop:configKey(name)===configKey(selectedAlasConfigName) || (!selectedVisible && index===0),
      onSelect:()=>selectAlasConfig(name)
    }));
  });
  if(!visible.length) list.appendChild(emptyListboxOption(query ? '没有匹配的配置。' : 'Runtime 与归属记录中都没有配置。'));
  $('alasConfigResultCount').textContent=`${visible.length} 个`;
  const catalog=alasCatalog();
  const error=String(catalog.error || (state.alas && state.alas.catalog_error) || '');
  $('alasCatalogState').textContent=error ? `Runtime 配置读取失败：${error}` : `Runtime ${(catalog.runtime_configs || []).length} 个 · 已归属 ${((state.alas && state.alas.bound_configs) || []).length} 个`;
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
  $('alasCurrentConfigName').textContent=config || '请选择配置';
  $('alasCurrentConfigMeta').textContent=config ? `${runtime.has(configKey(config))?'Runtime 中存在':'仅存在归属记录'} · ${ownership.conflict?'归属冲突':ownership.owner?`归属 ${ownership.owner}`:'未分配'}` : '选择左侧配置后才会读取运行状态。';
  const statusBox=$('alasStatus');
  clear(statusBox);
  if(!config) statusBox.appendChild(chip('无配置','warn'));
  else if(loadingCurrent) statusBox.appendChild(chip('正在读取','warn'));
  else if(!statusMatches) statusBox.appendChild(chip('状态未读取'));
  else {
    statusBox.appendChild(chip(statusLabel(status.status),status.status==='running'?'ok':status.status==='error'?'danger':''));
    if(status.task) statusBox.appendChild(chip(String(status.task)));
    if(status.error) statusBox.appendChild(chip(String(status.error),'danger'));
  }
  if(config && ownership.conflict) $('alasImpactNote').textContent=`检测到历史归属冲突：${config} 同时关联 ${ownership.owners.join('、')}。请先在“用户与归属”中移除多余关系。`;
  else if(config && ownership.owner) $('alasImpactNote').textContent=`启停或编辑 ${config} 只影响归属用户 ${ownership.owner} 使用的此配置。`;
  else if(config) $('alasImpactNote').textContent=`${config} 当前未分配给用户；管理员仍可检查或维护它。`;
  else $('alasImpactNote').textContent='启停操作只会发送给当前配置。';
  $('toggleAlas').disabled=!config || !settings.enabled || !settings.token_set || loadingCurrent;
  $('toggleAlas').textContent=statusMatches && status.status==='running'?'停止 ALAS':statusMatches && status.status==='error'?'重启 ALAS':'启动 ALAS';
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
    permissions.textContent=[binding.is_default?'默认':null,binding.can_run?'可运行':'仅查看',binding.can_edit?'可编辑':null].filter(Boolean).join(' · ');
    pill.append(label,permissions);
    currentUsers.appendChild(pill);
  } else if(ownership.conflict) currentUsers.appendChild(emptyMessage(`历史归属冲突：${ownership.owners.join('、')}。请先移除多余授权。`));
  else currentUsers.appendChild(emptyMessage('此配置尚未分配给用户。'));
  $('alasCurrentUserCount').textContent=ownership.conflict?'需处理':ownership.owner?'已分配':'未分配';
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
  placeholder.textContent='请选择 Runtime 配置';
  placeholder.disabled=true;
  select.appendChild(placeholder);
  if(unassigned.length){
    const group=document.createElement('optgroup');
    group.label='未分配配置';
    unassigned.forEach(({name})=>{
      const option=document.createElement('option');
      option.value=name;
      option.textContent=`${name} · 未分配`;
      group.appendChild(option);
    });
    select.appendChild(group);
  }
  if(ownedByCurrent.length){
    const group=document.createElement('optgroup');
    group.label='当前用户已拥有';
    ownedByCurrent.forEach(({name})=>{
      const option=document.createElement('option');
      option.value=name;
      option.textContent=`${name} · 已归属当前用户`;
      group.appendChild(option);
    });
    select.appendChild(group);
  }
  if(occupied.length){
    const group=document.createElement('optgroup');
    group.label='已归属其他用户（不可选）';
    occupied.forEach(({name,ownership,otherOwners})=>{
      const option=document.createElement('option');
      option.value=name;
      option.textContent=ownership.conflict ? `${name} · 归属冲突 ${ownership.owners.join('、')}` : `${name} · 已归属 ${otherOwners.join('、')}`;
      option.disabled=true;
      group.appendChild(option);
    });
    select.appendChild(group);
  }
  const manual=document.createElement('option');
  manual.value='';
  manual.dataset.manual='true';
  manual.textContent='手动输入配置名称…';
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
    title:'移除配置归属',
    message:`只解除 ${binding.config_name} 与用户 ${binding.username} 的归属关系；Runtime 中的配置内容不会被删除。`,
    confirmText:'移除归属'
  });
  if(!confirmed) return;
  await mutateAlasPermissions({username:binding.username, config_name:binding.config_name, enabled:false, is_default:false});
  focusAlasUserChoice(binding.username);
  show('配置归属已移除');
  await refreshDomains('overview');
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
    loadIfNeeded('alasCatalog',loadAlasCatalog).catch(error=>reportRequestError(error,'配置库加载失败')).finally(()=>{
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
    if(!isAbortError(error)) show(`配置状态读取失败：${error.message}`);
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
  $('alasConnectionDrawerContext').textContent=settings.enabled ? `当前 Runtime：${settings.base_url || '未填写地址'}` : '连接 Alas-Gyre Overlay Runtime';
  openEditorDrawer('alasConnection',trigger);
}
function openAlasAssignmentDrawer(binding=null,trigger=document.activeElement){
  const users=alasUsers();
  if(!users.length) return show('请先创建用户');
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
  $('alasAssignmentDrawerTitle').textContent=binding?'编辑配置归属与权限':'分配配置归属';
  $('alasAssignmentDrawerContext').textContent=binding ? `正在编辑 ${binding.username} → ${binding.config_name}；如需更换归属，请先移除当前归属` : `为 ${username} 分配一个独占配置`;
  updateAlasAssignmentOwnerHint();
  syncAlasAssignmentSummary();
  openEditorDrawer('alasAssignment',trigger);
  loadIfNeeded('alasCatalog',loadAlasCatalog).catch(error=>reportRequestError(error,'配置建议加载失败'));
}
function syncAlasAssignmentSummary(){
  const username=alasAssignmentUsername();
  const configName=alasAssignmentConfigName();
  const user=alasUsers().find(item=>item.username===username);
  $('alasAssignmentUserName').textContent=username || '待选择用户';
  $('alasAssignmentUserMeta').textContent=user ? `${alasRoleLabel(user.role)} · 已拥有 ${assignmentsForUser(username).length} 个配置` : '选择接收配置的用户';
  $('alasAssignmentConfigName').textContent=configName || '待选择配置';
  const summary=$('alasAssignmentConfigSummary');
  let stateName='idle';
  let meta='输入或选择一个配置名称';
  if(configName){
    const ownership=configOwnership(configName);
    const otherOwners=ownership.owners.filter(owner=>owner!==username);
    if(otherOwners.length){
      stateName='error';
      meta=`已归属 ${otherOwners.join('、')}`;
    } else if(ownership.owner===username){
      stateName='ready';
      meta='已归属当前用户，将更新权限';
    } else {
      stateName='available';
      meta='当前未分配，可以建立归属';
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
    hint.textContent='每个配置只能独占归属一个用户；一个用户仍可拥有多个配置。';
    stateName='idle';
  } else {
    const ownership=configOwnership(configName);
    const otherOwners=ownership.owners.filter(owner=>owner!==username);
    blocked=otherOwners.length>0;
    if(blocked){
      hint.textContent=`${configName} 已归属 ${otherOwners.join('、')}，不能直接分配给 ${username || '当前用户'}。请先移除原归属。`;
      stateName='error';
    } else if(ownership.owner===username){
      hint.textContent=`${configName} 已归属当前用户；保存将更新这条授权的权限。`;
    } else {
      hint.textContent=`${configName} 当前未分配，可以独占分配给 ${username || '所选用户'}。`;
    }
  }
  hint.dataset.state=stateName;
  const saveButton=$('saveAlasBinding');
  if(saveButton && !saveButton.classList.contains('busy')) saveButton.disabled=blocked || !username || !configName;
}
function requireAlasSuccess(result, fallback='ALAS 操作失败'){
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
  if(!config) return show('请先从配置库选择配置');
  const ownership=configOwnership(config);
  invalidateAlasConfigRead(false);
  alasConfigDrawerTarget=config;
  $('configSource').value=config;
  $('configTarget').value=config;
  $('alasConfigDrawerContext').textContent=`正在编辑 ${config}`;
  $('alasConfigDrawerImpact').textContent=ownership.conflict ? `保存会直接写回 ${config}；当前存在历史归属冲突，请随后清理 ${ownership.owners.join('、')} 的多余关系。` : ownership.owner ? `保存会直接写回 ${config}，并影响归属用户 ${ownership.owner}。` : `保存会直接写回 ${config}。该配置当前未分配给用户。`;
  $('configEditor').value='';
  openEditorDrawer('alasConfig',trigger);
  withBusy($('loadConfig'),loadConfig,'读取中').catch(error=>{ if(!isAbortError(error)) show(error.message); });
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
  state.overview=data;
  if(!loadedResources.has('users')) state.users=data.users || [];
  if(!loadedResources.has('devices')) state.devices=data.devices || [];
  if(!loadedResources.has('alas')) state.alas={settings:{}, status:data.alas || {}};
  renderOverview();
  if(loadedResources.has('devices')) renderDevices();
  if(loadedResources.has('users')) renderUsers();
  if(loadedResources.has('permissions')) renderPermissions();
  if(loadedResources.has('alas')) renderAlas();
}
function applyDevices(data){ state.devices=data.devices || []; renderDevices(); renderOverview(); if(loadedResources.has('permissions')) renderPermissions(); }
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
  renderOverview();
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
  renderOverview();
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
  renderOverview();
  return true;
}
function applyAlasCatalog(data){
  state.alas={...(state.alas || {}),catalog:{...(data || {}),loaded:true},catalog_error:''};
  renderAlas();
}
function applyLogs(data){
  state.logs=data.logs || [];
  renderAuditLogs();
  setLogLoadState('logs','ready',logReadyMessage('审计日志', state.logs.length));
}
function applyRuntimeLogs(data){
  state.runtimeLogs=data.logs || [];
  renderRuntimeLogs();
  setLogLoadState('runtimeLogs','ready',logReadyMessage('运行日志', state.runtimeLogs.length));
}
function loadOverview(options={}){ return requestResource('overview', signal=>api('/api/admin/overview',{signal}), applyOverview, options); }
function loadDevices(options={}){ return requestResource('devices', signal=>api('/api/admin/devices',{signal}), applyDevices, options); }
function loadDeviceStatuses(options={}){ return requestResource('deviceStatuses', signal=>api('/api/admin/adb/status',{signal}), applyDeviceStatuses, options); }
function deviceStatusPollingAllowed(){ return activeTab==='devices' && !document.hidden; }
function stopDeviceStatusPolling(abort=false){
  clearTimeout(deviceStatusPollTimer);
  deviceStatusPollTimer=null;
  if(abort){ deviceStatusPollGeneration+=1; markResourceStale('deviceStatuses'); }
}
function scheduleDeviceStatusPoll(){
  stopDeviceStatusPolling(false);
  if(!deviceStatusPollingAllowed()) return;
  deviceStatusPollTimer=setTimeout(refreshDeviceStatuses,DEVICE_STATUS_POLL_INTERVAL);
}
async function refreshDeviceStatuses(){
  if(!deviceStatusPollingAllowed()) return stopDeviceStatusPolling(true);
  const generation=deviceStatusPollGeneration;
  try{ await loadDeviceStatuses({force:true}); }
  catch(error){ if(!isAbortError(error)) console.warn('设备心跳状态刷新失败',error); }
  finally{ if(generation===deviceStatusPollGeneration) scheduleDeviceStatusPoll(); }
}
function syncDeviceStatusPolling(){
  stopDeviceStatusPolling(true);
  if(deviceStatusPollingAllowed()) refreshDeviceStatuses();
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
      permissionsLoadError=String(error && error.message || '未知错误');
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
    catch(error){ reportRequestError(error,'ALAS 归属刷新失败'); }
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
    details_error:results[1].status==='rejected' ? String(results[1].reason && results[1].reason.message || 'ALAS 数据读取失败') : ''
  };
  loadedResources.add('alas');
  renderAlas();
  return state.alas;
}
function loadAlasCatalog(options={}){
  return requestResource('alasCatalog',signal=>api('/api/admin/alas/configs',{signal}),applyAlasCatalog,options).catch(error=>{
    if(!isAbortError(error)){
      state.alas={...(state.alas || {}),catalog_error:String(error && error.message || '配置库读取失败')};
      renderAlas();
    }
    throw error;
  });
}
function loadLogs(options={}){
  setLogLoadState('logs','loading','正在加载审计日志…');
  return requestResource('logs', signal=>api('/api/admin/logs',{signal}), applyLogs, options).catch(error=>{
    if(!isAbortError(error)) setLogLoadState('logs','error',`审计日志加载失败：${error.message || '未知错误'}`);
    throw error;
  });
}
function loadRuntimeLogs(options={}){
  setLogLoadState('runtimeLogs','loading','正在加载运行日志…');
  return requestResource('runtimeLogs', signal=>api('/api/admin/runtime-logs?lines=400',{signal}), applyRuntimeLogs, options).catch(error=>{
    if(!isAbortError(error)) setLogLoadState('runtimeLogs','error',`运行日志加载失败：${error.message || '未知错误'}`);
    throw error;
  });
}
const RESOURCE_LOADERS = {overview:loadOverview, devices:loadDevices, users:loadUsers, permissions:loadPermissions, video:loadVideo, alas:loadAlas, logs:loadLogs, runtimeLogs:loadRuntimeLogs};
async function loadTab(tabId, options={}){
  const names=TAB_RESOURCES[tabId] || TAB_RESOURCES.overview;
  const force=!!options.force;
  const results=await Promise.allSettled(names.map(name=>loadIfNeeded(name, RESOURCE_LOADERS[name], {force})));
  results.forEach((result, index)=>{ if(result.status==='rejected') reportRequestError(result.reason, `${names[index]} 加载失败`); });
  return results;
}
async function loadAll(){
  const visible=new Set(TAB_RESOURCES[activeTab] || []);
  const names=Object.keys(RESOURCE_LOADERS).filter(name=>name==='overview' || loadedResources.has(name) || visible.has(name));
  const results=await Promise.allSettled(names.map(name=>RESOURCE_LOADERS[name]({force:true})));
  results.forEach((result, index)=>{ if(result.status==='rejected') reportRequestError(result.reason, `${names[index]} 加载失败`); });
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
  results.forEach((result, index)=>{ if(result.status==='rejected') reportRequestError(result.reason, `${refresh[index]} 刷新失败`); });
}
async function saveUser(){
  await api('/api/admin/users',{method:'PUT', body:{username:$('newUsername').value, password:$('newPassword').value, role:$('newRole').value}});
  show('用户已保存');
  closeEditorDrawer('user');
  await refreshDomains('overview','users','permissions','alas');
}
async function deleteUser(username){
  const confirmed=await confirmDanger({
    title:'删除用户',
    message:`删除用户 ${username} 后，其设备权限与 ALAS 配置绑定也将被移除。此操作无法撤销。`,
    confirmText:'删除用户'
  });
  if(!confirmed) return;
  await api(`/api/admin/users/${encodeURIComponent(username)}`,{method:'DELETE'});
  show('用户已删除');
  await refreshDomains('overview','users','permissions','alas');
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
    show(`设备已保存 · ${deviceAdbMeta(saved).label}`);
    closeEditorDrawer('device');
    clearDeviceForm();
    await refreshDomains('permissions');
  } finally {
    scheduleDeviceStatusPoll();
  }
}
async function deleteDevice(id){
  const confirmed=await confirmDanger({
    title:'删除设备',
    message:`删除设备 ${id} 后，相关用户权限也会被移除。请先确认该设备不再使用。`,
    confirmText:'删除设备'
  });
  if(!confirmed) return;
  stopDeviceStatusPolling(true);
  try{
    await api(`/api/admin/devices/${encodeURIComponent(id)}`,{method:'DELETE'});
    show('设备已删除');
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
    applyDeviceAdbResult(id,{state:'unknown',ok:false,detail:error.message || '检测失败'});
    throw error;
  } finally {
    deviceProbeRequests.delete(id);
    const device=state.devices.find(item=>getDeviceId(item)===id);
    if(device) updateDeviceCardHeartbeat(device);
    renderOverview();
    scheduleDeviceStatusPoll();
  }
}
async function savePermission(){
  const user=selectedPermissionUser();
  if(!user) throw new Error('请先选择用户');
  if(user.role==='admin') throw new Error('管理员权限由角色统一授予，无需保存');
  if(!permissionsAreReady()) throw new Error('设备权限尚未加载完成，请稍后重试');
  const changes=permissionChangesForUser(user.username);
  if(!changes.length) return show('没有待保存的权限修改');
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
  if(refreshError) throw new Error(`权限已提交，但权威状态刷新失败：${refreshError.message || '未知错误'}`);
  const failed=results.filter(result=>result.status==='rejected');
  if(failed.length) throw new Error(`${failed.length} 台设备权限保存失败，已重新读取服务器状态`);
  show(`已保存 ${changes.length} 台设备的权限`);
}
function collectVideoPresets(){ const presets={}; document.querySelectorAll('[data-preset-profile]').forEach(input=>{ const name=input.dataset.presetProfile; const field=input.dataset.presetField; let value=input.value; if(field==='video_bit_rate') value=bitrateMbpsToBps(value); else if(field==='max_size' && value===CUSTOM_OUTPUT_SIZE){ const control=input.closest('.preset-size-control'); const width=control.querySelector('[data-custom-width]'); const height=control.querySelector('[data-custom-height]'); if(!width.checkValidity() || !height.checkValidity()){ const invalid=!width.checkValidity() ? width : height; invalid.reportValidity(); throw new Error('自定义宽度或高度超出允许范围'); } value=Math.max(Number(width.value),Number(height.value)); } else value=Number(value); presets[name]=presets[name] || {}; presets[name][field]=value; }); return presets; }
function addCustomProfile(){ const id=($('customProfileId').value || '').trim(); if(!/^[a-zA-Z][a-zA-Z0-9_-]{1,31}$/.test(id)) return show('档位 ID 只能使用字母、数字、下划线或短横线，且以字母开头'); if(NORMAL_PROFILE_NAMES.concat(['custom','auto']).includes(id) || id.startsWith('alas_')) return show('这个 ID 是保留名称'); const width=$('customProfileWidth'); const height=$('customProfileHeight'); if($('customProfileSizeSelect').value===CUSTOM_OUTPUT_SIZE && (!width.checkValidity() || !height.checkValidity())){ const invalid=!width.checkValidity() ? width : height; invalid.reportValidity(); return show('自定义宽度或高度超出允许范围'); } customProfiles[id]={label:($('customProfileLabel').value || id).trim(), video_bit_rate:bitrateMbpsToBps($('customProfileBitrate').value || 0.9), max_size:customProfileSizeValue(), max_fps:Number($('customProfileFps').value || 24)}; renderCustomProfiles(); show('自定义档位已加入，确认后点保存'); }
async function saveVideo(){ const presets=collectVideoPresets(); const profile=$('videoProfile').value || 'balanced'; const selected=presets[profile] || customProfiles[profile] || presets.balanced || {}; const payload={profile, adaptive:false, scrcpy_stream_mode:$('videoStreamMode').value || 'raw', scrcpy_enabled_stream_modes:collectEnabledStreamModes(), auto_stop_minutes:Number($('videoAutoStop').value || 15), video_bit_rate:selected.video_bit_rate, max_size:selected.max_size, max_fps:selected.max_fps, presets, custom_profiles:customProfiles}; markResourceStale('video'); const result=await api('/api/admin/video',{method:'PUT', body:payload}); applyVideo(result); loadedResources.add('video'); show('画质设置已保存'); }
async function saveAlas(){ await api('/api/admin/alas',{method:'PUT', body:{enabled:true, base_url:$('alasBaseUrl').value, api_token:$('alasToken').value}}); closeEditorDrawer('alasConnection'); show('ALAS 连接设置已保存'); await refreshDomains('overview','alas'); }
async function reloadAlas(){
  const requests=[loadAlas({force:true})];
  if(activeAlasView==='configs') requests.push(loadAlasCatalog({force:true}));
  const results=await Promise.allSettled(requests);
  if(results.every(result=>result.status==='rejected')) throw results[0].reason;
  return results;
}
async function toggleAlas(){
  const config=selectedAlasConfig();
  if(!config) return show('请先从配置库选择配置');
  const sequence=++alasToggleSequence;
  const result=requireAlasSuccess(await api('/api/admin/alas/toggle',{method:'POST', body:{config_name:config}}),'ALAS 启停失败');
  if(sequence!==alasToggleSequence) return;
  const current=selectedAlasConfig();
  show(configKey(current)===configKey(config) ? `${config} 状态已更新` : `${config} 操作已完成；当前查看 ${current || '其他配置'}`);
  const statusRefresh=configKey(current)===configKey(config) ? loadAlasStatusForConfig(config) : Promise.resolve();
  await Promise.allSettled([statusRefresh,refreshDomains('overview')]);
  return result;
}
async function loadConfig(){
  const config=alasConfigDrawerTarget;
  if(!config) throw new Error('配置编辑器没有固定目标，请重新打开');
  const request=beginAlasConfigRead(config);
  try{
    const data=requireAlasSuccess(await api(`/api/admin/alas/config?config=${encodeURIComponent(config)}`,{signal:request.controller.signal}),'配置读取失败');
    if(!alasConfigReadIsCurrent(request)) return;
    $('configEditor').value=JSON.stringify(data.data || {}, null, 2);
    show(`${config} 配置已读取`);
    return data;
  } finally {
    if(alasConfigReadController===request.controller) alasConfigReadController=null;
  }
}
async function saveConfig(){
  const config=alasConfigDrawerTarget;
  if(!config) return show('配置编辑器没有固定目标，请重新打开');
  let data;
  try{ data=JSON.parse($('configEditor').value); }
  catch(e){ show('JSON 格式错误，请检查后再保存'); return; }
  const request=beginAlasConfigRead(config);
  try{
    const result=requireAlasSuccess(await api('/api/admin/alas/config',{method:'PUT', body:{source:config, target:config, data}, signal:request.controller.signal}),'配置保存失败');
    if(!alasConfigReadIsCurrent(request)) return;
    closeEditorDrawer('alasConfig');
    show(`${config} 配置已保存`);
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
  if(!username) return show('请选择用户');
  if(!configName) return show('请选择配置或输入配置名称');
  const otherOwners=configOwnership(configName).owners.filter(owner=>owner!==username);
  if(otherOwners.length) return show(`${configName} 已归属 ${otherOwners.join('、')}；请先移除原归属后再分配`);
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
  show(`已保存 ${username} → ${configName} 的归属与权限`);
  await refreshDomains('overview');
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
  $('userPagePrevious').onclick=()=>{ userAccountPage=Math.max(1,userAccountPage-1); renderUsers(); };
  $('userPageNext').onclick=()=>{ userAccountPage+=1; renderUsers(); };
  $('permissionUserSearch').oninput=()=>{ renderPermissionUserList(); renderPermissionDetail(); };
  $('permissionUserList').onkeydown=handlePermissionUserListKeydown;
  $('permUser').onchange=()=>selectPermissionUser($('permUser').value);
  $('permissionDeviceSearch').oninput=()=>renderPermissionDetail();
  $('permissionDeviceFilter').onchange=()=>renderPermissionDetail();
  $('permissionRetry').onclick=()=>withBusy($('permissionRetry'),()=>loadPermissions({force:true}),'加载中').catch(error=>reportRequestError(error,'设备权限加载失败'));
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
    show('请先从配置库选择配置');
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
const savedInitialTab=$(localStorage.getItem(ADMIN_TAB_KEY)) ? localStorage.getItem(ADMIN_TAB_KEY) : 'overview';
activateTab('overview', false);
bindAction('reloadAll', loadAll, '刷新中');
bindAction('saveUser', saveUser, '保存中');
bindAction('saveDevice', saveDevice, '保存中');
$('clearDeviceForm').onclick=()=>clearDeviceForm();
bindAction('reloadRuntimeLogs', ()=>loadRuntimeLogs({force:true}), '刷新中');
bindAction('reloadAuditLogs', ()=>loadLogs({force:true}), '刷新中');
$('savePermission').onclick=()=>withBusy($('savePermission'),savePermission,'保存中').catch(error=>show(error.message)).finally(()=>renderPermissionDetail());
bindAction('saveVideo', saveVideo, '保存中');
bindAction('addCustomProfile', async()=>addCustomProfile(), '添加中');
bindAction('saveAlas', saveAlas, '保存中');
bindAction('reloadAlas', reloadAlas, '刷新中');
bindAction('toggleAlas', toggleAlas, '执行中');
bindAction('loadConfig', loadConfig, '读取中');
bindAction('saveConfig', saveConfig, '保存中');
bindAction('saveAlasBinding', saveAlasBinding, '保存中');
loadOverview()
  .then(()=>{ if(savedInitialTab !== 'overview') activateTab(savedInitialTab, false); })
  .catch(error=>reportRequestError(error));
