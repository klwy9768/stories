# -*- coding: utf-8 -*-
# Copyright (c) 2026 刘晨宇 <https://github.com/klwy9768/stories>
# SPDX-License-Identifier: MIT
"""Stories 数据层：SQLite（角色/会话/消息/知识库/表情/背景/设置/队列）"""
import json
import os
import sqlite3
import threading
import time

DATA_DIR = os.environ.get("STORIES_DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
DB_PATH = os.environ.get("STORIES_DB_PATH", os.path.join(DATA_DIR, "stories.db"))

# 每线程独立连接：避免多线程共享单一事务上下文互相提交/回滚（WAL + busy_timeout 保证并发安全）
_tls = threading.local()
_conn_registry = {}          # 线程 ident -> conn（供后台回收已结束线程的连接，防 FD 泄漏）
_conn_reg_lock = threading.Lock()


def get_conn():
    conn = getattr(_tls, "conn", None)
    if conn is None:
        os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")   # 多线程并发写时等待而非直接报错
        conn.execute("PRAGMA synchronous=NORMAL")
        _tls.conn = conn
        with _conn_reg_lock:
            _conn_registry[threading.get_ident()] = conn
    return conn


def reap_dead_conns() -> int:
    """回收已结束线程遗留的数据库连接（由后台循环定期调用），返回回收数量"""
    alive = {t.ident for t in threading.enumerate()}
    dead = []
    with _conn_reg_lock:
        for ident, conn in list(_conn_registry.items()):
            if ident not in alive:
                dead.append(conn)
                _conn_registry.pop(ident, None)
    n = 0
    for conn in dead:
        try:
            conn.close()
            n += 1
        except Exception:
            pass
    if n:
        print(f"[db] 回收已退出线程的连接 {n} 个", flush=True)
    return n


def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS characters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            avatar TEXT DEFAULT '',
            description TEXT DEFAULT '',
            persona_full TEXT DEFAULT '',
            story TEXT DEFAULT '',
            fields_json TEXT DEFAULT '{}',
            greeting TEXT DEFAULT '',
            model TEXT DEFAULT '',
            temperature REAL DEFAULT 0.9,
            sort_order INTEGER DEFAULT 0,
            enabled INTEGER DEFAULT 1,
            created_at REAL,
            updated_at REAL
        );
        CREATE TABLE IF NOT EXISTS chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            character_id INTEGER NOT NULL UNIQUE,
            photo_enabled INTEGER DEFAULT 0,
            voice_enabled INTEGER DEFAULT 0,
            sticker_enabled INTEGER DEFAULT 1,
            video_enabled INTEGER DEFAULT 0,
            wake_enabled INTEGER DEFAULT 1,
            wake_ts REAL DEFAULT 0,
            wake_topic TEXT DEFAULT '',
            wake_pending_ts REAL DEFAULT 0,
            wake_last_reply_ts REAL DEFAULT 0,
            wake_retry INTEGER DEFAULT 0,
            cheat_memory TEXT DEFAULT '[]',
            commit_memory TEXT DEFAULT '[]',
            moment_memory TEXT DEFAULT '[]',
            reminder_enabled INTEGER DEFAULT 0,
            reminder_target INTEGER DEFAULT 5,
            moments_auto INTEGER DEFAULT 0,
            moments_react INTEGER DEFAULT 1,
            img_desc_enabled INTEGER DEFAULT 1,
            background TEXT DEFAULT '',
            summary TEXT DEFAULT '',
            summarized_until INTEGER DEFAULT 0,
            last_msg_at REAL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            character_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            kind TEXT DEFAULT 'text',
            content TEXT DEFAULT '',
            media_url TEXT DEFAULT '',
            img_desc TEXT DEFAULT '',
            duration REAL DEFAULT 0,
            created_at REAL
        );
        CREATE INDEX IF NOT EXISTS idx_messages_char ON messages(character_id, id);
        CREATE TABLE IF NOT EXISTS pending_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            character_id INTEGER NOT NULL,
            content TEXT DEFAULT '',
            kind TEXT DEFAULT 'reply',
            media_url TEXT DEFAULT '',
            duration REAL DEFAULT 0,
            ref_id INTEGER DEFAULT 0,
            ts REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_pending_char ON pending_tasks(character_id, kind, ts);
        CREATE TABLE IF NOT EXISTS reminder_state (
            character_id INTEGER PRIMARY KEY,
            week TEXT DEFAULT '',
            count INTEGER DEFAULT 0,
            last_reminder_ts REAL DEFAULT 0,
            last_judge_ts REAL DEFAULT 0,
            last_count_ts REAL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS moments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            author TEXT DEFAULT 'character',
            character_id INTEGER DEFAULT 0,
            content TEXT DEFAULT '',
            images TEXT DEFAULT '[]',
            created_at REAL,
            liked_by_user INTEGER DEFAULT 0,
            liked_by_character INTEGER DEFAULT 0,
            source TEXT DEFAULT 'auto',
            visible_to TEXT DEFAULT '[]',
            hidden_from TEXT DEFAULT '[]'
        );
        CREATE TABLE IF NOT EXISTS moment_comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            moment_id INTEGER NOT NULL,
            role TEXT DEFAULT 'user',
            character_id INTEGER DEFAULT 0,
            content TEXT DEFAULT '',
            created_at REAL,
            reply_to INTEGER DEFAULT 0,
            seen INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_moment_comments ON moment_comments(moment_id, id);
        CREATE TABLE IF NOT EXISTS moment_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            character_id INTEGER DEFAULT 0,
            kind TEXT DEFAULT '',
            moment_id INTEGER DEFAULT 0,
            due_ts REAL DEFAULT 0,
            payload TEXT DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS moment_plans (
            date TEXT NOT NULL,
            character_id INTEGER NOT NULL,
            planned INTEGER DEFAULT 0,
            post_time TEXT DEFAULT '',
            topic TEXT DEFAULT '',
            created_at REAL,
            PRIMARY KEY (date, character_id)
        );
        CREATE TABLE IF NOT EXISTS moment_likes (
            moment_id INTEGER NOT NULL,
            character_id INTEGER NOT NULL,
            created_at REAL,
            PRIMARY KEY (moment_id, character_id)
        );
        CREATE TABLE IF NOT EXISTS kb_docs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL,
            character_id INTEGER DEFAULT 0,
            filename TEXT NOT NULL,
            stored_name TEXT DEFAULT '',
            status TEXT DEFAULT 'parsing',
            error TEXT DEFAULT '',
            chunk_count INTEGER DEFAULT 0,
            hash TEXT DEFAULT '',
            uploaded_at REAL
        );
        CREATE TABLE IF NOT EXISTS kb_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id INTEGER NOT NULL,
            scope TEXT NOT NULL,
            character_id INTEGER DEFAULT 0,
            heading TEXT DEFAULT '',
            content TEXT NOT NULL,
            ord INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_chunks_doc ON kb_chunks(doc_id);
        CREATE INDEX IF NOT EXISTS idx_chunks_scope ON kb_chunks(scope, character_id);
        CREATE TABLE IF NOT EXISTS stickers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file TEXT NOT NULL,
            label TEXT DEFAULT '',
            tags TEXT DEFAULT '',
            sort_order INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS backgrounds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file TEXT NOT NULL,
            name TEXT DEFAULT '',
            sort_order INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT DEFAULT '',
            auto_enabled INTEGER DEFAULT 1,
            created_at REAL
        );
        CREATE TABLE IF NOT EXISTS group_members (
            group_id INTEGER NOT NULL,
            cid INTEGER NOT NULL,
            joined_at REAL,
            PRIMARY KEY (group_id, cid)
        );
        CREATE TABLE IF NOT EXISTS group_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL,
            role TEXT DEFAULT 'character',
            cid INTEGER DEFAULT 0,
            kind TEXT DEFAULT 'text',
            content TEXT DEFAULT '',
            media_url TEXT DEFAULT '',
            img_desc TEXT DEFAULT '',
            created_at REAL
        );
        CREATE INDEX IF NOT EXISTS idx_group_messages ON group_messages(group_id, id);
        CREATE TABLE IF NOT EXISTS group_state (
            group_id INTEGER PRIMARY KEY,
            auto_count_day TEXT DEFAULT '',
            auto_count INTEGER DEFAULT 0,
            last_auto_ts REAL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS favorites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT DEFAULT '',
            kind TEXT DEFAULT 'file',
            ext TEXT DEFAULT '',
            size INTEGER DEFAULT 0,
            url TEXT DEFAULT '',
            created_at REAL
        );
        CREATE TABLE IF NOT EXISTS music (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT DEFAULT '',
            filename TEXT DEFAULT '',
            size INTEGER DEFAULT 0,
            url TEXT DEFAULT '',
            created_at REAL
        );
        CREATE TABLE IF NOT EXISTS news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            day TEXT DEFAULT '',
            category TEXT DEFAULT '',
            title TEXT DEFAULT '',
            summary TEXT DEFAULT '',
            url TEXT DEFAULT '',
            source TEXT DEFAULT '',
            image TEXT DEFAULT '',
            published TEXT DEFAULT '',
            content TEXT DEFAULT '',
            video TEXT DEFAULT '',
            created_at REAL
        );
        CREATE INDEX IF NOT EXISTS idx_news_day ON news(day, category);
        CREATE TABLE IF NOT EXISTS worlds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT DEFAULT '',
            created_at REAL
        );
        CREATE TABLE IF NOT EXISTS world_members (
            world_id INTEGER NOT NULL,
            cid INTEGER NOT NULL,
            PRIMARY KEY (world_id, cid)
        );
        CREATE TABLE IF NOT EXISTS world_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            world_id INTEGER NOT NULL,
            sender TEXT DEFAULT 'system',
            source_cid INTEGER DEFAULT 0,
            content TEXT DEFAULT '',
            created_at REAL
        );
        CREATE INDEX IF NOT EXISTS idx_world_events ON world_events(world_id, id);
        CREATE TABLE IF NOT EXISTS mini_kv (
            app TEXT NOT NULL,
            k TEXT NOT NULL,
            value TEXT DEFAULT '',
            updated_at REAL,
            PRIMARY KEY (app, k)
        );
        CREATE TABLE IF NOT EXISTS mini_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            app TEXT DEFAULT '',
            content TEXT DEFAULT '',
            cid INTEGER DEFAULT 0,
            created_at REAL
        );
        CREATE INDEX IF NOT EXISTS idx_mini_events_created ON mini_events(created_at);
        """
    )
    conn.commit()
    # 迁移：pending_tasks 补充 ref_id（老库升级）
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(pending_tasks)")}
        if "ref_id" not in cols:
            conn.execute("ALTER TABLE pending_tasks ADD COLUMN ref_id INTEGER DEFAULT 0")
            conn.commit()
    except Exception:
        pass
    # 迁移：chats 补充 video_enabled（老库升级）
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(chats)")}
        if "video_enabled" not in cols:
            conn.execute("ALTER TABLE chats ADD COLUMN video_enabled INTEGER DEFAULT 0")
            conn.commit()
    except Exception:
        pass
    # 迁移：characters 补充 story（故事背景，老库升级）
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(characters)")}
        if "story" not in cols:
            conn.execute("ALTER TABLE characters ADD COLUMN story TEXT DEFAULT ''")
            conn.commit()
    except Exception:
        pass
    # 迁移：messages 补充 img_desc（图片文字描述，老库升级）
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)")}
        if "img_desc" not in cols:
            conn.execute("ALTER TABLE messages ADD COLUMN img_desc TEXT DEFAULT ''")
            conn.commit()
    except Exception:
        pass
    # 迁移：news 补充 content（新闻全文，老库升级）
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(news)")}
        if cols and "content" not in cols:
            conn.execute("ALTER TABLE news ADD COLUMN content TEXT DEFAULT ''")
            conn.commit()
        if cols and "video" not in cols:
            conn.execute("ALTER TABLE news ADD COLUMN video TEXT DEFAULT ''")
            conn.commit()
    except Exception:
        pass
    # 迁移：group_messages 补充 img_desc（群聊图片描述，老库升级）
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(group_messages)")}
        if cols and "img_desc" not in cols:
            conn.execute("ALTER TABLE group_messages ADD COLUMN img_desc TEXT DEFAULT ''")
            conn.commit()
    except Exception:
        pass
    # 迁移：chats 补充主动回复字段，老库升级
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(chats)")}
        for name, ddl in (
            ("wake_enabled", "ALTER TABLE chats ADD COLUMN wake_enabled INTEGER DEFAULT 1"),
            ("wake_ts", "ALTER TABLE chats ADD COLUMN wake_ts REAL DEFAULT 0"),
            ("wake_topic", "ALTER TABLE chats ADD COLUMN wake_topic TEXT DEFAULT ''"),
            ("wake_pending_ts", "ALTER TABLE chats ADD COLUMN wake_pending_ts REAL DEFAULT 0"),
            ("wake_last_reply_ts", "ALTER TABLE chats ADD COLUMN wake_last_reply_ts REAL DEFAULT 0"),
            ("wake_retry", "ALTER TABLE chats ADD COLUMN wake_retry INTEGER DEFAULT 0"),
            ("wake_day", "ALTER TABLE chats ADD COLUMN wake_day TEXT DEFAULT ''"),
            ("wake_count", "ALTER TABLE chats ADD COLUMN wake_count INTEGER DEFAULT 0"),
            ("cheat_memory", "ALTER TABLE chats ADD COLUMN cheat_memory TEXT DEFAULT '[]'"),
            ("commit_memory", "ALTER TABLE chats ADD COLUMN commit_memory TEXT DEFAULT '[]'"),
            ("long_memory", "ALTER TABLE chats ADD COLUMN long_memory TEXT DEFAULT '[]'"),
            ("reply_done_ref", "ALTER TABLE chats ADD COLUMN reply_done_ref INTEGER DEFAULT 0"),
            ("moment_memory", "ALTER TABLE chats ADD COLUMN moment_memory TEXT DEFAULT '[]'"),
            ("reminder_enabled", "ALTER TABLE chats ADD COLUMN reminder_enabled INTEGER DEFAULT 0"),
            ("reminder_target", "ALTER TABLE chats ADD COLUMN reminder_target INTEGER DEFAULT 5"),
            ("moments_auto", "ALTER TABLE chats ADD COLUMN moments_auto INTEGER DEFAULT 0"),
            ("moments_react", "ALTER TABLE chats ADD COLUMN moments_react INTEGER DEFAULT 1"),
            ("img_desc_enabled", "ALTER TABLE chats ADD COLUMN img_desc_enabled INTEGER DEFAULT 1"),
            ("voice_desc_enabled", "ALTER TABLE chats ADD COLUMN voice_desc_enabled INTEGER DEFAULT 1"),
        ):
            if name not in cols:
                try:
                    conn.execute(ddl)
                except Exception as e:
                    print(f"[db] 迁移列 {name} 失败（继续后续迁移）: {e}", flush=True)
        conn.commit()
    except Exception:
        pass
    # 迁移：moment_comments 补充 character_id（记录评论者角色，老库升级）
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(moment_comments)")}
        if "character_id" not in cols:
            conn.execute("ALTER TABLE moment_comments ADD COLUMN character_id INTEGER DEFAULT 0")
            conn.commit()
    except Exception:
        pass
    # 迁移：moments 补充可见范围（visible_to / hidden_from，老库升级）
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(moments)")}
        for name, ddl in (
            ("visible_to", "ALTER TABLE moments ADD COLUMN visible_to TEXT DEFAULT '[]'"),
            ("hidden_from", "ALTER TABLE moments ADD COLUMN hidden_from TEXT DEFAULT '[]'"),
        ):
            if name not in cols:
                try:
                    conn.execute(ddl)
                except Exception as e:
                    print(f"[db] 迁移列 {name} 失败（继续后续迁移）: {e}", flush=True)
        conn.commit()
    except Exception:
        pass
    # 迁移：热点查询索引（性能优化）
    try:
        for ddl in (
            "CREATE INDEX IF NOT EXISTS idx_news_url ON news(url)",
            "CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at)",
            "CREATE INDEX IF NOT EXISTS idx_moment_tasks_due ON moment_tasks(due_ts)",
            "CREATE INDEX IF NOT EXISTS idx_moments_author ON moments(author, id)",
            "CREATE INDEX IF NOT EXISTS idx_moment_comments_role ON moment_comments(role, id)",
            "CREATE INDEX IF NOT EXISTS idx_kb_docs_scope ON kb_docs(scope, character_id)",
            "CREATE INDEX IF NOT EXISTS idx_moments_created ON moments(created_at)",
            "CREATE INDEX IF NOT EXISTS idx_moment_comments_created ON moment_comments(created_at)",
            "CREATE INDEX IF NOT EXISTS idx_world_events_created ON world_events(created_at)",
        ):
            try:
                conn.execute(ddl)
            except Exception as e:
                print(f"[db] 建索引失败（继续）: {e}", flush=True)
        conn.commit()
    except Exception:
        pass
    # 迁移：group_state 补充已读水位（群聊未读）
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(group_state)")}
        if cols and "last_read_id" not in cols:
            conn.execute("ALTER TABLE group_state ADD COLUMN last_read_id INTEGER DEFAULT 0")
            conn.commit()
    except Exception:
        pass
    # 迁移：moments 补充 seen（新动态未读角标）
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(moments)")}
        if cols and "seen" not in cols:
            conn.execute("ALTER TABLE moments ADD COLUMN seen INTEGER DEFAULT 0")
            conn.commit()
    except Exception:
        pass
    # 迁移：mini_events 补充 cid（事件归属角色，0=通用；天气/纪念日按角色隔离）
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(mini_events)")}
        if cols and "cid" not in cols:
            conn.execute("ALTER TABLE mini_events ADD COLUMN cid INTEGER DEFAULT 0")
            conn.commit()
    except Exception:
        pass
    # 迁移（一次性）：群已读水位/朋友圈已读对齐 —— 升级后历史消息不刷成未读
    try:
        if not conn.execute("SELECT 1 FROM settings WHERE key='read_backfill_v1'").fetchone():
            for g in conn.execute("SELECT id FROM groups").fetchall():
                gid = int(g["id"])
                mx = conn.execute(
                    "SELECT COALESCE(MAX(id),0) AS m FROM group_messages WHERE group_id=?", (gid,)).fetchone()
                mxv = int((mx["m"] if mx else 0) or 0)
                conn.execute(
                    "INSERT INTO group_state (group_id, auto_count_day, auto_count, last_auto_ts, last_read_id) "
                    "VALUES (?, '', 0, 0, ?) "
                    "ON CONFLICT(group_id) DO UPDATE SET last_read_id=MAX(COALESCE(group_state.last_read_id, 0), ?)",
                    (gid, mxv, mxv))
            conn.execute("UPDATE moments SET seen=1 WHERE author='character'")
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('read_backfill_v1', '1')")
            conn.commit()
    except Exception:
        pass

    # 记忆库（聊天记录 → 可检索记忆）：增量水位 + 检索命中日志
    try:
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS mem_index (
                scope TEXT PRIMARY KEY,
                last_id INTEGER DEFAULT 0,
                updated_at REAL
            );
            CREATE TABLE IF NOT EXISTS recall_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cid INTEGER DEFAULT 0,
                ts REAL,
                query TEXT DEFAULT '',
                items_json TEXT DEFAULT '[]',
                latency_ms INTEGER DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_recall_log_ts ON recall_log(ts);
            """
        )
        conn.commit()
    except Exception:
        pass


