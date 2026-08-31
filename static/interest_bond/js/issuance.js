/* 发行跟踪页:进度卡片、历年节奏比较、20年+累计与限额管理 */
(function () {
  'use strict';
  var boot = window.TRACKER_BOOT || { years: [], categories: [] };
  var allProgressYears = (boot.years || []).map(Number);
  if (!allProgressYears.length) allProgressYears = [new Date().getFullYear()];
  var state = {
    year: Math.max.apply(null, allProgressYears), scope: 'total', chart: null, policyChart: null, progressChart: null,
    progressCategories: (boot.categories || ['一般国债']).slice(), progressYears: allProgressYears.slice(),
    showAllYears: false, showAllPolicyYears: false, limits: [], pendingAction: null
  };
  var colors = ['#2f6fad', '#16845b', '#8a5da8', '#d08a28', '#bf554d'];
  var yearColors = ['#7393b3', '#29a07b', '#9772ad', '#d19338', '#c65e58', '#163f73', '#6f7f91', '#3d78b2', '#8c735a', '#687e45'];
  function $(id) { return document.getElementById(id); }
  function esc(v) {
    return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function fmt(v, d) {
    if (v == null || isNaN(v)) return '—';
    return Number(v).toLocaleString('zh-CN', { minimumFractionDigits: d == null ? 2 : d, maximumFractionDigits: d == null ? 2 : d });
  }
  function api(url, opt) {
    return fetch(url, opt).then(function (r) {
      return r.json().catch(function () { return { error: '服务器返回无效数据' }; }).then(function (data) {
        if (!r.ok) { var e = new Error(data.error || ('请求失败 ' + r.status)); e.status = r.status; throw e; }
        return data;
      });
    });
  }
  function progressCard(item, index) {
    var p = item.progress, pct = p == null ? null : p * 100, visual = pct == null ? 0 : Math.max(0, Math.min(100, pct));
    var badgeClass = item.validation === 'none' ? '' : item.validation;
    var remainClass = item.remaining < 0 ? 'negative' : '';
    var metricLabel = item.category === '一般国债' ? '净增加' : '已发行';
    return '<article class="progress-card" style="--accent:' + colors[index] + '">' +
      '<div class="card-top"><div class="category-name">' + esc(item.category) + '</div><span class="validation-badge ' + badgeClass + '">' + esc(item.status_label) + '</span></div>' +
      '<div class="issued-line"><span class="issued-value">' + fmt(item.issued) + '</span><span class="issued-unit">亿</span><div class="issued-label">' + metricLabel + '</div></div>' +
      '<div class="bar-meta"><span>限额 ' + fmt(item.limit) + '</span><strong>' + (pct == null ? '无额度' : fmt(pct, 1) + '%') + '</strong></div>' +
      '<div class="progress-track"><div class="progress-fill ' + (pct > 100 ? 'over' : '') + '" style="width:' + visual + '%"></div></div>' +
      '<div class="remaining"><span>剩余额度</span><strong class="' + remainClass + '">' + (item.limit ? fmt(item.remaining) : '—') + '</strong></div></article>';
  }
  function loadDashboard() {
    $('progressGrid').innerHTML = '<div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div>';
    return api('/issuance/api/dashboard?year=' + state.year).then(function (data) {
      $('progressGrid').innerHTML = data.items.map(progressCard).join('');
      $('asOfText').textContent = '数据截止 ' + (data.as_of_date || '—');
    }).catch(function (e) {
      $('progressGrid').innerHTML = '<div class="no-data">加载失败:' + esc(e.message) + '</div>';
    });
  }
  function dayLabels() {
    var out = [], d = new Date(Date.UTC(2000, 0, 1));
    while (d.getUTCFullYear() === 2000) {
      out.push(String(d.getUTCMonth() + 1).padStart(2, '0') + '-' + String(d.getUTCDate()).padStart(2, '0'));
      d.setUTCDate(d.getUTCDate() + 1);
    }
    return out;
  }
  var axisDays = dayLabels();

  function renderProgressYearControls() {
    Array.from($('progressYearChips').querySelectorAll('button[data-year]')).forEach(function (button) {
      var active = state.progressYears.indexOf(Number(button.dataset.year)) >= 0;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
  }
  function renderCategoryControls() {
    Array.from($('progressCategoryTabs').querySelectorAll('button[data-category]')).forEach(function (button) {
      var active = state.progressCategories.indexOf(button.dataset.category) >= 0;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
  }
  function renderProgressChart(data) {
    var categories = data.categories || [];
    var isTreasuryOnly = categories.length === 1 && categories[0] === '一般国债';
    $('progressCategoryLabel').textContent = categories.length === boot.categories.length ? '全部品种' : (categories.length ? categories.join('＋') : '未选择品种');
    if (!categories.length) {
      $('progressBasis').textContent = '请点击品种或年份查看';
      $('issuanceProgressChart').classList.add('hidden');
      $('progressChartEmpty').textContent = '未选择比较品种。';
      $('progressChartEmpty').classList.remove('hidden');
      return;
    }
    var usable = data.years.filter(function (s) { return s.points.length && s.denominator > 0; });
    $('progressBasis').textContent = '历史=全年实际100%｜当年=年度限额100% · 已选' + data.years.length + '年' +
      (usable.length < data.years.length ? '(有数据' + usable.length + '年)' : '');
    if (!usable.length) {
      $('issuanceProgressChart').classList.add('hidden');
      $('progressChartEmpty').classList.remove('hidden');
      return;
    }
    $('issuanceProgressChart').classList.remove('hidden');
    $('progressChartEmpty').classList.add('hidden');
    if (!state.progressChart) state.progressChart = echarts.init($('issuanceProgressChart'));
    var latest = Math.max.apply(null, usable.map(function (s) { return s.year; }));
    var series = usable.map(function (s, index) {
      var byDay = {};
      s.points.forEach(function (p) { byDay[p.day] = p; });
      var last = s.points[s.points.length - 1].day, cumulative = 0, progress = 0;
      var values = axisDays.map(function (day) {
        if (byDay[day]) {
          cumulative = byDay[day].cumulative; progress = byDay[day].progress;
          return { value: progress * 100, daily: byDay[day].daily, cumulative: cumulative, denominator: s.denominator };
        }
        if (day <= last || s.basis === 'actual') return { value: progress * 100, daily: 0, cumulative: cumulative, denominator: s.denominator };
        return null;
      });
      return {
        name: String(s.year), type: 'line', step: 'end', showSymbol: false, data: values, connectNulls: false,
        lineStyle: { width: s.year === latest ? 2.8 : 1.6, opacity: s.year === latest ? 1 : .72 },
        itemStyle: { color: yearColors[index % yearColors.length] }, emphasis: { focus: 'series' },
        markLine: index === 0 ? {
          silent: true, symbol: 'none', lineStyle: { color: '#9aaabd', type: 'dashed', width: 1 },
          label: { formatter: '100%基准', color: '#77889a', fontSize: 9 }, data: [{ yAxis: 100 }]
        } : undefined
      };
    });
    state.progressChart.setOption({
      animationDuration: 350, color: yearColors,
      grid: { left: 56, right: 25, top: 53, bottom: 48 },
      legend: { type: 'scroll', top: 8, left: 4, right: 4, formatter: function (name) { return name + '年'; }, textStyle: { fontSize: 11, color: '#50657a' } },
      tooltip: {
        trigger: 'axis', confine: true, backgroundColor: 'rgba(18,39,65,.94)', borderWidth: 0, textStyle: { fontSize: 11 },
        formatter: function (params) {
          if (!params.length) return '';
          var html = '<b>' + params[0].axisValue + '</b>';
          params.forEach(function (p) {
            if (p.data == null) return;
            var dailyLabel = isTreasuryOnly ? '当日净变化' : '当日合计';
            html += '<br>' + p.marker + p.seriesName + '年:<b>' + fmt(p.data.value, 1) + '%</b> <span style="color:#aebed0">累计 ' + fmt(p.data.cumulative) + ' 亿｜' + dailyLabel + ' ' + fmt(p.data.daily) + ' 亿</span>';
          });
          return html;
        }
      },
      xAxis: {
        type: 'category', boundaryGap: false, data: axisDays,
        axisLine: { lineStyle: { color: '#b8c4d1' } }, axisTick: { show: false },
        axisLabel: {
          color: '#718095', fontSize: 10,
          interval: function (i, val) { return val.slice(3) === '01' || val === '12-31'; },
          formatter: function (v) { return v.slice(0, 2) + '月'; }
        }
      },
      yAxis: {
        type: 'value', name: '累计进度',
        min: function (v) { return Math.min(0, Math.floor(v.min / 10) * 10); },
        max: function (v) { return Math.max(100, Math.ceil(v.max / 10) * 10); },
        nameTextStyle: { color: '#788699', fontSize: 10 },
        axisLabel: { color: '#718095', fontSize: 10, formatter: '{value}%' },
        splitLine: { lineStyle: { color: '#e8edf3', type: 'dashed' } }
      },
      series: series
    }, true);
    setTimeout(function () { state.progressChart.resize(); }, 20);
  }
  function loadProgressChart() {
    if (!state.progressCategories.length || !state.progressYears.length) {
      renderProgressChart({ categories: state.progressCategories.slice(), years: [] });
      return Promise.resolve();
    }
    var query = 'category=' + encodeURIComponent(state.progressCategories.join(',')) + '&years=' + state.progressYears.join(',');
    return api('/issuance/api/issuance-progress-compare?' + query).then(renderProgressChart).catch(function (e) {
      $('issuanceProgressChart').classList.add('hidden');
      $('progressChartEmpty').textContent = e.message;
      $('progressChartEmpty').classList.remove('hidden');
    });
  }

  function scopeName(scope) { return scope === 'treasury' ? '国债' : scope === 'local' ? '地方债' : '国债＋地方债'; }
  function renderSeriesChart(data, chartId, emptyId, chartKey, showAllKey) {
    var chartEl = $(chartId), emptyEl = $(emptyId);
    if (!state[chartKey]) state[chartKey] = echarts.init(chartEl);
    var chart = state[chartKey];
    if (!data.years.length) { emptyEl.classList.remove('hidden'); chartEl.classList.add('hidden'); return; }
    emptyEl.classList.add('hidden'); chartEl.classList.remove('hidden');
    var latest = Math.max.apply(null, data.years.map(function (y) { return y.year; }));
    var selected = {};
    data.years.forEach(function (y) { selected[String(y.year)] = state[showAllKey] || y.year >= latest - 5; });
    var series = data.years.map(function (y, idx) {
      var byDay = {};
      y.points.forEach(function (p) { byDay[p.day] = p; });
      var last = y.points.length ? y.points[y.points.length - 1].day : '00-00', cum = 0;
      var values = axisDays.map(function (day) {
        if (byDay[day]) { cum = byDay[day].cumulative; return { value: cum, daily: byDay[day].daily }; }
        if (day <= last || y.year < latest) return { value: cum, daily: 0 };
        return null;
      });
      return {
        name: String(y.year), type: 'line', step: 'end', showSymbol: false, symbolSize: 4, data: values,
        connectNulls: false, smooth: false,
        lineStyle: { width: y.year === latest ? 2.8 : 1.5, opacity: y.year === latest ? 1 : .68 },
        itemStyle: { color: y.year === latest ? '#163f73' : colors[idx % colors.length] },
        emphasis: { focus: 'series' }
      };
    });
    chart.setOption({
      animationDuration: 350,
      color: ['#7b8da0', '#c17a42', '#6d9a7d', '#8b6fa3', '#c35f5b', '#163f73'],
      grid: { left: 65, right: 28, top: 52, bottom: 55 },
      legend: { type: 'scroll', top: 8, right: 8, selected: selected, textStyle: { fontSize: 11, color: '#50657a' } },
      tooltip: {
        trigger: 'axis', confine: true, backgroundColor: 'rgba(18,39,65,.94)', borderWidth: 0, textStyle: { fontSize: 11 },
        formatter: function (params) {
          if (!params.length) return '';
          var html = '<b>' + params[0].axisValue + '</b>';
          params.forEach(function (p) {
            if (p.data == null) return;
            html += '<br>' + p.marker + p.seriesName + '年:<b>' + fmt(p.data.value) + '</b> 亿 <span style="color:#aebed0">当日 ' + fmt(p.data.daily) + ' 亿</span>';
          });
          return html;
        }
      },
      xAxis: {
        type: 'category', boundaryGap: false, data: axisDays,
        axisLine: { lineStyle: { color: '#b8c4d1' } }, axisTick: { show: false },
        axisLabel: {
          color: '#718095', fontSize: 10,
          interval: function (i, val) { return val.slice(3) === '01' || val === '12-31'; },
          formatter: function (v) { return v.slice(0, 2) + '月'; }
        }
      },
      yAxis: {
        type: 'value', name: '累计发行(亿元)',
        nameTextStyle: { color: '#788699', fontSize: 10, padding: [0, 0, 7, 0] },
        axisLabel: { color: '#718095', fontSize: 10, formatter: function (v) { return Number(v).toLocaleString('zh-CN'); } },
        splitLine: { lineStyle: { color: '#e8edf3', type: 'dashed' } }
      },
      dataZoom: [{ type: 'inside', filterMode: 'none' }],
      series: series
    }, true);
    setTimeout(function () { chart.resize(); }, 20);
  }
  function renderChart(data) {
    renderSeriesChart(data, 'longTermChart', 'chartEmpty', 'chart', 'showAllYears');
  }
  function renderPolicyChart(data) {
    renderSeriesChart(data, 'policyFinancialChart', 'policyFinancialEmpty', 'policyChart', 'showAllPolicyYears');
  }
  function loadChart() {
    $('chartScopeLabel').textContent = scopeName(state.scope);
    return api('/issuance/api/long-term?scope=' + state.scope).then(renderChart).catch(function (e) {
      $('chartEmpty').textContent = e.message;
      $('chartEmpty').classList.remove('hidden');
    });
  }
  function loadPolicyChart() {
    return api('/issuance/api/policy-financial').then(renderPolicyChart).catch(function (e) {
      $('policyFinancialEmpty').textContent = e.message;
      $('policyFinancialEmpty').classList.remove('hidden');
    });
  }
  function refreshAll() { return Promise.all([loadDashboard(), loadProgressChart(), loadChart(), loadPolicyChart()]); }

  function openAdmin() { $('adminModal').classList.remove('hidden'); $('adminPassword').focus(); }
  function closeAdmin() { $('adminModal').classList.add('hidden'); $('adminError').textContent = ''; }
  function loadLimits() {
    return api('/issuance/api/limits').then(function (data) {
      state.limits = data.years; renderLimits();
      $('adminLoginView').classList.add('hidden'); $('adminPanel').classList.remove('hidden');
    });
  }
  function renderLimits() {
    $('limitBody').innerHTML = state.limits.map(function (y) {
      return '<tr><td><input class="limit-year" type="number" value="' + y.year + '" min="2000" max="2200"></td>' +
        boot.categories.map(function (c) {
          return '<td><input class="limit-value" data-category="' + esc(c) + '" type="number" min="0" step="0.0001" value="' + Number((y.values || {})[c] || 0) + '"></td>';
        }).join('') + '</tr>';
    }).join('');
  }
  function adminLogin() {
    $('adminError').textContent = '';
    return api('/issuance/api/admin/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ password: $('adminPassword').value }) })
      .then(function () { return loadLimits(); })
      .then(function () {
        if (state.pendingAction) { var fn = state.pendingAction; state.pendingAction = null; fn(); }
      })
      .catch(function (e) { $('adminError').textContent = e.message; });
  }
  function collectLimits() {
    return Array.from($('limitBody').querySelectorAll('tr')).map(function (tr) {
      var values = {};
      tr.querySelectorAll('.limit-value').forEach(function (input) { values[input.dataset.category] = Number(input.value); });
      return { year: Number(tr.querySelector('.limit-year').value), values: values };
    });
  }
  function saveLimits() {
    return api('/issuance/api/limits', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ years: collectLimits() }) })
      .then(function () { closeAdmin(); return refreshAll(); })
      .catch(function (e) { $('adminError').textContent = e.message; });
  }
  $('yearSelect').addEventListener('change', function () {
    state.year = Number(this.value);
    loadDashboard();
  });
  $('progressCategoryTabs').addEventListener('click', function (e) {
    var button = e.target.closest('button[data-category]');
    if (!button) return;
    var category = button.dataset.category;
    var index = state.progressCategories.indexOf(category);
    if (index >= 0) { state.progressCategories.splice(index, 1); }
    else state.progressCategories.push(category);
    renderCategoryControls(); loadProgressChart();
  });
  $('progressYearChips').addEventListener('click', function (e) {
    var button = e.target.closest('button[data-year]');
    if (!button) return;
    var year = Number(button.dataset.year);
    var index = state.progressYears.indexOf(year);
    if (index >= 0) state.progressYears.splice(index, 1);
    else { state.progressYears.push(year); state.progressYears.sort(); }
    renderProgressYearControls(); loadProgressChart();
  });
  $('scopeTabs').addEventListener('click', function (e) {
    var b = e.target.closest('button[data-scope]');
    if (!b) return;
    state.scope = b.dataset.scope;
    Array.from(this.children).forEach(function (x) { x.classList.toggle('active', x === b); });
    loadChart();
  });
  $('allYearsBtn').addEventListener('click', function () {
    state.showAllYears = !state.showAllYears;
    this.textContent = state.showAllYears ? '仅显示近六年' : '显示全部年份';
    loadChart();
  });
  $('policyAllYearsBtn').addEventListener('click', function () {
    state.showAllPolicyYears = !state.showAllPolicyYears;
    this.textContent = state.showAllPolicyYears ? '仅显示近六年' : '显示全部年份';
    loadPolicyChart();
  });
  $('manageBtn').addEventListener('click', openAdmin);
  $('modalClose').addEventListener('click', closeAdmin);
  $('cancelBtn').addEventListener('click', closeAdmin);
  $('adminModal').addEventListener('click', function (e) { if (e.target === this) closeAdmin(); });
  $('adminLoginBtn').addEventListener('click', adminLogin);
  $('adminPassword').addEventListener('keydown', function (e) { if (e.key === 'Enter') adminLogin(); });
  $('addYearBtn').addEventListener('click', function () {
    var max = Math.max.apply(null, state.limits.map(function (x) { return x.year; }).concat([new Date().getFullYear()]));
    var values = {};
    boot.categories.forEach(function (c) { values[c] = 0; });
    state.limits.push({ year: max + 1, values: values });
    renderLimits();
  });
  $('saveLimitsBtn').addEventListener('click', saveLimits);
  window.addEventListener('resize', function () { if (state.chart) state.chart.resize(); if (state.policyChart) state.policyChart.resize(); if (state.progressChart) state.progressChart.resize(); });
  renderProgressYearControls(); renderCategoryControls(); refreshAll();
})();
