# 债用债一级投资看板（Bond Primary Dashboard）

一个面向债券一级市场投资经理的本地化看板：通过 **DM (innodealing) API** 实时拉取信用债一级发行数据，结合本地投标记录，帮你一眼掌握发行节奏、定价、上市与团队动态。

---

## ✨ 功能

### 核心栏目（7 个）
| 栏目 | 功能 |
|------|------|
| 📅 **每日一级发行** | 任意日一级发行明细 + KPI + 类型/期限筛选 tabs |
| 📋 **上一工作日结果** | 已定价债券的票面/利差/认购倍数，自动定位上一工作日 |
| ⭐ **推荐个券** | 每日发行 Excel「是否推荐=是」的个券汇总（6/15~8/31，238 只，可筛选日期） |
| ✍️ **参与个券** | 参与投标记录（含从「一级中标登记表」导入的 45 只、98.4 亿） |
| 🏆 **中标个券** | 中标记录（含导入的 20 只、33.0 亿） |
| 🔔 **中标上市提醒** | 自动估算（缴款日+2工作日）/查询 DM 精确上市日，3 天内高亮、已上市标记 |
| 💬 **信息交流栏** | 团队留言板（昵称+内容），JSON 本地存储 |

### 我额外加的 4 个（理由见下）
| 栏目 | 为什么加 |
|------|---------|
| 📊 **总览仪表盘** | 打开看板第一眼：今日只数/规模/已定价/全场倍数/已上市 5 大 KPI + 类型/期限分布 + 快捷入口 |
| 📈 **收益率曲线** | DM 的中债国债曲线（1/3/5/7/10Y）→ 定价参考，1Y/10Y/利差 KPI + 平滑折线图 |
| 📉 **利差分析** | 新债预测 vs 可比券二级估值：超发空间/发飞 TOP5 + 分布柱状图 + 明细表 |
| 🗂️ **历史趋势** | 用你已有的 3.5 年（6.3 万条）历史 CSV 做月度/类型/期限/区域/利率分布图表 |

---

## 🚀 启动

```bash
# 1. 安装依赖（首次）
cd bond-dashboard
npm install

# 2. 启动开发服务器
npm run dev
# 浏览器打开 http://localhost:3000
```

### 密钥配置（必须）

编辑 `.env.local`，填入你的 DM API 凭证（**只在本机使用，不会泄漏到前端**）：

```env
INNO_APP_KEY=你的AppKey
INNO_APP_SECRET=你的AppSecret
```

