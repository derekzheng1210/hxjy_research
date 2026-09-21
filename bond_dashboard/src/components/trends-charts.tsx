"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { TrendData } from "@/lib/types";

const COLORS = ["#3b82f6", "#10b981", "#8b5cf6", "#f59e0b", "#ef4444", "#06b6d4", "#84cc16", "#f97316", "#64748b", "#e11d48", "#6366f1", "#14b8a6"];
const OTHER_COLOR = "#94a3b8";

/** 期限档固定配色（跨年可比） */
const TERM_COLORS: Record<string, string> = {
  "<1Y": "#3b82f6",
  "1-3Y": "#10b981",
  "3-5Y": "#8b5cf6",
  "5-10Y": "#f59e0b",
  ">=10Y": "#ef4444",
  "未知": "#94a3b8",
};
const YEAR_COLORS: Record<string, string> = { "2024": "#3b82f6", "2025": "#10b981", "2026": "#f59e0b" };

const YEARS = ["2024", "2025", "2026"];

function colorOf(key: string, i: number) {
  return key === "其他" ? OTHER_COLOR : COLORS[i % COLORS.length];
}

function LegendChips({ keys, colorMap }: { keys: string[]; colorMap?: Record<string, string> }) {
  return (
    <div className="mb-3 flex flex-wrap gap-x-4 gap-y-1">
      {keys.map((k, i) => (
        <span key={k} className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
          <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: colorMap?.[k] ?? colorOf(k, i) }} />
          {k}
        </span>
      ))}
    </div>
  );
}

