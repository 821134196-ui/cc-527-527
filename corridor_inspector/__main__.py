"""一条本地命令启动:

    python -m corridor_inspector                 # 首次自动播种演示数据
    python -m corridor_inspector --reseed        # 重置演示数据
    python -m corridor_inspector --port 8080
"""
from __future__ import annotations

import argparse
from pathlib import Path

from .db import connect, init_db
from .seed import DEFAULT_DB, DEFAULT_IMG, seed
from .web import serve


def main() -> None:
    ap = argparse.ArgumentParser(description="公共楼道占用巡查与物品处置系统")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--images", default=str(DEFAULT_IMG))
    ap.add_argument("--reseed", action="store_true", help="清空并重新播种演示数据")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()

    dbp, imgp = Path(a.db), Path(a.images)
    if a.reseed or not dbp.exists():
        seed(dbp, imgp, force=a.reseed)
        print("演示数据已就绪（含邻居误认、紧急移除、自清、照片不清、封存损坏等待处置样例）")
    else:
        init_db(connect(dbp))
    serve(a.host, a.port, dbp, imgp, open_browser=not a.no_browser)


if __name__ == "__main__":
    main()
