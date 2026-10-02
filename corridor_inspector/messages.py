"""模拟消息适配器: 不落外部服务, 仅写库; 提供发件箱查看。"""
from __future__ import annotations

import sqlite3
from datetime import datetime

from .clock import Clock


class MessageAdapter:
    def __init__(self, conn: sqlite3.Connection, clock: Clock | None = None):
        self.conn = conn
        self.clock = clock or Clock()

    def send(self, channel: str, to_contact: str, subject: str, body: str) -> int:
        # 不自行提交: 与调用方业务事务同生共死 (autocommit 模式下也会立即落库)
        cur = self.conn.execute(
            "INSERT INTO messages(channel, to_contact, subject, body, created_at) "
            "VALUES (?,?,?,?,?)",
            (channel, to_contact, subject, body, self.clock.now().isoformat(timespec="seconds")),
        )
        return cur.lastrowid

    def outbox(self, limit: int = 50) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT * FROM messages ORDER BY id DESC LIMIT ?", (limit,)
        ))

    def sent_to(self, contact: str) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT * FROM messages WHERE to_contact=? ORDER BY id", (contact,)
        ))
