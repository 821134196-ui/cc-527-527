"""领域服务: 巡查登记 -> 通知 -> 复查 -> 双人封存 -> 认领 -> 到期处置。

关键规则:
  * 公告只含楼层/公共位置/物品类别/期限, 不含姓名、房号、电话。
  * 封存须两名不同员工双签; 照片不清禁止封存, 须先重拍。
  * 清理申请与住户自清并发: 条件更新 + 活动票唯一索引保证不会重复封存。
  * 认领凭非公开特征, 由员工复核; 邻居误认标记 mistaken, 物品继续封存。
  * 封存后到期处置必须主管批准。
"""
from __future__ import annotations

import json
import secrets as _secrets
import sqlite3
from datetime import datetime

from .clock import Clock
from .messages import MessageAdapter
from .storage import ImageStore

# -- 物品状态 ---------------------------------------------------------------
REGISTERED = "registered"            # 已巡查登记
NOTICED = "noticed"                  # 已发限期清理通知
REMOVED = "removed"                  # 复查时住户已自行清走
EMERGENCY_REMOVED = "emergency_removed"  # 易燃物紧急移除
SEALED = "sealed"                    # 双人封存, 认领期内
RETURNED = "returned"                # 认领成功已发还
DISPOSED = "disposed"                # 到期经主管批准处置

LEVEL_LABEL = {"high": "高风险", "medium": "中风险", "low": "低风险"}
STATUS_LABEL = {
    REGISTERED: "已登记", NOTICED: "已发通知", REMOVED: "住户已清走",
    EMERGENCY_REMOVED: "已紧急移除", SEALED: "已封存待认领",
    RETURNED: "已认领发还", DISPOSED: "已到期处置",
}


class ServiceError(Exception):
    """业务规则冲突。"""


class NeedReshootError(ServiceError):
    """照片不清, 不得封存。"""


class PermissionError_(ServiceError):
    """角色/双签/权限不满足。"""


class ConflictError(ServiceError):
    """并发冲突: 状态已被另一流程改变。"""


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _public_announcement(floor: int, position: str, description: str,
                         action: str, deadline: str | None) -> str:
    """公告模板: 只出现公共信息。姓名/房号/电话一律不进入文本。"""
    text = f"【楼道清理公告】{floor}层公共楼道{position}发现堆放物品（{description}）。{action}"
    if deadline:
        text += f"期限：{deadline}。"
    text += "请勿占用消防通道。"
    return text


