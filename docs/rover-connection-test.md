# Rover connection test: MCP server -> Yousef's real API

Nov 14, 2026 · Sameer · run on branch `sameer/mcp` (merged with origin/main). Nothing committed.

## Setup (exact commands)

```bash
# Yousef's API in its own venv (python3.11 from Homebrew; requirements.txt untouched)
python3.11 -m venv api/.venv && api/.venv/bin/pip install -r api/requirements.txt

# Real Jezero in his data-dir layout (our site -> manifest.json + analysis/ + data/); same frame by construction
.venv/bin/python -m devapi.export_ares_dir data/build/site/jezero $SCRATCH/ares_real

# 1. Yousef's API on :8000 (real site)    2. our query engine on :8001 (same site)
ARES_DATA_DIR=$SCRATCH/ares_real api/.venv/bin/uvicorn ares_api.main:app --app-dir api --port 8000 &
ATLAS_SITE_DIR=data/build/site/jezero .venv/bin/python -m devapi --port 8001 &

# 3. MCP server over stdio (what Claude Desktop / the agent spawns), both URLs
ATLAS_API_URL=http://localhost:8001 ATLAS_ROVER_API_URL=http://localhost:8000 .venv/bin/python mcp/server.py
```

`GET /site` on both: jezero, lat0 18.452, lon0 77.418, 8192 m: frame check passes (no banner in any result below).
The client is the `mcp` 2.3 Python client over stdio (`mcp.client.Client(StdioServerParameters(...))`; failures
show `is_error=True`); a second client listens on `ws://localhost:8000/ws`. Long tool text is cut to 700 chars here.

## Result

* The rover moved: `follow_route(animate=true)` took it from (1844, -433) to the marker at (1900, -400), 69 m,
  `ARRIVED`, odometer 69 m; later runs drove 2.2 km (`follow_route` without animation, `speed_factor` ~10000x) and
  stopped on a real 21 degree slope (`blocked_slope`) with the correct hint.
* WebSocket: while `follow_route` ran, the listener on his `/ws` received **45 `rover.state`** events (mode `fsd`,
  x from 1844 to 1898 in order) and 4 `agent.event` (his hub broadcasts the MCP server's `POST /agent/event`, 2 per tool
  call: `started` then `ok`). The whole session also saw `marker.add`, `route.set`, `view.camera`, `view.layer`,
  `view.highlight`, `search.results`. His hub only broadcasts `rover.state` while a command runs (10 Hz) and at start/stop.
* Browser sandbox: see the last section (link opens, but a manual-mode browser cancels API drives).

## Transcript 1: main flow (`live.py`)

