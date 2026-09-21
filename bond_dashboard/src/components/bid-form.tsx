"use client";

import { apiFetch } from "@/lib/base-path";

import { useState } from "react";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { BidType } from "@/lib/types";

const TYPE_LABELS: Record<BidType, string> = {
  participated: "参与投标",
  won: "中标",
};

export function BidFormDialog({
  defaultType = "participated",
  onSaved,
}: {
  defaultType?: BidType;
  onSaved?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [type, setType] = useState<BidType>(defaultType);
  const [bondName, setBondName] = useState("");
  const [securityId, setSecurityId] = useState("");
  const [issuer, setIssuer] = useState("");
  const [term, setTerm] = useState("");
  const [amount, setAmount] = useState("");
  const [coupon, setCoupon] = useState("");
  const [spread, setSpread] = useState("");
  const [bidDate, setBidDate] = useState(new Date().toISOString().slice(0, 10));
  const [payDate, setPayDate] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  function reset() {
    setBondName("");
    setSecurityId("");
    setIssuer("");
    setTerm("");
    setAmount("");
    setCoupon("");
    setSpread("");
    setBidDate(new Date().toISOString().slice(0, 10));
    setPayDate("");
    setError("");
  }

  async function submit() {
    if (!bondName.trim()) {
      setError("请填写债券简称");
      return;
    }
    if (!amount || Number(amount) <= 0) {
      setError("请填写有效的金额（亿元）");
      return;
    }
    setSaving(true);
    setError("");
    try {
      const res = await apiFetch("/api/bids", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          type,
          bondName: bondName.trim(),
          securityId: securityId.trim() || undefined,
          issuer: issuer.trim() || undefined,
          term: term.trim() || undefined,
          amount: Number(amount),
          coupon: coupon ? Number(coupon) : undefined,
          spread: spread ? Number(spread) : undefined,
          bidDate,
          payDate: payDate || undefined,
        }),
      });
      if (!res.ok) {
        const j = await res.json().catch(() => ({}));
        throw new Error(j.error || "保存失败");
      }
      reset();
      setOpen(false);
      onSaved?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => { setOpen(o); if (!o) reset(); }}>
      <Button size="sm" className="gap-1.5" onClick={() => setOpen(true)}>
        <Plus className="h-4 w-4" />
        录入{type === "participated" ? "参与" : "中标"}记录
      </Button>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>录入投标记录</DialogTitle>
          <DialogDescription>
            记录参与投标 / 中标的个券信息，用于跟踪与上市提醒。
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4 py-2">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label>记录类型</Label>
              <Select value={type} onValueChange={(v) => setType(v as BidType)}>
                <SelectTrigger>
                  <SelectValue>{TYPE_LABELS[type]}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="participated" label="参与投标">参与投标</SelectItem>
                  <SelectItem value="won" label="中标">中标</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>投标日期</Label>
              <Input type="date" value={bidDate} onChange={(e) => setBidDate(e.target.value)} />
            </div>
          </div>
          <div className="space-y-1.5">
            <Label className="after:ml-0.5 after:text-destructive after:content-['*']">债券简称</Label>
            <Input placeholder="如 26华能集MTN001" value={bondName} onChange={(e) => setBondName(e.target.value)} />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label>债券代码</Label>
              <Input placeholder="如 102680123.IB" value={securityId} onChange={(e) => setSecurityId(e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label>期限</Label>
              <Input placeholder="如 3Y / 5+5Y" value={term} onChange={(e) => setTerm(e.target.value)} />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label className="after:ml-0.5 after:text-destructive after:content-['*']">金额（亿元）</Label>
              <Input type="number" step="0.1" min="0" placeholder="如 0.5" value={amount} onChange={(e) => setAmount(e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label>票面利率 %</Label>
              <Input type="number" step="0.01" placeholder="如 1.98" value={coupon} onChange={(e) => setCoupon(e.target.value)} />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label>利差（bp）</Label>
              <Input type="number" step="0.1" placeholder="票面-预测" value={spread} onChange={(e) => setSpread(e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label>缴款日</Label>
              <Input type="date" value={payDate} onChange={(e) => setPayDate(e.target.value)} />
            </div>
          </div>
          <div className="space-y-1.5">
            <Label>发行人</Label>
            <Input placeholder="发行人全称" value={issuer} onChange={(e) => setIssuer(e.target.value)} />
          </div>
          {error && <p className="text-sm text-destructive">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>取消</Button>
          <Button onClick={submit} disabled={saving}>
            {saving ? "保存中…" : "保存"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
