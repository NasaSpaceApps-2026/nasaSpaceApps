# MCP tool description review (simulated copilot, no API key)

Method: acted as a model that only sees the 28 tool descriptions/schemas plus `agent/system_prompt.md`. Planned each eval prompt from that alone, executed the plan through the MCP stdio client against devapi (site `jezero`), then compared with `agent/evals/questions.yaml`. Server source was not read to answer.

Data caveat that drives many failures: the live `jezero` site is **terrain-only** (layers: elevation, rough_5x5, slope_2m, quality_flag; POIs: `base` and `octavia_e_butler_landing`, same coordinates; no evidence/geologic layers, no delta POI, no search targets, no ground truth). Cases that need minerals cannot pass on this site regardless of wording; the table separates "data" from "description" causes.

Sample markers `m1`/`m2` were placed with `place_marker` to simulate user markers (they show as "placed by agent"). Rover state was changed by testing.

## Results

| Case | Planned calls | What happened | Verdict | Root cause |
|---|---|---|---|---|
| demo_carbonates | get_site_overview; find_sites(target="carbonate", near="base", radius_m=2000, max_slope_deg=20, top_k=5); set_view/fly_camera | find_sites -> is_error: "No searchable science targets are loaded ... Say that the data cannot answer". Clear, usable hint. Expected path (find_sites then set_view) unreachable. | FAIL (data) / description hesitate | Overview never says "Search targets: none"; find_sites description promises that section exists, so the model spends a call discovering it. Eval needs a mineral site or a skip rule. |
| demo_route_two_markers | list_markers; plan_route(start="marker:m1", goal="marker:m2", max_slope_deg=15); assess_route | Worked first try. Minor: assess says "no-go limit 15, caution 15" (caution band collapses when max_slope_deg is given), plan says "caution cell(s) (>15 deg)" on a 20 deg profile. | PASS | Slope-limit wording inconsistent between plan/assess. |
| demo_take_me_to_delta | search_pois(query="delta front"); then teleport_rover + fly_camera | search_pois: "No POIs match. Known types: base, landmark." Nothing to teleport to; correct behaviour is to say so. | FAIL (data) | No delta POI on site. Note the search_pois miss hint ("fewer filters") is useless for a name miss; it should list known POI names/ids. |
| demo_marswalk | plan_science_traverse(time_budget_min=240, max_stops=2); assess_route(route_id) | Returned 1 stop (the landing POI, value 0.5 assumed, 9 m route). User asked for "two best"; result does not say only one candidate exists. Consumables use the 480 min EVA limit although profile is eva_walkable by default (fine) but the rover profile elsewhere uses the same limit. | HESITATE | Result omits "N candidate stops considered / why fewer than max_stops". |
| demo_scout | (no route in conversation) -> plan_route(goal=..., start="rover"); fsd_enable(route_id); follow_route(route_id); fsd_status on stop | fsd_enable OK. follow_route() with no id used a stale armed route and failed "rover is 205 m from start of route-1" (hint good). Later follow_route() picked route-5, not the newest route-6. | HESITATE | "Omit route_id to use the route armed with fsd_enable" is easy to misread as "latest route"; fsd_enable({}) arms nothing yet the next plan is not auto-armed. Model must always pass route_id. Also "that route" with no route: system prompt says ask, eval expects fsd_enable. |
| demo_sample_near_me | search_pois(near="rover", radius_m=500) then find_regions/find_locations | search_pois: no match. Nothing sample-worthy is discoverable on this site; model must say so. Required group is satisfied by the search_pois call alone. | HESITATE | No guidance on what "worth sampling" maps to (science_value on POIs? evidence layers?). |
| q01_terrain_at_landing_site | search_pois(query="landing") -> describe_location(at="poi:octavia_e_butler_landing", radius_m=50) | Worked. Two POIs at identical coordinates (base and landing) both returned for "landing" because the query matched the "Next:" ordering/both. | PASS | Duplicate POI coordinates are confusing but harmless. |
| q02_three_flat_spots_walk | find_locations(profile="landing_pad", footprint_radius_m=50, within={"from":"poi:octavia_e_butler_landing","max":60,"metric":"walk_time_min"}, limit=3) | Worked first try thanks to the example; thresholds labelled assumed/user-specified. | PASS | Good example in description. |
| q03_good_landing_spots | find_locations(profile="landing_pad") | Worked; 1699 spots, set:2. | PASS | |
| q04_zero_slope_pad | find_locations(conditions=[slope_2m <= 0]) | 1st try: error "No footprint radius given and the profile has none" (hint good). `==` 0 rejected with good hint. With footprint -> "No spots found ... Relax that condition" with no suggested value. | HESITATE | Empty-result hint says "relax" but gives no number (e.g. the minimum achievable slope_2m); model cannot offer a concrete relaxation (system prompt demands one). |
| q05_crater_rim_walk | describe_location(at="rover", radius_m=50) (or marker) | "this" is unresolved: no app-state block in my simulation; "here" is rejected ("Unknown location here") although the system prompt maps "here" to the rover pose. Worked once `rover` keyword used. Eval asks for max slope; output gives max. | HESITATE | Keyword set (`base`, `rover`) is documented but "here"/"me" are not; fine if the model maps via the prompt, risky otherwise. No "crater rim" feature exists to anchor. |
| q06_slope_layer_limits | describe_layer(layer_id="slope_2m") | Worked, "Does NOT measure" line present. | PASS | |
| q07_outside_hirise_coverage | describe_layer(quality_flag) -> find_regions(quality_flag == ctx_fill) | Class names only discoverable via describe_layer (not in list_layers). Result: 0% pass, with hint "Relax that condition" (wrong advice: nothing to relax, it means no such cells). | PASS (hint misleading) | Hint text for a 0%-single-condition result is generic. |
| q08_carbonate_reachable_and_pad | find_regions(evidence carbonate, within 120 walk_time_min of base) -> find_locations(landing_pad) -> nearest | find_regions error "Unknown layer carbonate. Available layers: ..." (good). Chain cannot proceed. When chaining sets manually, nearest works (set:1 -> set:2). | FAIL (data) | No mineral layer. Note nearest with two overlapping pad sets returns 0 min self-pairs, unsurprising. |
| q09_rover_confirmed_carbonate | find_sites(target="carbonate", min_tier="confirmed_in_situ") | Error: no targets. Eval allows only find_regions/find_locations, but the find_sites description steers the model to find_sites (it even lists confirmed_in_situ as top tier). | FAIL (data + eval/description mismatch) | Description of find_sites vs eval expectation; also nothing says ground truth sites are searchable. |
| q10_olivine_delta_front | search_pois(query="delta") -> describe_location(at=..., layers=["olivine"]) | search_pois no match; describe_location layers=["olivine"] -> "Unknown layer olivine. Available layers..." good. Model must report cannot answer. | HESITATE | Data; hints fine. |

