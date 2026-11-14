# PRD: Ares Atlas — AI-Navigable 3D Mars (Space Apps 2026, Martian Map)

Nov 14, 2026 · @Rithvik

## Summary

Ares Atlas is a drivable 3D map of Jezero crater, built from NASA orbital data, that an AI agent can operate through an MCP server to scout and plan a Marswalk.

**Challenge fit.** The Martian Map statement asks for a layered, integrated view of a location or route on Mars that combines data from multiple NASA missions and helps a human explorer plan and carry out a Marswalk while doing new science. Every feature below must serve that sentence.

**User.** An astronaut or mission planner preparing an EVA from a base near the Perseverance landing site. Students and the public are the secondary audience.

**Core loop: Ask, Plan, Scout, Walk.**

1. **Ask:** the user tells the AI copilot a goal in plain language, such as a 4-hour EVA that samples the delta front and stays under 15 degrees of slope.
2. **Plan:** the agent calls MCP tools to query terrain, find science targets and compute a safe route with time and consumables.
3. **Scout:** a rover drives the route in the 3D scene while the agent reports hazards it finds.
4. **Walk:** the user drops into ground-level view and follows the route, with real Perseverance photos at nearby waypoints.

**What sets it apart.** Several teams have already published Jezero route planners for this challenge, some with a street view. Our three differentiators are:

- **AI-operable:** an open MCP server lets any AI client drive the rover, query layers and plan routes.
- **Scout before you walk:** a rover simulation tests the route at ground level before a human commits to it.
- **Orbit to ground:** an intro shows how orbital stereo images become the 3D terrain the user stands on.

**Honest framing.** The terrain comes from published NASA and USGS elevation models, which were themselves built from orbital stereo pairs. We show that process and reproduce it on one small patch. We do not claim to rebuild Jezero from raw images in 48 hours.

**Judging.** The plan is built against the five criteria the team supplied: Impact, Creativity, Validity, Relevance and Presentation. The next section maps each score-of-5 description to features and evidence.

## Judging criteria

Each criterion maps to a feature a judge can see in the demo, and each has one owner who is accountable for the evidence.

| Criterion | What a score of 5 requires | How we reach it | Evidence shown | Owner |
|---|---|---|---|---|
| Impact | A significant problem, extensive reach, benefits or inspires a large audience | EVA planning is safety-critical for crews. A public hosted build lets anyone drive Mars, and the open MCP server lets other teams and AI tools build on it. | A plan that rejects the shortest path because it crosses a steep scarp, with time and margin numbers. A public link that works without install. | C and D |
| Creativity | A groundbreaking approach not attempted before, which challenges conventional methods | The map is operated by an AI agent through tools, not only clicked by a person. A rover scouts the route before the human walks it. | Live agent session: prompt in, tool calls visible, rover moves. | D |
| Validity | Scientifically sound, technically feasible, fully developed, highly usable and user-friendly | Real elevation and imagery, stated assumptions, a benchmark against Perseverance's real drives, a complete end-to-end loop, and a first-run experience a newcomer can follow. | Benchmark card, assumptions panel, guided first-run tour, suggested prompts. | A, B and C |
| Relevance | Fully addresses the challenge; NASA data is integral and yields meaningful insights | Every route, hazard and science stop is computed from NASA data. The layered multi-mission view is the product, not a backdrop. | Layer switcher with mission labels; route warnings that quote the data, such as a slope value from the HiRISE model. | A and B |
| Presentation | Clear challenge, solution and significance; engaging, structured story; judged on the demo and the full project | One story: ask, plan, scout, walk. The project page follows challenge, solution, significance, data, validation. | The demo video and a complete project page. | D |

What the rubric adds to the build:

- Usability is scored under Validity. Stream B ships a 20-second guided tour and plain-language labels. Stream D ships three suggested prompts in the chat panel.
- Completeness is scored under Validity. A smaller loop that works end to end beats more features that half work.
- NASA data must drive insight. Every agent answer and route warning cites the layer and value behind it.
- Presentation covers the full project page, not only the video. Stream D treats the page as a deliverable with its own review at hour 38.
- Impact rewards reach. A hosted public build is promoted from optional to MVP if hosting takes under two hours.

Rules that follow from this table:

- Every map layer carries a visible mission and instrument label, because Relevance depends on showing multiple NASA missions.
- Every number shown to the user has a source or is labelled as an assumption, because Validity depends on it.
- The agent's tool calls are visible in the chat panel, because Creativity is only credible if judges see the AI acting.
- Any feature that does not appear in the demo script is lower priority than every feature that does.

## Scope

The MVP is one 8 km square of Jezero crater, working end to end by hour 30. Everything else is stretch or cut.

### MVP (must work in the demo)

- 3D terrain of the Jezero site from HiRISE and CTX elevation data, with HiRISE imagery draped on it.
- Three camera modes: orbit view, rover chase view, and ground-level first-person view.
- Manual rover driving with keyboard, following the terrain surface, with slope and tilt readout.
- At least four toggled layers from at least three missions: imagery, slope hazard, geology or minerals, and rover traverse with waypoints.
- Route planner: start, goal, optional science stops; returns path, distance, time, maximum slope and consumables margin.
- Points of interest: Perseverance waypoints and sample sites, named features, science targets.
- MCP server with the tool set in the contracts section, working from one external AI client.
- In-app chat panel where an agent plans a route and drives the rover, with tool calls visible.
- Validation card comparing our hazard map with the real Perseverance traverse.
- Data credits panel listing every dataset, mission and link.

### Stretch (only after hour 30 freeze criteria pass)

