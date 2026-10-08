"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { ArrowLeft, ExternalLink } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * 发行人下钻：点击看板内的发行人名称，在整页覆盖层中打开门户「一级发行研究」
 * 的发行人分析视图（embed 模式，无门户导航）。覆盖层不卸载底下的看板页面，
 * 一键返回后原页面的筛选、日期、滚动位置全部保持不变。
 */

const IssuerDrilldownContext = createContext<(issuer: string) => void>(() => {});

export function useIssuerDrilldown() {
  return useContext(IssuerDrilldownContext);
}

/** 门户页面地址：看板经 /bond-dashboard 反代部署，门户根在 basePath 之前 */
function portalUrl(path: string): string {
  if (typeof window === "undefined") return path;
  const idx = window.location.pathname.indexOf("/bond-dashboard");
  const base = idx > 0 ? window.location.pathname.slice(0, idx) : "";
  return base + path;
}

export function IssuerDrilldownProvider({ children }: { children: React.ReactNode }) {
  const [issuer, setIssuer] = useState<string | null>(null);

  const open = useCallback((name: string) => {
    const trimmed = (name || "").trim();
    if (trimmed) setIssuer(trimmed);
  }, []);

  useEffect(() => {
    if (!issuer) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setIssuer(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [issuer]);

  const embedSrc = issuer
    ? `${portalUrl("/primary-market-pricing/")}?view=issuer&issuer=${encodeURIComponent(issuer)}&embed=1`
    : "";
  // 新窗口打开时脱离嵌入环境，带上门户导航
  const standaloneSrc = issuer
    ? `${portalUrl("/primary-market-pricing/")}?view=issuer&issuer=${encodeURIComponent(issuer)}`
    : "";

  return (
    <IssuerDrilldownContext.Provider value={open}>
      {children}
      {issuer && (
        <div className="fixed inset-0 z-[10001] flex flex-col bg-background">
          <div className="flex h-12 shrink-0 items-center gap-3 border-b bg-background px-4">
            <Button variant="ghost" size="sm" className="gap-1.5 font-semibold" onClick={() => setIssuer(null)}>
              <ArrowLeft className="h-4 w-4" />
              返回看板
            </Button>
            <div className="min-w-0 flex-1 text-center">
              <span className="truncate text-sm font-bold">{issuer}</span>
              <span className="ml-2 hidden text-xs text-muted-foreground sm:inline">一级发行研究 · 发行人分析</span>
            </div>
            <Button
              variant="outline"
              size="sm"
              className="gap-1.5"
              onClick={() => standaloneSrc && window.open(standaloneSrc, "_blank")}
            >
              <ExternalLink className="h-3.5 w-3.5" />
              新窗口打开
            </Button>
          </div>
          <iframe
            key={issuer}
            src={embedSrc}
            title={`发行人分析 · ${issuer}`}
            className={cn("w-full flex-1 border-0")}
          />
        </div>
      )}
    </IssuerDrilldownContext.Provider>
  );
}
