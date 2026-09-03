# Wind 数据远程推送部署说明（有 Wind 本机 → 无 Wind 服务器）

## 背景

生产门户将部署在 **http://10.6.8.78:5000/**，该服务器没有 Wind 终端。
门户中唯一依赖 Wind 的模块是 **行业景气高频跟踪（ipm_tracker）**，其余模块
（超长端利率利差、新老券利差、国债地方债发行、一级发行定价、聚源主数据等）
走 Oracle/MySQL，服务器可自行更新。

因此采用「本机拉取 → 推送 → 服务器接收」模式：

```
┌─────────────────────┐         ① preflight：问服务器数据到哪天
│ 本机（有 Wind）      │ ──────► │
│ scripts/push_ipm_wind.py      │         ② 连 Wind 拉增量（复用 ipm_tracker/updater.py）
│ 每日 09:00 定时任务   │ ◄────── │
└─────────┬───────────┘         ③ POST 推送增量 JSON（令牌鉴权）
          │                     ④ 服务器合并进缓存并归档，服务立即生效
┌─────────▼───────────────────────────────┐
│ 服务器 10.6.8.78:5000（无 Wind）          │
│ POST /api/ingest/ipm  （ipm_tracker/ingest.py）│
└─────────────────────────────────────────┘
```

---

## 一、服务器端设置（10.6.8.78）

### 1. 部署本批代码

新代码包含接收接口（`ipm_tracker/ingest.py`，已在 `app.py` 注册），随门户一起部署：

```bash
git pull        # 或按现有部署流程同步代码
```

### 2. 配置推送令牌（必须）

生成一个随机令牌并写入服务端 `.env`（与门户其他配置同一文件）：

```bash
# 生成令牌
python -c "import secrets; print(secrets.token_urlsafe(32))"

# 写入服务器 .env
IPM_INGEST_TOKEN=<上一步生成的令牌>
```

**安全默认**：未配置 `IPM_INGEST_TOKEN` 时接口一律返回 503 停用；
令牌错误返回 401。令牌走请求头 `X-Ingest-Token` 传输。

### 3. 重启服务

按现有方式重启门户进程（waitress/gunicorn/systemd）。验证接口已生效：

```bash
# 无令牌 → 应返回 503（通道未启用 或 令牌无效）
curl http://127.0.0.1:5000/api/ingest/ipm/preflight

# 带令牌 → 应返回 {"status": "ok", "data_latest_date": "...", ...}
curl -H "X-Ingest-Token: <令牌>" http://127.0.0.1:5000/api/ingest/ipm/preflight
```

### 4. 防火墙 / 网络建议

- 5000 端口只对内网（10.x 网段）开放；ingest 接口有令牌保护，但仍建议收敛来源 IP。
- 确认本机能访问 `10.6.8.78:5000`（`curl http://10.6.8.78:5000/ipm-tracker/api/health`）。

### 5. 「一键更新」注意事项

服务器后台（内部研究工作台）的一键更新中，**「行业景气」模块需要 Wind，
在服务器上运行会失败并导致整批任务标记失败**——该模块的数据改由本机推送提供，
服务器上一键更新时**不要勾选行业景气**，其余模块照常勾选。

---

## 二、本机端设置（有 Wind 的机器）

### 1. 配置服务器地址与令牌

写入本机仓库 `.env`（已在 .gitignore 中，不会提交）：

```text
IPM_SERVER_URL=http://10.6.8.78:5000
IPM_INGEST_TOKEN=<与服务端一致的令牌>
```

### 2. 手动验证一次

```bash
# 常规增量（会先连 Wind，请确认 Wind 终端已打开并登录）
.venv\Scripts\python.exe scripts\push_ipm_wind.py

# 首次迁移：服务器还没有完整历史时，先推一次全量缓存
.venv\Scripts\python.exe scripts\push_ipm_wind.py --full
```

推送后打开 http://10.6.8.78:5000/ipm-tracker/ 确认数据已更新。

常用参数：

