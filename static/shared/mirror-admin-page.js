/* 投屏管理页面控制器（Apple Copy 重做）。
   两块内容：
   - 顶部状态条：按功能块开关（设备连接 / 观看端 / 剩余时长 / ALAS / 画质 + 通知中心）；
   - 底部控制栏：一级（控制栏）/ 二级（「更多」菜单）的拖拽编排，并可调整块内顺序。
   开关与编排只影响工作台前端的显示与可用性，不参与任何权限判定。 */
(function () {
  var layoutRoot = document.getElementById('workbench-layout');
  if (!layoutRoot) return;
  var tabs = document.getElementById('workbench-roletabs');
  var statusNode = document.getElementById('workbench-status');
  var saveBtn = document.getElementById('workbench-save');
  var resetBtn = document.getElementById('workbench-reset');

  function tr(value) {
    var text = String(value == null ? '' : value);
    return window.ScrcpyGateI18n ? window.ScrcpyGateI18n.t(text) : text;
  }
  function escapeText(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (ch) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch];
    });
  }
  function iconHtml(name) {
    return name ? '<i data-lucide="' + escapeText(name) + '" aria-hidden="true"></i>' : '';
  }
  function refreshIcons() {
    if (window.lucide && typeof window.lucide.createIcons === 'function') window.lucide.createIcons();
  }

  var ROLE_FALLBACK = { admin: '管理员', user: '普通用户' };
  var GROUP_TEXT = {
    topbar: { label: '顶部状态条', hint: '工作台上方的设备状态条与通知' },
    dock: { label: '底部控制栏', hint: '一级显示在画面下方，二级收在「更多」菜单里' },
    display: { label: '投屏行为', hint: '画面与控制栏的相处方式；只影响观感，不参与任何权限判定' },
  };
  var LEVEL_TEXT = {
    pool: { label: '未启用', hint: '选择位置，将需要的功能加入菜单。' },
    level1: { label: '一级菜单', hint: '常用操作留在控制栏，拖动或上下移动调整顺序。' },
    level2: { label: '更多菜单', hint: '辅助操作放在这里，工作台中点击「更多」打开。' },
  };

  var state = {
    features: {},
    layout: {},
    defaultLayout: {},
    roles: [],
    groups: [],
    items: [],
    activeRole: '',
    dragging: null,
    savedFeatures: null,
    savedLayout: null,
    previewMode: 'desktop',
    previewExpanded: false,
    editorLevel: 'level1',
    filter: '',
    busy: false,
    revision: 0,
  };
  // 打开弹窗前的焦点，关闭时还给它（键盘用户不会掉到页面顶部）
  var modalOpener = null;

  function setStatus(text, tone) {
    if (!statusNode) return;
    statusNode.textContent = text;
    statusNode.setAttribute('data-tone', tone || '');
  }

  function syncStructure() {
    state.roles = (state.roles || []).map(function (role) {
      return { id: String(role.id), label: String(role.label || ROLE_FALLBACK[role.id] || role.id) };
    });
    if (!state.roles.length) {
      state.roles = Object.keys(state.features || {}).map(function (id) {
        return { id: id, label: ROLE_FALLBACK[id] || id };
      });
    }
    state.items = [];
    (state.groups || []).forEach(function (group) {
      (group.items || []).forEach(function (item) {
        if (item && item.id) state.items.push(item);
      });
    });
    if (!state.activeRole || !state.roles.some(function (role) { return role.id === state.activeRole; })) {
      state.activeRole = state.roles.length ? state.roles[0].id : '';
    }
  }

  function roleValues(role) { return (state.features && state.features[role]) || {}; }
  function isOn(role, id) { return roleValues(role)[id] !== false; }
  function itemById(id) {
    for (var index = 0; index < state.items.length; index += 1) {
      if (String(state.items[index].id) === String(id)) return state.items[index];
    }
    return null;
  }
  function topItems(groupId) {
    return state.items.filter(function (item) {
      return String(item.group) === String(groupId) && !item.parent;
    });
  }
  function childrenOf(id) {
    return state.items.filter(function (item) { return String(item.parent || '') === String(id); });
  }
  function dockItems() {
    return state.items.filter(function (item) { return String(item.group) === 'dock'; });
  }
  // 位置锚点（ALAS 入口）与二级容器（更多）没有开关、也不参与一级/二级切换。
  function isFixed(item) { return !!(item.anchor || item.container); }
  function labelOf(item) { return tr((item && (item.label || item.id)) || ''); }
  function hintOf(item) { return item && item.hint ? tr(item.hint) : ''; }

  /* ---------------- 编排数据 ---------------- */
  function defaultLevels(role) {
    var target = role || state.activeRole;
    var fallback = state.defaultLayout && state.defaultLayout[target];
    if (fallback && fallback.level1 && fallback.level1.length) return JSON.parse(JSON.stringify(fallback));
    var level1 = [];
    var level2 = [];
    dockItems().forEach(function (item) {
      if (item.default_enabled !== false) (Number(item.level || 1) === 2 ? level2 : level1).push(String(item.id));
    });
    return { level1: level1, level2: level2 };
  }
  function levels(role) {
    var stored = (state.layout && state.layout[role]) || null;
    if (stored && stored.level1 && stored.level1.length) {
      return { level1: stored.level1.slice(), level2: (stored.level2 || []).slice() };
    }
    // 没有存档的角色必须回落到“该角色自己的”默认，否则会拿当前角色的默认去比。
    return defaultLevels(role);
  }
  function ensureLayout(role) {
    if (!state.layout[role] || !state.layout[role].level1) state.layout[role] = levels(role);
    return state.layout[role];
  }
  function featureLevel(role, id) {
    var current = levels(role);
    if (current.level1.indexOf(id) >= 0) return 'level1';
    if (current.level2.indexOf(id) >= 0) return 'level2';
    return 'pool';
  }
  function allowedLevels(item) {
    var list = item.levels && item.levels.length ? item.levels : [1];
    return list.map(Number);
  }
  function canPlace(item, level) {
    if (!level || level === 'pool') return !isFixed(item);
    if (isFixed(item)) return level === 'level1';
    return allowedLevels(item).indexOf(level === 'level1' ? 1 : 2) >= 0;
  }
  function setFeatureLevel(role, id, target, index) {
    var current = ensureLayout(role);
    current.level1 = current.level1.filter(function (item) { return item !== id; });
    current.level2 = current.level2.filter(function (item) { return item !== id; });
    if (target !== 'pool') {
      var list = target === 'level2' ? current.level2 : current.level1;
      var at = typeof index === 'number' && index >= 0 && index <= list.length ? index : list.length;
      list.splice(at, 0, id);
    }
    var item = itemById(id);
    if (item && !isFixed(item)) state.features[role][id] = target !== 'pool';
    if (state.features[role]) state.features[role].more = current.level2.length > 0;
  }
  function poolIds(role) {
    var current = levels(role);
    return dockItems()
      .filter(function (item) {
        return !isFixed(item)
          && current.level1.indexOf(String(item.id)) < 0
          && current.level2.indexOf(String(item.id)) < 0;
      })
      .map(function (item) { return String(item.id); });
  }

  /* ---------------- 预览（工作台样式） ---------------- */
  // 这几处画的是**工作台那个界面**的样子（状态条胶囊 / 控制栏 / 「更多」菜单），
  // 所以保留工作台的形状，只用一枚「预览」标记说明它不是本页的组件。
  function previewTagHtml() {
    return '<span class="wb-preview-tag">' + escapeText(tr('预览')) + '</span>';
  }
  function partHtml(part) {
    return '<span class="wb-part is-' + escapeText(part.kind || 'value') + '">'
      + escapeText(tr(part.text)) + '</span>';
  }
  function statusPreviewHtml(role) {
    var blocks = childrenOf('status').filter(function (item) { return isOn(role, item.id); });
    var html = '<div class="wb-preview" aria-hidden="true"><div class="wb-preview-inner">';
    if (!isOn(role, 'status') || !blocks.length) {
      html += '<span class="wb-preview-empty">' + escapeText(tr('都不显示')) + '</span>';
    } else {
      blocks.forEach(function (block) {
        html += '<span class="wb-preview-group">';
        (block.preview || []).forEach(function (part) { html += partHtml(part); });
        html += '</span>';
      });
    }
    html += '</div></div>';
    return html;
  }
  function enabledIds(role, level) {
    return levels(role)[level].filter(function (id) {
      var item = itemById(id);
      return item && (isFixed(item) || isOn(role, id));
    });
  }
  function dockChipHtml(item, level) {
    var parts = item.parts || [item.label || item.id];
    return parts.map(function (label, index) {
      var icon = item.id === 'nav' ? ['arrow-left', 'house', 'square'][index] : item.icon;
      return '<span class="wb-dock-item' + (level === 'level2' ? ' is-menu' : '') + '"'
        + ' data-dock-feature="' + escapeText(item.id) + '" title="' + escapeText(tr(label)) + '">'
        + iconHtml(icon) + '<span>' + escapeText(tr(label)) + '</span></span>';
    }).join('');
  }
  function previewHtml(role) {
    return '<div class="wb-dock-preview">'
      + '<div class="wb-preview-heading"><b>' + escapeText(tr('控制栏预览')) + '</b>'
      + '<div class="sg-segments wb-preview-modes" role="group" aria-label="' + escapeText(tr('预览尺寸')) + '">'
      + ['desktop', 'phone'].map(function (mode) {
        return '<button type="button" class="sg-seg' + (state.previewMode === mode ? ' active' : '')
          + '" data-preview-mode="' + mode + '" aria-pressed="' + (state.previewMode === mode) + '">'
          + iconHtml(mode === 'phone' ? 'smartphone' : 'monitor')
          + escapeText(tr(mode === 'phone' ? '手机' : '电脑')) + '</button>';
      }).join('') + '</div></div>'
      + '<div class="wb-preview-stage" data-preview-stage="' + state.previewMode + '" aria-hidden="true">'
      + '<div class="wb-level-shell is-level1 sg-dock-shape" data-preview-dock></div></div>'
      + '<details class="wb-preview-details"' + (state.previewExpanded ? ' open' : '') + '><summary class="wb-preview-heading wb-preview-menu-heading"><b>' + escapeText(tr('更多菜单'))
      + '</b><small data-preview-count></small>' + iconHtml('chevron-down')
      + '</summary><div class="wb-preview-secondary" aria-hidden="true"></div></details></div>';
  }
  function syncPreviews() {
    if (!window.ScrcpyGateWorkbenchDock) return;
    Array.prototype.forEach.call(document.querySelectorAll('.wb-dock-preview'), function (preview) {
      if (!preview.getBoundingClientRect().width) return;
      var dock = preview.querySelector('[data-preview-dock]');
      var stage = preview.querySelector('[data-preview-stage]');
      var secondary = preview.querySelector('.wb-preview-secondary');
      var level2 = enabledIds(state.activeRole, 'level2');
      dock.innerHTML = enabledIds(state.activeRole, 'level1').map(function (id) { return dockChipHtml(itemById(id), 'level1'); }).join('');
      secondary.innerHTML = level2.map(function (id) { return dockChipHtml(itemById(id), 'level2'); }).join('');
      var more = dock.querySelector('[data-dock-feature="more"]');
      if (more) more.hidden = !level2.length || !isOn(state.activeRole, 'more');
      dock.classList.remove('is-compact', 'is-primary-tight');
      var phone = stage.getAttribute('data-preview-stage') === 'phone';
      if (phone || dock.scrollWidth > dock.clientWidth + 1) dock.classList.add('is-compact');
      var moved = 0;
      window.ScrcpyGateWorkbenchDock.overflowCandidates(dock).forEach(function (button) {
        if (dock.scrollWidth <= dock.clientWidth + 1) return;
        button.classList.add('is-menu', 'is-overflow');
        button.setAttribute('data-preview-overflow', '');
        button.title += ' · ' + tr('窄屏自动收纳');
        secondary.appendChild(button);
        moved += 1;
        if (more) more.hidden = false;
      });
      // Release preview-only side space before clipping protected Android keys.
      if (dock.scrollWidth > dock.clientWidth + 1) dock.classList.add('is-primary-tight');
      window.ScrcpyGateWorkbenchDock.syncEdges(dock);
      var count = secondary.querySelectorAll('.wb-dock-item').length;
      preview.querySelector('[data-preview-count]').textContent = String(count) + (moved ? ' · ' + tr('含窄屏收纳') : '');
      if (!count) secondary.innerHTML = '<span class="wb-preview-empty">' + escapeText(tr('暂无功能')) + '</span>';
    });
    refreshIcons();
  }
  var previewFrame = 0;
  function schedulePreviews() {
    if (previewFrame) return;
    previewFrame = window.requestAnimationFrame(function () { previewFrame = 0; syncPreviews(); });
  }

  /* ---------------- 渲染 ---------------- */
  function tabsHtml() {
    var html = '<div class="sg-segments" role="tablist" aria-label="' + escapeText(tr('按角色配置')) + '">';
    state.roles.forEach(function (role) {
      var active = role.id === state.activeRole;
      html += '<button class="sg-seg' + (active ? ' active' : '') + '" type="button" role="tab"'
        + ' id="wb-tab-' + escapeText(role.id) + '" data-role="' + escapeText(role.id) + '"'
        + ' aria-controls="workbench-layout" aria-selected="' + (active ? 'true' : 'false') + '"'
        + ' tabindex="' + (active ? '0' : '-1') + '">'
        + escapeText(tr(role.label)) + '<small>' + escapeText(summaryText(role.id)) + '</small></button>';
    });
    return html + '</div>';
  }

  /* ---------------- 焦点：整页重渲染后要把焦点放回原处 ---------------- */
  // 开关与条目都是重渲染出来的新节点，不主动还原焦点的话键盘用户每点一下就被踢回 body。
  function focusSwitch(role, feature) {
    var button = layoutRoot.querySelector(
      '.sg-switch[data-role="' + String(role) + '"][data-feature="' + String(feature) + '"]');
    if (button) button.focus();
  }
  function focusChip(feature, action) {
    var chip = document.querySelector('#wb-layout-modal .wb-chip[data-feature="' + String(feature) + '"]');
    if (!chip) return;
    var button = action === 'position' ? chip.querySelector('[data-position]')
      : (action ? chip.querySelector('[data-move="' + String(action) + '"]') : null);
    if (button && !button.disabled) { button.focus(); return; }
    var handle = chip.querySelector('[data-drag-handle]');
    if (handle) handle.focus();
  }
  function focusRoleTab(role) {
    var tab = tabs && tabs.querySelector('.sg-seg[data-role="' + String(role) + '"]');
    if (tab) tab.focus();
  }

  function switchHtml(role, item, child) {
    var on = isOn(role, item.id);
    var masterOff = child && !isOn(role, item.parent);
    return '<button type="button" class="sg-switch sg-switch-touch' + (masterOff ? ' is-muted' : '') + '"'
      + ' role="switch" aria-checked="' + (on ? 'true' : 'false') + '"'
      + ' data-role="' + escapeText(role) + '" data-feature="' + escapeText(item.id) + '"'
      + ' aria-label="' + escapeText(tr(ROLE_FALLBACK[role] || role) + ' · ' + labelOf(item)) + '"'
      + (masterOff ? ' disabled' : '') + '><i></i></button>';
  }
  function rowHtml(role, item, child) {
    var on = isOn(role, item.id);
    var masterOff = child && !isOn(role, item.parent);
    return '<div class="wb-row' + (child ? ' is-child' : '') + (on && !masterOff ? '' : ' is-off') + '" data-feature="' + escapeText(item.id) + '">'
      + '<span class="wb-copy">'
      + '<span class="wb-copy-icon">' + iconHtml(item.icon || (child ? 'dot' : 'sliders-horizontal')) + '</span>'
      + '<span class="wb-copy-text"><b>' + escapeText(labelOf(item)) + '</b>'
      + (item.hint ? '<span>' + escapeText(hintOf(item)) + '</span>' : '')
      + '</span></span>'
      + switchHtml(role, item, child)
      + '</div>';
  }

  function statusCardHtml(role) {
    var items = topItems('topbar');
    if (!items.length) return '';
    var html = '<section class="panel wb-card" data-group="topbar" aria-labelledby="wb-card-topbar">'
      + '<div class="panel-header"><h2 class="panel-title" id="wb-card-topbar">'
      + escapeText(tr(GROUP_TEXT.topbar.label)) + previewTagHtml()
      + '<span class="panel-subtitle">' + escapeText(tr(GROUP_TEXT.topbar.hint)) + '</span></h2></div>'
      + statusPreviewHtml(role)
      + '<div class="wb-body">';
    items.forEach(function (item) {
      html += rowHtml(role, item, false);
      var children = childrenOf(item.id);
      if (children.length) {
        html += '<div class="wb-children">'
          + children.map(function (child) { return rowHtml(role, child, true); }).join('') + '</div>';
      }
    });
    return html + '</div></section>';
  }

  // 「投屏行为」：只有开关，不参与底部菜单编排（目录里这些条目没有 levels）。
  function displayCardHtml(role) {
    var items = topItems('display');
    if (!items.length) return '';
    var html = '<section class="panel wb-card" data-group="display" aria-labelledby="wb-card-display">'
      + '<div class="panel-header"><h2 class="panel-title" id="wb-card-display">'
      + escapeText(tr(GROUP_TEXT.display.label))
      + '<span class="panel-subtitle">' + escapeText(tr(GROUP_TEXT.display.hint)) + '</span></h2></div>'
      + '<div class="wb-body">';
    items.forEach(function (item) { html += rowHtml(role, item, false); });
    return html + '</div></section>';
  }

  function dockCardHtml(role) {
    return '<section class="panel wb-card" data-group="dock" aria-labelledby="wb-card-dock">'
      + '<div class="panel-header"><h2 class="panel-title" id="wb-card-dock">' + escapeText(tr('底部控制栏'))
      + '<span class="panel-subtitle">' + escapeText(tr('把常用操作留在一级，其余放入更多。')) + '</span></h2>'
      + '<button class="wb-btn primary" type="button" id="wb-layout-open">' + iconHtml('sliders-horizontal')
      + escapeText(tr('编排菜单')) + '</button></div>' + previewHtml(role)
      + '<div class="wb-card-foot"><span>' + escapeText(tr('两端圆角与外框自动匹配；窄屏优先保留 ALAS 和安卓三键。'))
      + '</span></div></section>';
  }

  function render() {
    syncStructure();
    if (!state.items.length) {
      layoutRoot.setAttribute('aria-busy', 'false');
      layoutRoot.innerHTML = '<p class="wb-placeholder">' + escapeText(tr('没有可配置的功能项')) + '</p>';
      return;
    }
    if (tabs) tabs.innerHTML = tabsHtml();
    layoutRoot.innerHTML = dockCardHtml(state.activeRole) + statusCardHtml(state.activeRole)
      + displayCardHtml(state.activeRole);
    layoutRoot.setAttribute('aria-busy', 'false');
    // tabpanel 与当前标签关联（与 /quality 的 tab/tabpanel 结构一致）
    layoutRoot.setAttribute('aria-labelledby', 'wb-tab-' + state.activeRole);
    renderModal();
    syncPreviews();
    refreshIcons();
  }

  /* ---------------- 编排弹窗 ---------------- */
  function chipHtml(item, level) {
    var id = String(item.id);
    var fixed = isFixed(item);
    var ids = level === 'pool' ? poolIds(state.activeRole) : levels(state.activeRole)[level];
    var at = ids.indexOf(id);
    var sorting = level !== 'pool' && !state.filter;
    var options = ['level1', 'level2', 'pool'].map(function (target) {
      var label = target === 'level1' ? '一级菜单' : (target === 'level2' ? '更多菜单' : '不显示');
      return '<option value="' + target + '"' + (target === level ? ' selected' : '')
        + (!canPlace(item, target) ? ' disabled' : '') + '>' + escapeText(tr(label)) + '</option>';
    }).join('');
    return '<li class="wb-chip" data-feature="' + escapeText(id) + '" data-level="' + level + '"'
      + ' draggable="' + sorting + '">'
      + '<button class="wb-chip-handle" type="button" data-drag-handle'
      + ' aria-label="' + escapeText(tr('拖动排序：') + labelOf(item)) + '" title="' + escapeText(tr('选中条目后用 ↑ / ↓ 调整顺序。')) + '">'
      + iconHtml(sorting ? 'grip-vertical' : (fixed ? 'lock-keyhole' : 'plus')) + '</button>'
      + '<span class="wb-chip-icon">' + iconHtml(item.icon) + '</span>'
      + '<span class="wb-chip-copy"><b>' + escapeText(labelOf(item)) + '</b>'
      + '<small>' + escapeText(hintOf(item)) + '</small></span>'
      + '<span class="wb-chip-actions"><select class="wb-position" data-position'
      + (fixed ? ' disabled' : '') + ' aria-label="' + escapeText(labelOf(item) + ' · ' + tr('所在位置'))
      + '">' + options + '</select>'
      + ['up', 'down'].map(function (direction) {
        return '<button class="wb-order" type="button" data-move="' + direction + '"'
          + ((!sorting || (direction === 'up' ? at <= 0 : at >= ids.length - 1)) ? ' disabled' : '')
          + ' aria-label="' + escapeText(tr(direction === 'up' ? '上移' : '下移') + ' ' + labelOf(item))
          + '" title="' + escapeText(tr(direction === 'up' ? '上移' : '下移')) + '">'
          + iconHtml(direction === 'up' ? 'arrow-up' : 'arrow-down') + '</button>';
      }).join('') + '</span></li>';
  }
  function zoneListHtml(role, level) {
    var ids = level === 'pool' ? poolIds(role) : levels(role)[level];
    var query = state.filter.trim().toLocaleLowerCase();
    var items = ids.map(itemById).filter(function (item) {
      return item && (!query || (labelOf(item) + ' ' + hintOf(item) + ' ' + item.id).toLocaleLowerCase().indexOf(query) >= 0);
    });
    return items.length ? items.map(function (item) { return chipHtml(item, level); }).join('')
      : '<li class="wb-zone-empty">' + escapeText(tr(query ? '没有匹配的功能' : (level === 'pool' ? '全部功能都已启用' : '暂无功能'))) + '</li>';
  }
  function renderModal() {
    var modal = document.getElementById('wb-layout-modal');
    if (!modal) return;
    var level = state.editorLevel;
    var zone = modal.querySelector('[data-zone]');
    zone.setAttribute('data-zone', level);
    var list = zone.querySelector('[data-zone-list]');
    list.setAttribute('data-zone-list', level);
    list.innerHTML = zoneListHtml(state.activeRole, level);
    modal.querySelector('#wb-editor-hint').textContent = tr(state.filter ? '清空搜索后可调整顺序。' : LEVEL_TEXT[level].hint);
    ['pool', 'level1', 'level2'].forEach(function (target) {
      var tab = modal.querySelector('[data-editor-level="' + target + '"]');
      var selected = target === level;
      var count = target === 'pool' ? poolIds(state.activeRole).length : levels(state.activeRole)[target].length;
      tab.classList.toggle('active', selected);
      tab.setAttribute('aria-selected', String(selected));
      tab.tabIndex = selected ? 0 : -1;
      tab.querySelector('small').textContent = String(count);
      list.setAttribute('aria-labelledby', 'wb-editor-tab-' + level);
    });
    var roleNode = modal.querySelector('[data-layout-role]');
    roleNode.textContent = tr(ROLE_FALLBACK[state.activeRole] || state.activeRole);
    modal.querySelector('[data-layout-content]').inert = state.busy;
    var preview = modal.querySelector('[data-editor-preview]');
    preview.innerHTML = previewHtml(state.activeRole);
    syncPreviews();
    refreshIcons();
  }
  function layoutModalHtml() {
    return '<div class="wb-layout-modal" id="wb-layout-modal" hidden>'
      + '<div class="wb-layout-scrim" data-layout-close></div>'
      + '<div class="wb-layout-dialog" role="dialog" aria-modal="true" aria-labelledby="wb-layout-title">'
      + '<header class="wb-layout-head"><div><h2 id="wb-layout-title">' + escapeText(tr('编排底部菜单'))
      + ' <span class="wb-role-label" data-layout-role></span></h2><p>'
      + escapeText(tr('选择所在位置，拖动或点击箭头排序。')) + '</p></div>'
      + '<button class="wb-layout-close" type="button" data-layout-close aria-label="'
      + escapeText(tr('关闭')) + '">' + iconHtml('x') + '</button></header>'
      + '<div class="wb-layout-body" data-layout-content><div data-editor-preview></div>'
      + '<div class="wb-editor-tools"><div class="sg-segments wb-editor-tabs" role="tablist" aria-label="'
      + escapeText(tr('菜单位置')) + '">' + ['level1', 'level2', 'pool'].map(function (level) {
        return '<button type="button" class="sg-seg" role="tab" id="wb-editor-tab-' + level
          + '" data-editor-level="' + level + '" aria-controls="wb-editor-list">'
          + escapeText(tr(LEVEL_TEXT[level].label)) + '<small></small></button>';
      }).join('') + '</div><label class="wb-search">' + iconHtml('search')
      + '<input type="search" id="wb-feature-search" placeholder="' + escapeText(tr('搜索功能'))
      + '" aria-label="' + escapeText(tr('搜索功能')) + '"></label></div>'
      + '<p class="wb-editor-hint" id="wb-editor-hint"></p><section class="wb-zone" data-zone="level1">'
      + '<ul class="wb-zone-list" id="wb-editor-list" role="tabpanel" data-zone-list="level1" aria-describedby="wb-editor-hint"></ul>'
      + '</section></div><footer class="wb-layout-foot"><span>' + escapeText(tr('改动随「保存」一起生效'))
      + '</span><button class="wb-btn primary" type="button" data-layout-close>' + escapeText(tr('完成'))
      + '</button></footer></div></div>';
  }
  function openModal() {
    var modal = document.getElementById('wb-layout-modal');
    if (!modal) return;
    modalOpener = document.activeElement && document.activeElement.focus ? document.activeElement : null;
    modal.hidden = false;
    modal.classList.add('open');
    setStageInert(true);
    renderModal();
    var closeBtn = modal.querySelector('.wb-layout-close');
    if (closeBtn) closeBtn.focus();
  }
  function closeModal() {
    var modal = document.getElementById('wb-layout-modal');
    if (!modal || modal.hidden) return;
    var shouldRestore = modal.contains(document.activeElement) || document.activeElement === document.body;
    modal.hidden = true;
    modal.classList.remove('open');
    setStageInert(false);
    if (!shouldRestore) return;
    if (modalOpener && modalOpener.isConnected) { modalOpener.focus(); return; }
    var openBtn = document.getElementById('wb-layout-open');
    if (openBtn) openBtn.focus();
  }
  // 弹窗打开时把背景（.admin-stage）设为 inert：屏幕阅读器与 Tab 都不会再进到后面的页面，
  // 弹窗挂在 body 上，不在 .admin-stage 里，所以自身不受影响。
  function setStageInert(on) {
    var stage = document.querySelector('.admin-stage');
    if (stage) {
      if (on) stage.setAttribute('inert', '');
      else stage.removeAttribute('inert');
    }
  }
  function isModalOpen() {
    var modal = document.getElementById('wb-layout-modal');
    return !!(modal && !modal.hidden);
  }
  // Tab 循环：inert 之外的顶栏仍在文档里，必须自己把焦点圈在弹窗内。
  function trapFocus(event) {
    var modal = document.getElementById('wb-layout-modal');
    if (!modal || modal.hidden || event.key !== 'Tab') return false;
    var dialog = modal.querySelector('.wb-layout-dialog');
    var focusable = Array.prototype.filter.call(
      dialog.querySelectorAll('button, [href], input, select, textarea, [tabindex]'),
      function (node) { return !node.disabled && node.tabIndex >= 0 && node.getClientRects().length > 0; });
    if (!focusable.length) return false;
    var first = focusable[0];
    var last = focusable[focusable.length - 1];
    var active = document.activeElement;
    if (event.shiftKey && (active === first || !dialog.contains(active))) {
      event.preventDefault();
      last.focus();
      return true;
    }
    if (!event.shiftKey && (active === last || !dialog.contains(active))) {
      event.preventDefault();
      first.focus();
      return true;
    }
    return false;
  }
  function zoneIndexFromEvent(list, event) {
    var chips = Array.prototype.slice.call(list.querySelectorAll('.wb-chip')).filter(function (chip) {
      return chip.getAttribute('data-feature') !== state.dragging;
    });
    for (var index = 0; index < chips.length; index += 1) {
      var rect = chips[index].getBoundingClientRect();
      if (event.clientY < rect.top + rect.height / 2) return index;
    }
    return chips.length;
  }
  function moveByButton(role, id, action) {
    var item = itemById(id);
    if (!item) return;
    if (action === 'pool') { if (canPlace(item, 'pool')) setFeatureLevel(role, id, 'pool'); return; }
    if (action === 'level1' || action === 'level2') {
      if (!canPlace(item, action)) return;
      if (featureLevel(role, id) === action) return;
      setFeatureLevel(role, id, action);
      return;
    }
    if (action === 'up' || action === 'down') {
      if (state.filter) return;
      var level = featureLevel(role, id);
      if (level === 'pool') return;
      var current = ensureLayout(role);
      var list = level === 'level2' ? current.level2 : current.level1;
      var at = list.indexOf(id);
      var to = action === 'up' ? at - 1 : at + 1;
      if (at < 0 || to < 0 || to >= list.length) return;
      list.splice(at, 1);
      list.splice(to, 0, id);
    }
  }

  /* ---------------- 状态与保存 ---------------- */
  function dirty() {
    var roles = state.roles.length ? state.roles : [{ id: 'admin' }, { id: 'user' }];
    var serverFeatures = state.savedFeatures || {};
    var serverLayout = state.savedLayout || {};
    return roles.some(function (role) {
      var roleId = role.id;
      var featuresChanged = state.items.some(function (item) {
        return isOn(roleId, item.id) !== ((serverFeatures[roleId] || {})[item.id] !== false);
      });
      var stored = serverLayout[roleId];
      var baseline = stored && stored.level1 && stored.level1.length ? stored : defaultLevels(roleId);
      var layoutChanged = JSON.stringify(levels(roleId)) !== JSON.stringify({ level1: baseline.level1, level2: baseline.level2 || [] });
      return featuresChanged || layoutChanged;
    });
  }
  function summaryText(role) {
    var current = levels(role);
    return tr('一级菜单') + ' ' + current.level1.length + ' · ' + tr('二级菜单') + ' ' + current.level2.length;
  }
  function syncSaveState() {
    var pending = dirty();
    if (saveBtn) saveBtn.disabled = state.busy || !pending;
    if (resetBtn) resetBtn.disabled = state.busy || !state.items.length;
    var undo = document.getElementById('workbench-undo');
    if (undo) undo.disabled = state.busy || !pending;
    layoutRoot.inert = state.busy;
    if (tabs) tabs.inert = state.busy;
    if (state.busy) { setStatus(tr('保存中…'), ''); return; }
    setStatus(pending ? tr('有未保存的修改') : tr('未修改'), pending ? 'warn' : '');
  }
  function applyPayload(payload) {
    state.features = (payload && payload.features) || {};
    state.layout = (payload && payload.layout) || {};
    state.defaultLayout = (payload && payload.default_layout) || {};
    state.roles = (payload && payload.roles) || [];
    state.groups = (payload && payload.groups) || [];
    syncStructure();
    state.roles.forEach(function (role) { ensureLayout(role.id); });
    state.savedFeatures = JSON.parse(JSON.stringify(state.features));
    state.savedLayout = JSON.parse(JSON.stringify(state.layout));
    render();
    syncSaveState();
  }
  function load() {
    if (!window.ScrcpyGateApi) {
      layoutRoot.setAttribute('aria-busy', 'false');
      layoutRoot.innerHTML = '<p class="wb-placeholder">' + escapeText(tr('数据服务不可用，请刷新页面')) + '</p>';
      return;
    }
    window.ScrcpyGateApi.configured('workbench.features')
      .then(applyPayload)
      .catch(function (error) {
        layoutRoot.setAttribute('aria-busy', 'false');
        layoutRoot.innerHTML = '<p class="wb-placeholder">'
          + escapeText(tr('加载失败') + '：' + (error && error.message ? error.message : tr('未知错误'))) + '</p>';
        setStatus(tr('加载失败'), 'error');
      });
  }
  function submit(endpoint, options, doneText) {
    if (!window.ScrcpyGateApi || state.busy) return;
    state.busy = true;
    state.revision += 1;
    syncSaveState();
    var submitted = JSON.parse(JSON.stringify(options));
    window.ScrcpyGateApi.configured(endpoint, submitted)
      .then(function (payload) {
        state.busy = false;
        applyPayload(payload);
        setStatus(tr(doneText), 'ok');
      })
      .catch(function (error) {
        state.busy = false;
        syncSaveState();
        setStatus(tr('保存失败') + '：' + (error && error.message ? error.message : tr('未知错误')), 'error');
      });
  }
  function edited() {
    state.revision += 1;
    render();
    syncSaveState();
  }

  /* ---------------- 事件 ---------------- */
  layoutRoot.addEventListener('click', function (event) {
    var openBtn = event.target.closest ? event.target.closest('#wb-layout-open') : null;
    if (openBtn) { openModal(); return; }
    var button = event.target.closest ? event.target.closest('.sg-switch') : null;
    if (!button || button.disabled || state.busy) return;
    var role = button.getAttribute('data-role');
    var feature = button.getAttribute('data-feature');
    if (!role || !feature || !state.features[role]) return;
    state.features[role][feature] = state.features[role][feature] === false;
    edited();
    focusSwitch(role, feature);
  });
  if (tabs) {
    tabs.addEventListener('click', function (event) {
      var tab = event.target.closest ? event.target.closest('.sg-seg') : null;
      if (!tab || state.busy) return;
      var role = tab.getAttribute('data-role');
      if (!role || role === state.activeRole) return;
      state.activeRole = role;
      render();
      syncSaveState();
      focusRoleTab(role);
    });
    // role="tab" 约定用左右方向键在标签间移动（与 ARIA APG 一致）
    tabs.addEventListener('keydown', function (event) {
      if (state.busy || (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight')) return;
      if (!state.roles.length) return;
      event.preventDefault();
      var index = state.roles.map(function (role) { return role.id; }).indexOf(state.activeRole);
      var step = event.key === 'ArrowRight' ? 1 : -1;
      var next = state.roles[(index + step + state.roles.length) % state.roles.length];
      state.activeRole = next.id;
      render();
      syncSaveState();
      focusRoleTab(next.id);
    });
  }
  document.addEventListener('click', function (event) {
    var previewMode = event.target.closest ? event.target.closest('[data-preview-mode]') : null;
    if (previewMode) {
      var inEditor = !!previewMode.closest('#wb-layout-modal');
      state.previewMode = previewMode.getAttribute('data-preview-mode');
      render();
      var scope = inEditor ? document.getElementById('wb-layout-modal') : layoutRoot;
      var currentMode = scope.querySelector('[data-preview-mode="' + state.previewMode + '"]');
      if (currentMode) currentMode.focus();
      return;
    }
    var modal = document.getElementById('wb-layout-modal');
    if (!modal || modal.hidden) return;
    if (event.target.closest && event.target.closest('[data-layout-close]')) { closeModal(); return; }
    if (state.busy) return;
    var editorTab = event.target.closest ? event.target.closest('[data-editor-level]') : null;
    if (editorTab) {
      state.editorLevel = editorTab.getAttribute('data-editor-level');
      renderModal();
      editorTab.focus();
      return;
    }
    var moveBtn = event.target.closest ? event.target.closest('[data-move]') : null;
    if (!moveBtn || moveBtn.disabled) return;
    var chip = moveBtn.closest('.wb-chip');
    if (!chip) return;
    var featureId = chip.getAttribute('data-feature');
    var action = moveBtn.getAttribute('data-move');
    moveByButton(state.activeRole, featureId, action);
    edited();
    focusChip(featureId, action);
  });
  document.addEventListener('keydown', function (event) {
    var modal = document.getElementById('wb-layout-modal');
    if (!modal || modal.hidden) return;
    if (trapFocus(event)) return;
    if (event.key === 'Escape') { closeModal(); return; }
    if (state.busy) return;
    var editorTab = event.target.closest ? event.target.closest('[data-editor-level]') : null;
    if (editorTab && ['ArrowLeft', 'ArrowRight', 'Home', 'End'].indexOf(event.key) >= 0) {
      event.preventDefault();
      var order = ['level1', 'level2', 'pool'];
      var next = event.key === 'Home' ? 0 : (event.key === 'End' ? 2 : (order.indexOf(state.editorLevel) + (event.key === 'ArrowRight' ? 1 : 2)) % 3);
      state.editorLevel = order[next];
      renderModal();
      modal.querySelector('[data-editor-level="' + state.editorLevel + '"]').focus();
      return;
    }
    var handle = event.target.closest ? event.target.closest('[data-drag-handle]') : null;
    if (!handle) return;
    if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
    var chip = handle.closest('.wb-chip');
    if (!chip) return;
    event.preventDefault();
    var featureId = chip.getAttribute('data-feature');
    moveByButton(state.activeRole, featureId, event.key === 'ArrowUp' ? 'up' : 'down');
    edited();
    focusChip(featureId, null);
  });
  document.addEventListener('dragstart', function (event) {
    var chip = event.target && event.target.closest ? event.target.closest('.wb-chip') : null;
    if (state.busy || !chip || chip.getAttribute('draggable') !== 'true') return;
    state.dragging = chip.getAttribute('data-feature');
    chip.classList.add('is-dragging');
    if (event.dataTransfer) {
      event.dataTransfer.effectAllowed = 'move';
      try { event.dataTransfer.setData('text/plain', state.dragging); } catch (e) {}
    }
  });
  document.addEventListener('dragend', function () {
    state.dragging = null;
    Array.prototype.slice.call(document.querySelectorAll('.wb-chip.is-dragging')).forEach(function (chip) {
      chip.classList.remove('is-dragging');
    });
    Array.prototype.slice.call(document.querySelectorAll('.wb-zone.is-over')).forEach(function (zone) {
      zone.classList.remove('is-over');
    });
  });
  document.addEventListener('dragover', function (event) {
    var modal = document.getElementById('wb-layout-modal');
    if (!modal || modal.hidden) return;
    var zone = event.target.closest ? event.target.closest('[data-zone]') : null;
    if (!zone) return;
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';
    Array.prototype.slice.call(modal.querySelectorAll('.wb-zone.is-over')).forEach(function (other) {
      if (other !== zone) other.classList.remove('is-over');
    });
    zone.classList.add('is-over');
  });
  document.addEventListener('drop', function (event) {
    var modal = document.getElementById('wb-layout-modal');
    if (!modal || modal.hidden) return;
    var zone = event.target.closest ? event.target.closest('[data-zone]') : null;
    if (!zone) return;
    event.preventDefault();
    var id = state.dragging;
    if (!id) {
      try { id = event.dataTransfer ? event.dataTransfer.getData('text/plain') : ''; } catch (e) { id = ''; }
    }
    var item = itemById(id);
    var target = zone.getAttribute('data-zone');
    if (state.busy || state.filter || !item || !canPlace(item, target)) return;
    var list = zone.querySelector('[data-zone-list]');
    setFeatureLevel(state.activeRole, id, target, list ? zoneIndexFromEvent(list, event) : undefined);
    state.dragging = null;
    edited();
    focusChip(id, null);
  });

  document.addEventListener('change', function (event) {
    var select = event.target.closest ? event.target.closest('[data-position]') : null;
    if (!select || select.disabled || state.busy) return;
    var chip = select.closest('.wb-chip');
    var id = chip.getAttribute('data-feature');
    var target = select.value;
    if (!canPlace(itemById(id), target)) return;
    moveByButton(state.activeRole, id, target);
    state.editorLevel = target;
    edited();
    focusChip(id, 'position');
  });
  document.addEventListener('toggle', function (event) {
    if (event.target.classList && event.target.classList.contains('wb-preview-details')) state.previewExpanded = event.target.open;
  }, true);
  document.addEventListener('input', function (event) {
    if (event.target.id !== 'wb-feature-search' || state.busy) return;
    state.filter = event.target.value;
    renderModal();
  });

  var holder = document.createElement('div');
  holder.innerHTML = layoutModalHtml();
  document.body.appendChild(holder.firstChild);

  if (saveBtn) {
    saveBtn.addEventListener('click', function () {
      if (!dirty()) {
        setStatus(tr('未修改'), '');
        saveBtn.disabled = true;
        return;
      }
      submit('workbench.features.update', {
        method: 'PUT',
        body: { features: state.features, layout: state.layout },
      }, '已保存并立即生效');
    });
  }
  if (resetBtn) {
    resetBtn.addEventListener('click', function () {
      if (state.busy) return;
      var role = state.activeRole;
      state.layout[role] = defaultLevels(role);
      state.features[role] = {};
      state.items.forEach(function (item) {
        if (!item.anchor) state.features[role][item.id] = item.default_enabled !== false;
      });
      edited();
      setStatus(tr(dirty() ? '已重置当前角色，保存后生效' : '当前角色已是推荐配置'), dirty() ? 'warn' : '');
    });
  }
  var undoBtn = document.getElementById('workbench-undo');
  if (undoBtn) undoBtn.addEventListener('click', function () {
    if (state.busy || !dirty()) return;
    state.features = JSON.parse(JSON.stringify(state.savedFeatures));
    state.layout = JSON.parse(JSON.stringify(state.savedLayout));
    edited();
  });
  window.setInterval(function () {
    if (!window.ScrcpyGateApi || state.busy || dirty() || isModalOpen() || state.dragging) return;
    var revision = state.revision;
    window.ScrcpyGateApi.configured('workbench.features').then(function (payload) {
      if (revision !== state.revision || state.busy || dirty() || isModalOpen() || state.dragging) return;
      applyPayload(payload);
    }).catch(function () {});
  }, 60000);
  window.addEventListener('resize', schedulePreviews, { passive: true });
  if (window.ResizeObserver) new ResizeObserver(schedulePreviews).observe(layoutRoot);
  window.addEventListener('beforeunload', function (event) {
    if (dirty()) { event.preventDefault(); event.returnValue = ''; }
  });

  if (window.ScrcpyGateI18n && window.ScrcpyGateI18n.on) window.ScrcpyGateI18n.on(function () {
    render();
    syncSaveState();
  });
  load();
})();
