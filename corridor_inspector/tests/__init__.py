"""测试夹具: 内存/临时 SQLite + 固定时钟 + 最小样例数据。"""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from corridor_inspector.clock import FixedClock
from corridor_inspector.db import connect, init_db
from corridor_inspector.messages import MessageAdapter
from corridor_inspector.services import InspectorService
from corridor_inspector.storage import ImageStore


class ServiceFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.conn = connect(root / "t.db")
        init_db(self.conn)
        self.clock = FixedClock(datetime(2026, 10, 2, 10, 0))
        self.store = ImageStore(root / "img")
        self.msg = MessageAdapter(self.conn, self.clock)
        self.svc = InspectorService(self.conn, self.store, self.msg, self.clock)
        self.conn.execute("INSERT INTO staff(name,role) VALUES('王巡','inspector')")
        self.conn.execute("INSERT INTO staff(name,role) VALUES('赵敏','inspector')")
        self.conn.execute("INSERT INTO staff(name,role) VALUES('李主管','supervisor')")
        r1 = self.conn.execute(
            "INSERT INTO residents(name,room,phone) VALUES('张三','302','13800000302')").lastrowid
        r2 = self.conn.execute(
            "INSERT INTO residents(name,room,phone) VALUES('李四','405','13800000405')").lastrowid
        self.conn.commit()
        self.r_zhang, self.r_li = r1, r2

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    # -- 便捷构造 -------------------------------------------------------------
    def make_item(self, owner=None, level="medium", clear=True, flammable=False,
                  floor=3, position="东侧楼梯口", desc="纸箱×2"):
        return self.svc.register_item(floor, position, desc, level, "王巡",
                                      owner_id=owner, flammable=flammable,
                                      photo=b"", photo_clear=clear)

    def notice_and_pass_deadline(self, iid, days=3):
        self.svc.issue_notice(iid, "王巡", days=days)
        self.clock.advance(days=days + 1)
