// Screenshot each camera preset once the viewer reports fully streamed (window.__ready).
// Usage (dev server running): SHOT_DIR=/tmp node scripts/shot.mjs orbit site ground
import { chromium } from 'playwright-core'
const views = process.argv.slice(2)
const b = await chromium.launch({
  executablePath: process.env.CHROME ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  headless: true,
  args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist', '--no-sandbox'],
})
const page = await b.newPage({ viewport: { width: 1280, height: 800 } })
const errs = []
page.on('pageerror', (e) => errs.push(String(e).slice(0, 200)))
page.on('console', (m) => { if (m.type() === 'error') errs.push(m.text().slice(0, 200)) })
for (const v of views) {
  await page.goto(`http://localhost:5173/?view=${v}`)
  const t0 = Date.now()
  const ok = await page.waitForFunction(() => window.__ready === true, null, { timeout: 240000 }).then(() => true).catch(() => false)
  await page.waitForTimeout(1500)
  await page.screenshot({ path: `${process.env.SHOT_DIR ?? '.'}/${v}.png` })
  console.log(v, ok ? 'READY' : 'TIMEOUT(not fully loaded)', ((Date.now() - t0) / 1000).toFixed(0) + 's')
}
console.log('page errors:', errs.length ? errs.slice(0, 5) : 'none')
await b.close()
