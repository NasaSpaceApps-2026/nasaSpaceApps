// Stand-in for the query engine's /overlay/* contract, so the Explore panel can be developed and demoed without the backend.
//   node scripts/overlay-stub.mjs [port]            (default 8091; point the viewer at it with ?overlay=http://localhost:8091)
// Serves the same shapes as the real service: /overlay/kinds, /overlay/{kind}.png, /overlay/{kind}.json. The images are
// computed from the viewer's own 2 m DEM (VITE_SITE_DIR/web/dem2m.*), so shapes line up with the terrain, but the maths is
// deliberately crude (threshold flood without connectivity, grid ray casts): this is a UI test double, not science.
import http from 'node:http'
import fs from 'node:fs'
import path from 'node:path'
import zlib from 'node:zlib'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const siteDir = path.resolve(here, process.env.VITE_SITE_DIR ?? '../../stack/cache/work/data/build')
const meta = JSON.parse(fs.readFileSync(path.join(siteDir, 'web/dem2m.json'), 'utf8'))
const raw = fs.readFileSync(path.join(siteDir, 'web/dem2m.u16'))
const u16 = new Uint16Array(raw.buffer, raw.byteOffset, raw.byteLength / 2)
const manifest = JSON.parse(fs.readFileSync(path.join(siteDir, 'manifest.json'), 'utf8'))
const HALF = manifest.size_m / 2
const N = 256
const CELL = manifest.size_m / N
const dem = new Float32Array(N * N) // row 0 north
for (let r = 0; r < N; r++) for (let c = 0; c < N; c++) {
  const sr = Math.min(meta.height - 1, Math.floor(((r + 0.5) / N) * meta.height)), sc = Math.min(meta.width - 1, Math.floor(((c + 0.5) / N) * meta.width))
  dem[r * N + c] = meta.base + meta.step * u16[sr * meta.width + sc]
}
let zmin = Infinity, zmax = -Infinity
for (const z of dem) { zmin = Math.min(zmin, z); zmax = Math.max(zmax, z) }
const slopeDeg = new Float32Array(N * N)
for (let r = 1; r < N - 1; r++) for (let c = 1; c < N - 1; c++) {
  const dx = (dem[r * N + c + 1] - dem[r * N + c - 1]) / (2 * CELL), dy = (dem[(r - 1) * N + c] - dem[(r + 1) * N + c]) / (2 * CELL)
  slopeDeg[r * N + c] = (Math.atan(Math.hypot(dx, dy)) * 180) / Math.PI
}
const xyToCell = (x, y) => [Math.floor(((y * -1 + HALF) / manifest.size_m) * N), Math.floor(((x + HALF) / manifest.size_m) * N)]

// ---- PNG encoder (RGBA, no filter) ----
const crcT = new Uint32Array(256).map((_, n) => { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; return c >>> 0 })
const crc = (b) => { let c = 0xffffffff; for (const x of b) c = crcT[(c ^ x) & 255] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0 }
const chunk = (t, d) => { const l = Buffer.alloc(4); l.writeUInt32BE(d.length); const td = Buffer.concat([Buffer.from(t), d]); const c = Buffer.alloc(4); c.writeUInt32BE(crc(td)); return Buffer.concat([l, td, c]) }
function png(rgba) {
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(N, 0); ihdr.writeUInt32BE(N, 4); ihdr[8] = 8; ihdr[9] = 6
  const rows = Buffer.alloc((N * 4 + 1) * N)
  for (let r = 0; r < N; r++) { rows[r * (N * 4 + 1)] = 0; Buffer.from(rgba.buffer, r * N * 4, N * 4).copy(rows, r * (N * 4 + 1) + 1) }
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', ihdr), chunk('IDAT', zlib.deflateSync(rows)), chunk('IEND', Buffer.alloc(0))])
}
const hex = (h) => [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)]
const km2 = (cells) => (cells * CELL * CELL) / 1e6

