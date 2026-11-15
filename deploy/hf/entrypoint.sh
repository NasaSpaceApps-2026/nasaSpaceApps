#!/bin/sh
# Starts every Ares Atlas service inside the Space container and keeps running. Logs go to stdout.
# Public port 7860 = `vite preview` (static viewer + same-origin proxy /__engine /__rover /__agent).
set -u
cd /app
export ENGINE_PORT=8001 ROVER_PORT=8000 AGENT_PORT=8766 MCP_PORT=8765
export HOME="${HOME:-/tmp/home}"; mkdir -p "$HOME" /tmp/ares /tmp/replays
export ATLAS_API_URL="http://127.0.0.1:$ENGINE_PORT" ATLAS_ROVER_API_URL="http://127.0.0.1:$ROVER_PORT" ATLAS_MCP_URL="http://127.0.0.1:$MCP_PORT/mcp"
PIDS=""
svc() { name="$1"; shift; ( "$@" 2>&1 | sed -u "s/^/[$name] /" ) & PIDS="$PIDS $!"; }
wait_for() { # name url seconds
  i=0; while [ $i -lt "$3" ]; do curl -s -o /dev/null --max-time 2 "$2" && { echo "ready: $1"; return 0; }; i=$((i+1)); sleep 1; done
  echo "ERROR: $1 not ready after $3 s ($2)"; return 1; }
trap 'kill $PIDS 2>/dev/null; exit 0' TERM INT

svc rover   python -m uvicorn ares_api.main:app --app-dir api --host 127.0.0.1 --port $ROVER_PORT
svc devapi  python -m devapi --port $ENGINE_PORT
wait_for rover  "http://127.0.0.1:$ROVER_PORT/site" 180 || exit 1
wait_for devapi "http://127.0.0.1:$ENGINE_PORT/health" 180 || exit 1
svc mcp     python mcp/server.py --http --port $MCP_PORT
wait_for mcp    "http://127.0.0.1:$MCP_PORT/mcp" 120 || exit 1
svc agent   python -m agent
wait_for agent  "http://127.0.0.1:$AGENT_PORT/health" 120 || exit 1
cd /app/web && svc viewer node node_modules/vite/bin/vite.js preview --host 0.0.0.0 --port "${PORT:-7860}" --strictPort
wait_for viewer "http://127.0.0.1:${PORT:-7860}/" 60 || exit 1
echo "Ares Atlas is up on :${PORT:-7860}"
# exit (so the Space restarts) as soon as any service dies
while :; do
  for p in $PIDS; do kill -0 "$p" 2>/dev/null || { echo "ERROR: a service exited; restarting container"; kill $PIDS 2>/dev/null; exit 1; }; done
  sleep 5
done
