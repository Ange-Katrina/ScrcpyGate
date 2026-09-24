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
  // 键位提示：把 ↑ / ↓ 这类按键包成 <kbd>（实体键的样子由 .wb-kbd-hint 提供）
  function kbdHint(text) {
    return escapeText(text).replace(/[↑↓]/g, function (key) { return '<kbd>' + key + '</kbd>'; });
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
    pool: { label: '未启用', hint: '拖到这里即停用，工作台不显示' },
    level1: { label: '一级菜单（控制栏）', hint: '显示在画面下方的一排按钮' },
    level2: { label: '二级菜单（「更多」）', hint: '收在「更多」弹出菜单里' },
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
    if (fallback && fallback.level1 && fallback.level1.length) return fallback;
    var level1 = [];
    var level2 = [];
    dockItems().forEach(function (item) {
      (Number(item.level || 1) === 2 ? level2 : level1).push(String(item.id));
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
  function dockChipHtml(item, level) {
    var badge = item.small_sample ? '<small>' + escapeText(tr(item.small_sample)) + '</small>' : '';
    return '<span class="wb-dock-item' + (level === 'level2' ? ' is-menu' : '') + '">'
      + iconHtml(item.icon) + '<span>' + escapeText(labelOf(item)) + '</span>' + badge + '</span>';
  }
  function levelShellHtml(role, level) {
    var current = levels(role);
    var ids = level === 'level2' ? current.level2 : current.level1;
    var items = ids.map(itemById).filter(Boolean);
    var html = '<div class="wb-level-shell is-' + level + '" aria-hidden="true">';
    if (!items.length) {
      html += '<span class="wb-preview-empty">' + escapeText(tr('暂无功能')) + '</span>';
    } else {
      items.forEach(function (item) { html += dockChipHtml(item, level); });
    }
    html += '</div>';
    return html;
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
    var button = action ? chip.querySelector('[data-move="' + String(action) + '"]') : null;
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
      + '<div class="panel-header"><h2 class="panel-title" id="wb-card-dock">'
      + escapeText(tr(GROUP_TEXT.dock.label))
      + '<span class="panel-subtitle">' + escapeText(tr(GROUP_TEXT.dock.hint)) + '</span></h2></div>'
      + '<div class="wb-dock-levels">'
      + '<div class="wb-dock-level"><div class="wb-level-head"><b>' + escapeText(tr(LEVEL_TEXT.level1.label)) + previewTagHtml()
      + '</b><small>' + escapeText(tr(LEVEL_TEXT.level1.hint)) + '</small></div>'
      + levelShellHtml(role, 'level1') + '</div>'
      + '<div class="wb-dock-level"><div class="wb-level-head"><b>' + escapeText(tr(LEVEL_TEXT.level2.label)) + previewTagHtml()
      + '</b><small>' + escapeText(tr(LEVEL_TEXT.level2.hint)) + '</small></div>'
      + levelShellHtml(role, 'level2') + '</div>'
      + '</div>'
      + '<div class="wb-card-foot"><span>' + escapeText(
        tr('「更多」显示二级功能；窄屏时也会收纳放不下的一级按钮。')
      ) + '</span>'
      + '<button class="wb-btn ghost" type="button" id="wb-layout-open">'
      + escapeText(tr('编排菜单')) + '</button></div>'
      + '</section>';
  }

  function render() {
    syncStructure();
    if (!state.items.length) {
      layoutRoot.setAttribute('aria-busy', 'false');
      layoutRoot.innerHTML = '<p class="wb-placeholder">' + escapeText(tr('没有可配置的功能项')) + '</p>';
      return;
    }
    if (tabs) tabs.innerHTML = tabsHtml();
    layoutRoot.innerHTML = statusCardHtml(state.activeRole) + dockCardHtml(state.activeRole)
      + displayCardHtml(state.activeRole);
    layoutRoot.setAttribute('aria-busy', 'false');
    // tabpanel 与当前标签关联（与 /quality 的 tab/tabpanel 结构一致）
    layoutRoot.setAttribute('aria-labelledby', 'wb-tab-' + state.activeRole);
    renderModal();
    refreshIcons();
  }

  /* ---------------- 编排弹窗 ---------------- */
  function chipHtml(item, level) {
    var id = String(item.id);
    var fixed = isFixed(item);
    var allowed = allowedLevels(item);
    var actions = '';
    if (fixed) {
      // 固定项既没有层级切换也没有停用：加一个锁形图标，明确这是"固定"而不是"控件缺失"。
      actions = '<span class="wb-chip-fixed">'
        + iconHtml('lock') + escapeText(item.anchor ? tr('位置锚点') : tr('二级容器')) + '</span>';
    } else if (level !== 'pool') {
      // 已在一级/二级：给一个紧凑的层级切换 + 停用。
      var current = featureLevel(state.activeRole, id);
      actions = '<span class="sg-segments wb-chip-seg" role="group" aria-label="' + escapeText(tr('所在层级')) + '">'
        + '<button class="sg-seg' + (current === 'level1' ? ' active' : '') + '" type="button" data-move="level1"'
        + ' aria-pressed="' + (current === 'level1') + '">' + escapeText(tr('一级')) + '</button>'
        + '<button class="sg-seg' + (current === 'level2' ? ' active' : '') + '" type="button" data-move="level2"'
        + ' aria-pressed="' + (current === 'level2') + '">' + escapeText(tr('二级')) + '</button></span>'
        + '<button class="wb-chip-remove" type="button" data-move="pool" title="' + escapeText(tr('停用'))
        + '" aria-label="' + escapeText(tr('停用') + ' ' + labelOf(item)) + '">'
        + '<i data-lucide="x" aria-hidden="true"></i></button>';
    } else {
      // 未启用：只给「放回一级 / 放回二级」，不再出现没有意义的 ×。
      actions = '<span class="sg-segments wb-chip-seg" role="group" aria-label="' + escapeText(tr('启用到')) + '">'
        + '<button class="sg-seg" type="button" data-move="level1">' + escapeText(tr('放到一级')) + '</button>'
        + '<button class="sg-seg" type="button" data-move="level2"'
        + (allowed.indexOf(2) < 0 ? ' disabled' : '') + '>' + escapeText(tr('放到二级')) + '</button></span>';
    }
    return '<li class="wb-chip" data-feature="' + escapeText(id) + '" data-level="' + escapeText(level) + '"'
      + ' draggable="true">'
      + '<button class="wb-chip-handle" type="button" data-drag-handle tabindex="0"'
      + ' aria-label="' + escapeText(tr('拖动排序：') + labelOf(item)) + '">'
      + '<i data-lucide="grip-vertical" aria-hidden="true"></i></button>'
      + '<span class="wb-chip-icon">' + iconHtml(item.icon) + '</span>'
      + '<span class="wb-chip-copy"><b>' + escapeText(labelOf(item)) + '</b>'
      + (item.hint ? '<small>' + escapeText(hintOf(item)) + '</small>' : '') + '</span>'
      + '<span class="wb-chip-actions">' + actions + '</span>'
      + '</li>';
  }
  function zoneListHtml(role, level) {
    var current = levels(role);
    if (level === 'pool') {
      var ids = poolIds(role);
      return ids.length
        ? ids.map(function (id) {
          var item = itemById(id);
          return item ? chipHtml(item, 'pool') : '';
        }).join('')
        : '<p class="wb-zone-empty">' + escapeText(tr('全部功能都已启用')) + '</p>';
    }
    var list = level === 'level2' ? current.level2 : current.level1;
    return list.length
      ? list.map(function (id) {
        var item = itemById(id);
        return item ? chipHtml(item, level) : '';
      }).join('')
      : '<p class="wb-zone-empty">' + escapeText(tr('拖功能到这里')) + '</p>';
  }
  function renderModal() {
    var modal = document.getElementById('wb-layout-modal');
    if (!modal) return;
    ['pool', 'level1', 'level2'].forEach(function (level) {
      var list = modal.querySelector('[data-zone-list="' + level + '"]');
      if (list) list.innerHTML = zoneListHtml(state.activeRole, level);
      var count = modal.querySelector('[data-zone-count="' + level + '"]');
      if (count) {
        var current = levels(state.activeRole);
        var value = level === 'pool' ? poolIds(state.activeRole).length
          : (level === 'level2' ? current.level2.length : current.level1.length);
        count.textContent = String(value);
      }
    });
    var roleNode = modal.querySelector('[data-layout-role]');
    if (roleNode) roleNode.textContent = tr(ROLE_FALLBACK[state.activeRole] || state.activeRole);
    refreshIcons();
  }
  function zoneHtml(level, extraClass) {
    return '<section class="wb-zone' + (extraClass ? ' ' + extraClass : '') + '" data-zone="' + level + '">'
      + '<div class="wb-zone-title"><b>' + escapeText(tr(LEVEL_TEXT[level].label))
      + ' <span data-zone-count="' + level + '">0</span></b>'
      + '<small>' + escapeText(tr(LEVEL_TEXT[level].hint)) + '</small></div>'
      + '<ul class="wb-zone-list' + (level === 'level1' ? ' is-dock' : '') + '" data-zone-list="' + level + '"></ul>'
      + '</section>';
  }
  function layoutModalHtml() {
    return '<div class="wb-layout-modal" id="wb-layout-modal" hidden>'
      + '<div class="wb-layout-scrim" data-layout-close></div>'
      + '<div class="wb-layout-dialog" role="dialog" aria-modal="true" aria-labelledby="wb-layout-title">'
      + '<header class="wb-layout-head"><div>'
      + '<h2 id="wb-layout-title">' + escapeText(tr('编排底部菜单')) + '</h2>'
      + '<p>' + escapeText(tr('把功能拖到一级或二级即启用，拖回左侧即停用；也可以用每项后面的按钮。'))
      + ' ' + escapeText(tr('当前角色：')) + '<b data-layout-role></b></p>'
      + '<p class="wb-kbd-hint">' + kbdHint(tr('选中条目后用 ↑ / ↓ 调整顺序。')) + '</p></div>'
      + '<button class="wb-layout-close" type="button" data-layout-close aria-label="'
      + escapeText(tr('关闭')) + '"><i data-lucide="x" aria-hidden="true"></i></button></header>'
      + '<div class="wb-layout-body">' + zoneHtml('pool')
      + '<div class="wb-zone-col">' + zoneHtml('level1') + zoneHtml('level2') + '</div></div>'
      + '<footer class="wb-layout-foot"><span>' + escapeText(tr('改动随「保存」一起生效'))
      + '</span><button class="wb-btn primary" type="button" data-layout-close>' + escapeText(tr('完成')) + '</button></footer>'
      + '</div></div>';
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
    var chips = Array.prototype.slice.call(list.querySelectorAll('.wb-chip'));
    for (var index = 0; index < chips.length; index += 1) {
      var rect = chips[index].getBoundingClientRect();
      if (event.clientY < rect.top + rect.height / 2) return index;
    }
    return chips.length;
  }
  function moveByButton(role, id, action) {
    var item = itemById(id);
    if (!item) return;
    if (action === 'pool') { setFeatureLevel(role, id, 'pool'); return; }
    if (action === 'level1' || action === 'level2') {
      if (!canPlace(item, action)) return;
      if (featureLevel(role, id) === action) return;
      setFeatureLevel(role, id, action);
      return;
    }
    if (action === 'up' || action === 'down') {
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
      var baseline = stored && stored.level1 && stored.level1.length ? stored : levels(roleId);
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
    if (saveBtn) saveBtn.disabled = !pending;
    setStatus(pending ? tr('有未保存的修改') : tr('未修改'), pending ? 'warn' : '');
  }
  function applyPayload(payload) {
    state.features = (payload && payload.features) || {};
    state.layout = (payload && payload.layout) || {};
    state.defaultLayout = (payload && payload.default_layout) || {};
    state.roles = (payload && payload.roles) || [];
    state.groups = (payload && payload.groups) || [];
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
    if (!window.ScrcpyGateApi) return;
    if (saveBtn) saveBtn.disabled = true;
    setStatus(tr('保存中…'), '');
    window.ScrcpyGateApi.configured(endpoint, options)
      .then(function (payload) {
        applyPayload(payload);
        setStatus(tr(doneText), 'ok');
      })
      .catch(function (error) {
        setStatus(tr('保存失败') + '：' + (error && error.message ? error.message : tr('未知错误')), 'error');
        if (saveBtn) saveBtn.disabled = !dirty();
      });
  }

  /* ---------------- 事件 ---------------- */
  layoutRoot.addEventListener('click', function (event) {
    var openBtn = event.target.closest ? event.target.closest('#wb-layout-open') : null;
    if (openBtn) { openModal(); return; }
    var button = event.target.closest ? event.target.closest('.sg-switch') : null;
    if (!button || button.disabled) return;
    var role = button.getAttribute('data-role');
    var feature = button.getAttribute('data-feature');
    if (!role || !feature || !state.features[role]) return;
    state.features[role][feature] = state.features[role][feature] === false;
    render();
    syncSaveState();
    focusSwitch(role, feature);
  });
  if (tabs) {
    tabs.addEventListener('click', function (event) {
      var tab = event.target.closest ? event.target.closest('.sg-seg') : null;
      if (!tab) return;
      var role = tab.getAttribute('data-role');
      if (!role || role === state.activeRole) return;
      state.activeRole = role;
      render();
      syncSaveState();
      focusRoleTab(role);
    });
    // role="tab" 约定用左右方向键在标签间移动（与 ARIA APG 一致）
    tabs.addEventListener('keydown', function (event) {
      if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
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
    var modal = document.getElementById('wb-layout-modal');
    if (!modal || modal.hidden) return;
    if (event.target.closest && event.target.closest('[data-layout-close]')) { closeModal(); return; }
    var moveBtn = event.target.closest ? event.target.closest('[data-move]') : null;
    if (!moveBtn || moveBtn.disabled) return;
    var chip = moveBtn.closest('.wb-chip');
    if (!chip) return;
    var featureId = chip.getAttribute('data-feature');
    var action = moveBtn.getAttribute('data-move');
    moveByButton(state.activeRole, featureId, action);
    render();
    syncSaveState();
    focusChip(featureId, action);
  });
  document.addEventListener('keydown', function (event) {
    var modal = document.getElementById('wb-layout-modal');
    if (!modal || modal.hidden) return;
    if (trapFocus(event)) return;
    if (event.key === 'Escape') { closeModal(); return; }
    var handle = event.target.closest ? event.target.closest('[data-drag-handle]') : null;
    if (!handle) return;
    if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
    var chip = handle.closest('.wb-chip');
    if (!chip) return;
    event.preventDefault();
    var featureId = chip.getAttribute('data-feature');
    moveByButton(state.activeRole, featureId, event.key === 'ArrowUp' ? 'up' : 'down');
    render();
    syncSaveState();
    focusChip(featureId, null);
  });
  document.addEventListener('dragstart', function (event) {
    var chip = event.target && event.target.closest ? event.target.closest('.wb-chip') : null;
    if (!chip || chip.getAttribute('draggable') !== 'true') return;
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
    if (!item || !canPlace(item, target)) return;
    var list = zone.querySelector('[data-zone-list]');
    setFeatureLevel(state.activeRole, id, target, list ? zoneIndexFromEvent(list, event) : undefined);
    state.dragging = null;
    render();
    syncSaveState();
    focusChip(id, null);
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
      submit('workbench.features.reset', { method: 'POST', body: {} }, '已恢复默认（全部开启）');
    });
  }
  window.setInterval(function () {
    // 弹窗开着或正在拖拽时不要重渲染：会把用户手里的节点换掉（拖拽中断、焦点丢失）。
    if (!window.ScrcpyGateApi || dirty() || isModalOpen() || state.dragging) return;
    window.ScrcpyGateApi.configured('workbench.features').then(applyPayload).catch(function () {});
  }, 60000);

  load();
})();
