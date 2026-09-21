# 方案B改造说明（门户反向代理就绪 + 内评接入 + Excel上传 + DM每日自动刷新）

> 本文档面向两类读者：
> ① **原项目开发者（同事）**——如何把本次改造合并进完整项目；
> ② **门户维护者**——如何部署、配置与后续对接 Flask 反向代理。

---

## 一、本次改造总览

| 模块 | 内容 | 关键文件 |
|---|---|---|
| basePath 反代就绪 | 应用整体挂在 `/bond-dashboard` 子路径下，供门户反向代理 | `next.config.ts`、`src/lib/base-path.ts` |
| 主体内评接入 | 信评门户内评数据（3700+ 主体）按发行人全称富化到 8 处表格 | `src/lib/internal-ratings.ts` |
| Excel 网页上传 | 每日发行 Excel + 一级中标登记表，浏览器上传、服务端解析合并 | `src/lib/excel-import.ts`、`src/app/api/excel/route.ts`、`src/components/data-admin.tsx` |
| DM 每日自动刷新 | 服务器内建调度：日历补数 / 个券估值 / 30Y 国债，每日 19:00 自动跑 | `src/lib/dm-refresh.ts`、`src/instrumentation.ts`、`src/app/api/refresh/route.ts` |

git 提交序列（可整体 merge 或按需 cherry-pick）：

```
e5a763b 基线：内网部署包 2026-09-14 原始状态（方案B改造前）
23c77bd 方案B改造：basePath /bond-dashboard + 33处fetch改apiFetch + PWA路径适配 + codemod脚本
14c0ef6 内评接入：internal-ratings库 + 7个API富化 + 8处UI列填充（信评门户portal_data.json，env可配路径）
dd5af0f Excel上传 + DM每日自动刷新：/api/excel导入、/api/refresh、dm-refresh调度器、数据管理面板、instrumentation启动钩子
（后续为构建修复与30Y曲线分段修复，见 git log）
```

---

## 二、basePath 改造细节（反代就绪）

1. **`next.config.ts`** 增加 `basePath: "/bond-dashboard"`（构建期固化，改动需重新 `npm run build`）
2. **`src/lib/base-path.ts`**（新增）：`BASE_PATH` 常量 + `apiUrl()` + `apiFetch()` 包装函数
3. **33 处手写 `fetch("/api/...")` 全部改为 `apiFetch(...)`**：Next 的 basePath 只对 `<Link>`/路由生效，手写 fetch 不自动带前缀
   - 由脚本 `scripts/apply_base_path_prefix.mjs` 批量完成（幂等可重放）
4. **PWA 三件套**：`manifest.ts`（start_url/scope/icons 加前缀）、`pwa-register.tsx`（注册路径）、`public/sw.js`（用 `self.location.pathname` 动态推导前缀，根部署/子路径部署两用；版本号升 v1.1.0 使旧缓存失效）
5. Cookie 已显式 `path: "/"`，无兼容问题；构建产物中已验证 manifest 链接与全部静态资源均带前缀

**验证结果**（2026-09-14，本机 3100 端口实测）：12 个页面全部 200；`/bond-dashboard/api/*` 全部正常；根路径 `/` 404 为预期行为。

---

## 三、主体内评接入

**数据流**：研究门户（juyuan_credit_tools_portal）每日任务抓取信评门户 → 落地 `portal_data.json`（`ratings` 段：主体全称 → 内评级别）→ 看板按环境变量 `INTERNAL_RATINGS_FILE` 指向该文件读取（mtime 缓存，门户更新后自动重读，无需重启看板）。

- 7 个 API 富化：`/api/excel-calendar`、`/api/dm/primary`、`/api/recommended`、`/api/bids`、`/api/issuer/ytd`、`/api/spread/date`、`/api/spread/recommended`，统一注入 `internalRating` 字段
- 8 处 UI 列填充：原项目已预留「主体内评」表头（issue-list-table / bid-list / recommended / results / spread×3 / issuer），本次接上数据
- 规范化与门户 `normalize_rating` 一致（去空格/大写/NA 视为空）；未配置路径或文件缺失时全站显示"-"，优雅降级
- 实测：推荐个券 261/261、中标记录 21/21 全命中；DM 口径约 7-8 成（未命中者为内评未覆盖主体，属正常）

---

