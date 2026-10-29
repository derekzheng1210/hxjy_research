# 内部研究工作台

这是可手工上传到 GitHub 的 Flask 部署目录。

## 数据目录

运行态数据（`data/`、`uploads/`、`logs/`、一级定价底稿）外置于独立目录，不随仓库分发。
代码通过环境变量 `PORTAL_DATA_ROOT` 定位数据根目录，未设置时默认使用项目同级 `../juyuan_credit_data`。

- 数据若放在项目同级（`../juyuan_credit_data`），可不设环境变量直接运行。
- 数据放别处时，复制 `.env.example` 为 `.env` 并填入本机路径，或设系统环境变量：
  ```powershell
  $env:PORTAL_DATA_ROOT="D:\信用债研究\完整网页内容\juyuan_credit_data"
  ```
- `config/映射表.xlsx` 随仓库分发；`primary_market_pricing/大智慧财汇元数据表结构 (1).xlsx` 为参考资料，不纳入版本控制，需另行放置。
- 利率债研究的 `spreads.db`、`bond_switch.db`、`tracker.db` 位于 `PORTAL_DATA_ROOT/interest_bond/`；页面显隐配置保存在 `PORTAL_DATA_ROOT/page_visibility.json`。

## 门户页面结构

首页按“管理与协同、利率债研究、信用债一级发行、信用债研究、机构行为、中观研究”分区。后台“页面显隐管理”可即时控制各入口是否出现在首页和统一导航。

- 利率债研究：超长端利率利差、新老券利差、国债·地方债发行跟踪（原 `bond_monitor` 三个模块，统一使用主站登录与端口）。
- 信用债一级发行：一级发行看板（`bond_dashboard/`，Next.js 独立服务，见下节）。
- 信用债研究：二级择券、收益率倒挂、信用骑乘策略、存量债利差、一级偏离统计。
- 机构行为：机构行为监测。
- 中观研究：行业景气度跟踪、行业景气高频跟踪。
- 管理与协同：内部知识库。

## 信用债一级发行看板（bond_dashboard）

原独立部署的 Next.js 一级市场看板整体并入本仓库 `bond_dashboard/`（含 `node_modules` 与 `.next` 构建产物，克隆后无需 npm install / build 即可运行），由门户反向代理整合：

- 访问入口 `/bond-dashboard/`，鉴权与门户站点密码合一（无独立登录）。
- 门户首次请求或生产启动时自动拉起 Node 服务（`node scripts/serve.mjs`，分离进程，绑定 127.0.0.1:3100，不可从局域网绕过门户直连）；`BOND_DASHBOARD_AUTOSTART=0` 关闭，`BOND_DASHBOARD_UPSTREAM` 改上游地址。更新看板代码后需杀掉 node 进程令其重启生效。
- HTML 响应由代理注入统一顶部导航栏：App Router 对整份 document 水合，导航在 load 事件后由脚本插入（`bond_dashboard_proxy.py`），侧边栏/移动顶栏自动下移让位。
- 凭证与环境由门户统一下发：DM 凭证复用门户 `.env` 的 `INNO_APP_KEY/INNO_APP_SECRET`；主体内评文件自动指向 `PORTAL_DATA_ROOT` 下的 `data/portal_data.json`。看板自身 `.env.local`（不入库）仍可覆盖。
- 看板运行态数据已**外置**到 `PORTAL_DATA_ROOT/bond_dashboard/`（投标库 bond.db、日历/估值/曲线 JSON、cache、海报；拉起时经 `BOND_DASHBOARD_DATA_DIR` 下发，源码收口在 `bond_dashboard/src/lib/data-dir.ts`，未设置时回退项目内 `./data` 保持独立部署兼容）。每日 19:00 由看板内置调度自动刷新 DM 数据，详见 `bond_dashboard/方案B改造说明.md`。改动看板源码后需 `npm run build` 重建（需能访问 fonts.googleapis.com 下载 Geist 字体）再杀 node 进程重拉。
- 个券中债估值双源：DM 行情接口（`market-data/date`）为首选；其订阅失效（HTTP 429 无订阅套餐）或个别券无值时，用门户 8:30 每日任务写入的聚源中债估值回退（`juyuan_update/dashboard_valuations.py` 查 TQ_QT_CBESTIMATE，DATASOURCE=1 到期口径，落盘 `oracle_cb_fallback.json`；看板 `dm-refresh.ts` 按 security_id 只补空位并在 valuations.json meta 标记 `cb_fallback`），DM 恢复后自动回归其口径（含权行权）。中证估值无聚源回退，DM 失效期间交易所券该列为空。

