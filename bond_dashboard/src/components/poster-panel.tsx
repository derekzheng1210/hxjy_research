"use client";

import { apiFetch } from "@/lib/base-path";

// 「每日一级发行结果汇总」海报：生成按钮 + 近期海报画廊 + 大图预览
// 生成走 /api/poster/generate（服务端跑 scripts/build_daily_poster.py，调用海报技能引擎）；
// 列表/取图走 /api/poster/list 与 /api/poster/file。
import { useCallback, useEffect, useState } from "react";
import { Wand2, Loader2, CheckCircle2, AlertTriangle, Image as ImageIcon, ExternalLink, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { fmtDateShort } from "@/lib/format";

interface PosterEntry {
  date: string;
  pngSize: number;
  hasHtml: boolean;
}

interface GenResult {
  ok?: boolean;
  reason?: string;
  date?: string;
  file?: string;
  pngSize?: number;
  posterDir?: string;
  message?: string;
  error?: string;
  stderrTail?: string[];
}

function pngUrl(date: string) {
  return `/api/poster/file?date=${date}&type=png`;
}

/** 生成结果 → 人类可读提示 */
function describeResult(r: GenResult): string {
  if (r?.ok) return `已生成 ${r.date} 海报（PNG ${(r.pngSize ?? 0) / 1024 >= 1 ? ((r.pngSize ?? 0) / 1024 / 1024).toFixed(1) + " MB" : Math.round((r.pngSize ?? 0) / 1024) + " KB"}）。`;
  switch (r?.reason) {
    case "no_coupon_col":
      return r.message || "所选日期 Excel 尚未含「票面利率」列（收盘版未生成），海报已跳过。";
    case "date_not_found":
      return "所选日期在投标目录中无对应 Excel 文件。";
    case "no_excel_dir":
      return "投标 Excel 目录不存在或为空。";
    case "engine_missing":
      return "海报引擎或 Python 环境缺失，请联系管理员检查。";
    case "engine_fail":
      return "海报引擎执行失败，详见下方日志。";
    default:
      return r?.error || "生成失败，请重试。";
  }
}

/** 「生成海报」弹窗 + 画廊 */
export function PosterPanel({ defaultDate = "" }: { defaultDate?: string }) {
  const [list, setList] = useState<PosterEntry[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [genOpen, setGenOpen] = useState(false);
  const [genDate, setGenDate] = useState(defaultDate || "");
  const [result, setResult] = useState<GenResult | null>(null);
  const [running, setRunning] = useState(false);
  const [preview, setPreview] = useState<PosterEntry | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await apiFetch("/api/poster/list", { cache: "no-store" });
      const j = await res.json().catch(() => ({}));
      setList(j.list ?? []);
    } catch {
      setList([]);
    }
  }, []);

  useEffect(() => {
    (async () => load())();
  }, [load]);

  async function runGenerate() {
    setRunning(true);
    setResult(null);
    setBusy(true);
    try {
      const res = await apiFetch("/api/poster/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(genDate ? { date: genDate } : {}),
      });
      const j: GenResult = await res.json().catch(() => ({}));
      setResult({ ...j, error: !res.ok ? j.error || `请求失败(${res.status})` : j.error });
      if (j?.ok) await load();
    } catch (e) {
      setResult({ ok: false, error: e instanceof Error ? e.message : "生成失败" });
    } finally {
      setRunning(false);
      setBusy(false);
    }
  }

  function openGen() {
    setGenDate(defaultDate || "");
    setResult(null);
    setGenOpen(true);
  }

  return (
    <div className="mt-2">
      {/* 头部：标题 + 生成按钮 */}
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-semibold">一级发行结果海报（酒红版式）</p>
        <Button size="sm" variant="outline" className="gap-1.5" onClick={openGen} disabled={busy}>
          {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Wand2 className="h-3.5 w-3.5" />}
          {busy ? "生成中…" : "生成海报"}
        </Button>
      </div>

      {/* 画廊 */}
      {list === null ? (
        <div className="rounded-xl border bg-background p-6 text-center text-sm text-muted-foreground">正在加载海报列表…</div>
      ) : !list.length ? (
        <div className="rounded-xl border bg-background p-6 text-center">
          <ImageIcon className="mx-auto mb-2 h-6 w-6 opacity-40" />
          <p className="text-sm text-muted-foreground">
            暂无海报。点击右上角「生成海报」，或由每交易日 17:15 计划任务自动生成。
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-4">
          {list.map((p) => (
            <button
              key={p.date}
              type="button"
              onClick={() => setPreview(p)}
              className="group overflow-hidden rounded-xl border bg-background text-left transition-shadow hover:shadow-md"
            >
              <div className="relative aspect-[3/4] w-full overflow-hidden bg-muted/40">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={pngUrl(p.date)}
                  alt={`${fmtDateShort(p.date)} 一级发行结果汇总海报`}
                  loading="lazy"
                  className="h-full w-full object-cover object-top transition-transform group-hover:scale-[1.03]"
                />
                <span className="absolute bottom-1.5 left-1.5 rounded-md bg-black/55 px-1.5 py-0.5 text-[11px] font-medium text-white">
                  {fmtDateShort(p.date)}
                </span>
              </div>
            </button>
          ))}
        </div>
      )}

      {/* 生成弹窗 */}
      {genOpen && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/30 p-4" onClick={() => !running && setGenOpen(false)}>
          <div
            role="dialog"
            aria-label="生成海报"
            className="w-full max-w-md rounded-xl border bg-background p-5 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-3 flex items-center justify-between">
              <p className="text-sm font-semibold">生成一级发行结果海报</p>
              <button className="rounded-md p-1 text-muted-foreground hover:bg-accent" onClick={() => !running && setGenOpen(false)} aria-label="关闭">
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="space-y-3">
              <div>
                <label className="mb-1 block text-[12px] font-medium text-muted-foreground">
                  发行日期（留空 = 自动取最新含票面的收盘版 Excel）
                </label>
                <Input
                  type="date"
                  value={genDate}
                  onChange={(e) => setGenDate(e.target.value)}
                  className="h-9 tabular-nums"
                  disabled={running}
                />
              </div>
              <div className="rounded-lg bg-muted/50 p-2.5 text-[12px] leading-relaxed text-muted-foreground">
                海报基于 D:/2026/一级投标/投标情况/ 当日收盘 Excel（需含「票面利率」列）生成。若所选日尚未收盘，将自动跳过并提示。
              </div>
              {result && (
                <div className="space-y-1.5">
                  <p className={`flex items-start gap-1.5 text-[13px] leading-relaxed ${result.ok ? "text-emerald-600 dark:text-emerald-400" : "text-amber-600 dark:text-amber-400"}`}>
                    {result.ok ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" /> : <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />}
                    <span>{describeResult(result)}</span>
                  </p>
                  {result?.file && <p className="text-[11px] text-muted-foreground">源文件：{result.file}</p>}
                  {!!result?.stderrTail?.length && (
                    <pre className="max-h-32 overflow-auto rounded bg-muted/50 p-2 text-[11px] leading-relaxed text-muted-foreground">
                      {result.stderrTail.join("\n")}
                    </pre>
                  )}
                </div>
              )}
            </div>
            <div className="mt-4 flex justify-end gap-2">
              <Button variant="outline" size="sm" onClick={() => setGenOpen(false)} disabled={running}>
                关闭
              </Button>
              <Button size="sm" className="gap-1.5" onClick={runGenerate} disabled={running}>
                {running ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Wand2 className="h-3.5 w-3.5" />}
                {running ? "生成中…" : "开始生成"}
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* 大图预览 */}
      {preview && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" onClick={() => setPreview(null)}>
          <div className="relative flex max-h-[94vh] w-full max-w-3xl flex-col" onClick={(e) => e.stopPropagation()}>
            <div className="mb-2 flex items-center justify-between text-white">
              <p className="text-sm font-medium">{fmtDateShort(preview.date)} 一级发行结果汇总</p>
              <div className="flex items-center gap-1">
                {preview.hasHtml && (
                  <a
                    href={`/api/poster/file?date=${preview.date}&type=html`}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 rounded-md bg-white/15 px-2.5 py-1.5 text-[12px] font-medium text-white hover:bg-white/25"
                  >
                    <ExternalLink className="h-3.5 w-3.5" /> 打开 HTML
                  </a>
                )}
                <a
                  href={pngUrl(preview.date)}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1 rounded-md bg-white/15 px-2.5 py-1.5 text-[12px] font-medium text-white hover:bg-white/25"
                >
                  <ImageIcon className="h-3.5 w-3.5" /> 原图
                </a>
                <button className="rounded-md bg-white/15 p-1.5 text-white hover:bg-white/25" onClick={() => setPreview(null)} aria-label="关闭预览">
                  <X className="h-4 w-4" />
                </button>
              </div>
            </div>
            <div className="flex-1 overflow-auto rounded-xl bg-black/40 p-2">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={pngUrl(preview.date)} alt={`${preview.date} 海报大图`} className="mx-auto h-auto max-h-[82vh] w-auto rounded-lg shadow-2xl" />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
