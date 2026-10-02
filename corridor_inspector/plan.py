"""楼层平面示意: 每层一张 SVG, 标出楼道占用点位 (紧急项红色置前闪烁)。"""
from __future__ import annotations

LEVEL_COLOR = {"high": "#c22020", "medium": "#c88214", "low": "#1f5a42"}

# 平面: 西楼梯间(左) -- 走廊 -- 电梯厅(中上) -- 东楼梯间(右)
def _xy(position: str) -> tuple[int, int]:
    if "西" in position:
        x = 78
    elif "东" in position:
        x = 522
    else:
        x = 300
    if "电梯" in position:
        y = 58
    elif "楼梯" in position:
        y = 128
    else:
        y = 92
    return x, y


def floor_plan_svg(floor: int, items: list, base: str = "/event") -> str:
    """items: 该层物品 Row 列表。紧急高风险点位添加脉冲圈。"""
    dots = []
    for it in items:
        x, y = _xy(it["position"])
        color = LEVEL_COLOR[it["fire_level"]]
        urgent = it["fire_level"] == "high" and it["status"] in (
            "registered", "noticed", "sealed")
        title = f"{it['code']} {it['position']} {it['description']}"
        if urgent:
            dots.append(
                f'<circle cx="{x}" cy="{y}" r="13" fill="none" stroke="{color}" '
                f'stroke-width="2" opacity="0.7"><animate attributeName="r" values="8;16;8" '
                f'dur="1.6s" repeatCount="indefinite"/>'
                f'<animate attributeName="opacity" values="0.8;0.1;0.8" '
                f'dur="1.6s" repeatCount="indefinite"/></circle>')
        dots.append(
            f'<a href="{base}?id={it["id"]}">'
            f'<circle cx="{x}" cy="{y}" r="8" fill="{color}" stroke="#fff" stroke-width="2">'
            f'<title>{title}</title></circle>'
            f'<text x="{x}" y="{y - 12}" text-anchor="middle" class="dot-label">'
            f'{it["code"].split("-")[-1]}</text></a>')
    dots_svg = "\n".join(dots)
    return f"""
<svg viewBox="0 0 600 175" class="plan" role="img" aria-label="{floor}层平面图">
  <rect x="8" y="8" width="584" height="159" rx="8" fill="#fafbfa" stroke="#c9cfca"/>
  <!-- 西侧楼梯间 -->
  <rect x="24" y="104" width="96" height="48" rx="4" fill="none" stroke="#9aa39d" stroke-dasharray="5 4"/>
  <text x="72" y="132" text-anchor="middle" class="room-label">西楼梯间</text>
  <!-- 电梯厅 -->
  <rect x="252" y="28" width="96" height="46" rx="4" fill="none" stroke="#9aa39d" stroke-dasharray="5 4"/>
  <text x="300" y="55" text-anchor="middle" class="room-label">电梯厅</text>
  <!-- 走廊 -->
  <rect x="24" y="80" width="552" height="46" rx="4" fill="#eef1ef" stroke="#c9cfca"/>
  <text x="300" y="107" text-anchor="middle" class="room-label">公 共 楼 道</text>
  <!-- 东侧楼梯间 -->
  <rect x="480" y="104" width="96" height="48" rx="4" fill="none" stroke="#9aa39d" stroke-dasharray="5 4"/>
  <text x="528" y="132" text-anchor="middle" class="room-label">东楼梯间</text>
  <text x="20" y="30" class="floor-tag">{floor}F</text>
  {dots_svg}
</svg>"""
