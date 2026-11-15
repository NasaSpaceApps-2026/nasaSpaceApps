#!/usr/bin/env bash
# Builds the viewer's tile/manifest/vector tree into stack/cache/work/data/build from the HiRISE DTM already in
# data/raw (no network except an optional name-list download in 05b). Runs COPIES of pipeline/*.py so that
# data/build (Caden's) is never written. The copies resolve their ROOT to stack/cache/work.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
say() { printf '%s\n' "$*"; }
W="$CACHE/work"
[ "${1:-}" = "--rebuild" ] && rm -rf "$W/data/build"
[ -f "$VIEWER_SITE/manifest.json" ] && [ -f "$VIEWER_SITE/data/traverse.geojson" ] && { say "viewer site already built: $VIEWER_SITE"; exit 0; }
DTM="$(ls "$ROOT"/data/raw/hirise/window_18.452_77.418_4096.tif "$ROOT"/data/raw/dtm_window.tif 2>/dev/null | head -1 || true)"
[ -n "$DTM" ] || { say "ERROR: no HiRISE DTM window in data/raw (run: .venv/bin/python pipeline/00_fetch.py dtm)"; exit 1; }
MM="$ROOT/data/raw/science/mmgis"
mkdir -p "$W/pipeline" "$W/data/raw" "$LOGS"
cp "$ROOT"/pipeline/*.py "$W/pipeline/"
ln -sf "$DTM" "$W/data/raw/dtm_window.tif"
# M20 traverse + waypoints (NASA/JPL MMGIS). Taken from data/raw when present, else downloaded once into stack/cache/dl.
mkdir -p "$CACHE/dl"
for f in traverse waypoints; do
  src=""
  for c in "$ROOT/data/raw/m20_$f.json" "$MM/M20_$f.json" "$CACHE/dl/m20_$f.json"; do [ -s "$c" ] && { src="$c"; break; }; done
  if [ -z "$src" ]; then
    say "downloading M20_$f.json (NASA/JPL MMGIS) ..."
    curl -fsS -m 120 -o "$CACHE/dl/m20_$f.json" "https://mars.nasa.gov/mmgis-maps/M20/Layers/json/M20_$f.json" \
      || { rm -f "$CACHE/dl/m20_$f.json"; say "ERROR: could not download M20_$f.json; put it in data/raw/ (see CLAUDE.md)"; exit 1; }
    src="$CACHE/dl/m20_$f.json"
  fi
  ln -sf "$src" "$W/data/raw/m20_$f.json"
done
# HiRISE orthoimagery (USGS 25 cm, windowed read, 1 m) + Viking colour source -> pipeline/build_tiles.py colourises it (FALSE COLOUR).
# Both are cached in stack/cache/dl; on failure the build falls back to the DTM hillshade (build_tiles.py does that when no ortho exists).
ORTHO="$CACHE/dl/ortho_window_1m.tif"
if [ ! -s "$ORTHO" ]; then
  say "fetching HiRISE orthoimage window (USGS, a few minutes) ..."
  "$PY" -I "$ROOT/stack/fetch_ortho.py" "$ORTHO.part" >"$LOGS/fetch_ortho.log" 2>&1 && mv "$ORTHO.part" "$ORTHO" \
    || { rm -f "$ORTHO.part"; say "(orthoimage fetch failed, see $LOGS/fetch_ortho.log: using hillshade base layer)"; }
fi
[ -s "$ORTHO" ] && ln -sf "$ORTHO" "$W/data/raw/ortho_window.tif"
if [ -s "$ORTHO" ] && [ ! -s "$CACHE/dl/viking_color.png" ]; then
  "$PY" "$ROOT/stack/fetch_viking.py" "$CACHE/dl" >>"$LOGS/fetch_ortho.log" 2>&1 || say "(Viking colour fetch failed: orthoimage stays greyscale)"
fi
for f in viking_color.png viking_color.json; do [ -s "$CACHE/dl/$f" ] && ln -sf "$CACHE/dl/$f" "$W/data/raw/$f"; done
cd "$W"
say "building viewer tiles from $DTM (about 1-2 min)..."
{ "$PY" pipeline/build_tiles.py && "$PY" pipeline/09_export_web.py && "$PY" pipeline/05_vectors.py \
  && { "$PY" pipeline/05b_names.py || say "(place names skipped: download failed)"; }; } >"$LOGS/build_viewer_site.log" 2>&1 \
  || { say "ERROR: viewer site build failed, see $LOGS/build_viewer_site.log"; exit 1; }
say "built $VIEWER_SITE"
