"use client";

import { useMemo } from "react";
import { ChevronLeft, ChevronRight, Flame, CalendarClock } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

// 热力主色（rose）
const HEAT_RGB = "225, 29, 72";
const WEEK_LABELS = ["一", "二", "三", "四", "五", "六", "日"];

export interface DayAgg {
  date: string; // YYYY-MM-DD
  count: number; // 发行只数
  planYi: number; // 计划发行规模（亿元）
}

export function MonthlyHeatCalendar({
  days,
  date,
  onSelect,
  onShiftMonth,
}: {
  days: DayAgg[];
  /** 当前选中日期 YYYY-MM-DD（决定展示月份与高亮格） */
  date: string;
  onSelect: (d: string) => void;
  /** 上/下月切换：dir=-1 上月，1 下月 */
  onShiftMonth: (dir: -1 | 1) => void;
}) {
  const ym = date.slice(0, 7);
  const [y, m] = [Number(ym.slice(0, 4)), Number(ym.slice(5, 7)) - 1];

  const dayMap = useMemo(() => {
    const map = new Map<string, DayAgg>();
    for (const d of days) if (d.date.startsWith(ym)) map.set(d.date, d);
    return map;
  }, [days, ym]);

  const { maxYi, monthTotal, monthCount, tradeDays } = useMemo(() => {
    let maxYi = 0;
    let monthTotal = 0;
    let monthCount = 0;
    for (const d of dayMap.values()) {
      if (d.planYi > maxYi) maxYi = d.planYi;
      monthTotal += d.planYi;
      monthCount += d.count;
    }
    return { maxYi, monthTotal, monthCount, tradeDays: dayMap.size };
  }, [dayMap]);

  // 生成当月日历格子（周一开头）
  const cells = useMemo(() => {
    const dim = new Date(y, m + 1, 0).getDate();
    const lead = (new Date(y, m, 1).getDay() + 6) % 7; // 周一=0
    const todayStr = new Date().toISOString().slice(0, 10);
    const monthKey = `${y}-${String(m + 1).padStart(2, "0")}`;
    const list: { day: number; agg: DayAgg | null; isToday: boolean }[] = [];
    for (let i = 0; i < lead; i++) list.push({ day: 0, agg: null, isToday: false });
    for (let d = 1; d <= dim; d++) {
      const ds = `${monthKey}-${String(d).padStart(2, "0")}`;
      list.push({ day: d, agg: dayMap.get(ds) ?? null, isToday: ds === todayStr });
    }
    while (list.length % 7 !== 0) list.push({ day: 0, agg: null, isToday: false });
    return list;
  }, [y, m, dayMap]);

  const isCurrentMonth = ym === new Date().toISOString().slice(0, 7);
  const hasData = maxYi > 0;

  return (
    <div className="rounded-xl border bg-background p-4">
      {/* 头部：标题 + 月切换 */}
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Flame className="h-4 w-4 text-rose-500" />
          <p className="text-sm font-semibold">{y}年{m + 1}月 · 发行热力</p>
          <span className="text-xs text-muted-foreground">色块深浅 = 当日计划发行规模</span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="mr-1 hidden text-[11px] text-muted-foreground sm:inline">
            当月合计
            <b className="mx-1 text-foreground tabular-nums">{monthTotal.toFixed(1)}</b>亿元 ·
            <b className="mx-1 text-foreground tabular-nums">{monthCount}</b>只 ·
            <b className="mx-1 text-foreground tabular-nums">{tradeDays}</b>个发行日
          </span>
          <Button size="icon" variant="outline" className="h-7 w-7" onClick={() => onShiftMonth(-1)} aria-label="上一月">
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <Button size="icon" variant="outline" className="h-7 w-7" onClick={() => onShiftMonth(1)} aria-label="下一月">
            <ChevronRight className="h-4 w-4" />
          </Button>
          {!isCurrentMonth && (
            <Button
              variant="outline"
              size="sm"
              className="h-7 px-2 text-xs"
              onClick={() => onSelect(new Date().toISOString().slice(0, 10))}
            >
              <CalendarClock className="mr-1 h-3.5 w-3.5" />
              本月
            </Button>
          )}
        </div>
      </div>

      {/* 星期表头 */}
      <div className="mb-1 grid grid-cols-7 gap-1.5">
        {WEEK_LABELS.map((w) => (
          <div
            key={w}
            className={cn(
              "py-0.5 text-center text-[11px]",
              (w === "六" || w === "日") && "text-rose-500/80"
            )}
          >
            {w}
          </div>
        ))}
      </div>

      {/* 日历格 */}
      <div className="grid grid-cols-7 gap-1.5">
        {cells.map((c, i) => {
          if (!c.agg) return <div key={i} className="min-h-[52px] rounded-lg sm:min-h-[58px]" />;
          const { agg } = c;
          const isSel = agg.date === date;
          const amountYi = agg.planYi;
          const amountLabel = amountYi >= 100 ? Math.round(amountYi).toString() : amountYi.toFixed(1);
          const hasAmt = amountYi > 0;
          const alpha = hasAmt ? 0.12 + 0.7 * Math.pow(amountYi / maxYi, 0.45) : 0;
          const deep = alpha > 0.5;
          return (
            <button
              key={i}
              type="button"
              onClick={() => onSelect(agg.date)}
              title={`${agg.date} · 发行 ${amountLabel} 亿元 · ${agg.count} 只`}
              className={cn(
                "relative flex min-h-[52px] flex-col items-center justify-center gap-0.5 rounded-lg border transition-transform hover:scale-[1.04] sm:min-h-[58px]",
                hasAmt ? "border-transparent" : "border-dashed",
                isSel && "ring-2 ring-rose-500 ring-offset-1 ring-offset-background"
              )}
              style={{
                background: hasAmt ? `rgba(${HEAT_RGB}, ${alpha.toFixed(3)})` : undefined,
              }}
            >
              <span
                className={cn(
                  "absolute left-1 top-0.5 text-[10px] tabular-nums",
                  hasAmt ? (deep ? "text-white/90" : "text-rose-600/90") : "text-muted-foreground/70",
                  c.isToday && "font-bold underline decoration-2 underline-offset-2"
                )}
              >
                {c.day}
              </span>
              {hasAmt ? (
                <>
                  <span className={cn("pt-1 text-[13px] font-bold leading-none tabular-nums", deep ? "text-white" : "text-rose-700")}>
                    {amountLabel}
                  </span>
                  <span className={cn("text-[10px] leading-none tabular-nums", deep ? "text-white/85" : "text-rose-600/80")}>
                    {agg.count}只
                  </span>
                </>
              ) : (
                <span className="pt-1 text-[11px] text-muted-foreground/60">—</span>
              )}
              {isSel && hasAmt && <span className="absolute bottom-0.5 h-1 w-4 rounded-full bg-white/90" />}
            </button>
          );
        })}
      </div>

      {/* 图例 */}
      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
        <span className="flex items-center gap-1.5">
          <span className="text-rose-600/70">低</span>
          <span
            className="inline-block h-2 w-20 rounded-full"
            style={{ background: `linear-gradient(to right, rgba(${HEAT_RGB},0.1), rgba(${HEAT_RGB},0.85))` }}
          />
          <span className="text-rose-600/90">高</span>
          {hasData && <span>峰值 {maxYi >= 100 ? Math.round(maxYi) : maxYi.toFixed(1)} 亿</span>}
        </span>
        <span className="hidden sm:inline">点击日期查看当日清单 · 圈出格为当前选中日</span>
      </div>
    </div>
  );
}
