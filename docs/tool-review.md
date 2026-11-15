# MCP tool description review (simulated copilot, no API key)

*Dated working log (Nov 14). The first section describes the earlier 28-tool, terrain-only state; later sections re-run the cases after science layers and honesty fixes (final: 17 pass, 4 hesitate, 0 fail of 21). The MCP server now lists 39 tools. Summary for judges: [science-validity.md](science-validity.md).*

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


## Re-run with science layers

Simulated copilot (no API key): read the system prompt and the tool list only, planned calls, executed them live through the MCP server against `devapi` on the real Jezero site (CRISM tiers, USGS units, sample POIs, Perseverance traverse). Scratch logs: `/private/tmp/claude-501/-Users-sameersahai-Projects-nasaSpaceApps/59edd807-1e53-40e6-b24e-f2e4f7969961/scratchpad/` (`tools.txt`, `cNN.out`, `qNN.out`, `e*.out`). Rover started at base; the demo_take_me and scout cases moved it, and it was teleported back to base before the q-cases. Verdicts cover tool choice AND what an honest final answer would need.

### Results

| Case | Calls | What happened | Verdict | Root cause |
|---|---|---|---|---|
| demo_carbonates | overview, describe_layer(carbonate), find_sites(carbonate, near=base), set_view(layers+highlights set:1) | 131 sites. Top two are tier 4 confirmed_in_situ ~0.8-0.9 km away (areas 1060 and 308 m2), but each line says "carbonate from MRO/CRISM". The next three are strong_orbital 6-7 km away. | HESITATE | find_sites cites the orbital instrument for tier 4 rows. No sample name, no rover instrument, and the "unit/section-level claim at a derived position" caveat appears only in describe_layer. The word "near" does not fit the 6 km rows. |
| demo_route_two_markers | list_markers | "No markers placed." Correct behaviour is to ask the user. The eval wants plan_route and assess_route, which would have to invent endpoints. | HESITATE (eval/state mismatch) | The eval assumes seeded markers. Add a seeded-marker fixture, or accept "ask" as a pass when no markers exist. |
| demo_take_me_to_delta | search_pois("delta front"), teleport_rover(poi:region_delta_front), fly_camera | Hit on the first search. Teleported and flew (no viewer connected, said so). Previously FAIL (data). | PASS | The search returns 10 fuzzy hits (all samples near the delta), which is noisy but harmless. |
| demo_marswalk | plan_science_traverse(240, start=rover), assess_route(route-1) | Selected 9 of 20 stops, not "two best". Every stop has value 0.5 (assumed), total "science value 4.5". Traverse says 169.7 min, assess_route says "34.7 min" with the same 169.7 min consumables. Paired samples at identical coordinates (10 and 11, 12 and 13, 17 and 18) are scheduled as separate 15 min stops. Route max slope 20.3 deg, "GO". | HESITATE | There is no max_stops or top-N argument. "Best" is meaningless when every value is assumed. Travel time and total time are not distinguished in assess_route. Co-located samples are not merged. |
| demo_scout | fsd_enable(route-1), follow_route(route-1), get_rover_state | STOPPED: replanned at (-666,407), 20 deg slope exceeds the 20 deg no-go. New route-2 is 313 m, 186 min. Reason and hazard are given plainly. | PASS (path) | The route was planned and assessed as eva_walkable (no-go 25 deg, "GO") but driven as rover_scout (no-go 20 deg). The tools never say that the profile changed, so "assessed GO, then the rover stopped" looks like a contradiction. Route-2 (313 m) is much shorter than the remaining route (about 1.1 km); the tool does not explain why. |
| demo_sample_near_me | get_rover_state, find_sites(olivine, near=rover, radius 1500), search_pois(near=rover, sample_site) | Row 2.1 reads "best tier 4 confirmed_in_situ, area 3,137,692 m2". The area is the whole connected orbital region that contains a confirmed cell. | HESITATE | The region-level best tier is read as if it covered the whole area. A model would write "3.1 km2 of confirmed olivine". Overclaim risk. |
| q01_terrain_at_landing_site | describe_location(poi:octavia_e_butler_landing, radius 0) | Slope 0.32 deg, hirise_dtm, unit Njf, olivine tier 2 weak_orbital, carbonate "tier 0 not_detected". | PASS | "tier 0 not_detected" has no gloss in the result (see fix 3). |
| q02_three_flat_spots_walk | find_locations(landing_pad, footprint_radius_m=25, within{from, max=60, metric=walk_time_min}) | 1240 spots, top 3 listed with walk times, thresholds marked assumed. | PASS | "50 m pad" could mean radius or diameter. The model picked radius 25 and must say so. |
| q03_good_landing_spots | find_locations(landing_pad) | 1699 spots, top 5 flattest, all in the NE. Thresholds marked assumed. | PASS | The top 5 are all 2.5 km or more from base. The result gives no distance without `within`. |
| q04_zero_slope_pad | find_locations(landing_pad, slope_2m <= 0) | "No spots found", with per-condition pass rates and the real range. | PASS | |
| q05_crater_rim_walk | describe_location(here, radius 50) | Mean 1.6, max 8.9 deg, 22% unknown for minerals. | PASS | |
| q06_slope_layer_limits | describe_layer(slope_2m) | A clear "Does NOT measure" section. | PASS | |
| q07_outside_hirise_coverage | describe_layer(quality_flag), find_regions(quality_flag != hirise_dtm) | 97 regions. There is no "HiRISE coverage" layer; quality_flag is the proxy, and its coverage reads 100%. | PASS (borderline) | A model must say that coverage is a proxy, not a map of HiRISE footprint. |
| q08_carbonate_reachable_and_pad | find_regions(carbonate >= weak_orbital, within base 120 walk_time_min), find_locations(landing_pad, limit 50), nearest(set:7, set:8) | The chain works end to end (57 pairs). Previously FAIL (data). | HESITATE | The region list shows no tier column, so the model cannot say weak or strong without extra describe_location calls. The result mixes in the tier-4 Malay cell (region 12.15 in the 90 min run) as if it were orbital. nearest lists the region's coordinates but not the pad's. "Safe" pad rests on assumed thresholds (slope <= 10, radius 50). Pad-to-outcrop distances of 7 m mean the pad may sit on the outcrop. |
| q09_rover_confirmed_carbonate | find_sites(carbonate, min_tier=confirmed_in_situ, near=base) | 2 sites (10.1 at 893 m and 10.2 at 772 m), cited "carbonate from MRO/CRISM". Previously FAIL (data). | HESITATE | The tool never names the sample (Sample 7 Malay, Samples 4 and 5) or the rover instrument. The layer text says "see each POI description" but no tool returns POI descriptions. Citing CRISM for a rover confirmation contradicts the system prompt rule. |
| q10_olivine_delta_front | describe_location(poi:region_delta_front, radius 100), describe_layer(olivine) | weak_orbital over 89% of the footprint, 8% unknown. Nearest in-situ olivine cell is 196 m away. Previously HESITATE (data). | PASS | The nearest ground-truth line does not name the instrument or sample. |
| a. confirmed olivine and walk distance | find_sites(olivine, min_tier=confirmed_in_situ, near=base), describe_location(set:11) | 4 sites at 818 m, 938 m, 2925 m and 3124 m walk. Two coincide with Samples 4 to 7 (carbonate also tier 4). | HESITATE | Same attribution gap as q09. Walk distance is in metres only (no time). The same cell is both carbonate and olivine tier 4 and the model cannot tell which sample supports which mineral. |
| b. carbonate within 90 min and nearest pad | find_regions(within 90), find_locations(landing_pad, 50), nearest | 16 regions, 10 pairs shown, walk times 0.3 to 3.2 min. | HESITATE | As q08. "Within 90 min" is measured to the region centre point, not the edge. |
| c. clay on the delta | describe_layer(geologic_unit), describe_location(delta_front, 300 m, clay layers), find_regions(NHjf2 AND any clay) | Fe/Mg-smectite best tier weak_orbital (73% of the footprint), Mg-smectite 2 best tier weak_orbital (0.43%), kaolinite not_detected, 24% unknown. | HESITATE | No layer named "clay" and no synonym table, so the model must map clay to smectite and kaolinite from its own knowledge. "Delta" is not a unit name (NHjf2 is "Jezero fan 2 unit"). The delta-front POI is a point, not a polygon. The unit-filtered region search returns an 8.3 km2 region far from the front. |
| d. unit and minerals here | get_rover_state, describe_location(here, 0), describe_location(here, 250) | Unit Njf at the point, 41% Nle within 250 m. Olivine best tier strong_orbital within 250 m, 45% unknown. | HESITATE | The ~100 m unit offset (measured +26 m E, -96 m N, median per-tile 102 m) lives only in describe_layer(geologic_unit); describe_location says nothing. At a point near a contact the unit may be wrong. "tier 0 not_detected" is printed with no "different prototype, not absence" gloss. |
| e. habitat in Séítah near olivine | find_locations(slope <= 5, hirise_dtm, unit in Nle, within 600 m straight of poi:region_south_seitah), describe_location(set:16) (error: 25 > 10), describe_location(list of 3) | 25 spots. 16.1 is 109 m from the Séítah point and 28 m from an in-situ carbonate cell. Olivine best tier 4 within 150 m. | HESITATE | Séítah is only a POI point, with no extent. Equating Séítah with Nle comes from model knowledge, not a tool. The unit offset (~100 m) is the same size as the spot-to-POI distance. describe_location refuses a set of more than 10, although its description says it accepts "a set". |

