# Copilot integration in the viewer (Sameer, final pass)

The chat copilot is mounted in the main viewer as a right-hand dock (`web/src/panels/chat/AtlasDock.tsx`, ours). This
page lists every change made in other people's files, why, and how to revert it. Run it all with
`stack/up.sh --with-viewer` (prints the one URL to open).

## Changes in teammates' files

| File | Owner | Change | Why | Revert |
|---|---|---|---|---|
| `web/src/App.tsx` | Rithvik / Caden | +1 import, +12 lines at the end of the JSX (`<AtlasDock ...>` with `getRover`, `cameraMode`, `onEnterSite`, `street`); deleted the line `if (surfaceView === 'street') leaveStreet()` in the camera-mode buttons | Mount. The deleted line sat inside `{surfaceView !== 'street' && ...}`, so it was dead code and TS 7 rejects it (TS2367, `"planet"` vs `"street"`) | delete the import and the `<AtlasDock>` element; restore the one line if wanted (dead either way) |
| `web/src/rover/sandbox/main.ts` | Yousef | +8 lines after `new RoverDriver(...)`: a same-origin `message` listener for `{type:'ares-atlas-pose', x, y, heading_deg}` that calls `driver.teleport` | Street view runs with `link=0` (no API). The dock mirrors the API rover (the one the agent drives) into the iframe, converted to world-local metres, so `drive_to`, `teleport_rover`, `follow_route` are visible through the buggy | delete the listener |
| `web/src/rover/sandbox/vite.config.ts` | Yousef | `port`, `origin`, `hmr.clientPort` read `SANDBOX_PORT` / `VIEWER_PORT` env (defaults 5174 / 5173) | `5173`/`5174` are often taken (another project on this laptop). Defaults unchanged | restore the literals |
| `web/vite.config.ts` | Rithvik | same two env vars for the viewer port, sandbox port, spawn args and `/sandbox` proxy target (defaults unchanged) | same | restore the literals |
| `web/src/rover/buggy/geom.ts`, `chaseCamera.ts`, `link.ts` | Yousef | constructor parameter properties rewritten as explicit fields (`HelixCurve`, `ChaseCamera.camera`, `RemotePoseInterpolator.delayMs`); behaviour identical | `erasableSyntaxOnly` (TS1294) failed `npm run build` | none needed |
| `web/package.json` | Rithvik | added `three@0.169.0` (dependency) and `@types/three@0.169.0` (dev), the versions the sandbox pins | `src/rover` imports `three`; it was only resolved by luck | remove the two lines |
| `web/tsconfig.app.json` | Rithvik | `exclude: ["src/**/*.test.ts"]` | our vitest files live next to the source and `vitest` is installed only in `chat-dev/`; they run with `cd chat-dev && npx vitest run`. No strictness flag was changed | remove the line |

## What the dock does

- Highlights: `find_locations` (pads with slope and walk time), `find_sites`/`find_regions` (evidence tier), `search_pois`,
  `nearest`, `place_marker`, stops, routes. Labels are built only from fields the tool returned.
- Result data: the agent does not forward MCP `structuredContent`, so `features.ts` parses the result text (`extractFromText`);
  if `structured` is ever forwarded it is used instead. Routes: `plan_route` / `drive_to` text has no polyline, so the dock
  fetches `GET :8000/route/<id>` and draws it only if its last point is the goal in the text.
- Camera: result rows fly there (click a row), `fly_camera`/`set_view` follow the API WebSocket (`view.camera`), and when
  the globe (`planet` mode) is showing, a tool result first switches the viewer to the site (`onEnterSite`, like the "Site tools" button).
- Rover: follows `rover.state` from `ws://localhost:8000/ws` (read only, it never sends a pose). Street view: the pose is
  mirrored into the sandbox iframe only while it lies inside that patch (`BUBBLES` centre, `sizeM`); otherwise the dock says so.