- Agent vision: the agent requests a camera snapshot and describes what it sees.
- Real Perseverance Navcam or Mastcam-Z photos shown at the nearest waypoint in ground view.
- Orbit-to-ground intro animation with a satellite pass and image swaths.
- Mini stereo reconstruction of one patch from two orbital images, shown as a how-it-works panel.
- Whole-planet globe that flies down into the Jezero site.
- Sun position and shadows by local Mars time.
- A second site, such as Gale crater.

### Cut (do not build)

- Full photogrammetry of the site from raw stereo pairs.
- Wheel-soil physics, suspension or rock collision.
- User accounts, saving, multiplayer.
- Mobile layout.
- Global high-resolution terrain streaming.

## Architecture and shared contracts

Four components talk only through five contracts, which the team freezes at hour 2. After that, a contract changes only if all four owners agree.

The API service is the single source of truth for world state. The viewer renders it, the MCP server changes it through REST, and the pipeline feeds both with static files.

Data flows left to right along the top row into static site files. The agent reaches the API only through the MCP server, and the viewer and API exchange state over REST and WebSocket.

### Repository layout

```
ares-atlas/
  contracts/     frozen at hour 2: schemas, OpenAPI file, fixtures, this PRD
    fixtures/    tiny sample terrain, canned API responses, sample GeoJSON
  pipeline/      Stream A: Python, GDAL, rasterio
  data/          gitignored: raw/ downloads, build/ outputs
  web/           Stream B: Vite, React, TypeScript, three.js
    src/panels/chat/   owned by Stream D
  api/           Stream C: Python, FastAPI
  mcp/           Stream D: Python MCP SDK, agent backend
  docs/          submission text, demo script, data credits
```

Ports: web 5173, API 8000, MCP 8765. The API serves `data/build/` as static files under `/tiles` and `/data`. Each stream works on its own branch and merges to main at each checkpoint.

### Contract 1: site coordinate frame

- Site frame is local metres. x is east, y is north, z is elevation in metres as given by the source elevation model.
- Origin is the centre of the region, at lat0, lon0 in the manifest. Latitude is planetocentric and longitude is east-positive.
- Conversion uses a sphere of radius 3,396,190 m: `x = R * cos(lat0) * (lon - lon0)` and `y = R * (lat - lat0)`, angles in radians.
- The matching GDAL definition is `+proj=eqc +lat_ts=<lat0> +lon_0=<lon0> +R=3396190 +units=m`, with northing shifted so the origin is zero.
- In three.js, X = x, Y = z, Z = -y. Headings are degrees clockwise from north.
- Every API takes and returns site-frame metres. Responses also include lat and lon for display.

### Contract 2: tiles and manifest

The region is a square of side 8,192 m. Tiles form a quadtree: level z has 2^z by 2^z tiles, x counts columns from the west, y counts rows from the north.

| Product | Path | Format | Levels |
|---|---|---|---|
| Elevation | `/tiles/height/{z}/{x}/{y}.png` | 257 by 257 px, RGB-encoded, edges shared with neighbours | 0 to 5 (1 m posts at level 5) |
| Imagery and overlays | `/tiles/{layer}/{z}/{x}/{y}.webp` | 512 by 512 px | 0 to 5 everywhere; level 6 (0.25 m per px) in the core 2 km box only |

- Elevation decoding: `elev_m = -10000 + 0.01 * (R * 65536 + G * 256 + B)`.
- A missing tile returns 404 and the viewer keeps the parent tile.
- Layer ids: ortho, ctx, slope, hazard, geology, minerals, thermal.

```json
{
  "site": "jezero",
  "lat0": 18.46, "lon0": 77.41, "radius_m": 3396190,
  "size_m": 8192, "elev_min": -2700, "elev_max": -2000,
  "height": {"max_level": 5, "tile_px": 257},
  "layers": [
    {"id": "ortho", "title": "HiRISE orthomosaic", "mission": "MRO", "instrument": "HiRISE",
     "max_level": 6, "kind": "base", "credit": "NASA/JPL/UArizona/USGS", "url": "..."},
    {"id": "slope", "title": "Slope hazard", "mission": "MRO", "instrument": "HiRISE DTM",
     "max_level": 5, "kind": "overlay", "legend": [[0, "safe"], [15, "caution"], [25, "no-go"]]}
  ],
  "vectors": {"pois": "/data/pois.geojson", "traverse": "/data/traverse.geojson"}
}
```

The lat0, lon0 and elevation values above are placeholders. Stream A sets the real values after checking data coverage.

### Contract 3: analysis files for the API

All rasters share one grid: site frame, 2 m per pixel, 4,096 by 4,096, float32 GeoTIFF, in `data/build/analysis/`.

| File | Content |
|---|---|
| dem.tif | Elevation in metres |
| slope.tif | Slope in degrees |
| rough.tif | Roughness: standard deviation of elevation in a 5 by 5 window |
| layer_<id>.tif | Optional science layers resampled to the grid |
| pois.geojson | Points of interest |
| traverse.geojson | Perseverance path as a line, plus waypoints with sol numbers |

POI properties: id, name, type (sample_site, science_target, landmark, rover_waypoint, base, hazard), x, y, z, lat, lon, mission, source_url, description, science_value (0 to 1), sol, image_urls.

### Contract 4: REST and WebSocket API

Base URL `http://localhost:8000`. The full OpenAPI file lives in `contracts/api.openapi.yaml`.

