"""演示数据播种: 构造覆盖全部分支的样例事件, 含"邻居误认"样例。

用法: python -m corridor_inspector.seed [--db data/demo.db] [--force]
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from . import images
from .clock import FixedClock
from .db import connect, init_db
from .messages import MessageAdapter
from .services import (EMERGENCY_REMOVED, NOTICED, REGISTERED, REMOVED,
                       RETURNED, SEALED, InspectorService)
from .storage import ImageStore

BASE = Path(__file__).resolve().parent.parent
DEFAULT_DB = BASE / "data" / "demo.db"
DEFAULT_IMG = BASE / "data" / "images"

STAFF = [
    ("王巡", "inspector"),
    ("赵敏", "inspector"),
    ("李主管", "supervisor"),
]
RESIDENTS = [
    ("张桂芳", "302", "13800000302"),
    ("李大爷", "201", "13800000201"),
    ("王建国", "402", "13800000402"),   # 样例中的误认邻居
    ("赵晓", "501", "13800000501"),
    ("陈姨", "305", "13800000305"),
]


def seed(db_path: Path = DEFAULT_DB, img_dir: Path = DEFAULT_IMG,
         force: bool = False) -> None:
    if force and db_path.exists():
        for p in db_path.parent.glob(db_path.name + "*"):
            p.unlink()
    conn = connect(db_path)
    init_db(conn)
    if conn.execute("SELECT COUNT(*) n FROM items").fetchone()["n"]:
        conn.close()
        return

    store = ImageStore(img_dir)
    # 锚定真实当前时间倒推 5 天, 保证"已逾期可封存/认领期未满/认领期满待处置"
    # 在服务器真实时钟下均成立
    anchor = datetime.now().replace(microsecond=0)
    clock = FixedClock(anchor - timedelta(days=6))
    msg = MessageAdapter(conn, clock)
    svc = InspectorService(conn, store, msg, clock)

    for name, role in STAFF:
        conn.execute("INSERT INTO staff(name,role) VALUES (?,?)", (name, role))
    resident_ids = {}
    for name, room, phone in RESIDENTS:
        cur = conn.execute("INSERT INTO residents(name,room,phone) VALUES (?,?,?)",
                           (name, room, phone))
        resident_ids[room] = cur.lastrowid
    conn.commit()

    def photo(code, label, level, clear=True, kind="public"):
        return images.make_photo(code, label, level, clear=clear, kind=kind)

    # 1. 3层 纸箱杂物(易燃,高风险) 已通知且逾期 → 可封存
    i1 = svc.register_item(3, "东侧楼梯口第一转角", "废纸箱及旧衣物约2箱", "high",
                           "王巡", owner_id=resident_ids["302"], flammable=True,
                           photo=photo("C-2026-001", "cartons east stair", "high"))
    # 2. 4层 旧床垫(中风险, 主人不明) 公告逾期 → 可封存
    i2 = svc.register_item(4, "西侧电梯间旁", "废弃单人床垫1张", "medium", "赵敏",
                           owner_id=None,
                           photo=photo("C-2026-002", "mattress near lift", "medium"))
    # 3. 2层 鞋柜(低风险) 期限未到 → 封存应被拒
    i3 = svc.register_item(2, "中段消火栓对面", "塑料鞋架1个", "low", "王巡",
                           owner_id=resident_ids["201"],
                           photo=photo("C-2026-003", "shoe rack 2f", "low"))
    # 4. 5层 油漆桶(易燃高风险) 仅登记 → 走紧急移除
    i4 = svc.register_item(5, "西侧疏散通道", "敞口油漆桶2只及稀料", "high", "赵敏",
                           owner_id=None, flammable=True,
                           photo=photo("C-2026-004", "paint cans 5f", "high"))
    # 5. 3层 自行车 复查时住户已自清
    i5 = svc.register_item(3, "西侧电梯厅", "折叠自行车1辆", "medium", "王巡",
                           owner_id=resident_ids["305"],
                           photo=photo("C-2026-005", "bike 3f lift", "medium"))
    # 6. 4层 储物箱 已封存, 邻居误认 + 封存后损坏 (核心样例)
    i6 = svc.register_item(4, "东侧楼梯口", "蓝白编织袋及纸箱3件", "medium", "赵敏",
                           owner_id=None,
                           photo=photo("C-2026-006", "bags 4f east", "medium"))
    # 7. 2层 旧木柜 已封存且认领期满 → 待主管处置
    i7 = svc.register_item(2, "东侧楼梯缓坡", "旧木柜1个", "low", "王巡",
                           owner_id=None,
                           photo=photo("C-2026-007", "cabinet 2f", "low"))
    # 8. 3层 杂物 照片不清 → 必须重拍后才能封存
    i8 = svc.register_item(3, "中段安全出口前", "黑色垃圾袋若干支", "medium", "王巡",
                           owner_id=resident_ids["302"],
                           photo=photo("C-2026-008", "bags unclear", "medium",
                                       clear=False), photo_clear=False)
    # 9. 5层 婴儿车 已被真正主人凭特征认领发还 (完整闭环)
    i9 = svc.register_item(5, "东侧电梯厅", "折叠婴儿车1辆", "low", "赵敏",
                           owner_id=resident_ids["501"],
                           photo=photo("C-2026-009", "stroller 5f", "low"))

    # ---- 通知 (09-26, 期限 09-29) ----
    for iid in (i1, i2, i5, i6, i7, i8, i9):
        svc.issue_notice(iid, "王巡", days=3)

    # ---- 09-27: 油漆桶紧急移除; 自行车复查, 住户已自清 ----
    clock.advance(days=1)
    svc.emergency_remove(i4, "赵敏", "敞口稀料有挥发气味，移至B1危险品柜并报备主管")
    svc.recheck(i5, "王巡", found_present=False)

    # ---- 09-30: 逾期后双人封存 ----
    clock.advance(days=3)
    svc.begin_cleanup_request(i6, "王巡")
    s6 = svc.seal_item(i6, "王巡", "赵敏",
                       ["蓝白编织袋×2（内装被褥）", "纸箱×1（内件-特征封存）"],
                       "物业B1暂存间A架", claim_days=7)
    secret6 = svc.get_seal(i6)["claim_secret"]
    svc.add_photo(i6, "detail", f"非公开特征照：{secret6}",
                  photo("C-2026-006", "detail feature", "medium", kind="detail"))
    svc.add_photo(i6, "sealed", "封存现场照（双签封条）",
                  photo("C-2026-006", "sealed bags", "medium", kind="sealed"))

    svc.seal_item(i7, "王巡", "赵敏", ["旧木柜×1（柜门松动）"],
                  "物业B1暂存间B架", claim_days=1)
    svc.add_photo(i7, "sealed", "封存现场照",
                  photo("C-2026-007", "sealed cabinet", "low", kind="sealed"))

    svc.begin_cleanup_request(i9, "赵敏")
    svc.seal_item(i9, "赵敏", "王巡", ["折叠婴儿车×1（蓝色）"],
                  "物业B1暂存间A架", claim_days=7)
    secret9 = svc.get_seal(i9)["claim_secret"]

    # ---- 10-01: 邻居误认 i6 (特征说错), 复核判 mistaken ----
    clock.advance(days=1)
    wrong = svc.submit_claim(i6, "王建国（402住户·邻居代认）", "13800000402",
                             "内件-000000（自称箱内是书）")
    svc.review_claim(wrong, "李主管", accept=False, note="邻居误认：所述为书籍，与被褥不符")

    # 婴儿车真正主人凭正确特征认领, 发还
    c9 = svc.submit_claim(i9, "赵晓（501）", "13800000501", secret9)
    svc.review_claim(c9, "李主管", accept=True, note="特征相符，发还")

    # i6 封存后损坏登记
    svc.report_damage(i6, "赵敏", "暂存间巡检发现编织袋外层受潮霉斑约掌心大",
                      photo=photo("C-2026-006", "mildew damage", "medium",
                                  kind="damage"))

    # ---- 10-02 (今天): 鞋柜刚发通知, 期限 10-05 ----
    clock.advance(days=1)
    svc.issue_notice(i3, "王巡", days=3)
    # 推进1小时: 旧木柜认领期(10-02 09:00)确已届满, 可在界面演示主管处置
    clock.advance(hours=1)

    conn.close()


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--images", default=str(DEFAULT_IMG))
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    seed(Path(a.db), Path(a.images), a.force)
    print(f"seeded -> {a.db}")
