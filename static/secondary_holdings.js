// Personal data stays in memory. Only the server's HttpOnly session cookie persists.
const personalHoldings={status:'idle',data:null,index:new Map(),busy:false,error:'',lastCheck:0,generation:0};
const HOLDINGS_DIMS=[{id:'msFund',key:'fund',label:'基金名称'},{id:'msUnit',key:'unit',label:'资产单元名称'},{id:'msPortfolio',key:'portfolio',label:'组合'}];

function personalBondList(){
  const index=personalHoldings.index,known=new Set(BONDS.map(b=>b.code));
  const rows=BONDS.filter(b=>index.has(b.code));
  for(const holding of index.values())if(!known.has(holding.code)){
    const raw=[];raw[F.CODE]=holding.code;raw[F.NAME]=holding.name;
    rows.push(toBond(raw));
  }
  return rows;
}
function holdingsMSValues(id){const wrap=document.getElementById(id);return wrap?[...wrap.querySelectorAll('input:checked')].map(input=>input.value):[]}
function holdingsDimFilter(){const sel={fund:holdingsMSValues('msFund'),unit:holdingsMSValues('msUnit'),portfolio:holdingsMSValues('msPortfolio')};return sel.fund.length||sel.unit.length||sel.portfolio.length?sel:null}
function detailMatches(d,f){return(!f.fund.length||f.fund.includes(d.fund))&&(!f.unit.length||f.unit.includes(d.unit))&&(!f.portfolio.length||f.portfolio.includes(d.portfolio))}
function matchingDetails(h){const f=holdingsDimFilter();return!f||!Array.isArray(h.details)?null:h.details.filter(d=>detailMatches(d,f))}
function personalBondEligible(b){const h=personalHoldings.index.get(b.code);if(!h)return false;const details=matchingDetails(h);return details===null||details.length>0}
function personalQuantity(b){const h=personalHoldings.index.get(b.code);if(!h)return null;const details=matchingDetails(h);if(details===null)return h.qty===null||h.qty===undefined?null:h.qty/100;return details.reduce((sum,d)=>sum+(d.qty||0),0)/100}
function personalQuantityText(b){const qty=personalQuantity(b);return qty===null?'-':qty.toLocaleString('zh-CN',{maximumFractionDigits:8})}
function personalQuantityTitle(){return '持仓量（万）：固收内部平台数量加总后除以100，按所选基金/资产单元/组合重算'}
function personalColumnLabel(){return '持仓量（万）'}
function personalDimensionValues(b){
  const h=personalHoldings.index.get(b.code);if(!h||!Array.isArray(h.details))return null;
  const details=matchingDetails(h)||h.details;if(!details.length)return null;
  const uniq=key=>[...new Set(details.map(d=>d[key]).filter(Boolean))];
  return{fund:uniq('fund'),unit:uniq('unit'),portfolio:uniq('portfolio')}
}
function personalDimensionText(b,key){const values=personalDimensionValues(b);return values&&values[key].length?values[key].join('、'):''}
function hasHoldingsDetails(){return(personalHoldings.data?.holdings||[]).some(b=>Array.isArray(b.details)&&b.details.some(d=>d.fund||d.unit||d.portfolio))}
function initHoldingsDimFilters(){
  const values=new Map(HOLDINGS_DIMS.map(dim=>[dim.key,new Set()]));
  for(const holding of personalHoldings.index.values())for(const d of holding.details||[])for(const dim of HOLDINGS_DIMS){if(d[dim.key])values.get(dim.key).add(d[dim.key])}
  for(const dim of HOLDINGS_DIMS){
    const drop=document.getElementById('drop-'+dim.id);if(!drop)continue;
    drop.innerHTML=`<div class="ms-opt ms-all" title="清除本项筛选，恢复全部" onclick="holdingsSelectAllMS('${dim.id}')"><b>全部</b></div>`
      +[...values.get(dim.key)].sort((a,b)=>a.localeCompare(b,'zh')).map(v=>`<label class="ms-opt"><input type="checkbox" value="${esc(v)}" onchange="onMSChange('${dim.id}')">${esc(v)}</label>`).join('');
    clearMS(dim.id);
  }
}
function holdingsSelectAllMS(id){clearMS(id);onFilter()}

