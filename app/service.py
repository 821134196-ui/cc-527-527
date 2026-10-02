"""核心工作流：登记 → 通知 → 复查 → 双人封存 → 认领 → 到期处置。

并发约定：所有会推进物品状态的操作都在 BEGIN IMMEDIATE 事务中
"先读状态再改写"，SQLite 的写锁保证复查时住户自清与员工封存
同时发生也只有一方生效，绝不重复封存。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from .db import connect

# 时间线在同一时刻的规范顺序：通知 → 复查 → 封存
EVENT_ORDER = {
    "register": 0,
    "notify": 1,
    "recheck": 2,
    "self_clear": 3,
    "urgent_remove": 3,
    "reseal_photo": 4,
    "seal": 5,
    "wrong_claim": 6,
    "claim": 6,
    "damage": 7,
    "dispose": 8,
}

# 可以发通知的状态
PRE_NOTICE = {"registered", "noticed"}
# 复查/封存时物品仍在楼道、允许封存的状态
SEALABLE = {"registered", "noticed", "rechecked", "urgent_removed"}


class WorkflowError(Exception):
    """业务规则不满足（状态不允许等）。"""


class PermissionError_(WorkflowError):
    """操作人权限不足。"""


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------- 内部工具

def _get(conn: sqlite3.Connection, item_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    if row is None:
        raise WorkflowError(f"物品 {item_id} 不存在")
    return row


def _staff(conn: sqlite3.Connection, staff_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM staff WHERE id=?", (staff_id,)).fetchone()
    if row is None:
        raise WorkflowError(f"员工 {staff_id} 不存在")
    return row


def _log(conn, item_id, etype, note, actor_id=None, ts=None):
    conn.execute(
        "INSERT INTO events(item_id,type,note,actor_id,created_at) VALUES(?,?,?,?,?)",
        (item_id, etype, note, actor_id, ts or now()),
    )


def _send(conn, target, kind, content, guard_pii: bool = False):
    """模拟消息适配器：只写本地发件箱。

    guard_pii=True 时对住户公告（公开）内容做隐私守卫，
    房号、姓名、手机号一律不得出现。
    """
    if guard_pii:
        _privacy_guard(conn, content)
    conn.execute(
        "INSERT INTO outbox(target,kind,content,created_at) VALUES(?,?,?,?)",
        (target, kind, content, now()),
    )


def _privacy_guard(conn: sqlite3.Connection, text: str) -> None:
    for r in conn.execute("SELECT unit,name,phone FROM residents").fetchall():
        for v in (r["name"], r["phone"], r["unit"]):
            if v and v in text:
                raise WorkflowError(f"公告内容泄露住户个人信息：{v}")
    import re
    if re.search(r"1[3-9]\d{9}", text):
        raise WorkflowError("公告内容疑似包含手机号")


# ---------------------------------------------------------------- 登记 / 照片

def register_item(conn, *, floor, location, x, y, description, secret_feature,
                  risk, flammable=False, resident_id=None, actor_id=None, ts=None):
    ts = ts or now()
    cur = conn.execute(
        """INSERT INTO items(code,floor,location,x,y,description,secret_feature,
                             risk,flammable,resident_id,status,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (f"A-{floor}F-{datetime.strptime(ts,'%Y-%m-%d %H:%M:%S').strftime('%H%M%S')}",
         floor, location, x, y, description, secret_feature, risk,
         int(flammable), resident_id, "registered", ts),
    )
    item_id = cur.lastrowid
    # 编号可能撞秒，简单加 id 后缀保证唯一
    code = f"A-{floor}F-{item_id:03d}"
    conn.execute("UPDATE items SET code=? WHERE id=?", (code, item_id))
    _log(conn, item_id, "register", f"巡查登记：{location}发现{description}，"
         f"消防风险级别：{risk}{'（易燃物）' if flammable else ''}", actor_id, ts)
    conn.commit()
    return item_id


