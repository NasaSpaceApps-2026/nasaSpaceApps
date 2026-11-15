#!/usr/bin/env bash
# Stop everything stack/up.sh started (by pid file; never touches other projects' processes).
set -u
. "$(cd "$(dirname "$0")" && pwd)/lib.sh"
echo "stopping Ares copilot stack"
for s in chat agent mcp devapi api sandbox viewer; do
  [ -f "$(pidfile "$s")" ] && stop_service "$s"
done
rm -f "$LOGS/frame" "$LOGS/viewer.ports"
for p in 8000 8001 8765 8766 5199; do
  o="$(port_pid "$p")"; [ -n "$o" ] && echo "  note: :$p is still held by pid $o ($(pid_cwd "$o")); not started by this stack, left alone"
done
echo done
