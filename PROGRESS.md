# Progress

Status of Streams C + D (Yousef, Sameer). Update after each meaningful change. Newest first.

## Yousef — backend + rover

Scope tasks from `docs/scope-c-d.md`:

| # | Task | Status |
|---|---|---|
| 1 | Stub server / OpenAPI / canned responses | Done — real implementation instead of stubs; `contracts/api.openapi.yaml`, `contracts/fixtures/api/*.json` |
| 2 | Terrain service (`/site`, `/terrain/*`, `/pois`, `/health`, static tiles) | Done |
| 3 | Markers (REST + WS `marker.click`/`marker.add`/`marker.remove`) | Done |
| 4 | Route planner (`/route`, `/route/{id}`, `/route/{id}/assess`) | Done |
| 5 | Site search (`/search/targets`, `/search/sites`) | Done on synthetic mineral layers |
| 6 | Rover sim + WebSocket hub (`/rover/command`, `/rover/state`, `/view`, `/ws`) | Done |
| 7 | FSD policy (slope stop, caution slowdown, one replan) | Done |
| 8 | Browser rover driving (`web/src/rover/`) | Done as a standalone module + sandbox (`cd web/src/rover/sandbox && npm install && npm run dev`, port 5174; `npm run check` → 10 physics checks). Waiting on Rithvik's `web/` app to plug in (see `web/src/rover/README.md`) |
| 9 | Science traverse + validation | Done |
| 10 | Hardening | Done: readable 422 errors, input validation, heavy endpoints in a worker thread (sim keeps ticking), markers/routes persist to `api/.state/state.json` (`ARES_STATE_FILE`), thread-safe WS emit |
| — | One vehicle model | API sim drives `crew_buggy` by default using the browser's power/grip balance (`ares_api/vehicle.py`); `GET /rover/vehicles`, `POST /rover/vehicle` |
| 11 | More realistic physics | v3 rigid-body vehicle (`web/src/rover/physics.ts`): chassis with momentum and rotation, per-wheel spring-damper suspension (buggy 1.15 Hz, ζ 0.38, 25 cm travel), friction-circle tyres (grip-limited cornering), Mars g, power/grip-limited drive, coasting, brakes, jumps and roll-overs. Crewed buggy concept (`crew_buggy`) with boost and time-lapse. 19 physics checks |
| — | Detailed buggy + astronaut model (`web/src/rover/buggyModel.ts`, `buggy/`) | Done: ~120k tris, ~130 draw calls; wired into sandbox with boost dust + suspension |
| — | Playground test site (`api/fixtures/playground/`, `api/scripts/make_playground.py`) | Done: 2 km synthetic site with gullied delta front (detour story works: direct line 36°, route 14° via gully), breached crater, dunes, butte, channels; 22 POIs, 2.5 km traverse. Sandbox serves it by default; run API with `ARES_DATA_DIR=api/fixtures/playground` |

Tests: `api/.venv/bin/python -m pytest api -q` → 17 passed.

### Additions beyond the PRD contract (tell Sameer)

- `GET /route/{id}/assess` backs the `assess_route` tool; `GET /route/{id}` returns a stored route.
- `POST /agent/event` — MCP posts each tool call here; the API broadcasts `agent.event` to the viewer.
- `GET /view` returns last view state; viewer may send WS `view.state` with its camera pose.
- Route responses include `direct_line` (straight-line distance, max slope, whether it crosses no-go)
  so the agent can explain why a route detours.
- `POST /rover/command` takes `wait` (default true) and `timeout_s`; drive results carry `stop_reason`,
  `message`, `replans`, `events`.
- `max_slope_deg` can only tighten a profile's limit, never loosen it.
- `GET /rover/vehicles`, `POST /rover/vehicle {profile}`: the sim drives `crew_buggy` by default (matches the viewer); `rover_scout` for Perseverance-like scouting. Rover state includes `vehicle`.
- Markers and routes persist across API restarts (per site). `/health` reports vehicle, viewers, marker and route counts.

### Known issues / notes

- Caden's fixture: the crater wall and the scarp are ~30° all round, so the crater floor and the
  east upland are unreachable on foot (25° limit). Correct behaviour, but the demo's "detour around
  the scarp" story needs a gap; real Jezero data or a fixture tweak (ask Caden) will provide one.
- Mineral layers are synthetic (`api/scripts/make_dev_layers.py`), labelled "not Mars data" in every
  response. Need real CRISM-derived `layer_min_<name>.tif` (0–1) from Caden; naming not yet confirmed.
- Real Jezero build (`ARES_DATA_DIR=data/build`, built from Caden's pipeline) works end to end. Timings on the
  8 km grid: `/route` 3-7 km 0.4-0.7 s, `/search/sites` 0.15 s, `/route/science` 4 h 0.4-1.6 s, `/validation`
  0.5 s then cached (`api/tests/test_real_data.py`, skipped without `data/build`). No real mineral/geology
  layers or science-valued POIs yet, so search and science traverse have nothing real to work on until Stream A
  ships them; IAU names inside the site (Belva) load as landmarks from `names.geojson`.
- Browser manual driving uses the force model; the API auto/FSD sim still uses the planner speed curve and
  stops at the no-go slope (that is the safety policy). Vehicle physics params live in `api/profiles.yaml`.

## Sameer — MCP, agent, chat

Not started in this repo yet. Build against `contracts/fixtures/api/*.json` or the live API on :8000.
