# Ares Atlas — notes for Claude

NASA Space Apps 2026, challenge "Interplanetary Survival Guide: Martian Map". A **research and
exploration tool** on real NASA data for planning Mars EVAs at Jezero crater — not a game.
Every number shown to a user cites its source (mission/instrument) or is labelled "assumed";
synthetic data is labelled "not Mars data".

Read first: `SCIENCE_VALIDITY.md` (what our numbers rest on), `PRD.md` (product + six contracts), `docs/scope-c-d.md` (Yousef/Sameer split and
the C/D interface — overrides the PRD where they differ), `PROGRESS.md` (current state).

## Ownership

| Who | Area | Folders |
|---|---|---|
| Caden | Data pipeline, bubble refine loop | `pipeline/`, `data/`, `contracts/fixtures/` (except `api/`) |
| Rithvik | Viewer app (globe, terrain, cameras, layers UI) | `web/` except below |
| Yousef | Backend API, route planning, search, rover sim, browser rover driving | `api/`, `contracts/api.openapi.yaml`, `contracts/fixtures/api/`, `web/src/rover/` |
| Sameer | MCP server, agent backend, chat panel, demo/docs | `mcp/`, `agent/`, `web/src/panels/chat/`, `docs/` |

Stay inside the folders of the person you're working for.

## API (`api/`)

```
python3.11 -m venv api/.venv && api/.venv/bin/pip install -r api/requirements.txt
api/.venv/bin/python api/scripts/make_dev_layers.py      # synthetic mineral layers (until Stream A ships CRISM)
api/.venv/bin/uvicorn ares_api.main:app --app-dir api --port 8000
ARES_DATA_DIR=api/fixtures/playground api/.venv/bin/uvicorn ares_api.main:app --app-dir api --port 8000  # richer 2 km test site
api/.venv/bin/python api/scripts/make_playground.py   # regenerate the playground (~2 min)
# Real Jezero (8 km, HiRISE DTM + Perseverance traverse) into data/build/ (gitignored), ~5 min:
#   api/.venv/bin/python pipeline/00_fetch.py dtm && git checkout data/SOURCES.md
#   curl -o data/raw/m20_traverse.json https://mars.nasa.gov/mmgis-maps/M20/Layers/json/M20_traverse.json
#   curl -o data/raw/m20_waypoints.json https://mars.nasa.gov/mmgis-maps/M20/Layers/json/M20_waypoints.json
#   api/.venv/bin/python pipeline/build_tiles.py && api/.venv/bin/python pipeline/05_vectors.py && api/.venv/bin/python pipeline/05b_names.py
ARES_DATA_DIR=data/build api/.venv/bin/uvicorn ares_api.main:app --app-dir api --port 8000   # real site
api/.venv/bin/python -m pytest api -q                     # tests build their own synthetic terrain
api/.venv/bin/python api/scripts/dump_contract.py         # regenerate OpenAPI + canned responses
```

- Data folder: `ARES_DATA_DIR` (default `contracts/fixtures`; real data later `data/build`).
  Extra `layer_*.tif` from `ARES_EXTRA_LAYER_DIR` (default `api/fixtures/analysis`, committed; regenerate with `make_dev_layers.py`).
- Module map (`api/ares_api/`): `terrain.py` rasters + sampling, `places.py` POIs/markers/position
  resolution, `planner.py` MCP_Geometric routes, `search.py` mineral/geology site search,
  `rover.py` 10 Hz sim + FSD, `science.py` science traverse + validation, `hub.py` WebSocket,
  `main.py` FastAPI app (`create_app(data_dir)`).
- Profiles and all speeds/slope limits: `api/profiles.yaml`. Search targets: `api/targets.yaml`.
- Positions everywhere accept `{x, y}`, `[x, y]`, `"x,y"`, `"poi:<id>"`, `"marker:<id>"`, `"rover"`, or a bare id.
- Errors are HTTP 422 `{"error": "<sentence an agent can act on>"}`.

## Rover sandbox (`web/src/rover/sandbox/`)

`npm install && npm run dev` (port 5174) serves `api/fixtures/playground` (or `ARES_SITE_DIR`). Drives the crewed buggy (`crew_buggy` profile); `npm run check` runs the physics checks.

## Conventions

- Site frame metres: x east, y north, origin at manifest lat0/lon0; headings clockwise from north
  (PRD Contract 1). three.js: X = x, Y = z, Z = -y.
- Contracts in `PRD.md` and `docs/scope-c-d.md` are frozen; additions are fine, renames are not.
- Keep `PROGRESS.md` updated after each meaningful change.
- Log every physics/science number or model change in `SCIENCE_VALIDITY.md` (value, status, basis). Never mark something "cited" without a real source.