| 参数 | 作用 |
| --- | --- |
| `--dry-run` | 只连 Wind 生成本机增量文件，不推送 |
| `--days 10` | 强制回看最近 10 天（补数） |
| `--start 2026-08-01` | 指定增量起始日期 |
| `--full` | 推送本机全量缓存整体替换服务端（首次迁移/灾备恢复） |
| `--merge-local` | 推送的同时合并进本机主缓存（默认不动本机缓存） |

### 3. 注册每日定时任务（Windows 任务计划程序）

双击运行 `scripts\register_wind_push_task.bat`，或手动执行：

```bat
schtasks /Create /TN "juyuan_wind_push_daily" /TR "D:\信用债研究\完整网页内容\juyuan_credit_tools_portal\scripts\wind_push_daily.bat" /SC DAILY /ST 09:00 /F
```

- 默认 09:00 执行（推送上一交易日已发布数据；如需收盘当天推送改 /ST 17:30），改 `/ST` 可调整时间。
- 日志写入本机仓库 `logs\wind_push.log`。

### 4. 无人值守运行的前提

Wind 终端需要登录后才能取数，定时任务能稳定跑起来需满足：

1. **本机保持登录状态**（任务计划程序默认在当前用户会话内运行）；
2. **Wind 终端随系统自启并勾选自动登录**（Wind 终端设置内有“开机自启动/自动登录”选项）；
3. 可选：在 `.env` 中设置 `WIND_TERMINAL_EXE=C:\Wind\...\Wind终端.exe`，
   脚本会在连接前检测并自动拉起终端（需 `pip install psutil` 以检测进程，可选）。

脚本对 Wind 未就绪有重试等待（默认 20 次 × 20 秒，可用 `--max-retries/--interval` 调整）。

---

## 三、脚本行为细节

- **增量区间以服务端为准**：起始日 = 服务端 `data_latest_date + 1`（服务端是
  权威数据源，避免两端历史漂移）。服务端已是最新时直接跳过，任务幂等。
- **同口径合并**：增量中同日期新值覆盖旧值、空值不覆盖已有历史值，与
  `ipm_tracker/updater.py` 及旧网页上传完全一致；重复推送同一文件无害。
- **增量归档**：服务端把推送的增量文件按日期归档到运行数据目录
  `ipm_tracker/data/YYYYMMDD.json`，本机也留档一份。
- **全量模式**：`{"indicators": {...}}`（无 date 字段）会被识别为全量替换。
- **失败处理**：Wind 拉取全失败或推送重试 3 次均失败时退出码为 1 并写入日志；
  次日任务会自动从服务端缺口处续推，不会丢数据。

## 四、故障排查

| 现象 | 原因与处理 |
| --- | --- |
| 退出码 1，日志含“Wind 连接超时” | Wind 终端未启动/未登录，见「无人值守运行的前提」 |
| HTTP 503 | 服务端未配置 `IPM_INGEST_TOKEN`，见服务器设置第 2 步 |
| HTTP 401 | 两端令牌不一致，核对 `.env` |
| 连接拒绝/超时（HTTP 层） | 服务器未启动、端口/防火墙未放行，先 `curl http://10.6.8.78:5000/ipm-tracker/api/health` |
| 页面数据未更新 | 看 `/ipm-tracker/api/data/status` 的 `data_latest_date`；必要时本机 `--days 7` 重推 |

本机日志：`logs\wind_push.log`；服务器日志：门户进程日志（含 `ipm_ingest` 记录）。

## 五、相关文件

| 文件 | 位置 | 作用 |
| --- | --- | --- |
| `scripts/push_ipm_wind.py` | 本机 | 定时推送脚本（拉 Wind + 推送） |
| `scripts/wind_push_daily.bat` | 本机 | 任务计划程序入口（重定向日志） |
| `scripts/register_wind_push_task.bat` | 本机 | 一键注册每日 09:00 定时任务 |
| `ipm_tracker/ingest.py` | 服务器 | 令牌鉴权接收接口（preflight + 推送） |
| `test_ipm_ingest.py` | 仓库 | 接口单元测试 |
