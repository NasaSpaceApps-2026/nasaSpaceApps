# Driving the crewed buggy from the copilot

Nov 15, 2026 · Sameer · basis: `api/ares_api/{vehicle,rover,planner,main}.py`, `api/profiles.yaml`,
`web/src/rover/link.ts` (read only) and `mcp/`, `devapi/` as of this branch. Live results at the end.

## 1. Audit: what Yousef's API supports

**Vehicles** are the profiles in `api/profiles.yaml` that the simulator can drive (`GET /rover/vehicles`):

| Profile | Title | Flat speed | Caution / no-go slope | Source label in the API |
|---|---|---|---|---|
| `crew_buggy` | crewed performance buggy (LRV-derived geometry) | 24 m/s (boost 40) | 15 / 25 deg | "assumed ... 25 deg slope rating and ~3 m turn radius from Apollo LRV (NASA)" |
| `rover_scout` | Perseverance-like scout | 0.04 m/s (display factor 200) | 15 / 20 deg | "flat speed close to Perseverance top speed; slopes assumed" |
| `crew_rover` | pressurised crew rover (concept) | 2.5 m/s | 10 / 15 deg | assumed |
| `astronaut_walk` | on foot | 0.9 m/s | 15 / 25 deg | assumed |

These are the API's numbers (not ours); the buggy's speed and grip are assumed concept values, only its slope rating
and geometry are LRV-derived. The buggy also has a force model (power, traction, wheelie limit in `vehicle.py`) that can
stall it on a climb below the no-go slope (stop reason `blocked_slope`, "Stalled ...").

**Selecting the vehicle.** `vehicle_profile: crew_buggy` in `profiles.yaml` is the default, so Yousef's sim already
drives the buggy at start-up. `POST /rover/vehicle {"profile": name}` switches it (cancels a running command, keeps the
pose); `GET /rover/vehicles` lists them; `GET /rover/state.vehicle` and `GET /site.vehicle_profile` report the active one.
`POST /route` and `/search/sites` take their own `profile` (default `astronaut_walk`, **not** the vehicle), and
`/rover/command` goto/follow_route use the ACTIVE vehicle's profile.

**Auto-drive / nav.** `POST /rover/command` `{cmd: goto | follow_route | drive | teleport | turn | stop | fsd_enable |
fsd_disable}`. `goto` plans (`planner.plan`, 4 m cost grid, slope no-go of the profile) from the rover to the target and
drives it; `follow_route {route_id}` drives a stored route (an approach leg is planned if the rover is more than 10 m
from the start); `teleport` is instant (accepts steep ground with a warning). The sim is real time at 10 Hz times
`speed_factor` and blocks until a stop reason: `arrived, blocked_slope, blocked_hazard, out_of_bounds, no_route,
stopped_by_user, timeout`. **FSD** (`fsd_enable`) adds caution slowdown, one in-place replan, and treats interpolated
(CTX-filled) terrain as a hazard (`blocked_hazard`); without FSD only the slope is checked.

**What our MCP exposed before this change.** `plan_route(profile)` (enum `rover_scout`, `eva_walkable`, **default
`rover_scout`**), `follow_route`, `goto_location`, `teleport_rover`, `fsd_*`, `find_locations(objective="highest")`
(query engine), `get_rover_state`.

## 2. Gaps found

| # | Gap | Effect |
|---|---|---|
| 1 | No tool to choose the vehicle; `/rover/vehicle` was not wired | the model could not ask for the buggy or the scout |
| 2 | `plan_route` always sent `rover_scout` (20 deg limit, 0.04 m/s) | a route "for the buggy" was planned and assessed as a scout: 33 h instead of minutes, wrong limit |
| 3 | `animate=false` speed factor came from the scout (`400 / 0.04 = 10000x`) whatever vehicle was active | wrong display speed for the buggy |
| 4 | `get_rover_state.profile` showed the scout profile name while the buggy drove | misleading |
| 5 | No "drive from A to B" with A different from the rover; no highest-point goal; no feasibility check tied to the vehicle's limit | several calls, easy to get wrong |
| 6 | The stand-in `devapi` only knew the scout and the walker | could not test or demo the buggy offline |

## 3. What was added (our side only)

* **`select_vehicle(vehicle?)`** (`mcp/tools_drive.py`): lists the vehicles with speed, caution and no-go slope and the
  API's source label; with an argument switches the simulator. "buggy", "the scout rover", "crew buggy" are accepted
  (`backends.map_vehicle`).
* **`drive_to(target?, vehicle?, start?, highest_within_m?, reposition?, max_slope_deg?, fsd?, animate?, speed_factor?)`**:
  select vehicle, plan with that vehicle's profile, check feasibility and the GO/NO-GO assessment, optionally place the
  rover at `start`, follow the route, return stop reason, distance driven, simulated minutes, planned max slope, route id.
  Infeasible or NO-GO means nothing moves and the reason plus the nearest reachable point come back as an answer.
* **`plan_route` without `profile`** now plans for the ACTIVE vehicle (devapi: its active profile; his API: the live
  `vehicle` of `/rover/state`); enum extended with `crew_buggy`, `crew_rover`. Existing calls that pass a profile are unchanged.
* Adapters (`mcp/backends.py`): `/rover/vehicle` GET/POST, vehicle aliases, speed factor from the active vehicle,
  `get_rover_state.profile` = active vehicle, `find_sites` reachability defaults to the active vehicle.