```text

### get_site_overview {}  is_error=False
Site "jezero": 8192 m square at 2 m resolution, centre 18.4520 N, 77.4180 E. Site frame: metres, x east, y north, origin at site centre (lat0, lon0).
Layers:
  continuous: aspect_deg (Direction the ground faces, i.e. the compass bearing of steepest DESCENT, degrees clockwise from north (0 = faces north, 90 = east, 180 = south, 270 = west), Horn 3x3 finite difference on the 2 m elevation (about 6 m support), same gradient as slope_2m. 'North-facing' means aspect_deg within 45 deg of 0 (or aspect_class N / NE / NW)); curvature_plan (Curvature of the contour line across the slope (spurs / ridges diverge = positive, gullies / hollows converge = negative), Zevenbergen and Thorne 3x3 partial-quart

### get_rover_state {}  is_error=False
Rover (1844, -433) m, z -2570 m, heading 0 deg (clockwise from north), pitch -0.1, roll 0.1 deg; mode idle, speed 0 m/s, odometer 0 m.
Status: ready. Last event: None.
Profile rover_scout. Simulated time driven 0 min.

### place_marker {"x": 1900, "y": -400, "label": "goal A"}  is_error=False
Placed marker:m1 "goal A" at (1900, -400) m (z -2569, 18.4453, 77.4518).
Use "marker:m1" as a location in other tools.

### list_markers {}  is_error=False
1 marker(s):
- marker:m1 (1900, -400) m z -2569, "goal A", placed by agent
Use them anywhere a location is accepted, e.g. plan_route(start="marker:m1", goal="marker:m2").

### plan_route {"goal": "marker:m1", "profile": "rover_scout"}  is_error=False
Route r1 (rover_scout, slope limit 20 deg): 69 m, 30.8 min, ascent 1 m, max slope 1.9 deg (caution above 15 deg, no-go above 20 deg), 0 cell(s) steeper than 15 deg.
Consumables (time only; EVA limit 1440 min, reserve 10%): used 30.2 min (2%), margin above reserve 1266 min (88%).
Assessment: GO
Times use a placeholder isotropic speed model (approximate). Assumed: travel speeds and slope limits of profile rover_scout are assumed: flat speed close to Perseverance top speed (~4.2 cm/s); slopes assumed.
Next: assess_route("r1"), then follow_route("r1") (rover must be at the start).

### assess_route {"route_id": "r1"}  is_error=False
GO for r1 (rover_scout): 69 m, 30.8 min, max slope 1.9 deg (caution above 15 deg, no-go above 20 deg), ascent 1 m.
Consumables (time only; EVA limit 1440 min, reserve 10%): used 30.2 min (2%), margin above reserve 1266 min (88%).
Limits marked assumed in the profile are not cited values.
Next: follow_route to drive it.

### fsd_enable {}  is_error=False
FSD ARMED; mode idle; policy replan_on_block=on, caution_slowdown=on; replans used unknown (the rover backend only reports replans in drive results) of 1; route none, 0 m remaining.
No hazard recorded.
Blockers: no active route (plan_route, then follow_route).
Next: follow_route to continue, or plan_route from the rover to re-plan.

### follow_route {"route_id": "r1", "animate": true, "speed_factor": 400}  is_error=False
ARRIVED - Arrived at goal A.
Rover now (1900, -400) m, heading 78 deg, odometer 69 m, pitch 0.8 roll -0.1 deg.

WS events during follow_route: {'agent.event': 4, 'rover.state': 45}

### get_rover_state {}  is_error=False
Rover (1900, -400) m, z -2569 m, heading 78 deg (clockwise from north), pitch 0.8, roll -0.1 deg; mode idle (FSD armed), speed 0 m/s, odometer 69 m.
Status: arrived. Last event: Arrived at goal A.
Profile rover_scout. Simulated time driven 30.7 min.

### teleport_rover {"target": "base", "heading_deg": 90}  is_error=False
ARRIVED - teleported to Mission base (assumed)
Rover now (1844, -433) m, heading 90 deg, odometer 69 m, pitch -0.1 roll -0.1 deg.

### set_view {"mode": "orbit", "target": "marker:m1", "layers": {"slope": true}, "highlights": ["base"]}  is_error=False
View update sent: mode orbit; target goal A (1900, -400) m; layers slope on; 1 highlight(s): base.
Next: describe the scene from tool results, or place_marker / plan_route.

### find_sites {"target": "carbonate", "near": "base", "radius_m": 2500, "top_k": 3}  is_error=False
3 site(s) for carbonate (abundance_index); top 3, ranked by score (0-1 index) with unreachable sites last, from Mission base (assumed) (1844, -433). Not stored as a set; use the site ids as locations.
1. site1 (1327, -1155) m, score 0.71 (0-1 index, not abundance), area 1060 m2, mean slope 4.9 deg, 888 m away, reachable; layer min_carbonate
2. site2 (-431, 603) m, score 0.63 (0-1 index, not abundance), area 6076 m2, mean slope 5.2 deg, 2500 m away, reachable; layer min_carbonate
3. site3 (1401, -1061) m, score 0.6 (0-1 index, not abundance), area 308 m2, mean slope 6.2 deg, 769 m away, reachable; layer min_carbonate
Hint: Scores are the rover backend's 0-1 index (mean layer value and area, p

### find_locations {"conditions": [{"layer": "slope_2m", "op": "<=", "value": 5}], "limit": 2}  is_error=True
Error: No footprint radius given and the profile has none.
Try: Pass footprint_radius_m (use 0 explicitly for point locations), for example 50 for a landing pad.

### describe_location {"at": "marker:m1"}  is_error=False
1 location(s), point values.
point (1900, -400) m (18.4453, 77.4518):
  elevation: -2569 m
  slope_2m: 0.72 degrees
  rough_5x5: 0.03 m
  quality_flag: hirise_dtm
  geologic_unit: Jezero floor unit (Njf)
  aspect_deg: unknown (no data)
  aspect_class: flat
  curvature_profile: 0.00149 1/m
  curvature_plan: -0.000436 1/m
  tpi_50m: 0.31 m
  tri: 0.07 m
  local_relief_50m: 1.5 m
  local_relief_200m: 6.4 m
  outcrop_exposure: 0 m
  dist_to_traverse_m: 2.7 m
  dist_to_unit_contact_m: 148.1 m
  unit_diversity_100m: 1 count
  mineral_diversity_100m: 1 count
  no_coverage (no data here (cannot answer)): carbonate, olivine, fe_mg_smectite, high_ca_pyroxene, low_ca_pyroxene, kaolinite, mg_smectite_2


### describe_location {"at": "rover"}  is_error=False
1 location(s), point values.
point (1844, -433) m (18.4447, 77.4508):
  elevation: -2570 m
  slope_2m: 0.31 degrees
  rough_5x5: 0.03 m
  quality_flag: hirise_dtm
  geologic_unit: Jezero floor unit (Njf)
  olivine: tier 2 weak_orbital (orbital: MRO/CRISM)
  aspect_deg: unknown (no data)
  aspect_class: flat
  curvature_profile: -0.000587 1/m
  curvature_plan: 0.000671 1/m
  tpi_50m: -0.32 m
  tri: 0.05 m
  local_relief_50m: 2 m
  local_relief_200m: 6.7 m
  outcrop_exposure: 0 m
  dist_to_traverse_m: 6.3 m
  dist_to_unit_contact_m: 107.4 m
  unit_diversity_100m: 1 count
  mineral_diversity_100m: 1 count
  not_detected (covered, nothing detected (absence of evidence, not absence of the mineral

### search_pois {"query": "delta", "limit": 3}  is_error=False
3 POI(s):
- poi:sample_10_swift_run Sample 10: Swift Run [sample_site] (-665, 409) m, science value not recorded (region Delta Front, rock type Sedimentary; minerals measured in situ per source: none read; position approximate +-13 m)
  description: Sample 10 of the NASA Perseverance sample list: core/sample 'Swift Run' from feature 'Skinner Ridge', sealed sol 490 (July 7, 2022). Rock type per NASA list: Sedimentary (Rock Core). Sampling region per NASA list: Delta Front. APPROXIMATE position derived from the rover waypoint at sol 477 (RMC 26_1004, MMGIS waypoint, the last waypoint at or before sol 490); NOT an official sample coordinate. The rover may have moved between that waypoint and co

### sample_terrain {"x": 1900, "y": -400}  is_error=False
1 location(s), point values.
point (1900, -400) m (18.4453, 77.4518):
  elevation: -2569 m
  slope_2m: 0.7 degrees
  rough_5x5: 0.03 m
  min_carbonate: unknown (no data)
  min_clay: unknown (no data)
  min_olivine: unknown (no data)
  data quality: rover backend: elevation/slope/roughness from its analysis rasters; no per-cell quality classes
  nearest ground truth: none in this site
Hazard class for rover_scout: safe (caution above 15 deg, no-go above 20 deg).
Unknown means no data, not safe or absent. Next: describe_layer(id) for a layer's limits.

### get_elevation_profile {"points": ["base", "marker:m1"]}  is_error=False
Profile 65 m, 8 samples every 10 m: elevation -2570 to -2569 m, ascent 1 m, descent 0.1 m, slope mean 1.2 / max 1.6 deg.
Samples (distance: elevation m, slope deg): 0 m: z -2570 slope 0.3; 9 m: z -2570 slope 1.6; 18 m: z -2570 slope 1.2; 28 m: z -2570 slope 1.6; 37 m: z -2570 slope 1.3; 46 m: z -2569 slope 1.2; 55 m: z -2569 slope 1.4; 65 m: z -2569 slope 0.7
Next: plan_route to get a drivable route; slope is per 2 m cell, so small rocks are not resolved.

### plan_science_traverse {"time_budget_min": 600}  is_error=False
No science stops fit (budget 600 min, 0 candidate(s) considered).
Hint: No science targets match. Try other interests or none.

### stop_rover {}  is_error=False
STOPPED: stopped_by_user
Rover now (1844, -433) m, heading 90 deg, odometer 69 m, pitch -0.1 roll -0.1 deg.

ALL WS EVENT TYPES: {'connected': 1, 'rover.state': 47, 'agent.event': 40, 'marker.add': 1, 'route.set': 1, 'view.camera': 1, 'view.layer': 1, 'view.highlight': 1, 'search.results': 1}
sample agent.event: [{'type': 'agent.event', 'tool': 'get_site_overview', 'args_summary': '{}', 'result_summary': '', 'status': 'started'}, {'type': 'agent.event', 'tool': 'get_site_overview', 'args_summary': '{}', 'result_summary': 'Site "jezero": 8192 m square at 2 m resolution, centre 18.4520 N, 77.4180 E. Site frame: metres, x east, y north, origin at site centre (lat0, lon0).', 'status': 'ok'}, {'type': 'agent.event', 'tool': 'get_rover_state', 'args_summary': '{}', 'result_summary': '', 'status': 'started'}]
rover.state samples: [(1844, -433, 'idle'), (1855, -433, 'fsd'), (1864, -426, 'fsd'), (1872, -420, 'fsd'), (1880, -412, 'fsd'), (1888, -404, 'fsd'), (1898, -400, 'fsd')]
```

