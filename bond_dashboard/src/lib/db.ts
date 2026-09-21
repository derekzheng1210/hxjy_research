// SQLite 数据库单例（better-sqlite3）—— 仅服务端 Route Handler / 构建脚本使用
// 库文件：<项目根>/data/bond.db；WAL 模式支持团队读写并发。
// 首次启动若 bids/messages 表为空而 data/store/*.json 有历史数据，则自动全量导入。
import { DATA_DIR } from "@/lib/data-dir";
import fs from "node:fs";
import path from "node:path";
import Database from "better-sqlite3";

let db: Database.Database | null = null;

export function getDb(): Database.Database {
  if (!db) {
    const dir = DATA_DIR;
    fs.mkdirSync(dir, { recursive: true });
    db = new Database(path.join(dir, "bond.db"));
    db.pragma("journal_mode = WAL");
    db.pragma("busy_timeout = 5000");
    migrate(db);
  }
  return db;
}

function migrate(d: Database.Database) {
  d.exec(`
  CREATE TABLE IF NOT EXISTS bids(
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    bondName TEXT, securityId TEXT, issuer TEXT, term TEXT,
    amount REAL NOT NULL, coupon REAL, spread REAL, yy TEXT,
    bidDate TEXT NOT NULL, payDate TEXT, listDate TEXT, note TEXT,
    author TEXT,
    createdAt TEXT NOT NULL, updatedAt TEXT
  );
  CREATE TABLE IF NOT EXISTS messages(
    id TEXT PRIMARY KEY,
    author TEXT NOT NULL,
    content TEXT NOT NULL,
    createdAt TEXT NOT NULL
  );
  CREATE INDEX IF NOT EXISTS idx_bids_type ON bids(type);
  CREATE INDEX IF NOT EXISTS idx_bids_bidDate ON bids(bidDate);
  `);

  importLegacyJson(d);
}

/** 历史 JSON 导入：bids/messages 表为空且对应 JSON 存在时导入 */
function importLegacyJson(d: Database.Database) {
  const dir = path.join(DATA_DIR, "store");
  const bidCount = (d.prepare("SELECT COUNT(*) c FROM bids").get() as { c: number }).c;
  if (bidCount === 0) {
    try {
      const p = path.join(dir, "bids.json");
      if (fs.existsSync(p)) {
        const rows = JSON.parse(fs.readFileSync(p, "utf8")) as Record<string, unknown>[];
        const ins = d.prepare(`INSERT OR REPLACE INTO bids(
          id, type, bondName, securityId, issuer, term, amount, coupon, spread, yy,
          bidDate, payDate, listDate, note, author, createdAt, updatedAt
        ) VALUES(@id,@type,@bondName,@securityId,@issuer,@term,@amount,@coupon,@spread,@yy,
          @bidDate,@payDate,@listDate,@note,@author,@createdAt,@updatedAt)`);
        const tx = d.transaction((list: Record<string, unknown>[]) => {
          for (const r of list) {
            ins.run({
              id: r.id,
              type: r.type ?? "participated",
              bondName: r.bondName ?? "",
              securityId: r.securityId ?? null,
              issuer: r.issuer ?? null,
              term: r.term ?? null,
              amount: Number(r.amount) || 0,
              coupon: r.coupon ?? null,
              spread: r.spread ?? null,
              yy: r.yy ?? null,
              bidDate: r.bidDate ?? "",
              payDate: r.payDate ?? null,
              listDate: r.listDate ?? null,
              note: r.note ?? null,
              author: r.author ?? null,
              createdAt: r.createdAt ?? new Date().toISOString(),
              updatedAt: r.updatedAt ?? null,
            });
          }
        });
        tx(rows);
        console.log(`[db] 已导入历史 bids.json ${rows.length} 条`);
      }
    } catch (e) {
      console.warn("[db] bids.json 导入失败:", (e as Error).message);
    }
  }
  const msgCount = (d.prepare("SELECT COUNT(*) c FROM messages").get() as { c: number }).c;
  if (msgCount === 0) {
    try {
      const p = path.join(dir, "messages.json");
      if (fs.existsSync(p)) {
        const rows = JSON.parse(fs.readFileSync(p, "utf8")) as Record<string, unknown>[];
        const ins = d.prepare(
          "INSERT OR REPLACE INTO messages(id, author, content, createdAt) VALUES(?,?,?,?)"
        );
        const tx = d.transaction((list: Record<string, unknown>[]) => {
          for (const r of list) {
            ins.run(r.id, r.author ?? "匿名", r.content ?? "", r.createdAt ?? new Date().toISOString());
          }
        });
        tx(rows);
        console.log(`[db] 已导入历史 messages.json ${rows.length} 条`);
      }
    } catch (e) {
      console.warn("[db] messages.json 导入失败:", (e as Error).message);
    }
  }
}
