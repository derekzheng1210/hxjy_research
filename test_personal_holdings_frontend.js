const assert=require('assert');
const fs=require('fs');
const vm=require('vm');
const source=fs.readFileSync('templates/secondary_bond_picker.html','utf8');
const script=source.slice(source.lastIndexOf('<script>')+8,source.lastIndexOf('</script>'))
  .replace(/let RAW=.*?const INITIAL_VERSION=.*?;/s,'let RAW=[];let META={};const INITIAL_VERSION="test";');
const elements=new Map();
function element(id){
  if(!elements.has(id))elements.set(id,{value:'',checked:false,textContent:'',innerHTML:'',hidden:false,disabled:false,style:{},inputs:[],
    classList:{toggle(){},add(){},remove(){}},
    querySelectorAll:selector=>{
      if(!selector.includes('input'))return [];
      const inputs=element(id).inputs;
      return selector.includes(':checked')?inputs.filter(input=>input.checked):inputs;
    },querySelector:()=>({innerHTML:''})});
  return elements.get(id);
}
const calls=[];
const response={username:'maxiao',display_name:'M',perspective:'研究盘视角',holding_date:'2026-10-14',refreshed_at:'2026-10-16T14:00:00',quantity_unit:'',
  holdings:[{code:'123.IB',name:'A',qty:300000},{code:'456.SH',name:'行情池外',qty:100000}]};
