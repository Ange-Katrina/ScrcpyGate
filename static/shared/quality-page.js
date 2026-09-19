/* Extracted from static/pages/quality.html. */
(function () {
      'use strict';
      if (window.lucide) { lucide.createIcons(); }

      /* ---------- toast ---------- */
      function showToast(msg, type) {
        var root = document.getElementById('toast-root');
        if (!root) return;
        var icon = type === 'success' ? 'circle-check' : (type === 'error' ? 'circle-alert' : 'info');
        var t = document.createElement('div');
        t.className = 'toast ' + (type || 'info');
        t.innerHTML = '<i data-lucide="' + icon + '"></i><span></span>';
        t.querySelector('span').textContent = tr(msg);
        root.appendChild(t);
        if (window.lucide) { lucide.createIcons(); }
        requestAnimationFrame(function () { t.classList.add('show'); });
        setTimeout(function () {
          t.classList.remove('show');
          setTimeout(function () { t.remove(); }, 240);
        }, 2600);
      }

      function $(id) { return document.getElementById(id); }
      function esc(s) { var d = document.createElement('div'); d.textContent = String(s == null ? '' : s); return d.innerHTML; }
      function tr(s) { return window.ScrcpyGateI18n ? window.ScrcpyGateI18n.t(String(s == null ? '' : s)) : String(s == null ? '' : s); }
      var dashboard = window.ScrcpyGateDashboard || {};
      function refreshIcons() { if (window.lucide) { window.lucide.createIcons(); } }

      /* ---------- 数据 ---------- */
      var MAXRES_LABELS = { none: '不限制', '1080p': '1080p', '1440p': '1440p', '4k': '4K' };
      // 输出尺寸档位由后端的 resolution_cap_options 生成（受分辨率上限约束），
      // 不再写死 720p/1080p 两项。
      var RES_OPTIONS = ['1280x720', '1920x1080'];

       var BUILTIN_PRESETS = [];
       var CUSTOM_PRESETS = [];

       function defaultParams() {
         return { resMode: '', customW: '', customH: '', customChecked: false, fps: '', bitrate: '', maxRes: '', h264: false, rawV2: false, protoAvail: false, legacyAvail: false, defaultMode: '', fullscreenQuality: '', idleStopMin: '', idleStopUnit: 'min', sessionLimitH: '', sessionLimitUnit: 'h', viewerHiddenStopEnabled: true, viewerHiddenStopMin: 5, viewerBlurStopEnabled: false, viewerBlurStopMin: 5, viewerStopSettingsSynced: false };
       }
      function clone(o) { return JSON.parse(JSON.stringify(o)); }

      var baseline = defaultParams();
      var current = defaultParams();
       var selectedPresetId = null;
      // 卡片上"选中预览"的预设：只影响高亮与徽标，不改表单、不进待保存变更。
      var highlightedPresetId = null;
      // 已保存到后端的预设（video_profile）；与 selectedPresetId 不同即「应用了但没保存」。
      var savedPresetId = null;
      // 「重置」弹窗要恢复的预设：点重置按钮时记录，取消弹窗不会污染选中态。
      var resetPresetId = null;
      var pendingRestart = false;
      var pendingToggle = null;
      var pendingMode = null;
      var editingPresetId = null;
      var deletingPresetId = null;
      var emergencyReadonly = false;
       var gen = null;
       var qualityState = 'loading';
       var qualityError = '';
       var statusData = null;
       var CLIENTS = [];
      var applying = false;
      var userQuality = { default_preset: 'balanced', enabled_presets: ['smooth','balanced','sharp','low_latency'], bandwidth_preset: '', max_size_limit: 1920, user_custom_tuning: false };
      var baselineUQ = null;
      var bandwidthTiers = [];
      var bandwidthRecommendations = {};
      var pendingBandwidthTier = '';
      var resolutionCapOptions = [];
      // 预设分辨率下限（后端 min_preset_max_size，480p）：低于它的档位对预设不合法。
      var minPresetMaxSizeValue = 854;
      // 码率上限（Mbps）来自后端 limits.video_bit_rate —— 前端不再自己写死，
      // 免得出现「页面允许 50、后端允许 100、设备只声明 10」这种三套说法。
      var videoBitRateMaxMbpsValue = 10;
      function videoBitRateMaxMbps() { return videoBitRateMaxMbpsValue > 0 ? videoBitRateMaxMbpsValue : 10; }
      function applyVideoBitRateBounds() {
        var max = String(videoBitRateMaxMbps());
        ['inp-bitrate', 'add-bitrate', 'edit-bitrate'].forEach(function (id) {
          var el = $(id);
          if (el) { el.setAttribute('max', max); }
        });
      }
      function cloneUserQuality() {
        return { default_preset: userQuality.default_preset || '', enabled_presets: (userQuality.enabled_presets || []).slice(), bandwidth_preset: userQuality.bandwidth_preset || '', max_size_limit: Number(userQuality.max_size_limit || 1920), user_custom_tuning: !!userQuality.user_custom_tuning };
      }
      baselineUQ = cloneUserQuality();

      function findBuiltin(id) {
        for (var i = 0; i < BUILTIN_PRESETS.length; i++) { if (BUILTIN_PRESETS[i].id === id) return BUILTIN_PRESETS[i]; }
        return null;
      }
       function findCustom(id) {
        for (var i = 0; i < CUSTOM_PRESETS.length; i++) { if (CUSTOM_PRESETS[i].id === id) return CUSTOM_PRESETS[i]; }
        return null;
       }
       function apiError(error) { return window.ScrcpyGateApi ? tr(window.ScrcpyGateApi.errorMessage(error)) : tr('数据服务不可用'); }
       function apiList(payload) { return window.ScrcpyGateApi ? window.ScrcpyGateApi.list(payload) : []; }
       function dataObject(payload) {
         if (!payload || typeof payload !== 'object') return {};
         return payload.data && typeof payload.data === 'object' && !Array.isArray(payload.data) ? payload.data : payload;
       }
       function payloadUsesEmergency(d, params) {
         d = d || {}; params = params || {};
         var emergency = d.emergency_preset || d.emergencyPreset || {};
         var flag = d.using_emergency_preset === true || d.usingEmergencyPreset === true || String(d.using_emergency_preset || '').toLowerCase() === 'true';
         var profile = String((d.defaults || {}).profile || params.profile || '').toLowerCase();
         return (flag || profile === 'emergency') && emergency.editable !== true;
       }
       function emergencyFieldsLocked() {
         return emergencyReadonly && !selectedPresetId;
       }
       function syncEmergencyReadonly() {
         var locked = emergencyFieldsLocked();
         ['sel-resolution', 'chk-custom-wh', 'inp-width', 'inp-height', 'inp-fps', 'inp-bitrate'].forEach(function (id) {
           var el = $(id); if (el) el.disabled = locked;
         });
         var note = $('emergency-readonly-note');
         if (note) note.hidden = !locked;
         var reset = $('btn-reset');
         if (reset) reset.disabled = locked;
       }
       function setDataUnavailable(message) {
         qualityState = 'error'; qualityError = tr(message || '画质数据服务不可用');
         $('param-real').hidden = false; $('form-skeleton').hidden = true;
         $('status-real').hidden = false; $('status-skeleton').hidden = true;
         $('status-data-note').textContent = qualityError;
         document.querySelectorAll('#param-real input, #param-real select, #param-real button, #presets button, #btn-save, #btn-reset, #btn-refresh-status, #btn-client-detail').forEach(function (el) { el.disabled = true; });
         ['active-config-text','param-source-label','preset-selected-badge'].forEach(function (id) { if ($(id)) $(id).textContent = qualityError; });
         ['m-status-sub','m-mode','m-keyframe','m-uptime','m-gen','m-frame-seq','m-parse-fail','m-discard','m-resync','m-cache-peak','m-client-count','m-queue','m-queue-bytes','m-drop','m-drop-sub','m-error-value','m-error-sub','m-recover-value','m-recover-sub'].forEach(function (id) { if ($(id)) $(id).textContent = '—'; });
       }
       function normalizePreset(p) {
         p = p || {};
         return { id: String(p.id || p.presetId || ''), name: String(p.name || p.displayName || '未命名预设'), width: p.width || p.resolutionWidth || '', height: p.height || p.resolutionHeight || '', fps: p.fps || p.frameRate || '', bitrate: p.bitrate || p.bitrateMbps || '', maxRes: p.maxRes || p.maxResolution || 'none', builtin: !!(p.builtin || p.system), enabled: p.enabled !== false, editable: p.editable !== false, fullscreenOnly: p.fullscreenOnly === true || p.fullscreen_only === true, projectionAllowed: p.projectionAllowed !== false && p.projection_allowed !== false, fullscreenAllowed: p.fullscreenAllowed !== false && p.fullscreen_allowed !== false };
       }
      function applyQualityPayload(payload) {
         var d = dataObject(payload);
         var params = d.config || d.settings || d.params || d.current || {};
         var presets = (d.presets || {}).items ? (d.presets || {}).items : (d.presets || []);
         var builtins = d.builtinPresets || d.builtins || presets.filter(function (p) { return p.builtin || p.system || p.readonly; });
         var customs = d.customPresets || d.custom || presets.filter(function (p) { return !(p.builtin || p.system || p.readonly); });
         BUILTIN_PRESETS = (Array.isArray(builtins) ? builtins : []).map(normalizePreset).filter(function (p) { return p.id; });
         CUSTOM_PRESETS = (Array.isArray(customs) ? customs : []).map(normalizePreset).filter(function (p) { return p.id; });
         var fullscreenOptions = BUILTIN_PRESETS.concat(CUSTOM_PRESETS).filter(function (p) { return p.enabled && p.fullscreenAllowed; });
         $('sel-fullscreen').innerHTML = '<option value="">未配置</option>' + fullscreenOptions.map(function (p) { return '<option value="' + esc(p.id) + '">' + esc(p.name) + (p.fullscreenOnly ? ' · 仅全屏' : '') + '</option>'; }).join('');
         current = Object.assign(defaultParams(), params);
         baseline = clone(current);
         gen = d.generation || d.version || params.generation || null;
         selectedPresetId = d.selectedPresetId || d.presetId || null;
         highlightedPresetId = selectedPresetId;
         savedPresetId = selectedPresetId;
         resetPresetId = null;
         emergencyReadonly = payloadUsesEmergency(d, params);
         statusData = d.status || null;
         qualityState = 'ready'; qualityError = '';
         document.querySelectorAll('#param-real input, #param-real select, #param-real button, #presets button, #btn-save, #btn-reset, #btn-refresh-status, #btn-client-detail').forEach(function (el) { el.disabled = false; });
         userQuality = {
           default_preset: d.default_preset_id || d.default_preset || '',
           enabled_presets: (Array.isArray(d.enabled_presets) ? d.enabled_presets : ['smooth','balanced','sharp','low_latency']).slice(),
           bandwidth_preset: d.bandwidth_preset || '',
           bandwidth_profile: d.bandwidth_profile || null,
           bandwidth_profile_id: d.bandwidth_profile_id || '',
           max_size_limit: Number(d.max_size_limit || 1920),
           user_custom_tuning: d.user_custom_tuning === true
         };
         bandwidthTiers = Array.isArray(d.bandwidth_presets) ? d.bandwidth_presets : [];
         bandwidthRecommendations = (d.bandwidth_recommendations && typeof d.bandwidth_recommendations === 'object') ? d.bandwidth_recommendations : {};
         resolutionCapOptions = Array.isArray(d.resolution_cap_options) ? d.resolution_cap_options : [];
         if (Number(d.min_preset_max_size) > 0) minPresetMaxSizeValue = Number(d.min_preset_max_size);
          // 码率上限同样以后端为准（bps -> Mbps），并同步到表单属性。
          var serverBitRateLimit = d.limits && d.limits.video_bit_rate;
          if (Array.isArray(serverBitRateLimit) && Number(serverBitRateLimit[1]) > 0) {
            videoBitRateMaxMbpsValue = Math.floor(Number(serverBitRateLimit[1]) / 1000000);
          }
          applyVideoBitRateBounds();
         baselineUQ = cloneUserQuality();
         renderUserQualityControls();
         renderCustomPresets(); renderBuiltinPresets();
         applying = true; updateFormUI(); applying = false; clearFieldErrors(); renderSummary(); renderActiveConfig(); updateSelection();
        renderStatus(statusData);
        $('form-skeleton').hidden = true; $('param-real').hidden = false; $('status-skeleton').hidden = true; $('status-real').hidden = false;
        syncEmergencyReadonly();
       }
      function qualityStatusState(value) {
        value = value || {};
        if (value.ok === true) return 'ok';
        var raw = String(value.status || value.state || value.health || '').trim().toLowerCase();
        if (['ok', 'healthy', 'online', 'running', 'connected', 'ready'].indexOf(raw) >= 0) return 'ok';
        if (['error', 'failed', 'failure', 'offline', 'unreachable', 'stalled', 'degraded', 'warn', 'warning'].indexOf(raw) >= 0 || value.ok === false) return 'warn';
        return 'unknown';
      }
      function renderStatus(s) {
         s = s || {};
         var state = qualityStatusState(s);
         var statusLabel = s.label ? tr(s.label) : (dashboard.statusLabel ? dashboard.statusLabel(state === 'ok' ? 'normal' : (state === 'warn' ? 'error' : 'unknown')) : tr(s.status || s.health || '未检查'));
         var chip = $('m-status');
         if (chip) { chip.className = 'status-chip ' + state; chip.innerHTML = '<span class="mini-dot"></span>' + esc(statusLabel); }
         var values = { 'm-status-sub': tr(s.message || s.detail || '未检查'), 'm-mode': s.mode || s.transportMode || '—', 'm-keyframe': s.keyframeAge == null ? '—' : s.keyframeAge, 'm-uptime': s.uptime || '—', 'm-gen': s.generation == null ? (gen == null ? '—' : String(gen)) : String(s.generation), 'm-frame-seq': s.frameSequence == null ? '—' : s.frameSequence, 'm-parse-fail': s.parseFailures == null ? '—' : s.parseFailures, 'm-discard': s.discardedFrames == null ? '—' : s.discardedFrames, 'm-resync': s.resyncCount == null ? '—' : s.resyncCount, 'm-cache-peak': s.cachePeak == null ? '—' : s.cachePeak, 'm-client-count': s.clientCount == null ? '—' : s.clientCount, 'm-queue': s.queueFrames == null ? '—' : s.queueFrames, 'm-queue-bytes': s.queueBytes == null ? '—' : s.queueBytes, 'm-drop': s.droppedFrames == null ? '—' : s.droppedFrames, 'm-drop-sub': s.dropRate == null ? '—' : s.dropRate, 'm-error-value': tr(s.error || '—'), 'm-error-sub': tr(s.errorDetail || '未检查'), 'm-recover-value': tr(s.recovery || '—'), 'm-recover-sub': tr(s.recoveryDetail || '未检查') };
         Object.keys(values).forEach(function (id) { if ($(id)) $(id).textContent = String(values[id]); });
         if ($('m-keyframe-unit')) $('m-keyframe-unit').hidden = s.keyframeAge == null;
         var q = $('m-queue-state'); if (q && s.queueState) q.textContent = tr(s.queueState);
         ['m-error-value','m-recover-value'].forEach(function (id) { var el = $(id); if (el) el.classList.toggle('good', s.ok === true && (id === 'm-recover-value' || !s.error)); });
       }
      function loadQualityStatus(showMessage) {
         if (!window.ScrcpyGateApi.isConfigured('quality.status')) { if ($('status-data-note')) $('status-data-note').textContent = tr('接口尚未配置：quality.status'); return Promise.reject(new Error('quality.status unavailable')); }
         var btn = $('btn-refresh-status'); btn.classList.add('loading'); btn.disabled = true;
         return window.ScrcpyGateApi.configured('quality.status', { method: 'GET' }).then(function (payload) { var d = dataObject(payload); statusData = d.status || d; CLIENTS = Array.isArray(d.clients) ? d.clients : apiList(d.clients); renderStatus(statusData); $('status-data-note').textContent = d.updatedAt ? tr('最后更新') + ' ' + d.updatedAt : tr('未检查'); if (showMessage) showToast(tr('传输状态已刷新'), 'success'); return payload; }).catch(function (error) { var message = apiError(error); statusData = { ok: false, status: 'unknown', label: '未检查', message: message, error: message, recovery: '等待重试', recoveryDetail: '传输状态接口不可用' }; renderStatus(statusData); $('status-data-note').textContent = message; throw error; }).finally(function () { btn.classList.remove('loading'); btn.disabled = false; });
       }
       function loadClients() {
         if (!window.ScrcpyGateApi.isConfigured('quality.clients')) { showToast(tr('接口尚未配置：quality.clients'), 'error'); return Promise.reject(new Error('quality.clients unavailable')); }
         return window.ScrcpyGateApi.configured('quality.clients', { method: 'GET' }).then(function (payload) { var d = dataObject(payload); CLIENTS = Array.isArray(d.clients) ? d.clients : apiList(payload); return CLIENTS; });
       }
       function loadQuality(showMessage) {
         qualityState = 'loading'; $('form-skeleton').hidden = false; $('param-real').hidden = true; $('status-skeleton').hidden = false; $('status-real').hidden = true;
         return window.ScrcpyGateApi.configured('quality.admin.config', { method: 'GET' }).then(function (payload) { applyQualityPayload(payload); return loadQualityStatus(false).catch(function () { return payload; }); }).then(function (payload) { if (showMessage) showToast(tr('画质设置已刷新'), 'success'); return payload; }).catch(function (error) { renderCustomPresets(); renderBuiltinPresets(); setDataUnavailable(apiError(error)); throw error; });
       }
       function reloadAfterPresetChange(message) {
         return loadQuality(false).then(function () { if (message) showToast(tr(message), 'success'); });
       }
      function presetName(id) {
        var b = findBuiltin(id);
        if (b) return b.name;
        var c = findCustom(id);
        return c ? c.name : '';
      }
      function effectiveRes(p) {
        var custom = p.customChecked || p.resMode === 'custom';
        if (custom) return { w: p.customW, h: p.customH };
        var parts = String(p.resMode).split('x');
        return { w: parts[0], h: parts[1] };
      }

      /* ---------- 输出尺寸档位（受分辨率上限约束） ---------- */
      function capEdge() {
        var cap = Number(userQuality.max_size_limit) || Number(resolutionCapOptions.length ? resolutionCapOptions[resolutionCapOptions.length - 1].value : 1920) || 1920;
        return Math.max(cap, minPresetMaxSize());
      }
      function minPresetMaxSize() {
        return Number(minPresetMaxSizeValue) > 0 ? Number(minPresetMaxSizeValue) : 854;
      }
      function resolutionOptionList() {
        var cap = capEdge();
        var floor = minPresetMaxSize();
        var items = [];
        resolutionCapOptions.forEach(function (o) {
          var edge = Number(o.value);
          if (!(edge > 0) || edge > cap || edge < floor) return;
          var parts = String(o.description || '').split(' x ');
          if (parts.length !== 2) return;
          var width = Number(parts[0]);
          var height = Number(parts[1]);
          if (!(width > 0 && height > 0)) return;
          items.push({
            key: width + 'x' + height,
            width: width,
            height: height,
            label: (o.label ? (o.label + ' · ') : '') + width + ' × ' + height,
          });
        });
        items.sort(function (a, b) { return b.width - a.width; });
        return items;
      }
      function clampResolutionToCap() {
        var cap = capEdge();
        var res = effectiveRes(current);
        var edge = Math.max(Number(res.w) || 0, Number(res.h) || 0);
        if (!(edge > cap)) return false;
        var width = cap;
        var height = Math.max(240, Math.round(cap * 9 / 16));
        current.resMode = 'custom';
        current.customChecked = true;
        current.customW = width;
        current.customH = height;
        return true;
      }
      function renderResolutionOptions() {
        var selRes = $('sel-resolution');
        if (!selRes) return;
        var items = resolutionOptionList();
        selRes.innerHTML = items.map(function (item) {
          return '<option value="' + esc(item.key) + '">' + esc(item.label) + '</option>';
        }).join('') + '<option value="custom">' + esc(tr('自定义')) + '</option>';
        RES_OPTIONS = items.map(function (item) { return item.key; });
        // 已保存的分辨率不在档位表里（历史配置/自定义值）时补一条只读展示项，
        // 否则下拉会显示成空。
        if (current.resMode && current.resMode !== 'custom' && RES_OPTIONS.indexOf(current.resMode) < 0) {
          var parts = String(current.resMode).split('x');
          selRes.innerHTML = '<option value="' + esc(current.resMode) + '">' + esc(parts[0] + ' × ' + parts[1]) + '</option>' + selRes.innerHTML;
        }
      }

      /* ---------- 开关 ---------- */
      function setSwitch(sw, on) {
        if (!sw) return;
        sw.setAttribute('aria-checked', on ? 'true' : 'false');
        var hintId = sw.getAttribute('data-hint');
        if (hintId) { var h = $(hintId); if (h) { h.classList.toggle('show', on); } }
      }

      function onManualEdit() {
         if (applying) return;
         if (emergencyFieldsLocked()) return;
         // Keep a selected builtin preset attached to manual edits so the
         // admin save request can persist the edited preset definition.
         // Custom presets are managed through their dedicated edit dialog.
         if (selectedPresetId && !findBuiltin(selectedPresetId)) { selectedPresetId = null; updateSelection(); }
         clearFieldErrors();
         renderSummary();
      }

      function setToggleState(sw, key, on) {
        applying = true;
        current[key] = on;
        setSwitch(sw, on);
        applying = false;
        onManualEdit();
        syncModeCards();
        syncDefaultModeOptions();
      }

      /* ---------- 预设渲染 ---------- */
      function presetMetaHtml(p) {
        return '<span class="preset-meta-chip">' + p.width + '×' + p.height + '</span>' +
          '<span class="preset-meta-chip">' + p.fps + ' fps</span>' +
          '<span class="preset-meta-chip">' + p.bitrate + ' Mbps</span>' +
          (p.fullscreenOnly ? '<span class="preset-flag">仅全屏</span>' : '') +
          (p.enabled === false ? '<span class="preset-flag off">已停用</span>' : '');
      }
      function presetToggleHtml(p) {
        var name = String(p.id || '');
        if (name.indexOf('p-') === 0) { name = name.slice(2); }
        var on = !!name && userQuality.enabled_presets.indexOf(name) >= 0;
        // 标准开关：决定该预设是否出现在工作台「画面」面板的预设列表里。
        return '<button class="preset-switch' + (on ? ' on' : '') + '" type="button" role="switch" aria-checked="' + (on ? 'true' : 'false') + '" data-toggle="allowed" data-preset-name="' + esc(name) + '" title="' + esc(tr('开启后普通用户可在工作台「画面」面板中选择该预设')) + '">'
          + '<span class="preset-switch-track" aria-hidden="true"><span class="preset-switch-knob"></span></span>'
          + '<span class="preset-switch-label">' + esc(tr('工作台可选')) + '</span></button>';
      }
      function presetCardHtml(p, custom) {
        // 「应用」才把参数写进下方表单（并进入待保存变更）；点卡片本身只选中预览，
        // 所以单纯点一下预设不会制造待保存变更。
        var applied = String(p.id) === String(selectedPresetId || '');
        var ops = presetToggleHtml(p);
        ops += applied
          ? '<span class="preset-op-state"><i data-lucide="check"></i>' + esc(tr('已应用')) + '</span>'
          : '<button class="preset-op-btn primary" type="button" data-op="apply"><i data-lucide="check"></i>' + esc(tr('应用')) + '</button>';
        if (custom) {
          ops += '<button class="preset-op-btn" type="button" data-op="edit"><i data-lucide="pencil"></i>编辑</button>'
            + '<button class="preset-op-btn danger" type="button" data-op="del"><i data-lucide="trash-2"></i>删除</button>';
        } else {
          ops += '<button class="preset-op-btn" type="button" data-op="reset" title="仅恢复此预设默认值"><i data-lucide="rotate-ccw"></i>重置</button>';
        }
        return '<div class="preset-item' + (applied ? ' applied' : '') + '" data-preset-id="' + esc(p.id) + '">' +
          '<button class="preset-item-main" type="button" aria-pressed="false">' +
            '<span class="preset-top"><span class="preset-name">' + esc(p.name) + '</span><i class="preset-check" data-lucide="check"></i></span>' +
            '<span class="preset-meta">' + presetMetaHtml(p) + '</span>' +
          '</button>' +
          '<span class="preset-ops">' + ops + '</span>' +
        '</div>';
      }
      function syncPresetToggles() {
        document.querySelectorAll('.preset-switch').forEach(function (btn) {
          var name = btn.getAttribute('data-preset-name');
          var on = !!name && userQuality.enabled_presets.indexOf(name) >= 0;
          btn.classList.toggle('on', on);
          btn.setAttribute('aria-checked', on ? 'true' : 'false');
          var label = btn.querySelector('.preset-switch-label');
          if (label) label.textContent = tr('工作台可选');
        });
        refreshIcons();
      }
      function normalizeEnabledPresets() {
        // 保持目录顺序，避免「关掉再打开」因数组顺序变化被误判为未保存修改。
        var order = BUILTIN_PRESETS.concat(CUSTOM_PRESETS).map(function (p) { return String(p.id || '').replace(/^p-/, ''); });
        var selected = {};
        userQuality.enabled_presets.forEach(function (name) { selected[name] = true; });
        var ordered = order.filter(function (name) { return !!selected[name]; });
        userQuality.enabled_presets.forEach(function (name) { if (ordered.indexOf(name) < 0) ordered.push(name); });
        userQuality.enabled_presets = ordered;
      }
      function toggleAllowedPreset(name) {
        if (!name) return;
        var idx = userQuality.enabled_presets.indexOf(name);
        if (idx >= 0) {
          if (userQuality.enabled_presets.length <= 1) { showToast(tr('至少保留一个可切换预设'), 'error'); return; }
          userQuality.enabled_presets.splice(idx, 1);
        } else {
          userQuality.enabled_presets.push(name);
        }
        normalizeEnabledPresets();
        syncPresetToggles();
        renderSummary();
      }
      function bindPresetToggles() {
        ['builtin-preset-list', 'custom-preset-list'].forEach(function (id) {
          var list = $(id);
          if (!list || list.getAttribute('data-toggle-bound') === 'true') return;
          list.setAttribute('data-toggle-bound', 'true');
          list.addEventListener('click', function (event) {
            var btn = event.target && event.target.closest ? event.target.closest('[data-toggle="allowed"]') : null;
            if (!btn || !list.contains(btn)) return;
            event.preventDefault();
            event.stopPropagation();
            toggleAllowedPreset(btn.getAttribute('data-preset-name'));
          });
        });
      }

       function renderCustomPresets() {
        var list = $('custom-preset-list');
        var empty = $('custom-preset-empty');
        var cnt = $('custom-preset-count');
        list.innerHTML = CUSTOM_PRESETS.map(function (p) { return presetCardHtml(p, true); }).join('');
        list.hidden = CUSTOM_PRESETS.length === 0;
        empty.hidden = CUSTOM_PRESETS.length > 0;
        cnt.textContent = String(CUSTOM_PRESETS.length);
        if (window.lucide) { lucide.createIcons(); }
        updateSelection();
       }
       function renderBuiltinPresets() {
         var list = $('builtin-preset-list');
         if (!list) return;
         if (!BUILTIN_PRESETS.length) { list.innerHTML = '<div class="preset-empty"><i data-lucide="bookmark-x"></i><span>暂无内置预设数据</span><small>预设由后端服务提供</small></div>'; refreshIcons(); return; }
         list.innerHTML = BUILTIN_PRESETS.map(function (p) { return presetCardHtml(p, false); }).join('');
         refreshIcons(); updateSelection();
       }

      function updateSelection() {
        document.querySelectorAll('.preset-item').forEach(function (item) {
          var id = item.getAttribute('data-preset-id');
          var picked = id === highlightedPresetId;
          var applied = id === selectedPresetId;
          item.classList.toggle('selected', picked);
          item.classList.toggle('applied', applied);
          var main = item.querySelector('.preset-item-main');
          if (main) { main.setAttribute('aria-pressed', picked ? 'true' : 'false'); }
        });
        var badge = $('preset-selected-badge');
        if (badge) {
          var badgeLocked = emergencyFieldsLocked();
          var badgeSelected = !!(highlightedPresetId || selectedPresetId);
          if (badgeLocked) {
            badge.textContent = tr('应急兼容 · 只读');
            badge.removeAttribute('title');
          } else if (highlightedPresetId && highlightedPresetId !== selectedPresetId) {
            // 选中但未应用：只影响预览高亮，不改表单、不进待保存变更。
            badge.textContent = tr('已选中：') + presetName(highlightedPresetId);
            badge.setAttribute('title', tr('点「应用」把该预设写入下方表单，保存后生效'));
          } else {
            badge.textContent = selectedPresetId ? presetName(selectedPresetId) : tr('手动配置');
            badge.removeAttribute('title');
          }
          // 徽标配色跟随状态：只读=警告色，选中=品牌色，未选=中性。
          badge.classList.toggle('is-locked', badgeLocked);
          badge.classList.toggle('is-selected', !badgeLocked && badgeSelected);
        }
        var src = $('param-source-label');
        if (src) {
          if (emergencyFieldsLocked()) src.textContent = tr('应急兼容 · 只读');
          else if (selectedPresetId) src.textContent = (hasPendingPresetSwitch() ? tr('待应用预设：') : tr('应用预设：')) + presetName(selectedPresetId);
          else src.textContent = tr('手动配置');
        }
        syncEmergencyReadonly();
      }

      function highlightPreset(p) {
        if (!p) return;
        highlightedPresetId = p.id;
        updateSelection();
      }

      /* ---------- 用户画质控制 ---------- */
      function presetDisplayName(name) {
        var b = findBuiltin(name) || findBuiltin(String(name || '').replace(/^p-/, ''));
        return b ? b.name : name;
      }
      function capOptionLabel(v) {
        for (var i = 0; i < resolutionCapOptions.length; i++) {
          if (Number(resolutionCapOptions[i].value) === Number(v)) return resolutionCapOptions[i].label + ' · ' + resolutionCapOptions[i].description;
        }
        return String(v);
      }
      function tierLabel(k) {
        for (var i = 0; i < bandwidthTiers.length; i++) {
          if (bandwidthTiers[i].key === k) return bandwidthTiers[i].label;
        }
        return k ? String(k) : tr('自定义');
      }
      function bandwidthPreviewRows(tierKey) {
        var rec = bandwidthRecommendations && bandwidthRecommendations[tierKey];
        var cap = Number(userQuality.max_size_limit) || 1920;
        return BUILTIN_PRESETS.map(function (p) {
          var name = String(p.id || '').replace(/^p-/, '');
          var next = rec && rec[name];
          var from = (p.width || '—') + '×' + (p.height || '—') + ' · ' + (p.fps || '—') + 'fps · ' + (p.bitrate || '—') + 'Mbps';
          var to = '';
          if (next) {
            var maxSize = Math.min(Number(next.max_size) || 0, cap);
            var mbps = Math.round((Number(next.video_bit_rate) || 0) / 100000) / 10;
            to = maxSize + '×' + Math.round(maxSize * 9 / 16) + ' · ' + (next.max_fps || '—') + 'fps · ' + mbps + 'Mbps';
          }
          return { name: p.name, from: from, to: to };
        });
      }
      function renderBandwidthConfirm(tierKey) {
        var list = $('bw-confirm-list');
        if (!list) return;
        var rows = bandwidthPreviewRows(tierKey);
        list.innerHTML = rows.map(function (row) {
          return '<li><span class="bw-row-name">' + esc(row.name) + '</span><span class="bw-row-from">' + esc(row.from) + '</span><i data-lucide="arrow-right"></i><span class="bw-row-to">' + esc(row.to || tr('按推荐值')) + '</span></li>';
        }).join('') || '<li class="bw-row-empty">' + esc(tr('没有可预览的内置预设')) + '</li>';
        var title = $('bw-confirm-title');
        if (title) title.textContent = tr('切换到 ' + tierLabel(tierKey) + ' 档后，内置预设将变为：');
        refreshIcons();
      }
      function hasUQChanges() {
        return userQuality.default_preset !== baselineUQ.default_preset
          || JSON.stringify(userQuality.enabled_presets) !== JSON.stringify(baselineUQ.enabled_presets)
          || userQuality.bandwidth_preset !== baselineUQ.bandwidth_preset
          || Number(userQuality.max_size_limit) !== Number(baselineUQ.max_size_limit)
          || !!userQuality.user_custom_tuning !== !!baselineUQ.user_custom_tuning;
      }
      function uqChangedFields() {
        var out = {};
        if (userQuality.default_preset !== baselineUQ.default_preset) out.default_preset = userQuality.default_preset;
        if (JSON.stringify(userQuality.enabled_presets) !== JSON.stringify(baselineUQ.enabled_presets)) out.enabled_presets = userQuality.enabled_presets.slice();
        if (userQuality.bandwidth_preset !== baselineUQ.bandwidth_preset) out.bandwidth_preset = userQuality.bandwidth_preset;
        if (Number(userQuality.max_size_limit) !== Number(baselineUQ.max_size_limit)) out.max_size_limit = Number(userQuality.max_size_limit);
        if (!!userQuality.user_custom_tuning !== !!baselineUQ.user_custom_tuning) out.user_custom_tuning = !!userQuality.user_custom_tuning;
        return out;
      }
      function renderUserQualityControls() {
        var defaultSelect = $('sel-default-preset');
        if (defaultSelect) {
          var defaultOptions = BUILTIN_PRESETS.concat(CUSTOM_PRESETS).filter(function (p) {
            return p.enabled && p.projectionAllowed && !p.fullscreenOnly;
          });
          defaultSelect.innerHTML = defaultOptions.length
            ? defaultOptions.map(function (p) { return '<option value="' + esc(p.id) + '">' + esc(p.name) + '</option>'; }).join('')
            : '<option value="">无可用普通预设</option>';
          var selectedDefault = defaultOptions.some(function (p) { return p.id === userQuality.default_preset; }) ? userQuality.default_preset : '';
          defaultSelect.value = selectedDefault;
          defaultSelect.disabled = defaultOptions.length === 0;
          defaultSelect.onchange = function () {
            userQuality.default_preset = defaultSelect.value || '';
            renderSummary();
          };
          var defaultNote = $('default-preset-note');
          if (defaultNote) {
            defaultNote.textContent = defaultOptions.length
              ? '应急兼容仅在所有普通预设失效时自动使用，且不可修改。'
              : '当前没有可用普通预设，将自动使用只读应急兼容配置。';
          }
        }
        // 「可切换」开关已移到每张预设卡片上（见 presetToggleHtml / syncPresetToggles）。
        syncPresetToggles();
        var tiers = $('bw-tier-list');
        if (tiers) {
          tiers.innerHTML = bandwidthTiers.map(function (t) {
            var active = t.key === userQuality.bandwidth_preset;
            return '<button class="bw-tier-btn' + (active ? ' active' : '') + '" type="button" role="radio" aria-checked="' + (active ? 'true' : 'false') + '" data-tier="' + esc(t.key) + '" title="' + esc(t.description) + '">' + esc(t.label) + '</button>';
          }).join('') + '<button class="bw-tier-btn' + (!userQuality.bandwidth_preset ? ' active' : '') + '" type="button" role="radio" aria-checked="' + (!userQuality.bandwidth_preset ? 'true' : 'false') + '" data-tier="" title="' + tr('手动维护各预设参数') + '">' + tr('自定义') + '</button>';
          tiers.querySelectorAll('.bw-tier-btn').forEach(function (btn) {
            btn.addEventListener('click', function () {
              var key = btn.getAttribute('data-tier') || '';
              if (key === userQuality.bandwidth_preset) return;
              if (!key) {
                // 「自定义」只是停止自动调整，不覆盖任何预设，无需确认。
                userQuality.bandwidth_preset = '';
                pendingBandwidthTier = '';
                renderUserQualityControls();
                renderSummary();
                showToast(tr('已切换为自定义，不再自动调整预设参数'), 'info');
                return;
              }
              pendingBandwidthTier = key;
              renderBandwidthConfirm(key);
              openModal('modal-bandwidth');
            });
          });
          var note = $('bw-tier-note');
          if (note) {
            var desc = '';
            for (var i = 0; i < bandwidthTiers.length; i++) {
              if (bandwidthTiers[i].key === userQuality.bandwidth_preset) desc = bandwidthTiers[i].description;
            }
            note.textContent = desc || tr('手动维护各预设参数，不自动调整');
          }
        }
        var cap = $('sel-max-size-limit');
        if (cap) {
          cap.innerHTML = resolutionCapOptions.filter(function (o) {
            // 低于预设下限的档位不能作为上限：预设最矮就是 480p（854）。
            return Number(o.value) >= minPresetMaxSize();
          }).map(function (o) {
            return '<option value="' + esc(o.value) + '"' + (Number(o.value) === Number(userQuality.max_size_limit) ? ' selected' : '') + '>' + esc(o.label) + ' · ' + esc(o.description) + '</option>';
          }).join('');
          cap.onchange = function () {
            userQuality.max_size_limit = Number(cap.value);
            // 上限收紧后：先按上限收紧当前参数，再重建档位（避免残留超限的历史档位项）。
            clampResolutionToCap();
            renderResolutionOptions();
            updateFormUI();
            renderSummary();
          };
        }
        renderResolutionOptions();
        var tuning = $('chk-user-tuning');
        if (tuning) {
          tuning.checked = !!userQuality.user_custom_tuning;
          tuning.onchange = function () {
            userQuality.user_custom_tuning = tuning.checked;
            renderSummary();
          };
        }
      }

      /* ---------- 应用预设 / 表单 ---------- */
      function applyPreset(p) {
        applying = true;
        var resKey = p.width + 'x' + p.height;
        current.resMode = RES_OPTIONS.indexOf(resKey) >= 0 ? resKey : 'custom';
        current.customW = p.width;
        current.customH = p.height;
        current.customChecked = current.resMode === 'custom';
        current.fps = p.fps;
        current.bitrate = p.bitrate;
        selectedPresetId = p.id;
        highlightedPresetId = p.id;
        applying = false;
        updateFormUI();
        clearFieldErrors();
        // 卡片上的「应用 / 已应用」状态跟着变，需要重画卡片。
        renderBuiltinPresets();
        renderCustomPresets();
        renderSummary();
        updateSelection();
      }

      function updateFormUI() {
        var selRes = $('sel-resolution'), chk = $('chk-custom-wh'), box = $('custom-wh-box');
        selRes.value = current.resMode;
        chk.checked = !!current.customChecked;
        box.hidden = !current.customChecked;
        $('inp-width').value = current.customW;
        $('inp-height').value = current.customH;
        $('inp-fps').value = current.fps;
        $('inp-bitrate').value = current.bitrate;
        setSwitch($('sw-rawv2'), !!current.rawV2);
        setSwitch($('sw-proto'), !!current.protoAvail);
        setSwitch($('sw-legacy'), !!current.legacyAvail);
        syncDefaultModeOptions();
        syncModeCards();
        $('sel-fullscreen').value = current.fullscreenQuality;
        $('inp-idle-stop').value = current.idleStopMin;
        $('sel-idle-unit').value = current.idleStopUnit;
        $('inp-session-limit').value = current.sessionLimitH;
        $('sel-session-unit').value = current.sessionLimitUnit;
        $('chk-viewer-hidden-stop').checked = !!current.viewerHiddenStopEnabled;
        $('inp-viewer-hidden-stop').value = current.viewerHiddenStopMin;
        $('chk-viewer-blur-stop').checked = !!current.viewerBlurStopEnabled;
        $('inp-viewer-blur-stop').value = current.viewerBlurStopMin;
        $('chk-viewer-stop-sync').checked = !!current.viewerStopSettingsSynced;
        syncViewerStopControls();
        syncEmergencyReadonly();
      }

      function syncViewerStopControls() {
        var synced = !!current.viewerStopSettingsSynced;
        var blurEnabled = $('chk-viewer-blur-stop');
        var blurMinutes = $('inp-viewer-blur-stop');
        var follow = $('viewer-blur-stop-follow');
        if (synced) {
          if (blurEnabled) blurEnabled.checked = !!current.viewerHiddenStopEnabled;
          if (blurMinutes) blurMinutes.value = current.viewerHiddenStopMin;
        }
        if (blurEnabled) blurEnabled.disabled = synced;
        if (blurMinutes) {
          blurMinutes.disabled = synced;
          blurMinutes.setAttribute('aria-readonly', synced ? 'true' : 'false');
        }
        if (follow) follow.hidden = !synced;
      }

      function syncModeCards() {
        var states = { rawV2: current.rawV2, protocol: current.protoAvail, legacy: current.legacyAvail };
        document.querySelectorAll('.mode-card').forEach(function (card) {
          var key = card.getAttribute('data-mode');
          var on = !!states[key];
          card.classList.toggle('on', on);
          var st = card.querySelector('.mode-state');
          if (st) { st.textContent = on ? '已启用' : '已停用'; }
          var isDefault = String(current.defaultMode) === String(key);
          card.classList.toggle('is-default', isDefault);
          var tag = card.querySelector('.mode-default-tag');
          if (tag) { tag.hidden = !isDefault; }
          var btn = card.querySelector('.mode-default-btn');
          if (btn) {
            btn.disabled = !on || isDefault;
            btn.setAttribute('aria-pressed', isDefault ? 'true' : 'false');
            btn.classList.toggle('active', isDefault);
            var label = btn.querySelector('span');
            if (label) { label.textContent = isDefault ? '当前默认' : '设为默认'; }
          }
        });
      }

      function syncDefaultModeOptions() {
        var dm = $('sel-default-mode');
        if (!dm) return;
        var rawOpt = dm.querySelector('option[value="rawV2"]');
        var protoOpt = dm.querySelector('option[value="protocol"]');
        var legacyOpt = dm.querySelector('option[value="legacy"]');
        if (rawOpt) {
          rawOpt.disabled = !current.rawV2;
          rawOpt.textContent = 'Raw v2' + (current.rawV2 ? '' : '（已停用）');
        }
        if (protoOpt) {
          protoOpt.disabled = !current.protoAvail;
          protoOpt.textContent = 'protocol' + (current.protoAvail ? '' : '（未启用）');
        }
        if (legacyOpt) {
          legacyOpt.disabled = !current.legacyAvail;
          legacyOpt.textContent = 'legacy' + (current.legacyAvail ? '' : '（未启用）');
        }
        if (current.defaultMode === 'rawV2' && !current.rawV2) {
          current.defaultMode = current.protoAvail ? 'protocol' : (current.legacyAvail ? 'legacy' : 'rawV2');
        }
        if (current.defaultMode === 'protocol' && !current.protoAvail) { current.defaultMode = current.rawV2 ? 'rawV2' : (current.legacyAvail ? 'legacy' : 'protocol'); }
        if (current.defaultMode === 'legacy' && !current.legacyAvail) { current.defaultMode = current.rawV2 ? 'rawV2' : (current.protoAvail ? 'protocol' : 'legacy'); }
        dm.value = current.defaultMode;
      }

      /* ---------- 错误与摘要 ---------- */
      function clearFieldErrors() {
        document.querySelectorAll('.param-row.has-error').forEach(function (r) {
          r.classList.remove('has-error');
          r.querySelectorAll('[aria-errormessage]').forEach(function (input) { input.removeAttribute('aria-invalid'); });
        });
      }
      function setRowError(rowId) {
        var r = $(rowId);
        if (r) {
          r.classList.add('has-error');
          r.querySelectorAll('[aria-errormessage]').forEach(function (input) { input.setAttribute('aria-invalid', 'true'); });
        }
      }

      function renderSummary() {
        // 画质三项（分辨率/帧率/码率）以「当前应用的预设」为参照：点预设、点「应用」
        // 都不会产生变更行，只有真的改了预设里的内容才会显示差异。
        var ref = appliedPresetReference();
        var be = effectiveRes(ref.values), ce = effectiveRes(current);
        var items = [];
        function push(label, from, to) { items.push({ label: label, from: from, to: to }); }
        if (String(be.w) !== String(ce.w) || String(be.h) !== String(ce.h)) {
          push('输出分辨率', be.w + '×' + be.h, ce.w + '×' + ce.h);
        }
        if (String(ref.values.fps) !== String(current.fps)) { push('帧率', String(ref.values.fps), String(current.fps)); }
        if (String(ref.values.bitrate) !== String(current.bitrate)) { push('码率', String(ref.values.bitrate), String(current.bitrate)); }
        if (!!baseline.rawV2 !== !!current.rawV2) { push('Raw v2 模式', baseline.rawV2 ? '启用' : '停用', current.rawV2 ? '启用' : '停用'); }
        if (!!baseline.protoAvail !== !!current.protoAvail) { push('protocol 备用模式', baseline.protoAvail ? '启用' : '停用', current.protoAvail ? '启用' : '停用'); }
        if (!!baseline.legacyAvail !== !!current.legacyAvail) { push('legacy 备用模式', baseline.legacyAvail ? '启用' : '停用', current.legacyAvail ? '启用' : '停用'); }
        if (baseline.defaultMode !== current.defaultMode) { push('默认传输模式', baseline.defaultMode, current.defaultMode); }
        if (baseline.fullscreenQuality !== current.fullscreenQuality) { push('全屏默认画质', baseline.fullscreenQuality, current.fullscreenQuality); }
        function fmtIdle(p) { var v = Number(p.idleStopMin); return v === 0 ? '仅 5 秒重连宽限' : (String(p.idleStopMin) + ' 分钟'); }
        function fmtSession(p) {
          var v = Number(p.sessionLimitH);
          if (v === 0) return '不限制';
          return p.sessionLimitUnit === 'min' ? String(p.sessionLimitH) + ' 分钟' : String(p.sessionLimitH) + ' 小时';
        }
        if (String(baseline.idleStopMin) !== String(current.idleStopMin) || baseline.idleStopUnit !== current.idleStopUnit) { push('无观看端自动停止', fmtIdle(baseline), fmtIdle(current)); }
        if (String(baseline.sessionLimitH) !== String(current.sessionLimitH) || baseline.sessionLimitUnit !== current.sessionLimitUnit) { push('连续投屏时间上限', fmtSession(baseline), fmtSession(current)); }
        if (!!baseline.viewerHiddenStopEnabled !== !!current.viewerHiddenStopEnabled || String(baseline.viewerHiddenStopMin) !== String(current.viewerHiddenStopMin)) {
          push('切换标签页后停止观看', (baseline.viewerHiddenStopEnabled ? '启用' : '停用') + ' · ' + baseline.viewerHiddenStopMin + ' 分钟', (current.viewerHiddenStopEnabled ? '启用' : '停用') + ' · ' + current.viewerHiddenStopMin + ' 分钟');
        }
        if (!!baseline.viewerBlurStopEnabled !== !!current.viewerBlurStopEnabled || String(baseline.viewerBlurStopMin) !== String(current.viewerBlurStopMin)) {
          push('窗口失去焦点后停止观看', (baseline.viewerBlurStopEnabled ? '启用' : '停用') + ' · ' + baseline.viewerBlurStopMin + ' 分钟', (current.viewerBlurStopEnabled ? '启用' : '停用') + ' · ' + current.viewerBlurStopMin + ' 分钟');
        }
        if (!!baseline.viewerStopSettingsSynced !== !!current.viewerStopSettingsSynced) {
          push('同步两个停止设置', baseline.viewerStopSettingsSynced ? '开启' : '关闭', current.viewerStopSettingsSynced ? '开启' : '关闭');
        }
        if (hasUQChanges()) {
          if (userQuality.default_preset !== baselineUQ.default_preset) {
            push('普通用户默认预设', presetDisplayName(baselineUQ.default_preset) || '应急兼容', presetDisplayName(userQuality.default_preset) || '应急兼容');
          }
          if (JSON.stringify(userQuality.enabled_presets) !== JSON.stringify(baselineUQ.enabled_presets)) {
            push('可切换预设', baselineUQ.enabled_presets.map(presetDisplayName).join('、'), userQuality.enabled_presets.map(presetDisplayName).join('、'));
          }
          if (userQuality.bandwidth_preset !== baselineUQ.bandwidth_preset) {
            push('带宽预设', tierLabel(baselineUQ.bandwidth_preset), tierLabel(userQuality.bandwidth_preset));
          }
          if (Number(userQuality.max_size_limit) !== Number(baselineUQ.max_size_limit)) {
            push('分辨率上限', capOptionLabel(baselineUQ.max_size_limit), capOptionLabel(userQuality.max_size_limit));
          }
          if (!!userQuality.user_custom_tuning !== !!baselineUQ.user_custom_tuning) {
            push('普通用户微调', baselineUQ.user_custom_tuning ? '允许' : '禁止', userQuality.user_custom_tuning ? '允许' : '禁止');
          }
        }

        var list = $('change-list'), empty = $('change-empty'), cnt = $('change-count'), save = $('btn-save');
        list.innerHTML = '';
        items.forEach(function (it) {
          var row = document.createElement('div');
          row.className = 'change-item';
          row.innerHTML = '<span class="lbl">' + esc(it.label) + '</span><span class="val">' + esc(it.from) + '</span><span class="arrow">→</span><span class="val">' + esc(it.to) + '</span>';
          list.appendChild(row);
        });
        empty.hidden = items.length > 0;
        list.hidden = items.length === 0;
        cnt.textContent = String(items.length);
        var scope = $('change-scope');
        if (scope) {
          scope.textContent = ref.presetId ? (tr('相对预设：') + presetName(ref.presetId)) : '';
          scope.hidden = !ref.presetId;
        }
        if (save) { save.classList.toggle('ready', items.length > 0 || hasUQChanges() || hasPendingPresetSwitch()); }
      }

      /* 当前应用预设的参数（画质三项的参照）；没选预设时就是已保存的全局配置。 */
      function appliedPresetReference() {
        var preset = selectedPresetId ? (findBuiltin(selectedPresetId) || findCustom(selectedPresetId)) : null;
        if (!preset) {
          return {
            presetId: null,
            values: { resMode: baseline.resMode, customChecked: baseline.customChecked, customW: baseline.customW, customH: baseline.customH, fps: baseline.fps, bitrate: baseline.bitrate },
          };
        }
        return {
          presetId: preset.id,
          values: { resMode: preset.width + 'x' + preset.height, customChecked: false, customW: preset.width, customH: preset.height, fps: preset.fps, bitrate: preset.bitrate },
        };
      }

      /* 「应用了预设但还没保存」：不进变更摘要，只用来点亮保存按钮与来源标签。 */
      function hasPendingPresetSwitch() {
        return !!selectedPresetId && String(selectedPresetId) !== String(savedPresetId || '');
      }

      /* ---------- 校验 / 保存 / 重置 ---------- */
      function validate() {
        var ok = true;
        clearFieldErrors();
        var custom = current.customChecked || current.resMode === 'custom';
        if (custom) {
          var w = parseFloat(current.customW), h = parseFloat(current.customH);
          if (!(w >= 320 && w <= 1920 && Math.floor(w) === w)) { setRowError('row-resolution'); ok = false; }
          if (!(h >= 240 && h <= 1920 && Math.floor(h) === h)) { setRowError('row-resolution'); ok = false; }
          if (Math.max(w, h) < 640) { setRowError('row-resolution'); ok = false; }
        }
        var fps = parseFloat(current.fps);
        if (!(fps >= 15 && fps <= 240)) { setRowError('row-fps'); ok = false; }
        var br = parseFloat(current.bitrate);
        if (!(br >= 0.5 && br <= videoBitRateMaxMbps())) { setRowError('row-bitrate'); ok = false; }
        var idle = parseFloat(current.idleStopMin);
        if (!(idle >= 0 && idle <= 1440 && Math.floor(idle) === idle)) { setRowError('row-idle-stop'); ok = false; }
        var sl = parseFloat(current.sessionLimitH);
        if (current.sessionLimitUnit === 'min') {
          if (!(sl >= 0 && sl <= 10080 && Math.floor(sl) === sl)) { setRowError('row-session-limit'); ok = false; }
        } else if (!(sl >= 0 && sl <= 168 && Math.floor(sl) === sl)) { setRowError('row-session-limit'); ok = false; }
        var hiddenMinutes = Number(current.viewerHiddenStopMin);
        if (!(hiddenMinutes >= 5 && hiddenMinutes <= 10 && Math.floor(hiddenMinutes) === hiddenMinutes)) { setRowError('row-viewer-hidden-stop'); ok = false; }
        var blurMinutes = Number(current.viewerBlurStopMin);
        if (!(blurMinutes >= 5 && blurMinutes <= 10 && Math.floor(blurMinutes) === blurMinutes)) { setRowError('row-viewer-blur-stop'); ok = false; }
        return ok;
      }

       $('btn-save').addEventListener('click', function () {
        var items = $('change-list').children.length;
        var uq = hasUQChanges();
        // 「应用了另一个预设但还没保存」也算待保存：它不进变更摘要（那里面只列内容改动）。
        var presetSwitch = hasPendingPresetSwitch();
        if (items === 0 && !uq && !presetSwitch) { showToast(tr('当前没有需要保存的变更'), 'info'); return; }
        if (emergencyFieldsLocked() && (String(baseline.fps) !== String(current.fps) || String(baseline.bitrate) !== String(current.bitrate) || JSON.stringify(effectiveRes(baseline)) !== JSON.stringify(effectiveRes(current)))) {
          showToast(tr('应急兼容预设仅供系统自动回退使用，不可修改或主动选择'), 'error');
          return;
        }
        if (!validate()) { showToast(tr('保存失败，请检查参数'), 'error'); return; }
         if (!window.ScrcpyGateApi.isConfigured('quality.admin.update')) { showToast(tr('接口尚未配置：quality.admin.update'), 'error'); return; }
         var btn = this; btn.classList.add('loading'); btn.disabled = true;
         // 注意：default_preset 只在「普通用户默认预设」真的改过时才提交（uqChangedFields 里）。
         // 以前这里无条件带上它，后端会把 video_profile 一起改写成默认预设，
         // 于是保存后总是回到「稳定」，应用的其他预设丢掉了。
         var body = Object.assign({}, current, uqChangedFields(), { presetId: selectedPresetId || null });
         // 应用了预设、又没单独改「普通用户默认预设」时，把该预设一并设为默认预设：
         // 后端以默认预设为准决定实际画质，不同步的话保存后运行时会继续用旧默认预设，
         // 页面也会跳回旧预设（就是「保存后回到稳定」）。
         if (selectedPresetId && uqChangedFields().default_preset === undefined) body.default_preset = selectedPresetId;
         var selectedPreset = findBuiltin(selectedPresetId) || findCustom(selectedPresetId);
         if (selectedPreset) {
           var presetRes = effectiveRes(current);
           body.presets = {};
           body.presets[selectedPreset.id] = {
             video_bit_rate: Math.round(Number(current.bitrate) * 1000000),
             max_size: Math.max(Number(presetRes.w) || 0, Number(presetRes.h) || 0),
             max_fps: Math.round(Number(current.fps) || 24)
           };
         }
         window.ScrcpyGateApi.configured('quality.admin.update', { method: 'PUT', body: body }).then(function (payload) { applyQualityPayload(payload); showToast(tr('设置已保存'), 'success'); }).catch(function (error) { showToast(apiError(error), 'error'); }).finally(function () { btn.classList.remove('loading'); btn.disabled = false; renderSummary(); });
      });

      function renderActiveConfig() {
        var el = $('active-config-text');
        if (!el) return;
        var modeLabel = baseline.defaultMode || '—';
        var name = selectedPresetId ? presetName(selectedPresetId) : tr('当前配置');
        var res = effectiveRes(baseline);
        el.textContent = qualityState === 'error' ? qualityError : name + ' · ' + (res.w || '—') + '×' + (res.h || '—') + ' · ' + (baseline.fps || '—') + ' fps · ' + (baseline.bitrate || '—') + ' Mbps · ' + modeLabel;
      }

      $('btn-reset').addEventListener('click', function () { resetPresetId = selectedPresetId; openModal('modal-reset'); });
      $('btn-confirm-reset').addEventListener('click', function () {
        if (emergencyFieldsLocked()) { showToast(tr('应急兼容预设仅供系统自动回退使用，不可修改或主动选择'), 'error'); return; }
        if (!window.ScrcpyGateApi.isConfigured('quality.reset')) { showToast(tr('接口尚未配置：quality.reset'), 'error'); return; }
        var btn = this; btn.classList.add('loading'); btn.disabled = true;
        var target = resetPresetId || selectedPresetId;
        if (!target || !findBuiltin(target)) { showToast(tr('请选择一个内置预设后再恢复默认'), 'error'); btn.disabled = false; return; }
        window.ScrcpyGateApi.configured('quality.reset', { method: 'POST', params: { id: target } }).then(function (payload) { resetPresetId = null; applyQualityPayload(payload); closeModal('modal-reset'); showToast(tr('已恢复当前预设默认设置'), 'success'); }).catch(function (error) { showToast(apiError(error), 'error'); }).finally(function () { btn.classList.remove('loading'); btn.disabled = false; });
      });

      /* ---------- 表单事件 ---------- */
      function wireForm() {
        $('sel-resolution').addEventListener('change', function () {
          current.resMode = this.value;
          current.customChecked = this.value === 'custom';
          var box = $('custom-wh-box');
          box.hidden = !current.customChecked;
          $('chk-custom-wh').checked = current.customChecked;
          onManualEdit();
        });
        $('chk-custom-wh').addEventListener('change', function () {
          current.customChecked = this.checked;
          $('custom-wh-box').hidden = !this.checked;
          onManualEdit();
        });
        $('inp-width').addEventListener('input', function () { current.customW = this.value; onManualEdit(); });
        $('inp-height').addEventListener('input', function () { current.customH = this.value; onManualEdit(); });
        $('inp-fps').addEventListener('input', function () { current.fps = this.value; onManualEdit(); });
        $('inp-bitrate').addEventListener('input', function () { current.bitrate = this.value; onManualEdit(); });

        function wireTransport(id, key) {
          var sw = $(id);
          sw.addEventListener('click', function () {
            var on = sw.getAttribute('aria-checked') !== 'true';
            if (on) {
              pendingToggle = { sw: sw, key: key };
              openModal('modal-transport');
            } else {
              setToggleState(sw, key, false);
              syncDefaultModeOptions();
              transportChanged();
            }
          });
        }
        wireTransport('sw-rawv2', 'rawV2');
        wireTransport('sw-proto', 'protoAvail');
        wireTransport('sw-legacy', 'legacyAvail');

        // 传输模式卡片上的「设为默认」与旧下拉共用确认弹窗与保存流程。
        document.querySelectorAll('.mode-default-btn').forEach(function (btn) {
          btn.addEventListener('click', function () {
            var mode = btn.getAttribute('data-mode');
            if (!mode || String(mode) === String(current.defaultMode)) return;
            pendingMode = mode;
            openModal('modal-transport');
          });
        });

        var dm = $('sel-default-mode');
        dm.addEventListener('change', function () {
          if (this.value === current.defaultMode) return;
          pendingMode = this.value;
          openModal('modal-transport');
        });
        $('sel-fullscreen').addEventListener('change', function () { current.fullscreenQuality = this.value; onManualEdit(); });
        $('inp-idle-stop').addEventListener('input', function () { current.idleStopMin = this.value; onManualEdit(); });
        $('sel-idle-unit').addEventListener('change', function () { current.idleStopUnit = this.value; onManualEdit(); });
        $('inp-session-limit').addEventListener('input', function () { current.sessionLimitH = this.value; onManualEdit(); });
        $('sel-session-unit').addEventListener('change', function () { current.sessionLimitUnit = this.value; onManualEdit(); });
        $('chk-viewer-hidden-stop').addEventListener('change', function () {
          current.viewerHiddenStopEnabled = this.checked;
          if (current.viewerStopSettingsSynced) current.viewerBlurStopEnabled = this.checked;
          syncViewerStopControls();
          onManualEdit();
        });
        $('inp-viewer-hidden-stop').addEventListener('input', function () {
          current.viewerHiddenStopMin = this.value;
          if (current.viewerStopSettingsSynced) current.viewerBlurStopMin = this.value;
          syncViewerStopControls();
          onManualEdit();
        });
        $('chk-viewer-blur-stop').addEventListener('change', function () {
          if (current.viewerStopSettingsSynced) return;
          current.viewerBlurStopEnabled = this.checked;
          onManualEdit();
        });
        $('inp-viewer-blur-stop').addEventListener('input', function () {
          if (current.viewerStopSettingsSynced) return;
          current.viewerBlurStopMin = this.value;
          onManualEdit();
        });
        $('chk-viewer-stop-sync').addEventListener('change', function () {
          current.viewerStopSettingsSynced = this.checked;
          if (this.checked) {
            current.viewerBlurStopEnabled = !!current.viewerHiddenStopEnabled;
            current.viewerBlurStopMin = current.viewerHiddenStopMin;
          }
          syncViewerStopControls();
          onManualEdit();
        });
      }

      /* ---------- 传输模式 / 重启 ---------- */
       function transportChanged() {
         if (qualityState !== 'ready') { return; }
         pendingRestart = true;
        var bar = $('restart-bar');
        if (bar) { bar.hidden = false; }
        var wrap = $('status-pending-wrap');
        if (wrap) { wrap.hidden = false; }
        var mode = $('m-mode');
        if (mode) { mode.textContent = current.defaultMode === 'protocol' ? 'protocol' : (current.defaultMode === 'legacy' ? 'legacy' : 'Raw v2'); }
        var err = $('m-error-value');
        if (err) { err.textContent = '待重启'; err.className = 'metric-value warn'; }
        var sub = $('m-error-sub');
        if (sub) { sub.textContent = '传输未重启'; }
        var rec = $('m-recover-value');
        if (rec) { rec.textContent = '待重启'; rec.className = 'metric-value warn'; }
        var recSub = $('m-recover-sub');
        if (recSub) { recSub.textContent = '重启后恢复传输'; }
      }

      $('btn-confirm-transport').addEventListener('click', function () {
        if (!pendingToggle && !pendingMode) { closeModal('modal-transport'); return; }
        if (pendingToggle) {
          var t = pendingToggle;
          pendingToggle = null;
          setToggleState(t.sw, t.key, true);
          syncDefaultModeOptions();
        }
        if (pendingMode) {
          current.defaultMode = pendingMode;
          pendingMode = null;
          var dm = $('sel-default-mode');
          if (dm) { dm.value = current.defaultMode; }
          syncModeCards();
        }
        closeModal('modal-transport');
        transportChanged();
      });

      $('btn-restart-now').addEventListener('click', function () {
        var btn = this;
        btn.classList.add('loading');
        btn.disabled = true;
        var span = btn.querySelector('span');
        if (span) { span.textContent = '重启中…'; }
         if (!window.ScrcpyGateApi.isConfigured('quality.restart')) { btn.classList.remove('loading'); btn.disabled = false; showToast(tr('接口尚未配置：quality.restart'), 'error'); return; }
         window.ScrcpyGateApi.configured('quality.restart', { method: 'POST' }).then(function (payload) { var bar = $('restart-bar'); var d = dataObject(payload); var restarted = Array.isArray(d.restarted) ? d.restarted.length : 0; var deferred = Array.isArray(d.deferred) ? d.deferred.length : 0; var failedRows = Array.isArray(d.failed) ? d.failed : []; var failed = failedRows.length; if (bar && deferred === 0 && failed === 0) { bar.hidden = true; } pendingRestart = deferred > 0 || failed > 0; $('status-pending-wrap').hidden = !pendingRestart; statusData = d.status || d; renderStatus(statusData); if (failed > 0) { var failedNames = failedRows.map(function (row) { return row.device_id || row.deviceId || '未知设备'; }).join('、'); showToast(tr('重启失败 ' + failed + ' 台：' + failedNames), 'error'); } else if (deferred > 0) { showToast(tr('已重启 ' + restarted + ' 台，' + deferred + ' 台因多人观看延迟重启'), 'info'); } else if (restarted > 0) { showToast(tr('传输已重启'), 'success'); } else { showToast(tr('当前没有需要重启的投屏'), 'info'); } }).catch(function (error) { showToast(apiError(error), 'error'); }).finally(function () { btn.classList.remove('loading'); btn.disabled = false; if (span) { span.textContent = tr('立即重启'); } });
      });

      $('btn-restart-later').addEventListener('click', function () {
        var bar = $('restart-bar');
        if (bar) { bar.hidden = true; }
         showToast(tr('已保留待重启状态'), 'info');
      });

      $('btn-refresh-status').addEventListener('click', function () {
        loadQualityStatus(true).catch(function () {});
      });

      /* ---------- 客户端队列与丢帧详情 ---------- */
       $('btn-client-detail').addEventListener('click', function () {
        var btn = this;
        btn.disabled = true;
        loadClients().then(function (clients) {
          var list = $('client-list');
          var html = '';
          clients.forEach(function (c) {
            var waitChip = c.waitKey ? '<span class="client-state warn">' + esc(tr('等待关键帧')) + '</span>' : '';
            var rawState = String(c.state || c.status || '').trim().toLowerCase();
            var state = c.waitKey || c.online === false || ['warn', 'warning', 'error', 'failed', 'failure', 'offline', 'stalled', 'degraded'].indexOf(rawState) >= 0
              ? 'warn'
              : (c.online === true || ['ok', 'online', 'healthy', 'connected', 'ready'].indexOf(rawState) >= 0 ? 'ok' : 'unknown');
            var stateLabel = c.label || c.stateLabel || (state === 'warn' ? tr('异常') : (state === 'ok' ? tr('正常') : tr('未检查')));
            html += '<div class="client-item">' +
              '<span class="client-dot ' + esc(state) + '"></span>' +
              '<span class="client-copy"><b>' + esc(c.name || c.displayName || c.id || '—') + '</b><small>' + esc(tr('连接')) + ' ' + esc(c.conn || c.connection || c.status || '—') + ' · ' + esc(tr('队列')) + ' ' + esc(c.queue == null ? '—' : c.queue) + ' ' + esc(tr('帧')) + ' / ' + esc(c.bytes == null ? '—' : c.bytes) + ' KB · ' + esc(tr('丢帧')) + ' ' + esc(c.drop == null ? '—' : c.drop) + ' ' + esc(tr('帧')) + '</small></span>' +
              '<span class="client-state ' + esc(state) + '">' + esc(stateLabel) + '</span>' +
              waitChip +
            '</div>';
          });
          list.innerHTML = html || '<div class="preset-empty"><i data-lucide="users-round"></i><span>' + esc(tr('暂无客户端数据')) + '</span><small>' + esc(tr('客户端状态将在连接后显示')) + '</small></div>';
          var errPanel = $('client-error-panel');
          var errItem = $('client-error-item');
          var abnormal = clients.filter(function (c) {
            var rawState = String(c.state || c.status || '').trim().toLowerCase();
            return c.waitKey || c.online === false || ['warn', 'warning', 'error', 'failed', 'failure', 'offline', 'stalled', 'degraded'].indexOf(rawState) >= 0;
          });
          if (abnormal.length && errPanel && errItem) {
            errPanel.hidden = false;
            var c = abnormal[0];
            errItem.innerHTML =
              '<div class="cer-grid">' +
                '<div class="cer-cell"><span>' + esc(tr('最近错误时间')) + '</span><b>' + esc(c.errTime || c.errorTime || '—') + '</b></div>' +
                '<div class="cer-cell"><span>' + esc(tr('错误原因')) + '</span><b>' + esc(c.errReason || c.errorReason || '—') + '</b></div>' +
                '<div class="cer-cell"><span>' + esc(tr('恢复状态')) + '</span><b class="cer-state">' + esc(c.recover || c.recovery || '—') + '</b></div>' +
              '</div>' +
              '<div class="cer-progress-row"><span>' + esc(tr('恢复进度')) + '</span><div class="cer-bar"><i style="width:' + Math.max(0, Math.min(100, Number(c.progress || c.recoveryProgress || 0))) + '%"></i></div><b>' + esc(c.progress == null ? (c.recoveryProgress == null ? '—' : c.recoveryProgress) : c.progress) + '%</b></div>';
          } else if (errPanel) {
            errPanel.hidden = true;
          }
          refreshIcons();
          openModal('modal-clients');
        }).catch(function (error) { showToast(apiError(error), 'error'); }).finally(function () { btn.disabled = qualityState === 'error'; });
      });

      /* ---------- 弹窗 ---------- */
      function openModal(id) { var m = $(id); if (m) { m.classList.add('open'); } }
      function closeModal(id) {
        var m = $(id);
        if (m) { m.classList.remove('open'); }
        if (id === 'modal-transport') {
          if (pendingMode) {
            var dm = $('sel-default-mode');
            if (dm) { dm.value = current.defaultMode; }
            pendingMode = null;
          }
          if (pendingToggle) { pendingToggle = null; }
        }
      }

      function presetFormValidate(prefix, out) {
        var ok = true;
        ['name', 'fps', 'bitrate', 'res'].forEach(function (k) {
          var f = $('qfield-' + prefix + '-' + k);
          if (f) { f.classList.remove('has-error'); }
        });
        ['w', 'h'].forEach(function (k) {
          var f = $('qfield-' + prefix + '-' + k);
          if (f) { f.classList.remove('has-error'); }
        });
        var name = $(prefix + '-name').value.trim();
        if (!name) { $('qfield-' + prefix + '-name').classList.add('has-error'); ok = false; }
        var fps = parseFloat($(prefix + '-fps').value);
        if (!(fps >= 15 && fps <= 240)) { $('qfield-' + prefix + '-fps').classList.add('has-error'); ok = false; }
        var br = parseFloat($(prefix + '-bitrate').value);
        if (!(br >= 0.5 && br <= videoBitRateMaxMbps())) { $('qfield-' + prefix + '-bitrate').classList.add('has-error'); ok = false; }
        var resSel = $(prefix + '-res').value;
        var width, height;
        if (resSel === 'custom') {
          width = parseFloat($(prefix + '-w').value);
          height = parseFloat($(prefix + '-h').value);
          if (!(width >= 320 && width <= 1920 && Math.floor(width) === width)) { $('qfield-' + prefix + '-w').classList.add('has-error'); ok = false; }
          if (!(height >= 240 && height <= 1920 && Math.floor(height) === height)) { $('qfield-' + prefix + '-h').classList.add('has-error'); ok = false; }
          if (Math.max(width, height) < 854) { $('qfield-' + prefix + '-w').classList.add('has-error'); $('qfield-' + prefix + '-h').classList.add('has-error'); ok = false; }
        } else {
          var parts = resSel.split('x');
          width = parseInt(parts[0], 10);
          height = parseInt(parts[1], 10);
        }
        out.name = name;
        out.fps = fps;
        out.bitrate = br;
        out.width = width;
        out.height = height;
        out.fullscreen_only = !!($(prefix + '-fullscreen-only') && $(prefix + '-fullscreen-only').checked);
        return ok;
      }

      /* 新增 */
      function openAddPreset() {
        ['name', 'fps', 'bitrate', 'res', 'w', 'h'].forEach(function (k) {
          var f = $('qfield-add-' + k);
          if (f) { f.classList.remove('has-error'); }
        });
        $('add-name').value = '';
        $('add-fps').value = '60';
        $('add-bitrate').value = '8';
        $('add-res').value = '1920x1080';
        $('add-fullscreen-only').checked = false;
        $('add-res-custom').hidden = true;
        openModal('modal-preset-add');
      }
      $('btn-add-preset').addEventListener('click', openAddPreset);
      $('btn-add-preset-empty').addEventListener('click', openAddPreset);
      $('add-res').addEventListener('change', function () {
        $('add-res-custom').hidden = this.value !== 'custom';
      });
      $('btn-confirm-add').addEventListener('click', function () {
        var out = {};
        if (!presetFormValidate('add', out)) { showToast(tr('新增失败，请检查输入'), 'error'); return; }
        if (!window.ScrcpyGateApi.isConfigured('quality.presets.create')) { showToast(tr('接口尚未配置：quality.presets.create'), 'error'); return; }
        var btn = this; btn.disabled = true;
        window.ScrcpyGateApi.configured('quality.presets.create', { method: 'POST', body: { name: out.name, width: out.width, height: out.height, fps: out.fps, bitrate: out.bitrate, fullscreen_only: out.fullscreen_only, maxRes: 'none' } }).then(function () {
          closeModal('modal-preset-add');
          return reloadAfterPresetChange('已创建自定义预设');
        }).catch(function (error) { showToast(apiError(error), 'error'); }).finally(function () { btn.disabled = false; });
      });

      /* 编辑 */
      function openEditPreset(id) {
        var p = findCustom(id);
        if (!p) return;
        editingPresetId = id;
        ['name', 'fps', 'bitrate', 'res', 'w', 'h'].forEach(function (k) {
          var f = $('qfield-edit-' + k);
          if (f) { f.classList.remove('has-error'); }
        });
        $('edit-name').value = p.name;
        $('edit-fps').value = p.fps;
        $('edit-bitrate').value = p.bitrate;
        var resKey = p.width + 'x' + p.height;
        if (RES_OPTIONS.indexOf(resKey) >= 0) {
          $('edit-res').value = resKey;
          $('edit-res-custom').hidden = true;
        } else {
          $('edit-res').value = 'custom';
          $('edit-res-custom').hidden = false;
        }
        $('edit-w').value = p.width;
        $('edit-h').value = p.height;
        $('edit-fullscreen-only').checked = !!p.fullscreenOnly;
        openModal('modal-preset-edit');
      }
      $('edit-res').addEventListener('change', function () {
        $('edit-res-custom').hidden = this.value !== 'custom';
      });
      $('btn-confirm-edit').addEventListener('click', function () {
        if (!editingPresetId) return;
        var out = {};
        if (!presetFormValidate('edit', out)) { showToast(tr('保存失败，请检查输入'), 'error'); return; }
        var p = findCustom(editingPresetId);
        if (!p) { closeModal('modal-preset-edit'); return; }
        if (!window.ScrcpyGateApi.isConfigured('quality.presets.update')) { showToast(tr('接口尚未配置：quality.presets.update'), 'error'); return; }
        var btn = this; btn.disabled = true;
        window.ScrcpyGateApi.configured('quality.presets.update', { params: { id: p.id }, method: 'PUT', body: { name: out.name, width: out.width, height: out.height, fps: out.fps, bitrate: out.bitrate, fullscreen_only: out.fullscreen_only, maxRes: p.maxRes } }).then(function () {
          closeModal('modal-preset-edit'); editingPresetId = null;
          return reloadAfterPresetChange('已更新自定义预设');
        }).catch(function (error) { showToast(apiError(error), 'error'); }).finally(function () { btn.disabled = false; });
      });

      /* 删除 */
      function openDeletePreset(id) {
        var p = findCustom(id);
        if (!p) return;
        deletingPresetId = id;
        $('preset-del-target').innerHTML = '确定删除自定义预设 <b>「' + esc(p.name) + '」</b> 吗？';
        openModal('modal-preset-del');
      }
      $('btn-confirm-del').addEventListener('click', function () {
        if (!deletingPresetId) return;
        if (!window.ScrcpyGateApi.isConfigured('quality.presets.delete')) { showToast(tr('接口尚未配置：quality.presets.delete'), 'error'); return; }
        var btn = this; btn.disabled = true;
        window.ScrcpyGateApi.configured('quality.presets.delete', { params: { id: deletingPresetId }, method: 'DELETE' }).then(function () {
          if (selectedPresetId === deletingPresetId) selectedPresetId = null;
          deletingPresetId = null; closeModal('modal-preset-del');
          return reloadAfterPresetChange('已删除自定义预设');
        }).catch(function (error) { showToast(apiError(error), 'error'); }).finally(function () { btn.disabled = false; });
      });

      /* 预设点击：点卡片=选中预览（不产生待保存变更），点「应用」=写入下方表单。 */
      $('builtin-preset-list').addEventListener('click', function (e) {
        if (e.target.closest('[data-toggle="allowed"]')) return;
        var item = e.target.closest('.preset-item');
        if (!item) return;
        var op = e.target.closest('[data-op]');
        var p = findBuiltin(item.getAttribute('data-preset-id'));
        if (op) {
          var kind = op.getAttribute('data-op');
          if (kind === 'reset') { resetPresetId = item.getAttribute('data-preset-id'); openModal('modal-reset'); return; }
          if (kind === 'apply' && p) { applyPreset(p); }
          return;
        }
        if (p) { highlightPreset(p); }
      });
      $('custom-preset-list').addEventListener('click', function (e) {
        if (e.target.closest('[data-toggle="allowed"]')) return;
        var item = e.target.closest('.preset-item');
        if (!item) return;
        var id = item.getAttribute('data-preset-id');
        var op = e.target.closest('[data-op]');
        var p = findCustom(id);
        if (op) {
          var kind = op.getAttribute('data-op');
          if (kind === 'edit') { openEditPreset(id); }
          else if (kind === 'del') { openDeletePreset(id); }
          else if (kind === 'apply' && p) { applyPreset(p); }
          return;
        }
        if (p) { highlightPreset(p); }
      });

      /* 弹窗通用关闭 */
      document.querySelectorAll('.q-modal-mask').forEach(function (m) {
        m.querySelectorAll('[data-close]').forEach(function (b) {
          b.addEventListener('click', function () { closeModal(m.id); });
        });
        m.addEventListener('click', function (e) { if (e.target === m) { closeModal(m.id); } });
      });
      document.addEventListener('keydown', function (e) {
        if (e.key !== 'Escape') return;
        var openM = document.querySelector('.q-modal-mask.open');
        if (openM) { closeModal(openM.id); }
      });
      /* 预设弹窗：阻止表单默认提交，Enter 触发确认按钮 */
      ['form-preset-add', 'form-preset-edit'].forEach(function (fid) {
        var f = $(fid);
        if (!f) return;
        f.addEventListener('submit', function (e) { e.preventDefault(); });
        f.addEventListener('keydown', function (e) {
          if (e.key !== 'Enter') return;
          e.preventDefault();
          var confirmBtn = fid === 'form-preset-add' ? $('btn-confirm-add') : $('btn-confirm-edit');
          if (confirmBtn) { confirmBtn.click(); }
        });
      });

      /* ---------- 分段切换与诊断折叠 ---------- */
      var SEGMENTS = ['quality', 'transport', 'session'];
      function showSegment(name, moveFocus) {
        if (SEGMENTS.indexOf(name) < 0) name = 'quality';
        var buttons = document.querySelectorAll('.quality-seg');
        buttons.forEach(function (btn) {
          var on = btn.getAttribute('data-seg') === name;
          btn.classList.toggle('active', on);
          btn.setAttribute('aria-selected', on ? 'true' : 'false');
          btn.tabIndex = on ? 0 : -1;
          if (on && moveFocus && typeof btn.focus === 'function') { btn.focus(); }
        });
        document.querySelectorAll('[data-seg-panel]').forEach(function (panel) {
          panel.hidden = panel.getAttribute('data-seg-panel') !== name;
        });
        if (window.history && window.history.replaceState) {
          window.history.replaceState(null, '', '#' + name);
        }
      }
      document.querySelectorAll('.quality-seg').forEach(function (btn, index, all) {
        btn.addEventListener('click', function () { showSegment(btn.getAttribute('data-seg'), false); });
        btn.addEventListener('keydown', function (e) {
          if (['ArrowRight', 'ArrowLeft', 'Home', 'End'].indexOf(e.key) < 0) return;
          e.preventDefault();
          var next = index;
          if (e.key === 'ArrowRight') next = (index + 1) % all.length;
          else if (e.key === 'ArrowLeft') next = (index - 1 + all.length) % all.length;
          else if (e.key === 'Home') next = 0;
          else if (e.key === 'End') next = all.length - 1;
          showSegment(all[next].getAttribute('data-seg'), true);
        });
      });
      window.addEventListener('hashchange', function () { showSegment((location.hash || '').replace('#', ''), false); });
      showSegment((location.hash || '').replace('#', '') || 'quality', false);

      var diagBtn = $('btn-toggle-diagnostics');
      if (diagBtn) {
        diagBtn.addEventListener('click', function () {
          var open = diagBtn.getAttribute('aria-expanded') === 'true';
          diagBtn.setAttribute('aria-expanded', open ? 'false' : 'true');
          diagBtn.classList.toggle('open', !open);
          var box = $('status-diagnostics');
          if (box) { box.hidden = open; }
          refreshIcons();
        });
      }

      var bwConfirmBtn = $('btn-confirm-bandwidth');
      if (bwConfirmBtn) {
        bwConfirmBtn.addEventListener('click', function () {
          if (!pendingBandwidthTier) { closeModal('modal-bandwidth'); return; }
          userQuality.bandwidth_preset = pendingBandwidthTier;
          pendingBandwidthTier = '';
          closeModal('modal-bandwidth');
          renderUserQualityControls();
          renderSummary();
          showToast(tr('已选择 ' + tierLabel(userQuality.bandwidth_preset) + ' 档，保存后自动调整全部预设参数'), 'info');
        });
      }

      /* ---------- 初始化 ---------- */
      bindPresetToggles();
      wireForm();
      renderCustomPresets();
      loadQuality(false).catch(function () {});
      if (window.ScrcpyGateI18n && typeof window.ScrcpyGateI18n.on === 'function') {
        window.ScrcpyGateI18n.on(function () {
          renderCustomPresets();
          renderBuiltinPresets();
          renderUserQualityControls();
          renderStatus(statusData);
          renderSummary();
          renderActiveConfig();
        });
      }
    })();
