"use client";

import { apiFetch } from "@/lib/base-path";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import {
  CalendarDays,
  ClipboardList,
  Trophy,
  BellRing,
  PenLine,
  TrendingUp,
  ArrowRight,
  CircleDollarSign,
  Percent,
  Layers,
  FileStack,
  Star,
  RefreshCw,
  CalendarClock,
} from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { PageHeader } from "@/components/page-header";
import { StatCard } from "@/components/stat-card";
import { LoadingState, ErrorState } from "@/components/status-state";
import { Button } from "@/components/ui/button";
import { MonthlyHeatCalendar, type DayAgg } from "@/components/month-heatmap";
import { bondTypeShort, fmtDateShort, today } from "@/lib/format";
import type { ExcelBond, ExcelCalendarDay } from "@/lib/types";

const TYPE_COLORS = ["#3b82f6", "#10b981", "#8b5cf6", "#f59e0b", "#ef4444", "#06b6d4", "#84cc16", "#f97316", "#64748b"];

const QUICK_LINKS = [
  { href: "/daily", label: "每日一级发行", desc: "查看当日/任意日发行计划", icon: CalendarDays, accent: "blue" },
  { href: "/recommended", label: "推荐个券", desc: "每日发行 Excel 推荐个券汇总", icon: Star, accent: "amber" },
  { href: "/results", label: "历史发行情况", desc: "发行结果与定价分析", icon: ClipboardList, accent: "green" },
  { href: "/participated", label: "参与个券", desc: "记录与跟踪参与投标", icon: PenLine, accent: "violet" },
  { href: "/won", label: "中标个券", desc: "中标记录与汇总", icon: Trophy, accent: "red" },
  { href: "/listing", label: "中标上市提醒", desc: "上市日倒计时提醒", icon: BellRing, accent: "blue" },
  { href: "/yield-curve", label: "收益率曲线", desc: "中债曲线与定价参考", icon: TrendingUp, accent: "green" },
] as const;

/** 期限分桶（兼容 0.31Y / 3+2 / 30Y / 永续） */
function tenorBucket(tenor: string | null | undefined): string {
  if (!tenor) return "未知";
  const s = String(tenor);
  if (/N/i.test(s) || s.includes("永续")) return "永续";
  const m = s.match(/([\d.]+)\s*(Y|D)?/);
  if (!m) return "未知";
  const y = m[2] === "D" ? parseFloat(m[1]) / 365 : parseFloat(m[1]);
  if (y < 1) return "<1Y";
  if (y < 3) return "1-3Y";
  if (y < 5) return "3-5Y";
  if (y < 10) return "5-10Y";
  return ">=10Y";
}

function ymOf(date: string) {
  return date.slice(0, 7);
}
function shiftMonth(date: string, dir: -1 | 1): string {
  const d = new Date(date + "T12:00:00");
  const day = d.getDate();
  d.setDate(1);
  d.setMonth(d.getMonth() + dir);
  const dim = new Date(d.getFullYear(), d.getMonth() + 1, 0).getDate();
  d.setDate(Math.min(day, dim));
  return d.toISOString().slice(0, 10);
}

