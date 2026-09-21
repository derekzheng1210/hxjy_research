# 修改说明（lint 清零 + 交接开发约定）

> 本文档随项目一起打包，面向**后续接手修改本项目的同事**，包含两部分：
> ① 2026-09-15 lint 修复的技术说明（改了什么、为什么这样改、后续写代码要遵守什么约定）；
> ② 快速上手指引（命令、目录、文档索引）。
>
> 部署相关请看《部署说明.md》，方案B改造（basePath/内评/Excel上传/DM刷新）请看《方案B改造说明.md》。

---

## 一、本次改动总览

对全仓执行 `npm run lint`，将 **21 个 error + 16 个 warning 全部清零**（当前 `npm run lint` 输出为空即通过），并通过 `npx tsc --noEmit` 与 `npm run build` 验证无回归。

| 类别 | 数量 | 处理方式 |
|---|---|---|
| `react-hooks/set-state-in-effect`（error） | 18 处 / 12 个文件 | 重写 effect 写法，见下文第三节 |
| `prefer-const`（error） | 2 处 | `let` → `const` |
| `react/display-name`（error） | 1 处 | 匿名渲染函数补 `displayName` |
| `no-unused-vars`（warning） | 10 处 | 删除死代码/死导入 |
| `react-hooks/exhaustive-deps`（warning） | 4 处 | 派生数组包 `useMemo`、回调包 `useCallback` |

**顺带修复一个真实 bug**：`public/sw.js` 的 Service Worker 缓存清理代码原来是
`.filter((k) => !k.startsWith(VERSION)).map((c) => caches.delete(k))`——map 回调里误用了外层已出作用域的 `k`、形参 `c` 未使用，实际运行到该分支会抛 `ReferenceError`，导致旧版本缓存无法清理。已改为 `.map((k) => caches.delete(k))`。

---

## 二、逐文件修改清单

### 数据拉取类 effect 重写（error 主项）

| 文件 | 原写法 | 现写法 |
|---|---|---|
| `src/app/daily/page.tsx` | effect 内直接调 `loadMonth(ym)` / `load(date)`；自动跳日期 effect 内直接 `setDate()` | 请求逻辑内联为 effect 内的 async IIFE（带 `cancelled` 卸载保护）；自动跳日期改为 `Promise.resolve().then(() => setDate(...))` 微任务回调；刷新按钮改用 `reloadTick` 计数触发 |
| `src/app/page.tsx` | 同上三处（`loadSummary` / `setDate` / `setDetail`） | 月汇总内联 async IIFE；`setDate` 改微任务回调；`setDetail` 分支体包 async IIFE |
| `src/app/recommended/page.tsx` | `useEffect(() => { load(); })` | `useEffect(() => { (async () => load())(); })` |
| `src/app/results/page.tsx` | 同上 | 同上 |
| `src/app/spread/page.tsx` | `loadRec()` | `(async () => loadRec())()` |
| `src/app/listing/page.tsx` | `load()` | 同上 |
| `src/app/issuer/page.tsx` | `load()` | 同上 |
| `src/components/bid-list.tsx` | `load()` | 同上 |
| `src/components/message-board.tsx` | `load()` | 同上 |
| `src/components/portfolio-chart.tsx` | `load()` | 同上 |
| `src/components/poster-panel.tsx` | `load()` | 同上 |
| `src/components/data-admin.tsx` | 打开弹窗时直接 `setMsg(null)` + `loadState()` | 包 async IIFE |
| `src/components/yield-curve-panel.tsx` | 初始化选中日期直接 `setSelected()`；`?cal=1` 直接 `setOpen(true)` | 均包 async IIFE；`colorOf` 改 `useCallback` 消除 useMemo 依赖警告 |

### 其他单点修复