## 迁移到另一台电脑

代码走 git（含看板源码，仅约 1MB），其余资产按下表搬运（`node_modules`/`.next`/`.env`/数据根目录均不入库）：

| 事项 | 内容 |
|---|---|
| 基础软件 | Python 3.12、Node.js ≥18（全局安装）、LibreOffice（知识库在线预览）、Git；NSSM 已随仓库携带 |
| `.env` | 门户凭证整文件拷贝：SITE_PASSWORD/ADMIN_PASSWORD/SECRET_KEY、INNO_APP_KEY/SECRET、DM 账号、LLM keys 等 |
| `juyuan_credit_data/` | 整个数据根目录拷贝（放项目同级，或设 `PORTAL_DATA_ROOT`）：各模块数据、internal_knowledge_base/、bond_dashboard/（看板 12M）、page_visibility.json、uploads |
| `bond_dashboard/node_modules` | 640M，npm 内网拉取慢，**建议整目录拷贝**（Windows + Node 版本一致即可，better-sqlite3 为预编译二进制） |
| `bond_dashboard/.next` | 首选新机 `npm run build` 重建（需 fonts.googleapis.com 可达）；网络不通时可整拷本机 `.next` 并实测 |
| `.venv` | 不拷贝，新机重建：`python -m venv .venv` + `pip install -r requirements.txt`（服务安装脚本会自动做） |
| 生产服务 | 管理员 PowerShell 运行 `install_windows_service.ps1`（建 venv、装依赖、写入服务环境变量、注册 NSSM 服务）；启动后访问 `/bond-dashboard/` 验证看板自动拉起 |
| 网络可达 | DM 数据源、机构行为上游、IPM 服务器、LLM 网关、Oracle（聚源更新）；均不可达时对应模块降级但不影响启动 |

## 本地运行

```powershell
pip install -r requirements.txt
$env:SITE_PASSWORD="your-password"
$env:ADMIN_PASSWORD="your-admin-password"
$env:SECRET_KEY="change-this-secret"
# 大模型（4 个，OpenAI 兼容）：自部署模型（阿里云网关）、DeepSeek 内网部署、MiMo、DeepSeek 官方；
# 默认按此顺序回退，优先级可在后台"大模型管理"调整；Windows 服务安装脚本会写入服务进程环境。
$env:SELF_LLM_API_KEY="sk-..."
# $env:DEEPSEEK_INTERNAL_BASE_URL="http://10.x.x.x:8000/v1"  # DeepSeek 内网部署，密钥可缺省
$env:MIMO_API_KEY="sk-..."
$env:DEEPSEEK_API_KEY="sk-..."
# 可选：数据不在项目同级时设置
# $env:PORTAL_DATA_ROOT="D:\信用债研究\完整网页内容\juyuan_credit_data"
# 可选：Wind 安装目录不是默认路径时设置（行业景气高频跟踪的一键更新使用）
# $env:WINDPY_HOME="C:\Wind\Wind.NET.Client\WindNET\x64"
# 可选：机构行为监测上游数据源（默认 http://43.137.12.140:8000）
# $env:INSTITUTION_FLOW_UPSTREAM="http://43.137.12.140:8000/bondflow/api"
python app.py
```

