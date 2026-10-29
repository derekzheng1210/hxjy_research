const assert=require('assert');
const T=require('./static/broker_series.js');
assert.deepStrictEqual(T.smooth([1,2,3,4,5,6],'ma5'),[null,null,null,null,3,4]);
assert.deepStrictEqual(T.smooth([1,2,3,4,5,null,7,8,9,10,11],'ma5'),[null,null,null,null,3,null,null,null,null,null,9]);
assert.deepStrictEqual(T.smooth([1,2,3,4,5,6],'ema5'),[null,null,null,null,3,4]);
assert.deepStrictEqual(T.transform([1,3,null,8,10],'diff'),[null,2,null,null,2]);
assert.strictEqual(T.transform(Array(20).fill(1),'z20')[19],null);
const values=Array.from({length:60},(_,i)=>i+Math.sin(i));
for(const smooth of ['none','ma5','ma10','ma20','ema5','ema10','ema20'])for(const transform of ['level','diff','z20']){
 const a=T.transform(T.smooth(values,smooth),transform),b=T.transform(T.smooth(values.slice(0,40),smooth),transform);
 assert.deepStrictEqual(a.slice(0,40),b,'transform must not use future observations');
}
const points=values.map((value,i)=>({date:`2026-${String(Math.floor(i/28)+1).padStart(2,'0')}-${String(i%28+1).padStart(2,'0')}`,value}));
assert.deepStrictEqual(T.prepare(points,'ma5','level').map(p=>p.value),T.smooth(values,'ma5'));
const pairs=values.map(x=>({x,y:2*x+3}));
assert(Math.abs(T.pearson(pairs)-1)<1e-12);assert(Math.abs(T.spearman(pairs)-1)<1e-12);
assert.strictEqual(T.pearson(pairs.slice(0,9)),null);assert.strictEqual(T.pearson(Array(20).fill({x:1,y:2})),null);
assert.deepStrictEqual(T.matched([{date:'a',value:1},{date:'b',value:null}],[{date:'a',value:3},{date:'b',value:4},{date:'c',value:5}]),[{date:'a',x:1,y:3}]);
const signal=points.map(p=>({...p,value:2*p.value+1}));
const future=T.forward(signal,points,5);assert.strictEqual(future.length,55);assert.strictEqual(future[0].date,points[0].date);assert.strictEqual(future[0].end,points[5].date);assert.strictEqual(future[0].y,points[5].value-points[0].value);
const missing=points.map((p,i)=>({...p,value:i===2?null:p.value}));assert(!T.forward(signal,missing,5).some(p=>p.date<=points[2].date));
assert.strictEqual(T.rolling(signal,points,20).filter(p=>p.value!==null).length,41);
const training=Array.from({length:40},(_,i)=>({date:String(i).padStart(3,'0'),end:String(i+5).padStart(3,'0'),x:i,y:2*i+3}));
const result=T.holdout(training);assert.strictEqual(result.train,23);assert.strictEqual(result.test,12);assert(Math.abs(result.r2-1)<1e-12);
assert.strictEqual(T.holdout(training.slice(0,19)),null);
console.log('Broker time-series processing: smoothing, no-lookahead, missing values, correlations, forward labels and purged holdout passed');