Counts: PASS 10, HESITATE 11, FAIL 0. The 5 earlier data-FAILs (demo_carbonates, demo_take_me, q08, q09, and the old q10 hesitate) are all now answerable. The remaining hesitations are about honesty and ergonomics, not missing data.

### Tool results that push a model toward overclaiming

1. find_sites and find_regions rows for tier-4 cells say "carbonate from MRO/CRISM ...". A model will write "CRISM confirmed" or cite no rover. Tier 4 is a sample-site statement placed at a derived position (10-50 m uncertainty).
2. find_sites region area is the connected orbital region containing the best-tier cell. "Confirmed_in_situ, 3,137,692 m2" reads as 3 km2 of rover-confirmed olivine.
3. describe_location prints "carbonate: tier 0 not_detected" with no note that it means "a different prototype won", and no mention of the dominant-mineralogy limit. The caveat is only in describe_layer, which costs a call from a 12-call budget.
4. The ~100 m geologic-map offset and the "tens of metres or more" CRISM offset appear only in describe_layer. No search or describe_location result carries them.
5. find_regions and nearest do not print tiers, so a mixed set of weak, strong and in-situ regions looks homogeneous.
6. "Safe landing pad" language: the pad is a profile of assumed thresholds, and the pad profile does not look at mineral or unit data.
7. Unknown is printed as "unknown (no data)" while the system prompt says "no_coverage". The model may not connect them.
8. plan_science_traverse reports "science value 4.5" when every stop was an assumed 0.5.

