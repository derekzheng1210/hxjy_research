"use client";

import { useEffect } from "react";
import { BASE_PATH } from "@/lib/base-path";

/** 注册 Service Worker（PWA 离线壳） */
export function PWARegister() {
  useEffect(() => {
    if (!("serviceWorker" in navigator)) return;
    // 延迟注册，避免与页面初始化竞争
    const t = window.setTimeout(() => {
      navigator.serviceWorker
        .register(BASE_PATH + "/sw.js", { scope: BASE_PATH + "/" })
        .catch(() => {
          /* 注册失败静默（如非 localhost 且未 HTTPS 的场景） */
        });
    }, 3000);
    return () => window.clearTimeout(t);
  }, []);
  return null;
}
