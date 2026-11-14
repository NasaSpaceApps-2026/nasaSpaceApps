// Fly the camera orbit -> site -> ground -> orbit -> site by clicking the preset buttons and
// scrolling, and fail if Cesium reports a render error at any step.
// Usage (dev server running): node scripts/flight.mjs
import { chromium } from 'playwright-core'
const b = await chromium.launch({
  executablePath: process.env.CHROME ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  headless: true,
  args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist', '--no-sandbox'],
})
const page = await b.newPage({ viewport: { width: 1280, height: 800 } })
await page.goto('http://localhost:5173/?view=orbit' + (process.env.QS ?? ''))
await page.waitForFunction(() => window.__ready === true, null, { timeout: 240000 })
const err = () => page.evaluate(() => window.__renderError ?? null)
let failed = false
for (const step of ['site', 'ground', 'orbit', 'site', 'ground', 'site']) {
  const pre = await err()
  if (pre) { console.log('FAIL before', step, '->', pre.split('\n')[0]); failed = true; break }
  try {
    await page.getByRole('button', { name: step, exact: true }).click({ timeout: 45000 })
  } catch (ce) {
    const e = await err()
    console.log(e ? `FAIL clicking ${step} -> render error: ${e.split('\n')[0]}` : `FAIL clicking ${step}: page too busy/slow (no render error): ${String(ce).split('\n')[0]}`)
    failed = true
    break
  }
  for (let i = 0; i < 8; i++) {
    await page.waitForTimeout(1000)
    const e = await err()
    if (e) { console.log('FAIL during', step, '->', e.split('\n')[0]); failed = true; break }
  }
  if (failed) break
  await page.mouse.move(640, 400)
  for (const dy of [-600, -600, 800, 800]) { await page.mouse.wheel(0, dy); await page.waitForTimeout(700) }
  const e = await err()
  if (e) { console.log('FAIL zooming after', step, '->', e.split('\n')[0]); failed = true; break }
  console.log('ok', step)
}
console.log(failed ? 'FLIGHT TEST FAIL' : 'FLIGHT TEST PASS')
await b.close()
process.exit(failed ? 1 : 0)
