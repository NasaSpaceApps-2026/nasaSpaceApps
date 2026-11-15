#!/usr/bin/env bash
# Start the Ares Atlas copilot stack (idempotent). See stack/README.md.
#   stack/up.sh [--frame jezero|crater] [--with-viewer] [--rebuild]
set -u
. "$(cd "$(dirname "$0")" && pwd)/lib.sh"
cd "$ROOT"

FRAME=jezero; VIEWER=0; REBUILD=0
while [ $# -gt 0 ]; do
  case "$1" in
    --frame) FRAME="${2:-}"; shift ;;
    --with-viewer) VIEWER=1 ;;
    --rebuild) REBUILD=1 ;;
    -h|--help) sed -n 2,3p "$0"; exit 0 ;;
    *) echo "unknown option $1"; exit 2 ;;
  esac
  shift
done
case "$FRAME" in jezero|crater) ;; *) echo "--frame must be jezero or crater"; exit 2 ;; esac

die() { echo "ERROR: $*" >&2; exit 1; }
[ -x "$PY" ] || die "$PY missing (python venv, see README)"
[ -x "$API_PY/uvicorn" ] || die "api/.venv missing: python3.11 -m venv api/.venv && api/.venv/bin/pip install -r api/requirements.txt"
[ -f "$ROOT/data/build/site/jezero/site.yaml" ] || die "data/build/site/jezero missing (real site, see PROGRESS.md / pipeline)"
[ -d "$ROOT/chat-dev/node_modules" ] || die "chat-dev/node_modules missing: cd chat-dev && npm install"

# ---- data: the one frame every backend uses -------------------------------------------------------
REAL="$ROOT/data/build/site/jezero"
if [ "$FRAME" = jezero ]; then
  SITE_DIR="$REAL"; ARES_DIR="$CACHE/ares_jezero"
else
  SITE_DIR="$CACHE/site_crater"; ARES_DIR="$CACHE/ares_crater"
  if [ "$REBUILD" = 1 ] || [ ! -f "$SITE_DIR/site.yaml" ]; then
    "$PY" -m devapi.crop_site "$REAL" "$SITE_DIR" --world crater >/dev/null || die "crop failed"
  fi
fi
if [ "$REBUILD" = 1 ] || [ ! -f "$ARES_DIR/manifest.json" ]; then
  echo "exporting $FRAME site for Yousef's API -> ${ARES_DIR#$ROOT/}"
  "$PY" -m devapi.export_ares_dir "$SITE_DIR" "$ARES_DIR" >/dev/null || die "export failed"
fi
"$PY" -m devapi.render_minimap "$SITE_DIR" "$ROOT/chat-dev/public/minimap" >/dev/null || die "minimap render failed"
echo "$FRAME" > "$LOGS/frame"

# ---- services ---------------------------------------------------------------------------------------
# start NAME PORT READY_URL ENV... -- CMD...   (env as KEY=VAL words before --)
start() {
  local name="$1" port="$2" url="$3"; shift 3
  local envs=()
  while [ "$1" != "--" ]; do envs+=("$1"); shift; done; shift
  local pid owner; pid="$(our_pid "$name")"
  if pid_alive "$pid" && [ -n "$(port_pid "$port")" ]; then echo "  $name: already running (pid $pid, :$port)"; return 0; fi
  owner="$(port_pid "$port")"
  if [ -n "$owner" ]; then die "port $port (needed by $name) is held by pid $owner in $(pid_cwd "$owner"); free it or stop that project"; fi
  nohup env ${envs[@]+"${envs[@]}"} "$@" >"$LOGS/$name.log" 2>&1 &
  echo $! > "$(pidfile "$name")"
  local i
  for i in $(seq 1 90); do
    if [ -n "$url" ]; then curl -s -o /dev/null --max-time 2 "$url" && break
    else [ -n "$(port_pid "$port")" ] && break; fi
    pid_alive "$(our_pid "$name")" || die "$name exited during start; see stack/logs/$name.log"
    sleep 1
  done
  [ "$i" = 90 ] && die "$name not ready after 90 s; see stack/logs/$name.log"
  echo "  $name: up on :$port"
}

