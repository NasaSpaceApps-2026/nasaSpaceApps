# API compatibility: our MCP server vs Yousef's `api/` (ares_api)

Nov 14, 2026 · Sameer · basis: `api/ares_api/*.py` and `contracts/api.openapi.yaml` as merged on `sameer/mcp`
(the code was read, the OpenAPI file spot-checked; the two agree on every path used here) and the MCP/devapi code in
`mcp/`, `devapi/`. Live results: [`rover-connection-test.md`](rover-connection-test.md).

## How the MCP server talks to both

| Env | Backend | Owns |
|---|---|---|
| `ATLAS_ROVER_API_URL` | Yousef's `ares_api` | rover state/commands, FSD, routes, science traverse, markers, view, `find_sites`, POIs, terrain sample/profile, agent events, WS |
| `ATLAS_API_URL` | our `devapi` (query engine `atlas_core`) | overview, layers, describe, `find_locations/regions`, `nearest`, sets, visibility, sun, lake, EVA, health |

`ATLAS_ROVER_API_URL` unset: everything goes to `ATLAS_API_URL` (the old behaviour, tool output identical).
Both set: `mcp/backends.py` routes each call (`backend_for`) and translates request and response shapes, so every
tool prints the same format as before. His original fields are kept next to the translated ones in
`structuredContent`. Status legend below: **OK** compatible as is, **ADAPT** the adapter in `mcp/backends.py`
translates it, **MISSING** his API has no equivalent (the tool returns a readable note or error naming the backend),
**CHANGE** a difference in behaviour that cannot be hidden.

## Conventions

| Topic | Ours (devapi) | His (ares_api) | Status |
|---|---|---|---|
| Site frame | metres, x east, y north, origin = site centre (lat0, lon0), headings clockwise from north | same (PRD Contract 1) | OK, **if both load the same site** (see frame check) |
| lat/lon from x,y | `lat0 + deg(y/R)`, `lon0 + deg(x/(R cos lat0))`, R from the manifest (3 396 190 m) | identical formulas | OK |
| Position input | `{x,y}`, `{lat,lon}`, `"poi:<id>"`, `"marker:<id>"`, `"base"`, `"rover"`, `"here"/"me"`, `"set:3"`, `"3.2"`, lists | `{x,y}`, `[x,y]`, `"x,y"`, `{lat,lon}`, `"poi:<id>"`, `"marker:<id>"`, `"rover"`, bare POI/marker id | ADAPT: `here`/`me` become `rover`; set refs and find_sites ids are resolved to `{x,y}` before the call (sets via our `/describe`, site ids from a per-session cache). A set that has several members is refused where one location is needed |
| Position output | `x,y,z` + `lat,lon` (2 dp m) | `x,y` (some endpoints round to 1 m: `/terrain/sample`, search sites), `z`, `lat,lon` | OK |
| Error body | HTTP 422 `{"error", "hint"}` | HTTP 422 `{"error"}` (no hint); FastAPI body-validation failures are `{"detail":[{loc,msg}]}` | ADAPT: `hint` becomes `null`, `detail` becomes `{"error": "Invalid request to the rover backend: points: ..."}` |
| Infeasible route | HTTP 200 `feasible:false, reason, nearest_reachable{x,y,distance_to_goal_m,travel_min}` | HTTP 422 `{"error": "No route from A to B ... Nearest reachable point is (x, y), D m from the goal ..."}` | ADAPT: the sentence is parsed into `feasible:false` + `nearest_reachable` (`travel_min` is null). Other 422s (unknown place) stay errors |
| Profiles | `rover_scout`, `eva_walkable` | `rover_scout`, `astronaut_walk`, `crew_buggy`, `crew_rover` | ADAPT: `eva_walkable` -> `astronaut_walk` (limits agree: no-go 25, caution 15). Results show his profile name |
| Route / marker ids | `route-N`, `m<N>` | `r<N>`, `m<N>`; sites `site<N>` | OK (ids are opaque; the tool text prints whatever the backend returned) |
| Health | `{ok, synthetic, site, viewers}` | `{ok, site, fixture}` | ADAPT: `fixture:true` also sets our SYNTHETIC banner |
| WS envelope | `{"type", "ts", "payload": {...}}` | flat `{"type", ...payload}` (`rover.state` is the rover dict plus `type`) | OK for the browser (`web/src/rover/link.ts` `parseRoverState` accepts flat and wrapped). The MCP server does not use the WS |
| Agent event | `POST /events/agent {tool, args_summary, result_summary, status: started/done/error, is_error}` | `POST /agent/event {tool, args_summary, result_summary, status: started/ok/error}` (other `status` values 422) | ADAPT: path and `done` -> `ok`; `is_error` folded into `status:"error"` |

