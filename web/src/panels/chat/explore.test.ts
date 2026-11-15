// Unit tests for the Explore tools' pure helpers (web/src/explore). They live here because this is the folder the
// chat-dev vitest config already runs: `cd chat-dev && npx vitest run`.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { FALLBACK_LEVELS, debounce, fmtMetres, kindDefault, lakeLevelsOf, ltstLabel, overlayBase, overlayUrl, parseNamedLevels, siteRect, slotOf, solAt, statLine } from '../../explore/overlayApi'
import { parseTraverse, progressAt, samplesAt, samplesFromPois } from '../../explore/historyData'
import { TOUR_STOPS } from '../../explore/tour'

describe('overlayUrl', () => {
  it('builds sorted, encoded query strings and drops empty params', () => {
    expect(overlayUrl('http://h:8001/', 'evidence', { layer: 'fe_mg_smectite' }, 'png')).toBe('http://h:8001/overlay/evidence.png?layer=fe_mg_smectite')
    expect(overlayUrl('http://h', 'envelope', { y: -433, x: 1844, budget_min: 240, reserve: 0.2, profile: undefined, z: '' }, 'json'))
      .toBe('http://h/overlay/envelope.json?budget_min=240&reserve=0.2&x=1844&y=-433')
    expect(overlayUrl('http://h', 'geology', {}, 'png')).toBe('http://h/overlay/geology.png')
    expect(overlayUrl('http://h', 'lake', { level: -2410 }, 'png')).toBe('http://h/overlay/lake.png?level=-2410')
    expect(overlayUrl('http://h', 'slope', { profile: 'a b&c' }, 'json')).toBe('http://h/overlay/slope.json?profile=a%20b%26c')
  })
  it('picks the base from ?overlay, then the env var, then the engine', () => {
    expect(overlayBase('?view=site&overlay=http://localhost:8090/', 'http://env', 'http://engine')).toBe('http://localhost:8090')
    expect(overlayBase('', 'http://env/', 'http://engine')).toBe('http://env')
    expect(overlayBase('', undefined, 'http://engine')).toBe('http://engine')
  })
  it('routes science kinds to their own slot', () => {
    expect(['evidence', 'geology', 'slope'].map((k) => slotOf(k as never))).toEqual(['science', 'science', 'science'])
    expect(['lake', 'sun', 'viewshed', 'envelope'].map((k) => slotOf(k as never))).toEqual(['main', 'main', 'main', 'main'])
  })
})

describe('siteRect', () => {
  it('matches the site frame: dlat = half/R, dlon = half/(R cos lat0)', () => {
    const r = siteRect(18.452, 77.418, 4096)
    const lat0 = (18.452 * Math.PI) / 180, lon0 = (77.418 * Math.PI) / 180
    expect(r.north - r.south).toBeCloseTo((2 * 4096) / 3396190, 12)
    expect((r.east - r.west) * Math.cos(lat0) * 3396190).toBeCloseTo(2 * 4096, 6)
    expect((r.west + r.east) / 2).toBeCloseTo(lon0, 12)
    expect((r.south + r.north) / 2).toBeCloseTo(lat0, 12)
    expect(r.east).toBeGreaterThan(r.west)
    expect(r.north).toBeGreaterThan(r.south)
  })
  it('scales with half_m', () => {
    const a = siteRect(0, 0, 1000), b = siteRect(0, 0, 2000)
    expect(b.east - b.west).toBeCloseTo(2 * (a.east - a.west), 12)
  })
})

