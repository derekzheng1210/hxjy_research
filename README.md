# 内部研究平台部署包

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

## 本地运行

```powershell
pip install -r requirements.txt
$env:SITE_PASSWORD="your-password"
$env:ADMIN_PASSWORD="your-admin-password"
$env:SECRET_KEY="change-this-secret"
# 大模型：默认 MiMo（小米开放平台，OpenAI 兼容），DeepSeek 兜底；Windows 服务安装脚本会写入服务进程环境。
$env:MIMO_API_KEY="sk-..."
$env:DEEPSEEK_API_KEY="sk-..."
# 可选：数据不在项目同级时设置
# $env:PORTAL_DATA_ROOT="D:\信用债研究\完整网页内容\juyuan_credit_data"
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

- 择券工具：后台上传 Excel。
- 策略仪表盘：本地运行 Wind 脚本生成 HTML 后上传。
- 利差监控：本地生成 `spread_data.js` 后上传。
- 行业景气度：后台上传静态 HTML。
- 信用债两倍标准差：后台上传页面 HTML 和 `spread_data.js`。
- 机构行为监测：实时代理机构行为上游数据；国债/国开债 1-30 年期曲线由后台“一键更新”的“机构行为曲线”模块增量维护。
- 曲线映射表固定随代码发布，位于 `config/映射表.xlsx`。

## 独立信用债交易择券版本

独立副本默认仅监听本机 `127.0.0.1:5011`，并使用同级目录 `juyuan_credit_bond_trading_data`。首次运行执行
`init_bond_trading.ps1`，之后双击 `start_bond_trading.bat`。网页窗口保持运行时，内置调度器会在
工作日 08:30 更新择券估值，并在 09:30 至 16:00 的约定时点更新 DM 最优报价。

经纪商抓取凭据保存在 Git 排除的 `broker_market/dm_client_local.py`，不得提交或对外分发。
失败的抓取不会覆盖最近成功快照。门户中拆分为两个独立一级入口：

- `/secondary-bond-picker`：二级择券工具，左侧筛选、右侧行情与推荐名单；自动剔除偏离中债估值达到 30BP 的异常单边报价。
- `/bond-picker`：收益率倒挂挖掘工具，仅保留同主体倒挂与凸点分析。

二级择券工具按 DM 的 11 个日内计划时点保存挂盘情绪。情绪定义为所有清洗后有效双边债券的
`[(Bid + Ofr) / 2 - 中债估值] × 100` 等权平均（BP），历史默认保留最近 60 个交易日，页面可查看
1D、2D、3D、4D、5D、10D 分时变化及隐含评级、参考内评和期限细分。

后台上传统一 Excel 时会同步解析 `neiping` 的“融资主体”和“最新可用对手限额”。推荐债券除评级、
期限、估值、卖量和相对情绪规则外，还要求主体最新可用对手限额严格大于 1；页面债券表支持点击
各数据表头升降序排列，空值始终排在末尾。

推荐债券另须满足合规 630 跟踪评级要求：查询日在 6 月 30 日之后时，除当年新发债外须有当年
1 月 1 日—6 月 30 日的主体跟踪评级；查询日在 6 月 30 日（含）之前时，除当年及去年新发债外
须有去年 1—6 月或当年年初至查询日的主体跟踪评级；债券本身有债项评级时须主体与债项均满足。
评级事件每日随估值任务从聚源 `TQ_BD_CREDITRATE` 与 `TQ_BD_CREDITRATEINFO` 双表合并抓取，
判定结果随事实一并缓存（仅保留一版，每日更新与后台上传统一 Excel 时整体覆盖；缓存跨日未
刷新时按事实现算兜底）。后台上传页会展示缓存更新时间、判定基准日与满足/不满足/待确认的
数量分布。未满足或未校验（待确认）的债券一律不进入推荐名单，且简称后以红点提示
“可能不满足合规630评级要求，投前请务必确认”；缓存缺失或有待确认债券时，页面状态条
会显示告警。

## 内部知识库

门户内置独立的管理型子系统，入口为 `/internal-knowledge-base/`，超级管理员后台为
`/internal-knowledge-base/admin`。进入子系统前需要先通过门户访问密码，再使用个人账号登录。

- 账号、报告、评分、系统配置和审计记录保存在
  `PORTAL_DATA_ROOT/internal_knowledge_base/knowledge_base.db`。
- 上传文件、永久 PDF 缓存和转换临时文件分别保存在该目录的 `uploads/`、`pdf_cache/` 和 `temp/`。
- PDF 缓存不会自动过期，超级管理员可在后台查看占用空间并按文件、报告或全部删除。
- Office 文档在线预览依赖本机 LibreOffice；PDF 查看和文件下载不受影响。
- 超级管理员密码保存在数据库中并进行哈希，可在后台“安全设置”中修改。
- 大模型默认调用 MiMo（小米开放平台 `https://platform.xiaomimimo.com`，模型 `mimo-v2.5`，OpenAI 兼容协议），失败自动回退 DeepSeek。
  智能补全、知识搜索、路演文本识别统一使用该配置；MiMo 调用统一关闭深度思考（`thinking.type=disabled`）以降低延迟。
- 知识搜索使用持久化本地向量索引与关键词混合召回；新上传或内容有变化的报告自动增量建索引。首次部署可运行
  `python scripts/rebuild_knowledge_vectors.py --data-root "D:\path\to\juyuan_credit_data"` 完成全量回填。
- 知识问答会先在本地判断任务意图：报告查找和核心观点查询沿用逐篇报告框架，统计、比较、归纳、提纲和推演等任务使用通用工作框架。
- 知识搜索默认覆盖全部报告来源，用户仍可在筛选器中限制报告类型、主题、作者和时间范围。
- 设置 `MIMO_API_KEY`（必需）与 `DEEPSEEK_API_KEY`（兜底，可选）后即可启用；密钥仅保存在 `.env` 或服务进程环境变量中，不会下发到浏览器。
  已安装的 Windows 服务需要同步更新其 NSSM 环境变量并重启服务。
  管理员可运行 `powershell -ExecutionPolicy Bypass -File scripts/sync_windows_service_deepseek_key.ps1` 同步 DeepSeek 密钥；
  MiMo 密钥参照该脚本把 `MIMO_API_KEY` 追加到服务的 `AppEnvironmentExtra` 后重启服务。

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
