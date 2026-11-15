# Planet and street view Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show the whole Mars globe in Cesium, with the Jezero fan and the Olympus Mons scarp as sharper bubbles, and enter the rover street view by dragging a pegman onto one of those bubbles.

**Architecture:** One page, two views. Planet view is the existing Cesium viewer (`web/`) with the ion Mars tileset left unclipped, plus two ground rectangles that use the bubble orthophotos. Street view is the existing three.js sandbox scene for that bubble (`?world=crater` or `?world=olympus`), shown in place of the globe. A pegman drop is accepted only inside a bubble. Dropping on the rest of Mars does nothing, the same way Street View refuses a place with no coverage. The sandbox keeps the high-resolution mesh, the sand grain, and the buggy. The globe does not try to draw that mesh for the whole planet.

**Tech Stack:** React 19, Cesium 1.147, the existing `web/src/rover/sandbox` Vite app, TypeScript, Vitest is not set up in `web/` so pure logic is tested with `node --experimental-strip-types` or a tiny `tsx` assert script. Do not add a new test framework.

## Global Constraints

- Planet imagery outside the bubbles is the Cesium ion Mars asset `3644333`. It needs `VITE_CESIUM_ION_TOKEN`. Without the token the sphere still shows, and the two bubbles still show their own orthophotos.
- Do not render a unique mesh of all of Mars. The globe is streamed. Only the bubbles are local high-resolution terrain.
- Jezero bubble: center latitude `18.4818`, longitude `77.4358` east, size `1800` m, sandbox world `crater`.
- Olympus bubble: center latitude `21.5548278127083`, longitude `222.31978845442714` east, size `2236` m, sandbox world `olympus`.
- Bubble local frame: x east, y north, origin at the bubble center. Same frame the sandbox already drives.
- Radius for conversions: `3396190` m.
- Pegman accepts a drop only when the ground point is inside a bubble square. Outside, the drop is refused and the view stays on the planet.
- Street view starts the rover at the drop’s local x,y, heading `0`.
- Do not change rover physics, the vision-loop model format, or the bubble tile files.

---

## File structure

- Create `web/src/bubbles.ts` — catalog and lat/lon ↔ local metres. No Cesium imports, so it can be tested in Node.
- Create `web/src/pegman.ts` — hit test from a ground longitude/latitude to a bubble and a start pose. No Cesium imports.
- Create `web/scripts/bubbles.check.ts` — the tests for those two modules.
- Modify `web/src/App.tsx` — planet view leaves the globe whole, draws both bubble footprints, hosts the pegman, and swaps in street view.
- Modify `web/src/App.css` — pegman button and the street-view frame.
- Modify `web/package.json` — `"check:bubbles": "node --experimental-strip-types scripts/bubbles.check.ts"`.
- The sandbox already reads `?world=` and `?x=&y=&heading=`. Do not change its physics. Only confirm those query params still set the start pose.

### Task 1: Bubble catalog

**Files:**
- Create: `web/src/bubbles.ts`
- Create: `web/scripts/bubbles.check.ts`
- Modify: `web/package.json`

**Interfaces:**
- Consumes: nothing
- Produces: `BUBBLES`, `Bubble`, `localMeters(bubble, latDeg, lonDeg)`, `covers(bubble, latDeg, lonDeg)`

- [ ] **Step 1: Write the failing check**

```ts
import assert from 'node:assert/strict'
import { BUBBLES, covers, localMeters } from '../src/bubbles.ts'

const jezero = BUBBLES.find((b) => b.id === 'crater')!
assert.equal(jezero.sizeM, 1800)
assert.equal(covers(jezero, jezero.lat, jezero.lon), true)
assert.equal(covers(jezero, 0, 0), false)
const atCenter = localMeters(jezero, jezero.lat, jezero.lon)
assert.ok(Math.abs(atCenter.x) < 1 && Math.abs(atCenter.y) < 1)

const olympus = BUBBLES.find((b) => b.id === 'olympus')!
assert.equal(covers(olympus, olympus.lat, olympus.lon), true)
assert.equal(covers(olympus, jezero.lat, jezero.lon), false)
console.log('bubbles ok')
```

- [ ] **Step 2: Run it and see it fail**

Run: `cd web && node --experimental-strip-types scripts/bubbles.check.ts`
Expected: FAIL, cannot find module `../src/bubbles.ts`

- [ ] **Step 3: Implement the catalog**