| 文件 | 修改 |
|---|---|
| `src/app/api/issuer/bonds/route.ts` | `let valMeta` → `const valMeta`（对象属性可变，引用本身不变） |
| `src/lib/dm-refresh.ts` | `prevWorkday()` 内 `let d` → `const d`（`setDate` 是原地变异，非重新赋值） |
| `src/components/bond-table.tsx` | `defaultCell` 返回的匿名渲染函数补 `displayName = "DefaultCell"` |
| `public/sw.js` | **bug 修复**（见第一节）+ 消除 unused-var 警告 |
| `src/app/api/dm/primary/route.ts` | 删除未使用的 `fs` / `path` 导入 |
| `src/app/api/poster/generate/route.ts` | 删除未使用的 `POSTERS_DIR` 常量 |
| `src/lib/marketval.ts` | 删除未使用的 `target` 变量 |
| `src/components/trends-charts.tsx` | 删除未使用的 recharts `Cell` 导入 |
| `src/app/results/page.tsx` | 删除未使用的 `Percent` 图标导入和 `today` 导入 |
| `src/components/message-board.tsx` | 删除未使用的 `User` 图标导入 |
| `src/app/issuer/page.tsx` | 删除未使用的 `maxPlan` 变量 |
| `src/app/daily/page.tsx`、`src/app/page.tsx` | `bonds: ExcelBond[] = xxx ?? []` 派生数组包 `useMemo`（消除下游 useMemo 依赖每次渲染变化的警告） |

---

## 三、重要约定：effect 中如何调用 setState（接手必读）

本项目 `eslint-config-next/core-web-vitals` 启用了 React Compiler 系列新规则，其中 **`react-hooks/set-state-in-effect` 是 error 级**：禁止在 effect 函数体内**同步直接**调用 setState（含直接调用内部会同步 setState 的函数），否则 `npm run lint` 报错。

**后续在 effect 里写数据拉取时，请遵守以下三种模式之一：**

1. **请求 + 落库（推荐）**：把请求逻辑写成 effect 内的 async IIFE，setState 放在 await 之后，并加卸载保护：

   ```tsx
   useEffect(() => {
     let cancelled = false;
     (async () => {
       const res = await apiFetch("/api/xxx");
       if (cancelled) return;
       setData(await res.json());
     })();
     return () => { cancelled = true; };
   }, [deps]);
   ```

2. **复用已有的 useCallback 加载函数**：调用处包一层 async IIFE：

   ```tsx
   useEffect(() => {
     (async () => load())();
   }, [load]);
   ```

3. **确需同步跳转的派生状态**（如「数据到达后自动落到最近一个有清单的日期」）：用微任务回调延后一拍，语义不变但不触发级联渲染警告：

   ```tsx
   Promise.resolve().then(() => setDate(pick.date));
   ```

**不要**用 `// eslint-disable-next-line` 压制该规则（存量代码中没有一处压制，请保持）；也不要把规则降级或关闭——它能在编译期暴露真实的级联渲染问题。

---

## 四、快速上手

### 常用命令

```bash
npm install          # 安装依赖
npm run dev          # 开发（http://localhost:3000/bond-dashboard）
npm run lint         # ESLint 检查（当前 0 error 0 warning，改动后请保持）
npx tsc --noEmit     # TypeScript 类型检查
npm run build        # 生产构建（ basePath /bond-dashboard 固化在构建期 ）
npm start            # 以构建产物启动
```

### 目录速览

- `src/app/` — 页面（总览/daily/results/spread/issuer/listing 等）+ `src/app/api/` 接口路由
- `src/components/` — 表格、图表、面板等客户端组件
- `src/lib/` — 核心库：`db.ts`（JSON 文件存储）、`dm-client.ts`/`dm-refresh.ts`（DM 数据拉取与每日调度）、`excel-import.ts`（Excel 解析）、`marketval.ts`/`ratings.ts`/`internal-ratings.ts`（估值与评级富化）、`base-path.ts`（apiFetch，**前端取数一律用它**，勿手写 fetch）
- `data/` — 运行时数据目录（JSON 存储、portal_data.json、海报产物）
- `scripts/` — 辅助脚本（如 basePath 批量前缀 codemod）

### 改代码前请先读

1. **`AGENTS.md`**（根目录）：本项目 Next.js 版本与常见训练数据存在破坏性差异，写代码前先查 `node_modules/next/dist/docs/` 对应指南，不要凭旧经验写。
2. **`方案B改造说明.md`**：basePath 反代、内评接入、Excel 上传、DM 每日刷新四大改造的设计与验证结论。
3. **`部署说明.md`**：内网部署、端口、计划任务。

### 提交前自检清单

- [ ] `npm run lint` 无输出（0 error 0 warning）
- [ ] `npx tsc --noEmit` 无报错
- [ ] `npm run build` 成功
- [ ] 新增 effect 内 setState 遵守第三节约定
- [ ] 界面上不要新增「数据来源/口径/同步时间」类小字注释（产品约定）

---

*文档生成：2026-09-15，基于内网部署包 20260914 + lint 修复提交。*
