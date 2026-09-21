"""Read-only reconciliation, path risk and final widening research report."""
import json
import html
from pathlib import Path
import numpy as np
import pandas as pd
from .run import Inputs,read

ROOT=Path('outputs/fair_value_widening')


class ValuationInputs(Inputs):
    """Use the same valuation/curve/term methods without loading broker input again."""
    def __init__(self,root):
        self.bonds=read(root/'universe.json')['bonds'];self.calendar=read(root/'calendar.json')
        self.curves=read(root/'curves.json');self.values={p.stem:read(p) for p in (root/'valuations').glob('*.json')}
        self._curves_cache={};self._term_cache={};self._actual_cache={}


def main():
    first=pd.read_csv(ROOT/'all_candidates.csv');events=pd.read_csv(ROOT/'events/all_candidates.csv')
    a=json.loads((ROOT/'summary.json').read_text(encoding='utf8'));b=json.loads((ROOT/'events/summary.json').read_text(encoding='utf8'))
    data=ValuationInputs(Path('outputs/fair_value_v1/inputs'));bonds={b['code']:b for b in data.bonds}
    details=[]
    for stage,folder in [('persistent',ROOT),('events',ROOT/'events')]:
        f=pd.read_csv(folder/'diagnostic_backtest.csv',dtype={'date':str,'code':str})
        records=[]
        for r in f.to_dict('records'):
            idx=data.calendar.index(r['date']);h=int(r['horizon']);bond=bonds[r['code']]
            path=[data.actual(bond,day,r['curve_name']) for day in data.calendar[idx+1:idx+h+1]]
            valid=len(path)==h and all(x is not None for x in path)
            end=path[-1] if path else None
            if end is not None:assert np.isclose(end[0]-r['base_spread'],r['actual_delta_bp'],atol=1e-5)
            moves=[x[0]-r['base_spread'] for x in path if x is not None]
            r.update(stage=stage,complete_path=valid,
                touched_1bp=bool(max(moves)>=r['prediction']-1) if valid else np.nan,
                reached_1bp=bool(moves[-1]>=r['prediction']-1) if valid else np.nan,
                max_adverse_bp=max(0.,-min(moves)) if valid else np.nan)
            records.append(r)
        result=pd.DataFrame(records);details.append(result)
        result.to_csv(folder/'path_risk_detail.csv',index=False,encoding='utf-8-sig')
    detail=pd.concat(details)
    risk=detail.groupby(['stage','horizon']).agg(n=('code','size'),complete_paths=('complete_path','sum'),end_reached=('reached_1bp','mean'),touched=('touched_1bp','mean'),adverse_mean=('max_adverse_bp','mean'),adverse_p90=('max_adverse_bp',lambda x:x.quantile(.9)))
    risk.to_csv(ROOT/'path_risk_summary.csv',encoding='utf-8-sig')
    x=detail[(detail.stage=='persistent')&(detail.horizon==5)].copy()
    cases=pd.concat([x.nsmallest(8,'actual_delta_bp'),x.nlargest(8,'actual_delta_bp')]).drop_duplicates(['date','code'])
    cases[['date','code','name','issuer','prediction','actual_delta_bp','error_bp','max_adverse_bp']].to_csv(ROOT/'balanced_cases.csv',index=False,encoding='utf-8-sig')
    concentration=x.groupby('issuer').agg(n=('code','size'),actual_sum=('actual_delta_bp','sum'),actual_mean=('actual_delta_bp','mean'))
    concentration.to_csv(ROOT/'issuer_concentration.csv',encoding='utf-8-sig')
    # Latest files must contain no fair values when acceptance fails.
    latest=pd.read_csv(ROOT/'latest_all_evidence.csv')
    if not a['release']:assert latest[['fair_yield_widening','fair_spread_widening']].isna().all().all()
    latest_event=pd.read_csv(ROOT/'events/latest_diagnostic_watchlist.csv')
    if not b['release']:assert latest_event[['fair_yield','fair_spread']].isna().all().all()
    assert (detail.prediction>=1).all()
    notes='''# 走阔专项研究：42组探索，尚未找到可接受的有效规则

完成30组持续偏离研究和12组另行登记的事件研究。成交已从估值幅度中完全剔除，包括成交对原联合模型报价权重的间接影响；只保留成交确认或否决条件。本次数据仍截至2026-09-16，未增加历史或补造挂盘。所有方案均为事后探索，不是独立样本外验证。

## 核心结果

持续卖价偏高、报价幅度缩小至25%：5日69条观测，28个主体，方向胜率50.7%，MAE 4.428BP，对应不变基准4.351BP；没有改善。开发发行人组胜率64.9%，其他发行人组仅34.4%，显示明显不稳定。

持续卖价偏高且卖价上移：25%幅度下39条，胜率53.8%，误差仅改善0.014BP；开发组68.0%，其他发行人组28.6%。不构成有效证据。

去掉“估值已偏低”条件，直接研究最近卖价上移：半幅度下4,230条、1,275个主体，胜率26.4%，误差反而增加1.464BP。加入双边窄价差、卖量增长、主体确认或官方此前已走阔，均未形成合格规则。卖价短期上移不等于未来官方利差继续走阔。

有限线索：持续偏离叠加成交确认、50%幅度的组合，5日7条、5个主体，胜率71.4%，MAE改善0.598BP。样本过小，只保留为待验证假设；成交仍只作确认条件，不提供独立预测幅度。不能据此宣传高胜率。卖量压力和主体确认同样存在有效观测不足。

## 输出决定

42组中没有开发规则通过预先登记的样本量、日期稳定性及方向/误差门槛，本轮新增有效预测为0。为诊断保留持续偏离21个最新代码、事件规则207个最新代码，两份名单可能重合，不可相加；所有公允值列留空。这些不是推荐名单，不替换上一版结果。上一版20个走阔实验信号仍不应视为已验证。

## 如何解释结果

当前更像是在识别“经纪商报价与估值有分歧”，尚不能证明分歧会按卖方方向兑现。可能涉及不可执行报价、报价更新选择、期限/曲线口径或市场状态，但本轮没有数据证明哪种解释成立。旧样本上继续放宽规则或按结果挑发行人，容易只剩偶然胜出者，因此本轮停止追加参数组合。

下一步优先补充按报价ID记录的更新、撤单与可匹配成交事件，以及新增交易日。日度成交不能证明某报价消失是由成交导致。本轮没有设置自动采集或复跑。后续只把“持续偏离加独立成交确认”作为预先登记的检验假设，并应保留新的封存日期；未验证前不给走阔公允值。

## 验证口径

方向胜率指未来5日官方评级曲线利差上升，并非盈利率。终点到达与期间触及采用预设1BP容差；当预测本身接近1BP时该容差较宽，不能用到达率替代方向准确率。反向风险是窗口内官方利差相对起点向收窄方向的最大变化。日期窗口重叠、仅4个成熟5日起点；10日尚未成熟。按发行人和近似同券组等权检查不代表消除全部相关性。

主体确认剔除同发行人、同发行日、同到期日、层级和担保匹配的整组近似同券，至少两只其他券组，期限差不超过2年。数量缺失不作零值处理，重复快照不累计为新增挂量。所有未来结果只进入评价，不用于当日起点特征。
'''
    (ROOT/'研究结论.md').write_text(notes,encoding='utf8')
    body='<h1>走阔专项研究</h1>'+''.join('<p>'+html.escape(p).replace('\n','<br>')+'</p>' for p in notes.split('\n\n'))
    links=[('all_candidates.csv','持续偏离全部30组'),('events/all_candidates.csv','事件全部12组'),('balanced_cases.csv','正反案例'),('path_risk_summary.csv','路径风险'),('latest_diagnostic_watchlist.csv','持续偏离诊断名单'),('events/latest_diagnostic_watchlist.csv','事件诊断名单')]
    body+=' '.join(f'<a href="{p}">{t}</a>' for p,t in links)
    for title,frame in [('持续偏离方案（全样本）',first[first.split=='all'][['id','n','issuers','direction_accuracy','mae_bp','baseline_mae_bp','improvement_bp']]),('事件方案（全样本）',events[events.split=='all'][['id','n','issuers','direction_accuracy','mae_bp','baseline_mae_bp','improvement_bp']]),('方向胜率与误差稳定性',pd.read_csv(ROOT/'chosen_metrics.csv')),('路径风险',risk.reset_index()),('正反案例',cases[['date','code','name','prediction','actual_delta_bp','max_adverse_bp']])]:
        body+='<h2>'+title+'</h2>'+frame.to_html(index=False,float_format=lambda x:f'{x:.3f}')
    (ROOT/'report.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>走阔专项研究</title><style>body{font:16px/1.8 sans-serif;margin:32px;color:#203348}p{max-width:1150px}table{border-collapse:collapse;font-size:13px;display:block;overflow:auto}td,th{padding:8px;border:1px solid #ccc;white-space:nowrap}th{background:#edf3f6}a{margin-right:18px}</style>'+body+'</html>',encoding='utf8')
    print(risk.to_string());print('Checked future labels, path completeness and null fair values for rejected rules.')


if __name__=='__main__':main()
