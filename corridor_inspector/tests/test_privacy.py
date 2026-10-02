"""公告/住户端隐私: 公告文本不泄露姓名、房号、电话; 住户端只见公开照片。"""
from __future__ import annotations

import re

from corridor_inspector.services import NOTICED, SEALED
from corridor_inspector import images

from . import ServiceFixture

PII_PATTERNS = ["张三", "李四", "302", "405", "13800000302", "13800000405"]


class PrivacyTest(ServiceFixture):
    def test_anonymous_notice_contains_no_pii(self):
        iid = self.make_item(owner=None)
        self.svc.issue_notice(iid, "王巡")
        bodies = [a["body"] for a in self.svc.announcements()]
        self.assertTrue(bodies, "无主物品必须发布公开公告")
        for body in bodies:
            for pii in PII_PATTERNS:
                self.assertNotIn(pii, body, f"公告泄露个人信息 {pii}: {body}")
        # 公告必须包含公共位置与认领说明
        self.assertIn("东侧楼梯口", bodies[-1])
        self.assertIn("层公共楼道", bodies[-1])

    def test_owner_notice_is_private_message_not_announcement(self):
        iid = self.make_item(owner=self.r_zhang)
        n = self.svc.issue_notice(iid, "王巡")
        self.assertGreater(n, 0)
        # 有主住户通知走私信, 不进公告栏
        public = [a["body"] for a in self.svc.announcements()]
        self.assertEqual(public, [])
        sent = self.msg.sent_to("13800000302")
        self.assertEqual(len(sent), 1)
        self.assertIn("限期清理", sent[0]["subject"])

    def test_seal_announcement_has_no_claimant_identity(self):
        iid = self.make_item(owner=None)
        self.notice_and_pass_deadline(iid)
        self.svc.seal_item(iid, "王巡", "赵敏", ["纸箱×2"], "B1暂存间")
        for a in self.svc.announcements():
            for pii in PII_PATTERNS:
                self.assertNotIn(pii, a["body"])

    def test_resident_photos_are_public_only(self):
        iid = self.make_item(owner=None)
        self.notice_and_pass_deadline(iid)
        self.svc.seal_item(iid, "王巡", "赵敏", ["纸箱×2"], "B1暂存间")
        self.svc.add_photo(iid, "detail", "非公开特征照",
                           images.make_photo("C-1", "detail", "medium", kind="detail"))
        self.svc.add_photo(iid, "damage", "封存损坏照",
                           images.make_photo("C-1", "damage", "medium", kind="damage"))
        public = self.svc.photos(iid, public_only=True)
        all_photos = self.svc.photos(iid)
        self.assertTrue(all(p["kind"] == "public" for p in public))
        self.assertLess(len(public), len(all_photos),
                        "细节照/损坏照不得出现在住户端")

    def test_timeline_claim_event_masks_contact(self):
        iid = self.make_item(owner=self.r_zhang)
        self.notice_and_pass_deadline(iid)
        self.svc.seal_item(iid, "王巡", "赵敏", ["纸箱×2"], "B1暂存间")
        secret = self.svc.get_seal(iid)["claim_secret"]
        self.svc.submit_claim(iid, "李四（邻居代认）", "13800000405", secret)
        for e in self.svc.timeline(iid):
            if e["kind"] == "claim":
                self.assertNotIn("13800000405", e["summary"])
