#!/usr/bin/env bash
# Run the service natively on this machine and expose it with a Cloudflare
# quick tunnel. Natively rather than in Docker so a local model can use the
# Mac's GPU (Docker on macOS has no Metal/MPS access).
#
#   scripts/local.sh start    build frontend, start api + worker + tunnel
#   scripts/local.sh stop
#   scripts/local.sh status   show processes and the public URL
#
# Settings come from .env (see .env.example). Logs and PIDs go to data/run/.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN="$ROOT/data/run"
PY="$ROOT/backend/.venv/bin/python"
PORT="${PORT:-8000}"
mkdir -p "$RUN"

running() { [ -f "$RUN/$1.pid" ] && kill -0 "$(cat "$RUN/$1.pid")" 2>/dev/null; }

launch() { # name, command...
  local name=$1; shift
  if running "$name"; then echo "$name already running"; return; fi
  nohup "$@" >"$RUN/$name.log" 2>&1 &
  echo $! >"$RUN/$name.pid"
  echo "started $name (pid $!)"
}

tunnel_url() { grep -Eo 'https://[a-z0-9-]+\.trycloudflare\.com' "$RUN/tunnel.log" 2>/dev/null | tail -1; }

start() {
  [ -f "$ROOT/.env" ] || { echo "missing .env (copy .env.example)"; exit 1; }
  set -a; . "$ROOT/.env"; set +a

  (cd "$ROOT/frontend" && npm run build >"$RUN/frontend-build.log" 2>&1) \
    || { echo "frontend build failed, see $RUN/frontend-build.log"; exit 1; }

  cd "$ROOT/backend"
  launch api "$PY" -m uvicorn app.api:app --host 127.0.0.1 --port "$PORT" --proxy-headers
  launch worker "$PY" -m app.worker
  # HTTP/2 over TCP: many networks block the UDP that QUIC (the default) needs.
  if [ "${EXPOSE_TUNNEL:-false}" = "true" ]; then
    [ -n "${API_KEYS:-}" ] || { echo "API_KEYS is required before exposing a tunnel"; exit 1; }
    launch tunnel cloudflared tunnel --no-autoupdate --protocol http2 --url "http://127.0.0.1:$PORT"

    for _ in $(seq 1 30); do [ -n "$(tunnel_url)" ] && break; sleep 1; done
  fi
  status
}

stop() {
  for name in tunnel worker api; do
    if running "$name"; then kill "$(cat "$RUN/$name.pid")" && echo "stopped $name"; fi
    rm -f "$RUN/$name.pid"
  done
}

status() {
  for name in api worker tunnel; do
    if running "$name"; then echo "$name: running (pid $(cat "$RUN/$name.pid"))"; else echo "$name: stopped"; fi
  done
  echo "public URL: $(tunnel_url || true)"
}

case "${1:-}" in
  start) start ;;
  stop) stop ;;
  status) status ;;
  *) echo "usage: $0 start|stop|status"; exit 1 ;;
esac