* **devapi mirror**: `GET/POST /rover/vehicle`; `crew_buggy` and `crew_rover` are added to the stand-in's site profiles
  from `api/profiles.yaml` when the site lacks them (`devapi/vehicles.py`; values and source strings copied, prefixed
  "copied from api/profiles.yaml"); `POST /route` without a profile uses the rover's profile.

### Decisions

* **"Drive from A to B" with A not at the rover:** the route is planned from A first (so an infeasible trip never moves
  the rover), then the rover is **teleported to A** (instant, no simulated time, odometer unchanged, said in the
  result) and driven A to B. The alternative, `reposition="drive"`, drives rover to A first (its time and distance are
  reported separately); `"none"` leaves it to the backend's approach leg. Teleport is the default because the user asked
  about A to B, not about the commute to A.
* **"Top of the mountain/hill/rim":** `drive_to(highest_within_m=R, target=<centre>)`. The centre is a named feature's
  POI (this site has e.g. `poi:sample_21_pilot_mountain`, `poi:sample_20_otis_peak`; there is no generic "mountain"
  POI, the model should use `search_pois`), a marker, or the start/rover when omitted. Method: `find_locations`
  (objective `highest`, footprint 0, `within` straight-line R) twice: unconstrained for the TRUE summit of the radius, and
  with `slope_2m <= <vehicle limit>` for candidates, spaced `max(50 m, R/10)`. Up to 8 candidates are tried in descending
  elevation; the first for which the planner finds a route from the start wins. A candidate can pass the slope test and
  still be cut off, which is why the route is the test.
* **When the summit is not reachable** the result says so with both coordinates and elevations, whether its own slope is
  above the limit or steeper ground cuts it off, and how much higher it is; the drive goes to the reachable high point.
  When the chosen point is within 10% of the radius edge, a note says higher ground may lie outside.
* **Limits are never typed in our code.** They come from `GET /rover/vehicle` (his `profiles.yaml`, or the stand-in's
  copy), printed with the API's source label ("assumed" or the citation). `max_slope_deg` can only tighten the limit.
* **FSD is off by default in `drive_to`** (`fsd=true` arms it for the drive and disarms after). Reason, found live: his
  FSD stops with `blocked_hazard` at the first interpolated cell, and his planner does not avoid or warn about such cells
  (base to Delta Front: stopped after 148 m, replan hit it again). Without FSD the same route arrives. This matches
  `goto_location`, which also runs without FSD.

## 4. Live results (real Jezero, Yousef's API on :8000, our devapi on :8001)

All through the MCP HTTP server; the vehicle and position were confirmed with `GET :8000/rover/state` before and after.

| Call | Vehicle | Result |
|---|---|---|
| `drive_to(target="poi:region_delta_front", start="base", vehicle="buggy")` | crew_buggy | arrived, 2939 m, 2.2 min planned (2 min driven), ascent 58 m, max slope 13.8 deg; state (-832.6, 169.6), odometer +2939 m |
| `drive_to(vehicle="buggy", highest_within_m=1000, start="base")` | crew_buggy | arrived at (1177, -291), z -2545 m (true highest cell of the radius, within the 25 deg limit), 733 m, max slope 11.3 deg |
| `drive_to(target="poi:sample_21_pilot_mountain", highest_within_m=300, vehicle="buggy", start="base")` | crew_buggy | arrived at (-3747, 2161), z -2403 m, 6.86 km in 4.8 min, ascent 229 m, max slope 21 deg; note: top is at the radius edge |
| `drive_to(target=(1860,-410), start="base", vehicle="scout")` | rover_scout | arrived, 28 m, 12 min (state: vehicle `rover_scout`, mission time +12 min) |
| `drive_to(target=(1900,-380), vehicle="scout")` from 4 km away | rover_scout | NOT DRIVEN: assessment NO-GO (33 h route, margin below reserve); rover did not move |
| `drive_to(target="poi:region_delta_front", vehicle="buggy", fsd=true)` (first run) | crew_buggy | `blocked_hazard` at (1694, -343) after 148 m, interpolated terrain; replan hit it again |

Stand-in (synthetic site, offline): the same tools, including an unreachable summit (slope 39 deg) that falls back to a
reachable high point 5.9 m lower, are covered by `tests/test_drive_to.py`.

DeepSeek agent (3 prompts, `stack/up.sh`): "Drive the buggy from base to the delta front" -> `search_pois`, `drive_to(vehicle="buggy", start=base)`;
"Drive the buggy to the highest point within 1 km" -> `drive_to(highest_within_m=1000, vehicle="buggy")` and the reply flagged the
summit being at the edge of the radius; "Switch to the scout rover and drive to marker X" -> `list_markers`, then said there is no
marker X (none was placed), correctly not driving. Screenshots: `docs/buggy_delta_front.png`, `docs/buggy_highest.png`
(copilot page, rover and trail on the minimap after each drive).

## 5. What Yousef could add on his side

1. FSD blocking on interpolated terrain with no warning from `/route` or `/route/{id}/assess`: have the planner flag
   (or avoid) filled cells, or let `fsd` policy choose whether filled terrain blocks.
2. `POST /route` default profile = the active vehicle (today `astronaut_walk`); `GET /rover/vehicles` could include
   `caution_slope_deg` and `eva_limit_min` (we read them from `/site`).
3. A per-command `elapsed_sim_min` and a synchronous "instant" mode (already requested in `docs/api-compat.md`).
4. A "highest point" query on his side is not needed; the query engine already does it.
