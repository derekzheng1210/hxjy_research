# 公允估值观察

页面 `/fair-value-observation`，归入信用债研究。读接口使用现有登录，管理员重试另需管理员身份及同源请求头；普通查询不采集、不重新选参。

## 初始化与启动

使用项目虚拟环境，在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe -m fair_value_observation init
.\.venv\Scripts\python.exe -m fair_value_observation predict
.\.venv\Scripts\python.exe run_production.py
```

`init` 从 `outputs/fair_value_v1/inputs` 和 `outputs/fair_value_widening/features.json` 导入冻结研究，重算纯报价候选、统计同方向5日胜率并冻结默认条件。历史输入复制到外置目录，不依赖输出文件在运行时持续存在。SQLite与输入归档位于 `PORTAL_DATA_ROOT/data/fair_value_observation`。

生产入口、开发入口和Gunicorn worker均接入独立单实例调度器。默认 `FAIR_VALUE_OBSERVATION_ENABLED=1`；设置为0可暂停本模块。08:30补齐兑现、16:10尝试日终预测，失败/等待至少5分钟后重试；不会阻塞原有经纪商任务。门户及经纪商快照调度需持续运行。若16:05前无快照或交易日未确认，记录等待，不补造预测。

停机期间需要补算可在停止观测调度后执行 `python -m fair_value_observation backfill --date YYYYMMDD`，这类数据归为事后补算。CLI与调度器共用文件锁，不允许并发修改。`settle`命令仅更新已发布官方数据及成熟结果。

## 规则与统计

`quote-only-v1`登记112个去重后的可执行条件、按方向分别统计。原联合水平重新使用纯报价贡献；纯成交中心和报价成交均价候选仅保留历史来源。成交仅可确认或否决，不改变公允值幅度。条件阈值至少1BP，调整上限10BP。

默认条件按历史全样本同方向5日胜率严格>50%选入，无最低样本门槛。成熟记录不足100或成熟起点不足20标记样本不足。入选后不自动降级；规则变更必须修改版本号，不能沿用旧数据库定义。

合成先按 `engine:gate` 条件族取调整BP中位数，再在条件族间取中位数。只有入选方向的条件进入合成，正反同时出现标注分歧，合成绝对调整<1BP不输出公允值。不把组件胜率当作合成胜率。

历史探索、上线后、补算三类独立保存。每个版本/日期/代码/条件的预测唯一且不覆盖。官方实际不变计未胜；未成熟、标签缺失不入分母。首次可用官方结果作为主口径，后续修订另存`revisions`。缺少完整路径不计算期间触及与反向幅度。数据接口未返回当日曲线不能推定休市。

覆盖率分母为同版本/样本类别/日期范围中有本券证据及完整官方曲线基准的债券起点数，未按方向预先筛选。检索条件存在时分母仍为该日期范围完整证据池，避免条件覆盖率与搜索命中率混淆。主体与近似同券组等权诊断通过条件逐日起点接口获取。

## 接口与验证

`/api/fair-value-observation/{overview,conditions,bonds,export}` 接受 version、kind、horizon、date、direction、search；bonds另接受page/page_size（1至200）。单券 `/bonds/<code>/history`；条件 `/conditions/<id>/daily`。空数据返回待兑现，不伪造0%胜率。

```powershell
.\.venv\Scripts\python.exe -m unittest test_fair_value_observation test_broker_market fair_value_research.test_model fair_value_research.test_selective fair_value_research.test_persistent fair_value_research.test_widening
```

`test_fair_value_observation_frontend.js` 使用Playwright对隔离测试服务验证筛选（含下拉多选标签）、读取进度条、明细、折叠、导出、10日待兑现、移动布局及浏览器错误；不得向生产接口提交采集任务。前置：`.venv\Scripts\python.exe scripts\fair_value_frontend_fixture.py` 启动5094隔离夹具服务（临时数据目录），并以 `PLAYWRIGHT_MODULE=<repo>\fair_value_research\node_modules\playwright` 运行测试。

## 债券名单界面（测试阶段）

主页面默认选择实际最新预测日期；日期选择仅列出已经生成预测的日期。收益率偏高表示负向调整，收益率偏低表示正向调整；不足1BP的合成结果不进入用户名单。简称打开债券详查，走势按钮打开独立抽屉。手机按债券卡片展示。

新增只读接口 `dates`、`facets`、`listings`、`predictions/<id>/chart`。名单支持评级、参考内评、主体性质、城投/次级、交易场所、期限和收益率范围以及排序分页；`export?view=user`复用完全相同的查询条件。`/fair-value-observation/admin`限制管理员访问任务状态及重试。

停下持有观测锁的门户后执行 `python -m fair_value_observation migrate`，从原始历史输入补充属性、条件胜率快照及真实逐日数据，不改变冻结目标或条件入选结果。原始资料未提供的属性保持未知。新预测生成时保存3/5/10日期限的条件统计；仅纳入生成时已观测且终点早于起点的成熟结果，事后补算不进入该历史胜率分母。历史探索明确为回看统计；条件范围不是单券胜率。

`daily_points`保存首次逐日官方值，缺失可补齐，后续修订另存`daily_revisions`；图表不进行插值，上游读取仅在采集任务发生。未来未确认交易日留空。新增数据表均为加法迁移。