describe('debounce', () => {
  beforeEach(() => { vi.useFakeTimers() })
  afterEach(() => { vi.useRealTimers() })
  it('fires once, with the last arguments, after the quiet period', () => {
    const fn = vi.fn()
    const d = debounce(fn, 200)
    d(1); d(2); vi.advanceTimersByTime(150); d(3)
    vi.advanceTimersByTime(199)
    expect(fn).not.toHaveBeenCalled()
    vi.advanceTimersByTime(1)
    expect(fn).toHaveBeenCalledTimes(1)
    expect(fn).toHaveBeenCalledWith(3)
  })
  it('flush runs the pending call now (slider released); cancel drops it', () => {
    const fn = vi.fn()
    const d = debounce(fn, 200)
    d('a'); d.flush()
    expect(fn).toHaveBeenCalledWith('a')
    d.flush(); vi.advanceTimersByTime(500)
    expect(fn).toHaveBeenCalledTimes(1)
    d('b'); d.cancel(); vi.advanceTimersByTime(500)
    expect(fn).toHaveBeenCalledTimes(1)
  })
})

describe('sol and time formatting', () => {
  it('sol 0 is the touchdown sol (2021-02-18)', () => {
    expect(solAt(new Date(Date.UTC(2021, 1, 18, 21, 0, 0)))).toBe(0)
    expect(solAt(new Date(Date.UTC(2021, 1, 18, 20, 0, 0)))).toBe(-1)
    expect(solAt(new Date(Date.UTC(2021, 2, 20, 21, 0, 0)))).toBe(29) // 30 Earth days = 29.19 sols
  })
  it('formats LTST', () => {
    expect(ltstLabel(5)).toBe('05:00')
    expect(ltstLabel(12.5)).toBe('12:30')
    expect(ltstLabel(18.75)).toBe('18:45')
  })
  it('formats metres with a real minus sign', () => {
    expect(fmtMetres(-2410)).toBe('−2,410 m')
  })
})

describe('statLine (keys as in devapi/OVERLAYS.md)', () => {
  it('lake: the demo sentence', () => {
    expect(statLine('lake', { level_m: -2410, area_km2: 64.6994, max_depth_m: 228.53 })).toBe('Lake level \u22122,410 m: 64.7 km\u00b2 underwater, deepest 229 m')
  })
  it('lake: falls back to level minus the site minimum when depth is missing', () => {
    expect(statLine('lake', { area_km2: 10 }, { level: -2500, reliefMin: -2638 })).toContain('deepest 138 m')
  })
  it('sun: elevation, sunrise and sunset from the *_hours fields, cast shadow share', () => {
    expect(statLine('sun', { sun_elevation_deg: 20.65, sunrise_ltst: '05:47:12', sunset_ltst: '18:12:56', sunrise_ltst_hours: 5.7867, sunset_ltst_hours: 18.2156, cast_shadow_fraction: 0.002 }))
      .toBe('Sun 20.7\u00b0 above the horizon \u00b7 sunrise 05:47 \u00b7 sunset 18:13 \u00b7 0.2% in cast shadow')
    expect(statLine('sun', { sun_elevation_deg: -5, sunrise_ltst: '05:47:12' })).toBe('Sun 5\u00b0 below the horizon \u00b7 sunrise 05:47')
  })
  it('reach, viewshed, slope', () => {
    expect(statLine('envelope', { area_km2: 42.0496, max_radius_m: 4528.6, usable_min: 192 })).toBe('42 km\u00b2 reachable \u00b7 farthest point 4,529 m away \u00b7 192 min usable after the reserve')
    expect(statLine('viewshed', { visible_km2: 0.8772, visible_fraction: 0.0334, farthest_visible_m: 2999.9 })).toBe('0.9 km\u00b2 visible \u00b7 3% of the area in range \u00b7 farthest visible point 3,000 m')
    expect(statLine('slope', { safe_fraction: 0.9, caution_fraction: 0.07, no_go_fraction: 0.03 })).toContain('90% walkable')
  })
  it('missing fields degrade to key/value text instead of failing', () => {
    expect(statLine('envelope', { weird_metric: 5, note: 'ok' })).toBe('weird metric 5 \u00b7 note ok')
    expect(statLine('geology', undefined)).toBe('')
  })
})