| Endpoint | Purpose |
|---|---|
| GET /site | Manifest plus profiles and limits |
| GET /terrain/sample?x=&y= | Elevation, slope, roughness, hazard class and layer values at a point |
| POST /terrain/profile | Elevation and slope along a polyline |
| GET /pois?type=&q=&near=x,y&radius= | Search points of interest |
| POST /route | Plan a route: start, goal, via[], profile, max_slope_deg, time_budget_min |
| POST /route/science | Pick and order science stops within a time budget |
| GET /rover/state | Current rover state |
| POST /rover/command | drive, turn, goto, follow_route, stop, teleport |
| POST /view | Ask the viewer to fly the camera, switch mode, toggle layers or highlight POIs |
| POST /view/snapshot | Ask the viewer for a JPEG from the rover camera; returns when the viewer uploads it |
| GET /validation | Benchmark results against the real traverse |
| WS /ws | Event stream in both directions |

**Rover state:** x, y, z, heading_deg, pitch_deg, roll_deg, speed_mps, mode (idle, manual, auto), odometer_m, route_id, status, last_event.

**Route response:** route_id, points[] (x, y, z), distance_m, duration_min, ascent_m, max_slope_deg, hazard_cells, consumables (used and margin), stops[], warnings[], feasible.

**WebSocket events**, all JSON with a type field:

| Direction | Type | Payload |
|---|---|---|
| Server to viewer | rover.state | Rover state, 10 per second in auto mode |
| Server to viewer | route.set | Route response |
| Server to viewer | view.camera | mode (orbit, chase, ground), target x, y, heading, pitch, distance |
| Server to viewer | view.layer | Layer id, visible, opacity |
| Server to viewer | view.highlight | POI ids |
| Server to viewer | view.snapshot_request | Request id |
| Server to viewer | agent.event | Tool name, arguments summary, result summary |
| Viewer to server | rover.pose | Pose from manual driving, 5 per second |
| Viewer to server | view.snapshot | Request id, base64 JPEG |

**Control rule:** in manual mode the viewer owns the pose and reports it. In auto mode the API simulates motion and the viewer only renders. Any key press in the viewer switches to manual and cancels the active command.

### Contract 5: MCP tools

The MCP server is a thin, well-described wrapper over the REST API. It holds no state.

| Tool | Arguments | Returns |
|---|---|---|
| get_site_overview | none | Site summary, layers, profiles, rover state, key POIs |
| search_pois | query, type, near, radius | POI list with coordinates and science value |
| sample_terrain | x, y | Elevation, slope, hazard, layer values |
| get_elevation_profile | points | Distance, elevation and slope series, summarised |
| plan_route | start, goal, via, profile, limits | Route summary with id and warnings |
| plan_science_traverse | start, time budget, interests | Ordered stops and route |
| assess_route | route id | Go or no-go with reasons and consumables margin |
| get_rover_state | none | Rover state |
| drive_rover | distance_m, heading_deg | Final state, stop reason |
| goto_location | x, y or POI id | Final state, stop reason |
| follow_route | route id, speed factor | Progress until done or blocked |
| stop_rover | none | State |
| set_view | mode, target, layers, highlights | Confirmation |
| capture_view | direction | Image from the rover camera (stretch) |

Tool results are short structured text, under about 300 tokens each, with coordinates rounded to the metre. Positions accept either site-frame x, y or a POI id.

### Working without each other

`contracts/fixtures/` holds a synthetic 1 km terrain in the tile format, a sample manifest, sample GeoJSON and canned JSON for every endpoint. Stream B builds against fixture tiles and a mock WebSocket. Stream C builds against fixture rasters. Stream D builds against the canned responses. Real data replaces fixtures by changing one base URL.

## Data

The site is an 8,192 m square of Jezero crater covering the Perseverance landing site and the western delta front, because it has the best open elevation and ground-truth data on Mars.

Jezero gives us 1 m elevation, 25 cm imagery, a real rover traverse to validate against, and real sample sites as science targets. Stream A confirms the exact centre after checking that the square sits inside the HiRISE elevation footprint.

### Datasets

First action for Stream A: open the Resources tab of the official challenge page and add any dataset NASA lists there to this table. Using the listed resources helps the Relevance score.

| Priority | Product | Mission and instrument | Resolution | Used for | Size | Source |
|---|---|---|---|---|---|---|
| P0 | Mars 2020 TRN CTX DTM mosaic | MRO, CTX | 20 m | Fallback terrain; available in minutes | 9.3 MB | USGS |
| P0 | Mars 2020 TRN CTX orthomosaic | MRO, CTX | 6 m | Fallback imagery; outer area | medium | USGS |
| P0 | Mars 2020 TRN HiRISE DTM mosaic | MRO, HiRISE | 1 m | Main terrain, slope, roughness | check page | USGS |
| P0 | Mars 2020 TRN HiRISE orthomosaic | MRO, HiRISE | 25 cm | Main imagery | 3.2 GB | USGS |
| P0 | Perseverance traverse and waypoints | Mars 2020 | per sol | Validation, POIs, ground view anchors | small | Mars 2020 location map GeoJSON; confirm URL |
| P1 | Jezero geologic map | USGS, from MRO data | vector | Geology layer, science targets | small | USGS SIM 3464 GIS package; confirm URL |
| P1 | CRISM mineral maps | MRO, CRISM | about 18 m | Minerals layer: carbonate, olivine, clays | medium | PDS Geosciences Node; confirm product |
| P1 | THEMIS infrared or thermal inertia | Mars Odyssey, THEMIS | 100 m | Surface type: sand against rock | medium | USGS or Mars Trek; confirm product |
| P1 | Perseverance raw images | Mars 2020, Navcam and Mastcam-Z | photo | Ground-view photos at waypoints | on demand | mars.nasa.gov raw images feed; confirm API |
| P1 | Place names | IAU and USGS Gazetteer | vector | Search and labels | small | USGS planetary names; confirm export |
| P2 | Science Investigation CTX DEM mosaic | MRO, CTX | 20 m | Whole-crater context | 87 MB | USGS |
| P2 | MSR TRN HiRISE DTM and orthomosaics | MRO, HiRISE | 1 m | Westward extension over the crater rim | large zip | USGS |
| P2 | Global basemap tiles | Viking, MGS MOLA, others | global | Globe for the intro | streamed | Mars Trek WMTS |