访问 `http://127.0.0.1:5000`，先输入 `SITE_PASSWORD`。

## Linux / 腾讯云生产部署

不要在服务器上使用 `python app.py`。项目提供了适合 2 核 / 2GB 实例的
Gunicorn 与 Nginx 示例配置：

```bash
gunicorn -c gunicorn.conf.py app:app
```

- `gunicorn.conf.py`：默认 2 workers × 2 threads，并开启预加载以减少重复内存。
- `deploy/nginx.conf.example`：开启 gzip、反向代理缓冲及 110MB 上传限制。
- `deploy/juyuan-credit-tools.service.example`：systemd 服务示例。
- 部署后用浏览器开发者工具确认大响应包含 `Content-Encoding: gzip`。

应用自身也有 gzip 兜底；Nginx 已压缩时不会重复压缩。可通过
`COMPRESS_MIN_SIZE`、`COMPRESS_LEVEL` 调整应用层压缩阈值和等级。
机构行为代理缓存默认 30 秒，可通过 `INSTITUTION_FLOW_QUERY_TTL` 调整；
一级定价结果缓存默认保留 12 组筛选结果，可通过 `PRICING_RESULT_CACHE_MAX` 调整。

示例文件替换域名和安装路径后，可按以下顺序启用：

```bash
sudo cp deploy/juyuan-credit-tools.service.example /etc/systemd/system/juyuan-credit-tools.service
sudo cp deploy/nginx.conf.example /etc/nginx/conf.d/juyuan-credit-tools.conf
sudo systemctl daemon-reload
sudo systemctl enable --now juyuan-credit-tools
sudo nginx -t && sudo systemctl reload nginx
```

## Render/其他云端建议

- Build Command: `pip install -r requirements.txt`
- Start Command: `gunicorn -c gunicorn.conf.py app:app`
- Environment Variables:
- `SITE_PASSWORD`: 全站访问密码
- `ADMIN_PASSWORD`: 后台上传和一键更新的二次验证密码，未设置时默认 `123456`
- `SECRET_KEY`: Flask session 密钥
- `PORTAL_DATA_ROOT`: 数据目录根路径（云端需单独配置持久化数据卷）

## 数据更新

- 择券工具/利差监控：后台“一键更新”从财汇 Oracle 刷新债券池与中债估值；Excel 不再提供债券信息。
- 信用骑乘策略：曲线由 Oracle 更新，中长期纯债基金指数由量化数据库（MySQL）在每日更新时自动同步，不再支持 Excel 上传。
- 行业景气度：后台上传静态 HTML。
- 行业景气高频跟踪：后台“一键更新”连接本机 Wind 终端拉取增量并自动合并缓存；业务页面不提供独立上传入口。
- 信用债两倍标准差：后台上传页面 HTML 和 `spread_data.js`。
- 机构行为监测：实时代理机构行为上游数据；国债/国开债 1-30 年期曲线由后台“一键更新”的“机构行为曲线”模块增量维护。
- 曲线映射表固定随代码发布，位于 `config/映射表.xlsx`。

## 独立信用债交易择券版本

独立副本默认仅监听本机 `127.0.0.1:5011`，并使用同级目录 `juyuan_credit_bond_trading_data`。首次运行执行
`init_bond_trading.ps1`，之后双击 `start_bond_trading.bat`。网页窗口保持运行时，内置调度器会在
工作日 08:30 更新择券估值，并在 09:30 至 16:00 的约定时点更新 DM 最优报价。

经纪商抓取凭据保存在 Git 排除的 `broker_market/dm_client_local.py`，不得提交或对外分发。
失败的抓取不会覆盖最近成功快照。门户中拆分为两个独立一级入口：