### Prioritised fixes

**P0 (honesty)**
1. Add the evidence tier and its provenance to every find_sites, find_regions and nearest row. Wording for the tier-4 row: `best tier 4 confirmed_in_situ (rover measurement at Sample 7: Malay, position uncertainty 10-50 m; orbital tier here: weak_orbital)`. Replace "carbonate from MRO/CRISM" on tier-4 rows with "orbital: MRO/CRISM; in situ: Perseverance (see poi:sample_07_malay)". Add `best_tier` and `tier_share` columns to find_regions rows.
2. For find_sites report the area at the best tier as well: `area 3137692 m2 at weak_orbital or higher; 992 m2 at confirmed_in_situ`. Do not print the region area next to the best tier without that split.
3. Append a one-line gloss to every describe_location evidence line: `not_detected = a different mineral class won the match; this is absence of evidence, not absence of the mineral. unknown = no CRISM data (no_coverage).` Use the same word "no_coverage" in tool output and in the system prompt.
4. Add the offsets to describe_location and find_* output whenever geologic_unit or an evidence layer is used: `Note: geologic unit boundaries may be offset by about 100 m (measured 102 m median); CRISM tiers may be offset by tens of metres or more. Points within about 100-200 m of a contact may be in the neighbouring unit.`
5. Expose POI descriptions (and the in-situ source and instrument) through search_pois(detail=true) or describe_location on a poi. The layer text says "see each POI description" and no tool delivers it.

**P1 (system prompt)** Add three lines.
- `Tier 4 (confirmed_in_situ) is a rover measurement at a sample site, placed at a derived position (10-50 m). Say which sample, and never credit CRISM for it.`
- `Quote the registration caveat (geologic map about 100 m, CRISM tens of metres) whenever you name a unit or a mineral tier at a specific place or near a contact.`
- `Mineral synonyms: clay means fe_mg_smectite, mg_smectite_2, kaolinite. Say which layers you used. Named regions (Séítah, delta front) are points, not polygons; do not claim "inside" without a polygon, and say that you treated a unit as a stand-in.`