This gives four NASA missions at P0 and P1: Mars Reconnaissance Orbiter, Mars 2020, Mars Odyssey, and Mars Global Surveyor if the globe ships. Rows marked confirm were not opened while writing this PRD, so Stream A verifies them in hour 1 and drops any that cost more than 30 minutes.

### Curation rules

- Record every download in `data/SOURCES.md`: product name, URL, date, file size, citation, licence note.
- Keep raw files untouched in `data/raw/`. All outputs go to `data/build/` and are reproducible by one command.
- Never commit raw data. Commit the pipeline and a script that downloads it.
- Fill elevation gaps by interpolation and write a mask of filled cells. The hazard layer treats filled cells as unknown, not safe.
- HiRISE imagery is greyscale. Any colour tint is false colour and the credits panel says so.
- Check alignment: overlay the traverse on the orthoimage and confirm it follows visible wheel tracks or lands on the known landing point within 10 m.
- Do not download the 11 GB global elevation model. Use streamed tiles for the globe.

### Processing pipeline

Each step is one script in `pipeline/`, run in order by `make data`.

1. `00_fetch.py`: download P0 files, verify sizes, write SOURCES.md. Try a windowed remote read with GDAL /vsicurl/ first so only the region is pulled; fall back to full download.
2. `01_region.py`: choose lat0, lon0; confirm the 8,192 m square is inside the HiRISE DTM footprint; write the site CRS string.
3. `02_warp.py`: reproject elevation and imagery into the site frame with gdalwarp. Outputs: elevation at 1 m and 2 m, imagery at 0.25 m for the core 2 km and 0.5 m for the rest. Where HiRISE has no data, fill from CTX and record the seam.
4. `03_derive.py`: slope with gdaldem slope, roughness, hillshade, and a hazard class raster from the thresholds owned by Stream C.
5. `04_tiles.py`: cut elevation tiles with RGB encoding and shared edges, and imagery and overlay tiles as WebP, for every level.
6. `05_vectors.py`: convert traverse, waypoints, names and science targets into the POI schema with site-frame coordinates.
7. `06_layers.py`: resample geology, minerals and thermal layers to the grid and cut overlay tiles.
8. `07_manifest.py`: write manifest.json with real bounds, levels and credits.
9. `08_check.py`: assert tile counts, decode a tile and compare with the source elevation, and print a coverage report.

### From orbit photos to 3D

The product idea is a 3D model built from pictures a satellite took in orbit. That is how the HiRISE and CTX elevation models were made: two images of the same ground from different angles, matched pixel by pixel, give height.

- **Main path:** use the published elevation models. This is accurate and fast.
- **Demonstration path (stretch):** take one stereo pair or two synthetic views of a 1 km patch, compute a disparity map with OpenCV, and show images, disparity and recovered terrain side by side with the published model and the error.
- Full reconstruction with NASA Ames Stereo Pipeline on a CTX pair is only attempted if someone has it installed and running by hour 6.

The demo and project page must describe this accurately: published models for the site, our own reconstruction for one patch.

## Stream A: data and terrain pipeline

Stream A turns raw NASA files into the tiles, rasters and vectors in contracts 2 and 3, and owns the Validity evidence about data.

**Owns:** `pipeline/`, `data/`, `contracts/fixtures/`, `docs/data-credits.md`. **Stack:** Python 3.11, GDAL, rasterio, numpy, Pillow, shapely.

**Depends on:** nothing. Others depend on it for: fixtures at hour 2, fallback tiles at hour 6, full tiles at hour 14.

### Tasks in order

1. Hour 0 to 2: write `make_fixture.py`, which generates a synthetic 1 km terrain with a crater and a slope, cut into the exact tile and raster formats. Commit the output to `contracts/fixtures/`.
2. Hour 0 to 2: check the official challenge Resources tab and the rows marked confirm in the data table. Update the table.
3. Hour 2 to 6: fallback build. Run the full pipeline on the 9.3 MB CTX elevation mosaic and CTX imagery. Publish real, low-resolution tiles so the other streams see Mars early.
4. Hour 6 to 14: main build with HiRISE elevation and imagery. Publish manifest.json, all tiles and the analysis rasters.
5. Hour 6 to 14, in parallel while downloads run: traverse, waypoints, sample sites and place names into pois.geojson and traverse.geojson.
6. Hour 14 to 22: science layers. Geology first, then minerals, then thermal. Each ships as an overlay tile set and an aligned raster.
7. Hour 14 to 22: science targets. Add 10 to 20 POIs of type science_target with a one-line reason and a science_value, taken from the geologic map units and published Perseverance sampling locations.
8. Hour 22 to 30: data credits file, alignment check, coverage report, and a one-page note on accuracy and limits.
9. Stretch: mini stereo reconstruction panel data; ground-view photo index mapping waypoints to image URLs; whole-crater context tiles.

### Acceptance tests

- `make data` rebuilds everything from an empty `data/build/` with no manual steps.
- `08_check.py` passes: tile counts match the manifest, and decoded elevation differs from the source by less than 0.05 m at 100 random points.
- Adjacent elevation tiles share identical edge values, so the viewer shows no cracks.
- Slope values at five hand-picked places match a manual calculation from the elevation model.
- The landing site POI sits within 10 m of the known landing coordinates when overlaid on the orthoimage.
- Every layer in the manifest has title, mission, instrument, credit and source URL.

