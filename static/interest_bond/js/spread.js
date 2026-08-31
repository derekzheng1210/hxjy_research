/* 超长端利差跟踪页:数据加载、图表与表格 */
(function () {
  'use strict';

  var boot = window.SPREAD_BOOT || { spreads: [] };
  var SPREAD_COLORS = {};
  boot.spreads.forEach(function (s) { SPREAD_COLORS[s.key] = s; });

  var curRange = '1y';
  var DATA = null;
  var spreadVisible = {};
  Object.keys(SPREAD_COLORS).forEach(function (k) { spreadVisible[k] = true; });
  var spreadChart = null, yieldChart = null;

  function $(id) { return document.getElementById(id); }
  function toast(msg, ms) {
    var t = $('toast');
    t.textContent = msg;
    t.style.display = 'block';
    setTimeout(function () { t.style.display = 'none'; }, ms || 2600);
  }
  document.querySelectorAll('#rangeBar .range-btn').forEach(function (b) {
    b.onclick = function () {
      document.querySelectorAll('#rangeBar .range-btn').forEach(function (x) { x.classList.remove('active'); });
      b.classList.add('active');
      curRange = b.dataset.r;
      loadData();
    };
  });
  document.querySelectorAll('.tab').forEach(function (t) {
    t.onclick = function () {
      document.querySelectorAll('.tab').forEach(function (x) { x.classList.remove('active'); });
      t.classList.add('active');
      ['table', 'yields'].forEach(function (n) {
        $('tab-' + n).style.display = n === t.dataset.tab ? '' : 'none';
      });
      if (t.dataset.tab === 'yields' && yieldChart) yieldChart.resize();
    };
  });

  function loadData() {
    return fetch('/spread/api/data?range=' + curRange).then(function (r) { return r.json(); }).then(function (data) {
      DATA = data;
      renderHead(); renderStats(); renderSelect(); renderChart(); renderTable(); renderYields();
    });
  }

  function renderHead() {
    var run = DATA.last_run;
    $('headMeta').textContent = run && run.as_of_date ? '数据截至 ' + run.as_of_date : '暂无数据';
  }

  function renderStats() {
    var g = $('statGrid');
    g.innerHTML = '';
    var shown = DATA.spreads.filter(function (s) { return spreadVisible[s.key]; });
    if (!shown.length) {
      g.innerHTML = '<div class="stat-card" style="border-top-color:#cbd5e1"><div class="t">未选择利差</div>' +
        '<div class="chg" style="margin-top:8px">请在上方"利差选择"中勾选一个或多个利差</div></div>';
      return;
    }
    shown.forEach(function (s) {
      var d = document.createElement('div');
      d.className = 'stat-card';
      d.style.setProperty('--accent', s.color);
      function chg(v) {
        if (v == null) return '';
        return v > 0 ? '<span class="up">+' + v.toFixed(1) + '</span>' :
               v < 0 ? '<span class="down">' + v.toFixed(1) + '</span>' : '0.0';
      }
      var mini = '', pctBar = '';
      if (s.stats) {
        mini = '<div class="mini"><span>均值 <b>' + s.stats.mean + '</b></span><span>中位数 <b>' + s.stats.median + '</b></span>' +
          '<span>σ <b>' + s.stats.std + '</b></span>' +
          '<span>区间 <b>' + s.stats.min + '~' + s.stats.max + '</b></span><span>分位 <b>' + s.stats.pct + '%</b></span></div>';
        if (s.latest != null && s.stats.max > s.stats.min) {
          function pos(v) { return Math.max(0, Math.min(100, (v - s.stats.min) / (s.stats.max - s.stats.min) * 100)); }
          pctBar = '<div class="pct-bar" title="当前值在所选区间的位置;刻度为P25/中位数/P75">' +
            '<div class="pct-fill" style="width:' + pos(s.latest).toFixed(1) + '%"></div>' +
            '<i class="tick" style="left:' + pos(s.stats.p25).toFixed(1) + '%"></i>' +
            '<i class="tick" style="left:' + pos(s.stats.median).toFixed(1) + '%"></i>' +
            '<i class="tick" style="left:' + pos(s.stats.p75).toFixed(1) + '%"></i>' +
            '<i class="mark" style="left:' + pos(s.latest).toFixed(1) + '%"></i></div>' +
            '<div class="pct-note">历史分位 <b>' + s.stats.pct + '%</b>(P25 ' + s.stats.p25 + ' / 中位 ' + s.stats.median + ' / P75 ' + s.stats.p75 + ')</div>';
        }
      }
      d.innerHTML = '<div class="t">' + s.name + '<small>' + (s.latest_date || '') + '</small></div>' +
        '<div class="v">' + (s.latest == null ? '--' : s.latest.toFixed(1)) + '<small> bp</small></div>' +
        '<div class="chg">日变动 ' + (chg(s.chg_1d) || '--') + ' · 5日 ' + (chg(s.chg_5d) || '--') + '</div>' + mini + pctBar;
      g.appendChild(d);
    });
  }

  function renderSelect() {
    var el = $('spreadSelect');
    el.querySelectorAll('.sel-chip').forEach(function (c) { c.remove(); });
    var allBtn = $('selAll');
    boot.spreads.forEach(function (s) {
      var chip = document.createElement('span');
      chip.className = 'sel-chip' + (spreadVisible[s.key] ? '' : ' off');
      chip.innerHTML = '<span class="dot" style="background:' + s.color + '"></span>' + s.name;
      chip.onclick = function () {
        spreadVisible[s.key] = !spreadVisible[s.key];
        renderSelect(); renderStats(); renderChart(); renderTable();
      };
      el.insertBefore(chip, allBtn);
    });
  }
  $('selAll').onclick = function () {
    Object.keys(spreadVisible).forEach(function (k) { spreadVisible[k] = true; });
    renderSelect(); renderStats(); renderChart(); renderTable();
  };

  function quantileOf(sortedVals, q) {
    if (!sortedVals.length) return null;
    if (sortedVals.length === 1) return sortedVals[0];
    var pos = q * (sortedVals.length - 1);
    var lo = Math.floor(pos), hi = Math.min(lo + 1, sortedVals.length - 1);
    return sortedVals[lo] + (sortedVals[hi] - sortedVals[lo]) * (pos - lo);
  }
  function parseQuants() {
    return ($('quantInput').value || '').split(',')
      .map(function (x) { return parseFloat(x.trim()); })
      .filter(function (v) { return isFinite(v) && v >= 0 && v <= 100; });
  }
  function auxLineItems(s) {
    if (!s.values.length) return null;
    var sorted = s.values.slice().sort(function (a, b) { return a - b; });
    function mk(val, label, type, opacity) {
      return {
        yAxis: val,
        lineStyle: { type: type, opacity: opacity },
        label: { show: true, formatter: label + ' ' + val.toFixed(1), position: 'insideEndTop', fontSize: 10, color: s.color }
      };
    }
    var items = [];
    if ($('auxMean').checked && s.stats && s.stats.mean != null) items.push(mk(s.stats.mean, '均值', 'dashed', .5));
    if ($('auxMedian').checked) {
      var med = quantileOf(sorted, .5);
      if (med != null) items.push(mk(med, '中位数', 'dashed', .65));
    }
    if ($('auxQuant').checked) {
      parseQuants().forEach(function (q) {
        var v = quantileOf(sorted, q / 100);
        if (v != null) items.push(mk(v, 'P' + q, 'dotted', .55));
      });
    }
    return items.length ? items : null;
  }

  function renderChart() {
    if (!spreadChart) {
      spreadChart = echarts.init($('spreadChart'));
      window.addEventListener('resize', function () { spreadChart && spreadChart.resize(); });
    }
    var series = [];
    DATA.spreads.forEach(function (s) {
      if (!spreadVisible[s.key]) return;
      var item = {
        name: s.name, type: 'line', showSymbol: false, connectNulls: true,
        lineStyle: { width: 1.8, color: s.color }, itemStyle: { color: s.color },
        data: s.dates.map(function (d, i) { return [d, s.values[i]]; }),
        z: 3
      };
      var aux = auxLineItems(s);
      if (aux) {
        item.markLine = {
          symbol: 'none', silent: true,
          lineStyle: { color: s.color, width: 1 },
          data: aux
        };
      }
      series.push(item);
    });
    spreadChart.setOption({
      animation: false, backgroundColor: '#fff',
      grid: { left: 56, right: 64, top: 30, bottom: 70 },
      tooltip: { trigger: 'axis', valueFormatter: function (v) { return v == null ? '--' : v.toFixed(1) + ' bp'; } },
      legend: { show: false },
      xAxis: { type: 'time', axisLine: { lineStyle: { color: '#cbd5e1' } } },
      yAxis: { scale: true, name: 'bp', axisLabel: { color: '#64748b' }, splitLine: { lineStyle: { color: '#f1f5f9' } } },
      dataZoom: [{ type: 'inside' }, { type: 'slider', height: 18, bottom: 8, borderColor: '#e2e8f0' }],
      series: series
    }, true);
  }
  ['auxMean', 'auxMedian', 'auxQuant'].forEach(function (id) {
    $(id).onchange = renderChart;
  });
  $('quantInput').onchange = renderChart;

  function renderYields() {
    if (!yieldChart) {
      yieldChart = echarts.init($('yieldChart'));
      window.addEventListener('resize', function () { yieldChart && yieldChart.resize(); });
    }
    var series = DATA.yields.map(function (y) {
      return {
        name: y.name, type: 'line', showSymbol: false, connectNulls: true,
        lineStyle: { width: 1.6, color: y.color }, itemStyle: { color: y.color },
        data: y.dates.map(function (d, i) { return [d, y.values[i]]; })
      };
    });
    yieldChart.setOption({
      animation: false, backgroundColor: '#fff',
      grid: { left: 56, right: 20, top: 30, bottom: 50 },
      tooltip: { trigger: 'axis', valueFormatter: function (v) { return v == null ? '--' : v.toFixed(3) + ' %'; } },
      legend: { top: 0, textStyle: { fontSize: 11 } },
      xAxis: { type: 'time', axisLine: { lineStyle: { color: '#cbd5e1' } } },
      yAxis: { scale: true, name: '%', axisLabel: { color: '#64748b' }, splitLine: { lineStyle: { color: '#f1f5f9' } } },
      dataZoom: [{ type: 'inside' }],
      series: series
    }, true);
  }

  function renderTable() {
    var t = $('dataTable');
    var shown = DATA.spreads.filter(function (s) { return spreadVisible[s.key]; });
    if (!shown.length) {
      t.innerHTML = '<tbody><tr><td style="padding:18px;color:#94a3b8">请先在上方勾选要查看的利差</td></tr></tbody>';
      return;
    }
    var names = shown.map(function (s) { return s.name; });
    var map = {};
    shown.forEach(function (s) {
      var m = {};
      s.dates.forEach(function (d, i) { m[d] = s.values[i]; });
      map[s.key] = m;
    });
    var dates = DATA.dates.slice().reverse().slice(0, 120);
    var html = '<thead><tr><th>交易日</th>' + names.map(function (n, i) {
      return '<th style="color:' + shown[i].color + '">' + n + '(bp)</th>';
    }).join('') + '</tr></thead><tbody>';
    dates.forEach(function (d) {
      html += '<tr><td>' + d + '</td>' + shown.map(function (s) {
        var v = map[s.key][d];
        return '<td>' + (v == null ? '--' : v.toFixed(1)) + '</td>';
      }).join('') + '</tr>';
    });
    html += '</tbody>';
    t.innerHTML = html;
  }

  loadData();
})();
