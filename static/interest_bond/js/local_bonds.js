(function () {
  'use strict';
  var loaded = false, loading = false, poll = null;
  var charts = {}, chartRequests = {}, chartRanges = {};
  var status = document.getElementById('localStatus');
  function esc(v) { return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) { return {'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]; }); }
  function fmt(v, n) { return v == null ? '—' : Number(v).toFixed(n == null ? 2 : n); }
  function bp(v) { return v == null ? '—' : (v > 0 ? '+' : '') + fmt(v) + ' bp'; }
  function range(v, unit) { return v ? fmt(v[0], unit === '%' ? 4 : 2) + ' ～ ' + fmt(v[1], unit === '%' ? 4 : 2) + unit : '—'; }
  function date(v) { return v ? v.slice(0, 4) + '-' + v.slice(4, 6) + '-' + v.slice(6, 8) : '—'; }
  function bondName(b) { return b ? esc(b.short_name) + '<span class="local-bond-code">' + esc(b.code) + '.IB</span>' : '—'; }
  function table(headers, body, cls) { return '<div class="local-table-scroll"><table class="local-table ' + (cls || '') + '"><thead><tr>' + headers.map(function (h) { return '<th>' + esc(h) + '</th>'; }).join('') + '</tr></thead><tbody>' + body + '</tbody></table></div>'; }
  function details(provinces, kind) {
    var reason = kind === 'inactive' ? '无符合活跃度及久期匹配条件的券' : '无符合期限、税制且近5日有成交的券';
    return table(['省份 / 发行地区', '选用债券 / 代码', '规模（亿元）', '首次发行日', '剩余期限', '修正久期', '中债估值', '近5日平均笔数'], provinces.map(function (r) {
      var b = r.bond;
      if (!b) return '<tr><td>' + esc(r.province) + '</td><td colspan="7" class="local-missing">' + reason + '</td></tr>';
      return '<tr><td>' + esc(r.province) + '</td><td class="local-bond-name" title="' + esc(b.full_name) + '">' + bondName(b) + '</td><td>' + fmt(b.outstanding_amount) + '</td><td>' + date(b.issue_date) + '</td><td>' + fmt(b.remaining_years) + '年</td><td>' + fmt(b.duration) + '</td><td>' + fmt(b.valuation_yield, 4) + '%</td><td>' + fmt(b.avg_deals_5d, 0) + '</td></tr>';
    }).join(''), 'local-detail-table');
  }
  function render(data) {
    Object.keys(charts).forEach(function (key) { charts[key].dispose(); });
    charts = {}; chartRequests = {};
    var benchmark = data.benchmark;
    document.getElementById('localComparisons').innerHTML = '<section class="card local-card"><div class="section-title-row compact"><div><h2>区域利差概览</h2></div></div><div class="local-comparison-grid">' + data.comparisons.map(function (g) {
      return '<details class="local-comparison" data-group="' + esc(g.key) + '"><summary><span>' + esc(g.label) + '</span><span class="local-expand">展开走势与省份</span><div class="local-spread-values"><div><span>活跃 − 不活跃</span><strong>' + bp(g.liquidity_bp) + '</strong></div><div><span>活跃新税券 − 免税券</span><strong>' + bp(g.tax_bp) + '</strong></div></div></summary><div class="local-comparison-detail"><div class="local-history-toolbar"><h3>当前选券历史利差（bp）</h3><div class="range-bar">' + [['1m', '1月'], ['3m', '3月'], ['6m', '6月'], ['1y', '1年']].map(function (item) { return '<button type="button" class="range-btn' + (item[0] === (chartRanges[g.key] || '3m') ? ' active' : '') + '" data-range="' + item[0] + '">' + item[1] + '</button>'; }).join('') + '</div></div><div class="local-history-status" role="status"></div><div class="local-history-chart" id="localHistory-' + esc(g.key) + '"></div>' + table(['省份', '活跃新税券', '不活跃新税券', '免税券', '活跃−不活跃', '新税−免税'], g.details.map(function (r) {
        return '<tr><td>' + esc(r.province) + '</td><td>' + bondName(r.active) + '</td><td>' + bondName(r.inactive) + '</td><td>' + bondName(r.exempt) + '</td><td>' + bp(r.liquidity_bp) + '</td><td>' + bp(r.tax_bp) + '</td></tr>';
      }).join('')) + '</div></details>';
    }).join('') + '</div></section>';
    document.querySelectorAll('.local-comparison').forEach(function (panel) {
      var group = panel.dataset.group;
      panel.addEventListener('toggle', function () {
        if (panel.open) { if (charts[group]) charts[group].resize(); else loadHistory(panel); }
        Object.keys(charts).forEach(function (key) { charts[key].resize(); });
      });
      panel.querySelectorAll('[data-range]').forEach(function (button) {
        button.onclick = function () {
          chartRanges[group] = button.dataset.range;
          panel.querySelectorAll('[data-range]').forEach(function (b) { b.classList.toggle('active', b === button); });
          loadHistory(panel);
        };
      });
    });
    var benchmarkYield = benchmark && benchmark.valuation_yield;
    var benchmarkHeader = 'vs 30Y国债' + (benchmarkYield == null ? '' : '（' + fmt(benchmarkYield, 4) + '%）');
    document.getElementById('localTables').innerHTML = data.tables.map(function (t) {
      var rows = t.groups.map(function (g) {
        var id = 'local-' + t.key + '-' + g.key;
        var values = g.valuation_range;
        var width = values ? (values[1] - values[0]) * 100 : null;
        var spread = values && benchmarkYield != null ? values.map(function (v) { return (v - benchmarkYield) * 100; }) : null;
        return '<tr><td><button type="button" class="local-group-toggle" aria-expanded="false" aria-controls="' + id + '"><span class="local-chevron">▶</span> ' + esc(g.label) + '</button></td><td>' + range(values, '%') + '</td><td>' + fmt(width) + (width == null ? '' : ' bp') + '</td><td>' + range(spread, ' bp') + '</td><td>' + fmt(g.avg_deals_5d, 0) + '</td></tr><tr id="' + id + '" hidden><td colspan="5" class="local-detail-cell">' + details(g.details, t.key) + '</td></tr>';
      }).join('');
      return '<section class="card local-card"><div class="section-title-row compact"><h2>' + esc(t.title) + '</h2></div>' + table(['区域分类', '估值收益率区间', '区间宽度', benchmarkHeader, '近5日平均成交笔数'], rows) + '</section>';
    }).join('');
    document.querySelectorAll('.local-group-toggle').forEach(function (button) {
      button.onclick = function () {
        var expanded = button.getAttribute('aria-expanded') === 'true';
        button.setAttribute('aria-expanded', String(!expanded));
        document.getElementById(button.getAttribute('aria-controls')).hidden = expanded;
      };
    });
  }
  async function loadHistory(panel) {
    var group = panel.dataset.group, request = (chartRequests[group] || 0) + 1;
    chartRequests[group] = request;
    var node = panel.querySelector('.local-history-status');
    var chart = charts[group] || (charts[group] = echarts.init(panel.querySelector('.local-history-chart')));
    chart.showLoading({text: '加载中', color: '#1f4e8c'});
    node.textContent = '';
    try {
      var response = await fetch('/bond-switch/api/local-series?group=' + encodeURIComponent(group) + '&range=' + (chartRanges[group] || '3m'));
      if (!response.ok) throw new Error('接口返回 ' + response.status);
      var data = await response.json();
      if (chartRequests[group] !== request || !panel.isConnected) return;
      var available = data.series.some(function (s) { return s.values.some(function (v) { return v != null; }); });
      node.textContent = data.history_error ? '历史数据更新异常，显示已有记录。' : !available ? '该区间暂无有效配对数据。' : data.dates.length < 2 ? '历史数据尚未补齐，当前仅有单日记录。' : '';
      chart.setOption({
        color: ['#1f4e8c', '#16845b'],
        tooltip: {trigger: 'axis', formatter: function (items) {
          if (!items.length) return '';
          return esc(items[0].axisValueLabel) + items.map(function (item) {
            var series = data.series[item.seriesIndex];
            return '<br>' + item.marker + esc(series.name) + '：' + bp(series.values[item.dataIndex]) + '（' + series.pair_counts[item.dataIndex] + '组配对）';
          }).join('');
        }},
        legend: {top: 5, textStyle: {fontSize: 13}},
        grid: {left: 65, right: 65, top: 65, bottom: 65},
        xAxis: {type: 'category', boundaryGap: false, data: data.dates.map(date)},
        yAxis: [
          {type: 'value', name: '活跃−不活跃(bp)', scale: true, nameTextStyle: {color: '#1f4e8c', fontSize: 13}, axisLabel: {color: '#1f4e8c'}, splitLine: {lineStyle: {color: '#e8edf4'}}},
          {type: 'value', name: '新税−免税(bp)', scale: true, nameTextStyle: {color: '#16845b', fontSize: 13}, axisLabel: {color: '#16845b'}, splitLine: {show: false}}
        ],
        dataZoom: [{type: 'inside'}, {type: 'slider', bottom: 5, height: 20}],
        series: data.series.map(function (s, i) { return {name: s.name, type: 'line', yAxisIndex: i, data: s.values, connectNulls: false, showSymbol: true, symbolSize: 4, lineStyle: {width: 2}}; })
      }, true);
    } catch (err) {
      if (chartRequests[group] === request && panel.isConnected) { chart.clear(); node.textContent = '走势图加载失败：' + err.message; }
    } finally { if (chartRequests[group] === request && panel.isConnected) chart.hideLoading(); }
  }
  window.addEventListener('resize', function () { Object.keys(charts).forEach(function (key) { charts[key].resize(); }); });
  async function load() {
    if (loading) return;
    loading = true;
    var reload = document.getElementById('localReload');
    reload.disabled = true;
    status.textContent = '正在读取地方债数据…';
    try {
      var response = await fetch('/bond-switch/api/local-dashboard');
      if (!response.ok) throw new Error('接口返回 ' + response.status);
      var data = await response.json();
      loaded = true;
      if (data.as_of_date) render(data);
      var messages = [];
      if (!data.as_of_date) messages.push('暂无地方债快照，请在后台数据维护中更新“新老券利差”。');
      if (data.update_error) messages.push('最近更新异常，保留上次成功数据：' + data.update_error);
      if (data.as_of_date && Date.now() - new Date(date(data.as_of_date) + 'T00:00:00+08:00').getTime() > 4 * 86400000) messages.push('当前为历史快照（' + date(data.as_of_date) + '）。');
      if (data.unmapped_count) messages.push(data.unmapped_count + '只候选券无法识别发行地区，未纳入统计。');
      if (data.update_status && data.update_status.running) {
        messages.push('后台更新中，完成后自动读取。');
        clearTimeout(poll); poll = setTimeout(load, 10000);
      }
      status.textContent = messages.join(' ');
      status.className = messages.length ? 'local-notice' : '';
    } catch (err) {
      status.textContent = '地方债数据加载失败：' + err.message + '。请点击重新加载。';
      status.className = 'local-notice'; loaded = false;
    } finally { loading = false; reload.disabled = false; }
  }
  document.querySelector('[data-panel="localPanel"]').addEventListener('click', function () {
    document.getElementById('headMeta').parentElement.hidden = true;
    if (!loaded) load();
  });
  document.querySelector('[data-panel="treasuryPanel"]').addEventListener('click', function () {
    document.getElementById('headMeta').parentElement.hidden = false;
  });
  document.getElementById('localReload').addEventListener('click', load);
  if (new URLSearchParams(window.location.search).get('market') === 'local') {
    document.querySelector('[data-panel="localPanel"]').click();
  }
})();
