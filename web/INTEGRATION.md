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
