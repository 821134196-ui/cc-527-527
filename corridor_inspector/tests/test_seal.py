"""双人封存: 必须两名不同员工; 照片不清禁止封存; 期限未到禁止封存。"""
from __future__ import annotations

from corridor_inspector.services import (NeedReshootError, PermissionError_,
                                         ConflictError, SEALED)
from corridor_inspector import images

from . import ServiceFixture


class SealRuleTest(ServiceFixture):
    def _overdue(self, owner=None, clear=True):
        iid = self.make_item(owner=owner, clear=clear)
        self.notice_and_pass_deadline(iid)
        return iid

    def test_two_distinct_staff_required(self):
        iid = self._overdue()
        with self.assertRaises(PermissionError_):
            self.svc.seal_item(iid, "王巡", "王巡", ["x"], "B1")
        seal_n = self.conn.execute("SELECT COUNT(*) n FROM seals").fetchone()["n"]
        self.assertEqual(seal_n, 0, "同人双签不得产生封存记录")

    def test_seal_records_both_signatures(self):
        iid = self._overdue()
        sid = self.svc.seal_item(iid, "王巡", "赵敏", ["纸箱×2"], "B1暂存间")
        seal = self.svc.get_seal(iid)
        self.assertEqual(seal["id"], sid)
        self.assertNotEqual(seal["witness1_id"], seal["witness2_id"])
        self.assertEqual({seal["w1name"], seal["w2name"]}, {"王巡", "赵敏"})
        self.assertEqual(self.svc.get_item(iid)["status"], SEALED)

    def test_unclear_photo_blocks_seal_until_reshoot(self):
        iid = self._overdue(clear=False)
        with self.assertRaises(NeedReshootError):
            self.svc.seal_item(iid, "王巡", "赵敏", ["垃圾袋"], "B1")
        # 重拍后可以封存
        self.svc.reshoot(iid, "王巡",
                         images.make_photo("C-1", "clear", "medium"))
        sid = self.svc.seal_item(iid, "王巡", "赵敏", ["垃圾袋"], "B1")
        self.assertTrue(sid)

    def test_cannot_seal_before_deadline(self):
        iid = self.make_item()
        self.svc.issue_notice(iid, "王巡", days=3)
        with self.assertRaises(ConflictError):
            self.svc.seal_item(iid, "王巡", "赵敏", ["x"], "B1")

    def test_seal_creates_public_announcement(self):
        iid = self._overdue(owner=None)
        self.svc.seal_item(iid, "王巡", "赵敏", ["x"], "B1")
        self.assertTrue(self.svc.announcements())
