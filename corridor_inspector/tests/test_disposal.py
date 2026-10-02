"""处置权限与认领规则: 主管批准、期限、误认、损坏、紧急移除。"""
from __future__ import annotations

from corridor_inspector.services import (DISPOSED, EMERGENCY_REMOVED, ConflictError,
                                         PermissionError_, RETURNED, SEALED,
                                         ServiceError)

from . import ServiceFixture


class DisposalPermissionTest(ServiceFixture):
    def _sealed(self, claim_days=7, owner=None):
        iid = self.make_item(owner=owner)
        self.notice_and_pass_deadline(iid)
        self.svc.seal_item(iid, "王巡", "赵敏", ["纸箱×2（内件特征封存）"],
                           "B1", claim_days=claim_days)
        return iid

    def test_inspector_cannot_dispose(self):
        iid = self._sealed(claim_days=1)
        self.clock.advance(days=2)
        with self.assertRaises(PermissionError_):
            self.svc.dispose(iid, "王巡", "统一清运销毁")
        self.assertEqual(self.svc.get_item(iid)["status"], SEALED)

    def test_supervisor_can_dispose_after_claim_window(self):
        iid = self._sealed(claim_days=1)
        self.clock.advance(days=2)
        self.svc.dispose(iid, "李主管", "统一清运销毁")
        self.assertEqual(self.svc.get_item(iid)["status"], DISPOSED)
        row = self.conn.execute(
            "SELECT d.*, s.role FROM disposals d JOIN staff s ON s.id=d.approved_by "
            "WHERE d.seal_id=(SELECT id FROM seals WHERE item_id=?)", (iid,)).fetchone()
        self.assertEqual(row["role"], "supervisor")

    def test_disposal_blocked_within_claim_window_even_for_supervisor(self):
        iid = self._sealed(claim_days=7)
        with self.assertRaises(ConflictError):
            self.svc.dispose(iid, "李主管", "捐赠")

    def test_mistaken_neighbor_claim_keeps_item_sealed(self):
        iid = self._sealed(owner=None)
        cid = self.svc.submit_claim(iid, "李四（邻居代认）", "13800000405",
                                    "内件-错误特征")
        result = self.svc.review_claim(cid, "李主管", accept=True, note="邻居误认")
        self.assertEqual(result, "mistaken")
        self.assertEqual(self.svc.get_item(iid)["status"], SEALED,
                         "误认后物品必须继续封存")

    def test_correct_feature_returns_item(self):
        iid = self._sealed(owner=self.r_zhang)
        secret = self.svc.get_seal(iid)["claim_secret"]
        cid = self.svc.submit_claim(iid, "张三", "13800000302", secret)
        self.assertEqual(self.svc.review_claim(cid, "李主管", accept=True), "matched")
        self.assertEqual(self.svc.get_item(iid)["status"], RETURNED)
        # 已发还的物品不得再处置
        with self.assertRaises(ConflictError):
            self.svc.dispose(iid, "李主管", "捐赠")

    def test_pending_claim_blocks_disposal(self):
        iid = self._sealed(claim_days=1)
        self.svc.submit_claim(iid, "某人", "13900000000", "内件-待核")
        self.clock.advance(days=2)
        with self.assertRaises(ConflictError):
            self.svc.dispose(iid, "李主管", "销毁")

    def test_damage_report_after_seal(self):
        iid = self._sealed()
        self.svc.report_damage(iid, "赵敏", "外包装受潮")
        self.assertIn("受潮", self.svc.get_seal(iid)["damage_note"])

    def test_emergency_remove_only_flammable(self):
        normal = self.make_item(flammable=False)
        with self.assertRaises(ServiceError):
            self.svc.emergency_remove(normal, "王巡", "x")
        danger = self.make_item(flammable=True)
        self.svc.emergency_remove(danger, "王巡", "有挥发气味，移至危险品柜")
        self.assertEqual(self.svc.get_item(danger)["status"], EMERGENCY_REMOVED)
        # 主管收到内部报备消息
        sup_msgs = self.msg.sent_to("李主管")
        self.assertTrue(any("紧急移除" in m["subject"] for m in sup_msgs))