# ============ 角色 ============

def create_character(data: dict) -> int:
    conn = get_conn()
    now = time.time()
    cur = conn.execute(
        "INSERT INTO characters (name, avatar, description, persona_full, story, fields_json, greeting, model, temperature, sort_order, enabled, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            data.get("name", "未命名"),
            data.get("avatar", ""),
            data.get("description", ""),
            data.get("persona_full", ""),
            data.get("story", ""),
            json.dumps(data.get("fields", {}), ensure_ascii=False),
            data.get("greeting", ""),
            data.get("model", ""),
            float(data.get("temperature", 0.9)),
            int(data.get("sort_order", 0)),
            int(data.get("enabled", 1)),
            now, now,
        ),
    )
    conn.commit()
    cid = cur.lastrowid
    ensure_chat(cid)
    return cid


def update_character(cid: int, data: dict):
    conn = get_conn()
    fields = []
    args = []
    for key in ["name", "avatar", "description", "persona_full", "story", "greeting", "model"]:
        if key in data:
            fields.append(f"{key}=?")
            args.append(data[key])
    if "fields" in data:
        fields.append("fields_json=?")
        args.append(json.dumps(data["fields"], ensure_ascii=False))
    if "temperature" in data:
        fields.append("temperature=?")
        args.append(float(data["temperature"]))
    if "sort_order" in data:
        fields.append("sort_order=?")
        args.append(int(data["sort_order"]))
    if "enabled" in data:
        fields.append("enabled=?")
        args.append(int(data["enabled"]))
    if not fields:
        return
    fields.append("updated_at=?")
    args.append(time.time())
    args.append(cid)
    conn.execute(f"UPDATE characters SET {', '.join(fields)} WHERE id=?", args)
    conn.commit()