let failStatus=0,lastBlob=null;
const context=vm.createContext({console,Map,Set,Date,Number,String,Array,JSON,Math,Blob,encodeURIComponent,
  document:{getElementById:element,querySelector:()=>element('workspace'),querySelectorAll:()=>[],createElement:()=>({click(){},style:{}})},
  sessionStorage:{getItem:()=>null,setItem(){}},window:{setInterval(){},addEventListener(){}},
  URL:{createObjectURL(blob){lastBlob=blob;return 'blob:test'},revokeObjectURL(){}},
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
  assert.equal((element('tableWrap').innerHTML.match(/<th(?:\s|>)/g)||[]).length,21);
  assert(element('tableWrap').innerHTML.includes('width:160px'),'short-name column widened');
  assert(element('tableWrap').innerHTML.includes('width:80px'));
  assert.equal(element('holdingsGate').hidden,true);
  run("sortBy('personalQty')");assert.equal(run('state.filtered[0].code'),'456.SH');
  run("setBondView('all');setBondView('holdings')");await tick();
  assert.equal(calls.length,1,'repeat visits must reuse session/data');
  assert.equal(run('personalQuantityText({code:"123.IB"})'),'3,000');
  assert(run('personalQuantityTitle()').includes('除以100'));
  element('search').value='no-match';run('onFilter()');assert.equal(run('state.filtered.length'),0);
  run('showAllPersonalHoldings()');assert.equal(run('state.filtered.length'),2);
  // Holdings tab defaults to all holdings: entering clears filters, button only appears with active filters.
  assert.equal(element('holdingsShowAll').hidden,true,'no filter conditions hides the show-all button');
  element('search').value='123';run('onFilter()');
  assert.equal(run('state.filtered.length'),1);
  assert.equal(element('holdingsShowAll').hidden,false,'active filter shows the show-all button');
  run("setBondView('all')");element('search').value='zzz';element('hasOffer').checked=true;
  run("setBondView('holdings')");
  assert.equal(element('search').value,'','entering holdings clears filter inputs');
  assert.equal(element('hasOffer').checked,false);
  assert.equal(run('state.filtered.length'),2,'holdings view defaults to all holdings');
  assert.equal(element('holdingsShowAll').hidden,true,'show-all button hidden after entry clear');
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
  // Fund / asset-unit / portfolio filters: options come from holdings details, default selects all.
  response.holdings=[
    {code:'123.IB',name:'A',qty:300000,details:[{fund:'基金甲',unit:'单元一',portfolio:'组合1',qty:200000},{fund:'基金乙',unit:'单元二',portfolio:'组合2',qty:100000}]},
    {code:'456.SH',name:'行情池外',qty:100000,details:[{fund:'基金甲',unit:'单元一',portfolio:'组合1',qty:100000}]}];
  await run('loadPersonalHoldings()');
  assert.equal(run('personalHoldings.status'),'ready');
  assert.equal(element('holdingsDims').hidden,false,'dimension filters appear once details exist');
  assert(element('drop-msFund').innerHTML.includes('基金甲'),'options built from holdings details');
  assert(element('drop-msFund').innerHTML.includes('全部'),'select-all shortcut offered per filter');
  assert.equal(run('state.filtered.length'),2);
  assert.equal((element('tableWrap').innerHTML.match(/<th(?:\s|>)/g)||[]).length,24,'dimension and deal columns appended at the right');
  assert(element('tableWrap').innerHTML.includes('资产单元名称'));
  assert(element('tableWrap').innerHTML.includes('dim-cell'),'dimension cells rendered per row');
  assert(element('tableWrap').innerHTML.includes('holdings-wide'),'wider table enables the horizontal scrollbar');
  assert.equal(run('personalQuantityText({code:"123.IB"})'),'3,000','no selection keeps the full total');
  element('msFund').inputs.push({value:'基金甲',checked:true});run('onFilter()');
  assert.deepEqual(run('state.filtered.map(b=>b.code)'),['123.IB','456.SH']);
  assert.equal(run('personalQuantityText({code:"123.IB"})'),'2,000','quantity recomputed over selected fund only');
  assert.equal(run('personalQuantityText({code:"456.SH"})'),'1,000');
  assert.equal(element('holdingsShowAll').hidden,false,'dimension selection counts as an active filter');
  element('msFund').inputs[0].checked=false;element('msFund').inputs.push({value:'基金乙',checked:true});run('onFilter()');
  assert.deepEqual(run('state.filtered.map(b=>b.code)'),['123.IB'],'bonds without any selected fund drop out');
  assert.equal(run('personalQuantityText({code:"123.IB"})'),'1,000','only the selected fund quantity counts');
  run('holdingsSelectAllMS("msFund")');
  assert.equal(run('state.filtered.length'),2,'select-all restores every bond');
  assert.equal(element('holdingsShowAll').hidden,true);
  // Deal column: latest trade from META.deals with a tooltip, sortable; missing deals render '-'.
  run('META.deals={"123.IB":{time:"10:15:23",yield:1.9075,net:100.2029,num:52,tkn:33,gvn:19,trd:0,high:1.915,low:1.9075}};META.deals_generated_at="2026-10-22 11:00:00"');
  run('onFilter()');
  assert(element('tableWrap').innerHTML.includes('dealYield'),'deal column header is sortable');
  assert(element('tableWrap').innerHTML.includes('1.9075'),'latest deal yield rendered');
  assert(element('tableWrap').innerHTML.includes('TKN 33 / GVN 19 / TRD 0'),'tooltip carries the day direction mix');
  assert(element('tableWrap').innerHTML.includes('最新成交 10:15:23'),'tooltip carries the deal time');
  run("sortBy('dealYield')");assert.equal(run('state.filtered[0].code'),'123.IB','deal yield sorting puts traded bonds first');
  run('exportCSV()');
  const csvText=await lastBlob.text();
  assert(csvText.includes('"基金名称","资产单元名称","组合"'),'CSV carries the dimension headers');
  assert(csvText.includes('基金甲、基金乙'),'CSV joins multiple dimension values');
  assert(csvText.includes('"成交收益率(%)","成交净价","成交时间","当日成交笔数(TKN/GVN/TRD)"'),'CSV carries deal columns');
  assert(csvText.includes('10:15:23'),'CSV carries the deal time');
  await run('logoutPersonalHoldings()');
  assert.equal(element('holdingsDims').hidden,true,'dimension filters hidden without data');
  assert.equal((source.match(/'简称','name','sticky-d','width:160px'/g)||[]).length,2,'short-name column widened in main and mover tables');
  assert(source.includes('max-width:140px')&&!source.includes('max-width:96px'),'bond-name ellipsis limit widened');
  assert(source.indexOf('id="viewHoldings"')<source.indexOf('id="viewMovers"'));
  console.log('personal holdings frontend: ok');
})().catch(error=>{console.error(error);process.exitCode=1});
