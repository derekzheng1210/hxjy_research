// 数据存储层：SQLite（better-sqlite3）为写入权威，data/store/*.json 保持同步镜像
// （供 python 脚本（sync_bids.py / daily_refresh 统计）与快照/备份读取）。
import { DATA_DIR } from "@/lib/data-dir";
import fs from "node:fs";
import path from "node:path";
import { randomUUID } from "node:crypto";
import { getDb } from "./db";
import type { BidRecord, Message } from "./types";

const STORE_DIR = path.join(DATA_DIR, "store");
const BIDS_FILE = "bids.json";
const MESSAGES_FILE = "messages.json";

function ensureDir() {
  fs.mkdirSync(STORE_DIR, { recursive: true });
}

function writeJson(file: string, data: unknown) {
  try {
    ensureDir();
    const p = path.join(STORE_DIR, file);
    const tmp = p + ".tmp";
    fs.writeFileSync(tmp, JSON.stringify(data, null, 2), "utf8");
    fs.renameSync(tmp, p);
  } catch (e) {
    // 镜像写失败不影响主库写入（仅记录）
    console.warn("[store] JSON 镜像写失败:", (e as Error).message);
  }
}

// ==================== 投标记录（参与/中标） ====================

function rowToBid(r: Record<string, unknown>): BidRecord {
  return {
    id: String(r.id),
    type: r.type as BidRecord["type"],
    bondName: String(r.bondName ?? ""),
    securityId: r.securityId != null ? String(r.securityId) : undefined,
    issuer: r.issuer != null ? String(r.issuer) : undefined,
    term: r.term != null ? String(r.term) : undefined,
    amount: Number(r.amount) || 0,
    coupon: r.coupon != null ? Number(r.coupon) : undefined,
    spread: r.spread != null ? Number(r.spread) : undefined,
    yy: r.yy != null ? String(r.yy) : undefined,
    bidDate: String(r.bidDate ?? ""),
    payDate: r.payDate != null ? String(r.payDate) : undefined,
    listDate: r.listDate != null ? String(r.listDate) : undefined,
    note: r.note != null ? String(r.note) : undefined,
    author: r.author != null ? String(r.author) : undefined,
    createdAt: String(r.createdAt),
  };
}

function dumpBids() {
  const db = getDb();
  const rows = db.prepare("SELECT * FROM bids ORDER BY createdAt ASC").all() as Record<string, unknown>[];
  writeJson(BIDS_FILE, rows.map(rowToBid));
}

export function getBids(): BidRecord[] {
  const db = getDb();
  const rows = db.prepare("SELECT * FROM bids").all() as Record<string, unknown>[];
  return rows.map(rowToBid);
}

/** author：录入人显示名（''/undefined=Excel 导入或脚本录入） */
export function addBid(
  bid: Omit<BidRecord, "id" | "createdAt"> & { author?: string }
): BidRecord {
  const db = getDb();
  const record: BidRecord = {
    ...bid,
    id: randomUUID(),
    createdAt: new Date().toISOString(),
  };
  db.prepare(
    `INSERT INTO bids(id, type, bondName, securityId, issuer, term, amount, coupon, spread, yy,
       bidDate, payDate, listDate, note, author, createdAt, updatedAt)
     VALUES(@id,@type,@bondName,@securityId,@issuer,@term,@amount,@coupon,@spread,@yy,
       @bidDate,@payDate,@listDate,@note,@author,@createdAt,@updatedAt)`
  ).run({
    id: record.id,
    type: record.type,
    bondName: record.bondName,
    securityId: record.securityId ?? null,
    issuer: record.issuer ?? null,
    term: record.term ?? null,
    amount: record.amount,
    coupon: record.coupon ?? null,
    spread: record.spread ?? null,
    yy: record.yy ?? null,
    bidDate: record.bidDate,
    payDate: record.payDate ?? null,
    listDate: record.listDate ?? null,
    note: record.note ?? null,
    author: record.author ?? null,
    createdAt: record.createdAt,
    updatedAt: null,
  });
  dumpBids();
  return record;
}

export function updateBid(id: string, patch: Partial<BidRecord>): BidRecord | null {
  const db = getDb();
  const cur = db.prepare("SELECT * FROM bids WHERE id = ?").get(id) as Record<string, unknown> | undefined;
  if (!cur) return null;
  const next: BidRecord = { ...rowToBid(cur), ...patch, id };
  db.prepare(
    `UPDATE bids SET type=@type, bondName=@bondName, securityId=@securityId, issuer=@issuer,
       term=@term, amount=@amount, coupon=@coupon, spread=@spread, yy=@yy,
       bidDate=@bidDate, payDate=@payDate, listDate=@listDate, note=@note, updatedAt=@updatedAt
     WHERE id=@id`
  ).run({
    id: next.id,
    type: next.type,
    bondName: next.bondName,
    securityId: next.securityId ?? null,
    issuer: next.issuer ?? null,
    term: next.term ?? null,
    amount: next.amount,
    coupon: next.coupon ?? null,
    spread: next.spread ?? null,
    yy: next.yy ?? null,
    bidDate: next.bidDate,
    payDate: next.payDate ?? null,
    listDate: next.listDate ?? null,
    note: next.note ?? null,
    updatedAt: new Date().toISOString(),
  });
  dumpBids();
  return next;
}

export function deleteBid(id: string): boolean {
  const db = getDb();
  const info = db.prepare("DELETE FROM bids WHERE id = ?").run(id);
  if (info.changes === 0) return false;
  dumpBids();
  return true;
}

// ==================== 信息交流栏 ====================

function rowToMessage(r: Record<string, unknown>): Message {
  return {
    id: String(r.id),
    author: String(r.author ?? "匿名"),
    content: String(r.content ?? ""),
    createdAt: String(r.createdAt),
  };
}

function dumpMessages() {
  const db = getDb();
  const rows = db.prepare("SELECT * FROM messages ORDER BY createdAt ASC").all() as Record<string, unknown>[];
  writeJson(MESSAGES_FILE, rows.map(rowToMessage));
}

export function getMessages(): Message[] {
  const db = getDb();
  const rows = db.prepare("SELECT * FROM messages").all() as Record<string, unknown>[];
  return rows.map(rowToMessage);
}

export function addMessage(msg: { author: string; content: string }): Message {
  const db = getDb();
  const record: Message = {
    id: randomUUID(),
    author: msg.author || "匿名",
    content: msg.content,
    createdAt: new Date().toISOString(),
  };
  db.prepare("INSERT INTO messages(id, author, content, createdAt) VALUES(?,?,?,?)").run(
    record.id,
    record.author,
    record.content,
    record.createdAt
  );
  dumpMessages();
  return record;
}

export function deleteMessage(id: string): boolean {
  const db = getDb();
  const info = db.prepare("DELETE FROM messages WHERE id = ?").run(id);
  if (info.changes === 0) return false;
  dumpMessages();
  return true;
}