def add_photo(conn, item_id, path, *, public=False, quality="ok", note=None):
    if quality not in ("ok", "blurry"):
        raise WorkflowError("照片质量标记只能是 ok/blurry")
    conn.execute(
        "INSERT INTO photos(item_id,path,public,quality,note,created_at) VALUES(?,?,?,?,?,?)",
        (item_id, path, int(public), quality, note, now()),
    )
    if quality == "blurry":
        _log(conn, item_id, "reseal_photo", f"照片不清（{note or '模糊'}），需重新拍摄补档")
    conn.commit()


# ---------------------------------------------------------------- 通知

def send_notice(conn, item_id, *, deadline_days=3, actor_id=None):
    """向相关楼层住户发出限期清理通知；无主物品改发匿名公告。"""
    conn.execute("BEGIN IMMEDIATE")
    item = _get(conn, item_id)
    if item["status"] not in PRE_NOTICE:
        conn.rollback()
        raise WorkflowError(f"当前状态 {item['status']} 不能再发通知")
    deadline = (datetime.now() + timedelta(days=deadline_days)
                ).strftime("%Y-%m-%d 23:59:59")
    conn.execute(
        "INSERT INTO notices(item_id,deadline,channel,created_at) VALUES(?,?,?,?)",
        (item_id, deadline, "mock", now()),
    )
    if item["resident_id"]:
        r = conn.execute("SELECT * FROM residents WHERE id=?",
                         (item["resident_id"],)).fetchone()
        # 私信可以含房号（收件人即本人）
        _send(conn, f"住户/{r['unit']}", "notice",
              f"【限期清理通知】{r['name']}您好：您{item['floor']}层"
              f"{item['location']}的{item['description']}占用公共楼道，"
              f"请于 {deadline} 前自行清理，逾期将统一封存。联系物业处理。")
    else:
        # 无主：公告只写楼层、位置、物品描述和公告编号
        _send(conn, "公告板/全体", "notice", _public_notice_text(item, deadline),
              guard_pii=True)
    conn.execute("UPDATE items SET status='noticed' WHERE id=?", (item_id,))
    _log(conn, item_id, "notify",
         f"发出限期清理通知，截止 {deadline}"
         + ("（匿名公告，主人不明）" if not item["resident_id"] else ""),
         actor_id)
    conn.commit()


def _public_notice_text(item, deadline: str) -> str:
    return (f"【楼道清理公告】编号 {item['code']}：{item['floor']}层"
            f"{item['location']}发现无人认领的{item['description']}，"
            f"占用公共疏散通道。请物品所有人于 {deadline} 前自行清理；"
            f"逾期将由物业双人清点封存。认领请到物业处描述物品非公开特征。")


# ---------------------------------------------------------------- 紧急移除（易燃物）

def urgent_remove(conn, item_id, *, actor_id, reason="易燃物，消防紧急移除"):
    """易燃物不等通知到期，立即紧急移除，随后进入封存流程。"""
    conn.execute("BEGIN IMMEDIATE")
    item = _get(conn, item_id)
    if not item["flammable"]:
        conn.rollback()
        raise WorkflowError("仅易燃物可走紧急移除流程")
    if item["status"] not in {"registered", "noticed"}:
        conn.rollback()
        raise WorkflowError(f"当前状态 {item['status']} 不能紧急移除")
    conn.execute("UPDATE items SET status='urgent_removed' WHERE id=?", (item_id,))
    _log(conn, item_id, "urgent_remove",
         f"{reason}，已立即移至安全暂存点，等待双人清点封存", actor_id)
    conn.commit()


# ---------------------------------------------------------------- 复查