- Layout: sets `--dock-w` on `.app`; `dock.css` shrinks `.cesium` and `.street-frame` by it and moves `.pegman` and `.map-back`
  (also moved to `top: 56px`, below the sandbox's world links). Collapsible (tab on the right edge); starts closed under 900 px width.
- Frame check: `GET :8000/site` lat0/lon0 vs the viewer manifest; a red banner on mismatch. Verified: both are 18.452 / 77.418, 8192 m.

## Frame

The viewer must draw in the frame the backend uses. `VITE_SITE_DIR` for `usage.md` commands is `contracts/fixtures` (a synthetic
1 km site, 18.46/77.41), which is a different place. `stack/build_viewer_site.sh` builds the viewer tiles for our real Jezero
site (HiRISE DTM `data/raw/hirise/window_18.452_77.418_4096.tif`, same lat0/lon0/size as `data/build/site/jezero`) into
`stack/cache/work/data/build` (gitignored, about 2 min, copies of `pipeline/*.py` run there so `data/build` is never written;
the M20 traverse/waypoints come from `data/raw` or are downloaded once from NASA/JPL MMGIS). `stack/up.sh --with-viewer` serves it via
`VITE_SITE_DIR`. Not chosen: the `--frame crater` crop (1.8 km): the viewer has no tiles for that frame and would need the red banner.
Street view uses the sandbox's `crater` world; the dock converts site metres to that world with the bubble centre (frame A offset
(+1000.85, +1766.39) m), so the agent's coordinates stay in the real-Jezero frame everywhere.

## Ports

Defaults 5173 (viewer) and 5174 (sandbox). `stack/up.sh --with-viewer` takes the next free ports when they are held and prints the URL.
By hand: `cd web && VIEWER_PORT=5175 SANDBOX_PORT=5176 VITE_SITE_DIR=../stack/cache/work/data/build npm run dev`.

## Build

`cd web && npm run build` and `npm run lint` pass (lint: 5 pre-existing warnings, no errors). Tests: `cd chat-dev && npx vitest run`.

## Visual polish pass (demo-ready, final)

Teammates' files touched, all surgical:

| File | Owner | Change | Why | Revert |
|---|---|---|---|---|
| `web/src/rover/heightTiles.ts` | Yousef | new `decodePngExact` (pure JS PNG inflate + unfilter via `DecompressionStream`), used first by `decodeHeightPng`; the canvas path stays as fallback | **Street-view artifact cause.** Height tiles encode metres in RGB; the canvas readback is not bit-exact in Brave (fingerprint "farbling" flips low bits). One bit in the RED byte is 655.36 m, so ~0.3% of posts became spikes (the orange cones) and pits (the black/orange striped walls are the stretched ortho on their vertical sides). Not a patch-edge skirt: the tile data is clean (checked with PIL) | restore the old body of `decodeHeightPng` |
| `web/src/rover/sandbox/main.ts` | Yousef | sky/fog colour `0xc8946c` (dusty butterscotch) instead of dark `0x2a2522`; fog 0.12-0.85 x patch size | the patch edge now dissolves into haze instead of a dark band | restore the two colour literals |
| `web/src/places.ts` | Rithvik | waypoints: 3.5 px translucent, every 6th visible from afar, rest within 1.2 km, labels within 220 m; traverse line pale yellow with dark outline | clutter, and the old orange line vanished on Mars-coloured imagery | restore previous values |
| `web/src/App.tsx`, `App.css` | Rithvik | controls help folded into `<details>`; site camera framing (target -250,-450, pitch -45, 9.5 km); `onEnterSite` flies 2.5 s instead of jumping; ortho layer saturation 0.78 / contrast 1.12 | demo path | revert hunks |

Ours: `panels/chat/overlay.ts` (halo under highlights, outlined rings, cyan routes), `prompts.ts` (chips = docs/demo-script.md beats).