describe('named levels and kinds defaults', () => {
  const kinds = { kinds: [
    { kind: 'sun', params: [{ name: 'ltst', default: 12 }, { name: 'sol', default: 2040 }] },
    { kind: 'lake', named_levels: [{ id: 'low', level_m: -2500, label: 'L', citation: 'C2', kind: 'measured' }, { id: 'top', level_m: -2410, label: 'T', citation: 'C1' }, { id: 'bad' }] },
  ] }
  it('reads the lake named_levels, sorted high to low', () => {
    const lv = lakeLevelsOf(kinds)
    expect(lv.map((l) => l.id)).toEqual(['top', 'low'])
    expect(lv[0]).toMatchObject({ level: -2410, label: 'T', citation: 'C1' })
    expect(lakeLevelsOf(null)).toEqual([])
    expect(parseNamedLevels({})).toEqual([])
  })
  it('reads parameter defaults', () => {
    expect(kindDefault(kinds, 'sun', 'sol')).toBe(2040)
    expect(kindDefault(kinds, 'sun', 'nope')).toBeUndefined()
  })
  it('the offline fallback carries the nine levels of docs/lake-levels.md', () => {
    expect(FALLBACK_LEVELS).toHaveLength(9)
    expect(FALLBACK_LEVELS.find((l) => l.level === -2410)?.citation).toMatch(/Mangold/)
  })
})

describe('rover history from /traverse.geojson', () => {
  const gj = { features: [
    { geometry: { type: 'LineString' }, properties: { x: [0, 10, 20, 30, 1000, 1010], y: [0, 0, 0, 0, 0, 0] } },
    { geometry: { type: 'Point' }, properties: { sol: 13, vertex_index: 0 } },
    { geometry: { type: 'Point' }, properties: { sol: 20, vertex_index: 2 } },
    { geometry: { type: 'Point' }, properties: { sol: 30, vertex_index: 5 } },
  ] }
  const segs = parseTraverse(gj)
  it('slices the line by waypoint vertex_index and splits at clipping jumps', () => {
    expect(segs.map((s) => [s.sol, s.xy.length])).toEqual([[20, 3], [30, 2], [30, 2]])
    expect(segs[0].length_m).toBe(20)
    expect(segs[2].xy[0]).toEqual([1000, 0])
  })
  it('cumulative distance and segment count at a sol', () => {
    expect(progressAt(segs, 13)).toEqual({ driven_m: 0, count: 0 })
    expect(progressAt(segs, 20).driven_m).toBe(20)
    expect(progressAt(segs, 1000)).toEqual({ driven_m: 20 + 10 + 10, count: 3 })
    expect(parseTraverse({ features: [] })).toEqual([])
  })
  it('sample markers appear on their sol', () => {
    const samples = samplesFromPois([
      { id: 's2', name: 'Sample 2: B', type: 'sample_site', sol: 194, x: 1, y: 2 },
      { id: 's1', name: 'Sample 1: A (aka "x")', type: 'sample_site', sol: 164, x: 3, y: 4 },
      { id: 'b', name: 'base', type: 'base', x: 0, y: 0 },
    ])
    expect(samples.map((s) => s.id)).toEqual(['s1', 's2'])
    expect(samples[0].name).toBe('Sample 1: A')
    expect(samplesAt(samples, 100)).toEqual({ shown: [], latest: null })
    expect(samplesAt(samples, 170).latest?.id).toBe('s1')
    expect(samplesAt(samples, 300).shown).toHaveLength(2)
  })
})

describe('tour', () => {
  it('has 6 stops in the agreed order, each with a short caption and a citation', () => {
    expect(TOUR_STOPS.map((s) => s.id)).toEqual(['landing', 'seitah', 'delta', 'belva', 'margin', 'lake'])
    for (const s of TOUR_STOPS) {
      expect(s.caption.length).toBeLessThan(260)
      expect(s.cite).toMatch(/docs\/|POI|Sample/)
    }
    expect(TOUR_STOPS[5].overlay).toEqual({ kind: 'lake', params: { level: -2410 } })
  })
})