class InspectorService:
    def __init__(self, conn: sqlite3.Connection, images: ImageStore,
                 messages: MessageAdapter, clock: Clock | None = None):
        self.conn = conn
        self.images = images
        self.msg = messages
        self.clock = clock or Clock()

    # -- 基础工具 -----------------------------------------------------------
    def _event(self, item_id: int, kind: str, actor: str, summary: str,
               payload: dict | None = None) -> None:
        self.conn.execute(
            "INSERT INTO events(item_id, kind, actor, summary, payload, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (item_id, kind, actor, summary,
             json.dumps(payload, ensure_ascii=False) if payload else None,
             _iso(self.clock.now())),
        )

    def get_item(self, item_id: int) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        if not row:
            raise ServiceError("物品不存在")
        return row

    def get_staff(self, name: str) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM staff WHERE name=?", (name,)).fetchone()
        if not row:
            raise PermissionError_(f"员工不存在: {name}")
        return row

    def timeline(self, item_id: int) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT * FROM events WHERE item_id=? ORDER BY id", (item_id,)
        ))

    # -- 巡查登记 -----------------------------------------------------------
    def register_item(self, floor: int, position: str, description: str,
                      level: str, actor: str, owner_id: int | None = None,
                      flammable: bool = False, photo: bytes | None = None,
                      photo_clear: bool = True) -> int:
        now = _iso(self.clock.now())
        year = self.clock.now().year
        seq = self.conn.execute("SELECT COUNT(*) n FROM items").fetchone()["n"] + 1
        code = f"C-{year}-{seq:03d}"
        cur = self.conn.execute(
            "INSERT INTO items(code,floor,position,description,fire_level,status,"
            "owner_id,is_flammable,photo_clear,discovered_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (code, floor, position, description, level, REGISTERED, owner_id,
             1 if flammable else 0, 1 if photo_clear else 0, now, now),
        )
        item_id = cur.lastrowid
        if photo:
            self._add_photo_row(item_id, "public", f"巡查登记照片 {code}", photo)
        if not photo_clear:
            self._event(item_id, "inspect", actor,
                        f"登记 {code}：{position}，{LEVEL_LABEL[level]}；照片不清，需重拍后才能封存",
                        {"level": level, "photo_clear": False})
        else:
            self._event(item_id, "inspect", actor,
                        f"登记 {code}：{position}，{LEVEL_LABEL[level]}", {"level": level})
        self.conn.commit()
        return item_id

    def _add_photo_row(self, item_id: int, kind: str, label: str, data: bytes) -> int:
        name = self.images.save_bytes(data, ".png")
        cur = self.conn.execute(
            "INSERT INTO item_photos(item_id,kind,path,label,created_at) VALUES (?,?,?,?,?)",
            (item_id, kind, name, label, _iso(self.clock.now())),
        )
        return cur.lastrowid

    def add_photo(self, item_id: int, kind: str, label: str, data: bytes) -> int:
        pid = self._add_photo_row(item_id, kind, label, data)
        self.conn.commit()
        return pid

    def reshoot(self, item_id: int, actor: str, photo: bytes) -> int:
        """重拍清晰照片后解除封存禁令。"""
        item = self.get_item(item_id)
        pid = self._add_photo_row(item_id, "public", "复查重拍清晰照片", photo)
        self.conn.execute("UPDATE items SET photo_clear=1, updated_at=? WHERE id=?",
                          (_iso(self.clock.now()), item_id))
        self._event(item_id, "reshoot", actor, "照片重拍清晰，解除封存限制")
        self.conn.commit()
        return pid

    def photos(self, item_id: int, public_only: bool = False) -> list[sqlite3.Row]:
        sql = "SELECT * FROM item_photos WHERE item_id=?"
        if public_only:
            sql += " AND kind='public'"
        sql += " ORDER BY id"
        return list(self.conn.execute(sql, (item_id,)))

    # -- 通知 ---------------------------------------------------------------
    def issue_notice(self, item_id: int, actor: str, days: int = 3) -> int:
        item = self.get_item(item_id)
        if item["status"] != REGISTERED:
            raise ConflictError(f"当前状态 {item['status']}，不能发送通知")
        deadline = self.clock.in_days(days)
        deadline_s = _iso(deadline)
        now = _iso(self.clock.now())
        notice_id: int | None = None
        if item["owner_id"]:
            owner = self.conn.execute(
                "SELECT * FROM residents WHERE id=?", (item["owner_id"],)
            ).fetchone()
            notice_id = self.conn.execute(
                "INSERT INTO notices(item_id,to_resident_id,deadline,created_at) VALUES (?,?,?,?)",
                (item_id, owner["id"], deadline_s, now),
            ).lastrowid
            self.msg.send(
                "sms", owner["phone"], "限期清理通知",
                f"{owner['name']}您好：{item['floor']}层公共楼道您堆放的"
                f"「{item['description']}」请于 {deadline_s} 前自行清理，"
                "逾期物品将被清点封存。",
            )
            self._event(item_id, "notice", actor,
                        f"向住户发送限期清理通知，截止 {deadline_s}",
                        {"deadline": deadline_s, "channel": "sms"})
        else:
            # 主人不明: 仅发公开公告, 不出现任何住户信息
            body = _public_announcement(
                item["floor"], item["position"], item["description"],
                "请物品所有人尽快自行清理；逾期将统一封存并开放认领。",
                deadline_s)
            self.conn.execute(
                "INSERT INTO announcements(item_id,floor,body,created_at) VALUES (?,?,?,?)",
                (item_id, item["floor"], body, now))
            self._event(item_id, "notice", actor,
                        f"主人不明，张贴公开公告，截止 {deadline_s}",
                        {"deadline": deadline_s, "announcement": body})
        self.conn.execute(
            "UPDATE items SET status=?, notice_deadline=?, updated_at=? WHERE id=?",
            (NOTICED, deadline_s, now, item_id))
        self.conn.commit()
        return notice_id or 0

    def announcements(self) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT * FROM announcements ORDER BY id DESC"))

    # -- 工作票 (并发互斥) ---------------------------------------------------
    def begin_cleanup_request(self, item_id: int, actor: str) -> int:
        """员工发起清理(封存)申请, 占一张活动票。"""
        item = self.get_item(item_id)
        if item["status"] not in (NOTICED,):
            raise ConflictError(f"当前状态 {item['status']}，无需封存")
        try:
            cur = self.conn.execute(
                "INSERT INTO clear_tickets(item_id,kind,status,actor,created_at) "
                "VALUES (?,?, 'active',?,?)",
                (item_id, "seal", actor, _iso(self.clock.now())))
            self.conn.commit()
            return cur.lastrowid
        except sqlite3.IntegrityError:
            self.conn.rollback()
            raise ConflictError("该物品已有进行中的清理/自清单据")

    def _cancel_active_tickets(self, item_id: int, reason: str) -> None:
        self.conn.execute(
            "UPDATE clear_tickets SET status='cancelled' WHERE item_id=? AND status='active'",
            (item_id,))

    # -- 复查 ---------------------------------------------------------------
    def recheck(self, item_id: int, actor: str, found_present: bool) -> str:
        item = self.get_item(item_id)
        if item["status"] != NOTICED:
            raise ConflictError(f"当前状态 {item['status']}，不能复查")
        if found_present:
            self._event(item_id, "recheck", actor, "复查：物品仍在楼道，待逾期封存")
            self.conn.commit()
            return "present"
        return self.confirm_self_clear(item_id, actor, via_recheck=True)

    def confirm_self_clear(self, item_id: int, actor: str,
                           via_recheck: bool = False) -> str:
        """住户已自行清走 (复查确认或住户自清回执)。与封存互斥。"""
        now = _iso(self.clock.now())
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            item = self.get_item(item_id)
            if item["status"] == REMOVED:
                self.conn.commit()
                return REMOVED
            if item["status"] == SEALED:
                # 封存已先完成: 自清无效, 住户应走认领流程
                self._event(item_id, "self_clear", actor,
                            "住户自清送达时物品已封存，转认领流程处理")
                self.conn.commit()
                raise ConflictError("物品已封存，不能按自清处理，请走认领流程")
            if item["status"] != NOTICED:
                raise ConflictError(f"当前状态 {item['status']}，不能确认自清")
            cur = self.conn.execute(
                "UPDATE items SET status=?, updated_at=? WHERE id=? AND status=?",
                (REMOVED, now, item_id, NOTICED))
            if cur.rowcount != 1:
                raise ConflictError("并发冲突：物品状态已改变，未重复封存")
            self._cancel_active_tickets(item_id, "self_clear")
            self.conn.execute(
                "INSERT INTO clear_tickets(item_id,kind,status,actor,created_at) "
                "VALUES (?, 'self_clear','done',?,?)", (item_id, actor, now))
            label = "复查确认物品已由住户自行清走，封存流程终止" if via_recheck \
                else "收到住户自清确认，复查通过，未再封存"
            self._event(item_id, "self_clear", actor, label)
            self.conn.commit()
            return REMOVED
        except ServiceError:
            self.conn.rollback()
            raise
        except sqlite3.OperationalError as e:
            self.conn.rollback()
            raise ConflictError(f"并发冲突，请重试: {e}")

    # -- 易燃物紧急移除 ------------------------------------------------------
    def emergency_remove(self, item_id: int, actor: str, note: str) -> None:
        item = self.get_item(item_id)
        if not item["is_flammable"]:
            raise ServiceError("仅易燃/危险品可走紧急移除流程")
        if item["status"] in (REMOVED, RETURNED, DISPOSED, EMERGENCY_REMOVED):
            raise ConflictError(f"当前状态 {item['status']}，不能紧急移除")
        now = _iso(self.clock.now())
        self.conn.execute("UPDATE items SET status=?, updated_at=? WHERE id=?",
                          (EMERGENCY_REMOVED, now, item_id))
        self._cancel_active_tickets(item_id, "emergency")
        self._event(item_id, "emergency", actor,
                    f"易燃物品紧急移除至安全暂存点：{note}",
                    {"flammable": True})
        body = _public_announcement(
            item["floor"], item["position"], item["description"],
            "因属易燃物品已紧急移除，可凭非公开特征联系物业认领。", None)
        self.conn.execute(
            "INSERT INTO announcements(item_id,floor,body,created_at) VALUES (?,?,?,?)",
            (item_id, item["floor"], body, now))
        # 通知主管
        sup = self.conn.execute(
            "SELECT * FROM staff WHERE role='supervisor' ORDER BY id LIMIT 1").fetchone()
        if sup:
            self.msg.send("internal", sup["name"], "易燃物紧急移除报备",
                          f"{actor} 紧急移除了 {item['code']}（{item['description']}）：{note}")
        self.conn.commit()

    # -- 双人封存 -----------------------------------------------------------
    def seal_item(self, item_id: int, witness1: str, witness2: str,
                  inventory: list[str], storage_loc: str, claim_days: int = 7) -> int:
        w1 = self.get_staff(witness1)
        w2 = self.get_staff(witness2)
        if w1["id"] == w2["id"]:
            raise PermissionError_("封存必须由两名不同员工双签")
        now = _iso(self.clock.now())
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            item = self.get_item(item_id)
            if item["status"] != NOTICED:
                raise ConflictError(f"当前状态 {item['status']}，不能封存")
            if not item["photo_clear"]:
                raise NeedReshootError("登记照片不清，须重拍清晰照片后方可封存")
            deadline_ts = datetime.fromisoformat(item["notice_deadline"])
            if self.clock.now() < deadline_ts:
                raise ConflictError("清理期限未到，不能提前封存")
            cur = self.conn.execute(
                "UPDATE items SET status=?, updated_at=? WHERE id=? AND status=?",
                (SEALED, now, item_id, NOTICED))
            if cur.rowcount != 1:
                raise ConflictError("并发冲突：物品已被自清或封存流程处理，未重复封存")
            secret = "内件-" + _secrets.token_hex(3)
            claim_deadline = _iso(self.clock.in_days(claim_days))
            seal_id = self.conn.execute(
                "INSERT INTO seals(item_id,sealed_at,witness1_id,witness2_id,"
                "inventory,storage_loc,claim_deadline,claim_secret) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (item_id, now, w1["id"], w2["id"],
                 json.dumps(inventory, ensure_ascii=False),
                 storage_loc, claim_deadline, secret),
            ).lastrowid
            self.conn.execute(
                "UPDATE clear_tickets SET status='done' "
                "WHERE item_id=? AND kind='seal' AND status='active'", (item_id,))
            body = _public_announcement(
                item["floor"], item["position"], item["description"],
                f"逾期物品已于 {now} 由物业双人清点封存，认领须说明物品非公开特征。",
                claim_deadline)
            self.conn.execute(
                "INSERT INTO announcements(item_id,floor,body,created_at) VALUES (?,?,?,?)",
                (item_id, item["floor"], body, now))
            self._event(item_id, "seal", f"{w1['name']}、{w2['name']}",
                        f"双人清点封存（{w1['name']}、{w2['name']}），"
                        f"存放于{storage_loc}，认领截止 {claim_deadline}",
                        {"witness1": w1["name"], "witness2": w2["name"],
                         "inventory": inventory, "claim_deadline": claim_deadline})
            self.conn.commit()
            return seal_id
        except ServiceError:
            self.conn.rollback()
            raise
        except (sqlite3.OperationalError, sqlite3.IntegrityError) as e:
            self.conn.rollback()
            if isinstance(e, sqlite3.IntegrityError):
                raise ConflictError("并发冲突：封存单据已存在，未重复封存")
            raise ConflictError(f"并发冲突，请重试: {e}")

    def get_seal(self, item_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT s.*, a.name w1name, b.name w2name FROM seals s "
            "JOIN staff a ON a.id=s.witness1_id JOIN staff b ON b.id=s.witness2_id "
            "WHERE s.item_id=?", (item_id,)).fetchone()

    # -- 认领 ---------------------------------------------------------------
    def submit_claim(self, item_id: int, claimant: str, contact: str,
                     feature: str) -> int:
        item = self.get_item(item_id)
        if item["status"] != SEALED:
            raise ConflictError("该物品当前不在认领期内")
        seal = self.get_seal(item_id)
        if self.clock.now().isoformat(timespec="seconds") > seal["claim_deadline"]:
            raise ConflictError("认领期限已过，等待统一处置")
        ok = feature.strip() == seal["claim_secret"].strip()
        claim_id = self.conn.execute(
            "INSERT INTO claims(seal_id,claimant,contact,feature,status,created_at) "
            "VALUES (?,?,?,?, 'pending',?)",
            (seal["id"], claimant, contact, feature.strip(), _iso(self.clock.now()))
        ).lastrowid
        self._event(item_id, "claim", claimant,
                    f"收到认领申请（联系方式已隐去），特征{'相符' if ok else '不符'}，待员工复核",
                    {"feature_match": ok})
        self.conn.commit()
        return claim_id

    def review_claim(self, claim_id: int, reviewer: str, accept: bool,
                     note: str = "") -> str:
        staff = self.get_staff(reviewer)
        claim = self.conn.execute("SELECT * FROM claims WHERE id=?", (claim_id,)).fetchone()
        if not claim:
            raise ServiceError("认领申请不存在")
        if claim["status"] != "pending":
            raise ConflictError("该申请已复核")
        seal = self.conn.execute("SELECT * FROM seals WHERE id=?",
                                 (claim["seal_id"],)).fetchone()
        feature_ok = claim["feature"].strip() == seal["claim_secret"].strip()
        accept = accept and feature_ok
        now = _iso(self.clock.now())
        if accept:
            item = self.get_item(seal["item_id"])
            if item["status"] != SEALED:
                raise ConflictError("物品已不在封存状态")
            self.conn.execute(
                "UPDATE claims SET status='matched', reviewed_by=?, note=? WHERE id=?",
                (staff["id"], note, claim_id))
            self.conn.execute("UPDATE items SET status=?, updated_at=? WHERE id=?",
                              (RETURNED, now, seal["item_id"]))
            self.conn.execute("UPDATE seals SET damage_note=damage_note WHERE id=?", (seal["id"],))
            self._event(seal["item_id"], "claim_review", reviewer,
                        f"非公开特征核对相符，认领发还（认领人信息不公开）",
                        {"result": "matched"})
            self.msg.send("sms", claim["contact"], "认领结果",
                          "您的认领特征核对相符，物品已发还，请凭有效联系方式领取。")
        else:
            reason = "非公开特征不符" + (f"；{note}" if note else "")
            self.conn.execute(
                "UPDATE claims SET status='mistaken', reviewed_by=?, note=? WHERE id=?",
                (staff["id"], reason, claim_id))
            self._event(seal["item_id"], "claim_review", reviewer,
                        f"认领人所述特征与封存记录不符，判为误认，物品继续封存待真正主人",
                        {"result": "mistaken", "reason": reason})
            self.msg.send("sms", claim["contact"], "认领结果",
                          "您提供的物品特征与封存记录不符，本次认领未通过，物品继续封存。")
        self.conn.commit()
        return "matched" if accept else "mistaken"

    def claims(self, item_id: int) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT c.*, s.name reviewer_name FROM claims c "
            "LEFT JOIN staff s ON s.id=c.reviewed_by "
            "JOIN seals se ON se.id=c.seal_id WHERE se.item_id=? ORDER BY c.id",
            (item_id,)))

    # -- 封存后损坏 ---------------------------------------------------------
    def report_damage(self, item_id: int, actor: str, note: str,
                      photo: bytes | None = None) -> None:
        item = self.get_item(item_id)
        seal = self.get_seal(item_id)
        if not seal:
            raise ServiceError("物品未封存，无封存损坏可登记")
        if photo:
            self._add_photo_row(item_id, "damage", f"封存后损坏：{note}", photo)
        prev = seal["damage_note"] or ""
        merged = (prev + " | " if prev else "") + f"[{_iso(self.clock.now())}] {actor}：{note}"
        self.conn.execute("UPDATE seals SET damage_note=? WHERE id=?", (merged, seal["id"]))
        self._event(item_id, "damage", actor, f"封存后损坏登记：{note}", {"note": note})
        sup = self.conn.execute(
            "SELECT * FROM staff WHERE role='supervisor' ORDER BY id LIMIT 1").fetchone()
        if sup:
            self.msg.send("internal", sup["name"], "封存物品损坏报备",
                          f"{actor} 登记 {item['code']} 封存后损坏：{note}")
        self.conn.commit()

    # -- 到期处置 (主管批准) -------------------------------------------------
    def dispose(self, item_id: int, actor: str, decision: str) -> int:
        staff = self.get_staff(actor)
        if staff["role"] != "supervisor":
            raise PermissionError_("到期处置必须由主管批准")
        item = self.get_item(item_id)
        if item["status"] != SEALED:
            raise ConflictError(f"当前状态 {item['status']}，不能处置")
        seal = self.get_seal(item_id)
        now_ts = self.clock.now().isoformat(timespec="seconds")
        if now_ts < seal["claim_deadline"]:
            raise ConflictError("认领期限未到，不能处置")
        matched = self.conn.execute(
            "SELECT COUNT(*) n FROM claims WHERE seal_id=? AND status='matched'",
            (seal["id"],)).fetchone()["n"]
        pending = self.conn.execute(
            "SELECT COUNT(*) n FROM claims WHERE seal_id=? AND status='pending'",
            (seal["id"],)).fetchone()["n"]
        if matched:
            raise ConflictError("已有认领成功记录，不得处置")
        if pending:
            raise ConflictError("仍有待复核认领申请，先完成复核")
        self.conn.execute("UPDATE items SET status=?, updated_at=? WHERE id=?",
                          (DISPOSED, now_ts, item_id))
        did = self.conn.execute(
            "INSERT INTO disposals(seal_id,decision,approved_by,created_at) VALUES (?,?,?,?)",
            (seal["id"], decision, staff["id"], now_ts)).lastrowid
        self._event(item_id, "disposal", actor,
                    f"认领期满无人认领，主管批准{decision}处置", {"decision": decision})
        self.conn.commit()
        return did

    # -- 查询 ---------------------------------------------------------------
    def list_items(self) -> list[sqlite3.Row]:
        """紧急(高风险且未结案)排最前, 其次状态、楼层。"""
        return list(self.conn.execute(
            "SELECT i.*, r.name owner_name, r.room owner_room FROM items i "
            "LEFT JOIN residents r ON r.id=i.owner_id "
            "ORDER BY CASE WHEN i.fire_level='high' AND i.status IN "
            "('registered','noticed','sealed') THEN 0 ELSE 1 END, "
            "i.status, i.floor, i.id"))

    def claim_open_items(self) -> list[sqlite3.Row]:
        """住户端: 仅封存待认领且在认领期内的物品, 只附公开照片。"""
        now = self.clock.now().isoformat(timespec="seconds")
        return list(self.conn.execute(
            "SELECT i.* FROM items i JOIN seals s ON s.item_id=i.id "
            "WHERE i.status='sealed' AND s.claim_deadline>=? ORDER BY i.floor, i.id",
            (now,)))