/** 年度堆叠柱：X = 月份，堆叠 = 分类，逐年一张 */
function YearStackChart({
  year,
  rows,
  keys,
  colorMap,
  height = 240,
  unit = "亿",
}: {
  year: string;
  rows: { ym: string; [k: string]: number | string }[];
  keys: string[];
  colorMap?: Record<string, string>;
  height?: number;
  unit?: string;
}) {
  const data = rows
    .filter((r) => String(r.ym).startsWith(year))
    .map((r) => {
      const rec: Record<string, number | string> = { m: `${Number(String(r.ym).slice(5, 7))}月` };
      for (const k of keys) rec[k] = Number(r[k] ?? 0);
      return rec;
    });
  const total = data.reduce((s, r) => s + keys.reduce((t, k) => t + Number(r[k] ?? 0), 0), 0);
  return (
    <div className="rounded-lg border p-3">
      <p className="mb-2 text-xs font-semibold">
        {year} 年
        <span className="ml-2 font-normal text-muted-foreground">合计 {total.toFixed(0)} 亿</span>
      </p>
      <ResponsiveContainer width="100%" height={height}>
        <BarChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
          <XAxis dataKey="m" fontSize={9} tickLine={false} axisLine={false} interval={0} />
          <YAxis fontSize={9} tickLine={false} axisLine={false} width={44} tickFormatter={(v) => `${Math.round(Number(v))}`} />
          <Tooltip
            formatter={(v) => [`${Number(v).toFixed(0)} ${unit}`, ""]}
            labelFormatter={(l) => `${year} 年 ${l}`}
            contentStyle={{ fontSize: 11 }}
          />
          {keys.map((k, i) => (
            <Bar key={k} dataKey={k} stackId="a" fill={colorMap?.[k] ?? colorOf(k, i)} />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export function MonthlyChart({ data }: { data: TrendData }) {
  return (
    <div className="rounded-xl border p-4">
      <p className="mb-3 text-sm font-semibold">月度发行规模与只数趋势（{data.meta.range}）</p>
      <ResponsiveContainer width="100%" height={320}>
        <ComposedChart data={data.monthly} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
          <XAxis dataKey="ym" fontSize={10} tickLine={false} axisLine={false} interval={2} />
          <YAxis yAxisId="plan" fontSize={11} tickLine={false} axisLine={false} tickFormatter={(v) => `${v / 1000}k`} />
          <YAxis yAxisId="cnt" orientation="right" fontSize={11} tickLine={false} axisLine={false} allowDecimals={false} />
          <Tooltip
            formatter={(v, name) => (name === "plan" ? [`${Number(v).toFixed(0)} 亿`, "发行规模"] : [`${v} 只`, "发行只数"])}
          />
          <Bar yAxisId="plan" dataKey="plan" name="plan" fill="#3b82f6" radius={[2, 2, 0, 0]} opacity={0.85} />
          <Line yAxisId="cnt" dataKey="cnt" name="cnt" stroke="#10b981" strokeWidth={2} dot={false} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

export function TypeDistChart({ data }: { data: TrendData }) {
  const keys = data.type_keys ?? [];
  const rows = data.type_monthly ?? [];
  return (
    <div className="rounded-xl border p-4">
      <p className="text-sm font-semibold">债券类型分布（2024 / 2025 / 2026 月度发行规模）</p>
      <LegendChips keys={keys} />
      <div className="grid gap-3 lg:grid-cols-3">
        {YEARS.map((y) => (
          <YearStackChart key={y} year={y} rows={rows} keys={keys} height={230} />
        ))}
      </div>
    </div>
  );
}

export function TermDistChart({ data }: { data: TrendData }) {
  const keys = data.term_keys ?? [];
  const rows = data.term_monthly ?? [];
  return (
    <div className="rounded-xl border p-4">
      <p className="text-sm font-semibold">期限结构分布（2024 / 2025 / 2026 月度发行规模）</p>
      <LegendChips keys={keys} colorMap={TERM_COLORS} />
      <div className="grid gap-3 lg:grid-cols-3">
        {YEARS.map((y) => (
          <YearStackChart key={y} year={y} rows={rows} keys={keys} colorMap={TERM_COLORS} height={230} />
        ))}
      </div>
    </div>
  );
}

/** 票面利率分布：逐年直方图 + 正态拟合曲线 */
function YieldNormalChart({ h }: { h: TrendData["yield_hist"][number] }) {
  const bw = 0.1;
  const mu = h.mean;
  const sd = h.sd > 0 ? h.sd : 0.0001;
  const data = h.bins.map((b) => {
    const pdf = Math.exp(-((b.ybin - mu) ** 2) / (2 * sd * sd)) / (sd * Math.sqrt(2 * Math.PI));
    return { ybin: b.ybin, cnt: b.cnt, fit: Number((h.n * bw * pdf).toFixed(2)) };
  });
  return (
    <div className="rounded-lg border p-3">
      <p className="mb-2 text-xs font-semibold">
        {h.year} 年
        <span className="ml-2 font-normal text-muted-foreground">
          均值 {mu.toFixed(3)}% · σ {sd.toFixed(3)}% · N={h.n.toLocaleString()}
        </span>
      </p>
      <ResponsiveContainer width="100%" height={230}>
        <ComposedChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
          <XAxis
            dataKey="ybin"
            fontSize={9}
            tickLine={false}
            axisLine={false}
            tickFormatter={(v) => `${Number(v).toFixed(1)}%`}
            interval={9}
          />
          <YAxis fontSize={9} tickLine={false} axisLine={false} width={40} allowDecimals={false} />
          <Tooltip
            formatter={(v, name) => [name === "cnt" ? `${v} 只` : `${Number(v).toFixed(0)} 只（拟合）`, name === "cnt" ? "只数" : "正态拟合"]}
            labelFormatter={(l) => `票面 ${Number(l).toFixed(1)}%`}
            contentStyle={{ fontSize: 11 }}
          />
          <Bar dataKey="cnt" fill="#f59e0b" radius={[2, 2, 0, 0]} />
          <Line type="monotone" dataKey="fit" stroke="#3b82f6" strokeWidth={2} dot={false} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

export function YieldHistChart({ data }: { data: TrendData }) {
  const list = data.yield_hist ?? [];
  return (
    <div className="rounded-xl border p-4">
      <p className="text-sm font-semibold">票面利率分布（2024 / 2025 / 2026，正态图）</p>
      <div className="grid gap-3 lg:grid-cols-3">
        {list.map((h) => (
          <YieldNormalChart key={h.year} h={h} />
        ))}
      </div>
    </div>
  );
}

export function MonthlyYieldChart({ data }: { data: TrendData }) {
  const series: { key: "t3" | "t5" | "t10" | "t15" | "t30"; name: string; color: string }[] = [
    { key: "t3", name: "3年期", color: "#3b82f6" },
    { key: "t5", name: "5年期", color: "#10b981" },
    { key: "t10", name: "10年期", color: "#8b5cf6" },
    { key: "t15", name: "15年期", color: "#f59e0b" },
    { key: "t30", name: "30年期", color: "#ef4444" },
  ];
  const rows = data.monthly_yield_term ?? [];
  const chartRows = rows.filter((r) => series.some((s) => r[s.key] !== null && r[s.key] !== undefined));
  const vals = chartRows.flatMap((r) => series.map((s) => r[s.key]).filter((v): v is number => v !== null && v !== undefined));
  const min = vals.length ? Math.min(...vals) : 0;
  const max = vals.length ? Math.max(...vals) : 1;
  return (
    <div className="rounded-xl border p-4">
      <p className="text-sm font-semibold">月度平均票面利率（2024.01 起，按发行期限：3Y / 5Y / 10Y / 15Y / 30Y）</p>
      <ResponsiveContainer width="100%" height={300}>
        <LineChart data={chartRows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
          <XAxis dataKey="ym" fontSize={10} tickLine={false} axisLine={false} interval={1} />
          <YAxis
            fontSize={11}
            tickLine={false}
            axisLine={false}
            domain={[Number((min - 0.08).toFixed(2)), Number((max + 0.08).toFixed(2))]}
            tickFormatter={(v) => `${Number(v).toFixed(2)}%`}
            width={56}
          />
          <Tooltip formatter={(v) => `${Number(v).toFixed(3)}%`} />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          {series.map((s) => (
            <Line
              key={s.key}
              type="monotone"
              dataKey={s.key}
              name={s.name}
              stroke={s.color}
              strokeWidth={2.2}
              dot={{ r: 2.5, fill: s.color }}
              connectNulls
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export function ProvinceTopChart({ data }: { data: TrendData }) {
  const groups = data.province_top_year ?? [];
  return (
    <div className="rounded-xl border p-4">
      <p className="text-sm font-semibold">区域发行规模 TOP12（2024 / 2025 / 2026）</p>
      <div className="grid gap-3 lg:grid-cols-3">
        {groups.map((g) => (
          <div key={g.year} className="rounded-lg border p-3">
            <p className="mb-2 text-xs font-semibold">
              {g.year} 年
              <span className="ml-2 font-normal text-muted-foreground">
                合计 {g.items.reduce((s, x) => s + x.plan, 0).toFixed(0)} 亿（TOP12）
              </span>
            </p>
            <ResponsiveContainer width="100%" height={330}>
              <BarChart data={g.items} layout="vertical" margin={{ top: 2, right: 12, bottom: 0, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" horizontal={false} />
                <XAxis type="number" fontSize={9} tickLine={false} axisLine={false} tickFormatter={(v) => `${Math.round(Number(v) / 1000)}k`} />
                <YAxis type="category" dataKey="province_name" fontSize={9} width={62} tickLine={false} axisLine={false} />
                <Tooltip formatter={(v) => [`${Number(v).toFixed(0)} 亿`, "发行规模"]} contentStyle={{ fontSize: 11 }} />
                <Bar dataKey="plan" fill={YEAR_COLORS[g.year] ?? "#3b82f6"} radius={[0, 3, 3, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        ))}
      </div>
    </div>
  );
}
