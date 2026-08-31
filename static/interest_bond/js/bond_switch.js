/* 新老券利差跟踪：活跃券信息卡、行式利差构建器、估值/利差图与免税利差 */
(function () {
  'use strict';
  var DASH = null, SERIES = null, TAX = null;
  var currentRange = '1y';
  var pairs = [];
  var rolePicks = {};   /* 角色 -> {日期: {code,name}} 每日判定的个券 */
  var seriesUnit = {};  /* 系列名 -> 单位，用于 tooltip */
  /* 角色虚拟券：每日按成交量排名判定对应个券后取当日估值 */
  var ROLE_OPTS = [['__active', '活跃券'], ['__secondary', '次活跃券'], ['__tertiary', '次次活跃券']];
  var ROLE_CODES = ROLE_OPTS.map(function (x) { return x[0]; });
  function usesRolePair() { return pairs.some(function (p) { return ROLE_CODES.indexOf(p[0]) >= 0 || ROLE_CODES.indexOf(p[1]) >= 0; }); }
  function defaultRange() { return usesRolePair() ? '1y' : 'issue'; }
  var rangeModeLast = null;
  function syncRangeButtons() {
    document.querySelectorAll('#rangeBar .range-btn').forEach(function (x) {
      x.classList.toggle('active', x.dataset.r === currentRange);
    });
    $('customDates').classList.toggle('show', currentRange === 'custom');
  }
  function syncDefaultRange() {
    var def = defaultRange();
    if (rangeModeLast !== def) {
      rangeModeLast = def;
      currentRange = def;
      syncRangeButtons();
      renderMainChart(); renderTaxChart();
    }
  }
  var pairColors = ['#4361ee', '#e63946', '#10b981', '#f59e0b', '#9333ea', '#0ea5e9'];
  var MAX_PAIRS = pairColors.length;
  var bondColors = ['#1f4e8c', '#7c3aed', '#0f766e', '#b45309'];
  var mainChart = null, taxChart = null;

  function $(id) { return document.getElementById(id); }
  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function toast(message) {
    var node = $('toast'); node.textContent = message; node.style.display = 'block';
    setTimeout(function () { node.style.display = 'none'; }, 2800);
  }
  function fmt(value, digits) { return value == null ? '--' : Number(value).toLocaleString('zh-CN', { minimumFractionDigits: digits || 0, maximumFractionDigits: digits || 0 }); }

  document.querySelectorAll('.switch-tab').forEach(function (tab) {
    tab.onclick = function () {
      document.querySelectorAll('.switch-tab').forEach(function (x) { x.classList.remove('active'); });
      document.querySelectorAll('.switch-panel').forEach(function (x) { x.classList.add('hidden'); });
      tab.classList.add('active');
      $(tab.dataset.panel).classList.remove('hidden');
      if (tab.dataset.panel === 'treasuryPanel') { if (mainChart) mainChart.resize(); if (taxChart) taxChart.resize(); }
    };
  });

  document.querySelectorAll('#rangeBar .range-btn').forEach(function (button) {
    button.onclick = function () {
      currentRange = button.dataset.r;
      rangeModeLast = defaultRange();
      syncRangeButtons();
      renderMainChart(); renderTaxChart();
    };
  });
  ['customStart', 'customEnd'].forEach(function (id) {
    $(id).onchange = function () { renderMainChart(); renderTaxChart(); };
  });

  function roleColor(role) { return role === 'active' ? '#e63946' : role === 'secondary' ? '#2f6fad' : '#16845b'; }
  function roleTitle(item) { return item.role === 'active' && item.ordinal > 1 ? '活跃券 ' + item.ordinal : item.role_label; }

  function metric(label, value) {
    return '<span>' + label + '<b>' + value + '</b></span>';
  }

  function renderDashboard() {
    if (!DASH.as_of_date) {
      $('headMeta').textContent = '暂无缓存数据';
      $('roleGrid').innerHTML = '<div class="empty-state">暂无数据</div>';
      return;
    }
    $('headMeta').textContent = '估值截至 ' + DASH.as_of_date + ' · 成交截至 ' + (DASH.quote_date || '--') + ' · 候选券 ' + DASH.candidate_count + ' 只';
    $('ruleNote').textContent = '活跃券估值差≤' + DASH.rules.active_yield_gap_bp + 'bp 并列 · 次活跃券取最新发行券 · 次次活跃券优先新发券，无新发券时取其余券中发行日新、成交量大者';
    var html = '';
    (DASH.roles || []).forEach(function (item) {
      var b = item.bond;
      var metrics = metric('剩余期限', fmt(b.remaining_years, 2) + 'Y') +
        metric('发行日', esc(b.issue_date || '--')) +
        metric('存量规模', fmt(b.outstanding_amount, 0) + ' 亿元') +
        metric('发行次数', fmt(b.reissue_count, 0) + ' 次');
      if (item.role === 'active' && item.active_since) metrics += metric('成为活跃券起始日期', esc(item.active_since));
      html += '<article class="role-card" style="--role:' + roleColor(item.role) + '">' +
        '<div class="role-tag">' + esc(roleTitle(item)) + '</div><h3>' + esc(b.short_name) + '</h3><div class="bond-code">' + esc(b.code) + ' · 到期 ' + esc(b.maturity_date) + '</div>' +
        '<div class="yield-row"><b>' + fmt(b.valuation_yield, 4) + '</b><span>% 中债估值</span></div>' +
        '<div class="role-metrics">' + metrics + '</div></article>';
    });
    $('roleGrid').innerHTML = html || '<div class="empty-state">未形成角色券</div>';
    renderPairRows();
  }

  /* ------- 左券-右券构建器：每条利差一行，改动即时生效 ------- */
  function optionsHtml(selected) {
    var roleHtml = '<optgroup label="角色">' + ROLE_OPTS.map(function (o) {
      return '<option value="' + o[0] + '"' + (selected === o[0] ? ' selected' : '') + '>' + o[1] + '</option>';
    }).join('') + '</optgroup>';
    var bondHtml = (DASH.selectable_bonds || []).map(function (b) {
      return '<option value="' + esc(b.code) + '"' + (b.code === selected ? ' selected' : '') + '>' + esc(b.short_name) + ' (' + esc(b.code) + ')</option>';
    }).join('');
    return roleHtml + '<optgroup label="具体债券">' + bondHtml + '</optgroup>';
  }
  function defaultPairs() {
    pairs = [['__active', '__secondary']];
  }
  function renderPairRows() {
    var wrap = $('pairRows'); wrap.innerHTML = '';
    pairs.forEach(function (pair, index) {
      var row = document.createElement('div'); row.className = 'pair-row';
      row.innerHTML =
        '<i class="pair-dot" style="background:' + pairColors[index % MAX_PAIRS] + '"></i>' +
        '<label><span>左券（被减数）</span><select data-index="' + index + '" data-side="0">' +
        '<option value="">-- 请选择 --</option>' + optionsHtml(pair[0]) + '</select></label>' +
        '<span class="minus-mark">−</span>' +
        '<label><span>右券（减数）</span><select data-index="' + index + '" data-side="1">' +
        '<option value="">-- 请选择 --</option>' + optionsHtml(pair[1]) + '</select></label>' +
        '<button type="button" class="remove-pair" title="移除该利差">×</button>';
      row.querySelectorAll('select').forEach(function (sel) {
        sel.onchange = function () {
          var i = Number(sel.dataset.index), side = Number(sel.dataset.side);
          var other = pairs[i][1 - side];
          if (!sel.value || sel.value === other) { toast('请选择两只不同的债券'); renderPairRows(); return; }
          if (pairs.some(function (p, j) { return j !== i && p[0] === pairs[i][0] && p[1] === pairs[i][1]; })) { renderPairRows(); return; }
          pairs[i][side] = sel.value;
          syncDefaultRange();
          loadSeries();
        };
      });
      row.querySelector('.remove-pair').onclick = function () {
        if (pairs.length <= 1) { toast('至少保留一条利差'); return; }
        pairs.splice(index, 1); renderPairRows(); syncDefaultRange(); loadSeries();
      };
      wrap.appendChild(row);
    });
  }
  $('addPair').onclick = function () {
    if (!pairs.length) { renderPairRows(); return; }
    if (pairs.length >= MAX_PAIRS) { toast('最多同时叠加' + MAX_PAIRS + '条利差'); return; }
    pairs.push(['', '']);
    renderPairRows();
  };

  function validPairs() {
    return pairs.filter(function (p) { return p[0] && p[1] && p[0] !== p[1]; });
  }
  function loadSeries() {
    if (!DASH || !DASH.as_of_date) return Promise.resolve();
    var q = new URLSearchParams(); q.set('range', 'all');
    validPairs().forEach(function (pair) { q.append('pair', pair[0] + ':' + pair[1]); });
    return Promise.all([
      fetch('/bond-switch/api/series?' + q).then(function (r) { return r.json(); }),
      fetch('/bond-switch/api/tax-spread?range=all').then(function (r) { return r.json(); })
    ]).then(function (values) { SERIES = values[0]; TAX = values[1]; buildRolePicks(); renderMainChart(); renderTaxChart(); });
  }
  function buildRolePicks() {
    rolePicks = {};
    (SERIES.bonds || []).forEach(function (b) { if (b.role_bonds) rolePicks[b.code] = b.role_bonds; });
  }
  /* 悬停提示：列出当日活跃券/次活跃券/次次活跃券实际对应的个券 */
  function rolePickHtml(date) {
    var hits = [];
    ROLE_OPTS.forEach(function (o) {
      var pick = rolePicks[o[0]] && rolePicks[o[0]][date];
      if (pick && pick.name) hits.push('<span style="color:#64748b">' + o[1] + '：</span>' + esc(pick.name));
    });
    return hits.length
      ? '<div style="margin-top:5px;border-top:1px solid #e5e7eb;padding-top:4px;font-size:11px">' + hits.join('<br>') + '</div>'
      : '';
  }
  /* 光标悬停的日期一律只显示到日 */
  function tsToDate(value) {
    var v = typeof value === 'object' && value ? value.value : value;
    var t = new Date(v);
    if (isNaN(t.getTime())) return String(v || '');
    return t.getFullYear() + '-' + String(t.getMonth() + 1).padStart(2, '0') + '-' + String(t.getDate()).padStart(2, '0');
  }
  function tooltipHtml(params, extraForDate) {
    var html = '', date = null;
    (params || []).forEach(function (p) {
      var d = p.data && p.data[0];
      if (!date && d) date = d;
      var unit = seriesUnit[p.seriesName] || '';
      var v = p.data && p.data[1];
      html += '<div>' + p.marker + ' ' + esc(p.seriesName) + ' <b>' + (v == null ? '--' : Number(v).toFixed(2) + unit) + '</b></div>';
    });
    if (!html) return '';
    return '<div style="font-weight:600;margin-bottom:3px">' + esc(date ? tsToDate(date) : '') + '</div>' + html +
      (extraForDate && date ? extraForDate(date) : '');
  }

  function loadDashboard() {
    return fetch('/bond-switch/api/dashboard').then(function (r) { return r.json(); }).then(function (data) {
      DASH = data;
      if (!pairs.length) defaultPairs();
      renderDashboard(); syncDefaultRange(); return loadSeries();
    });
  }

  /* ------- 时间窗口：角色模式默认近1年，具体券模式默认自两券均有估值起 ------- */
  function allDates() {
    var set = {};
    (SERIES.bonds || []).concat(SERIES.spreads || []).forEach(function (s) { s.dates.forEach(function (d) { set[d] = true; }); });
    return Object.keys(set).sort();
  }
  /* ISO日期字符串按字典序比较；Math.min会把日期串转成数字得到NaN，不能用它取最小日期 */
  function minDate(arr) {
    return arr.length ? arr.slice().sort()[0] : null;
  }
  function spreadsStart() {
    return minDate((SERIES.spreads || []).map(function (s) { return s.dates.length ? s.dates[0] : null; }).filter(Boolean));
  }
  /* “自发行时”：以每条利差中较晚发行的券的发行日为锚点；角色虚拟券退回其首个数据日 */
  function issueStart() {
    var anchors = [];
    validPairs().forEach(function (pair) {
      var refs = pair.map(function (code) {
        var b = (SERIES.bonds || []).filter(function (x) { return x.code === code; })[0];
        if (!b) return null;
        if (b.issue_date) return b.issue_date;
        return b.dates.length ? b.dates[0] : null;
      }).filter(Boolean);
      if (!refs.length) return;
      anchors.push(refs.sort()[refs.length - 1]);
    });
    if (!anchors.length) return spreadsStart();
    return minDate(anchors);
  }
  function calendarStart(days) {
    var dates = allDates();
    if (!dates.length) return null;
    var last = new Date(dates[dates.length - 1] + 'T00:00:00');
    last.setDate(last.getDate() - days);
    return last.toISOString().slice(0, 10);
  }
  function visibleWindow(startDate, endDate) {
    if (currentRange === 'custom') {
      return { start: $('customStart').value || null, end: $('customEnd').value || null };
    }
    var fixed = { '3m': 92, '6m': 184, '1y': 366 }[currentRange];
    if (fixed) return { start: calendarStart(fixed), end: null };
    return { start: startDate || null, end: endDate || null };
  }
  function sliceSeries(series, win) {
    var out = [];
    for (var i = 0; i < series.dates.length; i++) {
      var d = series.dates[i];
      if ((!win.start || d >= win.start) && (!win.end || d <= win.end)) out.push([d, series.values[i]]);
    }
    return out;
  }

  function maWindows() {
    return Array.prototype.slice.call(document.querySelectorAll('.ma-toggles input:checked')).map(function (x) { return x.value; });
  }
  document.querySelectorAll('.ma-toggles input').forEach(function (x) { x.onchange = function () { renderMainChart(); renderTaxChart(); }; });

  /* 利差在上方、估值收益率在下方 */
  function renderMainChart() {
    if (!SERIES) return;
    if (!mainChart) { mainChart = echarts.init($('bondSpreadChart')); window.addEventListener('resize', function () { mainChart.resize(); }); }
    var windows = maWindows(), chartSeries = [];
    seriesUnit = {};
    var win = visibleWindow(issueStart(), null);
    (SERIES.spreads || []).forEach(function (spread, index) {
      var color = pairColors[pairs.map(function (p) { return p.join(':'); }).indexOf(spread.key)];
      if (!color) color = pairColors[index % MAX_PAIRS];
      seriesUnit[spread.name] = ' bp';
      chartSeries.push({ name: spread.name, type: 'line', xAxisIndex: 0, yAxisIndex: 0, showSymbol: false, connectNulls: true, lineStyle: { width: 2, color: color }, itemStyle: { color: color }, data: sliceSeries(spread, win) });
      windows.forEach(function (w) {
        seriesUnit[spread.name + ' ' + w + 'D利差均值'] = ' bp';
        chartSeries.push({ name: spread.name + ' ' + w + 'D利差均值', type: 'line', xAxisIndex: 0, yAxisIndex: 0, showSymbol: false, connectNulls: true, lineStyle: { width: 1.1, type: 'dotted', color: color, opacity: .6 }, data: dateAligned(spread.dates, spread.ma[w], win) });
      });
    });
    (SERIES.bonds || []).forEach(function (bond, index) {
      var color = bondColors[index % bondColors.length];
      seriesUnit[bond.name + ' 估值'] = ' %';
      chartSeries.push({ name: bond.name + ' 估值', type: 'line', xAxisIndex: 1, yAxisIndex: 1, showSymbol: false, connectNulls: true, lineStyle: { width: 1.7, color: color }, itemStyle: { color: color }, data: sliceSeries(bond, win) });
      windows.forEach(function (w) {
        seriesUnit[bond.name + ' ' + w + 'D估值均值'] = ' %';
        chartSeries.push({ name: bond.name + ' ' + w + 'D估值均值', type: 'line', xAxisIndex: 1, yAxisIndex: 1, showSymbol: false, connectNulls: true, lineStyle: { width: 1, type: 'dashed', color: color, opacity: .45 }, data: dateAligned(bond.dates, bond.ma[w], win) });
      });
    });
    mainChart.setOption({
      animation: false, backgroundColor: '#fff',
      legend: { type: 'scroll', top: 0, textStyle: { fontSize: 10 } },
      tooltip: { trigger: 'axis', axisPointer: { type: 'cross' }, formatter: function (params) { return tooltipHtml(params, rolePickHtml); } },
      grid: [{ left: 58, right: 35, top: 58, height: '34%' }, { left: 58, right: 35, top: '58%', height: '27%' }],
      xAxis: [
        { type: 'time', gridIndex: 0, axisLabel: { show: false }, axisPointer: { label: { formatter: tsToDate } } },
        { type: 'time', gridIndex: 1, axisPointer: { label: { formatter: tsToDate } } }
      ],
      yAxis: [
        { type: 'value', scale: true, gridIndex: 0, name: '利差 bp', axisLabel: { formatter: '{value}bp' }, splitLine: { lineStyle: { color: '#f1f5f9' } } },
        { type: 'value', scale: true, gridIndex: 1, name: '估值 %', axisLabel: { formatter: '{value}%' }, splitLine: { lineStyle: { color: '#f1f5f9' } } }
      ],
      dataZoom: [{ type: 'inside', xAxisIndex: [0, 1] }, { type: 'slider', xAxisIndex: [0, 1], height: 18, bottom: 10 }],
      series: chartSeries
    }, true);
  }
  function dateData(dates, values) { return dates.map(function (d, i) { return [d, values[i]]; }); }
  function dateAligned(dates, values, win) {
    var out = [];
    for (var i = 0; i < dates.length; i++) {
      var d = dates[i];
      if ((!win.start || d >= win.start) && (!win.end || d <= win.end)) out.push([d, values ? values[i] : null]);
    }
    return out;
  }

  function renderTaxChart() {
    if (!taxChart) { taxChart = echarts.init($('taxSpreadChart')); window.addEventListener('resize', function () { taxChart.resize(); }); }
    if (!TAX || !TAX.available) {
      $('taxCurrent').textContent = '--';
      taxChart.clear(); taxChart.setOption({ title: { text: TAX && TAX.reason || '暂无数据', left: 'center', top: 'middle', textStyle: { color: '#94a3b8', fontSize: 13, fontWeight: 400 } } }); return;
    }
    var win = visibleWindow(null, null);
    var points = [], latest = null;
    for (var i = TAX.dates.length - 1; i >= 0; i--) {
      if ((!win.end || TAX.dates[i] <= win.end)) { if (latest == null) latest = TAX.values[i]; }
      if ((!win.start || TAX.dates[i] >= win.start) && (!win.end || TAX.dates[i] <= win.end)) points.unshift([TAX.dates[i], TAX.values[i]]);
    }
    if (!points.length) latest = null;
    $('taxCurrent').innerHTML = latest == null ? '--' : fmt(latest, 2) + '<small>bp</small>';
    $('taxCurrent').title = TAX.name || '';
    if (TAX.latest_tertiary) $('taxCurrent').title = TAX.latest_tertiary.date + ' 次次活跃券：' + TAX.latest_tertiary.name;
    var chartSeries = [{ name: TAX.name, type: 'line', showSymbol: false, connectNulls: true, lineStyle: { width: 2, color: '#b45309' }, itemStyle: { color: '#b45309' }, data: points }];
    maWindows().forEach(function (w) {
      chartSeries.push({ name: w + 'D均值', type: 'line', showSymbol: false, connectNulls: true, lineStyle: { width: 1, type: 'dashed', opacity: .55, color: '#b45309' }, data: dateAligned(TAX.dates, TAX.ma[w], win) });
    });
    taxChart.setOption({
      animation: false, legend: { top: 0 },
      tooltip: { trigger: 'axis', formatter: function (params) {
        params = params || [];
        var p0 = params[0] || {};
        var date = p0.data && p0.data[0];
        var v = p0.data ? p0.data[1] : null;
        var pick = TAX && TAX.tertiary_by_date && date ? TAX.tertiary_by_date[date] : null;
        var label = pick ? (pick + ' - ' + ((TAX && TAX.tax_name) || '免税债券')) : ((TAX && TAX.name) || '免税利差');
        var html = '<div style="font-weight:600;margin-bottom:3px">' + esc(date ? tsToDate(date) : '') + '</div>';
        html += '<div>' + (p0.marker || '') + ' ' + esc(label) + ' <b>' + (v == null ? '--' : Number(v).toFixed(2) + ' bp') + '</b></div>';
        return html;
      } },
      grid: { left: 58, right: 25, top: 38, bottom: 48 }, xAxis: { type: 'time', axisPointer: { label: { formatter: tsToDate } } }, yAxis: { type: 'value', scale: true, name: 'bp', splitLine: { lineStyle: { color: '#f1f5f9' } } }, dataZoom: [{ type: 'inside' }, { type: 'slider', height: 16, bottom: 8 }], series: chartSeries }, true);
  }

  loadDashboard().catch(function () { toast('新老券模块数据加载失败'); });
})();
