# stack/: one-command launcher for the Ares copilot

Starts the whole copilot chain on localhost, each service in the background with a pid file in `stack/logs/`
(logs, pids and exported data live in `stack/logs/` and `stack/cache/`, both gitignored).

    stack/up.sh                  # real 8 km Jezero in every backend
    stack/up.sh --frame crater   # 1.8 km crop that matches the sandbox world ?world=crater (see docs/frames.md)
    stack/up.sh --with-viewer    # also the viewer with the copilot dock + street-view sandbox; prints ONE url to open (first run builds viewer tiles, ~2 min)
    stack/up.sh --rebuild        # regenerate the cropped / exported data dirs
    stack/down.sh                # stop what up.sh started (never touches other processes)

`up.sh` is idempotent: a service that is already up (live pid file and listening port) is left alone, and it fails with
the port holder's pid and working directory if a port is taken by something else.

| Service | Port | What | Readiness check |
|---|---|---|---|
| rover API (Yousef's `ares_api`) | 8000 | `ARES_DATA_DIR=stack/cache/ares_<frame>`, exported from our site by `devapi/export_ares_dir.py` | `GET /site` |
| query engine (`devapi`) | 8001 | `ATLAS_SITE_DIR` = our real site (or the crop) | `GET /health` |
| MCP over HTTP | 8765 | `ATLAS_API_URL=:8001`, `ATLAS_ROVER_API_URL=:8000`; startup frame check goes to `stack/logs/mcp.log` | HTTP answer on `/mcp` |
| agent | 8766 | provider and key from the git-ignored repo `.env` (`DEEPSEEK_API_KEY`) or `ANTHROPIC_API_KEY`; never printed | `GET /health` |
| chat page + minimap (`chat-dev`) | 5199 | standalone copilot UI, pointed at the real agent (`:8766`) | `GET /` |
| viewer + dock, sandbox (`--with-viewer`) | 5173, 5174 (next free if held) | `web` Vite with `VITE_SITE_DIR=stack/cache/work/data/build` (real Jezero tiles from `stack/build_viewer_site.sh`); the viewer spawns the sandbox | `GET /` |

## Which frame

Default `jezero`: the query engine, Yousef's API **and the viewer** load the real Jezero frame (lat0/lon0 18.452 / 77.418,
8192 m), so highlights, flights and the rover land where the model says. The viewer gets it from
`stack/build_viewer_site.sh`: tiles built from `data/raw/hirise/window_18.452_77.418_4096.tif` (the DTM our site is built
from) into `stack/cache/work/data/build`, never into `data/build`. The viewer dock compares `GET :8000/site` with its manifest
and shows a red banner on mismatch. `--frame crater` (1.8 km crop) is for the standalone sandbox `?world=crater` only; with
`--with-viewer` it would trigger that banner. Street view in the viewer uses the sandbox `crater` world and the dock converts
between frames. Details: `docs/frames.md`, `web/INTEGRATION.md`.

## Ports

Our own ports (8000, 8001, 8765, 8766, 5199) abort the start with the owner shown if taken. The viewer defaults to `5173` and
the sandbox to `5174`; if another project holds them (it does on this laptop at times) `--with-viewer` uses the next free ports
(`web/vite.config.ts` and the sandbox config read `VIEWER_PORT` / `SANDBOX_PORT`) and prints the final URL. Check any port with
`lsof -iTCP:5173 -sTCP:LISTEN -n -P`.

## Chat page

`http://localhost:5199/`: the chat panel on the right, and on the left the rover state (live over the API WebSocket,
never sends a pose), a hillshade minimap of the site (rendered once at startup by `devapi/render_minimap.py` into
`chat-dev/public/minimap/`, gitignored), the rover and its trail, markers, places, and the latest tool results (search
hits and destinations) as pulsing dots. For an offline demo without the stack: `cd chat-dev && npm run mock` then
`npm run dev` (the minimap needs `stack/up.sh` once, or `python -m devapi.render_minimap data/build/site/jezero chat-dev/public/minimap`).

## Prerequisites

`.venv` (repo Python with `mcp`, `rasterio`), `api/.venv` (Yousef's requirements), `data/build/site/jezero` (real site),
`npm install` in `chat-dev/`. For `--with-viewer`, `npm install` in `web/` and `web/src/rover/sandbox/`, and `data/raw/hirise/window_18.452_77.418_4096.tif` (network once for the NASA/JPL M20 traverse if `data/raw/m20_*.json` is absent).

## Manual use of the pieces

    ATLAS_SITE_DIR=data/build/site/jezero .venv/bin/python -m devapi --port 8001
    ATLAS_API_URL=http://localhost:8001 ATLAS_ROVER_API_URL=http://localhost:8000 .venv/bin/python mcp/server.py --http
    ATLAS_API_URL=http://localhost:8001 ATLAS_ROVER_API_URL=http://localhost:8000 .venv/bin/python -m agent
