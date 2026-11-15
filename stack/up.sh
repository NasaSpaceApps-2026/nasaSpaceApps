#!/usr/bin/env bash
# Start the Ares Atlas copilot stack (idempotent). See stack/README.md.
#   stack/up.sh [--frame jezero|crater] [--with-viewer] [--rebuild]   (--with-viewer: viewer + sandbox, prints one URL)
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
VP=""; SP=""
if [ "$VIEWER" = 1 ]; then
  echo "viewer (Rithvik's Cesium app with the copilot dock) + street-view sandbox:"
  [ -d "$ROOT/web/node_modules" ] || die "web/node_modules missing: cd web && npm install"
  [ -d "$ROOT/web/src/rover/sandbox/node_modules" ] || die "sandbox node_modules missing: cd web/src/rover/sandbox && npm install"
  # The viewer must show the SAME frame the backend uses (lat0/lon0/size), so it serves tiles built from our real
  # Jezero DTM, not contracts/fixtures. Built once into stack/cache (about 2 minutes), gitignored.
  if [ "$REBUILD" = 1 ]; then "$ROOT/stack/build_viewer_site.sh" --rebuild || die "viewer site build failed"
  else "$ROOT/stack/build_viewer_site.sh" || die "viewer site build failed"; fi
  # Ports: 5173 / 5174 when free, else the next free ones (another project may hold them). web/vite.config.ts and the
  # sandbox config read VIEWER_PORT / SANDBOX_PORT.
  free_port() { local p="$1"; while [ -n "$(port_pid "$p")" ]; do p=$((p+1)); done; echo "$p"; }
  if [ -f "$LOGS/viewer.ports" ] && pid_alive "$(our_pid viewer)"; then read -r VP SP < "$LOGS/viewer.ports"; echo "  viewer: already running (:$VP, sandbox :$SP)"
  else
    VP="$(free_port 5173)"; SP="$(free_port 5174)"; [ "$SP" = "$VP" ] && SP="$(free_port $((VP+1)))"
    [ "$VP" != 5173 ] && echo "  :5173 is taken by another process, using :$VP"
    [ "$SP" != 5174 ] && echo "  :5174 is taken by another process, using :$SP"
    nohup sh -c "cd '$ROOT/web' && VIEWER_PORT=$VP SANDBOX_PORT=$SP VITE_SITE_DIR='$VIEWER_SITE' BROWSER=none exec npx vite --host 127.0.0.1 --port $VP --strictPort" >"$LOGS/viewer.log" 2>&1 &
    echo $! > "$(pidfile viewer)"
    echo "$VP $SP" > "$LOGS/viewer.ports"
    for i in $(seq 1 60); do curl -s -o /dev/null --max-time 2 "http://127.0.0.1:$VP/" && break; pid_alive "$(our_pid viewer)" || die "viewer exited; see stack/logs/viewer.log"; sleep 1; done
    for i in $(seq 1 40); do [ -n "$(port_pid "$SP")" ] && break; sleep 1; done  # the viewer starts the sandbox itself
    [ -n "$(port_pid "$SP")" ] || echo "  WARNING: sandbox not listening on :$SP (street view will not load); see stack/logs/viewer.log"
    echo "  viewer: up on :$VP, sandbox on :$SP"
  fi
  [ "$FRAME" = jezero ] || VIEWER_MSG="NOTE: --frame $FRAME is a different frame than the viewer's Jezero tiles; the dock will show a red frame-mismatch warning. Use the default frame with --with-viewer."
fi

echo
echo "Ares copilot stack is up (frame: $FRAME)"
echo "  chat + minimap : http://localhost:5199/"
echo "  agent          : http://localhost:8766/health"
echo "  MCP (HTTP)     : http://localhost:8765/mcp"
echo "  rover API      : http://localhost:8000/site   (Yousef's ares_api)"
echo "  query engine   : http://localhost:8001/site   (our devapi)"
if [ "$VIEWER" = 1 ]; then
  [ -n "$VIEWER_MSG" ] && echo "$VIEWER_MSG"
  echo
  echo "  OPEN THIS : http://localhost:$VP/        (site viewer + copilot dock; planet globe and street view: add ?view=planet)"
fi
echo "logs: stack/logs/*.log   stop: stack/down.sh"