```ts
export interface Bubble {
  id: 'crater' | 'olympus'
  title: string
  lat: number
  lon: number
  sizeM: number
}

const R = 3396190

export const BUBBLES: Bubble[] = [
  { id: 'crater', title: 'Jezero fan', lat: 18.4818, lon: 77.4358, sizeM: 1800 },
  { id: 'olympus', title: 'Olympus Mons scarp', lat: 21.5548278127083, lon: 222.31978845442714, sizeM: 2236 },
]

export function localMeters(bubble: Bubble, latDeg: number, lonDeg: number): { x: number; y: number } {
  const lat0 = bubble.lat * Math.PI / 180
  const x = R * Math.cos(lat0) * (lonDeg - bubble.lon) * Math.PI / 180
  const y = R * (latDeg - bubble.lat) * Math.PI / 180
  return { x, y }
}

export function covers(bubble: Bubble, latDeg: number, lonDeg: number): boolean {
  const { x, y } = localMeters(bubble, latDeg, lonDeg)
  const half = bubble.sizeM / 2
  return Math.abs(x) <= half && Math.abs(y) <= half
}
```

- [ ] **Step 4: Run the check**

Run: `cd web && node --experimental-strip-types scripts/bubbles.check.ts`
Expected: `bubbles ok`

- [ ] **Step 5: Commit**

```bash
git add web/src/bubbles.ts web/scripts/bubbles.check.ts web/package.json
git commit -m "Add the Jezero and Olympus bubble catalog."
```

### Task 2: Pegman drop rule

**Files:**
- Create: `web/src/pegman.ts`
- Modify: `web/scripts/bubbles.check.ts`

**Interfaces:**
- Consumes: `covers`, `localMeters`, `BUBBLES` from `web/src/bubbles.ts`
- Produces: `dropOnBubbles(latDeg, lonDeg): StreetDrop | null`
- `StreetDrop` is `{ world: 'crater' | 'olympus'; x: number; y: number; heading: number }`

- [ ] **Step 1: Add the failing cases to the check script**

```ts
import { dropOnBubbles } from '../src/pegman.ts'

const hit = dropOnBubbles(18.4818, 77.4358)
assert.equal(hit?.world, 'crater')
assert.ok(hit && Math.abs(hit.x) < 1 && Math.abs(hit.y) < 1)
assert.equal(hit?.heading, 0)

const mons = dropOnBubbles(21.5548278127083, 222.31978845442714)
assert.equal(mons?.world, 'olympus')

assert.equal(dropOnBubbles(0, 0), null)
```

- [ ] **Step 2: Run and see it fail**

Run: `cd web && node --experimental-strip-types scripts/bubbles.check.ts`
Expected: FAIL, `pegman.ts` missing

- [ ] **Step 3: Implement the drop**

```ts
import { BUBBLES, covers, localMeters, type Bubble } from './bubbles'

export interface StreetDrop {
  world: Bubble['id']
  x: number
  y: number
  heading: number
}

export function dropOnBubbles(latDeg: number, lonDeg: number): StreetDrop | null {
  const bubble = BUBBLES.find((item) => covers(item, latDeg, lonDeg))
  if (!bubble) return null
  const { x, y } = localMeters(bubble, latDeg, lonDeg)
  return { world: bubble.id, x, y, heading: 0 }
}
```

- [ ] **Step 4: Run the check**

Run: `cd web && node --experimental-strip-types scripts/bubbles.check.ts`
Expected: `bubbles ok`

- [ ] **Step 5: Commit**

```bash
git add web/src/pegman.ts web/scripts/bubbles.check.ts
git commit -m "Accept a pegman drop only inside a bubble."
```

### Task 3: Planet view shows both bubbles on the whole globe

**Files:**
- Modify: `web/src/App.tsx`

**Interfaces:**
- Consumes: `BUBBLES` from `web/src/bubbles.ts`
- Produces: two Cesium ground rectangles, one per bubble, using that bubble’s level-0 ortho. The ion tileset is not clipped to a single site while the view is `planet`.

The current `App.tsx` builds one `Site`, clips `scene.globe` to that site, and cuts a hole in ion asset `3644333`. Planet view must not do that. Keep the existing site load for the Jezero terrain buttons, but add a `planet` mode that:

- sets `scene.globe.clippingPlanes` to undefined
- removes the tileset clipping polygon
- flies the camera to Mars from `4_000_000` m above `18.65 N, 150 E` so both bubbles can be in frame when the user zooms
- adds one `Cesium.Entity` per bubble:

```ts
viewer.entities.add({
  name: bubble.title,
  rectangle: {
    coordinates: Cesium.Rectangle.fromDegrees(
      bubble.lon - halfDegLon, bubble.lat - halfDegLat,
      bubble.lon + halfDegLon, bubble.lat + halfDegLat,
    ),
    material: new Cesium.ImageMaterialProperty({
      image: `/worlds/${bubble.id}/tiles/ortho/0/0/0.webp`,
    }),
    height: 0,
  },
})
```

`halfDegLat = (bubble.sizeM / 2) / R * 180 / Math.PI`. `halfDegLon` divides by `cos(lat)`.

Serve those images from the Cesium app the same way the sandbox does: add the `roverWorlds()` middleware from `web/src/rover/sandbox/vite.config.ts` to `web/vite.config.ts`, including `crater` and `olympus`. Without that, the rectangle images 404.

- [ ] **Step 1: Add the middleware to `web/vite.config.ts`**
- [ ] **Step 2: Add `planet` to the mode union and the button row in `App.tsx`**
- [ ] **Step 3: In `fly()`, when mode is `planet`, clear clipping and show both rectangles**
- [ ] **Step 4: Manual check**

Run: `cd web && VITE_SITE_DIR=../contracts/fixtures npm run dev`
Open `http://localhost:5173/?view=planet`.
Expected: the Mars globe (or the bare sphere if no ion token) and two orange patches, one near 77°E and one near 222°E. They are visibly sharper than the surrounding globe.

- [ ] **Step 5: Commit**

```bash
git add web/vite.config.ts web/src/App.tsx
git commit -m "Show the Jezero and Olympus bubbles on the whole Mars globe."
```

### Task 4: Drag the pegman into street view

**Files:**
- Modify: `web/src/App.tsx`
- Modify: `web/src/App.css`

**Interfaces:**
- Consumes: `dropOnBubbles` from `web/src/pegman.ts`
- Produces: view state `planet | street`. Street state holds a `StreetDrop`.

Pegman behavior:

- A yellow pegman sits in the corner in planet view. It is an HTML element, not a Cesium entity, so the drag is ordinary pointer events.
- On pointer down, follow the pointer.
- On pointer up, pick the globe with `viewer.scene.pickPosition` or `camera.pickEllipsoid` and convert the Cartesian to longitude and latitude with `Cesium.Cartographic.fromCartesian` on the Mars sphere of radius `3396190`.
- Call `dropOnBubbles(latDeg, lonDeg)`.
- If the result is `null`, snap the pegman back and leave the status text `No street view here`.
- If it hits, set the view to street and remember the drop.

Street view:

- Hide the Cesium container.
- Show an iframe whose `src` is `/sandbox/index.html?world=${drop.world}&x=${drop.x}&y=${drop.y}&heading=0`.
- The Cesium app must proxy that path to the sandbox, or the iframe points at `http://localhost:5174/?world=...` while both dev servers are running. Use the dev-server URL for this task. Do not merge the two Vite graphs.
- A “Map” button clears the street drop and returns to planet view. The pegman is back in the corner.

Confirm the sandbox already honors `x`, `y`, and `heading` in `web/src/rover/sandbox/main.ts` (`START` reads those query params). Do not change physics.

- [ ] **Step 1: Add the pegman element and the Map button in `App.tsx`**
- [ ] **Step 2: Add the CSS. The pegman is a 28 px yellow circle with a 2 px dark outline, fixed at the lower right in planet view.**
- [ ] **Step 3: Wire pointer up to `dropOnBubbles`**
- [ ] **Step 4: Manual check**

Run the Cesium app on 5173 and the sandbox on 5174.
Drag the pegman onto empty Mars. Expected: it snaps back, status says `No street view here`.
Drag it onto the Jezero patch. Expected: the buggy street view opens at that spot.
Drag it onto the Olympus patch. Expected: the scarp street view opens.
Press Map. Expected: the globe returns with both patches.

- [ ] **Step 5: Commit**

```bash
git add web/src/App.tsx web/src/App.css
git commit -m "Enter rover street view by dropping the pegman on a bubble."
```

## Out of scope

- A unique mesh or texture for the whole planet.
- Three hundred rover photos of Olympus Mons. None exist.
- Replacing the sandbox buggy with the Cesium box rover.
- Editing bubble height or color inside this work.

## Self-review

- Planet view, two bubbles, pegman, street view, and the refusal outside coverage each have a task.
- `StreetDrop.world` is `'crater' | 'olympus'`, matching `Bubble.id` and the sandbox `?world=` values.
- The coordinate helper is the same affine used by `Site.lonLatToXY`, with the bubble center as the origin.