### Notes for the agent

- Source rasters are in a global equirectangular projection with standard parallel 0, so east-west distances are stretched at 18 degrees north. Always warp into the site CRS before measuring anything.
- Use `gdalwarp -r cubic` for elevation and `-r lanczos` for imagery when downsampling; use `-r near` for class rasters.
- Build overviews bottom-up: cut the finest level from the warped raster, then make each parent by averaging four children.
- Host the finished `data/build/` somewhere all four laptops can pull it, for example a release asset or shared drive, and post the link at each checkpoint.

## Stream B: 3D viewer, rover drive and ground view

Stream B builds the web app that renders the site, lets a person drive and walk it, and obeys view commands from the API.

**Owns:** `web/` except `web/src/panels/chat/`. **Stack:** Vite, React, TypeScript, three.js with react-three-fiber and drei, zustand for state.

**Depends on:** fixtures at hour 2; contract 4 events. Others depend on it for: the visible demo, the chat panel slot, snapshots.

### Tasks in order

1. Hour 0 to 2: scaffold the app. Agree the WebSocket event list. Write a mock event server script that replays a canned rover drive.
2. Hour 2 to 8: terrain renderer. Load the manifest, build a quadtree of terrain chunks, decode RGB elevation tiles into displaced plane meshes, drape the ortho layer, and switch level of detail by camera distance. Add skirts to hide seams.
3. Hour 2 to 8: orbit camera with fly-to animation, Mars sky colour, distance fog and a sun light.
4. Hour 8 to 14: rover. A simple rover model placed on the terrain by sampling elevation under four wheels to get height, pitch and roll. Keyboard driving with W, A, S, D. Chase camera.
5. Hour 8 to 14: ground view. First-person camera at 1.7 m eye height with mouse look and walking, snapped to terrain.
6. Hour 14 to 20: API link. Connect to WS /ws; render rover.state, route.set, view.camera, view.layer, view.highlight. Send rover.pose in manual mode.
7. Hour 14 to 20: layers and POIs. Layer switcher with opacity and mission labels. POI markers with labels and an info card. Route drawn as a line draped on the terrain, coloured by slope.
8. Hour 20 to 26: heads-up display. Position, heading, slope under the rover, tilt warning, distance driven, route progress, consumables bar from the route response. Minimap. Elevation profile chart for the active route.
9. Hour 20 to 26: snapshot. On view.snapshot_request, render the rover camera to a 768 px JPEG and send view.snapshot.
10. Hour 26 to 30: validation card, data credits panel, loading screen, and a demo mode that hides developer controls.
11. Stretch: ground-view photo popups at waypoints; orbit-to-ground intro with a satellite pass; sun position by local time; globe fly-in.

### Acceptance tests

- Holds 30 frames per second or better on a mid-range laptop at 1080p with the full site loaded.
- No visible cracks between chunks and no popping worse than one level at a time.
- The rover never floats or sinks more than 0.2 m relative to the terrain, and tilts on slopes.
- With the mock event server, the rover follows a canned route and the camera obeys view.camera events.
- Pressing a drive key during an auto drive switches to manual within one frame and reports it.
- Every layer toggle shows its mission and instrument in the legend.
- The app runs from fixtures with no backend when `VITE_USE_FIXTURES=1`.

### Notes for the agent

- Keep three.js units equal to metres and use the axis mapping in contract 1. Put all conversions in one `coords.ts` module.
- Sample elevation on the client from the decoded tile arrays with bilinear interpolation; do not call the API per frame.
- Vertical exaggeration defaults to 1.0. If a toggle is added, the display must state the factor.
- Use a chunk budget: at most about 150 chunks visible, 129 by 129 vertices each after decimating the 257 px tile at far range.
- Stream D owns the chat panel component. Give it a docked right-hand slot of 380 px and access to the shared store.
- Keep WebGL preserveDrawingBuffer off; take snapshots by rendering to a separate render target.

## Stream C: navigation engine and backend API

Stream C builds the service that answers terrain questions, plans routes, simulates the rover and holds world state, and owns the Validity evidence about the model.

**Owns:** `api/`, `contracts/api.openapi.yaml`. **Stack:** Python 3.11, FastAPI, uvicorn, numpy, rasterio, scikit-image, shapely.

**Depends on:** fixture rasters at hour 2; contract 3 files. Others depend on it for: every endpoint in contract 4.

### Tasks in order

1. Hour 0 to 2: write the OpenAPI file and canned responses for every endpoint. Stand up a stub server that returns them, so Streams B and D can connect immediately.
2. Hour 2 to 8: terrain service. Load the rasters once at start. Implement /terrain/sample, /terrain/profile, /site and /pois with text and radius search.
3. Hour 2 to 8: static file serving for /tiles and /data, plus CORS for the web app.
4. Hour 8 to 16: route planner, described below. Implement /route.
5. Hour 8 to 16: rover simulator and WebSocket hub. Implement /rover/command, /rover/state, /view and WS /ws.
6. Hour 16 to 22: science traverse. Implement /route/science, which picks and orders stops within a time budget.
7. Hour 16 to 22: snapshot relay. /view/snapshot sends a request event and waits up to 5 seconds for the viewer's image.
8. Hour 22 to 28: validation. Implement /validation and write a short methods note.
9. Hour 28 to 30: hardening. Input validation, clear error messages the agent can act on, and a /health endpoint.
10. Stretch: walk-back safety rule; day and night limits from sun elevation; an alternative route that trades time for safety.

### Route planner

