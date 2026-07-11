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
function renderOverview(){ $('summaryMirror').textContent=`投屏 ${activeSessions()}`; $('summaryMirror').className='chip ' + (activeSessions()?'ok':''); const alasStatus=(state.alas && state.alas.status && state.alas.status.status) || (state.overview && state.overview.alas && state.overview.alas.status) || 'unknown'; $('summaryAlas').textContent=`ALAS ${statusLabel(alasStatus)}`; $('summaryAlas').className='chip ' + (alasStatus==='running'?'ok':alasStatus==='error'?'warn':''); const od=$('overviewDevices'); clear(od); state.devices.forEach(d=>od.appendChild(chip(`${d.name || getDeviceId(d)}: ${d.enabled ? '启用' : '禁用'}`, d.enabled?'ok':'warn'))); if(!state.devices.length) od.appendChild(chip('暂无设备','warn')); const om=$('overviewMirror'); clear(om); state.devices.forEach(d=>om.appendChild(chip(`${d.name || getDeviceId(d)}: ${d.session&&d.session.running?'投屏中':'未投屏'}`, d.session&&d.session.running?'ok':''))); const oa=$('overviewAlas'); clear(oa); if(state.overview && state.overview.alas){ const a=state.overview.alas; oa.appendChild(chip(a.enabled?'已启用':'未启用', a.enabled?'ok':'warn')); oa.appendChild(chip(statusLabel(a.status), a.status==='running'?'ok':a.status==='error'?'warn':'')); if(a.config) oa.appendChild(chip(`配置 ${a.config}`)); } }
function renderDevices(){ const box=$('deviceCards'); clear(box); if(!state.devices.length){ box.appendChild(chip('暂无设备','warn')); return; } state.devices.forEach(d=>{ const id=getDeviceId(d); const card=document.createElement('div'); card.className='device-card'; const title=document.createElement('h3'); title.textContent=d.name || id; const address=document.createElement('div'); address.className='device-address'; address.textContent=d.address || id; const chips=document.createElement('div'); chips.className='chips'; chips.appendChild(chip(d.enabled?'启用':'禁用', d.enabled?'ok':'warn')); chips.appendChild(chip(d.session&&d.session.running?'投屏中':'未投屏', d.session&&d.session.running?'ok':'')); if(d.session&&d.session.control_lock) chips.appendChild(chip(`控制: ${d.session.control_lock.username}`, 'warn')); const actions=document.createElement('div'); actions.className='actions'; actions.append(btn('编辑','',()=>editDevice(d)), btn('测试 ADB','',()=>testDevice(id)), btn(d.session&&d.session.running?'停止投屏':'开始投屏', d.session&&d.session.running?'danger':'primary', ()=>d.session&&d.session.running?stopDevice(id):startDevice(id)), btn('删除','danger',()=>deleteDevice(id))); card.append(title,address,chips,actions); box.appendChild(card); }); }
function presetInput(profile, field, value){ const input=document.createElement('input'); input.type='number'; input.value=value == null ? '' : value; input.dataset.presetProfile=profile; input.dataset.presetField=field; if(field==='video_bit_rate'){ input.min='100000'; input.step='100000'; } else if(field==='max_size'){ input.min='480'; input.step='1'; } else { input.min='1'; input.max='60'; input.step='1'; } return input; }
function setPresetValue(profile, field, value){ const input=document.querySelector(`[data-preset-profile="${profile}"][data-preset-field="${field}"]`); if(input) input.value=value; }
function renderBandwidthActions(recommendations){ const box=$('bandwidthPresetActions'); if(!box) return; clear(box); Object.keys(recommendations).sort((a,b)=>parseInt(a)-parseInt(b)).forEach(name=>box.appendChild(btn(name.toUpperCase(), 'warn', ()=>applyBandwidthRecommendation(name)))); }
function applyBandwidthRecommendation(name){ const rec=((state.video || {}).bandwidth_recommendations || {})[name]; if(!rec) return; Object.entries(rec).forEach(([profile, values])=>['video_bit_rate','max_size','max_fps'].forEach(field=>setPresetValue(profile, field, values[field]))); show(`${name.toUpperCase()} \u63a8\u8350\u503c\u5df2\u586b\u5165\uff0c\u786e\u8ba4\u540e\u70b9\u4fdd\u5b58`); }
function renderCustomProfiles(){ const rows=$('customProfileRows'); if(!rows) return; clear(rows); Object.keys(customProfiles).sort().forEach(id=>{ const p=customProfiles[id] || {}; const tr=document.createElement('tr'); tr.append(td(id), td(p.label || id), td(p.video_bit_rate), td(p.max_size), td(p.max_fps)); const act=document.createElement('td'); act.className='actions'; act.append(btn('\u7f16\u8f91','',()=>{ $('customProfileId').value=id; $('customProfileLabel').value=p.label || id; $('customProfileBitrate').value=p.video_bit_rate || 900000; $('customProfileSize').value=p.max_size || 480; $('customProfileFps').value=p.max_fps || 24; }), btn('\u5220\u9664','danger',()=>{ delete customProfiles[id]; renderCustomProfiles(); show('\u4e13\u5c5e\u8bbe\u5b9a\u5df2\u79fb\u9664\uff0c\u786e\u8ba4\u540e\u70b9\u4fdd\u5b58'); })); tr.appendChild(act); rows.appendChild(tr); }); }
function streamModeLabel(mode){ return mode === 'legacy' ? 'legacy 诊断' : mode; }
function renderVideoStreamModes(data, selected){ const modes=data.stream_modes || ['raw','protocol','legacy']; const enabled=new Set(data.enabled_stream_modes || ['raw']); enabled.add('raw'); const toggles=$('streamModeToggles'); if(toggles){ clear(toggles); modes.forEach(mode=>{ const label=document.createElement('label'); label.style.margin='0'; label.style.display='inline-flex'; label.style.alignItems='center'; label.style.gap='6px'; const input=document.createElement('input'); input.type='checkbox'; input.value=mode; input.checked=enabled.has(mode); input.disabled=mode==='raw'; input.dataset.streamModeToggle='1'; input.style.width='auto'; input.style.minHeight='0'; label.append(input, document.createTextNode(streamModeLabel(mode))); toggles.appendChild(label); }); } const select=$('videoStreamMode'); if(select){ clear(select); modes.filter(mode=>enabled.has(mode)).forEach(mode=>{ const option=document.createElement('option'); option.value=mode; option.textContent=streamModeLabel(mode); select.appendChild(option); }); select.value=enabled.has(selected) ? selected : 'raw'; } }
function collectEnabledStreamModes(){ const modes=['raw']; document.querySelectorAll('[data-stream-mode-toggle]').forEach(input=>{ if(input.checked && !modes.includes(input.value)) modes.push(input.value); }); return modes; }
function renderVideo(){ const data=state.video || {}; const settings=data.settings || {}; const defaults=data.defaults || {}; const labels=data.profile_labels || {}; Object.keys(customProfiles).forEach(k=>delete customProfiles[k]); Object.assign(customProfiles, data.custom_profiles || {}); if($('videoProfile')){ clear($('videoProfile')); ['smooth','balanced','sharp','low_latency'].concat(Object.keys(customProfiles).sort()).forEach(name=>{ const o=document.createElement('option'); o.value=name; o.textContent=labels[name] || profileLabels[name] || name; $('videoProfile').appendChild(o); }); $('videoProfile').value=settings.video_profile || defaults.profile || 'balanced'; } renderVideoStreamModes(data, settings.scrcpy_stream_mode || data.stream_mode || defaults.scrcpy_stream_mode || 'raw'); if($('videoAutoStop')) $('videoAutoStop').value=settings.auto_stop_minutes || '15'; renderBandwidthActions(data.bandwidth_recommendations || {}); const rows=$('videoPresetRows'); if(rows){ clear(rows); const profiles=data.profiles || {}; ['smooth','balanced','sharp','low_latency'].forEach(name=>{ const p=profiles[name] || {}; const tr=document.createElement('tr'); tr.append(td(labels[name] || profileLabels[name] || name)); ['video_bit_rate','max_size','max_fps'].forEach(field=>{ const cell=document.createElement('td'); cell.appendChild(presetInput(name, field, p[field])); tr.appendChild(cell); }); tr.append(td(profileHints[name] || '')); rows.appendChild(tr); }); } renderCustomProfiles(); }
function editDevice(d){ $('deviceId').value=getDeviceId(d); $('deviceName').value=d.name || getDeviceId(d); $('deviceAddress').value=d.address || getDeviceId(d); $('deviceEnabled').value=d.enabled ? 'true' : 'false'; }
function clearDeviceForm(){ $('deviceId').value=''; $('deviceName').value=''; $('deviceAddress').value=''; $('deviceEnabled').value='true'; }
function renderUsers(){ const rows=$('userRows'); clear(rows); state.users.forEach(u=>{ const tr=document.createElement('tr'); tr.append(td(u.username), td(u.role==='admin'?'管理员':'普通用户'), td(u.created_at)); const act=document.createElement('td'); act.className='actions'; act.append(btn('编辑','',()=>{ $('newUsername').value=u.username; $('newPassword').value=''; $('newRole').value=u.role; })); if(u.username !== currentUsername) act.append(btn('删除','danger',()=>deleteUser(u.username))); tr.appendChild(act); rows.appendChild(tr); }); }
function fillSelect(sel, values, label){ clear(sel); values.forEach(v=>{ const o=document.createElement('option'); o.value=v.value; o.textContent=label(v); sel.appendChild(o); }); }
function renderPermissions(){ fillSelect($('permUser'), state.users.map(u=>({value:u.username, text:u.username})), v=>v.text); fillSelect($('permDevice'), state.devices.map(d=>({value:getDeviceId(d), text:d.name || getDeviceId(d)})), v=>v.text); const rows=$('permissionRows'); clear(rows); state.permissions.forEach(p=>{ const tr=document.createElement('tr'); tr.append(td(p.username), td(p.device_id), td(p.can_view?'允许':'拒绝'), td(p.can_control?'允许':'拒绝')); rows.appendChild(tr); }); }
function alasBindings(){ return (state.alas && state.alas.bindings) || []; }
function uniqueAlasConfigs(){ const source=(state.alas && state.alas.bound_configs) || alasBindings().map(b=>b.config_name); const seen=new Set(); const configs=[]; source.forEach(raw=>{ const name=String(raw || '').trim(); const key=name.toLowerCase(); if(name && !seen.has(key)){ seen.add(key); configs.push(name); } }); return configs; }
function fillConfigSelect(sel, configs, selected){ clear(sel); if(!configs.length){ const o=document.createElement('option'); o.value=''; o.textContent='暂无绑定配置'; sel.appendChild(o); sel.disabled=true; return ''; } sel.disabled=false; configs.forEach(name=>{ const o=document.createElement('option'); o.value=name; o.textContent=name; sel.appendChild(o); }); const value=configs.includes(selected) ? selected : configs[0]; sel.value=value; return value; }
function selectedAlasConfig(){ return (($('alasOperateConfig') && $('alasOperateConfig').value) || ($('configSource') && $('configSource').value) || uniqueAlasConfigs()[0] || '').trim(); }
function syncAlasConfigSelectors(configName){ const config=(configName || selectedAlasConfig()).trim(); if($('alasOperateConfig') && config) $('alasOperateConfig').value=config; if($('configSource') && config) $('configSource').value=config; if($('configTarget')) $('configTarget').value=config; return config; }
function currentAlasBinding(username){ return alasBindings().find(b=>b.username===username) || null; }
function fillAlasBindingForm(binding){ const b=binding || currentAlasBinding($('alasBindUser').value) || {}; $('alasBindUser').value=b.username || $('alasBindUser').value || ''; $('alasBindConfig').value=b.config_name || ''; $('alasBindRun').value=b.can_run ? 'true' : 'false'; $('alasBindEnabled').value=b.config_name ? 'true' : 'false'; }
function renderAlas(){ const a=state.alas || {}; const settings=a.settings || {}; const status=a.status || {}; const configs=uniqueAlasConfigs(); const selected=fillConfigSelect($('alasOperateConfig'), configs, status.config || selectedAlasConfig()); fillConfigSelect($('configSource'), configs, selected); syncAlasConfigSelectors(selected); $('alasBaseUrl').value=settings.base_url || ''; $('alasToken').value=''; const box=$('alasStatus'); clear(box); box.appendChild(chip(settings.enabled?'启用':'未启用', settings.enabled?'ok':'warn')); box.appendChild(chip(settings.token_set?'Token 已配置':'Token 未配置', settings.token_set?'ok':'warn')); if(!configs.length){ box.appendChild(chip('未绑定配置','warn')); } else { box.appendChild(chip(`配置 ${selected}`)); box.appendChild(chip(statusLabel(status.status), status.status==='running'?'ok':status.status==='error'?'warn':'')); } if(status.error) box.appendChild(chip(status.error, 'danger')); $('toggleAlas').textContent=status.status==='error'?'重启 ALAS':status.status==='running'?'停止 ALAS':'启动 ALAS'; $('toggleAlas').disabled=!configs.length; $('loadConfig').disabled=!configs.length; $('saveConfig').disabled=!configs.length; fillSelect($('alasBindUser'), state.users.map(u=>({value:u.username, text:u.username})), v=>v.text); if(!$('alasBindUser').value && state.users[0]) $('alasBindUser').value=state.users[0].username; fillAlasBindingForm(currentAlasBinding($('alasBindUser').value)); const rows=$('alasBindRows'); clear(rows); alasBindings().forEach(b=>{ const tr=document.createElement('tr'); tr.append(td(b.username), td(b.role==='admin'?'管理员':'普通用户'), td(b.config_name || '未绑定'), td(b.can_run?'允许':'拒绝')); const act=document.createElement('td'); act.className='actions'; act.append(btn('编辑','',()=>{ fillAlasBindingForm(b); if(b.config_name) syncAlasConfigSelectors(b.config_name); })); if(b.config_name) act.append(btn('取消绑定','danger',async()=>{ await api('/api/admin/alas/permissions',{method:'PUT', body:{username:b.username, enabled:false}}); show('ALAS 绑定已取消'); await refreshDomains('overview','alas'); })); tr.appendChild(act); rows.appendChild(tr); }); }
function renderLogs(){ $('runtimeLogs').textContent=(state.runtimeLogs || []).join('\n'); const rows=$('logRows'); clear(rows); [...state.logs].reverse().forEach(l=>{ const tr=document.createElement('tr'); tr.append(td(ts(l.ts)), td(l.username), td(actionText(l.action)), td(l.detail)); rows.appendChild(tr); }); }
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
function applyLogs(data){ state.logs=data.logs || []; renderLogs(); }
function applyRuntimeLogs(data){ state.runtimeLogs=data.logs || []; renderLogs(); }
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
function loadLogs(options={}){ return requestResource('logs', signal=>api('/api/admin/logs',{signal}), applyLogs, options); }
function loadRuntimeLogs(options={}){ return requestResource('runtimeLogs', signal=>api('/api/admin/runtime-logs?lines=400',{signal}), applyRuntimeLogs, options); }
const RESOURCE_LOADERS = {overview:loadOverview, devices:loadDevices, users:loadUsers, permissions:loadPermissions, video:loadVideo, alas:loadAlas, logs:loadLogs, runtimeLogs:loadRuntimeLogs};
async function loadTab(tabId, options={}){
  const names=TAB_RESOURCES[tabId] || TAB_RESOURCES.overview;
  const force=!!options.force;
  const results=await Promise.allSettled(names.map(name=>loadIfNeeded(name, RESOURCE_LOADERS[name], {force})));
  results.forEach((result, index)=>{ if(result.status==='rejected') reportRequestError(result.reason, `${names[index]} 加载失败`); });
  return results;
}
async function loadAll(){
  const names=Object.keys(RESOURCE_LOADERS);
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
async function saveUser(){ await api('/api/admin/users',{method:'PUT', body:{username:$('newUsername').value, password:$('newPassword').value, role:$('newRole').value}}); show('用户已保存'); await refreshDomains('overview','users','permissions','alas'); }
async function deleteUser(username){ if(!confirm(`删除用户 ${username}?`)) return; await api(`/api/admin/users/${encodeURIComponent(username)}`,{method:'DELETE'}); show('用户已删除'); await refreshDomains('overview','users','permissions','alas'); }
async function saveDevice(){ const id=$('deviceId').value.trim(); await api('/api/admin/devices',{method:'PUT', body:{device_id:id, name:$('deviceName').value.trim() || id, address:$('deviceAddress').value.trim() || id, enabled:$('deviceEnabled').value==='true'}}); show('设备已保存'); clearDeviceForm(); await refreshDomains('overview','devices','permissions'); }
async function deleteDevice(id){ if(!confirm(`删除设备 ${id}?`)) return; await api(`/api/admin/devices/${encodeURIComponent(id)}`,{method:'DELETE'}); show('设备已删除'); await refreshDomains('overview','devices','permissions'); }
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
function activateTab(tabId, save=true){
  const target=$(tabId) ? tabId : 'overview';
  activeTab=target;
  document.querySelectorAll('[data-tab]').forEach(x=>x.classList.toggle('active', x.dataset.tab===target));
  document.querySelectorAll('.panel').forEach(p=>p.classList.toggle('active', p.id===target));
  if(save) localStorage.setItem(ADMIN_TAB_KEY, target);
  loadTab(target).catch(error=>reportRequestError(error));
}
document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>activateTab(b.dataset.tab));
const savedInitialTab=$(localStorage.getItem(ADMIN_TAB_KEY)) ? localStorage.getItem(ADMIN_TAB_KEY) : 'overview';
activateTab('overview', false);
bindAction('reloadAll', loadAll, '刷新中');
bindAction('saveUser', saveUser, '保存中');
bindAction('saveDevice', saveDevice, '保存中');
$('clearDeviceForm').onclick=()=>clearDeviceForm();
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
