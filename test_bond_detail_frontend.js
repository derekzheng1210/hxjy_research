const assert = require('assert');
const fs = require('fs');

// Windows 检出为 CRLF 行尾时正则里的 \n 边界会失配，统一归一为 LF
const source = fs.readFileSync('templates/bond_detail.html', 'utf8').replace(/\r\n/g, '\n');
const scripts = [...source.matchAll(/<script>([\s\S]*?)<\/script>/g)];
assert(scripts.length, 'bond detail inline script is missing');

const script = scripts[scripts.length - 1][1]
  .replace('{{ initial_code|tojson }}', '""');
new Function(script);

const loadBond = script.match(/async function loadBond\(code\)\{([\s\S]*?)\}\nfunction render\(/);
assert(loadBond, 'loadBond function is missing');
const body = loadBond[1];
assert(
  body.indexOf("show('content')") < body.indexOf('render(d)'),
  'chart containers must be visible before ECharts initialization',
);
assert(
  body.indexOf('render(d)') < body.indexOf('scheduleChartResize()'),
  'charts must be resized after rendering',
);
assert(script.includes('new ResizeObserver(resizeCharts)'), 'container resize fallback is missing');
assert(script.includes('exclude_exchange_tech='), 'exchange tech-bond filter is not sent to the API');
assert(script.includes('rating_curve_points'), 'implied-rating curve overlay is missing');
assert(!script.includes('/summary'), 'bond detail must not call an LLM summary endpoint');
assert(source.includes('利差(BP)'), 'three-month return comparison must display BP spread');
assert(source.includes('id="rideChart"'), 'riding-return visualization is missing');
assert(script.includes('horizon_months=${rideMonths}'), '3/6 month riding toggle is not sent to the API');
assert(script.includes("addEventListener('change',refreshCurveFilter)"), 'curve filter must update locally');
assert(!script.includes("loadBond(current.bond.code)"), 'curve filter must not reload the whole page');
assert(script.includes("color:'#d97706'"), 'issuer curve needs a distinct orange color');
assert(script.includes("color:'#1d4ed8'"), 'rating curve needs a distinct blue color');
assert(script.includes('new AbortController()'), 'search must cancel the prior request');
assert(script.includes('searchAbort.abort()'), 'search must abort in-flight suggestions');
assert(script.includes('setTimeout(search,250)'), 'search must debounce input for 250ms');
assert(script.includes('searchRequestSeq'), 'stale search responses must be ignored');

// 摘要区仅保留“摘要”两个字
assert(source.includes('summary-title">摘要</div>'), 'summary title must read 摘要');
assert(!source.includes('规则诊断结论') && !source.includes('规则摘要'), 'old summary wordings must be removed');
assert(!script.includes('summaryMeta'), 'summary meta element is retired');

// 指标卡数值字号一致：标签用直接子选择器，数值内的 span 不再被压成 10px
assert(source.includes('.metric>span'), 'metric label selector must be direct-child');

// 主体关键日期利差表：期限列在中债估值列之前
assert(script.includes('<th>债券</th><th>期限</th><th>中债估值</th>'), 'spread table must list term before valuation');

// 经纪商报价：本券/主体视图切换，主体视图消费 issuer_latest
assert(source.includes('data-quote-mode'), 'broker quotes panel needs bond/issuer toggle');
assert(script.includes('renderIssuerQuotes'), 'issuer quotes renderer is missing');
assert(script.includes('issuer_latest'), 'issuer quotes view must consume issuer_latest');
assert(script.includes('bid_vs_valuation_bp') && script.includes('ofr_vs_valuation_bp'), 'issuer quotes must relate bid/ofr to valuation');
const issuerQuoteRenderer = script.match(/function renderIssuerQuotes\(q\)\{([\s\S]*?)\nfunction spreadChangeTone/);
assert(issuerQuoteRenderer, 'issuer quote renderer is missing');
assert(issuerQuoteRenderer[1].includes("type:'scatter'"), 'issuer quote view must use a scatter plot');
assert(issuerQuoteRenderer[1].includes("symbol:'diamond'"), 'Ofr points need a distinct marker');
assert(issuerQuoteRenderer[1].includes("xAxis:{type:'category'"), 'bonds must be arranged on the horizontal axis');
assert(issuerQuoteRenderer[1].includes("yAxis:{type:'value'"), 'quote deviation must be on the vertical BP axis');
assert(issuerQuoteRenderer[1].includes("formatter:'0BP'"), 'the valuation baseline must be labeled 0BP');

// 经纪商报价图例颜色必须与图一致：ECharts图例取series级itemStyle颜色，
// 只设lineStyle或数据点级颜色时图例会退回默认调色板
const bondQuoteRenderer = script.match(/function renderBondQuotes\(q,b\)\{([\s\S]*?)\nfunction renderIssuerQuotes/);
assert(bondQuoteRenderer, 'bond quote renderer is missing');
assert(
  bondQuoteRenderer[1].includes("itemStyle:{color:'#d9485f'}") && bondQuoteRenderer[1].includes("lineStyle:{color:'#d9485f'}"),
  'Bid legend marker must match the red line color',
);
assert(
  bondQuoteRenderer[1].includes("itemStyle:{color:'#0f8f67'}") && bondQuoteRenderer[1].includes("lineStyle:{color:'#0f8f67'}"),
  'Ofr legend marker must match the green line color',
);
assert(bondQuoteRenderer[1].includes("itemStyle:{color:'#64748b'}"), 'valuation legend marker must match the gray line color');
assert(issuerQuoteRenderer[1].includes("itemStyle:{color:'#d9485f'}"), 'issuer Bid legend must match the red scatter color');
assert(issuerQuoteRenderer[1].includes("itemStyle:{color:'#0f8f67'}"), 'issuer Ofr legend must match the green scatter color');

// 主体财务透视表：日期式报告期须折叠为标准期（2026-06-30→2026H1），
// 期间列按时间先后排序（年报按年末、H1/Q3按对应月），避免拆出不可比的孤立列
const pivotRenderer = script.match(/function aiPivotMetrics\(rows,category,title\)\{([\s\S]*?)\nfunction aiEventItem/);
assert(pivotRenderer, 'pivot metrics renderer is missing');
assert(pivotRenderer[1].includes("mo===6") && pivotRenderer[1].includes("mo===9") && pivotRenderer[1].includes("mo===3"),
  'date-form periods must fold into interim labels');
assert(pivotRenderer[1].includes('Q1:3,Q2:6,H1:6,Q3:9'), 'pivot periods must sort chronologically');
assert(pivotRenderer[1].includes("value写'—'") === false, 'placeholder rows come from data, not renderer');

// 主体曲线：样本点携带债券代码，点击可切换查询债券
assert(script.includes('[x.term,x.yield,x.name,x.code]'), 'issuer curve points must carry bond codes');
assert(script.includes("c.on('click'"), 'issuer curve chart must register a click handler');
assert(script.includes('loadBond(code)'), 'clicking a curve point must load that bond');
assert(script.includes('params.seriesName===\'隐含评级曲线\''), 'rating curve points must not be clickable');

// 发行人信用研究支持后台隐藏：AI面板条件渲染，aiOnBondLoaded对面板缺失做守卫
assert(source.includes('{% if credit_research_visible %}'), 'AI research panel must be conditionally rendered');
assert(script.includes("if(!document.getElementById('aiPanel'))return"), 'aiOnBondLoaded must no-op when panel is hidden');

console.log('bond detail chart lifecycle: ok');
