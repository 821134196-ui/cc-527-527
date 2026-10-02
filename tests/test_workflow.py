"""测试：公告隐私 / 并发清理不重复封存 / 封存双签 / 处置权限。

运行：python3 -m unittest discover -s tests -v
"""
import os
import sqlite3
import tempfile
import threading
import unittest

from app.db import init_db, connect
from app import service as svc


class HallwayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "t.db")
        self.photos = os.path.join(self.tmp.name, "photos")
        os.environ["HALLWAY_DB"] = self.db
        init_db(self.db)
        self.conn = connect(self.db)
        self.conn.executemany(
            "INSERT INTO staff(id,login,name,role) VALUES(?,?,?,?)",
            [(1, "wang", "王巡查", "patrol"),
             (2, "li", "李巡查", "patrol"),
             (3, "zhao", "赵主管", "supervisor")])
        self.conn.executemany(
            "INSERT INTO residents(id,unit,name,phone) VALUES(?,?,?,?)",
            [(1, "3-501", "张伟", "13800000501"),
             (2, "3-502", "陈芳", "13800000502")])
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _register(self, resident=None, flammable=False, risk="low"):
        return svc.register_item(
            self.conn, floor=3, location="501门口", x=0.4, y=0.2,
            description="旧纸箱", secret_feature="内有蓝色折叠伞",
            risk=risk, flammable=flammable, resident_id=resident, actor_id=1)

    def _to_sealable(self, iid, resident=None):
        if resident is not None:
            self.conn.execute("UPDATE items SET resident_id=? WHERE id=?",
                              (resident, iid)); self.conn.commit()
        svc.send_notice(self.conn, iid, deadline_days=0, actor_id=1)
        svc.recheck(self.conn, iid, still_present=True, actor_id=1)

    # ---------------------------------------------------------- 公告隐私

    def test_anonymous_notice_has_no_pii(self):
        """无主物品的公告/封存公告不得出现房号、姓名、手机号。"""
        iid = self._register(resident=None)
        svc.send_notice(self.conn, iid, deadline_days=3, actor_id=1)
        svc.recheck(self.conn, iid, still_present=True, actor_id=1)
        svc.seal_item(self.conn, iid, witness1_id=1, witness2_id=2)
        msgs = [r["content"] for r in
                self.conn.execute("SELECT content FROM outbox WHERE target='公告板/全体'")]
        self.assertTrue(msgs)
        for content in msgs:
            for secret in ("3-501", "3-502", "张伟", "陈芳", "13800000501", "13800000502"):
                self.assertNotIn(secret, content, f"公告泄露个人信息: {secret}")
            # 匿名公告用公告编号而非房号
            self.assertIn("A-3F-", content)

    def test_privacy_guard_blocks_leak(self):
        """守卫应直接拒绝把房号写进公开公告。"""
        with self.assertRaises(svc.WorkflowError):
            svc._send(self.conn, "公告板/全体", "notice",
                      "请 3-501 张伟 尽快清理", guard_pii=True)
            self.conn.commit()

    def test_direct_notice_to_owner_allowed_unit(self):
        """给主人本人的私信允许房号（非公开），且不进公告板。"""
        iid = self._register(resident=1)
        svc.send_notice(self.conn, iid, actor_id=1)
        board = self.conn.execute(
            "SELECT COUNT(*) c FROM outbox WHERE target='公告板/全体'").fetchone()["c"]
        self.assertEqual(board, 0)

    def test_public_photos_only(self):
        """住户端只能看到 public=1 的照片。"""
        iid = self._register(resident=None)
        svc.add_photo(self.conn, iid, "data/photos/internal.svg", public=False)
        svc.add_photo(self.conn, iid, "data/photos/open.svg", public=True)
        pub = self.conn.execute(
            "SELECT COUNT(*) c FROM photos WHERE item_id=? AND public=1",
            (iid,)).fetchone()["c"]
        internal = self.conn.execute(
            "SELECT path FROM photos WHERE item_id=? AND public=0", (iid,)).fetchone()
        self.assertEqual(pub, 1)
        self.assertNotIn(internal["path"].split("/")[-1],
                         [r["path"] for r in self.conn.execute(
                          "SELECT path FROM photos WHERE public=1")])

    # ---------------------------------------------------------- 并发清理

    def test_concurrent_self_clear_vs_seal_no_double_seal(self):
        """住户自清确认与员工封存同时发生：恰好一个成功，绝不重复封存。"""
        iid = self._register(resident=1)
        self._to_sealable(iid)
        results = []
        barrier = threading.Barrier(2)

        def worker(kind):
            conn = connect(self.db)
            barrier.wait()
            try:
                if kind == "clear":
                    svc.confirm_self_clear(conn, iid, actor_id=2)
                    results.append(("clear", "ok"))
                else:
                    svc.seal_item(conn, iid, witness1_id=1, witness2_id=2)
                    results.append(("seal", "ok"))
            except svc.WorkflowError as e:
                results.append((kind, f"rejected:{e}"))
            finally:
                conn.close()

        t1 = threading.Thread(target=worker, args=("clear",))
        t2 = threading.Thread(target=worker, args=("seal",))
        t1.start(); t2.start(); t1.join(); t2.join()

        status = self.conn.execute("SELECT status FROM items WHERE id=?",
                                   (iid,)).fetchone()["status"]
        seal_count = self.conn.execute(
            "SELECT COUNT(*) c FROM seals WHERE item_id=?", (iid,)).fetchone()["c"]
        kinds = {k for k, _ in results}
        self.assertEqual(kinds, {"clear", "seal"})
        oks = [k for k, r in results if r == "ok"]
        self.assertEqual(len(oks), 1, f"必须恰好一方成功，实际 {results}")
        self.assertLessEqual(seal_count, 1)
        self.assertIn(status, {"removed_by_owner", "sealed"})
        if status == "sealed":
            self.assertEqual(oks, ["seal"])
        else:
            self.assertEqual(oks, ["clear"])

    def test_double_self_clear_idempotent_reject(self):
        """自清后再封存、或重复自清都必须被拒。"""
        iid = self._register(resident=1)
        self._to_sealable(iid)
        svc.confirm_self_clear(self.conn, iid, actor_id=2)
        with self.assertRaises(svc.WorkflowError):
            svc.seal_item(self.conn, iid, witness1_id=1, witness2_id=2)
        with self.assertRaises(svc.WorkflowError):
            svc.confirm_self_clear(self.conn, iid, actor_id=2)

    def test_blurry_photo_blocks_seal(self):
        """照片不清时封存被拒，重拍合格后可封存。"""
        iid = self._register(resident=None)
        self._to_sealable(iid)
        svc.add_photo(self.conn, iid, "data/photos/blur.svg",
                      public=False, quality="blurry", note="模糊")
        with self.assertRaises(svc.WorkflowError):
            svc.seal_item(self.conn, iid, witness1_id=1, witness2_id=2)
        svc.add_photo(self.conn, iid, "data/photos/ok.svg", public=True)
        box = svc.seal_item(self.conn, iid, witness1_id=1, witness2_id=2)
        self.assertTrue(box.startswith("BOX-"))

    # ---------------------------------------------------------- 封存双签

    def test_seal_requires_two_distinct_staff(self):
        iid = self._register(resident=None)
        self._to_sealable(iid)
        with self.assertRaises(svc.WorkflowError):
            svc.seal_item(self.conn, iid, witness1_id=1, witness2_id=1)
        # 两名不同员工可以封存
        svc.seal_item(self.conn, iid, witness1_id=1, witness2_id=2)
        row = self.conn.execute(
            "SELECT w1.name a, w2.name b FROM seals s "
            "JOIN staff w1 ON w1.id=s.witness1_id JOIN staff w2 ON w2.id=s.witness2_id "
            "WHERE s.item_id=?", (iid,)).fetchone()
        self.assertNotEqual(row["a"], row["b"])

    # ---------------------------------------------------------- 认领错认

    def test_wrong_claim_rejected_right_feature_accepted(self):
        iid = self._register(resident=None)
        self._to_sealable(iid)
        svc.seal_item(self.conn, iid, witness1_id=1, witness2_id=2)
        self.assertFalse(svc.claim(
            self.conn, iid, claimer="邻居", contact="", feature="里面是衣服", handler_id=2))
        self.assertEqual(
            self.conn.execute("SELECT status FROM items WHERE id=?", (iid,)).fetchone()["status"],
            "sealed")
        self.assertTrue(svc.claim(
            self.conn, iid, claimer="真主人", contact="",
            feature="内有蓝色折叠伞", handler_id=2))
        self.assertEqual(
            self.conn.execute("SELECT status FROM items WHERE id=?", (iid,)).fetchone()["status"],
            "claimed")

    # ---------------------------------------------------------- 处置权限

    def test_dispose_requires_supervisor(self):
        from datetime import datetime, timedelta
        iid = self._register(resident=None)
        self._to_sealable(iid)
        svc.seal_item(self.conn, iid, witness1_id=1, witness2_id=2)
        # 认领期未满，主管也不能处置
        with self.assertRaises(svc.WorkflowError):
            svc.dispose(self.conn, iid, method="discard", approver_id=3)
        # 把认领期限改到过去
        past = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        self.conn.execute("UPDATE seals SET claim_deadline=? WHERE item_id=?",
                          (past, iid)); self.conn.commit()
        # 巡查员无权处置
        with self.assertRaises(svc.PermissionError_):
            svc.dispose(self.conn, iid, method="discard", approver_id=1)
        self.assertEqual(
            self.conn.execute("SELECT status FROM items WHERE id=?", (iid,)).fetchone()["status"],
            "sealed")
        # 主管批准后处置成功
        svc.dispose(self.conn, iid, method="donate", approver_id=3)
        self.assertEqual(
            self.conn.execute("SELECT status FROM items WHERE id=?", (iid,)).fetchone()["status"],
            "disposed")
        row = self.conn.execute("SELECT method FROM disposals WHERE item_id=?",
                                (iid,)).fetchone()
        self.assertEqual(row["method"], "donate")

    def test_urgent_remove_only_for_flammable(self):
        iid = self._register(flammable=False)
        with self.assertRaises(svc.WorkflowError):
            svc.urgent_remove(self.conn, iid, actor_id=1)
        iid2 = self._register(flammable=True, risk="high")
        svc.urgent_remove(self.conn, iid2, actor_id=1)
        self.assertEqual(
            self.conn.execute("SELECT status FROM items WHERE id=?", (iid2,)).fetchone()["status"],
            "urgent_removed")


if __name__ == "__main__":
    unittest.main()
