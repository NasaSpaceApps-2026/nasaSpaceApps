# Shared helpers for stack/up.sh and stack/down.sh (sourced; bash 3.2 compatible).
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOGS="$ROOT/stack/logs"
CACHE="$ROOT/stack/cache"
PY="$ROOT/.venv/bin/python"
API_PY="$ROOT/api/.venv/bin"
mkdir -p "$LOGS" "$CACHE"

port_pid() { lsof -tiTCP:"$1" -sTCP:LISTEN -n -P 2>/dev/null | head -1; }
pid_cwd() { lsof -p "$1" -a -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1; }
pid_alive() { [ -n "$1" ] && kill -0 "$1" 2>/dev/null; }
pidfile() { echo "$LOGS/$1.pid"; }
our_pid() { local f; f="$(pidfile "$1")"; [ -f "$f" ] && cat "$f"; }

descendants() { local c; for c in $(pgrep -P "$1" 2>/dev/null); do echo "$c"; descendants "$c"; done; }

# stop one service we started (whole process group, then the port holder if it is ours)
stop_service() {
  local name="$1" pid
  pid="$(our_pid "$name")"
  if pid_alive "$pid"; then
    local kids; kids="$(descendants "$pid")"
    kill "$pid" $kids 2>/dev/null
    for _ in 1 2 3 4 5 6 7 8 9 10; do pid_alive "$pid" || break; sleep 0.3; done
    pid_alive "$pid" && kill -9 "$pid" $kids 2>/dev/null
    echo "  stopped $name (pid $pid)"
  else
    echo "  $name: not running (no live pid file)"
  fi
  rm -f "$(pidfile "$name")"
}

# Cesium viewer tile tree for our real Jezero frame (built by stack/build_viewer_site.sh, gitignored under stack/cache).
VIEWER_SITE="$CACHE/work/data/build"
