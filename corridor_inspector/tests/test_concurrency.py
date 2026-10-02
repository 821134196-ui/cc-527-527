"""并发: 清理申请与住户自清同时到达时, 绝不允许重复封存。

通过两个独立连接 + 屏障线程真实模拟竞态: 两个事务交错执行,
断言恰好一方成功, 物品最终状态唯一且无重复封存记录。
"""
from __future__ import annotations

import threading

from corridor_inspector.clock import FixedClock
from corridor_inspector.db import connect
from corridor_inspector.messages import MessageAdapter
from corridor_inspector.services import (REMOVED, SEALED, ConflictError,
                                         InspectorService)
from corridor_inspector.storage import ImageStore

from . import ServiceFixture


class ConcurrencyTest(ServiceFixture):
    def _ready_item(self):
        iid = self.make_item(owner=self.r_zhang)
        self.notice_and_pass_deadline(iid)
        return iid

    def test_active_ticket_is_unique_per_item(self):
        iid = self._ready_item()
        self.svc.begin_cleanup_request(iid, "王巡")
        with self.assertRaises(ConflictError):
            self.svc.begin_cleanup_request(iid, "赵敏")

    def test_self_clear_cancels_pending_seal_ticket(self):
        iid = self._ready_item()
        self.svc.begin_cleanup_request(iid, "王巡")
        self.svc.confirm_self_clear(iid, "赵敏")
        item = self.svc.get_item(iid)
        self.assertEqual(item["status"], REMOVED)
        seals = self.conn.execute("SELECT COUNT(*) n FROM seals WHERE item_id=?",
                                  (iid,)).fetchone()["n"]
        self.assertEqual(seals, 0)
        # 已自清后不能再封存
        with self.assertRaises(ConflictError):
            self.svc.seal_item(iid, "王巡", "赵敏", ["x"], "B1")

    def test_seal_wins_self_clear_must_go_to_claim(self):
        iid = self._ready_item()
        self.svc.seal_item(iid, "王巡", "赵敏", ["纸箱×2"], "B1")
        with self.assertRaises(ConflictError):
            self.svc.confirm_self_clear(iid, "赵敏")
        self.assertEqual(self.svc.get_item(iid)["status"], SEALED)

    def test_parallel_seal_and_self_clear_exactly_one_wins(self):
        """两连接真实并发: 屏障同时放行封存与自清, 结果唯一。"""
        iid = self._ready_item()
        db_file = self.conn.execute("PRAGMA database_list").fetchone()[2]
        results = {}
        barrier = threading.Barrier(2)

        def worker(kind):
            conn = connect(db_file)
            try:
                clock = FixedClock(self.clock.now())
                svc = InspectorService(conn, self.store,
                                       MessageAdapter(conn, clock), clock)
                barrier.wait(timeout=10)
                if kind == "seal":
                    svc.seal_item(iid, "王巡", "赵敏", ["纸箱×2"], "B1")
                    results[kind] = "ok"
                else:
                    try:
                        svc.confirm_self_clear(iid, "赵敏")
                        results[kind] = "ok"
                    except ConflictError:
                        results[kind] = "conflict"
            except ConflictError:
                results[kind] = "conflict"
            except Exception as e:  # noqa: BLE001
                results[kind] = f"error:{e!r}"
            finally:
                conn.close()

        t1 = threading.Thread(target=worker, args=("seal",))
        t2 = threading.Thread(target=worker, args=("self",))
        t1.start(); t2.start(); t1.join(10); t2.join(10)

        # 封存与自清可能任一方先拿到写锁, 但必须恰好一方成功
        self.assertEqual(sorted(results.values()), ["conflict", "ok"], results)
        winners = [k for k, v in results.items() if v == "ok"]
        self.assertEqual(len(winners), 1)

        item = self.svc.get_item(iid)
        seal_n = self.conn.execute("SELECT COUNT(*) n FROM seals WHERE item_id=?",
                                   (iid,)).fetchone()["n"]
        self.assertIn(item["status"], (SEALED, REMOVED))
        if results["seal"] == "ok":
            self.assertEqual(seal_n, 1, "封存获胜: 恰好一条封存记录")
            self.assertEqual(item["status"], SEALED)
        else:
            self.assertEqual(seal_n, 0, "自清获胜: 不得有任何封存记录")
            self.assertEqual(item["status"], REMOVED)

    def test_double_self_clear_is_idempotent(self):
        iid = self._ready_item()
        self.svc.confirm_self_clear(iid, "王巡")
        self.assertEqual(self.svc.confirm_self_clear(iid, "赵敏"), REMOVED)
        seal_n = self.conn.execute("SELECT COUNT(*) n FROM seals WHERE item_id=?",
                                   (iid,)).fetchone()["n"]
        self.assertEqual(seal_n, 0)
