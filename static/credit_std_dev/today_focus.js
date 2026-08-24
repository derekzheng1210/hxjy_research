(function () {
  'use strict';

  function escapeHTML(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (char) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char];
    });
  }

  function latestStats(series, windowSize, sigma) {
    var values = series && Array.isArray(series.values) ? series.values : [];
    var valid = [];
    for (var i = 0; i < values.length; i += 1) {
      if (values[i] !== null && values[i] !== undefined && Number.isFinite(Number(values[i]))) valid.push(i);
    }
    if (!valid.length) return null;
    var lastIndex = valid[valid.length - 1];
    if (lastIndex < windowSize - 1) return null;
    var sample = [];
    for (var j = Math.max(0, lastIndex - windowSize + 1); j <= lastIndex; j += 1) {
      if (values[j] !== null && values[j] !== undefined && Number.isFinite(Number(values[j]))) sample.push(Number(values[j]));
    }
    if (sample.length < Math.max(Math.floor(windowSize * .7), 1)) return null;
    var mean = sample.reduce(function (sum, value) { return sum + value; }, 0) / sample.length;
    var variance = sample.reduce(function (sum, value) { return sum + Math.pow(value - mean, 2); }, 0) / sample.length;
    var std = Math.sqrt(variance);
    var latest = Number(values[lastIndex]);
    var weekIndex = valid.length > 5 ? valid[valid.length - 6] : null;
    return {
      latest: latest,
      upper: mean + sigma * std,
      lower: mean - sigma * std,
      weekly: weekIndex === null ? null : latest - Number(values[weekIndex]),
      date: (series.dates || [])[lastIndex] || ''
    };
  }

  function bp(value) {
    return (Number(value) * 100).toFixed(2) + 'bp';
  }

  function weeklyText(value) {
    if (value === null || value === undefined || !Number.isFinite(value)) return '周度变化暂无数据';
    var amount = Math.abs(value * 100).toFixed(2);
    if (Math.abs(value) < .00005) return '较一周前基本持平';
    return '较一周前' + (value > 0 ? '走阔 ' : '收窄 ') + amount + 'bp';
  }

  function collectFocus(windowSize, sigma) {
    var source = window.SPREAD_DATA;
    var high = [], low = [], latestDate = '';
    if (!source || !source.data) return { high: high, low: low, latestDate: latestDate };
    (source.categories || []).forEach(function (category) {
      (source.tenors_by_category[category] || []).forEach(function (tenor) {
        var series = source.data[category + '_' + tenor];
        var stats = latestStats(series, windowSize, sigma);
        if (!stats) return;
        if (stats.date > latestDate) latestDate = stats.date;
        var item = { category: category, tenor: tenor, stats: stats };
        if (stats.latest > stats.upper) {
          item.distance = stats.latest - stats.upper;
          high.push(item);
        } else if (stats.latest < stats.lower) {
          item.distance = stats.lower - stats.latest;
          low.push(item);
        }
      });
    });
    high.sort(function (a, b) { return b.distance - a.distance; });
    low.sort(function (a, b) { return b.distance - a.distance; });
    return { high: high, low: low, latestDate: latestDate };
  }

  function itemHTML(item, kind) {
    var boundary = kind === 'high' ? item.stats.upper : item.stats.lower;
    var direction = kind === 'high' ? '高于上轨' : '低于下轨';
    var label = item.category + ' · ' + item.tenor;
    return '<button type="button" class="today-focus__item ' + kind + '"' +
      ' data-category="' + escapeHTML(item.category) + '" data-tenor="' + escapeHTML(item.tenor) + '"' +
      ' aria-label="查看' + escapeHTML(label) + '的两倍标准差曲线">' +
      '<span class="today-focus__direction">' + direction + '</span>' +
      '<strong>' + escapeHTML(label) + '</strong>' +
      '<span class="today-focus__detail">最新 <em>' + bp(item.stats.latest) + '</em> · 超出 ' + bp(item.distance) +
      ' · 轨道 ' + bp(boundary) + ' · ' + weeklyText(item.stats.weekly) + '</span>' +
      '<span class="today-focus__view">查看曲线</span></button>';
  }

  function listHTML(focus) {
    var items = focus.high.map(function (item) { return { item: item, kind: 'high' }; })
      .concat(focus.low.map(function (item) { return { item: item, kind: 'low' }; }));
    items.sort(function (a, b) { return b.item.distance - a.item.distance; });
    if (!items.length) {
      return '<div class="today-focus__empty"><strong>暂无突破轨道的利差</strong>' +
        '<span>当前期限均位于统计区间内</span></div>';
    }
    return items.map(function (entry) { return itemHTML(entry.item, entry.kind); }).join('');
  }

  function ensureContainer() {
    var node = document.getElementById('todayFocus');
    if (node) return node;
    var statsBar = document.getElementById('statsBar');
    if (!statsBar) return null;
    node = document.createElement('section');
    node.id = 'todayFocus';
    node.className = 'today-focus';
    node.setAttribute('aria-live', 'polite');
    statsBar.parentNode.insertBefore(node, statsBar);
    return node;
  }

  function render() {
    var node = ensureContainer();
    if (!node) return;
    var windowInput = document.getElementById('maWindow');
    var sigmaInput = document.getElementById('sigmaMultiple');
    var windowSize = Math.max(5, parseInt(windowInput && windowInput.value, 10) || 30);
    var sigma = Math.max(.5, parseFloat(sigmaInput && sigmaInput.value) || 2);
    var focus = collectFocus(windowSize, sigma);
    var total = focus.high.length + focus.low.length;
    node.innerHTML = '<div class="today-focus__head"><div class="today-focus__heading">' +
      '<h2>今日关注</h2><div class="today-focus__meta">数据：' + escapeHTML(focus.latestDate || '—') +
      ' · ' + windowSize + '日均线 ± ' + sigma + 'σ · 周变化比较前5个有效交易日</div></div>' +
      '<div class="today-focus__counts"><span class="today-focus__count">突破 <b>' + total + '</b></span>' +
      '<span class="today-focus__count high">高于上轨 <b>' + focus.high.length + '</b></span>' +
      '<span class="today-focus__count low">低于下轨 <b>' + focus.low.length + '</b></span></div></div>' +
      '<div class="today-focus__list" role="list">' + listHTML(focus) + '</div>';
  }

  function openCurve(button) {
    var category = button.getAttribute('data-category');
    var tenor = button.getAttribute('data-tenor');
    var categorySelect = document.getElementById('categorySelect');
    var tenorChips = document.getElementById('tenorChips');
    var chartNode = document.getElementById('mainChart');
    if (!categorySelect || !tenorChips || !chartNode) return;

    categorySelect.value = category;
    categorySelect.dispatchEvent(new Event('change', { bubbles: true }));
    var matchingChip = Array.prototype.find.call(tenorChips.querySelectorAll('.tenor-chip'), function (chip) {
      return chip.textContent.trim() === tenor;
    });
    if (matchingChip) matchingChip.click();

    document.querySelectorAll('.today-focus__item.is-selected').forEach(function (item) {
      item.classList.remove('is-selected');
    });
    button.classList.add('is-selected');
    var chartContainer = chartNode.closest('.chart-container') || chartNode;
    chartContainer.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function bind() {
    render();
    var node = ensureContainer();
    if (node) {
      node.addEventListener('click', function (event) {
        var button = event.target.closest('.today-focus__item');
        if (button && node.contains(button)) openCurve(button);
      });
    }
    ['maWindow', 'sigmaMultiple'].forEach(function (id) {
      var input = document.getElementById(id);
      if (input) input.addEventListener('change', render);
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bind);
  else bind();
}());
