#!/usr/bin/env bash
set -euo pipefail
: "${INFERENCE_API_KEY:?Configure the existing shared inference API secret}"
export APP_ENV="${APP_ENV:-production}"
export QWEN_PROVIDER="${QWEN_PROVIDER:-qwen3_local}"
if [[ "$QWEN_PROVIDER" == qwen3_local ]]; then
  : "${QWEN_MODEL_DIR:?Mount the verified official Qwen3-VL-8B-Instruct model cache}"
  : "${YOLO_CONFIG:?Configure the accepted detector ensemble manifest}"
fi
export API_KEYS="$INFERENCE_API_KEY"
export YOLO_DEVICE="${YOLO_DEVICE:-cuda:0}"
python -m app.worker &
worker=$!
python -m uvicorn app.inference_api:create_inference_app --factory --host "${INFERENCE_BIND_HOST:-127.0.0.1}" --port "${PORT:-8000}" --no-proxy-headers &
api=$!
trap 'kill -TERM "$worker" "$api" 2>/dev/null || true' EXIT TERM INT
wait -n "$worker" "$api"
