// 发行人补充指标读取层（交易所质押比区间 + 近五年 YY 调整）
// 数据来源：打包者机 Python 管道（build_issuer_metrics.py）生成的
// issuer_metrics.json（数据主权划分见 bond_dashboard/方案B改造说明.md 第八节，
// 服务端不重建，仅读取快照）。
// 与 internal-ratings.ts 相同的 mtime 缓存模式：文件被管道更新后自动重读。
import fs from "node:fs";
import path from "node:path";

import { DATA_DIR } from "@/lib/data-dir";

export interface PledgeRange {
  min: number;
  max: number;
  n: number;
}

export interface YyMove {
  from: string;
  to: string;
  date: string;
}

export interface YyNet {
  dir: "up" | "down" | "flat";
  steps: number;
  from: string;
  to: string;
  firstDate: string;
  lastDate: string;
  moves: number;
  firstDir: "up" | "down" | null;
}

export interface IssuerMetrics {
  pledge?: PledgeRange | null;
  yyAdj?: { up: YyMove[]; down: YyMove[]; yyNet: YyNet | null } | null;
}

let cachedMtimeMs: number | null = null;
let cachedMap: Record<string, IssuerMetrics> = {};

function readMap(): Record<string, IssuerMetrics> {
  try {
    const file = path.join(DATA_DIR, "cache", "issuer_metrics.json");
    const st = fs.statSync(file);
    if (st.mtimeMs !== cachedMtimeMs) {
      const j = JSON.parse(fs.readFileSync(file, "utf8")) as {
        map?: Record<string, IssuerMetrics>;
      };
      cachedMap = j.map ?? {};
      cachedMtimeMs = st.mtimeMs;
    }
    return cachedMap;
  } catch {
    return {};
  }
}

/** 按主体全称取补充指标（质押比 / YY 调整；无则 undefined，前端显示"-"） */
export function issuerMetricsOf(issuer: string | null | undefined): IssuerMetrics | undefined {
  const key = String(issuer ?? "").trim();
  if (!key) return undefined;
  const hit = readMap()[key];
  return hit ?? undefined;
}