Imagery: `stack/fetch_ortho.py` reads the USGS `JEZ_hirise_soc_007_orthoMosaic_25cm_Ortho_blend120.tif` (tiled LZW, orthographic) over the 8 km site via range requests, 4x4 mean to 1 m (about 1 min) into `stack/cache/dl/`; `stack/fetch_viking.py` (copy of `pipeline/00b_fetch_color.py` with urllib) fetches the Viking colour source; `stack/build_viewer_site.sh` links both into the work dir and Caden's `build_tiles.py` colourises. Layer title/credit: "HiRISE orthomosaic, colourised (false colour)", "NASA/JPL/UArizona/USGS. HiRISE is greyscale: colour from Viking MDIM 2.1 ... Not true colour." If the fetch fails the build falls back to the DTM hillshade. Rebuild: `stack/build_viewer_site.sh --rebuild`.


## UI redesign (Nov 15, viewer shell)

Layout: slim top bar (Ares Atlas, one **Mars / Jezero / Drive** switch, place search, Layers popover, "?" help popover), the stage (globe or
street-view iframe, with the copilot dock on the right), and one bottom status strip (cursor lat/lon/elevation, rover state, Follow rover
toggle, Cesium data attribution). Keys: 1/2/3 switch modes (also from inside the street view), Esc closes popovers.

