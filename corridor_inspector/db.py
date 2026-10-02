"""SQLite 连接与建表。事件表保存完整时间线。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS staff (
    id      INTEGER PRIMARY KEY,
    name    TEXT NOT NULL UNIQUE,
    role    TEXT NOT NULL CHECK (role IN ('inspector','supervisor'))
);

CREATE TABLE IF NOT EXISTS residents (
    id      INTEGER PRIMARY KEY,
    name    TEXT NOT NULL,
    room    TEXT NOT NULL UNIQUE,   -- 如 302
    phone   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS items (
    id            INTEGER PRIMARY KEY,
    code          TEXT NOT NULL UNIQUE,        -- 巡查编号 C-2026-001
    floor         INTEGER NOT NULL,
    position      TEXT NOT NULL,               -- 楼道内公开位置描述
    description   TEXT NOT NULL,
    fire_level    TEXT NOT NULL CHECK (fire_level IN ('low','medium','high')),
    status        TEXT NOT NULL,
    owner_id      INTEGER REFERENCES residents(id),
    is_flammable  INTEGER NOT NULL DEFAULT 0,
    photo_clear   INTEGER NOT NULL DEFAULT 1,  -- 照片是否清晰可辨认
    notice_deadline TEXT,                      -- 限期清理截止(含无主物品公告期)
    discovered_at TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);

CREATE TABLE IF NOT EXISTS item_photos (
    id        INTEGER PRIMARY KEY,
    item_id   INTEGER NOT NULL REFERENCES items(id),
    kind      TEXT NOT NULL,   -- public(可公开) / detail(细节,含特征) / sealed / damage
    path      TEXT NOT NULL,
    label     TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- 处置工作票: 同一物品同时只允许一张活动票, 防止清理申请与住户自清并发导致重复封存
CREATE TABLE IF NOT EXISTS clear_tickets (
    id          INTEGER PRIMARY KEY,
    item_id     INTEGER NOT NULL REFERENCES items(id),
    kind        TEXT NOT NULL CHECK (kind IN ('seal','self_clear')),
    status      TEXT NOT NULL CHECK (status IN ('active','done','cancelled')),
    actor       TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ticket_one_active
    ON clear_tickets(item_id) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS notices (
    id             INTEGER PRIMARY KEY,
    item_id        INTEGER NOT NULL REFERENCES items(id),
    to_resident_id INTEGER NOT NULL REFERENCES residents(id),
    deadline       TEXT NOT NULL,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS seals (
    id             INTEGER PRIMARY KEY,
    item_id        INTEGER NOT NULL UNIQUE REFERENCES items(id),
    sealed_at      TEXT NOT NULL,
    witness1_id    INTEGER NOT NULL REFERENCES staff(id),
    witness2_id    INTEGER NOT NULL REFERENCES staff(id),
    inventory      TEXT NOT NULL,           -- 清点内容 JSON
    storage_loc    TEXT NOT NULL,
    claim_deadline TEXT NOT NULL,
    claim_secret   TEXT NOT NULL,           -- 非公开认领特征
    damage_note    TEXT,                    -- 封存后损坏记录
    claimed_by     INTEGER REFERENCES residents(id)
);

CREATE TABLE IF NOT EXISTS claims (
    id          INTEGER PRIMARY KEY,
    seal_id     INTEGER NOT NULL REFERENCES seals(id),
    claimant    TEXT NOT NULL,
    contact     TEXT NOT NULL,
    feature     TEXT NOT NULL,              -- 认领人给出的非公开特征
    status      TEXT NOT NULL CHECK (status IN ('pending','matched','mistaken','rejected')),
    reviewed_by INTEGER REFERENCES staff(id),
    note        TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS disposals (
    id          INTEGER PRIMARY KEY,
    seal_id     INTEGER NOT NULL REFERENCES seals(id),
    decision    TEXT NOT NULL,              -- discard / donate / return_after_approval
    approved_by INTEGER NOT NULL REFERENCES staff(id),
    created_at  TEXT NOT NULL
);

-- 公开公告: 落库前由服务层过滤, 不含住户姓名/房号/电话
CREATE TABLE IF NOT EXISTS announcements (
    id         INTEGER PRIMARY KEY,
    item_id    INTEGER NOT NULL REFERENCES items(id),
    floor      INTEGER NOT NULL,
    body       TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- 统一事件流: 事件页按 通知 -> 复查 -> 封存 排列
CREATE TABLE IF NOT EXISTS events (
    id        INTEGER PRIMARY KEY,
    item_id   INTEGER NOT NULL REFERENCES items(id),
    kind      TEXT NOT NULL,
    actor     TEXT NOT NULL,
    summary   TEXT NOT NULL,
    payload   TEXT,                          -- JSON
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_item ON events(item_id, id);

-- 模拟消息适配器发送记录
CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY,
    channel    TEXT NOT NULL,
    to_contact TEXT NOT NULL,
    subject    TEXT NOT NULL,
    body       TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
