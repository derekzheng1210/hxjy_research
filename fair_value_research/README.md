# 公允估值离线研究

走阔专项（30组有限事后探索，成交只作确认/否决，不作为估值幅度因子）：

```powershell
.\.venv\Scripts\python.exe -m fair_value_research.widening
.\.venv\Scripts\python.exe -m fair_value_research.widening_events
.\.venv\Scripts\python.exe -m fair_value_research.widening_report
.\.venv\Scripts\python.exe -m unittest fair_value_research.test_widening -v
```

输出 `outputs/fair_value_widening/report.html`、所有组合结果、逐条回测与最新诊断名单。持续偏离30组之后另行登记12组事件探索，停止进一步网格搜索；最终报告汇总两阶段及完整路径反向风险。先按开发发行人选择，再用其他发行人否决；若无方案通过，不输出公允值。诊断名单不是有效预测名单，不覆盖原研究结果。此研究仍只有4个完整5日起点，不能证明独立样本外有效。

第二轮稀疏预测研究（沿用冻结首版输入）：

```powershell
.\.venv\Scripts\python.exe -m fair_value_research.selective
.\.venv\Scripts\python.exe -m fair_value_research.selective_followup
.\.venv\Scripts\python.exe -m fair_value_research.selective_report
.\.venv\Scripts\python.exe -m unittest fair_value_research.test_model fair_value_research.test_selective fair_value_research.test_persistent -v
```

第二轮最终结论位于 `outputs/fair_value_v2/研究结论.html`，全量实验公允值和拒绝预测原因位于 `persistent_followup/latest_all_bonds.csv`。第一阶段182组方案及第二阶段24组事后探索分别登记；发行人检查组仅检查横截面稳定性，不称为独立时间样本外验证。未成熟期限不填，未经门槛筛选的债券不输出公允值。当前选定规则冻结用于后续新日期验证，不自动优化或扩大覆盖。

不改门户页面或生产缓存。输入快照与输出放在项目 `outputs/fair_value_v1/`（Git忽略）。

```powershell
.\.venv\Scripts\python.exe -m fair_value_research.collect --output outputs/fair_value_v1 --workers 4
.\.venv\Scripts\python.exe -m fair_value_research.run --output outputs/fair_value_v1
.\.venv\Scripts\python.exe -m fair_value_research.verify --output outputs/fair_value_v1
.\.venv\Scripts\python.exe -m fair_value_research.findings --output outputs/fair_value_v1
.\.venv\Scripts\python.exe -m unittest fair_value_research.test_model -v
```

采集只调用现有Oracle只读连接和DM日度成交接口，凭据从现有环境/.env读取，不写入输出。研究固定区间2026-08-31至2026-09-16，估值和曲线多取前一周用于起点已知基准。DM接口每次最多5个完整市场代码，成功分片重跑复用，失败重试。外部请求可能较慢。

`model.py` 是无IO的预设模型，参数无需训练；权重、上限与半衰期记录在 `parameters.json`。内部单位BP，收益率百分数输出。单边报价使用软约束，持续多日通过有效证据权重饱和；主体按同层级、同担保人、2年期限范围剔除本券后加权。只报探索性结果，不将参数敏感性最优项替换主模型。

输入当日截止16:05报价、严格早于当日的日度成交统计/官方估值/评级曲线。历史评级事件保守滞后1日；Oracle缺少点时发布和修订版本，报告明确这项限制。固定门户债券池并非历史全市场，不使用当前隐含评级倒填历史。历史成交使用日度收盘收益率，成交笔数只衡量活跃性，不是成交量。未独立核验GVN/TKN方向，因此系数为零。

回测冻结起点目标及评级曲线身份，未来按当日期限取曲线，分别衡量3/5/10交易日的终点与路径兑现。只在完整未来路径上计算触及与最大反向变动，未到期/标签缺失单列。评级曲线变更另有标记。当前样本只能提供最多4个完整5日预测起点。

主要文件：`latest_all_bonds.csv`、`historical_predictions.csv`、`backtest_detail.csv`、`metrics.csv`、`common_sample_metrics.csv`、`sensitivity.csv`、`label_coverage.csv`、`report.html`。表中的质量分数是证据覆盖强度，不是置信概率。敏感性上下限不是统计置信区间。

本次最终加入跨市场同券的保守防护：同主体、同发行日、同有效到期日、同层级和同担保人的整个券组不互相作为主体参考，其他券组最多取一个代表。不声称这些券组是官方同券映射；回测数量仍按上市代码统计。`aliases.py` 记录了Oracle标准ISIN字段核验结果，不能将 `ISINTSPLIT`（是否利息分拆）误用为ISIN。

Excel结果由 `export_workbook.mjs` 使用Bundled Node和artifact-tool导出。先在本目录建立指向Bundled `node_modules`的junction，运行Bundled Node `fair_value_research/export_workbook.mjs outputs/fair_value_v1`。XLSX仅是离线结果快照，HTML报告与CSV另含完整历史回测、共同样本、业务场景、因素移除和敏感性分析。