- `/secondary-bond-picker`：二级择券工具，左侧筛选、右侧行情与重点关注名单；自动剔除偏离中债估值达到 30BP 的异常单边报价。
- `/bond-picker`：收益率倒挂挖掘工具，仅保留同主体倒挂与凸点分析。
- `/bond-detail`：债券详查，按债券代码、简称或发行人搜索，汇总评级曲线两倍标准差位置、
  主体曲线凸点（拟合时剔除本券）、可切换3/6个月的骑乘收益、经纪商报价历史和主体关键日期利差。页面结论完全由
  确定性规则算法生成，不调用大模型。本券报价支持近5日、近10日与自定义区间（最长30天），
  展示区间内全部盘中时点（交易日以竖直虚线分隔），各日对照上一交易日中债估值，
  明细表可按每日一条、全部时点两档切换（从老到新）。

经纪商报价面板同时叠加 DM 量化API 的单券经纪商成交：走势图中以散点呈现各成交时点
（GVN 橙 / TKN 蓝 / TRD 紫，点大小对应当分钟笔数，图例可按方向开关），明细表可在
“报价明细 / 成交明细”间切换，成交明细支持每日汇总（笔数、GVN/TKN/TRD、收盘收益率、
高-低、中债估值与偏离BP）与逐笔时点（分钟级，同一分钟多笔合并）两档。数据经
`/api/bond-deal-history/<code>` 提供，实现位于 `broker_market/dm_deals.py`：日度统计来自
date 序列（data_source=1，次日更新），盘中时点来自 1 分钟K线（仅在有成交的分钟出bar），
当日无日度统计时用分钟线本地汇总、偏离用页面最新估值补算。按（债券, 交易日）落盘缓存于
`data/dm_deals/`（历史日永久有效，当日按 TTL 重取），凭据为 `.env` 的
`INNO_APP_KEY/INNO_APP_SECRET`，SDK `dm_quant_api_client` 见 `requirements.txt`（SM4 加密，
调用时线程本地实例并发拉取）。量化API始终为首选；不可用（订阅失效/凭据缺失/网络异常，
失败后熔断 5 分钟内不再重试、到期自动回归）时降级为网页端备源：每次经纪商抓取把当日成交
汇总增量写入单券日缓存（`stats_source="web"` 标记，不重复抓取），当日行展示网页端汇总
（无分钟时点），历史日仅返回已缓存日期且不报错；量化恢复后 web 标记日随一次区间重取被
官方数据覆盖修复。页面以 `today_source` / `quant_available` 标注实际数据来源。

债券详情页的三个月收益使用 Oracle 票息、付息频率和首次行权/到期现金流，将查询债券与
“现金流完全相同、收益率贴合隐含评级曲线”的假设券并列测算。主体曲线只使用同主体、同层级、
无担保样本，默认剔除交易所科创债（可手动恢复），剔除目标券后分段线性拟合，并自动叠加对应
隐含评级收益率曲线；期限单侧无样本且同类样本不少于4只时按两档外推：偏离边界1年以内按最近
2-3只样本最小二乘线性外推；偏离更远时按最近样本相对同隐含评级曲线的平均利差加评级曲线
在目标期限的收益率推算（可达距离不超过样本区间跨度，至少2年），置信度上限降为“中”，
超出可达距离或曲线不可用则不做外推，均在页面明确说明。

二级择券工具按 DM 的 12 个日内计划时点（9:10 预早盘及 9:30 起交易时段每半小时）保存挂盘情绪。
情绪定义为所有清洗后有效双边债券的
`[(Bid + Ofr) / 2 - 中债估值] × 100` 等权平均（BP），历史默认保留最近 60 个交易日，页面可查看
1D、2D、3D、4D、5D、10D 分时变化；右侧走势图同时展示全市场和二级资本债情绪，并可展开查看
隐含评级、参考内评和期限细分。页面状态条会明确显示当前正在更新的是 DM 经纪商挂盘，还是中债
估值与择券基础数据。