- **Grid:** the 2 m rasters downsampled to 4 m for planning, 2,048 by 2,048 cells. Refine the final path on the 2 m grid if time allows.
- **Algorithm:** least-cost path with `skimage.graph.MCP_Geometric`, which runs in compiled code and handles diagonal moves. Pure Python A* is too slow on this grid.
- **Cell cost** is seconds per metre: `1 / speed(slope)` multiplied by `1 + w_rough * roughness + w_unknown * filled`. Cells above the slope limit are impassable.
- **Via points:** plan leg by leg and join.
- **Output:** path simplified to about 5 m spacing, with elevation attached, plus the summary fields in contract 4.
- If no path exists, return `feasible: false` with the reason and the nearest reachable point.

### Traveller profiles

All values live in one `profiles.yaml` and are shown in the app as assumptions. Stream C replaces each default with a cited value where one can be found in under 20 minutes, and labels the rest as assumed.

| Profile | Flat speed | Caution slope | No-go slope | Notes |
|---|---|---|---|---|
| astronaut_walk | 0.9 m/s | 15 degrees | 25 degrees | Speed falls with slope; 8-hour EVA limit; reserve margin 20 percent |
| rover_scout | 0.04 m/s real, scaled up for display | 15 degrees | 20 degrees | Real speed is close to Perseverance's top speed; the simulator applies a speed factor |
| crew_rover | 2.5 m/s | 10 degrees | 15 degrees | Pressurised rover concept; optional |

- Slope-to-speed curve: a hiking function of the form `speed = flat * exp(-k * abs(tan(slope) + c))`. This is an Earth-derived shape used as a placeholder and must be labelled as such.
- Consumables: time-based. Used fraction is route duration plus time at stops, divided by the EVA limit. The margin is what remains above the reserve.
- Hazard classes for the overlay: safe, caution, no-go, unknown. Stream A cuts the tiles from these thresholds.

### Science traverse

- Input: start, time budget, optional interests such as carbonate or delta.
- Precompute travel time between the start and the 20 highest-value matching POIs.
- Greedy insertion: add the stop with the best science value per added minute until the budget, including return to start and 15 minutes per stop, is used.
- Return the ordered stops, the joined route and the science value collected.

### Rover simulator

- One background task at 10 ticks per second. In auto mode it advances the rover along the active command and broadcasts rover.state.
- Height, pitch and roll come from the elevation raster under the rover footprint.
- The rover stops and reports a reason when slope under it exceeds the no-go limit, when it reaches the target, or on stop.
- A speed_factor scales simulated time so a 400 m drive finishes in under a minute on screen. The HUD shows real elapsed mission time.
- Blocking commands return when the drive ends or after a timeout, with the final state and stop reason, so the agent gets one clear result per call.

### Validation

- **Hazard agreement:** the share of the real Perseverance traverse length that falls in cells our map classes as safe or caution for the rover profile. A high share supports the hazard map.
- **Route realism:** for three pairs of real waypoints, compare our planned route length and maximum slope with the path the rover actually drove.
- Report both honestly, including where they disagree, with one sentence on why.

### Acceptance tests

- /route across 3 km returns in under 3 seconds.
- No returned route crosses a no-go cell; a unit test checks this on 50 random pairs.
- Planned duration on flat fixture terrain equals distance divided by flat speed within 2 percent.
- follow_route moves the rover to within 3 m of the goal and emits state events throughout.
- A deliberately blocked goal returns `feasible: false` with a usable message.
- Unit tests run on the fixture terrain with no real data present.

## Stream D: MCP server, AI agent, demo and submission

Stream D makes the platform operable by AI, builds the in-app copilot, and owns the story: demo video, project page and submission.

**Owns:** `mcp/`, `web/src/panels/chat/`, `docs/`. **Stack:** Python MCP SDK for the server, a small FastAPI agent backend, any LLM API with tool calling, React for the chat panel.

**Depends on:** canned API responses at hour 2; the chat slot in the web app. Others depend on it for: the demo script, which sets everyone's priorities.

### Tasks in order

1. Hour 0 to 2: write the tool descriptions in `contracts/mcp-tools.md`. Draft the demo script and share it, so the team builds toward it.
2. Hour 2 to 8: MCP server. Implement every tool in contract 5 as a call to the REST API, against the stub server. Support stdio and streamable HTTP transports.
3. Hour 2 to 8: test from an external MCP client, such as a desktop AI app or the MCP Inspector. Fix tool descriptions until the model picks the right tool unaided.
4. Hour 8 to 16: agent backend at port 8766. POST /chat runs a tool-calling loop against the MCP server and streams text and tool events. System prompt below.
5. Hour 8 to 16: chat panel. Message list, streaming replies, a collapsible card per tool call with arguments and result, and suggested prompts.
6. Hour 16 to 22: switch from stub to the real API. Run the three demo scenarios end to end with the rover moving in the viewer.
7. Hour 22 to 28: reliability. Add tool-result guidance for failures, cap the loop at 12 tool calls, and record one good session as a replay file.
8. Hour 20 onward, in parallel: project page text, data credits, AI-use disclosure, screenshots, and the video.
9. Stretch: capture_view with an image result so the agent can describe the scene; an MCP resource exposing the manifest; a prompt template for EVA planning.

### Tool design rules

- Each description states what the tool does, when to use it, units, and one example call.
- Results are compact text with the key numbers first, then a next-step hint, for example that a route is infeasible and which limit to relax.
- Errors come back as readable tool results, not exceptions, and say what to try instead.
- Driving tools are blocking and return a stop reason: arrived, blocked_slope, stopped_by_user, timeout.
- Every tool call also posts an agent.event so the viewer can show what the agent is doing.
- No tool exposes file paths, shell access or anything outside the simulation.

### Agent system prompt outline

