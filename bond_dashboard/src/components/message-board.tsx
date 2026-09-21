"use client";

import { apiFetch } from "@/lib/base-path";

import { useCallback, useEffect, useState } from "react";
import { Send, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { LoadingState, ErrorState, EmptyState } from "@/components/status-state";
import type { Message } from "@/lib/types";

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1) return "刚刚";
  if (m < 60) return `${m} 分钟前`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} 小时前`;
  const d = Math.floor(h / 24);
  return `${d} 天前`;
}

export function MessageBoard() {
  const [messages, setMessages] = useState<Message[] | null>(null);
  const [error, setError] = useState("");
  const [author, setAuthor] = useState("");
  const [content, setContent] = useState("");
  const [sending, setSending] = useState(false);

  const load = useCallback(async () => {
    setError("");
    try {
      const res = await apiFetch("/api/messages");
      if (!res.ok) throw new Error("加载失败");
      const j = await res.json();
      setMessages(j.list ?? []);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    }
  }, []);

  useEffect(() => {
    (async () => load())();
  }, [load]);

  async function send() {
    if (!content.trim()) return;
    setSending(true);
    try {
      const res = await apiFetch("/api/messages", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ author: author.trim() || "匿名", content: content.trim() }),
      });
      if (!res.ok) {
        const j = await res.json().catch(() => ({}));
        throw new Error(j.error || "发送失败");
      }
      setContent("");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "发送失败");
    } finally {
      setSending(false);
    }
  }

  async function remove(id: string) {
    if (!confirm("确认删除该条留言？")) return;
    try {
      await apiFetch(`/api/messages?id=${id}`, { method: "DELETE" });
      await load();
    } catch {
      /* ignore */
    }
  }

  if (error) return <ErrorState message={error} onRetry={load} />;
  if (!messages) return <LoadingState label="留言加载中…" />;

  return (
    <>
      <div className="grid gap-6 lg:grid-cols-[1fr_340px]">
        {/* 留言列表 */}
        <div className="space-y-3">
          {!messages.length ? (
            <EmptyState title="暂无留言" description="发布第一条信息，和大家交流吧。" />
          ) : (
            messages.map((m) => (
              <div key={m.id} className="rounded-xl border p-4">
                <div className="mb-2 flex items-center gap-2">
                  <Avatar className="h-7 w-7">
                    <AvatarFallback className="bg-primary/10 text-[11px] text-primary">
                      {m.author.slice(0, 1)}
                    </AvatarFallback>
                  </Avatar>
                  <span className="text-sm font-semibold">{m.author}</span>
                  <span className="text-xs text-muted-foreground">{timeAgo(m.createdAt)}</span>
                  <span className="ml-auto text-xs tabular-nums text-muted-foreground">
                    {new Date(m.createdAt).toLocaleString("zh-CN", { hour12: false })}
                  </span>
                  <Button size="icon" variant="ghost" className="h-6 w-6 text-muted-foreground hover:text-destructive" onClick={() => remove(m.id)}>
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
                <p className="whitespace-pre-wrap text-[13px] leading-relaxed">{m.content}</p>
              </div>
            ))
          )}
        </div>

        {/* 发布框 */}
        <div className="h-fit rounded-xl border p-4 lg:sticky lg:top-6">
          <p className="mb-3 flex items-center gap-2 text-sm font-semibold">
            <Send className="h-4 w-4" /> 发布信息
          </p>
          <div className="space-y-3">
            <div>
              <label className="mb-1 block text-xs text-muted-foreground">昵称</label>
              <Input
                placeholder="你的名字（默认匿名）"
                value={author}
                maxLength={20}
                onChange={(e) => setAuthor(e.target.value)}
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-muted-foreground">内容</label>
              <Textarea
                rows={5}
                placeholder="分享发行信息、投标心得、市场观点…"
                value={content}
                maxLength={2000}
                onChange={(e) => setContent(e.target.value)}
              />
              <p className="mt-1 text-right text-[11px] text-muted-foreground">{content.length}/2000</p>
            </div>
            <Button className="w-full" onClick={send} disabled={sending || !content.trim()}>
              {sending ? "发送中…" : "发布"}
            </Button>
          </div>
        </div>
      </div>
    </>
  );
}
