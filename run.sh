#!/usr/bin/env bash
# 一条本地命令：初始化 + 播种（含错认样例）+ 启动
set -euo pipefail
cd "$(dirname "$0")"
PORT="${1:-8000}"
if [ ! -f data/hallway.db ]; then
  python3 -m app.seed
fi
echo "▶ 打开 http://127.0.0.1:${PORT}   （住户公告端 /public）"
exec python3 -m app.main "${PORT}"