## Transcript 2: FSD, blocked slope, no-route (flat start at (183, 435), steep ground 32 m north)

```text

### teleport_rover {"x": 183, "y": 435, "heading_deg": 0}  is_error=False
ARRIVED - teleported to (183, 435)
Rover now (183, 435) m, heading 0 deg, odometer 69 m, pitch -0.8 roll -1 deg.

### goto_location {"x": 183, "y": 450}  is_error=False
STOPPED: blocked_hazard - Stopped before unknown (interpolated) terrain at (183, 436); limit is 20 deg.
Rover now (183, 435) m, heading 0 deg, odometer 69 m, pitch -0.8 roll -1 deg.
Note: animate=false is approximated on the rover backend: its sim always runs in real time, so the drive was run about 10000x faster than real speed. FSD attempted 1 replan(s) during the drive (the rover backend replans in place and never stops with stop_reason replanned). Events: Hit unknown (interpolated) terrain; replanned as r3 (15 m to go).
Hint: Arm FSD (fsd_enable) so a blocked route is replanned once, or plan_route to the same goal with a lower max_slope_deg, or pick another direction.

### fsd_disable {}  is_error=False
FSD off; mode idle; policy replan_on_block=on, caution_slowdown=on; replans used unknown (the rover backend only reports replans in drive results) of 1; route r3, 0 m remaining.
Last hazard: unknown (interpolated) terrain at (183, 436) m.
No blockers.
Next: plan_route then follow_route; fsd_enable first to allow one automatic replan.

### drive_rover {"distance_m": 60, "heading_deg": 0}  is_error=False
STOPPED: blocked_slope - Stopped before 21 deg slope at (183, 463); limit is 20 deg.
Rover now (183, 462) m, heading 0 deg, odometer 69 m, pitch 14.7 roll 11 deg.
Note: animate=false is approximated on the rover backend: its sim always runs in real time, so the drive was run about 10000x faster than real speed.
Hint: Arm FSD (fsd_enable) so a blocked route is replanned once, or plan_route to the same goal with a lower max_slope_deg, or pick another direction.

### plan_route {"goal": {"x": 183, "y": 560}, "max_slope_deg": 15}  is_error=False
NO ROUTE (feasible: false). (183, 560) at (183, 560) is on a 30 deg slope, over the 15 deg limit. Pick a point on flatter ground nearby (sample_terrain shows slope).
No reachable point found.
Hint: Use the nearest reachable point as the goal, pick another goal, or relax max_slope_deg.

### fsd_enable {}  is_error=False
FSD ARMED; mode idle; policy replan_on_block=on, caution_slowdown=on; replans used unknown (the rover backend only reports replans in drive results) of 1; route none, 0 m remaining.
Last hazard: 20.5 degree slope at (183, 463) m.
Blockers: no active route (plan_route, then follow_route).
Next: follow_route to continue, or plan_route from the rover to re-plan.

### drive_rover {"distance_m": 60, "heading_deg": 0}  is_error=False
STOPPED: blocked_slope - Stopped before 21 deg slope at (183, 463); limit is 20 deg.
Rover now (183, 462) m, heading 0 deg, odometer 69 m, pitch 14.7 roll 11 deg.
Note: animate=false is approximated on the rover backend: its sim always runs in real time, so the drive was run about 10000x faster than real speed. FSD attempted 1 replan(s) during the drive (the rover backend replans in place and never stops with stop_reason replanned). Events: Hit 21 deg slope; replan failed: 60 m at 0 deg at (183, 522) is on a 21 deg slope, over the 20 deg limit. Pick a point on flatter ground nearby (sample_terrain shows slope).
Hint: Arm FSD (fsd_enable) so a blocked route is replanned once, or plan_route to

### fsd_status {}  is_error=False
FSD ARMED; mode idle; policy replan_on_block=on, caution_slowdown=on; replans used unknown (the rover backend only reports replans in drive results) of 1; route none, 0 m remaining.
Last hazard: 20.5 degree slope at (183, 463) m.
Blockers: no active route (plan_route, then follow_route).
Next: follow_route to continue, or plan_route from the rover to re-plan.

### plan_route {"goal": {"x": 183, "y": 467}}  is_error=False
NO ROUTE (feasible: false). (183, 467) at (183, 467) is on a 30 deg slope, over the 20 deg limit. Pick a point on flatter ground nearby (sample_terrain shows slope).
No reachable point found.
Hint: Use the nearest reachable point as the goal, pick another goal, or relax max_slope_deg.
```

