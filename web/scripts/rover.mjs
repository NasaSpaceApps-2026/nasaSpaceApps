// Drive the rover on the real Jezero world: W moves it, D turns it, terrain-following keeps it on the DEM.
// Usage (dev server running): SHOT_DIR=/tmp node scripts/rover.mjs
import { chromium } from 'playwright-core'
const b = await chromium.launch({
  executablePath: process.env.CHROME ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  headless: true,
  args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist', '--no-sandbox'],
})
const page = await b.newPage({ viewport: { width: 1280, height: 800 } })
let ok = true
const check = (n, c, d) => { console.log(c ? 'PASS' : 'FAIL', n, d); if (!c) ok = false }
const pose = () => page.evaluate(() => { const p = window.__rover.driver.pose; return { x: p.x, y: p.y, z: p.z, h: p.heading_deg, pitch: p.pitch_deg, roll: p.roll_deg, slope: p.slope_deg, status: p.status } })
await page.goto('http://localhost:5173/?view=rover')
await page.waitForFunction(() => window.__ready === true && window.__rover, null, { timeout: 240000 })
await page.waitForTimeout(1500)
const p0 = await pose()
console.log('start', JSON.stringify(p0))
// Software rendering runs at ~1 fps here, and physics dt is capped per frame, so key-hold timing is
// meaningless. Step the driver at a fixed rate with explicit input instead (same code path as keys).
const run = (throttle, steer, seconds) => page.evaluate(([t, st, sec]) => {
  for (let i = 0; i < sec * 20; i++) window.__rover.driver.step(0.05, { throttle: t, steer: st })
}, [throttle, steer, seconds])
await run(1, 0, 6)
const p1 = await pose()
const d1 = Math.hypot(p1.x - p0.x, p1.y - p0.y)
check('drive forward moves the rover', d1 > 20, `moved ${d1.toFixed(1)} m in 6 s (display speed 8 m/s), heading ${p1.h.toFixed(0)} deg, y ${p0.y.toFixed(0)} -> ${p1.y.toFixed(0)}`)
const groundZ = await page.evaluate(([x, y]) => window.__rover.driver.physics.sampler.sampleHeight(x, y), [p1.x, p1.y])
check('rover sits on the real DEM', Math.abs(p1.z - groundZ) < 0.5, `z ${p1.z.toFixed(2)} vs DEM ${groundZ.toFixed(2)} (site elevations run about -2640..-2290)`)
await run(0, 1, 2)
const p2 = await pose()
check('steering turns the rover', Math.abs(((p2.h - p1.h + 540) % 360) - 180) > 20, `heading ${p1.h.toFixed(0)} -> ${p2.h.toFixed(0)}`)
await run(0, 0, 1)
// keep driving long enough to leave the site or hit a slope limit; either must STOP the rover, never let it drive through
await run(1, 0, 400)
const p3 = await pose()
check('rover stops at a limit instead of driving through', p3.status.startsWith('blocked') || Math.hypot(p3.x, p3.y) < 4300, `after 400 s: status ${p3.status}, at x ${p3.x.toFixed(0)} y ${p3.y.toFixed(0)}`)
// Real no-go test: drive straight at a ~48 deg slope found in analysis/slope.tif (steep cell at
// x -2951, y 3539, gentle approach 60 m south). The rover must stop on the approach, not climb it.
await page.evaluate(() => window.__rover.driver.teleport(-2951, 3479, 0))
await run(0, 0, 1)
await run(1, 0, 60)
const p4 = await pose()
const stopReason = await page.evaluate(() => window.__rover.driver.pose.stop_reason)
// Force-based physics (teammate, 2026-11-14): no hard slope block; the rover stops when wheel grip runs out
// (mu 0.6 -> about atan(mu - Crr) = 27 deg). So: it must stop short of the 48 deg cell, on a slope at or below
// the grip limit, reporting blocked or slipping. Perseverance's real maximum measured tilt is 20.3 deg
// (docs/science-validation.md), so the planner's 20 deg limit, not this grip limit, is the realistic one.
check('cannot climb past its grip limit on real terrain', ['blocked_slope', 'slipping'].includes(p4.status) && p4.y < 3539 && p4.slope < 31,
  `status ${p4.status} (${stopReason}), y ${p4.y.toFixed(0)} (steep cell at 3539), slope under rover ${p4.slope.toFixed(1)} deg`)
await page.screenshot({ path: `${process.env.SHOT_DIR ?? '.'}/rover_chase.png` })
console.log('final', JSON.stringify(await pose()))
console.log('renderError:', await page.evaluate(() => window.__renderError ?? null))
console.log(ok ? 'ROVER TEST PASS' : 'ROVER TEST FAIL')
await b.close(); process.exit(ok ? 0 : 1)