echo "frame: $FRAME ($(basename "$SITE_DIR"))"
start api    8000 http://127.0.0.1:8000/site ARES_DATA_DIR="$ARES_DIR" -- "$API_PY/uvicorn" ares_api.main:app --app-dir api --host 127.0.0.1 --port 8000
start devapi 8001 http://127.0.0.1:8001/health ATLAS_SITE_DIR="$SITE_DIR" -- "$PY" -m devapi --port 8001
start mcp    8765 http://127.0.0.1:8765/mcp ATLAS_API_URL=http://localhost:8001 ATLAS_ROVER_API_URL=http://localhost:8000 -- "$PY" mcp/server.py --http --port 8765
start agent  8766 http://127.0.0.1:8766/health ATLAS_API_URL=http://localhost:8001 ATLAS_ROVER_API_URL=http://localhost:8000 ATLAS_MCP_URL=http://localhost:8765/mcp -- "$PY" -m agent
start chat   5199 http://127.0.0.1:5199/ -- sh -c "cd '$ROOT/chat-dev' && exec npx vite --host 127.0.0.1 --strictPort"

# MCP banner: did the startup frame check pass?
grep -h "frame check\|FRAME MISMATCH" "$LOGS/mcp.log" | tail -1

VIEWER_MSG=""
if [ "$VIEWER" = 1 ]; then
  echo "teammates' viewer (usage.md commands, unmodified):"
  # exact commands from usage.md; ports 5173/5174 are fixed there (street view iframes :5174)
  for spec in "5173:web:viewer" "5174:web/src/rover/sandbox:sandbox"; do
    port="${spec%%:*}"; rest="${spec#*:}"; dir="${rest%%:*}"; name="${rest#*:}"
    owner="$(port_pid "$port")"
    pid="$(our_pid "$name")"
    if [ -n "$owner" ] && pid_alive "$pid"; then echo "  $name: already running (:$port)"; continue; fi
    if [ -n "$owner" ]; then
      echo "  $name: SKIPPED, :$port is held by pid $owner in $(pid_cwd "$owner") (another project?). Stop it or open the URL there only if it is this repo."
      continue
    fi
    if [ ! -d "$ROOT/$dir/node_modules" ]; then echo "  $name: SKIPPED, run npm install in $dir first"; continue; fi
    if [ "$name" = viewer ]; then
      nohup sh -c "cd '$ROOT/web' && VITE_SITE_DIR=../contracts/fixtures exec npm run dev" >"$LOGS/viewer.log" 2>&1 &
    else
      nohup sh -c "cd '$ROOT/web/src/rover/sandbox' && exec npm run dev" >"$LOGS/sandbox.log" 2>&1 &
    fi
    echo $! > "$(pidfile "$name")"
    for i in $(seq 1 30); do [ -n "$(port_pid "$port")" ] && break; sleep 1; done
    echo "  $name: started on :$port"
  done
  [ "$FRAME" = jezero ] && VIEWER_MSG="NOTE: sandbox worlds and the 1 km fixture viewer are NOT in the real-Jezero frame; the rover will not show there. Use: stack/up.sh --frame crater --with-viewer and open the sandbox with ?world=crater (docs/frames.md)."
fi

echo
echo "Ares copilot stack is up (frame: $FRAME)"
echo "  chat + minimap : http://localhost:5199/"
echo "  agent          : http://localhost:8766/health"
echo "  MCP (HTTP)     : http://localhost:8765/mcp"
echo "  rover API      : http://localhost:8000/site   (Yousef's ares_api)"
echo "  query engine   : http://localhost:8001/site   (our devapi)"
if [ "$VIEWER" = 1 ]; then
  mine() { o="$(port_pid "$1")"; [ -n "$o" ] && case "$(pid_cwd "$o")" in "$ROOT"*) return 0 ;; esac; return 1; }
  mine 5173 && echo "  planet viewer  : http://localhost:5173/?view=planet" || echo "  planet viewer  : not running from this repo (see SKIPPED above)"
  if mine 5174; then [ "$FRAME" = crater ] && echo "  sandbox        : http://localhost:5174/?world=crater" || echo "  sandbox        : http://localhost:5174/"
  else echo "  sandbox        : not running from this repo (see SKIPPED above)"; fi
  [ -n "$VIEWER_MSG" ] && echo "$VIEWER_MSG"
fi
echo "logs: stack/logs/*.log   stop: stack/down.sh"
