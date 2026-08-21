"""SQLite persistence for the internal knowledge base."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path


CST = timezone(timedelta(hours=8))


def now_iso() -> str:
    return datetime.now(CST).isoformat(timespec="seconds")


class SQLiteStore:
    """Small transactional repository used by the knowledge-base Blueprint.

    Connections are intentionally short-lived so Flask threads and future
    multi-process deployments do not share SQLite connection objects.
    """

    def __init__(self, path: Path, reminder_default: dict):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._schema_lock = threading.Lock()
        self._reminder_default = reminder_default
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    @contextmanager
    def transaction(self):
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        with self._schema_lock, self.connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    org TEXT NOT NULL DEFAULT '',
                    role TEXT NOT NULL CHECK(role IN ('leader','admin','member')),
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS reports (
                    id TEXT PRIMARY KEY,
                    author_id TEXT REFERENCES users(id) ON DELETE SET NULL,
                    deleted_at TEXT,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_reports_author ON reports(author_id);
                CREATE INDEX IF NOT EXISTS idx_reports_deleted ON reports(deleted_at);

                CREATE TABLE IF NOT EXISTS ratings (
                    id TEXT PRIMARY KEY,
                    report_id TEXT NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(report_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS engagement (
                    report_id TEXT NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL CHECK(kind IN ('like','view','favorite')),
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(report_id, user_id, kind)
                );

                CREATE TABLE IF NOT EXISTS qa_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    day TEXT NOT NULL,
                    question TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_qa_usage_user_day ON qa_usage(user_id, day);

                CREATE TABLE IF NOT EXISTS qa_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    question TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    sources TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS pdf_cache (
                    cache_key TEXT PRIMARY KEY,
                    report_id TEXT REFERENCES reports(id) ON DELETE CASCADE,
                    source_hash TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    generated_at TEXT NOT NULL,
                    last_accessed_at TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL DEFAULT 0,
                    conversion_version TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_pdf_cache_report ON pdf_cache(report_id);

                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    actor_type TEXT NOT NULL,
                    actor_id TEXT,
                    action TEXT NOT NULL,
                    target_type TEXT,
                    target_id TEXT,
                    detail TEXT NOT NULL DEFAULT '{}',
                    ip_address TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at);

                CREATE TABLE IF NOT EXISTS roadshow_schedule (
                    id TEXT PRIMARY KEY,
                    event_time TEXT NOT NULL,
                    end_time TEXT NOT NULL DEFAULT '',
                    format TEXT NOT NULL CHECK(format IN ('online','offline','hybrid')),
                    institution TEXT NOT NULL DEFAULT '',
                    organizer TEXT NOT NULL DEFAULT '',
                    meeting_room TEXT NOT NULL DEFAULT '',
                    tencent_meeting_id TEXT NOT NULL DEFAULT '',
                    presenter TEXT NOT NULL DEFAULT '',
                    topic TEXT NOT NULL DEFAULT '',
                    created_by TEXT,
                    created_by_name TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_roadshow_event_time ON roadshow_schedule(event_time);
                """
            )
            self._ensure_roadshow_columns(conn)
            self._set_default(conn, "reminder_config", self._reminder_default)
            self._set_default(conn, "knowledge_config", {"memberLimit": 10, "leaderLimit": 100})
            self._set_default(conn, "schema_version", 1)
            conn.commit()
        self._migrate()

    def _migrate(self) -> None:
        """按 schema_version 执行数据迁移；幂等，多进程部署下只生效一次。"""
        version = self._get_setting("schema_version", 1)
        if version >= 2:
            return
        self._backup_db()
        with self._schema_lock, self.transaction() as conn:
            version = json.loads(conn.execute(
                "SELECT payload FROM settings WHERE key='schema_version'"
            ).fetchone()["payload"])
            if version >= 2:
                return
            self._delete_monthly_ratings(conn)
            self._backfill_uploaded_by(conn)
            conn.execute(
                "INSERT INTO settings(key,payload,updated_at) VALUES('schema_version',?,?) "
                "ON CONFLICT(key) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at",
                (json.dumps(2), now_iso()),
            )

    def _backup_db(self) -> None:
        """迁移前备份整个数据库（WAL 模式下用 backup API，直接拷文件不安全）。"""
        backup_dir = self.path.parent / "migration_backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        target = backup_dir / f"knowledge_base_backup_v1_{datetime.now(CST).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.db"
        src = sqlite3.connect(self.path)
        dst = sqlite3.connect(target)
        try:
            with dst:
                src.backup(dst)
        finally:
            src.close()
            dst.close()

    @staticmethod
    def _delete_monthly_ratings(conn: sqlite3.Connection) -> int:
        """月报打分关闭：彻底删除所有月报（含回收站）的历史评分记录。"""
        monthly_ids = []
        for row in conn.execute("SELECT id,payload FROM reports").fetchall():
            try:
                payload = json.loads(row["payload"])
            except json.JSONDecodeError:
                continue
            if payload.get("reportType", "internal") == "internal" and payload.get("category") == "monthly":
                monthly_ids.append(row["id"])
        if not monthly_ids:
            return 0
        marks = ",".join("?" for _ in monthly_ids)
        cursor = conn.execute(f"DELETE FROM ratings WHERE report_id IN ({marks})", monthly_ids)
        return cursor.rowcount

    @staticmethod
    def _backfill_uploaded_by(conn: sqlite3.Connection) -> int:
        """为历史报告回填"实际上传人"。

        上传操作在 audit_log 中留有 actor 与时间戳（after_request 写入），
        以"审计时间与报告 created_at 相差 ±5 秒"匹配上传人；匹配不到则留空，
        前端展示为"未记录"。普通用户上传时报告作者即上传人，此回填同样适用。
        """
        users = {row["id"]: row["name"] for row in conn.execute("SELECT id,name FROM users")}
        audits = conn.execute(
            "SELECT actor_id,created_at FROM audit_log "
            "WHERE actor_type='user' AND action LIKE '%api_reports_upload%' ORDER BY created_at"
        ).fetchall()

        def parse_ts(value):
            try:
                return datetime.fromisoformat(value)
            except (TypeError, ValueError):
                return None

        audit_times = [(row["actor_id"], parse_ts(row["created_at"])) for row in audits]
        changed = 0
        for row in conn.execute("SELECT id,payload FROM reports").fetchall():
            try:
                payload = json.loads(row["payload"])
            except json.JSONDecodeError:
                continue
            if payload.get("uploadedById"):
                continue
            report_ts = parse_ts(payload.get("uploadedAt") or "") or parse_ts(payload.get("createdAt") or "")
            if not report_ts:
                continue
            uploader_id = ""
            for actor_id, audit_ts in audit_times:
                if audit_ts and abs((audit_ts - report_ts).total_seconds()) <= 5:
                    uploader_id = actor_id
                    break
            if not uploader_id:
                continue
            payload["uploadedById"] = uploader_id
            payload["uploadedByName"] = users.get(uploader_id, "")
            conn.execute(
                "UPDATE reports SET payload=?,updated_at=? WHERE id=?",
                (json.dumps(payload, ensure_ascii=False), now_iso(), row["id"]),
            )
            changed += 1
        return changed

    @staticmethod
    def _set_default(conn: sqlite3.Connection, key: str, value) -> None:
        conn.execute(
            "INSERT OR IGNORE INTO settings(key,payload,updated_at) VALUES(?,?,?)",
            (key, json.dumps(value, ensure_ascii=False), now_iso()),
        )

    @staticmethod
    def _decode_payload(row):
        return json.loads(row["payload"]) if row else None

    # Users
    def users(self):
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT id,name,org,role,password_hash FROM users ORDER BY id"
            )]

    def get_user(self, uid):
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id,name,org,role,password_hash FROM users WHERE id=?", (uid,)
            ).fetchone()
            return dict(row) if row else None

    def find_user_by_login(self, username):
        return self.get_user(str(username).strip().lower().replace(" ", ""))

    def add_user(self, user):
        try:
            with self.transaction() as conn:
                conn.execute(
                    "INSERT INTO users(id,name,org,role,password_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                    (user["id"], user["name"], user.get("org", ""), user["role"],
                     user["password_hash"], now_iso(), now_iso()),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def update_user(self, uid, fields):
        allowed = {"name", "org", "role", "password_hash"}
        clean = {key: value for key, value in fields.items() if key in allowed}
        if clean:
            assignments = ",".join(f"{key}=?" for key in clean)
            with self.transaction() as conn:
                conn.execute(
                    f"UPDATE users SET {assignments},updated_at=? WHERE id=?",
                    (*clean.values(), now_iso(), uid),
                )
        return self.get_user(uid)

    def delete_user(self, uid):
        with self.transaction() as conn:
            cursor = conn.execute("DELETE FROM users WHERE id=?", (uid,))
            return cursor.rowcount > 0

    # Reports
    def reports(self, include_deleted=False):
        sql = "SELECT payload FROM reports" + ("" if include_deleted else " WHERE deleted_at IS NULL")
        with self.connect() as conn:
            return [self._decode_payload(row) for row in conn.execute(sql)]

    def get_report(self, rid, include_deleted=False):
        sql = "SELECT payload FROM reports WHERE id=?"
        if not include_deleted:
            sql += " AND deleted_at IS NULL"
        with self.connect() as conn:
            return self._decode_payload(conn.execute(sql, (rid,)).fetchone())

    def add_report(self, report):
        stamp = now_iso()
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO reports(id,author_id,deleted_at,payload,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (report["id"], report.get("authorId"), report.get("deletedAt"),
                 json.dumps(report, ensure_ascii=False), stamp, stamp),
            )

    def update_report(self, rid, fields):
        with self.transaction() as conn:
            row = conn.execute("SELECT payload FROM reports WHERE id=?", (rid,)).fetchone()
            if not row:
                return None
            report = self._decode_payload(row)
            report.update(fields)
            conn.execute(
                "UPDATE reports SET author_id=?,deleted_at=?,payload=?,updated_at=? WHERE id=?",
                (report.get("authorId"), report.get("deletedAt"),
                 json.dumps(report, ensure_ascii=False), now_iso(), rid),
            )
            return report

    def delete_reports(self, ids):
        ids = list(dict.fromkeys(ids))
        if not ids:
            return
        marks = ",".join("?" for _ in ids)
        with self.transaction() as conn:
            conn.execute(f"DELETE FROM reports WHERE id IN ({marks})", ids)

    def trash_reports(self, ids, deleted_by):
        changed = 0
        for rid in ids:
            report = self.get_report(rid)
            if report:
                report.update(deletedAt=now_iso(), deletedBy=deleted_by)
                self.update_report(rid, report)
                changed += 1
        return changed

    def restore_report(self, rid):
        report = self.get_report(rid, include_deleted=True)
        if not report or not report.get("deletedAt"):
            return None
        report.pop("deletedAt", None)
        report.pop("deletedBy", None)
        return self.update_report(rid, report)

    # Ratings
    def ratings(self):
        with self.connect() as conn:
            return [self._decode_payload(row) for row in conn.execute("SELECT payload FROM ratings")]

    def has_rating(self, report_id, user_id):
        with self.connect() as conn:
            return bool(conn.execute(
                "SELECT 1 FROM ratings WHERE report_id=? AND user_id=?", (report_id, user_id)
            ).fetchone())

    def add_rating(self, record):
        try:
            with self.transaction() as conn:
                record.setdefault("createdAt", now_iso())
                conn.execute(
                    "INSERT INTO ratings(id,report_id,user_id,payload,created_at) VALUES(?,?,?,?,?)",
                    (record["id"], record["reportId"], record["userId"],
                     json.dumps(record, ensure_ascii=False), record["createdAt"]),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    add_or_update_rating = add_rating

    def reset_ratings_for_report(self, rid):
        with self.transaction() as conn:
            conn.execute("DELETE FROM ratings WHERE report_id=?", (rid,))

    delete_ratings_for_report = reset_ratings_for_report

    # Engagement
    def report_engagement(self, report_id, user_id=None):
        with self.connect() as conn:
            counts = {row["kind"]: row["count"] for row in conn.execute(
                "SELECT kind,COUNT(*) AS count FROM engagement WHERE report_id=? GROUP BY kind", (report_id,)
            )}
            mine = set()
            if user_id:
                mine = {row["kind"] for row in conn.execute(
                    "SELECT kind FROM engagement WHERE report_id=? AND user_id=?", (report_id, user_id)
                )}
        return {
            "likeCount": counts.get("like", 0), "viewCount": counts.get("view", 0),
            "favoriteCount": counts.get("favorite", 0), "likedByMe": "like" in mine,
            "favoritedByMe": "favorite" in mine,
        }

    def _toggle_engagement(self, report_id, user_id, kind):
        with self.transaction() as conn:
            exists = conn.execute(
                "SELECT 1 FROM engagement WHERE report_id=? AND user_id=? AND kind=?",
                (report_id, user_id, kind),
            ).fetchone()
            if exists:
                conn.execute(
                    "DELETE FROM engagement WHERE report_id=? AND user_id=? AND kind=?",
                    (report_id, user_id, kind),
                )
                enabled = False
            else:
                conn.execute(
                    "INSERT INTO engagement(report_id,user_id,kind,created_at) VALUES(?,?,?,?)",
                    (report_id, user_id, kind, now_iso()),
                )
                enabled = True
        result = self.report_engagement(report_id, user_id)
        result["liked" if kind == "like" else "favorited"] = enabled
        return result

    def toggle_like(self, report_id, user_id):
        return self._toggle_engagement(report_id, user_id, "like")

    def toggle_favorite(self, report_id, user_id):
        return self._toggle_engagement(report_id, user_id, "favorite")

    def add_view(self, report_id, user_id):
        with self.transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO engagement(report_id,user_id,kind,created_at) VALUES(?,?,?,?)",
                (report_id, user_id, "view", now_iso()),
            )
        return self.report_engagement(report_id, user_id)

    # Settings and knowledge search
    def _get_setting(self, key, fallback=None):
        with self.connect() as conn:
            row = conn.execute("SELECT payload FROM settings WHERE key=?", (key,)).fetchone()
            return json.loads(row["payload"]) if row else fallback

    def _set_setting(self, key, value):
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO settings(key,payload,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at",
                (key, json.dumps(value, ensure_ascii=False), now_iso()),
            )

    def reminder_config(self):
        return self._get_setting("reminder_config", self._reminder_default)

    def set_reminder_config(self, config):
        self._set_setting("reminder_config", config)

    def knowledge_config(self):
        return self._get_setting("knowledge_config", {"memberLimit": 10, "leaderLimit": 100})

    def set_knowledge_config(self, config):
        self._set_setting("knowledge_config", config)

    def qa_usage_today(self, user_id, day):
        with self.connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM qa_usage WHERE user_id=? AND day=?", (user_id, day)
            ).fetchone()[0]

    def add_qa_usage(self, user_id, day, question):
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO qa_usage(user_id,day,question,created_at) VALUES(?,?,?,?)",
                (user_id, day, question[:300], now_iso()),
            )

    def add_qa_history(self, user_id, question, answer, sources):
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO qa_history(user_id,question,answer,sources,created_at) VALUES(?,?,?,?,?)",
                (user_id, question[:300], answer, json.dumps(sources, ensure_ascii=False), now_iso()),
            )

    def qa_history_for_user(self, user_id, limit=50):
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT question,answer,sources,created_at FROM qa_history WHERE user_id=? "
                "ORDER BY id DESC LIMIT ?", (user_id, limit),
            ).fetchall()
        return [{"question": row["question"], "answer": row["answer"],
                 "sources": json.loads(row["sources"]), "createdAt": row["created_at"]}
                for row in reversed(rows)]

    def clear_qa_history(self, user_id):
        with self.transaction() as conn:
            cursor = conn.execute("DELETE FROM qa_history WHERE user_id=?", (user_id,))
            return cursor.rowcount

    def admin_password_hash(self):
        return self._get_setting("admin_password_hash")

    def set_admin_password_hash(self, password_hash):
        self._set_setting("admin_password_hash", password_hash)

    # Permanent PDF cache index
    def get_pdf_cache(self, cache_key):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM pdf_cache WHERE cache_key=?", (cache_key,)).fetchone()
            return dict(row) if row else None

    def touch_pdf_cache(self, cache_key):
        with self.transaction() as conn:
            conn.execute("UPDATE pdf_cache SET last_accessed_at=? WHERE cache_key=?", (now_iso(), cache_key))

    def upsert_pdf_cache(self, cache_key, report_id, source_hash, file_name, size_bytes, version):
        stamp = now_iso()
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO pdf_cache(cache_key,report_id,source_hash,file_name,generated_at,last_accessed_at,size_bytes,conversion_version) "
                "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(cache_key) DO UPDATE SET "
                "report_id=excluded.report_id,source_hash=excluded.source_hash,file_name=excluded.file_name,"
                "last_accessed_at=excluded.last_accessed_at,size_bytes=excluded.size_bytes,conversion_version=excluded.conversion_version",
                (cache_key, report_id, source_hash, file_name, stamp, stamp, size_bytes, version),
            )

    def pdf_caches(self):
        with self.connect() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM pdf_cache ORDER BY generated_at DESC")]

    def delete_pdf_cache(self, cache_key):
        with self.transaction() as conn:
            cursor = conn.execute("DELETE FROM pdf_cache WHERE cache_key=?", (cache_key,))
            return cursor.rowcount

    def delete_pdf_caches_for_report(self, report_id):
        with self.transaction() as conn:
            rows = [row[0] for row in conn.execute("SELECT cache_key FROM pdf_cache WHERE report_id=?", (report_id,))]
            conn.execute("DELETE FROM pdf_cache WHERE report_id=?", (report_id,))
            return rows

    def clear_pdf_caches(self):
        with self.transaction() as conn:
            keys = [row[0] for row in conn.execute("SELECT cache_key FROM pdf_cache")]
            conn.execute("DELETE FROM pdf_cache")
            return keys

    @staticmethod
    def _ensure_roadshow_columns(conn: sqlite3.Connection) -> None:
        """旧库的 roadshow_schedule 表缺少后续新增列，按需补齐（幂等）。"""
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(roadshow_schedule)")}
        if not columns:
            return
        for column in ("end_time", "institution", "organizer"):
            if column not in columns:
                conn.execute(f"ALTER TABLE roadshow_schedule ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")

    # Roadshow schedule（路演安排表）
    def roadshow_items(self, date_from, date_to):
        """返回窗口 [date_from, date_to]（含端点，格式 YYYY-MM-DD）内的路演安排。"""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM roadshow_schedule "
                "WHERE substr(event_time,1,10) BETWEEN ? AND ? ORDER BY event_time",
                (date_from, date_to),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_roadshow_item(self, item_id):
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM roadshow_schedule WHERE id=?", (item_id,)
            ).fetchone()
        return dict(row) if row else None

    def add_roadshow_item(self, item):
        stamp = now_iso()
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO roadshow_schedule"
                "(id,event_time,end_time,format,institution,organizer,meeting_room,tencent_meeting_id,presenter,topic,"
                "created_by,created_by_name,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (item["id"], item["eventTime"], item.get("endTime", ""), item["format"],
                 item.get("institution", ""), item.get("organizer", ""), item.get("meetingRoom", ""),
                 item.get("tencentMeetingId", ""), item.get("presenter", ""), item.get("topic", ""),
                 item.get("createdById", ""), item.get("createdByName", ""), stamp, stamp),
            )

    def delete_roadshow_item(self, item_id):
        with self.transaction() as conn:
            cursor = conn.execute("DELETE FROM roadshow_schedule WHERE id=?", (item_id,))
            return cursor.rowcount > 0

    def audit(self, actor_type, actor_id, action, target_type=None, target_id=None,
              detail=None, ip_address=None):
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO audit_log(actor_type,actor_id,action,target_type,target_id,detail,ip_address,created_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (actor_type, actor_id, action, target_type, target_id,
                 json.dumps(detail or {}, ensure_ascii=False), ip_address, now_iso()),
            )