## 四、Excel 网页上传（替代原「读本机文件」的导入按钮）

> 原部署包剔除 Excel 上传功能的原因（部署说明第 39 行）：该功能依赖打包者本机 Excel 文件与路径，换机不可用。本次为**真·网页上传**（文件随 HTTP 请求传输），部署到任何机器都可用。

入口：登录后侧边栏头像菜单 / 用户条上的「数据管理」（原「成员管理」旁）。

| 文件 | 合并语义（沿用原 Python 脚本） |
|---|---|
| 每日发行《一级发行-信用债发行 YYYY-MM-DD.xlsx》 | ① `excel_calendar.json` 当日**整日覆盖**（重复上传幂等）② `recommended.json` 仅替换同日「是否推荐=是」条目（PPN 剔除，其他日期不动，保留手工修正）③ `yy_lookup.json` 增量累积 ④ 文件名/工作表名解析日期（微信重传 `(1)` 后缀已容错） |
| 《一级中标登记表.xlsx》 | 按（发行日+债券）分组：参与=Σ投资量、中标=Σ中标量>0；已存在 (bidDate, bondName, type) 记录不重复建，仅回填缺失的票面（仅纯数值）/期限/代码/YY（[1-5]档位） |

实测：真实文件（2026-09-10，46 只/547.78 亿/3 推荐）导入成功，与原快照数据逐项一致。

---

## 五、DM 每日自动刷新

**调度**：`src/instrumentation.ts` 在服务器启动时注册（Next 文件约定），每 10 分钟检查，到达 `DM_REFRESH_AT`（默认 **19:00**，当日发行结果基本落定）且当日未成功刷新时执行；服务中途重启会在下一个检查点补跑。手动触发：数据管理面板「立即刷新」或 `POST /api/refresh`；状态查询：`GET /api/refresh`（读 `data/refresh_state.json`）。

**六步流水**（只做服务器能产出的部分，Excel 上传日不覆盖）：
1. **日历补数**：DM 拉近 10 日一级发行（剔 PPN + 截标日对齐，与 Excel 同口径）→ 只填充日历中**没有 Excel 上传**的交易日（Excel 上传日为权威）；顺带回填 recommended 的实际发行规模/缺失票面（DM 为实际值权威）
2. **参与/中标记录回填**（2026-09-18 新增）：`sync_list_dates.py` 的 TS 移植——缺代码先按简称反查（name_code.json → 投标日 ±10 日 DM 一级发行），再 basic-info 补缴款日（inte_start_date）/上市日（list_date，DM 为准）/发行人/票面；上市提醒板块不再依赖打包者机器的 Python 回填
3. **个券估值**：`fetch_valuations.py` 的 TS 移植——参与/中标 + 推荐个券，basic-info 定场所（每批 20），market-data/date 抓估值（每批 5，当日覆盖 <30% 回退上一工作日），含权一律行权口径
4. **30Y 国债**：DM 曲线**按 ≤12 天分段**拉取（接口跨度限制），增量追加 `gov30y.json`
5. **发行人评级**（2026-09-18 新增，详见第十二节）：issuer_ytd 榜单 ∪ 近 30 日日历主体的「外部评级 + YY评分」每晚 DM 重查 → `data/cache/issuer_ratings_live.json`；`/api/issuer/ytd` 与 `localYyOf()` 读时以 live 优先、快照兜底，评级变动 T+1 生效。手动小批量：`POST /api/refresh?ratingsLimit=N`；服务器首部署可先跑 `node scripts/backfill_ratings_live.mjs` 全量回填
6. **票面曲线**（2026-09-18 新增）：`build_coupon_curve.py` 的 TS 移植——DM 按月窗口拉 YTD 一级发行全量重建 `coupon_curve.json` + `coupon_curve_by_date.json`（口径与 Python 版一致：信用债、剔 PPN/定向/私募/转债/可交换/永续/无票面、行权前期限≤30.5 年、YY 1-5 档、实际发行金额加权）。部署机无 `raw_primary_history.csv` 曲线也能每日更新；`/yield-curve`、`/trends` 页已改 `force-dynamic`，每晚刷新后即时生效无需重新构建

实测（2026-09-18）：曲线区间 2026-01-01~当日（5044 只）、记录回填 14 条缺缴款日中 11 条补齐（余 3 条为 DM 查无此券的误录代码）、估值 149 中债 + 96 中证。

