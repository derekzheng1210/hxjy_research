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
# 启用知识搜索：从 DeepSeek 控制台获取密钥后设置；Windows 服务安装脚本会写入服务进程环境。
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

## 内部知识库

门户内置独立的管理型子系统，入口为 `/internal-knowledge-base/`，超级管理员后台为
`/internal-knowledge-base/admin`。进入子系统前需要先通过门户访问密码，再使用个人账号登录。

- 账号、报告、评分、系统配置和审计记录保存在
  `PORTAL_DATA_ROOT/internal_knowledge_base/knowledge_base.db`。
- 上传文件、永久 PDF 缓存和转换临时文件分别保存在该目录的 `uploads/`、`pdf_cache/` 和 `temp/`。
- PDF 缓存不会自动过期，超级管理员可在后台查看占用空间并按文件、报告或全部删除。
- Office 文档在线预览依赖本机 LibreOffice；PDF 查看和文件下载不受影响。
- 超级管理员密码保存在数据库中并进行哈希，可在后台“安全设置”中修改。
- 知识搜索调用 DeepSeek，设置 `DEEPSEEK_API_KEY` 后即可启用；密钥仅保存在 `.env` 或服务进程环境变量中，不会下发到浏览器。已安装的 Windows 服务需要同步更新其 NSSM 环境变量并重启服务。
  管理员可运行 `powershell -ExecutionPolicy Bypass -File scripts/sync_windows_service_deepseek_key.ps1` 完成同步。

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