**P2 (tool ergonomics)**
6. plan_science_traverse: add `max_stops` (so "two best targets" works), merge co-located POIs, and print `science value (assumed for all stops)` when every value is assumed. assess_route should print both travel time and total time (travel 34.7 min, stops 135 min, total 169.7 min).
7. State the profile in assess_route and plan_route output (`profile eva_walkable, no-go 25 deg`) and warn when follow_route drives with a different profile: `route planned for eva_walkable but rover_scout no-go is 20 deg`. Explain what route-2 covers (remaining distance only, or all of it).
8. describe_location: for a set larger than 10 return the first 10 with a note ("showing 10 of 25; use get_set") instead of an error. Add the pad coordinates to nearest rows.
9. Add a `synonyms` field or a "clay" alias to list_layers(query="clay"), and a "delta" alias for NHjf2 in get_site_overview. Add a short "unit names in plain words" line.
10. Eval harness: seed two markers for demo_route_two_markers (or accept "ask the user" when none exist); add checks that the reply names the tier and mentions "orbital" for q08/q09/a/b and mentions the offset for c/d/e; add a check that a tier-4 answer never cites only CRISM.


## Re-run after honesty fixes

Same simulated copilot method as above: the 21 cases replayed live through the MCP server (stdio, `ATLAS_API_URL=http://localhost:8010`, mcp 2.3 client, `is_error` read on every result) against `devapi` on the real Jezero site. 45 tool calls, 0 errors. Script and raw output: `rerun_all.py` / `rerun_all.out` in the session scratchpad. devapi was stopped afterwards. Set ids were bound from each result (the earlier run's hard-coded `set:7` and so on no longer apply).

### What changed

- **Tier provenance.** The catalogue carries `tier_sources` per evidence layer (orbital tiers: MRO/CRISM with citation; `confirmed_in_situ`: Perseverance rover, the list of sample POIs with position, radius and citation). `find_regions`, `find_locations` (top spots), `find_sites`, `describe_location` and `nearest` print tier 4 as `[rover sample: Sample 7: Malay]` and orbital tiers as `[orbital: MRO/CRISM]`. One line names each sample's source (Farley et al. 2022 for samples 4-7, Williams et al. 2023 and Hurowitz et al. 2023 for 14-15). Rows no longer say "from MRO/CRISM" for a tier-4 cell.
- **Area per tier.** Regions report `area_by_tier_m2`. The old olivine row "best tier 4, 3,137,692 m2" now reads: region 3.14 km2 in total (whole region, not the area at the best tier); confirmed_in_situ 5708 m2 [Sample 15: Mageik], strong_orbital 0.47 km2, weak_orbital 2.67 km2.
- **Glosses.** `not_detected` = "covered by MRO/CRISM, nothing detected: absence of evidence"; unknown = "no_coverage: no data here (cannot answer)". The system prompt uses the same word.
- **Registration caveats in results.** `near_boundary_of` per spot or location and `registration_notes` per result. Rule: footprint within 100 m of a unit contact, within 36 m of a CRISM tier change; region searches on those layers always carry the note because region edges follow the layer boundary. A footprint that is all `no_coverage` is not flagged for the evidence layer.
- **Geologic map shift: applied.** The measurement was re-run (deterministic): mosaic offset +25.8 m east, -98.1 m north (per-tile median), 16 tiles, tile shift vectors deviate from the median by at most 25.2 m (rms 10.4 m), below the 30 m criterion. `sitebuild/science.py` now moves the polygons by (-25.8, +98.1) m (flag `APPLY_GEO_SHIFT`, applied only if the spread is below `GEO_SHIFT_MAX_SPREAD_M`), and records shift and residual spread in the catalogue `registration` entry. The shift changes 7.9% of cells. Samples 4-7 (South Séítah) fall in the Lower etched unit (Nle) both unshifted and shifted, which is the unit the literature associates with Séítah; they are deep inside Nle, so this check does not test the shift. The NASA sample list names regions, not units, so the unit expectation comes from the literature. One sample moved: Sample 14 (Shuyak, delta front, uncertainty 18 m) went from NHjf2 to Njf; it sits near a contact and is flagged. The caveat distance was kept at 100 m (residual spread 25 m, contact drawing at 1:75,000, and the USGS readme's 100-200 m).
- **Science traverse.** When every stop value is assumed there is no summed value; the result says stops were ordered by travel time only, with no science weighting, and that this is not a ranking of best targets. Mixed lists sum recorded values only. `assess_route` prints "travel N min" and the consumables line says how many minutes are at stops.
- **POIs and ground truth.** `search_pois` prints sample region, rock type, in-situ minerals (or "none read"), position uncertainty, and the full description with source for up to 3 hits. `nearest_ground_truth` names the sample, the NASA list as source, and the citation for any mineral statement.
- **Ergonomics.** `describe_location` over 10 locations describes the first 10 and says how to page. `follow_route` warns when the route was planned under another profile than the rover drives with (`profile_warning`). The FSD replan text now says the new route goes straight to the original goal and drops intermediate stops, and how much of the old route was left. `demo_route_two_markers` accepts "ask the user to place markers".
- **System prompt:** 4 added lines (tier 4 is a rover sample claim; pass on registration caveats; clay mapping; named regions are points).

### Results

| Case | Verdict | Before | What an honest answer now has |
|---|---|---|---|
| demo_carbonates | PASS | HESITATE | Top two are tier 4 at Samples 6 and 7 / 4 and 5 (1060 and 308 m2, rover sample, Farley et al. 2022); the next three are strong_orbital with their km2 split. Registration caveat printed. |
| demo_route_two_markers | PASS | HESITATE | "No markers placed" and asking is correct and now accepted by the eval. |
| demo_take_me_to_delta | PASS | PASS | Sample rows show region, minerals "none read" or listed, and approximate position. |
| demo_marswalk | PASS | HESITATE | No "science value 4.5". States stops were ordered by travel time with no science weighting. Residual: co-located samples are still separate 15 min stops. |
| demo_scout | PASS | PASS (path) | Profile warning (eva_walkable no-go 25 vs rover_scout 20) and what route-2 covers (about 1076 m of route-1 were left; replan skips the stops). |
| demo_sample_near_me | PASS | HESITATE | Region 3.14 km2 in total; 5708 m2 is rover-confirmed (Sample 15); the rest is orbital. |
| q01 landing terrain | PASS | PASS | "tier 0 not_detected" now glossed as absence of evidence. |
| q02 flat spots walk | PASS | PASS | Radius vs diameter ambiguity is still the model's to state. |
| q03 good landing spots | PASS | PASS | No distance without `within`. |
| q04 zero-slope pad | PASS | PASS | |
| q05 crater rim walk | PASS | PASS | Unit and tier caveats now present. |
| q06 slope limits | PASS | PASS | |
| q07 HiRISE coverage | PASS | PASS (borderline) | quality_flag is still a proxy. |
| q08 carbonate + pad | HESITATE | HESITATE | Tier and area per tier are in the region rows; nearest carries the region tier and the pad coordinates. Still: pad thresholds are assumed and the pad profile ignores minerals; pads 7 m from an outcrop may sit on it; walk time is to the region centre. |
| q09 rover-confirmed carbonate | PASS | HESITATE | Names Samples 4-7 and the Farley et al. 2022 statement; says tier 4 is a rover claim at a derived position. |
| q10 olivine delta front | PASS | PASS | Nearest in-situ cell 196 m away, Sample 15: Mageik, Williams/Hurowitz citation. |
| a. confirmed olivine + walk | PASS | HESITATE | Four sites with sample names per mineral layer. Walk is in metres only. |
| b. carbonate 90 min + pad | HESITATE | HESITATE | As q08. |
| c. clay on the delta | HESITATE | HESITATE | Tiers and caveats printed per layer; the clay synonym lives only in the system prompt; the unit-filtered region search returns regions far from the delta front; the delta front is still a point. |
| d. unit and minerals here | PASS | HESITATE | Unit and tier caveats appear for footprints near a contact or tier change. |
| e. habitat in Séítah | HESITATE | HESITATE | Spots 16.3 and 16.4 flagged near a unit contact. Séítah is only a point and Séítah = Nle is still model knowledge. |

Counts: PASS 17, HESITATE 4, FAIL 0 (before: PASS 10, HESITATE 11, FAIL 0). The four remaining hesitations (q08, b, c, e) are about assumed pad thresholds, point-versus-polygon regions and a missing clay synonym in tool output, not about misattributed evidence.

### Tests

`tests/test_honesty_fixes.py` (22 tests: synthetic site with provenance and registration metadata for the logic; the real Jezero build for catalogue facts, shift record, Samples 4-7 unit, tier-4 attribution). Two older tests were updated for the new behaviour (describe_location paging, summed science value). Full suite 258 passed.
