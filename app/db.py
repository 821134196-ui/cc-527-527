"""SQLite 数据层：楼道占用物品的全生命周期记录。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode=WAL;

-- 住户（系统内部数据，公告中绝不出现）
CREATE TABLE IF NOT EXISTS residents (
    id          INTEGER PRIMARY KEY,
    unit        TEXT NOT NULL,          -- 房号，如 3-501
    name        TEXT NOT NULL,          -- 真实姓名（内部）
    phone       TEXT NOT NULL           -- 手机号（内部）
);

-- 员工
CREATE TABLE IF NOT EXISTS staff (
    id          INTEGER PRIMARY KEY,
    login       TEXT UNIQUE NOT NULL,
    name        TEXT NOT NULL,
    role        TEXT NOT NULL CHECK (role IN ('patrol','supervisor'))
);

-- 占用物品（一个物品 = 一条处置流）
CREATE TABLE IF NOT EXISTS items (
    id              INTEGER PRIMARY KEY,
    code            TEXT UNIQUE NOT NULL,       -- 公告编号，如 A-3F-07
    floor           INTEGER NOT NULL,
    location        TEXT NOT NULL,              -- 楼层内位置描述
    x               REAL,                       -- 平面图坐标（0~1）
    y               REAL,
    description     TEXT NOT NULL,              -- 可公开描述
    secret_feature  TEXT NOT NULL,              -- 非公开特征，认领时核验
    risk            TEXT NOT NULL CHECK (risk IN ('low','medium','high')),
    flammable       INTEGER NOT NULL DEFAULT 0, -- 易燃物
    resident_id     INTEGER REFERENCES residents(id),  -- 主人不明时为 NULL
    status          TEXT NOT NULL DEFAULT 'registered',
    created_at      TEXT NOT NULL,
    resolved_at     TEXT
    -- status: registered(已登记) / noticed(已通知) / rechecked(已复查,仍占用)
    --         / removed_by_owner(住户自清) / urgent_removed(紧急移除)
    --         / sealed(已封存) / claimed(已认领) / disposed(到期处置)
);

-- 照片（住户端只展示 public=1 的照片）
CREATE TABLE IF NOT EXISTS photos (
    id          INTEGER PRIMARY KEY,
    item_id     INTEGER NOT NULL REFERENCES items(id),
    path        TEXT NOT NULL,                  -- 本地相对路径
    public      INTEGER NOT NULL DEFAULT 0,     -- 是否可公开
    quality     TEXT NOT NULL DEFAULT 'ok',     -- ok / blurry(不清)
    note        TEXT,
    created_at  TEXT NOT NULL
);

-- 时间线事件（事件页按 通知→复查→封存 排列）
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY,
    item_id     INTEGER NOT NULL REFERENCES items(id),
    type        TEXT NOT NULL,
    -- type: register / notify / recheck / self_clear / urgent_remove
    --       / reseal_photo(重拍) / seal / wrong_claim / claim / damage / dispose
    note        TEXT,
    actor_id    INTEGER REFERENCES staff(id),
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_item ON events(item_id, id);

-- 限期清理通知
CREATE TABLE IF NOT EXISTS notices (
    id          INTEGER PRIMARY KEY,
    item_id     INTEGER NOT NULL REFERENCES items(id),
    deadline    TEXT NOT NULL,                  -- 限期清理截止时间
    channel     TEXT NOT NULL DEFAULT 'mock',
    created_at  TEXT NOT NULL
);

-- 双人封存记录（双签）
CREATE TABLE IF NOT EXISTS seals (
    id              INTEGER PRIMARY KEY,
    item_id         INTEGER NOT NULL UNIQUE REFERENCES items(id),
    box_code        TEXT NOT NULL,              -- 封存箱编号
    witness1_id     INTEGER NOT NULL REFERENCES staff(id),
    witness2_id     INTEGER NOT NULL REFERENCES staff(id),
    sealed_at       TEXT NOT NULL,
    claim_deadline  TEXT NOT NULL,              -- 认领期限
    CHECK (witness1_id <> witness2_id)
);

-- 封存后损坏记录
CREATE TABLE IF NOT EXISTS damages (
    id          INTEGER PRIMARY KEY,
    seal_id     INTEGER NOT NULL REFERENCES seals(id),
    description TEXT NOT NULL,
    reporter_id INTEGER NOT NULL REFERENCES staff(id),
    created_at  TEXT NOT NULL
);

-- 认领记录（含错认）
CREATE TABLE IF NOT EXISTS claims (
    id          INTEGER PRIMARY KEY,
    item_id     INTEGER NOT NULL REFERENCES items(id),
    claimer     TEXT NOT NULL,                  -- 认领人自述姓名
    contact     TEXT,
    feature     TEXT NOT NULL,                  -- 其提供的"非公开特征"
    accepted    INTEGER NOT NULL,               -- 1 认领成功 / 0 错认驳回
    handler_id  INTEGER NOT NULL REFERENCES staff(id),
    created_at  TEXT NOT NULL
);

-- 到期处置（须主管批准）
CREATE TABLE IF NOT EXISTS disposals (
    id              INTEGER PRIMARY KEY,
    item_id         INTEGER NOT NULL UNIQUE REFERENCES items(id),
    method          TEXT NOT NULL,              -- discard(废弃) / donate(捐赠) / auction(变价)
    approver_id     INTEGER NOT NULL REFERENCES staff(id),
    created_at      TEXT NOT NULL
);

-- 模拟消息适配器的发件箱
CREATE TABLE IF NOT EXISTS outbox (
    id          INTEGER PRIMARY KEY,
    target      TEXT NOT NULL,                  -- 房号/公告板，不存真实电话外发
    kind        TEXT NOT NULL,                  -- notice / claim_ok / claim_bad / dispose
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: str | Path) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()
