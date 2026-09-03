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

  const { config, categories, themes } = window.InternalLibraryData;

  // 全部业务数据走服务端 API；localStorage 仅存少量 UI 偏好（如 AI 摘要版本记忆），不存业务数据
  const API = {
    login: (u, p) => apiFetch('/api/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin', body: JSON.stringify({ username: u, password: p }) }).then(handleJson),
    logout: () => apiFetch('/api/logout', { method: 'POST', credentials: 'same-origin' }).then(handleJson),
    me: () => apiFetch('/api/me', { credentials: 'same-origin' }).then(handleJson),
    changePassword: (oldP, newP) => apiFetch('/api/change-password', { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin', body: JSON.stringify({ oldPassword: oldP, newPassword: newP }) }).then(handleJson),
    reports: () => apiFetch('/api/reports', { credentials: 'same-origin' }).then(handleJson),
    ratings: () => apiFetch('/api/ratings', { credentials: 'same-origin' }).then(handleJson),
    submitRating: (data) => apiFetch('/api/ratings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin', body: JSON.stringify(data) }).then(handleJson),
    deleteReport: (id) => apiFetch(`/api/reports/${id}`, { method: 'DELETE', credentials: 'same-origin' }).then(handleJson),
    scoringStatus: (id) => apiFetch(`/api/reports/${id}/scoring-status`, { credentials: 'same-origin' }).then(handleJson),
    aiComplete: (formData) => apiFetch('/api/reports/ai-complete', { method: 'POST', credentials: 'same-origin', body: formData }).then(handleJson),
    reportAuthors: () => apiFetch('/api/report-authors', { credentials: 'same-origin' }).then(handleJson),
    reminders: () => apiFetch('/api/work-reminders', { credentials: 'same-origin' }).then(handleJson),
    knowledgeStatus: () => apiFetch('/api/knowledge-search', { credentials: 'same-origin' }).then(handleJson),
    knowledgeAsk: (question, options = {}) => apiFetch('/api/knowledge-search', { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin', body: JSON.stringify({ question, ...options }) }).then(handleJson),
    // 流式问答：SSE 逐事件回调 onEvent({type:'stage'|'delta'|'done'|'error', ...})，
    // filters 携带范围筛选（时间/来源/种类/主题/人员），
    // 前置校验失败（额度/密钥等）时服务端返回 JSON 错误，此处统一抛出。
    knowledgeAskStream: async (question, options = {}, onEvent) => {
      const response = await apiFetch('/api/knowledge-search/stream', { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin', body: JSON.stringify({ question, ...options }) });
      if (!response.ok || !(response.headers.get('content-type') || '').includes('text/event-stream')) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.error || `知识搜索失败 (${response.status})`);
      }
      if (!response.body || typeof response.body.getReader !== 'function') {
        // 浏览器不支持流式读取时，回落到一次性返回的旧接口，保证功能可用。
        const result = await API.knowledgeAsk(question, options);
        onEvent({ type: 'done', ...result });
        return;
      }
      await consumeSseStream(response, onEvent);
    },
    // 单篇报告 AI 摘要：读取缓存 / 流式生成（style: concise|standard|deep，force 强制重新生成；
    // signal 用于切换版本/关闭面板时打断进行中的生成流）
    getAiSummary: (id, style) => apiFetch(`/api/reports/${id}/ai-summary?style=${encodeURIComponent(style)}`, { credentials: 'same-origin' }).then(handleJson),
    aiSummaryStream: async (id, style, force, onEvent, signal) => {
      const response = await apiFetch(`/api/reports/${id}/ai-summary`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin', body: JSON.stringify({ style, force: Boolean(force) }), signal });
      if (!response.ok || !(response.headers.get('content-type') || '').includes('text/event-stream')) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.error || `AI 摘要失败 (${response.status})`);
      }
      if (!response.body || typeof response.body.getReader !== 'function') throw new Error('当前浏览器不支持流式生成，请更换浏览器后重试');
      await consumeSseStream(response, onEvent);
    },
    // 就本文提问：基于单篇报告摘要与全文的问答（SSE）
    reportAskStream: async (id, question, onEvent) => {
      const response = await apiFetch(`/api/reports/${id}/ask`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin', body: JSON.stringify({ question }) });
      if (!response.ok || !(response.headers.get('content-type') || '').includes('text/event-stream')) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.error || `提问失败 (${response.status})`);
      }
      if (!response.body || typeof response.body.getReader !== 'function') throw new Error('当前浏览器不支持流式回答，请更换浏览器后重试');
      await consumeSseStream(response, onEvent);
    },
    knowledgeClearHistory: () => apiFetch('/api/knowledge-search', { method: 'DELETE', credentials: 'same-origin' }).then(handleJson),
    toggleLike: (id) => apiFetch(`/api/reports/${id}/like`, { method: 'POST', credentials: 'same-origin' }).then(handleJson),
    recordView: (id) => apiFetch(`/api/reports/${id}/view`, { method: 'POST', credentials: 'same-origin' }).then(handleJson),
    toggleFavorite: (id) => apiFetch(`/api/reports/${id}/favorite`, { method: 'POST', credentials: 'same-origin' }).then(handleJson),
    updateReport: (id, data) => apiFetch(`/api/reports/${id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin', body: JSON.stringify(data) }).then(handleJson),
    // 路演安排表：按周（周一~周日）读取，所有人可新增，本人/行政可修改删除
    roadshowSchedule: (week) => apiFetch(`/api/roadshow-schedule${week ? `?week=${encodeURIComponent(week)}` : ''}`, { credentials: 'same-origin' }).then(handleJson),
    roadshowExport: (week) => apiFetch(`/api/roadshow-schedule/export${week ? `?week=${encodeURIComponent(week)}` : ''}`, { credentials: 'same-origin' }),
    roadshowAdd: (data) => apiFetch('/api/roadshow-schedule', { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin', body: JSON.stringify(data) }).then(handleJson),
    roadshowUpdate: (id, data) => apiFetch(`/api/roadshow-schedule/${id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin', body: JSON.stringify(data) }).then(handleJson),
    roadshowDelete: (id) => apiFetch(`/api/roadshow-schedule/${id}`, { method: 'DELETE', credentials: 'same-origin' }).then(handleJson),
    roadshowAiParse: (text, weekStart) => apiFetch('/api/roadshow-schedule/ai-parse', { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin', body: JSON.stringify({ text, weekStart }) }).then(handleJson),
    // 路演报告-路演安排匹配：候选项与手工建立/取消关联
    roadshowOptions: (date) => apiFetch(`/api/roadshow-schedule/options?date=${encodeURIComponent(date || '')}`, { credentials: 'same-origin' }).then(handleJson),
    roadshowMatch: (data) => apiFetch('/api/roadshow-schedule/match', { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin', body: JSON.stringify(data) }).then(handleJson),
    roadshowAutoMatch: (scheduleId) => apiFetch(`/api/roadshow-schedule/${scheduleId}/auto-match-report`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin', body: '{}' }).then(handleJson),
  };

  async function handleJson(res) {
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `请求失败 (${res.status})`);
    return data;
  }

  // 消费服务端 SSE 流（data: 行 JSON），逐事件回调 onEvent。知识搜索 / AI 摘要 / 就本文提问共用。
  async function consumeSseStream(response, onEvent) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let sep;
      while ((sep = buffer.indexOf('\n\n')) !== -1) {
        const frame = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        const dataLine = frame.split('\n').find(line => line.startsWith('data:'));
        if (!dataLine) continue;
        let payload = null;
        try { payload = JSON.parse(dataLine.slice(5).trim()); } catch (err) { payload = null; }
        if (payload) onEvent(payload);
      }
    }
  }

  async function uploadReports(formData, reportType) {
    const res = await apiFetch(`/api/reports/${reportType}`, { method: 'POST', credentials: 'same-origin', body: formData });
    return handleJson(res);
  }

  const state = {
    currentUser: null,
    view: 'dashboard',
    filters: { category: '', org: '', theme: '', score: '', query: '' },
    search: { query: '', reportType: 'all' },
    myReportFilters: { category: '', reportType: 'all', query: '', sort: 'date-desc' },
    reportView: 'list',
    reportListSort: { key: 'date', dir: 'desc' },
    myReportType: 'all',
    reportType: 'internal',
    ratingTab: 'pending',
    ratingFilters: { month: '', person: '', sort: 'date-desc', category: '', theme: '' },
    resultFilters: { month: '', person: '', sort: 'date-desc', category: '', theme: '' },
    reports: [],
    ratings: [],
    feedback: [],
    ratingProgress: {},
    reportScores: {},
    ratingSummary: { totalRatings: 0, participants: 0, ratedReports: 0, totalScorers: 0 },
    loading: false,
    batchMode: false,
    selected: new Set(),
    uploadFiles: [],  // 待上传文件列表 {file, title}
    uploadPreset: null,  // 预填信息（路演安排一键上传）：{reportType,title,reportDate,roadshowScheduleId,authorName}
    reportAuthors: [],
    roadshow: { weekOffset: 0, weekStart: '', weekEnd: '', items: [], wide: false },
    reminders: null,
    knowledge: { limit: 10, used: 0, remaining: 10, available: true, history: [], messages: [], questionType: 'general_work', thinking: false, conversationId: '', activeConversationId: '', draft: '', filters: { period: '1m', dateFrom: '', dateTo: '', reportTypes: [], categories: [], themes: [], authors: [] } }
  };

  const els = {};
  const dateFormatter = new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: 'long', day: 'numeric', weekday: 'long' });
  const shortDateFormatter = new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' });

  function escapeHTML(value = '') {
    return String(value).replace(/[&<>'"]/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));
  }

  function getReport(reportId) {
    return state.reports.find(report => report.id === reportId);
  }

  function getUser(userId) {
    // 不再在前端缓存全部用户，仅用于评分明细的姓名回退
    return state._userCache?.[userId] || null;
  }

  function canRate() {
    return state.currentUser && state.currentUser.role !== 'admin';
  }

  function reportScoringOrgs(report) {
    const orgs = report && Array.isArray(report.scoringOrgs) && report.scoringOrgs.length
      ? report.scoringOrgs
      : ['资产配置部', '固收中心'];
    return orgs;
  }

  // 部门领导（org 为资产配置部/固收中心）按报告所选打分部门过滤；通用领导
  // （org 为领导/行政/空）始终参与。
  function leaderInScoringScope(user, scoringOrgs) {
    const org = user && user.org;
    if (org === '资产配置部' || org === '固收中心') return scoringOrgs.includes(org);
    return true;
  }

  // 是否具备某份深度报告的评分资格：部门领导按 org 过滤，通用领导始终可评，
  // 研究人员仅当其部门在所选打分部门内。报告对所有人可见，本判断仅影响是否能进入评分流程。
  function canRateReport(report) {
    if (!canRate()) return false;
    if (isLeader()) return leaderInScoringScope(state.currentUser, reportScoringOrgs(report));
    return reportScoringOrgs(report).includes(state.currentUser.org);
  }

  function isLeader() {
    return state.currentUser && state.currentUser.role === 'leader';
  }

  function isAdminRole() {
    return state.currentUser && state.currentUser.role === 'admin';
  }

  function canDeleteReport(report) {
    if (!state.currentUser) return false;
    if (isAdminRole()) return true; // 行政可删任意报告
    return report.authorId === state.currentUser.id;
  }

  function canEditReport(report) {
    // 行政可改任意报告，其他角色仅改本人上传的报告
    if (!state.currentUser) return false;
    if (isAdminRole()) return true;
    return Boolean(report && report.authorId === state.currentUser.id);
  }

  function isOwnReport(report) {
    return Boolean(state.currentUser && report && report.authorId === state.currentUser.id);
  }

  function canViewReportTotal(report) {
    return Boolean(isLeader() || (isOwnReport(report) && Object.prototype.hasOwnProperty.call(state.reportScores, report.id)));
  }

  function roleLabel(role) {
    return ({ leader: '领导', admin: '行政', member: '研究人员' })[role] || '成员';
  }

  function initials(name) {
    return String(name || '内部').slice(-2);
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

  function reportDateValue(report) {
    const value = report?.reportDate || report?.uploadedAt || '';
    const date = new Date(String(value).length === 10 ? `${value}T00:00:00` : value);
    return Number.isNaN(date.getTime()) ? new Date(0) : date;
  }

  function monthKey(report) {
    const date = reportDateValue(report);
    if (!date.getTime()) return '';
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`;
  }

  function monthLabel(key) {
    const [year, month] = String(key).split('-');
    return year && month ? `${year}年${Number(month)}月` : key;
  }

  function isCurrentMonthReport(report) {
    const now = new Date();
    const date = reportDateValue(report);
    return date.getFullYear() === now.getFullYear() && date.getMonth() === now.getMonth();
  }

  function scoreOf(rating) {
    return Number(((Number(rating.inspiration) + Number(rating.depth) + Number(rating.utility)) / 3).toFixed(1));
  }

  function reportRatingStats(reportId) {
    const rows = state.ratings.filter(rating => rating.reportId === reportId);
    const progress = state.ratingProgress[reportId] || { done: rows.length, pending: 0, total: rows.length };
    if (!isLeader()) {
      const scores = state.reportScores[reportId] || {};
      return { count: progress.done || 0, pending: progress.pending || 0, total: progress.total || 0, overall: scores.overall ?? null, inspiration: scores.inspiration ?? null, depth: scores.depth ?? null, utility: scores.utility ?? null };
    }
    if (!rows.length) {
      return { count: progress.done || 0, pending: progress.pending || 0, total: progress.total || 0, overall: null, inspiration: null, depth: null, utility: null };
    }
    const average = key => Number((rows.reduce((sum, row) => sum + Number(row[key]), 0) / rows.length).toFixed(1));
    return {
      count: rows.length,
      pending: progress.pending || 0,
      total: progress.total || 0,
      inspiration: average('inspiration'),
      depth: average('depth'),
      utility: average('utility'),
      overall: Number((rows.reduce((sum, row) => sum + scoreOf(row), 0) / rows.length).toFixed(1))
    };
  }

  function userRating(reportId) {
    if (!state.currentUser) return null;
    return state.ratings.find(rating => rating.reportId === reportId && rating.userId === state.currentUser.id) || null;
  }

  function scoredReports() {
    return state.reports.filter(report => report.reportType === 'internal' && categories[report.category]?.scored);
  }

  async function refreshData() {
    state.loading = true;
    try {
      const [rep, rat, authorData, reminders, knowledge] = await Promise.all([
        API.reports(),
        API.ratings(),
        isAdminRole() ? API.reportAuthors() : Promise.resolve({ authors: [] }),
        API.reminders().catch(() => null),
        API.knowledgeStatus().catch(() => null)
      ]);
        state.reports = rep.reports || [];
      state.ratings = rat.ratings || [];
      state.reportAuthors = authorData.authors || [];
      state.feedback = rat.feedback || [];
      state.ratingProgress = rat.reportProgress || {};
      state.reportScores = rat.reportScores || {};
      state.ratingSummary = { ...state.ratingSummary, ...(rat.summary || {}) };
      state.reminders = reminders;
      if (knowledge) {
        // 每个人只能看到自己的历史问答：后端按当前用户过滤返回 history。
        const history = Array.isArray(knowledge.history) ? knowledge.history : [];
        // 历史按 conversationId 分组；默认打开一段新的“自由问答”，避免把互不相关的旧问题
        // 自动拼成模型上下文。用户可在左侧主动恢复任一旧会话。
        state.knowledge = { ...state.knowledge, ...knowledge, history };
      }
    } catch (error) {
      notify(error.message || '数据加载失败', 'error');
    } finally {
      state.loading = false;
    }
  }

  function notify(message, type = 'success') {
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = `<span class="toast-symbol">${type === 'success' ? '✓' : type === 'error' ? '!' : 'i'}</span><span>${escapeHTML(message)}</span>`;
    els.toastStack.appendChild(toast);
    setTimeout(() => toast.classList.add('toast-leave'), 2800);
    setTimeout(() => toast.remove(), 3150);
  }

  function setLoginError(message = '') {
    els.loginError.textContent = message;
    els.loginError.classList.toggle('show', Boolean(message));
  }

  async function login(user) {
    state.currentUser = user;
    els.loginPage.hidden = true;
    els.appShell.hidden = false;
    renderAccount();
    await refreshData();
    updateChrome();
    navigate('dashboard');
  }

  async function logout() {
    try { await API.logout(); } catch (_) { /* ignore */ }
    state.currentUser = null;
    state.reports = [];
    state.ratings = [];
    state.feedback = [];
    state.ratingProgress = {};
    state.reportScores = {};
    state.reportAuthors = [];
    state.ratingSummary = { totalRatings: 0, participants: 0, ratedReports: 0, totalScorers: 0 };
    closeModal();
    els.appShell.hidden = true;
    els.loginPage.hidden = false;
    els.loginForm.reset();
    setLoginError('');
    els.loginUsername.focus();
  }

  function renderAccount() {
    const user = state.currentUser;
    els.sidebarAccount.innerHTML = `
      <div class="account-avatar">${escapeHTML(initials(user.name))}</div>
      <div class="account-copy">
        <strong>${escapeHTML(user.name)}</strong>
        <span>${escapeHTML(user.org)} · ${roleLabel(user.role)}</span>
      </div>
      <button class="account-action" data-action="change-password" title="修改密码" aria-label="修改密码">
        <svg viewBox="0 0 24 24" fill="none"><rect x="4" y="10" width="16" height="11" rx="2" stroke="currentColor" stroke-width="1.8"/><path d="M8 10V7a4 4 0 0 1 8 0v3" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/><path d="M12 14v3" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>
      </button>
      <button class="account-logout" data-action="logout" title="退出登录" aria-label="退出登录">
        <svg viewBox="0 0 24 24" fill="none"><path d="M10 5H6a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h4M15 8l4 4-4 4M19 12H9" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>
      </button>`;
  }

  function updateChrome() {
    const pending = canRate() ? scoredReports().filter(report => canRateReport(report) && isCurrentMonthReport(report) && !userRating(report.id)).length : 0;
    els.pendingNavBadge.textContent = pending;
    els.pendingNavBadge.hidden = !pending;
    els.topbarDate.textContent = dateFormatter.format(new Date());
  }

  function navigate(view, options = {}) {
    state.view = view;
    state.batchMode = false;
    state.selected.clear();
    if (Object.prototype.hasOwnProperty.call(options, 'category')) state.filters.category = options.category || '';
    if (Object.prototype.hasOwnProperty.call(options, 'theme')) state.filters.theme = options.theme || '';
    const titles = { dashboard: '首页总览', reports: '内部报告', 'search-results': '搜索结果', 'external-reports': '外部报告', 'research-reports': '调研报告', 'roadshow-reports': '路演报告', 'my-reports': '我的报告', rating: '待我评分', results: '评分结果', 'knowledge-search': '知识搜索' };
    els.topbarTitle.textContent = titles[view] || config.name;
    document.querySelectorAll('.nav-item').forEach(item => {
      const exactView = item.dataset.view === view;
      const categoryMatch = view === 'reports' && item.dataset.category && item.dataset.category === state.filters.category;
      const themeMatch = view === 'reports' && item.dataset.theme && item.dataset.theme === state.filters.theme;
      const hasFilter = state.filters.category || state.filters.theme;
      const typeMatch = (view === 'research-reports' || view === 'roadshow-reports') && item.dataset.reportType === state.reportType;
      const mainMatch = (exactView || typeMatch) && !item.dataset.category && !item.dataset.theme && !(view === 'reports' && hasFilter);
      item.classList.toggle('active', categoryMatch || themeMatch || mainMatch);
    });
    closeSidebar();
    renderView();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  function renderView() {
    if (!state.currentUser) return;
    if (state.view === 'reports') renderReports();
    else if (state.view === 'search-results') renderSearchResults();
    else if (state.view === 'external-reports') renderExternalReports();
    else if (state.view === 'research-reports' || state.view === 'roadshow-reports') renderTypedReports(state.reportType);
    else if (state.view === 'my-reports') renderMyReports();
    else if (state.view === 'rating') renderRatingCenter();
    else if (state.view === 'results') renderResults();
    else if (state.view === 'knowledge-search') renderKnowledgeSearch();
    else renderDashboard();
    updateChrome();
  }

  function categoryPill(category) {
    const meta = categories[category] || categories.other;
    return `<span class="category-pill ${escapeHTML(category)}"><span></span>${escapeHTML(meta.label)}</span>`;
  }

  function themePill(theme) {
    const meta = themes[theme];
    if (!meta) return '';
    return `<span class="theme-pill ${escapeHTML(theme)}"><span></span>${escapeHTML(meta.label)}</span>`;
  }

  function renderDashboard() {
    const allReports = state.reports;
    const eligible = scoredReports();
    const currentEligible = eligible.filter(isCurrentMonthReport);
    const myCompleted = canRate() ? currentEligible.filter(report => userRating(report.id)).length : 0;
    const totalRatings = state.ratingSummary.totalRatings || 0;
    const recent = [...allReports].sort((a, b) => new Date(b.uploadedAt) - new Date(a.uploadedAt)).slice(0, 5);
    const progress = currentEligible.length ? Math.round((myCompleted / currentEligible.length) * 100) : 0;
    const greeting = new Date().getHours() < 12 ? '上午好' : new Date().getHours() < 18 ? '下午好' : '晚上好';
    const pendingCount = canRate() ? currentEligible.filter(report => !userRating(report.id)).length : currentEligible.filter(report => reportRatingStats(report.id).count === 0).length;
    const pendingSub = canRate() ? (pendingCount ? `剩余 ${pendingCount} 份待我评分` : '已全部完成评分') : (pendingCount ? `${pendingCount} 份尚无评分` : '均已收到评分');

    els.viewRoot.innerHTML = `
      <section class="welcome-block">
        <div>
          <h1>${greeting}，${escapeHTML(state.currentUser.name)}</h1>
          <p>${state.currentUser.role === 'admin' ? '这里汇总内部报告与团队评分进展，您可以管理归档并查看评分概览。' : '今天也从一份好研究开始。报告归档、评分与团队反馈都在这里。'}</p>
        </div>
        <button class="btn btn-primary btn-large" data-action="open-upload">
          <svg viewBox="0 0 24 24" fill="none"><path d="M12 5v14M5 12h14" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
          上传新报告
        </button>
      </section>

      <section class="dashboard-grid dashboard-home-grid">
        <div class="panel-card recent-panel">
          <div class="panel-header">
            <div><h2>最近更新</h2><p>平台最新归档的报告</p></div>
            <button class="text-button" data-action="go-reports">查看全部 <span>→</span></button>
          </div>
          <div class="recent-list">
            ${recent.map(report => recentReportRow(report)).join('') || emptyState('暂无报告', '上传第一份内部报告后会显示在这里。')}
          </div>
        </div>
        <div class="panel-card hot-panel">
          <div class="panel-header hot-panel-header"><h2>热点报告</h2><select id="hotReportType" class="select-control"><option value="all">全部报告</option><option value="internal">内部报告</option><option value="external">外部报告</option><option value="research_visit">调研报告</option><option value="roadshow">路演报告</option></select></div>
          <div class="recent-list hot-list" id="hotReportList">${renderHotReports('all')}</div>
        </div>
      </section>
      ${renderRoadshowPanel()}
      ${renderWorkReminder()}
      ${renderAdminStatsPanel()}`;
    loadRoadshowSchedule();
  }

  function renderWorkReminder() {
    const data = state.reminders;
    if (!data) return '';
    const summary = data.summary || { target: 0, completed: 0, remaining: 0 };
    const people = (data.people || []).filter(person => person.count > 0);
    const groupedReports = reports => {
      const groups = {};
      (reports || []).forEach(report => {
        const key = report.reportType === 'external' ? 'external' : (report.category || 'other');
        (groups[key] ||= []).push(report);
      });
      const order = ['weekly', 'monthly', 'deep', 'research_visit', 'roadshow', 'other', 'external'];
      return order.filter(key => groups[key]?.length).map(key => {
        const label = key === 'external' ? '外部报告' : (categories[key]?.label || '其他报告');
        const reportsInGroup = groups[key];
        return `<div class="reminder-report-group"><span class="reminder-group-label">${escapeHTML(label)}<b>${reportsInGroup.length}</b></span><ul>${reportsInGroup.map(report => `<li title="${escapeHTML(report.title)}">${escapeHTML(report.title)}</li>`).join('')}</ul></div>`;
      }).join('');
    };
    const peopleTitle = isAdminRole() || isLeader()
      ? '本自然年已上传报告（含内外部）'
      : '本自然年已上传报告（本人及关联要求）';
    return `<section class="work-reminder panel-card">
      <div class="panel-header"><div><h2>工作提醒</h2><p>${escapeHTML(data.period)} 自然年度专题报告进度与团队上传情况</p></div><span class="reminder-total">剩余 <strong>${summary.remaining}</strong> / ${summary.target} 篇</span></div>
      <div class="reminder-layout">
        <div class="reminder-rules">
          ${(data.rules || []).map(rule => {
            const percent = rule.target ? Math.min(100, Math.round(rule.completed / rule.target * 100)) : 100;
            return `<article class="reminder-rule ${rule.remaining ? '' : 'complete'}"><div><strong>${escapeHTML(rule.label)}</strong><span>${escapeHTML((rule.members || []).join('、'))}</span></div><b>${rule.completed}/${rule.target}</b><i><em style="width:${percent}%"></em></i><small>${rule.remaining ? `还需 ${rule.remaining} 篇` : '已完成'}</small></article>`;
          }).join('') || emptyState('暂无专题要求', '超级管理员可在后台维护提醒参数。')}
        </div>
        <div class="reminder-people"><h3>${peopleTitle}</h3>${people.length ? people.map(person => `<div class="reminder-person"><span class="account-avatar">${escapeHTML(initials(person.name))}</span><div><strong>${escapeHTML(person.name)}</strong><div class="reminder-person-reports">${groupedReports(person.reports)}</div></div><b>${person.count} 篇</b></div>`).join('') : '<p class="reminder-empty">本自然年暂未上传报告</p>'}</div>
      </div>
    </section>`;
  }

  function reportTypeLabel(type) {
    return ({ internal: '内部报告', external: '外部报告', research_visit: '调研报告', roadshow: '路演报告' })[type] || '报告';
  }

  function engagementScore(report) {
    if (report.reportType === 'internal') return Number(report.viewCount || 0);
    return Number(report.likeCount || 0) * 3 + Number(report.viewCount || 0) + Number(report.favoriteCount || 0) * 2;
  }

  function renderHotReports(type = 'all') {
    const rows = state.reports.filter(r => type === 'all' || r.reportType === type)
      .sort((a, b) => engagementScore(b) - engagementScore(a) || reportDateValue(b) - reportDateValue(a)).slice(0, 5);
    return rows.map((report, index) => recentReportRow(report, index + 1)).join('') || emptyState('暂无热点报告', '有更多互动后会在这里展示。');
  }

  function recentReportRow(report, rank = null) {
    const stats = reportRatingStats(report.id);
    const scored = report.reportType === 'internal' && categories[report.category]?.scored;
    return `<article class="recent-row">
      <span class="recent-file-wrap"><button class="report-file-icon" data-action="view-report" data-id="${report.id}">${escapeHTML(report.fileType || 'FILE')}</button>${rank ? '<b class="hot-rank" title="热点报告" aria-label="热点报告">🔥</b>' : ''}</span>
      <button class="recent-main" data-action="view-report" data-id="${report.id}">
        <strong>${escapeHTML(report.title)}</strong>
        <span>${escapeHTML(report.author)} · ${formatDate(report.reportDate)}</span>
      </button>
       ${['external', 'research_visit', 'roadshow'].includes(report.reportType) ? `<span class="external-badge">${reportTypeLabel(report.reportType)}</span>` : categoryPill(report.category)}
      ${themePill(report.theme)}
      <button class="icon-button" data-action="download" data-id="${report.id}" title="下载">
        <svg viewBox="0 0 24 24" fill="none"><path d="M12 3v12M7 10l5 5 5-5M5 20h14" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>
      </button>
    </article>`;
  }

  function renderMyReports() {
    const owned = state.reports.filter(isOwnReport);
    const ownedInternal = owned.filter(report => report.reportType === 'internal');
    const ownedExternal = owned.filter(report => report.reportType === 'external');
    const scored = ownedInternal.filter(report => categories[report.category]?.scored);
    const rated = scored.filter(report => reportRatingStats(report.id).count > 0);
    const waiting = scored.length - rated.length;
    const totals = rated.map(report => reportRatingStats(report.id).overall).filter(score => score != null);
    const averageTotal = totals.length ? Number((totals.reduce((sum, score) => sum + score, 0) / totals.length).toFixed(1)) : null;
    const filters = state.myReportFilters;
    const query = filters.query.trim().toLowerCase();
    const baseReports = filters.reportType === 'favorites' ? state.reports.filter(report => report.favoritedByMe) : owned.filter(report => filters.reportType === 'all' || report.reportType === filters.reportType);
    const filtered = baseReports.filter(report => {
      const matchesCategory = !filters.category || report.category === filters.category;
      const haystack = [report.title, report.summary, report.fileName, ...(report.tags || [])].join(' ').toLowerCase();
      return matchesCategory && (!query || haystack.includes(query));
    }).sort((a, b) => {
      if (filters.sort === 'date-asc') return reportDateValue(a) - reportDateValue(b);
      if (filters.sort === 'score-desc') return (reportRatingStats(b.id).overall ?? -1) - (reportRatingStats(a.id).overall ?? -1) || reportDateValue(b) - reportDateValue(a);
      return reportDateValue(b) - reportDateValue(a);
    });
    const scoreOverview = [...scored].sort((a, b) => {
      const scoreDiff = (reportRatingStats(b.id).overall ?? -1) - (reportRatingStats(a.id).overall ?? -1);
      return scoreDiff || reportDateValue(b) - reportDateValue(a);
    });

    els.viewRoot.innerHTML = `
      <section class="welcome-block">
        <div>
          <h1>我的报告</h1>
        </div>
        <button class="btn btn-primary btn-large" data-action="open-upload"><svg viewBox="0 0 24 24" fill="none"><path d="M12 5v14M5 12h14" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>上传新报告</button>
      </section>

      <section class="my-report-metrics">
        <article><span>内部报告</span><strong>${ownedInternal.length}</strong><small>优先展示内部成果</small></article>
        <article><span>外部推荐</span><strong>${ownedExternal.length}</strong><small>推荐给团队的报告</small></article>
        <article><span>需团队评分</span><strong>${scored.length}</strong><small>深度报告</small></article>
        <article class="score"><span>已收到评分</span><strong>${rated.length}</strong><small>${waiting ? `${waiting} 份等待反馈` : '均已收到反馈'}</small></article>
      </section>

      <section class="panel-card my-score-panel">
        <div class="panel-header"><div><h2>评分概览</h2><p>仅展示汇总平均分和参与进度，不展示任何评分人明细</p></div><span class="permission-note">仅本人报告可见</span></div>
        <div class="my-score-list">
          ${scoreOverview.length ? scoreOverview.map(report => myReportScoreRow(report)).join('') : emptyState('暂无需评分报告', '上传深度报告后，可在这里查看团队评分总分。')}
        </div>
      </section>

      <section class="my-report-library-head">
        <div><h2>报告库</h2><p>评分概览下方可切换本人署名报告与收藏报告</p></div>
        ${renderViewToggle()}
      </section>
      <div class="category-tabs">
        ${[['all','全部',owned.length],['internal','内部报告',ownedInternal.length],['external','外部报告',ownedExternal.length],['research_visit','调研报告',owned.filter(r => r.reportType === 'research_visit').length],['roadshow','路演报告',owned.filter(r => r.reportType === 'roadshow').length],['favorites','收藏报告',state.reports.filter(r => r.favoritedByMe).length]].map(([key,label,count]) => `<button class="category-tab ${filters.reportType === key ? 'active' : ''}" data-action="set-my-report-type" data-report-type="${key}">${label} <span>${count}</span></button>`).join('')}
      </div>
      <section class="filter-panel my-report-filter-panel">
        <label class="search-field"><svg viewBox="0 0 24 24" fill="none"><circle cx="11" cy="11" r="7" stroke="currentColor" stroke-width="1.8"/><path d="m16.5 16.5 4 4" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg><input id="myReportSearch" value="${escapeHTML(filters.query)}" placeholder="搜索我的标题、摘要、关键词或文件名"></label>
        <select id="myReportSort" class="select-control" aria-label="我的报告排序"><option value="date-desc" ${filters.sort === 'date-desc' ? 'selected' : ''}>日期：由近到远</option><option value="date-asc" ${filters.sort === 'date-asc' ? 'selected' : ''}>日期：由远到近</option><option value="score-desc" ${filters.sort === 'score-desc' ? 'selected' : ''}>总分：由高到低</option></select>
        <button class="btn btn-ghost" data-action="reset-my-report-filters">重置</button>
      </section>
      <div class="result-bar"><span>共找到 <strong>${filtered.length}</strong> 份本人报告</span><span>${filters.sort === 'score-desc' ? '按汇总平均分排序' : '按报告日期排序'}</span></div>
      <section class="report-grid ${state.reportView === 'list' ? 'report-list-view' : ''}">
        ${renderReportCollection(filtered, '没有匹配的报告', '切换其他分类，或收藏一份报告。')}
      </section>`;
  }

  function myReportScoreRow(report) {
    const stats = reportRatingStats(report.id);
    const progress = stats.total ? Math.round((stats.count / stats.total) * 100) : 0;
    return `<button class="my-score-row" data-action="view-results" data-id="${report.id}">
      <span class="my-score-doc ${report.category}">${report.category === 'deep' ? '深' : '月'}</span>
      <span class="my-score-copy"><strong>${escapeHTML(report.title)}</strong><small>${formatDate(report.reportDate)} · ${themes[report.theme]?.label || '未分类'}</small></span>
      <span class="my-score-progress"><i><b style="width:${progress}%"></b></i><small>${stats.count} / ${stats.total} 人已评</small></span>
      <span class="my-score-total"><small>汇总平均分</small><strong>${stats.overall ?? '—'}</strong></span>
      <span class="my-score-arrow">→</span>
    </button>`;
  }

  function renderViewToggle() {
    return `<div class="view-switch" aria-label="报告展示方式"><button class="view-toggle ${state.reportView === 'list' ? 'active' : ''}" data-action="set-report-view" data-view-mode="list">☷ 列表</button><button class="view-toggle ${state.reportView === 'card' ? 'active' : ''}" data-action="set-report-view" data-view-mode="card">▦ 卡片</button></div>`;
  }

  function renderReportCollection(reports, emptyTitle, emptyDescription, options = {}) {
    if (!reports.length) return `<div class="empty-grid">${emptyState(emptyTitle, emptyDescription)}</div>`;
    if (state.reportView !== 'list') return reports.map(report => reportCard(report, options)).join('');
    // 列表排序：日期/收藏数/浏览数，同列再点切换升降序；并列时按日期补齐
    const comparators = {
      date: (a, b) => reportDateValue(a) - reportDateValue(b),
      favorite: (a, b) => (a.favoriteCount || 0) - (b.favoriteCount || 0) || reportDateValue(a) - reportDateValue(b),
      view: (a, b) => (a.viewCount || 0) - (b.viewCount || 0) || reportDateValue(a) - reportDateValue(b),
    };
    const sort = state.reportListSort;
    const comparator = comparators[sort.key] || comparators.date;
    const listReports = options.preserveOrder ? reports : [...reports].sort((a, b) => sort.dir === 'asc' ? comparator(a, b) : comparator(b, a));
    const sortHeader = (key, label) => options.preserveOrder
      ? (key === 'date' ? `<span>日期（相关度优先）</span>` : `<span>${label}</span>`)
      : `<button data-action="sort-report-list" data-sort-key="${key}">${label}${sort.key === key ? ` <b>${sort.dir === 'asc' ? '↑' : '↓'}</b>` : ''}</button>`;
    return `<div class="report-list-header">
      <span></span><span>报告</span>${sortHeader('date', '日期')}<span>标签</span><span>评分</span>${sortHeader('favorite', '收藏')}${sortHeader('view', '浏览')}<span>在线查看</span><span></span><span></span>
    </div>${listReports.map(report => reportListRow(report, options)).join('')}`;
  }

  function renderReports() {
    const all = state.reports.filter(report => report.reportType === 'internal');
    const query = state.filters.query.trim().toLowerCase();
    const filtered = all.filter(report => {
      const matchesCategory = !state.filters.category || report.category === state.filters.category;
      const matchesTheme = !state.filters.theme || report.theme === state.filters.theme;
      const matchesOrg = !state.filters.org || report.org === state.filters.org;
      const rating = userRating(report.id);
      const matchesScore = !state.filters.score || (state.filters.score === 'rated' ? Boolean(rating) : categories[report.category]?.scored && isCurrentMonthReport(report) && !rating);
      const haystack = [report.title, report.author, report.org, report.summary, ...(report.tags || [])].join(' ').toLowerCase();
      return matchesCategory && matchesTheme && matchesOrg && matchesScore && (!query || haystack.includes(query));
    }).sort((a, b) => new Date(b.uploadedAt) - new Date(a.uploadedAt));

    els.viewRoot.innerHTML = `
      <section class="page-heading">
        <div><h1>内部报告</h1><p>统一浏览、筛选与下载全部内部研究成果。</p></div>
        <div class="heading-actions">${renderViewToggle()}<button class="btn btn-primary" data-action="open-upload"><svg viewBox="0 0 24 24" fill="none"><path d="M12 5v14M5 12h14" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>上传报告</button></div>
      </section>
      <div class="category-tabs">
        <button class="category-tab ${!state.filters.category ? 'active' : ''}" data-action="set-report-category" data-category="">全部 <span>${all.length}</span></button>
        ${Object.entries(categories).map(([key, meta]) => `<button class="category-tab ${state.filters.category === key ? 'active' : ''}" data-action="set-report-category" data-category="${key}">${meta.label} <span>${all.filter(r => r.category === key).length}</span></button>`).join('')}
      </div>
      <div class="theme-tabs">
        <button class="theme-tab ${!state.filters.theme ? 'active' : ''}" data-action="set-report-theme" data-theme="">全部主题</button>
        ${Object.entries(themes).map(([key, meta]) => `<button class="theme-tab ${key} ${state.filters.theme === key ? 'active' : ''}" data-action="set-report-theme" data-theme="${key}"><span class="theme-tab-dot ${key}"></span>${meta.label} <span>${all.filter(r => r.theme === key).length}</span></button>`).join('')}
      </div>
      <section class="filter-panel">
        <label class="search-field">
          <svg viewBox="0 0 24 24" fill="none"><circle cx="11" cy="11" r="7" stroke="currentColor" stroke-width="1.8"/><path d="m16.5 16.5 4 4" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>
          <input id="reportSearch" value="${escapeHTML(state.filters.query)}" placeholder="搜索标题、作者、标签或摘要">
        </label>
        <select id="orgFilter" class="select-control" aria-label="按部门筛选">
          <option value="">全部部门</option>
          <option value="资产配置部" ${state.filters.org === '资产配置部' ? 'selected' : ''}>资产配置部</option>
          <option value="固收中心" ${state.filters.org === '固收中心' ? 'selected' : ''}>固收中心</option>
        </select>
        ${canRate() ? `<select id="scoreFilter" class="select-control" aria-label="按评分状态筛选"><option value="">全部评分状态</option><option value="pending" ${state.filters.score === 'pending' ? 'selected' : ''}>待我评分</option><option value="rated" ${state.filters.score === 'rated' ? 'selected' : ''}>我已评分</option></select>` : ''}
        <button class="btn btn-ghost" data-action="reset-report-filters">重置</button>
      </section>
      <div class="result-bar"><span>共找到 <strong>${filtered.length}</strong> 份报告</span><span>按更新时间排序</span></div>
      <section class="report-grid ${state.reportView === 'list' ? 'report-list-view' : ''}">
        ${renderReportCollection(filtered, '没有匹配的报告', '尝试清除筛选条件，或上传一份新报告。')}
       </section>`;
  }

  function reportSearchPriority(report, query) {
    const matches = values => values.some(value => String(value || '').toLowerCase().includes(query));
    if (matches([report.title])) return 0;
    const metadata = [report.author, report.org, ...(report.tags || [])];
    if (report.reportType === 'external') metadata.push(report.sourceAuthor, report.sourceInstitution);
    if (matches(metadata)) return 1;
    const details = [report.summary];
    if (report.reportType === 'external') details.push(report.recommendation);
    return matches(details) ? 2 : -1;
  }

  function getSearchMatches() {
    const query = state.search.query.trim().toLowerCase();
    if (!query) return [];
    return state.reports.map(report => ({ report, priority: reportSearchPriority(report, query) }))
      .filter(item => item.priority >= 0)
      .sort((a, b) => a.priority - b.priority || reportDateValue(b.report) - reportDateValue(a.report) || new Date(b.report.uploadedAt || 0) - new Date(a.report.uploadedAt || 0));
  }

  function renderSearchResults() {
    const query = state.search.query.trim();
    const typeOrder = ['all', 'internal', 'external', 'research_visit', 'roadshow'];
    const matches = getSearchMatches();
    const counts = Object.fromEntries(typeOrder.map(type => [type, type === 'all' ? matches.length : matches.filter(item => item.report.reportType === type).length]));
    const visible = state.search.reportType === 'all' ? matches : matches.filter(item => item.report.reportType === state.search.reportType);
    const hasQuery = Boolean(query);
    if (els.globalSearch.value !== query) els.globalSearch.value = query;

    els.viewRoot.innerHTML = `
      <section class="page-heading search-results-heading">
        <div><span class="page-kicker">全库检索</span><h1>搜索结果</h1><p>${hasQuery ? `“${escapeHTML(query)}” 共找到 ${matches.length} 份报告` : '输入关键词，检索内部、外部、调研与路演报告。'}</p></div>
        <div class="heading-actions">${hasQuery ? renderViewToggle() : ''}</div>
      </section>
      <section class="search-results-box">
        <label class="search-field search-results-input">
          <svg viewBox="0 0 24 24" fill="none"><circle cx="11" cy="11" r="7" stroke="currentColor" stroke-width="1.8"/><path d="m16.5 16.5 4 4" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>
          <input id="searchResultInput" value="${escapeHTML(query)}" placeholder="搜索报告标题、作者、机构、标签或关键词" aria-label="搜索全部报告">
        </label>
        ${hasQuery ? '<button class="btn btn-ghost" data-action="clear-search">清空</button>' : ''}
      </section>
      ${hasQuery ? `
        <div class="category-tabs search-type-tabs">
          ${typeOrder.map(type => `<button class="category-tab ${state.search.reportType === type ? 'active' : ''}" data-action="set-search-report-type" data-report-type="${type}">${type === 'all' ? '全部报告' : reportTypeLabel(type)} <span>${counts[type]}</span></button>`).join('')}
        </div>
        <div class="result-bar"><span>当前显示 <strong>${visible.length}</strong> 份报告</span><span>按相关度与报告日期排序</span></div>
        <section class="report-grid ${state.reportView === 'list' ? 'report-list-view' : ''}">${renderReportCollection(visible.map(item => item.report), '没有匹配的报告', '尝试切换报告类型，或修改搜索关键词。', { searchResult: true, preserveOrder: true })}</section>`
        : `<section class="empty-grid search-empty-grid">${emptyState('开始搜索全部报告', '可搜索标题、摘要、标签、作者、部门，以及外部报告的原作者、机构和推荐语。')}</section>`}`;
  }

  function searchResultTypeBadge(report) {
    const category = report.reportType === 'internal' ? categories[report.category] || categories.other : null;
    const label = category ? `${reportTypeLabel(report.reportType)} · ${category.label}` : reportTypeLabel(report.reportType);
    return `<span class="search-result-type type-${escapeHTML(report.reportType || 'internal')}">${escapeHTML(label)}</span>`;
  }

  function reportCard(report, options = {}) {
    const searchResult = Boolean(options.searchResult);
    const category = categories[report.category] || categories.other;
    const external = report.reportType === 'external';
    // 外部报告与路演报告均展示报告作者/机构与内部上传人
    const extLike = external || report.reportType === 'roadshow';
    const typed = ['research_visit', 'roadshow'].includes(report.reportType);
    const stats = reportRatingStats(report.id);
    const mine = userRating(report.id);
    const isCurrent = isCurrentMonthReport(report);
    return `<article class="report-card ${external ? 'external-report-card' : ''}">
      <div class="report-card-top">
        ${searchResult ? searchResultTypeBadge(report) : (external || typed ? `<span class="external-badge">${reportTypeLabel(report.reportType)}</span>` : categoryPill(report.category))}
        <span class="file-type">${escapeHTML(report.fileType || 'FILE')}</span>
      </div>
      <button class="report-title-button" data-action="view-report" data-id="${report.id}">${escapeHTML(report.title)}</button>
      ${external && report.recommendation ? `<div class="recommendation-line"><span>推荐语</span>${escapeHTML(report.recommendation)}</div>` : ''}
      <p class="report-summary">${escapeHTML(report.summary || '暂无摘要')}</p>
      <div class="report-tags">${(report.tags || []).slice(0, 3).map(tag => `<span>${escapeHTML(tag)}</span>`).join('')}</div>
      ${searchResult ? `<div class="search-result-card-pills">${themePill(report.theme)}</div>` : ''}
      <div class="report-meta"><span><b>${escapeHTML(extLike ? (report.sourceAuthor || report.author) : report.author)}</b> · ${escapeHTML(extLike ? (report.sourceInstitution || report.org) : report.org)}</span><span>${formatDate(report.reportDate)}</span></div>
      ${extLike ? `<div class="uploaded-by-line">上传人：${escapeHTML(report.uploadedByName || '未记录')}</div>` : ''}
      <div class="external-engagement">${external || typed ? `<button class="like-button ${report.likedByMe ? 'liked' : ''}" data-action="toggle-like" data-id="${report.id}">♥ <span>${report.likeCount || 0}</span></button>` : ''}<button class="favorite-button ${report.favoritedByMe ? 'favorited' : ''}" data-action="toggle-favorite" data-id="${report.id}">★ <span>${report.favoriteCount || 0}</span></button><span>浏览 ${report.viewCount || 0}</span><em>${reportTypeLabel(report.reportType)}</em></div>
      ${!external && !typed && category.scored ? `<div class="report-score-strip">
        ${canViewReportTotal(report) ? `<span class="score-value ${stats.count ? '' : 'empty'}" title="团队评分总分">${stats.overall ?? '—'}</span>` : ''}
        <span>${stats.count ? `${stats.count} 人参与 · ${stats.pending} 人未评` : '暂无团队反馈'}</span>
        ${mine ? '<em>我已评分</em>' : canRateReport(report) && isCurrent ? '<em class="pending">待我评分</em>' : canRateReport(report) ? '<em class="history">历史报告</em>' : ''}
      </div>` : '<div class="report-score-strip unscored"><span>此类报告无需评分</span></div>'}
      <div class="report-actions">
        <button class="btn btn-ghost btn-small" data-action="view-report" data-id="${report.id}">${canEditReport(report) ? '查看详情/修改' : '查看详情'}</button>
        <button class="btn btn-ghost btn-small" data-action="preview-report" data-id="${report.id}">在线查看</button>
        <button class="btn btn-ghost btn-small" data-action="download" data-id="${report.id}"><svg viewBox="0 0 24 24" fill="none"><path d="M12 3v12M7 10l5 5 5-5M5 20h14" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>下载</button>
        ${!external && !typed && category.scored && canRateReport(report) ? (mine ? `<button class="btn btn-secondary btn-small" data-action="view-my-rating" data-id="${report.id}">查看我的评分</button>` : `<button class="btn ${isCurrent ? 'btn-primary' : 'btn-secondary'} btn-small" data-action="open-rating" data-id="${report.id}">${isCurrent ? '立即评分' : '补充评分'}</button>`) : !external && !typed && category.scored ? `<button class="btn btn-secondary btn-small" data-action="view-results" data-id="${report.id}">查看汇总</button>` : ''}
        ${canDeleteReport(report) ? `<button class="btn btn-danger btn-small danger-action" data-action="delete-report" data-id="${report.id}">删除</button>` : ''}
      </div>
    </article>`;
  }

  function reportListRow(report, options = {}) {
    const searchResult = Boolean(options.searchResult);
    const internal = report.reportType === 'internal';
    const mine = userRating(report.id);
    const category = categories[report.category] || categories.other;
    const typeBadge = searchResult ? searchResultTypeBadge(report) : (internal ? categoryPill(report.category) : `<span class="external-badge">${reportTypeLabel(report.reportType)}</span>`);
    const ratingAction = internal && category.scored
      ? canRateReport(report)
        ? `<button class="report-list-rating ${mine ? 'rated' : ''}" data-action="${mine ? 'view-my-rating' : 'open-rating'}" data-id="${report.id}">${mine ? '已评分' : '评分'}</button>`
        : `<button class="report-list-rating" data-action="view-results" data-id="${report.id}">查看</button>`
      : '<span class="report-list-empty">—</span>';
    return `<article class="report-list-row">
      <button class="report-file-icon" data-action="view-report" data-id="${report.id}">${escapeHTML(report.fileType || 'FILE')}</button>
      <button class="report-list-main" data-action="view-report" data-id="${report.id}"><strong>${escapeHTML(report.title)}</strong><span>${escapeHTML(['external', 'roadshow'].includes(report.reportType) ? (report.sourceAuthor || report.author) : report.author)} · ${escapeHTML(['external', 'roadshow'].includes(report.reportType) ? (report.sourceInstitution || report.org) : report.org)}${['external', 'roadshow'].includes(report.reportType) ? ` · 上传人：${escapeHTML(report.uploadedByName || '未记录')}` : ''}</span></button>
      <time class="report-list-date" datetime="${escapeHTML(report.reportDate || '')}">${formatDate(report.reportDate)}</time>
      <div class="report-list-badges">${typeBadge}${themePill(report.theme)}</div>
      <div class="report-list-rating-cell">${ratingAction}</div>
      <button class="report-list-favorite ${report.favoritedByMe ? 'active' : ''}" data-action="toggle-favorite" data-id="${report.id}" title="${report.favoritedByMe ? '取消收藏' : '收藏'}">★ <span>${report.favoriteCount || 0}</span></button>
      <span class="report-list-views">${report.viewCount || 0}</span>
      <button class="report-list-preview" data-action="preview-report" data-id="${report.id}">在线查看</button>
      <button class="icon-button report-list-download" data-action="download" data-id="${report.id}" title="下载"><svg viewBox="0 0 24 24" fill="none"><path d="M12 3v12M7 10l5 5 5-5M5 20h14" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg></button>
      ${canDeleteReport(report) ? `<button class="icon-button list-delete" data-action="delete-report" data-id="${report.id}" title="删除">×</button>` : '<span class="report-list-empty"></span>'}
    </article>`;
  }

  function renderExternalReports() {
    const query = state.filters.query.trim().toLowerCase();
    const all = state.reports.filter(report => report.reportType === 'external');
    const filtered = all.filter(report => {
      const haystack = [report.title, report.author, report.sourceAuthor, report.sourceInstitution, report.summary, report.recommendation, ...(report.tags || [])].join(' ').toLowerCase();
      return !query || haystack.includes(query);
    }).sort((a, b) => (b.likeCount || 0) - (a.likeCount || 0) || new Date(b.uploadedAt) - new Date(a.uploadedAt));
    els.viewRoot.innerHTML = `<section class="page-heading external-heading"><div><span class="page-kicker">团队精选</span><h1>外部报告</h1><p>分享值得阅读的外部研究。所有人均可点赞、收藏与浏览。</p></div><div class="heading-actions">${renderViewToggle()}<button class="btn btn-primary" data-action="open-upload"><svg viewBox="0 0 24 24" fill="none"><path d="M12 5v14M5 12h14" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>推荐外部报告</button></div></section>
      <section class="filter-panel"><label class="search-field"><svg viewBox="0 0 24 24" fill="none"><circle cx="11" cy="11" r="7" stroke="currentColor" stroke-width="1.8"/><path d="m16.5 16.5 4 4" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg><input id="externalReportSearch" value="${escapeHTML(state.filters.query)}" placeholder="搜索外部报告、推荐语或关键词"></label><span class="external-sort-note">按点赞数与更新时间排序</span></section>
      <div class="result-bar"><span>共 <strong>${filtered.length}</strong> 份外部推荐报告</span><span>${all.reduce((sum, report) => sum + (report.likeCount || 0), 0)} 次点赞</span></div>
      <section class="report-grid external-grid ${state.reportView === 'list' ? 'report-list-view' : ''}">${renderReportCollection(filtered, '还没有外部推荐报告', '上传第一份外部报告，与团队分享值得阅读的研究。')}</section>`;
  }

  function renderTypedReports(type) {
    state.reportType = type;
    const query = state.filters.query.trim().toLowerCase();
    const all = state.reports.filter(report => report.reportType === type);
    const filtered = all.filter(report => [report.title, report.author, report.org, report.summary, ...(report.tags || [])].join(' ').toLowerCase().includes(query)).sort((a, b) => reportDateValue(b) - reportDateValue(a));
    const label = reportTypeLabel(type);
    els.viewRoot.innerHTML = `<section class="page-heading"><div><span class="page-kicker">专项归档</span><h1>${label}</h1></div><div class="heading-actions">${renderViewToggle()}<button class="btn btn-primary" data-action="open-upload"><svg viewBox="0 0 24 24" fill="none"><path d="M12 5v14M5 12h14" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>上传${label}</button></div></section><section class="filter-panel"><label class="search-field"><svg viewBox="0 0 24 24" fill="none"><circle cx="11" cy="11" r="7" stroke="currentColor" stroke-width="1.8"/><path d="m16.5 16.5 4 4" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg><input id="typedReportSearch" value="${escapeHTML(state.filters.query)}" placeholder="搜索${label}标题、作者或关键词"></label></section><div class="result-bar"><span>共 <strong>${filtered.length}</strong> 份${label}</span><span>按更新时间排序</span></div><section class="report-grid ${state.reportView === 'list' ? 'report-list-view' : ''}">${renderReportCollection(filtered, '暂无报告', `上传第一份${label}后会显示在这里。`)}</section>`;
  }

  // ----------------------------------------------------------------------- //
  // 路演安排表（调研报告页上方，周历式日程图表：顶部周一~周五，左侧时间段，
  // 路演块按日期与时间纵向占位，同一时间段多个路演并排显示）
  // ----------------------------------------------------------------------- //
  const ROADSHOW_CAL_START = 8 * 60;     // 网格开始时间 08:00（分钟）
  const ROADSHOW_CAL_END = 20 * 60;      // 网格结束时间 20:00
  const ROADSHOW_SLOT_MIN = 30;          // 每格 30 分钟
  const ROADSHOW_DEFAULT_DURATION = 60;  // 未填结束时间时默认占 1 小时
  const ROADSHOW_DAY_NAMES = ['一', '二', '三', '四', '五'];
  // 一屏可见行数预算（8:00–17:30 共 19 行，与 CSS --roadshow-row-h 的 19 对应）；
  // 网格不足此行数时无纵向滚动条，超出时滚动查看
  const ROADSHOW_VIEW_ROWS = 19;

  // 行高来自 CSS 变量 --roadshow-row-h（随视口高度自适应，矮屏收缩保证 8:00–17:30 一屏放下），
  // 渲染时实时读取，读不到时回退 30px
  function roadshowRowHeight() {
    const value = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--roadshow-row-h'));
    return value > 0 ? value : 30;
  }

  function roadshowWeekAnchor() {
    const date = new Date();
    date.setDate(date.getDate() + (state.roadshow.weekOffset || 0) * 7);
    const pad = n => String(n).padStart(2, '0');
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  }

  function renderRoadshowPanel() {
    const rs = state.roadshow;
    const range = rs.weekStart && rs.weekEnd ? `（${formatDate(rs.weekStart)} - ${formatDate(rs.weekEnd)}）` : '';
    return `<section class="panel-card roadshow-panel" id="roadshowPanel">
      <div class="panel-header">
        <div><h2>路演安排</h2><p>一周路演日程${range}，点击空白时间格快速登记</p></div>
        <div class="roadshow-panel-actions">
          <button class="btn btn-ghost btn-small" data-action="roadshow-prev-week" title="上一周">←</button>
          ${isAdminRole() ? '<button class="btn btn-ghost btn-small" data-action="roadshow-export" title="导出当前周路演安排 Excel">导出Excel</button>' : ''}
          <button class="btn btn-ghost btn-small" data-action="roadshow-this-week">本周</button>
          <button class="btn btn-ghost btn-small" data-action="roadshow-next-week" title="下一周">→</button>
          <button class="btn btn-primary btn-small" data-action="roadshow-add"><svg viewBox="0 0 24 24" fill="none" width="14" height="14"><path d="M12 5v14M5 12h14" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>新增路演</button>
        </div>
      </div>
      <div id="roadshowPanelBody"><div class="detail-loading">正在加载路演安排…</div></div>
    </section>`;
  }

  async function loadRoadshowSchedule() {
    const body = document.getElementById('roadshowPanelBody');
    if (!body) return;
    try {
      const data = await API.roadshowSchedule(roadshowWeekAnchor());
      state.roadshow.items = data.items || [];
      state.roadshow.weekStart = data.weekStart || '';
      state.roadshow.weekEnd = data.weekEnd || '';
      const desc = document.querySelector('#roadshowPanel .panel-header p');
      if (desc) desc.textContent = `一周路演日程（${formatDate(state.roadshow.weekStart)} - ${formatDate(state.roadshow.weekEnd)}），点击空白时间格快速登记`;
      body.innerHTML = roadshowCalendarHTML(state.roadshow.items);
      bindRoadshowQuickAdd(body);
    } catch (error) {
      body.innerHTML = `<div class="detail-loading">路演安排加载失败：${escapeHTML(error.message || '请稍后重试')}</div>`;
    }
  }

  // 行政导出当前周路演安排 Excel：取 Blob 后触发浏览器下载，失败时提示服务端错误信息
  async function downloadRoadshowExcel() {
    try {
      const res = await API.roadshowExport(roadshowWeekAnchor());
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error || `导出失败 (${res.status})`);
      }
      const blob = await res.blob();
      const disposition = res.headers.get('content-disposition') || '';
      const match = disposition.match(/filename\*?=(?:UTF-8'')?"?([^";]+)/i);
      const fileName = match ? decodeURIComponent(match[1]) : `路演安排_${state.roadshow.weekStart || ''}.xlsx`;
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = fileName;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (error) {
      notify(error.message || '导出失败', 'error');
    }
  }

  function roadshowLocalDateKey(date) {
    const pad = n => String(n).padStart(2, '0');
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  }

  function roadshowCalendarHTML(items) {
    const pad = n => String(n).padStart(2, '0');
    const rowH = roadshowRowHeight();
    const weekStart = new Date(`${state.roadshow.weekStart}T00:00:00`);
    const days = [];
    for (let i = 0; i < ROADSHOW_DAY_NAMES.length; i++) {
      const day = new Date(weekStart);
      day.setDate(day.getDate() + i);
      days.push(day);
    }
    const todayKey = roadshowLocalDateKey(new Date());
    // 周一~周五分桶；落在周末的路演不进网格，在下方列出
    const byDay = {};
    days.forEach(day => { byDay[roadshowLocalDateKey(day)] = []; });
    const weekendItems = [];
    (items || []).forEach(item => {
      const key = String(item.event_time || '').slice(0, 10);
      if (Object.prototype.hasOwnProperty.call(byDay, key)) byDay[key].push(item);
      else weekendItems.push(item);
    });
    // 各天布局与最晚结束时间（无结束时间按默认 1 小时，超出 20:00 截断到网格）
    const layouts = days.map(day => roadshowAssignTracks(byDay[roadshowLocalDateKey(day)]));
    const dayTracks = layouts.map(layout => layout.tracks);
    let latestEnd = 0;
    layouts.forEach(layout => layout.blocks.forEach(block => {
      latestEnd = Math.max(latestEnd, Math.min(block.end, ROADSHOW_CAL_END));
    }));
    // 没有晚于 17:00 结束的路演时隐去其后时段：网格到 17:00 收尾，模块更简洁、无纵向滚动条
    const calEnd = Math.min(ROADSHOW_CAL_END, Math.max(17 * 60, Math.ceil(latestEnd / ROADSHOW_SLOT_MIN) * ROADSHOW_SLOT_MIN));
    // 左侧时间轴（每小时一个标签，每格 30 分钟；收尾不足一小时时标签只占实际高度）
    let timesHTML = '';
    for (let m = ROADSHOW_CAL_START; m < calEnd; m += 60) {
      const span = Math.min(60, calEnd - m);
      timesHTML += `<div class="roadshow-cal-slot" style="height:${rowH * span / ROADSHOW_SLOT_MIN}px">${pad(m / 60)}:00</div>`;
    }
    const slots = (calEnd - ROADSHOW_CAL_START) / ROADSHOW_SLOT_MIN;
    const gridHeight = slots * rowH;
    const headCells = days.map((day, i) => {
      const key = roadshowLocalDateKey(day);
      return `<div class="roadshow-cal-day${key === todayKey ? ' today' : ''}"><b>周${ROADSHOW_DAY_NAMES[i]}</b><span>${pad(day.getMonth() + 1)}.${pad(day.getDate())}</span></div>`;
    }).join('');
    // 各天实际并行栏数：加宽时每天按"栏数×200px"给足固定列宽（无并排的天至少 200px，
    // 屏幕有富余时拉伸占满），日历总宽随内容动态增长、超出屏幕由横向滚动承接，
    // 不因某天并排多而挤压其他星期列
    const wide = state.roadshow.wide;
    const needsWide = dayTracks.some(t => t > 1);
    const wideCols = dayTracks.map(t => (t > 1 ? `${t * 200}px` : 'minmax(200px,1fr)')).join(' ');
    const widenBtn = `<button type="button" class="roadshow-cal-widen-btn" data-action="roadshow-toggle-wide" ${needsWide ? '' : 'disabled'} title="${needsWide ? '按并排路演数量加宽日历，超出屏幕宽度时可横向滚动查看' : '本周没有并排路演，无需加宽'}">${wide ? '还原' : '加宽'}</button>`;
    const columnsHTML = days.map((day, i) => `<div class="roadshow-cal-grid" data-date="${roadshowLocalDateKey(day)}" style="height:${gridHeight}px">${roadshowEventBlocksHTML(layouts[i].blocks, calEnd)}</div>`).join('');
    const weekendNote = weekendItems.length
      ? `<div class="roadshow-weekend-note">周末另有 ${weekendItems.length} 场路演：${weekendItems.map(item => `${escapeHTML(formatDateTime(item.event_time))} ${escapeHTML(item.presenter || '')}《${escapeHTML(item.topic || '')}》`).join('；')}</div>`
      : '';
    // 单一滚动容器：表头行 sticky top、左侧时间列（含左上角）sticky left，
    // 横向拖动时时间列始终冻结在左侧，纵向滚动时表头固定在顶部
    const calMinWidth = wide ? Math.max(660, 76 + dayTracks.reduce((sum, t) => sum + t * 200, 0)) : 660;
    const innerStyle = wide
      ? `grid-template-columns:56px ${wideCols};min-width:${calMinWidth}px`
      : `min-width:660px`;
    return `<div class="roadshow-cal">
      <div class="roadshow-cal-scroller" style="max-height:calc(var(--roadshow-row-h) * ${Math.min(slots, ROADSHOW_VIEW_ROWS)} + 56px)">
        <div class="roadshow-cal-inner" style="${innerStyle}">
          <div class="roadshow-cal-corner"><span>时间</span>${widenBtn}</div>
          ${headCells}
          <div class="roadshow-cal-times">${timesHTML}</div>
          ${columnsHTML}
        </div>
      </div>
      <div class="roadshow-cal-foot">提示：点击空白时间格可快速登记该时段的路演；色块左侧蓝=线上、绿=线下、紫=线上+线下。</div>
      ${weekendNote}
    </div>`;
  }

  function roadshowMinutes(value) {
    const m = String(value || '').match(/T(\d{1,2}):(\d{2})/);
    if (!m) return ROADSHOW_CAL_START;
    return Math.min(Math.max(Number(m[1]) * 60 + Number(m[2]), 0), 24 * 60);
  }

  function roadshowTimeLabel(item) {
    const start = String(item.event_time || '').slice(11, 16);
    const end = item.end_time ? String(item.end_time).slice(11, 16) : '';
    return end ? `${start}-${end}` : start;
  }

  // 时间区间与重叠轨道分配：返回 { blocks, tracks }
  // tracks = 当天最大并行栏数（无路演记 1），供色块渲染与"加宽"列宽计算共用
  function roadshowAssignTracks(dayItems) {
    const blocks = (dayItems || []).map(item => {
      const start = roadshowMinutes(item.event_time);
      const end = item.end_time ? roadshowMinutes(item.end_time) : start + ROADSHOW_DEFAULT_DURATION;
      return { item, start, end: Math.max(end, start + ROADSHOW_SLOT_MIN) };
    }).sort((a, b) => a.start - b.start || b.end - a.end);
    // 按时间重叠聚簇：只有相互重叠的路演之间并排分栏（簇内算轨道数），
    // 不重叠的时段（含首尾相接）各自占满整行宽度，不因当天别处出现并行而被压窄
    const clusters = [];
    blocks.forEach(block => {
      const current = clusters[clusters.length - 1];
      if (current && block.start < current.end) {
        current.blocks.push(block);
        current.end = Math.max(current.end, block.end);
      } else {
        clusters.push({ blocks: [block], end: block.end });
      }
    });
    let tracks = 1;
    clusters.forEach(cluster => {
      const trackEnds = [];
      cluster.blocks.forEach(block => {
        let track = trackEnds.findIndex(end => end <= block.start);
        if (track === -1) { trackEnds.push(block.end); track = trackEnds.length - 1; }
        else trackEnds[track] = block.end;
        block.track = track;
      });
      // 轨道数须在簇内全部分配完后统一回写：先到的事件分配时轨道数可能尚未增长
      const clusterTracks = trackEnds.length;
      cluster.blocks.forEach(block => { block.tracks = clusterTracks; });
      tracks = Math.max(tracks, clusterTracks);
    });
    return { blocks, tracks };
  }

  function roadshowEventBlocksHTML(blocks, calEnd) {
    if (!blocks || !blocks.length) return '';
    const rowH = roadshowRowHeight();
    return blocks.map(block => {
      const top = ((Math.max(block.start, ROADSHOW_CAL_START) - ROADSHOW_CAL_START) / ROADSHOW_SLOT_MIN) * rowH;
      const bottom = ((Math.min(block.end, calEnd) - ROADSHOW_CAL_START) / ROADSHOW_SLOT_MIN) * rowH;
      const height = Math.max(bottom - top - 4, rowH - 8);
      const width = 100 / block.tracks;
      const item = block.item;
      const compact = height < 50;
      const meta = item.format === 'online'
        ? `线上 · ${item.tencent_meeting_id || ''}`
        : item.format === 'offline' ? `线下 · ${item.meeting_room || ''}` : '线上+线下';
      const institutionPrefix = item.institution ? `${item.institution} · ` : '';
      // 主约人以括号补充在路演人后；悬停提示含主约人，避免窄色块省略号截断后看不到
      const organizerSuffix = item.organizer ? `（主约:${escapeHTML(item.organizer)}）` : '';
      const tip = [item.topic || '未填主题', `${roadshowTimeLabel(item)} · ${item.presenter || ''}`,
        ...(item.organizer ? [`主约：${item.organizer}`] : []), institutionPrefix + meta].filter(Boolean).join('\n');
      // 已归档路演报告：右上角低调小标记（浅色小点），避免"督促上传"的观感
      const archivedMark = item.reportId ? '<span class="roadshow-archived-mark" title="已归档路演报告"></span>' : '';
      return `<button class="roadshow-event fmt-${escapeHTML(item.format)}${item.reportId ? ' archived' : ''}" style="top:${top}px;height:${height}px;left:calc(${(block.track * width).toFixed(3)}% + 2px);width:calc(${width.toFixed(3)}% - 4px)" data-action="roadshow-detail" data-id="${escapeHTML(item.id)}" title="${escapeHTML(tip)}">
        ${archivedMark}
        <strong>${escapeHTML(item.topic || '未填主题')}</strong>
        <span>${escapeHTML(roadshowTimeLabel(item))} · ${escapeHTML(item.presenter || '')}${organizerSuffix}</span>
        ${compact ? '' : `<em>${escapeHTML(institutionPrefix + meta)}</em>`}
      </button>`;
    }).join('');
  }

  // 点击空白时间格：按所在日期与时段预填新增表单
  function bindRoadshowQuickAdd(container) {
    const pad = n => String(n).padStart(2, '0');
    container.querySelectorAll('.roadshow-cal-grid').forEach(grid => {
      grid.addEventListener('click', event => {
        if (event.target.closest('.roadshow-event')) return;
        const rect = grid.getBoundingClientRect();
        const slot = Math.max(0, Math.floor((event.clientY - rect.top) / roadshowRowHeight()));
        const minutes = ROADSHOW_CAL_START + slot * ROADSHOW_SLOT_MIN;
        showRoadshowFormModal({ date: grid.dataset.date, time: `${pad(Math.floor(minutes / 60))}:${pad(minutes % 60)}` });
      });
    });
  }

  // 当前正在编辑的路演安排（null 表示新增）；提交成功后复位
  let roadshowEditing = null;

  function showRoadshowFormModal(prefill = {}) {
    const editing = prefill.item || null;
    roadshowEditing = editing;
    // 编辑时预填现有值；点击空白格快速登记时预填日期时段
    const presetTime = prefill.date && prefill.time
      ? `${prefill.date}T${prefill.time}`
      : (editing ? String(editing.event_time || '').slice(0, 16) : '');
    const presetEndTime = editing && editing.end_time ? String(editing.end_time).slice(11, 16) : '';
    // 主约人默认为账户本人；行政可从团队成员中选择（该字段不做 AI 识别）；
    // 编辑他人登记的安排时保留原主约人，不强制改为操作人
    const organizerValue = editing ? (editing.organizer || state.currentUser.name) : state.currentUser.name;
    const organizerField = isAdminRole()
      ? `<div class="form-field"><label for="roadshowOrganizer">主约人 <em>*</em></label><select id="roadshowOrganizer" name="organizer">${state.reportAuthors.map(author => `<option value="${escapeHTML(author.name)}" ${author.name === organizerValue ? 'selected' : ''}>${escapeHTML(author.name)}${author.org ? ' · ' + escapeHTML(author.org) : ''}</option>`).join('')}</select></div>`
      : `<div class="form-field"><label>主约人</label><div class="upload-scope-lock"><strong>${escapeHTML(organizerValue)}</strong><span>默认为本人</span></div></div>`;
    openModal(`<section class="modal-card">
      ${modalHeader('路演安排', editing ? '修改路演' : '新增路演')}
      <form id="roadshowForm">
        <div class="modal-body">
          <div class="roadshow-ai-box">
            <label for="roadshowAiText">粘贴文本自动识别 <span>支持群通知等路演文案，识别后请人工确认再保存</span></label>
            <textarea id="roadshowAiText" rows="4" maxlength="2000" placeholder="例如：&#10;🔥策略陈果 路演ing！&#10;🌟主题：四季度市场风格展望&#10;⏰时间：9月25日13:30&#10;📍地点：9层一会&#10;#腾讯会议：659-689-968"></textarea>
            <button type="button" class="btn btn-secondary btn-small" id="roadshowAiBtn" data-action="roadshow-ai-parse">
              <svg viewBox="0 0 24 24" fill="none" width="14" height="14"><path d="M12 2l2.4 6.6L21 11l-6.6 2.4L12 20l-2.4-6.6L3 11l6.6-2.4L12 2z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>
              自动识别
            </button>
          </div>
          <div class="form-grid two">
            <div class="form-field"><label for="roadshowTime">开始时间 <em>*</em></label><input id="roadshowTime" name="eventTime" type="datetime-local" value="${escapeHTML(presetTime)}" required></div>
            <div class="form-field"><label for="roadshowEndTime">结束时间 <span>选填，默认 1 小时</span></label><input id="roadshowEndTime" name="endTime" type="time" value="${escapeHTML(presetEndTime)}"></div>
            <div class="form-field"><label for="roadshowFormat">形式 <em>*</em></label><select id="roadshowFormat" name="format" required>
              <option value="online" ${editing && editing.format === 'online' ? 'selected' : ''}>线上（腾讯会议）</option>
              <option value="offline" ${editing && editing.format === 'offline' ? 'selected' : ''}>线下（会议室）</option>
              <option value="hybrid" ${editing && editing.format === 'hybrid' ? 'selected' : ''}>线上+线下</option>
            </select></div>
            <div class="form-field"><label for="roadshowInstitution">路演机构 <span>选填</span></label><input id="roadshowInstitution" name="institution" maxlength="80" autocomplete="off" value="${escapeHTML(editing ? editing.institution || '' : '')}" placeholder="例如：兴业证券"></div>
            ${organizerField}
            <div class="form-field" id="roadshowTencentField"><label for="roadshowTencent">腾讯会议号 <em>*</em></label><input id="roadshowTencent" name="tencentMeetingId" maxlength="40" autocomplete="off" value="${escapeHTML(editing ? editing.tencent_meeting_id || '' : '')}" placeholder="例如：659-689-968"></div>
            <div class="form-field" id="roadshowRoomField" hidden><label for="roadshowRoom">会议室/地点 <em>*</em></label><input id="roadshowRoom" name="meetingRoom" maxlength="60" autocomplete="off" value="${escapeHTML(editing ? editing.meeting_room || '' : '')}" placeholder="例如：9层一会"></div>
            <div class="form-field"><label for="roadshowPresenter">路演人 <em>*</em></label><input id="roadshowPresenter" name="presenter" maxlength="60" autocomplete="off" required value="${escapeHTML(editing ? editing.presenter || '' : '')}" placeholder="例如：陈果"></div>
            <div class="form-field full"><label for="roadshowTopic">主题 <em>*</em></label><input id="roadshowTopic" name="topic" maxlength="200" autocomplete="off" required value="${escapeHTML(editing ? editing.topic || '' : '')}" placeholder="例如：四季度市场风格展望"></div>
          </div>
          <div class="score-rule-note"><span>提示</span><p>行政账号代他人登记时，创建人仍为行政本人；路演安排所有人可见${editing ? '；修改不影响创建人记录' : ''}。</p></div>
        </div>
        <div class="modal-footer"><button type="button" class="btn btn-ghost" data-close="modal">取消</button><button type="submit" class="btn btn-primary" id="roadshowSubmit">${editing ? '保存修改' : '保存路演'}</button></div>
      </form>
    </section>`);
    const form = document.getElementById('roadshowForm');
    const formatSelect = document.getElementById('roadshowFormat');
    const syncFormatFields = () => {
      const fmt = formatSelect.value;
      const tencentField = document.getElementById('roadshowTencentField');
      const roomField = document.getElementById('roadshowRoomField');
      tencentField.hidden = fmt === 'offline';
      document.getElementById('roadshowTencent').required = fmt !== 'offline';
      roomField.hidden = fmt === 'online';
      document.getElementById('roadshowRoom').required = fmt !== 'online';
    };
    formatSelect.addEventListener('change', syncFormatFields);
    syncFormatFields();
    form.addEventListener('submit', submitRoadshow);
  }

  async function submitRoadshow(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const startTime = String(data.get('eventTime') || '');
    const endTimeRaw = String(data.get('endTime') || '').trim();
    const editing = roadshowEditing;
    const payload = {
      eventTime: startTime,
      endTime: endTimeRaw ? `${startTime.slice(0, 10)}T${endTimeRaw}` : '',
      format: String(data.get('format') || ''),
      institution: String(data.get('institution') || '').trim(),
      // 主约人：行政从下拉选择；其他角色新增默认本人、编辑保留原值
      organizer: isAdminRole() ? String(data.get('organizer') || '').trim()
        : (editing ? (editing.organizer || state.currentUser.name) : state.currentUser.name),
      tencentMeetingId: String(data.get('tencentMeetingId') || '').trim(),
      meetingRoom: String(data.get('meetingRoom') || '').trim(),
      presenter: String(data.get('presenter') || '').trim(),
      topic: String(data.get('topic') || '').trim(),
    };
    const submit = document.getElementById('roadshowSubmit');
    submit.disabled = true;
    submit.textContent = '保存中…';
    try {
      if (editing) {
        await API.roadshowUpdate(editing.id, payload);
        notify('路演安排已更新');
      } else {
        await API.roadshowAdd(payload);
        notify('路演安排已保存');
      }
      roadshowEditing = null;
      closeModal();
      loadRoadshowSchedule();
    } catch (error) {
      submit.disabled = false;
      submit.textContent = editing ? '保存修改' : '保存路演';
      notify(error.message || '保存失败', 'error');
    }
  }

  async function handleRoadshowAiParse() {
    const textarea = document.getElementById('roadshowAiText');
    const btn = document.getElementById('roadshowAiBtn');
    if (!textarea || !btn) return;
    const text = textarea.value.trim();
    if (!text) return notify('请先粘贴路演通知文本', 'error');
    btn.disabled = true;
    btn.textContent = '识别中…';
    try {
      // 携带当前显示周的周一：只有"周几"的日期按这一周推算
      const parsed = await API.roadshowAiParse(text, state.roadshow.weekStart || '');
      const timeInput = document.getElementById('roadshowTime');
      const endTimeInput = document.getElementById('roadshowEndTime');
      const formatSelect = document.getElementById('roadshowFormat');
      if (parsed.eventTime) timeInput.value = parsed.eventTime.slice(0, 16);
      if (parsed.format) { formatSelect.value = parsed.format; formatSelect.dispatchEvent(new Event('change')); }
      if (parsed.tencentMeetingId) document.getElementById('roadshowTencent').value = parsed.tencentMeetingId;
      if (parsed.meetingRoom) document.getElementById('roadshowRoom').value = parsed.meetingRoom;
      if (parsed.institution) document.getElementById('roadshowInstitution').value = parsed.institution;
      if (parsed.presenter) document.getElementById('roadshowPresenter').value = parsed.presenter;
      if (parsed.topic) document.getElementById('roadshowTopic').value = parsed.topic;
      // 路演通知一般不含结束时间：默认按 1 小时预填，可自行修改
      const start = timeInput.value;
      if (start && !endTimeInput.value) {
        const [, hhmm] = start.split('T');
        const [hh, mm] = hhmm.split(':').map(Number);
        const endMinutes = hh * 60 + mm + ROADSHOW_DEFAULT_DURATION;
        if (endMinutes < 24 * 60) {
          const pad = n => String(n).padStart(2, '0');
          endTimeInput.value = `${pad(Math.floor(endMinutes / 60))}:${pad(endMinutes % 60)}`;
        }
      }
      notify('识别完成，请核对信息（未识别出的字段显示为空）');
    } catch (error) {
      notify(error.message || '自动识别失败，请手动填写', 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = '自动识别';
    }
  }

  function showRoadshowDetailModal(itemId) {
    const item = (state.roadshow.items || []).find(row => row.id === itemId);
    if (!item) return notify('未找到该路演安排', 'error');
    // 创建人本人和行政可修改/删除；匹配报告额外对报告上传人/作者开放（后端校验）
    const canManage = isAdminRole() || (state.currentUser && item.created_by === state.currentUser.id);
    const endTime = item.end_time ? ` - ${escapeHTML(String(item.end_time).slice(11, 16))}` : '';
    // 一场路演可关联多篇报告：全部列出（新上传的在前），点击打开报告详情
    const linkedReports = (item.reportIds || [])
      .map(id => getReport(id))
      .filter(Boolean)
      .reverse();
    const reportListHTML = linkedReports.length ? `
        <div class="detail-section">
          <span class="detail-label">已归档报告（${linkedReports.length}）</span>
          <ul class="roadshow-report-list">
            ${linkedReports.map(report => `<li>
              <button class="roadshow-report-item" data-action="view-report" data-id="${escapeHTML(report.id)}" title="打开报告详情">
                <strong>${escapeHTML(report.title)}</strong>
                <span>${escapeHTML(report.reportDate || '')} · ${escapeHTML(report.sourceAuthor || report.author || '')}</span>
              </button>
            </li>`).join('')}
          </ul>
        </div>` : '';
    openModal(`<section class="modal-card">
      ${modalHeader('路演详情', item.topic || '路演安排')}
      <div class="modal-body">
        <div class="detail-meta-grid roadshow-detail-grid">
          <div><span>时间</span><strong>${escapeHTML(formatDateTime(item.event_time))}${endTime}</strong></div>
          <div><span>形式</span><strong>${escapeHTML(item.formatLabel || '')}</strong></div>
          <div><span>路演机构</span><strong>${escapeHTML(item.institution || '—')}</strong></div>
          <div><span>路演人</span><strong>${escapeHTML(item.presenter || '—')}</strong></div>
          <div><span>主约人</span><strong>${escapeHTML(item.organizer || '—')}</strong></div>
          <div><span>腾讯会议号</span><strong>${escapeHTML(item.tencent_meeting_id || '—')}</strong></div>
          <div><span>会议室/地点</span><strong>${escapeHTML(item.meeting_room || '—')}</strong></div>
          <div><span>创建人</span><strong>${escapeHTML(item.created_by_name || '—')}</strong></div>
          <div><span>路演报告</span><strong>${linkedReports.length ? `已归档 ${linkedReports.length} 篇` : '未上传'}</strong></div>
        </div>
        <div class="detail-section"><span class="detail-label">主题</span><p class="roadshow-topic-view">${escapeHTML(item.topic || '')}</p></div>
        ${reportListHTML}
      </div>
      <div class="modal-footer">
        <button class="btn btn-ghost" data-close="modal">关闭</button>
        <button class="btn btn-secondary" data-action="roadshow-upload" data-id="${escapeHTML(item.id)}" title="预填该路演信息上传路演报告">上传报告</button>
        <button class="btn btn-secondary" data-action="match-report" data-id="${escapeHTML(item.id)}" title="手工把已上传的路演报告关联到该安排">匹配报告</button>
        ${canManage ? `<button class="btn btn-secondary" data-action="roadshow-edit" data-id="${escapeHTML(item.id)}"><svg viewBox="0 0 24 24" fill="none" width="14" height="14"><path d="M4 20h4L18.5 9.5a2.12 2.12 0 0 0-3-3L5 17v3z" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/></svg>修改</button>` : ''}
        ${canManage ? `<button class="btn btn-danger danger-action" data-action="roadshow-delete" data-id="${escapeHTML(item.id)}">删除</button>` : ''}
      </div>
    </section>`);
  }

  function showRoadshowDeleteConfirm(itemId) {
    const item = (state.roadshow.items || []).find(row => row.id === itemId);
    if (!item) return notify('未找到该路演安排', 'error');
    const deletingOthers = state.currentUser && item.created_by !== state.currentUser.id;
    openModal(`<section class="modal-card">
      ${modalHeader('删除路演安排', item.topic || '路演')}
      <div class="modal-body">
        <div class="detail-permission"><svg viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.8"/><path d="M12 8v4M12 16h.01" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg><div><strong style="font-size:18px">确认删除这条路演安排吗？</strong><p>${escapeHTML(formatDateTime(item.event_time))} · ${escapeHTML(item.presenter || '')} · ${escapeHTML(item.topic || '')}</p>${deletingOthers ? `<p><b style="color:var(--warning)">注意：您正在以行政身份删除 ${escapeHTML(item.created_by_name || '他人')} 创建的安排。</b></p>` : ''}</div></div>
      </div>
      <div class="modal-footer">
        <button type="button" class="btn btn-ghost" data-close="modal">取消</button>
        <button type="button" class="btn btn-danger" data-action="confirm-roadshow-delete" data-id="${escapeHTML(item.id)}">删除</button>
      </div>
    </section>`);
  }

  async function deleteRoadshowItem(itemId) {
    try {
      await API.roadshowDelete(itemId);
      closeModal();
      notify('路演安排已删除');
      loadRoadshowSchedule();
    } catch (error) {
      notify(error.message || '删除失败', 'error');
    }
  }

  // 从路演安排一键上传报告：锁定路演报告类型并自动预填信息
  // 报告作者=路演人、报告机构=路演机构，行政可按路演人预选署名作者
  function openRoadshowUpload(itemId) {
    const item = (state.roadshow.items || []).find(row => row.id === itemId);
    if (!item) return notify('未找到该路演安排', 'error');
    showUploadModal({
      reportType: 'roadshow',
      title: item.topic || '',
      reportDate: String(item.event_time || '').slice(0, 10),
      roadshowScheduleId: item.id,
      authorName: item.presenter || '',
      sourceAuthor: item.presenter || '',
      sourceInstitution: item.institution || '',
    });
  }

  // ----------------------------------------------------------------------- //
  // 路演报告 ↔ 路演安排 手工匹配（自动匹配不强制，识别不了时可手工关联）
  // ----------------------------------------------------------------------- //
  function roadshowScheduleLabel(report) {
    const schedule = report.roadshowSchedule;
    if (!schedule) return '未匹配';
    const when = String(schedule.eventTime || '').slice(5, 16).replace('T', ' ');
    return `${when} ${schedule.presenter || ''}《${schedule.topic || ''}》`;
  }

  // 手工匹配权限：行政，或相关本人（报告署名作者/实际上传人）
  function canMatchRoadshow(report) {
    if (!state.currentUser) return false;
    if (isAdminRole()) return true;
    return report.authorId === state.currentUser.id || report.uploadedById === state.currentUser.id;
  }

  // 上传路演报告自动匹配成功后的结果卡片：展示匹配到的路演，可一键调整
  function showAutoMatchResultModal(report) {
    const schedule = report.roadshowSchedule || {};
    const when = String(schedule.eventTime || '').slice(5, 16).replace('T', ' ');
    const info = `${when} ${schedule.presenter || ''}《${schedule.topic || ''}》${schedule.institution ? `（${schedule.institution}）` : ''}`;
    openModal(`<section class="modal-card">
      ${modalHeader('自动匹配结果', report.title)}
      <div class="modal-body">
        <div class="roadshow-match-result">
          <div class="roadshow-match-result-icon">✓</div>
          <div>
            <strong>已自动匹配到路演安排</strong>
            <p>${escapeHTML(info)}</p>
            <span>${report.roadshowMatchedBy === 'rule' ? '规则匹配' : '大模型匹配'} · 匹配不强制，可随时调整</span>
          </div>
        </div>
      </div>
      <div class="modal-footer">
        <button type="button" class="btn btn-ghost" data-close="modal">完成</button>
        <button type="button" class="btn btn-primary" data-action="match-schedule" data-id="${escapeHTML(report.id)}">调整匹配</button>
      </div>
    </section>`);
  }

  // 报告详情侧：为路演报告选择/更换/取消关联的路演安排
  async function showScheduleMatchModal(reportId) {
    const report = getReport(reportId);
    if (!report) return notify('未找到该报告', 'error');
    if (!canMatchRoadshow(report)) return notify('仅行政或相关本人可匹配路演安排', 'error');
    const currentId = (report.roadshowSchedule && report.roadshowSchedule.id) || '';
    openModal(`<section class="modal-card">
      ${modalHeader('匹配路演安排', report.title)}
      <form id="scheduleMatchForm">
        <div class="modal-body">
          <div class="form-field">
            <label for="scheduleMatchSelect">选择路演安排 <span>按报告日期前后各 10 天列出</span></label>
            <select id="scheduleMatchSelect" name="scheduleId"><option value="">正在加载候选…</option></select>
          </div>
          <div class="score-rule-note"><span>提示</span><p>匹配不强制：列表选"不关联"可留空稍后再配；自动匹配的结果也可在此调整。</p></div>
        </div>
        <div class="modal-footer">
          <button type="button" class="btn btn-ghost" data-close="modal">取消</button>
          ${currentId ? `<button type="button" class="btn btn-danger danger-action" data-action="unlink-schedule" data-id="${escapeHTML(report.id)}">取消关联</button>` : ''}
          <button type="submit" class="btn btn-primary" id="scheduleMatchSubmit">保存匹配</button>
        </div>
      </form>
    </section>`);
    try {
      const data = await API.roadshowOptions(report.reportDate || '');
      const select = document.getElementById('scheduleMatchSelect');
      const options = data.options || [];
      select.innerHTML = `<option value="">不关联任何路演安排</option>` + options.map(opt =>
        `<option value="${escapeHTML(opt.id)}" ${opt.id === currentId ? 'selected' : ''}>${escapeHTML(opt.label)}</option>`).join('');
    } catch (error) {
      notify(error.message || '候选路演安排加载失败', 'error');
    }
    document.getElementById('scheduleMatchForm').addEventListener('submit', event => submitScheduleMatch(event, report.id));
  }

  async function submitScheduleMatch(event, reportId) {
    event.preventDefault();
    const select = document.getElementById('scheduleMatchSelect');
    const submit = document.getElementById('scheduleMatchSubmit');
    submit.disabled = true;
    submit.textContent = '保存中…';
    try {
      await API.roadshowMatch({ reportId, scheduleId: select.value });
      notify(select.value ? '路演关联已保存' : '已取消路演关联');
      closeModal();
      await refreshData();
      loadRoadshowSchedule();
    } catch (error) {
      submit.disabled = false;
      submit.textContent = '保存匹配';
      notify(error.message || '保存失败', 'error');
    }
  }

  async function unlinkSchedule(reportId) {
    try {
      await API.roadshowMatch({ reportId, scheduleId: '' });
      notify('已取消路演关联');
      closeModal();
      await refreshData();
      loadRoadshowSchedule();
    } catch (error) {
      notify(error.message || '操作失败', 'error');
    }
  }

  // 路演详情侧：把已上传的路演报告关联到该安排（报告日期前后各 10 天内的路演报告）
  // 已关联其他安排的报告默认隐藏，点击开关展开；另有"自动尝试匹配"按钮触发规则+大模型匹配
  function showReportMatchModal(scheduleId) {
    const item = (state.roadshow.items || []).find(row => row.id === scheduleId);
    if (!item) return notify('未找到该路演安排', 'error');
    const anchor = String(item.event_time || '').slice(0, 10);
    const inWindow = value => {
      const diff = Math.abs(new Date(value) - new Date(anchor)) / 86400000;
      return !Number.isNaN(diff) && diff <= 10;
    };
    // 候选：日期窗口内的路演报告，外加已关联本安排的报告（便于查看/换绑）
    const candidates = (state.reports || [])
      .filter(report => report.reportType === 'roadshow')
      .filter(report => (report.roadshowSchedule && report.roadshowSchedule.id === item.id) || inWindow(report.reportDate))
      .sort((a, b) => String(b.reportDate || '').localeCompare(String(a.reportDate || '')));
    if (!candidates.length) return notify('该时段前后没有可匹配的路演报告', 'info');
    const currentId = item.reportId || '';
    const linkedHere = new Set(item.reportIds || []);
    // 已关联其他安排的报告默认隐藏（避免误改绑），点开关展开
    const hiddenCandidates = candidates.filter(report =>
      report.roadshowSchedule && report.roadshowSchedule.id && !linkedHere.has(report.id));
    const hiddenIds = new Set(hiddenCandidates.map(report => report.id));
    const optionLabel = report => {
      const flag = linkedHere.has(report.id) ? '（已关联本安排）'
        : hiddenIds.has(report.id) ? '（已关联其他安排）' : '';
      return `${escapeHTML(report.reportDate || '')} 《${escapeHTML(report.title)}》 ${escapeHTML(report.sourceAuthor || '')}${escapeHTML(flag)}`;
    };
    const renderOptions = showHidden => candidates.map(report =>
      `<option value="${escapeHTML(report.id)}" ${hiddenIds.has(report.id) && !showHidden ? 'hidden' : ''} ${report.id === currentId ? 'selected' : ''}>${optionLabel(report)}</option>`).join('');
    const toggleBtn = hiddenIds.size
      ? `<button type="button" class="roadshow-match-toggle" id="toggleLinkedReports">展开已关联其他安排的报告（${hiddenIds.size}）</button>`
      : '';
    openModal(`<section class="modal-card">
      ${modalHeader('匹配路演报告', item.topic || '路演安排')}
      <form id="reportMatchForm" data-schedule-id="${escapeHTML(item.id)}">
        <div class="modal-body">
          <div class="form-field">
            <label for="reportMatchSelect">选择路演报告 <span>按路演日期前后各 10 天列出</span></label>
            <select id="reportMatchSelect" name="reportId" required>${renderOptions(false)}</select>
            ${toggleBtn}
          </div>
          <div class="score-rule-note"><span>提示</span><p>列表默认只显示未关联与已关联本安排的报告；选择报告保存即建立（或改绑）关联，一场路演可关联多篇报告。</p></div>
        </div>
        <div class="modal-footer">
          <button type="button" class="btn btn-ghost" data-close="modal">取消</button>
          <button type="button" class="btn btn-secondary" data-action="auto-match-report" data-id="${escapeHTML(item.id)}" id="autoMatchReportBtn" title="按日期/路演人/机构/主题自动挑选最吻合的未关联报告">自动尝试匹配</button>
          <button type="button" class="btn btn-danger danger-action" data-action="unlink-schedule" data-id="${escapeHTML(currentId)}" id="unlinkReportBtn" ${currentId ? '' : 'hidden'}>取消关联</button>
          <button type="submit" class="btn btn-primary" id="reportMatchSubmit">保存匹配</button>
        </div>
      </form>
    </section>`);

    const select = document.getElementById('reportMatchSelect');
    const toggle = document.getElementById('toggleLinkedReports');
    if (toggle) {
      let expanded = false;
      toggle.addEventListener('click', () => {
        expanded = !expanded;
        const selected = select.value;
        select.innerHTML = renderOptions(expanded);
        select.value = selected;
        toggle.textContent = expanded ? `收起已关联其他安排的报告（${hiddenIds.size}）` : `展开已关联其他安排的报告（${hiddenIds.size}）`;
      });
    }
    // 取消关联按钮跟随选中项：选中的报告已关联本安排时才可取消
    const unlinkBtn = document.getElementById('unlinkReportBtn');
    const syncUnlink = () => {
      unlinkBtn.hidden = !linkedHere.has(select.value);
      unlinkBtn.dataset.id = select.value;
    };
    select.addEventListener('change', syncUnlink);
    syncUnlink();

    document.getElementById('reportMatchForm').addEventListener('submit', async event => {
      event.preventDefault();
      const scheduleId = event.currentTarget.dataset.scheduleId;
      const reportId = select.value;
      const submit = document.getElementById('reportMatchSubmit');
      submit.disabled = true;
      submit.textContent = '保存中…';
      try {
        await API.roadshowMatch({ reportId, scheduleId });
        notify('路演关联已保存');
        closeModal();
        await refreshData();
        loadRoadshowSchedule();
      } catch (error) {
        submit.disabled = false;
        submit.textContent = '保存匹配';
        notify(error.message || '保存失败', 'error');
      }
    });
  }

  // 路演侧自动匹配：规则 + 大模型推荐一份未关联报告，仅在弹窗内展示推荐结果，
  // 由人工"采用"后核对保存，不会直接建立关联
  async function handleAutoMatchReport(scheduleId, button) {
    if (!button) return;
    const original = button.textContent;
    button.disabled = true;
    button.textContent = '匹配中…';
    try {
      const result = await API.roadshowAutoMatch(scheduleId);
      if (result.recommended) showAutoMatchSuggestion(result);
      else notify(result.message || '未找到足够吻合的路演报告，请手工选择', 'info');
    } catch (error) {
      notify(error.message || '自动匹配失败', 'error');
    } finally {
      button.disabled = false;
      button.textContent = original;
    }
  }

  // 在匹配报告弹窗内插入推荐结果条：采用=选入下拉（仍需点保存匹配确认），忽略=关闭
  function showAutoMatchSuggestion(result) {
    document.getElementById('autoMatchSuggestion')?.remove();
    const report = result.report || {};
    const box = document.createElement('div');
    box.className = 'roadshow-match-suggestion';
    box.id = 'autoMatchSuggestion';
    box.innerHTML = `
      <div class="roadshow-match-suggestion-info">
        <strong>推荐匹配（${result.method === 'rule' ? '规则' : '大模型'}）</strong>
        <p>《${escapeHTML(report.title || '')}》 ${escapeHTML(report.reportDate || '')} · ${escapeHTML(report.sourceAuthor || '')}</p>
        <span>采用后请核对信息，点击"保存匹配"确认建立关联</span>
      </div>
      <div class="roadshow-match-suggestion-actions">
        <button type="button" class="btn btn-primary btn-small" id="applySuggestionBtn">采用</button>
        <button type="button" class="btn btn-ghost btn-small" id="dismissSuggestionBtn">忽略</button>
      </div>`;
    const select = document.getElementById('reportMatchSelect');
    if (!select) return;
    select.closest('.form-field').insertBefore(box, select.nextSibling);
    box.querySelector('#dismissSuggestionBtn').addEventListener('click', () => box.remove());
    box.querySelector('#applySuggestionBtn').addEventListener('click', () => {
      // 推荐报告未关联其他安排，正常在默认列表中；若不在（理论上不会），先展开隐藏项
      if (!select.querySelector(`option[value="${CSS.escape(report.id)}"]`)) {
        document.getElementById('toggleLinkedReports')?.click();
      }
      select.value = report.id;
      select.dispatchEvent(new Event('change'));
      box.remove();
      notify('已选入推荐报告，请核对后点击"保存匹配"');
    });
  }

  function createKnowledgeConversationId() {
    if (globalThis.crypto && typeof globalThis.crypto.randomUUID === 'function') return globalThis.crypto.randomUUID();
    return `kb-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  }

  function knowledgeConversations() {
    const grouped = new Map();
    (Array.isArray(state.knowledge.history) ? state.knowledge.history : []).forEach((item, idx) => {
      const id = item.conversationId || `legacy-${item.id || idx}`;
      if (!grouped.has(id)) grouped.set(id, { id, title: item.question || '未命名对话', updatedAt: item.createdAt || '', items: [] });
      const conversation = grouped.get(id);
      conversation.items.push(item);
      if (item.createdAt && item.createdAt >= conversation.updatedAt) conversation.updatedAt = item.createdAt;
    });
    return [...grouped.values()].sort((a, b) => String(b.updatedAt).localeCompare(String(a.updatedAt)));
  }

  function renderKnowledgeSearch() {
    const knowledge = state.knowledge;
    const kf = knowledge.filters || {};
    const conversations = knowledgeConversations();
    const isFree = knowledge.questionType !== 'report_retrieval';
    const historyItems = conversations.map(conversation => {
      const time = conversation.updatedAt ? new Date(conversation.updatedAt).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '';
      const latest = conversation.items[conversation.items.length - 1] || {};
      const mode = latest.questionType === 'report_retrieval' ? '找报告' : (latest.thinking ? '自由问答 · 思考' : '自由问答');
      return `<li class="knowledge-history-item${knowledge.activeConversationId === conversation.id ? ' active' : ''}" data-action="focus-history" data-conversation-id="${escapeHTML(conversation.id)}"><p class="knowledge-history-q">${escapeHTML(conversation.title)}</p><span class="knowledge-history-meta"><em>${mode}</em>${time ? `<time>${time}</time>` : ''}</span></li>`;
    }).join('');
    const messages = Array.isArray(knowledge.messages) ? knowledge.messages : [];
    els.viewRoot.innerHTML = `<section class="knowledge-hero"><div><h1>智能搜索</h1><p>自由问答可连续追问，找报告按固定格式整理结果</p></div><div class="knowledge-quota"><strong>${knowledge.remaining}</strong><span>今日剩余次数<br><small>每日最多 ${knowledge.limit} 次</small></span></div></section>
      <section class="knowledge-main">
        <aside class="knowledge-sidebar panel-card">
          <div class="knowledge-new-wrap"><button class="knowledge-new-btn" data-action="new-knowledge-conversation"><span>＋</span> 新对话</button></div>
          <header><div class="knowledge-history-head"><h3>历史对话</h3><span class="knowledge-history-count">${conversations.length}</span></div>${conversations.length ? `<button class="knowledge-clear-btn" data-action="clear-history" title="清空全部历史对话">清理历史</button>` : ''}</header>
          <ul class="knowledge-history-list">${historyItems || '<li class="knowledge-history-empty">暂无历史对话</li>'}</ul>
        </aside>
        <div class="knowledge-shell panel-card">
          <div class="knowledge-filters">${knowledgeFiltersHTML(kf)}</div>
          <div class="knowledge-conversation" id="knowledgeConversation">${messages.length ? messages.map(message => `<article class="knowledge-message ${message.role}"><div>${message.role === 'user' ? '我' : 'AI'}</div><section>${message.role === 'user' && message.questionType ? `<span class="knowledge-message-mode">${message.questionType === 'report_retrieval' ? '找报告' : `自由问答${message.thinking ? ' · 深度思考' : ''}`}</span>` : ''}${message.role === 'assistant' && message.streaming ? `<div class="knowledge-status">${escapeHTML(message.stage || '正在检索知识库…')}</div>` : ''}<div class="knowledge-answer${message.role === 'assistant' && message.streaming ? ' streaming' : ''}">${message.role === 'assistant' ? (message.text ? renderKnowledgeAnswer(message.text) : '') : escapeHTML(message.text).replace(/\n/g, '<br>')}</div>${message.sources?.length ? `<aside><span>本轮引用报告 · ${message.sources.length}篇</span>${message.sources.map(source => `<button data-action="view-report" data-id="${source.id}">《${escapeHTML(source.title)}》${source.author ? ` · ${escapeHTML(source.author)}` : ''}${source.publishedAt ? ` · ${escapeHTML(source.publishedAt)}` : ''}</button>`).join('')}</aside>` : ''}</section></article>`).join('') : `<div class="knowledge-empty"><div class="knowledge-empty-mark">AI</div><strong>${isFree ? '从知识库开始自由问答' : '告诉我你想找哪类报告'}</strong><p>${isFree ? '例如：“信用利差最近有哪些共识？”回答后可继续追问“展开第二点”。' : '例如：“找出近三个月讨论城投利差的报告”。'}</p></div>`}</div>
          <form class="knowledge-form" id="knowledgeForm">
            <textarea id="knowledgeQuestion" maxlength="300" placeholder="${isFree ? '基于知识库提问，可在回答后继续追问…' : '描述主题、时间或报告方向…'}" ${knowledge.remaining ? '' : 'disabled'}>${escapeHTML(knowledge.draft || '')}</textarea>
            <div class="knowledge-composer-toolbar">
              <label class="knowledge-mode-select"><span>回答模式</span><select id="knowledgeModeSelect" aria-label="回答模式"><option value="general_work" ${isFree ? 'selected' : ''}>自由问答</option><option value="report_retrieval" ${isFree ? '' : 'selected'}>找报告 · 按格式输出</option></select></label>
              ${isFree ? `<button type="button" class="knowledge-thinking-compact${knowledge.thinking ? ' active' : ''}" data-action="toggle-knowledge-thinking" aria-pressed="${knowledge.thinking ? 'true' : 'false'}" title="${knowledge.thinking ? '关闭深度思考' : '开启深度思考'}"><span>✦</span> 深度思考</button>` : ''}
              <span class="knowledge-composer-shortcut">Enter 发送 · Shift + Enter 换行</span>
              <button class="btn btn-primary knowledge-send" type="submit" ${knowledge.remaining && knowledge.available !== false ? '' : 'disabled'}>发送</button>
            </div>
          </form>${knowledge.available === false ? '<p class="knowledge-warning">服务端尚未配置大模型 API 密钥，暂时无法发起问答。</p>' : ''}
        </div>
      </section>`;
    document.getElementById('knowledgeForm')?.addEventListener('submit', submitKnowledgeQuestion);
    document.getElementById('knowledgeModeSelect')?.addEventListener('change', event => setKnowledgeQuestionType(event.target.value));
    document.getElementById('knowledgeQuestion')?.addEventListener('input', event => { state.knowledge.draft = event.target.value; });
    document.getElementById('knowledgeQuestion')?.addEventListener('keydown', event => {
      // Enter 直接发送；Shift+Enter 保留换行能力。
      if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        const form = document.getElementById('knowledgeForm');
        if (form && !form.classList.contains('loading')) form.requestSubmit();
      }
    });
    const conversation = document.getElementById('knowledgeConversation');
    if (conversation) conversation.scrollTop = conversation.scrollHeight;
    bindKnowledgeFilters();
  }

  // 人员下拉选项：从已加载的报告列表提取去重后的署名作者。
  function knowledgeAuthorOptions() {
    const names = new Set();
    (state.reports || []).forEach(report => {
      const name = report.reportType === 'external' ? (report.sourceAuthor || report.author) : report.author;
      if (name) names.add(String(name));
    });
    return [...names].sort((a, b) => a.localeCompare(b, 'zh-Hans-CN'));
  }

  // 多选筛选项配置：value 为 '' 的“全部”选项与具体选项互斥
  const KNOWLEDGE_FILTER_LABELS = { reportTypes: '报告来源', categories: '报告种类', themes: '主题类型', authors: '人员' };

  function knowledgeFilterOptions(key) {
    if (key === 'reportTypes') return [['', '全部来源'], ['internal', '内部报告'], ['external', '外部报告'], ['research_visit', '调研报告'], ['roadshow', '路演报告']];
    if (key === 'categories') return [['', '全部种类'], ...Object.entries(categories).map(([value, cat]) => [value, cat.label])];
    if (key === 'themes') return [['', '全部主题'], ...Object.entries(themes).map(([value, theme]) => [value, theme.label])];
    return [['', '全部人员'], ...knowledgeAuthorOptions().map(name => [name, name])];
  }

  function knowledgeFilterSummary(key) {
    const kf = state.knowledge.filters || {};
    const options = knowledgeFilterOptions(key);
    const labelOf = value => (options.find(([v]) => v === value) || [, value])[1];
    const values = kf[key] || [];
    if (!values.length) return options[0][1];
    if (values.length === 1) return labelOf(values[0]);
    return `${labelOf(values[0])} 等${values.length}项`;
  }

  // 筛选栏：时间范围单选（自定义起止日期），来源/种类/主题/人员为多选下拉
  function knowledgeFiltersHTML(kf) {
    const multi = key => {
      const options = knowledgeFilterOptions(key);
      const selected = new Set(kf[key] || []);
      return `<div class="knowledge-multi" data-filter="${key}"><span class="knowledge-multi-label">${KNOWLEDGE_FILTER_LABELS[key] || key}</span><button type="button" class="knowledge-multi-toggle">${escapeHTML(knowledgeFilterSummary(key))}<em>▾</em></button><div class="knowledge-multi-menu" hidden>${options.map(([value, text]) => `<label class="knowledge-multi-option"><input type="checkbox" value="${escapeHTML(value)}" ${(value === '' ? selected.size === 0 : selected.has(value)) ? 'checked' : ''} ${value === '' ? 'data-exclusive="1"' : ''}><span>${escapeHTML(text)}</span></label>`).join('')}</div></div>`;
    };
    return `<label class="knowledge-filter">时间范围
        <select id="knowledgeFilterPeriod"><option value="1m" ${kf.period === '1m' ? 'selected' : ''}>过去一个月</option><option value="3m" ${kf.period === '3m' ? 'selected' : ''}>过去三个月</option><option value="all" ${kf.period === 'all' ? 'selected' : ''}>全部时间</option><option value="custom" ${kf.period === 'custom' ? 'selected' : ''}>自定义</option></select>
      </label>
      <span class="knowledge-filter-dates" id="knowledgeCustomDates" ${kf.period === 'custom' ? '' : 'hidden'}><input type="date" id="knowledgeDateFrom" value="${escapeHTML(kf.dateFrom || '')}" title="开始日期"><span>至</span><input type="date" id="knowledgeDateTo" value="${escapeHTML(kf.dateTo || '')}" title="结束日期"></span>
      ${multi('reportTypes')}${multi('categories')}${multi('themes')}${multi('authors')}`;
  }

  // 筛选条件只影响下一次提问，变更时写入 state 以便视图重渲染后保持选中。
  let knowledgeMenuCloserBound = false;
  function bindKnowledgeFilters() {
    const filters = state.knowledge.filters || (state.knowledge.filters = {});
    const period = document.getElementById('knowledgeFilterPeriod');
    const dates = document.getElementById('knowledgeCustomDates');
    const dateFrom = document.getElementById('knowledgeDateFrom');
    const dateTo = document.getElementById('knowledgeDateTo');
    period?.addEventListener('change', () => {
      filters.period = period.value;
      if (dates) dates.hidden = period.value !== 'custom';
    });
    dateFrom?.addEventListener('change', () => { filters.dateFrom = dateFrom.value; });
    dateTo?.addEventListener('change', () => { filters.dateTo = dateTo.value; });
    document.querySelectorAll('.knowledge-multi').forEach(container => {
      const key = container.dataset.filter;
      const menu = container.querySelector('.knowledge-multi-menu');
      const toggle = container.querySelector('.knowledge-multi-toggle');
      toggle?.addEventListener('click', () => {
        const willOpen = menu.hidden;
        document.querySelectorAll('.knowledge-multi-menu').forEach(item => { item.hidden = true; });
        menu.hidden = !willOpen;
      });
      container.querySelectorAll('input[type=checkbox]').forEach(box => {
        box.addEventListener('change', () => {
          const boxes = [...container.querySelectorAll('input[type=checkbox]')];
          // “全部”与具体选项互斥：勾选一方即取消另一方
          if (box.checked) {
            boxes.forEach(item => {
              if (box.dataset.exclusive !== item.dataset.exclusive) item.checked = false;
            });
          }
          filters[key] = boxes.filter(item => item.checked && !item.dataset.exclusive).map(item => item.value);
          refreshKnowledgeFilterSummaries();
        });
      });
    });
    if (!knowledgeMenuCloserBound) {
      knowledgeMenuCloserBound = true;
      // 点击组件外部时收起所有多选下拉
      document.addEventListener('click', event => {
        if (event.target.closest('.knowledge-multi')) return;
        document.querySelectorAll('.knowledge-multi-menu').forEach(menu => { menu.hidden = true; });
      });
    }
  }

  function refreshKnowledgeFilterSummaries() {
    document.querySelectorAll('.knowledge-multi').forEach(container => {
      const toggle = container.querySelector('.knowledge-multi-toggle');
      if (toggle) toggle.innerHTML = `${escapeHTML(knowledgeFilterSummary(container.dataset.filter))}<em>▾</em>`;
    });
  }

  function renderKnowledgeInline(value) {
    let html = escapeHTML(value || '');
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
    html = html.replace(/《([^》]+)》/g, '<span class="knowledge-citation">《$1》</span>');
    return html;
  }

  function renderKnowledgeAnswer(value) {
    const lines = String(value || '未找到相关报告').replace(/\r\n?/g, '\n').split('\n');
    const blocks = [];
    lines.forEach(rawLine => {
      const line = rawLine.trim();
      if (!line) return;
      let match = line.match(/^#{3}\s+(.+)$/);
      if (match) { blocks.push(`<h4>${renderKnowledgeInline(match[1])}</h4>`); return; }
      match = line.match(/^#{1,2}\s+(.+)$/);
      if (match) { blocks.push(`<h3>${renderKnowledgeInline(match[1])}</h3>`); return; }
      match = line.match(/^[*\-•]\s+(.+)$/);
      if (match) { blocks.push(`<div class="knowledge-bullet">${renderKnowledgeInline(match[1])}</div>`); return; }
      match = line.match(/^(\d+)[.、]\s*(.+)$/);
      if (match) { blocks.push(`<div class="knowledge-numbered"><span>${match[1]}.</span><p>${renderKnowledgeInline(match[2])}</p></div>`); return; }
      blocks.push(`<p>${renderKnowledgeInline(line)}</p>`);
    });
    return blocks.join('');
  }

  async function submitKnowledgeQuestion(event) {
    event.preventDefault();
    const input = document.getElementById('knowledgeQuestion');
    const question = input?.value.trim();
    if (!question) return notify('请输入问题', 'error');
    const questionType = state.knowledge.questionType === 'report_retrieval' ? 'report_retrieval' : 'general_work';
    const thinking = questionType === 'general_work' && Boolean(state.knowledge.thinking);
    const existingMessages = (state.knowledge.messages || []).filter(message => !message.streaming);
    const context = questionType === 'general_work'
      ? existingMessages.map(message => ({
          role: message.role,
          content: message.text || '',
          sources: (message.sources || []).map(source => ({ id: source.id, title: source.title })),
        }))
      : [];
    const conversationId = state.knowledge.conversationId || createKnowledgeConversationId();
    state.knowledge.conversationId = conversationId;
    state.knowledge.activeConversationId = conversationId;
    state.knowledge.draft = '';
    state.knowledge.messages.push({ role: 'user', text: question, questionType, thinking });
    // 追加一个流式占位气泡：状态行先显示检索进度，随后逐字填充 AI 回答。
    const placeholder = { role: 'assistant', text: '', sources: [], streaming: true, questionType, thinking, stage: questionType === 'general_work' ? (thinking ? '正在检索知识库并进行深度思考…' : '正在检索知识库并理解对话上下文…') : '正在检索相关报告…' };
    state.knowledge.messages.push(placeholder);
    renderKnowledgeSearch();
    const form = document.getElementById('knowledgeForm');
    form?.classList.add('loading');
    const submitBtn = form?.querySelector('button[type=submit]');
    if (submitBtn) { submitBtn.disabled = true; submitBtn.textContent = '生成中…'; }
    let doneResult = null;
    // 两种模式都会检索知识库；自由问答额外携带当前会话上下文。
    const options = {
      ...(state.knowledge.filters || {}),
      questionType,
      thinking,
      conversationId,
      context,
    };
    try {
      await API.knowledgeAskStream(question, options, evt => {
        if (evt.type === 'stage') {
          placeholder.stage = evt.text || '';
          scheduleKnowledgeStreamRefresh();
        } else if (evt.type === 'delta') {
          placeholder.text += evt.text || '';
          scheduleKnowledgeStreamRefresh();
        } else if (evt.type === 'done') {
          doneResult = evt;
        } else if (evt.type === 'error') {
          throw new Error(evt.message || '知识搜索暂时不可用。');
        }
      });
      if (doneResult) {
        placeholder.text = doneResult.answer || '';
        placeholder.sources = doneResult.sources || [];
        placeholder.streaming = false;
        placeholder.stage = '';
        const resolvedConversationId = doneResult.conversationId || conversationId;
        state.knowledge.conversationId = resolvedConversationId;
        state.knowledge.activeConversationId = resolvedConversationId;
        const newHistoryItem = { question, answer: placeholder.text, sources: placeholder.sources, questionType, thinking, conversationId: resolvedConversationId, createdAt: new Date().toISOString() };
        state.knowledge.history = [...(state.knowledge.history || []), newHistoryItem];
        state.knowledge = { ...state.knowledge, limit: doneResult.limit ?? state.knowledge.limit, used: doneResult.used ?? state.knowledge.used, remaining: doneResult.remaining ?? state.knowledge.remaining };
      }
    } catch (error) {
      placeholder.streaming = false;
      placeholder.stage = '';
      // 已流出的部分回答保留，仅在没有任何内容时才显示错误占位。
      placeholder.text = placeholder.text || error.message || '知识搜索暂时不可用。';
      notify(error.message || '知识搜索失败', 'error');
    }
    renderKnowledgeSearch();
  }

  // 流式期间避免每个 delta 都全量重渲染视图，只增量更新最后一个 assistant 气泡。
  let knowledgeStreamRefreshTimer = null;
  function scheduleKnowledgeStreamRefresh() {
    if (knowledgeStreamRefreshTimer) return;
    knowledgeStreamRefreshTimer = setTimeout(() => {
      knowledgeStreamRefreshTimer = null;
      refreshKnowledgeStreamBubble();
    }, 60);
  }

  function refreshKnowledgeStreamBubble() {
    const conversation = document.getElementById('knowledgeConversation');
    const message = state.knowledge.messages[state.knowledge.messages.length - 1];
    if (!conversation || !message || message.role !== 'assistant') return;
    const bubbles = conversation.querySelectorAll('.knowledge-message.assistant');
    const bubble = bubbles[bubbles.length - 1];
    if (!bubble) return;
    const statusEl = bubble.querySelector('.knowledge-status');
    if (statusEl && !(message.streaming && message.stage)) statusEl.remove();
    else if (statusEl) statusEl.textContent = message.stage;
    const answerEl = bubble.querySelector('.knowledge-answer');
    if (answerEl) {
      answerEl.classList.toggle('streaming', Boolean(message.streaming));
      answerEl.innerHTML = message.text ? renderKnowledgeAnswer(message.text) : '';
    }
    conversation.scrollTop = conversation.scrollHeight;
  }

  function focusHistoryItem(conversationId) {
    const conversation = knowledgeConversations().find(item => item.id === conversationId);
    if (!conversation) return;
    const latest = conversation.items[conversation.items.length - 1] || {};
    state.knowledge.activeConversationId = conversation.id;
    state.knowledge.conversationId = conversation.id;
    state.knowledge.questionType = latest.questionType === 'report_retrieval' ? 'report_retrieval' : 'general_work';
    state.knowledge.thinking = state.knowledge.questionType === 'general_work' && Boolean(latest.thinking);
    state.knowledge.draft = '';
    state.knowledge.messages = conversation.items.flatMap(item => [
      { role: 'user', text: item.question || '', questionType: item.questionType || 'general_work', thinking: Boolean(item.thinking) },
      { role: 'assistant', text: item.answer || '未找到相关报告', sources: item.sources || [], questionType: item.questionType || 'general_work', thinking: Boolean(item.thinking) },
    ]);
    renderKnowledgeSearch();
  }

  function startNewKnowledgeConversation() {
    state.knowledge.messages = [];
    state.knowledge.conversationId = createKnowledgeConversationId();
    state.knowledge.activeConversationId = '';
    state.knowledge.questionType = 'general_work';
    state.knowledge.thinking = false;
    state.knowledge.draft = '';
    renderKnowledgeSearch();
    document.getElementById('knowledgeQuestion')?.focus();
  }

  function setKnowledgeQuestionType(questionType) {
    state.knowledge.questionType = questionType === 'report_retrieval' ? 'report_retrieval' : 'general_work';
    renderKnowledgeSearch();
    const input = document.getElementById('knowledgeQuestion');
    if (input) { input.focus(); input.setSelectionRange(input.value.length, input.value.length); }
  }

  function toggleKnowledgeThinking() {
    if (state.knowledge.questionType !== 'general_work') return;
    state.knowledge.thinking = !state.knowledge.thinking;
    renderKnowledgeSearch();
    document.getElementById('knowledgeQuestion')?.focus();
  }

  function showClearHistoryModal() {
    const history = Array.isArray(state.knowledge.history) ? state.knowledge.history : [];
    if (!history.length) return notify('暂无历史对话可清理', 'error');
    openModal(`<section class="modal-card">
      ${modalHeader('知识搜索', '清理历史提问')}
      <div class="modal-body">
        <div class="detail-permission"><svg viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.8"/><path d="M12 8v4M12 16h.01" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg><div><strong style="font-size:18px">确认清空全部历史对话吗？</strong><p>共 <strong>${knowledgeConversations().length}</strong> 段对话、${history.length} 轮问答将被清除。此操作不可撤销。</p></div></div>
      </div>
      <div class="modal-footer">
        <button type="button" class="btn btn-ghost" data-close="modal">取消</button>
        <button type="button" class="btn btn-danger" data-action="confirm-clear-history">确认清理</button>
      </div>
    </section>`);
  }

  async function confirmClearHistory() {
    try {
      await API.knowledgeClearHistory();
      state.knowledge.history = [];
      state.knowledge.activeConversationId = '';
      state.knowledge.conversationId = createKnowledgeConversationId();
      state.knowledge.questionType = 'general_work';
      state.knowledge.thinking = false;
      state.knowledge.draft = '';
      state.knowledge.messages = [];
      closeModal();
      renderKnowledgeSearch();
      notify('历史对话已清空');
    } catch (error) {
      notify(error.message || '清空失败', 'error');
    }
  }

  function renderRatingCenter() {
    if (!canRate()) {
      els.viewRoot.innerHTML = `
        <section class="page-heading"><div><h1>待我评分</h1><p>深度报告采用三维等权评分。</p></div></section>
        <section class="permission-card">
          <div class="permission-icon"><svg viewBox="0 0 24 24" fill="none"><rect x="4" y="10" width="16" height="11" rx="2" stroke="currentColor" stroke-width="1.8"/><path d="M8 10V7a4 4 0 0 1 8 0v3" stroke="currentColor" stroke-width="1.8"/></svg></div>
          <h2>行政账号不参与评分</h2>
          <p>您可以上传、查看和下载报告，也可以查看团队评分汇总；个人评分明细仅领导可见。</p>
          <button class="btn btn-primary" data-action="go-results">查看评分汇总</button>
        </section>`;
      return;
    }

    const eligible = scoredReports().filter(canRateReport);
    const pending = eligible.filter(report => isCurrentMonthReport(report) && !userRating(report.id));
    const historical = eligible.filter(report => !isCurrentMonthReport(report) && !userRating(report.id));
    const completed = eligible.filter(report => userRating(report.id));
    const source = state.ratingTab === 'completed' ? completed : state.ratingTab === 'history' ? historical : pending;
    const filters = state.ratingFilters;
    const list = source.filter(report => (!filters.month || monthKey(report) === filters.month) && (!filters.person || report.author === filters.person) && (!filters.category || report.category === filters.category) && (!filters.theme || report.theme === filters.theme)).sort(reportComparator(filters.sort));
    const months = [...new Set(eligible.map(monthKey).filter(Boolean))].sort().reverse();
    const people = [...new Set(eligible.map(report => report.author).filter(Boolean))].sort((a, b) => a.localeCompare(b, 'zh-CN'));
    els.viewRoot.innerHTML = `
      <section class="page-heading"><div><h1>待我评分</h1><p>投资启发性、研究深度、实用性三个维度权重相同，每项 1–10 分。</p></div></section>
      <section class="rating-guide">
        ${config.scoreDimensions.map((dimension, index) => `<div><span>0${index + 1}</span><strong>${dimension.label}</strong><p>${dimension.description}</p></div>`).join('')}
      </section>
      <div class="rating-tabs">
        <button class="${state.ratingTab === 'pending' ? 'active' : ''}" data-action="rating-tab" data-tab="pending">待评分 <span>${pending.length}</span></button>
        <button class="${state.ratingTab === 'completed' ? 'active' : ''}" data-action="rating-tab" data-tab="completed">已完成 <span>${completed.length}</span></button>
        <button class="history-tab ${state.ratingTab === 'history' ? 'active' : ''}" data-action="rating-tab" data-tab="history">历史未评分 <span>${historical.length}</span></button>
      </div>
      ${renderListControls('rating', filters, months, people)}
      <section class="rating-list">
        ${list.map(report => ratingListItem(report)).join('') || emptyState(state.ratingTab === 'pending' ? '本月评分已完成' : state.ratingTab === 'history' ? '没有历史未评分报告' : '还没有已完成的评分', state.ratingTab === 'pending' ? '本月暂无待评分报告，可以前往评分结果查看团队反馈。' : state.ratingTab === 'history' ? '过往报告均已留下评分。' : '完成第一份深度报告评分后会显示在这里。')}
      </section>`;
  }

  function reportComparator(sort) {
    if (sort === 'date-asc') return (a, b) => reportDateValue(a) - reportDateValue(b);
    if (sort === 'person-asc') return (a, b) => String(a.author || '').localeCompare(String(b.author || ''), 'zh-CN') || reportDateValue(b) - reportDateValue(a);
    if (sort === 'person-desc') return (a, b) => String(b.author || '').localeCompare(String(a.author || ''), 'zh-CN') || reportDateValue(b) - reportDateValue(a);
    if (sort === 'participation-desc') return (a, b) => reportRatingStats(b.id).count - reportRatingStats(a.id).count || reportDateValue(b) - reportDateValue(a);
    if (sort === 'participation-asc') return (a, b) => reportRatingStats(a.id).count - reportRatingStats(b.id).count || reportDateValue(b) - reportDateValue(a);
    if (sort === 'score-desc' && isLeader()) return (a, b) => (reportRatingStats(b.id).overall || 0) - (reportRatingStats(a.id).overall || 0);
    if (sort === 'score-asc' && isLeader()) return (a, b) => (reportRatingStats(a.id).overall || 0) - (reportRatingStats(b.id).overall || 0);
    return (a, b) => reportDateValue(b) - reportDateValue(a);
  }

  function renderListControls(prefix, filters, months, people, includeParticipation = false, scoredOnly = false) {
    const categoryEntries = scoredOnly ? Object.entries(categories).filter(([key, meta]) => meta.scored) : Object.entries(categories);
    const categoryOptions = categoryEntries.map(([key, meta]) => `<option value="${key}" ${filters.category === key ? 'selected' : ''}>${meta.label}</option>`).join('');
    const themeOptions = Object.entries(themes).map(([key, meta]) => `<option value="${key}" ${filters.theme === key ? 'selected' : ''}>${meta.label}</option>`).join('');
    return `<section class="list-controls" aria-label="筛选与排序">
      <select id="${prefix}MonthFilter" class="select-control" aria-label="按年月筛选"><option value="">全部年月</option>${months.map(key => `<option value="${key}" ${filters.month === key ? 'selected' : ''}>${monthLabel(key)}</option>`).join('')}</select>
      <select id="${prefix}PersonFilter" class="select-control" aria-label="按人员筛选"><option value="">全部人员</option>${people.map(person => `<option value="${escapeHTML(person)}" ${filters.person === person ? 'selected' : ''}>${escapeHTML(person)}</option>`).join('')}</select>
      <select id="${prefix}CategoryFilter" class="select-control" aria-label="按报告类型筛选"><option value="">全部类型</option>${categoryOptions}</select>
      <select id="${prefix}ThemeFilter" class="select-control" aria-label="按主题筛选"><option value="">全部主题</option>${themeOptions}</select>
      <select id="${prefix}Sort" class="select-control" aria-label="排序方式">
        <option value="date-desc" ${filters.sort === 'date-desc' ? 'selected' : ''}>日期：由近到远</option><option value="date-asc" ${filters.sort === 'date-asc' ? 'selected' : ''}>日期：由远到近</option>
        <option value="person-asc" ${filters.sort === 'person-asc' ? 'selected' : ''}>人员：正序</option><option value="person-desc" ${filters.sort === 'person-desc' ? 'selected' : ''}>人员：倒序</option>
        ${includeParticipation ? `<option value="participation-desc" ${filters.sort === 'participation-desc' ? 'selected' : ''}>参与人数：由多到少</option><option value="participation-asc" ${filters.sort === 'participation-asc' ? 'selected' : ''}>参与人数：由少到多</option>${isLeader() ? `<option value="score-desc" ${filters.sort === 'score-desc' ? 'selected' : ''}>综合评分：由高到低</option><option value="score-asc" ${filters.sort === 'score-asc' ? 'selected' : ''}>综合评分：由低到高</option>` : ''}` : ''}
      </select>
      ${(filters.month || filters.person || filters.category || filters.theme || filters.sort !== 'date-desc') ? `<button class="text-button controls-reset" data-action="reset-${prefix}-filters">清除筛选</button>` : '<span></span>'}
    </section>`;
  }

  function ratingListItem(report) {
    const mine = userRating(report.id);
    const stats = reportRatingStats(report.id);
    return `<article class="rating-list-item">
      <div class="rating-doc-icon">${report.category === 'deep' ? '深' : '月'}</div>
      <div class="rating-list-main"><div>${categoryPill(report.category)}${themePill(report.theme)}<span class="rating-date">${formatDate(report.reportDate)}</span></div><h3 class="rating-title-link" data-action="view-report" data-id="${report.id}" title="点击查看报告详情">${escapeHTML(report.title)}</h3><p>${escapeHTML(report.author)} · ${escapeHTML(report.org)}</p></div>
      <div class="rating-team-score"><span>评分进度</span><strong>${stats.count}</strong><small>${stats.count} 人参与 · ${stats.pending} 人未评</small></div>
      ${mine ? `<div class="my-score"><span>我的评分</span><strong>${scoreOf(mine)}</strong></div>` : '<div class="my-score pending"><span>我的评分</span><strong>待完成</strong></div>'}
      <div class="rating-item-actions">
        <button class="btn btn-ghost btn-small" data-action="view-report" data-id="${report.id}">查看报告</button>
        ${mine ? `<button class="btn btn-secondary" data-action="view-my-rating" data-id="${report.id}">查看我的评分</button>` : `<button class="btn btn-primary" data-action="open-rating" data-id="${report.id}">开始评分</button>`}
      </div>
    </article>`;
  }

  function renderResults() {
    const allEligible = scoredReports();
    const filters = state.resultFilters;
    const eligible = allEligible.filter(report => (!filters.month || monthKey(report) === filters.month) && (!filters.person || report.author === filters.person) && (!filters.category || report.category === filters.category) && (!filters.theme || report.theme === filters.theme)).sort(reportComparator(filters.sort));
    const total = state.ratingSummary.totalRatings || 0;
    const ratedReports = allEligible.filter(report => reportRatingStats(report.id).count).length;
    const overall = isLeader() && state.ratings.length ? Number((state.ratings.reduce((sum, row) => sum + scoreOf(row), 0) / state.ratings.length).toFixed(1)) : null;
    const months = [...new Set(allEligible.map(monthKey).filter(Boolean))].sort().reverse();
    const people = [...new Set(allEligible.map(report => report.author).filter(Boolean))].sort((a, b) => a.localeCompare(b, 'zh-CN'));

    // 表头点击排序：在同列的 -desc / -asc 之间切换
    const sortArrow = col => {
      const colBase = col.replace(/-(?:desc|asc)$/, '');
      if (!filters.sort.startsWith(colBase)) return '';
      return filters.sort.endsWith('-asc') ? ' ▲' : ' ▼';
    };
    const leaderHead = () => `<tr>
      <th class="th-sortable" data-action="sort-result-column" data-sort="date-desc">报告${sortArrow('date-desc')}</th>
      <th>投资启发性</th><th>研究深度</th><th>实用性</th>
      <th class="th-sortable" data-action="sort-result-column" data-sort="score-desc">综合评分${sortArrow('score-desc')}</th>
      <th class="th-sortable" data-action="sort-result-column" data-sort="participation-desc">已评分${sortArrow('participation-desc')}</th>
      <th class="th-sortable" data-action="sort-result-column" data-sort="participation-asc">未评分${sortArrow('participation-asc')}</th>
      <th></th>
    </tr>`;
    const memberHead = () => `<tr>
      <th class="th-sortable" data-action="sort-result-column" data-sort="date-desc">报告${sortArrow('date-desc')}</th>
      <th class="th-sortable" data-action="sort-result-column" data-sort="date-desc">报告日期${sortArrow('date-desc')}</th>
      <th>汇总平均分</th>
      <th class="th-sortable" data-action="sort-result-column" data-sort="participation-desc">已评分${sortArrow('participation-desc')}</th>
      <th class="th-sortable" data-action="sort-result-column" data-sort="participation-asc">未评分${sortArrow('participation-asc')}</th>
      <th>意见建议</th>
      <th></th>
    </tr>`;

    els.viewRoot.innerHTML = `
      <section class="page-heading"><div><h1>评分结果</h1><p>${isLeader() ? '可查看团队评分汇总及全部人员明细。' : '可查看本人报告评分；其他报告仅展示参与进度及匿名意见。'}</p></div></section>
      <section class="result-summary-grid">
        ${isLeader() ? `<div><span>综合平均分</span><strong>${overall ?? '—'}</strong><small>三维等权</small></div>` : `<div><span>已收到反馈报告</span><strong>${ratedReports}</strong><small>共 ${allEligible.length} 份需评分</small></div>`}
        <div><span>累计评分记录</span><strong>${total}</strong><small>${state.ratingSummary.participants || 0} 人参与</small></div>
        <div><span>应评分人员</span><strong>${state.ratingSummary.totalScorers || 0}</strong><small>不含行政账号</small></div>
      </section>
      <section class="panel-card result-table-card">
        <div class="panel-header"><div><h2>报告评分汇总</h2><p>可按年月、报告人员、类型、主题筛选与排序${isLeader() ? '，支持点击表头排序' : ''}</p></div><div class="panel-header-actions">${isLeader() ? `<button class="btn btn-secondary btn-small" data-action="export-results-excel"><svg viewBox="0 0 24 24" fill="none" width="16" height="16"><path d="M12 3v12m0 0 4-4m-4 4-4-4M5 21h14" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>导出 Excel</button>` : ''}<span class="permission-note">${isLeader() ? '领导视图 · 完整评分明细' : '本人报告可见总分 · 无个人明细'}</span></div></div>
        ${renderListControls('result', filters, months, people, true, true)}
        <div class="result-table-wrap">
          <table class="result-table">
            <thead>${isLeader() ? leaderHead() : memberHead()}</thead>
            <tbody>${eligible.map(report => resultRow(report)).join('') || `<tr><td colspan="${isLeader() ? 8 : 7}"><div class="table-empty">没有匹配的评分报告</div></td></tr>`}</tbody>
          </table>
        </div>
      </section>`;
  }

  function scoreCell(score) {
    if (score == null) return '<span class="no-score">—</span>';
    return `<span class="table-score"><i style="--score:${score * 10}%"></i><b>${score}</b></span>`;
  }

  function resultRow(report) {
    const stats = reportRatingStats(report.id);
    const feedbackCount = visibleFeedback(report.id).length;
    if (!isLeader()) return `<tr>
      <td><button class="table-report" data-action="view-report" data-id="${report.id}"><strong>${escapeHTML(report.title)}</strong><span>${escapeHTML(report.author)} · ${categories[report.category].label}</span></button></td>
      <td><span class="result-date">${formatDate(report.reportDate)}</span></td>
      <td>${canViewReportTotal(report) ? `<strong class="overall-score ${stats.count ? '' : 'empty'}">${stats.overall ?? '—'}</strong>` : '<span class="no-score" title="仅报告作者可查看汇总平均分">—</span>'}</td>
      <td><span class="participant-count done">${stats.count}</span></td>
      <td><span class="participant-count pending">${stats.pending}</span></td>
      <td><span class="anonymous-count">${feedbackCount} 条</span></td>
      <td><button class="text-button" data-action="view-results" data-id="${report.id}">查看意见 →</button></td>
    </tr>`;
    return `<tr>
      <td><button class="table-report" data-action="view-report" data-id="${report.id}"><strong>${escapeHTML(report.title)}</strong><span>${escapeHTML(report.author)} · ${categories[report.category].label}</span></button></td>
      <td>${scoreCell(stats.inspiration)}</td><td>${scoreCell(stats.depth)}</td><td>${scoreCell(stats.utility)}</td>
      <td><strong class="overall-score ${stats.count ? '' : 'empty'}">${stats.overall ?? '—'}</strong></td>
      <td><span class="participant-count">${stats.count}</span></td>
      <td><span class="participant-count pending">${stats.pending}</span></td>
      <td><button class="text-button" data-action="view-results" data-id="${report.id}">查看${isLeader() ? '明细' : '详情'} →</button></td>
    </tr>`;
  }

  function exportResultsExcel() {
    if (!isLeader()) return;
    const filters = state.resultFilters;
    const eligible = scoredReports()
      .filter(report => (!filters.month || monthKey(report) === filters.month) && (!filters.person || report.author === filters.person) && (!filters.category || report.category === filters.category) && (!filters.theme || report.theme === filters.theme))
      .sort(reportComparator(filters.sort));
    const header = ['报告标题', '作者', '部门', '报告类型', '主题', '报告日期', '投资启发性', '研究深度', '实用性', '综合评分', '已评分', '未评分'];
    const rows = eligible.map(report => {
      const stats = reportRatingStats(report.id);
      return [
        report.title || '',
        report.author || '',
        report.org || '',
        categories[report.category]?.label || '',
        themes[report.theme]?.label || '',
        formatDate(report.reportDate),
        stats.inspiration ?? '',
        stats.depth ?? '',
        stats.utility ?? '',
        stats.overall ?? '',
        stats.count,
        stats.pending
      ];
    });
    const escapeCell = val => String(val).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    const html = `<html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:x="urn:schemas-microsoft-com:office:excel" xmlns="http://www.w3.org/TR/REC-html40"><head><meta charset="UTF-8"><!--[if gte mso 9]><xml><x:ExcelWorkbook><x:ExcelWorksheets><x:ExcelWorksheet><x:Name>评分结果</x:Name><x:WorksheetOptions><x:DisplayGridlines/></x:WorksheetOptions></x:ExcelWorksheet></x:ExcelWorksheets></x:ExcelWorkbook></xml><![endif]--></head><body><table border="1"><thead><tr>${header.map(h => `<th>${escapeCell(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(row => `<tr>${row.map(cell => `<td>${escapeCell(cell)}</td>`).join('')}</tr>`).join('')}</tbody></table></body></html>`;
    const blob = new Blob(['\ufeff' + html], { type: 'application/vnd.ms-excel;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `评分结果_${new Date().toISOString().slice(0, 10)}.xls`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  function exportResultDetailExcel(reportId) {
    if (!isLeader()) return notify('仅领导可导出评分明细', 'error');
    const report = getReport(reportId);
    if (!report) return notify('未找到该报告', 'error');
    const rows = state.ratings.filter(row => row.reportId === reportId).sort((a, b) => new Date(b.updatedAt) - new Date(a.updatedAt));
    const stats = reportRatingStats(reportId);
    const escapeCell = val => String(val == null ? '' : val).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    const safeTitle = (report.title || '报告').replace(/[\\/:*?"<>|]/g, '_');
    const dimLabels = config.scoreDimensions.map(dim => dim.label);
    const header = ['评分人', '部门', ...dimLabels, '综合评分', '意见和建议', '评分时间'];
    const bodyRows = rows.map(row => [
      row.userName || row.userId || '',
      row.userOrg || '',
      ...config.scoreDimensions.map(dim => row[dim.key]),
      scoreOf(row),
      row.comment || '',
      row.updatedAt ? formatDateTime(row.updatedAt) : ''
    ]);
    // 末尾追加团队均分行
    bodyRows.push(['团队均分', '', ...config.scoreDimensions.map(dim => stats[dim.key] ?? ''), stats.overall ?? '', `${stats.count} 人参与`, '']);
    const infoRows = [
      [`报告评分明细 · ${report.title || ''}`],
      ['作者', report.author || '', '部门', report.org || '', '报告日期', formatDate(report.reportDate), '类型', categories[report.category]?.label || '', '主题', themes[report.theme]?.label || ''],
      ['综合评分', stats.overall ?? '—', ...config.scoreDimensions.flatMap(dim => [dim.label, stats[dim.key] ?? '—']), '已评分', `${stats.count} 人`, '未评分', `${stats.pending} 人`]
    ];
    const html = `<html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:x="urn:schemas-microsoft-com:office:excel" xmlns="http://www.w3.org/TR/REC-html40"><head><meta charset="UTF-8"><!--[if gte mso 9]><xml><x:ExcelWorkbook><x:ExcelWorksheets><x:ExcelWorksheet><x:Name>评分明细</x:Name><x:WorksheetOptions><x:DisplayGridlines/></x:WorksheetOptions></x:ExcelWorksheet></x:ExcelWorksheets></x:ExcelWorkbook></xml><![endif]--></head><body><table border="1">
      <thead>${infoRows.map(row => `<tr>${row.map((cell, i) => `<th${i === 0 && row.length === 1 ? ' colspan="' + header.length + '"' : ''}>${escapeCell(cell)}</th>`).join('')}</tr>`).join('')}</thead>
      <tbody><tr>${header.map(h => `<th>${escapeCell(h)}</th>`).join('')}</tr>${bodyRows.map(row => `<tr>${row.map(cell => `<td>${escapeCell(cell)}</td>`).join('')}</tr>`).join('')}</tbody>
    </table></body></html>`;
    const blob = new Blob(['\ufeff' + html], { type: 'application/vnd.ms-excel;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `评分明细_${safeTitle}_${new Date().toISOString().slice(0, 10)}.xls`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    notify('已导出评分明细');
  }

  function visibleFeedback(reportId) {
    const mine = userRating(reportId);
    const myFeedback = mine && String(mine.comment || '').trim()
      ? [{ reportId, comment: mine.comment, updatedAt: mine.updatedAt, isMine: true }]
      : [];
    const anonymous = state.feedback
      .filter(item => item.reportId === reportId && String(item.comment || '').trim())
      .map(item => ({ ...item, isMine: false }));
    return [...myFeedback, ...anonymous];
  }

  function emptyState(title, description) {
    return `<div class="empty-state"><div class="empty-icon"><svg viewBox="0 0 24 24" fill="none"><path d="M6 3h8l4 4v14H6V3Z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><path d="M14 3v5h4M9 13h6M9 17h4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg></div><strong>${escapeHTML(title)}</strong><p>${escapeHTML(description)}</p></div>`;
  }

  function openModal(html, size = '') {
    els.modalLayer.className = `modal-layer open ${size}`;
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

  // ---- 单篇报告 AI 摘要面板（报告详情弹窗与在线查看分栏共用） ----
  const AI_SUMMARY_STYLES = [['concise', '精炼版'], ['standard', '标准版'], ['deep', '深度版']];
  const AI_SUMMARY_PREF_KEY = 'kbAiSummaryPrefs';
  // 同一时刻只存在一个面板实例（详情弹窗或在线查看二选一）
  const aiSummaryState = { reportId: null, style: 'standard', generating: false, asking: false, abortController: null };

  function aiSummaryPrefs() {
    try { return JSON.parse(localStorage.getItem(AI_SUMMARY_PREF_KEY) || '{}') || {}; } catch (_) { return {}; }
  }
  function saveAiSummaryPrefs(patch) {
    try { localStorage.setItem(AI_SUMMARY_PREF_KEY, JSON.stringify({ ...aiSummaryPrefs(), ...patch })); } catch (_) { /* 无痕模式等场景静默忽略 */ }
  }
  function currentAiSummaryStyle() {
    const prefs = aiSummaryPrefs();
    return AI_SUMMARY_STYLES.some(([key]) => key === prefs.style) ? prefs.style : 'standard';
  }
  function aiSummaryStyleLabel(style) {
    return (AI_SUMMARY_STYLES.find(([key]) => key === style) || [, '标准版'])[1];
  }
  function aiSummaryBodyEl() { return document.querySelector('.ai-summary-panel .ai-summary-body'); }

  function mountAiSummaryPanel(container, report) {
    // 新面板挂载时打断上一份报告还在进行的生成流
    aiSummaryState.abortController?.abort();
    aiSummaryState.abortController = null;
    aiSummaryState.reportId = report.id;
    aiSummaryState.style = currentAiSummaryStyle();
    aiSummaryState.generating = false;
    aiSummaryState.asking = false;
    container.innerHTML = `
      <div class="ai-summary-panel" data-report-id="${report.id}">
        <div class="ai-summary-head">
          <span class="detail-label">AI 摘要</span>
          <div class="ai-summary-styles">
            ${AI_SUMMARY_STYLES.map(([key, label]) => `<button type="button" class="ai-summary-style ${key === aiSummaryState.style ? 'active' : ''}" data-action="ai-summary-style" data-id="${report.id}" data-style="${key}">${label}</button>`).join('')}
          </div>
        </div>
        <div class="ai-qa">
          <button type="button" class="ai-qa-toggle" data-action="toggle-report-qa">就本文提问 ▾</button>
          <div class="ai-qa-panel" hidden>
            <div class="ai-qa-form">
              <input type="text" class="ai-qa-input" maxlength="500" placeholder="就这份报告提问，Enter 发送">
              <button type="button" class="btn btn-secondary ai-qa-send" data-action="submit-report-qa" data-id="${report.id}">提问</button>
            </div>
            <div class="ai-qa-answer"></div>
          </div>
        </div>
        <div class="ai-summary-body"><div class="ai-summary-status">正在读取 AI 摘要…</div></div>
      </div>`;
    loadAiSummary(report.id, aiSummaryState.style);
  }

  async function loadAiSummary(reportId, style) {
    let body = aiSummaryBodyEl();
    if (!body) return;
    body.innerHTML = '<div class="ai-summary-status">正在读取 AI 摘要…</div>';
    try {
      const data = await API.getAiSummary(reportId, style);
      body = aiSummaryBodyEl();
      if (!body || aiSummaryState.reportId !== reportId || aiSummaryState.style !== style) return;
      if (data.available) {
        // 命中缓存：打断可能仍在进行的旧生成流，直接展示缓存
        aiSummaryState.abortController?.abort();
        renderAiSummaryContent(body, data);
      } else {
        // 无缓存：自动生成当前版本，无需用户再点
        generateAiSummary(reportId, style, false);
      }
    } catch (error) {
      body = aiSummaryBodyEl();
      if (body) body.innerHTML = `<div class="ai-summary-error">${escapeHTML(error.message || 'AI 摘要加载失败')}</div>`;
    }
  }

  function renderAiSummaryContent(body, data) {
    body.innerHTML = `
      <div class="ai-summary-content">${renderKnowledgeAnswer(data.summary || '')}</div>
      <div class="ai-summary-meta">
        <span>${escapeHTML(aiSummaryStyleLabel(data.style || aiSummaryState.style))} · ${escapeHTML(formatDateTime(data.generatedAt))}${data.model ? ` · ${escapeHTML(data.model)}` : ''}${data.generatedByName ? ` · ${escapeHTML(data.generatedByName)}生成` : ''}</span>
        <button type="button" class="text-button ai-summary-regen" data-action="regenerate-ai-summary" data-id="${aiSummaryState.reportId}">重新生成</button>
      </div>`;
  }

  async function generateAiSummary(reportId, style, force) {
    let body = aiSummaryBodyEl();
    if (!body || aiSummaryState.reportId !== reportId) return;
    if (aiSummaryState.generating) aiSummaryState.abortController?.abort();
    const controller = new AbortController();
    aiSummaryState.abortController = controller;
    aiSummaryState.generating = true;
    body.innerHTML = '<div class="ai-summary-status"><span class="loading-spinner"></span>正在准备生成…</div>';
    let acc = '';
    let refreshTimer = null;
    const refresh = () => {
      if (refreshTimer) return;
      refreshTimer = setTimeout(() => {
        refreshTimer = null;
        const el = aiSummaryBodyEl();
        if (el && acc) el.innerHTML = `<div class="ai-summary-content streaming">${renderKnowledgeAnswer(acc)}</div>`;
      }, 60);
    };
    try {
      await API.aiSummaryStream(reportId, style, force, ev => {
        const el = aiSummaryBodyEl();
        if (!el || aiSummaryState.reportId !== reportId || aiSummaryState.style !== style) return;
        if (ev.type === 'stage') {
          el.innerHTML = `<div class="ai-summary-status"><span class="loading-spinner"></span>${escapeHTML(ev.text || '正在生成…')}</div>`;
        } else if (ev.type === 'delta') {
          acc += ev.text || '';
          refresh();
        } else if (ev.type === 'done') {
          if (refreshTimer) { clearTimeout(refreshTimer); refreshTimer = null; }
          renderAiSummaryContent(el, ev);
        } else if (ev.type === 'error') {
          throw new Error(ev.message || '生成失败');
        }
      }, controller.signal);
    } catch (error) {
      if (refreshTimer) { clearTimeout(refreshTimer); refreshTimer = null; }
      if (error.name === 'AbortError') return; // 被新请求/新面板打断，静默退出
      body = aiSummaryBodyEl();
      if (body) body.innerHTML = `<div class="ai-summary-error">${escapeHTML(error.message || 'AI 摘要生成失败')} <button type="button" class="text-button" data-action="generate-ai-summary" data-id="${reportId}">重试</button></div>`;
      notify(error.message || 'AI 摘要生成失败', 'error');
    } finally {
      if (aiSummaryState.abortController === controller) {
        aiSummaryState.generating = false;
        aiSummaryState.abortController = null;
      }
    }
  }

  function switchAiSummaryStyle(style) {
    if (!AI_SUMMARY_STYLES.some(([key]) => key === style)) return;
    if (style === aiSummaryState.style) return;
    aiSummaryState.style = style;
    saveAiSummaryPrefs({ style });
    document.querySelectorAll('.ai-summary-style').forEach(btn => btn.classList.toggle('active', btn.dataset.style === style));
    // 无缓存会自动开始生成，进行中的旧生成流在 loadAiSummary/generateAiSummary 内被打断
    loadAiSummary(aiSummaryState.reportId, style);
  }

  function toggleReportQa() {
    const panel = document.querySelector('.ai-summary-panel .ai-qa-panel');
    const toggle = document.querySelector('.ai-summary-panel .ai-qa-toggle');
    if (!panel || !toggle) return;
    panel.hidden = !panel.hidden;
    toggle.textContent = panel.hidden ? '就本文提问 ▾' : '收起提问 ▴';
    if (!panel.hidden) {
      const input = panel.querySelector('.ai-qa-input');
      if (input) input.focus();
    }
  }

  async function submitReportQuestion(reportId) {
    if (aiSummaryState.asking) return;
    const input = document.querySelector('.ai-summary-panel .ai-qa-input');
    const answerEl = document.querySelector('.ai-summary-panel .ai-qa-answer');
    if (!input || !answerEl || aiSummaryState.reportId !== reportId) return;
    const question = input.value.trim();
    if (question.length < 2) return notify('请输入具体问题', 'error');
    aiSummaryState.asking = true;
    input.disabled = true;
    const sendBtn = document.querySelector('.ai-summary-panel .ai-qa-send');
    if (sendBtn) sendBtn.disabled = true;
    let acc = '';
    let refreshTimer = null;
    const refresh = () => {
      if (refreshTimer) return;
      refreshTimer = setTimeout(() => {
        refreshTimer = null;
        const el = document.querySelector('.ai-summary-panel .ai-qa-answer');
        if (el && acc) el.innerHTML = `<div class="ai-qa-question">${escapeHTML(question)}</div><div class="ai-qa-content streaming">${renderKnowledgeAnswer(acc)}</div>`;
      }, 60);
    };
    answerEl.innerHTML = `<div class="ai-qa-question">${escapeHTML(question)}</div><div class="ai-qa-status"><span class="loading-spinner"></span>正在阅读本文并思考…</div>`;
    try {
      await API.reportAskStream(reportId, question, ev => {
        const el = document.querySelector('.ai-summary-panel .ai-qa-answer');
        if (!el) return;
        if (ev.type === 'stage') {
          el.innerHTML = `<div class="ai-qa-question">${escapeHTML(question)}</div><div class="ai-qa-status"><span class="loading-spinner"></span>${escapeHTML(ev.text || '正在思考…')}</div>`;
        } else if (ev.type === 'delta') {
          acc += ev.text || '';
          refresh();
        } else if (ev.type === 'done') {
          if (refreshTimer) { clearTimeout(refreshTimer); refreshTimer = null; }
          el.innerHTML = `<div class="ai-qa-question">${escapeHTML(question)}</div><div class="ai-qa-content">${renderKnowledgeAnswer(ev.answer || acc)}</div>`;
        } else if (ev.type === 'error') {
          throw new Error(ev.message || '回答失败');
        }
      });
      input.value = '';
    } catch (error) {
      if (refreshTimer) { clearTimeout(refreshTimer); refreshTimer = null; }
      const el = document.querySelector('.ai-summary-panel .ai-qa-answer');
      if (el) el.innerHTML = `<div class="ai-qa-question">${escapeHTML(question)}</div><div class="ai-qa-error">${escapeHTML(error.message || '问答暂时不可用')}</div>`;
      notify(error.message || '问答暂时不可用', 'error');
    } finally {
      aiSummaryState.asking = false;
      input.disabled = false;
      if (sendBtn) sendBtn.disabled = false;
    }
  }

  // 分栏比例偏好：摘要栏占预览区宽度的比例，限制在 15%-75%
  function paneRatioPref() {
    const ratio = Number(aiSummaryPrefs().paneRatio);
    return Number.isFinite(ratio) && ratio >= 0.15 && ratio <= 0.75 ? ratio : 0.34;
  }

  function applyPaneRatio() {
    const split = document.querySelector('.preview-split');
    if (split) split.style.setProperty('--pane-ratio', paneRatioPref());
  }

  // 在线查看：切换左侧 AI 摘要分栏（iframe 不移动不重载，仅切换布局列）。
  // 在线查看默认隐藏；force=true/false 显式开启/隐藏（详情弹窗"AI 摘要"按钮进入时用），缺省为切换。
  function togglePreviewSummary(force) {
    const pane = document.getElementById('previewSummaryPane');
    const resizer = document.getElementById('previewSplitResizer');
    const body = document.getElementById('previewBody');
    if (!pane || !body) return notify('预览窗口已关闭', 'error');
    const active = force !== undefined ? Boolean(force) : pane.hidden;
    pane.hidden = !active;
    if (resizer) resizer.hidden = !active;
    body.classList.toggle('split-on', active);
    // 摘要栏隐藏时左缘显示"›"浮出按钮，一键调出；分栏开启时隐藏
    const edgeToggle = document.getElementById('previewEdgeToggle');
    if (edgeToggle) edgeToggle.hidden = active;
    const btn = document.getElementById('previewSummaryBtn');
    if (btn) btn.classList.toggle('active', active);
    if (active) {
      applyPaneRatio();
      initPreviewSplitResizer();
      if (!pane.dataset.mounted) {
        const report = getReport(pane.dataset.reportId);
        if (report) mountAiSummaryPanel(pane, report);
        pane.dataset.mounted = '1';
      }
    }
  }

  // 拖拽分隔条调节摘要/原文比例：pointer capture 保证拖过 iframe 时仍收到移动事件。
  // 箭头按钮的 pointerdown 不启动拖拽；系统取消手势/窗口级 pointerup 兜底，防止卡在拖拽态。
  function initPreviewSplitResizer() {
    const resizer = document.getElementById('previewSplitResizer');
    const body = document.getElementById('previewBody');
    if (!resizer || !body || resizer.dataset.bound) return;
    resizer.dataset.bound = '1';
    resizer.addEventListener('pointerdown', event => {
      if (event.target.closest('.preview-split-toggle')) return;
      event.preventDefault();
      try { resizer.setPointerCapture(event.pointerId); } catch (_) { /* 无活动指针（合成事件）时忽略 */ }
      resizer.classList.add('dragging');
      const split = document.querySelector('.preview-split');
      const ratioAt = clientX => {
        const rect = body.getBoundingClientRect();
        return Math.min(Math.max((clientX - rect.left) / rect.width, 0.15), 0.75);
      };
      let finished = false;
      const move = e => split.style.setProperty('--pane-ratio', ratioAt(e.clientX));
      const finish = e => {
        if (finished) return;
        finished = true;
        resizer.removeEventListener('pointermove', move);
        resizer.removeEventListener('pointerup', finish);
        resizer.removeEventListener('pointercancel', finish);
        window.removeEventListener('pointerup', finish);
        resizer.classList.remove('dragging');
        if (e && e.type === 'pointerup') saveAiSummaryPrefs({ paneRatio: Number(ratioAt(e.clientX).toFixed(3)) });
      };
      resizer.addEventListener('pointermove', move);
      resizer.addEventListener('pointerup', finish);
      resizer.addEventListener('pointercancel', finish);
      window.addEventListener('pointerup', finish);
    });
  }

  // 详情弹窗"AI 摘要"按钮：关闭详情，打开在线查看并直接进入左摘要右原文分栏
  async function openAiSummary(reportId) {
    closeModal();
    await previewReport(reportId);
    togglePreviewSummary(true);
  }

  function showReportDetail(reportId) {
    const report = getReport(reportId);
    if (!report) return notify('未找到该报告', 'error');
    const external = report.reportType === 'external';
    // 外部报告与路演报告均展示报告作者/机构与内部上传人
    const extLike = external || report.reportType === 'roadshow';
    const typed = ['research_visit', 'roadshow'].includes(report.reportType);
    recordExternalView(report);
    const meta = categories[report.category] || categories.other;
    const stats = reportRatingStats(report.id);
    const mine = userRating(report.id);
    openModal(`<section class="modal-card report-detail-modal">
      ${modalHeader(meta.label, report.title)}
      <div class="modal-body">
        <div class="detail-hero">
          <div class="detail-file-icon">${escapeHTML(report.fileType || 'FILE')}</div>
          <div><div class="detail-pills">${external || typed ? `<span class="external-badge">${reportTypeLabel(report.reportType)}</span>` : categoryPill(report.category)}${themePill(report.theme)}</div><h3>${escapeHTML(report.title)}</h3>${external && report.recommendation ? `<div class="detail-recommendation"><span>推荐语</span><strong>${escapeHTML(report.recommendation)}</strong></div>` : ''}<p>${escapeHTML(report.summary || '暂无报告摘要')}</p></div>
        </div>
        <div class="detail-meta-grid">
          <div><span>报告作者</span><strong>${escapeHTML(extLike ? (report.sourceAuthor || report.author) : report.author)}</strong></div>
          <div><span>${extLike ? '报告机构' : '所属部门'}</span><strong>${escapeHTML(extLike ? (report.sourceInstitution || report.org) : report.org)}</strong></div>
          <div><span>研究主题</span><strong>${escapeHTML(themes[report.theme]?.label || '—')}</strong></div>
          <div><span>报告日期</span><strong>${formatDate(report.reportDate)}</strong></div>
          <div><span>文件信息</span><strong>${escapeHTML(report.fileType || '文件')} · ${escapeHTML(report.fileSize || '本地上传')}</strong></div>
          <div><span>上传时间</span><strong>${formatDateTime(report.uploadedAt)}</strong></div>
          ${extLike ? `<div><span>内部上传人</span><strong>${escapeHTML(report.uploadedByName || '未记录')}</strong></div>` : ''}
          ${report.reportType === 'roadshow' ? `<div><span>关联路演</span><strong title="${escapeHTML(roadshowScheduleLabel(report))}">${escapeHTML(roadshowScheduleLabel(report))}</strong></div>` : ''}
          <div><span>互动数据</span><strong>${external || typed ? `${report.likeCount || 0} 赞 · ` : ''}${report.viewCount || 0} 浏览 · ${report.favoriteCount || 0} 收藏</strong></div>
        </div>
        <div class="detail-section"><span class="detail-label">关键词</span><div class="report-tags large">${(report.tags || []).map(tag => `<span>${escapeHTML(tag)}</span>`).join('') || '<span>暂无标签</span>'}</div></div>
        ${!external && !typed && meta.scored ? (canViewReportTotal(report) && (stats.inspiration != null || stats.depth != null || stats.utility != null)) ? `<div class="detail-score-card">
          <div class="detail-score-head"><div><span>团队综合评分</span><strong>${stats.overall ?? '—'}</strong></div><p>${stats.count ? `${stats.count} 人已完成评分` : '暂无团队评分'}</p></div>
          <div class="detail-dimensions">${config.scoreDimensions.map(dim => `<div><span>${dim.label}</span><strong>${stats[dim.key] ?? '—'}</strong></div>`).join('')}</div>
        </div>` : `<div class="detail-progress-card"><div><span>已评分</span><strong>${stats.count}</strong></div><div><span>未评分</span><strong>${stats.pending}</strong></div><p>${isOwnReport(report) ? '本人报告的汇总评分将在评分完成后展示。' : '具体得分仅领导可见，意见建议以匿名形式共享。'}</p></div>` : ''}
      </div>
      <div class="modal-footer">
        <button class="btn btn-ghost" data-action="preview-report" data-id="${report.id}"><svg viewBox="0 0 24 24" fill="none"><path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6-10-6-10-6Z" stroke="currentColor" stroke-width="1.7"/><circle cx="12" cy="12" r="2.5" stroke="currentColor" stroke-width="1.7"/></svg>在线查看</button>
        <button class="btn btn-ghost" data-action="open-ai-summary" data-id="${report.id}"><svg viewBox="0 0 24 24" fill="none"><path d="M4 6h16M4 12h10M4 18h13" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>AI 摘要</button>
        <button class="btn btn-secondary" data-action="download" data-id="${report.id}"><svg viewBox="0 0 24 24" fill="none"><path d="M12 3v12M7 10l5 5 5-5M5 20h14" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>下载报告</button>
        ${external || typed ? `<button class="btn ${report.likedByMe ? 'btn-primary' : 'btn-secondary'}" data-action="toggle-like" data-id="${report.id}">♥ ${report.likedByMe ? '已点赞' : '点赞'} · ${report.likeCount || 0}</button>` : ''}<button class="btn ${report.favoritedByMe ? 'btn-primary' : 'btn-secondary'}" data-action="toggle-favorite" data-id="${report.id}">★ ${report.favoritedByMe ? '已收藏' : '收藏'} · ${report.favoriteCount || 0}</button>${!external && !typed && meta.scored && canRateReport(report) ? (mine ? `<button class="btn btn-primary" data-action="view-my-rating" data-id="${report.id}">查看我的评分</button>` : `<button class="btn btn-primary" data-action="open-rating" data-id="${report.id}">为报告评分</button>`) : !external && !typed && meta.scored ? `<button class="btn btn-primary" data-action="view-results" data-id="${report.id}">查看评分汇总</button>` : ''}
        ${report.reportType === 'roadshow' && canMatchRoadshow(report) ? `<button class="btn btn-secondary" data-action="match-schedule" data-id="${report.id}" title="手工关联/调整该报告对应的路演安排">匹配路演</button>` : ''}
        ${canEditReport(report) ? `<button class="btn btn-secondary" data-action="edit-report" data-id="${report.id}"><svg viewBox="0 0 24 24" fill="none"><path d="M4 20h4L18.5 9.5a2.12 2.12 0 0 0-3-3L5 17v3z" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/></svg>修改</button>` : ''}
        ${canDeleteReport(report) ? `<button class="btn btn-danger danger-action" data-action="delete-report" data-id="${report.id}">删除</button>` : ''}
      </div>
    </section>`, 'modal-wide');
  }

  async function recordExternalView(report) {
    try {
      const result = await API.recordView(report.id);
      Object.assign(report, { viewCount: result.viewCount, likeCount: result.likeCount, likedByMe: result.likedByMe });
    } catch (_) { /* 浏览统计不阻断查看 */ }
  }

  async function toggleExternalLike(reportId) {
    const report = getReport(reportId);
    if (!report) return;
    if (report.reportType === 'internal') return notify('内部报告不支持点赞', 'info');
    try {
      const result = await API.toggleLike(reportId);
      Object.assign(report, { likedByMe: result.liked, likeCount: result.likeCount, viewCount: result.viewCount });
      closeModal();
      renderView();
      notify(result.liked ? '已点赞' : '已取消点赞');
    } catch (error) { notify(error.message || '点赞失败', 'error'); }
  }

  async function toggleFavorite(reportId) {
    const report = getReport(reportId);
    if (!report) return;
    try {
      const result = await API.toggleFavorite(reportId);
      Object.assign(report, result);
      closeModal();
      renderView();
      notify(result.favorited ? '已收藏报告' : '已取消收藏');
    } catch (error) { notify(error.message || '收藏失败', 'error'); }
  }

  function showReportEditModal(reportId) {
    const report = getReport(reportId);
    if (!report) return notify('未找到该报告', 'error');
    if (!canEditReport(report)) return notify('无权修改该报告', 'error');
    const external = report.reportType === 'external';
    const internal = report.reportType === 'internal';
    const adminEditing = isAdminRole();
    // 内部报告可改二级分类（周报/月报/深度报告/其他报告）
    const categoryField = internal
      ? `<div class="form-field"><label for="editCategory">报告分类 <em>*</em></label><select id="editCategory" name="category" required><option value="">请选择分类</option>${Object.entries(categories).map(([key, meta]) => `<option value="${key}" ${report.category === key ? 'selected' : ''}>${meta.label}</option>`).join('')}</select></div>`
      : '';
    // 行政可改报告作者：内部报告从团队成员中选择，外部报告编辑原作者
    const authorField = !adminEditing ? '' : (external || report.reportType === 'roadshow'
      ? `<div class="form-field"><label for="editSourceAuthor">报告作者 <em>*</em></label><input id="editSourceAuthor" name="sourceAuthor" maxlength="100" value="${escapeHTML(report.sourceAuthor || report.author || '')}" placeholder="路演人或外部机构作者"></div>`
      : `<div class="form-field"><label for="editAuthor">报告作者 <em>*</em></label><select id="editAuthor" name="authorId" required>${state.reportAuthors.map(author => `<option value="${escapeHTML(author.id)}" ${author.id === report.authorId ? 'selected' : ''}>${escapeHTML(author.name)} · ${escapeHTML(author.org || roleLabel(author.role))}</option>`).join('')}</select></div>`);
    // 研究主题：所有报告均可改（内部报告置于报告日期右侧，非内部报告与报告作者同行）
    const themeField = `<div class="form-field"><label for="editTheme">研究主题 <em>*</em></label><select id="editTheme" name="theme" required><option value="">请选择主题</option>${Object.entries(themes).map(([key, meta]) => `<option value="${key}" ${report.theme === key ? 'selected' : ''}>${meta.label}</option>`).join('')}</select></div>`;
    const dateField = `<div class="form-field"><label for="editDate">报告日期 <em>*</em></label><input id="editDate" name="reportDate" type="date" value="${escapeHTML(report.reportDate || '')}" required></div>`;
    const tagsField = (cls) => `<div class="${cls}"><label for="editTags">关键词 <span>逗号分隔，最多 8 个</span></label><input id="editTags" name="tags" maxlength="100" value="${escapeHTML((report.tags || []).join('，'))}" placeholder="多个关键词用逗号分隔"></div>`;
    // 首行：内部报告=报告日期+研究主题（关键词下移整行）；非内部报告=报告日期+关键词
    const firstRow = internal
      ? `<div class="form-grid two">${dateField}${themeField}</div>`
      : `<div class="form-grid two">${dateField}${tagsField('form-field')}</div>`;
    // 内部报告关键词单独占一整行（原报告摘要位置）
    const tagsRow = internal ? tagsField('form-field full') : '';
    // 行布局：内部报告=报告分类+报告作者(行政)同行；非内部报告=报告作者(行政)+研究主题同行
    const metaRow = internal
      ? (adminEditing ? `<div class="form-grid two">${categoryField}${authorField}</div>` : categoryField)
      : (adminEditing ? `<div class="form-grid two">${authorField}${themeField}</div>` : `<div class="form-grid two">${themeField}<div class="form-field"></div></div>`);
    openModal(`<section class="modal-card upload-modal">
      ${modalHeader('修改报告信息', report.title)}
      <form id="reportEditForm">
        <div class="modal-body">
          ${firstRow}
          ${metaRow}
          ${tagsRow}
          <div class="form-field"><label for="editSummary">报告摘要</label><textarea id="editSummary" name="summary" maxlength="300" rows="4" placeholder="简要说明报告的研究主题、主要内容或核心观点">${escapeHTML(report.summary || '')}</textarea></div>
          <div class="score-rule-note"><span>提示</span><p>仅修改报告元信息，报告文件本身不可替换。</p></div>
        </div>
        <div class="modal-footer"><button type="button" class="btn btn-ghost" data-close="modal">取消</button><button type="submit" class="btn btn-primary" id="reportEditSubmit">保存修改</button></div>
      </form>
    </section>`, 'modal-wide');
    const form = document.getElementById('reportEditForm');
    form.dataset.reportId = report.id;
    form.addEventListener('submit', submitReportEdit);
  }

  async function submitReportEdit(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const id = form.dataset.reportId;
    const data = new FormData(form);
    const fields = {
      reportDate: String(data.get('reportDate') || '').trim(),
      tags: String(data.get('tags') || ''),
      summary: String(data.get('summary') || '').trim(),
    };
    if (!fields.reportDate) return notify('请选择报告日期', 'error');
    // 内部报告可改二级分类
    {
      const report = getReport(id);
      if (report && report.reportType === 'internal') {
        fields.category = String(data.get('category') || '').trim();
        if (!fields.category) return notify('请选择报告分类', 'error');
      }
    }
    // 所有报告均可改研究主题
    {
      fields.theme = String(data.get('theme') || '').trim();
      if (!fields.theme) return notify('请选择研究主题', 'error');
    }
    // 行政可改报告作者
    if (isAdminRole()) {
      const report = getReport(id);
      if (report && ['external', 'roadshow'].includes(report.reportType)) {
        fields.sourceAuthor = String(data.get('sourceAuthor') || '').trim();
        if (!fields.sourceAuthor) return notify('请填写报告作者', 'error');
      } else {
        fields.authorId = String(data.get('authorId') || '').trim();
        if (!fields.authorId) return notify('请选择报告作者', 'error');
      }
    }
    const submit = document.getElementById('reportEditSubmit');
    submit.disabled = true;
    const original = submit.textContent;
    submit.textContent = '保存中…';
    try {
      await API.updateReport(id, fields);
      await refreshData();
      closeModal();
      notify('报告信息已更新');
      // 修改完成后重新打开详情，便于查看最新信息
      showReportDetail(id);
    } catch (error) {
      console.error(error);
      submit.disabled = false;
      submit.textContent = original;
      notify(error.message || '保存失败', 'error');
    }
  }

  function showRatingModal(reportId) {
    const report = getReport(reportId);
    if (!report || report.reportType === 'external' || !categories[report.category]?.scored) return notify('该报告无需评分', 'error');
    if (!canRateReport(report)) return notify('您不在此报告的打分人员范围内', 'info');
    const existing = userRating(report.id);
    if (existing) {
      // 评分一经提交不得修改，仅展示已提交的评分
      showRatingReadonly(report, existing);
      return;
    }
    const dimensionHTML = config.scoreDimensions.map(dimension => {
      const selected = existing ? Number(existing[dimension.key]) : 0;
      return `<fieldset class="score-fieldset" data-dimension="${dimension.key}">
        <legend><span>${dimension.label}</span><small>${dimension.description}</small></legend>
        <div class="score-options">${Array.from({ length: 10 }, (_, index) => index + 1).map(score => `<label class="score-option ${selected === score ? 'selected' : ''}"><input type="radio" name="${dimension.key}" value="${score}" ${selected === score ? 'checked' : ''} required><span>${score}</span></label>`).join('')}</div>
        <div class="score-scale"><span>较弱</span><span>优秀</span></div>
      </fieldset>`;
    }).join('');
    openModal(`<section class="modal-card score-modal">
      ${modalHeader('报告评分', report.title)}
      <form id="ratingForm">
        <div class="modal-body">
          <div class="rating-report-line">${categoryPill(report.category)}${themePill(report.theme)}<span>${escapeHTML(report.author)} · ${formatDate(report.reportDate)}</span><div class="live-score"><span>综合分</span><strong id="liveScore">${existing ? scoreOf(existing) : '—'}</strong></div></div>
          <div class="score-rule-banner"><svg viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.7"/><path d="M12 11v5M12 8h.01" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg><span>评分提交后不可修改，请确认评分后再提交。</span></div>
          <div class="score-form">${dimensionHTML}</div>
          <div class="comment-field"><label for="ratingComment">意见和建议 <span>选填</span></label><textarea id="ratingComment" name="comment" maxlength="500" placeholder="请输入你对报告的观点、启发或改进建议……">${escapeHTML(existing?.comment || '')}</textarea><small><span id="commentCount">${(existing?.comment || '').length}</span> / 500</small></div>
        </div>
        <div class="modal-footer"><button type="button" class="btn btn-ghost" data-close="modal">取消</button><button type="submit" class="btn btn-primary">提交评分</button></div>
      </form>
    </section>`, 'modal-wide');

    const form = document.getElementById('ratingForm');
    form.dataset.reportId = report.id;
    form.addEventListener('change', updateLiveScore);
    form.addEventListener('submit', submitRating);
    document.getElementById('ratingComment').addEventListener('input', event => { document.getElementById('commentCount').textContent = event.target.value.length; });
  }

  function showRatingReadonly(report, existing) {
    const dimensionHTML = config.scoreDimensions.map(dimension => {
      const selected = Number(existing[dimension.key]);
      return `<fieldset class="score-fieldset readonly" data-dimension="${dimension.key}">
        <legend><span>${dimension.label}</span><small>${dimension.description}</small></legend>
        <div class="score-options">${Array.from({ length: 10 }, (_, index) => index + 1).map(score => `<label class="score-option ${selected === score ? 'selected' : ''}"><span>${score}</span></label>`).join('')}</div>
        <div class="score-scale"><span>较弱</span><span>优秀</span></div>
      </fieldset>`;
    }).join('');
    openModal(`<section class="modal-card score-modal">
      ${modalHeader('我的评分', report.title)}
      <div class="modal-body">
        <div class="rating-report-line">${categoryPill(report.category)}${themePill(report.theme)}<span>${escapeHTML(report.author)} · ${formatDate(report.reportDate)}</span><div class="live-score"><span>综合分</span><strong>${scoreOf(existing)}</strong></div></div>
        <div class="rating-locked-banner"><svg viewBox="0 0 24 24" fill="none"><rect x="4" y="10" width="16" height="11" rx="2" stroke="currentColor" stroke-width="1.8"/><path d="M8 10V7a4 4 0 0 1 8 0v3" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg><span>评分已提交，提交后不可修改。</span></div>
        <div class="score-form">${dimensionHTML}</div>
        <div class="comment-field readonly"><label>意见和建议</label><p class="rating-comment-view">${escapeHTML(existing.comment || '未填写意见建议')}</p></div>
      </div>
      <div class="modal-footer"><button type="button" class="btn btn-primary" data-close="modal">关闭</button></div>
    </section>`, 'modal-wide');
  }

  function updateLiveScore(event) {
    if (event.target.matches('input[type="radio"]')) {
      event.target.closest('.score-options').querySelectorAll('.score-option').forEach(option => option.classList.toggle('selected', option.querySelector('input').checked));
    }
    const form = document.getElementById('ratingForm');
    if (!form) return;
    const values = config.scoreDimensions.map(dimension => Number(new FormData(form).get(dimension.key))).filter(Boolean);
    document.getElementById('liveScore').textContent = values.length === 3 ? (values.reduce((a, b) => a + b, 0) / 3).toFixed(1) : '—';
  }

  async function submitRating(event) {
    event.preventDefault();
    if (!canRate()) return notify('当前账号无评分权限', 'error');
    const form = event.currentTarget;
    const data = new FormData(form);
    const values = Object.fromEntries(config.scoreDimensions.map(dimension => [dimension.key, Number(data.get(dimension.key))]));
    if (Object.values(values).some(value => !Number.isInteger(value) || value < 1 || value > 10)) return notify('请完成三个维度的评分', 'error');
    const submitBtn = form.querySelector('button[type="submit"]');
    submitBtn.disabled = true;
    submitBtn.textContent = '提交中…';
    try {
      await API.submitRating({
        reportId: form.dataset.reportId,
        ...values,
        comment: String(data.get('comment') || '').trim()
      });
      await refreshData();
      closeModal();
      notify('评分提交成功');
      renderView();
    } catch (error) {
      submitBtn.disabled = false;
      submitBtn.textContent = '提交评分';
      notify(error.message || '评分提交失败', 'error');
    }
  }

  function showResultDetail(reportId) {
    const report = getReport(reportId);
    if (!report) return notify('未找到该报告', 'error');
    const allRows = state.ratings.filter(row => row.reportId === reportId).sort((a, b) => new Date(b.updatedAt) - new Date(a.updatedAt));
    const stats = reportRatingStats(reportId);
    const feedbackRows = visibleFeedback(reportId).sort((a, b) => {
      if (a.isMine !== b.isMine) return a.isMine ? -1 : 1;
      return new Date(b.updatedAt) - new Date(a.updatedAt);
    });
    const otherFeedbackCount = feedbackRows.filter(row => !row.isMine).length;
    const showOwnReportTotal = !isLeader() && !isAdminRole() && canViewReportTotal(report);
    const adminView = isAdminRole();
    let details = '';
    if (isLeader()) {
      details = allRows.length ? allRows.map(row => detailRatingCard(row, true)).join('') : emptyState('暂无评分明细', '团队成员提交评分后会显示在这里。');
    } else if (adminView) {
      details = '<div class="detail-loading">正在加载评分人员名单…</div>';
    } else {
      let anonymousIndex = 0;
      details = feedbackRows.length ? feedbackRows.map(row => feedbackCard(row, row.isMine ? 0 : ++anonymousIndex)).join('') : emptyState('暂无意见建议', '参与人数会正常统计；未填写意见建议的评分不会显示在这里。');
      if (canRate() && feedbackRows.some(row => row.isMine) && !otherFeedbackCount) {
        details += '<div class="feedback-scope-note">本报告暂无其他成员填写的意见建议；其他报告的意见不会混合显示在这里。</div>';
      }
    }
    openModal(`<section class="modal-card result-detail-modal">
      ${modalHeader('评分结果', report.title)}
      <div class="modal-body">
        ${isLeader() ? `<div class="result-detail-summary"><div><span>综合评分</span><strong>${stats.overall ?? '—'}</strong><small>${stats.count} 人参与</small></div>${config.scoreDimensions.map(dim => `<div><span>${dim.label}</span><strong>${stats[dim.key] ?? '—'}</strong></div>`).join('')}</div>` : `<div class="anonymous-progress-summary ${showOwnReportTotal ? 'with-total' : ''}">${showOwnReportTotal ? `<div><span>汇总平均分</span><strong>${stats.overall ?? '—'}</strong><small>${stats.count} 人参与</small></div>` : ''}<div><span>已评分</span><strong>${stats.count}</strong><small>人</small></div><div><span>未评分</span><strong>${stats.pending}</strong><small>人</small></div><p>${adminView ? '可查看具体哪些人员已评分、哪些未评分，但不展示具体得分。' : showOwnReportTotal ? '本人报告仅展示汇总平均分，不展示各维度得分、评分人身份或个人评分明细。' : '评分内容已匿名处理，不展示评分人身份及任何具体得分。'}</p></div>`}
        <div class="detail-list-header"><h3>${isLeader() ? '全部人员明细' : adminView ? '评分人员名单' : '意见建议'}</h3><span>${isLeader() ? `${allRows.length} 条记录` : adminView ? `${stats.count} 已评 · ${stats.pending} 未评` : `${feedbackRows.length} 条意见 · ${otherFeedbackCount} 条他人意见`}</span></div>
        <div class="rating-detail-list" id="scoringStatusList">${details}</div>
      </div>
      <div class="modal-footer"><button class="btn btn-ghost" data-close="modal">关闭</button>${isLeader() ? `<button class="btn btn-secondary" data-action="export-result-detail-excel" data-id="${report.id}"><svg viewBox="0 0 24 24" fill="none" width="16" height="16"><path d="M12 3v12m0 0 4-4m-4 4-4-4M5 21h14" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>导出 Excel</button>` : ''}${canRateReport(report) ? (userRating(report.id) ? `<button class="btn btn-primary" data-action="view-my-rating" data-id="${report.id}">查看我的评分</button>` : `<button class="btn btn-primary" data-action="open-rating" data-id="${report.id}">为报告评分</button>`) : ''}</div>
    </section>`, 'modal-wide');
    if (adminView) loadScoringStatus(reportId);
  }

  function feedbackCard(feedback, index) {
    const label = feedback.isMine ? '我的意见' : `匿名意见 ${String(index).padStart(2, '0')}`;
    const tag = feedback.isMine ? '本人' : '已匿名';
    return `<article class="rating-detail-card anonymous-feedback-card">
      <div class="rating-detail-person"><div class="account-avatar ${feedback.isMine ? '' : 'muted'}">${feedback.isMine ? '我' : '匿'}</div><div><strong>${label}</strong><span>${formatDate(feedback.updatedAt)}</span></div><span class="anonymous-tag ${feedback.isMine ? 'mine' : ''}">${tag}</span></div>
      <div class="rating-comment"><span>意见和建议</span><p>${escapeHTML(feedback.comment)}</p></div>
    </article>`;
  }

  async function loadScoringStatus(reportId) {
    const listEl = document.getElementById('scoringStatusList');
    const countEl = document.getElementById('scoringStatusCount');
    try {
      const data = await API.scoringStatus(reportId);
      const done = data.scorers.filter(s => s.scored);
      const pending = data.scorers.filter(s => !s.scored);
      if (countEl) countEl.textContent = `${data.done} / ${data.total} 人已评分`;
      if (!listEl) return;
      if (!data.scorers.length) {
        listEl.innerHTML = emptyState('暂无应评分人员', '尚未配置可评分的团队成员。');
        return;
      }
      listEl.innerHTML = `
        ${done.length ? `
          <div class="scoring-group"><div class="scoring-group-head"><span class="scoring-group-tag done">已评分</span><small>${done.length} 人</small></div>
          ${done.map(s => scorerCard(s, true)).join('')}
          </div>` : ''}
        ${pending.length ? `
          <div class="scoring-group"><div class="scoring-group-head"><span class="scoring-group-tag pending">未评分</span><small>${pending.length} 人</small></div>
          ${pending.map(s => scorerCard(s, false)).join('')}
          </div>` : ''}
      `;
    } catch (error) {
      if (listEl) listEl.innerHTML = `<div class="detail-permission"><svg viewBox="0 0 24 24" fill="none"><path d="M12 8v4M12 16h.01" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.8"/></svg><div><strong>评分进度加载失败</strong><p>${escapeHTML(error.message || '请稍后重试')}</p></div></div>`;
    }
  }

  function scorerCard(scorer, scored) {
    const avatar = `<div class="account-avatar ${scored ? '' : 'muted'}">${escapeHTML(initials(scorer.name))}</div>`;
    if (scored) {
      return `<article class="rating-detail-card scorer-card">
        <div class="rating-detail-person">${avatar}<div><strong>${escapeHTML(scorer.name)}</strong><span>${escapeHTML(scorer.org)} · ${formatDate(scorer.updatedAt)}</span></div>${scorer.score != null ? `<b>${scorer.score}</b>` : ''}</div>
      </article>`;
    }
    return `<article class="rating-detail-card scorer-card scorer-pending">
      <div class="rating-detail-person">${avatar}<div><strong>${escapeHTML(scorer.name)}</strong><span>${escapeHTML(scorer.org)} · 待评分</span></div><span class="scorer-pending-tag">未评</span></div>
    </article>`;
  }

  function detailRatingCard(rating, showName) {
    const displayName = showName ? (rating.userName || rating.userId) : '我的评分';
    return `<article class="rating-detail-card">
      <div class="rating-detail-person"><div class="account-avatar">${escapeHTML(initials(displayName))}</div><div><strong>${escapeHTML(displayName)}</strong><span>${formatDate(rating.updatedAt)}</span></div><b>${scoreOf(rating)}</b></div>
      <div class="rating-detail-scores">${config.scoreDimensions.map(dim => `<span>${dim.label}<strong>${rating[dim.key]}</strong></span>`).join('')}</div>
      <div class="rating-comment"><span>意见和建议</span><p>${escapeHTML(rating.comment || '未填写意见和建议')}</p></div>
    </article>`;
  }

  function showChangePasswordModal() {
    openModal(`<section class="modal-card">
      ${modalHeader('修改密码', state.currentUser.name)}
      <form id="passwordForm">
        <div class="modal-body">
          <div class="form-field">
            <label for="oldPassword">当前密码</label>
            <input id="oldPassword" name="oldPassword" type="password" autocomplete="current-password" placeholder="请输入当前密码" required>
          </div>
          <div class="form-field" style="margin-top: 16px;">
            <label for="newPassword">新密码</label>
            <input id="newPassword" name="newPassword" type="password" autocomplete="new-password" placeholder="请输入新密码（至少 6 位）" minlength="6" required>
          </div>
          <div class="form-field" style="margin-top: 16px;">
            <label for="confirmPassword">确认新密码</label>
            <input id="confirmPassword" name="confirmPassword" type="password" autocomplete="new-password" placeholder="请再次输入新密码" required>
          </div>
        </div>
        <div class="modal-footer">
          <button type="button" class="btn btn-ghost" data-close="modal">取消</button>
          <button type="submit" class="btn btn-primary" id="passwordSubmit">确认修改</button>
        </div>
      </form>
    </section>`);
    const form = document.getElementById('passwordForm');
    form.addEventListener('submit', submitPasswordChange);
  }

  async function submitPasswordChange(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const oldPassword = String(data.get('oldPassword'));
    const newPassword = String(data.get('newPassword'));
    const confirmPassword = String(data.get('confirmPassword'));
    if (newPassword.length < 6) return notify('新密码至少需要 6 位', 'error');
    if (newPassword !== confirmPassword) return notify('两次输入的新密码不一致', 'error');
    if (newPassword === oldPassword) return notify('新密码不能与当前密码相同', 'error');
    const submit = document.getElementById('passwordSubmit');
    submit.disabled = true;
    try {
      await API.changePassword(oldPassword, newPassword);
      closeModal();
      notify('密码修改成功');
    } catch (error) {
      submit.disabled = false;
      notify(error.message || '密码修改失败', 'error');
    }
  }

  // ----------------------------------------------------------------------- //
  // 单篇报告上传
  // ----------------------------------------------------------------------- //
  function showUploadModal(preset = null) {
    const today = new Date().toISOString().slice(0, 10);
    // 路演安排一键上传（带 roadshowScheduleId 预填）时锁定为路演报告：
    // 防止误改类型导致报告与路演安排的关联丢失。
    const canChooseType = state.view === 'dashboard' && !preset?.roadshowScheduleId;
    const initialType = preset?.reportType || (state.view === 'external-reports' ? 'external' : state.view === 'research-reports' ? 'research_visit' : state.view === 'roadshow-reports' ? 'roadshow' : 'internal');
    state.uploadFiles = [];
    state.uploadPreset = preset;
    const defaultDate = preset?.reportDate || today;
    // 行政角色选择报告作者：作者列表随所属部门筛选；所属部门已移到报告作者之前。
    const authorField = isAdminRole() ? `
      <div class="form-field"><label for="uploadAuthor">报告作者 <em>*</em></label><select id="uploadAuthor" name="authorId" required><option value="">请选择报告作者</option></select></div>` : '';
    const categoryField = ['external', 'research_visit', 'roadshow'].includes(initialType) && !canChooseType ? '' : `<div class="form-field" id="uploadCategoryField" ${initialType !== 'internal' ? 'hidden' : ''}><label for="uploadCategory">报告分类 <em>*</em></label><select id="uploadCategory" name="category" ${initialType !== 'internal' ? '' : 'required'}><option value="">请选择分类</option>${Object.entries(categories).map(([key, meta]) => `<option value="${key}">${meta.label}${meta.scored ? '（内部报告需评分）' : ''}</option>`).join('')}</select></div>`;
    const typeField = canChooseType
      ? `<div class="form-field full"><label for="uploadReportType">报告类型 <em>*</em></label><select id="uploadReportType" name="reportType" required><option value="internal">内部报告</option><option value="external">外部报告</option><option value="research_visit">调研报告</option><option value="roadshow">路演报告</option></select></div>`
      : `<div class="form-field full"><label>报告类型</label><div class="upload-scope-lock"><strong>${reportTypeLabel(initialType)}</strong><span>当前入口仅允许上传对应类型的报告。</span></div><input id="uploadReportType" name="reportType" type="hidden" value="${initialType}"></div>`;
    // 打分部门多选：仅深度报告显示（月报打分已关闭），默认两部门均打分；不影响报告可见性。
    const scoringOrgField = `<div class="form-field full" id="scoringOrgField" hidden><label>打分人员 <em>*</em></label><div class="scoring-org-group"><label class="scoring-org-option"><input type="checkbox" name="scoringOrgs" value="资产配置部" checked><span>资产配置部</span></label><label class="scoring-org-option"><input type="checkbox" name="scoringOrgs" value="固收中心" checked><span>固收中心</span></label></div><small>默认两部门均打分，可调整为单部门；报告仍对所有人可见，仅影响评分资格。</small></div>`;
    openModal(`<section class="modal-card upload-modal">
      ${modalHeader('上传报告', `上传${reportTypeLabel(initialType)}`)}
      <form id="uploadForm" autocomplete="off">
        <div class="modal-body">
          <div class="upload-notice" id="uploadDropZone"><svg viewBox="0 0 24 24" fill="none"><path d="M12 3v12M7 10l5 5 5-5M5 20h14" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg><div><strong>选择或拖入单篇报告</strong><p>支持 PDF、PPT/PPTX、Word、Excel，单篇报告不超过 100MB</p></div><input id="reportFile" name="file" type="file" accept=".pdf,.ppt,.pptx,.doc,.docx,.xls,.xlsx"></div>
          <div class="upload-file-list" id="uploadFileList"></div>
          <div class="form-grid two">
            ${typeField}
            ${categoryField}
            <div class="form-field"><label for="uploadTheme">研究主题 <em>*</em></label><select id="uploadTheme" name="theme" required><option value="">请选择主题</option>${Object.entries(themes).map(([key, meta]) => `<option value="${key}">${meta.label}</option>`).join('')}</select></div>
            <div class="form-field"><label for="uploadOrg">所属部门 <em>*</em></label><select id="uploadOrg" name="org" required><option value="资产配置部" ${state.currentUser.org === '资产配置部' ? 'selected' : ''}>资产配置部</option><option value="固收中心" ${state.currentUser.org === '固收中心' ? 'selected' : ''}>固收中心</option></select></div>
            ${authorField}
            <div class="form-field external-only-field" id="externalSourceAuthorField" ${['external', 'roadshow'].includes(initialType) ? '' : 'hidden'}><label for="uploadSourceAuthor">报告作者 <em>*</em></label><input id="uploadSourceAuthor" name="sourceAuthor" maxlength="100" autocomplete="off" placeholder="外部报告原作者，或路演报告的路演人"></div>
            <div class="form-field external-only-field" id="externalSourceInstitutionField" ${['external', 'roadshow'].includes(initialType) ? '' : 'hidden'}><label for="uploadSourceInstitution">报告机构 <em>*</em></label><input id="uploadSourceInstitution" name="sourceInstitution" maxlength="120" autocomplete="off" placeholder="例如：兴业证券、中信证券"></div>
            ${scoringOrgField}
            <div class="form-field"><label for="uploadDate">报告日期 <em>*</em></label><input id="uploadDate" name="reportDate" type="date" value="${escapeHTML(defaultDate)}" autocomplete="off" required></div>
            <div class="form-field"><label for="uploadTags">关键词</label><input id="uploadTags" name="tags" maxlength="100" autocomplete="off" placeholder="多个关键词用逗号分隔"></div>
          </div>
          <div class="form-field" id="recommendationField" ${initialType === 'external' ? '' : 'hidden'}><label for="uploadRecommendation">推荐语 <span>选填，将展示在摘要上方</span></label><textarea id="uploadRecommendation" name="recommendation" maxlength="300" autocomplete="off" placeholder="为什么推荐团队阅读这份报告？"></textarea></div>
          <div class="form-field"><label for="uploadSummary">报告摘要 <span>应用于本篇报告</span></label><textarea id="uploadSummary" name="summary" maxlength="300" autocomplete="off" placeholder="简要说明报告的研究主题、主要内容或核心观点"></textarea></div>
          <button type="button" class="btn btn-secondary ai-complete-btn" id="aiCompleteBtn" data-action="ai-complete" disabled>
            <svg viewBox="0 0 24 24" fill="none"><path d="M12 2l2.4 6.6L21 11l-6.6 2.4L12 20l-2.4-6.6L3 11l6.6-2.4L12 2z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>
            <span>智能补全</span>
          </button>
          <div class="score-rule-note" id="scoreRuleNote"><span>评分规则</span><p>选择"深度报告"后，系统将开放投资启发性、研究深度、实用性三个等权评分维度，并可选择打分部门。</p></div>
        </div>
        <div class="modal-footer"><button type="button" class="btn btn-ghost" data-close="modal">取消</button><button type="submit" class="btn btn-primary" id="uploadSubmit">确认上传</button></div>
      </form>
    </section>`, 'modal-wide');
    const form = document.getElementById('uploadForm');
    form.addEventListener('submit', submitUpload);
    const fileInput = document.getElementById('reportFile');
    fileInput.addEventListener('change', handleFileSelection);
    const dropZone = document.getElementById('uploadDropZone');
    dropZone.addEventListener('dragover', event => { event.preventDefault(); dropZone.classList.add('drag-over'); });
    dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
    dropZone.addEventListener('drop', event => {
      event.preventDefault();
      dropZone.classList.remove('drag-over');
      addFiles(Array.from(event.dataTransfer.files));
    });
    document.getElementById('uploadReportType')?.addEventListener('change', event => updateUploadTypeFields(event.target.value));
    document.getElementById('uploadCategory')?.addEventListener('change', () => updateUploadScoringVisibility());
    document.getElementById('uploadOrg')?.addEventListener('change', populateUploadAuthors);
    updateUploadTypeFields(initialType);
    populateUploadAuthors();
    // 路演安排一键上传：预填报告作者（路演人）与报告机构（路演机构）
    if (preset?.sourceAuthor) {
      const input = document.getElementById('uploadSourceAuthor');
      if (input) input.value = preset.sourceAuthor;
    }
    if (preset?.sourceInstitution) {
      const input = document.getElementById('uploadSourceInstitution');
      if (input) input.value = preset.sourceInstitution;
    }
    // 路演安排一键上传：行政按路演人预选署名作者（按姓名匹配团队成员）
    if (preset?.authorName && isAdminRole()) {
      const match = state.reportAuthors.find(author => author.name === preset.authorName);
      const orgSelect = document.getElementById('uploadOrg');
      const authorSelect = document.getElementById('uploadAuthor');
      if (match && orgSelect && authorSelect) {
        if (match.org) orgSelect.value = match.org;
        authorSelect.dataset.preserveId = match.id;
        populateUploadAuthors();
      }
    }
  }

  // 按当前所属部门筛选可选择的报告作者（仅行政角色的上传表单存在该下拉）。
  function populateUploadAuthors() {
    const orgSelect = document.getElementById('uploadOrg');
    const authorSelect = document.getElementById('uploadAuthor');
    if (!orgSelect || !authorSelect) return;
    const org = orgSelect.value;
    const list = state.reportAuthors.filter(author => author.org === org);
    const preserve = authorSelect.dataset.preserveId || '';
    authorSelect.innerHTML = '<option value="">请选择报告作者</option>' + list.map(author => `<option value="${escapeHTML(author.id)}" ${author.id === preserve ? 'selected' : ''}>${escapeHTML(author.name)} · ${escapeHTML(author.org || roleLabel(author.role))}</option>`).join('');
  }

  // 打分部门多选框仅在「内部报告 + 深度报告」时显示。
  function updateUploadScoringVisibility() {
    const reportType = document.getElementById('uploadReportType')?.value || 'internal';
    const category = document.getElementById('uploadCategory')?.value || '';
    const show = reportType === 'internal' && categories[category]?.scored;
    document.getElementById('scoringOrgField')?.toggleAttribute('hidden', !show);
    document.getElementById('scoreRuleNote')?.classList.toggle('visible', Boolean(show));
  }

  function updateUploadTypeFields(reportType) {
    const external = reportType === 'external';
    // 外部报告与路演报告都需要填写报告作者/报告机构
    const extLike = external || reportType === 'roadshow';
    const internal = reportType === 'internal';
    const categoryField = document.getElementById('uploadCategoryField');
    const category = document.getElementById('uploadCategory');
    if (categoryField) categoryField.hidden = !internal;
    if (category) category.required = internal;
    document.getElementById('recommendationField')?.toggleAttribute('hidden', !external);
    document.getElementById('externalSourceAuthorField')?.toggleAttribute('hidden', !extLike);
    document.getElementById('externalSourceInstitutionField')?.toggleAttribute('hidden', !extLike);
    updateUploadScoringVisibility();
  }

  function handleFileSelection(event) {
    addFiles(Array.from(event.target.files));
    event.target.value = '';
  }

  function addFiles(files) {
    const allowed = ['.pdf', '.ppt', '.pptx', '.doc', '.docx', '.xls', '.xlsx'];
    if (files.length > 1) return notify('一次只能上传一篇报告，请仅选择一个文件', 'error');
    const file = files[0];
    if (!file) return;
    const ext = '.' + (file.name.split('.').pop() || '').toLowerCase();
    if (!allowed.includes(ext)) return notify(`文件 ${file.name} 格式不支持`, 'error');
    if (file.size > 100 * 1024 * 1024) return notify(`文件 ${file.name} 超过 100MB`, 'error');
    if (state.uploadFiles.length) return notify('当前已选择一篇报告，请先移除后再选择', 'info');
    state.uploadFiles.push({ file, title: (state.uploadPreset && state.uploadPreset.title) || file.name.replace(/\.[^.]+$/, '') });
    renderUploadFileList();
    notify('已选择 1 篇报告');
  }

  function removeUploadFile(name) {
    state.uploadFiles = state.uploadFiles.filter(f => f.file.name !== name);
    renderUploadFileList();
  }

  function renderUploadFileList() {
    const list = document.getElementById('uploadFileList');
    if (!list) return;
    const aiBtn = document.getElementById('aiCompleteBtn');
    if (aiBtn) aiBtn.disabled = !state.uploadFiles.length;
    if (!state.uploadFiles.length) { list.hidden = true; list.innerHTML = ''; return; }
    list.hidden = false;
    list.innerHTML = `<div class="upload-file-list-head"><span>已选择报告</span><span>标题可编辑</span></div>` +
      state.uploadFiles.map(item => `<div class="upload-file-row">
        <div class="detail-file-icon">${escapeHTML(item.file.name.split('.').pop().toUpperCase())}</div>
        <div class="upload-file-row-main">
          <input class="upload-title-input" data-name="${escapeHTML(item.file.name)}" value="${escapeHTML(item.title)}" autocomplete="off" placeholder="报告标题">
          <span>${escapeHTML(item.file.name)} · ${formatBytes(item.file.size)}</span>
        </div>
        <button type="button" class="icon-button upload-file-remove" data-action="remove-upload-file" data-name="${escapeHTML(item.file.name)}" title="移除"><svg viewBox="0 0 24 24" fill="none"><path d="m6 6 12 12M18 6 6 18" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg></button>
      </div>`).join('');
    list.querySelectorAll('.upload-title-input').forEach(input => {
      input.addEventListener('input', event => {
        const item = state.uploadFiles.find(f => f.file.name === event.target.dataset.name);
        if (item) item.title = event.target.value;
      });
    });
  }

  function formatBytes(bytes) {
    if (!Number(bytes)) return '0 KB';
    const units = ['B', 'KB', 'MB', 'GB'];
    const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
    return `${(bytes / Math.pow(1024, index)).toFixed(index > 1 ? 1 : 0)} ${units[index]}`;
  }

  async function submitUpload(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    if (!state.uploadFiles.length) return notify('请选择要上传的报告文件', 'error');
    const theme = String(data.get('theme'));
    const reportType = String(data.get('reportType') || 'internal');
    const category = reportType === 'external' ? 'other' : String(data.get('category') || '');
    const org = String(data.get('org'));
    const reportDate = String(data.get('reportDate'));
    const summary = String(data.get('summary') || '').trim();
    const recommendation = String(data.get('recommendation') || '').trim();
    const sourceAuthor = String(data.get('sourceAuthor') || '').trim();
    const sourceInstitution = String(data.get('sourceInstitution') || '').trim();
    const tags = String(data.get('tags') || '');
    const authorId = isAdminRole() ? String(data.get('authorId') || '') : '';
    // 打分部门：仅深度报告需要，至少选择一个部门。
    const scoringOrgs = data.getAll('scoringOrgs').map(v => String(v)).filter(Boolean);
    if (reportType === 'internal' && !category) return notify('请选择报告分类', 'error');
    if (!theme) return notify('请选择研究主题', 'error');
    if (!org) return notify('请选择所属部门', 'error');
    if (!reportDate) return notify('请选择报告日期', 'error');
    if (isAdminRole() && !authorId) return notify('请选择报告作者', 'error');
    if (['external', 'roadshow'].includes(reportType) && !sourceAuthor) return notify('请填写报告作者', 'error');
    if (['external', 'roadshow'].includes(reportType) && !sourceInstitution) return notify('请填写报告机构', 'error');
    if (reportType === 'internal' && ['monthly', 'deep'].includes(category) && !scoringOrgs.length) return notify('请至少选择一个打分部门', 'error');

    const titles = {};
    state.uploadFiles.forEach(item => { titles[item.file.name] = item.title.trim() || item.file.name.replace(/\.[^.]+$/, ''); });

    const formData = new FormData();
    formData.append('file', state.uploadFiles[0].file, state.uploadFiles[0].file.name);
    const meta = { reportType, category, theme, org, reportDate, summary, recommendation, sourceAuthor, sourceInstitution, tags, titles, authorId, scoringOrgs };
    // 路演安排一键上传：携带关联 ID，便于回溯报告对应的路演日程
    if (state.uploadPreset && state.uploadPreset.roadshowScheduleId && reportType === 'roadshow') {
      meta.roadshowScheduleId = state.uploadPreset.roadshowScheduleId;
    }
    formData.append('meta', JSON.stringify(meta));

    const submit = document.getElementById('uploadSubmit');
    submit.disabled = true;
    const original = submit.textContent;
    submit.textContent = '正在上传…';
    try {
      const result = await uploadReports(formData, reportType);
      await refreshData();
      closeModal();
      notify(`成功上传 ${result.count} 份报告`);
      // 路演报告自动匹配成功时弹出结果卡片（规则或大模型匹配），可一键调整
      const uploaded = (result.reports || [])[0];
      const autoMatched = reportType === 'roadshow' && uploaded && uploaded.roadshowScheduleId
        && ['rule', 'llm'].includes(uploaded.roadshowMatchedBy);
      state.uploadPreset = null;
      state.filters = { category: reportType === 'internal' ? category : '', theme: reportType === 'internal' ? theme : '', org: '', score: '', query: '' };
      navigate(reportType === 'external' ? 'external-reports' : reportType === 'research_visit' ? 'research-reports' : reportType === 'roadshow' ? 'roadshow-reports' : 'reports');
      if (autoMatched) {
        const saved = getReport(uploaded.id);
        if (saved && saved.roadshowSchedule) showAutoMatchResultModal(saved);
        else notify('已自动匹配到对应路演安排，可在报告详情中调整');
      }
    } catch (error) {
      console.error(error);
      submit.disabled = false;
      submit.textContent = original;
      notify(error.message || '文件上传失败', 'error');
    }
  }

  async function handleAiComplete() {
    if (!state.uploadFiles.length) return notify('请先选择报告文件', 'error');
    const btn = document.getElementById('aiCompleteBtn');
    if (!btn || btn.disabled) return;
    const original = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `<svg viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.8" stroke-dasharray="40" stroke-dashoffset="20"><animateTransform attributeName="transform" type="rotate" from="0 12 12" to="360 12 12" dur="0.8s" repeatCount="indefinite"/></circle></svg><span>智能补全中…</span>`;
    try {
      const formData = new FormData();
      formData.append('file', state.uploadFiles[0].file, state.uploadFiles[0].file.name);
      formData.append('reportType', document.getElementById('uploadReportType')?.value || 'internal');
      const result = await API.aiComplete(formData);
      if (result.error) {
        notify(result.error, 'error');
      } else {
        const tagsInput = document.getElementById('uploadTags');
        const summaryInput = document.getElementById('uploadSummary');
        if (result.tags && result.tags.length) {
          const existing = (tagsInput.value || '').trim();
          const existingTags = existing ? existing.split(/[,，]/).map(t => t.trim()).filter(Boolean) : [];
          const merged = [...new Set([...existingTags, ...result.tags])].slice(0, 8);
          tagsInput.value = merged.join('，');
        }
        if (result.summary) summaryInput.value = result.summary;
        if (result.author && document.getElementById('uploadSourceAuthor')) document.getElementById('uploadSourceAuthor').value = result.author;
        if (result.institution && document.getElementById('uploadSourceInstitution')) document.getElementById('uploadSourceInstitution').value = result.institution;
        notify('智能补全完成，请检查并按需修改');
      }
    } catch (error) {
      notify(error.message || '智能补全失败，请手动填写', 'error');
    } finally {
      btn.disabled = false;
      btn.innerHTML = original;
    }
  }

  async function downloadReport(reportId) {
    const report = getReport(reportId);
    if (!report) return notify('未找到该报告', 'error');
     await recordExternalView(report);
    try {
      const response = await apiFetch(`/api/reports/${reportId}/file`, { credentials: 'same-origin' });
      if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.error || '下载失败');
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = report.fileName || report.title;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      notify('已开始下载报告');
    } catch (error) {
      console.error(error);
      notify(error.message || '报告文件不可用', 'error');
    }
  }

  async function previewReport(reportId) {
    const report = getReport(reportId);
    if (!report) return notify('未找到该报告', 'error');
    await recordExternalView(report);
    const fileType = (report.fileType || '').toUpperCase();
    const isPdf = fileType === 'PDF';

    openModal(`<section class="modal-card">
      ${modalHeader('在线查看', report.title)}
      <div class="modal-body" id="previewBody"><div class="preview-split"><aside class="preview-summary-pane" id="previewSummaryPane" data-report-id="${report.id}" hidden></aside><div class="preview-split-resizer" id="previewSplitResizer" title="拖动调节摘要与原文比例" hidden><button type="button" class="preview-split-toggle" data-action="toggle-preview-summary" title="隐藏AI摘要栏" aria-label="隐藏AI摘要栏"><svg viewBox="0 0 24 24" fill="none"><path d="M15 6l-6 6 6 6" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg></button></div><div class="preview-doc" id="previewDoc"><div class="preview-notice">${isPdf ? '正在加载文件，请稍候……' : '正在转换为 PDF 预览格式，请稍候……'}</div></div></div><button type="button" class="preview-edge-toggle" id="previewEdgeToggle" data-action="toggle-preview-summary" title="展开AI摘要栏" aria-label="展开AI摘要栏"><svg viewBox="0 0 24 24" fill="none"><path d="M9 6l6 6-6 6" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg></button></div>
      <div class="modal-footer">
        <button class="btn btn-ghost" data-action="toggle-preview-summary" id="previewSummaryBtn"><svg viewBox="0 0 24 24" fill="none"><path d="M4 6h16M4 12h10M4 18h13" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>AI 摘要</button>
        <button class="btn btn-ghost" data-action="toggle-preview-fullscreen" id="previewFullscreenBtn"><svg viewBox="0 0 24 24" fill="none"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>全屏</button>
        <button class="btn btn-ghost" data-close="modal">关闭</button>
        <button class="btn btn-primary" data-action="download" data-id="${report.id}"><svg viewBox="0 0 24 24" fill="none"><path d="M12 3v12M7 10l5 5 5-5M5 20h14" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>下载报告</button>
      </div>
    </section>`, 'modal-preview');
    // 在线查看默认隐藏 AI 摘要栏；仅详情弹窗"AI 摘要"按钮或预览内手动切换时开启

    try {
      let pdfUrl, revokeUrl = false;
      if (isPdf) {
        const response = await apiFetch(`/api/reports/${reportId}/file`, { credentials: 'same-origin' });
        if (!response.ok) throw new Error('报告文件不可用');
        const blob = await response.blob();
        pdfUrl = URL.createObjectURL(blob);
        revokeUrl = true;
      } else {
        pdfUrl = await fetchConvertedPdfUrl(report);
        revokeUrl = true;
      }
      const body = document.getElementById('previewBody');
      const doc = document.getElementById('previewDoc');
      if (!body || !doc) { if (revokeUrl && pdfUrl) URL.revokeObjectURL(pdfUrl); return; }
      if (pdfUrl) {
        doc.innerHTML = `<iframe src="${pdfUrl}" title="${escapeHTML(report.title)}"></iframe>`;
        if (revokeUrl) {
          const observer = new MutationObserver(() => {
            if (!document.getElementById('previewBody')) { URL.revokeObjectURL(pdfUrl); observer.disconnect(); }
          });
          observer.observe(els.modalLayer, { childList: true });
        }
      } else {
        doc.innerHTML = `<div class="preview-notice">预览不可用，请下载后查看。</div>`;
      }
    } catch (error) {
      console.error(error);
      const doc = document.getElementById('previewDoc');
      if (doc) doc.innerHTML = `<div class="preview-notice">预览失败：${escapeHTML(error.message || '报告文件不可用')}。请下载后查看。</div>`;
      notify('预览失败，请尝试下载后查看', 'error');
    }
  }

  function previewFullscreenActive() {
    const body = document.getElementById('previewBody');
    return !!body && (document.fullscreenElement === body || body.classList.contains('preview-pseudo-fullscreen'));
  }

  function addPreviewExitButton(body) {
    if (body.querySelector('.preview-exit-fullscreen')) return;
    body.insertAdjacentHTML('beforeend', '<button type="button" class="btn btn-ghost preview-exit-fullscreen" data-action="exit-preview-fullscreen"><svg viewBox="0 0 24 24" fill="none"><path d="M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>退出全屏</button>');
  }

  function updatePreviewFullscreenButton() {
    const btn = document.getElementById('previewFullscreenBtn');
    if (btn) btn.lastChild.textContent = previewFullscreenActive() ? '退出全屏' : '全屏';
  }

  function togglePreviewFullscreen() {
    if (previewFullscreenActive()) return exitPreviewFullscreen();
    const body = document.getElementById('previewBody');
    if (!body) return notify('预览窗口已关闭', 'error');
    if (!body.querySelector('iframe')) return notify('文件加载完成后才能全屏', 'error');
    const activate = () => { addPreviewExitButton(body); updatePreviewFullscreenButton(); };
    if (body.requestFullscreen) {
      body.requestFullscreen().then(activate).catch(() => {
        // 浏览器限制或拒绝真全屏时退化为覆盖整屏的伪全屏
        body.classList.add('preview-pseudo-fullscreen');
        activate();
      });
    } else {
      body.classList.add('preview-pseudo-fullscreen');
      activate();
    }
  }

  function exitPreviewFullscreen() {
    const body = document.getElementById('previewBody');
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    if (body) {
      body.classList.remove('preview-pseudo-fullscreen');
      const exit = body.querySelector('.preview-exit-fullscreen');
      if (exit) exit.remove();
    }
    updatePreviewFullscreenButton();
  }

  // Esc 或系统手势退出真全屏时同步界面状态
  document.addEventListener('fullscreenchange', () => {
    if (!document.fullscreenElement) exitPreviewFullscreen();
  });

  async function fetchConvertedPdfUrl(report) {
    // 先取文件 Blob
    const fileResponse = await apiFetch(`/api/reports/${report.id}/file`, { credentials: 'same-origin' });
    if (!fileResponse.ok) throw new Error('报告文件不可用');
    const fileBlob = await fileResponse.blob();
    const formData = new FormData();
    formData.append('file', fileBlob, report.fileName || 'upload');
    formData.append('reportId', report.id);
    const response = await apiFetch('/api/preview', { method: 'POST', body: formData });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || '转换失败');
    }
    const blob = await response.blob();
    return URL.createObjectURL(blob);
  }

  function showDeleteConfirm(reportId) {
    const report = getReport(reportId);
    if (!report) return notify('未找到该报告', 'error');
    if (!canDeleteReport(report)) return notify('只能删除自己上传的报告', 'error');
    const isAdminDeletingOthers = isAdminRole() && report.authorId !== state.currentUser.id;
    openModal(`<section class="modal-card">
      ${modalHeader('删除报告', report.title)}
      <div class="modal-body">
        <div class="detail-permission"><svg viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.8"/><path d="M12 8v4M12 16h.01" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg><div><strong style="font-size:18px">确认将这份报告删除吗？</strong>${isAdminDeletingOthers ? '<p><b style="color:var(--warning)">注意：您正在以行政身份处理他人的报告。</b></p>' : ''}</div></div>
      </div>
      <div class="modal-footer">
        <button type="button" class="btn btn-ghost" data-close="modal">取消</button>
        <button type="button" class="btn btn-danger" data-action="confirm-delete" data-id="${report.id}">删除</button>
      </div>
    </section>`);
  }

  async function deleteReport(reportId) {
    const report = getReport(reportId);
    if (!report) return notify('未找到该报告', 'error');
    if (!canDeleteReport(report)) return notify('只能删除自己上传的报告', 'error');
    try {
      await API.deleteReport(reportId);
      await refreshData();
      closeModal();
      notify('报告已删除，后台管理员可恢复');
      renderView();
    } catch (error) {
      notify(error.message || '删除失败', 'error');
    }
  }

  // ----------------------------------------------------------------------- //
  // 行政角色报告统计导出（首页底部）
  // ----------------------------------------------------------------------- //
  function renderAdminStatsPanel() {
    if (!isAdminRole()) return '';
    const people = [...new Set(state.reports.map(report => report.author).filter(Boolean))]
      .sort((a, b) => a.localeCompare(b, 'zh-CN'));
    // 外部报告上传统计的上传人选项：三类外部报告实际上传人（含历史回填），未记录的归为“未记录”
    const uploaders = [...new Set(state.reports
      .filter(report => ['external', 'research_visit', 'roadshow'].includes(report.reportType))
      .map(report => report.uploadedByName || '未记录'))].sort((a, b) => a.localeCompare(b, 'zh-CN'));
    const today = new Date().toISOString().slice(0, 10);
    const yearStart = `${new Date().getFullYear()}-01-01`;
    const thisMonth = new Date().toISOString().slice(0, 7);
    return `<section class="panel-card admin-stats-panel">
      <div class="panel-header"><div><h2>报告统计导出</h2><p>按时间段、人员导出团队报告数量；内部报告细分到周报/月报/深度报告/其他，外部、调研、路演单独统计。</p></div><span class="permission-note">行政角色</span></div>
      <div class="admin-stats-controls">
        <div class="form-field"><label for="statsStartDate">开始日期</label><input id="statsStartDate" type="date" value="${yearStart}"></div>
        <div class="form-field"><label for="statsEndDate">结束日期</label><input id="statsEndDate" type="date" value="${today}"></div>
        <div class="form-field"><label for="statsPerson">人员</label><select id="statsPerson" class="select-control"><option value="">全部人员</option>${people.map(person => `<option value="${escapeHTML(person)}">${escapeHTML(person)}</option>`).join('')}</select></div>
        <button class="btn btn-primary" data-action="export-admin-stats"><svg viewBox="0 0 24 24" fill="none" width="16" height="16"><path d="M12 3v12m0 0 4-4m-4 4-4-4M5 21h14" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>导出 Excel</button>
      </div>
    </section>
    <section class="panel-card admin-stats-panel">
      <div class="panel-header"><div><h2>外部报告上传统计</h2><p>按月统计外部报告、调研报告、路演报告的上传数量，细分到上传人；行政代传计入行政名下。</p></div><span class="permission-note">行政角色</span></div>
      <div class="admin-stats-controls">
        <div class="form-field"><label for="extStatsStartMonth">开始月份</label><input id="extStatsStartMonth" type="month" value="${yearStart.slice(0, 7)}"></div>
        <div class="form-field"><label for="extStatsEndMonth">结束月份</label><input id="extStatsEndMonth" type="month" value="${thisMonth}"></div>
        <div class="form-field"><label for="extStatsUploader">上传人</label><select id="extStatsUploader" class="select-control"><option value="">全部上传人</option>${uploaders.map(person => `<option value="${escapeHTML(person)}">${escapeHTML(person)}</option>`).join('')}</select></div>
        <button class="btn btn-primary" data-action="export-external-upload-stats"><svg viewBox="0 0 24 24" fill="none" width="16" height="16"><path d="M12 3v12m0 0 4-4m-4 4-4-4M5 21h14" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>导出 Excel</button>
      </div>
    </section>`;
  }

  // 外部报告上传统计：按月 × 上传人统计外部/调研/路演报告上传数量，导出 Excel
  function exportExternalUploadStatsExcel() {
    if (!isAdminRole()) return notify('仅行政角色可导出统计', 'error');
    const startInput = document.getElementById('extStatsStartMonth');
    const endInput = document.getElementById('extStatsEndMonth');
    const uploaderInput = document.getElementById('extStatsUploader');
    if (!startInput || !endInput || !uploaderInput) return notify('请先填写统计条件', 'error');
    const startMonth = startInput.value;
    const endMonth = endInput.value;
    if (!startMonth || !endMonth) return notify('请选择起止月份', 'error');
    if (startMonth > endMonth) return notify('开始月份不能晚于结束月份', 'error');
    const uploader = uploaderInput.value;
    const typeKeys = ['external', 'research_visit', 'roadshow'];
    const typeLabels = { external: '外部报告', research_visit: '调研报告', roadshow: '路演报告' };
    const monthOf = report => String(report.uploadedAt || '').slice(0, 7);
    const filtered = state.reports.filter(report => typeKeys.includes(report.reportType)
      && monthOf(report) >= startMonth && monthOf(report) <= endMonth
      && (!uploader || (report.uploadedByName || '未记录') === uploader));
    // 月份 → 上传人 → 各类型计数
    const byMonth = new Map();
    filtered.forEach(report => {
      const month = monthOf(report) || '未知月份';
      const name = report.uploadedByName || '未记录';
      if (!byMonth.has(month)) byMonth.set(month, new Map());
      const people = byMonth.get(month);
      if (!people.has(name)) people.set(name, { external: 0, research_visit: 0, roadshow: 0, total: 0 });
      const entry = people.get(name);
      entry[report.reportType] += 1;
      entry.total += 1;
    });
    const months = [...byMonth.keys()].sort();
    const escapeCell = val => String(val ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    const header = ['月份', '上传人', '外部报告', '调研报告', '路演报告', '合计'];
    const rows = [];
    const grand = { external: 0, research_visit: 0, roadshow: 0, total: 0 };
    months.forEach(month => {
      const people = [...byMonth.get(month).entries()].sort((a, b) => a[0].localeCompare(b[0], 'zh-CN'));
      const monthTotal = { external: 0, research_visit: 0, roadshow: 0, total: 0 };
      people.forEach(([name, entry], idx) => {
        rows.push([idx === 0 ? monthLabel(month) : '', name, entry.external, entry.research_visit, entry.roadshow, entry.total]);
        typeKeys.forEach(key => { monthTotal[key] += entry[key]; grand[key] += entry[key]; });
        monthTotal.total += entry.total;
      });
      rows.push([`${monthLabel(month)} 小计`, '', monthTotal.external, monthTotal.research_visit, monthTotal.roadshow, monthTotal.total]);
      grand.total += monthTotal.total;
    });
    if (!months.length) return notify('该时间段内没有外部/调研/路演报告', 'info');
    const infoRows = [
      [`外部报告上传统计（${monthLabel(startMonth)} 至 ${monthLabel(endMonth)}${uploader ? ` · ${uploader}` : ' · 全部上传人'}）`],
      ['统计口径', '按上传时间归月，细分到实际上传人（行政代传计入行政名下）', '报告总数', grand.total],
    ];
    const html = `<html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:x="urn:schemas-microsoft-com:office:excel" xmlns="http://www.w3.org/TR/REC-html40"><head><meta charset="UTF-8"><!--[if gte mso 9]><xml><x:ExcelWorkbook><x:ExcelWorksheets><x:ExcelWorksheet><x:Name>上传统计</x:Name><x:WorksheetOptions><x:DisplayGridlines/></x:WorksheetOptions></x:ExcelWorksheet></x:ExcelWorksheets></x:ExcelWorkbook></xml><![endif]--></head><body><table border="1">
      <thead>${infoRows.map(row => `<tr>${row.map((cell, i) => `<th${i === 0 && row.length === 1 ? ' colspan="' + header.length + '"' : ''}>${escapeCell(cell)}</th>`).join('')}</tr>`).join('')}<tr>${header.map(h => `<th>${escapeCell(h)}</th>`).join('')}</tr></thead>
      <tbody>${rows.map(row => `<tr>${row.map((cell, i) => `<td${String(row[0]).includes('小计') && i === 0 ? ' style="font-weight:bold"' : ''}>${escapeCell(cell)}</td>`).join('')}</tr>`).join('')}<tr><th>总计</th><td></td><th>${grand.external}</th><th>${grand.research_visit}</th><th>${grand.roadshow}</th><th>${grand.total}</th></tr></tbody>
    </table></body></html>`;
    const blob = new Blob(['\ufeff' + html], { type: 'application/vnd.ms-excel;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `外部报告上传统计_${startMonth}_${endMonth}${uploader ? '_' + uploader : ''}.xls`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    notify(`已导出 ${months.length} 个月的上传统计`);
  }

  function exportAdminStatsExcel() {
    if (!isAdminRole()) return notify('仅行政角色可导出统计', 'error');
    const startInput = document.getElementById('statsStartDate');
    const endInput = document.getElementById('statsEndDate');
    const personInput = document.getElementById('statsPerson');
    if (!startInput || !endInput || !personInput) return notify('请先填写统计条件', 'error');
    const startDate = startInput.value;
    const endDate = endInput.value;
    if (!startDate || !endDate) return notify('请选择起止日期', 'error');
    const person = personInput.value;
    const inRange = report => {
      const value = String(report.reportDate || report.uploadedAt || '');
      const date = value.length === 10 ? `${value}T00:00:00` : value;
      return date >= `${startDate}T00:00:00` && date <= `${endDate}T23:59:59`;
    };
    const filtered = state.reports.filter(report => inRange(report) && (!person || report.author === person));
    // 列：周报 / 月报 / 深度报告 / 其他报告（内部报告二级分类）+ 外部报告 / 调研报告 / 路演报告 + 合计
    const keys = ['weekly', 'monthly', 'deep', 'otherInternal', 'external', 'research_visit', 'roadshow'];
    const bucketOf = report => {
      if (report.reportType === 'internal') {
        if (report.category === 'weekly') return 'weekly';
        if (report.category === 'monthly') return 'monthly';
        if (report.category === 'deep') return 'deep';
        return 'otherInternal';
      }
      if (report.reportType === 'external') return 'external';
      if (report.reportType === 'research_visit') return 'research_visit';
      if (report.reportType === 'roadshow') return 'roadshow';
      return 'otherInternal';
    };
    const byAuthor = new Map();
    filtered.forEach(report => {
      const key = report.author || '未知作者';
      if (!byAuthor.has(key)) byAuthor.set(key, { name: key, org: report.org || '', total: 0, weekly: 0, monthly: 0, deep: 0, otherInternal: 0, external: 0, research_visit: 0, roadshow: 0 });
      const entry = byAuthor.get(key);
      entry[bucketOf(report)] += 1;
      entry.total += 1;
    });
    const rows = [...byAuthor.values()].sort((a, b) => a.name.localeCompare(b.name, 'zh-CN'));
    const totals = keys.reduce((acc, k) => { acc[k] = rows.reduce((s, r) => s + r[k], 0); return acc; }, {});
    totals.total = rows.reduce((s, r) => s + r.total, 0);
    const header = ['姓名', '部门', '周报', '月报', '深度报告', '其他报告', '外部报告', '调研报告', '路演报告', '合计'];
    const escapeCell = val => String(val ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    const infoRows = [
      [`报告统计（${startDate} 至 ${endDate}${person ? ` · ${person}` : ' · 全部人员'}）`],
      ['统计范围', '所有可见报告；内部报告按周报/月报/深度报告/其他报告细分，外部、调研、路演单独统计', '报告总数', totals.total],
    ];
    const cellRow = row => `<tr><td>${escapeCell(row.name)}</td><td>${escapeCell(row.org)}</td>${keys.map(k => `<td>${row[k]}</td>`).join('')}<td>${row.total}</td></tr>`;
    const html = `<html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:x="urn:schemas-microsoft-com:office:excel" xmlns="http://www.w3.org/TR/REC-html40"><head><meta charset="UTF-8"><!--[if gte mso 9]><xml><x:ExcelWorkbook><x:ExcelWorksheets><x:ExcelWorksheet><x:Name>报告统计</x:Name><x:WorksheetOptions><x:DisplayGridlines/></x:WorksheetOptions></x:ExcelWorksheet></x:ExcelWorksheets></x:ExcelWorkbook></xml><![endif]--></head><body><table border="1">
      <thead>${infoRows.map(row => `<tr>${row.map((cell, i) => `<th${i === 0 && row.length === 1 ? ' colspan="' + header.length + '"' : ''}>${escapeCell(cell)}</th>`).join('')}</tr>`).join('')}<tr>${header.map(h => `<th>${escapeCell(h)}</th>`).join('')}</tr></thead>
      <tbody>${rows.map(cellRow).join('')}<tr><th>合计</th><td></td>${keys.map(k => `<th>${totals[k]}</th>`).join('')}<th>${totals.total}</th></tr></tbody>
    </table></body></html>`;
    const blob = new Blob(['\ufeff' + html], { type: 'application/vnd.ms-excel;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `报告统计_${startDate}_${endDate}${person ? '_' + person : ''}.xls`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    notify(`已导出 ${rows.length} 位人员的报告统计`);
  }

  function openSidebar() {
    els.sidebar.classList.add('open');
    els.sidebarMask.classList.add('open');
  }

  function closeSidebar() {
    els.sidebar.classList.remove('open');
    els.sidebarMask.classList.remove('open');
  }

  function handleRootClick(event) {
    const close = event.target.closest('[data-close="modal"]');
    if (close) return closeModal();
    const target = event.target.closest('[data-action]');
    if (!target) return;
    const action = target.dataset.action;
    const reportId = target.dataset.id;
    if (action === 'logout') logout();
    else if (action === 'change-password') showChangePasswordModal();
    else if (action === 'open-upload') showUploadModal();
    else if (action === 'view-report') showReportDetail(reportId);
    else if (action === 'edit-report') showReportEditModal(reportId);
    else if (action === 'toggle-like') toggleExternalLike(reportId);
    else if (action === 'toggle-favorite') toggleFavorite(reportId);
    else if (action === 'set-report-view') { state.reportView = target.dataset.viewMode === 'card' ? 'card' : 'list'; renderView(); }
    else if (action === 'sort-report-list') { const key = ['date', 'favorite', 'view'].includes(target.dataset.sortKey) ? target.dataset.sortKey : 'date'; state.reportListSort = state.reportListSort.key === key ? { key, dir: state.reportListSort.dir === 'desc' ? 'asc' : 'desc' } : { key, dir: 'desc' }; renderView(); }
    else if (action === 'ai-summary-style') { switchAiSummaryStyle(target.dataset.style); }
    else if (action === 'generate-ai-summary') { generateAiSummary(reportId, aiSummaryState.style, false); }
    else if (action === 'regenerate-ai-summary') { generateAiSummary(reportId, aiSummaryState.style, true); }
    else if (action === 'toggle-report-qa') { toggleReportQa(); }
    else if (action === 'submit-report-qa') { submitReportQuestion(reportId); }
    else if (action === 'toggle-preview-summary') { togglePreviewSummary(); }
    else if (action === 'open-ai-summary') { openAiSummary(reportId); }
    else if (action === 'preview-report') previewReport(reportId);
    else if (action === 'toggle-preview-fullscreen') togglePreviewFullscreen();
    else if (action === 'exit-preview-fullscreen') exitPreviewFullscreen();
    else if (action === 'download') downloadReport(reportId);
    else if (action === 'delete-report') showDeleteConfirm(reportId);
    else if (action === 'confirm-delete') deleteReport(reportId);
    else if (action === 'open-rating') showRatingModal(reportId);
    else if (action === 'view-my-rating') showRatingModal(reportId);
    else if (action === 'view-results') showResultDetail(reportId);
    else if (action === 'go-reports') { state.filters = { category: '', org: '', theme: '', score: '', query: '' }; navigate('reports'); }
    else if (action === 'go-rating') navigate('rating');
    else if (action === 'go-results') navigate('results');
    else if (action === 'go-my-reports') navigate('my-reports');
    else if (action === 'set-search-report-type') { state.search.reportType = target.dataset.reportType || 'all'; renderSearchResults(); }
    else if (action === 'clear-search') { state.search = { query: '', reportType: 'all' }; els.globalSearch.value = ''; renderSearchResults(); document.getElementById('searchResultInput')?.focus(); }
    else if (action === 'focus-history') focusHistoryItem(target.dataset.conversationId || '');
    else if (action === 'new-knowledge-conversation') startNewKnowledgeConversation();
    else if (action === 'set-knowledge-type') setKnowledgeQuestionType(target.dataset.questionType || 'general_work');
    else if (action === 'toggle-knowledge-thinking') toggleKnowledgeThinking();
    else if (action === 'clear-history') showClearHistoryModal();
    else if (action === 'confirm-clear-history') confirmClearHistory();
    else if (action === 'filter-category' || action === 'set-report-category') { state.filters.category = target.dataset.category || ''; navigate('reports'); }
    else if (action === 'filter-theme' || action === 'set-report-theme') { state.filters.theme = target.dataset.theme || ''; navigate('reports'); }
    else if (action === 'set-my-report-category') { state.myReportFilters.category = target.dataset.category || ''; renderMyReports(); }
    else if (action === 'set-my-report-type') { state.myReportFilters.reportType = target.dataset.reportType || 'all'; renderMyReports(); }
    else if (action === 'reset-report-filters') { state.filters = { category: '', org: '', theme: '', score: '', query: '' }; renderReports(); }
    else if (action === 'reset-my-report-filters') { state.myReportFilters = { category: '', reportType: 'all', query: '', sort: 'date-desc' }; renderMyReports(); }
    else if (action === 'reset-rating-filters') { state.ratingFilters = { month: '', person: '', sort: 'date-desc', category: '', theme: '' }; renderRatingCenter(); }
    else if (action === 'reset-result-filters') { state.resultFilters = { month: '', person: '', sort: 'date-desc', category: '', theme: '' }; renderResults(); }
    else if (action === 'rating-tab') { state.ratingTab = target.dataset.tab; renderRatingCenter(); }
    else if (action === 'export-results-excel') exportResultsExcel();
    else if (action === 'export-result-detail-excel') exportResultDetailExcel(reportId);
    else if (action === 'sort-result-column') {
      const col = target.dataset.sort;
      if (col) {
        // 同列再次点击：在 -desc / -asc 之间切换
        const current = state.resultFilters.sort;
        const colBase = col.replace(/-(?:desc|asc)$/, '');
        if (current.startsWith(colBase)) {
          // 已在该列排序：翻转到反方向
          state.resultFilters.sort = current.endsWith('-desc') ? current.replace(/-desc$/, '-asc') : current.replace(/-asc$/, '-desc');
        } else {
          state.resultFilters.sort = col;
        }
        renderResults();
      }
    }
    else if (action === 'remove-upload-file') removeUploadFile(target.dataset.name);
    else if (action === 'ai-complete') handleAiComplete();
    else if (action === 'export-admin-stats') exportAdminStatsExcel();
    else if (action === 'export-external-upload-stats') exportExternalUploadStatsExcel();
    else if (action === 'roadshow-add') showRoadshowFormModal();
    else if (action === 'roadshow-detail') showRoadshowDetailModal(reportId);
    else if (action === 'roadshow-prev-week') { state.roadshow.weekOffset = (state.roadshow.weekOffset || 0) - 1; loadRoadshowSchedule(); }
    else if (action === 'roadshow-export') downloadRoadshowExcel();
    else if (action === 'roadshow-toggle-wide') {
      // 加宽/还原：翻转后用已有数据本地重渲染，不重新请求接口
      state.roadshow.wide = !state.roadshow.wide;
      const body = document.getElementById('roadshowPanelBody');
      if (body && state.roadshow.weekStart) {
        body.innerHTML = roadshowCalendarHTML(state.roadshow.items);
        bindRoadshowQuickAdd(body);
      }
    }
    else if (action === 'roadshow-next-week') { state.roadshow.weekOffset = (state.roadshow.weekOffset || 0) + 1; loadRoadshowSchedule(); }
    else if (action === 'roadshow-this-week') { state.roadshow.weekOffset = 0; loadRoadshowSchedule(); }
    else if (action === 'roadshow-delete') showRoadshowDeleteConfirm(reportId);
    else if (action === 'confirm-roadshow-delete') deleteRoadshowItem(reportId);
    else if (action === 'roadshow-upload') openRoadshowUpload(reportId);
    else if (action === 'roadshow-ai-parse') handleRoadshowAiParse();
    else if (action === 'roadshow-edit') {
      const item = (state.roadshow.items || []).find(row => row.id === reportId);
      if (item) showRoadshowFormModal({ item });
      else notify('未找到该路演安排，请刷新后重试', 'error');
    }
    else if (action === 'match-schedule') showScheduleMatchModal(reportId);
    else if (action === 'unlink-schedule') unlinkSchedule(reportId);
    else if (action === 'match-report') showReportMatchModal(reportId);
    else if (action === 'auto-match-report') handleAutoMatchReport(reportId, target);
  }

  function bindEvents() {
    els.loginForm.addEventListener('submit', async event => {
      event.preventDefault();
      setLoginError('');
      try {
        const data = await API.login(els.loginUsername.value, els.loginPassword.value);
        await login(data.user);
      } catch (error) {
        setLoginError(error.message || '账号或密码不正确，请检查姓名全拼与初始密码。');
        els.loginPassword.focus();
      }
    });
    els.passwordToggle.addEventListener('click', () => {
      const input = els.loginPassword;
      input.type = input.type === 'password' ? 'text' : 'password';
      els.passwordToggle.classList.toggle('active', input.type === 'text');
    });
      document.querySelectorAll('.nav-item').forEach(item => item.addEventListener('click', () => { if (item.dataset.reportType) state.reportType = item.dataset.reportType; navigate(item.dataset.view, {
      category: item.dataset.category !== undefined ? (item.dataset.category || '') : (item.dataset.view === 'reports' ? state.filters.category : ''),
      theme: item.dataset.theme !== undefined ? (item.dataset.theme || '') : (item.dataset.view === 'reports' ? state.filters.theme : '')
    }); }));
    document.addEventListener('click', handleRootClick);
    // 就本文提问输入框：Enter 提交（输入法组合期间不触发）
    document.addEventListener('keydown', event => {
      if (event.key !== 'Enter' || event.isComposing) return;
      const input = event.target.closest ? event.target.closest('.ai-qa-input') : null;
      if (!input) return;
      event.preventDefault();
      const reportId = input.closest('.ai-summary-panel')?.dataset.reportId;
      if (reportId) submitReportQuestion(reportId);
    });
    els.viewRoot.addEventListener('input', event => {
      // 输入法组合（中文拼音等）期间不重渲染，避免销毁输入框导致候选窗丢失、无法输入中文。
      if (event.isComposing) return;
      if (event.target.id === 'reportSearch') { state.filters.query = event.target.value; renderReports(); const input = document.getElementById('reportSearch'); input.focus(); input.setSelectionRange(input.value.length, input.value.length); }
      if (event.target.id === 'searchResultInput') { state.search.query = event.target.value; state.search.reportType = 'all'; renderSearchResults(); const input = document.getElementById('searchResultInput'); input.focus(); input.setSelectionRange(input.value.length, input.value.length); }
      if (event.target.id === 'externalReportSearch') { state.filters.query = event.target.value; renderExternalReports(); const input = document.getElementById('externalReportSearch'); input.focus(); input.setSelectionRange(input.value.length, input.value.length); }
      if (event.target.id === 'typedReportSearch') { state.filters.query = event.target.value; renderTypedReports(state.reportType); const input = document.getElementById('typedReportSearch'); input.focus(); input.setSelectionRange(input.value.length, input.value.length); }
      if (event.target.id === 'myReportSearch') { state.myReportFilters.query = event.target.value; renderMyReports(); const input = document.getElementById('myReportSearch'); input.focus(); input.setSelectionRange(input.value.length, input.value.length); }
    });
    // 组合输入结束时强制刷新一次，确保中文输入后筛选结果同步更新。
    els.viewRoot.addEventListener('compositionend', event => {
      const id = event.target.id;
      if (id === 'reportSearch') { state.filters.query = event.target.value; renderReports(); const input = document.getElementById('reportSearch'); input.focus(); input.setSelectionRange(input.value.length, input.value.length); }
      else if (id === 'searchResultInput') { state.search.query = event.target.value; state.search.reportType = 'all'; renderSearchResults(); const input = document.getElementById('searchResultInput'); input.focus(); input.setSelectionRange(input.value.length, input.value.length); }
      else if (id === 'externalReportSearch') { state.filters.query = event.target.value; renderExternalReports(); const input = document.getElementById('externalReportSearch'); input.focus(); input.setSelectionRange(input.value.length, input.value.length); }
      else if (id === 'typedReportSearch') { state.filters.query = event.target.value; renderTypedReports(state.reportType); const input = document.getElementById('typedReportSearch'); input.focus(); input.setSelectionRange(input.value.length, input.value.length); }
      else if (id === 'myReportSearch') { state.myReportFilters.query = event.target.value; renderMyReports(); const input = document.getElementById('myReportSearch'); input.focus(); input.setSelectionRange(input.value.length, input.value.length); }
    });
    els.viewRoot.addEventListener('change', event => {
      if (event.target.id === 'orgFilter') { state.filters.org = event.target.value; renderReports(); }
      if (event.target.id === 'scoreFilter') { state.filters.score = event.target.value; renderReports(); }
      if (event.target.id === 'myReportSort') { state.myReportFilters.sort = event.target.value; renderMyReports(); }
      if (event.target.id === 'hotReportType') { const list = document.getElementById('hotReportList'); if (list) list.innerHTML = renderHotReports(event.target.value); }
      if (event.target.id === 'ratingMonthFilter') { state.ratingFilters.month = event.target.value; renderRatingCenter(); }
      if (event.target.id === 'ratingPersonFilter') { state.ratingFilters.person = event.target.value; renderRatingCenter(); }
      if (event.target.id === 'ratingCategoryFilter') { state.ratingFilters.category = event.target.value; renderRatingCenter(); }
      if (event.target.id === 'ratingThemeFilter') { state.ratingFilters.theme = event.target.value; renderRatingCenter(); }
      if (event.target.id === 'ratingSort') { state.ratingFilters.sort = event.target.value; renderRatingCenter(); }
      if (event.target.id === 'resultMonthFilter') { state.resultFilters.month = event.target.value; renderResults(); }
      if (event.target.id === 'resultPersonFilter') { state.resultFilters.person = event.target.value; renderResults(); }
      if (event.target.id === 'resultCategoryFilter') { state.resultFilters.category = event.target.value; renderResults(); }
      if (event.target.id === 'resultThemeFilter') { state.resultFilters.theme = event.target.value; renderResults(); }
      if (event.target.id === 'resultSort') { state.resultFilters.sort = event.target.value; renderResults(); }
    });
    els.globalSearch.addEventListener('keydown', event => {
      if (event.key === 'Enter' && !event.isComposing) { state.search = { query: event.target.value.trim(), reportType: 'all' }; navigate('search-results'); }
    });
    els.globalSearch.addEventListener('input', event => {
      if (!event.target.value && state.view === 'reports' && state.filters.query) { state.filters.query = ''; renderReports(); }
      if (!event.target.value && state.view === 'search-results' && state.search.query) { state.search = { query: '', reportType: 'all' }; renderSearchResults(); }
    });
    els.mobileMenu.addEventListener('click', openSidebar);
    els.sidebarMask.addEventListener('click', closeSidebar);
    document.addEventListener('keydown', event => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k' && state.currentUser) { event.preventDefault(); els.globalSearch.focus(); }
      if (event.key === 'Escape') { if (els.modalLayer.classList.contains('open')) closeModal(); else closeSidebar(); }
    });
    // 视口高度变化（换屏/系统或浏览器缩放）时行高随 CSS 变量变化，按已有数据重渲染路演周历，避免刻度错位
    let roadshowResizeTimer = null;
    window.addEventListener('resize', () => {
      if (!document.getElementById('roadshowPanelBody')) return;
      clearTimeout(roadshowResizeTimer);
      roadshowResizeTimer = setTimeout(() => {
        const body = document.getElementById('roadshowPanelBody');
        if (body && state.roadshow.items.length) {
          body.innerHTML = roadshowCalendarHTML(state.roadshow.items);
          bindRoadshowQuickAdd(body);
        }
      }, 150);
    });
  }

  async function boot() {
    Object.assign(els, {
      loginPage: document.getElementById('loginPage'),
      loginForm: document.getElementById('loginForm'),
      loginUsername: document.getElementById('loginUsername'),
      loginPassword: document.getElementById('loginPassword'),
      passwordToggle: document.getElementById('passwordToggle'),
      loginError: document.getElementById('loginError'),
      appShell: document.getElementById('appShell'),
      sidebar: document.getElementById('sidebar'),
      sidebarMask: document.getElementById('sidebarMask'),
      sidebarAccount: document.getElementById('sidebarAccount'),
      pendingNavBadge: document.getElementById('pendingNavBadge'),
      topbarTitle: document.getElementById('topbarTitle'),
      topbarDate: document.getElementById('topbarDate'),
      globalSearch: document.getElementById('globalSearch'),
      mobileMenu: document.getElementById('mobileMenu'),
      viewRoot: document.getElementById('viewRoot'),
      modalLayer: document.getElementById('modalLayer'),
      toastStack: document.getElementById('toastStack')
    });
    bindEvents();
    // 尝试恢复会话
    try {
      const data = await API.me();
      if (data.user) {
        state.currentUser = data.user;
        els.loginPage.hidden = true;
        els.appShell.hidden = false;
        renderAccount();
        await refreshData();
        updateChrome();
        navigate('dashboard');
        return;
      }
    } catch (_) { /* 未登录 */ }
    els.loginPage.hidden = false;
    els.appShell.hidden = true;
    els.loginUsername.focus();
  }

  boot();
})();
