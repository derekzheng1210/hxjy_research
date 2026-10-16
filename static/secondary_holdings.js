// Personal data stays in memory. Only the server's HttpOnly session cookie persists.
const personalHoldings={status:'idle',data:null,index:new Map(),busy:false,error:'',lastCheck:0,generation:0};

function personalBondList(){
  const index=personalHoldings.index,known=new Set(BONDS.map(b=>b.code));
  const rows=BONDS.filter(b=>index.has(b.code));
  for(const holding of index.values())if(!known.has(holding.code)){
    const raw=[];raw[F.CODE]=holding.code;raw[F.NAME]=holding.name;
    rows.push(toBond(raw));
  }
  return rows;
}
function personalQuantity(b){const qty=personalHoldings.index.get(b.code)?.qty;return qty===null||qty===undefined?null:qty/100}
function personalQuantityText(b){const qty=personalQuantity(b);return qty===null?'-':qty.toLocaleString('zh-CN',{maximumFractionDigits:8})}
function personalQuantityTitle(){return '持仓量（万）：固收内部平台数量加总后除以100'}
function personalColumnLabel(){return '持仓量（万）'}

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
  document.getElementById('holdingsLogout').hidden=!d;
  document.getElementById('holdingsIdentity').textContent=d?`${d.display_name} (${d.username}) · ${d.perspective} · 持仓日期 ${d.holding_date||'暂无'} · ${p.index.size} 只`:'固收内部数据平台 · 个人持仓';
  document.getElementById('holdingsRefreshError').textContent=d&&p.error?`${p.error} 当前显示上次读取的持仓。`:'';
  document.getElementById('holdingsRefreshError').hidden=!d||!p.error;
}
function acceptPersonalHoldings(data){
  personalHoldings.data=data;
  personalHoldings.index=new Map((data.holdings||[]).map(b=>[b.code,b]));
  personalHoldings.status='ready';personalHoldings.error='';personalHoldings.lastCheck=Date.now();
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
function showAllPersonalHoldings(){
  ['search','minTerm','maxTerm','minYield','maxYield','minOfferVolume','maxValOfr'].forEach(id=>document.getElementById(id).value='');
  ['ct','sub','market'].forEach(id=>document.getElementById(id).value='');
  ['msRating','msIR','msEntity'].forEach(clearMS);
  ['hasOffer','twoSided','favoritesOnly','offerAboveVal','onlyCredit','only630Ok'].forEach(id=>document.getElementById(id).checked=false);
  onFilter();
}
function togglePickerFilters(){const collapsed=document.querySelector('.workspace').classList.toggle('filters-collapsed');document.getElementById('toggleFilters').textContent=collapsed?'展开筛选':'收起筛选';drawEmotionChart()}
function fitPersonalColumns(){if(window.innerWidth<1380){document.querySelector('.workspace').classList.add('filters-collapsed');document.getElementById('toggleFilters').textContent='展开筛选'}}
