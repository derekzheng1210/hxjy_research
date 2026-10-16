const assert=require('assert');
const fs=require('fs');
const vm=require('vm');
const source=fs.readFileSync('templates/secondary_bond_picker.html','utf8');
const script=source.slice(source.lastIndexOf('<script>')+8,source.lastIndexOf('</script>'))
  .replace(/let RAW=.*?const INITIAL_VERSION=.*?;/s,'let RAW=[];let META={};const INITIAL_VERSION="test";');
const elements=new Map();
function element(id){
  if(!elements.has(id))elements.set(id,{value:'',checked:false,textContent:'',innerHTML:'',hidden:false,disabled:false,style:{},
    classList:{toggle(){},add(){},remove(){}},querySelectorAll:()=>[],querySelector:()=>({innerHTML:''})});
  return elements.get(id);
}
const calls=[];
const response={username:'maxiao',display_name:'M',perspective:'研究盘视角',holding_date:'2026-10-14',refreshed_at:'2026-10-16T14:00:00',quantity_unit:'',
  holdings:[{code:'123.IB',name:'A',qty:300000},{code:'456.SH',name:'行情池外',qty:100000}]};
let failStatus=0;
const context=vm.createContext({console,Map,Set,Date,Number,String,Array,JSON,Math,Blob,URL,encodeURIComponent,
  document:{getElementById:element,querySelector:()=>element('workspace'),querySelectorAll:()=>[]},
  sessionStorage:{getItem:()=>null,setItem(){}},window:{setInterval(){},addEventListener(){}},
  fetch:async(url,options={})=>{calls.push([url,options]);return {ok:!failStatus,status:failStatus||200,redirected:false,json:async()=>failStatus?{error:'expired'}:response}},
});
vm.runInContext(fs.readFileSync('static/secondary_holdings.js','utf8'),context);
vm.runInContext(script,context);
const run=code=>vm.runInContext(code,context);
const tick=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  run("setBondView('all');setBondView('recommended');setBondView('movers')");
  assert.equal(calls.length,0,'other tabs must never connect to holdings');
  run("setBondView('holdings')");await tick();
  assert.equal(calls.length,1);
  assert.equal(run('personalHoldings.status'),'ready');
  assert.equal(run('state.filtered.length'),2,'include holdings missing from market pool');
  assert(element('tableWrap').innerHTML.includes('持仓量（万）'));
  assert(element('tableWrap').innerHTML.includes('3,000'));
  assert.equal((element('tableWrap').innerHTML.match(/<th(?:\s|>)/g)||[]).length,20);
  assert(element('tableWrap').innerHTML.includes('width:80px'));
  assert.equal(element('holdingsGate').hidden,true);
  run("sortBy('personalQty')");assert.equal(run('state.filtered[0].code'),'456.SH');
  run("setBondView('all');setBondView('holdings')");await tick();
  assert.equal(calls.length,1,'repeat visits must reuse session/data');
  assert.equal(run('personalQuantityText({code:"123.IB"})'),'3,000');
  assert(run('personalQuantityTitle()').includes('除以100'));
  element('search').value='no-match';run('onFilter()');assert.equal(run('state.filtered.length'),0);
  run('showAllPersonalHoldings()');assert.equal(run('state.filtered.length'),2);
  // Refresh failure keeps last data visible, with an explicit stale-data message.
  failStatus=502;await run('loadPersonalHoldings(true)');
  assert.equal(run('personalHoldings.status'),'ready');
  assert(element('holdingsRefreshError').textContent.includes('上次读取'));
  // Authentication expiry clears all personal data, then offers login only here.
  failStatus=401;await run('loadPersonalHoldings(true)');
  assert.equal(run('personalHoldings.index.size'),0);
  assert.equal(element('holdingsLogin').hidden,false);
  assert.equal(element('tableWrap').hidden,true);
  assert.equal(element('holdingsLogout').hidden,true);
  failStatus=0;element('holdingsUsername').value='maxiao';element('holdingsPassword').value='test-only';
  await run('loginPersonalHoldings({preventDefault(){}})');
  assert.equal(element('holdingsPassword').value,'');
  assert.equal(calls.at(-1)[1].method,'POST');
  assert.equal(run('personalHoldings.status'),'ready');
  await run('logoutPersonalHoldings()');
  assert.equal(calls.at(-1)[1].method,'DELETE');
  assert.equal(run('personalHoldings.index.size'),0);
  assert.equal(run('personalHoldings.data'),null);
  assert.equal(run('state.sort.key'),null);
  assert(source.indexOf('id="viewHoldings"')<source.indexOf('id="viewMovers"'));
  console.log('personal holdings frontend: ok');
})().catch(error=>{console.error(error);process.exitCode=1});
