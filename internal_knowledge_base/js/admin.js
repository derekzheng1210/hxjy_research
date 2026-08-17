(function () {
  'use strict';

  const MODULE_BASE = '/internal-knowledge-base';
  const CSRF_TOKEN = document.querySelector('meta[name="csrf-token"]')?.content || '';
  function apiFetch(url, options = {}) {
    const target = url.startsWith('/api/') ? MODULE_BASE + url : url;
    const method = String(options.method || 'GET').toUpperCase();
    const headers = new Headers(options.headers || {});
    if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) headers.set('X-CSRF-Token', CSRF_TOKEN);
    return window.fetch(target, { ...options, headers });
  }

  const { categories, themes } = window.InternalLibraryData;

  // ---- API ----
  const API = {
    login: (password) => apiFetch('/api/admin/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin', body: JSON.stringify({ password }) }).then(handleJson),
    logout: () => apiFetch('/api/admin/logout', { method: 'POST', credentials: 'same-origin' }).then(handleJson),
    status: () => apiFetch('/api/admin/status', { credentials: 'same-origin' }).then(handleJson),
    stats: () => apiFetch('/api/admin/stats', { credentials: 'same-origin' }).then(handleJson),
    reports: () => apiFetch('/api/admin/reports', { credentials: 'same-origin' }).then(handleJson),
    updateReport: (id, data) => apiFetch(`/api/admin/reports/${id}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin', body: JSON.stringify(data) }).then(handleJson),
    trashReport: (id) => apiFetch(`/api/admin/reports/${id}/trash`, { method: 'POST', credentials: 'same-origin' }).then(handleJson),
    restoreReport: (id) => apiFetch(`/api/admin/reports/${id}/restore`, { method: 'POST', credentials: 'same-origin' }).then(handleJson),
    deleteReportPermanently: (id) => apiFetch(`/api/admin/reports/${id}`, { method: 'DELETE', credentials: 'same-origin' }).then(handleJson),
    resetScores: (id) => apiFetch(`/api/admin/reports/${id}/reset-scores`, { method: 'POST', credentials: 'same-origin' }).then(handleJson),
    users: () => apiFetch('/api/admin/users', { credentials: 'same-origin' }).then(handleJson),
    createUser: (data) => apiFetch('/api/admin/users', { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin', body: JSON.stringify(data) }).then(handleJson),
    updateUser: (id, data) => apiFetch(`/api/admin/users/${id}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin', body: JSON.stringify(data) }).then(handleJson),
    deleteUser: (id) => apiFetch(`/api/admin/users/${id}`, { method: 'DELETE', credentials: 'same-origin' }).then(handleJson),
    resetPassword: (id, password) => apiFetch(`/api/admin/users/${id}/reset-password`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin', body: JSON.stringify({ password }) }).then(handleJson),
    clearQaHistory: (id) => apiFetch(`/api/admin/users/${id}/clear-qa-history`, { method: 'POST', credentials: 'same-origin' }).then(handleJson),
    reminderConfig: () => apiFetch('/api/admin/reminder-config', { credentials: 'same-origin' }).then(handleJson),
    saveReminderConfig: (data) => apiFetch('/api/admin/reminder-config', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin', body: JSON.stringify(data) }).then(handleJson),
    knowledgeConfig: () => apiFetch('/api/admin/knowledge-config', { credentials: 'same-origin' }).then(handleJson),
    saveKnowledgeConfig: (data) => apiFetch('/api/admin/knowledge-config', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin', body: JSON.stringify(data) }).then(handleJson),
    changePassword: (data) => apiFetch('/api/admin/change-password', { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin', body: JSON.stringify(data) }).then(handleJson),
    pdfCache: () => apiFetch('/api/admin/pdf-cache', { credentials: 'same-origin' }).then(handleJson),
    deletePdfCache: (key) => apiFetch(`/api/admin/pdf-cache/${key}`, { method: 'DELETE', credentials: 'same-origin' }).then(handleJson),
    deleteReportPdfCache: (id) => apiFetch(`/api/admin/reports/${id}/pdf-cache`, { method: 'DELETE', credentials: 'same-origin' }).then(handleJson),
    clearPdfCache: () => apiFetch('/api/admin/pdf-cache', { method: 'DELETE', credentials: 'same-origin' }).then(handleJson),
  };

  async function handleJson(res) {
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `请求失败 (${res.status})`);
    return data;
  }

  const state = {
    view: 'overview',
    reports: [],
    users: [],
    stats: null,
    filters: { query: '', category: '', theme: '', status: 'active' },
    reminderConfig: { period: '', reportCategory: 'deep', rules: [] },
    knowledgeConfig: { memberLimit: 10, leaderLimit: 100 },
    pdfCache: { items: [], count: 0, totalSizeBytes: 0 }
  };

  const els = {};
  const shortDateFormatter = new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' });

  function escapeHTML(value = '') {
    return String(value).replace(/[&<>'"]/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));
  }

  function formatDate(value) {
    if (!value) return '—';
    const date = new Date(value.length === 10 ? `${value}T00:00:00` : value);
    return Number.isNaN(date.getTime()) ? value : shortDateFormatter.format(date).replaceAll('/', '.');
  }

  function formatDateTime(value) {
    if (!value) return '—';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    const pad = n => String(n).padStart(2, '0');
    return `${date.getFullYear()}.${pad(date.getMonth() + 1)}.${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
  }

  function toDatetimeLocal(value) {
    if (!value) return '';
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return '';
    const pad = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  function roleLabel(role) {
    return ({ leader: '领导', admin: '行政', member: '研究人员' })[role] || role;
  }

  function categoryLabel(category) {
    return categories[category]?.label || category || '—';
  }

  function themeLabel(theme) {
    return themes[theme]?.label || theme || '—';
  }

  function notify(message, type = 'success') {
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = `<span class="toast-symbol">${type === 'success' ? '✓' : type === 'error' ? '!' : 'i'}</span><span>${escapeHTML(message)}</span>`;
    els.toastStack.appendChild(toast);
    setTimeout(() => toast.classList.add('toast-leave'), 2800);
    setTimeout(() => toast.remove(), 3150);
  }

  // ---- Modal ----
  function openModal(html) {
    els.modalLayer.className = 'modal-layer open';
    els.modalLayer.setAttribute('aria-hidden', 'false');
    els.modalLayer.innerHTML = `<div class="modal-backdrop" data-close="modal"></div>${html}`;
    document.body.classList.add('modal-open');
    const autofocus = els.modalLayer.querySelector('[autofocus], input, button');
    if (autofocus) setTimeout(() => autofocus.focus(), 40);
  }

  function closeModal() {
    els.modalLayer.className = 'modal-layer';
    els.modalLayer.setAttribute('aria-hidden', 'true');
    els.modalLayer.innerHTML = '';
    document.body.classList.remove('modal-open');
  }

  function modalHeader(kicker, title) {
    return `<div class="modal-header"><div><span>${escapeHTML(kicker)}</span><h2>${escapeHTML(title)}</h2></div><button class="icon-button modal-close" data-close="modal" aria-label="关闭"><svg viewBox="0 0 24 24" fill="none"><path d="m6 6 12 12M18 6 6 18" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg></button></div>`;
  }

  // ---- 登录 ----
  function setLoginError(message = '') {
    els.loginError.textContent = message;
    els.loginError.classList.toggle('show', Boolean(message));
  }

  async function doLogin(password) {
    await API.login(password);
    els.loginPage.hidden = true;
    els.shell.hidden = false;
    await loadAll();
    renderView();
  }

  async function doLogout() {
    try { await API.logout(); } catch (_) { /* ignore */ }
    els.shell.hidden = true;
    els.loginPage.hidden = false;
    els.loginForm.reset();
    setLoginError('');
    els.password.focus();
  }

  // ---- 数据加载 ----
  async function loadAll() {
    try {
      const [stats, reports, users, reminder, knowledge, pdfCache] = await Promise.all([API.stats(), API.reports(), API.users(), API.reminderConfig(), API.knowledgeConfig(), API.pdfCache()]);
      state.stats = stats;
      state.reports = reports.reports || [];
      state.users = users.users || [];
      state.reminderConfig = reminder.config || state.reminderConfig;
      state.knowledgeConfig = knowledge.config || state.knowledgeConfig;
      state.pdfCache = pdfCache || state.pdfCache;
    } catch (error) {
      notify(error.message || '数据加载失败', 'error');
    }
  }

  async function reloadReports() {
    const data = await API.reports();
    state.reports = data.reports || [];
  }

  async function reloadUsers() {
    const data = await API.users();
    state.users = data.users || [];
  }

  async function reloadStats() {
    state.stats = await API.stats();
  }

  // ---- 导航 ----
  function navigate(view) {
    state.view = view;
    const titles = { overview: '概览', reports: '报告管理', users: '用户管理', reminders: '工作提醒', cache: 'PDF缓存', security: '安全设置' };
    const subs = { overview: '平台数据总览', reports: '管理在库报告、回收站与永久删除确认', users: '新建、编辑、删除用户与重置密码', reminders: '维护专题报告参数与知识搜索每日额度', cache: '永久保存的文档预览缓存', security: '修改超级管理员密码' };
    els.topbarTitle.textContent = titles[view] || '后台';
    els.topbarSub.textContent = subs[view] || '';
    document.querySelectorAll('.admin-nav-item').forEach(item => item.classList.toggle('active', item.dataset.view === view));
    closeSidebar();
    renderView();
  }

  function renderView() {
    if (state.view === 'reports') renderReports();
    else if (state.view === 'users') renderUsers();
    else if (state.view === 'reminders') renderReminderConfig();
    else if (state.view === 'cache') renderPdfCache();
    else if (state.view === 'security') renderSecurity();
    else renderOverview();
  }

  function formatBytes(value) {
    const size = Number(value || 0);
    if (size < 1024) return `${size} B`;
    if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
    if (size < 1024 * 1024 * 1024) return `${(size / 1024 / 1024).toFixed(1)} MB`;
    return `${(size / 1024 / 1024 / 1024).toFixed(2)} GB`;
  }

  function renderPdfCache() {
    const cache = state.pdfCache || { items: [], count: 0, totalSizeBytes: 0 };
    els.viewRoot.innerHTML = `
      <section class="admin-overview-grid">
        <div class="admin-stat-card"><span>缓存文件</span><strong>${cache.count || 0}</strong><small>不会自动过期</small></div>
        <div class="admin-stat-card"><span>占用空间</span><strong>${formatBytes(cache.totalSizeBytes)}</strong><small>PDF永久缓存</small></div>
      </section>
      <section class="admin-table-card">
        <div class="admin-table-toolbar"><div style="font-weight:700;">缓存明细</div><button class="btn btn-danger" data-action="clear-pdf-cache" ${cache.count ? '' : 'disabled'}>清空全部缓存</button></div>
        <div class="admin-table-wrap"><table class="admin-table">
          <thead><tr><th>报告</th><th>文件</th><th>生成时间</th><th>最后访问</th><th>大小</th><th style="text-align:right;">操作</th></tr></thead>
          <tbody>${cache.items?.length ? cache.items.map(item => `<tr><td>${escapeHTML(item.reportTitle)}</td><td>${escapeHTML(item.fileName)}</td><td>${formatDateTime(item.generatedAt)}</td><td>${formatDateTime(item.lastAccessedAt)}</td><td>${formatBytes(item.sizeBytes)}</td><td style="text-align:right;"><button class="table-action danger" data-action="delete-pdf-cache" data-key="${item.cacheKey}">删除</button>${item.reportId ? `<button class="table-action" data-action="delete-report-pdf-cache" data-id="${item.reportId}">删除该报告缓存</button>` : ''}</td></tr>`).join('') : '<tr><td colspan="6"><div class="admin-empty"><strong>暂无PDF缓存</strong>Office文档首次预览后会生成永久缓存。</div></td></tr>'}</tbody>
        </table></div>
      </section>`;
  }

  function renderSecurity() {
    els.viewRoot.innerHTML = `
      <section class="admin-table-card" style="max-width:620px;">
        <div class="admin-table-toolbar"><div style="font-weight:700;">修改管理员密码</div></div>
        <form id="adminPasswordChangeForm" class="admin-stats-controls" style="display:grid;">
          <label class="form-field"><span>当前密码</span><input class="text-control" name="oldPassword" type="password" autocomplete="current-password" required></label>
          <label class="form-field"><span>新密码</span><input class="text-control" name="newPassword" type="password" autocomplete="new-password" minlength="8" required></label>
          <label class="form-field"><span>再次输入新密码</span><input class="text-control" name="confirmPassword" type="password" autocomplete="new-password" minlength="8" required></label>
          <button class="btn btn-primary" type="submit">保存新密码</button>
        </form>
      </section>`;
    document.getElementById('adminPasswordChangeForm').addEventListener('submit', changeAdminPassword);
  }

  async function changeAdminPassword(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = Object.fromEntries(new FormData(form).entries());
    if (data.newPassword !== data.confirmPassword) return notify('两次输入的新密码不一致', 'error');
    try { await API.changePassword(data); form.reset(); notify('管理员密码已修改'); }
    catch (error) { notify(error.message, 'error'); }
  }

  async function refreshPdfCache() {
    state.pdfCache = await API.pdfCache();
    renderPdfCache();
  }

  // ---- 概览 ----
  function renderOverview() {
    const s = state.stats || {};
    els.viewRoot.innerHTML = `
      <section class="admin-overview-grid">
        <div class="admin-stat-card"><span>在库报告</span><strong>${s.reports ?? 0}</strong><small>业务前端可见</small></div>
        <div class="admin-stat-card"><span>内部 / 外部</span><strong>${s.internalReports ?? 0} / ${s.externalReports ?? 0}</strong><small>按报告类型</small></div>
        <div class="admin-stat-card"><span>用户总数</span><strong>${s.users ?? 0}</strong><small>注册账号</small></div>
        <div class="admin-stat-card"><span>评分记录</span><strong>${s.ratings ?? 0}</strong><small>累计提交</small></div>
        <div class="admin-stat-card"><span>参与评分</span><strong>${s.participants ?? 0}</strong><small>名团队成员</small></div>
        <div class="admin-stat-card"><span>已评分报告</span><strong>${s.ratedReports ?? 0}</strong><small>含至少一条评分</small></div>
        <div class="admin-stat-card"><span>回收站</span><strong>${s.deletedReports ?? 0}</strong><small>可恢复报告</small></div>
      </section>
      <section class="admin-table-card">
        <div class="admin-table-toolbar"><div style="font-weight:700;">分类分布</div></div>
        <div class="admin-category-stats" style="padding:18px;">
          ${Object.entries(categories).map(([k, m]) => `<div class="admin-category-stat"><strong>${s.byCategory?.[k] ?? 0}</strong><span>${m.label}</span></div>`).join('')}
        </div>
      </section>
      <section class="admin-table-card">
        <div class="admin-table-toolbar"><div style="font-weight:700;">研究主题分布</div></div>
        <div class="admin-category-stats" style="padding:18px;">
          ${Object.entries(themes).map(([k, m]) => `<div class="admin-category-stat"><strong>${s.byTheme?.[k] ?? 0}</strong><span>${m.label}</span></div>`).join('')}
        </div>
      </section>`;
  }

  // ---- 报告管理 ----
  function filteredReports() {
    const q = state.filters.query.trim().toLowerCase();
    return state.reports.filter(r => {
      const matchesCat = !state.filters.category || r.category === state.filters.category;
      const matchesTheme = !state.filters.theme || r.theme === state.filters.theme;
      const matchesStatus = state.filters.status === 'all' || (state.filters.status === 'deleted' ? Boolean(r.deletedAt) : !r.deletedAt);
      const haystack = [r.title, r.author, r.org, r.summary, ...(r.tags || [])].join(' ').toLowerCase();
      return matchesCat && matchesTheme && matchesStatus && (!q || haystack.includes(q));
    }).sort((a, b) => {
      if (Boolean(a.deletedAt) !== Boolean(b.deletedAt)) return a.deletedAt ? 1 : -1;
      return new Date(b.deletedAt || b.uploadedAt) - new Date(a.deletedAt || a.uploadedAt);
    });
  }

  function renderReports() {
    const list = filteredReports();
    els.viewRoot.innerHTML = `
      <section class="admin-table-card">
        <div class="admin-table-toolbar">
          <label class="search-field">
            <svg viewBox="0 0 24 24" fill="none"><circle cx="11" cy="11" r="7" stroke="currentColor" stroke-width="1.8"/><path d="m16.5 16.5 4 4" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>
            <input id="reportSearchInput" value="${escapeHTML(state.filters.query)}" placeholder="搜索标题、作者、标签">
          </label>
          <select id="reportCategoryFilter" class="select-control">
            <option value="">全部分类</option>
            ${Object.entries(categories).map(([k, m]) => `<option value="${k}" ${state.filters.category === k ? 'selected' : ''}>${m.label}</option>`).join('')}
          </select>
          <select id="reportThemeFilter" class="select-control">
            <option value="">全部主题</option>
            ${Object.entries(themes).map(([k, m]) => `<option value="${k}" ${state.filters.theme === k ? 'selected' : ''}>${m.label}</option>`).join('')}
          </select>
          <select id="reportStatusFilter" class="select-control">
            <option value="active" ${state.filters.status === 'active' ? 'selected' : ''}>在库报告</option>
            <option value="deleted" ${state.filters.status === 'deleted' ? 'selected' : ''}>回收站</option>
            <option value="all" ${state.filters.status === 'all' ? 'selected' : ''}>全部状态</option>
          </select>
        </div>
        <div class="admin-table-wrap">
          <table class="admin-table">
            <thead><tr><th>报告</th><th>分类</th><th>主题</th><th>作者</th><th>报告日期</th><th>上传时间</th><th>评分</th><th style="text-align:right;">操作</th></tr></thead>
            <tbody>
              ${list.length ? list.map(reportRow).join('') : `<tr><td colspan="8"><div class="admin-empty"><strong>暂无报告</strong>调整筛选条件或前往主站上传报告。</div></td></tr>`}
            </tbody>
          </table>
        </div>
      </section>`;
  }

  function reportRow(r) {
    const deleted = Boolean(r.deletedAt);
    return `<tr class="${deleted ? 'admin-report-deleted' : ''}">
      <td class="col-title"><strong>${escapeHTML(r.title)}${r.reportType === 'external' ? '<em class="admin-external-badge">外部</em>' : ''}${deleted ? '<em class="admin-trash-badge">回收站</em>' : ''}</strong><span>${escapeHTML(r.fileName || '')}${deleted ? ` · 删除于 ${formatDateTime(r.deletedAt)}` : ''}</span></td>
      <td>${escapeHTML(categoryLabel(r.category))}</td>
      <td>${escapeHTML(themeLabel(r.theme))}</td>
      <td>${escapeHTML(r.author)}<br><span style="color:var(--text-muted);font-size:12px;">${escapeHTML(r.org || '')}</span></td>
      <td>${formatDate(r.reportDate)}</td>
      <td>${formatDateTime(r.uploadedAt)}</td>
      <td>${r.reportType !== 'external' && (r.category === 'monthly' || r.category === 'deep') ? `<span title="评分记录数">${ratingCountFor(r.id)} 条</span>` : '<span style="color:var(--text-faint);">—</span>'}</td>
      <td>
        <div class="row-actions">
          ${deleted ? `<button class="btn btn-secondary" data-action="restore-report" data-id="${r.id}">恢复</button><button class="btn btn-danger" data-action="permanent-delete-report" data-id="${r.id}">永久删除</button>` : `<button class="btn btn-secondary" data-action="edit-report" data-id="${r.id}">编辑</button>${r.reportType !== 'external' && (r.category === 'monthly' || r.category === 'deep') ? `<button class="btn btn-ghost" data-action="reset-scores" data-id="${r.id}" title="清除该报告所有评分">重置评分</button>` : ''}<button class="btn btn-danger" data-action="trash-report" data-id="${r.id}">移入回收站</button>`}
        </div>
      </td>
    </tr>`;
  }

  function ratingCountFor(reportId) {
    // 后台 stats 不含每报告评分数，简单从 reports 字段读（如有）；否则显示 —
    const r = state.reports.find(x => x.id === reportId);
    return r?.ratingCount ?? '—';
  }

  // ---- 报告编辑弹窗 ----
  function showReportEditModal(id) {
    const r = state.reports.find(x => x.id === id);
    if (!r) return notify('报告不存在', 'error');
    openModal(`<section class="modal-card">
      ${modalHeader('编辑报告', r.title)}
      <form id="reportEditForm">
        <div class="modal-body">
          <div class="admin-form-grid">
            <div class="form-field full"><label for="reTitle">标题</label><input id="reTitle" name="title" value="${escapeHTML(r.title)}" required></div>
            <div class="form-field"><label for="reType">报告类型</label><select id="reType" name="reportType"><option value="internal" ${r.reportType !== 'external' ? 'selected' : ''}>内部报告</option><option value="external" ${r.reportType === 'external' ? 'selected' : ''}>外部推荐报告</option></select></div>
            <div class="form-field"><label for="reAuthor">作者</label><input id="reAuthor" name="author" value="${escapeHTML(r.author)}"></div>
            <div class="form-field"><label for="reSourceAuthor">外部原作者</label><input id="reSourceAuthor" name="sourceAuthor" value="${escapeHTML(r.sourceAuthor || '')}"></div>
            <div class="form-field"><label for="reSourceInstitution">外部报告机构</label><input id="reSourceInstitution" name="sourceInstitution" value="${escapeHTML(r.sourceInstitution || '')}"></div>
            <div class="form-field"><label for="reOrg">部门</label><select id="reOrg" name="org"><option value="资产配置部" ${r.org === '资产配置部' ? 'selected' : ''}>资产配置部</option><option value="固收中心" ${r.org === '固收中心' ? 'selected' : ''}>固收中心</option></select></div>
            <div class="form-field"><label for="reCategory">分类</label><select id="reCategory" name="category">${Object.entries(categories).map(([k, m]) => `<option value="${k}" ${r.category === k ? 'selected' : ''}>${m.label}</option>`).join('')}</select></div>
            <div class="form-field"><label for="reTheme">研究主题</label><select id="reTheme" name="theme">${Object.entries(themes).map(([k, m]) => `<option value="${k}" ${r.theme === k ? 'selected' : ''}>${m.label}</option>`).join('')}</select></div>
            <div class="form-field"><label for="reReportDate">报告日期</label><input id="reReportDate" name="reportDate" type="date" value="${escapeHTML(r.reportDate || '')}"></div>
            <div class="form-field"><label for="reUploadedAt">上传时间</label><input id="reUploadedAt" name="uploadedAt" type="datetime-local" value="${toDatetimeLocal(r.uploadedAt)}"></div>
            <div class="form-field full"><label for="reTags">关键词 <span>逗号分隔</span></label><input id="reTags" name="tags" value="${escapeHTML((r.tags || []).join('，'))}"></div>
            <div class="form-field full"><label for="reSummary">摘要</label><textarea id="reSummary" name="summary" rows="3">${escapeHTML(r.summary || '')}</textarea></div>
            <div class="form-field full"><label for="reRecommendation">推荐语 <span>外部报告展示在摘要上方</span></label><textarea id="reRecommendation" name="recommendation" rows="2">${escapeHTML(r.recommendation || '')}</textarea></div>
          </div>
          <div class="admin-form-note">注：文件本身不可替换，仅修改报告元信息。</div>
        </div>
        <div class="modal-footer"><button type="button" class="btn btn-ghost" data-close="modal">取消</button><button type="submit" class="btn btn-primary">保存修改</button></div>
      </form>
    </section>`);
    const form = document.getElementById('reportEditForm');
    form.dataset.id = id;
    form.addEventListener('submit', submitReportEdit);
  }

  async function submitReportEdit(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const id = form.dataset.id;
    const data = new FormData(form);
    const fields = {
      title: String(data.get('title')).trim(),
      author: String(data.get('author')).trim(),
      org: String(data.get('org')),
      category: String(data.get('category')),
      theme: String(data.get('theme')),
      reportType: String(data.get('reportType')),
      reportDate: String(data.get('reportDate')),
      tags: String(data.get('tags') || ''),
      summary: String(data.get('summary') || '').trim(),
      recommendation: String(data.get('recommendation') || '').trim(),
      sourceAuthor: String(data.get('sourceAuthor') || '').trim(),
      sourceInstitution: String(data.get('sourceInstitution') || '').trim(),
    };
    const uploadedAt = String(data.get('uploadedAt'));
    if (uploadedAt) fields.uploadedAt = uploadedAt.length === 16 ? `${uploadedAt}:00` : uploadedAt;
    try {
      await API.updateReport(id, fields);
      await Promise.all([reloadReports(), reloadStats()]);
      closeModal();
      notify('报告信息已更新');
      renderReports();
    } catch (error) {
      notify(error.message || '保存失败', 'error');
    }
  }

  function showReportTrashModal(id) {
    const r = state.reports.find(x => x.id === id);
    if (!r) return notify('报告不存在', 'error');
    openModal(`<section class="modal-card">
      ${modalHeader('移入回收站', r.title)}
      <div class="modal-body">
        <div class="detail-permission"><svg viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.8"/><path d="M12 8v4M12 16h.01" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg><div><strong>确认将这份报告移入回收站？</strong><p>报告会从业务前端隐藏，文件和全部评分记录仍会保留，可随时从后台恢复。</p></div></div>
      </div>
      <div class="modal-footer"><button type="button" class="btn btn-ghost" data-close="modal">取消</button><button type="button" class="btn btn-primary" data-action="confirm-trash-report" data-id="${id}">移入回收站</button></div>
    </section>`);
  }

  async function confirmTrashReport(id) {
    try {
      await API.trashReport(id);
      await Promise.all([reloadReports(), reloadStats()]);
      closeModal();
      notify('报告已移入回收站，可随时恢复');
      renderReports();
    } catch (error) {
      notify(error.message || '移入回收站失败', 'error');
    }
  }

  async function restoreReport(id) {
    try {
      await API.restoreReport(id);
      await Promise.all([reloadReports(), reloadStats()]);
      notify('报告及其原有评分已恢复');
      renderReports();
    } catch (error) {
      notify(error.message || '恢复失败', 'error');
    }
  }

  function showPermanentDeleteModal(id) {
    const r = state.reports.find(x => x.id === id);
    if (!r || !r.deletedAt) return notify('仅回收站报告可以永久删除', 'error');
    openModal(`<section class="modal-card">
      ${modalHeader('永久删除报告', r.title)}
      <div class="modal-body">
        <div class="detail-permission"><svg viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.8"/><path d="M12 8v4M12 16h.01" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg><div><strong>这是不可恢复的永久删除</strong><p>确认后将删除报告文件和全部评分记录。请输入“永久删除”完成超级管理员确认。</p></div></div>
        <div class="form-field" style="margin-top:16px;"><label for="permanentDeleteInput">确认文字</label><input id="permanentDeleteInput" autocomplete="off" placeholder="请输入：永久删除" autofocus></div>
      </div>
      <div class="modal-footer"><button type="button" class="btn btn-ghost" data-close="modal">取消</button><button type="button" class="btn btn-primary" data-action="confirm-permanent-delete-report" data-id="${id}" style="background:var(--danger);border-color:var(--danger);">确认永久删除</button></div>
    </section>`);
  }

  async function confirmPermanentDeleteReport(id) {
    const confirmation = document.getElementById('permanentDeleteInput');
    if (!confirmation || confirmation.value.trim() !== '永久删除') return notify('请输入“永久删除”后再确认', 'error');
    try {
      await API.deleteReportPermanently(id);
      await Promise.all([reloadReports(), reloadStats()]);
      closeModal();
      notify('报告、文件及评分记录已永久删除');
      renderReports();
    } catch (error) {
      notify(error.message || '永久删除失败', 'error');
    }
  }

  function showResetScoresModal(id) {
    const r = state.reports.find(x => x.id === id);
    if (!r) return notify('报告不存在', 'error');
    openModal(`<section class="modal-card">
      ${modalHeader('重置评分', r.title)}
      <div class="modal-body">
        <div class="detail-permission"><svg viewBox="0 0 24 24" fill="none"><path d="M3 12a9 9 0 1 0 3-6.7M3 4v5h5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg><div><strong>确认重置该报告的全部评分？</strong><p>所有团队成员对这份报告已提交的评分记录将被永久清除，无法恢复。</p></div></div>
      </div>
      <div class="modal-footer"><button type="button" class="btn btn-ghost" data-close="modal">取消</button><button type="button" class="btn btn-primary" data-action="confirm-reset-scores" data-id="${id}" style="background:var(--danger);border-color:var(--danger);">确认重置</button></div>
    </section>`);
  }

  async function confirmResetScores(id) {
    try {
      await API.resetScores(id);
      closeModal();
      notify('该报告评分已重置');
    } catch (error) {
      notify(error.message || '重置失败', 'error');
    }
  }

  // ---- 用户管理 ----
  function filteredUsers() {
    const q = state.filters.query.trim().toLowerCase();
    if (!q) return state.users;
    return state.users.filter(u => [u.id, u.name, u.org, u.role].join(' ').toLowerCase().includes(q));
  }

  function renderUsers() {
    const list = filteredUsers();
    els.viewRoot.innerHTML = `
      <section class="admin-table-card">
        <div class="admin-table-toolbar">
          <label class="search-field">
            <svg viewBox="0 0 24 24" fill="none"><circle cx="11" cy="11" r="7" stroke="currentColor" stroke-width="1.8"/><path d="m16.5 16.5 4 4" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>
            <input id="userSearchInput" value="${escapeHTML(state.filters.query)}" placeholder="搜索账号、姓名、部门">
          </label>
          <button class="btn btn-primary" data-action="new-user"><svg viewBox="0 0 24 24" fill="none"><path d="M12 5v14M5 12h14" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>新建用户</button>
        </div>
        <div class="admin-table-wrap">
          <table class="admin-table">
            <thead><tr><th>账号</th><th>姓名</th><th>部门</th><th>角色</th><th>密码状态</th><th style="text-align:right;">操作</th></tr></thead>
            <tbody>
              ${list.length ? list.map(userRow).join('') : `<tr><td colspan="6"><div class="admin-empty"><strong>暂无用户</strong>点击"新建用户"添加账号。</div></td></tr>`}
            </tbody>
          </table>
        </div>
      </section>`;
  }

  function userRow(u) {
    return `<tr>
      <td><strong>${escapeHTML(u.id)}</strong></td>
      <td>${escapeHTML(u.name)}</td>
      <td>${escapeHTML(u.org)}</td>
      <td><span class="admin-role-tag ${escapeHTML(u.role)}">${escapeHTML(roleLabel(u.role))}</span></td>
      <td><span class="admin-pw-status ${u.defaultPassword ? 'default' : ''}">${u.defaultPassword ? '默认密码' : '已修改'}</span></td>
      <td>
        <div class="row-actions">
          <button class="btn btn-secondary" data-action="edit-user" data-id="${u.id}">编辑</button>
          <button class="btn btn-ghost" data-action="reset-password" data-id="${u.id}">重置密码</button>
          <button class="btn btn-ghost" data-action="clear-qa-history" data-id="${u.id}" title="清空该用户的知识搜索历史">清空搜索历史</button>
          <button class="btn btn-ghost" data-action="delete-user" data-id="${u.id}" style="color:var(--danger);">删除</button>
        </div>
      </td>
    </tr>`;
  }

  function showUserEditModal(id, isNew = false) {
    const u = isNew ? { id: '', name: '', org: '资产配置部', role: 'member' } : state.users.find(x => x.id === id);
    if (!u) return notify('用户不存在', 'error');
    const idFieldDisabled = isNew ? '' : 'disabled';
    openModal(`<section class="modal-card">
      ${modalHeader(isNew ? '新建用户' : '编辑用户', isNew ? '新建用户账号' : u.name)}
      <form id="userEditForm">
        <div class="modal-body">
          <div class="admin-form-grid">
            <div class="form-field"><label for="ueId">账号 <em>*</em></label><input id="ueId" name="id" value="${escapeHTML(u.id)}" placeholder="姓名全拼，小写" ${idFieldDisabled} ${isNew ? 'required' : ''}></div>
            <div class="form-field"><label for="ueName">姓名 <em>*</em></label><input id="ueName" name="name" value="${escapeHTML(u.name)}" required></div>
            <div class="form-field"><label for="ueOrg">部门</label><select id="ueOrg" name="org"><option value="" ${u.org === '' ? 'selected' : ''}>（空置）</option><option value="资产配置部" ${u.org === '资产配置部' ? 'selected' : ''}>资产配置部</option><option value="固收中心" ${u.org === '固收中心' ? 'selected' : ''}>固收中心</option><option value="领导" ${u.org === '领导' ? 'selected' : ''}>领导</option><option value="行政" ${u.org === '行政' ? 'selected' : ''}>行政</option></select></div>
            <div class="form-field"><label for="ueRole">角色</label><select id="ueRole" name="role"><option value="member" ${u.role === 'member' ? 'selected' : ''}>研究人员</option><option value="leader" ${u.role === 'leader' ? 'selected' : ''}>领导</option><option value="admin" ${u.role === 'admin' ? 'selected' : ''}>行政</option></select></div>
            ${isNew ? `<div class="form-field full"><label for="uePassword">初始密码 <span>留空则默认 123456</span></label><input id="uePassword" name="password" type="password" placeholder="123456"></div>` : ''}
          </div>
          ${isNew ? '<div class="admin-form-note">账号建议使用姓名全拼（小写、无空格），登录后用户可自行修改密码。</div>' : '<div class="admin-form-note">账号创建后不可修改；如需重设登录密码请使用"重置密码"功能。</div>'}
        </div>
        <div class="modal-footer"><button type="button" class="btn btn-ghost" data-close="modal">取消</button><button type="submit" class="btn btn-primary">${isNew ? '创建用户' : '保存修改'}</button></div>
      </form>
    </section>`);
    const form = document.getElementById('userEditForm');
    form.dataset.id = id || '';
    form.dataset.isNew = isNew ? '1' : '0';
    form.addEventListener('submit', submitUserEdit);
  }

  async function submitUserEdit(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const isNew = form.dataset.isNew === '1';
    const data = new FormData(form);
    const fields = {
      name: String(data.get('name')).trim(),
      org: String(data.get('org')),
      role: String(data.get('role')),
    };
    try {
      if (isNew) {
        fields.id = String(data.get('id')).trim().toLowerCase().replace(/\s+/g, '');
        const pw = String(data.get('password') || '').trim();
        if (pw) fields.password = pw;
        await API.createUser(fields);
      } else {
        await API.updateUser(form.dataset.id, fields);
      }
      await reloadUsers();
      closeModal();
      notify(isNew ? '用户已创建' : '用户信息已更新');
      renderUsers();
    } catch (error) {
      notify(error.message || '保存失败', 'error');
    }
  }

  function showResetPasswordModal(id) {
    const u = state.users.find(x => x.id === id);
    if (!u) return notify('用户不存在', 'error');
    openModal(`<section class="modal-card">
      ${modalHeader('重置密码', u.name)}
      <form id="resetPwForm">
        <div class="modal-body">
          <div class="form-field">
            <label for="rpNew">新密码 <em>*</em></label>
            <input id="rpNew" name="password" type="password" minlength="6" placeholder="至少 6 位" required autofocus>
          </div>
          <div class="admin-form-note">重置后用户需使用新密码登录。常用初始密码为 <strong>123456</strong>。</div>
        </div>
        <div class="modal-footer"><button type="button" class="btn btn-ghost" data-close="modal">取消</button><button type="submit" class="btn btn-primary">重置密码</button></div>
      </form>
    </section>`);
    document.getElementById('resetPwForm').dataset.id = id;
    document.getElementById('resetPwForm').addEventListener('submit', submitResetPassword);
  }

  async function submitResetPassword(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const id = form.dataset.id;
    const password = String(new FormData(form).get('password'));
    try {
      await API.resetPassword(id, password);
      await reloadUsers();
      closeModal();
      notify('密码已重置');
      renderUsers();
    } catch (error) {
      notify(error.message || '重置失败', 'error');
    }
  }

  function showUserDeleteModal(id) {
    const u = state.users.find(x => x.id === id);
    if (!u) return notify('用户不存在', 'error');
    openModal(`<section class="modal-card">
      ${modalHeader('删除用户', u.name)}
      <div class="modal-body">
        <div class="detail-permission"><svg viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.8"/><path d="M12 8v4M12 16h.01" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg><div><strong>确认删除用户 ${escapeHTML(u.name)}（${escapeHTML(u.id)}）？</strong><p>删除后该账号无法登录；其历史评分记录会保留（显示为"已删除用户"）。此操作不可撤销。</p></div></div>
      </div>
      <div class="modal-footer"><button type="button" class="btn btn-ghost" data-close="modal">取消</button><button type="button" class="btn btn-primary" data-action="confirm-delete-user" data-id="${id}" style="background:var(--danger);border-color:var(--danger);">确认删除</button></div>
    </section>`);
  }

  async function confirmDeleteUser(id) {
    try {
      await API.deleteUser(id);
      await reloadUsers();
      closeModal();
      notify('用户已删除');
      renderUsers();
    } catch (error) {
      notify(error.message || '删除失败', 'error');
    }
  }

  function showClearQaHistoryModal(id) {
    const u = state.users.find(x => x.id === id);
    if (!u) return notify('用户不存在', 'error');
    openModal(`<section class="modal-card">
      ${modalHeader('清空搜索历史', u.name)}
      <div class="modal-body">
        <div class="detail-permission"><svg viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.8"/><path d="M12 8v4M12 16h.01" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg><div><strong>确认清空 ${escapeHTML(u.name)}（${escapeHTML(u.id)}）的知识搜索历史？</strong><p>该用户的全部历史提问与对话记录将被清除，且不影响其当日已用搜索额度。此操作不可撤销。</p></div></div>
      </div>
      <div class="modal-footer"><button type="button" class="btn btn-ghost" data-close="modal">取消</button><button type="button" class="btn btn-primary" data-action="confirm-clear-qa-history" data-id="${id}" style="background:var(--danger);border-color:var(--danger);">确认清空</button></div>
    </section>`);
  }

  async function confirmClearQaHistory(id) {
    try {
      const res = await API.clearQaHistory(id);
      closeModal();
      notify(`已清空 ${res && res.removed != null ? res.removed : ''} 条搜索历史`);
    } catch (error) {
      notify(error.message || '清空失败', 'error');
    }
  }

  // ---- 工作提醒配置 ----
  function renderReminderConfig() {
    const config = state.reminderConfig || { period: '', reportCategory: 'deep', rules: [] };
    const knowledgeConfig = state.knowledgeConfig || { memberLimit: 10, leaderLimit: 100 };
    const members = state.users.filter(user => user.role === 'member');
    els.viewRoot.innerHTML = `<form id="reminderConfigForm" class="reminder-config-page">
      <section class="admin-table-card reminder-config-head"><div><h2>专题报告参数</h2><p>首页按报告日期在自然年度（1 月 1 日至 12 月 31 日）内累计；仅所选报告分类计入专题完成数。</p></div><div class="reminder-config-fields"><label class="form-field"><span>统计年度</span><input type="number" name="period" min="2020" max="2100" step="1" value="${escapeHTML(config.period || new Date().getFullYear())}" required></label><label class="form-field"><span>计入分类</span><select name="reportCategory">${Object.entries(categories).map(([key, meta]) => `<option value="${key}" ${config.reportCategory === key ? 'selected' : ''}>${meta.label}</option>`).join('')}</select></label></div></section>
      <section class="admin-table-card reminder-config-head"><div><h2>知识搜索额度</h2><p>按业务账号角色限制每日问答次数，修改后立即对全体用户生效。</p></div><div class="reminder-config-fields"><label class="form-field"><span>研究人员 / 行政</span><input type="number" name="memberLimit" min="1" max="1000" step="1" value="${Number(knowledgeConfig.memberLimit) || 10}" required></label><label class="form-field"><span>领导</span><input type="number" name="leaderLimit" min="1" max="1000" step="1" value="${Number(knowledgeConfig.leaderLimit) || 100}" required></label></div></section>
      <section class="reminder-admin-list"><div class="admin-table-toolbar"><div><strong>专题要求</strong><span> · 固收中心按组、资产配置部研究组按人配置</span></div><button type="button" class="btn btn-secondary" data-action="add-reminder-rule">新增规则</button></div>
        ${(config.rules || []).map((rule, index) => `<article class="reminder-admin-card" data-rule-index="${index}"><div class="reminder-rule-number">${index + 1}</div><div class="admin-form-grid"><div class="form-field full"><label>要求名称</label><input name="ruleLabel" value="${escapeHTML(rule.label)}" required></div><div class="form-field"><label>核算方式</label><select name="ruleMode"><option value="group" ${rule.mode !== 'person' ? 'selected' : ''}>小组合计</option><option value="person" ${rule.mode === 'person' ? 'selected' : ''}>个人</option></select></div><div class="form-field"><label>目标篇数</label><input name="ruleTarget" type="number" min="0" max="100" value="${Number(rule.target) || 0}" required></div><div class="form-field full"><label>计入人员 <span>可多选</span></label><select name="ruleUsers" multiple size="5">${members.map(user => `<option value="${escapeHTML(user.id)}" ${(rule.userIds || []).includes(user.id) ? 'selected' : ''}>${escapeHTML(user.name)} · ${escapeHTML(user.org)}</option>`).join('')}</select></div></div><button type="button" class="btn btn-ghost reminder-remove" data-action="remove-reminder-rule" data-index="${index}">删除</button></article>`).join('') || '<div class="admin-empty"><strong>暂无专题规则</strong>点击“新增规则”开始配置。</div>'}
      </section><div class="reminder-save-bar"><span>保存后首页工作提醒立即按新参数重新计算。</span><button class="btn btn-primary" type="submit">保存工作提醒参数</button></div></form>`;
    document.getElementById('reminderConfigForm').addEventListener('submit', saveReminderConfig);
  }

  function addReminderRule() {
    state.reminderConfig.rules.push({ id: `rule-${Date.now()}`, label: '新专题要求', mode: 'group', target: 1, userIds: [] });
    renderReminderConfig();
  }

  function removeReminderRule(index) {
    state.reminderConfig.rules.splice(Number(index), 1);
    renderReminderConfig();
  }

  async function saveReminderConfig(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const cards = [...form.querySelectorAll('.reminder-admin-card')];
    const rules = cards.map((card, index) => ({
      id: state.reminderConfig.rules[index]?.id || `rule-${Date.now()}-${index}`,
      label: card.querySelector('[name="ruleLabel"]').value.trim(),
      mode: card.querySelector('[name="ruleMode"]').value,
      target: Number(card.querySelector('[name="ruleTarget"]').value),
      userIds: [...card.querySelector('[name="ruleUsers"]').selectedOptions].map(option => option.value),
    }));
      const payload = { period: form.elements.period.value, reportCategory: form.elements.reportCategory.value, rules };
      const knowledgePayload = { memberLimit: form.elements.memberLimit.value, leaderLimit: form.elements.leaderLimit.value };
    try {
      const [result, knowledgeResult] = await Promise.all([API.saveReminderConfig(payload), API.saveKnowledgeConfig(knowledgePayload)]);
      state.reminderConfig = result.config;
      state.knowledgeConfig = knowledgeResult.config;
      notify('工作提醒与知识搜索参数已保存');
      renderReminderConfig();
    } catch (error) { notify(error.message || '保存失败', 'error'); }
  }

  // ---- 事件 ----
  function handleRootClick(event) {
    const close = event.target.closest('[data-close="modal"]');
    if (close) return closeModal();
    const target = event.target.closest('[data-action]');
    if (!target) return;
    const action = target.dataset.action;
    const id = target.dataset.id;
    if (action === 'edit-report') showReportEditModal(id);
    else if (action === 'trash-report') showReportTrashModal(id);
    else if (action === 'confirm-trash-report') confirmTrashReport(id);
    else if (action === 'restore-report') restoreReport(id);
    else if (action === 'permanent-delete-report') showPermanentDeleteModal(id);
    else if (action === 'confirm-permanent-delete-report') confirmPermanentDeleteReport(id);
    else if (action === 'reset-scores') showResetScoresModal(id);
    else if (action === 'confirm-reset-scores') confirmResetScores(id);
    else if (action === 'new-user') showUserEditModal(null, true);
    else if (action === 'edit-user') showUserEditModal(id, false);
    else if (action === 'reset-password') showResetPasswordModal(id);
    else if (action === 'delete-user') showUserDeleteModal(id);
    else if (action === 'confirm-delete-user') confirmDeleteUser(id);
    else if (action === 'clear-qa-history') showClearQaHistoryModal(id);
    else if (action === 'confirm-clear-qa-history') confirmClearQaHistory(id);
    else if (action === 'add-reminder-rule') addReminderRule();
    else if (action === 'remove-reminder-rule') removeReminderRule(target.dataset.index);
    else if (action === 'delete-pdf-cache') {
      if (window.confirm('确认删除这个PDF缓存？下次预览时将重新生成。')) API.deletePdfCache(target.dataset.key).then(refreshPdfCache).catch(error => notify(error.message, 'error'));
    }
    else if (action === 'delete-report-pdf-cache') {
      if (window.confirm('确认删除该报告的全部PDF缓存？')) API.deleteReportPdfCache(id).then(refreshPdfCache).catch(error => notify(error.message, 'error'));
    }
    else if (action === 'clear-pdf-cache') {
      if (window.confirm('确认清空全部PDF缓存？此操作不可撤销，后续预览将重新生成。')) API.clearPdfCache().then(refreshPdfCache).catch(error => notify(error.message, 'error'));
    }
  }

  function openSidebar() {
    els.sidebar.classList.add('open');
    els.sidebarMask.classList.add('open');
  }

  function closeSidebar() {
    els.sidebar.classList.remove('open');
    els.sidebarMask.classList.remove('open');
  }

  function bindEvents() {
    els.loginForm.addEventListener('submit', async event => {
      event.preventDefault();
      setLoginError('');
      try {
        await doLogin(els.password.value);
      } catch (error) {
        setLoginError(error.message || '后台密码不正确');
        els.password.focus();
      }
    });
    document.getElementById('adminLogoutBtn').addEventListener('click', doLogout);
    document.querySelectorAll('.admin-nav-item').forEach(item => item.addEventListener('click', () => navigate(item.dataset.view)));
    document.addEventListener('click', handleRootClick);
    els.viewRoot.addEventListener('input', event => {
      if (event.target.id === 'reportSearchInput') { state.filters.query = event.target.value; renderReports(); const i = document.getElementById('reportSearchInput'); i.focus(); i.setSelectionRange(i.value.length, i.value.length); }
      if (event.target.id === 'userSearchInput') { state.filters.query = event.target.value; renderUsers(); const i = document.getElementById('userSearchInput'); i.focus(); i.setSelectionRange(i.value.length, i.value.length); }
    });
    els.viewRoot.addEventListener('change', event => {
      if (event.target.id === 'reportCategoryFilter') { state.filters.category = event.target.value; renderReports(); }
      if (event.target.id === 'reportThemeFilter') { state.filters.theme = event.target.value; renderReports(); }
      if (event.target.id === 'reportStatusFilter') { state.filters.status = event.target.value; renderReports(); }
    });
    els.menuBtn.addEventListener('click', openSidebar);
    els.sidebarMask.addEventListener('click', closeSidebar);
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape') { if (els.modalLayer.classList.contains('open')) closeModal(); else closeSidebar(); }
    });
  }

  async function boot() {
    Object.assign(els, {
      loginPage: document.getElementById('adminLoginPage'),
      loginForm: document.getElementById('adminLoginForm'),
      password: document.getElementById('adminPassword'),
      loginError: document.getElementById('adminLoginError'),
      shell: document.getElementById('adminShell'),
      sidebar: document.querySelector('.admin-sidebar'),
      sidebarMask: document.getElementById('adminSidebarMask'),
      menuBtn: document.getElementById('adminMenuBtn'),
      topbarTitle: document.getElementById('adminTopbarTitle'),
      topbarSub: document.getElementById('adminTopbarSub'),
      viewRoot: document.getElementById('adminViewRoot'),
      modalLayer: document.getElementById('adminModalLayer'),
      toastStack: document.getElementById('adminToastStack'),
    });
    bindEvents();
    // 恢复会话
    try {
      const data = await API.status();
      if (data.admin) {
        els.loginPage.hidden = true;
        els.shell.hidden = false;
        await loadAll();
        renderView();
        return;
      }
    } catch (_) { /* ignore */ }
    els.loginPage.hidden = false;
    els.shell.hidden = true;
    els.password.focus();
  }

  boot();
})();
