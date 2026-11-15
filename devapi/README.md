# devapi: dev REST + WebSocket API

A stand-in for `api/` (Yousef) and a reference implementation of every endpoint in
`contracts/mcp-api-needs.md`, built on `atlas_core` (the tested query engine). The MCP server talks to
it over HTTP only, so swapping in the real API is `ATLAS_API_URL=...`. Adopt, copy or replace any part.

```bash
.venv/bin/python -m devapi --port 8000 [--host 127.0.0.1]
```

To run next to Yousef's `api/` (same site, same frame): `.venv/bin/python -m devapi.export_ares_dir <site dir> <out dir>`
writes his `ARES_DATA_DIR` layout from one of our site dirs; see `mcp/README.md` and `docs/api-compat.md`.

Site: `ATLAS_SITE_DIR=<site dir>` (real site, optional `traverse.geojson` for `/validation`), otherwise the
synthetic test site (`atlas_core.testing.make_site`; `/health` and `/site` say `synthetic: true`).
Interactive docs: `http://localhost:8000/docs`. State is in memory (markers, routes, location sets, rover).

## Endpoints

| Endpoint | Notes |
|---|---|
| `GET /health`, `GET /site` | `/site`: manifest, profiles, resolved limits (flat speed, no-go and caution slope, EVA limit, reserve; defaults labelled assumed) |
| `GET /layers`, `GET /layers/{id}`, `GET /overview` | overview adds rover, markers, search targets |
| `POST /describe`, `POST /search/locations`, `POST /search/regions`, `POST /search/nearest`, `GET /sets/{id}` | bodies are the `atlas_core` kwargs (`nearest` uses `from`, `to`) |
| `GET /terrain/sample?x=&y=&profile=`, `POST /terrain/profile`, `GET /pois?type=&q=&near=&radius=&limit=` | `near` = `x,y` or any location ref; `science_value` is null where not recorded |
| `POST /route`, `GET /route/{id}`, `POST /route/science` | see below |
| `GET /rover/state`, `POST /rover/command`, `GET /fsd/status` | see below |
| `POST /markers`, `GET /markers`, `DELETE /markers/{id}` | `marker:<id>` works as a location everywhere (`atlas_core.refs`) |
| `POST /search/sites`, `GET /search/targets` | `find_regions` on evidence layers / categorical classes; `best_tier` + `best_tier_name` instead of a 0-1 score |
| `POST /view`, `POST /view/snapshot` | snapshot: 504 `{"error","hint"}` when no viewer or no reply in 5 s |
| `POST /events/agent`, `GET /validation`, `WS /ws` | `/validation` returns `available: false` without a traverse |

Errors: `AtlasError` and request validation become HTTP 422 `{"error", "hint"}`.

**Routes.** `POST /route {start?="rover", goal, via?, profile?="rover_scout", max_slope_deg?, time_budget_min?}`
returns the PRD route response (`points[{x,y,z}]` at <= 5 m, `distance_m` horizontal, `duration_min`, `ascent_m`,
`max_slope_deg`, `hazard_cells`, `consumables{used{minutes,fraction}, margin{minutes,fraction}, eva_limit_min,
reserve_fraction}`, `stops[]`, `warnings[]`, `feasible`) plus `assessment{go, reasons, caution}`,
`start`, `goal`, `assumptions`. Cost and planning grid are `atlas_core.travel`'s (4 m grid, placeholder
isotropic Tobler speed). Blocked goal or via -> `feasible: false`, `reason`, `nearest_reachable`, `hint`,
`route_id: null`. Feasible routes are stored and broadcast as `route.set`. `/route/science` is greedy insertion
over the 20 best POIs; missing `science_value` counts as 0.5 and is flagged `value_assumed`.

**Rover.** `POST /rover/command {cmd: drive|turn|goto|follow_route|stop|teleport|fsd_enable|fsd_disable, ...}`.
Blocking commands return `{state, stop_reason, detail, distance_m, elapsed_sim_min, route_id}`; stop reasons:
`arrived, blocked_slope, blocked_hazard, replanned, stopped_by_user, timeout`. By default motion is computed in
1 m steps with no waiting; `realtime: true` runs at 10 Hz with `speed_factor` (default shows ~8 m/s) and
`timeout_s`. Steps stop at slopes above the profile no-go limit (`blocked_slope`) or unknown ground
(`blocked_hazard`). With FSD armed, caution slopes halve speed and a blocked `follow_route`/`goto` is replanned once
with a 10% lower slope limit: the rover stops with `replanned` and the new `route_id` (call `follow_route`
again). `follow_route` requires the rover within 3 m of the route start. Pose: terrain-following from four
wheel samples; pitch up and roll right-side-up are positive. Viewer `rover.pose` switches to manual and cancels
the active command.

## WebSocket `/ws`

Envelope: `{"type", "ts", "payload"}` (the contract does not fix an envelope; this is ours). Server to viewer:
`rover.state`, `route.set`, `rover.hazard`, `view.camera`, `view.layer`, `view.highlight`,
`view.snapshot_request` `{request_id, direction}`, `agent.event`, `marker.add`, `marker.remove`, `search.results`.
Viewer to server: `rover.pose`, `view.snapshot` `{request_id, image_base64}`, `marker.click` `{x, y}`, `ping`.
Incoming messages may carry the payload under `payload` or flat.

## Beyond the contract list

`GET /route/{id}` (assess_route needs it), `assessment` on every route, `rover.hazard` event, `walk_distance_m`
on sites. Tests: `tests/test_devapi.py`.
