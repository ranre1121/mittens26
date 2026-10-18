#!/usr/bin/env bash
# Run the API and the analysis worker as two processes in one container, for
# hosts that give us a single container (Cloudflare Containers). Docker
# Compose runs them as separate services instead and doesn't use this script.
#
# If either process exits, stop the container so the platform restarts it
# rather than leaving a half-working service.
set -uo pipefail

python -m app.worker &
worker=$!
uvicorn app.api:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers &
api=$!

trap 'kill -TERM "$worker" "$api" 2>/dev/null' TERM INT
wait -n "$worker" "$api"
status=$?
kill -TERM "$worker" "$api" 2>/dev/null
wait
exit "$status"