def get_character(cid: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM characters WHERE id=?", (cid,)).fetchone()
    return dict(row) if row else None


def list_characters(include_disabled=True):
    conn = get_conn()
    if include_disabled:
        rows = conn.execute("SELECT * FROM characters ORDER BY sort_order, id").fetchall()
    else:
        rows = conn.execute("SELECT * FROM characters WHERE enabled=1 ORDER BY sort_order, id").fetchall()
    return [dict(r) for r in rows]


def delete_character(cid: int):
    conn = get_conn()
    mids = [r[0] for r in conn.execute("SELECT id FROM moments WHERE character_id=?", (cid,)).fetchall()]
    conn.execute("DELETE FROM messages WHERE character_id=?", (cid,))
    conn.execute("DELETE FROM pending_tasks WHERE character_id=?", (cid,))
    conn.execute("DELETE FROM chats WHERE character_id=?", (cid,))
    conn.execute("DELETE FROM kb_chunks WHERE scope='character' AND character_id=?", (cid,))
    conn.execute("DELETE FROM kb_docs WHERE scope='character' AND character_id=?", (cid,))
    conn.execute("DELETE FROM moment_tasks WHERE character_id=?", (cid,))
    conn.execute("DELETE FROM moment_plans WHERE character_id=?", (cid,))
    conn.execute("DELETE FROM reminder_state WHERE character_id=?", (cid,))
    conn.execute("DELETE FROM moment_likes WHERE character_id=?", (cid,))
    conn.execute("DELETE FROM moment_comments WHERE character_id=?", (cid,))
    if mids:
        marks = ",".join("?" * len(mids))
        conn.execute(f"DELETE FROM moment_comments WHERE moment_id IN ({marks})", mids)
        conn.execute(f"DELETE FROM moment_likes WHERE moment_id IN ({marks})", mids)
    conn.execute("DELETE FROM moments WHERE character_id=?", (cid,))
    conn.execute("DELETE FROM group_members WHERE cid=?", (cid,))
    conn.execute("DELETE FROM world_members WHERE cid=?", (cid,))
    conn.execute("DELETE FROM world_events WHERE source_cid=?", (cid,))  # 已删角色不再出现在他人世界动态
    # 清掉已无成员的群（含消息与自动发言状态）
    empties = [r[0] for r in conn.execute(
        "SELECT g.id FROM groups g LEFT JOIN group_members m ON m.group_id=g.id WHERE m.cid IS NULL").fetchall()]
    for gid in empties:
        conn.execute("DELETE FROM group_messages WHERE group_id=?", (gid,))
        conn.execute("DELETE FROM group_state WHERE group_id=?", (gid,))
        conn.execute("DELETE FROM groups WHERE id=?", (gid,))
    conn.execute("DELETE FROM characters WHERE id=?", (cid,))
    conn.commit()


def list_character_moment_images(cid: int):
    """该角色所有朋友圈动态里的图片 URL（删角色时清文件用）"""
    conn = get_conn()
    rows = conn.execute("SELECT images FROM moments WHERE character_id=?", (cid,)).fetchall()
    out = []
    for r in rows:
        try:
            out.extend(json.loads(r["images"] or "[]"))
        except Exception:
            pass
    return out


# ============ 会话（每角色一个） ============

def ensure_chat(cid: int) -> dict:
    conn = get_conn()
    row = conn.execute("SELECT * FROM chats WHERE character_id=?", (cid,)).fetchone()
    if row is None:
        conn.execute("INSERT OR IGNORE INTO chats (character_id) VALUES (?)", (cid,))
        conn.commit()
        row = conn.execute("SELECT * FROM chats WHERE character_id=?", (cid,)).fetchone()
    return dict(row)


def get_chat(cid: int):
    return ensure_chat(cid)


def update_chat(cid: int, **fields):
    allowed = ["photo_enabled", "voice_enabled", "sticker_enabled", "video_enabled", "background", "summary", "summarized_until", "last_msg_at",
               "wake_enabled", "wake_ts", "wake_topic", "wake_pending_ts", "wake_last_reply_ts", "wake_retry", "wake_day", "wake_count", "cheat_memory", "commit_memory",
               "reminder_enabled", "reminder_target", "moments_auto", "moments_react", "img_desc_enabled",
               "voice_desc_enabled",
               "moment_memory", "long_memory"]
    sets = []
    args = []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=?")
            args.append(v)
    if not sets:
        return
    args.append(cid)
    conn = get_conn()
    conn.execute(f"UPDATE chats SET {', '.join(sets)} WHERE character_id=?", args)
    conn.commit()


def update_summary_cas(cid: int, summary: str, until: int, expect_until: int) -> bool:
    """乐观并发写摘要：仅当水位线未变化时写入（防两次压缩线程互相覆盖）"""
    conn = get_conn()
    cur = conn.execute(
        "UPDATE chats SET summary=?, summarized_until=? WHERE character_id=? AND COALESCE(summarized_until,0)=?",
        (summary, until, cid, expect_until),
    )
    conn.commit()
    return cur.rowcount > 0


def list_chats():
    """所有聊天（用于主动回复后台循环）"""
    conn = get_conn()
    rows = conn.execute("SELECT * FROM chats").fetchall()
    return [dict(r) for r in rows]


def cancel_wake(cid: int):
    """取消闹铃与沉默倒计时（用户发消息 / 清空记录时调用）"""
    conn = get_conn()
    conn.execute(
        "UPDATE chats SET wake_ts=0, wake_pending_ts=0, wake_topic='', wake_retry=0 WHERE character_id=?",
        (cid,),
    )
    conn.commit()


# ============ 提醒插件状态（reminder） ============

def get_reminder_state(cid: int) -> dict:
    conn = get_conn()
    row = conn.execute("SELECT * FROM reminder_state WHERE character_id=?", (cid,)).fetchone()
    if not row:
        conn.execute("INSERT OR IGNORE INTO reminder_state (character_id) VALUES (?)", (cid,))
        conn.commit()
        row = conn.execute("SELECT * FROM reminder_state WHERE character_id=?", (cid,)).fetchone()
    return dict(row)


def update_reminder_state(cid: int, **fields):
    allowed = ["week", "count", "last_reminder_ts", "last_judge_ts", "last_count_ts"]
    sets, args = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=?")
            args.append(v)
    if not sets:
        return
    get_reminder_state(cid)
    args.append(cid)
    conn = get_conn()
    conn.execute(f"UPDATE reminder_state SET {', '.join(sets)} WHERE character_id=?", args)
    conn.commit()


# ============ 消息 ============

def add_message(cid: int, role: str, kind: str = "text", content: str = "", media_url: str = "", duration: float = 0) -> int:
    conn = get_conn()
    chat = ensure_chat(cid)
    cur = conn.execute(
        "INSERT INTO messages (character_id, chat_id, role, kind, content, media_url, duration, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (cid, chat["id"], role, kind, content, media_url, duration, time.time()),
    )
    conn.execute("UPDATE chats SET last_msg_at=? WHERE character_id=?", (time.time(), cid))
    conn.commit()
    return cur.lastrowid


def get_messages(cid: int, after_id: int = 0, limit: int = 0):
    conn = get_conn()
    if limit and limit > 0:
        rows = conn.execute(
            "SELECT * FROM (SELECT * FROM messages WHERE character_id=? AND id>? ORDER BY id DESC LIMIT ?) ORDER BY id",
            (cid, after_id, int(limit)),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM messages WHERE character_id=? AND id>? ORDER BY id",
            (cid, after_id),
        ).fetchall()
    return [dict(r) for r in rows]


def get_recent_messages(cid: int, limit: int = 40):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM messages WHERE character_id=? ORDER BY id DESC LIMIT ?",
        (cid, limit),
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


def get_last_message(cid: int):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM messages WHERE character_id=? ORDER BY id DESC LIMIT 1", (cid,)
    ).fetchone()
    return dict(row) if row else None


