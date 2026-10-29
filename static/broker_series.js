/* Trailing-only transforms and chronological correlation diagnostics. */
(function(root,factory){if(typeof module==='object'&&module.exports)module.exports=factory();else root.BrokerSeries=factory();})(typeof globalThis!=='undefined'?globalThis:this,function(){
'use strict';
const valid=v=>typeof v==='number'&&Number.isFinite(v);
function smooth(values,kind='none'){
 if(kind==='none')return values.map(v=>valid(v)?v:null);
 const n=Number(kind.replace(/\D/g,'')),ema=kind.startsWith('ema');
 if(!n)return values.slice();
 let prior=null,window=[];
 return values.map(v=>{
  if(!valid(v)){window=[];prior=null;return null;}
  window.push(v);if(window.length>n)window.shift();
  if(ema&&prior!==null){prior=2/(n+1)*v+(1-2/(n+1))*prior;return prior;}
  if(window.length<n)return null;
  const mean=window.reduce((a,b)=>a+b,0)/n;if(ema)prior=mean;return mean;
 });
}
function transform(values,kind='level'){
 return values.map((v,i)=>{
  if(!valid(v))return null;
  if(kind==='diff')return i&&valid(values[i-1])?v-values[i-1]:null;
  if(kind!=='z20')return v;
  const w=values.slice(Math.max(0,i-19),i+1);if(w.length<20||w.some(x=>!valid(x)))return null;
  const mean=w.reduce((a,b)=>a+b,0)/20,sd=Math.sqrt(w.reduce((a,b)=>a+(b-mean)**2,0)/20);
  return sd>1e-12?(v-mean)/sd:null;
 });
}
function prepare(points,smoothing='none',transformation='level'){
 const sorted=points.slice().sort((a,b)=>a.date.localeCompare(b.date));
 const values=transform(smooth(sorted.map(p=>p.value),smoothing),transformation);
 return sorted.map((p,i)=>({date:p.date,value:values[i]}));
}
function pearson(pairs,min=10){
 const p=pairs.filter(r=>valid(r.x)&&valid(r.y));if(p.length<min)return null;
 const mx=p.reduce((a,r)=>a+r.x,0)/p.length,my=p.reduce((a,r)=>a+r.y,0)/p.length;
 let xx=0,yy=0,xy=0;for(const r of p){xx+=(r.x-mx)**2;yy+=(r.y-my)**2;xy+=(r.x-mx)*(r.y-my);}
 return xx>1e-20&&yy>1e-20?Math.max(-1,Math.min(1,xy/Math.sqrt(xx*yy))):null;
}
function ranks(values){
 const order=values.map((v,i)=>({v,i})).sort((a,b)=>a.v-b.v),out=[];
 for(let i=0;i<order.length;){let j=i+1;while(j<order.length&&order[j].v===order[i].v)j++;for(let k=i;k<j;k++)out[order[k].i]=(i+j-1)/2+1;i=j;}return out;
}
function spearman(pairs){const p=pairs.filter(r=>valid(r.x)&&valid(r.y)),rx=ranks(p.map(r=>r.x)),ry=ranks(p.map(r=>r.y));return pearson(p.map((r,i)=>({x:rx[i],y:ry[i]})));}
function matched(signal,target){const m=new Map(signal.map(p=>[p.date,p.value]));return target.filter(p=>valid(p.value)&&valid(m.get(p.date))).map(p=>({date:p.date,x:m.get(p.date),y:p.value}));}
function forward(signal,rawTarget,horizon){
 const m=new Map(signal.map(p=>[p.date,p.value])),out=[];
 // Keep explicit missing target rows: never jump across them to shorten a horizon.
 const rows=rawTarget.slice().sort((a,b)=>a.date.localeCompare(b.date));
 for(let i=0;i+horizon<rows.length;i++){
  const r=rows[i],future=rows[i+horizon];
  if(!valid(m.get(r.date))||rows.slice(i,i+horizon+1).some(p=>!valid(p.value)))continue;
  out.push({date:r.date,end:future.date,x:m.get(r.date),y:future.value-r.value});
 }return out;
}
function rolling(signal,target,window=20){
 const m=new Map(signal.map(p=>[p.date,p.value]));
 return target.map((p,i)=>{
  const w=target.slice(Math.max(0,i-window+1),i+1),pairs=w.filter(r=>valid(r.value)&&valid(m.get(r.date))).map(r=>({x:m.get(r.date),y:r.value}));
  return {date:p.date,value:w.length===window?pearson(pairs,Math.max(10,Math.ceil(window*.8))):null,n:pairs.length};
 });
}
function holdout(pairs){
 if(pairs.length<20)return null;
 const split=Math.floor(pairs.length*.7),train=pairs.slice(0,split),test=pairs.slice(split);
 // Purge labels whose future endpoint reaches the test period.
 const usable=train.filter(p=>p.end<test[0].date);if(usable.length<10||test.length<6)return null;
 const mx=usable.reduce((a,p)=>a+p.x,0)/usable.length,my=usable.reduce((a,p)=>a+p.y,0)/usable.length;
 const xx=usable.reduce((a,p)=>a+(p.x-mx)**2,0);if(xx<1e-20)return null;
 const slope=usable.reduce((a,p)=>a+(p.x-mx)*(p.y-my),0)/xx;
 const loss=test.reduce((a,p)=>a+(p.y-(my+slope*(p.x-mx)))**2,0),baseline=test.reduce((a,p)=>a+(p.y-my)**2,0);
 const directional=test.filter(p=>p.y!==0),hit=directional.filter(p=>Math.sign(my+slope*(p.x-mx))===Math.sign(p.y)).length;
 return {train:usable.length,test:test.length,r2:baseline>1e-20?1-loss/baseline:null,hit:directional.length?hit/directional.length*100:null,directional:directional.length};
}
return {smooth,transform,prepare,pearson,spearman,matched,forward,rolling,holdout};
});