- Role: EVA planning copilot for a crew at Jezero crater.
- Always call get_site_overview first in a new session.
- Before recommending a route, call plan_route then assess_route; never estimate distances or slopes from memory.
- State the numbers that came from tools and name the assumptions behind them.
- When scouting, drive in legs, check state after each, and report hazards found.
- Keep replies short; the map shows the detail.
- Refuse to call a route safe if feasible is false or the margin is below the reserve.

### Demo scenarios to pass

| Scenario | Prompt | Expected behaviour |
|---|---|---|
| Plan | Plan a 4-hour Marswalk from base that visits the two best delta science targets and stays under 15 degrees | Finds targets, plans, assesses, shows route and margin |
| Scout | Send the rover to scout that route and tell me about any hazards | Follows the route, reports slope readings and a stop if blocked |
| Explore | Drive 200 m north-west, then show me what is nearby worth sampling | Drives, searches POIs, highlights them, switches the view |

### Acceptance tests

- An external MCP client lists all tools and completes the Plan scenario with no hand-holding.
- All three scenarios pass three times in a row against the real API.
- Every agent claim about distance, time or slope appears in a tool result in the same session.
- The chat panel shows each tool call within one second of it starting.
- With no LLM key, the replay file plays the recorded session through the same panel and viewer.

### Submission ownership

Stream D keeps the checklist in the demo section, collects one screenshot and one paragraph from each stream by hour 34, and submits by hour 44.

## Timeline

Features freeze at hour 30 and the project is submitted by hour 44, leaving a 4-hour buffer. Hours count from your local hackathon start.

Submission is reported as due Sunday November 15 at 11:59 PM local time. Confirm the exact deadline with your local event and shift the hours below if your window is shorter.

| Checkpoint | Hour | Stream A: data | Stream B: viewer | Stream C: API | Stream D: AI and demo | Gate to pass |
|---|---|---|---|---|---|---|
| Kickoff | 0 to 2 | Fixtures committed | App scaffold, mock events | OpenAPI file, stub server | Tool descriptions, demo script draft | Five contracts frozen |
| Solo build | 2 to 8 | CTX fallback tiles published at hour 6 | Terrain renders from fixtures, orbit camera | Terrain and POI endpoints | MCP server against stub, tested in a client | Each stream demos alone |
| Integration 1 | 10 | HiRISE build running | Loads real fallback tiles | Loads real fallback rasters | Calls real terrain endpoints | Real Mars on screen; one MCP tool hits real data |
| Core build | 10 to 20 | Full tiles at hour 14; vectors; first science layer | Rover, ground view, API link | Route planner, rover simulator, WebSocket | Agent backend, chat panel | Each acceptance list half green |
| Integration 2 | 20 | Geology layer live | Draws routes and obeys view events | /route and follow_route working | Plan scenario end to end | Agent plans a route and the rover drives it in the viewer |
| Finish MVP | 20 to 30 | Remaining layers, credits, checks | HUD, layers, snapshot, credits panel | Science traverse, validation | Three scenarios reliable, replay recorded | MVP list complete |
| Freeze | 30 | No new data | No new features | No new endpoints | Script locked | Full demo run-through recorded as backup |
| Polish | 30 to 40 | Accuracy note; stretch data | Visual polish; one stretch item | Bug fixes; methods note | Video, project page, screenshots | Team reviews video at hour 38 |
| Submit | 40 to 44 | Public repo and data script verified from a clean clone | Hosted build verified | Hosted or documented run steps | Submit and re-open the page to check | Submitted |
| Buffer | 44 to 48 | Fixes only | Fixes only | Fixes only | Local judging pitch rehearsal | None |

Working rules:

- Fifteen-minute stand-up at each checkpoint. Each owner shows a running thing, not a status.
- If a gate fails, the team cuts scope from the stretch list first, then from the bottom of the MVP list. Nobody extends the gate.
- Sleep is scheduled. Stagger it so one person is always able to unblock a merge between hours 14 and 30.
- At hour 20, if the HiRISE build is not done, the team commits to the CTX fallback for the outer area and HiRISE for whatever core has finished.

## Demo script and submission checklist

The demo tells one story in five beats: orbit, layers, ask, scout, walk. A 30-second cut and a 2-minute live version use the same beats.

### Beats

| Beat | 30-second cut | 2-minute live version | Criterion served |
|---|---|---|---|
| 1. Orbit | 4 s: camera falls from orbit onto Jezero as terrain resolves | Explain that orbital stereo images become this terrain; show data credits | Presentation, Validity |
| 2. Layers | 4 s: toggle slope hazard and geology | Name each mission as layers switch | Relevance |
| 3. Ask | 7 s: type the Plan prompt; tool calls flash; route appears with time and margin | Show the shortest path rejected for a steep scarp | Creativity, Impact |
| 4. Scout | 7 s: rover drives the route in chase view; a hazard report appears | Agent stops at a slope limit and replans | Creativity, Validity |
| 5. Walk | 6 s: drop to ground view at a science stop; real rover photo appears | Show the validation card against the real traverse | Impact, Validity |
| Close | 2 s: name, challenge, team | Say that the MCP server is open for any AI client | Impact |

Record beats 3 and 4 from the replay file if the live agent is slow. Captions carry the message, because judges may watch without sound.

### Submission checklist

Past years required a public demo link, a public code repository, a project description, a list of NASA data used and a statement of AI use. Stream D confirms each item in the 2026 Project Submission Guide at hour 2, including the allowed demo length and format.

