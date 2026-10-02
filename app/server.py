"""HTTP 界面：楼栋巡查 / 楼层平面 / 事件页 / 住户公告端 / 模拟消息箱。

仅用标准库 http.server，SQLite 持久化，图片读本地 data/photos。
"""
from __future__ import annotations

import html
import os
import sqlite3
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlencode

from .db import connect
from . import service as svc

DB_PATH = os.environ.get("HALLWAY_DB", "data/hallway.db")
PHOTO_ROOT = os.environ.get("HALLWAY_PHOTOS", "data/photos")

RISK_LABEL = {"low": "低", "medium": "中", "high": "高"}
STATUS_LABEL = {
    "registered": "已登记", "noticed": "已发通知", "rechecked": "复查仍占用",
    "urgent_removed": "紧急移除", "removed_by_owner": "住户自清",
    "sealed": "已封存", "claimed": "已认领", "disposed": "已处置",
}
METHOD_LABEL = {"discard": "统一废弃", "donate": "捐赠", "auction": "变价处理"}

CSS = """
:root{--gray:#f2f3f2;--panel:#fff;--green:#1f5c3d;--green2:#2e7d52;
--line:#d9dcd7;--red:#c62828;--ink:#243028;--muted:#6a746d;}
*{box-sizing:border-box}body{margin:0;background:var(--gray);color:var(--ink);
font:14px/1.6 "PingFang SC","Microsoft YaHei",sans-serif}
a{color:var(--green2);text-decoration:none}a:hover{text-decoration:underline}
header{background:var(--green);color:#fff;padding:12px 22px;display:flex;
align-items:center;gap:18px;flex-wrap:wrap}
header h1{font-size:17px;margin:0;font-weight:600}
header nav a{color:#d9eadf;margin-right:14px}header nav a:hover{color:#fff}
.wrap{max-width:1060px;margin:22px auto;padding:0 18px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;
padding:16px 18px;margin-bottom:16px}
h2{font-size:15px;color:var(--green);margin:0 0 12px}
.badge{display:inline-block;padding:1px 9px;border-radius:10px;font-size:12px;
border:1px solid var(--line);background:#f7f8f7;color:var(--muted)}
.badge.risk-high{color:var(--red);border-color:var(--red);background:#fdecec}
.badge.risk-medium{color:#b26a00;border-color:#e0b35f;background:#fdf6e7}
.badge.urgent{color:#fff;background:var(--red);border-color:var(--red)}
.badge.st-sealed,.badge.st-claimed{color:var(--green);border-color:var(--green2)}
.badge.st-disposed,.badge.st-removed_by_owner{color:var(--muted)}
.badge.st-urgent_removed,.badge.st-rechecked{color:var(--red);border-color:var(--red)}
table{width:100%;border-collapse:collapse}td,th{padding:8px 10px;text-align:left;
border-bottom:1px solid var(--line);font-weight:400}th{color:var(--muted);font-size:12px}
tr.urgent-row td{background:#fdf3f3}
.btn{display:inline-block;border:1px solid var(--green2);color:var(--green);
background:#fff;border-radius:5px;padding:3px 12px;cursor:pointer;font-size:13px}
.btn:hover{background:#eef5f0;text-decoration:none}
.btn.red{border-color:var(--red);color:var(--red)}
.btn.red:hover{background:#fdecec}
.btn[disabled]{opacity:.45;cursor:not-allowed}
.timeline{list-style:none;margin:0;padding:0;border-left:2px solid var(--line)}
.timeline li{position:relative;padding:6px 0 12px 20px}
.timeline li::before{content:"";position:absolute;left:-6px;top:12px;width:10px;
height:10px;border-radius:50%;background:var(--green2)}
.timeline li.ev-seal::before{background:var(--green)}
.timeline li.ev-wrong_claim::before,.timeline li.ev-damage::before,
.timeline li.ev-urgent_remove::before{background:var(--red)}
.timeline .t{color:var(--muted);font-size:12px;margin-right:8px}
.muted{color:var(--muted)}.red{color:var(--red)}.green{color:var(--green)}
.msg{border-bottom:1px dashed var(--line);padding:8px 0}
.msg .meta{color:var(--muted);font-size:12px}
form.inline{display:inline}
select,input[type=text]{padding:3px 8px;border:1px solid var(--line);border-radius:4px}
.photos{display:flex;gap:10px;flex-wrap:wrap}.photos img{width:180px;border-radius:6px;
border:1px solid var(--line)}
.plan{width:100%;height:380px;background:#eef0ee;border-radius:8px}
.legend{font-size:12px;color:var(--muted);margin-top:6px}
.legend i{display:inline-block;width:10px;height:10px;border-radius:50%;margin:0 4px 0 12px}
.notice{background:#eef5f0;border-left:3px solid var(--green2);padding:8px 12px;
border-radius:4px;margin:8px 0}.notice.red{background:#fdecec;border-left-color:var(--red)}
"""


