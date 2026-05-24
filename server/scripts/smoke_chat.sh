#!/usr/bin/env bash
# Phase 2 SSE 端到端 smoke。
#
# 前置：
#   1. docker compose up -d 起 MySQL（docker/mysql 目录）
#   2. python -m app.db.init_db && python -m scripts.seed_mysql
#   3. python -m scripts.build_index --rebuild
#   4. uvicorn app.main:app --host 0.0.0.0 --port 8000 &
#
# 用法：
#   bash scripts/smoke_chat.sh                 # 默认 query
#   bash scripts/smoke_chat.sh "你想问的话"     # 自定义 query
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
QUERY="${1:-推荐一款适合油皮的洗面奶}"

# 用 python -c 构造 JSON body，避免 shell 引号 / 中文转义坑
BODY=$(QUERY="${QUERY}" python3 -c 'import json,os;print(json.dumps({"session_id": None, "message": os.environ["QUERY"]}, ensure_ascii=False))')

echo "POST ${BASE_URL}/api/v1/chat/stream  query=${QUERY}"
echo "----------"
curl -N -s -X POST "${BASE_URL}/api/v1/chat/stream" \
  -H 'Content-Type: application/json' \
  --data-raw "${BODY}"
echo
echo "----------"
echo "完成。检查上方输出应包含：event: token / event: product_card / event: done。"