每次经纪商抓取在保存最优报价快照后，用同一 DM 网页会话顺带拉取“今日成交概览”（每券最新成交
+ 当日 TKN/GVN/TRD 汇总，`broker_market/dm_trades.py`，逐笔明细接口对该账号返回空故不用），
按券种/发行方式/剩余期限分块并以 count/list 双重校验保证完整，落盘
`data/broker_market/latest_deals.json` 并纳入择券数据版本（前端 ETag 自动感知）。成交拉取失败
只记录状态（deals_error），绝不影响报价快照。择券表在“隐含评级”前展示“成交”列：当日有成交的
债券显示最新成交收益率（悬浮显示成交时间、净价、当日笔数与 TKN/GVN/TRD 方向构成、当日收益率
区间），无成交显示"-"，支持按成交收益率排序；CSV 导出含成交收益率/净价/时间/当日笔数四列。
仅当日成交文件下发页面，隔日或当日尚未首次抓取时不展示。DM 量化 API（`dm_deals.py`，
债券详情页成交曲线）另走付费订阅，与网页端成交数据源相互独立。

二级择券的“持仓债券”页签位于“异动债券”前，仅点击该页签后才连接固收内部数据平台
`http://10.6.8.190:8600`（有线内网，优先）或 `http://10.6.121.24:8600`（WiFi 内网，前者连不上时
自动回退；两个地址是同一平台、会话互通，切换后无需重新登录），由使用人输入个人账号密码。
接口读取 `p_hold_bond` 的最新日期明细，
优先取获授权的研究盘视角；`zhenghongbin`、`cuizhuoju`、`maxiao`、`zhumeng` 必须使用研究盘，
研究盘不可用时明确报错，绝不回退到领导视角。普通账户使用获授权的个人/主基金视角。
同券按完整证券代码（保留交易所后缀）跨组合加总 `qty`，零数量不展示；页面“持仓量（万）”
及 CSV 导出统一将平台数量除以100，例如300000显示为3000万。持仓量与原有信评持仓占比口径独立。
持仓明细包含基金名称（`fund_name`）、资产单元名称（`prd_name`）、组合（`l4_name`）三个账户
维度，接口随每只债券返回按维度聚合的明细。持仓条（人名/视角行）下方提供这三个维度的多选筛选
（选项取自当前持仓，默认全部，每项下拉带“全部”一键恢复），选择后“持仓量（万）”仅统计所选
维度的持仓且无匹配的券不展示，主基金等多账户视角可按基金/资产单元/组合查看持仓；列表最右侧
新增基金名称、资产单元名称、组合三列（多账户持券以“、”合并展示，表格加宽后可横向滚动，
简称列同步加宽），CSV 导出亦包含这三列。上游若缺失维度列，筛选与三列自动隐藏，持仓展示不受
影响。
该表沿用左侧筛选，支持按持仓量排序和 CSV 导出；“查看全部持仓”清空筛选（含维度筛选），
行情池外债券保留代码、简称、持仓量并将缺失行情留空。窄屏默认收起筛选，亦可手动展开。

个人会话通过随机 HttpOnly、SameSite=Strict Cookie 隔离，最长保留30天，内部平台会话失效后
才要求重新登录；密码仅用于当次登录，不保存。上游 Cookie 和个人持仓缓存加密存于
`PORTAL_DATA_ROOT/personal_holdings/sessions.sqlite3`，多进程与重启可复用（保持 `SECRET_KEY`
稳定）。个人数据不会加入共享行情缓存或共享偏好，接口使用 `private, no-store`。
持仓缓存5分钟，当前持仓页每分钟检查，支持手动强制刷新；刷新失败保留上次数据并显示提示，
登录失效清空个人数据。共用浏览器使用后可点“退出 / 切换账号”撤销该会话。
服务器需能访问该内网平台；发布此变更后需重启 WSGI 服务以注册新接口。

每个成功抓取时点的完整经纪商报价快照按 `data/broker_market/history/` 独立留存（文件名为抓取
时刻，同刻覆盖去重），永久保留不自动清理；最新快照仍以
`data/broker_market/latest_snapshot.json` 单独维护。债券详查的本券报价历史即按需读取这些盘中
快照（按交易日与债券增量缓存单券提取结果，避免整页解析数 MB 全量文件），
读取入口为 `broker_market.storage.list_quote_history()`。