## Endpoint by endpoint

| MCP tool | We call | Backend in split mode | His endpoint | Status / notes |
|---|---|---|---|---|
| `get_site_overview` | `GET /overview` | ours, then overlaid | none (his `GET /site` + `/pois` + `/markers` + `/search/targets` + `/rover/state` hold the parts) | ADAPT: layers, profiles, POIs, tiers from ours; **rover pose/mode, markers, search targets from his** (targets because `find_sites` runs on his). If his API is down the text says the rover/marker block is the stand-in's |
| `list_layers`, `describe_layer`, `describe_location`, `find_locations`, `find_regions`, `nearest`, `get_set`, visibility, sun, lake, EVA tools | `/layers`, `/describe`, `/search/*`, `/sets`, ... | ours | none | OK. `"rover"`, `"here"`, `"marker:<id>"` and find_sites ids inside their arguments are replaced by coordinates from his API (ours does not know his rover/markers) |
| `get_rover_state` | `GET /rover/state` | his (+ `GET /fsd/status` for `remaining_m`) | `GET /rover/state` | ADAPT. His fields kept. Missing in his: `profile` (filled from `/site.rover_profile`), `sim_elapsed_s` (filled from `mission_time_min * 60`, his is cumulative mission time), `remaining_m` (from `/fsd/status.remaining_route_m`). Extra in his: `slope_deg`, `lat`, `lon` |
| `drive_rover`, `goto_location`, `follow_route`, `teleport_rover`, `stop_rover` | `POST /rover/command {cmd,...}` | his | `POST /rover/command` | ADAPT, see command table |
| `fsd_enable`, `fsd_disable` | `POST /rover/command` | his | same | ADAPT, see FSD table |
| `fsd_status` | `GET /fsd/status` | his | `GET /fsd/status` | ADAPT, see FSD table |
| `plan_route` | `POST /route` | his | `POST /route` | ADAPT, see route table. Needs a second call (`GET /route/{id}/assess`) for GO/NO-GO |
| `assess_route` | `GET /route/{id}` (full route + assessment) | his | `GET /route/{id}` (route only) and `GET /route/{id}/assess` | ADAPT: both fetched and merged |
| `plan_science_traverse` | `POST /route/science` | his | `POST /route/science` | ADAPT, see science table |
| `list_markers` | `GET /markers` -> `{count, markers}` | his | `GET /markers` -> `{markers}` | ADAPT: `count` added. Marker fields (`id,x,y,z,lat,lon,label,created_by`) are the same, his adds `created_at` |
| `place_marker` | `POST /markers {x,y,at,label,created_by:"agent"}` | his | `POST /markers {x,y,at,label,created_by}` | OK (`at` that is a set member is resolved to x,y first). `DELETE /markers/{id}`: no MCP tool uses it |
| `set_view`, `fly_camera` | `POST /view` | his | `POST /view` | ADAPT, see view table |
| `find_sites` | `POST /search/sites` | his | `POST /search/sites`, `GET /search/targets` | ADAPT + CHANGE, see sites table |
| `search_pois` | `GET /pois?q,type,near,radius,limit` | his | `GET /pois` | ADAPT: adds `ref` (`poi:<id>`), `types`, `known_ids` (a second call on an empty result). CHANGE: his `q` needs every word to match (ours is a substring), `limit` max 200 vs 100; his POI list has no `in_situ_minerals`/`region` unless the geojson carries them (same file, so same fields) |
| `sample_terrain` | `GET /terrain/sample?x,y` | his | `GET /terrain/sample` | ADAPT: reshaped to `describe_location` form. His gives elevation, slope, roughness, `hazard`, `filled`, his layers (unknown = null); no quality classes, no nearest ground truth (text says so); `x,y` rounded to 1 m. `profile=rover_scout` is passed so the hazard class is the rover's, not his default `astronaut_walk` |
| `get_elevation_profile` | `POST /terrain/profile` | his | `POST /terrain/profile` | ADAPT: `series[{d,x,y,z,slope}]` -> `samples[{d,x,y,z,slope_deg}]`; `descent_m`, `mean_slope_deg` computed from the series; `unknown_samples` is 0 (his cannot report unknown). His default spacing is 10 m (ours chosen by length) |
| (none) | `POST /view/snapshot`, `GET /validation`, `GET /bubble/status`, `GET /fsd/status` | his | present | no MCP tool yet |

