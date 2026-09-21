"""Add business conclusions and balanced examples from frozen backtest files."""
import json
import argparse
from pathlib import Path
import pandas as pd
from .collect import save


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--output",default="outputs/fair_value_v1")
    out=Path(ap.parse_args().output)
    metrics=pd.read_csv(out/"metrics.csv")
    paired=pd.read_csv(out/"common_sample_metrics.csv")
    scenarios=pd.read_csv(out/"scenario_metrics.csv")
    ablation=pd.read_csv(out/"ablation_metrics.csv")
    verification=json.loads((out/"verification.json").read_text(encoding="utf8"))
    mainrow=metrics[(metrics.model=="joint")&(metrics.horizon==5)].iloc[0]
    pair=paired[paired.horizon==5].set_index("model")
    trade=metrics[(metrics.model=="trade")&(metrics.horizon==5)].iloc[0]
    scenario=scenarios[scenarios.horizon==5].set_index("scenario")
    lines=["# 首轮公允估值研究结果", "",
        f"已测算固定债券池 {verification['universe']:,} 只，其中 {verification['valid_fair_values']:,} 只具备公允值。全池成交查询完成，最终失败批次 {len(verification['failed_trade_batches'])} 个。",
        "", "## 整体效果",
        f"联合模型5日MAE为 {mainrow.mae_bp:.2f}BP，同样本官方利差不变为 {mainrow.baseline_mae_bp:.2f}BP，首版未优于不变基准。",
        f"共同样本报价版MAE {pair.loc['quote','mae_bp']:.2f}BP，联合版 {pair.loc['joint','mae_bp']:.2f}BP，成交有增量信息；但该共同样本不变基准为 {pair.loc['joint','baseline_mae_bp']:.2f}BP，联合版仍未胜出。",
        f"成交版5日MAE {trade.mae_bp:.3f}BP，接近其同样本不变基准 {trade.baseline_mae_bp:.3f}BP；偏离至少1BP的信号仅 {int(trade.active_observations)} 个，不能把强收缩后的微小优势视为稳定预测能力。",
        "", "## 四类业务信号",
    ]
    for name in ["持续单边Ofr偏高","买量占优且报价偏低","报价收益率下移","主体卖压向上"]:
        r=scenario.loc[name]
        lines.append(f"- {name}：5日方向正确率 {r.direction_accuracy:.1%}，模型MAE {r.mae_bp:.2f}BP，对应不变基准 {r.baseline_mae_bp:.2f}BP，非中性信号 {int(r.active_observations)} 个。")
    lines += ["", "报价下移和买量占优的方向表现较好，但这不能单独证明模型有效：四类场景的估值误差均未优于各自不变基准，方向比例也受本段市场环境影响。",
        "", "## 参数与因素检验"]
    for r in ablation[ablation.horizon==5].itertuples():
        lines.append(f"- 移除{r.ablation}因素后，5日MAE为 {r.mae_bp:.2f}BP。")
    lines += ["", "趋势修正使本段样本误差增加较明显。缩小总修正幅度的敏感性方案也更接近基准，提示首版幅度偏激进。主模型保持预先登记参数，不用这些事后发现回头替换结果。",
        "", "## 限制",
        "只有4个完整5日起点；10日结果尚未成熟。参数未做独立训练或样本外验证。历史评级仅有事件日期，缺少原始发布日期和修订版本；固定债券池也不是逐日历史全市场。",
        "日度成交统计保守滞后一日使用，首版没有把GVN/TKN方向和不明单位的成交量加入权重。研究结果不能解释成已经验证的真实成交价格。"]
    (out/"研究结论.md").write_text("\n".join(lines),encoding="utf8")
    cases=pd.read_csv(out/"five_day_cases.csv",dtype={"date":str,"target_date":str,"code":str})
    recent=cases[cases.date==cases.date.max()]
    positive=recent[recent.convergence_bp>0].sort_values("convergence_bp",ascending=False).head(10)
    negative=recent[recent.convergence_bp<0].sort_values("convergence_bp").head(10)
    example=pd.concat([positive.assign(result="距离收敛最多"),negative.assign(result="距离扩大最多")])
    keep=["result","date","target_date","code","name","predicted_delta_bp","actual_delta_bp","convergence_bp"]
    example=example[keep]
    example.to_csv(out/"five_day_examples.csv",index=False,encoding="utf-8-sig")
    html_path=out/"report.html"
    content=html_path.read_text(encoding="utf8")
    marker="<!--business-findings-->"
    if marker in content:
        content=content.split(marker)[0]+"</main></html>"
    extra=f'''{marker}<h2>业务结论与具体债券</h2><div class="card"><p>共同样本的不变基准为{pair.loc['joint','baseline_mae_bp']:.2f}BP；联合模型虽改善报价版，但仍未胜过该基准。</p><p>四类业务场景的误差均未优于各自不变基准。趋势修正在本段样本中增加误差；主模型保持预设参数。成交版看似接近基准，但有至少1BP偏离的5日信号仅{int(trade.active_observations)}个。</p><p><a href="研究结论.md">阅读完整中文研究结论</a> · <a href="five_day_cases.csv">全部5日兑现案例</a></p></div><p>以下按最近成熟起点列出距离收敛最多与扩大最多的案例，均是事后示例，不是推荐清单。</p><div class="card scroll">{example.rename(columns={"result":"分组","date":"起点","target_date":"终点","code":"代码","name":"简称","predicted_delta_bp":"预测偏离BP","actual_delta_bp":"实际变化BP","convergence_bp":"距离收敛BP"}).to_html(index=False,float_format=lambda v:f"{v:.2f}")}</div>'''
    html_path.write_text(content.replace("</main></html>",extra+"</main></html>"),encoding="utf8")
    print("business findings and balanced examples exported")


if __name__=="__main__":
    main()
