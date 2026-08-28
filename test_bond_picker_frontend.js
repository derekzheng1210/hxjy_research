const assert = require('assert');
const fs = require('fs');

const source = fs.readFileSync('templates/bond_picker.html', 'utf8');
const script = source.slice(source.lastIndexOf('<script>') + 8, source.lastIndexOf('</script>'))
  .replace(/let RAW = .*?const INITIAL_DATA_VERSION = .*?;/s, 'let RAW=[];let MARKET_META={};const INITIAL_DATA_VERSION="test";');
new Function(script);
function between(start, end) {
  const from = source.lastIndexOf(start);
  const to = source.indexOf(end, from);
  assert(from >= 0 && to > from, `missing ${start}`);
  return source.slice(from, to);
}

const STATE = { favorites: new Set() };
eval(between('function bondMatchesFilter', 'function sortValue'));
eval(between('function compareNullable', 'function applyNormalFilters'));
eval(source.match(/function spreadClass\(value\)\{[^}]+\}/)[0]);

function filters(overrides = {}) {
  return {
    ct: '', entity: '', sub: '', search: '', rating: [], ir: [],
    minTerm: null, maxTerm: null, minY: null, maxY: null,
    hasOffer: true, twoSided: false, favoritesOnly: false,
    minOfferVolume: null, maxValuationOffer: null,
    offerAtOrAboveValuation: false,
    ...overrides,
  };
}

const cheapOffer = {
  code: '102681601.IB', name: '测试债', issuer: '测试主体', rating: 'AA+', ir: 'AA+',
  ct: '否', entity: '地方国企', sub: '否', term: 2, ytm: 2.1,
  hasOfr: true, twoSided: true, ofrVol: 5000, valOfr: -2,
};

assert(bondMatchesFilter(cheapOffer, filters({ minOfferVolume: 3000 })));
assert(!bondMatchesFilter(cheapOffer, filters({ minOfferVolume: 6000 })));
assert(bondMatchesFilter(cheapOffer, filters({ maxValuationOffer: 0 })));
assert(!bondMatchesFilter({ ...cheapOffer, valOfr: 1 }, filters({ maxValuationOffer: 0 })));
assert(!bondMatchesFilter({ ...cheapOffer, hasOfr: false }, filters()));
assert.strictEqual(compareNullable(null, 1, 'asc'), 1);
assert.strictEqual(compareNullable(1, null, 'desc'), -1);
assert.strictEqual(spreadClass(-1), 'spread-cheap');
assert.strictEqual(spreadClass(1), 'spread-rich');

const RECOMMENDATION_SETTINGS = {min_yield:1.65,max_yield:3,min_offer_volume:1000,bbb_minus_max_term:3,require_better_than_market:true};
const MARKET_META = {emotion:{value:-2}};
const numOrNull = value => value === null || value === '' || !Number.isFinite(Number(value)) ? null : Number(value);
eval(between('function ratingEligible', 'function computeRecommendations'));
const recommended = {ir:'BBB+',term:5,ytm:2.1,hasOfr:true,ofr:2.1,ofrVol:1000,counterpartyLimit:2,rating630:'ok'};
assert(recommendationEligible(recommended));
assert(!recommendationEligible({...recommended,ir:'BBB'}));
assert(recommendationEligible({...recommended,ir:'BBB',term:4.99}));

assert(source.includes('id="fRecommendedOnly"'));
assert(source.includes('id="fHasOffer"'));
assert(source.includes("activeTab:'convex'"));
assert(source.includes('COUNTERPARTY_LIMIT:27,RATING630:28'));
assert(source.includes('function recommendationEligible'));
assert(source.includes('function rating630Dot'));
assert(source.includes('Ofr(%)'));
assert(!source.includes("<th>#</th><th>主体</th><th>类型</th><th>凸点债券</th>"));

console.log('bond picker frontend logic: ok');