密钥从 [DM 终端](https://www.innodealing.com) 申请，认证原理：SM4-ECB + base64url 加密 POST body + `X-Dm-App-Key` header（与官方 Python SDK 100% 兼容）。

---

## 🛠 技术栈

- **Next.js 16.3** (App Router) + **React 19** + **TypeScript strict**
- **Tailwind CSS v4** + **shadcn/ui** (Nova 预设，Base UI + Tailwind)
- **Recharts 3**（KPI、柱状、环形、折线、组合图）
- **better-sqlite3 替代方案：JSON 文件存储**（零依赖，够用）
- **sm-crypto**（Node 端 DM 客户端，SM4-ECB 实现）
- **Lucide React** 图标

### 架构亮点

1. **密钥零暴露**：DM 凭证只存在服务端 `.env.local`，客户端通过 `/api/dm/*` Route Handler 间接调用
2. **Node 版 DM 客户端**：完全用 `sm-crypto` 复刻 Python SDK 协议（`X-Dm-App-Key` + base64url(SM4-ECB(JSON))），避免启动 Python 微服务
3. **生产级三态**：每个数据页都实现 loading / error / empty 状态
4. **响应式**：桌面侧边栏 + 移动顶栏，1440px / 平板 / 手机全适配
5. **类型严格**：`strict: true`，所有 props/state 类型明确
6. **可访问性**：语义化标签、`aria-label`、键盘导航

---

## 📂 目录结构

```
bond-dashboard/
├── .env.local                  # DM 密钥（不提交）
├── data/
│   ├── history_trends.json     # 历史趋势统计（预处理好的）
│   └── store/                  # 本地存储
│       ├── bids.json           # 参与/中标记录
│       └── messages.json       # 留言
└── src/
    ├── app/
    │   ├── layout.tsx          # 根布局 + AppShell
    │   ├── page.tsx            # 总览仪表盘
    │   ├── daily/              # 每日一级发行
    │   ├── results/            # 上一工作日结果
    │   ├── participated/       # 参与个券
    │   ├── won/                # 中标个券
    │   ├── listing/            # 中标上市提醒
    │   ├── exchange/           # 信息交流栏
    │   ├── yield-curve/        # 收益率曲线
    │   ├── spread/             # 利差分析
    │   ├── trends/             # 历史趋势
    │   ├── recommended/        # 推荐个券（Excel 导入）
    │   └── api/
    │       ├── dm/primary/     # DM 一级发行代理
    │       ├── dm/yield-curve/ # DM 曲线代理
    │       ├── dm/basic-info/  # DM 基础资料（含上市日）
    │       ├── recommended/    # 推荐个券读取
    │       ├── bids/           # 参与/中标 CRUD
    │       └── messages/       # 留言 CRUD
    ├── components/
    │   ├── app-shell.tsx       # 侧边栏布局
    │   ├── bid-form.tsx        # 参与/中标录入表单
    │   ├── bid-list.tsx        # 记录列表
    │   ├── bond-table.tsx      # 通用债券表格
    │   ├── message-board.tsx   # 留言板
    │   ├── page-header.tsx
    │   ├── stat-card.tsx
    │   ├── status-state.tsx    # loading/error/empty
    │   ├── trends-charts.tsx   # 历史趋势图表
    │   └── ui/                 # shadcn 组件（17 个）
    └── lib/
        ├── dm-client.ts        # Node 版 DM 客户端（SM4 加密）
        ├── store.ts            # JSON 文件存储
        ├── format.ts           # 格式化工具
        ├── types.ts            # 类型定义
        └── utils.ts            # cn
```

---

## 📥 Excel 数据导入

数据来自你 `D:/2026/一级投标/投标情况/` 下的 Excel，导入脚本已固化到项目：

```bash
# 用你本机 Python（需 openpyxl）运行：
C:/Users/yoyo1/.workbuddy/binaries/python/envs/default/Scripts/python.exe scripts/import_excel.py
```

脚本做两件事：
1. 读 `一级发行-信用债发行 2026-06-15 ~ 08-31.xlsx` 的「是否推荐」列 → `data/recommended.json`（推荐个券）
2. 读 `一级中标登记表.xlsx` → `data/store/bids.json`（参与个券 = 全部个券按债券合并投资量；中标个券 = 中标量 > 0 的按债券合并）
   - 同时从每日发行 Excel 匹配缴款日，供「中标上市提醒」使用

## 📊 DM 估值抓取（参与/中标/推荐页面用）

参与/中标/推荐三个页面新增"中债估值"和"中证估值"两列，从 DM `bond/market-data/date`（cb_ytm/cs_ytm）+ `bond/basic-info/info`（secondary_market 发行场所）预取。规则：
- 仅银行间 → 只取中债
- 仅交易所 → 只取中证
- 银行间+交易所（双市场）→ 都取
- 估值日期：上一工作日

```bash
C:/Users/yoyo1/.workbuddy/binaries/python/envs/default/Scripts/python.exe scripts/fetch_valuations.py
# 输出 data/valuations.json（239 只），页面通过 /api/valuations 读取
```

---

## 📝 我额外加的功能及理由

1. **总览仪表盘** — 打开看板第一眼就能看全局（5 个核心 KPI + 图表 + 快捷入口），不需要点来点去
2. **收益率曲线** — 一级投标必须参考二级曲线定价，这个在债券圈是基础设施
3. **利差分析** — 海报里"超发/发飞"你已经在用，看板里做成可筛选/排序的实时表更实用
4. **历史趋势** — 你已经有 3.5 年数据，不利用太可惜，做成趋势图能辅助看市场冷热

---

## 🔌 DM API 集成说明

### 已验证接口
| 接口 | 路径 | 用途 |
|------|------|------|
| 一级发行 | `/bond/primary/data` | 当日/历史发行明细（含票面/倍数/利差/上市日） |
| 基础资料 | `/bond/basic-info/info` | 查债券精确上市日 + 发行场所（secondary_market） |
| 收益率曲线 | `/bond/yield-curve/data` | 中债国债收益率曲线 |
| 日期序列 | `/bond/market-data/date` | 个券中债/中证估值（cb_ytm / cs_ytm），单次最多 5 个代码 |
| 利率债标书 | `/bond/primary/rate-bond-tender-detail` | 利率债投标明细（未用，预留） |
| 余额包销 | `/bond/basic-info/underwriter-balance` | 承销获配（未用，预留） |

### Node 端 DM 客户端验证
- 加密协议（`X-Dm-App-Key` + base64url(SM4-ECB(JSON))）：与官方 Python SDK 输出**完全一致**
- 解密：响应是 base64url 加密串（JSON 字符串值），sm-crypto decrypt 直接返回明文 JSON
- 字段命名：服务端接收 `camelCase`（如 `securityIdList`），返回 `snake_case`（统一前端使用习惯）

---

## 📦 后续可扩展

- Excel 导入（你之前 `D:/2026/一级投标/投标情况/` 的 Excel 解析）
- 桌面端通知（上市前 1 天）
- 移动端 PWA（已可响应式，可加 manifest）
- 团队多用户（目前是单用户 JSON，加登录 + SQLite 即可）
- 海报自动生成（接 `primary-issuance-results-poster` / `bond-daily-poster` 技能）

---

## ⚠️ 免责声明

本看板数据来自 DM 数据库，仅供个人/团队内部参考，不构成投资建议。
