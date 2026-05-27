#!/usr/bin/env bash
# Phase 5 端到端冒烟：upload → chat (with image_id) → 看到 token + product_card 事件。
#
# 用法：
#   bash scripts/smoke_image_chat.sh                                    # 默认 Nike Pegasus 41
#   bash scripts/smoke_image_chat.sh path/to/img.jpg "找同款"            # 自定义
#   BASE_URL=http://10.0.0.5:8000 bash scripts/smoke_image_chat.sh
#
# 前置：
#   1. 服务起着：uvicorn app.main:app --port 8000
#   2. Milvus 已灌过 image chunk：python -m scripts.build_image_index --rebuild
set -euo pipefail

BASE="${BASE_URL:-http://127.0.0.1:8000}"
IMG="${1:-../ecommerce_agent_dataset/3_服饰运动/images/p_clothes_007_live.jpg}"
MSG="${2:-找同款}"

if [ ! -f "$IMG" ]; then
  echo "图片不存在：$IMG"
  exit 1
fi

echo "== 1) upload $IMG"
UPLOAD_RESP=$(curl -s -X POST "$BASE/api/v1/upload/image" -F "file=@$IMG;type=image/jpeg")
echo "$UPLOAD_RESP" | python -m json.tool

IMAGE_ID=$(echo "$UPLOAD_RESP" | python -c "import sys,json;print(json.load(sys.stdin).get('image_id',''))")
if [ -z "$IMAGE_ID" ]; then
  echo "未拿到 image_id，看上方响应体定位（503 = vision API 繁忙；4xx = 入参错）"
  exit 2
fi
echo "  → image_id=$IMAGE_ID"
echo

echo "== 2) chat with image_id（message=\"$MSG\"）"
curl -N -s -X POST "$BASE/api/v1/chat/stream" \
  -H "Content-Type: application/json" \
  -d "{\"message\":\"$MSG\",\"image_id\":\"$IMAGE_ID\"}" | head -80

echo
echo "== 完成"
