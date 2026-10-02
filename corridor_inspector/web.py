"""本地 HTTP 界面 (标准库 http.server, 无第三方依赖)。

页面:
  /staff    员工总览: 楼层平面示意 + 物品列表(紧急置前) + 公告 + 模拟消息发件箱
  /event    事件页: 照片/时间线, 按 通知→复查→封存 分区, 含操作表单
  /resident 住户端: 公开公告 + 可认领物品(仅公开照片) + 认领表单
"""
from __future__ import annotations

import html
import json
import urllib.parse
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import images
from .clock import Clock
from .db import connect, init_db
from .messages import MessageAdapter
from .plan import floor_plan_svg
from .seed import DEFAULT_DB, DEFAULT_IMG, seed
from .services import (LEVEL_LABEL, NOTICED, REGISTERED, SEALED, STATUS_LABEL,
                       ConflictError, InspectorService, NeedReshootError,
                       PermissionError_, ServiceError)
from .storage import ImageStore

BASE_DIR = Path(__file__).resolve().parent.parent

CSS = """
:root{--gray:#f0f1f2; --line:#c9cfca; --green:#1f5a42; --green-d:#15402f;
  --red:#c22020; --ink:#333;}
*{box-sizing:border-box}
body{margin:0;background:var(--gray);color:var(--ink);
  font:14px/1.6 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}
header{background:var(--green);color:#fff;padding:10px 22px;display:flex;
  align-items:center;gap:18px;flex-wrap:wrap}
header a{color:#dff0e8;text-decoration:none}
header a:hover{color:#fff}
h1{font-size:17px;margin:0;font-weight:600}
.wrap{max-width:1080px;margin:18px auto;padding:0 16px}
.card{background:#fff;border:1px solid var(--line);border-radius:10px;
  padding:14px 16px;margin:14px 0}
.card h2{font-size:15px;margin:0 0 10px;color:var(--green-d)}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{padding:6px 8px;border-bottom:1px solid #e6e9e7;text-align:left;vertical-align:top}
th{color:var(--green-d);font-weight:600;background:#f6f8f7}
a{color:var(--green)}
.btn{display:inline-block;background:var(--green);color:#fff;border:0;border-radius:6px;
  padding:5px 12px;font-size:13px;cursor:pointer;text-decoration:none}
.btn:hover{background:var(--green-d)}
.btn-red{background:var(--red)} .btn-red:hover{background:#9d1818}
.btn-ghost{background:#fff;color:var(--green);border:1px solid var(--green)}
.badge{display:inline-block;border-radius:10px;padding:1px 9px;font-size:12px;color:#fff}
.lv-high{background:var(--red)} .lv-medium{background:#c88214} .lv-low{background:var(--green)}
.st-high{color:var(--red);font-weight:600}
.urgent-row td{background:#fdf0f0}
.muted{color:#777;font-size:12px}
input,select,textarea{padding:4px 7px;border:1px solid var(--line);border-radius:5px;
  font-size:13px;background:#fff}
label.field{display:inline-flex;flex-direction:column;gap:2px;margin:3px 10px 3px 0;
  font-size:12px;color:#555}
.timeline li{margin:4px 0}.tl-kind{display:inline-block;min-width:64px;color:var(--green-d)}
.photos{display:flex;gap:10px;flex-wrap:wrap}
.photos figure{margin:0;width:180px}
.photos img{width:180px;border:1px solid var(--line);border-radius:6px}
figcaption{font-size:12px;color:#555}
.tag-private{color:var(--red);border:1px solid var(--red);border-radius:4px;
  padding:0 5px;font-size:11px}
.tag-public{color:var(--green);border:1px solid var(--green);border-radius:4px;
  padding:0 5px;font-size:11px}
.plan{width:100%;height:auto;margin:6px 0}
.room-label{fill:#5c6660;font-size:12px}
.floor-tag{fill:var(--green);font-size:15px;font-weight:700}
.dot-label{fill:var(--ink);font-size:10px;font-weight:700}
.section-sep{color:var(--green);font-weight:600;border-top:2px solid #dfe7e3;
  margin-top:10px;padding-top:6px}
.flash{background:#eaf4ee;border:1px solid #b9d8c7;border-radius:8px;padding:8px 12px;
  margin:10px 0}
.flash-err{background:#fcebeb;border-color:#e4b4b4}
.msg-box{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;
  background:#f6f8f7;border-radius:6px;padding:8px;margin:6px 0;white-space:pre-wrap}
.private-hidden{color:#999;font-style:italic}
"""


