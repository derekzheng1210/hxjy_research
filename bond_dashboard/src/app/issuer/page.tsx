"use client";

import { apiFetch } from "@/lib/base-path";

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Building2,
  Search,
  Layers,
  TrendingUp,
  CalendarRange,
  Trophy,
  Landmark,
  ChevronDown,
} from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { PageHeader } from "@/components/page-header";
import { StatCard } from "@/components/stat-card";
import { LoadingState, ErrorState } from "@/components/status-state";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { SortableTh, useTableSort } from "@/components/table-sort";
import { ratingRank, termSortKey, yyRank } from "@/lib/rating-sort";
import { cn } from "@/lib/utils";

interface IssuerRow {
  issuer: string;
  cnt: number;
  planYi: number;
  actYi: number;
  couponAvg: number | null;
  topType: string | null;
  topMarket: string | null;
  exCount: number;
  province: string;
  ratingExt: string | null;
  yy: string | null;
  internalRating?: string | null; // 主体内评（信评门户数据）
  pledge?: { min: number; max: number; n: number } | null;
  yyAdj?: {
    up: { from: string; to: string; date: string }[];
    down: { from: string; to: string; date: string }[];
    yyNet?: { dir: "up" | "down" | "flat"; steps: number; from: string; to: string; firstDate: string; lastDate: string; moves: number; firstDir?: "up" | "down" | null } | null;
  } | null;
  firstDate: string;
  lastDate: string;
}
interface IssuerBond {
  name: string;
  code: string;
  tenor: string | null;
  type: string;
  planYi: number;
  actYi: number;
  coupon: number | null;
  date: string;
  market: string;
  val?: {
    latest: number | null;
    latestDate: string | null;
    pct: number | null;
    obs: number;
  } | null;
}

/** 发行以来估值分位单元格（配色与 spread 页一致：高位偏红 / 低位偏绿） */
function PctCell({ p }: { p: number | null }) {
  if (p === null) return <span className="text-muted-foreground/60">-</span>;
  return (
    <span className={cn("tabular-nums", p >= 70 ? "text-rose-600 dark:text-rose-400" : p <= 30 ? "text-emerald-600 dark:text-emerald-400" : "")}>
      {p}%
    </span>
  );
}

interface IssuerData {
  meta: { generated: string; range: string; clean: string; issuerCount: number; bondCount: number; planYi: number; actYi: number } | null;
  monthly: { ym: string; cnt: number; planYi: number; actYi: number }[];
  types: { type: string; cnt: number; planYi: number }[];
  markets: { market: string; cnt: number; planYi: number }[];
  issuers: IssuerRow[];
}

const PAGE_SIZE = 150;

const SORT_LABELS: Record<string, string> = {
  planYi: "按计划规模",
  cnt: "按发行只数",
  couponAvg: "按平均票面",
};

