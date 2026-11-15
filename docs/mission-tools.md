# Mid-mission astronaut tools

Twelve MCP tools for the astronaut who is already outside on an EVA. They answer first-person field questions ("how much time do
I have left", "get me back", "am I in radio contact", "log this rock", "what is ahead if I keep going north", "where do I meet
the rover", "when do I lose the light") from the existing engines. Code: `atlas_core/mission.py` (pure functions),
`devapi/routes_mission.py` (`/mission/*`, the EVA state and sample log), `mcp/tools_mission.py` (plain-language results).
Tests: `tests/test_mission.py`. Live eval: `agent/evals/mission_questions.yaml`, results in `docs/eval-run-mission.md`.

## Model

- **Crew position** = the rover position of the stand-in world. `"me"` and `"here"` mean the crew in every place argument.
  In the mission tools `"the rover"` means the vehicle left where the EVA began (not the crew); `"base"`, POI ids, markers, place
  names ("Ha'ahoni", "the delta") and `{x, y}` work as everywhere else.
- **EVA state** is held in devapi (`app.state.eva`): start time, base, budget (default 480 min), reserve (default 20 %, from the
  `eva_walkable` profile), havens (base + the parked rover + optional shelters), `already_out_min`.
- **Budget rule** (same as `atlas_core.envelope`): `usable = budget x (1 - reserve)`, `margin = usable - elapsed - walk back to the
  nearest haven`. `RETURN NOW` at margin <= 0 (or no walkable path), `CAUTION` under 30 min (assumed threshold), else `GO`.
  Every minute walked farther out costs two (out and back), so the tool also says how far out you can go before you must turn back.
- **Mission clock**: simulated UTC. Starts at `eva_start` (real time, or an explicit `now`), runs 1 s per real second, and can be
  moved with `POST /mission/advance {minutes}` (tests, demos, evals). Walking is not clocked automatically. Every endpoint takes an
  explicit `now` (UTC ISO string, or minutes since the EVA started) that overrides the clock; the unit tests use it.
- **Walking** uses profile `eva_walkable` through `atlas_core.travel` / `route.plan_route` (4 m planning grid, placeholder
  isotropic Tobler-shaped speed, slope limit and unknown ground respected). Times are approximate and labelled as such.
- **Sun and shadow** are geometric (Mars24 sun on the 2 m DEM, ray-cast terrain shadow). No dust twilight (Mars stays dimly lit
  roughly 2 h after sunset), no boulders under the 2 m grid. "Shaded" means no direct sun.

## Tools

| Tool | Endpoint | Returns |
|---|---|---|
| `eva_start(base?, budget_min?, reserve?, crew_at?, havens?, already_out_min?)` | `POST /mission/eva/start` | budget, usable minutes, havens, start time; `crew_at` moves the crew |
| `eva_end()` | `POST /mission/eva/end` | elapsed vs budget, at a haven or not, samples logged |
| `eva_status()` | `POST /mission/status` | GO / CAUTION / RETURN NOW, elapsed and left, walk back and margin, nearest named place, minutes to sunset and to shade |
| `return_route(to?)` | `POST /mission/return_route` | fastest safe walking route to the nearest haven (or `to`): time, distance, steepest slope, legs (heading, distance, turn), point-of-no-return against the budget |
| `nearest_haven()` | `POST /mission/havens` | havens ranked by walking time, with distance and direction |
| `check_path_ahead(heading_deg? \| toward?, distance_m=200)` | `POST /mission/path_ahead` | slope, roughness, unknown ground and shadow along the line, first hazard and first blocker with distances, walk time, margin at the end and point of no return |
| `comm_check(to="base", antenna_h=1.7)` | `POST /mission/comm_check` | line of sight; if blocked, the blocking point and the nearest spot within 300 m of walking that restores it (direction, distance, time) |
| `log_sample(note, at?)` / `list_samples()` | `POST /mission/sample`, `GET /mission/samples` | numbered marker with time, position, map unit and mineral evidence tiers (orbital; never presented as in-situ) |
| `bearing_to(target)` | `POST /mission/bearing` | heading (degrees + compass point), straight distance, walking time and distance |
| `rendezvous(rover?)` | `POST /mission/rendezvous` | the cell minimising max(crew walk time, rover drive time) over every reachable cell; alternatives "walk to the rover" and "rover comes to you" |
| `daylight_check(at?)` | `POST /mission/daylight` | sun elevation, minutes to sunset, minutes until the spot is shaded (terrain or sunset), minutes to sunrise at night |

Without `eva_start`, `return_route`, `nearest_haven`, `check_path_ahead`, `comm_check`, `log_sample`, `bearing_to`, `rendezvous` and
`daylight_check` still work (the base is the only haven, no budget check); `eva_status` returns an error that says to start one.
Errors use the usual `Error: ... / Try: ...` format. Tool results carry the full numbers in `structuredContent`.

## Assumptions to keep in mind

- Thresholds that are ours, not cited: CAUTION under 30 min of margin; "rough" = 5x5 roughness above 0.5 m; comm search radius
  300 m; haven merge distance 30 m. They are printed or listed in `assumptions`.
- The rover that comes to the rendezvous drives as the active crewed vehicle profile (`crew_rover`, else `crew_buggy`; speed from
  its profile) on its own slope limits; the scout (0.04 m/s) is never used.
- `comm_check` is geometry only (no Fresnel zone, link budget or frequency). Oxygen and power are not modelled: the budget is time.
- In split mode (Yousef's API next to devapi) the crew position is the rover position of the rover API, but the EVA state lives in
  devapi only.