def recheck(conn, item_id, *, still_present: bool, actor_id=None):
    """到期复查：物品还在 → 可封存；已清走 → 住户自清结案。"""
    conn.execute("BEGIN IMMEDIATE")
    item = _get(conn, item_id)
    if item["status"] not in {"noticed", "rechecked", "urgent_removed"}:
        conn.rollback()
        raise WorkflowError(f"当前状态 {item['status']} 无需复查")
    if still_present:
        conn.execute("UPDATE items SET status='rechecked' WHERE id=?", (item_id,))
        _log(conn, item_id, "recheck", "到期复查：物品仍在公共楼道，进入封存流程", actor_id)
    else:
        conn.execute(
            "UPDATE items SET status='removed_by_owner', resolved_at=? WHERE id=?",
            (now(), item_id),
        )
        _log(conn, item_id, "self_clear", "到期复查：住户已自行清走，楼道恢复畅通，结案", actor_id)
    conn.commit()


def confirm_self_clear(conn, item_id, *, actor_id=None, note="住户确认已自行清走"):
    """住户端/巡查员登记自清。与封存竞争同一把写锁，先到先得。"""
    conn.execute("BEGIN IMMEDIATE")
    item = _get(conn, item_id)
    if item["status"] in {"removed_by_owner", "sealed", "claimed", "disposed"}:
        conn.rollback()
        raise WorkflowError(f"物品已处于 {item['status']}，不能再登记自清")
    conn.execute(
        "UPDATE items SET status='removed_by_owner', resolved_at=? WHERE id=?",
        (now(), item_id),
    )
    _log(conn, item_id, "self_clear", note, actor_id)
    conn.commit()


# ---------------------------------------------------------------- 双人封存

def seal_item(conn, item_id, *, witness1_id, witness2_id, claim_days=7, actor_id=None):
    """两名员工清点、封存。必须两名不同员工；自清与封存并发时不重复封存。"""
    conn.execute("BEGIN IMMEDIATE")
    item = _get(conn, item_id)
    w1 = _staff(conn, witness1_id)
    w2 = _staff(conn, witness2_id)
    if witness1_id == witness2_id:
        conn.rollback()
        raise WorkflowError("封存必须由两名不同员工双签，不能同一人重复签名")
    if item["status"] not in SEALABLE:
        conn.rollback()
        raise WorkflowError(f"物品状态为 {item['status']}，不能封存（可能已被住户自清）")
    # 最新一张照片仍不清时不得封存（重拍合格后自动解除）
    last_q = conn.execute(
        "SELECT quality FROM photos WHERE item_id=? ORDER BY id DESC LIMIT 1",
        (item_id,)).fetchone()
    if last_q and last_q["quality"] == "blurry":
        conn.rollback()
        raise WorkflowError("最新照片不清，须重新拍摄补档后才能封存")
    box = f"BOX-{item['floor']}F-{item_id:03d}"
    deadline = (datetime.now() + timedelta(days=claim_days)
                ).strftime("%Y-%m-%d 23:59:59")
    conn.execute(
        """INSERT INTO seals(item_id,box_code,witness1_id,witness2_id,sealed_at,claim_deadline)
           VALUES(?,?,?,?,?,?)""",
        (item_id, box, witness1_id, witness2_id, now(), deadline),
    )
    conn.execute("UPDATE items SET status='sealed' WHERE id=?", (item_id,))
    _log(conn, item_id, "seal",
         f"由{w1['name']}、{w2['name']}双人清点，封存于 {box}；认领期限至 {deadline}",
         actor_id)
    if not item["resident_id"]:
        _send(conn, "公告板/全体", "notice",
              f"【封存认领公告】编号 {item['code']} 的物品已双人清点封存"
              f"（{box}）。如属您所有，请于 {deadline} 前到物业处描述物品"
              f"非公开特征办理认领。", guard_pii=True)
    conn.commit()
    return box


# ---------------------------------------------------------------- 认领（含错认）