export default function HomePage() {
  const [date, setDate] = useState(today());
  const [summary, setSummary] = useState<{ key: string; days: DayAgg[]; generated: string } | null>(null);
  const [summaryKey, setSummaryKey] = useState("");
  const [loadingSummary, setLoadingSummary] = useState(false);
  const [error, setError] = useState("");
  const [detail, setDetail] = useState<{ date: string; day: ExcelCalendarDay | null } | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const seqRef = useRef(0);
  const initAuto = useRef(false);

  const ym = ymOf(date);

  // ===== 加载整月汇总（Excel 清单口径）=====
  const loadSummary = useCallback(async (key: string) => {
    const seq = ++seqRef.current;
    setError("");
    setLoadingSummary(true);
    try {
      const res = await apiFetch(`/api/excel-calendar?month=${key}`);
      if (!res.ok) {
        const j = await res.json().catch(() => ({}));
        throw new Error(j.error || "加载失败");
      }
      const j = await res.json();
      if (seq !== seqRef.current) return;
      setSummary({ key, days: j.days ?? [], generated: j.generated ?? "" });
      setSummaryKey(key);
    } catch (e) {
      if (seq !== seqRef.current) return;
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      if (seq === seqRef.current) setLoadingSummary(false);
    }
  }, []);

  useEffect(() => {
    if (summaryKey === ym) return;
    let cancelled = false;
    (async () => {
      const seq = ++seqRef.current;
      setError("");
      setLoadingSummary(true);
      try {
        const res = await apiFetch(`/api/excel-calendar?month=${ym}`);
        if (!res.ok) {
          const j = await res.json().catch(() => ({}));
          throw new Error(j.error || "加载失败");
        }
        const j = await res.json();
        if (cancelled || seq !== seqRef.current) return;
        setSummary({ key: ym, days: j.days ?? [], generated: j.generated ?? "" });
        setSummaryKey(ym);
      } catch (e) {
        if (cancelled || seq !== seqRef.current) return;
        setError(e instanceof Error ? e.message : "加载失败");
      } finally {
        if (!cancelled && seq === seqRef.current) setLoadingSummary(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ym]);

  // 初始自动落到当月最近一个有清单的日期
  useEffect(() => {
    if (initAuto.current || !summary || summary.key !== ym) return;
    initAuto.current = true;
    const has = summary.days.some((d) => d.date === date);
    if (!has && summary.days.length) {
      const pick = summary.days.filter((d) => d.date <= date).pop() ?? summary.days[0];
      Promise.resolve().then(() => setDate(pick.date));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [summary, ym]);

  // ===== 加载选中日完整清单 =====
  const loadDetail = useCallback(async (d: string) => {
    const seq = ++seqRef.current;
    setError("");
    setLoadingDetail(true);
    try {
      const res = await apiFetch(`/api/excel-calendar?date=${d}`);
      if (!res.ok) {
        const j = await res.json().catch(() => ({}));
        throw new Error(j.error || "加载失败");
      }
      const j = await res.json();
      if (seq !== seqRef.current) return;
      setDetail({ date: d, day: j.found ? (j.day as ExcelCalendarDay) : null });
    } catch (e) {
      if (seq !== seqRef.current) return;
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      if (seq === seqRef.current) setLoadingDetail(false);
    }
  }, []);

  // 当月已同步的日期：直接读取汇总即可，无需再请求
  const dayAvailable = useMemo(() => {
    const s = summary;
    if (!s) return null;
    return s.days.find((d) => d.date === date) ?? null;
  }, [summary, date]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (dayAvailable === null) {
        if (cancelled) return;
        if (summary && summary.key === ym) setDetail({ date, day: null });
        else setDetail(null);
        return;
      }
      if (!detail || detail.date !== date) loadDetail(date);
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dayAvailable, date, ym]);

  const reloadAll = () => {
    if (summaryKey !== ym) {
      loadSummary(ym);
    } else if (summary) {
      // 重新拉月份汇总 + 当日明细
      const seq = ++seqRef.current;
      setLoadingSummary(true);
      setError("");
      apiFetch(`/api/excel-calendar?month=${ym}`)
        .then((r) => r.json())
        .then((j) => {
          if (seq !== seqRef.current) return;
          setSummary({ key: ym, days: j.days ?? [], generated: j.generated ?? "" });
          setSummaryKey(ym);
          setLoadingSummary(false);
        })
        .catch((e) => {
          if (seq !== seqRef.current) return;
          setError(e instanceof Error ? e.message : "刷新失败");
          setLoadingSummary(false);
        });
    }
    const has = summary?.days.some((d) => d.date === date);
    if (has) loadDetail(date);
  };

  const bonds: ExcelBond[] = useMemo(() => detail?.day?.bonds ?? [], [detail]);
  const planYi = detail?.day?.planYi ?? dayAvailable?.planYi ?? null;
  const count = detail?.day?.count ?? dayAvailable?.count ?? null;

  const stats = useMemo(() => {
    const rec = bonds.filter((b) => b.recommended).length;
    const priced = bonds.filter((b) => b.coupon !== null && b.coupon !== undefined).length;
    const hasPay = bonds.filter((b) => b.payDate).length;
    return { rec, priced, hasPay, avgYi: bonds.length ? (planYi ?? 0) / bonds.length : null };
  }, [bonds, planYi]);

  const typeDist = useMemo(() => {
    const map = new Map<string, number>();
    for (const b of bonds) {
      const t = bondTypeShort(b.type);
      map.set(t, (map.get(t) ?? 0) + (b.amountYi || 0));
    }
    return [...map.entries()].map(([name, value]) => ({ name, value: Math.round(value * 10) / 10 })).sort((a, b) => b.value - a.value);
  }, [bonds]);

  const termDist = useMemo(() => {
    const buckets = ["<1Y", "1-3Y", "3-5Y", "5-10Y", ">=10Y", "永续"];
    const map = new Map<string, number>();
    for (const b of bonds) {
      const bk = tenorBucket(b.tenor);
      map.set(bk, (map.get(bk) ?? 0) + (b.amountYi || 0));
    }
    return buckets.filter((bk) => map.has(bk)).map((bk) => ({ name: bk, value: Math.round((map.get(bk) ?? 0) * 10) / 10 }));
  }, [bonds]);

  return (
    <>
      <PageHeader
        title="总览仪表盘"
        description={`一级信用债发行概览 · ${fmtDateShort(date)}`}
      >
        <div className="flex items-center gap-1.5">
          <input
            type="date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
            className="h-9 rounded-md border bg-background px-3 text-sm"
          />
          <Button size="icon" variant="ghost" className="h-9 w-9" onClick={reloadAll} aria-label="刷新">
            <RefreshCw className="h-4 w-4" />
          </Button>
        </div>
      </PageHeader>

      {error && <ErrorState message={error} onRetry={reloadAll} />}

      {loadingSummary && !summary && !error && <LoadingState label="正在加载当月发行清单…" />}

      {summary && (
        <>
          {/* 月度发行热力日历（Excel 清单口径） */}
          <div className="mb-4">
            <MonthlyHeatCalendar days={summary.days} date={date} onSelect={setDate} onShiftMonth={(dir) => setDate(shiftMonth(date, dir))} />
          </div>

          {loadingDetail && <div className="mb-4 rounded-xl border bg-background p-6 text-center text-sm text-muted-foreground">正在加载当日清单…</div>}

          {!loadingDetail && detail && detail.date === date && !detail.day && (
            <div className="mb-6 rounded-xl border bg-background p-6 text-center text-sm text-muted-foreground">
              <CalendarClock className="mx-auto mb-2 h-5 w-5 opacity-60" />
              {fmtDateShort(date)} 无发行清单（当日非发行日）
            </div>
          )}

          {!loadingDetail && detail && detail.date === date && detail.day && (
            <>
              {/* KPI */}
              <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-5">
                <StatCard label="发行只数" value={count ?? 0} icon={FileStack} accent="blue" sub="当日清单合计" />
                <StatCard label="发行规模" value={`${(planYi ?? 0).toFixed(2)} 亿`} icon={CircleDollarSign} accent="green" />
                <StatCard label="推荐个券" value={stats.rec} icon={Star} accent="amber" sub="清单中标★" />
                <StatCard label="已定价只数" value={stats.priced} icon={Percent} accent="violet" sub="含票面利率" />
                <StatCard label="平均单券规模" value={stats.avgYi ? stats.avgYi.toFixed(1) + " 亿" : "-"} icon={Layers} accent="red" />
              </div>

              {/* 图表区 */}
              <div className="mb-6 grid gap-4 lg:grid-cols-2">
                <div className="rounded-xl border p-4">
                  <p className="mb-3 text-sm font-semibold">按债券类型发行规模（亿元）</p>
                  <ResponsiveContainer width="100%" height={210}>
                    <BarChart data={typeDist}>
                      <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
                      <XAxis dataKey="name" fontSize={11} tickLine={false} axisLine={false} />
                      <YAxis fontSize={11} tickLine={false} axisLine={false} />
                      <Tooltip formatter={(v) => [`${v} 亿`, "发行规模"]} />
                      <Bar dataKey="value" radius={[4, 4, 0, 0]}>
                        {typeDist.map((_, i) => (
                          <Cell key={i} fill={TYPE_COLORS[i % TYPE_COLORS.length]} />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
                <div className="rounded-xl border p-4">
                  <p className="mb-3 text-sm font-semibold">按期限发行规模（亿元）</p>
                  <ResponsiveContainer width="100%" height={210}>
                    <PieChart>
                      <Pie data={termDist} dataKey="value" nameKey="name" cx="50%" cy="50%" innerRadius={52} outerRadius={82} paddingAngle={2}>
                        {termDist.map((_, i) => (
                          <Cell key={i} fill={TYPE_COLORS[i % TYPE_COLORS.length]} />
                        ))}
                      </Pie>
                      <Tooltip formatter={(v) => [`${v} 亿`, "发行规模"]} />
                    </PieChart>
                  </ResponsiveContainer>
                  <div className="mt-2 flex flex-wrap justify-center gap-x-3 gap-y-1">
                    {termDist.map((t, i) => (
                      <span key={t.name} className="flex items-center gap-1 text-[11px] text-muted-foreground">
                        <span className="h-2 w-2 rounded-full" style={{ background: TYPE_COLORS[i % TYPE_COLORS.length] }} />
                        {t.name} {t.value}亿
                      </span>
                    ))}
                  </div>
                </div>
              </div>

              {/* 当日清单明细表已迁至「每日一级发行」板块 */}
              <div className="mb-6 flex flex-wrap items-center justify-between gap-2 rounded-xl border bg-background px-4 py-3">
                <p className="text-sm text-muted-foreground">
                  {fmtDateShort(date)} 发行清单（{bonds.length} 只）已移至「每日一级发行」板块
                </p>
                <Link
                  href="/daily"
                  className="inline-flex h-8 items-center gap-1 rounded-lg border border-border bg-background px-2.5 text-sm font-medium transition-colors hover:bg-muted"
                >
                  前往查看
                  <ArrowRight className="h-3.5 w-3.5" />
                </Link>
              </div>
            </>
          )}

          {/* 快捷入口 */}
          <p className="mb-3 text-sm font-semibold">快捷入口</p>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-7">
            {QUICK_LINKS.map((q) => {
              const Icon = q.icon;
              const accentMap: Record<string, string> = {
                blue: "border-blue-200 bg-blue-50/50 hover:bg-blue-100/60 dark:border-blue-900 dark:bg-blue-950/30 dark:hover:bg-blue-950/50",
                green: "border-emerald-200 bg-emerald-50/50 hover:bg-emerald-100/60 dark:border-emerald-900 dark:bg-emerald-950/30 dark:hover:bg-emerald-950/50",
                amber: "border-amber-200 bg-amber-50/50 hover:bg-amber-100/60 dark:border-amber-900 dark:bg-amber-950/30 dark:hover:bg-amber-950/50",
                violet: "border-violet-200 bg-violet-50/50 hover:bg-violet-100/60 dark:border-violet-900 dark:bg-violet-950/30 dark:hover:bg-violet-950/50",
                red: "border-rose-200 bg-rose-50/50 hover:bg-rose-100/60 dark:border-rose-900 dark:bg-rose-950/30 dark:hover:bg-rose-950/50",
              };
              return (
                <Link
                  key={q.href}
                  href={q.href}
                  className={`group rounded-xl border p-4 transition-colors ${accentMap[q.accent]}`}
                >
                  <Icon className="mb-2 h-5 w-5" />
                  <p className="text-[13px] font-semibold">{q.label}</p>
                  <p className="mt-0.5 line-clamp-2 text-[11px] opacity-70">{q.desc}</p>
                  <ArrowRight className="mt-2 h-3.5 w-3.5 opacity-0 transition-opacity group-hover:opacity-100" />
                </Link>
              );
            })}
          </div>
        </>
      )}
    </>
  );
}
