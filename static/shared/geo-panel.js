/* 地域限制面板（GEO）：/security 页上的可选功能。
   - 模式 off/observe/enforce、允许的国家/地区、无法定位来源的处理、例外网段。
   - 保存前可「预演」：留空即预演管理员**自己当前的来源**（这是启用前最该看的一行）。
   - 被服务端自锁预检拦下（409）时，显示警告并要求勾选确认后才能再次提交。
   - 地区库状态与自动更新：库版本、大小、上次成功、今日下载次数、下次到期、立即检查。
   端点：geo.status / geo.check / geo.simulate / geo.settings.update（设置写入走 /api/admin/settings）。 */
(function () {
  var root = document.querySelector('.geo-card');
  if (!root) return;

  var els = {
    credentialForm: document.getElementById('geo-credentials-form'),
    account: document.getElementById('geo-account-id'),
    key: document.getElementById('geo-license-key'),
    credentialSave: document.getElementById('geo-credentials-save'),
    credentialClear: document.getElementById('geo-credentials-clear'),
    credentialSource: document.getElementById('geo-credentials-source'),
    credentialStatus: document.getElementById('geo-credentials-status'),
    credentialPresence: document.getElementById('geo-credentials-presence'),
    summary: document.getElementById('geo-summary'),
    status: document.getElementById('geo-status'),
    policyNotice: document.getElementById('geo-policy-notice'),
    databaseNote: document.getElementById('geo-database-note'),
    dbStats: document.getElementById('geo-db-stats'),
    mode: document.getElementById('geo-mode'),
    countries: document.getElementById('geo-countries'),
    preset: document.getElementById('geo-country-preset'),
    interval: document.getElementById('geo-interval'),
    scheduleForm: document.getElementById('geo-schedule-form'),
    scheduleSave: document.getElementById('geo-schedule-save'),
    scheduleStatus: document.getElementById('geo-schedule-status'),
    verificationState: document.getElementById('geo-verification-state'),
    verificationTime: document.getElementById('geo-verification-time'),
    unknown: document.getElementById('geo-unknown'),
    cidrs: document.getElementById('geo-cidrs'),
    simIp: document.getElementById('geo-sim-ip'),
    simRun: document.getElementById('geo-sim-run'),
    simResult: document.getElementById('geo-sim-result'),
    check: document.getElementById('geo-check'),
    checkHint: document.getElementById('geo-check-hint'),
    progress: document.getElementById('geo-progress'),
    progressStage: document.getElementById('geo-progress-stage'),
    progressPercent: document.getElementById('geo-progress-percent'),
    progressTrack: document.getElementById('geo-progress-track'),
    progressFill: document.getElementById('geo-progress-fill'),
    progressDetail: document.getElementById('geo-progress-detail'),
    downloadForm: document.getElementById('geo-download-form'),
    downloadCountry: document.getElementById('geo-download-country'),
    downloadCity: document.getElementById('geo-download-city'),
    downloadSave: document.getElementById('geo-download-save'),
    downloadStatus: document.getElementById('geo-download-status'),
    proxyMode: document.getElementById('geo-proxy-mode'),
    proxyField: document.getElementById('geo-proxy-field'),
    proxyUrl: document.getElementById('geo-proxy-url'),
    proxyClear: document.getElementById('geo-proxy-clear'),
    proxySaved: document.getElementById('geo-proxy-saved'),
    runLog: document.getElementById('geo-run-log'),
    logFollow: document.getElementById('geo-log-follow'),
    refresh: document.getElementById('geo-refresh'),
    save: document.getElementById('geo-save'),
    selfWarning: document.getElementById('geo-self-warning'),
    confirmWrap: document.getElementById('geo-confirm-wrap'),
    confirm: document.getElementById('geo-confirm')
  };

  var state = { status: null, seq: 0, loaded: false };

  function api() { return window.ScrcpyGateApi; }
  function t(text) { return window.ScrcpyGateI18n ? window.ScrcpyGateI18n.t(text) : text; }

  function escapeText(value) {
    return String(value === null || value === undefined ? '' : value).replace(/[&<>"']/g, function (ch) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch];
    });
  }

  function num(value) {
    var parsed = Number(value);
    return isFinite(parsed) ? parsed : 0;
  }

  function fmtTime(seconds) {
    var value = num(seconds);
    if (!value) return '—';
    try { return new Date(value * 1000).toLocaleString(); } catch (error) { return '—'; }
  }

  function fmtBytes(bytes) {
    var value = num(bytes);
    if (value <= 0) return '—';
    if (value >= 1024 * 1024) return (value / (1024 * 1024)).toFixed(1) + ' MB';
    if (value >= 1024) return Math.round(value / 1024) + ' KB';
    return value + ' B';
  }

  function setStatus(text, tone) {
    if (!els.status) return;
    els.status.textContent = text || '';
    els.status.setAttribute('data-tone', tone || '');
  }

  function setWarning(text) {
    if (!els.selfWarning) return;
    els.selfWarning.hidden = !text;
    els.selfWarning.textContent = text || '';
  }

  /* 更新进度：阶段名与后端 PROGRESS_STAGES 一一对应；百分比由服务端算好（下载阶段
     按真实字节数换算），前端只负责显示，避免两边各自估算出现不一致。 */
  var progressStages = {
    queued: '排队中…', credentials: '正在验证下载凭据…', download: '正在下载地区库…',
    verify: '正在校验地区库…', activate: '正在启用新地区库…', cleanup: '正在清理旧库…',
    done: '地区库已更新', failed: '本次更新失败'
  };

  function renderProgress(status) {
    if (!els.progress) return;
    var info = status.progress || {};
    var stage = String(info.stage || 'idle');
    var running = status.running === true;
    // 只有任务运行中、刚完成或刚失败才显示：进程重启后进度会回到 idle，不显示过期进度条。
    var visible = running || (stage === 'done' && (status.jobState === 'completed' || status.jobState === 'unchanged'))
      || (stage === 'failed' && status.jobState === 'failed');
    els.progress.hidden = !visible;
    if (!visible) return;
    var percent = Math.max(0, Math.min(100, num(info.percent)));
    var downloaded = num(info.downloadedBytes);
    var total = num(info.totalBytes);
    // 下载阶段但拿不到总长度（上游没给 Content-Length）→ 不确定进度动画。
    var indeterminate = stage === 'download' && total <= 0;
    els.progress.classList.toggle('is-indeterminate', indeterminate);
    els.progress.classList.toggle('is-failed', stage === 'failed');
    var label = progressStages[stage] || '正在更新地区库…';
    if (stage === 'done' && status.jobState === 'unchanged') label = '地区库已是最新版本';
    if (els.progressStage) els.progressStage.textContent = label;
    if (els.progressPercent) els.progressPercent.textContent = indeterminate ? '—' : percent + '%';
    if (els.progressTrack) {
      if (indeterminate) els.progressTrack.removeAttribute('aria-valuenow');
      else els.progressTrack.setAttribute('aria-valuenow', String(percent));
      var accessibleLabel = window.ScrcpyGateI18n ? window.ScrcpyGateI18n.t(label) : label;
      els.progressTrack.setAttribute('aria-valuetext', accessibleLabel + (indeterminate ? '' : ' ' + percent + '%'));
    }
    if (els.progressFill) els.progressFill.style.width = indeterminate ? '' : percent + '%';
    if (els.progressDetail) {
      els.progressDetail.textContent = (info.edition ? info.edition + ' (' + num(info.index) + '/' + num(info.count) + ') · ' : '')
        + (stage === 'download' ? fmtBytes(downloaded) + (total > 0 ? ' / ' + fmtBytes(total) : '') : '');
    }
  }

  function renderLogs(force) {
    if (!els.runLog) return;
    var entries = (state.status && state.status.events || []).slice(-120);
    var signature = entries.length ? entries[0].id + ':' + entries[entries.length - 1].id : 'empty';
    if (!force && state.logSignature === signature) return;
    state.logSignature = signature;
    var oldTop = els.runLog.scrollTop;
    var existing = new Set();
    if (force || !entries.length) els.runLog.replaceChildren();
    Array.prototype.forEach.call(els.runLog.children, function (line) {
      var id = line.getAttribute('data-event-id');
      if (!entries.some(function (entry) { return String(entry.id) === id; })) line.remove();
      else existing.add(id);
    });
    entries.forEach(function (entry) {
      if (existing.has(String(entry.id))) return;
      var label = progressStages[entry.stage] || ({updated:'该数据库已更新', unchanged:'该数据库已是最新版本', connection:'下载连接', error:'本次更新失败'})[entry.stage] || '正在更新地区库…';
      if (entry.stage === 'done') label = '本次检查完成';
      var extra = '';
      if (entry.stage === 'connection') extra = t(({system:'跟随服务器代理', direct:'直接连接', custom:'自定义代理'})[entry.code] || '');
      else if (entry.code) extra = t(errorText(entry.code));
      if (entry.stage === 'download' || entry.stage === 'updated') extra = fmtBytes(entry.downloaded_bytes) + (num(entry.total_bytes) > 0 ? ' / ' + fmtBytes(entry.total_bytes) : '');
      var line = document.createElement('p');
      line.setAttribute('data-event-id', String(entry.id));
      line.setAttribute('data-tone', entry.stage === 'error' || entry.stage === 'failed' ? 'error' : entry.stage === 'updated' || entry.stage === 'unchanged' || entry.stage === 'done' ? 'ok' : '');
      line.textContent = '[' + fmtTime(entry.ts) + ' · ' + num(entry.elapsed_seconds).toFixed(1) + ' s] '
        + (entry.edition ? entry.edition + ' · ' : '') + t(label) + (extra ? ' · ' + extra : '');
      els.runLog.appendChild(line);
    });
    if (!entries.length) {
      var empty = document.createElement('p');
      empty.textContent = t('暂无运行记录，点击「立即检查更新」后可查看下载过程。');
      els.runLog.appendChild(empty);
    }
    els.runLog.scrollTop = els.logFollow && els.logFollow.checked ? els.runLog.scrollHeight : oldTop;
  }

  function renderDownloadSettings(status) {
    if (!els.downloadForm) return;
    var config = status.downloadSettings || { editions: ['GeoLite2-City'], proxy_mode: 'system' };
    if (!state.downloadDirty) {
      els.downloadCountry.checked = config.editions.indexOf('GeoLite2-Country') !== -1;
      els.downloadCity.checked = config.editions.indexOf('GeoLite2-City') !== -1;
      els.proxyMode.value = config.proxy_mode;
      els.proxyUrl.value = config.proxy_url || '';
    }
    Array.prototype.forEach.call(els.downloadForm.elements, function (field) { field.disabled = state.downloadBusy || status.running; });
    els.proxySaved.textContent = config.error ? errorText(config.error)
      : config.proxy_configured ? '代理地址已保存，可直接修改。' : '尚未保存自定义代理地址。';
  }

  function modeLabel(mode) {
    // 注意：off 的中文用「不启用」而不是「关闭」，避免与封禁卡片的关闭按钮共用同一个
    // 词条（i18n 是整段/子串替换，同词不同义会让另一处翻译错位）。
    return mode === 'enforce' ? '强制执行' : mode === 'observe' ? '只观察' : '不启用';
  }

  var updateErrors = {
    downloads_disabled: '已暂停所有地区库下载，请先选择下载内容并保存。',
    download_editions_invalid: '请选择 Country 或 City。',
    download_settings_invalid: '下载选项格式不正确。',
    download_settings_unreadable: '无法读取下载配置，请重新保存或检查文件权限。',
    download_settings_permissions: '下载配置文件权限不安全，请设为仅服务账户可读写（0600）。',
    download_settings_write_failed: '保存下载配置失败，请检查数据目录权限与磁盘空间。',
    proxy_invalid: '代理地址无效，请填写包含端口的 HTTP/HTTPS 地址，不要填写下载镜像链接。',
    proxy_mode_invalid: '请选择有效的下载连接方式。',
    proxy_required: '请填写代理地址，或选择直接连接。',
    proxy_error: '无法使用代理隧道，请检查代理地址、认证信息及 CONNECT 支持。',
    interval_invalid: '更新周期必须为 1–168 小时的整数。',
    database_missing: '尚未下载地区库。', database_expired: '地区库已过期，请检查自动更新。',
    database_unreadable: '无法读取地区库，请检查文件和权限。', database_invalid: '下载的地区库校验失败，旧库未替换。',
    dependency_missing: '缺少地区库读取组件，请更新完整镜像。',
    database_lookup_failed: '地区库查询失败，请检查库状态。',
    unexpected_database_type: '下载的数据库类型不匹配，原有地区库保留。',
    download_too_large: '地区库超过下载大小限制，原有地区库保留。',
    license_key_missing: '请先配置 License Key。', account_id_missing: '请先配置 Account ID。',
    account_id_invalid: 'Account ID 必须为数字（最多 20 位）。', license_key_invalid: '请填写有效的 License Key（最多 256 个字符）。',
    license_key_required: '更换 Account ID 时请同时填写新 Key。', credentials_invalid: '凭据格式不正确。',
    credentials_managed: '凭据由环境变量管理，请在服务器修改或移除环境变量后重启。',
    credentials_unreadable: '无法读取已保存凭据。请重新填写两项或清除后配置。',
    credentials_permissions: '凭据文件权限不安全，请设为仅服务账户可读写（0600）。',
    credentials_write_failed: '保存失败，请检查数据目录写入权限与剩余空间。',
    license_rejected: 'MaxMind 拒绝了凭据，请确认 Account ID、Key 和数据库下载权限。',
    network_error: '无法连接 MaxMind，请检查服务器 HTTPS 出站网络和代理设置。',
    timeout: '下载连接或传输超时，原有地区库保留；可检查代理后重试。', too_soon: '正在短暂冷却，请按倒计时重试。',
    dns_error: '无法解析下载服务器域名，请检查服务器 DNS。',
    tls_error: 'HTTPS 证书校验失败，请检查服务器时间、CA 证书和代理。',
    connection_refused: '下载连接被拒绝，请检查服务器出站规则和代理端口。',
    download_forbidden: '地区库文件下载被拒绝，请重试或检查代理；这不代表 Key 无效。',
    upstream_rate_limited: 'MaxMind 暂时限制下载频率，请稍后重试。',
    upstream_unavailable: '下载服务暂时不可用，请稍后重试。',
    http_error: '下载服务返回异常状态，请查看运行记录后重试。',
    update_io_failed: '地区库文件操作失败，请检查数据目录权限和剩余磁盘空间。',
    download_destination_rejected: '下载重定向地址未通过安全校验，请检查代理或更新服务版本。',
    daily_limit: '已达到今日 30 次尝试上限，请明日重试。', update_in_progress: '更新正在进行，请完成后再修改凭据。',
    updater_disabled: '自动更新已由服务器关闭（GEO_UPDATE_ENABLED=false）。'
  };
  function errorText(code) { return updateErrors[code] || code; }
  function requestError(error) {
    var headers = error && error.detail && error.detail.headers || {};
    return errorText(headers['x-geo-update-error'] || headers['X-Geo-Update-Error'] || '') || (error && error.message) || '未知错误';
  }
  function renderPresence() {
    var status = state.status || {};
    var edited = (els.account && els.account.value.trim()) || (els.key && els.key.value.trim());
    var complete = status.accountIdPresent && status.licenseKeyPresent && !status.credentialsError;
    var partial = status.accountIdPresent || status.licenseKeyPresent || status.credentialsError;
    if (els.credentialPresence) {
      els.credentialPresence.textContent = t(edited ? '有未保存修改' : complete ? '已配置' : partial ? '配置不完整' : '未配置');
      els.credentialPresence.setAttribute('data-tone', !edited && complete ? 'ok' : 'warn');
    }
    if (els.account) els.account.placeholder = t(status.accountIdPresent ? '留空保留现有值' : 'MaxMind Account ID');
    if (els.key) els.key.placeholder = t(status.licenseKeyPresent ? '留空保留现有值' : '填写 License Key');
  }

  function renderCheck() {
    if (!els.check) return;
    var status = state.status || {};
    var remaining = Math.max(0, Math.ceil(((state.retryDeadline || 0) - Date.now()) / 1000));
    var canSaveFirst = state.downloadDirty && ['too_soon', 'downloads_disabled'].indexOf(status.blockedReason) >= 0;
    els.check.disabled = !!(state.checkBusy || state.credentialBusy || state.downloadBusy || status.running
      || !status.licenseKeyPresent || !status.accountIdPresent || (!status.canUpdateNow && !canSaveFirst));
    els.check.textContent = t(status.running ? '正在更新…' : state.downloadDirty ? '保存选项并检查更新' : status.lastError ? '重试更新' : '立即检查更新');
    var hint = state.credentialBusy || state.downloadBusy || state.checkBusy ? t('保存或提交中…')
      : status.running ? t('地区库更新任务运行中…')
      : !status.accountIdPresent ? t(errorText('account_id_missing'))
      : !status.licenseKeyPresent ? t(errorText('license_key_missing'))
      : !status.canUpdateNow && status.blockedReason !== 'too_soon' && !canSaveFirst ? t(errorText(status.blockedReason || ''))
      : state.downloadDirty ? t('检查时会先保存当前下载选项。')
      : status.lastError ? t(errorText(status.lastError))
      : status.jobState === 'completed' ? t('地区库已更新')
      : status.jobState === 'unchanged' ? t('地区库已是最新版本')
      : !status.canUpdateNow ? t(errorText(status.blockedReason || '')) : t('已就绪，可以检查更新。');
    if (remaining && status.blockedReason === 'too_soon' && !state.downloadDirty) {
      hint += ' ' + t('可重试倒计时：') + Math.floor(remaining / 60) + ':' + String(remaining % 60).padStart(2, '0');
    }
    els.check.title = hint;
    if (els.checkHint) {
      els.checkHint.textContent = hint;
      els.checkHint.setAttribute('data-tone', status.lastError ? 'error' : status.running || remaining ? 'warn'
        : status.jobState === 'completed' || status.jobState === 'unchanged' ? 'ok' : '');
    }
  }

  function render() {
    var status = state.status || {};
    if (!state.intervalDirty && els.interval) els.interval.value = status.intervalHours || 12;
    var verificationLabels = {
      unconfigured: '下载权限尚未验证', unverified: '下载权限尚未验证',
      verified: '下载权限已验证', rejected: '凭据被拒绝 · 请检查或更换 Key',
      unavailable: '下载权限尚未验证'
    };
    if (els.verificationState) els.verificationState.textContent = status.credentialsError ? errorText(status.credentialsError)
      : verificationLabels[status.credentialVerification] || verificationLabels.unverified;
    if (els.verificationState) els.verificationState.parentElement.setAttribute('data-tone',
      status.credentialsError || status.credentialVerification === 'rejected' ? 'error'
        : status.credentialVerification === 'verified' ? 'ok'
          : status.credentialVerification === 'unconfigured' ? '' : 'warn');
    if (els.verificationTime) els.verificationTime.textContent = '最近检查：' + fmtTime(status.credentialCheckedTs)
      + ' · 最近验证通过：' + fmtTime(status.credentialVerifiedTs);
    renderPresence();
    var managed = status.credentialsManaged === true;
    [els.account, els.key, els.credentialSave].forEach(function (node) { if (node) node.disabled = managed || state.credentialBusy || status.running; });
    if (els.credentialClear) els.credentialClear.disabled = !status.credentialsSaved || state.credentialBusy || status.running;
    if (els.credentialSource) els.credentialSource.textContent = status.credentialsError ? errorText(status.credentialsError)
      : managed ? '当前使用服务器环境变量，后台无法覆盖。清除仅删除后台保存的副本。'
        : '';
    if (els.summary) {
      var pieces = [modeLabel(status.mode)];
      if (status.forcedOff) pieces.push('已被环境变量强制关闭');
      if (!status.databaseAvailable) pieces.push('无地区库');
      els.summary.textContent = pieces.join(' · ');
      els.summary.setAttribute('data-tone', status.forcedOff ? 'warn' : status.mode === 'enforce'
        ? (status.databaseAvailable ? 'ok' : 'error') : status.mode === 'observe' ? 'warn' : '');
    }
    if (!state.formDirty) {
      if (els.mode) els.mode.value = status.mode || 'off';
      if (els.countries) els.countries.value = (status.allowedCountries || []).join(',');
      syncPreset();
      if (els.unknown) els.unknown.value = status.unknownAction || 'deny';
      if (els.cidrs) els.cidrs.value = (status.allowCidrs || []).join(',');
    }

    if (els.dbStats) {
      var items = [
        ['定位精度', status.cityAvailable ? '国家 / 省份 / 城市' : status.databaseAvailable ? '仅国家' : '不可用', status.cityAvailable ? 'ok' : 'warn'],
        ['库版本', status.databaseEpoch ? fmtTime(status.databaseEpoch) : '—'],
        ['上次成功', fmtTime(status.lastSuccessTs)],
        ['下次自动检查', fmtTime(status.nextDueTs)]
      ];
      els.dbStats.innerHTML = items.map(function (pair) {
        return '<li class="vis-stat" data-tone="' + (pair[2] || '') + '"><b>' + escapeText(pair[1]) + '</b><span>' + escapeText(pair[0]) + '</span></li>';
      }).join('');
    }

    renderCheck();
    if (els.save) els.save.disabled = false;
    renderProgress(status);
    renderDownloadSettings(status);
    renderLogs();

    var notes = [];
    if (status.databaseAvailable && !status.cityAvailable && (!status.downloadSettings || status.downloadSettings.editions.indexOf('GeoLite2-City') !== -1)) notes.push('当前仍使用国家库。点击「立即检查更新」下载城市库后，归属将显示可用的省市信息。');
    if (status.enabled === false) notes.push(errorText("updater_disabled"));
    if (!status.licenseKeyPresent) {
      notes.push('未配置 License Key：不会联网下载，也不会自动更新（已存在的地区库照常使用）。');
    }
    if (!status.accountIdPresent) notes.push('未配置 Account ID：内置更新不会联网。');
    if (status.forcedOff) {
      notes.push('环境变量 GEO_ENFORCE_DISABLED=true 正在强制关闭地域限制（设置里的档位不生效）。');
    }
    if (status.databaseError) {
      notes.push('地区库错误：' + errorText(status.databaseError) + '（强制执行时会按保护策略拒绝请求）。');
    }
    if (status.removedOldDatabases) {
      notes.push('已清理过期地区库 ' + num(status.removedOldDatabases) + ' 个（保留期 ' + num(status.oldDatabaseMaxAgeDays) + ' 天）。');
    }
    if (els.databaseNote) {
      els.databaseNote.textContent = notes.join(' ');
      els.databaseNote.setAttribute('data-tone', status.lastError || (status.databaseError && status.mode === 'enforce') ? 'error'
        : status.databaseError || status.enabled === false || status.forcedOff ? 'warn' : '');
    }
    if (els.policyNotice) {
      var unavailable = !status.databaseAvailable;
      els.policyNotice.hidden = !unavailable && !status.forcedOff;
      els.policyNotice.textContent = status.forcedOff ? '服务器已临时关闭地域限制。这里保存的策略暂不执行。'
        : status.mode === 'enforce' ? '地区库不可用，当前按保护策略拒绝访问。请恢复地区库或从服务器关闭地域限制。'
          : '地区库尚未就绪。请展开「地区库与自动更新」完成配置，再开启地域限制。';
      els.policyNotice.setAttribute('data-tone', status.mode === 'enforce' && !status.forcedOff ? 'error' : '');
    }
  }

  var jobPoll = null;
  var pollFailures = 0;
  var pollExpected = false;
  var pagePaused = false;
  var cooldownTimer = null;

  function scheduleCooldown() {
    if (cooldownTimer !== null) window.clearTimeout(cooldownTimer);
    cooldownTimer = null;
    if (pagePaused || !state.status || state.status.running || state.status.blockedReason !== 'too_soon') return;
    cooldownTimer = window.setTimeout(function () {
      cooldownTimer = null;
      renderCheck();
      if (Date.now() >= state.retryDeadline) load(true);
      else scheduleCooldown();
    }, 1000);
  }

  function clearJobPoll() {
    if (jobPoll !== null) window.clearTimeout(jobPoll);
    jobPoll = null;
  }

  function scheduleJobPoll(delay) {
    clearJobPoll();
    if (!pagePaused) jobPoll = window.setTimeout(function () { load(true); }, delay);
  }

  function load(automatic) {
    if (!api() || pagePaused) return;
    clearJobPoll();
    if (automatic !== true) pollFailures = 0;
    var seq = (state.seq += 1);
    return api().configured('geo.status', { force: true })
      .then(function (status) {
        if (seq !== state.seq) return;
        pollFailures = 0;
        pollExpected = status.running === true;
        state.status = status;
        state.retryDeadline = Date.now() + Math.max(0, num(status.retryAfterSeconds)) * 1000;
        scheduleCooldown();
        var databaseSignature = status.databaseAvailable + ':' + status.databaseType + ':' + status.databaseEpoch + ':' + status.lastSuccessTs;
        if (state.databaseSignature && state.databaseSignature !== databaseSignature) {
          window.dispatchEvent(new CustomEvent('scrcpygate:geo-updated'));
        }
        state.databaseSignature = databaseSignature;
        state.loaded = true;
        render();
        if (status.running) {
          setStatus('地区库更新任务运行中…', '');
          // 运行中 1 秒一次：进度条要跟得上下载；结束后回到单次加载不再轮询。
          scheduleJobPoll(1000);
        } else if (status.jobState === 'completed') setStatus('地区库已更新', 'ok');
        else if (status.jobState === 'unchanged') setStatus('地区库已是最新版本', 'ok');
        else if (status.lastError) setStatus(errorText(status.lastError), 'error');
        else setStatus('', '');
      })
      .catch(function (error) {
        if (seq !== state.seq) return;
        var httpStatus = num(error && error.detail && error.detail.status);
        var transient = !httpStatus || httpStatus === 408 || httpStatus === 429 || httpStatus >= 500;
        var message;
        if (pollExpected && transient && pollFailures < 6) {
          pollFailures += 1;
          var retryAfter = num(error && error.detail && error.detail.retryAfterMs);
          scheduleJobPoll(Math.max(Math.min(30000, 1000 * Math.pow(2, pollFailures)), retryAfter));
          message = '连接暂时中断，正在自动重试；显示的是上次获取的进度。';
        } else {
          message = httpStatus === 401 || httpStatus === 403
            ? '登录已失效或无权读取状态，请重新登录或检查访问权限。'
            : '状态刷新已暂停，请点击「刷新」重试；后台更新可能仍在运行。';
        }
        setStatus(message, 'error');
        if (els.checkHint) {
          els.checkHint.textContent = message;
          els.checkHint.setAttribute('data-tone', 'error');
        }
      });
  }

  window.addEventListener('pagehide', function () {
    pagePaused = true;
    state.seq += 1;
    clearJobPoll();
    if (cooldownTimer !== null) window.clearTimeout(cooldownTimer);
  });
  window.addEventListener('pageshow', function (event) {
    if (!event.persisted) return;
    pagePaused = false;
    load();
  });
  if (window.ScrcpyGateI18n && window.ScrcpyGateI18n.on) {
    window.ScrcpyGateI18n.on(function () { renderProgress(state.status || {}); renderLogs(true); renderCheck(); renderPresence(); });
  }

  function currentForm() {
    return {
      geoMode: (els.mode && els.mode.value) || 'off',
      geoAllowedCountries: (els.countries && els.countries.value || '').replace(/，/g, ',').trim().toUpperCase(),
      geoUnknownAction: (els.unknown && els.unknown.value) || 'deny',
      geoAllowCidrs: (els.cidrs && els.cidrs.value || '').replace(/，/g, ',').trim()
    };
  }

  function save() {
    if (!api()) return;
    var body = currentForm();
    if (els.confirm && els.confirm.checked) body.geoConfirmSelfLock = true;
    if (els.save) els.save.disabled = true;
    setStatus('保存中…', '');
    api().configured('geo.settings.update', { method: 'PUT', body: body })
      .then(function (result) {
        if (result && result.selfLockBlocked) {
          // 服务端判定「启用后连你自己都会被拒绝」：必须让管理员显式确认，不能默默失败。
          setWarning(result.message || '启用后你自己的来源也会被拒绝。请确认后再保存。');
          if (els.confirmWrap) els.confirmWrap.hidden = false;
          setStatus('未保存：需要先确认自锁风险', 'warn');
          return;
        }
        setWarning('');
        state.formDirty = false;
        if (els.confirmWrap) els.confirmWrap.hidden = true;
        if (els.confirm) els.confirm.checked = false;
        if (result && result.status) state.status = result.status;
        render();
        if (result && result.refreshFailed) {
          // 保存成功但状态刷新被拒：说明刚刚把自己锁在外面了，这里必须说清楚怎么恢复。
          setWarning('地域设置已保存，但当前来源已被拒绝访问（这正是自锁）。'
            + '请在服务器上执行 ./deploy.sh --geo-off，或设置 GEO_ENFORCE_DISABLED=true 后重启容器，即可恢复。');
          if (els.confirmWrap) els.confirmWrap.hidden = false;
          setStatus('已保存：当前来源已被拒绝，需用服务器侧命令恢复', 'warn');
          return;
        }
        setStatus('地域设置已保存并立即生效', 'ok');
      })
      .catch(function (error) {
        setStatus('保存失败：' + ((error && error.message) || '未知错误'), 'error');
      })
      .then(function () { if (els.save) els.save.disabled = false; });
  }

  function simulate() {
    if (!api()) return;
    var target = (els.simIp && els.simIp.value || '').trim();
    var body = currentForm();
    body.ip = target || 'auto';
    if (els.simResult) els.simResult.textContent = '预演中…';
    api().configured('geo.preview', { method: 'POST', body: body })
      .then(function (result) {
        var label = result.would === 'allow' ? '放行'
          : result.would === 'observe_only' ? '只观察（不会拦）' : '拒绝';
        var detail = (result.self ? '（这是你当前的来源）' : '') + ' ' + (result.country ? ('国家/地区 ' + result.country) : '无法定位国家');
        if (els.simResult) {
          els.simResult.textContent = result.ip + ' → ' + label + ' ' + detail + ' · ' + result.reason;
          els.simResult.setAttribute('data-tone', result.would === 'deny' ? 'warn' : 'ok');
        }
        if (result.self && result.would === 'deny') {
          setWarning('注意：按当前表单的设置，你自己的来源会被拒绝。请先把你的地址加入允许的国家/地区，或加入例外网段。');
          if (els.confirmWrap) els.confirmWrap.hidden = false;
        }
      })
      .catch(function (error) {
        if (els.simResult) els.simResult.textContent = '预演失败：' + ((error && error.message) || '未知错误');
      });
  }

  async function checkNow() {
    if (!api() || state.checkBusy || state.downloadBusy || state.credentialBusy) return;
    if (els.account.value.trim() || els.key.value.trim()) {
      els.credentialStatus.textContent = t('请先保存已填写的下载凭据，再检查更新。');
      els.credentialStatus.setAttribute('data-tone', 'warn');
      els.credentialSave.focus();
      return;
    }
    if (state.downloadDirty && !await saveDownloadSettings()) return;
    if (state.status && (state.status.running || !state.status.canUpdateNow)) { renderCheck(); return; }
    state.checkBusy = true;
    renderCheck();
    try {
      await api().configured('geo.check', { method: 'POST', body: {} });
      pollExpected = true;
      await load();
    } catch (error) {
      setStatus('更新失败：' + requestError(error), 'error');
      await load();
    } finally { state.checkBusy = false; renderCheck(); }
  }

  function credentialFieldError(field, message) {
    var translated = window.ScrcpyGateI18n ? window.ScrcpyGateI18n.t(message) : message;
    field.setCustomValidity(translated);
    field.setAttribute('aria-invalid', 'true');
    els.credentialStatus.textContent = translated;
    els.credentialStatus.setAttribute('data-tone', 'error');
    field.reportValidity();
    return false;
  }

  function validateCredentials() {
    // Normalize before constraint validation, including when submitting with Enter.
    // Avoid maxlength truncation of pasted credentials; reject overlong values intact.
    [els.account, els.key].forEach(function (field) {
      field.value = field.value.trim();
      field.setCustomValidity('');
      field.removeAttribute('aria-invalid');
    });
    var status = state.status || {};
    if (!els.account.value && !status.accountIdPresent) {
      return credentialFieldError(els.account, '请先配置 Account ID。');
    }
    if (els.account.value && !/^[0-9]{1,20}$/.test(els.account.value)) {
      return credentialFieldError(els.account, 'Account ID 必须为数字（最多 20 位）。');
    }
    if (!els.key.value && !status.licenseKeyPresent) {
      return credentialFieldError(els.key, '请先配置 License Key。');
    }
    if (els.key.value && !/^[A-Za-z0-9_-]{1,256}$/.test(els.key.value)) {
      return credentialFieldError(els.key, '请填写有效的 License Key（最多 256 个字符）。');
    }
    return els.credentialForm.reportValidity();
  }

  async function configureCredentials(clear) {
    if (!api() || state.credentialBusy) return;
    if (clear && !await window.ScrcpyGateSecurity.confirm({ title: '清除下载凭据', message: '清除后台保存的 Account ID 和 Key？', detail: '地区库与地域策略保留。环境变量中的凭据不受影响。', accept: '清除凭据' })) return;
    if (!clear && !validateCredentials()) return;
    var body = clear ? {} : { account_id: els.account.value.trim(), license_key: els.key.value.trim() };
    state.credentialBusy = true;
    render();
    els.credentialStatus.textContent = '保存中…';
    try {
      await api().configured(clear ? 'geo.credentials.clear' : 'geo.credentials.save', { method: clear ? 'DELETE' : 'PUT', body: body });
      els.account.value = '';
      els.key.value = '';
      els.credentialStatus.textContent = clear ? '已清除后台保存的凭据。' : '凭据已保存，可点击「立即检查更新」验证下载权限。';
      els.credentialStatus.setAttribute('data-tone', 'ok');
      await load();
    } catch (error) {
      els.key.value = '';
      els.credentialStatus.textContent = requestError(error);
      els.credentialStatus.setAttribute('data-tone', 'error');
    } finally { state.credentialBusy = false; render(); }
  }
  if (els.credentialForm) els.credentialForm.addEventListener('submit', function (event) { event.preventDefault(); configureCredentials(false); });
  if (els.credentialClear) els.credentialClear.addEventListener('click', function () { configureCredentials(true); });
  [els.account, els.key].forEach(function (field) {
    if (field) field.addEventListener('input', function () {
      field.setCustomValidity('');
      field.removeAttribute('aria-invalid');
      els.credentialStatus.textContent = '';
      els.credentialStatus.removeAttribute('data-tone');
      renderPresence();
    });
  });
  window.addEventListener('pagehide', function () { if (els.key) els.key.value = ''; });
  if (els.logFollow) els.logFollow.addEventListener('change', function () {
    if (els.logFollow.checked) els.runLog.scrollTop = els.runLog.scrollHeight;
  });
  async function saveDownloadSettings() {
    if (!api() || state.downloadBusy || els.downloadSave.disabled) return false;
    var editions = [];
    if (els.downloadCountry.checked) editions.push('GeoLite2-Country');
    if (els.downloadCity.checked) editions.push('GeoLite2-City');
    var body = { editions: editions, proxy_mode: els.proxyMode.value, proxy_url: els.proxyClear.checked ? '' : els.proxyUrl.value.trim(), clear_proxy: els.proxyClear.checked };
    state.downloadBusy = true;
    renderDownloadSettings(state.status || {});
    renderCheck();
    els.downloadStatus.textContent = '保存中…';
    try {
      await api().configured('geo.downloads.save', {method:'PUT', body:body});
      state.downloadDirty = false;
      els.proxyClear.checked = false;
      els.downloadStatus.textContent = '下载选项已保存，下次检查时生效。';
      els.downloadStatus.setAttribute('data-tone', 'ok');
      await load();
      return true;
    } catch (error) {
      els.downloadStatus.textContent = requestError(error);
      els.downloadStatus.setAttribute('data-tone', 'error');
      return false;
    } finally {
      state.downloadBusy = false;
      renderDownloadSettings(state.status || {});
      renderCheck();
    }
  }
  if (els.downloadForm) {
    function downloadsChanged() { state.downloadDirty = true; els.downloadStatus.textContent = ''; renderCheck(); }
    els.downloadForm.addEventListener('input', downloadsChanged);
    els.downloadForm.addEventListener('change', downloadsChanged);
    els.downloadForm.addEventListener('submit', function (event) { event.preventDefault(); saveDownloadSettings(); });
  }

  function syncPreset() {
    if (!els.preset || !els.countries) return;
    var codes = els.countries.value.toUpperCase().split(/[,，]/).map(function (s) { return s.trim(); }).filter(Boolean).sort().join(',');
    els.preset.value = 'custom';
    Array.prototype.forEach.call(els.preset.options, function (option) {
      if (option.value !== 'custom' && option.value.split(',').sort().join(',') === codes) els.preset.value = option.value;
    });
  }
  function policyChanged() {
    state.formDirty = true;
    if (els.confirm) els.confirm.checked = false;
    if (els.confirmWrap) els.confirmWrap.hidden = true;
    setWarning('');
    if (els.simResult) els.simResult.textContent = '';
  }
  if (els.preset) els.preset.addEventListener('change', function () {
    if (els.preset.value !== 'custom') els.countries.value = els.preset.value;
    policyChanged();
    els.countries.focus();
  });
  if (els.countries) els.countries.addEventListener('input', syncPreset);
  if (els.interval) els.interval.addEventListener('input', function () { state.intervalDirty = true; els.scheduleStatus.textContent = ''; });
  if (els.scheduleForm) els.scheduleForm.addEventListener('submit', async function (event) {
    event.preventDefault();
    if (!api() || !els.scheduleForm.reportValidity() || els.scheduleSave.disabled) return;
    els.scheduleSave.disabled = true;
    els.scheduleStatus.textContent = '保存中…';
    try {
      await api().configured('geo.schedule.save', { method: 'PUT', body: { interval_hours: Number(els.interval.value) } });
      state.intervalDirty = false;
      els.scheduleStatus.textContent = '更新周期已保存，无需重启。';
      els.scheduleStatus.setAttribute('data-tone', 'ok');
      await load();
    } catch (error) {
      els.scheduleStatus.textContent = requestError(error);
      els.scheduleStatus.setAttribute('data-tone', 'error');
    } finally { els.scheduleSave.disabled = false; }
  });

  [els.mode, els.countries, els.unknown, els.cidrs].forEach(function (el) {
    if (el) el.addEventListener('input', policyChanged);
  });
  if (els.save) els.save.addEventListener('click', save);
  if (els.simRun) els.simRun.addEventListener('click', simulate);
  if (els.simIp) els.simIp.addEventListener('keydown', function (event) {
    if (event.key === 'Enter') { event.preventDefault(); simulate(); }
  });
  if (els.check) els.check.addEventListener('click', checkNow);
  if (els.refresh) els.refresh.addEventListener('click', load);

  load();
})();