债券池从 Oracle 筛选剩余首次行权期限不少于 0.2 年、公募、非永续、固定利率且有中债隐含评级的
中票、短融、超短融、金融债、企业债、普通公司债、铁道部一般债和汇金债；项目收益票据不纳入，
并剔除国开行、进出口行和农发行。`3+2` 等含权债按首次行权日计算，
不按最终到期日。日常使用 `DATA_DOWNLOAD_TIME` 水位增量更新，每 7 天全量校准；首次切换或差异
超过阈值时只生成候选与差异报告，不覆盖旧池。

主体内评、最新可用对手限额和债项持仓从信评门户的最新可用日期自动同步。重点关注债券除评级、
期限、估值、卖量和相对情绪规则外，还要求主体最新可用对手限额严格大于 1；页面债券表支持点击
各数据表头升降序排列，空值始终排在末尾。

重点关注债券另须满足合规 630 跟踪评级要求：查询日在 6 月 30 日之后时，除当年新发债外须有当年
1 月 1 日—6 月 30 日的主体跟踪评级；查询日在 6 月 30 日（含）之前时，除当年及去年新发债外
须有去年 1—6 月或当年年初至查询日的主体跟踪评级；债券本身有债项评级时须主体与债项均满足。
评级事件每日随估值任务从聚源 `TQ_BD_CREDITRATE` 与 `TQ_BD_CREDITRATEINFO` 双表合并抓取，
判定结果随事实一并缓存（仅保留一版，每日随 Oracle 债券池与估值任务整体覆盖；缓存跨日未
刷新时按事实现算兜底）。后台上传页会展示缓存更新时间、判定基准日与满足/不满足/待确认的
数量分布。未满足或未校验（待确认）的债券一律不进入重点关注名单，且简称后以红点提示
“可能不满足合规630评级要求，投前请务必确认”；缓存缺失或有待确认债券时，页面状态条
会显示告警。

## 内部知识库

门户内置独立的管理型子系统，入口为 `/internal-knowledge-base/`，超级管理员后台为
`/internal-knowledge-base/admin`。进入子系统前需要先通过门户访问密码，再使用个人账号登录。

- 账号、报告、评分、系统配置和审计记录保存在
  `PORTAL_DATA_ROOT/internal_knowledge_base/knowledge_base.db`。
- 上传文件、永久 PDF 缓存和转换临时文件分别保存在该目录的 `uploads/`、`pdf_cache/` 和 `temp/`。
- PDF 缓存不会自动过期，超级管理员可在后台查看占用空间并按文件、报告或全部删除。
- Office 文档在线预览依赖本机 LibreOffice（标准安装目录、`D:\LibreOffice` 免安装目录或
  `LIBREOFFICE_PATH` 环境变量指定的 soffice 均可）；PDF 查看和文件下载不受影响。
- 超级管理员密码保存在数据库中并进行哈希，可在后台“安全设置”中修改。
- 大模型共 4 个（均 OpenAI 兼容协议）：自部署模型（阿里云网关，默认 `http://10.9.50.201:3005/v1` + `glm-5.2`）、
  DeepSeek 内网部署（`DEEPSEEK_INTERNAL_BASE_URL`，密钥可缺省）、MiMo（小米开放平台，`mimo-v2.6-flash`，v2.5 已于 2026-10-21 下线、官方映射为 flash）、
  DeepSeek 官方（`deepseek-chat`）。默认按上述顺序回退；顺序可在门户后台
  “大模型管理”卡片中调整（持久化到数据目录 `llm_settings.json`，即时生效），并支持一键连通性测试。
  智能补全、知识搜索、路演文本识别、路演报告自动匹配统一使用该配置；自部署模型通过
  `chat_template_kwargs.enable_thinking=false`、MiMo 通过 `thinking.type=disabled` 关闭深度思考，
  只取 `content`、不消费 `reasoning_content`，以降低延迟与 token 消耗。
  自部署网关不支持 `response_format`，JSON 输出靠提示词约束并由服务端剥离 Markdown 围栏。