def claim(conn, item_id, *, claimer, contact, feature, handler_id):
    """认领必须说出非公开特征；不符即错认，登记但不放行。"""
    conn.execute("BEGIN IMMEDIATE")
    item = _get(conn, item_id)
    handler = _staff(conn, handler_id)
    if item["status"] != "sealed":
        conn.rollback()
        raise WorkflowError(f"物品状态 {item['status']}，不在认领期")
    seal = conn.execute("SELECT * FROM seals WHERE item_id=?", (item_id,)).fetchone()
    if datetime.now() > datetime.strptime(seal["claim_deadline"], "%Y-%m-%d %H:%M:%S"):
        conn.rollback()
        raise WorkflowError("认领期限已过，进入到期处置流程")
    ok = feature.strip() == item["secret_feature"].strip()
    conn.execute(
        """INSERT INTO claims(item_id,claimer,contact,feature,accepted,handler_id,created_at)
           VALUES(?,?,?,?,?,?,?)""",
        (item_id, claimer, contact, feature, int(ok), handler_id, now()),
    )
    if ok:
        conn.execute("UPDATE items SET status='claimed', resolved_at=? WHERE id=?",
                     (now(), item_id))
        _log(conn, item_id, "claim",
             f"{claimer} 所述非公开特征核验通过，{handler['name']} 办理认领放行",
             handler_id)
        _send(conn, f"认领/{claimer}", "claim_ok",
              f"您认领的编号 {item['code']} 物品特征核验通过，请凭凭证领取封存箱。")
    else:
        _log(conn, item_id, "wrong_claim",
             f"{claimer} 自称物品主人，但所述非公开特征不符，判定为误认，物品继续封存",
             handler_id)
        _send(conn, f"认领/{claimer}", "claim_bad",
              f"您对编号 {item['code']} 物品的认领描述与登记特征不符，认领未通过；"
              f"该编号仍处于封存认领期。")
    conn.commit()
    return ok


# ---------------------------------------------------------------- 封存后损坏

def record_damage(conn, item_id, *, description, reporter_id):
    conn.execute("BEGIN IMMEDIATE")
    item = _get(conn, item_id)
    seal = conn.execute("SELECT * FROM seals WHERE item_id=?", (item_id,)).fetchone()
    if seal is None:
        conn.rollback()
        raise WorkflowError("物品未封存，无封存损坏可登记")
    reporter = _staff(conn, reporter_id)
    conn.execute(
        "INSERT INTO damages(seal_id,description,reporter_id,created_at) VALUES(?,?,?,?)",
        (seal["id"], description, reporter_id, now()),
    )
    _log(conn, item_id, "damage",
         f"封存后发现损坏：{description}（登记人：{reporter['name']}）", reporter_id)
    conn.commit()


# ---------------------------------------------------------------- 到期处置（主管批准）

def dispose(conn, item_id, *, method, approver_id):
    """认领期满后的处置：废弃/捐赠/变价，必须主管批准。"""
    conn.execute("BEGIN IMMEDIATE")
    item = _get(conn, item_id)
    approver = _staff(conn, approver_id)
    if approver["role"] != "supervisor":
        conn.rollback()
        raise PermissionError_("到期处置必须由主管批准，巡查员无权处置")
    if item["status"] != "sealed":
        conn.rollback()
        raise WorkflowError(f"物品状态 {item['status']}，不能处置")
    seal = conn.execute("SELECT * FROM seals WHERE item_id=?", (item_id,)).fetchone()
    if datetime.now() <= datetime.strptime(seal["claim_deadline"], "%Y-%m-%d %H:%M:%S"):
        conn.rollback()
        raise WorkflowError("认领期限未满，不得提前处置")
    if method not in ("discard", "donate", "auction"):
        conn.rollback()
        raise WorkflowError("处置方式只能是 discard/donate/auction")
    conn.execute(
        "INSERT INTO disposals(item_id,method,approver_id,created_at) VALUES(?,?,?,?)",
        (item_id, method, approver_id, now()),
    )
    conn.execute("UPDATE items SET status='disposed', resolved_at=? WHERE id=?",
                 (now(), item_id))
    label = {"discard": "统一废弃", "donate": "捐赠", "auction": "变价处理"}[method]
    _log(conn, item_id, "dispose",
         f"认领期满无人认领，经主管 {approver['name']} 批准，作{label}", approver_id)
    conn.commit()
