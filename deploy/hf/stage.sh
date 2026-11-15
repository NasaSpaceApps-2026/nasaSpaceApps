#!/usr/bin/env bash
# Assemble the Hugging Face Space repo contents into a staging dir.
#   deploy/hf/stage.sh [STAGE_DIR=/tmp/ares-space] [--no-compress]
# Reads (never writes): repo code, data/build/site/jezero (backend site), stack/cache (viewer tiles, rover-API data).
# No secrets: .env is never copied; DEEPSEEK_API_KEY comes from the Space secret at runtime.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STAGE="/tmp/ares-space"; COMPRESS=1
for a in "$@"; do case "$a" in --no-compress) COMPRESS=0 ;; *) STAGE="$a" ;; esac; done
PY="$ROOT/.venv/bin/python"; [ -x "$PY" ] || PY=python3
SITE="$ROOT/data/build/site/jezero"
VIEW="$ROOT/stack/cache/work/data/build"
ARES="$ROOT/stack/cache/ares_jezero"
for f in "$SITE/site.yaml" "$VIEW/manifest.json" "$VIEW/data/names.geojson" "$ARES/manifest.json"; do
  [ -f "$f" ] || { echo "missing $f (run stack/up.sh --with-viewer once to build the caches)"; exit 1; }
done
[ -d "$ROOT/api/fixtures/analysis" ] || { echo "missing api/fixtures/analysis"; exit 1; }

rm -rf "$STAGE"; mkdir -p "$STAGE"
X=(--exclude __pycache__ --exclude '*.pyc' --exclude .venv --exclude node_modules --exclude dist --exclude .state --exclude .pytest_cache)
rsync -a "${X[@]}" --exclude tests --exclude fixtures/playground --exclude fixtures/analysis "$ROOT/api/" "$STAGE/api/"
# api/fixtures/analysis = ARES_EXTRA_LAYER_DIR default (extra layers added to the rover API data)
mkdir -p "$STAGE/api/fixtures"; rsync -a "$ROOT/api/fixtures/analysis" "$STAGE/api/fixtures/"
for d in devapi atlas_core mcp; do rsync -a "${X[@]}" "$ROOT/$d/" "$STAGE/$d/"; done
rsync -a "${X[@]}" --exclude replays --exclude evals "$ROOT/agent/" "$STAGE/agent/"
rsync -a "${X[@]}" --exclude 'src/rover/sandbox/node_modules' --exclude 'src/rover/sandbox/dist' --exclude shots "$ROOT/web/" "$STAGE/web/"
rsync -a "$ARES/" "$STAGE/ares/"
mkdir -p "$STAGE/contracts/fixtures"; rsync -a "$ROOT/contracts/fixtures/rover_worlds" "$STAGE/contracts/fixtures/"
rsync -a --exclude 'SOURCES_site.md' "$SITE/" "$STAGE/site/"
# viewer tiles: everything the viewer fetches (tiles, manifest, vectors, names, DEM); analysis/ is only for the backends
rsync -a --exclude analysis "$VIEW/" "$STAGE/viewer_site/"
mkdir -p "$STAGE/viewer_site_names"; cp "$VIEW/data/names.geojson" "$STAGE/viewer_site_names/names.geojson"
cp "$ROOT/deploy/hf/Dockerfile" "$ROOT/deploy/hf/README.md" "$ROOT/deploy/hf/requirements.txt" "$ROOT/deploy/hf/entrypoint.sh" "$STAGE/"
printf '__pycache__/\n*.pyc\nnode_modules/\n.env\n' > "$STAGE/.gitignore"
printf '.env\n**/node_modules\n**/__pycache__\n' > "$STAGE/.dockerignore"

if [ "$COMPRESS" = 1 ]; then
  echo "recompressing GeoTIFFs (lossless deflate) ..."
  "$PY" "$ROOT/deploy/hf/recompress.py" "$STAGE/site" | tail -3
fi
if grep -rIl "sk-[A-Za-z0-9]\{20,\}" "$STAGE" --exclude-dir=node_modules 2>/dev/null | head -1 | grep -q .; then
  echo "ERROR: something that looks like an API key is in the staging dir"; exit 1
fi
echo; du -sh "$STAGE"; du -sh "$STAGE"/* | sort -h | tail -6
echo "staged: $STAGE   next: hf upload <user>/<space> $STAGE --repo-type=space"
