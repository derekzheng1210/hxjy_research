const assert = require('assert');
const fs = require('fs');

const source = fs.readFileSync('templates/secondary_bond_picker.html', 'utf8');
const script = source.slice(source.lastIndexOf('<script>') + 8, source.lastIndexOf('</script>'))
  .replace(/let RAW=.*?const INITIAL_VERSION=.*?;/s, 'let RAW=[];let META={};const INITIAL_VERSION="test";');
new Function(script);
function functionSource(name, nextName) {
  const start = source.indexOf(`function ${name}`);
  const end = source.indexOf(`function ${nextName}`, start);
  assert(start >= 0 && end > start, `missing ${name}`);
  return source.slice(start, end);
}

const DEFAULT_SETTINGS = {min_yield:1.65,max_yield:3,min_offer_volume:1000,bbb_minus_max_term:3,require_better_than_market:true};
const state = {settings:{...DEFAULT_SETTINGS}};
const META = {emotion:{value:-2}};
const n = value => value === null || value === '' || value === undefined ? null : Number(value);
eval(functionSource('ratingEligible', 'recommendationEligible'));
eval(functionSource('recommendationEligible', 'computeRecommendations'));

const base = {ir:'BBB',term:5,ytm:2.1,hasOfr:true,ofr:2.1,ofrVol:1000,counterpartyLimit:2,rating630:'ok'};
assert(ratingEligible(base));
assert(ratingEligible({...base,ir:'BBB-',term:3}));
assert(!ratingEligible({...base,ir:'BBB-',term:3.01}));
assert(!ratingEligible({...base,ir:''}));
assert(recommendationEligible(base));
assert(!recommendationEligible({...base,ofrVol:999}));
assert(!recommendationEligible({...base,ytm:1.64}));
assert(!recommendationEligible({...base,ofr:2.07}));
assert(!recommendationEligible({...base,counterpartyLimit:1}));
assert(!recommendationEligible({...base,counterpartyLimit:null}));

// 合规630跟踪评级：必须已校验且满足（ok）才可推荐
assert(!recommendationEligible({...base,rating630:'fail'}));
assert(recommendationEligible({...base,rating630:'ok'}));
assert(!recommendationEligible({...base,rating630:'unknown'}));
assert(!recommendationEligible({...base,rating630:undefined}));

eval(functionSource('rating630Dot', 'computeRecommendations'));
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
assert.strictEqual(rating630Dot({rating630:'ok',rating630Note:''}), '');
assert(rating630Dot({rating630:'fail',rating630Note:''}).includes('rating-dot'));
const dotHtml = rating630Dot({rating630:'fail',rating630Note:'缺少2026年1月1日-6月30日的主体跟踪评级'});
assert(dotHtml.includes('可能不满足合规630评级要求，投前请务必确认'));
assert(dotHtml.includes('缺少2026年1月1日-6月30日'));
assert(rating630Dot({rating630:'unknown',rating630Note:'暂无评级数据，未校验'}).includes('rating-dot'));

eval(source.match(/const F=\{[^}]+\};/)[0] + ';' + functionSource('toBond', 'esc').split('let BONDS=')[0]);
const row = new Array(29).fill(null);
row[28] = ['fail', '缺少2026年1月1日-6月30日的主体跟踪评级'];
const bond = toBond(row);
assert.strictEqual(bond.rating630, 'fail');
assert.strictEqual(bond.rating630Note, '缺少2026年1月1日-6月30日的主体跟踪评级');
assert.strictEqual(toBond(new Array(28).fill(null)).rating630, 'unknown');

eval(functionSource('compareSort', 'sortBy'));
assert.strictEqual(compareSort(null, 1, 'asc'), 1);
assert.strictEqual(compareSort(null, 1, 'desc'), 1);
assert(compareSort(1, 2, 'asc') < 0);
assert(compareSort(1, 2, 'desc') > 0);

// 情绪图纵坐标自适应：刻度取 1/2/5×10^n 的整档
eval(functionSource('niceStep', 'drawEmotionChart'));
assert.strictEqual(niceStep(0.8), 0.2);
assert.strictEqual(niceStep(2), 0.5);
assert.strictEqual(niceStep(3), 0.5);
assert.strictEqual(niceStep(8), 2);
assert.strictEqual(niceStep(20), 5);
assert.strictEqual(niceStep(40), 10);

assert(source.includes('中债-Ofr（BP）'));
assert(!source.includes('Bid-Ofr'));
assert(source.includes('id="viewAll"'));
assert(source.includes('id="viewRecommended"'));
assert(!source.includes('id="recommendList"'));
assert(!source.includes('共享筛选预设'));
assert(!source.includes('id="preset"'));
assert(source.includes('可用对手限额'));
assert(source.includes('id="ratingPill"'));
assert(source.includes('function renderRatingPill'));
assert(source.includes("if(b.rating630!=='ok')return false"));
assert(source.includes("tableHeader('中债估值','ytm'"));
assert(source.includes('table-layout:fixed'));
assert(source.includes('.sort-mark{display:inline-block;width:10px'));
assert(source.includes(":'&nbsp;';return `<span class=\"sort-mark\">"));
console.log('secondary bond picker frontend logic: ok');
