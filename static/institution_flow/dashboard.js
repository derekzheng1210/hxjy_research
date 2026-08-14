/* 机构行为净买入监测 —— 前端逻辑
 * 数据层: /bondflow/api/ (由 Flask 代理至 43.137.12.140:8000)
 */
(function () {
'use strict';

/* ================= 常量与分组（按金融属性分组，便于整组选择） ================= */
var INST_GROUPS = [
  { name: '配置盘', members: ['大型银行', '中小型银行', '保险公司', '理财子公司及理财类产品'] },
  { name: '交易盘', members: ['基金公司及产品', '证券公司', '货币市场基金'] },
  { name: '其他', members: ['其他'] }
];
var BOND_GROUPS = [
  { name: '利率债', members: ['国债', '政金债', '地方政府债'] },
  { name: '信用债', members: ['中期票据', '企业债', '短期和超短期融资券'] },
  { name: '存单及其他', members: ['同业存单', '资产支持证券', '其他'] }
];
var TENOR_GROUPS = [
  { name: '短端', members: ['1年及1年以下', '1-3年'] },
  { name: '中端', members: ['3-5年', '5-7年'] },
  { name: '长端', members: ['7-10年', '10-15年'] },
  { name: '超长端', members: ['15-20年', '20-30年', '30年以上'] }
];
var GROUP_ORDERS = { institution: INST_GROUPS, bond_type: BOND_GROUPS, tenor: TENOR_GROUPS };
var ALLOC_MEMBERS = INST_GROUPS[0].members.slice();
var TRADE_MEMBERS = INST_GROUPS[1].members.slice();
var PALETTE = ['#5470c6', '#91cc75', '#fac858', '#ee6666', '#73c0de', '#3ba272', '#fc8452', '#9a60b4', '#ea7ccc', '#48b0f0', '#ffb980', '#c4ccd3'];
/* 季节性对比历史年份专用调色板：排除红色系，把红色留给当年红线(#f56c6c)，避免相邻年份撞色 */
var SEAS_PALETTE = ['#5470c6', '#91cc75', '#fac858', '#73c0de', '#3ba272', '#fc8452', '#9a60b4', '#ea7ccc', '#48b0f0', '#ffb980', '#c4ccd3'];
var COLOR_TOTAL = '#303133';
var DATA_START = '2019-01-01';

/* ================= 叠加曲线常量 ================= */
var OVL_RATE_CURVES = ['国债', '国开债', '地方债'];
var OVL_CREDIT_CURVES = ['中短票AAA', '中短票AA+', '中短票AA', '大行二级资本债', '股份行二级资本债'];
var OVL_CURVE_TO_SPREAD = {
  '中短票AAA': '中短票AAA-国开', '中短票AA+': '中短票AA+-国开', '中短票AA': '中短票AA-国开',
  '大行二级资本债': '大行二级资本债-国开', '股份行二级资本债': '股份行二级资本债-国开'
};
var OVL_RATE_TENORS = ['1Y', '2Y', '3Y', '5Y', '7Y', '10Y', '15Y', '20Y', '30Y'];
var OVL_CREDIT_TENORS = ['1M', '1Y', '2Y', '3Y', '4Y', '5Y', '6Y', '7Y', '8Y', '9Y', '10Y'];
/* 券种 -> 曲线跟随映射（按 BOND_GROUPS 顺序取第一个命中） */
var OVL_BOND_MAP = {
  '国债': { curve: '国债', proxy: null },
  '政金债': { curve: '国开债', proxy: null },
  '地方政府债': { curve: '地方债', proxy: null },
  '中期票据': { curve: '中短票AAA', proxy: null },
  '企业债': { curve: '中短票AAA', proxy: null },
  '短期和超短期融资券': { curve: '中短票AAA', proxy: null }
};
/* 期限档 -> 曲线期限（利率/信用两套） */
var OVL_TENOR_MAP = {
  rate: { '1年及1年以下': '1Y', '1-3年': '2Y', '3-5年': '5Y', '5-7年': '7Y', '7-10年': '10Y', '10-15年': '15Y', '15-20年': '20Y', '20-30年': '30Y', '30年以上': '30Y' },
  credit: { '1年及1年以下': '1Y', '1-3年': '2Y', '3-5年': '5Y', '5-7年': '7Y', '7-10年': '10Y', '10-15年': null, '15-20年': null, '20-30年': null, '30年以上': null }
};
var COLOR_YIELD = '#1565c0';
var COLOR_SPREAD = '#8e24aa';
var COLOR_BAND = 'rgba(142,36,170,0.10)';

/* ================= 状态 ================= */
var state = {
  tab: 'ts',
  inst: new Set(), bond: new Set(), tenor: new Set(),   // 空 = 全部
  startDate: null, endDate: null, rangeKey: '1Y',
  dim: 'institution', groupMode: 'detail', granularity: 'day', chartType: 'bar',
  extreme: 'clip',                                       // raw | winsor | clip
  sideMode: 'single', selectedDate: null,
  seasGran: 'week', seasMa: 'none', seasYear: '5', seasWinsor: true, seasAnomaly: false,
  domTopN: 3,
  /* 单日机构行为状态 */
  dailyDate: null,
  dailyRowView: 'detail',     // detail | inst_group | market
  dailyColView: 'detail',     // detail | bond_group
  dailyChart: 'matrix',       // matrix | sankey
  /* 叠加曲线状态 */
  ovl: {
    mode: 'yield',            // none | yield | spread | both
    curve: '国债',
    tenor: '10Y',
    follow: true,
    layout: 'overlay',        // overlay | split
    band: false,              // ±2σ 区间总开关
    bandYield: false,         // 收益率2σ（派生：band && mode含yield && yieldData）
    bandSpread: false,        // 利差2σ（派生：band && mode含spread && 信用曲线 && spreadData）
    prevLayout: null,         // 开启2σ前的layout，关闭时恢复
    proxy: null,              // 代理提示文案（如某券种以其它曲线代理）
    tenorMiss: false,         // 所选期限档无对应信用曲线
    colorYield: '#1565c0',    // 收益率曲线颜色（可调，亦作收益率2σ带颜色）
    colorSpread: '#8e24aa',   // 利差曲线颜色（可调，亦作利差2σ带颜色）
    meta: null,               // /api/overlay/meta
    yieldData: null,          // {curve,tenor,dates[],values[]}
    spreadData: null          // {category,tenor,dates[],values[]}
  }
};
var options = null;          // /options/ 返回
var charts = {};             // echarts 实例
var loadedTabs = {};
var mainStore = null;        // 主图当前数据 {cats, names, raw{name:[]}, disp{name:[]}, bounds}
var sideStore = null;

function createRequestGate() {
  var seq = 0;
  var controller = null;
  return {
    begin: function () {
      if (controller) controller.abort();
      seq += 1;
      controller = typeof AbortController === 'function' ? new AbortController() : null;
      return { id: seq, signal: controller ? controller.signal : undefined };
    },
    invalidate: function () {
      if (controller) controller.abort();
      controller = null;
      seq += 1;
    },
    isCurrent: function (id) { return id === seq; },
    finish: function (id) {
      if (id === seq) controller = null;
    }
  };
}

var kpiRequests = createRequestGate();
var mainRequests = createRequestGate();
var seasonalityRequests = createRequestGate();
var dailyRequests = createRequestGate();
var seasonYieldSeq = 0;

/* ================= 工具 ================= */
function $(id) { return document.getElementById(id); }
function fmt(v, digits) {
  if (v == null || !isFinite(v)) return '—';
  var d = digits == null ? (Math.abs(v) >= 100 ? 0 : Math.abs(v) >= 10 ? 1 : 2) : digits;
  return v.toLocaleString('zh-CN', { minimumFractionDigits: d, maximumFractionDigits: d });
}
function fmtSigned(v, digits) { return v == null ? '—' : (v > 0 ? '+' : '') + fmt(v, digits); }
function cls(v) { return v > 0 ? 'pos' : v < 0 ? 'neg' : 'zero'; }
function addDays(dateStr, n) { var d = new Date(dateStr + 'T00:00:00'); d.setDate(d.getDate() + n); return iso(d); }
function addMonths(dateStr, n) { var d = new Date(dateStr + 'T00:00:00'); d.setMonth(d.getMonth() + n); return iso(d); }
function iso(d) { return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); }
function percentile(sortedAsc, p) {
  if (!sortedAsc.length) return null;
  var idx = (sortedAsc.length - 1) * p / 100;
  var lo = Math.floor(idx), hi = Math.ceil(idx);
  if (lo === hi) return sortedAsc[lo];
  return sortedAsc[lo] + (sortedAsc[hi] - sortedAsc[lo]) * (idx - lo);
}
function escapeHtml(s) { return String(s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
function showErr(msg) { var b = $('errBox'); b.textContent = msg; b.style.display = 'block'; clearTimeout(b._t); b._t = setTimeout(function () { b.style.display = 'none'; }, 8000); }
function setLoading(elId, on) { var el = $(elId); if (el) el.classList.toggle('loading-mask', !!on); }
function groupOrder(dim, names) {
  var order = [];
  GROUP_ORDERS[dim].forEach(function (g) { g.members.forEach(function (m) { order.push(m); }); });
  return names.slice().sort(function (a, b) {
    var ia = order.indexOf(a), ib = order.indexOf(b);
    return (ia < 0 ? 999 : ia) - (ib < 0 ? 999 : ib) || String(a).localeCompare(String(b), 'zh-CN');
  });
}

/* ================= API ================= */
function api(path, params, signal) {
  var qs = new URLSearchParams();
  Object.keys(params || {}).forEach(function (k) {
    var v = params[k];
    if (v == null || v === '') return;
    if (Array.isArray(v)) v.forEach(function (x) { qs.append(k, x); });
    else qs.set(k, v);
  });
  var url = (window.INSTITUTION_FLOW_BASE_URL || '/institution-flow') + '/bondflow/api/' + path + (qs.toString() ? '?' + qs.toString() : '');
  return fetch(url, signal ? { signal: signal } : undefined).then(function (r) {
    if (!r.ok) return r.json().catch(function () { return {}; }).then(function (e) { throw new Error(e.error || ('请求失败 HTTP ' + r.status)); });
    return r.json();
  });
}
function currentFilters() {
  return {
    institutions: state.inst.size ? Array.from(state.inst) : options.institutions,
    bond_types: state.bond.size ? Array.from(state.bond) : options.bond_types,
    tenors: state.tenor.size ? Array.from(state.tenor) : options.tenors
  };
}
function filterParams() {
  var f = currentFilters();
  return { institutions: f.institutions, bond_types: f.bond_types, tenors: f.tenors };
}

/* ================= 筛选 UI ================= */
function buildFilterRow(elId, groups, stateSet) {
  var box = $(elId); box.innerHTML = '';
  var hint = document.createElement('span');
  hint.className = 'all-hint'; hint.textContent = '全部';
  function refreshHint() { hint.style.display = stateSet.size ? 'none' : ''; }
  groups.forEach(function (g, gi) {
    var tag = document.createElement('span');
    tag.className = 'group-tag'; tag.textContent = g.name;
    tag.title = '点击整组选择/取消';
    tag.addEventListener('click', function () {
      var allIn = g.members.every(function (m) { return stateSet.has(m); });
      g.members.forEach(function (m) { allIn ? stateSet.delete(m) : stateSet.add(m); });
      renderFilterStates(); onFilterChange();
    });
    box.appendChild(tag);
    g.members.forEach(function (m) {
      var c = document.createElement('span');
      c.className = 'chip'; c.textContent = m; c.dataset.value = m;
      c.addEventListener('click', function () {
        stateSet.has(m) ? stateSet.delete(m) : stateSet.add(m);
        renderFilterStates(); onFilterChange();
      });
      box.appendChild(c);
    });
    if (gi < groups.length - 1) { var sep = document.createElement('span'); sep.className = 'chip-sep'; box.appendChild(sep); }
  });
  box.appendChild(hint);
  box._refreshHint = refreshHint;
}
function renderFilterStates() {
  [['instFilter', state.inst, INST_GROUPS], ['bondFilter', state.bond, BOND_GROUPS], ['tenorFilter', state.tenor, TENOR_GROUPS]].forEach(function (cfg) {
    var box = $(cfg[0]), set = cfg[1], groups = cfg[2];
    box.querySelectorAll('.chip').forEach(function (c) { c.classList.toggle('on', set.has(c.dataset.value)); });
    var tags = box.querySelectorAll('.group-tag');
    groups.forEach(function (g, i) {
      tags[i].classList.toggle('on', g.members.every(function (m) { return set.has(m); }));
    });
    if (box._refreshHint) box._refreshHint();
  });
  renderSummary();
}
function renderSummary() {
  var wrap = $('summaryChips'); wrap.innerHTML = '';
  function addChips(set, allList, label) {
    if (!set.size) return;
    set.forEach(function (v) {
      var s = document.createElement('span');
      s.className = 's-chip';
      s.innerHTML = escapeHtml(v) + ' <i class="x">×</i>';
      s.querySelector('.x').addEventListener('click', function () { set.delete(v); renderFilterStates(); onFilterChange(); });
      wrap.appendChild(s);
    });
  }
  addChips(state.inst, options.institutions); addChips(state.bond, options.bond_types); addChips(state.tenor, options.tenors);
  if (!wrap.children.length) wrap.innerHTML = '<span style="font-size:12px;color:var(--muted)">全部机构 × 全部券种 × 全部期限</span>';
}
function onFilterChange() {
  loadedTabs = {};
  mainRequests.invalidate();
  seasonalityRequests.invalidate();
  dailyRequests.invalidate();
  seasonYieldSeq += 1;
  lastMainResp = null;
  mainStore = null;
  sideStore = null;
  state.selectedDate = null;
  if (charts.mainChart) charts.mainChart.clear();
  if (charts.sideChart) charts.sideChart.clear();
  $('mainStatus').textContent = '';
  $('sideTitle').textContent = '—';
  ovlFollowFilters();   // 跨 Tab 保持跟随一致（季节性页的收益率季节图同样联动）
  syncOvlControls();
  syncSyControls();
  loadKpi();
  loadTab(state.tab);
}

/* ================= 日期 ================= */
function applyQuickRange(key) {
  state.rangeKey = key;
  var end = options.latest_date;
  var map = { '1M': addMonths(end, -1), '3M': addMonths(end, -3), '6M': addMonths(end, -6), '1Y': addMonths(end, -12), 'YTD': end.slice(0, 4) + '-01-01', 'ALL': DATA_START };
  state.startDate = map[key]; state.endDate = end;
  $('startDate').value = state.startDate; $('endDate').value = state.endDate;
  document.querySelectorAll('.quick-btn').forEach(function (b) { b.classList.toggle('on', b.dataset.range === key); });
  onFilterChange();
}
function bindDates() {
  $('startDate').value = state.startDate; $('endDate').value = state.endDate;
  document.querySelectorAll('.quick-btn').forEach(function (b) {
    b.addEventListener('click', function () { applyQuickRange(b.dataset.range); });
  });
  $('applyDateBtn').addEventListener('click', function () {
    var s = $('startDate').value, e = $('endDate').value;
    if (!s || !e) return showErr('请选择完整的起止日期');
    if (s > e) return showErr('开始日期不能晚于结束日期');
    state.startDate = s; state.endDate = e; state.rangeKey = '';
    document.querySelectorAll('.quick-btn').forEach(function (b) { b.classList.remove('on'); });
    onFilterChange();
  });
}

/* ================= 极值处理 ================= */
function calcBounds(matrix) {
  // matrix: 各系列显示值数组的集合
  var nums = [];
  matrix.forEach(function (arr) { arr.forEach(function (v) { if (v != null && isFinite(v)) nums.push(v); }); });
  if (nums.length < 20) return null;
  nums.sort(function (a, b) { return a - b; });
  var lo = percentile(nums, 1), hi = percentile(nums, 99);
  if (lo == null || hi == null || !(hi > lo)) return null;
  if (lo > 0) lo = 0; if (hi < 0) hi = 0;   // 单边数据保留零轴
  return { lo: lo, hi: hi };
}
function clampV(v, b) { return Math.min(Math.max(v, b.lo), b.hi); }
/* 十六进制颜色 -> rgba（±2σ 区间带用） */
function hexA(hex, alpha) {
  var h = String(hex || '').replace('#', '');
  if (h.length !== 6) return 'rgba(142,36,170,' + alpha + ')';
  return 'rgba(' + parseInt(h.slice(0, 2), 16) + ',' + parseInt(h.slice(2, 4), 16) + ',' + parseInt(h.slice(4, 6), 16) + ',' + alpha + ')';
}

/* ================= KPI 快照 ================= */
function loadKpi() {
  var request = kpiRequests.begin();
  var end = options.latest_date;
  var start = addDays(end, -120);
  var f = currentFilters();
  api('dimension/', {
    institutions: options.institutions,   // KPI 口径：全部机构（沿用券种/期限筛选）
    bond_types: f.bond_types, tenors: f.tenors,
    start_date: start, end_date: end, granularity: 'day', dimension: 'institution'
  }, request.signal).then(function (res) {
    if (!kpiRequests.isCurrent(request.id)) return;
    var series = res.data || [];
    if (!series.length || !series[0].series.length) return;
    var dates = series[0].series.map(function (p) { return p.date; });
    var totals = dates.map(function (_, i) {
      return series.reduce(function (s, it) { var v = it.series[i]; return s + (v && v.value != null ? v.value : 0); }, 0);
    });
    var lastIdx = dates.length - 1, lastDate = dates[lastIdx], lastTotal = totals[lastIdx];
    $('kpi1').textContent = fmtSigned(lastTotal) + ' 亿';
    $('kpi1').className = 'k-value ' + cls(lastTotal);
    $('kpi1Label').textContent = '最新日合计净买入（' + lastDate.slice(5) + '）';
    $('kpi1Sub').textContent = '口径：全部机构 × 当前券种/期限筛选';

    // 最新日 最大买/卖机构
    var best = null, worst = null;
    series.forEach(function (it) {
      var p = it.series[lastIdx]; if (!p || p.value == null) return;
      if (!best || p.value > best.v) best = { n: it.name, v: p.value };
      if (!worst || p.value < worst.v) worst = { n: it.name, v: p.value };
    });
    $('kpi2').textContent = best ? best.n : '—'; $('kpi2Sub').textContent = best ? fmtSigned(best.v) + ' 亿' : '';
    $('kpi3').textContent = worst ? worst.n : '—'; $('kpi3Sub').textContent = worst ? fmtSigned(worst.v) + ' 亿' : '';

    // 近5/20日累计
    function cum(n) { var s = 0, c = 0; for (var i = lastIdx; i >= 0 && c < n; i-- , c++) s += totals[i]; return s; }
    var c5 = cum(5), c20 = cum(20);
    $('kpi4').innerHTML = '<span class="' + cls(c5) + '">' + fmtSigned(c5) + '</span> / <span class="' + cls(c20) + '">' + fmtSigned(c20) + '</span> 亿';
    $('kpi4Sub').textContent = '按交易日累计';

    // 配置盘-交易盘分歧（近5日）
    function groupCum(members, n) {
      var s = 0;
      series.forEach(function (it) {
        if (members.indexOf(it.name) < 0) return;
        for (var i = lastIdx, c = 0; i >= 0 && c < n; i-- , c++) { var v = it.series[i]; s += (v && v.value != null ? v.value : 0); }
      });
      return s;
    }
    var diff = groupCum(ALLOC_MEMBERS, 5) - groupCum(TRADE_MEMBERS, 5);
    $('kpi5').textContent = fmtSigned(diff) + ' 亿';
    $('kpi5').className = 'k-value ' + cls(diff);
    $('kpi5Sub').textContent = diff > 0 ? '配置盘相对更积极' : diff < 0 ? '交易盘相对更积极' : '两方力度相当';
  }).catch(function (e) {
    if (!kpiRequests.isCurrent(request.id) || e.name === 'AbortError') return;
    showErr('KPI 加载失败：' + e.message);
  }).finally(function () { kpiRequests.finish(request.id); });
}

/* ================= 叠加曲线模块 ================= */
function ovlIsRate(curve) { return OVL_RATE_CURVES.indexOf(curve) >= 0; }
function ovlTenorsFor(curve) { return ovlIsRate(curve) ? OVL_RATE_TENORS : OVL_CREDIT_TENORS; }
function ovlApi(path, params, signal) {
  var qs = new URLSearchParams();
  Object.keys(params || {}).forEach(function (k) { if (params[k] != null) qs.set(k, params[k]); });
  return fetch((window.INSTITUTION_FLOW_BASE_URL || '/institution-flow') + '/api/overlay/' + path + (qs.toString() ? '?' + qs.toString() : ''), signal ? { signal: signal } : undefined).then(function (r) {
    if (!r.ok) return r.json().catch(function () { return {}; }).then(function (e) { throw new Error(e.error || ('请求失败 HTTP ' + r.status)); });
    return r.json();
  });
}
function setOvlManual() {
  state.ovl.follow = false;
  var chk = $('ovlFollowChk'); if (chk) chk.checked = false;
}
/* asof 对齐：取曲线上 ≤ cat 的最近一个值（cats/dates 均升序，cat 为 YYYY-MM-DD 期末日） */
function asofAlign(dates, values, cats) {
  var res = new Array(cats.length).fill(null);
  var j = -1, n = dates.length;
  for (var i = 0; i < cats.length; i++) {
    while (j + 1 < n && dates[j + 1] <= cats[i]) j++;
    if (j >= 0) res[i] = values[j];
  }
  return res;
}
/* MA30 ± 2σ 滚动计算（与两倍标准差页面同口径） */
function rollingCalc(values, window) {
  var n = values.length;
  var ma = new Array(n).fill(null), std = new Array(n).fill(null);
  var minCount = Math.max(Math.floor(window * 0.7), 1);
  for (var i = window - 1; i < n; i++) {
    var sum = 0, cnt = 0, arr = [];
    for (var j = i - window + 1; j <= i; j++) {
      if (values[j] != null) { sum += values[j]; cnt++; arr.push(values[j]); }
    }
    if (cnt >= minCount) {
      var mean = sum / cnt;
      ma[i] = mean;
      std[i] = Math.sqrt(arr.reduce(function (a, v) { return a + (v - mean) * (v - mean); }, 0) / cnt);
    }
  }
  return { ma: ma, std: std };
}
/* 跟随筛选：券种 → 品种、期限档 → 曲线期限 */
function ovlFollowFilters() {
  var o = state.ovl;
  if (!o.follow) return;
  o.proxy = null; o.tenorMiss = false;
  var mapped = null;
  BOND_GROUPS.forEach(function (g) {
    g.members.forEach(function (m) {
      if (!mapped && state.bond.has(m) && OVL_BOND_MAP[m]) mapped = OVL_BOND_MAP[m];
    });
  });
  if (mapped) {
    var changed = mapped.curve !== o.curve;
    o.curve = mapped.curve;
    o.proxy = mapped.proxy;
    var isRate = ovlIsRate(o.curve);
    // 仅在品种因跟随而切换时调整模式（不覆盖用户后续的手动模式选择）
    if (changed) {
      if (isRate && (o.mode === 'spread' || o.mode === 'both')) o.mode = 'yield';
      if (!isRate && o.mode === 'yield') o.mode = 'both';
    }
  }
  if (state.tenor.size) {
    var grp = ovlIsRate(o.curve) ? 'rate' : 'credit';
    var found = null, miss = false;
    TENOR_GROUPS.forEach(function (g) {
      g.members.forEach(function (m) {
        if (!state.tenor.has(m)) return;
        var t = OVL_TENOR_MAP[grp][m];
        if (t && !found) found = t;
        if (t === null) miss = true;
      });
    });
    if (found) o.tenor = found;
    o.tenorMiss = miss && grp === 'credit';
  }
  var valid = ovlTenorsFor(o.curve);
  if (valid.indexOf(o.tenor) < 0) o.tenor = ovlIsRate(o.curve) ? '10Y' : '3Y';
}
/* 控件构建与同步 */
function buildOvlCurveSel() {
  var sel = $('ovlCurveSel');
  sel.innerHTML = '';
  var meta = state.ovl.meta;
  function mk(label, curves, infoMap) {
    var g = document.createElement('optgroup');
    g.label = label;
    curves.forEach(function (c) {
      var opt = document.createElement('option');
      opt.value = c;
      opt.textContent = c;
      var info = infoMap && infoMap[c];
      if (info && !info.end) opt.textContent += '（无数据）';
      g.appendChild(opt);
    });
    sel.appendChild(g);
  }
  mk('利率（含15/20/30Y）', OVL_RATE_CURVES, meta && meta.rate_curves);
  mk('信用', OVL_CREDIT_CURVES, meta && meta.credit_curves);
}
function renderOvlTenorChips() {
  var o = state.ovl;
  var box = $('ovlTenorChips');
  box.innerHTML = '';
  var avail = null;
  if (o.meta) {
    var info = ovlIsRate(o.curve) ? (o.meta.rate_curves || {})[o.curve] : (o.meta.credit_curves || {})[o.curve];
    avail = info && info.tenors ? info.tenors : null;
  }
  ovlTenorsFor(o.curve).forEach(function (t) {
    var has = !avail || avail.indexOf(t) >= 0;
    var chip = document.createElement('span');
    chip.className = 't-chip' + (t === o.tenor ? ' on' : '') + (has ? '' : ' off');
    chip.textContent = t;
    chip.title = has ? '' : '该期限暂无数据';
    if (has) chip.addEventListener('click', function () {
      if (state.ovl.tenor === t) return;
      state.ovl.tenor = t;
      setOvlManual();
      syncOvlControls();
      ovlRefresh();
    });
    box.appendChild(chip);
  });
}
function syncOvlControls() {
  var o = state.ovl;
  document.querySelectorAll('#ovlModeSeg button').forEach(function (b) {
    b.classList.toggle('active', b.dataset.value === o.mode);
  });
  var sel = $('ovlCurveSel');
  if (sel.options.length) sel.value = o.curve;
  document.querySelectorAll('#ovlLayoutSeg button').forEach(function (b) {
    b.classList.toggle('active', b.dataset.value === o.layout);
  });
  $('ovlFollowChk').checked = o.follow;
  $('ovlBandChk').checked = o.band;
  var isRate = ovlIsRate(o.curve);
  // 派生：band 开启后，按 mode/curve 计算各自是否显示2σ带
  o.bandYield  = o.band && (o.mode === 'yield' || o.mode === 'both')
    && !!o.yieldData && !!(o.yieldData.dates && o.yieldData.dates.length);
  o.bandSpread = o.band && (o.mode === 'spread' || o.mode === 'both')
    && !isRate && !!o.spreadData && !!(o.spreadData.dates && o.spreadData.dates.length);
  document.querySelectorAll('#ovlModeSeg button').forEach(function (b) {
    var dis = isRate && (b.dataset.value === 'spread' || b.dataset.value === 'both');
    b.disabled = dis;
    b.style.opacity = dis ? '0.35' : '';
    b.style.cursor = dis ? 'not-allowed' : '';
    b.title = dis ? '利率品种无信用利差' : '';
  });
  // 2σ开启时锁定分屏：overlay 按钮置灰
  document.querySelectorAll('#ovlLayoutSeg button').forEach(function (b) {
    var lockOverlay = o.band && b.dataset.value === 'overlay';
    b.disabled = lockOverlay;
    b.style.opacity = lockOverlay ? '0.35' : '';
    b.style.cursor = lockOverlay ? 'not-allowed' : '';
    b.title = lockOverlay ? '2σ区间需分屏显示，关闭2σ后可切回叠加' : '';
  });
  renderOvlTenorChips();
}
/* 数据加载 */
function loadOvlData(done, signal) {
  var o = state.ovl;
  function finish() { if (done) done(); }
  if (o.mode === 'none') { o.yieldData = null; o.spreadData = null; finish(); return; }
  // 2σ带遵循当前 mode：yield模式只画收益率2σ，spread/both才画利差2σ，故数据按 mode 加载
  var needYield = o.mode === 'yield' || o.mode === 'both';
  var needSpread = (o.mode === 'spread' || o.mode === 'both') && !ovlIsRate(o.curve);
  var jobs = [];
  if (needYield) {
    jobs.push(ovlApi('yield', { curve: o.curve, tenor: o.tenor }, signal).then(function (d) { o.yieldData = d; }));
  } else o.yieldData = null;
  if (needSpread) {
    jobs.push(ovlApi('spread', { category: OVL_CURVE_TO_SPREAD[o.curve], tenor: o.tenor }, signal).then(function (d) { o.spreadData = d; }));
  } else o.spreadData = null;
  Promise.all(jobs).then(finish).catch(function (e) {
    if (e.name !== 'AbortError') showErr('叠加曲线加载失败：' + e.message);
    finish();
  });
}
function ovlRefresh() {
  if (mainStore) {
    loadOvlData(function () {
      renderMain();
      renderOvlSplit();
      renderOvlSnapshot();
    });
  }
  if (state.tab === 'seas' && $('seasYieldChk').checked) loadSeasYield();
}
/* ±2σ 区间带（基于利差全历史，asof 对齐到主图日期轴） */
function ovlBandSeries(yAxisIndex) {
  var sd = state.ovl.spreadData;
  if (!sd || !sd.dates || !sd.dates.length || !mainStore) return [];
  var calc = rollingCalc(sd.values, 30);
  var n = sd.dates.length;
  var upper = new Array(n).fill(null), lower = new Array(n).fill(null);
  for (var i = 0; i < n; i++) {
    if (calc.ma[i] != null) {
      upper[i] = (calc.ma[i] + 2 * calc.std[i]) * 100;
      lower[i] = (calc.ma[i] - 2 * calc.std[i]) * 100;
    }
  }
  var cats = mainStore.cats;
  var upA = asofAlign(sd.dates, upper, cats);
  var loA = asofAlign(sd.dates, lower, cats);
  var width = upA.map(function (u, i) { return (u != null && loA[i] != null) ? +(u - loA[i]).toFixed(3) : null; });
  var loR = loA.map(function (v) { return v == null ? null : +v.toFixed(3); });
  return [
    { name: '_bandBase', type: 'line', data: loR, stack: '_ovlband', yAxisIndex: yAxisIndex, lineStyle: { width: 0, color: 'transparent' }, symbol: 'none', silent: true, z: 0, tooltip: { show: false } },
    { name: '_bandFill', type: 'line', data: width, stack: '_ovlband', yAxisIndex: yAxisIndex, lineStyle: { width: 0, color: 'transparent' }, areaStyle: { color: hexA(state.ovl.colorSpread, 0.10) }, symbol: 'none', silent: true, z: 0, tooltip: { show: false } }
  ];
}
/* 收益率 ±2σ 区间带（基于收益率全历史，asof 对齐到主图日期轴；单位% 不转bp） */
function ovlYieldBandSeries(yAxisIndex) {
  var yd = state.ovl.yieldData;
  if (!yd || !yd.dates || !yd.dates.length || !mainStore) return [];
  var calc = rollingCalc(yd.values, 30);
  var n = yd.dates.length;
  var upper = new Array(n).fill(null), lower = new Array(n).fill(null);
  for (var i = 0; i < n; i++) {
    if (calc.ma[i] != null) {
      upper[i] = calc.ma[i] + 2 * calc.std[i];   // % 不转bp
      lower[i] = calc.ma[i] - 2 * calc.std[i];
    }
  }
  var cats = mainStore.cats;
  var upA = asofAlign(yd.dates, upper, cats);
  var loA = asofAlign(yd.dates, lower, cats);
  var width = upA.map(function (u, i) { return (u != null && loA[i] != null) ? +(u - loA[i]).toFixed(4) : null; });
  var loR = loA.map(function (v) { return v == null ? null : +v.toFixed(4); });
  return [
    { name: '_ybandBase', type: 'line', data: loR, stack: '_ovlyband', yAxisIndex: yAxisIndex, lineStyle: { width: 0, color: 'transparent' }, symbol: 'none', silent: true, z: 0, tooltip: { show: false } },
    { name: '_ybandFill', type: 'line', data: width, stack: '_ovlyband', yAxisIndex: yAxisIndex, lineStyle: { width: 0, color: 'transparent' }, areaStyle: { color: hexA(state.ovl.colorYield, 0.10) }, symbol: 'none', silent: true, z: 0, tooltip: { show: false } }
  ];
}
/* 曲线快照条 */
function renderOvlSnapshot() {
  var o = state.ovl;
  var el = $('ovlSnapshot');
  if (o.mode === 'none') { el.innerHTML = ''; return; }
  // 数据可能刚加载完，重算派生2σ状态（与 syncOvlControls 同口径）
  o.bandYield  = o.band && (o.mode === 'yield' || o.mode === 'both')
    && !!o.yieldData && !!(o.yieldData.dates && o.yieldData.dates.length);
  o.bandSpread = o.band && (o.mode === 'spread' || o.mode === 'both')
    && !ovlIsRate(o.curve) && !!o.spreadData && !!(o.spreadData.dates && o.spreadData.dates.length);
  var parts = [];
  if (o.proxy) parts.push('<span class="ovl-proxy">⚠ ' + escapeHtml(o.proxy) + '</span>');
  if (o.tenorMiss) parts.push('<span class="ovl-proxy">⚠ 所选期限档超出信用曲线范围（仅至10Y），当前显示 ' + escapeHtml(o.tenor) + '</span>');
  if ((o.mode === 'yield' || o.mode === 'both')) {
    if (o.yieldData && o.yieldData.dates && o.yieldData.dates.length) {
      var d = o.yieldData, n = d.dates.length, last = d.values[n - 1];
      var chg1 = n >= 2 ? (last - d.values[n - 2]) * 100 : null;
      var chg5 = n >= 6 ? (last - d.values[n - 6]) * 100 : null;
      var s = '<b>' + escapeHtml(d.curve) + ' ' + escapeHtml(d.tenor) + '</b> 收益率 <b>' + fmt(last, 4) + '%</b>（' + d.dates[n - 1] + '）';
      if (chg1 != null) s += ' 日变动 <span class="' + (chg1 > 0 ? 'up' : 'down') + '">' + fmtSigned(chg1, 1) + 'bp</span>';
      if (chg5 != null) s += '｜5日 <span class="' + (chg5 > 0 ? 'up' : 'down') + '">' + fmtSigned(chg5, 1) + 'bp</span>';
      if (o.bandYield) {
        var ycalc = rollingCalc(d.values, 30);
        if (ycalc.ma[n - 1] != null) {
          var yup = ycalc.ma[n - 1] + 2 * ycalc.std[n - 1], ylo = ycalc.ma[n - 1] - 2 * ycalc.std[n - 1];
          s += last > yup ? '，<span class="up">高于+2σ</span>' : last < ylo ? '，<span class="down">低于-2σ</span>' : '，±2σ区间内';
        }
      }
      parts.push(s);
    } else {
      parts.push('<span class="ovl-proxy">' + escapeHtml(o.curve) + ' ' + escapeHtml(o.tenor) + ' 收益率暂无数据</span>');
    }
  }
  if ((o.mode === 'spread' || o.mode === 'both') && !ovlIsRate(o.curve)) {
    if (o.spreadData && o.spreadData.dates && o.spreadData.dates.length) {
      var sd = o.spreadData, m = sd.dates.length;
      var lastBp = sd.values[m - 1] * 100;
      var sorted = sd.values.filter(function (v) { return v != null; }).sort(function (a, b) { return a - b; });
      var below = sorted.filter(function (v) { return v <= sd.values[m - 1]; }).length;
      var pct = sorted.length ? below / sorted.length * 100 : null;
      var posText = '';
      var calc = rollingCalc(sd.values, 30);
      if (calc.ma[m - 1] != null) {
        var up = calc.ma[m - 1] + 2 * calc.std[m - 1], lo = calc.ma[m - 1] - 2 * calc.std[m - 1];
        posText = sd.values[m - 1] > up ? '，<span class="up">高于+2σ</span>' : sd.values[m - 1] < lo ? '，<span class="down">低于-2σ</span>' : '，±2σ区间内';
      }
      var s2 = '利差 <b>' + fmt(lastBp, 1) + 'bp</b>（' + sd.dates[m - 1] + '）';
      if (pct != null) s2 += ' 2022年来分位 <b>' + fmt(pct, 1) + '%</b>' + posText;
      parts.push(s2);
    } else {
      parts.push('<span class="ovl-proxy">' + escapeHtml(OVL_CURVE_TO_SPREAD[o.curve] || o.curve) + ' ' + escapeHtml(o.tenor) + ' 利差暂无数据</span>');
    }
  }
  el.innerHTML = parts.join('　｜　');
}
/* 分屏模式：独立曲线图（与主图 group 联动缩放/十字线） */
function renderOvlSplit() {
  var o = state.ovl;
  var split = o.layout === 'split' && o.mode !== 'none';
  $('overlayChart').classList.toggle('hidden', !split);
  $('mainChart').classList.toggle('split-mode', split);
  if (charts['mainChart']) charts['mainChart'].resize();
  if (charts['overlayChart']) charts['overlayChart'].resize();   // 隐藏/显示切换后同步画布尺寸
  if (!split) {
    if (charts['overlayChart']) charts['overlayChart'].clear();
    if (charts['mainChart']) charts['mainChart'].group = '';
    return;
  }
  if (!mainStore) return;
  var cats = mainStore.cats;
  var chart = getChart('overlayChart');
  var series = [], yAxis = [], yIdx = 0;
  // 折线显示仍按 mode；2σ带仅在 band 开启且对应 mode 含该类型时绘制（双带仅在 both 模式同时出现）
  var drawYieldLine  = (o.mode === 'yield' || o.mode === 'both');
  var drawSpreadLine = (o.mode === 'spread' || o.mode === 'both') && !ovlIsRate(o.curve);
  var ydOk = !!o.yieldData && !!(o.yieldData.dates && o.yieldData.dates.length);
  var sdOk = !!o.spreadData && !!(o.spreadData.dates && o.spreadData.dates.length);
  var bandYield  = o.band && drawYieldLine  && ydOk;   // 收益率2σ带
  var bandSpread = o.band && drawSpreadLine && sdOk;   // 利差2σ带（仅信用曲线+spread/both模式）
  // 是否需要建收益率轴：画收益率折线 或 画收益率2σ带
  var hasYield = (drawYieldLine || bandYield) && ydOk;
  // 是否需要建利差轴：画利差折线 或 画利差2σ带
  var hasSpread = (drawSpreadLine || bandSpread) && sdOk;
  if (hasYield) {
    var yv = asofAlign(o.yieldData.dates, o.yieldData.values, cats);
    yAxis.push({ type: 'value', position: 'left', scale: true, splitLine: { lineStyle: { color: '#ebeef5' } }, axisLabel: { color: o.colorYield, formatter: function (v) { return fmt(v, 2) + '%'; } } });
    if (bandYield) series = series.concat(ovlYieldBandSeries(yIdx));   // 收益率2σ带（蓝）
    if (drawYieldLine) series.push({ id: 'sYield', name: o.yieldData.curve + ' ' + o.yieldData.tenor + '收益率', type: 'line', data: yv, yAxisIndex: yIdx, symbol: 'none', connectNulls: true, lineStyle: { color: o.colorYield, width: 1.8 }, itemStyle: { color: o.colorYield } });
    yIdx++;
  }
  if (hasSpread) {
    var sv = asofAlign(o.spreadData.dates, o.spreadData.values, cats).map(function (v) { return v == null ? null : +(v * 100).toFixed(3); });
    yAxis.push({ type: 'value', position: hasYield ? 'right' : 'left', scale: true, splitLine: { show: false }, axisLabel: { color: o.colorSpread, formatter: function (v) { return fmt(v, 0) + 'bp'; } } });
    if (bandSpread) series = series.concat(ovlBandSeries(yIdx));        // 利差2σ带（紫）
    if (drawSpreadLine) series.push({ id: 'sSpread', name: o.spreadData.category + ' 利差(bp)', type: 'line', data: sv, yAxisIndex: yIdx, symbol: 'none', connectNulls: true, lineStyle: { color: o.colorSpread, width: 1.5 }, itemStyle: { color: o.colorSpread } });
    yIdx++;
  }
  if (!series.length) {
    chart.clear();
    chart.setOption({ title: { text: '当前品种暂无曲线数据', left: 'center', top: 'center', textStyle: { fontSize: 13, color: '#909399' } } }, true);
    return;
  }
  chart.setOption({
    animation: false,
    tooltip: {
      trigger: 'axis', confine: true,
      formatter: function (params) {
        var xi = params[0].dataIndex;
        var html = '<b>' + cats[xi] + '</b>';
        params.forEach(function (p) {
          if (p.seriesName.charAt(0) === '_') return;
          var v = p.value;
          if (v == null) return;
          if (p.seriesId === 'sYield') html += '<br/>' + p.marker + escapeHtml(p.seriesName) + '：<b>' + fmt(v, 4) + '</b>%';
          else if (p.seriesId === 'sSpread') html += '<br/>' + p.marker + escapeHtml(p.seriesName) + '：<b>' + fmt(v, 1) + '</b>bp';
        });
        return html;
      }
    },
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    legend: { type: 'scroll', top: 0, itemWidth: 14, itemHeight: 9, textStyle: { fontSize: 12 }, data: series.filter(function (s) { return s.name.charAt(0) !== '_'; }).map(function (s) { return s.name; }) },
    // grid 左右边距须与主图分屏时( left:62, right:58 )严格一致，否则上下两图日期刻度线错位
    grid: { left: 62, right: 58, top: 40, bottom: 24 },
    xAxis: { type: 'category', data: cats, axisLabel: { fontSize: 11 } },
    yAxis: yAxis,
    dataZoom: [{ type: 'inside', throttle: 50 }],
    series: series
  }, true);
  chart.group = 'ovlgrp';
  getChart('mainChart').group = 'ovlgrp';
  echarts.connect('ovlgrp');
}

/* ================= 主图 ================= */
function getChart(id) {
  if (!charts[id]) charts[id] = echarts.init($(id));
  return charts[id];
}
function aggregateGroups(names, rawByName) {
  // 机构维度 + alloc_trade 模式：归并 配置盘/交易盘/其他
  var defs = [
    { name: '配置盘(银行/保险/理财)', members: ALLOC_MEMBERS },
    { name: '交易盘(基金/券商/货基)', members: TRADE_MEMBERS },
    { name: '其他', members: ['其他'] }
  ];
  var outNames = [], outRaw = {};
  defs.forEach(function (d) {
    var present = d.members.filter(function (m) { return names.indexOf(m) >= 0; });
    if (!present.length) return;
    outNames.push(d.name);
    outRaw[d.name] = rawByName[present[0]].map(function (_, i) {
      return present.reduce(function (s, m) { var v = rawByName[m][i]; return s + (v == null ? 0 : v); }, 0);
    });
  });
  return { names: outNames, raw: outRaw };
}
var lastMainResp = null;   // 主图最近一次响应（极值/分组切换时复用，不重复请求）
function loadMain() {
  var request = mainRequests.begin();
  lastMainResp = null;
  setLoading('mainChart', true);
  ovlFollowFilters();
  syncOvlControls();
  var f = filterParams();
  api('dimension/', {
    institutions: f.institutions, bond_types: f.bond_types, tenors: f.tenors,
    start_date: state.startDate, end_date: state.endDate,
    granularity: state.granularity, dimension: state.dim
  }, request.signal).then(function (res) {
    if (!mainRequests.isCurrent(request.id)) return;
    lastMainResp = res;
    buildMainStore(res);
    loadOvlData(function () {
      if (!mainRequests.isCurrent(request.id)) return;
      setLoading('mainChart', false);
      renderMain();
      renderSide();
      renderOvlSplit();
      renderOvlSnapshot();
      mainRequests.finish(request.id);
    }, request.signal);
  }).catch(function (e) {
    if (!mainRequests.isCurrent(request.id) || e.name === 'AbortError') return;
    setLoading('mainChart', false);
    delete loadedTabs.ts;
    showErr('主图数据加载失败：' + e.message);
    mainRequests.finish(request.id);
  });
}
function buildMainStoreFromCache() { if (lastMainResp) { buildMainStore(lastMainResp); renderMain(); renderSide(); } }
function buildMainStore(res) {
  var list = res.data || [];
  // 日期并集（保序）
  var catSet = {}, cats = [];
  list.forEach(function (it) { it.series.forEach(function (p) { if (!catSet[p.date]) { catSet[p.date] = 1; cats.push(p.date); } }); });
  cats.sort();
  var rawByName = {};
  list.forEach(function (it) {
    var m = {}; it.series.forEach(function (p) { m[p.date] = p.value; });
    rawByName[it.name] = cats.map(function (d) { return m[d] != null ? m[d] : null; });
  });
  var names = groupOrder(state.dim, list.map(function (it) { return it.name; }));
  var raw = {}; names.forEach(function (n) { raw[n] = rawByName[n]; });
  if (state.dim === 'institution' && state.groupMode === 'alloc_trade') {
    var g = aggregateGroups(names, raw); names = g.names; raw = g.raw;
  }
  // 合计
  var total = cats.map(function (_, i) {
    return names.reduce(function (s, n) { var v = raw[n][i]; return s + (v == null ? 0 : v); }, 0);
  });
  // 极值边界（基于分段值，不含合计）
  var bounds = state.extreme === 'raw' ? null : calcBounds(names.map(function (n) { return raw[n]; }));
  var disp = {};
  names.forEach(function (n) {
    disp[n] = bounds ? raw[n].map(function (v) { return v == null ? null : clampV(v, bounds); }) : raw[n];
  });
  // Y 轴范围：基于截断后的"堆叠累计值"分布（正侧/负侧分别取 P99/P1），
  // 避免单段阈值过窄导致堆叠柱大量顶到边界；至少覆盖单段阈值 ×1.15
  var axisBounds = null;
  if (bounds) {
    var posArr = [], negArr = [];
    cats.forEach(function (d, xi) {
      var pos = 0, neg = 0;
      names.forEach(function (n) {
        var v = disp[n][xi];
        if (v == null) return;
        if (v > 0) pos += v; else neg += v;
      });
      if (pos > 0) posArr.push(pos);
      if (neg < 0) negArr.push(neg);
    });
    posArr.sort(function (a, b) { return a - b; });
    negArr.sort(function (a, b) { return a - b; });
    var hi = Math.max(posArr.length ? percentile(posArr, 99) : 0, bounds.hi * 1.15);
    var lo = Math.min(negArr.length ? percentile(negArr, 1) : 0, bounds.lo * 1.15);
    axisBounds = { lo: Math.floor(lo * 1.05), hi: Math.ceil(hi * 1.05) };
  }
  // 合计折线数值范围（右轴刻度精度用）
  var totalVals = total.filter(function (v) { return v != null && isFinite(v); });
  var totalRange = totalVals.length ? Math.max.apply(null, totalVals) - Math.min.apply(null, totalVals) : 0;
  mainStore = { cats: cats, names: names, raw: raw, disp: disp, total: total, bounds: bounds, axisBounds: axisBounds, totalRange: totalRange };
  if (!cats.length || cats.indexOf(state.selectedDate) < 0) state.selectedDate = cats[cats.length - 1] || null;

  var st = '共 ' + cats.length + ' 期 | ' + (cats[0] || '—') + ' ~ ' + (cats[cats.length - 1] || '—');
  if (bounds) st += ' | 显示阈值 ' + fmt(bounds.lo) + ' ~ ' + fmt(bounds.hi) + ' 亿（tooltip 为真实值）';
  else st += state.extreme !== 'raw' ? ' | 样本不足，未做极值处理' : '';
  $('mainStatus').textContent = st;
}
function renderMain() {
  if (!mainStore) return;
  var o = state.ovl;
  // 口径提示：券种/期限维度下若机构全选，全市场买卖抵消，合计趋近 0
  var hintEl = $('mainHint');
  if (state.dim !== 'institution' && state.inst.size === 0) {
    hintEl.textContent = '提示：当前为全机构口径，全市场买卖互相抵消，各' + (state.dim === 'bond_type' ? '券种' : '期限') +
      '净买入合计趋近于 0。建议先筛选具体机构（如基金公司及产品），再观察其' + (state.dim === 'bond_type' ? '券种' : '期限') + '配置结构';
    hintEl.style.color = '#e6a23c';
  } else {
    hintEl.textContent = '堆叠柱为各主体净买入，折线为合计；点击柱子可在右侧查看当日明细';
    hintEl.style.color = '';
  }
  var chart = getChart('mainChart');
  var cats = mainStore.cats, names = mainStore.names;
  var isBar = state.chartType === 'bar';
  var split = o.layout === 'split' && o.mode !== 'none';
  $('mainChart').classList.toggle('split-mode', split);
  if (charts['mainChart']) charts['mainChart'].resize();   // 容器高度变化后同步画布尺寸，避免溢出遮挡下方分屏
  var series = names.map(function (n, i) {
    var base = {
      name: n, type: state.chartType, data: mainStore.disp[n],
      emphasis: { focus: 'series' },
      itemStyle: { color: PALETTE[i % PALETTE.length] },
      lineStyle: { color: PALETTE[i % PALETTE.length], width: 1.5 }
    };
    if (isBar) { base.stack = 'net'; base.barMaxWidth = 22; }
    return base;
  });
  // 合计折线（右轴，避免极值截断后被裁掉）；仅一个主体时合计与该主体完全相同，隐藏以免冗余
  if (names.length > 1) series.push({
    name: '合计', type: 'line', data: mainStore.total, z: 10, yAxisIndex: 1,
    symbol: 'none', lineStyle: { color: COLOR_TOTAL, width: 1.6, type: 'dashed' },
    itemStyle: { color: COLOR_TOTAL }
  });
  // 截断标记（仅柱状 + clip 模式 + 有阈值）
  if (isBar && state.extreme === 'clip' && mainStore.bounds) {
    var marks = [];
    cats.forEach(function (d, xi) {
      var posCum = 0, negCum = 0;
      names.forEach(function (n) {
        var rv = mainStore.raw[n][xi], dv = mainStore.disp[n][xi];
        if (rv == null) return;
        if (rv !== dv) marks.push({ value: [xi, rv > 0 ? posCum + dv : negCum + dv], realValue: rv });
        if (dv > 0) posCum += dv; else negCum += dv;
      });
    });
    if (marks.length) series.push({
      name: '极值标记', type: 'effectScatter', data: marks, z: 20,
      symbol: 'diamond', symbolSize: 9, rippleEffect: { scale: 1.6 },
      itemStyle: { color: '#e6a23c' }, tooltip: { show: true }
    });
  }
  // ===== 叠加曲线（单图模式；分屏模式走 renderOvlSplit） =====
  var ovlY = null, ovlS = null;
  if (!split) {
    if ((o.mode === 'yield' || o.mode === 'both') && o.yieldData && o.yieldData.dates && o.yieldData.dates.length) {
      ovlY = asofAlign(o.yieldData.dates, o.yieldData.values, cats);
      series.push({
        id: 'ovlYield', name: o.yieldData.curve + ' ' + o.yieldData.tenor + '收益率',
        type: 'line', data: ovlY, yAxisIndex: 2, symbol: 'none', connectNulls: true, z: 12,
        lineStyle: { color: o.colorYield, width: 1.8 }, itemStyle: { color: o.colorYield }
      });
    }
    if ((o.mode === 'spread' || o.mode === 'both') && o.spreadData && o.spreadData.dates && o.spreadData.dates.length) {
      ovlS = asofAlign(o.spreadData.dates, o.spreadData.values, cats).map(function (v) { return v == null ? null : +(v * 100).toFixed(3); });
      var sAxis = ovlY ? 3 : 2;
      if (o.band) series = series.concat(ovlBandSeries(sAxis));
      series.push({
        id: 'ovlSpread', name: o.spreadData.category + ' 利差(bp)',
        type: 'line', data: ovlS, yAxisIndex: sAxis, symbol: 'none', connectNulls: true, z: 12,
        lineStyle: { color: o.colorSpread, width: 1.5 }, itemStyle: { color: o.colorSpread }
      });
    }
  }
  // ===== Y 轴（动态：0=净买入 1=合计 2=收益率 3=利差；右轴名称省略，单位由刻度后缀承担，避免文字重叠） =====
  var yAxis = [
    {
      type: 'value', name: '亿元(堆叠)',
      min: mainStore.axisBounds ? mainStore.axisBounds.lo : null,
      max: mainStore.axisBounds ? mainStore.axisBounds.hi : null,
      splitLine: { lineStyle: { color: '#ebeef5' } },
      axisLabel: { formatter: function (v) { return fmt(v, 0); } }
    },
    {
      type: 'value',
      scale: true, splitLine: { show: false },
      axisLabel: {
        color: '#909399',
        formatter: function (v) {
          var r = mainStore.totalRange;
          return fmt(v, r < 2 ? 2 : r < 20 ? 1 : 0) + '亿';
        }
      }
    }
  ];
  if (ovlY) yAxis.push({
    type: 'value', position: 'right', offset: 52,
    scale: true, splitLine: { show: false },
    axisLabel: { color: o.colorYield, formatter: function (v) { return fmt(v, 2) + '%'; } }
  });
  if (ovlS) yAxis.push({
    type: 'value', position: 'right', offset: ovlY ? 104 : 52,
    scale: true, splitLine: { show: false },
    axisLabel: { color: o.colorSpread, formatter: function (v) { return fmt(v, 0) + 'bp'; } }
  });
  var gridRight = 58 + (ovlY ? 52 : 0) + (ovlS ? 52 : 0);
  chart.setOption({
    animation: false,
    tooltip: {
      trigger: 'axis', axisPointer: { type: isBar ? 'shadow' : 'line' },
      confine: true,
      formatter: function (params) {
        var xi = params[0].dataIndex;
        var html = '<b>' + cats[xi] + '</b>';
        params.forEach(function (p) {
          if (p.seriesName === '极值标记' || p.seriesName.charAt(0) === '_') return;
          if (p.seriesId === 'ovlYield') {
            var yv = ovlY ? ovlY[xi] : null;
            if (yv != null) html += '<br/>' + p.marker + escapeHtml(p.seriesName) + '：<b>' + fmt(yv, 4) + '</b>%';
            return;
          }
          if (p.seriesId === 'ovlSpread') {
            var sv = ovlS ? ovlS[xi] : null;
            if (sv != null) html += '<br/>' + p.marker + escapeHtml(p.seriesName) + '：<b>' + fmt(sv, 1) + '</b>bp';
            return;
          }
          var v;
          if (p.seriesName === '合计') v = mainStore.total[xi];
          else v = mainStore.raw[p.seriesName] ? mainStore.raw[p.seriesName][xi] : null;
          if (v == null) return;
          var clipped = mainStore.disp[p.seriesName] && mainStore.disp[p.seriesName][xi] !== v;
          html += '<br/>' + p.marker + escapeHtml(p.seriesName) + '：<b>' + fmtSigned(v) + '</b> 亿' + (clipped ? ' <span style="color:#e6a23c">◆截断</span>' : '');
        });
        return html;
      }
    },
    legend: { type: 'scroll', top: 0, itemWidth: 14, itemHeight: 9, textStyle: { fontSize: 12 }, data: series.filter(function (s) { return s.name.charAt(0) !== '_'; }).map(function (s) { return s.name; }) },
    grid: { left: 62, right: gridRight, top: 60, bottom: split ? 30 : 58 },
    xAxis: { type: 'category', data: cats, axisLabel: { fontSize: 11, show: !split } },
    yAxis: yAxis,
    dataZoom: [
      { type: 'inside', throttle: 50 },
      { type: 'slider', height: 18, bottom: 8, brushSelect: false }
    ],
    series: series
  }, true);
  chart.off('click');
  chart.on('click', function (p) {
    if (p.componentType !== 'series' || p.seriesName === '极值标记') return;
    var d = cats[p.dataIndex];
    if (d) { state.selectedDate = d; renderSide(); }
  });
}

/* ================= 副图（当期排名 / 区间累计） ================= */
function renderSide() {
  if (!mainStore || !state.selectedDate) return;
  var xi = mainStore.cats.indexOf(state.selectedDate);
  if (xi < 0) return;
  var items = mainStore.names.map(function (n) {
    var v;
    if (state.sideMode === 'single') {
      v = mainStore.raw[n][xi];
    } else {
      v = 0;
      for (var i = 0; i <= xi; i++) { var x = mainStore.raw[n][i]; v += (x == null ? 0 : x); }
    }
    return { name: n, value: v };
  }).filter(function (it) { return it.value != null; });
  items.sort(function (a, b) { return b.value - a.value; });

  var granText = { day: '日', week: '周', month: '月', quarter: '季', year: '年' }[state.granularity] || '';
  $('sideTitle').textContent = (state.sideMode === 'single' ? '当期净买入_' : '区间累计_') + state.selectedDate +
    (state.sideMode === 'cum' ? '（自 ' + mainStore.cats[0] + ' 起，按' + granText + '度累计）' : '');

  sideStore = items;
  var chart = getChart('sideChart');
  chart.setOption({
    animation: false,
    tooltip: { trigger: 'item', confine: true, formatter: function (p) { return escapeHtml(p.name) + '：<b>' + fmtSigned(p.value) + '</b> 亿'; } },
    grid: { left: 8, right: 46, top: 10, bottom: 24, containLabel: true },
    xAxis: { type: 'value', splitLine: { lineStyle: { color: '#ebeef5' } }, axisLabel: { formatter: function (v) { return fmt(v, 0); } } },
    yAxis: {
      type: 'category', inverse: true,
      data: items.map(function (it) { return it.name; }),
      axisLabel: { fontSize: 12 }
    },
    series: [{
      type: 'bar', barMaxWidth: 20,
      data: items.map(function (it) {
        return { value: it.value, itemStyle: { color: it.value >= 0 ? '#f56c6c' : '#67c23a', borderRadius: it.value >= 0 ? [0, 3, 3, 0] : [3, 0, 0, 3] } };
      }),
      label: { show: true, position: 'right', fontSize: 11, formatter: function (p) { return fmtSigned(p.value); } }
    }]
  }, true);
}

/* ================= 季节性 ================= */
function loadSeasonality() {
  var request = seasonalityRequests.begin();
  setLoading('seasFlowChart', true); setLoading('seasYtdChart', true);
  var f = filterParams();
  var params = {
    institutions: f.institutions, bond_types: f.bond_types, tenors: f.tenors,
    granularity: state.seasGran, year_count: state.seasYear
  };
  if (state.seasMa !== 'none') params.ma = state.seasMa;
  api('seasonality/', params, request.signal).then(function (res) {
    if (!seasonalityRequests.isCurrent(request.id)) return;
    setLoading('seasFlowChart', false); setLoading('seasYtdChart', false);
    renderSeasonality(res);
    seasonalityRequests.finish(request.id);
    loadSeasYield();
  }).catch(function (e) {
    if (!seasonalityRequests.isCurrent(request.id) || e.name === 'AbortError') return;
    setLoading('seasFlowChart', false); setLoading('seasYtdChart', false);
    delete loadedTabs.seas;
    showErr('季节性数据加载失败：' + e.message);
    seasonalityRequests.finish(request.id);
  });
}
function renderSeasonality(res) {
  var axis = res.axis || [];
  var flow = res.flow_series || [], ytd = res.ytd_series || [];
  var years = flow.map(function (s) { return s.year; });
  var curYear = Math.max.apply(null, years);
  function mkSeries(list, isYtd) {
    var matrix = list.map(function (s) { return s.data; });
    var bounds = (state.seasWinsor && !isYtd) ? calcBounds(matrix) : null;
    return list.map(function (s, i) {
      var isCur = s.year === curYear;
      var data = bounds ? s.data.map(function (v) { return v == null ? null : clampV(v, bounds); }) : s.data;
      return {
        name: String(s.year), type: 'line', data: data, connectNulls: true,
        symbol: 'none', z: isCur ? 10 : 3,
        lineStyle: isCur ? { width: 3, color: '#f56c6c' } : { width: 1.5, color: SEAS_PALETTE[i % SEAS_PALETTE.length], opacity: 0.85 },
        itemStyle: { color: isCur ? '#f56c6c' : SEAS_PALETTE[i % SEAS_PALETTE.length] },
        emphasis: { focus: 'series' }
      };
    });
  }
  function filterLabel() {
    var f = currentFilters();
    function brief(arr, all) { return arr.length === all.length ? '全部' : arr.join('/'); }
    return brief(f.institutions, options.institutions) + ' × ' + brief(f.bond_types, options.bond_types) + ' × ' + brief(f.tenors, options.tenors);
  }
  $('seasFlowSub').textContent = filterLabel();
  $('seasYtdSub').textContent = '红实线=' + curYear + ' 年';
  var granText = { day: '日', week: '周', month: '月' }[state.seasGran] || '';
  $('seasStatus').textContent = '聚合粒度：' + granText + ' | 年份：' + years.join('、') + (state.seasWinsor ? ' | 流量图已缩尾(P1-P99)' : '');

  getChart('seasFlowChart').setOption({
    animation: false,
    tooltip: { trigger: 'axis', confine: true, valueFormatter: function (v) { return v == null ? '—' : fmtSigned(v) + ' 亿'; } },
    legend: { type: 'scroll', top: 0, textStyle: { fontSize: 12 } },
    grid: { left: 58, right: 16, top: 36, bottom: 30 },
    xAxis: { type: 'category', data: axis, axisLabel: { fontSize: 11 } },
    yAxis: { type: 'value', name: '亿元', splitLine: { lineStyle: { color: '#ebeef5' } } },
    series: mkSeries(flow, false)
  }, true);
  getChart('seasYtdChart').setOption({
    animation: false,
    tooltip: { trigger: 'axis', confine: true, valueFormatter: function (v) { return v == null ? '—' : fmtSigned(v) + ' 亿'; } },
    legend: { type: 'scroll', top: 0, textStyle: { fontSize: 12 } },
    grid: { left: 58, right: 16, top: 36, bottom: 30 },
    xAxis: { type: 'category', data: axis, axisLabel: { fontSize: 11 } },
    yAxis: { type: 'value', name: '亿元', splitLine: { lineStyle: { color: '#ebeef5' } } },
    series: mkSeries(ytd, true)
  }, true);
}

/* ================= 收益率季节性（类型/品种/期限可选 + 小图对比） ================= */
var SY_SPREAD_CATS = ['中短票AAA-国开', '中短票AA+-国开', '中短票AA-国开', '大行二级资本债-国开', '股份行二级资本债-国开'];
var syState = { follow: true, type: 'yield', curve: '国债', tenor: '10Y', compare: [], cache: {} };

function isoWeek(dateStr) {
  // ISO 8601 周数
  var d = new Date(dateStr + 'T00:00:00');
  var day = (d.getDay() + 6) % 7;
  d.setDate(d.getDate() - day + 3);
  var firstThursday = new Date(d.getFullYear(), 0, 4);
  var fday = (firstThursday.getDay() + 6) % 7;
  firstThursday.setDate(firstThursday.getDate() - fday + 3);
  return 1 + Math.round((d - firstThursday) / (7 * 864e5));
}
/* ISO 周年份：日期所在 ISO 周的周四所属的日历年。
   年末/年初几天其 ISO 周可能跨年（如 2024-12-31 属 2025-W01），季节性按周聚合须用 ISO 周年份分组，否则 W1 会被年末值污染。 */
function isoYear(dateStr) {
  var d = new Date(dateStr + 'T00:00:00');
  var day = (d.getDay() + 6) % 7;
  d.setDate(d.getDate() - day + 3);   // 调整到本周周四
  return d.getFullYear();
}
function buildYieldSeasonal(dates, values, gran) {
  // 按年分组、按聚合期对齐：day=MM-DD，week=W01-53，month=N月；期内取最后一值
  // week 模式按 ISO 周年份分组（isoYear），避免年末跨年周污染 W1
  var byYear = {};
  for (var i = 0; i < dates.length; i++) {
    if (values[i] == null) continue;
    var d = dates[i], y = d.slice(0, 4), md = d.slice(5);
    var key;
    if (gran === 'month') key = parseInt(md.slice(0, 2), 10) + '月';
    else if (gran === 'week') { y = String(isoYear(d)); key = 'W' + String(isoWeek(d)).padStart(2, '0'); }
    else key = md;
    if (!byYear[y]) byYear[y] = {};
    byYear[y][key] = values[i];
  }
  var keySet = {};
  Object.keys(byYear).forEach(function (y) { Object.keys(byYear[y]).forEach(function (k) { keySet[k] = 1; }); });
  var axis = Object.keys(keySet).sort(function (a, b) {
    if (gran === 'month') return parseInt(a, 10) - parseInt(b, 10);
    if (gran === 'week') return parseInt(a.slice(1), 10) - parseInt(b.slice(1), 10);
    return a < b ? -1 : 1;
  });
  var years = Object.keys(byYear).sort();
  var series = years.map(function (y) {
    return { year: +y, data: axis.map(function (k) { var v = byYear[y][k]; return v == null ? null : +(+v).toFixed(4); }) };
  });
  return { axis: axis, series: series };
}
function maSmoothArr(arr, win) {
  var out = new Array(arr.length).fill(null);
  var need = Math.max(Math.ceil(win / 2), 1);
  for (var i = 0; i < arr.length; i++) {
    var sum = 0, cnt = 0;
    for (var j = Math.max(0, i - win + 1); j <= i; j++) { if (arr[j] != null) { sum += arr[j]; cnt++; } }
    if (cnt >= need) out[i] = +(sum / cnt).toFixed(4);
  }
  return out;
}
/* ---- 选择状态 ---- */
function syCurrent() {
  if (syState.follow) return { type: 'yield', curve: state.ovl.curve, tenor: state.ovl.tenor };
  return { type: syState.type, curve: syState.curve, tenor: syState.tenor };
}
function syKey(it) { return it.type + '|' + it.curve + '|' + it.tenor; }
function syCurveList(type) {
  if (type === 'spread') return SY_SPREAD_CATS;
  return OVL_RATE_CURVES.concat(OVL_CREDIT_CURVES);
}
function syTenors(type, curve) {
  if (type === 'spread') return OVL_CREDIT_TENORS;
  return ovlIsRate(curve) ? OVL_RATE_TENORS : OVL_CREDIT_TENORS;
}
/* 控件编辑时：若处于跟随，先接管当前生效值再脱离跟随 */
function syDemanual() {
  if (syState.follow) {
    var cur = syCurrent();
    syState.type = cur.type; syState.curve = cur.curve; syState.tenor = cur.tenor;
    syState.follow = false;
  }
}
function syFetch(it) {
  var key = syKey(it);
  if (syState.cache[key]) return Promise.resolve(syState.cache[key]);
  var promise = it.type === 'spread'
    ? ovlApi('spread', { category: it.curve, tenor: it.tenor })
    : ovlApi('yield', { curve: it.curve, tenor: it.tenor });
  return promise.then(function (d) { syState.cache[key] = d; return d; });
}
/* ---- 控件构建与同步 ---- */
function buildSyCurveSel(type) {
  var sel = $('syCurveSel');
  sel.innerHTML = '';
  if (type === 'spread') {
    SY_SPREAD_CATS.forEach(function (c) {
      var opt = document.createElement('option'); opt.value = c; opt.textContent = c; sel.appendChild(opt);
    });
  } else {
    var g1 = document.createElement('optgroup'); g1.label = '利率（含15/20/30Y）';
    OVL_RATE_CURVES.forEach(function (c) { var o = document.createElement('option'); o.value = c; o.textContent = c; g1.appendChild(o); });
    var g2 = document.createElement('optgroup'); g2.label = '信用';
    OVL_CREDIT_CURVES.forEach(function (c) { var o = document.createElement('option'); o.value = c; o.textContent = c; g2.appendChild(o); });
    sel.appendChild(g1); sel.appendChild(g2);
  }
}
function buildSyTenorSel() {
  var cur = syCurrent();
  var sel = $('syTenorSel');
  sel.innerHTML = '';
  syTenors(cur.type, cur.curve).forEach(function (t) {
    var opt = document.createElement('option');
    opt.value = t;
    opt.textContent = t;
    sel.appendChild(opt);
  });
  sel.value = cur.tenor;
  sel.disabled = syState.follow;
}
function syncSyControls() {
  var cur = syCurrent();
  document.querySelectorAll('#syTypeSeg button').forEach(function (b) {
    b.classList.toggle('active', b.dataset.value === cur.type);
    var dis = syState.follow && b.dataset.value === 'spread';
    b.disabled = dis;
    b.style.opacity = dis ? '0.35' : '';
    b.title = dis ? '跟随主图时仅展示收益率' : '';
  });
  buildSyCurveSel(cur.type);
  $('syCurveSel').value = cur.curve;
  $('syCurveSel').disabled = syState.follow;
  buildSyTenorSel();
  $('syFollowChk').checked = syState.follow;
}
/* ---- 渲染（主图与小图共用） ---- */
function syChartOption(d, cur, mini) {
  var isSpread = cur.type === 'spread';
  var factor = isSpread ? 100 : 1;
  var unit = isSpread ? 'bp' : '%';
  var built = buildYieldSeasonal(d.dates, d.values.map(function (v) { return v == null ? null : v * factor; }), state.seasGran);
  var seriesArr = built.series;
  if (state.seasYear !== 'all') seriesArr = seriesArr.slice(-parseInt(state.seasYear, 10));
  // 去均值（仅看波动）：各年减自身均值，放大季节性形态对比
  if (state.seasAnomaly) {
    seriesArr.forEach(function (s) {
      var vals = s.data.filter(function (v) { return v != null; });
      if (!vals.length) return;
      var mean = vals.reduce(function (a, b) { return a + b; }, 0) / vals.length;
      s.data = s.data.map(function (v) { return v == null ? null : +(v - mean).toFixed(4); });
    });
  }
  var ma = state.seasMa !== 'none' ? parseInt(state.seasMa, 10) : null;
  var curYear = Math.max.apply(null, seriesArr.map(function (s) { return s.year; }));
  var series = seriesArr.map(function (s, i) {
    var isCur = s.year === curYear;
    return {
      name: String(s.year), type: 'line', data: ma ? maSmoothArr(s.data, ma) : s.data, connectNulls: true,
      symbol: 'none', z: isCur ? 10 : 3,
      lineStyle: isCur ? { width: 3, color: '#f56c6c' } : { width: 1.5, color: SEAS_PALETTE[i % SEAS_PALETTE.length], opacity: 0.85 },
      itemStyle: { color: isCur ? '#f56c6c' : SEAS_PALETTE[i % SEAS_PALETTE.length] },
      emphasis: { focus: 'series' }
    };
  });
  return {
    option: {
      animation: false,
      tooltip: { trigger: 'axis', confine: true, valueFormatter: function (v) { return v == null ? '—' : fmt(v, isSpread ? 1 : 3) + unit; } },
      legend: { type: 'scroll', top: 0, textStyle: { fontSize: mini ? 10 : 12 }, itemWidth: mini ? 12 : 14, itemHeight: mini ? 8 : 9 },
      grid: { left: mini ? 50 : 58, right: 16, top: mini ? 26 : 36, bottom: mini ? 22 : 30 },
      xAxis: { type: 'category', data: built.axis, axisLabel: { fontSize: mini ? 10 : 11 } },
      yAxis: { type: 'value', scale: true, splitLine: { lineStyle: { color: '#ebeef5' } }, axisLabel: { fontSize: mini ? 10 : 11, formatter: function (v) { return fmt(v, isSpread ? 0 : 2) + unit; } } },
      series: series
    },
    years: seriesArr.map(function (s) { return s.year; })
  };
}
/* ---- 数据加载与渲染 ---- */
function loadSeasYield() {
  var requestId = ++seasonYieldSeq;
  if (!$('seasYieldChk').checked) return;
  var cur = syCurrent();
  setLoading('seasYieldChart', true);
  Promise.all([syFetch(cur)].concat(syState.compare.map(syFetch))).then(function (results) {
    if (requestId !== seasonYieldSeq) return;
    setLoading('seasYieldChart', false);
    renderSeasYield(results[0], cur);
    renderSyCompare(syState.compare.map(function (it, i) { return { it: it, d: results[i + 1] }; }));
  }).catch(function (e) {
    if (requestId !== seasonYieldSeq) return;
    setLoading('seasYieldChart', false);
    $('seasYieldStatus').textContent = '收益率季节性加载失败：' + e.message;
  });
}
function renderSeasYield(d, cur) {
  var chart = getChart('seasYieldChart');
  var isSpread = cur.type === 'spread';
  $('seasYieldSub').textContent = '　' + cur.curve + ' ' + cur.tenor + (isSpread ? ' 利差（bp）' : ' 收益率（%）') + (state.seasAnomaly ? '（去均值）' : '');
  $('syAnomalyChk').checked = state.seasAnomaly;   // 切换 Tab 回填开关状态
  if (!d || !d.dates || !d.dates.length) {
    chart.clear();
    chart.setOption({ title: { text: cur.curve + ' ' + cur.tenor + ' 暂无数据', left: 'center', top: 'center', textStyle: { fontSize: 13, color: '#909399' } } }, true);
    $('seasYieldStatus').textContent = '';
    return;
  }
  var r = syChartOption(d, cur, false);
  var granText = { day: '日', week: '周', month: '月' }[state.seasGran] || '';
  var ma = state.seasMa !== 'none' ? ' | MA' + state.seasMa : '';
  var anom = state.seasAnomaly ? ' | 去均值（仅看年内波动）' : '';
  $('seasYieldStatus').textContent = '聚合粒度：' + granText + ma + anom +
    ' | 年份：' + r.years.join('、') + ' | 数据截至 ' + d.dates[d.dates.length - 1];
  chart.setOption(r.option, true);
}
function renderSyCompareChips() {
  var wrap = $('syCompareChips');
  wrap.innerHTML = '';
  if (!syState.compare.length) return;
  var label = document.createElement('span');
  label.style.cssText = 'font-size:12px;color:var(--muted)';
  label.textContent = '对比序列：';
  wrap.appendChild(label);
  syState.compare.forEach(function (it, i) {
    var s = document.createElement('span');
    s.className = 's-chip';
    s.innerHTML = escapeHtml(it.curve + ' ' + it.tenor + (it.type === 'spread' ? '（利差）' : '')) + ' <i class="x">×</i>';
    s.querySelector('.x').addEventListener('click', function () {
      syState.compare.splice(i, 1);
      loadSeasYield();
    });
    wrap.appendChild(s);
  });
  var clear = document.createElement('span');
  clear.style.cssText = 'font-size:12px;color:var(--muted);cursor:pointer;margin-left:6px';
  clear.textContent = '清空';
  clear.addEventListener('click', function () { syState.compare = []; loadSeasYield(); });
  wrap.appendChild(clear);
}
function renderSyCompare(items) {
  // 释放旧小图实例
  Object.keys(charts).forEach(function (k) {
    if (k.indexOf('syCmp') === 0) { charts[k].dispose(); delete charts[k]; }
  });
  renderSyCompareChips();
  var grid = $('syCompareGrid');
  grid.innerHTML = '';
  grid.classList.toggle('hidden', !items.length);
  if (!items.length) return;
  items.forEach(function (entry, idx) {
    var cell = document.createElement('div');
    cell.style.cssText = 'border:1px solid var(--border);border-radius:6px;padding:6px;background:#fff';
    var title = document.createElement('div');
    title.style.cssText = 'font-size:12px;color:var(--muted);margin-bottom:2px';
    title.textContent = entry.it.curve + ' ' + entry.it.tenor + (entry.it.type === 'spread' ? ' 利差（bp）' : ' 收益率（%）');
    var box = document.createElement('div');
    box.id = 'syCmp' + idx;
    box.style.cssText = 'width:100%;height:230px';
    cell.appendChild(title);
    cell.appendChild(box);
    grid.appendChild(cell);
    if (!entry.d || !entry.d.dates || !entry.d.dates.length) {
      box.innerHTML = '<div style="padding:70px 0;text-align:center;color:var(--muted);font-size:12px">暂无数据</div>';
      return;
    }
    var r = syChartOption(entry.d, entry.it, true);
    charts[box.id] = echarts.init(box);
    charts[box.id].setOption(r.option, true);
  });
}

/* ================= 主力机构矩阵 ================= */
function loadDominant() {
  var s = $('domStart').value, e = $('domEnd').value;
  if (!s || !e) return showErr('请选择统计区间');
  if (s > e) return showErr('开始日期不能晚于结束日期');
  var f = filterParams();
  $('domStatus').textContent = '加载中…';
  api('dominant/', {
    institutions: f.institutions, bond_types: f.bond_types, tenors: f.tenors,
    start_date: s, end_date: e, top_n: state.domTopN
  }).then(function (res) {
    renderDominant('domBuyWrap', res.buy_matrix || {}, true);
    renderDominant('domSellWrap', res.sell_matrix || {}, false);
    $('domStatus').textContent = '区间 ' + s + ' ~ ' + e + ' | 已套用全局筛选（机构子集内取主力）';
  }).catch(function (err) { $('domStatus').textContent = ''; showErr('主力机构加载失败：' + err.message); });
}
function renderDominant(wrapId, matrix, isBuy) {
  var wrap = $(wrapId);
  var f = currentFilters();
  var bonds = groupOrder('bond_type', f.bond_types.filter(function (b) { return matrix[b]; }));
  var tenorSet = {};
  bonds.forEach(function (b) { Object.keys(matrix[b]).forEach(function (t) { tenorSet[t] = 1; }); });
  var tenors = groupOrder('tenor', Object.keys(tenorSet));
  if (!bonds.length || !tenors.length) { wrap.innerHTML = '<div style="padding:30px;text-align:center;color:var(--muted)">该区间暂无数据</div>'; return; }

  var html = '<table class="dom"><thead><tr><th style="text-align:left">券种 \\ 期限</th>';
  tenors.forEach(function (t) { html += '<th>' + escapeHtml(t) + '</th>'; });
  html += '</tr></thead><tbody>';
  bonds.forEach(function (b) {
    html += '<tr><th>' + escapeHtml(b) + '</th>';
    tenors.forEach(function (t) {
      var cell = (matrix[b] && matrix[b][t]) || [];
      if (!cell.length) { html += '<td class="dom-empty">—</td>'; return; }
      var maxRatio = Math.max.apply(null, cell.map(function (c) { return c.ratio || 0; }));
      var alpha = Math.min(0.32, 0.08 + maxRatio * 0.3);
      var bg = isBuy ? 'rgba(245,108,108,' + alpha + ')' : 'rgba(103,194,58,' + alpha + ')';
      html += '<td style="background:' + bg + '">';
      cell.forEach(function (c) {
        var pct = (c.ratio * 100).toFixed(1) + '%';
        html += '<div class="dom-line"><span class="dom-inst">' + escapeHtml(c.institution) + '</span>' +
          '<span class="dom-ratio" style="background:' + (isBuy ? '#fef0f0;color:#f56c6c' : '#f0f9eb;color:#67c23a') + '">' + pct + '</span></div>';
      });
      html += '</td>';
    });
    html += '</tr>';
  });
  html += '</tbody></table>';
  wrap.innerHTML = html;
}

/* ================= 单日机构行为 ================= */
/* 取数策略：
 * 1) 交易日历：首次进入时拉取全历史交易日列表（轻量单机构单券种单期限请求），翻页基于此跳过非交易日。
 * 2) 主矩阵：对每个所选机构并行调用 dimension/bond_type（单日），构建 机构×券种 净买入矩阵。
 * 3) 期限展开（按需）：用户点击某券种表头时，对该券种并行请求每个机构的 dimension=tenor（单券种），
 *    缓存 net[inst][bond][tenor]，再次点击直接展开（已缓存）。
 */
var dailyStore = null;          // {date, institutions[], bonds[], net{inst:{bond:v}}, tenorDetail{bond:{inst:{tenor:v}}}, expandedBond}
var dailyTradeDates = null;     // ['YYYY-MM-DD', ...] 升序

function dailyInstList() {
  var f = currentFilters();
  return groupOrder('institution', f.institutions);
}
function dailyBondList() {
  var f = currentFilters();
  return groupOrder('bond_type', f.bond_types);
}
function dailyInstGroupName(inst) {
  for (var i = 0; i < INST_GROUPS.length; i++) {
    if (INST_GROUPS[i].members.indexOf(inst) >= 0) return INST_GROUPS[i].name;
  }
  return '其他';
}
function dailyBondGroupName(bond) {
  for (var i = 0; i < BOND_GROUPS.length; i++) {
    if (BOND_GROUPS[i].members.indexOf(bond) >= 0) return BOND_GROUPS[i].name;
  }
  return '其他';
}

/* 拉取交易日历（全历史，单机构单券种单期限多日请求，series.date 即实际交易日） */
function ensureDailyTradeDates() {
  if (dailyTradeDates) return Promise.resolve(dailyTradeDates);
  return api('dimension/', {
    institutions: ['大型银行'], bond_types: ['国债'], tenors: ['1-3年'],
    start_date: DATA_START, end_date: options.latest_date, granularity: 'day', dimension: 'bond_type'
  }).then(function (res) {
    var list = (res && res.data && res.data[0] && res.data[0].series) || [];
    dailyTradeDates = list.map(function (p) { return p.date; }).sort();
    return dailyTradeDates;
  });
}
/* 在交易日列表中查找相邻交易日；超出范围返回 null */
function dailyAdjTradeDate(date, dir) {
  if (!dailyTradeDates || !dailyTradeDates.length) return null;
  var i = dailyTradeDates.indexOf(date);
  if (i < 0) {
    /* 当日不在列表（如周末），找最近的交易日 */
    for (i = 0; i < dailyTradeDates.length; i++) { if (dailyTradeDates[i] > date) break; }
    return dir < 0 ? (i > 0 ? dailyTradeDates[i - 1] : null) : (i < dailyTradeDates.length ? dailyTradeDates[i] : null);
  }
  return dir < 0 ? (i > 0 ? dailyTradeDates[i - 1] : null) : (i < dailyTradeDates.length - 1 ? dailyTradeDates[i + 1] : null);
}

function loadDaily() {
  var request = dailyRequests.begin();
  var date = state.dailyDate || (options && options.latest_date);
  if (!date) { $('dailyStatus').textContent = '无可用日期'; return; }
  state.dailyDate = date;
  $('dailyDate').value = date;
  /* 更新翻页按钮可用状态 */
  var prev = dailyTradeDates ? dailyAdjTradeDate(date, -1) : null;
  var next = dailyTradeDates ? dailyAdjTradeDate(date, 1) : null;
  $('dailyPrevBtn').disabled = !prev;
  $('dailyNextBtn').disabled = !next;
  $('dailyLatestBtn').classList.toggle('on', date === (options && options.latest_date));

  var f = filterParams();
  var tenors = f.tenors;
  var bonds = dailyBondList();
  var insts = dailyInstList();
  setLoading('dailyTableWrap', true);
  $('dailyStatus').textContent = '加载中…（' + insts.length + ' 个机构并行查询）';

  var jobs = insts.map(function (inst) {
    return api('dimension/', {
      institutions: [inst], bond_types: bonds, tenors: tenors,
      start_date: date, end_date: date, granularity: 'day', dimension: 'bond_type'
    }, request.signal).then(function (res) { return { inst: inst, res: res }; });
  });

  Promise.all(jobs).then(function (results) {
    if (!dailyRequests.isCurrent(request.id)) return;
    setLoading('dailyTableWrap', false);
    var net = {};
    results.forEach(function (r) {
      net[r.inst] = {};
      var list = (r.res && r.res.data) || [];
      list.forEach(function (it) {
        var v = it.series && it.series.length ? it.series[0].value : 0;
        net[r.inst][it.name] = (v == null ? 0 : v);
      });
    });
    insts.forEach(function (inst) {
      if (!net[inst]) net[inst] = {};
      bonds.forEach(function (b) { if (net[inst][b] == null) net[inst][b] = 0; });
    });
    dailyStore = { date: date, institutions: insts, bonds: bonds, net: net, tenorDetail: {}, expandedBond: null };
    renderDailyAll();
    $('dailyStatus').textContent = '日期 ' + date + ' | 机构 ' + insts.length + ' × 券种 ' + bonds.length +
      ' | 口径：所选期限合计净买入（亿元），正向=净买入，负向=净卖出';
    dailyRequests.finish(request.id);
  }).catch(function (e) {
    if (!dailyRequests.isCurrent(request.id) || e.name === 'AbortError') return;
    setLoading('dailyTableWrap', false);
    delete loadedTabs.daily;
    $('dailyStatus').textContent = '';
    showErr('单日机构行为加载失败：' + e.message);
    dailyRequests.finish(request.id);
  });
}

/* 按需加载某券种的期限明细：对每个机构请求 dimension=tenor（单券种、所选期限、单日） */
function loadDailyTenorDetail(bond) {
  if (!dailyStore) return Promise.resolve();
  if (dailyStore.tenorDetail[bond]) return Promise.resolve(dailyStore.tenorDetail[bond]);
  var request = dailyRequests.begin();
  var date = dailyStore.date;
  var f = filterParams();
  var tenors = f.tenors;
  var insts = dailyStore.institutions;
  $('dailyStatus').textContent = '加载 ' + bond + ' 期限明细…';
  var jobs = insts.map(function (inst) {
    return api('dimension/', {
      institutions: [inst], bond_types: [bond], tenors: tenors,
      start_date: date, end_date: date, granularity: 'day', dimension: 'tenor'
    }, request.signal).then(function (res) { return { inst: inst, res: res }; });
  });
  return Promise.all(jobs).then(function (results) {
    if (!dailyRequests.isCurrent(request.id)) return;
    var detail = {};
    var tenorSet = {};
    results.forEach(function (r) {
      detail[r.inst] = {};
      var list = (r.res && r.res.data) || [];
      list.forEach(function (it) {
        var v = it.series && it.series.length ? it.series[0].value : 0;
        detail[r.inst][it.name] = (v == null ? 0 : v);
        tenorSet[it.name] = 1;
      });
    });
    detail._tenors = groupOrder('tenor', Object.keys(tenorSet));
    dailyStore.tenorDetail[bond] = detail;
    dailyRequests.finish(request.id);
    return detail;
  }).catch(function (e) {
    if (!dailyRequests.isCurrent(request.id) || e.name === 'AbortError') return;
    $('dailyStatus').textContent = '';
    showErr('期限明细加载失败：' + e.message);
    dailyRequests.finish(request.id);
  });
}

/* 按当前视图（明细/机构组/全市场 × 券种明细/券种组）聚合 */
function aggregateDaily() {
  if (!dailyStore) return null;
  var insts = dailyStore.institutions, bonds = dailyStore.bonds, net = dailyStore.net;
  var rowView = state.dailyRowView, colView = state.dailyColView;
  var rowNames, colNames;
  var rowAgg = {};   // rowName -> {colName: sum}

  if (rowView === 'market') {
    rowNames = ['全市场'];
    rowAgg['全市场'] = {};
    colNames = (colView === 'bond_group') ? BOND_GROUPS.map(function (g) { return g.name; }) : bonds;
    colNames.forEach(function (c) { rowAgg['全市场'][c] = 0; });
    insts.forEach(function (inst) {
      bonds.forEach(function (b) {
        var col = (colView === 'bond_group') ? dailyBondGroupName(b) : b;
        rowAgg['全市场'][col] += net[inst][b] || 0;
      });
    });
  } else if (rowView === 'inst_group') {
    var groupSet = {};
    insts.forEach(function (inst) { groupSet[dailyInstGroupName(inst)] = 1; });
    rowNames = INST_GROUPS.map(function (g) { return g.name; }).filter(function (n) { return groupSet[n]; });
    colNames = (colView === 'bond_group') ? BOND_GROUPS.map(function (g) { return g.name; }) : bonds;
    rowNames.forEach(function (rn) { rowAgg[rn] = {}; colNames.forEach(function (c) { rowAgg[rn][c] = 0; }); });
    insts.forEach(function (inst) {
      var rn = dailyInstGroupName(inst);
      bonds.forEach(function (b) {
        var col = (colView === 'bond_group') ? dailyBondGroupName(b) : b;
        rowAgg[rn][col] += net[inst][b] || 0;
      });
    });
  } else {
    rowNames = insts.slice();
    colNames = (colView === 'bond_group') ? BOND_GROUPS.map(function (g) { return g.name; }) : bonds;
    rowNames.forEach(function (rn) {
      rowAgg[rn] = {};
      colNames.forEach(function (c) { rowAgg[rn][c] = 0; });
      bonds.forEach(function (b) {
        var col = (colView === 'bond_group') ? dailyBondGroupName(b) : b;
        rowAgg[rn][col] += net[rn][b] || 0;
      });
    });
  }
  /* 保留 4 位小数避免浮点误差 */
  rowNames.forEach(function (rn) { colNames.forEach(function (c) { rowAgg[rn][c] = +rowAgg[rn][c].toFixed(4); }); });
  return { rowNames: rowNames, colNames: colNames, agg: rowAgg };
}

function renderDailyAll() {
  renderDailyKpi();
  if (state.dailyChart === 'matrix') {
    $('dailyMatrixWrap').classList.remove('hidden');
    $('dailySankeyWrap').classList.add('hidden');
    renderDailyMatrix();
  } else {
    $('dailyMatrixWrap').classList.add('hidden');
    $('dailySankeyWrap').classList.remove('hidden');
    renderDailySankey();
  }
  renderDailyTable();
}

/* 双向横向柱矩阵：行=机构，每行内按券种分组左右双向柱（左=净卖出，右=净买入） */
function renderDailyMatrix() {
  if (!dailyStore) return;
  var data = aggregateDaily();
  if (!data) return;
  var rowNames = data.rowNames, colNames = data.colNames, agg = data.agg;
  var chart = getChart('dailyMatrixChart');
  if (charts['dailySankeyChart']) { charts['dailySankeyChart'].clear(); }

  /* 每行内：券种正向柱 + 券种负向柱。用多 series，每个券种一对（买/卖）。
   * 简化：用堆叠柱，正负向分别堆叠在 0 轴两侧。每个券种一个 series，data 按行给值（负值自然在左）。 */
  var series = colNames.map(function (col, i) {
    return {
      name: col, type: 'bar', stack: 'net',
      barMaxWidth: 26,
      data: rowNames.map(function (rn) { return agg[rn][col]; }),
      itemStyle: { color: PALETTE[i % PALETTE.length] },
      emphasis: { focus: 'series' }
    };
  });
  /* 行合计折线（右轴） */
  var totals = rowNames.map(function (rn) {
    return +colNames.reduce(function (s, c) { return s + agg[rn][c]; }, 0).toFixed(4);
  });
  if (rowNames.length > 1) {
    series.push({
      name: '行合计', type: 'line', data: totals, yAxisIndex: 1, symbol: 'circle', symbolSize: 6,
      lineStyle: { color: COLOR_TOTAL, width: 1.6, type: 'dashed' }, itemStyle: { color: COLOR_TOTAL }, z: 10
    });
  }
  chart.setOption({
    animation: false,
    tooltip: {
      trigger: 'axis', axisPointer: { type: 'shadow' }, confine: true,
      formatter: function (params) {
        var xi = params[0].dataIndex;
        var html = '<b>' + escapeHtml(rowNames[xi]) + '</b>';
        var posSum = 0, negSum = 0;
        params.forEach(function (p) {
          if (p.seriesName === '行合计') return;
          var v = p.value;
          if (v == null || v === 0) return;
          html += '<br/>' + p.marker + escapeHtml(p.seriesName) + '：<b>' + fmtSigned(v) + '</b> 亿';
          if (v > 0) posSum += v; else negSum += v;
        });
        html += '<br/>──────<br/>净买入合计：<b>' + fmtSigned(posSum + negSum) + '</b> 亿（买 ' + fmt(posSum) + ' / 卖 ' + fmt(Math.abs(negSum)) + '）';
        return html;
      }
    },
    legend: { type: 'scroll', top: 0, itemWidth: 14, itemHeight: 9, textStyle: { fontSize: 12 }, data: colNames.concat(rowNames.length > 1 ? ['行合计'] : []) },
    grid: { left: 110, right: 70, top: 50, bottom: 30 },
    xAxis: { type: 'value', name: '亿元', splitLine: { lineStyle: { color: '#ebeef5' } }, axisLabel: { formatter: function (v) { return fmt(v, 0); } } },
    yAxis: [
      { type: 'category', inverse: true, data: rowNames, axisLabel: { fontSize: 12 } },
      { type: 'value', scale: true, splitLine: { show: false }, axisLabel: { color: '#909399', formatter: function (v) { return fmt(v, 0) + '亿'; } } }
    ],
    series: series
  }, true);
}

/* 桑基流向图：左=机构，右=券种，连线宽度=净买入绝对值 */
function renderDailySankey() {
  if (!dailyStore) return;
  var data = aggregateDaily();
  if (!data) return;
  var rowNames = data.rowNames, colNames = data.colNames, agg = data.agg;
  var chart = getChart('dailySankeyChart');
  if (charts['dailyMatrixChart']) { charts['dailyMatrixChart'].clear(); }

  /* 节点：左机构 + 右券种（加后缀区分） */
  var nodes = [];
  rowNames.forEach(function (rn) { nodes.push({ name: rn, depth: 0 }); });
  colNames.forEach(function (cn) { nodes.push({ name: cn + '◆', depth: 1 }); });

  /* 连线：仅净买入非零；过滤小值（绝对值<全市场总规模 1%） */
  var allVals = [];
  rowNames.forEach(function (rn) { colNames.forEach(function (cn) { allVals.push(Math.abs(agg[rn][cn])); }); });
  var totalAbs = allVals.reduce(function (a, b) { return a + b; }, 0);
  var threshold = totalAbs * 0.01;
  var links = [];
  rowNames.forEach(function (rn) {
    colNames.forEach(function (cn) {
      var v = agg[rn][cn];
      if (v === 0 || Math.abs(v) < threshold) return;
      links.push({
        source: rn, target: cn + '◆',
        value: +Math.abs(v).toFixed(4),
        netValue: v,
        lineStyle: { color: v > 0 ? 'rgba(245,108,108,0.55)' : 'rgba(103,194,58,0.55)' }
      });
    });
  });

  if (!links.length) {
    chart.clear();
    chart.setOption({ title: { text: '当日无显著资金流向', left: 'center', top: 'center', textStyle: { fontSize: 13, color: '#909399' } } }, true);
    return;
  }
  chart.setOption({
    animation: false,
    tooltip: {
      trigger: 'item', confine: true,
      formatter: function (p) {
        if (p.dataType !== 'edge') return escapeHtml(p.name.replace('◆', ''));
        var d = p.data;
        return escapeHtml(d.source) + ' → ' + escapeHtml(d.target.replace('◆', '')) + '<br/>净买入：<b>' + fmtSigned(d.netValue) + '</b> 亿';
      }
    },
    series: [{
      type: 'sankey', data: nodes, links: links,
      left: 60, right: 140, top: 20, bottom: 20,
      nodeWidth: 16, nodeGap: 8, nodeAlign: 'justify',
      orient: 'horizontal',
      label: { fontSize: 12, formatter: function (p) { return p.name.replace('◆', ''); } },
      lineStyle: { curveness: 0.5, opacity: 0.55 },
      emphasis: { focus: 'adjacency' }
    }]
  }, true);
}

/* 数据表格：机构×券种 净买入矩阵，红买绿卖着色；券种明细+机构明细视图下表头可点击展开期限 */
function renderDailyTable() {
  if (!dailyStore) return;
  var data = aggregateDaily();
  if (!data) return;
  var rowNames = data.rowNames, colNames = data.colNames, agg = data.agg;
  var wrap = $('dailyTableWrap');
  var canExpand = state.dailyRowView === 'detail' && state.dailyColView === 'detail';
  var expandedBond = canExpand ? dailyStore.expandedBond : null;
  $('dailyTblSub').textContent = '　日期 ' + dailyStore.date + ' | 单位：亿元（红买绿卖）' +
    (canExpand ? ' | 点击券种表头展开/收起期限明细' : '');

  var maxAbs = 0;
  rowNames.forEach(function (rn) { colNames.forEach(function (cn) { var a = Math.abs(agg[rn][cn]); if (a > maxAbs) maxAbs = a; }); });

  function cellHtml(v) {
    if (v === 0) return '<td class="dl-empty">—</td>';
    var alpha = maxAbs > 0 ? Math.min(0.45, 0.08 + Math.abs(v) / maxAbs * 0.4) : 0.1;
    var bg = v > 0 ? 'rgba(245,108,108,' + alpha + ')' : 'rgba(103,194,58,' + alpha + ')';
    var cls2 = v > 0 ? 'pos' : 'neg';
    return '<td style="background:' + bg + '" class="' + cls2 + '">' + fmtSigned(v) + '</td>';
  }

  var html = '<table class="daily-tbl"><thead><tr><th style="text-align:left">' +
    (state.dailyRowView === 'market' ? '口径' : state.dailyRowView === 'inst_group' ? '机构组' : '机构') +
    ' \\ ' + (state.dailyColView === 'bond_group' ? '券种组' : '券种') + '</th>';
  colNames.forEach(function (cn) {
    if (canExpand) {
      var isExp = cn === expandedBond;
      html += '<th class="dl-toggle ' + (isExp ? 'expanded' : 'collapsed') + '" data-bond="' + escapeHtml(cn) + '">' + escapeHtml(cn) + '</th>';
    } else {
      html += '<th>' + escapeHtml(cn) + '</th>';
    }
  });
  html += '<th>行合计</th></tr></thead><tbody>';
  rowNames.forEach(function (rn) {
    var rowSum = 0;
    html += '<tr><th>' + escapeHtml(rn) + '</th>';
    colNames.forEach(function (cn) { var v = agg[rn][cn]; rowSum += v; html += cellHtml(v); });
    html += '<td class="' + (rowSum > 0 ? 'pos' : rowSum < 0 ? 'neg' : 'zero') + '"><b>' + fmtSigned(+rowSum.toFixed(2)) + '</b></td></tr>';

    /* 展开券种时，为该机构插入期限明细子行 */
    if (canExpand && expandedBond && dailyStore.tenorDetail[expandedBond]) {
      var detail = dailyStore.tenorDetail[expandedBond];
      var tenors = detail._tenors || [];
      tenors.forEach(function (tn, ti) {
        html += '<tr class="dl-tenor-row"><td class="dl-tenor-label">　' + escapeHtml(tn) + '</td>';
        colNames.forEach(function (cn) {
          if (cn === expandedBond) {
            var tv = detail[rn] ? detail[rn][tn] : 0;
            html += cellHtml(tv || 0);
          } else {
            html += '<td></td>';
          }
        });
        /* 期限行合计即该机构该券种该期限值 */
        var tvSum = detail[rn] ? (detail[rn][tn] || 0) : 0;
        html += '<td class="' + (tvSum > 0 ? 'pos' : tvSum < 0 ? 'neg' : 'zero') + '">' + fmtSigned(+tvSum.toFixed(2)) + '</td></tr>';
      });
    }
  });
  /* 列合计行 */
  html += '<tr><th>列合计</th>';
  var grandTotal = 0;
  colNames.forEach(function (cn) {
    var s = 0;
    rowNames.forEach(function (rn) { s += agg[rn][cn]; });
    grandTotal += s;
    html += '<td class="' + (s > 0 ? 'pos' : s < 0 ? 'neg' : 'zero') + '"><b>' + fmtSigned(+s.toFixed(2)) + '</b></td>';
  });
  html += '<td class="' + (grandTotal > 0 ? 'pos' : grandTotal < 0 ? 'neg' : 'zero') + '"><b>' + fmtSigned(+grandTotal.toFixed(2)) + '</b></td></tr>';
  html += '</tbody></table>';
  wrap.innerHTML = html;

  /* 绑定券种表头点击展开/收起 */
  if (canExpand) {
    wrap.querySelectorAll('th.dl-toggle').forEach(function (th) {
      th.addEventListener('click', function () {
        var bond = th.dataset.bond;
        if (dailyStore.expandedBond === bond) {
          dailyStore.expandedBond = null;
          renderDailyTable();
        } else {
          dailyStore.expandedBond = bond;
          if (dailyStore.tenorDetail[bond]) {
            renderDailyTable();
          } else {
            setLoading('dailyTableWrap', true);
            loadDailyTenorDetail(bond).then(function () {
              setLoading('dailyTableWrap', false);
              renderDailyTable();
              $('dailyStatus').textContent = '日期 ' + dailyStore.date + ' | 机构 ' + dailyStore.institutions.length + ' × 券种 ' + dailyStore.bonds.length +
                ' | 口径：所选期限合计净买入（亿元），正向=净买入，负向=净卖出';
            });
          }
        }
      });
    });
  }
}

function renderDailyKpi() {
  if (!dailyStore) return;
  var insts = dailyStore.institutions, bonds = dailyStore.bonds, net = dailyStore.net;
  var grandTotal = 0, best = null, worst = null;
  insts.forEach(function (inst) {
    bonds.forEach(function (b) {
      var v = net[inst][b] || 0;
      grandTotal += v;
      if (!best || v > best.v) best = { inst: inst, bond: b, v: v };
      if (!worst || v < worst.v) worst = { inst: inst, bond: b, v: v };
    });
  });
  grandTotal = +grandTotal.toFixed(4);
  $('dkpi1').textContent = fmtSigned(grandTotal) + ' 亿';
  $('dkpi1').className = 'k-value ' + cls(grandTotal);
  $('dkpi1Sub').textContent = dailyStore.date + ' | 全机构×全所选券种';

  if (best) {
    $('dkpi2').textContent = best.inst;
    $('dkpi2Sub').textContent = best.bond + '：' + fmtSigned(best.v) + ' 亿';
  }
  if (worst) {
    $('dkpi3').textContent = worst.inst;
    $('dkpi3Sub').textContent = worst.bond + '：' + fmtSigned(worst.v) + ' 亿';
  }

  /* 配置盘-交易盘 */
  var allocSum = 0, tradeSum = 0;
  insts.forEach(function (inst) {
    var rowSum = bonds.reduce(function (s, b) { return s + (net[inst][b] || 0); }, 0);
    if (ALLOC_MEMBERS.indexOf(inst) >= 0) allocSum += rowSum;
    else if (TRADE_MEMBERS.indexOf(inst) >= 0) tradeSum += rowSum;
  });
  var diff = +(allocSum - tradeSum).toFixed(4);
  $('dkpi4').textContent = fmtSigned(diff) + ' 亿';
  $('dkpi4').className = 'k-value ' + cls(diff);
  $('dkpi4Sub').textContent = '配置盘 ' + fmtSigned(+allocSum.toFixed(2)) + ' / 交易盘 ' + fmtSigned(+tradeSum.toFixed(2));
}

/* ================= CSV 下载 ================= */
function downloadCsv(filename, rows) {
  var csv = '﻿' + rows.map(function (r) {
    return r.map(function (c) { var s = c == null ? '' : String(c); return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; }).join(',');
  }).join('\n');
  var a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
  a.download = filename;
  document.body.appendChild(a); a.click(); document.body.removeChild(a);
  URL.revokeObjectURL(a.href);
}
function bindDownloads() {
  $('dlMainBtn').addEventListener('click', function () {
    if (!mainStore) return;
    var o = state.ovl;
    var hasY = (o.mode === 'yield' || o.mode === 'both') && o.yieldData && o.yieldData.dates && o.yieldData.dates.length;
    var hasS = (o.mode === 'spread' || o.mode === 'both') && o.spreadData && o.spreadData.dates && o.spreadData.dates.length;
    var head = ['日期'].concat(mainStore.names, ['合计']);
    if (hasY) head.push(o.yieldData.curve + ' ' + o.yieldData.tenor + '收益率(%)');
    if (hasS) head.push(o.spreadData.category + ' 利差(bp)');
    var rows = [head];
    var yA = hasY ? asofAlign(o.yieldData.dates, o.yieldData.values, mainStore.cats) : null;
    var sA = hasS ? asofAlign(o.spreadData.dates, o.spreadData.values, mainStore.cats) : null;
    mainStore.cats.forEach(function (d, i) {
      var row = [d].concat(mainStore.names.map(function (n) { var v = mainStore.raw[n][i]; return v == null ? '' : v.toFixed(2); }), [mainStore.total[i].toFixed(2)]);
      if (hasY) row.push(yA[i] == null ? '' : yA[i].toFixed(4));
      if (hasS) row.push(sA[i] == null ? '' : (sA[i] * 100).toFixed(2));
      rows.push(row);
    });
    downloadCsv('机构行为_主图_' + state.dim + '_' + state.granularity + '_' + mainStore.cats[0] + '_' + mainStore.cats[mainStore.cats.length - 1] + '.csv', rows);
  });
  $('dlSideBtn').addEventListener('click', function () {
    if (!sideStore || !state.selectedDate) return;
    var rows = [['主体', (state.sideMode === 'single' ? '当期净买入(亿)_' : '区间累计(亿)_') + state.selectedDate]];
    sideStore.forEach(function (it) { rows.push([it.name, it.value.toFixed(2)]); });
    downloadCsv('机构行为_明细_' + state.selectedDate + '.csv', rows);
  });
}

function resizeAllCharts() {
  Object.keys(charts).forEach(function (k) {
    if (charts[k]) charts[k].resize();
  });
}

/* ================= 控件绑定 ================= */
function bindSeg(elId, key, cb) {
  $(elId).querySelectorAll('button').forEach(function (b) {
    b.addEventListener('click', function () {
      $(elId).querySelectorAll('button').forEach(function (x) { x.classList.remove('active'); });
      b.classList.add('active');
      state[key] = b.dataset.value;
      if (cb) cb();
    });
  });
}
function bindControls() {
  bindSeg('dimSeg', 'dim', function () {
    $('groupSeg').classList.toggle('hidden', state.dim !== 'institution');
    state.selectedDate = null; loadMain();
  });
  bindSeg('groupSeg', 'groupMode', function () { if (mainStore) { buildMainStoreFromCache(); } });
  bindSeg('granSeg', 'granularity', function () { state.selectedDate = null; loadMain(); });
  bindSeg('typeSeg', 'chartType', function () { renderMain(); });
  bindSeg('sideModeSeg', 'sideMode', function () { renderSide(); });
  $('extremeSel').addEventListener('change', function () { state.extreme = this.value; if (mainStore) buildMainStoreFromCache(); });

  bindSeg('seasGranSeg', 'seasGran', loadSeasonality);
  $('seasMaSel').addEventListener('change', function () { state.seasMa = this.value; loadSeasonality(); });
  $('seasYearSel').addEventListener('change', function () { state.seasYear = this.value; loadSeasonality(); });
  $('seasWinsorChk').addEventListener('change', function () { state.seasWinsor = this.checked; loadSeasonality(); });
  $('seasWinsorChk').checked = state.seasWinsor;
  $('syAnomalyChk').addEventListener('change', function () { state.seasAnomaly = this.checked; loadSeasYield(); });
  $('seasRefreshBtn').addEventListener('click', loadSeasonality);

  /* ===== 叠加曲线控件 ===== */
  document.querySelectorAll('#ovlModeSeg button').forEach(function (b) {
    b.addEventListener('click', function () {
      if (b.disabled) return;
      var o = state.ovl;
      var v = b.dataset.value;
      // 利率品种无信用利差：切到含利差模式时自动换到默认信用品种
      if ((v === 'spread' || v === 'both') && ovlIsRate(o.curve)) {
        o.curve = '中短票AAA';
        if (OVL_CREDIT_TENORS.indexOf(o.tenor) < 0) o.tenor = '3Y';
        showErr('利率品种无信用利差，已切换至中短票AAA');
      }
      o.mode = v;
      syncOvlControls();
      ovlRefresh();
    });
  });
  $('ovlCurveSel').addEventListener('change', function () {
    var o = state.ovl;
    o.curve = this.value;
    o.proxy = null; o.tenorMiss = false;
    if (ovlIsRate(o.curve) && (o.mode === 'spread' || o.mode === 'both')) o.mode = 'yield';
    var valid = ovlTenorsFor(o.curve);
    if (valid.indexOf(o.tenor) < 0) o.tenor = ovlIsRate(o.curve) ? '10Y' : '3Y';
    setOvlManual();
    syncOvlControls();
    ovlRefresh();
  });
  document.querySelectorAll('#ovlLayoutSeg button').forEach(function (b) {
    b.addEventListener('click', function () {
      if (b.disabled) return;                          // 2σ开启时 overlay 按钮置灰
      if (state.ovl.layout === b.dataset.value) return;
      state.ovl.layout = b.dataset.value;
      syncOvlControls();
      renderMain();
      renderOvlSplit();
    });
  });
  $('ovlFollowChk').addEventListener('change', function () {
    state.ovl.follow = this.checked;
    if (state.ovl.follow) {
      ovlFollowFilters();
      syncOvlControls();
      ovlRefresh();
    }
  });
  $('ovlBandChk').addEventListener('change', function () {
    var o = state.ovl;
    if (this.checked) {
      // 开启2σ：记录原layout并强制分屏；若模式为none则默认切到yield（2σ需有曲线显示）
      if (o.mode === 'none') { o.mode = 'yield'; }
      if (o.layout !== 'split') { o.prevLayout = o.layout; o.layout = 'split'; }
      o.band = true;
    } else {
      // 关闭2σ：恢复开启前layout
      o.band = false;
      if (o.prevLayout && o.layout === 'split') { o.layout = o.prevLayout; }
      o.prevLayout = null;
    }
    syncOvlControls();   // 重算 bandYield/bandSpread 并刷新分屏锁
    ovlRefresh();        // 按需加载数据 + 渲染（含主图/分屏/快照）
  });
  $('ovlYieldColor').addEventListener('change', function () {
    state.ovl.colorYield = this.value;
    renderMain();
    renderOvlSplit();
  });
  $('ovlSpreadColor').addEventListener('change', function () {
    state.ovl.colorSpread = this.value;
    renderMain();
    renderOvlSplit();
  });
  $('seasYieldChk').addEventListener('change', function () {
    $('seasYieldCard').classList.toggle('hidden', !this.checked);
    if (this.checked) loadSeasYield();
  });
  $('seasYieldRefreshBtn').addEventListener('click', loadSeasYield);

  /* ===== 收益率季节性控件 ===== */
  document.querySelectorAll('#syTypeSeg button').forEach(function (b) {
    b.addEventListener('click', function () {
      if (b.disabled) return;
      syDemanual();
      syState.type = b.dataset.value;
      var list = syCurveList(syState.type);
      if (list.indexOf(syState.curve) < 0) syState.curve = list[0];
      var ts = syTenors(syState.type, syState.curve);
      if (ts.indexOf(syState.tenor) < 0) syState.tenor = syState.type === 'spread' ? '3Y' : (ovlIsRate(syState.curve) ? '10Y' : '3Y');
      syncSyControls();
      loadSeasYield();
    });
  });
  $('syCurveSel').addEventListener('change', function () {
    syDemanual();
    syState.curve = this.value;
    var ts = syTenors(syState.type, syState.curve);
    if (ts.indexOf(syState.tenor) < 0) syState.tenor = (ovlIsRate(syState.curve) && syState.type === 'yield') ? '10Y' : '3Y';
    syncSyControls();
    loadSeasYield();
  });
  $('syTenorSel').addEventListener('change', function () {
    syDemanual();
    syState.tenor = this.value;
    syncSyControls();
    loadSeasYield();
  });
  $('syFollowChk').addEventListener('change', function () {
    syState.follow = this.checked;
    syncSyControls();
    loadSeasYield();
  });
  $('syAddBtn').addEventListener('click', function () {
    var cur = syCurrent();
    var key = syKey(cur);
    if (syState.compare.some(function (it) { return syKey(it) === key; })) return showErr('该序列已在对比列表中');
    if (syState.compare.length >= 6) return showErr('对比序列最多 6 个');
    syState.compare.push({ type: cur.type, curve: cur.curve, tenor: cur.tenor });
    loadSeasYield();
  });

  $('domTopN').addEventListener('change', function () { state.domTopN = parseInt(this.value, 10); loadDominant(); });
  $('domRefreshBtn').addEventListener('click', loadDominant);

  /* ===== 单日机构行为控件 ===== */
  bindSeg('dailyRowSeg', 'dailyRowView', function () { renderDailyAll(); });
  bindSeg('dailyColSeg', 'dailyColView', function () { renderDailyAll(); });
  bindSeg('dailyChartSeg', 'dailyChart', function () {
    if (state.dailyChart === 'matrix') {
      $('dailyMatrixWrap').classList.remove('hidden');
      $('dailySankeyWrap').classList.add('hidden');
      renderDailyMatrix();
    } else {
      $('dailyMatrixWrap').classList.add('hidden');
      $('dailySankeyWrap').classList.remove('hidden');
      renderDailySankey();
    }
    setTimeout(resizeAllCharts, 30);
  });
  $('dailyDate').addEventListener('change', function () {
    if (!this.value) return;
    state.dailyDate = this.value;
    loadDaily();
  });
  $('dailyPrevBtn').addEventListener('click', function () {
    var d = state.dailyDate || (options && options.latest_date);
    if (!d) return;
    var prev = dailyTradeDates ? dailyAdjTradeDate(d, -1) : addDays(d, -1);
    if (!prev) { showErr('已是最早可用日期'); return; }
    state.dailyDate = prev;
    loadDaily();
  });
  $('dailyNextBtn').addEventListener('click', function () {
    var d = state.dailyDate || (options && options.latest_date);
    if (!d) return;
    var next = dailyTradeDates ? dailyAdjTradeDate(d, 1) : addDays(d, 1);
    var latest = options && options.latest_date;
    if (latest && next > latest) { showErr('已是最新可用日期'); return; }
    if (!next) { showErr('已是最新可用日期'); return; }
    state.dailyDate = next;
    loadDaily();
  });
  $('dailyLatestBtn').addEventListener('click', function () {
    if (!options) return;
    state.dailyDate = options.latest_date;
    $('dailyLatestBtn').classList.add('on');
    loadDaily();
  });
  $('dailyRefreshBtn').addEventListener('click', function () { loadDaily(); });

  $('refreshBtn').addEventListener('click', function () { loadedTabs = {}; loadKpi(); loadTab(state.tab, true); });
  $('clearAllBtn').addEventListener('click', function () {
    state.inst.clear(); state.bond.clear(); state.tenor.clear();
    renderFilterStates(); onFilterChange();
  });
  window.addEventListener('resize', resizeAllCharts);
}

/* ================= Tab ================= */
function loadTab(tab, force) {
  if (!force && loadedTabs[tab]) return;
  loadedTabs[tab] = true;
  if (tab === 'ts') loadMain();
  else if (tab === 'seas') loadSeasonality();
  else if (tab === 'dom') loadDominant();
  else if (tab === 'daily') { ensureDailyTradeDates().then(loadDaily); }
}
function bindTabs() {
  document.querySelectorAll('.tabs .tab').forEach(function (t) {
    t.addEventListener('click', function () {
      document.querySelectorAll('.tabs .tab').forEach(function (x) { x.classList.remove('active'); });
      t.classList.add('active');
      state.tab = t.dataset.tab;
      ['ts', 'seas', 'dom', 'daily'].forEach(function (k) { $('tab-' + k).classList.toggle('hidden', k !== state.tab); });
      loadTab(state.tab);
      setTimeout(resizeAllCharts, 60);
    });
  });
}

/* ================= 初始化 ================= */
function init() {
  if (!window.echarts) { showErr('ECharts 加载失败，请检查网络后刷新'); return; }
  api('options/', {}).then(function (res) {
    options = res;
    $('latestDateBadge').textContent = '数据更新至 ' + res.latest_date;
    state.endDate = res.latest_date;
    state.startDate = addMonths(res.latest_date, -12);
    // 主力矩阵默认区间（上游给出近一周）
    $('domStart').value = res.dominant_default_start_date || addDays(res.latest_date, -7);
    $('domEnd').value = res.dominant_default_end_date || res.latest_date;
    state.dailyDate = res.latest_date;
    $('dailyDate').value = res.latest_date;
    $('dailyLatestBtn').classList.add('on');

    buildFilterRow('instFilter', INST_GROUPS, state.inst);
    buildFilterRow('bondFilter', BOND_GROUPS, state.bond);
    buildFilterRow('tenorFilter', TENOR_GROUPS, state.tenor);
    renderFilterStates();
    bindDates(); bindControls(); bindTabs(); bindDownloads();

    // 叠加曲线控件构建（先同步构建保证可用，再异步拉 meta 标注数据范围）
    buildOvlCurveSel();
    syncOvlControls();
    syncSyControls();
    ovlApi('meta', {}).then(function (meta) {
      state.ovl.meta = meta;
      buildOvlCurveSel();
      syncOvlControls();
    }).catch(function (e) {
      showErr('叠加曲线元数据加载失败：' + e.message + '（机构行为主功能不受影响）');
    });

    loadKpi();
    loadMain();
  }).catch(function (e) { showErr('初始化失败：' + e.message + '（请确认 Flask 代理与上游服务可达）'); });
}

init();
})();
