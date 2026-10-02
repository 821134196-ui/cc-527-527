"""初始化数据库并播种演示数据：包含邻居误认、易燃紧急移除、自清、
照片不清、封存损坏、主管处置等待分支。一条命令：python3 -m app.seed
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

from .db import init_db, connect
from . import service as svc

DB = os.environ.get("HALLWAY_DB", "data/hallway.db")
PHOTOS = os.environ.get("HALLWAY_PHOTOS", "data/photos")


def svg_photo(name: str, label: str, color: str, *, blurry: bool = False,
              note_tag: str = "") -> str:
    filt = 'filter="url(#b)"' if blurry else ""
    blur_def = ('<filter id="b"><feGaussianBlur stdDeviation="3.2"/></filter>'
                if blurry else "")
    path = os.path.join(PHOTOS, name)
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="360" height="240"
 viewBox="0 0 360 240"><defs>{blur_def}</defs>
 <rect width="360" height="240" fill="#e7ebe6"/>
 <rect x="20" y="150" width="320" height="70" fill="#cfd6ce"/>
 <g {filt}>
 <rect x="120" y="90" width="120" height="62" rx="4" fill="{color}"/>
 <rect x="135" y="70" width="90" height="26" rx="3" fill="{color}" opacity=".8"/>
 <circle cx="140" cy="165" r="8" fill="#5b655c"/><circle cx="220" cy="165" r="8" fill="#5b655c"/>
 </g>
 <text x="180" y="40" text-anchor="middle" font-size="16" fill="#243028">{label}</text>
 <text x="180" y="210" text-anchor="middle" font-size="11" fill="#6a746d">
   巡查现场照片（本地存档）{note_tag}</text></svg>"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(svg)
    return name


def ts(days_ago: float, hour=10) -> str:
    d = datetime.now() - timedelta(days=days_ago)
    return d.strftime(f"%Y-%m-%d {hour:02d}:00:00")


def seed() -> None:
    if os.path.exists(DB):
        os.remove(DB)
    for dbf in (DB + "-wal", DB + "-shm"):
        if os.path.exists(dbf):
            os.remove(dbf)
    os.makedirs(PHOTOS, exist_ok=True)
    init_db(DB)
    conn = connect(DB)

    # 员工
    conn.executemany(
        "INSERT INTO staff(id,login,name,role) VALUES(?,?,?,?)",
        [(1, "wang", "王巡查", "patrol"),
         (2, "li", "李巡查", "patrol"),
         (3, "zhao", "赵主管", "supervisor")])
    # 住户（内部信息）
    conn.executemany(
        "INSERT INTO residents(id,unit,name,phone) VALUES(?,?,?,?)",
        [(1, "3-501", "张伟", "13800000501"),
         (2, "3-502", "陈芳", "13800000502"),
         (3, "5-503", "刘洋", "13800000503")])

    reg = lambda **k: svc.register_item(conn, **k)

    # ① 3层鞋柜：有明确主人，已发限期通知，等待复查
    i1 = reg(floor=3, location="501门口东侧", x=0.42, y=0.2,
             description="木质双门鞋柜（约80cm）", secret_feature="柜内第二层放有一把蓝色折叠伞",
             risk="medium", resident_id=1, actor_id=1, ts=ts(2))
    svg_photo(f"item{i1}-1.svg", "3层 501门口 · 鞋柜", "#8a6b4a")
    svc.add_photo(conn, i1, f"data/photos/item{i1}-1.svg", public=False)
    svc.send_notice(conn, i1, deadline_days=3, actor_id=1)

    # ② 3层纸箱+油漆：易燃高风险，紧急移除 → 双人封存，且封存后有损坏
    i2 = reg(floor=3, location="楼梯间防火门旁", x=0.9, y=0.75,
             description="堆放纸箱三个、油漆桶两桶", secret_feature="其中一个纸箱侧面用红笔写了“刘”字",
             risk="high", flammable=True, actor_id=1, ts=ts(1, 9))
    svg_photo(f"item{i2}-1.svg", "3层 楼梯间 · 纸箱油漆", "#c0563a")
    svc.add_photo(conn, i2, f"data/photos/item{i2}-1.svg", public=False)
    svc.send_notice(conn, i2, deadline_days=1, actor_id=1)
    svc.urgent_remove(conn, i2, actor_id=1)
    svc.seal_item(conn, i2, witness1_id=1, witness2_id=2, claim_days=7)
    svc.record_damage(conn, i2, description="清点时一桶油漆桶身凹陷，有渗漏痕迹，已套防渗袋",
                      reporter_id=2)

    # ③ 5层无主旧行李箱：匿名公告 → 双人封存 → 邻居误认（样例）→ 等待真主人
    i3 = reg(floor=5, location="电梯厅西侧", x=0.12, y=0.6,
             description="深灰色24寸旧行李箱，箱体有多处托运划痕",
             secret_feature="箱内有一件叠好的儿童红色舞蹈裙，拉杆把手缠了两圈黑色胶带",
             risk="low", actor_id=2, ts=ts(6))
    svg_photo(f"item{i3}-1.svg", "5层 电梯厅 · 旧行李箱", "#5b6670", note_tag="（可公开）")
    svc.add_photo(conn, i3, f"data/photos/item{i3}-1.svg", public=True)
    svc.send_notice(conn, i3, deadline_days=3, actor_id=2)
    svc.recheck(conn, i3, still_present=True, actor_id=2)
    svc.seal_item(conn, i3, witness1_id=1, witness2_id=2, claim_days=7)
    # 邻居误认：说不出真正的非公开特征
    svc.claim(conn, i3, claimer="5-503 刘洋（邻居）", contact="13800000503",
              feature="箱子里应该是几件冬天的衣服", handler_id=2)

    # ④ 2层婴儿车：通知后住户已自行清走
    i4 = reg(floor=2, location="502门口", x=0.5, y=0.25,
             description="折叠婴儿推车", secret_feature="扶手上挂着一只小老虎挂件",
             risk="low", resident_id=2, actor_id=2, ts=ts(4))
    svg_photo(f"item{i4}-1.svg", "2层 502门口 · 婴儿车", "#7a8fa6")
    svc.add_photo(conn, i4, f"data/photos/item{i4}-1.svg", public=False)
    svc.send_notice(conn, i4, deadline_days=2, actor_id=2)
    svc.confirm_self_clear(conn, i4, actor_id=2,
                           note="复查时住户陈芳表示已自行搬入屋内，楼道恢复畅通，结案")

    # ⑤ 4层杂物架：首拍照片不清，待重拍后才能封存（已通知、复查仍在）
    i5 = reg(floor=4, location="走廊尽头消火栓前", x=0.78, y=0.5,
             description="四层金属杂物架，遮挡消火栓", secret_feature="架顶压着一本卷边的《唐诗三百首》",
             risk="high", actor_id=1, ts=ts(2, 15))
    svg_photo(f"item{i5}-1.svg", "照片模糊", "#9aa39a", blurry=True)
    svc.add_photo(conn, i5, f"data/photos/item{i5}-1.svg", public=False,
                  quality="blurry", note="逆光失焦，看不清物品全貌")
    svc.send_notice(conn, i5, deadline_days=1, actor_id=1)
    svc.recheck(conn, i5, still_present=True, actor_id=1)

    # ⑥ 6层无主旧自行车：封存认领期已满，等待主管批准处置
    i6 = reg(floor=6, location="楼道拐角", x=0.3, y=0.8,
             description="落灰的黑色旧自行车，车胎已瘪",
             secret_feature="车座下贴着一张褪色的幼儿园接送卡号 0317",
             risk="medium", actor_id=1, ts=ts(20))
    svg_photo(f"item{i6}-1.svg", "6层 拐角 · 旧自行车", "#4d5148", note_tag="（可公开）")
    svc.add_photo(conn, i6, f"data/photos/item{i6}-1.svg", public=True)
    svc.send_notice(conn, i6, deadline_days=3, actor_id=1)
    svc.recheck(conn, i6, still_present=True, actor_id=1)
    # 认领期已过：直接写入一份"过去时"的封存记录
    past = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d 23:59:59")
    sealed_at = ts(9)
    conn.execute("""INSERT INTO seals(item_id,box_code,witness1_id,witness2_id,
                     sealed_at,claim_deadline) VALUES(?,?,?,?,?,?)""",
                 (i6, f"BOX-6F-{i6:03d}", 1, 2, sealed_at, past))
    conn.execute("UPDATE items SET status='sealed' WHERE id=?", (i6,))
    svc._log(conn, i6, "seal",
             f"由王巡查、李巡查双人清点，封存于 BOX-6F-{i6:03d}；认领期限至 {past}", 1)
    conn.commit()

    print(f"播种完成：数据库 {DB}，照片目录 {PHOTOS}")
    print("演示分支：①待复查 ②易燃紧急移除+封存损坏 ③匿名公告+【邻居误认样例】")
    print("         ④住户自清 ⑤照片不清待重拍 ⑥认领期满待主管处置")
    print("住户端公告：http://127.0.0.1:8000/public （可对行李箱再试误认）")


if __name__ == "__main__":
    seed()