- 知识搜索使用持久化本地向量索引与关键词混合召回；新上传或内容有变化的报告自动增量建索引。首次部署可运行
  `python scripts/rebuild_knowledge_vectors.py --data-root "D:\path\to\juyuan_credit_data"` 完成全量回填。
- 知识问答会先在本地判断任务意图：报告查找和核心观点查询沿用逐篇报告框架，统计、比较、归纳、提纲和推演等任务使用通用工作框架。
- 知识搜索默认覆盖全部报告来源，用户仍可在筛选器中限制报告类型、主题、作者和时间范围。
- 设置 `SELF_LLM_API_KEY`（自部署模型）、`DEEPSEEK_INTERNAL_BASE_URL`（DeepSeek 内网部署）、
  `MIMO_API_KEY`、`DEEPSEEK_API_KEY`（均可选）后即可启用，至少配置一层；
  密钥仅保存在 `.env` 或服务进程环境变量中，不会下发到浏览器。
  已安装的 Windows 服务需要同步更新其 NSSM 环境变量并重启服务。
  管理员可运行 `powershell -ExecutionPolicy Bypass -File scripts/sync_windows_service_deepseek_key.ps1` 同步 DeepSeek 密钥；
  MiMo / 自部署模型 / DeepSeek 内网部署密钥参照该脚本把对应变量追加到服务的 `AppEnvironmentExtra` 后重启服务
  （重新执行 install_windows_service.ps1 或 scripts/install_windows_service_silent.ps1 亦可）。

## 测试环境（隔离于正式系统）

正式系统以 Windows 服务（NSSM，`CreditToolsPortal`，端口 5000）运行于本目录。
新功能先在独立克隆目录测试，验收后再合并回正式目录：

1. `git clone juyuan_credit_tools_portal juyuan_credit_tools_portal_test`（或直接使用既有克隆，切换到功能分支）
2. `scripts\init_test_runtime.bat`：从生产数据目录复制一份快照到 `.test_runtime`（数据库用 sqlite backup API，生产目录只读不受影响）
3. `scripts\start_test_portal.bat`：以端口 5010 启动测试实例（复用正式环境 `.venv`，独立数据目录，站点密码在测试目录 `.env` 中配置）
4. 验收通过后在正式目录 `git merge feature/xxx` 并重启服务；启动时会自动执行数据库迁移（迁移前自动备份到 `migration_backups/`）

账号迁移默认只做预检；确认结果后加 `--apply` 写入。账号迁移只保留账号和超级管理员密码，
不会复制报告、评分或附件。专题要求使用独立命令迁移，仍不会复制任何报告、评分、附件或 PDF 缓存：

```powershell
python scripts/migrate_internal_knowledge_base_accounts.py --source "D:\path\to\data\store.json"
python scripts/migrate_internal_knowledge_base_accounts.py --source "D:\path\to\data\store.json" --apply

# 预检并迁移原系统的专题要求（迁移前自动备份知识库数据库）
python scripts/migrate_internal_knowledge_base_reminders.py --source "D:\path\to\data\store.json"
python scripts/migrate_internal_knowledge_base_reminders.py --source "D:\path\to\data\store.json" --apply
```

## 数据维护

- 后台上传会比较文件内容；内容未变化时不会创建重复备份。
- 内容变化时保留该上传类型最近 3 份历史备份，备份位于 `uploads/`。
- 清理历史冗余前先预览：`python cleanup_redundant_data.py --report logs/data_cleanup_preview.json`。
- 确认清单后执行清理：`python cleanup_redundant_data.py --apply --report logs/data_cleanup_applied.json`。
