"use client";

import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

export interface BondDetail {
  /** 债券简称 */
  name: string;
  /** 行权前期限（年） */
  termY: number;
  /** 票面利率（%） */
  coupon: number;
  /** 发行量（亿元，实际发行额口径） */
  amtYi: number;
}

export interface CurveBucket {
  term: string;
  termY: number | null;
  count: number;
  amountYi: number;
  coupon: number | null;
  couponMin: number | null;
  couponMax: number | null;
  /** 该档所包含个券明细（按发行量降序），供悬浮浮框展示 */
  bonds?: BondDetail[];
}

export interface CurveSeries {
  /** 日期（快照截止簿记日） */
  date: string;
  color: string;
  data: CurveBucket[];
}

/** 序列配色：recency 0=最新（主色蓝），依次 amber/violet/emerald/red */
export const SERIES_COLORS = ["#3b82f6", "#f59e0b", "#8b5cf6", "#10b981", "#ef4444"];

export function colorForRecency(recency: number): string {
  return SERIES_COLORS[recency % SERIES_COLORS.length];
}

interface TipRow {
  label: string;
  value: string;
  color?: string;
  sub?: boolean;
  detail?: boolean;
}

function TipPanel({ rows }: { rows: TipRow[] }) {
  return (
    <div className="rounded-lg border border-border bg-popover px-3 py-2 text-xs shadow-md">
      {rows.map((r, i) => (
        <div
          key={i}
          className={`flex items-center justify-between gap-6 ${r.detail ? "gap-3 py-0.5" : r.sub ? "py-0" : "py-0.5"}`}
        >
          <span className="flex min-w-0 items-center gap-1.5 text-muted-foreground">
            {r.color && (
              <span
                className="inline-block h-2 w-2 shrink-0 rounded-full"
                style={{ background: r.color }}
              />
            )}
            <span className={r.sub || r.detail ? "text-[10px]" : ""}>{r.label}</span>
          </span>
          <span
            className={`whitespace-nowrap font-semibold tabular-nums ${r.sub || r.detail ? "text-[10px] text-muted-foreground" : ""}`}
          >
            {r.value}
          </span>
        </div>
      ))}
    </div>
  );
}

type ChartRow = Record<string, string | number | null | BondDetail[]>;

/** 将若干日期序列对齐为逐行数据（行=期限档，列=s0..sn-1，附 _nX/_aX 只数与金额、_bX 个券明细） */
export function buildChartRows(series: CurveSeries[]): ChartRow[] {
  const terms = series[0]?.data.map((b) => b.term) ?? [];
  const rows: ChartRow[] = terms.map((term) => ({ term }));
  for (const b of series[0]?.data ?? []) {
    const row = rows[terms.indexOf(b.term)];
    if (row) row.termY = b.termY;
  }
  series.forEach((s, j) => {
    s.data.forEach((b) => {
      const row = rows[terms.indexOf(b.term)];
      if (!row) return;
      row[`s${j}`] = b.coupon;
      row[`_n${j}`] = b.count;
      row[`_a${j}`] = b.amountYi;
      row[`_b${j}`] = b.bonds ?? [];
    });
  });
  return rows;
}

export function CouponCurveChart({ series }: { series: CurveSeries[] }) {
  const data = buildChartRows(series);
  const ticks = series[0]?.data.map((b) => b.term) ?? [];
  const ys: number[] = [];
  series.forEach((s) => {
    s.data.forEach((b) => {
      if (b.coupon !== null && b.coupon !== undefined) ys.push(b.coupon);
    });
  });
  const yLo = ys.length ? Math.min(...ys) - 0.15 : 0;
  const yHi = ys.length ? Math.max(...ys) + 0.15 : 2;

  return (
    <div className="h-[340px] w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 24, bottom: 8, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
          <XAxis
            dataKey="term"
            fontSize={12}
            tickLine={false}
            axisLine={false}
            interval={0}
            ticks={ticks}
            height={50}
            tick={{ fontSize: 12, angle: -25, textAnchor: "end", dy: 4 }}
            padding={{ left: 4, right: 12 }}
          />
          <YAxis
            fontSize={12}
            tickLine={false}
            axisLine={false}
            domain={[yLo, yHi]}
            tickFormatter={(v: number) => v.toFixed(2) + "%"}
            width={56}
          />
          <Tooltip
            cursor={{ stroke: "var(--border)", strokeDasharray: "3 3" }}
            content={(props) => {
              const p = props.payload?.[0]?.payload as ChartRow | undefined;
              if (!props.active || !p) return null;
              const rows: TipRow[] = [{ label: "期限档", value: String(p.term) }];
              const entries = props.payload ?? [];
              for (let j = 0; j < entries.length; j++) {
                const ent = entries[j];
                const idx = Number(String(ent.dataKey).slice(1));
                const s = series[idx];
                const v = ent.value;
                if (!s || typeof v !== "number") continue;
                // 日期行（含颜色圆点）
                rows.push({ label: s.date, value: `${v.toFixed(3)}%`, color: s.color });
                rows.push({
                  label: "只数 / 金额",
                  value: `${p[`_n${idx}`] ?? "-"} 只 · ${Number(p[`_a${idx}`] ?? 0).toLocaleString("zh-CN")} 亿`,
                  sub: true,
                });
                // 该日期该档的个券明细（紧跟日期下方，不重复日期；按发行量降序取前 6 只）
                const bonds = (p[`_b${idx}`] as BondDetail[] | undefined) ?? [];
                for (const b of bonds.slice(0, 6)) {
                  rows.push({
                    label: b.name,
                    value: `${b.termY}Y · ${b.coupon.toFixed(2)}% · ${b.amtYi} 亿`,
                    detail: true,
                  });
                }
                if (bonds.length > 6) {
                  rows.push({ label: `…其余 ${bonds.length - 6} 只略`, value: "", sub: true });
                }
              }
              if (rows.length <= 1) return null;
              return <TipPanel rows={rows} />;
            }}
          />
          {series.map((s, j) => (
            <Line
              key={s.date}
              type="monotone"
              dataKey={`s${j}`}
              stroke={s.color}
              strokeWidth={j === 0 ? 2.5 : 2}
              strokeDasharray={j === 0 ? undefined : undefined}
              dot={(p: { cx?: number; cy?: number; payload?: ChartRow }) => {
                const row = p.payload;
                const v = row?.[`s${j}`];
                if (typeof v !== "number" || p.cx == null) return <g />;
                return (
                  <circle
                    cx={p.cx}
                    cy={p.cy}
                    r={j === 0 ? 4 : 3.2}
                    fill={s.color}
                    stroke="var(--background)"
                    strokeWidth={1.5}
                  />
                );
              }}
              activeDot={{ r: 6 }}
              connectNulls={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