---

## 六、环境变量清单（.env.local，不入版本库）

```
INNO_APP_KEY=            # DM 凭证（必填，实时数据与自动刷新）
INNO_APP_SECRET=
WECHAT_WEBHOOK=          # 企业微信推送（可选）
INTERNAL_RATINGS_FILE=   # 门户 portal_data.json 路径（内评数据；不配则内评列显示"-"）
DM_REFRESH_AT=19:00      # DM 自动刷新时间（可选，默认 19:00）
```

---

## 七、同事如何把改造套用到完整项目

推荐 **git 合并**：本仓库的改造提交独立清晰，merge 进完整项目即可。若个别文件（如被你大改过的页面）冲突：

1. basePath 相关：跑 `node scripts/apply_base_path_prefix.mjs`（幂等），它会把你版本里所有 `fetch("/api/...")` 批量改为 `apiFetch(...)` 并自动加 import
2. 其余为**新增文件**（base-path / internal-ratings / excel-import / dm-refresh / data-admin / instrumentation / api/excel / api/refresh），直接拷贝
3. `next.config.ts` 加 `basePath`、manifest/sw/pwa-register 三处路径微调（对照本仓库对应提交的 diff）

**重要——原项目里的旧「Excel 导入」模块（api/excel、excel-import-button）请删除**：它依赖本机文件路径，部署到服务器会坏；本次的 `/api/excel` 是全新实现。

## 八、数据主权划分（两套管道并存规则）

| 数据 | 权威来源 | 说明 |
|---|---|---|
| 每日发行清单 / 推荐个券 / YY映射 | **Excel 上传**（服务器） | DM 刷新不覆盖上传日 |
| 市场数据（当日发行明细/曲线/上市日/估值） | **DM 实时/每日刷新**（服务器） | 页面实时拉或定时刷 |
| 发行人评级（外部评级 / YY 当前值） | **DM 每日刷新**（服务器 → `cache/issuer_ratings_live.json`） | 2026-09-18 起每晚重查，读时覆盖 issuer_ytd 内嵌快照；快照同步不带此文件，互不覆盖 |
| 投标/中标记录 | **SQLite**（服务器） | 登记表上传合并 + 页面手工录入 |
| 内评 | **门户每日任务**（portal_data.json） | 看板只读 |
| 历史趋势 / 发行人YTD / 组合序列 | **同事机器管道**（pack_snapshot 快照同步） | 服务器暂不重建；同步时请勿覆盖 excel_calendar / recommended / bids / yy_lookup / valuations / gov30y / refresh_state / coupon_curve* |

## 九、门户对接（下一阶段，Flask 反向代理）

在门户 `app.py` 增加通配反代路由，把 `/bond-dashboard/<path>` 转发到 `http://127.0.0.1:3100`（同机部署）：
- 转发方法/请求头/请求体，回传 Set-Cookie 与缓存头（Next 静态资源带 immutable 缓存头，注意透传）
- **上传接口**为 multipart，代理需流式转发请求体
- `page_registry.py` 信用债研究分区加卡片、`portal_nav()` 加菜单
- 看板作为 NSSM Windows 服务部署（门户仓库 `install_windows_service.ps1` 模式），Node ≥ 18
- 鉴权 v1 保持双轨（门户站点密码守门 + 看板自身登录）；后续可做信任头桥接

## 十、已知限制

1. 海报「生成」接口（`/api/poster/generate`）硬编码打包者 Python 路径，本机/服务器不可用（海报列表与下载不受影响）——待同事参数化 `build_daily_poster.py` 后修复
2. 历史趋势 / 发行人 YTD / 组合序列仍为打包快照（数据主权表第 5 行），由同事管道继续同步；票面曲线已于 2026-09-18 起由服务器每晚重建，不再依赖快照
3. `scripts/` 下 Python 脚本仍保留原硬编码路径（同事本机管道用），服务器侧不需要它们
4. 本机测试库 `data/bond.db` 内含打包时的用户/投标数据，部署到服务器前建议清库重置（保留表结构删记录）

## 十一、YY 评分数据链路（2026-09-14 排查结论与修复）

