# Live eval run: DeepSeek (`deepseek-chat`)

Provider `deepseek`, model `deepseek-chat` (currently an alias served as `deepseek-flash`), thinking mode off.
Site: real Jezero (`data/build/site/jezero`) via devapi on :8000, MCP over stdio, 24 cases.
Recordings: `agent/replays/run1-full/` (full run), `agent/replays/run2-failing-rerun/` (rerun of the 9 failures).
Re-score without a key: `.venv/bin/python -m agent.evals.run_evals --replay agent/replays/run1-full`.

## Run 1 (all cases): 15/24 passed

| Case | Result | Tool path | Failure |
|---|---|---|---|
| demo_carbonates | PASS | get_site_overview, find_sites, set_view | |
| demo_route_two_markers | PASS | list_markers (no markers: asks user) | |
| demo_take_me_to_delta | FAIL | overview, search_pois, teleport_rover, fly_camera | numbers not in tool results: 2.7 km (model-computed distance), 250 m |
| demo_marswalk | FAIL | overview, plan_science_traverse, find_sites, describe_layer, plan_route x3, set_view | assess_route never called |
| demo_scout | FAIL | overview, get_rover_state, list_markers, fsd_status | no route: asked for destination instead of planning/arming FSD |
| demo_sample_near_me | FAIL | overview, search_pois, find_sites x2, set_view | number 43 m (a "+-43 m" uncertainty) not found |
| q01, q02, q05, q06, q07, q09, q10 | PASS | | |
| q03_good_landing_spots | FAIL | find_locations, describe_layer, describe_location, fly_camera, set_view | unexpected viewer tools (fly_camera, set_view) |
| q04_zero_slope_pad | FAIL | overview, describe_layer, find_locations | reply correct ("No zero-slope pad exists") but phrase check wants "no spot/none/could not..." |
| q08_carbonate_reachable_and_pad | FAIL | find_regions, find_locations, nearest | 0.36, 0.33 (km2 from m2), 0.047 |
| sun_local_time_now, sun_pad_shadow_1600, lake_*, eva_* (4) | PASS | | |
| los_radio_base_from_marker | FAIL | overview, list_markers | line_of_sight never called (no marker: asked user); 1.7 m assumed eye height |
| vantage_overlook_outcrop | FAIL | find_regions x3, find_vantage_points x2, plan_route, set_view | 0.63 (km2 conversion) |

Number-provenance check: 19/24 sessions clean. Violations: model-derived distance (2.7 km), m2->km2 conversion
(0.36/0.33/0.047/0.63), an uncertainty figure quoted from a result in different form (43 m), the model's own assumed 1.7 m.

## Run 2 (rerun of the 9 failures): 2/9 passed

PASS: q03_good_landing_spots (no stray viewer calls this time), q08 (numbers grounded this time).
Still FAIL: demo_take_me_to_delta (skipped search_pois, used a guessed poi id), demo_marswalk (never assess_route; used check_eva_return),
demo_scout (0 tool calls, asked for a route), demo_sample_near_me (43 m again), q04 (phrase check), los_radio (no line_of_sight, 1.7 m),
vantage_overlook_outcrop (now fails the reply-keyword check instead of numbers).

Systematic: demo_marswalk, demo_scout, demo_sample_near_me, q04, los_radio, demo_take_me_to_delta (different failure each time).
Flaky: q03, q08, vantage_overlook_outcrop.

## Causes

(a) Model behaviour / prompt: asks the user instead of acting when the precondition is absent (demo_scout, los_radio: expected tools are called
even with no route/marker); substitutes check_eva_return/plan_route for assess_route; teleports without search_pois; narrates "I'll start by..."
before tool calls; calls viewer tools unprompted (q03); mentions assumed values (1.7 m) not present in results.
(b) Tool descriptions / results: results give areas in m2 only, so models convert to km2 and the grounding check fails; uncertainty
(+-43 m) is not in an easily quotable form; no tool states distances between arbitrary POIs, so the model computes them.
(c) Data: none observed (real data answered all queries; q04 correctly reports no zero-slope pad).
Also scorer/eval-set issues: q04 phrase list misses "No ... exists"; unit conversions m2->km2 unsupported by the grounding check.

## Run 3 (after fixes)

Recordings: `agent/replays/run3-failing/`, `run3-failing-b/`, `run3-failing-c/` (targeted reruns of the Run 1/2 failures while iterating),
`run3-full/` (the one full run), `run3-rerun/` (4 cases rerun after a last prompt tweak). Same model, same site.

**Score: 19/24** on the full run, rescored with the final scorer (18/24 as scored live; `lake_landing_underwater` failed only because the
reply wrote `today's** topography` with markdown bold, fixed in the scorer, see below). Run 1 was 15/24. For reference, re-scoring the unchanged
Run 1 recordings with the final scorer and eval set gives 19/24, so part of the gain is scorer/eval correction, not the agent.

### What changed

