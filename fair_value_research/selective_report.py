"""Reconcile and publish the two explicitly exploratory V2 research stages."""
import html
import json
from pathlib import Path
import numpy as np
import pandas as pd
from .selective_followup import apply


def main():
    root=Path('outputs/fair_value_v2'); out=root/'persistent_followup'
    info=json.loads((out/'summary.json').read_text(encoding='utf8'))
    latest=pd.read_csv(out/'latest_all_bonds.csv',dtype={'code':str,'date':str})
    back=pd.read_csv(out/'selected_backtest.csv',dtype={'code':str,'date':str})
    metrics=pd.read_csv(out/'chosen_metrics.csv')
    original=pd.read_csv('outputs/fair_value_v1/backtest_detail.csv',dtype={'code':str,'date':str})
    pairs=[]
    for model in ['latest_mid','quote_center','latest_trade']:
        x=back.merge(original[original.model==model][['date','code','horizon','error_bp']],on=['date','code','horizon'],validate='one_to_one')
        for h,g in x.groupby('horizon'):
            pairs.append(dict(model=model,horizon=h,n=len(g),v2_mae=g.v2_error.mean(),baseline_mae=g.error_bp.mean()))
    pairs=pd.DataFrame(pairs);pairs.to_csv(out/'paired_quote_trade_baselines.csv',index=False,encoding='utf-8-sig')
    f=pd.read_json(out/'features.json',convert_dates=False,dtype={'code':str,'date':str})
    d,m=apply(f,info['chosen'])
    history=f[['date','code','name','issuer','official_yield','base_spread','curve_yield','valuation_date','curve_name','quote_days','trade_days']].copy()
    history['fair_yield_v2']=(f.official_yield+d/100).where(m&info['release_experimental'])
    history['fair_spread_v2']=(f.base_spread+d).where(m&info['release_experimental'])
    history['signal_bp']=d.where(m);history['selected']=m
    history['research_status']='事后探索规则历史重算，非当时实盘预测'
    history.to_csv(out/'historical_predictions_v2.csv',index=False,encoding='utf-8-sig')
    hlast=history[history.date==history.date.max()]
    check=latest.merge(hlast[['code','fair_yield_v2','fair_spread_v2']],on='code',suffixes=('_export','_calc'))
    for field in ['fair_yield_v2','fair_spread_v2']:
        assert np.allclose(check[field+'_export'],check[field+'_calc'],equal_nan=True)
    assert latest.code.is_unique and len(latest)==24186
    assert latest.loc[latest.status=='不预测',['fair_yield_v2','fair_spread_v2']].isna().all().all()
    assert len(back[back.horizon==5])==458
    # Historical fair spread and fair yield must imply exactly the same adjustment.
    active=history.dropna(subset=['fair_yield_v2'])
    assert np.allclose((active.fair_yield_v2-active.official_yield)*100,active.fair_spread_v2-active.base_spread)
    latest.groupby('reason',dropna=False).size().rename('count').to_csv(out/'abstention_counts.csv',encoding='utf-8-sig')
    watch=latest[latest.fair_yield_v2.notna()].copy()
    directions=back[back.horizon==5].copy()
    directions['direction']=np.where(directions.prediction>0,'走阔','收窄')
    directions=directions.groupby('direction').agg(n=('code','size'),v2_mae=('v2_error','mean'),baseline_mae=('zero_error','mean')).reset_index()
    directions.to_csv(out/'direction_diagnostics.csv',index=False,encoding='utf-8-sig')
    note='''# 公允估值第二轮：减少指标、允许不预测

已完成两阶段探索。保留首版作对照，网页及生产缓存未修改。最新数据仍截至2026-09-16，并非实时行情。

## 结论

1. 首版全样本5日MAE为1.558BP；去掉数量、趋势和主体修正后降至1.410BP，但仍略差于同样本官方利差不变基准1.392BP。当前数据不支持保留这些修正，不等于证明它们长期无效。
2. 首轮182组稀疏筛选探索未产生合格开发组。强制要求窄双边报价及多日成交确认，仅留下8条成熟5日记录，不能据此论证有效。
3. 后续明确登记为事后探索的24组持续报价方案中，按开发组选择 level_persistent_0.25，再用不同发行人组作稳定性检查。该方案最新输出122个上市代码的实验公允值，占全池24,186代码的0.50%；其余24,064个不预测并给出原因。
4. 历史5日选中458个“起点×上市代码”观测、90个主体、4个起点，占原联合模型可评价观测的0.82%。MAE为2.131BP，对应同样本不变基准3.711BP，下降42.6%；原模型在同一筛选样本误差为6.053BP。不可拿2.131BP与全池1.392BP直接比较。
5. 不同发行人检查组181条观测、36个主体，误差2.048BP，对应基准3.380BP，4个起点均改善。但它不是未接触过的独立样本外测试。5日方向准确率检查组84.5%，低于全部预测收窄的88.4%；目前证据主要支持调整幅度控制，尚不能宣称方向择时有效。
6. 最终方向拆分显示：收窄组387条，误差1.706BP，对照3.581BP；走阔组71条，误差4.447BP，对照4.419BP，没有改善。最新122条中102条收窄、20条走阔，因此不能把整体改善推广到走阔信号。保留全部规则输出供复核，不再按本次结果追加方向过滤；20条走阔仅作为证据较弱的实验观察。

## 当前实验规则

过去5个交易日至少4日有效报价；当天仍有有效报价，新鲜度至少0.8；至少3日报价边界相对起点官方利差呈同向偏离，最新边界继续确认该方向。保留首版报价水平和成交水平贡献，将二者合计调整缩为25%，绝对调整不足1BP时不预测，上限10BP。持续性比较在评级曲线利差空间内进行；历史边界与同一起点参考比较，并非每个历史日的当日官方偏离。

实验公允估值 = 起点已知官方收益率 + 调整BP/100；实验公允利差 = 起点已知官方利差 + 调整BP。官方与曲线按原模型发布滞后口径使用。缩为25%是开发组内有限探索选出的参数，不是独立验证最优值。未知挂量不当零；当前选中规则不要求挂量或成交，不因这两项缺失自动丢弃持续单边报价。

## 成交是否有增量信息

首版共同样本中成交改善了报价模型，但联合模型仍未胜过不变基准。第二轮强制成交确认显著压缩样本；本轮胜出的规则允许无成交证据。成交水平保留为可用证据，但无法从本轮结果单独归因其增量有效性。成交量缺失仍不以成交笔数冒充。

## 边界及下一步

全部选择过程都发生在见过首版结果之后，报告包含共206组探索的多重比较风险。发行人分组不能消除同一时期市场环境、日期重叠、同券多代码及历史数据修订影响。按券组及发行人等权的误差改善仍为正，但独立日期仅4个。3日仅作辅助，10日终点尚未成熟，不填结果；不声称统计显著，不报告虚假的置信区间。

现在冻结该规则作为后续验证候选。后续新日期只按冻结规则形成预测并保存，再等待完整5/10日结果；如继续调参，应另留封存日期测试。当前没有设置自动采集或自动复跑。本轮不自动扩大覆盖：缺乏证据时宁可留空。

## 交付和复现

latest_all_bonds.csv：全池含不预测原因；latest_watchlist.csv：122个实验信号；historical_predictions_v2.csv：历史逐日起点重算（不代表当时已生成）；selected_backtest.csv：成熟3/5日期间的逐条终点结果；chosen_metrics.csv、daily_stability.csv、matched_baselines.csv、paired_quote_trade_baselines.csv：同样本比较；两个 experiment_registry.json 与所有候选结果保留探索轨迹。

运行：python -m fair_value_research.selective，然后 python -m fair_value_research.selective_followup，再 python -m fair_value_research.selective_report。沿用冻结的首版输入，不补造历史。
'''
    (root/'研究结论.md').write_text(note,encoding='utf8')
    def table(df):return df.to_html(index=False,escape=True,float_format=lambda v:f'{v:.3f}')
    body=''.join('<p>'+html.escape(p).replace('\n','<br>')+'</p>' for p in note.split('\n\n'))
    links=' '.join(f'<a href="persistent_followup/{n}">{html.escape(label)}</a>' for n,label in [('latest_watchlist.csv','下载实验信号'),('latest_all_bonds.csv','下载全池及不预测原因'),('historical_predictions_v2.csv','历史重算'),('selected_backtest.csv','逐条回测')])
    columns=['code','name','official_yield','fair_yield_v2','fair_spread_v2','watch_delta_bp','quote_days','trade_days']
    body+=links+'<h2>分组与期限</h2>'+table(metrics[['horizon','split','n','issuers','origins','coverage','mae_bp','baseline_mae_bp','direction_accuracy','always_down_accuracy']])
    body+='<h2>同样本市场/本券动量基准</h2>'+table(pd.read_csv(out/'matched_baselines.csv'))
    body+='<h2>同样本报价与成交基准（各基准覆盖不同）</h2>'+table(pairs)
    body+='<h2>每日稳定性</h2>'+table(pd.read_csv(out/'daily_stability.csv'))
    body+='<h2>方向诊断：走阔组尚未胜过基准</h2>'+table(directions)
    body+='<h2>最新实验信号：共122个上市代码</h2>'+table(watch[columns])
    (root/'研究结论.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>公允估值第二轮研究</title><style>body{font:16px/1.7 sans-serif;color:#203348;margin:32px;max-width:1400px}table{border-collapse:collapse;font-size:13px;display:block;overflow:auto}td,th{padding:8px;border:1px solid #ddd;white-space:nowrap}th{background:#eaf1f5}a{margin-right:24px}p{max-width:1100px}</style>'+body+'</html>',encoding='utf8')
    print(pairs.to_string(index=False));print('Reconciled latest full pool, fair yield/spread identities, abstentions and historical exports.')


if __name__=='__main__':main()
