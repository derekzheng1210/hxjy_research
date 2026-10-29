(() => {
'use strict';
const $=id=>document.getElementById(id), charts={}, state={data:null,meta:null,seq:0,ovlSeq:0};
const fmt=(v,d=2)=>v==null||!Number.isFinite(Number(v))?'—':Number(v).toLocaleString('zh-CN',{minimumFractionDigits:d,maximumFractionDigits:d});
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(url){const r=await fetch(url,{headers:{Accept:'application/json'}});if(r.redirected)throw new Error('登录已失效，请重新登录门户');const p=await r.json();if(!r.ok)throw new Error(p.error||'读取失败');return p;}
function chart(id,option){const c=charts[id]||(charts[id]=echarts.init($(id)));c.setOption(option,true);}
function noData(id,text){chart(id,{title:{text,left:'center',top:'middle',textStyle:{fontSize:13,fontWeight:400,color:'#909399'}}});}
function kpi(label,value,unit,note,color=''){return `<div class="kpi"><div class="label">${label}</div><div class="value ${color}">${value}<small>${unit}</small></div><div class="note">${note}</div></div>`;}
function render(){
 const d=state.data,q=d.quote_summary,s=d.deal_summary;
 $('start').value=d.range.start;$('end').value=d.range.end;
 $('freshness').textContent=`报价快照 ${d.snapshot_at||'暂无'}${d.stale?' · 非今日快照':''} ｜ 盘面日报更新 ${d.report_updated||'暂无'}（成交至上一交易日）${d.snapshot_in_range?'':' ｜ 当前报价快照不在所选历史区间内'}`;
 $('quoteKpis').innerHTML=kpi('双边报价均值偏离',fmt(q.mid_dev),'BP',`Bid偏离 ${fmt(q.bid_dev)} / Ofr偏离 ${fmt(q.ofr_dev)} BP`,'blue')+kpi('平均买卖价差',fmt(q.width),'BP','有效双边逐券等权，观察报价宽窄')+kpi('有效报价券数',fmt(q.quoted,0),'只',`双边 ${fmt(q.two_sided,0)} 只 / 当前估值券池 ${fmt(q.pool,0)} 只`)+kpi('有效报价覆盖率',fmt(q.coverage_pct),'%','有单边或双边报价券 / 当前估值券池');
 $('dealKpis').innerHTML=kpi('区间成交笔数',s.days?fmt(s.num,0):'—','笔',`DM盘面日报全市场口径 · ${fmt(s.days,0)} 个交易日`)+kpi('TKN占比 · 主动买入',fmt(s.tkn_pct),'%',`${fmt(s.tkn,0)} 笔 / 区间总成交笔数`,'red')+kpi('GVN占比 · 主动卖出',fmt(s.gvn_pct),'%',`${fmt(s.gvn,0)} 笔 / 区间总成交笔数`,'green')+kpi('净主动成交笔数',s.days?fmt(s.net,0):'—','笔',`TKN−GVN ｜ 未分类 ${fmt(s.other_pct)}%`);
 $('coverage').textContent=s.days?`成交笔数与 TKN/GVN 占比来自 DM 信用债盘面日报（经纪商来源、全部信用券种）：交易日盘后发布，本页每日晨自动补齐至上一交易日。历史覆盖 ${d.report_range?.first||'—'} 至 ${d.report_range?.last||'—'}，本区间 ${fmt(s.days,0)} 个交易日${d.deals.length?`，最新一日 ${d.deals.at(-1).date} 成交 ${fmt(d.deals.at(-1).num,0)} 笔`:''}。`:`所选区间暂无盘面日报成交数据，历史覆盖 ${d.report_range?.first||'—'} 至 ${d.report_range?.last||'—'}，可调整区间后重试。`;
 renderDirection();renderStructure();renderMain();
}
function renderDirection(){
 const rows=state.data.deals.filter(x=>!isWeekend(x.date));if(!rows.length)return noData('directionChart','所选区间暂无盘面日报成交');
 chart('directionChart',{tooltip:{trigger:'axis',confine:true},legend:{top:8},grid:{left:55,right:55,top:52,bottom:55},dataZoom:[{type:'inside'},{type:'slider',height:15,bottom:10}],xAxis:{type:'category',data:rows.map(x=>x.date)},yAxis:[{type:'value',name:'占比 %',min:0,max:100},{type:'value',name:'成交笔数',minInterval:1}],series:[{name:'TKN',type:'bar',stack:'direction',data:rows.map(x=>x.tkn_pct),itemStyle:{color:'#f56c6c'}},{name:'GVN',type:'bar',stack:'direction',data:rows.map(x=>x.gvn_pct),itemStyle:{color:'#67c23a'}},{name:'未分类',type:'bar',stack:'direction',data:rows.map(x=>x.other_pct),itemStyle:{color:'#c0c4cc'}},{name:'成交笔数',type:'line',yAxisIndex:1,data:rows.map(x=>x.num),itemStyle:{color:'#5470c6'}}]});
}
function renderStructure(){
 if(!state.data)return;const rows=(state.data.breakdown[$('dimension').value]||[]).slice().sort((a,b)=>b.value-a.value);
 if(!rows.length)return noData('structureChart','暂无有效双边报价');
 chart('structureChart',{tooltip:{trigger:'axis',confine:true,formatter:p=>{const r=rows[p[0].dataIndex];return `${esc(r.label)}<br>偏离 ${fmt(r.value)} BP<br>有效双边 ${r.count} 只`;}},grid:{left:75,right:35,top:28,bottom:30},xAxis:{type:'value',name:'BP'},yAxis:{type:'category',data:rows.map(x=>x.label)},series:[{type:'bar',data:rows.map(x=>({value:x.value,itemStyle:{color:x.value<0?'#409eff':'#f0a65a'}})),label:{show:true,position:'right',formatter:p=>fmt(p.value)}}]});
}
async function initMeta(){try{state.meta=await api('/institution-flow/api/overlay/meta');const m=state.meta,options={...m.rate_curves,...m.credit_curves};$('curve').innerHTML=Object.keys(options).map(n=>`<option>${esc(n)}</option>`).join('');if(options['中短票AAA'])$('curve').value='中短票AAA';setTenors();}catch(e){$('overlayStatus').textContent=`曲线选项不可用：${e.message}`;}}
function setTenors(){const m=state.meta;if(!m)return;const c=$('curve').value,info=m.credit_curves[c]||m.rate_curves[c],previous=$('tenor').value;$('tenor').innerHTML=(info?.tenors||[]).map(t=>`<option>${esc(t)}</option>`).join('');if(info?.tenors.includes(previous))$('tenor').value=previous;else if(info?.tenors.includes('3Y'))$('tenor').value='3Y';}
const TS=window.BrokerSeries, overlayCache=new Map();
const isWeekend=d=>{const t=new Date(d+'T00:00:00Z').getUTCDay();return t===0||t===6;};
function cachedOverlay(url){if(!overlayCache.has(url)){const promise=api(url).catch(e=>{overlayCache.delete(url);throw e;});overlayCache.set(url,promise);}return overlayCache.get(url);}
function prepareSeries(points,isYield=false){
 const result=TS.prepare(points,$('smooth').value,$('transform').value);
 return isYield&&$('transform').value==='diff'?result.map(p=>({...p,value:p.value==null?null:p.value*100})):result;
}
async function renderMain(){
 if(!state.data)return;const token=++state.ovlSeq,d=state.data,metric=$('metric').value,mode=$('mode').value,curve=$('curve').value,tenor=$('tenor').value;
 const base=(metric==='emotion'?d.emotion:d.deals).map(x=>({date:x.date,value:metric==='emotion'?x.value:x[metric]})).filter(p=>!isWeekend(p.date));
 let y=null,s=null,notes=[];const info=state.meta?.credit_curves[curve],jobs=[];
 const getSeries=(kind,url)=>cachedOverlay(url).then(p=>{if(kind==='y')y=p;else s=p;}).catch(e=>notes.push(e.message));
 if(mode!=='none'&&curve&&tenor){if(mode==='both'||mode==='yield')jobs.push(getSeries('y',`/institution-flow/api/overlay/yield?curve=${encodeURIComponent(curve)}&tenor=${encodeURIComponent(tenor)}`));if(mode==='both'||mode==='spread'){if(info?.spread)jobs.push(getSeries('s',`/institution-flow/api/overlay/spread?category=${encodeURIComponent(info.spread)}&tenor=${encodeURIComponent(tenor)}`));else notes.push('该曲线无对应信用利差，请选择信用曲线');}}
 await Promise.all(jobs);if(token!==state.ovlSeq)return;
 // 类目横轴只含交易日（周六周日含调休不展示），柱状图连续无周末空档；
 // 各序列缺日置 null 且连线跨过，互不打洞。
 const native=(p,mult)=>p?p.dates.map((date,i)=>({date,value:typeof p.values[i]==='number'&&Number.isFinite(p.values[i])?p.values[i]*mult:null})).filter(p=>p.date>=d.range.start&&p.date<=d.range.end&&!isWeekend(p.date)):[];
 const rawYield=native(y,1),rawSpread=native(s,100),signal=prepareSeries(base),yieldPoints=prepareSeries(rawYield,true),spreadPoints=prepareSeries(rawSpread);
 state.analysis={signal,yieldPoints,spreadPoints,rawYield:rawYield.map(p=>({...p,value:p.value==null?null:p.value*100})),rawSpread,curve,tenor};
 const dates=[...new Set([...base,...rawYield,...rawSpread].map(p=>p.date))].sort();
 const transformed=$('transform').value,z=transformed==='z20',diff=transformed==='diff',smoothing=$('smooth').value,tag=(smoothing==='none'?'':` · ${smoothing.toUpperCase()}`)+(z?' · Z20':diff?' · Δ':'');
 const signalUnit=z?'Z-score':metric==='emotion'?'BP':metric.includes('pct')?(diff?'百分点':'%'):'笔',yieldUnit=z?'Z-score':diff?'BP':'%';
 $('processingStatus').textContent=`${smoothing==='none'?'不平滑':smoothing.toUpperCase()} → ${$('transform').selectedOptions[0].textContent} · 仅使用当期及历史数据`;
 $('chartNote').textContent='横轴为各序列交易日并集（周六周日含调休不展示）；某序列缺日时连线跨过、不打洞，显式缺值会中断平滑窗口，窗口不足留空。平滑后再求变化量或标准化，日期按同日匹配。'+(diff?'收益率变化换算为BP，占比变化为百分点。':'');
 const series=[];
 function add(points,label,axis,color,type){const map=new Map(points.filter(p=>p.value!=null).map(p=>[p.date,p.value]));if(!map.size)notes.push(label+'在当前处理下无有效值');series.push({name:label+tag,type,yAxisIndex:z?0:axis,data:dates.map(day=>map.get(day)??null),connectNulls:true,showSymbol:false,itemStyle:{color},lineStyle:{width:2}});}
 add(signal,`${$('metric').selectedOptions[0].textContent.replace(/（.*?）/g,'')}（${signalUnit}）`,0,'#409eff',metric==='emotion'?'line':'bar');
 if(y)add(yieldPoints,`${curve} ${tenor}（${yieldUnit}）`,1,'#1565c0','line');
 if(s)add(spreadPoints,`${info?.spread} ${tenor}（${z?'Z-score':'BP'}）`,2,'#8e24aa','line');
 $('overlayStatus').textContent=notes.join('；')||'交易日横轴 · 缺日连线跨过 · 周末不展示';
 if(dates.length)chart('mainChart',{tooltip:{trigger:'axis',confine:true,valueFormatter:v=>fmt(v,3)},legend:{top:5,type:'scroll'},grid:{left:65,right:z?35:s?140:75,top:65,bottom:65},dataZoom:[{type:'inside'},{type:'slider',height:18,bottom:12}],xAxis:{type:'category',data:dates},yAxis:[{type:'value',name:signalUnit,scale:true,splitLine:{lineStyle:{color:'#ebeef5'}}},{type:'value',name:`收益率 ${yieldUnit}`,position:'right',scale:true,show:!!y&&!z,splitLine:{show:false}},{type:'value',name:'利差 BP',position:'right',offset:y?72:0,scale:true,show:!!s&&!z,splitLine:{show:false}}],series});
 else noData('mainChart','所选区间暂无数据');
 renderAnalysis();
}
function renderAnalysis(){
 const a=state.analysis;if(!a)return;
 const target=$('analysisTarget').value,h=Number($('horizon').value),win=Number($('corrWindow').value),spread=target==='spread',label=spread?'利差':'收益率',raw=spread?a.rawSpread:a.rawYield,processed=spread?a.spreadPoints:a.yieldPoints;
 const pairs=TS.matched(a.signal,processed),future=TS.forward(a.signal,raw,h),pearson=TS.pearson(pairs),spearman=TS.spearman(pairs),lead=TS.pearson(future),test=TS.holdout(future);
 state.analysisPairs=future;
 $('analysisKpis').innerHTML=kpi('同期 Pearson',fmt(pearson),'',`共同有效观测 ${pairs.length} 期`)+kpi('同期 Spearman',fmt(spearman),'','秩相关，观察单调关系')+kpi(`领先 ${h} 期相关系数`,fmt(lead),'',`指标t vs 未来${h}期${label}变化 · ${future.length} 对`)+kpi('时间留出检验 R²',fmt(test?.r2),'',test?`训练 ${test.train} / 检验 ${test.test} 对 · 方向命中 ${fmt(test.hit,1)}%`:'至少20对且清除跨界标签后训练≥10、检验≥6');
 const rolling=TS.rolling(a.signal,processed,win);
 if(!processed.length)noData('correlationChart','请在上方叠加对应价格参照');
 else if(!rolling.some(p=>p.value!=null))noData('correlationChart',`滚动${win}期有效样本不足`);
 else chart('correlationChart',{tooltip:{trigger:'axis',confine:true,formatter:p=>{const r=rolling[p[0].dataIndex];return `${esc(r.date)}<br>Pearson ${fmt(r.value,3)}<br>有效配对 ${r.n}/${win}`;}},grid:{left:45,right:25,top:20,bottom:55},dataZoom:[{type:'inside'},{type:'slider',height:14,bottom:10}],xAxis:{type:'category',data:rolling.map(p=>p.date)},yAxis:{type:'value',min:-1,max:1},series:[{name:'滚动Pearson',type:'line',connectNulls:false,showSymbol:false,data:rolling.map(p=>p.value),itemStyle:{color:'#409eff'},markLine:{silent:true,symbol:'none',data:[{yAxis:0}],lineStyle:{color:'#a8b0bc'}}}]});
 const horizons=[1,3,5,10,20],scan=horizons.map(n=>{const p=TS.forward(a.signal,raw,n);return {h:n,n:p.length,value:TS.pearson(p)};});
 if(!raw.length)noData('leadChart','请在上方叠加对应价格参照');
 else chart('leadChart',{tooltip:{trigger:'axis',confine:true,formatter:p=>{const r=scan[p[0].dataIndex];return `领先${r.h}期<br>相关系数 ${fmt(r.value,3)}<br>有效配对 ${r.n}`;}},grid:{left:45,right:25,top:20,bottom:35},xAxis:{type:'category',data:scan.map(p=>`${p.h}期`)},yAxis:{type:'value',min:-1,max:1},series:[{type:'bar',data:scan.map(p=>({value:p.value,itemStyle:{color:p.h===h?'#409eff':'#b3d8ff'}})),label:{show:true,position:'top',formatter:p=>fmt(p.value)},markLine:{silent:true,symbol:'none',data:[{yAxis:0}],lineStyle:{color:'#a8b0bc'}}}]});
 $('analysisStatus').textContent=`同期使用图中处理后的序列；领先检验以处理后的指标，对照未来${h}个价格观测期的原始${label}累计变化（BP）。相关系数至少10对，滚动窗口需≥80%配对。时间留出按前70%训练、后30%检验，剔除训练标签跨入检验期的记录；R²相对训练期均值基准，负值代表未超过基准。`;
 $('exportAnalysis').disabled=!future.length;
}
function exportAnalysis(){
 const rows=state.analysisPairs||[];if(!rows.length)return;
 const cell=v=>'"'+String(v??'').replace(/"/g,'""')+'"',name=$('metric').selectedOptions[0].textContent;
 const csv='\uFEFF'+[['指标日期','目标日期',name+'（'+$('smooth').value+' / '+$('transform').value+'）','未来'+$('horizon').value+'期'+$('analysisTarget').selectedOptions[0].textContent+'变化(BP)'],...rows.map(r=>[r.date,r.end,r.x,r.y])].map(r=>r.map(cell).join(',')).join('\r\n');
 const u=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'})),el=document.createElement('a');el.href=u;el.download='经纪商领先检验配对数据.csv';el.click();setTimeout(()=>URL.revokeObjectURL(u),1000);
}

async function load(initial=false){
 const params=new URLSearchParams({scope:$('scope').value});if(!initial){if(!$('start').value||!$('end').value){$('error').hidden=false;$('error').textContent='请选择完整的起止日期';return;}params.set('start',$('start').value);params.set('end',$('end').value);}
 const token=++state.seq;state.ovlSeq++;$('error').hidden=true;$('refresh').disabled=true;document.querySelector('main').setAttribute('aria-busy','true');
 try{const data=await api('/api/broker-data?'+params);if(token!==state.seq)return;state.data=data;render();}catch(e){if(token!==state.seq)return;$('error').hidden=false;$('error').textContent=`读取失败：${e.message}。${state.data?'下方保留上次成功结果，尚未应用本次筛选。':''}`;}finally{if(token===state.seq){$('refresh').disabled=false;document.querySelector('main').removeAttribute('aria-busy');}}
}
$('refresh').onclick=()=>{overlayCache.clear();load();};$('apply').onclick=()=>{document.querySelectorAll('#ranges button').forEach(b=>b.classList.remove('on'));load();};$('scope').onchange=()=>load();
document.querySelectorAll('#ranges button').forEach(b=>b.onclick=()=>{if(!state.data)return;const end=state.data.snapshot_at?.slice(0,10)||state.data.range.end,dt=new Date(end+'T00:00:00Z');dt.setUTCDate(dt.getUTCDate()-Number(b.dataset.days));$('end').value=end;$('start').value=dt.toISOString().slice(0,10);document.querySelectorAll('#ranges button').forEach(x=>x.classList.toggle('on',x===b));load();});
for(const id of ['metric','mode','tenor','smooth','transform'])$(id).onchange=renderMain;$('curve').onchange=()=>{setTenors();renderMain();};$('dimension').onchange=renderStructure;for(const id of ['analysisTarget','corrWindow','horizon'])$(id).onchange=renderAnalysis;$('exportAnalysis').onclick=exportAnalysis;window.addEventListener('resize',()=>Object.values(charts).forEach(c=>c.resize()));
Promise.all([initMeta(),load(true)]).then(renderMain);
})();