const LEVELS = [
  ['delta_top_2410', -2410, 'measured (minimum water level)', 'Uppermost delta foresets observed so far (north of Belva crater)', "Mangold et al., LPSC 2024 abstract 'Past variations of water level of Jezero paleolake'"],
  ['eastern_breach_ca_2400', -2400, 'approximate (ca.)', 'Elevation of the eastern breach (outlet), ca. -2400 m', 'Mangold et al., LPSC 2024'],
  ['carew_castle_2445', -2445, 'measured', 'Topset/foreset transition at Carew Castle (fan top)', 'Mangold et al., LPSC 2024'],
  ['pinestand_2448', -2448, 'measured (minimum water level)', 'Tops of the foresets at Pinestand Mountain', 'Mangold et al., LPSC 2024'],
  ['fan_front_2470', -2470, 'measured', 'Upper end of the fan-front topset/foreset transitions', 'Mangold et al., LPSC 2024'],
  ['kodiak_2490', -2490, 'measured', 'Kodiak butte', 'Mangold et al., LPSC 2024'],
  ['delta_low_2500', -2500, 'measured', 'Lowest delta level (Eastern Cape and Kodiak)', 'Mangold et al., LPSC 2024'],
].map(([id, level_m, kind, what, source]) => ({ named_level: id, level_m, kind, what, source }))

const hash = (r, c, s) => { let h = (r * 73856093) ^ (c * 19349663) ^ (s * 83492791); h = (h ^ (h >>> 13)) * 1274126177; return ((h ^ (h >>> 16)) >>> 0) / 4294967295 }
const smooth = (r, c, s, k) => { const r0 = Math.floor(r / k), c0 = Math.floor(c / k), fr = r / k - r0, fc = c / k - c0; const v = (a, b) => hash(a, b, s); return (v(r0, c0) * (1 - fc) + v(r0, c0 + 1) * fc) * (1 - fr) + (v(r0 + 1, c0) * (1 - fc) + v(r0 + 1, c0 + 1) * fc) * fr }

