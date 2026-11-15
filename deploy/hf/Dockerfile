# Ares Atlas: whole stack in one image for a Hugging Face Docker Space (port 7860, uid 1000, only /tmp writable).
# Space repo layout (built by deploy/hf/stage.sh): Dockerfile, README.md, requirements.txt, entrypoint.sh,
# code dirs (api devapi atlas_core mcp agent web), site/ (backend site), ares/ (rover API data), viewer_site/ (viewer tiles).

# ---- 1. viewer build --------------------------------------------------------------------------------------------------
FROM node:22-slim AS viewer
WORKDIR /app/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
# the viewer's publicDir (tiles, manifest, vectors, names) is baked into dist/ by `vite build`
COPY viewer_site/ /app/viewer_site/
RUN VITE_SITE_DIR=/app/viewer_site npm run build
# street view (Drive tab): the sandbox is its own Vite app (base /sandbox/). Build it and drop it into dist/sandbox so
# `vite preview` serves it statically (no dev proxy needed). Its site data (/manifest.json, /tiles) is the viewer's, at the root.
RUN mkdir -p /empty && cd src/rover/sandbox && npm ci --no-audit --no-fund \
 && ARES_SITE_DIR=/empty npm run build \
 && cp -r dist /app/web/dist/sandbox \
 && rm -rf /app/web/src /app/viewer_site

# ---- 2. runtime: python + node (for `vite preview`) --------------------------------------------------------------------
FROM python:3.13-slim
COPY --from=node:22-slim /usr/local/bin/node /usr/local/bin/node
RUN apt-get update && apt-get install -y --no-install-recommends curl libexpat1 && rm -rf /var/lib/apt/lists/* \
 && useradd -m -u 1000 user
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=user api/ api/
COPY --chown=user devapi/ devapi/
COPY --chown=user atlas_core/ atlas_core/
COPY --chown=user mcp/ mcp/
COPY --chown=user agent/ agent/
COPY --chown=user entrypoint.sh /app/entrypoint.sh
COPY --chown=user site/ /app/site/jezero/
COPY --chown=user ares/ /app/ares/
# /worlds/<name> for street view (served by the roverWorlds() plugin of vite preview, repo root = /app)
COPY --chown=user contracts/ /app/contracts/
# export_ares_dir symlinks dem/slope/rough to the site rasters (absolute host paths); re-point them
RUN ln -sf /app/site/jezero/elevation.tif /app/ares/analysis/dem.tif \
 && ln -sf /app/site/jezero/slope_2m.tif /app/ares/analysis/slope.tif \
 && ln -sf /app/site/jezero/rough_5x5.tif /app/ares/analysis/rough.tif
# place names are looked up at data/build/data/names.geojson (atlas_core/places.py)
COPY --chown=user viewer_site_names/names.geojson /app/data/build/data/names.geojson
# built viewer + the node_modules `vite preview` needs
COPY --from=viewer --chown=user /app/web/ /app/web/

ENV PYTHONUNBUFFERED=1 HOME=/tmp/home \
    ATLAS_SITE_DIR=/app/site/jezero ARES_DATA_DIR=/app/ares ARES_STATE_FILE=/tmp/ares/state.json \
    ATLAS_RECORD=0 ATLAS_REPLAY_DIR=/tmp/replays \
    ATLAS_CHAT_RATE_PER_MIN=6 ATLAS_CHAT_DAILY_CAP=500 ATLAS_TRUST_XFF=1 \
    PORT=7860
USER 1000
EXPOSE 7860
CMD ["/bin/sh", "/app/entrypoint.sh"]
