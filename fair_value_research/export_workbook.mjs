// Run with the bundled Node runtime. Requires a node_modules junction beside this file.
import fs from 'node:fs/promises';
import path from 'node:path';
import {Workbook, SpreadsheetFile} from '@oai/artifact-tool';

const root=path.resolve(process.argv[2] || 'outputs/fair_value_v1');
const rows=JSON.parse(await fs.readFile(path.join(root,'latest_all_bonds.json'),'utf8'));
const metrics=JSON.parse(await fs.readFile(path.join(root,'metrics.json'),'utf8'));
const overview=JSON.parse(await fs.readFile(path.join(root,'run_summary.json'),'utf8'));
const wb=Workbook.create();
const summary=wb.worksheets.add('研究结果');
const detail=wb.worksheets.add('全量公允估值');
const names={quote:'报价模型',trade:'成交模型',joint:'联合模型',official:'官方利差不变',latest_mid:'最新双边中心',quote_center:'衰减报价中心',latest_trade:'最近成交'};
function header(sheet,range){sheet.getRange(range).format={fill:'#183D54',font:{name:'Microsoft YaHei',size:10,bold:true,color:'#FFFFFF'},rowHeight:32,wrapText:true,verticalAlignment:'center'};}
for (const s of [summary,detail]) {s.showGridLines=false;s.tabColor='#183D54';}
summary.getRange('A2').values=[['公允估值与多期限兑现研究']];
summary.getRange('A2').format.font={name:'Microsoft YaHei',size:16,bold:true,color:'#183D54'};
summary.getRange('A4:F6').values=[['数据截止','2026-09-16','观察窗口',5,'交易日',null],['全池债券',overview.universe,'可测算债券',overview.fair_values,'有成交覆盖',overview.trade_covered],['模型状态','预设参数探索版',null,null,null,null]];
summary.getRange('A8').values=[['重点观察5日兑现；仅4个完整起点，不构成独立样本外验证。10日尚未成熟。']];
summary.getRange('A10:H10').values=[['模型','兑现期限(日)','起点数','样本数','MAE(BP)','同样本不变MAE','方向准确率','日均Rank IC']];
const mr=metrics.map(x=>[names[x.model]||x.model,x.horizon,x.origins,x.observations,x.mae_bp,x.baseline_mae_bp,x.direction_accuracy,x.rank_ic]);
if(mr.length) summary.getRangeByIndexes(10,0,mr.length,8).values=mr;
header(summary,'A10:H10');
summary.getRange(`E11:F${10+mr.length}`).setNumberFormat('0.00');
summary.getRange(`G11:G${10+mr.length}`).setNumberFormat('0.0%');
summary.getRange(`H11:H${10+mr.length}`).setNumberFormat('0.000');
summary.getRange('A1:H40').format.columnWidth=18;
summary.getRange('A1:A40').format.columnWidth=24;
summary.getRange('A1:H40').format.font.name='Microsoft YaHei';
const noteRow=13+mr.length;
summary.getRange(`A${noteRow}`).values=[['口径与来源']];
summary.getRange(`A${noteRow+1}`).values=[['公允利差减对应评级曲线；公允估值为收益率%，不是净价。']];
summary.getRange(`A${noteRow+2}`).values=[['输入：DM最优报价快照、DM经纪商日度成交、Oracle中债估值及评级曲线。']];
summary.getRange(`A${noteRow+3}`).values=[['当日成交日度统计滞后使用；质量分数和敏感性范围不是概率或置信区间。']];
summary.getRange(`A${noteRow+4}`).values=[['固定2026-08-27门户债券池；历史评级缺少原始发布时间和修订版本。']];
detail.getRange('A2').values=[['全量公允估值']];
detail.getRange('A2').format.font={name:'Microsoft YaHei',size:16,bold:true,color:'#183D54'};
detail.getRange('A3').values=[['截至2026-09-16 16:05；空值说明见最右列。模型结果为本次离线快照。']];
const headers=['债券代码','债券简称','发行人','官方收益率(%)','公允估值(%)','公允利差(BP)','偏离官方(BP)','报价天数','成交天数','证据质量','主体可比券数','参考估值日','模型曲线','成交数据状态','空值原因'];
detail.getRange('A5:O5').values=[headers];header(detail,'A5:O5');
const values=rows.map(r=>[r.code,r.name,r.issuer,r.official_yield,r.fair_yield,r.fair_spread_bp,r.delta_bp,r.quote_days,r.trade_days,r.quality,r.peer_count,r.valuation_date?new Date(`${r.valuation_date.slice(0,4)}-${r.valuation_date.slice(4,6)}-${r.valuation_date.slice(6,8)}T00:00:00Z`):null,r.curve_name,r.trade_status,r.reason].map(x=>x??null));
for(let start=0;start<values.length;start+=2000)detail.getRangeByIndexes(start+5,0,Math.min(2000,values.length-start),15).values=values.slice(start,start+2000);
const end=values.length+5;
detail.getRange(`A6:O${end}`).format.font={name:'Microsoft YaHei',size:10,color:'#172B43'};
detail.getRange(`D6:E${end}`).setNumberFormat('0.0000');
detail.getRange(`F6:G${end}`).setNumberFormat('0.00');
detail.getRange(`J6:J${end}`).setNumberFormat('0.00');
detail.getRange(`L6:L${end}`).setNumberFormat('yyyy-mm-dd');
detail.getRange(`A5:A${end}`).format.columnWidth=19;
detail.getRange(`B5:B${end}`).format.columnWidth=28;
detail.getRange(`C5:C${end}`).format.columnWidth=38;
detail.getRange(`D5:N${end}`).format.columnWidth=18;
detail.getRange(`O5:O${end}`).format.columnWidth=48;
detail.freezePanes.freezeRows(5);
detail.tables.add(`A5:O${end}`,true,'FairValueResults');
wb.recalculate();
console.log((await wb.inspect({kind:'table',range:'研究结果!A10:H17',include:'values',tableMaxRows:8,tableMaxCols:8,maxChars:2500})).ndjson);
console.log((await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NUM!',options:{useRegex:true,maxResults:10},maxChars:500})).ndjson);
for (const [sheetName,range,file] of [['研究结果','A1:H23','summary_preview.png'],['全量公允估值','A1:G14','detail_preview.png']]) {
 const blob=await wb.render({sheetName,range,scale:1.5,format:'png'});
 await fs.writeFile(path.join(root,file),new Uint8Array(await blob.arrayBuffer()));
}
await (await SpreadsheetFile.exportXlsx(wb)).save(path.join(root,'公允估值研究.xlsx'));
console.log('exported',values.length,'bonds');
