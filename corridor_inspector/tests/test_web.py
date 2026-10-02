"""HTTP 端到端: 起真实服务器, 验证页面隐私与操作权限状态码。"""
from __future__ import annotations

import tempfile
import threading
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
import unittest

from corridor_inspector.clock import FixedClock
from corridor_inspector.db import connect, init_db
from corridor_inspector.messages import MessageAdapter
from corridor_inspector.services import InspectorService
from corridor_inspector.storage import ImageStore
from corridor_inspector.web import App, make_handler


class WebFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db, self.img = root / "t.db", root / "img"
        conn = connect(self.db)
        init_db(conn)
        self.clock = FixedClock(datetime(2026, 10, 2, 10, 0))
        store, msg = ImageStore(self.img), MessageAdapter(conn, self.clock)
        svc = InspectorService(conn, store, msg, self.clock)
        conn.executescript(
            "INSERT INTO staff(name,role) VALUES('王巡','inspector');"
            "INSERT INTO staff(name,role) VALUES('赵敏','inspector');"
            "INSERT INTO staff(name,role) VALUES('李主管','supervisor');"
            "INSERT INTO residents(name,room,phone) VALUES('张三','302','13800000302');")
        conn.commit()
        iid = svc.register_item(3, "东侧楼梯口", "废纸箱×2", "high", "王巡",
                                owner_id=None, flammable=True)
        svc.issue_notice(iid, "王巡", days=3)
        self.clock.advance(days=4)
        iid2 = svc.register_item(2, "中段", "旧木柜", "low", "王巡", owner_id=None)
        svc.issue_notice(iid2, "王巡", days=3)
        self.clock.advance(days=4)
        svc.seal_item(iid2, "王巡", "赵敏", ["旧木柜"], "B1", claim_days=1)
        self.iid2 = iid2
        conn.close()

        self.app = App(self.db, self.img, self.clock)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.app))
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.tmp.cleanup()

    def _get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}") as r:
            return r.status, r.read().decode("utf-8")

    def _post(self, path, data):
        # 不自动跟随 303, 以便断言跳转
        opener = urllib.request.build_opener(urllib.request.HTTPRedirectHandler())

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None

        opener = urllib.request.build_opener(NoRedirect)
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=urllib.parse.urlencode(data).encode(),
            method="POST")
        try:
            with opener.open(req) as r:
                return r.status, r.headers.get("Location", "")
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Location") or e.read().decode("utf-8")

    def test_pages_render_and_urgent_first(self):
        _, staff = self._get("/staff")
        self.assertIn("楼层平面示意", staff)
        self.assertIn("脉冲", staff)
        # 紧急高风险物品在表格中排在旧木柜之前
        self.assertLess(staff.index("废纸箱"), staff.index("旧木柜"))

    def test_resident_page_no_pii_and_public_photos_only(self):
        _, page = self._get("/resident")
        for pii in ("张三", "302", "13800000302"):
            self.assertNotIn(pii, page)
        # 非公开特征照标记不得出现在住户端
        self.assertNotIn("非公开</span>", page)

    def test_http_double_sign_and_disposal_permission(self):
        self.clock.advance(days=2)  # 认领期已届满, 此时唯一门槛应是角色权限
        code, body = self._post("/act/dispose",
                                {"id": self.iid2, "actor": "王巡", "decision": "销毁"})
        self.assertEqual(code, 409, "巡查员无权处置")
        self.assertIn("主管", body)
        code, loc = self._post("/act/dispose",
                               {"id": self.iid2, "actor": "李主管", "decision": "销毁"})
        self.assertEqual(code, 303)
        self.assertIn(f"/event?id={self.iid2}", loc)

    def test_http_same_person_double_sign_rejected(self):
        # 易燃物紧急移除后另一物品: 直接造一个逾期物品测同人双签
        conn = connect(self.db)
        svc = InspectorService(conn, ImageStore(self.img),
                               MessageAdapter(conn, self.clock), self.clock)
        iid = svc.register_item(3, "西侧", "纸箱", "low", "王巡", owner_id=None)
        svc.issue_notice(iid, "王巡", days=1)
        self.clock.advance(days=2)
        conn.close()
        code, body = self._post("/act/seal", {
            "id": iid, "actor": "王巡", "w1": "王巡", "w2": "王巡",
            "inventory": "纸箱", "storage_loc": "B1"})
        self.assertEqual(code, 409)
        self.assertIn("两名不同员工", body)