| Was | Now |
|---|---|
| Left panel with text block, five camera buttons (orbit/site/ground/rover/planet), two "Drive" buttons, "Site tools", Map button | Removed. Mars/Jezero/Drive switch in the top bar; Drive always opens the Jezero fan street view (the sandbox's own crater/olympus links switch worlds) |
| Layer checkboxes + traverse + place names in the left panel | Layers popover (Jezero only; mission/instrument as secondary text, imagery tagged false colour) |
| "Controls" details block | "?" popover |
| `drive` / `status` / `hud` text in the left panel | Status strip |
| Cesium credit box bottom-left | Cesium `creditContainer` moved into the status strip (attribution kept) |
| orbit camera | Removed (see below). Jezero keeps mouse rotate/tilt/zoom, a Reset view icon and a Ground view toggle |
| Dock overlay, 380 px, brown theme | Dock sits below the top bar; tokens shared via `web/src/index.css` (dock.css/chat.css fall back to the old values when the tokens are absent, so chat-dev on :5199 is unchanged) |
| Default view `site` | Default view is Mars (`?view=site` still opens Jezero; `window.__rover` exists only outside Mars) |

**Orbit bug**: `fly('orbit')` flew to 4000 km over Jezero, but the site view clips the globe to the 8 km site rectangle (and the ion Mars tileset is
absent without a token), so at that altitude only a black void and a tiny patch remained. The planet view is the orbital view, so orbit was removed.

New files: `web/src/ui/{TopBar,StatusBar,NoWebGL}.tsx`, `ui.css`. WebGL failure shows a full-page message (`NoWebGL`) instead of Cesium's error box.
The street-view iframe is same-origin, so on load App injects a small style (hides the sandbox's terrain/imagery text block and the
non-world links) and forwards 1/2/3 to the page; the sandbox files themselves are untouched.

Real-time agent drives: `AtlasDock` follows `rover.state`; in auto/fsd it calls `CesiumRover.pushRemote` (150 ms interpolation, smooth at 10 Hz) instead of
teleporting, draws `route.set` routes, and reports `DriveInfo` (remaining metres by projecting onto the route) to App. App auto-switches to the chase
camera when a drive starts (dragging the map or the Follow rover toggle hands the camera back) and shows a progress chip. In street view with the rover
outside the patch the dock shows one line plus "Watch in Jezero view".

## Interactivity pass (Nov 15)

All in `web/` (no changes to mcp/, agent/, devapi/, atlas_core/, api/). New: `src/interact/{Interactions.tsx,labels.ts,api.ts,words.ts,interact.css}`.

| Interaction | How |
|---|---|
| **Pegman fix** | Root cause: the Jezero/Olympus patches are ~2 km on a 3,400 km planet, sub-pixel at globe zoom, so the exact lat/lon hit test almost never matched. The drop now snaps to the nearest pin within 48 px (visible side of the globe only), starting at the patch centre. While dragging: ring on the pin and "Drop here to drive: <patch>" hint; a miss says so. Verified with real mouse events in headless Brave for both pins |
| Terrain click (Jezero) | Anchored info card: lat/lon, elevation, slope, geologic unit, data quality, minerals in plain words (confirmed by the rover at Sample N (citation), seen from orbit (strong/weak), inferred, no orbital data here). Actions Drive here / Ask the copilot / Drop marker. Esc or x closes |
| Place labels | HTML labels over the canvas (crisp, clickable, declutter by priority and camera height): landing site (merged with the assumed base), named regions, sample sites (grouped by spot), IAU names (Belva...). The old Cesium name labels are gone; the Layers "Place names" toggle controls these. Click opens a place card with sample/region facts and suggested questions (sent to the copilot) |
| Measure (button + M) | A then B: line on terrain, dashed cyan walking route, card with straight distance, walking time, steepest slope, climb, hoverable elevation sparkline. Esc cancels |
| Highlights | Hover tooltip, click opens a card (kind, tool that found it, tier/radius, terrain facts; routes: length + elevation profile). Markers can be removed. Hit test is screen-space (`CopilotOverlay.hitAt`) because `scene.pick` missed the marker points in software rendering |
| Globe pins | Pins are HTML labels + dots; click opens a card: Explore Jezero / Drive here (street view) / Fly in / Ask |
| `view.camera` mode `planet` | WS message (flat or under `payload`) `{mode:"planet", lat, lon, height_m?, label?}`: switches to Mars, flies there, shows a pink labelled pin. Dev hook `window.__planetCamera({lat,lon,label})` |
| Follow cam | Auto-follow only when this tab started the drive (chat drive tools `drive_to/goto_location/drive_rover/follow_route`, or Drive here); other clients' drives only show the chip. On arrival the camera flies out to an overview of the route; the Follow rover toggle stays |

Endpoints used from the browser (both send CORS `*`): devapi :8001 `POST /describe`, `GET /pois?limit=100`, `POST /terrain/profile`, `POST /route` (profile `eva_walkable`; stores a route in devapi only); rover API :8000 `POST /rover/command {cmd:goto, wait:false}` (real time: 10 Hz `rover.state` over the WS), `GET/POST/DELETE /markers`, WS `/ws`.

Gaps: no explicit `realtime` flag in the API (`wait:false` + display speed factor is the real-time path); `ViewReq.mode` does not accept `planet` yet (the other builder's emit path); `GET /pois` is capped at 100; walking route times use the assumed pace; `scene.pick` on thin entities is unreliable in SwiftShader (fallback added). Test markers created while verifying were removed again through the UI.

## Explore toolset (Nov 15, Jezero tab)

New in `web/src/explore/` (ours): `ExplorePanel.tsx` (drawer, tools, tour, keys), `OverlayManager.ts` (overlay PNGs on Cesium), `ActiveOverlays.tsx` (legend cards),
`overlayApi.ts` (pure helpers: URLs, site rectangle, debounce, stats lines), `historyData.ts`, `tour.ts`, `scene.ts`, `explore.css`. Tests: `src/panels/chat/explore.test.ts`
(21 tests; lives there because `chat-dev` vitest already includes that folder). `scripts/overlay-stub.mjs` is a stand-in for the backend contract.

| Tool | What it does |
|---|---|
| Ancient lake | Slider from the site minimum to maximum elevation (m), ticks at the named levels of `docs/lake-levels.md` (hover = what it is + citation; click snaps), presets, stats line ("Lake level -2,410 m: 64.7 km² underwater, deepest 228 m"), caveat in the legend card |
| Sun & shadows | LTST 05:00-19:00 slider, sol field (default = today's mission sol, computed from the touchdown date), play (steps 0.5 h, waits for each frame, prefetches the next, loops), sun elevation / sunrise / sunset line |
| Science layers | Chips: Olivine, Carbonate, Clay (`fe_mg_smectite`), Geology, Slope hazard. Own slot, so it sits under any other tool. Legend, source, caveat from the overlay JSON |
| Reach (EVA) | "Click a start point" (crosshair, hint, Esc cancels) or "Use landing site"; budget 1-8 h, reserve 10/20/30% (20% default); re-fetches as sliders move |
| Viewshed | Click a point or use the landing site; eye height 1.7 m astronaut / 2 m rover mast |
| Rover history | Sol slider grows the traverse (from `/data/traverse.geojson`, per-sol segments) and sample-site pins appear on their sol (from `GET /pois`, `sol` field); play button; "km driven in this map, N of 23 samples sealed" + latest sample. The static traverse is hidden while active and restored after |
| Guided tour | 6 stops (landing site, South Séítah, delta front, Belva crater, margin unit, lake at -2410 m): camera flight, numbered pin, overlay, caption, source line, Prev / Next / Exit; Esc exits, Left/Right arrow keys step. Captions come from `docs/lake-levels.md`, `docs/science-validity.md` and the sample POI descriptions; the `cite` field of each stop in `tour.ts` names the source |

UI: left tool rail gets a compass button (`aria-pressed`), key **E** toggles the drawer (Jezero view only). The drawer sits right of the rail, below the top bar, and is capped
at `100% - --dock-w`, so it never covers the copilot dock; the tour caption is centred in the stage minus the dock. Esc: leaves the tour, else cancels a pick, else stops a
play loop. Leaving the Jezero view (Mars / Drive) clears every overlay, tool and the tour. Slider requests are debounced (220-260 ms, flushed on release), the last image stays
until the new one has loaded (a new layer is added, the old one removed 250 ms later), stale answers are dropped by a per-slot token, up to 40 frames are cached as blobs.

### Files touched in teammates' code

| File | Change | Revert |
|---|---|---|
| `src/App.tsx` | import, `exploreOpen` + `pickRef` state, compass button in `.viewtools`, `<ExplorePanel .../>` before `<AtlasDock>`, `pickRef` prop to `Interactions` | delete those lines |
| `src/interact/Interactions.tsx` | optional `pickRef` prop: while set, a terrain click inside the map calls it (site metres) instead of opening the info card; crosshair cursor | delete the `if (pr.pickRef?.current)` block |

Measure and Explore picking are mutually exclusive (turning one on turns the other off); the info card, measure tool and copilot highlights are otherwise unchanged.

### Overlay contract (devapi, default `:8001`)

Implemented by `devapi/routes_overlay.py`; contract, params and stats keys are in `devapi/OVERLAYS.md`. The viewer uses `GET /overlay/kinds` (lake `named_levels` for the ticks, `sun.sol` default),
`GET /overlay/{kind}.png|.json` and `GET /traverse.geojson` (Rover history: one LineString with per-vertex `x`/`y`, waypoints with `sol` + `vertex_index`; runs are split where the clipped line jumps >120 m).
`overlayApi.statLine` reads the documented stats keys; a missing field only drops its clause. Service location: `?overlay=http://host:port`, else `VITE_OVERLAY_URL`, else the engine URL
(`VITE_ENGINE_URL` or `:8001`, so it works once the stack restarts with the new devapi). `scripts/overlay-stub.mjs` remains as an offline stand-in (`?overlay=http://localhost:8091`).
Esc order: tour, pick mode, play loop, active tool, then the drawer.

### Copilot hook: `window.atlasOverlay`

```js
await atlasOverlay.show('lake', { level: -2450 })           // opens the drawer, selects the tool, shows it; returns { ok, error? }
await atlasOverlay.show('sun', { ltst: 15.5, sol: 2040 })
await atlasOverlay.show('envelope', { x: 1844, y: -433, budget_min: 360, reserve: 0.2 })
await atlasOverlay.show('viewshed', { x: 1844, y: -433, h: 2 })
await atlasOverlay.show('evidence', { layer: 'carbonate' }) // science kinds: evidence | geology | slope (own slot, stays under a tool overlay)
atlasOverlay.clear('lake')   // or clear() for everything
atlasOverlay.setOpacity('lake', 0.5)
atlasOverlay.state()         // { main: {kind, params, meta, loading, error, opacity}, science: {...} }
atlasOverlay.base()          // overlay service URL
```
Outside the Jezero view `show` first switches to it. Unknown kinds return `{ ok: false, error }`. Extra params are forwarded to the backend untouched.

### Verified

`npm run build` and `npm run lint` pass (0 errors; the new files add react-hooks style warnings of the kind the repo already has). `cd chat-dev && npx vitest run`: all pass (21 new in explore.test.ts on top of the existing 48; the count has since grown).
Headless Brave (SwiftShader): lake / sun / layers / reach / history / tour screenshots in `docs/explore_*.png`, a 10-event slider drag sends one request, sun play steps frames and Esc stops it,
Mars/Jezero round trip leaves the imagery stack at its baseline (4 layers), `atlasOverlay` calls work. Final screenshots were taken against the real devapi (port 8092, new code).

### Mission base marker (Sameer, Nov 15)

The assumed base (POI type `base`, "Mission base (assumed)", from the API `/pois`) now has its own always-visible marker instead of being folded into the landing label. Verified in headless Brave: `scratchpad/pp/base_default.png`, `base_zoom.png`, `base_card.png`. Revert: undo the edits below.

| File | Change | Why |
|---|---|---|
| `src/interact/words.ts` | `PlaceKind` gains `'base'`; `placesFromPois` emits a separate `base` place (priority -1, name from the POI, so "(assumed)" stays); `placeSummary`/`placeQuestions` text for it | base was hidden under / merged into the Butler Landing label |
| `src/interact/labels.ts` | `LabelItem.kind` gains `'base'`; new optional `always` (skips declutter, still reserves space) and `dy` (screen-px offset, used in overlap checks and positioning) | keep the base label from being decluttered and let it sit above the landing label |
| `src/interact/Interactions.tsx` | base labels get `always: true, dy: -34`; hint and card badge text for `base` | wires the above; clicking still opens the normal place info card |
| `src/interact/interact.css` | `.plabel--base` styles: green pill, dome icon, leader line and ring to the true spot | distinct from other labels |

### Same-origin backend proxy (Sameer, Nov 15)

Bug: in the user's Retina Brave/Chrome the Jezero map had no POI labels (Delta Front, Margin Unit, Butler Landing, Mission base); only IAU names showed. The viewer called `http://<hostname>:8001/pois` and `:8000` cross-port; when those fetches fail in a real browser (localhost -> ::1 while backends bind 127.0.0.1, or blocked local cross-port requests) `fetchPois()` silently returned `[]`. Fix: the browser now talks only to the viewer's own origin; Vite proxies to the backends. Env overrides (`VITE_ENGINE_URL`, `VITE_API_URL`, `VITE_OVERLAY_URL`, `?overlay=`) still win. Revert: undo the edits below.

| File | Change | Why |
|---|---|---|
| `vite.config.ts` | dev + preview proxy: `/__engine` -> 127.0.0.1:8001, `/__rover` -> :8000 (ws on), `/__agent` -> :8766; ports via `ENGINE_PORT`/`ROVER_PORT`/`AGENT_PORT`, prefix stripped | same-origin access, no CORS or IPv6 issues; SSE from the agent streams through (verified) |
| `src/interact/api.ts` | `ENGINE`/`ROVER_API` default to `/__engine`, `/__rover`; error message uses `new URL(url, location.href)`; `fetchPois` retries 5x with backoff and `console.warn`s instead of failing silently | labels, describe, routes, markers, drive, overlays (ExplorePanel uses `ENGINE`) all follow |
| `src/panels/chat/useChat.ts` | `DEFAULT_AGENT_URL` -> `/__agent` | chat + agent health (SSE) same-origin |
| `src/panels/chat/AtlasDock.tsx` | `DEFAULT_AGENT` -> `/__agent`; `DEFAULT_WS` -> `ws(s)://<host>/__rover/ws`; (frame check, route fetch derive from it) | rover WebSocket and `/site`, `/route` calls same-origin |

Not changed: `rover/link.ts` default and `rover/sandbox` (the dock passes its own URL; sandbox is a separate app on 5174).
