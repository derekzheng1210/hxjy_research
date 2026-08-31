/* 首页:读取各模块的关键指标填充入口卡片 */
(function () {
  'use strict';

  function el(id) { return document.getElementById(id); }
  function esc(v) {
    return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function chip(value, unit, label, date) {
    return '<span class="stat-chip"><b class="bp">' + value + '</b>' + unit + ' ' + esc(label) +
      (date ? ' <i>' + esc(date) + '</i>' : '') + '</span>';
  }

  fetch('/spread/api/data?range=1y').then(function (r) {
    if (!r.ok) throw new Error();
    return r.json();
  }).then(function (d) {
    var byKey = {};
    (d.spreads || []).forEach(function (s) { byKey[s.key] = s; });
    var a = byKey['tsy30_tsy10'], b = byKey['lgb30_tsy30'];
    if (!a || a.latest == null) {
      el('spreadStats').innerHTML = '<span class="stat-loading">暂无数据,进入模块执行一次更新即可回补历史</span>';
      return;
    }
    var html = chip(a.latest.toFixed(1), 'bp', a.name, a.latest_date);
    if (b && b.latest != null) html += chip(b.latest.toFixed(1), 'bp', b.name, b.latest_date);
    el('spreadStats').innerHTML = html;
  }).catch(function () {
    el('spreadStats').innerHTML = '<span class="stat-loading">利差数据暂不可用</span>';
  });

  fetch('/issuance/api/dashboard').then(function (r) {
    if (!r.ok) throw new Error();
    return r.json();
  }).then(function (d) {
    var pick = function (name) {
      return (d.items || []).filter(function (x) { return x.category === name; })[0];
    };
    var general = pick('一般国债'), special = pick('地方新增专项债');
    var year = d.year ? d.year + '年' : '';
    var html = '';
    if (general && general.progress != null) {
      html += chip((general.progress * 100).toFixed(1) + '%', '', year + '一般国债限额使用', d.as_of_date);
    }
    if (special && special.progress != null) {
      html += chip((special.progress * 100).toFixed(1) + '%', '', year + '新增专项债限额使用', d.as_of_date);
    }
    el('issuanceStats').innerHTML = html ||
      '<span class="stat-loading">暂无数据,进入模块执行一次更新即可回补历史</span>';
  }).catch(function () {
    el('issuanceStats').innerHTML = '<span class="stat-loading">发行数据暂不可用</span>';
  });

  fetch('/bond-switch/api/dashboard').then(function (r) {
    if (!r.ok) throw new Error();
    return r.json();
  }).then(function (d) {
    var active = (d.roles || []).filter(function (x) { return x.role === 'active'; });
    var tertiary = (d.roles || []).filter(function (x) { return x.role === 'tertiary'; })[0];
    if (!active.length) {
      el('bondSwitchStats').innerHTML = '<span class="stat-loading">暂无数据,进入模块执行一次更新即可建立角色券</span>';
      return;
    }
    var html = active.map(function (x) {
      return chip(Number(x.bond.valuation_yield).toFixed(4), '%', x.bond.short_name + ' 中债估值', d.as_of_date);
    }).join('');
    if (tertiary) html += chip(Number(tertiary.bond.volume || 0).toLocaleString('zh-CN'), '万', tertiary.bond.short_name + ' 成交量', d.quote_date);
    el('bondSwitchStats').innerHTML = html;
  }).catch(function () {
    el('bondSwitchStats').innerHTML = '<span class="stat-loading">新老券数据暂不可用</span>';
  });
})();
