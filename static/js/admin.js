const bootstrap = JSON.parse(document.getElementById("scrcpygate-bootstrap").textContent);
const csrfToken = bootstrap.csrf_token;
const currentUsername = bootstrap.user.username;
const state = { overview:null, users:[], devices:[], permissions:[], logs:[], runtimeLogs:[], video:{}, alas:null };
const profileLabels = {smooth:'\u6d41\u7545', balanced:'\u7a33\u5b9a', sharp:'\u9ad8\u6e05', low_latency:'\u4f4e\u5ef6\u8fdf'};
const profileHints = {smooth:'\u6700\u4f4e\u4e0a\u884c\u8d1f\u8f7d', balanced:'\u65e5\u5e38\u9ed8\u8ba4', sharp:'\u753b\u9762\u66f4\u6e05\u6670', low_latency:'\u64cd\u4f5c\u4f18\u5148'};
const customProfiles = {};
const $ = (id)=>document.getElementById(id);
const ADMIN_TAB_KEY = 'scrcpygate:admin:tab';
const STATUS_LABELS = {running:'运行中', stopped:'已停止', idle:'空闲', error:'异常', disabled:'未启用', disconnected:'未连接', unknown:'未知', unbound:'未绑定配置'};
const resourceRequests = new Map();
const resourceSequences = new Map();
const loadedResources = new Set();
const TAB_RESOURCES = {
  overview:['overview'],
  devices:['devices'],
  users:['users','permissions'],
  video:['video'],
  alas:['alas'],
  logs:['logs','runtimeLogs']
};
let activeTab = 'overview';
const adminNavMedia = window.matchMedia('(max-width: 920px)');
let adminNavReturnFocus = null;
let pendingConfirmation = null;

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
  ['device','user'].filter(other=>other!==name).forEach(closeEditorDrawer);
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
function ts(value){ return value ? new Date(value * 1000).toLocaleString() : ''; }
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
    const title=document.createElement('h3');
    title.textContent=device.name || id;
    const address=document.createElement('div');
    address.className='device-address';
    address.textContent=device.address || id;
    const statuses=document.createElement('div');
    statuses.className='chips';
    statuses.appendChild(chip(device.enabled?'启用':'禁用', device.enabled?'ok':'warn'));
    statuses.appendChild(chip(device.session&&device.session.running?'投屏中':'未投屏', device.session&&device.session.running?'ok':''));
    if(device.session&&device.session.control_lock) statuses.appendChild(chip(`控制: ${device.session.control_lock.username || '已占用'}`, 'warn'));
    const actions=document.createElement('div');
    actions.className='actions';
    actions.append(
      btn('编辑','',()=>editDevice(device)),
      btn('测试 ADB','',()=>testDevice(id)),
      btn(device.session&&device.session.running?'停止投屏':'开始投屏', device.session&&device.session.running?'warn':'primary', ()=>device.session&&device.session.running?stopDevice(id):startDevice(id)),
      btn('删除','danger',()=>deleteDevice(id))
    );
    card.append(title,address,statuses,actions);
    box.appendChild(card);
  });
}
function presetInput(profile, field, value){ const input=document.createElement('input'); input.type='number'; input.value=value == null ? '' : value; input.dataset.presetProfile=profile; input.dataset.presetField=field; if(field==='video_bit_rate'){ input.min='100000'; input.step='100000'; } else if(field==='max_size'){ input.min='480'; input.step='1'; } else { input.min='1'; input.max='60'; input.step='1'; } return input; }
function setPresetValue(profile, field, value){ const input=document.querySelector(`[data-preset-profile="${profile}"][data-preset-field="${field}"]`); if(input) input.value=value; }
function renderBandwidthActions(recommendations){ const box=$('bandwidthPresetActions'); if(!box) return; clear(box); Object.keys(recommendations).sort((a,b)=>parseInt(a)-parseInt(b)).forEach(name=>box.appendChild(btn(name.toUpperCase(), 'warn', ()=>applyBandwidthRecommendation(name)))); }
function applyBandwidthRecommendation(name){ const rec=((state.video || {}).bandwidth_recommendations || {})[name]; if(!rec) return; Object.entries(rec).forEach(([profile, values])=>['video_bit_rate','max_size','max_fps'].forEach(field=>setPresetValue(profile, field, values[field]))); show(`${name.toUpperCase()} \u63a8\u8350\u503c\u5df2\u586b\u5165\uff0c\u786e\u8ba4\u540e\u70b9\u4fdd\u5b58`); }
async function removeCustomProfile(id){
  const confirmed=await confirmDanger({
    title:'删除专属画质',
    message:`将专属画质 ${id} 从当前设置中移除。保存画质设置后生效。`,
    confirmText:'删除画质'
  });
  if(!confirmed) return;
  delete customProfiles[id];
  renderCustomProfiles();
  show('专属设定已移除，确认后点保存');
}
function renderCustomProfiles(){
  const rows=$('customProfileRows');
  if(!rows) return;
  clear(rows);
  Object.keys(customProfiles).sort().forEach(id=>{
    const profile=customProfiles[id] || {};
    const tr=document.createElement('tr');
    tr.append(td(id), td(profile.label || id), td(profile.video_bit_rate), td(profile.max_size), td(profile.max_fps));
    const actions=document.createElement('td');
    actions.className='actions';
    actions.append(
      btn('编辑','',()=>{
        $('customProfileId').value=id;
        $('customProfileLabel').value=profile.label || id;
        $('customProfileBitrate').value=profile.video_bit_rate || 900000;
        $('customProfileSize').value=profile.max_size || 480;
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
    ['smooth','balanced','sharp','low_latency'].concat(Object.keys(customProfiles).sort()).forEach(name=>{
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
    ['smooth','balanced','sharp','low_latency'].forEach(name=>{
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
  $('deviceDrawerContext').textContent=device ? `正在编辑 ${device.name || id}（${id}）` : '填写设备标识、名称与 ADB 地址';
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
function renderUsers(){
  const rows=$('userRows');
  clear(rows);
  state.users.forEach(user=>{
    const tr=document.createElement('tr');
    tr.append(td(user.username), td(user.role==='admin'?'管理员':'普通用户'), td(user.created_at));
    const actions=document.createElement('td');
    actions.className='actions';
    actions.append(btn('编辑','',()=>editUser(user)));
    if(user.username !== currentUsername) actions.append(btn('删除','danger',()=>deleteUser(user.username)));
    tr.appendChild(actions);
    rows.appendChild(tr);
  });
  applyTableLabels(rows);
}
function fillSelect(sel, values, label){ clear(sel); values.forEach(v=>{ const o=document.createElement('option'); o.value=v.value; o.textContent=label(v); sel.appendChild(o); }); }
function renderPermissions(){
  fillSelect($('permUser'), state.users.map(user=>({value:user.username, text:user.username})), value=>value.text);
  fillSelect($('permDevice'), state.devices.map(device=>({value:getDeviceId(device), text:device.name || getDeviceId(device)})), value=>value.text);
  const rows=$('permissionRows');
  clear(rows);
  state.permissions.forEach(permission=>{
    const tr=document.createElement('tr');
    tr.append(td(permission.username), td(permission.device_id), td(permission.can_view?'允许':'拒绝'), td(permission.can_control?'允许':'拒绝'));
    rows.appendChild(tr);
  });
  applyTableLabels(rows);
}
function alasBindings(){ return (state.alas && state.alas.bindings) || []; }
function uniqueAlasConfigs(){ const source=(state.alas && state.alas.bound_configs) || alasBindings().map(b=>b.config_name); const seen=new Set(); const configs=[]; source.forEach(raw=>{ const name=String(raw || '').trim(); const key=name.toLowerCase(); if(name && !seen.has(key)){ seen.add(key); configs.push(name); } }); return configs; }
function fillConfigSelect(sel, configs, selected){ clear(sel); if(!configs.length){ const o=document.createElement('option'); o.value=''; o.textContent='暂无绑定配置'; sel.appendChild(o); sel.disabled=true; return ''; } sel.disabled=false; configs.forEach(name=>{ const o=document.createElement('option'); o.value=name; o.textContent=name; sel.appendChild(o); }); const value=configs.includes(selected) ? selected : configs[0]; sel.value=value; return value; }
function selectedAlasConfig(){ return (($('alasOperateConfig') && $('alasOperateConfig').value) || ($('configSource') && $('configSource').value) || uniqueAlasConfigs()[0] || '').trim(); }
function syncAlasConfigSelectors(configName){ const config=(configName || selectedAlasConfig()).trim(); if($('alasOperateConfig') && config) $('alasOperateConfig').value=config; if($('configSource') && config) $('configSource').value=config; if($('configTarget')) $('configTarget').value=config; return config; }
function currentAlasBinding(username){ return alasBindings().find(b=>b.username===username) || null; }
function fillAlasBindingForm(binding){ const b=binding || currentAlasBinding($('alasBindUser').value) || {}; $('alasBindUser').value=b.username || $('alasBindUser').value || ''; $('alasBindConfig').value=b.config_name || ''; $('alasBindRun').value=b.can_run ? 'true' : 'false'; $('alasBindEnabled').value=b.config_name ? 'true' : 'false'; }
async function removeAlasBinding(binding){
  const confirmed=await confirmDanger({
    title:'取消 ALAS 配置绑定',
    message:`将取消用户 ${binding.username} 与配置 ${binding.config_name} 的绑定，用户将无法继续操作该配置。`,
    confirmText:'取消绑定'
  });
  if(!confirmed) return;
  await api('/api/admin/alas/permissions',{method:'PUT', body:{username:binding.username, enabled:false}});
  show('ALAS 绑定已取消');
  await refreshDomains('overview','alas');
}
function renderAlas(){
  const alas=state.alas || {};
  const settings=alas.settings || {};
  const status=alas.status || {};
  const configs=uniqueAlasConfigs();
  const selected=fillConfigSelect($('alasOperateConfig'), configs, status.config || selectedAlasConfig());
  fillConfigSelect($('configSource'), configs, selected);
  syncAlasConfigSelectors(selected);
  $('alasBaseUrl').value=settings.base_url || '';
  $('alasToken').value='';

  const statusBox=$('alasStatus');
  clear(statusBox);
  statusBox.appendChild(chip(settings.enabled?'启用':'未启用', settings.enabled?'ok':'warn'));
  statusBox.appendChild(chip(settings.token_set?'Token 已配置':'Token 未配置', settings.token_set?'ok':'warn'));
  if(!configs.length){
    statusBox.appendChild(chip('未绑定配置','warn'));
  } else {
    statusBox.appendChild(chip(`配置 ${selected}`));
    statusBox.appendChild(chip(statusLabel(status.status), status.status==='running'?'ok':status.status==='error'?'warn':''));
  }
  if(status.error) statusBox.appendChild(chip(status.error, 'danger'));
  $('toggleAlas').textContent=status.status==='error'?'重启 ALAS':status.status==='running'?'停止 ALAS':'启动 ALAS';
  $('toggleAlas').disabled=!configs.length;
  $('loadConfig').disabled=!configs.length;
  $('saveConfig').disabled=!configs.length;

  fillSelect($('alasBindUser'), state.users.map(user=>({value:user.username, text:user.username})), value=>value.text);
  if(!$('alasBindUser').value && state.users[0]) $('alasBindUser').value=state.users[0].username;
  fillAlasBindingForm(currentAlasBinding($('alasBindUser').value));
  const rows=$('alasBindRows');
  clear(rows);
  alasBindings().forEach(binding=>{
    const tr=document.createElement('tr');
    tr.append(td(binding.username), td(binding.role==='admin'?'管理员':'普通用户'), td(binding.config_name || '未绑定'), td(binding.can_run?'允许':'拒绝'));
    const actions=document.createElement('td');
    actions.className='actions';
    actions.append(btn('编辑','',()=>{
      fillAlasBindingForm(binding);
      if(binding.config_name) syncAlasConfigSelectors(binding.config_name);
    }));
    if(binding.config_name) actions.append(btn('取消绑定','danger',()=>removeAlasBinding(binding)));
    tr.appendChild(actions);
    rows.appendChild(tr);
  });
  applyTableLabels(rows);
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
function applyPermissions(data){
  state.permissions=data.permissions || [];
  if(data.users && !loadedResources.has('users')) state.users=data.users;
  if(data.devices && !loadedResources.has('devices')) state.devices=data.devices;
  renderPermissions();
  if(loadedResources.has('users')) renderUsers();
  if(loadedResources.has('devices')) renderDevices();
  renderOverview();
}
function applyVideo(data){ state.video=data; renderVideo(); }
function applyAlas(data){ state.alas=data; renderAlas(); renderOverview(); }
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
function loadUsers(options={}){ return requestResource('users', signal=>api('/api/admin/users',{signal}), applyUsers, options); }
function loadPermissions(options={}){ return requestResource('permissions', signal=>api('/api/admin/permissions',{signal}), applyPermissions, options); }
function loadVideo(options={}){ return requestResource('video', signal=>api('/api/admin/video',{signal}), applyVideo, options); }
function loadAlas(options={}){
  const config=String(options.config === undefined ? selectedAlasConfig() : options.config || '').trim();
  const url='/api/admin/alas' + (config ? `?config=${encodeURIComponent(config)}` : '');
  return requestResource('alas', signal=>api(url,{signal}), applyAlas, options);
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
  const id=$('deviceId').value.trim();
  await api('/api/admin/devices',{method:'PUT', body:{device_id:id, name:$('deviceName').value.trim() || id, address:$('deviceAddress').value.trim() || id, enabled:$('deviceEnabled').value==='true'}});
  show('设备已保存');
  closeEditorDrawer('device');
  clearDeviceForm();
  await refreshDomains('overview','devices','permissions');
}
async function deleteDevice(id){
  const confirmed=await confirmDanger({
    title:'删除设备',
    message:`删除设备 ${id} 后，相关用户权限也会被移除。请先确认该设备不再使用。`,
    confirmText:'删除设备'
  });
  if(!confirmed) return;
  await api(`/api/admin/devices/${encodeURIComponent(id)}`,{method:'DELETE'});
  show('设备已删除');
  await refreshDomains('overview','devices','permissions');
}
async function testDevice(id){ const result=await api(`/api/admin/devices/${encodeURIComponent(id)}/adb/test`,{method:'POST'}); show(`ADB ${id}: ${result.state}${result.detail ? ' - ' + result.detail : ''}`, 5200); }
async function startDevice(id){ await api(`/api/devices/${encodeURIComponent(id)}/mirror/start`,{method:'POST'}); show('投屏已启动'); await refreshDomains('overview','devices','permissions'); }
async function stopDevice(id){ await api(`/api/devices/${encodeURIComponent(id)}/mirror/stop`,{method:'POST'}); show('投屏已停止'); await refreshDomains('overview','devices','permissions'); }
async function savePermission(){ await api('/api/admin/permissions',{method:'PUT', body:{username:$('permUser').value, device_id:$('permDevice').value, can_view:$('permView').value==='true', can_control:$('permControl').value==='true'}}); show('权限已保存'); await refreshDomains('permissions'); }
function collectVideoPresets(){ const presets={}; document.querySelectorAll('[data-preset-profile]').forEach(input=>{ const name=input.dataset.presetProfile; const field=input.dataset.presetField; presets[name]=presets[name] || {}; presets[name][field]=Number(input.value); }); return presets; }
function addCustomProfile(){ const id=($('customProfileId').value || '').trim(); if(!/^[a-zA-Z][a-zA-Z0-9_-]{1,31}$/.test(id)) return show('档位 ID 只能使用字母、数字、下划线或短横线，且以字母开头'); if(['smooth','balanced','sharp','low_latency','custom','auto'].includes(id)) return show('这个 ID 是保留名称'); customProfiles[id]={label:($('customProfileLabel').value || id).trim(), video_bit_rate:Number($('customProfileBitrate').value || 900000), max_size:Number($('customProfileSize').value || 480), max_fps:Number($('customProfileFps').value || 24)}; renderCustomProfiles(); show('专属设定已加入，确认后点保存'); }
async function saveVideo(){ const presets=collectVideoPresets(); const profile=$('videoProfile').value || 'balanced'; const selected=presets[profile] || customProfiles[profile] || presets.balanced || {}; const payload={profile, adaptive:false, scrcpy_stream_mode:$('videoStreamMode').value || 'raw', scrcpy_enabled_stream_modes:collectEnabledStreamModes(), auto_stop_minutes:Number($('videoAutoStop').value || 15), video_bit_rate:selected.video_bit_rate, max_size:selected.max_size, max_fps:selected.max_fps, presets, custom_profiles:customProfiles}; markResourceStale('video'); const result=await api('/api/admin/video',{method:'PUT', body:payload}); applyVideo(result); loadedResources.add('video'); show('画质设置已保存'); }
async function saveAlas(){ await api('/api/admin/alas',{method:'PUT', body:{enabled:true, base_url:$('alasBaseUrl').value, api_token:$('alasToken').value}}); show('ALAS 设置已保存'); await refreshDomains('overview','alas'); }
async function reloadAlas(){ await loadAlas({force:true}); }
async function toggleAlas(){ const config=selectedAlasConfig(); if(!config) return show('请先为用户绑定 ALAS 配置'); await api('/api/admin/alas/toggle',{method:'POST', body:{config_name:config}}); show('ALAS 状态已更新'); await refreshDomains('overview','alas'); }
async function loadConfig(){ const config=selectedAlasConfig(); if(!config) return show('请先选择绑定配置'); const data=await api(`/api/admin/alas/config?config=${encodeURIComponent(config)}`); syncAlasConfigSelectors(data.config || config); $('configEditor').value=JSON.stringify(data.data || {}, null, 2); show('绑定配置已读取'); }
async function saveConfig(){ const config=selectedAlasConfig(); if(!config) return show('请先选择绑定配置'); let data; try{ data=JSON.parse($('configEditor').value); }catch(e){ show('JSON 格式错误'); return; } await api('/api/admin/alas/config',{method:'PUT', body:{source:config, target:config, data}}); show('配置已保存'); await refreshDomains('alas'); }
async function saveAlasBinding(){ await api('/api/admin/alas/permissions',{method:'PUT', body:{username:$('alasBindUser').value, config_name:$('alasBindConfig').value, enabled:$('alasBindEnabled').value==='true', can_run:$('alasBindRun').value==='true', can_edit:false}}); show('ALAS 绑定已保存'); await refreshDomains('overview','alas'); }
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
  ['device','user'].forEach(name=>{
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
initializeAdminNavigation();
initializeEditorDrawers();
initializeConfirmDialog();
initializeTabs();
const savedInitialTab=$(localStorage.getItem(ADMIN_TAB_KEY)) ? localStorage.getItem(ADMIN_TAB_KEY) : 'overview';
activateTab('overview', false);
bindAction('reloadAll', loadAll, '刷新中');
bindAction('saveUser', saveUser, '保存中');
bindAction('saveDevice', saveDevice, '保存中');
$('clearDeviceForm').onclick=()=>clearDeviceForm();
bindAction('reloadRuntimeLogs', ()=>loadRuntimeLogs({force:true}), '刷新中');
bindAction('reloadAuditLogs', ()=>loadLogs({force:true}), '刷新中');
bindAction('savePermission', savePermission, '保存中');
bindAction('saveVideo', saveVideo, '保存中');
bindAction('addCustomProfile', async()=>addCustomProfile(), '添加中');
bindAction('saveAlas', saveAlas, '保存中');
bindAction('reloadAlas', reloadAlas, '刷新中');
bindAction('toggleAlas', toggleAlas, '执行中');
bindAction('loadConfig', loadConfig, '读取中');
bindAction('saveConfig', saveConfig, '保存中');
bindAction('saveAlasBinding', saveAlasBinding, '保存中');
$('alasBindUser').onchange=()=>fillAlasBindingForm();
$('alasOperateConfig').onchange=()=>{ syncAlasConfigSelectors($('alasOperateConfig').value); reloadAlas().catch(e=>show(e.message)); };
$('configSource').onchange=()=>syncAlasConfigSelectors($('configSource').value);
loadOverview()
  .then(()=>{ if(savedInitialTab !== 'overview') activateTab(savedInitialTab, false); })
  .catch(error=>reportRequestError(error));