const build = {
  lake(q) {
    const level = Number(q.level ?? -2410)
    const px = new Uint8ClampedArray(N * N * 4)
    let n = 0, deepest = 0
    for (let i = 0; i < N * N; i++) if (dem[i] <= level) {
      const d = level - dem[i]; n++; deepest = Math.max(deepest, d)
      const t = Math.min(1, d / 220), [r, g, b] = [Math.round(120 - 90 * t), Math.round(200 - 120 * t), Math.round(255 - 60 * t)]
      px.set([r, g, b, 175], i * 4)
    }
    return { px, title: `Ancient lake at ${level} m`, legend: [{ color: '#7fd0ff', label: 'Shallow water' }, { color: '#1f5aa8', label: 'Deep water' }],
      source: 'HiRISE DTM flooded to the chosen level (stub: no connectivity test)', caveat: 'Present-day topography approximation, not ancient lake-floor bathymetry. (STUB data)',
      stats: { level_m: level, area_km2: Math.round(km2(n) * 10) / 10, max_depth_m: Math.round(deepest) } }
  },
  sun(q) {
    const ltst = Number(q.ltst ?? 12)
    const az = ((ltst - 6) / 12) * Math.PI // east at 06:00 to west at 18:00
    const el = Math.max(-0.1, Math.sin(((ltst - 6) / 12) * Math.PI)) * 1.15
    const east = Math.cos(az), north = -0.35 // toward the sun: east in the morning, west in the evening
    const px = new Uint8ClampedArray(N * N * 4)
    const tanEl = Math.tan(Math.max(0.03, el))
    let lit = 0
    for (let r = 0; r < N; r++) for (let c = 0; c < N; c++) {
      let shadow = el <= 0.02
      if (!shadow) for (let k = 1; k < 60; k++) {
        const rr = Math.round(r - north * k), cc = Math.round(c + east * k)
        if (rr < 0 || cc < 0 || rr >= N || cc >= N) break
        if (3 * (dem[rr * N + cc] - dem[r * N + c]) > k * CELL * tanEl) { shadow = true; break } // relief x3 so the stub shows visible shadows
      }
      if (!shadow) lit++
      px.set(shadow ? [10, 14, 40, 140] : [255, 214, 120, 0], (r * N + c) * 4)
    }
    return { px, title: `Sun and shadow, LTST ${String(Math.floor(ltst)).padStart(2, '0')}:${String(Math.round((ltst % 1) * 60)).padStart(2, '0')}`,
      legend: [{ color: '#0a0e28', label: 'In shadow' }, { color: '#ffd678', label: 'Sunlit' }],
      source: 'Mars24 sun position + terrain shadow casting (stub: crude grid ray march)', caveat: 'Geometric sun centre, no dust scattering. (STUB data)',
      stats: { sun_elevation_deg: Math.round(((el * 180) / Math.PI) * 10) / 10, sunrise_ltst: 6.0, sunset_ltst: 18.0, sunlit_pct: Math.round((100 * lit) / (N * N)) } }
  },
  slope(q) {
    const px = new Uint8ClampedArray(N * N * 4)
    for (let i = 0; i < N * N; i++) { const s = slopeDeg[i]; px.set(s > 20 ? [220, 50, 40, 190] : s > 15 ? [240, 170, 50, 170] : [60, 170, 90, 60], i * 4) }
    return { px, title: 'Slope hazard', legend: [{ color: '#dc3228', label: 'Too steep to walk (over 20 degrees)' }, { color: '#f0aa32', label: 'Caution (15 to 20 degrees)' }, { color: '#3caa5a', label: 'Walkable' }],
      source: 'HiRISE DTM slope at 2 m; thresholds assumed (stub)', caveat: 'Slope limits are assumed values, not mission data. (STUB data)', stats: {} }
  },
  geology() {
    const px = new Uint8ClampedArray(N * N * 4)
    const cols = [[200, 120, 70], [120, 140, 190], [170, 150, 90], [150, 90, 150]]
    for (let r = 0; r < N; r++) for (let c = 0; c < N; c++) { const z = dem[r * N + c]; const b = Math.min(3, Math.floor(((z - zmin) / (zmax - zmin)) * 4 + (smooth(r, c, 3, 24) - 0.5) * 0.9)); px.set([...cols[Math.max(0, b)], 140], (r * N + c) * 4) }
    return { px, title: 'Geologic units', legend: [{ color: '#c87846', label: 'Crater-floor rock' }, { color: '#788cbe', label: 'Delta deposits' }, { color: '#aa965a', label: 'Margin unit' }, { color: '#965a96', label: 'Other units' }],
      source: 'USGS SIM 3464 (stub bands by elevation)', caveat: 'Unit boundaries are approximate. (STUB data)', stats: {} }
  },
  evidence(q) {
    const layer = q.layer ?? 'olivine', seed = [...layer].reduce((a, ch) => a + ch.charCodeAt(0), 0)
    const px = new Uint8ClampedArray(N * N * 4)
    const col = { olivine: [90, 220, 90], carbonate: [255, 120, 220], fe_mg_smectite: [255, 220, 80] }[layer] ?? [255, 255, 255]
    for (let r = 0; r < N; r++) for (let c = 0; c < N; c++) { const v = smooth(r, c, seed, 18); if (v > 0.66) px.set([...col, v > 0.8 ? 200 : 120], (r * N + c) * 4) }
    const nice = { olivine: 'Olivine', carbonate: 'Carbonate', fe_mg_smectite: 'Clay (Fe/Mg smectite)' }[layer] ?? layer
    return { px, title: `${nice} seen from orbit`, legend: [{ color: `rgb(${col})`, label: 'Strong detection' }, { color: `rgba(${col},0.5)`, label: 'Weak detection' }],
      source: 'CRISM mineral map, 36 m (Parente et al., Zenodo) (stub pattern)', caveat: 'No detection is not the same as none: orbital coverage is patchy. (STUB data)', stats: {} }
  },
  viewshed(q) {
    const [r0, c0] = xyToCell(Number(q.x ?? 0), Number(q.y ?? 0)), h = Number(q.h ?? 1.7), R = Math.round(Number(q.radius ?? 3000) / CELL)
    const eye = dem[Math.min(N - 1, Math.max(0, r0)) * N + Math.min(N - 1, Math.max(0, c0))] + h
    const px = new Uint8ClampedArray(N * N * 4)
    let vis = 0
    for (let r = 0; r < N; r++) for (let c = 0; c < N; c++) {
      const d = Math.hypot(r - r0, c - c0); if (d > R) continue
      let ok = true, maxT = -Infinity
      for (let k = 1; k < d; k++) { const rr = Math.round(r0 + ((r - r0) * k) / d), cc = Math.round(c0 + ((c - c0) * k) / d); maxT = Math.max(maxT, (dem[rr * N + cc] - eye) / (k * CELL)) }
      if ((dem[r * N + c] - eye) / (d * CELL) < maxT - 1e-6) ok = false
      if (ok) { vis++; px.set([60, 220, 255, 110], (r * N + c) * 4) }
    }
    return { px, title: `Visible from here (eye ${h} m)`, legend: [{ color: '#3cdcff', label: 'Can be seen from the point' }, { color: 'rgba(0,0,0,0)', label: 'Hidden behind terrain' }],
      source: 'Line of sight over the HiRISE DTM (stub, 8 m grid)', caveat: 'Ignores rocks smaller than the grid. (STUB data)', stats: { area_km2: Math.round(km2(vis) * 10) / 10, visible_pct: Math.round((100 * vis) / (Math.PI * R * R)) } }
  },
  envelope(q) {
    const [r0, c0] = xyToCell(Number(q.x ?? 0), Number(q.y ?? 0)), budget = Number(q.budget_min ?? 240), reserve = Number(q.reserve ?? 0.2)
    const Rm = ((budget * (1 - reserve)) / 2) * 60 * 0.9 // walk out and back at 0.9 m/s
    const px = new Uint8ClampedArray(N * N * 4)
    let n = 0, far = 0
    for (let r = 0; r < N; r++) for (let c = 0; c < N; c++) {
      const d = Math.hypot(r - r0, c - c0) * CELL
      if (d <= Rm * (1 - 0.35 * smooth(r, c, 9, 20)) && slopeDeg[r * N + c] < 20) { n++; far = Math.max(far, d); px.set([255, 170, 40, d > Rm * 0.7 ? 90 : 150], (r * N + c) * 4) }
    }
    return { px, title: `Reach: ${Math.round(budget / 60 * 10) / 10} h EVA, ${Math.round(reserve * 100)}% reserve`, legend: [{ color: '#ffaa28', label: 'Can reach and get home in time' }],
      source: 'Walking-time model on the HiRISE DTM (stub: distance and slope only)', caveat: 'Walking speed and slope limits are assumed values. (STUB data)', stats: { area_km2: Math.round(km2(n) * 10) / 10, max_radius_m: Math.round(far) } }
  },
}