## Transcript 3: error and edge cases

```text

### get_rover_state {}  is_error=False
Rover (183, 462) m, z -2543 m, heading 0 deg (clockwise from north), pitch 14.7, roll 11 deg; mode idle (FSD armed), speed 0 m/s, odometer 69 m.
Status: blocked_slope. Last event: Stopped before 21 deg slope at (183, 463); limit is 20 deg.
Profile rover_scout. Simulated time driven 52 min.

### find_sites {"target": "clay", "near": "base", "radius_m": 3000, "top_k": 2}  is_error=False
2 site(s) for clay (abundance_index); top 2, ranked by score (0-1 index) with unreachable sites last, from Mission base (assumed) (1844, -433). Not stored as a set; use the site ids as locations.
1. site4 (1923, 641) m, score 0.67 (0-1 index, not abundance), area 18576 m2, mean slope 2.7 deg, 1077 m away, reachable; layer min_clay
2. site5 (-947, -1313) m, score 0.67 (0-1 index, not abundance), area 7468 m2, mean slope 1.8 deg, 2927 m away, reachable; layer min_clay
Hint: Scores are the rover backend's 0-1 index (mean layer value and area, penalised by slope), NOT evidence tiers or abundance; cite the layer, mission and instrument in each site's evidence. Sites are not stored as a set: use their ids (e.g. site1) as locations.
Next: set_view(highlights=[ids]) to show them, plan_route(goal="<id>") to get there.

### describe_location {"at": "site1"}  is_error=True
Error: Unknown location "site1".
Try: Known POIs include: octavia_e_butler_landing, base, sample_01_roubion, sample_02_montdenier, sample_03_montagnac, sample_04_salette, sample_05_coulettes, sample_06_robine, sample_07_malay, sample_08_ha_ahoni, sample_09_atsa, sample_10_swift_run, sample_11_skyland, sample_12_hazeltop, sample_13_bearwallow. Or pass {"x":..,"y":..}.

### plan_route {"goal": "site1"}  is_error=True
Error: Unknown place 'site1'. Use search_pois or list_markers to find ids.

### find_regions {"conditions": [{"layer": "slope_2m", "op": "<=", "value": 4}], "min_area_m2": 20000, "limit": 2}  is_error=False
Found 81 regions; showing top 2 by largest. Full ranked list stored as set:2.
1. 2.1 (2515, 1363) m, area 24818564 m2, score 24818564, slope_2m_mean=1.9, slope_2m_max=4
2. 2.2 (-983, 2423) m, area 922852 m2, score 922852, slope_2m_mean=2.2, slope_2m_max=4
Diagnostics: all conditions passes 56.7% of cells.
Thresholds used: slope_2m <= 4 [user-specified]; min_area_m2 = 20000 [user-specified or default].
Hint: Call describe_location on a region point, or nearest(from_ref=set:2, to_ref=<pad set>) for the closest pads.

### plan_route {"goal": "1.1"}  is_error=False
Route r4 (rover_scout, slope limit 20 deg): 2723 m, 1231 min, ascent 14 m, max slope 18.3 deg (caution above 15 deg, no-go above 20 deg), 1 cell(s) steeper than 15 deg.
Consumables (time only; EVA limit 1440 min, reserve 10%): used 1231 min (86%), margin above reserve 64.8 min (4%).
Assessment: GO with cautions: 1 of 556 route points are on caution slopes (>= 15 deg).
Warnings: rover moved 6 m to the nearest safe ground.
Times use a placeholder isotropic speed model (approximate). Assumed: travel speeds and slope limits of profile rover_scout are assumed: flat speed close to Perseverance top speed (~4.2 cm/s); slopes assumed.
Next: assess_route("r4"), then follow_route("r4") (rover must be at the start).

### plan_route {"goal": "marker:nope"}  is_error=True
Error: Unknown place 'marker:nope'. Use search_pois or list_markers to find ids.

### plan_route {"goal": {"x": 99999, "y": 0}}  is_error=False
NO ROUTE (feasible: false). (99999, 0) is outside the site (|x|, |y| <= 4096 m).
No reachable point found.
Hint: Use the nearest reachable point as the goal, pick another goal, or relax max_slope_deg.

### drive_rover {"distance_m": 40, "heading_deg": 0}  is_error=False
STOPPED: blocked_hazard - Stopped before unknown (interpolated) terrain at (362, 593); limit is 20 deg.
Rover now (362, 592) m, heading 0.1 deg, odometer 315.1 m, pitch 2.4 roll 1.1 deg.
Note: animate=false is approximated on the rover backend: its sim always runs in real time, so the drive was run about 10000x faster than real speed. FSD attempted 1 replan(s) during the drive (the rover backend replans in place and never stops with stop_reason replanned). Events: Hit 21 deg slope; replanned as r5 (1366 m to go).
Hint: Arm FSD (fsd_enable) so a blocked route is replanned once, or plan_route to the same goal with a lower max_slope_deg, or pick another direction.

### goto_location {"x": 1800, "y": -300}  is_error=False
STOPPED: blocked_hazard - Stopped before unknown (interpolated) terrain at (546, 450); limit is 20 deg.
Rover now (546, 451) m, heading 172.1 deg, odometer 552.9 m, pitch -0.7 roll 0.2 deg.
Note: animate=false is approximated on the rover backend: its sim always runs in real time, so the drive was run about 10000x faster than real speed. FSD attempted 1 replan(s) during the drive (the rover backend replans in place and never stops with stop_reason replanned). Events: Hit unknown (interpolated) terrain; replanned as r7 (1606 m to go).
Hint: Arm FSD (fsd_enable) so a blocked route is replanned once, or plan_route to the same goal with a lower max_slope_deg, or pick another direction.

### fsd_status {}  is_error=False
FSD ARMED; mode idle; policy replan_on_block=on, caution_slowdown=on; replans used unknown (the rover backend only reports replans in drive results) of 1; route r7, 0 m remaining.
Last hazard: unknown (interpolated) terrain at (546, 450) m.
No blockers.
Next: plan_route then follow_route; fsd_enable first to allow one automatic replan.

### fsd_disable {}  is_error=False
FSD off; mode idle; policy replan_on_block=on, caution_slowdown=on; replans used unknown (the rover backend only reports replans in drive results) of 1; route r7, 0 m remaining.
Last hazard: unknown (interpolated) terrain at (546, 450) m.
No blockers.
Next: plan_route then follow_route; fsd_enable first to allow one automatic replan.

### fly_camera {"target": "marker:m1", "distance_m": 300}  is_error=False
View update sent: mode orbit; target goal A (1900, -400) m.
No viewer is connected, so nothing was displayed; the request was accepted.
Next: describe the scene from tool results, or place_marker / plan_route.

### teleport_rover {"x": 99999, "y": 0}  is_error=True
Error: (99999, 0) is outside the site.

### follow_route {}  is_error=False
ARRIVED - Arrived at (2515, 1363).
Rover now (2514, 1363) m, heading 88 deg, odometer 3642 m, pitch -0 roll 1.8 deg.
Note: route_id was not given, so the most recently planned route r4 was used. Pass route_id explicitly to avoid surprises. animate=false is approximated on the rover backend: its sim always runs in real time, so the drive was run about 10000x faster than real speed.

### find_sites {"target": "unobtainium"}  is_error=True
Error: Unknown target 'unobtainium'. Available targets: carbonate, olivine, clay.

### assess_route {"route_id": "r999"}  is_error=True
Error: No route 'r999'. Plan one with plan_route first. Known: r1, r2, r3, r4, r5, r6, r7, r8.

### search_pois {"query": "zzzz"}  is_error=False
No POIs match. Known POIs: octavia_e_butler_landing, base, sample_01_roubion, sample_02_montdenier, sample_03_montagnac, sample_04_salette, sample_05_coulettes, sample_06_robine, sample_07_malay, sample_08_ha_ahoni, sample_09_atsa, sample_10_swift_run, sample_11_skyland, sample_12_hazeltop, sample_13_bearwallow, sample_14_shuyak, sample_15_mageik, sample_16_kukaklek, sample_17_atmo_mountain, sample_18_crosswind_lake, sample_19_melyn, sample_20_otis_peak, sample_21_pilot_mountain, sample_22_pelican_point, sample_23_lefroy_bay. Known types: base, landmark, named_region, sample_site.
Try: a shorter query (the query matches id, name, type and description), or search_pois() with no filters to list them all.

### sample_terrain {"x": 99999, "y": 0}  is_error=True
Error: (99999, 0) is outside the site (|x|, |y| <= 4096 m).
```

