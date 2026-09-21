"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  DatabaseZap,
  Loader2,
  RefreshCw,
  Sheet,
  TableProperties,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { apiFetch } from "@/lib/base-path";

interface RefreshState {
  lastRun?: string;
  lastOkDate?: string;
  summary?: Record<string, unknown>;
  error?: string;
}

/**
 * 数据管理面板（侧边栏「上传 Excel」打开）：
 *  - 上传每日发行 Excel（当日整日覆盖 + 推荐个券 + YY 映射）
 *  - 上传一级中标登记表（幂等合并投标记录）
 *  - 手动触发 / 查看 DM 每日自动刷新状态（默认每日 19:00 自动跑）
 */
export function DataAdminDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const dailyRef = useRef<HTMLInputElement>(null);
  const bidsRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState<"" | "daily" | "bids" | "refresh">("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [state, setState] = useState<RefreshState | null>(null);

  const loadState = useCallback(async () => {
    try {
      const res = await apiFetch("/api/refresh");
      if (res.ok) setState(await res.json());
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    (async () => {
      setMsg(null);
      await loadState();
    })();
  }, [open, loadState]);

  async function upload(type: "daily" | "bids", file: File) {
    setBusy(type);
    setMsg(null);
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("type", type);
      const res = await apiFetch("/api/excel", {
        method: "POST",
        body: form,
      });
      const j = await res.json().catch(() => ({}));
      if (!res.ok) {
        setMsg({ ok: false, text: j?.error || `导入失败(${res.status})` });
      } else {
        setMsg({ ok: true, text: j?.message || "导入成功" });
        loadState();
      }
    } catch {
      setMsg({ ok: false, text: "网络异常，上传失败" });
    } finally {
      setBusy("");
    }
  }

  async function refresh() {
    setBusy("refresh");
    setMsg(null);
    try {
      const res = await apiFetch("/api/refresh", {
        method: "POST",
      });
      const j = await res.json().catch(() => ({}));
      if (!res.ok) {
        setMsg({ ok: false, text: j?.error || `刷新失败(${res.status})` });
      } else if (j?.skipped) {
        setMsg({ ok: true, text: j?.message || "刷新进行中" });
      } else {
        const cal = j?.summary?.calendar as { daysAdded?: number; bondsAdded?: number; recBackfilled?: number } | undefined;
        const val = j?.summary?.valuations as { bonds?: number; valuedDate?: string } | undefined;
        const bids = j?.summary?.bidsInfo as { patched?: number; payDate?: number; listDate?: number } | undefined;
        const curve = j?.summary?.couponCurve as { range?: string; bondCount?: number } | undefined;
        const curveEnd = curve?.range ? curve.range.split("~")[1] : "-";
        setMsg({
          ok: true,
          text: `刷新完成：日历新增 ${cal?.daysAdded ?? 0} 日/${cal?.bondsAdded ?? 0} 只，推荐回填 ${cal?.recBackfilled ?? 0} 只，记录回填 ${bids?.patched ?? 0} 条（缴款日 ${bids?.payDate ?? 0}/上市日 ${bids?.listDate ?? 0}），估值 ${val?.bonds ?? 0} 只（${val?.valuedDate ?? "-"}），曲线至 ${curveEnd}`,
        });
      }
      loadState();
    } catch {
      setMsg({ ok: false, text: "网络异常，刷新失败" });
    } finally {
      setBusy("");
    }
  }

  const lastOk = state?.summary
    ? String((state.summary as { finishedAt?: string }).finishedAt ?? state.lastRun ?? "").replace("T", " ").slice(0, 19)
    : "";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <DatabaseZap className="h-4 w-4" /> 数据管理
          </DialogTitle>
          <DialogDescription>
            每日发行 Excel 与中标登记表在此上传；DM 市场数据每日 19:00 自动刷新（也可手动触发）。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          {/* 每日发行 Excel */}
          <div className="rounded-lg border bg-muted/30 p-3">
            <p className="flex items-center gap-1.5 text-xs font-semibold text-foreground/80">
              <Sheet className="h-3.5 w-3.5" /> 每日发行 Excel
            </p>
            <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
              《一级发行-信用债发行 YYYY-MM-DD.xlsx》：更新当日发行清单与推荐个券（重复上传同一天为覆盖式，幂等）。文件名需含日期。
            </p>
            <input
              ref={dailyRef}
              type="file"
              accept=".xlsx"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void upload("daily", f);
                e.target.value = "";
              }}
            />
            <Button
              size="sm"
              className="mt-2 gap-1"
              disabled={busy !== ""}
              onClick={() => dailyRef.current?.click()}
            >
              {busy === "daily" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
              {busy === "daily" ? "导入中…" : "选择文件上传"}
            </Button>
          </div>

          {/* 中标登记表 */}
          <div className="rounded-lg border bg-muted/30 p-3">
            <p className="flex items-center gap-1.5 text-xs font-semibold text-foreground/80">
              <TableProperties className="h-3.5 w-3.5" /> 一级中标登记表
            </p>
            <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
              按（发行日 + 债券）分组合并：参与 = Σ投资量、中标 = Σ中标量；已录入的记录不重复建，仅回填缺失的票面/期限/代码/YY。
            </p>
            <input
              ref={bidsRef}
              type="file"
              accept=".xlsx"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void upload("bids", f);
                e.target.value = "";
              }}
            />
            <Button
              size="sm"
              className="mt-2 gap-1"
              disabled={busy !== ""}
              onClick={() => bidsRef.current?.click()}
            >
              {busy === "bids" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
              {busy === "bids" ? "合并中…" : "选择文件上传"}
            </Button>
          </div>

          {/* DM 数据刷新 */}
          <div className="rounded-lg border bg-muted/30 p-3">
            <p className="flex items-center gap-1.5 text-xs font-semibold text-foreground/80">
              <RefreshCw className="h-3.5 w-3.5" /> DM 数据刷新
            </p>
            <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
              自动任务每日 19:00 执行：DM 补充未上传日的发行清单、重抓个券估值、追加 30Y 国债。Excel 上传过的日期不会被覆盖。
              {lastOk && <> 最近成功：{lastOk}</>}
              {state?.error && <span className="text-destructive">（上次出错：{state.error.slice(0, 80)}）</span>}
            </p>
            <Button
              size="sm"
              variant="outline"
              className="mt-2 gap-1"
              disabled={busy !== ""}
              onClick={() => void refresh()}
            >
              {busy === "refresh" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
              {busy === "refresh" ? "刷新中…" : "立即刷新"}
            </Button>
          </div>

          {msg && (
            <p className={`text-sm ${msg.ok ? "text-emerald-600 dark:text-emerald-400" : "text-destructive"}`}>{msg.text}</p>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
