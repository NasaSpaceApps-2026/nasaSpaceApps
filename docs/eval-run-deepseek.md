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