function renderHoldingsPanel(){
  const active=state.view==='holdings',p=personalHoldings,d=p.data;
  document.querySelector('.workspace').classList.toggle('holdings-workspace',active);
  document.getElementById('holdingsBar').hidden=!active;
  document.getElementById('holdingsGate').hidden=!active||p.status==='ready';
  document.getElementById('tableWrap').hidden=active&&p.status!=='ready';
  document.getElementById('pagination').hidden=active&&p.status!=='ready';
  document.getElementById('holdingCount').textContent=d?p.index.size.toLocaleString():'—';
  document.getElementById('holdingsLogin').hidden=p.status!=='login';
  document.getElementById('holdingsMessage').textContent=p.error||(p.busy?'正在读取个人持仓…':'点击持仓债券后才连接固收内部数据平台');
  document.getElementById('holdingsRetry').hidden=p.status!=='error';
  document.getElementById('holdingsSubmit').disabled=p.busy;
  document.getElementById('holdingsSubmit').textContent=p.busy?'正在登录…':'登录并读取持仓';
  document.getElementById('holdingsRefresh').disabled=p.busy||!d;
  document.getElementById('holdingsShowAll').hidden=!d||!personalFiltersActive();
  document.getElementById('holdingsLogout').hidden=!d;
  document.getElementById('holdingsDims').hidden=!d||!hasHoldingsDetails();
  document.getElementById('holdingsIdentity').textContent=d?`${d.display_name} (${d.username}) · ${d.perspective} · 持仓日期 ${d.holding_date||'暂无'} · ${p.index.size} 只`:'固收内部数据平台 · 个人持仓';
  document.getElementById('holdingsRefreshError').textContent=d&&p.error?`${p.error} 当前显示上次读取的持仓。`:'';
  document.getElementById('holdingsRefreshError').hidden=!d||!p.error;
}
function acceptPersonalHoldings(data){
  personalHoldings.data=data;
  personalHoldings.index=new Map((data.holdings||[]).map(b=>[b.code,b]));
  personalHoldings.status='ready';personalHoldings.error='';personalHoldings.lastCheck=Date.now();
  initHoldingsDimFilters();
  document.getElementById('holdingsPassword').value='';
}
async function holdingsRequest(options={},refresh=false){
  const response=await fetch('/api/secondary-bond-picker/holdings'+(refresh?'?refresh=1':''),{cache:'no-store',...options});
  if(response.redirected)throw Object.assign(new Error('门户登录已失效，请刷新页面后重新登录。'),{status:302});
  const data=await response.json();
  if(!response.ok)throw Object.assign(new Error(data.error||'读取持仓失败，请重试。'),{status:response.status});
  return data;
}
async function loadPersonalHoldings(refresh=false){
  const p=personalHoldings;
  if(p.busy)return;
  if(!refresh&&p.status==='ready'&&Date.now()-p.lastCheck<300000){renderHoldingsPanel();return}
  p.busy=true;p.error='';if(!p.data)p.status='loading';renderHoldingsPanel();
  const generation=p.generation;
  try{const data=await holdingsRequest({},refresh);if(generation===p.generation)acceptPersonalHoldings(data)}
  catch(error){if(generation!==p.generation)return;p.error=error.message;if([401,403].includes(error.status)){p.data=null;p.index.clear();p.status='login'}else p.status=p.data?'ready':'error'}
  finally{if(generation===p.generation){p.busy=false;renderHoldingsPanel();if(state.view==='holdings')onFilter(true)}}
}
async function loginPersonalHoldings(event){
  event.preventDefault();const p=personalHoldings;if(p.busy)return;
  const username=document.getElementById('holdingsUsername').value.trim(),password=document.getElementById('holdingsPassword').value;
  if(!username||!password)return;
  p.busy=true;p.error='';renderHoldingsPanel();
  try{acceptPersonalHoldings(await holdingsRequest({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username,password})}))}
  catch(error){p.error=error.message;p.status='login'}
  finally{document.getElementById('holdingsPassword').value='';p.busy=false;renderHoldingsPanel();if(state.view==='holdings')onFilter()}
}
async function logoutPersonalHoldings(){
  const p=personalHoldings;if(p.busy)return;p.busy=true;renderHoldingsPanel();
  try{
    await holdingsRequest({method:'DELETE',headers:{'Content-Type':'application/json'},body:'{}'});
    p.generation++;p.data=null;p.index.clear();p.status='login';p.error='';
    document.getElementById('holdingsPassword').value='';
    if(state.sort.key==='personalQty')state.sort={key:null,dir:'asc'};
  }catch(error){p.error=error.message}
  finally{p.busy=false;renderHoldingsPanel();onFilter()}
}
function clearPickerFilters(){
  ['search','minTerm','maxTerm','minYield','maxYield','minOfferVolume','maxValOfr'].forEach(id=>document.getElementById(id).value='');
  ['ct','sub','market'].forEach(id=>document.getElementById(id).value='');
  ['msRating','msIR','msEntity'].forEach(clearMS);
  HOLDINGS_DIMS.forEach(dim=>clearMS(dim.id));
  ['hasOffer','twoSided','favoritesOnly','offerAboveVal','onlyCredit','only630Ok'].forEach(id=>document.getElementById(id).checked=false);
}
function personalFiltersActive(){
  return !!document.getElementById('search').value.trim()
    ||['minTerm','maxTerm','minYield','maxYield','minOfferVolume','maxValOfr'].some(id=>document.getElementById(id).value.trim()!=='')
    ||['ct','sub','market'].some(id=>document.getElementById(id).value!=='')
    ||['msRating','msIR','msEntity'].some(id=>getMSValues(id).length>0)
    ||HOLDINGS_DIMS.some(dim=>holdingsMSValues(dim.id).length>0)
    ||['hasOffer','twoSided','favoritesOnly','offerAboveVal','onlyCredit','only630Ok'].some(id=>document.getElementById(id).checked);
}
function showAllPersonalHoldings(){clearPickerFilters();onFilter()}
function togglePickerFilters(){const collapsed=document.querySelector('.workspace').classList.toggle('filters-collapsed');document.getElementById('toggleFilters').textContent=collapsed?'展开筛选':'收起筛选';drawEmotionChart()}
function fitPersonalColumns(){if(window.innerWidth<1380){document.querySelector('.workspace').classList.add('filters-collapsed');document.getElementById('toggleFilters').textContent='展开筛选'}}
