# stack/: one-command launcher for the Ares copilot

Starts the whole copilot chain on localhost, each service in the background with a pid file in `stack/logs/`
(logs, pids and exported data live in `stack/logs/` and `stack/cache/`, both gitignored).

    stack/up.sh                  # real 8 km Jezero in every backend
    stack/up.sh --frame crater   # 1.8 km crop that matches the sandbox world ?world=crater (see docs/frames.md)
    stack/up.sh --with-viewer    # also the teammates' planet viewer (:5173) and rover sandbox (:5174)
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
| viewer, sandbox (`--with-viewer`) | 5173, 5174 | exactly the `usage.md` commands (`VITE_SITE_DIR=../contracts/fixtures npm run dev`, `npm run dev` in the sandbox) | port listening |

## Which frame

Default `jezero`: the query engine and Yousef's API both load our real site, so every coordinate the model gets from a
search is one the rover can drive to. The teammates' viewer and sandbox worlds are different frames and do not show this
rover. For the sandbox use `--frame crater --with-viewer` and open `http://localhost:5174/?world=crater`. Details, the
offset maths and what was verified: `docs/frames.md`.

## Ports

`5173` and `5174` are fixed by `usage.md` (street view iframes `:5174`). If another project holds them (it does on this
laptop at times), `--with-viewer` reports who holds each port and skips it rather than starting on another port. Our own
ports (8000, 8001, 8765, 8766, 5199) abort the start with the owner shown. Check any port yourself with
`lsof -iTCP:5173 -sTCP:LISTEN -n -P`.

## Chat page

`http://localhost:5199/`: the chat panel on the right, and on the left the rover state (live over the API WebSocket,
never sends a pose), a hillshade minimap of the site (rendered once at startup by `devapi/render_minimap.py` into
`chat-dev/public/minimap/`, gitignored), the rover and its trail, markers, places, and the latest tool results (search
hits and destinations) as pulsing dots. For an offline demo without the stack: `cd chat-dev && npm run mock` then
`npm run dev` (the minimap needs `stack/up.sh` once, or `python -m devapi.render_minimap data/build/site/jezero chat-dev/public/minimap`).

## Prerequisites

`.venv` (repo Python with `mcp`, `rasterio`), `api/.venv` (Yousef's requirements), `data/build/site/jezero` (real site),
`npm install` in `chat-dev/`. For `--with-viewer`, `npm install` in `web/` and `web/src/rover/sandbox/`.

## Manual use of the pieces

    ATLAS_SITE_DIR=data/build/site/jezero .venv/bin/python -m devapi --port 8001
    ATLAS_API_URL=http://localhost:8001 ATLAS_ROVER_API_URL=http://localhost:8000 .venv/bin/python mcp/server.py --http
    ATLAS_API_URL=http://localhost:8001 ATLAS_ROVER_API_URL=http://localhost:8000 .venv/bin/python -m agent