def esc(v) -> str:
    return html.escape("" if v is None else str(v))


def db():
    return connect(DB_PATH)


def staff_bar(conn, cur) -> str:
    opts = []
    for s in conn.execute("SELECT * FROM staff ORDER BY id"):
        sel = " selected" if cur and s["id"] == cur["id"] else ""
        opts.append(f'<option value="{s["id"]}"{sel}>{esc(s["name"])}'
                    f'（{"主管" if s["role"]=="supervisor" else "巡查员"}）</option>')
    return ('<form method="post" action="/switch-staff" class="inline">'
            f'当前操作人 <select name="staff_id">{"".join(opts)}</select>'
            '<button class="btn">切换</button></form>')


def current_staff(conn, cookie) -> sqlite3.Row | None:
    sid = None
    if cookie:
        c = SimpleCookie(); c.load(cookie)
        if "staff" in c:
            sid = c["staff"].value
    if sid:
        return conn.execute("SELECT * FROM staff WHERE id=?", (sid,)).fetchone()
    return conn.execute("SELECT * FROM staff ORDER BY id LIMIT 1").fetchone()


# ---------------------------------------------------------------- 页面

def page(title, body, staff=None, conn=None, public=False) -> bytes:
    nav = ('<nav><a href="/">楼栋巡查</a><a href="/public">住户公告端</a>'
           '<a href="/messages">消息箱</a></nav>')
    bar = staff_bar(conn, staff) if (conn is not None and not public) else ""
    h = (f'<header><h1>🧯 楼道占用巡查处置</h1>{nav}<span style="flex:1"></span>'
         f'{bar}</header>' if not public else
         f'<header><h1>🏠 住户公告 · 楼道清理</h1>'
         '<nav><a href="/public">公告列表</a></nav></header>')
    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title><style>{CSS}</style></head>
