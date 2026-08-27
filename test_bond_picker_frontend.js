const assert = require('assert');
const fs = require('fs');

const source = fs.readFileSync('templates/bond_picker.html', 'utf8');
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

console.log('bond picker frontend logic: ok');