const cors = { 'access-control-allow-origin': '*', 'access-control-allow-headers': '*' }
const server = http.createServer((req, res) => {
  const u = new URL(req.url, 'http://x')
  if (req.method === 'OPTIONS') { res.writeHead(204, cors); return res.end() }
  const send = (code, type, body) => { res.writeHead(code, { ...cors, 'content-type': type }); res.end(body) }
  if (u.pathname === '/overlay/kinds') return send(200, 'application/json', JSON.stringify({ stub: true, kinds: Object.keys(build).map((k) => ({ kind: k, ...(k === 'lake' ? { named_levels: LEVELS, params: { level: { default: -2410, min: zmin, max: zmax } } } : {}) })) }))
  const m = /^\/overlay\/(\w+)\.(png|json)$/.exec(u.pathname)
  if (!m || !build[m[1]]) return send(404, 'application/json', JSON.stringify({ detail: 'not found' }))
  const q = Object.fromEntries(u.searchParams)
  setTimeout(() => {
    const { px, ...info } = build[m[1]](q)
    if (m[2] === 'png') return send(200, 'image/png', png(px))
    send(200, 'application/json', JSON.stringify({ ...info, bounds: { half_m: HALF, lat0: manifest.lat0, lon0: manifest.lon0 } }))
  }, Number(process.env.STUB_DELAY_MS ?? 120))
})
const port = Number(process.argv[2] ?? 8091)
server.listen(port, '127.0.0.1', () => console.log(`overlay stub on http://localhost:${port}  (site ${manifest.site}, half ${HALF} m, z ${zmin.toFixed(0)}..${zmax.toFixed(0)})`))
