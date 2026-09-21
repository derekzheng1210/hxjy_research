"use client";

import { apiFetch } from "@/lib/base-path";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { BellRing, CalendarClock, CheckCircle2, Hourglass, RefreshCw, Search } from "lucide-react";
import { PageHeader } from "@/components/page-header";
import { StatCard } from "@/components/stat-card";
import { LoadingState, ErrorState, EmptyState } from "@/components/status-state";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { estimateListDate, daysUntil, fmtDateShort } from "@/lib/format";
import type { BidRecord } from "@/lib/types";

interface Item {
  bid: BidRecord;
  listDate: string | null;
  days: number | null;
  status: "listed" | "today" | "soon" | "pending";
}

export default function ListingPage() {
  const [items, setItems] = useState<Item[] | null>(null);
  const [error, setError] = useState("");
  const [checking, setChecking] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError("");
    try {
      const res = await apiFetch("/api/bids?type=won");
      if (!res.ok) throw new Error("加载失败");
      const j = await res.json();
      const rows: Item[] = (j.list ?? []).map((b: BidRecord) => {
        const listDate = b.listDate || estimateListDate(b.payDate);
        const days = daysUntil(listDate);
        let status: Item["status"] = "pending";
        if (days === null) status = "pending";
        else if (days < 0) status = "listed";
        else if (days === 0) status = "today";
        else status = "soon";
        return { bid: b, listDate, days, status };
      });
      setItems(rows);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    }
  }, []);

  useEffect(() => {
    (async () => load())();
  }, [load]);

  const stats = useMemo(() => {
    if (!items) return null;
    const totalAmt = items.reduce((s, it) => s + (it.bid.amount || 0), 0);
    const soonAmt = items.filter((it) => it.status === "soon" && it.days !== null && it.days <= 7).reduce((s, it) => s + (it.bid.amount || 0), 0);
    const listed = items.filter((it) => it.status === "listed");
    const soon = items.filter((it) => it.status === "soon" && it.days !== null && it.days <= 7);
    return { total: items.length, totalAmt, soon, listed, soonAmt, today: items.filter((it) => it.status === "today") };
  }, [items]);

  // 查询 DM 上市日
  async function queryListDate(securityId: string | undefined, bid: BidRecord) {
    if (!securityId) return;
    setChecking(bid.id);
    try {
      const res = await apiFetch("/api/dm/basic-info", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ securityIds: [securityId] }),
      });
      if (!res.ok) throw new Error("查询失败");
      const j = await res.json();
      const info = (j.list ?? []).find((r: Record<string, unknown>) => r.security_id === securityId);
      const listDate = info?.list_date ? String(info.list_date) : null;
      if (listDate) {
        await apiFetch(`/api/bids?id=${bid.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ listDate }),
        });
      }
      await load();
    } catch (e) {
      alert(e instanceof Error ? e.message : "查询失败");
    } finally {
      setChecking(null);
    }
  }

  if (error) return <ErrorState message={error} onRetry={load} />;
  if (!items) return <LoadingState label="加载中标记录…" />;

  return (
    <>
      <PageHeader title="中标个券上市提醒" description="根据缴款日估算上市日（缴款后第 2 个工作日），也可录入债券代码后从 DM 查询精确上市日">
        <Button size="sm" variant="outline" className="gap-1.5" onClick={load}>
          <RefreshCw className="h-3.5 w-3.5" /> 刷新
        </Button>
      </PageHeader>

      {!items.length ? (
        <EmptyState
          title="暂无中标记录"
          description="请先在「中标个券」页面录入中标记录，上市提醒将自动生成。"
          action={
            <Link href="/won" className="text-sm font-medium text-primary hover:underline">
              去录入 →
            </Link>
          }
        />
      ) : (
        stats && (
          <>
            <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
              <StatCard label="中标记录" value={stats.total} icon={BellRing} accent="blue" sub={`合计 ${stats.totalAmt.toFixed(1)} 亿`} />
              <StatCard label="7天内上市" value={stats.soon.length + stats.today.length} icon={CalendarClock} accent="amber" sub={`涉及 ${stats.soonAmt.toFixed(1)} 亿`} />
              <StatCard label="今日上市" value={stats.today.length} icon={Hourglass} accent="red" />
              <StatCard label="已上市" value={stats.listed.length} icon={CheckCircle2} accent="green" />
            </div>

            <div className="space-y-3">
              {items.map((it) => {
                const { bid, listDate, days, status } = it;
                return (
                  <div
                    key={bid.id}
                    className={cn(
                      "flex flex-col gap-3 rounded-xl border p-4 sm:flex-row sm:items-center",
                      status === "today" && "border-rose-300 bg-rose-50/60 dark:border-rose-900 dark:bg-rose-950/30",
                      status === "soon" && days !== null && days <= 3 && "border-amber-300 bg-amber-50/60 dark:border-amber-900 dark:bg-amber-950/30",
                      status === "listed" && "border-emerald-200 bg-emerald-50/40 dark:border-emerald-900 dark:bg-emerald-950/20"
                    )}
                  >
                    <div className="flex-1">
                      <div className="flex items-center gap-2">
                        <p className="font-semibold">{bid.bondName}</p>
                        {bid.yy && (
                          <span className="inline-flex rounded-md bg-violet-50 px-1.5 py-0.5 text-[11px] font-semibold text-violet-700 dark:bg-violet-950 dark:text-violet-400">
                            YY {bid.yy}
                          </span>
                        )}
                        {bid.term && <span className="rounded bg-muted px-1.5 py-0.5 text-[11px]">{bid.term}</span>}
                        <span className="text-[12px] text-muted-foreground">{bid.amount.toFixed(1)} 亿</span>
                      </div>
                      <p className="mt-0.5 text-[12px] text-muted-foreground">
                        缴款日：{bid.payDate ? fmtDateShort(bid.payDate) : "未填"} · 投标日：{bid.bidDate}
                        {bid.securityId && <span className="ml-2 font-mono text-[11px]">{bid.securityId}</span>}
                      </p>
                    </div>

                    <div className="flex items-center gap-3">
                      {status === "pending" ? (
                        <div className="flex items-center gap-2">
                          <span className="text-sm text-muted-foreground">待补充缴款日/上市日</span>
                          {bid.securityId && (
                            <Button size="sm" variant="outline" disabled={checking === bid.id} onClick={() => queryListDate(bid.securityId, bid)}>
                              <Search className="mr-1 h-3.5 w-3.5" />
                              {checking === bid.id ? "查询中…" : "查询DM上市日"}
                            </Button>
                          )}
                        </div>
                      ) : (
                        <div className="text-right">
                          <p className="text-[12px] text-muted-foreground">
                            {status === "listed" ? "已于" : "预计"} {listDate ? fmtDateShort(listDate) : "-"} 上市
                          </p>
                          <p
                            className={cn(
                              "text-lg font-bold tabular-nums",
                              status === "today" ? "text-rose-600 dark:text-rose-400" : status === "soon" ? "text-amber-600 dark:text-amber-400" : status === "listed" ? "text-emerald-600 dark:text-emerald-400" : "text-muted-foreground"
                            )}
                          >
                            {status === "listed" ? "已上市" : status === "today" ? "今日上市" : `${days} 天后`}
                          </p>
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </>
        )
      )}
    </>
  );
}