<body>{h}<div class="wrap">{body}</div></body></html>""".encode()


def item_badges(item) -> str:
    urgent = item["flammable"] or item["risk"] == "high"
    b = [f'<span class="badge st-{item["status"]}">{STATUS_LABEL[item["status"]]}</span>']
    b.append(f'<span class="badge risk-{item["risk"]}">风险{RISK_LABEL[item["risk"]]}</span>')
    if item["flammable"]:
        b.append('<span class="badge urgent">易燃·紧急</span>')
    return " ".join(b)


def dashboard(conn, staff) -> bytes:
    rows = conn.execute("""
        SELECT * FROM items ORDER BY
          (flammable=1 OR risk='high') DESC,
          CASE status WHEN 'urgent_removed' THEN 0 WHEN 'rechecked' THEN 1
                      WHEN 'noticed' THEN 2 ELSE 3 END,
          floor, id""").fetchall()
    counts = {s: 0 for s in STATUS_LABEL}
    for r in rows:
        counts[r["status"]] += 1
    stats = " ".join(f'<span class="badge">{STATUS_LABEL[s]} {n}</span>'
                     for s, n in counts.items() if n)
    trs = []
    for it in rows:
        urgent = it["flammable"] or it["risk"] == "high"
        loc = f'{it["floor"]}层 · {esc(it["location"])}'
        trs.append(
            f'<tr class="{"urgent-row" if urgent else ""}">'
            f'<td><b>{"🔴 " if urgent else ""}<a href="/item?id={it["id"]}">'
            f'{esc(it["code"])}</a></b></td>'
            f'<td><a href="/floor?n={it["floor"]}">{loc}</a></td>'
            f'<td>{esc(it["description"])}</td>'
            f'<td>{item_badges(it)}</td></tr>')
    body = f"""
    <div class="card"><h2>楼栋巡查总览</h2>
      <p>{stats}</p>
      <p class="muted">紧急（易燃或高风险）项固定排在列表最前方。</p></div>
    <div class="card"><table>
      <tr><th>编号</th><th>位置</th><th>物品</th><th>状态 / 风险</th></tr>
      {''.join(trs)}</table></div>"""
    return page("楼栋巡查", body, staff, conn)


def floor_plan(conn, staff, n: int) -> bytes:
    items = conn.execute("SELECT * FROM items WHERE floor=? ORDER BY "
                         "(flammable=1 OR risk='high') DESC, id", (n,)).fetchall()
    # 走廊平面：横向走廊 + 两侧房门 + 占用点位
    doors = []
    for i in range(8):
        x = 60 + i * 110
        doors.append(f'<rect x="{x}" y="60" width="46" height="14" fill="#c2c9c2"/>'
                     f'<rect x="{x}" y="306" width="46" height="14" fill="#c2c9c2"/>')
    marks = []
    for it in items:
        if it["x"] is None:
            continue
        cx = 60 + float(it["x"]) * 880
        cy = 120 + float(it["y"]) * 150
        urgent = it["flammable"] or it["risk"] == "high"
        color = "#c62828" if urgent else "#1f5c3d"
        marks.append(
            f'<a href="/item?id={it["id"]}">'
            f'<circle cx="{cx}" cy="{cy}" r="11" fill="{color}" opacity=".95"/>'
            f'<circle cx="{cx}" cy="{cy}" r="11" fill="none" stroke="{color}" '
            f'stroke-width="2" opacity=".6"><animate attributeName="r" values="11;20;11" '
            f'dur="2s" repeatCount="indefinite"/><animate attributeName="opacity" '
            f'values=".6;0;.6" dur="2s" repeatCount="indefinite"/></circle>'
            f'<text x="{cx}" y="{cy+4}" text-anchor="middle" fill="#fff" font-size="11">'
            f'{it["id"]}</text></a>')
    svg = f"""<svg class="plan" viewBox="0 0 960 380" xmlns="http://www.w3.org/2000/svg">
      <rect x="40" y="90" width="880" height="200" fill="#e3e7e2" stroke="#b9c0b8"/>
      <text x="480" y="195" text-anchor="middle" fill="#9aa39a">公 共 楼 道</text>
      <text x="480" y="120" text-anchor="middle" fill="#9aa39a" font-size="12">疏散通道</text>
      {''.join(doors)}{''.join(marks)}</svg>"""
    lis = "".join(
        f'<li><a href="/item?id={it["id"]}">{esc(it["code"])}</a> '
        f'{esc(it["location"])} — {esc(it["description"])} {item_badges(it)}</li>'
        for it in items) or '<li class="muted">本层暂无登记物品</li>'
    body = f"""
    <div class="card"><h2>{n} 层平面示意</h2>{svg}
      <div class="legend">占用点位：<i style="background:#1f5c3d"></i>常规
        <i style="background:#c62828"></i>易燃 / 高风险（点击圆点查看事件）</div></div>
    <div class="card"><h2>本层占用登记</h2><ul>{lis}</ul></div>
    <p><a href="/">← 返回总览</a></p>"""
    return page(f"{n}层平面", body, staff, conn)


def photo_html(conn, item_id, *, public_only) -> str:
    q = "SELECT * FROM photos WHERE item_id=? " + ("AND public=1" if public_only else "")
    rows = conn.execute(q + " ORDER BY id", (item_id,)).fetchall()
    out = []
    for p in rows:
        tag = "" if p["quality"] == "ok" else ' <span class="badge">不清·待重拍</span>'
        pub = "" if p["public"] else ' <span class="badge">内部照片</span>'
        out.append(f'<div><img src="/img/{esc(p["path"].split("/")[-1])}">'
                   f'<div class="muted" style="font-size:12px">{esc(p["note"] or "")}{tag}{pub}</div></div>')
    return '<div class="photos">' + "".join(out) + "</div>" if out else \
        '<p class="muted">暂无可展示照片</p>'


def item_page(conn, staff, item_id: int) -> bytes:
    it = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    if not it:
        return page("未找到", '<p>物品不存在</p>', staff, conn)
    # 时间线按 通知→复查→封存 的顺序排列（同刻按 EVENT_ORDER）
    order = svc.EVENT_ORDER
    cases = " ".join(f"WHEN '{k}' THEN {v}" for k, v in order.items())
    events = conn.execute(f"""
        SELECT e.*, s.name actor FROM events e LEFT JOIN staff s ON s.id=e.actor_id
        WHERE e.item_id=? ORDER BY e.created_at, CASE e.type {cases} END, e.id""",
        (item_id,)).fetchall()
    tl = "".join(
        f'<li class="ev-{e["type"]}"><span class="t">{esc(e["created_at"])}</span>'
        f'{esc(e["note"])}'
        f'{(" · " + esc(e["actor"])) if e["actor"] else ""}</li>'
        for e in events)
    seal = conn.execute("""SELECT se.*, a.name w1, b.name w2 FROM seals se
        JOIN staff a ON a.id=se.witness1_id JOIN staff b ON b.id=se.witness2_id
        WHERE se.item_id=?""", (item_id,)).fetchone()
    seal_html = ""
    if seal:
        claims = conn.execute("SELECT * FROM claims WHERE item_id=? ORDER BY id",
                              (item_id,)).fetchall()
        cl = "".join(
            f'<tr><td>{esc(c["created_at"])}</td><td>{esc(c["claimer"])}</td>'
            f'<td>{"<span class=green>通过</span>" if c["accepted"] else "<span class=red>误认驳回</span>"}'
            f'</td></tr>' for c in claims)
        damages = conn.execute("""SELECT d.*, s.name r FROM damages d
            JOIN staff s ON s.id=d.reporter_id WHERE d.seal_id=?""",
            (seal["id"],)).fetchall()
        dm = "".join(f'<li class="red">{esc(d["created_at"])} {esc(d["description"])}'
                     f'（{esc(d["r"])}）</li>' for d in damages)
        seal_html = f"""<div class="card"><h2>封存信息（双签）</h2>
          <table><tr><th>封存箱</th><td>{esc(seal["box_code"])}</td></tr>
          <tr><th>清点员工</th><td class="green">{esc(seal["w1"])} ＋ {esc(seal["w2"])}</td></tr>
          <tr><th>封存时间</th><td>{esc(seal["sealed_at"])}</td></tr>
          <tr><th>认领期限</th><td>{esc(seal["claim_deadline"])}</td></tr></table>
          {f"<ul class=timeline style=margin-top:10px>{dm}</ul>" if dm else ""}
          <h2 style="margin-top:14px">认领核验记录</h2>
          <table><tr><th>时间</th><th>认领人</th><th>结果</th></tr>{cl}</table></div>"""

    notice = conn.execute("SELECT * FROM notices WHERE item_id=? ORDER BY id DESC LIMIT 1",
                          (item_id,)).fetchone()
    # ---- 操作区
    sid = staff["id"]
    actions = []
    st = it["status"]
    if st in svc.PRE_NOTICE:
        actions.append(("发出限期清理通知", "notice", ""))
    if it["flammable"] and st in {"registered", "noticed"}:
        actions.append("__RED__易燃物紧急移除", "__RED__urgent_remove", "")
    if st in {"noticed", "rechecked", "urgent_removed"}:
        actions.append(("复查：仍在楼道", "recheck_yes", ""))
        actions.append(("复查 / 登记：住户已自清", "self_clear", ""))
    if st in svc.SEALABLE:
        staffs = conn.execute("SELECT id,name FROM staff ORDER BY id").fetchall()
        w2 = "".join(f'<option value="{s["id"]}">{esc(s["name"])}</option>' for s in staffs)
        actions.append(("__RAW__双人封存：第二清点人 "
                        f'<select name="w2">{w2}</select>', "seal", ""))
    if st == "sealed":
        actions.append(("__RAW__登记封存后损坏 <input type=text name=damage placeholder=损坏情况>",
                        "damage", ""))
        actions.append(("__RAW__到期处置 <select name=method>"
                        "<option value=discard>统一废弃</option><option value=donate>捐赠</option>"
                        "<option value=auction>变价处理</option></select>（须主管）",
                        "dispose", ""))
    btns = []
    for label, act, _ in actions:
        if label.startswith("__RAW__"):
            label = label[7:]
            btns.append(f'<form class="inline" method="post" action="/action">'
                        f'<input type=hidden name=item_id value={it["id"]}>'
                        f'<input type=hidden name=action value={act}>'
                        f'{label} <button class="btn{" red" if act=="urgent_remove" else ""}">执行</button></form>')
        else:
            red = " red" if label.startswith("__RED__") else ""
            label = label.replace("__RED__", "")
            btns.append(f'<form class="inline" method="post" action="/action">'
                        f'<input type=hidden name=item_id value={it["id"]}>'
                        f'<input type=hidden name=action value={act}>'
                        f'<button class="btn{red}">{esc(label)}</button></form>')
    actions_html = ("<div class='card'><h2>处置操作</h2><p>" +
                    " &nbsp; ".join(btns) + "</p>"
                    '<p class="muted">住户自清与员工封存同时提交时，以数据库写锁为准，'
                    "先到先得，不会重复封存。</p></div>") if btns else ""

    body = f"""
    <p><a href="/">← 总览</a> · <a href="/floor?n={it["floor"]}">{it["floor"]}层平面</a></p>
    <div class="card"><h2>{esc(it["code"])} {item_badges(it)}</h2>
      <table>
        <tr><th>位置</th><td>{it["floor"]}层 {esc(it["location"])}</td></tr>
        <tr><th>物品（公开描述）</th><td>{esc(it["description"])}</td></tr>
        <tr><th>登记时间</th><td>{esc(it["created_at"])}</td></tr>
        <tr><th>通知限期</th><td>{esc(notice["deadline"]) if notice else "—"}</td></tr>
      </table>
      <h2 style="margin-top:12px">现场照片</h2>{photo_html(conn, item_id, public_only=False)}
    </div>
    {actions_html}
    <div class="card"><h2>事件经过</h2>
      <p class="muted">按 通知 → 复查 → 封存 顺序排列</p>
      <ul class="timeline">{tl}</ul></div>
    {seal_html}"""
    return page(f"事件 {it['code']}", body, staff, conn)


def public_page(conn) -> bytes:
    rows = conn.execute("""SELECT * FROM items WHERE resident_id IS NULL
        AND status IN ('noticed','sealed') ORDER BY
        (flammable=1 OR risk='high') DESC, floor, id""").fetchall()
    cards = []
    for it in rows:
        seal = conn.execute("SELECT * FROM seals WHERE item_id=?", (it["id"],)).fetchone()
        deadline = seal["claim_deadline"] if seal else \
            conn.execute("SELECT deadline FROM notices WHERE item_id=? ORDER BY id DESC LIMIT 1",
                         (it["id"],)).fetchone()["deadline"]
        cards.append(f"""<div class="card">
          <h2>{esc(it["code"])}　{item_badges(it)}</h2>
          <p>{it["floor"]}层 {esc(it["location"])} · {esc(it["description"])}
             {'<span class=red>（易燃物品，已紧急移除）</span>' if it["flammable"] else ""}</p>
          <p class="muted">限期 / 认领截止：{esc(deadline)}</p>
          {photo_html(conn, it["id"], public_only=True)}
          <p class="notice">认领不询问房号与姓名，请描述该物品的<b>非公开特征</b>
            （只有真正主人才知道的细节），由物业核对登记记录：</p>
          <form method="post" action="/claim">
            <input type=hidden name=item_id value={it["id"]}>
            <input type=text name=claimer placeholder="称呼（可化名）" required>
            <input type=text name=contact placeholder="联系方式（选填）">
            <input type=text name=feature size=42 placeholder="非公开特征，如内装物品/标记" required>
            <button class="btn">提交认领</button></form></div>""")
    body = ("<div class='notice red'>公告不包含任何住户房号、姓名或电话；"
            "本页仅展示经审核可公开的照片。</div>" + "".join(cards)
            + ('<p class="muted">当前没有进行中的公告。</p>' if not cards else ""))
    return page("住户公告", body, public=True)


def messages_page(conn) -> bytes:
    rows = conn.execute("SELECT * FROM outbox ORDER BY id DESC LIMIT 60").fetchall()
    ms = "".join(
        f'<div class="msg"><div class="meta">{esc(m["created_at"])} → {esc(m["target"])}'
        f' [{esc(m["kind"])}]</div><div>{esc(m["content"])}</div></div>' for m in rows)
    return page("模拟消息箱", f'<div class="card"><h2>模拟消息适配器发件箱</h2>{ms}</div>',
                None, conn)


# ---------------------------------------------------------------- 分派

class App:
    def __init__(self, conn_factory=db):
        self.conn_factory = conn_factory

    def __call__(self, environ, start_response):
        method = environ["REQUEST_METHOD"]
        path = environ["PATH_INFO"]
        try:
            with self.conn_factory() as conn:
                cookie = environ.get("HTTP_COOKIE", "")
                if method == "GET":
                    return self._get(conn, path, environ, cookie, start_response)
                return self._post(conn, path, environ, cookie, start_response)
        except svc.WorkflowError as e:
            body = f'<p class="red">操作被拒绝：{esc(str(e))}</p><p><a href="/">返回</a></p>'
            data = page("操作未执行", f'<div class="card">{body}</div>')
            start_response("409 Conflict", [("Content-Type", "text/html;charset=utf-8")])
            return [data]

    def _staff(self, conn, cookie):
        return current_staff(conn, cookie)

    def _redirect(self, start_response, location):
        start_response("303 See Other", [("Location", location)])
        return [b""]

    def _get(self, conn, path, environ, cookie, sr):
        from urllib.parse import urlparse
        q = parse_qs(environ.get("QUERY_STRING", ""))
        if path == "/":
            data = dashboard(conn, self._staff(conn, cookie))
        elif path == "/floor":
            data = floor_plan(conn, self._staff(conn, cookie), int(q.get("n", ["1"])[0]))
        elif path == "/item":
            data = item_page(conn, self._staff(conn, cookie), int(q["id"][0]))
        elif path == "/public":
            data = public_page(conn)
        elif path == "/messages":
            data = messages_page(conn)
        elif path.startswith("/img/"):
            return self._img(path, sr)
        else:
            data = page("404", "<p>页面不存在</p>")
        sr("200 OK", [("Content-Type", "text/html;charset=utf-8")])
        return [data]

    def _img(self, path, sr):
        name = os.path.basename(path)
        fp = os.path.join(PHOTO_ROOT, name)
        if not os.path.isfile(fp):
            sr("404 Not Found", []); return [b"not found"]
        ctype = "image/svg+xml" if name.endswith(".svg") else "application/octet-stream"
        sr("200 OK", [("Content-Type", ctype)]);
        with open(fp, "rb") as f:
            return [f.read()]

    def _post(self, conn, path, environ, cookie, sr):
        length = int(environ.get("CONTENT_LENGTH") or 0)
        body = environ["wsgi.input"].read(length).decode() if length else ""
        form = parse_qs(body, keep_blank_values=True)
        g = lambda k: form.get(k, [""])[0]
        staff = self._staff(conn, cookie)

        if path == "/switch-staff":
            sid = int(g("staff_id"))
            sr("303 See Other", [("Location", "/"),
                                 ("Set-Cookie", f"staff={sid}; Path=/")])
            return [b""]

        if path == "/claim":
            iid = int(g("item_id"))
            svc.claim(conn, iid, claimer=g("claimer"), contact=g("contact"),
                      feature=g("feature"), handler_id=staff["id"])
            return self._redirect(sr, f"/item?id={iid}")

        if path == "/action":
            iid = int(g("item_id"))
            act = g("action")
            if act == "notice":
                svc.send_notice(conn, iid, actor_id=staff["id"])
            elif act == "urgent_remove":
                svc.urgent_remove(conn, iid, actor_id=staff["id"])
            elif act == "recheck_yes":
                svc.recheck(conn, iid, still_present=True, actor_id=staff["id"])
            elif act == "self_clear":
                svc.confirm_self_clear(conn, iid, actor_id=staff["id"])
            elif act == "seal":
                svc.seal_item(conn, iid, witness1_id=staff["id"],
                              witness2_id=int(g("w2")))
            elif act == "damage":
                svc.record_damage(conn, iid, description=g("damage"),
                                  reporter_id=staff["id"])
            elif act == "dispose":
                svc.dispose(conn, iid, method=g("method"), approver_id=staff["id"])
            return self._redirect(sr, f"/item?id={iid}")

        sr("404 Not Found", []); return [b"no such action"]


def make_server(host="127.0.0.1", port=8000):
    from wsgiref.simple_server import make_server
    return make_server(host, port, App())