- [ ] Team registered, all four members on the team page, challenge selected: Interplanetary Survival Guide: Martian Map
- [ ] Project name, one-sentence summary and detailed description written against the five criteria
- [ ] Demo video or slides in the allowed format, publicly viewable without sign-in
- [ ] Public repository with licence, README, run steps and data download script
- [ ] NASA data and resources list with links, copied from SOURCES.md
- [ ] Space agency partner and other references listed, including code libraries
- [ ] AI use disclosed: which tools helped write code, and that an LLM runs inside the product
- [ ] No NASA logos or branding used in the app or video
- [ ] Hosted build or clear local run steps tested from a clean machine
- [ ] Submission page re-opened after saving to confirm every link works

## Risks and fallbacks

The two largest risks are slow data preparation and an unreliable live agent; both have a fallback that still produces a complete demo.

| Risk | Likelihood | Effect | Fallback | Owner |
|---|---|---|---|---|
| HiRISE files are slow to download or process | High | No high-resolution terrain by hour 14 | CTX fallback tiles from hour 6; HiRISE for a 2 km core only | A |
| A dataset marked confirm is not easily available | Medium | Fewer layers | Geology map alone meets the multi-mission rule with CTX, HiRISE and rover data | A |
| Terrain renderer performance is poor | Medium | Choppy demo | Cap detail at level 4 outside a 1 km radius; fixed chunk budget | B |
| Live agent is slow or picks wrong tools on stage | Medium | Weak central beat | Replay file through the same UI; tighter tool descriptions; fewer tools | D |
| No LLM API key or quota | Low | No in-app agent | Use an external MCP client for the demo; replay in the app | D |
| Contracts drift between streams | Medium | Integration fails at hour 20 | Frozen contracts, fixtures, two integration checkpoints | All |
| Many teams ship a similar Jezero planner | High | Lower Creativity score | Lead the pitch with the AI operator and scout-before-walk, not the map | D |
| Assumed speeds and limits are challenged | Medium | Lower Validity score | Show assumptions on screen, cite what can be cited, show the traverse benchmark | C |
| Layers misaligned by projection errors | Medium | Routes and imagery disagree | Single site CRS, alignment test against the landing point | A |
| Feature creep into stretch items | High | Unfinished MVP | Hour 30 freeze; demo script decides priority | All |

Open questions for the team to settle in the kickoff:

- Which LLM provider and key will the agent backend use?
- Will the app be hosted, and where will the tile set of several hundred megabytes live?
- Who records and edits the video?

## Kickoff prompts for coding agents

Each owner starts their agent with the shared preamble plus their stream prompt, and attaches this PRD and the `contracts/` folder.

### Shared preamble

```
You are building one stream of Ares Atlas, a 48-hour NASA Space Apps 2026 project for the
challenge "Interplanetary Survival Guide: Martian Map". Read the attached PRD fully first.

Rules:
- The five contracts in "Architecture and shared contracts" are frozen. Never change a path,
  field name, unit or axis convention. If a contract blocks you, stop and tell me.
- Work only inside the folders your stream owns.
- Build against contracts/fixtures first so your stream runs alone. Real data and services
  arrive later by changing one base URL or path.
- Follow your stream's "Tasks in order". Finish and test each task before starting the next.
- After each task, run the acceptance tests that apply and report pass or fail.
- Prefer the simplest thing that passes the acceptance test. No stretch items until I say so.
- Every number shown to a user needs a source or an "assumed" label.
- Keep a short CHANGELOG.md in your folder with what works and how to run it.
```

### Stream A

```
You own Stream A: data and terrain pipeline (folders: pipeline/, data/, contracts/fixtures/).
Start with task 1: write pipeline/make_fixture.py to generate a synthetic 1 km terrain and
emit it in the exact tile, raster, GeoJSON and manifest formats of contracts 2 and 3. Then
build the numbered pipeline scripts in the Data section, first on the CTX fallback products,
then on HiRISE. Use GDAL and rasterio. Write data/SOURCES.md as you download. Finish with
08_check.py and make every Stream A acceptance test pass.
```

### Stream B

```
You own Stream B: the web viewer (folder: web/, except web/src/panels/chat/).
Scaffold Vite + React + TypeScript + react-three-fiber. Put all coordinate conversion in
src/coords.ts using contract 1. Build the quadtree terrain renderer from contract 2 tiles
using contracts/fixtures, then cameras, rover, ground view, WebSocket link, layers, POIs,
HUD and snapshot, in the order of the Stream B task list. Write scripts/mock-ws.ts that
replays a canned rover drive using contract 4 events. Leave a 380 px right-hand slot and a
shared zustand store for the chat panel owned by Stream D.
```

### Stream C

```
You own Stream C: navigation engine and API (folders: api/, contracts/api.openapi.yaml).
First write the OpenAPI file and a stub FastAPI server that returns canned responses for
every endpoint in contract 4, and save those responses in contracts/fixtures/api/. Then
implement the terrain service, route planner with skimage.graph.MCP_Geometric, rover
simulator, WebSocket hub, science traverse and validation, in the order of the Stream C
task list. Keep all profile numbers in api/profiles.yaml. Write unit tests that run on the
fixture terrain and make every Stream C acceptance test pass.
```

### Stream D

```
You own Stream D: MCP server, agent and chat panel (folders: mcp/, web/src/panels/chat/, docs/).
First write contracts/mcp-tools.md with a full description, argument schema and example for
each tool in contract 5. Implement the MCP server with the Python MCP SDK as a stateless
wrapper over the REST API, using the stub server and fixtures, with stdio and streamable
HTTP transports. Then build the agent backend (tool-calling loop, streaming, 12-call cap,
replay file) and the chat panel with visible tool-call cards and three suggested prompts.
Make the three demo scenarios pass. Draft docs/project-page.md following: challenge,
solution, significance, NASA data used, validation, AI use.
```