export default function IssuerPage() {
  const [data, setData] = useState<IssuerData | null>(null);
  const [error, setError] = useState("");
  const [kw, setKw] = useState("");
  const [sortKey, setSortKey] = useState("planYi");
  const [visible, setVisible] = useState(PAGE_SIZE);
  // 发行人下拉子集：展开后展示该主体今年以来全部个券（期限从长到短）
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [bondCache, setBondCache] = useState<Record<string, IssuerBond[]>>({});
  const [bondLoading, setBondLoading] = useState<Set<string>>(new Set());
  const bondRequested = useRef<Set<string>>(new Set());

  const load = useCallback(async () => {
    setError("");
    try {
      const res = await apiFetch("/api/issuer/ytd");
      if (!res.ok) {
        const j = await res.json().catch(() => ({}));
        throw new Error(j.error || "加载失败");
      }
      setData(await res.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    }
  }, []);

  useEffect(() => {
    (async () => load())();
  }, [load]);

  // 展开 / 收起发行人个券子集（首次展开时按需拉取，结果缓存）
  const toggleIssuer = useCallback((issuer: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(issuer)) next.delete(issuer);
      else next.add(issuer);
      return next;
    });
    if (!bondRequested.current.has(issuer)) {
      bondRequested.current.add(issuer);
      setBondLoading((prev) => new Set(prev).add(issuer));
      apiFetch(`/api/issuer/bonds?name=${encodeURIComponent(issuer)}`)
        .then((r) => r.json())
        .then((j) => setBondCache((c) => ({ ...c, [issuer]: (j.bonds ?? []) as IssuerBond[] })))
        .catch(() => setBondCache((c) => ({ ...c, [issuer]: [] })))
        .finally(() =>
          setBondLoading((prev) => {
            const n = new Set(prev);
            n.delete(issuer);
            return n;
          })
        );
    }
  }, []);

  const filtered = useMemo(() => {
    if (!data) return [];
    const q = kw.trim().toLowerCase();
    let list = data.issuers;
    if (q) {
      list = list.filter(
        (x) =>
          x.issuer.toLowerCase().includes(q) ||
          (x.province || "").toLowerCase().includes(q) ||
          (x.topType || "").toLowerCase().includes(q)
      );
    }
    const sorted = [...list].sort((a, b) => {
      if (sortKey === "cnt") return b.cnt - a.cnt;
      if (sortKey === "couponAvg") return (b.couponAvg ?? -1) - (a.couponAvg ?? -1);
      return b.planYi - a.planYi;
    });
    return sorted;
  }, [data, kw, sortKey]);

  // 表头点击排序（在上方筛选/排序结果基础上叠加；YY/评级按金融评级逻辑）
  const valueFns = useMemo(
    () => ({
      issuer: (r: IssuerRow) => r.issuer,
      cnt: (r: IssuerRow) => r.cnt,
      planYi: (r: IssuerRow) => r.planYi,
      actYi: (r: IssuerRow) => r.actYi,
      couponAvg: (r: IssuerRow) => r.couponAvg,
      topType: (r: IssuerRow) => r.topType,
      topMarket: (r: IssuerRow) => r.topMarket,
      ratingExt: (r: IssuerRow) => ratingRank(r.ratingExt),
      yy: (r: IssuerRow) => yyRank(r.yy),
      internalRating: (r: IssuerRow) => ratingRank(r.internalRating),
      pledge: (r: IssuerRow) => (r.pledge && r.pledge.n > 0 ? (r.pledge.min + r.pledge.max) / 2 : null),
      yyAdj: (r: IssuerRow) => {
        const n = r.yyAdj?.yyNet;
        if (!n || n.moves <= 0) return null;
        return n.dir === "up" ? n.steps : n.dir === "down" ? -n.steps : 0;
      },
      lastDate: (r: IssuerRow) => r.lastDate,
    }),
    []
  );
  const { sorted: ordered, sort, onSort } = useTableSort(filtered, valueFns);

  const monthly = useMemo(
    () =>
      (data?.monthly || []).map((m) => ({
        ...m,
        label: `${Number(m.ym.slice(5))}月`,
      })),
    [data]
  );

  const meta = data?.meta;
  const topTypes = data?.types || [];
  const maxTypePlan = topTypes.length ? Math.max(...topTypes.map((t) => t.planYi)) : 1;
  const markets = data?.markets || [];
  const maxMktPlan = markets.length ? Math.max(...markets.map((m) => m.planYi)) : 1;
  const avgPerIssuer = meta && meta.issuerCount ? meta.planYi / meta.issuerCount : 0;

  const visibleRows = ordered.slice(0, visible);

  return (
    <>
      <PageHeader
        title="发行人分析"
        description="今年以来（YTD）各发行人债券发行总量 · 主体评级 / 质押分析"
      >
        <Button size="sm" variant="outline" onClick={load}>刷新</Button>
      </PageHeader>

      {error && <ErrorState message={error} onRetry={load} />}
      {!data && !error && <LoadingState label="加载发行人分析…" />}

      {data && meta && (
        <>
          {/* KPI */}
          <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-5">
            <StatCard label="发行主体数" value={meta.issuerCount.toLocaleString()} icon={Building2} accent="blue" sub={`共 ${meta.bondCount.toLocaleString()} 只债券`} />
            <StatCard label="YTD 计划规模" value={(meta.planYi / 10000).toFixed(2) + " 万亿"} icon={TrendingUp} accent="green" sub="按截标日归属" />
            <StatCard label="YTD 实际规模" value={(meta.actYi / 10000).toFixed(2) + " 万亿"} icon={TrendingUp} accent="amber" sub="已完成发行口径" />
            <StatCard label="平均单主体" value={avgPerIssuer.toFixed(1) + " 亿"} icon={Layers} accent="violet" sub="按计划规模" />
            <StatCard label="数据截止" value={meta.range.slice(-10)} icon={CalendarRange} accent="red" sub="信用债口径" />
          </div>

          {/* 趋势 + 结构 */}
          <div className="mb-4 grid gap-4 lg:grid-cols-3">
            <div className="rounded-xl border p-4 lg:col-span-2">
              <p className="mb-3 flex items-center gap-1.5 text-sm font-semibold">
                <TrendingUp className="h-4 w-4 text-primary" /> 月度发行节奏（2026 年至今）
              </p>
              <ResponsiveContainer width="100%" height={230}>
                <BarChart data={monthly} margin={{ top: 4, right: 8, bottom: 0, left: -8 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
                  <XAxis dataKey="label" fontSize={12} tickLine={false} axisLine={false} />
                  <YAxis fontSize={11} tickLine={false} axisLine={false} width={52} tickFormatter={(v) => `${Math.round(v)}`} />
                  <Tooltip
                    formatter={(v) => [`${(Number(v) / 10000).toFixed(2)} 万亿`, "计划规模"]}
                    labelFormatter={(l) => `2026年${l}`}
                  />
                  <Bar dataKey="planYi" name="planYi" fill="#3b82f6" radius={[3, 3, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>

            <div className="space-y-4">
              <div className="rounded-xl border p-4">
                <p className="mb-3 flex items-center gap-1.5 text-sm font-semibold">
                  <Layers className="h-4 w-4 text-primary" /> 品种结构（按规模 Top 6）
                </p>
                <div className="space-y-2">
                  {topTypes.slice(0, 6).map((t) => (
                    <div key={t.type}>
                      <div className="mb-0.5 flex items-center justify-between text-xs">
                        <span>{t.type}</span>
                        <span className="tabular-nums text-muted-foreground">
                          {(t.planYi / 10000).toFixed(2)} 万亿 · {t.cnt.toLocaleString()} 只
                        </span>
                      </div>
                      <div className="h-1.5 overflow-hidden rounded-full bg-muted">
                        <div className="h-full rounded-full bg-blue-500" style={{ width: `${(t.planYi / maxTypePlan) * 100}%` }} />
                      </div>
                    </div>
                  ))}
                </div>
              </div>
              <div className="rounded-xl border p-4">
                <p className="mb-3 flex items-center gap-1.5 text-sm font-semibold">
                  <Landmark className="h-4 w-4 text-primary" /> 发行市场分布
                </p>
                <div className="space-y-2">
                  {markets.filter((m) => m.market !== "其他").map((m) => (
                    <div key={m.market}>
                      <div className="mb-0.5 flex items-center justify-between text-xs">
                        <span>{m.market}</span>
                        <span className="tabular-nums text-muted-foreground">
                          {(m.planYi / 10000).toFixed(2)} 万亿 · {m.cnt.toLocaleString()} 只
                        </span>
                      </div>
                      <div className="h-1.5 overflow-hidden rounded-full bg-muted">
                        <div className={cn("h-full rounded-full", m.market === "银行间" ? "bg-emerald-500" : "bg-violet-500")} style={{ width: `${(m.planYi / maxMktPlan) * 100}%` }} />
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>

          {/* 排行表 */}
          <div className="rounded-xl border">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b p-3">
              <p className="flex items-center gap-1.5 text-sm font-semibold">
                <Trophy className="h-4 w-4 text-primary" /> 今年以来发行人发行总量排行
                <span className="ml-1 text-xs font-normal text-muted-foreground">
                  {filtered.length.toLocaleString()} 家主体
                </span>
              </p>
              <div className="flex items-center gap-2">
                <div className="relative">
                  <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    value={kw}
                    onChange={(e) => { setKw(e.target.value); setVisible(PAGE_SIZE); }}
                    placeholder="搜主体 / 省份 / 品种"
                    className="h-8 w-52 pl-8 text-xs"
                  />
                </div>
                <Select value={sortKey} onValueChange={(v) => { setSortKey(v ?? "planYi"); setVisible(PAGE_SIZE); }}>
                  <SelectTrigger className="h-8 w-36 text-xs">
                    <SelectValue>{SORT_LABELS[sortKey] ?? sortKey}</SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="planYi" label="按计划规模">按计划规模</SelectItem>
                    <SelectItem value="cnt" label="按发行只数">按发行只数</SelectItem>
                    <SelectItem value="couponAvg" label="按平均票面">按平均票面</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="max-h-[560px] overflow-auto">
              <table className="w-full text-sm">
                <thead className="sticky top-0 z-10 bg-muted/90 text-left text-xs text-muted-foreground backdrop-blur">
                  <tr>
                    <th className="w-9 px-1 py-2"></th>
                    <th className="px-3 py-2">#</th>
                    <SortableTh sortKey="issuer" sort={sort} onSort={onSort} className="px-3 py-2 min-w-[180px]">发行人</SortableTh>
                    <SortableTh sortKey="cnt" sort={sort} onSort={onSort} className="px-3 py-2 text-right">只数</SortableTh>
                    <SortableTh sortKey="planYi" sort={sort} onSort={onSort} className="px-3 py-2 text-right">计划规模(亿)</SortableTh>
                    <SortableTh sortKey="actYi" sort={sort} onSort={onSort} className="px-3 py-2 text-right">实际(亿)</SortableTh>
                    <SortableTh sortKey="couponAvg" sort={sort} onSort={onSort} className="px-3 py-2 text-right">平均票面%</SortableTh>
                    <SortableTh sortKey="topType" sort={sort} onSort={onSort} className="px-3 py-2 text-center">主力品种</SortableTh>
                    <SortableTh sortKey="topMarket" sort={sort} onSort={onSort} className="px-3 py-2 text-center">市场</SortableTh>
                    <SortableTh sortKey="ratingExt" sort={sort} onSort={onSort} className="px-3 py-2 text-center">外部评级</SortableTh>
                    <SortableTh sortKey="yy" sort={sort} onSort={onSort} className="px-3 py-2 text-center">YY</SortableTh>
                    <SortableTh sortKey="internalRating" sort={sort} onSort={onSort} className="px-3 py-2 text-center">主体内评</SortableTh>
                    <SortableTh sortKey="pledge" sort={sort} onSort={onSort} className="px-3 py-2 text-center">质押比区间</SortableTh>
                    <SortableTh sortKey="yyAdj" sort={sort} onSort={onSort} className="px-3 py-2 text-center">YY调整(近5年)</SortableTh>
                    <SortableTh sortKey="lastDate" sort={sort} onSort={onSort} className="px-3 py-2 text-center">最近发行</SortableTh>
                  </tr>
                </thead>
                <tbody>
                  {visibleRows.map((r, i) => {
                    const rank = i + 1;
                    const isOpen = expanded.has(r.issuer);
                    const bonds = bondCache[r.issuer];
                    return (
                      <Fragment key={r.issuer}>
                        <tr key={r.issuer} className={cn("border-b last:border-0 hover:bg-muted/40", isOpen && "bg-muted/30")}>
                          <td className="px-1 py-2 text-center">
                            <button
                              onClick={() => toggleIssuer(r.issuer)}
                              className="rounded p-0.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                              aria-label={isOpen ? "收起个券明细" : "展开个券明细"}
                              title={isOpen ? "收起个券明细" : "展开今年以来个券明细"}
                            >
                              <ChevronDown className={cn("h-4 w-4 transition-transform", isOpen && "rotate-180")} />
                            </button>
                          </td>
                          <td className="px-3 py-2 text-xs text-muted-foreground tabular-nums">{rank}</td>
                          <td className="px-3 py-2 cursor-pointer" onClick={() => toggleIssuer(r.issuer)}>
                            <div className="font-medium">{r.issuer}</div>
                            <div className="text-[11px] text-muted-foreground">
                              {r.province} · {r.firstDate.slice(5).replace("-", "/")}~{r.lastDate.slice(5).replace("-", "/")}
                            </div>
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums">{r.cnt}</td>
                          <td className="px-3 py-2 text-right tabular-nums font-semibold">
                            {r.planYi.toLocaleString("zh-CN", { maximumFractionDigits: 1 })}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums text-muted-foreground">
                            {r.actYi.toLocaleString("zh-CN", { maximumFractionDigits: 1 })}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums">{r.couponAvg !== null ? r.couponAvg.toFixed(2) : "-"}</td>
                          <td className="px-3 py-2 text-center text-[11px] text-muted-foreground">{r.topType || "-"}</td>
                          <td className="px-3 py-2 text-center">
                            <span className={cn("inline-flex rounded px-1.5 py-0.5 text-[11px] font-medium",
                              r.topMarket === "银行间" ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-400"
                                : r.topMarket === "交易所" ? "bg-violet-50 text-violet-700 dark:bg-violet-950 dark:text-violet-400"
                                  : "bg-muted text-muted-foreground")}>
                              {r.topMarket || "-"}
                            </span>
                          </td>
                          <td className="px-3 py-2 text-center">
                            {r.ratingExt ? (
                              <span className="inline-flex rounded-md border border-amber-300/60 bg-amber-50 px-1.5 py-0.5 text-[11px] font-semibold text-amber-700 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-400">
                                {r.ratingExt}
                              </span>
                            ) : (
                              <span className="text-muted-foreground/40">-</span>
                            )}
                          </td>
                          <td className="px-3 py-2 text-center">
                            {r.yy ? (
                              <span className="inline-flex rounded-md bg-violet-50 px-1.5 py-0.5 text-[11px] font-semibold text-violet-700 dark:bg-violet-950 dark:text-violet-400">
                                {r.yy}
                              </span>
                            ) : (
                              <span className="text-muted-foreground/40">-</span>
                            )}
                          </td>
                          <td className="px-3 py-2 text-center">
                            {r.internalRating ? (
                              <span className="inline-flex rounded-md bg-sky-50 px-1.5 py-0.5 text-[11px] font-semibold text-sky-700 dark:bg-sky-950 dark:text-sky-400">
                                {r.internalRating}
                              </span>
                            ) : (
                              <span className="text-muted-foreground/40">-</span>
                            )}
                          </td>
                          <td className="px-3 py-2 text-center">
                            {r.pledge && r.pledge.n > 0 ? (
                              <span
                                className="inline-flex rounded-md bg-sky-50 px-1.5 py-0.5 text-[11px] font-semibold tabular-nums text-sky-700 dark:bg-sky-950 dark:text-sky-400"
                                title={`该发行人交易所已发债券标准券折算率（${r.pledge.n} 只可质押券）`}
                              >
                                {r.pledge.min === r.pledge.max
                                  ? r.pledge.min.toFixed(2)
                                  : `${r.pledge.min.toFixed(2)}~${r.pledge.max.toFixed(2)}`}
                              </span>
                            ) : (
                              <span className="text-muted-foreground/40">-</span>
                            )}
                          </td>
                          <td className="px-3 py-2 text-center">
                            {r.yyAdj && r.yyAdj.yyNet && r.yyAdj.yyNet.moves > 0 ? (
                              (() => {
                                const net = r.yyAdj.yyNet;
                                const history =
                                  [...r.yyAdj.up.map((u) => `调高 ${u.from}→${u.to}(${u.date.slice(0, 7)})`),
                                   ...r.yyAdj.down.map((d) => `调低 ${d.from}→${d.to}(${d.date.slice(0, 7)})`)].join("、");
                                if (net.dir === "up") {
                                  return (
                                    <span
                                      className="inline-flex items-center gap-0.5 rounded-md bg-red-50 px-1.5 py-0.5 text-[11px] font-semibold text-red-700 dark:bg-red-950 dark:text-red-400"
                                      title={`调优 ${net.from}→${net.to}（近五年净上调 ${net.steps} 档）\n明细：${history}`}
                                    >
                                      调优{net.steps}档
                                    </span>
                                  );
                                }
                                if (net.dir === "down") {
                                  return (
                                    <span
                                      className="inline-flex items-center gap-0.5 rounded-md bg-emerald-50 px-1.5 py-0.5 text-[11px] font-semibold text-emerald-700 dark:bg-emerald-950 dark:text-emerald-400"
                                      title={`调差 ${net.from}→${net.to}（近五年净下调 ${net.steps} 档）\n明细：${history}`}
                                    >
                                      调差{net.steps}档
                                    </span>
                                  );
                                }
                                const flatLabel =
                                  net.firstDir === "up" ? "先优后差" : "先差后优";
                                return (
                                  <span
                                    className="inline-flex items-center gap-0.5 rounded-md bg-muted px-1.5 py-0.5 text-[11px] font-medium text-muted-foreground"
                                    title={`维持（近五年 ${net.from}→${net.to} 净无变化，${flatLabel}）\n明细：${history}`}
                                  >
                                    {flatLabel}
                                  </span>
                                );
                              })()
                            ) : (
                              <span className="font-medium text-foreground">-</span>
                            )}
                          </td>
                          <td className="px-3 py-2 text-center text-xs tabular-nums text-muted-foreground">
                            {r.lastDate.slice(5).replace("-", "/")}
                          </td>
                        </tr>
                        {isOpen && (
                          <tr key={`${r.issuer}-detail`} className="border-b bg-muted/20 last:border-0">
                            <td colSpan={15} className="px-4 py-3">
                              {bondLoading.has(r.issuer) ? (
                                <p className="py-2 text-center text-xs text-muted-foreground">
                                  正在加载 {r.issuer} 个券明细…（首次展开需回补估值序列，约数秒）
                                </p>
                              ) : !bonds || bonds.length === 0 ? (
                                <p className="py-2 text-center text-xs text-muted-foreground">暂无个券明细</p>
                              ) : (
                                <div>
                                  <p className="mb-1.5 text-xs font-medium">
                                    {r.issuer} · 今年以来 {bonds.length} 只
                                    <span className="ml-2 font-normal text-muted-foreground">默认按期限从长到短，点击表头可排序 · 点击券名可复制代码</span>
                                    {bonds.some((b) => b.val?.latestDate) && (
                                      <span className="ml-2 font-normal text-muted-foreground">
                                        估值截至 {bonds.map((b) => b.val?.latestDate).filter(Boolean).sort()!.pop()}
                                      </span>
                                    )}
                                  </p>
                                  <IssuerBondsTable bonds={bonds} />
                                </div>
                              )}
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {filtered.length > visible && (
              <div className="border-t p-2 text-center">
                <Button size="sm" variant="ghost" onClick={() => setVisible((v) => v + PAGE_SIZE)}>
                  加载更多（已显示 {visible} / {filtered.length}）
                </Button>
              </div>
            )}
          </div>
        </>
      )}
    </>
  );
}

/** 发行人个券明细表（展开行内）：默认按期限从长到短，点击表头可排序 */
function IssuerBondsTable({ bonds }: { bonds: IssuerBond[] }) {
  const valueFns = useMemo(
    () => ({
      name: (b: IssuerBond) => b.name,
      code: (b: IssuerBond) => b.code,
      type: (b: IssuerBond) => b.type,
      tenor: (b: IssuerBond) => termSortKey(b.tenor),
      scale: (b: IssuerBond) => (b.actYi > 0 ? b.actYi : b.planYi),
      coupon: (b: IssuerBond) => b.coupon,
      latest: (b: IssuerBond) => b.val?.latest ?? null,
      pct: (b: IssuerBond) => b.val?.pct ?? null,
      market: (b: IssuerBond) => b.market,
      date: (b: IssuerBond) => b.date,
    }),
    []
  );
  const { sorted, sort, onSort } = useTableSort(bonds, valueFns);

  return (
    <div className="max-h-[320px] overflow-auto rounded-lg border bg-background">
      <table className="w-full text-[12px]">
        <thead className="sticky top-0 bg-muted/80 text-left text-muted-foreground">
          <tr>
            <SortableTh sortKey="name" sort={sort} onSort={onSort} className="px-3 py-1.5 font-medium">债券简称</SortableTh>
            <SortableTh sortKey="code" sort={sort} onSort={onSort} className="px-2 py-1.5 font-medium">代码</SortableTh>
            <SortableTh sortKey="type" sort={sort} onSort={onSort} className="px-2 py-1.5 font-medium">类型</SortableTh>
            <SortableTh sortKey="tenor" sort={sort} onSort={onSort} className="px-2 py-1.5 text-right font-medium">期限</SortableTh>
            <SortableTh sortKey="scale" sort={sort} onSort={onSort} className="px-2 py-1.5 text-right font-medium">规模(亿)</SortableTh>
            <SortableTh sortKey="coupon" sort={sort} onSort={onSort} className="px-2 py-1.5 text-right font-medium">票面%</SortableTh>
            <SortableTh sortKey="latest" sort={sort} onSort={onSort} className="px-2 py-1.5 text-right font-medium">最新估值%</SortableTh>
            <SortableTh sortKey="pct" sort={sort} onSort={onSort} className="px-2 py-1.5 text-center font-medium">发行以来分位</SortableTh>
            <SortableTh sortKey="market" sort={sort} onSort={onSort} className="px-2 py-1.5 text-center font-medium">市场</SortableTh>
            <SortableTh sortKey="date" sort={sort} onSort={onSort} className="px-3 py-1.5 text-center font-medium">截标日</SortableTh>
          </tr>
        </thead>
        <tbody>
          {sorted.map((b) => (
            <tr key={b.code || b.name} className="border-b last:border-0 hover:bg-accent/50">
              <td className="px-3 py-1.5 font-medium">{b.name || "-"}</td>
              <td className="px-2 py-1.5">
                <button
                  className="tabular-nums text-muted-foreground hover:text-primary"
                  onClick={() => navigator.clipboard?.writeText(b.code).catch(() => {})}
                  title="点击复制代码"
                >
                  {b.code || "-"}
                </button>
              </td>
              <td className="px-2 py-1.5 text-muted-foreground">{b.type || "-"}</td>
              <td className="px-2 py-1.5 text-right">
                <span className="inline-flex rounded bg-muted px-1.5 py-0.5 text-[11px] font-semibold tabular-nums">
                  {b.tenor || "-"}
                </span>
              </td>
              <td className="px-2 py-1.5 text-right font-medium tabular-nums">
                {(b.actYi > 0 ? b.actYi : b.planYi).toLocaleString("zh-CN", { maximumFractionDigits: 1 })}
              </td>
              <td className="px-2 py-1.5 text-right font-semibold tabular-nums">
                {b.coupon !== null && b.coupon !== undefined ? b.coupon.toFixed(3) : <span className="font-normal text-muted-foreground/60">-</span>}
              </td>
              <td
                className="px-2 py-1.5 text-right tabular-nums"
                title={b.val?.latestDate ? `估值日期 ${b.val.latestDate} · ${b.val.obs} 个观测` : "未上市/无估值"}
              >
                {b.val?.latest !== null && b.val?.latest !== undefined ? (
                  b.val.latest.toFixed(2)
                ) : (
                  <span className="font-normal text-muted-foreground/60">-</span>
                )}
              </td>
              <td className="px-2 py-1.5 text-center">
                <PctCell p={b.val?.pct ?? null} />
              </td>
              <td className="px-2 py-1.5 text-center">
                <span className={cn("inline-flex rounded px-1.5 py-0.5 text-[11px] font-medium",
                  b.market === "银行间" ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-400"
                    : b.market === "交易所" ? "bg-violet-50 text-violet-700 dark:bg-violet-950 dark:text-violet-400"
                      : "bg-muted text-muted-foreground")}>
                  {b.market}
                </span>
              </td>
              <td className="px-3 py-1.5 text-center tabular-nums text-muted-foreground">
                {b.date.slice(5).replace("-", "/")}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