1. Number provenance (tool results, `mcp/format.py`, `mcp/tools_visibility.py`): areas print as `430708 m2 (0.431 km2)`, distances as
   `2700 m (2.7 km)` (plain metres under 100 m), durations as `95.2 min (1.59 h)` (plain minutes under 30), in routes, assessments, consumables,
   traverses, sites, POIs, nearest, regions/spots, LOS, viewshed and vantage lines. Route consumables show the EVA limit in hours too.
   `fly_camera`/`set_view` results now echo `camera distance N m` (the model quoted its own argument). `fsd_status` reports the latest planned
   route (the `follow_route` default). Scoring: exact m<->km, m2<->km2, min<->h conversions count as grounded; `+-43 m` no longer parses as -43;
   a negative source value (`B is -4.4 m relative to A`) grounds its magnitude. Numbers that never appeared in any unit are still flagged (tests).
2. Prompt (`agent/system_prompt.md`, about 8 lines net): act instead of asking when ids are available; "scout" always means fsd_enable then
   follow_route; "take me to X" starts with search_pois; plan then assess_route before recommending a route (check_eva_return is point-of-no-return
   only); viewer tools only for "where/show me/take me", not after plain searches; offer only the relaxations a hint names. Tool descriptions:
   `set_view` no longer says "use after searches"; `check_eva_return` says it does not replace assess_route.
3. Eval changes (each is a case or scorer defect, not an agent defect):
   - `q04_zero_slope_pad`: reply keywords now also accept "doesn't/does not exist", "no cell", "bottleneck" (the correct answer "A literal
     zero-slope pad doesn't exist" failed the old phrase list). Test added; a reply that invents a pad still fails.
   - `los_radio_base_from_marker`: `list_markers` no longer required. The marker is in the app-state block, so acting directly is correct.
     The case now has `setup` (a marker is placed through the REST API, removed afterwards) because "my marker" presupposes one exists; with 0
     markers, asking is right and the old case demanded a `line_of_sight` call anyway.
   - `demo_scout`: new `setup` plans a route first (the prompt presupposes "that route"). `run_evals.py` gained `setup` support (live runs only).
   - `check_reply` ignores markdown `*` and backticks so emphasis cannot split a required phrase (lake_landing_underwater).

### Per-case results (full run)

PASS (19): demo_route_two_markers, demo_marswalk (assess_route now called), demo_scout (fsd_enable + follow_route), demo_sample_near_me, q02,
q03, q04, q05, q06, q07, q08, q09, q10, sun_local_time_now, sun_pad_shadow_1600, los_radio_base_from_marker, lake_shoreline_level,
eva_envelope_8h, lake_landing_underwater (after the markdown fix).
FAIL (5):
- demo_take_me_to_delta (systematic, number): searches correctly now, but the reply invents "~200-400 m" for nearby samples (model-made number).
- q01_terrain_at_landing_site (flaky, number): "1,460 m2" is a model-rounded/summed area; not in any result.
- eva_route_point_of_no_return (systematic, number): model writes "2 x 62 min = 124 min"; the tool only says out-and-back is twice one-way.
- vantage_overlook_outcrop (flaky, tool budget): 11 calls > 8 (passed in the targeted runs).
- demo_carbonates (flaky, tool path): skipped set_view after the prompt restricted viewer use; a follow-up prompt tweak fixed it in a rerun
  (passed), but the same tweak made q03 call set_view again in that rerun (q03 flipped between runs: viewer-call behaviour is flaky on DeepSeek).

Flaky: q03, demo_carbonates, vantage_overlook_outcrop, q01. Systematic: demo_take_me_to_delta and eva_route numbers (model arithmetic / invented
ranges). Fixed systematically: marswalk, scout, sample_near_me (43 m), q04, los, q08/vantage unit conversions.

### Number-provenance results
Unit-conversion violations (km2 from m2, km from m) from Runs 1/2: none in Run 3. Remaining ungrounded numbers are model arithmetic or invented
ranges (124 min, 200-400 m, 1,460 m2), 3 of 24 sessions. No unit-conversion or assumed-eye-height violations.

## Run 4: deepseek-v4-pro (same tools, prompt and evals as run 3)

Full run, all 24 cases, real Jezero site. **19/24 pass**, the same score as `deepseek-chat`, but different failures:

| Case | v4-pro | Note |
|---|---|---|
| demo_take_me_to_delta | PASS | flash invented "~200-400 m"; v4-pro did not |
| vantage_overlook_outcrop | PASS | 3 calls (flash used 11) |
| eva_route_point_of_no_return | FAIL | Same as flash: reply computes "124 min" (2 x 62) itself. **Tool gap**: check_eva_return never states the round-trip total |
| demo_carbonates | FAIL | No set_view/fly_camera. **Eval vs prompt conflict**: the prompt says use viewer tools only when asked |
| demo_scout | FAIL | 1 call, no fsd_enable/follow_route |
| q01_terrain_at_landing_site | FAIL | Extra search_pois call (harmless lookup; eval strict) |
| q05_crater_rim_walk | FAIL | Skipped describe_location; no max slope quoted |

Conclusion: swapping in the larger model does not raise the score; it trades failures. The one failure both models
share is a tool gap (no stated round-trip time). Recordings: `agent/replays/run4-v4pro/`.