def esc(v) -> str:
    return html.escape("" if v is None else str(v))


def lv_badge(level: str) -> str:
    return f'<span class="badge lv-{level}">{LEVEL_LABEL[level]}</span>'


class App:
    def __init__(self, db_path: Path = DEFAULT_DB, img_dir: Path = DEFAULT_IMG,
                 clock: Clock | None = None):
        self.db_path = db_path
        self.img_dir = img_dir
        self.clock = clock or Clock()

    def fresh(self):
        conn = connect(self.db_path)
        store = ImageStore(self.img_dir)
        msg = MessageAdapter(conn, self.clock)
        svc = InspectorService(conn, store, msg, self.clock)
        return conn, svc, store

    # ------------------------------------------------------------------ pages
    def staff_dashboard(self, q: dict, cookie: str) -> str:
        conn, svc, _ = self.fresh()
        staff_as = self._who(cookie, conn)
        items = svc.list_items()
        floors = sorted({r["floor"] for r in items}, reverse=True)
        plans = ""
        for f in floors:
            fitems = [r for r in items if r["floor"] == f]
            plans += f'<h3 class="section-sep">{f} 层</h3>' + floor_plan_svg(f, fitems)
        rows = ""
        for it in items:
            urgent = it["fire_level"] == "high" and it["status"] in (
                "registered", "noticed", "sealed")
            owner = esc(it["owner_name"] and f"{it['owner_name']}（{it['owner_room']}）"
                        or "主人不明")
            rows += (
                f'<tr class="{"urgent-row" if urgent else ""}">'
                f'<td>{"🔴 " if urgent else ""}<a href="/event?id={it["id"]}">{esc(it["code"])}</a></td>'
                f'<td>{it["floor"]}F {esc(it["position"])}</td>'
                f'<td>{esc(it["description"])}</td>'
                f'<td>{lv_badge(it["fire_level"])}</td>'
                f'<td class="{"st-high" if urgent else ""}">{STATUS_LABEL[it["status"]]}</td>'
                f'<td data-pii>{owner}</td>'
                f'<td class="muted">{esc(it["updated_at"])}</td></tr>')
        announces = ""
        for a in svc.announcements():
            announces += f'<div class="msg-box">📢 {esc(a["body"])}</div>'
        outbox = ""
        for m in svc.msg.outbox(12):
            outbox += (f'<div class="msg-box">[{esc(m["channel"])}] → {esc(m["to_contact"])} '
                       f'<b>{esc(m["subject"])}</b>\n{esc(m["body"])}</div>')
        staff_opts = "".join(
            f'<option {"selected" if s["name"]==staff_as else ""}>{esc(s["name"])}</option>'
            for s in conn.execute("SELECT * FROM staff ORDER BY role DESC,id"))
        resident_opts = '<option value="">主人不明（仅公告）</option>' + "".join(
            f'<option value="{r["id"]}">{r["room"]} {esc(r["name"])}</option>'
            for r in conn.execute("SELECT * FROM residents ORDER BY room"))
        conn.close()
        return self._wrap("员工端 · 楼道巡查总览", f"""
<header><h1>楼道占用巡查 · 员工端</h1>
<a href="/staff">总览/楼层平面</a><a href="/resident">住户端视图</a>
<form method="get" action="/staff" style="margin-left:auto;display:flex;gap:6px;align-items:center">
  <span>当前操作人</span><select name="as" onchange="this.form.submit()">{staff_opts}</select>
</form></header>
<div class="wrap">
  <div class="card"><h2>楼层平面示意（点位颜色=风险级别，🔴脉冲为待处置紧急项）</h2>{plans}</div>
  <div class="card"><h2>巡查物品（紧急项排在最前）</h2>
  <table><thead><tr><th>编号</th><th>位置</th><th>物品</th><th>消防风险</th>
  <th>状态</th><th data-pii>登记住户</th><th>更新时间</th></tr></thead><tbody>{rows}</tbody></table></div>
  <div class="card"><h2>公开公告（对外，无个人信息）</h2>{announces or '<p class="muted">暂无</p>'}</div>
  <div class="card"><h2>模拟消息适配器发件箱</h2>{outbox or '<p class="muted">暂无</p>'}</div>
  <div class="card"><h2>新巡查登记</h2>
  <form method="post" action="/act/register">
    <input type="hidden" name="actor" value="{esc(staff_as)}">
    <label class="field">楼层<input name="floor" value="3" size="4"></label>
    <label class="field">公共位置<input name="position" value="中段消火栓旁" size="16"></label>
    <label class="field">物品描述<input name="description" value="杂物一堆" size="16"></label>
    <label class="field">风险级别<select name="level">
      <option value="low">低风险</option><option value="medium">中风险</option>
      <option value="high">高风险</option></select></label>
    <label class="field">登记住户<select name="owner_id">{resident_opts}</select></label>
    <label class="field"><span style="height:18px"></span>
      <span><input type="checkbox" name="flammable" value="1"> 易燃/危险品</span></label>
    <label class="field"><span style="height:18px"></span>
      <span><input type="checkbox" name="photo_clear" value="0"> 照片不清</span></label>
    <button class="btn">登记</button>
  </form></div>
</div>""")

    def event_page(self, q: dict, cookie: str, flash: str = "") -> str:
        conn, svc, _ = self.fresh()
        item_id = int(q.get("id", ["0"])[0]) if isinstance(q.get("id"), list) else int(q["id"])
        item = svc.get_item(item_id)
        staff_as = self._who(cookie, conn)
        seal = svc.get_seal(item_id)

        photos_html = ""
        for p in svc.photos(item_id):
            tag = ('<span class="tag-private">非公开</span>' if p["kind"] != "public"
                   else '<span class="tag-public">可公开</span>')
            photos_html += (
                f'<figure><img src="/img?name={esc(p["path"])}">'
                f'<figcaption>{tag} {esc(p["label"])}</figcaption></figure>')
        if not photos_html:
            photos_html = '<p class="muted">暂无照片</p>'

        events = svc.timeline(item_id)
        groups = {"notice": [], "recheck": [], "seal": [], "other": []}
        order = {"inspect": "other", "reshoot": "other", "notice": "notice",
                 "recheck": "recheck", "self_clear": "recheck", "emergency": "other",
                 "seal": "seal", "claim": "other", "claim_review": "other",
                 "damage": "other", "disposal": "other"}
        for e in events:
            groups[order.get(e["kind"], "other")].append(e)

        def tl(lst):
            return "<ul class='timeline'>" + "".join(
                f'<li><span class="tl-kind">{esc(e["created_at"])}</span> '
                f'{esc(e["summary"])} <span class="muted">— {esc(e["actor"])}</span></li>'
                for e in lst) + "</ul>" if lst else '<p class="muted">无</p>'

        actions = self._action_panel(conn, svc, item, seal, staff_as)

        info = (
            f'<table><tr><th>编号</th><td>{esc(item["code"])}</td>'
            f'<th>楼层位置</th><td>{item["floor"]}F {esc(item["position"])}</td></tr>'
            f'<tr><th>物品</th><td>{esc(item["description"])}</td>'
            f'<th>消防风险</th><td>{lv_badge(item["fire_level"])}'
            f'{" 易燃/危险" if item["is_flammable"] else ""}</td></tr>'
            f'<tr><th>状态</th><td>{STATUS_LABEL[item["status"]]}'
            f'{" <span class=tag-private>照片不清·禁止封存</span>" if not item["photo_clear"] else ""}'
            f'</td><th>清理期限</th><td>{esc(item["notice_deadline"]) or "—"}</td></tr></table>')

        seal_html = "<p class='muted'>未封存</p>"
        if seal:
            inv = "、".join(json.loads(seal["inventory"]))
            claims = svc.claims(item_id)
            claim_rows = "".join(
                f'<tr><td>{esc(c["created_at"])}</td><td data-pii>{esc(c["claimant"])}</td>'
                f'<td data-pii>{esc(c["contact"])}</td>'
                f'<td><details><summary><span class="tag-private">特征已隐藏</span> 点击展开</summary>'
                f'<span class="private-hidden">{esc(c["feature"])}</span></details></td>'
                f'<td>{ {"pending":"待复核","matched":"相符已发还","mistaken":"误认","rejected":"驳回"}[c["status"]] }</td>'
                f'<td>{esc(c["reviewer_name"] or "")}</td>'
                f'<td>{esc(c["note"] or "")}</td></tr>' for c in claims)
            seal_html = f"""
<table><tr><th>封存时间</th><td>{esc(seal['sealed_at'])}</td>
<th>双签员工</th><td>✅ {esc(seal['w1name'])} ＋ ✅ {esc(seal['w2name'])}</td></tr>
<tr><th>清点清单</th><td colspan="3">{esc(inv)}</td></tr>
<tr><th>存放位置</th><td>{esc(seal['storage_loc'])}</td>
<th>认领截止</th><td>{esc(seal['claim_deadline'])}</td></tr>
<tr><th>认领特征</th><td colspan="3"><details><summary class="muted">非公开，复核时展开</summary>
<span class="private-hidden">{esc(seal['claim_secret'])}</span></details></td></tr>
<tr><th>封存后损坏</th><td colspan="3">{esc(seal['damage_note']) or "无"}</td></tr></table>
<h3 class="section-sep">认领记录（含邻居误认样例）</h3>
<table><thead><tr><th>时间</th><th data-pii>认领人</th><th data-pii>联系方式</th>
<th>所述非公开特征</th><th>结果</th><th>复核人</th><th>备注</th></tr></thead>
<tbody>{claim_rows or '<tr><td colspan=7 class=muted>暂无</td></tr>'}</tbody></table>"""

        conn.close()
        return self._wrap(f"事件 {item['code']}", f"""
<header><h1>事件页 · {esc(item["code"])}</h1>
<a href="/staff">← 返回总览</a><a href="/resident">住户端视图</a></header>
<div class="wrap">{flash}
  <div class="card"><h2>基本信息</h2>{info}</div>
  <div class="card"><h2>照片（住户端只显示“可公开”）</h2><div class="photos">{photos_html}</div></div>
  <div class="card"><h2>操作</h2>{actions}</div>
  <div class="card"><h2>① 通知</h2>{tl(groups["notice"])}</div>
  <div class="card"><h2>② 复查 / 自清 / 紧急移除</h2>{tl(groups["recheck"] + [e for e in groups["other"] if e["kind"]=="emergency"])}</div>
  <div class="card"><h2>③ 封存 / 认领 / 处置</h2>
    {tl([e for e in groups["seal"]] + [e for e in groups["other"] if e["kind"] in ("claim","claim_review","damage","disposal")])}
    {seal_html}</div>
  <div class="card"><h2>完整时间线（登记→通知→复查→封存→认领→处置）</h2>{tl(events)}</div>
</div>""")

    def _action_panel(self, conn, svc, item, seal, staff_as) -> str:
        iid = item["id"]
        hidden = f'<input type="hidden" name="id" value="{iid}"><input type="hidden" name="actor" value="{esc(staff_as)}">'
        staff_rows = list(conn.execute("SELECT * FROM staff ORDER BY id"))
        staff_opts = "".join(
            f'<option {"selected" if s["name"]==staff_as else ""}>{esc(s["name"])}</option>'
            for s in staff_rows)
        other_opts = "".join(
            f'<option {"selected" if s["name"]!=staff_as else ""}>{esc(s["name"])}</option>'
            for s in staff_rows)
        is_super = any(s["name"] == staff_as and s["role"] == "supervisor"
                       for s in staff_rows)
        parts = []
        now_s = svc.clock.now().isoformat(timespec="seconds")

        if item["status"] == REGISTERED:
            parts.append(f"""<form method="post" action="/act/notice">{hidden}
              <button class="btn">发送限期清理通知（有主→私信；无主→公开公告）</button></form>""")
        if item["status"] == REGISTERED and item["is_flammable"]:
            parts.append(f"""<form method="post" action="/act/emergency">{hidden}
              <input name="note" placeholder="紧急移除情况说明" size="40">
              <button class="btn btn-red">易燃物紧急移除并报备主管</button></form>""")
        if item["status"] == NOTICED:
            overdue = item["notice_deadline"] and now_s >= item["notice_deadline"]
            if not item["photo_clear"]:
                parts.append(f"""<form method="post" action="/act/reshoot">{hidden}
                  <span class="tag-private">照片不清</span>
                  <button class="btn btn-ghost">现场重拍并上传清晰照片</button></form>""")
            parts.append(f"""<form method="post" action="/act/recheck" style="display:inline">{hidden}
              <input type="hidden" name="present" value="1">
              <button class="btn btn-ghost">复查：物品仍在</button></form>
              <form method="post" action="/act/recheck" style="display:inline">{hidden}
              <input type="hidden" name="present" value="0">
              <button class="btn">复查：住户已自行清走</button></form>
              <form method="post" action="/act/self-clear" style="display:inline">{hidden}
              <button class="btn">住户自清回执（与封存并发互斥）</button></form>""")
            if item["is_flammable"]:
                parts.append(f"""<form method="post" action="/act/emergency">{hidden}
                  <input name="note" placeholder="紧急移除情况说明" size="40">
                  <button class="btn btn-red">易燃物紧急移除</button></form>""")
            can_seal = overdue and item["photo_clear"]
            parts.append(f"""<form method="post" action="/act/seal" style="margin-top:8px">{hidden}
              <fieldset style="border:1px solid #c9cfca;border-radius:8px">
              <legend class="muted">逾期双人封存（{ "可封存" if can_seal else ("期限未到" if not overdue else "照片不清，禁止封存") }）</legend>
              <label class="field">清点员工一<select name="w1">{staff_opts}</select></label>
              <label class="field">清点员工二<select name="w2">{other_opts}</select></label>
              <label class="field">清点清单（每行一件）<textarea name="inventory" rows="2" cols="34">{esc(item['description'])}×1</textarea></label>
              <label class="field">存放位置<input name="storage_loc" value="物业B1暂存间A架"></label>
              <button class="btn {"btn-red" if can_seal else ""}" {" " if can_seal else "disabled"}>两名员工清点封存</button>
              </fieldset></form>""")
        if item["status"] == SEALED and seal:
            expired = now_s >= seal["claim_deadline"]
            parts.append(f"""<form method="post" action="/act/damage" style="display:inline">{hidden}
              <input name="note" placeholder="封存后损坏情况" size="34">
              <button class="btn btn-ghost">登记封存后损坏（自动报备主管）</button></form>""")
            if expired:
                parts.append(f"""<form method="post" action="/act/dispose" style="display:inline">{hidden}
                  <label class="field">处置方式<select name="decision">
                    <option value="统一清运销毁">统一清运销毁</option>
                    <option value="捐赠">捐赠</option></select></label>
                  {"<button class='btn btn-red'>主管批准到期处置</button>" if is_super else "<span class='tag-private'>仅主管可批准到期处置（当前操作人不是主管）</span>"}</form>""")
            else:
                parts.append('<p class="muted">认领期未满，不可处置。</p>')
            pending = conn.execute(
                "SELECT c.* FROM claims c JOIN seals s ON s.id=c.seal_id "
                "WHERE s.item_id=? AND c.status='pending'", (iid,)).fetchall()
            for c in pending:
                parts.append(f"""<form method="post" action="/act/review-claim" style="display:inline">{hidden}
                  <input type="hidden" name="claim_id" value="{c['id']}">
                  <input type="hidden" name="accept" value="1">
                  <button class="btn">复核通过认领 #{c['id']}</button></form>
                  <form method="post" action="/act/review-claim" style="display:inline">{hidden}
                  <input type="hidden" name="claim_id" value="{c['id']}">
                  <input type="hidden" name="accept" value="0">
                  <input name="note" placeholder="误认/驳回原因" size="22">
                  <button class="btn btn-ghost">判为误认/驳回</button></form>""")
        return "<hr style='border:none;border-top:1px solid #eee'>".join(parts) or \
            '<p class="muted">当前状态无可用操作。</p>'

    def resident_page(self, q: dict, flash: str = "") -> str:
        conn, svc, _ = self.fresh()
        announces = ""
        for a in svc.announcements():
            announces += f'<div class="msg-box">📢 {esc(a["body"])}</div>'
        cards = ""
        for it in svc.claim_open_items():
            seal = svc.get_seal(it["id"])
            pub = svc.photos(it["id"], public_only=True)
            imgs = "".join(f'<img src="/img?name={esc(p["path"])}" alt="公开照片">' for p in pub)
            cards += f"""<div class="card"><h2>{it["floor"]}F {esc(it["position"])}
              <span class="badge lv-{it['fire_level']}">{LEVEL_LABEL[it['fire_level']]}</span></h2>
            <p>{esc(it["description"])}　<span class="muted">封存于 {esc(seal['storage_loc'])}，
            认领截止 {esc(seal['claim_deadline'])}</span></p>
            <div class="photos">{imgs or '<span class="muted">无公开照片</span>'}</div>
            <form method="post" action="/act/claim" class="card" style="margin:8px 0 0">
              <input type="hidden" name="id" value="{it['id']}">
              <label class="field">称呼<input name="claimant" placeholder="如：本楼住户" size="14"></label>
              <label class="field">联系方式<input name="contact" placeholder="手机号（不公开展示）" size="16"></label>
              <label class="field">物品的非公开特征
                <input name="feature" placeholder="如内件/标记/瑕疵等，外人无从得知" size="26"></label>
              <button class="btn">提交认领（凭非公开特征核对）</button>
            </form></div>"""
        conn.close()
        return self._wrap("住户端 · 楼道公告与认领", f"""
<header><h1>住户端 · 公告与物品认领</h1>
<a href="/staff">员工端</a><span class="muted" style="margin-left:auto;color:#cfe6da">
本页不显示任何住户姓名、房号、电话；只展示可公开照片</span></header>
<div class="wrap">{flash}
<div class="card"><h2>楼道清理公告</h2>{announces or '<p class="muted">暂无公告</p>'}</div>
<h2 class="section-sep">封存待认领物品（认领须给出非公开特征）</h2>
{cards or '<div class="card"><p class="muted">暂无可认领物品</p></div>'}
</div>""")

    # ------------------------------------------------------------------ actions
    def do_action(self, path: str, form: dict, cookie: str) -> tuple[str, str]:
        """返回 (跳转地址, flash)。"""
        conn, svc, store = self.fresh()
        actor = form.get("actor") or self._who(cookie, conn)
        iid = int(form.get("id", 0))
        try:
            if path == "/act/register":
                level = form.get("level", "low")
                owner_raw = form.get("owner_id", "")
                owner_id = int(owner_raw) if owner_raw else None
                clear = form.get("photo_clear", "1") == "1"
                data = images.make_photo(
                    f"C-{self.clock.now().year}-XXXX", form.get("position", ""), level,
                    clear=clear)
                nid = svc.register_item(
                    int(form.get("floor", 3)), form.get("position", ""),
                    form.get("description", ""), level, actor, owner_id=owner_id,
                    flammable=form.get("flammable") == "1", photo=data, photo_clear=clear)
                conn.close()
                return f"/event?id={nid}", "登记成功"
            if path == "/act/notice":
                svc.issue_notice(iid, actor)
                return f"/event?id={iid}", "通知已发送（有主私信 / 无主公开公告）"
            if path == "/act/recheck":
                present = form.get("present") == "1"
                r = svc.recheck(iid, actor, present)
                return f"/event?id={iid}", "复查：物品仍在楼道" if r == "present" else \
                    "复查确认住户已自行清走，未封存"
            if path == "/act/self-clear":
                svc.confirm_self_clear(iid, actor)
                return f"/event?id={iid}", "住户自清确认成功，未重复封存"
            if path == "/act/emergency":
                svc.emergency_remove(iid, actor, form.get("note", ""))
                return f"/event?id={iid}", "易燃物已紧急移除并报备主管"
            if path == "/act/reshoot":
                item = svc.get_item(iid)
                data = images.make_photo(item["code"], item["position"], item["fire_level"])
                svc.reshoot(iid, actor, data)
                return f"/event?id={iid}", "重拍清晰照片已上传，解除封存限制"
            if path == "/act/seal":
                inventory = [x.strip() for x in form.get("inventory", "").splitlines() if x.strip()]
                svc.seal_item(iid, form.get("w1"), form.get("w2"), inventory,
                              form.get("storage_loc", "物业暂存间"))
                data = images.make_photo(svc.get_item(iid)["code"], "sealed",
                                         svc.get_item(iid)["fire_level"], kind="sealed")
                svc.add_photo(iid, "sealed", "封存现场照（双签封条）", data)
                return f"/event?id={iid}", "双人清点封存完成，已发认领公告"
            if path == "/act/claim":
                svc.submit_claim(iid, form.get("claimant", ""), form.get("contact", ""),
                                 form.get("feature", ""))
                return "/resident", "认领申请已提交，将由员工核对非公开特征"
            if path == "/act/review-claim":
                r = svc.review_claim(int(form.get("claim_id", 0)), actor,
                                     form.get("accept") == "1", form.get("note", ""))
                return f"/event?id={iid}", "特征相符，已发还" if r == "matched" else \
                    "特征不符，判为误认，物品继续封存"
            if path == "/act/damage":
                item = svc.get_item(iid)
                data = images.make_photo(item["code"], "damage", item["fire_level"],
                                         kind="damage")
                svc.report_damage(iid, actor, form.get("note", ""), data)
                return f"/event?id={iid}", "封存后损坏已登记并报备主管"
            if path == "/act/dispose":
                svc.dispose(iid, actor, form.get("decision", "统一清运销毁"))
                return f"/event?id={iid}", "主管已批准到期处置"
        finally:
            conn.close()
        return "/staff", ""

    def _who(self, cookie: str, conn) -> str:
        ck = SimpleCookie()
        try:
            ck.load(cookie)
            name = ck.get("staff_as")
            if name:
                row = conn.execute("SELECT name FROM staff WHERE name=?",
                                   (name.value,)).fetchone()
                if row:
                    return row["name"]
        except Exception:
            pass
        row = conn.execute("SELECT name FROM staff ORDER BY id LIMIT 1").fetchone()
        return row["name"] if row else ""

    def _wrap(self, title: str, body: str) -> str:
        return (f"<!doctype html><html lang=zh><meta charset=utf-8>"
                f"<meta name=viewport content='width=device-width,initial-scale=1'>"
                f"<title>{esc(title)}</title><style>{CSS}</style>{body}")


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, text: str, status: int = 200, cookies: dict | None = None):
            data = text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            for k, v in (cookies or {}).items():
                self.send_header("Set-Cookie", f"{k}={v}; Path=/")
            self.end_headers()
            self.wfile.write(data)

        def _flash(self, msg: str, ok: bool = True) -> str:
            return (f'<div class="flash {"flash-err" if not ok else ""}">{esc(msg)} '
                    f'<a href="javascript:history.back()">返回</a></div>')

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            path, raw_q = parsed.path, parsed.query
            q = urllib.parse.parse_qs(raw_q)
            cookies: dict[str, str] = {}
            if "as" in q:
                cookies["staff_as"] = urllib.parse.quote(q["as"][0])
            try:
                flash_msg = urllib.parse.unquote(q["m"][0]) if "m" in q else ""
                if path in ("/", "/staff"):
                    body = app.staff_dashboard(q, self.headers.get("Cookie", ""))
                    if flash_msg:
                        body = body.replace('<div class="wrap">',
                                            f'<div class="wrap">{self._flash(flash_msg)}', 1)
                    self._send(body, cookies=cookies)
                elif path == "/event":
                    body = app.event_page(q, self.headers.get("Cookie", ""))
                    if flash_msg:
                        body = body.replace('<div class="wrap">',
                                            f'<div class="wrap">{self._flash(flash_msg)}', 1)
                    self._send(body, cookies=cookies)
                elif path == "/resident":
                    body = app.resident_page(q)
                    if flash_msg:
                        body = body.replace('<div class="wrap">',
                                            f'<div class="wrap">{self._flash(flash_msg)}', 1)
                    self._send(body)
                elif path == "/img":
                    name = Path(q.get("name", [""])[0]).name
                    f = app.img_dir / name
                    if not f.exists():
                        self.send_error(404)
                        return
                    data = f.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                else:
                    self.send_error(404)
            except ServiceError as e:
                self._send(app._wrap("错误", f'<div class="wrap">{self._flash(str(e), False)}</div>'),
                           400)

        def do_POST(self):
            parsed = urllib.parse.urlparse(self.path)
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            form = {k: v[0] for k, v in urllib.parse.parse_qs(body).items()}
            try:
                target, flash = app.do_action(parsed.path, form,
                                              self.headers.get("Cookie", ""))
                self.send_response(303)
                sep = "&" if "?" in target else "?"
                self.send_header("Location", target + sep + "m=" +
                                 urllib.parse.quote(flash))
                self.end_headers()
            except ServiceError as e:
                kind = {NeedReshootError: "照片问题", PermissionError_: "权限问题",
                        ConflictError: "并发/状态冲突"}.get(type(e), "业务错误")
                back = form.get("id") and f'/event?id={form["id"]}' or "/staff"
                page = app._wrap("操作被拒绝",
                                 f'<div class="wrap"><div class="flash flash-err">'
                                 f'<b>{kind}：</b>{esc(str(e))}<br>'
                                 f'<a href="{back}">返回事件页</a></div></div>')
                self._send(page, 409)

    return Handler


def serve(host: str = "127.0.0.1", port: int = 8000, db_path: Path = DEFAULT_DB,
          img_dir: Path = DEFAULT_IMG, clock: Clock | None = None,
          open_browser: bool = True) -> None:
    if not db_path.exists():
        seed(db_path, img_dir)
    init_db(connect(db_path))  # 幂等迁移
    app = App(db_path, img_dir, clock)
    httpd = ThreadingHTTPServer((host, port), make_handler(app))
    url = f"http://{host}:{port}/staff"
    print(f"楼道巡查系统已启动: {url}")
    print("  员工端 /staff  事件页 /event?id=N  住户端 /resident")
    if open_browser:
        import threading
        import webbrowser
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
