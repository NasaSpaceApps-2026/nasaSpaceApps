# web/src/rover: manual rover driving

Browser side of manual rover driving (scope doc `docs/scope-c-d.md`, Yousef task 8). This is plain TypeScript and three.js with no React. It drops into the viewer app (Vite + React + TS + three / r3f). The only runtime dependency is `three`.

## Run the sandbox

```sh
cd web/src/rover/sandbox
npm install
npm run dev        # http://localhost:5174
npm run check      # physics + height-tile assertions (node, no test framework)
npm run build      # tsc --noEmit + vite build
```

The sandbox serves `contracts/fixtures/` as its public dir. It decodes the height tiles itself, builds a displaced mesh with the `ortho` tile draped on it, and adds the rover, the chase camera and a HUD. If `http://localhost:8000/site` is reachable it reads `profiles.rover_scout` and `speed_curve.k` from it. Otherwise it uses built-in defaults (0.04 m/s, x200, caution 15 deg, no-go 20 deg, k 3.5), and the HUD labels them "assumed". The API WebSocket is optional: the link retries with backoff and driving works offline.

Query params: `?base=<tiles+manifest URL>` (for example `http://localhost:8000` once the API serves `/tiles`), `?api=`, `?level=`, `?x=&y=&heading=`, `?world=projection` (Navcam drape, frozen heights), `?world=vision` (vision-loop edits; `llm` is the same), `?world=crater` (1.2 km colourised HiRISE fan front). With no `world`, the server's site is unchanged: the playground when `api/fixtures/playground/manifest.json` exists, otherwise `contracts/fixtures`. The page links in the top right switch worlds. The default start is (100, -60) facing east. Hold W to drive into the fixture scarp at x ~180: the rover slows above 15 deg and stops with `blocked_slope` before 20 deg.

Keys: W/S or Up/Down drive, A/D or Left/Right turn, mouse wheel zooms, R resets.

## Modules

| File | What |
|---|---|
| `coords.ts` | Contract 1: `siteToThree`, `threeToSite`, `headingToDir`, `poseToEulerYXZ`, lat/lon conversions. Heading is clockwise from north, pitch is positive nose-up, roll is positive right-side-down. |
| `heightTiles.ts` | `HeightSampler` interface; `TileHeightSampler` (bilinear, uses the highest loaded level, clamps at the edges); `loadHeightTiles({baseUrl, level})`, `loadHeightTile`, `decodeHeightRGBA`, `tileBounds`. |
| `physics.ts` | `RoverPhysics` (`step(dtWall, {throttle, steer})`), `footprint`, `speedForSlope`, `profileFromSite`, `DEFAULT_ROVER_PROFILE`, `DEFAULT_ROVER_PARAMS`. |
| `controls.ts` | `KeyboardControls`: `getInput()`, `onTakeover(fn)` (fires on a fresh drive-key press and ignores focused inputs). |
| `link.ts` | `RoverLink` (WebSocket with reconnect backoff, `onState`, `onMessage`, `sendPose`), `RemotePoseInterpolator`, `makePoseMessage`, `parseRoverState`. |
| `driver.ts` | `RoverDriver`: physics + keys + link with the Contract 4 control rule. Call `step(dt)` each frame and render `driver.pose`. |
| `roverModel.ts` | `createRoverModel()` (body, mast, RTG, arm, rocker rails, 6 wheels; origin at ground level, forward -Z) and `applyPose(obj, pose)`. |
| `chaseCamera.ts` | `ChaseCamera(camera, {distance, height, stiffness, sampler})` with `update(dt, pose)` and `snap()`. |
| `index.ts` | Barrel. |

### Physics v1 (terrain-following)

- The four corner wheels sit on a 2.7 m wheelbase and 2.3 m track. Body z is the mean of their heights. Pitch is `atan((front-rear)/wheelbase)`, roll is `atan((left-right)/track)`, and slope is the tilt of that plane.
- Speed is `flat_speed * exp(-k * |tan(slope)|)` on the steeper of the slope under the rover and the slope just ahead. Above `caution_slope_deg` it is multiplied by `caution_speed_factor` (0.5, assumed; the API sim should use the same value).
- The rover refuses to move, forward or backward, when the terrain just beyond the leading axle (or the footprint it would move onto) is steeper than `nogo_slope_deg`. It then sets `status: "blocked_slope"` and a `stop_reason`. Moves that reduce the slope are allowed, so it can back away. Leaving the data region gives `blocked_edge`.
- Time: `step` takes wall-clock seconds. Motion advances in mission time (`dt * display_speed_factor`). `speed_mps` is the real or mission speed (0.04 m/s on the flat), and `display_speed_mps` is the speed on screen. Turn rate (40 deg/s) and acceleration (0.6 s to full speed) are display limits and are not modelled on Perseverance.

### Link and control rule

- In manual mode, `rover.pose` is sent at 5 Hz: `{"type":"rover.pose","x","y","z","heading_deg","pitch_deg","roll_deg","speed_mps"}`, with `speed_mps` as mission m/s.
- When `rover.state` arrives with `mode` `auto` or `fsd`, the viewer renders the server pose, interpolated 150 ms behind. Payloads are accepted flat or wrapped in `state`, `data` or `payload`.
- A drive-key press during auto switches to manual at once, continues from the rendered pose and sends a pose immediately. The server should treat a viewer pose as a takeover and cancel its command. Auto states that are still in flight are ignored for 1 s after a takeover.
- When the socket opens, the viewer waits 1 s before sending poses, so that reconnecting during an auto drive does not cancel it.
- A non-auto `rover.state` more than 25 m from the local pose is adopted as a teleport (for example, the agent's `teleport_rover`). A non-auto state that arrives after auto hands control back to local physics at the server's final pose.

## Plugging into the viewer (Rithvik)

```ts
import { RoverDriver, RoverLink, KeyboardControls, ChaseCamera, createRoverModel, applyPose, profileFromSite, DEFAULT_ROVER_PROFILE, type HeightSampler } from './rover';

const sampler: HeightSampler = { sampleHeight: (x, y) => terrain.heightAt(x, y) }; // site-frame metres
const profile = profileFromSite(await (await fetch('http://localhost:8000/site')).json()) ?? DEFAULT_ROVER_PROFILE;
const driver = new RoverDriver({ sampler, profile, controls: new KeyboardControls(), link: new RoverLink(), start: { x: 0, y: 0, heading_deg: 0 } });
const rover = createRoverModel();
scene.add(rover);                    // in r3f: <primitive object={rover} />
const chase = new ChaseCamera(camera, { sampler });

// each frame (r3f: useFrame((_, dt) => { ... }))
const pose = driver.step(dt);
applyPose(rover, pose);
if (cameraMode === 'chase') chase.update(dt, pose);
```

- If your app already has a WS client, you can skip `RoverLink`. Forward `rover.state` messages to the driver, or wrap your socket in the same small interface (`onState`, `onStatus`, `sendPose`, `status`).
- The sampler must return heights in the same frame and with no vertical exaggeration. If the terrain mesh is exaggerated, scale the rover's Y separately rather than feeding scaled heights into the physics.
- `driver.pose` contains everything the HUD needs (`slope_deg`, `ahead_slope_deg`, `mode`, `status`, `stop_reason`). Show `profile.source` next to the limits.
