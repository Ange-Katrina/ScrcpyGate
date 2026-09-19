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
    refresh: document.getElementById('geo-refresh'),
    save: document.getElementById('geo-save'),
    selfWarning: document.getElementById('geo-self-warning'),
    confirmWrap: document.getElementById('geo-confirm-wrap'),
    confirm: document.getElementById('geo-confirm')
  };

  var state = { status: null, seq: 0, loaded: false };

  function api() { return window.ScrcpyGateApi; }

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

  function modeLabel(mode) {
    // 注意：off 的中文用「不启用」而不是「关闭」，避免与封禁卡片的关闭按钮共用同一个
    // 词条（i18n 是整段/子串替换，同词不同义会让另一处翻译错位）。
    return mode === 'enforce' ? '强制执行' : mode === 'observe' ? '只观察' : '不启用';
  }

  var updateErrors = {
    interval_invalid: '更新周期必须为 1–168 小时的整数。',
    database_missing: '尚未下载地区库。', database_expired: '地区库已过期，请检查自动更新。',
    database_unreadable: '无法读取地区库，请检查文件和权限。', database_invalid: '下载的地区库校验失败，旧库未替换。',
    dependency_missing: '缺少地区库读取组件，请更新完整镜像。',
    database_lookup_failed: '地区库查询失败，请检查库状态。',
    license_key_missing: '请先配置 License Key。', account_id_missing: '请先配置 Account ID。',
    account_id_invalid: 'Account ID 必须为数字（最多 20 位）。', license_key_invalid: '请填写有效的 License Key（最多 256 个字符）。',
    license_key_required: '更换 Account ID 时请同时填写新 Key。', credentials_invalid: '凭据格式不正确。',
    credentials_managed: '凭据由环境变量管理，请在服务器修改或移除环境变量后重启。',
    credentials_unreadable: '无法读取已保存凭据。请重新填写两项或清除后配置。',
    credentials_permissions: '凭据文件权限不安全，请设为仅服务账户可读写（0600）。',
    credentials_write_failed: '保存失败，请检查数据目录写入权限与剩余空间。',
    license_rejected: 'MaxMind 拒绝了凭据，请确认 Account ID、Key 和数据库下载权限。',
    network_error: '无法连接 MaxMind，请检查服务器 HTTPS 出站网络和代理设置。',
    timeout: '下载超时，请稍后重试。', too_soon: '检查过于频繁，请等待 10 分钟冷却结束。',
    daily_limit: '已达到今日 30 次尝试上限，请明日重试。', update_in_progress: '更新正在进行，请完成后再修改凭据。',
    updater_disabled: '自动更新已由服务器关闭（GEO_UPDATE_ENABLED=false）。'
  };
  function errorText(code) { return updateErrors[code] || code; }
  function requestError(error) {
    var headers = error && error.detail && error.detail.headers || {};
    return errorText(headers['x-geo-update-error'] || headers['X-Geo-Update-Error'] || '') || (error && error.message) || '未知错误';
  }
  function render() {
    var status = state.status || {};
    if (!state.intervalDirty && els.interval) els.interval.value = status.intervalHours || 12;
    var verificationLabels = {
      unconfigured: '未配置下载凭据', unverified: '凭据已配置 · 待验证',
      verified: '下载权限已验证', rejected: '凭据被拒绝 · 请检查或更换 Key',
      unavailable: '暂时无法验证 · 请检查网络后重试'
    };
    if (els.verificationState) els.verificationState.textContent = status.credentialsError ? errorText(status.credentialsError)
      : verificationLabels[status.credentialVerification] || verificationLabels.unverified;
    if (els.verificationState) els.verificationState.parentElement.setAttribute('data-tone',
      status.credentialsError || status.credentialVerification === 'rejected' ? 'error'
        : status.credentialVerification === 'verified' ? 'ok'
          : status.credentialVerification === 'unconfigured' ? '' : 'warn');
    if (els.verificationTime) els.verificationTime.textContent = '最近检查：' + fmtTime(status.credentialCheckedTs)
      + ' · 最近验证通过：' + fmtTime(status.credentialVerifiedTs);
    var managed = status.credentialsManaged === true;
    [els.account, els.key, els.credentialSave].forEach(function (node) { if (node) node.disabled = managed || state.credentialBusy || status.running; });
    if (els.credentialClear) els.credentialClear.disabled = !status.credentialsSaved || state.credentialBusy || status.running;
    if (els.credentialSource) els.credentialSource.textContent = status.credentialsError ? errorText(status.credentialsError)
      : managed ? '当前使用服务器环境变量，后台无法覆盖。清除仅删除后台保存的副本。'
        : status.licenseKeyPresent ? '已保存下载凭据。Key 不会回显，留空保留。' : '尚未配置下载凭据。';
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
        ['地区库', status.databaseAvailable ? '可用' : '不可用', status.databaseAvailable ? 'ok' : (status.mode === 'enforce' ? 'error' : 'warn')],
        ['库版本', status.databaseEpoch ? fmtTime(status.databaseEpoch) : '—'],
        ['库大小', fmtBytes(status.databaseSizeBytes)],
        ['上次成功', fmtTime(status.lastSuccessTs)],
        ['今日下载', num(status.downloadsToday)],
        ['今日尝试', num(status.attemptsToday) + ' / ' + num(status.maxDownloadsPerDay)],
        ['下次自动检查', fmtTime(status.nextDueTs)]
      ];
      els.dbStats.innerHTML = items.map(function (pair) {
        return '<li class="vis-stat" data-tone="' + (pair[2] || '') + '"><b>' + escapeText(pair[1]) + '</b><span>' + escapeText(pair[0]) + '</span></li>';
      }).join('');
    }

    if (els.check) {
      els.check.disabled = state.credentialBusy || status.running || !status.licenseKeyPresent || !status.accountIdPresent || !status.canUpdateNow;
      var checkHint = state.credentialBusy ? '保存中…' : status.running ? '地区库更新任务运行中…'
        : !status.accountIdPresent ? errorText('account_id_missing')
        : !status.licenseKeyPresent ? errorText('license_key_missing')
        : !status.canUpdateNow ? errorText(status.blockedReason || '')
        : '凭据已配置，可点击「立即检查更新」验证下载权限。';
      els.check.title = checkHint;
      if (!status.running && status.jobState === 'completed') checkHint = '地区库已更新';
      else if (!status.running && status.jobState === 'unchanged') checkHint = '地区库已是最新版本';
      else if (!status.running && status.lastError) checkHint = errorText(status.lastError);
      if (els.checkHint) {
        els.checkHint.textContent = checkHint;
        els.checkHint.setAttribute('data-tone', status.running ? 'warn' : status.lastError ? 'error'
          : status.jobState === 'completed' || status.jobState === 'unchanged' ? 'ok' : '');
      }
    }
    if (els.save) els.save.disabled = false;

    var notes = [];
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
    if (status.lastError) {
      notes.push('上次更新失败：' + errorText(status.lastError) + '（' + fmtTime(status.lastErrorTs) + '）。');
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

  function load() {
    if (!api()) return;
    var seq = (state.seq += 1);
    return api().configured('geo.status', { force: true })
      .then(function (status) {
        if (seq !== state.seq) return;
        state.status = status;
        var databaseSignature = status.databaseAvailable + ':' + status.databaseEpoch + ':' + status.lastSuccessTs;
        if (state.databaseSignature && state.databaseSignature !== databaseSignature) {
          window.dispatchEvent(new CustomEvent('scrcpygate:geo-updated'));
        }
        state.databaseSignature = databaseSignature;
        state.loaded = true;
        render();
        if (jobPoll) window.clearTimeout(jobPoll);
        jobPoll = null;
        if (status.running) {
          setStatus('地区库更新任务运行中…', '');
          jobPoll = window.setTimeout(load, 2000);
        } else if (status.jobState === 'completed') setStatus('地区库已更新', 'ok');
        else if (status.jobState === 'unchanged') setStatus('地区库已是最新版本', 'ok');
        else if (status.lastError) setStatus(errorText(status.lastError), 'error');
      })
      .catch(function (error) {
        if (seq !== state.seq) return;
        if (els.dbStats) els.dbStats.innerHTML = '';
        setStatus('加载地域状态失败：' + ((error && error.message) || '未知错误'), 'error');
      });
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

  function checkNow() {
    if (!api()) return;
    if (els.check) els.check.disabled = true;
    if (els.checkHint) els.checkHint.textContent = '正在检查并下载地区库…';
    setStatus('正在检查并下载地区库…', '');
    api().configured('geo.check', { method: 'POST', body: {} })
      .then(function (result) {
        setStatus('地区库更新任务已提交', '');
        return load();
      })
      .catch(function (error) {
        var message = requestError(error);
        setStatus('更新失败：' + message, 'error');
        if (els.checkHint) els.checkHint.textContent = message;
        return load();
      });
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
    });
  });
  window.addEventListener('pagehide', function () { if (els.key) els.key.value = ''; });

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
