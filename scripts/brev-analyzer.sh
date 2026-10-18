#!/usr/bin/env bash
# Set up and run the GPU analysis worker on a Brev (or any CUDA Linux) machine.
#
# Two processes:
#   qwen_runner.daemon  keeps Qwen3-VL-8B-Instruct resident on the GPU and
#                       answers on a local Unix socket
#   app.remote          pulls jobs from the Cloudflare Worker over HTTPS and runs
#                       them through the normal backend pipeline, whose Qwen
#                       provider command (qwen_runner.client) talks to the daemon
#
# Usage, from your laptop:
#   rsync -az --exclude .venv backend rules scripts <host>:/ephemeral/kc/repo/
#   ssh <host> 'bash /ephemeral/kc/repo/scripts/brev-analyzer.sh setup'   # once
#   ssh <host> 'bash /ephemeral/kc/repo/scripts/brev-analyzer.sh start'
#
# The analyzer needs ANALYZER_TOKEN (the Worker secret of the same name) in
# $ROOT/analyzer.env. It is never stored in Git.
set -euo pipefail

ROOT=${ROOT:-/ephemeral/kc}
REVISION=0c351dd01ed87e9c1b53cbc748cba10e6187ff3b
MODEL_DIR=$ROOT/hf/hub/models--Qwen--Qwen3-VL-8B-Instruct/snapshots/$REVISION
export PATH=$HOME/.local/bin:$PATH

setup() {
  command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
  cd "$ROOT"
  [ -d venv ] || uv venv --python 3.12 venv
  . venv/bin/activate
  uv pip install "torch==2.10.0" torchvision --index-url https://download.pytorch.org/whl/cu126
  uv pip install -e "repo/backend[qwen]" "huggingface_hub[hf_xet]"
  HF_HOME=$ROOT/hf python -c "from huggingface_hub import snapshot_download as d; print(d('Qwen/Qwen3-VL-8B-Instruct', revision='$REVISION'))"

  [ -f daemon.env ] || cat > daemon.env <<EOF
QWEN_MODEL_DIR=$MODEL_DIR
QWEN_SOCKET=$ROOT/qwen.sock
QWEN_LOG_DIR=$ROOT/qwen-log
QWEN_TIME_BUDGET_S=200
QWEN_BATCH_SIZE=4
# Optional experimental pilot adapter (NF4 + LoRA). Also set QWEN_MODEL_VERSION
# in analyzer.env to the version the daemon logs at startup.
# QWEN_LORA_DIR=$ROOT/lora
EOF

  [ -f analyzer.env ] || { umask 077; cat > analyzer.env <<EOF
APP_ENV=production
QWEN_PROVIDER=command
QWEN_COMMAND_JSON='["$ROOT/venv/bin/python","-m","qwen_runner.client"]'
QWEN_MODEL_ID=Qwen/Qwen3-VL-8B-Instruct
QWEN_MODEL_VERSION=${REVISION:0:12}+prompt-v2
QWEN_SOCKET=$ROOT/qwen.sock
JOB_TIMEOUT_SECONDS=280
DATA_DIR=$ROOT/data
WORKER_POLL_SECONDS=3
ANALYZER_URL=https://datathon26.pognar1121.workers.dev
ANALYZER_TOKEN=
EOF
  echo "Fill in ANALYZER_TOKEN in $ROOT/analyzer.env"; }
}

# Start (or restart) one background process: name, env file, python module.
run() {
  local name=$1 envfile=$2 module=$3
  local pid
  pid=$(cat "$ROOT/$name.pid" 2>/dev/null) && kill "$pid" 2>/dev/null && sleep 2 || true
  cd "$ROOT/repo/backend"
  set -a; . "$ROOT/$envfile"; set +a
  setsid "$ROOT/venv/bin/python" -m "$module" >> "$ROOT/$name.log" 2>&1 < /dev/null &
  echo $! > "$ROOT/$name.pid"
  echo "$name started (pid $!), log $ROOT/$name.log"
}

case ${1:-} in
  setup) setup ;;
  start)
    run daemon daemon.env qwen_runner.daemon
    until grep -q "ready on" "$ROOT/daemon.log" 2>/dev/null; do sleep 2; done
    run analyzer analyzer.env app.remote ;;
  stop)
    for name in analyzer daemon; do
      pid=$(cat "$ROOT/$name.pid" 2>/dev/null) && kill "$pid" 2>/dev/null && echo "$name stopped" || true
    done ;;
  *) echo "usage: $0 setup|start|stop" >&2; exit 2 ;;
esac
