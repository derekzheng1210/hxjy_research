"use client";

import { apiFetch } from "@/lib/base-path";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronLeft,
  ChevronRight,
  RefreshCw,
  CalendarDays,
  CircleDollarSign,
  Percent,
  Layers,
  Star,
  CalendarClock,
} from "lucide-react";
import { PageHeader } from "@/components/page-header";
import { StatCard } from "@/components/stat-card";
import { IssueListTable } from "@/components/issue-list-table";
import { LoadingState, ErrorState } from "@/components/status-state";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { today, fmtDateShort } from "@/lib/format";
import type { ExcelBond, ExcelCalendarDay, ExcelCalendarSummary } from "@/lib/types";

/**
 * 每日一级发行：展示 D 盘《一级发行-信用债发行》每日 Excel 的发行清单。
 * 清单表由总览仪表盘迁入（原 DM 口径「发行情况表」已由本表替代）。
 */
export default function DailyPage() {
  const [date, setDate] = useState(today());
  const [day, setDay] = useState<ExcelCalendarDay | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [tab, setTab] = useState("all");
  const [monthDays, setMonthDays] = useState<ExcelCalendarSummary[]>([]);
  const [monthKey, setMonthKey] = useState("");
  const initAuto = useRef(false);

  const ym = date.slice(0, 7);

  useEffect(() => {
    if (monthKey === ym) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await apiFetch(`/api/excel-calendar?month=${ym}`);
        if (!res.ok) return;
        const j = await res.json();
        if (cancelled) return;
        setMonthDays((j.days ?? []) as ExcelCalendarSummary[]);
        setMonthKey(ym);
      } catch {
        /* 忽略：仅用于日期跳转 */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [ym, monthKey]);

  // ===== 当日清单 =====
  const [reloadTick, setReloadTick] = useState(0);
  const load = useCallback(() => setReloadTick((t) => t + 1), []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setError("");
      setLoading(true);
      try {
        const res = await apiFetch(`/api/excel-calendar?date=${date}`);
        if (!res.ok) {
          const j = await res.json().catch(() => ({}));
          throw new Error(j.error || "加载失败");
        }
        const j = await res.json();
        if (cancelled) return;
        setDay(j.found ? (j.day as ExcelCalendarDay) : null);
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : "加载失败");
        setDay(null);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [date, reloadTick]);

  // 首次进入：若当日无清单，自动落到当月最近一个有清单的日期（在月度数据到达后执行）
  useEffect(() => {
    if (initAuto.current || !monthDays.length || monthKey !== ym) return;
    initAuto.current = true;
    if (!monthDays.some((d) => d.date === date)) {
      const pick = monthDays.filter((d) => d.date <= date).pop() ?? monthDays[0];
      if (pick && pick.date !== date) {
        // 延迟到微任务中更新，避免在 effect 体内同步级联渲染
        Promise.resolve().then(() => setDate(pick.date));
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [monthDays, monthKey, ym]);

  // 按发行日跳转（当月无清单时退化为 ±1 天）
  const shift = (dir: -1 | 1) => {
    if (monthDays.length) {
      const target =
        dir < 0
          ? [...monthDays].reverse().find((d) => d.date < date)?.date
          : monthDays.find((d) => d.date > date)?.date;
      if (target) {
        setDate(target);
        setTab("all");
        return;
      }
    }
    const d = new Date(date + "T12:00:00");
    d.setDate(d.getDate() + dir);
    setDate(d.toISOString().slice(0, 10));
    setTab("all");
  };

  const bonds: ExcelBond[] = useMemo(() => day?.bonds ?? [], [day]);

  const stats = useMemo(() => {
    const rec = bonds.filter((b) => b.recommended).length;
    const priced = bonds.filter((b) => b.coupon !== null && b.coupon !== undefined).length;
    const planYi = day?.planYi ?? bonds.reduce((s, b) => s + (b.amountYi || 0), 0);
    return { rec, priced, planYi, avgYi: bonds.length ? planYi / bonds.length : null };
  }, [bonds, day]);

  const filtered = useMemo(() => {
    if (tab === "rec") return bonds.filter((b) => b.recommended);
    if (tab === "priced") return bonds.filter((b) => b.coupon !== null && b.coupon !== undefined);
    return bonds;
  }, [bonds, tab]);

  return (
    <>
      <PageHeader title="每日一级发行清单" description={`${fmtDateShort(date)} 发行清单`}>
        <div className="flex items-center gap-1.5">
          <Button size="icon" variant="outline" className="h-9 w-9" onClick={() => shift(-1)} aria-label="上一个发行日">
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <input
            type="date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
            className="h-9 rounded-md border bg-background px-3 text-sm tabular-nums"
          />
          <Button size="icon" variant="outline" className="h-9 w-9" onClick={() => shift(1)} aria-label="下一个发行日">
            <ChevronRight className="h-4 w-4" />
          </Button>
          <Button size="icon" variant="ghost" className="h-9 w-9" onClick={() => load()} aria-label="刷新">
            <RefreshCw className="h-4 w-4" />
          </Button>
        </div>
      </PageHeader>

      {error && <ErrorState message={error} onRetry={() => load()} />}
      {loading && !day && !error && <LoadingState label={`正在读取 ${fmtDateShort(date)} 发行清单…`} />}

      {!loading && !error && !day && (
        <div className="rounded-xl border bg-background p-10 text-center text-sm text-muted-foreground">
          <CalendarClock className="mx-auto mb-2 h-5 w-5 opacity-60" />
          {fmtDateShort(date)} 无发行清单（当日非发行日）
        </div>
      )}

      {day && (
        <>
          <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-5">
            <StatCard label="发行只数" value={bonds.length} icon={CalendarDays} accent="blue" sub="当日清单合计" />
            <StatCard label="发行规模" value={`${stats.planYi.toFixed(2)} 亿`} icon={CircleDollarSign} accent="green" />
            <StatCard label="推荐个券" value={stats.rec} icon={Star} accent="amber" sub="清单中标 ★" />
            <StatCard label="已定价只数" value={stats.priced} icon={Percent} accent="violet" sub="含票面利率" />
            <StatCard
              label="平均单券规模"
              value={stats.avgYi ? `${stats.avgYi.toFixed(1)} 亿` : "-"}
              icon={Layers}
              accent="red"
            />
          </div>

          <div className="mb-4">
            <Tabs
              value={tab}
              onValueChange={(v) => setTab(String(v))}
            >
              <TabsList>
                <TabsTrigger value="all">全部（{bonds.length}）</TabsTrigger>
                <TabsTrigger value="rec">推荐（{stats.rec}）</TabsTrigger>
                <TabsTrigger value="priced">已定价（{stats.priced}）</TabsTrigger>
              </TabsList>
            </Tabs>
          </div>

          <IssueListTable
            bonds={filtered}
            title={`${fmtDateShort(date)} 发行清单`}
            file={day.file}
            maxHeight={620}
          />
        </>
      )}
    </>
  );
}