def get_message(msg_id: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone()
    return dict(row) if row else None


def delete_message(msg_id: int):
    """删除单条消息（管理端）"""
    conn = get_conn()
    conn.execute("DELETE FROM messages WHERE id=?", (msg_id,))
    conn.commit()


def delete_messages_after(cid: int, msg_id: int) -> int:
    """回溯：删除某角色聊天中 id 大于 msg_id 的全部消息（保留 msg_id 这条），返回删除条数"""
    conn = get_conn()
    cur = conn.execute("DELETE FROM messages WHERE character_id=? AND id>?", (cid, msg_id))
    conn.commit()
    return cur.rowcount or 0


def _kept_memories(row) -> str:
    """清空/重置时保留的记忆：永久记忆（pin=1）与管理端手写条目（ts=0）"""
    try:
        cur = json.loads((row["long_memory"] if row else "") or "[]")
        if isinstance(cur, list):
            return json.dumps(
                [x for x in cur if isinstance(x, dict) and (x.get("pin") or float(x.get("ts") or 0) == 0)],
                ensure_ascii=False)
    except Exception:
        pass
    return "[]"


def clear_messages(cid: int):
    """清空某角色的全部消息与摘要（管理端）；永久记忆（★）与手写条目保留"""
    conn = get_conn()
    conn.execute("DELETE FROM messages WHERE character_id=?", (cid,))
    conn.execute("DELETE FROM pending_tasks WHERE character_id=?", (cid,))
    row = conn.execute("SELECT long_memory FROM chats WHERE character_id=?", (cid,)).fetchone()
    conn.execute("UPDATE chats SET summary='', summarized_until=0, last_msg_at=0, wake_ts=0, wake_pending_ts=0, wake_topic='', wake_retry=0, wake_day='', wake_count=0, cheat_memory='[]', commit_memory='[]', long_memory=? WHERE character_id=?", (_kept_memories(row), cid))
    conn.commit()


def get_messages_upto(cid: int, msg_id: int, limit: int = 0):
    """取该消息（含）之前的消息；limit>0 时只取最近 limit 条（长会话省内存）"""
    conn = get_conn()
    if limit and limit > 0:
        rows = conn.execute(
            "SELECT * FROM (SELECT * FROM messages WHERE character_id=? AND id<=? ORDER BY id DESC LIMIT ?) ORDER BY id",
            (cid, msg_id, int(limit)),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM messages WHERE character_id=? AND id<=? ORDER BY id",
            (cid, msg_id),
        ).fetchall()
    return [dict(r) for r in rows]


# 说明：count_messages 的实现见文件后段（此处原有一份逐字重复的定义，已移除）


def max_message_id(cid: int) -> int:
    conn = get_conn()
    row = conn.execute("SELECT MAX(id) AS m FROM messages WHERE character_id=?", (cid,)).fetchone()
    return int(row["m"] or 0) if row else 0


def chats_by_ids(cids: list) -> dict:
    """批量取 chats（避免 bootstrap 的 N+1）"""
    cids = [int(x) for x in (cids or [])]
    if not cids:
        return {}
    marks = ",".join("?" * len(cids))
    rows = get_conn().execute(f"SELECT * FROM chats WHERE character_id IN ({marks})", cids).fetchall()
    return {int(r["character_id"]): dict(r) for r in rows}


def last_messages_by_ids(cids: list) -> dict:
    """批量取每个角色的最后一条消息"""
    cids = [int(x) for x in (cids or [])]
    if not cids:
        return {}
    marks = ",".join("?" * len(cids))
    rows = get_conn().execute(
        f"SELECT m.* FROM messages m JOIN (SELECT character_id, MAX(id) AS mid FROM messages "
        f"WHERE character_id IN ({marks}) GROUP BY character_id) t ON m.id = t.mid", cids).fetchall()
    return {int(r["character_id"]): dict(r) for r in rows}


def message_counts_by_ids(cids: list) -> dict:
    """批量统计每个角色的消息数"""
    cids = [int(x) for x in (cids or [])]
    if not cids:
        return {}
    marks = ",".join("?" * len(cids))
    rows = get_conn().execute(
        f"SELECT character_id, COUNT(*) AS c FROM messages WHERE character_id IN ({marks}) "
        f"GROUP BY character_id", cids).fetchall()
    return {int(r["character_id"]): int(r["c"]) for r in rows}


def reset_messages(cid: int):
    """清空消息（回溯/重置用）；永久记忆（★）与手写条目保留"""
    conn = get_conn()
    conn.execute("DELETE FROM messages WHERE character_id=?", (cid,))
    conn.execute("DELETE FROM pending_tasks WHERE character_id=?", (cid,))
    row = conn.execute("SELECT long_memory FROM chats WHERE character_id=?", (cid,)).fetchone()
    conn.execute("UPDATE chats SET summary='', summarized_until=0, last_msg_at=0, wake_ts=0, wake_pending_ts=0, wake_topic='', wake_retry=0, wake_day='', wake_count=0, cheat_memory='[]', commit_memory='[]', long_memory=? WHERE character_id=?", (_kept_memories(row), cid))
    conn.commit()


def update_message(msg_id: int, **fields):
    allowed = ["content", "media_url", "duration", "kind", "img_desc"]
    sets = []
    args = []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=?")
            args.append(v)
    if not sets:
        return
    args.append(msg_id)
    conn = get_conn()
    conn.execute(f"UPDATE messages SET {', '.join(sets)} WHERE id=?", args)
    conn.commit()


# ============ 延迟队列 ============

def add_pending(cid: int, content: str, kind: str = "reply", media_url: str = "", duration: float = 0,
                ts: float | None = None, ref_id: int = 0):
    conn = get_conn()
    conn.execute(
        "INSERT INTO pending_tasks (character_id, content, kind, media_url, duration, ref_id, ts) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (cid, content, kind, media_url, duration, ref_id, ts if ts is not None else time.time()),
    )
    conn.commit()


def get_due_pending(cid: int, reply_delay: float):
    conn = get_conn()
    now = time.time()
    rows = conn.execute(
        "SELECT * FROM pending_tasks WHERE character_id=? AND "
        "( (kind!='reply' AND ts<=?) OR (kind='reply' AND ts<=?) ) ORDER BY id",
        (cid, now, now - reply_delay),
    ).fetchall()
    return [dict(r) for r in rows]


def get_all_pending(cid: int):
    conn = get_conn()
    rows = conn.execute("SELECT * FROM pending_tasks WHERE character_id=? ORDER BY id", (cid,)).fetchall()
    return [dict(r) for r in rows]


def delete_pending(task_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM pending_tasks WHERE id=?", (task_id,))
    conn.commit()


def has_pending(cid: int) -> bool:
    conn = get_conn()
    row = conn.execute("SELECT COUNT(*) AS c FROM pending_tasks WHERE character_id=?", (cid,)).fetchone()
    return row["c"] > 0


def clear_pending(cid: int):
    conn = get_conn()
    conn.execute("DELETE FROM pending_tasks WHERE character_id=?", (cid,))
    conn.commit()


def clear_script_pending(cid: int):
    """清掉未发出的延迟台词（用户发新消息时丢弃旧回复的剩余分段/语音/表情）"""
    conn = get_conn()
    conn.execute("DELETE FROM pending_tasks WHERE character_id=? AND kind IN ('script','voice','sticker')", (cid,))
    conn.commit()


def pop_script_pending(cid: int) -> str:
    """取出并删除未发出的 script 分段，返回拼接文本。
    用户插话打断时用它兜底补发，避免"半句话永久丢失"。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, content FROM pending_tasks WHERE character_id=? AND kind='script' ORDER BY ts, id",
        (cid,)).fetchall()
    if not rows:
        return ""
    ids = [int(r["id"]) for r in rows]
    marks = ",".join("?" * len(ids))
    conn.execute(f"DELETE FROM pending_tasks WHERE id IN ({marks})", ids)
    conn.commit()
    return "".join((r["content"] or "").strip() for r in rows if (r["content"] or "").strip())


def take_script_pending_and_clear(cid: int):
    """原子地取出未发出的 script 分段，并清空其余延迟任务（script/voice/sticker）。

    返回 (拼接文本, 被丢弃的语音任务数)。
    - 原实现是 pop + clear 两次调用：中间存在并发窗口；
    - 且 clear 会一并删掉 'voice' 任务却不退还语音额度（额度被静默吞掉），
      因此把语音任务数返回给调用方用于退款。
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, kind, content FROM pending_tasks "
        "WHERE character_id=? AND kind IN ('script','voice','sticker') ORDER BY ts, id",
        (cid,)).fetchall()
    if not rows:
        return "", 0
    ids = [int(r["id"]) for r in rows]
    marks = ",".join("?" * len(ids))
    conn.execute(f"DELETE FROM pending_tasks WHERE id IN ({marks})", ids)
    conn.commit()
    text = "".join((r["content"] or "").strip() for r in rows
                   if (r["kind"] or "") == "script" and (r["content"] or "").strip())
    voices = sum(1 for r in rows if (r["kind"] or "") == "voice")
    return text, voices


def delete_pending_by_ref(msg_id: int):
    """删除引用某条消息的待处理任务（管理端删消息时调用，避免回复幽灵消息）"""
    conn = get_conn()
    conn.execute("DELETE FROM pending_tasks WHERE ref_id=?", (msg_id,))
    conn.commit()


def cleanup_orphans():
    """清理已删角色的残留：孤儿 chat 行与待处理任务（删角色时在途线程可能把它们复活）"""
    conn = get_conn()
    conn.execute("DELETE FROM pending_tasks WHERE character_id NOT IN (SELECT id FROM characters)")
    conn.execute("DELETE FROM chats WHERE character_id NOT IN (SELECT id FROM characters)")
    conn.commit()


# ============ 知识库 ============

def add_kb_doc(scope: str, character_id: int, filename: str, stored_name: str, doc_hash: str, status: str = "parsing") -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO kb_docs (scope, character_id, filename, stored_name, status, hash, uploaded_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (scope, character_id, filename, stored_name, status, doc_hash, time.time()),
    )
    conn.commit()
    return cur.lastrowid


def update_kb_doc(doc_id: int, **fields):
    allowed = ["status", "error", "chunk_count", "stored_name", "filename"]
    sets = []
    args = []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=?")
            args.append(v)
    if not sets:
        return
    args.append(doc_id)
    conn = get_conn()
    conn.execute(f"UPDATE kb_docs SET {', '.join(sets)} WHERE id=?", args)
    conn.commit()


def get_kb_doc(doc_id: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM kb_docs WHERE id=?", (doc_id,)).fetchone()
    return dict(row) if row else None


def list_kb_docs(scope: str, character_id: int = 0):
    conn = get_conn()
    if scope == "character":
        rows = conn.execute(
            "SELECT * FROM kb_docs WHERE scope='character' AND character_id=? ORDER BY id DESC",
            (character_id,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM kb_docs WHERE scope='public' ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]


def delete_kb_doc(doc_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM kb_chunks WHERE doc_id=?", (doc_id,))
    conn.execute("DELETE FROM kb_docs WHERE id=?", (doc_id,))
    conn.commit()


def add_kb_chunks(doc_id: int, scope: str, character_id: int, chunks: list):
    conn = get_conn()
    try:
        conn.executemany(
            "INSERT INTO kb_chunks (doc_id, scope, character_id, heading, content, ord) VALUES (?, ?, ?, ?, ?, ?)",
            [(doc_id, scope, character_id, c.get("heading", ""), c["content"], i) for i, c in enumerate(chunks)],
        )
        conn.commit()
    except Exception:
        try:
            conn.rollback()  # 避免半截写入被后续 update/commit 意外提交
        except Exception:
            pass
        raise


def list_kb_chunks(scope: str, character_id: int = 0):
    conn = get_conn()
    if scope == "character":
        rows = conn.execute(
            "SELECT * FROM kb_chunks WHERE scope='character' AND character_id=? ORDER BY doc_id, ord",
            (character_id,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM kb_chunks WHERE scope='public' ORDER BY doc_id, ord").fetchall()
    return [dict(r) for r in rows]


# ============ 表情 / 背景 ============

def add_sticker(file: str, label: str = "", tags: str = "") -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO stickers (file, label, tags, sort_order) VALUES (?, ?, ?, (SELECT COALESCE(MAX(sort_order),0)+1 FROM stickers))",
        (file, label, tags),
    )
    conn.commit()
    return cur.lastrowid


def list_stickers():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM stickers ORDER BY sort_order, id").fetchall()
    return [dict(r) for r in rows]


def get_sticker_by_label(label: str):
    conn = get_conn()
    row = conn.execute("SELECT * FROM stickers WHERE label=? LIMIT 1", (label,)).fetchone()
    return dict(row) if row else None


def delete_sticker(sid: int):
    conn = get_conn()
    conn.execute("DELETE FROM stickers WHERE id=?", (sid,))
    conn.commit()


def add_background(file: str, name: str = "") -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO backgrounds (file, name, sort_order) VALUES (?, ?, (SELECT COALESCE(MAX(sort_order),0)+1 FROM backgrounds))",
        (file, name),
    )
    conn.commit()
    return cur.lastrowid


def list_backgrounds():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM backgrounds ORDER BY sort_order, id").fetchall()
    return [dict(r) for r in rows]


def delete_background(bid: int):
    conn = get_conn()
    conn.execute("DELETE FROM backgrounds WHERE id=?", (bid,))
    conn.commit()


# ============ 设置 ============

_settings_cache = {"ts": 0.0, "data": None}
_settings_lock = threading.Lock()
_SETTINGS_TTL = 3.0


def get_settings() -> dict:
    now = time.time()
    with _settings_lock:
        cached = _settings_cache["data"]
        if cached is not None and now - float(_settings_cache["ts"] or 0) < _SETTINGS_TTL:
            return dict(cached)
    conn = get_conn()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    out = {}
    for r in rows:
        try:
            out[r["key"]] = json.loads(r["value"])
        except Exception:
            out[r["key"]] = r["value"]
    with _settings_lock:
        _settings_cache["ts"] = now
        _settings_cache["data"] = out
    return dict(out)


def set_settings(data: dict):
    conn = get_conn()
    for k, v in data.items():
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (k, json.dumps(v, ensure_ascii=False)),
        )
    conn.commit()
    with _settings_lock:
        _settings_cache["data"] = None


def cleanup_voice_quota(days: int = 7) -> int:
    """清理过期的语音配额键 voice_count_<cid>_<YYYYMMDD>（防 settings 表无限膨胀）"""
    conn = get_conn()
    cutoff = time.strftime("%Y%m%d", time.localtime(time.time() - max(1, days) * 86400))
    rows = conn.execute("SELECT key FROM settings WHERE key LIKE 'voice_count_%'").fetchall()
    old = []
    for r in rows:
        key = str(r["key"])
        day = key.rsplit("_", 1)[-1]
        if len(day) == 8 and day.isdigit() and day < cutoff:
            old.append(key)
    if not old:
        return 0
    marks = ",".join("?" * len(old))
    conn.execute(f"DELETE FROM settings WHERE key IN ({marks})", old)
    conn.commit()
    with _settings_lock:
        _settings_cache["data"] = None
    return len(old)


# ============ 朋友圈（Moments） ============

def add_moment(author: str, character_id: int, content: str, images: list, created_at: float = 0, source: str = "auto",
               visible_to: list = None, hidden_from: list = None) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO moments (author, character_id, content, images, created_at, source, visible_to, hidden_from) VALUES (?,?,?,?,?,?,?,?)",
        (author, character_id, content or "", json.dumps(images or [], ensure_ascii=False), created_at or time.time(), source,
         json.dumps(visible_to or [], ensure_ascii=False), json.dumps(hidden_from or [], ensure_ascii=False)),
    )
    conn.commit()
    return cur.lastrowid


def list_moments(after_id: int = 0, limit: int = 200):
    conn = get_conn()
    if after_id > 0:
        # 增量拉取：从最旧的新动态开始，避免 LIMIT 截断导致中间数据跳过
        rows = conn.execute(
            "SELECT * FROM moments WHERE id>? ORDER BY id ASC LIMIT ?", (after_id, limit)
        ).fetchall()
        return [dict(r) for r in rows]
    rows = conn.execute(
        "SELECT * FROM moments ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


def get_moment(mid: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM moments WHERE id=?", (mid,)).fetchone()
    return dict(row) if row else None


def delete_moment(mid: int):
    conn = get_conn()
    conn.execute("DELETE FROM moments WHERE id=?", (mid,))
    conn.execute("DELETE FROM moment_comments WHERE moment_id=?", (mid,))
    conn.execute("DELETE FROM moment_likes WHERE moment_id=?", (mid,))
    conn.execute("DELETE FROM moment_tasks WHERE moment_id=?", (mid,))
    conn.commit()


def set_moment_like(mid: int, who: str, value: int):
    col = "liked_by_user" if who == "user" else "liked_by_character"
    conn = get_conn()
    conn.execute(f"UPDATE moments SET {col}=? WHERE id=?", (1 if value else 0, mid))
    conn.commit()


def add_moment_comment(moment_id: int, role: str, content: str, reply_to: int = 0, created_at: float = 0, seen: int = 0, character_id: int = 0) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO moment_comments (moment_id, role, character_id, content, created_at, reply_to, seen) VALUES (?,?,?,?,?,?,?)",
        (moment_id, role, character_id, content or "", created_at or time.time(), reply_to, seen),
    )
    conn.commit()
    return cur.lastrowid


def add_moment_like(mid: int, cid: int):
    """记录某个角色点赞（多角色各自独立，不再互相覆盖）"""
    conn = get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO moment_likes (moment_id, character_id, created_at) VALUES (?,?,?)",
        (mid, cid, time.time()),
    )
    conn.commit()


def list_moment_likes(mid: int):
    conn = get_conn()
    rows = conn.execute(
        "SELECT ml.moment_id, ml.character_id, c.name, c.avatar FROM moment_likes ml "
        "LEFT JOIN characters c ON c.id=ml.character_id WHERE ml.moment_id=? ORDER BY ml.created_at",
        (mid,),
    ).fetchall()
    return [dict(r) for r in rows]


def list_moment_comments(moment_id: int):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM moment_comments WHERE moment_id=? ORDER BY id", (moment_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def list_moment_comments_for(mids: list) -> dict:
    """批量取多条动态的评论（避免朋友圈列表 N+1）"""
    mids = [int(x) for x in (mids or [])]
    if not mids:
        return {}
    marks = ",".join("?" * len(mids))
    rows = get_conn().execute(
        f"SELECT * FROM moment_comments WHERE moment_id IN ({marks}) ORDER BY id", mids).fetchall()
    out = {}
    for r in rows:
        out.setdefault(int(r["moment_id"]), []).append(dict(r))
    return out


def list_moment_likes_for(mids: list) -> dict:
    """批量取多条动态的点赞（避免朋友圈列表 N+1）"""
    mids = [int(x) for x in (mids or [])]
    if not mids:
        return {}
    marks = ",".join("?" * len(mids))
    rows = get_conn().execute(
        f"SELECT ml.moment_id, ml.character_id, c.name, c.avatar FROM moment_likes ml "
        f"LEFT JOIN characters c ON c.id=ml.character_id WHERE ml.moment_id IN ({marks}) "
        f"ORDER BY ml.created_at", mids).fetchall()
    out = {}
    for r in rows:
        out.setdefault(int(r["moment_id"]), []).append(dict(r))
    return out


def delete_moment_comment(cid: int):
    conn = get_conn()
    conn.execute("DELETE FROM moment_comments WHERE id=?", (cid,))
    conn.commit()


def count_unseen_moment_comments() -> int:
    conn = get_conn()
    row = conn.execute("SELECT COUNT(*) FROM moment_comments WHERE role='character' AND seen=0").fetchone()
    return int(row[0]) if row else 0


def count_unseen_moments_and_comments() -> int:
    """未读 = 角色评论未读 + 角色新动态未读（朋友圈角标）"""
    conn = get_conn()
    c1 = conn.execute("SELECT COUNT(*) FROM moment_comments WHERE role='character' AND seen=0").fetchone()[0]
    c2 = conn.execute("SELECT COUNT(*) FROM moments WHERE author='character' AND seen=0").fetchone()[0]
    return int(c1 or 0) + int(c2 or 0)


def mark_moments_seen():
    conn = get_conn()
    conn.execute("UPDATE moment_comments SET seen=1 WHERE role='character'")
    conn.execute("UPDATE moments SET seen=1 WHERE author='character'")
    conn.commit()


def add_moment_task(character_id: int, kind: str, due_ts: float, moment_id: int = 0, payload: dict = None) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO moment_tasks (character_id, kind, moment_id, due_ts, payload) VALUES (?,?,?,?,?)",
        (character_id, kind, moment_id, due_ts, json.dumps(payload or {}, ensure_ascii=False)),
    )
    conn.commit()
    return cur.lastrowid


def get_due_moment_tasks(now: float):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM moment_tasks WHERE due_ts<=? ORDER BY due_ts LIMIT 20", (now,)
    ).fetchall()
    return [dict(r) for r in rows]


