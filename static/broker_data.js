(() => {
'use strict';
const $=id=>document.getElementById(id), charts={}, state={data:null,meta:null,seq:0,ovlSeq:0,mainDates:[],axisInUse:[0],axisNames:{},
 draw:{mode:'',pending:null,mouse:null,lines:[],visible:true,cfgKey:'',zoomBound:false}};
const fmt=(v,d=2)=>v==null||!Number.isFinite(Number(v))?'—':Number(v).toLocaleString('zh-CN',{minimumFractionDigits:d,maximumFractionDigits:d});
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(url){const r=await fetch(url,{headers:{Accept:'application/json'}});if(r.redirected)throw new Error('登录已失效，请重新登录门户');const p=await r.json();if(!r.ok)throw new Error(p.error||'读取失败');return p;}
function chart(id,option){const c=charts[id]||(charts[id]=echarts.init($(id)));c.setOption(option,true);return c;}
function noData(id,text){chart(id,{title:{text,left:'center',top:'middle',textStyle:{fontSize:13,fontWeight:400,color:'#909399'}}});}
function loadLS(key){try{const raw=localStorage.getItem(key);return raw?JSON.parse(raw):null;}catch(e){return null;}}
function saveLS(key,value){try{value==null?localStorage.removeItem(key):localStorage.setItem(key,JSON.stringify(value));}catch(e){/* 隐私模式等场景静默降级为不记忆 */}}

/* ── 曲线颜色（本地记忆） ─────────────────────────────── */
const DEFAULT_COLORS={signal:'#409eff',yield:'#1565c0',spread:'#8e24aa'};
const seriesColors={...DEFAULT_COLORS,...(loadLS('brokerMainColors')||{})};
function syncColorInputs(){for(const [id,key] of [['colorSignal','signal'],['colorYield','yield'],['colorSpread','spread']])$(id).value=seriesColors[key];}

/* ── 报价偏离结构：维度 × 指标 × 快照/趋势 ─────────────── */
const STRUCT_METRICS={
 mid:{label:'中枢偏离',unit:'BP',get:g=>g.value},
 bid:{label:'Bid偏离',unit:'BP',get:g=>g.bid},
 ofr:{label:'Ofr偏离',unit:'BP',get:g=>g.ofr},
 width:{label:'买卖价差',unit:'BP',get:g=>g.width},
 coverage:{label:'双边覆盖率',unit:'%',get:g=>g.pool?Math.round(g.count/g.pool*1000)/10:null},
};
function render(){
 const d=state.data;
 $('start').value=d.range.start;$('end').value=d.range.end;
 $('freshness').textContent=`报价快照 ${d.snapshot_at||'暂无'}${d.stale?' · 非今日快照':''}（挂盘情绪与报价偏离结构基准） ｜ 盘面日报更新 ${d.report_updated||'暂无'} · 成交历史覆盖 ${d.report_range?.first||'—'} 至 ${d.report_range?.last||'—'}（成交至上一交易日）${d.snapshot_in_range?'':' ｜ 当前报价快照不在所选历史区间内'}`;
 renderDirection();renderStructure();renderMain();
}
function renderDirection(){
 const rows=state.data.deals.filter(x=>!isWeekend(x.date));if(!rows.length)return noData('directionChart','所选区间暂无盘面日报成交');
 chart('directionChart',{tooltip:{trigger:'axis',confine:true},legend:{top:8},grid:{left:55,right:55,top:52,bottom:55},dataZoom:[{type:'inside'},{type:'slider',height:15,bottom:10}],xAxis:{type:'category',data:rows.map(x=>x.date)},yAxis:[{type:'value',name:'占比 %',min:0,max:100},{type:'value',name:'成交笔数',minInterval:1}],series:[{name:'TKN',type:'bar',stack:'direction',data:rows.map(x=>x.tkn_pct),itemStyle:{color:'#f56c6c'}},{name:'GVN',type:'bar',stack:'direction',data:rows.map(x=>x.gvn_pct),itemStyle:{color:'#67c23a'}},{name:'未分类',type:'bar',stack:'direction',data:rows.map(x=>x.other_pct),itemStyle:{color:'#c0c4cc'}},{name:'成交笔数',type:'line',yAxisIndex:1,data:rows.map(x=>x.num),itemStyle:{color:'#5470c6'}}]});
}
function renderStructure(){
 if(!state.data)return;
 const d=state.data,dim=$('dimension').value,view=$('structureView').value,metric=STRUCT_METRICS[$('structureMetric').value];
 $('structureMetric').disabled=view==='trend';
 if(view==='trend'){
  const days=d.emotion.filter(x=>!isWeekend(x.date));
  const groups=new Map();
  for(const day of days)for(const g of (day.breakdown||{})[dim]||[]){
   if(g.value==null)continue;
   if(!groups.has(g.label))groups.set(g.label,new Map());
   groups.get(g.label).set(day.date,g.value);
  }
  const names=[...groups.keys()];
  const dates=days.map(x=>x.date);
  if(!names.length||!dates.length){$('structureStatus').textContent='';return noData('structureChart',dim==='bond_type'?'历史点位暂无券种分组（自启用日起逐日积累）':'所选区间暂无分组偏离历史');}
  const ordered=names.map(label=>{const m=groups.get(label);return {label,m,n:m.size};}).sort((a,b)=>b.n-a.n);
  chart('structureChart',{color:['#409eff','#f57c00','#8e24aa','#19a15f','#c23531','#1565c0','#d4a017','#7e6bb8','#3ba272','#e8746a','#5ab7b2','#bda29a','#6e7074','#ff9dbb','#89c3eb','#f2b3c6'],tooltip:{trigger:'axis',confine:true,valueFormatter:v=>fmt(v)},legend:{top:4,type:'scroll',icon:'roundRect'},grid:{left:55,right:25,top:56,bottom:50},dataZoom:[{type:'inside'},{type:'slider',height:13,bottom:8}],xAxis:{type:'category',data:dates},yAxis:{type:'value',name:'中枢偏离 BP',scale:true,splitLine:{lineStyle:{color:'#ebeef5'}}},series:ordered.map(g=>({name:g.label,type:'line',showSymbol:false,connectNulls:true,data:dates.map(day=>g.m.get(day)??null)}))});
  $('structureStatus').textContent=`${dates.length} 个交易日 · 中枢偏离（BP） · ${ordered.length} 组`;
  return;
 }
 const rows=(d.breakdown[dim]||[]).map(g=>({g,v:metric.get(g)})).filter(r=>r.v!=null).sort((a,b)=>b.v-a.v);
 if(!rows.length){$('structureStatus').textContent='';return noData('structureChart','暂无有效报价');}
 chart('structureChart',{tooltip:{trigger:'axis',confine:true,formatter:p=>{const r=rows[p[0].dataIndex].g,cover=r.pool?Math.round(r.count/r.pool*1000)/10:null;return `${esc(r.label)}<br>${metric.label} ${fmt(metric.get(r))} ${metric.unit}<br>中枢 ${fmt(r.value)} / Bid ${fmt(r.bid)} / Ofr ${fmt(r.ofr)} BP<br>价差 ${fmt(r.width)} BP ｜ 双边 ${r.count} 只 · 单边Bid ${r.bid_n} / Ofr ${r.ofr_n} 只<br>池内 ${r.pool} 只 · 双边覆盖率 ${cover==null?'—':cover+'%'}`;}},grid:{left:80,right:45,top:28,bottom:30},xAxis:{type:'value',name:metric.unit},yAxis:{type:'category',data:rows.map(r=>r.g.label)},series:[{type:'bar',data:rows.map(r=>({value:r.v,itemStyle:{color:$('structureMetric').value==='coverage'?'#79bbff':r.v<0?'#409eff':'#f0a65a'}})),label:{show:true,position:'right',formatter:p=>fmt(p.value)}}]});
 $('structureStatus').textContent=`最新快照 ${d.snapshot_at||'—'} · ${metric.label}（${metric.unit}） · ${rows.length} 组`;
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
function parseShift(v){return v==='0'?0:v[0]==='L'?Number(v.slice(1)):-Number(v.slice(1));}
/* 领先k日＝指标值整体右移k个交易日槽位（t日指标画在t+k日，与未来价格对齐） */
function shiftSeries(points,dates,k){
 if(!k)return points;
 const map=new Map(points.map(p=>[p.date,p.value])),out=[];
 dates.forEach((day,i)=>{const src=dates[i-k];if(src!=null&&map.has(src))out.push({date:day,value:map.get(src)});});
 return out;
}
async function renderMain(){
 if(!state.data)return;
 const token=++state.ovlSeq,d=state.data,metric=$('metric').value,mode=$('mode').value,curve=$('curve').value,tenor=$('tenor').value;
 const base=(metric==='emotion'?d.emotion:d.deals).map(x=>({date:x.date,value:metric==='emotion'?x.value:x[metric]})).filter(p=>!isWeekend(p.date));
 let y=null,s=null,notes=[];const info=state.meta?.credit_curves[curve],jobs=[];
 const getSeries=(kind,url)=>cachedOverlay(url).then(p=>{if(kind==='y')y=p;else s=p;}).catch(e=>notes.push(e.message));
 if(mode!=='none'&&curve&&tenor){if(mode==='both'||mode==='yield')jobs.push(getSeries('y',`/institution-flow/api/overlay/yield?curve=${encodeURIComponent(curve)}&tenor=${encodeURIComponent(tenor)}`));if(mode==='both'||mode==='spread'){if(info?.spread)jobs.push(getSeries('s',`/institution-flow/api/overlay/spread?category=${encodeURIComponent(info.spread)}&tenor=${encodeURIComponent(tenor)}`));else notes.push('该曲线无对应信用利差，请选择信用曲线');}}
 await Promise.all(jobs);if(token!==state.ovlSeq)return;
 // 类目横轴只含交易日（周六周日含调休不展示），柱状图连续无周末空档；
 // 各序列缺日置 null 且连线跨过，互不打洞。
 const native=(p,mult)=>p?p.dates.map((date,i)=>({date,value:typeof p.values[i]==='number'&&Number.isFinite(p.values[i])?p.values[i]*mult:null})).filter(p=>p.date>=d.range.start&&p.date<=d.range.end&&!isWeekend(p.date)):[];
 const rawYield=native(y,1),rawSpread=native(s,100),signal=prepareSeries(base),yieldPoints=prepareSeries(rawYield,true),spreadPoints=prepareSeries(rawSpread);
 const dates=[...new Set([...base,...rawYield,...rawSpread].map(p=>p.date))].sort();
 const k=parseShift($('shift').value),signalShifted=shiftSeries(signal,dates,k),shiftTag=k>0?`→领先${k}日`:k<0?`→滞后${-k}日`:'';
 const transformed=$('transform').value,z=transformed==='z20',diff=transformed==='diff',smoothing=$('smooth').value,tag=(smoothing==='none'?'':` · ${smoothing.toUpperCase()}`)+(z?' · Z20':diff?' · Δ':'');
 const signalUnit=z?'Z-score':metric==='emotion'?'BP':metric.includes('pct')?(diff?'百分点':'%'):'笔',yieldUnit=z?'Z-score':diff?'BP':'%';
 $('processingStatus').textContent=`${smoothing==='none'?'不平滑':smoothing.toUpperCase()} → ${$('transform').selectedOptions[0].textContent} · 仅使用当期及历史数据`;
 $('chartNote').textContent='横轴为各序列交易日并集（周六周日含调休不展示）；某序列缺日时连线跨过、不打洞，显式缺值会中断平滑窗口，窗口不足留空。'+(diff?'收益率变化换算为BP，占比变化为百分点。':'')+'领先N日＝指标整体右移N个交易日（t日指标对齐t+N日价格），用于目视检验指标对价格的领先性；滞后为左移。辅助线画在叠加画布上，随缩放保持数据坐标，按当前图表配置本地记忆。';
 const series=[];
 function add(points,label,axis,color,type){const map=new Map(points.filter(p=>p.value!=null).map(p=>[p.date,p.value]));if(!map.size)notes.push(label.replace(/→.*$/,'')+'在当前处理下无有效值');series.push({name:label+tag,type,yAxisIndex:z?0:axis,data:dates.map(day=>map.get(day)??null),connectNulls:true,showSymbol:false,itemStyle:{color},lineStyle:{width:2}});}
 add(signalShifted,`${$('metric').selectedOptions[0].textContent.replace(/（.*?）/g,'')}（${signalUnit}）${shiftTag}`,0,seriesColors.signal,metric==='emotion'?'line':'bar');
 if(y)add(yieldPoints,`${curve} ${tenor}（${yieldUnit}）`,1,seriesColors.yield,'line');
 if(s)add(spreadPoints,`${info?.spread} ${tenor}（${z?'Z-score':'BP'}）`,2,seriesColors.spread,'line');
 // 平移后的同期相关：目视对齐之外的量化参照
 let hint='';
 if(k){const parts=[];
  if(y){const pairs=TS.matched(signalShifted,yieldPoints),r=TS.pearson(pairs);if(r!=null)parts.push(`收益率 r=${fmt(r,2)}（N=${pairs.length}）`);}
  if(s){const pairs=TS.matched(signalShifted,spreadPoints),r=TS.pearson(pairs);if(r!=null)parts.push(`利差 r=${fmt(r,2)}（N=${pairs.length}）`);}
  if(parts.length)hint=` ｜ 平移后同期相关：${parts.join(' · ')}`;
 }
 $('overlayStatus').textContent=(notes.join('；')||'交易日横轴 · 缺日连线跨过 · 周末不展示')+hint;
 state.mainDates=dates;
 state.axisInUse=z?[0]:[0,...(y?[1]:[]),...(s?[2]:[])];
 state.axisNames={0:`指标 ${signalUnit}`,1:`收益率 ${yieldUnit}`,2:'利差 BP'};
 if(dates.length){const c=chart('mainChart',{tooltip:{trigger:'axis',confine:true,valueFormatter:v=>fmt(v,3)},legend:{top:5,type:'scroll'},grid:{left:65,right:z?35:s?140:75,top:65,bottom:65},dataZoom:[{type:'inside'},{type:'slider',height:18,bottom:12}],xAxis:{type:'category',data:dates},yAxis:[{type:'value',name:signalUnit,scale:true,splitLine:{lineStyle:{color:'#ebeef5'}}},{type:'value',name:`收益率 ${yieldUnit}`,position:'right',scale:true,show:!!y&&!z,splitLine:{show:false}},{type:'value',name:'利差 BP',position:'right',offset:y?72:0,scale:true,show:!!s&&!z,splitLine:{show:false}}],series});
  if(!state.draw.zoomBound){c.on('datazoom',()=>drawAll());state.draw.zoomBound=true;}}
 else{noData('mainChart','所选区间暂无数据');state.mainDates=[];}
 rebuildDrawAxis();switchAuxConfig();drawAll();
}

/* ── 用户辅助线：叠加画布，存数据坐标 ─────────────────── */
const AUX_STYLE={line:'#f57c00',dot:'#e65100',text:'#8a5a00',preview:'#f57c00'};
function rebuildDrawAxis(){
 const labels={0:state.axisNames[0]||'指标轴',1:state.axisNames[1]||'收益率轴',2:state.axisNames[2]||'利差轴'};
 const current=$('drawAxis').value;
 $('drawAxis').innerHTML=state.axisInUse.map(a=>`<option value="${a}">${esc(labels[a]||a)}</option>`).join('');
 if(!state.axisInUse.includes(Number(current)))$('drawAxis').value='0';
}
function switchAuxConfig(){
 const key=[state.data?.scope??'',$('metric').value,$('mode').value,$('curve').value,$('tenor').value,$('smooth').value,$('transform').value].join('|');
 if(key===state.draw.cfgKey)return;
 state.draw.cfgKey=key;state.draw.pending=null;
 state.draw.lines=loadLS('brokerAux:'+key)||[];
}
function persistLines(){saveLS('brokerAux:'+state.draw.cfgKey,state.draw.lines);}
function axisInUse(axis){return state.axisInUse.includes(axis);}
function pointToPixel(date,y,axis){
 const c=charts.mainChart;if(!c||!state.mainDates.length||!axisInUse(axis))return null;
 try{const x=c.convertToPixel({xAxisIndex:0},date),py=c.convertToPixel({yAxisIndex:axis},y);
  return Number.isFinite(x)&&Number.isFinite(py)?[x,py]:null;}catch(e){return null;}
}
function pixelToPoint(px,py,axis){
 const c=charts.mainChart;if(!c||!state.mainDates.length||!axisInUse(axis))return null;
 try{let xi=c.convertFromPixel({xAxisIndex:0},px);
  if(typeof xi!=='number')xi=state.mainDates.indexOf(xi);
  const idx=Math.round(xi);
  if(idx<0||idx>=state.mainDates.length)return null;
  const y=c.convertFromPixel({yAxisIndex:axis},py);
  return Number.isFinite(y)?{date:state.mainDates[idx],y}:null;}catch(e){return null;}
}
function lineGeometry(ln){
 const c=charts.mainChart;
 if(ln.type==='h'){
  if(!axisInUse(ln.axis))return null;
  try{const py=c.convertToPixel({yAxisIndex:ln.axis},ln.y),x0=c.convertToPixel({xAxisIndex:0},state.mainDates[0]),x1=c.convertToPixel({xAxisIndex:0},state.mainDates[state.mainDates.length-1]);
   return Number.isFinite(py)&&Number.isFinite(x0)&&Number.isFinite(x1)?{kind:'h',y:py,x0,x1}:null;}catch(e){return null;}
 }
 const p0=pointToPixel(ln.d0,ln.y0,ln.axis),p1=pointToPixel(ln.d1,ln.y1,ln.axis);
 return p0&&p1?{kind:'t',p0,p1}:null;
}
function setDrawStatus(hidden=0){
 const n=state.draw.lines.length;
 $('drawStatus').textContent=state.draw.mode==='trend'?(state.draw.pending?'已取第一点，再点一点完成趋势线（Esc 取消）':'点击图上两点画趋势线')
  :state.draw.mode==='hline'?'点击图上一点画水平线（对齐所选坐标轴）'
  :state.draw.mode==='erase'?'点击要删除的辅助线'
  :(n?`${n} 条辅助线${hidden?` · ${hidden} 条因坐标轴未显示而隐藏`:''}`:'在图上点击取点：趋势线两点、水平线一点，按当前配置记忆');
}
function drawAll(){
 const cv=$('drawLayer');if(!cv)return;
 const host=cv.parentElement,dpr=window.devicePixelRatio||1,w=host.clientWidth,h=host.clientHeight;
 if(cv.width!==Math.round(w*dpr)||cv.height!==Math.round(h*dpr)){cv.width=Math.round(w*dpr);cv.height=Math.round(h*dpr);}
 const ctx=cv.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,w,h);
 if(!state.draw.visible){setDrawStatus();return;}
 const c=charts.mainChart;if(!c||!state.mainDates.length){setDrawStatus();return;}
 let hidden=0;
 ctx.font='11px "PingFang SC","Microsoft YaHei",sans-serif';
 for(const ln of state.draw.lines){
  const g=lineGeometry(ln);if(!g){hidden++;continue;}
  if(g.kind==='h'){
   ctx.strokeStyle=AUX_STYLE.line;ctx.lineWidth=1;ctx.setLineDash([6,4]);
   ctx.beginPath();ctx.moveTo(g.x0,g.y);ctx.lineTo(g.x1,g.y);ctx.stroke();ctx.setLineDash([]);
   const label=fmt(ln.y);ctx.fillStyle=AUX_STYLE.text;ctx.textAlign='right';ctx.textBaseline='bottom';
   ctx.fillText(label,g.x1-4,g.y-2);
  }else{
   ctx.strokeStyle=AUX_STYLE.line;ctx.lineWidth=1.5;
   ctx.beginPath();ctx.moveTo(g.p0[0],g.p0[1]);ctx.lineTo(g.p1[0],g.p1[1]);ctx.stroke();
   for(const p of [g.p0,g.p1]){ctx.fillStyle=AUX_STYLE.dot;ctx.beginPath();ctx.arc(p[0],p[1],3,0,Math.PI*2);ctx.fill();}
  }
 }
 const pending=state.draw.pending,mouse=state.draw.mouse;
 if(pending&&mouse){
  const axis=pending.axis??Number($('drawAxis').value||0),p0=pointToPixel(pending.d,pending.y,axis);
  ctx.strokeStyle=AUX_STYLE.preview;ctx.lineWidth=1;
  if(p0){ctx.setLineDash([4,4]);ctx.beginPath();ctx.moveTo(p0[0],p0[1]);ctx.lineTo(mouse[0],mouse[1]);ctx.stroke();ctx.setLineDash([]);}
 }else if(state.draw.mode==='hline'&&mouse){
  ctx.strokeStyle=AUX_STYLE.preview;ctx.lineWidth=1;ctx.setLineDash([4,4]);
  ctx.beginPath();ctx.moveTo(0,mouse[1]);ctx.lineTo(w,mouse[1]);ctx.stroke();ctx.setLineDash([]);
  ctx.fillStyle=AUX_STYLE.text;ctx.textAlign='left';ctx.textBaseline='bottom';
  ctx.fillText(fmt(pixelToPoint(mouse[0],mouse[1],Number($('drawAxis').value||0))?.y),6,mouse[1]-3);
 }
 setDrawStatus(hidden);
}
function setMode(mode){
 state.draw.mode=mode;if(mode!=='trend')state.draw.pending=null;
 for(const [id,m] of [['drawTrend','trend'],['drawHline','hline'],['drawErase','erase']])$(id).classList.toggle('on',m===mode);
 const cv=$('drawLayer');cv.className=mode==='erase'?'erasing':mode?'active':'';
 drawAll();
}
function eraseAt(px,py){
 for(let i=state.draw.lines.length-1;i>=0;i--){
  const g=lineGeometry(state.draw.lines[i]);if(!g)continue;
  let hit=false;
  if(g.kind==='h')hit=Math.abs(py-g.y)<=5&&px>=g.x0-5&&px<=g.x1+5;
  else{const [x0,y0]=g.p0,[x1,y1]=g.p1,dx=x1-x0,dy=y1-y0,len2=dx*dx+dy*dy;
   const t=len2?Math.max(0,Math.min(1,((px-x0)*dx+(py-y0)*dy)/len2)):0;
   hit=Math.hypot(px-(x0+t*dx),py-(y0+t*dy))<=6;}
  if(hit){state.draw.lines.splice(i,1);persistLines();drawAll();return true;}
 }
 return false;
}
function onCanvasClick(e){
 const mode=state.draw.mode;if(!mode)return;
 if(mode==='erase'){if(!eraseAt(e.offsetX,e.offsetY))setDrawStatus();return;}
 const axis=Number($('drawAxis').value||0);
 if(!axisInUse(axis)){setDrawStatus();return;}
 const pt=pixelToPoint(e.offsetX,e.offsetY,axis);
 if(!pt){$('drawStatus').textContent='请绘图区内取点';return;}
 if(mode==='hline'){state.draw.lines.push({type:'h',axis,y:pt.y});persistLines();drawAll();return;}
 if(!state.draw.pending){state.draw.pending={axis,d:pt.date,y:pt.y};drawAll();return;}
 state.draw.lines.push({type:'t',axis,d0:state.draw.pending.d,y0:state.draw.pending.y,d1:pt.date,y1:pt.y});
 state.draw.pending=null;persistLines();drawAll();
}

async function load(initial=false){
 const params=new URLSearchParams({scope:$('scope').value});if(!initial){if(!$('start').value||!$('end').value){$('error').hidden=false;$('error').textContent='请选择完整的起止日期';return;}params.set('start',$('start').value);params.set('end',$('end').value);}
 const token=++state.seq;state.ovlSeq++;$('error').hidden=true;$('refresh').disabled=true;document.querySelector('main').setAttribute('aria-busy','true');
 try{const data=await api('/api/broker-data?'+params);if(token!==state.seq)return;state.data=data;render();}catch(e){if(token!==state.seq)return;$('error').hidden=false;$('error').textContent=`读取失败：${e.message}。${state.data?'下方保留上次成功结果，尚未应用本次筛选。':''}`;}finally{if(token===state.seq){$('refresh').disabled=false;document.querySelector('main').removeAttribute('aria-busy');}}
}
$('refresh').onclick=()=>{overlayCache.clear();load();};$('apply').onclick=()=>{document.querySelectorAll('#ranges button').forEach(b=>b.classList.remove('on'));load();};$('scope').onchange=()=>load();
document.querySelectorAll('#ranges button').forEach(b=>b.onclick=()=>{if(!state.data)return;const end=state.data.snapshot_at?.slice(0,10)||state.data.range.end,dt=new Date(end+'T00:00:00Z');dt.setUTCDate(dt.getUTCDate()-Number(b.dataset.days));$('end').value=end;$('start').value=dt.toISOString().slice(0,10);document.querySelectorAll('#ranges button').forEach(x=>x.classList.toggle('on',x===b));load();});
for(const id of ['metric','mode','tenor','smooth','transform','shift'])$(id).onchange=renderMain;$('curve').onchange=()=>{setTenors();renderMain();};
for(const id of ['dimension','structureMetric'])$(id).onchange=renderStructure;
$('structureView').onchange=renderStructure;
for(const [id,key] of [['colorSignal','signal'],['colorYield','yield'],['colorSpread','spread']])$(id).onchange=e=>{seriesColors[key]=e.target.value;saveLS('brokerMainColors',seriesColors);renderMain();};
$('resetColors').onclick=()=>{for(const key in DEFAULT_COLORS)seriesColors[key]=DEFAULT_COLORS[key];saveLS('brokerMainColors',null);syncColorInputs();renderMain();};
$('drawTrend').onclick=()=>setMode(state.draw.mode==='trend'?'':'trend');
$('drawHline').onclick=()=>setMode(state.draw.mode==='hline'?'':'hline');
$('drawErase').onclick=()=>setMode(state.draw.mode==='erase'?'':'erase');
$('undoLine').onclick=()=>{state.draw.lines.pop();state.draw.pending=null;persistLines();drawAll();};
$('clearLines').onclick=()=>{if(!state.draw.lines.length)return;state.draw.lines=[];state.draw.pending=null;persistLines();drawAll();};
$('showLines').onchange=e=>{state.draw.visible=e.target.checked;drawAll();};
$('drawAxis').onchange=()=>{state.draw.pending=null;drawAll();};
const drawCanvas=$('drawLayer');
drawCanvas.addEventListener('click',onCanvasClick);
drawCanvas.addEventListener('mousemove',e=>{state.draw.mouse=[e.offsetX,e.offsetY];if(state.draw.mode==='trend'&&state.draw.pending)drawAll();else if(state.draw.mode==='hline')drawAll();});
drawCanvas.addEventListener('mouseleave',()=>{state.draw.mouse=null;if(state.draw.mode)drawAll();});
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&state.draw.mode)setMode('');});
window.addEventListener('resize',()=>{Object.values(charts).forEach(c=>c.resize());drawAll();});
syncColorInputs();
Promise.all([initMeta(),load(true)]).then(renderMain);
})();
