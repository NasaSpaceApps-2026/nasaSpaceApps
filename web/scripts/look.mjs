// Check mouse controls: in ground view, dragging up/down must change camera pitch; in site view,
// right-drag must tilt. Usage (dev server running): node scripts/look.mjs
import { chromium } from 'playwright-core'
const b = await chromium.launch({
  executablePath: process.env.CHROME ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  headless: true,
  args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist', '--no-sandbox'],
})
const page = await b.newPage({ viewport: { width: 1280, height: 800 } })
const pitch = () => page.evaluate(() => window.__viewer.camera.pitch * 180 / Math.PI)
const heading = () => page.evaluate(() => window.__viewer.camera.heading * 180 / Math.PI)
let ok = true
const check = (name, cond, detail) => { console.log(cond ? 'PASS' : 'FAIL', name, detail); if (!cond) ok = false }
async function drag(button, dx, dy) {
  await page.mouse.move(700, 500); await page.mouse.down({ button })
  for (let i = 1; i <= 10; i++) { await page.mouse.move(700 + (dx * i) / 10, 500 + (dy * i) / 10); await page.waitForTimeout(40) }
  await page.mouse.up({ button }); await page.waitForTimeout(600)
}
await page.goto('http://localhost:5173/?view=ground')
await page.waitForFunction(() => window.__ready === true, null, { timeout: 240000 })
await page.waitForTimeout(1500)
const p0 = await pitch(), h0 = await heading()
await drag('left', 0, 150)                       // drag down -> look up (or vice versa): pitch must change
const p1 = await pitch()
check('ground: vertical drag changes pitch', Math.abs(p1 - p0) > 5, `pitch ${p0.toFixed(1)} -> ${p1.toFixed(1)}`)
await drag('left', 0, -300)
const p2 = await pitch()
check('ground: opposite drag reverses pitch', Math.sign(p2 - p1) === -Math.sign(p1 - p0), `pitch -> ${p2.toFixed(1)}`)
await drag('left', 150, 0)
const h1 = await heading()
check('ground: horizontal drag changes heading', Math.abs(h1 - h0) > 5, `heading ${h0.toFixed(1)} -> ${h1.toFixed(1)}`)
await page.getByRole('button', { name: 'site', exact: true }).click({ timeout: 45000 })
await page.waitForTimeout(6000)
const q0 = await pitch()
await drag('right', 0, 120)
const q1 = await pitch()
check('site: right-drag tilts', Math.abs(q1 - q0) > 3, `pitch ${q0.toFixed(1)} -> ${q1.toFixed(1)}`)
console.log(ok ? 'LOOK TEST PASS' : 'LOOK TEST FAIL')
await b.close(); process.exit(ok ? 0 : 1)