Notes on the edge cases: `site1` was created by an earlier MCP session, so this session's id cache does not know it
(readable error, documented limit); `find_regions` on our side gives a `set:1` whose member `1.1` is a valid goal for the
rover backend (resolved through our `/describe`); `find_locations` without `footprint_radius_m` is an engine error
(test script mistake, shown on purpose).

## Frame mismatch demo (query engine on the synthetic site, rover API on real Jezero)

```text
$ ATLAS_API_URL=http://localhost:8002 ATLAS_ROVER_API_URL=http://localhost:8000 python mcp/server.py
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
[ares-atlas] FRAME MISMATCH: the query engine site 'synthetic' (centre 18.4600 N, 77.4100 E, 512 m) and the rover API site 'jezero' (centre 18.4520 N, 77.4180 E, 8192 m) have origins 654 m apart (dx 450, dy -474). The same x,y means a different place on each: coordinates from find_*/describe tools would send the rover to the wrong place. Do not drive the rover to coordinates from the other backend until both load the same site (ARES_DATA_DIR vs ATLAS_SITE_DIR; see docs/api-compat.md).
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
```

Every tool result then starts with `[FRAME MISMATCH: ...]`, also `get_site_overview`.
With the synthetic query engine and his 2 km playground (same origin, different extent) results start with
`[EXTENT DIFFERS: ...]` instead.

## Browser rover sandbox (`web/src/rover/sandbox`, port 5175 because 5174 was taken by another process)

`npm install` in the sandbox, `ARES_SITE_DIR=<playground> npx vite --port 5175`, his API on the playground at :8003,
our synthetic devapi on :8002, headless Brave via puppeteer-core loading
`http://localhost:5175/?api=http://localhost:8003` and reading the HUD every second.

* The page connected (`API link open (ws://localhost:8003/ws)`) and the MCP tools worked against the same API
  (teleport, marker, plan_route all fine; the browser showed mode `manual` at x -243).
* **API-driven motion did not reach the browser as motion**: `goto_location(animate=true)` and
  `follow_route(animate=true)` returned `STOPPED: stopped_by_user - Manual takeover from the viewer.` in 5 of 5 tries,
  each after 6-10 m. The sandbox in manual mode sends `rover.pose` at 5 Hz even when idle; his `RoverSim.manual_pose`
  cancels the running command on every pose. Without a browser the same drives arrive (transcript 1). Details and
  suggested fix in `docs/api-compat.md` (last section). Not fixed here: both sides are Yousef's.