**数据现实**（DM 手册 V2.12 实测验证）：
- YY 评分的**权威来源是团队每日 Excel 的「YY评分」列**（同事的 YY 主表），积累在 `yy_lookup.json`
- DM `company/rating/data` 老端点带 `dataSource:"YY评分"` 的行**只覆盖部分主体**（渤海银行有、山东黄金/中冶等没有）
- 手册 V2.10 新增的 YY 专用端点 `company/rating/yy/com/implied-rating/data`（入参 `comFullNameList`，≤5 个/次）**实测对这批主体返回空**——已验证暂无数据，后续 DM 充实后可接入
- 同事管道同步的 `issuer_ratings.json`（2056 主体/YY 1608）同样不全

**已实施的修复**（`src/lib/ratings.ts`）：
1. **缓存哨兵**：原逻辑把"查过无 YY"的主体永久短路；现改为当日哨兵（`yyQueriedAt`）——当日不重查、次日自动重查，DM 后来补充的 YY 行能被捡起
2. **本地兜底链 `localYyOf()`**：yy_lookup（Excel 归集）→ issuer_ratings（同事同步），mtime 缓存自动跟进文件更新
3. **读时富化**：`/api/excel-calendar` 对缺 YY 的个券按发行人走「本地映射 → DM 评级」补齐；`/api/dm/primary`、DM 每日刷新同步升级

**残余缺口**：当日 DM 补数日里"首次出现 + 不在任何本地映射 + DM 无 YY 行"的主体（如 2026-09-14 的衡阳城投/中冶/山东黄金等 11 只），**只能等当日 Excel 上传后补齐**（上传为覆盖式，YY 列来自同事的 YY 主表）——这是数据源本身的覆盖问题，非代码问题。


## 十二、发行人评级时效修复（2026-09-18：外部评级 / YY 更新不及时）

**现象**：发行人页「外部评级 / YY」两列长期停在快照日（实测 issuer_ytd.json 停在 09-11），评级调升/调降看不到。

**根因**（三个叠加）：
1. `issuer_ytd.json` 由同事机器管道手工重建，服务器"暂不重建"→ 页面评级整表冻结在快照日
2. `sync_issuer_ratings.py` 的补查条件 `if n in yy and n in ext: continue` —— **已有评级的主体永远不进 DM 重查清单**，评级变动拉不到（09-11 那轮 `extDmUpdated=0` 印证）
3. 服务端 `getCompanyRatings` 缓存同样"只补缺、不更新"（有 YY 永久命中）

**修复**（服务端自建评级刷新，不动同事管道）：
1. `src/lib/dm-refresh.ts` 新增第 4 步 `refreshIssuerRatings()`：每晚对 issuer_ytd 榜单 ∪ 近 30 日日历主体（约 2100 家，415 批 × 0.35s 限速 ≈ 5 分钟）重查 DM `company/rating/data`，YY评分 / 外部评级各取 `rating_date` 最新一条，写 **`data/cache/issuer_ratings_live.json`**（独立文件名，pack_snapshot 快照不带，与同事管道的 `issuer_ratings.json` 互不覆盖）
2. `/api/issuer/ytd` 读时富化：`ratingExt/yy` 以 live 优先、issuer_ytd 内嵌快照兜底 → 发行人页评级 T+1 生效
3. `localYyOf()` 兜底链插入 live：yy_lookup（Excel 权威）→ **live（每晚 DM）** → issuer_ratings（快照）→ 日历 / DM 实时页"缺 YY 补齐"也拿到新值
4. 附带修复：`fetchCompanyRatingHistory` 原默认带 `startDate: "2021-01-01"` 实测返回 **0 行**（探针验证），改为不传日期区间（= 近 5 年全量，与 Python 脚本一致）
5. 首部署回填：`node scripts/backfill_ratings_live.mjs`（与 refreshIssuerRatings 同口径的独立 Node 脚本，全量约 5 分钟）

**口径与边界**：
- 外部评级以 DM 最新行为准（与 `sync_issuer_ratings.py` 的"DM 最新覆盖"一致）；YY 在 live 覆盖到的主体上取 DM 最新行，DM 无 YY 行的主体回退 Excel/快照口径（DM YY 行覆盖不全，见第十一节）
- `/api/issuer/ytd` 响应 meta 新增 `ratingsMeta.generated`（live 刷新时间），页面不展示
- `yyAdj`（近五年 YY 调整明细）仍来自同事管道的 `issuer_metrics.json` 快照，服务端不重建；评级"当前值"已由 live 保证时效