### `POST /rover/command`

| Item | Ours | His | Status |
|---|---|---|---|
| Command names | `drive, turn, goto, follow_route, stop, teleport, fsd_enable, fsd_disable` | same eight | OK |
| Target | `target` or top-level `x`,`y` | `target` only (extra fields ignored: `x`,`y` would be dropped silently) | ADAPT: `x,y` -> `target:{x,y}` |
| Real time vs instant | `realtime:false` computes the drive instantly; `true` plays at 10 Hz | always plays at 10 Hz in real time, `speed_factor` (default `display_speed_factor` of the profile, 200 for the scout) | ADAPT/CHANGE: `animate=false` is approximated with `speed_factor = 400 m/s / flat_speed` (the sim then finishes in a few seconds) and the result note says so; `animate=true` uses the profile default |
| `wait`, `timeout_s` | blocks, `timeout_s` 120 | blocks (`wait:true`), `timeout_s` default 120, max 600, returns `stop_reason:"timeout"` while the drive continues | OK (we send 280) |
| Result | `{state, stop_reason, detail, distance_m, elapsed_sim_min, route_id, new_route?, replan_failed?, hint?, note?}` | `{ok, cmd, stop_reason, message, distance_m, replans, events[], route_id, state, fsd}` | ADAPT: `message` -> `detail`; `events`/`replans`/`warnings` -> `note`; blocked reasons get the hint. `elapsed_sim_min` MISSING (no per-command sim time; the state's `mission_time_min` is cumulative) |
| Stop reasons | `arrived, blocked_slope, blocked_hazard, replanned, stopped_by_user, timeout` | `arrived, blocked_slope, blocked_hazard, out_of_bounds, no_route, stopped_by_user, timeout` (no `replanned`) | ADAPT: his two extra reasons are printed as is. FSD replans inside the command and keeps driving (`replans`, `events`); there is no stop at a replan, so `follow_route` need not be called again |
| `follow_route` without `route_id` | defaults to the most recently planned route | `route_id` required | ADAPT: the MCP session remembers the last route (from `plan_route`/science) and says so in a note; with none it errors readably |
| `follow_route` far from the start | error naming the teleport/goto call | auto-plans an approach leg if the rover is more than 10 m away | OK (better) |
| `goto` unreachable | 200 `feasible:false` + nearest point | 422 `{"error": ...}` with the nearest reachable point in the sentence | OK as a readable tool error (not converted) |
| `teleport` onto steep/no-data ground | refused, names the nearest walkable point | accepted with a `warnings` entry; only out-of-site is refused | CHANGE (his is more permissive; the warning is shown as a note) |
| `drive` | `distance_m`, `heading_deg` | same | OK |

### FSD

| Item | Ours | His | Status |
|---|---|---|---|
| Policy keys | `replan_on_block`, `caution_slowdown` (booleans) | `max_slope_deg`, `caution_slope_deg`, `caution_speed_factor` (0.5), `replan`, `max_replans` | ADAPT: booleans map to `replan` and `caution_speed_factor` (0.5 / 1.0); his own keys also accepted; an unknown key is an error, never dropped |
| Status | `replans_used`, `replans_allowed`, `blockers[]`, `last_hazard.detail` | `policy`, `route_id`, `active_command`, `remaining_route_m`, `last_hazard{x,y,slope_deg,reason}`, `last_event`, `status`, `limits_source` | ADAPT: `blockers` and `detail` derived. `replans_used` MISSING (his only reports `replans` in a drive result; the tool prints "unknown") |
| Replan slope limit | 10% below the limit | replans with the policy limit (`max_slope_deg`, default = no-go) | CHANGE (his replan often fails on the same slope: seen live) |

### `POST /route` and `GET /route/{id}`

| Field | Ours | His | Status |
|---|---|---|---|
| Request | `start?, goal, via?, profile?, max_slope_deg?, time_budget_min?` | same | OK |
| `points` | `[{x,y,z}]` ~5 m | `[{x,y,z,slope}]` ~5 m | OK (extra `slope` kept) |
| `distance_m`, `duration_min`, `ascent_m`, `max_slope_deg`, `hazard_cells`, `warnings` | yes | yes (+ `descent_m`, `stop_minutes`, `legs`, `direct_line`, `sources`) | OK |
| `consumables` | `{eva_limit_min, reserve_fraction, used{minutes,fraction}, margin{minutes,fraction}, stops_min}` | `{used_frac, reserve_frac, margin_frac, eva_limit_min, basis}` | ADAPT (minutes = fraction x EVA limit) |
| `stops` | `[{kind,x,y,distance_m,eta_min}]` | `stops` = labels of via points; positions only through `legs` | ADAPT: positions found along `points` at the cumulative leg length |
| `assessment{go,reasons,caution}` | in the route | only in `GET /route/{id}/assess` (`go, verdict, reasons`) | ADAPT: second call; `caution` taken from his warnings |
| `start`, `goal` | point objects | label strings | ADAPT (position from the first/last point) |
| `max_slope_limit_deg`, `caution_slope_deg` | yes | `slope_limit_deg`; caution only in `/site.profiles` | ADAPT |
| `assumptions` | list | `sources.speeds` text ("assumed") | ADAPT |

### `POST /route/science`

| Item | Ours | His | Status |
|---|---|---|---|
| Parameters | `time_budget_min, start, interests, profile, max_slope_deg, stop_minutes, return_to_start, max_stops` | `start, time_budget_min, interests, profile, max_stops` (1..12) | `max_slope_deg`, `stop_minutes` (fixed 15 min) and `return_to_start:false` (always returns) are MISSING: a note says so, they are never silently dropped |
| Candidates | top 20 POIs; missing `science_value` counts 0.5 and is flagged `value_assumed` | POIs **with** a recorded `science_value` only (types science_target, sample_site, landmark) plus mapped sites for interests that name a target; max 8 | CHANGE: on the real Jezero POIs (none has a `science_value`) his API returns `feasible:false, "No science targets match"` |
| Result | `stops[], route, time{...}, skipped[{id,reason}], notes` | the route dict plus `science_stops[{order,label,ref,x,y,science_value,source}]`, `science_value_total`, `skipped[labels]` | ADAPT: `arrive_min` from `legs`; `skipped` reasons MISSING (generic text); `candidates_considered` is a lower bound |

### `POST /view`

| Item | Ours | His | Status |
|---|---|---|---|
| `mode`, `target`, `distance_m`, `heading_deg`, `pitch_deg` | yes | yes | OK |
| `layers` | `{id: bool}` or `[ids]`, validated against our layer ids | `[{id, visible, opacity?}]`, not validated | ADAPT; CHANGE: his accepts any id (the viewer's tile layers `ortho/slope/hazard` are not our `carbonate` etc.), so a wrong id is not reported |
| `highlights` | refs incl. sets (expanded to <= 50 points) | list of id strings, forwarded as `view.highlight {ids}` | ADAPT: set refs expanded to member ids; raw `{x,y}` is refused with a readable error (his cannot show a coordinate) |
| Response | `{ok, mode, target{id,x,y}, layers[{layer,visible}], highlights[{id}], viewers, note}` | `{ok, camera{mode,x,y,z,target}, highlights[ids], layers[ids], viewers_connected}` | ADAPT; the "no viewer connected" note is generated |

### `POST /search/sites` (`find_sites`)

| Item | Ours | His | Status |
|---|---|---|---|
| Targets | evidence layers (minerals) and geologic classes, aliases (`clay` = three layers) | `targets.yaml`: carbonate, olivine, clay, delta, crater_floor ... (needs `layer_min_<name>.tif` 0-1 abundance or class raster), aliases | CHANGE: valid target names differ per backend; `find_sites` description uses his list |
| Ranking and evidence | ordinal tiers (`best_tier`, `best_tier_name`, `area_by_tier_m2`, in-situ samples); SCIENCE_VALIDITY: detections, not abundance | `score` 0-1 (mean value x area, slope penalty), `evidence[{layer,mission,instrument,data,mean_value,peak_value,threshold}]` | CHANGE: output says "score 0.71 (0-1 index, not abundance)"; `best_tier` is null. **Yousef should rank by tier** (proposal in `contracts/mcp-api-needs.md`) |
| `min_tier` | yes | MISSING | note in the result; not applied |
| Sets | stored as `set:N` (usable by `nearest`, `get_set`, highlights) | not stored; ids `site<N>` | CHANGE: the MCP session caches the site ids so `plan_route(goal="site4")`, `describe_location(at="site4")` work; they do not work in a new MCP session or for `nearest`/`get_set` |
| `reachable` | walk reachability on our grid | planner reachability from `near` (`null` without `near`; the adapter always sends `near`, default `rover`) | OK |
| `near` in the result | `{id,x,y}` | label string | ADAPT (x,y from his `/terrain/sample?at=`) |

## Site and frame check

Two things must be identical or coordinates from one backend send the rover to the wrong place: origin
(`lat0`, `lon0`, radius) and, for sanity, extent (`size_m`).

| Source | Site | lat0, lon0 | extent | Notes |
|---|---|---|---|---|
| Our real site `data/build/site/jezero` (`site.yaml`) | jezero | 18.452, 77.418 | 8192 m (4096 px x 2 m) | `pipeline/site_cfg.py` SIZE = 8192 m |
| Our synthetic `atlas_core.testing.make_site` | synthetic | 18.46, 77.41 | 512 m | devapi default without `ATLAS_SITE_DIR` |
| His default `contracts/fixtures` | fixture | 18.46, 77.41 | 1024 m | `ARES_DATA_DIR` default; synthetic, "not Mars" |
| His `api/fixtures/playground` (generate with `make_playground.py`, not committed) | playground | 18.46, 77.41 | 2048 m | richer 2 km synthetic site |

Results (checked with `GET /site` on both, `mcp/backends.compare_sites`):

* **Real Jezero (ours) vs any of his fixtures: MISMATCH.** Origins are 654 m apart (dx -450, dy +474); the same x,y is
  a different place. The MCP server prints `[FRAME MISMATCH: ...]` as the first line of every tool result and in
  `structuredContent.frame_warning` while this holds, and `mcp/server.py` prints a banner on stderr at start.
* **Our synthetic vs his fixture/playground: same origin, different extent** (512 m vs 1024/2048 m): coordinates agree
  where both have data; the MCP prints `[EXTENT DIFFERS: ...]`. Fine for demos, not a mismatch.
* **His API cannot load `data/build/site/jezero` at all.** `ARES_DATA_DIR` must hold `manifest.json`,
  `analysis/{dem,slope,rough}.tif` (+ optional `filled.tif`, `layer_*.tif`) and `data/pois.geojson`; our site dir has
  `site.yaml` and `<layer>.tif`. `data/build` has no such layout today, so "real data" for his API does not exist yet.
* **Fix used for the live test:** `devapi/export_ares_dir.py` writes his layout from our site dir (symlinks for the
  big rasters; same grid, lat0/lon0, size). Both backends then agree to the metre and the check passes silently.
  `layer_min_<target>` there is `tier / 4` of our evidence layers (a **test mapping**: tiers are detections, not
  abundance), so his `find_sites` scores on real data are tier indices, not abundances.

The check runs (a) at MCP start (`main()`, stderr, non-fatal if a backend is not up yet), (b) lazily before the first
tool result (re-tried every call until both answer), (c) inside `get_site_overview`. Tolerance: 1 m of origin
offset; radius difference > 1 m also counts.

## Not supported by his API (Yousef: please add or tell us the alternative)

1. A synchronous "compute the drive now" mode (`realtime:false`), and per-command `elapsed_sim_min`.
2. `follow_route` default to the last planned route; `replans_used` in `/fsd/status`.
3. Route response: `assessment`, `stops[{x,y,eta}]`, structured `nearest_reachable` (HTTP 200 `feasible:false` instead of
   a 422 sentence), `caution_slope_deg`.
4. Science traverse: `stop_minutes`, `max_slope_deg`, `return_to_start`, reasons for skipped candidates, a way to include
   POIs without `science_value` (the real Jezero POIs have none, so it finds nothing).
5. `find_sites` by evidence tier with `min_tier`, `best_tier`, `area_by_tier_m2`; stored sets.
6. A data dir the real Jezero build can use (manifest + `analysis/` from `data/build/site/jezero`), and an
   `agent/event` that accepts `done` (or document `ok`).
7. `/view`: validate layer ids; highlights by coordinates; and `/health.synthetic` for symmetry.
8. Custom handler for FastAPI `RequestValidationError` so bodies are `{"error"}` like everything else.

### Integration bug found while testing the browser (`web/src/rover/`, Yousef)

With the rover sandbox open and connected to the API, **every API-driven command is cancelled almost at once**: the
browser, in manual mode, sends `rover.pose` at 5 Hz even when it is not moving; `RoverSim.manual_pose` treats each
pose as a takeover (`stopped_by_user`, "Manual takeover from the viewer") and sets the pose back. Reproduced 5/5 with
`goto_location(animate=true)` and `follow_route` (the rover moved 6-10 m, then the browser pose snapped back).
The same drives run to `arrived` when no browser is connected. Suggested fixes: the browser should only send poses
while there is input (or the pose changed), or the server should ignore a pose equal to its own state when a command
is running, and `RoverSim.start` should broadcast `rover.state` (mode `auto`) immediately instead of on the next tick.
`teleport_rover` has the same problem when the teleport is shorter than `teleportThresholdM`.