def delete_moment_task(tid: int):
    conn = get_conn()
    conn.execute("DELETE FROM moment_tasks WHERE id=?", (tid,))
    conn.commit()


def delete_moment_tasks_by_moment(mid: int):
    conn = get_conn()
    conn.execute("DELETE FROM moment_tasks WHERE moment_id=?", (mid,))
    conn.commit()


def get_moment_plan(date_key: str, cid: int):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM moment_plans WHERE date=? AND character_id=?", (date_key, cid)
    ).fetchone()
    return dict(row) if row else None


def upsert_moment_plan(date_key: str, cid: int, planned: int, post_time: str = "", topic: str = ""):
    conn = get_conn()
    conn.execute(
        "INSERT INTO moment_plans (date, character_id, planned, post_time, topic, created_at) VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(date, character_id) DO UPDATE SET planned=excluded.planned, post_time=excluded.post_time, topic=excluded.topic",
        (date_key, cid, 1 if planned else 0, post_time, topic, time.time()),
    )
    conn.commit()


def recent_user_moments(limit: int = 20):
    """最近的用户朋友圈（动态读取：删除后自动消失）"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM moments WHERE author='user' ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


# ============ 群聊（Groups） ============

def create_group(name: str, cids: list) -> int:
    conn = get_conn()
    cur = conn.execute("INSERT INTO groups (name, created_at) VALUES (?,?)", (name or "", time.time()))
    gid = cur.lastrowid
    for cid in cids:
        conn.execute(
            "INSERT OR IGNORE INTO group_members (group_id, cid, joined_at) VALUES (?,?,?)",
            (gid, int(cid), time.time()))
    conn.commit()
    return gid


def list_groups():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM groups ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]


def get_group(gid: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM groups WHERE id=?", (gid,)).fetchone()
    return dict(row) if row else None


def group_members(gid: int):
    conn = get_conn()
    rows = conn.execute(
        "SELECT gm.cid, c.name, c.avatar FROM group_members gm "
        "LEFT JOIN characters c ON c.id=gm.cid WHERE gm.group_id=? ORDER BY gm.joined_at",
        (gid,)).fetchall()
    return [dict(r) for r in rows]


def set_group_members(gid: int, cids: list):
    conn = get_conn()
    keep = [int(x) for x in cids if int(x) > 0]
    if keep:
        marks = ",".join("?" * len(keep))
        conn.execute(
            f"DELETE FROM group_members WHERE group_id=? AND cid NOT IN ({marks})", [gid] + keep)
    else:
        conn.execute("DELETE FROM group_members WHERE group_id=?", (gid,))
    for cid in keep:
        conn.execute(
            "INSERT OR IGNORE INTO group_members (group_id, cid, joined_at) VALUES (?,?,?)",
            (gid, cid, time.time()))
    conn.commit()


def update_group(gid: int, **fields):
    if not fields:
        return
    allowed = {"name", "auto_enabled"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    conn = get_conn()
    conn.execute(f"UPDATE groups SET {cols} WHERE id=?", [*fields.values(), gid])
    conn.commit()


def delete_group(gid: int):
    conn = get_conn()
    conn.execute("DELETE FROM group_messages WHERE group_id=?", (gid,))
    conn.execute("DELETE FROM group_members WHERE group_id=?", (gid,))
    conn.execute("DELETE FROM group_state WHERE group_id=?", (gid,))
    conn.execute("DELETE FROM groups WHERE id=?", (gid,))
    conn.commit()


def add_group_message(gid: int, role: str, cid: int, kind: str, content: str,
                      media_url: str = "", img_desc: str = "") -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO group_messages (group_id, role, cid, kind, content, media_url, img_desc, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (gid, role, int(cid or 0), kind or "text", content or "", media_url or "",
         img_desc or "", time.time()))
    conn.commit()
    return cur.lastrowid


def get_group_messages(gid: int, after_id: int = 0, limit: int = 200):
    conn = get_conn()
    if after_id > 0:
        rows = conn.execute(
            "SELECT * FROM group_messages WHERE group_id=? AND id>? ORDER BY id ASC LIMIT ?",
            (gid, after_id, limit)).fetchall()
        return [dict(r) for r in rows]
    rows = conn.execute(
        "SELECT * FROM group_messages WHERE group_id=? ORDER BY id DESC LIMIT ?",
        (gid, limit)).fetchall()
    return [dict(r) for r in reversed(rows)]


def get_group_recent(gid: int, limit: int = 30):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM group_messages WHERE group_id=? ORDER BY id DESC LIMIT ?",
        (gid, limit)).fetchall()
    return [dict(r) for r in reversed(rows)]


def group_last_message(gid: int):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM group_messages WHERE group_id=? ORDER BY id DESC LIMIT 1", (gid,)).fetchone()
    return dict(row) if row else None


def all_group_members() -> dict:
    """批量取所有群的成员（避免群列表 N+1）"""
    rows = get_conn().execute(
        "SELECT gm.group_id, gm.cid, c.name, c.avatar FROM group_members gm "
        "LEFT JOIN characters c ON c.id=gm.cid ORDER BY gm.joined_at").fetchall()
    out = {}
    for r in rows:
        out.setdefault(int(r["group_id"]), []).append(dict(r))
    return out


def all_group_last_messages() -> dict:
    """批量取所有群的最后一条消息"""
    rows = get_conn().execute(
        "SELECT m.* FROM group_messages m JOIN (SELECT group_id, MAX(id) AS mid FROM group_messages "
        "GROUP BY group_id) t ON m.id = t.mid").fetchall()
    return {int(r["group_id"]): dict(r) for r in rows}


def groups_for_cid(cid: int):
    conn = get_conn()
    rows = conn.execute(
        "SELECT g.* FROM groups g JOIN group_members m ON m.group_id=g.id WHERE m.cid=? ORDER BY g.id",
        (int(cid),)).fetchall()
    return [dict(r) for r in rows]


def group_last_auto_ts(gid: int) -> float:
    conn = get_conn()
    row = conn.execute("SELECT last_auto_ts FROM group_state WHERE group_id=?", (gid,)).fetchone()
    return float(row["last_auto_ts"] or 0) if row else 0.0


def group_auto_budget(gid: int, day: str, limit: int) -> bool:
    conn = get_conn()
    row = conn.execute("SELECT * FROM group_state WHERE group_id=?", (gid,)).fetchone()
    if not row:
        return True
    st = dict(row)
    if st.get("auto_count_day") != day:
        return True
    return int(st.get("auto_count") or 0) < limit


def group_mark_auto(gid: int, day: str):
    conn = get_conn()
    row = conn.execute("SELECT * FROM group_state WHERE group_id=?", (gid,)).fetchone()
    now = time.time()
    if not row:
        conn.execute(
            "INSERT INTO group_state (group_id, auto_count_day, auto_count, last_auto_ts) VALUES (?,?,?,?)",
            (gid, day, 1, now))
    else:
        st = dict(row)
        cnt = (int(st.get("auto_count") or 0) + 1) if st.get("auto_count_day") == day else 1
        conn.execute(
            "UPDATE group_state SET auto_count_day=?, auto_count=?, last_auto_ts=? WHERE group_id=?",
            (day, cnt, now, gid))
    conn.commit()


def group_read_mark(gid: int, last_id: int):
    """推进群已读水位（只增不减）"""
    if gid <= 0 or last_id <= 0:
        return
    conn = get_conn()
    conn.execute(
        "INSERT INTO group_state (group_id, auto_count_day, auto_count, last_auto_ts, last_read_id) "
        "VALUES (?, '', 0, 0, ?) "
        "ON CONFLICT(group_id) DO UPDATE SET last_read_id=MAX(COALESCE(group_state.last_read_id, 0), ?)",
        (gid, last_id, last_id))
    conn.commit()


def all_group_unread() -> dict:
    """各群未读角色消息数（> 已读水位；按群走索引计数，避免全表扫描）"""
    conn = get_conn()
    out = {}
    try:
        states = {int(r["group_id"]): int(r["last_read_id"] or 0)
                  for r in conn.execute("SELECT group_id, last_read_id FROM group_state").fetchall()}
        for g in conn.execute("SELECT id FROM groups").fetchall():
            gid = int(g["id"])
            mark = int(states.get(gid, 0))
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM group_messages WHERE group_id=? AND role='character' AND id > ?",
                (gid, mark)).fetchone()
            c = int(row["c"] or 0) if row else 0
            if c:
                out[gid] = c
    except Exception:
        return out
    return out


def count_messages(cid: int) -> int:
    """消息总数（走 character_id 索引，轻量）"""
    conn = get_conn()
    row = conn.execute("SELECT COUNT(*) AS c FROM messages WHERE character_id=?", (int(cid),)).fetchone()
    return int(row["c"] or 0) if row else 0


def bump_reply_done(cid: int, ref_id: int):
    """标记已回复到 ref_id（防重水位：仅跳过 <= 水位的重复任务，避免误吞新消息）"""
    try:
        conn = get_conn()
        conn.execute(
            "UPDATE chats SET reply_done_ref=MAX(COALESCE(reply_done_ref,0), ?) WHERE character_id=?",
            (int(ref_id or 0), int(cid)))
        conn.commit()
    except Exception:
        pass


# ============ 收藏（网盘） ============

def add_favorite(name: str, kind: str, ext: str, size: int, url: str) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO favorites (name, kind, ext, size, url, created_at) VALUES (?,?,?,?,?,?)",
        (name or "", kind or "file", ext or "", int(size or 0), url or "", time.time()))
    conn.commit()
    return cur.lastrowid


def list_favorites(limit: int = 500):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM favorites ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def get_favorite(fid: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM favorites WHERE id=?", (fid,)).fetchone()
    return dict(row) if row else None


def delete_favorite(fid: int):
    conn = get_conn()
    conn.execute("DELETE FROM favorites WHERE id=?", (fid,))
    conn.commit()


def rename_favorite(fid: int, name: str):
    conn = get_conn()
    conn.execute("UPDATE favorites SET name=? WHERE id=?", (name or "", fid))
    conn.commit()


# ============ 音乐 ============

def add_music(title: str, filename: str, size: int, url: str) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO music (title, filename, size, url, created_at) VALUES (?,?,?,?,?)",
        (title or "", filename or "", int(size or 0), url or "", time.time()))
    conn.commit()
    return cur.lastrowid


def list_music():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM music ORDER BY id ASC").fetchall()
    return [dict(r) for r in rows]


def get_music(mid: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM music WHERE id=?", (mid,)).fetchone()
    return dict(row) if row else None


def delete_music(mid: int):
    conn = get_conn()
    conn.execute("DELETE FROM music WHERE id=?", (mid,))
    conn.commit()


def rename_music(mid: int, title: str):
    conn = get_conn()
    conn.execute("UPDATE music SET title=? WHERE id=?", (title or "", mid))
    conn.commit()


# ============ 新闻（每日自动） ============

def news_exists(url: str) -> bool:
    if not url:
        return False
    conn = get_conn()
    row = conn.execute("SELECT 1 FROM news WHERE url=? LIMIT 1", (url,)).fetchone()
    return bool(row)


def add_news(day: str, category: str, title: str, summary: str, url: str,
             source: str, image: str, published: str = "", content: str = "",
             video: str = "") -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO news (day, category, title, summary, url, source, image, published, content, video, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (day or "", category or "", title or "", summary or "", url or "",
         source or "", image or "", published or "", content or "", video or "", time.time()))
    conn.commit()
    return cur.lastrowid


def update_news_content(nid: int, content: str):
    conn = get_conn()
    conn.execute("UPDATE news SET content=? WHERE id=?", (content or "", nid))
    conn.commit()


def update_news_video(nid: int, video: str):
    conn = get_conn()
    conn.execute("UPDATE news SET video=? WHERE id=?", (video or "", nid))
    conn.commit()


def delete_news(nid: int):
    conn = get_conn()
    conn.execute("DELETE FROM news WHERE id=?", (nid,))
    conn.commit()


def list_news_recent(days: int = 2, limit: int = 300):
    """返回最近 N 天（按 day 字段字符串比较，格式 YYYY-MM-DD）"""
    cutoff = time.strftime("%Y-%m-%d", time.localtime(time.time() - max(1, days) * 86400))
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM news WHERE day>=? ORDER BY id DESC LIMIT ?", (cutoff, limit)).fetchall()
    return [dict(r) for r in rows]


def cleanup_news(before_day: str):
    """删除 before_day 之前的新闻，返回需要删除的本地媒体 URL 列表（图片+视频）"""
    conn = get_conn()
    rows = conn.execute("SELECT image, video FROM news WHERE day<?", (before_day,)).fetchall()
    files = []
    for r in rows:
        if r["image"]:
            files.append(r["image"])
        if r["video"]:
            files.append(r["video"])
    conn.execute("DELETE FROM news WHERE day<?", (before_day,))
    conn.commit()
    return files


def get_moment_comment(cid: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM moment_comments WHERE id=?", (cid,)).fetchone()
    return dict(row) if row else None


# ============ 世界（跨角色共享动态 / 广播） ============

def create_world(name: str, cids: list) -> int:
    conn = get_conn()
    cur = conn.execute("INSERT INTO worlds (name, created_at) VALUES (?,?)", (name or "", time.time()))
    wid = cur.lastrowid
    for cid in cids:
        conn.execute("INSERT OR IGNORE INTO world_members (world_id, cid) VALUES (?,?)",
                     (wid, int(cid)))
    conn.commit()
    return wid


def list_worlds():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM worlds ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def get_world(wid: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM worlds WHERE id=?", (wid,)).fetchone()
    return dict(row) if row else None


def world_members(wid: int):
    conn = get_conn()
    rows = conn.execute(
        "SELECT wm.cid, c.name, c.avatar FROM world_members wm "
        "LEFT JOIN characters c ON c.id=wm.cid WHERE wm.world_id=? ORDER BY wm.cid",
        (wid,)).fetchall()
    return [dict(r) for r in rows]


def set_world_members(wid: int, cids: list):
    conn = get_conn()
    keep = [int(x) for x in cids if int(x) > 0]
    if keep:
        marks = ",".join("?" * len(keep))
        conn.execute(f"DELETE FROM world_members WHERE world_id=? AND cid NOT IN ({marks})",
                     [wid] + keep)
    else:
        conn.execute("DELETE FROM world_members WHERE world_id=?", (wid,))
    for cid in keep:
        conn.execute("INSERT OR IGNORE INTO world_members (world_id, cid) VALUES (?,?)", (wid, cid))
    conn.commit()


def update_world(wid: int, **fields):
    if not fields:
        return
    allowed = {"name"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    conn = get_conn()
    conn.execute(f"UPDATE worlds SET {cols} WHERE id=?", [*fields.values(), wid])
    conn.commit()


def delete_world(wid: int):
    conn = get_conn()
    conn.execute("DELETE FROM world_members WHERE world_id=?", (wid,))
    conn.execute("DELETE FROM world_events WHERE world_id=?", (wid,))
    conn.execute("DELETE FROM worlds WHERE id=?", (wid,))
    conn.commit()


def add_world_event(world_id: int, content: str, sender: str = "system", source_cid: int = 0) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO world_events (world_id, sender, source_cid, content, created_at) VALUES (?,?,?,?,?)",
        (world_id, sender or "system", int(source_cid or 0), content or "", time.time()))
    conn.commit()
    return cur.lastrowid


def list_world_events(world_id: int, limit: int = 50):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM world_events WHERE world_id=? ORDER BY id DESC LIMIT ?",
        (world_id, limit)).fetchall()
    return [dict(r) for r in reversed(rows)]


def delete_world_event(eid: int):
    conn = get_conn()
    conn.execute("DELETE FROM world_events WHERE id=?", (eid,))
    conn.commit()


def worlds_for_cid(cid: int):
    conn = get_conn()
    rows = conn.execute(
        "SELECT w.* FROM worlds w JOIN world_members m ON m.world_id=w.id WHERE m.cid=? ORDER BY w.id",
        (int(cid),)).fetchall()
    return [dict(r) for r in rows]


def world_recent_events(world_id: int, limit: int = 30):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM world_events WHERE world_id=? ORDER BY id DESC LIMIT ?",
        (world_id, limit)).fetchall()
    return [dict(r) for r in reversed(rows)]


# ============ 记忆库（聊天记录 → 可检索记忆）============

def mem_index_get(scope: str) -> int:
    """该范围已索引到的最大消息 id（增量水位）"""
    conn = get_conn()
    row = conn.execute("SELECT last_id FROM mem_index WHERE scope=?", (str(scope),)).fetchone()
    return int(row["last_id"] or 0) if row else 0


def mem_index_set(scope: str, last_id: int):
    conn = get_conn()
    conn.execute(
        "INSERT INTO mem_index (scope, last_id, updated_at) VALUES (?,?,?) "
        "ON CONFLICT(scope) DO UPDATE SET last_id=excluded.last_id, updated_at=excluded.updated_at",
        (str(scope), int(last_id or 0), time.time()))
    conn.commit()


def recall_log_add(cid: int, query: str, items: list, latency_ms: int = 0):
    """记录一次检索命中（用于管理页"最近召回"与调参依据）"""
    try:
        conn = get_conn()
        conn.execute(
            "INSERT INTO recall_log (cid, ts, query, items_json, latency_ms) VALUES (?,?,?,?,?)",
            (int(cid or 0), time.time(), str(query or "")[:500],
             json.dumps(items or [], ensure_ascii=False)[:20000], int(latency_ms or 0)))
        conn.commit()
        # 只保留最近 200 条，避免无限增长
        conn.execute(
            "DELETE FROM recall_log WHERE id NOT IN (SELECT id FROM recall_log ORDER BY id DESC LIMIT 200)")
        conn.commit()
    except Exception:
        pass


def recall_log_recent(limit: int = 50):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM recall_log ORDER BY id DESC LIMIT ?", (max(1, min(int(limit), 200)),)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["items"] = json.loads(d.get("items_json") or "[]")
        except Exception:
            d["items"] = []
        d.pop("items_json", None)
        out.append(d)
    return out


# ============ 小程序（mini programs） ============

MINI_KV_MAX_CHARS = 200000


def mini_kv_set(app: str, k: str, value_json: str):
    """写入小程序 KV。超限直接报错 —— 原实现用 [:200000] 静默截断，
    会把 JSON 截成非法内容后落库（数据损坏且无任何提示）。"""
    v = str(value_json or "")
    if len(v) > MINI_KV_MAX_CHARS:
        raise ValueError("value too large (max %d chars)" % MINI_KV_MAX_CHARS)
    conn = get_conn()
    conn.execute(
        "INSERT INTO mini_kv (app, k, value, updated_at) VALUES (?,?,?,?) "
        "ON CONFLICT(app, k) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (str(app or "")[:32], str(k or "")[:64], v, time.time()))
    conn.commit()


def mini_kv_get(app: str, k: str) -> str:
    conn = get_conn()
    row = conn.execute(
        "SELECT value FROM mini_kv WHERE app=? AND k=?",
        (str(app or "")[:32], str(k or "")[:64])).fetchone()
    return str(row["value"]) if row else ""


def mini_event_add(app: str, content: str, cid: int = 0) -> int:
    conn = get_conn()
    content = str(content or "").strip()[:200]
    if not content:
        return 0
    cid = int(cid or 0)
    # 简单去重：同 app 同内容 90 秒内只记一次（按角色分别去重，避免误伤其他角色）
    row = conn.execute(
        "SELECT content, cid, created_at FROM mini_events WHERE app=? ORDER BY id DESC LIMIT 1",
        (str(app or "")[:32],)).fetchone()
    if (row and row["content"] == content and int(row["cid"] or 0) == cid
            and time.time() - float(row["created_at"] or 0) < 90):
        return 0
    cur = conn.execute(
        "INSERT INTO mini_events (app, content, cid, created_at) VALUES (?,?,?,?)",
        (str(app or "")[:32], content, cid, time.time()))
    conn.commit()
    return cur.lastrowid


def mini_events_recent(limit: int = 10, days: int = 3):
    conn = get_conn()
    cutoff = time.time() - max(1, int(days)) * 86400
    rows = conn.execute(
        "SELECT * FROM mini_events WHERE created_at>=? ORDER BY id DESC LIMIT ?",
        (cutoff, max(1, min(int(limit), 50)))).fetchall()
    return [dict(r) for r in reversed(rows)]


def mini_events_cleanup(days: int = 7) -> int:
    conn = get_conn()
    cutoff = time.time() - max(1, int(days)) * 86400
    cur = conn.execute("DELETE FROM mini_events WHERE created_at<?", (cutoff,))
    conn.commit()
    return cur.rowcount or 0


def delete_world_events_by_source(source_cid: int, since_ts: float) -> int:
    """回溯同步：删除某角色在指定时间之后产生的世界广播，返回删除条数"""
    conn = get_conn()
    cur = conn.execute(
        "DELETE FROM world_events WHERE source_cid=? AND created_at>=?",
        (int(source_cid), float(since_ts)),
    )
    conn.commit()
    return int(cur.rowcount or 0)


def count_group_messages(gid: int) -> int:
    """群消息总数（客户端对账用：服务端条数 < 本地缓存时触发全量重拉）"""
    conn = get_conn()
    r = conn.execute("SELECT COUNT(*) AS n FROM group_messages WHERE group_id=?", (int(gid),)).fetchone()
    return int(r["n"] if r else 0)