Counts: PASS 6, HESITATE 6, FAIL 4 (all four FAILs are data-driven on the terrain-only site).

## Cross-tool problems found

1. **Set member references.** Description says a member is written `"3.2"` (no prefix) while a set is `"set:3"`. A model naturally writes `"set:11.1"`; result: `Unknown set "set:11.1"` and the error does not say to drop the prefix. (`"11.1"` works; bare `"2"` also works for get_set.)
2. **`nearest` argument names.** Params are `from_ref` / `to_ref`, but result hints from find_regions say `nearest(from=set:9, to=<pad set>)`. Following the hint gives a raw pydantic error ("Field required") with no `Try:`. The same concept is `start`/`goal` in plan_route, `from` inside `within`, `at` in describe_location, `target` in goto/teleport/fly_camera/find_sites(`near`). Five spellings for "a location".
3. **Raw pydantic validation errors have no hint.** Wrong top-level types (`conditions` passed as a single dict, wrong parameter names) return `Error executing tool X: N validation errors...` while the server-level errors are well formed (`Error:`/`Try:`). Models retry blindly on these.
4. **Untyped location params.** `at`, `near`, `target`, `goal`, `start`, `points`, `from_ref`, `to_ref`, `layers` (set_view), `highlights` have no JSON type in the schema (just `title`). The schema gives the model no hint; everything depends on the long prose paragraph, repeated in 10 descriptions (about 700 chars each).
5. **`conditions`/`within` are `additionalProperties: true` objects.** Schema does not list `layer/op/value`, `evidence/min_tier`, `any_of`, nor within's required `from`, `max`, `metric`. `within` without `metric` or `from` is rejected at runtime even though the description example is the only place that says they are required (hint is good once hit).
6. **`follow_route()` with no id**: ambiguity between "armed" and "latest" route (see demo_scout). The failed call costs one of 12 turns' calls.
7. **`goto_location` vs `teleport_rover` vs `drive_rover`**: well separated in prose (goto = plan+drive, teleport = instant, drive = straight line). Real difference not stated: goto/drive advance simulated time (96.8 min for 215 m at rover_scout speed) and odometer; teleport is free. The system prompt says "take me to X" = teleport, while goto description says "drive to the landing site". OK but add the time cost to the description. Everything blocks and returns instantly unless animate=true; `speed_factor` units unclear ("default shows about 8 m/s" vs real 0.04 m/s).
8. **find_sites vs find_regions vs find_locations vs search_pois**: the descriptions cross-reference each other well. Residual overlap: find_regions(profile="landing_pad") returns 272 regions (largest 36 km2) which is meaningless for pads; nothing says profile + find_regions is not for pads. find_sites advertises "Search targets in get_site_overview" but the overview omits that section when empty.
9. **describe_location vs sample_terrain**: sample_terrain is a strict subset (point values, plus hazard class). Description says it returns "every layer" and a hazard class; describe_location(radius_m=0) does not return the hazard class. Model may hesitate; recommend a one-line "prefer describe_location unless you need the hazard class" or fold sample_terrain away.
10. **Slope limit wording in plan/assess**: `max_slope_deg=15` leads to "no-go limit 15, caution 15"; unlimited profile prints "caution cell(s) (>15 deg)" with a 20 deg no-go. Model may mis-state limits.
11. **Units**: placeholder walking speeds make `walk_time_min` values hard to sanity-check (spot 1850 m away = 42 min on foot) while rover routes run at 0.04 m/s (388 m = 173 min) against the same 480 min EVA limit. Not wrong, but the result never states the speed used, so the model cannot explain the discrepancy ("state the assumptions", system prompt rule).
12. **Result ids**: all search results include usable ids (`poi:`, set members `N.M`, `route-N`, `marker:mN`). Good. Missing: `fsd_enable` does not return the route id it bound when none given; `plan_science_traverse` lists stops but not each stop's POI as a `poi:` id usable in goto (it does print `poi:...`; fine). `set_view(highlights=["set:2"])` sends all 1699 members with no cap/warning.
13. **Empty-result hints**: "Relax that condition" is the generic hint even when a condition can never be satisfied (slope <= 0, ctx_fill absent). No achievable range is given (the layer's range is available from describe_layer).
14. **Tool-use friction on terrain-only sites**: overview carries no explicit "no mineral/geology layers" statement, so every mineral question costs 1-2 failed calls. Error message there is excellent (names the limitation and instructs to say data cannot answer).
15. **search_pois**: `near` hint says "Pass near=x,y" (string "1844,-433" accepted, though description lists only dict/POI/marker/rover); radius without near error is fine. Name-miss hint ("fewer filters") does not list known names.
16. `get_elevation_profile(points=["rover"])` error is good. `place_marker` marks agent markers vs user markers: "my markers" prompt will find only user-created ones; fine.

## Prioritised fixes (proposed wording)

**P0 - correctness / wasted calls**

1. Set member syntax. In the shared location-reference paragraph replace `a stored set "set:3" (every member) or one member "3.2"` with:
   `a stored set "set:3" (every member) or ONE member written WITHOUT the "set:" prefix, e.g. "3.2" (never "set:3.2")`.
   Also make the resolver accept `set:3.2` or return `Try: write a member as "3.2", not "set:3.2"`.
2. `nearest` hints: change generated hint text everywhere to `nearest(from_ref="set:9", to_ref="set:2")`, or rename params to `from_ref`/`to_ref` consistently with the hints. Preferably accept aliases `from`/`to`.
3. Wrap pydantic validation errors in the `Error: / Try:` format, naming the parameter and showing the example from the docstring (e.g. `conditions must be a LIST of objects: [{"layer": "slope_2m", "op": "<=", "value": 10}]`).
4. `follow_route`: replace "Omit route_id to use the route armed with fsd_enable." with `Always pass route_id from the most recent plan_route. If omitted it uses the route bound by fsd_enable(route_id=...), NOT the latest plan. Plan from start="rover" so the rover is within 3 m of the start.` Also have `plan_route` hint show `fsd_enable(route_id="route-N")`.
5. `get_site_overview`: always print a `Search targets:` line, and when empty say `Search targets: none (terrain-only site: no mineral/geology layers; find_sites and evidence conditions will fail; say the data cannot answer).` Add the same note to the `find_sites` description: `Only works if get_site_overview lists Search targets.`

**P1 - ambiguity**

6. Type the schemas. Give `conditions` an item schema (`layer|evidence`, `op`, `value`, `min_tier`, `any_of`), `within` required `[from, max, metric]` with an enum for `metric` and `profile`, `objective` enums, `mode` enum for set_view/fly_camera, `metric` enum on nearest. Give location params a `anyOf` of string/object/array so models see them as locations. Move the 700-char location-reference paragraph into one place (the overview or a param description) and shorten the per-tool copies to "location reference (see get_site_overview)".
7. `goto_location`/`teleport_rover`/`drive_rover` descriptions: add `goto_location and drive_rover advance simulated time and odometer (the result reports minutes); teleport_rover costs none. For "take me to X" use teleport_rover + fly_camera; use goto_location only when the user asks to drive.`
8. `sample_terrain`: `Quick check for one point: use describe_location(at={x,y}) unless you specifically need the safe/caution/no-go hazard class.` Or add `hazard_class` to describe_location output.
9. `find_regions` description: add `Do not use profile="landing_pad" here (that profile is for find_locations); with a pad profile it returns huge flat regions, not pads.`
10. Empty-result diagnostics: when a single condition passes 0%, hint `No cell passes "slope_2m <= 0" (layer range 0.0012-69.3 deg, median 3.4). Raise the threshold, for example to <= 1.` Include the layer min/max in the hint; for categorical, list the classes present and say if the class has 0% coverage ("class ctx_fill is not present on this site"). Replace the generic "Relax that condition".
11. `search_pois` miss hint: `No POIs match "delta front". Known POIs: base, octavia_e_butler_landing. Types: base, landmark.`

**P2 - polish**

12. plan_route/assess_route: state `no-go limit N deg, caution above M deg` correctly when max_slope_deg overrides (print both effective values), and make the "caution cell(s)" threshold match.
13. Always print the speed model used with every time (`rover_scout 0.04 m/s`, `eva_walkable 1.0 m/s [assumed]`) so the model can cite assumptions.
14. `plan_science_traverse`: result line `Considered N candidate stops; selected K (max_stops=...; fewer because ...)`; document that POIs without science_value default to 0.5 [assumed] (already noted in result).
15. `set_view(highlights=...)`: cap whole-set highlights (e.g. top 50) and say so; `speed_factor`: document units as a multiplier of real rover speed and give an example of the resulting playback speed.
16. Accept `"here"`/`"me"` as aliases of `rover` in location references (system prompt already maps them), or state in the reference list: `"here"/"me" = "rover"`.
17. Eval fixtures: `demo_carbonates`, `demo_take_me_to_delta`, `q08`, `q09`, `q10` need a mineral/delta site or should be SKIPped on the terrain-only site; widen `q09` required group to include `find_sites` (the description steers there); `demo_scout` needs a seeded route in the app-state block or should accept plan_route before fsd_enable.

## Re-run after fixes

Same 16 cases re-run through the MCP stdio client against a live devapi (`ATLAS_SITE_DIR=data/build/site/jezero`, port 8010), after the fixes below. The site is still terrain-only, so the data-caused FAILs stay FAIL.

### What changed (code)

- Location references: `"set:3.2"` now resolves like `"3.2"` (`atlas_core/refs.py`, `sets.py`); `"here"`/`"me"` mean `rover`. One shared, rewritten location paragraph states the member form ("WITHOUT the prefix, e.g. 3.2; set:3.2 also accepted") in every tool that takes locations.
- `nearest`: hints now say `nearest(from_ref=..., to_ref=...)`; the tool also accepts `from`/`to` aliases (applied server-side before validation; the published schema lists `from_ref`/`to_ref`).
- Validation: unknown, missing and wrong-type arguments come back as `Error: ... / Try: Valid arguments ...; Example: <tool>(...)`, naming the argument. Input schemas now carry condition item schemas, `within` (`from`/`max`/`metric` required, metric and profile enums), enums for metric, profile, tier, view mode and layer kind, a pattern for `objective` (incl. `best_evidence:<layer>`), and union types for locations (`{x,y}` | `{lat,lon}` | string | list where lists are accepted). There is no rover-command parameter on any tool (each command is its own tool), so there is no command enum.
- `follow_route`: with no `route_id` it uses the most recently planned route (plan_route, plan_science_traverse, FSD replan, or `fsd_enable(route_id)`, whichever is newest) and the result says which one. A "not at the route start" error now gives `teleport_rover(x=..,y=..)` / `goto_location(x=..,y=..)` for the start.
- `get_site_overview`: always prints `Search targets for find_sites:`, with "none (terrain-only site: no mineral or geology layers)..." when empty; lists all POIs (id, name, type, science value) and markers.
- Empty-result hints (find_locations/find_regions, added in `devapi/app.py` because `atlas_core/search.py` was out of scope): include the failing layer's real range/median or class list, and drop the misleading "Relax that condition" when a threshold can never match. `search_pois` misses list the known POI ids.
- Use-this-when / use-X-instead-when lines added for find_sites / find_regions / find_locations / search_pois, describe_location vs sample_terrain, goto_location vs teleport_rover (time cost stated), set_view vs fly_camera; find_regions says not to use `profile="landing_pad"`; speed_factor documented as a multiplier.
- P2: plan/assess slope-limit wording (`no-go above N deg, no separate caution band` when `max_slope_deg` collapses the band); plan_science_traverse prints "Considered N candidate stop(s); selected K"; `set_view` highlights capped at 50 with a note.
- q09 eval: `find_sites` added to the accepted tool group. Reason: `find_sites(min_tier="confirmed_in_situ")` is the most direct answer and its description now says so; `contracts/mcp-tools.md` only says "only tier 4 evidence; cites sample sites", which all three tools satisfy. System prompt gets two short lines (find_sites usage and "no targets means say the data cannot answer"; member form and explicit route_id).

### Results

| Case | Before | After | Notes |
|---|---|---|---|
| demo_carbonates | FAIL (data) | FAIL (data) | Overview now says up front "Search targets: none (terrain-only site)", so no wasted discovery call; still no minerals. |
| demo_route_two_markers | PASS | PASS | Limit wording now "no-go above 15 deg, no separate caution band". |
| demo_take_me_to_delta | FAIL (data) | FAIL (data) | Miss now lists "Known POIs: octavia_e_butler_landing, base". No delta POI exists. |
| demo_marswalk | HESITATE | PASS | "Considered 1 candidate stop(s); selected 1" explains why only one stop. |
| demo_scout | HESITATE | PASS | `plan_route` then `fsd_enable()` then `follow_route()` drove route-3 (newest) and said so. |
| demo_sample_near_me | HESITATE | HESITATE | Still no site data for "worth sampling" (data); model must report nothing found. |
| q01_terrain_at_landing_site | PASS | PASS | |
| q02_three_flat_spots_walk | PASS | PASS | |
| q03_good_landing_spots | PASS | PASS | |
| q04_zero_slope_pad | HESITATE | PASS | Hint: "range 0.001236 to 69.3, median 3.42 degrees ... Pick a threshold inside that range." |
| q05_crater_rim_walk | HESITATE | PASS | `describe_location(at="here")` now resolves to the rover. |
| q06_slope_layer_limits | PASS | PASS | |
| q07_outside_hirise_coverage | PASS (hint misleading) | PASS | Hint lists classes and says `ctx_fill` is absent or tiny; no "relax" advice. |
| q08_carbonate_reachable_and_pad | FAIL (data) | FAIL (data) | No mineral layer; chain `find_regions` -> `find_locations` -> `nearest(from=..., to=...)` itself now works (hint and aliases). |
| q09_rover_confirmed_carbonate | FAIL (data + eval mismatch) | FAIL (data) | Mismatch fixed (find_sites accepted); still no targets on this site. |
| q10_olivine_delta_front | HESITATE | HESITATE | Data: no olivine layer or delta POI; hints are correct. |

Counts after: PASS 10, HESITATE 2, FAIL 4 (all four FAILs are data-caused). Before: PASS 6, HESITATE 6, FAIL 4.

Not changed: P2 item 13 (print the speed model with every time) and the core `plan_science_traverse` "fewer because ..." reason need `atlas_core/route.py`; `contracts/mcp-tools.md` was not edited (it does not mention find_sites or the member form).
