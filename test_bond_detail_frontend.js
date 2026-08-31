const assert = require('assert');
const fs = require('fs');

const source = fs.readFileSync('templates/bond_detail.html', 'utf8');
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

console.log('bond detail chart lifecycle: ok');
